import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from database.shop_model import ShopModel

class ItemSelect(discord.ui.Select):
    def __init__(self, cog, items):
        options = [discord.SelectOption(label=item['name'][:100], value=str(item['id']), description=f"Cost: {item['price']} Cookies"[:100]) 
                  for item in items if item['stock'] != 0] or [discord.SelectOption(label="No items available", value="0")]
        super().__init__(placeholder="Select an item to purchase...", options=options)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "0":
            return await interaction.response.send_message("No items available.", ephemeral=True)
        await ConfirmView(self.cog, int(self.values[0])).send_confirmation(interaction)

class ManagementSelect(discord.ui.Select):
    def __init__(self, cog, shop_name):
        options = [
            discord.SelectOption(label="Add Item", value="add_item", description="Add new item to shop"),
            discord.SelectOption(label="Edit Item", value="edit_item", description="Edit existing item"),
            discord.SelectOption(label="Delete Item", value="delete_item", description="Remove item from shop"),
            discord.SelectOption(label="Edit Shop", value="edit_shop", description="Edit shop design"),
            discord.SelectOption(label="Refresh Shop", value="refresh", description="Update shop embed")
        ]
        super().__init__(placeholder="Choose management action...", options=options)
        self.cog, self.shop_name = cog, shop_name

    async def callback(self, interaction: discord.Interaction):
        action = self.values[0]
        
        if action == "add_item":
            return await interaction.response.send_modal(ItemModal(self.cog, self.shop_name))
        
        if action == "edit_shop":
            shop = await self.cog.shop_model.get_shop(self.shop_name)
            return await interaction.response.send_modal(ShopModal(self.cog, shop)) if shop else None
        
        if action == "refresh":
            await self.cog.refresh_shop_embed(interaction.guild, self.shop_name)
            return await interaction.response.send_message("✅ Shop refreshed!", ephemeral=True)
        
        # Handle edit_item and delete_item
        items = await self.cog.shop_model.get_items(self.shop_name)
        if not items:
            return await interaction.response.send_message(f"No items to {action.split('_')[0]}.", ephemeral=True)
        
        view = discord.ui.View(timeout=180)
        item_select = discord.ui.Select(
            placeholder=f"Select item to {action.split('_')[0]}...",
            options=[discord.SelectOption(label=item['name'][:100], value=str(item['id'])) for item in items[:25]]
        )
        
        async def item_callback(edit_interaction):
            item_id = int(item_select.values[0])
            item = await self.cog.shop_model.get_item(item_id)
            if not item:
                return
            
            if action == "edit_item":
                await edit_interaction.response.send_modal(ItemModal(self.cog, self.shop_name, item))
            else:  # delete_item
                success = await self.cog.shop_model.delete_item(item_id)
                if success:
                    await self.cog.refresh_shop_embed(edit_interaction.guild, self.shop_name)
                msg = f"✅ Deleted {item['name']}" if success else "❌ Failed to delete item"
                await edit_interaction.response.send_message(msg, ephemeral=True)
        
        item_select.callback = item_callback
        view.add_item(item_select)
        await interaction.response.send_message(f"Select item to {action.split('_')[0]}:", view=view, ephemeral=True)

class ConfirmView(discord.ui.View):
    def __init__(self, cog, item_id):
        super().__init__(timeout=180)
        self.cog, self.item_id = cog, item_id

    async def send_confirmation(self, interaction: discord.Interaction):
        item = await self.cog.shop_model.get_item(self.item_id)
        if not item:
            return await interaction.response.send_message("Item not found.", ephemeral=True)
        if item['stock'] == 0:
            return await interaction.response.send_message("This item is sold out.", ephemeral=True)
        
        await interaction.response.send_message(
            f"Are you sure you want to purchase **{item['name']}** for **{item['price']} Cookies**?",
            view=self, ephemeral=True
        )

    @discord.ui.button(label="Confirm Purchase", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        async with self.cog.db_lock:
            pilot_result = await self.cog.bot.pilots_model.identify_pilot(interaction.user)
            if not pilot_result['success']:
                return await interaction.followup.send(pilot_result['error_message'], ephemeral=True)
            
            pilot_record = pilot_result['pilot_data']
            pilot_id, callsign = pilot_record['id'], pilot_record['callsign']

            item = await self.cog.shop_model.get_item(self.item_id)
            if not item or item['stock'] == 0:
                return await interaction.followup.send("Sorry, this item just sold out!", ephemeral=True)

            balance = await self.cog.bot.event_transaction_model.get_balance(pilot_id)
            if balance < item['price']:
                return await interaction.followup.send(f"You don't have enough Cookies! You need {item['price']}, but you only have {balance}.", ephemeral=True)

            if not await self.cog.bot.event_transaction_model.add_transaction(pilot_id, -item['price'], f"Shop Purchase: {item['name']}"):
                return await interaction.followup.send("Transaction failed. Please try again.", ephemeral=True)
            
            if item['stock'] != -1 and not await self.cog.shop_model.decrease_item_stock(self.item_id):
                await self.cog.bot.event_transaction_model.add_transaction(pilot_id, item['price'], f"Shop Refund: {item['name']} (sold out)")
                return await interaction.followup.send("Item sold out during purchase. Your cookies have been refunded.", ephemeral=True)

            await self.cog.log_to_thread(interaction.guild, interaction.user, item)
            await self.cog.refresh_shop_embed(interaction.guild, item['shop_name'])
            await interaction.followup.send(f"✅ Purchase successful! You bought **{item['name']}**.", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Purchase cancelled.", view=self)

class ShopView(discord.ui.View):
    def __init__(self, cog, shop_name):
        super().__init__(timeout=None)
        self.cog, self.shop_name = cog, shop_name

    @discord.ui.button(label="🛒 Buy Item", style=discord.ButtonStyle.success, custom_id="shop_buy")
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            items = await self.cog.shop_model.get_available_items(self.shop_name)
            if not items:
                return await interaction.response.send_message("The shop is currently empty or all items are sold out.", ephemeral=True)
            
            view = discord.ui.View(timeout=180)
            view.add_item(ItemSelect(self.cog, items))
            await interaction.response.send_message("Please select an item from the menu below.", view=view, ephemeral=True)
        except Exception as e:
            print(f"Shop buy error: {e}")
            await interaction.response.send_message("❌ Shop temporarily unavailable. Please try again.", ephemeral=True)
    
    @discord.ui.button(label="ℹ️ Item Info", style=discord.ButtonStyle.secondary, custom_id="shop_info")
    async def info_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        items = await self.cog.shop_model.get_items(self.shop_name)
        if not items:
            return await interaction.response.send_message("The shop is currently empty.", ephemeral=True)

        embed = discord.Embed(title="🍬 Shop Item Descriptions", color=discord.Color.blue())
        for item in items:
            embed.add_field(name=item['name'], value=f"> {item['description']}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="⚙️ Management", style=discord.ButtonStyle.primary, custom_id="shop_manage")
    async def manage_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Only Excutives can use this .", ephemeral=True)
        
        view = discord.ui.View(timeout=180)
        view.add_item(ManagementSelect(self.cog, self.shop_name))
        await interaction.response.send_message("Choose a management action:", view=view, ephemeral=True)

class ShopModal(discord.ui.Modal):
    def __init__(self, cog, shop_data=None):
        super().__init__(title="Create Shop" if not shop_data else "Edit Shop")
        self.cog, self.shop_data = cog, shop_data
        
        inputs = [
            ("Shop Name (ID)", "halloween_2025", shop_data['shop_name'] if shop_data else "", 100),
            ("Shop Title", "🎁 QRV HALLOWEEN SHOP – 2025", shop_data['title'] if shop_data else "", 200),
            ("Description", "Welcome! Use the buttons below...", shop_data['description'] if shop_data else "", 1000),
            ("Footer Text", "QRV Halloween 2025 | Candy Shop System", shop_data['footer_text'] if shop_data else "", 200),
            ("Color", "orange, red, blue, or #FFD700", shop_data['color'] if shop_data else "orange", 50)
        ]
        
        self.inputs = []
        for i, (label, placeholder, default, max_len) in enumerate(inputs):
            text_input = discord.ui.TextInput(
                label=label, placeholder=placeholder, default=default, max_length=max_len,
                style=discord.TextStyle.paragraph if i == 2 else discord.TextStyle.short,
                required=i < 2
            )
            self.inputs.append(text_input)
            self.add_item(text_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        values = [inp.value.strip() for inp in self.inputs]
        shop_info = {
            'shop_name': values[0], 'title': values[1], 'description': values[2],
            'footer_text': values[3] or None, 'color': values[4] or 'orange',
            'created_by': interaction.user.id
        }
        
        if not shop_info['shop_name'] or not shop_info['title']:
            return await interaction.response.send_message("❌ Shop name and title are required.", ephemeral=True)
        
        if self.shop_data:
            success = await self.cog.shop_model.update_shop(self.shop_data['shop_name'], shop_info)
            msg = f"✅ Updated shop '{shop_info['title']}'." if success else "❌ Failed to update shop."
        else:
            success = await self.cog.shop_model.create_shop(shop_info)
            msg = f"✅ Created shop '{shop_info['title']}'." if success else "❌ Failed to create shop."
        
        await interaction.response.send_message(msg, ephemeral=True)

class ItemModal(discord.ui.Modal):
    def __init__(self, cog, shop_name, item_data=None):
        super().__init__(title="Add Item" if not item_data else "Edit Item")
        self.cog, self.shop_name, self.item_data = cog, shop_name, item_data
        
        inputs = [
            ("Item Name", "5 Hours Bonus", item_data['name'] if item_data else "", 100),
            ("Description", "Get **+5 hours** to your Account.", item_data['description'] if item_data else "", 1000),
            ("Price (Cookies)", "250", str(item_data['price']) if item_data else "", 10),
            ("Stock (-1 for unlimited)", "10", str(item_data['stock']) if item_data else "", 10)
        ]
        
        self.inputs = []
        for i, (label, placeholder, default, max_len) in enumerate(inputs):
            text_input = discord.ui.TextInput(
                label=label, placeholder=placeholder, default=default, max_length=max_len,
                style=discord.TextStyle.paragraph if i == 1 else discord.TextStyle.short
            )
            self.inputs.append(text_input)
            self.add_item(text_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            values = [inp.value.strip() for inp in self.inputs]
            name, description, price, stock = values[0], values[1], int(values[2]), int(values[3])
            
            if not name or not description or price < 0 or stock < -1:
                return await interaction.response.send_message("❌ Invalid input.", ephemeral=True)
        except ValueError:
            return await interaction.response.send_message("❌ Price and stock must be valid numbers.", ephemeral=True)
        
        item_info = {'name': name, 'description': description, 'price': price, 'stock': stock}
        
        if self.item_data:
            success = await self.cog.shop_model.update_item(self.item_data['id'], item_info)
            msg = f"✅ Updated item '{name}'." if success else "❌ Failed to update item."
        else:
            success = await self.cog.shop_model.add_item(self.shop_name, item_info)
            msg = f"✅ Added item '{name}' to shop." if success else "❌ Failed to add item."
        
        if success:
            refresh_success = await self.cog.refresh_shop_embed(interaction.guild, self.shop_name)
            if not refresh_success:
                msg += " (Warning: Could not update shop display)"
        await interaction.response.send_message(msg, ephemeral=True)

class ShopCog(commands.Cog, name="Shop"):
    def __init__(self, bot):
        self.bot = bot
        self.shop_model = bot.shop_model
        self.db_lock = asyncio.Lock()
        
    async def cog_load(self):
        """Called when cog is loaded - restore persistent views"""
        await self.restore_persistent_views()
        
    async def restore_persistent_views(self):
        """Restore all shop views after bot restart"""
        try:
            shops = await self.shop_model.get_all_shops()
            for shop in shops:
                if shop.get('message_id'):
                    view = ShopView(self, shop['shop_name'])
                    self.bot.add_view(view)
        except Exception as e:
            print(f"Error restoring shop views: {e}")

    async def build_shop_embed(self, shop_name: str):
        shop = await self.shop_model.get_shop(shop_name)
        if not shop:
            return None
        
        embed = discord.Embed(title=shop['title'], description=shop['description'], color=self._parse_color(shop['color']))
        if shop['image_url']:
            embed.set_image(url=shop['image_url'])
        
        items = await self.shop_model.get_items(shop_name)
        if not items:
            embed.add_field(name="Shop is Empty", value="No items available yet.")
        else:
            for item in items:
                stock_str = "Unlimited" if item['stock'] == -1 else "SOLD OUT" if item['stock'] == 0 else str(item['stock'])
                embed.add_field(name=item['name'], value=f"> `Price: {item['price']} 🍪 | Stock: {stock_str}`", inline=False)
        
        if shop['footer_text']:
            embed.set_footer(text=shop['footer_text'])
        return embed

    def _parse_color(self, color_str):
        color_map = {"red": discord.Color.red(), "blue": discord.Color.blue(), "green": discord.Color.green(),
                    "gold": discord.Color.gold(), "purple": discord.Color.purple(), "orange": discord.Color.orange(),
                    "yellow": discord.Color.yellow(), "pink": discord.Color.magenta(), "teal": discord.Color.teal()}
        
        if color_str and color_str.lower() in color_map:
            return color_map[color_str.lower()]
        if color_str and color_str.startswith('#'):
            try:
                return discord.Color(int(color_str[1:], 16))
            except ValueError:
                pass
        return discord.Color.orange()

    async def refresh_shop_embed(self, guild: discord.Guild, shop_name: str):
        shop = await self.shop_model.get_shop(shop_name)
        if not shop or not shop['message_id']:
            return False
        try:
            channel = guild.get_channel(shop['channel_id'])
            if not channel:
                return False
            message = await channel.fetch_message(shop['message_id'])
            new_embed = await self.build_shop_embed(shop_name)
            if new_embed:
                view = ShopView(self, shop_name)
                await message.edit(embed=new_embed, view=view)
                return True
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            print(f"Failed to refresh shop embed: {e}")
        return False

    async def log_to_thread(self, guild: discord.Guild, user: discord.User, item: dict):
        shop = await self.shop_model.get_shop(item['shop_name'])
        if not shop or not shop['thread_id']:
            return
        try:
            thread = guild.get_thread(shop['thread_id'])
            if thread:
                await thread.send(f"**New Purchase!** {user.mention} just bought **{item['name']}**.")
        except (discord.NotFound, discord.Forbidden):
            pass

    async def shop_autocomplete(self, interaction: discord.Interaction, current: str):
        shops = await self.shop_model.get_all_shops()
        return [app_commands.Choice(name=f"{shop['title']} ({shop['shop_name']})", value=shop['shop_name'])
                for shop in shops if current.lower() in shop['shop_name'].lower() or current.lower() in shop['title'].lower()][:25]

    @app_commands.command(name="shop", description="Manage shops and items")
    @app_commands.describe(action="What to do", shop_name="Shop to work with")
    @app_commands.choices(action=[
        app_commands.Choice(name="Create Shop", value="create"),
        app_commands.Choice(name="Deploy Shop", value="deploy"),
        app_commands.Choice(name="List Shops", value="list"),
        app_commands.Choice(name="Delete Shop", value="delete")
    ])
    @app_commands.autocomplete(shop_name=shop_autocomplete)
    @app_commands.checks.has_permissions(administrator=True)
    async def shop_command(self, interaction: discord.Interaction, action: str, shop_name: str = None):
        if action == "create":
            return await interaction.response.send_modal(ShopModal(self))
        
        if action == "list":
            shops = await self.shop_model.get_all_shops()
            if not shops:
                return await interaction.response.send_message("No shops found.", ephemeral=True)
            
            embed = discord.Embed(title="🏪 All Shops", color=discord.Color.blue())
            for shop in shops:
                items_count = len(await self.shop_model.get_items(shop['shop_name']))
                embed.add_field(name=shop['title'], value=f"ID: `{shop['shop_name']}`\nItems: {items_count}", inline=True)
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        if not shop_name:
            return await interaction.response.send_message("❌ Shop name is required.", ephemeral=True)
        
        if action == "deploy":
            await interaction.response.defer(ephemeral=True)
            embed = await self.build_shop_embed(shop_name)
            if not embed:
                return await interaction.followup.send("❌ Shop not found.", ephemeral=True)
            
            view = ShopView(self, shop_name)
            shop_message = await interaction.channel.send(embed=embed, view=view)
            log_thread = await shop_message.create_thread(name=f"{shop_name} Purchase Log")
            
            success = await self.shop_model.update_shop_deployment(shop_name, interaction.channel.id, shop_message.id, log_thread.id)
            msg = "✅ Shop deployed successfully!" if success else "❌ Failed to save deployment info."
            await interaction.followup.send(msg, ephemeral=True)
        
        elif action == "delete":
            try:
                success = await self.shop_model.delete_shop(shop_name)
                if success:
                    msg = f"✅ Deleted shop '{shop_name}' successfully."
                else:
                    msg = f"❌ Shop '{shop_name}' not found or already deleted."
            except Exception as e:
                print(f"Error deleting shop {shop_name}: {e}")
                msg = f"❌ Error deleting shop: {str(e)}"
            await interaction.response.send_message(msg, ephemeral=True)

    @shop_command.error
    async def on_shop_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        msg = "❌ You need administrator permissions to use this command." if isinstance(error, app_commands.MissingPermissions) else f"❌ An error occurred: {error}"
        await interaction.response.send_message(msg, ephemeral=True)

async def setup(bot):
    cog = ShopCog(bot)
    await bot.add_cog(cog)
import discord
from discord.ext import commands
from discord import app_commands
import logging

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 1. CREATE EMBED MODAL (Moved outside the Cog)
# ------------------------------------------------------------------
class CreateEmbedModal(discord.ui.Modal, title='Create New Embed'):
    # Define inputs as class attributes for stability
    embed_title = discord.ui.TextInput(
        label='Title',
        placeholder='Embed title...',
        required=False,
        max_length=256
    )
    description = discord.ui.TextInput(
        label='Description',
        placeholder='Main content...',
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=4000
    )
    color = discord.ui.TextInput(
        label='Color (Name or Hex)',
        placeholder='blue, red, or #FFFFFF',
        required=False,
        max_length=20
    )
    image_url = discord.ui.TextInput(
        label='Image URL',
        placeholder='https://...',
        required=False
    )
    footer_text = discord.ui.TextInput(
        label='Footer',
        placeholder='Footer text...',
        required=False,
        max_length=2048
    )

    async def on_submit(self, interaction: discord.Interaction):
        # We process this silently first
        await interaction.response.defer(ephemeral=True)

        try:
            # 1. Resolve Color
            resolved_color = discord.Color.default()
            if self.color.value:
                color_map = {
                    'red': discord.Color.red(), 'green': discord.Color.green(),
                    'blue': discord.Color.blue(), 'yellow': discord.Color.gold(),
                    'purple': discord.Color.purple(), 'orange': discord.Color.orange(),
                    'black': 0x000000, 'white': 0xFFFFFF
                }
                
                raw_color = self.color.value.lower().strip()
                if raw_color in color_map:
                    resolved_color = color_map[raw_color]
                elif raw_color.startswith('#'):
                    try:
                        resolved_color = discord.Color(int(raw_color.replace('#', ''), 16))
                    except ValueError:
                        pass # Keep default if invalid hex

            # 2. Build Embed
            embed = discord.Embed(
                title=self.embed_title.value,
                description=self.description.value,
                color=resolved_color
            )

            if self.image_url.value:
                embed.set_image(url=self.image_url.value)
            
            if self.footer_text.value:
                embed.set_footer(text=self.footer_text.value)

            # 3. Send to Channel (Publicly)
            await interaction.channel.send(embed=embed)
            
            # 4. Finish the interaction
            await interaction.followup.send("✅ Embed created successfully!", ephemeral=True)

        except Exception as e:
            logger.error(f"Error creating embed: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


# ------------------------------------------------------------------
# 2. EDIT EMBED MODAL (Moved outside the Cog)
# ------------------------------------------------------------------
class EditEmbedModal(discord.ui.Modal, title="Edit Message Content"):
    # Define inputs here so Discord UI registers them correctly
    embed_title = discord.ui.TextInput(
        label="Title",
        required=False,
        max_length=256
    )
    description = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=4000
    )
    image_url = discord.ui.TextInput(
        label="Image URL",
        placeholder="https://...",
        required=False
    )
    footer_text = discord.ui.TextInput(
        label="Footer Text",
        required=False,
        max_length=2048
    )
    color = discord.ui.TextInput(
        label="Color (Name or Hex)",
        placeholder="red, blue, #FF0000",
        required=False
    )

    def __init__(self, target_message: discord.Message):
        super().__init__()
        self.target_message = target_message
        
        # PRE-FILL LOGIC
        # We modify the 'default' value of the inputs based on existing message
        if target_message.embeds:
            embed = target_message.embeds[0]
            self.embed_title.default = embed.title or ""
            self.description.default = embed.description or ""
            
            if embed.image:
                self.image_url.default = embed.image.url
            if embed.footer:
                self.footer_text.default = embed.footer.text
            if embed.color:
                # Convert int color to Hex string (e.g. #FF0000)
                self.color.default = str(embed.color)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            # Get existing embed or make new one
            if self.target_message.embeds:
                embed = self.target_message.embeds[0]
            else:
                embed = discord.Embed()

            # Update Fields
            embed.title = self.embed_title.value or None
            embed.description = self.description.value
            
            # Update Color
            if self.color.value:
                raw_color = self.color.value.lower().strip()
                color_map = {
                    'red': 0xff0000, 'green': 0x00ff00, 'blue': 0x0000ff,
                    'yellow': 0xffff00, 'purple': 0x800080, 'orange': 0xffa500,
                    'white': 0xffffff, 'black': 0x000000
                }
                if raw_color in color_map:
                    embed.color = color_map[raw_color]
                elif raw_color.startswith('#'):
                    try:
                        embed.color = int(raw_color.replace('#', ''), 16)
                    except ValueError:
                        pass
            
            # Update Image/Footer
            embed.set_image(url=self.image_url.value if self.image_url.value else None)
            embed.set_footer(text=self.footer_text.value if self.footer_text.value else None)

            # SAVE TO DISCORD (No Database required)
            await self.target_message.edit(embed=embed)
            
            await interaction.followup.send("✅ Message updated!", ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error editing embed: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


# ------------------------------------------------------------------
# 3. THE COG
# ------------------------------------------------------------------
class EmbedCreator(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        # Register the Context Menu (Right Click)
        self.ctx_menu = app_commands.ContextMenu(
            name="Edit Embed",
            callback=self.edit_message_context
        )
        self.bot.tree.add_command(self.ctx_menu)

    async def cog_unload(self):
        # Clean up context menu when cog unloads
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)

    # SLASH COMMAND: /embed
    @app_commands.command(name="embed", description="Create a custom embed")
    async def create_embed_command(self, interaction: discord.Interaction):
        # Open the Create Modal
        await interaction.response.send_modal(CreateEmbedModal())

    # CONTEXT MENU CALLBACK
    async def edit_message_context(self, interaction: discord.Interaction, message: discord.Message):
        # Permission check
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need 'Manage Messages' permission.", ephemeral=True)
            return

        # Check if bot owns the message
        if message.author != self.bot.user:
            await interaction.response.send_message("❌ I can only edit my own messages.", ephemeral=True)
            return

        # Open the Edit Modal (Passing the message to it)
        await interaction.response.send_modal(EditEmbedModal(message))

async def setup(bot):
    await bot.add_cog(EmbedCreator(bot))
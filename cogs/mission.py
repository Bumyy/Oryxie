# cogs/mission.py
import discord
from discord import app_commands, ui
from discord.ext import commands, tasks
from datetime import datetime, timezone
from typing import Optional, List
from database.mission_module import MissionDB

def is_admin():
    def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.manage_guild
    return app_commands.check(predicate)

class MissionModal(ui.Modal):
    def __init__(self, bot: commands.Bot, channel_id: int, mission_id: Optional[int] = None, default_title: str = ""):
        super().__init__(title="Mission Details")
        self.bot = bot
        self.mission_id = mission_id
        self.channel_id = channel_id
        
        self.title_input = ui.TextInput(label="Mission Title", placeholder="Welcome to Prague", default=default_title, style=discord.TextStyle.short, max_length=200)
        self.description = ui.TextInput(label="Description", placeholder="Welcome to Prague, a city where Christmas feels like...", style=discord.TextStyle.paragraph, max_length=2000)
        self.image_url = ui.TextInput(label="Image URL", placeholder="https://i.imgur.com/image.png", required=False, style=discord.TextStyle.short)
        self.footer_text = ui.TextInput(label="Footer Text", placeholder="✨ Let the magic inspire your journey! ✨", required=False, style=discord.TextStyle.short)
        self.color = ui.TextInput(label="Color", placeholder="gold, red, blue, or #FFD700", required=False, style=discord.TextStyle.short)
        
        for item in [self.title_input, self.description, self.image_url, self.footer_text, self.color]:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        mission_data = {
            "title": self.title_input.value,
            "description": self.description.value,
            "image_url": self.image_url.value or None,
            "footer_text": self.footer_text.value or None,
            "color": self.color.value or "gold",
            "creator_id": interaction.user.id,
            "channel_id": self.channel_id
        }
        
        view = MissionTypeView(self.bot, mission_data, self.mission_id)
        
        if self.mission_id:
            existing_mission = await interaction.client.mission_db.get_mission_by_id(self.mission_id)
            if existing_mission:
                view.existing_flight_data = existing_mission
        
        await interaction.followup.send("Choose mission type:", view=view, ephemeral=True)

class FlightModal(ui.Modal):
    def __init__(self, bot: commands.Bot, mission_data: dict, mission_id: Optional[int] = None, existing_data=None):
        super().__init__(title="Flight Configuration")
        self.bot = bot
        self.mission_data = mission_data
        self.mission_id = mission_id
        
        self.flight_numbers = ui.TextInput(label="Flight Numbers", placeholder="QR123,QR456,QR789", style=discord.TextStyle.short)
        self.multiplier = ui.TextInput(label="Multiplier", placeholder="3", style=discord.TextStyle.short)
        self.post_date = ui.TextInput(label="Post Date", placeholder="25:12:2024", style=discord.TextStyle.short)
        self.post_time = ui.TextInput(label="Post Time (UTC)", placeholder="18:00", style=discord.TextStyle.short)
        self.deadline_hours = ui.TextInput(label="Deadline Hours", placeholder="24", style=discord.TextStyle.short)
        
        if existing_data:
            self.flight_numbers.default = existing_data['flight_numbers'] or ""
            self.multiplier.default = str(existing_data['multiplier']) if existing_data['multiplier'] else "1"
            self.deadline_hours.default = str(existing_data['deadline_hours']) if existing_data['deadline_hours'] else "24"
            
            if existing_data['post_time']:
                post_dt = datetime.fromisoformat(existing_data['post_time'])
                self.post_date.default = f"{post_dt.day:02d}:{post_dt.month:02d}:{post_dt.year}"
                self.post_time.default = f"{post_dt.hour:02d}:{post_dt.minute:02d}"
        
        for item in [self.flight_numbers, self.multiplier, self.post_date, self.post_time, self.deadline_hours]:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            multiplier_val = float(self.multiplier.value)
            date_parts = self.post_date.value.split(':')
            if len(date_parts) != 3:
                raise ValueError("Date must be in dd:mm:yyyy format")
            day, month, year = map(int, date_parts)
            
            time_parts = self.post_time.value.split(':')
            if len(time_parts) != 2:
                raise ValueError("Time must be in HH:MM format")
            hour, minute = map(int, time_parts)
            
            post_time_dt = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
            deadline_hours_val = int(self.deadline_hours.value)
        except ValueError as e:
            await interaction.response.send_message(f"❌ Invalid format: {str(e)}", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        
        self.mission_data.update({
            "flight_numbers": self.flight_numbers.value,
            "multiplier": multiplier_val,
            "post_time": post_time_dt,
            "deadline_hours": deadline_hours_val,
            "author_name": "Note: You can fly any aircraft which is mentioned in CC for that route"
        })

        flight_nums = [fn.strip().upper() for fn in self.flight_numbers.value.split(',')]
        view = EmojiSelectionView(self.bot, self.mission_data, flight_nums, self.mission_id)
        await interaction.followup.send("Select emojis for each route:", view=view, ephemeral=True)

class MissionTypeView(ui.View):
    def __init__(self, bot: commands.Bot, mission_data: dict, mission_id: Optional[int] = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.mission_data = mission_data
        self.mission_id = mission_id

    @ui.button(label="Flight Mission (with routes)", style=discord.ButtonStyle.primary)
    async def flight_mission(self, interaction: discord.Interaction, button: ui.Button):
        view = FlightConfigView(self.bot, self.mission_data, self.mission_id)
        if hasattr(self, 'existing_flight_data'):
            view.existing_flight_data = self.existing_flight_data
        await interaction.response.edit_message(content="Configure flight details:", view=view)

    @ui.button(label="Simple Embed (no routes)", style=discord.ButtonStyle.secondary)
    async def simple_embed(self, interaction: discord.Interaction, button: ui.Button):
        modal = SimpleEmbedModal(self.bot, self.mission_data, self.mission_id)
        await interaction.response.send_modal(modal)

class FlightConfigView(ui.View):
    def __init__(self, bot: commands.Bot, mission_data: dict, mission_id: Optional[int] = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.mission_data = mission_data
        self.mission_id = mission_id

    @ui.button(label="Configure Flights", style=discord.ButtonStyle.primary)
    async def configure_flights(self, interaction: discord.Interaction, button: ui.Button):
        existing_data = getattr(self, 'existing_flight_data', None)
        modal = FlightModal(self.bot, self.mission_data, self.mission_id, existing_data)
        await interaction.response.send_modal(modal)

class SimpleEmbedModal(ui.Modal):
    def __init__(self, bot: commands.Bot, mission_data: dict, mission_id: Optional[int] = None):
        super().__init__(title="Simple Embed Configuration")
        self.bot = bot
        self.mission_data = mission_data
        self.mission_id = mission_id
        
        self.post_date = ui.TextInput(label="Post Date", placeholder="25:12:2024", style=discord.TextStyle.short)
        self.post_time = ui.TextInput(label="Post Time (UTC)", placeholder="18:00", style=discord.TextStyle.short)
        
        for item in [self.post_date, self.post_time]:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            date_parts = self.post_date.value.split(':')
            if len(date_parts) != 3:
                raise ValueError("Date must be in dd:mm:yyyy format")
            day, month, year = map(int, date_parts)
            
            time_parts = self.post_time.value.split(':')
            if len(time_parts) != 2:
                raise ValueError("Time must be in HH:MM format")
            hour, minute = map(int, time_parts)
            
            post_time_dt = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError as e:
            await interaction.response.send_message(f"❌ Invalid format: {str(e)}", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        
        self.mission_data.update({
            "post_time": post_time_dt,
            "flight_numbers": "N/A",
            "custom_emojis": "N/A",
            "multiplier": 0,
            "deadline_hours": 0,
            "author_name": None
        })

        if self.mission_id is None:
            await interaction.client.mission_db.create_mission(self.mission_data)
            message = f"✅ Simple embed '{self.mission_data['title']}' scheduled!"
        else:
            await interaction.client.mission_db.update_mission(self.mission_id, self.mission_data)
            message = f"✅ Simple embed '{self.mission_data['title']}' updated!"
        
        await interaction.followup.send(content=message, ephemeral=True)

class EmojiSelectionView(ui.View):
    def __init__(self, bot: commands.Bot, mission_data: dict, flight_numbers: List[str], mission_id: Optional[int] = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.mission_data = mission_data
        self.mission_id = mission_id
        self.flight_numbers = flight_numbers

        server_emojis = [e for e in bot.emojis if e.available]
        if not server_emojis:
            self.add_item(ui.Button(label="No emojis found", disabled=True, style=discord.ButtonStyle.danger))
            return

        emoji_options = [discord.SelectOption(label=emoji.name, value=str(emoji.id), emoji=emoji) for emoji in server_emojis[:25]]
        self.selects = []
        
        for i, flight in enumerate(flight_numbers):
            select = ui.Select(placeholder=f"Emoji for {flight}", options=emoji_options, custom_id=f"emoji_{i}")
            select.callback = self.emoji_selected
            self.add_item(select)
            self.selects.append(select)
    
    async def emoji_selected(self, interaction: discord.Interaction):
        await interaction.response.defer()

    @ui.button(label="Save Mission", style=discord.ButtonStyle.success, row=4)
    async def save_mission(self, interaction: discord.Interaction, button: ui.Button):
        selected_emojis = [select.values[0] for select in self.selects if select.values]
        
        if len(selected_emojis) != len(self.selects):
            await interaction.response.send_message("❌ Select emoji for every route", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        self.mission_data["custom_emojis"] = ",".join(selected_emojis)
        
        if self.mission_id is None:
            await interaction.client.mission_db.create_mission(self.mission_data)
            message = f"✅ Mission '{self.mission_data['title']}' scheduled!"
        else:
            await interaction.client.mission_db.update_mission(self.mission_id, self.mission_data)
            message = f"✅ Mission '{self.mission_data['title']}' updated!"
        
        await interaction.followup.send(content=message, ephemeral=True)

class Mission(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tasks_started = False
        self.mission_db = bot.mission_db
        self.current_interval = 3600  # Start with 1 hour (3600 seconds)

    def cog_unload(self):
        self.tasks_started = False
        if self.check_missions_task.is_running():
            self.check_missions_task.cancel()

    @tasks.loop(seconds=3600)  # Start with 1 hour
    async def check_missions_task(self):
        if not self.tasks_started:
            return
        
        missions = await self.mission_db.get_pending_missions()
        
        for mission in missions:
            await self._post_mission(mission)
            await self.mission_db.mark_mission_posted(mission['id'])
        
        await self._adjust_polling_interval()
    
    async def _adjust_polling_interval(self):
        try:
            now_utc = datetime.now(timezone.utc)
            
            next_mission = await self.mission_db.db.fetch_one(
                "SELECT post_time FROM scheduled_events WHERE is_posted = 0 AND post_time > %s ORDER BY post_time ASC LIMIT 1",
                (now_utc,)
            )
            
            if not next_mission:
                new_interval = 3600  # 1 hour
            else:
                next_time = next_mission['post_time']
                if isinstance(next_time, str):
                    next_time = datetime.fromisoformat(next_time)
                elif next_time.tzinfo is None:
                    next_time = next_time.replace(tzinfo=timezone.utc)
                
                time_diff = (next_time - now_utc).total_seconds()
                new_interval = 300 if time_diff <= 3600 else 3600  # 5 min if within 1 hour, else 1 hour
            
            if new_interval != self.current_interval:
                self.current_interval = new_interval
                self.check_missions_task.change_interval(seconds=new_interval)
                
        except Exception:
            pass

    @check_missions_task.before_loop
    async def before_check_missions(self):
        await self.bot.wait_until_ready()

    def _parse_color(self, color_str):
        if not color_str:
            return discord.Color.gold()
        
        color_str = color_str.lower().strip()
        color_map = {
            "red": discord.Color.red(), "blue": discord.Color.blue(), "green": discord.Color.green(),
            "gold": discord.Color.gold(), "purple": discord.Color.purple(), "orange": discord.Color.orange(),
            "yellow": discord.Color.yellow(), "pink": discord.Color.magenta(), "teal": discord.Color.teal()
        }
        
        if color_str in color_map:
            return color_map[color_str]
        
        if color_str.startswith('#'):
            try:
                return discord.Color(int(color_str[1:], 16))
            except ValueError:
                return discord.Color.gold()
        
        return discord.Color.gold()

    async def _post_mission(self, mission_data):
        channel = self.bot.get_channel(mission_data['channel_id'])
        if not channel:
            return
        
        embed = await self._create_mission_embed(mission_data)
        await channel.send(content="@everyone", embed=embed)
    
    async def _create_mission_embed(self, mission_data):
        embed = discord.Embed(
            title=mission_data['title'],
            description=mission_data['description'],
            color=self._parse_color(mission_data['color'] if mission_data['color'] else 'gold')
        )
        
        if mission_data['image_url']:
            embed.set_image(url=mission_data['image_url'])

        # Check if it's a simple embed (flight_numbers is N/A)
        if mission_data['flight_numbers'] == 'N/A':
            footer_text = mission_data['footer_text'] if mission_data['footer_text'] else ""
            if footer_text:
                embed.set_footer(text=footer_text)
            return embed

        # Flight mission logic
        flight_nums = [fn.strip() for fn in mission_data['flight_numbers'].split(',')]
        emoji_ids = [int(eid.strip()) for eid in mission_data['custom_emojis'].split(',') if eid.strip()]
        
        routes_text = ""
        for i, flight in enumerate(flight_nums):
            emoji = self.bot.get_emoji(emoji_ids[i]) if i < len(emoji_ids) else "✈️"
            
            if hasattr(self.bot, 'routes_model'):
                route_data = await self.bot.routes_model.find_route_by_fltnum(flight)
                if route_data:
                    aircraft = route_data['aircraft'][0]['icao'] if route_data['aircraft'] else "Unknown"
                    duration_seconds = route_data['duration']
                    hours = duration_seconds // 3600
                    minutes = (duration_seconds % 3600) // 60
                    duration_formatted = f"{hours:02d}:{minutes:02d}"
                    routes_text += f"{emoji} | {route_data['dep']} - {route_data['arr']} | {aircraft} | {duration_formatted}\n"
                else:
                    routes_text += f"{emoji} | {flight} | Route not found\n"
            else:
                routes_text += f"{emoji} | {flight}\n"
        
        embed.add_field(name="Routes", value=routes_text, inline=False)
        
        post_time = mission_data['post_time']
        if isinstance(post_time, str):
            post_time = datetime.fromisoformat(post_time)
        elif post_time.tzinfo is None:
            post_time = post_time.replace(tzinfo=timezone.utc)
        deadline = int(post_time.timestamp() + (mission_data['deadline_hours'] if mission_data['deadline_hours'] else 24) * 3600)
        
        embed.add_field(name="", value=f"You can fly any of these routes with a **{mission_data['multiplier']}x** multiplier!", inline=False)
        embed.add_field(name="", value=f"PIREPs must be filed before <t:{deadline}:f>", inline=False)
        
        footer_text = mission_data['footer_text'] if mission_data['footer_text'] else ""
        if mission_data['author_name']:
            footer_text = f"{footer_text} | {mission_data['author_name']}" if footer_text else mission_data['author_name']
        
        if footer_text:
            embed.set_footer(text=footer_text)
        
        return embed
    


    async def title_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        titles = await self.mission_db.get_mission_titles(current)
        return [app_commands.Choice(name=title, value=title) for title in titles]

    async def channel_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        choices = []
        for channel in interaction.guild.text_channels:
            if not current or current.lower() in channel.name.lower():
                choices.append(app_commands.Choice(name=f"#{channel.name}", value=str(channel.id)))
                if len(choices) >= 25:
                    break
        return choices

    @app_commands.command(name="mission", description="Manage missions")
    @app_commands.describe(action="What action to perform", title="Mission title", channel="Channel to post mission")
    @app_commands.choices(action=[
        app_commands.Choice(name="Add Mission", value="add"),
        app_commands.Choice(name="Edit Mission", value="edit"),
        app_commands.Choice(name="Delete Mission", value="delete"),
        app_commands.Choice(name="Preview Mission", value="preview"),
        app_commands.Choice(name="Force Post Mission", value="force_post"),
        app_commands.Choice(name="Start Polling", value="start_polling"),
        app_commands.Choice(name="Stop Polling", value="stop_polling")
    ])
    @app_commands.autocomplete(title=title_autocomplete, channel=channel_autocomplete)
    @is_admin()
    async def mission_command(self, interaction: discord.Interaction, action: str, title: str = None, channel: str = None):
        if action == "start_polling":
            if self.tasks_started:
                await interaction.response.send_message("Mission polling is already running.", ephemeral=True)
                return
            self.tasks_started = True
            self.check_missions_task.start()
            await interaction.response.send_message("✅ Mission polling started!", ephemeral=True)
            
        elif action == "stop_polling":
            if not self.tasks_started:
                await interaction.response.send_message("Mission polling is not currently running.", ephemeral=True)
                return
            self.tasks_started = False
            self.check_missions_task.cancel()
            await interaction.response.send_message("🛑 Mission polling stopped.", ephemeral=True)
            
        elif action == "add":
            channel_id = int(channel) if channel else interaction.channel.id
            modal = MissionModal(self.bot, channel_id, default_title=title or "")
            await interaction.response.send_modal(modal)
        elif action == "edit":
            await self._handle_edit_by_title(interaction, title)
        elif action == "delete":
            await self._handle_delete_by_title(interaction, title)
        elif action == "preview":
            await self._handle_preview_by_title(interaction, title)
        elif action == "force_post":
            await self._handle_force_post(interaction, title)

    async def _handle_edit_by_title(self, interaction, title):
        mission = await self.mission_db.get_mission_by_title(title)
        if not mission:
            await interaction.response.send_message(f"❌ Mission '{title}' not found.", ephemeral=True)
            return

        modal = MissionModal(self.bot, mission['channel_id'], mission_id=mission['id'], default_title=mission['title'])
        modal.description.default = mission['description']
        modal.image_url.default = mission['image_url'] or ""
        modal.footer_text.default = mission['footer_text'] or ""
        modal.color.default = mission['color'] or "gold"
        
        await interaction.response.send_modal(modal)

    async def _handle_delete_by_title(self, interaction, title):
        await interaction.response.defer(ephemeral=True)
        deleted_count = await self.mission_db.delete_mission_by_title(title)
        if deleted_count > 0:
            await interaction.followup.send(f"🗑️ Mission '{title}' deleted!", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Mission '{title}' not found.", ephemeral=True)

    async def _handle_preview_by_title(self, interaction, title):
        await interaction.response.defer(ephemeral=True)
        mission = await self.mission_db.get_mission_by_title(title)
        if not mission:
            await interaction.followup.send(f"❌ Mission '{title}' not found.", ephemeral=True)
            return

        embed = await self._create_mission_embed(mission)
        await interaction.followup.send(content="**PREVIEW** (will include @everyone ping when posted):", embed=embed, ephemeral=True)

    async def _handle_force_post(self, interaction, title):
        await interaction.response.defer(ephemeral=True)
        mission = await self.mission_db.get_mission_by_title(title)
        if not mission:
            await interaction.followup.send(f"❌ Mission '{title}' not found.", ephemeral=True)
            return

        await self._post_mission(mission)
        await self.mission_db.mark_mission_posted(mission['id'])
        await interaction.followup.send(f"✅ Mission '{title}' posted immediately!", ephemeral=True)



async def setup(bot: commands.Bot):
    cog = Mission(bot)
    await bot.add_cog(cog)
    
    if not cog.check_missions_task.is_running():
        cog.tasks_started = True
        cog.check_missions_task.start()
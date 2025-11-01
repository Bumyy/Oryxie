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
    def __init__(self, bot: commands.Bot, channel_id: int, mission_id: Optional[int] = None, default_title: str = "", existing_data=None):
        super().__init__(title="Mission Details")
        self.bot = bot
        self.mission_id = mission_id
        self.channel_id = channel_id
        
        self.title_input = ui.TextInput(label="Mission Title", placeholder="Welcome to Prague", default=default_title, style=discord.TextStyle.short, max_length=200)
        self.description = ui.TextInput(label="Description", placeholder="Welcome to Prague, a city where Christmas feels like...", style=discord.TextStyle.paragraph, max_length=2000)
        self.image_url = ui.TextInput(label="Image URL", placeholder="https://i.imgur.com/image.png", required=False, style=discord.TextStyle.short)
        self.footer_text = ui.TextInput(label="Footer Text", placeholder="✨ Let the magic inspire your journey! ✨", required=False, style=discord.TextStyle.short)
        self.color = ui.TextInput(label="Color", placeholder="gold, red, blue, or #FFD700", required=False, style=discord.TextStyle.short)
        self.post_date = ui.TextInput(label="Post Date", placeholder="25:12:2024", style=discord.TextStyle.short)
        self.post_time = ui.TextInput(label="Post Time (UTC)", placeholder="18:00", style=discord.TextStyle.short)
        
        if existing_data:
            self.description.default = existing_data.get('description', '')
            self.image_url.default = existing_data.get('image_url', '')
            self.footer_text.default = existing_data.get('footer_text', '')
            self.color.default = existing_data.get('color', 'gold')
            if existing_data.get('post_time'):
                post_dt = datetime.fromisoformat(existing_data['post_time']) if isinstance(existing_data['post_time'], str) else existing_data['post_time']
                self.post_date.default = f"{post_dt.day:02d}:{post_dt.month:02d}:{post_dt.year}"
                self.post_time.default = f"{post_dt.hour:02d}:{post_dt.minute:02d}"
        
        for item in [self.title_input, self.description, self.image_url, self.footer_text, self.color, self.post_date, self.post_time]:
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
        
        mission_data = {
            "title": self.title_input.value,
            "description": self.description.value,
            "image_url": self.image_url.value or None,
            "footer_text": self.footer_text.value or None,
            "color": self.color.value or "gold",
            "creator_id": interaction.user.id,
            "channel_id": self.channel_id,
            "post_time": post_time_dt,
            "flight_numbers": "N/A",
            "custom_emojis": "N/A",
            "multiplier": 0,
            "deadline_hours": 0,
            "author_name": None
        }
        
        if self.mission_id is None:
            await interaction.client.mission_db.create_mission(mission_data)
            message = f"✅ Mission '{mission_data['title']}' scheduled!"
        else:
            await interaction.client.mission_db.update_mission(self.mission_id, mission_data)
            message = f"✅ Mission '{mission_data['title']}' updated!"
        
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
        try:
            channel = self.bot.get_channel(mission_data['channel_id'])
            if not channel:
                print(f"Mission posting failed: Channel {mission_data['channel_id']} not found")
                return
            
            embed = await self._create_mission_embed(mission_data)
            await channel.send(content="@everyone", embed=embed)
            print(f"Mission '{mission_data['title']}' posted successfully to {channel.name}")
        except Exception as e:
            print(f"Error posting mission '{mission_data.get('title', 'Unknown')}': {e}")
    
    async def _create_mission_embed(self, mission_data):
        embed = discord.Embed(
            title=mission_data['title'],
            description=mission_data['description'],
            color=self._parse_color(mission_data['color'] if mission_data['color'] else 'gold')
        )
        
        if mission_data['image_url']:
            embed.set_image(url=mission_data['image_url'])

        footer_text = mission_data['footer_text'] if mission_data['footer_text'] else ""
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

        modal = MissionModal(self.bot, mission['channel_id'], mission_id=mission['id'], default_title=mission['title'], existing_data=mission)
        await interaction.response.send_modal(modal)

    async def _handle_delete_by_title(self, interaction, title):
        await interaction.response.defer(ephemeral=True)
        try:
            deleted_count = await self.mission_db.delete_mission_by_title(title)
            if deleted_count > 0:
                await interaction.followup.send(f"🗑️ Mission '{title}' deleted successfully! ({deleted_count} record(s) removed)", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Mission '{title}' not found or already deleted.", ephemeral=True)
        except Exception as e:
            print(f"Error deleting mission {title}: {e}")
            await interaction.followup.send(f"❌ Error deleting mission: {str(e)}", ephemeral=True)

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
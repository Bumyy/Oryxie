import discord
from discord import app_commands
from discord.ext import commands
import datetime
import logging
import json

from database.routes_model import RoutesModel
from database.pilots_model import PilotsModel
from services.priority_service import PriorityService

logger = logging.getLogger('oryxie.cogs.flight_poll_system')

LONG_HAUL_THRESHOLD_SECONDS = 6 * 60 * 60
MAX_ROUTES_PER_POLL = 6
MAX_POLL_DURATION_HOURS = 168

class PollSetupView(discord.ui.View):
    def __init__(self, bot, ctx_interaction, routes_data, poll_title, duration_hours, author):
        super().__init__(timeout=300)
        self.bot = bot
        self.original_interaction = ctx_interaction
        self.routes_data = routes_data
        self.poll_title = poll_title
        self.duration_hours = duration_hours
        self.author = author


        # No emoji selection needed anymore

    @discord.ui.button(label="🚀 Launch Poll", style=discord.ButtonStyle.green, row=4)
    async def launch_poll(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.author:
            return await interaction.response.send_message("Only the command author can launch this.", ephemeral=True)

        await interaction.response.defer()
        
        poll = discord.Poll(
            question=discord.PollMedia(text=self.poll_title),
            duration=datetime.timedelta(hours=self.duration_hours)
        )
        
        for index, route in enumerate(self.routes_data[:MAX_ROUTES_PER_POLL]):
            # Get aircraft codes
            ac_list = route.get('aircraft', [])
            ac_codes = []
            cog = self.bot.get_cog("FlightPollSystem")
            
            for aircraft in ac_list:
                if aircraft['icao'] not in ['XXXX', 'Unknown', '']:
                    ac_codes.append(aircraft['icao'])
                elif cog:
                    icao_code = cog.convert_aircraft_name_to_icao(aircraft['name'])
                    if icao_code != 'Heavy':
                        ac_codes.append(icao_code)
            
            ac_name = ','.join(ac_codes) if ac_codes else 'Heavy'
            
            # Format poll text
            dur_seconds = route.get('duration', 0)
            dur_str = f"{dur_seconds // 3600}h{(dur_seconds % 3600) // 60:02d}"
            atc_tag = " ATC HUB" if route.get('is_atc', False) else ""
            
            # Get livery name from route data
            livery_name = route.get('livery', 'Qatar Airways')  # Default to Qatar Airways
            print(f"DEBUG POLL: Route {index} - Livery: '{livery_name}', Flight: '{route['fltnum']}'")
            print(f"DEBUG POLL: Full route data: {route}")
            
            # Try with livery name first
            final_text = f"{livery_name}: {route['dep']}-{route['arr']} {ac_name} {dur_str}{atc_tag}"
            print(f"DEBUG POLL: Initial text: '{final_text}' (length: {len(final_text)})")
            
            # If exceeds 55 characters, use flight number instead
            if len(final_text) > 55:
                final_text = f"{route['fltnum']}: {route['dep']}-{route['arr']} {ac_name} {dur_str}{atc_tag}"
                print(f"DEBUG POLL: Using flight number due to length: '{final_text}'")
                if len(final_text) > 55:
                    final_text = final_text[:52] + "..."
                    print(f"DEBUG POLL: Truncated: '{final_text}'")
            
            print(f"DEBUG POLL: Final poll answer: '{final_text}'")
            poll.add_answer(text=final_text)

        view = PollControlView(self.bot, self.author)
        poll_message = await interaction.channel.send(poll=poll, view=view)
        view.poll_end_time = datetime.datetime.utcnow() + datetime.timedelta(hours=self.duration_hours + 24)
        
        try:
            await self.original_interaction.delete_original_response()
        except:
            pass
            
        await interaction.followup.send("✅ Poll launched successfully!", ephemeral=True)



class PollControlView(discord.ui.View):
    def __init__(self, bot, author):
        super().__init__(timeout=None)
        self.bot = bot
        self.author = author
        self.poll_closed = False
        self.post_created = False
        self.poll_end_time = None

    def _check_timer_expired(self):
        return self.poll_end_time and datetime.datetime.utcnow() > self.poll_end_time

    def _check_poll_ended(self, message):
        if not self.poll_closed and message.poll and message.poll.is_finalised():
            self.poll_closed = True
            self.poll_end_time = datetime.datetime.utcnow() + datetime.timedelta(hours=24)

    async def get_route_from_answer(self, text):
        try:
            # Handle new format: "Virgin Australia: YSSY-OTHH B77W 14h30"
            if ':' in text:
                # Split by colon to separate livery from route info
                route_part = text.split(':', 1)[1].strip()
            else:
                # Fallback for old format
                route_part = text
            
            parts = route_part.split(' ')
            if len(parts) >= 3 and '-' in parts[0]:
                dep, arr = parts[0].split('-')
                query = "SELECT * FROM routes WHERE dep = %s AND arr = %s LIMIT 1"
                result = await self.bot.db_manager.fetch_one(query, (dep, arr))
                if result:
                    result['poll_aircraft'] = parts[1]
                return result
        except Exception as e:
            logger.error(f"Error fetching route data: {e}")
        return None

    @discord.ui.button(label="🛑 Close Poll", style=discord.ButtonStyle.danger, custom_id="poll_close_btn")
    async def close_poll(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.poll_closed:
            return await interaction.response.send_message("Poll is already closed.", ephemeral=True)
            
        if self.author and interaction.user != self.author and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Only the poll creator can close this.", ephemeral=True)
        
        try:
            await interaction.message.end_poll()
            self.poll_closed = True
            button.disabled = True
            button.label = "🛑 Poll Closed"
            self.poll_end_time = datetime.datetime.utcnow() + datetime.timedelta(hours=24)
            
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("✅ Poll closed.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error closing poll: {e}", ephemeral=True)

    @discord.ui.button(label="📋 Voter List", style=discord.ButtonStyle.secondary, custom_id="poll_voters_btn")
    async def voter_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._check_poll_ended(interaction.message)
        
        if self._check_timer_expired():
            button.disabled = True
            await interaction.response.edit_message(view=self)
            return await interaction.followup.send("Voter list is no longer available.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        
        if not interaction.message.poll:
            return await interaction.followup.send("This is not a poll.", ephemeral=True)

        # Find winner
        winner = max(interaction.message.poll.answers, key=lambda x: x.vote_count, default=None)
        
        if not winner or winner.vote_count == 0:
            return await interaction.followup.send("No votes yet.", ephemeral=True)

        # Get voters
        voters = []
        try:
            async for voter in winner.voters():
                voters.append(voter)
        except:
            pass

        # Sort by priority
        members = [interaction.guild.get_member(v.id) for v in voters if interaction.guild.get_member(v.id)]
        cog = self.bot.get_cog("FlightPollSystem")
        
        if cog and members:
            sorted_voters = await cog.priority_service.sort_members_by_priority(members)
        else:
            sorted_voters = members

        if sorted_voters:
            voter_list = [f"{i+1}. {pilot.mention}" for i, pilot in enumerate(sorted_voters)]
            text = f"**📊 Voters for: {winner.text}**\n\n" + "\n".join(voter_list)
        else:
            text = f"**📊 Voters for: {winner.text}**\n\nNo valid voters found."
            
        await interaction.followup.send(text, ephemeral=True)

    @discord.ui.button(label="📢 Create Post", style=discord.ButtonStyle.primary, custom_id="poll_post_btn")
    async def create_post(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.post_created:
            return await interaction.response.send_message("Post has already been created.", ephemeral=True)
            
        self._check_poll_ended(interaction.message)
        
        if self._check_timer_expired():
            button.disabled = True
            await interaction.response.edit_message(view=self)
            return await interaction.followup.send("Create post is no longer available.", ephemeral=True)
            
        if self.author and interaction.user != self.author and not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message("You don't have permission to create the post.", ephemeral=True)
        
        await interaction.response.send_modal(DepartureTimeModal(self.bot, self.author, interaction.message, self))

class DepartureTimeModal(discord.ui.Modal, title='Set Departure Time'):
    def __init__(self, bot, author, poll_message, view):
        super().__init__()
        self.bot = bot
        self.author = author
        self.poll_message = poll_message
        self.view = view

    departure_time = discord.ui.TextInput(
        label='Departure Time',
        placeholder='Enter time (e.g., 1600z, 14:30z)',
        required=True, max_length=10
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        # Find winner
        winner = max(self.poll_message.poll.answers, key=lambda x: x.vote_count, default=None)
        if not winner:
            return await interaction.followup.send("No winning route found.", ephemeral=True)

        # Get route data
        view = PollControlView(self.bot, self.author)
        route_data = await view.get_route_from_answer(winner.text)
        if not route_data:
            return await interaction.followup.send("Could not find route data in database for winner.", ephemeral=True)

        # Get livery from route data
        livery_name = route_data.get('livery', 'Qatar Airways')
        
        # Determine target channel
        target_forum = interaction.channel.parent if isinstance(interaction.channel, discord.Thread) else interaction.channel

        # Create post content
        current_date = datetime.datetime.utcnow().strftime("%d/%m/%y")
        ac_name = route_data.get('poll_aircraft', 'Heavy')
        post_title = f"{route_data['dep']} - {route_data['arr']} Group Flight"
        dur = route_data['duration']
        d_str = f"{dur // 3600}h {(dur % 3600) // 60}m"
        organizer = self.author.mention if self.author else interaction.user.mention

        message_content = f"""📅 **Date:** {current_date}
⏰ **Time:** {self.departure_time.value}
🌍 **Departure:** {route_data['dep']}
🌍 **Destination:** {route_data['arr']}
🌐 **Multiplier:** 3x
#️⃣ **Flight Number:** {route_data['fltnum']}
⏱️ **Flight Duration:** {d_str}
✈️ **Aircraft Type:** {livery_name} {ac_name}

**Organized by:** {organizer}"""

        # Create post
        try:
            if isinstance(target_forum, discord.ForumChannel):
                tags = [t for t in target_forum.available_tags 
                       if ("Long Haul" in t.name and dur > LONG_HAUL_THRESHOLD_SECONDS) or "Qatar Airways" in t.name][:5]
                
                new_thread = await target_forum.create_thread(
                    name=post_title, content=message_content, applied_tags=tags
                )
                result_msg = f"✅ Forum Post Created: {new_thread.thread.mention}"
            else:
                await target_forum.send(message_content)
                result_msg = "✅ Post created in this channel."
            
            # Update button
            self.view.post_created = True
            for item in self.view.children:
                if item.custom_id == "poll_post_btn":
                    item.disabled = True
                    item.label = "📢 Post Created"
                    break
            
            await self.poll_message.edit(view=self.view)
            await interaction.followup.send(result_msg, ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create post: {str(e)}", ephemeral=True)

class FlightPollSystem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.routes_model = RoutesModel(self.bot.db_manager)
        self.pilots_model = PilotsModel(self.bot.db_manager)
        self.priority_service = PriorityService(self.bot)
        self.aircraft_data = self.load_aircraft_data()
    
    def load_aircraft_data(self):
        try:
            with open('assets/aircraft_data.json', 'r') as f:
                return json.load(f).get('infinite_flight', {})
        except Exception as e:
            logger.error(f"Error loading aircraft data: {e}")
            return {}
    
    def convert_aircraft_name_to_icao(self, aircraft_name):
        clean_name = aircraft_name.replace('Qatar Airways ', '').replace('Qatar ', '')
        clean_name = clean_name.replace('Virgin Australia ', '').replace('Airbus ', '').replace('Boeing ', '')
        
        fallback_map = {
            'A350': 'A359', 'A380': 'A388', '777-300ER': 'B77W', 'B777-300ER': 'B77W',
            'Boeing 777-300ER': 'B77W', '777F': 'B77F', 'A330-300': 'A333', 'A340-600': 'A346'
        }
        
        for key, icao in fallback_map.items():
            if key in clean_name:
                return icao
        
        for icao_code, full_name in self.aircraft_data.items():
            if clean_name in full_name or full_name in clean_name:
                return icao_code
        
        return 'Heavy'

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(PollControlView(self.bot, None))

    @app_commands.command(name="create_flight_poll", description="Creates an interactive Flight Poll.")
    @app_commands.describe(
        title="Title of the Poll (e.g. Today's Flight 1600z)",
        atc_routes="Flight numbers for ATC HUB (comma separated)",
        normal_routes="Flight numbers for standard routes (comma separated)",
        duration="Poll duration in hours (Default 24)"
    )
    async def create_flight_poll(self, interaction: discord.Interaction, 
                                 title: str, 
                                 atc_routes: str = None, 
                                 normal_routes: str = None, 
                                 duration: int = 24):
        await interaction.response.defer(ephemeral=True)

        if not (1 <= duration <= MAX_POLL_DURATION_HOURS):
            return await interaction.followup.send(f"❌ Duration must be between 1 and {MAX_POLL_DURATION_HOURS} hours.")

        # Gather flight numbers
        route_requests = []
        if atc_routes:
            route_requests.extend([{'flt': f.strip(), 'is_atc': True} for f in atc_routes.split(',') if f.strip()])
        if normal_routes:
            route_requests.extend([{'flt': f.strip(), 'is_atc': False} for f in normal_routes.split(',') if f.strip()])

        if not route_requests:
            return await interaction.followup.send("❌ You must provide at least one flight number.")

        # Fetch routes from database
        valid_routes = []
        try:
            for req in route_requests:
                data = await self.routes_model.find_route_by_fltnum(req['flt'])
                if data:
                    data['is_atc'] = req['is_atc']
                    valid_routes.append(data)
        except Exception as e:
            logger.error(f"Error fetching route data: {e}")
            return await interaction.followup.send("❌ Database error occurred while fetching routes.")
        
        if not valid_routes:
            return await interaction.followup.send("❌ None of the provided flight numbers were found in the database.")

        # Launch setup view
        view = PollSetupView(self.bot, interaction, valid_routes, title, duration, interaction.user)
        await interaction.followup.send(
            f"**Setup Poll:** {title}\nFound {len(valid_routes)} valid routes.\nPlease select logos below and click Launch.", 
            view=view, ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(FlightPollSystem(bot))
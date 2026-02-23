import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import logging

from services.simbrief_service import SimBriefService
from services.flight_board_service import FlightBoardService

logger = logging.getLogger(__name__)

# Configuration for the embed and buttons
MISSION_CONFIG = {
    "EMBED_TITLE": "🌍 World Tour Mission Dispatch",
    "EMBED_DESCRIPTION": (
        "**Welcome to the Mission Dispatch Center**\n\n"
        "Select a service below to manage your assigned mission.\n"
        "• **SimBrief**: Generate an operational flight plan.\n"
        "• **Flight Board**: Announce your flight to the live board."
    ),
    "EMBED_COLOR": 0x1D5367,
    "FOOTER_TEXT": "QRV Mission Systems | World Tour 2025",
    "BUTTON_SIMBRIEF_LABEL": "SimBrief Dispatch",
    "BUTTON_SIMBRIEF_EMOJI": "📋",
    "BUTTON_BOARD_LABEL": "Flight Board",
    "BUTTON_BOARD_EMOJI": "✈️",
    "ACTIVE_SETS": ["WorldTourSet1"],
    "LOGO_PATH": "assets/WT2026.PNG"
}

class MissionDispatcherCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.mission_data = self._load_mission_data()
        
        # Initialize services locally if not found on bot
        self.simbrief_service = getattr(bot, 'simbrief_service', None)
        if not self.simbrief_service:
            self.simbrief_service = SimBriefService()
            
        self.flight_board_service = getattr(bot, 'flight_board_service', None)
        if not self.flight_board_service:
            self.flight_board_service = FlightBoardService(bot)

    def _load_mission_data(self):
        try:
            path = os.path.join('assets', 'mission_dispatcher.json')
            if not os.path.exists(path):
                logger.warning("mission_dispatcher.json not found.")
                return {}
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading mission data: {e}")
            return {}

    async def get_route_details(self, mission_entry):
        """Resolves route details from JSON or Database."""
        details = {
            'flight_number': mission_entry.get('flight_number'),
            'departure': mission_entry.get('departure'),
            'arrival': mission_entry.get('arrival'),
            'aircraft': mission_entry.get('aircraft'),
            'livery': mission_entry.get('livery')
        }

        # If source is Crew Center (cc) and data is missing, fetch from DB
        if mission_entry.get('source') == 'cc' and details['flight_number']:
            if hasattr(self.bot, 'routes_model'):
                try:
                    route_data = await self.bot.routes_model.find_route_by_fltnum(details['flight_number'])
                    if route_data:
                        if not details['departure']: details['departure'] = route_data.get('dep')
                        if not details['arrival']: details['arrival'] = route_data.get('arr')
                        if not details['livery']: details['livery'] = route_data.get('livery')
                        
                        if not details['aircraft']:
                            db_aircraft = route_data.get('aircraft', [])
                            if db_aircraft:
                                ac_options = []
                                for ac in db_aircraft:
                                    icao = ac.get('icao')
                                    name = ac.get('name')
                                    if (not icao or icao == 'XXXX') and self.flight_board_service:
                                        icao = self.flight_board_service.convert_aircraft_name_to_icao(name)
                                    val = icao if icao and icao != 'XXXX' else name
                                    if val and val not in ac_options:
                                        ac_options.append(val)
                                
                                if len(ac_options) == 1:
                                    details['aircraft'] = ac_options[0]
                                elif len(ac_options) > 1:
                                    details['aircraft'] = ac_options
                except Exception as e:
                    logger.error(f"RoutesModel lookup failed for {details['flight_number']}: {e}")
            elif hasattr(self.bot, 'db_manager'):
                # Fallback if routes_model is missing
                pass
        
        if not details['livery']:
            details['livery'] = 'Qatar Airways'
        
        return details

    async def execute_action(self, interaction, mode, details):
        """Finalizes the action: Generates SimBrief link or Posts to Flight Board."""
        pilot_res = await self.bot.pilots_model.identify_pilot(interaction.user)
        if not pilot_res['success']:
            await interaction.edit_original_response(content=f"❌ {pilot_res['error_message']}", view=None)
            return
        
        pilot_data = pilot_res['pilot_data']
        callsign = pilot_data['callsign']

        if mode == "simbrief":
            if self.simbrief_service:
                link = self.simbrief_service.generate_dispatch_link(
                    origin=details['departure'],
                    destination=details['arrival'],
                    aircraft_type=details['aircraft'],
                    callsign=callsign,
                    flight_number=details['flight_number']
                )
                await interaction.edit_original_response(
                    content=f"✅ **SimBrief Dispatch Generated**\n\n**Flight:** {details['flight_number']}\n**Route:** {details['departure']} → {details['arrival']}\n**Aircraft:** {details['aircraft']}\n\n[Click to Dispatch]({link})", 
                    view=None
                )
            else:
                await interaction.edit_original_response(content="❌ SimBrief service unavailable.", view=None)
        
        elif mode == "board":
            fb_data = {
                'flight_num': details['flight_number'],
                'departure': details['departure'],
                'arrival': details['arrival'],
                'aircraft': details['aircraft'],
                'pilot_id': interaction.user.id,
                'pilot_name': interaction.user.display_name,
                'status': 'Scheduled',
                'note': "World Tour Mission",
                'flight_type': 'scheduled',
                'livery': details.get('livery', 'Qatar Airways'),
                'thumbnail_path': MISSION_CONFIG.get("LOGO_PATH")
            }
            print(f"[DEBUG] Flight Board Data prepared with thumbnail: {fb_data.get('thumbnail_path')}")
            
            if self.flight_board_service:
                msg = await self.flight_board_service.post_flight_board(fb_data)
                if msg:
                    await interaction.edit_original_response(content=f"✅ Posted to Flight Board: {msg.jump_url}", view=None)
                else:
                    await interaction.edit_original_response(content="❌ Failed to post to Flight Board.", view=None)
            else:
                await interaction.edit_original_response(content="❌ Flight Board service unavailable.", view=None)

    @app_commands.command(name="mission_dispatcher", description="Post the Mission Dispatcher panel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def mission_panel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title=MISSION_CONFIG["EMBED_TITLE"],
            description=MISSION_CONFIG["EMBED_DESCRIPTION"],
            color=MISSION_CONFIG["EMBED_COLOR"]
        )
        embed.set_footer(text=MISSION_CONFIG["FOOTER_TEXT"])
        
        view = MissionDispatcherView(self)
        
        logo_path = MISSION_CONFIG.get("LOGO_PATH", "assets/WT2026.PNG")
        print(f"[DEBUG] Checking logo path for mission panel: {logo_path}")
        print(f"[DEBUG] Absolute path: {os.path.abspath(logo_path)}")
        
        if os.path.exists(logo_path):
            print("[DEBUG] Logo file found. Attaching to embed.")
            file = discord.File(logo_path, filename="logo.png")
            embed.set_thumbnail(url="attachment://logo.png")
            await interaction.channel.send(embed=embed, view=view, file=file)
        else:
            print(f"[DEBUG] Logo file NOT found at {logo_path}. Sending embed without thumbnail.")
            await interaction.channel.send(embed=embed, view=view)
            
        await interaction.followup.send("✅ Mission panel posted.", ephemeral=True)

class MissionDispatcherView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    async def start_flow(self, interaction: discord.Interaction, mode: str):
        active_sets = MISSION_CONFIG.get("ACTIVE_SETS", [])
        if active_sets:
            sets = [k for k in active_sets if k in self.cog.mission_data]
        else:
            sets = [k for k in self.cog.mission_data.keys() if not k.startswith('_')]
        
        if not sets:
            await interaction.response.send_message("No missions loaded.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        if len(sets) == 1:
            set_name = sets[0]
            missions = self.cog.mission_data.get(set_name, {})
            view = MissionRouteSelectView(self.cog, mode, set_name, missions)
            await interaction.followup.send(f"Mission Set: **{set_name}**. Choose a route:", view=view, ephemeral=True)
        else:
            view = MissionSetSelectView(self.cog, mode, sets)
            await interaction.followup.send("Select a Mission Set:", view=view, ephemeral=True)

    @discord.ui.button(label=MISSION_CONFIG["BUTTON_SIMBRIEF_LABEL"], style=discord.ButtonStyle.primary, emoji=MISSION_CONFIG["BUTTON_SIMBRIEF_EMOJI"], custom_id="mission_simbrief_btn")
    async def simbrief_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.start_flow(interaction, "simbrief")

    @discord.ui.button(label=MISSION_CONFIG["BUTTON_BOARD_LABEL"], style=discord.ButtonStyle.success, emoji=MISSION_CONFIG["BUTTON_BOARD_EMOJI"], custom_id="mission_board_btn")
    async def board_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.start_flow(interaction, "board")

class MissionSetSelectView(discord.ui.View):
    def __init__(self, cog, mode, sets):
        super().__init__(timeout=180)
        self.cog = cog
        self.mode = mode
        
        options = [discord.SelectOption(label=s, value=s) for s in sets]
        
        if not options:
            options.append(discord.SelectOption(label="No Missions Available", value="none"))

        self.select = discord.ui.Select(placeholder="Choose a Mission Set...", options=options[:25])
        self.select.callback = self.callback
        self.add_item(self.select)

    async def callback(self, interaction: discord.Interaction):
        set_name = self.select.values[0]
        if set_name == "none":
            await interaction.response.send_message("No missions loaded.", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        missions = self.cog.mission_data.get(set_name, {})
        view = MissionRouteSelectView(self.cog, self.mode, set_name, missions)
        await interaction.edit_original_response(content=f"Selected Set: **{set_name}**. Now choose a route:", view=view)

class MissionRouteSelectView(discord.ui.View):
    def __init__(self, cog, mode, set_name, missions):
        super().__init__(timeout=180)
        self.cog = cog
        self.mode = mode
        self.set_name = set_name
        self.missions = missions
        
        options = []
        for key, data in missions.items():
            label = data.get('label') or f"{key}: {data.get('flight_number', 'Unknown')}"
            options.append(discord.SelectOption(label=label[:100], value=key, description=f"Flight: {data.get('flight_number')}"))
        
        self.select = discord.ui.Select(placeholder="Choose a Route...", options=options[:25])
        self.select.callback = self.callback
        self.add_item(self.select)

    async def callback(self, interaction: discord.Interaction):
        mission_key = self.select.values[0]
        mission_data = self.missions.get(mission_key)
        
        await interaction.response.defer(ephemeral=True)
        
        details = await self.cog.get_route_details(mission_data)
        
        if not details['departure'] or not details['arrival']:
            await interaction.edit_original_response(content=f"❌ Could not resolve route details for **{details['flight_number']}**. Please check database or config.", view=None)
            return

        aircraft_opt = details['aircraft']
        
        # If aircraft is a list, show aircraft dropdown
        if isinstance(aircraft_opt, list) and len(aircraft_opt) > 1:
            view = AircraftSelectView(self.cog, self.mode, details, aircraft_opt)
            await interaction.edit_original_response(content=f"Route: **{details['departure']} - {details['arrival']}**. Select Aircraft:", view=view)
        else:
            # Handle single aircraft or default
            if isinstance(aircraft_opt, list) and len(aircraft_opt) == 1:
                details['aircraft'] = aircraft_opt[0]
            if not details['aircraft']:
                details['aircraft'] = "B77W" # Default fallback
            
            await self.cog.execute_action(interaction, self.mode, details)

class AircraftSelectView(discord.ui.View):
    def __init__(self, cog, mode, details, aircraft_list):
        super().__init__(timeout=180)
        self.cog = cog
        self.mode = mode
        self.details = details
        
        options = [discord.SelectOption(label=ac, value=ac) for ac in aircraft_list]
        self.select = discord.ui.Select(placeholder="Select Aircraft...", options=options[:25])
        self.select.callback = self.callback
        self.add_item(self.select)

    async def callback(self, interaction: discord.Interaction):
        self.details['aircraft'] = self.select.values[0]
        await interaction.response.defer(ephemeral=True)
        await self.cog.execute_action(interaction, self.mode, self.details)

async def setup(bot):
    cog = MissionDispatcherCog(bot)
    await bot.add_cog(cog)
    bot.add_view(MissionDispatcherView(cog))
import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import re
import logging
from datetime import datetime

from services.simbrief_service import SimBriefService
from services.flight_board_service import FlightBoardService
from services.pirep_filing_service import PirepFilingService

logger = logging.getLogger(__name__)

# Configuration for the embed and buttons
MISSION_CONFIG = {
    "EMBED_TITLE": "🌍 World Tour Mission Dispatcher",
    "EMBED_DESCRIPTION": (
        "**Welcome to the Mission Dispatch Center**\n\n"
        "Tap a Button below to manage your World Tour planning.\n"
        "• **SimBrief**: Generate a Simbrief link for your selected Route and aircraft.\n"
        "• **Flight Board**: Announce your flight to the #live-Flight channel.\n"
        "• **File PIREP**: Submit PIREP of your completed flight .\n"
        "• **Leaderboard**: View your progress and rankings."
    ),
    "EMBED_COLOR": 0x1D5367,
    "FOOTER_TEXT": "QRV Mission Dispatcher | World Tour 2026",
    "BUTTON_SIMBRIEF_LABEL": "SimBrief Dispatch",
    "BUTTON_SIMBRIEF_EMOJI": "📋",
    "BUTTON_BOARD_LABEL": "Flight Board",
    "BUTTON_BOARD_EMOJI": "✈️",
    "BUTTON_PIREP_LABEL": "File PIREP",
    "BUTTON_PIREP_EMOJI": "📄",
    "BUTTON_LEADERBOARD_LABEL": "Leaderboard",
    "BUTTON_LEADERBOARD_EMOJI": "🏆",
    "ACTIVE_SETS": ["WorldTourSet2", "WorldTourSet1"],
    "LOGO_PATH": "assets/WT2026.png"
}

# --- UI Components for PIREP Filing ---

class PirepDurationModalHHMM(discord.ui.Modal, title="Enter Flight Duration"):
    hours = discord.ui.TextInput(
        label="Hours (HH)",
        placeholder="e.g., 07",
        required=True,
        max_length=2
    )
    minutes = discord.ui.TextInput(
        label="Minutes (MM)",
        placeholder="e.g., 30",
        required=True,
        max_length=2
    )

    def __init__(self, cog, pirep_data: dict):
        super().__init__()
        self.cog = cog
        self.pirep_data = pirep_data

    async def on_submit(self, interaction: discord.Interaction):
        try:
            h = int(self.hours.value)
            m = int(self.minutes.value)
            if not (0 <= h <= 99 and 0 <= m <= 59):
                raise ValueError()
        except ValueError:
            await interaction.response.send_message("❌ Invalid time format. Please use numbers for HH and MM.", ephemeral=True)
            return
        
        self.pirep_data['duration'] = f"{h:02d}:{m:02d}"
        await interaction.response.defer(ephemeral=True)
        await self.cog._start_multiplier_selection(interaction, self.pirep_data)

class PirepDurationConfirmView(discord.ui.View):
    def __init__(self, cog, pirep_data: dict):
        super().__init__(timeout=300)
        self.cog = cog
        self.pirep_data = pirep_data

    @discord.ui.button(label="✅ Proceed with this time", style=discord.ButtonStyle.success)
    async def proceed(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self.cog._start_multiplier_selection(interaction, self.pirep_data)

    @discord.ui.button(label="✏️ Edit Time", style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PirepDurationModalHHMM(self.cog, self.pirep_data)
        await interaction.response.send_modal(modal)

class PirepMultiplierSelectView(discord.ui.View):
    def __init__(self, cog, pirep_data: dict, multipliers: list):
        super().__init__(timeout=300)
        self.cog = cog
        self.pirep_data = pirep_data
        
        options = [discord.SelectOption(label=f"{m['name']} ({m['multiplier']}x)", value=str(m['id'])) for m in multipliers]
        options.insert(0, discord.SelectOption(label="No Multiplier", value="0"))

        self.select = discord.ui.Select(placeholder="Choose a Multiplier...", options=options)
        self.select.callback = self.callback
        self.add_item(self.select)

    async def callback(self, interaction: discord.Interaction):
        multiplier_id = int(self.select.values[0])
        if multiplier_id != 0:
            self.pirep_data['multiplier_id'] = multiplier_id
        
        confirm_view = PirepConfirmView(self.cog, self.pirep_data)
        await confirm_view.prepare_embed()
        await interaction.response.edit_message(content="Please confirm the final details:", embed=confirm_view.embed, view=confirm_view)

class PirepConfirmView(discord.ui.View):
    def __init__(self, cog, pirep_data: dict):
        super().__init__(timeout=300)
        self.cog = cog
        self.pirep_data = pirep_data
        self.embed = None

    async def prepare_embed(self):
        """Prepares the embed, fetching multiplier name if needed."""
        multiplier_name = "None"
        if self.pirep_data.get('multiplier_id'):
            try:
                mult_data = await self.cog.bot.multiplier_model.get_multiplier_by_id(self.pirep_data['multiplier_id'])
                if mult_data:
                    multiplier_name = f"{mult_data['name']} ({mult_data['multiplier']}x)"
            except Exception as e:
                logger.error(f"Error fetching multiplier name for confirm view: {e}")
                multiplier_name = "Error fetching name"

        embed = discord.Embed(title="📄 Confirm PIREP Details", description="Please review the details below before filing.", color=0x3498db)
        embed.add_field(name="Flight", value=self.pirep_data['flight_num'], inline=True)
        embed.add_field(name="Route", value=f"{self.pirep_data['departure']} → {self.pirep_data['arrival']}", inline=True)
        embed.add_field(name="Duration", value=self.pirep_data['duration'], inline=True)
        embed.add_field(name="Aircraft", value=f"ID: {self.pirep_data['aircraft_id']} (Fixed for WT)", inline=True)
        embed.add_field(name="Multiplier", value=multiplier_name, inline=True)
        user = None
        discord_id_str = self.pirep_data.get('discord_id')
        if discord_id_str:
            try:
                user = await self.cog.bot.fetch_user(int(discord_id_str))
            except (discord.NotFound, ValueError):
                logger.warning(f"Could not fetch user with discord_id: {discord_id_str}")
        embed.set_footer(text=f"Filing for: {user.display_name if user else 'Unknown User'}")
        self.embed = embed

    @discord.ui.button(label="✅ Confirm & File PIREP", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        for child in self.children: child.disabled = True
        await interaction.edit_original_response(view=self)
        
        response = await self.cog.pirep_filing_service.submit_pirep(
            pilot_id=self.pirep_data['pilot_id'], flight_num=self.pirep_data['flight_num'],
            dep=self.pirep_data['departure'], arr=self.pirep_data['arrival'],
            aircraft_id=self.pirep_data['aircraft_id'], duration_str=self.pirep_data['duration'],
            multiplier_id=self.pirep_data.get('multiplier_id')
        )
        
        msg = "✅ PIREP filed successfully!" if response and response.get('status') == 0 else f"❌ PIREP filing failed: {response.get('result', 'Unknown error') if response else 'No response'}"
        await interaction.followup.send(msg, ephemeral=True)
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="PIREP filing cancelled.", view=None, embed=None)
        self.stop()

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
            
        self.pirep_filing_service = getattr(bot, 'pirep_filing_service', None)
        if not self.pirep_filing_service:
            self.pirep_filing_service = PirepFilingService(bot)

    async def _start_multiplier_selection(self, interaction: discord.Interaction, pirep_data: dict):
        """Fetches multipliers and shows the selection view."""
        multipliers = await self.pirep_filing_service.get_pilot_multipliers(pirep_data.get('pilot_id'))
        
        if not multipliers:
            confirm_view = PirepConfirmView(self, pirep_data)
            await confirm_view.prepare_embed()
            # The interaction was deferred, so we must use followup.
            await interaction.followup.send(
                content="No multipliers available for your rank. Please confirm details:", 
                embed=confirm_view.embed, view=confirm_view, ephemeral=True
            )
            return

        view = PirepMultiplierSelectView(self, pirep_data, multipliers)
        # The interaction was deferred, so we must use followup.
        await interaction.followup.send(
            content="Please select a flight multiplier:", view=view, embed=None, ephemeral=True
        )

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
                                    livery = ac.get('livery', 'Standard')
                                    if (not icao or icao == 'XXXX') and self.flight_board_service:
                                        icao = self.flight_board_service.convert_aircraft_name_to_icao(name)
                                    val = icao if icao and icao != 'XXXX' else name
                                    if val and not any(opt['code'] == val and opt['livery'] == livery for opt in ac_options):
                                        ac_options.append({'code': val, 'livery': livery})
                                
                                if len(ac_options) == 1:
                                    details['aircraft'] = ac_options[0]['code']
                                    details['livery'] = ac_options[0]['livery']
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

        if mode == "simbrief":
            pilot_res = await self.bot.pilots_model.identify_pilot(interaction.user)
            if not pilot_res['success']:
                await interaction.edit_original_response(content=f"❌ {pilot_res['error_message']}", view=None)
                return
            pilot_data = pilot_res['pilot_data']
            callsign = pilot_data['callsign']
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
            pilot_res = await self.bot.pilots_model.identify_pilot(interaction.user)
            if not pilot_res['success']:
                await interaction.edit_original_response(content=f"❌ {pilot_res['error_message']}", view=None)
                return
            pilot_data = pilot_res['pilot_data']
            callsign = pilot_data['callsign']
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
        
        elif mode == "pirep":
            prepared_data = await self.pirep_filing_service.prepare_pirep_data(
                discord_id=interaction.user.id,
                flight_data=details
            )

            if not prepared_data.get('success'):
                await interaction.response.edit_message(content=f"❌ Error preparing PIREP: {prepared_data.get('error')}", view=None)
                return

            pirep_to_file = {
                'pilot_id': prepared_data['pilot_data']['id'],
                'discord_id': prepared_data['pilot_data'].get('discordid'),
                'aircraft_id': prepared_data['aircraft_data']['id'],
                'flight_num': prepared_data['pirep_data']['flight_num'],
                'departure': prepared_data['pirep_data']['departure'],
                'arrival': prepared_data['pirep_data']['arrival'],
                'duration': prepared_data['pirep_data']['duration']
            }

            if pirep_to_file['duration']:
                view = PirepDurationConfirmView(self, pirep_to_file)
                await interaction.response.edit_message(
                    content=f"⏱️ **Flight Detected!**\nWe found a matching flight with duration: **{pirep_to_file['duration']}**.",
                    view=view
                )
            else:
                modal = PirepDurationModalHHMM(self, pirep_to_file)
                await interaction.response.send_modal(modal)

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
        
        logo_path = MISSION_CONFIG.get("LOGO_PATH", "assets/WT2026.png")
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

    @discord.ui.button(label=MISSION_CONFIG["BUTTON_PIREP_LABEL"], style=discord.ButtonStyle.secondary, emoji=MISSION_CONFIG["BUTTON_PIREP_EMOJI"], custom_id="mission_pirep_btn")
    async def pirep_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.start_flow(interaction, "pirep")

    @discord.ui.button(label="Leaderboard", style=discord.ButtonStyle.secondary, emoji="🏆", custom_id="mission_leaderboard_btn")
    async def leaderboard_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        # Collect mission keys (WT1, WT2, etc.)
        mission_keys = []
        for set_name, missions in self.cog.mission_data.items():
            if set_name.startswith('_'): continue
            for key in missions.keys():
                mission_keys.append(key)
        
        if not mission_keys:
            await interaction.followup.send("No World Tour missions configured.", ephemeral=True)
            return

        # Get leaderboard data
        leaderboard = await self.cog.bot.pireps_model.get_mission_leaderboard(mission_keys)
        
        if not leaderboard:
            await interaction.followup.send("No completed World Tour flights found yet.", ephemeral=True)
            return

        # Build Embed
        embed = discord.Embed(title="🌍 World Tour Leaderboard", color=0x1D5367)
        
        # Top 10
        medals = ["🥇", "🥈", "🥉"]
        description_lines = []
        for i, entry in enumerate(leaderboard[:10]):
            medal = medals[i] if i < 3 else f"**#{i+1}**"
            display_name = f"<@{entry['discordid']}>" if entry['discordid'] else entry['pilot_name']
            count = entry['completed_count']
            description_lines.append(f"{medal} **{display_name}** — {count} Legs")
            
        embed.description = "\n".join(description_lines) if description_lines else "No data."
        
        # User Progress
        user_entry = next((x for x in leaderboard if str(x['discordid']) == str(interaction.user.id)), None)
        total_legs = len(mission_keys)
        
        if user_entry:
            rank = leaderboard.index(user_entry) + 1
            completed = user_entry['completed_count']
            percentage = (completed / total_legs) * 100 if total_legs > 0 else 0
            embed.add_field(name="Your Progress", value=f"Rank: **#{rank}**\nCompleted: **{completed}/{total_legs}** ({percentage:.1f}%)", inline=False)
        else:
            embed.add_field(name="Your Progress", value=f"You haven't completed any World Tour legs yet.\nTotal Legs: **{total_legs}**", inline=False)
            
        await interaction.followup.send(embed=embed, ephemeral=True)

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
        
        if self.mode == 'pirep':
            details = await self.cog.get_route_details(mission_data)
            details['flight_number'] = mission_key
            details['cc_aircraft_id'] = 11
            await self.cog.execute_action(interaction, self.mode, details)
            return

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
                if isinstance(aircraft_opt[0], dict):
                    details['aircraft'] = aircraft_opt[0]['code']
                    details['livery'] = aircraft_opt[0]['livery']
                else:
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
        
        options = []
        for ac in aircraft_list:
            if isinstance(ac, dict):
                label = f"{ac['code']} ({ac['livery']})"[:100]
                value = f"{ac['code']}|{ac['livery']}"
            else:
                label = str(ac)[:100]
                value = str(ac)
            options.append(discord.SelectOption(label=label, value=value))

        self.select = discord.ui.Select(placeholder="Select Aircraft...", options=options[:25])
        self.select.callback = self.callback
        self.add_item(self.select)

    async def callback(self, interaction: discord.Interaction):
        val = self.select.values[0]
        if '|' in val:
            self.details['aircraft'], self.details['livery'] = val.split('|', 1)
        else:
            self.details['aircraft'] = val
        await interaction.response.defer(ephemeral=True)
        await self.cog.execute_action(interaction, self.mode, self.details)

async def setup(bot):
    cog = MissionDispatcherCog(bot)
    await bot.add_cog(cog)
    bot.add_view(MissionDispatcherView(cog))
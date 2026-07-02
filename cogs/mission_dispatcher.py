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

# Configuration for the embed and buttons (Repurposed for World Cup 2026 CYYZ Fly-In)
MISSION_CONFIG = {
    "EMBED_TITLE": "🏆 World Cup 2026 CYYZ Fly-In",
    "EMBED_DESCRIPTION": (
        "**Welcome to the World Cup 2026 Dispatch Center**\n\n"
        "Tap a Button below to manage your World Cup planning.\n"
        "• **SimBrief**: Generate a SimBrief link for your selected route and aircraft.\n"
        "• **Flight Board**: Announce your flight to the #live-flight channel.\n"
        "• **Leaderboard**: View your progress and rankings."
    ),
    "EMBED_COLOR": 0xc41e3a,
    "FOOTER_TEXT": "QRV World Cup 2026 | CYYZ Fly-In",
    "BUTTON_SIMBRIEF_LABEL": "SimBrief Dispatch",
    "BUTTON_SIMBRIEF_EMOJI": "📋",
    "BUTTON_BOARD_LABEL": "Flight Board",
    "BUTTON_BOARD_EMOJI": "✈️",
    "BUTTON_LEADERBOARD_LABEL": "Leaderboard",
    "BUTTON_LEADERBOARD_EMOJI": "🏆",
    "LOGO_PATH": "assets/WorldCupQRV.png",
    "IMAGE_URL": "https://cdn.qatarivirtual.xyz/graphics%20and%20images/Worldcup.png",
    "ARRIVAL_AIRPORT": "CYYZ"
}

# --- UI Components for PIREP Filing (Commented Out as requested) ---
#
# class PirepDurationModalHHMM(discord.ui.Modal, title="Enter Flight Duration"):
#     hours = discord.ui.TextInput(
#         label="Hours (HH)",
#         placeholder="e.g., 07",
#         required=True,
#         max_length=2
#     )
#     minutes = discord.ui.TextInput(
#         label="Minutes (MM)",
#         placeholder="e.g., 30",
#         required=True,
#         max_length=2
#     )
#
#     def __init__(self, cog, pirep_data: dict):
#         super().__init__()
#         self.cog = cog
#         self.pirep_data = pirep_data
#
#     async def on_submit(self, interaction: discord.Interaction):
#         try:
#             h = int(self.hours.value)
#             m = int(self.minutes.value)
#             if not (0 <= h <= 99 and 0 <= m <= 59):
#                 raise ValueError()
#         except ValueError:
#             await interaction.response.send_message("❌ Invalid time format. Please use numbers for HH and MM.", ephemeral=True)
#             return
#         
#         self.pirep_data['duration'] = f"{h:02d}:{m:02d}"
#         await interaction.response.defer(ephemeral=True)
#         await self.cog._start_multiplier_selection(interaction, self.pirep_data)
#
# class PirepDurationConfirmView(discord.ui.View):
#     def __init__(self, cog, pirep_data: dict):
#         super().__init__(timeout=300)
#         self.cog = cog
#         self.pirep_data = pirep_data
#
#     @discord.ui.button(label="✅ Proceed with this time", style=discord.ButtonStyle.success)
#     async def proceed(self, interaction: discord.Interaction, button: discord.ui.Button):
#         await interaction.response.defer(ephemeral=True)
#         await self.cog._start_multiplier_selection(interaction, self.pirep_data)
#
#     @discord.ui.button(label="✏️ Edit Time", style=discord.ButtonStyle.secondary)
#     async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
#         modal = PirepDurationModalHHMM(self.cog, self.pirep_data)
#         await interaction.response.send_modal(modal)
#
# class PirepMultiplierSelectView(discord.ui.View):
#     def __init__(self, cog, pirep_data: dict, multipliers: list):
#         super().__init__(timeout=300)
#         self.cog = cog
#         self.pirep_data = pirep_data
#         
#         options = [discord.SelectOption(label=f"{m['name']} ({m['multiplier']}x)", value=str(m['id'])) for m in multipliers]
#         options.insert(0, discord.SelectOption(label="No Multiplier", value="0"))
#
#         self.select = discord.ui.Select(placeholder="Choose a Multiplier...", options=options)
#         self.select.callback = self.callback
#         self.add_item(self.select)
#
#     async def callback(self, interaction: discord.Interaction):
#         multiplier_id = int(self.select.values[0])
#         if multiplier_id != 0:
#             self.pirep_data['multiplier_id'] = multiplier_id
#         
#         confirm_view = PirepConfirmView(self.cog, self.pirep_data)
#         await confirm_view.prepare_embed()
#         await interaction.response.edit_message(content="Please confirm the final details:", embed=confirm_view.embed, view=confirm_view)
#
# class PirepConfirmView(discord.ui.View):
#     def __init__(self, cog, pirep_data: dict):
#         super().__init__(timeout=300)
#         self.cog = cog
#         self.pirep_data = pirep_data
#         self.embed = None
#
#     async def prepare_embed(self):
#         multiplier_name = "None"
#         if self.pirep_data.get('multiplier_id'):
#             try:
#                 mult_data = await self.cog.bot.multiplier_model.get_multiplier_by_id(self.pirep_data['multiplier_id'])
#                 if mult_data:
#                     multiplier_name = f"{mult_data['name']} ({mult_data['multiplier']}x)"
#             except Exception as e:
#                 logger.error(f"Error fetching multiplier name for confirm view: {e}")
#                 multiplier_name = "Error fetching name"
#
#         embed = discord.Embed(title="📄 Confirm PIREP Details", description="Please review the details below before filing.", color=0x3498db)
#         embed.add_field(name="Flight", value=self.pirep_data['flight_num'], inline=True)
#         embed.add_field(name="Route", value=f"{self.pirep_data['departure']} → {self.pirep_data['arrival']}", inline=True)
#         embed.add_field(name="Duration", value=self.pirep_data['duration'], inline=True)
#         embed.add_field(name="Aircraft", value=f"ID: {self.pirep_data['aircraft_id']} (Fixed for WT)", inline=True)
#         embed.add_field(name="Multiplier", value=multiplier_name, inline=True)
#         user = None
#         discord_id_str = self.pirep_data.get('discord_id')
#         if discord_id_str:
#             try:
#                 user = await self.cog.bot.fetch_user(int(discord_id_str))
#             except (discord.NotFound, ValueError):
#                 logger.warning(f"Could not fetch user with discord_id: {discord_id_str}")
#         embed.set_footer(text=f"Filing for: {user.display_name if user else 'Unknown User'}")
#         self.embed = embed
#
#     @discord.ui.button(label="✅ Confirm & File PIREP", style=discord.ButtonStyle.success)
#     async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
#         await interaction.response.defer(ephemeral=True)
#         for child in self.children: child.disabled = True
#         await interaction.edit_original_response(view=self)
#         
#         response = await self.cog.pirep_filing_service.submit_pirep(
#             pilot_id=self.pirep_data['pilot_id'], flight_num=self.pirep_data['flight_num'],
#             dep=self.pirep_data['departure'], arr=self.pirep_data['arrival'],
#             aircraft_id=self.pirep_data['aircraft_id'], duration_str=self.pirep_data['duration'],
#             multiplier_id=self.pirep_data.get('multiplier_id')
#         )
#         
#         msg = "✅ PIREP filed successfully!" if response and response.get('status') == 0 else f"❌ PIREP filing failed: {response.get('result', 'Unknown error') if response else 'No response'}"
#         await interaction.followup.send(msg, ephemeral=True)
#         self.stop()
#
#     @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
#     async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
#         await interaction.response.edit_message(content="PIREP filing cancelled.", view=None, embed=None)
#         self.stop()


class DepartureAirportModal(discord.ui.Modal, title="Enter Departure Airport"):
    dep_icao = discord.ui.TextInput(
        label="Departure Airport ICAO",
        placeholder="e.g., OTHH, EGLL",
        min_length=4,
        max_length=4,
        required=True
    )

    def __init__(self, cog, mode):
        super().__init__()
        self.cog = cog
        self.mode = mode

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        dep_input = self.dep_icao.value.strip().upper()
        await self.cog.process_departure_airport(interaction, self.mode, dep_input)


class MissionDispatcherCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        # Initialize services locally if not found on bot
        self.simbrief_service = getattr(bot, 'simbrief_service', None)
        if not self.simbrief_service:
            self.simbrief_service = SimBriefService()
            
        self.flight_board_service = getattr(bot, 'flight_board_service', None)
        if not self.flight_board_service:
            self.flight_board_service = FlightBoardService(bot)

    async def _get_routes_from_dep(self, dep_icao: str, arr_icao: str):
        """Queries routes from a specific departure to arrival."""
        query = """
            SELECT DISTINCT
                r.id AS route_id,
                r.dep,
                r.arr,
                r.fltnum,
                r.duration,
                a.id AS aircraft_id,
                a.icao AS aircraft_icao,
                a.name AS aircraft_name,
                a.liveryname AS aircraft_livery
            FROM
                routes r
            LEFT JOIN
                route_aircraft ra ON r.id = ra.routeid
            LEFT JOIN
                aircraft a ON ra.aircraftid = a.id
            WHERE
                r.dep = %s AND r.arr = %s
            ORDER BY
                r.fltnum
        """
        try:
            results = await self.bot.db_manager.fetch_all(query, (dep_icao, arr_icao))
            return results
        except Exception as e:
            logger.error(f"Error fetching routes from {dep_icao} to {arr_icao}: {e}")
            return []

    async def process_departure_airport(self, interaction: discord.Interaction, mode: str, dep_input: str):
        arr_icao = MISSION_CONFIG.get("ARRIVAL_AIRPORT", "CYYZ")
        raw_routes = await self._get_routes_from_dep(dep_input, arr_icao)
        
        if not raw_routes:
            await interaction.followup.send(f"❌ No routes found from **{dep_input}** to **{arr_icao}** in the database.", ephemeral=True)
            return

        # Group raw database rows by route_id
        routes = {}
        for row in raw_routes:
            route_id = row['route_id']
            if route_id not in routes:
                routes[route_id] = {
                    'route_id': route_id,
                    'dep': row['dep'],
                    'arr': row['arr'],
                    'fltnum': row['fltnum'],
                    'duration': row['duration'],
                    'aircraft': []
                }
            if row['aircraft_id'] is not None:
                aircraft_info = {
                    'id': row['aircraft_id'],
                    'icao': row['aircraft_icao'],
                    'name': row['aircraft_name'],
                    'livery': row['aircraft_livery'] or "Standard"
                }
                if aircraft_info not in routes[route_id]['aircraft']:
                    routes[route_id]['aircraft'].append(aircraft_info)

        if len(routes) == 1:
            route = list(routes.values())[0]
            await self.handle_route_selection(interaction, mode, route)
        else:
            view = MissionRouteSelectView(self, mode, dep_input, routes)
            await interaction.followup.send(
                content=f"Selected departure: **{dep_input}**. Choose a specific route/flight:", 
                view=view,
                ephemeral=True
            )

    async def handle_route_selection(self, interaction: discord.Interaction, mode: str, route: dict):
        """Processes the selected route, determining if aircraft selection is needed."""
        aircraft_options = route.get('aircraft', [])
        
        details = {
            'flight_number': route.get('fltnum'),
            'departure': route.get('dep'),
            'arrival': route.get('arr'),
            'duration': route.get('duration'),
            'aircraft': None,
            'aircraft_name': None,
            'aircraft_id': None,
            'livery': 'Qatar Airways'
        }
        
        # If there are no aircraft, fallback to B77W
        if not aircraft_options:
            details['aircraft'] = "B77W"
            details['aircraft_name'] = "Boeing 777-300ER"
            details['livery'] = "Qatar Airways"
            await self.execute_action(interaction, mode, details)
        
        # If there is exactly 1 aircraft, use it directly
        elif len(aircraft_options) == 1:
            ac = aircraft_options[0]
            details['aircraft'] = ac.get('icao') or "B77W"
            details['aircraft_name'] = ac.get('name')
            details['aircraft_id'] = ac.get('id')
            details['livery'] = ac.get('livery') or "Qatar Airways"
            await self.execute_action(interaction, mode, details)
            
        # If there are multiple aircraft, prompt user to choose
        else:
            view = MissionAircraftSelectView(self, mode, details, aircraft_options)
            await interaction.edit_original_response(
                content=f"Route: **{details['departure']} → {details['arrival']}**. Select aircraft option:", 
                view=view
            )

    async def execute_action(self, interaction, mode, details):
        """Finalizes the action: Generates SimBrief link or Posts to Flight Board."""

        if mode == "simbrief":
            pilot_res = await self.bot.pilots_model.identify_pilot(interaction.user)
            if not pilot_res['success']:
                await interaction.edit_original_response(content=f"❌ {pilot_res['error_message']}", view=None)
                return
            pilot_data = pilot_res['pilot_data']
            callsign = pilot_data['callsign']
            
            # Clean aircraft code (sometimes it's a list or dict if passed wrongly)
            aircraft_code = details['aircraft']
            if isinstance(aircraft_code, list):
                if aircraft_code and isinstance(aircraft_code[0], dict):
                    aircraft_code = aircraft_code[0]['code']
                else:
                    aircraft_code = aircraft_code[0] if aircraft_code else "B77W"

            if self.simbrief_service:
                link = self.simbrief_service.generate_dispatch_link(
                    origin=details['departure'],
                    destination=details['arrival'],
                    aircraft_type=aircraft_code,
                    callsign=callsign,
                    flight_number=details['flight_number']
                )
                await interaction.edit_original_response(
                    content=f"✅ **SimBrief Dispatch Generated**\n\n**Flight:** {details['flight_number']}\n**Route:** {details['departure']} → {details['arrival']}\n**Aircraft:** {aircraft_code}\n\n[Click to Dispatch]({link})", 
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
            
            aircraft_code = details['aircraft']
            if isinstance(aircraft_code, list):
                if aircraft_code and isinstance(aircraft_code[0], dict):
                    aircraft_code = aircraft_code[0]['code']
                else:
                    aircraft_code = aircraft_code[0] if aircraft_code else "B77W"

            fb_data = {
                'flight_num': details['flight_number'],
                'departure': details['departure'],
                'arrival': details['arrival'],
                'aircraft': aircraft_code,
                'pilot_id': interaction.user.id,
                'pilot_name': interaction.user.display_name,
                'status': 'Scheduled',
                'note': "World Cup 2026 Flight",
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
        embed.set_image(url=MISSION_CONFIG["IMAGE_URL"])
        
        view = MissionDispatcherView(self)
        await interaction.channel.send(embed=embed, view=view)
        await interaction.followup.send("✅ Mission panel posted.", ephemeral=True)


class MissionDispatcherView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label=MISSION_CONFIG["BUTTON_SIMBRIEF_LABEL"], style=discord.ButtonStyle.primary, emoji=MISSION_CONFIG["BUTTON_SIMBRIEF_EMOJI"], custom_id="mission_simbrief_btn")
    async def simbrief_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = DepartureAirportModal(self.cog, "simbrief")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label=MISSION_CONFIG["BUTTON_BOARD_LABEL"], style=discord.ButtonStyle.success, emoji=MISSION_CONFIG["BUTTON_BOARD_EMOJI"], custom_id="mission_board_btn")
    async def board_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = DepartureAirportModal(self.cog, "board")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label=MISSION_CONFIG["BUTTON_LEADERBOARD_LABEL"], style=discord.ButtonStyle.secondary, emoji=MISSION_CONFIG["BUTTON_LEADERBOARD_EMOJI"], custom_id="mission_leaderboard_btn")
    async def leaderboard_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Query leaderboard data for wc26
            leaderboard_data = await self.cog.bot.pireps_model.get_flight_number_leaderboard("wc26")

            if not leaderboard_data:
                await interaction.followup.send("ℹ️ No approved WC26 flights have been recorded yet.", ephemeral=True)
                return

            # Construct leaderboard embed
            embed = discord.Embed(
                title="🏆 WC26 Flight Leaderboard",
                description="Rankings based on the number of approved WC26 flights.",
                color=discord.Color.gold()
            )

            medals = ["🥇", "🥈", "🥉"]
            description_lines = []
            
            # Format and display the top 10 pilots
            for rank, entry in enumerate(leaderboard_data[:10], start=1):
                medal_or_rank = medals[rank - 1] if rank <= 3 else f"**#{rank}**"
                
                # Mention the user if discordid is present, otherwise display their name and callsign
                pilot_mention = f"<@{entry['discordid']}>" if entry['discordid'] else f"{entry['pilot_name']} ({entry['callsign']})"
                
                flights = entry['flight_count']
                description_lines.append(f"{medal_or_rank} {pilot_mention} — **{flights}** flights")

            embed.description = "\n".join(description_lines)
            
            # Find and display the current user's rank/stats if they're in the list
            user_entry = next((x for x in leaderboard_data if str(x['discordid']) == str(interaction.user.id)), None)
            if user_entry:
                user_rank = leaderboard_data.index(user_entry) + 1
                embed.add_field(
                    name="Your Position",
                    value=f"Rank: **#{user_rank}** | Flights: **{user_entry['flight_count']}**",
                    inline=False
                )
            else:
                embed.add_field(
                    name="Your Position",
                    value="You haven't completed any approved WC26 flights yet.",
                    inline=False
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error executing leaderboard callback: {e}")
            await interaction.followup.send("❌ An error occurred while generating the leaderboard.", ephemeral=True)


class MissionRouteSelectView(discord.ui.View):
    def __init__(self, cog, mode, dep, dep_routes):
        super().__init__(timeout=180)
        self.cog = cog
        self.mode = mode
        self.dep = dep
        self.dep_routes = dep_routes
        
        options = []
        for route_id, route in dep_routes.items():
            fltnum = route.get('fltnum') or "Unknown"
            label = f"Flight {fltnum}"
            arr_icao = MISSION_CONFIG.get("ARRIVAL_AIRPORT", "CYYZ")
            options.append(discord.SelectOption(label=label[:100], value=str(route_id), description=f"{self.dep} → {arr_icao}"))
            
        self.select = discord.ui.Select(placeholder="Choose a Flight Number...", options=options[:25])
        self.select.callback = self.callback
        self.add_item(self.select)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        route_id = int(self.select.values[0])
        route = self.dep_routes.get(route_id)
        if route:
            await self.cog.handle_route_selection(interaction, self.mode, route)
        else:
            await interaction.edit_original_response(content="❌ Selected route not found.", view=None)


class MissionAircraftSelectView(discord.ui.View):
    def __init__(self, cog, mode, details, aircraft_options):
        super().__init__(timeout=180)
        self.cog = cog
        self.mode = mode
        self.details = details
        self.aircraft_options = aircraft_options
        
        options = []
        for idx, ac in enumerate(aircraft_options):
            label = f"{ac['icao']} - {ac['name']}"
            if ac.get('livery'):
                label += f" ({ac['livery']})"
            options.append(discord.SelectOption(label=label[:100], value=str(idx)))
            
        self.select = discord.ui.Select(placeholder="Select Aircraft...", options=options[:25])
        self.select.callback = self.callback
        self.add_item(self.select)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        idx = int(self.select.values[0])
        ac = self.aircraft_options[idx]
        
        self.details['aircraft'] = ac.get('icao') or "B77W"
        self.details['aircraft_name'] = ac.get('name')
        self.details['aircraft_id'] = ac.get('id')
        self.details['livery'] = ac.get('livery') or "Qatar Airways"
        
        await self.cog.execute_action(interaction, self.mode, self.details)


async def setup(bot):
    cog = MissionDispatcherCog(bot)
    await bot.add_cog(cog)
    bot.add_view(MissionDispatcherView(cog))

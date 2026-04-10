"""
Ascaris Cog V2 - Automatic PIREP Filing System

This cog handles the Ascaris PIREP filing workflow:
1. User clicks Ascaris button → Fetch 4 recent flights from IF API
2. Display flights in embed with selection buttons
3. User selects flight → Process aircraft matching & rank check
4. Show confirmation with Proceed/Edit buttons
5. Edit opens modal with pre-filled fields
6. Multiplier dropdown selection
7. Submit PIREP to Crew Center
"""

import discord
from discord import ui
from discord import app_commands
from discord.ext import commands
from datetime import datetime
import logging

# Setup logging
logger = logging.getLogger('oryxie.ascaris_cog')

def get_final_confirmation_ui(bot, flight_info, original_user_id):
    """Helper function to build the 2nd Confirmation Screen (State 5)."""
    aircraft = flight_info['aircraft_data']
    route = flight_info['route_data']
    rank_check = flight_info['rank_check']
    pirep = flight_info['pirep_data']
    flight_type = flight_info.get('flight_type', 'normal')
    
    if flight_type == 'oneworld':
        route_status = "✅ OWD Route Found" if route else "❌ OWD Route Not Found"
        aircraft_info = f"**{aircraft.get('name', 'Unknown')}**\n🆔 CC Aircraft ID: {aircraft.get('id')} (One World)"
        desc = "Database match successful! Please review your final flight details below:"
    elif flight_type == 'event':
        route_status = "📝 Event (Manual Entry)"
        aircraft_info = f"**{aircraft.get('name', 'Unknown')}**\n🆔 CC Aircraft ID: {aircraft.get('id')} (Event)"
        desc = "Please review your event flight details below:"
    else:
        route_status = "✅ Route Found in Database" if route else "❌ Route Not Found"
        aircraft_info = f"**{aircraft.get('name', 'Unknown')}**\n🆔 CC Aircraft ID: {aircraft.get('id')}"
        if pirep.get('rank_warning'):
            aircraft_info += f"\n{pirep['rank_warning']}"
        desc = "Database match successful! Please review your final flight details below:"
        
    rank_status = "✅ Rank Requirement Met" if rank_check.get('can_fly') else f"⚠️ {rank_check.get('message', 'Rank Issue')}"
    
    embed = discord.Embed(
        title="✈️ Step 3: Final Flight Confirmation",
        description=desc,
        color=discord.Color.green()
    )
    
    embed.add_field(name="🛫 Route", value=f"**{pirep['departure']} → {pirep['arrival']}**", inline=False)
    embed.add_field(name="✈️ Aircraft", value=aircraft_info, inline=True)
    embed.add_field(name="⏱️ Flight Info", value=f"Duration: **{pirep.get('duration', 'N/A')}**\nDate: **{pirep.get('date', 'N/A')}**", inline=True)
    
    multiplier_label = pirep.get('multiplier_label', '1x (Standard)')
    embed.add_field(name="🎯 Multiplier", value=f"**{multiplier_label}**", inline=True)
    
    embed.add_field(name="✅ Validation Status", value=f"{route_status}\n{rank_status}", inline=False)
    
    flight_num = pirep.get('flight_num', '')
    if flight_num:
        source = "(from database)" if route else "(manual)"
        embed.add_field(name="🔢 Flight Number", value=f"**{flight_num}** {source}", inline=False)
    else:
        embed.add_field(name="🔢 Flight Number", value="⚠️ Not found - Must enter before proceeding", inline=False)
        
    embed.set_footer(text="Click ✅ Submit PIREP to file, or use Edit buttons to make changes")
    view = FinalConfirmationView(bot, flight_info, original_user_id)
    
    return embed, view

async def send_multiplier_selection(interaction: discord.Interaction, bot, flight_info, original_user_id):
    """Helper function to show Step 2: Multiplier Selection."""
    embed = discord.Embed(
        title="🎯 Step 2: Select Multiplier",
        description="Choose a multiplier for this PIREP before final confirmation:",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="Available Multipliers",
        value="• **1x** - Standard (100% hours)\n• **1.5x** - Bonus (150% hours)\n• **2x** - Double (200% hours)\n• **3x** - Triple (300% hours)",
        inline=False
    )
    view = MultiplierSelectionView(bot, flight_info, original_user_id)
    await interaction.edit_original_response(embed=embed, view=view)

async def send_final_confirmation(interaction: discord.Interaction, bot, flight_info, original_user_id):
    """Helper function to build the 3rd Confirmation Screen (Final)."""
    embed, view = get_final_confirmation_ui(bot, flight_info, original_user_id)
    await interaction.edit_original_response(embed=embed, view=view)

class AscarisCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        logger.info("AscarisCog initialized")

    @app_commands.command(name="ascaris", description="Automatic PIREP filing from Infinite Flight flights")
    async def ascaris_command(self, interaction: discord.Interaction):
        """Main Ascaris slash command - fetch flights and start PIREP filing."""
        logger.info(f"Ascaris slash command triggered by user: {interaction.user.id} ({interaction.user.display_name})")
        await interaction.response.send_message("🔄 Fetching your recent flights from Infinite Flight...", ephemeral=False)
        await self.fetch_and_display_flights(interaction)

    async def fetch_and_display_flights(self, interaction: discord.Interaction):
        """Fetch flights from IF API and display selection embed (for slash command usage)."""
        logger.info(f"Fetching IF flights for Discord user: {interaction.user.id}")
        # Call service to fetch flights
        result = await self.bot.pirep_filing_service.fetch_if_flights(interaction.user.id)
        
        if not result['success']:
            logger.error(f"Failed to fetch flights: {result.get('error')}")
            embed = discord.Embed(
                title="❌ Error Fetching Flights",
                description=result['error'],
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
            return
        
        flights = result['flights']
        pilot_data = result['pilot_data']

        if not flights:
            embed = discord.Embed(
                title="❌ No Flights Found",
                description="No recent flights were found for your linked Infinite Flight account. Please make sure you have flown recently.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
            return

        # Create selection embed
        embed = discord.Embed(
            title="✈️ Ascaris - Select Your Flight",
            description=f"**{pilot_data.get('callsign', 'Unknown')}** - Choose a flight to file a PIREP for.",
            color=discord.Color.blue()
        )
        
        # Add flight fields
        for i, flight in enumerate(flights, 1):
            route = f"{flight['departure']} → {flight['arrival']}"
            embed.add_field(
                name=f"Flight {i}",
                value=f"🛫 **{route}**\n⏱️ Duration: {flight['duration']}\n📅 Date: {flight['date']}\n✈️ Aircraft ID: {str(flight.get('aircraft_id', 'N/A'))[:20]}...",
                inline=True
            )

        embed.set_footer(text="Click a button below to select your flight")
        # Create view with buttons
        view = FlightSelectionView(self.bot, pilot_data, flights, interaction.user.id)

        logger.info(f"Sending flight selection embed with {len(flights)} flights")
        await interaction.followup.send(embed=embed, view=view)

class FlightSelectionView(ui.View):
    """View for selecting a flight from the list."""

    def __init__(self, bot, pilot_data, flights, original_user_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.pilot_data = pilot_data
        self.flights = flights
        self.original_user_id = original_user_id
        
        # Add buttons for each flight
        for i, flight in enumerate(flights, 1):
            button = ui.Button(
                label=f"Flight {i}",
                style=discord.ButtonStyle.primary,
                custom_id=f"ascaris_flight_{i}"
            )
            button.callback = self.create_flight_callback(i - 1)
            self.add_item(button)

    def create_flight_callback(self, flight_index):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.original_user_id:
                await interaction.response.send_message("❌ This interaction was not initiated by you.", ephemeral=True)
                return
            await self.process_flight_selection(interaction, flight_index)
        return callback

    async def process_flight_selection(self, interaction: discord.Interaction, flight_index: int):
        """Process the selected flight based on flight type."""
        flight = self.flights[flight_index]
        await interaction.response.defer()
        
        logger.info(f"[ASCARIS V2] User {interaction.user.id} selected flight {flight_index + 1}: {flight.get('departure')} -> {flight.get('arrival')}")
        flight_info = {
            'pilot_data': self.pilot_data,
            'flight_data': flight
        }
        embed = discord.Embed(
            title="🔍 Step 1: Verify Raw Flight Data",
            description="Please verify the flight data fetched from Infinite Flight.\nIf anything is incorrect, click **✏️ Edit**.",
            color=discord.Color.orange()
        )
        embed.add_field(name="🛫 Route", value=f"**{flight.get('departure', 'N/A')} → {flight.get('arrival', 'N/A')}**", inline=False)
        embed.add_field(name="📅 Date", value=flight.get('date', 'N/A'), inline=True)
        embed.add_field(name="⏱️ Duration", value=flight.get('duration', 'N/A'), inline=True)

        view = RawDataConfirmationView(self.bot, flight_info, self.original_user_id)
        await interaction.followup.send(embed=embed, view=view)

class RouteSelectionDropdownView(ui.View):
    def __init__(self, bot, flight_info, routes_list, original_user_id):
        super().__init__(timeout=300)
        self.bot = bot
        self.flight_info = flight_info
        self.routes_list = routes_list
        self.original_user_id = original_user_id
        
        select_options = []
        for r in routes_list[:25]:
            label = str(r.get('fltnum', 'Unknown Route'))
            if len(label) > 100:
                label = label[:97] + "..."
                
            select_options.append(discord.SelectOption(label=label, value=str(r['route_id']), description="Select this flight number"))
            
        select = ui.Select(placeholder="Select a route...", options=select_options, custom_id="ascaris_route_select")
        select.callback = self.route_select_callback
        self.add_item(select)
        
    async def route_select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("❌ This interaction was not initiated by you.", ephemeral=True)
            return
            
        selected_route_id = int(interaction.data['values'][0])
        await interaction.response.defer()
        
        logger.info(f"[ASCARIS V2] User {interaction.user.id} selected route ID {selected_route_id} from multiple options.")
        selected_route = next((r for r in self.routes_list if str(r['route_id']) == str(selected_route_id)), None)
        if not selected_route:
            await interaction.followup.send("❌ Error finding selected route.", ephemeral=True)
            return
            
        self.flight_info['route_data'] = selected_route
        self.flight_info['pirep_data']['flight_num'] = selected_route.get('fltnum', '')
        
        is_invalid_aircraft = self.flight_info.get('is_invalid_aircraft', False)
        if is_invalid_aircraft:
            route_aircraft_list = selected_route.get('aircraft', [])
            if route_aircraft_list:
                aircraft_options = []
                for ac in route_aircraft_list:
                    if ac.get('id'):
                        aircraft_options.append({
                            'id': ac.get('id'),
                            'name': ac.get('name', ''),
                            'icao': ac.get('icao', ''),
                            'livery': ac.get('livery', '')
                        })
                if aircraft_options:
                    embed = discord.Embed(
                        title="✈️ Select Aircraft",
                        description=f"Route **{self.flight_info['flight_data'].get('departure')} → {self.flight_info['flight_data'].get('arrival')}** found in database.\n\nYour Infinite Flight aircraft data was not recognized. Please select an aircraft from the dropdown below:",
                        color=discord.Color.blue()
                    )
                    view = AircraftDropdownView(self.bot, self.flight_info['pilot_data'], self.flight_info['flight_data'], selected_route, aircraft_options, self.original_user_id)
                    await interaction.edit_original_response(embed=embed, view=view)
                    return
                    
        await send_multiplier_selection(interaction, self.bot, self.flight_info, self.original_user_id)


class AircraftDropdownView(ui.View):
    def __init__(self, bot, pilot_data, flight_data, route_data, aircraft_options, original_user_id):
        super().__init__(timeout=300)
        self.bot = bot
        self.pilot_data = pilot_data
        self.flight_data = flight_data
        self.route_data = route_data
        self.aircraft_options = aircraft_options
        self.original_user_id = original_user_id
        
        select_options = []
        for ac in aircraft_options:
            label = f"{ac['name']} ({ac['livery']})"
            if len(label) > 100:
                label = label[:97] + "..."
            value = str(ac['id'])
            select_options.append(discord.SelectOption(label=label, value=value))
        select_options.append(discord.SelectOption(label="📝 ROTW (or Events)", value="manual"))
        
        select = ui.Select(placeholder="Select an aircraft...", options=select_options, custom_id="ascaris_aircraft_select")
        select.callback = self.aircraft_select_callback
        self.add_item(select)
    
    async def aircraft_select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("❌ This interaction was not initiated by you.", ephemeral=True)
            return

        selected_value = interaction.data['values'][0]
        await interaction.response.defer()
        
        logger.info(f"[ASCARIS V2] User {interaction.user.id} selected aircraft option: {selected_value}")
        if selected_value == "manual":
            aircraft = await self.bot.aircraft_model.get_aircraft_by_id(11)
            if not aircraft:
                embed_err = discord.Embed(title="❌ Error", description="Default aircraft (ID: 11) not found.", color=discord.Color.red())
                await interaction.followup.send(embed=embed_err)
                return
            
            flight_num = self.route_data.get('fltnum', '') if self.route_data else ''
            pirep_data = {
                'departure': self.flight_data.get('departure', ''),
                'arrival': self.flight_data.get('arrival', ''),
                'aircraft_id': aircraft['id'],
                'aircraft_name': aircraft.get('name', ''),
                'livery': aircraft.get('liveryname', ''),
                'flight_num': flight_num,
                'duration': self.flight_data.get('duration', ''),
                'duration_seconds': self.flight_data.get('duration_seconds', 0),
                'date': self.flight_data.get('date', datetime.now().strftime('%Y-%m-%d')),
                'route_found': self.route_data is not None,
                'rank_warning': "⚠️ ROTW (or Events) used as default aircraft"
            }
            
            flight_info = {
                'pilot_data': self.pilot_data,
                'flight_data': self.flight_data,
                'aircraft_data': aircraft,
                'route_data': self.route_data,
                'rank_check': {'can_fly': True, 'message': 'ROTW (or Events) entry'},
                'pirep_data': pirep_data
            }
            
            # Use standalone send confirmation function logic
            self.clear_items()
            await self._send_confirmation_from_dropdown(interaction, flight_info)
            return
        
        selected_aircraft_id = int(selected_value)
        aircraft = await self.bot.aircraft_model.get_aircraft_by_id(selected_aircraft_id)
        
        if not aircraft:
            embed_err = discord.Embed(title="❌ Error", description="Selected aircraft not found in database.", color=discord.Color.red())
            await interaction.followup.send(embed=embed_err)
            return
        
        rank_check = await self.bot.rank_model.can_pilot_fly_aircraft(self.pilot_data['id'], selected_aircraft_id)
        rank_warning = None
        if not rank_check.get('can_fly'):
            aircraft = await self.bot.aircraft_model.get_aircraft_by_id(11)
            if aircraft:
                rank_warning = "⚠️ Selected aircraft not in rank, used default aircraft"
                rank_check = {'can_fly': True, 'message': rank_warning}
        
        flight_num = self.route_data.get('fltnum', '') if self.route_data else ''
        pirep_data = {
            'departure': self.flight_data.get('departure', ''),
            'arrival': self.flight_data.get('arrival', ''),
            'aircraft_id': aircraft['id'],
            'aircraft_name': aircraft.get('name', ''),
            'livery': aircraft.get('liveryname', ''),
            'flight_num': flight_num,
            'duration': self.flight_data.get('duration', ''),
            'duration_seconds': self.flight_data.get('duration_seconds', 0),
            'date': self.flight_data.get('date', datetime.now().strftime('%Y-%m-%d')),
            'route_found': self.route_data is not None,
            'rank_warning': rank_warning
        }
        
        flight_info = {
            'pilot_data': self.pilot_data,
            'flight_data': self.flight_data,
            'aircraft_data': aircraft,
            'route_data': self.route_data,
            'rank_check': rank_check,
            'pirep_data': pirep_data
        }
        
        self.clear_items()
        await self._send_confirmation_from_dropdown(interaction, flight_info)

    async def _send_confirmation_from_dropdown(self, interaction: discord.Interaction, flight_info):
        # For normal flights coming from the dropdown, immediately go to the multiplier screen
        await send_multiplier_selection(interaction, self.bot, flight_info, self.original_user_id)

class RawDataConfirmationView(ui.View):
    """Step 1: Verification view for Normal flights."""
    def __init__(self, bot, flight_info, original_user_id):
        super().__init__(timeout=300)
        self.bot = bot
        self.flight_info = flight_info
        self.original_user_id = original_user_id

    @ui.button(label="✅ Proceed", style=discord.ButtonStyle.success, custom_id="raw_proceed")
    async def proceed_btn(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("❌ This interaction was not initiated by you.", ephemeral=True)
            return

        await interaction.response.defer()
        flight_data = self.flight_info['flight_data']
        pilot_data = self.flight_info['pilot_data']
        
        logger.info(f"[ASCARIS V2] User {interaction.user.id} clicked Proceed. Auto-detecting flight type for {flight_data.get('departure')} -> {flight_data.get('arrival')}")

        # NEW: Call the waterfall logic service method
        result = await self.bot.pirep_filing_service.auto_process_flight(
            pilot_data['id'],
            flight_data
        )
        
        logger.info(f"[ASCARIS V2] Auto-detection result: success={result.get('success')}, type={result.get('flight_type')}")
        
        if not result['success']:
            embed = discord.Embed(title="❌ Error", description=result.get('error'), color=discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
            
        flight_type = result.get('flight_type')

        if flight_type == 'oneworld' or flight_type == 'event':
            self.flight_info.update({
                'aircraft_data': result['aircraft_data'],
                'route_data': result['route_data'],
                'rank_check': result['rank_check'],
                'pirep_data': result['pirep_data'],
                'is_invalid_aircraft': False,
                'flight_type': flight_type
            })
            await send_multiplier_selection(interaction, self.bot, self.flight_info, self.original_user_id)
            return
            
        if not result.get('route_data'):
            await interaction.followup.send("❌ **Route not found in the CC database.**\nPlease click **✏️ Edit** to correct your Departure or Arrival ICAO.", ephemeral=True)
            return
            
        exact_aircraft = await self.bot.aircraft_model.get_aircraft_by_if_ids(
            flight_data.get('aircraft_id', ''),
            flight_data.get('livery_id', '')
        )
        is_invalid_aircraft = (exact_aircraft is None)
        
        logger.info(f"[ASCARIS V2] Aircraft exact match: {not is_invalid_aircraft} (IF_ID: {flight_data.get('aircraft_id')})")
            
        self.flight_info.update({
            'aircraft_data': result['aircraft_data'],
            'route_data': result['route_data'],
            'rank_check': result['rank_check'],
            'pirep_data': result['pirep_data'],
            'is_invalid_aircraft': is_invalid_aircraft,
            'flight_type': flight_type
        })

        if result.get('multiple_routes'):
            logger.info(f"[ASCARIS V2] Multiple routes found ({len(result['routes_list'])}). Showing RouteSelectionDropdownView.")
            options = []
            for r in result['routes_list']:
                label = str(r['fltnum'])
                if len(label) > 100:
                    label = label[:97] + "..."
                options.append(discord.SelectOption(label=label, value=str(r['route_id'])))
                
            embed = discord.Embed(
                title="🔀 Select Route",
                description="Multiple routes found for these airports. Please select your flight number:",
                color=discord.Color.blue()
            )
            view = RouteSelectionDropdownView(self.bot, self.flight_info, result['routes_list'], self.original_user_id)
            await interaction.edit_original_response(embed=embed, view=view)
            return
        
        if is_invalid_aircraft:
            route_aircraft_list = result['route_data'].get('aircraft', [])
            logger.info(f"[ASCARIS V2] Invalid aircraft detected. Found {len(route_aircraft_list)} allowed aircraft for this route.")
            if route_aircraft_list:
                aircraft_options = []
                for ac in route_aircraft_list:
                    if ac.get('id'):
                        aircraft_options.append({
                            'id': ac.get('id'),
                            'name': ac.get('name', ''),
                            'icao': ac.get('icao', ''),
                            'livery': ac.get('livery', '')
                        })
                if aircraft_options:
                    embed = discord.Embed(
                        title="✈️ Select Aircraft",
                        description=f"Route **{flight_data.get('departure')} → {flight_data.get('arrival')}** found in database.\n\nYour Infinite Flight aircraft data was not recognized. Please select an aircraft from the dropdown below:",
                        color=discord.Color.blue()
                    )
                    view = AircraftDropdownView(self.bot, pilot_data, flight_data, result['route_data'], aircraft_options, self.original_user_id)
                    await interaction.edit_original_response(embed=embed, view=view)
                    return
                    
        await send_multiplier_selection(interaction, self.bot, self.flight_info, self.original_user_id)

    @ui.button(label="✏️ Edit", style=discord.ButtonStyle.secondary, custom_id="raw_edit")
    async def edit_btn(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("❌ This interaction was not initiated by you.", ephemeral=True)
            return
        modal = RawDataEditModal(self.bot, self.flight_info, self.original_user_id)
        await interaction.response.send_modal(modal)

class RawDataEditModal(ui.Modal):
    """Modal to edit raw data with exact 5 fields."""
    def __init__(self, bot, flight_info, original_user_id):
        super().__init__(title="✏️ Edit Raw Flight Data")
        self.bot = bot
        self.flight_info = flight_info
        self.original_user_id = original_user_id
        
        flight_data = flight_info['flight_data']
        
        self.departure = ui.TextInput(label="Departure Airport (ICAO)", default=flight_data.get('departure', ''), required=True, max_length=4)
        self.add_item(self.departure)
        
        self.arrival = ui.TextInput(label="Arrival Airport (ICAO)", default=flight_data.get('arrival', ''), required=True, max_length=4)
        self.add_item(self.arrival)
        
        self.date = ui.TextInput(label="Flight Date (YYYY-MM-DD)", default=flight_data.get('date', datetime.now().strftime('%Y-%m-%d')), required=True, max_length=10)
        self.add_item(self.date)
        
        duration = flight_data.get('duration', '00:00')
        parts = duration.split(':')
        hh = parts[0] if len(parts) > 0 else '00'
        mm = parts[1] if len(parts) > 1 else '00'
        
        self.hours = ui.TextInput(label="Hours (HH)", default=hh, required=True, max_length=2)
        self.add_item(self.hours)
        
        self.minutes = ui.TextInput(label="Minutes (MM)", default=mm, required=True, max_length=2)
        self.add_item(self.minutes)

    async def on_submit(self, interaction: discord.Interaction):
        self.flight_info['flight_data']['departure'] = self.departure.value.upper().strip()
        self.flight_info['flight_data']['arrival'] = self.arrival.value.upper().strip()
        self.flight_info['flight_data']['date'] = self.date.value.strip()
        
        hh = self.hours.value.strip().zfill(2)
        mm = self.minutes.value.strip().zfill(2)
        self.flight_info['flight_data']['duration'] = f"{hh}:{mm}"
        
        logger.info(f"[ASCARIS V2] User {interaction.user.id} submitted raw data edits: {self.flight_info['flight_data']['departure']} -> {self.flight_info['flight_data']['arrival']}, Duration: {hh}:{mm}")
        flight = self.flight_info['flight_data']
        
        embed = discord.Embed(
            title="🔍 Step 1: Verify Raw Flight Data",
            description="Please verify the flight data fetched from Infinite Flight.\nIf anything is incorrect, click **✏️ Edit**.",
            color=discord.Color.orange()
        )
        embed.add_field(name="🛫 Route", value=f"**{flight.get('departure', 'N/A')} → {flight.get('arrival', 'N/A')}**", inline=False)
        embed.add_field(name="📅 Date", value=flight.get('date', 'N/A'), inline=True)
        embed.add_field(name="⏱️ Duration", value=flight.get('duration', 'N/A'), inline=True)
        
        view = RawDataConfirmationView(self.bot, self.flight_info, self.original_user_id)
        await interaction.response.edit_message(embed=embed, view=view)

class FinalConfirmationView(ui.View):
    """Step 3: Final confirmation screen before submitting."""
    def __init__(self, bot, flight_info, original_user_id):
        super().__init__(timeout=300)
        self.bot = bot
        self.flight_info = flight_info
        self.original_user_id = original_user_id
    
    @ui.button(label="✅ Submit PIREP", style=discord.ButtonStyle.success, custom_id="final_submit")
    async def submit_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("❌ This interaction was not initiated by you.", ephemeral=True)
            return
            
        flight_num = self.flight_info['pirep_data'].get('flight_num', '').strip()
        if not flight_num:
            modal = FlightNumberInputModal(self.bot, self.flight_info, self.original_user_id)
            await interaction.response.send_modal(modal)
            return
            
        await interaction.response.defer()
        
        pirep_data = self.flight_info['pirep_data']
        pilot_data = self.flight_info['pilot_data']
        multiplier = pirep_data.get('multiplier', '')
        multiplier_label = pirep_data.get('multiplier_label', '1x (Standard)')
        
        logger.info(f"[ASCARIS V2] User {interaction.user.id} submitting PIREP with multiplier: '{multiplier_label}'")
        
        result = await self.bot.pirep_filing_service.submit_ascaris_pirep(
            pilot_id=pilot_data['id'],
            flight_num=pirep_data.get('flight_num', 'ASC'),
            departure=pirep_data['departure'],
            arrival=pirep_data['arrival'],
            flight_time=pirep_data['duration'],
            date=pirep_data['date'],
            aircraft_id=pirep_data['aircraft_id'],
            multiplier=multiplier
        )
        
        logger.info(f"[ASCARIS V2] PIREP Submission result: success={result.get('success')}")
        if result['success']:
            embed = discord.Embed(title="✅ PIREP Filed Successfully!", description=f"🎉 Your PIREP has been submitted to Crew Center!", color=discord.Color.green())
            embed.add_field(name="✈️ Flight Details", value=f"**{pirep_data.get('flight_num', 'N/A')}** | {pirep_data['departure']} → {pirep_data['arrival']}", inline=False)
            embed.add_field(name="⏱️ Duration", value=pirep_data['duration'], inline=True)
            embed.add_field(name="🎯 Multiplier", value=multiplier_label, inline=True)
            embed.add_field(name="👤 Pilot", value=f"**{pilot_data.get('callsign', 'Unknown')}**", inline=True)
            embed.set_footer(text="Your flight has been safely logged. Thanks for flying QRV! <:qatari:1094679033205227580>")
        else:
            embed = discord.Embed(title="❌ PIREP Submission Failed", description=f"**Error:** {result.get('error', 'Unknown error occurred')}\n\nPlease try again or contact staff.", color=discord.Color.red())
            embed.add_field(name="Debug Info", value=f"Pilot ID: {pilot_data['id']}\nFlight: {pirep_data.get('flight_num')} {pirep_data['departure']}→{pirep_data['arrival']}\nAircraft ID: {pirep_data['aircraft_id']}", inline=False)
        
        # Disable all buttons upon submission
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)
        await interaction.followup.send(embed=embed)

    @ui.button(label="🎯 Edit Multiplier", style=discord.ButtonStyle.primary, custom_id="final_edit_multiplier")
    async def edit_multiplier_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("❌ This interaction was not initiated by you.", ephemeral=True)
            return
        
        await interaction.response.defer()
        await send_multiplier_selection(interaction, self.bot, self.flight_info, self.original_user_id)
    
    @ui.button(label="⬅️ Back to Step 1", style=discord.ButtonStyle.secondary, custom_id="final_back")
    async def back_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("❌ This interaction was not initiated by you.", ephemeral=True)
            return
        
        flight = self.flight_info['flight_data']
        embed = discord.Embed(
            title="🔍 Step 1: Verify Raw Flight Data",
            description="Please verify the flight data fetched from Infinite Flight.\nIf anything is incorrect, click **✏️ Edit**.",
            color=discord.Color.orange()
        )
        embed.add_field(name="🛫 Route", value=f"**{flight.get('departure', 'N/A')} → {flight.get('arrival', 'N/A')}**", inline=False)
        embed.add_field(name="📅 Date", value=flight.get('date', 'N/A'), inline=True)
        embed.add_field(name="⏱️ Duration", value=flight.get('duration', 'N/A'), inline=True)
        
        view = RawDataConfirmationView(self.bot, self.flight_info, self.original_user_id)
        await interaction.response.edit_message(embed=embed, view=view)

class FlightNumberInputModal(ui.Modal):
    def __init__(self, bot, flight_info, original_user_id):
        super().__init__(title="✏️ Enter Flight Number")
        self.bot = bot
        self.flight_info = flight_info
        self.original_user_id = original_user_id
        
        self.flight_number = ui.TextInput(
            label="Flight Number", 
            placeholder="e.g., QR101", 
            required=True, 
            max_length=10
        )
        self.add_item(self.flight_number)
        
    async def on_submit(self, interaction: discord.Interaction):
        self.flight_info['pirep_data']['flight_num'] = self.flight_number.value.strip().upper()
        
        embed, view = get_final_confirmation_ui(self.bot, self.flight_info, self.original_user_id)
        await interaction.response.edit_message(embed=embed, view=view)

async def setup(bot):
    """Setup function to add cog to bot."""
    logger.info("Loading AscarisCog...")
    await bot.add_cog(AscarisCog(bot))
    logger.info("AscarisCog loaded successfully!")

class MultiplierSelectionView(ui.View):
    def __init__(self, bot, flight_info, original_user_id):
        super().__init__(timeout=300)
        self.bot = bot
        self.flight_info = flight_info
        self.original_user_id = original_user_id
    
    @ui.select(
        placeholder="Select a multiplier...",
        options=[
            discord.SelectOption(label="1x (Standard)", value="empty", description="100% flight hours"),
            discord.SelectOption(label="1.5x (Bonus)", value="150000", description="150% flight hours"),
            discord.SelectOption(label="2x (Double)", value="200000", description="200% flight hours"),
            discord.SelectOption(label="3x (Triple)", value="300000", description="300% flight hours"),
        ],
        custom_id="ascaris_multiplier_select"
    )
    async def multiplier_select(self, interaction: discord.Interaction, select: ui.Select):
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("❌ This interaction was not initiated by you.", ephemeral=True)
            return

        multiplier = select.values[0] if select.values[0] != "empty" else ""
        multiplier_label = next((opt.label for opt in select.options if opt.value == select.values[0]), "1x (Standard)")
        
        await interaction.response.defer()
        
        logger.info(f"[ASCARIS V2] User {interaction.user.id} selected multiplier: '{multiplier_label}'. Proceeding to final confirmation.")
        
        self.flight_info['pirep_data']['multiplier'] = multiplier
        self.flight_info['pirep_data']['multiplier_label'] = multiplier_label
        
        await send_final_confirmation(interaction, self.bot, self.flight_info, self.original_user_id)
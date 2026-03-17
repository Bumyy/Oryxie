"""
Ascaris Cog - Automatic PIREP Filing System

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


class AscarisCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        logger.info("AscarisCog initialized")
        
    @app_commands.command(name="ascaris", description="Automatic PIREP filing from Infinite Flight flights")
    @app_commands.describe(
        flight_type="Type of flight: normal (CC routes), oneworld (OWD routes), or event"
    )
    @app_commands.choices(flight_type=[
        app_commands.Choice(name="Normal Flight", value="normal"),
        app_commands.Choice(name="One World", value="oneworld"),
        app_commands.Choice(name="Event", value="event")
    ])
    async def ascaris_command(self, interaction: discord.Interaction, flight_type: str = "normal"):
        """Main Ascaris slash command - fetch flights and start PIREP filing."""
        logger.info(f"Ascaris slash command triggered by user: {interaction.user.id} ({interaction.user.display_name}), flight_type: {flight_type}")
        
        # Store flight type for later use
        self.bot.ascaris_flight_type = flight_type
        
        await interaction.response.send_message("🔄 Fetching your recent flights from Infinite Flight...", ephemeral=False)
        await self.fetch_and_display_flights(interaction, flight_type)
    
    async def fetch_and_display_flights(self, interaction: discord.Interaction, flight_type: str = "normal"):
        """Fetch flights from IF API and display selection embed (for slash command usage)."""
        logger.info(f"Fetching IF flights for Discord user: {interaction.user.id}, flight_type: {flight_type}")
        
        # Call service to fetch flights
        result = await self.bot.pirep_filing_service.fetch_if_flights(interaction.user.id)
        
        logger.info(f"fetch_if_flights result: success={result.get('success')}, flights_count={len(result.get('flights', []))}")
        
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
        
        logger.info(f"Pilot data: id={pilot_data.get('id')}, callsign={pilot_data.get('callsign')}, ifuserid={pilot_data.get('ifuserid')}")
        
        if not flights:
            logger.warning("No flights found in IF API response")
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
            description=f"**{pilot_data.get('callsign', 'Unknown')}** - Choose a flight to file PIREP",
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
        
        # Add flight type info to embed
        flight_type_display = {
            "normal": "Normal Flight (CC Routes)",
            "oneworld": "One World (OWD Routes)",
            "event": "Event (Manual Entry)"
        }.get(flight_type, "Normal Flight")
        
        embed.add_field(
            name="📋 Flight Type",
            value=f"**{flight_type_display}**",
            inline=False
        )
        
        embed.set_footer(text="Click a button below to select your flight")
        
        # Create view with buttons - pass flight_type
        view = FlightSelectionView(self.bot, pilot_data, flights, flight_type)
        
        logger.info(f"Sending flight selection embed with {len(flights)} flights")
        await interaction.followup.send(embed=embed, view=view)


class FlightSelectionView(ui.View):
    """View for selecting a flight from the list."""
    
    def __init__(self, bot, pilot_data, flights, flight_type: str = "normal"):
        super().__init__(timeout=300)
        self.bot = bot
        self.pilot_data = pilot_data
        self.flights = flights
        self.flight_type = flight_type
        
        logger.info(f"Creating FlightSelectionView with {len(flights)} flights for pilot {pilot_data.get('callsign')}, flight_type: {flight_type}")
        
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
            await self.process_flight_selection(interaction, flight_index)
        return callback
    
    async def process_flight_selection(self, interaction: discord.Interaction, flight_index: int):
        """Process the selected flight based on flight type."""
        flight = self.flights[flight_index]
        
        logger.info(f"User {interaction.user.id} selected flight {flight_index + 1}: {flight['departure']} → {flight['arrival']}, flight_type: {self.flight_type}")
        
        await interaction.response.defer()
        
        # Check if this is Event mode - skip all processing, go directly to manual
        if self.flight_type == 'event':
            logger.info(f"Event mode selected - going to manual entry")
            await self.process_event_flight(interaction, flight)
            return
        
        # Check if aircraft_id/livery_id is invalid (0000000000000)
        if_aircraft_id = flight.get('aircraft_id', '')
        if_livery_id = flight.get('livery_id', '')
        is_invalid_aircraft = (if_aircraft_id == '0000000000000' or if_livery_id == '0000000000000')
        
        # For Normal mode with invalid aircraft, first check if route exists
        if self.flight_type == 'normal' and is_invalid_aircraft:
            # Check if route exists in CC - if yes, show aircraft dropdown
            dep = flight.get('departure', '')
            arr = flight.get('arrival', '')
            route_data = await self.bot.routes_model.find_route_by_icao(dep, arr)
            
            if route_data:
                # Route found - show aircraft selection dropdown
                logger.info(f"Invalid aircraft + route found - showing aircraft dropdown")
                await self.show_aircraft_selection(interaction, flight, route_data)
                return
        
        # Process flight with the new method that handles different types
        logger.info(f"Processing flight: calling process_flight_by_type for pilot_id={self.pilot_data['id']}, flight_type={self.flight_type}")
        result = await self.bot.pirep_filing_service.process_flight_by_type(
            self.pilot_data['id'],
            flight,
            self.flight_type
        )
        
        logger.info(f"process_flight_by_type result: success={result.get('success')}, aircraft={result.get('aircraft_data', {}).get('name')}, route_found={result.get('route_data') is not None}")
        
        if not result['success']:
            logger.error(f"Failed to process flight: {result.get('error')}")
            embed = discord.Embed(
                title="❌ Error Processing Flight",
                description=result['error'],
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
            return
        
        # Store processed data for later use
        self.bot.ascaris_selected_flight = {
            'pilot_data': self.pilot_data,
            'flight_data': flight,
            'aircraft_data': result['aircraft_data'],
            'route_data': result['route_data'],
            'rank_check': result['rank_check'],
            'pirep_data': result['pirep_data'],
            'flight_type': self.flight_type
        }
        
        logger.info(f"Stored flight data: aircraft_id={result['aircraft_data']['id']}, flight_num={result['pirep_data'].get('flight_num')}")
        
        # Create confirmation embed
        await self.send_confirmation_embed(interaction, flight, result)
    
    async def process_event_flight(self, interaction: discord.Interaction, flight):
        """Process event flight - go directly to manual entry with aircraft_id=11."""
        logger.info(f"Processing event flight for user {interaction.user.id}")
        
        # Get aircraft data for ID 11
        aircraft = await self.bot.aircraft_model.get_aircraft_by_id(11)
        if not aircraft:
            embed = discord.Embed(
                title="❌ Error",
                description="Default aircraft (ID: 11) not found in database.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
            return
        
        # Build pirep data for manual entry
        pirep_data = {
            'departure': flight.get('departure', ''),
            'arrival': flight.get('arrival', ''),
            'aircraft_id': 11,
            'flight_num': '',  # Empty - user must enter
            'duration': flight.get('duration', ''),
            'date': flight.get('date', datetime.now().strftime('%Y-%m-%d')),
            'route_found': False,
            'is_manual': True,
            'is_event': True
        }
        
        # Store processed data
        self.bot.ascaris_selected_flight = {
            'pilot_data': self.pilot_data,
            'flight_data': flight,
            'aircraft_data': aircraft,
            'route_data': None,
            'rank_check': {'can_fly': True, 'message': 'Event flight - no rank check'},
            'pirep_data': pirep_data,
            'flight_type': 'event'
        }
        
        # Send confirmation embed for event
        result = {
            'aircraft_data': aircraft,
            'route_data': None,
            'rank_check': {'can_fly': True, 'message': 'Event flight - no rank check'},
            'pirep_data': pirep_data
        }
        
        await self.send_confirmation_embed(interaction, flight, result)
    
    async def show_aircraft_selection(self, interaction: discord.Interaction, flight, route_data):
        """Show aircraft selection dropdown when IF aircraft data is invalid but route is found."""
        logger.info(f"Showing aircraft selection dropdown for route {flight.get('departure')} → {flight.get('arrival')}")
        
        # Get aircraft list from route
        route_aircraft_list = route_data.get('aircraft', [])
        
        if not route_aircraft_list:
            # No aircraft in route - go to manual entry
            logger.info(f"No aircraft in route - going to manual entry")
            await self.process_normal_manual_entry(interaction, flight, route_data)
            return
        
        # Get full aircraft details for each aircraft in route
        aircraft_options = []
        for ac in route_aircraft_list:
            icao = ac.get('icao', '')
            if icao:
                aircraft = await self.bot.aircraft_model.get_aircraft_by_icao(icao)
                if aircraft:
                    aircraft_options.append({
                        'id': aircraft.get('id'),
                        'name': aircraft.get('name', ''),
                        'icao': icao,
                        'livery': aircraft.get('liveryname', '')
                    })
        
        if not aircraft_options:
            # No valid aircraft found - go to manual entry
            logger.info(f"No valid aircraft options - going to manual entry")
            await self.process_normal_manual_entry(interaction, flight, route_data)
            return
        
        # Store data for selection callback
        self.bot.ascaris_pending_aircraft_selection = {
            'pilot_data': self.pilot_data,
            'flight_data': flight,
            'route_data': route_data,
            'flight_type': self.flight_type,
            'aircraft_options': aircraft_options
        }
        
        # Create embed
        embed = discord.Embed(
            title="✈️ Select Aircraft",
            description=f"Route **{flight.get('departure')} → {flight.get('arrival')}** found in database.\n\nYour Infinite Flight aircraft data was not recognized. Please select an aircraft from the dropdown below:",
            color=discord.Color.blue()
        )
        
        # Create dropdown with aircraft options
        select_options = []
        for ac in aircraft_options:
            label = f"{ac['name']} ({ac['icao']})"
            value = str(ac['id'])
            select_options.append(discord.SelectOption(label=label, value=value))
        
        # Add manual entry option
        select_options.append(discord.SelectOption(label="📝 Manual Entry", value="manual"))
        
        view = AircraftDropdownView(self.bot, self.pilot_data, flight, route_data, aircraft_options, self.flight_type)
        
        await interaction.followup.send(embed=embed, view=view)
    
    async def process_normal_manual_entry(self, interaction, flight, route_data):
        """Process normal flight with manual entry (aircraft_id=11)."""
        result = await self.bot.pirep_filing_service.process_flight_by_type(
            self.pilot_data['id'],
            flight,
            'normal'
        )
        
        if not result['success']:
            embed_err = discord.Embed(
                title="❌ Error",
                description=result.get('error'),
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed_err)
            return
        
        # Store and show confirmation
        self.bot.ascaris_selected_flight = {
            'pilot_data': self.pilot_data,
            'flight_data': flight,
            'aircraft_data': result['aircraft_data'],
            'route_data': result['route_data'],
            'rank_check': result['rank_check'],
            'pirep_data': result['pirep_data'],
            'flight_type': self.flight_type
        }
        
        await self.send_confirmation_embed(interaction, flight, result)
    
    async def send_confirmation_embed(self, interaction: discord.Interaction, flight, result):
        """Send the confirmation embed based on flight type and result."""
        aircraft = result['aircraft_data']
        route = result['route_data']
        rank_check = result['rank_check']
        pirep = result['pirep_data']
        flight_type = self.flight_type
        
        # Build status messages based on flight type
        if flight_type == 'oneworld':
            route_status = "✅ Route Found in OWD Database" if route else "❌ OWD Route Not Found"
            aircraft_info = f"**{aircraft.get('name', 'Unknown')}**\n🆔 CC Aircraft ID: {aircraft.get('id')} (One World)"
        elif flight_type == 'event':
            route_status = "📝 Manual Entry (Event)"
            aircraft_info = f"**{aircraft.get('name', 'Unknown')}**\n🆔 CC Aircraft ID: {aircraft.get('id')}"
        else:
            route_status = "✅ Route Found in Database" if route else "❌ Route Not Found (Manual Entry)"
            # Check for rank warning
            rank_warning = pirep.get('rank_warning')
            if rank_warning:
                aircraft_info = f"**{aircraft.get('name', 'Unknown')}**\n🎨 Livery: {aircraft.get('liveryname', 'Unknown')}\n🆔 CC Aircraft ID: {aircraft.get('id')}\n{rank_warning}"
            else:
                aircraft_info = f"**{aircraft.get('name', 'Unknown')}**\n🎨 Livery: {aircraft.get('liveryname', 'Unknown')}\n🆔 CC Aircraft ID: {aircraft.get('id')}"
        
        rank_status = "✅ Rank Requirement Met" if rank_check.get('can_fly') else f"⚠️ {rank_check.get('message', 'Rank Issue')}"
        
        embed = discord.Embed(
            title="✈️ Ascaris - Flight Confirmation",
            description="Please review your flight details below:",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="🛫 Route",
            value=f"**{pirep['departure']} → {pirep['arrival']}**",
            inline=False
        )
        
        embed.add_field(
            name="✈️ Aircraft",
            value=aircraft_info,
            inline=True
        )
        
        embed.add_field(
            name="⏱️ Flight Info",
            value=f"Duration: **{pirep.get('duration', 'N/A')}**\nDate: **{pirep.get('date', 'N/A')}**",
            inline=True
        )
        
        embed.add_field(
            name="✅ Validation Status",
            value=f"{route_status}\n{rank_status}",
            inline=False
        )
        
        # Show flight number based on route status
        if route and pirep.get('flight_num'):
            embed.add_field(
                name="🔢 Flight Number",
                value=f"**{pirep['flight_num']}** (from database)",
                inline=False
            )
        else:
            embed.add_field(
                name="🔢 Flight Number",
                value="⚠️ Not found - you'll need to enter manually",
                inline=False
            )
        
        embed.set_footer(text="Click ✅ Proceed to continue or ✏️ Edit to modify details")
        
        # Create view with Proceed/Edit buttons
        flight_info = {
            'pilot_data': self.pilot_data,
            'flight_data': flight,
            'aircraft_data': result['aircraft_data'],
            'route_data': result['route_data'],
            'rank_check': result['rank_check'],
            'pirep_data': result['pirep_data'],
            'flight_type': flight_type
        }
        view = ConfirmationView(self.bot, flight_info)
        
        logger.info("Sending confirmation embed")
        await interaction.followup.send(embed=embed, view=view)


class AircraftDropdownView(ui.View):
    """Dropdown view for selecting aircraft when IF aircraft data is invalid."""
    
    def __init__(self, bot, pilot_data, flight_data, route_data, aircraft_options, flight_type):
        super().__init__(timeout=300)
        self.bot = bot
        self.pilot_data = pilot_data
        self.flight_data = flight_data
        self.route_data = route_data
        self.aircraft_options = aircraft_options
        self.flight_type = flight_type
        logger.info(f"Creating AircraftDropdownView with {len(aircraft_options)} options")
        
        # Create dropdown
        select_options = []
        for ac in aircraft_options:
            label = f"{ac['name']} ({ac['icao']})"
            value = str(ac['id'])
            select_options.append(discord.SelectOption(label=label, value=value))
        
        # Add manual entry option
        select_options.append(discord.SelectOption(label="📝 Manual Entry", value="manual"))
        
        select = ui.Select(
            placeholder="Select an aircraft...",
            options=select_options,
            custom_id="ascaris_aircraft_select"
        )
        select.callback = self.aircraft_select_callback
        self.add_item(select)
    
    async def aircraft_select_callback(self, interaction: discord.Interaction):
        """Handle aircraft selection."""
        selected_value = interaction.data['values'][0]
        logger.info(f"User {interaction.user.id} selected aircraft: {selected_value}")
        
        await interaction.response.defer()
        
        # Check if manual entry selected
        if selected_value == "manual":
            logger.info("Manual entry selected - processing with default aircraft")
            # Process with normal flow (will use aircraft_id=11)
            result = await self.bot.pirep_filing_service.process_flight_by_type(
                self.pilot_data['id'],
                self.flight_data,
                'normal'
            )
            
            if not result['success']:
                embed_err = discord.Embed(
                    title="❌ Error",
                    description=result.get('error'),
                    color=discord.Color.red()
                )
                await interaction.followup.send(embed=embed_err)
                return
            
            # Store and show confirmation
            self.bot.ascaris_selected_flight = {
                'pilot_data': self.pilot_data,
                'flight_data': self.flight_data,
                'aircraft_data': result['aircraft_data'],
                'route_data': result['route_data'],
                'rank_check': result['rank_check'],
                'pirep_data': result['pirep_data'],
                'flight_type': self.flight_type
            }
            
            # Show confirmation
            flight_type = self.flight_type
            aircraft = result['aircraft_data']
            route = result['route_data']
            rank_check = result['rank_check']
            pirep = result['pirep_data']
            
            embed = discord.Embed(
                title="✈️ Ascaris - Flight Confirmation",
                description="Please review your flight details below:",
                color=discord.Color.blue()
            )
            
            embed.add_field(
                name="🛫 Route",
                value=f"**{pirep['departure']} → {pirep['arrival']}**",
                inline=False
            )
            
            embed.add_field(
                name="✈️ Aircraft",
                value=f"**{aircraft.get('name', 'Unknown')}**\n🆔 CC Aircraft ID: {aircraft.get('id')}",
                inline=True
            )
            
            embed.add_field(
                name="⏱️ Flight Info",
                value=f"Duration: **{pirep.get('duration', 'N/A')}**\nDate: **{pirep.get('date', 'N/A')}**",
                inline=True
            )
            
            route_status = "✅ Route Found in Database" if route else "❌ Route Not Found (Manual Entry)"
            rank_status = "✅ Rank Requirement Met" if rank_check.get('can_fly') else f"⚠️ {rank_check.get('message', 'Rank Issue')}"
            
            embed.add_field(
                name="✅ Validation Status",
                value=f"{route_status}\n{rank_status}",
                inline=False
            )
            
            if route and pirep.get('flight_num'):
                embed.add_field(
                    name="🔢 Flight Number",
                    value=f"**{pirep['flight_num']}** (from database)",
                    inline=False
                )
            else:
                embed.add_field(
                    name="🔢 Flight Number",
                    value="⚠️ Not found - you'll need to enter manually",
                    inline=False
                )
            
            embed.set_footer(text="Click ✅ Proceed to continue or ✏️ Edit to modify details")
            
            flight_info = {
                'pilot_data': self.pilot_data,
                'flight_data': self.flight_data,
                'aircraft_data': result['aircraft_data'],
                'route_data': result['route_data'],
                'rank_check': result['rank_check'],
                'pirep_data': result['pirep_data'],
                'flight_type': self.flight_type
            }
            view = ConfirmationView(self.bot, flight_info)
            
            await interaction.followup.send(embed=embed, view=view)
            return
        
        # User selected an aircraft - check rank
        selected_aircraft_id = int(selected_value)
        aircraft = await self.bot.aircraft_model.get_aircraft_by_id(selected_aircraft_id)
        
        if not aircraft:
            embed_err = discord.Embed(
                title="❌ Error",
                description="Selected aircraft not found in database.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed_err)
            return
        
        # Check rank
        rank_check = await self.bot.rank_model.can_pilot_fly_aircraft(
            self.pilot_data['id'], 
            selected_aircraft_id
        )
        
        rank_warning = None
        if not rank_check.get('can_fly'):
            # Not in rank - use aircraft_id=11
            logger.warning(f"Selected aircraft not in rank, using aircraft_id=11")
            aircraft = await self.bot.aircraft_model.get_aircraft_by_id(11)
            if aircraft:
                rank_warning = "⚠️ Selected aircraft not in rank, used default aircraft"
                rank_check = {'can_fly': True, 'message': rank_warning}
        
        # Build pirep data
        flight_num = self.route_data.get('fltnum', '').split(',')[0] if self.route_data else ''
        
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
        
        # Store and show confirmation
        self.bot.ascaris_selected_flight = {
            'pilot_data': self.pilot_data,
            'flight_data': self.flight_data,
            'aircraft_data': aircraft,
            'route_data': self.route_data,
            'rank_check': rank_check,
            'pirep_data': pirep_data,
            'flight_type': self.flight_type
        }
        
        # Show confirmation embed
        route_status = "✅ Route Found in Database"
        aircraft_info = f"**{aircraft.get('name', 'Unknown')}**\n🆔 CC Aircraft ID: {aircraft.get('id')}"
        if rank_warning:
            aircraft_info += f"\n{rank_warning}"
        
        rank_status = "✅ Rank Requirement Met" if rank_check.get('can_fly') else f"⚠️ {rank_check.get('message', 'Rank Issue')}"
        
        embed = discord.Embed(
            title="✈️ Ascaris - Flight Confirmation",
            description="Please review your flight details below:",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="🛫 Route",
            value=f"**{pirep_data['departure']} → {pirep_data['arrival']}**",
            inline=False
        )
        
        embed.add_field(
            name="✈️ Aircraft",
            value=aircraft_info,
            inline=True
        )
        
        embed.add_field(
            name="⏱️ Flight Info",
            value=f"Duration: **{pirep_data.get('duration', 'N/A')}**\nDate: **{pirep_data.get('date', 'N/A')}**",
            inline=True
        )
        
        embed.add_field(
            name="✅ Validation Status",
            value=f"{route_status}\n{rank_status}",
            inline=False
        )
        
        if flight_num:
            embed.add_field(
                name="🔢 Flight Number",
                value=f"**{flight_num}** (from database)",
                inline=False
            )
        
        embed.set_footer(text="Click ✅ Proceed to continue or ✏️ Edit to modify details")
        
        flight_info = {
            'pilot_data': self.pilot_data,
            'flight_data': self.flight_data,
            'aircraft_data': aircraft,
            'route_data': self.route_data,
            'rank_check': rank_check,
            'pirep_data': pirep_data,
            'flight_type': self.flight_type
        }
        view = ConfirmationView(self.bot, flight_info)
        
        # Disable the select
        self.clear_items()
        
        await interaction.followup.send(embed=embed, view=view)


class ConfirmationView(ui.View):
    """View with Proceed and Edit buttons."""
    
    def __init__(self, bot, flight_info=None):
        super().__init__(timeout=300)
        self.bot = bot
        self.flight_info = flight_info
        logger.info("ConfirmationView created")
    
    @ui.button(label="✅ Proceed", style=discord.ButtonStyle.success, custom_id="ascaris_proceed")
    async def proceed_button(self, interaction: discord.Interaction, button: ui.Button):
        """Handle Proceed button - show multiplier selection."""
        logger.info(f"User {interaction.user.id} clicked Proceed button")
        
        await interaction.response.defer()
        
        # Get selected flight data from instance or fallback to bot attribute
        flight_info = self.flight_info if self.flight_info else getattr(self.bot, 'ascaris_selected_flight', None)
        if not flight_info:
            logger.error("No flight data found in session")
            await interaction.followup.send("Session expired. Please start again.")
            return
        
        logger.info(f"Proceeding with: pilot_id={flight_info['pilot_data']['id']}, aircraft_id={flight_info['pirep_data']['aircraft_id']}")
        
        # Show multiplier selection
        embed = discord.Embed(
            title="🎯 Select Multiplier",
            description="Choose a multiplier for this PIREP:",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="Available Multipliers",
            value="• **1x** - Standard (100% hours)\n• **1.5x** - Bonus (150% hours)\n• **2x** - Double (200% hours)\n• **3x** - Triple (300% hours)",
            inline=False
        )
        
        view = MultiplierSelectionView(self.bot, flight_info)
        
        logger.info("Sending multiplier selection embed")
        await interaction.followup.send(embed=embed, view=view)
    
    @ui.button(label="✏️ Edit", style=discord.ButtonStyle.secondary, custom_id="ascaris_edit")
    async def edit_button(self, interaction: discord.Interaction, button: ui.Button):
        """Handle Edit button - open modal."""
        logger.info(f"User {interaction.user.id} clicked Edit button")
        
        # Get selected flight data from instance or fallback to bot attribute
        flight_info = self.flight_info if self.flight_info else getattr(self.bot, 'ascaris_selected_flight', None)
        if not flight_info:
            logger.error("No flight data found in session for edit")
            await interaction.followup.send("Session expired. Please start again.")
            return
        
        # Open edit modal
        logger.info("Opening edit modal")
        modal = AscarisEditModal(self.bot, flight_info)
        await interaction.response.send_modal(modal)


class AscarisEditModal(ui.Modal):
    """Modal for editing flight details."""
    
    def __init__(self, bot, flight_info):
        super().__init__(title="✏️ Edit Flight Details")
        self.bot = bot
        self.flight_info = flight_info
        
        logger.info(f"Creating edit modal with current data: {flight_info['pirep_data']}")
        
        pirep_data = flight_info['pirep_data']
        
        self.flight_number = ui.TextInput(
            label="Flight Number",
            default=pirep_data.get('flight_num', ''),
            placeholder="e.g., QR101",
            required=True
        )
        self.add_item(self.flight_number)
        
        self.departure = ui.TextInput(
            label="Departure Airport (ICAO)",
            default=pirep_data.get('departure', ''),
            placeholder="e.g., OTHH",
            required=True,
            max_length=4
        )
        self.add_item(self.departure)
        
        self.arrival = ui.TextInput(
            label="Arrival Airport (ICAO)",
            default=pirep_data.get('arrival', ''),
            placeholder="e.g., EGLL",
            required=True,
            max_length=4
        )
        self.add_item(self.arrival)
        
        self.duration = ui.TextInput(
            label="Flight Duration (HH:MM)",
            default=pirep_data.get('duration', ''),
            placeholder="e.g., 07:30",
            required=True,
            max_length=5
        )
        self.add_item(self.duration)
        
        self.date = ui.TextInput(
            label="Flight Date (YYYY-MM-DD)",
            default=pirep_data.get('date', datetime.now().strftime('%Y-%m-%d')),
            placeholder="e.g., 2026-03-15",
            required=True,
            max_length=10
        )
        self.add_item(self.date)
    
    async def callback(self, interaction: discord.Interaction):
        """Handle modal submission."""
        logger.info(f"User {interaction.user.id} submitted edit modal")
        
        # Update flight info with edited values
        self.flight_info['pirep_data']['flight_num'] = self.flight_number.value
        self.flight_info['pirep_data']['departure'] = self.departure.value.upper()
        self.flight_info['pirep_data']['arrival'] = self.arrival.value.upper()
        self.flight_info['pirep_data']['duration'] = self.duration.value
        self.flight_info['pirep_data']['date'] = self.date.value
        
        logger.info(f"Updated pirep_data: {self.flight_info['pirep_data']}")
        
        # Show confirmation with updated data
        embed = discord.Embed(
            title="✏️ Flight Details Updated",
            description="Your changes have been saved. Review below:",
            color=discord.Color.green()
        )
        
        embed.add_field(
            name="✈️ Updated Flight",
            value=f"**{self.flight_number.value}** | {self.departure.value.upper()} → {self.arrival.value.upper()}",
            inline=False
        )
        
        embed.add_field(
            name="⏱️ Duration",
            value=self.duration.value,
            inline=True
        )
        
        embed.add_field(
            name="📅 Date",
            value=self.date.value,
            inline=True
        )
        
        embed.set_footer(text="Click ✅ Proceed to continue with multiplier selection")
        
        view = ConfirmationView(self.bot, self.flight_info)
        
        logger.info("Sending updated confirmation embed")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False)


class MultiplierSelectionView(ui.View):
    """View for selecting multiplier."""
    
    def __init__(self, bot, flight_info):
        super().__init__(timeout=300)
        self.bot = bot
        self.flight_info = flight_info
        logger.info("MultiplierSelectionView created")
    
    @ui.select(
        placeholder="Select a multiplier...",
        options=[
            discord.SelectOption(label="1x (Standard)", value="100000", description="100% flight hours"),
            discord.SelectOption(label="1.5x (Bonus)", value="150000", description="150% flight hours"),
            discord.SelectOption(label="2x (Double)", value="200000", description="200% flight hours"),
            discord.SelectOption(label="3x (Triple)", value="300000", description="300% flight hours"),
        ],
        custom_id="ascaris_multiplier_select"
    )
    async def multiplier_select(self, interaction: discord.Interaction, select: ui.Select):
        """Handle multiplier selection and submit PIREP."""
        multiplier = select.values[0]
        
        logger.info(f"User {interaction.user.id} selected multiplier: {multiplier}")
        
        await interaction.response.defer()
        
        # Submit PIREP
        pirep_data = self.flight_info['pirep_data']
        pilot_data = self.flight_info['pilot_data']
        
        logger.info(f"Submitting PIREP: pilot_id={pilot_data['id']}, flight_num={pirep_data.get('flight_num')}, dep={pirep_data['departure']}, arr={pirep_data['arrival']}, duration={pirep_data['duration']}, aircraft_id={pirep_data['aircraft_id']}, multiplier={multiplier}")
        
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
        
        logger.info(f"PIREP submission result: success={result.get('success')}, error={result.get('error')}")
        
        if result['success']:
            embed = discord.Embed(
                title="✅ PIREP Filed Successfully!",
                description=f"🎉 Your PIREP has been submitted to Crew Center!",
                color=discord.Color.green()
            )
            
            embed.add_field(
                name="✈️ Flight Details",
                value=f"**{pirep_data.get('flight_num', 'N/A')}** | {pirep_data['departure']} → {pirep_data['arrival']}",
                inline=False
            )
            
            embed.add_field(
                name="⏱️ Duration",
                value=pirep_data['duration'],
                inline=True
            )
            
            embed.add_field(
                name="🎯 Multiplier",
                value=multiplier,
                inline=True
            )
            
            embed.add_field(
                name="👤 Pilot",
                value=f"**{pilot_data.get('callsign', 'Unknown')}**",
                inline=True
            )
            
            embed.set_footer(text="Thank you for flying with Qatar Virtual! 🐫")
        else:
            logger.error(f"PIREP submission failed: {result.get('error')}")
            embed = discord.Embed(
                title="❌ PIREP Submission Failed",
                description=f"**Error:** {result.get('error', 'Unknown error occurred')}\n\nPlease try again or contact staff.",
                color=discord.Color.red()
            )
            
            embed.add_field(
                name="Debug Info",
                value=f"Pilot ID: {pilot_data['id']}\nFlight: {pirep_data.get('flight_num')} {pirep_data['departure']}→{pirep_data['arrival']}\nAircraft ID: {pirep_data['aircraft_id']}",
                inline=False
            )
        
        # Disable the select
        select.disabled = True
        
        logger.info("Sending final result embed")
        await interaction.followup.send(embed=embed, ephemeral=False)


async def setup(bot):
    """Setup function to add cog to bot."""
    logger.info("Loading AscarisCog...")
    await bot.add_cog(AscarisCog(bot))
    logger.info("AscarisCog loaded successfully!")

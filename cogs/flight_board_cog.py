
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
import logging
import time
from cogs.flight_board_views import (
    FlightEditModal, 
    FlightBoardView, 
    AircraftSelectView, 
    StatusSelectView,
    FlightNumberSelectView,
    reconstruct_flight_data
)
from services.flight_board_service import FlightBoardService

class FlightBoardCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.fb_service = FlightBoardService(bot)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Handle persistent button interactions"""
        if interaction.type != discord.InteractionType.component:
            return
            
        custom_id = interaction.data.get('custom_id', '')
        if not custom_id.startswith('fb:'):
            return
            
        if interaction.response.is_done():
            return

        try:
            # Parse ID: fb:action:uid:expiry
            _, action, uid, expiry = custom_id.split(':')
            
            # Check Expiry
            if int(time.time()) > int(expiry):
                await interaction.response.send_message("❌ This interaction has expired.", ephemeral=True)
                return
                
            # Check User
            if str(interaction.user.id) != str(uid):
                await interaction.response.send_message("❌ You are not the pilot of this flight.", ephemeral=True)
                return
                
            # Reconstruct Data
            flight_data = reconstruct_flight_data(interaction.message)
            if not flight_data:
                await interaction.response.send_message("❌ Could not retrieve flight data.", ephemeral=True)
                return
            
            flight_data['pilot_id'] = int(uid)
            
            # Handle Actions
            if action == 'edit':
                modal = FlightEditModal(flight_data, message=interaction.message)
                await interaction.response.send_modal(modal)
                
            elif action == 'simbrief':
                if hasattr(self.bot, 'simbrief_service') and self.bot.simbrief_service:
                    pilot_data = await self.bot.pilots_model.get_pilot_by_discord_id(str(uid))
                    if not pilot_data:
                        await interaction.response.send_message("❌ Pilot not found in database.", ephemeral=True)
                        return

                    link = self.bot.simbrief_service.generate_dispatch_link(
                        origin=flight_data.get('departure'),
                        destination=flight_data.get('arrival'),
                        aircraft_type=flight_data.get('aircraft', 'B77W'),
                        callsign=pilot_data['callsign'],
                        flight_number=flight_data.get('flight_num')
                    )
                    await interaction.response.send_message(f"🔗 **SimBrief Flight Plan Generator**\n{link}", ephemeral=True)
                else:
                    await interaction.response.send_message("SimBrief service not available!", ephemeral=True)

            elif action == 'flightaware':
                if hasattr(self.bot, 'simbrief_service') and self.bot.simbrief_service:
                    link = self.bot.simbrief_service.generate_flightaware_link(
                        origin=flight_data.get('departure'),
                        destination=flight_data.get('arrival')
                    )
                    await interaction.response.send_message(f"🔗 **FlightAware Live Flight Tracker**\n{link}", ephemeral=True)
                else:
                    await interaction.response.send_message("FlightAware service not available!", ephemeral=True)

            elif action == 'checklist':
                view = FlightBoardView(flight_data)
                await view._handle_checklist(interaction)
                
            elif action == 'status':
                view = StatusSelectView(flight_data, interaction.message)
                await interaction.response.send_message("📊 **Update Flight Status:**", view=view, ephemeral=True)
        
        except discord.HTTPException as e:
            if e.code == 40060:
                return
            logging.error(f"HTTP Error in persistent flight board handler: {e}")
                
        except Exception as e:
            logging.error(f"Error in persistent flight board handler: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ An error occurred processing this request.", ephemeral=True)

    async def _check_pilot_rank_for_owd(self, discord_id: int) -> bool:
        """Check if pilot has OneWorld rank or above"""
        rank, _, error = await self.bot.flightdata.get_pilot_rank_and_aircraft(
            str(discord_id), 
            self.bot.pilots_model, 
            self.bot.pireps_model
        )
        
        if error:
            return False
            
        owd_ranks = ['OneWorld', 'Oryx']
        return rank in owd_ranks

    @app_commands.command(name="flight_board", description="Post your upcoming flight to the live flights board")
    @app_commands.describe(
        departure="4-letter departure airport ICAO (e.g., OTHH)",
        arrival="4-letter arrival airport ICAO (e.g., EGLL)",
        etd="Estimated Time of Departure in HHMM Zulu format (optional)",
        status="Flight status",
        note="Additional note (optional)"
    )
    @app_commands.choices(status=[
        app_commands.Choice(name="Scheduled", value="Scheduled"),
        app_commands.Choice(name="Boarding", value="Boarding"),
        app_commands.Choice(name="Departed", value="Departed"),
        app_commands.Choice(name="En Route", value="En Route"),
        app_commands.Choice(name="Arrived", value="Arrived"),
        app_commands.Choice(name="Delayed", value="Delayed"),
        app_commands.Choice(name="Cancelled", value="Cancelled")
    ])
    async def flight_board(self, interaction: discord.Interaction,
                           departure: str,
                           arrival: str,
                           etd: str = None, status: str = "Scheduled", note: str = None):
        await interaction.response.defer()
        
        try:
            # 1. Search CC routes first
            all_flight_numbers = await self.bot.routes_model.find_flight_numbers_by_icao(departure.upper(), arrival.upper())

            # 2. If no CC routes found, check for OWD routes
            if not all_flight_numbers:
                has_rank = await self._check_pilot_rank_for_owd(interaction.user.id)
                
                if has_rank and hasattr(self.bot, 'owd_route_model'):
                    # Pilot has rank, so search for OWD routes
                    all_flight_numbers = await self.bot.owd_route_model.find_flight_numbers_by_icao(departure.upper(), arrival.upper())
                elif hasattr(self.bot, 'owd_route_model'):
                    # Pilot does NOT have rank. Check if the route exists in OWD to give a specific error.
                    owd_route_check = await self.bot.owd_route_model.find_flight_numbers_by_icao(departure.upper(), arrival.upper())
                    if owd_route_check:
                        await interaction.followup.send(
                            "❌ This is an OneWorld Discover route. You need 'OneWorld' rank or higher to access it.", 
                            ephemeral=True
                        )
                        return  # Stop execution

            # 3. If still no routes found after all checks, send error
            if not all_flight_numbers:
                await interaction.followup.send(f"❌ No routes found for {departure.upper()} to {arrival.upper()}.", ephemeral=True)
                return

            # 4. Process based on the number of results found
            if len(all_flight_numbers) == 1:
                # Only one flight number found, proceed directly.
                await self._process_flight_request(interaction, all_flight_numbers[0], etd, status, note)
            else:
                # Multiple flight numbers found, show the selection dropdown.
                original_options = {'etd': etd, 'status': status, 'note': note}
                view = FlightNumberSelectView(all_flight_numbers, original_options, self)
                await interaction.followup.send("✈️ **Multiple flight numbers found. Please select one:**", view=view, ephemeral=True)
                
        except Exception as e:
            logging.error(f"Flight board creation failed: {e}")
            await interaction.followup.send("❌ Error creating flight board.", ephemeral=True)

    async def _process_flight_request(self, interaction: discord.Interaction, flight_num: str, etd: str, status: str, note: str):
        """
        Helper function to process a flight request once a flight number is determined.
        """
        try:
            route_data = await self.bot.routes_model.find_route_by_fltnum(flight_num)
            is_owd_route = False  # Flag to track if this is an OWD route
            
            if not route_data:
                # Check if pilot has OneWorld rank or above
                has_rank = await self._check_pilot_rank_for_owd(interaction.user.id)
                
                if has_rank and hasattr(self.bot, 'owd_route_model'):
                    # Search in OWD routes
                    owd_route = await self.bot.owd_route_model.find_route_by_flight_number(flight_num)
                    if owd_route:
                        # Convert OWD route format to standard route format
                        route_data = {
                            'dep': owd_route['departure'],
                            'arr': owd_route['arrival'],
                            'duration': self.fb_service.parse_flight_time_to_seconds(owd_route['flight_time']),
                            'fltnum': owd_route['flight_number'],
                            'livery': owd_route['airline'],
                            'aircraft': [{'icao': owd_route['aircraft'], 'name': owd_route['aircraft']}]
                        }
                        is_owd_route = True
                        # Add OWD note
                        if note:
                            note = f"OneWorld Discover route | {note}"
                        else:
                            note = "OneWorld Discover route"
            
            if not route_data:
                await interaction.followup.send(f"❌ Flight number {flight_num} not found in routes database.", ephemeral=True)
                return
            
            eta = None
            if etd and route_data.get('duration'):
                try:
                    etd_time = datetime.strptime(etd, "%H%M")
                    eta_time = etd_time + timedelta(seconds=route_data['duration'])
                    eta = eta_time.strftime("%H%M")
                except (ValueError, TypeError) as e:
                    logging.error(f"ETA calculation failed: {e}")
            
            if len(route_data['aircraft']) == 1:
                aircraft_name_from_db = route_data['aircraft'][0]['name']
                aircraft_icao = self.fb_service.convert_aircraft_name_to_icao(aircraft_name_from_db)
                
                if aircraft_icao == "XXXX":
                    await interaction.followup.send("❌ Could not determine a valid aircraft type.", ephemeral=True)
                    return
                
                aircraft_name = self.fb_service.aircraft_db.get('infinite_flight', {}).get(aircraft_icao, aircraft_name_from_db)
                
                flight_data = {
                    'flight_num': flight_num,
                    'departure': route_data['dep'],
                    'arrival': route_data['arr'],
                    'duration': route_data['duration'],
                    'aircraft': aircraft_icao,
                    'aircraft_name': aircraft_name,
                    'livery': route_data.get('livery', 'Qatar Airways'),
                    'etd': etd,
                    'eta': eta,
                    'status': status,
                    'note': note,
                    'pilot_name': interaction.user.display_name,
                    'pilot_id': interaction.user.id,
                    'flight_type': 'oneworld' if is_owd_route else None
                }
                
                msg = await self.fb_service.post_flight_board(flight_data)
                if msg:
                    await interaction.followup.send(f"✅ Live board is posted: {msg.jump_url}", ephemeral=True)
                else:
                    await interaction.followup.send("❌ Failed to post flight to board.", ephemeral=True)
            
            else:
                aircraft_options = []
                for ac_data_from_db in route_data['aircraft']:
                    name = ac_data_from_db['name'] 
                    livery = ac_data_from_db.get('livery', 'Standard')
                    icao = self.fb_service.convert_aircraft_name_to_icao(name)
                    
                    if icao == "XXXX": 
                        continue

                    display_name = self.fb_service.aircraft_db.get('infinite_flight', {}).get(icao, name)
                    value = f"{icao}|{livery}"
                    label = f"{icao} - {display_name} ({livery})"
                    aircraft_options.append((value, label))
                
                if not aircraft_options:
                    await interaction.followup.send("❌ No valid aircraft types found.", ephemeral=True)
                    return

                flight_data = {
                    'flight_num': flight_num,
                    'departure': route_data['dep'],
                    'arrival': route_data['arr'],
                    'duration': route_data['duration'],
                    'livery': route_data.get('livery', 'Qatar Airways'),
                    'etd': etd,
                    'eta': eta,
                    'status': status,
                    'note': note,
                    'pilot_name': interaction.user.display_name,
                    'pilot_id': interaction.user.id,
                    'flight_type': 'oneworld' if is_owd_route else None
                }
                
                view = AircraftSelectView(flight_data, aircraft_options, self.bot, self.fb_service.aircraft_db)
                await interaction.followup.send("✈️ **Select your aircraft:**", view=view, ephemeral=True)
            
        except Exception as e:
            logging.error(f"Flight board creation failed: {e}")
            await interaction.followup.send("❌ Error creating flight board.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(FlightBoardCog(bot))
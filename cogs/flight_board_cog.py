
'''
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
                    
            elif action == 'checklist':
                # Reuse the logic from FlightBoardView.checklist_gen via a temporary view instance or direct call
                view = FlightBoardView(flight_data)
                await view._handle_checklist(interaction)
                
            elif action == 'status':
                view = StatusSelectView(flight_data, interaction.message)
                await interaction.response.send_message("📊 **Update Flight Status:**", view=view, ephemeral=True)
        
        except discord.HTTPException as e:
            if e.code == 40060: # Interaction already acknowledged
                return
            logging.error(f"HTTP Error in persistent flight board handler: {e}")
                
        except Exception as e:
            logging.error(f"Error in persistent flight board handler: {e}")
            # Don't send message if already responded (e.g. by view logic above)
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ An error occurred processing this request.", ephemeral=True)



    async def _check_pilot_rank_for_owd(self, discord_id: int) -> bool:
        """Check if pilot has OneWorld rank or above"""
        # Use flightdata service to calculate rank based on hours
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
        flight_num="Flight number (e.g., QR123)",
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
    async def flight_board(self, interaction: discord.Interaction, flight_num: str,
                          etd: str = None, status: str = "Scheduled", note: str = None):
        await interaction.response.defer()
        
        try:
            route_data = await self.bot.routes_model.find_route_by_fltnum(flight_num)
            is_owd_route = False
            
            if not route_data:
                # Check if pilot has OneWorld rank or above
                has_rank = await self._check_pilot_rank_for_owd(interaction.user.id)
                
                if has_rank:
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
                
                # Always convert aircraft name to ICAO code using aircraft_data.json
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
                    'pilot_id': interaction.user.id
                }
                
               
            else:
                aircraft_options = []
                for ac_data_from_db in route_data['aircraft']:
                    name = ac_data_from_db['name'] 
                    livery = ac_data_from_db.get('livery', 'Standard')
                    
                    # Always convert aircraft name to ICAO code using aircraft_data.json
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
                    'pilot_id': interaction.user.id
                }
                
                view = AircraftSelectView(flight_data, aircraft_options, self.bot, self.fb_service.aircraft_db)
                await interaction.followup.send("✈️ **Select your aircraft:**", view=view, ephemeral=True)
            
        except Exception as e:
            logging.error(f"Flight board creation failed: {e}")
            await interaction.followup.send("❌ Error creating flight board.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(FlightBoardCog(bot))

'''
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
        flight_num="Flight number (e.g., QR123)",
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
    async def flight_board(self, interaction: discord.Interaction, flight_num: str,
                          etd: str = None, status: str = "Scheduled", note: str = None):
        await interaction.response.defer()
        
        try:
            route_data = await self.bot.routes_model.find_route_by_fltnum(flight_num)
            
            if not route_data:
                has_rank = await self._check_pilot_rank_for_owd(interaction.user.id)
                
                if has_rank:
                    owd_route = await self.bot.owd_route_model.find_route_by_flight_number(flight_num)
                    if owd_route:
                        route_data = {
                            'dep': owd_route['departure'],
                            'arr': owd_route['arrival'],
                            'duration': self.fb_service.parse_flight_time_to_seconds(owd_route['flight_time']),
                            'fltnum': owd_route['flight_number'],
                            'livery': owd_route['airline'],
                            'aircraft': [{'icao': owd_route['aircraft'], 'name': owd_route['aircraft']}]
                        }
                        note = f"OneWorld Discover route | {note}" if note else "OneWorld Discover route"
                
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
                    'pilot_id': interaction.user.id
                }
                
                msg = await self.fb_service.post_flight_board(flight_data)
                if msg:
                    await interaction.followup.send(f"✅ Flight posted to board: {msg.jump_url}")
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
                    'pilot_id': interaction.user.id
                }
                
                view = AircraftSelectView(flight_data, aircraft_options, self.bot, self.fb_service.aircraft_db)
                await interaction.followup.send("✈️ **Select your aircraft:**", view=view, ephemeral=True)
            
        except Exception as e:
            logging.error(f"Flight board creation failed: {e}")
            await interaction.followup.send("❌ Error creating flight board.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(FlightBoardCog(bot))

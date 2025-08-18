import discord
from discord.ext import commands, tasks
import os
import re
from datetime import datetime
import asyncio
import math

from .utils import get_country_flag

def format_duration(seconds: int) -> str:
    if not isinstance(seconds, (int, float)) or seconds <= 0: return "N/A"
    hours, remainder = divmod(int(seconds), 3600)
    minutes, _ = divmod(remainder, 60)
    if hours > 0: return f"{hours:02d}h{minutes:02d}"
    return f"{minutes:02d}m"

def calculate_distance_nm(lat1, lon1, lat2, lon2):
    R = 3440.065; lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1; dlat = lat2 - lat1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)); return R * c

class NoteModal(discord.ui.Modal):
    def __init__(self, current_note=""):
        super().__init__(title="Flight Note")
        self.note_input = discord.ui.TextInput(
            label="Add your flight note:",
            placeholder="Enter your note here...",
            default=current_note,
            style=discord.TextStyle.paragraph,
            required=False
        )
        self.add_item(self.note_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()

class FlightView(discord.ui.View):
    def __init__(self, pilot_discord_id, flight_id, has_note=False):
        super().__init__(timeout=None)
        self.pilot_discord_id = pilot_discord_id
        self.flight_id = flight_id
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Convert pilot_discord_id to int if it's a string
        pilot_id = int(self.pilot_discord_id) if isinstance(self.pilot_discord_id, str) else self.pilot_discord_id
        
        if pilot_id and interaction.user.id != pilot_id:
            await interaction.response.send_message(" This is not your Flight data ", ephemeral=True)
            return False
        return True
    
    @discord.ui.button(label="Add Note", style=discord.ButtonStyle.secondary)
    async def note_button(self, interaction: discord.Interaction, button: discord.ui.Button):

        # Get current note from embed
        embed = interaction.message.embeds[0]
        current_note = ""
        lines = embed.description.split("\n")
        if len(lines) > 4:
            # Note starts after the 4th line (after empty line)
            note_with_prefix = "\n".join(lines[4:])
            # Remove 'Note - ' prefix if present
            if note_with_prefix.startswith("Note - "):
                current_note = note_with_prefix[7:]  # Remove 'Note - '
            else:
                current_note = note_with_prefix
        
        modal = NoteModal(current_note)
        await interaction.response.send_modal(modal)
        await modal.wait()
        
        # Update embed with new note
        new_note = modal.note_input.value
        lines = embed.description.split("\n")
        
        # Keep only the first 3 lines (flight info)
        base_lines = lines[:3]
        
        # Add new note if provided
        if new_note:
            base_lines.extend(["", new_note])
            button.label = "Update Note"
        else:
            button.label = "Add Note"
        
        embed.description = "\n".join(base_lines)
        await interaction.edit_original_response(embed=embed, view=self)

# --- Main Cog ---
class LiveFlights(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.callsign_pattern = re.compile(r"^(Qatari\s.*VA|.*QR)(?:\s(?:Heavy|Super))?$", re.IGNORECASE)
        self.active_flights_cache = {}
        self.qatari_emoji = self.bot.get_emoji(1094679033205227580) or "✈️"
        self.track_flights.start()

    def cog_unload(self):
        self.track_flights.cancel()

    def _get_flight_status_note(self, flight_data: dict) -> str:
        altitude = flight_data.get('altitude', 0)
        vspeed = flight_data.get('verticalSpeed', 0)
        if altitude < 3000 and vspeed > 300: return "Takeoff"
        if altitude < 15000 and vspeed > 300: return "Climbing"
        if altitude > 15000: return f"Cruising at {altitude:.0f}ft"
        if altitude < 15000 and vspeed < -300: return "Descending"
        if altitude < 3000 and vspeed < -200: return "Approach"
        return "on Ground"

    def _create_flight_embed(self, flight_data: dict, dep_icao: str, arr_icao: str, duration_str: str, status: str, progress_percent: float, fltnum: str, pilot_discord_id: int = None, note: str = "") -> discord.Embed:
        color = discord.Color.green() if status != "Landed" else discord.Color.dark_grey()
        embed = discord.Embed(color=color)
        embed.title = f"**{fltnum}**"
        
        aircraft_name = self.bot.aircraft_name_map.get(flight_data.get('aircraftId'), "Unknown Aircraft")
        
        description_lines = [
            f"{dep_icao} {get_country_flag(dep_icao)} → {arr_icao} {get_country_flag(arr_icao)}",
            aircraft_name,
            f"{duration_str} • {progress_percent:.1f}% • {status}"
        ]
        
        if note:
            description_lines.extend(["", f"Note - {note}"])
        
        embed.description = "\n".join(description_lines)
        
        # Footer with pilot info - show username and callsign always
        embed.set_footer(text=f"{flight_data.get('username', 'N/A')} ({flight_data.get('callsign', 'N/A')})")
        
        return embed

    @tasks.loop(minutes=2)
    async def track_flights(self):
        # Check if bot is ready and database is connected
        if not self.bot.is_ready() or not self.bot.db_manager or not self.bot.db_manager.pool:
            return
            
        channel = self.bot.get_channel(int(os.getenv("FLIGHT_TRACKER_CHANNEL_ID")))
        if not channel: return

        try:
            # --- FIX: Added robust checks for API calls ---
            sessions_data = await self.bot.if_api_manager.get_sessions()
            if not sessions_data or not sessions_data.get('result'):
                print("Failed to fetch session data from API.")
                return
            expert_server_id = next((s['id'] for s in sessions_data['result'] if s['name'] == 'Expert'), None)
            if not expert_server_id: return

            flights_data = await self.bot.if_api_manager.get_flights(expert_server_id)
            if not flights_data or not flights_data.get('result'):
                print("Failed to fetch flight data from API.")
                return
            current_flights_map = {f['flightId']: f for f in flights_data['result']}

            # --- Update or remove existing flights ---
            for flight_id in list(self.active_flights_cache.keys()):
                cache_entry = self.active_flights_cache[flight_id]
                try:
                    message = await channel.fetch_message(cache_entry['message_id'])
                    if flight_id in current_flights_map:
                        flight_data = current_flights_map[flight_id]
                        route_data = await self.bot.if_api_manager.get_flight_route(flight_id)
                        dist_flown = 0.0
                        if route_data and route_data.get('result'):
                            points = route_data['result']
                            for i in range(len(points) - 1):
                                dist_flown += calculate_distance_nm(points[i]['latitude'], points[i]['longitude'], points[i+1]['latitude'], points[i+1]['longitude'])
                        progress = (dist_flown / cache_entry['total_dist_nm']) * 100 if cache_entry['total_dist_nm'] > 0 else 0
                        status_note = self._get_flight_status_note(flight_data)
                        
                        # Get existing note from message
                        existing_note = ""
                        if message.embeds and len(message.embeds[0].description.split("\n")) > 4:
                            # Note starts after the 4th line (after empty line)
                            lines = message.embeds[0].description.split("\n")
                            if len(lines) > 4:
                                note_with_prefix = "\n".join(lines[4:])
                                # Remove 'Note - ' prefix if present
                                if note_with_prefix.startswith("Note - "):
                                    existing_note = note_with_prefix[7:]  # Remove 'Note - '
                                else:
                                    existing_note = note_with_prefix
                        
                        embed = self._create_flight_embed(flight_data, cache_entry['dep_icao'], cache_entry['arr_icao'], cache_entry['duration_str'], status_note, progress, cache_entry['fltnum'], cache_entry.get('pilot_discord_id'), existing_note)
                        
                        # Update view with current note status
                        view = None
                        if cache_entry.get('pilot_discord_id'):
                            view = FlightView(cache_entry.get('pilot_discord_id'), flight_id, bool(existing_note))
                            if existing_note:
                                view.note_button.label = "Update Note"
                        
                        await message.edit(embed=embed, view=view)
                    else:
                        final_flight_data = {'callsign': cache_entry['callsign'], 'username': cache_entry['username'], 'aircraftId': cache_entry['aircraftId']}
                        embed = self._create_flight_embed(final_flight_data, cache_entry['dep_icao'], cache_entry['arr_icao'], cache_entry['duration_str'], "Landed", 100.0, cache_entry['fltnum'])
                        await message.edit(content=f"Flight {cache_entry['callsign']} has landed.", embed=embed, view=None)
                        del self.active_flights_cache[flight_id]
                except discord.NotFound: 
                    del self.active_flights_cache[flight_id]
                except Exception as e: 
                    print(f"Error updating flight {flight_id}: {e}")

            # --- Find and post new flights ---
            for flight_data in flights_data['result']:
                flight_id = flight_data['flightId']
                if flight_id not in self.active_flights_cache and self.callsign_pattern.match(flight_data['callsign']):
                    plan_data = await self.bot.if_api_manager.get_flight_plan(flight_id)
                    if not plan_data or not plan_data.get('result') or not plan_data['result'].get('flightPlanItems') or len(plan_data['result']['flightPlanItems']) < 2:
                        continue
                    
                    fpl_items = plan_data['result']['flightPlanItems']
                    dep_icao, arr_icao = fpl_items[0]['name'], fpl_items[-1]['name']
                    dep_coords, arr_coords = fpl_items[0]['location'], fpl_items[-1]['location']
                    total_dist_nm = calculate_distance_nm(dep_coords['latitude'], dep_coords['longitude'], arr_coords['latitude'], arr_coords['longitude'])
                    
                    # --- FIX: This block now safely handles when a route is not found ---
                    try:
                        route_info = await self.bot.routes_model.find_route_by_icao(dep_icao, arr_icao)
                        if route_info:
                            duration_str = format_duration(route_info.get('duration'))
                            fltnum = route_info.get('fltnum', flight_data['callsign'])
                        else:
                            duration_str = "N/A"
                            fltnum = flight_data['callsign']
                    except Exception as e:
                        print(f"Database error getting route info: {e}")
                        duration_str = "N/A"
                        fltnum = flight_data['callsign']

                    try:
                        pilot_db_entry = await self.bot.pilots_model.get_pilot_by_ifuserid(flight_data['userId'])
                        pilot_discord_id = pilot_db_entry['discordid'] if pilot_db_entry else None

                    except Exception as e:
                        print(f"Database error getting pilot info: {e}")
                        pilot_discord_id = None
                    
                    initial_status = self._get_flight_status_note(flight_data)
                    embed = self._create_flight_embed(flight_data, dep_icao, arr_icao, duration_str, initial_status, 0.0, fltnum, pilot_discord_id)
                    
                    ping_content = f"Hey <@{pilot_discord_id}>, your flight is now being tracked!" if pilot_discord_id else f"Tracking new flight: **{flight_data['callsign']}**"
                    
                    # Create view with button only if pilot has Discord ID
                    view = FlightView(pilot_discord_id, flight_id) if pilot_discord_id else None
                    message = await channel.send(content=ping_content, embed=embed, view=view)
                    
                    self.active_flights_cache[flight_id] = {
                        "message_id": message.id, "callsign": flight_data['callsign'], "username": flight_data['username'],
                        "aircraftId": flight_data.get('aircraftId'), "dep_icao": dep_icao, "arr_icao": arr_icao,
                        "duration_str": duration_str, "total_dist_nm": total_dist_nm, "fltnum": fltnum,
                        "pilot_discord_id": pilot_discord_id
                    }
                    await asyncio.sleep(1)

        except Exception as e:
            print(f"Error in track_flights: {e}")

    @track_flights.before_loop
    async def before_track_flights(self):
        await self.bot.wait_until_ready()



async def setup(bot):
    await bot.add_cog(LiveFlights(bot))
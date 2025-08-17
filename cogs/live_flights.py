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
        if altitude > 15000: return "Cruise"
        if altitude < 15000 and vspeed < -300: return "Descending"
        if altitude < 3000 and vspeed < -200: return "Approach"
        return "En Route"

    def _create_flight_embed(self, flight_data: dict, dep_icao: str, arr_icao: str, duration_str: str, status: str, progress_percent: float, fltnum: str) -> discord.Embed:
        color = discord.Color.green() if status != "Landed" else discord.Color.dark_grey()
        embed = discord.Embed(color=color)
        embed.title = f"**{fltnum}** {self.qatari_emoji}"
        aircraft_name = self.bot.aircraft_name_map.get(flight_data.get('aircraftId'), "Unknown Aircraft")
        description_lines = [
            f"{dep_icao} {get_country_flag(dep_icao)} **→** {arr_icao} {get_country_flag(arr_icao)}",
            f"{aircraft_name}",
            f"{duration_str} {progress_percent:.1f}%",
            f"{status}"
        ]
        embed.description = "\n".join(description_lines)
        embed.set_footer(text=f"{flight_data.get('username', 'N/A')} ({flight_data.get('callsign', 'N/A')})")
        return embed

    @tasks.loop(minutes=2)
    async def track_flights(self):
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
                        embed = self._create_flight_embed(flight_data, cache_entry['dep_icao'], cache_entry['arr_icao'], cache_entry['duration_str'], status_note, progress, cache_entry['fltnum'])
                        await message.edit(embed=embed)
                    else:
                        final_flight_data = {'callsign': cache_entry['callsign'], 'username': cache_entry['username'], 'aircraftId': cache_entry['aircraftId']}
                        embed = self._create_flight_embed(final_flight_data, cache_entry['dep_icao'], cache_entry['arr_icao'], cache_entry['duration_str'], "Landed", 100.0, cache_entry['fltnum'])
                        await message.edit(content=f"Flight {cache_entry['callsign']} has landed.", embed=embed)
                        del self.active_flights_cache[flight_id]
                except discord.NotFound: del self.active_flights_cache[flight_id]
                except Exception as e: print(f"Error updating flight {flight_id}: {e}")

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
                    route_info = await self.bot.routes_model.find_route_by_icao(dep_icao, arr_icao)
                    if route_info:
                        # Use .get() for extra safety in case a column is NULL
                        duration_str = format_duration(route_info.get('duration'))
                        fltnum = route_info.get('fltnum', flight_data['callsign']) # Default to callsign
                    else:
                        # Provide default values if route is not in the database
                        duration_str = "N/A"
                        fltnum = flight_data['callsign'] # Use the pilot's callsign as the flight number

                    pilot_db_entry = await self.bot.pilots_model.get_pilot_by_ifuserid(flight_data['userId'])
                    pilot_discord_id = pilot_db_entry['discordid'] if pilot_db_entry else None
                    
                    initial_status = self._get_flight_status_note(flight_data)
                    embed = self._create_flight_embed(flight_data, dep_icao, arr_icao, duration_str, initial_status, 0.0, fltnum)
                    
                    ping_content = f"Hey <@{pilot_discord_id}>, your flight is now being tracked!" if pilot_discord_id else f"Tracking new flight: **{flight_data['callsign']}**"
                    message = await channel.send(content=ping_content, embed=embed)
                    
                    self.active_flights_cache[flight_id] = {
                        "message_id": message.id, "callsign": flight_data['callsign'], "username": flight_data['username'],
                        "aircraftId": flight_data.get('aircraftId'), "dep_icao": dep_icao, "arr_icao": arr_icao,
                        "duration_str": duration_str, "total_dist_nm": total_dist_nm, "fltnum": fltnum
                    }
                    await asyncio.sleep(1)

        except TypeError as e:
            # This helps catch the specific error if it happens elsewhere unexpectedly
            print(f"A TypeError occurred in the flight tracker task, likely due to missing data: {e}")
        except Exception as e:
            print(f"An unexpected error occurred in the flight tracker task: {e}")

    @track_flights.before_loop
    async def before_track_flights(self):
        await self.bot.wait_until_ready()
        print("Live flight tracker is ready and waiting for the first loop.")

async def setup(bot: commands.Bot):
    await bot.add_cog(LiveFlights(bot))
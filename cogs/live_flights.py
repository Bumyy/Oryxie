import discord
from discord.ext import commands, tasks
import os
import re
from datetime import datetime
import asyncio
import math

# --- Helper Functions ---
def format_duration(seconds: int) -> str:
    if not isinstance(seconds, (int, float)) or seconds <= 0: return "N/A"
    hours, remainder = divmod(int(seconds), 3600)
    minutes, _ = divmod(remainder, 60)
    if hours > 0: return f"{hours}h {minutes}m"
    return f"{minutes}m"

def calculate_distance_nm(lat1, lon1, lat2, lon2):
    R = 3440.065
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def create_progress_bar(percentage: float, length: int = 10) -> str:
    if not (0 <= percentage <= 100): percentage = max(0, min(100, percentage))
    filled_length = int(length * percentage // 100)
    bar = '█' * filled_length + '░' * (length - filled_length)
    return f"[{bar}] {percentage:.1f}%"

class LiveFlights(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.callsign_pattern = re.compile(r"^(Qatari\s.*VA|.*QR)(?:\s(?:Heavy|Super))?$", re.IGNORECASE)
        self.active_flights_cache = {}
        self.track_flights.start()

    def cog_unload(self):
        self.track_flights.cancel()

    def _create_flight_embed(self, flight_data, route_str, duration_str, status, progress_percent=0.0, time_left_str="N/A", ground_speed_kts=0) -> discord.Embed:
        color = discord.Color.green()
        if "Landed" in status:
            color = discord.Color.dark_grey()
            progress_percent = 100.0
            time_left_str = "Landed"
        embed = discord.Embed(title=f"✈️ {flight_data['callsign']}", color=color)
        embed.description = (f"**Pilot:** {flight_data['username']}\n"
                             f"**Route:** {route_str} | **Est. Duration:** {duration_str}")
        embed.add_field(name="Altitude", value=f"{int(flight_data.get('altitude', 0)):,} ft")
        embed.add_field(name="Ground Speed", value=f"{int(ground_speed_kts)} kts")
        embed.add_field(name="Time Left", value=time_left_str)
        progress_bar = create_progress_bar(progress_percent)
        embed.add_field(name=f"Progress ({status})", value=progress_bar, inline=False)
        embed.set_footer(text=f"Flight ID: {flight_data['flightId']}")
        embed.timestamp = datetime.utcnow()
        return embed

    @tasks.loop(minutes=5)
    async def track_flights(self):
        print("Running live flight tracker task...")
        channel = self.bot.get_channel(int(os.getenv("FLIGHT_TRACKER_CHANNEL_ID")))
        if not channel: return

        try:
            sessions_data = await self.bot.if_api_manager.get_sessions()
            expert_server_id = next((s['id'] for s in sessions_data['result'] if s['name'] == 'Expert'), None)
            if not expert_server_id: return

            flights_data = await self.bot.if_api_manager.get_flights(expert_server_id)
            current_flights_map = {f['flightId']: f for f in flights_data['result']}

            for flight_id in list(self.active_flights_cache.keys()):
                cache_entry = self.active_flights_cache[flight_id]
                if flight_id in current_flights_map:
                    flight_data = current_flights_map[flight_id]
                    
                    route_data = await self.bot.if_api_manager.get_flight_route(flight_id)
                    dist_flown, ground_speed_kts, time_left_str = 0.0, 0.0, "N/A"
                    
                    if route_data and route_data.get('result'):
                        points = route_data['result']
                        for i in range(len(points) - 1):
                            p1, p2 = points[i], points[i+1]
                            dist_flown += calculate_distance_nm(p1['latitude'], p1['longitude'], p2['latitude'], p2['longitude'])
                        
                        ground_speed_kts = points[-1]['groundSpeed']

                        if ground_speed_kts > 50:
                            dist_remaining = max(0, cache_entry['total_dist_nm'] - dist_flown)
                            time_left_hours = dist_remaining / ground_speed_kts
                            time_left_str = format_duration(time_left_hours * 3600)
                    
                    progress_percent = (dist_flown / cache_entry['total_dist_nm']) * 100 if cache_entry['total_dist_nm'] > 0 else 0
                    
                    try:
                        message = await channel.fetch_message(cache_entry['message_id'])
                        embed = self._create_flight_embed(flight_data, cache_entry['route_str'], cache_entry['duration_str'], "🟢 En Route", progress_percent, time_left_str, ground_speed_kts)
                        await message.edit(embed=embed)
                    except discord.NotFound: del self.active_flights_cache[flight_id]
                else:
                    try:
                        message = await channel.fetch_message(cache_entry['message_id'])
                        final_flight_data = {'callsign': message.embeds[0].title.split(' ', 1)[1], 'username': 'N/A', 'flightId': flight_id}
                        embed = self._create_flight_embed(final_flight_data, cache_entry['route_str'], cache_entry['duration_str'], "🏁 Landed")
                        await message.edit(embed=embed)
                    except discord.NotFound: pass
                    finally: del self.active_flights_cache[flight_id]

            # --- Find and post new flights ---
            for flight_data in flights_data['result']:
                flight_id = flight_data['flightId']
                if flight_id not in self.active_flights_cache and self.callsign_pattern.match(flight_data['callsign']):
                    plan_data = await self.bot.if_api_manager.get_flight_plan(flight_id)
                    if not plan_data or not plan_data['result'].get('flightPlanItems'): continue

                    fpl_items = plan_data['result']['flightPlanItems']
                    if len(fpl_items) < 2: continue

                    dep_coords, arr_coords = fpl_items[0]['location'], fpl_items[-1]['location']
                    dep_icao, arr_icao = fpl_items[0]['name'], fpl_items[-1]['name']
                    route_info = await self.bot.routes_model.find_route_by_icao(dep_icao, arr_icao)
                    
                    route_str = f"{dep_icao} → {arr_icao}"
                    duration_str = "N/A"
                    if route_info:
                        route_str += f" ({route_info['fltnum']})"
                        duration_str = format_duration(route_info['duration'])

                    total_dist_nm = calculate_distance_nm(dep_coords['latitude'], dep_coords['longitude'], arr_coords['latitude'], arr_coords['longitude'])
                    
                    route_data = await self.bot.if_api_manager.get_flight_route(flight_id)
                    dist_flown, ground_speed_kts, time_left_str = 0.0, 0.0, "N/A"

                    if route_data and route_data.get('result'):
                        points = route_data['result']
                        for i in range(len(points) - 1):
                            p1, p2 = points[i], points[i+1]
                            dist_flown += calculate_distance_nm(p1['latitude'], p1['longitude'], p2['latitude'], p2['longitude'])
                        
                        ground_speed_kts = points[-1]['groundSpeed']
                        
                        if ground_speed_kts > 50:
                            dist_remaining = max(0, total_dist_nm - dist_flown)
                            time_left_hours = dist_remaining / ground_speed_kts
                            time_left_str = format_duration(time_left_hours * 3600)
                    
                    initial_progress_percent = (dist_flown / total_dist_nm) * 100 if total_dist_nm > 0 else 0

                    embed = self._create_flight_embed(flight_data, route_str, duration_str, "🟢 En Route", initial_progress_percent, time_left_str, ground_speed_kts)
                    message = await channel.send(embed=embed)
                    
                    self.active_flights_cache[flight_id] = {
                        "message_id": message.id, "route_str": route_str,
                        "duration_str": duration_str, "total_dist_nm": total_dist_nm
                    }

        except Exception as e:
            print(f"An unexpected error occurred in the flight tracker task: {e}")

    @track_flights.before_loop
    async def before_track_flights(self):
        await self.bot.wait_until_ready()
        print("Live flight tracker is ready and waiting for the first loop.")

async def setup(bot: commands.Bot):
    await bot.add_cog(LiveFlights(bot))
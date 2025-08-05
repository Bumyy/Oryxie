import discord
from discord.ext import commands, tasks
import os
import re
from datetime import datetime
import asyncio

class LiveFlights(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.callsign_pattern = re.compile(r"^(Qatari\s.*VA|.*QR)(?:\s(?:Heavy|Super))?$", re.IGNORECASE)
        
        # In-memory cache to track active flights and their corresponding message IDs.
        # Structure: { "flight_id": {"message_id": 123, "route_str": "OTHH -> EGLL"} }
        self.active_flights_cache = {}
        
        self.track_flights.start()

    def cog_unload(self):
        self.track_flights.cancel()

    # --- Helper function to create the embed ---
    def _create_flight_embed(self, flight_data: dict, route_str: str, status: str) -> discord.Embed:
        """Creates a standardized embed for a flight."""
        
        color = discord.Color.green() if "En Route" in status else discord.Color.dark_grey()
        
        embed = discord.Embed(
            title=f"✈️ {flight_data['callsign']}",
            color=color
        )
        embed.description = (
            f"**Pilot:** {flight_data['username']}\n"
            f"**Route:** {route_str}"
        )
        embed.add_field(name="Altitude", value=f"{int(flight_data.get('altitude', 0))} ft")
        embed.add_field(name="Speed", value=f"{int(flight_data.get('speed', 0))} kts")
        embed.add_field(name="Status", value=status, inline=False)
        
        embed.set_footer(text=f"Flight ID: {flight_data['flightId']}")
        embed.timestamp = datetime.utcnow()
        return embed

    # --- Main Task Loop ---
    @tasks.loop(minutes=1) # Reduced interval for more "live" updates
    async def track_flights(self):
        print("Running live flight tracker task...")
        
        channel_id_str = os.getenv("FLIGHT_TRACKER_CHANNEL_ID")
        if not channel_id_str:
            return
            
        channel = self.bot.get_channel(int(channel_id_str))
        if not channel:
            return

        try:
            # 1. Fetch all current flights on the Expert Server
            sessions_data = await self.bot.if_api_manager.get_sessions()
            expert_server_id = next((s['id'] for s in sessions_data['result'] if s['name'] == 'Expert'), None)
            if not expert_server_id: return

            flights_data = await self.bot.if_api_manager.get_flights(expert_server_id)
            if not flights_data or flights_data.get("errorCode") != 0: return

            # Create a map of current flights for easy lookup
            current_flights_map = {f['flightId']: f for f in flights_data['result']}

            # 2. Update existing tracked flights or mark them as ended
            tracked_flight_ids = list(self.active_flights_cache.keys())
            for flight_id in tracked_flight_ids:
                if flight_id in current_flights_map:
                    # Flight is still active, update its message
                    flight_data = current_flights_map[flight_id]
                    cache_entry = self.active_flights_cache[flight_id]
                    message_id = cache_entry['message_id']
                    route_str = cache_entry['route_str']

                    try:
                        message = await channel.fetch_message(message_id)
                        embed = self._create_flight_embed(flight_data, route_str, "🟢 En Route")
                        await message.edit(embed=embed)
                    except discord.NotFound:
                        # Message was deleted, so stop tracking this flight
                        del self.active_flights_cache[flight_id]
                        print(f"Message for flight {flight_id} not found. Removed from cache.")
                    except Exception as e:
                        print(f"Error updating message for flight {flight_id}: {e}")
                else:
                    # Flight has ended, finalize its message and remove from cache
                    cache_entry = self.active_flights_cache.pop(flight_id) # Remove and get value
                    message_id = cache_entry['message_id']
                    route_str = cache_entry['route_str']
                    
                    try:
                        message = await channel.fetch_message(message_id)
                        # Create a dummy flight_data dict for the final embed
                        final_flight_data = {'callsign': message.embeds[0].title.split(' ', 1)[1], 'username': 'N/A', 'flightId': flight_id}
                        embed = self._create_flight_embed(final_flight_data, route_str, "🏁 Flight Ended / Landed")
                        await message.edit(embed=embed)
                        print(f"Finalized message for ended flight {flight_id}")
                    except discord.NotFound:
                        print(f"Message for ended flight {flight_id} was already gone.")
                    except Exception as e:
                        print(f"Error finalizing message for flight {flight_id}: {e}")

            # 3. Find and post new flights
            for flight_data in flights_data['result']:
                flight_id = flight_data['flightId']
                # Check if it's a new flight that matches our callsign pattern
                if flight_id not in self.active_flights_cache and self.callsign_pattern.match(flight_data['callsign']):
                    # This is a new flight, let's process it
                    plan_data = await self.bot.if_api_manager.get_flight_plan(flight_id)
                    
                    # Skip if no valid flight plan (as requested)
                    if not plan_data or plan_data.get("errorCode") != 0: continue
                    waypoints = plan_data['result'].get('waypoints', [])
                    if len(waypoints) < 2: continue
                    
                    dep_icao = waypoints[0]
                    arr_icao = waypoints[-1]
                    
                    fltnum = await self.bot.routes_model.find_route_by_icao(dep_icao, arr_icao)
                    route_str = f"{dep_icao} → {arr_icao} ({fltnum})" if fltnum else f"{dep_icao} → {arr_icao} (Unscheduled)"
                    
                    # Post the new message
                    embed = self._create_flight_embed(flight_data, route_str, "🟢 En Route")
                    message = await channel.send(embed=embed)
                    
                    # Add to cache
                    self.active_flights_cache[flight_id] = {
                        "message_id": message.id,
                        "route_str": route_str
                    }
                    print(f"Posted new flight {flight_id} with message ID {message.id}")

        except Exception as e:
            print(f"An unexpected error occurred in the flight tracker task: {e}")

    @track_flights.before_loop
    async def before_track_flights(self):
        await self.bot.wait_until_ready()
        print("Live flight tracker is ready and waiting for the first loop.")


async def setup(bot: commands.Bot):
    if not os.getenv("FLIGHT_TRACKER_CHANNEL_ID"):
        print("WARNING: Flight Tracker cog not loaded because FLIGHT_TRACKER_CHANNEL_ID is not set.")
        return
    await bot.add_cog(LiveFlights(bot))
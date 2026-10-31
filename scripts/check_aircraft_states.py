"""Query Infinite Flight Live Fleet state via the new cached LiveFleetService layer."""

import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.manager import DatabaseManager
from database.if_live_tokens_model import IFLiveTokensModel
from api.if_live_manager import IFLiveManager
from api.manager import InfiniteFlightAPIManager


class DummyBot:
    def __init__(self, db):
        self.if_live_tokens_model = IFLiveTokensModel(db)
        self.if_api_manager = InfiniteFlightAPIManager(self)


async def main():
    target_filter = sys.argv[1].strip().upper() if len(sys.argv) > 1 else None

    org_id = os.getenv("IF_ORGANIZATION_ID", "").strip()
    if not org_id:
        print("Error: IF_ORGANIZATION_ID is not configured in .env")
        return

    db = DatabaseManager(bot=None)
    await db.connect()
    dummy_bot = DummyBot(db)
    await dummy_bot.if_api_manager.connect()
    manager = IFLiveManager(dummy_bot)

    try:
        print(f"Querying LiveFleetService for organization {org_id}...")
        snapshot = await manager.get_live_fleet(org_id)

        stats = manager.fleet_service.get_cache_stats()
        print(f"Fleet snapshot acquired: {len(snapshot)} aircraft. Refetches: {stats['refetch_count']}, Cache hits: {stats['cache_hits']}")

        aircraft_list = list(snapshot)
        if target_filter:
            aircraft_list = [
                ac for ac in aircraft_list
                if target_filter in ac.registration or target_filter in ac.aircraft_id.upper()
            ]
            print(f"Filtered down to {len(aircraft_list)} aircraft matching '{target_filter}'.")

        # Sort: STOPPED first, then LIVE, then GROUND
        def sort_key(ac):
            if ac.is_stopped:
                return (0, -(ac.stopped_duration_seconds or 0))
            if ac.is_actively_flying:
                return (1, -ac.speed)
            return (2, ac.registration)

        aircraft_list.sort(key=sort_key)

        header = f"{'REGISTRATION':<13} {'STATUS':<23} {'GROUND':<7} {'ALT (ft)':<9} {'SPD':<5} {'POSITION (LAT, LON)':<23} {'LAST PILOT':<18} {'STOPPED / LAST SEEN'}"
        sep = "-" * len(header)
        print(sep)
        print(header)
        print(sep)

        for ac in aircraft_list:
            lat, lon = ac.get_coordinates()
            coords_str = f"{lat:7.3f}, {lon:8.3f}" if (lat or lon) else "N/A"
            pilot = ac.last_pilot_username or "None"

            if ac.is_stopped:
                note = f"{ac.stopped_display_time} ({ac.stopped_since.isoformat()[:16] if ac.stopped_since else 'N/A'})"
            elif ac.is_actively_flying:
                note = f"Active in {ac.multiplayer_callsign or 'Multiplayer'}"
            else:
                note = "Parked"

            print(f"{ac.registration:<13} {ac.formatted_status:<23} {str(ac.is_on_ground):<7} {ac.altitude:<9.0f} {ac.speed:<5.0f} {coords_str:<23} {pilot:<18} {note}")

        print(sep)
        print(f"Summary: {len(snapshot.get_stopped_aircraft())} STOPPED in air (Needs pilot), {len(snapshot.get_active_flights())} actively in multiplayer, {len(snapshot.get_parked_aircraft())} on ground.")
        print(f"API Refetch Count: {stats['refetch_count']} | API Calls: {stats['api_call_counts']}")

        # Test cache hit on immediate second call
        print("\nTesting cache hit on second call...")
        cached_snapshot = await manager.get_live_fleet(org_id)
        stats2 = manager.fleet_service.get_cache_stats()
        print(f"Second call complete! Cache hits: {stats2['cache_hits']} (Age: {cached_snapshot.cache_age_seconds:.2f}s). Refetch count: {stats2['refetch_count']}")

    finally:
        await dummy_bot.if_api_manager.close()
        await manager.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())

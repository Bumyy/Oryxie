"""Integration tests for LiveFleetService and IFLiveManager live fleet state.

This integration test suite connects to the live Infinite Flight Public APIs (v3 and v2)
and the configured database to test the live fleet services:
1. Retrieval of live organization fleet snapshot.
2. Formatted fleet reporting and state classification (STOPPED, ACTIVELY_FLYING, PARKED).
3. Stopped airborne detection (matching v3 persistence with v2 multiplayer flights & route breadcrumbs).
4. Caching intervals compliance (TTL hits without redundant API refetches).
5. Quick CLI runner mode when executed directly (retains check_aircraft_states table output & filtering).
"""

import asyncio
import os
import sys
import unittest
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.manager import DatabaseManager
from database.if_live_tokens_model import IFLiveTokensModel
from api.if_live_manager import IFLiveManager
from api.manager import InfiniteFlightAPIManager
from api.live_fleet_state import LiveFleetSnapshot, LiveAircraftState


class DummyBot:
    def __init__(self, db: DatabaseManager):
        self.if_live_tokens_model = IFLiveTokensModel(db)
        self.if_api_manager = InfiniteFlightAPIManager(self)


class TestLiveFleetIntegration(unittest.IsolatedAsyncioTestCase):
    """Integration test suite testing LiveFleetService against real IF APIs."""

    async def asyncSetUp(self):
        self.org_id = os.getenv("IF_ORGANIZATION_ID", "").strip()
        if not self.org_id:
            self.skipTest("IF_ORGANIZATION_ID is not configured in .env")

        self.db = DatabaseManager(bot=None)
        await self.db.connect()
        self.bot = DummyBot(self.db)
        await self.bot.if_api_manager.connect()
        self.manager = IFLiveManager(self.bot)

    async def asyncTearDown(self):
        if hasattr(self, "manager") and self.manager:
            await self.manager.close()
        if hasattr(self, "bot") and hasattr(self.bot, "if_api_manager"):
            await self.bot.if_api_manager.close()
        if hasattr(self, "db") and self.db:
            await self.db.close()

    async def test_live_fleet_service_full_workflow(self):
        """End-to-end test of LiveFleetService: retrieval, stopped detection, lookups, and caching."""
        fleet_service = self.manager.fleet_service
        self.assertIsNotNone(fleet_service, "IFLiveManager should provide fleet_service instance.")

        # 1. Fetch initial live fleet snapshot from IF APIs
        snapshot = await self.manager.get_live_fleet(self.org_id)
        self.assertIsInstance(snapshot, LiveFleetSnapshot)
        self.assertGreater(len(snapshot), 0, "Organization fleet should contain aircraft.")

        stats1 = fleet_service.get_cache_stats()
        self.assertEqual(stats1["refetch_count"], 1, "First fetch should increment refetch_count to 1.")
        self.assertEqual(stats1["cache_hits"], 0, "First fetch should have 0 cache hits.")

        # 2. Verify aircraft properties and data integrity
        for ac in snapshot:
            self.assertIsInstance(ac, LiveAircraftState)
            self.assertTrue(ac.registration, "Aircraft must have a registration.")
            self.assertTrue(ac.aircraft_id, "Aircraft must have a UUID.")
            self.assertIn(ac.persistence_state, [0, 1, 2, 4, 5], f"Unexpected persistence state {ac.persistence_state}")
            lat, lon = ac.get_coordinates()
            self.assertIsInstance(lat, (int, float))
            self.assertIsInstance(lon, (int, float))

        # 3. Verify stopped airborne vs active flights categorization
        stopped_aircraft = snapshot.get_stopped_aircraft()
        active_aircraft = snapshot.get_active_flights()
        parked_aircraft = snapshot.get_parked_aircraft()

        for ac in stopped_aircraft:
            self.assertTrue(ac.is_stopped, f"{ac.registration} should have is_stopped=True")
            self.assertFalse(ac.is_on_ground, f"Stopped {ac.registration} cannot be on ground")
            self.assertFalse(ac.is_actively_flying, f"Stopped {ac.registration} cannot be actively flying")
            self.assertIn("STOPPED", ac.formatted_status)
            if ac.stopped_since:
                self.assertIsNotNone(ac.stopped_duration_seconds)
                self.assertGreaterEqual(ac.stopped_duration_seconds, 0)

        for ac in active_aircraft:
            self.assertTrue(ac.is_actively_flying, f"{ac.registration} should have is_actively_flying=True")
            self.assertTrue(ac.is_in_multiplayer)
            self.assertFalse(ac.is_stopped)

        for ac in parked_aircraft:
            self.assertTrue(ac.is_on_ground)
            self.assertFalse(ac.is_stopped)
            self.assertFalse(ac.is_actively_flying)

        # 4. Verify case-insensitive & hyphen-tolerant dictionary lookups
        sample_ac = next(iter(snapshot))
        clean_reg = sample_ac.registration.replace("-", "").lower()
        exact_reg = sample_ac.registration.upper()
        self.assertEqual(snapshot.get(clean_reg).aircraft_id, sample_ac.aircraft_id)
        self.assertEqual(snapshot.get(exact_reg).aircraft_id, sample_ac.aircraft_id)

        # 5. Verify caching behavior: second call must be a cache hit within TTL
        cached_snapshot = await self.manager.get_live_fleet(self.org_id)
        stats2 = fleet_service.get_cache_stats()

        self.assertEqual(stats2["refetch_count"], 1, "Refetch count must stay 1 on cache hit.")
        self.assertEqual(stats2["cache_hits"], 1, "Cache hit count must increment to 1.")
        self.assertLess(cached_snapshot.cache_age_seconds, 15.0, "Cached snapshot must be fresh.")
        self.assertEqual(len(cached_snapshot), len(snapshot))


def print_fleet_table(snapshot: LiveFleetSnapshot, stats: dict, filter_query: str = None):
    """Pretty prints the fleet status table."""
    aircraft_list = list(snapshot)
    if filter_query:
        target = filter_query.upper().strip()
        aircraft_list = [
            ac for ac in aircraft_list
            if target in ac.registration or target in ac.aircraft_id.upper()
        ]
        print(f"Filtered down to {len(aircraft_list)} aircraft matching '{filter_query}'.")

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


async def show_fleet_table(filter_query: str = None):
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
        snapshot = await manager.get_live_fleet(org_id)
        stats = manager.fleet_service.get_cache_stats()
        print_fleet_table(snapshot, stats, filter_query=filter_query)
    finally:
        await dummy_bot.if_api_manager.close()
        await manager.close()
        await db.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        asyncio.run(show_fleet_table(sys.argv[1].strip()))
    else:
        unittest.main()

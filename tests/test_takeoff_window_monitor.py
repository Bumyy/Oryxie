import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from api.live_fleet_state import LiveAircraftState, LiveFleetSnapshot
from cogs.airline_hubs import hubs_for_registration, takeoff_window_hours
from cogs.takeoff_window_monitor import TakeoffWindowMonitor, _utcnow


class TakeoffWindowMonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = MagicMock()
        self.bot.wait_until_ready = AsyncMock()
        self.bot.get_user = MagicMock()
        self.bot.fetch_user = AsyncMock()
        self.bot.db_manager = None
        self.bot.pilots_model = MagicMock()
        self.bot.pilots_model.get_pilot_by_ifuserid = AsyncMock(
            return_value={"discordid": "999888777", "callsign": "QRV001"}
        )

        self.mock_user = MagicMock()
        self.mock_user.send = AsyncMock()
        self.bot.get_user.return_value = self.mock_user

        self.patch_tasks = patch("discord.ext.tasks.Loop.start")
        self.mock_loop_start = self.patch_tasks.start()
        self.addCleanup(self.patch_tasks.stop)

    def test_takeoff_window_hours_calculation(self):
        # Qatar Airways at OTHH (hub) -> 6 hours
        self.assertEqual(takeoff_window_hours("A7-BFA", "OTHH"), 6)
        # Qatar Airways at EGLL (non-hub) -> 12 hours
        self.assertEqual(takeoff_window_hours("A7-BFA", "EGLL"), 12)
        # British Airways (IF-KSKI) at EGLL (hub) -> 6 hours
        self.assertEqual(takeoff_window_hours("IF-KSKI", "EGLL"), 6)
        # British Airways (IF-KSKI) at OTHH (non-hub) -> 12 hours
        self.assertEqual(takeoff_window_hours("IF-KSKI", "OTHH"), 12)

    async def test_discovers_parked_aircraft_and_records_state(self):
        cog = TakeoffWindowMonitor(self.bot)

        schedule = {
            "id": "sch-101",
            "sequence": 1,
            "status": 1,
            "callsign": "QR101",
            "origin_icao": "OTHH",
            "destination_icao": "OMDB",
            "crew": [{"userId": "user-if-1", "role": 0}],
        }
        ac = LiveAircraftState(
            aircraft_id="ac-uuid-1",
            content_aircraft_id="content-1",
            registration="A7-BAB",
            organization_id="org-1",
            status=0,
            is_on_ground=True,
            is_parked=True,
            is_actively_flying=False,
            is_stopped=False,
            schedules=[schedule],
        )
        snapshot = LiveFleetSnapshot(aircraft_by_id={"ac-uuid-1": ac})

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.remove_crew = AsyncMock()

        manager = MagicMock()
        manager.get_live_fleet = AsyncMock(return_value=snapshot)
        manager.get_client = AsyncMock(return_value=client)
        self.bot.if_live_manager = manager

        await cog._run_cycle()

        state = await cog._get_state("sch-101")
        self.assertIsNotNone(state)
        self.assertEqual(state["registration"], "A7-BAB")
        self.assertEqual(state["takeoff_limit_hours"], 6)
        self.assertEqual(state["warning_sent"], 0)
        self.assertEqual(state["unassigned"], 0)
        self.mock_user.send.assert_not_called()

    async def test_sends_warning_at_4_hours_for_hub(self):
        cog = TakeoffWindowMonitor(self.bot)

        # Pre-seed state with 4.5 hours elapsed (between 4 and 6 hours)
        now = _utcnow()
        assigned_since = now - timedelta(hours=4, minutes=30)
        await cog._save_state({
            "schedule_id": "sch-101",
            "aircraft_id": "ac-uuid-1",
            "registration": "A7-BAB",
            "callsign": "QR101",
            "origin_icao": "OTHH",
            "destination_icao": "OMDB",
            "parked_icao": "OTHH",
            "crew_json": json.dumps(["user-if-1"]),
            "assigned_since": assigned_since,
            "takeoff_limit_hours": 6,
            "warning_sent": 0,
            "warning_sent_at": None,
            "unassigned": 0,
            "unassigned_at": None,
        })

        schedule = {
            "id": "sch-101",
            "sequence": 1,
            "status": 1,
            "callsign": "QR101",
            "origin_icao": "OTHH",
            "destination_icao": "OMDB",
            "crew": [{"userId": "user-if-1", "role": 0}],
        }
        ac = LiveAircraftState(
            aircraft_id="ac-uuid-1",
            content_aircraft_id="content-1",
            registration="A7-BAB",
            organization_id="org-1",
            status=0,
            is_on_ground=True,
            is_parked=True,
            schedules=[schedule],
        )
        snapshot = LiveFleetSnapshot(aircraft_by_id={"ac-uuid-1": ac})

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)

        manager = MagicMock()
        manager.get_live_fleet = AsyncMock(return_value=snapshot)
        manager.get_client = AsyncMock(return_value=client)
        self.bot.if_live_manager = manager

        with patch("cogs.takeoff_window_monitor.post_dm_report", new_callable=AsyncMock):
            await cog._run_cycle()

        self.mock_user.send.assert_called_once()
        msg = self.mock_user.send.call_args[0][0]
        self.assertIn("Takeoff window alert", msg)
        self.assertIn("A7-BAB", msg)
        self.assertIn("OTHH", msg)
        self.assertIn("4 hours", msg)

        state = await cog._get_state("sch-101")
        self.assertEqual(state["warning_sent"], 1)

    async def test_unassigns_crew_after_6_hours_at_hub(self):
        cog = TakeoffWindowMonitor(self.bot)

        # Pre-seed state with 6.5 hours elapsed
        now = _utcnow()
        assigned_since = now - timedelta(hours=6, minutes=30)
        await cog._save_state({
            "schedule_id": "sch-101",
            "aircraft_id": "ac-uuid-1",
            "registration": "A7-BAB",
            "callsign": "QR101",
            "origin_icao": "OTHH",
            "destination_icao": "OMDB",
            "parked_icao": "OTHH",
            "crew_json": json.dumps(["user-if-1"]),
            "assigned_since": assigned_since,
            "takeoff_limit_hours": 6,
            "warning_sent": 1,
            "warning_sent_at": assigned_since + timedelta(hours=4),
            "unassigned": 0,
            "unassigned_at": None,
        })

        schedule = {
            "id": "sch-101",
            "sequence": 1,
            "status": 1,
            "callsign": "QR101",
            "origin_icao": "OTHH",
            "destination_icao": "OMDB",
            "crew": [{"userId": "user-if-1", "role": 0}],
        }
        ac = LiveAircraftState(
            aircraft_id="ac-uuid-1",
            content_aircraft_id="content-1",
            registration="A7-BAB",
            organization_id="org-1",
            status=0,
            is_on_ground=True,
            is_parked=True,
            schedules=[schedule],
        )
        snapshot = LiveFleetSnapshot(aircraft_by_id={"ac-uuid-1": ac})

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.remove_crew = AsyncMock()

        manager = MagicMock()
        manager.get_live_fleet = AsyncMock(return_value=snapshot)
        manager.get_client = AsyncMock(return_value=client)
        self.bot.if_live_manager = manager

        with patch("cogs.takeoff_window_monitor.post_dm_report", new_callable=AsyncMock):
            await cog._run_cycle()

        client.remove_crew.assert_awaited_once_with("sch-101", "user-if-1")
        self.mock_user.send.assert_called_once()
        msg = self.mock_user.send.call_args[0][0]
        self.assertIn("Takeoff window expired", msg)
        self.assertIn("A7-BAB", msg)
        self.assertIn("6 hours", msg)

        state = await cog._get_state("sch-101")
        self.assertEqual(state["unassigned"], 1)

    async def test_executive_crew_is_not_unassigned(self):
        cog = TakeoffWindowMonitor(self.bot)
        cog._executive_crew_ids = AsyncMock(return_value={"exec-user-1"})

        now = _utcnow()
        assigned_since = now - timedelta(hours=7)
        await cog._save_state({
            "schedule_id": "sch-102",
            "aircraft_id": "ac-uuid-2",
            "registration": "A7-BAB",
            "callsign": "QR102",
            "origin_icao": "OTHH",
            "destination_icao": "EGLL",
            "parked_icao": "OTHH",
            "crew_json": json.dumps(["exec-user-1"]),
            "assigned_since": assigned_since,
            "takeoff_limit_hours": 6,
            "warning_sent": 1,
            "warning_sent_at": assigned_since + timedelta(hours=4),
            "unassigned": 0,
            "unassigned_at": None,
        })

        schedule = {
            "id": "sch-102",
            "sequence": 1,
            "status": 1,
            "callsign": "QR102",
            "origin_icao": "OTHH",
            "destination_icao": "EGLL",
            "crew": [{"userId": "exec-user-1", "role": 0}],
        }
        ac = LiveAircraftState(
            aircraft_id="ac-uuid-2",
            content_aircraft_id="content-2",
            registration="A7-BAB",
            organization_id="org-1",
            status=0,
            is_on_ground=True,
            is_parked=True,
            schedules=[schedule],
        )
        snapshot = LiveFleetSnapshot(aircraft_by_id={"ac-uuid-2": ac})

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.remove_crew = AsyncMock()

        manager = MagicMock()
        manager.get_live_fleet = AsyncMock(return_value=snapshot)
        manager.get_client = AsyncMock(return_value=client)
        self.bot.if_live_manager = manager

        with patch("cogs.takeoff_window_monitor.post_dm_report", new_callable=AsyncMock):
            await cog._run_cycle()

        # Executive crew must NOT be removed
        client.remove_crew.assert_not_called()
        self.mock_user.send.assert_not_called()

    async def test_cleans_up_state_when_aircraft_takes_off(self):
        cog = TakeoffWindowMonitor(self.bot)

        # Pre-seed state for an aircraft that was parked
        await cog._save_state({
            "schedule_id": "sch-103",
            "aircraft_id": "ac-uuid-3",
            "registration": "A7-BAB",
            "callsign": "QR103",
            "origin_icao": "OTHH",
            "destination_icao": "KJFK",
            "parked_icao": "OTHH",
            "crew_json": json.dumps(["user-if-1"]),
            "assigned_since": _utcnow(),
            "takeoff_limit_hours": 6,
            "warning_sent": 0,
            "warning_sent_at": None,
            "unassigned": 0,
            "unassigned_at": None,
        })

        # Aircraft is now flying (is_parked = False, is_actively_flying = True)
        ac = LiveAircraftState(
            aircraft_id="ac-uuid-3",
            content_aircraft_id="content-3",
            registration="A7-BAB",
            organization_id="org-1",
            status=0,
            is_on_ground=False,
            is_parked=False,
            is_actively_flying=True,
            schedules=[],
        )
        snapshot = LiveFleetSnapshot(aircraft_by_id={"ac-uuid-3": ac})

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)

        manager = MagicMock()
        manager.get_live_fleet = AsyncMock(return_value=snapshot)
        manager.get_client = AsyncMock(return_value=client)
        self.bot.if_live_manager = manager

        await cog._run_cycle()

        # State should have been cleared
        state = await cog._get_state("sch-103")
        self.assertIsNone(state)


if __name__ == "__main__":
    unittest.main()

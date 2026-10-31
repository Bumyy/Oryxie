import os
import sys
from types import SimpleNamespace
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.if_live_manager import AircraftPersistenceState, IFAircraft, IFAircraftPosition
from api.live_fleet_state import LiveAircraftState, LiveFleetService, LiveFleetSnapshot


class LiveFleetStateTests(unittest.IsolatedAsyncioTestCase):
    def test_live_aircraft_state_stopped_display(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        state = LiveAircraftState(
            aircraft_id="ac-1",
            content_aircraft_id="type-1",
            registration="A7-APC",
            organization_id="org-1",
            status=0,
            is_stopped=True,
            stopped_since=now - timedelta(hours=7, minutes=12),
        )

        self.assertTrue(state.is_stopped)
        self.assertEqual(state.formatted_status, "STOPPED (Needs Pilot)")
        self.assertEqual(state.stopped_display_time, "7.2h ago")

    def test_snapshot_lookups_and_filters(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        ac1 = LiveAircraftState(
            aircraft_id="id-1",
            content_aircraft_id="type-1",
            registration="A7-APC",
            organization_id="org-1",
            status=0,
            is_stopped=True,
            stopped_since=now - timedelta(hours=2),
        )
        ac2 = LiveAircraftState(
            aircraft_id="id-2",
            content_aircraft_id="type-1",
            registration="A7-AOA",
            organization_id="org-1",
            status=0,
            is_actively_flying=True,
            is_on_ground=False,
        )
        ac3 = LiveAircraftState(
            aircraft_id="id-3",
            content_aircraft_id="type-1",
            registration="A7-BCA",
            organization_id="org-1",
            status=0,
            is_parked=True,
            is_on_ground=True,
        )

        snapshot = LiveFleetSnapshot(
            aircraft_by_id={"id-1": ac1, "id-2": ac2, "id-3": ac3},
            aircraft_by_reg={"A7-APC": ac1, "A7-AOA": ac2, "A7-BCA": ac3},
        )

        self.assertEqual(len(snapshot), 3)
        self.assertEqual(snapshot.get("id-1"), ac1)
        self.assertEqual(snapshot.get("A7-APC"), ac1)
        self.assertEqual(snapshot.get("a7apc"), ac1)  # case-insensitive and no hyphen
        self.assertEqual(snapshot.get_stopped_aircraft(), [ac1])
        self.assertEqual(snapshot.get_active_flights(), [ac2])
        self.assertEqual(snapshot.get_parked_aircraft(), [ac3])

    async def test_live_fleet_service_caching_and_refetch_count(self):
        v3_manager = MagicMock()
        client = AsyncMock()
        client_ctx = MagicMock()
        client_ctx.__aenter__ = AsyncMock(return_value=client)
        client_ctx.__aexit__ = AsyncMock(return_value=None)
        v3_manager.get_client = AsyncMock(return_value=client_ctx)

        # Mock fleet
        client.list_organization_aircraft.return_value = [
            IFAircraft(
                id="id-1",
                aircraft_id="content-1",
                organization_id="org-1",
                registration="A7-APC",
                status=0,
                visibility=1,
            )
        ]
        client.list_organization_schedules.return_value = SimpleNamespace(
            items=[],
            total_pages=1,
        )

        # Mock position
        client.get_aircraft_position.return_value = IFAircraftPosition(
            id="id-1",
            state=int(AircraftPersistenceState.IN_FLIGHT),
            is_on_ground=False,
            latitude=26.0,
            longitude=51.0,
            altitude=40000.0,
            heading=0.0,
            speed=400.0,
            vertical_speed=0.0,
            updated_at="2026-10-31T03:00:00Z",
        )

        # Mock v2 api
        v2_api = AsyncMock()
        v2_api.get_sessions.return_value = {"result": [{"id": "s1", "name": "Expert"}]}
        v2_api.get_flights.return_value = {"result": []}  # not in live session
        v2_api.get_flight_route.return_value = {
            "result": [
                {
                    "latitude": 49.4,
                    "longitude": 7.3,
                    "altitude": 40000.0,
                    "groundSpeed": 405.0,
                    "track": 300.0,
                    "date": "2026-10-31T03:30:00Z",
                }
            ]
        }

        service = LiveFleetService(v3_manager, v2_api=v2_api)
        self.assertEqual(service.refetch_count, 0)
        self.assertEqual(service.cache_hits, 0)

        # First call -> refetch
        snapshot1 = await service.get_live_fleet("org-1")
        self.assertEqual(service.refetch_count, 1)
        self.assertEqual(service.cache_hits, 0)
        self.assertEqual(len(snapshot1.get_stopped_aircraft()), 1)

        ac = snapshot1.get("A7-APC")
        self.assertIsNotNone(ac)
        self.assertTrue(ac.is_stopped)
        self.assertTrue(ac.stopped_at_tod)
        self.assertAlmostEqual(ac.latitude, 49.4)
        self.assertAlmostEqual(ac.longitude, 7.3)

        # Second call immediately -> cache hit, no new API calls
        snapshot2 = await service.get_live_fleet("org-1")
        self.assertEqual(service.refetch_count, 1)
        self.assertEqual(service.cache_hits, 1)
        self.assertEqual(snapshot2.cache_age_seconds >= 0.0, True)


if __name__ == "__main__":
    unittest.main()

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
            is_in_multiplayer=True,
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
            state=int(AircraftPersistenceState.STOPPED),
            is_on_ground=False,
            latitude=26.0,
            longitude=51.0,
            altitude=40000.0,
            heading=0.0,
            speed=0.0,
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

    async def test_exclusive_v2_flight_matching_does_not_ghost_to_historical_aircraft(self):
        v3_manager = MagicMock()
        client = AsyncMock()
        client_ctx = MagicMock()
        client_ctx.__aenter__ = AsyncMock(return_value=client)
        client_ctx.__aexit__ = AsyncMock(return_value=None)
        v3_manager.get_client = AsyncMock(return_value=client_ctx)

        # Two aircraft in organization: AC1 (A7-BAE) and AC2 (A7-BCB)
        # Pilot 'user-bumy' previously flew BOTH aircraft, so both have last_pilot_id = 'user-bumy'
        client.list_organization_aircraft.return_value = [
            IFAircraft(
                id="ac-bae",
                aircraft_id="content-1",
                organization_id="org-1",
                registration="A7-BAE",
                status=0,
                visibility=1,
            ),
            IFAircraft(
                id="ac-bcb",
                aircraft_id="content-2",
                organization_id="org-1",
                registration="A7-BCB",
                status=0,
                visibility=1,
            ),
        ]
        client.list_organization_schedules.return_value = SimpleNamespace(
            items=[
                {"id": "sch-1", "aircraft_id": "ac-bae", "callsign": "QR655", "origin_icao": "VCBI", "destination_icao": "OTHH", "status": 6},
                {"id": "sch-2", "aircraft_id": "ac-bcb", "callsign": "QR927", "origin_icao": "RPMD", "destination_icao": "OTHH", "status": 1},
            ],
            total_pages=1,
        )

        client.get_aircraft_position.side_effect = lambda aid: IFAircraftPosition(
            id=aid,
            latitude=25.0,
            longitude=51.0,
            altitude=0.0,
            heading=0.0,
            speed=0.0,
            vertical_speed=0.0,
            state=1,
            is_on_ground=True,
            last_pilot_id="user-bumy",
            last_pilot_username="Bumy",
        )

        # Pilot is currently flying ONLY ONE flight in multiplayer (QR655 on A7-BAE)
        v2_api = AsyncMock()
        v2_api.get_sessions.return_value = {"result": [{"id": "session-expert", "name": "Expert Server"}]}
        v2_api.get_flight_route.return_value = {"result": []}
        v2_api.get_flights.return_value = {
            "result": [
                {
                    "flightId": "flight-bae-live",
                    "userId": "user-bumy",
                    "callsign": "QR655",
                    "username": "Bumy",
                    "latitude": 10.0,
                    "longitude": 60.0,
                    "altitude": 38000.0,
                    "speed": 460.0,
                    "heading": 310.0,
                    "verticalSpeed": 0.0,
                }
            ]
        }

        service = LiveFleetService(v3_manager, v2_api=v2_api)
        snapshot = await service.get_live_fleet("org-1")

        ac_bae = snapshot.get("A7-BAE")
        ac_bcb = snapshot.get("A7-BCB")

        self.assertIsNotNone(ac_bae)
        self.assertIsNotNone(ac_bcb)

        # A7-BAE must match the live flight
        self.assertTrue(ac_bae.is_in_multiplayer)
        self.assertTrue(ac_bae.is_actively_flying)
        self.assertEqual(ac_bae.v2_flight_id, "flight-bae-live")

        # A7-BCB MUST NOT mirror the live flight or claim the same flightId
        self.assertFalse(ac_bcb.is_in_multiplayer)
        self.assertFalse(ac_bcb.is_actively_flying)
        self.assertIsNone(ac_bcb.v2_flight_id)

    async def test_pilot_with_multiple_aircraft_does_not_mismatch_or_steal_flight(self):
        v3_manager = MagicMock()
        client = AsyncMock()
        client_ctx = MagicMock()
        client_ctx.__aenter__ = AsyncMock(return_value=client)
        client_ctx.__aexit__ = AsyncMock(return_value=None)
        v3_manager.get_client = AsyncMock(return_value=client_ctx)

        # A7AED (Parked at OTHH, B777) and A7BCB (Airborne, B787)
        client.list_organization_aircraft.return_value = [
            IFAircraft(
                id="ac-aed",
                aircraft_id="type-777",
                organization_id="org-1",
                registration="A7-AED",
                status=0,
                visibility=1,
            ),
            IFAircraft(
                id="ac-bcb",
                aircraft_id="type-787",
                organization_id="org-1",
                registration="A7-BCB",
                status=0,
                visibility=1,
            ),
        ]

        # A7AED has 16 schedules, first is QR820 crewed by 'bumy', last is QR639 unassigned
        # A7BCB has QR843 HISTORIC crewed by 'bumy'
        aed_schedules = [
            {"id": "sch-aed-1", "aircraft_id": "ac-aed", "callsign": "QR820 HISTORIC", "origin_icao": "OTHH", "destination_icao": "RJBB", "sequence": 1, "status": 1, "crew": [{"userId": "user-bumy"}]},
            {"id": "sch-aed-16", "aircraft_id": "ac-aed", "callsign": "QR639 HISTORIC", "origin_icao": "WSSS", "destination_icao": "OTHH", "sequence": 16, "status": 1, "crew": []},
        ]
        bcb_schedules = [
            {"id": "sch-bcb-1", "aircraft_id": "ac-bcb", "callsign": "QR843 HISTORIC", "origin_icao": "VTSP", "destination_icao": "OTHH", "sequence": 1, "status": 1, "crew": [{"userId": "user-bumy"}]},
        ]
        client.list_organization_schedules.return_value = SimpleNamespace(
            items=aed_schedules + bcb_schedules,
            total_pages=1,
        )

        client.get_aircraft_position.side_effect = lambda aid: IFAircraftPosition(
            id=aid,
            latitude=25.26 if aid == "ac-aed" else 8.11,
            longitude=51.56 if aid == "ac-aed" else 98.30,
            altitude=0.0 if aid == "ac-aed" else 38000.0,
            heading=0.0,
            speed=0.0 if aid == "ac-aed" else 499.0,
            vertical_speed=0.0,
            state=1 if aid == "ac-aed" else 2,  # 1 = ON_GROUND, 2 = IN_FLIGHT
            is_on_ground=True if aid == "ac-aed" else False,
            last_pilot_id="user-bumy",
            last_pilot_username="bumy",
        )

        # Live multiplayer reports flight under "QR843" (without HISTORIC suffix)
        v2_api = AsyncMock()
        v2_api.get_sessions.return_value = {"result": [{"id": "session-expert", "name": "Expert Server"}]}
        v2_api.get_flight_route.return_value = {"result": []}
        v2_api.get_flights.return_value = {
            "result": [
                {
                    "flightId": "flight-bcb-live",
                    "userId": "user-bumy",
                    "callsign": "QR843",
                    "username": "bumy",
                    "aircraftId": "type-787",
                    "latitude": 8.11,
                    "longitude": 98.30,
                    "altitude": 38000.0,
                    "speed": 499.0,
                    "heading": 300.0,
                    "verticalSpeed": 0.0,
                }
            ]
        }

        service = LiveFleetService(v3_manager, v2_api=v2_api)
        snapshot = await service.get_live_fleet("org-1")

        ac_bcb = snapshot.get("A7-BCB")
        ac_aed = snapshot.get("A7-AED")

        self.assertIsNotNone(ac_bcb)
        self.assertIsNotNone(ac_aed)

        # 1. A7-BCB must match the flight QR843 despite the " HISTORIC" suffix in schedule
        self.assertTrue(ac_bcb.is_in_multiplayer)
        self.assertTrue(ac_bcb.is_actively_flying)
        self.assertFalse(ac_bcb.is_stopped)
        self.assertEqual(ac_bcb.v2_flight_id, "flight-bcb-live")
        self.assertIsNotNone(ac_bcb.active_schedule)
        self.assertEqual(ac_bcb.active_schedule["callsign"], "QR843 HISTORIC")

        # 2. A7-AED must remain parked on ground, NOT steal the flight, and NOT be stopped
        self.assertFalse(ac_aed.is_in_multiplayer)
        self.assertFalse(ac_aed.is_actively_flying)
        self.assertTrue(ac_aed.is_parked)
        self.assertTrue(ac_aed.is_on_ground)
        self.assertFalse(ac_aed.is_stopped)
        self.assertIsNone(ac_aed.v2_flight_id)

        # 3. Check that cogs.stopped_flight_monitor._status_schedule picks QR820 (first crewed), NOT QR639 (last leg)
        from cogs.stopped_flight_monitor import _status_schedule
        sch = _status_schedule("ENROUTE", aed_schedules)
        self.assertIsNotNone(sch)
        self.assertEqual(sch["callsign"], "QR820 HISTORIC")

    async def test_simultaneous_ap_plus_cruise_and_second_flight(self):
        """Tests that a pilot cruising on AP+ with aircraft 1 while flying aircraft 2 is fully supported."""
        v3_manager = MagicMock()
        client = AsyncMock()
        client_ctx = MagicMock()
        client_ctx.__aenter__ = AsyncMock(return_value=client)
        client_ctx.__aexit__ = AsyncMock(return_value=None)
        v3_manager.get_client = AsyncMock(return_value=client_ctx)

        # Aircraft 1: A7BCB (cruising on AP+ at FL380, 499 kts)
        # Aircraft 2: A7AED (taking off in multiplayer at 180 kts)
        client.list_organization_aircraft.return_value = [
            IFAircraft(id="ac-bcb", aircraft_id="type-787", organization_id="org-1", registration="A7-BCB", status=0, visibility=1),
            IFAircraft(id="ac-aed", aircraft_id="type-777", organization_id="org-1", registration="A7-AED", status=0, visibility=1),
        ]
        client.list_organization_schedules.return_value = SimpleNamespace(
            items=[
                {"id": "sch-bcb", "aircraft_id": "ac-bcb", "callsign": "QR843 HISTORIC", "origin_icao": "VTSP", "destination_icao": "OTHH", "sequence": 1, "status": 6, "crew": [{"userId": "user-bumy"}]},
                {"id": "sch-aed", "aircraft_id": "ac-aed", "callsign": "QR820 HISTORIC", "origin_icao": "OTHH", "destination_icao": "RJBB", "sequence": 1, "status": 1, "crew": [{"userId": "user-bumy"}]},
            ],
            total_pages=1,
        )

        # In v3 positions:
        # A7BCB is airborne cruising at 499 kts (state=2 IN_FLIGHT, is_on_ground=False)
        # A7AED is departing OTHH at 180 kts (state=2 IN_FLIGHT, is_on_ground=False)
        client.get_aircraft_position.side_effect = lambda aid: IFAircraftPosition(
            id=aid,
            latitude=15.0 if aid == "ac-bcb" else 25.26,
            longitude=80.0 if aid == "ac-bcb" else 51.56,
            altitude=38000.0 if aid == "ac-bcb" else 3000.0,
            heading=300.0 if aid == "ac-bcb" else 90.0,
            speed=499.0 if aid == "ac-bcb" else 180.0,
            vertical_speed=0.0 if aid == "ac-bcb" else 1500.0,
            state=2,  # IN_FLIGHT
            is_on_ground=False,
            last_pilot_id="user-bumy",
            last_pilot_username="bumy",
        )

        # In multiplayer: only A7AED is actively connected to the foreground session,
        # while A7BCB is running on AP+ in background simulation
        v2_api = AsyncMock()
        v2_api.get_sessions.return_value = {"result": [{"id": "session-expert", "name": "Expert Server"}]}
        v2_api.get_flight_route.return_value = {"result": []}
        v2_api.get_flights.return_value = {
            "result": [
                {
                    "flightId": "flight-aed-multiplayer",
                    "userId": "user-bumy",
                    "callsign": "QR820",
                    "username": "bumy",
                    "aircraftId": "type-777",
                    "latitude": 25.26,
                    "longitude": 51.56,
                    "altitude": 3000.0,
                    "speed": 180.0,
                    "heading": 90.0,
                    "verticalSpeed": 1500.0,
                }
            ]
        }

        service = LiveFleetService(v3_manager, v2_api=v2_api)
        snapshot = await service.get_live_fleet("org-1")

        ac_bcb = snapshot.get("A7-BCB")
        ac_aed = snapshot.get("A7-AED")

        # Aircraft 2 (A7AED) is in multiplayer and actively flying
        self.assertTrue(ac_aed.is_in_multiplayer)
        self.assertTrue(ac_aed.is_actively_flying)
        self.assertFalse(ac_aed.is_stopped)
        self.assertEqual(ac_aed.v2_flight_id, "flight-aed-multiplayer")

        # Aircraft 1 (A7BCB) is cruising on AP+ via v3 telemetry:
        # Crucially: it MUST NOT be falsely marked is_stopped ("Paused at TOD") while speed is 499 kts!
        self.assertFalse(ac_bcb.is_in_multiplayer)
        self.assertTrue(ac_bcb.is_actively_flying)
        self.assertFalse(ac_bcb.is_stopped)
        self.assertFalse(ac_bcb.is_on_ground)
        self.assertEqual(ac_bcb.speed, 499.0)
        self.assertEqual(ac_bcb.altitude, 38000.0)

    async def test_stranger_in_multiplayer_cannot_hijack_assigned_or_parked_aircraft(self):
        """Tests that a public multiplayer flight matching a schedule callsign or registration
        does NOT hijack an aircraft when the pilot in multiplayer is not the assigned crew or org member."""
        from cogs.stopped_flight_monitor import _aircraft_status_text

        v3_manager = MagicMock()
        client = AsyncMock()
        client_ctx = MagicMock()
        client_ctx.__aenter__ = AsyncMock(return_value=client)
        client_ctx.__aexit__ = AsyncMock(return_value=None)
        v3_manager.get_client = AsyncMock(return_value=client_ctx)

        # A7-BAJ parked at OTHH, scheduled for DeltaLimaCharlie (user-dlc)
        client.list_organization_aircraft.return_value = [
            IFAircraft(
                id="ac-baj",
                aircraft_id="type-777",
                organization_id="org-1",
                registration="A7-BAJ",
                status=0,
                visibility=1,
            )
        ]
        schedules = [
            {
                "id": "sch-baj",
                "aircraft_id": "ac-baj",
                "callsign": "QR28",
                "origin_icao": "EGCC",
                "destination_icao": "OTHH",
                "sequence": 1,
                "status": 1,
                "crew": [{"userId": "user-dlc", "username": "DeltaLimaCharlie"}],
            }
        ]
        client.list_organization_schedules.return_value = SimpleNamespace(
            items=schedules,
            total_pages=1,
        )

        client.get_aircraft_position.return_value = IFAircraftPosition(
            id="ac-baj",
            latitude=25.26,
            longitude=51.56,
            altitude=0.0,
            heading=0.0,
            speed=0.0,
            vertical_speed=0.0,
            state=1,  # ON_GROUND
            is_on_ground=True,
            last_pilot_id="user-dlc",
            last_pilot_username="DeltaLimaCharlie",
        )

        # Organization members list only includes DeltaLimaCharlie
        client.list_organization_members.return_value = [
            SimpleNamespace(user_id="user-dlc", username="DeltaLimaCharlie")
        ]

        # Case 1: Random stranger in multiplayer flying QR28 airborne at FL350, speed 460 kts
        v2_api = AsyncMock()
        v2_api.get_sessions.return_value = {"result": [{"id": "session-expert", "name": "Expert Server"}]}
        v2_api.get_flight_route.return_value = {"result": []}
        v2_api.get_flights.return_value = {
            "result": [
                {
                    "flightId": "flight-stranger-qr28",
                    "userId": "random-stranger-guid-1234",
                    "callsign": "QR28",
                    "username": "RandomStranger",
                    "latitude": 53.36,
                    "longitude": -2.27,
                    "altitude": 35000.0,
                    "speed": 460.0,
                    "heading": 120.0,
                    "verticalSpeed": 0.0,
                }
            ]
        }

        service = LiveFleetService(v3_manager, v2_api=v2_api)
        snapshot = await service.get_live_fleet("org-1")

        ac_baj = snapshot.get("A7-BAJ")
        self.assertIsNotNone(ac_baj)

        # Aircraft must NOT be hijacked by the stranger
        self.assertFalse(ac_baj.is_in_multiplayer)
        self.assertFalse(ac_baj.is_actively_flying)
        self.assertTrue(ac_baj.is_parked)
        self.assertTrue(ac_baj.is_on_ground)
        self.assertIsNone(ac_baj.v2_flight_id)

        # Status logger must remain PARKED AT OTHH, never transition to ENROUTE
        status_text = _aircraft_status_text(
            bot=MagicMock(),
            position=ac_baj.to_position_dict(),
            schedules=schedules,
            previous_status="PARKED AT OTHH",
        )
        self.assertEqual(status_text, "PARKED AT OTHH")

        # Case 2: Random stranger in multiplayer flying with callsign 'A7BAJ' / 'A7-BAJ'
        service.invalidate_cache()
        v2_api.get_flights.return_value = {
            "result": [
                {
                    "flightId": "flight-stranger-reg",
                    "userId": "random-stranger-guid-5678",
                    "callsign": "A7-BAJ",
                    "username": "AnotherStranger",
                    "latitude": 40.0,
                    "longitude": 20.0,
                    "altitude": 38000.0,
                    "speed": 480.0,
                    "heading": 100.0,
                    "verticalSpeed": 0.0,
                }
            ]
        }

        snapshot2 = await service.get_live_fleet("org-1")
        ac_baj2 = snapshot2.get("A7-BAJ")
        self.assertFalse(ac_baj2.is_in_multiplayer)
        self.assertFalse(ac_baj2.is_actively_flying)
        self.assertTrue(ac_baj2.is_parked)
        self.assertIsNone(ac_baj2.v2_flight_id)

        status_text2 = _aircraft_status_text(
            bot=MagicMock(),
            position=ac_baj2.to_position_dict(),
            schedules=schedules,
            previous_status="PARKED AT OTHH",
        )
        self.assertEqual(status_text2, "PARKED AT OTHH")

    async def test_ap_plus_stopped_at_tod_resolves_route_point_and_updates_coordinates(self):
        """Aircraft stopped at TOD with stale v3 position coordinates (e.g. AP+ flight)

        verifies that:
        1. ac.status == 4 (STOPPED / 'Needs a pilot') is recognized even if pos.state is IN_FLIGHT.
        2. Session flight_id is resolved via list_aircraft_flights or get_user_flights.
        3. v2 route breadcrumb is queried using the resolved flight_id.
        4. Real final coordinates near TOD/destination overwrite the stale cruise coordinates.
        5. Position state becomes STOPPED (4) and speed is 0 kts.
        """
        client = AsyncMock()
        client_ctx = AsyncMock()
        client_ctx.__aenter__.return_value = client
        client_ctx.__aexit__.return_value = None

        v3_manager = MagicMock()
        v3_manager.get_client = AsyncMock(return_value=client_ctx)

        # Organization fleet shows A7BCB with status=4 (STOPPED / Needs a pilot)
        client.list_organization_aircraft.return_value = [
            IFAircraft(
                id="ac-bcb-id",
                aircraft_id="content-788",
                organization_id="org-1",
                registration="A7-BCB",
                status=int(AircraftPersistenceState.STOPPED),  # 4
                visibility=1,
            )
        ]
        client.list_organization_schedules.return_value = SimpleNamespace(
            items=[],
            total_pages=1,
        )
        client.list_organization_members = AsyncMock(return_value=[])

        # v3 position has stale coordinates south of India with 478 kts from when pilot left app
        client.get_aircraft_position.return_value = IFAircraftPosition(
            id="ac-bcb-id",
            state=int(AircraftPersistenceState.IN_FLIGHT),  # Still 2 in stale v3 position!
            is_on_ground=False,
            latitude=8.0,
            longitude=75.0,
            altitude=40000.0,
            heading=305.0,
            speed=478.0,
            vertical_speed=0.0,
            updated_at="2026-11-02T10:00:00Z",
        )

        # Aircraft flight history provides the session flight ID
        client.list_aircraft_flights = AsyncMock(return_value=SimpleNamespace(
            items=[{"id": "session-flight-uuid-bcb-999"}]
        ))

        # Public v2 API mock
        v2_api = AsyncMock()
        v2_api.get_sessions.return_value = {"result": [{"id": "s1", "name": "Expert"}]}
        v2_api.get_flights.return_value = {"result": []}  # not in active multiplayer
        # Breadcrumb route endpoint returns final TOD holding point near Doha (OTHH)
        v2_api.get_flight_route.return_value = {
            "result": [
                {
                    "latitude": 8.0,
                    "longitude": 75.0,
                    "altitude": 40000.0,
                    "groundSpeed": 478.0,
                    "track": 305.0,
                    "date": "2026-11-02T10:00:00Z",
                },
                {
                    "latitude": 25.27,
                    "longitude": 51.56,
                    "altitude": 40000.0,
                    "groundSpeed": 0.0,
                    "track": 310.0,
                    "date": "2026-11-02T13:40:00Z",
                },
            ]
        }

        service = LiveFleetService(v3_manager, v2_api=v2_api)
        snapshot = await service.get_live_fleet("org-1")

        ac = snapshot.get("A7BCB")
        self.assertIsNotNone(ac)
        # 1. Stopped flags
        self.assertTrue(ac.is_stopped)
        self.assertFalse(ac.is_actively_flying)
        self.assertFalse(ac.is_parked)
        self.assertTrue(ac.stopped_at_tod)

        # 2. Coordinates updated from breadcrumb at SODEX/OTHH, NOT stale Indian Ocean coords
        self.assertAlmostEqual(ac.latitude, 25.27)
        self.assertAlmostEqual(ac.longitude, 51.56)
        self.assertEqual(ac.speed, 0.0)

        # 3. Flight ID resolved and passed to get_flight_route
        v2_api.get_flight_route.assert_awaited_with("session-flight-uuid-bcb-999")

        # 4. Position dict reflects STOPPED status
        pos_dict = ac.to_position_dict()
        self.assertEqual(pos_dict["state"], int(AircraftPersistenceState.STOPPED))
        self.assertEqual(pos_dict["v2_flight_id"], "session-flight-uuid-bcb-999")
        self.assertAlmostEqual(pos_dict["latitude"], 25.27)
        self.assertAlmostEqual(pos_dict["longitude"], 51.56)


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.stopped_flight_monitor import (
    StoppedFlightMonitor,
    _flight_progress,
    _parse_step_climbs_from_flight_plan,
    _flatten_fpl_items,
)


class LiveFlightTrackerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = MagicMock()
        self.bot.flightdata.airports_db = {
            "OTHH": {
                "ident": "OTHH",
                "name": "Hamad International Airport",
                "municipality": "Doha",
                "latitude_deg": 25.273,
                "longitude_deg": 51.608,
            },
            "VTSP": {
                "ident": "VTSP",
                "name": "Phuket International Airport",
                "municipality": "Phuket",
                "latitude_deg": 8.113,
                "longitude_deg": 98.317,
            },
        }
        self.cog = StoppedFlightMonitor.__new__(StoppedFlightMonitor)
        self.cog.bot = self.bot
        self.cog._live_flight_trackers = {}

        self.schedule = {
            "id": "sched-123",
            "aircraft_id": "ac-uuid-1",
            "callsign": "QRV001",
            "origin_icao": "OTHH",
            "destination_icao": "VTSP",
            "model": "Boeing 787-9",
        }
        self.pilot = {
            "discordid": "987654321",
            "callsign": "QRV001",
            "pilotname": "Bumy",
        }
        # Position halfway: ~2000 NM from VTSP, speed 450 kts -> ~266 min to dest, ~226 min to TOD
        self.position = {
            "latitude": 18.0,
            "longitude": 75.0,
            "altitude": 38000.0,
            "speed": 450.0,
            "vertical_speed": 0.0,
            "is_on_ground": False,
            "state": 2,
        }

    def test_flight_progress_calculation(self):
        dist, eta = _flight_progress(self.bot, self.position, "VTSP")
        self.assertIsNotNone(dist)
        self.assertIsNotNone(eta)
        self.assertGreater(dist, 1000)
        self.assertGreater(eta, 40)

    def test_build_live_flight_embed_cruising(self):
        embed = self.cog._build_live_flight_embed(
            registration="A7-BCB",
            schedule=self.schedule,
            position=self.position,
            status="ENROUTE",
            pilot=self.pilot,
        )

        self.assertIn("Live Flight Tracker", embed.title)
        self.assertIn("QRV001", embed.title)
        self.assertIn("OTHH", embed.description)
        self.assertIn("VTSP", embed.description)
        self.assertIn("A7-BCB", embed.description)

        # Check telemetry is directly in description without redundant field labels
        self.assertIn("Enroute", embed.description)
        self.assertIn("FL380", embed.description)
        self.assertIn("450 kts", embed.description)
        self.assertIn("NM", embed.description)

        field_names = [f.name for f in embed.fields]
        self.assertIn("TOP OF DESCENT (TOD)", field_names)
        self.assertIn("ESTIMATED ARRIVAL", field_names)
        # Redundant field labels are removed
        self.assertNotIn("STATUS", field_names)
        self.assertNotIn("ALTITUDE", field_names)
        self.assertNotIn("SPEED", field_names)
        self.assertNotIn("REMAINING", field_names)

        # Check TOD field contains countdown relative tag AND exact Zulu time and subtext
        tod_field = next(f for f in embed.fields if f.name == "TOP OF DESCENT (TOD)")
        self.assertIn("<t:", tod_field.value)
        self.assertIn("Z", tod_field.value)
        self.assertIn("-# Take controls ~40m prior", tod_field.value)

        # Check Estimated Arrival field contains exact Zulu time
        eta_field = next(f for f in embed.fields if f.name == "ESTIMATED ARRIVAL")
        self.assertIn("Z", eta_field.value)

        # Check footer is removed
        self.assertIsNone(embed.footer.text)

    def test_build_live_flight_embed_within_arrival_window(self):
        # Position very close to VTSP: ~150 NM away, 350 kts -> ~25 min to destination (<40 min)
        close_position = {
            "latitude": 8.5,
            "longitude": 96.0,
            "altitude": 14000.0,
            "speed": 350.0,
            "vertical_speed": -1200.0,
            "is_on_ground": False,
            "state": 2,
        }
        embed = self.cog._build_live_flight_embed(
            registration="A7-BCB",
            schedule=self.schedule,
            position=close_position,
            status="ENROUTE",
            pilot=self.pilot,
        )

        self.assertIn("Within Arrival Window", embed.title)
        tod_field = next(f for f in embed.fields if f.name == "TOP OF DESCENT (TOD)")
        self.assertIn("Take controls now!", tod_field.value)

    def test_build_live_flight_embed_stopped_at_tod(self):
        embed = self.cog._build_live_flight_embed(
            registration="A7-BCB",
            schedule=self.schedule,
            position=self.position,
            status="STOPPED",
            pilot=self.pilot,
        )

        self.assertIn("Paused at TOD", embed.title)
        arrival_field = next(f for f in embed.fields if f.name == "ARRIVAL WINDOW")
        self.assertIn("Paused since", arrival_field.value)

    def test_build_live_flight_embed_landed(self):
        embed = self.cog._build_live_flight_embed(
            registration="A7-BCB",
            schedule=self.schedule,
            position=self.position,
            status="LANDED",
            pilot=self.pilot,
        )

        self.assertIn("Flight Completed", embed.title)
        self.assertIn("Welcome to **VTSP**", embed.description)

    async def test_start_and_update_live_flight_tracker(self):
        mock_user = AsyncMock()
        mock_msg = AsyncMock()
        mock_msg.id = 112233
        mock_msg.channel.id = 445566
        mock_user.send = AsyncMock(return_value=mock_msg)
        self.bot.get_user = MagicMock(return_value=mock_user)

        model = MagicMock()
        model.save_live_flight_tracker = AsyncMock()
        model.update_live_flight_tracker_status = AsyncMock()

        # 1. Start tracker
        await self.cog._start_live_flight_tracker(
            model=model,
            aircraft_id="ac-uuid-1",
            registration="A7-BCB",
            schedule=self.schedule,
            position=self.position,
            pilot=self.pilot,
        )

        mock_user.send.assert_awaited_once()
        self.assertIn("ac-uuid-1", self.cog._live_flight_trackers)
        model.save_live_flight_tracker.assert_awaited_once()

        # 2. Update tracker
        mock_channel = AsyncMock()
        mock_channel.fetch_message = AsyncMock(return_value=mock_msg)
        self.bot.get_channel = MagicMock(return_value=mock_channel)

        positions = {"ac-uuid-1": self.position}
        schedules_by_aircraft = {"ac-uuid-1": [self.schedule]}

        await self.cog._update_live_flight_trackers(
            client=MagicMock(),
            model=model,
            fleet=[],
            positions=positions,
            schedules_by_aircraft=schedules_by_aircraft,
        )

        mock_msg.edit.assert_awaited_once()

    async def test_track_flight_slash_command_manual(self):
        interaction = AsyncMock()
        interaction.user.id = 987654321
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        mock_msg = AsyncMock()
        mock_msg.id = 998877
        mock_msg.channel.id = 332211
        interaction.user.send = AsyncMock(return_value=mock_msg)

        # Mock snapshot
        mock_ac = SimpleNamespace(
            aircraft_id="ac-uuid-1",
            registration="A7-BCB",
            formatted_status="Enroute",
            last_pilot_id="if-user-1",
        )
        snapshot = MagicMock()
        snapshot.__iter__.return_value = [mock_ac]
        snapshot.to_positions_dict.return_value = {"ac-uuid-1": self.position}
        snapshot.schedules_by_aircraft = {"ac-uuid-1": [self.schedule]}

        manager = AsyncMock()
        manager.get_live_fleet.return_value = snapshot
        self.bot.if_live_manager = manager

        self.bot.pilots_model.identify_pilot = AsyncMock(return_value={"success": True, "pilot_data": self.pilot})
        self.bot.pilots_model.get_pilot_by_discord_id = AsyncMock(return_value=self.pilot)

        # Trigger manually with registration
        with patch("cogs.stopped_flight_monitor.ORG_ID", "test-org"):
            await self.cog.track_flight.callback(self.cog, interaction, registration="A7-BCB")

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        interaction.user.send.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        self.assertIn("A7-BCB", interaction.followup.send.call_args[0][0])
        self.assertIn("ac-uuid-1", self.cog._live_flight_trackers)

    def test_parse_step_climbs_from_flight_plan(self):
        fpl_items = [
            {"name": "DOH", "location": {"latitude": 25.0, "longitude": 52.0}, "altitude": 32000},
            {"name": "PASOV", "location": {"latitude": 22.0, "longitude": 60.0}, "altitude": 34000},
            {"name": "MABOD", "location": {"latitude": 18.0, "longitude": 75.0}, "altitude": 36000},
            {"name": "GIVAL", "location": {"latitude": 15.0, "longitude": 85.0}, "altitude": 38000},
            {"name": "IDASO", "location": {"latitude": 12.0, "longitude": 90.0}, "altitude": 38000},
            {"name": "PUDET", "location": {"latitude": 8.5, "longitude": 98.0}, "altitude": 5000},
        ]
        # Current aircraft near PASOV at FL340 (34,000 ft), speed 450 kts
        step_climbs = _parse_step_climbs_from_flight_plan(
            flight_plan_items=fpl_items,
            current_alt=34000.0,
            current_lat=22.0,
            current_lon=60.0,
            current_speed=450.0,
        )

        self.assertEqual(len(step_climbs), 2)
        # First step: FL360 at MABOD
        self.assertEqual(step_climbs[0]["target_fl"], 360)
        self.assertEqual(step_climbs[0]["waypoint"], "MABOD")
        self.assertGreater(step_climbs[0]["dist_nm"], 0)
        self.assertGreater(step_climbs[0]["min_remaining"], 0)
        self.assertTrue(step_climbs[0]["step_zulu"].endswith("Z"))

        # Second step: FL380 at GIVAL
        self.assertEqual(step_climbs[1]["target_fl"], 380)
        self.assertEqual(step_climbs[1]["waypoint"], "GIVAL")
        self.assertGreater(step_climbs[1]["dist_nm"], step_climbs[0]["dist_nm"])
        self.assertGreater(step_climbs[1]["min_remaining"], step_climbs[0]["min_remaining"])

    def test_build_live_flight_embed_with_step_climbs(self):
        step_climbs = [
            {
                "target_fl": 360,
                "target_alt": 36000,
                "waypoint": "PASOV",
                "dist_nm": 600,
                "min_remaining": 81,
                "step_ts": 1762015320,
                "step_zulu": "16:20Z",
            },
            {
                "target_fl": 380,
                "target_alt": 38000,
                "waypoint": "MABOD",
                "dist_nm": 40,
                "min_remaining": 5,
                "step_ts": 1762011000,
                "step_zulu": "15:05Z",
            },
        ]
        embed = self.cog._build_live_flight_embed(
            registration="A7-BCB",
            schedule=self.schedule,
            position=self.position,
            status="ENROUTE",
            pilot=self.pilot,
            step_climbs=step_climbs,
        )

        field_names = [f.name for f in embed.fields]
        self.assertIn("STEP CLIMBS", field_names)

        sc_field = next(f for f in embed.fields if f.name == "STEP CLIMBS")
        # Check standard timing
        self.assertIn("FL360 at PASOV", sc_field.value)
        self.assertIn("16:20Z", sc_field.value)
        # Check immediate timing (min_remaining <= 10)
        self.assertIn("FL380 at MABOD", sc_field.value)
        self.assertIn("Immediate / NOW!", sc_field.value)
        self.assertIn("15:05Z", sc_field.value)

    def test_build_live_flight_embed_no_upcoming_step_climbs(self):
        embed = self.cog._build_live_flight_embed(
            registration="A7-BCB",
            schedule=self.schedule,
            position=self.position,
            status="ENROUTE",
            pilot=self.pilot,
            step_climbs=[],
        )

        field_names = [f.name for f in embed.fields]
        # When no step climbs remain, no redundant field is shown
        self.assertNotIn("STEP CLIMBS", field_names)

    async def test_get_flight_plan_items_cached(self):
        self.bot.if_api_manager = MagicMock()
        self.bot.if_api_manager.get_flight_plan = AsyncMock(
            return_value={"result": {"flightPlanItems": [{"name": "PASOV", "altitude": 36000}]}}
        )

        items1 = await self.cog._get_flight_plan_items("flight-12345")
        self.assertEqual(len(items1), 1)
        self.assertEqual(items1[0]["name"], "PASOV")
        self.bot.if_api_manager.get_flight_plan.assert_awaited_once_with("flight-12345")

        # Second call should hit the cache
        items2 = await self.cog._get_flight_plan_items("flight-12345")
        self.assertEqual(len(items2), 1)
        self.bot.if_api_manager.get_flight_plan.assert_awaited_once()

    async def test_generate_flight_map_file(self):
        import io
        fake_buf = io.BytesIO(b"fake_image_bytes")
        mock_map_service = AsyncMock()
        mock_map_service.create_route_map = AsyncMock(return_value=fake_buf)
        self.bot.route_map_service = mock_map_service

        map_file = await self.cog._generate_flight_map_file(
            schedule=self.schedule,
            position=self.position,
            flight_plan_items=[{"name": "PASOV", "location": {"latitude": 20.0, "longitude": 60.0}}],
            registration="A7-BCB",
        )
        self.assertIsNotNone(map_file)
        self.assertEqual(map_file.filename, "flight_map.png")
        mock_map_service.create_route_map.assert_awaited_once()

    def test_build_live_flight_embed_has_map(self):
        embed = self.cog._build_live_flight_embed(
            registration="A7-BCB",
            schedule=self.schedule,
            position=self.position,
            status="ENROUTE",
            pilot=self.pilot,
            has_map=True,
        )
        self.assertEqual(embed.image.url, "attachment://flight_map.png")


if __name__ == "__main__":
    unittest.main()

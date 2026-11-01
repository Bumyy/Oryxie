import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.stopped_flight_monitor import (
    StoppedFlightMonitor,
    _flight_progress,
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
        self.assertIn("A7-BCB", embed.title)

        field_names = [f.name for f in embed.fields]
        self.assertIn("Route & Aircraft", field_names)
        self.assertIn("Altitude", field_names)
        self.assertIn("Ground Speed", field_names)
        self.assertIn("Time Until Take Control (TOD)", field_names)

        # Check altitude format FL380
        alt_field = next(f for f in embed.fields if f.name == "Altitude")
        self.assertIn("FL380", alt_field.value)

        # Check speed format
        spd_field = next(f for f in embed.fields if f.name == "Ground Speed")
        self.assertIn("450 kts", spd_field.value)

        # Check TOD field contains countdown relative tag AND exact Zulu time
        tod_field = next(f for f in embed.fields if f.name == "Time Until Take Control (TOD)")
        self.assertIn("<t:", tod_field.value)
        self.assertIn("Exact TOD Time:", tod_field.value)
        self.assertIn("Z", tod_field.value)

        # Check Estimated Arrival field contains exact Zulu time
        eta_field = next(f for f in embed.fields if f.name == "Estimated Arrival")
        self.assertIn("Z", eta_field.value)

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
        tod_field = next(f for f in embed.fields if f.name == "Time Until Take Control (TOD)")
        self.assertIn("Take controls now!", tod_field.value)
        self.assertIn("Z", tod_field.value)

    def test_build_live_flight_embed_stopped_at_tod(self):
        embed = self.cog._build_live_flight_embed(
            registration="A7-BCB",
            schedule=self.schedule,
            position=self.position,
            status="STOPPED",
            pilot=self.pilot,
        )

        self.assertIn("Paused at TOD", embed.title)
        arrival_field = next(f for f in embed.fields if f.name == "Arrival Window")
        self.assertIn("paused at Top of Descent", arrival_field.value)

    def test_build_live_flight_embed_landed(self):
        embed = self.cog._build_live_flight_embed(
            registration="A7-BCB",
            schedule=self.schedule,
            position=self.position,
            status="LANDED",
            pilot=self.pilot,
        )

        self.assertIn("Flight Completed", embed.title)
        status_field = next(f for f in embed.fields if f.name == "Flight Status")
        self.assertIn("Welcome to", status_field.value)

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


if __name__ == "__main__":
    unittest.main()

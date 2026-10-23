import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.dm_logging import DM_LOG_CHANNEL_ID
from cogs.stopped_flight_monitor import (
    StoppedFlightMonitor,
    _aircraft_status_text,
)


class AircraftStatusModel:
    def __init__(self, statuses):
        self.statuses = statuses
        self.updates = []

    async def has_aircraft_status_table(self):
        return True

    async def get_aircraft_statuses(self):
        return self.statuses.copy()

    async def set_aircraft_status(self, aircraft_id, registration, status_text):
        self.updates.append((aircraft_id, registration, status_text))
        self.statuses[aircraft_id] = status_text
        return 1


class AircraftStatusLoggingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.aircraft = {"id": "aircraft-1", "registration": "A7QRV"}
        self.position = {
            "state": 2,
            "is_on_ground": False,
            "latitude": 25.0,
            "longitude": 51.0,
        }
        self.flight = {
            "status": 6,
            "sequence": 1,
            "destination_icao": "EGLL",
            "origin_icao": "OTHH",
        }

    def make_cog(self, channel):
        bot = MagicMock()
        bot.get_channel.return_value = channel
        cog = StoppedFlightMonitor.__new__(StoppedFlightMonitor)
        cog.bot = bot
        cog._warned_status_logging_disabled = False
        return cog, bot

    def test_in_flight_status_includes_destination(self):
        text = _aircraft_status_text(
            MagicMock(),
            self.position,
            [self.flight],
        )

        self.assertEqual(text, "ENROUTE TO EGLL")

    async def test_enroute_transition_posts_and_persists_once(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        cog, bot = self.make_cog(channel)
        model = AircraftStatusModel({"aircraft-1": "PARKED AT OTHH"})
        client = MagicMock()
        client.get_aircraft_position = AsyncMock(return_value=self.position)

        with (
            patch(
                "cogs.stopped_flight_monitor.STATUS_LOG_CHANNEL_ID",
                DM_LOG_CHANNEL_ID,
            ),
            patch(
                "cogs.stopped_flight_monitor.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            await cog._log_fleet_status_changes(
                client,
                model,
                [self.aircraft],
                {"aircraft-1": [self.flight]},
            )

        bot.get_channel.assert_called_once_with(DM_LOG_CHANNEL_ID)
        channel.send.assert_awaited_once()
        self.assertIn("ENROUTE TO EGLL", channel.send.await_args.args[0])
        self.assertEqual(
            model.updates,
            [("aircraft-1", "A7QRV", "ENROUTE TO EGLL")],
        )

    async def test_unchanged_status_does_not_post_again(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        cog, _ = self.make_cog(channel)
        model = AircraftStatusModel({"aircraft-1": "ENROUTE TO EGLL"})
        client = MagicMock()
        client.get_aircraft_position = AsyncMock(return_value=self.position)

        await cog._log_fleet_status_changes(
            client,
            model,
            [self.aircraft],
            {"aircraft-1": [self.flight]},
        )

        channel.send.assert_not_awaited()
        self.assertEqual(model.updates, [])

    async def test_failed_channel_post_keeps_change_pending(self):
        channel = MagicMock()
        channel.send = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(), "Missing permissions")
        )
        cog, _ = self.make_cog(channel)
        model = AircraftStatusModel({"aircraft-1": "PARKED AT OTHH"})
        client = MagicMock()
        client.get_aircraft_position = AsyncMock(return_value=self.position)

        with (
            patch(
                "cogs.stopped_flight_monitor.STATUS_LOG_CHANNEL_ID",
                DM_LOG_CHANNEL_ID,
            ),
            patch(
                "cogs.stopped_flight_monitor.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            await cog._log_fleet_status_changes(
                client,
                model,
                [self.aircraft],
                {"aircraft-1": [self.flight]},
            )
            await cog._log_fleet_status_changes(
                client,
                model,
                [self.aircraft],
                {"aircraft-1": [self.flight]},
            )

        self.assertEqual(channel.send.await_count, 2)
        self.assertEqual(model.statuses["aircraft-1"], "PARKED AT OTHH")
        self.assertEqual(model.updates, [])


if __name__ == "__main__":
    unittest.main()

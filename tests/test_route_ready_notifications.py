import unittest
from unittest.mock import AsyncMock, MagicMock

import discord

from cogs.dm_logging import DM_LOG_CHANNEL_ID, post_dm_report
from cogs.route_ready_notifications import RouteReadyNotifications, _arrival_candidates


class RouteReadyDatabase:
    def __init__(self):
        self.events = {}
        self.states = [
            {"schedule_id": "arrived", "last_status": 8},
            {"schedule_id": "next", "last_status": 1},
        ]

    async def fetch_all(self, query, args=None):
        if "FROM if_route_ready_dm_events" in query:
            return [
                {"arrival_schedule_id": event["arrival_schedule_id"]}
                for event in self.events.values()
                if event["delivery_status"] == "failed"
            ]
        return self.states

    async def fetch_one(self, query, args=None):
        return self.events.get(args[0])

    async def execute(self, query, args=None):
        if "INSERT INTO if_route_ready_dm_events" in query:
            event_key, arrived_id, next_id, if_user_id, discord_id, status = args
            self.events[event_key] = {
                "delivery_status": status,
                "retry_pending": False,
                "arrival_schedule_id": arrived_id,
                "next_schedule_id": next_id,
                "if_user_id": if_user_id,
                "discord_user_id": discord_id,
            }
        return 1


class RouteReadyNotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.arrived = {
            "id": "arrived",
            "aircraft_id": "aircraft",
            "status": 11,
            "sequence": 1,
            "destination_icao": "KJFK",
        }
        self.next_route = {
            "id": "next",
            "aircraft_id": "aircraft",
            "status": 1,
            "sequence": 2,
            "origin_icao": "KJFK",
            "destination_icao": "EGLL",
            "callsign": "QRV123",
            "crew": [{"userId": "if-pilot"}],
        }

    def test_candidates_include_arrival_with_a_failed_delivery(self):
        candidates = _arrival_candidates(
            [self.arrived, self.next_route],
            {"arrived": 11},
            {"arrived"},
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][0], "aircraft")

    async def test_failed_route_ready_dm_is_reported_retried_and_not_resent(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        user = MagicMock()
        user.send = AsyncMock(
            side_effect=[
                discord.Forbidden(MagicMock(), "DM disabled"),
                MagicMock(),
            ]
        )
        bot = MagicMock()
        bot.get_channel.return_value = channel
        bot.fetch_channel = AsyncMock(return_value=channel)
        bot.get_user.return_value = user
        bot.fetch_user = AsyncMock(return_value=user)
        bot.pilots_model = MagicMock()
        bot.pilots_model.get_pilot_by_ifuserid = AsyncMock(
            return_value={"discordid": "12345", "callsign": "QRV123"}
        )

        cog = RouteReadyNotifications.__new__(RouteReadyNotifications)
        cog.bot = bot
        db = RouteReadyDatabase()
        client = MagicMock()
        client.get_aircraft_position = AsyncMock(
            return_value={"is_on_ground": True}
        )
        schedules = [self.arrived, self.next_route]

        for _ in range(3):
            await cog._process_schedules(
                client,
                db,
                bot.pilots_model,
                schedules,
                {"aircraft": "A7QRV"},
            )

        self.assertEqual(user.send.await_count, 2)
        self.assertGreaterEqual(channel.send.await_count, 2)
        self.assertTrue(
            any(
                "Not delivered / follow-up needed" in str(call.args[0])
                for call in channel.send.await_args_list
                if call.args
            )
        )
        self.assertTrue(
            any(
                "Route-ready DM report" in str(call.args[0])
                for call in channel.send.await_args_list
                if call.args
            )
        )

    async def test_dm_report_helper_posts_to_configured_staff_channel(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        bot = MagicMock()
        bot.get_channel.return_value = channel

        posted = await post_dm_report(
            bot,
            "delivery report",
            MagicMock(),
            "test DM",
        )

        self.assertTrue(posted)
        bot.get_channel.assert_called_once_with(DM_LOG_CHANNEL_ID)
        channel.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.dm_logging import post_dm_report
from cogs.route_ready_notifications import (
    ROUTE_READY_TABLE_DDL,
    RouteReadyNotifications,
    _arrival_candidates,
)


class RouteReadyDatabase:
    def __init__(self):
        self.events = {}
        self.states = [
            {"schedule_id": "arrived", "last_status": 8},
            {"schedule_id": "next", "last_status": 1},
        ]

    async def fetch_all(self, query, args=None):
        if "delivery_status IN ('failed', 'failed_final')" in query:
            return [
                {"arrival_schedule_id": event["arrival_schedule_id"]}
                for event in self.events.values()
                if event["delivery_status"] in {"failed", "failed_final"}
            ]
        return self.states

    async def fetch_one(self, query, args=None):
        return self.events.get(args[0])

    async def execute(self, query, args=None):
        if "INSERT INTO if_route_ready_dm_events" in query:
            event_key, arrived_id, next_id, if_user_id, discord_id, status = args
            self.events[event_key] = {
                "delivery_status": status,
                "arrival_schedule_id": arrived_id,
                "next_schedule_id": next_id,
                "if_user_id": if_user_id,
                "discord_user_id": discord_id,
            }
        elif "INSERT INTO if_route_ready_schedule_state" in query:
            schedule_id, last_status = args
            for row in self.states:
                if row["schedule_id"] == schedule_id:
                    row["last_status"] = last_status
        elif "SET delivery_status = 'failed_reported'" in query:
            arrival_id = args[0]
            for event in self.events.values():
                if (
                    event["arrival_schedule_id"] == arrival_id
                    and event["delivery_status"] == "failed_final"
                ):
                    event["delivery_status"] = "failed_reported"
        return 1


class RouteReadySchemaDatabase:
    def __init__(self, allow_create=True):
        self.tables = set()
        self.allow_create = allow_create

    async def fetch_one(self, query, args=None):
        table_name = args[0]
        return {"table": table_name} if table_name in self.tables else None

    async def execute(self, query, args=None):
        if query.strip().startswith("CREATE TABLE") and self.allow_create:
            table_name = query.split("CREATE TABLE IF NOT EXISTS ", 1)[1].split(
                " ",
                1,
            )[0]
            self.tables.add(table_name)
            return 1
        return 0


class RouteReadyNotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dm_log_channel_patcher = patch(
            "cogs.dm_logging.DM_LOG_CHANNEL_ID",
            123456789,
        )
        self.dm_log_channel_patcher.start()
        self.addCleanup(self.dm_log_channel_patcher.stop)
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

    def test_arrival_is_not_selected_again_after_failed_delivery(self):
        candidates = _arrival_candidates(
            [self.arrived, self.next_route],
            {"arrived": 11},
        )

        self.assertEqual(candidates, [])

    async def test_ensure_tables_creates_missing_route_ready_tables(self):
        db = RouteReadySchemaDatabase()
        cog = RouteReadyNotifications.__new__(RouteReadyNotifications)
        cog._tables_ready = False

        await cog._ensure_tables(db)

        self.assertEqual(db.tables, set(ROUTE_READY_TABLE_DDL))
        self.assertTrue(cog._tables_ready)

    async def test_ensure_tables_reports_missing_create_permission(self):
        db = RouteReadySchemaDatabase(allow_create=False)
        cog = RouteReadyNotifications.__new__(RouteReadyNotifications)
        cog._tables_ready = False

        with self.assertRaisesRegex(
            RuntimeError,
            "Grant CREATE TABLE permissions",
        ):
            await cog._ensure_tables(db)

    async def test_failed_route_ready_dm_gets_one_retry_then_stops(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        user = MagicMock()
        user.send = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(), "DM disabled")
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
        self.assertEqual(channel.send.await_count, 2)
        self.assertTrue(
            any(
                "**Not delivered:**" in str(call.args[0])
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
        self.assertEqual(
            db.events[next(iter(db.events))]["delivery_status"],
            "failed_reported",
        )

    async def test_route_ready_dm_retry_can_recover_transient_failure(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        user = MagicMock()
        user.send = AsyncMock(
            side_effect=[
                discord.HTTPException(MagicMock(), "Temporary failure"),
                MagicMock(),
            ]
        )
        bot = MagicMock()
        bot.get_channel.return_value = channel
        bot.get_user.return_value = user
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
        self.assertEqual(channel.send.await_count, 2)
        self.assertEqual(
            db.events[next(iter(db.events))]["delivery_status"],
            "delivered",
        )

    async def test_route_ready_report_retries_without_duplicating_delivered_dm(self):
        channel = MagicMock()
        channel.send = AsyncMock(
            side_effect=[
                discord.Forbidden(MagicMock(), "Missing permissions"),
                MagicMock(),
            ]
        )
        user = MagicMock()
        user.send = AsyncMock()
        bot = MagicMock()
        bot.get_channel.return_value = channel
        bot.get_user.return_value = user
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

        self.assertEqual(user.send.await_count, 1)
        self.assertEqual(channel.send.await_count, 2)
        self.assertEqual(
            db.events[next(iter(db.events))]["delivery_status"],
            "delivered",
        )

    async def test_dm_report_helper_posts_to_configured_staff_channel(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        bot = MagicMock()
        bot.get_channel.return_value = channel

        with patch("cogs.dm_logging.DM_LOG_CHANNEL_ID", 123456789):
            posted = await post_dm_report(
                bot,
                "delivery report",
                MagicMock(),
                "test DM",
            )

        self.assertTrue(posted)
        bot.get_channel.assert_called_once_with(123456789)
        channel.send.assert_awaited_once()

    async def test_dm_report_helper_splits_reports_over_discord_limit(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        bot = MagicMock()
        bot.get_channel.return_value = channel
        content = "DM delivery report\n" + ("x" * 4500)

        with patch("cogs.dm_logging.DM_LOG_CHANNEL_ID", 123456789):
            posted = await post_dm_report(
                bot,
                content,
                MagicMock(),
                "large test DM",
            )

        self.assertTrue(posted)
        chunks = [call.args[0] for call in channel.send.await_args_list]
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 2000 for chunk in chunks))
        self.assertEqual("".join(chunks), content)


if __name__ == "__main__":
    unittest.main()

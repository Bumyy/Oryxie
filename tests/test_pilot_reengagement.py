"""Unit tests for the Pilot Re-engagement system and ReengagementModel."""

from datetime import date, datetime, timedelta, timezone
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import discord

from cogs.pilot_reengagement_cog import (
    PilotReengagementCog,
    ReengagementPromptView,
)
from database.reengagement_model import ReengagementModel


class ReengagementModelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = MagicMock()
        self.db.fetch_all = AsyncMock()
        self.db.fetch_one = AsyncMock()
        self.db.execute = AsyncMock()
        self.model = ReengagementModel(self.db)

    async def test_get_inactive_pilot_candidates_queries_expected_window(self):
        self.db.fetch_all.return_value = [
            {
                "pilot_id": 1,
                "callsign": "QRV001",
                "discordid": "123456789",
                "days_inactive": 25,
            }
        ]

        candidates = await self.model.get_inactive_pilot_candidates(
            min_days=14, max_days=180, cooldown_days=7
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["callsign"], "QRV001")
        self.db.fetch_all.assert_awaited_once()
        args = self.db.fetch_all.await_args.args[1]
        self.assertEqual(args, (7, 180, 14))

    async def test_is_pilot_on_cooldown(self):
        self.db.fetch_one.return_value = {"id": 1}
        on_cooldown = await self.model.is_pilot_on_cooldown("123456789", cooldown_days=7)
        self.assertTrue(on_cooldown)

        self.db.fetch_one.return_value = None
        on_cooldown = await self.model.is_pilot_on_cooldown("987654321", cooldown_days=7)
        self.assertFalse(on_cooldown)

    async def test_record_nudge_inserts_expected_row(self):
        self.db.execute.return_value = 1
        result = await self.model.record_nudge(
            pilot_id=42,
            discord_user_id="123456789",
            if_user_id="if-user-guid",
            aircraft_id="ac-guid",
            registration="A7-BAC",
            aircraft_model="Boeing 777-300ER",
            origin_icao="OTHH",
            destination_icao="EGLL",
            channel_id="dm-chan-1",
            message_id="msg-1",
            delivery_status="wave_1",
        )
        self.assertEqual(result, 1)
        self.db.execute.assert_awaited_once()
        query, args = self.db.execute.await_args.args
        self.assertIn("INSERT INTO if_aircraft_reengagement_logs", query)
        self.assertEqual(args[0], 42)
        self.assertEqual(args[1], "123456789")
        self.assertEqual(args[4], "A7-BAC")
        self.assertEqual(args[6], "OTHH")
        self.assertEqual(args[7], "EGLL")
        self.assertEqual(args[10], "wave_1")

    async def test_snooze_pilot_records_snooze_event(self):
        self.db.execute.return_value = 1
        await self.model.snooze_pilot("123456789", pilot_id=42, snooze_days=30)
        self.db.execute.assert_awaited_once()
        query, args = self.db.execute.await_args.args
        self.assertIn("'snoozed'", query)
        self.assertEqual(args[1], "123456789")


class PilotReengagementCogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = MagicMock()
        self.bot.aircraft_name_map = {"ac-1": "Boeing 777-300ER"}
        self.cog = PilotReengagementCog(self.bot, auto_start=False)

    def test_get_ready_now_aircraft_identifies_unassigned_first_leg(self):
        ac1 = MagicMock()
        ac1.aircraft_id = "ac-1"
        ac1.registration = "A7-BAC"
        # First leg unassigned (crew == []) -> Ready now
        ac1.schedules = [
            {"id": "s1", "status": 1, "sequence": 1, "origin_icao": "OTHH", "destination_icao": "EGLL", "crew": []},
            {"id": "s2", "status": 1, "sequence": 2, "origin_icao": "EGLL", "destination_icao": "OTHH", "crew": []},
        ]

        ac2_busy = MagicMock()
        ac2_busy.aircraft_id = "ac-2"
        ac2_busy.registration = "A7-BAG"
        # First leg has crew assigned -> NOT ready now
        ac2_busy.schedules = [
            {"id": "s3", "status": 6, "sequence": 1, "origin_icao": "OTHH", "destination_icao": "OMDB", "crew": [{"userId": "pilot-1"}]},
            {"id": "s4", "status": 1, "sequence": 2, "origin_icao": "OMDB", "destination_icao": "OTHH", "crew": []},
        ]

        ac3_retired = MagicMock()
        ac3_retired.aircraft_id = "ac-3"
        ac3_retired.registration = "A7-BFC"  # In RETIRED_REGISTRATIONS
        ac3_retired.schedules = [
            {"id": "s5", "status": 1, "sequence": 1, "origin_icao": "OTHH", "destination_icao": "EGLL", "crew": []},
        ]

        fleet_snapshot = [ac1, ac2_busy, ac3_retired]
        ready = self.cog._get_ready_now_aircraft(fleet_snapshot)

        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["registration"], "A7-BAC")
        self.assertEqual(ready[0]["origin_icao"], "OTHH")
        self.assertEqual(ready[0]["destination_icao"], "EGLL")
        self.assertEqual(ready[0]["model"], "Boeing 777-300ER")

    def test_build_nudge_embed_includes_personalized_details(self):
        candidate = {"callsign": "QRV042", "days_inactive": 28}
        aircraft = {
            "registration": "A7-BAC",
            "model": "Boeing 777-300ER",
            "origin_icao": "OTHH",
            "destination_icao": "EGLL",
            "callsign": "QR001",
        }
        embed = self.cog._build_nudge_embed(candidate, aircraft, wave=1)

        self.assertIn("Captain QRV042", embed.title)
        self.assertIn("28 days", embed.description)
        field_dict = {f.name: f.value for f in embed.fields}
        self.assertIn("A7-BAC", field_dict["Ready Now Aircraft"])
        self.assertIn("OTHH → EGLL", field_dict["Assigned Route"])
        self.assertIn("0 flights ahead", field_dict["Queue Status"])

    def test_select_candidate_prioritizes_online_before_offline(self):
        m_online = MagicMock()
        m_online.id = 111
        m_online.status = discord.Status.online

        m_offline = MagicMock()
        m_offline.id = 222
        m_offline.status = discord.Status.offline

        fleet_members = {"111": m_online, "222": m_offline}
        candidates = [
            {"discordid": "222", "callsign": "QRV002"},
            {"discordid": "111", "callsign": "QRV001"},
        ]

        # Prioritizes online user first
        chosen = self.cog._select_candidate_for_aircraft(candidates, fleet_members, already_contacted=set())
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["discordid"], "111")

        # When online user is already contacted, falls back to offline user
        chosen_fallback = self.cog._select_candidate_for_aircraft(candidates, fleet_members, already_contacted={"111"})
        self.assertIsNotNone(chosen_fallback)
        self.assertEqual(chosen_fallback["discordid"], "222")

    async def test_reengagement_view_claim_button_assigns_outbound_and_inbound_legs(self):
        cog_mock = MagicMock()
        cog_mock._active_campaigns = {"A7-BAC": {"wave": 1}}
        cog_mock._post_staff_report = AsyncMock()
        cog_mock.expire_aircraft_nudges = AsyncMock()

        view = ReengagementPromptView(
            aircraft_id="ac-1",
            schedule_id="s1",
            registration="A7-BAC",
            route_str="OTHH → EGLL",
            cog=cog_mock,
        )

        interaction = MagicMock()
        interaction.user.id = 123456789
        interaction.message.id = 999
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock pilot model
        interaction.client.pilots_model = MagicMock()
        interaction.client.pilots_model.get_pilot_by_discord_id = AsyncMock(
            return_value={"id": 42, "callsign": "QRV042", "ifuserid": "pilot-guid-1"}
        )

        # Mock reengagement model
        interaction.client.reengagement_model = MagicMock()
        interaction.client.reengagement_model.update_nudge_status = AsyncMock()

        # Mock live manager and client
        leg1 = {
            "id": "s1",
            "aircraft_id": "ac-1",
            "callsign": "QR001",
            "origin_icao": "OTHH",
            "destination_icao": "EGLL",
            "status": 1,
            "crew": [],
        }
        leg2 = {
            "id": "s2",
            "aircraft_id": "ac-1",
            "callsign": "QR002",
            "origin_icao": "EGLL",
            "destination_icao": "OTHH",
            "status": 1,
            "crew": [],
        }

        manager = MagicMock()
        client_mock = AsyncMock()
        client_mock.list_aircraft_schedules = AsyncMock(return_value=[leg1, leg2])

        def mock_assign_crew(schedule_id, user_id, role):
            # return updated route with assigned crew
            return {
                "id": schedule_id,
                "crew": [{"userId": user_id, "role": role}],
            }

        client_mock.assign_crew = AsyncMock(side_effect=mock_assign_crew)

        # Async context manager for manager.get_client()
        ctx_mock = AsyncMock()
        ctx_mock.__aenter__ = AsyncMock(return_value=client_mock)
        ctx_mock.__aexit__ = AsyncMock(return_value=None)
        manager.get_client = AsyncMock(return_value=ctx_mock)

        interaction.client.if_live_manager = manager

        await view.request_flight_button.callback(interaction)

        # Verify assign_crew called for both leg 1 and leg 2
        self.assertEqual(client_mock.assign_crew.await_count, 2)
        client_mock.assign_crew.assert_any_await("s1", "pilot-guid-1", 0)
        client_mock.assign_crew.assert_any_await("s2", "pilot-guid-1", 0)

        # Verify database status updated to 'claimed'
        interaction.client.reengagement_model.update_nudge_status.assert_awaited_once_with(
            "999", "claimed"
        )

        # Verify active campaign was removed
        self.assertNotIn("A7-BAC", cog_mock._active_campaigns)

        # Verify response sent to pilot
        interaction.followup.send.assert_awaited_once()
        sent_text = interaction.followup.send.await_args.args[0]
        self.assertIn("QRV042", sent_text)
        self.assertIn("A7-BAC", sent_text)
        self.assertIn("OTHH → EGLL", sent_text)
        self.assertIn("EGLL → OTHH", sent_text)

        # Verify staff report posted
        cog_mock._post_staff_report.assert_awaited_once()

    async def test_reengagement_view_claim_unlinked_pilot_rejected(self):
        view = ReengagementPromptView(
            aircraft_id="ac-1",
            schedule_id="s1",
            registration="A7-BAC",
            route_str="OTHH → EGLL",
        )
        interaction = MagicMock()
        interaction.user.id = 123456789
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Pilot has no IF linked account
        interaction.client.pilots_model = MagicMock()
        interaction.client.pilots_model.get_pilot_by_discord_id = AsyncMock(
            return_value={"id": 42, "callsign": "QRV042", "ifuserid": None}
        )

        await view.request_flight_button.callback(interaction)

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.await_args.args[0]
        self.assertIn("not linked", msg)

    async def test_reengagement_view_claim_already_claimed_rejected(self):
        cog_mock = MagicMock()
        cog_mock.expire_aircraft_nudges = AsyncMock()

        view = ReengagementPromptView(
            aircraft_id="ac-1",
            schedule_id="s1",
            registration="A7-BAC",
            route_str="OTHH → EGLL",
            cog=cog_mock,
        )
        interaction = MagicMock()
        interaction.user.id = 123456789
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        interaction.client.pilots_model = MagicMock()
        interaction.client.pilots_model.get_pilot_by_discord_id = AsyncMock(
            return_value={"id": 42, "callsign": "QRV042", "ifuserid": "pilot-guid-1"}
        )

        # First leg already has crew assigned
        leg1 = {
            "id": "s1",
            "aircraft_id": "ac-1",
            "status": 1,
            "crew": [{"userId": "other-pilot"}],
        }
        manager = MagicMock()
        client_mock = AsyncMock()
        client_mock.list_aircraft_schedules = AsyncMock(return_value=[leg1])

        ctx_mock = AsyncMock()
        ctx_mock.__aenter__ = AsyncMock(return_value=client_mock)
        ctx_mock.__aexit__ = AsyncMock(return_value=None)
        manager.get_client = AsyncMock(return_value=ctx_mock)
        interaction.client.if_live_manager = manager

        await view.request_flight_button.callback(interaction)

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.await_args.args[0]
        self.assertIn("already been claimed", msg)

    async def test_reengagement_view_snooze_button(self):
        view = ReengagementPromptView("s1", "A7-BAC", "OTHH → EGLL")
        interaction = MagicMock()
        interaction.user.id = 123456789
        interaction.client.reengagement_model = MagicMock()
        interaction.client.reengagement_model.snooze_pilot = AsyncMock()
        interaction.response.send_message = AsyncMock()

        await view.snooze_button.callback(interaction)

        interaction.client.reengagement_model.snooze_pilot.assert_awaited_once_with(
            discord_user_id="123456789", snooze_days=30
        )
        interaction.response.send_message.assert_awaited_once()
        self.assertIn("30 days", interaction.response.send_message.await_args.args[0])

    async def test_dispatch_reengagement_dry_run(self):
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        model = MagicMock()
        model.get_inactive_pilot_candidates = AsyncMock(
            return_value=[
                {"discordid": "111", "callsign": "QRV001", "days_inactive": 20, "pilot_id": 1},
                {"discordid": "222", "callsign": "QRV002", "days_inactive": 40, "pilot_id": 2},
            ]
        )
        manager = MagicMock()
        ac_mock = MagicMock()
        ac_mock.registration = "A7-BAC"
        ac_mock.aircraft_id = "ac-1"
        ac_mock.schedules = [{"id": "s1", "status": 1, "sequence": 1, "origin_icao": "OTHH", "destination_icao": "EGLL", "crew": []}]
        snapshot = [ac_mock]
        manager.get_live_fleet = AsyncMock(return_value=snapshot)

        self.bot.reengagement_model = model
        self.bot.if_live_manager = manager

        guild = MagicMock()
        role = MagicMock()
        m1 = MagicMock()
        m1.id = 111
        m1.status = discord.Status.online
        m1.bot = False
        m2 = MagicMock()
        m2.id = 222
        m2.status = discord.Status.offline
        m2.bot = False
        role.members = [m1, m2]
        guild.members = [m1, m2]
        guild.get_role.return_value = role
        self.bot.get_guild.return_value = guild
        self.bot.guilds = [guild]
        interaction.guild = guild

        await self.cog.dispatch_reengagement.callback(
            self.cog,
            interaction,
            dry_run=True,
        )

        interaction.followup.send.assert_awaited_once()
        output = interaction.followup.send.await_args.args[0]
        self.assertIn("DRY RUN", output)
        self.assertIn("QRV001", output)
        self.assertIn("A7-BAC", output)

    async def test_dispatch_wave_1_pairs_unique_pilot_per_aircraft(self):
        model = MagicMock()
        model.record_nudge = AsyncMock()

        ac1 = {
            "aircraft_id": "ac-1",
            "registration": "A7-BAC",
            "model": "B777",
            "origin_icao": "OTHH",
            "destination_icao": "EGLL",
            "schedule_id": "s1",
            "callsign": "QR001",
        }
        ac2 = {
            "aircraft_id": "ac-2",
            "registration": "A7-ALR",
            "model": "A350",
            "origin_icao": "OTHH",
            "destination_icao": "LFPG",
            "schedule_id": "s2",
            "callsign": "QR002",
        }

        m1 = MagicMock()
        m1.id = 111
        m1.status = discord.Status.online
        m1.bot = False

        m2 = MagicMock()
        m2.id = 222
        m2.status = discord.Status.idle
        m2.bot = False

        fleet_members = {"111": m1, "222": m2}
        candidates = [
            {"discordid": "111", "callsign": "QRV001", "pilot_id": 1},
            {"discordid": "222", "callsign": "QRV002", "pilot_id": 2},
        ]

        user_mock = MagicMock()
        msg_mock = MagicMock()
        msg_mock.channel.id = 999
        msg_mock.id = 888
        user_mock.send = AsyncMock(return_value=msg_mock)
        self.bot.get_user.return_value = user_mock

        with patch.object(self.cog, "_post_staff_report", new_callable=AsyncMock) as post_report:
            post_report.return_value = True
            result = await self.cog._dispatch_wave_1([ac1, ac2], candidates, fleet_members, model)

        self.assertTrue(result["success"])
        self.assertEqual(len(result["delivered"]), 2)
        self.assertIn("A7-BAC", self.cog._active_campaigns)
        self.assertIn("A7-ALR", self.cog._active_campaigns)
        self.assertEqual(self.cog._active_campaigns["A7-BAC"]["wave"], 1)
        self.assertEqual(self.cog._active_campaigns["A7-ALR"]["wave"], 1)

    async def test_followup_wave_triggers_after_1_hour_for_unclaimed_aircraft(self):
        model = MagicMock()
        model.get_inactive_pilot_candidates = AsyncMock(
            return_value=[
                {"discordid": "222", "callsign": "QRV002", "pilot_id": 2},
            ]
        )
        model.record_nudge = AsyncMock()

        manager = MagicMock()
        ac_mock = MagicMock()
        ac_mock.registration = "A7-BAC"
        ac_mock.aircraft_id = "ac-1"
        # Aircraft is STILL unassigned (crew == [])
        ac_mock.schedules = [{"id": "s1", "status": 1, "sequence": 1, "origin_icao": "OTHH", "destination_icao": "EGLL", "crew": []}]
        manager.get_live_fleet = AsyncMock(return_value=[ac_mock])

        self.bot.reengagement_model = model
        self.bot.if_live_manager = manager

        guild = MagicMock()
        role = MagicMock()
        m2 = MagicMock()
        m2.id = 222
        m2.status = discord.Status.online
        m2.bot = False
        role.members = [m2]
        guild.members = [m2]
        guild.get_role.return_value = role
        self.bot.get_guild.return_value = guild
        self.bot.guilds = [guild]

        user_mock = MagicMock()
        msg_mock = MagicMock()
        msg_mock.channel.id = 999
        msg_mock.id = 888
        user_mock.send = AsyncMock(return_value=msg_mock)
        self.bot.get_user.return_value = user_mock

        # Existing campaign set 65 minutes ago for Wave 1
        past_time = datetime.now(timezone.utc) - timedelta(minutes=65)
        self.cog._active_campaigns["A7-BAC"] = {
            "schedule_id": "s1",
            "wave": 1,
            "wave_sent_at": past_time,
            "contacted_pilot_ids": {"111"},
            "aircraft": {
                "registration": "A7-BAC",
                "schedule_id": "s1",
                "aircraft_id": "ac-1",
                "model": "B777",
                "origin_icao": "OTHH",
                "destination_icao": "EGLL",
                "callsign": "QR001",
            },
        }

        with patch.object(self.cog, "_post_staff_report", new_callable=AsyncMock) as post_report:
            post_report.return_value = True
            await self.cog._process_reengagement_cycle()

        # Follow-up Wave 2 should have been sent to pilot 222
        self.assertEqual(self.cog._active_campaigns["A7-BAC"]["wave"], 2)
        self.assertIn("222", self.cog._active_campaigns["A7-BAC"]["contacted_pilot_ids"])

    async def test_expire_aircraft_nudges_edits_messages_and_disables_buttons(self):
        model = MagicMock()
        model.get_active_nudges_for_registration = AsyncMock(
            return_value=[
                {"id": 1, "channel_id": "201", "message_id": "101"},
                {"id": 2, "channel_id": "202", "message_id": "102"},
            ]
        )
        model.mark_nudges_expired = AsyncMock()
        self.bot.reengagement_model = model

        channel_mock = MagicMock()
        msg_mock = MagicMock()
        old_embed = discord.Embed(title="Aircraft Departure", description="Ready now")
        msg_mock.embeds = [old_embed]
        msg_mock.edit = AsyncMock()
        channel_mock.fetch_message = AsyncMock(return_value=msg_mock)
        self.bot.get_channel.return_value = channel_mock

        # Call expire, excluding message 101 (e.g. the claimant's own message)
        count = await self.cog.expire_aircraft_nudges(
            registration="A7-BAC",
            reason="Flight claimed by another pilot",
            exclude_message_id="101",
        )

        self.assertEqual(count, 1)
        # Verify message 102 was edited
        msg_mock.edit.assert_awaited_once()
        edit_kwargs = msg_mock.edit.await_args.kwargs
        self.assertIn("embeds", edit_kwargs)
        self.assertIn("view", edit_kwargs)
        edited_embed = edit_kwargs["embeds"][0]
        self.assertIn("[UNAVAILABLE]", edited_embed.title)
        self.assertIn("Flight claimed by another pilot", edited_embed.description)

        # Verify database marked as expired for id 2
        model.mark_nudges_expired.assert_awaited_once_with([2])

    async def test_process_reengagement_cycle_expires_nudges_when_aircraft_departed(self):
        manager = MagicMock()
        # Fleet returns empty -> A7-BAC departed/claimed
        manager.get_live_fleet = AsyncMock(return_value=[])
        self.bot.if_live_manager = manager

        model = MagicMock()
        model.get_inactive_pilot_candidates = AsyncMock(return_value=[])
        self.bot.reengagement_model = model

        self.cog._active_campaigns["A7-BAC"] = {
            "schedule_id": "s1",
            "wave": 1,
            "wave_sent_at": datetime.now(timezone.utc),
            "contacted_pilot_ids": {"111"},
            "aircraft": {"registration": "A7-BAC", "schedule_id": "s1"},
        }

        with patch.object(self.cog, "expire_aircraft_nudges", new_callable=AsyncMock) as expire_mock:
            await self.cog._process_reengagement_cycle()

        self.assertNotIn("A7-BAC", self.cog._active_campaigns)
        expire_mock.assert_awaited_once()
        self.assertEqual(expire_mock.await_args.kwargs["registration"], "A7-BAC")


if __name__ == "__main__":
    unittest.main()

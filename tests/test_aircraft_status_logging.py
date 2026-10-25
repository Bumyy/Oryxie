import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import discord

from cogs.dm_logging import DM_LOG_CHANNEL_ID
from cogs.stopped_flight_monitor import (
    StoppedFlightMonitor,
    _aircraft_status_text,
    _status_log_line,
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
            "callsign": "QRV004",
            "destination_icao": "EGLL",
            "origin_icao": "OTHH",
        }

    def make_cog(self, channel):
        bot = MagicMock()
        bot.get_channel.return_value = channel
        bot.pilots_model.get_pilot_by_ifuserid = AsyncMock(
            return_value={"discordid": "123456789", "callsign": "QRV004"}
        )
        cog = StoppedFlightMonitor.__new__(StoppedFlightMonitor)
        cog.bot = bot
        cog._warned_status_logging_disabled = False
        cog._status_log_initialized = True
        return cog, bot

    def test_in_flight_status_includes_destination(self):
        text = _aircraft_status_text(
            MagicMock(),
            self.position,
            [self.flight],
        )

        self.assertEqual(text, "ENROUTE OTHH → EGLL by QRV004")

    def test_landing_and_parking_are_separate_status_transitions(self):
        landed = {
            "status": 11,
            "sequence": 1,
            "callsign": "QRV004",
            "destination_icao": "EGLL",
            "origin_icao": "OTHH",
        }
        position = {
            "state": 1,
            "is_on_ground": True,
            "latitude": 25.0,
            "longitude": 51.0,
        }

        landed_text = _aircraft_status_text(
            MagicMock(),
            position,
            [landed],
            previous_status="ENROUTE OTHH → EGLL by QRV004",
        )
        parked_text = _aircraft_status_text(
            MagicMock(),
            position,
            [landed],
            previous_status=landed_text,
        )

        self.assertEqual(
            landed_text,
            "LANDED AT EGLL · OTHH → EGLL by QRV004",
        )
        self.assertEqual(parked_text, "PARKED")

    async def test_enroute_transition_posts_and_persists_once(self):
        self.flight["crew"] = [{"userId": "if-user"}]
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
        self.assertIn(
            "Enroute, Route: OTHH -> EGLL by <@123456789> (QRV004)",
            channel.send.await_args.args[0],
        )
        self.assertNotIn("Aircraft status updates", channel.send.await_args.args[0])
        self.assertNotIn("✈️", channel.send.await_args.args[0])
        channel.send.assert_awaited_once()
        self.assertTrue(
            channel.send.await_args.kwargs["allowed_mentions"].users
        )
        bot.pilots_model.get_pilot_by_ifuserid.assert_awaited_once_with(
            "if-user"
        )
        self.assertEqual(
            model.updates,
            [("aircraft-1", "A7QRV", "ENROUTE OTHH → EGLL by QRV004")],
        )

    async def test_startup_poll_silently_replaces_stale_status_baseline(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        cog, _ = self.make_cog(channel)
        cog._status_log_initialized = False
        model = AircraftStatusModel({"aircraft-1": "PARKED AT OLD AIRPORT"})
        client = MagicMock()
        client.get_aircraft_position = AsyncMock(return_value=self.position)

        with patch(
            "cogs.stopped_flight_monitor.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await cog._log_fleet_status_changes(
                client,
                model,
                [self.aircraft],
                {"aircraft-1": [self.flight]},
            )

        channel.send.assert_not_awaited()
        self.assertEqual(
            model.statuses["aircraft-1"],
            "ENROUTE OTHH → EGLL by QRV004",
        )
        self.assertEqual(
            model.updates,
            [("aircraft-1", "A7QRV", "ENROUTE OTHH → EGLL by QRV004")],
        )
        self.assertTrue(cog._status_log_initialized)

    async def test_aircraft_missing_from_partial_startup_baseline_is_logged_later(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        cog, _ = self.make_cog(channel)
        cog._status_log_initialized = False
        model = AircraftStatusModel({})
        aircraft_ape = {"id": "aircraft-ape", "registration": "A7-APE"}
        position_available = False

        async def get_position(aircraft_id):
            if aircraft_id == "aircraft-ape" and not position_available:
                raise RuntimeError("Position temporarily unavailable")
            return self.position

        client = MagicMock()
        client.get_aircraft_position = AsyncMock(side_effect=get_position)
        fleet = [self.aircraft, aircraft_ape]

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
                fleet,
                {},
            )

            self.assertTrue(cog._status_log_initialized)
            channel.send.assert_not_awaited()

            position_available = True
            await cog._log_fleet_status_changes(
                client,
                model,
                fleet,
                {},
            )

        channel.send.assert_awaited_once()
        self.assertIn("**A7-APE**", channel.send.await_args.args[0])
        self.assertIn("Enroute", channel.send.await_args.args[0])
        self.assertIn("aircraft-ape", [update[0] for update in model.updates])

    def test_status_line_uses_route_and_pilot_mention_not_flight_number(self):
        landed_flight = {
            "status": 11,
            "origin_icao": "KJFK",
            "destination_icao": "KLAX",
            "callsign": "AA123",
        }
        pilot = {"discordid": "123456789", "callsign": "QRV004"}

        line = _status_log_line(
            "A7BFA",
            "LANDED KJFK → KLAX by AA123",
            landed_flight,
            pilot,
        )

        self.assertEqual(
            line,
            "• **A7BFA** — Landed at KLAX, Route: KJFK -> KLAX "
            "by <@123456789> (QRV004)",
        )

    def test_landed_airport_uses_observed_position_not_schedule_destination(self):
        bot = MagicMock()
        bot.flightdata.airports_db = {
            "KSFO": {"latitude_deg": 0, "longitude_deg": 0},
        }
        position = {
            "state": 1,
            "is_on_ground": True,
            "latitude": 0,
            "longitude": 0,
        }
        previous_status = "ENROUTE OTHH → KSFO by QRV136"
        stale_completed_schedule = {
            "status": 11,
            "sequence": 1,
            "origin_icao": "OTHH",
            "destination_icao": "OTHH",
            "callsign": "QRV135",
        }

        landed_status = _aircraft_status_text(
            bot,
            position,
            [stale_completed_schedule],
            previous_status=previous_status,
        )
        line = _status_log_line(
            "A7-ANN",
            landed_status,
            stale_completed_schedule,
            {"discordid": "123456789", "callsign": "QRV136"},
        )

        self.assertEqual(
            landed_status,
            "LANDED AT KSFO · OTHH → KSFO by QRV136",
        )
        self.assertEqual(
            line,
            "• **A7-ANN** — Landed at KSFO, Route: OTHH -> KSFO "
            "by <@123456789> (QRV136)",
        )

    def test_landing_at_airport_other_than_destination_is_marked_diverted(self):
        bot = MagicMock()
        bot.flightdata.airports_db = {
            "EGCC": {"latitude_deg": 0, "longitude_deg": 0},
        }
        position = {
            "state": 1,
            "is_on_ground": True,
            "latitude": 0,
            "longitude": 0,
        }
        route = {
            "status": 6,
            "sequence": 1,
            "origin_icao": "OTHH",
            "destination_icao": "YPPH",
            "callsign": "QRV226",
        }
        previous_status = "ENROUTE OTHH → YPPH by QRV226"

        landed_status = _aircraft_status_text(
            bot,
            position,
            [route],
            previous_status=previous_status,
        )
        line = _status_log_line(
            "A7BAF",
            landed_status,
            route,
            {"discordid": "123456789", "callsign": "QRV226"},
        )

        self.assertEqual(
            landed_status,
            "LANDED AT EGCC (DIVERTED) · OTHH → YPPH by QRV226",
        )
        self.assertEqual(
            line,
            "• **A7BAF** — Landed at EGCC (DIVERTED), Route: "
            "OTHH -> YPPH by <@123456789> (QRV226)",
        )

    def test_parked_status_does_not_repeat_the_at_prefix(self):
        for status_text in ("PARKED AT ORNI", "PARKED AT AT KSFO"):
            with self.subTest(status_text=status_text):
                self.assertEqual(
                    _status_log_line(
                        "A7ADS",
                        status_text,
                        None,
                        {"discordid": "123456789", "callsign": "QRV100"},
                    ),
                    (
                        "• **A7ADS** — Parked at ORNI by <@123456789> (QRV100)"
                        if status_text == "PARKED AT ORNI"
                        else "• **A7ADS** — Parked at KSFO by "
                        "<@123456789> (QRV100)"
                    ),
                )

    async def test_unchanged_status_does_not_post_again(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        cog, _ = self.make_cog(channel)
        model = AircraftStatusModel(
            {"aircraft-1": "ENROUTE OTHH → EGLL by QRV004"}
        )
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

    async def test_stopped_flight_dm_report_includes_selection_diagnostics(self):
        cog, _ = self.make_cog(MagicMock())
        report_poster = AsyncMock(return_value=True)
        row = {
            "schedule_id": "schedule-1",
            "registration": "A7QRV",
            "callsign": "QRV004",
            "origin_icao": "OTHH",
            "destination_icao": "EGLL",
        }

        with patch(
            "cogs.stopped_flight_monitor.post_dm_report",
            report_poster,
        ):
            await cog._send_dm_recipient_report(
                row,
                "1-hour recovery alert",
                [],
                notes=[
                    "Liam_Edris (QRV042): no Live flights in the 3-day activity window."
                ],
            )

        report_content = report_poster.await_args.args[1]
        self.assertIn("Selection / delivery notes", report_content)
        self.assertIn("Liam_Edris (QRV042)", report_content)
        self.assertIn("3-day activity window", report_content)

    async def test_one_hour_keeps_executive_assigned_and_sends_warning(self):
        cog, bot = self.make_cog(MagicMock())
        executive_role = MagicMock()
        executive_member = MagicMock()
        executive_member.roles = [executive_role]
        guild = MagicMock()
        guild.get_role.return_value = executive_role
        guild.get_member.side_effect = lambda discord_id: (
            executive_member if discord_id == 123456789 else None
        )
        guild.fetch_member = AsyncMock(return_value=None)
        bot.get_guild.return_value = guild
        bot.pilots_model.get_pilot_by_ifuserid = AsyncMock(
            side_effect=lambda if_user_id: {
                "ifuserid": "if-executive",
                "discordid": "123456789",
                "callsign": "QRV001",
            }
            if if_user_id == "if-executive"
            else {"ifuserid": if_user_id, "discordid": "987654321"}
        )

        row = {
            "schedule_id": "schedule-1",
            "aircraft_id": "aircraft-1",
            "registration": "A7BAB",
            "callsign": "QR1302",
            "origin_icao": "HECA",
            "destination_icao": "OTHH",
            "original_crew_json": '["if-executive", "if-normal"]',
        }
        model = MagicMock()
        model.update_incident = AsyncMock()
        model.get_incident = AsyncMock(return_value=row)
        client = MagicMock()
        client.remove_crew = AsyncMock()
        client.list_aircraft_schedules = AsyncMock(
            return_value=[
                {
                    "id": "schedule-1",
                    "crew": [{"userId": "if-executive"}],
                }
            ]
        )

        with (
            patch(
                "cogs.stopped_flight_monitor.EXECUTIVE_ROLE_ID",
                1090751678564810913,
            ),
            patch.object(
                cog,
                "_notify_unassigned_pilot",
                new_callable=AsyncMock,
                return_value="<@987654321> (QRV002)",
            ) as removal_dm,
            patch.object(
                cog,
                "_notify_executive_pilot",
                new_callable=AsyncMock,
                return_value="<@123456789> (QRV001)",
            ) as executive_dm,
            patch.object(
                cog,
                "_send_dm_recipient_report",
                new_callable=AsyncMock,
                return_value=True,
            ) as report,
            patch.object(
                cog,
                "_send_pilot_wave",
                new_callable=AsyncMock,
            ) as recovery_wave,
        ):
            await cog._unassign_and_alert(
                client,
                model,
                {"id": "schedule-1", "crew": [
                    {"userId": "if-executive"},
                    {"userId": "if-normal"},
                ]},
                row,
            )

        client.remove_crew.assert_awaited_once_with("schedule-1", "if-normal")
        removal_dm.assert_awaited_once_with(
            model, row, "if-normal", ANY
        )
        executive_dm.assert_awaited_once_with(
            model, row, "if-executive", ANY
        )
        self.assertEqual(
            report.await_args_list[-1].args[1],
            "1-hour Executive warning",
        )
        recovery_wave.assert_not_awaited()

    async def test_public_recovery_offer_pings_opt_in_role_and_persists_message(self):
        channel = MagicMock()
        channel.id = 444
        message = MagicMock()
        message.id = 555
        channel.send = AsyncMock(return_value=message)
        cog, bot = self.make_cog(channel)
        cog._registered_views = set()
        bot.add_view = MagicMock()
        role = MagicMock()
        role.mention = "<@&321>"
        guild = MagicMock()
        guild.get_role.return_value = role
        bot.get_guild.return_value = guild
        row = {
            "schedule_id": "schedule-1",
            "registration": "A7BAB",
            "callsign": "QR1302",
            "origin_icao": "HECA",
            "destination_icao": "OTHH",
        }
        model = MagicMock()
        model.update_incident = AsyncMock()

        with (
            patch(
                "cogs.stopped_flight_monitor.RECOVERY_ALERT_CHANNEL_ID",
                444,
            ),
            patch(
                "cogs.stopped_flight_monitor.RECOVERY_PING_ROLE_ID",
                321,
            ),
        ):
            posted = await cog._send_public_recovery_offer(model, row)

        self.assertTrue(posted)
        content = channel.send.await_args.args[0]
        self.assertIn("<@&321>", content)
        self.assertIn("A7BAB · QR1302", content)
        self.assertIn("Any Fleet pilot may claim", content)
        self.assertEqual(
            model.update_incident.await_args.args[1],
            {"alert_channel_id": "444", "alert_message_id": "555"},
        )
        self.assertEqual(
            channel.send.await_args.kwargs["allowed_mentions"].roles,
            [role],
        )

    async def test_one_hour_posts_public_offer_without_starting_dm_wave(self):
        cog, bot = self.make_cog(MagicMock())
        stopped_since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            hours=1,
            minutes=5,
        )
        row = {
            "schedule_id": "schedule-1",
            "aircraft_id": "aircraft-1",
            "registration": "A7BAB",
            "callsign": "QR1302",
            "origin_icao": "HECA",
            "destination_icao": "OTHH",
            "original_crew_json": "[]",
            "stopped_since": stopped_since,
            "lifecycle": "available",
            "eta_warning_sent": True,
            "one_hour_sent": False,
            "two_hour_sent": False,
            "three_hour_sent": False,
            "four_hour_sent": False,
        }
        model = MagicMock()
        model.get_incident = AsyncMock(return_value=row)
        model.update_incident = AsyncMock()
        client = MagicMock()
        schedule = {
            "id": "schedule-1",
            "aircraft_id": "aircraft-1",
            "status": 6,
            "crew": [],
            "origin_icao": "HECA",
            "destination_icao": "OTHH",
        }
        position = {"state": 4, "is_on_ground": False}

        with (
            patch.object(
                cog,
                "_send_public_recovery_offer",
                new_callable=AsyncMock,
                return_value=True,
            ) as public_offer,
            patch.object(
                cog,
                "_send_pilot_wave",
                new_callable=AsyncMock,
            ) as recovery_wave,
        ):
            await cog._process_schedule(
                client,
                model,
                schedule,
                position,
                "A7BAB",
            )

        public_offer.assert_awaited_once_with(model, row)
        recovery_wave.assert_not_awaited()
        self.assertTrue(
            any(
                call.args[1] == {"one_hour_sent": 1}
                for call in model.update_incident.await_args_list
            )
        )

    async def test_recovery_role_reaction_adds_role_only_on_configured_panel(self):
        cog, bot = self.make_cog(MagicMock())
        bot.user = None
        member = MagicMock()
        member.bot = False
        member.add_roles = AsyncMock()
        payload = MagicMock()
        payload.user_id = 123
        payload.guild_id = 456
        payload.message_id = 789
        payload.emoji.id = 987
        payload.member = member
        role = MagicMock()
        guild = MagicMock()
        guild.get_role.return_value = role
        guild.fetch_member = AsyncMock(return_value=member)
        bot.get_guild.return_value = guild
        model = MagicMock()
        model.get_recovery_role_panel = AsyncMock(
            return_value={
                "guild_id": "456",
                "message_id": "789",
                "emoji_id": "987",
            }
        )
        bot.stopped_flight_model = model

        with patch("cogs.stopped_flight_monitor.RECOVERY_PING_ROLE_ID", 321):
            await cog.on_raw_reaction_add(payload)

        member.add_roles.assert_awaited_once_with(
            role,
            reason="Opted in to stopped-flight recovery alerts",
        )

        payload.message_id = 790
        await cog.on_raw_reaction_add(payload)
        member.add_roles.assert_awaited_once()

        payload.message_id = 789
        member.remove_roles = AsyncMock()
        await cog.on_raw_reaction_remove(payload)
        member.remove_roles.assert_awaited_once_with(
            role,
            reason="Opted out of stopped-flight recovery alerts",
        )

    async def test_fleet_pilot_can_claim_open_slot_with_executive_retained(self):
        cog, bot = self.make_cog(MagicMock())
        fleet_role = MagicMock()
        member = MagicMock()
        member.id = 987654321
        member.roles = [fleet_role]
        guild = MagicMock()
        guild.get_role.return_value = fleet_role
        interaction = MagicMock()
        interaction.guild = guild
        interaction.user = member
        interaction.response.send_message = AsyncMock()

        incident = {
            "schedule_id": "schedule-1",
            "aircraft_id": "aircraft-1",
            "registration": "A7BAB",
            "callsign": "QR1302",
        }
        model = MagicMock()
        model.try_claim = AsyncMock(return_value=True)
        model.get_incident = AsyncMock(return_value=incident)
        model.confirm_claim = AsyncMock()
        bot.stopped_flight_model = model
        bot.pilots_model.get_pilot_by_discord_id = AsyncMock(
            return_value={"ifuserid": "if-recovery"}
        )

        assigned_schedule = {
            "id": "schedule-1",
            "status": 6,
            "crew": [
                {"userId": "if-executive", "role": 0},
                {"userId": "if-recovery", "role": 1},
            ],
        }
        client = MagicMock()
        client.get_aircraft_position = AsyncMock(
            return_value={"is_on_ground": False, "state": 4}
        )
        client.list_aircraft_schedules = AsyncMock(
            return_value=[
                {
                    "id": "schedule-1",
                    "status": 6,
                    "crew": [{"userId": "if-executive", "role": 0}],
                }
            ]
        )
        client.assign_crew = AsyncMock(return_value=assigned_schedule)
        client_context = MagicMock()
        client_context.__aenter__ = AsyncMock(return_value=client)
        client_context.__aexit__ = AsyncMock(return_value=False)
        manager = MagicMock()
        manager.get_client = AsyncMock(return_value=client_context)
        bot.if_live_manager = manager

        with (
            patch("cogs.stopped_flight_monitor.FLEET_ROLE_ID", 42),
            patch.object(
                cog,
                "_expire_claim_messages",
                new_callable=AsyncMock,
            ) as expire_messages,
        ):
            await cog.claim_stopped_flight(interaction, "schedule-1")

        client.assign_crew.assert_awaited_once_with(
            "schedule-1", "if-recovery", 1
        )
        model.confirm_claim.assert_awaited_once_with(
            "schedule-1", str(member.id)
        )
        self.assertIn(
            f"claimed by <@{member.id}> (Pilot)",
            expire_messages.await_args.args[2],
        )

    async def test_eta_warning_uses_aircraft_registration_not_flight_number(self):
        cog, bot = self.make_cog(MagicMock())
        user = MagicMock()
        user.send = AsyncMock()
        bot.get_user.return_value = user
        row = {
            "schedule_id": "schedule-1",
            "registration": "A7BAB",
            "callsign": "XXX1302",
            "origin_icao": "HECA",
            "destination_icao": "OTHH",
        }

        with patch(
            "cogs.stopped_flight_monitor.post_dm_report",
            new_callable=AsyncMock,
        ):
            sent = await cog._send_eta_warning(row, ["if-user-1"], 25)

        self.assertTrue(sent)
        message = user.send.await_args.args[0]
        self.assertIn("A7BAB is within about 30 minutes of OTHH", message)
        self.assertNotIn("XXX1302", message)

    async def test_eta_warning_attempt_does_not_abort_schedule_processing(self):
        cog, bot = self.make_cog(MagicMock())
        row = {
            "schedule_id": "schedule-1",
            "aircraft_id": "aircraft-1",
            "registration": "A7BAB",
            "callsign": "QR1302",
            "origin_icao": "HECA",
            "destination_icao": "OTHH",
            "original_crew_json": '["if-user-1"]',
            "stopped_since": None,
            "lifecycle": "monitoring",
            "eta_warning_sent": False,
            "eta_warning_attempts": 1,
            "one_hour_sent": False,
            "two_hour_sent": False,
            "three_hour_sent": False,
            "four_hour_sent": False,
        }
        model = MagicMock()
        model.get_incident = AsyncMock(return_value=row)
        model.update_incident = AsyncMock()
        model.try_start_eta_warning_attempt = AsyncMock(return_value=True)
        client = MagicMock()
        cog._send_eta_warning = AsyncMock(return_value=True)
        schedule = {
            "id": "schedule-1",
            "aircraft_id": "aircraft-1",
            "status": 6,
            "callsign": "QR1302",
            "origin_icao": "HECA",
            "destination_icao": "OTHH",
            "crew": [{"userId": "if-user-1"}],
        }
        position = {
            "state": 2,
            "is_on_ground": False,
        }

        with patch(
            "cogs.stopped_flight_monitor._eta_minutes",
            return_value=25,
        ):
            await cog._process_schedule(
                client,
                model,
                schedule,
                position,
                "A7BAB",
            )

        cog._send_eta_warning.assert_awaited_once_with(
            row,
            ["if-user-1"],
            25,
        )
        self.assertTrue(
            any(
                call.args[1] == {"eta_warning_sent": 1}
                for call in model.update_incident.await_args_list
            )
        )

    async def test_recovery_wave_returns_false_when_staff_report_fails(self):
        cog, bot = self.make_cog(MagicMock())
        cog._activity_cache = {}
        role = MagicMock()
        role.members = []
        guild = MagicMock()
        guild.get_role.return_value = role
        bot.get_guild.return_value = guild
        model = MagicMock()
        model.get_dm_events = AsyncMock(return_value=[])
        model.get_active_pilots = AsyncMock(return_value=[])
        report_poster = AsyncMock(return_value=False)
        row = {
            "schedule_id": "schedule-1",
            "registration": "A7BAB",
            "callsign": "XXX1302",
            "origin_icao": "HECA",
            "destination_icao": "OTHH",
        }

        with patch(
            "cogs.stopped_flight_monitor.post_dm_report",
            report_poster,
        ):
            report_posted = await cog._send_pilot_wave(model, row, wave=2)

        self.assertFalse(report_posted)
        report_poster.assert_awaited_once()

    async def test_recovery_wave_dm_explains_half_split_flight_hours(self):
        cog, bot = self.make_cog(MagicMock())
        now = datetime.now()
        member = MagicMock()
        member.id = 123456789
        member.bot = False
        member.display_name = "Recovery Pilot"
        member.nick = None
        message = MagicMock()
        message.channel.id = 123
        message.id = 456
        member.send = AsyncMock(return_value=message)
        role = MagicMock()
        role.members = [member]
        guild = MagicMock()
        guild.get_role.return_value = role
        bot.get_guild.return_value = guild
        bot.add_view = MagicMock()
        cog._registered_views = set()
        cog._activity_cache = {"if-user-1": (now - timedelta(minutes=1), 1)}
        model = MagicMock()
        model.get_dm_events = AsyncMock(return_value=[])
        model.get_active_pilots = AsyncMock(
            return_value=[
                {
                    "callsign": "QRV042",
                    "discordid": str(member.id),
                    "ifuserid": "if-user-1",
                }
            ]
        )
        model.get_incident_state = AsyncMock(
            return_value={"lifecycle": "available"}
        )
        model.record_dm_event = AsyncMock()
        row = {
            "schedule_id": "schedule-1",
            "registration": "A7BAB",
            "callsign": "QR1302",
            "origin_icao": "HECA",
            "destination_icao": "OTHH",
        }

        with patch(
            "cogs.stopped_flight_monitor.post_dm_report",
            new_callable=AsyncMock,
            return_value=True,
        ):
            report_posted = await cog._send_pilot_wave(model, row, wave=1)

        self.assertTrue(report_posted)
        message_text = member.send.await_args.args[0]
        self.assertIn(
            "Flight hours for this flight will be split 50/50 between you "
            "and the original assigned pilot.",
            message_text,
        )

    async def test_recovery_wave_contacts_no_more_than_five_fleet_pilots(self):
        cog, bot = self.make_cog(MagicMock())
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        members = []
        pilots = []
        for index in range(6):
            member = MagicMock()
            member.id = 100 + index
            member.bot = False
            member.display_name = f"Pilot {index}"
            member.nick = None
            message = MagicMock()
            message.channel.id = 123
            message.id = 456 + index
            member.send = AsyncMock(return_value=message)
            members.append(member)
            pilots.append(
                {
                    "callsign": f"QRV{index:03}",
                    "discordid": str(member.id),
                    "ifuserid": f"if-user-{index}",
                }
            )
        role = MagicMock()
        role.members = members
        guild = MagicMock()
        guild.get_role.return_value = role
        bot.get_guild.return_value = guild
        bot.add_view = MagicMock()
        cog._registered_views = set()
        cog._activity_cache = {
            pilot["ifuserid"]: (now - timedelta(minutes=1), 1)
            for pilot in pilots
        }
        model = MagicMock()
        model.get_dm_events = AsyncMock(return_value=[])
        model.get_active_pilots = AsyncMock(return_value=pilots)
        model.get_incident_state = AsyncMock(
            return_value={"lifecycle": "available"}
        )
        model.record_dm_event = AsyncMock()
        row = {
            "schedule_id": "schedule-1",
            "registration": "A7BAB",
            "callsign": "QR1302",
            "origin_icao": "HECA",
            "destination_icao": "OTHH",
            "original_crew_json": "[]",
        }

        with patch(
            "cogs.stopped_flight_monitor.post_dm_report",
            new_callable=AsyncMock,
            return_value=True,
        ):
            await cog._send_pilot_wave(model, row, wave=2)

        self.assertEqual(sum(member.send.await_count for member in members), 5)

    async def test_crew_removal_dm_explains_next_leg_continuation(self):
        cog, bot = self.make_cog(MagicMock())
        user = MagicMock()
        message = MagicMock()
        message.channel.id = 123
        message.id = 456
        user.send = AsyncMock(return_value=message)
        bot.get_user.return_value = user
        model = MagicMock()
        model.record_dm_event = AsyncMock()
        row = {
            "schedule_id": "schedule-1",
            "registration": "A7BAB",
            "callsign": "QR1302",
            "origin_icao": "HECA",
            "destination_icao": "OTHH",
        }

        recipient = await cog._notify_unassigned_pilot(
            model,
            row,
            "if-user-1",
        )

        self.assertEqual(recipient, "<@123456789> (QRV004)")
        message_text = user.send.await_args.args[0]
        self.assertIn(
            "Another pilot will land the aircraft, and you can continue "
            "the next leg if it is booked.",
            message_text,
        )
        self.assertIn(
            "Flight hours for this flight will be split 50/50 between you "
            "and the recovery pilot.",
            message_text,
        )
        self.assertNotIn("Live Admin", message_text)


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

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

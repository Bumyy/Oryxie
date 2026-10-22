"""Monitor stopped-in-air assignments and offer them to the fleet."""

import asyncio
import json
import logging
import math
import os
import random
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

log = logging.getLogger(__name__)

ORG_ID = os.getenv("IF_ORGANIZATION_ID", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
STAFF_CHANNEL_ID = int(os.getenv("STOPPED_FLIGHT_STAFF_CHANNEL_ID", "0") or 0)
DM_LOG_CHANNEL_ID = int(
    os.getenv("STOPPED_FLIGHT_DM_LOG_CHANNEL_ID", "0") or 0
)
STATUS_LOG_CHANNEL_ID = int(
    os.getenv("AIRCRAFT_STATUS_LOG_CHANNEL_ID", "0") or 0
)
FLEET_ROLE_ID = int(os.getenv("STOPPED_FLIGHT_FLEET_ROLE_ID", "0") or 0)
TEST_PILOT_DISCORD_ID = os.getenv(
    "STOPPED_FLIGHT_TEST_PILOT_DISCORD_ID",
    "1277609750191800334",
).strip()
ROUTE_EXHAUSTION_CHANNEL_ID = int(
    os.getenv("ROUTE_EXHAUSTION_CHANNEL_ID", "0") or 0
)
ROUTE_EXHAUSTION_ROLE_ID = int(
    os.getenv("ROUTE_EXHAUSTION_ROLE_ID", "0") or 0
)
TOKEN_OWNER_ID = 1
POLL_INTERVAL_MINUTES = 2
STOPPED_POSITION_STATE = 4
IN_FLIGHT_SCHEDULE_STATUS = 6
FLIGHT_HISTORY_DAYS = 3
DEMAND_HISTORY_DAYS = 7
HIGH_DEMAND_FLIGHT_THRESHOLD = 3
MAX_DEMAND_HISTORY_PAGES = 5
DM_BATCH_SIZE = 5
FLIGHT_HISTORY_PAGE_SIZE = 25
MAX_FLIGHT_HISTORY_PAGES = 20
ACTIVITY_CACHE_SECONDS = 15 * 60


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _status(schedule):
    try:
        return int(_get(schedule, "status"))
    except (TypeError, ValueError):
        return None


def _sequence(schedule):
    try:
        return int(_get(schedule, "sequence", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _crew_members(schedule):
    return list(_get(schedule, "crew", []) or [])


def _crew_user_id(member):
    value = _get(member, "userId") or _get(member, "user_id")
    return str(value).strip() if value else ""


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_utc(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except ValueError:
        return None


def _distance_nm(lat1, lon1, lat2, lon2):
    radius_nm = 3440.065
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    value = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * radius_nm * math.asin(math.sqrt(min(1.0, value)))


def _eta_minutes(bot, position, destination_icao):
    airports = getattr(getattr(bot, "flightdata", None), "airports_db", None)
    if not airports:
        return None
    airport = airports.get(str(destination_icao or "").upper())
    if not airport:
        return None
    try:
        distance = _distance_nm(
            float(_get(position, "latitude")),
            float(_get(position, "longitude")),
            float(airport["latitude_deg"]),
            float(airport["longitude_deg"]),
        )
        speed = float(_get(position, "speed"))
    except (KeyError, TypeError, ValueError):
        return None
    if speed <= 30:
        return None
    return distance * 60 / speed


def _nearest_airport_icao(bot, position):
    airports = getattr(getattr(bot, "flightdata", None), "airports_db", None)
    if not airports:
        return None
    try:
        latitude = float(_get(position, "latitude"))
        longitude = float(_get(position, "longitude"))
    except (TypeError, ValueError):
        return None

    nearest_icao = None
    nearest_distance = float("inf")
    for icao, airport in airports.items():
        try:
            distance = _distance_nm(
                latitude,
                longitude,
                float(airport["latitude_deg"]),
                float(airport["longitude_deg"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if distance < nearest_distance:
            nearest_distance = distance
            nearest_icao = str(icao).upper()

    return nearest_icao if nearest_distance <= 25 else None


def _aircraft_status_text(bot, position, schedules):
    try:
        state = int(_get(position, "state", -1))
    except (TypeError, ValueError):
        state = -1
    on_ground = bool(_get(position, "is_on_ground", False))
    active_schedules = sorted(
        (
            schedule
            for schedule in schedules or []
            if _status(schedule) not in {8, 9, 11}
        ),
        key=_sequence,
    )
    current_flight = next(
        (schedule for schedule in active_schedules if _status(schedule) == 6),
        None,
    )
    taxiing_flight = next(
        (schedule for schedule in active_schedules if _status(schedule) == 10),
        None,
    )
    destination = str(
        _get(current_flight, "destination_icao", "") or ""
    ).strip().upper()
    taxiing_destination = str(
        _get(taxiing_flight, "destination_icao", "") or ""
    ).strip().upper()

    if state == 5:
        return "MAINTENANCE"
    if state == STOPPED_POSITION_STATE and not on_ground:
        return f"STOPPED IN AIR · ENROUTE TO {destination}" if destination else "STOPPED IN AIR"
    if taxiing_flight:
        return (
            f"TAXIING TO {taxiing_destination}"
            if taxiing_destination
            else "TAXIING"
        )
    if not on_ground or state == 2:
        return f"ENROUTE TO {destination}" if destination else "ENROUTE"

    airport = _nearest_airport_icao(bot, position)
    if airport:
        return f"PARKED AT {airport}"
    if active_schedules:
        origin = str(
            _get(active_schedules[0], "origin_icao", "") or ""
        ).strip().upper()
        if origin:
            return f"PARKED AT {origin}"
    return "PARKED · LOCATION UNKNOWN"


def _weight_pick(candidates, count):
    """Weighted sampling without replacement, proportional to recent flights."""
    remaining = list(candidates)
    selected = []
    while remaining and len(selected) < count:
        winner = random.choices(
            remaining,
            weights=[max(1, item["flight_count"]) for item in remaining],
            k=1,
        )[0]
        selected.append(winner)
        remaining.remove(winner)
    return selected


class StoppedFlightClaimView(discord.ui.View):
    def __init__(self, cog, schedule_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.schedule_id = str(schedule_id)
        button = discord.ui.Button(
            label="Claim this flight",
            style=discord.ButtonStyle.success,
            emoji="✈️",
            custom_id=f"stopped-flight:claim:{self.schedule_id}",
        )
        button.callback = self.claim
        self.add_item(button)

    async def claim(self, interaction):
        await self.cog.claim_stopped_flight(interaction, self.schedule_id)


class StoppedFlightMonitor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._registered_views = set()
        self._activity_cache = {}
        self._warned_status_logging_disabled = False
        self.monitor_stopped_flights.start()

    def cog_unload(self):
        self.monitor_stopped_flights.cancel()

    @tasks.loop(minutes=POLL_INTERVAL_MINUTES)
    async def monitor_stopped_flights(self):
        if not ORG_ID:
            log.error("IF_ORGANIZATION_ID is not configured; stopped-flight monitoring is disabled.")
            return
        if not all((GUILD_ID, STAFF_CHANNEL_ID, DM_LOG_CHANNEL_ID, FLEET_ROLE_ID)):
            log.error(
                "Stopped-flight monitoring requires guild, staff, DM-log, "
                "and fleet-role IDs."
            )
            return

        model = getattr(self.bot, "stopped_flight_model", None)
        manager = getattr(self.bot, "if_live_manager", None)
        if model is None or manager is None:
            log.error("Stopped-flight monitoring requires database and Live API services.")
            return

        try:
            await model.ensure_tables()
            await self._register_active_views(model)
            async with await manager.get_client(TOKEN_OWNER_ID) as client:
                schedules = await self._get_organization_schedules(client)
                fleet = await client.list_organization_aircraft(ORG_ID)
                registrations = {
                    str(_get(aircraft, "id")): str(
                        _get(aircraft, "registration") or "Unknown aircraft"
                    )
                    for aircraft in fleet or []
                }
                schedules_by_aircraft = {}
                for schedule in schedules:
                    aircraft_id = str(_get(schedule, "aircraft_id") or "")
                    if aircraft_id:
                        schedules_by_aircraft.setdefault(aircraft_id, []).append(
                            schedule
                        )
                for aircraft_id, registration in registrations.items():
                    has_active_routes = any(
                        _status(schedule) not in {8, 9, 11}
                        for schedule in schedules_by_aircraft.get(aircraft_id, [])
                    )
                    await model.sync_route_availability(
                        aircraft_id,
                        registration,
                        has_active_routes,
                    )
                await self._send_route_exhaustion_alerts(client, model)
                positions = await self._log_fleet_status_changes(
                    client,
                    model,
                    fleet,
                    schedules_by_aircraft,
                )

                active = [
                    schedule
                    for schedule in schedules
                    if _status(schedule) == IN_FLIGHT_SCHEDULE_STATUS
                ]
                active_ids = {str(_get(schedule, "id")) for schedule in active}
                for schedule in active:
                    aircraft_id = str(_get(schedule, "aircraft_id") or "")
                    schedule_id = str(_get(schedule, "id") or "")
                    if not aircraft_id or not schedule_id:
                        continue
                    if not _crew_members(schedule):
                        existing = await model.get_incident(schedule_id)
                        if not existing:
                            continue
                    try:
                        position = positions.get(aircraft_id)
                        if position is None:
                            position = await client.get_aircraft_position(aircraft_id)
                        await self._process_schedule(
                            client,
                            model,
                            schedule,
                            position,
                            registrations.get(aircraft_id, "Unknown aircraft"),
                        )
                    except Exception:
                        log.exception(
                            "Stopped-flight monitoring failed for schedule %s",
                            schedule_id,
                        )
                await self._close_inactive_incidents(model, active_ids)
        except Exception:
            log.exception("Stopped-flight monitoring cycle failed.")

    @monitor_stopped_flights.before_loop
    async def before_monitor_stopped_flights(self):
        await self.bot.wait_until_ready()

    async def _send_route_exhaustion_alerts(self, client, model):
        if not ROUTE_EXHAUSTION_CHANNEL_ID or not ROUTE_EXHAUSTION_ROLE_ID:
            log.error(
                "Route exhaustion alerts require channel and role IDs."
            )
            return
        pending = await model.list_pending_route_exhaustion_alerts()
        for aircraft in pending:
            try:
                try:
                    recent_flights = await self._recent_aircraft_flight_count(
                        client,
                        str(aircraft["aircraft_id"]),
                    )
                    if recent_flights is None:
                        demand_label = "Demand unknown"
                    else:
                        demand_level = (
                            "High"
                            if recent_flights >= HIGH_DEMAND_FLIGHT_THRESHOLD
                            else "Low"
                        )
                        flight_word = "flight" if recent_flights == 1 else "flights"
                        demand_label = (
                            f"{demand_level} Demand · {recent_flights} "
                            f"{flight_word} in 7 days"
                        )
                except Exception:
                    log.exception(
                        "Could not determine recent demand for aircraft %s",
                        aircraft["aircraft_id"],
                    )
                    demand_label = "Demand unknown"

                channel = self.bot.get_channel(ROUTE_EXHAUSTION_CHANNEL_ID)
                if channel is None:
                    channel = await self.bot.fetch_channel(
                        ROUTE_EXHAUSTION_CHANNEL_ID
                    )
                await channel.send(
                    f"<@&{ROUTE_EXHAUSTION_ROLE_ID}> **Scheduled routes finished "
                    f"for {aircraft['registration']} · ({demand_label}).** "
                    "Please add more routes.",
                    allowed_mentions=discord.AllowedMentions(roles=True),
                )
                await model.mark_route_exhaustion_alert_sent(
                    aircraft["aircraft_id"]
                )
            except (discord.Forbidden, discord.HTTPException):
                log.exception(
                    "Could not post route exhaustion alert for aircraft %s",
                    aircraft["aircraft_id"],
                )

    async def _recent_aircraft_flight_count(self, client, aircraft_id):
        cutoff = _utcnow() - timedelta(days=DEMAND_HISTORY_DAYS)
        count = 0
        for page_number in range(1, MAX_DEMAND_HISTORY_PAGES + 1):
            page = await client.list_aircraft_flights(
                aircraft_id,
                page=page_number,
                page_size=FLIGHT_HISTORY_PAGE_SIZE,
            )
            flights = _get(page, "items")
            if not isinstance(flights, list):
                raise RuntimeError(
                    f"Infinite Flight returned invalid flight history for {aircraft_id}"
                )
            if not flights:
                return count

            older_flight_found = False
            for flight in flights:
                created = _parse_utc(
                    _get(flight, "created") or _get(flight, "createdAt")
                )
                if created is None:
                    continue
                if created < cutoff:
                    older_flight_found = True
                    continue
                if float(_get(flight, "totalTime", 0) or 0) > 0:
                    count += 1
                    if count >= HIGH_DEMAND_FLIGHT_THRESHOLD:
                        return count

            if older_flight_found or page_number >= int(
                _get(page, "total_pages", 0) or 0
            ):
                return count

        raise RuntimeError(
            f"Could not determine seven-day flight count for aircraft {aircraft_id} "
            f"within {MAX_DEMAND_HISTORY_PAGES} pages"
        )

    async def _log_fleet_status_changes(
        self,
        client,
        model,
        fleet,
        schedules_by_aircraft,
    ):
        if not STATUS_LOG_CHANNEL_ID:
            if not self._warned_status_logging_disabled:
                log.warning(
                    "Aircraft status logging is disabled; set "
                    "AIRCRAFT_STATUS_LOG_CHANNEL_ID to enable it."
                )
                self._warned_status_logging_disabled = True
            return {}

        if not await model.has_aircraft_status_table():
            log.error(
                "Aircraft status logging is disabled because "
                "if_aircraft_status_logs is missing; apply "
                "database/stopped_flight_monitor_mysql_schema.sql."
            )
            return {}

        previous_statuses = await model.get_aircraft_statuses()
        current_statuses = {}
        positions = {}
        for aircraft in fleet or []:
            aircraft_id = str(_get(aircraft, "id") or "")
            if not aircraft_id:
                continue
            try:
                position = await client.get_aircraft_position(aircraft_id)
            except Exception:
                log.exception(
                    "Could not load position for aircraft status log %s",
                    aircraft_id,
                )
                continue
            positions[aircraft_id] = position
            registration = str(
                _get(aircraft, "registration") or "Unknown aircraft"
            )
            current_statuses[aircraft_id] = (
                registration,
                _aircraft_status_text(
                    self.bot,
                    position,
                    schedules_by_aircraft.get(aircraft_id, []),
                ),
            )

        changed = [
            (aircraft_id, registration, status_text)
            for aircraft_id, (registration, status_text) in current_statuses.items()
            if previous_statuses.get(aircraft_id) != status_text
        ]
        if not changed:
            return positions

        channel = self.bot.get_channel(STATUS_LOG_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(STATUS_LOG_CHANNEL_ID)
            except (discord.Forbidden, discord.HTTPException):
                log.exception(
                    "Could not access aircraft status log channel %s",
                    STATUS_LOG_CHANNEL_ID,
                )
                return positions
        header = "✈️ **Aircraft status updates**"
        chunks = []
        current_entries = []
        current_length = len(header)
        for entry in changed:
            aircraft_id, registration, status_text = entry
            line = f"• **{registration}** — {status_text}"
            if current_entries and current_length + len(line) + 1 > 1800:
                chunks.append(current_entries)
                current_entries = []
                current_length = len(header)
            current_entries.append(entry)
            current_length += len(line) + 1
        if current_entries:
            chunks.append(current_entries)

        for chunk in chunks:
            content = "\n".join(
                [header]
                + [
                    f"• **{registration}** — {status_text}"
                    for _, registration, status_text in chunk
                ]
            )
            try:
                await channel.send(content)
            except discord.HTTPException:
                log.exception("Could not post aircraft status updates.")
                break
            for aircraft_id, registration, status_text in chunk:
                await model.set_aircraft_status(
                    aircraft_id,
                    registration,
                    status_text,
                )
            await asyncio.sleep(0.5)
        return positions

    async def _register_active_views(self, model):
        rows = await model.list_claimable_incidents()
        for row in rows or []:
            schedule_id = str(row["schedule_id"])
            if schedule_id not in self._registered_views:
                self.bot.add_view(StoppedFlightClaimView(self, schedule_id))
                self._registered_views.add(schedule_id)

    async def _get_organization_schedules(self, client):
        schedules = []
        page_number = 1
        total_pages = 1
        while page_number <= total_pages:
            page = await client.list_organization_schedules(
                ORG_ID, page=page_number, page_size=100
            )
            if page is None or not hasattr(page, "items"):
                raise RuntimeError("Infinite Flight returned an invalid schedule page")
            schedules.extend(page.items or [])
            total_pages = max(1, int(page.total_pages or 1))
            page_number += 1
        return schedules

    async def _process_schedule(
        self, client, model, schedule, position, registration
    ):
        schedule_id = str(_get(schedule, "id"))
        aircraft_id = str(_get(schedule, "aircraft_id"))
        crew = _crew_members(schedule)
        crew_ids = list(dict.fromkeys(filter(None, map(_crew_user_id, crew))))
        row = await model.get_incident(schedule_id)
        if row is None:
            await model.create_incident(
                schedule_id=schedule_id,
                aircraft_id=aircraft_id,
                registration=registration,
                callsign=str(_get(schedule, "callsign") or "Unknown flight"),
                origin_icao=str(_get(schedule, "origin_icao") or ""),
                destination_icao=str(_get(schedule, "destination_icao") or ""),
                original_crew_json=json.dumps(crew_ids),
            )
            row = await model.get_incident(schedule_id)
        if row is None or row["lifecycle"] in {"claimed", "completed", "resumed"}:
            return
        if row["lifecycle"] == "available" and crew_ids:
            claimant_discord_id = ""
            pilot = await self.bot.pilots_model.get_pilot_by_ifuserid(crew_ids[0])
            if pilot:
                claimant_discord_id = str(pilot.get("discordid") or "")
            await model.update_incident(
                schedule_id,
                {"lifecycle": "claimed", "claim_user_id": claimant_discord_id or None},
                expected_lifecycle="available",
            )
            await self._expire_claim_messages(
                model,
                schedule_id,
                "✅ This flight has been booked by another pilot.",
            )
            return
        if row["lifecycle"] == "claiming":
            if crew_ids:
                await model.update_incident(
                    schedule_id,
                    {"lifecycle": "claimed"},
                    expected_lifecycle="claiming",
                )
                await self._expire_claim_messages(
                    model, schedule_id, "✅ This flight was booked by another fleet pilot."
                )
            return

        if not row["stopped_since"]:
            await model.update_incident(
                schedule_id,
                {
                    "registration": registration,
                    "callsign": str(_get(schedule, "callsign") or "Unknown flight"),
                    "origin_icao": str(_get(schedule, "origin_icao") or ""),
                    "destination_icao": str(_get(schedule, "destination_icao") or ""),
                    "original_crew_json": json.dumps(crew_ids),
                },
                expected_lifecycle="monitoring",
            )

        stopped_airborne = (
            int(_get(position, "state", -1)) == STOPPED_POSITION_STATE
            and not bool(_get(position, "is_on_ground", True))
        )
        if stopped_airborne and not row["stopped_since"]:
            await model.update_incident(
                schedule_id,
                {
                    "stopped_since": _utcnow(),
                    "lifecycle": "monitoring",
                    "original_crew_json": json.dumps(crew_ids),
                    "one_hour_sent": 0,
                    "two_hour_sent": 0,
                    "three_hour_sent": 0,
                    "four_hour_sent": 0,
                },
                expected_lifecycle="monitoring",
            )
            row = await model.get_incident(schedule_id)
            log.info(
                "Aircraft %s schedule %s entered stopped-airborne state.",
                registration,
                schedule_id,
            )
        elif row["stopped_since"] and not stopped_airborne:
            if row["lifecycle"] == "available":
                await model.update_incident(
                    schedule_id,
                    {"lifecycle": "resumed"},
                    expected_lifecycle="available",
                )
                await self._expire_claim_messages(
                    model, schedule_id, "Aircraft resumed; this claim is no longer available."
                )
                return
            await model.update_incident(
                schedule_id,
                {
                    "stopped_since": None,
                    "lifecycle": "monitoring",
                    "one_hour_sent": 0,
                    "two_hour_sent": 0,
                    "three_hour_sent": 0,
                    "four_hour_sent": 0,
                },
                expected_lifecycle="monitoring",
            )
            return

        row = await model.get_incident(schedule_id)
        if row is None:
            return
        if not row["eta_warning_sent"] and not stopped_airborne:
            eta = _eta_minutes(
                self.bot, position, _get(schedule, "destination_icao")
            )
            if eta is not None and 0 <= eta <= 30:
                sent = await self._send_eta_warning(row, crew_ids, eta)
                if sent:
                    await model.update_incident(
                        schedule_id, {"eta_warning_sent": 1}
                    )

        stopped_since = _parse_utc(row.get("stopped_since"))
        if stopped_since is None:
            return
        elapsed = _utcnow() - stopped_since
        if elapsed >= timedelta(hours=1) and not row["one_hour_sent"]:
            row = await self._unassign_and_alert(client, model, schedule, row)
            if not row:
                return
        if elapsed >= timedelta(hours=2) and not row["two_hour_sent"]:
            wave_sent = True
            try:
                await self._send_pilot_wave(model, row, wave=2)
            except Exception:
                wave_sent = False
                log.exception(
                    "Could not send two-hour recovery DMs for %s",
                    schedule_id,
                )
                await self._send_dm_recipient_report(
                    row,
                    "2-hour follow-up",
                    [],
                    notes=["The recovery DM wave failed; see bot logs for details."],
                )
            if wave_sent:
                await model.update_incident(
                    schedule_id,
                    {"two_hour_sent": 1},
                    expected_lifecycle="available",
                )
        if elapsed >= timedelta(hours=3) and not row["three_hour_sent"]:
            wave_sent = True
            try:
                await self._send_pilot_wave(model, row, wave=3)
            except Exception:
                wave_sent = False
                log.exception(
                    "Could not send three-hour recovery DMs for %s",
                    schedule_id,
                )
                await self._send_dm_recipient_report(
                    row,
                    "3-hour follow-up",
                    [],
                    notes=["The recovery DM wave failed; see bot logs for details."],
                )
            if wave_sent:
                await model.update_incident(
                    schedule_id,
                    {"three_hour_sent": 1},
                    expected_lifecycle="available",
                )
        if elapsed >= timedelta(hours=4) and not row["four_hour_sent"]:
            await self._send_staff_alert(row, elapsed)
            await model.update_incident(
                schedule_id,
                {"four_hour_sent": 1},
                expected_lifecycle="available",
            )

    async def _send_eta_warning(self, row, if_user_ids, eta):
        sent_any = False
        delivered_recipients = []
        failed_recipients = []
        for if_user_id in if_user_ids:
            try:
                pilot = await self.bot.pilots_model.get_pilot_by_ifuserid(
                    if_user_id
                )
            except Exception:
                log.exception(
                    "Could not look up pilot for ETA warning, IF user %s",
                    if_user_id,
                )
                failed_recipients.append(
                    f"Could not look up the pilot linked to IF user `{if_user_id}`."
                )
                continue
            discord_id = str((pilot or {}).get("discordid") or "")
            if not discord_id.isdigit():
                failed_recipients.append(
                    f"No valid Discord account is linked to IF user `{if_user_id}`."
                )
                continue
            try:
                user = self.bot.get_user(int(discord_id))
                if user is None:
                    user = await self.bot.fetch_user(int(discord_id))
                await user.send(
                    f"🛬 **{row['callsign']} is within about 30 minutes of "
                    f"{row['destination_icao']}** (estimated {max(1, round(eta))} min). "
                    "If the aircraft is stopped in the air, it will be unassigned "
                    "after one hour stopped."
                )
                sent_any = True
                delivered_recipients.append(
                    f"<@{discord_id}> ({pilot.get('callsign') or 'Pilot'})"
                )
            except (discord.Forbidden, discord.HTTPException):
                log.exception(
                    "Could not send 30-minute arrival warning to IF user %s",
                    if_user_id,
                )
                failed_recipients.append(
                    f"<@{discord_id}> ({pilot.get('callsign') or 'Pilot'}) "
                    "could not be DM'd; Discord rejected the message."
                )
            except Exception:
                log.exception(
                    "Unexpected error sending 30-minute arrival warning to IF user %s",
                    if_user_id,
                )
                failed_recipients.append(
                    f"An unexpected error prevented the arrival warning for "
                    f"<@{discord_id}> ({pilot.get('callsign') or 'Pilot'})."
                )
        if delivered_recipients or failed_recipients:
            await self._send_dm_recipient_report(
                row,
                "30-minute arrival warning",
                delivered_recipients,
                failed_recipients=failed_recipients,
            )
        return sent_any

    async def _unassign_and_alert(self, client, model, schedule, row):
        schedule_id = str(row["schedule_id"])
        original_ids = json.loads(row["original_crew_json"] or "[]")
        current_ids = {_crew_user_id(member) for member in _crew_members(schedule)}
        try:
            for if_user_id in original_ids:
                if if_user_id in current_ids:
                    await client.remove_crew(schedule_id, if_user_id)
            refreshed_schedules = await client.list_aircraft_schedules(
                str(row["aircraft_id"])
            )
            refreshed = next(
                (
                    item
                    for item in refreshed_schedules or []
                    if str(_get(item, "id")) == schedule_id
                ),
                None,
            )
            remaining = {
                _crew_user_id(member)
                for member in _crew_members(refreshed)
            } if refreshed else set()
            if not refreshed or remaining.intersection(original_ids):
                raise RuntimeError(
                    "Infinite Flight did not confirm removal of the original crew"
                )
        except Exception:
            log.exception("Could not unassign crew from stopped flight %s", schedule_id)
            await self._send_dm_recipient_report(
                row,
                "1-hour recovery attempt failed",
                [],
                notes=[
                    "The bot could not verify removal of the original crew, "
                    "so it did not offer the flight to recovery pilots."
                ],
            )
            return None

        await model.update_incident(
            schedule_id,
            {"lifecycle": "available", "one_hour_sent": 1},
            expected_lifecycle="monitoring",
        )
        row = await model.get_incident(schedule_id)
        delivered_recipients = []
        failed_recipients = []
        for if_user_id in original_ids:
            try:
                recipient = await self._notify_unassigned_pilot(
                    model,
                    row,
                    if_user_id,
                    failed_recipients,
                )
            except Exception:
                log.exception(
                    "Could not process the crew-removal DM for IF user %s "
                    "on schedule %s",
                    if_user_id,
                    schedule_id,
                )
                failed_recipients.append(
                    f"An unexpected error prevented the removal notice for "
                    f"IF user `{if_user_id}`."
                )
                recipient = None
            if recipient:
                delivered_recipients.append(recipient)
        await self._send_dm_recipient_report(
            row,
            "1-hour crew removal notice",
            delivered_recipients,
            failed_recipients=failed_recipients,
        )
        try:
            await self._send_pilot_wave(model, row, wave=1)
        except Exception:
            log.exception("Could not send one-hour recovery DMs for %s", schedule_id)
            await self._send_dm_recipient_report(
                row,
                "1-hour recovery alert",
                [],
                notes=["The recovery DM wave failed; see bot logs for details."],
            )
        return row

    async def _notify_unassigned_pilot(
        self,
        model,
        row,
        if_user_id,
        failed_recipients=None,
    ):
        failed_recipients = failed_recipients if failed_recipients is not None else []
        try:
            pilot = await self.bot.pilots_model.get_pilot_by_ifuserid(if_user_id)
        except Exception:
            log.exception(
                "Could not look up pilot for stopped-flight removal DM, IF user %s",
                if_user_id,
            )
            failed_recipients.append(
                f"Could not look up the pilot linked to IF user `{if_user_id}`."
            )
            return
        discord_id = str((pilot or {}).get("discordid") or "")
        if not discord_id.isdigit():
            log.warning("No Discord user linked to removed crew member %s", if_user_id)
            failed_recipients.append(
                f"No valid Discord account is linked to removed crew member "
                f"`{if_user_id}`."
            )
            return
        try:
            user = self.bot.get_user(int(discord_id))
            if user is None:
                user = await self.bot.fetch_user(int(discord_id))
            message = await user.send(
                f"⚠️ Your assignment on **{row['registration']} · {row['callsign']}** "
                f"({row['origin_icao']} → {row['destination_icao']}) was removed "
                "because the aircraft remained stopped in the air for one hour. "
                "Do not request this stopped route again. You may fly this aircraft "
                "after this flight is complete and a Live Admin adds new routes."
            )
            await self._record_dm_event_safely(
                model,
                schedule_id=row["schedule_id"],
                if_user_id=if_user_id,
                discord_user_id=discord_id,
                wave=0,
                channel_id=str(message.channel.id),
                message_id=str(message.id),
            )
            return f"<@{discord_id}> ({pilot.get('callsign') or 'Pilot'})"
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not DM pilot %s about stopped-flight removal", if_user_id)
            failed_recipients.append(
                f"<@{discord_id}> ({pilot.get('callsign') or 'Pilot'}) "
                "could not be DM'd; Discord rejected the message."
            )
        except Exception:
            log.exception(
                "Unexpected error sending stopped-flight removal DM to IF user %s",
                if_user_id,
            )
            failed_recipients.append(
                f"An unexpected error prevented the removal notice for "
                f"<@{discord_id}> ({pilot.get('callsign') or 'Pilot'})."
            )
        return None

    async def _record_dm_event_safely(self, model, **event):
        try:
            await model.record_dm_event(**event)
        except Exception:
            log.exception(
                "Could not persist stopped-flight DM delivery record for "
                "schedule %s and IF user %s",
                event.get("schedule_id"),
                event.get("if_user_id"),
            )

    async def _recent_live_flight_count(self, if_user_id):
        now = _utcnow()
        cached = self._activity_cache.get(if_user_id)
        if cached and now - cached[0] < timedelta(seconds=ACTIVITY_CACHE_SECONDS):
            return cached[1]

        api = getattr(self.bot, "if_api_manager", None)
        if api is None:
            raise RuntimeError("Infinite Flight public API manager is unavailable")
        cutoff = now - timedelta(days=FLIGHT_HISTORY_DAYS)
        count = 0
        for page_index in range(MAX_FLIGHT_HISTORY_PAGES):
            response = await api.get_user_flights_page(
                if_user_id,
                page_index=page_index,
                page_size=FLIGHT_HISTORY_PAGE_SIZE,
            )
            if not isinstance(response, dict) or response.get("errorCode") != 0:
                raise RuntimeError(
                    f"Infinite Flight returned an error loading history for {if_user_id}"
                )
            result = response.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(
                    f"Infinite Flight returned invalid history for {if_user_id}"
                )
            flights = result.get("data") or []
            if not isinstance(flights, list):
                raise RuntimeError("Infinite Flight returned invalid user flight history")
            stop = False
            for flight in flights:
                created = _parse_utc(_get(flight, "created"))
                if created is None:
                    continue
                if created < cutoff:
                    stop = True
                    continue
                if float(_get(flight, "totalTime", 0) or 0) > 0:
                    count += 1
            if stop or not result.get("hasNextPage") or not flights:
                break
        self._activity_cache[if_user_id] = (now, count)
        return count

    async def _send_pilot_wave(self, model, row, wave):
        guild = self.bot.get_guild(GUILD_ID)
        role = guild.get_role(FLEET_ROLE_ID) if guild else None
        if guild is None or role is None:
            raise RuntimeError(
                "Cannot send stopped-flight wave: configured guild or fleet role is unavailable"
            )
        contacted_rows = await model.get_dm_events(
            row["schedule_id"], delivered_only=True
        )
        contacted = {str(item["if_user_id"]) for item in contacted_rows or []}
        pilots = await model.get_active_pilots()
        by_discord = {
            str(pilot["discordid"]): pilot
            for pilot in pilots or []
            if pilot.get("discordid") and pilot.get("ifuserid")
        }
        candidates = []
        activity_check_failures = 0
        for member in role.members:
            if member.bot:
                continue
            pilot = by_discord.get(str(member.id))
            if not pilot:
                continue
            if_user_id = str(pilot["ifuserid"])
            if if_user_id in contacted:
                continue
            had_cached_score = if_user_id in self._activity_cache
            try:
                flight_count = await self._recent_live_flight_count(if_user_id)
            except Exception:
                log.exception(
                    "Could not check three-day Live flight history for %s",
                    pilot.get("callsign", if_user_id),
                )
                activity_check_failures += 1
                continue
            if flight_count:
                candidates.append(
                    {
                        "member": member,
                        "pilot": pilot,
                        "if_user_id": if_user_id,
                        "flight_count": flight_count,
                    }
                )
            if not had_cached_score:
                await asyncio.sleep(1.05)

        selected = _weight_pick(candidates, len(candidates))
        if TEST_PILOT_DISCORD_ID:
            test_pilot_index = next(
                (
                    index
                    for index, candidate in enumerate(selected)
                    if str(candidate["member"].id) == TEST_PILOT_DISCORD_ID
                ),
                None,
            )
            if test_pilot_index is not None:
                selected.insert(0, selected.pop(test_pilot_index))
            else:
                log.info(
                    "Configured stopped-flight test pilot %s is not eligible "
                    "for schedule %s in wave %s.",
                    TEST_PILOT_DISCORD_ID,
                    row["schedule_id"],
                    wave,
                )
        delivered_recipients = []
        failed_recipients = []
        for candidate in selected:
            if len(delivered_recipients) >= DM_BATCH_SIZE:
                break
            try:
                incident_state = await model.get_incident_state(row["schedule_id"])
            except Exception:
                log.exception(
                    "Could not verify recovery status before DM wave for %s",
                    row["schedule_id"],
                )
                failed_recipients.append(
                    "Stopped sending because the bot could not verify whether "
                    "the flight had already been claimed."
                )
                break
            if not incident_state or incident_state["lifecycle"] != "available":
                break
            member = candidate["member"]
            try:
                message = await member.send(
                    f"🚨 **Stopped flight available now**\n"
                    f"**{row['registration']} · {row['callsign']}**\n"
                    f"Route: **{row['origin_icao']} → {row['destination_icao']}**\n"
                    "The aircraft has been stopped in the air for at least one "
                    "hour and the previous crew was removed. Claiming this recovery "
                    "flight does **not** count toward your normal two-aircraft "
                    "booking limit.",
                    view=self._claim_view(str(row["schedule_id"])),
                )
                delivered_recipients.append(
                    f"<@{member.id}> ({candidate['pilot'].get('callsign') or 'Pilot'})"
                )
                await self._record_dm_event_safely(
                    model,
                    schedule_id=row["schedule_id"],
                    if_user_id=candidate["if_user_id"],
                    discord_user_id=str(member.id),
                    wave=wave,
                    flight_count_7d=candidate["flight_count"],
                    channel_id=str(message.channel.id),
                    message_id=str(message.id),
                )
                try:
                    incident_state = await model.get_incident_state(row["schedule_id"])
                except Exception:
                    log.exception(
                        "Could not recheck recovery status after DM for %s",
                        row["schedule_id"],
                    )
                    failed_recipients.append(
                        f"Could not verify whether <@{member.id}>'s claim buttons "
                        "remain active."
                    )
                    continue
                if incident_state and incident_state["lifecycle"] != "available":
                    claimant = incident_state.get("claim_user_id")
                    booked_text = (
                        f"✅ This flight was booked by <@{claimant}>."
                        if claimant and str(claimant).isdigit()
                        else "✅ This flight has already been booked."
                    )
                    await self._edit_claim_message(
                        str(message.channel.id), str(message.id), booked_text
                    )
                    break
            except (discord.Forbidden, discord.HTTPException):
                log.warning(
                    "Could not DM fleet-role pilot %s about stopped flight %s; "
                    "continuing to the next eligible pilot.",
                    member.id,
                    row["schedule_id"],
                    exc_info=True,
                )
                failed_recipients.append(
                    f"<@{member.id}> ({candidate['pilot'].get('callsign') or 'Pilot'}) "
                    "could not be DM'd; Discord rejected the message."
                )
                await self._record_dm_event_safely(
                    model,
                    schedule_id=row["schedule_id"],
                    if_user_id=candidate["if_user_id"],
                    discord_user_id=str(member.id),
                    wave=wave,
                    flight_count_7d=candidate["flight_count"],
                    delivery_status="failed",
                )
            except Exception:
                log.exception(
                    "Unexpected error processing recovery DM for pilot %s "
                    "on schedule %s",
                    member.id,
                    row["schedule_id"],
                )
                failed_recipients.append(
                    f"An unexpected error prevented the recovery DM for "
                    f"<@{member.id}> "
                    f"({candidate['pilot'].get('callsign') or 'Pilot'})."
                )
            await asyncio.sleep(0.5)
        wave_labels = {
            1: "1-hour recovery alert",
            2: "2-hour follow-up",
            3: "3-hour follow-up",
        }
        await self._send_dm_recipient_report(
            row,
            wave_labels.get(wave, f"Wave {wave}"),
            delivered_recipients,
            failed_recipients=failed_recipients,
            notes=(
                [
                    f"Activity history could not be checked for "
                    f"{activity_check_failures} pilot(s)."
                ]
                if activity_check_failures
                else None
            ),
        )
        log.info(
            "Stopped-flight wave %s for schedule %s sent to %s eligible pilots.",
            wave,
            row["schedule_id"],
            len(delivered_recipients),
        )

    async def _send_dm_recipient_report(
        self,
        row,
        event_label,
        recipients,
        failed_recipients=None,
        notes=None,
    ):
        recipient_list = "\n".join(f"• {recipient}" for recipient in recipients)
        if not recipient_list:
            recipient_list = "No eligible pilots received a DM."
        sections = [recipient_list]
        if failed_recipients:
            sections.append(
                "**Not delivered / follow-up needed:**\n"
                + "\n".join(f"• {item}" for item in failed_recipients)
            )
        if notes:
            sections.append("\n".join(f"ℹ️ {item}" for item in notes))
        try:
            channel = self.bot.get_channel(DM_LOG_CHANNEL_ID)
            if channel is None:
                channel = await self.bot.fetch_channel(DM_LOG_CHANNEL_ID)
            await channel.send(
                f"📨 **Stopped-flight DM report · {event_label}**\n"
                f"**{row['registration']} · {row['callsign']}** "
                f"({row['origin_icao']} → {row['destination_icao']})\n"
                + "\n".join(sections),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            log.exception(
                "Could not post stopped-flight DM recipient report for schedule %s",
                row["schedule_id"],
            )

    async def _send_staff_alert(self, row, elapsed):
        channel = self.bot.get_channel(STAFF_CHANNEL_ID)
        if channel is None:
            channel = await self.bot.fetch_channel(STAFF_CHANNEL_ID)
        hours = max(4, int(elapsed.total_seconds() // 3600))
        await channel.send(
            f"🛑 **Stopped flight unresolved for {hours} hours**\n"
            f"Aircraft: **{row['registration']}** · Flight: **{row['callsign']}**\n"
            f"Route: **{row['origin_icao']} → {row['destination_icao']}**\n"
            "The assigned pilot was removed after one hour; no fleet pilot has claimed it."
        )

    def _claim_view(self, schedule_id):
        schedule_id = str(schedule_id)
        if schedule_id not in self._registered_views:
            view = StoppedFlightClaimView(self, schedule_id)
            self.bot.add_view(view)
            self._registered_views.add(schedule_id)
            return view
        return StoppedFlightClaimView(self, schedule_id)

    async def _expire_claim_messages(self, model, schedule_id, content):
        incident = await model.get_incident(schedule_id)
        if incident and incident.get("alert_channel_id") and incident.get("alert_message_id"):
            await self._edit_claim_message(
                incident["alert_channel_id"], incident["alert_message_id"], content
            )
        events = await model.get_dm_events(
            schedule_id, with_message_ids=True
        )
        for event in events or []:
            await self._edit_claim_message(
                event["channel_id"], event["message_id"], content
            )

    async def _edit_claim_message(self, channel_id, message_id, content):
        try:
            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                channel = await self.bot.fetch_channel(int(channel_id))
            message = await channel.fetch_message(int(message_id))
            await message.edit(content=content, view=None)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            log.warning(
                "Could not expire stopped-flight claim message %s", message_id,
                exc_info=True,
            )

    async def _close_inactive_incidents(self, model, active_ids):
        rows = await model.list_incidents(("monitoring", "available"))
        for row in rows or []:
            schedule_id = str(row["schedule_id"])
            if schedule_id in active_ids:
                continue
            await model.update_incident(
                schedule_id,
                {"lifecycle": "completed"},
                expected_lifecycle=row["lifecycle"],
            )
            if row["lifecycle"] == "available":
                await self._expire_claim_messages(
                    model, schedule_id, "This flight is no longer available to claim."
                )

    async def _claim_member(self, interaction):
        guild = interaction.guild or self.bot.get_guild(GUILD_ID)
        if guild is None:
            return None
        member = interaction.user
        if interaction.guild is None:
            member = guild.get_member(interaction.user.id)
            if member is None:
                try:
                    member = await guild.fetch_member(interaction.user.id)
                except discord.HTTPException:
                    return None
        role = guild.get_role(FLEET_ROLE_ID)
        return member if role and role in member.roles else None

    async def claim_stopped_flight(self, interaction, schedule_id):
        member = await self._claim_member(interaction)
        if member is None:
            await interaction.response.send_message(
                "Only a member of the fleet role can claim this recovery flight.",
                ephemeral=interaction.guild is not None,
            )
            return
        model = getattr(self.bot, "stopped_flight_model", None)
        if model is None:
            await interaction.response.send_message(
                "The booking service is unavailable. Please try again later.",
                ephemeral=interaction.guild is not None,
            )
            return
        if not await model.try_claim(schedule_id, str(member.id)):
            await interaction.response.send_message(
                "This recovery flight has already been claimed or is no longer available.",
                ephemeral=interaction.guild is not None,
            )
            return

        incident = await model.get_incident(schedule_id)
        pilot = await self.bot.pilots_model.get_pilot_by_discord_id(str(member.id))
        if not pilot or not pilot.get("ifuserid"):
            await model.release_claim(schedule_id)
            await interaction.response.send_message(
                "Your Discord account is not linked to an active Infinite Flight pilot.",
                ephemeral=interaction.guild is not None,
            )
            return

        try:
            manager = self.bot.if_live_manager
            async with await manager.get_client(TOKEN_OWNER_ID) as client:
                position = await client.get_aircraft_position(incident["aircraft_id"])
                schedules = await client.list_aircraft_schedules(incident["aircraft_id"])
                current = next(
                    (
                        schedule
                        for schedule in schedules or []
                        if str(_get(schedule, "id")) == str(schedule_id)
                    ),
                    None,
                )
                if (
                    not current
                    or _status(current) != IN_FLIGHT_SCHEDULE_STATUS
                    or _get(position, "is_on_ground", True)
                    or int(_get(position, "state", -1)) != STOPPED_POSITION_STATE
                    or _crew_members(current)
                ):
                    raise RuntimeError("The stopped flight is no longer claimable")
                result = await client.assign_crew(
                    str(schedule_id), str(pilot["ifuserid"]), 0
                )
                assigned = any(
                    _crew_user_id(crew) == str(pilot["ifuserid"])
                    for crew in _crew_members(result)
                )
                if not assigned:
                    raise RuntimeError("Infinite Flight did not confirm the assignment")
        except Exception as exc:
            log.exception("Could not assign stopped-flight recovery schedule %s", schedule_id)
            await model.release_claim(schedule_id)
            message = (
                "The flight could not be claimed because it is no longer available. "
                "Please refresh or contact a Live Admin."
                if isinstance(exc, RuntimeError)
                else "The assignment service failed. Please try again or contact a Live Admin."
            )
            await interaction.response.send_message(
                message,
                ephemeral=interaction.guild is not None,
            )
            return

        await model.confirm_claim(schedule_id, str(member.id))
        await self._expire_claim_messages(
            model, schedule_id, f"✅ This flight was booked by <@{member.id}>."
        )
        await interaction.response.send_message(
            f"✅ **Flight booked.** You are assigned to "
            f"{incident['registration']} · {incident['callsign']}. "
            "This recovery assignment bypasses the normal aircraft booking limit.",
            ephemeral=interaction.guild is not None,
        )


async def setup(bot):
    await bot.add_cog(StoppedFlightMonitor(bot))

"""Monitor stopped-in-air assignments and offer them to the fleet."""

import asyncio
import json
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord.ext import commands, tasks

from api.if_live_manager import (
    IFLiveError,
    PersistentFlightCrewMemberRole,
    ScheduledFlightStatus,
)
from cogs.dm_logging import DM_LOG_CHANNEL_ID, post_dm_report

log = logging.getLogger(__name__)

ORG_ID = os.getenv("IF_ORGANIZATION_ID", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
STAFF_CHANNEL_ID = int(
    os.getenv(
        "STOPPED_FLIGHT_STAFF_CHANNEL_ID",
        str(DM_LOG_CHANNEL_ID),
    )
    or DM_LOG_CHANNEL_ID
)
STATUS_LOG_CHANNEL_ID = int(
    os.getenv("AIRCRAFT_STATUS_LOG_CHANNEL_ID", "1531201456928063569")
    or "1531201456928063569"
)
FLEET_ROLE_ID = int(
    os.getenv("STOPPED_FLIGHT_FLEET_ROLE_ID", "1484645309983490119") or 0
)
EXECUTIVE_ROLE_ID = 1090751678564810913
ROUTE_EXHAUSTION_CHANNEL_ID = int(
    os.getenv("ROUTE_EXHAUSTION_CHANNEL_ID", str(DM_LOG_CHANNEL_ID))
    or DM_LOG_CHANNEL_ID
)
ROUTE_EXHAUSTION_ROLE_ID = int(
    os.getenv("ROUTE_EXHAUSTION_ROLE_ID", "0") or 0
)
TOKEN_OWNER_ID = 1
POLL_INTERVAL_MINUTES = 2
STOPPED_POSITION_STATE = 4
IN_FLIGHT_SCHEDULE_STATUS = 6
MAX_ETA_WARNING_ATTEMPTS = 1
FLIGHT_HISTORY_DAYS = 3
DEMAND_HISTORY_DAYS = 7
MAX_DEMAND_COUNT_BEFORE_CAPPING = 11
MAX_DEMAND_HISTORY_PAGES = 5
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


def _has_active_routes(schedules):
    terminal_statuses = {
        int(ScheduledFlightStatus.CANCELLED),
        int(ScheduledFlightStatus.ARRIVED),
    }
    return any(
        _status(schedule) not in terminal_statuses
        for schedule in schedules or []
    )


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


def _crew_role(member):
    value = _get(member, "role")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_utc(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not value:
        return None
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        try:
            timestamp = float(value)
            if timestamp > 100_000_000_000:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, timezone.utc).replace(
                tzinfo=None
            )
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except ValueError:
        return None


def _flight_timestamp(flight):
    for key in (
        "created",
        "created_at",
        "createdAt",
        "start_time",
        "startTime",
        "started_at",
        "startedAt",
        "actual_departure_utc",
        "actualDepartureUtc",
        "actual_arrival_utc",
        "actualArrivalUtc",
        "departure_time",
        "departureTime",
        "arrival_time",
        "arrivalTime",
        "flight_start_time",
        "flightStartTime",
        "flight_end_time",
        "flightEndTime",
        "timestamp",
        "date",
        "dateTime",
        "flightDate",
    ):
        timestamp = _parse_utc(_get(flight, key))
        if timestamp is not None:
            return timestamp
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


def _aircraft_status_text(bot, position, schedules, previous_status=None):
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
    current_flight = _status_schedule("ENROUTE", schedules)
    completed_flight = _status_schedule("LANDED", schedules)
    taxiing_flight = next(
        (schedule for schedule in active_schedules if _status(schedule) == 10),
        None,
    )
    if state == 5:
        return "MAINTENANCE"
    if state == STOPPED_POSITION_STATE and not on_ground:
        return (
            _schedule_status_text("STOPPED IN AIR", current_flight)
            if current_flight
            else "STOPPED IN AIR"
        )
    if taxiing_flight:
        return _schedule_status_text("TAXIING", taxiing_flight)
    if not on_ground or state == 2:
        return _schedule_status_text("ENROUTE", current_flight)

    airport = _nearest_airport_icao(bot, position)
    if previous_status and previous_status.startswith(("ENROUTE", "TAXIING")):
        landed_flight = current_flight or completed_flight
        if landed_flight:
            previous_route = _route_details_from_status(previous_status)
            origin = previous_route[0] or str(
                _get(landed_flight, "origin_icao") or ""
            ).strip().upper()
            destination = previous_route[1] or str(
                _get(landed_flight, "destination_icao") or ""
            ).strip().upper()
            callsign = previous_route[2] or str(
                _get(landed_flight, "callsign") or ""
            ).strip()
            route = " → ".join(
                value for value in (origin, destination) if value
            )
            details = " ".join(
                value
                for value in (
                    route,
                    f"by {callsign}" if callsign else "",
                )
                if value
            )
            landed_at = airport or destination
            diverted = bool(
                landed_at
                and destination
                and landed_at != destination.upper()
            ) or _status(landed_flight) == 7
            diversion_label = " (DIVERTED)" if diverted else ""
            location = (
                f" AT {landed_at}{diversion_label}" if landed_at else ""
            )
            return f"LANDED{location} · {details}".strip(" ·")
    if previous_status and previous_status.startswith("LANDED"):
        return f"PARKED AT {airport}" if airport else "PARKED"
    if airport:
        return f"PARKED AT {airport}"
    if active_schedules:
        origin = str(
            _get(active_schedules[0], "origin_icao", "") or ""
        ).strip().upper()
        if origin:
            return f"PARKED AT {origin}"
    return "PARKED · LOCATION UNKNOWN"


def _status_schedule(status_text, schedules):
    ordered_schedules = sorted(schedules or [], key=_sequence)
    if status_text.startswith("LANDED"):
        return next(
            (
                schedule
                for schedule in reversed(ordered_schedules)
                if _status(schedule) == 11
            ),
            None,
        )

    if not status_text.startswith(
        ("ENROUTE", "STOPPED IN AIR", "TAXIING")
    ):
        return None

    preferred_statuses = (
        (10, 6, 7)
        if status_text.startswith("TAXIING")
        else (6, 7, 10)
    )
    for preferred_status in preferred_statuses:
        schedule = next(
            (
                item
                for item in ordered_schedules
                if _status(item) == preferred_status
            ),
            None,
        )
        if schedule:
            return schedule

    return next(
        (
            schedule
            for schedule in reversed(ordered_schedules)
            if _status(schedule) not in {8, 9, 11}
            and _get(schedule, "origin_icao")
            and _get(schedule, "destination_icao")
        ),
        None,
    )


def _route_details_from_status(status_text):
    _, _, details = str(status_text or "").partition(" ")
    if " · " in details:
        details = details.split(" · ", 1)[1]
    route, separator, callsign = details.partition(" by ")
    origin, arrow, destination = route.partition(" → ")
    if not arrow:
        return "", "", ""
    return (
        origin.strip().upper(),
        destination.strip().upper(),
        callsign.strip() if separator else "",
    )


def _schedule_status_text(status, schedule):
    if not schedule:
        return status
    origin = str(_get(schedule, "origin_icao", "") or "").strip().upper()
    destination = str(
        _get(schedule, "destination_icao", "") or ""
    ).strip().upper()
    callsign = str(_get(schedule, "callsign", "") or "").strip()
    route = " → ".join(value for value in (origin, destination) if value)
    details = " ".join(
        value
        for value in (route, f"by {callsign}" if callsign else "")
        if value
    )
    return f"{status} {details}".strip()


def _status_log_line(registration, status_text, schedule, pilot):
    status, _, location = status_text.partition(" ")
    if status == "LANDED":
        landed_at = ""
        if location.upper().startswith("AT "):
            landed_at = location[3:].split(" · ", 1)[0].strip()
        if not landed_at:
            landed_at = _get(schedule, "destination_icao") or location.split(" → ")[-1]
        landed_at = str(landed_at).split(" by ", 1)[0].strip().upper()
        description = f"Landed at {landed_at}"
    elif status == "ENROUTE":
        description = "Enroute"
    elif status == "PARKED":
        if location.startswith("AT "):
            airport = location.strip()
            while airport.upper().startswith("AT "):
                airport = airport[3:].strip()
            description = f"Parked at {airport}" if airport else "Parked"
        elif location.startswith("·"):
            description = "Parked · Location unknown"
        else:
            description = "Parked"
    elif status_text.startswith("STOPPED IN AIR"):
        description = "Stopped in air"
    else:
        description = status.replace("_", " ").title()

    status_origin, status_destination, _ = _route_details_from_status(
        status_text
    )
    origin = status_origin or str(
        _get(schedule, "origin_icao") or ""
    ).strip().upper()
    destination = status_destination or str(
        _get(schedule, "destination_icao") or ""
    ).strip().upper()
    if origin and destination:
        description += f", Route: {origin} -> {destination}"

    if pilot:
        discord_id = str(pilot.get("discordid") or "").strip()
        callsign = str(pilot.get("callsign") or "").strip()
        if discord_id:
            description += f" by <@{discord_id}>"
            if callsign:
                description += f" ({callsign})"
        elif callsign:
            description += f" by {callsign}"

    return f"• **{registration}** — {description}"


def _demand_level(flight_count):
    if flight_count <= 3:
        return "Low"
    if flight_count <= 5:
        return "Medium"
    if flight_count <= 8:
        return "High"
    return "Very High"


def _recent_schedule_flight_count(schedules, cutoff):
    completed_flights = [
        schedule for schedule in schedules or [] if _status(schedule) == 11
    ]
    if not completed_flights:
        return None

    timestamps = [
        _parse_utc(
            _get(schedule, "actual_arrival_utc")
            or _get(schedule, "actualArrivalUtc")
            or _get(schedule, "actual_departure_utc")
            or _get(schedule, "actualDepartureUtc")
        )
        for schedule in completed_flights
    ]
    if any(timestamp is None for timestamp in timestamps):
        return None
    return sum(timestamp >= cutoff for timestamp in timestamps)


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
        self._warned_route_exhaustion_role_disabled = False
        self._status_log_initialized = False
        self.monitor_stopped_flights.start()

    def cog_unload(self):
        self.monitor_stopped_flights.cancel()

    @tasks.loop(minutes=POLL_INTERVAL_MINUTES)
    async def monitor_stopped_flights(self):
        if not ORG_ID:
            log.error("IF_ORGANIZATION_ID is not configured; stopped-flight monitoring is disabled.")
            return
        if not all((GUILD_ID, STAFF_CHANNEL_ID, FLEET_ROLE_ID, EXECUTIVE_ROLE_ID)):
            log.error(
                "Stopped-flight monitoring requires guild, staff, and "
                "fleet and Executive role IDs."
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
                    has_active_routes = _has_active_routes(
                        schedules_by_aircraft.get(aircraft_id, [])
                    )
                    await model.sync_route_availability(
                        aircraft_id,
                        registration,
                        has_active_routes,
                    )
                await self._send_route_exhaustion_alerts(
                    client,
                    model,
                    schedules_by_aircraft,
                )
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

    async def _send_route_exhaustion_alerts(
        self,
        client,
        model,
        schedules_by_aircraft=None,
    ):
        if not ROUTE_EXHAUSTION_CHANNEL_ID:
            log.error("Route exhaustion alerts require a channel ID.")
            return
        if (
            not ROUTE_EXHAUSTION_ROLE_ID
            and not self._warned_route_exhaustion_role_disabled
        ):
            log.warning(
                "ROUTE_EXHAUSTION_ROLE_ID is not configured; route-exhaustion "
                "alerts will be posted without a role mention."
            )
            self._warned_route_exhaustion_role_disabled = True
        pending = await model.list_pending_route_exhaustion_alerts()
        for aircraft in pending:
            try:
                try:
                    recent_flights = await self._recent_aircraft_flight_count(
                        client,
                        str(aircraft["aircraft_id"]),
                        (schedules_by_aircraft or {}).get(
                            str(aircraft["aircraft_id"]),
                            [],
                        ),
                    )
                    if recent_flights is None:
                        demand_label = "Demand unknown"
                    else:
                        demand_level = _demand_level(recent_flights)
                        flight_word = "flight" if recent_flights == 1 else "flights"
                        flight_count_label = (
                            "11+"
                            if recent_flights >= MAX_DEMAND_COUNT_BEFORE_CAPPING
                            else str(recent_flights)
                        )
                        demand_label = (
                            f"{demand_level} Demand · {flight_count_label} "
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
                    (
                        f"<@&{ROUTE_EXHAUSTION_ROLE_ID}> "
                        if ROUTE_EXHAUSTION_ROLE_ID
                        else ""
                    )
                    + f"**Scheduled routes finished "
                    f"for {aircraft['registration']} · ({demand_label}).** "
                    "Please add more routes.",
                    allowed_mentions=discord.AllowedMentions(
                        roles=bool(ROUTE_EXHAUSTION_ROLE_ID)
                    ),
                )
                await model.mark_route_exhaustion_alert_sent(
                    aircraft["aircraft_id"]
                )
            except (discord.Forbidden, discord.HTTPException):
                log.exception(
                    "Could not post route exhaustion alert for aircraft %s",
                    aircraft["aircraft_id"],
                )

    async def _recent_aircraft_flight_count(
        self,
        client,
        aircraft_id,
        schedules=None,
    ):
        cutoff = _utcnow() - timedelta(days=DEMAND_HISTORY_DAYS)
        count = 0
        unparseable_flight_found = False
        try:
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
                    if unparseable_flight_found:
                        raise RuntimeError(
                            f"Infinite Flight returned flight records without usable "
                            f"timestamps for aircraft {aircraft_id}"
                        )
                    return count

                older_flight_found = False
                for flight in flights:
                    created = _flight_timestamp(flight)
                    if created is None:
                        unparseable_flight_found = True
                        continue
                    if created < cutoff:
                        older_flight_found = True
                        continue
                    count += 1
                    if count >= MAX_DEMAND_COUNT_BEFORE_CAPPING:
                        return MAX_DEMAND_COUNT_BEFORE_CAPPING

                if older_flight_found or page_number >= int(
                    _get(page, "total_pages", 0) or 0
                ):
                    if (
                        unparseable_flight_found
                        and count < MAX_DEMAND_COUNT_BEFORE_CAPPING
                    ):
                        raise RuntimeError(
                            f"Could not determine whether all flights occurred within "
                            f"the last {DEMAND_HISTORY_DAYS} days for aircraft {aircraft_id}"
                        )
                    return count

            raise RuntimeError(
                f"Could not determine seven-day flight count for aircraft {aircraft_id} "
                f"within {MAX_DEMAND_HISTORY_PAGES} pages"
            )
        except (
            IFLiveError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
            RuntimeError,
        ) as exc:
            schedule_count = _recent_schedule_flight_count(schedules, cutoff)
            if schedule_count is not None:
                log.warning(
                    "Using completed schedule history for demand on aircraft %s "
                    "because flight history was unavailable: %s",
                    aircraft_id,
                    exc,
                )
                return schedule_count
            raise

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
        expected_aircraft_ids = {
            str(_get(aircraft, "id") or "")
            for aircraft in fleet or []
            if _get(aircraft, "id")
        }
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
                    previous_status=previous_statuses.get(aircraft_id),
                ),
            )

        if not getattr(self, "_status_log_initialized", False):
            for aircraft_id, (registration, status_text) in current_statuses.items():
                await model.set_aircraft_status(
                    aircraft_id,
                    registration,
                    status_text,
                )
            if expected_aircraft_ids.issubset(current_statuses):
                self._status_log_initialized = True
                log.info(
                    "Established the initial aircraft status baseline for %s aircraft; "
                    "startup status messages were suppressed.",
                    len(current_statuses),
                )
            else:
                log.warning(
                    "Aircraft status baseline is incomplete (%s of %s positions); "
                    "status notifications remain suppressed until a complete poll.",
                    len(current_statuses),
                    len(expected_aircraft_ids),
                )
            return positions

        initial_statuses = [
            (aircraft_id, registration, status_text)
            for aircraft_id, (registration, status_text) in current_statuses.items()
            if aircraft_id not in previous_statuses
        ]
        for aircraft_id, registration, status_text in initial_statuses:
            await model.set_aircraft_status(
                aircraft_id,
                registration,
                status_text,
            )

        changed = [
            (aircraft_id, registration, status_text)
            for aircraft_id, (registration, status_text) in current_statuses.items()
            if aircraft_id in previous_statuses
            and previous_statuses[aircraft_id] != status_text
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
        pilot_cache = {}
        rendered_lines = {}
        for aircraft_id, registration, status_text in changed:
            schedule = _status_schedule(
                status_text,
                schedules_by_aircraft.get(aircraft_id, []),
            )
            crew = _crew_members(schedule) if schedule else []
            pilot = None
            if_user_ids = list(
                dict.fromkeys(
                    if_user_id
                    for if_user_id in (
                        *(_crew_user_id(member) for member in crew),
                        str(_get(positions[aircraft_id], "last_pilot_id") or "").strip(),
                    )
                    if if_user_id
                )
            )
            for if_user_id in if_user_ids:
                if if_user_id not in pilot_cache:
                    try:
                        pilot_cache[if_user_id] = (
                            await self.bot.pilots_model.get_pilot_by_ifuserid(
                                if_user_id
                            )
                        )
                    except Exception:
                        log.exception(
                            "Could not resolve pilot %s for aircraft status log.",
                            if_user_id,
                        )
                        pilot_cache[if_user_id] = None
                candidate = pilot_cache[if_user_id]
                if candidate:
                    pilot = candidate
                    if candidate.get("discordid"):
                        break
            rendered_lines[aircraft_id] = _status_log_line(
                registration,
                status_text,
                schedule,
                pilot,
            )

        chunks = []
        current_entries = []
        current_length = 0
        for entry in changed:
            aircraft_id, registration, status_text = entry
            line = rendered_lines[aircraft_id]
            if current_entries and current_length + len(line) + 1 > 1800:
                chunks.append(current_entries)
                current_entries = []
                current_length = 0
            current_entries.append(entry)
            current_length += len(line) + 1
        if current_entries:
            chunks.append(current_entries)

        for chunk in chunks:
            content = "\n".join(
                rendered_lines[aircraft_id]
                for aircraft_id, _, _ in chunk
            )
            try:
                await channel.send(
                    content,
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except discord.HTTPException:
                log.exception("Could not post aircraft status updates.")
                break
            for aircraft_id, registration, status_text in chunk:
                await model.set_aircraft_status(
                    aircraft_id,
                    registration,
                    status_text,
                )
            log.info(
                "Posted aircraft status changes for %s aircraft to channel %s.",
                len(chunk),
                STATUS_LOG_CHANNEL_ID,
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
        original_ids = json.loads(row["original_crew_json"] or "[]")
        executive_ids = set(
            await self._executive_crew_ids(original_ids)
        ) if row["lifecycle"] in {"available", "claiming"} else set()
        if row["lifecycle"] == "available" and set(crew_ids) - executive_ids:
            claimant_discord_id = ""
            claimant_if_id = next(
                if_user_id
                for if_user_id in crew_ids
                if if_user_id not in executive_ids
            )
            pilot = await self.bot.pilots_model.get_pilot_by_ifuserid(claimant_if_id)
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
            if set(crew_ids) - executive_ids:
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
                attempt_started = await model.try_start_eta_warning_attempt(
                    schedule_id,
                    _utcnow(),
                    retry_after_minutes=0,
                    max_attempts=MAX_ETA_WARNING_ATTEMPTS,
                )
                if attempt_started:
                    sent = await self._send_eta_warning(
                        row,
                        crew_ids,
                        eta,
                    )
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
                wave_sent = await self._send_pilot_wave(model, row, wave=2)
            except Exception as exc:
                wave_sent = False
                log.exception(
                    "Could not send two-hour recovery DMs for %s",
                    schedule_id,
                )
                await self._send_dm_recipient_report(
                    row,
                    "2-hour follow-up",
                    [],
                    notes=[
                        "The recovery DM wave failed "
                        f"({type(exc).__name__}); see bot logs for details."
                    ],
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
                wave_sent = await self._send_pilot_wave(model, row, wave=3)
            except Exception as exc:
                wave_sent = False
                log.exception(
                    "Could not send three-hour recovery DMs for %s",
                    schedule_id,
                )
                await self._send_dm_recipient_report(
                    row,
                    "3-hour follow-up",
                    [],
                    notes=[
                        "The recovery DM wave failed "
                        f"({type(exc).__name__}); see bot logs for details."
                    ],
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
                    f"No valid Discord account is linked to "
                    f"{(pilot or {}).get('callsign') or 'the pilot'} "
                    f"(IF user `{if_user_id}`)."
                )
                continue
            try:
                user = self.bot.get_user(int(discord_id))
                if user is None:
                    user = await self.bot.fetch_user(int(discord_id))
                await user.send(
                    f"🛬 **{row['registration']} is within about 30 minutes of "
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
            executive_ids = await self._executive_crew_ids(original_ids)
            removable_ids = [
                if_user_id
                for if_user_id in original_ids
                if if_user_id not in executive_ids
            ]
            for if_user_id in removable_ids:
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
            if not refreshed or remaining.intersection(removable_ids):
                raise RuntimeError(
                    "Infinite Flight did not confirm removal of the non-Executive crew"
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
        for if_user_id in removable_ids:
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
        if delivered_recipients or failed_recipients or removable_ids:
            await self._send_dm_recipient_report(
                row,
                "1-hour crew removal notice",
                delivered_recipients,
                failed_recipients=failed_recipients,
                notes=[
                    f"{len(removable_ids)} non-Executive crew member(s) "
                    "were eligible for removal notices."
                ],
            )
        executive_recipients = []
        executive_failures = []
        for if_user_id in sorted(set(executive_ids).intersection(current_ids)):
            try:
                recipient = await self._notify_executive_pilot(
                    model,
                    row,
                    if_user_id,
                    executive_failures,
                )
            except Exception:
                log.exception(
                    "Could not process the Executive warning DM for IF user %s "
                    "on schedule %s",
                    if_user_id,
                    schedule_id,
                )
                executive_failures.append(
                    f"An unexpected error prevented the Executive warning for "
                    f"IF user `{if_user_id}`."
                )
                recipient = None
            if recipient:
                executive_recipients.append(recipient)
        await self._send_dm_recipient_report(
            row,
            "1-hour Executive warning",
            executive_recipients,
            failed_recipients=executive_failures,
            notes=[
                "Executive crew members remain assigned and were not removed."
            ],
        )
        try:
            await self._send_pilot_wave(model, row, wave=1)
        except Exception as exc:
            log.exception("Could not send one-hour recovery DMs for %s", schedule_id)
            await self._send_dm_recipient_report(
                row,
                "1-hour recovery alert",
                [],
                notes=[
                    "The recovery DM wave failed "
                    f"({type(exc).__name__}); see bot logs for details."
                ],
            )
        return row

    async def _executive_crew_ids(self, if_user_ids, required=True):
        if not if_user_ids:
            return set()
        guild = self.bot.get_guild(GUILD_ID)
        role = guild.get_role(EXECUTIVE_ROLE_ID) if guild else None
        if guild is None or role is None:
            if not required:
                return set()
            raise RuntimeError(
                "The configured guild or Executive role is unavailable"
            )
        executive_ids = set()
        for if_user_id in if_user_ids:
            pilot = await self.bot.pilots_model.get_pilot_by_ifuserid(
                str(if_user_id)
            )
            discord_id = str((pilot or {}).get("discordid") or "")
            if not discord_id:
                continue
            member = guild.get_member(int(discord_id))
            if member is None:
                try:
                    member = await guild.fetch_member(int(discord_id))
                except discord.NotFound:
                    continue
            if member is None:
                continue
            if role in member.roles:
                executive_ids.add(str(if_user_id))
        return executive_ids

    async def _notify_executive_pilot(
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
                "Could not look up Executive for stopped-flight warning DM, IF user %s",
                if_user_id,
            )
            failed_recipients.append(
                f"Could not look up the Executive linked to IF user `{if_user_id}`."
            )
            return None
        discord_id = str((pilot or {}).get("discordid") or "")
        if not discord_id.isdigit():
            failed_recipients.append(
                f"No valid Discord account is linked to the Executive "
                f"(IF user `{if_user_id}`)."
            )
            return None
        try:
            user = self.bot.get_user(int(discord_id))
            if user is None:
                user = await self.bot.fetch_user(int(discord_id))
            message = await user.send(
                f"⚠️ **Stopped-flight notice · {row['registration']}**\n"
                f"Flight: **{row['callsign']}** "
                f"({row['origin_icao']} → {row['destination_icao']})\n"
                "The aircraft has been stopped in the air for one hour. "
                "Because you hold the Executive role, you will remain assigned; "
                "the bot will not unassign you. Non-Executive crew will be removed, "
                "and eligible recovery pilots will be notified."
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
            log.exception(
                "Stopped-flight Executive warning DM failed for IF user %s.",
                if_user_id,
            )
            failed_recipients.append(
                f"<@{discord_id}> ({pilot.get('callsign') or 'Pilot'}) "
                "could not be DM'd; Discord rejected the message."
            )
        except Exception:
            log.exception(
                "Unexpected error sending stopped-flight Executive warning "
                "to IF user %s",
                if_user_id,
            )
            failed_recipients.append(
                f"An unexpected error prevented the Executive warning for "
                f"<@{discord_id}> ({pilot.get('callsign') or 'Pilot'})."
            )
        return None

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
                f"{(pilot or {}).get('callsign') or 'Unknown pilot'} "
                f"(IF user `{if_user_id}`)."
            )
            return
        try:
            user = self.bot.get_user(int(discord_id))
            if user is None:
                user = await self.bot.fetch_user(int(discord_id))
            message = await user.send(
                f"⚠️ Your assignment on "
                f"**{row['registration']} · {row['callsign']}** "
                f"({row['origin_icao']} → {row['destination_icao']}) was "
                "removed because the aircraft remained stopped in the air "
                "for one hour. Another pilot will land the aircraft, and "
                "you can continue the next leg if it is booked. Flight hours "
                "for this flight will be split 50/50 between you and the "
                "recovery pilot."
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
            log.exception(
                "Stopped-flight crew notice DM failed after one attempt for "
                "IF user %s.",
                if_user_id,
            )
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
        fleet_role = guild.get_role(FLEET_ROLE_ID) if guild else None
        if guild is None or fleet_role is None:
            raise RuntimeError(
                "Cannot send stopped-flight wave: configured guild or fleet "
                "role is unavailable"
            )
        original_ids = json.loads(row.get("original_crew_json") or "[]")
        executive_crew_ids = await self._executive_crew_ids(
            original_ids,
            required=False,
        )
        contacted_rows = await model.get_dm_events(
            row["schedule_id"],
            delivered_only=True,
            recovery_only=True,
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
        role_member_count = 0
        linked_pilot_count = 0
        recent_activity_count = 0
        exclusion_counts = {
            "missing_link": 0,
            "already_contacted": 0,
            "no_recent_flights": 0,
            "activity_check_failed": 0,
            "assigned_executive": 0,
        }
        exclusion_examples = {reason: [] for reason in exclusion_counts}

        def record_exclusion(reason, member, pilot=None):
            exclusion_counts[reason] += 1
            if len(exclusion_examples[reason]) >= 5:
                return
            callsign = (pilot or {}).get("callsign")
            identity = f"{member.display_name}"
            if callsign and str(callsign).casefold() not in identity.casefold():
                identity += f" ({callsign})"
            exclusion_examples[reason].append(identity)

        for member in fleet_role.members:
            if member.bot:
                continue
            role_member_count += 1
            pilot = by_discord.get(str(member.id))
            if not pilot:
                record_exclusion("missing_link", member)
                continue
            nickname = str(member.nick or "")
            callsign_match = re.search(r"QRV\d{3,}", nickname, re.IGNORECASE)
            if callsign_match:
                current_callsign = str(pilot.get("callsign") or "").upper()
                new_callsign = callsign_match.group(0).upper()
                if current_callsign != new_callsign:
                    sync_result = await self.bot.pilots_model.sync_callsign_by_discord_id(
                        str(member.id),
                        new_callsign,
                    )
                    if sync_result == "updated":
                        pilot["callsign"] = new_callsign
                    elif sync_result == "callsign_conflict":
                        log.warning(
                            "Could not sync callsign %s for linked pilot %s "
                            "during recovery-wave selection; the callsign "
                            "belongs to another pilot record.",
                            new_callsign,
                            member.id,
                        )
            linked_pilot_count += 1
            if_user_id = str(pilot["ifuserid"])
            if if_user_id in executive_crew_ids:
                record_exclusion("assigned_executive", member, pilot)
                continue
            if if_user_id in contacted:
                record_exclusion("already_contacted", member, pilot)
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
                record_exclusion("activity_check_failed", member, pilot)
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
                if flight_count:
                    recent_activity_count += 1
            else:
                record_exclusion("no_recent_flights", member, pilot)
            if not had_cached_score:
                await asyncio.sleep(1.05)

        selected = candidates
        delivered_recipients = []
        failed_recipients = []
        for candidate in selected:
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
                retained_executive_text = (
                    "Non-Executive crew were removed; an Executive remains "
                    "assigned. "
                    if executive_crew_ids
                    else "The previous crew was removed. "
                )
                message = await member.send(
                    f"🚨 **Stopped flight available now**\n"
                    f"**{row['registration']} · {row['callsign']}**\n"
                    f"Route: **{row['origin_icao']} → {row['destination_icao']}**\n"
                    "The aircraft has been stopped in the air for at least one "
                    "hour. "
                    f"{retained_executive_text}"
                    "Claiming this recovery "
                    "flight does **not** count toward your normal two-aircraft "
                    "booking limit. Flight hours for this flight will be split "
                    "50/50 between you and the original assigned pilot.",
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
                    "Could not DM recovery pilot %s about stopped flight %s; "
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
                await self._record_dm_event_safely(
                    model,
                    schedule_id=row["schedule_id"],
                    if_user_id=candidate["if_user_id"],
                    discord_user_id=str(member.id),
                    wave=wave,
                    flight_count_7d=candidate["flight_count"],
                    delivery_status="failed",
                )
            await asyncio.sleep(0.5)
        wave_labels = {
            1: "1-hour recovery alert",
            2: "2-hour follow-up",
            3: "3-hour follow-up",
        }
        diagnostic_notes = [
            (
                "Candidate check: "
                f"{role_member_count} non-bot Fleet-role members, "
                f"{linked_pilot_count} with active linked pilot records, "
                f"{recent_activity_count} with recent flights, "
                f"{activity_check_failures} flight-history checks failed, "
                f"{len(selected)} eligible pilots selected."
            )
        ]
        exclusion_labels = {
            "missing_link": "no active linked pilot record",
            "already_contacted": "already received this recovery flight DM",
            "no_recent_flights": "no Live flights in the 3-day activity window",
            "activity_check_failed": "Live activity check failed",
            "assigned_executive": "Executive remains assigned to this flight",
        }
        for reason, count in exclusion_counts.items():
            if not count:
                continue
            examples = ", ".join(exclusion_examples[reason])
            detail = f"{count} {exclusion_labels[reason]}"
            if examples:
                detail += f" (e.g. {examples})"
            diagnostic_notes.append(detail + ".")
        report_posted = await self._send_dm_recipient_report(
            row,
            wave_labels.get(wave, f"Wave {wave}"),
            delivered_recipients,
            failed_recipients=failed_recipients,
            notes=diagnostic_notes,
        )
        if not report_posted:
            log.error(
                "Staff report for stopped-flight wave %s on schedule %s "
                "could not be posted; the wave will be retried.",
                wave,
                row["schedule_id"],
            )
        log.info(
            "Stopped-flight wave %s for schedule %s sent to %s eligible pilots.",
            wave,
            row["schedule_id"],
            len(delivered_recipients),
        )
        return report_posted

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
                "**Not delivered:**\n"
                + "\n".join(f"• {item}" for item in failed_recipients)
            )
        if notes:
            sections.append(
                "**Selection / delivery notes:**\n"
                + "\n".join(f"• {item}" for item in notes)
            )
        return await post_dm_report(
            self.bot,
            (
                f"📨 **Stopped-flight DM report · {event_label}**\n"
                f"**{row['registration']} · {row['callsign']}** "
                f"({row['origin_icao']} → {row['destination_icao']})\n"
                + "\n".join(sections)
            ),
            log,
            f"stopped-flight schedule {row['schedule_id']}",
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
            "Non-Executive crew were removed after one hour; no recovery pilot "
            "has claimed the flight."
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
        fleet_role = guild.get_role(FLEET_ROLE_ID)
        return member if fleet_role and fleet_role in member.roles else None

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
                current_crew = _crew_members(current) if current else []
                used_roles = {
                    _crew_role(crew)
                    for crew in current_crew
                }
                assignment_role = next(
                    (
                        int(role)
                        for role in (
                            PersistentFlightCrewMemberRole.CAPTAIN,
                            PersistentFlightCrewMemberRole.FIRST_OFFICER,
                        )
                        if int(role) not in used_roles
                    ),
                    None,
                )
                if (
                    not current
                    or _status(current) != IN_FLIGHT_SCHEDULE_STATUS
                    or _get(position, "is_on_ground", True)
                    or int(_get(position, "state", -1)) != STOPPED_POSITION_STATE
                    or assignment_role is None
                ):
                    raise RuntimeError("The stopped flight is no longer claimable")
                result = await client.assign_crew(
                    str(schedule_id), str(pilot["ifuserid"]), assignment_role
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

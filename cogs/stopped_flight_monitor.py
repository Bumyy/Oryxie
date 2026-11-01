"""Monitor stopped-in-air assignments and offer them to the fleet."""

import asyncio
import json
import logging
import math
import os
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from api.if_live_manager import (
    AircraftPersistenceState,
    IFLiveError,
    PersistentFlightCrewMemberRole,
    ScheduledFlightStatus,
)
from cogs.airline_hubs import hubs_for_registration
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
    os.getenv("AIRCRAFT_STATUS_LOG_CHANNEL_ID")
    or os.getenv("FLIGHT_TRACKER_CHANNEL_ID")
    or "0"
)
FLEET_ROLE_ID = int(
    os.getenv("STOPPED_FLIGHT_FLEET_ROLE_ID", "0") or 0
)
EXECUTIVE_ROLE_ID = int(
    os.getenv("STOPPED_FLIGHT_EXECUTIVE_ROLE_ID", "0") or 0
)
RECOVERY_ALERT_CHANNEL_ID = int(
    os.getenv("STOPPED_FLIGHT_RECOVERY_CHANNEL_ID", "0") or 0
)
RECOVERY_PING_ROLE_ID = int(
    os.getenv("STOPPED_FLIGHT_RECOVERY_ROLE_ID", "0") or 0
)
RECOVERY_ROLE_SETUP_AUTHORIZED_ROLE_ID = int(
    os.getenv("RECOVERY_ROLE_SETUP_AUTHORIZED_ROLE_ID", "0") or 0
)
RECOVERY_OPT_IN_EMOJI_NAME = "qatari"
RECOVERY_PANEL_TITLE = "Stopped-flight recovery alerts"
QATAR_MAROON = 0x8A1538
ROUTE_EXHAUSTION_CHANNEL_ID = int(
    os.getenv("ROUTE_EXHAUSTION_CHANNEL_ID", str(DM_LOG_CHANNEL_ID))
    or DM_LOG_CHANNEL_ID
)
ROUTE_EXHAUSTION_ROLE_ID = int(
    os.getenv("ROUTE_EXHAUSTION_ROLE_ID", "0") or 0
)
TOKEN_OWNER_ID = 1
POLL_INTERVAL_MINUTES = 2
STOPPED_POSITION_STATE = int(AircraftPersistenceState.STOPPED)
IN_FLIGHT_SCHEDULE_STATUS = 6
MAX_ETA_WARNING_ATTEMPTS = 3
ETA_WARNING_RETRY_MINUTES = 10
FLIGHT_HISTORY_DAYS = 30
RECOVERY_CANDIDATE_LIMIT = 3
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


def _flight_progress(bot, position, destination_icao):
    airports = getattr(getattr(bot, "flightdata", None), "airports_db", None)
    if not airports:
        return None, None
    airport = airports.get(str(destination_icao or "").strip().upper())
    if not airport:
        return None, None
    try:
        distance = _distance_nm(
            float(_get(position, "latitude")),
            float(_get(position, "longitude")),
            float(airport["latitude_deg"]),
            float(airport["longitude_deg"]),
        )
        speed = float(_get(position, "speed"))
    except (KeyError, TypeError, ValueError):
        return None, None
    if speed <= 30:
        return distance, None
    return distance, distance * 60 / speed


def _eta_minutes(bot, position, destination_icao):
    _, eta = _flight_progress(bot, position, destination_icao)
    return eta


def _aircraft_model(item):
    if not item:
        return ""
    for key in (
        "model",
        "aircraft_name",
        "aircraftName",
        "name",
        "title",
        "type_name",
        "typeName",
    ):
        value = _get(item, key)
        if value and str(value).strip():
            return str(value).strip()
    return ""


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


def _takeoff_window_hours(registration, airport_icao=None):
    normalized_registration = str(registration or "").strip().upper()
    airport = str(airport_icao or "").strip().upper()
    if airport and airport in hubs_for_registration(normalized_registration):
        return 6
    if airport and airport not in hubs_for_registration(normalized_registration):
        return 12
    return 6 if not normalized_registration else 12


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
    if state == int(AircraftPersistenceState.MAINTENANCE):
        return "MAINTENANCE"
    if state == STOPPED_POSITION_STATE:
        return (
            _schedule_status_text("STOPPED IN AIR", current_flight)
            if current_flight
            else "STOPPED IN AIR"
        )
    if taxiing_flight:
        return _schedule_status_text("TAXIING", taxiing_flight)
    if not on_ground or state == int(AircraftPersistenceState.IN_FLIGHT):
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
        self._takeoff_limit_warning_cache = set()
        self._warned_status_logging_disabled = False
        self._warned_route_exhaustion_role_disabled = False
        self._status_log_initialized = False
        self._live_flight_trackers = {}
        self.monitor_stopped_flights.start()

    def cog_unload(self):
        self.monitor_stopped_flights.cancel()

    @app_commands.command(
        name="setup_recovery_role",
        description="Post the opt-in panel for stopped-flight alert pings.",
    )
    @app_commands.checks.has_role(RECOVERY_ROLE_SETUP_AUTHORIZED_ROLE_ID)
    async def setup_recovery_role(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Run this command in a server text channel.",
                ephemeral=True,
            )
            return
        role = guild.get_role(RECOVERY_PING_ROLE_ID)
        emoji = discord.utils.get(
            guild.emojis,
            name=RECOVERY_OPT_IN_EMOJI_NAME,
        )
        if role is None:
            await interaction.response.send_message(
                f"The configured recovery alert role `{RECOVERY_PING_ROLE_ID}` "
                "was not found in this server.",
                ephemeral=True,
            )
            return
        bot_member = guild.me
        if (
            bot_member is None
            or not bot_member.guild_permissions.manage_roles
            or bot_member.top_role <= role
        ):
            await interaction.response.send_message(
                "I need Manage Roles permission, and my highest role must be "
                "above the recovery alert role.",
                ephemeral=True,
            )
            return
        if emoji is None or not emoji.available:
            await interaction.response.send_message(
                f"The server custom emoji `:{RECOVERY_OPT_IN_EMOJI_NAME}:` "
                "is unavailable.",
                ephemeral=True,
            )
            return
        try:
            embed = discord.Embed(
                title=RECOVERY_PANEL_TITLE,
                description=(
                    f"React with {emoji} to get the recovery alert role "
                    f"({role.mention}). Remove your reaction to opt out."
                ),
                color=discord.Color(QATAR_MAROON),
            )
            message = await interaction.channel.send(
                embed=embed,
            )
            await message.add_reaction(emoji)
        except (discord.Forbidden, discord.HTTPException):
            log.exception(
                "Could not publish stopped-flight recovery role panel."
            )
            await interaction.response.send_message(
                "I couldn't post the opt-in message or add its reaction. "
                "Check my channel permissions and try again.",
                ephemeral=True,
            )
            return
        except Exception:
            log.exception("Could not publish stopped-flight recovery role panel.")
            await interaction.response.send_message(
                "I couldn't publish the recovery opt-in panel. Check the bot's "
                "channel permissions and try again.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Recovery alert opt-in is set up in {interaction.channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="track-flight",
        description="Get a live flight tracking DM with automatic updates and time to TOD (Zulu time).",
    )
    @app_commands.describe(
        registration="Aircraft registration (e.g. A7-BCB). If omitted, finds your active flight.",
    )
    async def track_flight(
        self,
        interaction: discord.Interaction,
        registration: Optional[str] = None,
    ):
        await interaction.response.defer(ephemeral=True)

        manager = getattr(self.bot, "if_live_manager", None)
        if not manager or not ORG_ID:
            await interaction.followup.send(
                "Live fleet tracking service is currently unavailable.",
                ephemeral=True,
            )
            return

        try:
            fleet_snapshot = await manager.get_live_fleet(ORG_ID, pilot_id=TOKEN_OWNER_ID)
        except Exception as exc:
            log.exception("Could not retrieve live fleet snapshot for /track-flight")
            await interaction.followup.send(
                f"Error querying live fleet: {exc}",
                ephemeral=True,
            )
            return

        positions = fleet_snapshot.to_positions_dict()
        schedules_by_aircraft = fleet_snapshot.schedules_by_aircraft

        matched_ac = None
        pilot_data = None

        if registration and registration.strip():
            reg_clean = registration.strip().upper().replace("-", "")
            matched_ac = next(
                (
                    ac for ac in fleet_snapshot
                    if (ac.registration or "").upper().replace("-", "") == reg_clean
                    or (ac.registration or "").upper() == registration.strip().upper()
                ),
                None,
            )
            if matched_ac is None:
                await interaction.followup.send(
                    f"Aircraft `{registration}` was not found in the live fleet.",
                    ephemeral=True,
                )
                return
        else:
            # Look up the caller's linked pilot account
            try:
                pilot_ident = await self.bot.pilots_model.identify_pilot(interaction.user)
                if pilot_ident and pilot_ident.get("success"):
                    pilot_data = pilot_ident.get("pilot_data")
                if not pilot_data:
                    pilot_data = await self.bot.pilots_model.get_pilot_by_discord_id(
                        str(interaction.user.id)
                    )
            except Exception:
                log.exception("Could not look up pilot for /track-flight")
                pilot_data = None

            if not pilot_data:
                await interaction.followup.send(
                    "No linked pilot profile was found for your Discord account. "
                    "Please specify an aircraft registration (e.g. `/track-flight registration:A7-BCB`).",
                    ephemeral=True,
                )
                return

            if_user_id = str(pilot_data.get("ifuserid") or "").strip()

            # Search fleet for an active aircraft piloted by this user
            for ac in fleet_snapshot:
                ac_pos = positions.get(ac.aircraft_id)
                last_pilot = str(getattr(ac, "last_pilot_id", "") or (_get(ac_pos, "last_pilot_id") if ac_pos else "") or "").strip()
                if if_user_id and last_pilot == if_user_id:
                    matched_ac = ac
                    break

                # Also check active schedule crew
                for s in schedules_by_aircraft.get(ac.aircraft_id, []):
                    crew = _crew_members(s)
                    if any(str(_crew_user_id(m) or "").strip() == if_user_id for m in crew):
                        matched_ac = ac
                        break
                if matched_ac:
                    break

            if matched_ac is None:
                await interaction.followup.send(
                    "No active flight was detected for your account. "
                    "Please specify an aircraft registration (e.g. `/track-flight registration:A7-BCB`).",
                    ephemeral=True,
                )
                return

        target_aircraft_id = matched_ac.aircraft_id
        target_registration = matched_ac.registration
        pos = positions.get(target_aircraft_id)

        ac_schedules = schedules_by_aircraft.get(target_aircraft_id, [])
        active_sched = next(
            (s for s in ac_schedules if _status(s) == IN_FLIGHT_SCHEDULE_STATUS),
            None,
        )
        if active_sched is None and ac_schedules:
            active_sched = ac_schedules[0]
        if active_sched is None:
            active_sched = {
                "id": target_aircraft_id,
                "aircraft_id": target_aircraft_id,
                "callsign": target_registration,
                "origin_icao": "???",
                "destination_icao": "???",
            }

        pos_state = int(_get(pos, "state", -1)) if pos else -1
        is_ground = bool(_get(pos, "is_on_ground", False)) if pos else False
        spd = float(_get(pos, "speed", 0.0)) if pos else 0.0

        if pos_state == STOPPED_POSITION_STATE or (not is_ground and spd < 30 and pos_state != 1):
            flight_status = "STOPPED"
        elif is_ground and spd < 30:
            flight_status = "LANDED"
        else:
            flight_status = "ENROUTE"

        target_pilot = pilot_data
        if not target_pilot:
            crew = _crew_members(active_sched)
            pilot_id_candidate = _crew_user_id(crew[0]) if crew else str(_get(pos, "last_pilot_id") or "").strip()
            if pilot_id_candidate:
                try:
                    target_pilot = await self.bot.pilots_model.get_pilot_by_ifuserid(pilot_id_candidate)
                except Exception:
                    target_pilot = None

        embed = self._build_live_flight_embed(
            registration=target_registration,
            schedule=active_sched,
            position=pos,
            status=flight_status,
            pilot=target_pilot,
        )

        try:
            msg = await interaction.user.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send(
                "Could not send you a direct message. Please check your Discord privacy "
                "settings to allow direct messages from server members.",
                ephemeral=True,
            )
            return

        tracker_data = {
            "schedule_id": str(_get(active_sched, "id") or target_aircraft_id),
            "aircraft_id": str(target_aircraft_id),
            "registration": str(target_registration),
            "discord_user_id": str(interaction.user.id),
            "channel_id": str(msg.channel.id),
            "message_id": str(msg.id),
            "origin_icao": str(_get(active_sched, "origin_icao") or ""),
            "destination_icao": str(_get(active_sched, "destination_icao") or ""),
            "callsign": str(_get(active_sched, "callsign") or target_registration),
            "status": "enroute",
        }
        if not hasattr(self, "_live_flight_trackers"):
            self._live_flight_trackers = {}
        self._live_flight_trackers[str(target_aircraft_id)] = tracker_data

        model = getattr(self.bot, "stopped_flight_model", None)
        if model and hasattr(model, "save_live_flight_tracker"):
            res = model.save_live_flight_tracker(**tracker_data)
            if asyncio.iscoroutine(res):
                await res

        callsign_str = str(_get(active_sched, "callsign") or target_registration)
        await interaction.followup.send(
            f"Sent live flight tracker for **{target_registration}** ({callsign_str}) "
            "to your DMs! It will update automatically every ~2 minutes.",
            ephemeral=True,
        )

    @track_flight.autocomplete("registration")
    async def track_flight_registration_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        manager = getattr(self.bot, "if_live_manager", None)
        if not manager or not ORG_ID:
            return []
        try:
            fleet_snapshot = await manager.get_live_fleet(ORG_ID, pilot_id=TOKEN_OWNER_ID)
            choices = []
            curr_clean = current.strip().upper().replace("-", "")
            for ac in fleet_snapshot:
                reg = ac.registration or ""
                reg_clean = reg.upper().replace("-", "")
                if not curr_clean or curr_clean in reg_clean:
                    status_desc = ac.formatted_status or "Available"
                    label = f"{reg} ({status_desc})"[:100]
                    choices.append(app_commands.Choice(name=label, value=reg))
                    if len(choices) >= 25:
                        break
            return choices
        except Exception:
            return []

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        await self._handle_recovery_role_reaction(payload, add_role=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        await self._handle_recovery_role_reaction(payload, add_role=False)

    async def _handle_recovery_role_reaction(self, payload, add_role):
        bot_user = self.bot.user
        if bot_user and int(payload.user_id) == int(bot_user.id):
            return
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(int(payload.guild_id))
        if guild is None:
            return
        emoji_id = getattr(payload.emoji, "id", None)
        emoji = discord.utils.get(
            guild.emojis,
            name=RECOVERY_OPT_IN_EMOJI_NAME,
        )
        if emoji is None or str(emoji_id or "") != str(emoji.id):
            return
        channel = self.bot.get_channel(int(payload.channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(payload.channel_id))
            except (discord.Forbidden, discord.HTTPException):
                log.exception(
                    "Could not access channel %s for recovery role reaction.",
                    payload.channel_id,
                )
                return
        try:
            message = await channel.fetch_message(int(payload.message_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            log.exception(
                "Could not inspect message %s for recovery role reaction.",
                payload.message_id,
            )
            return
        if (
            not bot_user
            or str(message.author.id) != str(bot_user.id)
            or not any(
                embed.title == RECOVERY_PANEL_TITLE
                and f"<@&{RECOVERY_PING_ROLE_ID}>" in (embed.description or "")
                for embed in message.embeds
            )
        ):
            return
        role = guild.get_role(RECOVERY_PING_ROLE_ID)
        if role is None:
            log.error(
                "Recovery opt-in role %s is not available in guild %s.",
                RECOVERY_PING_ROLE_ID,
                guild.id,
            )
            return
        member = getattr(payload, "member", None) if add_role else None
        if member is None and guild:
            try:
                member = await guild.fetch_member(int(payload.user_id))
            except discord.NotFound:
                return
            except discord.HTTPException:
                log.exception(
                    "Could not fetch member %s for recovery role reaction.",
                    payload.user_id,
                )
                return
        if member is None or member.bot:
            return
        try:
            if add_role:
                await member.add_roles(
                    role,
                    reason="Opted in to stopped-flight recovery alerts",
                )
            else:
                await member.remove_roles(
                    role,
                    reason="Opted out of stopped-flight recovery alerts",
                )
        except (discord.Forbidden, discord.HTTPException):
            log.exception(
                "Could not %s recovery alert role for member %s.",
                "add" if add_role else "remove",
                payload.user_id,
            )

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
            if not getattr(self, "_live_flight_trackers_loaded", False):
                if hasattr(model, "get_active_live_flight_trackers"):
                    self._live_flight_trackers = await model.get_active_live_flight_trackers()
                self._live_flight_trackers_loaded = True
            # Query LiveFleetService for consolidated fleet, schedules, and positions
            fleet_snapshot = await manager.get_live_fleet(ORG_ID, pilot_id=TOKEN_OWNER_ID)
            schedules = fleet_snapshot.all_schedules
            schedules_by_aircraft = fleet_snapshot.schedules_by_aircraft
            positions = fleet_snapshot.to_positions_dict()
            fleet = (
                getattr(fleet_snapshot, "fleet_list", None)
                or list(fleet_snapshot)
                or []
            )
            registrations = {
                ac.aircraft_id: ac.registration
                for ac in fleet_snapshot
            }
            for ac in fleet_snapshot:
                await model.sync_route_availability(
                    ac.aircraft_id,
                    ac.registration,
                    ac.has_active_routes,
                )

            async with await manager.get_client(TOKEN_OWNER_ID) as client:

                active = [
                    schedule
                    for schedule in schedules
                    if _status(schedule) == IN_FLIGHT_SCHEDULE_STATUS
                ]
                processed_aircraft = set()
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
                            positions[aircraft_id] = position
                        await self._process_schedule(
                            client,
                            model,
                            schedule,
                            position,
                            registrations.get(aircraft_id, "Unknown aircraft"),
                        )
                        processed_aircraft.add(aircraft_id)
                    except Exception:
                        log.exception(
                            "Stopped-flight monitoring failed for schedule %s",
                            schedule_id,
                        )
                for aircraft_id, position in positions.items():
                    if aircraft_id in processed_aircraft:
                        continue
                    try:
                        state = int(_get(position, "state", -1))
                    except (TypeError, ValueError):
                        continue
                    if state != STOPPED_POSITION_STATE:
                        continue
                    aircraft_schedules = schedules_by_aircraft.get(aircraft_id, [])
                    schedule = None
                    # Prefer the schedule already associated with an incident,
                    # so its stopped timer survives changes to schedule status.
                    for candidate in aircraft_schedules:
                        candidate_id = str(_get(candidate, "id") or "")
                        if not candidate_id:
                            continue
                        incident = await model.get_incident(candidate_id)
                        if incident and incident.get("lifecycle") != "claimed":
                            schedule = candidate
                            break
                    # For a newly detected STOPPED aircraft, choose the current
                    # route using schedule details only; STOPPED position state
                    # above is the detection gate, not IN_FLIGHT schedule status.
                    if schedule is None:
                        schedule = _status_schedule(
                            "STOPPED IN AIR",
                            aircraft_schedules,
                        )
                    if schedule is None:
                        # A STOPPED position is authoritative even if IF has
                        # marked the corresponding schedule terminal. Use its
                        # latest route record to create and track the incident.
                        schedule = next(
                            (
                                candidate
                                for candidate in reversed(
                                    sorted(aircraft_schedules, key=_sequence)
                                )
                                if _get(candidate, "id")
                                and _get(candidate, "origin_icao")
                                and _get(candidate, "destination_icao")
                            ),
                            None,
                        )
                    if schedule is None:
                        log.warning(
                            "Aircraft %s is STOPPED airborne, but IF Live returned "
                            "no usable schedule for recovery tracking.",
                            aircraft_id,
                        )
                        continue
                    try:
                        await self._process_schedule(
                            client,
                            model,
                            schedule,
                            position,
                            registrations.get(aircraft_id, "Unknown aircraft"),
                        )
                    except Exception:
                        log.exception(
                            "Stopped-flight monitoring failed for stopped aircraft %s schedule %s",
                            aircraft_id,
                            str(_get(schedule, "id") or ""),
                        )
                await self._close_inactive_incidents(model, positions)
                await self._send_route_exhaustion_alerts(
                    client,
                    model,
                    schedules_by_aircraft,
                )
                await self._log_fleet_status_changes(
                    client,
                    model,
                    fleet,
                    schedules_by_aircraft,
                    position_cache=positions,
                )
                await self._update_live_flight_trackers(
                    client,
                    model,
                    fleet,
                    positions,
                    schedules_by_aircraft,
                )
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
        position_cache=None,
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
        positions = position_cache if position_cache is not None else {}
        expected_aircraft_ids = {
            str(_get(aircraft, "id") or "")
            for aircraft in fleet or []
            if _get(aircraft, "id")
        }
        for aircraft in fleet or []:
            aircraft_id = str(_get(aircraft, "id") or "")
            if not aircraft_id:
                continue
            position = positions.get(aircraft_id)
            if position is None:
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
            if current_statuses or not expected_aircraft_ids:
                self._status_log_initialized = True
                if expected_aircraft_ids.issubset(current_statuses):
                    log.info(
                        "Established the initial aircraft status baseline for %s "
                        "aircraft; startup status messages were suppressed.",
                        len(current_statuses),
                    )
                else:
                    log.warning(
                        "Established a partial aircraft status baseline (%s of %s "
                        "positions). Aircraft first seen on later polls will be logged.",
                        len(current_statuses),
                        len(expected_aircraft_ids),
                    )
            elif expected_aircraft_ids:
                log.warning(
                    "Could not establish an aircraft status baseline: no positions "
                    "were available from %s aircraft.",
                    len(expected_aircraft_ids),
                )
            return positions

        changed = [
            (aircraft_id, registration, status_text)
            for aircraft_id, (registration, status_text) in current_statuses.items()
            if aircraft_id not in previous_statuses
            or previous_statuses[aircraft_id] != status_text
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
        resolved_pilots = {}
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
            resolved_pilots[aircraft_id] = (schedule, pilot)

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
                prev_status = previous_statuses.get(aircraft_id) or ""
                if status_text.startswith("ENROUTE") and not prev_status.startswith("ENROUTE"):
                    sch, plt = resolved_pilots.get(aircraft_id, (None, None))
                    if sch and plt:
                        try:
                            await self._start_live_flight_tracker(
                                model,
                                aircraft_id,
                                registration,
                                sch,
                                positions.get(aircraft_id),
                                plt,
                            )
                        except Exception:
                            log.exception(
                                "Could not start live flight tracker for %s",
                                registration,
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
        if row is None:
            return
        stopped_airborne = (
            int(_get(position, "state", -1)) == STOPPED_POSITION_STATE
        )
        log.debug(
            "_process_schedule: schedule_id=%s registration=%s state=%s (stopped_airborne=%s) lifecycle=%s stopped_since=%s",
            schedule_id,
            registration,
            _get(position, "state", -1),
            stopped_airborne,
            row.get("lifecycle"),
            row.get("stopped_since"),
        )
        if row["lifecycle"] == "claimed":
            return
        if row["lifecycle"] in {"completed", "resumed"}:
            if not stopped_airborne:
                return
            # Older monitor cycles could close incidents based on schedule
            # status before seeing the aircraft's STOPPED position. Reopen
            # those stale rows from the live API state and start a fresh timer.
            await model.update_incident(
                schedule_id,
                {
                    "lifecycle": "monitoring",
                    "stopped_since": _utcnow(),
                    "original_crew_json": (
                        json.dumps(crew_ids)
                        if crew_ids
                        else row.get("original_crew_json") or "[]"
                    ),
                    "one_hour_sent": 0,
                    "two_hour_sent": 0,
                    "three_hour_sent": 0,
                    "four_hour_sent": 0,
                },
                expected_lifecycle=row["lifecycle"],
            )
            row = await model.get_incident(schedule_id)
            if row is None or row["lifecycle"] != "monitoring":
                return
            log.info(
                "Reopened stopped-flight incident %s from current IF Live STOPPED state.",
                schedule_id,
            )
            await self._announce_new_stopped_aircraft(row)
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
                (
                    f"This flight was claimed by <@{claimant_discord_id}> "
                    f"({pilot.get('callsign')})."
                    if claimant_discord_id and pilot and pilot.get("callsign")
                    else "This flight has already been claimed by another pilot."
                ),
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
                    model,
                    schedule_id,
                    "This flight has already been claimed by another Fleet pilot.",
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
                    "original_crew_json": (
                        json.dumps(crew_ids)
                        if crew_ids
                        else row.get("original_crew_json") or "[]"
                    ),
                },
                expected_lifecycle=row["lifecycle"],
            )

        if stopped_airborne and not row["stopped_since"]:
            log.info(
                "Detected stopped airborne aircraft! Setting stopped_since for schedule %s (%s, registration %s)",
                schedule_id,
                _get(schedule, "callsign"),
                registration,
            )
            values = {
                "stopped_since": _utcnow(),
                "original_crew_json": (
                    json.dumps(crew_ids)
                    if crew_ids
                    else row.get("original_crew_json") or "[]"
                ),
            }
            if row["lifecycle"] == "monitoring":
                values.update(
                    {
                        "one_hour_sent": 0,
                        "two_hour_sent": 0,
                        "three_hour_sent": 0,
                        "four_hour_sent": 0,
                    }
                )
            await model.update_incident(
                schedule_id,
                values,
                expected_lifecycle=row["lifecycle"],
            )
            row = await model.get_incident(schedule_id)
            if row is not None:
                await self._announce_new_stopped_aircraft(row)
        elif row["stopped_since"] and not stopped_airborne:
            if row["lifecycle"] == "available":
                await model.update_incident(
                    schedule_id,
                    {"lifecycle": "resumed"},
                    expected_lifecycle="available",
                )
                await self._expire_claim_messages(
                    model,
                    schedule_id,
                    "Aircraft resumed; this claim is no longer available.",
                    reply_text=(
                        "This claim is no longer available because the aircraft resumed. "
                        "The original pilot should be noted for any 50/50 PIREP tracking."
                    ),
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
            if eta is not None and 0 <= eta <= 40:
                attempt_started = await model.try_start_eta_warning_attempt(
                    schedule_id,
                    _utcnow(),
                    retry_after_minutes=ETA_WARNING_RETRY_MINUTES,
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
        original_ids = json.loads(row["original_crew_json"] or "[]")
        parked_icao = (
            _nearest_airport_icao(self.bot, position)
            or str(_get(schedule, "destination_icao") or "").strip().upper()
        )
        takeoff_limit_hours = _takeoff_window_hours(registration, parked_icao)
        warning_window_hours = max(0, takeoff_limit_hours - 2)
        if (
            not stopped_airborne
            and elapsed >= timedelta(hours=warning_window_hours)
            and elapsed < timedelta(hours=takeoff_limit_hours)
            and schedule_id not in self._takeoff_limit_warning_cache
        ):
            self._takeoff_limit_warning_cache.add(schedule_id)
            for if_user_id in original_ids:
                try:
                    pilot = await self.bot.pilots_model.get_pilot_by_ifuserid(if_user_id)
                except Exception:
                    continue
                discord_id = str((pilot or {}).get("discordid") or "")
                if not discord_id.isdigit():
                    continue
                try:
                    user = self.bot.get_user(int(discord_id))
                    if user is None:
                        user = await self.bot.fetch_user(int(discord_id))
                    await user.send(
                        f"**Takeoff window alert**\n"
                        f"**{registration}** has been parked on the ground at **{parked_icao or 'an unknown airport'}** "
                        f"for {max(1, int(elapsed.total_seconds() // 3600))} hours. "
                        f"If it has not departed within the next 2 hours, this assignment will be unassigned."
                    )
                except Exception:
                    log.exception(
                        "Could not DM takeoff-window warning for IF user %s on schedule %s",
                        if_user_id,
                        schedule_id,
                    )
        if (
            not stopped_airborne
            and elapsed >= timedelta(hours=takeoff_limit_hours)
            and row["lifecycle"] in {"monitoring", "available"}
        ):
            if row["lifecycle"] == "monitoring":
                await self._unassign_and_alert(
                    client,
                    model,
                    schedule,
                    row,
                    executive_ids=set(await self._executive_crew_ids(original_ids)),
                )
            elif row["lifecycle"] == "available":
                await self._send_public_recovery_offer(
                    model,
                    {**row, "registration": registration, "callsign": str(_get(schedule, "callsign") or row.get("callsign") or "Unknown flight")},
                )
                await self._send_dm_recipient_report(
                    row,
                    "Parked on ground takeoff timeout",
                    [],
                    notes=[
                        f"The aircraft has been parked on the ground at **{parked_icao or 'an unknown airport'}** for {takeoff_limit_hours} hours; the assignment has been reopened for recovery pilots."
                    ],
                )
        if elapsed >= timedelta(hours=1) and not row["one_hour_sent"]:
            if row["lifecycle"] == "monitoring":
                executive_ids = set(
                    await self._executive_crew_ids(original_ids)
                )
                row = await self._unassign_and_alert(
                    client,
                    model,
                    schedule,
                    row,
                    executive_ids=executive_ids,
                )
                if not row:
                    return
            if row["lifecycle"] == "available" and not row["one_hour_sent"]:
                if set(original_ids).intersection(executive_ids):
                    await model.update_incident(
                        schedule_id,
                        {"one_hour_sent": 1},
                        expected_lifecycle="available",
                    )
                    row = await model.get_incident(schedule_id)
                    if row is None:
                        return
                    log.info(
                        "Skipped the one-hour public recovery offer for %s "
                        "because its original crew includes an Executive.",
                        schedule_id,
                    )
                elif await self._send_public_recovery_offer(model, row):
                    await model.update_incident(
                        schedule_id,
                        {"one_hour_sent": 1},
                        expected_lifecycle="available",
                    )
                    row = await model.get_incident(schedule_id)
                    if row is None:
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
        target_ids = list(if_user_ids or [])
        if not target_ids:
            try:
                target_ids = json.loads(row.get("original_crew_json") or "[]")
            except Exception:
                target_ids = []
        for if_user_id in target_ids:
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
                    f"**{row['registration']} is within about 40 minutes of "
                    f"{row['destination_icao']}** (estimated {max(1, round(eta))} min). "
                    "If the aircraft is stopped in the air, it will be unassigned "
                    "after one hour stopped."
                )
                sent_any = True
                delivered_recipients.append(
                    f"<@{discord_id}> ({pilot.get('callsign') or 'Pilot'})"
                )
            except discord.Forbidden as exc:
                if getattr(exc, "code", None) == 50278:
                    reason = (
                        "Discord says this account shares no server with the bot. "
                        "Verify the linked Discord account and make sure it is a "
                        "member of this server."
                    )
                    log.warning(
                        "Cannot send 40-minute arrival warning to IF user %s "
                        "(Discord %s): no mutual guild.",
                        if_user_id,
                        discord_id,
                    )
                else:
                    reason = (
                        "Discord rejected the DM. Check the linked account's "
                        "privacy settings."
                    )
                    log.warning(
                        "Discord rejected 40-minute arrival warning to IF user %s "
                        "(Discord %s), error code %s.",
                        if_user_id,
                        discord_id,
                        getattr(exc, "code", "unknown"),
                    )
                failed_recipients.append(
                    f"<@{discord_id}> ({pilot.get('callsign') or 'Pilot'}): "
                    f"{reason}"
                )
            except discord.HTTPException:
                log.exception(
                    "Could not send 40-minute arrival warning to IF user %s",
                    if_user_id,
                )
                failed_recipients.append(
                    f"<@{discord_id}> ({pilot.get('callsign') or 'Pilot'}) "
                    "could not be DM'd; Discord failed to deliver the message."
                )
            except Exception:
                log.exception(
                    "Unexpected error sending 40-minute arrival warning to IF user %s",
                    if_user_id,
                )
                failed_recipients.append(
                    f"An unexpected error prevented the arrival warning for "
                    f"<@{discord_id}> ({pilot.get('callsign') or 'Pilot'})."
                )
        if delivered_recipients or failed_recipients:
            await self._send_dm_recipient_report(
                row,
                "40-minute arrival warning",
                delivered_recipients,
                failed_recipients=failed_recipients,
            )
        return sent_any

    async def _unassign_and_alert(
        self,
        client,
        model,
        schedule,
        row,
        executive_ids=None,
    ):
        schedule_id = str(row["schedule_id"])
        original_ids = json.loads(row["original_crew_json"] or "[]")
        current_ids = {_crew_user_id(member) for member in _crew_members(schedule)}
        try:
            if executive_ids is None:
                executive_ids = await self._executive_crew_ids(original_ids)
            removable_ids = [
                if_user_id
                for if_user_id in original_ids
                if if_user_id not in executive_ids
            ]
            if removable_ids:
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
                        "Infinite Flight did not confirm removal of the "
                        "non-Executive crew"
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
            {"lifecycle": "available"},
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
        return row

    async def _send_public_recovery_offer(self, model, row):
        if row.get("alert_message_id"):
            return True
        if not RECOVERY_ALERT_CHANNEL_ID or not RECOVERY_PING_ROLE_ID:
            log.error(
                "Public stopped-flight offers require recovery channel and role IDs."
            )
            return False
        guild = self.bot.get_guild(GUILD_ID)
        role = guild.get_role(RECOVERY_PING_ROLE_ID) if guild else None
        if role is None:
            log.error(
                "Cannot post stopped-flight offer: recovery ping role %s is unavailable.",
                RECOVERY_PING_ROLE_ID,
            )
            return False
        try:
            channel = self.bot.get_channel(RECOVERY_ALERT_CHANNEL_ID)
            if channel is None:
                channel = await self.bot.fetch_channel(RECOVERY_ALERT_CHANNEL_ID)
            embed = discord.Embed(
                title="Stopped flight available to claim",
                description=(
                    f"**{row['registration']} · {row['callsign']}**\n"
                    f"Route: **{row['origin_icao']} → {row['destination_icao']}**\n\n"
                    "Stopped in the air for at least one hour. Any Fleet pilot may "
                    "claim it with the button below. Recovery assignments bypass the "
                    "normal two-aircraft limit."
                ),
                color=discord.Color(QATAR_MAROON),
            )
            content = (
                f"<@&{RECOVERY_PING_ROLE_ID}> **Stopped flight available to claim**\n"
                f"**{row['registration']} · {row['callsign']}** "
                f"({row['origin_icao']} → {row['destination_icao']})\n"
                "Stopped in the air for at least one hour. Any Fleet pilot may "
                "claim it with the button below. Recovery assignments bypass the "
                "normal two-aircraft limit."
            )
            message = await channel.send(
                content,
                embed=embed,
                view=self._claim_view(str(row["schedule_id"])),
                allowed_mentions=discord.AllowedMentions(
                    roles=[role],
                    users=False,
                    everyone=False,
                ),
            )
            await model.update_incident(
                row["schedule_id"],
                {
                    "alert_channel_id": str(channel.id),
                    "alert_message_id": str(message.id),
                },
                expected_lifecycle="available",
            )
            log.info(
                "Posted public stopped-flight offer for %s to channel %s.",
                row["schedule_id"],
                channel.id,
            )
            return True
        except (discord.Forbidden, discord.HTTPException):
            log.exception(
                "Could not post public stopped-flight offer for schedule %s.",
                row["schedule_id"],
            )
            await self._send_dm_recipient_report(
                row,
                "1-hour public recovery offer failed",
                [],
                notes=[
                    "Could not post the public recovery alert. The bot will retry "
                    "on the next monitor cycle; recovery DMs are scheduled at two hours."
                ],
            )
            return False

    async def _announce_new_stopped_aircraft(self, row):
        message = (
            f"**New stopped aircraft detected**\n"
            f"**{row['registration']} · {row['callsign']}** "
            f"({row['origin_icao']} → {row['destination_icao']})\n"
            "IF Live reports it stopped airborne. The recovery timer starts now; "
            "the public offer is due after 1 hour and pilot DMs after 2 hours."
        )
        log.warning(
            "New stopped-aircraft incident detected: %s · %s (%s → %s), "
            "schedule %s; recovery timer started.",
            row["registration"],
            row["callsign"],
            row["origin_icao"],
            row["destination_icao"],
            row["schedule_id"],
        )
        await post_dm_report(
            self.bot,
            message,
            log,
            f"new stopped-aircraft detection for schedule {row['schedule_id']}",
        )

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
                f"**Stopped-flight notice · {row['registration']}**\n"
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
                f"Your assignment on "
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

    async def _recent_live_flight_activity(self, if_user_id):
        now = _utcnow()
        cached = self._activity_cache.get(if_user_id)
        if cached and now - cached[0] < timedelta(seconds=ACTIVITY_CACHE_SECONDS):
            val = cached[1]
            if isinstance(val, (tuple, list)):
                return int(val[0]), int(val[1])
            return int(val), int(val)

        api = getattr(self.bot, "if_api_manager", None)
        if api is None:
            raise RuntimeError("Infinite Flight public API manager is unavailable")
        cutoff_30d = now - timedelta(days=FLIGHT_HISTORY_DAYS)
        cutoff_7d = now - timedelta(days=7)
        count_7d = 0
        count_30d = 0
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
                if created < cutoff_30d:
                    stop = True
                    continue
                if float(_get(flight, "totalTime", 0) or 0) > 0:
                    count_30d += 1
                    if created >= cutoff_7d:
                        count_7d += 1
            if stop or not result.get("hasNextPage") or not flights:
                break
        res = (count_7d, count_30d)
        self._activity_cache[if_user_id] = (now, res)
        return res

    async def _recent_live_flight_count(self, if_user_id):
        activity = await self._recent_live_flight_activity(if_user_id)
        return activity[1]

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
        recent_dm_counts = {}
        try:
            if hasattr(model, "get_recent_recovery_dm_counts"):
                recent_dm_counts = await model.get_recent_recovery_dm_counts(hours=24)
        except Exception:
            log.exception("Could not retrieve 24-hour recovery DM frequency counts")

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
        offline_filtered_count = 0
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
            if member.status not in (discord.Status.online, discord.Status.idle, discord.Status.dnd):
                offline_filtered_count += 1
                continue
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
                flights_7d, flights_30d = await self._recent_live_flight_activity(if_user_id)
            except Exception:
                log.exception(
                    "Could not check Live flight history for %s",
                    pilot.get("callsign", if_user_id),
                )
                activity_check_failures += 1
                record_exclusion("activity_check_failed", member, pilot)
                continue
            if flights_30d:
                candidates.append(
                    {
                        "member": member,
                        "pilot": pilot,
                        "if_user_id": if_user_id,
                        "flight_count": flights_30d,
                        "flights_7d": flights_7d,
                        "flights_30d": flights_30d,
                        "is_hook": (flights_7d == 0 and flights_30d > 0),
                        "recent_dm_count": recent_dm_counts.get(if_user_id, 0),
                    }
                )
                recent_activity_count += 1
            else:
                record_exclusion("no_recent_flights", member, pilot)
            if not had_cached_score:
                await asyncio.sleep(1.05)

        # Separate into active pilots (flew in last 7d) and re-engagement hook pilots (flew in last 30d, but not 7d)
        active_candidates = [c for c in candidates if not c["is_hook"]]
        hook_candidates = [c for c in candidates if c["is_hook"]]

        # Target slot allocation: 1 slot for re-engagement hook pilot, up to 2 for active pilots
        hook_target = 1 if hook_candidates else 0
        active_target = min(len(active_candidates), RECOVERY_CANDIDATE_LIMIT - hook_target)
        if active_target + hook_target < RECOVERY_CANDIDATE_LIMIT:
            hook_target = min(len(hook_candidates), RECOVERY_CANDIDATE_LIMIT - active_target)

        def _weighted_pick(pool, k):
            if not pool or k <= 0:
                return []
            if len(pool) <= k:
                res = list(pool)
                random.shuffle(res)
                return res
            # Weighted sampling without replacement (A-Res):
            # Scale down weight aggressively if the pilot received recovery DMs in the last 24h
            scored = []
            for item in pool:
                base_act = math.sqrt(max(1, item["flights_7d"] if not item["is_hook"] else item["flights_30d"]))
                dm_penalty = 1.0 + 3.0 * item["recent_dm_count"]
                effective_weight = max(base_act / dm_penalty, 0.0001)
                rnd = max(random.random(), 1e-9)
                score = rnd ** (1.0 / effective_weight)
                scored.append((score, item))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [item for _, item in scored[:k]]

        selected_active = _weighted_pick(active_candidates, active_target)
        selected_hook = _weighted_pick(hook_candidates, hook_target)
        selected = selected_active + selected_hook
        random.shuffle(selected)
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
                    f"**Stopped flight available now**\n"
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
                        f"This flight was booked by <@{claimant}>."
                        if claimant and str(claimant).isdigit()
                        else "This flight has already been booked."
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
                f"{offline_filtered_count} offline (filtered out), "
                f"{linked_pilot_count} with active linked pilot records, "
                f"{recent_activity_count} with recent 30-day flights, "
                f"{activity_check_failures} flight-history checks failed, "
                f"{len(candidates)} eligible pilots "
                f"({len(active_candidates)} active in 7d, {len(hook_candidates)} re-engagement in 30d), "
                f"{len(selected)} selected "
                f"({len(selected_active)} active, {len(selected_hook)} re-engagement; maximum "
                f"{RECOVERY_CANDIDATE_LIMIT})."
            )
        ]
        if len(candidates) > len(selected):
            diagnostic_notes.append(
                f"{len(candidates) - len(selected)} eligible pilots were not "
                "contacted because the recovery DM wave is capped."
            )
        deprioritized_count = sum(1 for c in candidates if c["recent_dm_count"] > 0)
        if deprioritized_count:
            diagnostic_notes.append(
                f"{deprioritized_count} candidates deprioritized to prevent spamming "
                "(already received recovery DMs in the last 24 hours)."
            )
        exclusion_labels = {
            "missing_link": "no active linked pilot record",
            "already_contacted": "already received this recovery flight DM",
            "no_recent_flights": "no Live flights in the 30-day activity window",
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
                f"**Stopped-flight DM report · {event_label}**\n"
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
            f"**Stopped flight unresolved for {hours} hours**\n"
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

    async def _expire_claim_messages(self, model, schedule_id, content, reply_text=None):
        incident = await model.get_incident(schedule_id)
        if incident and incident.get("alert_channel_id") and incident.get("alert_message_id"):
            await self._edit_claim_message(
                incident["alert_channel_id"],
                incident["alert_message_id"],
                content,
                reply_text=reply_text,
            )
        events = await model.get_dm_events(
            schedule_id, with_message_ids=True
        )
        for event in events or []:
            await self._edit_claim_message(
                event["channel_id"],
                event["message_id"],
                content,
                reply_text=reply_text,
            )

    def _strike_out_claim_message(self, content):
        text = str(content or "").strip()
        if not text:
            return text
        if text.startswith("~~") and text.endswith("~~"):
            return text
        return f"~~{text}~~"

    async def _edit_claim_message(self, channel_id, message_id, content, reply_text=None):
        try:
            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                channel = await self.bot.fetch_channel(int(channel_id))
            message = await channel.fetch_message(int(message_id))
            await message.edit(
                content=self._strike_out_claim_message(content),
                view=None,
            )
            if reply_text and str(reply_text).strip():
                await channel.send(
                    str(reply_text).strip(),
                    reference=message.to_reference(),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            log.warning(
                "Could not expire stopped-flight claim message %s", message_id,
                exc_info=True,
            )

    async def _close_inactive_incidents(self, model, positions):
        rows = await model.list_incidents(("monitoring", "available"))
        for row in rows or []:
            schedule_id = str(row["schedule_id"])
            aircraft_id = str(row["aircraft_id"])
            position = positions.get(aircraft_id)
            if not position:
                continue
            try:
                state = int(_get(position, "state", -1))
            except (TypeError, ValueError):
                continue
            if state == STOPPED_POSITION_STATE:
                continue
            await model.update_incident(
                schedule_id,
                {"lifecycle": "completed"},
                expected_lifecycle=row["lifecycle"],
            )
            if row["lifecycle"] == "available":
                await self._expire_claim_messages(
                    model,
                    schedule_id,
                    "This flight is no longer available to claim.",
                    reply_text=(
                        "This claim expired because the aircraft is no longer available to claim."
                    ),
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
            ac_state = None
            if hasattr(manager, "get_live_fleet") and asyncio.iscoroutinefunction(manager.get_live_fleet):
                fleet_snapshot = await manager.get_live_fleet(ORG_ID, pilot_id=TOKEN_OWNER_ID)
                ac_state = fleet_snapshot.get(incident["aircraft_id"]) if fleet_snapshot else None
                if ac_state is not None and not ac_state.is_stopped:
                    raise RuntimeError("The stopped flight is no longer claimable")

            async with await manager.get_client(TOKEN_OWNER_ID) as client:
                if ac_state is None:
                    position = await client.get_aircraft_position(incident["aircraft_id"])
                    # Allow either STOPPED (4) or IN_FLIGHT (2) if not in live session
                    state_val = int(_get(position, "state", -1))
                    is_ground = bool(_get(position, "is_on_ground", False))
                    if is_ground or state_val not in (STOPPED_POSITION_STATE, int(AircraftPersistenceState.IN_FLIGHT)):
                        raise RuntimeError("The stopped flight is no longer claimable")

                schedules = (getattr(ac_state, "schedules", None) or []) if ac_state else await client.list_aircraft_schedules(incident["aircraft_id"])
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
                if not current or assignment_role is None:
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
            model,
            schedule_id,
            (
                f"This flight was claimed by <@{member.id}> "
                f"({pilot.get('callsign') or 'Pilot'})."
            ),
            reply_text=(
                f"This recovery flight is now claimed by <@{member.id}> "
                f"({pilot.get('callsign') or 'Pilot'}). "
                "The original assigned pilot should be noted for any 50/50 PIREP tracking."
            ),
        )
        await interaction.response.send_message(
            f"**Flight booked.** You are assigned to "
            f"{incident['registration']} · {incident['callsign']}. "
            "This recovery assignment bypasses the normal aircraft booking limit.",
            ephemeral=interaction.guild is not None,
        )

    async def _start_live_flight_tracker(
        self,
        model,
        aircraft_id,
        registration,
        schedule,
        position,
        pilot,
    ):
        if not hasattr(self, "_live_flight_trackers"):
            self._live_flight_trackers = {}
        if not pilot:
            return
        discord_id = str(pilot.get("discordid") or "").strip()
        if not discord_id.isdigit():
            return
        schedule_id = str(_get(schedule, "id") or "")
        if not schedule_id:
            return

        if str(aircraft_id) in self._live_flight_trackers:
            existing = self._live_flight_trackers[str(aircraft_id)]
            if existing.get("schedule_id") == schedule_id:
                return

        try:
            user = self.bot.get_user(int(discord_id))
            if user is None:
                user = await self.bot.fetch_user(int(discord_id))
            if not user:
                return

            embed = self._build_live_flight_embed(
                registration=registration,
                schedule=schedule,
                position=position,
                status="ENROUTE",
                pilot=pilot,
            )
            msg = await user.send(embed=embed)
            origin_icao = str(_get(schedule, "origin_icao") or "").strip().upper()
            destination_icao = str(_get(schedule, "destination_icao") or "").strip().upper()
            callsign = str(_get(schedule, "callsign") or registration).strip().upper()
            tracker_data = {
                "schedule_id": schedule_id,
                "aircraft_id": str(aircraft_id),
                "registration": str(registration),
                "discord_user_id": str(discord_id),
                "channel_id": str(msg.channel.id),
                "message_id": str(msg.id),
                "origin_icao": origin_icao,
                "destination_icao": destination_icao,
                "callsign": callsign,
                "status": "enroute",
            }
            self._live_flight_trackers[str(aircraft_id)] = tracker_data
            if hasattr(model, "save_live_flight_tracker"):
                res = model.save_live_flight_tracker(**tracker_data)
                if asyncio.iscoroutine(res):
                    await res
            log.info(
                "Sent live flight tracker DM for %s (%s) to pilot <@%s>",
                registration,
                callsign,
                discord_id,
            )
        except discord.Forbidden:
            log.warning(
                "Cannot DM live flight tracker to pilot %s: DMs closed or forbidden.",
                discord_id,
            )
        except Exception:
            log.exception(
                "Failed to send live flight tracker for aircraft %s schedule %s",
                aircraft_id,
                schedule_id,
            )

    def _build_live_flight_embed(
        self,
        registration: str,
        schedule: Any,
        position: Any,
        status: str = "ENROUTE",
        pilot: Optional[Dict[str, Any]] = None,
    ) -> discord.Embed:
        origin = str(_get(schedule, "origin_icao") or "???").strip().upper()
        destination = str(_get(schedule, "destination_icao") or "???").strip().upper()
        callsign = str(_get(schedule, "callsign") or registration).strip().upper()

        airports = getattr(getattr(self.bot, "flightdata", None), "airports_db", None) or {}
        orig_apt = airports.get(origin)
        dest_apt = airports.get(destination)
        orig_name = (orig_apt.get("name") or "").strip() if orig_apt else ""
        dest_name = (dest_apt.get("name") or "").strip() if dest_apt else ""

        orig_label = orig_apt.get("municipality") or orig_name if orig_apt else ""
        dest_label = dest_apt.get("municipality") or dest_name if dest_apt else ""
        route_display = (
            f"**{origin}**" + (f" *({orig_label})*" if orig_label else "")
            + " -> "
            + f"**{destination}**" + (f" *({dest_label})*" if dest_label else "")
        )

        pilot_callsign = str((pilot or {}).get("callsign") or "").strip()
        pilot_info = f" · Pilot: **{pilot_callsign}**" if pilot_callsign else ""

        ac_name = ""
        if hasattr(self.bot, "aircraft_name_map") and self.bot.aircraft_name_map:
            ac_name = self.bot.aircraft_name_map.get(_get(schedule, "aircraft_id"), "")
        if not ac_name:
            ac_name = _aircraft_model(schedule) or ""
        ac_display = f"**{registration}**" + (f" ({ac_name})" if ac_name else "")

        alt = float(_get(position, "altitude", 0.0) or 0.0)
        speed = float(_get(position, "speed", 0.0) or 0.0)
        vs = float(_get(position, "vertical_speed", 0.0) or 0.0)

        fl = round(alt / 100)
        alt_str = f"FL{fl} ({int(alt):,} ft)" if alt >= 10000 else f"{int(alt):,} ft"
        spd_str = f"{int(speed)} kts"

        if vs > 300:
            phase = "Enroute (Climbing)"
        elif vs < -300:
            phase = "Enroute (Descending)"
        else:
            phase = "Enroute (Cruising)"

        distance, eta = _flight_progress(self.bot, position, destination)
        dist_str = f"{int(distance):,} NM" if distance is not None else "Calculating..."

        now_utc = _utcnow()
        now_ts = int(now_utc.timestamp())

        if status == "LANDED":
            color = discord.Color.green()
            title = f"Flight Completed · {registration}"
            tod_value = f"Welcome to **{destination}**! The flight has ended and aircraft is parked."
            eta_str = "Arrived"
        elif status == "STOPPED":
            color = discord.Color.orange()
            title = f"Aircraft Paused at TOD · {registration}"
            stopped_at = _parse_utc(_get(position, "updated_at")) or now_utc
            stopped_zulu = stopped_at.strftime("%H:%M") + "Z"
            tod_value = (
                f"**Aircraft paused at Top of Descent (since {stopped_zulu}).**\n"
                "Please resume your flight within 1 hour to prevent recovery unassignment."
            )
            eta_str = f"Paused near {destination}"
        elif eta is not None:
            eta_ts = now_ts + int(eta * 60)
            eta_dt = datetime.fromtimestamp(eta_ts, tz=timezone.utc)
            eta_zulu = eta_dt.strftime("%H:%M") + "Z"
            if eta_dt.date() != now_utc.date():
                eta_zulu = eta_dt.strftime("%d %b %H:%M") + "Z"
            eta_str = f"**{eta_zulu}** (<t:{eta_ts}:t> Local · <t:{eta_ts}:R>)"

            if eta > 40:
                color = discord.Color(QATAR_MAROON)
                title = f"Live Flight Tracker · {registration}"
                tod_min = eta - 40
                tod_ts = now_ts + int(tod_min * 60)
                tod_dt = datetime.fromtimestamp(tod_ts, tz=timezone.utc)
                tod_zulu = tod_dt.strftime("%H:%M") + "Z"
                if tod_dt.date() != now_utc.date():
                    tod_zulu = tod_dt.strftime("%d %b %H:%M") + "Z"

                tod_value = (
                    f"**~{int(tod_min)} min remaining** (<t:{tod_ts}:R>)\n"
                    f"Exact TOD Time: **{tod_zulu}** (<t:{tod_ts}:t> Local)\n"
                    f"*Take controls ~40 min prior to landing for descent.*"
                )
            else:
                color = discord.Color.gold()
                title = f"Within Arrival Window · {registration}"
                tod_value = (
                    f"**Take controls now!**\n"
                    f"Aircraft is within arrival window (~{int(eta)} min to landing).\n"
                    f"Estimated Landing: **{eta_zulu}** (<t:{eta_ts}:R>)"
                )
        else:
            color = discord.Color(QATAR_MAROON)
            title = f"Live Flight Tracker · {registration}"
            eta_str = "Calculating..."
            tod_value = "Calculating arrival window based on current ground speed..."

        embed = discord.Embed(
            title=title,
            color=color,
            timestamp=now_utc,
        )
        embed.add_field(
            name="Route & Aircraft",
            value=f"{route_display}\nAircraft: {ac_display} · Callsign: **{callsign}**{pilot_info}",
            inline=False,
        )

        if status == "ENROUTE":
            embed.add_field(
                name="Flight Status",
                value=f"**{phase}**",
                inline=True,
            )
            embed.add_field(
                name="Altitude",
                value=f"**{alt_str}**",
                inline=True,
            )
            embed.add_field(
                name="Ground Speed",
                value=f"**{spd_str}**",
                inline=True,
            )
            embed.add_field(
                name="Time Until Take Control (TOD)",
                value=tod_value,
                inline=False,
            )
            embed.add_field(
                name="Remaining Distance",
                value=f"**{dist_str}**",
                inline=True,
            )
            embed.add_field(
                name="Estimated Arrival",
                value=f"**{eta_str}**",
                inline=True,
            )
        elif status == "STOPPED":
            embed.add_field(
                name="Arrival Window",
                value=tod_value,
                inline=False,
            )
            embed.add_field(
                name="Altitude",
                value=f"**{alt_str}**",
                inline=True,
            )
            embed.add_field(
                name="Distance Remaining",
                value=f"**{dist_str}**",
                inline=True,
            )
        elif status == "LANDED":
            embed.add_field(
                name="Flight Status",
                value=tod_value,
                inline=False,
            )

        embed.set_footer(
            text="Oryxie Flight Monitor • Updates automatically every ~2 min"
        )
        return embed

    async def _update_live_flight_trackers(
        self,
        client,
        model,
        fleet,
        positions,
        schedules_by_aircraft,
    ):
        if not getattr(self, "_live_flight_trackers", None):
            return

        completed_ids = []
        for aircraft_id, tracker in list(self._live_flight_trackers.items()):
            try:
                schedule_id = tracker.get("schedule_id")
                registration = tracker.get("registration", "Aircraft")
                channel_id = tracker.get("channel_id")
                message_id = tracker.get("message_id")
                if not channel_id or not message_id:
                    continue

                position = positions.get(aircraft_id)
                aircraft_schedules = schedules_by_aircraft.get(aircraft_id, [])
                current_schedule = next(
                    (s for s in aircraft_schedules if str(_get(s, "id") or "") == schedule_id),
                    None,
                )

                if current_schedule is None:
                    status = "LANDED"
                    completed_ids.append(aircraft_id)
                else:
                    pos_state = int(_get(position, "state", -1)) if position else -1
                    is_ground = bool(_get(position, "is_on_ground", False)) if position else False
                    speed = float(_get(position, "speed", 0.0)) if position else 0.0

                    if pos_state == STOPPED_POSITION_STATE or (not is_ground and speed < 30 and pos_state != 1):
                        status = "STOPPED"
                    elif is_ground and speed < 30:
                        status = "LANDED"
                        completed_ids.append(aircraft_id)
                    else:
                        status = "ENROUTE"

                channel = self.bot.get_channel(int(channel_id))
                if channel is None:
                    try:
                        channel = await self.bot.fetch_channel(int(channel_id))
                    except Exception:
                        channel = None

                if channel:
                    try:
                        msg = await channel.fetch_message(int(message_id))
                        embed = self._build_live_flight_embed(
                            registration=registration,
                            schedule=current_schedule or tracker,
                            position=position,
                            status=status,
                        )
                        await msg.edit(embed=embed)
                    except discord.NotFound:
                        log.debug("Live flight tracker DM message %s not found; stopping tracking.", message_id)
                        completed_ids.append(aircraft_id)
                    except discord.Forbidden:
                        log.warning("Live flight tracker DM forbidden for user %s; stopping tracking.", tracker.get("discord_user_id"))
                        completed_ids.append(aircraft_id)

                if status == "LANDED":
                    if hasattr(model, "update_live_flight_tracker_status"):
                        await model.update_live_flight_tracker_status(schedule_id, "completed")
            except Exception:
                log.exception("Error updating live flight tracker for aircraft %s", aircraft_id)

        for ac_id in completed_ids:
            self._live_flight_trackers.pop(ac_id, None)


async def setup(bot):
    await bot.add_cog(StoppedFlightMonitor(bot))
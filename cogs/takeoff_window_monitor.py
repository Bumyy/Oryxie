"""Ground takeoff window monitor cog.

Monitors aircraft parked on the ground with assigned schedules and unassigns
crew if the aircraft has not departed within the designated takeoff window:
  - 6 hours at an airline hub (e.g. OTHH for Qatar Airways)
  - 12 hours at non-hub / outstation airports
  - Sends a warning DM 2 hours before the takeoff limit expires (at 4h / 10h)
  - Uses api.live_fleet_state (LiveFleetService / LiveFleetSnapshot) as the source of truth.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import discord
from discord.ext import commands, tasks

from api.if_live_manager import ScheduledFlightStatus
from cogs.airline_hubs import hubs_for_registration, takeoff_window_hours
from cogs.dm_logging import post_dm_report

log = logging.getLogger(__name__)

ORG_ID = os.getenv("IF_ORGANIZATION_ID", "").strip()
TOKEN_OWNER_ID = 1
POLL_INTERVAL_MINUTES = 2
UNAVAILABLE_STATUSES = {
    int(ScheduledFlightStatus.CANCELLED),  # 9
    int(ScheduledFlightStatus.ARRIVED),    # 11
}

GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
EXECUTIVE_ROLE_ID = int(
    os.getenv("STOPPED_FLIGHT_EXECUTIVE_ROLE_ID", "0") or 0
)

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS if_ground_takeoff_monitor_state (
    schedule_id VARCHAR(64) NOT NULL PRIMARY KEY,
    aircraft_id VARCHAR(64) NOT NULL,
    registration VARCHAR(32) NOT NULL,
    callsign VARCHAR(64) NOT NULL,
    origin_icao VARCHAR(8) NOT NULL,
    destination_icao VARCHAR(8) NOT NULL,
    parked_icao VARCHAR(8) NOT NULL,
    crew_json TEXT NOT NULL,
    assigned_since DATETIME NOT NULL,
    takeoff_limit_hours INT NOT NULL,
    warning_sent TINYINT(1) NOT NULL DEFAULT 0,
    warning_sent_at DATETIME NULL,
    unassigned TINYINT(1) NOT NULL DEFAULT 0,
    unassigned_at DATETIME NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_takeoff_aircraft (aircraft_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_utc(value: Any) -> Optional[datetime]:
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
    except (ValueError, TypeError):
        return None


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _status(schedule: Any) -> Optional[int]:
    try:
        val = _get(schedule, "status")
        return int(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _sequence(schedule: Any) -> int:
    try:
        val = _get(schedule, "sequence", 0)
        return int(val) if val is not None else 0
    except (TypeError, ValueError):
        return 0


def _crew_members(schedule: Any) -> List[Any]:
    return list(_get(schedule, "crew", []) or [])


def _crew_user_id(member: Any) -> str:
    value = _get(member, "userId") or _get(member, "user_id")
    return str(value).strip() if value else ""


def _distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_nm = 3440.065
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    lat1 = math.radians(lat1)
    lat2 = math.radians(lat2)
    val = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * radius_nm * math.asin(math.sqrt(min(1.0, val)))


class TakeoffWindowMonitor(commands.Cog):
    """Monitors ground parked aircraft with crew assignments and enforces takeoff deadlines."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._table_ready = False
        self._warned_missing_table = False
        self._memory_state: Dict[str, Dict[str, Any]] = {}
        self.monitor_takeoff_windows.start()

    def cog_unload(self):
        self.monitor_takeoff_windows.cancel()

    async def _ensure_table(self):
        if self._table_ready:
            return True
        db = getattr(self.bot, "db_manager", None)
        if db is None or getattr(db, "_pool", None) is None:
            return False
        try:
            table = await db.fetch_one(
                "SHOW TABLES LIKE %s", ("if_ground_takeoff_monitor_state",)
            )
            if not table:
                try:
                    await db.execute(TABLE_DDL)
                    table = await db.fetch_one(
                        "SHOW TABLES LIKE %s", ("if_ground_takeoff_monitor_state",)
                    )
                except Exception:
                    pass
            if table:
                self._table_ready = True
                return True
            else:
                if not self._warned_missing_table:
                    log.warning(
                        "Table 'if_ground_takeoff_monitor_state' does not exist in MySQL. "
                        "Using in-memory tracking until created. "
                        "Apply database/takeoff_window_monitor_mysql_schema.sql to persist across restarts."
                    )
                    self._warned_missing_table = True
                return False
        except Exception:
            return False

    def _nearest_airport_icao(self, position: Any) -> Optional[str]:
        airports = getattr(getattr(self.bot, "flightdata", None), "airports_db", None)
        if not airports:
            return None
        try:
            lat = float(_get(position, "latitude"))
            lon = float(_get(position, "longitude"))
        except (TypeError, ValueError):
            return None

        nearest_icao = None
        nearest_distance = float("inf")
        for icao, airport in airports.items():
            try:
                distance = _distance_nm(
                    lat,
                    lon,
                    float(airport["latitude_deg"]),
                    float(airport["longitude_deg"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_icao = str(icao).upper()

        return nearest_icao if nearest_distance <= 25 else None

    async def _executive_crew_ids(self, if_user_ids: List[str]) -> Set[str]:
        if not if_user_ids or not GUILD_ID or not EXECUTIVE_ROLE_ID:
            return set()
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            try:
                guild = await self.bot.fetch_guild(GUILD_ID)
            except Exception:
                log.warning("Could not fetch guild %s for Executive check", GUILD_ID)
                return set()
        role = guild.get_role(EXECUTIVE_ROLE_ID) if guild else None
        if not role:
            return set()

        executive_ids = set()
        pilots_model = getattr(self.bot, "pilots_model", None)
        if not pilots_model:
            return set()

        for uid in if_user_ids:
            try:
                pilot = await pilots_model.get_pilot_by_ifuserid(str(uid))
                discord_id = str((pilot or {}).get("discordid") or "")
                if not discord_id.isdigit():
                    continue
                member = guild.get_member(int(discord_id))
                if member is None:
                    try:
                        member = await guild.fetch_member(int(discord_id))
                    except discord.NotFound:
                        continue
                if member and role in member.roles:
                    executive_ids.add(str(uid))
            except Exception:
                log.exception("Error checking Executive role for IF user %s", uid)
        return executive_ids

    async def _get_state(self, schedule_id: str) -> Optional[Dict[str, Any]]:
        if self._table_ready:
            db = getattr(self.bot, "db_manager", None)
            if db is not None and getattr(db, "_pool", None) is not None:
                try:
                    row = await db.fetch_one(
                        "SELECT * FROM if_ground_takeoff_monitor_state WHERE schedule_id = %s",
                        (schedule_id,),
                    )
                    if row:
                        return dict(row)
                except Exception:
                    log.exception("Error querying if_ground_takeoff_monitor_state for %s", schedule_id)
        return self._memory_state.get(schedule_id)

    async def _save_state(self, state: Dict[str, Any]):
        schedule_id = state["schedule_id"]
        self._memory_state[schedule_id] = state

        if self._table_ready:
            db = getattr(self.bot, "db_manager", None)
            if db is not None and getattr(db, "_pool", None) is not None:
                try:
                    await db.execute(
                        """
                        INSERT INTO if_ground_takeoff_monitor_state (
                            schedule_id, aircraft_id, registration, callsign,
                            origin_icao, destination_icao, parked_icao, crew_json,
                            assigned_since, takeoff_limit_hours, warning_sent,
                            warning_sent_at, unassigned, unassigned_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        ) ON DUPLICATE KEY UPDATE
                            parked_icao = VALUES(parked_icao),
                            crew_json = VALUES(crew_json),
                            assigned_since = VALUES(assigned_since),
                            takeoff_limit_hours = VALUES(takeoff_limit_hours),
                            warning_sent = VALUES(warning_sent),
                            warning_sent_at = VALUES(warning_sent_at),
                            unassigned = VALUES(unassigned),
                            unassigned_at = VALUES(unassigned_at)
                        """,
                        (
                            state["schedule_id"],
                            state["aircraft_id"],
                            state["registration"],
                            state["callsign"],
                            state["origin_icao"],
                            state["destination_icao"],
                            state["parked_icao"],
                            state["crew_json"],
                            state["assigned_since"],
                            state["takeoff_limit_hours"],
                            1 if state.get("warning_sent") else 0,
                            state.get("warning_sent_at"),
                            1 if state.get("unassigned") else 0,
                            state.get("unassigned_at"),
                        ),
                    )
                except Exception:
                    log.exception("Error saving if_ground_takeoff_monitor_state for %s", schedule_id)

    async def _delete_state(self, schedule_id: str):
        self._memory_state.pop(schedule_id, None)
        if self._table_ready:
            db = getattr(self.bot, "db_manager", None)
            if db is not None and getattr(db, "_pool", None) is not None:
                try:
                    await db.execute(
                        "DELETE FROM if_ground_takeoff_monitor_state WHERE schedule_id = %s",
                        (schedule_id,),
                    )
                except Exception:
                    log.exception("Error deleting if_ground_takeoff_monitor_state for %s", schedule_id)

    async def _send_warning_dm(
        self,
        registration: str,
        parked_icao: str,
        elapsed_hours: int,
        crew_ids: List[str],
        schedule_id: str,
    ) -> Tuple[List[str], List[str]]:
        delivered = []
        failed = []
        pilots_model = getattr(self.bot, "pilots_model", None)
        for if_user_id in crew_ids:
            try:
                pilot = await pilots_model.get_pilot_by_ifuserid(if_user_id) if pilots_model else None
            except Exception:
                pilot = None
            discord_id = str((pilot or {}).get("discordid") or "")
            if not discord_id.isdigit():
                failed.append(f"IF user `{if_user_id}`: no linked Discord account.")
                continue
            try:
                user = self.bot.get_user(int(discord_id))
                if user is None:
                    user = await self.bot.fetch_user(int(discord_id))
                await user.send(
                    f"⚠️ **Takeoff window alert**\n"
                    f"**{registration}** has been parked on the ground at **{parked_icao or 'an unknown airport'}** "
                    f"for {max(1, elapsed_hours)} hours. "
                    f"If it has not departed within the next 2 hours, this assignment will be unassigned."
                )
                delivered.append(f"<@{discord_id}> ({(pilot or {}).get('callsign') or 'Pilot'})")
            except Exception as exc:
                log.warning("Could not DM takeoff warning to IF user %s (%s): %s", if_user_id, discord_id, exc)
                failed.append(f"<@{discord_id}>: message could not be delivered.")
        return delivered, failed

    async def _send_unassign_dm(
        self,
        registration: str,
        callsign: str,
        origin_icao: str,
        destination_icao: str,
        parked_icao: str,
        limit_hours: int,
        if_user_id: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        pilots_model = getattr(self.bot, "pilots_model", None)
        try:
            pilot = await pilots_model.get_pilot_by_ifuserid(if_user_id) if pilots_model else None
        except Exception:
            pilot = None
        discord_id = str((pilot or {}).get("discordid") or "")
        if not discord_id.isdigit():
            return None, f"IF user `{if_user_id}`: no linked Discord account."
        try:
            user = self.bot.get_user(int(discord_id))
            if user is None:
                user = await self.bot.fetch_user(int(discord_id))
            await user.send(
                f"⚠️ **Takeoff window expired**\n"
                f"Your assignment on **{registration} · {callsign}** ({origin_icao} → {destination_icao}) "
                f"was unassigned because the aircraft remained parked at **{parked_icao or 'an unknown airport'}** "
                f"for {limit_hours} hours without taking off."
            )
            return f"<@{discord_id}> ({(pilot or {}).get('callsign') or 'Pilot'})", None
        except Exception as exc:
            log.warning("Could not DM takeoff expired notice to IF user %s (%s): %s", if_user_id, discord_id, exc)
            return None, f"<@{discord_id}>: message could not be delivered."

    async def _report_dm_outcome(
        self,
        title: str,
        registration: str,
        callsign: str,
        origin: str,
        destination: str,
        limit_hours: int,
        delivered: List[str],
        failed: List[str],
    ):
        sections = [
            "**Delivered:**\n"
            + ("\n".join(f"• {r}" for r in delivered) if delivered else "• None")
        ]
        if failed:
            sections.append(
                "**Not delivered:**\n" + "\n".join(f"• {r}" for r in failed)
            )
        content = (
            f"📨 **{title}**\n"
            f"**{registration} · {callsign}** ({origin} → {destination}) · {limit_hours}-hour takeoff limit\n"
            + "\n".join(sections)
        )
        await post_dm_report(self.bot, content, log, f"takeoff window {callsign}")

    @tasks.loop(minutes=POLL_INTERVAL_MINUTES)
    async def monitor_takeoff_windows(self):
        """Periodic background task that processes parked aircraft and enforces takeoff windows."""
        try:
            await self._run_cycle()
        except Exception:
            log.exception("Unexpected error in monitor_takeoff_windows cycle")

    @monitor_takeoff_windows.before_loop
    async def before_monitor_takeoff_windows(self):
        await self.bot.wait_until_ready()

    async def _run_cycle(self):
        manager = getattr(self.bot, "if_live_manager", None)
        if not manager or not hasattr(manager, "get_live_fleet"):
            return

        await self._ensure_table()

        try:
            fleet_snapshot = await manager.get_live_fleet(ORG_ID, pilot_id=TOKEN_OWNER_ID)
        except Exception:
            log.exception("Could not obtain live fleet snapshot for takeoff window monitor")
            return

        active_tracked_schedule_ids = set()
        now = _utcnow()

        async with await manager.get_client(TOKEN_OWNER_ID) as client:
            for ac in fleet_snapshot:
                # Use live_fleet_state as the single source of truth for parked status
                if not getattr(ac, "is_parked", False):
                    continue

                registration = str(getattr(ac, "registration", "") or "").strip().upper()
                aircraft_id = str(getattr(ac, "aircraft_id", "") or "")
                raw_schedules = getattr(ac, "schedules", []) or []

                # Filter active schedules in sequence order
                active_schedules = [
                    sch
                    for sch in sorted(raw_schedules, key=_sequence)
                    if _status(sch) not in UNAVAILABLE_STATUSES
                ]
                if not active_schedules:
                    continue

                # The first active schedule is the leg currently waiting to depart
                first_leg = active_schedules[0]
                schedule_id = str(_get(first_leg, "id") or "")
                if not schedule_id:
                    continue

                crew = _crew_members(first_leg)
                crew_ids = list(dict.fromkeys(filter(None, map(_crew_user_id, crew))))
                if not crew_ids:
                    continue

                active_tracked_schedule_ids.add(schedule_id)

                callsign = str(_get(first_leg, "callsign") or "Unknown flight")
                origin_icao = str(_get(first_leg, "origin_icao") or "").strip().upper()
                destination_icao = str(_get(first_leg, "destination_icao") or "").strip().upper()

                parked_icao = (
                    self._nearest_airport_icao(ac)
                    or origin_icao
                    or "unknown airport"
                )

                limit_hours = takeoff_window_hours(registration, parked_icao)
                warning_window_hours = max(0, limit_hours - 2)

                parked_at = _parse_utc(getattr(ac, "v3_updated_at", None))
                state = await self._get_state(schedule_id)
                current_crew_json = json.dumps(crew_ids)

                if state is None:
                    # New assignment discovered on a parked aircraft
                    # The takeoff timer starts when first tracked while parked
                    assigned_since = now
                    state = {
                        "schedule_id": schedule_id,
                        "aircraft_id": aircraft_id,
                        "registration": registration,
                        "callsign": callsign,
                        "origin_icao": origin_icao,
                        "destination_icao": destination_icao,
                        "parked_icao": parked_icao,
                        "crew_json": current_crew_json,
                        "assigned_since": assigned_since,
                        "takeoff_limit_hours": limit_hours,
                        "warning_sent": 0,
                        "warning_sent_at": None,
                        "unassigned": 0,
                        "unassigned_at": None,
                    }
                    await self._save_state(state)
                    log.info(
                        "Tracking takeoff window for %s (%s, %s -> %s) parked at %s. Limit: %dh, assigned since %s.",
                        registration, callsign, origin_icao, destination_icao, parked_icao, limit_hours, assigned_since
                    )
                else:
                    # If crew changed completely, reset assignment timer
                    prev_crew = set(json.loads(state.get("crew_json") or "[]"))
                    if prev_crew != set(crew_ids):
                        state["crew_json"] = current_crew_json
                        state["assigned_since"] = now
                        state["warning_sent"] = 0
                        state["warning_sent_at"] = None
                        state["unassigned"] = 0
                        state["unassigned_at"] = None
                        await self._save_state(state)

                stored_assigned_since = _parse_utc(state["assigned_since"]) or now
                # The takeoff window cannot start before the aircraft actually parked at this airport
                if parked_at and parked_at <= now:
                    effective_assigned_since = max(stored_assigned_since, parked_at)
                else:
                    effective_assigned_since = stored_assigned_since

                elapsed = now - effective_assigned_since
                elapsed_hours = int(elapsed.total_seconds() // 3600)

                # 1. Warning Check (2 hours before limit)
                if (
                    elapsed >= timedelta(hours=warning_window_hours)
                    and elapsed < timedelta(hours=limit_hours)
                    and not state.get("warning_sent")
                ):
                    delivered, failed = await self._send_warning_dm(
                        registration,
                        parked_icao,
                        elapsed_hours,
                        crew_ids,
                        schedule_id,
                    )
                    state["warning_sent"] = 1
                    state["warning_sent_at"] = now
                    await self._save_state(state)
                    if delivered or failed:
                        await self._report_dm_outcome(
                            "Takeoff window 2-hour warning",
                            registration,
                            callsign,
                            origin_icao,
                            destination_icao,
                            limit_hours,
                            delivered,
                            failed,
                        )

                # 2. Limit Expired Check -> Unassign non-Executive crew
                elif (
                    elapsed >= timedelta(hours=limit_hours)
                    and not state.get("unassigned")
                ):
                    executive_ids = await self._executive_crew_ids(crew_ids)
                    removable_ids = [uid for uid in crew_ids if uid not in executive_ids]
                    delivered_unassign = []
                    failed_unassign = []

                    if removable_ids:
                        for if_user_id in removable_ids:
                            try:
                                await client.remove_crew(schedule_id, if_user_id)
                                log.info(
                                    "Unassigned crew %s from %s schedule %s after %dh takeoff limit expired",
                                    if_user_id, registration, schedule_id, limit_hours
                                )
                                d, f = await self._send_unassign_dm(
                                    registration,
                                    callsign,
                                    origin_icao,
                                    destination_icao,
                                    parked_icao,
                                    limit_hours,
                                    if_user_id,
                                )
                                if d:
                                    delivered_unassign.append(d)
                                if f:
                                    failed_unassign.append(f)
                            except Exception:
                                log.exception(
                                    "Could not remove crew %s from schedule %s on %s",
                                    if_user_id, schedule_id, registration
                                )
                                failed_unassign.append(f"IF user `{if_user_id}`: removal call failed.")

                    state["unassigned"] = 1
                    state["unassigned_at"] = now
                    await self._save_state(state)

                    if delivered_unassign or failed_unassign:
                        await self._report_dm_outcome(
                            "Takeoff window expired — Crew unassigned",
                            registration,
                            callsign,
                            origin_icao,
                            destination_icao,
                            limit_hours,
                            delivered_unassign,
                            failed_unassign,
                        )

        # Cleanup: Remove tracked schedules that are no longer parked legs with crew
        # (e.g. took off, departed, completed, or crew manually removed)
        all_stored_ids = list(self._memory_state.keys())
        if self._table_ready:
            db = getattr(self.bot, "db_manager", None)
            if db is not None and getattr(db, "_pool", None) is not None:
                try:
                    db_rows = await db.fetch_all("SELECT schedule_id FROM if_ground_takeoff_monitor_state")
                    all_stored_ids = list({row["schedule_id"] for row in db_rows or []} | set(all_stored_ids))
                except Exception:
                    pass

        for sid in all_stored_ids:
            if sid not in active_tracked_schedule_ids:
                await self._delete_state(sid)


async def setup(bot: commands.Bot):
    await bot.add_cog(TakeoffWindowMonitor(bot))

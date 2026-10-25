import hashlib
import logging
import os

import discord
from discord.ext import commands, tasks

from cogs.airline_hubs import hubs_for_registration
from cogs.dm_logging import post_dm_report

log = logging.getLogger(__name__)

ORG_ID = os.getenv("IF_ORGANIZATION_ID", "").strip()
TOKEN_OWNER_ID = 1
POLL_INTERVAL_MINUTES = 2
ARRIVED_STATUS = 11
UNAVAILABLE_STATUSES = {9, 11}
NEXT_LEG_READY_STATUSES = {1, 2, 3, 4, 8}
ROUTE_READY_TABLE_DDL = {
    "if_route_ready_schedule_state": """
        CREATE TABLE IF NOT EXISTS if_route_ready_schedule_state (
            schedule_id VARCHAR(64) NOT NULL PRIMARY KEY,
            last_status INT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            COLLATE=utf8mb4_unicode_ci
    """,
    "if_route_ready_dm_events": """
        CREATE TABLE IF NOT EXISTS if_route_ready_dm_events (
            event_key CHAR(64) NOT NULL PRIMARY KEY,
            arrival_schedule_id VARCHAR(64) NOT NULL,
            next_schedule_id VARCHAR(64) NOT NULL,
            if_user_id VARCHAR(64) NOT NULL,
            discord_user_id VARCHAR(32) NOT NULL,
            delivery_status VARCHAR(16) NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_if_route_ready_arrival (arrival_schedule_id),
            INDEX idx_if_route_ready_next (next_schedule_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            COLLATE=utf8mb4_unicode_ci
    """,
}


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _status(schedule):
    try:
        return int(_get(schedule, "status"))
    except (TypeError, ValueError):
        return None


def _sequence(schedule):
    try:
        return int(_get(schedule, "sequence", 0))
    except (TypeError, ValueError):
        return 0


def _crew_user_ids(schedule):
    user_ids = []
    for member in (_get(schedule, "crew", []) or []):
        user_id = next(
            (
                _get(member, key)
                for key in ("userId", "user_id", "ifuserid", "id")
                if _get(member, key) not in (None, "")
            ),
            None,
        )
        if user_id is not None:
            normalized = str(user_id).strip()
            if normalized and normalized not in user_ids:
                user_ids.append(normalized)
    return user_ids


def _arrival_candidates(
    schedules,
    observed_statuses,
    retryable_arrival_ids=None,
):
    retryable_arrival_ids = {
        str(schedule_id) for schedule_id in retryable_arrival_ids or ()
    }
    by_aircraft = {}
    for schedule in schedules:
        aircraft_id = str(_get(schedule, "aircraft_id", "") or "")
        if aircraft_id:
            by_aircraft.setdefault(aircraft_id, []).append(schedule)

    candidates = []
    for aircraft_id, aircraft_schedules in by_aircraft.items():
        aircraft_schedules.sort(key=_sequence)
        for index, arrived in enumerate(aircraft_schedules[:-1]):
            route_id = str(_get(arrived, "id", "") or "")
            if (
                not route_id
                or _status(arrived) != ARRIVED_STATUS
                or (
                    observed_statuses.get(route_id) == ARRIVED_STATUS
                    and route_id not in retryable_arrival_ids
                )
            ):
                continue

            next_route = aircraft_schedules[index + 1]
            if (
                _get(next_route, "id") in (None, "")
                or _status(next_route) not in NEXT_LEG_READY_STATUSES
            ):
                continue

            destination = str(
                _get(arrived, "destination_icao", "") or ""
            ).strip().upper()
            origin = str(
                _get(next_route, "origin_icao", "") or ""
            ).strip().upper()
            if not destination or destination != origin:
                continue

            crew_ids = _crew_user_ids(next_route)
            if crew_ids:
                candidates.append((aircraft_id, arrived, next_route, crew_ids))

    return candidates


def _event_key(arrived_route_id, next_route_id, if_user_id):
    payload = "|".join(
        (str(arrived_route_id), str(next_route_id), str(if_user_id))
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RouteReadyNotifications(commands.Cog):
    """Notify assigned next-leg crew when an aircraft lands at their departure airport."""

    def __init__(self, bot):
        self.bot = bot
        self._tables_ready = False
        self.poll_route_readiness.start()

    def cog_unload(self):
        self.poll_route_readiness.cancel()

    @tasks.loop(minutes=POLL_INTERVAL_MINUTES)
    async def poll_route_readiness(self):
        if not ORG_ID:
            log.error("IF_ORGANIZATION_ID is not configured; route-ready notifications are disabled.")
            return

        manager = getattr(self.bot, "if_live_manager", None)
        db = getattr(self.bot, "db_manager", None)
        pilots_model = getattr(self.bot, "pilots_model", None)
        if manager is None or db is None or pilots_model is None:
            log.error("Route-ready notifications require Live API and database services.")
            return

        try:
            await self._ensure_tables(db)
            async with await manager.get_client(TOKEN_OWNER_ID) as client:
                schedules = await self._get_organization_schedules(client)
                aircraft = await client.list_organization_aircraft(ORG_ID)
                registrations = {
                    str(_get(item, "id")): str(_get(item, "registration") or "")
                    for item in aircraft or []
                    if _get(item, "id") not in (None, "")
                }
                await self._process_schedules(
                    client,
                    db,
                    pilots_model,
                    schedules,
                    registrations,
                )
        except Exception:
            log.exception("Route-ready notification polling failed.")

    @poll_route_readiness.before_loop
    async def before_poll_route_readiness(self):
        await self.bot.wait_until_ready()

    async def _ensure_tables(self, db):
        if self._tables_ready:
            return

        for table_name in (
            "if_route_ready_schedule_state",
            "if_route_ready_dm_events",
        ):
            table = await db.fetch_one("SHOW TABLES LIKE %s", (table_name,))
            if not table:
                await db.execute(ROUTE_READY_TABLE_DDL[table_name])
                table = await db.fetch_one("SHOW TABLES LIKE %s", (table_name,))
                if not table:
                    raise RuntimeError(
                        f"Required route-ready table {table_name} is missing "
                        "and could not be created. Grant CREATE TABLE "
                        "permissions to the bot database account or apply "
                        "database/route_ready_notifications_mysql_schema.sql."
                    )
        self._tables_ready = True

    async def _get_organization_schedules(self, client):
        schedules = []
        page_number = 1
        total_pages = 1

        while page_number <= total_pages:
            page = await client.list_organization_schedules(
                ORG_ID,
                page=page_number,
                page_size=100,
            )
            schedules.extend(page.items or [])
            total_pages = max(1, int(page.total_pages or 1))
            page_number += 1

        return schedules

    async def _process_schedules(
        self,
        client,
        db,
        pilots_model,
        schedules,
        registrations,
    ):
        state_rows = await db.fetch_all(
            "SELECT schedule_id, last_status FROM if_route_ready_schedule_state"
        )
        observed_statuses = {
            str(row["schedule_id"]): int(row["last_status"])
            for row in state_rows or []
        }
        failed_event_rows = await db.fetch_all(
            "SELECT DISTINCT arrival_schedule_id "
            "FROM if_route_ready_dm_events "
            "WHERE delivery_status IN ('failed', 'failed_final')"
        )
        retryable_arrival_ids = {
            str(row["arrival_schedule_id"])
            for row in failed_event_rows or []
        }
        schedules_by_id = {
            str(_get(schedule, "id")): schedule
            for schedule in schedules
            if _get(schedule, "id") not in (None, "")
        }

        for route_id, schedule in schedules_by_id.items():
            current_status = _status(schedule)
            if current_status is None:
                log.warning("Skipping schedule %s with an invalid status.", route_id)
                continue

            old_status = observed_statuses.get(route_id)
            if old_status is None:
                await db.execute(
                    """
                    INSERT IGNORE INTO if_route_ready_schedule_state
                        (schedule_id, last_status)
                    VALUES (%s, %s)
                    """,
                    (route_id, current_status),
                )
                continue

            if current_status != ARRIVED_STATUS:
                await db.execute(
                    """
                    INSERT INTO if_route_ready_schedule_state
                        (schedule_id, last_status)
                    VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE last_status = VALUES(last_status)
                    """,
                    (route_id, current_status),
                )

        candidates = _arrival_candidates(
            schedules,
            observed_statuses,
            retryable_arrival_ids,
        )
        positions = {}
        for aircraft_id, arrived, next_route, if_user_ids in candidates:
            position = positions.get(aircraft_id)
            if position is None:
                position = await client.get_aircraft_position(aircraft_id)
                positions[aircraft_id] = position
            if not _get(position, "is_on_ground", False):
                continue

            registration = str(
                registrations.get(aircraft_id, "")
                or aircraft_id
            )
            airport = str(
                _get(arrived, "destination_icao", "") or "the destination airport"
            ).strip().upper()
            next_callsign = str(
                _get(next_route, "callsign", "") or "your next scheduled flight"
            )
            next_destination = str(
                _get(next_route, "destination_icao", "") or "unknown"
            ).strip().upper()
            next_schedule_id = str(_get(next_route, "id"))
            arrived_schedule_id = str(_get(arrived, "id"))
            hubs = hubs_for_registration(registration)
            takeoff_window_hours = 6 if airport in hubs else 12
            airport_type = "hub" if airport in hubs else "non-hub"
            attempted_delivery = False
            retry_pending = False
            delivered_recipients = []
            failed_recipients = []

            for if_user_id in if_user_ids:
                event_key = _event_key(
                    arrived_schedule_id,
                    next_schedule_id,
                    if_user_id,
                )
                existing_event = await db.fetch_one(
                    "SELECT delivery_status, discord_user_id "
                    "FROM if_route_ready_dm_events "
                    "WHERE event_key = %s",
                    (event_key,),
                )
                if existing_event:
                    existing_status = existing_event.get("delivery_status")
                    if existing_status == "delivered":
                        discord_user_id = str(
                            existing_event.get("discord_user_id") or ""
                        )
                        delivered_recipients.append(
                            f"<@{discord_user_id}>" if discord_user_id else
                            f"IF user `{if_user_id}`"
                        )
                        attempted_delivery = True
                        continue
                    if existing_status == "failed_final":
                        discord_user_id = str(
                            existing_event.get("discord_user_id") or ""
                        )
                        failed_recipients.append(
                            f"<@{discord_user_id}>: prior DM attempts failed."
                            if discord_user_id
                            else f"IF user `{if_user_id}`: prior DM attempts failed."
                        )
                        attempted_delivery = True
                        continue
                    if existing_status != "failed":
                        attempted_delivery = True
                        continue
                    is_retry = True
                else:
                    is_retry = False

                delivery_status = "failed_final" if is_retry else "failed"
                discord_user_id = ""
                try:
                    pilot = await pilots_model.get_pilot_by_ifuserid(if_user_id)
                except Exception:
                    log.exception(
                        "Could not look up pilot %s for route-ready DM "
                        "on schedule %s.",
                        if_user_id,
                        next_schedule_id,
                    )
                    failed_recipients.append(
                        f"IF user `{if_user_id}`: pilot lookup failed."
                    )
                else:
                    discord_user_id = str(
                        (pilot or {}).get("discordid") or ""
                    ).strip()
                    if not discord_user_id.isdigit():
                        log.warning(
                            "No Discord account is linked to Infinite Flight user %s "
                            "on next schedule %s.",
                            if_user_id,
                            next_schedule_id,
                        )
                        failed_recipients.append(
                            f"IF user `{if_user_id}`: no valid Discord account is linked."
                        )
                    else:
                        try:
                            user = self.bot.get_user(int(discord_user_id))
                            if user is None:
                                user = await self.bot.fetch_user(int(discord_user_id))
                            await user.send(
                                f"🛬 **Aircraft {registration} is ready for your next flight.**\n"
                                f"It is parked at **{airport}** for your scheduled flight "
                                f"**{next_callsign}**: {airport} → {next_destination}.\n"
                                f"Please take off within **{takeoff_window_hours} hours** "
                                f"({airport_type} airport)."
                            )
                            delivery_status = "delivered"
                            delivered_recipients.append(
                                f"<@{discord_user_id}> "
                                f"({(pilot or {}).get('callsign') or 'Pilot'})"
                            )
                            log.info(
                                "Sent route-ready DM to pilot %s (%s) for %s on schedule %s; "
                                "takeoff window is %s hours from %s.",
                                if_user_id,
                                discord_user_id,
                                registration,
                                next_schedule_id,
                                takeoff_window_hours,
                                airport,
                            )
                        except discord.Forbidden as exc:
                            if getattr(exc, "code", None) == 50278:
                                reason = (
                                    "Discord says this account shares no server "
                                    "with the bot; verify the linked account and "
                                    "server membership."
                                )
                                log.warning(
                                    "Cannot DM route-ready pilot %s (%s) for %s: "
                                    "no mutual guild.",
                                    if_user_id,
                                    discord_user_id,
                                    registration,
                                )
                            else:
                                reason = (
                                    "Discord rejected the DM; check the linked "
                                    "account's privacy settings."
                                )
                                log.warning(
                                    "Discord rejected route-ready DM to pilot %s "
                                    "(%s), error code %s.",
                                    if_user_id,
                                    discord_user_id,
                                    getattr(exc, "code", "unknown"),
                                )
                            failed_recipients.append(
                                f"<@{discord_user_id}> "
                                f"({(pilot or {}).get('callsign') or 'Pilot'}): "
                                + reason
                            )
                        except discord.HTTPException:
                            failed_recipients.append(
                                f"<@{discord_user_id}> "
                                f"({(pilot or {}).get('callsign') or 'Pilot'}): "
                                "Discord could not deliver the DM."
                            )
                            log.exception(
                                "Discord failed to deliver route-ready DM to pilot %s.",
                                if_user_id,
                            )
                        except Exception:
                            failed_recipients.append(
                                f"<@{discord_user_id}> "
                                f"({(pilot or {}).get('callsign') or 'Pilot'}): "
                                "an unexpected error prevented DM delivery."
                            )
                            log.exception(
                                "Unexpected error sending route-ready DM to pilot %s.",
                                if_user_id,
                            )
                try:
                    await db.execute(
                        """
                        INSERT INTO if_route_ready_dm_events
                            (event_key, arrival_schedule_id, next_schedule_id,
                             if_user_id, discord_user_id, delivery_status)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            discord_user_id = VALUES(discord_user_id),
                            delivery_status = VALUES(delivery_status),
                            created_at = CURRENT_TIMESTAMP
                        """,
                        (
                            event_key,
                            arrived_schedule_id,
                            next_schedule_id,
                            if_user_id,
                            discord_user_id,
                            delivery_status,
                        ),
                    )
                except Exception:
                    log.exception(
                        "Could not save route-ready DM event for IF user %s "
                        "on schedule %s.",
                        if_user_id,
                        next_schedule_id,
                    )
                if delivery_status == "failed":
                    retry_pending = True
                else:
                    attempted_delivery = True

            report_posted = True
            if delivered_recipients or failed_recipients:
                report_posted = await self._send_dm_recipient_report(
                    registration=registration,
                    callsign=next_callsign,
                    origin=airport,
                    destination=next_destination,
                    takeoff_window_hours=takeoff_window_hours,
                    delivered_recipients=delivered_recipients,
                    failed_recipients=failed_recipients,
                )

            if report_posted and failed_recipients:
                await db.execute(
                    """
                    UPDATE if_route_ready_dm_events
                    SET delivery_status = 'failed_reported'
                    WHERE arrival_schedule_id = %s
                      AND delivery_status = 'failed_final'
                    """,
                    (arrived_schedule_id,),
                )

            if attempted_delivery and not retry_pending and report_posted:
                await db.execute(
                    """
                    INSERT INTO if_route_ready_schedule_state
                        (schedule_id, last_status)
                    VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE last_status = VALUES(last_status)
                    """,
                    (arrived_schedule_id, ARRIVED_STATUS),
                )

    async def _send_dm_recipient_report(
        self,
        registration,
        callsign,
        origin,
        destination,
        takeoff_window_hours,
        delivered_recipients,
        failed_recipients,
    ):
        sections = [
            "**Delivered:**\n"
            + (
                "\n".join(f"• {recipient}" for recipient in delivered_recipients)
                if delivered_recipients
                else "• None"
            )
        ]
        if failed_recipients:
            sections.append(
                "**Not delivered:**\n"
                + "\n".join(f"• {recipient}" for recipient in failed_recipients)
            )

        return await post_dm_report(
            self.bot,
            (
                f"📨 **Route-ready DM report**\n"
                f"**{registration} · {callsign}** "
                f"({origin} → {destination}) · "
                f"{takeoff_window_hours}-hour takeoff window\n"
                + "\n".join(sections)
            ),
            log,
            f"route-ready flight {callsign}",
        )


async def setup(bot):
    await bot.add_cog(RouteReadyNotifications(bot))

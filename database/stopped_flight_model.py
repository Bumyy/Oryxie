from datetime import timedelta
from typing import Optional

from .manager import DatabaseManager


class StoppedFlightModel:
    """Database operations for stopped-flight incidents and recovery DMs."""

    _INCIDENT_FIELDS = {
        "aircraft_id",
        "registration",
        "callsign",
        "origin_icao",
        "destination_icao",
        "original_crew_json",
        "stopped_since",
        "lifecycle",
        "eta_warning_sent",
        "eta_warning_attempts",
        "eta_warning_last_attempt_at",
        "test_wave_rechecked",
        "one_hour_sent",
        "two_hour_sent",
        "three_hour_sent",
        "four_hour_sent",
        "claim_user_id",
        "alert_channel_id",
        "alert_message_id",
    }

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    async def ensure_tables(self) -> None:
        for table_name in (
            "if_stopped_flight_incidents",
            "if_stopped_flight_dm_events",
            "if_aircraft_route_availability",
        ):
            if not await self.db.fetch_one("SHOW TABLES LIKE %s", (table_name,)):
                raise RuntimeError(
                    f"Required table {table_name} is missing; apply "
                    "database/stopped_flight_monitor_mysql_schema.sql"
                )
        await self._ensure_eta_warning_columns()

    async def _ensure_eta_warning_columns(self) -> None:
        columns = (
            (
                "eta_warning_attempts",
                "TINYINT UNSIGNED NOT NULL DEFAULT 0",
            ),
            (
                "eta_warning_last_attempt_at",
                "DATETIME NULL",
            ),
            (
                "test_wave_rechecked",
                "TINYINT(1) NOT NULL DEFAULT 0",
            ),
        )
        for column_name, column_definition in columns:
            if not await self.db.fetch_one(
                "SHOW COLUMNS FROM if_stopped_flight_incidents LIKE %s",
                (column_name,),
            ):
                await self.db.execute(
                    "ALTER TABLE if_stopped_flight_incidents "
                    f"ADD COLUMN {column_name} {column_definition}"
                )

    async def try_start_eta_warning_attempt(
        self,
        schedule_id: str,
        attempted_at,
        retry_after_minutes: int = 5,
        max_attempts: int = 2,
    ) -> bool:
        retry_cutoff = attempted_at - timedelta(minutes=retry_after_minutes)
        changed = await self.db.execute(
            """
            UPDATE if_stopped_flight_incidents
            SET eta_warning_attempts = eta_warning_attempts + 1,
                eta_warning_last_attempt_at = %s
            WHERE schedule_id = %s
              AND eta_warning_sent = 0
              AND eta_warning_attempts < %s
              AND (
                  eta_warning_last_attempt_at IS NULL
                  OR eta_warning_last_attempt_at <= %s
              )
            """,
            (
                attempted_at,
                str(schedule_id),
                int(max_attempts),
                retry_cutoff,
            ),
        )
        return bool(changed)

    async def has_aircraft_status_table(self) -> bool:
        return bool(
            await self.db.fetch_one(
                "SHOW TABLES LIKE %s",
                ("if_aircraft_status_logs",),
            )
        )

    async def get_aircraft_statuses(self) -> dict[str, str]:
        rows = await self.db.fetch_all(
            "SELECT aircraft_id, status_text FROM if_aircraft_status_logs"
        )
        return {
            str(row["aircraft_id"]): str(row["status_text"])
            for row in rows or []
        }

    async def set_aircraft_status(
        self,
        aircraft_id: str,
        registration: str,
        status_text: str,
    ) -> int:
        return await self.db.execute(
            """
            INSERT INTO if_aircraft_status_logs
                (aircraft_id, registration, status_text)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                registration = VALUES(registration),
                status_text = VALUES(status_text)
            """,
            (str(aircraft_id), registration, status_text),
        )

    async def sync_route_availability(
        self,
        aircraft_id: str,
        registration: str,
        has_active_routes: bool,
    ) -> None:
        existing = await self.db.fetch_one(
            "SELECT has_active_routes FROM if_aircraft_route_availability "
            "WHERE aircraft_id = %s",
            (str(aircraft_id),),
        )
        active_value = int(has_active_routes)
        if existing is None:
            await self.db.execute(
                """
                INSERT IGNORE INTO if_aircraft_route_availability
                    (aircraft_id, registration, has_active_routes, alert_sent)
                VALUES (%s, %s, %s, 1)
                """,
                (str(aircraft_id), registration, active_value),
            )
            return

        if has_active_routes:
            await self.db.execute(
                """
                UPDATE if_aircraft_route_availability
                SET registration = %s, has_active_routes = 1, alert_sent = 1
                WHERE aircraft_id = %s
                """,
                (registration, str(aircraft_id)),
            )
        elif bool(existing["has_active_routes"]):
            await self.db.execute(
                """
                UPDATE if_aircraft_route_availability
                SET registration = %s, has_active_routes = 0, alert_sent = 0
                WHERE aircraft_id = %s AND has_active_routes = 1
                """,
                (registration, str(aircraft_id)),
            )

    async def list_pending_route_exhaustion_alerts(self) -> list[dict]:
        return await self.db.fetch_all(
            """
            SELECT aircraft_id, registration
            FROM if_aircraft_route_availability
            WHERE has_active_routes = 0 AND alert_sent = 0
            """
        )

    async def mark_route_exhaustion_alert_sent(self, aircraft_id: str) -> int:
        return await self.db.execute(
            """
            UPDATE if_aircraft_route_availability
            SET alert_sent = 1
            WHERE aircraft_id = %s AND has_active_routes = 0
            """,
            (str(aircraft_id),),
        )

    async def get_incident(self, schedule_id: str) -> Optional[dict]:
        return await self.db.fetch_one(
            "SELECT * FROM if_stopped_flight_incidents WHERE schedule_id = %s",
            (str(schedule_id),),
        )

    async def get_incident_state(self, schedule_id: str) -> Optional[dict]:
        return await self.db.fetch_one(
            "SELECT lifecycle, claim_user_id FROM if_stopped_flight_incidents "
            "WHERE schedule_id = %s",
            (str(schedule_id),),
        )

    async def create_incident(
        self,
        schedule_id: str,
        aircraft_id: str,
        registration: str,
        callsign: str,
        origin_icao: str,
        destination_icao: str,
        original_crew_json: str,
    ) -> int:
        return await self.db.execute(
            """
            INSERT IGNORE INTO if_stopped_flight_incidents
                (schedule_id, aircraft_id, registration, callsign,
                 origin_icao, destination_icao, original_crew_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(schedule_id),
                str(aircraft_id),
                registration,
                callsign,
                origin_icao,
                destination_icao,
                original_crew_json,
            ),
        )

    async def update_incident(
        self,
        schedule_id: str,
        values: dict,
        expected_lifecycle: str | None = None,
    ) -> int:
        if not values:
            return 0
        invalid_fields = set(values) - self._INCIDENT_FIELDS
        if invalid_fields:
            raise ValueError(
                f"Unsupported stopped-flight incident fields: "
                f"{', '.join(sorted(invalid_fields))}"
            )

        assignments = ", ".join(f"{field} = %s" for field in values)
        args = list(values.values())
        query = (
            f"UPDATE if_stopped_flight_incidents SET {assignments} "
            "WHERE schedule_id = %s"
        )
        args.append(str(schedule_id))
        if expected_lifecycle is not None:
            query += " AND lifecycle = %s"
            args.append(expected_lifecycle)
        return await self.db.execute(query, tuple(args))

    async def list_incidents(self, lifecycles: tuple[str, ...]) -> list[dict]:
        if not lifecycles:
            return []
        placeholders = ", ".join(["%s"] * len(lifecycles))
        return await self.db.fetch_all(
            "SELECT schedule_id, lifecycle FROM if_stopped_flight_incidents "
            f"WHERE lifecycle IN ({placeholders})",
            tuple(lifecycles),
        )

    async def list_claimable_incidents(self) -> list[dict]:
        return await self.db.fetch_all(
            "SELECT schedule_id FROM if_stopped_flight_incidents "
            "WHERE lifecycle IN ('available', 'claiming')"
        )

    async def get_dm_events(
        self,
        schedule_id: str,
        delivered_only: bool = False,
        with_message_ids: bool = False,
        recovery_only: bool = False,
    ) -> list[dict]:
        fields = "if_user_id, channel_id, message_id" if with_message_ids else "if_user_id"
        query = (
            f"SELECT {fields} FROM if_stopped_flight_dm_events "
            "WHERE schedule_id = %s"
        )
        args = [str(schedule_id)]
        if delivered_only:
            query += " AND delivery_status = 'delivered'"
        if recovery_only:
            query += " AND wave > 0"
        if with_message_ids:
            query += " AND channel_id IS NOT NULL AND message_id IS NOT NULL"
        return await self.db.fetch_all(query, tuple(args))

    async def get_active_pilots(self) -> list[dict]:
        return await self.db.fetch_all(
            "SELECT callsign, discordid, ifuserid FROM pilots "
            "WHERE status = 1 AND discordid IS NOT NULL AND ifuserid IS NOT NULL"
        )

    async def record_dm_event(
        self,
        schedule_id: str,
        if_user_id: str,
        discord_user_id: str,
        wave: int,
        flight_count_7d: int = 0,
        channel_id: str | None = None,
        message_id: str | None = None,
        delivery_status: str = "delivered",
    ) -> int:
        return await self.db.execute(
            """
            INSERT INTO if_stopped_flight_dm_events
                (schedule_id, if_user_id, discord_user_id, wave,
                 flight_count_7d, channel_id, message_id, delivery_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                wave = VALUES(wave),
                flight_count_7d = VALUES(flight_count_7d),
                channel_id = VALUES(channel_id),
                message_id = VALUES(message_id),
                delivery_status = VALUES(delivery_status)
            """,
            (
                str(schedule_id),
                str(if_user_id),
                str(discord_user_id),
                int(wave),
                int(flight_count_7d),
                str(channel_id) if channel_id is not None else None,
                str(message_id) if message_id is not None else None,
                delivery_status,
            ),
        )

    async def try_claim(self, schedule_id: str, discord_user_id: str) -> bool:
        changed = await self.db.execute(
            """
            UPDATE if_stopped_flight_incidents
            SET lifecycle = 'claiming', claim_user_id = %s
            WHERE schedule_id = %s AND lifecycle = 'available'
            """,
            (str(discord_user_id), str(schedule_id)),
        )
        return bool(changed)

    async def release_claim(self, schedule_id: str) -> int:
        return await self.db.execute(
            """
            UPDATE if_stopped_flight_incidents
            SET lifecycle = 'available', claim_user_id = NULL
            WHERE schedule_id = %s AND lifecycle = 'claiming'
            """,
            (str(schedule_id),),
        )

    async def confirm_claim(self, schedule_id: str, discord_user_id: str) -> int:
        return await self.db.execute(
            """
            UPDATE if_stopped_flight_incidents
            SET lifecycle = 'claimed'
            WHERE schedule_id = %s AND lifecycle = 'claiming'
                AND claim_user_id = %s
            """,
            (str(schedule_id), str(discord_user_id)),
        )

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
        ):
            if not await self.db.fetch_one("SHOW TABLES LIKE %s", (table_name,)):
                raise RuntimeError(
                    f"Required table {table_name} is missing; apply "
                    "database/stopped_flight_monitor_mysql_schema.sql"
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
    ) -> list[dict]:
        fields = "if_user_id, channel_id, message_id" if with_message_ids else "if_user_id"
        query = (
            f"SELECT {fields} FROM if_stopped_flight_dm_events "
            "WHERE schedule_id = %s"
        )
        args = [str(schedule_id)]
        if delivered_only:
            query += " AND delivery_status = 'delivered'"
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

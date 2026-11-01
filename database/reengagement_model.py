"""Database model for pilot re-engagement and ready aircraft nudges."""

from datetime import datetime
from typing import Any, Dict, List, Optional


class ReengagementModel:
    """Manages the `if_aircraft_reengagement_logs` table and candidate queries."""

    def __init__(self, db_manager):
        self.db = db_manager

    async def get_inactive_pilot_candidates(
        self,
        min_days: int = 14,
        max_days: int = 180,
        cooldown_days: int = 7,
    ) -> List[Dict[str, Any]]:
        """Return active pilots whose last PIREP was between min_days and max_days ago,

        and who have not received a re-engagement nudge within the cooldown_days window.
        """
        query = """
            SELECT 
                p.id AS pilot_id,
                p.callsign,
                p.name AS pilot_name,
                p.discordid,
                p.ifuserid,
                MAX(pr.date) AS last_flight_date,
                DATEDIFF(CURDATE(), MAX(pr.date)) AS days_inactive
            FROM pilots p
            INNER JOIN pireps pr ON pr.pilotid = p.id
            WHERE p.status = 1 
              AND p.discordid IS NOT NULL 
              AND p.discordid != ''
              AND p.discordid NOT IN (
                  SELECT discord_user_id 
                  FROM if_aircraft_reengagement_logs 
                  WHERE sent_at >= NOW() - INTERVAL %s DAY
                    AND delivery_status NOT IN ('failed')
              )
            GROUP BY p.id, p.callsign, p.name, p.discordid, p.ifuserid
            HAVING last_flight_date BETWEEN DATE_SUB(CURDATE(), INTERVAL %s DAY)
                                        AND DATE_SUB(CURDATE(), INTERVAL %s DAY)
            ORDER BY last_flight_date DESC
        """
        return await self.db.fetch_all(query, (cooldown_days, max_days, min_days)) or []

    async def is_pilot_on_cooldown(
        self, discord_user_id: str, cooldown_days: int = 7
    ) -> bool:
        """Check whether a pilot has been contacted recently or has snoozed."""
        row = await self.db.fetch_one(
            """
            SELECT id FROM if_aircraft_reengagement_logs
            WHERE discord_user_id = %s
              AND sent_at >= NOW() - INTERVAL %s DAY
              AND delivery_status NOT IN ('failed')
            LIMIT 1
            """,
            (str(discord_user_id), cooldown_days),
        )
        return bool(row)

    async def record_nudge(
        self,
        pilot_id: Optional[int],
        discord_user_id: str,
        if_user_id: Optional[str],
        aircraft_id: str,
        registration: str,
        aircraft_model: Optional[str],
        origin_icao: str,
        destination_icao: str,
        channel_id: Optional[str] = None,
        message_id: Optional[str] = None,
        delivery_status: str = "delivered",
    ) -> int:
        """Insert a re-engagement nudge log record."""
        return await self.db.execute(
            """
            INSERT INTO if_aircraft_reengagement_logs (
                pilot_id, discord_user_id, if_user_id, aircraft_id,
                registration, aircraft_model, origin_icao, destination_icao,
                channel_id, message_id, delivery_status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                pilot_id,
                str(discord_user_id),
                str(if_user_id) if if_user_id else None,
                str(aircraft_id),
                str(registration),
                aircraft_model,
                origin_icao,
                destination_icao,
                str(channel_id) if channel_id else None,
                str(message_id) if message_id else None,
                delivery_status,
            ),
        )

    async def get_last_nudge_for_registration(
        self, registration: str
    ) -> Optional[Dict[str, Any]]:
        """Return the most recent nudge record for a specific aircraft registration."""
        return await self.db.fetch_one(
            """
            SELECT * FROM if_aircraft_reengagement_logs
            WHERE registration = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (str(registration),),
        )

    async def get_last_session_time(self) -> Optional[datetime]:
        """Return the timestamp of the most recent re-engagement dispatch."""
        row = await self.db.fetch_one(
            """
            SELECT MAX(sent_at) as last_sent
            FROM if_aircraft_reengagement_logs
            WHERE delivery_status NOT IN ('failed')
            """
        )
        if row and row.get("last_sent"):
            return row["last_sent"]
        return None

    async def update_nudge_status(
        self,
        message_id: str,
        new_status: str,
    ) -> int:
        """Update the delivery/interaction status for a specific sent nudge."""
        return await self.db.execute(
            """
            UPDATE if_aircraft_reengagement_logs
            SET delivery_status = %s
            WHERE message_id = %s
            """,
            (new_status, str(message_id)),
        )

    async def snooze_pilot(
        self,
        discord_user_id: str,
        pilot_id: Optional[int] = None,
        snooze_days: int = 30,
    ) -> int:
        """Record a snooze action so the pilot won't be contacted for `snooze_days`."""
        return await self.db.execute(
            """
            INSERT INTO if_aircraft_reengagement_logs (
                pilot_id, discord_user_id, if_user_id, aircraft_id,
                registration, aircraft_model, origin_icao, destination_icao,
                delivery_status
            ) VALUES (%s, %s, NULL, 'SNOOZE', 'SNOOZE', 'SNOOZE', 'ZZZZ', 'ZZZZ', 'snoozed')
            """,
            (pilot_id, str(discord_user_id)),
        )

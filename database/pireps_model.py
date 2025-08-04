import discord
from database.manager import DatabaseManager

class PirepsModel:
    """
    Handles all database operations related to the 'pireps' table.
    """
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def _format_flight_time(self, seconds: int) -> str:
        """Converts flight time in seconds to a HH:MM:SS string."""
        if seconds is None:
            return "N/A"
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    async def get_pending_pireps(self) -> list[dict]:
        """
        Fetches all PIREPs with a status of 0 (pending), joining with the pilots
        and aircraft tables to get their names.

        Returns:
            A list of dictionaries, where each dictionary represents a pending PIREP
            with pilot and aircraft information included.
        """

        query = """
            SELECT
                p.id AS pirep_id,
                p.flightnum,
                p.departure,
                p.arrival,
                p.flighttime,
                p.pilotid,
                p.fuelused,
                p.date,
                pi.name AS pilot_name,
                a.name AS aircraft_name
            FROM
                pireps AS p
            INNER JOIN
                pilots AS pi ON p.pilotid = pi.id
            INNER JOIN
                aircraft AS a ON p.aircraftid = a.id
            WHERE
                p.status = %s
            ORDER BY
                p.date ASC
        """
        args = (0,)
        
        pending_reports = await self.db.fetch_all(query, args)
        
        for report in pending_reports:
            report['formatted_flighttime'] = self._format_flight_time(report.get('flighttime'))

        return pending_reports
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
                p.multi,
                pi.name AS pilot_name,
                pi.ifuserid,
                pi.ifc,
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

    async def update_pirep_status(self, pirep_id: int, status: int, rejected_by: str = None, rejection_reason: str = None) -> int:
        """
        Updates the status of a PIREP and optionally sets rejection details.
        
        Args:
            pirep_id: The ID of the PIREP to update
            status: The new status (0=pending, 1=accepted, 2=rejected)
            rejected_by: Discord ID of who rejected it (for status=2)
            rejection_reason: Reason for rejection (for status=2)
            
        Returns:
            The number of rows affected by the update
        """
        if status == 2:  # Rejected
            query = """
                UPDATE pireps 
                SET status = %s, rejected_by = %s, rejection_reason = %s, rejection_date = NOW()
                WHERE id = %s
            """
            args = (status, rejected_by, rejection_reason, pirep_id)
        else:  # Accepted or Pending
            query = """
                UPDATE pireps 
                SET status = %s
                WHERE id = %s
            """
            args = (status, pirep_id)
        
        return await self.db.execute(query, args)

    async def get_accepted_pireps(self) -> list[dict]:
        """
        Fetches all PIREPs with a status of 1 (accepted), joining with the pilots
        and aircraft tables to get their names.

        Returns:
            A list of dictionaries, where each dictionary represents an accepted PIREP
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
                p.multi,
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
                p.date DESC
        """
        args = (1,)
        
        accepted_reports = await self.db.fetch_all(query, args)
        
        for report in accepted_reports:
            report['formatted_flighttime'] = self._format_flight_time(report.get('flighttime'))

        return accepted_reports

    async def get_rejected_pireps_last_10_days(self) -> list[dict]:
        """
        Fetches all PIREPs with a status of 2 (rejected) from the last 10 days,
        joining with the pilots and aircraft tables to get their names.
        Also includes rejection reason and who rejected it if available.

        Returns:
            A list of dictionaries, where each dictionary represents a rejected PIREP
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
                p.multi,
                p.rejected_by,
                p.rejection_reason,
                p.rejection_date,
                pi.name AS pilot_name,
                pi.ifuserid,
                pi.ifc,
                a.name AS aircraft_name
            FROM
                pireps AS p
            INNER JOIN
                pilots AS pi ON p.pilotid = pi.id
            INNER JOIN
                aircraft AS a ON p.aircraftid = a.id
            WHERE
                p.status = %s
                AND p.rejection_date >= DATE_SUB(NOW(), INTERVAL 10 DAY)
            ORDER BY
                p.rejection_date DESC
        """
        args = (2,)
        
        rejected_reports = await self.db.fetch_all(query, args)
        
        for report in rejected_reports:
            report['formatted_flighttime'] = self._format_flight_time(report.get('flighttime'))

        return rejected_reports

    async def get_pirep_by_id(self, pirep_id: int) -> dict:
        """
        Fetches a single PIREP by its ID with pilot and aircraft information.
        
        Args:
            pirep_id: The ID of the PIREP to fetch
            
        Returns:
            A dictionary representing the PIREP or None if not found
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
                p.multi,
                p.status,
                p.rejected_by,
                p.rejection_reason,
                p.rejection_date,
                pi.name AS pilot_name,
                pi.ifuserid,
                pi.ifc,
                a.name AS aircraft_name
            FROM
                pireps AS p
            INNER JOIN
                pilots AS pi ON p.pilotid = pi.id
            INNER JOIN
                aircraft AS a ON p.aircraftid = a.id
            WHERE
                p.id = %s
        """
        args = (pirep_id,)
        
        pirep = await self.db.fetch_one(query, args)
        
        if pirep:
            pirep['formatted_flighttime'] = self._format_flight_time(pirep.get('flighttime'))
        
        return pirep
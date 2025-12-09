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
                p.date DESC
        """
        args = (0,)
        
        pending_reports = await self.db.fetch_all(query, args)
        
        for report in pending_reports:
            report['formatted_flighttime'] = self._format_flight_time(report.get('flighttime'))

        return pending_reports



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

    async def get_pireps_by_month(self, month: int, year: int) -> list[dict]:
        """
        Fetches all PIREPs for a specific month and year with pilot information.
        
        Args:
            month: Month number (1-12)
            year: Year (e.g., 2024)
            
        Returns:
            A list of dictionaries representing PIREPs for the specified month
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
                pi.name AS pilot_name,
                pi.ifuserid,
                pi.ifc,
                pi.discordid
            FROM
                pireps AS p
            INNER JOIN
                pilots AS pi ON p.pilotid = pi.id
            WHERE
                MONTH(p.date) = %s
                AND YEAR(p.date) = %s
                AND p.status = 1
            ORDER BY
                p.date DESC
        """
        args = (month, year)
        
        pireps = await self.db.fetch_all(query, args)
        
        for pirep in pireps:
            pirep['formatted_flighttime'] = self._format_flight_time(pirep.get('flighttime'))
        
        return pireps

    async def count_route_flights_by_callsign(self, callsign_digits: str, departure: str, arrival: str) -> dict:
        """
        Counts how many times a pilot has flown a specific route based on their callsign digits.
        
        Args:
            callsign_digits: Last 3 digits of the callsign (e.g., "123" for QRV123)
            departure: Departure airport code (e.g., "NZAA")
            arrival: Arrival airport code (e.g., "OTHH")
            
        Returns:
            Dictionary with pilot info and flight count
        """
        query = """
            SELECT
                pi.name AS pilot_name,
                pi.callsign,
                COUNT(p.id) AS flight_count
            FROM
                pireps AS p
            INNER JOIN
                pilots AS pi ON p.pilotid = pi.id
            WHERE
                pi.callsign LIKE %s
                AND p.departure = %s
                AND p.arrival = %s
                AND p.status = 1
            GROUP BY
                pi.id, pi.name, pi.callsign
        """
        callsign_pattern = f"%{callsign_digits}"
        args = (callsign_pattern, departure, arrival)
        
        result = await self.db.fetch_one(query, args)
        return result if result else {"pilot_name": None, "callsign": None, "flight_count": 0}
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
        
        if result:
            return {
                'pilot_name': result['pilot_name'],
                'callsign': result['callsign'],
                'flight_count': result['flight_count']
            }
        else:
            return {'flight_count': 0}

    async def get_2025_year_stats(self) -> dict:
        """
        Gets comprehensive 2025 year statistics including top aircraft, routes, airports and totals.
        
        Returns:
            Dictionary with all 2025 statistics
        """
        # Top 5 aircraft by raw flight hours
        aircraft_query = """
            SELECT 
                a.name AS aircraft_name,
                COUNT(p.id) AS flight_count,
                SUM(p.flighttime / p.multi) AS raw_hours
            FROM pireps p
            INNER JOIN aircraft a ON p.aircraftid = a.id
            WHERE YEAR(p.date) = 2025 
                AND p.status = 1 
                AND p.flighttime > 0
            GROUP BY a.id, a.name
            ORDER BY raw_hours DESC
            LIMIT 5
        """
        
        # Top 3 routes by frequency - Fixed collation issue
        routes_query = """
            SELECT 
                CONCAT(CONVERT(p.departure USING utf8mb4), ' → ', CONVERT(p.arrival USING utf8mb4)) AS route,
                COUNT(p.id) AS flight_count
            FROM pireps p
            WHERE YEAR(p.date) = 2025 
                AND p.status = 1 
                AND p.flighttime > 0
            GROUP BY p.departure, p.arrival
            ORDER BY flight_count DESC
            LIMIT 3
        """
        
        # Top 3 airports by traffic
        airports_query = """
            SELECT airport, SUM(traffic) AS total_traffic FROM (
                SELECT departure AS airport, COUNT(*) AS traffic
                FROM pireps 
                WHERE YEAR(date) = 2025 AND status = 1 AND flighttime > 0
                GROUP BY departure
                UNION ALL
                SELECT arrival AS airport, COUNT(*) AS traffic
                FROM pireps 
                WHERE YEAR(date) = 2025 AND status = 1 AND flighttime > 0
                GROUP BY arrival
            ) AS combined
            GROUP BY airport
            ORDER BY total_traffic DESC
            LIMIT 3
        """
        
        # Total raw hours and approved PIREPs
        totals_query = """
            SELECT 
                COUNT(p.id) AS total_pireps,
                SUM(p.flighttime / p.multi) AS total_raw_hours
            FROM pireps p
            WHERE YEAR(p.date) = 2025 
                AND p.status = 1 
                AND p.flighttime > 0
        """
        
        aircraft_data = await self.db.fetch_all(aircraft_query)
        routes_data = await self.db.fetch_all(routes_query)
        airports_data = await self.db.fetch_all(airports_query)
        totals_data = await self.db.fetch_one(totals_query)
        
        return {
            'top_aircraft': aircraft_data,
            'top_routes': routes_data,
            'top_airports': airports_data,
            'totals': totals_data
        }

    async def get_total_flight_time_seconds(self, pilot_id: int) -> int:
        """
        Calculates the total approved flight time in seconds for a specific pilot.
        Only includes PIREPs with flight time > 5 minutes and status = 1.
        
        Args:
            pilot_id: The database ID of the pilot.
            
        Returns:
            Total seconds (int). Returns 0 if no flights are found.
        """
        query = """
            SELECT SUM(flighttime) as total_seconds 
            FROM pireps 
            WHERE pilotid = %s AND status = 1 AND flighttime > 300
        """
        args = (pilot_id,)
        
        result = await self.db.fetch_one(query, args)
        
        if result and result['total_seconds'] is not None:
            # Decimal handling for some databases, forcing to int
            return int(result['total_seconds'])
        return 0

    async def get_pilot_flight_count(self, pilot_id: int) -> int:
        """
        Counts the total number of approved flights (status=1) for a pilot.
        Only includes PIREPs with flight time > 5 minutes.
        """
        query = "SELECT COUNT(*) as count FROM pireps WHERE pilotid = %s AND status = 1 AND flighttime > 300"
        result = await self.db.fetch_one(query, (pilot_id,))
        return result['count'] if result else 0

    async def get_pilot_favorite_aircraft(self, pilot_id: int) -> str:
        """
        Finds the aircraft name the pilot uses the most.
        Only includes PIREPs with flight time > 5 minutes and status = 1.
        If most used aircraft is ROTW, returns 2nd most used aircraft.
        """
        query = """
            SELECT a.name 
            FROM pireps p
            JOIN aircraft a ON p.aircraftid = a.id
            WHERE p.pilotid = %s AND p.status = 1 AND p.flighttime > 300
            GROUP BY a.id, a.name
            ORDER BY COUNT(p.id) DESC
            LIMIT 2
        """
        results = await self.db.fetch_all(query, (pilot_id,))
        
        if not results:
            return "N/A"
        
        # If most used aircraft is ROTW and there's a 2nd option, return 2nd
        if len(results) > 1 and results[0]['name'] == "ROTW":
            return results[1]['name']
        
        return results[0]['name']

    async def get_pilot_cargo_stats(self, pilot_id: int) -> dict:
        """
        Gets cargo-specific statistics for a pilot (PIREPs starting with QC).
        Only includes PIREPs with flight time > 5 minutes and status = 1.
        
        Returns:
            Dictionary with cargo flight count and total cargo hours
        """
        query = """
            SELECT 
                COUNT(*) as cargo_flights,
                SUM(flighttime) as cargo_seconds
            FROM pireps 
            WHERE pilotid = %s 
                AND status = 1 
                AND flighttime > 300
                AND flightnum LIKE 'QC%%'
        """
        result = await self.db.fetch_one(query, (pilot_id,))
        
        if result:
            cargo_hours = (result['cargo_seconds'] or 0) / 3600
            return {
                'cargo_flights': result['cargo_flights'] or 0,
                'cargo_hours': cargo_hours
            }
        return {'cargo_flights': 0, 'cargo_hours': 0.0}

    async def get_pilot_favorite_dest(self, pilot_id: int) -> str:
        """
        Finds the most visited airport (Dep or Arr) excluding the Hub (OTHH).
        Only includes PIREPs with flight time > 5 minutes and status = 1.
        """
        query = """
            SELECT airport, COUNT(*) as cnt FROM (
                SELECT departure as airport FROM pireps 
                WHERE pilotid = %s AND status = 1 AND flighttime > 300 AND departure != 'OTHH'
                
                UNION ALL
                
                SELECT arrival as airport FROM pireps 
                WHERE pilotid = %s AND status = 1 AND flighttime > 300 AND arrival != 'OTHH'
            ) as trips
            GROUP BY airport
            ORDER BY cnt DESC
            LIMIT 1
        """
        result = await self.db.fetch_one(query, (pilot_id, pilot_id))
        return result['airport'] if result else "N/A"

    async def get_first_accepted_pireps_qrv001_to_qrv019(self) -> list[dict]:
        """
        Gets the first accepted PIREP (status=1) filed by each pilot from QRV001 to QRV019.
        
        Returns:
            List of dictionaries with pilot callsign and their first accepted PIREP details
        """
        query = """
            SELECT 
                pi.callsign,
                pi.name AS pilot_name,
                p.id AS pirep_id,
                p.flightnum,
                p.departure,
                p.arrival,
                p.date,
                p.status
            FROM pilots pi
            INNER JOIN (
                SELECT 
                    pilotid,
                    MIN(date) as first_accepted_date
                FROM pireps
                WHERE status = 1
                GROUP BY pilotid
            ) first_accepted ON pi.id = first_accepted.pilotid
            INNER JOIN pireps p ON pi.id = p.pilotid AND p.date = first_accepted.first_accepted_date AND p.status = 1
            WHERE pi.callsign REGEXP '^QRV(00[1-9]|01[0-9])$'
            ORDER BY pi.callsign
        """
        
        results = await self.db.fetch_all(query)
        return results if results else []
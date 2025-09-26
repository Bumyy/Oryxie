from typing import Optional, Dict, List
from .manager import DatabaseManager

class RoutesModel:
    """
    Handles all database operations related to the 'routes' table.
    """
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    async def find_route_by_icao(self, dep_icao: str, arr_icao: str) -> Optional[Dict]:
        """
        Finds a route in the database matching a specific departure and arrival ICAO,
        including all associated aircraft.

        Args:
            dep_icao: The departure airport ICAO code.
            arr_icao: The arrival airport ICAO code.

        Returns:
            A dictionary containing route details ('fltnum', 'duration') and a list
            of associated aircraft ('aircraft' key, which is a list of dicts with
            'icao' and 'name'). Returns None if no route is found.
            The 'aircraft' list will be empty if the route exists but has no
            associated aircraft.
        """
        query = """
            SELECT
                r.fltnum,
                r.duration,
                a.icao AS aircraft_icao,
                a.name AS aircraft_name
            FROM
                routes r
            LEFT JOIN
                route_aircraft ra ON r.id = ra.routeid
            LEFT JOIN
                aircraft a ON ra.aircraftid = a.id
            WHERE
                r.dep = %s AND r.arr = %s
            ORDER BY
                a.name -- Optional: Orders the aircraft for consistent output
        """
        args = (dep_icao, arr_icao)
        
        results = await self.db.fetch_all(query, args)
        
        if not results:
            return None
        
        # Initialize the route dictionary from the first result
        # All rows for the same route will have the same fltnum and duration
        route_data = {
            "fltnum": results[0]["fltnum"],
            "duration": results[0]["duration"],
            "aircraft": []
        }
        
        # Aggregate aircraft from all results
        # A single route might appear multiple times if it has multiple aircraft
        for row in results:
            # Check if aircraft details are present (they could be None if the route
            # exists but has no associated aircraft due to the LEFT JOIN)
            if row["aircraft_icao"] is not None and row["aircraft_name"] is not None:
                route_data["aircraft"].append({
                    "icao": row["aircraft_icao"],
                    "name": row["aircraft_name"]
                })
        
        return route_data

    async def find_route_by_fltnum(self, fltnum: str) -> Optional[Dict]:
        """
        Finds a route in the database matching a specific flight number,
        including all associated aircraft. Supports multiple flight numbers
        separated by commas (e.g., "QR1,QR3,QR4").

        Args:
            fltnum: The flight number or comma-separated flight numbers.

        Returns:
            A dictionary containing route details ('dep', 'arr', 'duration') and a list
            of associated aircraft ('aircraft' key, which is a list of dicts with
            'icao' and 'name'). Returns None if no route is found.
            The 'aircraft' list will be empty if the route exists but has no
            associated aircraft.
        """
        # More precise matching for flight numbers in comma-separated lists
        query = """
            SELECT DISTINCT
                r.dep,
                r.arr,
                r.duration,
                r.fltnum,
                a.icao AS aircraft_icao,
                a.name AS aircraft_name,
                a.liveryname AS aircraft_livery
            FROM
                routes r
            LEFT JOIN
                route_aircraft ra ON r.id = ra.routeid
            LEFT JOIN
                aircraft a ON ra.aircraftid = a.id
            WHERE
                (r.fltnum = %s OR 
                 r.fltnum LIKE %s OR 
                 r.fltnum LIKE %s OR 
                 r.fltnum LIKE %s)
            LIMIT 20
        """
        
        # Create patterns for different positions in comma-separated list
        start_pattern = f"{fltnum},%"  # QR4,something
        middle_pattern = f"%,{fltnum},%"  # something,QR4,something
        end_pattern = f"%,{fltnum}"  # something,QR4
        
        args = (fltnum, start_pattern, middle_pattern, end_pattern)
        
        results = await self.db.fetch_all(query, args)
        
        if not results:
            return None
        
        route_data = {
            "dep": results[0]["dep"],
            "arr": results[0]["arr"],
            "duration": results[0]["duration"],
            "fltnum": results[0]["fltnum"],
            "aircraft": []
        }
        
        # Use a set to avoid duplicates
        seen_aircraft = set()
        for row in results:
            if row["aircraft_icao"] is not None and row["aircraft_name"] is not None:
                # Format as "Livery Name Aircraft Name" or just "Aircraft Name" if no livery
                livery = row.get("aircraft_livery") if row.get("aircraft_livery") else ""
                
                if livery and livery.lower() != "generic" and livery.strip():
                    display_name = f"{livery} {row['aircraft_name']}"
                else:
                    display_name = row["aircraft_name"]
                
                aircraft_key = (row["aircraft_icao"], display_name)
                if aircraft_key not in seen_aircraft:
                    aircraft_info = {
                        "icao": row["aircraft_icao"],
                        "name": display_name
                    }
                    route_data["aircraft"].append(aircraft_info)
                    seen_aircraft.add(aircraft_key)
        return route_data
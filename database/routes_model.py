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
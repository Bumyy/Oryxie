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
        
        print(f"DEBUG ROUTES: Searching for flight number: '{fltnum}'")
        print(f"DEBUG ROUTES: Query patterns - exact: '{fltnum}', start: '{start_pattern}', middle: '{middle_pattern}', end: '{end_pattern}'")
        
        results = await self.db.fetch_all(query, args)
        print(f"DEBUG ROUTES: Query returned {len(results) if results else 0} results")
        if results:
            print(f"DEBUG ROUTES: First result: {results[0]}")
        
        if not results:
            print(f"DEBUG ROUTES: No results found for flight number: '{fltnum}'")
            return None
        
        # Get the livery from the first aircraft result
        livery_name = "Qatar Airways"  # Default
        if results[0].get("aircraft_livery") and results[0]["aircraft_livery"].strip():
            livery_name = results[0]["aircraft_livery"]
        
        route_data = {
            "dep": results[0]["dep"],
            "arr": results[0]["arr"],
            "duration": results[0]["duration"],
            "fltnum": fltnum,  # Use the requested flight number, not the full DB field
            "livery": livery_name,  # Add livery field
            "aircraft": []
        }
        
        # Use a set to avoid duplicates
        seen_aircraft = set()
        for row in results:
            if row["aircraft_icao"] is not None and row["aircraft_name"] is not None:
                aircraft_key = (row["aircraft_icao"], row["aircraft_name"])
                if aircraft_key not in seen_aircraft:
                    aircraft_info = {
                        "icao": row["aircraft_icao"],
                        "name": row["aircraft_name"]
                    }
                    route_data["aircraft"].append(aircraft_info)
                    seen_aircraft.add(aircraft_key)
        return route_data

    async def find_routes_to_airport(self, arr_icao: str) -> List[Dict]:
        """
        Finds all routes going to a specific airport.
        
        Args:
            arr_icao: The arrival airport ICAO code.
        
        Returns:
            A list of dictionaries containing route details.
        """
        query = """
            SELECT DISTINCT
                r.dep,
                r.arr,
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
                r.arr = %s
            ORDER BY
                r.dep, r.fltnum
        """
        
        print(f"\n=== DB QUERY DEBUG for {arr_icao} ===")
        print(f"Query: {query}")
        
        results = await self.db.fetch_all(query, (arr_icao,))
        print(f"Raw query results count: {len(results) if results else 0}")
        
        if results:
            print("First 3 raw results:")
            for i, row in enumerate(results[:3]):
                print(f"  [{i}] {dict(row)}")
        
        if not results:
            return []
        
        # Group results by route (dep-arr-fltnum combination)
        routes_dict = {}
        for row in results:
            route_key = (row['dep'], row['arr'], row['fltnum'])
            
            if route_key not in routes_dict:
                routes_dict[route_key] = {
                    'dep': row['dep'],
                    'arr': row['arr'],
                    'fltnum': row['fltnum'],
                    'duration': row['duration'],
                    'aircraft': []
                }
            
            # Add aircraft if present
            if row['aircraft_icao'] and row['aircraft_name']:
                aircraft_info = {
                    'icao': row['aircraft_icao'],
                    'name': row['aircraft_name']
                }
                if aircraft_info not in routes_dict[route_key]['aircraft']:
                    routes_dict[route_key]['aircraft'].append(aircraft_info)
                    print(f"  Added aircraft: ICAO='{row['aircraft_icao']}', Name='{row['aircraft_name']}'")
            else:
                print(f"  No aircraft data: ICAO='{row['aircraft_icao']}', Name='{row['aircraft_name']}'")
        
        final_routes = list(routes_dict.values())
        print(f"Final grouped routes count: {len(final_routes)}")
        print(f"=== END DB QUERY DEBUG ===\n")
        
        return final_routes

    async def get_all_liveries(self) -> List[str]:
        """
        Gets all unique livery names from the aircraft table.
        
        Returns:
            A list of unique livery names.
        """
        query = """
            SELECT DISTINCT a.liveryname 
            FROM aircraft a 
            WHERE a.liveryname IS NOT NULL AND a.liveryname != '' AND a.liveryname != 'Generic'
            ORDER BY a.liveryname
        """
        
        results = await self.db.fetch_all(query)
        return [row['liveryname'] for row in results if row['liveryname'].strip()]
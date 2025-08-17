from typing import Optional, Dict
from .manager import DatabaseManager

class RoutesModel:
    """
    Handles all database operations related to the 'routes' table.
    """
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    async def find_route_by_icao(self, dep_icao: str, arr_icao: str) -> Optional[Dict]:
        """
        Finds a route in the database matching a specific departure and arrival ICAO.

        Args:
            dep_icao: The departure airport ICAO code.
            arr_icao: The arrival airport ICAO code.

        Returns:
            A dictionary with 'fltnum' and 'duration' if a match is found, otherwise None.
        """
        query = """
            SELECT fltnum, duration
            FROM routes 
            WHERE dep = %s AND arr = %s
            LIMIT 1
        """
        args = (dep_icao, arr_icao)
        
        result = await self.db.fetch_one(query, args)
        
        return result if result else None
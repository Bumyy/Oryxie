from typing import Optional
from .manager import DatabaseManager

class RoutesModel:
    """
    Handles all database operations related to the 'routes' table.
    """
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    async def find_route_by_icao(self, dep_icao: str, arr_icao: str) -> Optional[str]:
        """
        Finds a route in the database matching a specific departure and arrival ICAO.

        Args:
            dep_icao: The departure airport ICAO code.
            arr_icao: The arrival airport ICAO code.

        Returns:
            The 'fltnum' string from the database if a match is found, otherwise None.
        """
        query = """
            SELECT fltnum 
            FROM routes 
            WHERE dep = %s AND arr = %s
            LIMIT 1
        """
        args = (dep_icao, arr_icao)
        
        result = await self.db.fetch_one(query, args)
        
        return result['fltnum'] if result else None
from typing import Optional, Dict
from .manager import DatabaseManager

class PilotsModel:
    """
    Handles all database operations related to the 'pilots' table.
    """
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    async def get_pilot_by_callsign(self, callsign: str) -> Optional[Dict]:
        """
        Retrieves a pilot's data using their callsign.

        Args:
            callsign: The pilot's callsign (e.g., 'QRV001').

        Returns:
            A dictionary of the pilot's data if found, otherwise None.
        """
        query = """
            SELECT discordid FROM pilots WHERE callsign = %s
        """
        args = (callsign,)
        return await self.db.fetch_one(query, args)

    async def update_discord_id(self, callsign: str, discord_id: str) -> int:
        """
        Updates the discordid for a pilot with a given callsign.
        We use a string for discord_id to avoid any potential integer overflow issues.

        Args:
            callsign: The pilot's callsign (e.g., 'QRV001').
            discord_id: The user's Discord ID as a string.

        Returns:
            The number of rows affected by the update (should be 1 on success, 0 if no match).
        """
        query = """
            UPDATE pilots
            SET discordid = %s
            WHERE callsign = %s
        """
        args = (discord_id, callsign)
        
        rows_affected = await self.db.execute(query, args)
        return rows_affected
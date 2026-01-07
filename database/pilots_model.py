from typing import Optional, Dict, Set
from .manager import DatabaseManager

class PilotsModel:
    """
    Handles all database operations related to the 'pilots' table.
    """
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    async def get_pilot_by_callsign(self, callsign: str) -> Optional[Dict]:
        """
        Retrieves a pilot's data using their callsign (only active pilots).

        Args:
            callsign: The pilot's callsign (e.g., 'QRV001').

        Returns:
            A dictionary of the pilot's data if found, otherwise None.
        """
        query = """
            SELECT id, callsign, discordid FROM pilots WHERE callsign = %s AND status = 1
        """
        args = (callsign,)
        return await self.db.fetch_one(query, args)

    async def get_pilot_full_data(self, callsign: str) -> Optional[Dict]:
        """
        Retrieves full pilot data including ifuserid and ifc fields.

        Args:
            callsign: The pilot's callsign (e.g., 'QRV001').

        Returns:
            A dictionary of the pilot's full data if found, otherwise None.
        """
        query = """
            SELECT id, callsign, discordid, ifuserid, ifc FROM pilots WHERE callsign = %s AND status = 1
        """
        args = (callsign,)
        return await self.db.fetch_one(query, args)

    async def get_pilot_by_callsign_any_status(self, callsign: str) -> Optional[Dict]:
        """
        Retrieves a pilot's data using their callsign (any status - for roster sync).

        Args:
            callsign: The pilot's callsign (e.g., 'QRV001').

        Returns:
            A dictionary of the pilot's data if found, otherwise None.
        """
        query = """
            SELECT id, callsign, discordid, status FROM pilots WHERE callsign = %s
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

    async def get_pilot_by_ifuserid(self, ifuserid: str) -> Optional[Dict]:
        """
        Retrieves a pilot's data using their Infinite Flight User ID.

        Args:
            ifuserid: The user's unique ID from the Infinite Flight API.

        Returns:
            A dictionary of the pilot's data if found, otherwise None.
        """
        query = "SELECT discordid FROM pilots WHERE ifuserid = %s"
        args = (ifuserid,)
        return await self.db.fetch_one(query, args)

    async def get_pilot_by_ifc_username(self, username: str) -> Optional[Dict]:
        """
        Retrieves a pilot's data using their IFC username as a fallback.
        This method is designed to find a match even if the stored URL has
        trailing slashes or paths like '/summary'.

        Args:
            username: The clean IFC username (e.g., 'bumy').

        Returns:
            A dictionary of the pilot's data if found, otherwise None.
        """
        query = "SELECT discordid FROM pilots WHERE ifc LIKE %s"
        
        pattern = f"%/{username}%"
        args = (pattern,)
        return await self.db.fetch_one(query, args)

    async def update_ifuserid_by_ifc_username(self, username: str, ifuserid: str) -> int:
        """
        Updates the ifuserid for a pilot found via their IFC username.
        This makes future lookups faster and more reliable.

        Args:
            username: The clean IFC username (e.g., 'bumy').
            ifuserid: The Infinite Flight User ID to set.

        Returns:
            The number of rows affected.
        """
        query = """
            UPDATE pilots
            SET ifuserid = %s
            WHERE ifc LIKE %s
        """
        pattern = f"%/{username}%"
        args = (ifuserid, pattern)
        return await self.db.execute(query, args)

    async def get_all_verified_discord_ids(self) -> Set[str]:
        """
        Retrieves a set of all unique, non-empty Discord IDs from the pilots table.

        Returns:
            A set of Discord IDs as strings, for efficient lookup.
        """
        query = "SELECT DISTINCT discordid FROM pilots WHERE discordid IS NOT NULL AND discordid != ''"
        records = await self.db.fetch_all(query)
        return {str(row['discordid']) for row in records}
    
    async def get_all_callsigns(self) -> Set[str]:
        """
        Retrieves a set of all unique callsigns from the pilots table (both active and inactive).
        A set is used for highly efficient 'in' checks (O(1) average time complexity).

        Returns:
            A set of all callsigns as uppercase strings.
        """
        query = "SELECT callsign FROM pilots"
        records = await self.db.fetch_all(query)
        return {str(row['callsign']).upper() for row in records if row['callsign']}

    async def get_all_pilot_records(self) -> list:
        """
        Retrieves a list of all pilot records containing their callsign and discordid.
        This is used for database auditing purposes.

        Returns:
            A list of dictionaries, where each dictionary represents a pilot.
        """
        query = "SELECT callsign, discordid FROM pilots WHERE callsign IS NOT NULL AND callsign != ''"
        return await self.db.fetch_all(query)

    async def get_pilot_by_id(self, pilot_id: int) -> Optional[Dict]:
        """
        Retrieves a pilot's data using their ID (only active pilots).

        Args:
            pilot_id: The pilot's ID.

        Returns:
            A dictionary of the pilot's data if found, otherwise None.
        """
        query = "SELECT id, callsign, discordid FROM pilots WHERE id = %s AND status = 1"
        args = (pilot_id,)
        return await self.db.fetch_one(query, args)
        
    async def get_pilot_by_discord_id(self, discord_id: str) -> Optional[Dict]:
        """
        Retrieves a pilot's data using their Discord ID (only active pilots).

        Args:
            discord_id: The pilot's Discord ID.

        Returns:
            A dictionary of the pilot's data if found, otherwise None.
        """
        query = "SELECT id, callsign, discordid FROM pilots WHERE discordid = %s AND status = 1"
        args = (discord_id,)
        return await self.db.fetch_one(query, args)
        
    async def identify_pilot(self, discord_user) -> Dict:
        """
        Central pilot identification system - tries multiple methods to find pilot.
        
        Args:
            discord_user: Discord user object with .display_name and .id
            
        Returns:
            Dict with 'success', 'pilot_data', 'method', and 'error_message' keys
        """
        import re
        
        # Method 1: Try to extract callsign from nickname
        callsign_match = re.search(r'QRV\d{3,}', discord_user.display_name, re.IGNORECASE)
        if callsign_match:
            extracted_callsign = callsign_match.group(0).upper()
            pilot_data = await self.get_pilot_by_callsign(extracted_callsign)
            if pilot_data:
                return {
                    'success': True,
                    'pilot_data': pilot_data,
                    'method': 'callsign_from_nickname',
                    'extracted_callsign': extracted_callsign
                }
        
        # Method 2: Try Discord ID lookup
        pilot_data = await self.get_pilot_by_discord_id(str(discord_user.id))
        if pilot_data:
            return {
                'success': True,
                'pilot_data': pilot_data,
                'method': 'discord_id_lookup'
            }
        
        # Both methods failed
        return {
            'success': False,
            'pilot_data': None,
            'method': 'none',
            'error_message': 'Your Discord ID and callsign are not matching. Please contact Ayush or any staff.'
        }

    def get_html_template(self):
        """Returns HTML template for pilot documentation"""
        import os
        try:
            html_path = os.path.join(os.path.dirname(__file__), '..', 'flight-briefing-template-qatar.html')
            with open(html_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            return 'HTML template file not found'
        except Exception as e:
            return f'Error reading HTML template: {e}'
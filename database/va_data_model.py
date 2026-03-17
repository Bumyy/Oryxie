"""
Database models for VA data: Rank, Aircraft, and Multiplier.
These models provide methods to query ranks, aircraft mappings, and multipliers.
"""
from typing import Optional, Dict, List, Any
import json
import os
import logging
from .manager import DatabaseManager

logger = logging.getLogger('oryxie.rank_model')

class RankModel:
    """
    Handles operations related to pilot ranks.
    Uses the 'ranks' table and pilot's flight hours to determine rank.
    Enriches DB data with Discord Role IDs from JSON.
    """
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.rank_config = self._load_rank_config()

    def _load_rank_config(self) -> dict:
        """Load Discord-specific rank config (Role IDs, etc) from JSON."""
        config_path = os.path.join('assets', 'rank_config.json')
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    data = json.load(f)
                    return data.get('ranks', {})
        except Exception as e:
            logger.error(f"Failed to load rank_config.json: {e}")
        return {}

    def _enrich_rank_data(self, db_rank: Optional[Dict]) -> Optional[Dict]:
        """Merges DB rank data with JSON config data (Role IDs)."""
        if not db_rank:
            return None
        
        # Get the extra info from JSON using the rank name as the key
        json_info = self.rank_config.get(db_rank['name'], {})
        
        # Merge them (JSON data takes precedence for Discord fields)
        return {**db_rank, **json_info}
    
    async def get_rank_by_id(self, rank_id: int) -> Optional[Dict]:
        """Get rank details by ID."""
        query = "SELECT * FROM ranks WHERE id = %s"
        result = await self.db.fetch_one(query, (rank_id,))
        return self._enrich_rank_data(result)
    
    async def get_rank_by_name(self, rank_name: str) -> Optional[Dict]:
        """Get rank details by name."""
        query = "SELECT * FROM ranks WHERE name = %s"
        result = await self.db.fetch_one(query, (rank_name,))
        return self._enrich_rank_data(result)
    
    async def get_all_ranks(self) -> List[Dict]:
        """Get all ranks ordered by time requirement."""
        query = "SELECT * FROM ranks ORDER BY timereq ASC"
        return await self.db.fetch_all(query)
    
    async def get_rank_by_hours(self, total_hours_seconds: int) -> Optional[Dict]:
        """
        Determine rank based on flight hours (in seconds).
        Returns the highest rank the pilot qualifies for.
        """
        query = """
            SELECT * FROM ranks 
            WHERE timereq <= %s 
            ORDER BY timereq DESC 
            LIMIT 1
        """
        result = await self.db.fetch_one(query, (total_hours_seconds,))
        
        # If no rank found (shouldn't happen as Cadet has 0 requirement), return first rank
        if not result:
            query = "SELECT * FROM ranks ORDER BY timereq ASC LIMIT 1"
            result = await self.db.fetch_one(query)
        
        return self._enrich_rank_data(result)

    async def is_owd_eligible(self, pilot_id: int) -> bool:
        """Checks if a pilot is eligible for OneWorld Discover routes."""
        rank_data = await self.get_pilot_rank(pilot_id)
        if not rank_data:
            return False
        return rank_data['name'] in ['OneWorld', 'Oryx']

    async def get_aircraft_rank_requirement(self, aircraft_id: int) -> Optional[Dict]:
        """
        Get the minimum rank required to fly a specific aircraft.
        
        Args:
            aircraft_id: The Crew Center aircraft ID (from aircraft table)
            
        Returns:
            Dict with rank info (id, name, timereq) or None if aircraft not found
        """
        query = "SELECT rankreq FROM aircraft WHERE id = %s"
        aircraft = await self.db.fetch_one(query, (aircraft_id,))
        
        if not aircraft or not aircraft.get('rankreq'):
            return None
        
        # Get the rank details
        rank_id = aircraft['rankreq']
        return await self.get_rank_by_id(rank_id)
    
    async def can_pilot_fly_aircraft(self, pilot_id: int, aircraft_id: int) -> Dict:
        """
        Check if a pilot can fly a specific aircraft based on their rank.
        
        Args:
            pilot_id: The pilot's database ID
            aircraft_id: The aircraft's database ID (Crew Center ID)
            
        Returns:
            Dict with:
                - 'can_fly': bool
                - 'pilot_rank': Dict with pilot's rank info
                - 'required_rank': Dict with aircraft's required rank info
                - 'message': str explanation
        """
        result = {
            'can_fly': False,
            'pilot_rank': None,
            'required_rank': None,
            'message': ''
        }
        
        # Get pilot's current rank
        pilot_rank = await self.get_pilot_rank(pilot_id)
        if not pilot_rank:
            result['message'] = 'Pilot not found in database'
            return result
        
        # Get aircraft's required rank
        required_rank = await self.get_aircraft_rank_requirement(aircraft_id)
        if not required_rank:
            result['pilot_rank'] = pilot_rank
            result['message'] = 'Aircraft not found or has no rank requirement'
            result['can_fly'] = True  # Allow if aircraft has no rank req
            return result
        
        # Compare ranks
        pilot_rank_id = pilot_rank['id']
        required_rank_id = required_rank['id']
        
        result['pilot_rank'] = pilot_rank
        result['required_rank'] = required_rank
        
        if pilot_rank_id >= required_rank_id:
            result['can_fly'] = True
            result['message'] = f"Pilot rank '{pilot_rank['name']}' meets requirement '{required_rank['name']}'"
        else:
            result['can_fly'] = False
            result['message'] = f"Pilot rank '{pilot_rank['name']}' is below required rank '{required_rank['name']}'"
        
        return result
    
    async def can_pilot_fly_if_aircraft(self, pilot_id: int, if_aircraft_id: str) -> Dict:
        """
        Check if a pilot can fly an aircraft using Infinite Flight aircraft ID.
        This is the main method for Ascaris - uses IF aircraft UUID to find VA aircraft.
        
        Args:
            pilot_id: The pilot's database ID
            if_aircraft_id: The Infinite Flight aircraft UUID (e.g., 'a266b67f-03e3-4f8c-a2bb-b57cfd4b12f3')
            
        Returns:
            Dict with same structure as can_pilot_fly_aircraft
        """
        # First, find the VA aircraft by IF aircraft ID
        from .va_data_model import AircraftModel
        aircraft_model = AircraftModel(self.db)
        
        aircraft = await aircraft_model.get_aircraft_by_if_id(if_aircraft_id)
        if not aircraft:
            return {
                'can_fly': False,
                'pilot_rank': None,
                'required_rank': None,
                'message': f'Aircraft not found in VA database (IF ID: {if_aircraft_id})'
            }
        
        # Now check rank requirement
        return await self.can_pilot_fly_aircraft(pilot_id, aircraft['id'])
    
    async def get_pilot_rank(self, pilot_id: int) -> Optional[Dict]:
        """
        Get pilot's current rank based on their total flight hours.
        Combines transfer hours + PIREP hours.
        """
        # Get pilot data including transfer hours
        pilot_query = "SELECT transhours FROM pilots WHERE id = %s"
        pilot_data = await self.db.fetch_one(pilot_query, (pilot_id,))
        
        if not pilot_data:
            return None
        
        # Get PIREP hours (sum of approved flight times)
        pirep_query = """
            SELECT COALESCE(SUM(flighttime), 0) as pirep_hours 
            FROM pireps 
            WHERE pilotid = %s AND status = 1 AND flighttime > 300
        """
        pirep_data = await self.db.fetch_one(pirep_query, (pilot_id,))
        
        total_seconds = (pilot_data.get('transhours', 0) or 0) + (pirep_data.get('pirep_hours', 0) or 0)
        
        return await self.get_rank_by_hours(total_seconds)


class AircraftModel:
    """
    Handles operations related to aircraft.
    Provides mapping between Infinite Flight aircraft IDs and Crew Center aircraft IDs.
    """
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    
    async def get_aircraft_by_id(self, aircraft_id: int) -> Optional[Dict]:
        """Get aircraft details by Crew Center ID."""
        query = "SELECT * FROM aircraft WHERE id = %s"
        return await self.db.fetch_one(query, (aircraft_id,))
    
    async def get_aircraft_by_if_id(self, if_aircraft_id: str) -> Optional[Dict]:
        """Get aircraft details by Infinite Flight aircraft UUID."""
        query = "SELECT * FROM aircraft WHERE ifaircraftid = %s"
        return await self.db.fetch_one(query, (if_aircraft_id,))
    
    async def get_aircraft_by_if_ids(self, if_aircraft_id: str, if_livery_id: str) -> Optional[Dict]:
        """
        Get aircraft details by BOTH Infinite Flight aircraft UUID AND livery UUID.
        This is the primary method for Ascaris - matches exact aircraft+livery combination.
        
        Args:
            if_aircraft_id: The Infinite Flight aircraft UUID (e.g., 'a266b67f-03e3-4f8c-a2bb-b57cfd4b12f3')
            if_livery_id: The Infinite Flight livery UUID (e.g., '38ad8ab5-57fd-4e9b-9367-8792aebc15b6')
            
        Returns:
            Dict with aircraft details including 'id' (CC aircraft ID) or None if not found
        """
        query = "SELECT * FROM aircraft WHERE ifaircraftid = %s AND ifliveryid = %s AND status = 1"
        return await self.db.fetch_one(query, (if_aircraft_id, if_livery_id))
    
    async def get_aircraft_by_if_ids_fallback(self, if_aircraft_id: str, if_livery_id: str) -> Optional[Dict]:
        """
        Get aircraft by IF IDs with fallback logic:
        1. First try exact match (aircraft + livery)
        2. If not found, return generic aircraft (id=11) - DO NOT search by aircraft only
        
        Args:
            if_aircraft_id: The Infinite Flight aircraft UUID
            if_livery_id: The Infinite Flight livery UUID
            
        Returns:
            Dict with aircraft details or generic aircraft (id=11)
        """
        # Try exact match first
        aircraft = await self.get_aircraft_by_if_ids(if_aircraft_id, if_livery_id)
        if aircraft:
            return aircraft
        
        # Fallback: Return generic aircraft (id=11) instead of searching by aircraft only
        # Searching by only ifaircraftid would return 50+ results which is ambiguous
        return await self.get_aircraft_by_id(11)
    
    async def get_aircraft_by_icao(self, icao: str) -> Optional[Dict]:
        """Get aircraft details by ICAO code."""
        query = "SELECT * FROM aircraft WHERE icao = %s AND status = 1"
        return await self.db.fetch_one(query, (icao,))
    
    async def get_aircraft_by_name(self, name: str) -> Optional[Dict]:
        """Get aircraft details by full name."""
        query = "SELECT * FROM aircraft WHERE name = %s AND status = 1"
        return await self.db.fetch_one(query, (name,))
    
    async def get_aircraft_by_name_and_livery(self, name: str, livery: str) -> Optional[Dict]:
        """Get aircraft details by full name and livery."""
        query = "SELECT * FROM aircraft WHERE name = %s AND livery = %s AND status = 1"
        return await self.db.fetch_one(query, (name, livery))
    
    async def get_all_aircraft(self) -> List[Dict]:
        """Get all active aircraft."""
        query = "SELECT * FROM aircraft WHERE status = 1 ORDER BY name"
        return await self.db.fetch_all(query)
    
    async def get_aircraft_by_route(self, route_id: int) -> List[Dict]:
        """
        Get all aircraft allowed for a specific route.
        Uses route_aircraft join table.
        """
        query = """
            SELECT a.* FROM aircraft a
            INNER JOIN route_aircraft ra ON a.id = ra.aircraftid
            WHERE ra.routeid = %s AND a.status = 1
        """
        return await self.db.fetch_all(query, (route_id,))
    
    async def get_aircraft_by_route_icao(self, dep_icao: str, arr_icao: str) -> List[Dict]:
        """
        Get all aircraft allowed for a route (by departure/arrival ICAO).
        """
        query = """
            SELECT a.* FROM aircraft a
            INNER JOIN route_aircraft ra ON a.id = ra.aircraftid
            INNER JOIN routes r ON ra.routeid = r.id
            WHERE r.dep = %s AND r.arr = %s AND a.status = 1
        """
        return await self.db.fetch_all(query, (dep_icao, arr_icao))
    
    async def get_route_id(self, dep_icao: str, arr_icao: str) -> Optional[int]:
        """Get route ID by departure and arrival ICAO."""
        query = "SELECT id FROM routes WHERE dep = %s AND arr = %s"
        result = await self.db.fetch_one(query, (dep_icao, arr_icao))
        return result['id'] if result else None


class MultiplierModel:
    """
    Handles operations related to flight multipliers.
    """
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    
    async def get_multiplier_by_id(self, multiplier_id: int) -> Optional[Dict]:
        """Get multiplier details by ID."""
        query = "SELECT * FROM multipliers WHERE id = %s"
        return await self.db.fetch_one(query, (multiplier_id,))
    
    async def get_multiplier_by_code(self, code: int) -> Optional[Dict]:
        """Get multiplier by code."""
        query = "SELECT * FROM multipliers WHERE code = %s"
        return await self.db.fetch_one(query, (code,))
    
    async def get_all_multipliers(self) -> List[Dict]:
        """Get all multipliers."""
        query = "SELECT * FROM multipliers ORDER BY multiplier"
        return await self.db.fetch_all(query)
    
    async def get_multipliers_for_rank(self, rank_id: int) -> List[Dict]:
        """
        Get all multipliers available for a specific rank.
        """
        query = """
            SELECT * FROM multipliers 
            WHERE minrankid <= %s 
            ORDER BY multiplier
        """
        return await self.db.fetch_all(query, (rank_id,))
    
    async def get_route_multiplier(self, dep_icao: str, arr_icao: str) -> Optional[float]:
        """
        Get the multiplier for a specific route.
        Returns the multiplier value from the routes table.
        """
        query = "SELECT multiplier FROM routes WHERE dep = %s AND arr = %s"
        result = await self.db.fetch_one(query, (dep_icao, arr_icao))
        return float(result['multiplier']) if result and result.get('multiplier') else 1.0

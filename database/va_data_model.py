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

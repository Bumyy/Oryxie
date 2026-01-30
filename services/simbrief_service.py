import aiohttp
import logging
import urllib.parse
import json
import os
from typing import Optional, Dict, Any

class SimBriefService:
    def __init__(self):
        self.logger = logging.getLogger('oryxie.simbrief_service')
        self.base_fetch_url = "https://www.simbrief.com/api/xml.fetcher.php"
        self.base_dispatch_url = "https://www.simbrief.com/system/dispatch.php"
        self.aircraft_data = self._load_aircraft_data()
    
    def _load_aircraft_data(self) -> Dict:
        """Load aircraft data from JSON file"""
        try:
            json_path = os.path.join(os.path.dirname(__file__), '..', 'assets', 'aircraft_data.json')
            with open(json_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Error loading aircraft data: {e}")
            return {}

    async def fetch_latest_ofp(self, username: str) -> Optional[Dict[str, Any]]:
        """
        Fetches the latest active Operational Flight Plan (OFP) for a user.
        No private API key required.
        """
        params = {
            "username": username,
            "json": "v2"
        }
        
        url = f"{self.base_fetch_url}?{urllib.parse.urlencode(params)}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        self.logger.warning(f"SimBrief API returned status {response.status} for user {username}")
                        return None
                    
                    data = await response.json()
                    
                    if 'fetch' in data and data['fetch'].get('status') != "Success":
                        return None
                        
                    return data
        except Exception as e:
            self.logger.error(f"Error fetching SimBrief data for {username}: {e}")
            return None

    def parse_weights_and_fuel(self, ofp_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extracts only the data needed for Checklists and V-Speeds.
        """
        if not ofp_data:
            return {}

        try:
            units = ofp_data.get('params', {}).get('units', 'kgs')
            aircraft = ofp_data.get('aircraft', {}).get('icao_code', 'Unknown')
            
            weights = ofp_data.get('weights', {})
            fuel = ofp_data.get('fuel', {})
            
            return {
                "status": "success",
                "units": units,
                "aircraft": aircraft,
                "tow": float(weights.get('est_tow', 0)),
                "zfw": float(weights.get('est_zfw', 0)),
                "block_fuel": float(fuel.get('plan_ramp', 0)),
                "pax_count": ofp_data.get('weights', {}).get('pax_count', 0),
                "cargo": float(weights.get('cargo', 0))
            }
        except Exception as e:
            self.logger.error(f"Error parsing SimBrief JSON: {e}")
            return {"status": "error"}

    def generate_dispatch_link(
        self, 
        origin: str, 
        destination: str, 
        aircraft_type: str, 
        callsign: str,
        flight_number: Optional[str] = None
    ) -> str:
        """
        Generates the SimBrief pre-fill URL.
        
        :param aircraft_type: ICAO code from aircraft_data.json
        :param callsign: Pilot callsign from database (e.g., 'QRV001')
        """
        params = {
            "orig": origin.upper(),
            "dest": destination.upper(),
            "type": aircraft_type.upper(),
            "callsign": callsign.upper(),
            "airline": "QRV"
        }
        
        if flight_number:
            params["fltnum"] = flight_number

        query_string = urllib.parse.urlencode(params)
        return f"{self.base_dispatch_url}?{query_string}"
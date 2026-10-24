import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


class FleetService:
    def __init__(self, bot):
        self.bot = bot

    def _aircraft_model(self):
        return self.bot.aircraft_model

    async def match_aircraft_by_if_id(self, if_id: str) -> Optional[Dict]:
        if not if_id:
            return None
        return await self._aircraft_model().get_aircraft_by_if_any_id(if_id)

    async def match_fleet_to_db(self, fleet: List[Any]) -> List[Dict]:
        enriched = []
        for aircraft in fleet or []:
            aircraft_id = getattr(aircraft, "aircraft_id", None) or (
                aircraft.get("aircraftId") if isinstance(aircraft, dict) else None
            )
            db_row = await self.match_aircraft_by_if_id(aircraft_id or "")
            entry = {
                "if_aircraft": aircraft,
                "db_aircraft": db_row,
            }
            enriched.append(entry)
        return enriched

    async def get_fleet_db_map(self, fleet: List[Any]) -> Dict[str, Optional[Dict]]:
        fleet_map = {}
        for aircraft in fleet or []:
            aircraft_id = getattr(aircraft, "aircraft_id", None) or (
                aircraft.get("aircraftId") if isinstance(aircraft, dict) else None
            )
            if aircraft_id:
                db_row = await self.match_aircraft_by_if_id(aircraft_id)
                fleet_map[aircraft_id] = db_row
        return fleet_map

import logging
import time
from typing import Any, Dict, List, Optional

from services.aircraft_classifier import load_aircraft_models

log = logging.getLogger(__name__)


class FleetService:
    CACHE_SECONDS = 300

    def __init__(self, bot):
        self.bot = bot
        self._aircraft_by_if_id = {}
        self._cache_expires_at = 0.0

    def _aircraft_model(self):
        return self.bot.aircraft_model

    @staticmethod
    def _aircraft_id(aircraft):
        if isinstance(aircraft, dict):
            return aircraft.get("aircraft_id") or aircraft.get("aircraftId")
        return getattr(aircraft, "aircraft_id", None)

    async def _get_aircraft_by_if_id(self):
        if time.monotonic() < self._cache_expires_at:
            return self._aircraft_by_if_id

        rows = await self._aircraft_model().get_all_aircraft()
        aircraft_by_if_id = {}
        for row in rows or []:
            for column in ("ifaircraftid", "ifliveryid"):
                if_id = str(row.get(column) or "").strip().casefold()
                if if_id:
                    aircraft_by_if_id[if_id] = row

        self._aircraft_by_if_id = aircraft_by_if_id
        self._cache_expires_at = time.monotonic() + self.CACHE_SECONDS
        return aircraft_by_if_id

    async def match_aircraft_by_if_id(self, if_id: str) -> Optional[Dict]:
        normalized_id = str(if_id or "").strip().casefold()
        if not normalized_id:
            return None
        aircraft_by_if_id = await self._get_aircraft_by_if_id()
        return aircraft_by_if_id.get(normalized_id)

    async def match_fleet_to_db(self, fleet: List[Any]) -> List[Dict]:
        fleet_map = await self.get_fleet_db_map(fleet)
        return [
            {
                "if_aircraft": aircraft,
                "db_aircraft": fleet_map.get(str(self._aircraft_id(aircraft))),
            }
            for aircraft in fleet or []
        ]

    async def get_fleet_db_map(self, fleet: List[Any]) -> Dict[str, Optional[Dict]]:
        aircraft_by_if_id = await self._get_aircraft_by_if_id()
        fleet_map = {}
        for aircraft in fleet or []:
            aircraft_id = self._aircraft_id(aircraft)
            if aircraft_id:
                fleet_map[str(aircraft_id)] = aircraft_by_if_id.get(
                    str(aircraft_id).strip().casefold()
                )
        return fleet_map

    async def enrich_fleet(self, fleet: List[Any]) -> List[Any]:
        fleet_map = await self.get_fleet_db_map(fleet)
        catalog = getattr(self.bot, "aircraft_name_map", {})
        registration_models = load_aircraft_models()
        missing_models = 0

        for aircraft in fleet or []:
            aircraft_id = self._aircraft_id(aircraft)
            db_aircraft = fleet_map.get(str(aircraft_id)) if aircraft_id else None
            catalog_name = catalog.get(str(aircraft_id)) if aircraft_id else None
            registration = (
                aircraft.get("registration")
                if isinstance(aircraft, dict)
                else getattr(aircraft, "registration", None)
            )
            registration_model = registration_models.get(
                str(registration or "").strip().upper()
            )
            model_name = (
                (db_aircraft or {}).get("name")
                or catalog_name
                or registration_model
                or ""
            )
            livery_name = (db_aircraft or {}).get("liveryname") or ""

            if isinstance(aircraft, dict):
                aircraft["db_aircraft"] = db_aircraft
                aircraft["model"] = model_name
                aircraft["livery_name"] = livery_name
            else:
                aircraft.db_aircraft = db_aircraft
                aircraft.model = model_name
                aircraft.livery_name = livery_name

            if not model_name:
                missing_models += 1

        if missing_models:
            log.warning(
                "Aircraft model data is missing for %s of %s fleet aircraft.",
                missing_models,
                len(fleet or []),
            )
        return fleet

"""Live fleet state and caching layer for Infinite Flight (v3 + v2).

Provides cached, consolidated live aircraft representations adhering to Infinite Flight's
mandatory caching and polling interval rules (https://infiniteflight.com/llms/live-api-v3.txt):
  - Sessions: 10 minutes minimum cache (server-cached upstream)
  - Multiplayer flights list: 15+ seconds cache
  - Flight route breadcrumbs: 15+ seconds cache
  - Organization aircraft roster: 5 minutes cache
  - Individual positions: 30 seconds cache
  - Max live cache duration: 1 hour

Detects active multiplayer flights, ground aircraft, and stopped airborne flights (including
AP+ holding at TOD) using true route breadcrumbs.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import airportsdata
    _AIRPORTS_DB: Optional[Dict[str, Dict[str, Any]]] = airportsdata.load("ICAO")
except Exception:
    _AIRPORTS_DB = None

from api.if_live_manager import AircraftPersistenceState, IFAircraft, IFAircraftPosition, ScheduledFlightStatus

log = logging.getLogger(__name__)

# Mandatory minimum caching intervals per Infinite Flight rules
TTL_SESSIONS_SECONDS = 600.0        # 10 minutes
TTL_FLIGHTS_SECONDS = 20.0          # 20 seconds (>= 15s rule)
TTL_ROUTES_SECONDS = 60.0           # 60 seconds (>= 15s rule)
TTL_ORGANIZATION_AIRCRAFT = 300.0   # 5 minutes
TTL_ORGANIZATION_SCHEDULES = 60.0   # 60 seconds (open schedules list)
TTL_ORGANIZATION_MEMBERS = 900.0     # 15 minutes
TTL_AIRCRAFT_POSITIONS = 30.0       # 30 seconds
MAX_CACHE_EXPIRY_SECONDS = 3600.0   # 1 hour maximum lifetime


def parse_utc(value: Any) -> Optional[datetime]:
    """Parse string or timestamp into UTC datetime without tzinfo."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        val_str = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(val_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


def utcnow() -> datetime:
    """Current UTC datetime without tzinfo."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


_CALLSIGN_STRIP_SUFFIXES = {
    "HISTORIC",
    "CARGO",
    "CHARTER",
    "VIP",
    "FERRY",
    "TRAINING",
    "TEST",
    "OPERATED",
    "BY",
}

_AIRLINE_ICAO_IATA = {
    "QR": "QTR",
    "QTR": "QR",
    "BA": "BAW",
    "BAW": "BA",
    "EK": "UAE",
    "UAE": "EK",
    "EY": "ETD",
    "ETD": "EY",
    "WY": "OMA",
    "OMA": "WY",
    "SV": "SVA",
    "SVA": "SV",
    "MS": "MSR",
    "MSR": "MS",
    "RJ": "RJA",
    "RJA": "RJ",
    "LH": "DLH",
    "DLH": "LH",
    "AF": "AFR",
    "AFR": "AF",
    "KL": "KLM",
    "KLM": "KL",
}


def clean_callsign_tokens(raw: str) -> Set[str]:
    """Generate normalized lookup tokens for a callsign or registration.

    Handles:
    - Suffixes like ' HISTORIC', ' CARGO'
    - Spaces and hyphens ('QR 843', 'A7-BCB' -> 'QR843', 'A7BCB')
    - Airline code interchangeability (IATA 'QR843' <-> ICAO 'QTR843')
    """
    if not raw:
        return set()
    cleaned = str(raw).strip().upper()
    if not cleaned:
        return set()

    tokens: Set[str] = set()

    # Exact cleaned variant with spaces/hyphens removed
    no_space_hyphen = cleaned.replace(" ", "").replace("-", "")
    if no_space_hyphen:
        tokens.add(no_space_hyphen)

    # Word-based extraction: e.g. 'QR843 HISTORIC' -> 'QR843', 'QR 843 HISTORIC' -> 'QR843'
    parts = [p.replace("-", "") for p in cleaned.split() if p.replace("-", "")]
    if parts:
        first = parts[0]
        if first not in _CALLSIGN_STRIP_SUFFIXES:
            tokens.add(first)

        # Check if first word is airline code and second word is flight number (e.g. 'QR 843')
        if len(parts) >= 2 and parts[1].isdigit():
            tokens.add(first + parts[1])

        # Filter out trailing descriptor tokens
        content_parts = [p for p in parts if p not in _CALLSIGN_STRIP_SUFFIXES]
        if content_parts:
            tokens.add("".join(content_parts))

    # Expand each token with IATA/ICAO airline code mapping
    expanded: Set[str] = set()
    for tok in tokens:
        expanded.add(tok)
        m = re.match(r"^([A-Z]{2,3})(\d+[A-Z]?)$", tok)
        if m:
            airline_code, flt_num = m.groups()
            mapped = _AIRLINE_ICAO_IATA.get(airline_code)
            if mapped:
                expanded.add(f"{mapped}{flt_num}")

    return expanded


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great circle distance between two points in nautical miles."""
    r = 3440.065
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def _bearing_diff(b1: float, b2: float) -> float:
    diff = abs(b1 - b2) % 360.0
    return 360.0 - diff if diff > 180.0 else diff


def _initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r_lat1, r_lon1, r_lat2, r_lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = r_lon2 - r_lon1
    x = math.sin(dlon) * math.cos(r_lat2)
    y = math.cos(r_lat1) * math.sin(r_lat2) - (math.sin(r_lat1) * math.cos(r_lat2) * math.cos(dlon))
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _route_corridor_score(
    cand_lat: float,
    cand_lon: float,
    origin_icao: str,
    dest_icao: str,
    heading: Optional[float] = None,
    speed: Optional[float] = None,
    alt: Optional[float] = None,
) -> float:
    """Score bonus or penalty based on whether a flight's position aligns with the scheduled route."""
    if not _AIRPORTS_DB or not origin_icao or not dest_icao:
        return 0.0
    orig = _AIRPORTS_DB.get(str(origin_icao).strip().upper())
    dest = _AIRPORTS_DB.get(str(dest_icao).strip().upper())
    if not orig or not dest:
        return 0.0

    try:
        orig_lat, orig_lon = float(orig.get("lat", 0.0)), float(orig.get("lon", 0.0))
        dest_lat, dest_lon = float(dest.get("lat", 0.0)), float(dest.get("lon", 0.0))
    except (TypeError, ValueError):
        return 0.0

    dist_orig = _haversine_nm(cand_lat, cand_lon, orig_lat, orig_lon)
    dist_dest = _haversine_nm(cand_lat, cand_lon, dest_lat, dest_lon)
    route_len = _haversine_nm(orig_lat, orig_lon, dest_lat, dest_lon)

    if route_len <= 10.0:
        return 0.0

    score = 0.0
    bearing_to_dest = _initial_bearing(cand_lat, cand_lon, dest_lat, dest_lon)
    h_diff = _bearing_diff(float(heading), bearing_to_dest) if heading is not None else None

    # Departure bonus: flight within 120 NM of origin and at lower altitude
    cur_alt = float(alt or 0.0)
    cur_speed = float(speed or 0.0)
    if dist_orig <= 120.0 and cur_alt <= 20000.0:
        if h_diff is not None and h_diff <= 60.0:
            score += 300.0
        elif h_diff is not None and h_diff >= 120.0:
            score -= 150.0
        else:
            score += 150.0

    # Arrival bonus: flight within 120 NM of destination
    if dist_dest <= 120.0 and cur_alt <= 20000.0:
        if h_diff is not None and h_diff <= 60.0:
            score += 250.0

    # Corridor excess: dist(P, Origin) + dist(P, Destination) - dist(Origin, Destination)
    excess = (dist_orig + dist_dest) - route_len

    if excess <= max(100.0, route_len * 0.08):
        score += 200.0  # Directly along scheduled route corridor
    elif excess <= max(250.0, route_len * 0.15):
        score += 100.0  # Reasonable corridor alignment
    elif excess >= 500.0:
        score -= 250.0  # Severely off-route (e.g. completely different region/corridor)

    # Heading alignment toward destination while enroute
    if cur_speed >= 100.0 and h_diff is not None:
        if h_diff <= 45.0:
            score += 100.0
        elif h_diff >= 120.0:
            score -= 150.0

    return score


@dataclass
class LiveAircraftState:
    """Consolidated live representation of an organization aircraft."""

    # v3 Fleet metadata
    aircraft_id: str                      # Persistent organization aircraft UUID
    content_aircraft_id: str              # Model / livery content UUID
    registration: str                     # e.g. "A7-APC"
    organization_id: str
    status: int                           # 0 = active slot
    visibility: int = 1
    fleet_rank: int = 0
    is_fleet_active_slot: bool = True

    # Position telemetry (consolidated from v3 or v2)
    is_on_ground: bool = True
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    speed: float = 0.0                   # Ground speed (kts)
    heading: float = 0.0
    vertical_speed: float = 0.0
    persistence_state: int = int(AircraftPersistenceState.UNKNOWN)

    # v2 Multiplayer Link
    is_in_multiplayer: bool = False
    v2_flight_id: Optional[str] = None
    multiplayer_session_name: Optional[str] = None
    multiplayer_callsign: Optional[str] = None
    multiplayer_is_connected: Optional[bool] = None

    # Stopped / AP+ detection
    is_stopped: bool = False              # True if stopped in air (AP+ TOD or paused)
    is_actively_flying: bool = False      # True if airborne and actively in multiplayer
    is_parked: bool = False               # True if on the ground
    stopped_since: Optional[datetime] = None
    stopped_at_tod: bool = False          # True if stopped coordinates came from final breadcrumb
    last_pilot_id: Optional[str] = None
    last_pilot_username: Optional[str] = None
    v3_updated_at: Optional[datetime] = None

    # Optional route and schedule data
    route_last_point: Optional[Dict[str, Any]] = None
    schedules: List[Any] = field(default_factory=list)
    active_schedule: Optional[Any] = None
    has_active_routes: bool = False

    @property
    def id(self) -> str:
        """Alias for aircraft_id to support legacy access patterns."""
        return self.aircraft_id

    def __getitem__(self, key: str) -> Any:
        """Support dict-style access."""
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    @property
    def stopped_duration(self) -> Optional[timedelta]:
        """Time elapsed since the aircraft was stopped."""
        if not self.stopped_since:
            return None
        return utcnow() - self.stopped_since

    @property
    def stopped_duration_seconds(self) -> Optional[float]:
        """Seconds elapsed since the aircraft was stopped."""
        duration = self.stopped_duration
        return duration.total_seconds() if duration else None

    @property
    def formatted_status(self) -> str:
        """User-friendly status string."""
        if self.is_stopped:
            return "STOPPED (Needs Pilot)"
        if self.is_actively_flying:
            return "IN_FLIGHT (Active Live)"
        if self.is_parked:
            return "ON_GROUND"
        return "UNKNOWN"

    @property
    def stopped_display_time(self) -> str:
        """Human-readable stopped duration (e.g. '7.0h ago', '42m ago')."""
        seconds = self.stopped_duration_seconds
        if seconds is None:
            return "N/A"
        minutes = seconds / 60
        if minutes < 120:
            return f"{minutes:.0f}m ago"
        hours = minutes / 60
        if hours < 24:
            return f"{hours:.1f}h ago"
        days = hours / 24
        return f"{days:.1f}d ago"

    def get_coordinates(self) -> Tuple[float, float]:
        """Return (latitude, longitude) tuple."""
        return (self.latitude, self.longitude)

    def to_position_dict(self) -> Dict[str, Any]:
        """Convert state to a position mapping compatible with legacy position parsers."""
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude": self.altitude,
            "speed": self.speed,
            "heading": self.heading,
            "verticalSpeed": self.vertical_speed,
            "v2_flight_id": self.v2_flight_id,
            "state": (
                int(AircraftPersistenceState.STOPPED)
                if self.is_stopped
                else (
                    int(AircraftPersistenceState.IN_FLIGHT)
                    if self.is_actively_flying
                    else self.persistence_state
                )
            ),
            "is_on_ground": self.is_on_ground,
            "last_pilot_id": self.last_pilot_id,
            "last_pilot_username": self.last_pilot_username,
            "updated_at": (
                self.stopped_since.isoformat()
                if self.stopped_since
                else (self.v3_updated_at.isoformat() if self.v3_updated_at else None)
            ),
        }


@dataclass
class LiveFleetSnapshot:
    """An immutable-style snapshot of the entire fleet at a given time."""

    aircraft_by_id: Dict[str, LiveAircraftState] = field(default_factory=dict)
    aircraft_by_reg: Dict[str, LiveAircraftState] = field(default_factory=dict)
    fleet_list: List[Any] = field(default_factory=list)
    all_schedules: List[Any] = field(default_factory=list)
    schedules_by_aircraft: Dict[str, List[Any]] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=utcnow)
    refetch_count: int = 0
    cache_age_seconds: float = 0.0

    def __len__(self) -> int:
        return len(self.aircraft_by_id)

    def __iter__(self):
        return iter(self.aircraft_by_id.values())

    def get(self, identifier: str) -> Optional[LiveAircraftState]:
        """Look up aircraft by ID or registration (case-insensitive)."""
        if not identifier:
            return None
        # Try ID match
        if identifier in self.aircraft_by_id:
            return self.aircraft_by_id[identifier]
        # Try registration match
        norm = identifier.strip().upper()
        if norm in self.aircraft_by_reg:
            return self.aircraft_by_reg[norm]
        # Try registration without hyphens
        no_hyphen = norm.replace("-", "")
        for reg, state in self.aircraft_by_reg.items():
            if reg.replace("-", "") == no_hyphen:
                return state
        return None

    def get_stopped_aircraft(self) -> List[LiveAircraftState]:
        """All aircraft currently stopped in the air (requiring a pilot)."""
        return [ac for ac in self.aircraft_by_id.values() if ac.is_stopped]

    def get_active_flights(self) -> List[LiveAircraftState]:
        """All aircraft currently actively flying in multiplayer."""
        return [ac for ac in self.aircraft_by_id.values() if ac.is_actively_flying]

    def get_parked_aircraft(self) -> List[LiveAircraftState]:
        """All aircraft currently on the ground."""
        return [ac for ac in self.aircraft_by_id.values() if ac.is_parked]

    def to_positions_dict(self) -> Dict[str, Dict[str, Any]]:
        """Return a mapping of aircraft_id -> position dict for all aircraft in the snapshot."""
        return {
            ac.aircraft_id: ac.to_position_dict()
            for ac in self.aircraft_by_id.values()
        }


class LiveFleetService:
    """Service that caches and consolidates v3 organization fleet and v2 multiplayer state."""

    def __init__(self, v3_manager, v2_api=None):
        self.v3_manager = v3_manager
        self.v2_api = v2_api

        # Cache entries
        self._fleet_list: Optional[List[IFAircraft]] = None
        self._fleet_list_fetched_at: float = 0.0

        self._v3_positions: Dict[str, IFAircraftPosition] = {}
        self._v3_positions_fetched_at: float = 0.0

        self._schedules: List[Any] = []
        self._schedules_fetched_at: float = 0.0

        self._org_members: Optional[List[Any]] = None
        self._org_members_fetched_at: float = 0.0

        self._v2_sessions: List[Dict[str, Any]] = []
        self._v2_sessions_fetched_at: float = 0.0

        self._v2_flights: Dict[str, Dict[str, Any]] = {}
        self._v2_flights_fetched_at: float = 0.0

        self._v2_routes: Dict[str, Dict[str, Any]] = {}
        self._v2_routes_fetched_at: Dict[str, float] = {}

        # Cached consolidated snapshot
        self._cached_snapshot: Optional[LiveFleetSnapshot] = None
        self._snapshot_created_at: float = 0.0

        # Stats & Metrics
        self.refetch_count: int = 0
        self.cache_hits: int = 0
        self.last_refetch_time: Optional[datetime] = None
        self.api_call_counts: Dict[str, int] = {
            "v3_aircraft": 0,
            "v3_schedules": 0,
            "v3_positions": 0,
            "v3_members": 0,
            "v2_sessions": 0,
            "v2_flights": 0,
            "v2_routes": 0,
        }

        self._lock = asyncio.Lock()

    def get_cache_stats(self) -> Dict[str, Any]:
        """Return current cache usage and refetch statistics."""
        return {
            "refetch_count": self.refetch_count,
            "cache_hits": self.cache_hits,
            "last_refetch_time": self.last_refetch_time.isoformat() if self.last_refetch_time else None,
            "api_call_counts": self.api_call_counts.copy(),
            "cached_aircraft_count": len(self._fleet_list) if self._fleet_list else 0,
            "cached_active_flights_count": len(self._v2_flights),
        }

    def invalidate_cache(self) -> None:
        """Clear all cached data to force a fresh fetch on next call."""
        self._fleet_list_fetched_at = 0.0
        self._v3_positions_fetched_at = 0.0
        self._schedules_fetched_at = 0.0
        self._org_members_fetched_at = 0.0
        self._v2_sessions_fetched_at = 0.0
        self._v2_flights_fetched_at = 0.0
        self._v2_routes_fetched_at.clear()
        self._cached_snapshot = None
        self._snapshot_created_at = 0.0

    async def _get_fleet_list(self, client, org_id: str, force: bool = False) -> List[IFAircraft]:
        now = time.monotonic()
        if not force and self._fleet_list is not None and (now - self._fleet_list_fetched_at < TTL_ORGANIZATION_AIRCRAFT):
            return self._fleet_list

        self.api_call_counts["v3_aircraft"] += 1
        fleet = await client.list_organization_aircraft(org_id)
        self._fleet_list = fleet or []
        self._fleet_list_fetched_at = now
        return self._fleet_list

    async def _get_v3_positions(self, client, fleet: List[IFAircraft], force: bool = False) -> Dict[str, IFAircraftPosition]:
        now = time.monotonic()
        if not force and self._v3_positions and (now - self._v3_positions_fetched_at < TTL_AIRCRAFT_POSITIONS):
            return self._v3_positions

        async def _fetch_one(ac_id: str):
            try:
                self.api_call_counts["v3_positions"] += 1
                return ac_id, await client.get_aircraft_position(ac_id)
            except Exception as e:
                log.debug("Could not fetch v3 position for %s: %s", ac_id, e)
                return ac_id, None

        results = await asyncio.gather(*(_fetch_one(str(ac.id)) for ac in fleet))
        self._v3_positions = {aid: pos for aid, pos in results if pos is not None}
        self._v3_positions_fetched_at = now
        return self._v3_positions

    async def _get_organization_schedules(self, client, org_id: str, force: bool = False) -> List[Any]:
        now = time.monotonic()
        if not force and self._schedules and (now - self._schedules_fetched_at < TTL_ORGANIZATION_SCHEDULES):
            return self._schedules

        schedules = []
        page_number = 1
        total_pages = 1
        while page_number <= total_pages:
            self.api_call_counts["v3_schedules"] += 1
            page = await client.list_organization_schedules(
                org_id, page=page_number, page_size=100
            )
            if page is None or not hasattr(page, "items"):
                raise RuntimeError("Infinite Flight returned an invalid schedule page")
            schedules.extend(page.items or [])
            total_pages = max(1, int(page.total_pages or 1))
            page_number += 1

        self._schedules = schedules
        self._schedules_fetched_at = now
        return self._schedules

    async def _get_organization_members(self, client, org_id: str, force: bool = False) -> List[Any]:
        now = time.monotonic()
        if not force and self._org_members is not None and (now - self._org_members_fetched_at < TTL_ORGANIZATION_MEMBERS):
            return self._org_members

        if hasattr(client, "list_organization_members"):
            try:
                self.api_call_counts["v3_members"] += 1
                members = await client.list_organization_members(org_id)
                if isinstance(members, list):
                    self._org_members = members
                else:
                    self._org_members = []
                self._org_members_fetched_at = now
                return self._org_members
            except Exception as e:
                log.warning("Could not fetch organization members: %s", e)
                return self._org_members or []
        return self._org_members or []

    async def _get_v2_active_flights(self, force: bool = False) -> Dict[str, Dict[str, Any]]:
        if not self.v2_api:
            return {}

        now = time.monotonic()
        if not force and self._v2_flights and (now - self._v2_flights_fetched_at < TTL_FLIGHTS_SECONDS):
            return self._v2_flights

        # 1. Sessions check (10 min TTL)
        if force or not self._v2_sessions or (now - self._v2_sessions_fetched_at >= TTL_SESSIONS_SECONDS):
            try:
                self.api_call_counts["v2_sessions"] += 1
                resp = await self.v2_api.get_sessions()
                self._v2_sessions = resp.get("result", []) if resp else []
                self._v2_sessions_fetched_at = now
            except Exception as e:
                log.warning("Could not fetch v2 sessions: %s", e)

        # 2. Query flights across sessions (20s TTL)
        flights_map: Dict[str, Dict[str, Any]] = {}
        for session in self._v2_sessions:
            sid = session.get("id")
            sname = session.get("name", "Unknown")
            if not sid:
                continue
            try:
                self.api_call_counts["v2_flights"] += 1
                resp = await self.v2_api.get_flights(sid)
                for f in resp.get("result", []) if resp else []:
                    fid = f.get("flightId")
                    if fid:
                        f["session_name"] = sname
                        flights_map[fid] = f
            except Exception as e:
                log.debug("Could not fetch flights for session %s: %s", sname, e)

        self._v2_flights = flights_map
        self._v2_flights_fetched_at = now
        return self._v2_flights

    async def _get_v2_last_route_point(self, aircraft_id: str, force: bool = False) -> Optional[Dict[str, Any]]:
        if not self.v2_api:
            return None

        now = time.monotonic()
        fetched_at = self._v2_routes_fetched_at.get(aircraft_id, 0.0)
        if not force and aircraft_id in self._v2_routes and (now - fetched_at < TTL_ROUTES_SECONDS):
            return self._v2_routes[aircraft_id]

        try:
            self.api_call_counts["v2_routes"] += 1
            resp = await self.v2_api.get_flight_route(aircraft_id)
            points = resp.get("result", []) if resp else []
            last_pt = points[-1] if points else None
            self._v2_routes[aircraft_id] = last_pt
            self._v2_routes_fetched_at[aircraft_id] = now
            return last_pt
        except Exception as e:
            log.debug("Could not fetch v2 route for %s: %s", aircraft_id, e)
            return None

    async def get_live_fleet(
        self,
        organization_id: Optional[str] = None,
        force_refresh: bool = False,
        pilot_id: int = 1,
    ) -> LiveFleetSnapshot:
        """Fetch or return cached snapshot of the organization's fleet states."""
        now = time.monotonic()
        org_id = organization_id or os.getenv("IF_ORGANIZATION_ID", "").strip()
        if not org_id:
            raise ValueError("IF_ORGANIZATION_ID is not configured.")

        # Check snapshot cache (TTL matching individual position freshness)
        if not force_refresh and self._cached_snapshot is not None:
            cache_age = now - self._snapshot_created_at
            if cache_age < TTL_AIRCRAFT_POSITIONS:
                self.cache_hits += 1
                self._cached_snapshot.cache_age_seconds = cache_age
                return self._cached_snapshot

        async with self._lock:
            # Double check after acquiring lock
            if not force_refresh and self._cached_snapshot is not None:
                cache_age = time.monotonic() - self._snapshot_created_at
                if cache_age < TTL_AIRCRAFT_POSITIONS:
                    self.cache_hits += 1
                    self._cached_snapshot.cache_age_seconds = cache_age
                    return self._cached_snapshot

            async with await self.v3_manager.get_client(pilot_id) as client:
                fleet = await self._get_fleet_list(client, org_id, force=force_refresh)
                v3_positions = await self._get_v3_positions(client, fleet, force=force_refresh)
                schedules_list = await self._get_organization_schedules(client, org_id, force=force_refresh)
                v2_flights = await self._get_v2_active_flights(force=force_refresh)

                # Group schedules by aircraft and sort by sequence
                schedules_by_aircraft: Dict[str, List[Any]] = {}
                for schedule in schedules_list:
                    ac_uuid = str(
                        schedule.get("aircraft_id")
                        if isinstance(schedule, dict)
                        else getattr(schedule, "aircraft_id", "")
                    )
                    if ac_uuid:
                        schedules_by_aircraft.setdefault(ac_uuid, []).append(schedule)

                def _get_seq(sch):
                    try:
                        return int(sch.get("sequence", 0) if isinstance(sch, dict) else getattr(sch, "sequence", 0))
                    except (TypeError, ValueError):
                        return 0

                for aid, sch_list in schedules_by_aircraft.items():
                    sch_list.sort(key=_get_seq)

                # Build lookup indexes for matching v2 multiplayer flights to organization aircraft
                v2_by_user: Dict[str, List[Dict[str, Any]]] = {}
                v2_by_callsign: Dict[str, List[Dict[str, Any]]] = {}
                for f in v2_flights.values() if isinstance(v2_flights, dict) else []:
                    if isinstance(f, dict):
                        uid = str(f.get("userId") or "").strip()
                        if uid:
                            v2_by_user.setdefault(uid, []).append(f)
                        raw_cs = str(f.get("callsign") or "").strip()
                        for tok in clean_callsign_tokens(raw_cs):
                            v2_by_callsign.setdefault(tok, []).append(f)

                # Precompute known organization user IDs
                known_org_uids: Set[str] = set()
                org_members = await self._get_organization_members(client, org_id, force=force_refresh)
                if isinstance(org_members, list):
                    for m in org_members:
                        uid = str(getattr(m, "user_id", None) or (m.get("userId") if isinstance(m, dict) else "") or "").strip()
                        if uid:
                            known_org_uids.add(uid)
                for sch in schedules_list:
                    crew_list = getattr(sch, "crew", []) or (sch.get("crew", []) if isinstance(sch, dict) else [])
                    for member in crew_list:
                        m_uid = str(member.get("userId") if isinstance(member, dict) else getattr(member, "userId", "") or "").strip()
                        if m_uid:
                            known_org_uids.add(m_uid)
                for pos in v3_positions.values():
                    if pos and pos.last_pilot_id:
                        p_uid = str(pos.last_pilot_id).strip()
                        if p_uid:
                            known_org_uids.add(p_uid)

                matched_v2_by_aircraft: Dict[str, Dict[str, Any]] = {}
                claimed_v2_flight_ids: Set[str] = set()

                # Pass 1: Direct registration or active schedule callsign matches (exclusive)
                for ac in fleet:
                    ac_id = str(ac.id)
                    reg = str(ac.registration or "Unknown").strip().upper()
                    reg_tokens = clean_callsign_tokens(reg)
                    pos = v3_positions.get(ac_id)
                    ac_last_pilot_uid = str(pos.last_pilot_id).strip() if (pos and pos.last_pilot_id) else None

                    ac_crew_uids: Set[str] = set()
                    for sch in schedules_by_aircraft.get(ac_id, []):
                        crew_list = getattr(sch, "crew", []) or (sch.get("crew", []) if isinstance(sch, dict) else [])
                        for member in crew_list:
                            m_uid = str(member.get("userId") if isinstance(member, dict) else getattr(member, "userId", "") or "").strip()
                            if m_uid:
                                ac_crew_uids.add(m_uid)

                    ac_assigned_uids = set(ac_crew_uids)
                    if not ac_assigned_uids and ac_last_pilot_uid:
                        ac_assigned_uids.add(ac_last_pilot_uid)

                    flight = None
                    for token in reg_tokens:
                        for cand in v2_by_callsign.get(token, []):
                            cand_id = cand.get("flightId")
                            if not cand_id or cand_id in claimed_v2_flight_ids:
                                continue
                            cand_uid = str(cand.get("userId") or "").strip()
                            if ac_assigned_uids and cand_uid not in ac_assigned_uids:
                                continue
                            if known_org_uids and cand_uid not in known_org_uids:
                                continue
                            flight = cand
                            break
                        if flight:
                            break

                    if flight:
                        matched_v2_by_aircraft[ac_id] = flight
                        claimed_v2_flight_ids.add(flight["flightId"])
                        continue

                    for sch in schedules_by_aircraft.get(ac_id, []):
                        sch_raw = sch.get("callsign") if isinstance(sch, dict) else getattr(sch, "callsign", "") or ""
                        sch_tokens = clean_callsign_tokens(sch_raw)
                        sch_crew_uids: Set[str] = set()
                        crew_list = getattr(sch, "crew", []) or (sch.get("crew", []) if isinstance(sch, dict) else [])
                        for member in crew_list:
                            m_uid = str(member.get("userId") if isinstance(member, dict) else getattr(member, "userId", "") or "").strip()
                            if m_uid:
                                sch_crew_uids.add(m_uid)

                        matched_sch_flight = None
                        for tok in sch_tokens:
                            for cand in v2_by_callsign.get(tok, []):
                                cand_id = cand.get("flightId")
                                if not cand_id or cand_id in claimed_v2_flight_ids:
                                    continue
                                cand_uid = str(cand.get("userId") or "").strip()
                                # If the schedule is crewed, candidate MUST be the assigned crew member!
                                if sch_crew_uids and cand_uid not in sch_crew_uids:
                                    continue
                                # If the aircraft has assigned crew, candidate MUST be associated with this aircraft
                                if ac_assigned_uids and cand_uid not in ac_assigned_uids:
                                    continue
                                # In all cases, candidate must be a known org member
                                if known_org_uids and cand_uid not in known_org_uids:
                                    continue
                                matched_sch_flight = cand
                                break
                            if matched_sch_flight:
                                break

                        if matched_sch_flight:
                            matched_v2_by_aircraft[ac_id] = matched_sch_flight
                            claimed_v2_flight_ids.add(matched_sch_flight["flightId"])
                            break

                # Pass 2: Pilot User ID matches (for pilots who fly without matching callsign)
                # Evaluates all candidate aircraft for each unclaimed flight and assigns the flight
                # to the aircraft that best matches the aircraft model, flight phase, and route.
                unclaimed_flights = [
                    f for f in (v2_flights.values() if isinstance(v2_flights, dict) else [])
                    if isinstance(f, dict) and f.get("flightId") and f.get("flightId") not in claimed_v2_flight_ids
                ]

                # Precompute pilot UIDs for each unmatched aircraft
                unmatched_ac = [ac for ac in fleet if str(ac.id) not in matched_v2_by_aircraft]
                ac_pilot_map: Dict[str, Set[str]] = {}
                for ac in unmatched_ac:
                    ac_id = str(ac.id)
                    uids: Set[str] = set()
                    for sch in schedules_by_aircraft.get(ac_id, []):
                        crew_list = getattr(sch, "crew", []) or (sch.get("crew", []) if isinstance(sch, dict) else [])
                        for member in crew_list:
                            m_uid = str(member.get("userId") if isinstance(member, dict) else getattr(member, "userId", "") or "").strip()
                            if m_uid:
                                uids.add(m_uid)
                    pos = v3_positions.get(ac_id)
                    if not uids and pos and pos.last_pilot_id:
                        p_uid = str(pos.last_pilot_id).strip()
                        if p_uid:
                            uids.add(p_uid)
                    ac_pilot_map[ac_id] = uids

                for cand in unclaimed_flights:
                    cand_id = cand.get("flightId")
                    if not cand_id or cand_id in claimed_v2_flight_ids:
                        continue
                    cand_uid = str(cand.get("userId") or "").strip()
                    if not cand_uid:
                        continue

                    cand_cs = str(cand.get("callsign") or "").strip().upper()
                    cand_tokens = clean_callsign_tokens(cand_cs)
                    cand_aircraft_type = str(cand.get("aircraftId") or "").strip()
                    cand_livery_id = str(cand.get("liveryId") or "").strip()
                    cand_speed = float(cand.get("speed", 0.0) or 0.0)
                    cand_alt = float(cand.get("altitude", 0.0) or 0.0)
                    cand_lat = float(cand.get("latitude", 0.0) or 0.0)
                    cand_lon = float(cand.get("longitude", 0.0) or 0.0)
                    cand_heading = float(cand["heading"]) if cand.get("heading") is not None else None
                    is_cand_airborne = cand_speed >= 50 or cand_alt >= 1000

                    # Find all candidate aircraft associated with this pilot
                    best_ac = None
                    best_score = 0

                    for ac in unmatched_ac:
                        ac_id = str(ac.id)
                        if ac_id in matched_v2_by_aircraft:
                            continue
                        if cand_uid not in ac_pilot_map.get(ac_id, set()):
                            continue

                        pos = v3_positions.get(ac_id)
                        reg = str(ac.registration or "Unknown").strip().upper()
                        reg_tokens = clean_callsign_tokens(reg)

                        # Hard contradiction: callsign starts with A7 but is a different registration
                        if cand_cs.startswith("A7") and not cand_tokens.intersection(reg_tokens):
                            continue

                        score = 20  # Base match for pilot ID

                        # 1. Aircraft Model / Livery match
                        ac_content_id = str(ac.aircraft_id or "").strip()
                        if ac_content_id and (cand_aircraft_type or cand_livery_id):
                            if ac_content_id in (cand_aircraft_type, cand_livery_id):
                                score += 150  # Strong model / livery match
                            elif cand_aircraft_type and cand_livery_id and ac_content_id not in (cand_aircraft_type, cand_livery_id):
                                score -= 150  # Different aircraft model penalty

                        # 2. Airborne vs Ground match
                        if pos:
                            pos_is_ground = bool(pos.is_on_ground)
                            pos_state = int(pos.state)
                            pos_airborne = not pos_is_ground or pos_state in {
                                int(AircraftPersistenceState.IN_FLIGHT),
                                int(AircraftPersistenceState.STOPPED),
                            }
                            if is_cand_airborne:
                                if pos_airborne:
                                    score += 100  # Both airborne
                                elif pos_is_ground or pos_state == int(AircraftPersistenceState.ON_GROUND):
                                    score -= 150  # Cruising flight should never match a parked ground aircraft
                            else:
                                if pos_is_ground:
                                    score += 50

                        # 3. Schedule status and route match
                        ac_schedules = schedules_by_aircraft.get(ac_id, [])
                        for sch in ac_schedules:
                            sch_stat = None
                            try:
                                sch_stat = int(sch.get("status") if isinstance(sch, dict) else getattr(sch, "status"))
                            except (TypeError, ValueError):
                                pass
                            if sch_stat == int(ScheduledFlightStatus.IN_FLIGHT):
                                score += 100
                                break

                        # 4. Schedule callsign token match
                        for sch in ac_schedules:
                            s_cs = sch.get("callsign") if isinstance(sch, dict) else getattr(sch, "callsign", "")
                            if cand_tokens.intersection(clean_callsign_tokens(s_cs)):
                                score += 200
                                break

                        # 5. Route corridor alignment match (origin -> destination)
                        best_corridor_score = -999.0
                        for sch in ac_schedules:
                            o_icao = sch.get("origin_icao") if isinstance(sch, dict) else getattr(sch, "origin_icao", "")
                            d_icao = sch.get("destination_icao") if isinstance(sch, dict) else getattr(sch, "destination_icao", "")
                            if o_icao and d_icao:
                                c_score = _route_corridor_score(
                                    cand_lat,
                                    cand_lon,
                                    o_icao,
                                    d_icao,
                                    heading=cand_heading,
                                    speed=cand_speed,
                                    alt=cand_alt,
                                )
                                if c_score > best_corridor_score:
                                    best_corridor_score = c_score

                        if best_corridor_score != -999.0:
                            score += best_corridor_score

                        if score > best_score:
                            best_score = score
                            best_ac = ac

                    if best_ac and best_score > 0:
                        chosen_id = str(best_ac.id)
                        matched_v2_by_aircraft[chosen_id] = cand
                        claimed_v2_flight_ids.add(cand_id)

                def _find_v2_flight(ac_id: str, pos: Optional[IFAircraftPosition] = None, reg: str = "") -> Optional[Dict[str, Any]]:
                    return matched_v2_by_aircraft.get(ac_id)

                # Identify stopped/AP+ aircraft that require v2 route breadcrumbs
                route_fetch_ids = []
                for ac in fleet:
                    ac_id = str(ac.id)
                    reg = str(ac.registration or "Unknown").strip().upper()
                    pos = v3_positions.get(ac_id)
                    if not pos:
                        continue
                    is_ground = bool(pos.is_on_ground)
                    state_val = int(pos.state)
                    matched_v2 = _find_v2_flight(ac_id, pos, reg)
                    # Airborne but not reporting in v2 live multiplayer => stopped / AP+ TOD
                    if not matched_v2 and (not is_ground or state_val == int(AircraftPersistenceState.IN_FLIGHT) or state_val == int(AircraftPersistenceState.STOPPED)):
                        route_fetch_ids.append(ac_id)

                route_last_points: Dict[str, Any] = {}
                if route_fetch_ids and self.v2_api:
                    route_results = await asyncio.gather(
                        *(self._get_v2_last_route_point(aid, force=force_refresh) for aid in route_fetch_ids)
                    )
                    route_last_points = dict(zip(route_fetch_ids, route_results))

                # Build consolidated LiveAircraftState for every aircraft
                snapshot_time = utcnow()
                aircraft_by_id: Dict[str, LiveAircraftState] = {}
                aircraft_by_reg: Dict[str, LiveAircraftState] = {}

                for ac in fleet:
                    ac_id = str(ac.id)
                    reg = str(ac.registration or "Unknown").strip().upper()
                    pos = v3_positions.get(ac_id)
                    v2_flight = _find_v2_flight(ac_id, pos, reg)
                    route_pt = route_last_points.get(ac_id)

                    state = LiveAircraftState(
                        aircraft_id=ac_id,
                        content_aircraft_id=str(ac.aircraft_id),
                        registration=reg,
                        organization_id=str(ac.organization_id),
                        status=int(ac.status),
                        visibility=int(ac.visibility),
                        fleet_rank=int(getattr(ac, "fleet_rank", 0) or 0),
                        is_fleet_active_slot=bool(getattr(ac, "is_fleet_active_slot", True)),
                    )

                    if pos:
                        state.last_pilot_id = str(pos.last_pilot_id) if pos.last_pilot_id else None
                        state.last_pilot_username = str(pos.last_pilot_username) if pos.last_pilot_username else None
                        state.v3_updated_at = parse_utc(pos.updated_at)
                        state.persistence_state = int(pos.state)

                    if v2_flight is not None:
                        # Case 1: Actively flying in live multiplayer
                        state.is_in_multiplayer = True
                        state.v2_flight_id = str(v2_flight.get("flightId") or "")
                        state.multiplayer_session_name = v2_flight.get("session_name")
                        state.multiplayer_callsign = v2_flight.get("callsign")
                        state.multiplayer_is_connected = v2_flight.get("isConnected")

                        state.latitude = float(v2_flight.get("latitude", 0.0))
                        state.longitude = float(v2_flight.get("longitude", 0.0))
                        state.altitude = float(v2_flight.get("altitude", 0.0))
                        state.speed = float(v2_flight.get("speed", 0.0))
                        state.heading = float(v2_flight.get("heading", 0.0))
                        state.vertical_speed = float(v2_flight.get("verticalSpeed", 0.0))

                        # Ground speed threshold for ground vs airborne:
                        # Airliners cannot fly below 50 knots (stall speed > 120 kts),
                        # and taxi speeds in Infinite Flight commonly reach 35 knots.
                        state.is_on_ground = bool(state.speed < 50)
                        state.is_actively_flying = not state.is_on_ground
                        state.is_parked = state.is_on_ground
                        state.is_stopped = False
                        if v2_flight.get("userId"):
                            state.last_pilot_id = str(v2_flight["userId"])
                        if v2_flight.get("username"):
                            state.last_pilot_username = str(v2_flight["username"])

                    elif pos and (
                        not bool(pos.is_on_ground)
                        or int(pos.state) == int(AircraftPersistenceState.IN_FLIGHT)
                        or int(pos.state) == int(AircraftPersistenceState.STOPPED)
                    ):
                        pos_spd = float(pos.speed or 0.0)
                        pos_state = int(pos.state)
                        # An aircraft is truly stopped (e.g. AP+ holding at TOD) if Infinite Flight marked
                        # it STOPPED or its airborne ground speed dropped below flight threshold (< 50 kts).
                        is_truly_stopped = pos_state == int(AircraftPersistenceState.STOPPED) or (pos_spd < 50 and pos_state != int(AircraftPersistenceState.ON_GROUND))

                        if is_truly_stopped:
                            # AP+ holding at TOD or flight paused
                            state.is_stopped = True
                            state.is_actively_flying = False
                            state.is_parked = False
                            state.is_on_ground = False

                            if route_pt is not None:
                                # True final position from v2 route breadcrumb
                                state.route_last_point = route_pt
                                state.stopped_at_tod = True
                                state.latitude = float(route_pt.get("latitude", 0.0))
                                state.longitude = float(route_pt.get("longitude", 0.0))
                                state.altitude = float(route_pt.get("altitude", 0.0))
                                state.speed = float(route_pt.get("groundSpeed", 0.0))
                                state.heading = float(route_pt.get("track", 0.0))
                                state.stopped_since = parse_utc(route_pt.get("date"))
                            else:
                                state.latitude = float(pos.latitude)
                                state.longitude = float(pos.longitude)
                                state.altitude = float(pos.altitude)
                                state.speed = float(pos.speed)
                                state.heading = float(pos.heading)
                                state.vertical_speed = float(pos.vertical_speed)
                                state.stopped_since = state.v3_updated_at
                        else:
                            # Actively cruising on AP+ in background (not stopped)
                            state.is_stopped = False
                            state.is_actively_flying = True
                            state.is_parked = False
                            state.is_on_ground = False
                            state.latitude = float(pos.latitude)
                            state.longitude = float(pos.longitude)
                            state.altitude = float(pos.altitude)
                            state.speed = float(pos.speed)
                            state.heading = float(pos.heading)
                            state.vertical_speed = float(pos.vertical_speed)

                    elif pos:
                        # Case 3: Parked on the ground
                        state.is_parked = True
                        state.is_on_ground = True
                        state.is_stopped = False
                        state.is_actively_flying = False
                        state.latitude = float(pos.latitude)
                        state.longitude = float(pos.longitude)
                        state.altitude = float(pos.altitude)
                        state.speed = float(pos.speed)
                        state.heading = float(pos.heading)

                    # Associate schedules and route availability
                    ac_schedules = schedules_by_aircraft.get(ac_id, [])
                    state.schedules = ac_schedules

                    terminal_statuses = {
                        int(ScheduledFlightStatus.CANCELLED),
                        int(ScheduledFlightStatus.ARRIVED),
                    }

                    def _sch_status(sch):
                        try:
                            return int(sch.get("status") if isinstance(sch, dict) else getattr(sch, "status"))
                        except (TypeError, ValueError):
                            return None

                    state.has_active_routes = any(
                        _sch_status(sch) not in terminal_statuses
                        for sch in ac_schedules
                    )

                    # Determine active schedule
                    if state.is_stopped:
                        # Prefer in-flight schedule
                        state.active_schedule = next(
                            (sch for sch in ac_schedules if _sch_status(sch) == int(ScheduledFlightStatus.IN_FLIGHT)),
                            None
                        )
                        if state.active_schedule is None:
                            # Prefer crewed active schedule in sequence order
                            state.active_schedule = next(
                                (
                                    sch for sch in ac_schedules
                                    if _sch_status(sch) not in terminal_statuses
                                    and (sch.get("crew") if isinstance(sch, dict) else getattr(sch, "crew", None))
                                ),
                                None
                            )
                        if state.active_schedule is None and ac_schedules:
                            state.active_schedule = ac_schedules[0]
                    elif state.is_actively_flying:
                        state.active_schedule = next(
                            (sch for sch in ac_schedules if _sch_status(sch) in {int(ScheduledFlightStatus.IN_FLIGHT), int(ScheduledFlightStatus.DIVERTED)}),
                            None
                        )
                        if state.active_schedule is None and v2_flight and v2_flight.get("callsign"):
                            cs_tokens = clean_callsign_tokens(v2_flight.get("callsign"))
                            state.active_schedule = next(
                                (
                                    sch for sch in ac_schedules
                                    if clean_callsign_tokens(sch.get("callsign") if isinstance(sch, dict) else getattr(sch, "callsign", "")).intersection(cs_tokens)
                                ),
                                None
                            )
                        if state.active_schedule is None:
                            # Prefer crewed active schedule in sequence order
                            state.active_schedule = next(
                                (
                                    sch for sch in ac_schedules
                                    if _sch_status(sch) not in terminal_statuses
                                    and (sch.get("crew") if isinstance(sch, dict) else getattr(sch, "crew", None))
                                ),
                                None
                            )
                        if state.active_schedule is None and ac_schedules:
                            state.active_schedule = ac_schedules[0]

                    aircraft_by_id[ac_id] = state
                    aircraft_by_reg[reg] = state

                self.refetch_count += 1
                self.last_refetch_time = snapshot_time
                self._snapshot_created_at = time.monotonic()

                snapshot = LiveFleetSnapshot(
                    aircraft_by_id=aircraft_by_id,
                    aircraft_by_reg=aircraft_by_reg,
                    fleet_list=list(self._fleet_list or []),
                    all_schedules=schedules_list,
                    schedules_by_aircraft=schedules_by_aircraft,
                    timestamp=snapshot_time,
                    refetch_count=self.refetch_count,
                    cache_age_seconds=0.0,
                )
                self._cached_snapshot = snapshot
                return snapshot

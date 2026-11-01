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
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from api.if_live_manager import AircraftPersistenceState, IFAircraft, IFAircraftPosition, ScheduledFlightStatus

log = logging.getLogger(__name__)

# Mandatory minimum caching intervals per Infinite Flight rules
TTL_SESSIONS_SECONDS = 600.0        # 10 minutes
TTL_FLIGHTS_SECONDS = 20.0          # 20 seconds (>= 15s rule)
TTL_ROUTES_SECONDS = 60.0           # 60 seconds (>= 15s rule)
TTL_ORGANIZATION_AIRCRAFT = 300.0   # 5 minutes
TTL_ORGANIZATION_SCHEDULES = 60.0   # 60 seconds (open schedules list)
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
                v2_by_user: Dict[str, Dict[str, Any]] = {}
                v2_by_callsign: Dict[str, Dict[str, Any]] = {}
                for f in v2_flights.values() if isinstance(v2_flights, dict) else []:
                    if isinstance(f, dict):
                        uid = str(f.get("userId") or "").strip()
                        if uid:
                            v2_by_user[uid] = f
                        cs = str(f.get("callsign") or "").strip().upper().replace(" ", "")
                        if cs:
                            v2_by_callsign[cs] = f

                def _normalize_cs(callsign_str: str) -> str:
                    return str(callsign_str or "").strip().upper().replace(" ", "").replace("-", "")

                matched_v2_by_aircraft: Dict[str, Dict[str, Any]] = {}
                claimed_v2_flight_ids: Set[str] = set()

                # Pass 1: Direct registration or active schedule callsign matches (exclusive)
                for ac in fleet:
                    ac_id = str(ac.id)
                    reg = str(ac.registration or "Unknown").strip().upper()
                    norm_reg = _normalize_cs(reg)

                    flight = v2_by_callsign.get(norm_reg) or v2_by_callsign.get(reg.replace(" ", ""))
                    if flight and flight.get("flightId") not in claimed_v2_flight_ids:
                        matched_v2_by_aircraft[ac_id] = flight
                        claimed_v2_flight_ids.add(flight["flightId"])
                        continue

                    for sch in schedules_by_aircraft.get(ac_id, []):
                        sch_cs = _normalize_cs(sch.get("callsign") if isinstance(sch, dict) else getattr(sch, "callsign", "") or "")
                        if sch_cs and sch_cs in v2_by_callsign:
                            cand = v2_by_callsign[sch_cs]
                            if cand.get("flightId") not in claimed_v2_flight_ids:
                                matched_v2_by_aircraft[ac_id] = cand
                                claimed_v2_flight_ids.add(cand["flightId"])
                                break

                # Pass 2: Pilot User ID matches (for pilots who fly without matching callsign)
                # Enforces that a single multiplayer flight cannot be claimed by multiple aircraft
                for ac in fleet:
                    ac_id = str(ac.id)
                    if ac_id in matched_v2_by_aircraft:
                        continue
                    pos = v3_positions.get(ac_id)
                    reg = str(ac.registration or "Unknown").strip().upper()
                    norm_reg = _normalize_cs(reg)

                    matched = None
                    for sch in schedules_by_aircraft.get(ac_id, []):
                        crew_list = getattr(sch, "crew", []) or (sch.get("crew", []) if isinstance(sch, dict) else [])
                        for member in crew_list:
                            m_uid = str(member.get("userId") if isinstance(member, dict) else getattr(member, "userId", "") or "").strip()
                            if m_uid and m_uid in v2_by_user:
                                cand = v2_by_user[m_uid]
                                if cand.get("flightId") not in claimed_v2_flight_ids:
                                    matched = cand
                                    break
                        if matched:
                            break

                    if not matched and pos and pos.last_pilot_id:
                        p_uid = str(pos.last_pilot_id).strip()
                        if p_uid in v2_by_user:
                            cand = v2_by_user[p_uid]
                            if cand.get("flightId") not in claimed_v2_flight_ids:
                                cand_cs = _normalize_cs(cand.get("callsign"))
                                if not bool(cand_cs and cand_cs.startswith("A7") and cand_cs != norm_reg):
                                    matched = cand

                    if matched and matched.get("flightId") not in claimed_v2_flight_ids:
                        matched_v2_by_aircraft[ac_id] = matched
                        claimed_v2_flight_ids.add(matched["flightId"])

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
                        # Case 2: Airborne but not in multiplayer => STOPPED / AP+ TOD paused
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
                        if state.active_schedule is None and ac_schedules:
                            state.active_schedule = ac_schedules[0]
                    elif state.is_actively_flying:
                        state.active_schedule = next(
                            (sch for sch in ac_schedules if _sch_status(sch) in {int(ScheduledFlightStatus.IN_FLIGHT), int(ScheduledFlightStatus.DIVERTED)}),
                            None
                        )

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

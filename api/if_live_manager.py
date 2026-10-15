import base64
import hashlib
import os
import secrets
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from typing import Any, Optional, Dict, List

import aiohttp


class PersistentOrganizationType(IntEnum):
    AUTO_JOIN = 0
    MANUAL_JOIN = 1
    APPLY_TO_JOIN = 2
    INVITE_ONLY = 3
    SINGLE_MEMBER = 4


class OrganizationOperationType(IntEnum):
    UNDEFINED = 0
    AIRLINE = 1
    CHARTER = 2
    FREIGHT = 3
    MILITARY = 4
    FLIGHT_SCHOOL = 5
    PRIVATE = 6


class WorldType(IntEnum):
    SOLO = 0
    CASUAL = 1
    TRAINING = 2
    EXPERT = 3
    PRIVATE = 4


class PersistentOrganizationRecordStatus(IntEnum):
    ACTIVE = 0
    SUSPENDED = 1
    DELETED = 2


class PersistentAircraftRecordStatus(IntEnum):
    ACTIVE = 0
    DELETED = 1


class PersistentAircraftVisibility(IntEnum):
    UNKNOWN = 0
    VISIBLE = 1
    HANGARED = 2


class ScheduledFlightStatus(IntEnum):
    UNKNOWN = 0
    SCHEDULED = 1
    BOARDING = 2
    BOARDED = 3
    TAXIING_TO_RUNWAY = 4
    IN_FLIGHT = 6
    DIVERTED = 7
    DELAYED = 8
    CANCELLED = 9
    TAXIING_TO_PARKING = 10
    ARRIVED = 11


class PersistentFlightType(IntEnum):
    NONE = 0
    COMMERCIAL = 1
    CHARTER = 2
    CARGO = 3
    TRAINING = 4
    TEST_FLIGHT = 5
    MEDICAL_EMERGENCY = 6
    MILITARY = 7
    VIP_EXECUTIVE = 8
    HUMANITARIAN_RELIEF = 9
    GENERAL_AVIATION = 10
    AIRSHOW = 11
    OTHER = 12


class PersistentOrganizationMemberRole(IntEnum):
    MEMBER = 0
    ADMIN = 1
    OWNER = 2


class AircraftPersistenceState(IntEnum):
    UNKNOWN = 0
    ON_GROUND = 1
    IN_FLIGHT = 2
    CANCELLED = 3
    STOPPED = 4
    MAINTENANCE = 5


class AircraftFlightStatus(IntEnum):
    ACTIVE = 0
    COMPLETED = 1
    CANCELLED = 2


class PersistentFlightCrewMemberRole(IntEnum):
    CAPTAIN = 0
    FIRST_OFFICER = 1


@dataclass
class IFOrganization:
    id: str
    name: str
    type: int
    operation_type: int
    world_type: int
    status: int
    description: str | None = None
    role: int | None = None


@dataclass
class IFOrganizationMember:
    user_id: str
    username: str | None
    role: int
    joined_at: str
    active_lifts: int = 0


@dataclass
class IFAircraft:
    id: str
    aircraft_id: str
    organization_id: str
    registration: str
    status: int
    visibility: int
    fleet_priority: int | None = None
    fleet_rank: int = 0
    is_fleet_active_slot: bool = True
    created_at: str = ""


@dataclass
class IFAircraftPosition:
    id: str
    state: int
    is_on_ground: bool
    latitude: float
    longitude: float
    altitude: float
    heading: float
    speed: float
    vertical_speed: float
    last_pilot_id: str | None = None
    last_pilot_username: str | None = None
    updated_at: str = ""


@dataclass
class IFSchedule:
    id: str
    status: int
    callsign: str
    organization_id: str
    aircraft_id: str
    flight_type: int
    origin_icao: str
    destination_icao: str
    scheduled_departure_utc: str
    scheduled_arrival_utc: str
    is_timeless: bool = False
    actual_departure_utc: str | None = None
    actual_arrival_utc: str | None = None
    briefing: str | None = None
    debriefing: str | None = None
    flight_plan: str | None = None
    crew: list = field(default_factory=list)
    sequence: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass
class IFScheduleRequest:
    callsign: str
    flight_type: PersistentFlightType
    origin_icao: str
    destination_icao: str
    scheduled_departure_utc: str | None = None
    scheduled_arrival_utc: str | None = None
    is_timeless: bool = False
    briefing: str | None = None
    flight_plan: str | None = None


@dataclass
class IFScheduleFlightPlanRequest:
    flight_plan: str | None = None


@dataclass
class IFScheduleReorderRequest:
    schedule_id: str
    after_id: str | None = None


@dataclass
class IFPage:
    items: list
    page: int
    page_size: int
    total_count: int
    total_pages: int


class IFLiveError(Exception):
    def __init__(self, status_code: int, error_code: int, detail: str = ""):
        self.status_code = status_code
        self.error_code = error_code
        self.detail = detail
        super().__init__(f"IF API error {error_code} (HTTP {status_code}): {detail}")


class IFLiveAuthError(IFLiveError):
    pass


class IFLiveForbiddenError(IFLiveError):
    pass


class IFLiveNotFoundError(IFLiveError):
    pass


class IFLiveRateLimitError(IFLiveError):
    pass


_STATUS_ERROR_MAP = {
    401: IFLiveAuthError,
    403: IFLiveForbiddenError,
    404: IFLiveNotFoundError,
    429: IFLiveRateLimitError,
}


def _parse_if_response(body: dict) -> Any:
    error_code = body.get("errorCode", -1)
    if error_code == 0:
        return body.get("result")
    raise IFLiveError(0, error_code, body.get("error", ""))


def _build_if_org(raw: dict) -> IFOrganization:
    return IFOrganization(
        id=raw["id"],
        name=raw["name"],
        type=raw["type"],
        operation_type=raw["operationType"],
        world_type=raw["worldType"],
        status=raw["status"],
        description=raw.get("description"),
        role=raw.get("role"),
    )


def _build_if_member(raw: dict) -> IFOrganizationMember:
    return IFOrganizationMember(
        user_id=raw["userId"],
        username=raw.get("username"),
        role=raw["role"],
        joined_at=raw["joinedAt"],
        active_lifts=raw.get("activeLifts", 0),
    )


def _build_if_aircraft(raw: dict) -> IFAircraft:
    return IFAircraft(
        id=raw["id"],
        aircraft_id=raw["aircraftId"],
        organization_id=raw["organizationId"],
        registration=raw["registration"],
        status=raw["status"],
        visibility=raw["visibility"],
        fleet_priority=raw.get("fleetPriority"),
        fleet_rank=raw.get("fleetRank", 0),
        is_fleet_active_slot=raw.get("isFleetActiveSlot", True),
        created_at=raw.get("createdAt", ""),
    )


def _build_if_position(raw: dict) -> IFAircraftPosition:
    return IFAircraftPosition(
        id=raw["id"],
        state=raw["state"],
        is_on_ground=raw["isOnGround"],
        latitude=raw["latitude"],
        longitude=raw["longitude"],
        altitude=raw["altitude"],
        heading=raw["heading"],
        speed=raw["speed"],
        vertical_speed=raw["verticalSpeed"],
        last_pilot_id=raw.get("lastPilotId"),
        last_pilot_username=raw.get("lastPilotUsername"),
        updated_at=raw.get("updatedAt", ""),
    )


def _build_if_schedule(raw: dict) -> IFSchedule:
    return IFSchedule(
        id=raw["id"],
        status=raw["status"],
        callsign=raw["callsign"],
        organization_id=raw["organizationId"],
        aircraft_id=raw["aircraftId"],
        flight_type=raw["flightType"],
        origin_icao=raw["originIcao"],
        destination_icao=raw["destinationIcao"],
        scheduled_departure_utc=raw["scheduledDepartureUtc"],
        scheduled_arrival_utc=raw["scheduledArrivalUtc"],
        is_timeless=raw.get("isTimeless", False),
        actual_departure_utc=raw.get("actualDepartureUtc"),
        actual_arrival_utc=raw.get("actualArrivalUtc"),
        briefing=raw.get("briefing"),
        debriefing=raw.get("debriefing"),
        flight_plan=raw.get("flightPlan"),
        crew=raw.get("crew", []),
        sequence=raw.get("sequence", 0),
        created_at=raw.get("createdAt", ""),
        updated_at=raw.get("updatedAt", ""),
    )


def _build_if_page(raw: dict, builder) -> IFPage:
    items = [builder(item) for item in raw.get("items", [])]
    return IFPage(
        items=items,
        page=raw.get("page", 1),
        page_size=raw.get("pageSize", 100),
        total_count=raw.get("totalCount", 0),
        total_pages=raw.get("totalPages", 0),
    )


def _schedule_request_body(data: IFScheduleRequest) -> dict:
    body: dict[str, Any] = {
        "callsign": data.callsign,
        "flightType": int(data.flight_type),
        "originIcao": data.origin_icao.upper(),
        "destinationIcao": data.destination_icao.upper(),
    }
    if data.is_timeless:
        body["isTimeless"] = True
    else:
        if data.scheduled_departure_utc is not None:
            body["scheduledDepartureUtc"] = data.scheduled_departure_utc
        if data.scheduled_arrival_utc is not None:
            body["scheduledArrivalUtc"] = data.scheduled_arrival_utc
    if data.briefing is not None:
        body["briefing"] = data.briefing
    if data.flight_plan is not None:
        body["flightPlan"] = data.flight_plan
    return body


def generate_code_verifier(length: int = 64) -> str:
    return secrets.token_urlsafe(length)[:128]


def generate_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


DEFAULT_SCOPES = "openid profile offline_access live:organizations.read live:aircraft.read live:schedules.read live:schedules.write"


class IFLiveClient:
    def __init__(self, access_token: str, base_url: str | None = None, timeout: float = 30.0):
        self.access_token = access_token
        self.base_url = (base_url or os.getenv("IF_API_BASE_URL", "https://api.infiniteflight.com/public/v3")).rstrip("/")
        self._session: aiohttp.ClientSession | None = None
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def open(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Accept": "application/json",
                },
                timeout=self._timeout,
            )
        return self

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    @property
    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            raise RuntimeError("Use 'async with IFLiveClient(...)' or call .open() first")
        return self._session

    async def _request(self, method: str, url: str, **kwargs) -> Any:
        full_url = f"{self.base_url}{url}"
        async with self._http.request(method, full_url, **kwargs) as response:
            body = await response.json(content_type=None)
            if response.status == 200:
                return _parse_if_response(body)
            exc_cls = _STATUS_ERROR_MAP.get(response.status, IFLiveError)
            error_code = body.get("errorCode", response.status) if isinstance(body, dict) else response.status
            detail = body.get("error", body.get("detail", str(body))) if isinstance(body, dict) else str(body)
            raise exc_cls(response.status, error_code, detail)

    async def list_organizations(self) -> list[IFOrganization]:
        result = await self._request("GET", "/live/organizations")
        return [_build_if_org(o) for o in result]

    async def get_organization(self, organization_id: str) -> IFOrganization:
        result = await self._request("GET", f"/live/organizations/{organization_id}")
        return _build_if_org(result)

    async def list_organization_members(self, organization_id: str) -> list[IFOrganizationMember]:
        result = await self._request("GET", f"/live/organizations/{organization_id}/members")
        return [_build_if_member(m) for m in result]

    async def list_organization_aircraft(self, organization_id: str) -> list[IFAircraft]:
        result = await self._request("GET", f"/live/organizations/{organization_id}/aircraft")
        return [_build_if_aircraft(a) for a in result]

    async def list_organization_schedules(self, organization_id: str, page: int = 1, page_size: int = 100, aircraft_ids: list[str] | None = None) -> IFPage:
        params = {"page": page, "pageSize": page_size}
        if aircraft_ids:
            params["aircraftId"] = aircraft_ids
        result = await self._request("GET", f"/live/organizations/{organization_id}/schedules", params=params)
        return _build_if_page(result, _build_if_schedule)

    async def get_aircraft(self, aircraft_id: str) -> IFAircraft:
        result = await self._request("GET", f"/live/aircraft/{aircraft_id}")
        return _build_if_aircraft(result)

    async def get_aircraft_position(self, aircraft_id: str) -> IFAircraftPosition:
        result = await self._request("GET", f"/live/aircraft/{aircraft_id}/position")
        return _build_if_position(result)

    async def list_aircraft_flights(self, aircraft_id: str, page: int = 1, page_size: int = 25) -> IFPage:
        result = await self._request("GET", f"/live/aircraft/{aircraft_id}/flights", params={"page": page, "pageSize": page_size})
        return _build_if_page(result, lambda r: r)

    async def list_aircraft_schedules(self, aircraft_id: str) -> list[IFSchedule]:
        result = await self._request("GET", f"/live/aircraft/{aircraft_id}/schedules")
        return [_build_if_schedule(s) for s in result]

    async def create_schedule(self, aircraft_id: str, data: IFScheduleRequest) -> IFSchedule:
        result = await self._request("POST", f"/live/aircraft/{aircraft_id}/schedules", json=_schedule_request_body(data))
        return _build_if_schedule(result)

    async def update_schedule(self, schedule_id: str, data: IFScheduleRequest) -> IFSchedule:
        result = await self._request("PUT", f"/live/schedules/{schedule_id}", json=_schedule_request_body(data))
        return _build_if_schedule(result)

    async def update_flightplan(self, schedule_id: str, flight_plan: str | None) -> IFSchedule:
        result = await self._request("PUT", f"/live/schedules/{schedule_id}/flightplan", json={"flightPlan": flight_plan})
        return _build_if_schedule(result)

    async def assign_crew(self, schedule_id: str, user_id: str, role: int) -> IFSchedule:
        result = await self._request("PUT", f"/live/schedules/{schedule_id}/crew/{user_id}", json={"role": role})
        return _build_if_schedule(result)

    async def remove_crew(self, schedule_id: str, user_id: str) -> IFSchedule:
        result = await self._request("DELETE", f"/live/schedules/{schedule_id}/crew/{user_id}")
        return _build_if_schedule(result)

    async def reorder_schedules(self, aircraft_id: str, schedule_id: str, after_id: str | None = None) -> bool:
        result = await self._request("PUT", f"/live/aircraft/{aircraft_id}/schedules/reorder", json={"scheduleId": schedule_id, "afterId": after_id})
        return result is True

    async def delete_schedule(self, schedule_id: str) -> bool:
        result = await self._request("DELETE", f"/live/schedules/{schedule_id}")
        return result is True


class IFLiveManager:
    def __init__(self, bot):
        self.bot = bot
        self.client_id = os.getenv("IF_CLIENT_ID")
        self.client_secret = os.getenv("IF_CLIENT_SECRET", "")
        self.redirect_uri = os.getenv("IF_REDIRECT_URI")
        self.auth_base_url = os.getenv("IF_AUTH_BASE_URL", "https://api.infiniteflight.com/auth/v2").rstrip("/")
        self.api_base_url = os.getenv("IF_API_BASE_URL", "https://api.infiniteflight.com/public/v3").rstrip("/")
        self.scopes = os.getenv("IF_SCOPES", DEFAULT_SCOPES)
        self._auth_session: aiohttp.ClientSession | None = None

    async def _get_auth_session(self) -> aiohttp.ClientSession:
        if self._auth_session is None or self._auth_session.closed:
            self._auth_session = aiohttp.ClientSession(
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
        return self._auth_session

    async def close(self):
        if self._auth_session and not self._auth_session.closed:
            await self._auth_session.close()
            self._auth_session = None

    async def _token_request(self, body: dict) -> dict:
        session = await self._get_auth_session()
        url = f"{self.auth_base_url}/connect/token"
        async with session.post(url, data=body) as response:
            if response.status == 200:
                return await response.json()
            text = await response.text()
            try:
                err = await response.json()
                detail = err.get("error_description", text)
            except Exception:
                detail = text
            raise IFLiveAuthError(response.status, 0, detail)

    async def exchange_authorization_code(self, code: str, code_verifier: str) -> dict:
        body = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier,
            "client_id": self.client_id,
        }
        if self.client_secret:
            body["client_secret"] = self.client_secret
        return await self._token_request(body)

    async def refresh_access_token(self, refresh_token: str) -> dict:
        body = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
        if self.client_secret:
            body["client_secret"] = self.client_secret
        return await self._token_request(body)

    def build_authorize_url(self, state: str | None = None, code_challenge: str | None = None) -> tuple[str, str, str]:
        code_verifier = generate_code_verifier()
        challenge = code_challenge or generate_code_challenge(code_verifier)
        state = state or secrets.token_urlsafe(32)
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        url = f"{self.auth_base_url}/connect/authorize?{urllib.parse.urlencode(params)}"
        return url, state, code_verifier

    async def begin_authorization(self, pilot_id: int) -> str:
        url, state, verifier = self.build_authorize_url()
        await self.bot.if_live_tokens_model.set_pending(pilot_id, state, verifier)
        return url

    async def handle_callback(self, pilot_id: int, code: str, state: str) -> dict:
        token_row = await self.bot.if_live_tokens_model.get_pending_by_state(state)
        if token_row is None:
            token_row = await self.bot.if_live_tokens_model.get_by_pilot_id(pilot_id)
            if token_row is None or not token_row.get("access_token", "").startswith("pending:"):
                raise IFLiveAuthError(400, 0, "No pending authorization")

        parts = token_row["access_token"].split(":", 2)
        if len(parts) < 3:
            raise IFLiveAuthError(400, 0, "Corrupt pending authorization")
        saved_state = parts[1]
        code_verifier = parts[2]
        if saved_state != state:
            raise IFLiveAuthError(400, 0, "State mismatch")

        token_data = await self.exchange_authorization_code(code, code_verifier)
        expires_in = token_data.get("expires_in", 1800)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        await self.bot.if_live_tokens_model.save_token(
            pilot_id=pilot_id,
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token", ""),
            expires_at=expires_at,
            scope=token_data.get("scope", ""),
        )
        return token_data

    async def get_client(self, pilot_id: int) -> IFLiveClient:
        token_row = await self.bot.if_live_tokens_model.get_by_pilot_id(pilot_id)
        if token_row is None or not token_row.get("refresh_token"):
            raise IFLiveAuthError(401, 0, "No stored token for this pilot")

        expires_at = token_row.get("expires_at")
        if expires_at:
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(str(expires_at))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if expires_at < now + timedelta(seconds=60):
                await self._refresh_token(token_row)
                token_row = await self.bot.if_live_tokens_model.get_by_pilot_id(pilot_id)

        return IFLiveClient(token_row["access_token"], base_url=self.api_base_url)

    async def _refresh_token(self, token_row: dict) -> None:
        refresh_token = token_row.get("refresh_token", "")
        if not refresh_token:
            raise IFLiveAuthError(401, 0, "No refresh token")
        token_data = await self.refresh_access_token(refresh_token)
        expires_in = token_data.get("expires_in", 1800)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        await self.bot.if_live_tokens_model.update_token(
            pilot_id=token_row["pilot_id"],
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token", refresh_token),
            expires_at=expires_at,
            scope=token_data.get("scope", ""),
        )

    async def revoke(self, pilot_id: int) -> None:
        await self.bot.if_live_tokens_model.delete_token(pilot_id)

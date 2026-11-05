# External API Layer: Architecture & Boundaries

The `api/` package provides asynchronous client interfaces for external third-party services: the Infinite Flight Live API (v2 and v3), the CrewCenter PHP portal, and the OpenRouter / OpenAI AI services.

---

## 🎯 API Layer Responsibilities (What `api/` MUST Do)

1. **Protocol & Network Encapsulation**:
   - Encapsulate all raw HTTP requests, status codes, query parameters, and header authentication.
   - Prevent any raw network details from leaking into Cogs or Services.
2. **Session Lifecycle Management**:
   - Maintain a single, reusable `aiohttp.ClientSession` per API manager for connection reuse and keep-alive.
   - Implement graceful reconnection logic if a session becomes closed or invalidated.
   - Provide clean `connect()` and `close()` lifecycle methods invoked by `bot.py`.
3. **Upstream Rate Limiting & Caching**:
   - Strictly honor upstream provider caching requirements. For Infinite Flight, mandatory caching intervals are required by their API terms of service.
4. **Resilience & Safe Startup**:
   - If an external API is down or missing credentials at bot launch, **the bot must still boot safely**.
   - API managers must log warnings and allow unaffected features to continue running.
5. **Typed Domain Translation & Custom Exceptions**:
   - Parse JSON responses into dataclasses, Enums, or normalized dictionaries.
   - Translate HTTP error codes (401, 403, 404, 429, 500) into typed domain exceptions (`IFLiveAuthError`, `IFLiveNotFoundError`, `IFLiveRateLimitError`).

---

## 🚫 API Layer Anti-Patterns (What `api/` MUST NOT Do)

| Anti-Pattern | Why It Is Prohibited | Proper Alternative |
| :--- | :--- | :--- |
| **Discord Imports or Embeds** | The API layer has zero awareness of Discord. Coupling API code to Discord destroys modularity and testability. | Return plain Python dictionaries or dataclasses; let Cogs handle Discord formatting. |
| **Direct Database Writes** | The API layer handles external I/O. Database operations belong in `database/` models (with the exception of auth token persistence). | Pass retrieved data to Services, which coordinate with Database Models. |
| **Blocking Network Calls (`requests`)** | Blocking HTTP requests freeze the Python `asyncio` event loop and disconnect the bot from the Discord Gateway. | Use `aiohttp` exclusively for HTTP requests. |
| **Ephemeral Ad-Hoc Sessions** | Creating `async with aiohttp.ClientSession():` on every single request wastes TCP sockets and causes SSL handshake bottlenecks. | Use the persistent `self._session` managed by the manager instance. |
| **Uncached Polling** | Excessive calls to Infinite Flight or CrewCenter will trigger IP bans or rate-limit lockouts. | Cache responses using TTL timestamps (e.g. `live_fleet_state.py`). |

---

## 📋 API Managers in Oryxie

| File | Class | Target External Service | Purpose |
| :--- | :--- | :--- | :--- |
| [`api/manager.py`](./infinite-flight.md) | `InfiniteFlightAPIManager` | Infinite Flight Public API v2 | Sessions, flights, aircraft definitions, liveries, user stats |
| [`api/if_live_manager.py`](./infinite-flight.md) | `IFLiveManager`, `IFLiveClient` | Infinite Flight Live API v3 (OAuth2) | Organizations, fleet aircraft, live positions, schedules |
| [`api/live_fleet_state.py`](./infinite-flight.md) | `LiveFleetService`, `LiveFleetSnapshot` | Consolidator / Cache | Caching engine adhering to IF rate limits; stationary flight detection |
| [`api/crewcenter_manager.py`](./crew-center.md) | `CrewCenterAPIManager` | Custom PHP CrewCenter | Pilot authentication, flight logs, automated PIREP filing |
| [`api/oa_manager.py`](./openrouter-ai.md) | `APIManager` | OpenRouter / OpenAI API | LLM generative text for missions and briefings |

---

## 🛡️ Exception Handling Pattern

Every API manager must define or map to clean exception classes so callers can catch specific operational failures:

```python
class IFLiveError(Exception):
    """Base exception for all Infinite Flight Live API errors."""
    pass

class IFLiveAuthError(IFLiveError):
    """Raised when authentication credentials or OAuth tokens are invalid/expired."""
    pass

class IFLiveRateLimitError(IFLiveError):
    """Raised when upstream rate limits are encountered (HTTP 429)."""
    pass

class IFLiveNotFoundError(IFLiveError):
    """Raised when the requested resource does not exist (HTTP 404)."""
    pass
```

When a Cog or Service calls the API, it handles these exceptions gracefully:

```python
try:
    aircraft = await self.bot.if_live_manager.get_aircraft_by_id(aircraft_id)
except IFLiveRateLimitError:
    logger.warning("IF Live rate limit reached; serving cached data.")
    return cached_data
except IFLiveAuthError:
    logger.error("IF OAuth token expired.")
    return None
```

# Infinite Flight API Integration (v2 & v3)

Oryxie integrates extensively with the **Infinite Flight Public API** to provide live fleet tracking, flight verification, oceanic track plotting, and automated pilot PIREP submission.

---

## 🛰️ Integration Overview

The integration spans three dedicated modules:
1. `api/manager.py` (`InfiniteFlightAPIManager`): Public API v2 (API key authentication).
2. `api/if_live_manager.py` (`IFLiveClient` & `IFLiveManager`): Public API v3 (OAuth2 PKCE authentication).
3. `api/live_fleet_state.py` (`LiveFleetService`): High-performance caching and breadcrumb analysis engine.

---

## ⏱️ Mandatory Caching & Polling Rules

Per [Infinite Flight's Live API specifications](https://infiniteflight.com/llms/live-api-v3.txt), the following minimum cache durations **must be strictly enforced** in code:

| Resource | Minimum Cache Duration | Code Constant in `live_fleet_state.py` | Notes |
| :--- | :--- | :--- | :--- |
| **Server Sessions** | **10 minutes** (600s) | `TTL_SESSIONS_SECONDS = 600.0` | Sessions change infrequently; upstream caches heavily. |
| **Multiplayer Flights** | **15 seconds** (20s code) | `TTL_FLIGHTS_SECONDS = 20.0` | Global list of all active flights in a session. |
| **Flight Route Breadcrumbs** | **15 seconds** (60s code) | `TTL_ROUTES_SECONDS = 60.0` | Coordinates flown by an aircraft; polled per active flight. |
| **Organization Aircraft Roster** | **5 minutes** (300s) | `TTL_ORGANIZATION_AIRCRAFT = 300.0` | Persistent airframe registration and state list. |
| **Organization Open Schedules**| **60 seconds** (60s) | `TTL_ORGANIZATION_SCHEDULES = 60.0` | Active flight bookings. |
| **Organization Members** | **15 minutes** (900s) | `TTL_ORGANIZATION_MEMBERS = 900.0` | Member roster and role mappings. |
| **Individual Aircraft Positions** | **30 seconds** (30s) | `TTL_AIRCRAFT_POSITIONS = 30.0` | Lat/Lon/Speed coordinates of a specific airframe. |
| **Maximum Cache Lifespan** | **1 hour** (3600s) | `MAX_CACHE_EXPIRY_SECONDS = 3600.0` | Absolute ceiling before forced purge. |

> [!WARNING]
> Polling Infinite Flight Live API endpoints faster than these intervals can result in automated IP rate-limiting, HTTP 429 errors, and temporary API key revocation.

---

## 🔑 Authentication Architecture

### 1. API Key (v2)
- Environment variable: `IF_API_KEY`.
- Injected via HTTP header: `Authorization: Bearer {IF_API_KEY}`.
- Used for static data: global aircraft models, official liveries, active session IDs, user grade statistics.

### 2. OAuth2 PKCE (v3)
- Environment variables:
  - `IF_CLIENT_ID`
  - `IF_CLIENT_SECRET`
  - `IF_REDIRECT_URI`
  - `IF_SCOPES`
- Used for persistent organization data: Virtual Airline airframes, live schedules, and organization member management.
- Tokens are persisted and refreshed using `database/if_live_tokens_model.py`.

---

## 🧭 Live Fleet State & Stopped Flight Detection

`LiveFleetService` in `api/live_fleet_state.py` tracks active airframes and evaluates true flight progress:

1. **Active Flight Discovery**:
   - Queries session flights and matches callsigns / registrations against QTR Virtual's registered fleet.
2. **Breadcrumb Analysis**:
   - Rather than assuming flight state from a single instantaneous position, `LiveFleetService` inspects the aircraft's route breadcrumb history over time.
3. **Stationary Flight Detection**:
   - Detects when an airborne aircraft (altitude > 10,000 ft or off ground) has maintained a ground speed $< 50$ knots or remained in a tight holding pattern beyond normal Top of Descent (TOD) arrival times.
   - Supplies this state directly to `StoppedFlightMonitor` for alerting.

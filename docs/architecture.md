# System Architecture

Oryxie is built around an asynchronous, event-driven, multi-tier architecture. It interfaces with the Discord Gateway API, external flight simulation APIs, and a centralized MySQL database.

---

## 🏗️ Architectural Overview

The system is structured into four primary tiers with strictly enforced boundaries:

```mermaid
graph TB
    subgraph PresentationTier["1. Presentation Tier (Discord Cogs)"]
        SlashCmds["Slash Commands (/request-flight, etc.)"]
        TaskLoops["Background Loops (@tasks.loop)"]
        EventListeners["Discord Gateway Listeners"]
        WebhookServer["Embedded aiohttp Server (Oryxpace)"]
        UIComponents["Views, Buttons, Modals, Embeds"]
    end

    subgraph ServiceTier["2. Domain & Service Tier"]
        FleetSvc["FleetService (Hubs & Aircraft)"]
        PirepSvc["PirepFilingService & Validation"]
        SimBriefSvc["SimBriefService (OFP XML/JSON)"]
        FlightGenSvc["FlightService (Routing & Limits)"]
        BoardSvc["FlightBoardService"]
        DocGenSvc["PDF & Map Rendering Services"]
    end

    subgraph IntegrationTier["3. External Integration Tier (API)"]
        IFv2["Infinite Flight API v2 (Sessions, Liveries)"]
        IFv3["Infinite Flight API v3 (Live Org, Aircraft)"]
        LiveCache["LiveFleetState (TTL Caching Engine)"]
        CrewCenterAPI["CrewCenter API (PHP Backend)"]
        OpenRouterAI["OpenRouter / OpenAI LLM Client"]
    end

    subgraph PersistenceTier["4. Persistence Tier (Database)"]
        DBMgr["DatabaseManager (aiomysql Pool)"]
        PilotModel["PilotsModel"]
        PirepModel["PirepsModel"]
        RouteModel["RoutesModel / OwdRouteModel"]
        StoppedFlightModel["StoppedFlightModel"]
        StaticAssets["FlightData (CSVs / JSON Assets)"]
    end

    PresentationTier --> ServiceTier
    PresentationTier --> PersistenceTier
    ServiceTier --> IntegrationTier
    ServiceTier --> PersistenceTier
    IntegrationTier --> PersistenceTier
```

---

## 🔄 Core Workflow Sequences

### 1. Flight Booking Workflow (`/request-flight`)

When a pilot books an aircraft:

```mermaid
sequenceDiagram
    autonumber
    actor Pilot as Discord Pilot
    participant Cog as RequestFlightCog
    participant FleetSvc as FleetService
    participant RouteModel as RoutesModel
    participant DB as DatabaseManager
    participant SimBrief as SimBriefService

    Pilot->>Cog: /request-flight [Category/Destination]
    Cog->>Cog: Defer interaction (ephemeral)
    Cog->>FleetSvc: get_claimable_aircraft(criteria)
    FleetSvc->>RouteModel: query available airframes & legs
    RouteModel->>DB: execute SELECT query
    DB-->>RouteModel: return available routes
    RouteModel-->>FleetSvc: route records
    FleetSvc-->>Cog: claimable aircraft list
    Cog-->>Pilot: Render interactive Discord Select Menu / View
    
    Pilot->>Cog: Select airframe and confirm
    Cog->>FleetSvc: reserve_aircraft(pilot_id, airframe_id)
    FleetSvc->>RouteModel: assign_pilot_to_leg()
    RouteModel->>DB: execute UPDATE query
    DB-->>RouteModel: OK
    
    opt Pilot requests SimBrief dispatch
        Cog->>SimBrief: generate_ofp(route_details)
        SimBrief-->>Cog: OFP XML & PDF download link
    end
    
    Cog-->>Pilot: Booking confirmation embed with takeoff window
```

---

### 2. Live Telemetry & Stopped Flight Monitoring

How Oryxie detects stationary flights and triggers recovery waves without exceeding Infinite Flight API rate limits:

```mermaid
sequenceDiagram
    autonumber
    participant Task as StoppedFlightMonitor (@tasks.loop)
    participant Cache as LiveFleetState (TTL Engine)
    participant IFAPI as IFLiveClient / API Manager
    participant DB as StoppedFlightModel
    participant Discord as Discord Channels / DMs

    loop Every 60 Seconds
        Task->>Cache: get_live_fleet_snapshot()
        alt Cache Valid (< TTL)
            Cache-->>Task: Return in-memory cached state
        else Cache Expired
            Cache->>IFAPI: fetch_sessions_and_flights()
            IFAPI-->>Cache: Raw live JSON
            Cache->>Cache: Update TTLs (Sessions: 10m, Flights: 20s)
            Cache-->>Task: Consolidated LiveFleetSnapshot
        end
        
        Task->>Task: Check ground speed, altitude & breadcrumbs
        
        opt Aircraft airborne but stationary (Speed < 50kts)
            Task->>DB: get_or_create_incident(aircraft_id)
            alt In holding/stopped > threshold
                Task->>Discord: Send 30-min ETA warning DM to pilot
                Task->>DB: record_warning_sent()
            else Incident abandoned (> 4 hours)
                Task->>Discord: Post staff alert in STOPPED_FLIGHT_STAFF_CHANNEL_ID
                Task->>Discord: Trigger recovery wave DMs to eligible Fleet role
            end
        end
    end
```

---

## 🔒 Layer Isolation & Dependency Rules

To maintain high stability and testability, components must strictly adhere to the following dependency graph:

1. **Downwards Only**: Upper layers may call lower layers; lower layers must never import or call upper layers.
   - `cogs` $\rightarrow$ `services`, `database`, `api`
   - `services` $\rightarrow$ `database`, `api`, `models`
   - `api` $\rightarrow$ `models` (and auth models in `database`)
   - `database` $\rightarrow$ `models`
2. **Circular Dependency Prevention**:
   - `bot.py` instantiates all managers, services, and models in `setup_hook()`.
   - Dependencies are injected into Cogs via `self.bot` or passed directly to constructors.
   - Never import a Cog from a Service or API module.
3. **Async Everywhere**:
   - Blocking I/O (e.g. `requests`, `time.sleep`, blocking file reading) is forbidden in async event loops.
   - Use `aiohttp` for HTTP, `aiomysql` for DB, `asyncio.sleep` for delays, and `asyncio.to_thread` for CPU-intensive tasks (e.g. ReportLab PDF rendering, matplotlib plotting).

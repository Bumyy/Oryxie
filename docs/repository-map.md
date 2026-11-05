# Repository Map: What Can Be Found Where

This document provides an exhaustive map of the Oryxie codebase. Use this guide to quickly locate features, configuration, assets, business logic, and database schemas.

---

## 📂 Root Directory Structure

```
Oryxie/
├── .agents/                 # AI Agent instructions and localized rule definitions
│   └── rules/               # Architecture, Cog, and API boundary rules
├── .github/
│   └── workflows/           # CI/CD deployment pipelines (e.g. deploy.yaml)
├── api/                     # External API client wrappers and live caching engines
├── assets/                  # Static datasets, logos, templates, and airport databases
├── cogs/                    # Discord bot extensions (Commands, Listeners, UI Views)
├── database/                # Database pool manager, model classes, and SQL migrations
├── docs/                    # Technical documentation, architecture, and developer guides
├── models/                  # Domain dataclasses and data structures
├── scripts/                 # Standalone maintenance utilities and diagnostic scripts
├── services/                # Business logic, calculations, PDF generators, and dispatchers
├── tests/                   # Pytest test suites and unit test mocks
├── AGENTS.md                # AI Agent guidelines, operational rules, and boundaries
├── bot.py                   # Primary application entry point and bot lifecycle manager
├── requirements.txt         # Python project dependencies
├── .env.example             # Template for local environment variables
└── README.md                # Human contributor onboarding and setup guide
```

---

## 🔍 Detailed Component Directory Breakdown

### 1. `cogs/` — Presentation & Discord Interaction Layer
Contains all Discord extension modules loaded dynamically in `bot.py`. Each file in this directory handles Discord-facing commands, modals, buttons, select menus, background task loops (`@tasks.loop`), and event listeners (`@commands.Cog.listener`).

- **Flight Operations**:
  - `request_flight_cog.py`: Interactive flight booking, multi-leg reservations, cargo bookings.
  - `available_aircraft_cog.py`: Search available aircraft by registration or browse claimable routes.
  - `remove_assignment_cog.py`: Release or cancel an active flight reservation.
  - `flight_generator_pdf.py`: Generation of printable Operational Flight Plans (OFP).
  - `checklist_cog.py`: Interactive aircraft checklist viewer and PDF generator.
  - `gate_assignment.py`: Gate allocation at Hamad International (OTHH) and partner hubs.
  - `flight_board_cog.py` & `flight_board_views.py`: Live interactive Discord arrival/departure board.
  - `airline_hubs.py`: Hub ICAO registries and partner airline mappings.
- **Flight Tracking & Safety**:
  - `stopped_flight_monitor.py`: Background monitor detecting stationary airborne aircraft, holding patterns, and dispatching recovery waves.
  - `takeoff_window_monitor.py`: Tracks 6-hour (hub) and 12-hour (outstation) takeoff deadlines.
  - `route_ready_notifications.py`: DM dispatcher notifying next-leg pilots when aircraft arrive and park.
  - `live_flights.py`: Command to view current flights on Infinite Flight.
  - `dm_logging.py`: Centralized utility logging outgoing pilot DMs to staff channels.
- **Pilot & Community Management**:
  - `roster.py`: Pilot roster queries, callsign lookup, and Discord synchronization.
  - `rank_management.py`: Promotions, rank requirements, and flight hour milestones.
  - `member_verification.py`: Onboarding and verification of new community members.
  - `activity_check.py`: Periodic inactivity auditing and status flags.
  - `pilot_reengagement_cog.py`: Direct outreach and re-engagement flows for inactive pilots.
  - `callsign_finder.py`: Callsign vacancy search and conflict detection.
  - `leaderboard.py`: Top pilot rankings (hours, landings, flights).
  - `monthly_stats.py`: Monthly Virtual Airline performance aggregates.
- **PIREPs & Telemetry**:
  - `pireps.py`: Manual flight report submission and verification.
  - `pirep_validator.py`: Automated PIREP validation against Infinite Flight live telemetry.
  - `fleet_pirep_cog.py`: Fleet-specific PIREP filing and airframe status transitions.
  - `ascaris_cog.py`: ACARS telemetry stream tracking.
- **Missions, Training & Gamification**:
  - `mission.py`: VA missions, routes, and pilot mission rewards.
  - `mission_dispatcher.py`: Automated dispatch and rotation of special mission assignments.
  - `special_events.py`: Community events, group flights, and event flight plans.
  - `hajj_operations.py`: Annual seasonal Hajj charter operation workflows.
  - `training.py`: Pilot flight training curriculum and checkrides.
  - `cargo_training.py`: Specialized cargo rating certification.
  - `shop_cog.py`: Oryxie virtual coin shop for roles, cosmetics, and perks.
  - `gift_box.py`: Reward boxes and randomized item generation.
- **Utilities & Support**:
  - `ticket_system.py`: Discord support ticketing and staff modmail.
  - `notification_cog.py`: Embedded HTTP server receiving task updates from Oryxpace.
  - `flight_poll_system.py`: Community destination voting polls.
  - `message_cleaner.py`: Channel cleanup and moderation commands.
  - `database_audit.py`: DB health diagnostics and data integrity checks.
  - `embed_creator.py`: Utility command for crafting structured embeds.
  - `pingpong.py`: Bot latency and heartbeat ping.
  - `utils.py`: Flag inspection and test utilities.

---

### 2. `api/` — External API Integration Layer
Dedicated async clients for external REST/OAuth APIs. Enforces caching, backoff, and authentication.

- `manager.py` (`InfiniteFlightAPIManager`):
  - Primary client for Infinite Flight Public API v2.
  - Fetches sessions, multiplayer flights, oceanic tracks, global aircraft definitions, and liveries.
- `if_live_manager.py` (`IFLiveManager` & `IFLiveClient`):
  - Infinite Flight Live API v3 with OAuth2 PKCE token flow.
  - Interacts with Persistent Organizations, fleet registries, live aircraft status, and live schedules.
- `live_fleet_state.py` (`LiveFleetService`, `LiveAircraftState`):
  - High-performance caching engine adhering to Infinite Flight mandatory rate limits (10m sessions, 20s flights, 60s breadcrumbs, 5m organization roster).
  - Evaluates true breadcrumb flight paths to identify stationary aircraft and TOD holding patterns.
- `crewcenter_manager.py` (`CrewCenterAPIManager`):
  - Client for the PHP-based CrewCenter VA portal.
  - Handles automated pilot PIREP submission, user authorization, and airline statistics.
- `oa_manager.py` (`APIManager`):
  - OpenRouter / OpenAI client wrapper utilizing async streaming and automatic model routing.

---

### 3. `services/` — Business Logic & Generation Layer
Decoupled domain logic, data transformation, and file generation services.

- `fleet_service.py` (`FleetService`): Manages airframe statuses, hubs, claimable flight assignments, and registration matching.
- `flight_board_service.py` (`FlightBoardService`): Compiles departure and arrival flight boards into Discord embed representations.
- `flight_generation_service.py` & `flight_service.py` (`FlightService`): Computes flight routes, calculates nautical miles, flight times, and aircraft restrictions.
- `pdf_service.py` (`PDFService`): Generates PDF flight plan dispatches using ReportLab.
- `checklist_pdf_service.py` (`ChecklistPDFService`): Renders printable aircraft checklist PDFs.
- `pirep_filing_service.py` (`PirepFilingService`): Validates, calculates bonus multipliers, and posts PIREPs to CrewCenter.
- `pirep_validation_service.py` (`PirepValidationService`): Mathematical verification of flight times, ground speeds, touch-down rates, and server authenticity.
- `simbrief_service.py` (`SimBriefService`): Calls SimBrief API to retrieve OFP XML/JSON data and parses route waypoints.
- `route_map_service.py` (`RouteMapService`): Renders orthographic great-circle flight trajectory maps using matplotlib and cartopy.
- `priority_service.py` (`PriorityService`): Calculates pilot VIP priority tiers and event assignment weights.
- `aircraft_classifier.py`: Maps airframes to ICAO wake turbulence categories and type designators.
- `ai_service.py` (`AIService`): Generates narrative mission briefings and AI conversational responses.

---

### 4. `database/` — Persistence & Data Access Layer
Async MySQL database communication via `aiomysql`.

- `manager.py` (`DatabaseManager`):
  - Manages the asynchronous MySQL connection pool (`aiomysql.Pool`).
  - Implements resilient auto-reconnect, retry logic, and parameterized query execution (`fetch_one`, `fetch_all`, `execute`).
- **Model Classes**:
  - `pilots_model.py`: Pilot profiles, Discord ID mapping, IFC usernames, callsigns, and activity states.
  - `pireps_model.py`: Stored flight reports and logbook history.
  - `routes_model.py`: Curated scheduled routes database.
  - `Owd_route_model.py`: One-Way / World Discovery routes.
  - `flight_data.py`: CSV/JSON loader for processed airports, runaways, and airframes.
  - `flight_board_model.py`: State tracking for active Discord flight board messages.
  - `stopped_flight_model.py`: Incident logs, recovery wave tracking, and warning timestamps.
  - `reengagement_model.py`: Re-engagement campaign metrics and delivery status.
  - `shop_model.py`: Shop inventory, purchase logs, and coin balances.
  - `mission_module.py`: Mission objectives, completions, and claims.
  - `event_transaction_model.py`: Event registrations and bonus payouts.
  - `va_data_model.py`: Ranks, aircraft rank-locks, and bonus multipliers.
  - `if_live_tokens_model.py`: Encrypted or persisted OAuth2 tokens for Infinite Flight API.
- **SQL Schemas & Migrations**:
  - `migrate_stopped_flight_eta_warning_columns.sql`
  - `route_ready_notifications_mysql_schema.sql`
  - `stopped_flight_monitor_mysql_schema.sql`
  - `takeoff_window_monitor_mysql_schema.sql`

---

### 5. `models/` — Domain Data Models
Contains typed domain objects and dataclasses.
- `flight_details.py`: `FlightDetails` dataclass capturing departure, arrival, aircraft, callsign, and route metadata.

---

### 6. `assets/` — Static Data & Templates
Static resource files loaded at runtime.
- `airport_database_processed.csv`: ICAO, IATA, names, coordinates, and runway capabilities.
- `aircrafts.json` & `aircraft_data.json`: Liveries, cruise speeds, ranges, and seat configurations.
- `fleet_frames.json`: Airframe registrations, fleet numbers, and engine types.
- `checklist_template.json`: Pre-flight, takeoff, cruise, approach, and landing checklist steps.
- `dignitary_names.json`: Names for VIP / VIP charter passengers.
- `hajj_config.json`: Hajj operational routes, special rules, and airports (OEMA, OEJN, etc.).
- Image assets (e.g. `Amiri flight logo.png`).

---

### 7. `tests/` — Automated Test Suite
Pytest test suites covering core business logic and regression tests:
- `test_airline_hubs.py`: Hub resolution and cargo registration checks.
- `test_fleet_service.py`: Aircraft matching, route claim logic.
- `test_live_fleet_state.py` & `test_live_fleet_integration.py`: Live telemetry caching and rate limit verification.
- `test_stopped_flight_model.py` & `test_aircraft_status_logging.py`: Incident detection and status transitions.
- `test_takeoff_window_monitor.py`: Takeoff deadline countdown tests.
- `test_route_ready_notifications.py`: Route-ready DM triggers and retries.
- `test_pilots_model_case_insensitive.py`: Case-insensitive callsign and pilot lookups.
- `test_if_api_manager_liveries.py`: Livery mapping and endpoint resilience.

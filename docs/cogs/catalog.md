# Cogs Catalog & Functional Boundaries

This catalog specifies the exact responsibilities, trigger commands, and boundaries for each Cog module in the Oryxie bot.

---

## 🗂️ Domain 1: Flight Operations & Dispatch

### `request_flight_cog.py` (`RequestFlightCog`)
- **Primary Responsibility**: Provides the `/request-flight` command interface for pilots to search and book scheduled flights, multi-leg flights, and cargo trips.
- **Commands**:
  - `/request-flight`: Interactive flight booking menu with aircraft selection, destination filters, and SimBrief dispatch integration.
  - Sub-categories: Passenger, Cargo (`A7BFA`), and historical/retired airframes (`A7BFC`).
- **Dependencies**: `FleetService`, `FlightService`, `RoutesModel`, `OwdRouteModel`, `PilotsModel`, `SimBriefService`.
- **Boundaries**: Handles UI dropdowns, modals, and embeds. Route assignment and validation must remain in `FleetService` / `RoutesModel`.

### `available_aircraft_cog.py` (`AvailableAircraftCog`)
- **Primary Responsibility**: Allows pilots and dispatchers to search available airframes and claimable routes across the fleet.
- **Commands**:
  - `/available-aircraft`: Search by registration (supports fuzzy and partial registrations like `FXQK` $\rightarrow$ `IF-FXQK`), or browse all claimable routes.
- **Dependencies**: `FleetService`, `PilotsModel`, `RoutesModel`.
- **Boundaries**: Presentation of fleet availability. Registration normalization is handled via `FleetService` and `airline_hubs.py`.

### `remove_assignment_cog.py` (`RemoveAssignmentCog`)
- **Primary Responsibility**: Cancellation and release of active flight assignments by pilots or staff.
- **Commands**:
  - `/remove-assignment`: Unassigns the pilot from their current reservation and returns the airframe to the open pool.
- **Dependencies**: `FleetService`, `RoutesModel`.

### `flight_generator_pdf.py` (`FlightGeneratorPDF`)
- **Primary Responsibility**: Generates downloadable PDF operational flight releases.
- **Commands**:
  - `/generate-flight-pdf`: Dispatches SimBrief OFP data and creates a formatted PDF document.
- **Dependencies**: `PDFService`, `SimBriefService`, `RouteMapService`.
- **Boundaries**: ReportLab compilation must be executed off the main event loop via `PDFService`.

### `checklist_cog.py` (`ChecklistCog`)
- **Primary Responsibility**: Aircraft checklist commands and PDF checklist exports.
- **Commands**:
  - `/checklist`: Displays step-by-step interactive checklists (Pre-flight, Taxi, Takeoff, Cruise, Descent, Landing, Shutdown).
  - `/checklist-pdf`: Generates a PDF checklist document.
- **Dependencies**: `ChecklistPDFService`, `assets/checklist_template.json`.

### `gate_assignment.py` (`GateAssignment`)
- **Primary Responsibility**: Automatic or manual gate and stand assignments at Doha Hamad International Airport (OTHH) and partner hubs.
- **Commands**:
  - `/assign-gate`: Assigns an authentic terminal/cargo stand based on aircraft type and size.
- **Dependencies**: `FlightData`.

### `flight_board_cog.py` & `flight_board_views.py` (`FlightBoardCog`)
- **Primary Responsibility**: Live Discord departure and arrival flight boards that refresh periodically.
- **Commands & Listeners**:
  - `/setup-flight-board`: Sets up a pinned flight board message in a designated channel.
  - Background task loop updating arrival/departure statuses.
- **Dependencies**: `FlightBoardService`, `FlightBoardModel`, `LiveFleetService`.

### `airline_hubs.py` (Helper Module)
- **Primary Responsibility**: Shared registry mapping aircraft registrations to partner airlines (Qatar Airways, Cathay Pacific, Iberia, British Airways, LATAM, JAL, Qantas, American Airlines) and their respective hub ICAOs.
- **Boundaries**: Configuration helper; does not register commands.

---

## 🛰️ Domain 2: Flight Tracking & Safety Monitoring

### `stopped_flight_monitor.py` (`StoppedFlightMonitor`)
- **Primary Responsibility**: Autonomous monitoring of airborne aircraft to detect stalled flights, holding at Top of Descent (TOD), or abandoned flights.
- **Key Logic**:
  - Tracks true flight route breadcrumbs from Infinite Flight API.
  - Sends 30-minute arrival warnings via DM to the assigned pilot.
  - Dispatches recovery wave notifications to eligible Fleet-role members if an incident persists.
  - Posts incident alerts to `STOPPED_FLIGHT_STAFF_CHANNEL_ID`.
- **Dependencies**: `LiveFleetService`, `StoppedFlightModel`, `dm_logging.py`, `airline_hubs.py`.

### `takeoff_window_monitor.py` (`TakeoffWindowMonitor`)
- **Primary Responsibility**: Enforces takeoff deadline rules after a flight is reserved:
  - **Hub Airport**: 6-hour takeoff window.
  - **Outstation Airport**: 12-hour takeoff window.
- **Key Logic**:
  - Background loop periodically scanning pending reservations.
  - Sends reminder DMs and automatically releases expired reservations to keep the fleet moving.
- **Dependencies**: `RoutesModel`, `PilotsModel`, `dm_logging.py`.

### `route_ready_notifications.py` (`RouteReadyNotifications`)
- **Primary Responsibility**: Notifies next-leg pilots when the incoming leg has completed and parked at the gate.
- **Key Logic**:
  - Detects aircraft arrival at the route origin.
  - Sends a direct message with flight details and the new takeoff window deadline.
  - Retries failed DMs after 5 minutes.
- **Dependencies**: `LiveFleetService`, `RoutesModel`, `dm_logging.py`.

### `live_flights.py` (`LiveFlights`)
- **Primary Responsibility**: Slash command to view real-time operations and current flights in Infinite Flight.
- **Commands**:
  - `/live-flights`: Lists currently active virtual airline flights with callsign, airframe, altitude, and ETA.
- **Dependencies**: `LiveFleetService`.

### `dm_logging.py` (Helper Module)
- **Primary Responsibility**: Centralized utility for posting automated DM delivery reports to `STOPPED_FLIGHT_DM_LOG_CHANNEL_ID` (`1562428318043602965`).
- **Boundaries**: Handles Discord message chunking (2000 character limit) and failure fallback.

---

## 👨‍✈️ Domain 3: Pilot & Community Management

### `roster.py` (`Roster`)
- **Primary Responsibility**: Roster lookup and Discord-to-Infinite Flight callsign synchronization.
- **Commands**:
  - `/roster`: View pilot profiles, callsigns, total hours, and status.
  - Syncs nickname changes with registered callsigns.
- **Dependencies**: `PilotsModel`.

### `rank_management.py` (`RankManagement`)
- **Primary Responsibility**: Tracks flight hour progression, validates qualifications, and automates rank promotions.
- **Commands**:
  - `/promote`: Staff command to advance pilot rank.
  - `/ranks`: Displays rank tier progression and airframe authorizations.
- **Dependencies**: `RankModel`, `PilotsModel`.

### `member_verification.py` (`MemberVerification`)
- **Primary Responsibility**: Onboarding workflow for incoming pilots and Infinite Flight Community (IFC) verification.
- **Commands & Listeners**:
  - `on_member_join`: Assigns non-verified role.
  - `/verify`: Checks IFC profile and assigns official pilot roles.
- **Dependencies**: `PilotsModel`.

### `activity_check.py` (`ActivityCheckCog`)
- **Primary Responsibility**: Automated auditing of pilot flight activity to ensure roster hygiene.
- **Commands**:
  - `/activity-check`: Scans for pilots without flight submissions in the past 30/60 days.
- **Dependencies**: `PilotsModel`, `PirepsModel`.

### `pilot_reengagement_cog.py` (`PilotReengagementCog`)
- **Primary Responsibility**: Automated campaigns re-engaging inactive pilots with personalized flight invitations.
- **Dependencies**: `ReengagementModel`, `dm_logging.py`.

### `callsign_finder.py` (`CallsignFinderCog`)
- **Primary Responsibility**: Identifies available callsigns within the QTR virtual numbering sequence.
- **Commands**:
  - `/find-callsign`: Searches vacant numbers and validates vanity callsign requests.
- **Dependencies**: `PilotsModel`.

### `leaderboard.py` (`LeaderboardCog`)
- **Primary Responsibility**: Gamified community rankings.
- **Commands**:
  - `/leaderboard`: Displays top pilots by flight hours, total landings, and monthly flight counts.
- **Dependencies**: `PirepsModel`, `PilotsModel`.

### `monthly_stats.py` (`MonthlyStatsCog`)
- **Primary Responsibility**: Aggregates VA-wide monthly metrics (total block hours, top routes, fuel consumption).
- **Commands**:
  - `/monthly-stats`: Generates an infographic embed of VA monthly achievements.
- **Dependencies**: `PirepsModel`.

---

## 📝 Domain 4: PIREPs & Telemetry

### `pireps.py` (`Pireps`)
- **Primary Responsibility**: Manual PIREP submission interface and staff review workflow.
- **Commands**:
  - `/file-pirep`: Modal-based flight report filing.
- **Dependencies**: `PirepsModel`, `PirepFilingService`.

### `pirep_validator.py` (`PirepValidator`)
- **Primary Responsibility**: Background or on-demand validation of flight telemetry against the Infinite Flight API to prevent fraudulent logbook entries.
- **Dependencies**: `PirepValidationService`, `InfiniteFlightAPIManager`.

### `fleet_pirep_cog.py` (`FleetPirepCog`)
- **Primary Responsibility**: Links submitted PIREPs directly to specific airframes, transitioning airframe location and operational status.
- **Dependencies**: `FleetService`, `PirepsModel`, `RoutesModel`.

### `ascaris_cog.py` (`AcarsCog`)
- **Primary Responsibility**: ACARS telemetry feed processing and real-time flight stage updates (Climb, Cruise, Descent, Touchdown).
- **Dependencies**: `LiveFleetService`.

---

## 🎯 Domain 5: Missions, Training & Events

### `mission.py` & `mission_dispatcher.py` (`Mission`, `MissionDispatcherCog`)
- **Primary Responsibility**: Curated mission challenges (e.g. VIP Transport, Relief Flights, Historic Routes).
- **Commands**:
  - `/mission`: View available missions and claim rewards.
  - Automated weekly/monthly mission dispatching.
- **Dependencies**: `MissionDB`, `AIService`.

### `special_events.py` (`SpecialEventsCog`)
- **Primary Responsibility**: Support for group flights, fly-ins, and anniversary celebrations.
- **Commands**:
  - `/event-flight`: Event briefing, slot booking, and route distribution.
- **Dependencies**: `EventTransactionModel`, `RoutesModel`.

### `hajj_operations.py` (`HajjOperationsCog`)
- **Primary Responsibility**: Seasonal operations handling charter flights into Jeddah (OEJN) and Medina (OEMA).
- **Dependencies**: `assets/hajj_config.json`, `FlightService`.

### `training.py` & `cargo_training.py` (`TrainingCogV2`, `CargoTraining`)
- **Primary Responsibility**: Pilot flight training program, checkrides, and specialized cargo type rating certifications.
- **Dependencies**: `DatabaseManager`, Discord role assignments.

---

## 💰 Domain 6: Economy & Gamification

### `shop_cog.py` (`ShopCog`)
- **Primary Responsibility**: Virtual coin store where pilots spend earned flight currency.
- **Commands**:
  - `/shop`: Interactive store view.
  - `/buy`: Purchase custom roles, priority passes, and livery unlocks.
- **Dependencies**: `ShopModel`.

### `gift_box.py` (`GiftBoxCog`)
- **Primary Responsibility**: Mystery reward box drops awarded after flight milestones or special achievements.
- **Dependencies**: `ShopModel`.

---

## 🛠️ Domain 7: Administration, Utilities & Integrations

### `notification_cog.py` (`NotificationCog`)
- **Primary Responsibility**: Runs an internal `aiohttp` web server (default port `8081`) listening for webhooks from Oryxpace to send staff task updates via Discord DMs.
- **Endpoints**: `GET /health`, `POST /notify`.
- **Dependencies**: `dm_logging.py`.

### `ticket_system.py` (`TicketSystem`)
- **Primary Responsibility**: Private channel-based support ticketing for pilots needing staff assistance.
- **Commands & Listeners**: Support buttons, modal inquiries, ticket closure transcripts.

### `database_audit.py` (`DatabaseAuditCog`)
- **Primary Responsibility**: Health checks verifying database schema integrity, orphan records, and missing links.
- **Commands**: `/db-audit` (Staff only).
- **Dependencies**: `DatabaseManager`.

### `message_cleaner.py` (`MessageCleaner`)
- **Primary Responsibility**: Channel moderation cleanup command (`/clean`).

### `flight_poll_system.py` (`FlightPollSystem`)
- **Primary Responsibility**: Automated or manual community polling for route preferences.

### `embed_creator.py` (`EmbedCreator`)
- **Primary Responsibility**: Staff tool for formatting polished Discord announcements with embeds.

### `pingpong.py` (`PingPong`)
- **Primary Responsibility**: Heartbeat ping command measuring Discord API and database response latency.

### `utils.py` (`FlagTestUtils`)
- **Primary Responsibility**: Developer utilities and country flag / airport helper tests.

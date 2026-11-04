# Services Layer: Business Logic & Processing

The `services/` directory contains all domain logic, mathematical validations, algorithmic dispatchers, and file generators in Oryxie. It serves as the intermediary between the presentation layer (`cogs/`), data persistence (`database/`), and external APIs (`api/`).

---

## 🎯 Service Responsibilities (What Services MUST Do)

1. **Encapsulate Business Rules**:
   - Validation of flight parameters (touchdown rate, max cruise speed, stall margins).
   - Bonus multiplier calculations based on rank, aircraft type, and active events.
   - Airframe claim eligibility, cargo flight restrictions, and hub turnaround times.
2. **File & Artifact Generation**:
   - Operational Flight Plan (OFP) PDF rendering using ReportLab (`pdf_service.py`).
   - Aircraft checklist generation (`checklist_pdf_service.py`).
   - Great-circle route trajectory map rendering using matplotlib and cartopy (`route_map_service.py`).
3. **Cross-Layer Orchestration**:
   - Calling external APIs via `api/` managers, parsing responses, and updating database models accordingly.
4. **Testability**:
   - Services must be written as pure Python classes that can be tested with unit tests and mocks without running a Discord bot instance.

---

## 🚫 Service Anti-Patterns (What Services MUST NOT Do)

| Anti-Pattern | Why It Is Prohibited | Proper Alternative |
| :--- | :--- | :--- |
| **Sending Discord Messages** | Coupling services to `discord.Interaction` or `discord.TextChannel` prevents reuse in background workers, webhooks, or test suites. | Return result dataclasses, dictionaries, or file streams to the calling Cog. |
| **Defining Slash Commands** | Commands belong strictly in `cogs/`. | Keep services command-agnostic. |
| **Executing Raw Blocking File/CPU Tasks** | Rendering large PDFs or plotting complex maps takes hundreds of milliseconds and will block the event loop. | Run CPU-intensive routines inside `await asyncio.to_thread(...)`. |

---

## 📂 Services Catalog

### `fleet_service.py` (`FleetService`)
- **Role**: Fleet status, airframe assignment, and hub logic.
- **Key Methods**:
  - `get_claimable_aircraft(...)`: Finds aircraft open for booking according to pilot rank and cargo certification.
  - `match_registration(...)`: Fuzzy matches input registrations (e.g. `FXQK` $\rightarrow$ `IF-FXQK`).
  - Hub resolution using `cogs/airline_hubs.py`.

### `pirep_filing_service.py` (`PirepFilingService`)
- **Role**: Coordinates the entire flight log submission lifecycle.
- **Key Methods**:
  - Validates flight data via `PirepValidationService`.
  - Calculates rank bonuses and event multipliers.
  - Submits valid PIREPs to CrewCenter via `CrewCenterAPIManager`.
  - Records filed flight records in `PirepsModel`.

### `pirep_validation_service.py` (`PirepValidationService`)
- **Role**: Mathematical verification of submitted flight logs against Infinite Flight physics and server rules.
- **Checks**:
  - Flight duration consistency against departure/arrival timestamps.
  - Landing vertical speed (FPM) within acceptable safety limits.
  - Infinite Flight server authenticity (Expert Server enforcement).

### `simbrief_service.py` (`SimBriefService`)
- **Role**: Client interface for SimBrief dispatch system.
- **Key Methods**:
  - Generates Operational Flight Plans (OFP).
  - Fetches and parses OFP XML/JSON data (route waypoints, block fuel, contingency fuel, payload, cruise altitude).

### `flight_generation_service.py` & `flight_service.py` (`FlightService`)
- **Role**: Flight route routing algorithms and schedule filtering.
- **Key Methods**:
  - Great-circle distance calculations between airports.
  - Estimated Time Enroute (ETE) calculations based on aircraft cruising profile.

### `flight_board_service.py` (`FlightBoardService`)
- **Role**: Formats active flight states into structured Discord embed tables for live departure/arrival boards.

### `pdf_service.py` (`PDFService`)
- **Role**: Generates professional, multi-page Operational Flight Plan (OFP) PDF documents using ReportLab.
- **Threading Rule**: Calls to `render()` must be offloaded via `asyncio.to_thread`.

### `checklist_pdf_service.py` (`ChecklistPDFService`)
- **Role**: Compiles JSON aircraft checklist templates into printable PDF cards.

### `route_map_service.py` (`RouteMapService`)
- **Role**: Generates flight path map images plotting departure, arrival, waypoints, and orthographic great-circle curves.

### `priority_service.py` (`PriorityService`)
- **Role**: Calculates priority weighting for pilot flight bids and VIP charter slot allocations.

### `aircraft_classifier.py`:
- **Role**: Maps aircraft types to ICAO classifications, engine configurations, and category wake profiles.

### `ai_service.py` (`AIService`):
- **Role**: Generates contextual aviation mission briefings and roleplay narratives using LLMs.

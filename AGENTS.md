# AGENTS.md — Instructions for AI Coding Agents

> **Project**: Oryxie (Discord Bot for Qatari Virtual / Infinite Flight)  
> **Repository**: [https://github.com/Bumyy/Oryxie](https://github.com/Bumyy/Oryxie)  
> **Tech Stack**: Python 3.12, `discord.py` 2.x, `aiomysql`, `aiohttp`, `pytest`

Welcome, AI Agent. This document outlines the mandatory architectural rules, boundary contracts, codebase navigation, and coding standards you must adhere to when operating in this repository.

---

## 🧭 Repository Navigation: What Can Be Found Where

Consult **[`docs/`](./docs/README.md)** for exhaustive technical documentation. Here is your quick navigation index:

| Directory / File | Responsibility | Documentation Link |
| :--- | :--- | :--- |
| **`cogs/`** | Discord Presentation Layer: slash commands, UI components (modals, buttons, embeds), event listeners, background task loops. | [Cogs Architecture](./docs/cogs/overview.md) & [Catalog](./docs/cogs/catalog.md) |
| **`api/`** | External API Layer: async wrappers for Infinite Flight Live API (v2/v3), CrewCenter PHP API, OpenRouter LLM. | [API Architecture](./docs/api/overview.md) |
| **`services/`** | Domain & Business Logic Layer: flight routing, PIREP validation, SimBrief parsing, PDF/map generation. | [Services Overview](./docs/services/overview.md) |
| **`database/`** | Persistence Layer: `DatabaseManager` pool (`aiomysql`), model classes (`PilotsModel`, `RoutesModel`, etc.), SQL migrations. | [Database Overview](./docs/database/overview.md) |
| **`models/`** | Domain data structures & dataclasses (e.g. `FlightDetails`). | [Architecture](./docs/architecture.md) |
| **`assets/`** | Static datasets: airports CSV, aircraft configurations, checklist templates, logos. | [Repository Map](./docs/repository-map.md) |
| **`tests/`** | Pytest test suite covering core business logic, models, and mock integrations. | [Development Guide](./docs/development-guide.md) |
| **`bot.py`** | Bot entry point: dependency initialization (`setup_hook`), extension loading, global command synchronization. | [System Architecture](./docs/architecture.md) |
| **`docs/`** | Complete human & agent technical documentation hub. | [Docs Hub](./docs/README.md) |

---

## 🏛️ Layer Boundaries & Strict Separation of Concerns

Oryxie enforces a strict multi-tier architecture. Never blur layer boundaries:

```
[ Discord Gateway / Users ]
           │
           ▼
┌────────────────────────────────────────────────────────┐
│ 1. PRESENTATION LAYER (cogs/)                          │
│    - Discord slash commands, UI views, modals, embeds  │
│    - Background tasks (@tasks.loop)                    │
│    - NEVER execute SQL queries or raw HTTP calls       │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ 2. BUSINESS LOGIC & SERVICES (services/)               │
│    - Calculations, validations, PDF/map generation     │
│    - NO Discord UI objects or interaction handling     │
│    - Run heavy CPU tasks in worker threads             │
└──────────────┬──────────────────────────┬──────────────┘
               │                          │
               ▼                          ▼
┌───────────────────────────┐  ┌─────────────────────────┐
│ 3. API LAYER (api/)       │  │ 4. PERSISTENCE LAYER    │
│    - External REST/OAuth  │  │    (database/)          │
│    - Caching & TTL rules  │  │    - aiomysql pool      │
│    - Upstream rate limits │  │    - Table model classes│
│    - NO Discord imports   │  │    - Parameterized SQL  │
└───────────────────────────┘  └─────────────────────────┘
```

### 1. Rules for Discord Cogs (`cogs/`)
- **Presentation Only**: Cogs handle input validation, permissions, and Discord formatting.
- **NO Raw SQL Queries**: Call model methods on `self.bot.<model_name>` (e.g., `self.bot.pilots_model.get_pilot_by_callsign(...)`).
- **NO Direct External HTTP**: Call `self.bot.if_api_manager`, `self.bot.cc_api_manager`, or a dedicated service.
- **Always Respect the 3-Second Rule**: Discord interactions timeout after 3 seconds. Call `await interaction.response.defer(ephemeral=...)` immediately before any async I/O or computations.
- **Safe Background Loops**: Wrap `@tasks.loop` logic in `try...except Exception:` with `logger.exception()` so unexpected errors do not kill the task loop permanently. Cancel loops in `cog_unload()`.

### 2. Rules for External APIs (`api/`)
- **No Discord Coupling**: Never import `discord` or create embeds/views in `api/`.
- **Session Pooling**: Reuse a single persistent `aiohttp.ClientSession` across the bot lifecycle. Do not create ad-hoc sessions per request.
- **Strict Infinite Flight Caching Rules**:
  - Server sessions: **10 minutes minimum**.
  - Multiplayer flights list: **15+ seconds minimum** (default 20s).
  - Route breadcrumbs: **15+ seconds minimum** (default 60s).
  - Organization aircraft: **5 minutes minimum**.
  - Positions: **30 seconds minimum**.
- **Typed Exceptions**: Catch HTTP status codes and raise typed domain exceptions (`IFLiveAuthError`, `IFLiveRateLimitError`, `IFLiveNotFoundError`).

### 3. Rules for Services (`services/`)
- **Pure Domain Logic**: Calculations, SimBrief XML parsing, PIREP rules, and PDF/map rendering.
- **Thread Safety for CPU Tasks**: ReportLab rendering or matplotlib plotting must be offloaded using `await asyncio.to_thread(...)` to prevent blocking the async event loop.
- **Framework Independence**: Services must be testable without an active Discord connection.

### 4. Rules for Database (`database/`)
- **Parameterized Queries**: Always use `%s` placeholders. Never interpolate user input directly into SQL queries.
- **Encapsulation**: Put SQL queries inside table model classes in `database/`.
- **Safe Migrations**: Write non-destructive SQL migrations (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`).

---

## 🛡️ Coding & Operational Standards for Agents

1. **Async Discipline**:
   - Every I/O operation must be asynchronous (`aiohttp`, `aiomysql`, `asyncio.sleep`).
   - Never use blocking packages (`requests`, `urllib`, `time.sleep`).
2. **Safe Startup & Resilient Degradation**:
   - If an API key is missing or an external service is down at startup, the bot must log a warning and continue running unaffected features. Never crash `bot.py` startup for optional external services.
3. **Preserve Documentation & Comments**:
   - When modifying files, preserve existing comments and docstrings unless explicitly instructed otherwise.
4. **Git Branching & Workflow**:
   - All active development and modifications must target the `dev` branch.
   - Do not push directly to `main`.
5. **Testing Verification**:
   - After modifying or creating logic, run the test suite:
     ```bash
     pytest
     ```
   - Write unit tests in `tests/` for new business logic or bug fixes using mocks for external APIs and Discord objects.

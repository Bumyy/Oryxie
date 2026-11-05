# Architecture Boundaries & Layer Rules

When creating or modifying code in this repository, enforce strict layer separation:

1. **Presentation Layer (`cogs/`)**:
   - MUST ONLY handle Discord interaction (slash commands, context menus, UI views, buttons, modals, embeds, task loops).
   - NEVER execute raw SQL queries. Call database models via `self.bot.<model_name>`.
   - NEVER perform raw HTTP requests to external services. Call API managers or services.
   - Defer slash command interactions immediately (`await interaction.response.defer()`) if processing could exceed 2.5 seconds.

2. **Business Logic Layer (`services/`)**:
   - Encapsulates pure domain logic, mathematical validations, algorithmic dispatchers, and file generators (PDF, maps).
   - NEVER reference Discord UI objects (`discord.Interaction`, `discord.TextChannel`) directly. Return data structures, dataclasses, or file buffers.
   - Run CPU-intensive routines (PDF compilation, cartopy map plotting) in worker threads with `asyncio.to_thread()`.

3. **External API Layer (`api/`)**:
   - Manages asynchronous external HTTP communication (`aiohttp`).
   - Maintains a single, persistent `aiohttp.ClientSession` per manager.
   - Honors upstream rate limits and mandatory caching intervals (e.g. Infinite Flight Live API rules).
   - Translates raw responses into clean domain objects and raises typed custom exceptions.
   - NEVER import Discord or format Discord messages in API managers.

4. **Persistence Layer (`database/`)**:
   - Centralizes MySQL queries through `DatabaseManager` with connection pooling (`aiomysql`).
   - Every query MUST use parameterized placeholders (`%s`) to prevent SQL injection.
   - Tables and queries belong in dedicated model classes; no raw SQL in upper layers.

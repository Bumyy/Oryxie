# External API Rules & Guidelines

Guidelines for building and modifying API clients in `api/`:

1. **Async Only**:
   - Use `aiohttp.ClientSession` exclusively. Never use blocking HTTP libraries (`requests`, `urllib`).
   - Use a single, persistent session managed by the class lifecycle (`connect()`, `close()`). Do not instantiate new sessions per request.

2. **Infinite Flight Live API Rules**:
   - Strictly observe upstream caching requirements:
     - Sessions: 10 minutes minimum cache.
     - Multiplayer flights list: 15+ seconds cache.
     - Flight breadcrumbs: 15+ seconds cache.
     - Organization aircraft roster: 5 minutes cache.
     - Aircraft positions: 30 seconds cache.
   - Polling faster than these intervals violates terms of service and risks rate-limit bans.

3. **Safe Startup**:
   - The bot must start cleanly even if external credentials (`IF_API_KEY`, `CC_API_KEY`) are missing or external endpoints are temporarily unreachable.
   - Catch initialization errors in `setup_hook()`, log warning/critical messages, and set manager instances to `None` so unaffected bot features remain functional.

4. **Error Handling**:
   - Raise typed custom exceptions (`IFLiveAuthError`, `IFLiveNotFoundError`, `IFLiveRateLimitError`).
   - Do not let raw `aiohttp.ClientResponseError` or unhandled HTTP status codes escape directly into Discord UI handlers.

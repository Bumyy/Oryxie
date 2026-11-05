# Development & Contribution Guide

This guide covers setup instructions, coding conventions, testing procedures, and Git workflows for contributing to Oryxie.

---

## 🛠️ Local Environment Setup

### 1. Prerequisites
- Python 3.11 or 3.12 (Python 3.12 recommended)
- Git
- MySQL 8.0+ (or MariaDB 10.5+)

### 2. Repository Clone & Branching
```bash
git clone https://github.com/Bumyy/Oryxie.git
cd Oryxie

# Switch to the active development branch
git checkout dev
```

> [!IMPORTANT]
> Always branch off and commit changes to `dev`. The `main` branch is strictly reserved for production deployments.

### 3. Virtual Environment & Dependencies
```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux / macOS
source venv/bin/activate

pip install -r requirements.txt
```

### 4. Environment Variables
Copy `.env.example` to `.env` and fill in required values:
```bash
cp .env.example .env
```
Key configuration items:
- `DISCORD_TOKEN`: Bot token from Discord Developer Portal.
- `GUILD_ID`: Discord Server ID of your development/testing server.
- `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`: MySQL connection parameters.
- `IF_API_KEY`: Infinite Flight Live API key.

---

## 🧪 Testing Guidelines

Unit and integration tests are located in `tests/` and run using `pytest`.

### Running Tests
```bash
# Run entire test suite
pytest

# Run a specific test file
pytest tests/test_airline_hubs.py

# Run tests with verbose output
pytest -v
```

### Writing New Tests
- Use `unittest.IsolatedAsyncioTestCase` or `pytest-asyncio` for async methods.
- External APIs (`InfiniteFlightAPIManager`, `CrewCenterAPIManager`) and Discord interactions (`discord.Interaction`) must be mocked using `unittest.mock.AsyncMock` or `MagicMock`. Never hit live external production APIs in automated unit tests.

---

## 📐 Python Coding Conventions

1. **Async Everywhere**:
   - All I/O operations must be asynchronous.
   - Never use blocking libraries (`requests`, `urllib.request`, `time.sleep`). Use `aiohttp` and `asyncio.sleep`.
2. **Type Hinting**:
   - Add type hints to all function signatures and public class attributes.
3. **Logging Over Printing**:
   - Use Python's built-in `logging` module (`logger = logging.getLogger(__name__)`).
   - Use `logger.exception()` inside `try...except` blocks to record stack traces.
4. **Layer Separation**:
   - Keep Cogs focused on presentation and Discord interaction.
   - Keep business logic in `services/`.
   - Keep database queries in `database/`.
   - Keep third-party HTTP requests in `api/`.

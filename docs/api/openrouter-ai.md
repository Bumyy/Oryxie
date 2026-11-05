# OpenRouter / OpenAI AI Integration

Oryxie integrates with large language models (LLMs) via **OpenRouter** or direct **OpenAI** APIs to power narrative mission briefings, dynamic operational scenarios, and contextual assistance.

---

## ⚙️ Configuration

Defined in `.env`:
```env
OPENROUTER_API_KEY=your_openrouter_api_key
# Optional direct OpenAI fallback
OPENAI_API_KEY=your_openai_api_key
```

---

## 🛠️ Architecture: `APIManager` (`api/oa_manager.py`) & `AIService` (`services/ai_service.py`)

### 1. `APIManager` (API Client)
- Located in `api/oa_manager.py`.
- Instantiates an asynchronous `AsyncOpenAI` client pointing to `https://openrouter.ai/api/v1`.
- Configures required OpenRouter HTTP headers (`HTTP-Referer`, `X-Title`).
- Uses model routing (e.g. `openrouter/auto`) to dynamically select available inference models without hardcoded vendor locks.

### 2. `AIService` (Domain Service)
- Located in `services/ai_service.py`.
- Acts as the domain wrapper consumed by Cogs (e.g. `mission.py`).
- Constructs prompt templates for aviation scenarios, VIP dignitary profiles, and operational mission descriptions.
- Ensures graceful fallback if the `openai` Python dependency is not installed or API keys are missing.

---

## 🚫 Boundaries
- **No Direct Discord Interactions**: `APIManager` does not format Discord embeds. It returns raw generated text tuples `(reply_text, model_used)`.
- **Safe Fallback**: If LLM generation fails or times out, the calling Cog must fallback to a pre-defined static template without raising an unhandled exception to the user.

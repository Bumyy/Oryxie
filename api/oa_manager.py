''' import os
import logging
from dotenv import load_dotenv
from openai import AsyncOpenAI

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("APIManager")

# Load credentials
load_dotenv()

class APIManager:
    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        
        if not self.api_key:
            logger.critical("❌ OPENROUTER_API_KEY is missing from .env file!")

        # Initialize the ASYNC client (Crucial for Discord Cogs)
        self.client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self.api_key,
            default_headers={
                "HTTP-Referer": "https://discord.com", # Required by OpenRouter
                "X-Title": "Flight Bot",
            }
        )

        # THE MAGIC SETTING:
        # "openrouter/auto" tells the system to pick the best model automatically.
        # No specific model names are hardcoded here.
        self.default_model = "openrouter/auto"

    async def get_response(self, prompt: str, system_instruction: str = None) -> str:
        """
        Asynchronously calls OpenRouter using the 'auto' model.
        """
        messages = []

        # 1. Add System Instruction (Pilot Persona, etc.)
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})

        # 2. Add User Prompt
        messages.append({"role": "user", "content": prompt})

        try:
            logger.debug(f"Sending request to OpenRouter (Model: {self.default_model})...")

            # Call the API
            response = await self.client.chat.completions.create(
                model=self.default_model,
                messages=messages
            )

            # Extract text
            reply_text = response.choices[0].message.content
            return reply_text

        except Exception as e:
            logger.error(f"⚠️ API Error: {e}")
            return "Error: Unable to reach AI control tower."

# Create a single instance to import in your Cogs
ai_manager = APIManager() ###'''
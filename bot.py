import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv

from database.manager import DatabaseManager
from database.pireps_model import PirepsModel

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True


class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='!', intents=intents)
        self.db_manager = None
        self.pireps_model = None

    async def setup_hook(self):
        """
        Called before the bot connects to Discord.
        Good for initializing things that need the bot instance but don't need to be connected yet.
        """
        self.db_manager = DatabaseManager(self)
        self.pireps_model = PirepsModel(self.db_manager)

        print("DatabaseManager instance created.")

        print("Loading extensions...")
        try:
            await self.load_extension('cogs.pingpong')
            await self.load_extension('cogs.pireps')
            await self.load_extension('cogs.cargo_training')

            print("All cogs loaded.")
        except Exception as e:
            print(f"Failed to load one or more cogs: {e}")

    async def on_ready(self):
        """
        Called when the bot has successfully connected to Discord.
        This is the ideal place to connect to the database pool.
        """
        print(f'Logged in as {self.user} (ID: {self.user.id})')

        if self.db_manager and self.db_manager.pool is None:
            await self.db_manager.connect()

        print("Database connection pool established.")

        try:
            print("Syncing slash commands...")
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} slash command(s) globally.")
        except Exception as e:
            print(f"Failed to sync slash commands globally: {e}")

    async def on_disconnect(self):
        """
        Called when the bot disconnects from Discord.
        Ensures the database pool is properly closed.
        """
        print("Bot disconnected. Attempting to close database pool...")
        if self.db_manager:
            await self.db_manager.close()
            self.db_manager = None 

async def start_bot():
    """
    Function to create and run the bot.
    """
    bot = MyBot()

    if TOKEN is None:
        print("ERROR: DISCORD_BOT_TOKEN environment variable not set.")
        print("Please create a .env file in the same directory as this script and add DISCORD_BOT_TOKEN=YOUR_TOKEN_HERE")

    async with bot:
        await bot.start(TOKEN)

if __name__ == '__main__':
    asyncio.run(start_bot())
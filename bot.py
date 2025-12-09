import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv
import logging
import logging.handlers

from database.manager import DatabaseManager
from database.pireps_model import PirepsModel
from database.routes_model import RoutesModel
from database.pilots_model import PilotsModel
from database.event_transaction_model import EventTransactionModel
from database.flight_data import FlightData
from database.shop_model import ShopModel
from database.mission_module import MissionDB
from api.manager import InfiniteFlightAPIManager
from services.ai_service import AIService
from services.flight_generation_service import FlightService
from services.pdf_service import PDFService

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guild_scheduled_events = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='!', intents=intents)
        self.db_manager: DatabaseManager = None
        self.pireps_model: PirepsModel = None
        self.routes_model: RoutesModel = None
        self.pilots_model: PilotsModel = None
        self.event_transaction_model: EventTransactionModel = None
        self.shop_model: ShopModel = None
        self.mission_db: MissionDB = None
        self.flightdata: FlightData = None
        self.if_api_manager: InfiniteFlightAPIManager = None
        self.aircraft_name_map = {}
        self.livery_cache = {}
        # Services
        self.ai_service: AIService = None
        self.flight_service: FlightService = None
        self.pdf_service: PDFService = None

    async def setup_hook(self):
        """
        Called before the bot connects to Discord.
        Initialize all managers and models here.
        """
        # --- Initialize Database Manager ---
        self.db_manager = DatabaseManager(self)
        self.pireps_model = PirepsModel(self.db_manager)
        self.routes_model = RoutesModel(self.db_manager)
        self.pilots_model = PilotsModel(self.db_manager)
        self.event_transaction_model = EventTransactionModel(self.db_manager)
        self.shop_model = ShopModel(self.db_manager)
        self.mission_db = MissionDB(self.db_manager)
        
        # --- Initialize Flight Data ---
        self.flightdata = FlightData()
        
        # --- Initialize Services ---
        self.ai_service = AIService()
        self.flight_service = FlightService(self.flightdata)
        self.pdf_service = PDFService()
        print("DatabaseManager, FlightData, and Services instances created.")
        
        # --- Initialize API Manager ---
        try:
            self.if_api_manager = InfiniteFlightAPIManager(self)
            await self.if_api_manager.connect()
            print("Infinite Flight API Manager initialized.")
        except ValueError as e:
            print(f"ERROR: {e}")

         # --- Populate the aircraft name map ---
        print("\n=== AIRCRAFT DATA FETCH DEBUG ===")
        print("Fetching aircraft data from Infinite Flight API...")
        aircraft_data = await self.if_api_manager.get_aircraft()
        print(f"Raw aircraft_data type: {type(aircraft_data)}")
        print(f"Aircraft data keys: {aircraft_data.keys() if aircraft_data else 'None'}")
        
        if aircraft_data and aircraft_data.get('result'):
            print(f"Number of aircraft in result: {len(aircraft_data['result'])}")
            print("\nFirst 3 aircraft samples:")
            for i, aircraft in enumerate(aircraft_data['result'][:3]):
                print(f"  [{i}] ID: {aircraft.get('id')} | Name: {aircraft.get('name')}")
            
            self.aircraft_name_map = {
                aircraft['id']: aircraft['name'] 
                for aircraft in aircraft_data['result']
            }
            print(f"\nSuccessfully loaded {len(self.aircraft_name_map)} aircraft names.")
            print(f"Sample IDs in map: {list(self.aircraft_name_map.keys())[:5]}")
        else:
            print("WARNING: Could not load aircraft names from the API. Aircraft will show as 'Unknown'.")
            print(f"aircraft_data value: {aircraft_data}")
        print("=== END AIRCRAFT DATA DEBUG ===\n")
            
        print("Loading extensions...")
        try:
            await self.load_extension('cogs.pingpong')
            await self.load_extension('cogs.pireps')
            await self.load_extension('cogs.roster')
            await self.load_extension('cogs.training')
            await self.load_extension('cogs.cargo_training')
            await self.load_extension('cogs.utils')
            await self.load_extension('cogs.flight_generator_pdf')
            #await self.load_extension('cogs.special_events')
            await self.load_extension('cogs.shop_cog')
            await self.load_extension('cogs.mission')
            await self.load_extension('cogs.gate_assignment')
            await self.load_extension('cogs.callsign_finder')
            await self.load_extension('cogs.flight_poll_system')
            await self.load_extension('cogs.ticket_system')
            await self.load_extension('cogs.pirep_validator')
            
            #await self.load_extension('cogs.live_flights')
            #await self.load_extension('cogs.remainder')
            
            
            print("All cogs loaded.")
        except Exception as e:
            print(f"Failed to load one or more cogs: {e}")

    async def on_ready(self):
        """
        Called when the bot has successfully connected to Discord.
        """
        print(f'Logged in as {self.user} (ID: {self.user.id})')

        if self.db_manager and self.db_manager._pool is None:
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
        Ensures all connections and sessions are properly closed.
        """
        print("Bot disconnected. Cleaning up resources...")
        if self.db_manager:
            await self.db_manager.close()
        
        if self.if_api_manager:
            await self.if_api_manager.close()

async def start_bot():
    """
    Function to create and run the bot.
    """
    handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')

    bot = MyBot()

    if TOKEN is None:
        print("ERROR: DISCORD_BOT_TOKEN environment variable not set.")
        print("Please create a .env file in the same directory as this script and add DISCORD_BOT_TOKEN=YOUR_TOKEN_HERE")

    async with bot:
        await bot.start(TOKEN)

if __name__ == '__main__':
    logger = logging.getLogger('discord')
    logger.setLevel(logging.INFO)

    oryxie_logger = logging.getLogger('oryxie')
    oryxie_logger.setLevel(logging.INFO)
    
    handler = logging.handlers.RotatingFileHandler(
        filename='discord.log',
        encoding='utf-8',
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=5,
    )

    dt_fmt = '%Y-%m-%d %H:%M:%S'
    formatter = logging.Formatter('[{asctime}] [{levelname:<8}] {name}: {message}', dt_fmt, style='{')
    
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    oryxie_logger.addHandler(handler)

    try:
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        print("Bot shut down by user.")
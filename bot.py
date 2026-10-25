# Bot.py
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
from database.Owd_route_model import OwdRouteModel
from database.pilots_model import PilotsModel
from database.event_transaction_model import EventTransactionModel
from database.flight_data import FlightData
from database.shop_model import ShopModel
from database.flight_board_model import FlightBoardModel
from database.mission_module import MissionDB
from database.va_data_model import RankModel, AircraftModel, MultiplierModel
from api.manager import InfiniteFlightAPIManager
from api.crewcenter_manager import CrewCenterAPIManager
from api.if_live_manager import IFLiveManager
from database.if_live_tokens_model import IFLiveTokensModel
from database.stopped_flight_model import StoppedFlightModel
try:
    from services.ai_service import AIService
except ImportError:
    print("Warning: 'openai' module not found. AI features will be disabled.")
    AIService = None
from services.flight_generation_service import FlightService
from services.pdf_service import PDFService
from services.route_map_service import RouteMapService
from services.checklist_pdf_service import ChecklistPDFService
from services.simbrief_service import SimBriefService
from services.flight_board_service import FlightBoardService
from services.pirep_filing_service import PirepFilingService
from services.fleet_service import FleetService

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True
intents.guild_scheduled_events = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='!', intents=intents)
        self.db_manager: DatabaseManager = None
        self.pireps_model: PirepsModel = None
        self.routes_model: RoutesModel = None
        self.owd_route_model: OwdRouteModel = None
        self.pilots_model: PilotsModel = None
        self.event_transaction_model: EventTransactionModel = None
        self.shop_model: ShopModel = None
        self.mission_db: MissionDB = None
        self.flight_board_model: FlightBoardModel = None
        self.stopped_flight_model: StoppedFlightModel = None
        self.flightdata: FlightData = None
        # VA Data Models
        self.rank_model: RankModel = None
        self.aircraft_model: AircraftModel = None
        self.multiplier_model: MultiplierModel = None
        self.if_api_manager: InfiniteFlightAPIManager = None
        self.cc_api_manager: CrewCenterAPIManager = None
        self.if_live_manager: IFLiveManager = None
        self.if_live_tokens_model: IFLiveTokensModel = None
        self.aircraft_name_map = {}
        self.livery_name_map = {}
        self.livery_cache = {}
        # Services
        self.ai_service: AIService = None
        self.flight_service: FlightService = None
        self.pdf_service: PDFService = None
        self.route_map_service: RouteMapService = None
        self.checklist_pdf_service: ChecklistPDFService = None
        self.simbrief_service: SimBriefService = None
        self.flight_board_service: FlightBoardService = None
        self.pirep_filing_service: PirepFilingService = None
        self.fleet_service: FleetService = None

    async def setup_hook(self):
        """
        Called before the bot connects to Discord.
        Initialize all managers and models here.
        """
        # --- Initialize Database Manager ---
        self.db_manager = DatabaseManager(self)
        self.pireps_model = PirepsModel(self.db_manager)
        self.routes_model = RoutesModel(self.db_manager)
        self.owd_route_model = OwdRouteModel(self.db_manager)
        self.pilots_model = PilotsModel(self.db_manager)
        self.event_transaction_model = EventTransactionModel(self.db_manager)
        self.shop_model = ShopModel(self.db_manager)
        self.mission_db = MissionDB(self.db_manager)
        self.flight_board_model = FlightBoardModel(self.db_manager)
        self.stopped_flight_model = StoppedFlightModel(self.db_manager)
        
        # Initialize VA Data Models
        self.rank_model = RankModel(self.db_manager)
        self.aircraft_model = AircraftModel(self.db_manager)
        self.multiplier_model = MultiplierModel(self.db_manager)
        
        # --- Initialize Flight Data ---
        self.flightdata = FlightData()
        
        # --- Initialize Services ---
        self.ai_service = AIService() if AIService else None
        self.flight_service = FlightService(self.flightdata)
        self.pdf_service = PDFService()
        self.route_map_service = RouteMapService()
        self.checklist_pdf_service = ChecklistPDFService()
        self.simbrief_service = SimBriefService()
        self.flight_board_service = FlightBoardService(self)
        self.pirep_filing_service = PirepFilingService(self)
        self.fleet_service = FleetService(self)
        self.auto_pirep_service = None  # Lazy loaded in cog
        print("DatabaseManager, FlightData, and Services instances created.")
        
        # --- Initialize API Manager (SAFE STARTUP) ---
        self.if_api_manager = None
        self.cc_api_manager = None
        aircraft_data = None

        try:
            self.if_api_manager = InfiniteFlightAPIManager(self)
            await self.if_api_manager.connect()
            print("Infinite Flight API Manager initialized.")

            self.cc_api_manager = CrewCenterAPIManager(self)
            await self.cc_api_manager.connect()
            print("Crew Center API Manager initialized.")

            self.if_live_tokens_model = IFLiveTokensModel(self.db_manager)
            self.if_live_manager = IFLiveManager(self)
            print("IF Live Manager initialized.")

            aircraft_data = await self.if_api_manager.get_aircraft()
        except Exception as e:
            print(f"[IF API ERROR] {e}")
            self.if_api_manager = None
            self.cc_api_manager = None
            self.if_live_manager = None
            self.if_live_tokens_model = None
            aircraft_data = None

        if aircraft_data and aircraft_data.get('result'):
            self.aircraft_name_map = {
                aircraft['id']: aircraft['name']
                for aircraft in aircraft_data['result']
            }
            
            for aircraft in aircraft_data['result']:
                if 'liveries' in aircraft:
                    for livery in aircraft['liveries']:
                        self.livery_name_map[livery['id']] = livery['name']
            print(f"Successfully loaded {len(self.aircraft_name_map)} aircraft names.")
        else:
            print("WARNING: Could not load aircraft names from the API. Aircraft will show as 'Unknown'.")
            
        print("Loading extensions...")
        extensions = (
            "cogs.pingpong",
            "cogs.roster",
            "cogs.training",
            "cogs.cargo_training",
            "cogs.utils",
            "cogs.embed_creator",
            "cogs.flight_generator_pdf",
            "cogs.mission",
            "cogs.gate_assignment",
            "cogs.database_audit",
            "cogs.callsign_finder",
            "cogs.flight_poll_system",
            "cogs.ticket_system",
            "cogs.pirep_validator",
            "cogs.message_cleaner",
            "cogs.rank_management",
            "cogs.mission_dispatcher",
            "cogs.checklist_cog",
            "cogs.flight_board_cog",
            "cogs.monthly_stats",
            "cogs.fleet_pirep_cog",
            "cogs.ascaris_cog",
            "cogs.leaderboard",
            "cogs.notification_cog",
            "cogs.request_flight_cog",
            "cogs.available_aircraft_cog",
            "cogs.remove_assignment_cog",
            "cogs.stopped_flight_monitor",
            "cogs.route_ready_notifications",
        )
        loaded_extensions = []
        for extension in extensions:
            try:
                await self.load_extension(extension)
                loaded_extensions.append(extension)
            except Exception:
                logging.exception("Failed to load extension %s", extension)

        logging.info(
            "Loaded %s of %s configured extensions.",
            len(loaded_extensions),
            len(extensions),
        )

    async def on_ready(self):
        """
        Called when the bot has successfully connected to Discord.
        """
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        logging.info(f'Logged in as {self.user} (ID: {self.user.id})')

        if self.db_manager and self.db_manager._pool is None:
            await self.db_manager.connect()
            logging.info("Database connection pool established.")

        print("Database connection pool established.")

        try:
            print("Syncing slash commands...")
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} slash command(s) globally.")
            logging.info(f"Synced {len(synced)} slash command(s) globally.")
            print("Registered commands:", [cmd.name for cmd in self.tree.get_commands()])

            guild_id = int(os.getenv("GUILD_ID", "0") or 0)
            recovery_command = self.tree.get_command("setup_recovery_role")
            if guild_id and recovery_command:
                guild = discord.Object(id=guild_id)
                self.tree.add_command(
                    recovery_command,
                    guild=guild,
                    override=True,
                )
                guild_synced = await self.tree.sync(guild=guild)
                logging.info(
                    "Synced %s slash command(s) to guild %s, including "
                    "setup_recovery_role.",
                    len(guild_synced),
                    guild_id,
                )
            elif not guild_id:
                logging.warning(
                    "GUILD_ID is not configured; setup_recovery_role will only "
                    "be available through global slash-command sync."
                )
        except Exception as e:
            print(f"Failed to sync slash commands globally: {e}")
            logging.error(f"Failed to sync slash commands globally: {e}")

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

        if self.cc_api_manager:
            await self.cc_api_manager.close()

        if self.if_live_manager:
            await self.if_live_manager.close()

async def start_bot():
    """
    Function to create and run the bot.
    """

    bot = MyBot()

    if TOKEN is None:
        print("ERROR: DISCORD_BOT_TOKEN environment variable not set.")
        print("Please create a .env file in the same directory as this script and add DISCORD_BOT_TOKEN=YOUR_TOKEN_HERE")
        logging.critical("DISCORD_BOT_TOKEN environment variable not set.")

    async with bot:
        await bot.start(TOKEN)

if __name__ == '__main__':
    # --- Configure Root Logger (Captures all logging.info calls) ---
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Formatter
    dt_fmt = '%Y-%m-%d %H:%M:%S'
    formatter = logging.Formatter('[{asctime}] [{levelname:<8}] {name}: {message}', dt_fmt, style='{')
    
    # 1. File Handler
    file_handler = logging.handlers.RotatingFileHandler(
        filename='discord.log',
        encoding='utf-8',
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=5,
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Silence noisy libraries
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)

    try:
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        print("Bot shut down by user.")
        logging.info("Bot shut down by user.")
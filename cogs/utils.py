import discord
from discord.ext import commands
from discord import app_commands
import airportsdata

# 1. LOAD THE DATABASE ONCE (Global Variable)
# This loads 28,000+ airports into memory instantly when the bot starts.
print("Loading Airport Database...") 
AIRPORTS_DB = airportsdata.load('ICAO')
print("Database Loaded!")

def get_country_flag(icao: str) -> str:
    """
    1. Looks up the ICAO in the official database.
    2. Gets the ISO Country Code (e.g., 'IN', 'US').
    3. Converts that code into a Flag Emoji.
    """
    if not icao:
        return "🏳️"

    # Normalize input
    icao_upper = icao.upper()
    
    # --- STEP 1: DATABASE LOOKUP ---
    # We check if the exact airport exists in the library
    airport_data = AIRPORTS_DB.get(icao_upper)
    
    if airport_data:
        # If found, grab the country code (e.g., "IN" for India)
        country_code = airport_data['country']
    else:
        # --- OPTIONAL FALLBACK ---
        # If the airport isn't in the real-world database (e.g., a fake VA airport),
        # return Unknown or keep a TINY map for custom airports.
        return "🏳️" 

    # --- STEP 2: CONVERT TO EMOJI ---
    # Your original math logic here was actually perfect! 
    # It converts "IN" -> 🇮🇳 without needing extra libraries.
    return "".join(chr(0x1F1E6 + ord(c) - ord('A')) for c in country_code)


# --- YOUR DISCORD COG ---
class FlagTestUtils(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="route_flag", description="Get flags for a flight route")
    @app_commands.describe(dep="Departure ICAO", arr="Arrival ICAO")
    async def route_flag(self, interaction: discord.Interaction, dep: str, arr: str):
        
        # Get flags for both
        dep_flag = get_country_flag(dep)
        arr_flag = get_country_flag(arr)
        
        # Make them uppercase for display
        dep = dep.upper()
        arr = arr.upper()

        # Send the result
        # Output: Route: VABB 🇮🇳 - KJFK 🇺🇸
        await interaction.response.send_message(f"Route: {dep} {dep_flag} - {arr} {arr_flag}")

async def setup(bot: commands.Bot):
    await bot.add_cog(FlagTestUtils(bot))
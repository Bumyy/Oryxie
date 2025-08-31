ICAO_COUNTRY_PREFIX_MAP = {
    # Two-letter prefixes (most specific) - checked first
    "PA": "US", "PH": "US", "TI": "VI", "TJ": "PR", "BG": "GL", "MY": "BS", "MU": "CU", "MK": "JM", "MD": "DO",
    "SA": "AR", "SB": "BR", "SC": "CL", "SE": "EC", "SK": "CO", "SP": "PE", "SV": "VE", "MP": "PA", "MR": "CR",
    "EB": "BE", "ED": "DE", "EE": "EE", "EF": "FI", "EG": "GB", "EH": "NL", "EI": "IE", "EK": "DK", "EN": "NO",
    "EP": "PL", "ES": "SE", "ET": "DE", "LE": "ES", "LF": "FR", "LG": "GR", "LH": "HU", "LI": "IT", "LJ": "SI",
    "LK": "CZ", "LL": "IL", "LM": "MT", "LO": "AT", "LP": "PT", "LQ": "BA", "LR": "RO", "LS": "CH", "LT": "TR",
    "LU": "LU", "LY": "RS", "LZ": "BG", "OA": "AF", "OB": "BH", "OE": "SA", "OI": "IR", "OJ": "JO", "OK": "KW",
    "OM": "AE", "OO": "OM", "OP": "PK", "OR": "IQ", "OS": "SY", "OT": "QA", "DA": "DZ", "DN": "NG", "DT": "TN", "GM": "MA",
    "HE": "EG", "HK": "KE", "HR": "RW", "RC": "TW", "RJ": "JP", "RP": "PH", "VC": "LK", "VG": "BD", "VH": "HK",
    "VL": "LA", "VM": "MO", "VN": "NP", "VT": "TH", "VV": "VN", "WM": "MY", "WS": "SG", "WI": "ID", "WA": "ID",
    # Ukraine (must come before Russian prefixes)
    "UK": "UA",
    # Russian Federation prefixes (fixed duplicates)
    "UE": "RU", "UH": "RU", "UI": "RU", "UL": "RU", "UN": "RU", "UO": "RU", "UR": "RU",
    "US": "RU", "UU": "RU", "UW": "RU",
    # Additional correct prefixes
    "BI": "BJ", "FH": "SH", "FI": "MU", "FJ": "RE", "FK": "CM", "FL": "ZM", "FM": "KM", "FO": "GA", "FP": "ST",
    "FQ": "MZ", "FS": "SC", "FT": "TD", "FV": "ZW", "FW": "MW", "FX": "LS", "FY": "NA", "FZ": "CG",
    "GO": "SN", "GU": "GN", "GV": "CV", "HA": "ET", "HB": "BW", "HC": "SO", "HD": "DJ", "HH": "ER", "HL": "LY",
    "HM": "MG", "HN": "CD", "HS": "SD", "HT": "TZ", "HU": "UG", "LA": "AL", "LB": "BG", "LC": "CY", "LD": "HR",
    "NF": "FJ", "NG": "VU", "NI": "ID", "NL": "WF", "NS": "WS", "NT": "PF", "NV": "VU", "NW": "NC", "NZ": "NZ",
    "PG": "GU", "PJ": "MH", "PL": "KI", "PM": "MH", "PT": "FM", "PW": "PW",
    "RK": "KR", "RO": "KR", "SL": "SL", "SM": "SR", "SO": "GF", "SU": "UY", "SW": "SZ", "SY": "GY",
    "TA": "AG", "TB": "BB", "TC": "TC", "TD": "DM", "TF": "GP", "TG": "GD", "TK": "KN", "TL": "LC", "TN": "AN",
    "TQ": "AI", "TR": "MQ", "TT": "TT", "TU": "VG", "TV": "VC", "TX": "BM", "UA": "KZ", "UB": "AZ", "UC": "KG",
    "UD": "AM", "UG": "GE", "UJ": "TJ", "UZ": "UZ",
    # Single-letter prefixes (least specific) - NO "U" here to avoid Russian flag for all U codes
    "C": "CA", "K": "US", "M": "MX", "F": "ZA", "G": "GH", "H": "ET", "N": "NZ", "R": "KR", "V": "IN",
    "W": "ID", "Y": "AU", "Z": "CN"
}

def get_country_flag(icao: str) -> str:
    """
    Returns a country flag emoji based on the airport's ICAO code.
    Checks two-letter prefixes first, then single-letter prefixes.
    """
    if not isinstance(icao, str) or not icao:
        return "🏳️"  # White flag for invalid input

    icao_upper = icao.upper()
    country_code = None
    
    # Check for two-letter prefixes first (most specific)
    if len(icao_upper) >= 2:
        country_code = ICAO_COUNTRY_PREFIX_MAP.get(icao_upper[:2])

    # Check for single-letter prefixes only if no two-letter match found
    if not country_code and len(icao_upper) >= 1:
        country_code = ICAO_COUNTRY_PREFIX_MAP.get(icao_upper[0])

    if country_code:
        # Convert country code (e.g., "US") to flag emoji (e.g., 🇺🇸)
        return "".join(chr(0x1F1E6 + ord(c) - ord('A')) for c in country_code)

    return "🏳️" # Default flag if no match is found

# Test command for ICAO flag generation
import discord
from discord.ext import commands
from discord import app_commands

class FlagTestUtils(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @app_commands.command(name="flag_test", description="Test ICAO flag generation")
    @app_commands.describe(icao="ICAO code to test (e.g., OTHH, UUDD, UKBB, EGLL)")
    async def flag_test(self, interaction: discord.Interaction, icao: str):
        flag = get_country_flag(icao)
        prefix_info = ""
        icao_upper = icao.upper()
        
        # Show which prefix was matched
        if len(icao_upper) >= 2 and icao_upper[:2] in ICAO_COUNTRY_PREFIX_MAP:
            prefix_info = f" (matched: {icao_upper[:2]})"
        elif len(icao_upper) >= 1 and icao_upper[0] in ICAO_COUNTRY_PREFIX_MAP:
            prefix_info = f" (matched: {icao_upper[0]})"
        else:
            prefix_info = " (no match)"
            
        await interaction.response.send_message(f"**{icao_upper}** → {flag}{prefix_info}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(FlagTestUtils(bot))
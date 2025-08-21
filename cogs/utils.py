ICAO_COUNTRY_PREFIX_MAP = {
    "C": "CA", "K": "US", "M": "MX", "PA": "US", "PH": "US", "TI": "VI", "TJ": "PR", "BG": "GL", "MY": "BS",
    "MU": "CU", "MK": "JM", "MD": "DO", "SA": "AR", "SB": "BR", "SC": "CL", "SE": "EC", "SK": "CO", "SP": "PE",
    "SV": "VE", "MP": "PA", "MR": "CR", "EB": "BE", "ED": "DE", "EE": "EE", "EF": "FI", "EG": "GB", "EH": "NL",
    "EI": "IE", "EK": "DK", "EN": "NO", "EP": "PL", "ES": "SE", "ET": "DE", "LE": "ES", "LF": "FR", "LG": "GR",
    "LH": "HU", "LI": "IT", "LJ": "SI", "LK": "CZ", "LL": "IL", "LM": "MT", "LO": "AT", "LP": "PT", "LQ": "BA",
    "LR": "RO", "LS": "CH", "LT": "TR", "LU": "LU", "LY": "RS", "LZ": "BG", "U": "RU", "OA": "AF", "OB": "BH",
    "OE": "SA", "OI": "IR", "OJ": "JO", "OK": "KW", "OM": "AE", "OP": "PK", "OR": "IQ", "OS": "SY", "OT": "QA",
    "DA": "DZ", "DN": "NG", "DT": "TN", "F": "ZA", "G": "GH", "GM": "MA", "H": "ET", "HE": "EG", "HK": "KE",
    "HR": "RW", "N": "NZ", "R": "KR", "RC": "TW", "RJ": "JP", "RP": "PH", "V": "IN", "VC": "LK", "VG": "BD",
    "VH": "HK", "VL": "LA", "VM": "MO", "VN": "NP", "VT": "TH", "VV": "VN", "W": "ID", "WM": "MY", "WS": "SG",
    "Y": "AU", "Z": "CN"
}

def get_country_flag(icao: str) -> str:
    """
    Returns a country flag emoji based on the airport's ICAO code.
    This version handles both single and double letter prefixes.
    """
    if not isinstance(icao, str) or not icao:
        return "🏳️"  # White flag for invalid input

    icao_upper = icao.upper()
    country_code = None

    # Check for two-letter prefixes first (more specific)
    if len(icao_upper) >= 2:
        country_code = ICAO_COUNTRY_PREFIX_MAP.get(icao_upper[:2])

    # If not found, check for single-letter prefixes (less specific)
    if not country_code and icao_upper[0] in ICAO_COUNTRY_PREFIX_MAP:
        country_code = ICAO_COUNTRY_PREFIX_MAP.get(icao_upper[0])

    if country_code:
        # Convert country code (e.g., "US") to flag emoji (e.g., 🇺🇸)
        return "".join(chr(0x1F1E6 + ord(c) - ord('A')) for c in country_code)

    return "🏳️" # Default flag if no match is found
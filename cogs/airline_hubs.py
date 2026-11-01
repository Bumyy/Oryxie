"""Shared airline and hub configuration for fleet workflows."""

DEFAULT_HUB_ICAO = "OTHH"
CARGO_REGISTRATIONS = frozenset({"A7BFA"})
RETIRED_REGISTRATIONS = frozenset({"A7BFC"})

AIRCRAFT_AIRLINES = {
    "A7BFA": "Qatar Airways Cargo",
    "A7BFC": "Qatar Airways",
    "B-LRO": "Cathay Pacific",
    "EC-ORE": "Iberia",
    "IF-KSKI": "British Airways",
    "IF-MPSK": "LATAM",
    "IF-FXQK": "JAL",
    "VH-ZNF": "Qantas",
    "N762AN": "American Airlines",
    "N302NY": "American Airlines",
}

AIRLINE_HUBS = {
    "Qatar Airways": ("OTHH",),
    "Qatar Airways Cargo": ("OTHH",),
    "Cathay Pacific": ("VHHH",),
    "Iberia": ("LEMD",),
    "British Airways": ("EGLL",),
    "LATAM": ("SBGR", "SBBR"),
    "JAL": ("RJTT",),
    "Qantas": ("YSSY",),
    "American Airlines": ("KJFK", "KPHL", "KPHX", "KCLT", "KMIA", "KDFW", "KLAX"),
}


def airline_name_for_registration(registration):
    normalized = str(registration or "").strip().upper()
    return AIRCRAFT_AIRLINES.get(normalized, "Qatar Airways")


def hubs_for_registration(registration):
    airline = airline_name_for_registration(registration)
    return AIRLINE_HUBS.get(airline, (DEFAULT_HUB_ICAO,))


def is_cargo_registration(registration):
    normalized = str(registration or "").strip().upper()
    return normalized in CARGO_REGISTRATIONS


def takeoff_window_hours(registration, airport_icao=None):
    """Determine takeoff window in hours: 6h at hub, 12h elsewhere."""
    normalized_registration = str(registration or "").strip().upper()
    airport = str(airport_icao or "").strip().upper()
    if airport and airport in hubs_for_registration(normalized_registration):
        return 6
    if airport and airport not in hubs_for_registration(normalized_registration):
        return 12
    return 6 if not normalized_registration else 12

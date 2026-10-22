"""Shared airline and hub configuration for fleet workflows."""

DEFAULT_HUB_ICAO = "OTHH"

AIRCRAFT_AIRLINES = {
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
    "Cathay Pacific": ("VHHH",),
    "Iberia": ("LEMD",),
    "British Airways": ("EGLL",),
    "LATAM": ("SCEL", "SBGR", "SPJC"),
    "JAL": ("RJTT",),
    "Qantas": ("YSSY",),
    "American Airlines": ("KDFW", "KJFK", "KPHX"),
}


def airline_name_for_registration(registration):
    normalized = str(registration or "").strip().upper()
    return AIRCRAFT_AIRLINES.get(normalized, "Qatar Airways")


def hubs_for_registration(registration):
    airline = airline_name_for_registration(registration)
    return AIRLINE_HUBS.get(airline, (DEFAULT_HUB_ICAO,))

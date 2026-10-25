"""Command to request an Infinite Flight flight assignment from the VA fleet and schedule."""

import asyncio
import logging
import os
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

from cogs.airline_hubs import (
    DEFAULT_HUB_ICAO,
    RETIRED_REGISTRATIONS,
    airline_name_for_registration,
    hubs_for_registration,
    is_cargo_registration,
)

log = logging.getLogger(__name__)

ORG_ID = os.getenv("IF_ORGANIZATION_ID", "").strip()
TOKEN_OWNER_ID = 1

FLEET_CACHE_SECONDS = 300
SCHEDULE_CACHE_SECONDS = 60
MAX_BOOKED_AIRCRAFT = 2
SCHEDULES_PER_SCAN = 3
VIEW_TIMEOUT_SECONDS = 180
AIRCRAFT_AIRLINES = {
    "JA-742J": "JAL",
    "PT-LBN": "LATAM",
    "A7-BAJ": "Qatar Airways",
    "A7BAB": "Qatar Airways",
    "G-ZBKC": "British Airways",
    "A7-APE": "Qatar Airways",
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
    "Qatar Airways": "OTHH",
    "Cathay Pacific": "VHHH",
    "Iberia": "LEMD",
    "British Airways": "EGLL",
    "LATAM": ("SBGR", "SBBR"),
    "JAL": "RJTT",
    "Qantas": "YSSY",
    "American Airlines": "KDFW",
}

FLIGHT_RULES = (
    "• Take off within 6 hours at a hub or 12 hours elsewhere\n"
    "• Stopped in air for 1 hour: assignment removed and public recovery claim posted\n"
    "• If unclaimed, recovery DMs start at 2 hours\n"
    "• Staff alerted if unresolved for 4 hours"
)


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _registration(aircraft):
    return str(_get(aircraft, "registration", "") or "").strip().upper()


def _crew_user_id(member):
    for key in ("userId", "user_id", "ifuserid", "id"):
        value = _get(member, key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _aircraft_model(aircraft):
    for key in (
        "model",
        "aircraft_name",
        "aircraftName",
        "name",
        "title",
        "type_name",
        "typeName",
    ):
        value = _get(aircraft, key)
        if value and str(value).strip():
            return str(value).strip()

    return ""


# Explicit overrides for the registrations that were recently changed.
# Keep airline and hub resolution local so the shared lookup cannot silently
# fall back to Qatar Airways / OTHH for these aircraft.
AIRCRAFT_HUB_OVERRIDES = {
    "JA-742J": ("RJTT",),
    "PT-LBN": ("SBGR", "SBBR"),
    "A7-BAJ": ("OTHH",),
    "A7BAB": ("OTHH",),
    "G-ZBKC": ("EGLL",),
    "A7-APE": ("OTHH",),
}


def _airline_name(aircraft):
    livery_name = _get(aircraft, "livery_name")
    if livery_name and str(livery_name).strip():
        return str(livery_name).strip()
    registration = _registration(aircraft)
    if registration in AIRCRAFT_AIRLINES:
        return AIRCRAFT_AIRLINES[registration]
    return airline_name_for_registration(registration)


def _aircraft_hubs(aircraft):
    registration = _registration(aircraft)
    if registration in AIRCRAFT_HUB_OVERRIDES:
        return AIRCRAFT_HUB_OVERRIDES[registration]
    return hubs_for_registration(registration)


def _aircraft_hub(aircraft):
    return ", ".join(_aircraft_hubs(aircraft))


def _aircraft_category(aircraft):
    registration = _registration(aircraft)
    if is_cargo_registration(registration):
        return "Qatar Cargo"
    if registration in RETIRED_REGISTRATIONS:
        return "Retired"

    model = re.sub(r"[^a-z0-9]", "", _aircraft_model(aircraft).lower())

    if model.startswith("airbus") or (
        model.startswith("a") and len(model) > 1 and model[1].isdigit()
    ):
        return "Airbus"

    if model.startswith("boeing") or model.startswith(
        ("777", "787", "737", "747", "757", "767", "717", "727")
    ):
        return "Boeing"

    return "Other"


def _retired_aircraft():
    return [
        {
            "id": f"retired:{registration}",
            "registration": registration,
            "model": "Model unknown",
        }
        for registration in sorted(RETIRED_REGISTRATIONS)
    ]


def _build_cargo_route_chain(schedules, start_index, max_routes=2):
    if start_index < 0 or start_index >= len(schedules):
        return []

    routes = []
    previous = None
    for route in schedules[start_index:]:
        if len(routes) >= max_routes:
            break
        if (
            _is_unavailable_status(route)
            or _route_status(route) in {6, 7, 10}
            or (_get(route, "crew", []) or [])
        ):
            break

        origin = str(_get(route, "origin_icao", "") or "").strip().upper()
        destination = str(
            _get(route, "destination_icao", "") or ""
        ).strip().upper()
        if not origin or not destination:
            break
        if previous is not None and not _connected_routes(previous, route):
            break

        routes.append(route)
        previous = route

    return routes


def _model_label(aircraft):
    return f"{_airline_name(aircraft)} {_aircraft_model(aircraft) or 'Model unknown'}"


def _state_label(position):
    labels = {
        0: "Unknown",
        1: "On ground",
        2: "In flight",
        3: "Cancelled",
        4: "Stopped",
        5: "Maintenance",
    }

    raw = _get(position, "state")

    try:
        return labels.get(int(raw), f"Unknown ({raw})")
    except (TypeError, ValueError):
        return str(raw if raw is not None else "Unknown")


def _route_text(route):
    callsign = _get(route, "callsign") or "Callsign unavailable from API"
    origin = _get(route, "origin_icao") or "???"
    destination = _get(route, "destination_icao") or "???"
    return f"**{callsign}** — {origin} → {destination}"


def _sequence(route):
    try:
        return int(_get(route, "sequence", 0))
    except (TypeError, ValueError):
        return 0


def _route_status(route):
    try:
        return int(_get(route, "status"))
    except (TypeError, ValueError):
        return None


def _is_unavailable_status(route):
    return _route_status(route) in {9, 11}


def _is_return_leg(previous, route):
    previous_origin = str(
        _get(previous, "origin_icao", "") or ""
    ).strip().upper()
    previous_destination = str(
        _get(previous, "destination_icao", "") or ""
    ).strip().upper()
    origin = str(_get(route, "origin_icao", "") or "").strip().upper()
    destination = str(
        _get(route, "destination_icao", "") or ""
    ).strip().upper()
    return bool(
        previous_origin
        and previous_destination
        and origin
        and destination
        and previous_origin == destination
        and previous_destination == origin
    )


def _counted_booked_aircraft_ids(schedules, if_user_id):
    """Count aircraft with the pilot's unflown, non-return assigned routes."""
    target_id = str(if_user_id).strip()
    schedules_by_aircraft = {}
    for route in schedules or []:
        aircraft_id = _get(route, "aircraft_id")
        if aircraft_id not in (None, ""):
            schedules_by_aircraft.setdefault(str(aircraft_id), []).append(route)

    booked = set()
    for aircraft_id, aircraft_schedules in schedules_by_aircraft.items():
        ordered = sorted(aircraft_schedules, key=_sequence)
        for index, route in enumerate(ordered):
            if _is_unavailable_status(route) or _route_status(route) in {6, 7}:
                continue
            if not any(
                _crew_user_id(member) == target_id
                for member in (_get(route, "crew", []) or [])
            ):
                continue
            if index and _is_return_leg(ordered[index - 1], route):
                continue
            booked.add(aircraft_id)
            break
    return booked


def _active_schedules(schedules):
    """Deduplicate schedules and remove cancelled or completed routes."""
    by_id = {}
    no_id = []

    for route in sorted(schedules or [], key=_sequence):
        route_id = _get(route, "id")

        if route_id in (None, ""):
            no_id.append(route)
            continue

        key = str(route_id)
        old = by_id.get(key)

        if old is None:
            by_id[key] = route
        elif _is_unavailable_status(route) and not _is_unavailable_status(old):
            by_id[key] = route
        elif _is_unavailable_status(route) == _is_unavailable_status(old):
            if not (_get(old, "crew", []) or []) and (_get(route, "crew", []) or []):
                by_id[key] = route

    return [
        route
        for route in sorted(list(by_id.values()) + no_id, key=_sequence)
        if not _is_unavailable_status(route)
    ]


def _current_assignments_for_pilot(schedules, if_user_id, registration_by_aircraft):
    """Return this pilot's active assignments on aircraft still in the fleet."""
    assignments = {}
    target_user_id = str(if_user_id).strip()
    current_aircraft_ids = set(registration_by_aircraft)

    for route in _active_schedules(schedules):
        if not any(
            _crew_user_id(member) == target_user_id
            for member in (_get(route, "crew", []) or [])
        ):
            continue

        aircraft_id = str(_get(route, "aircraft_id") or "")
        if aircraft_id not in current_aircraft_ids:
            continue

        assignments.setdefault(aircraft_id, []).append(route)

    return assignments


def _schedules_for_aircraft(schedules, aircraft_id):
    """Keep only schedule records belonging to the requested aircraft."""
    expected_id = str(aircraft_id)
    matched = []
    mismatched_count = 0

    for route in schedules or []:
        route_aircraft_id = _get(route, "aircraft_id")
        if str(route_aircraft_id or "") == expected_id:
            matched.append(route)
        else:
            mismatched_count += 1

    if mismatched_count:
        log.warning(
            "Ignoring %d schedule record(s) returned for a different aircraft "
            "while loading aircraft %s",
            mismatched_count,
            expected_id,
        )

    return matched


def _has_active_assignment_for_pilots(schedules, pilot_ids):
    assigned_pilot_ids = {
        str(pilot_id).strip()
        for pilot_id in pilot_ids
        if pilot_id not in (None, "")
    }
    if not assigned_pilot_ids:
        return False

    return any(
        _crew_user_id(member) in assigned_pilot_ids
        for route in schedules or []
        if not _is_unavailable_status(route)
        for member in (_get(route, "crew", []) or [])
    )


def _assigned_schedule_prefix(schedules):
    """Return the index and ID of the last consecutively assigned route."""
    index = 0
    after_id = None
    for route in schedules:
        if not (_get(route, "crew", []) or []):
            break
        after_id = str(_get(route, "id"))
        index += 1
    return index, after_id


def _next_claimable_index(schedules):
    for index, route in enumerate(schedules):
        if not (_get(route, "crew", []) or []):
            return index
    return None


def _connected_routes(first, second):
    destination = str(
        _get(first, "destination_icao", "") or ""
    ).strip().upper()
    origin = str(
        _get(second, "origin_icao", "") or ""
    ).strip().upper()

    return bool(destination and origin and destination == origin)


def _build_connected_route_chain(schedules, start_index, hub_icao):
    """
    Build a consecutive chain of unassigned routes that ends at the airline hub.

    The chain is rejected if a route is assigned, completed, cancelled, missing
    airport codes, disconnected from the previous route, or fails to return
    to the hub.
    """
    if start_index < 0 or start_index >= len(schedules):
        return []

    if isinstance(hub_icao, str):
        hubs = {hub_icao.strip().upper()}
    else:
        hubs = {
            str(hub).strip().upper()
            for hub in (hub_icao or (DEFAULT_HUB_ICAO,))
            if hub
        }
    routes = []
    previous = None

    for route in schedules[start_index:]:
        if _is_unavailable_status(route) or (_get(route, "crew", []) or []):
            return []

        origin = str(
            _get(route, "origin_icao", "") or ""
        ).strip().upper()
        destination = str(
            _get(route, "destination_icao", "") or ""
        ).strip().upper()

        if not origin or not destination:
            return []

        if previous is not None and not _connected_routes(previous, route):
            return []

        routes.append(route)

        if destination in hubs:
            return routes

        previous = route

    return []


def _route_ids(routes):
    return [str(_get(route, "id")) for route in routes]


def _route_option_label_chain(routes):
    callsigns = list(dict.fromkeys(
        str(_get(route, "callsign", "") or "").strip()
        for route in routes
        if _get(route, "callsign")
    ))
    if not callsigns:
        callsign_label = "Scheduled route"
    elif len(callsigns) == 1:
        callsign_label = callsigns[0]
    else:
        parts = [re.fullmatch(r"(.*?)(\d+)", callsign) for callsign in callsigns]
        if all(parts) and len({part.group(1) for part in parts if part}) == 1:
            callsign_label = (
                f"{callsigns[0]} (and "
                f"{', '.join(part.group(2) for part in parts[1:])})"
            )
        else:
            callsign_label = f"{callsigns[0]} (and {', '.join(callsigns[1:])})"

    airports = [
        str(_get(routes[0], "origin_icao", "") or "???").strip().upper()
    ]
    airports.extend(
        str(_get(route, "destination_icao", "") or "???").strip().upper()
        for route in routes
    )
    return f"{callsign_label} - {'-'.join(airports)}"[:100]


def _route_start_key(route):
    return (
        str(_get(route, "callsign", "") or "").strip().casefold(),
        str(_get(route, "origin_icao", "") or "").strip().upper(),
        str(_get(route, "destination_icao", "") or "").strip().upper(),
    )


def _route_chain_description(routes):
    return " → ".join(
        str(_get(route, "destination_icao", "") or "???").upper()
        for route in routes
    )


class AircraftCategorySelect(discord.ui.Select):
    def __init__(self, cog, pilot, aircraft):
        self.cog = cog
        self.pilot = pilot
        self.aircraft = aircraft

        available_categories = [
            name
            for name in ("Airbus", "Boeing", "Other", "Qatar Cargo")
            if any(_aircraft_category(a) == name for a in aircraft)
        ]
        if RETIRED_REGISTRATIONS:
            available_categories.append("Retired")

        options = [
            discord.SelectOption(
                label=name,
                description=(
                    "1 retired aircraft"
                    if name == "Retired"
                    else f"{sum(_aircraft_category(a) == name for a in aircraft)} aircraft"
                ),
            )
            for name in available_categories
        ]

        super().__init__(
            placeholder="Choose aircraft type…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction):
        if str(interaction.user.id) != str(self.pilot.get("discordid")):
            await interaction.response.send_message(
                "Only the requesting pilot can use this menu.",
                ephemeral=True,
            )
            return

        category = self.values[0]
        selected = (
            _retired_aircraft()
            if category == "Retired"
            else [
                aircraft
                for aircraft in self.aircraft
                if _aircraft_category(aircraft) == category
            ]
        )

        await interaction.response.edit_message(
            content=(
                f"**{category} aircraft** — choose a registration. "
                "Airline and model are shown."
            ),
            view=AircraftListView(self.cog, self.pilot, selected, category),
        )


class AircraftListSelect(discord.ui.Select):
    def __init__(self, cog, pilot, aircraft, category, page=0):
        self.cog = cog
        self.pilot = pilot
        self.aircraft = aircraft
        self.category = category
        self.page = page

        page_items = aircraft[page * 25:(page + 1) * 25]
        options = [
            discord.SelectOption(
                label=(_registration(item) or "Unknown")[:100],
                description=_model_label(item)[:100],
                value=str(_get(item, "id")),
            )
            for item in page_items
            if _get(item, "id") not in (None, "")
        ]

        super().__init__(
            placeholder=(
                f"Choose {category} registration "
                f"(page {page + 1}/{max(1, (len(aircraft) + 24) // 25)})…"
            ),
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction):
        if str(interaction.user.id) != str(self.pilot.get("discordid")):
            await interaction.response.send_message(
                "Only the requesting pilot can use this menu.",
                ephemeral=True,
            )
            return

        aircraft = next(
            (
                item
                for item in self.aircraft
                if str(_get(item, "id")) == self.values[0]
            ),
            None,
        )

        if aircraft is None:
            await interaction.response.send_message(
                "Aircraft not found. Run `/request-flight` again.",
                ephemeral=True,
            )
            return

        if _registration(aircraft) in RETIRED_REGISTRATIONS:
            await interaction.response.send_message(
                "✈️ **A7BFC · Boeing 777F**\n"
                "She was a beauty—every cargo run showed what a 777F could do. "
                "Though she has retired from the fleet, her legacy still flies "
                "on in every aircraft following in her contrail.",
                ephemeral=False,
            )
            return

        await self.cog.show_aircraft_route(interaction, self.pilot, aircraft)


class AircraftListView(discord.ui.View):
    def __init__(self, cog, pilot, aircraft, category, page=0):
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)

        self.add_item(
            AircraftListSelect(cog, pilot, aircraft, category, page)
        )

        if page > 0:
            self.add_item(
                AircraftPageButton(
                    cog, pilot, aircraft, category, page - 1, "Previous"
                )
            )

        if (page + 1) * 25 < len(aircraft):
            self.add_item(
                AircraftPageButton(
                    cog, pilot, aircraft, category, page + 1, "Next"
                )
            )

        self.add_item(
            AircraftPageButton(
                cog,
                pilot,
                aircraft,
                category,
                0,
                "Back to categories",
                back=True,
            )
        )


class AircraftPageButton(discord.ui.Button):
    def __init__(
        self,
        cog,
        pilot,
        aircraft,
        category,
        page,
        label,
        back=False,
    ):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
        )

        self.cog = cog
        self.pilot = pilot
        self.aircraft = aircraft
        self.category = category
        self.page = page
        self.back = back

    async def callback(self, interaction):
        if str(interaction.user.id) != str(self.pilot.get("discordid")):
            await interaction.response.send_message(
                "Only the requesting pilot can use this menu.",
                ephemeral=True,
            )
            return

        if self.back:
            aircraft = self.cog._aircraft_cache or self.aircraft

            await interaction.response.edit_message(
                content="Choose an aircraft category:",
                view=AircraftSelectView(self.cog, self.pilot, aircraft),
            )
        else:
            await interaction.response.edit_message(
                content=f"**{self.category} aircraft** — choose a registration:",
                view=AircraftListView(
                    self.cog,
                    self.pilot,
                    self.aircraft,
                    self.category,
                    self.page,
                ),
            )


class AircraftSelectView(discord.ui.View):
    def __init__(self, cog, pilot, aircraft):
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.add_item(AircraftCategorySelect(cog, pilot, aircraft))


class RouteStartSelect(discord.ui.Select):
    """Let the pilot choose which valid scheduled route chain to request."""

    def __init__(self, cog, pilot, aircraft, schedules, choices, page=0):
        self.cog = cog
        self.pilot = pilot
        self.aircraft = aircraft
        self.schedules = schedules
        self.choices = choices
        self.page = page

        page_items = choices[page * 25:(page + 1) * 25]
        options = []

        for start_index, routes in page_items:
            route_kind = (
                f"{len(routes)} cargo leg(s) · "
                if is_cargo_registration(_registration(aircraft))
                else (
                    f"{len(routes)} route(s) · "
                    f"Returns to one of {_aircraft_hub(aircraft)} · "
                )
            )
            options.append(
                discord.SelectOption(
                    label=_route_option_label_chain(routes),
                    description=(
                        route_kind + _route_chain_description(routes)
                    )[:100],
                    value=str(start_index),
                )
            )

        super().__init__(
            placeholder=(
                f"Choose your starting route "
                f"(page {page + 1}/{max(1, (len(choices) + 24) // 25)})…"
            ),
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction):
        if str(interaction.user.id) != str(self.pilot.get("discordid")):
            await interaction.response.send_message(
                "Only the requesting pilot can use this menu.",
                ephemeral=True,
            )
            return

        try:
            start_index = int(self.values[0])
        except (TypeError, ValueError):
            await interaction.response.send_message(
                "Invalid route selection. Run `/request-flight` again.",
                ephemeral=True,
            )
            return

        selected_routes = next(
            (
                routes
                for index, routes in self.choices
                if index == start_index
            ),
            None,
        )

        if not selected_routes:
            await interaction.response.send_message(
                "That route option is no longer available. Run `/request-flight` again.",
                ephemeral=True,
            )
            return

        await self.cog.show_route_confirmation(
            interaction,
            self.pilot,
            self.aircraft,
            self.schedules,
            start_index,
            selected_routes,
        )


class RouteStartView(discord.ui.View):
    def __init__(
        self,
        cog,
        pilot,
        aircraft,
        schedules,
        choices,
        page=0,
    ):
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)

        self.cog = cog
        self.pilot = pilot
        self.aircraft = aircraft
        self.schedules = schedules
        self.choices = choices
        self.page = page

        self._rebuild()

    def _rebuild(self):
        self.clear_items()

        self.add_item(
            RouteStartSelect(
                self.cog,
                self.pilot,
                self.aircraft,
                self.schedules,
                self.choices,
                self.page,
            )
        )

        if self.page > 0:
            previous = discord.ui.Button(
                label="Previous",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            previous.callback = self.previous_page
            self.add_item(previous)

        if (self.page + 1) * 25 < len(self.choices):
            next_button = discord.ui.Button(
                label="Next",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            next_button.callback = self.next_page
            self.add_item(next_button)

    async def previous_page(self, interaction):
        if str(interaction.user.id) != str(self.pilot.get("discordid")):
            await interaction.response.send_message(
                "Only the requesting pilot can use this menu.",
                ephemeral=True,
            )
            return

        self.page = max(0, self.page - 1)
        self._rebuild()
        await interaction.response.edit_message(view=self)

    async def next_page(self, interaction):
        if str(interaction.user.id) != str(self.pilot.get("discordid")):
            await interaction.response.send_message(
                "Only the requesting pilot can use this menu.",
                ephemeral=True,
            )
            return

        max_page = max(0, (len(self.choices) - 1) // 25)
        self.page = min(max_page, self.page + 1)
        self._rebuild()
        await interaction.response.edit_message(view=self)


class CopilotCallsignModal(discord.ui.Modal, title="Set Co-pilot"):
    callsign = discord.ui.TextInput(
        label="Co-pilot callsign",
        placeholder="Enter a VA callsign, e.g. QRV133 or 133",
        required=True,
        max_length=50,
    )

    def __init__(self, parent_view):
        super().__init__(timeout=300)
        self.parent_view = parent_view

    async def on_submit(self, interaction):
        view = self.parent_view

        if view.claimed:
            await interaction.response.send_message(
                "This request has already been processed.",
                ephemeral=True,
            )
            return

        value = str(self.callsign.value).strip().upper()

        if not value:
            await interaction.response.send_message(
                "Enter a valid pilot callsign.",
                ephemeral=True,
            )
            return

        callsign = f"QRV{value.zfill(3)}" if value.isdigit() else value
        model = getattr(view.cog.bot, "pilots_model", None)

        if model is None:
            await interaction.response.send_message(
                "Pilot database unavailable. Contact staff.",
                ephemeral=True,
            )
            return

        try:
            copilot = await model.get_pilot_by_callsign(callsign)
        except Exception:
            log.exception("Co-pilot lookup failed for %s", callsign)
            await interaction.response.send_message(
                "Could not look up that callsign. Try again later.",
                ephemeral=True,
            )
            return

        if not copilot:
            await interaction.response.send_message(
                f"No active pilot profile found for `{callsign}`.",
                ephemeral=True,
            )
            return

        copilot_id = str(copilot.get("ifuserid") or "").strip()

        if not copilot_id:
            await interaction.response.send_message(
                f"`{callsign}` has no Infinite Flight user ID in the database.",
                ephemeral=True,
            )
            return

        if copilot_id == view.pilot_if_user_id:
            await interaction.response.send_message(
                "You cannot select yourself as co-pilot.",
                ephemeral=True,
            )
            return

        view.copilot = copilot
        view.copilot_if_user_id = copilot_id
        view.copilot_callsign = str(copilot.get("callsign") or callsign)

        if view.message and view.confirmation_embed:
            embed = view.confirmation_embed.copy()

            for index, field in enumerate(embed.fields):
                if field.name == "Co-pilot":
                    embed.set_field_at(
                        index,
                        name="Co-pilot",
                        value=view.copilot_callsign,
                        inline=False,
                    )
                    break

            view.confirmation_embed = embed

            try:
                await view.message.edit(embed=embed, view=view)
            except Exception:
                log.exception("Could not update confirmation embed")

        confirmation_view = CopilotConfirmationView(view)
        await interaction.response.send_message(
            f"Co-pilot set to **{view.copilot_callsign}**. Confirm to continue.",
            ephemeral=True,
            view=confirmation_view,
        )
        confirmation_view.message = await interaction.original_response()


class ClaimRouteView(discord.ui.View):
    def __init__(
        self,
        cog,
        pilot,
        aircraft,
        schedule,
        pilot_if_user_id,
        routes_to_assign,
    ):
        super().__init__(timeout=300)

        self.cog = cog
        self.pilot = pilot
        self.aircraft = aircraft
        self.schedule = schedule
        self.pilot_if_user_id = str(pilot_if_user_id)
        self.routes_to_assign = list(routes_to_assign)

        self.claimed = False
        self.copilot = None
        self.copilot_if_user_id = None
        self.copilot_callsign = None
        self.message = None
        self.confirmation_embed = None

    async def on_timeout(self):
        if self.claimed or self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(
                content="This flight request expired. Run `/request-flight` again.",
                view=self,
            )
        except discord.HTTPException:
            log.exception("Could not disable expired route confirmation")

    async def interaction_check(self, interaction):
        if str(interaction.user.id) != str(self.pilot.get("discordid")):
            await interaction.response.send_message(
                "Only the requesting pilot can confirm this flight.",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="Set Co-pilot",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def set_copilot(self, interaction, button):
        if self.claimed:
            await interaction.response.send_message(
                "This request has already been processed.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(CopilotCallsignModal(self))

    @discord.ui.button(
        label="Confirm & Assign Listed Routes",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def confirm(self, interaction, button):
        if self.claimed:
            await interaction.response.send_message(
                "This request has already been processed.",
                ephemeral=True,
            )
            return

        self.claimed = True

        for child in self.children:
            child.disabled = True

        if interaction.response.is_done():
            await interaction.edit_original_response(view=None)
        else:
            await interaction.response.edit_message(view=self)
        if (
            self.message
            and interaction.message
            and self.message.id != interaction.message.id
        ):
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                log.exception("Could not disable the original route confirmation")

        assigned = []
        failed_route = None
        schedule_order_note = ""
        registration = _registration(self.aircraft)
        hub = _aircraft_hub(self.aircraft)
        hubs = _aircraft_hubs(self.aircraft)
        airline = _airline_name(self.aircraft)
        cargo_aircraft = is_cargo_registration(registration)

        await self.cog._booking_lock.acquire()
        try:
            manager = getattr(self.cog.bot, "if_live_manager", None)

            if manager is None:
                raise RuntimeError(
                    "Infinite Flight Live manager is not initialized"
                )

            async with await manager.get_client(TOKEN_OWNER_ID) as client:
                aircraft_id = str(_get(self.aircraft, "id"))

                try:
                    organization_schedules = (
                        await self.cog.get_organization_schedules(client)
                    )
                except Exception:
                    log.exception(
                        "Could not verify organization-wide aircraft bookings"
                    )
                    await interaction.edit_original_response(
                        content=(
                            "I couldn't verify your current aircraft bookings, "
                            "so nothing was assigned. Please try again shortly."
                        ),
                        embed=None,
                        view=None,
                    )
                    return

                pilot_bookings, pilot_ready = await self.cog.booked_aircraft_ids(
                    client,
                    self.pilot_if_user_id,
                    schedules=organization_schedules,
                )

                if len(pilot_bookings - {aircraft_id}) >= MAX_BOOKED_AIRCRAFT:
                    await interaction.edit_original_response(
                        content=(
                            "⛔ **Aircraft booking limit reached.** You already have "
                            "2 other aircraft with unflown outbound routes assigned. "
                            "Flights that have taken off and return legs do not count. "
                            "Contact a Live Admin if you believe this is incorrect."
                        ),
                        embed=None,
                        view=None,
                    )
                    return

                if not pilot_ready:
                    log.warning(
                        "Booking check for pilot %s is incomplete; continuing with "
                        "known cached assignments. Global booking limit is not guaranteed.",
                        self.pilot_if_user_id,
                    )

                if self.copilot_if_user_id:
                    copilot_bookings, copilot_ready = (
                        await self.cog.booked_aircraft_ids(
                            client,
                            self.copilot_if_user_id,
                            schedules=organization_schedules,
                        )
                    )

                    if len(copilot_bookings - {aircraft_id}) >= MAX_BOOKED_AIRCRAFT:
                        await interaction.edit_original_response(
                            content=(
                                "⛔ **Co-pilot aircraft booking limit reached.** "
                                f"{self.copilot_callsign} already has 2 other aircraft "
                                "with unflown outbound routes assigned. Flights that "
                                "have taken off and return legs do not count. Choose "
                                "another co-pilot or contact a Live Admin."
                            ),
                            embed=None,
                            view=None,
                        )
                        return

                    if not copilot_ready:
                        log.warning(
                            "Booking check for co-pilot %s is incomplete; continuing "
                            "with known cached assignments.",
                            self.copilot_if_user_id,
                        )

                schedules = _schedules_for_aircraft(
                    _active_schedules(
                        await self.cog.get_schedules(
                            client,
                            aircraft_id,
                            force=True,
                        )
                    ),
                    aircraft_id,
                )

                if not schedules:
                    await interaction.edit_original_response(
                        content=(
                            f"{registration} has no active scheduled "
                            f"{'cargo ' if cargo_aircraft else ''}routes. "
                            "Contact a Live Admin."
                        ),
                        embed=None,
                        view=None,
                    )
                    return

                current_first = next(
                    (
                        route
                        for route in schedules
                        if str(_get(route, "id")) == str(_get(self.schedule, "id"))
                    ),
                    None,
                )

                if (
                    current_first is None
                    or _is_unavailable_status(current_first)
                    or (_get(current_first, "crew", []) or [])
                ):
                    await interaction.edit_original_response(
                        content=(
                            "The route you selected is no longer available. "
                            "Nothing was assigned; run `/request-flight` again."
                        ),
                        embed=None,
                        view=None,
                    )
                    return

                if cargo_aircraft and _has_active_assignment_for_pilots(
                    schedules,
                    (self.pilot_if_user_id, self.copilot_if_user_id),
                ):
                    await interaction.edit_original_response(
                        content=(
                            f"**{registration} cargo routes are booked two legs at "
                            "a time.** Finish your currently assigned cargo leg(s) "
                            "before booking the next pair."
                        ),
                        embed=None,
                        view=None,
                    )
                    return

                start_index = next(
                    (
                        index
                        for index, route in enumerate(schedules)
                        if str(_get(route, "id")) == str(_get(self.schedule, "id"))
                    ),
                    -1,
                )

                current_routes = (
                    _build_cargo_route_chain(schedules, start_index)
                    if cargo_aircraft
                    else _build_connected_route_chain(
                        schedules,
                        start_index,
                        hubs,
                    )
                )

                if (
                    not current_routes
                    or _route_ids(current_routes) != _route_ids(self.routes_to_assign)
                ):
                    if cargo_aircraft:
                        changed_route_message = (
                            "The selected cargo legs changed or are no longer "
                            "available. Nothing was assigned; run `/request-flight` "
                            "again."
                        )
                    else:
                        changed_route_message = (
                            "The selected route chain changed or no longer returns "
                            f"to the airline hub ({hub}). Nothing was assigned; "
                            "run `/request-flight` again."
                        )
                    await interaction.edit_original_response(
                        content=changed_route_message,
                        embed=None,
                        view=None,
                    )
                    return

                insertion_index, after_id = _assigned_schedule_prefix(schedules)
                route_ids = _route_ids(current_routes)
                already_positioned = (
                    _route_ids(
                        schedules[
                            insertion_index : insertion_index + len(route_ids)
                        ]
                    )
                    == route_ids
                )

                if not already_positioned:
                    next_after_id = after_id
                    reordered_count = 0
                    for route_id in route_ids:
                        try:
                            reordered = await client.reorder_schedules(
                                aircraft_id,
                                route_id,
                                after_id=next_after_id,
                            )
                        except Exception:
                            log.exception(
                                "Could not reorder schedule %s for aircraft %s",
                                route_id,
                                aircraft_id,
                            )
                            partial_reorder_note = (
                                f" Up to {reordered_count + 1} route(s) may already "
                                "have moved; check the schedule before retrying."
                                if reordered_count
                                else ""
                            )
                            await interaction.edit_original_response(
                                content=(
                                    "I couldn't move the selected route chain beneath "
                                    "the already-assigned routes. Nothing was assigned;"
                                    f"{partial_reorder_note} please contact a Live Admin."
                                ),
                                embed=None,
                                view=None,
                            )
                            return

                        if not reordered:
                            log.warning(
                                "Infinite Flight did not reorder schedule %s "
                                "for aircraft %s.",
                                route_id,
                                aircraft_id,
                            )
                            partial_reorder_note = (
                                f" Up to {reordered_count} route(s) may already have "
                                "moved; check the schedule before retrying."
                                if reordered_count
                                else ""
                            )
                            await interaction.edit_original_response(
                                content=(
                                    "Infinite Flight rejected the schedule reorder. "
                                    "Nothing was assigned;"
                                    f"{partial_reorder_note} please contact a Live Admin."
                                ),
                                embed=None,
                                view=None,
                            )
                            return
                        next_after_id = route_id
                        reordered_count += 1

                    self.cog._schedule_cache.pop(aircraft_id, None)
                    try:
                        reordered_schedules = _active_schedules(
                            await self.cog.get_schedules(
                                client,
                                aircraft_id,
                                force=True,
                            )
                        )
                    except Exception:
                        log.exception(
                            "Could not verify reordered schedules for aircraft %s",
                            aircraft_id,
                        )
                        await interaction.edit_original_response(
                            content=(
                                "The schedule reorder was requested, but I couldn't "
                                "verify the new order. Nothing was assigned; please "
                                "check the aircraft schedule before retrying."
                            ),
                            embed=None,
                            view=None,
                        )
                        return

                    verified_index, _ = _assigned_schedule_prefix(
                        reordered_schedules
                    )
                    if (
                        _route_ids(
                            reordered_schedules[
                                verified_index : verified_index + len(route_ids)
                            ]
                        )
                        != route_ids
                    ):
                        log.error(
                            "Schedule reorder verification failed for aircraft %s",
                            aircraft_id,
                        )
                        await interaction.edit_original_response(
                            content=(
                                "Infinite Flight did not place the selected routes "
                                "beneath the already-assigned routes. Nothing was "
                                "assigned; please contact a Live Admin."
                            ),
                            embed=None,
                            view=None,
                        )
                        return

                schedule_order_note = (
                    "The selected route chain is beneath all consecutive "
                    "already-assigned routes."
                )

                for current in current_routes:
                    failed_route = current

                    if _is_unavailable_status(current) or (_get(current, "crew", []) or []):
                        break

                    if assigned and not _connected_routes(assigned[-1], current):
                        break

                    if (
                        not cargo_aircraft
                        and assigned
                        and str(
                            _get(assigned[-1], "destination_icao", "") or ""
                        ).strip().upper() in hubs
                    ):
                        break

                    updated = await client.assign_crew(
                        str(_get(current, "id")),
                        self.pilot_if_user_id,
                        0,
                    )

                    crew = _get(updated, "crew", []) or []

                    if not any(
                        _crew_user_id(member) == self.pilot_if_user_id
                        for member in crew
                    ):
                        try:
                            refreshed = _active_schedules(
                                await self.cog.get_schedules(
                                    client,
                                    aircraft_id,
                                    force=True,
                                )
                            )
                        except Exception:
                            log.exception(
                                "Could not verify pilot assignment for route %s",
                                _get(current, "id"),
                            )
                            break

                        match = next(
                            (
                                item
                                for item in refreshed
                                if str(_get(item, "id")) == str(_get(current, "id"))
                            ),
                            None,
                        )

                        crew = _get(match, "crew", []) or [] if match else []

                    pilot_assigned = any(
                        _crew_user_id(member) == self.pilot_if_user_id
                        for member in crew
                    )

                    if not pilot_assigned:
                        break

                    if self.copilot_if_user_id:
                        updated = await client.assign_crew(
                            str(_get(current, "id")),
                            self.copilot_if_user_id,
                            1,
                        )

                        crew = _get(updated, "crew", []) or []

                        if not any(
                            _crew_user_id(member) == self.copilot_if_user_id
                            for member in crew
                        ):
                            try:
                                refreshed = _active_schedules(
                                    await self.cog.get_schedules(
                                        client,
                                        aircraft_id,
                                        force=True,
                                    )
                                )
                            except Exception:
                                log.exception(
                                    "Could not verify co-pilot assignment for route %s",
                                    _get(current, "id"),
                                )
                                break

                            match = next(
                                (
                                    item
                                    for item in refreshed
                                    if str(_get(item, "id")) == str(_get(current, "id"))
                                ),
                                None,
                            )

                            crew = _get(match, "crew", []) or [] if match else []

                        if not (
                            any(
                                _crew_user_id(member) == self.pilot_if_user_id
                                for member in crew
                            )
                            and any(
                                _crew_user_id(member) == self.copilot_if_user_id
                                for member in crew
                            )
                        ):
                            log.error(
                                "Could not verify both pilots on route %s",
                                _get(current, "id"),
                            )
                            break

                    assigned.append(current)
                    failed_route = None

                    if (
                        str(
                            _get(current, "destination_icao", "") or ""
                        ).strip().upper() in hubs
                    ):
                        break

            if not assigned:
                await interaction.edit_original_response(
                    content=(
                        "I couldn't verify any route assignments. Contact a Live Admin "
                        "before retrying, especially if a pilot may have been partially assigned."
                    ),
                    embed=None,
                    view=None,
                )
                return

            route_lines = "\n".join(_route_text(route) for route in assigned)
            pilot_name = self.pilot.get(
                "callsign",
                interaction.user.display_name,
            )
            copilot_line = ""

            if self.copilot:
                discord_id = (
                    self.copilot.get("discordid")
                    or self.copilot.get("discord_id")
                )
                mention = (
                    f"<@{discord_id}>"
                    if discord_id
                    else f"**{self.copilot_callsign}**"
                )
                copilot_line = (
                    f"Co-pilot: {mention} · **{self.copilot_callsign}**\n"
                )

            if interaction.channel is None:
                await interaction.edit_original_response(
                    content=(
                        "Routes were assigned, but I couldn't access this channel "
                        "to post confirmation. Contact staff."
                    ),
                    embed=None,
                    view=None,
                )
                return

            await interaction.channel.send(
                f"✅ **Flight assigned to <@{interaction.user.id}>!**\n"
                f"Pilot: **{pilot_name}**\n"
                f"{copilot_line}"
                f"Aircraft: **{registration}** ({_model_label(self.aircraft)})\n"
                + (
                    ""
                    if cargo_aircraft
                    else f"Airline hub: **{airline} · {hub}**\n"
                )
                + f"Route(s):\n{route_lines}\n"
                + f"Schedule: {schedule_order_note}\n\n"
                + f"**Rules**\n{FLIGHT_RULES}",
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                ),
            )

            if failed_route is not None:
                result = (
                    f"⚠️ {len(assigned)} route(s) were assigned, but the next route "
                    "couldn't be confirmed. Contact staff before retrying."
                )
            else:
                result = (
                    f"✅ All {len(assigned)} selected route(s) were verified and posted."
                )

            await interaction.edit_original_response(
                content=result,
                embed=None,
                view=None,
            )

        except Exception:
            log.exception("Failed to assign Infinite Flight schedule crew")

            message = (
                f"⚠️ {len(assigned)} route(s) may have been assigned. Check the schedule "
                "and contact a Live Admin before retrying."
                if assigned
                else (
                    "There was an error while assigning the flight. "
                    "Contact a Live Admin before retrying."
                )
            )

            try:
                await interaction.edit_original_response(
                    content=message,
                    embed=None,
                    view=None,
                )
            except Exception:
                log.exception("Could not update assignment response")
        finally:
            self.cog._booking_lock.release()

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def cancel(self, interaction, button):
        if self.claimed:
            await interaction.response.send_message(
                "This request has already been processed.",
                ephemeral=True,
            )
            return

        self.claimed = True

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content="Flight request cancelled.",
            embed=None,
            view=self,
        )
        if (
            self.message
            and interaction.message
            and self.message.id != interaction.message.id
        ):
            try:
                await self.message.edit(
                    content="Flight request cancelled.",
                    embed=None,
                    view=self,
                )
            except discord.HTTPException:
                log.exception("Could not update the original route confirmation")


class CopilotConfirmationView(discord.ui.View):
    def __init__(self, claim_view):
        super().__init__(timeout=300)
        self.claim_view = claim_view
        self.message = None

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message is None:
            return
        try:
            await self.message.edit(
                content="This flight confirmation expired. Run `/request-flight` again.",
                view=self,
            )
        except discord.HTTPException:
            log.exception("Could not disable expired co-pilot confirmation")

    async def interaction_check(self, interaction):
        if str(interaction.user.id) != str(self.claim_view.pilot.get("discordid")):
            await interaction.response.send_message(
                "Only the requesting pilot can confirm this flight.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Confirm & Assign",
        style=discord.ButtonStyle.success,
    )
    async def confirm(self, interaction, button):
        if self.claim_view.claimed:
            await interaction.response.send_message(
                "This request has already been processed.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        await self.claim_view.confirm.callback(interaction)

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel(self, interaction, button):
        if self.claim_view.claimed:
            await interaction.response.send_message(
                "This request has already been processed.",
                ephemeral=True,
            )
            return
        await self.claim_view.cancel.callback(interaction)


class RequestFlightCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        self._aircraft_cache = None
        self._aircraft_cache_at = 0.0
        self._aircraft_cache_lock = asyncio.Lock()

        self._schedule_cache = {}
        self._schedule_locks = {}
        self._schedule_scan_cursor = 0
        self._scan_lock = asyncio.Lock()
        self._rate_limit_until = 0.0
        self._booking_lock = asyncio.Lock()

    async def get_organization_schedules(self, client):
        """Fetch every organization schedule for an authoritative booking check."""
        schedules = []
        page_number = 1
        total_pages = 1

        while page_number <= total_pages:
            page = await client.list_organization_schedules(
                ORG_ID,
                page=page_number,
                page_size=100,
            )
            if page is None or not hasattr(page, "items"):
                raise RuntimeError(
                    "Infinite Flight returned an invalid organization schedule page"
                )

            schedules.extend(page.items or [])
            try:
                total_pages = max(1, int(page.total_pages or 1))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "Infinite Flight returned an invalid organization page count"
                ) from exc
            page_number += 1

        return schedules

    async def _get_fleet(self, client, force=False):
        now = time.monotonic()

        if (
            not force
            and self._aircraft_cache is not None
            and now - self._aircraft_cache_at < FLEET_CACHE_SECONDS
        ):
            return self._aircraft_cache

        async with self._aircraft_cache_lock:
            now = time.monotonic()

            if (
                not force
                and self._aircraft_cache is not None
                and now - self._aircraft_cache_at < FLEET_CACHE_SECONDS
            ):
                return self._aircraft_cache

            fleet = await client.list_organization_aircraft(ORG_ID)

            self._aircraft_cache = [
                aircraft
                for aircraft in (fleet or [])
                if _registration(aircraft) not in RETIRED_REGISTRATIONS
                and _get(aircraft, "id") not in (None, "")
            ]
            fleet_service = getattr(self.bot, "fleet_service", None)
            if fleet_service is not None:
                try:
                    await fleet_service.enrich_fleet(self._aircraft_cache)
                except Exception:
                    log.exception(
                        "Could not enrich request-flight aircraft with VA database models."
                    )

            self._aircraft_cache_at = time.monotonic()
            return self._aircraft_cache

    async def get_schedules(self, client, aircraft_id, force=False):
        """Get schedules with caching and a shared rate-limit cooldown."""
        aircraft_id = str(aircraft_id)
        now = time.monotonic()
        cached = self._schedule_cache.get(aircraft_id)

        if (
            cached
            and not force
            and now - cached[0] < SCHEDULE_CACHE_SECONDS
        ):
            return cached[1]

        if now < self._rate_limit_until:
            if cached:
                return cached[1]

            remaining = max(1, int(self._rate_limit_until - now))
            raise RuntimeError(
                f"Infinite Flight API cooldown is active for about {remaining} more seconds, "
                "and this aircraft has no cached schedule."
            )

        lock = self._schedule_locks.setdefault(
            aircraft_id,
            asyncio.Lock(),
        )

        async with lock:
            now = time.monotonic()
            cached = self._schedule_cache.get(aircraft_id)

            if (
                cached
                and not force
                and now - cached[0] < SCHEDULE_CACHE_SECONDS
            ):
                return cached[1]

            if now < self._rate_limit_until:
                if cached:
                    return cached[1]

                remaining = max(1, int(self._rate_limit_until - now))
                raise RuntimeError(
                    f"Infinite Flight API cooldown is active for about {remaining} more seconds."
                )

            try:
                schedules = await client.list_aircraft_schedules(aircraft_id)

            except Exception as exc:
                error_name = type(exc).__name__.lower()
                error_text = str(exc)

                if "ratelimit" in error_name or "429" in error_text:
                    match = re.search(
                        r"try again in\s+(\d+)\s+second",
                        error_text,
                        re.IGNORECASE,
                    )

                    cooldown_seconds = int(match.group(1)) + 2 if match else 35

                    self._rate_limit_until = max(
                        self._rate_limit_until,
                        time.monotonic() + cooldown_seconds,
                    )

                    log.warning(
                        "Infinite Flight API rate limit reached; cooling down for %s seconds.",
                        cooldown_seconds,
                    )

                    if cached:
                        return cached[1]

                raise

            schedules = list(schedules or [])
            self._schedule_cache[aircraft_id] = (
                time.monotonic(),
                schedules,
            )

            return schedules

    async def booked_aircraft_ids(self, client, if_user_id, schedules=None):
        """Return aircraft with unflown outbound assignments and snapshot freshness."""
        if schedules is not None:
            return _counted_booked_aircraft_ids(schedules, if_user_id), True

        if not ORG_ID:
            raise RuntimeError("IF_ORGANIZATION_ID is not configured")

        fleet = await self._get_fleet(client)

        aircraft_ids = [
            str(_get(aircraft, "id"))
            for aircraft in fleet
            if _get(aircraft, "id") not in (None, "")
        ]

        if not aircraft_ids:
            return set(), True

        now = time.monotonic()

        stale_ids = [
            aircraft_id
            for aircraft_id in aircraft_ids
            if (
                aircraft_id not in self._schedule_cache
                or now - self._schedule_cache[aircraft_id][0]
                >= SCHEDULE_CACHE_SECONDS
            )
        ]

        if stale_ids and now >= self._rate_limit_until:
            async with self._scan_lock:
                now = time.monotonic()

                stale_ids = [
                    aircraft_id
                    for aircraft_id in aircraft_ids
                    if (
                        aircraft_id not in self._schedule_cache
                        or now - self._schedule_cache[aircraft_id][0]
                        >= SCHEDULE_CACHE_SECONDS
                    )
                ]

                if stale_ids and now >= self._rate_limit_until:
                    count = min(SCHEDULES_PER_SCAN, len(stale_ids))
                    start = self._schedule_scan_cursor % len(stale_ids)

                    batch = [
                        stale_ids[(start + offset) % len(stale_ids)]
                        for offset in range(count)
                    ]

                    self._schedule_scan_cursor = (
                        start + count
                    ) % max(1, len(stale_ids))

                    for aircraft_id in batch:
                        try:
                            await self.get_schedules(
                                client,
                                aircraft_id,
                                force=False,
                            )
                        except Exception as exc:
                            log.warning(
                                "Could not refresh schedules for aircraft %s (%s)",
                                aircraft_id,
                                type(exc).__name__,
                            )

                            if time.monotonic() < self._rate_limit_until:
                                break

        target_id = str(if_user_id).strip()
        booked = set()
        missing_count = 0
        stale_count = 0
        now = time.monotonic()

        for aircraft_id in aircraft_ids:
            cached = self._schedule_cache.get(aircraft_id)

            if not cached:
                missing_count += 1
                continue

            if now - cached[0] >= SCHEDULE_CACHE_SECONDS:
                stale_count += 1

            booked.update(
                _counted_booked_aircraft_ids(cached[1], target_id)
            )

        cache_complete = missing_count == 0 and stale_count == 0

        if not cache_complete:
            log.info(
                "Booking cache incomplete: %d aircraft missing snapshots, "
                "%d aircraft with stale snapshots. Known bookings: %d.",
                missing_count,
                stale_count,
                len(booked),
            )

        return booked, cache_complete

    @app_commands.command(
        name="current-assignments",
        description="View your currently assigned aircraft and routes",
    )
    async def current_assignments(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not ORG_ID:
            await interaction.followup.send(
                "The bot is missing `IF_ORGANIZATION_ID`.",
                ephemeral=True,
            )
            return

        pilots_model = getattr(self.bot, "pilots_model", None)
        manager = getattr(self.bot, "if_live_manager", None)
        if pilots_model is None or manager is None:
            await interaction.followup.send(
                "Pilot database or Infinite Flight services are unavailable. Contact staff.",
                ephemeral=True,
            )
            return

        try:
            pilot = await pilots_model.get_pilot_by_discord_id(
                str(interaction.user.id)
            )
            if not pilot:
                await interaction.followup.send(
                    "No active pilot profile is linked to your Discord account.",
                    ephemeral=True,
                )
                return

            if_user_id = str(pilot.get("ifuserid") or "").strip()
            if not if_user_id:
                await interaction.followup.send(
                    "Your pilot profile has no Infinite Flight user ID. Contact staff to update it.",
                    ephemeral=True,
                )
                return

            async with await manager.get_client(TOKEN_OWNER_ID) as client:
                schedules = await self.get_organization_schedules(client)
                fleet = await self._get_fleet(client)

            registration_by_aircraft = {
                str(_get(aircraft, "id")): _registration(aircraft)
                for aircraft in fleet
                if _get(aircraft, "id") not in (None, "")
            }
            assignments = _current_assignments_for_pilot(
                schedules,
                if_user_id,
                registration_by_aircraft,
            )

            if not assignments:
                await interaction.followup.send(
                    "You have no current aircraft assignments.",
                    ephemeral=True,
                )
                return

            status_labels = {
                0: "Scheduled",
                1: "Scheduled",
                2: "Scheduled",
                3: "Scheduled",
                4: "Scheduled",
                5: "Scheduled",
                6: "In flight",
                7: "Diverted",
                10: "Taxiing",
            }
            embed = discord.Embed(
                title=f"Current assignments · {pilot.get('callsign', 'Pilot')}",
                description=(
                    f"You have **{len(assignments)} distinct aircraft** assigned. "
                    "Connected routes on the same aircraft count as one booking."
                ),
                color=discord.Color.blue(),
            )

            for aircraft_id, routes in sorted(
                assignments.items(),
                key=lambda item: registration_by_aircraft.get(item[0], item[0]),
            )[:25]:
                registration = (
                    registration_by_aircraft.get(aircraft_id) or "Aircraft"
                )
                route_lines = []
                for route in routes:
                    try:
                        status = int(_get(route, "status"))
                    except (TypeError, ValueError):
                        status = None
                    status_label = status_labels.get(status, "Active")
                    route_lines.append(
                        f"• **{_get(route, 'callsign') or 'Callsign unavailable'}** "
                        f"{_get(route, 'origin_icao') or '???'} → "
                        f"{_get(route, 'destination_icao') or '???'} "
                        f"· {status_label}"
                    )

                value = "\n".join(route_lines)
                if len(value) > 1024:
                    value = value[:1000].rsplit("\n", 1)[0] + "\n… more routes omitted"
                embed.add_field(
                    name=f"{registration} · {_airline_name({'registration': registration})}",
                    value=value,
                    inline=False,
                )

            if len(assignments) > 25:
                embed.set_footer(
                    text=f"Showing 25 of {len(assignments)} assigned aircraft."
                )

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            log.exception(
                "Could not load current assignments for Discord user %s",
                interaction.user.id,
            )
            await interaction.followup.send(
                "I couldn't load your current assignments. Please try again shortly.",
                ephemeral=True,
            )

    @app_commands.command(
        name="request-flight",
        description="Choose an aircraft and a scheduled route chain to request",
    )
    async def request_flight(self, interaction: discord.Interaction):
        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        if not ORG_ID:
            await interaction.followup.send(
                "The bot is missing `IF_ORGANIZATION_ID`.",
                ephemeral=True,
            )
            return

        pilots_model = getattr(self.bot, "pilots_model", None)

        if pilots_model is None:
            await interaction.followup.send(
                "Pilot database unavailable. Contact staff.",
                ephemeral=True,
            )
            return

        try:
            pilot = await pilots_model.get_pilot_by_discord_id(
                str(interaction.user.id)
            )

            if not pilot:
                await interaction.followup.send(
                    "No active pilot profile is linked to your Discord account.",
                    ephemeral=True,
                )
                return

            ifc_url = str(pilot.get("ifc") or "").strip()
            profile_match = re.search(
                r"/u/([^/?#]+)",
                ifc_url,
                re.IGNORECASE,
            )
            if profile_match:
                community_username = profile_match.group(1)
                if_api_manager = getattr(self.bot, "if_api_manager", None)
                if if_api_manager is None:
                    await interaction.followup.send(
                        "I couldn't verify your Infinite Flight profile from "
                        "Crew Center right now. Please contact staff.",
                        ephemeral=True,
                    )
                    return

                try:
                    profile_data = await if_api_manager.get_user_by_ifc_username(
                        community_username
                    )
                except Exception:
                    log.exception(
                        "Could not resolve the Community profile for pilot %s",
                        pilot.get("callsign", "unknown"),
                    )
                    await interaction.followup.send(
                        "I couldn't verify your Infinite Flight profile from "
                        "Crew Center right now. Please try again later.",
                        ephemeral=True,
                    )
                    return

                profile = (profile_data or {}).get("result") or {}
                resolved_username = str(
                    profile.get("discourseUsername") or ""
                ).strip()
                resolved_if_user_id = str(
                    profile.get("userId") or ""
                ).strip()
                if (
                    not resolved_if_user_id
                    or resolved_username.casefold()
                    != community_username.casefold()
                ):
                    log.warning(
                        "Could not verify Community username %s for pilot %s",
                        community_username,
                        pilot.get("callsign", "unknown"),
                    )
                    await interaction.followup.send(
                        "I couldn't match your Crew Center Community profile "
                        "to an Infinite Flight user. Please contact staff to "
                        "check the profile link.",
                        ephemeral=True,
                    )
                    return

                stored_if_user_id = str(pilot.get("ifuserid") or "").strip()
                if stored_if_user_id != resolved_if_user_id:
                    updated = await pilots_model.update_ifuserid_by_id(
                        pilot["id"],
                        resolved_if_user_id,
                    )
                    if updated != 1:
                        log.error(
                            "Could not update Infinite Flight user ID for pilot %s",
                            pilot.get("callsign", "unknown"),
                        )
                        await interaction.followup.send(
                            "I verified your Infinite Flight profile, but "
                            "couldn't save the updated link. Please contact staff.",
                            ephemeral=True,
                        )
                        return
                    log.info(
                        "Updated Infinite Flight profile link for pilot %s",
                        pilot.get("callsign", "unknown"),
                    )

                pilot["ifuserid"] = resolved_if_user_id

            if not pilot.get("ifuserid"):
                await interaction.followup.send(
                    "Your pilot profile has no Infinite Flight user ID linked.",
                    ephemeral=True,
                )
                return

            manager = getattr(self.bot, "if_live_manager", None)

            if manager is None:
                await interaction.followup.send(
                    "Infinite Flight Live is not initialized. Contact staff.",
                    ephemeral=True,
                )
                return

            # Do not scan every aircraft schedule here. The user must be able
            # to choose an aircraft before any booking-cache scan is attempted.
            async with await manager.get_client(TOKEN_OWNER_ID) as client:
                aircraft = sorted(
                    await self._get_fleet(client),
                    key=_registration,
                )

            if not aircraft:
                await interaction.followup.send(
                    "No aircraft were returned for Qatari Virtual.",
                    ephemeral=True,
                )
                return

            counts = {
                name: sum(
                    _aircraft_category(item) == name
                    for item in aircraft
                )
                for name in ("Airbus", "Boeing", "Other", "Qatar Cargo")
            }
            counts["Retired"] = len(RETIRED_REGISTRATIONS)

            await interaction.followup.send(
                f"Choose an aircraft category ({len(aircraft)} aircraft available): "
                f"Airbus {counts['Airbus']} · "
                f"Boeing {counts['Boeing']} · "
                f"Other {counts['Other']} · "
                f"Qatar Cargo {counts['Qatar Cargo']} · "
                f"Retired {counts['Retired']}.\n\n"
                f"⚠️ Maximum **{MAX_BOOKED_AIRCRAFT} aircraft with unflown outbound "
                "routes per pilot**. Taken-off flights and return legs do not count.",
                view=AircraftSelectView(self, pilot, aircraft),
                ephemeral=True,
            )

        except Exception:
            log.exception("Could not load aircraft for /request-flight")

            await interaction.followup.send(
                "I couldn't load Qatari Virtual's aircraft. Please try again shortly.",
                ephemeral=True,
            )

    async def show_aircraft_route(self, interaction, pilot, aircraft):
        """Load an aircraft schedule and let the pilot select a valid starting route."""
        await interaction.response.defer(
            ephemeral=True,
            thinking=False,
        )

        try:
            manager = getattr(self.bot, "if_live_manager", None)

            if manager is None:
                raise RuntimeError(
                    "Infinite Flight Live manager is not initialized"
                )

            aircraft_id = str(_get(aircraft, "id"))

            async with await manager.get_client(TOKEN_OWNER_ID) as client:
                position = await client.get_aircraft_position(aircraft_id)
                raw_schedules = await self.get_schedules(client, aircraft_id)

            schedules = _schedules_for_aircraft(
                _active_schedules(raw_schedules),
                aircraft_id,
            )
            registration = _registration(aircraft)
            airline = _airline_name(aircraft)
            hub = _aircraft_hub(aircraft)
            hubs = _aircraft_hubs(aircraft)
            cargo_aircraft = is_cargo_registration(registration)

            if not schedules:
                await interaction.edit_original_response(
                    content=(
                        f"{registration} has no active scheduled "
                        f"{'cargo ' if cargo_aircraft else ''}routes. "
                        "Contact a Live Admin."
                    ),
                    embed=None,
                    view=None,
                )
                return

            if cargo_aircraft and _has_active_assignment_for_pilots(
                schedules,
                (pilot.get("ifuserid"),),
            ):
                await interaction.edit_original_response(
                    content=(
                        f"**{registration} cargo routes are booked two legs at a "
                        "time.** Finish your currently assigned cargo leg(s) before "
                        "booking the next pair."
                    ),
                    embed=None,
                    view=None,
                )
                return

            # Present complete hub-return chains, or batches of two cargo legs.
            choices = []
            seen_start_routes = set()
            included_route_ids = set()

            for index, route in enumerate(schedules):
                if _is_unavailable_status(route) or (_get(route, "crew", []) or []):
                    continue

                route_key = _route_start_key(route)
                if (
                    route_key in seen_start_routes
                    or str(_get(route, "id")) in included_route_ids
                ):
                    continue

                chain = (
                    _build_cargo_route_chain(schedules, index)
                    if cargo_aircraft
                    else _build_connected_route_chain(schedules, index, hubs)
                )

                if chain:
                    choices.append((index, chain))
                    seen_start_routes.add(route_key)
                    included_route_ids.update(
                        str(_get(chain_route, "id"))
                        for chain_route in chain
                        if _get(chain_route, "id") not in (None, "")
                    )

            if not choices:
                if cargo_aircraft:
                    no_routes_message = (
                        f"**{registration} has no available cargo legs to book.** "
                        "Please ask a Live Admin to add cargo routes."
                    )
                else:
                    no_routes_message = (
                        f"**{registration} does not have a complete connected route "
                        f"chain in its schedule that returns to a configured "
                        f"{airline} hub ({hub}).** A route may end at an airport "
                        "without a following scheduled route back to a configured hub. "
                        "Please ask a Live Admin to add the missing connecting "
                        "routes to the schedule."
                    )
                await interaction.edit_original_response(
                    content=no_routes_message,
                    embed=None,
                    view=None,
                )
                return

            ground = _get(position, "is_on_ground")

            status = (
                f"Aircraft state: **{_state_label(position)}** · "
                + (
                    "On ground"
                    if ground is True
                    else "In flight"
                    if ground is False
                    else "Ground status unknown"
                )
            )

            embed = discord.Embed(
                title=f"Choose your route · {registration}",
                description=(
                    f"**Aircraft:** {_model_label(aircraft)}\n"
                    + (
                        "Cargo routes do not need to return to an approved hub."
                        if cargo_aircraft
                        else (
                            f"**Airline hubs:** {airline} · **{hub}**\n\n"
                            "Choose the starting route you want. Each option "
                            "includes the connected scheduled routes that follow "
                            "it and return to the airline hub.\n\n"
                            "Only routes that are currently unassigned and have a "
                            "complete connected chain back to the hub are listed."
                        )
                    )
                ),
                color=discord.Color.blue(),
            )

            embed.add_field(
                name="Aircraft status",
                value=status,
                inline=False,
            )

            embed.add_field(
                name="Valid route choices",
                value=str(len(choices)),
                inline=True,
            )

            embed.add_field(
                name="Rules",
                value=FLIGHT_RULES,
                inline=False,
            )

            embed.add_field(
                name="Aircraft booking limit",
                value=(
                    "Maximum **2 aircraft with unflown outbound routes per pilot**. "
                    "Once a route takes off, its aircraft no longer counts. Return "
                    "legs do not count toward the limit."
                ),
                inline=False,
            )

            view = RouteStartView(
                self,
                pilot,
                aircraft,
                schedules,
                choices,
            )

            await interaction.edit_original_response(
                content="Select the route chain you want to request:",
                embed=embed,
                view=view,
            )

        except Exception as exc:
            log.exception(
                "Could not inspect selected aircraft (registration=%s, aircraft_id=%s)",
                _registration(aircraft),
                _get(aircraft, "id"),
            )

            error_text = str(exc)

            if "cooldown is active" in error_text.lower():
                message = (
                    "⏳ Infinite Flight is temporarily rate-limited, and this aircraft "
                    "doesn't have a usable cached schedule yet. Please wait for the "
                    "cooldown to expire, then select the aircraft again."
                )
            else:
                message = (
                    "I couldn't inspect that aircraft's schedule right now. "
                    "Please try again shortly."
                )

            await interaction.edit_original_response(
                content=f"{message}\nError type: `{type(exc).__name__}`",
                embed=None,
                view=None,
            )

    async def show_route_confirmation(
        self,
        interaction,
        pilot,
        aircraft,
        schedules,
        start_index,
        routes,
    ):
        """Show confirmation for the route chain the pilot selected."""
        try:
            registration = _registration(aircraft)
            airline = _airline_name(aircraft)
            hub = _aircraft_hub(aircraft)
            hubs = _aircraft_hubs(aircraft)
            cargo_aircraft = is_cargo_registration(registration)

            if start_index < 0 or start_index >= len(schedules):
                await interaction.response.send_message(
                    "That route selection is invalid. Please run `/request-flight` again.",
                    ephemeral=True,
                )
                return

            selected_route = schedules[start_index]
            if (
                _is_unavailable_status(selected_route)
                or (_get(selected_route, "crew", []) or [])
            ):
                await interaction.response.send_message(
                    "That route is no longer available. Please select another route.",
                    ephemeral=True,
                )
                return

            current_chain = (
                _build_cargo_route_chain(schedules, start_index)
                if cargo_aircraft
                else _build_connected_route_chain(
                    schedules,
                    start_index,
                    hubs,
                )
            )

            if not current_chain or _route_ids(current_chain) != _route_ids(routes):
                await interaction.response.send_message(
                    "The selected route chain is no longer valid. Please choose a route again.",
                    ephemeral=True,
                )
                return

            route_lines = "\n".join(
                f"**{index}.** {_route_text(route)}"
                for index, route in enumerate(routes, 1)
            )
            final_hub = str(
                _get(routes[-1], "destination_icao", "") or ""
            ).strip().upper()
            route_policy = (
                ""
                if cargo_aircraft
                else (
                    f"**Return-to-base check:** The final route returns to "
                    f"**{final_hub}**."
                )
            )

            position_state = "Not checked in this step"

            embed = discord.Embed(
                title=f"Confirm flight request · {registration}",
                description=(
                    f"**Aircraft model:** {_model_label(aircraft)}\n"
                    f"**Registration:** {registration}\n"
                    + (
                        ""
                        if cargo_aircraft
                        else f"**Airline hubs:** {airline} · {hub}\n"
                    )
                    + f"**Starting route:** {_route_text(selected_route)}\n"
                    + f"**Routes to be assigned:** {len(routes)}\n\n"
                    + f"**Your selected route chain**\n{route_lines}\n\n"
                    + (f"{route_policy}\n\n" if route_policy else "")
                    + "Confirm only if you want every listed route assigned to you."
                ),
                color=discord.Color.green(),
            )

            embed.add_field(
                name="Aircraft status",
                value=position_state,
                inline=False,
            )

            embed.add_field(
                name="Rules",
                value=FLIGHT_RULES,
                inline=False,
            )

            embed.add_field(
                name="Co-pilot",
                value="Not selected (solo flight)",
                inline=False,
            )

            view = ClaimRouteView(
                self,
                pilot,
                aircraft,
                selected_route,
                pilot["ifuserid"],
                routes,
            )
            view.confirmation_embed = embed

            await interaction.response.edit_message(
                content="Review your selected route chain before confirming:",
                embed=embed,
                view=view,
            )

            view.message = interaction.message

        except Exception:
            log.exception("Could not display route confirmation")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "I couldn't prepare that route confirmation. Please try again.",
                    ephemeral=True,
                )


async def setup(bot):
    await bot.add_cog(RequestFlightCog(bot))

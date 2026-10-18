
"""Discord command for requesting scheduled routes on Qatari Virtual aircraft."""

import asyncio
import logging
import os
import time

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

ORG_ID = os.getenv("IF_ORGANIZATION_ID", "").strip()
TOKEN_OWNER_ID = 1
FLEET_CACHE_SECONDS = 300
HUB_ICAO = "OTHH"

AIRCRAFT_MODELS = {
    "A7-ANN": "A350-1000",
    "A7-APC": "A380-800",
    "A7-BAE": "777-300ER",
    "A7-ALR": "A350-900",
    "A7-DAC": "A350-900",
    "A7-BCA": "787-8",
    "A7BBA": "777-200LR",
    "A7BAG": "777-300ER",
    "A7-ANG": "A350-1000",
    "A7BCC": "787-8",
    "A7DAB": "A350-900",
    "A7-AOB": "A350-1000",
    "A7BAF": "777-300ER",
    "A7BAB": "777-300ER",
    "A7BCB": "787-8",
    "A7DAA": "A350-900",
    "A7AED": "A330-300",
    "A7-BAH": "777-300ER",
    "A7APD": "A380-800",
    "A7-BBB": "777-200LR",
    "A7AEE": "A330-300",
    "A7-BCD": "787-8",
    "A7ADS": "A321-200",
    "A7BFC": "777F",
    "A7BFA": "777F",
    "A7-AOA": "A350-1000",
    "A7-AOH": "A350-1000",
    "A7-BAA": "777-300ER",
    "B-LRO": "A350-900",
    "EC-ORE": "A321neo",
    "A7-BAC": "777-300ER",
    "A7-BAI": "777-300ER",
    "N302NY": "A321neo",
    "N762AN": "777-200ER",
    "VH-ZNF": "787-9",
    "IF-FXQK": "777-300ER",
    "IF-MPSK": "A321neo",
    "IF-SKJK": "777-300ER",
    "IF-KSKI": "787-9",
}

RULES = (
    "**Qatari Virtual flight rules**\n"
    "• If an aircraft is stopped in the air, you have 24 hours to land.\n"
    "• If parked at a hub airport, you have 6 hours from assignment to take off.\n"
    "• If parked at a non-hub airport, you have 12 hours from assignment to take off.\n"
    "• If overdue, an admin may take over the flight or make the assignment available to other pilots."
)


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _crew_user_id(member):
    for key in ("userId", "user_id", "ifuserid", "id"):
        value = _get(member, key)
        if value not in (None, ""):
            return str(value)
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

    registration = str(
        _get(aircraft, "registration", "") or ""
    ).strip().upper()

    return AIRCRAFT_MODELS.get(registration, "")


def _aircraft_category(aircraft):
    model = _aircraft_model(aircraft).lower().replace(" ", "")

    if model.startswith("a") and len(model) > 1 and model[1].isdigit():
        return "Airbus"

    if model.startswith(
        ("777", "787", "737", "747", "757", "767", "717", "727")
    ):
        return "Boeing"

    return "Other"


def _model_label(aircraft):
    return _aircraft_model(aircraft) or "Model unknown"


def _state_label(position):
    labels = {
        0: "Unknown",
        1: "On ground",
        2: "In flight",
        3: "Cancelled",
        4: "Stopped",
        5: "Maintenance",
    }

    raw = _get(position, "state", None)

    try:
        return labels.get(int(raw), f"Unknown ({raw})")
    except (TypeError, ValueError):
        return str(raw if raw is not None else "Unknown")


def _route_text(schedule):
    callsign = _get(schedule, "callsign") or "Callsign unavailable from API"
    origin = _get(schedule, "origin_icao") or "???"
    destination = _get(schedule, "destination_icao") or "???"

    return f"**{callsign}** — {origin} → {destination}"


def _sequence(schedule):
    try:
        return int(_get(schedule, "sequence", 0))
    except (TypeError, ValueError):
        return 0


def _sorted_schedules(schedules):
    return sorted(schedules or [], key=_sequence)


def _route_status_number(schedule):
    try:
        return int(_get(schedule, "status", None))
    except (TypeError, ValueError):
        return None


def _is_unavailable_status(schedule):
    # Status values used by this cog: CANCELLED=9, ARRIVED=11.
    return _route_status_number(schedule) in {9, 11}


def _active_schedules(schedules):
    """Deduplicate schedule IDs and remove cancelled/arrived history rows."""
    by_id = {}
    no_id = []

    for route in _sorted_schedules(schedules):
        route_id = _get(route, "id")

        if route_id in (None, ""):
            no_id.append(route)
            continue

        key = str(route_id)
        previous = by_id.get(key)

        if previous is None:
            by_id[key] = route
            continue

        prev_terminal = _is_unavailable_status(previous)
        this_terminal = _is_unavailable_status(route)

        if this_terminal and not prev_terminal:
            by_id[key] = route
        elif this_terminal == prev_terminal:
            if (
                not (_get(previous, "crew", []) or [])
                and (_get(route, "crew", []) or [])
            ):
                by_id[key] = route

    unique = list(by_id.values()) + no_id

    return [
        route
        for route in _sorted_schedules(unique)
        if not _is_unavailable_status(route)
    ]


def _next_claimable_index(schedules):
    """Find the first unassigned route in schedule order."""
    for index, route in enumerate(schedules):
        if not (_get(route, "crew", []) or []):
            return index
    return None


def _connected_routes(first, second):
    """True when the second route starts at the first route's destination."""
    first_destination = str(
        _get(first, "destination_icao", "") or ""
    ).strip().upper()

    second_origin = str(
        _get(second, "origin_icao", "") or ""
    ).strip().upper()

    return bool(
        first_destination
        and second_origin
        and first_destination == second_origin
    )


def _build_connected_route_chain(schedules, start_index):
    """
    Build the connected, unassigned route chain.

    The chain ends immediately after including a route whose destination
    is OTHH. It never continues with the next departure from OTHH.
    """
    if start_index < 0 or start_index >= len(schedules):
        return []

    first = schedules[start_index]

    if _is_unavailable_status(first) or (_get(first, "crew", []) or []):
        return []

    routes = []
    previous = None

    for candidate in schedules[start_index:]:
        if _is_unavailable_status(candidate):
            break

        if _get(candidate, "crew", []) or []:
            break

        origin = str(
            _get(candidate, "origin_icao", "") or ""
        ).strip().upper()

        destination = str(
            _get(candidate, "destination_icao", "") or ""
        ).strip().upper()

        # An incomplete route cannot safely be chained.
        if not origin or not destination:
            break

        # Every route after the first must connect to the previous route.
        if previous is not None and not _connected_routes(previous, candidate):
            break

        routes.append(candidate)

        # Include the return-to-hub route, then stop.
        if destination == HUB_ICAO:
            break

        previous = candidate

    return routes


def _route_ids(routes):
    return [str(_get(route, "id")) for route in routes]


class AircraftCategorySelect(discord.ui.Select):
    def __init__(self, cog, pilot, aircraft):
        self.cog = cog
        self.pilot = pilot
        self.aircraft = aircraft

        categories = [
            name
            for name in ("Airbus", "Boeing", "Other")
            if any(_aircraft_category(item) == name for item in aircraft)
        ]

        options = [
            discord.SelectOption(
                label=name,
                description=(
                    f"{sum(_aircraft_category(item) == name for item in aircraft)} "
                    "aircraft"
                ),
            )
            for name in categories
        ]

        super().__init__(
            placeholder="Choose aircraft type…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != str(self.pilot.get("discordid")):
            await interaction.response.send_message(
                "Only the pilot who requested this list can use it.",
                ephemeral=True,
            )
            return

        category = self.values[0]
        selected = [
            item for item in self.aircraft
            if _aircraft_category(item) == category
        ]

        await interaction.response.edit_message(
            content=(
                f"**{category} aircraft** — choose a registration. "
                "The model is shown in each option."
            ),
            view=AircraftListView(
                self.cog, self.pilot, selected, category, 0
            ),
        )


class AircraftListSelect(discord.ui.Select):
    def __init__(self, cog, pilot, aircraft, category, page):
        self.cog = cog
        self.pilot = pilot
        self.aircraft = aircraft
        self.category = category
        self.page = page

        start = page * 25
        page_aircraft = aircraft[start:start + 25]
        options = []

        for item in page_aircraft:
            registration = str(
                _get(item, "registration") or "Unknown registration"
            )

            options.append(
                discord.SelectOption(
                    label=registration[:100],
                    description=_model_label(item)[:100],
                    value=str(_get(item, "id")),
                )
            )

        pages = max(1, (len(aircraft) + 24) // 25)

        super().__init__(
            placeholder=(
                f"Choose {category} registration "
                f"(page {page + 1}/{pages})…"
            ),
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != str(self.pilot.get("discordid")):
            await interaction.response.send_message(
                "Only the pilot who requested this list can use it.",
                ephemeral=True,
            )
            return

        aircraft = next(
            (
                item for item in self.aircraft
                if str(_get(item, "id")) == self.values[0]
            ),
            None,
        )

        if aircraft is None:
            await interaction.response.send_message(
                "I couldn't find that aircraft. Run `/request-flight` again.",
                ephemeral=True,
            )
            return

        await self.cog.show_aircraft_route(
            interaction,
            self.pilot,
            aircraft,
        )


class AircraftListView(discord.ui.View):
    def __init__(self, cog, pilot, aircraft, category, page=0):
        super().__init__(timeout=180)

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

    async def callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != str(self.pilot.get("discordid")):
            await interaction.response.send_message(
                "Only the pilot who requested this list can use it.",
                ephemeral=True,
            )
            return

        if self.back:
            await interaction.response.edit_message(
                content="Choose an aircraft category:",
                view=AircraftSelectView(
                    self.cog,
                    self.pilot,
                    self.cog._aircraft_cache or self.aircraft,
                ),
            )
        else:
            await interaction.response.edit_message(
                content=(
                    f"**{self.category} aircraft** — "
                    "choose a registration:"
                ),
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
        super().__init__(timeout=180)
        self.add_item(AircraftCategorySelect(cog, pilot, aircraft))


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
        super().__init__(timeout=120)

        self.cog = cog
        self.pilot = pilot
        self.aircraft = aircraft
        self.schedule = schedule
        self.pilot_if_user_id = str(pilot_if_user_id)
        self.routes_to_assign = list(routes_to_assign)
        self.claimed = False

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if str(interaction.user.id) != str(self.pilot.get("discordid")):
            await interaction.response.send_message(
                "Only the pilot who requested this flight can confirm it.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Confirm & Assign All Listed Routes",
        style=discord.ButtonStyle.success,
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if self.claimed:
            await interaction.response.send_message(
                "This request has already been processed.",
                ephemeral=True,
            )
            return

        self.claimed = True

        for child in self.children:
            child.disabled = True

        # Acknowledge the click before making API calls.
        await interaction.response.edit_message(view=self)

        assigned = []
        failed_route = None
        registration = str(
            _get(self.aircraft, "registration", "Aircraft")
        )

        try:
            manager = getattr(self.cog.bot, "if_live_manager", None)

            if manager is None:
                raise RuntimeError(
                    "Infinite Flight Live manager is not initialized"
                )

            async with await manager.get_client(TOKEN_OWNER_ID) as client:
                aircraft_id = str(_get(self.aircraft, "id"))

                schedules = _active_schedules(
                    await client.list_aircraft_schedules(aircraft_id)
                )

                if not schedules:
                    await interaction.edit_original_response(
                        content=(
                            f"{registration} does not have any active "
                            "scheduled routes. Please contact a live admin."
                        ),
                        embed=None,
                        view=None,
                    )
                    return

                claim_index = _next_claimable_index(schedules)

                if claim_index is None:
                    await interaction.edit_original_response(
                        content=(
                            f"{registration} has no unassigned active routes. "
                            "Please check again later or contact a live admin."
                        ),
                        embed=None,
                        view=None,
                    )
                    return

                top = schedules[claim_index]

                if str(_get(top, "id")) != str(_get(self.schedule, "id")):
                    await interaction.edit_original_response(
                        content=(
                            "The next available route changed. Nothing was "
                            "assigned; run `/request-flight` again to review "
                            "the current route chain."
                        ),
                        embed=None,
                        view=None,
                    )
                    return

                preview_ids = _route_ids(self.routes_to_assign)
                current_routes = _build_connected_route_chain(
                    schedules,
                    claim_index,
                )

                if _route_ids(current_routes) != preview_ids:
                    await interaction.edit_original_response(
                        content=(
                            "⚠️ The available route chain changed since you "
                            "opened this confirmation. Nothing was assigned. "
                            "Run `/request-flight` again to review the updated "
                            "routes."
                        ),
                        embed=None,
                        view=None,
                    )
                    return

                top_crew = _get(top, "crew", []) or []

                if any(
                    _crew_user_id(member) == self.pilot_if_user_id
                    for member in top_crew
                ):
                    await interaction.edit_original_response(
                        content=(
                            "You are already assigned to this route. "
                            "Nothing was changed."
                        ),
                        embed=None,
                        view=None,
                    )
                    return

                if top_crew:
                    await interaction.edit_original_response(
                        content=(
                            "The first route has already been assigned. "
                            "Nothing was changed."
                        ),
                        embed=None,
                        view=None,
                    )
                    return

                for route in current_routes:
                    failed_route = route

                    # Refresh before each assignment to detect changes.
                    refreshed = _active_schedules(
                        await client.list_aircraft_schedules(aircraft_id)
                    )

                    current = next(
                        (
                            item for item in refreshed
                            if str(_get(item, "id"))
                            == str(_get(route, "id"))
                        ),
                        None,
                    )

                    if current is None or _is_unavailable_status(current):
                        break

                    if _get(current, "crew", []) or []:
                        break

                    if assigned and not _connected_routes(assigned[-1], current):
                        break

                    # Never assign a route after the return-to-hub route.
                    if assigned and str(
                        _get(assigned[-1], "destination_icao", "") or ""
                    ).strip().upper() == HUB_ICAO:
                        break

                    updated = await client.assign_crew(
                        str(_get(current, "id")),
                        self.pilot_if_user_id,
                        0,
                    )

                    updated_crew = _get(updated, "crew", []) or []

                    if not any(
                        _crew_user_id(member) == self.pilot_if_user_id
                        for member in updated_crew
                    ):
                        refreshed_after = _active_schedules(
                            await client.list_aircraft_schedules(aircraft_id)
                        )

                        match = next(
                            (
                                item for item in refreshed_after
                                if str(_get(item, "id"))
                                == str(_get(current, "id"))
                            ),
                            None,
                        )

                        updated_crew = (
                            _get(match, "crew", []) or []
                            if match is not None
                            else []
                        )

                    if not any(
                        _crew_user_id(member) == self.pilot_if_user_id
                        for member in updated_crew
                    ):
                        break

                    assigned.append(current)
                    failed_route = None

                    # Include the route arriving at OTHH, then stop.
                    if str(
                        _get(current, "destination_icao", "") or ""
                    ).strip().upper() == HUB_ICAO:
                        break

            if not assigned:
                await interaction.edit_original_response(
                    content=(
                        "I couldn't verify any route assignments. "
                        "Please contact staff before trying again."
                    ),
                    embed=None,
                    view=None,
                )
                return

            route_lines = "\n".join(
                _route_text(route) for route in assigned
            )

            extra = (
                f"\n🔁 **{len(assigned)} connected routes assigned.**"
                if len(assigned) > 1
                else ""
            )

            public_channel = interaction.channel

            if public_channel is None:
                await interaction.edit_original_response(
                    content=(
                        f"{len(assigned)} assignment(s) were verified, but "
                        "I couldn't access this channel to post the "
                        "confirmation. Please contact staff."
                    ),
                    embed=None,
                    view=None,
                )
                return

            pilot_name = self.pilot.get(
                "callsign",
                interaction.user.display_name,
            )

            await public_channel.send(
                f"✅ **Flight assigned to you, <@{interaction.user.id}>!**\n"
                f"Pilot: **{pilot_name}**\n"
                f"Aircraft: **{registration}** "
                f"({_model_label(self.aircraft)})\n"
                f"Route(s):\n{route_lines}{extra}\n\n"
                f"{RULES}",
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                ),
            )

            if failed_route is not None:
                result_text = (
                    f"⚠️ {len(assigned)} route(s) were assigned and "
                    "verified, but the next route could not be confirmed. "
                    "Check the public message and contact staff before "
                    "retrying."
                )
            else:
                result_text = (
                    f"✅ All {len(assigned)} previewed route(s) were "
                    "verified and the confirmation was posted."
                )

            await interaction.edit_original_response(
                content=result_text,
                embed=None,
                view=None,
            )

        except Exception:
            log.exception(
                "Failed to assign Infinite Flight schedule crew"
            )

            try:
                if assigned:
                    message = (
                        f"⚠️ The API confirmed {len(assigned)} route(s), "
                        "but an error occurred before the process finished. "
                        "Check the schedule and contact staff before retrying."
                    )
                else:
                    message = (
                        "There was an error while assigning the flight. "
                        "Please contact staff before retrying."
                    )

                await interaction.edit_original_response(
                    content=message,
                    embed=None,
                    view=None,
                )
            except Exception:
                log.exception(
                    "Could not update the flight assignment response"
                )

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
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


class RequestFlightCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._aircraft_cache = None
        self._aircraft_cache_at = 0.0
        self._aircraft_cache_lock = asyncio.Lock()

    @app_commands.command(
        name="request-flight",
        description="Request assignment to an aircraft's scheduled route chain",
    )
    async def request_flight(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        if not ORG_ID:
            await interaction.followup.send(
                "The bot is missing `IF_ORGANIZATION_ID` in its environment.",
                ephemeral=True,
            )
            return

        pilots_model = getattr(self.bot, "pilots_model", None)

        if pilots_model is None:
            await interaction.followup.send(
                "Pilot database is not available right now. Please contact staff.",
                ephemeral=True,
            )
            return

        try:
            pilot = await pilots_model.get_pilot_by_discord_id(
                str(interaction.user.id)
            )

            if not pilot:
                await interaction.followup.send(
                    "I couldn't find an active pilot profile linked to your Discord account.",
                    ephemeral=True,
                )
                return

            if not pilot.get("ifuserid"):
                await interaction.followup.send(
                    "Your pilot profile doesn't have an Infinite Flight user ID linked.",
                    ephemeral=True,
                )
                return

            manager = getattr(self.bot, "if_live_manager", None)

            if manager is None:
                await interaction.followup.send(
                    "Infinite Flight Live isn't initialized. Please contact staff.",
                    ephemeral=True,
                )
                return

            now = time.monotonic()

            if (
                self._aircraft_cache is None
                or now - self._aircraft_cache_at > FLEET_CACHE_SECONDS
            ):
                async with self._aircraft_cache_lock:
                    now = time.monotonic()

                    if (
                        self._aircraft_cache is None
                        or now - self._aircraft_cache_at > FLEET_CACHE_SECONDS
                    ):
                        async with await manager.get_client(
                            TOKEN_OWNER_ID
                        ) as client:
                            fleet = await client.list_organization_aircraft(
                                ORG_ID
                            )

                        self._aircraft_cache = list(fleet or [])
                        self._aircraft_cache_at = time.monotonic()

            aircraft = sorted(
                self._aircraft_cache or [],
                key=lambda item: str(_get(item, "registration", "")),
            )

            retired_registrations = {"A7BFC", "A7BFA"}

            aircraft = [
                item for item in aircraft
                if str(
                    _get(item, "registration", "")
                ).strip().upper() not in retired_registrations
            ]

            if not aircraft:
                await interaction.followup.send(
                    "No aircraft were returned for Qatari Virtual. "
                    "Check the API account's organization access.",
                    ephemeral=True,
                )
                return

            counts = {
                name: sum(
                    _aircraft_category(item) == name
                    for item in aircraft
                )
                for name in ("Airbus", "Boeing", "Other")
            }

            await interaction.followup.send(
                f"Choose an aircraft category "
                f"({len(aircraft)} aircraft available): "
                f"Airbus {counts['Airbus']} · "
                f"Boeing {counts['Boeing']} · "
                f"Other {counts['Other']}.",
                view=AircraftSelectView(self, pilot, aircraft),
                ephemeral=True,
            )

        except Exception:
            log.exception(
                "Could not load aircraft for /request-flight"
            )

            await interaction.followup.send(
                "I couldn't load Qatari Virtual's aircraft from Infinite Flight. "
                "Please contact staff; no assignment was made.",
                ephemeral=True,
            )

    async def show_aircraft_route(
        self,
        interaction,
        pilot,
        aircraft,
    ):
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

            async with await manager.get_client(TOKEN_OWNER_ID) as client:
                aircraft_id = str(_get(aircraft, "id"))

                position, raw_schedules = await asyncio.gather(
                    client.get_aircraft_position(aircraft_id),
                    client.list_aircraft_schedules(aircraft_id),
                )

                schedules = _active_schedules(raw_schedules)

            registration = _get(
                aircraft, "registration", "Aircraft"
            )

            if not schedules:
                await interaction.edit_original_response(
                    content=(
                        f"{registration} does not have any active scheduled "
                        "routes. Please ask a live admin to check the schedule."
                    ),
                    embed=None,
                    view=None,
                )
                return

            claim_index = _next_claimable_index(schedules)

            if claim_index is None:
                await interaction.edit_original_response(
                    content=(
                        f"{registration} has no unassigned active routes. "
                        "Please check again later or contact a live admin."
                    ),
                    embed=None,
                    view=None,
                )
                return

            top = schedules[claim_index]
            assigned_before = schedules[:claim_index]

            routes_to_assign = _build_connected_route_chain(
                schedules,
                claim_index,
            )

            if not routes_to_assign:
                await interaction.edit_original_response(
                    content=(
                        "I couldn't find a valid connected route chain. "
                        "Please contact staff."
                    ),
                    embed=None,
                    view=None,
                )
                return

            ground = _get(position, "is_on_ground", None)

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

            route_lines = "\n".join(
                f"**{index}.** {_route_text(route)}"
                for index, route in enumerate(routes_to_assign, start=1)
            )

            embed = discord.Embed(
                title=f"Confirm flight request · {registration}",
                description=(
                    f"**Aircraft model:** {_model_label(aircraft)}\n"
                    f"**Registration:** {registration}\n"
                    f"**Routes to be assigned:** {len(routes_to_assign)}\n\n"
                    f"**Proposed route chain**\n{route_lines}\n\n"
                    "Confirm only if you want every route listed above "
                    "assigned to you."
                ),
                color=discord.Color.blue(),
            )

            embed.add_field(
                name="Aircraft status",
                value=status,
                inline=False,
            )

            embed.add_field(
                name="Flight rules",
                value=(
                    "• 6 hours to take off from a hub\n"
                    "• 12 hours to take off from a non-hub\n"
                    "• 24 hours to land if stopped in the air"
                ),
                inline=False,
            )

            if assigned_before:
                embed.add_field(
                    name="Earlier routes",
                    value=(
                        f"{len(assigned_before)} earlier route(s) are "
                        "already assigned. The chain above starts at the "
                        "first unassigned route."
                    ),
                    inline=False,
                )

            state = _get(position, "state", None)

            try:
                in_flight = int(state) == 2
            except (TypeError, ValueError):
                in_flight = str(state).lower() in {
                    "in_flight",
                    "in flight",
                }

            if in_flight:
                embed.set_footer(
                    text=(
                        "This request assigns scheduled routes; it does "
                        "not change the aircraft's current flight."
                    )
                )
            else:
                embed.set_footer(
                    text=(
                        "The route chain will be checked again when you confirm."
                    )
                )

            view = ClaimRouteView(
                self,
                pilot,
                aircraft,
                top,
                pilot["ifuserid"],
                routes_to_assign,
            )

            await interaction.edit_original_response(
                content="Review the proposed route chain before confirming:",
                embed=embed,
                view=view,
            )

        except Exception:
            log.exception(
                "Could not inspect selected aircraft schedule"
            )

            await interaction.edit_original_response(
                content=(
                    "I couldn't verify the aircraft or its route chain. "
                    "Nothing was assigned; please contact staff."
                ),
                embed=None,
                view=None,
            )


async def setup(bot):
    await bot.add_cog(RequestFlightCog(bot))

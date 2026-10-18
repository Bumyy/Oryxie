
"""Discord command for requesting the top scheduled route on Qatari Virtual aircraft."""

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

# Fleet registrations change rarely; keep this cache warm to avoid a Live API call
# every time someone opens the command.
FLEET_CACHE_SECONDS = 300

# Retired aircraft are excluded from all request-flight workflows.
RETIRED_AIRCRAFT_REGISTRATIONS = {"A7BFC", "A7BFA"}

# Verified registration -> aircraft model mapping supplied by Qatari Virtual.
# Add new active aircraft here when the fleet changes.
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


def _normalized_registration(aircraft):
    registration = str(_get(aircraft, "registration", "") or "")
    return registration.strip().upper().replace("-", "")


def _is_retired_aircraft(aircraft):
    return _normalized_registration(aircraft) in RETIRED_AIRCRAFT_REGISTRATIONS


def _crew_user_id(member):
    for key in ("userId", "user_id", "ifuserid", "id"):
        value = _get(member, key)
        if value not in (None, ""):
            return str(value)
    return ""


def _aircraft_model(aircraft):
    for key in (
        "model", "aircraft_name", "aircraftName", "name",
        "title", "type_name", "typeName",
    ):
        value = _get(aircraft, key)
        if value and str(value).strip():
            return str(value).strip()

    registration = str(_get(aircraft, "registration", "") or "").strip().upper()
    return AIRCRAFT_MODELS.get(registration, "")


def _aircraft_category(aircraft):
    model = _aircraft_model(aircraft).lower().replace(" ", "")
    if model.startswith("a") and len(model) > 1 and model[1].isdigit():
        return "Airbus"
    if model.startswith(("777", "787", "737", "747", "757", "767", "717", "727")):
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
    # Infinite Flight status values used here: CANCELLED=9, ARRIVED=11.
    return _route_status_number(schedule) in {9, 11}


def _active_schedules(schedules):
    """Deduplicate repeated schedule IDs, then remove cancelled/arrived history rows.

    Some Live API responses can repeat the same schedule ID with different status
    snapshots. A terminal status wins so a completed route is not accidentally
    resurrected by an older scheduled snapshot.
    """
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
            # Prefer the snapshot with crew data, otherwise keep the earlier row.
            if not (_get(previous, "crew", []) or []) and (_get(route, "crew", []) or []):
                by_id[key] = route

    unique = list(by_id.values()) + no_id
    return [
        route for route in _sorted_schedules(unique)
        if not _is_unavailable_status(route)
    ]


def _next_claimable_index(schedules):
    """Find the first unassigned route after any leading routes already crewed."""
    for index, route in enumerate(schedules):
        if not (_get(route, "crew", []) or []):
            return index
    return None


def _reciprocal_routes(first, second):
    """True when second is the immediately following reciprocal route."""
    first_origin = str(_get(first, "origin_icao", "") or "").upper()
    first_dest = str(_get(first, "destination_icao", "") or "").upper()
    second_origin = str(_get(second, "origin_icao", "") or "").upper()
    second_dest = str(_get(second, "destination_icao", "") or "").upper()

    return bool(
        first_origin and first_dest and second_origin and second_dest
        and first_origin == second_dest
        and first_dest == second_origin
    )


class AircraftCategorySelect(discord.ui.Select):
    def __init__(self, cog, pilot, aircraft):
        self.cog, self.pilot, self.aircraft = cog, pilot, aircraft

        categories = [
            name for name in ("Airbus", "Boeing", "Other")
            if any(_aircraft_category(a) == name for a in aircraft)
        ]

        options = [
            discord.SelectOption(
                label=name,
                description=f"{sum(_aircraft_category(a) == name for a in aircraft)} aircraft",
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
            aircraft for aircraft in self.aircraft
            if _aircraft_category(aircraft) == category
            and not _is_retired_aircraft(aircraft)
        ]

        await interaction.response.edit_message(
            content=f"**{category} aircraft** — choose a registration. The model is shown in each option.",
            view=AircraftListView(self.cog, self.pilot, selected, category, 0),
        )


class AircraftListSelect(discord.ui.Select):
    def __init__(self, cog, pilot, aircraft, category, page):
        self.cog, self.pilot, self.aircraft, self.category, self.page = (
            cog, pilot, aircraft, category, page
        )

        start = page * 25
        page_aircraft = aircraft[start:start + 25]
        options = []

        for item in page_aircraft:
            registration = str(_get(item, "registration") or "Unknown registration")
            options.append(
                discord.SelectOption(
                    label=registration[:100],
                    description=_model_label(item)[:100],
                    value=str(_get(item, "id")),
                )
            )

        pages = max(1, (len(aircraft) + 24) // 25)

        super().__init__(
            placeholder=f"Choose {category} registration (page {page + 1}/{pages})…",
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

        if aircraft is None or _is_retired_aircraft(aircraft):
            await interaction.response.send_message(
                "That aircraft is unavailable or has been retired. Run `/request-flight` again.",
                ephemeral=True,
            )
            return

        await self.cog.show_aircraft_route(interaction, self.pilot, aircraft)


class AircraftListView(discord.ui.View):
    def __init__(self, cog, pilot, aircraft, category, page=0):
        super().__init__(timeout=180)

        self.add_item(AircraftListSelect(cog, pilot, aircraft, category, page))

        if page > 0:
            self.add_item(
                AircraftPageButton(cog, pilot, aircraft, category, page - 1, "Previous")
            )

        if (page + 1) * 25 < len(aircraft):
            self.add_item(
                AircraftPageButton(cog, pilot, aircraft, category, page + 1, "Next")
            )

        self.add_item(
            AircraftPageButton(
                cog, pilot, aircraft, category, 0, "Back to categories", back=True
            )
        )


class AircraftPageButton(discord.ui.Button):
    def __init__(self, cog, pilot, aircraft, category, page, label, back=False):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.cog, self.pilot, self.aircraft, self.category, self.page, self.back = (
            cog, pilot, aircraft, category, page, back
        )

    async def callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != str(self.pilot.get("discordid")):
            await interaction.response.send_message(
                "Only the pilot who requested this list can use it.",
                ephemeral=True,
            )
            return

        if self.back:
            active_aircraft = [
                aircraft for aircraft in (self.cog._aircraft_cache or self.aircraft)
                if not _is_retired_aircraft(aircraft)
            ]

            await interaction.response.edit_message(
                content="Choose an aircraft category:",
                view=AircraftSelectView(self.cog, self.pilot, active_aircraft),
            )
        else:
            await interaction.response.edit_message(
                content=f"**{self.category} aircraft** — choose a registration:",
                view=AircraftListView(
                    self.cog, self.pilot, self.aircraft, self.category, self.page
                ),
            )


class AircraftSelectView(discord.ui.View):
    def __init__(self, cog, pilot, aircraft):
        super().__init__(timeout=180)
        active_aircraft = [
            item for item in aircraft if not _is_retired_aircraft(item)
        ]
        if active_aircraft:
            self.add_item(AircraftCategorySelect(cog, pilot, active_aircraft))


class ClaimRouteView(discord.ui.View):
    def __init__(self, cog, pilot, aircraft, schedule, pilot_if_user_id):
        super().__init__(timeout=120)
        self.cog = cog
        self.pilot = pilot
        self.aircraft = aircraft
        self.schedule = schedule
        self.pilot_if_user_id = str(pilot_if_user_id)
        self.claimed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != str(self.pilot.get("discordid")):
            await interaction.response.send_message(
                "Only the pilot who requested this flight can confirm it.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Confirm & Assign Flight", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.claimed:
            await interaction.response.send_message(
                "This request has already been processed.",
                ephemeral=True,
            )
            return

        self.claimed = True

        if _is_retired_aircraft(self.aircraft):
            for child in self.children:
                child.disabled = True

            await interaction.response.edit_message(
                content="This aircraft has been retired from the request-flight system. No assignment was made.",
                embed=None,
                view=self,
            )
            return

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(view=self)

        try:
            manager = getattr(self.cog.bot, "if_live_manager", None)
            if manager is None:
                raise RuntimeError("Infinite Flight Live manager is not initialized")

            async with await manager.get_client(TOKEN_OWNER_ID) as client:
                aircraft_id = str(_get(self.aircraft, "id"))
                schedules = _active_schedules(
                    await client.list_aircraft_schedules(aircraft_id)
                )
                registration = _get(self.aircraft, "registration", "Aircraft")

                if not schedules:
                    await interaction.edit_original_response(
                        content=f"{registration} does not have any scheduled routes at the moment. Please ask a live admin to check the schedule.",
                        embed=None,
                        view=None,
                    )
                    return

                claim_index = _next_claimable_index(schedules)

                if claim_index is None:
                    await interaction.edit_original_response(
                        content=f"{registration} has no unassigned active routes right now. Please check again later or ask a live admin.",
                        embed=None,
                        view=None,
                    )
                    return

                top = schedules[claim_index]

                if str(_get(top, "id")) != str(_get(self.schedule, "id")):
                    await interaction.edit_original_response(
                        content="The next available route changed since you opened this request. Nothing was assigned; run `/request-flight` again.",
                        embed=None,
                        view=None,
                    )
                    return

                crew = _get(top, "crew", []) or []

                if any(
                    _crew_user_id(member) == self.pilot_if_user_id
                    for member in crew
                ):
                    await interaction.edit_original_response(
                        content="You are already assigned to this route. Nothing was changed.",
                        embed=None,
                        view=None,
                    )
                    return

                if crew:
                    await interaction.edit_original_response(
                        content="This route has been assigned to another crew member. Nothing was changed.",
                        embed=None,
                        view=None,
                    )
                    return

                # All active aircraft use the same route-chain assignment logic.
                # Assign consecutive unassigned legs until reaching OTHH.
                # Never skip a leg or cross a gap.
                routes_to_assign = [top]
                current_destination = str(
                    _get(top, "destination_icao", "") or ""
                ).upper()
                next_index = claim_index + 1

                while current_destination != "OTHH" and next_index < len(schedules):
                    next_route = schedules[next_index]
                    next_origin = str(
                        _get(next_route, "origin_icao", "") or ""
                    ).upper()

                    if (
                        next_origin != current_destination
                        or (_get(next_route, "crew", []) or [])
                    ):
                        break

                    routes_to_assign.append(next_route)
                    current_destination = str(
                        _get(next_route, "destination_icao", "") or ""
                    ).upper()
                    next_index += 1

                assigned = []

                for route in routes_to_assign:
                    updated = await client.assign_crew(
                        str(_get(route, "id")), self.pilot_if_user_id, 0
                    )
                    updated_crew = _get(updated, "crew", []) or []

                    if not any(
                        _crew_user_id(member) == self.pilot_if_user_id
                        for member in updated_crew
                    ):
                        refreshed = _active_schedules(
                            await client.list_aircraft_schedules(aircraft_id)
                        )
                        match = next(
                            (
                                schedule for schedule in refreshed
                                if str(_get(schedule, "id")) == str(_get(route, "id"))
                            ),
                            None,
                        )
                        updated_crew = (
                            _get(match, "crew", []) or [] if match else []
                        )

                    if not any(
                        _crew_user_id(member) == self.pilot_if_user_id
                        for member in updated_crew
                    ):
                        detail = "The API did not confirm your assignment."

                        if assigned:
                            detail += (
                                f" {len(assigned)} earlier leg(s) were assigned, "
                                "but the next leg could not be verified; contact staff immediately."
                            )
                        else:
                            detail += (
                                " Nothing is being reported as successful; contact staff."
                            )

                        await interaction.edit_original_response(
                            content=detail,
                            embed=None,
                            view=None,
                        )
                        return

                    assigned.append(route)

                public_channel = interaction.channel

                if public_channel is None:
                    await interaction.edit_original_response(
                        content="Assignment verified, but I couldn't access this channel to post the public confirmation. Please contact staff.",
                        embed=None,
                        view=None,
                    )
                    return

                route_lines = "\n".join(_route_text(route) for route in assigned)

                extra = (
                    f"\nI’ve assigned the consecutive route chain through "
                    f"{str(_get(assigned[-1], 'destination_icao', '') or 'the final stop').upper()}."
                    if len(assigned) > 1 else ""
                )

                await public_channel.send(
                    f"✅ **Flight assignment confirmed**\n"
                    f"Pilot: **{self.pilot.get('callsign', interaction.user.display_name)}**\n"
                    f"Aircraft: **{registration}** ({_model_label(self.aircraft)})\n"
                    f"Route(s):\n{route_lines}{extra}\n\n{RULES}"
                )

                await interaction.edit_original_response(
                    content="Your assignment was verified and the confirmation was posted in this channel.",
                    embed=None,
                    view=None,
                )

        except Exception:
            log.exception("Failed to assign Infinite Flight schedule crew")
            await interaction.edit_original_response(
                content="❌ I couldn't verify the assignment with Infinite Flight. Please contact staff and don't retry until they check the schedule.",
                embed=None,
                view=None,
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content="Request cancelled. No assignment was made.",
            view=self,
        )


class RequestFlightCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._aircraft_cache = None
        self._aircraft_cache_at = 0.0
        self._aircraft_cache_lock = asyncio.Lock()

    async def _get_aircraft(self, client):
        """Return the cached active organization fleet, refreshing it when stale."""
        now = time.monotonic()

        if (
            self._aircraft_cache is not None
            and now - self._aircraft_cache_at < FLEET_CACHE_SECONDS
        ):
            return self._aircraft_cache

        async with self._aircraft_cache_lock:
            now = time.monotonic()

            if (
                self._aircraft_cache is not None
                and now - self._aircraft_cache_at < FLEET_CACHE_SECONDS
            ):
                return self._aircraft_cache

            fleet = await client.list_organization_aircraft(ORG_ID)
            self._aircraft_cache = [
                aircraft for aircraft in (fleet or [])
                if not _is_retired_aircraft(aircraft)
            ]
            self._aircraft_cache_at = time.monotonic()

            return self._aircraft_cache

    @app_commands.command(
        name="request-flight",
        description="Request assignment to an aircraft's top scheduled route",
    )
    async def request_flight(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

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
            pilot = await pilots_model.get_pilot_by_discord_id(str(interaction.user.id))

            if not pilot:
                await interaction.followup.send(
                    "I couldn't find an active pilot profile linked to your Discord account. Please contact staff.",
                    ephemeral=True,
                )
                return

            if not pilot.get("ifuserid"):
                await interaction.followup.send(
                    "Your pilot profile doesn't have an Infinite Flight user ID linked. Please contact staff.",
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
                # Only one command refreshes the fleet at a time.
                # Other callers reuse the newly populated cache.
                async with self._aircraft_cache_lock:
                    now = time.monotonic()

                    if (
                        self._aircraft_cache is None
                        or now - self._aircraft_cache_at > FLEET_CACHE_SECONDS
                    ):
                        async with await manager.get_client(TOKEN_OWNER_ID) as client:
                            fleet = await client.list_organization_aircraft(ORG_ID)

                        self._aircraft_cache = [
                            aircraft for aircraft in (fleet or [])
                            if not _is_retired_aircraft(aircraft)
                        ]
                        self._aircraft_cache_at = time.monotonic()

            aircraft = sorted(
                [
                    item for item in (self._aircraft_cache or [])
                    if not _is_retired_aircraft(item)
                ],
                key=lambda item: str(_get(item, "registration", "")),
            )

            if not aircraft:
                await interaction.followup.send(
                    "No active aircraft were returned for Qatari Virtual. Check the API account's organization access and fleet status.",
                    ephemeral=True,
                )
                return

            counts = {
                name: sum(_aircraft_category(item) == name for item in aircraft)
                for name in ("Airbus", "Boeing", "Other")
            }

            await interaction.followup.send(
                f"Choose an aircraft category ({len(aircraft)} aircraft available): "
                f"Airbus {counts['Airbus']} · Boeing {counts['Boeing']} · Other {counts['Other']}.",
                view=AircraftSelectView(self, pilot, aircraft),
                ephemeral=True,
            )

        except Exception:
            log.exception("Could not load aircraft for /request-flight")
            await interaction.followup.send(
                "I couldn't load Qatari Virtual's aircraft from Infinite Flight. Please contact staff; no assignment was made.",
                ephemeral=True,
            )

    async def show_aircraft_route(self, interaction, pilot, aircraft):
        # Guard against old menus or stale component interactions.
        if _is_retired_aircraft(aircraft):
            await interaction.response.send_message(
                "This aircraft has been retired from the request-flight system. Run `/request-flight` again and choose an active aircraft.",
                ephemeral=True,
            )
            return

        # Edit the existing ephemeral menu instead of creating a new message.
        await interaction.response.defer(ephemeral=True, thinking=False)

        try:
            manager = getattr(self.bot, "if_live_manager", None)

            if manager is None:
                raise RuntimeError("Infinite Flight Live manager is not initialized")

            async with await manager.get_client(TOKEN_OWNER_ID) as client:
                aircraft_id = str(_get(aircraft, "id"))

                position, raw_schedules = await asyncio.gather(
                    client.get_aircraft_position(aircraft_id),
                    client.list_aircraft_schedules(aircraft_id),
                )

                schedules = _active_schedules(raw_schedules)

            registration = _get(aircraft, "registration", "Aircraft")

            if not schedules:
                await interaction.edit_original_response(
                    content=f"{registration} does not have any scheduled routes at the moment. Please ask a live admin to check the schedule.",
                    embed=None,
                    view=None,
                )
                return

            claim_index = _next_claimable_index(schedules)

            if claim_index is None:
                await interaction.edit_original_response(
                    content=f"{registration} has no unassigned active routes right now. Please check again later or ask a live admin.",
                    embed=None,
                    view=None,
                )
                return

            top = schedules[claim_index]
            crew = _get(top, "crew", []) or []
            route = _route_text(top)
            assigned_before = schedules[:claim_index]

            ground = _get(position, "is_on_ground", None)
            status = f"Aircraft state: **{_state_label(position)}** · " + (
                "On ground" if ground is True
                else "In flight" if ground is False
                else "Ground status unknown"
            )

            embed = discord.Embed(
                title=f"Request flight · {registration}",
                description=route,
                color=discord.Color.orange() if crew else discord.Color.blue(),
            )

            embed.add_field(
                name="Aircraft model",
                value=_model_label(aircraft),
                inline=False,
            )
            embed.add_field(name="Aircraft status", value=status, inline=False)
            embed.add_field(
                name="Available schedule",
                value=(
                    f"{sum(1 for item in schedules if not (_get(item, 'crew', []) or []))} "
                    f"unassigned · {len(schedules)} active route(s)."
                ),
                inline=False,
            )

            if assigned_before:
                assigned_count = len(assigned_before)
                embed.add_field(
                    name="Route availability",
                    value=(
                        f"{assigned_count} earlier route(s) are already assigned. "
                        "The route shown above is the next unassigned route in schedule order. "
                        "Infinite Flight may take a little time to confirm the assignment."
                    ),
                    inline=False,
                )

            state = _get(position, "state", None)

            try:
                in_flight = int(state) == 2
            except (TypeError, ValueError):
                in_flight = str(state).lower() in {"in_flight", "in flight"}

            embed.add_field(
                name="Before confirming",
                value=(
                    "You agree to fly routes in order and follow QV take-off/landing "
                    "deadlines (6h at hubs, 12h elsewhere, 24h if stopped in the air)."
                ),
                inline=False,
            )

            if in_flight:
                embed.set_footer(
                    text="The request assigns the next route; it does not change the aircraft's current flight."
                )
            else:
                embed.set_footer(text="Confirm only if you agree to the flight rules.")

            view = ClaimRouteView(self, pilot, aircraft, top, pilot["ifuserid"])

            await interaction.edit_original_response(
                content=None,
                embed=embed,
                view=view,
            )

        except Exception:
            log.exception("Could not inspect selected aircraft schedule")
            await interaction.edit_original_response(
                content="I couldn't verify the aircraft or its top route. Nothing was assigned; please contact staff.",
                embed=None,
                view=None,
            )


async def setup(bot):
    await bot.add_cog(RequestFlightCog(bot))

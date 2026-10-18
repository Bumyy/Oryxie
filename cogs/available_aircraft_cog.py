
"""Public command showing Qatari Virtual aircraft with claimable scheduled routes in Live Mode."""

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
SCHEDULE_CACHE_SECONDS = 300
PAGE_SIZE = 10
SCAN_CONCURRENCY = 1
MAX_SCHEDULE_FETCHES_PER_SCAN = 5
VIEW_TIMEOUT_SECONDS = 300

# Queue color thresholds based on assigned flights before the next
# unassigned route.
GREEN_MAX_FLIGHTS_AHEAD = 0
YELLOW_MAX_FLIGHTS_AHEAD = 2

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


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


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
    """Remove duplicate IDs and completed or cancelled routes."""
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

        previous_terminal = _is_unavailable_status(previous)
        current_terminal = _is_unavailable_status(route)

        if current_terminal and not previous_terminal:
            by_id[key] = route
        elif current_terminal == previous_terminal:
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
    """Find the first active route without assigned crew."""
    for index, route in enumerate(schedules):
        if not (_get(route, "crew", []) or []):
            return index

    return None


def _queue_color(assigned_before):
    """Return an embed color based on assigned flights ahead in the queue."""
    if assigned_before <= GREEN_MAX_FLIGHTS_AHEAD:
        return discord.Color.green()

    if assigned_before <= YELLOW_MAX_FLIGHTS_AHEAD:
        return discord.Color.yellow()

    return discord.Color.red()


def _queue_emoji(assigned_before):
    """Return the matching queue indicator."""
    if assigned_before <= GREEN_MAX_FLIGHTS_AHEAD:
        return "🟢"

    if assigned_before <= YELLOW_MAX_FLIGHTS_AHEAD:
        return "🟡"

    return "🔴"


class AvailabilityView(discord.ui.View):
    """Public, paginated aircraft availability list."""

    def __init__(self, cog, rows, failed_count=0, page=0):
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)

        self.cog = cog
        self.rows = rows
        self.failed_count = failed_count
        self.page = page

        self._update_buttons()

    def _update_buttons(self):
        self.clear_items()

        if len(self.rows) > PAGE_SIZE:
            previous = discord.ui.Button(
                label="Previous",
                style=discord.ButtonStyle.secondary,
                disabled=self.page <= 0,
            )
            previous.callback = self.previous_page
            self.add_item(previous)

            next_button = discord.ui.Button(
                label="Next",
                style=discord.ButtonStyle.secondary,
                disabled=(self.page + 1) * PAGE_SIZE >= len(self.rows),
            )
            next_button.callback = self.next_page
            self.add_item(next_button)

        refresh = discord.ui.Button(
            label="Refresh",
            style=discord.ButtonStyle.primary,
            emoji="🔄",
        )
        refresh.callback = self.refresh_list
        self.add_item(refresh)

    def make_embed(self):
        total_pages = max(
            1,
            (len(self.rows) + PAGE_SIZE - 1) // PAGE_SIZE,
        )

        embed = discord.Embed(
            title="Qatari Virtual · Aircraft Availability",
            description=(
                "Public live-schedule snapshot. **Available** means the next "
                "unassigned active route can be requested.\n"
                "This command only checks schedules; it never assigns a flight.\n\n"
                "**Queue indicators — assigned flights ahead of your turn:**\n"
                "🟢 **Green:** 0 flights ahead — next route is ready to claim.\n"
                "🟡 **Yellow:** 1–2 flights ahead.\n"
                "🔴 **Red:** 3 or more flights ahead.\n\n"
                "The queue indicator is based on assigned routes before the "
                "next unassigned route, not a guaranteed waiting time.\n"
                "Use **Refresh** to check updated availability."
                "⚠️ This list and its buttons stop responding after 5 minutes. Run /available-aircraft again to check availability."
            ),
            color=discord.Color.teal(),
        )

        page_rows = self.rows[
            self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE
        ]

        if not page_rows:
            embed.add_field(
                name="No claimable routes found",
                value=(
                    "No available routes were found in the latest scan. "
                    "Try refreshing or run this command again later."
                ),
                inline=False,
            )
        else:
            for row in page_rows:
                registration, model, route, active_count, assigned_count = row

                details = (
                    f"**Next route:** {route}\n"
                    f"**Active routes:** {active_count}\n"
                    f"**Assigned flights ahead:** {assigned_count}"
                )

                embed.add_field(
                    name=(
                        f"{_queue_emoji(assigned_count)} "
                        f"{registration} · {model}"
                    ),
                    value=details,
                    inline=False,
                )

        if self.failed_count:
            embed.add_field(
                name="Some aircraft could not be checked",
                value=(
                    f"{self.failed_count} aircraft could not be refreshed "
                    "because of an API error or rate limit. Cached results "
                    "are retained where available; try Refresh again later."
                ),
                inline=False,
            )

        embed.set_footer(
            text=(
                f"Page {self.page + 1}/{total_pages} · "
                "Refresh checks a rotating batch; cached results are retained "
                "during API limits."
            )
        )

        return embed

    async def _edit(self, interaction):
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.make_embed(),
            view=self,
        )

    async def previous_page(self, interaction):
        self.page = max(0, self.page - 1)
        await self._edit(interaction)

    async def next_page(self, interaction):
        max_page = max(0, (len(self.rows) - 1) // PAGE_SIZE)
        self.page = min(max_page, self.page + 1)
        await self._edit(interaction)

    async def refresh_list(self, interaction):
        await interaction.response.defer()

        try:
            rows, failed_count = await self.cog.collect_availability(
                force_refresh=False
            )

            self.rows = rows
            self.failed_count = failed_count
            self.page = min(
                self.page,
                max(0, (len(rows) - 1) // PAGE_SIZE),
            )

            self._update_buttons()

            await interaction.edit_original_response(
                embed=self.make_embed(),
                view=self,
            )

        except Exception:
            log.exception("Failed to refresh public aircraft availability")

            await interaction.followup.send(
                "I couldn't refresh the aircraft list right now. "
                "Please try again shortly.",
                ephemeral=True,
            )


class AvailableAircraftCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._fleet = None
        self._fleet_at = 0.0
        self._fleet_lock = asyncio.Lock()
        self._schedule_cache = {}
        self._schedule_lock = asyncio.Lock()
        self._scan_cursor = 0

    async def _get_fleet(self, client):
        now = time.monotonic()

        if (
            self._fleet is not None
            and now - self._fleet_at < FLEET_CACHE_SECONDS
        ):
            return self._fleet

        async with self._fleet_lock:
            now = time.monotonic()

            if (
                self._fleet is not None
                and now - self._fleet_at < FLEET_CACHE_SECONDS
            ):
                return self._fleet

            fleet = await client.list_organization_aircraft(ORG_ID)

            self._fleet = sorted(
                list(fleet or []),
                key=lambda item: str(_get(item, "registration", "")),
            )
            self._fleet_at = time.monotonic()

            return self._fleet

    async def _fetch_one(
        self,
        client,
        aircraft,
        semaphore,
        force_refresh,
    ):
        aircraft_id = str(
            _get(aircraft, "id", "")
            or _get(aircraft, "aircraft_id", "")
        )

        registration = str(
            _get(aircraft, "registration", "Unknown registration")
            or "Unknown registration"
        )

        if not aircraft_id:
            return None, True

        now = time.monotonic()
        cached = self._schedule_cache.get(aircraft_id)

        if (
            cached
            and not force_refresh
            and now - cached[0] < SCHEDULE_CACHE_SECONDS
        ):
            schedules = cached[1]
            fetch_failed = False

        else:
            async with semaphore:
                try:
                    schedules = _active_schedules(
                        await client.list_aircraft_schedules(aircraft_id)
                    )

                    self._schedule_cache[aircraft_id] = (
                        time.monotonic(),
                        schedules,
                    )
                    fetch_failed = False

                except Exception:
                    log.warning(
                        "Could not check schedules for %s; retaining cached "
                        "data when available",
                        registration,
                        exc_info=True,
                    )

                    if cached:
                        schedules = cached[1]
                        fetch_failed = True
                    else:
                        return None, True

        if not schedules:
            return None, fetch_failed

        claim_index = _next_claimable_index(schedules)

        if claim_index is None:
            return None, fetch_failed

        route = schedules[claim_index]

        origin = _get(route, "origin_icao") or "???"
        destination = _get(route, "destination_icao") or "???"

        model = AIRCRAFT_MODELS.get(
            registration.strip().upper(),
            _aircraft_model(aircraft) or "Model unknown",
        )

        assigned_before = sum(
            1
            for item in schedules[:claim_index]
            if _get(item, "crew", []) or []
        )

        row = (
            registration,
            model,
            f"{origin} → {destination}",
            len(schedules),
            assigned_before,
        )

        return row, fetch_failed

    async def collect_availability(self, force_refresh=False):
        if not ORG_ID:
            raise RuntimeError("IF_ORGANIZATION_ID is not configured")

        manager = getattr(self.bot, "if_live_manager", None)

        if manager is None:
            raise RuntimeError(
                "Infinite Flight Live manager is not initialized"
            )

        async with await manager.get_client(TOKEN_OWNER_ID) as client:
            fleet = await self._get_fleet(client)
            semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

            now = time.monotonic()
            stale_indexes = []

            for index, aircraft in enumerate(fleet):
                aircraft_id = str(
                    _get(aircraft, "id", "")
                    or _get(aircraft, "aircraft_id", "")
                )

                cached = self._schedule_cache.get(aircraft_id)

                is_fresh = bool(
                    cached
                    and now - cached[0] < SCHEDULE_CACHE_SECONDS
                )

                if not is_fresh:
                    stale_indexes.append(index)

            if force_refresh:
                stale_indexes = list(range(len(fleet)))

            if stale_indexes:
                start = self._scan_cursor % len(stale_indexes)
                rotated_indexes = (
                    stale_indexes[start:] + stale_indexes[:start]
                )

                selected_indexes = rotated_indexes[
                    :MAX_SCHEDULE_FETCHES_PER_SCAN
                ]

                self._scan_cursor = (
                    start + len(selected_indexes)
                ) % len(stale_indexes)
            else:
                selected_indexes = []

            selected_set = set(selected_indexes)
            rows = []
            failed_count = 0

            for index, aircraft in enumerate(fleet):
                aircraft_id = str(
                    _get(aircraft, "id", "")
                    or _get(aircraft, "aircraft_id", "")
                )

                cached = self._schedule_cache.get(aircraft_id)

                if index in selected_set:
                    row, fetch_failed = await self._fetch_one(
                        client,
                        aircraft,
                        semaphore,
                        force_refresh,
                    )
                elif cached:
                    row, fetch_failed = await self._fetch_one(
                        client,
                        aircraft,
                        semaphore,
                        False,
                    )
                else:
                    # No cache yet, and this aircraft is outside the
                    # current API-fetch batch. It can be checked in a
                    # subsequent scan.
                    row, fetch_failed = None, False

                if fetch_failed:
                    failed_count += 1

                if row:
                    rows.append(row)

            rows.sort(key=lambda row: row[0].upper())

            return rows, failed_count

    @app_commands.command(
        name="available-aircraft",
        description="Show aircraft with an unassigned scheduled route.",
    )
    async def available_aircraft(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.defer(thinking=True)

        try:
            rows, failed_count = await self.collect_availability()

            view = AvailabilityView(
                self,
                rows,
                failed_count,
            )

            await interaction.followup.send(
                embed=view.make_embed(),
                view=view,
            )

        except Exception:
            log.exception("Failed to load available aircraft")

            await interaction.followup.send(
                "I couldn't load aircraft availability right now. "
                "Please try again shortly.",
                ephemeral=True,
            )


async def setup(bot):
    await bot.add_cog(AvailableAircraftCog(bot))

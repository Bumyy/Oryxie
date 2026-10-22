
"""Public command showing Qatari Virtual aircraft with claimable scheduled routes in Live Mode."""

import asyncio
import logging
import os
import time

import discord
from discord import app_commands
from discord.ext import commands

from cogs.airline_hubs import RETIRED_REGISTRATIONS, is_cargo_registration

log = logging.getLogger(__name__)

ORG_ID = os.getenv("IF_ORGANIZATION_ID", "").strip()
TOKEN_OWNER_ID = 1

FLEET_CACHE_SECONDS = 300
SCHEDULE_CACHE_SECONDS = 300
PAGE_SIZE = 10

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
    registration = str(
        _get(aircraft, "registration", "") or ""
    ).strip().upper()
    if is_cargo_registration(registration):
        return "Cargo"

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
    # Completed=8, CANCELLED=9, ARRIVED=11.
    return _route_status_number(schedule) in {8, 9, 11}


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


def _queue_group(assigned_before):
    if assigned_before <= GREEN_MAX_FLIGHTS_AHEAD:
        return "green"
    if assigned_before <= YELLOW_MAX_FLIGHTS_AHEAD:
        return "yellow"
    return "red"


class AvailabilityView(discord.ui.View):
    """Public, paginated aircraft availability list."""

    def __init__(self, cog, rows, failed_count=0, page=0):
        super().__init__(timeout=None)

        self.cog = cog
        self.rows = rows
        self.failed_count = failed_count
        self.page = page
        self.pages = self._build_pages()

        self._update_buttons()

    def _build_pages(self):
        grouped = {"green": [], "yellow": [], "red": []}
        for row in sorted(self.rows, key=lambda item: item[0].upper()):
            grouped[_queue_group(row[4])].append(row)

        pages = []
        for group in ("green", "yellow", "red"):
            rows = grouped[group]
            pages.extend(
                (group, rows[start:start + PAGE_SIZE])
                for start in range(0, len(rows), PAGE_SIZE)
            )
        return pages

    def _update_buttons(self):
        self.clear_items()

        previous = discord.ui.Button(
            label="Previous",
            style=discord.ButtonStyle.secondary,
            disabled=self.page <= 0,
            custom_id="available-aircraft:previous",
        )
        previous.callback = self.previous_page
        self.add_item(previous)

        next_button = discord.ui.Button(
            label="Next",
            style=discord.ButtonStyle.secondary,
            disabled=self.page + 1 >= len(self.pages),
            custom_id="available-aircraft:next",
        )
        next_button.callback = self.next_page
        self.add_item(next_button)

        refresh = discord.ui.Button(
            label="Refresh",
            style=discord.ButtonStyle.primary,
            emoji="🔄",
            custom_id="available-aircraft:refresh",
        )
        refresh.callback = self.refresh_list
        self.add_item(refresh)

    def make_embed(self):
        total_pages = max(1, len(self.pages))
        page_group, page_rows = (
            self.pages[self.page] if self.pages else ("green", [])
        )
        page_colors = {
            "green": discord.Color.green(),
            "yellow": discord.Color.yellow(),
            "red": discord.Color.red(),
        }
        group_labels = {
            "green": "🟢 Ready now",
            "yellow": "🟡 1–2 assigned flights ahead",
            "red": "🔴 3 or more assigned flights ahead",
        }

        embed = discord.Embed(
            title=f"Qatari Virtual · Aircraft Availability · {group_labels[page_group]}",
            description=(
                "Public live-schedule snapshot. **Available** means the next "
                "unassigned active route can be requested.\n"
                "This command only checks schedules; it never assigns a flight.\n\n"
                "Aircraft are grouped by queue status: green first, then yellow, "
                "then red. Queue status counts assigned flights before the next "
                "unassigned route; it is not a guaranteed waiting time.\n"
                "Use **Refresh** to check updated availability."
                "\n\nUse **Previous**, **Next**, or **Refresh** to browse the live list."
            ),
            color=page_colors[page_group],
        )

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
                    f"{self.failed_count} aircraft could not be verified "
                    "because of an API error or rate limit. Cached results "
                    "are retained where available; try Refresh again later."
                ),
                inline=False,
            )

        embed.set_footer(
            text=(
                f"{group_labels[page_group]} · Page {self.page + 1}/{total_pages} · "
                "The schedule snapshot includes the full organization fleet; "
                "cached results are retained during API limits."
            )
        )

        return embed

    @staticmethod
    def _message_page(interaction):
        if not interaction.message or not interaction.message.embeds:
            return 0
        footer = interaction.message.embeds[0].footer.text or ""
        for section in footer.split("·"):
            section = section.strip()
            if section.startswith("Page "):
                try:
                    return max(0, int(section[5:].split("/", 1)[0]) - 1)
                except (TypeError, ValueError):
                    return 0
        return 0

    async def _edit(self, interaction, page, force_refresh=False):
        await interaction.response.defer()
        try:
            rows, failed_count = await self.cog.collect_availability(
                force_refresh=force_refresh
            )
            updated_view = AvailabilityView(
                self.cog,
                rows,
                failed_count=failed_count,
                page=page,
            )
            updated_view.page = min(
                max(0, page),
                max(0, len(updated_view.pages) - 1),
            )
            updated_view._update_buttons()
            await interaction.edit_original_response(
                embed=updated_view.make_embed(),
                view=updated_view,
            )
        except Exception:
            log.exception("Failed to update public aircraft availability")
            await interaction.followup.send(
                "I couldn't update the aircraft list right now. "
                "Please try again shortly.",
                ephemeral=True,
            )

    async def previous_page(self, interaction):
        await self._edit(interaction, self._message_page(interaction) - 1)

    async def next_page(self, interaction):
        await self._edit(interaction, self._message_page(interaction) + 1)

    async def refresh_list(self, interaction):
        await self._edit(
            interaction,
            self._message_page(interaction),
            force_refresh=True,
        )


class AvailableAircraftCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._fleet = None
        self._fleet_at = 0.0
        self._fleet_lock = asyncio.Lock()
        self._schedule_cache = {}

    async def cog_load(self):
        self.bot.add_view(AvailabilityView(self, []))

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
                [
                    aircraft
                    for aircraft in (fleet or [])
                    if str(
                        _get(aircraft, "registration", "") or ""
                    ).strip().upper()
                    not in RETIRED_REGISTRATIONS
                ],
                key=lambda item: str(_get(item, "registration", "")),
            )
            self._fleet_at = time.monotonic()

            return self._fleet

    async def _get_organization_schedules(self, client):
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

    @staticmethod
    def _availability_row(aircraft, schedules):
        schedules = _active_schedules(schedules)
        if not schedules:
            return None

        claim_index = _next_claimable_index(schedules)
        if claim_index is None:
            return None

        registration = str(
            _get(aircraft, "registration", "Unknown registration")
            or "Unknown registration"
        )
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

        return (
            registration,
            model,
            f"{origin} → {destination}",
            len(schedules),
            assigned_before,
        )

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
            now = time.monotonic()
            cache_fresh = all(
                (
                    str(_get(aircraft, "id", "") or _get(aircraft, "aircraft_id", ""))
                    in self._schedule_cache
                    and now
                    - self._schedule_cache[
                        str(_get(aircraft, "id", "") or _get(aircraft, "aircraft_id", ""))
                    ][0]
                    < SCHEDULE_CACHE_SECONDS
                )
                for aircraft in fleet
            )

            refresh_failed = False
            if force_refresh or not cache_fresh:
                try:
                    organization_schedules = await self._get_organization_schedules(
                        client
                    )
                    schedules_by_aircraft = {}
                    for schedule in organization_schedules:
                        aircraft_id = str(_get(schedule, "aircraft_id") or "")
                        if aircraft_id:
                            schedules_by_aircraft.setdefault(
                                aircraft_id, []
                            ).append(schedule)

                    refreshed_at = time.monotonic()
                    for aircraft in fleet:
                        aircraft_id = str(
                            _get(aircraft, "id", "")
                            or _get(aircraft, "aircraft_id", "")
                        )
                        if aircraft_id:
                            self._schedule_cache[aircraft_id] = (
                                refreshed_at,
                                schedules_by_aircraft.get(aircraft_id, []),
                            )
                except Exception:
                    refresh_failed = True
                    log.exception(
                        "Could not refresh organization schedules for "
                        "/available-aircraft; using cached snapshots where available"
                    )

            rows = []
            failed_count = len(fleet) if refresh_failed else 0
            for aircraft in fleet:
                aircraft_id = str(
                    _get(aircraft, "id", "")
                    or _get(aircraft, "aircraft_id", "")
                )
                cached = self._schedule_cache.get(aircraft_id)
                if not cached:
                    if not refresh_failed:
                        failed_count += 1
                    continue

                row = self._availability_row(aircraft, cached[1])
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

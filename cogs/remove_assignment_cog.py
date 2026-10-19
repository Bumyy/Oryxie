
import logging
import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

TOKEN_OWNER_ID = 1
LIVE_ADMINS_ROLE_ID = 1534431240327921745
ORG_ID = os.getenv("IF_ORGANIZATION_ID", "").strip()

CANCELLED_STATUS = 9
COMPLETED_STATUSES = {8, 11}
ARCHIVED_STATUSES = set()

VIEW_TIMEOUT = 120
MAX_OPTIONS = 25

STATUS_NAMES = {
    0: "Scheduled",
    1: "Scheduled",
    2: "Scheduled",
    3: "Scheduled",
    4: "Scheduled",
    5: "Scheduled",
    6: "In flight",
    7: "Diverted",
    8: "Completed",
    9: "Cancelled",
    10: "Taxiing to parking",
    11: "Arrived",
}


def get_value(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def normalize_registration(value):
    return "".join(
        char for char in str(value or "").upper()
        if char.isalnum()
    )


def crew_user_id(member):
    for key in ("userId", "user_id", "ifuserid", "id"):
        value = get_value(member, key)
        if value not in (None, ""):
            return str(value)
    return ""


def sequence(schedule):
    try:
        return int(get_value(schedule, "sequence", 0))
    except (TypeError, ValueError):
        return 0


def get_status_number(schedule):
    try:
        return int(get_value(schedule, "status"))
    except (TypeError, ValueError):
        return None


def status_label(schedule):
    status = get_status_number(schedule)
    if status is None:
        return str(get_value(schedule, "status", "Unknown"))
    return STATUS_NAMES.get(status, f"Status {status}")


def has_taken_off(schedule):
    """Detect takeoff using actual-departure fields only."""
    for key in (
        "actual_departure_utc",
        "actualDepartureUtc",
        "actual_departure",
        "actualDeparture",
        "actual_departure_time",
        "actualDepartureTime",
    ):
        value = get_value(schedule, key)

        if value not in (None, "", False, 0):
            return True

    return False


def parse_datetime(value):
    if value in (None, ""):
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        try:
            stamp = float(value)
            if stamp > 10_000_000_000:
                stamp /= 1000
            parsed = datetime.fromtimestamp(stamp, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(
                value.strip().replace("Z", "+00:00")
            )
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def scheduled_departure(schedule):
    """Read a scheduled departure time if the API supplies one."""
    for key in (
        "scheduled_departure_utc",
        "scheduledDepartureUtc",
        "scheduled_departure",
        "scheduledDeparture",
        "departure_time_utc",
        "departureTimeUtc",
        "departure_utc",
        "departureUtc",
        "scheduled_time_utc",
        "scheduledTimeUtc",
        "departure_time",
        "departureTime",
    ):
        parsed = parse_datetime(get_value(schedule, key))
        if parsed is not None:
            return parsed

    return None


def is_active_scheduled_flight(schedule):
    """Check whether a schedule is active and eligible for removal."""
    if not get_value(schedule, "id"):
        return False

    status = get_status_number(schedule)

    if status == CANCELLED_STATUS:
        return False

    if status in COMPLETED_STATUSES or status in ARCHIVED_STATUSES:
        return False

    for key in (
        "archived",
        "is_archived",
        "completed",
        "is_completed",
    ):
        if get_value(schedule, key) is True:
            return False

    return True


def ordered_schedules(schedules):
    return sorted(schedules or [], key=sequence)


def is_return_leg(schedules, selected):
    """Identify a possible return leg by comparing it with the previous leg."""
    ordered = ordered_schedules(schedules)
    selected_id = str(get_value(selected, "id", ""))

    for index, item in enumerate(ordered):
        if str(get_value(item, "id", "")) != selected_id:
            continue

        if index == 0:
            return False

        previous = ordered[index - 1]

        previous_origin = str(
            get_value(previous, "origin_icao", "")
        ).upper()
        previous_destination = str(
            get_value(previous, "destination_icao", "")
        ).upper()
        selected_origin = str(
            get_value(selected, "origin_icao", "")
        ).upper()
        selected_destination = str(
            get_value(selected, "destination_icao", "")
        ).upper()

        return (
            previous_origin == selected_destination
            and previous_destination == selected_origin
        )

    return False


def is_live_admin(member):
    return any(
        getattr(role, "id", None) == LIVE_ADMINS_ROLE_ID
        for role in getattr(member, "roles", [])
    )


def schedule_label(schedule):
    callsign = (
        get_value(schedule, "callsign")
        or get_value(schedule, "flight_number")
        or "Scheduled flight"
    )
    origin = get_value(schedule, "origin_icao", "?")
    destination = get_value(schedule, "destination_icao", "?")
    return f"{callsign} | {origin} → {destination}"[:100]


def schedule_description(schedule, display_number=None):
    if display_number is None:
        return status_label(schedule)[:100]
    return f"Route {display_number} • {status_label(schedule)}"[:100]


def get_crew_name(member):
    return str(
        get_value(member, "username")
        or get_value(member, "name")
        or get_value(member, "displayName")
        or f"Pilot {crew_user_id(member)}"
    )


def has_crew_assignment(schedule, if_user_id):
    return str(if_user_id) in {
        crew_user_id(member)
        for member in (get_value(schedule, "crew", []) or [])
    }


def eligible_pilot_schedules(schedules, if_user_id):
    """Return the pilot's active assignments that have not taken off."""
    return [
        item
        for item in ordered_schedules(schedules)
        if is_active_scheduled_flight(item)
        and has_crew_assignment(item, if_user_id)
        and not has_taken_off(item)
    ]


async def get_current_schedule(client, aircraft_id, schedule_id):
    schedules = await client.list_aircraft_schedules(aircraft_id)
    schedules = schedules or []

    schedule = next(
        (
            item for item in schedules
            if str(get_value(item, "id", "")) == str(schedule_id)
        ),
        None,
    )

    return schedules, schedule


async def find_discord_id_for_if_user(bot, if_user_id):
    model = getattr(bot, "pilots_model", None)
    if model is None:
        return None

    for method_name in (
        "get_pilot_by_ifuserid",
        "get_pilot_by_if_user_id",
        "get_pilot_by_infinite_flight_id",
    ):
        method = getattr(model, method_name, None)
        if not callable(method):
            continue

        try:
            pilot = await method(str(if_user_id))
        except Exception:
            log.debug(
                "IF-to-Discord pilot lookup failed via %s",
                method_name,
                exc_info=True,
            )
            continue

        if not pilot:
            continue

        discord_id = (
            get_value(pilot, "discord_id")
            or get_value(pilot, "discordid")
            or get_value(pilot, "discord_user_id")
        )

        if discord_id:
            return str(discord_id)

    return None


async def announce_removal(
    interaction,
    bot,
    registration,
    schedules,
    if_user_id,
    fallback_name,
):
    if not schedules or interaction.channel is None:
        return

    discord_id = await find_discord_id_for_if_user(bot, if_user_id)

    if discord_id:
        mention = f"<@{discord_id}>"
        allowed_mentions = discord.AllowedMentions(
            users=True,
            roles=False,
            everyone=False,
        )
    else:
        mention = discord.utils.escape_mentions(fallback_name)
        allowed_mentions = discord.AllowedMentions.none()

    if len(schedules) == 1:
        message = (
            f"✅ {mention}, your assignment for **{registration}** — "
            f"**{schedule_label(schedules[0])}** — has been removed."
        )
    else:
        lines = [
            f"• {schedule_label(item)}"
            for item in schedules[:15]
        ]

        if len(schedules) > 15:
            lines.append(f"• …and {len(schedules) - 15} more.")

        message = (
            f"✅ {mention}, **{len(schedules)} assignments** "
            f"for **{registration}** have been removed:\n"
            + "\n".join(lines)
        )

    await interaction.channel.send(
        message,
        allowed_mentions=allowed_mentions,
    )


class ConfirmRemovalView(discord.ui.View):
    """Admin-only confirmation to remove one pilot from one flight."""

    def __init__(
        self,
        cog,
        requester_id,
        aircraft_id,
        registration,
        schedule_id,
        target_user_id,
        target_name,
        was_admin,
    ):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.requester_id = requester_id
        self.aircraft_id = str(aircraft_id)
        self.registration = registration
        self.schedule_id = str(schedule_id)
        self.target_user_id = str(target_user_id)
        self.target_name = target_name
        self.was_admin = was_admin

    async def interaction_check(self, interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "This confirmation belongs to the person who started the command.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Confirm removal",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
    )
    async def confirm(self, interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not ORG_ID:
            await interaction.followup.send(
                "The organization ID is not configured. "
                "Please contact a Live Admin for help.",
                ephemeral=True,
            )
            return

        try:
            admin_now = is_live_admin(interaction.user)

            if self.was_admin and not admin_now:
                await interaction.followup.send(
                    "Your Live Admin permission has changed. "
                    "Run the command again.",
                    ephemeral=True,
                )
                return

            async with await self.cog.bot.if_live_manager.get_client(
                TOKEN_OWNER_ID
            ) as client:
                aircraft_list = await client.list_organization_aircraft(ORG_ID)

                aircraft = next(
                    (
                        item for item in (aircraft_list or [])
                        if str(get_value(item, "id", "")) == self.aircraft_id
                    ),
                    None,
                )

                if aircraft is None:
                    await interaction.followup.send(
                        "That aircraft is no longer in the organization fleet. "
                        "Please contact a Live Admin if you need help.",
                        ephemeral=True,
                    )
                    return

                schedules, schedule = await get_current_schedule(
                    client,
                    self.aircraft_id,
                    self.schedule_id,
                )

                if schedule is None or not is_active_scheduled_flight(schedule):
                    await interaction.followup.send(
                        "That flight no longer exists or is no longer active. "
                        "Please contact a Live Admin if you need help.",
                        ephemeral=True,
                    )
                    return

                if not has_crew_assignment(schedule, self.target_user_id):
                    await interaction.followup.send(
                        "That pilot is no longer assigned to this flight.",
                        ephemeral=True,
                    )
                    return

                if not admin_now:
                    pilot = await self.cog.bot.pilots_model.get_pilot_by_discord_id(
                        str(interaction.user.id)
                    )

                    if not pilot or not pilot.get("ifuserid"):
                        await interaction.followup.send(
                            "Your Discord account is not linked to an Infinite Flight "
                            "pilot profile. Please contact a Live Admin for help.",
                            ephemeral=True,
                        )
                        return

                    if str(pilot["ifuserid"]) != self.target_user_id:
                        await interaction.followup.send(
                            "You can only remove your own assignment.",
                            ephemeral=True,
                        )
                        return

                    if has_taken_off(schedule):
                        await interaction.followup.send(
                            "You cannot remove an assignment after takeoff.",
                            ephemeral=True,
                        )
                        return

                # Pilots are directed to bulk removal in ScheduleSelectView.
                # This view is for admins removing one pilot from one flight.
                if not admin_now:
                    await interaction.followup.send(
                        "Individual-leg cancellation is not allowed. "
                        "Please use the bulk-removal confirmation instead.",
                        ephemeral=True,
                    )
                    return

                target_id = str(get_value(schedule, "id", ""))

                try:
                    await client.remove_crew(
                        target_id,
                        self.target_user_id,
                    )
                except Exception:
                    log.exception(
                        "Could not remove pilot %s from schedule %s",
                        self.target_user_id,
                        target_id,
                    )
                    await interaction.followup.send(
                        "I couldn't remove that assignment. "
                        "Please contact a Live Admin for help.",
                        ephemeral=True,
                    )
                    return

                refreshed_schedules = await client.list_aircraft_schedules(
                    self.aircraft_id
                )
                refreshed = next(
                    (
                        item for item in (refreshed_schedules or [])
                        if str(get_value(item, "id", "")) == target_id
                    ),
                    None,
                )

                verified_removed = (
                    refreshed is not None
                    and not has_crew_assignment(
                        refreshed,
                        self.target_user_id,
                    )
                )

            for child in self.children:
                child.disabled = True

            try:
                await interaction.edit_original_response(view=self)
            except discord.HTTPException:
                pass

            if not verified_removed:
                await interaction.followup.send(
                    "The removal could not be verified. "
                    "Please contact a Live Admin before retrying.",
                    ephemeral=True,
                )
                return

            await announce_removal(
                interaction,
                self.cog.bot,
                self.registration,
                [schedule],
                self.target_user_id,
                self.target_name,
            )

            await interaction.followup.send(
                f"✅ Removed and verified the assignment for "
                f"**{self.registration}**.",
                ephemeral=True,
            )

        except Exception:
            log.exception("Failed to remove assignment")
            await interaction.followup.send(
                "An error occurred while removing the assignment. "
                "Please contact a Live Admin for help before retrying.",
                ephemeral=True,
            )

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
        emoji="✖️",
    )
    async def cancel(self, interaction, button):
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content="Removal cancelled. No assignment was changed.",
            view=self,
        )
        self.stop()


class ConfirmBulkRemovalView(discord.ui.View):
    """Confirmation to remove all eligible assignments for a pilot/aircraft."""

    def __init__(
        self,
        cog,
        requester_id,
        aircraft_id,
        registration,
        requester_if_id,
        schedules,
    ):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.requester_id = requester_id
        self.aircraft_id = str(aircraft_id)
        self.registration = registration
        self.requester_if_id = str(requester_if_id)
        self.initial_schedules = list(schedules)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "This confirmation belongs to the person who started the command.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Yes — remove all my eligible assignments",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
    )
    async def confirm_bulk(self, interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not ORG_ID:
            await interaction.followup.send(
                "The organization ID is not configured. "
                "Please contact a Live Admin for help.",
                ephemeral=True,
            )
            return

        try:
            if is_live_admin(interaction.user):
                await interaction.followup.send(
                    "Your permissions changed. Run the command again.",
                    ephemeral=True,
                )
                return

            pilot = await self.cog.bot.pilots_model.get_pilot_by_discord_id(
                str(interaction.user.id)
            )

            if (
                not pilot
                or not pilot.get("ifuserid")
                or str(pilot["ifuserid"]) != self.requester_if_id
            ):
                await interaction.followup.send(
                    "Your linked Infinite Flight pilot has changed. "
                    "Run the command again.",
                    ephemeral=True,
                )
                return

            removed_ids = set()
            failed_ids = set()
            eligible_labels = {}

            async with await self.cog.bot.if_live_manager.get_client(
                TOKEN_OWNER_ID
            ) as client:
                aircraft_list = await client.list_organization_aircraft(ORG_ID)

                aircraft = next(
                    (
                        item for item in (aircraft_list or [])
                        if str(get_value(item, "id", "")) == self.aircraft_id
                    ),
                    None,
                )

                if aircraft is None:
                    await interaction.followup.send(
                        "That aircraft is no longer in the organization fleet. "
                        "Please contact a Live Admin for help.",
                        ephemeral=True,
                    )
                    return

                schedules = ordered_schedules(
                    await client.list_aircraft_schedules(
                        self.aircraft_id
                    ) or []
                )

                # Re-check current eligibility immediately before removing.
                eligible = eligible_pilot_schedules(
                    schedules,
                    self.requester_if_id,
                )

                if not eligible:
                    await interaction.followup.send(
                        "No eligible assignments remain to remove. "
                        "No changes were made.",
                        ephemeral=True,
                    )
                    return

                for schedule in eligible:
                    schedule_id = str(get_value(schedule, "id", ""))
                    eligible_labels[schedule_id] = schedule

                    try:
                        await client.remove_crew(
                            schedule_id,
                            self.requester_if_id,
                        )
                        removed_ids.add(schedule_id)
                    except Exception:
                        log.exception(
                            "Could not remove pilot %s from schedule %s",
                            self.requester_if_id,
                            schedule_id,
                        )
                        failed_ids.add(schedule_id)

                final_schedules = await client.list_aircraft_schedules(
                    self.aircraft_id
                )

                final_by_id = {
                    str(get_value(item, "id", "")): item
                    for item in (final_schedules or [])
                }

                verified_removed = []

                for schedule_id in removed_ids:
                    refreshed = final_by_id.get(schedule_id)

                    if (
                        refreshed is not None
                        and not has_crew_assignment(
                            refreshed,
                            self.requester_if_id,
                        )
                    ):
                        verified_removed.append(
                            eligible_labels[schedule_id]
                        )
                    else:
                        failed_ids.add(schedule_id)

                failed_labels = [
                    schedule_label(eligible_labels[schedule_id])
                    for schedule_id in failed_ids
                    if schedule_id in eligible_labels
                ]

            for child in self.children:
                child.disabled = True

            try:
                await interaction.edit_original_response(view=self)
            except discord.HTTPException:
                pass

            if verified_removed:
                await announce_removal(
                    interaction,
                    self.cog.bot,
                    self.registration,
                    verified_removed,
                    self.requester_if_id,
                    interaction.user.display_name,
                )

            lines = [
                f"**Bulk removal finished for {self.registration}.**",
                f"✅ Removed and verified: **{len(verified_removed)}**",
            ]

            if failed_labels:
                lines.append(
                    f"⚠️ Failed or not verified: **{len(failed_labels)}**"
                )
                lines.append(
                    "\n**Please check these routes:**\n"
                    + "\n".join(
                        f"• {name}" for name in failed_labels[:10]
                    )
                )
                lines.append(
                    "Please contact a Live Admin for help before retrying."
                )
            elif verified_removed:
                lines.append(
                    "All eligible assignments selected for removal were verified."
                )

            await interaction.followup.send(
                "\n".join(lines),
                ephemeral=True,
            )

        except Exception:
            log.exception("Bulk assignment removal failed")
            await interaction.followup.send(
                "Bulk removal encountered an error. Some assignments may already "
                "have been removed. Please contact a Live Admin for help before retrying.",
                ephemeral=True,
            )

    @discord.ui.button(
        label="No — keep my assignments",
        style=discord.ButtonStyle.secondary,
        emoji="✖️",
    )
    async def cancel_bulk(self, interaction, button):
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=(
                f"Removal cancelled for **{self.registration}**. "
                "Your assignments have been left unchanged."
            ),
            view=self,
        )
        self.stop()


class CrewSelectView(discord.ui.View):
    def __init__(
        self,
        cog,
        requester_id,
        aircraft,
        schedule,
        schedules,
        crew,
        admin,
    ):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.requester_id = requester_id
        self.aircraft = aircraft
        self.schedule = schedule
        self.schedules = schedules
        self.crew = crew
        self.admin = admin

        options = []

        for member in crew[:MAX_OPTIONS]:
            user_id = crew_user_id(member)

            if not user_id:
                continue

            options.append(
                discord.SelectOption(
                    label=get_crew_name(member)[:100],
                    value=user_id,
                    description=f"IF user ID: {user_id}"[:100],
                )
            )

        if options:
            select = discord.ui.Select(
                placeholder="Select the assigned pilot",
                min_values=1,
                max_values=1,
                options=options,
            )
            select.callback = self.selected
            self.add_item(select)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "This menu belongs to the person who started the command.",
                ephemeral=True,
            )
            return False
        return True

    async def selected(self, interaction):
        target_id = interaction.data["values"][0]

        member = next(
            (
                item for item in self.crew
                if crew_user_id(item) == target_id
            ),
            None,
        )

        if member is None:
            await interaction.response.send_message(
                "That pilot is no longer available in the crew list.",
                ephemeral=True,
            )
            return

        registration = str(
            get_value(self.aircraft, "registration", "Unknown")
        )

        if not self.admin:
            await interaction.response.send_message(
                "Only Live Admins can remove an individual flight assignment. "
                "Use the pilot bulk-removal confirmation to remove all your "
                "eligible assignments together.",
                ephemeral=True,
            )
            return

        view = ConfirmRemovalView(
            self.cog,
            interaction.user.id,
            get_value(self.aircraft, "id"),
            registration,
            get_value(self.schedule, "id"),
            target_id,
            get_crew_name(member),
            self.admin,
        )

        await interaction.response.edit_message(
            content=(
                "⚠️ **Confirm assignment removal**\n\n"
                f"Aircraft: **{registration}**\n"
                f"Flight: **{schedule_label(self.schedule)}**\n"
                f"Pilot: **{get_crew_name(member)}**\n\n"
                "Only the crew assignment will be removed. "
                "The scheduled flight will remain."
            ),
            view=view,
        )


class ScheduleSelectView(discord.ui.View):
    def __init__(
        self,
        cog,
        requester_id,
        aircraft,
        schedules,
        admin,
        requester_if_id,
    ):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.requester_id = requester_id
        self.aircraft = aircraft
        self.schedules = ordered_schedules(schedules)
        self.admin = admin
        self.requester_if_id = requester_if_id

        options = [
            discord.SelectOption(
                label=schedule_label(schedule),
                value=str(get_value(schedule, "id", "")),
                description=schedule_description(schedule, index),
            )
            for index, schedule in enumerate(
                self.schedules[:MAX_OPTIONS],
                start=1,
            )
        ]

        select = discord.ui.Select(
            placeholder="Select a scheduled flight",
            min_values=1,
            max_values=1,
            options=options,
        )
        select.callback = self.selected
        self.add_item(select)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "This menu belongs to the person who started the command.",
                ephemeral=True,
            )
            return False
        return True

    async def selected(self, interaction):
        schedule_id = interaction.data["values"][0]

        schedule = next(
            (
                item for item in self.schedules
                if str(get_value(item, "id", "")) == schedule_id
            ),
            None,
        )

        if schedule is None or not is_active_scheduled_flight(schedule):
            await interaction.response.send_message(
                "That flight is no longer active or available.",
                ephemeral=True,
            )
            return

        crew = get_value(schedule, "crew", []) or []

        if not crew:
            await interaction.response.send_message(
                "No crew assignments were found for this flight.",
                ephemeral=True,
            )
            return

        if not self.admin:
            if not has_crew_assignment(schedule, self.requester_if_id):
                await interaction.response.send_message(
                    "You are not assigned to this flight.",
                    ephemeral=True,
                )
                return

            if has_taken_off(schedule):
                await interaction.response.send_message(
                    "You cannot remove an assignment after takeoff.",
                    ephemeral=True,
                )
                return

            # Do not remove a single leg: direct the pilot to the bulk
            # confirmation flow. The selected flight is just the entry point.
            eligible = eligible_pilot_schedules(
                self.schedules,
                self.requester_if_id,
            )

            if not eligible:
                await interaction.response.send_message(
                    "No eligible assignments remain to remove.",
                    ephemeral=True,
                )
                return

            view = ConfirmBulkRemovalView(
                self.cog,
                interaction.user.id,
                get_value(self.aircraft, "id"),
                str(get_value(self.aircraft, "registration", "Unknown")),
                self.requester_if_id,
                eligible,
            )

            lines = [
                f"• {schedule_label(item)}"
                for item in eligible[:15]
            ]
            if len(eligible) > 15:
                lines.append(f"• …and {len(eligible) - 15} more.")

            await interaction.response.edit_message(
                content=(
                    f"⚠️ **Multi-leg assignment removal — "
                    f"{get_value(self.aircraft, 'registration', 'Unknown')}**\n\n"
                    f"You have **{len(eligible)} eligible assignment(s)** "
                    "on this aircraft. Individual-leg cancellation is not allowed.\n\n"
                    "**Would you like to remove all of these eligible assignments together?**\n"
                    + "\n".join(lines)
                    + "\n\nChoose **Yes** to remove them, or **No** to keep all assignments. "
                    "If an error occurs, contact a Live Admin for help."
                ),
                view=view,
            )
            return

        view = CrewSelectView(
            self.cog,
            interaction.user.id,
            self.aircraft,
            schedule,
            self.schedules,
            crew,
            self.admin,
        )

        if not view.children:
            await interaction.response.send_message(
                "No selectable crew members were returned by the API.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content=(
                f"Aircraft: **{get_value(self.aircraft, 'registration', 'Unknown')}**\n"
                f"Flight: **{schedule_label(schedule)}**\n\n"
                "Select the assigned pilot to continue."
            ),
            view=view,
        )


class RemoveAssignmentCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="remove-assignment",
        description="Remove a pilot's assignment from a scheduled flight.",
    )
    @app_commands.describe(
        registration="Aircraft registration, e.g. A7-ANN"
    )
    async def remove_assignment(
        self,
        interaction: discord.Interaction,
        registration: str,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not ORG_ID:
            await interaction.followup.send(
                "The organization ID is not configured. "
                "Please contact a Live Admin for help.",
                ephemeral=True,
            )
            return

        admin = is_live_admin(interaction.user)
        requester_if_id = None

        try:
            if not admin:
                pilot = await self.bot.pilots_model.get_pilot_by_discord_id(
                    str(interaction.user.id)
                )

                if not pilot or not pilot.get("ifuserid"):
                    await interaction.followup.send(
                        "Your Discord account is not linked to an Infinite Flight "
                        "pilot profile. Please contact a Live Admin for help.",
                        ephemeral=True,
                    )
                    return

                requester_if_id = str(pilot["ifuserid"])

            async with await self.bot.if_live_manager.get_client(
                TOKEN_OWNER_ID
            ) as client:
                aircraft_list = await client.list_organization_aircraft(ORG_ID)
                wanted = normalize_registration(registration)

                aircraft = next(
                    (
                        item for item in (aircraft_list or [])
                        if normalize_registration(
                            get_value(item, "registration", "")
                        ) == wanted
                    ),
                    None,
                )

                if aircraft is None:
                    await interaction.followup.send(
                        f"No aircraft registration matching **{registration}** "
                        "was found. Please contact a Live Admin if you need help.",
                        ephemeral=True,
                    )
                    return

                aircraft_id = str(get_value(aircraft, "id", ""))
                actual_registration = str(
                    get_value(aircraft, "registration", registration)
                )

                all_schedules = ordered_schedules(
                    await client.list_aircraft_schedules(aircraft_id) or []
                )

                active_schedules = [
                    item for item in all_schedules
                    if is_active_scheduled_flight(item)
                ]

                if not admin:
                    eligible = eligible_pilot_schedules(
                        all_schedules,
                        requester_if_id,
                    )

                    if not eligible:
                        any_assignment = any(
                            has_crew_assignment(item, requester_if_id)
                            for item in all_schedules
                        )

                        if any_assignment:
                            await interaction.followup.send(
                                "You have assignments on this aircraft, but none "
                                "are eligible for removal. Flights marked as "
                                "departed, completed, or cancelled are excluded.",
                                ephemeral=True,
                            )
                        else:
                            await interaction.followup.send(
                                f"You don't currently have an assignment on "
                                f"**{actual_registration}**.",
                                ephemeral=True,
                            )
                        return

                    view = ConfirmBulkRemovalView(
                        self,
                        interaction.user.id,
                        aircraft_id,
                        actual_registration,
                        requester_if_id,
                        eligible,
                    )

                    lines = [
                        f"• {schedule_label(item)}"
                        for item in eligible[:15]
                    ]
                    if len(eligible) > 15:
                        lines.append(f"• …and {len(eligible) - 15} more.")

                    await interaction.followup.send(
                        f"⚠️ **Multi-leg assignment removal — "
                        f"{actual_registration}**\n\n"
                        f"You have **{len(eligible)} eligible assignment(s)** "
                        "on this aircraft. Individual-leg cancellation is not allowed.\n\n"
                        "**Would you like to remove all of these eligible assignments together?**\n"
                        + "\n".join(lines)
                        + "\n\nChoose **Yes** to remove them, or **No** to keep all "
                        "assignments. If an error occurs, contact a Live Admin for help.",
                        view=view,
                        ephemeral=True,
                    )
                    return

                if not active_schedules:
                    await interaction.followup.send(
                        f"No active scheduled flights were found for "
                        f"**{actual_registration}**.",
                        ephemeral=True,
                    )
                    return

                if len(active_schedules) > MAX_OPTIONS:
                    await interaction.followup.send(
                        "There are more than 25 active scheduled flights for this "
                        "aircraft, so they cannot fit in one menu. Please contact "
                        "a Live Admin for help.",
                        ephemeral=True,
                    )
                    return

                view = ScheduleSelectView(
                    self,
                    interaction.user.id,
                    aircraft,
                    active_schedules,
                    admin,
                    requester_if_id,
                )

                await interaction.followup.send(
                    f"Aircraft: **{actual_registration}**\n"
                    "Select the active scheduled flight.",
                    view=view,
                    ephemeral=True,
                )

        except Exception:
            log.exception("Failed to load assignment removal menu")
            await interaction.followup.send(
                "I couldn't load the aircraft or schedules. "
                "Please contact a Live Admin for help.",
                ephemeral=True,
            )


async def setup(bot):
    await bot.add_cog(RemoveAssignmentCog(bot))

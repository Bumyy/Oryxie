"""Discord command for requesting the top scheduled route on Qatari Virtual aircraft.

Copy this file to cogs/request_flight_cog.py. The sequence ordering assumption must
be checked against the Infinite Flight organization page before enabling claims.
"""

import os
import logging

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)
ORG_ID = os.getenv("IF_ORGANIZATION_ID", "").strip()

RULES = (
    "**Qatari Virtual flight rules**\n"
    "• If an aircraft is stopped in the air, you have 24 hours to land.\n"
    "• If parked at a hub airport, you have 6 hours from assignment to take off.\n"
    "• If parked at a non-hub airport, you have 12 hours from assignment to take off.\n"
    "• You must fly the topmost scheduled route; you cannot skip lower routes.\n"
    "• If overdue, an admin may take over the flight or make the assignment available to other pilots."
)


def _get(obj, key, default=None):
    """Read a value from either a dataclass/object or a dictionary."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _crew_user_id(member):
    return str(_get(member, "userId", _get(member, "user_id", _get(member, "id", ""))))


class AircraftSelect(discord.ui.Select):
    def __init__(self, cog, pilot, aircraft):
        self.cog = cog
        self.pilot = pilot
        self.aircraft_by_id = {str(_get(a, "id")): a for a in aircraft}
        options = [
            discord.SelectOption(
                label=str(_get(a, "registration", "Unknown registration"))[:100],
                description=f"Aircraft status: {_get(a, 'status', 'unknown')}"[:100],
                value=str(_get(a, "id")),
            )
            for a in aircraft[:25]
        ]
        super().__init__(placeholder="Choose an aircraft registration…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        aircraft = self.aircraft_by_id.get(self.values[0])
        if aircraft is None:
            await interaction.response.send_message("I couldn't find that aircraft. Run `/request-flight` again.", ephemeral=True)
            return
        await self.cog.show_aircraft_route(interaction, self.pilot, aircraft)


class AircraftSelectView(discord.ui.View):
    def __init__(self, cog, pilot, aircraft):
        super().__init__(timeout=120)
        self.add_item(AircraftSelect(cog, pilot, aircraft))


class ClaimRouteView(discord.ui.View):
    def __init__(self, cog, pilot, aircraft, schedule, pilot_if_user_id):
        super().__init__(timeout=120)
        self.cog = cog
        self.pilot = pilot
        self.aircraft = aircraft
        self.schedule = schedule
        self.pilot_if_user_id = str(pilot_if_user_id)
        self.message = None
        self.claimed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != str(self.pilot.get("discordid")):
            await interaction.response.send_message("Only the pilot who requested this flight can confirm it.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm and request assignment", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.claimed:
            await interaction.response.send_message("This request has already been processed.", ephemeral=True)
            return
        self.claimed = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("Checking the schedule again before assigning…", ephemeral=True)

        try:
            manager = getattr(self.cog.bot, "if_live_manager", None)
            if manager is None:
                raise RuntimeError("Infinite Flight Live manager is not initialized")
            async with await manager.get_client(int(self.pilot["id"])) as client:
                # Re-fetch schedules immediately before writing, to avoid claiming a route
                # that has changed or already been assigned since the preview.
                schedules = await client.list_aircraft_schedules(str(_get(self.aircraft, "id")))
                if not schedules:
                    await interaction.followup.send("No scheduled routes were returned for this aircraft. Nothing was assigned.", ephemeral=True)
                    return
                schedules = sorted(schedules, key=lambda s: int(_get(s, "sequence", 0)))
                top = schedules[0]
                if str(_get(top, "id")) != str(_get(self.schedule, "id")):
                    await interaction.followup.send("The top scheduled route changed since you opened this request. Nothing was assigned; run `/request-flight` again.", ephemeral=True)
                    return
                crew = _get(top, "crew", []) or []
                if any(_crew_user_id(member) == self.pilot_if_user_id for member in crew):
                    await interaction.followup.send("You are already assigned to this route. Nothing was changed.", ephemeral=True)
                    return
                if crew:
                    await interaction.followup.send("This route has been assigned to another crew member. Nothing was changed.", ephemeral=True)
                    return
                # Infinite Flight crew role 0 = Captain.
                updated = await client.assign_crew(str(_get(top, "id")), self.pilot_if_user_id, 0)
                updated_crew = _get(updated, "crew", []) or []
                if not any(_crew_user_id(member) == self.pilot_if_user_id for member in updated_crew):
                    # Verify by fetching the route again if the response did not include the crew.
                    refreshed = await client.list_aircraft_schedules(str(_get(self.aircraft, "id")))
                    match = next((s for s in refreshed if str(_get(s, "id")) == str(_get(top, "id"))), None)
                    updated_crew = _get(match, "crew", []) or [] if match else []
                if not any(_crew_user_id(member) == self.pilot_if_user_id for member in updated_crew):
                    await interaction.followup.send("The API did not confirm your assignment. Please contact staff before trying again.", ephemeral=True)
                    return
                await interaction.followup.send(
                    f"✅ **Assignment confirmed**\nAircraft: **{_get(self.aircraft, 'registration', 'Unknown')}**\n"
                    f"Route: **{_get(top, 'callsign', 'Unknown')}** — {_get(top, 'origin_icao', '???')} → {_get(top, 'destination_icao', '???')}\n"
                    f"Pilot: **{self.pilot.get('callsign', interaction.user.display_name)}**\n\n{RULES}",
                    ephemeral=True,
                )
        except Exception:
            log.exception("Failed to assign Infinite Flight schedule crew")
            await interaction.followup.send("❌ I couldn't verify the assignment with Infinite Flight. No success is being reported; please contact staff and don't retry until they check the schedule.", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Request cancelled. No assignment was made.", view=self)


class RequestFlightCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="request-flight", description="Request assignment to an aircraft's top scheduled route")
    async def request_flight(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not ORG_ID:
            await interaction.followup.send("The bot is missing `IF_ORGANIZATION_ID` in its environment.", ephemeral=True)
            return
        pilots_model = getattr(self.bot, "pilots_model", None)
        if pilots_model is None:
            await interaction.followup.send("Pilot database is not available right now. Please contact staff.", ephemeral=True)
            return
        try:
            pilot = await pilots_model.get_pilot_by_discord_id(str(interaction.user.id))
            if not pilot:
                await interaction.followup.send("I couldn't find an active pilot profile linked to your Discord account. Please contact staff.", ephemeral=True)
                return
            if not pilot.get("ifuserid"):
                await interaction.followup.send("Your pilot profile doesn't have an Infinite Flight user ID linked. Please contact staff.", ephemeral=True)
                return
            manager = getattr(self.bot, "if_live_manager", None)
            if manager is None:
                await interaction.followup.send("Infinite Flight Live isn't initialized. Please contact staff.", ephemeral=True)
                return
            async with await manager.get_client(int(pilot["id"])) as client:
                aircraft = await client.list_organization_aircraft(ORG_ID)
            if not aircraft:
                await interaction.followup.send("No aircraft were returned for Qatari Virtual. Check the API account's organization access.", ephemeral=True)
                return
            aircraft = sorted(aircraft, key=lambda a: str(_get(a, "registration", "")))
            await interaction.followup.send("Choose the aircraft you want to request:", view=AircraftSelectView(self, pilot, aircraft), ephemeral=True)
        except Exception:
            log.exception("Could not load aircraft for /request-flight")
            await interaction.followup.send("I couldn't load Qatari Virtual's aircraft from Infinite Flight. Please contact staff; no assignment was made.", ephemeral=True)

    async def show_aircraft_route(self, interaction, pilot, aircraft):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            manager = getattr(self.bot, "if_live_manager", None)
            async with await manager.get_client(int(pilot["id"])) as client:
                position = await client.get_aircraft_position(str(_get(aircraft, "id")))
                schedules = await client.list_aircraft_schedules(str(_get(aircraft, "id")))
            if not schedules:
                await interaction.followup.send(f"**{_get(aircraft, 'registration', 'Aircraft')}** has no scheduled route available to request.", ephemeral=True)
                return
            # IMPORTANT: validate sequence ordering against the organization's website before production use.
            schedules = sorted(schedules, key=lambda s: int(_get(s, "sequence", 0)))
            top = schedules[0]
            crew = _get(top, "crew", []) or []
            route = f"**{_get(top, 'callsign', 'Unknown')}** — {_get(top, 'origin_icao', '???')} → {_get(top, 'destination_icao', '???')}"
            state = _get(position, "state", "unknown")
            ground = _get(position, "is_on_ground", None)
            status = f"Aircraft state: `{state}` · " + ("On ground" if ground is True else "In flight" if ground is False else "Ground status unknown")
            if crew:
                names = [str(_get(m, "username", _get(m, "userName", _crew_user_id(m)))) for m in crew]
                embed = discord.Embed(title=f"{_get(aircraft, 'registration', 'Aircraft')} · Top scheduled route", description=route, color=discord.Color.orange())
                embed.add_field(name="Assignment", value="Already assigned: " + ", ".join(names), inline=False)
                embed.add_field(name="Aircraft status", value=status, inline=False)
                embed.set_footer(text="This route is not available to claim while assigned.")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            embed = discord.Embed(title=f"Request flight · {_get(aircraft, 'registration', 'Aircraft')}", description=route, color=discord.Color.blue())
            embed.add_field(name="Aircraft status", value=status, inline=False)
            embed.add_field(name="Rules acknowledgement", value=RULES, inline=False)
            embed.set_footer(text="Confirm only if you understand and agree to these rules.")
            view = ClaimRouteView(self, pilot, aircraft, top, pilot["ifuserid"])
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        except Exception:
            log.exception("Could not inspect selected aircraft schedule")
            await interaction.followup.send("I couldn't verify the aircraft or its top route. Nothing was assigned; please contact staff.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(RequestFlightCog(bot))

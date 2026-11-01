"""Pilot Re-engagement system targeting inactive Fleet pilots with Ready Now aircraft.

Follows a 3-wave re-nudge cycle spaced 1 hour apart, followed by an 8-hour wait.
Targeted at Middle Eastern peak hours (UTC+3) prioritizing pilots currently active in Discord.
"""

from datetime import datetime, timedelta, timezone
import asyncio
import logging
import os
import random
from typing import Any, Dict, List, Optional, Set

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.airline_hubs import (
    RETIRED_REGISTRATIONS,
    hubs_for_registration,
    is_cargo_registration,
)
from cogs.available_aircraft_cog import (
    _active_schedules,
    _aircraft_model,
    _get,
    _is_unavailable_status,
    _next_claimable_index,
)
from cogs.dm_logging import DM_LOG_CHANNEL_ID, post_dm_report
from cogs.request_flight_cog import (
    _build_cargo_route_chain,
    _build_connected_route_chain,
    _connected_routes,
    _crew_user_id,
    _schedules_for_aircraft,
)

log = logging.getLogger(__name__)

ORG_ID = os.getenv("IF_ORGANIZATION_ID", "").strip()
TOKEN_OWNER_ID = 1
FLEET_ROLE_ID = int(os.getenv("STOPPED_FLIGHT_FLEET_ROLE_ID", "0") or 0)
LOG_CHANNEL_ID = int(
    os.getenv("STOPPED_FLIGHT_DM_LOG_CHANNEL_ID")
    or os.getenv("STOPPED_FLIGHT_STAFF_CHANNEL_ID")
    or os.getenv("AIRCRAFT_STATUS_LOG_CHANNEL_ID")
    or os.getenv("FLIGHT_TRACKER_CHANNEL_ID")
    or "0"
)
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
QATAR_MAROON = 0x8A1538

# Timezone & Schedule configuration (Arabia Standard Time = UTC+3)
AST_OFFSET_HOURS = 3
MORNING_SESSION_HOUR_AST = int(os.getenv("REENGAGEMENT_MORNING_HOUR_AST", "10") or 10)  # 10:00 AM AST
EVENING_SESSION_HOUR_AST = int(os.getenv("REENGAGEMENT_EVENING_HOUR_AST", "20") or 20)  # 08:00 PM AST
WAVE_INTERVAL_MINUTES = 60
SESSION_COOLDOWN_HOURS = 8
MAX_WAVES = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now_ast() -> datetime:
    """Current time in Arabia Standard Time (UTC+3)."""
    return datetime.now(timezone(timedelta(hours=AST_OFFSET_HOURS)))


class ReengagementPromptView(discord.ui.View):
    """Interactive view attached to the re-engagement DM without emojis."""

    def __init__(
        self,
        aircraft_id: str = "",
        schedule_id: str = "",
        registration: str = "",
        route_str: str = "",
        cog: Optional[Any] = None,
    ):
        super().__init__(timeout=None)
        self.aircraft_id = aircraft_id
        self.schedule_id = schedule_id
        self.registration = registration
        self.route_str = route_str
        self.cog = cog

    @discord.ui.button(
        label="Request & Fly This Route",
        style=discord.ButtonStyle.success,
        custom_id="reengage:request_flight",
    )
    async def request_flight_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer(ephemeral=True)

        model = getattr(interaction.client, "reengagement_model", None)
        pilots_model = getattr(interaction.client, "pilots_model", None)
        manager = getattr(interaction.client, "if_live_manager", None)

        if not pilots_model:
            await interaction.followup.send(
                "Pilot database service is currently unavailable. Please try again shortly.",
                ephemeral=True,
            )
            return

        pilot = await pilots_model.get_pilot_by_discord_id(str(interaction.user.id))
        if not pilot or not pilot.get("ifuserid"):
            await interaction.followup.send(
                "Your Discord account is not linked to an active Infinite Flight pilot record. Please link your account or contact a staff member.",
                ephemeral=True,
            )
            return

        aircraft_id = self.aircraft_id
        registration = self.registration
        if (not aircraft_id or not registration) and model and interaction.message:
            try:
                nudge_row = await model.get_nudge_by_message_id(str(interaction.message.id))
                if nudge_row:
                    aircraft_id = aircraft_id or str(nudge_row.get("aircraft_id") or "")
                    registration = registration or str(nudge_row.get("registration") or "")
            except Exception:
                log.exception("Could not look up nudge record from message id")

        if not aircraft_id or not registration:
            await interaction.followup.send(
                "Aircraft details could not be resolved. Please use `/request-flight` to book manually.",
                ephemeral=True,
            )
            return

        if not manager:
            await interaction.followup.send(
                "Infinite Flight live manager is currently unavailable. Please try again shortly.",
                ephemeral=True,
            )
            return

        cog = self.cog or interaction.client.get_cog("PilotReengagementCog")

        try:
            async with await manager.get_client(TOKEN_OWNER_ID) as client:
                raw_schedules = await client.list_aircraft_schedules(aircraft_id)
                schedules = _schedules_for_aircraft(_active_schedules(raw_schedules), aircraft_id)

                if not schedules:
                    await interaction.followup.send(
                        f"**{registration}** has no active scheduled routes. Please check the Flight Board or contact staff.",
                        ephemeral=True,
                    )
                    return

                first_leg = schedules[0]
                if _is_unavailable_status(first_leg) or bool(_get(first_leg, "crew", []) or []):
                    button.disabled = True
                    button.label = "No Longer Available"
                    if interaction.message:
                        try:
                            await interaction.message.edit(view=self)
                        except Exception:
                            pass
                    if cog and hasattr(cog, "expire_aircraft_nudges"):
                        try:
                            await cog.expire_aircraft_nudges(
                                registration=registration,
                                reason="This flight has already been claimed by another pilot.",
                            )
                        except Exception:
                            log.exception("Could not expire nudge messages for %s", registration)
                    await interaction.followup.send(
                        f"The upcoming flight for **{registration}** has already been claimed by another pilot or is no longer available.",
                        ephemeral=True,
                    )
                    return

                # Build chain of legs (outbound + inbound)
                is_cargo = is_cargo_registration(registration)
                hubs = hubs_for_registration(registration)
                if is_cargo:
                    routes_to_assign = _build_cargo_route_chain(schedules, start_index=0, max_routes=2)
                else:
                    routes_to_assign = _build_connected_route_chain(schedules, start_index=0, hub_icao=hubs)

                if not routes_to_assign:
                    routes_to_assign = [first_leg]
                    if (
                        len(schedules) > 1
                        and not bool(_get(schedules[1], "crew", []) or [])
                        and not _is_unavailable_status(schedules[1])
                        and _connected_routes(first_leg, schedules[1])
                    ):
                        routes_to_assign.append(schedules[1])

                for r in routes_to_assign:
                    if bool(_get(r, "crew", []) or []) or _is_unavailable_status(r):
                        await interaction.followup.send(
                            f"One or more routes for **{registration}** are no longer available. Nothing was assigned; please run `/request-flight`.",
                            ephemeral=True,
                        )
                        return

                pilot_if_user_id = str(pilot["ifuserid"])
                assigned_routes = []
                for current in routes_to_assign:
                    r_id = str(_get(current, "id"))
                    updated = await client.assign_crew(
                        r_id,
                        pilot_if_user_id,
                        0,  # Captain role
                    )
                    crew = _get(updated, "crew", []) or []
                    if not any(_crew_user_id(m) == pilot_if_user_id for m in crew):
                        log.error("Pilot %s was not verified on route %s", pilot_if_user_id, r_id)
                        break
                    assigned_routes.append(current)

                if not assigned_routes:
                    await interaction.followup.send(
                        "Could not complete the flight assignment in Infinite Flight. Please try again or contact staff.",
                        ephemeral=True,
                    )
                    return

        except Exception as e:
            log.exception("Error assigning re-engagement flight for %s", registration)
            await interaction.followup.send(
                f"An error occurred while booking this flight: {e}. Please contact a staff member or try `/request-flight`.",
                ephemeral=True,
            )
            return

        # Update nudge status to 'claimed'
        if model and interaction.message:
            try:
                await model.update_nudge_status(str(interaction.message.id), "claimed")
            except Exception:
                log.exception("Could not update nudge click status")

        # Terminate active campaign for this aircraft since it's now booked
        if cog and hasattr(cog, "_active_campaigns") and registration in cog._active_campaigns:
            del cog._active_campaigns[registration]
            cog._last_session_finished_at = _utcnow()

        # Disable button on message
        button.disabled = True
        button.label = "Claimed & Booked"
        if interaction.message:
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass

        pilot_callsign = pilot.get("callsign", "Captain")

        # Expire any other sent DMs for this aircraft so other pilots see it is booked
        if cog and hasattr(cog, "expire_aircraft_nudges"):
            try:
                await cog.expire_aircraft_nudges(
                    registration=registration,
                    reason=f"This flight has been booked by Captain {pilot_callsign}.",
                    exclude_message_id=str(interaction.message.id) if interaction.message else None,
                )
            except Exception:
                log.exception("Could not expire other nudge messages for %s", registration)
        route_lines = "\n".join(
            f"• **{_get(r, 'callsign') or registration}**: {_get(r, 'origin_icao')} → {_get(r, 'destination_icao')}"
            for r in assigned_routes
        )

        await interaction.followup.send(
            f"Flight booked successfully, Captain {pilot_callsign}!\n\n"
            f"You have been assigned as Captain to **{registration}** for the following leg(s):\n"
            f"{route_lines}\n\n"
            "Welcome back to the skies!",
            ephemeral=True,
        )

        staff_lines = [
            "**Pilot Re-engagement Claimed**",
            f"• **Pilot:** <@{interaction.user.id}> ({pilot_callsign})",
            f"• **Aircraft:** **{registration}**",
            f"• **Assigned Legs ({len(assigned_routes)}):**",
            route_lines,
        ]
        if cog and hasattr(cog, "_post_staff_report"):
            try:
                await cog._post_staff_report("\n".join(staff_lines))
            except Exception:
                log.exception("Could not post staff report for claimed re-engagement flight")

    @discord.ui.button(
        label="Snooze for 30 Days",
        style=discord.ButtonStyle.secondary,
        custom_id="reengage:snooze",
    )
    async def snooze_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        model = getattr(interaction.client, "reengagement_model", None)
        if model:
            try:
                await model.snooze_pilot(
                    discord_user_id=str(interaction.user.id),
                    snooze_days=30,
                )
            except Exception:
                log.exception("Could not record pilot snooze")

        button.disabled = True
        self.stop()
        if interaction.message:
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass

        await interaction.response.send_message(
            "Re-engagement alerts snoozed for **30 days**. We won't nudge you while you're taking time off. "
            "Take care, and welcome back whenever you're ready!",
            ephemeral=True,
        )


class PilotReengagementCog(commands.Cog):
    """Cog managing 3-wave pilot re-engagement for Ready Now aircraft."""

    def __init__(self, bot: commands.Bot, auto_start: bool = True):
        self.bot = bot
        # In-memory campaign state: registration -> campaign_info
        # {
        #    "schedule_id": str,
        #    "wave": int (1, 2, 3),
        #    "wave_sent_at": datetime,
        #    "contacted_pilot_ids": set[str],
        #    "aircraft_info": dict,
        # }
        self._active_campaigns: Dict[str, Dict[str, Any]] = {}
        self._last_session_finished_at: Optional[datetime] = None

        if auto_start:
            try:
                self.reengagement_loop.start()
            except RuntimeError:
                pass

    def cog_unload(self):
        self.reengagement_loop.cancel()

    @tasks.loop(minutes=5)
    async def reengagement_loop(self):
        """Background monitor running every 5 minutes to manage wave follow-ups and sessions."""
        try:
            await self._process_reengagement_cycle()
        except Exception:
            log.exception("Error in pilot re-engagement monitor cycle")

    @reengagement_loop.before_loop
    async def before_reengagement_loop(self):
        await self.bot.wait_until_ready()

    async def _post_staff_report(self, report_text: str) -> bool:
        """Post a summary report to the designated staff/DM log channel."""
        posted = await post_dm_report(
            self.bot,
            report_text,
            log,
            "pilot-reengagement-wave",
        )
        if not posted and LOG_CHANNEL_ID:
            try:
                channel = self.bot.get_channel(LOG_CHANNEL_ID)
                if channel is None:
                    channel = await self.bot.fetch_channel(LOG_CHANNEL_ID)
                if channel:
                    await channel.send(
                        report_text,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return True
            except Exception:
                log.exception("Could not post re-engagement report to channel %s", LOG_CHANNEL_ID)
        return posted

    async def expire_aircraft_nudges(
        self,
        registration: str,
        reason: str = "This flight has already been claimed by another pilot.",
        exclude_message_id: Optional[str] = None,
    ) -> int:
        """Edit previously sent DMs for this aircraft to show it has been taken and disable their buttons."""
        model = getattr(self.bot, "reengagement_model", None)
        if not model:
            return 0

        try:
            active_nudges = await model.get_active_nudges_for_registration(registration)
        except Exception:
            log.exception("Could not fetch active nudges to expire for %s", registration)
            return 0

        if not active_nudges:
            return 0

        expired_ids = []
        for nudge in active_nudges:
            msg_id_str = str(nudge.get("message_id") or "")
            chan_id_str = str(nudge.get("channel_id") or "")
            if not msg_id_str or not chan_id_str:
                continue
            if exclude_message_id and msg_id_str == str(exclude_message_id):
                continue

            expired_ids.append(nudge["id"])

            try:
                channel = self.bot.get_channel(int(chan_id_str))
                if channel is None:
                    channel = await self.bot.fetch_channel(int(chan_id_str))
                if not channel:
                    continue

                msg = await channel.fetch_message(int(msg_id_str))
                if not msg:
                    continue

                expired_embeds = []
                for emb in msg.embeds:
                    emb_dict = emb.to_dict()
                    orig_title = emb_dict.get("title", "Aircraft Departure")
                    if not orig_title.startswith("[UNAVAILABLE]"):
                        emb_dict["title"] = f"[UNAVAILABLE] {orig_title}"
                    emb_dict["color"] = 0x72767D  # Neutral Discord gray
                    orig_desc = emb_dict.get("description", "")
                    if "Notice:" not in orig_desc:
                        emb_dict["description"] = f"{orig_desc}\n\n**Notice: {reason}**"
                    emb_dict["footer"] = {
                        "text": "Qatar Airways Virtual · Re-engagement · Flight No Longer Available"
                    }
                    expired_embeds.append(discord.Embed.from_dict(emb_dict))

                disabled_view = discord.ui.View()
                disabled_view.add_item(
                    discord.ui.Button(
                        label="Flight Claimed / Unavailable",
                        style=discord.ButtonStyle.secondary,
                        disabled=True,
                    )
                )

                await msg.edit(
                    embeds=expired_embeds if expired_embeds else discord.utils.MISSING,
                    view=disabled_view,
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log.debug("Could not edit expired re-engagement message %s", msg_id_str)
            except Exception:
                log.warning("Unexpected error expiring re-engagement message %s", msg_id_str, exc_info=True)

        if expired_ids:
            try:
                await model.mark_nudges_expired(expired_ids)
            except Exception:
                log.exception("Could not mark nudges as expired in database for %s", registration)

        return len(expired_ids)

    def _get_ready_now_aircraft(self, fleet_snapshot) -> List[Dict[str, Any]]:
        """Identify aircraft that are Ready now (unassigned first leg, zero queue)."""
        ready_aircraft = []
        for ac in fleet_snapshot:
            registration = str(ac.registration or "").strip().upper()
            norm_reg = registration.replace("-", "")
            if (
                not registration
                or registration in RETIRED_REGISTRATIONS
                or norm_reg in RETIRED_REGISTRATIONS
            ):
                continue

            schedules = _active_schedules(getattr(ac, "schedules", []))
            if not schedules:
                continue

            claim_idx = _next_claimable_index(schedules)
            if claim_idx is None or claim_idx != 0:
                continue

            first_leg = schedules[0]
            origin = str(_get(first_leg, "origin_icao") or "???").upper()
            dest = str(_get(first_leg, "destination_icao") or "???").upper()
            schedule_id = str(_get(first_leg, "id") or "")
            callsign = str(_get(first_leg, "callsign") or registration).upper()

            model_name = ""
            if hasattr(self.bot, "aircraft_name_map") and self.bot.aircraft_name_map:
                model_name = self.bot.aircraft_name_map.get(ac.aircraft_id, "")
            if not model_name:
                model_name = _aircraft_model(ac) or "Aircraft"

            ready_aircraft.append(
                {
                    "aircraft_id": ac.aircraft_id,
                    "registration": registration,
                    "model": model_name,
                    "origin_icao": origin,
                    "destination_icao": dest,
                    "schedule_id": schedule_id,
                    "callsign": callsign,
                    "schedules": schedules,
                }
            )
        return ready_aircraft

    def _build_nudge_embed(
        self,
        candidate: Dict[str, Any],
        aircraft: Dict[str, Any],
        wave: int = 1,
    ) -> discord.Embed:
        """Create a personalized Discord embed without emojis."""
        callsign = candidate.get("callsign", "Captain")
        days = candidate.get("days_inactive", 14)

        title = f"Captain {callsign}, your aircraft is ready for departure!"
        if wave > 1:
            title = f"Captain {callsign}, open flight waiting on the ramp"

        embed = discord.Embed(
            title=title,
            description=(
                f"It's been **{days} days** since your last flight with Qatari Virtual. "
                "An aircraft is fueled and ready on the ramp for immediate departure with **zero wait time**."
            ),
            color=QATAR_MAROON,
            timestamp=datetime.now(timezone.utc),
        )

        route_val = f"**{aircraft['origin_icao']} → {aircraft['destination_icao']}**"
        ac_val = f"**{aircraft['registration']}** ({aircraft['model']})"

        embed.add_field(
            name="Ready Now Aircraft",
            value=f"{ac_val}\nFlight number: `{aircraft['callsign']}`",
            inline=False,
        )
        embed.add_field(
            name="Assigned Route",
            value=route_val,
            inline=True,
        )
        embed.add_field(
            name="Queue Status",
            value="**0 flights ahead** (Ramp Ready)",
            inline=True,
        )

        embed.set_footer(
            text=f"Max 1 alert / week"
        )
        return embed

    def _get_fleet_members(self, default_guild: Optional[discord.Guild] = None) -> Dict[str, discord.Member]:
        """Resolve fleet members from configured role, or fallback to guild members."""
        guild = self.bot.get_guild(GUILD_ID) if GUILD_ID else default_guild
        if not guild and hasattr(self.bot, "guilds") and self.bot.guilds:
            guild = self.bot.guilds[0]
        if not guild:
            return {}

        fleet_role = guild.get_role(FLEET_ROLE_ID) if FLEET_ROLE_ID else None
        if fleet_role and fleet_role.members:
            return {str(m.id): m for m in fleet_role.members if not m.bot}

        # Fallback to guild members if role is not configured or in unit test mocks
        members = getattr(guild, "members", []) or []
        return {str(m.id): m for m in members if not getattr(m, "bot", False)}

    def _select_candidate_for_aircraft(
        self,
        candidates: List[Dict[str, Any]],
        fleet_members: Dict[str, discord.Member],
        already_contacted: Set[str],
    ) -> Optional[Dict[str, Any]]:
        """Select 1 candidate pilot for an aircraft, prioritizing active Discord users with offline fallback."""
        online_pool = []
        offline_pool = []

        for cand in candidates:
            discord_id = str(cand["discordid"])
            if discord_id in already_contacted:
                continue

            member = fleet_members.get(discord_id)
            if not member:
                continue

            cand_with_member = {**cand, "member": member}
            if member.status in (discord.Status.online, discord.Status.idle, discord.Status.dnd):
                online_pool.append(cand_with_member)
            else:
                offline_pool.append(cand_with_member)

        # Prioritize online candidates; fallback to offline if no online candidates exist
        if online_pool:
            return random.choice(online_pool)
        elif offline_pool:
            return random.choice(offline_pool)
        return None

    def _is_session_time_ast(self) -> bool:
        """Check if current time in Qatar / AST is within morning or evening launch windows."""
        now_ast = _now_ast()
        hour = now_ast.hour

        # Morning window: e.g. 10:00 to 11:00 AST
        # Evening window: e.g. 20:00 to 21:00 AST
        is_morning = hour == MORNING_SESSION_HOUR_AST
        is_evening = hour == EVENING_SESSION_HOUR_AST
        return is_morning or is_evening

    async def _process_reengagement_cycle(self):
        """Main processing loop: handles Wave 2/3 follow-ups, and launches Wave 1 at session times."""
        manager = getattr(self.bot, "if_live_manager", None)
        model = getattr(self.bot, "reengagement_model", None)
        if not manager or not model:
            return

        now = _utcnow()

        # 1. Check live fleet state
        try:
            fleet_snapshot = await manager.get_live_fleet(ORG_ID, pilot_id=TOKEN_OWNER_ID)
            ready_aircraft = self._get_ready_now_aircraft(fleet_snapshot)
        except Exception:
            log.exception("Error checking live fleet in re-engagement cycle")
            return

        ready_by_reg = {ac["registration"]: ac for ac in ready_aircraft}

        # 2. Check and clean up existing campaigns
        finished_regs = []
        followup_needed: List[Dict[str, Any]] = []

        for reg, campaign in list(self._active_campaigns.items()):
            current_ready = ready_by_reg.get(reg)

            # If the aircraft is no longer ready now or its schedule changed/claimed, campaign terminates
            if not current_ready or current_ready["schedule_id"] != campaign["schedule_id"]:
                log.info("Re-engagement campaign for %s ended (claimed or departed).", reg)
                finished_regs.append(reg)
                continue

            # Check if 1 hour has elapsed since the last wave
            elapsed = (now - campaign["wave_sent_at"]).total_seconds()
            if elapsed >= (WAVE_INTERVAL_MINUTES * 60):
                next_wave = campaign["wave"] + 1
                if next_wave <= MAX_WAVES:
                    followup_needed.append(
                        {
                            "registration": reg,
                            "aircraft": current_ready,
                            "campaign": campaign,
                            "next_wave": next_wave,
                        }
                    )
                else:
                    log.info("Re-engagement campaign for %s completed all %s waves.", reg, MAX_WAVES)
                    finished_regs.append(reg)

        for reg in finished_regs:
            del self._active_campaigns[reg]
            self._last_session_finished_at = now
            try:
                await self.expire_aircraft_nudges(
                    registration=reg,
                    reason="This aircraft is no longer available on the ramp (claimed or departed).",
                )
            except Exception:
                log.exception("Error expiring nudges for finished campaign %s", reg)

        # 3. Query inactive pilot candidates
        try:
            candidates = await model.get_inactive_pilot_candidates(min_days=14, max_days=180, cooldown_days=7)
        except Exception:
            log.exception("Error querying inactive pilots in re-engagement cycle")
            return

        fleet_members = self._get_fleet_members()

        global_contacted_in_tick: Set[str] = set()

        # 4. Handle follow-up waves (Wave 2 and Wave 3)
        if followup_needed:
            delivered_followups = []
            failed_followups = []

            for item in followup_needed:
                reg = item["registration"]
                ac = item["aircraft"]
                camp = item["campaign"]
                wave = item["next_wave"]

                already_contacted = camp["contacted_pilot_ids"].union(global_contacted_in_tick)
                cand = self._select_candidate_for_aircraft(candidates, fleet_members, already_contacted)

                if not cand:
                    log.info("No eligible candidates available for follow-up wave %s on %s.", wave, reg)
                    continue

                discord_id = str(cand["discordid"])
                global_contacted_in_tick.add(discord_id)
                camp["contacted_pilot_ids"].add(discord_id)

                success, err = await self._send_nudge(cand, ac, model, wave=wave)
                if success:
                    camp["wave"] = wave
                    camp["wave_sent_at"] = now
                    presence_label = "online" if cand["member"].status != discord.Status.offline else "offline"
                    delivered_followups.append(
                        f"• <@{discord_id}> ({cand['callsign']}) -> **{reg}** ({ac['origin_icao']} → {ac['destination_icao']}) [Discord: {presence_label}]"
                    )
                else:
                    failed_followups.append(f"• <@{discord_id}> ({cand['callsign']}): {err}")

            if delivered_followups or failed_followups:
                report_lines = [
                    f"**Pilot Re-engagement Follow-up Report**",
                    f"**Delivered ({len(delivered_followups)}):**",
                    *(delivered_followups if delivered_followups else ["None"]),
                ]
                if failed_followups:
                    report_lines.extend(["**Failed:**", *failed_followups])
                await self._post_staff_report("\n".join(report_lines))

        # 5. Check if it's time to start a NEW session (Wave 1)
        # Conditions:
        # a) No active campaigns running
        # b) Either no previous session, or at least 8 hours passed since previous session
        # c) Current time in Qatar / AST is at session start window (10:00 AST or 20:00 AST)
        has_active_campaigns = bool(self._active_campaigns)
        time_since_last_session = (
            (now - self._last_session_finished_at).total_seconds()
            if self._last_session_finished_at
            else float("inf")
        )

        should_start_session = (
            not has_active_campaigns
            and time_since_last_session >= (SESSION_COOLDOWN_HOURS * 3600)
            and self._is_session_time_ast()
        )

        if should_start_session and ready_aircraft:
            log.info("Starting new scheduled re-engagement session (Wave 1)...")
            await self._dispatch_wave_1(ready_aircraft, candidates, fleet_members, model)

    async def _dispatch_wave_1(
        self,
        ready_aircraft: List[Dict[str, Any]],
        candidates: List[Dict[str, Any]],
        fleet_members: Dict[str, discord.Member],
        model: Any,
    ) -> Dict[str, Any]:
        """Dispatch Wave 1: 1 pilot per ready aircraft, active Discord users prioritized."""
        now = _utcnow()
        delivered = []
        failed = []
        contacted_in_wave: Set[str] = set()

        for ac in ready_aircraft:
            reg = ac["registration"]
            cand = self._select_candidate_for_aircraft(candidates, fleet_members, contacted_in_wave)
            if not cand:
                continue

            discord_id = str(cand["discordid"])
            contacted_in_wave.add(discord_id)

            success, err = await self._send_nudge(cand, ac, model, wave=1)
            if success:
                self._active_campaigns[reg] = {
                    "schedule_id": ac["schedule_id"],
                    "wave": 1,
                    "wave_sent_at": now,
                    "contacted_pilot_ids": {discord_id},
                    "aircraft": ac,
                }
                presence_label = "online" if cand["member"].status != discord.Status.offline else "offline"
                delivered.append(
                    f"• <@{discord_id}> ({cand['callsign']}) -> **{reg}** ({ac['origin_icao']} → {ac['destination_icao']}) [Discord: {presence_label}]"
                )
            else:
                failed.append(f"• <@{discord_id}> ({cand['callsign']}): {err}")

        report_lines = [
            f"**Pilot Re-engagement Dispatch Report · Wave 1/3**",
            f"**Ready Now Aircraft:** {len(ready_aircraft)}",
            f"**Delivered ({len(delivered)}):**",
            *(delivered if delivered else ["None"]),
        ]
        if failed:
            report_lines.extend(["**Failed:**", *failed])

        await self._post_staff_report("\n".join(report_lines))

        return {
            "success": True,
            "delivered": delivered,
            "failed": failed,
            "ready_count": len(ready_aircraft),
        }

    async def _send_nudge(
        self,
        candidate: Dict[str, Any],
        aircraft: Dict[str, Any],
        model: Any,
        wave: int = 1,
    ) -> (bool, Optional[str]):
        """Send a single re-engagement nudge DM and log it to the database."""
        discord_id = int(candidate["discordid"])
        route_str = f"{aircraft['origin_icao']} → {aircraft['destination_icao']}"
        embed = self._build_nudge_embed(candidate, aircraft, wave=wave)
        view = ReengagementPromptView(
            aircraft_id=aircraft["aircraft_id"],
            schedule_id=aircraft["schedule_id"],
            registration=aircraft["registration"],
            route_str=route_str,
            cog=self,
        )

        try:
            user = self.bot.get_user(discord_id)
            if user is None:
                user = await self.bot.fetch_user(discord_id)

            msg = await user.send(embed=embed, view=view)

            await model.record_nudge(
                pilot_id=candidate.get("pilot_id"),
                discord_user_id=str(discord_id),
                if_user_id=candidate.get("ifuserid"),
                aircraft_id=aircraft["aircraft_id"],
                registration=aircraft["registration"],
                aircraft_model=aircraft["model"],
                origin_icao=aircraft["origin_icao"],
                destination_icao=aircraft["destination_icao"],
                channel_id=str(msg.channel.id),
                message_id=str(msg.id),
                delivery_status=f"wave_{wave}",
            )
            return True, None
        except Exception as e:
            log.warning("Could not send re-engagement DM to pilot %s: %s", discord_id, e)
            try:
                await model.record_nudge(
                    pilot_id=candidate.get("pilot_id"),
                    discord_user_id=str(discord_id),
                    if_user_id=candidate.get("ifuserid"),
                    aircraft_id=aircraft["aircraft_id"],
                    registration=aircraft["registration"],
                    aircraft_model=aircraft["model"],
                    origin_icao=aircraft["origin_icao"],
                    destination_icao=aircraft["destination_icao"],
                    delivery_status="failed",
                )
            except Exception:
                pass
            return False, str(e)

    @app_commands.command(
        name="dispatch-reengagement",
        description="Manually dispatch or preview Ready Now aircraft re-engagement DMs.",
    )
    @app_commands.describe(
        dry_run="If True, shows what would be sent without messaging pilots",
    )
    async def dispatch_reengagement(
        self,
        interaction: discord.Interaction,
        dry_run: bool = False,
    ):
        await interaction.response.defer(ephemeral=True)

        model = getattr(self.bot, "reengagement_model", None)
        manager = getattr(self.bot, "if_live_manager", None)
        if not model or not manager:
            await interaction.followup.send("Live API or Re-engagement database service is not available.", ephemeral=True)
            return

        try:
            fleet_snapshot = await manager.get_live_fleet(ORG_ID, pilot_id=TOKEN_OWNER_ID)
            ready_aircraft = self._get_ready_now_aircraft(fleet_snapshot)
        except Exception as e:
            await interaction.followup.send(f"Failed to fetch live fleet state: {e}", ephemeral=True)
            return

        if not ready_aircraft:
            await interaction.followup.send("No Ready Now aircraft currently on the ramp.", ephemeral=True)
            return

        try:
            candidates = await model.get_inactive_pilot_candidates(min_days=14, max_days=180, cooldown_days=7)
        except Exception as e:
            await interaction.followup.send(f"Failed to query inactive pilots: {e}", ephemeral=True)
            return

        fleet_members = self._get_fleet_members(interaction.guild)

        if dry_run:
            preview_lines = [
                "**[DRY RUN] Pilot Re-engagement Preview**",
                f"• **Ready Now Aircraft:** {len(ready_aircraft)}",
                f"• **Eligible Inactive Pilots:** {len(candidates)}",
                "\n**Pairings (1 Pilot per Aircraft):**",
            ]
            contacted = set()
            for ac in ready_aircraft:
                cand = self._select_candidate_for_aircraft(candidates, fleet_members, contacted)
                if cand:
                    contacted.add(str(cand["discordid"]))
                    status = "online" if cand["member"].status != discord.Status.offline else "offline"
                    preview_lines.append(
                        f"• <@{cand['discordid']}> ({cand['callsign']}) [{status}] -> **{ac['registration']}** ({ac['origin_icao']} → {ac['destination_icao']})"
                    )
                else:
                    preview_lines.append(f"• **{ac['registration']}**: No remaining candidate pilots available")
            await interaction.followup.send("\n".join(preview_lines), ephemeral=True)
            return

        result = await self._dispatch_wave_1(ready_aircraft, candidates, fleet_members, model)
        delivered_count = len(result.get("delivered", []))
        failed_count = len(result.get("failed", []))
        await interaction.followup.send(
            f"Wave 1 dispatched: {delivered_count} DMs sent, {failed_count} failed. Follow-up waves will check in 1 hour.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    cog = PilotReengagementCog(bot)
    await bot.add_cog(cog)
    bot.add_view(ReengagementPromptView(cog=cog))

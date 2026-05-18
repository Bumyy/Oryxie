import discord
from discord.ext import commands, tasks
from discord import app_commands
from typing import Literal
import json
import os
from datetime import datetime, timezone
import logging

DASHBOARD_THUMBNAIL = "https://cdn.discordapp.com/attachments/1150347101205696562/1499493493494517770/content.png?ex=6a0ac064&is=6a096ee4&hm=adb3fcd36102c274fcadcbf4dda006d210a11bb7933cde0f9d75cf8b226377ea"

CONTINENT_EMOJI = {
    "AS": "🌏",
    "EU": "🇪🇺",
    "NA": "🌎",
    "AF": "🌍",
    "OC": "🌊",
}
CONTINENT_NAME = {
    "AS": "Asia",
    "EU": "Europe",
    "NA": "North America",
    "AF": "Africa",
    "OC": "Oceania",
}

DEST_LABEL = {
    "OEJN": "🇸🇦 Jeddah (OEJN)",
    "OEMA": "🇸🇦 Madinah (OEMA)",
}

def _progress_bar(pct: float, length: int = 10) -> str:
    filled = int(min(100, max(0, pct)) / 100 * length)
    return "▓" * filled + "░" * (length - filled)

def _embed_color(overall_pct: float) -> int:
    if overall_pct >= 66:
        return 0x2ECC71
    elif overall_pct >= 33:
        return 0xF1C40F
    return 0xE74C3C


class HajjOperationsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.hajj_config = self._load_hajj_config()
        self.dashboard_message = None

        self.dashboard_channel_id = 1325788690227920981
        self.staff_log_channel_id = int(os.getenv("CANDY_LOGS_CHANNEL_ID", 0)) or None

        self.hajj_event_name = self.hajj_config.get("hajj_event_name", "HajjOperations")
        self.hajj_currency_name = "Pilgrims"

        self.hajj_event_model = self.bot.event_transaction_model.__class__(
            self.bot.db_manager,
            event_name=self.hajj_event_name,
            currency_name=self.hajj_currency_name
        )

        self.update_dashboard.start()

    def cog_unload(self):
        self.update_dashboard.cancel()

    def _load_hajj_config(self):
        try:
            with open("assets/hajj_config.json", 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading hajj_config.json: {e}")
            return {"initial_pilgrims": {}, "target_airports": [], "hajj_event_name": "HajjOperations"}

    async def _get_current_hajj_state(self):
        initial_pilgrims = self.hajj_config.get("initial_pilgrims", {})
        target_airports = self.hajj_config.get("target_airports", [])

        current_state = {
            "AS": initial_pilgrims.get("AS", 0),
            "EU": initial_pilgrims.get("EU", 0),
            "NA": initial_pilgrims.get("NA", 0),
            "AF": initial_pilgrims.get("AF", 0),
            "OC": initial_pilgrims.get("OC", 0),
            "OTHH_TRANSITION": 0,
        }
        for airport in target_airports:
            current_state[f"{airport}_ARRIVED"] = 0

        all_transactions = await self.hajj_event_model.get_all_records_for_event(
            self.hajj_event_name, self.hajj_currency_name
        )

        for transaction in all_transactions:
            reason = transaction['reason']
            amount = transaction['amount']
            if "Pilgrims from AS" in reason:       current_state["AS"] += amount
            elif "Pilgrims from EU" in reason:     current_state["EU"] += amount
            elif "Pilgrims from NA" in reason:     current_state["NA"] += amount
            elif "Pilgrims from AF" in reason:     current_state["AF"] += amount
            elif "Pilgrims from OC" in reason:     current_state["OC"] += amount
            elif "Pilgrims to OTHH" in reason:     current_state["OTHH_TRANSITION"] += amount
            elif "Pilgrims from OTHH" in reason:   current_state["OTHH_TRANSITION"] += amount
            elif "Pilgrims to OEJN" in reason:     current_state["OEJN_ARRIVED"] += amount
            elif "Pilgrims to OEMA" in reason:     current_state["OEMA_ARRIVED"] += amount

        return current_state

    def _build_embed(self, current_state: dict) -> discord.Embed:
        initial_pilgrims = self.hajj_config.get("initial_pilgrims", {})
        target_airports = self.hajj_config.get("target_airports", [])

        total_initial = sum(initial_pilgrims.values())
        total_arrived = sum(current_state.get(f"{ap}_ARRIVED", 0) for ap in target_airports)
        overall_pct = (total_arrived / total_initial * 100) if total_initial > 0 else 0

        embed = discord.Embed(
            title="🕋 HAJJ OPERATIONS 2026",
            description=(
                "A virtual pilgrimage operation uniting pilots worldwide.\n"
                "Fly pilgrims from every corner of the earth to the Holy Land.\n"
                "Track the journey from departure to final destination below."
            ),
            color=_embed_color(overall_pct)
        )
        embed.set_image(url=DASHBOARD_THUMBNAIL)

        # --- Continent pools ---
        continent_lines = []
        for code, initial in initial_pilgrims.items():
            remaining = current_state.get(code, initial)
            emoji = CONTINENT_EMOJI.get(code, "🌐")
            name = CONTINENT_NAME.get(code, code)

            if remaining <= 0:
                overbooked = abs(remaining)
                status = "All pilgrims departed ✅"
                if overbooked > 0:
                    status += f"\n+{overbooked:,} additional demand 🎟️"
                continent_lines.append(f"{emoji} **{name}**\n{status}")
            else:
                pct = (remaining / initial * 100) if initial > 0 else 0
                bar = _progress_bar(pct)
                continent_lines.append(
                    f"{emoji} **{name}**\n"
                    f"`{bar}` {remaining:,} / {initial:,} ({pct:.1f}%)"
                )

        embed.add_field(
            name="🌍 Pilgrims Remaining by Continent",
            value="\n\n".join(continent_lines),
            inline=False
        )

        # --- OTHH Transit ---
        othh_count = current_state.get("OTHH_TRANSITION", 0)
        embed.add_field(
            name="🇶🇦 OTHH Transit Pool",
            value=f"**{othh_count:,}** pilgrims awaiting onward flight from Doha",
            inline=False
        )

        # --- Final destinations ---
        dest_lines = []
        for airport in target_airports:
            arrived = current_state.get(f"{airport}_ARRIVED", 0)
            label = DEST_LABEL.get(airport, f"🕌 {airport}")
            dest_lines.append(f"{label}: **{arrived:,}** pilgrims arrived")
        embed.add_field(
            name="🕌 Final Destinations",
            value="\n".join(dest_lines) if dest_lines else "None yet",
            inline=False
        )

        # --- Overall progress ---
        overall_bar = _progress_bar(min(overall_pct, 100))
        if overall_pct >= 100:
            overall_value = (
                f"`{overall_bar}` **100%**\n"
                f"🌙 **Hajj Operations Complete** — All pilgrims have reached their destination. 🕋"
            )
        else:
            overall_value = (
                f"`{overall_bar}` **{overall_pct:.2f}%**\n"
                f"{total_arrived:,} / {total_initial:,} pilgrims reached final destination"
            )
        embed.add_field(name="📊 Overall Progress", value=overall_value, inline=False)

        now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        embed.set_footer(text=f"Hajj Operations 2026 • Last updated: {now_utc}")

        return embed

    @tasks.loop(hours=1)
    async def update_dashboard(self):
        await self.bot.wait_until_ready()
        if not self.dashboard_channel_id:
            return
        dashboard_channel = self.bot.get_channel(self.dashboard_channel_id)
        if not dashboard_channel:
            logging.warning(f"Hajj dashboard channel {self.dashboard_channel_id} not found.")
            return

        staff_log_channel = self.bot.get_channel(self.staff_log_channel_id) if self.staff_log_channel_id else None
        try:
            all_pireps = await self.bot.pireps_model.get_accepted_pireps()
            processed_count = 0
            for pirep_data in all_pireps:
                if await self._process_hajj_pirep(pirep_data, staff_log_channel):
                    processed_count += 1
            if processed_count > 0:
                logging.info(f"HajjOperationsCog processed {processed_count} new Hajj PIREPs.")
        except Exception as e:
            logging.error(f"Error processing Hajj PIREPs: {e}")

        if not self.dashboard_message:
            self.dashboard_message = await self._find_pinned_dashboard(dashboard_channel)

        current_state = await self._get_current_hajj_state()
        embed = self._build_embed(current_state)

        if self.dashboard_message:
            try:
                await self.dashboard_message.edit(embed=embed)
            except discord.NotFound:
                self.dashboard_message = await dashboard_channel.send(embed=embed)
                await self.dashboard_message.pin()
            except Exception as e:
                logging.error(f"Error editing Hajj dashboard: {e}")
        else:
            self.dashboard_message = await dashboard_channel.send(embed=embed)
            await self.dashboard_message.pin()

    async def _find_pinned_dashboard(self, channel: discord.TextChannel):
        try:
            pins = await channel.pins()
            for msg in pins:
                if (msg.author == self.bot.user
                        and msg.embeds
                        and msg.embeds[0].title == "🕋 HAJJ OPERATIONS 2026"):
                    logging.info("Hajj dashboard: found existing pinned embed.")
                    return msg
        except Exception as e:
            logging.error(f"Error scanning pinned messages: {e}")
        return None

    async def _process_hajj_pirep(self, pirep_data: dict, staff_log_channel: discord.TextChannel) -> bool:
        flight_number = pirep_data.get('flightnum', '').upper()
        if not flight_number.startswith("HAJJOPS"):
            return False

        try:
            pilgrim_count = int(flight_number[len("HAJJOPS"):])
            if pilgrim_count <= 0:
                return False
        except ValueError:
            return False

        pilot_id = pirep_data['pilotid']
        pirep_id = pirep_data['pirep_id']
        departure_icao = pirep_data['departure'].upper()
        arrival_icao = pirep_data['arrival'].upper()
        target_airports = self.hajj_config.get("target_airports", [])

        if await self.hajj_event_model.check_duplicate(pilot_id, f"HajjOps%PIREP #{pirep_id}%"):
            return False

        pilot_data = await self.bot.pilots_model.get_pilot_by_id(pilot_id)
        pilot_callsign = pilot_data['callsign'] if pilot_data else f"Pilot ID: {pilot_id}"
        pilot_mention = f"<@{pilot_data['discordid']}>" if pilot_data and pilot_data.get('discordid') else f"**{pilot_callsign}**"

        is_phase1 = arrival_icao == "OTHH"
        is_phase2 = departure_icao == "OTHH" and arrival_icao in target_airports
        is_phase3 = departure_icao != "OTHH" and arrival_icao in target_airports

        if is_phase1:
            continent_code = self.bot.flightdata.get_continent_from_icao(departure_icao)
            if not continent_code:
                if staff_log_channel:
                    await staff_log_channel.send(
                        f"☪️ **Hajj Flight Error**\n"
                        f"{pilot_mention} ({pilot_callsign}) filed PIREP #{pirep_id} but continent for {departure_icao} could not be determined. Skipping."
                    )
                return False
            await self.hajj_event_model.add_transaction(
                pilot_id, -pilgrim_count, f"HajjOps: Pilgrims from {continent_code} (PIREP #{pirep_id})"
            )
            await self.hajj_event_model.add_transaction(
                pilot_id, pilgrim_count, f"HajjOps: Pilgrims to OTHH (PIREP #{pirep_id})"
            )
            continent_name = CONTINENT_NAME.get(continent_code, continent_code)
            log_message = (
                f"☪️ **Sacred Journey Completed**\n\n"
                f"{pilot_mention} ({pilot_callsign}) has safely transported **{pilgrim_count:,} pilgrims** "
                f"from the skies of {departure_icao} ({continent_name}), landing them at Doha (OTHH) — one step closer to their destination.\n\n"
                f"PIREP #{pirep_id}"
            )

        elif is_phase2:
            await self.hajj_event_model.add_transaction(
                pilot_id, -pilgrim_count, f"HajjOps: Pilgrims from OTHH (PIREP #{pirep_id})"
            )
            await self.hajj_event_model.add_transaction(
                pilot_id, pilgrim_count, f"HajjOps: Pilgrims to {arrival_icao} (PIREP #{pirep_id})"
            )
            dest_name = "Makkah" if arrival_icao == "OEMA" else "Jeddah"
            log_message = (
                f"☪️ **Pilgrims Arrive at the Holy Land**\n\n"
                f"{pilot_mention} ({pilot_callsign}) has delivered **{pilgrim_count:,} pilgrims** "
                f"from Doha (OTHH) to {dest_name} ({arrival_icao}).\n\n"
                f"PIREP #{pirep_id}"
            )

        elif is_phase3:
            continent_code = self.bot.flightdata.get_continent_from_icao(departure_icao)
            if not continent_code:
                if staff_log_channel:
                    await staff_log_channel.send(
                        f"☪️ **Hajj Flight Error**\n"
                        f"{pilot_mention} ({pilot_callsign}) filed PIREP #{pirep_id} but continent for {departure_icao} could not be determined. Skipping."
                    )
                return False
            await self.hajj_event_model.add_transaction(
                pilot_id, -pilgrim_count, f"HajjOps: Pilgrims from {continent_code} (PIREP #{pirep_id})"
            )
            await self.hajj_event_model.add_transaction(
                pilot_id, pilgrim_count, f"HajjOps: Pilgrims to {arrival_icao} (PIREP #{pirep_id})"
            )
            continent_name = CONTINENT_NAME.get(continent_code, continent_code)
            dest_name = "Makkah" if arrival_icao == "OEMA" else "Jeddah"
            log_message = (
                f"☪️ **Direct Flight to the Holy Land**\n\n"
                f"{pilot_mention} ({pilot_callsign}) has carried **{pilgrim_count:,} pilgrims** "
                f"directly from the skies of {departure_icao} ({continent_name}) to {dest_name} ({arrival_icao}).\n\n"
                f"PIREP #{pirep_id}"
            )

        else:
            return False

        if staff_log_channel:
            await staff_log_channel.send(log_message)
        logging.info(f"HajjOps PIREP #{pirep_id} processed for {pilot_callsign}.")
        return True

    @app_commands.command(name="hajj_dashboard_update", description="Manually update the Hajj Operations dashboard.")
    @app_commands.checks.has_permissions(administrator=True)
    async def hajj_dashboard_update_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.update_dashboard()
        await interaction.followup.send("✅ Hajj dashboard updated.", ephemeral=True)

    @app_commands.command(name="hajj_admin", description="Admin commands for Hajj Operations event.")
    @app_commands.describe(
        action="The action to perform (add/remove pilgrims)",
        pilot_identifier="Pilot's Discord ID or Callsign",
        amount="Number of pilgrims to add or remove",
        pool="The pilgrim pool to affect",
        reason="Reason for the manual adjustment"
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Add Pilgrims", value="add"),
            app_commands.Choice(name="Remove Pilgrims", value="remove"),
        ],
        pool=[
            app_commands.Choice(name="Asia (AS)", value="AS"),
            app_commands.Choice(name="Europe (EU)", value="EU"),
            app_commands.Choice(name="North America (NA)", value="NA"),
            app_commands.Choice(name="Africa (AF)", value="AF"),
            app_commands.Choice(name="Oceania (OC)", value="OC"),
            app_commands.Choice(name="OTHH Transition", value="OTHH_TRANSITION"),
            app_commands.Choice(name="OEJN Arrived", value="OEJN_ARRIVED"),
            app_commands.Choice(name="OEMA Arrived", value="OEMA_ARRIVED"),
        ]
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def hajj_admin(
        self,
        interaction: discord.Interaction,
        action: Literal["add", "remove"],
        pilot_identifier: str,
        amount: int,
        pool: Literal["AS", "EU", "NA", "AF", "OC", "OTHH_TRANSITION", "OEJN_ARRIVED", "OEMA_ARRIVED"],
        reason: str
    ):
        await interaction.response.defer(ephemeral=True)

        if amount <= 0:
            await interaction.followup.send("Amount must be a positive number.", ephemeral=True)
            return

        pilot_data = None
        try:
            pilot_data = await self.bot.pilots_model.get_pilot_by_discord_id(str(int(pilot_identifier)))
        except ValueError:
            pilot_data = await self.bot.pilots_model.get_pilot_by_callsign(pilot_identifier.upper())

        if not pilot_data:
            await interaction.followup.send(f"Could not find pilot: `{pilot_identifier}`.", ephemeral=True)
            return

        pilot_id = pilot_data['id']
        pilot_callsign = pilot_data['callsign']
        final_amount = amount if action == "add" else -amount
        transaction_reason = f"Admin {action.capitalize()} Pilgrims to {pool}: {reason} (by {interaction.user.display_name})"

        success = await self.hajj_event_model.add_transaction(pilot_id, final_amount, transaction_reason)

        if success:
            log_message = (
                f"✈️ **HajjOps Admin Adjustment:**\n"
                f"• Admin: {interaction.user.display_name}\n"
                f"• Pilot: {pilot_callsign} (ID: {pilot_id})\n"
                f"• Action: {action.capitalize()} {amount:,} pilgrims\n"
                f"• Pool: {pool}\n"
                f"• Reason: {reason}\n"
            )
            if self.staff_log_channel_id:
                staff_log_channel = self.bot.get_channel(self.staff_log_channel_id)
                if staff_log_channel:
                    await staff_log_channel.send(log_message)
            await interaction.followup.send(
                f"✅ Successfully {action}ed {amount:,} pilgrims to/from {pool} for `{pilot_callsign}`.",
                ephemeral=True
            )
        else:
            await interaction.followup.send("❌ Failed to record transaction.", ephemeral=True)


async def setup(bot: commands.Bot):
    if not hasattr(bot, 'db_manager'):
        print("ERROR: DatabaseManager not attached to bot. HajjOperationsCog not loaded.")
        return
    if not hasattr(bot, 'pilots_model'):
        print("ERROR: PilotsModel not attached to bot. HajjOperationsCog not loaded.")
        return
    if not hasattr(bot, 'flightdata'):
        print("ERROR: Flightdata not attached to bot. HajjOperationsCog not loaded.")
        return
    if not hasattr(bot, 'event_transaction_model'):
        print("ERROR: EventTransactionModel not attached to bot. HajjOperationsCog not loaded.")
        return

    await bot.add_cog(HajjOperationsCog(bot))

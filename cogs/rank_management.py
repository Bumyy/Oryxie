import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
import json
import os

# =============================================================================
# ⚙️ CONFIGURATION
# =============================================================================

# Load configuration from JSON file
def load_rank_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'assets', 'rank_config.json')
    with open(config_path, 'r') as f:
        return json.load(f)

RANK_DATA = load_rank_config()
ASK_FOR_CARGO_CHANNEL_ID = RANK_DATA['ask_for_cargo_channel_id']
QATARI_EMOJI = RANK_DATA['qatari_emoji']
RANK_CONFIG = RANK_DATA['ranks']

# FOOTER TEXTS
FOOTER_STANDARD = f"Thank you for flying with us! Keep the blue skies in sight and happy landings! {QATARI_EMOJI}"
FOOTER_SENIOR   = f"Thank you for your hard work and dedication! On to 500 hours! {QATARI_EMOJI}"
FOOTER_RUBY     = f"Thank you for your incredible loyalty! A true milestone achieved. {QATARI_EMOJI}"
FOOTER_SAPPHIRE = f"One thousand hours in the sky. Simply legendary. Thank you! {QATARI_EMOJI}"
FOOTER_EMERALD  = f"Two thousand five hundred hours. A monumental achievement! {QATARI_EMOJI}"
FOOTER_ONEWORLD = f"The world is truly yours. Thank you for exploring it with us! {QATARI_EMOJI}"
FOOTER_ORYX     = f"5,000 hours. There are no words. Just pure respect! {QATARI_EMOJI}"

# 4. MESSAGE TEXTS
RANK_MESSAGES = {
    "Second Officer": {
        "content": (
            "🎉 **CONGRATULATIONS** {user_mention}\n\n"
            "You have been promoted to **Second Officer Rank**! 🔥\n"
            "You have successfully reached **10 Flight Hours**.\n\n"
            "🔓 **Aircraft Unlocked:**\n"
            "• Airbus A220\n"
            "• Airbus A330\n"
            "• Boeing 787 Dreamliner Family\n\n"
            "✨ *You now have access to all routes of these aircraft which are present in the Crew Center Route Database.*\n\n"
            f"{FOOTER_STANDARD}"
        )
    },
    "First Officer": {
        "content": (
            "🎉 **CONGRATULATIONS** {user_mention}\n\n"
            "You have been promoted to **First Officer Rank**! 🚀\n"
            "You have successfully reached **25 Flight Hours**.\n\n"
            "🔓 **Aircraft Unlocked:**\n"
            "• Airbus A350\n"
            "• Boeing 767\n\n"
            "✨ *You now have access to all routes of these aircraft which are present in the Crew Center Route Database.*\n\n"
            f"{FOOTER_STANDARD}"
        )
    },
    "Captain": {
        "content": (
            "🎉 **CONGRATULATIONS** {user_mention}\n\n"
            "You have been promoted to **Captain Rank**! ✈️\n"
            "You have successfully reached **100 Flight Hours**.\n\n"
            "🔓 **Aircraft Unlocked:**\n"
            "• Boeing 777-200LR/ER\n"
            "• Boeing 777-300ER\n"
            "• B77F (Codeshares)\n\n"
            "✨ *You now have access to all routes of these aircraft which are present in the Crew Center Route Database.*\n\n"
            "📦 **Cargo Career Opportunity**\n"
            f"You are now Eligible to apply for the Cargo Training <#{ASK_FOR_CARGO_CHANNEL_ID}>\n\n"
            f"{FOOTER_STANDARD}"
        )
    },
    "Senior Captain": {
        "content": (
            "🎉 **CONGRATULATIONS** {user_mention}\n\n"
            "You have been promoted to **Senior Captain Rank**! 🎖️\n"
            "You have successfully reached **250 Flight Hours**.\n\n"
            "🔓 **Aircraft Unlocked:**\n"
            "• Airbus A340\n"
            "• Airbus A380\n"
            "• Boeing 747-400/8\n\n"
            "✨ *You have now Access to All Routes of Crew centre Route Database.*\n\n"
            f"{FOOTER_SENIOR}"
        )
    },
    "Ruby": {
        "content": (
            "🪩 **ACHIEVEMENT UNLOCKED** {user_mention}\n\n"
            "You have achieved the **Ruby Award**! 💎\n"
            "You have reached a massive **500 Flight Hours**.\n\n"
            "🔓 **You have Unlocked a new type of Flight: Amiri Flights**\n"
            "*A VIP transport service providing exclusive travel for high-profile officials and royalty.*\n\n"
            "✈️ **Aircraft Unlocked:**\n"
            "• Qatar Airways A319 (for Qatar Amiri flights)\n\n"
            "👑 **Perks:**\n"
            "• **Callsign:** You can now change your callsign (Range 40-100)\n\n"
            "🔐 **Staff Server Access**\n"
            "A Manager or Executive Member will soon send you a DM with the Staff Server joining link!!\n\n"
            f"{FOOTER_RUBY}"
        )
    },
    "Sapphire": {
        "content": (
            "🪩 **ACHIEVEMENT UNLOCKED** {user_mention}\n\n"
            "You have achieved the **Sapphire Award**! 💠\n"
            "You have reached an impressive **1,000 Flight Hours**.\n\n"
            "🔓 **Full Amiri Fleet Unlocked:**\n"
            "You now have access to the following VIP aircraft:\n"
            "• Airbus A319\n"
            "• Airbus A340\n"
            "• Boeing 747-8 BBJ\n\n"
            f"{FOOTER_SAPPHIRE}"
        )
    },
    "Emerald": {
        "content": (
            "🪩 **ACHIEVEMENT UNLOCKED** {user_mention}\n\n"
            "You have achieved the **Emerald Award**! ❇️\n"
            "You have reached **2,500 Flight Hours**.\n\n"
            "🔓 **Elite Status Reached:**\n"
            "You have joined the absolute elite of virtual aviation. Your dedication over these 2,500 hours is unmatched.\n\n"
            "*Stay tuned! Exclusive Emerald benefits are currently in development.*\n\n"
            f"{FOOTER_EMERALD}"
        )
    },
    "OneWorld": {
        "content": (
            "🪩 **ACHIEVEMENT UNLOCKED** {user_mention}\n\n"
            "You have achieved the **OneWorld Discover Award**! 🌐\n"
            "You have reached **3,000 Flight Hours**.\n\n"
            "🔓 **Unlimited Access:**\n"
            "You now have access to **All Oneworld Airline Routes**.\n"
            "Approximately 5,000 new routes are now at your fingertips!\n\n"
            "📝 *A Staff Member will soon contact you with the Route Database sheet.*\n\n"
            f"{FOOTER_ONEWORLD}"
        )
    },
    "Oryx": {
        "content": (
            "👑 **THE ULTIMATE ACHIEVEMENT** {user_mention}\n\n"
            "You have achieved **The Oryx Award**! \n"
            "You have reached **5,000 Flight Hours**.\n\n"
            "🔓 **The Pinnacle Unlock:**\n"
            "• **Qatari Executive**\n\n"
            "You have completed the journey. You are a master of aviation.\n\n"
            f"{FOOTER_ORYX}"
        )
    },
    # Fallback needed for ranks with no messages (like Cadet)
    "Cadet": { "content": "" }
}

# =============================================================================
# 🧩 UI VIEWS
# =============================================================================

class RankSelectView(discord.ui.View):
    """Dropdown for Manual Override if the user clicks 'No' initially."""
    def __init__(self, cog, pilot_data, actual_hours):
        super().__init__(timeout=60)
        self.cog = cog
        self.pilot_data = pilot_data
        self.actual_hours = actual_hours

    @discord.ui.select(
        placeholder="Override: Select the desired rank...",
        options=[discord.SelectOption(label=rank) for rank in RANK_CONFIG.keys()]
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        selected_rank = select.values[0]
        # Proceed with confirmation
        view = RankConfirmView(self.cog, self.pilot_data, selected_rank, self.actual_hours, is_override=True)
        await interaction.response.edit_message(
            content=f"⚠️ **Override Selected:** You are setting **{selected_rank}** manually.\nDo you want to apply this rank?",
            view=view
        )

class RankConfirmView(discord.ui.View):
    """The Yes/No Confirmation buttons."""
    def __init__(self, cog, pilot_data, target_rank_name, hours, is_override=False):
        super().__init__(timeout=60)
        self.cog = cog
        self.pilot_data = pilot_data
        self.target_rank_name = target_rank_name
        self.hours = hours
        self.is_override = is_override

    @discord.ui.button(label="✅ Yes, Update", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer() # prevent interaction failure
        
        # Disable buttons so they can't be clicked twice
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)

        # Call the update logic
        await self.cog.apply_rank_update(interaction, self.pilot_data, self.target_rank_name)

    @discord.ui.button(label="❌ No", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.is_override:
            await interaction.response.edit_message(content="Action cancelled.", view=None)
        else:
            # Pressed NO on initial check -> Show Dropdown Override
            view = RankSelectView(self.cog, self.pilot_data, self.hours)
            await interaction.response.edit_message(content="Select a manual rank override below:", view=view)


# =============================================================================
# 🚀 MAIN COG CLASS
# =============================================================================

class RankManagement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _get_rank_from_hours(self, hours: float) -> str:
        """Determines the appropriate rank name based on flight hours."""
        best_rank = "Cadet"
        # We assume RANK_CONFIG follows the correct order (Low -> High)
        # We iterate and if hours >= requirement, we set that as the best rank so far.
        for rank_name, data in RANK_CONFIG.items():
            if hours >= data["min_hours"]:
                best_rank = rank_name
        return best_rank

    async def _manage_roles(self, member: discord.Member, target_rank: str) -> bool:
        """
        Removes ALL configured rank roles, then adds only the TARGET rank role.
        Handles the special Captain + Cargo case.
        """
        try:
            guild = member.guild
            roles_to_remove = []
            roles_to_add = []
            
            # 1. Gather all Roles mentioned in config to potentially Remove
            for r_name, r_data in RANK_CONFIG.items():
                rid = r_data["role_id"]
                if rid != 0:
                    role_obj = guild.get_role(rid)
                    if role_obj and role_obj in member.roles:
                        roles_to_remove.append(role_obj)
                
                # Check for Cargo Role cleaning (removes it if user moves to a rank that doesn't have it)
                cid = r_data.get("cargo_role_id", 0)
                if cid != 0:
                    c_role = guild.get_role(cid)
                    if c_role and c_role in member.roles:
                        roles_to_remove.append(c_role)

            # 2. Identify the specific roles needed for the Target Rank
            target_data = RANK_CONFIG.get(target_rank)
            if target_data:
                # Add Main Rank Role
                target_id = target_data["role_id"]
                if target_id != 0:
                    t_role = guild.get_role(target_id)
                    if t_role:
                        roles_to_add.append(t_role)
                
                # Add Cargo Role (If applicable, e.g. Captain)
                c_id = target_data.get("cargo_role_id", 0)
                if c_id != 0:
                    c_role = guild.get_role(c_id)
                    if c_role:
                        roles_to_add.append(c_role)

            # 3. Optimize lists: If we are adding a role, don't remove it
            final_remove = [r for r in roles_to_remove if r not in roles_to_add]
            
            # 4. Execute API calls
            if final_remove:
                await member.remove_roles(*final_remove, reason=f"Rank Update to {target_rank}")
            if roles_to_add:
                await member.add_roles(*roles_to_add, reason=f"Rank Update to {target_rank}")
            
            return True
        except Exception as e:
            print(f"Error managing roles for {member.display_name}: {e}")
            return False

    async def apply_rank_update(self, interaction: discord.Interaction, pilot_data: dict, rank_name: str):
        """Used by the slash command to apply role + send message."""
        discord_id = pilot_data.get('discordid')
        if not discord_id:
            await interaction.followup.send("❌ Error: No Discord ID linked.", ephemeral=True)
            return

        guild = interaction.guild
        member = guild.get_member(int(discord_id))
        
        # If member is None, try fetching them
        if not member:
            try:
                member = await guild.fetch_member(int(discord_id))
            except:
                await interaction.followup.send("❌ Pilot not found in this Discord server.", ephemeral=True)
                return

        # 1. Update Roles
        success = await self._manage_roles(member, rank_name)
        if not success:
            await interaction.followup.send("⚠️ Roles may not have updated completely (Check permissions).", ephemeral=True)
        
        # 2. Send Public Congratulatory Message
        msg_template = RANK_MESSAGES.get(rank_name, {}).get("content", "")
        
        if msg_template:
            final_msg = msg_template.replace("{user_mention}", member.mention)
            
            # Send to the channel (Publicly)
            await interaction.channel.send(final_msg)
            
            # Confirm to the admin who ran command
            await interaction.followup.send(f"✅ Successfully promoted {pilot_data['callsign']} to **{rank_name}**.", ephemeral=True)
        else:
            await interaction.followup.send(f"✅ Roles updated to **{rank_name}**, but no specific promo message is defined for this rank.", ephemeral=True)


    # @app_commands.command(name="verify_roles", description="Check if all configured role IDs exist in the server.")
    # @app_commands.checks.has_permissions(administrator=True)
    # async def verify_roles(self, interaction: discord.Interaction):
    #     """Verify all role IDs from config exist in the server."""
    #     guild = interaction.guild
    #     role_status = []
        
    #     # Check cargo channel
    #     cargo_channel = self.bot.get_channel(ASK_FOR_CARGO_CHANNEL_ID)
    #     channel_status = f"✅ {cargo_channel.name}" if cargo_channel else "❌ Channel not found"
    #     role_status.append(f"**Cargo Channel:** {channel_status}")
    #     role_status.append("")
        
    #     # Check all rank roles
    #     for rank_name, rank_data in RANK_CONFIG.items():
    #         role_id = rank_data["role_id"]
    #         role = guild.get_role(role_id)
            
    #         if role:
    #             status = f"✅ {role.name}"
    #         else:
    #             status = "❌ Role not found"
            
    #         role_status.append(f"**{rank_name}:** {status}")
            
    #         # Check cargo role for Captain
    #         if "cargo_role_id" in rank_data:
    #             cargo_role_id = rank_data["cargo_role_id"]
    #             cargo_role = guild.get_role(cargo_role_id)
    #             cargo_status = f"✅ {cargo_role.name}" if cargo_role else "❌ Cargo role not found"
    #             role_status.append(f"  └─ **Cargo Role:** {cargo_status}")
        
    #     await interaction.response.send_message("\n".join(role_status), ephemeral=True)

    async def _check_executive_or_staff(self, interaction: discord.Interaction) -> bool:
        """Custom check: Returns True if user is Executive (QRV001-QRV004) or Staff (QRV005-QRV019)"""
        discord_id = str(interaction.user.id)
        is_executive = await self.bot.pilots_model.is_executive(discord_id)
        is_staff = await self.bot.pilots_model.is_staff(discord_id)
        return is_executive or is_staff

    @app_commands.command(name="promocheck", description="Check rank status for a pilot and promote if needed.")
    @app_commands.describe(callsign_digits="The 3 or 4 digits of the callsign (e.g., 101 for QRV101)")
    async def promocheck(self, interaction: discord.Interaction, callsign_digits: str):
        """Allows manual rank checking and updating with confirmation."""
        # Check if user is Executive or Staff
        if not await self._check_executive_or_staff(interaction):
            await interaction.response.send_message(
                "❌ You must be an Executive (QRV001-QRV004) or Staff (QRV005-QRV019) to use this command.",
                ephemeral=True
            )
            return
        
        # Clean up input (e.g., allow "101" or "QRV101")
        full_callsign = callsign_digits.upper()
        if not full_callsign.startswith("QRV"):
            full_callsign = f"QRV{full_callsign}"

        pilot = await self.bot.pilots_model.get_pilot_by_callsign_any_status(full_callsign)
        
        if not pilot:
            await interaction.response.send_message(f"❌ Pilot with callsign **{full_callsign}** not found.", ephemeral=True)
            return

        pilot_id = pilot['id']
        if not pilot.get('discordid'):
            await interaction.response.send_message(f"⚠️ Pilot **{full_callsign}** found but has no Discord ID linked.", ephemeral=True)
            return

        # Fetch Total Flight Time (including transfer hours)
        total_hours = await self.bot.pilots_model.get_pilot_total_hours(pilot_id, pilot['callsign'])
        formatted_hours = f"{total_hours:.2f}"

        # Calculate Rank
        suggested_rank = self._get_rank_from_hours(total_hours)

        # Show Ephemeral Interface
        view = RankConfirmView(self, pilot, suggested_rank, total_hours, is_override=False)

        await interaction.response.send_message(
            content=(
                f"🔎 **Pilot Check:** {pilot['callsign']}\n"
                f"⏱️ **Approved Flight Time:** {formatted_hours} hours\n"
                f"📊 **Calculated Rank:** `{suggested_rank}`\n\n"
                "**Update Rank & Post Message?**"
            ),
            view=view,
            ephemeral=True
        )

    # @app_commands.command(name="audit_ranks", description="Admin: Audits and Syncs roles for all server members.")
    # @app_commands.checks.has_permissions(administrator=True)
    # async def audit_ranks(self, interaction: discord.Interaction):
    #     """Admin Tool: Loops through everyone, matches DB, corrects roles."""
    #     await interaction.response.defer(ephemeral=True)
        
    #     guild = interaction.guild
    #     members = guild.members
        
    #     stats = {"checked": 0, "updated": 0, "skipped": 0, "errors": 0}

    #     await interaction.followup.send(f"⏳ Starting Audit for {len(members)} members... Please wait.")

    #     for member in members:
    #         if member.bot: 
    #             continue

    #         try:
    #             # 1. Find pilot in DB
    #             pilot_data = await self.bot.pilots_model.get_pilot_by_discord_id(str(member.id))
    #             if not pilot_data:
    #                 stats["skipped"] += 1
    #                 continue

    #             # 2. Calculate correct rank using total hours (transfer + PIREP)
    #             pilot_id = pilot_data['id']
    #             total_hours = await self.bot.pilots_model.get_pilot_total_hours(pilot_id, pilot_data['callsign'])
    #             correct_rank_name = self._get_rank_from_hours(total_hours)
                
    #             # 3. Strict Check: If they don't have exactly the right roles, Update.
    #             # To be efficient, we check if they ALREADY have the correct Main Role.
    #             # (However, for a clean Audit, simply running manage_roles ensures older/wrong roles are removed too).
                
    #             await self._manage_roles(member, correct_rank_name)
    #             # We assume if the function ran without error, we updated/verified them.
    #             # Differentiating "Verified" vs "Changed" would require inspecting roles_before vs roles_after
    #             # For simplicity here, we count it as checked/handled.
    #             stats["updated"] += 1 # In this context, Updated means "Processed/Synced"

    #         except Exception as e:
    #             # print(f"Error auditing {member.name}: {e}")
    #             stats["errors"] += 1

    #     await interaction.followup.send(
    #         content=(
    #             f"✅ **Audit Complete**\n"
    #             f"👥 Processed Pilots: {stats['updated']}\n"
    #             f"👻 Non-Pilots Skipped: {stats['skipped']}\n"
    #             f"⚠️ Errors: {stats['errors']}"
    #         )
    #     )

async def setup(bot):
    await bot.add_cog(RankManagement(bot))
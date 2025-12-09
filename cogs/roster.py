import discord
from discord import app_commands
from discord.ext import commands
import re

class Roster(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.callsign_pattern = re.compile(r"(QRV\d{3})")

    @app_commands.command(name="sync_discord_ids", description="Syncs Discord IDs to the pilot roster based on nicknames.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sync_discord_ids(self, interaction: discord.Interaction):
        """
        Iterates through all server members to link their Discord ID to their pilot profile.
        """
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        if not self.bot.pilots_model:
            await interaction.followup.send("Error: Pilots database model is not initialized. Please contact the bot developer.")
            return

        updated_count = 0
        already_synced_count = 0
        no_db_match_count = 0
        skipped_count = 0
        total_members = guild.member_count
        failed_members = []

        status_message = await interaction.followup.send(f"🚀 Starting sync for **{total_members}** members... This may take a moment.")
        
        member_info = []
        
        for member in guild.members:
            if member.bot:
                skipped_count += 1
                member_info.append(f"{member.mention} : bot : skipped")
                continue
                
            if not member.nick:
                skipped_count += 1
                member_info.append(f"{member.mention} : no nickname : skipped")
                continue

            match = self.callsign_pattern.search(member.nick)
            if match:
                callsign = match.group(1)  # QRV123
                member_id_str = str(member.id)
                
                # Step 1: Check for active pilot (status = 1)
                active_pilot = await self.bot.pilots_model.get_pilot_by_callsign(callsign)
                
                if active_pilot:
                    # Found active pilot, check Discord ID
                    current_discord_id = active_pilot.get('discordid')
                    
                    if not current_discord_id:
                        # No Discord ID present, add it
                        await self.bot.pilots_model.update_discord_id(callsign, member_id_str)
                        updated_count += 1
                        member_info.append(f"{member.mention} : active : discord id added")
                    elif current_discord_id == member_id_str:
                        # Discord ID matches
                        already_synced_count += 1
                    else:
                        # Discord ID doesn't match, update with new one
                        await self.bot.pilots_model.update_discord_id(callsign, member_id_str)
                        updated_count += 1
                        member_info.append(f"{member.mention} : active : discord id updated")
                else:
                    # Step 2: Check other statuses
                    any_pilot = await self.bot.pilots_model.get_pilot_by_callsign_any_status(callsign)
                    
                    if any_pilot:
                        # Found pilot with different status
                        skipped_count += 1
                        failed_members.append(member)
                        member_info.append(f"{member.mention} : status {any_pilot.get('status')} : not active pilot")
                    else:
                        # Callsign not found in database
                        no_db_match_count += 1
                        failed_members.append(member)
                        member_info.append(f"{member.mention} : not found : callsign not in database")
            else:
                skipped_count += 1
                failed_members.append(member)
                member_info.append(f"{member.mention} : no QRV found : {member.nick or 'no nickname'}")
        
        embed = discord.Embed(
            title="✅ Discord ID Sync Report",
            description="The sync process has completed.",
            color=discord.Color.green()
        )
        embed.add_field(name="✍️ IDs Updated", value=f"`{updated_count}` pilots had their Discord ID added or corrected.", inline=False)
        embed.add_field(name="👍 Already Synced", value=f"`{already_synced_count}` pilots already had the correct ID.", inline=False)
        embed.add_field(name="❓ No DB Match", value=f"`{no_db_match_count}` members had a valid callsign format in their nickname, but the callsign was not found in the database.", inline=False)
        embed.add_field(name="⏭️ Skipped", value=f"`{skipped_count}` members were skipped (bots, no nickname, or incorrect format).", inline=False)
        embed.set_footer(text=f"Total members checked: {total_members}")
        
        await status_message.edit(content="Sync complete!", embed=embed)
        
        # Send member info in smaller batches to avoid 2000 char limit
        if member_info:
            for i in range(0, len(member_info), 15):
                batch = member_info[i:i+15]
                batch_text = "\n".join(batch)
                batch_num = (i // 15) + 1
                total_batches = (len(member_info) + 14) // 15
                try:
                    await interaction.followup.send(f"**Details (Batch {batch_num}/{total_batches}):**\n{batch_text}", ephemeral=True)
                except discord.HTTPException:
                    truncated_text = batch_text[:1800] + "...\n[Truncated]"
                    await interaction.followup.send(f"**Details (Batch {batch_num}/{total_batches}):**\n{truncated_text}", ephemeral=True)

    @sync_discord_ids.error
    async def on_sync_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            if not interaction.response.is_done():
                await interaction.response.send_message("You do not have the `Manage Server` permission to use this command.", ephemeral=True)
            else:
                await interaction.followup.send("You do not have the `Manage Server` permission to use this command.", ephemeral=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"An unexpected error occurred: {error}", ephemeral=True)
            else:
                await interaction.followup.send(f"An unexpected error occurred: {error}", ephemeral=True)
            print(f"Error in sync_discord_ids command: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Roster(bot)) 
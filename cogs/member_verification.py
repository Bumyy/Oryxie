import discord
from discord.ext import commands
from discord import app_commands
import os
import asyncio
import logging

log = logging.getLogger(__name__)

class MemberVerification(commands.Cog):
    """
    A cog for verifying server members against a database of pilot records.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pilots_model = bot.pilots_model 

    @app_commands.command(name="verify_members", description="Check all members against the database and sync verification roles.")
    @commands.has_permissions(administrator=True)
    async def verify_members(self, interaction: discord.Interaction):
        """
        Iterates through all server members, checks their Discord ID against the
        'pilots' database table, and adds or removes the non-verified role accordingly.
        """
        await interaction.response.defer(ephemeral=False)

        try:
            guild_id = int(os.getenv("SERVER_ID"))
            non_verified_role_id = int(os.getenv("NON_VERIFIED_ROLE_ID"))
        except (TypeError, ValueError):
            await interaction.followup.send("❌ **Configuration Error:** `SERVER_ID` or `NON_VERIFIED_ROLE_ID` are not set correctly in your environment.", ephemeral=True)
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            await interaction.followup.send(f"❌ **Error:** Could not find the server with ID `{guild_id}`.", ephemeral=True)
            return

        non_verified_role = guild.get_role(non_verified_role_id)
        if not non_verified_role:
            await interaction.followup.send(f"❌ **Error:** Could not find the role with ID `{non_verified_role_id}`.", ephemeral=True)
            return

        try:
            verified_ids = await self.pilots_model.get_all_verified_discord_ids()
        except Exception as e:
            log.error(f"Database error during member verification: {e}")
            await interaction.followup.send(f"❌ **Database Error:** Could not fetch verified IDs. Please check logs.\n`{e}`")
            return

        members_to_check = [m for m in guild.members if not m.bot]
        total_members = len(members_to_check)
        
        stats = {
            "added": 0,
            "removed": 0,
            "processed": 0
        }
        
        batch_size = 50
        total_batches = (total_members + batch_size - 1) // batch_size

        progress_embed = discord.Embed(
            title="🔄 Member Verification In Progress...",
            description=f"Preparing to process **{total_members}** members.",
            color=discord.Color.blue()
        )
        progress_message = await interaction.followup.send(embed=progress_embed)

        for i in range(0, total_members, batch_size):
            batch = members_to_check[i:i + batch_size]
            batch_num = (i // batch_size) + 1

            for member in batch:
                stats["processed"] += 1
                member_id_str = str(member.id)
                has_role = non_verified_role in member.roles

                is_verified = member_id_str in verified_ids

                try:
                    if is_verified and has_role:
                        await member.remove_roles(non_verified_role, reason="Database verification sync")
                        stats["removed"] += 1
                    elif not is_verified and not has_role:
                        await member.add_roles(non_verified_role, reason="Database verification sync")
                        stats["added"] += 1
                except discord.Forbidden:
                    log.warning(f"Could not modify roles for {member} ({member.id}). Missing permissions.")
                except discord.HTTPException as e:
                    log.error(f"HTTP error modifying roles for {member} ({member.id}): {e}")
            
            progress_embed.title = f"🔄 Verifying Members... (Batch {batch_num}/{total_batches})"
            progress_embed.description = f"Checked **{stats['processed']} / {total_members}** members."
            progress_embed.set_field_at(0, name="🟢 Role Removed (Verified)", value=str(stats["removed"]), inline=True)
            progress_embed.set_field_at(1, name="🔴 Role Added (Not Verified)", value=str(stats["added"]), inline=True)
            
            if len(progress_embed.fields) < 2:
                progress_embed.add_field(name="🟢 Role Removed (Verified)", value=str(stats["removed"]), inline=True)
                progress_embed.add_field(name="🔴 Role Added (Not Verified)", value=str(stats["added"]), inline=True)

            await progress_message.edit(embed=progress_embed)

            if i + batch_size < total_members:
                await asyncio.sleep(3)
        
        summary_embed = discord.Embed(
            title="✅ Verification Complete!",
            description=f"Finished syncing roles for **{total_members}** members.",
            color=discord.Color.green()
        )
        summary_embed.add_field(name="🟢 Role Removed", value=f"{stats['removed']} members are now verified.", inline=False)
        summary_embed.add_field(name="🔴 Role Added", value=f"{stats['added']} members are now un-verified.", inline=False)
        summary_embed.set_footer(text="Sync process finished.")
        
        await progress_message.edit(embed=summary_embed)


async def setup(bot: commands.Bot):
    if not hasattr(bot, 'pilots_model'):
         log.error("PilotsModel not found on bot object. MemberVerification cog will not be loaded.")
         return
         
    await bot.add_cog(MemberVerification(bot))
import discord
from discord.ext import commands
from discord import app_commands
import os

class DatabaseIDChecker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="verify_members", description="Check all members against database and assign/remove verification roles")
    @commands.has_permissions(administrator=True)
    async def verify_members(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        guild_id = int(os.getenv("SERVER_ID"))
        non_verified_role_id = int(os.getenv("NON_VERIFIED_ROLE_ID"))
        
        guild = self.bot.get_guild(guild_id)
        if not guild:
            await interaction.followup.send("❌ Could not find the server!")
            return

        non_verified_role = guild.get_role(non_verified_role_id)
        if not non_verified_role:
            await interaction.followup.send("❌ Could not find the non verified role!")
            return

        # Get all database Discord IDs
        try:
            query = "SELECT DISTINCT discordid FROM pilots WHERE discordid IS NOT NULL AND discordid != ''"
            db_discord_ids = await self.bot.db_manager.fetch_all(query)
            verified_ids = {str(row['discordid']) for row in db_discord_ids}
        except Exception as e:
            await interaction.followup.send(f"❌ Database error: {e}")
            return

        import asyncio
        
        added_count = 0
        removed_count = 0
        all_non_verified = []
        total_members = len([m for m in guild.members if not m.bot])
        processed = 0
        batch_size = 30
        
        members_list = [m for m in guild.members if not m.bot]
        
        await interaction.followup.send(f"🔄 Starting verification of {total_members} members in batches of {batch_size}...")
        
        # Process members in batches of 30
        for i in range(0, len(members_list), batch_size):
            batch = members_list[i:i + batch_size]
            batch_non_verified = []
            batch_added = 0
            batch_removed = 0
            
            for member in batch:
                processed += 1
                member_id = str(member.id)
                has_non_verified = non_verified_role in member.roles
                
                if member_id in verified_ids:
                    if has_non_verified:
                        try:
                            await member.remove_roles(non_verified_role)
                            removed_count += 1
                            batch_removed += 1
                        except discord.Forbidden:
                            pass
                else:
                    batch_non_verified.append(member.display_name)
                    all_non_verified.append(member.display_name)
                    if not has_non_verified:
                        try:
                            await member.add_roles(non_verified_role)
                            added_count += 1
                            batch_added += 1
                        except discord.Forbidden:
                            pass
            
            # Send batch results
            batch_num = (i // batch_size) + 1
            total_batches = (len(members_list) + batch_size - 1) // batch_size
            
            if batch_non_verified:
                member_list = "\n".join([f"• {name}" for name in batch_non_verified])
                batch_msg = f"📊 **Batch {batch_num}/{total_batches}** ({processed}/{total_members})\n🔴 Added: {batch_added} | 🟢 Removed: {batch_removed}\n\n**Non-verified in this batch:**\n{member_list}"
            else:
                batch_msg = f"📊 **Batch {batch_num}/{total_batches}** ({processed}/{total_members})\n🔴 Added: {batch_added} | 🟢 Removed: {batch_removed}\n\n✅ All members in this batch are verified!"
            
            await interaction.followup.send(batch_msg)
            
            # Wait 5 seconds before next batch (except for last batch)
            if i + batch_size < len(members_list):
                await asyncio.sleep(5)
        
        # Final summary
        summary = f"✅ **VERIFICATION COMPLETE!**\n📊 Total members: {total_members}\n🔴 Total added role: {added_count}\n🟢 Total removed role: {removed_count}\n🔴 Total non-verified: {len(all_non_verified)}"
        await interaction.followup.send(summary)

async def setup(bot):
    await bot.add_cog(DatabaseIDChecker(bot))
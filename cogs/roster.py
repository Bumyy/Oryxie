import discord
from discord import app_commands
from discord.ext import commands
import re

class Roster(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.callsign_pattern = re.compile(r".*\|\s*(QRV\d{3})", re.IGNORECASE)

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

        status_message = await interaction.followup.send(f"🚀 Starting sync for **{total_members}** members... This may take a moment.")
        
        for member in guild.members:
            if member.bot or not member.nick:
                skipped_count += 1
                continue

            match = self.callsign_pattern.search(member.nick)
            if match:
                callsign = match.group(1).upper()
                member_id_str = str(member.id)

                pilot_data = await self.bot.pilots_model.get_pilot_by_callsign(callsign)

                if pilot_data is None:
                    no_db_match_count += 1
                else:
                    current_discord_id = pilot_data.get('discordid')
                    
                    if current_discord_id == member_id_str:
                        already_synced_count += 1
                    else:
                        await self.bot.pilots_model.update_discord_id(callsign, member_id_str)
                        updated_count += 1
            else:
                skipped_count += 1
        
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

    @sync_discord_ids.error
    async def on_sync_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You do not have the `Manage Server` permission to use this command.", ephemeral=True)
        else:
            await interaction.response.send_message(f"An unexpected error occurred: {error}", ephemeral=True)
            print(f"Error in sync_discord_ids command: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Roster(bot)) 
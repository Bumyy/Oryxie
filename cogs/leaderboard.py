import discord
from discord.ext import commands
from discord import app_commands

class LeaderboardCog(commands.Cog):
    """
    A cog for handling the flight leaderboard command.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pireps_model = bot.pireps_model

    @app_commands.command(name="leaderboard", description="View the leaderboard for WC26 flights.")
    async def leaderboard(self, interaction: discord.Interaction):
        """Displays pilot rankings based on approved WC26 flights."""
        await interaction.response.defer(ephemeral=False)

        try:
            # Query leaderboard data for wc26
            leaderboard_data = await self.pireps_model.get_flight_number_leaderboard("wc26")

            if not leaderboard_data:
                await interaction.followup.send("ℹ️ No approved WC26 flights have been recorded yet.", ephemeral=True)
                return

            # Construct leaderboard embed
            embed = discord.Embed(
                title="🏆 WC26 Flight Leaderboard",
                description="Rankings based on the number of approved WC26 flights.",
                color=discord.Color.gold()
            )

            medals = ["🥇", "🥈", "🥉"]
            description_lines = []
            
            # Format and display the top 10 pilots
            for rank, entry in enumerate(leaderboard_data[:10], start=1):
                medal_or_rank = medals[rank - 1] if rank <= 3 else f"**#{rank}**"
                
                # Mention the user if discordid is present, otherwise display their name and callsign
                pilot_mention = f"<@{entry['discordid']}>" if entry['discordid'] else f"{entry['pilot_name']} ({entry['callsign']})"
                
                flights = entry['flight_count']
                description_lines.append(f"{medal_or_rank} {pilot_mention} — **{flights}** flights")

            embed.description = "\n".join(description_lines)
            
            # Find and display the current user's rank/stats if they're in the list
            user_entry = next((x for x in leaderboard_data if str(x['discordid']) == str(interaction.user.id)), None)
            if user_entry:
                user_rank = leaderboard_data.index(user_entry) + 1
                embed.add_field(
                    name="Your Position",
                    value=f"Rank: **#{user_rank}** | Flights: **{user_entry['flight_count']}**",
                    inline=False
                )
            else:
                embed.add_field(
                    name="Your Position",
                    value="You haven't completed any approved WC26 flights yet.",
                    inline=False
                )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            print(f"Error executing /leaderboard command: {e}")
            await interaction.followup.send("❌ An error occurred while generating the leaderboard.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(LeaderboardCog(bot))

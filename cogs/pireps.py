import discord
from discord.ext import commands
from discord import app_commands

class Pireps(commands.Cog):
    """
    A cog for handling PIREP (Pilot Report) commands.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pireps_model = bot.pireps_model

    @app_commands.command(name="pending_pireps", description="View all pending pilot reports.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def pending_pireps(self, interaction: discord.Interaction):
        """Displays a list of all PIREPs awaiting approval."""
        await interaction.response.defer(ephemeral=True)

        try:
            pending_reports = await self.pireps_model.get_pending_pireps()

            if not pending_reports:
                await interaction.followup.send("✅ There are no pending PIREPs at the moment.", ephemeral=True)
                return

            embed = discord.Embed(
                title="✈️ Pending Pilot Reports",
                description="The following PIREPs are awaiting review.",
                color=discord.Color.orange()
            )

            for report in pending_reports:

                embed.add_field(
                    name=f"Flight `{report['flightnum']}`: {report['departure']} ➔ {report['arrival']}",
                    value=(
                        f"**Pilot:** {report['pilot_name']}\n"
                        f"**Aircraft:** {report['aircraft_name']}\n"
                        f"**Flight Time:** {report['formatted_flighttime']}\n"
                        f"**Fuel Used:** {report['fuelused']:,} kg\n"
                        f"**Date Filed:** {report['date'].strftime('%Y-%m-%d')}\n"
                    ),
                    inline=False
                )
            
            embed.set_footer(text=f"Total Pending: {len(pending_reports)}")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            print(f"Error in /pending_pireps command: {e}")
            await interaction.followup.send("❌ An error occurred while fetching pending PIREPs.", ephemeral=True)

    @pending_pireps.error
    async def on_pending_pireps_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        else:
            await interaction.response.send_message(f"An unexpected error occurred: {error}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Pireps(bot))
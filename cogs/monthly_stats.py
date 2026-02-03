import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta

class MonthlyStatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="monthly_stats", description="Shows top pilots for the last month.")
    @app_commands.checks.has_permissions(administrator=True)
    async def monthly_stats(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            # 1. Get top pilots from pireps_model
            top_pilots_stats = await self.bot.pireps_model.get_top_pilots_last_31_days()

            if not top_pilots_stats:
                await interaction.followup.send("No pilot data found for the last 31 days.")
                return

            pilot_details = []
            for pilot_stat in top_pilots_stats:
                # Using get_pilot_by_id which now returns name
                pilot_info = await self.bot.pilots_model.get_pilot_by_id(pilot_stat['pilotid'])
                if pilot_info:
                    pilot_details.append({
                        **pilot_stat,
                        **pilot_info
                    })

            if not pilot_details:
                await interaction.followup.send("Could not retrieve details for top pilots.")
                return

            # 2. Format the message
            now = datetime.utcnow()
            # Get previous month's name
            first_day_of_current_month = now.replace(day=1)
            last_day_of_previous_month = first_day_of_current_month - timedelta(days=1)
            month_name = last_day_of_previous_month.strftime("%B")

            message = f"**Top 5 Pilots with Most Hours ({month_name} – 31 Days)**\n\n"

            # Top 5
            for i, pilot in enumerate(pilot_details[:5], 1):
                hours = int(pilot['total_seconds'] // 3600)
                minutes = int((pilot['total_seconds'] % 3600) // 60)
                
                # Get IFC username from pilot data
                ifc_raw = pilot.get('ifc', '')
                print(f"DEBUG: Pilot {i} - Raw IFC: '{ifc_raw}'")
                
                if ifc_raw:
                    # Remove trailing slashes and extract username
                    ifc_clean = ifc_raw.rstrip('/')
                    ifc_username = ifc_clean.split('/')[-1]
                    # Remove /summary if present
                    if ifc_username == 'summary' and len(ifc_clean.split('/')) > 1:
                        ifc_username = ifc_clean.split('/')[-2]
                    print(f"DEBUG: Pilot {i} - Extracted username: '{ifc_username}'")
                else:
                    ifc_username = pilot.get('name', 'Unknown')
                    print(f"DEBUG: Pilot {i} - No IFC, using name: '{ifc_username}'")
                
                mention = f"@{ifc_username}" if ifc_username != 'Unknown' else pilot.get('name', 'Unknown Pilot')
                
                message += f"{i} - {mention} — {hours}:{minutes:02d} flight hours | {pilot['pirep_count']} PIREPs\n"

            message += "\n"

            # Pilots 6-10
            if len(pilot_details) > 5:
                ifc_mentions_6_to_10 = []
                for j, p in enumerate(pilot_details[5:10], 6):
                    ifc_raw = p.get('ifc', '')
                    print(f"DEBUG: Pilot {j} - Raw IFC: '{ifc_raw}'")
                    
                    if ifc_raw:
                        ifc_clean = ifc_raw.rstrip('/')
                        ifc_username = ifc_clean.split('/')[-1]
                        if ifc_username == 'summary' and len(ifc_clean.split('/')) > 1:
                            ifc_username = ifc_clean.split('/')[-2]
                        print(f"DEBUG: Pilot {j} - Extracted username: '{ifc_username}'")
                    else:
                        ifc_username = p.get('name', 'Unknown')
                        print(f"DEBUG: Pilot {j} - No IFC, using name: '{ifc_username}'")
                    
                    mention = f"@{ifc_username}" if ifc_username != 'Unknown' else p.get('name', 'Unknown Pilot')
                    ifc_mentions_6_to_10.append(mention)
                
                if ifc_mentions_6_to_10:
                    message += f"We also congratulate {', '.join(ifc_mentions_6_to_10)} for placing in the Top 10.\n\n"

            # Pilot of the Month
            if pilot_details:
                potm = pilot_details[0]
                ifc_raw = potm.get('ifc', '')
                print(f"DEBUG: POTM - Raw IFC: '{ifc_raw}'")
                
                if ifc_raw:
                    ifc_clean = ifc_raw.rstrip('/')
                    ifc_username = ifc_clean.split('/')[-1]
                    if ifc_username == 'summary' and len(ifc_clean.split('/')) > 1:
                        ifc_username = ifc_clean.split('/')[-2]
                    print(f"DEBUG: POTM - Extracted username: '{ifc_username}'")
                else:
                    ifc_username = potm.get('name', 'Unknown')
                    print(f"DEBUG: POTM - No IFC, using name: '{ifc_username}'")
                
                potm_mention = f"@{ifc_username}" if ifc_username != 'Unknown' else potm.get('name', 'Unknown Pilot')
                message += f"Pilot of the Month is…\n\n{potm_mention}\n\n"
            
            message += "Pilot of the Month is selected based on activity, consistency, participation, and overall attitude.\n\n"
            message += "Congratulations, and thank you for representing QRV with excellence."

            await interaction.followup.send(message)

        except Exception as e:
            await interaction.followup.send(f"An error occurred: {e}")

    @monthly_stats.error
    async def on_monthly_stats_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
        else:
            await interaction.response.send_message(f"An error occurred: {error}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(MonthlyStatsCog(bot))
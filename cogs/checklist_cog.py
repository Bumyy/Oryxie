import discord
from discord import app_commands
from discord.ext import commands
from bot import MyBot
import json
import os

class ChecklistCog(commands.Cog):
    def __init__(self, bot: MyBot):
        self.bot = bot
        # Load aircraft list from JSON
        aircraft_db_path = os.path.join('assets', 'aircrafts.json')
        with open(aircraft_db_path, 'r') as f:
            aircraft_db = json.load(f)
        self.aircraft_choices = [
            app_commands.Choice(name=f"{code} - {data['properties']['full_name']}", value=code)
            for code, data in aircraft_db.items()
        ]

    checklist_group = app_commands.Group(name="checklist", description="Generates a flight checklist PDF.")

    @checklist_group.command(name="generate", description="Generate a complete flight checklist.")
    @app_commands.describe(
        aircraft="The aircraft model (e.g., B77W)",
        load="Your takeoff load percentage (e.g., 78)",
        direction="Your direction of flight (East or West)"
    )
    @app_commands.choices(direction=[
        app_commands.Choice(name="East", value="east"),
        app_commands.Choice(name="West", value="west"),
    ])
    async def generate(self, interaction: discord.Interaction, aircraft: str, load: int, direction: str):
        await interaction.response.defer(ephemeral=False)
        
        try:
            # Access the PDF service through the bot instance and call the new function
            pdf_file = self.bot.checklist_pdf_service.generate_checklist_pdf(aircraft.upper(), load, "checklist", direction)
            await interaction.followup.send("Here is your flight checklist:", file=discord.File(pdf_file), ephemeral=False)
        except Exception as e:
            await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)
    
    @generate.autocomplete('aircraft')
    async def aircraft_autocomplete(self, interaction: discord.Interaction, current: str):
        return [choice for choice in self.aircraft_choices if current.upper() in choice.name.upper()][:25]

async def setup(bot: MyBot):
    await bot.add_cog(ChecklistCog(bot))
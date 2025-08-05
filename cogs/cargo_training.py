import discord
from discord.ext import commands
from discord.ui import Button, View
from discord import app_commands

# --- CONFIGURATION ---
APPLICATIONS_CHANNEL_ID = 1402016405666398371
TRAINER_ROLE_ID = 1402015201171083355
# <<<--- RE-ADDED: Make sure to set this to your 'Cargo Available' role ID ---
CARGO_AVAILABLE_ROLE_ID = 1402206575422210048 # <<<--- CHANGE THIS!

class CargoApplyView(View):
    def __init__(self):
        super().__init__(timeout=None) 

    @discord.ui.button(label="Apply", style=discord.ButtonStyle.primary, custom_id="cargo_apply_button")
    async def apply_button_callback(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        applicant = interaction.user
        
        # <<<--- RE-ADDED: Role Restriction Logic ---
        cargo_available_role = guild.get_role(CARGO_AVAILABLE_ROLE_ID)

        if not cargo_available_role:
            await interaction.followup.send("Configuration error: 'Cargo Available' role not found. Please contact an admin.", ephemeral=True)
            return

        # Check if the user has the required role
        if cargo_available_role not in applicant.roles:
            await interaction.followup.send("You are not eligible to apply for cargo training. This might be because you have already applied or do not have the required role.", ephemeral=True)
            return
        # --- End of Role Restriction Logic ---

        applications_channel = guild.get_channel(APPLICATIONS_CHANNEL_ID)
        trainer_role = guild.get_role(TRAINER_ROLE_ID)

        if not applications_channel or not trainer_role:
            await interaction.followup.send("Configuration error: Applications channel or Trainer role not found.", ephemeral=True)
            return
            
        # <<<--- RE-ADDED: Remove the role from the applicant ---
        try:
            await applicant.remove_roles(cargo_available_role, reason="Started cargo training application.")
        except discord.Forbidden:
            await interaction.followup.send("Error: I don't have permission to manage roles. Please contact an admin.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"An error occurred while updating your roles: {e}", ephemeral=True)
            return
        # --- End of role removal ---

        application_message = await applications_channel.send(
            f"✈️ **Cargo Training for {applicant.mention}**\n"
            f"● **Status:** In progress\n"
            f"● **Trainer:** {trainer_role.mention}"
        )
        
        thread = await application_message.create_thread(name=f"Cargo Training for {applicant.name}")
        
        # <<<--- RE-ADDED: The long, detailed welcome message ---
        welcome_message = f"""
## Welcome to the Qatari Virtual Cargo Captain Training, {applicant.mention}!

This training is mainly about explaining our custom made system for the Cargo Mode, but we are also sharing information about the B777F and special cargo gate selection.

### The Cargo Mode
This gamemode is a Career type of mode meaning you will have to take off from the last airport you landed at.

### Challenges
In each Challenge you will have to deliver goods between certain airports. At each airport you will see a delivery list which contains items, each of these items will needed to be delivered to a different airport indicated on their delivery information board.

### Flexible flights
In this gamemode you have to use your planning skills and think of the best combination of routes to take to deliver the goods in the shortest flight time and distance possible. You can take any flight between the airports of the challenge, load any kind of cargo item to allow your creativity to fly!

### The goal
I think at this point you already guessed it. The goal of this mode is to deliver all goods to the their destination airport, in other words the challenge is completed once every item is at the airport they should go to.

### Cargo Training
To really show you how the system works we created a little Tutorial Challenge for you. In order to really understand the possibilities in this mode, you will have to complete this simple 2 flight challenge where we show almost every case you will encounter while flying Cargo!

## Are you ready to start the Cargo Training?
If ready then ping {trainer_role.mention} and say 'I am ready'.
"""
        await thread.send(welcome_message)
        # --- End of re-added section ---
        
        await interaction.followup.send(f"Application submitted! Your 'Cargo Available' role has been removed. See your training thread: {thread.mention}", ephemeral=True)


class CargoTraining(commands.Cog):
    """A cog for the cargo training system."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(CargoApplyView())

    @app_commands.command(name="setup_cargo_panel", description="Sets up the cargo training application panel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_cargo_panel(self, interaction: discord.Interaction):
        """Sends the cargo training application panel."""
        embed = discord.Embed(
            title="Cargo Training",
            description="Apply for Qatari Cargo and begin your cargo journey by clicking the button below.",
            color=0x5865F2
        )
        
        await interaction.response.send_message(embed=embed, view=CargoApplyView())


async def setup(bot: commands.Bot):
    await bot.add_cog(CargoTraining(bot))
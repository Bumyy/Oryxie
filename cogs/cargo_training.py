# /your-bot-project/cogs/cargo_training.py

import discord
from discord.ext import commands
from discord.ui import Button, View

# --- CONFIGURATION ---
APPLICATIONS_CHANNEL_ID = 1402016405666398371
TRAINER_ROLE_ID = 1402015201171083355
AUTHORIZED_USER_ID = 1212420109017161729 # The user ID allowed to run the setup command

# --- Custom Check Function ---
def is_authorized_user():
    def predicate(ctx: commands.Context) -> bool:
        return ctx.author.id == AUTHORIZED_USER_ID
    return commands.check(predicate)


class CargoApplyView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Apply", style=discord.ButtonStyle.primary, custom_id="cargo_apply_button")
    async def apply_button_callback(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        applicant = interaction.user
        applications_channel = guild.get_channel(APPLICATIONS_CHANNEL_ID)
        trainer_role = guild.get_role(TRAINER_ROLE_ID)

        if not applications_channel or not trainer_role:
            await interaction.followup.send("Configuration error: Channel/Role not found.", ephemeral=True)
            return

        application_message = await applications_channel.send(
            f"✈️ **Cargo Training for {applicant.mention}**\n"
            f"● **Status:** In progress\n"
            f"● **Trainer:** {trainer_role.mention}"
        )
        
        thread = await application_message.create_thread(name=f"Cargo Training for {applicant.name}")
        
        await thread.send(
            f"**Welcome to the Qatari Virtual Cargo Captain Training, {applicant.mention}!**\n\n"
            "Your trainer will be with you shortly."
        )
        
        await interaction.followup.send(f"Application submitted! See your training thread: {thread.mention}", ephemeral=True)


# <<<--- RENAMED THIS CLASS for better consistency ---
class CargoTrainingCog(commands.Cog):
    """A cog for the cargo training system."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command()
    @is_authorized_user()
    async def setup_cargo_panel(self, ctx: commands.Context):
        """Sends the cargo training application panel."""
        embed = discord.Embed(
            title="Cargo Training",
            description="Apply for Qatari Cargo and begin your cargo journey by clicking the button below.",
            color=0x5865F2
        )
        await ctx.send(embed=embed, view=CargoApplyView())
        await ctx.message.add_reaction("✅")


# <<<--- THIS IS THE MOST IMPORTANT CHANGE in this file ---
# It now adds the correctly named Cog to the bot.
async def setup(bot: commands.Bot):
    await bot.add_cog(CargoTrainingCog(bot))
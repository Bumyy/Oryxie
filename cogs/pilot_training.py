import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput
import os

# --- Configuration ---
TRAINING_CHANNEL_ID = int(os.getenv("TRAINING_CHANNEL_ID"))

class PilotTraining(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _start_training_process(self, member: discord.Member):
        """
        A helper method containing the core logic to start a training thread for a member.
        This can be called by any event or command.
        """
        if TRAINING_CHANNEL_ID == 0:
            print("Error: TRAINING_CHANNEL_ID is not set in environment variables.")
            return None
            
        training_channel = self.bot.get_channel(TRAINING_CHANNEL_ID)
        if not training_channel:
            print(f"Error: Training channel with ID {TRAINING_CHANNEL_ID} not found.")
            return None

        initial_message = await training_channel.send(
            f"Initial Training for {member.mention}\n- Status: In progress\n- Trainer: Not yet assigned"
        )
        training_thread = await initial_message.create_thread(name=f"Training for {member.display_name}")
        embed = discord.Embed(
            title=f"Welcome to Qatari Virtual, {member.display_name}!",
            description="Please click the button below to begin your verification process with your Infinite Flight account.",
            color=discord.Color.from_rgb(88, 101, 242)
        )
        embed.set_footer(text="You have been automatically added to this private training thread.")
        await training_thread.send(content=member.mention, embed=embed, view=VerificationView())
        return training_thread

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """
        Handles the event when a new member joins the server.
        """
        print(f"New member joined: {member.name} ({member.id}). Starting training process.")
        await self._start_training_process(member)

    @app_commands.command(name="start_training", description="Manually start the training process for a user.")
    @app_commands.checks.has_permissions(administrator=True)
    async def start_training_command(self, interaction: discord.Interaction, member: discord.Member):
        """Manually triggers the training thread creation for a specified user."""
        
        await interaction.response.defer(ephemeral=True)
        
        thread = await self._start_training_process(member)

        if thread:
            await interaction.followup.send(f"✅ Successfully created training thread for {member.mention} in {thread.mention}", ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed to create training thread. Please check the bot's console for errors.", ephemeral=True)
            
    @start_training_command.error
    async def start_training_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message("An unexpected error occurred. The developer has been notified.", ephemeral=True)
            else:
                await interaction.followup.send("An unexpected error occurred. The developer has been notified.", ephemeral=True)
            raise error

class VerificationView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.green, custom_id="verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(VerificationModal(bot=interaction.client))

class VerificationModal(Modal, title="Infinite Flight Verification"):
    ifc_username = TextInput(
        label="Infinite Flight Community Username",
        placeholder="Enter your IFC username (not display name)",
        required=True,
        style=discord.TextStyle.short
    )

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        username = self.ifc_username.value

        data = await self.bot.if_api_manager.get_user_stats(discourse_names=[username])

        if data and data.get("errorCode") == 0:
            if data["result"]:
                user_info = data["result"][0]
                if user_info.get("atcRank") is not None and user_info.get("atcRank") > 1:
                    await interaction.response.send_message(
                        f"Welcome, {interaction.user.mention}! Since you are IFATC member so you don't have to go for training. \n"
                        "Please provide **5 callsigns** (in priority order) between **101 - 499** and ping @recruiter.",
                        ephemeral=False
                    )
                else:
                    view = View()
                    view.add_item(Button(
                        label="Written Test",
                        style=discord.ButtonStyle.primary,
                        custom_id="start_persistent_written_test"
                    ))
                    await interaction.response.send_message(
                        f"Hello {interaction.user.mention}. Your account has been verified, Please proceed to the written test.",
                        view=view,
                        ephemeral=False
                    )
            else:
                await interaction.response.send_message(
                    "Could not find a user with that Infinite Flight Community username.",
                    ephemeral=False
                )
        else:
            print(f"API Error during verification: {data}")
            await interaction.response.send_message(
                "There was an error verifying your account. Please try again later or contact staff.",
                ephemeral=False
            )

async def setup(bot: commands.Bot):
    if not hasattr(bot, 'if_api_manager'):
        print("ERROR: InfiniteFlightAPIManager is not attached to the bot. PilotTraining cog not loaded.")
        return
    await bot.add_cog(PilotTraining(bot))
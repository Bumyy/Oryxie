import discord
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput

# --- Configuration ---
TRAINING_CHANNEL_ID = 1402302786175107144

class PilotTraining(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """
        Handles the event when a new member joins the server.
        """
        print(f"New member joined: {member.name} ({member.id})")
        training_channel = self.bot.get_channel(TRAINING_CHANNEL_ID)
        if not training_channel:
            print(f"Error: Training channel with ID {TRAINING_CHANNEL_ID} not found.")
            return

        # 1. Send the initial training message
        initial_message = await training_channel.send(
            f"Initial Training for {member.mention}\n- Status: In progress\n- Trainer: @Recruiter"
        )

        # 2. Create a thread from the message
        training_thread = await initial_message.create_thread(name=f"Training for {member.display_name}")

        # 3. Send the welcome embed with a verify button in the thread
        embed = discord.Embed(
            title="Welcome to the Qatari Virtual Discord",
            description="Please click the button below to verify your Infinite Flight account.",
            color=discord.Color.blue()
        )
        await training_thread.send(embed=embed, view=VerificationView())

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
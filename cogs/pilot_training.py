# cogs/pilot_training.py

import discord
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput
import aiohttp

# --- Configuration ---
TRAINING_CHANNEL_ID = 123456789012345678 # Replace with your training channel ID
INFINITE_FLIGHT_API_KEY = "YOUR_INFINITE_FLIGHT_API_KEY" # Replace with your API key

class PilotTraining(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """
        Handles the event when a new member joins the server.
        """
        training_channel = self.bot.get_channel(TRAINING_CHANNEL_ID)
        if not training_channel:
            print(f"Error: Training channel with ID {TRAINING_CHANNEL_ID} not found.")
            return

        # 1. Send the initial training message
        initial_message = await training_channel.send(f"Initial Training for {member.mention}\n- Status: In progress\n- Trainer: @Recruiter")

        # 2. Create a thread from the message
        training_thread = await initial_message.create_thread(name=f"Training for {member.display_name}")

        # 3. Send the welcome embed with a verify button in the thread
        embed = discord.Embed(
            title="Welcome to the Qatari Virtual Discord",
            description="Please click the button below to verify your Infinite Flight account.",
            color=discord.Color.blue()
        )
        await training_thread.send(embed=embed, view=VerificationView())

    async def cog_unload(self):
        await self.session.close()

class VerificationView(View):
    def __init__(self):
        super().__init__(timeout=None) # The view will not time out

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.green, custom_id="verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(VerificationModal())

class VerificationModal(Modal, title="Infinite Flight Verification"):
    if_username = TextInput(
        label="Infinite Flight Username",
        placeholder="Enter your Infinite Flight username here",
        required=True,
        style=discord.TextStyle.short
    )

    async def on_submit(self, interaction: discord.Interaction):
        username = self.if_username.value
        
        headers = {"Authorization": f"Bearer {INFINITE_FLIGHT_API_KEY}"}
        params = {"usernames[]": username}
        
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.infiniteflight.com/public/v2/users", headers=headers, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data["result"]:
                        user_info = data["result"][0]
                        # Check if the user has an ATC rank, indicating they are IFATC
                        if user_info.get("atcRank") is not None and user_info.get("atcRank") > 0:
                            await interaction.response.send_message(f"Welcome, {interaction.user.mention}! Your IFATC status has been confirmed.", ephemeral=True)
                        else:
                            view = View()
                            view.add_item(Button(label="Written Test", style=discord.ButtonStyle.primary, custom_id="written_test_button"))
                            await interaction.response.send_message(f"Hello {interaction.user.mention}. Your account has been verified, but you are not an active IFATC member. Please proceed to the written test.", view=view, ephemeral=True)
                    else:
                        await interaction.response.send_message("Could not find a user with that Infinite Flight username.", ephemeral=True)
                else:
                    await interaction.response.send_message("There was an error verifying your account. Please try again later.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(PilotTraining(bot))
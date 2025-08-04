import discord
from discord.ext import commands
from discord import app_commands, ButtonStyle, Interaction
from discord.ui import View, Button
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 123456789012345678  # <-- Replace with your channel ID

# Customize these:
EMBED_TITLE = "Cargo Training"
EMBED_DESCRIPTION = "Click the button below to register for cargo training!"
BUTTON_LABEL = "Apply"
BUTTON_CUSTOM_ID = "456654"

class MyView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(Button(label=BUTTON_LABEL, style=ButtonStyle.primary, custom_id=BUTTON_CUSTOM_ID))

async def send_embed():
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        channel = bot.get_channel(CHANNEL_ID)
        embed = discord.Embed(title=EMBED_TITLE, description=EMBED_DESCRIPTION, color=0x00ff00)
        await channel.send(embed=embed, view=MyView())
        print("Embed sent!")
        await bot.close()

    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(send_embed())
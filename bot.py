import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import asyncio

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced_global = await bot.tree.sync()
        print(f"Synced {len(synced_global)} slash command(s) globally.")
    except Exception as e:
        print(f"Failed to sync slash commands globally: {e}")

async def main():
    async with bot:
        await bot.load_extension("cogs.pingpong")
        await bot.start(TOKEN)

asyncio.run(main())
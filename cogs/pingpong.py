import discord
from discord.ext import commands
from discord import app_commands

class PingPong(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Slash command version
    @app_commands.command(name="ping", description="Check bot latency")
    async def slash_ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"🏓 Pong! `{latency}ms`", ephemeral=True)

    # Event listener for message-based "ping"
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if message.content.lower() == "ping":
            latency = round(self.bot.latency * 1000)
            await message.channel.send(f"🏓 Pong! `{latency}ms`")

async def setup(bot):
    await bot.add_cog(PingPong(bot))

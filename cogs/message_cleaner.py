import discord
from discord.ext import commands
from discord import app_commands

class MessageCleaner(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="clear", description="Delete a specified number of messages from the current channel")
    @app_commands.describe(amount="Number of messages to delete (1-100)")
    async def clear_messages(self, interaction: discord.Interaction, amount: int):
        # Check if user has manage messages permission
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You don't have permission to delete messages!", ephemeral=True)
            return
        
        # Validate amount
        if amount < 1 or amount > 100:
            await interaction.response.send_message("❌ Please specify a number between 1 and 100!", ephemeral=True)
            return
        
        # Check if bot has manage messages permission
        if not interaction.guild.me.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ I don't have permission to delete messages!", ephemeral=True)
            return
        
        try:
            # Defer the response since deletion might take time
            await interaction.response.defer(ephemeral=True)
            
            # Delete messages
            deleted = await interaction.channel.purge(limit=amount)
            
            # Send confirmation
            await interaction.followup.send(f"✅ Successfully deleted {len(deleted)} message(s)!", ephemeral=True)
            
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to delete messages in this channel!", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ An error occurred while deleting messages: {str(e)}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ An unexpected error occurred: {str(e)}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(MessageCleaner(bot))
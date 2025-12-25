import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import logging
from typing import Dict, Set

logger = logging.getLogger('oryxie')

class PersistentGiftView(discord.ui.View):
    def __init__(self, target_user_id: int):
        super().__init__(timeout=259200)  # 72 hours
        self.target_user_id = target_user_id
        self.opened = False
        
    @discord.ui.button(label='Open Gift Box', style=discord.ButtonStyle.primary, emoji='🎁')
    async def open_gift_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_user_id:
            await interaction.response.send_message("❌ This gift box is not for you!", ephemeral=True)
            return
            
        if self.opened:
            await interaction.response.send_message("❌ This gift box has already been opened!", ephemeral=True)
            return
            
        # Mark as opened and disable button
        self.opened = True
        button.disabled = True
        button.label = "Gift Opened"
        button.style = discord.ButtonStyle.secondary
        
        await interaction.response.edit_message(view=self)
        
        # Start gift opening animation
        await self._open_gift_animation(interaction)
        
    async def _open_gift_animation(self, interaction: discord.Interaction):
        """Handle the gift opening animation"""
        try:
            logger.info("DEBUG: Starting gift animation")
            
            # Define rewards
            items = [
                {
                    "name": "50 Flight Hours + Custom Role",
                    "color": 0xFFD700,
                    "icon": "🏅",
                    "desc": "🎉 **Congratulations!** You've won 50 bonus flight hours and your very own custom Discord role! You get to choose the role name and image (subject to staff approval). Contact Ayush to claim your custom role!"
                },
                {
                    "name": "100 Flight Hours",
                    "color": 0x0099FF,
                    "icon": "✈️",
                    "desc": "🚀 **Amazing!** You've been awarded 100 bonus flight hours! These have been added directly to your CC account."
                },
                {
                    "name": "Qatar Charter Holiday Route",
                    "color": 0x8D0004,
                    "icon": "🗺️",
                    "desc": "🌍 **Incredible!** You've unlocked one Qatar Charter Holiday Route with 3× multiplier! You can fly to or from DOHA (OTHH) to ANY airport worldwide using any Qatar Airways fleet aircraft - even if it's not a real Qatar Airways route! You can use this route ONE TIME with the 3× multiplier."
                }
            ]

            # Animation Phase 1 - Progress 10%
            logger.info("DEBUG: Creating phase 1 embed")
            embed = discord.Embed(
                title="🎁 SPECIAL GIFT BOX",
                description="```\n      SHAKING... \n```\n*The ribbons are coming off!*\n\n**Progress:** ▓░░░░░░░░░ 10%",
                color=discord.Color.red()
            )
            
            logger.info("DEBUG: Sending phase 1 followup")
            msg = await interaction.followup.send(embed=embed)
            logger.info(f"DEBUG: Phase 1 sent, message ID: {msg.id}")
            
            await asyncio.sleep(0.5)
            logger.info("DEBUG: Phase 1 sleep complete")

            # Animation Phase 2 - Progress 50%
            logger.info("DEBUG: Starting phase 2")
            embed.title = "✨ SOMETHING IS INSIDE..."
            embed.description = "```\n    !!! GLOWING !!! \n```\n*The box is bursting with light!*\n\n**Progress:** ▓▓▓▓▓░░░░░ 50%"
            embed.color = discord.Color.gold()
            
            logger.info("DEBUG: Editing message for phase 2")
            await msg.edit(embed=embed)
            logger.info("DEBUG: Phase 2 edit complete")
            
            await asyncio.sleep(0.5)
            logger.info("DEBUG: Phase 2 sleep complete")

            # Progress Phase 3 - 90%
            logger.info("DEBUG: Starting phase 3")
            embed.title = "🎊 ALMOST THERE..."
            embed.description = "```\n   REVEALING PRIZE... \n```\n*The moment of truth!*\n\n**Progress:** ▓▓▓▓▓▓▓▓▓░ 90%"
            embed.color = discord.Color.purple()
            
            logger.info("DEBUG: Editing message for phase 3")
            await msg.edit(embed=embed)
            logger.info("DEBUG: Phase 3 edit complete")
            
            await asyncio.sleep(0.5)
            logger.info("DEBUG: Phase 3 sleep complete")

            # Reveal Phase - Smart selection to avoid duplicates
            logger.info("DEBUG: Starting reveal phase")
            
            # Get user's previous win if any
            user_id = interaction.user.id
            cog = interaction.client.get_cog('GiftBoxCog')
            previous_win = getattr(cog, 'user_previous_wins', {}).get(user_id) if cog else None
            
            # Filter out previous win to ensure variety
            available_items = items.copy()
            if previous_win:
                available_items = [item for item in items if item['name'] != previous_win]
                logger.info(f"DEBUG: Filtered out previous win '{previous_win}', {len(available_items)} items available")
            
            # If somehow all items were filtered out, use all items
            if not available_items:
                available_items = items
                logger.info("DEBUG: No items available after filtering, using all items")
            
            winning_item = random.choice(available_items)
            
            # Store this win for next time
            if cog:
                if not hasattr(cog, 'user_previous_wins'):
                    cog.user_previous_wins = {}
                cog.user_previous_wins[user_id] = winning_item['name']
            
            logger.info(f"DEBUG: Selected item: {winning_item['name']} (previous: {previous_win})")
            
            reveal_embed = discord.Embed(
                title=f"{winning_item['icon']} CONGRATULATIONS!",
                description=f"**You won: {winning_item['name']}**\n\n{winning_item['desc']}",
                color=winning_item['color']
            )
            reveal_embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            reveal_embed.set_footer(text="Christmas 2025 • QRV Special Gift Box")
            


            logger.info("DEBUG: Editing message for reveal phase")
            await msg.edit(embed=reveal_embed)
            logger.info("DEBUG: Animation complete!")
                
        except Exception as e:
            logger.error(f"ERROR in gift animation: {e}")
            logger.error(f"ERROR details: {type(e).__name__}: {str(e)}")
            import traceback
            logger.error(f"ERROR traceback: {traceback.format_exc()}")

class GiftBoxCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.user_previous_wins = {}  # Track previous wins per user

    @app_commands.command(name="send_gift", description="Send a gift box to a pilot by their 3-digit callsign")
    @app_commands.describe(callsign="3-digit pilot callsign (e.g., 001, 123)")
    async def send_gift(self, interaction: discord.Interaction, callsign: str):
        # Check if user has permission (you can modify this check)
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You don't have permission to send gift boxes!", ephemeral=True)
            return
            
        # Validate callsign format
        if not callsign.isdigit() or len(callsign) != 3:
            await interaction.response.send_message("❌ Please provide a valid 3-digit callsign (e.g., 001, 123)", ephemeral=True)
            return
            
        try:
            # Find pilot by callsign in database
            search_callsign = f"QRV{callsign}"
            pilot_data = await self.bot.pilots_model.get_pilot_by_callsign(search_callsign)
            
            if not pilot_data:
                await interaction.response.send_message(f"❌ No pilot found with callsign **{search_callsign}** in database", ephemeral=True)
                return
                
            # Get Discord user from pilot's discord ID
            discord_id = pilot_data['discordid']
            if not discord_id:
                await interaction.response.send_message(f"❌ Pilot **{search_callsign}** has no Discord ID linked", ephemeral=True)
                return
                
            try:
                target_user = await interaction.guild.fetch_member(int(discord_id))
            except (discord.NotFound, discord.HTTPException, ValueError):
                await interaction.response.send_message(f"❌ Discord user with ID {discord_id} not found in this server", ephemeral=True)
                return
                
            # Create gift box message with ping above
            message_content = f"🎁 {target_user.mention} has received a special gift box!"
            
            embed = discord.Embed(
                title="🎁 SPECIAL GIFT BOX DELIVERY",
                description=f"*Click the button below to open your gift!*",
                color=0xFF6B6B
            )
            embed.add_field(name="Recipient", value=target_user.mention, inline=True)
            embed.add_field(name="Sent by", value=interaction.user.mention, inline=True)
            embed.set_footer(text="This gift box expires in 72 hours • Christmas 2025")
            
            # Create persistent view
            view = PersistentGiftView(target_user.id)
            
            await interaction.response.send_message(content=message_content, embed=embed, view=view)
            
            logger.info(f"Gift box sent to {target_user.display_name} (ID: {target_user.id}) by {interaction.user.display_name}")
            
        except Exception as e:
            logger.error(f"Error in send_gift command: {e}")
            await interaction.response.send_message("❌ An error occurred while sending the gift box.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(GiftBoxCog(bot))
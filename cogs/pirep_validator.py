import discord
from discord.ext import commands
from discord import app_commands
from typing import TYPE_CHECKING
import os
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta
from services.pirep_validation_service import PirepValidationService

if TYPE_CHECKING:
    from ..bot import MyBot

logger = logging.getLogger(__name__)

class PirepRetryView(discord.ui.View):
    def __init__(self, callsign: str, flight_num: str, departure: str = None, arrival: str = None):
        super().__init__(timeout=None)
        self.callsign = callsign
        self.flight_num = flight_num
        self.departure = departure
        self.arrival = arrival
    
    @discord.ui.button(label="🔄 Retry Validation", style=discord.ButtonStyle.primary, custom_id="pirep_retry")
    async def retry_validation(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not any("staff" in role.name.lower() for role in interaction.user.roles):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
        
        await interaction.response.defer()
        
        validation_service = PirepValidationService(interaction.client)
        
        if self.departure and self.arrival:
            target_pirep = await validation_service.find_pirep_by_callsign_flight_and_route(self.callsign, self.flight_num, self.departure, self.arrival)
        else:
            target_pirep = await validation_service.find_pirep_by_callsign_and_flight(self.callsign, self.flight_num)
        
        if not target_pirep:
            return await interaction.followup.send("⚠️ PIREP still not found in database. Please wait longer or check manually.", ephemeral=True)
        
        try:
            report_embed = await validation_service.validate_pirep(target_pirep)
            
            # Get the original webhook message ID from the thread's parent channel
            webhook_message_id = None
            try:
                # Look for the original webhook message in the parent channel
                async for message in interaction.channel.parent.history(limit=50):
                    if (message.embeds and 
                        "New PIREP Filed" in message.embeds[0].title and
                        self.callsign in message.embeds[0].description and
                        self.flight_num in message.embeds[0].description):
                        webhook_message_id = message.id
                        break
            except Exception as e:
                logger.error(f"Could not find webhook message: {e}")
            
            # Check if validation failed to find a match (for retry button)
            if "MATCH NOT FOUND" in str(report_embed.fields[0].name if report_embed.fields else ""):
                # Still no match, show retry again
                retry_view = PirepRetryView(self.callsign, self.flight_num, self.departure, self.arrival)
                await interaction.followup.send(embed=report_embed, view=retry_view)
            else:
                # Success! Show thread view with correct webhook message ID
                view = PirepThreadView(target_pirep['pirep_id'], webhook_message_id or 0, self.callsign, self.flight_num, self.departure, self.arrival)
                await interaction.followup.send(embed=report_embed, view=view)
        except Exception as e:
            await interaction.followup.send(f"❌ **Error during validation:** {str(e)}", ephemeral=True)

class PirepThreadView(discord.ui.View):
    def __init__(self, pirep_id: int, webhook_message_id: int, callsign: str = None, flight_num: str = None, departure: str = None, arrival: str = None):
        super().__init__(timeout=None)  # Must be None for persistent views
        self.pirep_id = pirep_id
        self.webhook_message_id = webhook_message_id
        self.callsign = callsign
        self.flight_num = flight_num
        self.departure = departure
        self.arrival = arrival
    
    def _check_staff_role(self, user) -> bool:
        """Check if user has staff role."""
        return any("staff" in role.name.lower() for role in user.roles)
    
    async def _get_pirep(self, interaction):
        """Get PIREP using multiple methods."""
        # Try by ID first
        pirep = await interaction.client.pireps_model.get_pirep_by_id(self.pirep_id)
        if pirep:
            return pirep
        
        # Fallback to search by callsign/flight/route if available
        if self.callsign and self.flight_num:
            validation_service = PirepValidationService(interaction.client)
            if self.departure and self.arrival:
                return await validation_service.find_pirep_by_callsign_flight_and_route(self.callsign, self.flight_num, self.departure, self.arrival)
            else:
                return await validation_service.find_pirep_by_callsign_and_flight(self.callsign, self.flight_num)
        
        return None
    
    @discord.ui.button(label="🐛 Debug", style=discord.ButtonStyle.secondary, custom_id="pirep_debug")
    async def debug_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_staff_role(interaction.user):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        
        pirep = await self._get_pirep(interaction)
        if pirep:
            validator_service = PirepValidationService(interaction.client)
            debug_messages = await validator_service.get_debug_info(pirep)
            for msg in debug_messages:
                await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.followup.send("PIREP not found.", ephemeral=True)
    
    @discord.ui.button(label="📅 Flight History", style=discord.ButtonStyle.secondary, custom_id="pirep_history")
    async def history_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_staff_role(interaction.user):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        
        pirep = await self._get_pirep(interaction)
        if pirep:
            validator_service = PirepValidationService(interaction.client)
            history_embeds = await validator_service.get_flight_history(pirep)
            for embed in history_embeds:
                await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send("PIREP not found.", ephemeral=True)
    
    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success, custom_id="pirep_approve")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_staff_role(interaction.user):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
        
        await interaction.response.defer()
        
        # Add checkmark reaction to parent webhook message
        try:
            webhook_message = await interaction.channel.parent.fetch_message(self.webhook_message_id)
            await webhook_message.add_reaction("✅")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"Could not add reaction to webhook message: {e}")
        
        # Send approval message
        await interaction.followup.send(f"✅ **PIREP approved by {interaction.user.mention}**")
        
        # Disable the approve button
        button.disabled = True
        button.label = "✅ Approved"
        await interaction.message.edit(view=self)

class PirepPaginationView(discord.ui.View):
    def __init__(self, bot, pending_pireps, current_index=0, count_message=None, validation_service=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.pending_pireps = pending_pireps
        self.current_index = current_index
        self.count_message = count_message
        self.validation_service = validation_service or PirepValidationService(bot)
        self.update_buttons()
    
    def _check_staff_role(self, user) -> bool:
        """Check if user has staff role."""
        return any("staff" in role.name.lower() for role in user.roles)
    
    def update_buttons(self):
        self.previous_button.disabled = self.current_index == 0
        self.next_button.disabled = self.current_index >= len(self.pending_pireps) - 1
    
    @discord.ui.button(label="⬅️ Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_staff_role(interaction.user):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
        
        await interaction.response.defer()
        
        try:
            if self.current_index > 0:
                self.current_index -= 1
                embed = await self.validation_service.validate_pirep(self.pending_pireps[self.current_index])
                self.update_buttons()
                await interaction.message.edit(embed=embed, view=self)
        except Exception as e:
            await interaction.followup.send(f"Error updating PIREP: {str(e)}", ephemeral=True)
    
    @discord.ui.button(label="➡️ Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_staff_role(interaction.user):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
        
        await interaction.response.defer()
        
        try:
            if self.current_index < len(self.pending_pireps) - 1:
                self.current_index += 1
                embed = await self.validation_service.validate_pirep(self.pending_pireps[self.current_index])
                self.update_buttons()
                await interaction.message.edit(embed=embed, view=self)
        except Exception as e:
            await interaction.followup.send(f"Error updating PIREP: {str(e)}", ephemeral=True)
    
    @discord.ui.button(label="🔄 Refresh List", style=discord.ButtonStyle.primary)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_staff_role(interaction.user):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
        
        await interaction.response.defer()
        
        self.pending_pireps = await self.bot.pireps_model.get_pending_pireps()
        
        if not self.pending_pireps:
            await interaction.followup.send("✅ No more pending PIREPs!", ephemeral=True)
            return
        
        if self.count_message:
            try:
                await self.count_message.edit(content=f"📋 **{len(self.pending_pireps)} PIREPs pending validation**")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                logger.error(f"Could not update count message: {e}")
        
        self.current_index = 0
        embed = await self.validation_service.validate_pirep(self.pending_pireps[0])
        self.update_buttons()
        await interaction.message.edit(embed=embed, view=self)
    
    @discord.ui.button(label="📅 Flight History", style=discord.ButtonStyle.success, row=1)
    async def flight_history_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_staff_role(interaction.user):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            history_embeds = await self.validation_service.get_flight_history(self.pending_pireps[self.current_index])
            for embed in history_embeds:
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Error getting flight history: {str(e)}", ephemeral=True)
    
    @discord.ui.button(label="🐛 Debug", style=discord.ButtonStyle.danger, row=1)
    async def debug_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_staff_role(interaction.user):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        
        debug_messages = await self.validation_service.get_debug_info(self.pending_pireps[self.current_index])
        
        for msg in debug_messages:
            await interaction.followup.send(msg, ephemeral=True)

class PirepValidator(commands.Cog):
    def __init__(self, bot: 'MyBot'):
        self.bot = bot
        self.WATCH_CHANNEL_ID = 1459564652945084578  # Production channel
        self.TEST_CHANNEL_ID = 1422286417618407504   # Test channel
        self.validation_service = PirepValidationService(bot)
        self.rate_limit = defaultdict(list)
        self.max_requests_per_minute = 5
        
        # Add persistent views for button handling after bot restart
        try:
            dummy_thread_view = PirepThreadView(0, 0, "", "", "", "")
            dummy_retry_view = PirepRetryView("", "", "", "")
            self.bot.add_view(dummy_thread_view)
            self.bot.add_view(dummy_retry_view)
        except Exception as e:
            logger.error(f"Could not add persistent views: {e}")
    
    def _check_rate_limit(self, user_id: int) -> bool:
        """Check if user is rate limited."""
        now = datetime.now()
        minute_ago = now - timedelta(minutes=1)
        
        # Clean old requests
        self.rate_limit[user_id] = [req_time for req_time in self.rate_limit[user_id] if req_time > minute_ago]
        
        # Check limit
        if len(self.rate_limit[user_id]) >= self.max_requests_per_minute:
            return False
        
        # Add current request
        self.rate_limit[user_id].append(now)
        return True

    # ------------------------------------------------------------------
    # WEBHOOK THREAD SYSTEM
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Automatic webhook listener for thread validation."""
        # Watch both production and test channels
        if message.channel.id not in [self.WATCH_CHANNEL_ID, self.TEST_CHANNEL_ID]:
            return
        
        # For test channel, accept any message with embed (not just webhooks)
        # For production channel, only accept webhook messages
        if message.channel.id == self.WATCH_CHANNEL_ID and not message.webhook_id:
            return
        
        if not message.embeds or "New PIREP Filed" not in message.embeds[0].title:
            return

        # Parse webhook data with validation
        try:
            description = message.embeds[0].description
            pilot_match = re.search(r"Pilot: .* \((.+)\)", description)
            flight_match = re.search(r"Flight Number: (.+)", description)
            route_match = re.search(r"Route: (.+?)[-–](.+)", description)  # Handle both - and – with optional spaces

            if not pilot_match or not flight_match or not route_match:
                logger.warning(f"Invalid webhook format in message {message.id}")
                return

            callsign_str = pilot_match.group(1).strip()
            flight_num_str = flight_match.group(1).strip()
            departure_str = route_match.group(1).strip()
            arrival_str = route_match.group(2).strip()
        except (IndexError, AttributeError) as e:
            logger.error(f"Failed to parse webhook data: {e}")
            return

        # Create thread
        try:
            thread = await message.create_thread(
                name=f"Validating {flight_num_str} ({callsign_str})",
                auto_archive_duration=60
            )
        except Exception as e:
            logger.error(f"Could not create thread for {flight_num_str}: {e}")
            return

        # Find and validate PIREP
        target_pirep = await self.validation_service.find_pirep_by_callsign_flight_and_route(callsign_str, flight_num_str, departure_str, arrival_str)

        if not target_pirep:
            retry_view = PirepRetryView(callsign_str, flight_num_str, departure_str, arrival_str)
            await thread.send(
                f"⚠️ **Could not find PIREP in database.**\n"
                f"Callsign: `{callsign_str}` | Flight: `{flight_num_str}` | Route: `{departure_str} - {arrival_str}`\n"
                f"This may happen if the pilot hasn't despawned yet. Use the button below to retry validation once the pilot has despawned.",
                view=retry_view
            )
            return

        await thread.send("🔍 **Analyzing flight data...**")
        
        try:
            report_embed = await self.validation_service.validate_pirep(target_pirep)
            
            # Check if validation failed to find a match (for retry button)
            if "MATCH NOT FOUND" in str(report_embed.fields[0].name if report_embed.fields else ""):
                # Add retry button for failed matches (player didn't despawn)
                retry_view = PirepRetryView(callsign_str, flight_num_str, departure_str, arrival_str)
                await thread.send(embed=report_embed, view=retry_view)
            else:
                # Normal validation with thread buttons
                view = PirepThreadView(target_pirep['pirep_id'], message.id, callsign_str, flight_num_str, departure_str, arrival_str)
                await thread.send(embed=report_embed, view=view)
        except Exception as e:
            await thread.send(f"❌ **Error during validation:** {str(e)}")

    # ------------------------------------------------------------------
    # SLASH COMMAND SYSTEM
    # ------------------------------------------------------------------
    @app_commands.command(name="validate_pireps", description="Validate pending PIREPs one at a time.")
    async def validate_pireps(self, interaction: discord.Interaction):
        """Manual PIREP validation with pagination and rate limiting."""
        if not any("staff" in role.name.lower() for role in interaction.user.roles):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
        
        # Rate limiting
        if not self._check_rate_limit(interaction.user.id):
            return await interaction.response.send_message("Rate limit exceeded. Please wait before trying again.", ephemeral=True)
            
        await interaction.response.defer(ephemeral=False)
        
        try:
            pending_pireps = await self.bot.pireps_model.get_pending_pireps()

            if not pending_pireps:
                return await interaction.followup.send("There are no pending PIREPs to validate.", ephemeral=False)
            
            count_message = await interaction.followup.send(f"📋 **{len(pending_pireps)} PIREPs pending validation**", ephemeral=False)
            
            embed = await self.validation_service.validate_pirep(pending_pireps[0])
            view = PirepPaginationView(self.bot, pending_pireps, 0, count_message, self.validation_service)
            await interaction.followup.send(embed=embed, view=view, ephemeral=False)
            
        except Exception as e:
            logger.error(f"SLASH ERROR: {e}")
            await interaction.followup.send(f"Error: {str(e)}", ephemeral=True)

async def setup(bot: 'MyBot'):
    await bot.add_cog(PirepValidator(bot))
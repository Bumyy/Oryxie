import discord
from discord.ext import commands
from discord import app_commands
from typing import TYPE_CHECKING
import re
from services.pirep_validation_service import PirepValidationService

if TYPE_CHECKING:
    from ..bot import MyBot

class PirepThreadView(discord.ui.View):
    def __init__(self, pirep_id: int, webhook_message_id: int):
        super().__init__(timeout=None)  # Must be None for persistent views
        self.pirep_id = pirep_id
        self.webhook_message_id = webhook_message_id
    
    def _check_staff_role(self, user) -> bool:
        """Check if user has staff role."""
        return any("staff" in role.name.lower() for role in user.roles)
    
    @discord.ui.button(label="🐛 Debug", style=discord.ButtonStyle.secondary, custom_id="pirep_debug")
    async def debug_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_staff_role(interaction.user):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        
        validator_service = PirepValidationService(interaction.client)
        pirep = await interaction.client.pireps_model.get_pirep_by_id(self.pirep_id)
        if pirep:
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
        
        validator_service = PirepValidationService(interaction.client)
        pirep = await interaction.client.pireps_model.get_pirep_by_id(self.pirep_id)
        if pirep:
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
            print(f"Could not add reaction to webhook message: {e}")
        
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
                print(f"Could not update count message: {e}")
        
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
        self.WATCH_CHANNEL_ID = 1459564652945084578
        self.validation_service = PirepValidationService(bot)
        
        # Add persistent view for button handling after bot restart
        try:
            dummy_view = PirepThreadView(0, 0)
            self.bot.add_view(dummy_view)
        except Exception as e:
            print(f"Could not add persistent view: {e}")

    # ------------------------------------------------------------------
    # WEBHOOK THREAD SYSTEM
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Automatic webhook listener for thread validation."""
        if message.channel.id != self.WATCH_CHANNEL_ID or not message.webhook_id:
            return

        if not message.embeds or "New PIREP Filed" not in message.embeds[0].title:
            return

        # Parse webhook data
        description = message.embeds[0].description
        pilot_match = re.search(r"Pilot: .* \((.+)\)", description)
        flight_match = re.search(r"Flight Number: (.+)", description)

        if not pilot_match or not flight_match:
            return

        callsign_str = pilot_match.group(1).strip()
        flight_num_str = flight_match.group(1).strip()

        # Create thread
        try:
            thread = await message.create_thread(
                name=f"Validating {flight_num_str} ({callsign_str})",
                auto_archive_duration=60
            )
        except Exception as e:
            print(f"Could not create thread: {e}")
            return

        # Find and validate PIREP
        target_pirep = await self.validation_service.find_pirep_by_callsign_and_flight(callsign_str, flight_num_str)

        if not target_pirep:
            await thread.send(
                f"⚠️ **Could not find PIREP in database.**\n"
                f"Callsign: `{callsign_str}` | Flight: `{flight_num_str}`\n"
                f"The database might be slow to update. Try running `/validate_pireps` manually in a moment."
            )
            return

        await thread.send("🔍 **Analyzing flight data...**")
        
        try:
            report_embed = await self.validation_service.validate_pirep(target_pirep)
            view = PirepThreadView(target_pirep['pirep_id'], message.id)
            await thread.send(embed=report_embed, view=view)
        except Exception as e:
            await thread.send(f"❌ **Error during validation:** {str(e)}")

    # ------------------------------------------------------------------
    # SLASH COMMAND SYSTEM
    # ------------------------------------------------------------------
    @app_commands.command(name="validate_pireps", description="Validate pending PIREPs one at a time.")
    async def validate_pireps(self, interaction: discord.Interaction):
        """Manual PIREP validation with pagination."""
        if not any("staff" in role.name.lower() for role in interaction.user.roles):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
            
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
            print(f"SLASH ERROR: {e}")
            await interaction.followup.send(f"Error: {str(e)}", ephemeral=True)

async def setup(bot: 'MyBot'):
    await bot.add_cog(PirepValidator(bot))
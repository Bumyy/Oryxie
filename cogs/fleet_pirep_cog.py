import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# Load fleet frames data
FLEET_FRAMES_FILE = "assets/fleet_frames.json"

# Fleet role ID
FLEET_ROLE_ID = 1480252371320967239

def load_fleet_frames():
    """Load fleet frames from JSON file."""
    try:
        with open(FLEET_FRAMES_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading fleet frames: {e}")
        return {}

class FleetPirepModal(discord.ui.Modal, title="Fleet Flight Details"):
    """Modal for entering fleet flight details."""
    
    departure = discord.ui.TextInput(
        label="Departure ICAO",
        placeholder="e.g., OTHH",
        required=True,
        max_length=4,
        min_length=4
    )
    
    arrival = discord.ui.TextInput(
        label="Arrival ICAO",
        placeholder="e.g., EGLL",
        required=True,
        max_length=4,
        min_length=4
    )
    
    hours = discord.ui.TextInput(
        label="Flight Duration Hours (HH)",
        placeholder="e.g., 07",
        required=True,
        max_length=2
    )
    
    minutes = discord.ui.TextInput(
        label="Flight Duration Minutes (MM)",
        placeholder="e.g., 30",
        required=True,
        max_length=2
    )
    
    def __init__(self, cog, frame_name: str, frame_data: dict, pilot1_id: int, pilot2_id: Optional[int], pilot1_name: str = None, pilot2_name: str = None):
        super().__init__()
        self.cog = cog
        self.frame_name = frame_name
        self.frame_data = frame_data
        self.pilot1_id = pilot1_id
        self.pilot2_id = pilot2_id
        self.pilot1_name = pilot1_name  # Discord member display name
        self.pilot2_name = pilot2_name  # Discord member display name
        
        # Add frame name to frame_data for the service
        self.frame_data['name'] = frame_name
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        # Validate ICAOs
        dep_icao = self.departure.value.upper()
        arr_icao = self.arrival.value.upper()
        
        if len(dep_icao) != 4 or len(arr_icao) != 4:
            await interaction.followup.send("❌ Invalid ICAO code. Must be 4 characters.", ephemeral=True)
            return
        
        # Validate and parse duration
        try:
            hours = int(self.hours.value)
            minutes = int(self.minutes.value)
            if not (0 <= hours <= 99 and 0 <= minutes <= 59):
                raise ValueError()
        except ValueError:
            await interaction.followup.send("❌ Invalid time format. Hours: 0-99, Minutes: 0-59", ephemeral=True)
            return
        
        # Submit fleet PIREP
        try:
            pirep_service = self.cog._get_pirep_service()
            result = await pirep_service.submit_fleet_pirep(
                pilot1_discord_id=self.pilot1_id,
                pilot2_discord_id=self.pilot2_id,
                frame_data=self.frame_data,
                dep_icao=dep_icao,
                arr_icao=arr_icao,
                total_duration_hours=hours,
                total_duration_minutes=minutes,
                pilot1_name=self.pilot1_name,
                pilot2_name=self.pilot2_name
            )
            
            if result.get('error'):
                await interaction.followup.send(f"❌ Error: {result['error']}", ephemeral=True)
                return
            
            # Build success embed
            embed = discord.Embed(
                title="✅ Fleet PIREP Filed Successfully!",
                color=0x00FF00
            )
            
            total_duration = f"{hours:02d}:{minutes:02d}"
            embed.add_field(name="✈️ Flight", value=f"{self.frame_name} ({dep_icao} → {arr_icao})", inline=False)
            embed.add_field(name="⏱️ Total Duration", value=total_duration, inline=False)
            
            # Pilot 1 result
            p1_result = result.get('pilot1_result')
            if p1_result:
                p1_status = "✅" if p1_result.get('success') else "❌"
                embed.add_field(
                    name=f"👤 Pilot 1",
                    value=f"{p1_status} {p1_result.get('pilot_name', 'Unknown')} - {p1_result.get('duration', 'N/A')} hours",
                    inline=False
                )
            
            # Pilot 2 result (if exists)
            if self.pilot2_id:
                p2_result = result.get('pilot2_result')
                if p2_result:
                    p2_status = "✅" if p2_result.get('success') else "❌"
                    embed.add_field(
                        name=f"👤 Pilot 2",
                        value=f"{p2_status} {p2_result.get('pilot_name', 'Unknown')} - {p2_result.get('duration', 'N/A')} hours",
                        inline=False
                    )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error submitting fleet PIREP: {e}")
            await interaction.followup.send(f"❌ Error submitting PIREP: {str(e)}", ephemeral=True)


class FleetPirepCog(commands.Cog):
    """Cog for handling fleet PIREP filing commands."""
    
    def __init__(self, bot):
        self.bot = bot
        self.fleet_frames = load_fleet_frames()
        self._pirep_service = None
    
    def _get_pirep_service(self):
        """Get or create PirepFilingService instance."""
        if self._pirep_service is None:
            from services.pirep_filing_service import PirepFilingService
            self._pirep_service = PirepFilingService(self.bot)
        return self._pirep_service
    
    async def frame_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete for fleet frames with full data."""
        frames = list(self.fleet_frames.keys())
        
        # Filter by frame name
        if current:
            frames = [f for f in frames if current.upper() in f.upper()]
        
        # Build choices with full data: "FRAME (ICAO - Livery)"
        choices = []
        for frame in frames[:25]:
            frame_data = self.fleet_frames[frame]
            icao = frame_data.get('icao', 'N/A')
            livery = frame_data.get('livery', 'N/A')
            display_name = f"{frame} ({icao} - {livery})"
            choices.append(app_commands.Choice(name=display_name, value=frame))
        
        return choices
    
    @app_commands.command(name="live_fleet_pirep", description="File a dual-pilot PIREP for fleet flights")
    @app_commands.checks.has_role(FLEET_ROLE_ID)
    @app_commands.autocomplete(frame=frame_autocomplete)
    async def live_fleet_pirep(
        self, 
        interaction: discord.Interaction,
        frame: str,
        pilot_1: discord.Member,
        pilot_2: discord.Member = None
    ):
        """File a fleet PIREP with optional dual pilot split."""
        
        # Validate frame exists
        if frame not in self.fleet_frames:
            await interaction.response.send_message("❌ Invalid frame selected.", ephemeral=True)
            return
        
        # Validate pilot 1
        if not pilot_1:
            await interaction.response.send_message("❌ Pilot 1 is required.", ephemeral=True)
            return
        
        # Validate pilots are different
        if pilot_2 and pilot_1.id == pilot_2.id:
            await interaction.response.send_message("❌ Cannot select the same pilot for both positions.", ephemeral=True)
            return
        
        # Get frame data
        frame_data = self.fleet_frames[frame]
        
        # Get Discord IDs and display names directly from Member objects
        pilot1_id = pilot_1.id
        pilot1_name = pilot_1.display_name
        pilot2_id = pilot_2.id if pilot_2 else None
        pilot2_name = pilot_2.display_name if pilot_2 else None
        
        # Send modal
        modal = FleetPirepModal(self, frame, frame_data, pilot1_id, pilot2_id, pilot1_name, pilot2_name)
        await interaction.response.send_modal(modal)

async def setup(bot):
    """Setup function for loading the cog."""
    await bot.add_cog(FleetPirepCog(bot))

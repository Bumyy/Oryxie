import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import time
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Fallback JSON file if IF Live API is temporarily unreachable
FLEET_FRAMES_FILE = "assets/fleet_frames.json"

ORG_ID = os.getenv("IF_ORGANIZATION_ID", "").strip()
TOKEN_OWNER_ID = 1
FLEET_CACHE_SECONDS = 300

# Allowed Role IDs (Fleet role + Executive role)
FLEET_ROLE_ID = 1484645309983490119
EXECUTIVE_ROLE_ID = 1421074400773668894
ALLOWED_ROLE_IDS = (FLEET_ROLE_ID, EXECUTIVE_ROLE_ID)


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def load_fleet_frames():
    """Fallback: Load fleet frames from JSON file if IF Live is unavailable."""
    try:
        if os.path.exists(FLEET_FRAMES_FILE):
            with open(FLEET_FRAMES_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading fallback fleet frames: {e}")
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
    
    def __init__(self, cog, frame_name: str, frame_data: dict, pilot1_id: int, pilot2_id: Optional[int], pilot1_name: str = None, pilot2_name: str = None, multiplier: str = "150000"):
        super().__init__()
        self.cog = cog
        self.frame_name = frame_name
        self.frame_data = frame_data
        self.pilot1_id = pilot1_id
        self.pilot2_id = pilot2_id
        self.pilot1_name = pilot1_name  # Discord member display name
        self.pilot2_name = pilot2_name  # Discord member display name
        self.multiplier = multiplier
        
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
                pilot2_name=self.pilot2_name,
                multiplier=self.multiplier
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
            multiplier_label = "2x (Lift Users)" if self.multiplier == "200000" else "1.5x (Non-Lift Users)"
            embed.add_field(name="✈️ Flight", value=f"{self.frame_name} ({dep_icao} → {arr_icao})", inline=False)
            embed.add_field(name="⏱️ Total Duration", value=total_duration, inline=True)
            embed.add_field(name="✨ Multiplier", value=multiplier_label, inline=True)
            
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
        self.fleet_frames: Dict[str, dict] = load_fleet_frames()
        self._fleet_at = 0.0
        self._fleet_lock = asyncio.Lock()
        self._pirep_service = None
    
    def _get_pirep_service(self):
        """Get or create PirepFilingService instance."""
        if self._pirep_service is None:
            from services.pirep_filing_service import PirepFilingService
            self._pirep_service = PirepFilingService(self.bot)
        return self._pirep_service

    async def _get_live_fleet_frames(self) -> Dict[str, dict]:
        """Fetch active fleet registrations from IF Live and enrich with DB aircraft/livery data."""
        now = time.monotonic()
        if self.fleet_frames and (now - self._fleet_at < FLEET_CACHE_SECONDS):
            return self.fleet_frames

        async with self._fleet_lock:
            now = time.monotonic()
            if self.fleet_frames and (now - self._fleet_at < FLEET_CACHE_SECONDS):
                return self.fleet_frames

            manager = getattr(self.bot, "if_live_manager", None)
            if not ORG_ID or manager is None:
                return self.fleet_frames or load_fleet_frames()

            try:
                async with await manager.get_client(TOKEN_OWNER_ID) as client:
                    fleet = await client.list_organization_aircraft(ORG_ID)

                fleet_service = getattr(self.bot, "fleet_service", None)
                fleet_db_map = (
                    await fleet_service.get_fleet_db_map(fleet)
                    if fleet_service
                    else {}
                )

                live_frames: Dict[str, dict] = {}
                for ac in sorted(
                    list(fleet or []),
                    key=lambda item: str(_get(item, "registration", "")).upper(),
                ):
                    reg = str(_get(ac, "registration", "") or "").strip()
                    if not reg:
                        continue
                    if_id = str(_get(ac, "aircraft_id", "") or "").strip()
                    db_row = fleet_db_map.get(if_id) or {}

                    icao = (
                        db_row.get("icao")
                        or db_row.get("name")
                        or "Unknown"
                    )
                    livery = (
                        db_row.get("liveryname")
                        or db_row.get("livery")
                        or "VA Fleet"
                    )
                    live_frames[reg] = {
                        "icao": str(icao).strip(),
                        "livery": str(livery).strip(),
                        "aircraft_id": db_row.get("id"),
                    }

                if live_frames:
                    self.fleet_frames = live_frames
                    self._fleet_at = time.monotonic()
            except Exception as e:
                logger.error(f"Error fetching live fleet frames from IF Live: {e}")
                if not self.fleet_frames:
                    self.fleet_frames = load_fleet_frames()

            return self.fleet_frames
    
    async def frame_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete for fleet frames with full data from IF Live + DB."""
        frames_map = await self._get_live_fleet_frames()
        frames = list(frames_map.keys())
        
        # Filter by frame name, ICAO, or livery
        if current:
            query = current.upper()
            frames = [
                f for f in frames
                if query in f.upper()
                or query in frames_map[f].get('icao', '').upper()
                or query in frames_map[f].get('livery', '').upper()
            ]
        
        # Build choices with full data: "FRAME (ICAO - Livery)"
        choices = []
        for frame in frames[:25]:
            frame_data = frames_map[frame]
            icao = frame_data.get('icao', 'N/A')
            livery = frame_data.get('livery', 'N/A')
            display_name = f"{frame} ({icao} - {livery})"
            choices.append(app_commands.Choice(name=display_name[:100], value=frame))
        
        return choices
    
    @app_commands.command(name="live_fleet_pirep", description="File a dual-pilot PIREP for fleet flights")
    @app_commands.checks.has_any_role(*ALLOWED_ROLE_IDS)
    @app_commands.autocomplete(frame=frame_autocomplete)
    @app_commands.describe(
        frame="Select the aircraft registration",
        multiplier="1.5x for non-lift users, 2x for lift users",
        pilot_1="Primary pilot",
        pilot_2="Second pilot (optional, splits flight time 50/50)",
    )
    @app_commands.choices(
        multiplier=[
            app_commands.Choice(name="1.5x (For Non-Lift Users)", value="150000"),
            app_commands.Choice(name="2x (For Lift Users)", value="200000"),
        ]
    )
    async def live_fleet_pirep(
        self, 
        interaction: discord.Interaction,
        frame: str,
        multiplier: app_commands.Choice[str],
        pilot_1: discord.Member,
        pilot_2: discord.Member = None
    ):
        """File a fleet PIREP with optional dual pilot split."""
        frames_map = await self._get_live_fleet_frames()
        
        # Match frame case-insensitively
        matched_key = next(
            (k for k in frames_map if k.upper() == frame.strip().upper()),
            None,
        )
        if not matched_key:
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
        
        # Get frame data (copy so modal mutation doesn't alter cache)
        frame_data = dict(frames_map[matched_key])
        
        # Get Discord IDs and display names directly from Member objects
        pilot1_id = pilot_1.id
        pilot1_name = pilot_1.display_name
        pilot2_id = pilot_2.id if pilot_2 else None
        pilot2_name = pilot_2.display_name if pilot_2 else None
        selected_multiplier = multiplier.value if multiplier else "150000"
        
        # Send modal
        modal = FleetPirepModal(
            self,
            matched_key,
            frame_data,
            pilot1_id,
            pilot2_id,
            pilot1_name,
            pilot2_name,
            multiplier=selected_multiplier,
        )
        await interaction.response.send_modal(modal)

    @live_fleet_pirep.error
    async def live_fleet_pirep_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, (app_commands.MissingRole, app_commands.MissingAnyRole)):
            await interaction.response.send_message(
                "❌ You do not have the required Fleet or Executive role to use this command.",
                ephemeral=True
            )
        else:
            logger.error(f"Error in live_fleet_pirep: {error}")
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Error: {error}", ephemeral=True)

async def setup(bot):
    """Setup function for loading the cog."""
    await bot.add_cog(FleetPirepCog(bot))

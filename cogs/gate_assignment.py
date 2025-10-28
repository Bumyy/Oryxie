import discord
from discord import app_commands
from discord.ext import commands
import json
import os
from datetime import datetime
import logging
from typing import List
import asyncio

from database.routes_model import RoutesModel

logger = logging.getLogger('oryxie.cogs.gate_assignment')

def format_duration(seconds: int) -> str:
    try:
        seconds = int(seconds)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours:02d}:{minutes:02d}"
    except (ValueError, TypeError):
        return ""

class FlightDetailsModal(discord.ui.Modal):
    def __init__(self, event_name: str, sorted_attendees: list, default_message: str, flight_number: str):
        super().__init__(title=f"Details for {event_name}")
        self.sorted_attendees = sorted_attendees

        # Ensure default message fits in modal
        safe_default = default_message[:1800] if len(default_message) > 1800 else default_message
        
        self.message_content = discord.ui.TextInput(
            label="Event Message (Edit as needed)",
            style=discord.TextStyle.paragraph,
            default=safe_default,
            max_length=1800
        )
        
        self.gates = discord.ui.TextInput(
            label=f"Gates (Need {len(self.sorted_attendees)}) - One per line",
            style=discord.TextStyle.paragraph,
            placeholder="Terminal 1A\nA1\nA2\nA3\nA4\n\nTerminal 2B\nB1\nB2",
            max_length=1000
        )

        self.add_item(self.message_content)
        self.add_item(self.gates)

    async def send_with_retry(self, interaction: discord.Interaction, content: str, max_retries: int = 3):
        """Send message with retry logic for rate limiting"""
        for attempt in range(max_retries):
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(content)
                else:
                    await interaction.followup.send(content)
                return True
            except discord.HTTPException as e:
                if e.status == 429:  # Rate limited
                    retry_after = getattr(e, 'retry_after', 2 ** attempt)
                    logger.warning(f"Rate limited, retrying after {retry_after}s (attempt {attempt + 1})")
                    await asyncio.sleep(retry_after)
                    continue
                else:
                    raise e
            except Exception as e:
                if attempt == max_retries - 1:
                    raise e
                await asyncio.sleep(1)
        return False

    def split_message(self, message: str, max_length: int = 2000) -> List[str]:
        """Split long messages into chunks"""
        if len(message) <= max_length:
            return [message]
        
        chunks = []
        current_chunk = ""
        
        for line in message.split('\n'):
            if len(current_chunk) + len(line) + 1 > max_length:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = line + '\n'
                else:
                    # Single line too long, force split
                    while len(line) > max_length:
                        chunks.append(line[:max_length])
                        line = line[max_length:]
                    current_chunk = line + '\n'
            else:
                current_chunk += line + '\n'
        
        if current_chunk.strip():
            chunks.append(current_chunk.strip())
        
        return chunks

    async def on_submit(self, interaction: discord.Interaction):
        try:
            gate_lines = [line.strip() for line in self.gates.value.strip().split('\n') if line.strip()]
            
            assignable_gates = []
            for line in gate_lines:
                if len(line) <= 6 and any(c.isdigit() or c.isalpha() for c in line):
                    if not any(word in line.lower() for word in ['terminal', 'concourse', 'pier', 'gate']):
                        assignable_gates.append(line)

            if len(assignable_gates) < len(self.sorted_attendees):
                error_msg = f"**Error:** You provided {len(assignable_gates)} assignable gates, but there are {len(self.sorted_attendees)} attendees.\n\nFound gates: {assignable_gates}"
                await self.send_with_retry(interaction, error_msg)
                return

            gate_assignment_text = "\n\n**📍 Gate Assignments:**\n"
            attendee_index = 0
            
            for line in gate_lines:
                if any(word in line.lower() for word in ['terminal', 'concourse', 'pier']) or len(line) > 6:
                    gate_assignment_text += f"\n**{line}**\n"
                else:
                    if attendee_index < len(self.sorted_attendees):
                        attendee = self.sorted_attendees[attendee_index]
                        gate_assignment_text += f"- {line.upper()}: {attendee.mention}\n"
                        attendee_index += 1
                    else:
                        gate_assignment_text += f"- {line.upper()}: Vacant\n"
            
            final_message = self.message_content.value + gate_assignment_text
            
            # Handle long messages by splitting
            message_chunks = self.split_message(final_message)
            
            if len(message_chunks) == 1:
                await self.send_with_retry(interaction, message_chunks[0])
            else:
                # Send first chunk as response
                await self.send_with_retry(interaction, message_chunks[0])
                
                # Send remaining chunks as followups with delay
                for i, chunk in enumerate(message_chunks[1:], 1):
                    await asyncio.sleep(0.5)  # Prevent rate limiting
                    try:
                        await interaction.followup.send(f"**Part {i + 1}:**\n{chunk}")
                    except discord.HTTPException as e:
                        if e.status == 429:
                            await asyncio.sleep(2)
                            await interaction.followup.send(f"**Part {i + 1}:**\n{chunk}")
                        else:
                            logger.error(f"Failed to send message part {i + 1}: {e}")
            
        except discord.HTTPException as e:
            logger.error(f"Discord API error in FlightDetailsModal.on_submit: {e}")
            error_msg = "Discord API error occurred. Please try again in a moment."
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(error_msg, ephemeral=True)
                else:
                    await interaction.followup.send(error_msg, ephemeral=True)
            except:
                pass
                
        except Exception as e:
            logger.error(f"Unexpected error in FlightDetailsModal.on_submit: {e}")
            error_msg = "An unexpected error occurred. Please try again."
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(error_msg, ephemeral=True)
                else:
                    await interaction.followup.send(error_msg, ephemeral=True)
            except:
                pass

class GateAssignmentView(discord.ui.View):
    def __init__(self, event_name: str, sorted_attendees: list, default_message: str, flight_number: str):
        super().__init__(timeout=180)
        self.event_name = event_name
        self.sorted_attendees = sorted_attendees
        self.default_message = default_message
        self.flight_number = flight_number

    @discord.ui.button(label="Assign Gates", style=discord.ButtonStyle.primary)
    async def assign_gates_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Truncate default message if too long for modal
            truncated_message = self.default_message[:1800] if len(self.default_message) > 1800 else self.default_message
            
            modal = FlightDetailsModal(self.event_name, self.sorted_attendees, truncated_message, self.flight_number)
            await interaction.response.send_modal(modal)
        except discord.InteractionResponded:
            logger.warning("Interaction already responded to")
            return
        except discord.HTTPException as e:
            logger.error(f"Failed to send modal: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Failed to open gate assignment form. Please try again.", ephemeral=True)
            else:
                await interaction.followup.send("Failed to open gate assignment form. Please try again.", ephemeral=True)
        except Exception as e:
            logger.error(f"Unexpected error opening modal: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("An error occurred. Please try again.", ephemeral=True)
            else:
                await interaction.followup.send("An error occurred. Please try again.", ephemeral=True)

class GateAssignment(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ranks = self.load_ranks()
        self.routes_model = RoutesModel(self.bot.db_manager)

    def load_ranks(self):
        try:
            path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'assets', 'rank.json')
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"Successfully loaded {len(data['priority'])} ranks from assets/rank.json")
                return data['priority']
        except FileNotFoundError:
            logger.error("CRITICAL: assets/rank.json not found. The gate assignment command will not work.")
            return []
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"CRITICAL: Error reading assets/rank.json: {e}. The gate assignment command will not work.")
            return []

    def get_member_rank_priority(self, member: discord.Member) -> int:
        if not self.ranks:
            return 999

        user_roles = [role.name for role in member.roles]
        
        for rank_index, rank in enumerate(self.ranks):
            rank_parts = [part.strip() for part in rank.split('|')]
            
            for role in user_roles:
                for rank_part in rank_parts:
                    if rank_part == role or rank_part in role:
                        return rank_index
        
        return 999
    
    def generate_callsign(self, flight_number: str) -> str:
        """Generate callsign based on flight number prefix."""
        if not flight_number:
            return ""
        
        if flight_number.lower().startswith('qr'):
            return "Qatari --- VA"
        else:
            return "--- QR"

    async def event_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        events = interaction.guild.scheduled_events
        choices = []
        for event in events:
            if current.lower() in event.name.lower():
                choices.append(app_commands.Choice(name=event.name, value=str(event.id)))
        return choices[:25]

    @app_commands.command(name="gate_assignment", description="Assigns event gates to attendees based on rank.")
    @app_commands.autocomplete(event=event_autocomplete)
    async def gate_assignment(self, interaction: discord.Interaction, event: str, flight_number: str = None):
        await interaction.response.defer(ephemeral=True)

        if not any(role.name.lower() == "staff" for role in interaction.user.roles):
            await interaction.followup.send("You need the Staff role to use this command.", ephemeral=True)
            return

        try:
            event_id = int(event)
            scheduled_event = discord.utils.get(interaction.guild.scheduled_events, id=event_id)
        except (ValueError, TypeError):
            await interaction.followup.send("Invalid event selected.", ephemeral=True)
            return

        if not scheduled_event:
            await interaction.followup.send("Event not found.", ephemeral=True)
            return

        user_list = [user async for user in scheduled_event.users()]
        if not user_list:
            await interaction.followup.send("No attendees found for this event.", ephemeral=True)
            return

        attendees = [member for user in user_list if (member := interaction.guild.get_member(user.id)) is not None]
        
        sorted_attendees = sorted(attendees, key=self.get_member_rank_priority)

        route_info = None
        status_message = "Proceed to assign gates."
        if flight_number:
            try:
                route_info = await self.routes_model.find_route_by_fltnum(flight_number)
                if route_info:
                    status_message = "Route details found and pre-filled."
                else:
                    status_message = "Route not found for the given flight number."
            except Exception as e:
                logger.error(f"Error fetching route details: {e}")
                status_message = "An error occurred while fetching route details."

        event_time = scheduled_event.start_time.strftime("%H:%Mz")
        current_date = datetime.utcnow().strftime("%d/%m/%Y")
        
        dep = route_info.get('dep', '') if route_info else ''
        arr = route_info.get('arr', '') if route_info else ''
        duration = format_duration(route_info['duration']) if route_info and route_info.get('duration') else ''
        aircraft_text = ', '.join(ac.get('name', ac.get('icao', 'Unknown')) for ac in route_info['aircraft']) if route_info and route_info.get('aircraft') else 'Not specified'

        callsign = self.generate_callsign(flight_number) if flight_number else ''
        
        default_message = f"""{scheduled_event.name}
**📅 Date:** {current_date}
**⏰ Time:** {event_time}
**🌍 Departure:** {dep}
**🌍 Destination:** {arr}
**🌐 Multiplier:** 3x
**#️⃣ Flight Number:** {flight_number or ""}
**⏱️ Flight Duration:** {duration}
**✈️ Aircraft Type:** {aircraft_text}

**Flight Details:**

**📶 Callsign:** {callsign}
**⛽ Fuel Tank Capacity:** kg
**🫂 Passengers:** 
**📦 Cargo:** kg
**☁️ Cruise Alt:** FL
**💨 Cruise Speed:** M"""

        view = GateAssignmentView(scheduled_event.name, sorted_attendees, default_message, flight_number)
        await interaction.followup.send(status_message, view=view, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(GateAssignment(bot))
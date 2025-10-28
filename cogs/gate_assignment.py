import discord
from discord import app_commands
from discord.ext import commands
import json
import os
from datetime import datetime
import logging
from typing import List

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

        self.message_content = discord.ui.TextInput(
            label="Event Message (Edit as needed)",
            style=discord.TextStyle.paragraph,
            default=default_message,
            max_length=2000
        )
        
        self.gates = discord.ui.TextInput(
            label=f"Gates (Need {len(self.sorted_attendees)}) - One per line",
            style=discord.TextStyle.paragraph,
            placeholder="Terminal 1A\nA1\nA2\nA3\nA4\n\nTerminal 2B\nB1\nB2",
            max_length=1000
        )

        self.add_item(self.message_content)
        self.add_item(self.gates)

    async def on_submit(self, interaction: discord.Interaction):
        gate_lines = [line.strip() for line in self.gates.value.strip().split('\n') if line.strip()]
        
        assignable_gates = []
        for line in gate_lines:
            if len(line) <= 6 and any(c.isdigit() or c.isalpha() for c in line):
                if not any(word in line.lower() for word in ['terminal', 'concourse', 'pier', 'gate']):
                    assignable_gates.append(line)

        if len(assignable_gates) < len(self.sorted_attendees):
            await interaction.response.send_message(
                f"**Error:** You provided {len(assignable_gates)} assignable gates, but there are {len(self.sorted_attendees)} attendees.\n\nFound gates: {assignable_gates}",
                ephemeral=True
            )
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
        await interaction.response.send_message(final_message)

class GateAssignmentView(discord.ui.View):
    def __init__(self, event_name: str, sorted_attendees: list, default_message: str, flight_number: str):
        super().__init__(timeout=180)
        self.event_name = event_name
        self.sorted_attendees = sorted_attendees
        self.default_message = default_message
        self.flight_number = flight_number

    @discord.ui.button(label="Assign Gates", style=discord.ButtonStyle.primary)
    async def assign_gates_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = FlightDetailsModal(self.event_name, self.sorted_attendees, self.default_message, self.flight_number)
        await interaction.response.send_modal(modal)

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
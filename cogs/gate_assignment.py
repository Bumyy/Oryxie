import discord
from discord import app_commands
from discord.ext import commands
import json
import os
from datetime import datetime
import logging
from typing import List
import asyncio
import re

from database.routes_model import RoutesModel
from services.priority_service import PriorityService

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
    def __init__(self, event_name: str, sorted_attendees: list, default_message: str, flight_number: str, event_organiser_text: str = "", event_organiser_mention: str = "", priority_service=None):

        
        try:
            title = " ".join(event_name.split()[:4])[:45]
            super().__init__(title=title)
            
            self.sorted_attendees = sorted_attendees
            self.event_organiser_text = event_organiser_text
            self.event_organiser_mention = event_organiser_mention
            self.priority_service = priority_service

            # Ensure default message fits in modal
            safe_default = default_message[:1800] if len(default_message) > 1800 else default_message

            
            try:
                self.message_content = discord.ui.TextInput(
                    label="Event Message (Edit as needed)",
                    style=discord.TextStyle.paragraph,
                    default=safe_default,
                    max_length=1800
                )

            except Exception as e:
                logger.error(f"Failed to create message_content TextInput: {e}")
                raise
            
            try:
                self.gates = discord.ui.TextInput(
                    label=f"Gates (Need {len(self.sorted_attendees)}) - One per line",
                    style=discord.TextStyle.paragraph,
                    placeholder="Terminal 1A\nA1\nA2\nA3\nA4\n\nTerminal 2B\nB1\nB2",
                    max_length=1000
                )

            except Exception as e:
                logger.error(f"Failed to create gates TextInput: {e}")
                raise

            try:
                self.add_item(self.message_content)
                self.add_item(self.gates)
            except Exception as e:
                logger.error(f"Failed to add items to modal: {e}")
                raise
                
        except Exception as e:
            logger.error(f"FlightDetailsModal initialization failed: {e}")
            raise

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
            
            # Parse gates and create assignments
            gate_assignments = []
            current_terminal = ""
            attendee_index = 0
            
            for line in gate_lines:
                if any(word in line.lower() for word in ['terminal', 'concourse', 'pier']) or len(line) > 6:
                    current_terminal = line
                else:
                    if attendee_index < len(self.sorted_attendees):
                        attendee = self.sorted_attendees[attendee_index]
                        gate_assignments.append({
                            'terminal': current_terminal,
                            'gate': line.upper(),
                            'pilot': attendee,
                            'position': attendee_index + 1
                        })
                        attendee_index += 1
            
            # Separate into teams based on position
            team_a_assignments = [assignment for assignment in gate_assignments if assignment['position'] % 2 == 1]
            team_b_assignments = [assignment for assignment in gate_assignments if assignment['position'] % 2 == 0]
            
            # Build team A section
            if team_a_assignments:
                gate_assignment_text += "\n**Team A**\n"
                for assignment in team_a_assignments:
                    terminal_gate = f"{assignment['terminal']} {assignment['gate']}" if assignment['terminal'] else assignment['gate']
                    gate_assignment_text += f"- {terminal_gate}: {assignment['pilot'].mention}\n"
            
            # Build team B section
            if team_b_assignments:
                gate_assignment_text += "\n**Team B**\n"
                for assignment in team_b_assignments:
                    terminal_gate = f"{assignment['terminal']} {assignment['gate']}" if assignment['terminal'] else assignment['gate']
                    gate_assignment_text += f"- {terminal_gate}: {assignment['pilot'].mention}\n"
            
            # Add event rules with dynamic event organiser mention
            organiser_rule = f"Copy flight plan from {self.event_organiser_mention} only" if self.event_organiser_mention else "Copy flight plan from @event organiser only"
            event_rules = f"\n\n**Event Rules:**\n1) {organiser_rule}\n2) On air, everyone should maintain at least 5nm distance separation\n3) All participants should have the same cruise speed and cruise altitude before setting flight in AP+\n4) Taxi speed will not exceed 25 knots in straight lines and 10 knots during turns\n5) During turns, always keep an eye on surroundings - blocking other pilot paths and high-speed taxi will not be tolerated\n6) Any violation of the above rules will result in warnings and retraining\n7) Enjoy your flight!"
            
            final_message = self.message_content.value + self.event_organiser_text + gate_assignment_text + event_rules
            
            # Assign team roles if priority service is available and role IDs are set
            if self.priority_service and self.priority_service.TEAM_A_ROLE_ID and self.priority_service.TEAM_B_ROLE_ID:
                role_errors = []
                try:
                    team_a, team_b = await self.priority_service.assign_teams(self.sorted_attendees)
                    guild = interaction.guild
                    team_a_role = guild.get_role(self.priority_service.TEAM_A_ROLE_ID)
                    team_b_role = guild.get_role(self.priority_service.TEAM_B_ROLE_ID)
                    
                    if team_a_role and team_b_role:
                        # Remove existing team roles from all attendees first
                        for member in self.sorted_attendees:
                            try:
                                if team_a_role in member.roles:
                                    await member.remove_roles(team_a_role)
                                    await asyncio.sleep(0.2)  # Rate limit delay
                                if team_b_role in member.roles:
                                    await member.remove_roles(team_b_role)
                                    await asyncio.sleep(0.2)  # Rate limit delay
                            except Exception as e:
                                role_errors.append(f"Failed to remove roles from {member.display_name}: {str(e)}")
                        
                        # Small delay before adding new roles
                        await asyncio.sleep(0.5)
                        
                        # Assign new team roles
                        for member in team_a:
                            try:
                                await member.add_roles(team_a_role)
                                await asyncio.sleep(0.2)  # Rate limit delay
                            except Exception as e:
                                role_errors.append(f"Failed to add Team A role to {member.display_name}: {str(e)}")
                        
                        for member in team_b:
                            try:
                                await member.add_roles(team_b_role)
                                await asyncio.sleep(0.2)  # Rate limit delay
                            except Exception as e:
                                role_errors.append(f"Failed to add Team B role to {member.display_name}: {str(e)}")
                        
                        logger.info(f"Assigned {len(team_a)} members to Team A and {len(team_b)} members to Team B")
                        
                        # Send role error debug if any failures occurred
                        if role_errors:
                            error_message = "**⚠️ Role Assignment Issues:**\n" + "\n".join(role_errors)
                            try:
                                await interaction.followup.send(error_message, ephemeral=True)
                            except:
                                pass  # Don't fail if we can't send debug message
                                
                except Exception as e:
                    logger.error(f"Error assigning team roles: {e}")
                    # Send debug message about the failure
                    try:
                        await interaction.followup.send(f"**❌ Team Role Assignment Failed:**\n{str(e)}", ephemeral=True)
                    except:
                        pass
            
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
    def __init__(self, event_name: str, sorted_attendees: list, default_message: str, flight_number: str, event_organiser_text: str = "", event_organiser_mention: str = "", priority_service=None):

        
        try:
            super().__init__(timeout=180)

            
            self.event_name = event_name
            self.sorted_attendees = sorted_attendees
            self.default_message = default_message
            self.flight_number = flight_number
            self.event_organiser_text = event_organiser_text
            self.event_organiser_mention = event_organiser_mention
            self.priority_service = priority_service
            
        except Exception as e:
            logger.error(f"GateAssignmentView initialization failed: {e}")
            raise

    @discord.ui.button(label="Assign Gates", style=discord.ButtonStyle.primary)
    async def assign_gates_button(self, interaction: discord.Interaction, button: discord.ui.Button):

        
        try:
            # Truncate default message if too long for modal
            truncated_message = self.default_message[:1800] if len(self.default_message) > 1800 else self.default_message
            modal = FlightDetailsModal(self.event_name, self.sorted_attendees, truncated_message, self.flight_number, self.event_organiser_text, self.event_organiser_mention, self.priority_service)
            await interaction.response.send_modal(modal)
            
        except discord.InteractionResponded as e:
            logger.error(f"Interaction already responded: {e}")
            logger.warning(f"Interaction already responded to: {e}")
            return
        except discord.HTTPException as e:
            logger.error(f"Failed to send modal: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("Failed to open gate assignment form. Please try again.", ephemeral=True)
                else:
                    await interaction.followup.send("Failed to open gate assignment form. Please try again.", ephemeral=True)
            except Exception:
                logger.error("Failed to send error message to user")
        except Exception as e:
            logger.error(f"Unexpected error opening modal: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("An error occurred. Please try again.", ephemeral=True)
                else:
                    await interaction.followup.send("An error occurred. Please try again.", ephemeral=True)
            except Exception:
                logger.error("Failed to send error message to user")

class GateAssignment(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ranks = self.load_ranks()
        self.routes_model = RoutesModel(self.bot.db_manager)
        self.priority_service = PriorityService(self.bot)

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

    def set_team_roles(self, team_a_id: int, team_b_id: int):
        """Set the team role IDs for automatic assignment"""
        self.priority_service.set_team_role_ids(team_a_id, team_b_id)
        logger.info(f"Team role IDs set: Team A = {team_a_id}, Team B = {team_b_id}")


    
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
    async def gate_assignment(self, interaction: discord.Interaction, event: str, flight_number: str = None, event_organiser: str = None):
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
        
        # Get event organiser ID if provided
        event_organiser_id = None
        if event_organiser:
            event_organiser = event_organiser.strip()
            if event_organiser.isdigit() and len(event_organiser) == 3:
                full_callsign = f"QRV{event_organiser}"
                try:
                    pilot_data = await self.bot.pilots_model.get_pilot_by_callsign(full_callsign)
                    if pilot_data and pilot_data.get('discordid'):
                        event_organiser_id = pilot_data['discordid']
                except Exception as e:
                    logger.error(f"Error looking up event organiser: {e}")
        
        # Sort attendees by priority
        sorted_attendees = await self.priority_service.sort_members_by_priority(attendees, event_organiser_id)
        
        # Assign teams
        team_a, team_b = await self.priority_service.assign_teams(sorted_attendees)
        
        # Get debug info
        debug_info = await self.priority_service.get_priority_debug_info(sorted_attendees, event_organiser_id)
        
        # Add team assignment info to debug
        team_info = []
        team_info.append(f"\n**Team A ({len(team_a)} members):** {', '.join([m.mention for m in team_a])}")
        team_info.append(f"**Team B ({len(team_b)} members):** {', '.join([m.mention for m in team_b])}")
        
        # Send debug info as ephemeral message
        debug_message = "**🔍 Priority Debug Info:**\n" + "\n".join(debug_info) + "\n" + "\n".join(team_info)
        await interaction.followup.send(debug_message, ephemeral=True)
        


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
        
        # Handle event organiser lookup
        event_organiser_text = ""
        event_organiser_mention = ""
        if event_organiser:
            event_organiser = event_organiser.strip()
            if event_organiser.isdigit() and len(event_organiser) == 3:
                full_callsign = f"QRV{event_organiser}"
                try:
                    pilot_data = await self.bot.pilots_model.get_pilot_by_callsign(full_callsign)
                    if pilot_data and pilot_data.get('discordid'):
                        event_organiser_text = f"\n**👑 Event Organiser:** <@{pilot_data['discordid']}>\n"
                        event_organiser_mention = f"<@{pilot_data['discordid']}>"
                    else:
                        event_organiser_text = f"\n**👑 Event Organiser:** {full_callsign} (Not found in database)\n"
                        event_organiser_mention = full_callsign
                except Exception as e:
                    logger.error(f"Error looking up event organiser: {e}")
                    event_organiser_text = f"\n**👑 Event Organiser:** {full_callsign}\n"
                    event_organiser_mention = full_callsign
            else:
                event_organiser_text = f"\n**👑 Event Organiser:** {event_organiser}\n"
                event_organiser_mention = event_organiser
        
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
        
        try:
            view = GateAssignmentView(scheduled_event.name, sorted_attendees, default_message, flight_number, event_organiser_text, event_organiser_mention, self.priority_service)
            await interaction.followup.send(status_message, view=view, ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to create view or send message: {e}")
            await interaction.followup.send("An error occurred while setting up gate assignment.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(GateAssignment(bot))
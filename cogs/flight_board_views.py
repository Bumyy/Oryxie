import discord
import logging
import traceback
import re
import json
import os
import time
import io
from datetime import datetime, timedelta
from cogs.utils import get_country_flag
from services.flight_service import FlightService

def load_rank_config():
    # Use absolute path relative to this file
    base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rank_config_path = os.path.join(base_path, 'assets', 'rank_config.json')
    try:
        with open(rank_config_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logging.error(f"[DEBUG] Error: {rank_config_path} not found.")
        return {}
    except json.JSONDecodeError:
        logging.error(f"[DEBUG] Error: Could not decode JSON from {rank_config_path}.")
        return {}

def reconstruct_flight_data(message):
    """Reconstruct flight data from the embed message"""
    if not message.embeds:
        return None
        
    embed = message.embeds[0]
    data = {}
    
    # Parse Description for Route: # OTHH 🇶🇦 - EGLL 🇬🇧
    if embed.description:
        # Regex to capture ICAO codes (4 letters) ignoring flags/emojis
        match = re.search(r'#\s+([A-Z]{4}).*?-\s+([A-Z]{4})', embed.description)
        if match:
            data['departure'] = match.group(1)
            data['arrival'] = match.group(2)
        else:
            # Fallback simple split if regex fails
            parts = embed.description.replace('#', '').split('-')
            if len(parts) >= 2:
                data['departure'] = parts[0].strip().split(' ')[0]
                data['arrival'] = parts[1].strip().split(' ')[0]

    # Parse Fields
    for field in embed.fields:
        if field.name == "Flight Number":
            data['flight_num'] = field.value
        elif field.name == "Aircraft":
            # Value format: "Livery Name ICAO" e.g. "Qatar Airways B77W"
            # We assume the last part is the ICAO code
            parts = field.value.split(' ')
            if parts:
                data['aircraft'] = parts[-1]
                data['livery'] = " ".join(parts[:-1])
                data['aircraft_name'] = field.value # Fallback
        elif field.name == "Flight Time":
            # Format HH:MM
            try:
                h, m = map(int, field.value.split(':'))
                data['duration'] = (h * 3600) + (m * 60)
            except:
                data['duration'] = 0
        elif field.name == "ETD":
            data['etd'] = field.value.replace('Z', '')
        elif field.name == "ETA":
            data['eta'] = field.value.replace('Z', '')
        elif field.name == "Status":
            data['status'] = field.value
        elif field.name == "Note":
            data['note'] = field.value

    # Parse Footer for Pilot Name
    if embed.footer and embed.footer.text:
        # Format: Pilot in Command - @Name | ...
        footer_text = embed.footer.text
        name_match = re.search(r'Pilot in Command - @([^|]+)', footer_text)
        if name_match:
            data['pilot_name'] = name_match.group(1).strip()
        else:
            data['pilot_name'] = "Unknown"

    return data

class ChecklistModal(discord.ui.Modal):
    def __init__(self, aircraft_icao: str, flight_data: dict):
        super().__init__(title=f"Generate {aircraft_icao} Checklist")
        self.aircraft_icao = aircraft_icao
        self.flight_data = flight_data
        
        self.load_percentage = discord.ui.TextInput(
            label="Load Percentage (0-100%)",
            placeholder="Enter load percentage (e.g., 85)",
            default="75",
            max_length=3
        )
        self.add_item(self.load_percentage)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            load_pct = int(self.load_percentage.value)
            if not 0 <= load_pct <= 100:
                await interaction.followup.send("❌ Load percentage must be between 0 and 100.", ephemeral=True)
                return
            
            if 'departure' not in self.flight_data or 'arrival' not in self.flight_data:
                await interaction.followup.send("❌ Missing departure or arrival airport data.", ephemeral=True)
                return
            
            try:
                flight_service = FlightService()
                direction = flight_service.get_flight_direction(
                    self.flight_data['departure'], 
                    self.flight_data['arrival']
                )
                print(f"DEBUG: Direction returned = '{direction}' for {self.flight_data['departure']} -> {self.flight_data['arrival']}")
            except ValueError as ve:
                await interaction.followup.send(f"❌ Invalid airport codes: {ve}", ephemeral=True)
                return
            
            if direction not in ['east', 'west']:
                await interaction.followup.send(f"❌ Unable to determine flight direction. Got: {direction}", ephemeral=True)
                return
            
            pdf_file = interaction.client.checklist_pdf_service.generate_checklist_pdf(
                self.aircraft_icao.upper(), load_pct, "checklist", direction
            )
            
            await interaction.followup.send(
                f"✅ **{self.aircraft_icao} Checklist Generated**\n**Route:** {self.flight_data['departure']} → {self.flight_data['arrival']}\n**Load:** {load_pct}%", 
                file=discord.File(pdf_file), 
                ephemeral=True
            )
        except Exception as e:
            logging.error(f"[CHECKLIST] Error generating checklist: {e}")
            logging.error(traceback.format_exc())
            await interaction.followup.send(f"❌ Error generating checklist: {e}", ephemeral=True)

class StatusSelectView(discord.ui.View):
    def __init__(self, flight_data, original_message):
        super().__init__(timeout=300)
        self.flight_data = flight_data
        self.original_message = original_message
        
        select = discord.ui.Select(
            placeholder="Select new status...",
            options=[
                discord.SelectOption(label="Scheduled", value="Scheduled", emoji="📅"),
                discord.SelectOption(label="Boarding", value="Boarding", emoji="🚪"),
                discord.SelectOption(label="Departed", value="Departed", emoji="🛫"),
                discord.SelectOption(label="En Route", value="En Route", emoji="✈️"),
                discord.SelectOption(label="Arrived", value="Arrived", emoji="🛬"),
                discord.SelectOption(label="Delayed", value="Delayed", emoji="⏰"),
                discord.SelectOption(label="Cancelled", value="Cancelled", emoji="❌")
            ]
        )
        select.callback = self.status_selected
        self.add_item(select)
    
    async def status_selected(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        new_status = interaction.data['values'][0]
        self.flight_data['status'] = new_status
        
        embed, _ = await interaction.client.flight_board_service.create_flight_embed(self.flight_data)
        view = FlightBoardView(self.flight_data)
        
        await self.original_message.edit(embed=embed, view=view)
        await interaction.followup.send(f"✅ Status updated to: **{new_status}**", ephemeral=True)

class AircraftSelectView(discord.ui.View):
    def __init__(self, flight_data, aircraft_options, bot, aircraft_db):
        super().__init__(timeout=300)
        self.flight_data = flight_data
        self.bot = bot
        self.aircraft_db = aircraft_db
        
        select = discord.ui.Select(
            placeholder="Choose your aircraft...",
            options=[discord.SelectOption(label=label[:100], value=value) for value, label in aircraft_options]
        )
        select.callback = self.aircraft_selected
        self.add_item(select)
    
    async def aircraft_selected(self, interaction: discord.Interaction):
        await interaction.response.defer()
        selected_value = interaction.data['values'][0]
        
        if '|' in selected_value:
            selected_icao, selected_livery = selected_value.split('|', 1)
        else:
            selected_icao = selected_value
            selected_livery = "Qatar Airways"
        
        aircraft_name = self.aircraft_db.get('infinite_flight', {}).get(selected_icao, selected_icao)
        self.flight_data['aircraft'] = selected_icao
        self.flight_data['aircraft_name'] = aircraft_name
        self.flight_data['livery'] = selected_livery
        
        embed, thumbnail_file = await self.bot.flight_board_service.create_flight_embed(self.flight_data)
        view = FlightBoardView(self.flight_data)
        
        files = []
        if thumbnail_file:
            files.append(thumbnail_file)
        
        if hasattr(self.bot, 'route_map_service'):
            try:
                map_result = await self.bot.route_map_service.create_route_map(
                    self.flight_data['departure'], self.flight_data['arrival']
                )
                if not isinstance(map_result, str):
                    files.append(discord.File(map_result, filename="route_map.png"))
                    embed.set_image(url="attachment://route_map.png")
            except Exception as e:
                logging.error(f"Map generation failed: {e}")
        
        if files:
            await interaction.edit_original_response(content=None, embed=embed, view=view, attachments=files)
        else:
            await interaction.edit_original_response(content=None, embed=embed, view=view)

class FlightBoardView(discord.ui.View):
    def __init__(self, flight_data):
        super().__init__(timeout=None)
        self.flight_data = flight_data
        
        expiry = int(datetime.now().timestamp() + (48 * 3600))
        uid = flight_data.get('pilot_id', 0)
        
        # Correctly assign custom_ids to the button items in the view
        for item in self.children:
            label = getattr(item, 'label', '')
            if label == "Edit":
                item.custom_id = f"fb:edit:{uid}:{expiry}"
            elif label == "SimBrief":
                item.custom_id = f"fb:simbrief:{uid}:{expiry}"
            elif label == "Checklist":
                item.custom_id = f"fb:checklist:{uid}:{expiry}"
            elif label == "Status":
                item.custom_id = f"fb:status:{uid}:{expiry}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != str(self.flight_data.get('pilot_id')):
            await interaction.response.send_message("❌ You are not the pilot of this flight.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, emoji="✏️")
    async def edit_flight(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = FlightEditModal(self.flight_data, message=interaction.message)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="SimBrief", style=discord.ButtonStyle.secondary, emoji="📋")
    async def simbrief_gen(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if hasattr(interaction.client, 'simbrief_service') and interaction.client.simbrief_service:
            pilot_data = await interaction.client.pilots_model.get_pilot_by_discord_id(str(interaction.user.id))
            if not pilot_data:
                await interaction.followup.send("❌ Pilot not found in database.", ephemeral=True)
                return
            
            aircraft_icao = self.flight_data.get('aircraft', 'B77W')
            link = interaction.client.simbrief_service.generate_dispatch_link(
                origin=self.flight_data['departure'],
                destination=self.flight_data['arrival'],
                aircraft_type=aircraft_icao,
                callsign=pilot_data['callsign'],
                flight_number=self.flight_data['flight_num']
            )
            await interaction.followup.send(f"🔗 **SimBrief Flight Plan Generator**\n{link}", ephemeral=True)
        else:
            await interaction.followup.send("SimBrief service not available!", ephemeral=True)

    async def _handle_checklist(self, interaction: discord.Interaction):
        aircraft_icao = self.flight_data.get('aircraft', '')
        if not (hasattr(interaction.client, 'checklist_pdf_service') and interaction.client.checklist_pdf_service):
            await interaction.response.send_message("⏳ Checklist service not available!", ephemeral=True)
            return
        
        checklist_service = interaction.client.checklist_pdf_service
        if aircraft_icao.upper() not in checklist_service.aircraft_db:
            available = ", ".join(sorted(checklist_service.aircraft_db.keys()))
            await interaction.response.send_message(
                f"❌ Checklist for **{aircraft_icao}** is not available.\n**Current available checklists:** {available}", 
                ephemeral=True
            )
            return
        
        modal = ChecklistModal(aircraft_icao, self.flight_data)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Checklist", style=discord.ButtonStyle.success, emoji="✅")
    async def checklist_gen(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_checklist(interaction)

    @discord.ui.button(label="Status", style=discord.ButtonStyle.gray, emoji="📊")
    async def status_change(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.message:
            view = StatusSelectView(self.flight_data, interaction.message)
            await interaction.response.send_message("📊 **Update Flight Status:**", view=view, ephemeral=True)
        else:
            await interaction.response.send_message("❌ Unable to update status at this time.", ephemeral=True)

class FlightEditModal(discord.ui.Modal):
    def __init__(self, flight_data, message=None):
        super().__init__(title="Edit Flight Information")
        self.flight_data = flight_data
        self.message = message
        
        self.etd = discord.ui.TextInput(
            label="ETD (HHMM Zulu - Optional)", 
            default=flight_data.get('etd', ''), 
            required=False, 
            max_length=4
        )
        self.note = discord.ui.TextInput(
            label="Note (Optional)", 
            default=flight_data.get('note', ''), 
            required=False, 
            max_length=100
        )
        
        self.add_item(self.etd)
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        self.flight_data.update({
            'etd': self.etd.value,
            'note': self.note.value
        })
        
        if self.etd.value and self.flight_data.get('duration'):
            try:
                etd_time = datetime.strptime(self.etd.value, "%H%M")
                eta_time = etd_time + timedelta(seconds=int(self.flight_data['duration']))
                self.flight_data['eta'] = eta_time.strftime("%H%M")
            except (ValueError, TypeError):
                pass
        
        embed, thumbnail_file = await interaction.client.flight_board_service.create_flight_embed(self.flight_data)
        view = FlightBoardView(self.flight_data)
        
        files_to_upload = []
        if thumbnail_file:
            files_to_upload.append(thumbnail_file)
        
        if hasattr(interaction.client, 'route_map_service'):
            try:
                map_result = await interaction.client.route_map_service.create_route_map(
                    self.flight_data['departure'], self.flight_data['arrival']
                )
                if not isinstance(map_result, str):
                    if isinstance(map_result, bytes):
                        map_result = io.BytesIO(map_result)
                    if hasattr(map_result, 'seek'):
                        map_result.seek(0)
                    files_to_upload.append(discord.File(fp=map_result, filename="route_map.png"))
                    embed.set_image(url="attachment://route_map.png")
            except Exception:
                pass
        
        target_message = self.message or interaction.message
        if target_message:
            try:
                await target_message.edit(content=None, embed=embed, view=view, attachments=files_to_upload)
            except discord.HTTPException as e:
                logging.error(f"Failed to edit message: {e}")
        
        await interaction.followup.send("✅ **Flight Updated!**", ephemeral=True)
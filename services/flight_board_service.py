import discord
import json
import os
import re
import logging
import airportsdata
import io
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from cogs.flight_board_views import FlightBoardView, FlightEditModal

class FlightBoardService:
    def __init__(self, bot):
        self.bot = bot
        self.aircraft_db = self._load_aircraft_data()
        self.airports = airportsdata.load('ICAO')
        self.logos = self._load_logos()
    
    def _load_aircraft_data(self) -> dict:
        aircraft_db_path = os.path.join('assets', 'aircraft_data.json')
        try:
            with open(aircraft_db_path, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logging.error(f"Error loading aircraft data: {e}")
            return {}
    
    def _load_logos(self) -> dict:
        return {
            'amiri': 'assets/Amiri  flight logo.png',
            'executive': 'assets/Qatar_Executive_Logo.png'
        }
    
    def get_airport_name(self, icao_code: str) -> str:
        airport_data = self.airports.get(icao_code.upper())
        if airport_data:
            return airport_data.get('name', icao_code)
        return icao_code
    
    def parse_flight_time_to_seconds(self, flight_time_str: str) -> int:
        try:
            flight_time_str = str(flight_time_str).strip()
            
            if ':' in flight_time_str:
                parts = flight_time_str.split(':')
                hours = int(parts[0])
                minutes = int(parts[1])
                return (hours * 3600) + (minutes * 60)

            hours = 0
            minutes = 0
            if 'h' in flight_time_str:
                parts = flight_time_str.split('h')
                hours = int(parts[0].strip())
                if len(parts) > 1 and 'm' in parts[1]:
                    minutes = int(parts[1].replace('m', '').strip())
            elif 'm' in flight_time_str:
                minutes = int(flight_time_str.replace('m', '').strip())
            return (hours * 3600) + (minutes * 60)
        except:
            return 0
    
    def convert_aircraft_name_to_icao(self, aircraft_full_name: str) -> str:
        if not aircraft_full_name:
            return "XXXX"

        infinite_flight_map = self.aircraft_db.get('infinite_flight', {})
        
        # Direct lookup
        for icao_code, full_name_in_json in infinite_flight_map.items():
            if aircraft_full_name.strip() == full_name_in_json.strip():
                return icao_code
        
        # Partial match
        clean_name = aircraft_full_name.lower().strip()
        clean_name = clean_name.replace('qatar airways ', '').replace('qatar ', '')
        clean_name = clean_name.replace('airbus ', '').replace('boeing ', '')
        
        for icao_code, full_name_in_db in infinite_flight_map.items():
            clean_full_name_in_db = full_name_in_db.lower().replace('airbus ', '').replace('boeing ', '')
            if clean_name == clean_full_name_in_db:
                return icao_code

        # Regex fallback
        match = re.search(r'[A-Z]{1,4}\d{1,4}', aircraft_full_name.upper())
        if match:
            return match.group(0)

        return "XXXX"
    
    def parse_route_string(self, route: str) -> tuple[Optional[str], Optional[str]]:
        """Parse route string like 'OTHH 🇶🇦 to EGLL 🇬🇧' into (dep, arr)"""
        clean = re.sub(r'[^\w\s]', '', route)
        icaos = re.findall(r'\b[A-Z]{4}\b', clean)
        if len(icaos) >= 2:
            return icaos[0], icaos[1]
        return None, None
    
    async def create_flight_embed(self, data: dict) -> tuple[discord.Embed, Optional[discord.File]]:
        """Create flight board embed with graceful field handling. Returns (embed, thumbnail_file)"""
        status_colors = {
            "Scheduled": 0x0099ff, "Boarding": 0xFFD700, "Departed": 0x2ECC71,
            "En Route": 0x1ABC9C, "Arrived": 0x95A5A6, "Delayed": 0xE67E22, 
            "Cancelled": 0xFF0000
        }
        
        embed = discord.Embed(
            title="Flight Schedule BETA",
            color=status_colors.get(data.get('status', 'Scheduled'), 0x0099ff)
        )
        
        # Thumbnail for special flight types
        thumbnail_file = None
        flight_type = data.get('flight_type')
        if flight_type and flight_type in self.logos:
            logo_path = self.logos[flight_type]
            if os.path.exists(logo_path):
                thumbnail_file = discord.File(logo_path, filename=f"{flight_type}_logo.png")
                embed.set_thumbnail(url=f"attachment://{flight_type}_logo.png")
        
        # Required: Route description
        dep = data.get('departure', 'XXXX')
        arr = data.get('arrival', 'XXXX')
        from cogs.utils import get_country_flag
        embed.description = f"## {dep} {get_country_flag(dep)} - {arr} {get_country_flag(arr)}"
        
        # Required fields
        if data.get('flight_num'):
            embed.add_field(name="Flight Number", value=data['flight_num'], inline=True)
        
        # Airport names
        dep_name = self.get_airport_name(dep)
        arr_name = self.get_airport_name(arr)
        embed.add_field(name="Departure Airport", value=dep_name, inline=True)
        embed.add_field(name="Arrival Airport", value=arr_name, inline=True)
        
        if data.get('aircraft'):
            livery = data.get('livery', '').strip()
            aircraft_display = f"{livery} {data['aircraft']}" if livery else data['aircraft']
            embed.add_field(name="Aircraft", value=aircraft_display, inline=True)
        
        # Optional: Duration
        if data.get('duration'):
            seconds = int(data['duration'])
            flight_time = f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}"
            embed.add_field(name="Flight Time", value=flight_time, inline=True)
        
        # Optional: ETD/ETA
        if data.get('etd'):
            embed.add_field(name="ETD", value=f"{data['etd']}Z", inline=True)
            if data.get('eta'):
                embed.add_field(name="ETA", value=f"{data['eta']}Z", inline=True)
        
        # Optional: Status
        if data.get('status'):
            embed.add_field(name="Status", value=data['status'], inline=True)
        
        # Optional: Note
        if data.get('note'):
            embed.add_field(name="Note", value=data['note'], inline=False)
        
        # Footer with pilot rank
        pilot_name = data.get('pilot_name', 'Unknown')
        footer_text = f"Pilot in Command - @{pilot_name}"
        
        if hasattr(self.bot, 'pilots_model'):
            try:
                pilot_data = await self.bot.pilots_model.get_pilot_by_discord_id(str(data.get('pilot_id', '')))
                if pilot_data:
                    pilot_hours = await self.bot.pilots_model.get_pilot_total_hours(
                        pilot_data['id'], pilot_data['callsign']
                    ) if hasattr(self.bot.pilots_model, 'get_pilot_total_hours') else pilot_data.get('hours', 0)
                    
                    try:
                        pilot_hours = float(pilot_hours)
                    except:
                        pilot_hours = 0.0
                    
                    # Get rank
                    rank_config_path = os.path.join('assets', 'rank_config.json')
                    try:
                        with open(rank_config_path, 'r') as f:
                            rank_config = json.load(f)
                            ranks = rank_config.get('ranks', {})
                            sorted_ranks = sorted(ranks.items(), key=lambda x: x[1].get('min_hours', 0))
                            rank_name = next((r for r, d in reversed(sorted_ranks) if pilot_hours >= d.get('min_hours', 0)), None)
                            
                            if rank_name in ['Ruby', 'Sapphire', 'Emerald', 'OneWorld', 'Oryx']:
                                footer_text += f" | Senior Captain | {rank_name} Award Holder"
                            elif rank_name:
                                footer_text += f" | {rank_name}"
                    except:
                        pass
            except:
                pass
        
        embed.set_footer(text=footer_text)
        return embed, thumbnail_file
    
    async def post_flight_board(self, flight_data: dict) -> Optional[discord.Message]:
        """
        Post flight to board with embed, view, and optional map.
        Tries Test channel first, falls back to Main channel.
        
        Required fields: flight_num, departure, arrival, aircraft, pilot_id, pilot_name
        Optional fields: etd, eta, duration, status, note, livery, flight_type
        """
        required = ['flight_num', 'departure', 'arrival', 'aircraft', 'pilot_id', 'pilot_name']
        if not all(flight_data.get(field) for field in required):
            logging.warning(f"Missing required fields for flight board post: {flight_data}")
            return None
        
        # Try Test channel first, then Main channel
        TEST_CHANNEL_ID = 1402291473302421555
        MAIN_CHANNEL_ID = 1094522995680215090
        
        channel = self.bot.get_channel(TEST_CHANNEL_ID)
        if not channel:
            channel = self.bot.get_channel(MAIN_CHANNEL_ID)
        
        if not channel:
            logging.error("Could not find any flight board channel")
            return None
        
        embed, thumbnail_file = await self.create_flight_embed(flight_data)
        view = FlightBoardView(flight_data)
        
        files = []
        if thumbnail_file:
            files.append(thumbnail_file)
        
        if hasattr(self.bot, 'route_map_service'):
            try:
                map_result = await self.bot.route_map_service.create_route_map(
                    flight_data['departure'], flight_data['arrival']
                )
                if not isinstance(map_result, str):
                    if isinstance(map_result, bytes):
                        map_result = io.BytesIO(map_result)
                    
                    if hasattr(map_result, 'seek'):
                        map_result.seek(0)
                    
                    files.append(discord.File(map_result, filename="route_map.png"))
                    embed.set_image(url="attachment://route_map.png")
            except Exception as e:
                logging.error(f"Map generation failed: {e}")
        
        if files:
            return await channel.send(embed=embed, view=view, files=files)
        return await channel.send(embed=embed, view=view)

import csv
import json
from haversine import haversine, Unit
import random
from datetime import datetime, timedelta
from typing import Optional

class FlightData:
    def __init__(self):
        self.airports_db = None
        self._load_airport_data()
        self._load_configuration()

    def _load_airport_data(self):
        """Load airport database from CSV file using built-in csv library"""
        try:
            self.airports_db = {}
            with open("assets/airport_database_processed.csv", 'r', encoding='utf-8') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    icao = row.get('ident') or list(row.values())[0]  # First column as ICAO
                    if icao and len(icao) == 4:  # Only 4-letter ICAO codes
                        self.airports_db[icao.upper()] = row
        except (FileNotFoundError, Exception):
            self.airports_db = None
    
    def _load_configuration(self):
        """Load configuration from JSON files"""
        try:
            with open("assets/aircraft_data.json", 'r') as f:
                self.AIRCRAFT_DATA = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Warning: Could not load aircraft_data.json: {e}")
            self.AIRCRAFT_DATA = {}
            
        try:
            with open("assets/rank_permissions.json", 'r') as f:
                self.RANK_PERMISSIONS = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Warning: Could not load rank_permissions.json: {e}")
            self.RANK_PERMISSIONS = {}
            
        try:
            with open("assets/rank_config.json", 'r') as f:
                self.RANK_CONFIG = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Warning: Could not load rank_config.json: {e}")
            self.RANK_CONFIG = {}
            
        try:
            with open("assets/dignitary_names.json", 'r') as f:
                dignitary_data = json.load(f)
                self.ROYAL_NAMES = dignitary_data["royal_names"]
                self.OFFICIAL_ROLES = dignitary_data["official_roles"]
                self.DIGNITARY_SCENARIOS = dignitary_data.get("dignitary_scenarios", [])
                self.ROYAL_SCENARIOS = dignitary_data.get("royal_scenarios", [])
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Warning: Could not load dignitary_names.json: {e}")
            self.ROYAL_NAMES = []
            self.OFFICIAL_ROLES = []
            self.DIGNITARY_SCENARIOS = []
            self.ROYAL_SCENARIOS = []

    def get_airport_data(self, icao: str):
        """Get airport information by ICAO code"""
        try:
            if self.airports_db is None:
                return None
            return self.airports_db.get(icao.upper())
        except (KeyError, TypeError):
            return None

    def get_random_suitable_airport(self, aircraft_type: str) -> Optional[str]:
        """Select random suitable airport based on aircraft type"""
        try:
            if self.airports_db is None:
                return None
                
            suitable_airports = []
            for icao, data in self.airports_db.items():
                if aircraft_type == 'A319':
                    if data.get('A319_Amiri') == '1':
                        suitable_airports.append(icao)
                elif aircraft_type in ['A346', 'B748']:
                    if data.get('Amiri') == '1':
                        suitable_airports.append(icao)
                else:
                    # Executive aircraft fallback
                    if data.get('type') == 'large_airport':
                        suitable_airports.append(icao)
            
            return random.choice(suitable_airports) if suitable_airports else None
        except Exception:
            return None

    def calculate_distance(self, icao1: str, icao2: str) -> Optional[float]:
        """Calculate distance between two airports in nautical miles"""
        try:
            data1 = self.get_airport_data(icao1)
            data2 = self.get_airport_data(icao2)
            if data1 is None or data2 is None:
                return None
            # Convert string coordinates to float
            coords1 = (float(data1['latitude_deg']), float(data1['longitude_deg']))
            coords2 = (float(data2['latitude_deg']), float(data2['longitude_deg']))
            return haversine(coords1, coords2, unit=Unit.NAUTICAL_MILES)
        except (ValueError, KeyError, TypeError):
            return None

    def get_aircraft_range(self, aircraft_code: str, flight_type: str, passengers: int, cargo_kg: int) -> int:
        """Calculate aircraft range based on payload"""
        aircraft_fleet = self.AIRCRAFT_DATA.get(flight_type)
        if not aircraft_fleet or aircraft_code not in aircraft_fleet:
            return 3000
        
        data = aircraft_fleet[aircraft_code]
        pax_category = 0 if passengers <= data["pax_range"][0] else (1 if passengers <= data["pax_range"][1] else 2)
        cargo_category = 0 if cargo_kg <= data["cargo_kg_range"][0] else (1 if cargo_kg <= data["cargo_kg_range"][1] else 2)
        payload_category = max(pax_category, cargo_category)
        return data["range_nm"][2 - payload_category]

    def needs_fuel_stop(self, distance_nm: float, aircraft_code: str, flight_type: str, passengers: int, cargo_kg: int) -> bool:
        """Check if fuel stop is required"""
        if distance_nm is None:
            return False
        aircraft_range = self.get_aircraft_range(aircraft_code, flight_type, passengers, cargo_kg)
        return distance_nm > aircraft_range

    def generate_flight_number(self, flight_type: str) -> str:
        """Generate unique flight number"""
        prefix = "Q4" if flight_type == "amiri" else "QE"
        return f"{prefix}{random.randint(1000, 9999)}"

    def get_dates(self) -> tuple:
        """Generate current date and deadline (4 days later)"""
        current_date = datetime.now()
        deadline_date = current_date + timedelta(days=4)
        return current_date.strftime("%d %B %Y"), deadline_date.strftime("%d %B %Y")

    def get_rank_from_hours(self, total_hours: float) -> str:
        """Get pilot rank based on flight hours"""
        if not self.RANK_CONFIG or 'ranks' not in self.RANK_CONFIG:
            return "Cadet"
        
        # Convert seconds to hours if needed
        if total_hours > 10000:  # Assume it's in seconds if > 10000
            total_hours = total_hours / 3600
        
        # Find highest rank pilot qualifies for
        qualified_rank = "Cadet"
        for rank_name, rank_data in self.RANK_CONFIG['ranks'].items():
            if total_hours >= rank_data['min_hours']:
                qualified_rank = rank_name
        
        return qualified_rank

    async def get_pilot_rank_and_aircraft(self, discord_id: str, pilots_model, pireps_model) -> tuple:
        """Get pilot's rank and available aircraft based on flight hours"""
        try:
            # Find pilot by Discord ID
            pilot_data = await pilots_model.get_pilot_by_discord_id(discord_id)
            if not pilot_data:
                return None, None, "❌ Pilot not found in database. Please contact staff."
            
            # Get pilot's total flight hours (transfer + PIREP hours)
            total_hours = await pilots_model.get_pilot_total_hours(pilot_data['id'], pilot_data['callsign'])
            
            # Determine rank based on hours
            rank = self.get_rank_from_hours(total_hours)
            
            return rank, total_hours, None
        except Exception as e:
            return None, None, f"❌ Database error: {str(e)}"

    def get_available_aircraft_by_rank(self, rank: str, flight_type: str) -> list:
        """Get available aircraft for a rank and flight type"""
        if not rank or rank not in self.RANK_PERMISSIONS:
            return []
        permissions = self.RANK_PERMISSIONS[rank]
        return permissions.get(f"{flight_type}_aircraft", [])

    async def check_pilot_permissions(self, discord_id: str, flight_type: str, aircraft_code: str, pilots_model, pireps_model) -> Optional[str]:
        """Check pilot permissions based on flight hours instead of roles"""
        rank, hours, error = await self.get_pilot_rank_and_aircraft(discord_id, pilots_model, pireps_model)
        
        if error:
            return error
        
        available_aircraft = self.get_available_aircraft_by_rank(rank, flight_type)
        if not available_aircraft:
            return f"❌ Your rank ({rank}) does not permit you to request {flight_type} flights. You need more flight hours."
        
        if aircraft_code and aircraft_code not in available_aircraft:
            return f"❌ Your rank ({rank} - {hours:.1f}h) cannot claim the **{aircraft_code}**. You need more flight hours."
        
        return None

    def has_staff_permissions(self, user_roles) -> bool:
        """Check if user has staff privileges"""
        role_names = [role.name.lower() for role in user_roles]
        return any(role in role_names for role in ["dispatcher", "administrator", "admin"])

    def select_dignitary(self) -> str:
        """Select dignitary for Amiri flights"""
        if random.random() < 0.6:
            return random.choice(self.OFFICIAL_ROLES)
        else:
            return random.choice(self.ROYAL_NAMES)
    
    def get_scenario_options(self, dignitary_name: str) -> list:
        """Get appropriate scenario options based on dignitary type"""
        if dignitary_name in self.ROYAL_NAMES:
            return self.ROYAL_SCENARIOS
        else:
            return self.DIGNITARY_SCENARIOS

    def get_aircraft_code_from_name(self, aircraft_name: str) -> Optional[str]:
        """Extract aircraft code from display name"""
        search_map = {
            'B748': ('B748', '747-8'),
            'A346': ('A346', 'A340'),
            'A319': ('A319',),
            'B737': ('B737',),
            'A318': ('A318',),
            'CL35': ('CL35', 'Challenger'),
        }
        for code, terms in search_map.items():
            if any(term in aircraft_name for term in terms):
                return code
        return None
    
    def is_royal_dignitary(self, dignitary_name: str) -> bool:
        """Check if dignitary is from royal family"""
        return dignitary_name in self.ROYAL_NAMES


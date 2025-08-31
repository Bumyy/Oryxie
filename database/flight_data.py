import pandas as pd
from haversine import haversine, Unit
import random
import re
from datetime import datetime, timedelta
from typing import Optional

class FlightData:
    def __init__(self):
        self.airports_db = None
        self._load_airport_data()
        
        # Aircraft specifications
        self.AIRCRAFT_DATA = {
            "amiri": {
                "A319": {"name": "Airbus A319 (ACJ)", "pax_range": [16, 32, 60], "cargo_kg_range": [2000, 3000, 4500], "range_nm": [1500, 2000, 3700]},
                "A346": {"name": "Airbus A340-600", "pax_range": [100, 140, 370], "cargo_kg_range": [10000, 20000, 35000], "range_nm": [2500, 6000, 7800]},
                "B748": {"name": "Boeing 747-8 (BBJ)", "pax_range": [100, 200, 400], "cargo_kg_range": [15000, 30000, 45000], "range_nm": [3000, 6500, 7370]}
            },
            "executive": {
                "A318": {"name": "Airbus A318 ACJ", "pax_range": [8, 20, 38], "cargo_kg_range": [2000, 3000, 3500], "range_nm": [800, 2800, 3100]},
                "B737": {"name": "Boeing 737 BBJ", "pax_range": [16, 40, 150], "cargo_kg_range": [1000, 5000, 15000], "range_nm": [1200, 2200, 3000]},
                "C350": {"name": "Challenger 350", "pax_range": [6, 8, 10], "cargo_kg_range": [200, 500, 1000], "range_nm": [1800, 2100, 3100]}
            }
        }
        
        # Dignitary names for Amiri flights
        self.ROYAL_NAMES = [
            "Sheikh Faisal bin Jassim Al Thani", "Sheikh Nasser bin Khalid Al Thani", "Sheikh Salman bin Hamad Al Thani",
            "Sheikh Jassim bin Mohammed Al Thani", "Sheikh Abdullah bin Suhaim Al Thani", "Sheikh Turki bin Abdulaziz Al Thani",
            "Sheikh Khalid bin Fahad Al Thani", "Sheikh Mansour bin Rashid Al Thani"
        ]
        
        self.OFFICIAL_ROLES = [
            "Prime Minister", "Foreign Minister", "Defense Minister", "Energy Minister", "Finance Minister",
            "Senior Cabinet Minister", "Parliamentary Delegation Head", "National Olympic Committee Head",
            "Trade Delegation Head", "State Protocol Team Leader", "Senior Military Delegation", "Medical Emergency Response Team"
        ]
        
        # Rank permissions
        self.RANK_PERMISSIONS = {
            "Ruby": {"amiri_aircraft": ["A319"], "executive_aircraft": []},
            "Sapphire": {"amiri_aircraft": ["A319", "A346", "B748"], "executive_aircraft": []},
            "Emerald": {"amiri_aircraft": ["A319", "A346", "B748"], "executive_aircraft": []},
            "OWD": {"amiri_aircraft": ["A319", "A346", "B748"], "executive_aircraft": []},
            "Oryx": {"amiri_aircraft": ["A319", "A346", "B748"], "executive_aircraft": ["A318", "B737", "C350"]}
        }

    def _load_airport_data(self):
        """Load airport database from CSV file"""
        try:
            df = pd.read_csv("Assests/airport_database_processed.csv")
            df.rename(columns={df.columns[0]: 'ident'}, inplace=True)
            self.airports_db = df.set_index('ident')
            self.airports_db = self.airports_db[self.airports_db.index.str.len() == 4]
            
            pass
        except (FileNotFoundError, Exception):
            self.airports_db = None

    def get_airport_data(self, icao: str):
        """Get airport information by ICAO code"""
        try:
            if self.airports_db is None:
                return None
            return self.airports_db.loc[icao.upper()]
        except (KeyError, TypeError):
            return None

    def get_random_suitable_airport(self, aircraft_type: str) -> Optional[str]:
        """Select random suitable airport based on aircraft type"""
        try:
            if self.airports_db is None:
                return None
                
            if aircraft_type == 'A319':
                suitable_airports = self.airports_db[self.airports_db['A319_Amiri'] == 1]
            elif aircraft_type in ['A346', 'B748']:
                suitable_airports = self.airports_db[self.airports_db['Amiri'] == 1]
            else:
                # Executive aircraft fallback
                suitable_airports = self.airports_db[self.airports_db['type'] == 'large_airport']
            
            if not suitable_airports.empty:
                return suitable_airports.sample().index[0]
            return None
        except Exception:
            return None

    def calculate_distance(self, icao1: str, icao2: str) -> Optional[float]:
        """Calculate distance between two airports in nautical miles"""
        try:
            data1 = self.get_airport_data(icao1)
            data2 = self.get_airport_data(icao2)
            if data1 is None or data2 is None:
                return None
            coords1 = (data1['latitude_deg'], data1['longitude_deg'])
            coords2 = (data2['latitude_deg'], data2['longitude_deg'])
            return haversine(coords1, coords2, unit=Unit.NAUTICAL_MILES)
        except Exception:
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

    def get_user_rank(self, user_roles) -> Optional[str]:
        """Get user's highest rank from Discord roles"""
        role_names = [role.name for role in user_roles]
        for rank in ["Oryx", "OWD", "Emerald", "Sapphire", "Ruby"]:
            if rank in role_names:
                return rank
        return None

    def get_available_aircraft(self, user_roles, flight_type: str) -> list:
        """Get available aircraft for user's rank"""
        rank = self.get_user_rank(user_roles)
        if not rank or rank not in self.RANK_PERMISSIONS:
            return []
        permissions = self.RANK_PERMISSIONS[rank]
        return permissions["amiri_aircraft"] if flight_type == "amiri" else permissions["executive_aircraft"]

    def check_permissions(self, user_roles, flight_type: str, aircraft_code: str = None) -> Optional[str]:
        """Check user permissions for flight requests and claims"""
        rank = self.get_user_rank(user_roles)
        if not rank:
            return "❌ You need a rank role to perform this action."

        available_aircraft = self.get_available_aircraft(user_roles, flight_type)
        if not available_aircraft:
            return f"❌ Your rank ({rank}) does not permit you to request {flight_type} flights."

        if aircraft_code and aircraft_code not in available_aircraft:
            return f"❌ Your rank ({rank}) cannot claim the **{aircraft_code}**."
            
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

    def get_aircraft_code_from_name(self, aircraft_name: str) -> Optional[str]:
        """Extract aircraft code from display name"""
        search_map = {
            'B748': ('B748', '747-8'),
            'A346': ('A346', 'A340'),
            'A319': ('A319',),
            'B737': ('B737',),
            'A318': ('A318',),
            'C350': ('C350', 'Challenger'),
        }
        for code, terms in search_map.items():
            if any(term in aircraft_name for term in terms):
                return code
        return None

    def _clean_text(self, text) -> str:
        """Clean text for PDF generation"""
        return re.sub(r'[^\x00-\x7F]+', '', str(text))

    def generate_professional_pdf(self, flight_data: dict, flight_type: str, pilot_user) -> Optional[bytes]:
        """Generate professional PDF flight document"""
        try:
            from fpdf import FPDF
            import os
            
            pdf = FPDF()
            pdf.add_page()
            
            # Add logos
            logo_paths = {
                "amiri": "Assests/Amiri  flight logo.png",
                "executive": "Assests/Qatar_Executive_Logo.png"
            }
            qatari_virtual_logo = "Assests/Qatar_Virtual_logo.PNG"
            
            # Left logo (flight type)
            logo_path = logo_paths.get(flight_type)
            if logo_path and os.path.exists(logo_path):
                try:
                    pdf.image(logo_path, x=10, y=8, w=25)
                except Exception:
                    pass
            
            # Right logo (Qatari Virtual) - Made much bigger
            if os.path.exists(qatari_virtual_logo):
                try:
                    pdf.image(qatari_virtual_logo, x=155, y=8, w=45)
                except Exception:
                    pass
            
            pdf.ln(25)
            
            # Header
            pdf.set_font('Arial', 'B', 18)
            pdf.cell(0, 15, f'QATARI VIRTUAL {"AMIRI" if flight_type == "amiri" else "EXECUTIVE"}', 0, 1, 'C')
            pdf.set_font('Arial', 'B', 14)
            pdf.cell(0, 10, 'OPERATIONAL DOCUMENT', 0, 1, 'C')
            pdf.ln(10)
            
            # Flight Information
            pdf.set_font('Arial', 'B', 12)
            pdf.cell(0, 8, 'FLIGHT INFORMATION', 0, 1)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)
            
            pdf.set_font('Arial', '', 10)
            flight_info = [
                ('Flight Number:', self._clean_text(flight_data['flight_number'])),
                ('Aircraft Type:', self._clean_text(flight_data['aircraft_name'])),
                ('Route:', self._clean_text(flight_data['route'])),
                ('Passengers:', f"{flight_data['passengers']} PAX"),
                ('Cargo Weight:', f"{flight_data['cargo']} KG"),
                ('Fuel Stop Required:', 'YES' if flight_data['fuel_stop_required'] else 'NO'),
                ('Issue Date:', self._clean_text(flight_data['current_date'])),
                ('Deadline:', self._clean_text(flight_data['deadline']))
            ]
            
            for label, value in flight_info:
                pdf.cell(60, 6, label, 0, 0)
                pdf.cell(0, 6, str(value), 0, 1)
            
            # Fuel Stop Information (if required)
            if flight_data.get('fuel_stop_required', False):
                pdf.ln(8)
                pdf.set_font('Arial', 'B', 12)
                pdf.cell(0, 8, 'FUEL STOP INFORMATION', 0, 1)
                pdf.line(10, pdf.get_y(), 200, pdf.get_y())
                pdf.ln(5)
                
                pdf.set_font('Arial', '', 10)
                fuel_stop_text = "Fuel stop required. Plan fuel stops considering NOTAMs and weather conditions."
                pdf.multi_cell(0, 5, fuel_stop_text)
            
            # Mission/Flight Briefing
            pdf.ln(8)
            pdf.set_font('Arial', 'B', 12)
            briefing_title = 'MISSION BRIEFING' if flight_type == 'amiri' else 'FLIGHT BRIEFING'
            pdf.cell(0, 8, briefing_title, 0, 1)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)
            
            if flight_type == "amiri":
                briefing_data = [
                    (f"Dossier: {self._clean_text(flight_data.get('dignitary', 'N/A'))}", 
                     self._clean_text(flight_data.get('dignitary_intro', 'No introduction available.'))),
                    ('Mission Objectives:', 
                     self._clean_text(flight_data.get('mission_briefing', 'No briefing available.'))),
                    ('Urgency:', 
                     self._clean_text(flight_data.get('deadline_rationale', 'Standard operational timeline.')))
                ]
            else:
                briefing_data = [
                    (f"Client Profile: {self._clean_text(flight_data.get('client', 'N/A'))}", 
                     self._clean_text(flight_data.get('client_intro', 'No client introduction available.'))),
                    ('Flight Purpose:', 
                     self._clean_text(flight_data.get('mission_briefing', 'No briefing available.'))),
                    ('Urgency:', 
                     self._clean_text(flight_data.get('deadline_rationale', 'Standard operational timeline.')))
                ]
            
            for title, content in briefing_data:
                pdf.set_font('Arial', 'B', 10)
                pdf.cell(0, 6, title, 0, 1)
                pdf.set_font('Arial', '', 10)
                pdf.multi_cell(0, 5, content)
                pdf.ln(3)

            # Crew Assignment
            pdf.ln(8)
            pdf.set_font('Arial', 'B', 12)
            pdf.cell(0, 8, 'CREW ASSIGNMENT', 0, 1)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)
            
            pdf.set_font('Arial', '', 10)
            crew_info = [
                ('Pilot in Command:', self._clean_text(pilot_user.display_name)),
                ('Claim Time (UTC):', datetime.now().strftime('%d %B %Y at %H:%M UTC'))
            ]
            
            for label, value in crew_info:
                pdf.cell(60, 6, label, 0, 0)
                pdf.cell(0, 6, str(value), 0, 1)
            
            # Footer with better styling
            pdf.ln(15)
            
            # Add a line above footer
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)
            
            # Main footer text
            pdf.set_font('Arial', 'I', 11)
            pdf.set_text_color(0, 102, 153)  # Blue color
            pdf.cell(0, 6, 'Generated by Qatari Virtual - Flight Operations Department', 0, 1, 'C')
            
            pdf.ln(3)
            
            # Disclaimer box
            pdf.set_fill_color(240, 240, 240)  # Light gray background
            pdf.set_text_color(0, 0, 0)  # Black text
            pdf.set_font('Arial', 'B', 9)
            
            # Calculate box position
            box_width = 180
            box_x = (210 - box_width) / 2
            
            pdf.set_x(box_x)
            pdf.cell(box_width, 8, 'HYPOTHETICAL FLIGHT - FOR PERSONAL FLIGHT SIMULATOR USE ONLY', 1, 1, 'C', True)
            
            pdf.set_font('Arial', '', 8)
            pdf.set_text_color(80, 80, 80)  # Dark gray
            pdf.cell(0, 4, 'This document is not affiliated with any real Qatar Executive or government operations', 0, 1, 'C')
            
            # Reset text color
            pdf.set_text_color(0, 0, 0)
            
            return pdf.output()
        except (ImportError, Exception):
            return None
import random
from datetime import datetime, timedelta
from models.flight_details import FlightDetails

class FlightService:
    def __init__(self, flight_data):
        self.flight_data = flight_data
    
    def get_dates_with_fuel_logic(self, flight_type: str, fuel_stop_required: bool) -> tuple:
        """Generate current date and deadline based on fuel stop requirements"""
        current_date = datetime.now()
        
        # Amiri flights with fuel stop get 6 days, otherwise 4 days
        if flight_type == "amiri" and fuel_stop_required:
            deadline_days = 6
        else:
            deadline_days = 4
            
        deadline_date = current_date + timedelta(days=deadline_days)
        return current_date.strftime("%d %B %Y"), deadline_date.strftime("%d %B %Y")
    
    async def generate_flight(self, aircraft: str, flight_type: str, departure: str = None, destination: str = None, passengers: int = None, cargo: int = None):
        """Generate flight data without AI scenario"""
        try:
            if departure is None:
                departure = "OTHH"

            aircraft_data = self.flight_data.AIRCRAFT_DATA[flight_type][aircraft]
            
            if passengers is None:
                passengers = random.randint(aircraft_data['pax_range'][0], aircraft_data['pax_range'][1])
            if cargo is None:
                cargo = random.randint(aircraft_data['cargo_kg_range'][0], aircraft_data['cargo_kg_range'][1])
            
            dep_data = self.flight_data.get_airport_data(departure)
            if dep_data is None: return None
            
            if destination is None:
                destination = self.flight_data.get_random_suitable_airport(aircraft)
                if not destination: return None

            dest_data = self.flight_data.get_airport_data(destination)
            if dest_data is None: return None

            # Calculate distance and fuel stop requirement
            distance = self.flight_data.calculate_distance(departure, destination)
            fuel_stop_required = self.flight_data.needs_fuel_stop(distance, aircraft, flight_type, passengers, cargo)
            
            # Generate dates with fuel stop logic
            current_date, deadline = self.get_dates_with_fuel_logic(flight_type, fuel_stop_required)
            
            # No AI scenario generation here - only basic dignitary selection
            scenario_data = {'dignitary': self.flight_data.select_dignitary() if flight_type == 'amiri' else 'Business Client'}
            
            # Import get_country_flag function
            try:
                from cogs.utils import get_country_flag
            except ImportError:
                def get_country_flag(icao):
                    return "🏳️"  # Fallback flag
            
            route_format = f"{departure} {get_country_flag(departure)} to {destination} {get_country_flag(destination)} to {departure} {get_country_flag(departure)}"

            return FlightDetails(
                flight_number=self.flight_data.generate_flight_number(flight_type),
                aircraft_name=aircraft_data['name'],
                passengers=passengers,
                cargo=cargo,
                route=route_format,
                fuel_stop_required=fuel_stop_required,
                current_date=current_date,
                deadline=deadline,
                **scenario_data
            )
        except Exception:
            return None
    
    async def generate_custom_flight(self, aircraft: str, flight_type: str, departure: str, arrival: str):
        """Generate custom executive flight"""
        try:
            dep_data = self.flight_data.get_airport_data(departure)
            dest_data = self.flight_data.get_airport_data(arrival)
            if dep_data is None or dest_data is None: return None
            
            aircraft_data = self.flight_data.AIRCRAFT_DATA[flight_type][aircraft]
            # Import get_country_flag function
            try:
                from cogs.utils import get_country_flag
            except ImportError:
                def get_country_flag(icao):
                    return "🏳️"  # Fallback flag
            
            route_format = f"{departure} {get_country_flag(departure)} - {arrival} {get_country_flag(arrival)}"
            
            distance = self.flight_data.calculate_distance(departure, arrival)
            passengers = random.randint(aircraft_data['pax_range'][0], aircraft_data['pax_range'][1])
            cargo = random.randint(aircraft_data['cargo_kg_range'][0], aircraft_data['cargo_kg_range'][1])
            fuel_stop_required = self.flight_data.needs_fuel_stop(distance, aircraft, flight_type, passengers, cargo)
            
            # Executive flights always get 4 days (no special fuel stop logic)
            current_date, deadline = self.get_dates_with_fuel_logic(flight_type, fuel_stop_required)
            
            # No AI scenario generation here - only basic client info
            scenario_data = {'client': 'Business Client'}

            return FlightDetails(
                flight_number=self.flight_data.generate_flight_number(flight_type),
                aircraft_name=aircraft_data['name'],
                passengers=passengers,
                cargo=cargo,
                route=route_format,
                fuel_stop_required=fuel_stop_required,
                current_date=current_date,
                deadline=deadline,
                **scenario_data
            )
        except Exception:
            return None
import logging
from datetime import datetime
from typing import List, Dict

class PirepFilingService:
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('oryxie.pirep_filing_service')

    async def get_pilot_and_aircraft(self, discord_id: int, aircraft_icao: str = None, cc_aircraft_id: int = None, aircraft_name: str = None, aircraft_livery: str = None):
        """
        Retrieves pilot DB ID and Aircraft DB ID.
        
        Parameters:
        - discord_id: Discord user ID
        - aircraft_icao: ICAO code (e.g., 'B77W')
        - cc_aircraft_id: Crew Center aircraft ID (optional, takes precedence)
        - aircraft_name: Full aircraft name from aircraft_data.json (e.g., 'Boeing 777-300ER')
        - aircraft_livery: Livery name from aircraft_data.json (e.g., 'Qatar Airways')
        """
        # 1. Get Pilot Data
        pilot_data = await self.bot.pilots_model.get_pilot_by_discord_id(str(discord_id))
        if not pilot_data:
            return None, None, "Pilot not found in database. Please link your account."
        
        # 2. Get Aircraft Data
        aircraft_data = None
        
        # Priority 1: Use CC aircraft ID if provided
        # Priority 1: Use Crew Center aircraft ID (from DB) if provided
        if cc_aircraft_id:
            aircraft_data = await self.bot.aircraft_model.get_aircraft_by_id(cc_aircraft_id)
            if aircraft_data:
                return pilot_data, aircraft_data, None # Found aircraft by CC ID, no further lookup needed
            else:
                self.logger.warning(f"Provided CC Aircraft ID {cc_aircraft_id} not found in VA database. Falling back to other methods.")

        # Priority 2: Use name and livery if provided (most accurate)
        if aircraft_name and aircraft_livery:
            aircraft_data = await self.bot.aircraft_model.get_aircraft_by_name_and_livery(aircraft_name, aircraft_livery)
            if aircraft_data:
                return pilot_data, aircraft_data, None

        # Priority 3: Use aircraft name alone if livery not available
        if aircraft_name:
            aircraft_data = await self.bot.aircraft_model.get_aircraft_by_name(aircraft_name)
            if aircraft_data:
                return pilot_data, aircraft_data, None

        # Priority 4: Use ICAO code as fallback
        # Priority 2: Use ICAO code (from aircraft_data.json) if provided
        # We trust that this 'aircraft_icao' is already a valid ICAO from the flight_board_service
        if aircraft_icao:
            aircraft_data = await self.bot.aircraft_model.get_aircraft_by_icao(aircraft_icao)
            
            if not aircraft_data:
                # Fallback: Try to find by name if ICAO lookup fails
                # This helps if aircraft_icao is actually a name like "Boeing 777-300ER"
                all_aircraft = await self.bot.aircraft_model.get_all_aircraft()
                for ac in all_aircraft:
                    # Check if name matches or if ICAO is contained in the input string
                    if ac['name'].lower() == aircraft_icao.lower() or ac['icao'] == aircraft_icao:
                        aircraft_data = ac
                        break
        
        if not aircraft_data:
             return pilot_data, None, f"Aircraft not found in VA database."

        return pilot_data, aircraft_data, None

    async def detect_flight_duration(self, if_user_id: str, dep_icao: str, arr_icao: str):
        """
        Attempts to find a matching flight in IF API and return duration string (HH:MM).
        """
        if not if_user_id:
            return None

        try:
            # Fetch recent flights from Infinite Flight API
            response = await self.bot.if_api_manager.get_user_flights(if_user_id)
            
            flights = []
            if isinstance(response, dict):
                 flights = response.get('data', [])
            
            for flight in flights:
                # Check route match
                f_dep = flight.get('departure', {}).get('code', '').upper()
                f_arr = flight.get('arrival', {}).get('code', '').upper()
                
                if f_dep == dep_icao and f_arr == arr_icao:
                    start_time_str = flight.get('startTime')
                    end_time_str = flight.get('endTime')
                    
                    if start_time_str and end_time_str:
                        try:
                            # Parse ISO format (handle Z for UTC)
                            start_dt = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                            end_dt = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
                            duration_seconds = (end_dt - start_dt).total_seconds()
                            
                            hours = int(duration_seconds // 3600)
                            minutes = int((duration_seconds % 3600) // 60)
                            return f"{hours:02d}:{minutes:02d}"
                        except Exception as e:
                            self.logger.error(f"Error parsing dates for PIREP detection: {e}")
                            continue
            
            return None

        except Exception as e:
            self.logger.error(f"Error fetching flights from IF API: {e}")
            return None

    async def submit_pirep(self, pilot_id, flight_num, dep, arr, aircraft_id, duration_str, multiplier_id=None):
        """
        Submits the PIREP to Crew Center with default date (today) and fuel (0).
        """
        date_str = datetime.now().strftime("%Y-%m-%d")
        fuel_used = 0
        
        # Get multiplier name if ID provided
        multiplier_name = None
        if multiplier_id and hasattr(self.bot, 'multiplier_model'):
            try:
                mult_data = await self.bot.multiplier_model.get_multiplier_by_id(multiplier_id)
                if mult_data:
                    # API expects the 'code' field value (e.g., 120000), NOT name or multiplier
                    code_val = mult_data.get('code')
                    if code_val:
                        multiplier_name = str(code_val)
                    else:
                        multiplier_name = mult_data.get('name')
                else:
                    self.logger.warning(f"Multiplier ID {multiplier_id} not found in database.")
            except Exception as e:
                self.logger.error(f"Error fetching multiplier name: {e}")
                pass
        
        response = await self.bot.cc_api_manager.submit_pirep(
            pilot_id=pilot_id,
            flight_num=flight_num,
            departure=dep,
            arrival=arr,
            flight_time=duration_str,
            date=date_str,
            aircraft_id=aircraft_id,
            fuel_used=fuel_used,
            multiplier=multiplier_name
        )
        return response
    
    async def get_pilot_multipliers(self, pilot_id: int) -> List[Dict]:
        """
        Get available multipliers for a pilot based on their rank.
        """
        if not hasattr(self.bot, 'multiplier_model'):
            return []
        
        try:
            # Get pilot's actual rank object from DB
            rank_data = await self.bot.rank_model.get_pilot_rank(pilot_id)
            if not rank_data:
                return await self.bot.multiplier_model.get_all_multipliers()

            # Filter multipliers by rank requirement
            return await self.bot.multiplier_model.get_multipliers_for_rank(rank_data['id'])
        except Exception as e:
            self.logger.error(f"Error getting multipliers: {e}")
            return []
    
    async def prepare_pirep_data(self, discord_id: int, flight_data: dict) -> Dict:
        """
        Prepares all PIREP data for review.
        Returns a dictionary with all details needed for the review embed.
        
        Returns:
            {
                'success': True/False,
                'pilot_data': {...},
                'aircraft_data': {...},
                'pirep_data': {...},  # flight_num, dep, arr, duration, etc.
                'error': 'error message if failed'
            }
        """
        result = {
            'success': False,
            'pilot_data': None,
            'aircraft_data': None,
            'pirep_data': {},
            'error': None
        }
        
        # Get aircraft info from flight_data
        aircraft_icao = flight_data.get('aircraft', 'XXXX')
        aircraft_name = flight_data.get('aircraft_name')
        aircraft_livery = flight_data.get('livery')
        cc_aircraft_id = flight_data.get('aircraft_id') or flight_data.get('cc_aircraft_id')
        
        # 1. Get Pilot and Aircraft
        pilot_data, aircraft_data, error = await self.get_pilot_and_aircraft(
            discord_id=discord_id,
            aircraft_icao=aircraft_icao,
            cc_aircraft_id=cc_aircraft_id,
            aircraft_name=aircraft_name,
            aircraft_livery=aircraft_livery
        )
        
        if error:
            result['error'] = error
            return result
        
        # 2. Try to detect flight duration
        duration_str = None
        if pilot_data.get('ifuserid'):
            duration_str = await self.detect_flight_duration(
                pilot_data.get('ifuserid'),
                flight_data.get('departure'),
                flight_data.get('arrival')
            )
        
        # Build pirep data
        result['success'] = True
        result['pilot_data'] = pilot_data
        result['aircraft_data'] = aircraft_data
        result['pirep_data'] = {
            'flight_num': flight_data.get('flight_num') or flight_data.get('flight_number'),
            'departure': flight_data.get('departure'),
            'arrival': flight_data.get('arrival'),
            'duration': duration_str,  # Can be None if not detected
            'duration_seconds': flight_data.get('duration', 0),
            'aircraft_icao': aircraft_icao,
            'aircraft_name': aircraft_data.get('name') if aircraft_data else aircraft_name,
            'livery': aircraft_livery,
            'pilot_name': pilot_data.get('name') if pilot_data else None,
            'callsign': pilot_data.get('callsign') if pilot_data else None
        }
        
        return result

    async def submit_fleet_pirep(
        self, 
        pilot1_discord_id: int, 
        pilot2_discord_id: int,
        frame_data: dict,
        dep_icao: str, 
        arr_icao: str, 
        total_duration_hours: int,
        total_duration_minutes: int,
        pilot1_name: str = None,
        pilot2_name: str = None
    ) -> Dict:
        """
        Submits dual-pilot PIREP with 50/50 hour split.
        
        Args:
            pilot1_discord_id: Discord ID of Pilot 1
            pilot2_discord_id: Discord ID of Pilot 2 (can be None for single pilot)
            frame_data: Frame info from fleet_frames.json (contains icao and livery)
            dep_icao: Departure airport ICAO
            arr_icao: Arrival airport ICAO
            total_duration_hours: Total flight hours
            total_duration_minutes: Total flight minutes
            pilot1_name: Discord display name of Pilot 1 (fallback if DB name is None)
            pilot2_name: Discord display name of Pilot 2 (fallback if DB name is None)
        
        Returns:
            Dict with success status and results
        """
        result = {
            'success': False,
            'pilot1_result': None,
            'pilot2_result': None,
            'error': None
        }
        
        # Hardcoded values
        aircraft_id = 11
        multiplier = "150000"
        
        # Calculate duration string
        total_minutes = (total_duration_hours * 60) + total_duration_minutes
        
        # Determine split
        if pilot2_discord_id:
            # 50/50 split
            split_minutes = total_minutes // 2
            pilot1_hours = split_minutes // 60
            pilot1_minutes = split_minutes % 60
            pilot2_hours = split_minutes // 60
            pilot2_minutes = split_minutes % 60
            
            # Handle odd minute remainder - add to pilot 1
            if total_minutes % 2 == 1:
                pilot1_minutes += 1
                if pilot1_minutes >= 60:
                    pilot1_hours += 1
                    pilot1_minutes -= 60
        else:
            # Full duration for pilot 1 only
            pilot1_hours = total_duration_hours
            pilot1_minutes = total_duration_minutes
            pilot2_hours = 0
            pilot2_minutes = 0
        
        # Get frame name (becomes flight number)
        frame_name = frame_data.get('name')
        
        # Use hardcoded aircraft_id = 11 for all fleet PIREPs
        # Use hardcoded multiplier = 150000 for all fleet PIREPs
        
        # Get Pilot 1 data
        pilot1_data = await self.bot.pilots_model.get_pilot_by_discord_id(str(pilot1_discord_id))
        if not pilot1_data:
            result['error'] = "Pilot 1 not found in database. Please link their account."
            return result
        
        # Submit PIREP for Pilot 1
        pilot1_duration = f"{pilot1_hours:02d}:{pilot1_minutes:02d}"
        try:
            pilot1_response = await self.bot.cc_api_manager.submit_pirep(
                pilot_id=pilot1_data['id'],
                flight_num=frame_name or 'FLEET',
                departure=dep_icao,
                arrival=arr_icao,
                flight_time=pilot1_duration,
                date=datetime.now().strftime("%Y-%m-%d"),
                aircraft_id=aircraft_id,  # Always 11
                fuel_used=0,
                multiplier=multiplier  # Always 150000
            )
            result['pilot1_result'] = {
                'success': pilot1_response and pilot1_response.get('status') == 0,
                'pilot_name': pilot1_name,  # Always use Discord name
                'duration': pilot1_duration,
                'response': pilot1_response
            }
        except Exception as e:
            self.logger.error(f"Error submitting PIREP for Pilot 1: {e}")
            result['pilot1_result'] = {
                'success': False,
                'pilot_name': pilot1_name or "Unknown",
                'duration': pilot1_duration,
                'error': str(e)
            }
        
        # Submit PIREP for Pilot 2 if provided
        if pilot2_discord_id:
            pilot2_data = await self.bot.pilots_model.get_pilot_by_discord_id(str(pilot2_discord_id))
            if not pilot2_data:
                result['pilot2_result'] = {
                    'success': False,
                    'pilot_name': pilot2_name or "Unknown",
                    'error': "Pilot 2 not found in database"
                }
            else:
                pilot2_duration = f"{pilot2_hours:02d}:{pilot2_minutes:02d}"
                try:
                    pilot2_response = await self.bot.cc_api_manager.submit_pirep(
                        pilot_id=pilot2_data['id'],
                        flight_num=frame_name or 'FLEET',
                        departure=dep_icao,
                        arrival=arr_icao,
                        flight_time=pilot2_duration,
                        date=datetime.now().strftime("%Y-%m-%d"),
                        aircraft_id=aircraft_id,  # Always 11
                        fuel_used=0,
                        multiplier=multiplier  # Always 150000
                    )
                    result['pilot2_result'] = {
                        'success': pilot2_response and pilot2_response.get('status') == 0,
                        'pilot_name': pilot2_name,  # Always use Discord name
                        'duration': pilot2_duration,
                        'response': pilot2_response
                    }
                except Exception as e:
                    self.logger.error(f"Error submitting PIREP for Pilot 2: {e}")
                    result['pilot2_result'] = {
                        'success': False,
                        'pilot_name': pilot2_name or "Unknown",
                        'duration': pilot2_duration,
                        'error': str(e)
                    }
        
        # Determine overall success
        result['success'] = result['pilot1_result'] and result['pilot1_result'].get('success', False)
        
        return result
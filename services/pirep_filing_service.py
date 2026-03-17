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
            if isinstance(response, dict) and response.get("result"):
                 flights = response.get("result", {}).get("data", [])
            
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

    # ================================================================================
    # ASCARIS PIREP FILING METHODS
    # ================================================================================

    async def fetch_if_flights(self, discord_id: int, limit: int = 4) -> Dict:
        """
        Fetch recent flights from Infinite Flight API for a pilot.
        
        Args:
            discord_id: Discord user ID
            limit: Number of flights to fetch (default 4)
            
        Returns:
            {
                'success': True/False,
                'pilot_data': {...},
                'flights': [...],
                'error': 'error message if failed'
            }
        """
        self.logger.info(f"[ASCARIS] fetch_if_flights called for discord_id={discord_id}, limit={limit}")
        
        result = {
            'success': False,
            'pilot_data': None,
            'flights': [],
            'error': None
        }
        
        # 1. Get pilot by Discord ID
        self.logger.info(f"[ASCARIS] Looking up pilot by discord_id={discord_id}")
        pilot_data = await self.bot.pilots_model.get_pilot_by_discord_id(str(discord_id))
        
        if not pilot_data:
            self.logger.warning(f"[ASCARIS] Pilot not found for discord_id={discord_id}")
            result['error'] = "Pilot not found. Please link your account first."
            return result
        
        self.logger.info(f"[ASCARIS] Pilot found: id={pilot_data.get('id')}, callsign={pilot_data.get('callsign')}")
        
        # 2. Check if user has IF user ID
        ifuserid = pilot_data.get('ifuserid')
        if not ifuserid:
            self.logger.warning(f"[ASCARIS] No IF user ID linked for pilot_id={pilot_data.get('id')}")
            result['error'] = "No Infinite Flight account linked. Please link your IF account first."
            return result
        
        self.logger.info(f"[ASCARIS] IF user ID found: {ifuserid}")
        
        # 3. Fetch flights from IF API (all time, not just last 72 hours)
        try:
            self.logger.info(f"[ASCARIS] Fetching up to {limit} flights from IF API for user_id={ifuserid}")
            flights_data = await self.bot.if_api_manager.get_last_user_flights(ifuserid, limit=limit)
            
            self.logger.info(f"[ASCARIS] IF API response received: {type(flights_data)}")
            
            flights = []
            if isinstance(flights_data, list):
                self.logger.info(f"[ASCARIS] Processing {len(flights_data)} flights from API manager")
                
                for i, flight in enumerate(flights_data): # flights_data is already processed
                    duration_minutes = flight.get('duration_minutes', 0) or 0
                    duration_seconds = int(duration_minutes * 60)
                    hours = int(duration_minutes // 60)
                    minutes = int(duration_minutes % 60)
                    duration_str = f"{hours:02d}:{minutes:02d}"
                    
                    created_str = flight.get('created', '')
                    flight_date = created_str[:10] if created_str else datetime.now().strftime('%Y-%m-%d')
                    
                    flight_entry = {
                        'if_flight_id': flight.get('if_flight_id'),
                        'departure': flight.get('departure'),
                        'arrival': flight.get('arrival'),
                        'aircraft_id': flight.get('aircraft_id'),
                        'livery_id': flight.get('livery_id'),
                        'duration_seconds': duration_seconds,
                        'duration': duration_str,
                        'date': flight_date
                    }
                    flights.append(flight_entry)
                    self.logger.info(f"[ASCARIS] Flight {i+1}: {flight_entry['departure']}→{flight_entry['arrival']}, aircraft={flight_entry['aircraft_id'][:20]}..., livery={flight_entry['livery_id'][:20] if flight_entry['livery_id'] else 'None'}...")

            result['success'] = True
            result['pilot_data'] = pilot_data
            result['flights'] = flights
            self.logger.info(f"[ASCARIS] Successfully processed {len(flights)} flights")
            
        except Exception as e:
            self.logger.error(f"[ASCARIS] Error fetching IF flights: {e}", exc_info=True)
            result['error'] = f"Failed to fetch flights from Infinite Flight: {str(e)}"
        
        return result

    async def process_flight(self, pilot_id: int, flight_data: Dict) -> Dict:
        """
        Process a single flight - match aircraft, check rank, validate route.
        
        Args:
            pilot_id: Pilot's database ID
            flight_data: Flight data from IF API
            
        Returns:
            {
                'success': True/False,
                'aircraft_data': {...},
                'route_data': {...},
                'rank_check': {...},
                'pirep_data': {...},
                'error': 'error message if failed'
            }
        """
        self.logger.info(f"[ASCARIS] process_flight called for pilot_id={pilot_id}")
        self.logger.info(f"[ASCARIS] Flight data: dep={flight_data.get('departure')}, arr={flight_data.get('arrival')}, aircraft_id={flight_data.get('aircraft_id', '')[:30]}..., livery_id={flight_data.get('livery_id', '')[:30] if flight_data.get('livery_id') else 'None'}...")
        
        result = {
            'success': False,
            'aircraft_data': None,
            'route_data': None,
            'rank_check': None,
            'pirep_data': None,
            'error': None
        }
        
        # 1. Match aircraft (IF aircraft + livery → CC aircraft)
        self.logger.info(f"[ASCARIS] Step 1: Matching aircraft...")
        aircraft = await self.bot.aircraft_model.get_aircraft_by_if_ids_fallback(
            flight_data.get('aircraft_id', ''),
            flight_data.get('livery_id', '')
        )
        
        if not aircraft:
            self.logger.error(f"[ASCARIS] Aircraft not found in VA database for IF aircraft_id={flight_data.get('aircraft_id')}")
            result['error'] = "Aircraft not found in VA database."
            return result
        
        self.logger.info(f"[ASCARIS] Aircraft matched: CC aircraft_id={aircraft.get('id')}, name={aircraft.get('name')}, livery={aircraft.get('liveryname')}")
        result['aircraft_data'] = aircraft
        
        # 2. Check rank requirement
        self.logger.info(f"[ASCARIS] Step 2: Checking rank requirement for pilot_id={pilot_id} and aircraft_id={aircraft.get('id')}")
        rank_check = await self.bot.rank_model.can_pilot_fly_aircraft(pilot_id, aircraft['id'])
        self.logger.info(f"[ASCARIS] Rank check result: can_fly={rank_check.get('can_fly')}, message={rank_check.get('message')}")
        result['rank_check'] = rank_check
        
        if not rank_check.get('can_fly'):
            self.logger.warning(f"[ASCARIS] Rank check failed: {rank_check.get('message')}")
        
        # 3. Validate route
        dep = flight_data.get('departure', '')
        arr = flight_data.get('arrival', '')
        
        self.logger.info(f"[ASCARIS] Step 3: Validating route {dep} → {arr}")
        route_data = await self.bot.routes_model.find_route_by_icao(dep, arr)
        
        if route_data:
            self.logger.info(f"[ASCARIS] Route found: fltnum={route_data.get('fltnum')}, duration={route_data.get('duration')}")
        else:
            self.logger.warning(f"[ASCARIS] Route NOT found in database for {dep} → {arr}")
        
        result['route_data'] = route_data
        
        # 4. Build PIREP data
        flight_num = route_data.get('fltnum', '').split(',')[0] if route_data else ''
        
        self.logger.info(f"[ASCARIS] Step 4: Building PIREP data")
        result['pirep_data'] = {
            'departure': dep,
            'arrival': arr,
            'duration': flight_data.get('duration', '00:00'),
            'duration_seconds': flight_data.get('duration_seconds', 0),
            'date': flight_data.get('date', datetime.now().strftime('%Y-%m-%d')),
            'aircraft_id': aircraft['id'],  # CC aircraft ID
            'aircraft_name': aircraft.get('name', ''),
            'livery': aircraft.get('liveryname', ''),
            'route_found': route_data is not None,
            'flight_num': flight_num
        }
        
        self.logger.info(f"[ASCARIS] PIREP data built: flight_num={flight_num}, aircraft_id={aircraft['id']}, route_found={route_data is not None}")
        
        result['success'] = True
        return result

    async def _has_owd_rank(self, pilot_rank: dict) -> bool:
        """
        Check if pilot has OneWorld rank or higher.
        
        Args:
            pilot_rank: Dictionary containing rank info from get_pilot_rank
            
        Returns:
            True if pilot has OWD rank, False otherwise
        """
        if not pilot_rank:
            return False
        
        rank_name = pilot_rank.get('name', '').lower()
        # Check for OneWorld or higher ranks
        owd_ranks = ['oneworld', 'one world', 'discover']
        
        # Also check rank level/id - assuming higher IDs = higher ranks
        rank_id = pilot_rank.get('id', 0)
        
        return rank_name in owd_ranks or rank_id >= 5  # Adjust threshold as needed

    async def process_flight_by_type(
        self, 
        pilot_id: int, 
        flight_data: Dict,
        flight_type: str,
        selected_aircraft_id: int = None
    ) -> Dict:
        """
        Process a single flight based on flight type (normal, oneworld, event).
        
        Args:
            pilot_id: Pilot's database ID
            flight_data: Flight data from IF API
            flight_type: 'normal' | 'oneworld' | 'event'
            selected_aircraft_id: Aircraft ID selected from dropdown (optional)
            
        Returns:
            {
                'success': True/False,
                'aircraft_data': {...},
                'route_data': {...},
                'rank_check': {...},
                'pirep_data': {...},
                'error': 'error message if failed'
            }
        """
        self.logger.info(f"[ASCARIS] process_flight_by_type called for pilot_id={pilot_id}, flight_type={flight_type}")
        
        result = {
            'success': False,
            'aircraft_data': None,
            'route_data': None,
            'rank_check': None,
            'pirep_data': None,
            'error': None
        }
        
        dep = flight_data.get('departure', '')
        arr = flight_data.get('arrival', '')
        
        if flight_type == 'oneworld':
            # ONE WORLD: Check rank first, then search OWD routes only
            self.logger.info(f"[ASCARIS] Processing as One World flight")
            
            # 1. Check pilot rank for OWD access
            pilot_rank = await self.bot.rank_model.get_pilot_rank(pilot_id)
            if not self._has_owd_rank(pilot_rank):
                self.logger.warning(f"[ASCARIS] Pilot does not have OneWorld rank")
                result['error'] = "You need OneWorld rank or higher to file OWD flights."
                return result
            
            # 2. Get aircraft data for ID 78 (fixed for OWD)
            self.logger.info(f"[ASCARIS] Getting aircraft ID 78 for OWD")
            aircraft = await self.bot.aircraft_model.get_aircraft_by_id(78)
            if not aircraft:
                self.logger.error(f"[ASCARIS] One World aircraft (ID: 78) not found in database")
                result['error'] = "One World aircraft (ID: 78) not found in database."
                return result
            
            result['aircraft_data'] = aircraft
            
            # 3. Search OWD routes ONLY
            self.logger.info(f"[ASCARIS] Searching OWD route for {dep} → {arr}")
            if hasattr(self.bot, 'owd_route_model'):
                route_data = await self.bot.owd_route_model.find_route_by_icao(dep, arr)
            else:
                self.logger.error(f"[ASCARIS] owd_route_model not available")
                route_data = None
            
            if not route_data:
                self.logger.warning(f"[ASCARIS] OWD route NOT found for {dep} → {arr}")
                result['error'] = f"One World route not found for {dep} → {arr}."
                return result
            
            self.logger.info(f"[ASCARIS] OWD route found: {route_data}")
            result['route_data'] = route_data
            
            # 4. Build PIREP data with fixed aircraft_id = 78
            flight_num = route_data.get('flight_number', '')
            
            result['rank_check'] = {'can_fly': True, 'message': 'OneWorld rank verified'}
            result['pirep_data'] = {
                'departure': dep,
                'arrival': arr,
                'aircraft_id': 78,  # Fixed for OWD
                'aircraft_name': aircraft.get('name', ''),
                'livery': aircraft.get('liveryname', ''),
                'flight_num': flight_num,
                'duration': flight_data.get('duration', '00:00'),
                'duration_seconds': flight_data.get('duration_seconds', 0),
                'date': flight_data.get('date', datetime.now().strftime('%Y-%m-%d')),
                'route_found': True,
                'is_owd': True
            }
            
            result['success'] = True
            return result
        
        elif flight_type == 'event':
            # EVENTS: Skip all searches, go to manual entry with aircraft_id=11
            self.logger.info(f"[ASCARIS] Processing as Event flight")
            
            # 1. Get aircraft data for ID 11 (fixed for events)
            aircraft = await self.bot.aircraft_model.get_aircraft_by_id(11)
            if not aircraft:
                self.logger.error(f"[ASCARIS] Event aircraft (ID: 11) not found in database")
                result['error'] = "Event aircraft (ID: 11) not found in database."
                return result
            
            result['aircraft_data'] = aircraft
            
            # 2. No route search for events
            result['route_data'] = None
            
            # 3. Build PIREP data for manual entry
            result['rank_check'] = {'can_fly': True, 'message': 'Event flight - no rank check'}
            result['pirep_data'] = {
                'departure': dep,
                'arrival': arr,
                'aircraft_id': 11,  # Fixed for events
                'aircraft_name': aircraft.get('name', ''),
                'livery': aircraft.get('liveryname', ''),
                'flight_num': '',  # Empty - user must enter
                'duration': flight_data.get('duration', ''),
                'duration_seconds': flight_data.get('duration_seconds', 0),
                'date': flight_data.get('date', datetime.now().strftime('%Y-%m-%d')),
                'route_found': False,
                'is_manual': True,
                'is_event': True
            }
            
            result['success'] = True
            return result
        
        else:
            # NORMAL: Search CC routes, no OWD fallback
            self.logger.info(f"[ASCARIS] Processing as Normal flight")
            
            # 1. Check if aircraft_id/livery_id is invalid
            if_aircraft_id = flight_data.get('aircraft_id', '')
            if_livery_id = flight_data.get('livery_id', '')
            is_invalid_aircraft = (if_aircraft_id == '0000000000000' or if_livery_id == '0000000000000')
            
            # 2. Try to match aircraft if valid
            aircraft = None
            rank_warning = None
            
            if not is_invalid_aircraft:
                aircraft = await self.bot.aircraft_model.get_aircraft_by_if_ids_fallback(
                    if_aircraft_id,
                    if_livery_id
                )
            
            # 3. If no aircraft matched or invalid, try to use aircraft from selected dropdown or default to 11
            if not aircraft:
                if selected_aircraft_id:
                    aircraft = await self.bot.aircraft_model.get_aircraft_by_id(selected_aircraft_id)
                else:
                    # Try to get aircraft ID 11 as fallback
                    aircraft = await self.bot.aircraft_model.get_aircraft_by_id(11)
                    if aircraft:
                        rank_warning = "⚠️ Aircraft not recognized, used default"
            
            if not aircraft:
                self.logger.error(f"[ASCARIS] Aircraft not found in VA database")
                result['error'] = "Aircraft not found in VA database."
                return result
            
            self.logger.info(f"[ASCARIS] Aircraft matched: CC aircraft_id={aircraft.get('id')}, name={aircraft.get('name')}")
            result['aircraft_data'] = aircraft
            
            # 4. Check rank requirement
            self.logger.info(f"[ASCARIS] Checking rank requirement for aircraft_id={aircraft.get('id')}")
            rank_check = await self.bot.rank_model.can_pilot_fly_aircraft(pilot_id, aircraft['id'])
            
            # If rank check fails, use aircraft_id=11
            if not rank_check.get('can_fly'):
                self.logger.warning(f"[ASCARIS] Rank check failed, using aircraft_id=11")
                aircraft = await self.bot.aircraft_model.get_aircraft_by_id(11)
                if aircraft:
                    rank_check = {'can_fly': True, 'message': 'Used default aircraft due to rank'}
                    rank_warning = "⚠️ Aircraft not in rank, used default aircraft"
                    result['aircraft_data'] = aircraft
            
            result['rank_check'] = rank_check
            
            # 5. Search CC routes ONLY (no OWD fallback)
            self.logger.info(f"[ASCARIS] Searching CC route for {dep} → {arr}")
            route_data = await self.bot.routes_model.find_route_by_icao(dep, arr)
            
            if route_data:
                self.logger.info(f"[ASCARIS] CC route found: {route_data.get('fltnum')}")
            else:
                self.logger.warning(f"[ASCARIS] CC route NOT found for {dep} → {arr}")
            
            result['route_data'] = route_data
            
            # 6. Build PIREP data
            flight_num = route_data.get('fltnum', '').split(',')[0] if route_data else ''
            
            result['pirep_data'] = {
                'departure': dep,
                'arrival': arr,
                'aircraft_id': aircraft['id'],
                'aircraft_name': aircraft.get('name', ''),
                'livery': aircraft.get('liveryname', ''),
                'flight_num': flight_num,
                'duration': flight_data.get('duration', '00:00'),
                'duration_seconds': flight_data.get('duration_seconds', 0),
                'date': flight_data.get('date', datetime.now().strftime('%Y-%m-%d')),
                'route_found': route_data is not None,
                'rank_warning': rank_warning
            }
            
            result['success'] = True
            return result

    async def submit_ascaris_pirep(
        self, 
        pilot_id: int, 
        flight_num: str, 
        departure: str, 
        arrival: str, 
        flight_time: str, 
        date: str,
        aircraft_id: int, 
        multiplier: str = None
    ) -> Dict:
        """
        Submit PIREP via Crew Center API.
        
        Args:
            pilot_id: Pilot's database ID
            flight_num: Flight number
            departure: Departure airport ICAO
            arrival: Arrival airport ICAO
            flight_time: Duration in HH:MM format
            date: Flight date YYYY-MM-DD
            aircraft_id: CC aircraft ID
            multiplier: Multiplier code (e.g., '100000', '120000')
            
        Returns:
            {
                'success': True/False,
                'response': {...},
                'error': 'error message if failed'
            }
        """
        self.logger.info(f"[ASCARIS] submit_ascaris_pirep called:")
        self.logger.info(f"  - pilot_id: {pilot_id}")
        self.logger.info(f"  - flight_num: {flight_num}")
        self.logger.info(f"  - departure: {departure}")
        self.logger.info(f"  - arrival: {arrival}")
        self.logger.info(f"  - flight_time: {flight_time}")
        self.logger.info(f"  - date: {date}")
        self.logger.info(f"  - aircraft_id: {aircraft_id}")
        self.logger.info(f"  - multiplier: {multiplier}")
        
        try:
            self.logger.info(f"[ASCARIS] Submitting PIREP to Crew Center API...")
            response = await self.bot.cc_api_manager.submit_pirep(
                pilot_id=pilot_id,
                flight_num=flight_num,
                departure=departure,
                arrival=arrival,
                flight_time=flight_time,
                date=date,
                aircraft_id=aircraft_id,
                fuel_used=0,
                multiplier=multiplier
            )
            
            self.logger.info(f"[ASCARIS] Crew Center API response: {response}")
            
            success = response and response.get('status') == 0
            
            if success:
                self.logger.info(f"[ASCARIS] PIREP submitted successfully!")
            else:
                self.logger.warning(f"[ASCARIS] PIREP submission failed: status={response.get('status') if response else 'None'}, message={response.get('message') if response else 'None'}")
            
            return {
                'success': success,
                'response': response,
                'error': None if success else response.get('message', 'Unknown error')
            }
            
        except Exception as e:
            self.logger.error(f"[ASCARIS] Error submitting Ascaris PIREP: {e}", exc_info=True)
            return {
                'success': False,
                'response': None,
                'error': str(e)
            }
import os
import asyncio
import aiohttp
from typing import Optional, Dict, Any

class CrewCenterAPIManager:
    """
    An asynchronous wrapper for your custom PHP Crew Center API.
    Handles submitting PIREPs and other VA data from the Discord bot.
    """
    def __init__(self, bot):
        self.bot = bot
        self.api_key = os.getenv("CC_API_KEY") 
        self.base_url = os.getenv("CC_API_BASE_URL")
        
        if not self.api_key or not self.base_url:
            raise ValueError("CC_API_KEY or CC_API_BASE_URL not found in environment variables.")
        
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def connect(self):
        await self._get_session()
        print("Crew Center API session created.")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None 
            print("Crew Center API session closed.")

    async def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        session = await self._get_session()
        
        params = kwargs.pop('params', {})
        params['apikey'] = self.api_key
        
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        
        try:
            async with session.request(method, url, params=params, **kwargs) as response:
                response.raise_for_status()
                
                if 'application/json' in response.headers.get('Content-Type', ''):
                    return await response.json()
                return await response.text()
                
        except aiohttp.ClientResponseError as e:
            print(f"[CC API] Request Error to {url}: {e.status}, message='{e.message}'")
            return None
        except Exception as e:
            print(f"[CC API] Connection Error to {url}: {e}")
            await self.close()
            return None

    async def submit_pirep(
        self, 
        pilot_id: int, 
        flight_num: str, 
        departure: str, 
        arrival: str, 
        flight_time: str, 
        date: str, 
        aircraft_id: int, 
        fuel_used: float, 
        multiplier: Optional[str] = None
    ) -> Dict:
        """
        Submits a PIREP to the Crew Center backend on behalf of a user.

        Args:
            pilot_id (int): The database ID of the pilot (e.g., 2).
            flight_num (str): The flight number without the airline code (e.g., "1234").
            departure (str): The 4-letter ICAO code of the departure airport (e.g., "EGLL").
            arrival (str): The 4-letter ICAO code of the arrival airport (e.g., "KJFK").
            flight_time (str): The duration of the flight, formatted as "HH:MM" (e.g., "05:30").
                               This gets parsed by PHP's Time::strToSecs.
            date (str): The date the flight was completed, formatted as "YYYY-MM-DD" (e.g., "2023-10-25").
            aircraft_id (int): The internal database ID of the aircraft flown (e.g., 1).
                               The pilot must have the required rank/awards to fly this ID.
            fuel_used (float): The total amount of fuel used during the flight (e.g., 15000).
            multiplier (Optional[str]): The name of the multiplier to apply, if any (e.g., "Event").
                                        Must match a multiplier name in database.

        Returns:
            Dict: The JSON response from the PHP API (e.g., {"status": 0, "result": null})
            Where 0 means Successful.
        """
        
        payload = {
            'pilotid': pilot_id,
            'flightnum': flight_num,
            'departure': departure,
            'arrival': arrival,
            'flighttime': flight_time,
            'date': date,
            'aircraft': aircraft_id,
            'fuel': fuel_used
        }

        if multiplier:
            payload['multi'] = multiplier
            
        return await self._request('POST', '/pireps', data=payload)
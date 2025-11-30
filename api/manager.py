import os
import asyncio
import aiohttp
from typing import Optional, List, Dict, Any

class InfiniteFlightAPIManager:
    """
    An asynchronous wrapper for the Infinite Flight Live API.
    Handles creating and closing a single aiohttp session for the bot's lifetime,
    with automatic reconnection logic.
    """
    def __init__(self, bot):
        self.bot = bot
        self.api_key = os.getenv("IF_API_KEY")
        if not self.api_key:
            raise ValueError("IF_API_KEY not found in environment variables. Please add it to your .env file.")
        
        self.base_url = "https://api.infiniteflight.com/public/v2"
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Gets the current session, creating a new one if it doesn't exist or is closed.
        This is the core of the self-healing logic.
        """
        if self._session is None or self._session.closed:
            if self._session is not None: 
                await asyncio.sleep(1)
            self._session = aiohttp.ClientSession()
            print("Infinite Flight API session created or re-created.")
        return self._session

    async def connect(self):
        """Creates the initial aiohttp ClientSession."""
        await self._get_session()
        print("Initial Infinite Flight API session checked/created.")

    async def close(self):
        """Closes the aiohttp ClientSession if it exists and is open."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None 
            print("Infinite Flight API session closed.")

    async def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        """
        Helper function to make API requests with improved, resilient error handling.
        """
        session = await self._get_session()
        
        # Handle params separately to avoid double question marks
        params = kwargs.pop('params', {})
        params['apikey'] = self.api_key
        url = f"{self.base_url}{endpoint}"
        
        # Ensure Content-Type: application/json for POST requests
        headers = kwargs.pop('headers', {})
        if method.upper() == 'POST' and 'json' in kwargs:
            headers['Content-Type'] = 'application/json'
        
        try:
            async with session.request(method, url, params=params, headers=headers, **kwargs) as response:
                response.raise_for_status()
                
                if 'application/json' in response.headers.get('Content-Type', ''):
                    return await response.json()
                return await response.text()
                
        except aiohttp.ClientResponseError as e:
            print(f"API Request Error to {url}: {e.status}, message='{e.message}'")
            return None

        except (aiohttp.ClientConnectorError, asyncio.TimeoutError) as e:
            print(f"API Connection Error to {url}: {e}. Closing session and will retry.")
            await self.close() 
            return None

        except Exception as e:
            print(f"An unexpected error occurred during API request to {url}: {e}")
            await self.close()
            return None

    async def get_sessions(self) -> Dict:
        return await self._request('GET', '/sessions')

    async def get_flights(self, session_id: str) -> Dict:
        return await self._request('GET', f'/flights/{session_id}')

    async def get_flight_route(self, flight_id: str) -> Dict:
        return await self._request('GET', f'/flight/{flight_id}/route')

    async def get_flight_plan(self, flight_id: str) -> Dict:
        return await self._request('GET', f'/flight/{flight_id}/flightplan')

    async def get_atc_facilities(self, session_id: str) -> Dict:
        return await self._request('GET', f'/atc/{session_id}')

    async def get_user_stats(self, discourse_names: List[str]) -> Dict:
        payload = {"discourseNames": discourse_names}
        return await self._request('POST', '/user/stats', json=payload)

    async def get_user_grade(self, user_id: str) -> Dict:
        return await self._request('GET', f'/user/grade/{user_id}')
        
    async def get_atis(self, airport_icao: str, session_id: str) -> Dict:
        return await self._request('GET', f'/airport/{airport_icao}/atis/{session_id}')

    async def get_airport_status(self, airport_icao: str, session_id: str) -> Dict:
        return await self._request('GET', f'/airport/{airport_icao}/status/{session_id}')

    async def get_world_status(self, session_id: str) -> str:
        return await self._request('GET', f'/world/status/{session_id}')

    async def get_oceanic_tracks(self) -> Dict:
        return await self._request('GET', '/tracks')

    async def get_aircraft(self) -> Dict:
        return await self._request('GET', '/aircraft')
    
    async def get_user_flights(self, user_id: str, hours: int = 72) -> Dict:
        """
        Get user flights using the correct Public API v2 endpoint.
        Endpoint: GET /users/{userId}/flights
        """
        # The correct endpoint uses plural 'users' and 'flights'
        # This returns a paginated result including 'data' list.
        return await self._request('GET', f'/users/{user_id}/flights')
    
    async def get_user_by_ifc_username(self, username: str) -> Dict:
        """
        Get user information by IFC username.
        Uses POST /user/stats endpoint with proper JSON formatting.
        """
        payload = {"discourseNames": [username]}
        response = await self._request('POST', '/user/stats', json=payload)
        
        if response and isinstance(response, dict) and response.get('result'):
            result_list = response['result']
            if isinstance(result_list, list) and len(result_list) > 0:
                user_obj = result_list[0]
                return {'result': user_obj}
        
        return None
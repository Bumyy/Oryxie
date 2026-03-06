"""
Flight Board Database Models

This module provides database models for the Flight_Board system.
Enables persistent storage of flight board data for better recovery and performance.

Tables:
- flight_board: Main flight records

Route lookup is done dynamically using flight_num:
- First tries routes_model (CC routes)
- Then tries owd_route_model (OneWorld Discover)
- Then tries mission_dispatcher (World Tour)
"""

from typing import Optional, Dict, List
from datetime import datetime
import logging
from .manager import DatabaseManager

logger = logging.getLogger('oryxie.flight_board_model')


class FlightBoardModel:
    """
    Handles operations related to Flight Board flights.
    Provides persistent storage for flight board data.
    """
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    
    # =========================================================================
    # FLIGHT CRUD OPERATIONS
    # =========================================================================
    
    async def create_flight(self, flight_data: Dict) -> Optional[int]:
        """
        Create a new flight record.
        
        Args:
            flight_data: Dictionary containing flight information
                - flight_num: Flight number (e.g., QR123)
                - pilot_id: Pilot ID from pilots table
                - aircraft_id: Aircraft ID from aircraft table (Crew Center)
                - discord_message_id: Discord message ID
                - discord_thread_id: Discord thread ID (workspace)
                - thumbnail_name: Thumbnail name (amiri, executive)
                - route_map_url: Discord CDN URL for route map image
        
        Returns:
            int: Newly created flight ID, or None if failed
        """
        query = """
            INSERT INTO flight_board (
                flight_num, pilot_id, aircraft_id, 
                discord_message_id, discord_thread_id,
                thumbnail_name, route_map_url,
                created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, NOW()
            )
        """
        params = (
            flight_data.get('flight_num'),
            flight_data.get('pilot_id'),
            flight_data.get('aircraft_id'),
            flight_data.get('discord_message_id'),
            flight_data.get('discord_thread_id'),
            flight_data.get('thumbnail_name'),
            flight_data.get('route_map_url'),
        )
        
        return await self.db.insert(query, params)
    
    async def get_flight_by_id(self, flight_id: int) -> Optional[Dict]:
        """Get flight details by ID."""
        query = "SELECT * FROM flight_board WHERE id = %s"
        return await self.db.fetch_one(query, (flight_id,))
    
    async def get_flight_by_message_id(self, message_id: int) -> Optional[Dict]:
        """Get flight by Discord message ID."""
        query = "SELECT * FROM flight_board WHERE discord_message_id = %s"
        return await self.db.fetch_one(query, (message_id,))
    
    async def get_flight_by_thread_id(self, thread_id: int) -> Optional[Dict]:
        """Get flight by Discord thread ID."""
        query = "SELECT * FROM flight_board WHERE discord_thread_id = %s"
        return await self.db.fetch_one(query, (thread_id,))
    
    async def get_flight_by_number_and_pilot(self, flight_num: str, pilot_id: int) -> Optional[Dict]:
        """Get flight by flight number and pilot ID."""
        query = """
            SELECT * FROM flight_board 
            WHERE flight_num = %s AND pilot_id = %s
            ORDER BY created_at DESC
            LIMIT 1
        """
        return await self.db.fetch_one(query, (flight_num, pilot_id))
    
    async def get_pilot_flights(self, pilot_id: int, limit: int = 10) -> List[Dict]:
        """
        Get flights assigned to a specific pilot.
        
        Args:
            pilot_id: Pilot ID from pilots table
            limit: Maximum number of flights to return
        """
        query = """
            SELECT * FROM flight_board 
            WHERE pilot_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """
        return await self.db.fetch_all(query, (pilot_id, limit))
    
    async def get_recent_flights(self, limit: int = 20) -> List[Dict]:
        """Get recent flights from the flight board."""
        query = """
            SELECT * FROM flight_board 
            ORDER BY created_at DESC
            LIMIT %s
        """
        return await self.db.fetch_all(query, (limit,))
    
    async def update_flight(self, flight_id: int, flight_data: Dict) -> bool:
        """
        Update flight information.
        
        Args:
            flight_id: Flight ID to update
            flight_data: Dictionary containing fields to update
        
        Returns:
            bool: True if successful
        """
        allowed_fields = [
            'aircraft_id', 'discord_thread_id', 
            'thumbnail_name', 'route_map_url'
        ]
        
        updates = []
        params = []
        
        for field in allowed_fields:
            if field in flight_data:
                updates.append(f"{field} = %s")
                params.append(flight_data[field])
        
        if not updates:
            return False
        
        params.append(flight_id)
        query = f"UPDATE flight_board SET {', '.join(updates)} WHERE id = %s"
        return await self.db.execute(query, params)
    
    async def update_thread_id(self, flight_id: int, thread_id: int) -> bool:
        """Update the thread ID for a flight."""
        query = "UPDATE flight_board SET discord_thread_id = %s WHERE id = %s"
        return await self.db.execute(query, (thread_id, flight_id))
    
    async def update_route_map_url(self, flight_id: int, route_map_url: str) -> bool:
        """Update the route map URL for a flight."""
        query = "UPDATE flight_board SET route_map_url = %s WHERE id = %s"
        return await self.db.execute(query, (route_map_url, flight_id))
    
    async def delete_flight(self, flight_id: int) -> bool:
        """Delete a flight record."""
        query = "DELETE FROM flight_board WHERE id = %s"
        return await self.db.execute(query, (flight_id,))
    
    async def delete_flight_by_message_id(self, message_id: int) -> bool:
        """Delete a flight record by message ID."""
        query = "DELETE FROM flight_board WHERE discord_message_id = %s"
        return await self.db.execute(query, (message_id,))
    
    async def update_thread_id_by_message(self, message_id: int, thread_id: int) -> bool:
        """Update thread ID by message ID."""
        query = "UPDATE flight_board SET discord_thread_id = %s WHERE discord_message_id = %s"
        return await self.db.execute(query, (thread_id, message_id))
    
    # =========================================================================
    # ROUTE LOOKUP HELPERS
    # These use existing models to look up route details dynamically
    # =========================================================================
    
    async def get_flight_for_interaction(self, message_id: int, bot) -> Optional[Dict]:
        """
        Get complete flight data for button interaction.
        First tries DB, then falls back to embed parsing.
        
        Args:
            message_id: Discord message ID
            bot: Discord bot instance
        
        Returns:
            Dictionary with flight data or None
        """
        # Try database first
        if self.db:
            try:
                db_flight = await self.get_flight_by_message_id(message_id)
                if db_flight:
                    # Get route details using flight_num
                    route_data = await self.get_route_details(bot, db_flight['flight_num'])
                    
                    if route_data:
                        flight_data = {
                            'flight_num': db_flight['flight_num'],
                            'departure': route_data.get('dep') or route_data.get('departure'),
                            'arrival': route_data.get('arr') or route_data.get('arrival'),
                            'duration': route_data.get('duration'),
                            'aircraft': route_data.get('aircraft', ''),
                            'aircraft_name': route_data.get('aircraft', ''),
                            'livery': route_data.get('livery', 'Qatar Airways'),
                            'pilot_id': db_flight['pilot_id'],
                            'aircraft_id': db_flight['aircraft_id'],  # CC aircraft ID
                        }
                        return flight_data
            except Exception as e:
                logging.error(f"DB flight retrieval failed: {e}")
        
        return None
    
    async def get_route_details(self, bot, flight_num: str, pilot_rank: str = None) -> Optional[Dict]:
        """
        Get route details by flight number.
        Tries multiple sources in order:
        1. Crew Center routes
        2. OneWorld Discover routes
        3. Mission Dispatcher routes
        
        Args:
            bot: Discord bot instance (for accessing models)
            flight_num: Flight number to look up
            pilot_rank: Optional pilot rank for OWD routes
        
        Returns:
            Dictionary with route details or None
        """
        # Try Crew Center routes first
        if hasattr(bot, 'routes_model'):
            try:
                route_data = await bot.routes_model.find_route_by_fltnum(flight_num)
                if route_data:
                    return route_data
            except Exception:
                pass
        
        # Try OneWorld Discover routes (if pilot has OneWorld rank)
        if pilot_rank in ['OneWorld', 'Oryx'] and hasattr(bot, 'owd_route_model'):
            try:
                owd_route = await bot.owd_route_model.find_route_by_flight_number(flight_num)
                if owd_route:
                    return {
                        'dep': owd_route.get('departure'),
                        'arr': owd_route.get('arrival'),
                        'duration': self._parse_flight_time(owd_route.get('flight_time')),
                        'livery': owd_route.get('airline'),
                        'flight_num': owd_route.get('flight_number'),
                        'is_owd': True
                    }
            except Exception:
                pass
        
        return None
    
    def _parse_flight_time(self, flight_time_str: str) -> int:
        """Parse flight time string to seconds."""
        if not flight_time_str:
            return 0
        
        try:
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
    
    # =========================================================================
    # STATISTICS
    # =========================================================================
    
    async def get_flight_count(self) -> int:
        """Get total number of flights in the flight board."""
        query = "SELECT COUNT(*) as count FROM flight_board"
        result = await self.db.fetch_one(query)
        return result['count'] if result else 0
    
    async def get_daily_flight_count(self) -> int:
        """Get number of flights created today."""
        query = "SELECT COUNT(*) as count FROM flight_board WHERE DATE(created_at) = CURDATE()"
        result = await self.db.fetch_one(query)
        return result['count'] if result else 0
    
    async def get_pilot_flight_count(self, pilot_id: int) -> int:
        """Get number of flights for a specific pilot."""
        query = "SELECT COUNT(*) as count FROM flight_board WHERE pilot_id = %s"
        result = await self.db.fetch_one(query, (pilot_id,))
        return result['count'] if result else 0


# ==============================================================================
# SQL SCHEMA DEFINITION
# ==============================================================================

FLIGHT_BOARD_SCHEMA = """
-- ==============================================================================
-- FLIGHT BOARD TABLE
-- ==============================================================================
-- Stores flight board entries with minimal data
-- Route details are looked up dynamically using flight_num

CREATE TABLE IF NOT EXISTS flight_board (
    id                              INT AUTO_INCREMENT PRIMARY KEY,
    flight_num                      VARCHAR(20) NOT NULL,
    pilot_id                        INT NOT NULL,
    aircraft_id                     INT,
    discord_message_id              BIGINT UNIQUE NOT NULL,
    discord_thread_id               BIGINT,
    thumbnail_name                  VARCHAR(50),
    route_map_url                   VARCHAR(500),
    created_at                      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_flight_num (flight_num),
    INDEX idx_pilot_id (pilot_id),
    INDEX idx_discord_message (discord_message_id),
    INDEX idx_discord_thread (discord_thread_id),
    INDEX idx_created_at (created_at),
    
    FOREIGN KEY (pilot_id) REFERENCES pilots(id) ON DELETE CASCADE,
    FOREIGN KEY (aircraft_id) REFERENCES aircraft(id) ON DELETE SET NULL
);
"""

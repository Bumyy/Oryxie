# database/mission_module.py
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from .manager import DatabaseManager

class MissionDB:
    """Database abstraction layer for mission system - MySQL version"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    
    async def get_mission_by_id(self, mission_id: int) -> Optional[Dict[str, Any]]:
        return await self.db.fetch_one("SELECT * FROM scheduled_events WHERE id = %s", (mission_id,))

    async def get_mission_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        return await self.db.fetch_one("SELECT * FROM scheduled_events WHERE title = %s", (title,))

    async def get_pending_missions(self) -> List[Dict[str, Any]]:
        now_utc = datetime.now(timezone.utc)
        return await self.db.fetch_all("SELECT * FROM scheduled_events WHERE is_posted = 0 AND post_time <= %s", (now_utc.isoformat(),))

    async def get_mission_titles(self, search: str = "") -> List[str]:
        if search:
            results = await self.db.fetch_all("SELECT title FROM scheduled_events WHERE title LIKE %s ORDER BY created_at DESC LIMIT 25", (f"%{search}%",))
        else:
            results = await self.db.fetch_all("SELECT title FROM scheduled_events ORDER BY created_at DESC LIMIT 25")
        return [row['title'] for row in results]

    async def create_mission(self, mission_data: Dict[str, Any]) -> None:
        await self.db.execute("""
            INSERT INTO scheduled_events 
            (title, description, image_url, footer_text, color, author_name, flight_numbers, custom_emojis, multiplier, deadline_hours, channel_id, post_time, creator_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            mission_data["title"], mission_data["description"], 
            mission_data["image_url"], mission_data["footer_text"],
            mission_data["color"], mission_data.get("author_name"),
            mission_data["flight_numbers"], mission_data.get("custom_emojis"),
            mission_data.get("multiplier", 0), mission_data.get("deadline_hours", 0),
            mission_data["channel_id"], mission_data["post_time"].isoformat(), 
            mission_data["creator_id"]
        ))

    async def update_mission(self, mission_id: int, mission_data: Dict[str, Any]) -> None:
        await self.db.execute("""
            UPDATE scheduled_events SET
            title = %s, description = %s, image_url = %s, footer_text = %s, color = %s, author_name = %s,
            flight_numbers = %s, custom_emojis = %s, multiplier = %s, deadline_hours = %s, post_time = %s
            WHERE id = %s
        """, (
            mission_data["title"], mission_data["description"],
            mission_data["image_url"], mission_data["footer_text"],
            mission_data["color"], mission_data.get("author_name"),
            mission_data["flight_numbers"], mission_data.get("custom_emojis"),
            mission_data.get("multiplier", 0), mission_data.get("deadline_hours", 0),
            mission_data["post_time"].isoformat(), mission_id
        ))

    async def delete_mission_by_title(self, title: str) -> int:
        return await self.db.execute("DELETE FROM scheduled_events WHERE title = %s", (title,))

    async def mark_mission_posted(self, mission_id: int) -> None:
        await self.db.execute("UPDATE scheduled_events SET is_posted = 1 WHERE id = %s", (mission_id,))
from typing import Optional, Dict, List
from .manager import DatabaseManager


class IFLiveTokensModel:

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    async def get_by_pilot_id(self, pilot_id: int) -> Optional[Dict]:
        query = """
            SELECT * FROM live_if_oauth_tokens WHERE pilot_id = %s
        """
        args = (pilot_id,)
        return await self.db.fetch_one(query, args)

    async def get_by_if_user_id(self, if_user_id: str) -> Optional[Dict]:
        query = """
            SELECT * FROM live_if_oauth_tokens WHERE if_user_id = %s
        """
        args = (if_user_id,)
        return await self.db.fetch_one(query, args)

    async def get_pending_by_state(self, state: str) -> Optional[Dict]:
        query = """
            SELECT * FROM live_if_oauth_tokens WHERE access_token LIKE %s
        """
        args = (f"pending:{state}:%",)
        return await self.db.fetch_one(query, args)

    async def save_token(self, pilot_id: int, access_token: str, refresh_token: str, expires_at, scope: str, if_user_id: str = None) -> int:
        query = """
            INSERT INTO live_if_oauth_tokens (pilot_id, access_token, refresh_token, expires_at, scope, if_user_id, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                access_token = VALUES(access_token),
                refresh_token = VALUES(refresh_token),
                expires_at = VALUES(expires_at),
                scope = VALUES(scope),
                if_user_id = VALUES(if_user_id),
                updated_at = NOW()
        """
        args = (pilot_id, access_token, refresh_token, expires_at, scope, if_user_id)
        return await self.db.insert(query, args)

    async def update_token(self, pilot_id: int, access_token: str, refresh_token: str, expires_at, scope: str) -> int:
        query = """
            UPDATE live_if_oauth_tokens
            SET access_token = %s, refresh_token = %s, expires_at = %s, scope = %s, updated_at = NOW()
            WHERE pilot_id = %s
        """
        args = (access_token, refresh_token, expires_at, scope, pilot_id)
        return await self.db.execute(query, args)

    async def update_if_user_id(self, pilot_id: int, if_user_id: str) -> int:
        query = """
            UPDATE live_if_oauth_tokens
            SET if_user_id = %s, updated_at = NOW()
            WHERE pilot_id = %s
        """
        args = (if_user_id, pilot_id)
        return await self.db.execute(query, args)

    async def set_pending(self, pilot_id: int, state: str, verifier: str) -> int:
        pending_value = f"pending:{state}:{verifier}"
        query = """
            INSERT INTO live_if_oauth_tokens (pilot_id, access_token, refresh_token, expires_at, created_at, updated_at)
            VALUES (%s, %s, %s, NOW(), NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                access_token = VALUES(access_token),
                refresh_token = VALUES(refresh_token),
                expires_at = NOW(),
                updated_at = NOW()
        """
        args = (pilot_id, pending_value, "")
        return await self.db.insert(query, args)

    async def delete_token(self, pilot_id: int) -> int:
        query = """
            DELETE FROM live_if_oauth_tokens WHERE pilot_id = %s
        """
        args = (pilot_id,)
        return await self.db.execute(query, args)

from typing import Optional, Dict, List
from .manager import DatabaseManager

class EventTransactionModel:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.event_name = "halloween_2025"
        self.currency_name = "Candy"

    async def get_balance(self, pilot_id: int) -> int:
        query = "SELECT COALESCE(SUM(amount), 0) AS balance FROM event_transactions WHERE pilot_id = %s AND currency_name = %s"
        result = await self.db.fetch_one(query, (pilot_id, self.currency_name))
        return int(result['balance']) if result else 0

    async def add_transaction(self, pilot_id: int, amount: int, reason: str) -> bool:
        query = "INSERT INTO event_transactions (pilot_id, event_name, currency_name, amount, reason) VALUES (%s, %s, %s, %s, %s)"
        args = (pilot_id, self.event_name, self.currency_name, amount, reason)
        return await self.db.execute(query, args) is not None

    async def check_duplicate(self, pilot_id: int, reason: str) -> bool:
        query = "SELECT id FROM event_transactions WHERE pilot_id = %s AND reason = %s"
        result = await self.db.fetch_one(query, (pilot_id, reason))
        return result is not None

    async def check_cooldown(self, pilot_id: int, reason_pattern: str, hours: int = 20) -> bool:
        query = """SELECT id FROM event_transactions 
                   WHERE pilot_id = %s AND reason LIKE %s 
                   AND transaction_date > DATE_SUB(NOW(), INTERVAL %s HOUR)"""
        result = await self.db.fetch_one(query, (pilot_id, reason_pattern, hours))
        return result is not None

    async def get_all_records(self) -> List[Dict]:
        query = "SELECT et.*, p.callsign FROM event_transactions et LEFT JOIN pilots p ON et.pilot_id = p.id ORDER BY et.transaction_date DESC"
        return await self.db.fetch_all(query)

    async def get_top_holders(self, limit: int = 3) -> List[Dict]:
        query = """SELECT p.callsign, SUM(et.amount) as total_candy 
                   FROM event_transactions et 
                   JOIN pilots p ON et.pilot_id = p.id 
                   WHERE et.currency_name = %s AND p.status = 1
                   GROUP BY et.pilot_id, p.callsign 
                   ORDER BY total_candy DESC 
                   LIMIT %s"""
        return await self.db.fetch_all(query, (self.currency_name, limit))

    async def count_claims(self, reason: str) -> int:
        query = "SELECT COUNT(id) AS claim_count FROM event_transactions WHERE reason = %s"
        result = await self.db.fetch_one(query, (reason,))
        return result['claim_count'] if result else 0

    async def get_last_transaction(self, pilot_id: int, reason_pattern: str) -> Optional[Dict]:
        query = """SELECT * FROM event_transactions 
                   WHERE pilot_id = %s AND reason LIKE %s 
                   ORDER BY transaction_date DESC LIMIT 1"""
        return await self.db.fetch_one(query, (pilot_id, reason_pattern))

    async def process_pirep_reward(self, pirep_data: Dict, pilots_model) -> bool:
        pirep_id = pirep_data['pirep_id']
        pilot_id = pirep_data['pilotid']
        flight_time_seconds = pirep_data.get('flighttime', 0)
        multiplier = float(pirep_data.get('multi', 1) or 1)
        
        raw_flight_time_seconds = flight_time_seconds / multiplier if multiplier > 0 else flight_time_seconds
        candy_amount = max(1, int(raw_flight_time_seconds // 60)) if raw_flight_time_seconds else 1
        
        reason = f"PIREP Reward: #{pirep_id}"
        
        if await self.check_duplicate(pilot_id, reason):
            return False
            
        pilot_data = await pilots_model.get_pilot_by_id(pilot_id)
        if not pilot_data:
            return False
            
        return await self.add_transaction(pilot_id, candy_amount, reason)
import logging
from database.pilots_model import PilotsModel

logger = logging.getLogger('oryxie.services.priority_service')

class PriorityService:
    def __init__(self, bot):
        self.bot = bot
        self.pilots_model = PilotsModel(self.bot.db_manager)

    async def get_pilot_total_hours(self, pilot_id: int) -> float:
        """Get total flight hours for a pilot from accepted PIREPs"""
        try:
            query = "SELECT SUM(flighttime) as total_seconds FROM pireps WHERE pilotid = %s AND status = 1"
            result = await self.bot.db_manager.fetch_one(query, (pilot_id,))
            if result and result['total_seconds']:
                return round(result['total_seconds'] / 3600, 1)
        except Exception as e:
            logger.error(f"Error fetching pilot hours: {e}")
        return 0.0

    async def get_member_priority(self, member) -> tuple:
        """
        Returns (priority_group, sort_value) where:
        - priority_group: 0=staff, 1=pilot_of_month, 2=regular
        - sort_value: callsign_number for staff, -hours for others (negative for desc sort)
        """
        # Try to identify pilot
        pilot_result = await self.pilots_model.identify_pilot(member)
        
        if pilot_result['success']:
            callsign = pilot_result['pilot_data']['callsign']
            
            # Check if staff callsign (QRV001-QRV019)
            if callsign.startswith('QRV'):
                try:
                    callsign_num = int(callsign[3:])  # Extract number after QRV
                    if 1 <= callsign_num <= 19:
                        return (0, callsign_num)  # Staff priority, sorted by callsign number
                except ValueError:
                    logger.debug(f"Invalid callsign format: {callsign}")
            
            # Get flight hours for non-staff
            hours = await self.get_pilot_total_hours(pilot_result['pilot_data']['id'])
        else:
            hours = 0.0
        
        # Check for Pilot of the Month role
        if any(role.name.lower() == "pilot of the month" for role in member.roles):
            return (1, -hours)  # Second priority, sorted by hours desc
        
        # Regular pilot
        return (2, -hours)  # Third priority, sorted by hours desc

    async def sort_members_by_priority(self, members: list) -> list:
        """Sort a list of Discord members by priority and return sorted list"""
        member_priorities = []
        
        for member in members:
            priority = await self.get_member_priority(member)
            member_priorities.append((member, priority))
        
        # Sort by priority tuple (group first, then sort value)
        member_priorities.sort(key=lambda x: x[1])
        
        return [member for member, _ in member_priorities]

    async def get_priority_debug_info(self, members: list) -> list:
        """Get debug information for member priorities"""
        debug_info = []
        
        for member in members:
            priority = await self.get_member_priority(member)
            pilot_result = await self.pilots_model.identify_pilot(member)
            callsign = pilot_result['pilot_data']['callsign'] if pilot_result['success'] else 'No callsign'
            
            if priority[0] == 0:  # Staff
                category = 'Staff callsign'
            elif priority[0] == 1:  # Pilot of the month
                hours = -priority[1]  # Convert back from negative
                category = f'Pilot of the month : {hours}hrs'
            else:  # Regular pilot
                hours = -priority[1]  # Convert back from negative
                category = f'{hours}hrs'
            
            debug_info.append(f"{member.mention} : {callsign} : {category}")
        
        return debug_info
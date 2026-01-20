import logging
from database.pilots_model import PilotsModel

logger = logging.getLogger('oryxie.services.priority_service')

class PriorityService:
    def __init__(self, bot):
        self.bot = bot
        self.pilots_model = PilotsModel(self.bot.db_manager)
        # Team role IDs
        self.TEAM_A_ROLE_ID = 1463169315976249461
        self.TEAM_B_ROLE_ID = 1463169521534763084

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

    async def get_member_priority(self, member, event_organiser_id=None) -> tuple:
        """
        Returns (priority_group, sort_value) where:
        - priority_group: 0=high_staff, 1=event_organiser, 2=regular_staff, 3=others
        - sort_value: callsign_number for staff, -hours for others (negative for desc sort)
        """
        # Check if this member is the event organiser
        if event_organiser_id and member.id == event_organiser_id:
            hours = await self.get_pilot_total_hours_by_member(member)
            return (1, -hours)  # Event organiser priority
        
        # Try to identify pilot
        pilot_result = await self.pilots_model.identify_pilot(member)
        
        if pilot_result['success']:
            callsign = pilot_result['pilot_data']['callsign']
            
            # Check if staff callsign (QRV001-QRV019)
            if callsign.startswith('QRV'):
                try:
                    callsign_num = int(callsign[3:])  # Extract number after QRV
                    if 1 <= callsign_num <= 4:
                        return (0, callsign_num)  # High staff priority (QRV001-004)
                    elif 5 <= callsign_num <= 19:
                        return (2, callsign_num)  # Regular staff priority (QRV005-019)
                except ValueError:
                    logger.debug(f"Invalid callsign format: {callsign}")
            
            # Get flight hours for non-staff
            hours = await self.get_pilot_total_hours(pilot_result['pilot_data']['id'])
        else:
            hours = 0.0
        
        # Check for Pilot of the Month role
        if any(role.name.lower() == "pilot of the month" for role in member.roles):
            return (3, -hours)  # Others priority, sorted by hours desc
        
        # Regular pilot
        return (3, -hours)  # Others priority, sorted by hours desc

    async def get_pilot_total_hours_by_member(self, member) -> float:
        """Get total flight hours for a member by identifying their pilot ID first"""
        try:
            pilot_result = await self.pilots_model.identify_pilot(member)
            if pilot_result['success']:
                return await self.get_pilot_total_hours(pilot_result['pilot_data']['id'])
        except Exception as e:
            logger.error(f"Error fetching pilot hours for member: {e}")
        return 0.0

    async def sort_members_by_priority(self, members: list, event_organiser_id=None) -> list:
        """Sort a list of Discord members by priority and return sorted list"""
        member_priorities = []
        
        for member in members:
            priority = await self.get_member_priority(member, event_organiser_id)
            member_priorities.append((member, priority))
        
        # Sort by priority tuple (group first, then sort value)
        member_priorities.sort(key=lambda x: x[1])
        
        return [member for member, _ in member_priorities]

    async def assign_teams(self, sorted_members: list) -> tuple:
        """Assign members to Team A (odd positions) and Team B (even positions)"""
        team_a = []
        team_b = []
        
        for i, member in enumerate(sorted_members):
            if (i + 1) % 2 == 1:  # Odd position (1, 3, 5, ...)
                team_a.append(member)
            else:  # Even position (2, 4, 6, ...)
                team_b.append(member)
        
        return team_a, team_b

    def set_team_role_ids(self, team_a_id: int, team_b_id: int):
        """Set the role IDs for Team A and Team B"""
        self.TEAM_A_ROLE_ID = team_a_id
        self.TEAM_B_ROLE_ID = team_b_id

    async def get_priority_debug_info(self, members: list, event_organiser_id=None) -> list:
        """Get debug information for member priorities"""
        debug_info = []
        
        for i, member in enumerate(members):
            priority = await self.get_member_priority(member, event_organiser_id)
            pilot_result = await self.pilots_model.identify_pilot(member)
            callsign = pilot_result['pilot_data']['callsign'] if pilot_result['success'] else 'No callsign'
            
            # Determine team assignment
            team = "Team A" if (i + 1) % 2 == 1 else "Team B"
            
            if priority[0] == 0:  # High staff
                category = 'High Staff (QRV001-004)'
            elif priority[0] == 1:  # Event organiser
                hours = -priority[1]  # Convert back from negative
                category = f'Event Organiser : {hours}hrs'
            elif priority[0] == 2:  # Regular staff
                category = 'Regular Staff (QRV005-019)'
            else:  # Others
                hours = -priority[1]  # Convert back from negative
                pilot_of_month = any(role.name.lower() == "pilot of the month" for role in member.roles)
                if pilot_of_month:
                    category = f'Pilot of the month : {hours}hrs'
                else:
                    category = f'{hours}hrs'
            
            debug_info.append(f"{member.mention} : {callsign} : {category} : {team}")
        
        return debug_info
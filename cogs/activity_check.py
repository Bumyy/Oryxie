import discord
from discord.ext import commands
from discord import app_commands
import re
from datetime import datetime, timedelta

class ActivityCheckCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.INACTIVE_ROLE_ID = 1224371905012961342
        self.TRAINEE_ROLE_ID = 1137998383630524477
        self.LOA_ROLE_ID = 1193185998859935755

    @app_commands.command(name="activity_check", description="Manage pilot activity checking")
    @app_commands.describe(
        action="What to do",
        callsign="Pilot callsign (for check_pilot)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Run Full Activity Check", value="run_check"),
        app_commands.Choice(name="Check Specific Pilot", value="check_pilot")
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def activity_check(self, interaction: discord.Interaction, action: str, callsign: str = None):
        if action == "run_check":
            await self._run_full_activity_check(interaction)
        elif action == "check_pilot":
            if not callsign:
                await interaction.response.send_message("Callsign is required for pilot check.", ephemeral=True)
                return
            await self._check_specific_pilot(interaction, callsign)

    async def _run_full_activity_check(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        # Validate dependencies
        if not hasattr(self.bot, 'pilots_model') or not hasattr(self.bot, 'db_manager'):
            await interaction.followup.send("❌ Database services not available!", ephemeral=True)
            return
        
        # Get all server members
        guild = interaction.guild
        members = guild.members
        
        # Get roles
        inactive_role = guild.get_role(self.INACTIVE_ROLE_ID)
        trainee_role = guild.get_role(self.TRAINEE_ROLE_ID)
        loa_role = guild.get_role(self.LOA_ROLE_ID)
        
        if not inactive_role:
            await interaction.followup.send("❌ Inactive role not found!", ephemeral=True)
            return
        
        # Scan members for callsigns
        pilots_found = []
        no_callsign = []
        
        for member in members:
            if member.bot:
                continue
                
            callsign_match = re.search(r'QRV\d{3,}', member.display_name, re.IGNORECASE)
            if callsign_match:
                callsign = callsign_match.group(0).upper()
                pilots_found.append({
                    'member': member,
                    'callsign': callsign,
                    'display_name': member.display_name
                })
            else:
                try:
                    pilot_data = await self.bot.pilots_model.get_pilot_by_discord_id(str(member.id))
                    if pilot_data and pilot_data.get('callsign'):
                        pilots_found.append({
                            'member': member,
                            'callsign': pilot_data['callsign'],
                            'display_name': member.display_name
                        })
                    else:
                        no_callsign.append(member)
                except Exception:
                    no_callsign.append(member)
        
        # Process pilots for activity check
        processed = 0
        removed_inactive = 0
        skipped_protected = 0
        skipped_trainee = 0
        skipped_loa = 0
        skipped_already_inactive = 0
        skipped_veteran = 0
        skipped_active = 0
        newly_inactive = []
        
        for pilot_info in pilots_found:
            member = pilot_info['member']
            callsign = pilot_info['callsign']
            has_inactive_role = inactive_role in member.roles
            
            # Determine if pilot should be inactive based on conditions
            should_be_inactive = await self._should_pilot_be_inactive(callsign, member, trainee_role, loa_role)
            
            if should_be_inactive is None:
                # Database error or pilot not found
                continue
            elif should_be_inactive == "protected":
                skipped_protected += 1
                if has_inactive_role:
                    await member.remove_roles(inactive_role, reason="Protected callsign")
                    removed_inactive += 1
            elif should_be_inactive == "trainee":
                skipped_trainee += 1
                if has_inactive_role:
                    await member.remove_roles(inactive_role, reason="Trainee role")
                    removed_inactive += 1
            elif should_be_inactive == "loa":
                skipped_loa += 1
                if has_inactive_role:
                    await member.remove_roles(inactive_role, reason="LOA role")
                    removed_inactive += 1
            elif should_be_inactive == "veteran":
                skipped_veteran += 1
                if has_inactive_role:
                    await member.remove_roles(inactive_role, reason="Veteran status")
                    removed_inactive += 1
            elif should_be_inactive == "active":
                skipped_active += 1
                if has_inactive_role:
                    await member.remove_roles(inactive_role, reason="Recent activity")
                    removed_inactive += 1
            elif should_be_inactive == True:
                # Should be inactive
                if not has_inactive_role:
                    try:
                        pilot_data = await self.bot.pilots_model.get_pilot_by_callsign(callsign)
                        last_pirep_date = await self._get_pilot_last_pirep_date(pilot_data['id'])
                        days_since = (datetime.now().date() - last_pirep_date).days if last_pirep_date else 'Never'
                        
                        await member.add_roles(inactive_role, reason="No PIREP in 60+ days")
                        newly_inactive.append({
                            'callsign': callsign,
                            'member': member,
                            'last_pirep': last_pirep_date.strftime('%Y-%m-%d') if last_pirep_date else 'Never',
                            'days': days_since
                        })
                        processed += 1
                    except Exception as e:
                        print(f"Error adding inactive role to {callsign}: {e}")
                else:
                    skipped_already_inactive += 1
        
        # Send reports
        await self._send_summary_report(interaction, len(members), len(pilots_found), len(no_callsign), 
                                      processed, removed_inactive, skipped_protected, skipped_trainee, skipped_loa, skipped_already_inactive, 
                                      skipped_veteran, skipped_active)
        
        if newly_inactive:
            await self._send_inactive_list(interaction, newly_inactive)
        
        if no_callsign:
            await self._send_no_callsign_list(interaction, no_callsign)
    
    async def _should_pilot_be_inactive(self, callsign: str, member, trainee_role, loa_role):
        """Determine if pilot should be inactive. Returns True/False/reason string or None for error"""
        try:
            # Check protected callsigns (QRV001-QRV019)
            callsign_num = int(callsign[3:])
            if 1 <= callsign_num <= 19:
                return "protected"
            
            # Check trainee role
            if trainee_role and trainee_role in member.roles:
                return "trainee"
            
            # Check LOA role
            if loa_role and loa_role in member.roles:
                return "loa"
            
            # Check database conditions
            pilot_data = await self.bot.pilots_model.get_pilot_by_callsign(callsign)
            if not pilot_data:
                return None
            
            # Check if veteran (5000+ hours)
            flight_hours = await self._get_pilot_flight_hours(pilot_data['id'])
            if flight_hours and flight_hours >= 5000:
                return "veteran"
            
            # Check activity (last PIREP within 60 days)
            last_pirep_date = await self._get_pilot_last_pirep_date(pilot_data['id'])
            if last_pirep_date:
                days_since = (datetime.now().date() - last_pirep_date).days
                if days_since < 60:
                    return "active"
            
            # Should be inactive
            return True
            
        except Exception as e:
            print(f"Error checking pilot status for {callsign}: {e}")
            return None

    async def _check_specific_pilot(self, interaction: discord.Interaction, callsign: str):
        await interaction.response.defer(ephemeral=True)
        
        # Format callsign
        if not callsign.upper().startswith('QRV'):
            callsign = f"QRV{callsign}"
        
        # Get pilot data
        pilot = await self.bot.pilots_model.get_pilot_by_callsign(callsign.upper())
        if not pilot:
            await interaction.followup.send(f"❌ Pilot {callsign} not found", ephemeral=True)
            return
        
        # Get activity data
        last_pirep_date = await self._get_pilot_last_pirep_date(pilot['id'])
        flight_hours = await self._get_pilot_flight_hours(pilot['id'])
        
        report = f"Activity Report: {callsign}\n\n"
        report += f"Last PIREP: {last_pirep_date.strftime('%Y-%m-%d') if last_pirep_date else 'Never'}\n"
        report += f"Total Flight Hours: {flight_hours:.1f}\n"
        
        if last_pirep_date:
            days_ago = (datetime.now().date() - last_pirep_date).days
            status = "🟢 Active" if days_ago <= 60 else "🔴 Inactive"
            report += f"Days Since Last PIREP: {days_ago}\n"
            report += f"Status: {status}"
        else:
            report += "Status: 🔴 No PIREPs found"
        
        await interaction.followup.send(report, ephemeral=True)

    async def _get_pilot_flight_hours(self, pilot_id):
        """Get total flight hours for pilot from PIREPs"""
        query = """
        SELECT SUM(flighttime) as total_seconds 
        FROM pireps 
        WHERE pilotid = %s AND status = 1
        """
        result = await self.bot.db_manager.fetch_one(query, (pilot_id,))
        total_seconds = result['total_seconds'] if result and result['total_seconds'] else 0
        return total_seconds / 3600  # Convert seconds to hours

    async def _get_pilot_last_pirep_date(self, pilot_id):
        """Get pilot's most recent PIREP date"""
        query = """
        SELECT DATE(date) as pirep_date 
        FROM pireps 
        WHERE pilotid = %s AND status = 1 
        ORDER BY date DESC 
        LIMIT 1
        """
        result = await self.bot.db_manager.fetch_one(query, (pilot_id,))
        return result['pirep_date'] if result else None

    async def _send_summary_report(self, interaction, total_members, pilots_found, no_callsign_count, 
                                 processed, removed_inactive, skipped_protected, skipped_trainee, skipped_loa, skipped_already_inactive, 
                                 skipped_veteran, skipped_active):
        """Send summary report"""
        total_skipped = skipped_protected + skipped_trainee + skipped_loa + skipped_already_inactive + skipped_veteran + skipped_active
        
        report = f"""Activity Check Complete!

Server Members Scanned: {total_members}
Pilots Found (QRV###): {pilots_found}
No Callsign Found: {no_callsign_count}

PROCESSED: {processed} pilots marked inactive
REMOVED INACTIVE: {removed_inactive} pilots reactivated
SKIPPED: {total_skipped} pilots

SKIPPED BREAKDOWN:
- Protected (QRV001-019): {skipped_protected}
- Trainees: {skipped_trainee}
- LOA Members: {skipped_loa}
- Already Inactive: {skipped_already_inactive}
- Veterans (5000+ hrs): {skipped_veteran}
- Active (recent PIREPs): {skipped_active}"""
        
        await interaction.followup.send(report, ephemeral=True)

    async def _send_inactive_list(self, interaction, newly_inactive):
        """Send newly inactive pilots list"""
        if not newly_inactive:
            return
        
        report = f"NEWLY INACTIVE ({len(newly_inactive)}):\n"
        
        for pilot in newly_inactive:
            line = f"{pilot['callsign']} @{pilot['member'].display_name} ({pilot['days']} days)\n"
            
            # Check if adding this line would exceed limit
            if len(report + line) > 1900:
                await interaction.followup.send(report, ephemeral=True)
                report = "NEWLY INACTIVE (continued):\n" + line
            else:
                report += line
        
        if report.strip() != "NEWLY INACTIVE (continued):":
            await interaction.followup.send(report, ephemeral=True)

    async def _send_no_callsign_list(self, interaction, no_callsign_members):
        """Send no callsign members list"""
        if not no_callsign_members:
            return
        
        report = f"NO CALLSIGN FOUND ({len(no_callsign_members)}):\n"
        
        for member in no_callsign_members:
            line = f"@{member.display_name}, "
            
            # Check if adding this line would exceed limit
            if len(report + line) > 1900:
                await interaction.followup.send(report.rstrip(', '), ephemeral=True)
                report = "NO CALLSIGN (continued):\n" + line
            else:
                report += line
        
        if report.strip() != "NO CALLSIGN (continued):":
            await interaction.followup.send(report.rstrip(', '), ephemeral=True)

    @activity_check.error
    async def on_activity_check_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need administrator permissions to use this command.", ephemeral=True)
        else:
            await interaction.response.send_message(f"An error occurred: {error}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(ActivityCheckCog(bot))
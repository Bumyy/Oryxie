import discord
from discord import app_commands
from discord.ext import commands
import re
import asyncio

class DatabaseAuditCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="audit", description="Database audit operations")
    @app_commands.describe(action="What audit action to perform")
    @app_commands.choices(action=[
        app_commands.Choice(name="Check IFC Usernames Validity", value="check_ifc_usernames"),
        app_commands.Choice(name="Active Pilots Only", value="active_pilots"),
        app_commands.Choice(name="Pilots Table", value="pilots")
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def audit(self, interaction: discord.Interaction, action: str):
        if action == "check_ifc_usernames":
            await self._check_ifc_usernames_validity(interaction)
        elif action == "active_pilots":
            await self._audit_active_pilots(interaction)
        elif action == "pilots":
            await self._audit_pilots(interaction)

    async def _check_ifc_usernames_validity(self, interaction: discord.Interaction):
        """Check all active pilots' IFC usernames by fetching user stats from API."""
        await interaction.response.defer(ephemeral=False)
        
        try:
            # 1. Fetch pilots from DB
            query = "SELECT id, callsign, name, discordid, ifc FROM pilots WHERE status = 1 AND ifc IS NOT NULL AND ifc != ''"
            active_pilots = await self.bot.db_manager.fetch_all(query)
            
            if not active_pilots:
                await interaction.followup.send("No active pilots with IFC usernames found.", ephemeral=False)
                return
            
            await interaction.followup.send(f"🔍 **Checking {len(active_pilots)} active pilots' IFC usernames...**\nProcessing in batches of 20.", ephemeral=False)
            
            batch_size = 20
            invalid_pilots = []
            updated_count = 0
            
            # 2. Process in batches
            for i in range(0, len(active_pilots), batch_size):
                batch = active_pilots[i:i + batch_size]
                batch_num = (i // batch_size) + 1
                total_batches = (len(active_pilots) + batch_size - 1) // batch_size
                
                # Update status
                if batch_num % 1 == 0: # Notify every batch
                    await interaction.followup.send(f"📊 **Processing Batch {batch_num}/{total_batches}**...", ephemeral=False)
                
                batch_invalid = []
                
                for pilot in batch:
                    # Small delay to prevent API Rate Limiting (429 Too Many Requests)
                    await asyncio.sleep(0.3) 
                    
                    ifc_url = pilot['ifc']
                    
                    # IMPROVED REGEX:
                    # Handles: /u/username, /users/username, /u/username/summary, /u/username?preferences
                    username_match = re.search(r'/(?:u|users)/([^/?#\s]+)', ifc_url)
                    
                    if not username_match:
                        # Fallback: maybe they just entered the username directly without URL?
                        if "http" not in ifc_url and "/" not in ifc_url:
                            username = ifc_url.strip()
                        else:
                            batch_invalid.append({
                                'name': pilot['name'],
                                'callsign': pilot['callsign'],
                                'discord_id': pilot['discordid'],
                                'reason': f'Invalid URL format: {ifc_url}'
                            })
                            continue
                    else:
                        username = username_match.group(1)
                    
                    try:
                        # Call the API Manager (Must be the updated version I sent previously)
                        user_data = await self.bot.if_api_manager.get_user_by_ifc_username(username)
                        
                        # Logic: If user_data is None or empty, the user doesn't exist (or API failed)
                        if not user_data or not user_data.get('result'):
                            batch_invalid.append({
                                'name': pilot['name'],
                                'callsign': pilot['callsign'],
                                'discord_id': pilot['discordid'],
                                'reason': f'Username "{username}" not found'
                            })
                            continue
                        
                        user_id = user_data['result'].get('userId')
                        if not user_id:
                            batch_invalid.append({
                                'name': pilot['name'],
                                'callsign': pilot['callsign'],
                                'discord_id': pilot['discordid'],
                                'reason': 'No UserID in API response'
                            })
                            continue
                        
                        # Update database with the found user ID
                        try:
                            rows_updated = await self.bot.pilots_model.update_ifuserid_by_ifc_username(username, user_id)
                            if rows_updated > 0:
                                updated_count += rows_updated
                        except Exception as db_e:
                            await interaction.followup.send(f"🔧 **DEBUG:** DB Update Error for {username}: {str(db_e)}", ephemeral=False)
                    
                    except Exception as api_e:
                        await interaction.followup.send(f"🔧 **DEBUG:** API Error for {username}: {str(api_e)}", ephemeral=False)
                        batch_invalid.append({
                            'name': pilot['name'],
                            'callsign': pilot['callsign'],
                            'discord_id': pilot['discordid'],
                            'reason': f'API Error: {str(api_e)}'
                        })
                
                invalid_pilots.extend(batch_invalid)
                
                if batch_invalid:
                    # Format the error message to be readable
                    result_msg = f"❌ **Batch {batch_num} Issues:**\n"
                    for p in batch_invalid:
                        d_id = f"<@{p['discord_id']}>" if p['discord_id'] else "No Discord"
                        callsign = p.get('callsign', 'N/A')
                        result_msg += f"• **{p['name']}** ({callsign}): {p['reason']} - {d_id}\n"
                    
                    if len(result_msg) > 1900:
                         result_msg = result_msg[:1900] + "...(truncated)"
                    await interaction.followup.send(result_msg, ephemeral=False)
                    
                    # Send separate ping message for easy contact
                    ping_msg = "📞 **Contact these pilots:** "
                    ping_msg += " ".join([f"<@{p['discord_id']}>" for p in batch_invalid if p['discord_id']])
                    if len(ping_msg) > 20:  # Only send if there are actual pings
                        await interaction.followup.send(ping_msg, ephemeral=False)
            
            # Final Summary
            total_valid = len(active_pilots) - len(invalid_pilots)
            summary_msg = f"\n📋 **FINAL AUDIT SUMMARY**\n"
            summary_msg += f"✅ **Valid IFC Usernames:** {total_valid}\n"
            summary_msg += f"❌ **Invalid/Not Found:** {len(invalid_pilots)}\n"
            summary_msg += f"💾 **Database Records Updated:** {updated_count}\n"
            summary_msg += f"📊 **Total Checked:** {len(active_pilots)}"
            
            await interaction.followup.send(summary_msg, ephemeral=False)
            
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            await interaction.followup.send(f"❌ **Critical Error during audit:** {str(e)}\n```\n{error_trace[:1500]}\n```", ephemeral=False)

    async def _audit_active_pilots(self, interaction: discord.Interaction):
        """Audits callsigns for Status 1 (active) pilots only."""
        await interaction.response.defer(ephemeral=False)
        
        try:
            query = "SELECT id, callsign, name FROM pilots WHERE status = 1 AND callsign IS NOT NULL AND callsign != ''"
            pilots = await self.bot.db_manager.fetch_all(query)
            
            invalid_callsigns = []
            duplicate_callsigns = {}
            callsign_counts = {}
            
            for pilot in pilots:
                callsign = pilot['callsign'].strip()
                pilot_id = pilot['id']
                pilot_name = pilot['name']
                
                if callsign in callsign_counts:
                    callsign_counts[callsign].append({'id': pilot_id, 'name': pilot_name})
                else:
                    callsign_counts[callsign] = [{'id': pilot_id, 'name': pilot_name}]
                
                if not re.match(r'^QRV\d{3}$', callsign, re.IGNORECASE):
                    invalid_callsigns.append({
                        'id': pilot_id,
                        'callsign': callsign,
                        'name': pilot_name
                    })
            
            for callsign, pilots_list in callsign_counts.items():
                if len(pilots_list) > 1:
                    duplicate_callsigns[callsign] = pilots_list
            
            report_msg = "**🔍 ACTIVE PILOTS CALLSIGN AUDIT**\n\n"
            report_msg += f"📊 **Active Pilots Checked:** {len(pilots)}\n\n"
            
            if invalid_callsigns:
                report_msg += f"❌ **Invalid QRV Format ({len(invalid_callsigns)}):**\n```\n"
                for invalid in invalid_callsigns:
                    report_msg += f"ID: {invalid['id']} | Callsign: {invalid['callsign']} | Name: {invalid['name']}\n"
                report_msg += "```\n\n"
            else:
                report_msg += "✅ **Invalid Format:** No invalid callsigns found.\n\n"
            
            if duplicate_callsigns:
                report_msg += f"⚠️ **Duplicate Callsigns ({len(duplicate_callsigns)}):**\n```\n"
                for callsign, pilots_list in duplicate_callsigns.items():
                    report_msg += f"Callsign: {callsign} (Found {len(pilots_list)} times)\n"
                    for pilot in pilots_list:
                        report_msg += f"  - ID: {pilot['id']} | Name: {pilot['name']}\n"
                    report_msg += "\n"
                report_msg += "```\n\n"
            else:
                report_msg += "✅ **Duplicates:** No duplicate callsigns found.\n\n"
            
            if not invalid_callsigns and not duplicate_callsigns:
                report_msg += "🎉 **All active pilot callsigns are valid and unique!**"
            else:
                issues_count = len(invalid_callsigns) + len(duplicate_callsigns)
                report_msg += f"⚠️ **Total Issues Found:** {issues_count}"
            
            if len(report_msg) > 2000:
                parts = [report_msg[i:i+1900] for i in range(0, len(report_msg), 1900)]
                for part in parts:
                    await interaction.followup.send(part, ephemeral=False)
            else:
                await interaction.followup.send(report_msg, ephemeral=False)
            
        except Exception as e:
            await interaction.followup.send(f"An unexpected error occurred: {str(e)}", ephemeral=False)

    async def _audit_pilots(self, interaction: discord.Interaction):
        """Audits the pilots table structure and sample data."""
        await interaction.response.defer(ephemeral=False)
        
        try:
            structure_query = "DESCRIBE pilots"
            structure = await self.bot.db_manager.fetch_all(structure_query)
            
            count_query = "SELECT COUNT(*) as total FROM pilots"
            count_result = await self.bot.db_manager.fetch_one(count_query)
            total_count = count_result['total'] if count_result else 0
            
            sample_query = "SELECT id, callsign, name, ifc, ifuserid, discordid, status FROM pilots LIMIT 5"
            sample_data = await self.bot.db_manager.fetch_all(sample_query)
            
            audit_msg = "**🔍 PILOTS TABLE AUDIT**\n\n"
            audit_msg += f"📊 **Total Records:** {total_count}\n\n"
            
            audit_msg += "**🏗️ Structure:**\n```\n"
            for col in structure:
                field_name = col['Field']
                sensitive_fields = ['password', 'email', 'vanet_accesstoken', 'vanet_refreshtoken', 'vanet_expiry']
                if any(sensitive in field_name.lower() for sensitive in sensitive_fields):
                    audit_msg += f"{field_name}: {col['Type']} [SENSITIVE - EXCLUDED]\n"
                else:
                    audit_msg += f"{field_name}: {col['Type']}\n"
            audit_msg += "```\n\n"
            
            if sample_data:
                audit_msg += "**📋 Sample Data:**\n```\n"
                columns = list(sample_data[0].keys())
                audit_msg += " | ".join(columns) + "\n"
                audit_msg += "-" * 50 + "\n"
                
                for row in sample_data:
                    row_str = " | ".join(str(row[col])[:15] if row[col] else "NULL" for col in columns)
                    audit_msg += row_str + "\n"
                audit_msg += "```"
            else:
                audit_msg += "**📋 Sample Data:** No data found in pilots table."
            
            if len(audit_msg) > 2000:
                parts = [audit_msg[i:i+1900] for i in range(0, len(audit_msg), 1900)]
                for part in parts:
                    await interaction.followup.send(part, ephemeral=False)
            else:
                await interaction.followup.send(audit_msg, ephemeral=False)
            
        except Exception as e:
            await interaction.followup.send(f"An unexpected error occurred: {str(e)}", ephemeral=False)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ **Permission Denied**\nYou must have the `Administrator` permission to use this command.", ephemeral=False)

async def setup(bot: commands.Bot):
    await bot.add_cog(DatabaseAuditCog(bot))
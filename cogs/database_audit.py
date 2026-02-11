import discord
from discord import app_commands
from discord.ext import commands
import re
import io
import asyncio

class MultiplierFixApprovalView(discord.ui.View):
    def __init__(self, fix_data):
        super().__init__(timeout=300)
        self.fix_data = fix_data
        
    @discord.ui.button(label="✅ Approve & Apply Fixes", style=discord.ButtonStyle.green)
    async def approve_fixes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        fixed_count = 0
        for fix in self.fix_data:
            success = await interaction.client.event_transaction_model.add_transaction(
                fix['pilot_id'],
                fix['missing_amount'],
                f"Multiplier Fix: PIREP #{fix['pirep_id']} ({fix['multiplier']}x)"
            )
            if success:
                fixed_count += 1
        
        await interaction.followup.send(f"✅ Applied fixes for {fixed_count}/{len(self.fix_data)} pilots.", ephemeral=True)
        
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(view=self)
    
    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
    async def cancel_fixes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("❌ Multiplier fixes cancelled.", ephemeral=True)
        
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(view=self)

class CallsignModal(discord.ui.Modal, title='Enter Callsign'):
    def __init__(self):
        super().__init__()
        self.callsign_digits = None
        self.interaction = None

    callsign = discord.ui.TextInput(
        label='3-Digit Callsign',
        placeholder='Enter 3 digits (e.g., 123 for QRV123)',
        required=True,
        max_length=3
    )

    async def on_submit(self, interaction: discord.Interaction):
        self.interaction = interaction
        callsign_input = self.callsign.value.strip()
        
        if not callsign_input.isdigit() or len(callsign_input) != 3:
            await interaction.response.send_message("❌ Please enter exactly 3 digits.", ephemeral=True)
            return
        
        self.callsign_digits = callsign_input
        await interaction.response.defer(ephemeral=True)
        self.stop()

class FlightNumberModal(discord.ui.Modal, title='Enter Flight Number'):
    def __init__(self):
        super().__init__()
        self.flight_number_value = None
        self.interaction = None

    flight_number = discord.ui.TextInput(
        label='Flight Number',
        placeholder='Enter flight number (e.g., AA1234, BA123)',
        required=True,
        max_length=10
    )

    async def on_submit(self, interaction: discord.Interaction):
        self.interaction = interaction
        self.flight_number_value = self.flight_number.value.strip().upper()
        await interaction.response.defer(ephemeral=True)
        self.stop()

class DatabaseAuditCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="audit", description="Database audit operations")
    @app_commands.describe(action="What audit action to perform")
    @app_commands.choices(action=[
        app_commands.Choice(name="Check IFC Usernames Validity", value="check_ifc_usernames"),
        app_commands.Choice(name="Active Pilots Only", value="active_pilots"),
        app_commands.Choice(name="Pilots Table", value="pilots"),
        app_commands.Choice(name="2025 Year Report", value="year_2025_report"),
        app_commands.Choice(name="First Accepted PIREPs QRV001-019", value="first_accepted_pireps"),
        app_commands.Choice(name="OWD Route Lookup", value="owd_route_lookup"),
        app_commands.Choice(name="ROS Mission Progress", value="ros_mission_progress"),
        app_commands.Choice(name="Test AI-PDF Flow", value="test_ai_pdf_flow")
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def audit(self, interaction: discord.Interaction, action: str):
        if action == "check_ifc_usernames":
            await self._check_ifc_usernames_validity(interaction)
        elif action == "active_pilots":
            await self._audit_active_pilots(interaction)
        elif action == "pilots":
            await self._audit_pilots(interaction)
        elif action == "year_2025_report":
            await self._year_2025_report(interaction)
        elif action == "first_accepted_pireps":
            await self._first_accepted_pireps(interaction)
        elif action == "owd_route_lookup":
            await self._owd_route_lookup(interaction)
        elif action == "ros_mission_progress":
            await self._ros_mission_progress(interaction)
        elif action == "test_ai_pdf_flow":
            await self._test_ai_pdf_flow(interaction)

    async def _check_ifc_usernames_validity(self, interaction: discord.Interaction):
        """Check all active pilots' IFC usernames by fetching user stats from API."""
        await interaction.response.defer(ephemeral=False)
        
        print("\n" + "="*80)
        print("[AUDIT DEBUG] STARTING IFC USERNAME VALIDATION AUDIT")
        print("="*80)
        
        try:
            # 1. Fetch pilots from DB
            query = "SELECT id, callsign, name, discordid, ifc FROM pilots WHERE status = 1 AND ifc IS NOT NULL AND ifc != ''"
            print(f"[AUDIT DEBUG] Executing query: {query}")
            
            active_pilots = await self.bot.db_manager.fetch_all(query)
            print(f"[AUDIT DEBUG] Query returned {len(active_pilots) if active_pilots else 0} pilots")
            
            if not active_pilots:
                print("[AUDIT DEBUG] No active pilots with IFC usernames found - exiting")
                await interaction.followup.send("No active pilots with IFC usernames found.", ephemeral=False)
                return
            
            print(f"[AUDIT DEBUG] Found {len(active_pilots)} active pilots with IFC data")
            for i, pilot in enumerate(active_pilots[:5]):  # Show first 5 for debug
                print(f"[AUDIT DEBUG] Sample pilot {i+1}: ID={pilot['id']}, Name={pilot['name']}, Callsign={pilot['callsign']}, IFC={pilot['ifc']}")
            
            await interaction.followup.send(f"🔍 **Checking {len(active_pilots)} active pilots' IFC usernames...**\nProcessing in batches of 20.", ephemeral=False)
            
            batch_size = 20
            invalid_pilots = []
            updated_count = 0
            
            print(f"[AUDIT DEBUG] Processing in batches of {batch_size}")
            
            # 2. Process in batches
            for i in range(0, len(active_pilots), batch_size):
                batch = active_pilots[i:i + batch_size]
                batch_num = (i // batch_size) + 1
                total_batches = (len(active_pilots) + batch_size - 1) // batch_size
                
                print(f"\n[AUDIT DEBUG] ===== BATCH {batch_num}/{total_batches} =====")
                print(f"[AUDIT DEBUG] Batch contains {len(batch)} pilots")
                
                # Update status
                if batch_num % 1 == 0: # Notify every batch
                    await interaction.followup.send(f"📊 **Processing Batch {batch_num}/{total_batches}**...", ephemeral=False)
                
                batch_invalid = []
                
                for pilot_idx, pilot in enumerate(batch):
                    print(f"\n[AUDIT DEBUG] --- Processing pilot {pilot_idx+1}/{len(batch)} in batch {batch_num} ---")
                    print(f"[AUDIT DEBUG] Pilot ID: {pilot['id']}")
                    print(f"[AUDIT DEBUG] Pilot Name: {pilot['name']}")
                    print(f"[AUDIT DEBUG] Pilot Callsign: {pilot['callsign']}")
                    print(f"[AUDIT DEBUG] Discord ID: {pilot['discordid']}")
                    
                    # Small delay to prevent API Rate Limiting (429 Too Many Requests)
                    print(f"[AUDIT DEBUG] Sleeping 0.3s to prevent rate limiting...")
                    await asyncio.sleep(0.3) 
                    
                    ifc_url = pilot['ifc']
                    print(f"[AUDIT DEBUG] Raw IFC URL: '{ifc_url}'")
                    print(f"[AUDIT DEBUG] IFC URL type: {type(ifc_url)}")
                    print(f"[AUDIT DEBUG] IFC URL length: {len(ifc_url) if ifc_url else 0}")
                    
                    # IMPROVED REGEX:
                    # Handles: /u/username, /users/username, /u/username/summary, /u/username?preferences
                    regex_pattern = r'/(?:u|users)/([^/?#\s]+)'
                    print(f"[AUDIT DEBUG] Using regex pattern: {regex_pattern}")
                    
                    username_match = re.search(regex_pattern, ifc_url)
                    print(f"[AUDIT DEBUG] Regex match result: {username_match}")
                    
                    if not username_match:
                        print(f"[AUDIT DEBUG] No regex match found")
                        # Fallback: maybe they just entered the username directly without URL?
                        if "http" not in ifc_url and "/" not in ifc_url:
                            username = ifc_url.strip()
                            print(f"[AUDIT DEBUG] Using direct username fallback: '{username}'")
                        else:
                            print(f"[AUDIT DEBUG] Invalid URL format detected: '{ifc_url}'")
                            batch_invalid.append({
                                'name': pilot['name'],
                                'callsign': pilot['callsign'],
                                'discord_id': pilot['discordid'],
                                'reason': f'Invalid URL format: {ifc_url}'
                            })
                            print(f"[AUDIT DEBUG] Added to invalid list: Invalid URL format")
                            continue
                    else:
                        username = username_match.group(1)
                        print(f"[AUDIT DEBUG] Extracted username from regex: '{username}'")
                    
                    print(f"[AUDIT DEBUG] Final username to check: '{username}'")
                    
                    try:
                        print(f"[AUDIT DEBUG] Calling API manager for username: '{username}'")
                        print(f"[AUDIT DEBUG] API manager object: {self.bot.if_api_manager}")
                        print(f"[AUDIT DEBUG] API manager type: {type(self.bot.if_api_manager)}")
                        
                        # Call the API Manager
                        user_data = await self.bot.if_api_manager.get_user_by_ifc_username(username)
                        
                        print(f"[AUDIT DEBUG] API Response type: {type(user_data)}")
                        print(f"[AUDIT DEBUG] API Response: {user_data}")
                        
                        if user_data:
                            print(f"[AUDIT DEBUG] API Response keys: {list(user_data.keys()) if isinstance(user_data, dict) else 'Not a dict'}")
                            if isinstance(user_data, dict) and 'result' in user_data:
                                print(f"[AUDIT DEBUG] Result field: {user_data['result']}")
                                print(f"[AUDIT DEBUG] Result type: {type(user_data['result'])}")
                        
                        # Logic: If user_data is None or empty, the user doesn't exist (or API failed)
                        if not user_data or not user_data.get('result'):
                            print(f"[AUDIT DEBUG] Username '{username}' not found or API failed")
                            print(f"[AUDIT DEBUG] user_data is None: {user_data is None}")
                            print(f"[AUDIT DEBUG] user_data.get('result'): {user_data.get('result') if user_data else 'user_data is None'}")
                            
                            batch_invalid.append({
                                'name': pilot['name'],
                                'callsign': pilot['callsign'],
                                'discord_id': pilot['discordid'],
                                'reason': f'Username "{username}" not found'
                            })
                            print(f"[AUDIT DEBUG] Added to invalid list: Username not found")
                            continue
                        
                        user_id = user_data['result'].get('userId')
                        print(f"[AUDIT DEBUG] Extracted UserID: {user_id}")
                        print(f"[AUDIT DEBUG] UserID type: {type(user_id)}")
                        
                        if not user_id:
                            print(f"[AUDIT DEBUG] No UserID in API response for '{username}'")
                            batch_invalid.append({
                                'name': pilot['name'],
                                'callsign': pilot['callsign'],
                                'discord_id': pilot['discordid'],
                                'reason': 'No UserID in API response'
                            })
                            print(f"[AUDIT DEBUG] Added to invalid list: No UserID")
                            continue
                        
                        print(f"[AUDIT DEBUG] SUCCESS: Found UserID {user_id} for username '{username}'")
                        
                        # Update database with the found user ID
                        try:
                            print(f"[AUDIT DEBUG] Attempting to update database for username '{username}' with UserID '{user_id}'")
                            rows_updated = await self.bot.pilots_model.update_ifuserid_by_ifc_username(username, user_id)
                            print(f"[AUDIT DEBUG] Database update result: {rows_updated} rows affected")
                            
                            if rows_updated > 0:
                                updated_count += rows_updated
                                print(f"[AUDIT DEBUG] Successfully updated {rows_updated} DB records for '{username}'")
                                print(f"[AUDIT DEBUG] Total updated count now: {updated_count}")
                            else:
                                print(f"[AUDIT DEBUG] WARNING: No rows updated for '{username}' - this might indicate a problem")
                                
                        except Exception as db_e:
                            print(f"[AUDIT DEBUG] DATABASE ERROR for '{username}': {str(db_e)}")
                            print(f"[AUDIT DEBUG] DB Error type: {type(db_e)}")
                            import traceback
                            print(f"[AUDIT DEBUG] DB Error traceback: {traceback.format_exc()}")
                            await interaction.followup.send(f"🔧 **DEBUG:** DB Update Error for {username}: {str(db_e)}", ephemeral=False)
                    
                    except Exception as api_e:
                        print(f"[AUDIT DEBUG] API ERROR for '{username}': {str(api_e)}")
                        print(f"[AUDIT DEBUG] API Error type: {type(api_e)}")
                        import traceback
                        print(f"[AUDIT DEBUG] API Error traceback: {traceback.format_exc()}")
                        
                        await interaction.followup.send(f"🔧 **DEBUG:** API Error for {username}: {str(api_e)}", ephemeral=False)
                        batch_invalid.append({
                            'name': pilot['name'],
                            'callsign': pilot['callsign'],
                            'discord_id': pilot['discordid'],
                            'reason': f'API Error: {str(api_e)}'
                        })
                        print(f"[AUDIT DEBUG] Added to invalid list: API Error")
                
                invalid_pilots.extend(batch_invalid)
                print(f"\n[AUDIT DEBUG] ===== BATCH {batch_num} COMPLETED =====")
                print(f"[AUDIT DEBUG] Batch {batch_num} invalid count: {len(batch_invalid)}")
                print(f"[AUDIT DEBUG] Total invalid so far: {len(invalid_pilots)}")
                print(f"[AUDIT DEBUG] Total updated so far: {updated_count}")
                
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
            
            print(f"\n" + "="*80)
            print(f"[AUDIT DEBUG] FINAL AUDIT SUMMARY")
            print(f"[AUDIT DEBUG] Total pilots checked: {len(active_pilots)}")
            print(f"[AUDIT DEBUG] Valid usernames: {total_valid}")
            print(f"[AUDIT DEBUG] Invalid/Not found: {len(invalid_pilots)}")
            print(f"[AUDIT DEBUG] Database records updated: {updated_count}")
            print(f"[AUDIT DEBUG] Invalid pilots list: {invalid_pilots}")
            print("="*80)
            
            summary_msg = f"\n📋 **FINAL AUDIT SUMMARY**\n"
            summary_msg += f"✅ **Valid IFC Usernames:** {total_valid}\n"
            summary_msg += f"❌ **Invalid/Not Found:** {len(invalid_pilots)}\n"
            summary_msg += f"💾 **Database Records Updated:** {updated_count}\n"
            summary_msg += f"📊 **Total Checked:** {len(active_pilots)}"
            
            await interaction.followup.send(summary_msg, ephemeral=False)
            
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            
            print(f"\n[AUDIT DEBUG] CRITICAL ERROR OCCURRED:")
            print(f"[AUDIT DEBUG] Error: {str(e)}")
            print(f"[AUDIT DEBUG] Error type: {type(e)}")
            print(f"[AUDIT DEBUG] Full traceback:")
            print(error_trace)
            print("="*80)
            
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

    async def _year_2025_report(self, interaction: discord.Interaction):
        """Generate comprehensive 2025 year report with raw flight hours."""
        await interaction.response.defer(ephemeral=False)
        
        try:
            stats = await self.bot.pireps_model.get_2025_year_stats()
            
            report_msg = "📊 **2025 YEAR REPORT** 📊\n\n"
            
            # Top 5 Aircraft
            report_msg += "✈️ **TOP 5 AIRCRAFT (Raw Hours):**\n"
            for i, aircraft in enumerate(stats['top_aircraft'], 1):
                raw_hours = round(aircraft['raw_hours'] / 3600, 2)  # Convert seconds to hours
                report_msg += f"{i}. {aircraft['aircraft_name']} - {raw_hours}h ({aircraft['flight_count']} flights)\n"
            
            # Top 3 Routes
            report_msg += "\n🛫 **TOP 3 ROUTES:**\n"
            for i, route in enumerate(stats['top_routes'], 1):
                report_msg += f"{i}. {route['route']} - {route['flight_count']} flights\n"
            
            # Top 3 Airports
            report_msg += "\n🏢 **TOP 3 AIRPORTS:**\n"
            for i, airport in enumerate(stats['top_airports'], 1):
                report_msg += f"{i}. {airport['airport']} - {airport['total_traffic']} flights\n"
            
            # Totals
            if stats['totals']:
                total_raw_hours = round(stats['totals']['total_raw_hours'] / 3600, 2)
                report_msg += f"\n📈 **TOTALS:**\n"
                report_msg += f"Total Raw Flight Hours: {total_raw_hours}h\n"
                report_msg += f"Total Approved PIREPs: {stats['totals']['total_pireps']}"
            
            await interaction.followup.send(report_msg, ephemeral=False)
            
        except Exception as e:
            await interaction.followup.send(f"❌ Error generating 2025 report: {str(e)}", ephemeral=False)

    async def _first_accepted_pireps(self, interaction: discord.Interaction):
        """Shows first accepted PIREP for pilots QRV001-QRV019."""
        await interaction.response.defer(ephemeral=False)
        
        try:
            pireps = await self.bot.pireps_model.get_first_accepted_pireps_qrv001_to_qrv019()
            
            if not pireps:
                await interaction.followup.send("❌ No accepted PIREPs found for QRV001-QRV019.", ephemeral=False)
                return
            
            report_msg = "📋 **FIRST ACCEPTED PIREPs (QRV001-QRV019)**\n\n"
            
            for pirep in pireps:
                report_msg += f"**{pirep['callsign']}** ({pirep['pilot_name']})\n"
                report_msg += f"Flight: {pirep['flightnum']} | {pirep['departure']} → {pirep['arrival']}\n"
                report_msg += f"Date: {pirep['date']} | PIREP ID: {pirep['pirep_id']}\n\n"
            
            if len(report_msg) > 2000:
                parts = [report_msg[i:i+1900] for i in range(0, len(report_msg), 1900)]
                for part in parts:
                    await interaction.followup.send(part, ephemeral=False)
            else:
                await interaction.followup.send(report_msg, ephemeral=False)
                
        except Exception as e:
            await interaction.followup.send(f"❌ Error fetching first accepted PIREPs: {str(e)}", ephemeral=False)

    async def _owd_route_lookup(self, interaction: discord.Interaction):
        """Lookup OWD route data by flight number using modal input."""
        print("[OWD DEBUG] Starting OWD route lookup")
        
        try:
            modal = FlightNumberModal()
            print("[OWD DEBUG] Created FlightNumberModal")
            
            await interaction.response.send_modal(modal)
            print("[OWD DEBUG] Sent modal to user")
            
            await modal.wait()
            print(f"[OWD DEBUG] Modal completed, flight_number: {modal.flight_number_value}")
            
            if not modal.flight_number_value:
                print("[OWD DEBUG] No flight number provided")
                return
            
            print(f"[OWD DEBUG] Searching for flight number: '{modal.flight_number_value}'")
            print(f"[OWD DEBUG] Bot owd_route_model exists: {hasattr(self.bot, 'owd_route_model')}")
            
            if not hasattr(self.bot, 'owd_route_model') or self.bot.owd_route_model is None:
                print("[OWD DEBUG] ERROR: owd_route_model not found")
                await modal.interaction.followup.send("❌ OWD route model not initialized", ephemeral=False)
                return
            
            route_data = await self.bot.owd_route_model.find_route_by_flight_number(modal.flight_number_value)
            print(f"[OWD DEBUG] Route data result: {route_data}")
            
            if not route_data:
                print(f"[OWD DEBUG] No route found for flight number: {modal.flight_number_value}")
                await modal.interaction.followup.send(f"❌ No OWD route found for flight number: **{modal.flight_number_value}**", ephemeral=False)
                return
            
            print(f"[OWD DEBUG] Creating embed with route data")
            embed = discord.Embed(
                title=f"✈️ OWD Route: {route_data['flight_number']}",
                color=0x00ff00
            )
            
            embed.add_field(name="🛫 Departure", value=route_data['departure'], inline=True)
            embed.add_field(name="🛬 Arrival", value=route_data['arrival'], inline=True)
            embed.add_field(name="⏱️ Flight Time", value=route_data['flight_time'], inline=True)
            embed.add_field(name="✈️ Aircraft", value=route_data['aircraft'], inline=True)
            embed.add_field(name="🏢 Airline", value=route_data['airline'], inline=True)
            
            print(f"[OWD DEBUG] Sending embed response")
            await modal.interaction.followup.send(embed=embed, ephemeral=False)
            print(f"[OWD DEBUG] Successfully sent response")
            
        except Exception as e:
            print(f"[OWD DEBUG] ERROR in _owd_route_lookup: {str(e)}")
            print(f"[OWD DEBUG] Error type: {type(e)}")
            import traceback
            print(f"[OWD DEBUG] Full traceback: {traceback.format_exc()}")
            
            try:
                if 'modal' in locals() and modal.interaction:
                    await modal.interaction.followup.send(f"❌ Error looking up OWD route: {str(e)}", ephemeral=False)
                else:
                    await interaction.followup.send(f"❌ Error in OWD lookup: {str(e)}", ephemeral=False)
            except Exception as followup_error:
                print(f"[OWD DEBUG] Error sending followup: {followup_error}")

    async def _ros_mission_progress(self, interaction: discord.Interaction):
        """Check which pilots have completed ROS mission routes."""
        await interaction.response.defer(ephemeral=False)
        
        MISSION_ROUTES = {
            'ROS1A': ('VHHH', 'EGLL'), 'ROS1B': ('EGLL', 'VHHH'),
            'ROS2A': ('VHHH', 'WSSS'), 'ROS2B': ('WSSS', 'VHHH'),
            'ROS3A': ('VHHH', 'NZAA'), 'ROS3B': ('NZAA', 'VHHH'),
            'ROS4A': ('VHHH', 'WADD'), 'ROS4B': ('WADD', 'VHHH'),
            'ROS5A': ('VHHH', 'VABB'), 'ROS5B': ('VABB', 'VHHH'),
            'ROS6A': ('VHHH', 'OMDB'), 'ROS6B': ('OMDB', 'VHHH'),
            'ROS7A': ('VHHH', 'KJFK'), 'ROS7B': ('KJFK', 'VHHH')
        }
        
        try:
            query = """
                SELECT 
                    pi.id, pi.callsign, pi.name, pi.discordid,
                    p.flightnum, p.departure, p.arrival
                FROM pilots pi
                LEFT JOIN pireps p ON pi.id = p.pilotid 
                    AND p.status = 1 
                    AND p.flightnum LIKE 'ROS%'
                WHERE pi.status = 1
            """
            results = await self.bot.db_manager.fetch_all(query)
            
            pilot_progress = {}
            
            for row in results:
                pilot_id = row['id']
                if pilot_id not in pilot_progress:
                    pilot_progress[pilot_id] = {
                        'name': row['name'],
                        'callsign': row['callsign'],
                        'discord_id': row['discordid'],
                        'completed': set()
                    }
                
                if row['flightnum']:
                    flight = row['flightnum']
                    if flight in MISSION_ROUTES:
                        expected_dep, expected_arr = MISSION_ROUTES[flight]
                        if row['departure'] == expected_dep and row['arrival'] == expected_arr:
                            pilot_progress[pilot_id]['completed'].add(flight)
            
            completed_pilots = []
            incomplete_pilots = []
            
            for pilot_data in pilot_progress.values():
                count = len(pilot_data['completed'])
                if count == 14:
                    completed_pilots.append(pilot_data)
                elif count > 0:
                    pilot_data['count'] = count
                    incomplete_pilots.append(pilot_data)
            
            report_msg = "🎯 **ROS MISSION PROGRESS REPORT**\n\n"
            
            if completed_pilots:
                report_msg += f"✅ **COMPLETED ALL ROUTES ({len(completed_pilots)} pilots):**\n"
                for p in completed_pilots:
                    discord_mention = f"<@{p['discord_id']}>" if p['discord_id'] else "No Discord"
                    report_msg += f"• **{p['callsign']}** - {p['name']} {discord_mention}\n"
                report_msg += "\n"
            else:
                report_msg += "✅ **COMPLETED ALL ROUTES:** None yet\n\n"
            
            if incomplete_pilots:
                incomplete_pilots.sort(key=lambda x: x['count'], reverse=True)
                report_msg += f"📊 **IN PROGRESS ({len(incomplete_pilots)} pilots):**\n"
                for p in incomplete_pilots:
                    missing = [f for f in MISSION_ROUTES.keys() if f not in p['completed']]
                    discord_mention = f"<@{p['discord_id']}>" if p['discord_id'] else "No Discord"
                    report_msg += f"• **{p['callsign']}** ({p['count']}/14) - Missing: {', '.join(missing)} {discord_mention}\n"
            
            if len(report_msg) > 2000:
                parts = [report_msg[i:i+1900] for i in range(0, len(report_msg), 1900)]
                for part in parts:
                    await interaction.followup.send(part, ephemeral=False)
            else:
                await interaction.followup.send(report_msg, ephemeral=False)
                
        except Exception as e:
            await interaction.followup.send(f"❌ Error checking ROS mission progress: {str(e)}", ephemeral=False)

    async def _test_ai_pdf_flow(self, interaction: discord.Interaction):
        """Runs a test of the AI to PDF generation flow with heavy debugging."""
        await interaction.response.defer(ephemeral=True)
        
        print("\n" + "="*80)
        print("[AUDIT-PDF-TEST] STARTING AI-PDF FLOW TEST")
        print("="*80)
        
        try:
            # 1. Check for services
            print("[AUDIT-PDF-TEST] Checking for required services on bot object...")
            if not hasattr(self.bot, 'ai_service'):
                print("[AUDIT-PDF-TEST] ❌ self.bot.ai_service not found!")
                await interaction.followup.send("❌ `ai_service` is not available on the bot.", ephemeral=True)
                return
            if not hasattr(self.bot, 'pdf_service'):
                print("[AUDIT-PDF-TEST] ❌ self.bot.pdf_service not found!")
                await interaction.followup.send("❌ `pdf_service` is not available on the bot.", ephemeral=True)
                return
            if not hasattr(self.bot, 'flightdata'):
                print("[AUDIT-PDF-TEST] ❌ self.bot.flightdata not found!")
                await interaction.followup.send("❌ `flightdata` service is not available on the bot.", ephemeral=True)
                return
            
            print("[AUDIT-PDF-TEST] ✅ All services found.")
            await interaction.followup.send("✅ Services located. Preparing test data...", ephemeral=True)

            # 2. Prepare test data
            print("[AUDIT-PDF-TEST] Preparing test data...")
            flight_type = "amiri"
            aircraft_name = "A319"
            dep_icao = "OTHH"
            dest_icao = "EGLL"
            passengers = 20
            cargo = 1500
            deadline = "24 Hours"
            
            print(f"[AUDIT-PDF-TEST] Flight Type: {flight_type}, Aircraft: {aircraft_name}")
            print(f"[AUDIT-PDF-TEST] Route: {dep_icao} -> {dest_icao}")

            dep_data = self.bot.flightdata.get_airport_data(dep_icao)
            dest_data = self.bot.flightdata.get_airport_data(dest_icao)

            if not dep_data or not dest_data:
                print(f"[AUDIT-PDF-TEST] ❌ Failed to get airport data for {dep_icao} or {dest_icao}")
                await interaction.followup.send(f"❌ Failed to get airport data for {dep_icao} or {dest_icao}", ephemeral=True)
                return
            
            print("[AUDIT-PDF-TEST] ✅ Test data prepared.")
            await interaction.followup.send("✅ Test data prepared. Calling AI service...", ephemeral=True)

            # 3. Call AI Service
            print("[AUDIT-PDF-TEST] --- CALLING AI SERVICE ---")
            scenario_data = await self.bot.ai_service.generate_ai_scenario(
                aircraft_name, dep_data, dest_data, passengers, cargo, flight_type, deadline
            )
            print("[AUDIT-PDF-TEST] --- AI SERVICE CALL COMPLETE ---")

            # 4. Debug AI response
            print(f"[AUDIT-PDF-TEST] AI response type: {type(scenario_data)}")
            if isinstance(scenario_data, dict):
                print(f"[AUDIT-PDF-TEST] AI response keys: {list(scenario_data.keys())}")
                print(f"[AUDIT-PDF-TEST] AI response content: {scenario_data}")
                ai_content_present = all(k in scenario_data for k in ['dignitary_intro', 'mission_briefing', 'manifest_details'])
                print(f"[AUDIT-PDF-TEST] All expected AI keys present: {ai_content_present}")
                if not ai_content_present:
                     await interaction.followup.send("⚠️ AI service did not return all expected keys. Check logs for `[DEBUG]` messages from `ai_service.py`.", ephemeral=True)
                else:
                     await interaction.followup.send("✅ AI service returned expected data structure. Preparing PDF...", ephemeral=True)
            else:
                print(f"[AUDIT-PDF-TEST] ❌ AI response is not a dictionary. Response: {scenario_data}")
                await interaction.followup.send(f"❌ AI service returned an unexpected type: `{type(scenario_data)}`. Check logs.", ephemeral=True)
                return

            # 5. Prepare final data for PDF
            print("[AUDIT-PDF-TEST] Preparing final data for PDF service...")
            import random
            from datetime import datetime
            
            flight_data = {
                'flight_number': f"QRV{random.randint(100, 999)}",
                'aircraft_name': "Airbus A319 (ACJ)",
                'route': f"{dep_icao} to {dest_icao}",
                'passengers': passengers,
                'cargo': cargo,
                'fuel_stop_required': False,
                'current_date': datetime.now().strftime('%d %B %Y'),
                'deadline': deadline,
            }
            flight_data.update(scenario_data)
            
            print(f"[AUDIT-PDF-TEST] Final data keys for PDF: {list(flight_data.keys())}")
            
            # 6. Call PDF Service
            print("[AUDIT-PDF-TEST] --- CALLING PDF SERVICE ---")
            pilot_info = {'rank': 'Test Rank', 'callsign': 'QRV999'}
            pdf_output = self.bot.pdf_service.generate_flight_pdf(flight_data, flight_type, interaction.user, pilot_info)
            print("[AUDIT-PDF-TEST] --- PDF SERVICE CALL COMPLETE ---")

            # 7. Report result
            if pdf_output:
                print("[AUDIT-PDF-TEST] ✅ PDF generation returned bytes.")
                pdf_buffer = io.BytesIO(pdf_output)
                await interaction.followup.send(
                    "✅ **Test Complete: PDF Generated Successfully!**\nThis indicates the AI->PDF flow is working. The issue in production might be related to the specific context of the `flight_generator_pdf` command.",
                    file=discord.File(pdf_buffer, "ai_pdf_test.pdf"),
                    ephemeral=True
                )
            else:
                print("[AUDIT-PDF-TEST] ❌ PDF generation returned None.")
                await interaction.followup.send(
                    "❌ **Test Complete: PDF Generation FAILED.**\nThe `pdf_service` returned `None`. Check the bot's console logs for `[PDF DEBUG]` messages to see what went wrong inside the PDF generator.",
                    ephemeral=True
                )
            print("="*80)
            print("[AUDIT-PDF-TEST] END OF TEST")
            print("="*80 + "\n")

        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            print(f"[AUDIT-PDF-TEST] ❌ CRITICAL ERROR during test: {e}")
            print(error_trace)
            await interaction.followup.send(f"❌ A critical error occurred during the test: `{e}`\n```\n{error_trace[:1500]}\n```", ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ **Permission Denied**\nYou must have the `Administrator` permission to use this command.", ephemeral=False)

async def setup(bot: commands.Bot):
    await bot.add_cog(DatabaseAuditCog(bot))
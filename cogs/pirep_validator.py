import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
import re

def parse_api_datetime(date_string):
    """Parse API datetime string handling microseconds + Z format."""
    if date_string.endswith('Z'):
        date_string = date_string[:-1] + '+00:00'
    
    # Handle microseconds - pad to 6 digits or truncate if more than 6
    import re
    microsecond_pattern = r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d+)(\+\d{2}:\d{2}|Z)$'
    match = re.match(microsecond_pattern, date_string)
    if match:
        date_part, microseconds, timezone = match.groups()
        # Pad or truncate microseconds to exactly 6 digits
        microseconds = microseconds.ljust(6, '0')[:6]
        date_string = f"{date_part}.{microseconds}{timezone}"
    
    return datetime.fromisoformat(date_string)

if TYPE_CHECKING:
    from ..bot import MyBot

def format_flight_time(seconds: int) -> str:
    """Format seconds into HH:MM."""
    if seconds is None:
        return "N/A"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}"

def get_multiplier_text(pirep_time, api_time, pirep_multiplier):
    """Calculate and return multiplier information with time difference."""
    if api_time == 0:
        return "⚠️ **INVALID** - API flight time is 0"
    
    pirep_minutes = pirep_time // 60
    api_minutes = api_time // 60
    
    if api_minutes == 0:
        return "⚠️ **INVALID** - API flight time is 0"
    
    # Check for excessive multiplier first
    if pirep_multiplier > 3:
        return f"❌ **{pirep_multiplier}x** - Multiplier too high"
    
    # Calculate expected time with PIREP's multiplier
    expected_time_sec = api_time * pirep_multiplier
    expected_minutes = expected_time_sec // 60
    
    # Calculate difference
    time_diff_sec = pirep_time - expected_time_sec
    time_diff_minutes = abs(time_diff_sec) // 60
    
    # Format multiplier display
    if pirep_multiplier == 1:
        multiplier_display = "1x"
    elif pirep_multiplier == 1.5:
        multiplier_display = "1.5x"
    elif pirep_multiplier == 2:
        multiplier_display = "2x"
    elif pirep_multiplier == 3:
        multiplier_display = "3x"
    else:
        multiplier_display = f"{pirep_multiplier}x"
    
    # Format time difference
    if time_diff_sec > 300:  # More than 5 minutes difference
        return f"⚠️ **{multiplier_display}** (+{time_diff_minutes}min vs IF time)"
    elif time_diff_sec < -300:  # Less than 5 minutes difference
        return f"⚠️ **{multiplier_display}** (-{time_diff_minutes}min vs IF time)"
    else:
        return f"✅ **{multiplier_display}** - Accurate time"

class PirepPaginationView(discord.ui.View):
    def __init__(self, bot, pending_pireps, current_index=0, count_message=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.pending_pireps = pending_pireps
        self.current_index = current_index
        self.count_message = count_message
        self.update_buttons()
    
    def update_buttons(self):
        self.previous_button.disabled = self.current_index == 0
        self.next_button.disabled = self.current_index >= len(self.pending_pireps) - 1
    
    @discord.ui.button(label="⬅️ Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not any("staff" in role.name.lower() for role in interaction.user.roles):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
        
        await interaction.response.defer()
        
        if self.current_index > 0:
            self.current_index -= 1
            validator = self.bot.get_cog('PirepValidator')
            embed = await validator.validate_single_pirep(self.pending_pireps[self.current_index])
            self.update_buttons()
            await interaction.message.edit(embed=embed, view=self)
    
    @discord.ui.button(label="➡️ Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not any("staff" in role.name.lower() for role in interaction.user.roles):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
        
        await interaction.response.defer()
        
        if self.current_index < len(self.pending_pireps) - 1:
            self.current_index += 1
            validator = self.bot.get_cog('PirepValidator')
            embed = await validator.validate_single_pirep(self.pending_pireps[self.current_index])
            self.update_buttons()
            await interaction.message.edit(embed=embed, view=self)
    
    @discord.ui.button(label="🔄 Refresh List", style=discord.ButtonStyle.primary)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not any("staff" in role.name.lower() for role in interaction.user.roles):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
        
        await interaction.response.defer()
        
        self.pending_pireps = await self.bot.pireps_model.get_pending_pireps()
        
        if not self.pending_pireps:
            await interaction.followup.send("✅ No more pending PIREPs!", ephemeral=True)
            return
        
        if self.count_message:
            try:
                await self.count_message.edit(content=f"📋 **{len(self.pending_pireps)} PIREPs pending validation**")
            except:
                pass
        
        self.current_index = 0
        validator = self.bot.get_cog('PirepValidator')
        embed = await validator.validate_single_pirep(self.pending_pireps[0])
        self.update_buttons()
        await interaction.message.edit(embed=embed, view=self)
    
    @discord.ui.button(label="📅 Flight History", style=discord.ButtonStyle.success, row=1)
    async def flight_history_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not any("staff" in role.name.lower() for role in interaction.user.roles):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        
        validator = self.bot.get_cog('PirepValidator')
        history_embed = await validator.get_flight_history(self.pending_pireps[self.current_index])
        await interaction.followup.send(embed=history_embed, ephemeral=True)
    
    @discord.ui.button(label="🐛 Debug", style=discord.ButtonStyle.danger, row=1)
    async def debug_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not any("staff" in role.name.lower() for role in interaction.user.roles):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        
        validator = self.bot.get_cog('PirepValidator')
        debug_messages = await validator.get_debug_info(self.pending_pireps[self.current_index])
        
        for msg in debug_messages:
            await interaction.followup.send(msg, ephemeral=True)

class PirepValidator(commands.Cog):
    def __init__(self, bot: 'MyBot'):
        self.bot = bot
        # REPLACE THIS WITH YOUR CHANNEL ID (Integer, not string)
        self.WATCH_CHANNEL_ID = 1459564652945084578

    # ------------------------------------------------------------------
    # AUTOMATIC WEBHOOK LISTENER
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # 1. Check Channel
        if message.channel.id != self.WATCH_CHANNEL_ID:
            return

        # 2. Check if it's a Webhook
        if not message.webhook_id:
            return

        # 3. Check for Embeds and Title
        if not message.embeds:
            return
        
        embed = message.embeds[0]
        if not embed.title or "New PIREP Filed" not in embed.title:
            return

        # 4. Parse Data using Regex
        description = embed.description
        
        # Regex to find content inside parentheses for Pilot ID
        pilot_match = re.search(r"Pilot: .* \((.*)\)", description)
        # Regex to find Flight Number
        flight_match = re.search(r"Flight Number: (.*)", description)

        if not pilot_match or not flight_match:
            print(f"Error: Could not parse PIREP Webhook. Desc: {description[:50]}...")
            return

        pilot_id_str = pilot_match.group(1).strip()
        flight_num_str = flight_match.group(1).strip()

        # 5. Create Thread immediately
        try:
            thread = await message.create_thread(
                name=f"Validating {flight_num_str} ({pilot_id_str})",
                auto_archive_duration=60
            )
        except Exception as e:
            print(f"Could not create thread: {e}")
            return

        # 6. Find the full DB object
        target_pirep = await self.find_pirep_from_webhook(pilot_id_str, flight_num_str)

        if not target_pirep:
            await thread.send(
                f"⚠️ **Could not find PIREP in database.**\n"
                f"Pilot: `{pilot_id_str}` | Flight: `{flight_num_str}`\n"
                f"The database might be slow to update. Try running `/validate_pireps` manually in a moment."
            )
            return

        # 7. Run Validation
        await thread.send("🔍 **Analyzing flight data...**")
        
        try:
            report_embed = await self.validate_single_pirep(target_pirep)
            await thread.send(embed=report_embed)
        except Exception as e:
            await thread.send(f"❌ **Error during validation:** {str(e)}")

    async def find_pirep_from_webhook(self, pilot_id_str, flight_num_str):
        """
        Helper to find the specific PIREP dictionary from the pending list
        based on Webhook data.
        """
        # Give the DB a second to sync if the webhook was instant
        import asyncio
        await asyncio.sleep(2) 

        pending_pireps = await self.bot.pireps_model.get_pending_pireps()
        
        if not pending_pireps:
            return None

        # Iterate and find match
        for pirep in pending_pireps:
            # Check if the ID from webhook is inside the DB pilotid (or equal)
            db_pilot_id = str(pirep['pilotid'])
            db_flight_num = str(pirep.get('flightnum', ''))

            # Check Pilot ID Match (Flexible)
            id_match = (pilot_id_str == db_pilot_id) or (db_pilot_id in pilot_id_str)
            
            # Check Flight Number Match
            flight_match = (flight_num_str == db_flight_num)

            # If both match, this is our guy
            if id_match and flight_match:
                return pirep
        
        return None

    # ------------------------------------------------------------------
    # EXISTING CODE BELOW
    # ------------------------------------------------------------------

    async def resolve_livery_name(self, aircraft_id: str, livery_id: str) -> str:
        """Resolve livery name from cache or API."""
        if livery_id in self.bot.livery_cache:
            return self.bot.livery_cache[livery_id]

        if aircraft_id:
            try:
                response = await self.bot.if_api_manager.get_aircraft_liveries(aircraft_id)
                if response and response.get('result'):
                    for livery in response['result']:
                        self.bot.livery_cache[livery['id']] = livery['liveryName']
                    return self.bot.livery_cache.get(livery_id, "Unknown Livery")
            except:
                pass

        return "Unknown Livery"
    
    def extract_ifc_username(self, ifc_url):
        """Extract IFC username from URL."""
        if not ifc_url:
            return "N/A"
        username_match = re.search(r'/u/([^/]+)', ifc_url)
        return username_match.group(1) if username_match else "N/A"
    
    async def resolve_ifuserid(self, pirep):
        """Resolve IF User ID from PIREP data."""
        ifuserid = pirep.get('ifuserid')
        if ifuserid and ifuserid != '':
            return ifuserid
        
        ifc_url = pirep.get('ifc')
        if not ifc_url:
            return None
        
        username_match = re.search(r'/u/([^/]+)', ifc_url)
        if not username_match:
            return None
        
        try:
            user_data = await self.bot.if_api_manager.get_user_by_ifc_username(username_match.group(1))
            if user_data and user_data.get('result'):
                ifuserid = user_data['result'].get('userId')
                if ifuserid:
                    await self.bot.pilots_model.update_ifuserid_by_ifc_username(username_match.group(1), ifuserid)
                return ifuserid
        except:
            pass
        
        return None

    async def validate_single_pirep(self, pirep):
        """Validate a single PIREP and return the result embed."""
        pilot_info = await self.bot.pilots_model.get_pilot_by_id(pirep['pilotid'])
        pilot_display = pirep['pilot_name']
        if pilot_info:
            if pilot_info.get('discordid'):
                pilot_display = f"{pilot_info['callsign']} | <@{str(pilot_info['discordid'])}>"
            elif pilot_info.get('callsign'):
                pilot_display = f"{pilot_info['callsign']} | {pirep['pilot_name']}"
        
        ifuserid = await self.resolve_ifuserid(pirep)
        
        if not ifuserid:
            return discord.Embed(
                title=f"# {pirep['departure']} - {pirep['arrival']} #",
                description=f"**Flight Time:** {pirep['formatted_flighttime']}\n**Pilot:** {pirep['pilot_name']}",
                color=discord.Color.orange()
            ).add_field(name="⚠️ VALIDATION SKIPPED", value="Could not resolve Infinite Flight User ID. Manual review required.", inline=False) or discord.Embed()

        try:
            user_flights_data = await self.bot.if_api_manager.get_user_flights(ifuserid, hours=72)
        except:
            user_flights_data = None
        
        if not user_flights_data or not user_flights_data.get('result'):
            return discord.Embed(
                title=f"# {pirep['departure']} - {pirep['arrival']} #",
                description=f"**Flight Time:** {pirep['formatted_flighttime']}\n**Pilot:** {pirep['pilot_name']}",
                color=discord.Color.orange()
            ).add_field(name="⚠️ API LIMITATION", value="Flight validation API endpoint not available. Manual review required.", inline=False) or discord.Embed()
        
        result_data = user_flights_data['result']
        user_flights = result_data.get('data', []) if isinstance(result_data, dict) else result_data

        pirep_datetime = pirep['date'] if hasattr(pirep['date'], 'date') else datetime.combine(pirep['date'], datetime.min.time())
        
        matching_flight = None
        for flight in user_flights:
            if isinstance(flight, dict) and flight.get('originAirport') == pirep['departure'] and flight.get('destinationAirport') == pirep['arrival']:
                try:
                    flight_date = parse_api_datetime(flight['created'])
                    if flight_date.tzinfo:
                        flight_date = flight_date.replace(tzinfo=None)
                    if abs(flight_date - pirep_datetime) < timedelta(days=3):
                        matching_flight = flight
                        break
                except:
                    continue

        try:
            route_valid = await self.check_route_database(pirep['departure'], pirep['arrival'], pirep.get('flightnum'))
            route_exists = await self.check_route_exists(pirep['departure'], pirep['arrival'])
        except:
            route_valid = False
            route_exists = False

        if not matching_flight:
            ifc_username = self.extract_ifc_username(pirep.get('ifc'))
            embed = discord.Embed(
                title=f"# {pirep['departure']} - {pirep['arrival']} #",
                description=f"👤 **Pilot:** {pilot_display}\n💬 **IFC:** {ifc_username}\n✈️ **Flight:** {pirep.get('flightnum', 'N/A')}",
                color=discord.Color.red()
            )
            
            analysis_details = [
                f"**Looking for:** {pirep['departure']} → {pirep['arrival']}",
                f"**Aircraft:** {pirep['aircraft_name']}",
                f"**Date:** {pirep['date']}",
                f"**Flight Time:** {pirep['formatted_flighttime']}"
            ]
            
            embed.add_field(name="❌ MATCH NOT FOUND - DETAILED ANALYSIS", value="\n".join(analysis_details), inline=False)
            
            recent_flights = []
            for flight in user_flights[:5]:
                if isinstance(flight, dict):
                    livery_name = await self.resolve_livery_name(flight.get('aircraftId'), flight.get('liveryId'))
                    aircraft_name = self.bot.aircraft_name_map.get(flight.get('aircraftId'), "Unknown Aircraft")
                    landings = flight.get('landingCount', 0) if flight.get('landingCount') is not None else 0
                    try:
                        flight_date = parse_api_datetime(flight['created'])
                        if flight_date.tzinfo is not None:
                            flight_date = flight_date.replace(tzinfo=None)
                        date_str = flight_date.strftime('%m/%d %H:%M')
                    except:
                        date_str = 'Unknown'
                    time_str = format_flight_time(int(flight.get('totalTime', 0) * 60))
                    recent_flights.append(f"`{date_str}` {flight.get('originAirport', 'N/A')} → {flight.get('destinationAirport', 'N/A')} ({time_str})\n   📡 **{flight.get('callsign', 'N/A')}** • 🛬 {landings}\n   ✈️ {aircraft_name} • 🎨 {livery_name}")
            
            if recent_flights:
                embed.add_field(name="📋 Recent Flights (Last 3 Days)", value="\n".join(recent_flights), inline=False)
            
            return embed

        landings = matching_flight.get('landingCount') if matching_flight.get('landingCount') is not None else 'N/A'
        route_match = f"{pirep['departure']} → {pirep['arrival']}" == f"{matching_flight['originAirport']} → {matching_flight['destinationAirport']}"
        aircraft_pirep = pirep['aircraft_name']
        aircraft_api_name = self.bot.aircraft_name_map.get(matching_flight.get('aircraftId'), "Unknown Aircraft")
        aircraft_match = aircraft_pirep == aircraft_api_name
        
        time_pirep_sec = pirep.get('flighttime', 0)
        time_api_sec = int(matching_flight.get('totalTime', 0) * 60)
        pirep_multiplier = pirep.get('multi', 1)
        multiplier_text = get_multiplier_text(time_pirep_sec, time_api_sec, pirep_multiplier)
        
        livery_name = await self.resolve_livery_name(matching_flight.get('aircraftId'), matching_flight.get('liveryId'))
        
        try:
            flight_date = parse_api_datetime(matching_flight['created'])
            if flight_date.tzinfo is not None:
                flight_date = flight_date.replace(tzinfo=None)
            date_str = flight_date.strftime('%d %b %Y %H:%M Z')
        except Exception as e:
            print(f"Error parsing date: {e}")
            date_str = "Unknown"
        
        issues = []
        if not route_match:
            issues.append("Route mismatch")
        if not aircraft_match:
            issues.append("Aircraft mismatch")
        if multiplier_text.startswith('❌'):
            issues.append("Invalid time")
        if not route_exists:
            issues.append("Route not in database")
        elif not route_valid:
            issues.append("Flight number not valid for route")
        
        multiplier_used = pirep_multiplier > 1
        high_multiplier = pirep_multiplier > 3
        
        # Check if time difference is significant (more than 5 minutes off)
        expected_time = time_api_sec * pirep_multiplier
        time_diff = abs(time_pirep_sec - expected_time)
        significant_time_error = time_diff > 300  # 5 minutes
        
        if high_multiplier:
            issues.append("Multiplier higher than 3x")
        if significant_time_error:
            issues.append("Significant time discrepancy")
        
        icon_aircraft = "✅" if aircraft_match else "❌"
        icon_time = "❌" if ("INVALID" in multiplier_text or "❌" in multiplier_text or high_multiplier or significant_time_error) else "✅"
        
        overall_valid = len(issues) == 0
        
        if overall_valid and not multiplier_used:
            embed_color = discord.Color.green()
            status_text = "✅ PIREP APPROVED"
        elif overall_valid and multiplier_used:
            embed_color = discord.Color.gold()
            status_text = "✅ PIREP VALID (MULTIPLIER USED)"
        else:
            embed_color = discord.Color.red()
            status_text = "🛑 REVIEW REQUIRED"
        
        if route_exists and route_valid:
            flight_num_status = "✅ Valid"
        elif route_exists:
            flight_num_status = "⚠️ Route exists, flight # invalid"
        else:
            flight_num_status = "⚠️ Route not in database"
        ifc_username = self.extract_ifc_username(pirep.get('ifc'))
        
        embed = discord.Embed(
            title=f"# {pirep['departure']} - {pirep['arrival']} #",
            description=f"**Flight:** {pirep.get('flightnum', 'N/A')} ({flight_num_status})",
            color=embed_color
        )
        
        embed.add_field(
            name="📄 Pilot Claim",
            value=f"👤 **Pilot:** {pilot_display}\n💬 **IFC:** {ifc_username}\n✈️ **Aircraft:** {aircraft_pirep}\n⏱️ **Filed Time:** {pirep['formatted_flighttime']}",
            inline=True
        )
        
        embed.add_field(
            name="☁️ Infinite Flight Log",
            value=f"📡 **Callsign:** {matching_flight.get('callsign', 'N/A')}\n🛩️ **Aircraft:** {aircraft_api_name} {icon_aircraft}\n⏱️ **Actual:** {format_flight_time(time_api_sec)} ({multiplier_text}) {icon_time}",
            inline=True
        )
        
        embed.add_field(
            name="📊 Flight Performance",
            value=f"🛬 **Landings:** {landings}\n⚠️ **Violations:** {len(matching_flight.get('violations', []))}\n🌍 **Server:** {matching_flight.get('server', 'Unknown')}\n📅 **Date:** {date_str}\n🎨 **Livery:** {livery_name}",
            inline=False
        )
        
        result_text = f"{status_text}\n**Issues:** {', '.join(issues)}" if not overall_valid else status_text
        embed.add_field(name="🎯 Result", value=result_text, inline=False)
        embed.set_footer(text=f"PIREP ID: {pirep['pirep_id']} | Validated via Public API v2")
        
        return embed

    async def get_debug_info(self, pirep):
        """Get debug information for troubleshooting."""
        ifuserid = await self.resolve_ifuserid(pirep)
        
        if not ifuserid:
            return ["No IF User ID found"]
        
        try:
            user_flights_data = await self.bot.if_api_manager.get_user_flights(ifuserid, hours=72)
        except Exception as e:
            return [f"API Error: {e}"]
        
        if not user_flights_data or not user_flights_data.get('result'):
            return ["No flight data from API"]
        
        result_data = user_flights_data['result']
        user_flights = result_data.get('data', []) if isinstance(result_data, dict) else result_data
        
        pirep_datetime = pirep['date'] if hasattr(pirep['date'], 'date') else datetime.combine(pirep['date'], datetime.min.time())
        
        debug_info = []
        debug_info.append(f"**PIREP Date Raw:** {pirep['date']} ({type(pirep['date'])})")
        debug_info.append(f"**PIREP DateTime:** {pirep_datetime}")
        debug_info.append(f"**Looking for:** {pirep['departure']} → {pirep['arrival']}")
        debug_info.append(f"**Time Window:** ±3 days")
        debug_info.append("")
        
        for idx, flight in enumerate(user_flights[:3]):
            if isinstance(flight, dict):
                origin = flight.get('originAirport', 'N/A')
                dest = flight.get('destinationAirport', 'N/A')
                created_raw = flight.get('created', 'MISSING')
                
                debug_info.append(f"**Flight {idx+1}:** {origin} → {dest}")
                debug_info.append(f"  Raw: {created_raw}")
                
                route_match = (origin == pirep['departure'] and dest == pirep['arrival'])
                debug_info.append(f"  Route Match: {route_match}")
                
                if route_match:
                    try:
                        flight_date = parse_api_datetime(created_raw)
                        debug_info.append(f"  Parsed: {flight_date}")
                        if flight_date.tzinfo:
                            flight_date = flight_date.replace(tzinfo=None)
                            debug_info.append(f"  No TZ: {flight_date}")
                        
                        time_diff = abs(flight_date - pirep_datetime)
                        debug_info.append(f"  Diff: {time_diff}")
                        debug_info.append(f"  Match: {time_diff < timedelta(days=3)}")
                    except Exception as e:
                        debug_info.append(f"  Error: {e}")
                debug_info.append("")
        
        return ["\n".join(debug_info)]
    
    async def get_flight_history(self, pirep):
        """Get flight history for the pilot."""
        ifuserid = await self.resolve_ifuserid(pirep)
        
        if not ifuserid:
            return discord.Embed(
                title="❌ Flight History Unavailable",
                description="No Infinite Flight User ID found for this pilot.",
                color=discord.Color.red()
            )
        
        try:
            user_flights_data = await self.bot.if_api_manager.get_user_flights(ifuserid, hours=72)
        except:
            return discord.Embed(
                title="⚠️ Flight History Unavailable",
                description="Could not fetch flight data from API.",
                color=discord.Color.orange()
            )
        
        if not user_flights_data or not user_flights_data.get('result'):
            return discord.Embed(
                title="⚠️ Flight History Unavailable",
                description="Could not fetch flight data from API.",
                color=discord.Color.orange()
            )
        
        result_data = user_flights_data['result']
        user_flights = result_data.get('data', []) if isinstance(result_data, dict) else result_data
        expert_flights = [f for f in user_flights if isinstance(f, dict) and f.get('server', '').lower() == 'expert']
        
        pirep_date = pirep['date'] if hasattr(pirep['date'], 'date') else datetime.combine(pirep['date'], datetime.min.time())
        
        past_flights = []
        future_flights = []
        
        for flight in expert_flights:
            try:
                flight_date = parse_api_datetime(flight['created'])
                if flight_date.tzinfo:
                    flight_date = flight_date.replace(tzinfo=None)
                
                if flight_date <= pirep_date:
                    past_flights.append((flight, flight_date))
                else:
                    future_flights.append((flight, flight_date))
            except:
                continue
        
        past_flights.sort(key=lambda x: x[1], reverse=True)
        future_flights.sort(key=lambda x: x[1])
        
        embed = discord.Embed(
            title=f"📅 Flight History - {pirep['pilot_name']}",
            description=f"Expert Server flights around PIREP submission date: **{pirep_date.strftime('%d %b %Y')}**",
            color=discord.Color.blue()
        )
        
        if past_flights:
            past_text = []
            for f, fd in past_flights[:10]:
                aircraft_name = self.bot.aircraft_name_map.get(f.get('aircraftId'), "Unknown Aircraft")
                livery_name = await self.resolve_livery_name(f.get('aircraftId'), f.get('liveryId'))
                landings = f.get('landingCount', 0) if f.get('landingCount') is not None else 0
                past_text.append(f"`{fd.strftime('%d %b %H:%M')}` **{f.get('originAirport', 'N/A')}** → **{f.get('destinationAirport', 'N/A')}** ({format_flight_time(int(f.get('totalTime', 0) * 60))})\n   📡 **{f.get('callsign', 'N/A')}** • 🛬 {landings}\n   ✈️ {aircraft_name} • 🎨 {livery_name}")
            embed.add_field(name="⏪ Past Flights (Last 3 Days)", value="\n".join(past_text) if past_text else "No flights", inline=False)
        else:
            embed.add_field(name="⏪ Past Flights (Last 3 Days)", value="No Expert server flights found", inline=False)
        
        if future_flights:
            future_text = []
            for f, fd in future_flights[:10]:
                aircraft_name = self.bot.aircraft_name_map.get(f.get('aircraftId'), "Unknown Aircraft")
                livery_name = await self.resolve_livery_name(f.get('aircraftId'), f.get('liveryId'))
                landings = f.get('landingCount', 0) if f.get('landingCount') is not None else 0
                future_text.append(f"`{fd.strftime('%d %b %H:%M')}` **{f.get('originAirport', 'N/A')}** → **{f.get('destinationAirport', 'N/A')}** ({format_flight_time(int(f.get('totalTime', 0) * 60))})\n   📡 **{f.get('callsign', 'N/A')}** • 🛬 {landings}\n   ✈️ {aircraft_name} • 🎨 {livery_name}")
            embed.add_field(name="⏩ Future Flights (After Submission)", value="\n".join(future_text) if future_text else "No flights", inline=False)
        else:
            embed.add_field(name="⏩ Future Flights (After Submission)", value="No future flights found", inline=False)
        
        embed.set_footer(text=f"PIREP ID: {pirep['pirep_id']} | Total Expert Flights: {len(expert_flights)}")
        return embed
    
    async def check_route_database(self, departure, arrival, flight_num):
        """Check if the flight number exists in the routes database."""
        if not flight_num:
            return False
        
        try:
            route_data = await self.bot.routes_model.find_route_by_icao(departure, arrival)
            if route_data:
                return self.validate_flight_number(flight_num, route_data['fltnum'])
            return False
        except:
            return False
    
    async def check_route_exists(self, departure, arrival):
        """Check if the route exists in the database."""
        try:
            route_data = await self.bot.routes_model.find_route_by_icao(departure, arrival)
            return route_data is not None
        except:
            return False
    
    def validate_flight_number(self, pilot_flight_num, db_flight_nums):
        """Validate if pilot's flight number is allowed."""
        if not pilot_flight_num or not db_flight_nums:
            return False
        
        allowed_flights = [f.strip() for f in db_flight_nums.split(',')]
        pilot_flights = [f.strip() for f in pilot_flight_num.split(',')]
        
        return all(pf in allowed_flights for pf in pilot_flights)

    @app_commands.command(name="validate_pireps", description="Validate pending PIREPs one at a time.")
    async def validate_pireps(self, interaction: discord.Interaction):
        if not any("staff" in role.name.lower() for role in interaction.user.roles):
            return await interaction.response.send_message("You must have a role containing 'staff' to use this command.", ephemeral=True)
            
        await interaction.response.defer(ephemeral=False)
        
        pending_pireps = await self.bot.pireps_model.get_pending_pireps()

        if not pending_pireps:
            return await interaction.followup.send("There are no pending PIREPs to validate.", ephemeral=False)
        
        count_message = await interaction.followup.send(f"📋 **{len(pending_pireps)} PIREPs pending validation**", ephemeral=False)
        embed = await self.validate_single_pirep(pending_pireps[0])
        view = PirepPaginationView(self.bot, pending_pireps, 0, count_message)
        await interaction.followup.send(embed=embed, view=view, ephemeral=False)

async def setup(bot: 'MyBot'):
    await bot.add_cog(PirepValidator(bot))

import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
import re

if TYPE_CHECKING:
    from ..bot import MyBot

def format_flight_time(seconds: int) -> str:
    """Format seconds into HH:MM."""
    if seconds is None:
        return "N/A"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}"

def get_multiplier_text(pirep_time, api_time):
    """Calculate and return multiplier information."""
    if api_time == 0:
        return "⚠️ **INVALID** - API flight time is 0"
    
    pirep_minutes = pirep_time // 60
    api_minutes = api_time // 60
    
    if api_minutes == 0:
        return "⚠️ **INVALID** - API flight time is 0"
    
    multiplier = pirep_minutes / api_minutes
    
    if multiplier < 1:
        return "❌ **INVALID** - PIREP time is less than actual flight time"
    elif multiplier <= 1.1:
        return "✅ **1x** - Normal speed"
    elif multiplier <= 1.7:
        return "✅ **1.5x** - 1.5x multiplier detected"
    elif multiplier <= 2.3:
        return "✅ **2x** - 2x multiplier detected"
    elif multiplier <= 3.3:
        return "✅ **3x** - 3x multiplier detected"
    else:
        return f"⚠️ **{multiplier:.1f}x** - Unusually high multiplier"

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
                    flight_date = datetime.fromisoformat(flight['created'])
                    if flight_date.tzinfo:
                        flight_date = flight_date.replace(tzinfo=None)
                    if abs(flight_date - pirep_datetime) < timedelta(days=2):
                        matching_flight = flight
                        break
                except:
                    continue

        try:
            route_valid = await self.check_route_database(pirep['departure'], pirep['arrival'], pirep.get('flightnum'))
        except:
            route_valid = False

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
                    try:
                        flight_date = datetime.fromisoformat(flight['created'])
                        if flight_date.tzinfo is not None:
                            flight_date = flight_date.replace(tzinfo=None)
                        date_str = flight_date.strftime('%m/%d %H:%M')
                    except:
                        date_str = 'Unknown'
                    time_str = format_flight_time(int(flight.get('totalTime', 0) * 60))
                    recent_flights.append(f"`{date_str}` {flight.get('originAirport', 'N/A')} → {flight.get('destinationAirport', 'N/A')} ({time_str})\n   **{flight.get('callsign', 'N/A')}** • {livery_name}")
            
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
        multiplier_text = get_multiplier_text(time_pirep_sec, time_api_sec)
        
        livery_name = await self.resolve_livery_name(matching_flight.get('aircraftId'), matching_flight.get('liveryId'))
        
        try:
            flight_date = datetime.fromisoformat(matching_flight['created'])
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
        if not route_valid:
            issues.append("Flight number not in route database")
        
        multiplier_used = False
        high_multiplier = False
        time_pirep_minutes = time_pirep_sec // 60
        time_api_minutes = time_api_sec // 60
        if time_api_minutes > 0:
            actual_multiplier = time_pirep_minutes / time_api_minutes
            if actual_multiplier > 1.1:
                multiplier_used = True
            if actual_multiplier > 3.3:
                high_multiplier = True
                issues.append("Multiplier higher than 3x")
        
        icon_aircraft = "✅" if aircraft_match else "❌"
        icon_time = "❌" if ("INVALID" in multiplier_text or "❌" in multiplier_text or high_multiplier) else "✅"
        
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
        
        flight_num_status = "✅ Valid" if route_valid else "⚠️ Not in CC"
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
        messages = []
        
        # Part 1: PIREP Info
        part1 = ["```", "🔍 PIREP VALIDATION DEBUG", "="*60]
        part1.append(f"\n📋 PIREP DATA:")
        part1.append(f"  ID: {pirep.get('pirep_id')}")
        part1.append(f"  Pilot: {pirep['pilot_name']}")
        part1.append(f"  Route: {pirep['departure']} → {pirep['arrival']}")
        part1.append(f"  Aircraft: {pirep['aircraft_name']}")
        part1.append(f"  Date (raw): {pirep['date']}")
        part1.append(f"  Date type: {type(pirep['date'])}")
        
        pirep_datetime = pirep['date'] if hasattr(pirep['date'], 'date') else datetime.combine(pirep['date'], datetime.min.time())
        part1.append(f"  Date (parsed): {pirep_datetime}")
        part1.append(f"  Flight Time: {pirep['formatted_flighttime']} ({pirep.get('flighttime')}s)")
        part1.append(f"  Flight #: {pirep.get('flightnum', 'N/A')}")
        
        ifuserid = await self.resolve_ifuserid(pirep)
        part1.append(f"\n🆔 IF User ID: {ifuserid}")
        
        if not ifuserid:
            part1.append("\n❌ No IF User ID - Cannot validate")
            part1.append("```")
            messages.append("\n".join(part1))
            return messages
        
        try:
            user_flights_data = await self.bot.if_api_manager.get_user_flights(ifuserid, hours=72)
            part1.append(f"\n✅ API Response received")
        except Exception as e:
            part1.append(f"\n❌ API Error: {e}")
            part1.append("```")
            messages.append("\n".join(part1))
            return messages
        
        if not user_flights_data or not user_flights_data.get('result'):
            part1.append("\n❌ No flight data from API")
            part1.append("```")
            messages.append("\n".join(part1))
            return messages
        
        result_data = user_flights_data['result']
        user_flights = result_data.get('data', []) if isinstance(result_data, dict) else result_data
        
        part1.append(f"\n✈️ Total flights: {len(user_flights)}")
        part1.append(f"🔎 Looking for: {pirep['departure']} → {pirep['arrival']}")
        part1.append(f"Date window: ±2 days from {pirep_datetime}")
        part1.append("```")
        messages.append("\n".join(part1))
        
        # Part 2+: Flight Analysis (split into chunks)
        matching_flight = None
        for idx, flight in enumerate(user_flights[:10]):
            flight_debug = ["```"]
            flight_debug.append(f"[{idx+1}] {flight.get('originAirport', '?')} → {flight.get('destinationAirport', '?')}")
            
            if not isinstance(flight, dict):
                flight_debug.append(f"  ⚠️ Not a dict: {type(flight)}")
                flight_debug.append("```")
                messages.append("\n".join(flight_debug))
                continue
            
            origin = flight.get('originAirport', 'N/A')
            dest = flight.get('destinationAirport', 'N/A')
            created_raw = flight.get('created', 'MISSING')
            
            flight_debug.append(f"  Callsign: {flight.get('callsign', 'N/A')}")
            flight_debug.append(f"  Aircraft: {flight.get('aircraftId', 'N/A')}")
            flight_debug.append(f"  Created: {created_raw}")
            flight_debug.append(f"  Time: {flight.get('totalTime', 0)} min")
            
            route_match = (origin == pirep['departure'] and dest == pirep['arrival'])
            flight_debug.append(f"  Route match: {route_match}")
            
            if not route_match:
                if origin != pirep['departure']:
                    flight_debug.append(f"    ❌ Origin: '{origin}' != '{pirep['departure']}'")
                if dest != pirep['arrival']:
                    flight_debug.append(f"    ❌ Dest: '{dest}' != '{pirep['arrival']}'")
                flight_debug.append("```")
                messages.append("\n".join(flight_debug))
                continue
            
            flight_debug.append(f"  ✅ ROUTE MATCHES! Checking date...")
            
            try:
                flight_date = datetime.fromisoformat(created_raw)
                flight_debug.append(f"    Parsed: {flight_date}")
                flight_debug.append(f"    Has TZ: {flight_date.tzinfo is not None}")
                
                if flight_date.tzinfo:
                    flight_date = flight_date.replace(tzinfo=None)
                    flight_debug.append(f"    After TZ removal: {flight_date}")
                
                time_diff = abs(flight_date - pirep_datetime)
                flight_debug.append(f"    Time diff: {time_diff}")
                flight_debug.append(f"    Within 2 days: {time_diff < timedelta(days=2)}")
                
                if time_diff < timedelta(days=2):
                    matching_flight = flight
                    flight_debug.append(f"    ✅✅✅ MATCH FOUND!")
                    flight_debug.append("```")
                    messages.append("\n".join(flight_debug))
                    break
                else:
                    flight_debug.append(f"    ❌ Outside date window")
            except Exception as e:
                flight_debug.append(f"    ❌ Date error: {type(e).__name__}: {e}")
            
            flight_debug.append("```")
            messages.append("\n".join(flight_debug))
        
        # Final summary
        summary = ["```"]
        if not matching_flight:
            summary.append(f"❌ NO MATCH in {min(len(user_flights), 10)} flights")
            if len(user_flights) > 10:
                summary.append(f"(Showing first 10 of {len(user_flights)} total)")
        else:
            summary.append(f"✅ MATCH FOUND:")
            summary.append(f"  Callsign: {matching_flight.get('callsign')}")
            summary.append(f"  Aircraft: {matching_flight.get('aircraftId')}")
            summary.append(f"  Server: {matching_flight.get('server')}")
        summary.append("```")
        messages.append("\n".join(summary))
        
        return messages
    
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
                flight_date = datetime.fromisoformat(flight['created'])
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
            past_text = [f"`{fd.strftime('%d %b %H:%M')}` **{f.get('originAirport', 'N/A')}** → **{f.get('destinationAirport', 'N/A')}** ({format_flight_time(int(f.get('totalTime', 0) * 60))})\n   📡 {f.get('callsign', 'N/A')}" for f, fd in past_flights[:10]]
            embed.add_field(name="⏪ Past Flights (Last 3 Days)", value="\n".join(past_text) if past_text else "No flights", inline=False)
        else:
            embed.add_field(name="⏪ Past Flights (Last 3 Days)", value="No Expert server flights found", inline=False)
        
        if future_flights:
            future_text = [f"`{fd.strftime('%d %b %H:%M')}` **{f.get('originAirport', 'N/A')}** → **{f.get('destinationAirport', 'N/A')}** ({format_flight_time(int(f.get('totalTime', 0) * 60))})\n   📡 {f.get('callsign', 'N/A')}" for f, fd in future_flights[:10]]
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

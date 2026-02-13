import discord
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional, Dict, List
import re

if TYPE_CHECKING:
    from ..bot import MyBot

def parse_api_datetime(date_string):
    """Parse API datetime string handling microseconds + Z format."""
    if date_string.endswith('Z'):
        date_string = date_string[:-1] + '+00:00'
    
    # Handle microseconds - pad to 6 digits or truncate if more than 6
    microsecond_pattern = r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d+)(\+\d{2}:\d{2}|Z)$'
    match = re.match(microsecond_pattern, date_string)
    if match:
        date_part, microseconds, timezone = match.groups()
        microseconds = microseconds.ljust(6, '0')[:6]
        date_string = f"{date_part}.{microseconds}{timezone}"
    
    return datetime.fromisoformat(date_string)

def format_flight_time(seconds: int) -> str:
    """Format seconds into HH:MM."""
    if seconds is None:
        return "N/A"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}"

def get_multiplier_text(pirep_time, api_time, pirep_multiplier):
    """Calculate and return multiplier information with time difference."""
    # Ensure all values are numeric
    try:
        pirep_time = int(pirep_time) if pirep_time is not None and str(pirep_time).strip() != 'None' and str(pirep_time).strip() != '' else 0
        api_time = int(api_time) if api_time is not None and str(api_time).strip() != 'None' and str(api_time).strip() != '' else 0
        
        # Handle multiplier more carefully
        if pirep_multiplier is None or str(pirep_multiplier).strip() in ['None', '']:
            pirep_multiplier = 1.0
        else:
            pirep_multiplier = float(str(pirep_multiplier).strip())
    except (ValueError, TypeError):
        return "⚠️ **INVALID** - Invalid time data"
    
    if api_time == 0:
        return "⚠️ **INVALID** - API flight time is 0"
    
    if pirep_multiplier > 3:
        return f"❌ **{pirep_multiplier}x** - Multiplier too high"
    
    expected_time_sec = api_time * pirep_multiplier
    time_diff_sec = pirep_time - expected_time_sec
    time_diff_minutes = abs(time_diff_sec) // 60
    
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
    
    if time_diff_sec > 300:
        return f"⚠️ **{multiplier_display}** (+{time_diff_minutes}min vs IF time)"
    elif time_diff_sec < -300:
        return f"⚠️ **{multiplier_display}** (-{time_diff_minutes}min vs IF time)"
    else:
        return f"✅ **{multiplier_display}** - Accurate time"

import logging
from typing import TYPE_CHECKING, Optional, Dict, List
import asyncio
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class PirepValidationService:
    def __init__(self, bot: 'MyBot'):
        self.bot = bot

    async def find_pirep_by_callsign_flight_and_route(self, callsign: str, flight_number: str, departure: str, arrival: str) -> Optional[Dict]:
        """Find PIREP by callsign, flight number, and route with validation."""
        logger.info(f"[DEBUG] Searching PIREP (Route): Call={callsign}, Flt={flight_number}, Dep={departure}, Arr={arrival}")
        if not all([callsign, flight_number, departure, arrival]):
            logger.warning("Invalid search parameters provided")
            return None
            
        try:
            await asyncio.sleep(2)  # DB sync delay
            
            pilot_info = await self.bot.pilots_model.get_pilot_by_callsign(callsign)
            if not pilot_info:
                logger.warning(f"[DEBUG] Pilot not found for callsign: {callsign}")
                return None
                
            pilot_id = pilot_info['id']
            pending_pireps = await self.bot.pireps_model.get_pending_pireps()
            logger.info(f"[DEBUG] Found {len(pending_pireps)} pending PIREPs in DB")
            
            for pirep in pending_pireps:
                if pirep['pilotid'] == pilot_id:
                     logger.info(f"[DEBUG] Checking candidate PIREP {pirep['pirep_id']}: Flt={pirep.get('flightnum')} Dep={pirep.get('departure')} Arr={pirep.get('arrival')}")

                if (pirep['pilotid'] == pilot_id and 
                    str(pirep.get('flightnum', '')) == flight_number and
                    pirep.get('departure', '') == departure and
                    pirep.get('arrival', '') == arrival):
                    logger.info(f"[DEBUG] Found PIREP {pirep['pirep_id']} for {callsign}")
                    return pirep
            
            logger.info(f"[DEBUG] No matching PIREP found for {callsign} {flight_number} {departure}-{arrival}")
            return None
            
        except Exception as e:
            logger.error(f"Error searching for PIREP: {e}")
            return None

    async def find_pirep_by_callsign_and_flight(self, callsign: str, flight_number: str) -> Optional[Dict]:
        """Find PIREP by callsign and flight number."""
        logger.info(f"[DEBUG] Searching PIREP (Simple): Call={callsign}, Flt={flight_number}")
        import asyncio
        await asyncio.sleep(2)  # DB sync delay
        
        pilot_info = await self.bot.pilots_model.get_pilot_by_callsign(callsign)
        if not pilot_info:
            logger.warning(f"[DEBUG] Pilot not found for callsign: {callsign}")
            return None
            
        pilot_id = pilot_info['id']
        pending_pireps = await self.bot.pireps_model.get_pending_pireps()
        
        for pirep in pending_pireps:
            if pirep['pilotid'] == pilot_id and str(pirep.get('flightnum', '')) == flight_number:
                logger.info(f"[DEBUG] Found PIREP {pirep['pirep_id']} for {callsign}")
                return pirep
        
        logger.info(f"[DEBUG] No matching PIREP found for {callsign} {flight_number}")
        return None

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

    async def validate_pirep(self, pirep: Dict) -> discord.Embed:
        """Main validation logic - returns Discord embed with results."""
        logger.info(f"[DEBUG] Validating PIREP ID: {pirep.get('pirep_id')}")
        pilot_info = await self.bot.pilots_model.get_pilot_by_id(pirep['pilotid'])
        pilot_display = pirep['pilot_name']
        if pilot_info:
            if pilot_info.get('discordid'):
                pilot_display = f"{pilot_info['callsign']} | <@{str(pilot_info['discordid'])}>"
            elif pilot_info.get('callsign'):
                pilot_display = f"{pilot_info['callsign']} | {pirep['pilot_name']}"
        
        ifuserid = await self.resolve_ifuserid(pirep)
        
        if not ifuserid:
            logger.warning(f"[DEBUG] Could not resolve IF User ID for PIREP {pirep.get('pirep_id')}")
            return discord.Embed(
                title=f"# {pirep['departure']} - {pirep['arrival']} #",
                description=f"**Flight Time:** {pirep['formatted_flighttime']}\\n**Pilot:** {pirep['pilot_name']}",
                color=discord.Color.orange()
            ).add_field(name="⚠️ VALIDATION SKIPPED", value="Could not resolve Infinite Flight User ID. Manual review required.", inline=False)

        try:
            user_flights_data = await self.bot.if_api_manager.get_user_flights(ifuserid, hours=72)
        except Exception as e:
            logger.error(f"API error getting user flights for {ifuserid}: {e}")
            user_flights_data = None
        
        if not user_flights_data or not user_flights_data.get('result'):
            logger.warning(f"[DEBUG] No flight data returned from API for user {ifuserid}")
            return discord.Embed(
                title=f"# {pirep['departure']} - {pirep['arrival']} #",
                description=f"**Flight Time:** {pirep['formatted_flighttime']}\\n**Pilot:** {pirep['pilot_name']}",
                color=discord.Color.orange()
            ).add_field(name="⚠️ API LIMITATION", value="Flight validation API endpoint not available. Manual review required.", inline=False)
        
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
                except Exception as e:
                    logger.debug(f"Error parsing flight date: {e}")
                    continue
        
        logger.info(f"[DEBUG] Matching flight found: {matching_flight is not None}")

        try:
            route_valid = await self.check_route_database(pirep['departure'], pirep['arrival'], pirep.get('flightnum'), pirep.get('pilotid'))
            route_exists = await self.check_route_exists(pirep['departure'], pirep['arrival'], pirep.get('pilotid'))
            
            # Check if it's an OWD route
            is_owd_route = False
            if route_valid and await self._check_pilot_rank_for_owd(pirep.get('pilotid')):
                regular_route = await self.bot.routes_model.find_route_by_icao(pirep['departure'], pirep['arrival'])
                if not regular_route:
                    is_owd_route = True
        except Exception as e:
            logger.error(f"Error checking route database: {e}")
            route_valid = False
            route_exists = False
            is_owd_route = False

        if not matching_flight:
            return self._create_no_match_embed(pirep, pilot_display, user_flights)

        return await self._create_validation_embed(pirep, pilot_display, matching_flight, route_valid, route_exists, is_owd_route)

    def _create_no_match_embed(self, pirep, pilot_display, user_flights):
        """Create embed for when no matching flight is found."""
        ifc_username = self.extract_ifc_username(pirep.get('ifc'))
        
        # Clean up pilot display to remove HTML entities
        pilot_display_clean = pilot_display.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        
        embed = discord.Embed(
            title=f"# {pirep['departure']} - {pirep['arrival']} #",
            description=f"👤 **Pilot:** {pilot_display_clean}\n💬 **IFC:** {ifc_username}\n✈️ **Flight:** {pirep.get('flightnum', 'N/A')}",
            color=discord.Color.red()
        )
        
        # Check if flight time is zero or invalid
        flight_time_display = pirep['formatted_flighttime']
        if flight_time_display in ['00:00:00', '0:00', '00:00']:
            flight_time_display = "⚠️ Invalid (0 minutes)"
        
        analysis_details = [
            f"**Looking for:** {pirep['departure']} → {pirep['arrival']}",
            f"**Aircraft:** {pirep['aircraft_name']}",
            f"**Date:** {pirep['date']}",
            f"**Flight Time:** {flight_time_display}"
        ]
        
        embed.add_field(name="❌ MATCH NOT FOUND - DETAILED ANALYSIS", value="\n".join(analysis_details), inline=False)
        return embed

    async def _create_validation_embed(self, pirep, pilot_display, matching_flight, route_valid, route_exists, is_owd_route=False):
        """Create detailed validation embed with all checks."""
        landings = matching_flight.get('landingCount') if matching_flight.get('landingCount') is not None else 'N/A'
        route_match = f"{pirep['departure']} → {pirep['arrival']}" == f"{matching_flight['originAirport']} → {matching_flight['destinationAirport']}"
        aircraft_pirep = pirep['aircraft_name']
        aircraft_api_name = self.bot.aircraft_name_map.get(matching_flight.get('aircraftId'), "Unknown Aircraft")
        aircraft_match = aircraft_pirep == aircraft_api_name
        
        time_pirep_sec = 0
        time_api_sec = 0
        pirep_multiplier = 1.0
        
        try:
            pirep_time_raw = pirep.get('flighttime')
            if pirep_time_raw is not None and str(pirep_time_raw).strip() not in ['None', '']:
                time_pirep_sec = int(pirep_time_raw)
        except (ValueError, TypeError):
            time_pirep_sec = 0
            
        try:
            api_time_raw = matching_flight.get('totalTime')
            if api_time_raw is not None and str(api_time_raw).strip() not in ['None', '']:
                time_api_sec = int(float(api_time_raw) * 60)
        except (ValueError, TypeError):
            time_api_sec = 0
            
        try:
            multi_raw = pirep.get('multi')
            if multi_raw is not None and str(multi_raw).strip() not in ['None', '']:
                pirep_multiplier = float(multi_raw)
        except (ValueError, TypeError):
            pirep_multiplier = 1.0
        multiplier_text = get_multiplier_text(time_pirep_sec, time_api_sec, pirep_multiplier)
        
        livery_name = await self.resolve_livery_name(matching_flight.get('aircraftId'), matching_flight.get('liveryId'))
        
        try:
            flight_date = parse_api_datetime(matching_flight['created'])
            if flight_date.tzinfo is not None:
                flight_date = flight_date.replace(tzinfo=None)
            date_str = flight_date.strftime('%d %b %Y %H:%M Z')
        except Exception as e:
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
        
        # Check for zero flight time issue
        if time_pirep_sec == 0:
            issues.append("Zero flight time reported")
        
        multiplier_used = float(pirep_multiplier) > 1
        high_multiplier = float(pirep_multiplier) > 3
        
        expected_time = time_api_sec * float(pirep_multiplier)
        time_diff = abs(time_pirep_sec - expected_time)
        significant_time_error = time_diff > 300
        
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
            if is_owd_route:
                flight_num_status = "✅ Valid (OneWorld Discover route)"
            else:
                flight_num_status = "✅ Valid"
        elif route_exists:
            flight_num_status = "⚠️ Route exists, flight # invalid"
        else:
            flight_num_status = "⚠️ Route not in database"
            
        # Clean up pilot display to remove HTML entities
        pilot_display_clean = pilot_display.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        
        ifc_username = self.extract_ifc_username(pirep.get('ifc'))
        
        embed = discord.Embed(
            title=f"# {pirep['departure']} - {pirep['arrival']} #",
            description=f"**Flight:** {pirep.get('flightnum', 'N/A')} ({flight_num_status})",
            color=embed_color
        )
        
        embed.add_field(
            name="📄 Pilot Claim",
            value=f"👤 **Pilot:** {pilot_display_clean}\n💬 **IFC:** {ifc_username}\n✈️ **Aircraft:** {aircraft_pirep}\n⏱️ **Filed Time:** {pirep['formatted_flighttime']}",
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
        
        result_text = f"{status_text}\nIssues: {', '.join(issues)}" if not overall_valid else status_text
        embed.add_field(name="🎯 Result", value=result_text, inline=False)
        embed.set_footer(text=f"PIREP ID: {pirep['pirep_id']} | Validated via Public API v2")
        
        return embed

    async def get_debug_info(self, pirep) -> List[str]:
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
    
    async def get_flight_history(self, pirep) -> List[discord.Embed]:
        """Get flight history for the pilot - returns list of embeds."""
        logger.info(f"[DEBUG] Fetching flight history for PIREP {pirep.get('pirep_id')}")
        ifuserid = await self.resolve_ifuserid(pirep)
        
        if not ifuserid:
            return [discord.Embed(
                title="❌ Flight History Unavailable",
                description="No Infinite Flight User ID found for this pilot.",
                color=discord.Color.red()
            )]
        
        try:
            user_flights_data = await self.bot.if_api_manager.get_user_flights(ifuserid, hours=72)
        except Exception as e:
            logger.error(f"[DEBUG] API Error getting flight history: {e}")
            return [discord.Embed(
                title="⚠️ Flight History Unavailable",
                description="Could not fetch flight data from API.",
                color=discord.Color.orange()
            )]
        
        if not user_flights_data or not user_flights_data.get('result'):
            logger.warning("[DEBUG] No result in user_flights_data")
            return [discord.Embed(
                title="⚠️ Flight History Unavailable",
                description="Could not fetch flight data from API.",
                color=discord.Color.orange()
            )]
        
        result_data = user_flights_data['result']
        user_flights = result_data.get('data', []) if isinstance(result_data, dict) else result_data
        
        logger.info(f"[DEBUG] Processing {len(user_flights)} flights from API")
        
        expert_flights = []
        for f in user_flights:
            if not isinstance(f, dict):
                continue
            try:
                # Safe check for server
                if str(f.get('server') or '').lower() == 'expert':
                    expert_flights.append(f)
            except Exception as e:
                logger.error(f"[DEBUG] Error processing flight record: {f} - Error: {e}")
        
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
            except Exception as e:
                logger.error(f"[DEBUG] Error parsing flight date in history: {e}")
                continue
        
        past_flights.sort(key=lambda x: x[1], reverse=True)
        future_flights.sort(key=lambda x: x[1])
        
        embeds = []
        
        # Main embed
        main_embed = discord.Embed(
            title=f"📅 Flight History - {pirep['pilot_name']}",
            description=f"Expert Server flights around PIREP submission date: **{pirep_date.strftime('%d %b %Y')}**",
            color=discord.Color.blue()
        )
        main_embed.set_footer(text=f"PIREP ID: {pirep['pirep_id']} | Total Expert Flights: {len(expert_flights)}")
        embeds.append(main_embed)
        
        # Past flights embed
        if past_flights:
            past_text = []
            for f, fd in past_flights[:10]:
                aircraft_name = self.bot.aircraft_name_map.get(f.get('aircraftId'), "Unknown Aircraft")
                livery_name = await self.resolve_livery_name(f.get('aircraftId'), f.get('liveryId'))
                landings = f.get('landingCount', 0) if f.get('landingCount') is not None else 0
                past_text.append(f"`{fd.strftime('%d %b %H:%M')}` **{f.get('originAirport', 'N/A')}** → **{f.get('destinationAirport', 'N/A')}** ({format_flight_time(int((f.get('totalTime') or 0) * 60))})\n   📡 **{f.get('callsign', 'N/A')}** • 🛬 {landings}\n   ✈️ {aircraft_name} • 🎨 {livery_name}")
            
            past_embed = discord.Embed(
                title="⏪ Past Flights (Last 3 Days)",
                description="\n\n".join(past_text[:5]),
                color=discord.Color.blue()
            )
            embeds.append(past_embed)
            
            if len(past_text) > 5:
                past_embed2 = discord.Embed(
                    title="⏪ Past Flights (Continued)",
                    description="\n\n".join(past_text[5:10]),
                    color=discord.Color.blue()
                )
                embeds.append(past_embed2)
        else:
            past_embed = discord.Embed(
                title="⏪ Past Flights (Last 3 Days)",
                description="No Expert server flights found",
                color=discord.Color.blue()
            )
            embeds.append(past_embed)
        
        # Future flights embed
        if future_flights:
            future_text = []
            for f, fd in future_flights[:10]:
                aircraft_name = self.bot.aircraft_name_map.get(f.get('aircraftId'), "Unknown Aircraft")
                livery_name = await self.resolve_livery_name(f.get('aircraftId'), f.get('liveryId'))
                landings = f.get('landingCount', 0) if f.get('landingCount') is not None else 0
                future_text.append(f"`{fd.strftime('%d %b %H:%M')}` **{f.get('originAirport', 'N/A')}** → **{f.get('destinationAirport', 'N/A')}** ({format_flight_time(int((f.get('totalTime') or 0) * 60))})\n   📡 **{f.get('callsign', 'N/A')}** • 🛬 {landings}\n   ✈️ {aircraft_name} • 🎨 {livery_name}")
            
            future_embed = discord.Embed(
                title="⏩ Future Flights (After Submission)",
                description="\n\n".join(future_text[:5]),
                color=discord.Color.blue()
            )
            embeds.append(future_embed)
            
            if len(future_text) > 5:
                future_embed2 = discord.Embed(
                    title="⏩ Future Flights (Continued)",
                    description="\n\n".join(future_text[5:10]),
                    color=discord.Color.blue()
                )
                embeds.append(future_embed2)
        else:
            future_embed = discord.Embed(
                title="⏩ Future Flights (After Submission)",
                description="No future flights found",
                color=discord.Color.blue()
            )
            embeds.append(future_embed)
        
        return embeds
    
    async def _check_pilot_rank_for_owd(self, pilot_id: int) -> bool:
        """Check if pilot has OneWorld rank or above"""
        import json
        import os
        
        pilot_data = await self.bot.pilots_model.get_pilot_by_id(pilot_id)
        if not pilot_data:
            return False
        
        rank_config_path = os.path.join('assets', 'rank_config.json')
        with open(rank_config_path, 'r') as f:
            rank_config = json.load(f)
        
        pilot_rank = pilot_data.get('rank', 'Cadet')
        owd_ranks = ['OneWorld', 'Oryx']
        return pilot_rank in owd_ranks

    async def check_route_database(self, departure, arrival, flight_num, pilot_id=None):
        """Check if the flight number exists in the routes database or OWD routes."""
        if not flight_num:
            return False
        
        try:
            route_data = await self.bot.routes_model.find_route_by_icao(departure, arrival)
            if route_data:
                return self.validate_flight_number(flight_num, route_data['fltnum'])
            
            # Check OWD routes if pilot has OneWorld rank or above
            if pilot_id and await self._check_pilot_rank_for_owd(pilot_id):
                owd_route = await self.bot.owd_routes_model.find_route_by_icao(departure, arrival)
                if owd_route and owd_route['flight_number'] == flight_num:
                    return True
            
            return False
        except Exception as e:
            print(f"Error checking route database: {e}")
            return False
    
    async def check_route_exists(self, departure, arrival, pilot_id=None):
        """Check if the route exists in the database or OWD routes."""
        try:
            route_data = await self.bot.routes_model.find_route_by_icao(departure, arrival)
            if route_data:
                return True
            
            # Check OWD routes if pilot has OneWorld rank or above
            if pilot_id and await self._check_pilot_rank_for_owd(pilot_id):
                owd_route = await self.bot.owd_routes_model.find_route_by_icao(departure, arrival)
                return owd_route is not None
            
            return False
        except Exception as e:
            print(f"Error checking if route exists: {e}")
            return False
    
    def validate_flight_number(self, pilot_flight_num, db_flight_nums):
        """Validate if pilot's flight number is allowed."""
        if not pilot_flight_num or not db_flight_nums:
            return False
        
        allowed_flights = [f.strip() for f in db_flight_nums.split(',')]
        pilot_flights = [f.strip() for f in pilot_flight_num.split(',')]
        
        return all(pf in allowed_flights for pf in pilot_flights)
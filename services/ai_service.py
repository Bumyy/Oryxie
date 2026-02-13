import logging
import json
import random

logger = logging.getLogger('oryxie')

class AIService:
    def __init__(self):
        pass

    async def generate_ai_scenario(self, aircraft_name, dep_data, dest_data, passengers, cargo, flight_type, deadline):
        if flight_type == "amiri":
            return await self._generate_amiri_scenario(aircraft_name, dep_data, dest_data, passengers, cargo)
        else:
            return await self._generate_executive_scenario(aircraft_name, dep_data, dest_data, passengers, cargo)

    async def _generate_amiri_scenario(self, aircraft_name, dep_data, dest_data, passengers, cargo):
        # Load dignitary names and scenarios
        try:
            with open("assets/dignitary_names.json", 'r') as f:
                dignitary_data = json.load(f)
                if random.random() > 0.6:
                    dignitary = random.choice(dignitary_data["royal_names"])
                    scenario = random.choice(dignitary_data["royal_scenarios"])
                    is_royal = True
                else:
                    dignitary = random.choice(dignitary_data["official_roles"])
                    scenario = random.choice(dignitary_data["dignitary_scenarios"])
                    is_royal = False
        except Exception as e:
            logger.warning(f"Could not load dignitary names: {e}")
            dignitary = "Senior Official"
            scenario = "Official"
            is_royal = False

        prompt = f"""
Generate a Qatar Amiri Flight briefing for a single event.

FLIGHT DETAILS:
- Principal: {dignitary}
- Mission Type: {scenario}
- Destination: {dest_data.get('municipality')}, {dest_data.get('iso_country')}
- Passengers: {passengers} 
- Cargo: {cargo}kg 

You MUST provide exactly 3 sections separated by ||| :

1. Dossier (15-20 words): Principal's title and role in this specific {scenario} mission
2. Purpose (50-70 words): ONE clear {scenario} event with specific counterpart and objective Based on Destination: {dest_data.get('municipality')}, {dest_data.get('iso_country')}. Focus on what will be accomplished in this {scenario} mission with Clear Explanation of Dossier and Counterpart role 
3. Payload (25-35 words): Give Details of {passengers} number and {cargo}kg 

RULES:
- Plain text only, no markdown formatting
- Create ONE realistic {scenario} scenario, not multiple objectives
- Be specific about the single event/meeting purpose
- Use professional diplomatic language
- Note: Sometimes Event is public so you can clearly give all details, sometimes Events is not public and Purpose of flights is not disclosed, so you can create any type of thing at your Choice 
- CRITICAL: You MUST include ALL THREE sections. Do not stop after section 2.
- CRITICAL: Each section MUST be separated by exactly ||| (three pipe symbols)

Example format: 
Dossier text here ||| Purpose text here ||| Payload text here

Format: [Dossier] ||| [Purpose] ||| [Payload]
"""

        return await self._call_ai_api(prompt, "amiri", {"dignitary": dignitary, "is_royal": is_royal, "scenario": scenario}, passengers, cargo)

    async def _generate_executive_scenario(self, aircraft_name, dep_data, dest_data, passengers, cargo):
        # Generate hypothetical names based on cities
        dep_city = dep_data.get('municipality', 'Unknown')
        dest_city = dest_data.get('municipality', 'Unknown')
        
        prompt = f"""
Generate an charter briefing for a single event.

FLIGHT DETAILS:
- Route: {dep_city} to {dest_city}
- Aircraft: {aircraft_name}
- Passengers: {passengers} 
- Cargo: {cargo}kg 

You MUST provide exactly 3 sections separated by ||| :

1. Client (12-18 words): Hypothetical executive name, title, and company Based on {dep_city} City.
2. Purpose (50-60 words): First Explain little about Counterpart based on {dest_city} City and Country  (10-15 words) and then Explain a single Event Based on All Flight Details provided above (remaining words) 
3. Manifest (25-35 words): {passengers} details and {cargo}kg contents for this specific event

RULES:
- Plain text only, no markdown formatting
- Create ONE realistic scenario, not multiple meetings
- Avoid Qatar/Qatari references in names
- Be specific about the single Event
- Note: Sometimes Event is public so you can clearly give all details, sometimes Events is not public and Purpose of flights is not disclosed, so you can create any type of thing at your Choice 
- CRITICAL: You MUST include ALL THREE sections. Do not stop after section 2.
- CRITICAL: Each section MUST be separated by exactly ||| (three pipe symbols)

Example format: 
Client text here ||| Purpose text here ||| Manifest text here

Format: [Client] ||| [Purpose] ||| [Manifest]
"""
        
        return await self._call_ai_api(prompt, "executive", {}, passengers, cargo)

    async def _call_ai_api(self, prompt, context_type, extra_data, passengers, cargo):
        return await self._get_fallback(context_type, passengers, cargo, extra_data)

    async def _get_fallback(self, flight_type, passengers, cargo, extra_data=None):
        if flight_type == "amiri":
            dignitary = extra_data.get("dignitary", "Official") if extra_data else "Official"
            return {
                "dignitary": dignitary, 
                "dignitary_intro": f"His Excellency {dignitary}", 
                "mission_briefing": "Meeting with senior government officials for diplomatic consultations to advance bilateral relations and strategic partnerships in key sectors. This high priority mission involves time-sensitive diplomatic objectives requiring immediate attention and coordination.",
                "manifest_details": f"Delegation of {passengers} officials with {cargo}kg of diplomatic materials and equipment.",
                "mission_type": "Official Mission"
            }
        return {
            "client": "VIP Charter", 
            "client_intro": "Executive Client, CEO requiring executive transportation services.", 
            "mission_briefing": "Priority business charter flight to meet with international partners for corporate travel requirements and strategic business discussions.",
            "manifest_details": f"Traveling with {passengers} passengers and {cargo}kg of business materials and equipment.",
            "purpose": "Executive Charter"
        }
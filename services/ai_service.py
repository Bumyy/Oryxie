import os
import logging
import re
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

logger = logging.getLogger('oryxie')

class AIService:
    def __init__(self):
        self.model = None
        self.current_model = None
        self.models = ['gemma-3-12b-it']
        self.model_index = 0
        self._initialize_ai()
    
    def _initialize_ai(self):
        print("[DEBUG] Starting AI initialization...")
        try:
            google_ai_key = os.getenv("GOOGLE_AI_KEY")
            print(f"[DEBUG] GOOGLE_AI_KEY found: {bool(google_ai_key)}")
            if google_ai_key:
                print(f"[DEBUG] API key length: {len(google_ai_key) if google_ai_key else 0}")
                genai.configure(api_key=google_ai_key)
                print("[DEBUG] genai.configure() completed")
                self._try_next_model()
            else:
                print("[DEBUG] No GOOGLE_AI_KEY found in environment")
                logger.warning("GOOGLE_AI_KEY not found, AI features disabled")
                self.model = None
        except Exception as e:
            print(f"[DEBUG] Exception in _initialize_ai: {e}")
            logger.error(f"Failed to initialize AI model: {e}")
            self.model = None
    
    def _try_next_model(self):
        print(f"[DEBUG] Trying model index {self.model_index} of {len(self.models)}")
        if self.model_index >= len(self.models):
            print("[DEBUG] All AI models exhausted")
            logger.error("All AI models exhausted")
            self.model = None
            return
        
        try:
            model_name = self.models[self.model_index]
            print(f"[DEBUG] Attempting to initialize model: {model_name}")
            self.model = genai.GenerativeModel(model_name)
            self.current_model = model_name
            print(f"[DEBUG] Successfully initialized model: {model_name}")
            logger.info(f"AI model initialized with {model_name}")
        except Exception as e:
            print(f"[DEBUG] Failed to initialize {self.models[self.model_index]}: {e}")
            logger.warning(f"Failed to initialize {self.models[self.model_index]}: {e}")
            self.model_index += 1
            self._try_next_model()
    
    def _sanitize_input(self, text: str) -> str:
        if not isinstance(text, str): text = str(text)
        text = re.sub(r'[\r\n\t]', ' ', text)
        text = re.sub(r'[{}\[\]"\']', '', text)
        return text[:200]

    async def generate_ai_scenario(self, aircraft_name, dep_data, dest_data, passengers, cargo, flight_type, deadline):
        print(f"[DEBUG] generate_ai_scenario called with flight_type: {flight_type}")
        print(f"[DEBUG] Model available: {bool(self.model)}")
        print(f"[DEBUG] Current model: {self.current_model}")
        
        if not self.model:
            print("[DEBUG] No model available, using fallback")
            return await self._get_fallback(flight_type, passengers, cargo)
            
        if flight_type == "amiri":
            print("[DEBUG] Generating Amiri scenario")
            return await self._generate_amiri_scenario(aircraft_name, dep_data, dest_data, passengers, cargo)
        else:
            print("[DEBUG] Generating Executive scenario")
            return await self._generate_executive_scenario(aircraft_name, dep_data, dest_data, passengers, cargo)

    async def _generate_amiri_scenario(self, aircraft_name, dep_data, dest_data, passengers, cargo):
        import json
        import random
        
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
        print(f"[DEBUG] _call_ai_api called with context_type: {context_type}")
        print(f"[DEBUG] Model status: {bool(self.model)}")
        print(f"[DEBUG] Current model: {self.current_model}")
        print(f"[DEBUG] Prompt length: {len(prompt)}")
        
        try:
            # SAFETY SETTINGS: Prevent the AI from blocking "diplomatic" or "official" terms
            safety_settings = {
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            }

            print("[DEBUG] Calling model.generate_content()...")
            response = self.model.generate_content(
                prompt, 
                generation_config={"temperature": 0.7, "max_output_tokens": 1000},
                safety_settings=safety_settings
            )
            print(f"[DEBUG] API call completed. Response received: {bool(response)}")

            # Check for blocked content (safety filters)
            print(f"[DEBUG] Response candidates: {len(response.candidates) if response.candidates else 0}")
            if not response.candidates or len(response.candidates) == 0:
                print(f"[DEBUG] Content blocked by {self.current_model}, switching models")
                logger.warning(f"Model {self.current_model} blocked the content. Retrying with next model...")
                self.model_index += 1
                self._try_next_model()
                if self.model:
                    return await self._call_ai_api(prompt, context_type, extra_data, passengers, cargo)
                return await self._get_fallback(context_type, passengers, cargo, extra_data)

            txt = response.text
            print(f"[DEBUG] Raw AI response length: {len(txt)}")
            print(f"[DEBUG] AI Response preview: {txt[:200]}...")
            logger.info(f"AI Response: {txt[:200]}...")  # Debug log

            parts = []
            if "|||" in txt:
                parts = txt.split("|||")
                print(f"[DEBUG] Found {len(parts)} parts after splitting by |||")
                logger.info(f"AI Parts found: {len(parts)} parts")
                
                # Handle incomplete responses by padding with fallback content
                if len(parts) == 2:
                    logger.info("AI returned only 2 parts, adding fallback third section")
                    if context_type == "amiri":
                        parts.append(f"Delegation of {passengers} officials with {cargo}kg of diplomatic materials and equipment.")
                    else:
                        parts.append(f"Business delegation of {passengers} passengers with {cargo}kg of equipment and documents.")
                
                if len(parts) >= 3:
                    if context_type == "amiri":
                        return {
                            "dignitary": extra_data.get("dignitary"),
                            "dignitary_intro": parts[0].strip(),
                            "mission_briefing": parts[1].strip(),
                            "manifest_details": parts[2].strip(),
                            "mission_type": "Royal Mission" if extra_data.get("is_royal") else "Official Mission"
                        }
                    else:
                        return {
                            "client": "Private Client",
                            "client_intro": parts[0].strip(),
                            "mission_briefing": parts[1].strip(),
                            "manifest_details": parts[2].strip(),
                            "purpose": "Executive Charter"
                        }
            else:
                logger.warning("AI response does not contain ||| separators")

            # Only show error if we couldn't handle it
            if not parts or len(parts) < 2:
                separators_found = len(parts) - 1 if parts else 0
                logger.warning(f"AI Format Error: Found {separators_found} separators, expected 2.")
        except Exception as e:
            print(f"[DEBUG] Exception in _call_ai_api: {type(e).__name__}: {e}")
            # Check specifically for Quota/Rate Limit errors
            error_msg = str(e).lower()
            if "429" in error_msg or "resource_exhausted" in error_msg or "quota" in error_msg:
                print(f"[DEBUG] Quota exhausted for {self.current_model}, switching models")
                logger.error(f"QUOTA EXHAUSTED for {self.current_model}. Switching permanently for this session.")
            if "429" in error_msg or "resource_exhausted" in error_msg or "quota" in error_msg or "location" in error_msg:
                print(f"[DEBUG] Error '{error_msg}' for {self.current_model}, switching models")
                logger.error(f"Error for {self.current_model}: {error_msg}. Switching permanently for this session.")
                self.model_index += 1
                self._try_next_model()
                if self.model:
                    return await self._call_ai_api(prompt, context_type, extra_data, passengers, cargo)
            
            print(f"[DEBUG] Unexpected AI Error: {e}")
            logger.error(f"Unexpected AI Error: {e}")
        
        return await self._get_fallback(context_type, passengers, cargo, extra_data)

    async def _get_fallback(self, flight_type, passengers, cargo, extra_data=None):
        print(f"[DEBUG] Using fallback for flight_type: {flight_type}")
        logger.info(f"Returning {flight_type} fallback content")
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
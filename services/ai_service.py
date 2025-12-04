import os
import google.generativeai as genai

class AIService:
    def __init__(self):
        self.model = None
        self._initialize_ai()
    
    def _initialize_ai(self):
        """Initialize Google AI model"""
        try:
            google_ai_key = os.getenv("GOOGLE_AI_KEY")
            if google_ai_key:
                genai.configure(api_key=google_ai_key)
                self.model = genai.GenerativeModel('gemini-2.5-pro')
            else:
                self.model = None
        except Exception:
            self.model = None
    
    async def generate_ai_scenario(self, aircraft_name, dep_data, dest_data, passengers, cargo, flight_type, deadline):
        """Generate detailed AI scenario for flights when claimed"""
        if flight_type == "amiri":
            result = await self._generate_amiri_scenario(aircraft_name, dep_data, dest_data, passengers, cargo, deadline)
        else:
            result = await self._generate_executive_scenario(aircraft_name, dep_data, dest_data, passengers, cargo, deadline)
        return result
    
    async def _generate_amiri_scenario(self, aircraft_name, dep_data, dest_data, passengers, cargo, deadline):
        """Generate detailed Amiri flight scenario"""
        import json
        import random
        try:
            with open("assets/dignitary_names.json", 'r') as f:
                dignitary_data = json.load(f)
                if random.random() > 0.6:
                    dignitary = random.choice(dignitary_data["royal_names"])
                    is_royal = True
                else:
                    dignitary = random.choice(dignitary_data["official_roles"])
                    is_royal = False
        except Exception:
            dignitary = "Senior Official"
            is_royal = False

        if not self.model:
            return {"dignitary": dignitary, "dignitary_intro": "Intro unavailable", "mission_briefing": "Briefing unavailable", "mission_type": "Official"}

        try:
            prompt = f"""
Act as a Diplomatic Protocol Officer briefing the Chief Pilot. 
Create a rich, story-driven briefing for a high-profile Qatar Amiri Flight.

--- FLIGHT DATA ---
1. PRINCIPAL: {dignitary} (Identify the real-world person holding this role if it is a generic title).
2. DESTINATION: {dest_data.get('municipality', 'Unknown city')}, {dest_data.get('iso_country', 'International')}.
3. CONTEXT: {passengers} passengers, {cargo}kg cargo.

--- BRIEFING REQUIREMENTS ---
**SECTION 1: THE PRINCIPAL** (Max 15 words)
Identify who acts as the Lead Passenger. Use proper titles.

**SECTION 2: THE DIPLOMATIC BRIEFING** (Approx 80-100 words)
Write a detailed diplomatic narrative focusing ONLY on the mission objective.
1.  **The Mission:** Focus on the *High-Level Objective*. Is it a treaty? An energy deal? A secret mediation? Connect it specifically to the country we are visiting.
2.  **The Counterpart:** **Explicitly name** the Ministry, Company, or real-world Leader they are meeting in {dest_data.get('municipality', 'that city')}.
3.  **DO NOT mention passenger count or cargo weight in this section.**

**SECTION 3: MANIFEST DETAILS** (Approx 40-60 words)
Explain the passenger count and cargo weight separately:
1.  **Passenger Explanation:** Why this specific number of passengers? (delegation size, security team, etc.)
2.  **Cargo Explanation:** What specific items justify this cargo weight? (vehicles, equipment, gifts, etc.)

**TONE:** Serious, Sophisticated, "Inside Knowledge".

--- OUTPUT FORMAT ---
[Section 1 Text] ||| [Section 2 Text] ||| [Section 3 Text]
"""
            response = self.model.generate_content(prompt)
            
            if response and response.text and response.text.count('|||') == 2:
                parts = response.text.strip().split('|||')
                if len(parts) >= 3:
                    return {
                        "dignitary": dignitary, 
                        "dignitary_intro": parts[0].strip(), 
                        "mission_briefing": parts[1].strip(),
                        "manifest_details": parts[2].strip(),
                        "deadline_rationale": "N/A",
                        "mission_type": "Royal Mission" if is_royal else "Official Mission"
                    }
            
        except Exception:
            pass
        
        return {
            "dignitary": dignitary, 
            "dignitary_intro": f"His Excellency {dignitary}", 
            "mission_briefing": "Transporting diplomatic delegation for official state business.",
            "manifest_details": f"Delegation of {passengers} officials with {cargo}kg of diplomatic materials and security equipment.",
            "deadline_rationale": "N/A",
            "mission_type": "Official Mission"
        }
    
    async def _generate_executive_scenario(self, aircraft_name, dep_data, dest_data, passengers, cargo, deadline):
        """Generate detailed Qatar Executive (Charter) flight scenario with Region-Aware Names"""
        
        if not self.model:
            return {
                "client": "VIP Charter", 
                "client_intro": "Private Client", 
                "mission_briefing": "Confidential charter flight.", 
                "purpose": "Business"
            }
        
        try:
            prompt = f"""
Act as a "Private jet airline " Dispatcher briefing the crew on a high-end charter flight.

--- FLIGHT VARIABLES ---
1. DEPARTURE: {dep_data.get('municipality', 'Unknown')}, {dep_data.get('iso_country', 'Origin')}
2. ARRIVAL: {dest_data.get('municipality', 'Unknown')}, {dest_data.get('iso_country', 'Destination')}
3. LOAD: {passengers} passengers, {cargo}kg cargo
4. AIRCRAFT: {aircraft_name}

--- INSTRUCTIONS ---
**SECTION 1: THE CLIENT** (Max 15 words)
Invent a specific fictional name, title, and industry/profession.
*   **CRITICAL:** The Name MUST sound native to either the **Departure Country** OR the **Arrival Country**.
*   *Example:* If flying China -> UK, use a Chinese name OR a British name.

**SECTION 2: THE BACKSTORY** (Approx 60-80 words)
Why are they flying specifically from {dep_data.get('municipality')} to {dest_data.get('municipality')}?
1.  **The Context:** Connect the Client's industry to the location. (e.g., Tech CEO flying to San Francisco; Oil Baron flying to Norway).
2.  **DO NOT mention passenger count or cargo weight in this section.**

**SECTION 3: MANIFEST DETAILS** (Approx 30-50 words)
Explain the passenger count and cargo weight separately:
1.  **Passenger Explanation:** Why this {passengers} number of passengers? (entourage, staff, family, etc.)
2.  **Cargo Explanation:** What specific items justify this {cargo} cargo weight? (equipment, personal items, etc.)
    
**TONE:** Discreet, Elite, "Inside information".

--- OUTPUT FORMAT ---
[Section 1 Text] ||| [Section 2 Text] ||| [Section 3 Text]
"""
            response = self.model.generate_content(prompt)

            if response and response.text and response.text.count('|||') == 2:
                parts = response.text.strip().split('|||')
                if len(parts) >= 3:
                    return {
                        "client": "Private Client",
                        "client_intro": parts[0].strip(),
                        "mission_briefing": parts[1].strip(),
                        "manifest_details": parts[2].strip(),
                        "deadline_rationale": "N/A",
                        "purpose": "Executive Charter"
                    }

        except Exception:
            pass
        
        return {
            "client": "VIP Charter", 
            "client_intro": "Confidential Client", 
            "mission_briefing": "Priority charter flight requested for global business travel.",
            "manifest_details": f"Traveling with {passengers} passengers and {cargo}kg of business materials.",
            "purpose": "Executive Charter"
        }
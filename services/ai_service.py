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
                self.model = genai.GenerativeModel('gemini-1.5-flash-latest')
        except Exception as e:
            print(f"Failed to initialize AI model: {e}")
            self.model = None
    
    async def generate_ai_scenario(self, aircraft_name, dep_data, dest_data, passengers, cargo, flight_type, deadline):
        """Generate detailed AI scenario for flights when claimed"""
        if flight_type == "amiri":
            return await self._generate_amiri_scenario(aircraft_name, dep_data, dest_data, passengers, cargo, deadline)
        else:
            return await self._generate_executive_scenario(aircraft_name, dep_data, dest_data, passengers, cargo, deadline)
    
    async def _generate_amiri_scenario(self, aircraft_name, dep_data, dest_data, passengers, cargo, deadline):
        """Generate Amiri flight scenario"""
        # Get dignitary and scenarios from JSON data directly
        import json
        import random
        try:
            with open("assets/dignitary_names.json", 'r') as f:
                dignitary_data = json.load(f)
                royal_names = dignitary_data["royal_names"]
                official_roles = dignitary_data["official_roles"]
                dignitary_scenarios = dignitary_data.get("dignitary_scenarios", [])
                royal_scenarios = dignitary_data.get("royal_scenarios", [])
                
                # Select dignitary and corresponding scenario type
                is_royal = random.random() > 0.6
                if is_royal:
                    dignitary = random.choice(royal_names)
                    scenario_options = royal_scenarios
                else:
                    dignitary = random.choice(official_roles)
                    scenario_options = dignitary_scenarios
        except (FileNotFoundError, json.JSONDecodeError):
            dignitary = "Senior Official"
            scenario_options = ["Official", "Diplomatic", "Government"]
        
        if not self.model:
            return {"dignitary": dignitary, "dignitary_intro": "Intro unavailable.", "mission_briefing": "Briefing unavailable.", "deadline_rationale": "Rationale unavailable.", "mission_type": "Official Mission"}
        
        try:
            # Let AI choose the best scenario based on destination
            scenario_list = ", ".join(scenario_options)
            prompt = f"""
                Generate a detailed, multi-part briefing for a Qatari Amiri flight. The response must be structured with the specified separators.

                Flight Details:
                - Dignitary: {dignitary}
                - Destination: {dest_data['municipality']}, {dest_data.get('iso_country', '')}
                - Aircraft: {aircraft_name}
                - Available Scenario Types: {scenario_list}

                Task:
                First, choose the most appropriate scenario type from the list based on the given  Airport location and dignitary type. Then write three distinct sections for the flight plan. Avoid conflict or high tension tone.

                1.  **DIGNITARY INTRODUCTION:** (Approx. 5 words) Provide positive background for the dignitary, {dignitary}. 
                2.  **MISSION BRIEFING:** (Approx. 30 - 45 words) Detail the primary purpose of the flight based on your chosen scenario type. Be specific about the goals at the destination and incorporate the scenario theme.
                3.  **DEADLINE RATIONALE:** (Approx. 10 words) Explain why the flight must be completed by {deadline}. This should relate to the mission's timing.

                Output Format: Use '|||' as a separator between each section. Do not include section titles. The response must contain exactly two '|||' separators.
                """
            response = self.model.generate_content(prompt)
            if response and response.text and response.text.count('|||') == 2:
                intro, briefing, deadline_rationale = [part.strip() for part in response.text.strip().split('|||', 2)]
                return {"dignitary": dignitary, "dignitary_intro": intro, "mission_briefing": briefing, "deadline_rationale": deadline_rationale, "mission_type": "Royal Mission" if is_royal else "Official Mission"}
        except Exception:
            pass
        
        return {"dignitary": dignitary, "dignitary_intro": "Default intro.", "mission_briefing": "Default briefing.", "deadline_rationale": "Default rationale.", "mission_type": "Royal Mission" if is_royal else "Official Mission"}
    
    async def _generate_executive_scenario(self, aircraft_name, dep_data, dest_data, passengers, cargo, deadline):
        """Generate Executive flight scenario"""
        if not self.model:
            return {"client": "Business Client", "client_intro": "Intro unavailable.", "mission_briefing": "Briefing unavailable.", "deadline_rationale": "Rationale unavailable.", "purpose": "Executive Travel"}
        
        try:
            prompt = f"""
                Generate a detailed, multi-part briefing for a Qatar Executive charter flight. The response must be structured with the specified separators.

                Flight Details:
                - Route: {dep_data.get('municipality', 'Unknown')} to {dest_data.get('municipality', 'Unknown')}
                - Aircraft: {aircraft_name}
                - Load: {passengers} passengers, {cargo}kg cargo

                Task:
                Write four distinct sections for the flight plan.

                1.  **CLIENT NAME:** Create a realistic, fictional company or individual's name.
                2.  **CLIENT INTRODUCTION:** (Approx. 8 words) Provide a brief background on the client.
                3.  **MISSION BRIEFING:** (Approx. 30-45 words) Detail the specific business purpose of the flight.
                4.  **DEADLINE RATIONALE:** (Approx. 20 words) Explain the urgency of the flight and why it must be completed by {deadline}.

                Output Format: Use '|||' as a separator between each section. Do not include section titles. The response must contain exactly three '|||' separators.
                """
            response = self.model.generate_content(prompt)
            if response and response.text and response.text.count('|||') == 3:
                client, intro, briefing, deadline_rationale = [part.strip().replace('"', '').replace("'", '') for part in response.text.strip().split('|||', 3)]
                return {"client": client, "client_intro": intro, "mission_briefing": briefing, "deadline_rationale": deadline_rationale, "purpose": "Executive Travel"}
        except Exception:
            pass
        
        return {"client": "Default Client", "client_intro": "Default intro.", "mission_briefing": "Default briefing.", "deadline_rationale": "Default rationale.", "purpose": "Executive Travel"}
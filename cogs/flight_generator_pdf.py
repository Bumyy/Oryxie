import discord
from discord.ext import commands
from discord import app_commands
import os
import random
import google.generativeai as genai
from .utils import get_country_flag
from database.flight_data import FlightData
import io

# Flight generator cog for Amiri and Executive flights with AI scenario generation
GOOGLE_AI_KEY = os.getenv("GOOGLE_AI_KEY")
if GOOGLE_AI_KEY:
    genai.configure(api_key=GOOGLE_AI_KEY)

# Channel configuration
FLIGHT_REQUEST_CHANNEL_ID = int(os.getenv("FLIGHT_REQUEST_CHANNEL_ID"))
DISPATCH_CHANNEL_ID = int(os.getenv("DISPATCH_CHANNEL_ID"))
AMIRI_CHANNEL_ID = int(os.getenv("AMIRI_CHANNEL_ID"))
EXECUTIVE_CHANNEL_ID = int(os.getenv("EXECUTIVE_CHANNEL_ID"))
# UI view for flight request buttons
class FlightRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # Helper method to handle flight requests with error checking
    async def _handle_request(self, interaction: discord.Interaction, flight_type: str):
        cog = interaction.client.get_cog("FlightGeneratorPDF")
        if not cog:
            return await interaction.response.send_message("❌ Service unavailable.", ephemeral=True)
        await cog.handle_flight_request(interaction, flight_type)

    @discord.ui.button(label="Request Amiri Flight", style=discord.ButtonStyle.primary, emoji="🇶🇦", custom_id="persistent_amiri_pdf")
    async def amiri_flight(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_request(interaction, "amiri")

    @discord.ui.button(label="Request Executive Flight", style=discord.ButtonStyle.secondary, emoji="✈️", custom_id="persistent_executive_pdf")
    async def executive_flight(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_request(interaction, "executive")

# UI view for continent selection in executive flights
class ContinentSelectView(discord.ui.View):
    def __init__(self, flight_type: str, aircraft_list: list):
        super().__init__(timeout=60)
        self.flight_type = flight_type
        self.aircraft_list = aircraft_list
        # Create buttons for each continent
        for continent in ["Asia", "Europe", "North America", "South America", "Africa", "Oceania"]:
            button = discord.ui.Button(label=continent, style=discord.ButtonStyle.primary)
            button.callback = self.continent_callback
            self.add_item(button)

    # Handle continent selection and proceed to aircraft selection
    async def continent_callback(self, interaction: discord.Interaction):
        pressed_button = discord.utils.get(self.children, custom_id=interaction.data.get('custom_id'))
        if not pressed_button:
            return await interaction.response.send_message("❌ Invalid selection.", ephemeral=True)
        
        view = AircraftSelectView(self.flight_type, self.aircraft_list, continent=pressed_button.label)
        await interaction.response.edit_message(content="Great! Now, please select your desired aircraft:", view=view)

# UI view for aircraft selection
class AircraftSelectView(discord.ui.View):
    def __init__(self, flight_type: str, aircraft_list: list, continent: str = None):
        super().__init__(timeout=60)
        self.flight_type = flight_type
        self.continent = continent
        # Create buttons for each available aircraft
        for aircraft in aircraft_list:
            button = discord.ui.Button(label=aircraft, style=discord.ButtonStyle.secondary)
            button.callback = self.aircraft_callback
            self.add_item(button)
    
    # Handle aircraft selection and send dispatch request
    async def aircraft_callback(self, interaction: discord.Interaction):
        pressed_button = discord.utils.get(self.children, custom_id=interaction.data.get('custom_id'))
        if not pressed_button:
            return await interaction.response.send_message("❌ Invalid selection.", ephemeral=True)
        
        cog = interaction.client.get_cog("FlightGeneratorPDF")
        if not cog:
            return await interaction.response.send_message("❌ Service unavailable.", ephemeral=True)

        channel_name = "amiri-flights" if self.flight_type == "amiri" else "executive-flights"
        await interaction.response.edit_message(content=f"✅ Your flight request for a **{pressed_button.label}** is sent. A dispatcher will soon announce new flights in `#{channel_name}`. Keep an eye on the channel.", view=None)
        await cog.send_dispatch_request(interaction, self.flight_type, pressed_button.label, self.continent)

# UI view for dispatchers to claim flight requests
class DispatchClaimView(discord.ui.View):
    def __init__(self, requester, flight_type, aircraft, rank, continent: str = None):
        super().__init__(timeout=300)
        self.requester = requester
        self.flight_type = flight_type
        self.aircraft = aircraft
        self.rank = rank
        self.continent = continent
    
    # Handle dispatcher claiming a flight request
    @discord.ui.button(label="Claim Request", style=discord.ButtonStyle.success, emoji="✋")
    async def claim_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("FlightGeneratorPDF")
        if not cog:
            return await interaction.response.send_message("❌ Service unavailable.", ephemeral=True)
        
        if not cog.bot.flightdata.has_staff_permissions(interaction.user.roles):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        
        button.disabled = True
        await interaction.response.edit_message(view=self)

        message = f"✅ {interaction.user.mention} claimed this request. Please use `/amiri` or `/executive` to generate the flight for {self.requester.mention}."
        if self.continent:
            message += f"\n**Note:** The pilot requested a flight to **{self.continent}**."
        await interaction.followup.send(message)

# UI view for pilots to claim generated flights
class FlightClaimView(discord.ui.View):
    def __init__(self, flight_data: dict, flight_type: str, flight_brain: FlightData):
        super().__init__(timeout=None)
        self.flight_data = flight_data
        self.flight_type = flight_type
        self.flight_brain = flight_brain

    @discord.ui.button(label="Claim Flight", style=discord.ButtonStyle.success, emoji="✈️", custom_id="claim_flight_pdf")
    async def claim_flight(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)

            aircraft_code = self.flight_brain.get_aircraft_code_from_name(self.flight_data['aircraft_name'])
            error_msg = self.flight_brain.check_permissions(interaction.user.roles, self.flight_type, aircraft_code)
            if error_msg:
                return await interaction.followup.send(error_msg, ephemeral=True)

            button.disabled = True
            button.label = f"Claimed by {interaction.user.display_name}"
            await interaction.message.edit(view=self)

            # Generate AI scenario now when flight is claimed
            cog = interaction.client.get_cog("FlightGeneratorPDF")
            if cog and cog.model:
                dep_data = self.flight_brain.get_airport_data("OTHH")
                # Parse destination from route format
                route_parts = self.flight_data['route'].split()
                if len(route_parts) >= 4:
                    dest_icao = route_parts[3]  # "OTHH 🇶🇦 to DEST"
                else:
                    dest_icao = route_parts[2] if len(route_parts) >= 3 else "OTHH"  # Fallback
                
                dest_data = self.flight_brain.get_airport_data(dest_icao)
                
                if dep_data is not None and dest_data is not None:
                    aircraft_name = self.flight_data['aircraft_name']
                    passengers = self.flight_data['passengers']
                    cargo = self.flight_data['cargo']
                    deadline = self.flight_data['deadline']
                    
                    scenario_data = await cog._generate_ai_scenario(aircraft_name, dep_data, dest_data, passengers, cargo, self.flight_type, deadline)
                    self.flight_data.update(scenario_data)

            pdf_output = self.flight_brain.generate_professional_pdf(self.flight_data, self.flight_type, interaction.user)
            if pdf_output:
                pdf_buffer = io.BytesIO(pdf_output)
                await interaction.channel.send(f"✈️ Here are the flight documents for **{self.flight_data.get('flight_number', 'Unknown')}** claimed by {interaction.user.mention}.", file=discord.File(pdf_buffer, f"flight_{self.flight_data['flight_number']}.pdf"))
                await interaction.followup.send("✅ Flight claimed! The documents have been posted in the channel.", ephemeral=True)
            else:
                await interaction.followup.send("✅ Flight claimed! (But PDF generation failed).", ephemeral=True)

        except Exception as e:
            await interaction.followup.send("❌ An error occurred while claiming the flight.", ephemeral=True)

class AmiriApprovalView(discord.ui.View):
    def __init__(self, cog, aircraft: str, interaction: discord.Interaction):
        super().__init__(timeout=300)
        self.cog = cog
        self.aircraft = aircraft
        self.interaction = interaction
        self.departure = "OTHH"
        self.arrival = None
        self.passengers = None
        self.cargo = None
        self.message = None

    async def send_initial_message(self):
        await self.propose_new_route()

    async def propose_new_route(self):
        try:
            aircraft_code = self.aircraft
            aircraft_data = self.cog.bot.flightdata.AIRCRAFT_DATA["amiri"][aircraft_code]
            
            self.passengers = random.randint(aircraft_data['pax_range'][0], aircraft_data['pax_range'][1])
            self.cargo = random.randint(aircraft_data['cargo_kg_range'][0], aircraft_data['cargo_kg_range'][1])

            # Check if airport database is loaded
            if self.cog.bot.flightdata.airports_db is None:
                content = "❌ Airport database not loaded. Please contact an administrator."
                if self.message:
                    await self.message.edit(content=content, view=None, embed=None)
                else:
                    await self.interaction.followup.send(content, ephemeral=True)
                return

            self.arrival = self.cog.bot.flightdata.get_random_suitable_airport(aircraft_code)
            
            if not self.arrival:
                content = "❌ Could not find a suitable airport. Please try again with a different aircraft."
                if self.message:
                    await self.message.edit(content=content, view=None, embed=None)
                else:
                    await self.interaction.followup.send(content, ephemeral=True)
                return

            dep_data = self.cog.bot.flightdata.get_airport_data(self.departure)
            arr_data = self.cog.bot.flightdata.get_airport_data(self.arrival)

            if dep_data is None or arr_data is None:
                content = "❌ Error fetching airport data. Please try again."
                if self.message:
                    await self.message.edit(content=content, view=None, embed=None)
                else:
                    await self.interaction.followup.send(content, ephemeral=True)
                return

            dep_location = f"{dep_data.get('municipality', 'N/A')}, {dep_data.get('iso_country', 'N/A')}"
            arr_location = f"{arr_data.get('municipality', 'N/A')}, {arr_data.get('iso_country', 'N/A')}"

            embed = discord.Embed(title="✈️ Amiri Flight Proposal", color=discord.Color.orange())
            embed.description = f"Aircraft: **{self.aircraft}**"
            embed.add_field(name="Departure", value=f"**{self.departure}**\n{dep_location} {get_country_flag(self.departure)}", inline=False)
            embed.add_field(name="Arrival", value=f"**{self.arrival}**\n{arr_location} {get_country_flag(self.arrival)}", inline=False)
            embed.set_footer(text="Please approve or reject this route.")

            if self.message:
                await self.message.edit(content=None, embed=embed, view=self)
            else:
                self.message = await self.interaction.followup.send(embed=embed, view=self, ephemeral=True)
        except Exception as e:
            content = f"❌ An error occurred: {str(e)}"
            if self.message:
                await self.message.edit(content=content, view=None, embed=None)
            else:
                await self.interaction.followup.send(content, ephemeral=True)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        for item in self.children:
            item.disabled = True
        await self.message.edit(view=self)

        flight_data = await self.cog._generate_flight(
            self.aircraft, 
            "amiri", 
            departure=self.departure, 
            destination=self.arrival,
            passengers=self.passengers,
            cargo=self.cargo
        )

        if not flight_data:
            await interaction.followup.send("❌ Failed to generate flight details.", ephemeral=True)
            await self.message.delete()
            return

        embed = discord.Embed(title="🇶🇦 New Amiri Flight Order", color=discord.Color.blue())
        embed.add_field(name="Flight Number", value=flight_data['flight_number'], inline=True)
        embed.add_field(name="Aircraft", value=flight_data['aircraft_name'], inline=True)
        embed.add_field(name="Route", value=flight_data['route'], inline=False)
        
        amiri_channel = self.cog.bot.get_channel(AMIRI_CHANNEL_ID)
        if amiri_channel:
            claim_view = FlightClaimView(flight_data, "amiri", self.cog.bot.flightdata)
            await amiri_channel.send(embed=embed, view=claim_view)
            await self.message.edit(content=f"✅ Flight Approved and posted to {amiri_channel.mention}!", embed=None, view=None)
            await interaction.followup.send("✅ Flight posted!", ephemeral=True)
        else:
            await self.message.edit(content="❌ Could not find the Amiri flight channel.", embed=None, view=None)
            await interaction.followup.send("❌ Could not find the Amiri flight channel.", ephemeral=True)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self.message.edit(content="🔄 Selecting a new airport...", embed=None, view=None)
        await self.propose_new_route()


class FlightGeneratorPDF(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        try:
            self.model = genai.GenerativeModel('gemini-1.5-flash-latest')
        except Exception as e:
            self.model = None
        self.bot.add_view(FlightRequestView())

    def get_dates_with_fuel_logic(self, flight_type: str, fuel_stop_required: bool) -> tuple:
        """Generate current date and deadline based on fuel stop requirements"""
        from datetime import datetime, timedelta
        current_date = datetime.now()
        
        # Amiri flights with fuel stop get 6 days, otherwise 4 days
        if flight_type == "amiri" and fuel_stop_required:
            deadline_days = 6
        else:
            deadline_days = 4
            
        deadline_date = current_date + timedelta(days=deadline_days)
        return current_date.strftime("%d %B %Y"), deadline_date.strftime("%d %B %Y")

    # FLIGHT REQUEST COMMANDS & LOGIC
    
    @app_commands.command(name="setup_request", description="Setup flight request interface (Staff only)")
    async def setup_request(self, interaction: discord.Interaction):
        if not self.bot.flightdata.has_staff_permissions(interaction.user.roles):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        
        embed = discord.Embed(
            title="Amiri & Executive Flights",
            description=(
                "Welcome to the request channel.\n"
                "Here you can request flights by choosing an aircraft based on your rank.\n\n"
                "Your request will be processed by a Dispatcher, who will then send flight details in the respective Amiri or Executive channels.\n\n"
                "You must claim the flight to get the Flight Documents."
            ),
            color=discord.Color.from_rgb(140, 20, 50)
        )
        embed.add_field(
            name="General Rules",
            value=(
                "- **One Flight at a Time:** You cannot claim a new flight until the current one is completed and a PIREP is filed.\n"
                "- **Deadlines:** Each flight has a deadline from when it is claimed. Missing this deadline results in a 24h cooldown before you can request again.\n"
                "- **Responsibility:** Once claimed, the flight is yours to complete."
            ),
            inline=False
        )
        embed.add_field(
            name="Flight Multiplier",
            value="Every flight has a 1.5x (150000) multiplier. You will have to file a manual PIREP for each flight you complete.",
            inline=False
        )
        
        view = FlightRequestView()
        await interaction.response.send_message("✅ Setup message created.", ephemeral=True)
        await interaction.channel.send(embed=embed, view=view)

    async def handle_flight_request(self, interaction: discord.Interaction, flight_type: str):
        error_msg = self.bot.flightdata.check_permissions(interaction.user.roles, flight_type)
        if error_msg:
            return await interaction.response.send_message(error_msg, ephemeral=True)
        
        user_rank = self.bot.flightdata.get_user_rank(interaction.user.roles)
        
        if user_rank == "Ruby" and flight_type == "amiri":
            await interaction.response.send_message(f"✅ Your flight request for an **A319** is sent. A dispatcher will soon announce new flights in `amiri-flights`. Keep an eye on the channel.", ephemeral=True)
            await self.send_dispatch_request(interaction, flight_type, "A319")
            return

        available_aircraft = self.bot.flightdata.get_available_aircraft(interaction.user.roles, flight_type)

        if flight_type == "executive":
            view = ContinentSelectView(flight_type, available_aircraft)
            await interaction.response.send_message("Please select a destination continent for your charter flight:", view=view, ephemeral=True)
        else:
            view = AircraftSelectView(flight_type, available_aircraft)
            await interaction.response.send_message("Please select your desired aircraft:", view=view, ephemeral=True)

    async def send_dispatch_request(self, interaction: discord.Interaction, flight_type: str, aircraft: str, continent: str = None):
        user_rank = self.bot.flightdata.get_user_rank(interaction.user.roles)
        
        dispatch_channel = self.bot.get_channel(DISPATCH_CHANNEL_ID)
        if dispatch_channel:
            dispatcher_role = discord.utils.get(interaction.guild.roles, name="Dispatcher")
            ping = dispatcher_role.mention if dispatcher_role else "@Dispatcher"
            
            embed = discord.Embed(title="🔔 New Flight Request", color=discord.Color.orange())
            embed.add_field(name="Pilot", value=interaction.user.mention, inline=True)
            embed.add_field(name="Rank", value=user_rank, inline=True)
            embed.add_field(name="Type", value=flight_type.title(), inline=True)
            embed.add_field(name="Aircraft", value=aircraft, inline=True)
            if continent:
                embed.add_field(name="Destination Continent", value=continent, inline=True)
            
            view = DispatchClaimView(interaction.user, flight_type, aircraft, user_rank, continent)
            await dispatch_channel.send(f"{ping}", embed=embed, view=view)

    # FLIGHT GENERATION COMMANDS - Dispacher commands to create flights
  
    @app_commands.command(name="amiri", description="Generate Amiri flight (Staff only)")
    @app_commands.describe(aircraft="Aircraft type")
    @app_commands.choices(aircraft=[
        app_commands.Choice(name="A319", value="A319"),
        app_commands.Choice(name="A346", value="A346"),
        app_commands.Choice(name="B748", value="B748")
    ])
    async def amiri_flight(self, interaction: discord.Interaction, aircraft: str):
        if not self.bot.flightdata.has_staff_permissions(interaction.user.roles):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        approval_view = AmiriApprovalView(self, aircraft, interaction)
        await approval_view.send_initial_message()
    
    @app_commands.command(name="executive", description="Generate Executive flight (Staff only)")
    @app_commands.describe(aircraft="Aircraft type", departure="Departure ICAO code", arrival="Arrival ICAO code")
    @app_commands.choices(aircraft=[
        app_commands.Choice(name="A318", value="A318"),
        app_commands.Choice(name="B737", value="B737"),
        app_commands.Choice(name="C350", value="C350")
    ])
    async def executive_flight(self, interaction: discord.Interaction, aircraft: str, departure: str, arrival: str):
        if not self.bot.flightdata.has_staff_permissions(interaction.user.roles):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        
        await interaction.response.defer()
        flight_data = await self._generate_custom_flight(aircraft, "executive", departure.upper(), arrival.upper())
        if not flight_data:
            return await interaction.followup.send("❌ Failed to generate flight.")
        
        embed = discord.Embed(title="🏢 New Charter Request", color=discord.Color.gold())
        embed.add_field(name="Flight Number", value=flight_data['flight_number'], inline=True)
        embed.add_field(name="Aircraft", value=flight_data['aircraft_name'], inline=True)
        embed.add_field(name="Route", value=flight_data['route'], inline=False)
        
        exec_channel = self.bot.get_channel(EXECUTIVE_CHANNEL_ID)
        if exec_channel:
            claim_view = FlightClaimView(flight_data, "executive", self.bot.flightdata)
            await exec_channel.send(embed=embed, view=claim_view)
        
        await interaction.followup.send("✅ Executive flight posted!")

    # AMIRI FLIGHT GENERATION - Core logic for Amiri flights
   
    async def _generate_flight(self, aircraft: str, flight_type: str, departure: str = None, destination: str = None, passengers: int = None, cargo: int = None):
        try:
            if departure is None:
                departure = "OTHH"

            aircraft_data = self.bot.flightdata.AIRCRAFT_DATA[flight_type][aircraft]
            
            if passengers is None:
                passengers = random.randint(aircraft_data['pax_range'][0], aircraft_data['pax_range'][1])
            if cargo is None:
                cargo = random.randint(aircraft_data['cargo_kg_range'][0], aircraft_data['cargo_kg_range'][1])
            
            dep_data = self.bot.flightdata.get_airport_data(departure)
            if dep_data is None: return None
            
            if destination is None:
                destination = self.bot.flightdata.get_random_suitable_airport(aircraft)
                if not destination: return None

            dest_data = self.bot.flightdata.get_airport_data(destination)
            if dest_data is None: return None

            # Calculate distance and fuel stop requirement
            distance = self.bot.flightdata.calculate_distance(departure, destination)
            fuel_stop_required = self.bot.flightdata.needs_fuel_stop(distance, aircraft, flight_type, passengers, cargo)
            
            # Generate dates with fuel stop logic
            current_date, deadline = self.get_dates_with_fuel_logic(flight_type, fuel_stop_required)
            
            # No AI scenario generation here - only basic dignitary selection
            scenario_data = {'dignitary': self.bot.flightdata.select_dignitary() if flight_type == 'amiri' else 'Business Client'}
            
            route_format = f"{departure} {get_country_flag(departure)} to {destination} {get_country_flag(destination)} to {departure} {get_country_flag(departure)}"

            return {
                'flight_number': self.bot.flightdata.generate_flight_number(flight_type),
                'aircraft_name': aircraft_data['name'],
                'passengers': passengers,
                'cargo': cargo,
                'route': route_format,
                'fuel_stop_required': fuel_stop_required,
                'current_date': current_date,
                'deadline': deadline,
                **scenario_data
            }
        except Exception as e:
            return None
    
    # EXECUTIVE FLIGHT GENERATION - Core logic for charter flights
   
    async def _generate_custom_flight(self, aircraft: str, flight_type: str, departure: str, arrival: str):
        try:
            dep_data = self.bot.flightdata.get_airport_data(departure)
            dest_data = self.bot.flightdata.get_airport_data(arrival)
            if dep_data is None or dest_data is None: return None
            
            aircraft_data = self.bot.flightdata.AIRCRAFT_DATA[flight_type][aircraft]
            route_format = f"{departure} {get_country_flag(departure)} - {arrival} {get_country_flag(arrival)}"
            
            distance = self.bot.flightdata.calculate_distance(departure, arrival)
            passengers = random.randint(aircraft_data['pax_range'][0], aircraft_data['pax_range'][1])
            cargo = random.randint(aircraft_data['cargo_kg_range'][0], aircraft_data['cargo_kg_range'][1])
            fuel_stop_required = self.bot.flightdata.needs_fuel_stop(distance, aircraft, flight_type, passengers, cargo)
            
            # Executive flights always get 4 days (no special fuel stop logic)
            current_date, deadline = self.get_dates_with_fuel_logic(flight_type, fuel_stop_required)
            
            # No AI scenario generation here - only basic client info
            scenario_data = {'client': 'Business Client'}

            return {
                'flight_number': self.bot.flightdata.generate_flight_number(flight_type),
                'aircraft_name': aircraft_data['name'],
                'passengers': passengers,
                'cargo': cargo,
                'route': route_format,
                'fuel_stop_required': fuel_stop_required,
                'current_date': current_date,
                'deadline': deadline,
                **scenario_data
            }
        except Exception as e:
            return None
    
    # AI SCENARIO GENERATION - Create realistic flight scenarios
 
    async def _generate_ai_scenario(self, aircraft_name, dep_data, dest_data, passengers, cargo, flight_type, deadline):
        if flight_type == "amiri":
            dignitary = self.bot.flightdata.select_dignitary()
            if not self.model:
                return { "dignitary": dignitary, "dignitary_intro": "Intro unavailable.", "mission_briefing": "Briefing unavailable.", "deadline_rationale": "Rationale unavailable.", "mission_type": "Official Mission"}
            try:
                prompt = f"""
                    Generate a detailed, multi-part briefing for a Qatari Amiri flight. The response must be structured with the specified separators.

                    Flight Details:
                    - Dignitary: {dignitary}
                    - Destination: {dest_data['municipality']}, {dest_data.get('iso_country', '')}
                    - Aircraft: {aircraft_name}

                    Task:
                    Write three distinct sections for the flight plan. Avoide conflict or high tension tone.

                    1.  **DIGNITARY INTRODUCTION:** (Approx. 5 words) Provide positive background for the dignitary, {dignitary}. 
                    2.  **MISSION BRIEFING:** (Approx. 30 - 45 words) Detail the primary purpose of the flight. This should be a comprehensive scenario involving activities like establishing trade relations, attending a cultural summit, overseeing a humanitarian aid delivery, or fostering educational partnerships. Be specific about the goals at the destination.
                    3.  **DEADLINE RATIONALE:** (Approx. 10 words) Explain why the flight must be completed by {deadline}. This should relate to the mission's timing.

                    Output Format: Use '|||' as a separator between each section. Do not include section titles. The response must contain exactly two '|||' separators.
                    """
                response = self.model.generate_content(prompt)
                if response and response.text and response.text.count('|||') == 2:
                    intro, briefing, deadline_rationale = [part.strip() for part in response.text.strip().split('|||', 2)]
                    return {"dignitary": dignitary, "dignitary_intro": intro, "mission_briefing": briefing, "deadline_rationale": deadline_rationale, "mission_type": "Official Mission"}
            except Exception as e:
                pass
            return {"dignitary": dignitary, "dignitary_intro": "Default intro.", "mission_briefing": "Default briefing.", "deadline_rationale": "Default rationale.", "mission_type": "Official Mission"}
        else:
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
            except Exception as e:
                pass
            return {"client": "Default Client", "client_intro": "Default intro.", "mission_briefing": "Default briefing.", "deadline_rationale": "Default rationale.", "purpose": "Executive Travel"}


async def setup(bot: commands.Bot):
    await bot.add_cog(FlightGeneratorPDF(bot))
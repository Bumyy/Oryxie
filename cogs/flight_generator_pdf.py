import discord
from discord.ext import commands
from discord import app_commands
import os
import random
from .utils import get_country_flag
from database.flight_data import FlightData
from services.ai_service import AIService
from services.flight_generation_service import FlightService
from services.pdf_service import PDFService
from models.flight_details import FlightDetails
import io

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
    def __init__(self, flight_data, flight_type: str, flight_brain: FlightData):
        super().__init__(timeout=None)
        self.flight_data = flight_data
        self.flight_type = flight_type
        self.flight_brain = flight_brain

    @discord.ui.button(label="Claim Flight", style=discord.ButtonStyle.success, emoji="✈️", custom_id="claim_flight_pdf")
    async def claim_flight(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)

            aircraft_name = self.flight_data.aircraft_name if isinstance(self.flight_data, FlightDetails) else self.flight_data['aircraft_name']
            aircraft_code = self.flight_brain.get_aircraft_code_from_name(aircraft_name)
            error_msg = self.flight_brain.check_permissions(interaction.user.roles, self.flight_type, aircraft_code)
            if error_msg:
                return await interaction.followup.send(error_msg, ephemeral=True)

            button.disabled = True
            button.label = f"Claimed by {interaction.user.display_name}"
            await interaction.message.edit(view=self)

            # Generate AI scenario now when flight is claimed
            cog = interaction.client.get_cog("FlightGeneratorPDF")
            if cog:
                print(f"[DEBUG] Starting AI scenario generation for {self.flight_type} flight")
                # Parse departure and destination from route format
                route = self.flight_data.route if isinstance(self.flight_data, FlightDetails) else self.flight_data['route']
                print(f"[DEBUG] Route: {route}")
                route_parts = route.split()
                
                # Parse departure ICAO (first part)
                dep_icao = route_parts[0] if len(route_parts) >= 1 else "OTHH"
                dep_data = self.flight_brain.get_airport_data(dep_icao)
                
                # Parse destination ICAO
                if len(route_parts) >= 4:
                    dest_icao = route_parts[3]  # "OTHH 🇶🇦 to DEST"
                else:
                    dest_icao = route_parts[2] if len(route_parts) >= 3 else "OTHH"  # Fallback
                
                print(f"[DEBUG] Departure ICAO: {dep_icao}, Destination ICAO: {dest_icao}")
                dest_data = self.flight_brain.get_airport_data(dest_icao)
                
                if dep_data is not None and dest_data is not None:
                    print(f"[DEBUG] Airport data found - Dep: {dep_data.get('municipality', 'N/A')}, Dest: {dest_data.get('municipality', 'N/A')}")
                    aircraft_name = self.flight_data.aircraft_name if isinstance(self.flight_data, FlightDetails) else self.flight_data['aircraft_name']
                    passengers = self.flight_data.passengers if isinstance(self.flight_data, FlightDetails) else self.flight_data['passengers']
                    cargo = self.flight_data.cargo if isinstance(self.flight_data, FlightDetails) else self.flight_data['cargo']
                    deadline = self.flight_data.deadline if isinstance(self.flight_data, FlightDetails) else self.flight_data['deadline']
                    
                    print(f"[DEBUG] Calling AI service with: aircraft={aircraft_name}, passengers={passengers}, cargo={cargo}, deadline={deadline}")
                    scenario_data = await cog.ai_service.generate_ai_scenario(aircraft_name, dep_data, dest_data, passengers, cargo, self.flight_type, deadline)
                    print(f"[DEBUG] AI scenario data received: {scenario_data}")
                    if isinstance(self.flight_data, FlightDetails):
                        for key, value in scenario_data.items():
                            setattr(self.flight_data, key, value)
                    else:
                        self.flight_data.update(scenario_data)
                    print(f"[DEBUG] AI scenario data applied to flight_data")
                else:
                    print(f"[DEBUG] Airport data missing - Dep: {dep_data is not None}, Dest: {dest_data is not None}")

            # Generate PDF
            pdf_output = cog.pdf_service.generate_flight_pdf(self.flight_data, self.flight_type, interaction.user)
            print(f"[DEBUG] PDF generation result: {pdf_output is not None}")
            
            if pdf_output:
                # Create private thread with only the claimer
                thread_name = f"{'Mission Brief' if self.flight_type == 'amiri' else 'Flight Brief'} - {interaction.user.display_name}"
                
                # Create thread with only the claimer (no dispatchers)
                thread = await interaction.channel.create_thread(
                    name=thread_name,
                    type=discord.ChannelType.private_thread,
                    invitable=False,
                    reason=f"Flight briefing for {interaction.user.display_name}"
                )
                
                # Add claimer to thread
                await thread.add_user(interaction.user)
                
                # Add dispatchers to thread
                dispatcher_role = discord.utils.get(interaction.guild.roles, name="Dispatcher")
                if dispatcher_role:
                    for member in interaction.guild.members:
                        if dispatcher_role in member.roles:
                            await thread.add_user(member)
                
                # Send message in thread with PDF
                pdf_buffer = io.BytesIO(pdf_output)
                flight_number = self.flight_data.flight_number if isinstance(self.flight_data, FlightDetails) else self.flight_data.get('flight_number', 'Unknown')
                
                thread_message = f"""Hi {interaction.user.display_name}! 👋

🔒 This private thread is opened for you to :
Ask questions about your mission
Report any issues
Inform about your PIREP when filled , for fast Approval
and Maintaining secrecy of This classified operations

Dispatchers can assist you if needed.

Good luck with your Flight!

Here is your Details of Flight"""
                
                await thread.send(thread_message, file=discord.File(pdf_buffer, f"flight_{flight_number}.pdf"))
                
                await interaction.followup.send(f"✅ Flight claimed! Your private briefing thread has been created: {thread.mention}", ephemeral=True)
            else:
                print(f"[DEBUG] PDF generation failed")
                await interaction.followup.send("✅ Flight claimed! (But PDF generation failed).", ephemeral=True)

        except Exception as e:
            print(f"[DEBUG] Exception in claim_flight: {e}")
            import traceback
            traceback.print_exc()
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

        flight_data = await self.cog.flight_service.generate_flight(
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
        embed.add_field(name="Flight Number", value=flight_data.flight_number, inline=True)
        embed.add_field(name="Aircraft", value=flight_data.aircraft_name, inline=True)
        embed.add_field(name="Route", value=flight_data.route, inline=False)
        
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
        self.ai_service = bot.ai_service
        self.flight_service = bot.flight_service
        self.pdf_service = bot.pdf_service
        self.bot.add_view(FlightRequestView())



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
        flight_data = await self.flight_service.generate_custom_flight(aircraft, "executive", departure.upper(), arrival.upper())
        if not flight_data:
            return await interaction.followup.send("❌ Failed to generate flight.")
        
        embed = discord.Embed(title="🏢 New Charter Request", color=discord.Color.gold())
        embed.add_field(name="Flight Number", value=flight_data.flight_number, inline=True)
        embed.add_field(name="Aircraft", value=flight_data.aircraft_name, inline=True)
        embed.add_field(name="Route", value=flight_data.route, inline=False)
        
        exec_channel = self.bot.get_channel(EXECUTIVE_CHANNEL_ID)
        if exec_channel:
            claim_view = FlightClaimView(flight_data, "executive", self.bot.flightdata)
            await exec_channel.send(embed=embed, view=claim_view)
        
        await interaction.followup.send("✅ Executive flight posted!")




async def setup(bot: commands.Bot):
    await bot.add_cog(FlightGeneratorPDF(bot))
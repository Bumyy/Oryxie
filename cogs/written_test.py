import discord
from discord.ext import commands
import asyncio
import random
import datetime
import os

# --- CONFIGURATION ---
RECRUITER_ROLE_ID = int(os.getenv("RECRUITER_ROLE_ID"))
WRITTEN_TEST_ROLE_ID = int(os.getenv("WRITTEN_TEST_ROLE_ID"))

# --- TEST DATA ---
ALL_QUESTIONS = [
    {"id": 1, "text": "Match the following:", "options": {'A': 'A->1, B->4, C->3, D->2', 'B': 'A->2, B->4, C->3, D->1', 'C': 'A->3, B->4, C->1, D->2', 'D': 'A->3, B->1, C->4, D->2'}, "correct": "D", "timeout": 300, "image": "https://cdn.discordapp.com/attachments/1326629089121144832/1402560900484956160/image.png?ex=68945c20&is=68930aa0&hm=53638a6112de9f391db364d3d529fb05a46f3a3b4c0f3d27b173a1a549fae7fc&"},
    {"id": 2, "text": "What are the maximum **preferred speeds** during taxi according to the User Guide?", "options": {'A': 'Straight line -> 15 knots, Turns -> 15 knots', 'B': 'Straight line -> 35 knots, Turns -> 5 knots', 'C': 'Straight line -> 25 knots, Turns -> 10 knots', 'D': 'Straight line -> 25 knots, Turns -> 5 knots'}, "correct": "C", "timeout": 180, "image": None},
    {"id": 3, "text": "You are Qatari 005CR, you are departing west from OTHH to OERK from runway 34R, what will be your take off announcement?", "options": {'A': 'Qatari 005CR, taking off runway 34R, departing east', 'B': 'Qatari 005CR, taking off runway 34L, departing west', 'C': 'Qatari 005CR, taking off runway 34R, Remaining in pattern', 'D': 'Qatari 005CR, taking off runway 34R, departing west'}, "correct": "D", "timeout": 180, "image": None},
    {"id": 4, "text": "If an airport's elevation is 1,500 feet, what would typically be the pattern work altitude for jet aircraft and propeller aircraft (props) in MSL?", "options": {'A': 'Jet: 2,500 ft | Prop: 2,000 ft', 'B': 'Jet: 2,000 ft | Prop: 1,500 ft', 'C': 'Jet: 3,000 ft | Prop: 2,500 ft', 'D': 'Jet: 2,000 ft | Prop: 2,000 ft'}, "correct": "A", "timeout": 300, "image": None},
    {"id": 5, "text": "You are flying eastbound (heading between 000° and 179°) under IFR rules. According to standard semicircular rules for cruising altitudes, which of the following is the correct flight level?", "options": {'A': 'FL320', 'B': 'FL335', 'C': 'FL330', 'D': 'FL340'}, "correct": "C", "timeout": 180, "image": None},
    {"id": 6, "text": "Match the aircrafts with the correct option:", "options": {'A': 'A->1, B->2, C->3, D->3', 'B': 'A->3, B->1, C->2, D->1', 'C': 'A->3, B->1, C->1, D->2', 'D': 'A->2, B->3, C->1, D->1'}, "correct": "C", "timeout": 300, "image": "https://cdn.discordapp.com/attachments/1326629089121144832/1402584441423007815/image.png?ex=6894720d&is=6893208d&hm=a66941af60509a03451c3014139304b08eead24ad3500ed3a8fceb8b069614b9&"},
    {"id": 7, "text": "You are approaching OERK runway 33R as shown in the figure and ATC is not present there. What will be the order of your UNICOM announcements according to the figure?", "options": {'A': 'Inbound -> right base -> Right downwind -> final', 'B': 'Inbound -> left base -> left downwind -> final', 'C': 'Inbound -> right Downwind -> Right base -> final', 'D': 'right downwind -> right base -> Inbound -> final'}, "correct": "C", "timeout": 300, "image": "https://cdn.discordapp.com/attachments/1326629089121144832/1402591034558517248/IMG_20250806_152120.jpg?ex=68947831&is=689326b1&hm=806f1988cba4b4085e5dc99f2931189df980267d5bc9a71caedbcfaecc60738b&"},
    {"id": 8, "text": "To ensure safe and realistic operation in Infinite Flight, at what ground speed should you typically disengage reverse thrust?", "options": {'A': '30 knots', 'B': '45 knots', 'C': '50 knots', 'D': '60 knots'}, "correct": "D", "timeout": 180, "image": None},
    {"id": 9, "text": "Based on the diagram, choose the option that correctly identifies the numbered positions of the traffic pattern.", "options": {'A': '1-Downwind, 2-Base, 3-Upwind, 4-Final', 'B': '1-Upwind, 2-Downwind, 3-Crosswind, 5-Base', 'C': '1-Upwind, 2-Crosswind, 3-Downwind, 4-Base', 'D': '2-Final, 3-Base, 4-Downwind, 5-Crosswind'}, "correct": "C", "timeout": 300, "image": "https://cdn.discordapp.com/attachments/1326629089121144832/1402637387200335952/atc-traffic-pattern-1.jpg?ex=6894a35c&is=689351dc&hm=6d0596e3675292f64869f721d4a4a6d84c62d0301bfab954547d8329f6a66ae4&"},
    {"id": 10, "text": 'The "Check In" command should only be used for which of the following frequencies?', "options": {'A': 'Center, Approach', 'B': 'Approach, Tower', 'C': 'Tower, Departure', 'D': 'Center, Departure'}, "correct": "D", "timeout": 180, "image": None},
    {"id": 11, "text": "When approaching an airport with an Approach frequency open, which one is the right procedure assuming flying IFR? (Without prior Center hand-off)", "options": {'A': 'When at or below 18000 ft and within 50 nm to destination, tune into approach frequency...', 'B': 'When at or below 10000 ft and within 40 nm to destination, tune into approach frequency...', 'C': 'When at or below 15000 ft and within 60 nm to destination, tune into approach frequency...', 'D': 'When at or below 12000 ft and within 40 nm to destination, tune into approach frequency...'}, "correct": "A", "timeout": 180, "image": None},
    {"id": 12, "text": "You are approaching OTHH to land. Active frequencies are Tower and Ground, but there is no active Approach frequency. What will be your first request to Tower?", "options": {'A': 'Inbound on the Visual runway', 'B': 'Requesting Transition', 'C': 'Inbound on the ILS runway', 'D': 'Inbound for landing'}, "correct": "D", "timeout": 180, "image": None},
    {"id": 13, "text": "You are approaching OTHH to land. After being cleared for the ILS approach by the Approach controller, you are handed off to Tower. What should your initial call to Tower be?", "options": {'A': 'Inbound on the Visual runway', 'B': 'Requesting Transition', 'C': 'Inbound on the ILS runway', 'D': 'Inbound for landing'}, "correct": "C", "timeout": 180, "image": None},
    {"id": 14, "text": "What is the standard minimum horizontal and vertical separation required between two aircraft under IFR control?", "options": {'A': '3nm and 1000ft', 'B': '5nm and 2000ft', 'C': '3nm and 500ft', 'D': '10nm and 1000ft'}, "correct": "A", "timeout": 180, "image": None},
    {"id": 15, "text": 'What is the correct procedure after receiving the instruction "Taxi to runway, contact Tower when ready"?', "options": {'A': 'Stop at the holding point, request and wait for frequency change from Ground, tune into Tower and request takeoff clearance.', 'B': 'When arriving at the instructed runway, switch to Tower frequency and request takeoff clearance.', 'C': 'If the runway is clear, enter and line-up. Once ready for takeoff, request clearance.', 'D': 'None of these.'}, "correct": "B", "timeout": 180, "image": None},
    {"id": 16, "text": "Which of the following commands is the right one to use when exiting a runway without ATC present?", "options": {'A': 'Clear of all runways', 'B': 'Request frequency change', 'C': 'Request taxi to park', 'D': 'Announce taxi to parking'}, "correct": "A", "timeout": 180, "image": None},
    {"id": 17, "text": "When ready for pushback and the airport is busy, which of the following procedures is the most advised?", "options": {'A': 'Without checking surroundings, request pushback.', 'B': "Check surroundings, it doesn't matter if there is an aircraft blocking the taxiway.", 'C': 'Check surroundings, if the taxiway is clear for your pushback, request pushback.', 'D': 'Check surroundings, and request pushback immediately, the ATC will handle your request.'}, "correct": "C", "timeout": 180, "image": None},
    {"id": 18, "text": "If you are arriving at OTHH from the west and the wind is reported as 100 at 12 knots, which runway should you plan to land on?", "options": {'A': '34R', 'B': '16L', 'C': 'I will divert', 'D': 'We can land on anyone'}, "correct": "B", "timeout": 180, "image": None},
    {"id": 19, "text": "How many concourses does Hamad Intl' have?", "options": {'A': '4 concourses', 'B': '5 concourses', 'C': '3 concourses', 'D': '6 concourses'}, "correct": "B", "timeout": 180, "image": None},
    {"id": 20, "text": "What is the ceiling of the Airbus A321 aircraft?", "options": {'A': '40,000ft', 'B': '41,000ft', 'C': '42,000ft', 'D': '39,000ft'}, "correct": "D", "timeout": 180, "image": None}
]

# A simple View for each question
class QuestionView(discord.ui.View):
    def __init__(self, author_id: int, timeout: int, options: dict):
        super().__init__(timeout=float(timeout))
        self.author_id = author_id
        self.value = None

        for label in options.keys():
            self.add_item(discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, custom_id=label))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(content="This is not your test.", ephemeral=True, delete_after=5)
            return False
        
        self.value = interaction.data["custom_id"]
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()
        return True

# The Persistent View that is ALWAYS listening for the button click
class TestInitiatorView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Written Test", style=discord.ButtonStyle.primary, custom_id="start_persistent_written_test")
    async def written_test_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        
        cog = interaction.client.get_cog("WrittenTest")
        if not cog:
            await interaction.response.send_message("The test module is not loaded correctly. Please contact an admin.", ephemeral=True)
            return

        user = interaction.user
        if user.id in cog.active_tests:
            await interaction.response.send_message(content="You already have a test in progress!", ephemeral=True)
            return

        await interaction.response.defer()

        # Disable the button on the original message to prevent double-clicks
        disabled_view = discord.ui.View()
        disabled_view.add_item(discord.ui.Button(label="Test Started", style=discord.ButtonStyle.primary, disabled=True))
        await interaction.edit_original_response(view=disabled_view)
        
        cog.active_tests.add(user.id)
        try:
            await cog.run_test_session(interaction)
        finally:
            cog.active_tests.remove(user.id)

class WrittenTest(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_tests = set()

    async def cog_load(self):
        self.bot.add_view(TestInitiatorView())
        print("WrittenTest Cog loaded and Persistent View registered.")

    async def run_test_session(self, interaction: discord.Interaction):
        user = interaction.user
        channel = interaction.channel

        written_test_role = interaction.guild.get_role(WRITTEN_TEST_ROLE_ID)
        if written_test_role and written_test_role not in user.roles:
            try:
                await user.add_roles(written_test_role, reason="Started the written test.")
            except discord.Forbidden:
                await channel.send(content="**Error:** I lack permissions to assign the `Written Test` role.", delete_after=15)
                return
        
        questions = random.sample(population=ALL_QUESTIONS, k=len(ALL_QUESTIONS))
        score = 0
        wrong_answers = []

        await interaction.followup.send(content=f"The test for {user.mention} has started! Good luck.", ephemeral=False)
        await asyncio.sleep(1)

        for i, q_data in enumerate(questions):
            options_text = "\n".join([f"**{lbl}:** {txt}" for lbl, txt in q_data["options"].items()])
            end_time = discord.utils.utcnow() + datetime.timedelta(seconds=q_data["timeout"])
            
            embed = discord.Embed(title=f"Question {i + 1}/{len(questions)}", description=f"{q_data['text']}\n\n{options_text}", color=discord.Color.blue())
            embed.set_footer(text="Time remaining:")
            if q_data.get("image"):
                embed.set_image(url=q_data["image"])
            
            q_view = QuestionView(author_id=user.id, timeout=q_data["timeout"], options=q_data["options"])
            q_message = await channel.send(content=f"Ends <t:{int(end_time.timestamp())}:R>", embed=embed, view=q_view)
            
            await q_view.wait()

            if q_view.value == q_data["correct"]:
                score += 1
                await channel.send(content="✅ Correct!", delete_after=5)
            else:
                wrong_answers.append(q_data["id"])
                correct_answer = q_data['options'][q_data['correct']]
                if q_view.value is None:
                    await channel.send(content=f"❌ Time's up! The correct answer was **{q_data['correct']}**: {correct_answer}", delete_after=10)
                else:
                    await channel.send(content=f"❌ Incorrect. The correct answer was **{q_data['correct']}**: {correct_answer}", delete_after=10)
            
            await q_message.delete()
            await asyncio.sleep(1)

        # --- Test Completion ---
        passed = score >= 16
        recruiter_role = interaction.guild.get_role(RECRUITER_ROLE_ID)
        recruiter_ping = recruiter_role.mention if recruiter_role else "@Recruiter (Role not found)"
        
        result_embed = discord.Embed(title="Written Test Results", color=discord.Color.green() if passed else discord.Color.red())
        result_embed.add_field(name="Examinee", value=user.mention, inline=False)
        result_embed.add_field(name="Score", value=f"{score}/{len(questions)}", inline=True)
        result_embed.add_field(name="Status", value="PASSED" if passed else "FAILED", inline=True)
        
        if wrong_answers:
            wrong_answers.sort()
            result_embed.add_field(name="Incorrect Questions", value=", ".join([f"#{qid}" for qid in wrong_answers]), inline=False)

        await channel.send(content=recruiter_ping, embed=result_embed)

        if passed and written_test_role and written_test_role in user.roles:
            try:
                await user.remove_roles(written_test_role, reason="Passed the written test.")
            except discord.Forbidden:
                await channel.send(content="**Error:** I lack permissions to remove the `Written Test` role.", delete_after=15)

async def setup(bot: commands.Bot):
    await bot.add_cog(WrittenTest(bot))
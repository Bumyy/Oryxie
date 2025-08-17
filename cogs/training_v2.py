# cogs/Training_V2.py created by Ayush 

import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, Modal, TextInput
import asyncio
import random
import datetime
import re

# --- CONFIGURATION ---
TRAINING_CHANNEL_ID = int(os.getenv("TRAINING_CHANNEL_ID"))
RECRUITER_ROLE_ID = int(os.getenv("RECRUITER_ROLE_ID"))
WRITTEN_TEST_ROLE_ID = int(os.getenv("WRITTEN_TEST_ROLE_ID"))
CHRO_ROLE_ID = int(os.getenv("CHRO_ROLE_ID"))
IFATC_ROLE_ID = int(os.getenv("IFATC_ROLE_ID"))
BRAND_LOGO_URL = "https://cdn.discordapp.com/attachments/1214282648009056317/1214283143037845534/IMG_7370.PNG?ex=6899920d&is=6898408d&hm=78467d8776c850ac0181a7a8d1ec02613d1bdbc0a1f4a3e612b8bd7edd073424&"  # replace with real logo URL
BRAND_COLOR_RGB = (100, 0, 49)  # maroon #640031

# --- TEST DATA ---
ALL_QUESTIONS = [
    {"id": 1, "text": "Match the following:", "options": {'A': 'A->1, B->4, C->3, D->2', 'B': 'A->2, B->4, C->3, D->1', 'C': 'A->3, B->4, C->1, D->2', 'D': 'A->3, B->1, C->4, D->2'}, "correct": "D", "timeout": 300, "image": "https://cdn.discordapp.com/attachments/1326629089121144832/1402560900484956160/image.png?ex=68945c20&is=68930aa0&hm=53638a6112de9f391db364d3d529fb05a46f3a3b4c0f3d27b173a1a549fa7fc&"},
    {"id": 2, "text": "What are the maximum **preferred speeds** during taxi according to the User Guide?", "options": {'A': 'Straight line -> 15 knots, Turns -> 15 knots', 'B': 'Straight line -> 35 knots, Turns -> 5 knots', 'C': 'Straight line -> 25 knots, Turns -> 10 knots', 'D': 'Straight line -> 25 knots, Turns -> 5 knots'}, "correct": "C", "timeout": 180, "image": None},
    {"id": 3, "text": "You are Qatari 005CR, you are departing west from OTHH to OERK from runway 34R, what will be your take off announcement?", "options": {'A': 'Qatari 005CR, taking off runway 34R, departing east', 'B': 'Qatari 005CR, taking off runway 34L, departing west', 'C': 'Qatari 005CR, taking off runway 34R, Remaining in pattern', 'D': 'Qatari 005CR, taking off runway 34R, departing west'}, "correct": "D", "timeout": 180, "image": None},
    {"id": 4, "text": "If an airport's elevation is 1,500 feet, what would typically be the pattern work altitude for jet aircraft and propeller aircraft (props) in MSL?", "options": {'A': 'Jet: 2,500 ft | Prop: 2,000 ft', 'B': 'Jet: 2,000 ft | Prop: 1,500 ft', 'C': 'Jet: 3,000 ft | Prop: 2,500 ft', 'D': 'Jet: 2,000 ft | Prop: 2,000 ft'}, "correct": "C", "timeout": 300, "image": None},
    {"id": 5, "text": "You are flying eastbound (heading between 000° and 179°) under IFR rules. According to standard semicircular rules for cruising altitudes, which of the following is the correct flight level?", "options": {'A': 'FL320', 'B': 'FL335', 'C': 'FL330', 'D': 'FL340'}, "correct": "C", "timeout": 180, "image": None},
    {"id": 6, "text": "Match the aircrafts with the correct Cruise speeds:", "options": {'A': 'A->1, B->2, C->3, D->3', 'B': 'A->3, B->1, C->2, D->1', 'C': 'A->3, B->1, C->1, D->2', 'D': 'A->2, B->3, C->1, D->1'}, "correct": "C", "timeout": 300, "image": "https://cdn.discordapp.com/attachments/1326629089121144832/1402584441423007815/image.png?ex=6894720d&is=6893208d&hm=a66941af60509a03451c3014139304b08eead24ad3500ed3a8fceb8b069614b9&"},
    {"id": 7, "text": "You are approaching OERK runway 33R as shown in the figure and ATC is not present there. What will be the order of your UNICOM announcements according to the figure?", "options": {'A': 'Inbound -> right base -> Right downwind -> final', 'B': 'Inbound -> left base -> left downwind -> final', 'C': 'Inbound -> right Downwind -> Right base -> final', 'D': 'right downwind -> right base -> Inbound -> final'}, "correct": "C", "timeout": 300, "image": "https://cdn.discordapp.com/attachments/1326629089121144832/1402591034558517248/IMG_20250806_152120.jpg?ex=68947831&is=689326b1&hm=806f1988cba4b4085e5dc99f2931189df980267d5bc9a71caedbcfaecc60738b&"},
    {"id": 8, "text": "To ensure safe and realistic operation in Infinite Flight, at what ground speed should you typically disengage reverse thrust?", "options": {'A': '30 knots', 'B': '45 knots', 'C': '50 knots', 'D': '60 knots'}, "correct": "D", "timeout": 180, "image": None},
    {"id": 9, "text": "Based on the diagram, choose the option that correctly identifies the numbered positions of the traffic pattern.", "options": {'A': '1-Downwind, 2-Base, 3-Upwind, 4-Final', 'B': '1-Upwind, 2-Downwind, 3-Crosswind, 5-Base', 'C': '1-Upwind, 2-Crosswind, 3-Downwind, 4-Base', 'D': '2-Final, 3-Base, 4-Downwind, 5-Crosswind'}, "correct": "C", "timeout": 300, "image": "https://cdn.discordapp.com/attachments/1326629089121144832/1402637387200335952/atc-traffic-pattern-1.jpg?ex=6894a35c&is=689351dc&hm=6d0596e3675292f64869f721d4a4a6d84c62d0301bfab954547d8329f6a66ae4&"},
    {"id": 10, "text": 'The "Check In" command should only be used for which of the following frequencies?', "options": {'A': 'Center, Approach', 'B': 'Approach, Tower', 'C': 'Tower, Departure', 'D': 'Center, Departure'}, "correct": "D", "timeout": 180, "image": None},
    {"id": 11, "text": "When approaching an airport with an Approach frequency open, which one is the right procedure assuming flying IFR? (Without prior Center hand-off)", "options": {'A': 'When at or below 18000 ft and within 50 nm to destination, tune into approach...', 'B': 'When at or below 10000 ft and within 40 nm to destination, tune into approach...', 'C': 'When at or below 15000 ft and within 60 nm to destination, tune into approach...', 'D': 'When at or below 12000 ft and within 40 nm to destination, tune into approach...'}, "correct": "A", "timeout": 180, "image": None},
    {"id": 12, "text": "You are approaching OTHH to land. Active frequencies are Tower and Ground, but no active Approach. What will be your first request to Tower?", "options": {'A': 'Inbound on the Visual runway', 'B': 'Requesting Transition', 'C': 'Inbound on the ILS runway', 'D': 'Inbound for landing'}, "correct": "D", "timeout": 180, "image": None},
    {"id": 13, "text": "You are approaching OTHH. Approach clears you for the ILS and hands you to Tower. What is your initial call?", "options": {'A': 'Inbound on the Visual runway', 'B': 'Requesting Transition', 'C': 'Inbound on the ILS runway', 'D': 'Inbound for landing'}, "correct": "C", "timeout": 180, "image": None},
    {"id": 14, "text": "What is the standard minimum horizontal and vertical separation between two aircraft under IFR control?", "options": {'A': '3nm and 1000ft', 'B': '5nm and 2000ft', 'C': '3nm and 500ft', 'D': '10nm and 1000ft'}, "correct": "A", "timeout": 180, "image": None},
    {"id": 15, "text": 'What is the correct procedure after receiving the instruction "Taxi to runway, contact Tower when ready"?', "options": {'A': 'Stop at the hold short line, request frequency change, then call Tower.', 'B': 'When at the runway, switch to Tower frequency and request takeoff.', 'C': 'Enter the runway, then request takeoff.', 'D': 'None of these.'}, "correct": "B", "timeout": 180, "image": None},
    {"id": 16, "text": "Which command is the right one to use when exiting a runway on UNICOM?", "options": {'A': 'Clear of all runways', 'B': 'Request frequency change', 'C': 'Request taxi to park', 'D': 'Announce taxi to parking'}, "correct": "A", "timeout": 180, "image": None},
    {"id": 17, "text": "When ready for pushback at a busy airport, which procedure is most advised?", "options": {'A': 'Request pushback without checking.', 'B': "Pushback even if an aircraft is blocking the taxiway.", 'C': 'Check surroundings, if clear, request pushback.', 'D': 'Request pushback immediately and let ATC handle it.'}, "correct": "C", "timeout": 180, "image": None},
    {"id": 18, "text": "If you are arriving at OTHH from the west and the wind is 100 at 12 knots, which runway should you plan to land on?", "options": {'A': '34R', 'B': '16L', 'C': 'I will divert', 'D': 'We can land on anyone'}, "correct": "B", "timeout": 180, "image": None},
    {"id": 19, "text": "How many concourses does Hamad Intl' have?", "options": {'A': '4 concourses', 'B': '5 concourses', 'C': '3 concourses', 'D': '6 concourses'}, "correct": "B", "timeout": 180, "image": None},
    {"id": 20, "text": "What is the ceiling of the Airbus A321 aircraft?", "options": {'A': '40,000ft', 'B': '41,000ft', 'C': '42,000ft', 'D': '39,000ft'}, "correct": "D", "timeout": 180, "image": None}
]

# --- SELF-CONTAINED UI CLASSES ---

class QuestionView(View):
    def __init__(self, author_id: int, timeout: int, options: dict):
        super().__init__(timeout=float(timeout))
        self.author_id = author_id; self.value = None; self.interaction = None
        for label in options.keys(): self.add_item(Button(label=label, style=discord.ButtonStyle.secondary, custom_id=f"answer_{label}"))
        self.add_item(Button(label="Skip to Results", style=discord.ButtonStyle.danger, custom_id="chro_skip_test"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        custom_id = interaction.data.get("custom_id", "")
        if custom_id == "chro_skip_test":
            chro_role = interaction.guild.get_role(CHRO_ROLE_ID)
            if chro_role and chro_role in interaction.user.roles:
                self.value = "skipped"; self.interaction = interaction; self.stop(); return True
            else:
                await interaction.response.send_message("You do not have permission to use this button.", ephemeral=True); return False
        elif custom_id.startswith("answer_"):
            if interaction.user.id != self.author_id:
                await interaction.response.send_message("This is not your test.", ephemeral=True); return False
            self.value = custom_id.split("_")[1]; self.interaction = interaction
            for item in self.children: item.disabled = True
            await interaction.response.edit_message(view=self); self.stop(); return True
        return False

class VerificationModal(Modal, title="Infinite Flight Verification"):
    ifc_username = TextInput(label="IFC Username", required=True)
    def __init__(self, cog: "TrainingCogV2"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        username = self.ifc_username.value
        data = await self.cog.bot.if_api_manager.get_user_stats(discourse_names=[username])
        if data and data.get("errorCode") == 0 and data.get("result"):
            user_info = data["result"][0]
            # IFATC flow: assign IFATC role and skip test
            if user_info.get("atcRank", 0) > 1:
                # assign IFATC role if available
                ifatc_role = interaction.guild.get_role(IFATC_ROLE_ID)
                try:
                    if ifatc_role and ifatc_role not in interaction.user.roles:
                        await interaction.user.add_roles(ifatc_role)
                except Exception:
                    # fail silently if role assignment doesn't work (permissions)
                    pass
                # send the IFATC-specific welcome message
                msg = (
                    f"Welcome, {interaction.user.mention}! Your IFATC status has been confirmed and the IFATC role has been assigned to you. "
                    "You do not need to enter the Qatari Virtual Cadet Training Program. "
                    "To get started, please Provide **5 Callsigns** in priority order between ** 101-499 ** and ping @recruiter."
                )
                await interaction.followup.send(msg)
                return
            # Non-IFATC: send briefing embed
            color = discord.Color.from_rgb(*BRAND_COLOR_RGB)
            embed = discord.Embed(
                title="Pilot Entry Test Briefing",
                description=(
                    "Welcome to Qatari Virtual. Before you begin the written test, please study the following resources carefully."
                ),
                color=color
            )
            embed.add_field(
                name="Required Reading",
                value=( "• [Qatari Virtual Pilot Guidelines](https://drive.google.com/file/d/1FoyWliMfZ6AromV3qoqxbmy2RN_YU-WL/view?usp=share_link)\n"
                        "• [A320 Family Aircraft Guide](https://drive.google.com/file/d/1W3ROBvPRvBHwS6zGETGKjUidtvqf7QPY/view?usp=sharing)\n"
                        "• [Infinite Flight Flying Guide](https://infiniteflight.com/guide/flying-guide)"
                                ),
                 inline=False
)
            embed.add_field(
                name="📖 TEST RULES:",
                value=( "• 20 multiple choice questions\n"
                        "• Each question has a time limit\n"
                        "• You need 16/20 (80%) to pass\n"
                        "• Questions cover aviation knowledge\n"
                        "• No external help allowed\n"
                        "• You can retake if you fail (with recruiter approval)"
                                ),
                 inline=False
)
            embed.add_field(
                name="Questions?",
                value="If you have any questions about the test or the materials, please ping a recruiter.",
                inline=False
            )
            # attach start button view
            await interaction.followup.send(
                f"Hello {interaction.user.mention}. Your account has been verified. Your next step is the written test.",
                embed=embed,
                view=StartTestView(cog=self.cog, author_id=interaction.user.id)
            )
        else:
            await interaction.followup.send("Could not find a user with that IFC username.")

class InitialVerificationView(View):
    def __init__(self, cog: "TrainingCogV2"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Verify Account", style=discord.ButtonStyle.green, custom_id="v2_final_verify")
    async def verify_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(VerificationModal(cog=self.cog))

class StartTestView(View):
    def __init__(self, cog: "TrainingCogV2", author_id: int):
        super().__init__(timeout=86400)
        self.cog = cog
        self.author_id = author_id

    @discord.ui.button(label="Start Written Test", style=discord.ButtonStyle.primary, custom_id="v2_final_start_test")
    async def start_button(self, interaction: discord.Interaction, button: Button):
        await self.cog.handle_start_test(interaction)
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This is not your test button.", ephemeral=True)
            return False
        return True

class AuthorizeRetestView(View):
    def __init__(self, cog: "TrainingCogV2", trainee_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.trainee_id = trainee_id
    
    @discord.ui.button(label="Authorize Retest", style=discord.ButtonStyle.secondary, custom_id="v2_final_auth_retest")
    async def authorize_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        recruiter_role = interaction.guild.get_role(RECRUITER_ROLE_ID)
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.followup.send("Only recruiters can authorize a retest.", ephemeral=True)
            return
        trainee = interaction.guild.get_member(self.trainee_id)
        if not trainee:
            await interaction.followup.send("Could not find the original trainee.", ephemeral=True)
            return
        
        # Edit the original message to indicate authorization
        await interaction.edit_original_response(content=f"Retest authorized by {interaction.user.mention}.", view=None)

        # Create persistent TakeRetestView and register it so it stays active while bot runs
        retest_view = TakeRetestView(cog=self.cog, author_id=self.trainee_id)
        try:
            # register the view so the button callback is available
            self.cog.bot.add_view(retest_view)
        except Exception:
            pass

        # Inform the trainee with recruiter mention and a clear instruction
        await interaction.followup.send(
            content=f"{trainee.mention}, you have been authorized for a retest by {interaction.user.mention}. You may begin whenever you are ready. Click the button below to start.",
            view=retest_view
        )
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        recruiter_role = interaction.guild.get_role(RECRUITER_ROLE_ID)
        if recruiter_role and recruiter_role in interaction.user.roles:
            return True
        await interaction.response.send_message("Only recruiters can authorize a retest.", ephemeral=True)
        return False

class TakeRetestView(View):
    def __init__(self, cog: "TrainingCogV2", author_id: int):
        super().__init__(timeout=86400)
        self.cog = cog
        self.author_id = author_id

    @discord.ui.button(label="Take Retest", style=discord.ButtonStyle.success, custom_id="v2_final_take_retest")
    async def retest_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        button.disabled = True
        await interaction.edit_original_response(view=self)
        asyncio.create_task(self.cog.run_test_session(interaction))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

# --- THE MAIN COG ---
class TrainingCogV2(commands.Cog, name="Training V2"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_tests = {}
        # Register persistent views that must be available while bot runs
        self.bot.add_view(InitialVerificationView(self))

    @app_commands.command(name="forcetest", description="Manually start the training flow for a user.")
    @app_commands.checks.has_permissions(administrator=True)
    async def forcetest(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.send_message(f"Forcing training flow for {user.mention}...", ephemeral=True)
        await self.start_training_flow(user)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self.start_training_flow(member)

    async def start_training_flow(self, member: discord.Member):
        training_channel = self.bot.get_channel(TRAINING_CHANNEL_ID)
        if not training_channel: return

        # New multi-line initial message with status and trainer mention
        recruiter_role = training_channel.guild.get_role(RECRUITER_ROLE_ID) if training_channel.guild else None
        trainer_mention = recruiter_role.mention if recruiter_role else "@Recruiter"
        initial_text = (
            f"✈️ Initial Training for {member.mention}\n"
            f"Status: In progress\n"
            f"Trainer: {trainer_mention}"
        )
        initial_message = await training_channel.send(initial_text)
        thread = await initial_message.create_thread(name=f"Training for {member.display_name}")
        color = discord.Color.from_rgb(*BRAND_COLOR_RGB)
        embed = discord.Embed(
            title="Welcome to the Qatari Virtual Discord Server!",
            description="Please click below to verify your account.",
            color=color
        )
        if BRAND_LOGO_URL:
            embed.set_thumbnail(url=BRAND_LOGO_URL)
        await thread.send(embed=embed, view=InitialVerificationView(self))

    async def handle_start_test(self, interaction: discord.Interaction):
        user = interaction.user
        if user.id in self.active_tests:
            await interaction.response.send_message("You already have a test in progress!", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        # disable the start button on the original message
        try:
            view = View.from_message(interaction.message)
            button = discord.utils.get(view.children, custom_id=interaction.data.get("custom_id", ""))
            if button:
                button.disabled = True
                await interaction.edit_original_response(view=view)
        except Exception:
            pass
        
        asyncio.create_task(self.run_test_session(interaction))

    async def run_test_session(self, interaction: discord.Interaction):
        user = interaction.user; channel = interaction.channel
        try:
            self.active_tests[user.id] = {"score": 0, "wrong_answers": [], "messages": []}
            written_test_role = interaction.guild.get_role(WRITTEN_TEST_ROLE_ID)
            if written_test_role and written_test_role not in user.roles:
                await user.add_roles(written_test_role)
            questions = random.sample(population=ALL_QUESTIONS, k=len(ALL_QUESTIONS))
            start_msg = await interaction.followup.send(f"The test for {user.mention} is starting!", ephemeral=False)
            self.active_tests[user.id]["messages"].append(start_msg)
            for i, q_data in enumerate(questions):
                if user.id not in self.active_tests: break
                if q_data.get("image"):
                    img_msg = await channel.send(q_data["image"]); self.active_tests[user.id]["messages"].append(img_msg)
                options_text = "\n".join([f"**{lbl}:** {txt}" for lbl, txt in q_data["options"].items()])
                end_time = discord.utils.utcnow() + datetime.timedelta(seconds=q_data["timeout"])
                embed = discord.Embed(title=f"Question {i + 1}/{len(questions)}", description=f"{q_data['text']}\n\n{options_text}", color=discord.Color.blue())
                msg_content = f"Question closes {discord.utils.format_dt(end_time, style='R')}"
                q_view = QuestionView(user.id, q_data["timeout"], q_data["options"])
                q_msg = await channel.send(content=msg_content, embed=embed, view=q_view)
                self.active_tests[user.id]["messages"].append(q_msg)
                await q_view.wait()
                if q_view.value == "skipped":
                    await q_view.interaction.response.send_message("Test has been ended by a staff member.", ephemeral=True)
                    for j, rem_q in enumerate(questions[i:], start=i+1): 
                        self.active_tests[user.id]["wrong_answers"].append((j, rem_q["id"]))
                    break
                
                if q_view.value == q_data["correct"]:
                    self.active_tests[user.id]["score"] += 1
                    res_msg = await channel.send("✅ Correct!")
                else:
                    # Store tuple: (question_order, original_question_id)
                    self.active_tests[user.id]["wrong_answers"].append((i + 1, q_data["id"]))
                    correct_ans = q_data['options'][q_data['correct']]
                    res_msg = await channel.send(f"❌ Incorrect. The correct answer was **{q_data['correct']}**: {correct_ans}")
                
                self.active_tests[user.id]["messages"].append(res_msg)
                await asyncio.sleep(3)
            
            await self.finalize_test(interaction, self.active_tests[user.id])
     
        except Exception:
            print(f"--- CRITICAL ERROR IN TEST SESSION FOR {user.name} ---"); import traceback; traceback.print_exc()
            await channel.send(f"An unexpected error occurred for {user.mention}. The test has been aborted.")
        finally:
            if user.id in self.active_tests: del self.active_tests[user.id]

    async def finalize_test(self, interaction: discord.Interaction, test_data: dict):
        user = interaction.user
        channel = interaction.channel
        
        passed = test_data["score"] >= 16
        recruiter_ping = f"<@&{RECRUITER_ROLE_ID}>"
        
        embed = discord.Embed(title="Written Test Results", color=discord.Color.green() if passed else discord.Color.red())
        embed.add_field(name="Examinee", value=user.mention, inline=False)
        embed.add_field(name="Score", value=f"{test_data['score']}/{len(ALL_QUESTIONS)}", inline=True)
        embed.add_field(name="Status", value="PASSED" if passed else "FAILED", inline=True)
       
        
        if test_data["wrong_answers"]:
            test_data["wrong_answers"].sort(key=lambda x: x[0])  # Sort by order
            embed.add_field(name="Incorrect Questions", value=", ".join([f"Q{order} (#{qid})" for order, qid in test_data["wrong_answers"]]), inline=False)

        await channel.send(content=recruiter_ping, embed=embed)

        written_test_role = interaction.guild.get_role(WRITTEN_TEST_ROLE_ID)
        if passed and written_test_role and written_test_role in user.roles:
            await user.remove_roles(written_test_role)
            # FOLLOW-UP PASS MESSAGE
            follow_msg = (
                f"Congratulations, {user.mention}! You have passed the written test. "
                "Your next step is to choose your callsign and please provide us 5 callsigns in priority order . "
                "between  ** 101 - 499 ** , A recruiter will assign you one of callsign shortly ."
            )
            await channel.send(follow_msg)
        elif not passed:
            # FAIL MESSAGE and retest prompt (recruiter will authorize)
            fail_msg = (
                f"Unfortunately, you did not meet the passing score. Please take some time to review the guides and discuss with a recruiter before attempting a retest. "
                "If you need help, ping a recruiter or request guidance."
            )
            await channel.send(fail_msg, view=AuthorizeRetestView(cog=self, trainee_id=user.id))
        
        # schedule cleanup in background
        async def cleanup_messages(messages, delay):
            await asyncio.sleep(delay)
            try:
                await channel.delete_messages(messages)
            except:
                pass

        asyncio.create_task(cleanup_messages(test_data["messages"], 300))

async def setup(bot: commands.Bot):
    if not hasattr(bot, 'if_api_manager'):
        print("ERROR: InfiniteFlightAPIManager is not attached to the bot. TrainingCogV2 not loaded.")
        return
    await bot.add_cog(TrainingCogV2(bot))

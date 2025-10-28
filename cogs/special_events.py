import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
import random
import asyncio
import datetime
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

EVENT_CONFIG = {
    "EVENT_NAME": "halloween_2025",
    "CURRENCY_NAME": "Candy",
    "CURRENCY_EMOJI": "🍬",
    "MISSION_FLIGHTNUM": "HWN",
    "DROP_REWARD_MIN": 50,
    "DROP_REWARD_MAX": 150,
    "DROP_LIMIT": 5,
    "EMBED_COLOR": 0xff7518,
}

class CandyDropView(discord.ui.View):
    def __init__(self, cog_instance):
        super().__init__(timeout=None)
        self.cog = cog_instance

    @discord.ui.button(label="🎃 Claim Your Candy", style=discord.ButtonStyle.primary, custom_id="candy_drop_claim")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        message_id = interaction.message.id
        pilot_result = await self.cog.bot.pilots_model.identify_pilot(interaction.user)
        
        if not pilot_result['success']:
            await interaction.followup.send(pilot_result['error_message'], ephemeral=True)
            return
            
        pilot_record = pilot_result['pilot_data']
        pilot_id = pilot_record['id']
        callsign = pilot_record['callsign']

        if await self.cog.bot.event_transaction_model.check_duplicate(pilot_id, f"Candy Drop Claim: #{message_id}"):
            await interaction.followup.send("You have already claimed this drop!", ephemeral=True)
            return
            
        if await self.cog.bot.event_transaction_model.check_cooldown(pilot_id, 'Candy Drop Claim:%'):
            try:
                last_transaction = await self.cog.bot.event_transaction_model.get_last_transaction(pilot_id, 'Candy Drop Claim:%')
                if last_transaction:
                    now = datetime.datetime.utcnow()
                    last_time = last_transaction['transaction_date']
                    elapsed = (now - last_time).total_seconds()
                    remaining = (20 * 3600) - elapsed
                    hours = int(remaining // 3600)
                    minutes = int((remaining % 3600) // 60)
                    await interaction.followup.send(f"You've already enjoyed your treat recently! Try again in **{hours}h {minutes}m** ⏳", ephemeral=True)
                else:
                    await interaction.followup.send("You've already enjoyed your treat recently! Try again after 20 hours ⏳", ephemeral=True)
            except Exception as e:
                logger.error(f"Error checking cooldown: {e}")
                await interaction.followup.send("You've already enjoyed your treat recently! Try again after 20 hours ⏳", ephemeral=True)
            return
            
        claim_count = await self.cog.bot.event_transaction_model.count_claims(f"Candy Drop Claim: #{message_id}")
        if claim_count >= EVENT_CONFIG["DROP_LIMIT"]:
            try:
                button.disabled = True
                button.label = "Fully Claimed"
                await interaction.message.edit(view=self)
            except (discord.HTTPException, discord.Forbidden) as e:
                logger.error(f"Failed to disable button: {e}")
            await interaction.followup.send("This Candy Drop has already been claimed! Watch for the next one 👀", ephemeral=True)
            return

        try:
            reward_amount = random.randint(EVENT_CONFIG["DROP_REWARD_MIN"], EVENT_CONFIG["DROP_REWARD_MAX"])
            
            if await self.cog.bot.event_transaction_model.add_transaction(pilot_id, reward_amount, f"Candy Drop Claim: #{message_id}"):
                pilot_data = {'callsign': callsign, 'discordid': pilot_record.get('discordid')}
                await self.cog.log_candy_transaction(pilot_data, reward_amount, f"Candy Drop Claim: #{message_id}", interaction.user.mention)
                await interaction.followup.send(f"You grabbed the Candy Drop! +{reward_amount} Candy added to your balance 🍬", ephemeral=True)
            else:
                await interaction.followup.send("Database error occurred. Please try again later.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error processing candy claim: {e}")
            await interaction.followup.send("An error occurred while processing your claim.", ephemeral=True)

class SpecialEventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tasks_started = False
        
        try:
            self.general_chat_channel_id = int(os.getenv("GENERAL_CHAT_CHANNEL_ID"))
        except (TypeError, ValueError):
            self.general_chat_channel_id = None
            
        try:
            self.candy_logs_channel_id = int(os.getenv("CANDY_LOGS_CHANNEL_ID"))
        except (TypeError, ValueError):
            self.candy_logs_channel_id = None

    async def log_candy_transaction(self, pilot_data, amount, reason, user_mention=None, flight_info=None):
        if not self.candy_logs_channel_id:
            return
            
        channel = self.bot.get_channel(self.candy_logs_channel_id)
        if not channel:
            return
            
        pilot_display = f"<@{pilot_data['discordid']}>" if pilot_data.get('discordid') else pilot_data['callsign']
        
        message_parts = [
            f"🍬 **Candy Transaction**",
            f"**Pilot:** {pilot_display}",
            f"**Amount:** {amount:+} {EVENT_CONFIG['CURRENCY_NAME']}",
            f"**Reason:** {reason}"
        ]
        
        if flight_info:
            message_parts.append(f"**Flight:** {flight_info['departure']} → {flight_info['arrival']}")
        if user_mention:
            message_parts.append(f"**User:** {user_mention}")
            
        try:
            await channel.send("\n".join(message_parts))
        except (discord.HTTPException, discord.Forbidden) as e:
            logger.error(f"Failed to log candy transaction: {e}")

    def cog_unload(self):
        self.tasks_started = False
        if hasattr(self, 'pirep_checker') and self.pirep_checker.is_running():
            self.pirep_checker.cancel()
        if hasattr(self, 'candy_drop_scheduler') and self.candy_drop_scheduler.is_running():
            self.candy_drop_scheduler.cancel()

    @app_commands.command(name="halloween", description="Manage the Halloween event.")
    @app_commands.describe(
        action="What to do",
        callsign="Pilot callsign (for add/remove/check)",
        amount="Amount of candy (for add/remove)",
        reason="Reason for transaction (for add/remove)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Start Event", value="start"),
        app_commands.Choice(name="Stop Event", value="stop"),
        app_commands.Choice(name="Add Candy", value="add"),
        app_commands.Choice(name="Remove Candy", value="remove"),
        app_commands.Choice(name="Check Balance", value="check"),
        app_commands.Choice(name="Test Drop", value="test_drop"),
        app_commands.Choice(name="View Records", value="records"),
        app_commands.Choice(name="Poll PIREPs", value="poll_pireps")
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def halloween(self, interaction: discord.Interaction, action: str, callsign: str = None, amount: int = None, reason: str = None):
        if action == "start":
            if self.tasks_started:
                await interaction.response.send_message("Event tasks are already running.", ephemeral=True)
                return
            self.tasks_started = True
            self.pirep_checker.start()
            self.candy_drop_scheduler.start()
            await interaction.response.send_message("✅ Halloween event started!", ephemeral=True)
            
        elif action == "stop":
            if not self.tasks_started:
                await interaction.response.send_message("Event tasks are not currently running.", ephemeral=True)
                return
            self.tasks_started = False
            self.pirep_checker.cancel()
            self.candy_drop_scheduler.cancel()
            await interaction.response.send_message("🛑 Halloween event stopped.", ephemeral=True)
            
        elif action in ["add", "remove", "check"]:
            if not callsign:
                await interaction.response.send_message("Callsign is required for this action.", ephemeral=True)
                return
                
            pilot_record = await self.bot.pilots_model.get_pilot_by_callsign(callsign)
            if not pilot_record:
                await interaction.response.send_message(f"Could not find pilot `{callsign}`.", ephemeral=True)
                return
                
            pilot_id = pilot_record['id']
            
            if action == "check":
                balance = await self.bot.event_transaction_model.get_balance(pilot_id)
                await interaction.response.send_message(f"🔎 `{callsign}` has **{balance} {EVENT_CONFIG['CURRENCY_NAME']}**.", ephemeral=True)
                
            elif action in ["add", "remove"]:
                if not amount or not reason:
                    await interaction.response.send_message("Amount and reason are required.", ephemeral=True)
                    return
                    
                final_amount = abs(amount) if action == "add" else -abs(amount)
                
                if await self.bot.event_transaction_model.add_transaction(pilot_id, final_amount, reason):
                    pilot_data = {'callsign': callsign, 'discordid': None}
                    await self.log_candy_transaction(pilot_data, final_amount, f"Admin {action}: {reason}", interaction.user.mention)
                    
                    action_word = "Added" if action == "add" else "Removed"
                    await interaction.response.send_message(f"✅ {action_word} {abs(amount)} {EVENT_CONFIG['CURRENCY_NAME']} {'to' if action == 'add' else 'from'} `{callsign}`.", ephemeral=True)
                else:
                    await interaction.response.send_message("❌ Database error occurred.", ephemeral=True)
                
        elif action == "test_drop":
            if self.general_chat_channel_id:
                channel = self.bot.get_channel(self.general_chat_channel_id)
                if channel:
                    embed = discord.Embed(
                        title="🍭 Candy Drop Alert!",
                        description=f"A sweet surprise has appeared in the skies! {EVENT_CONFIG['CURRENCY_EMOJI']}\n"
                                  f"The first {EVENT_CONFIG['DROP_LIMIT']} pilots to tap below will receive **50-150 Candy**!\n\n"
                                  "⏰ Once you claim, you must wait 20 hours before claiming again —\n"
                                  "let's make sure everyone gets a chance to enjoy the treats!",
                        color=EVENT_CONFIG["EMBED_COLOR"]
                    )
                    embed.set_footer(text="QRV Halloween 2025 | Candy Drop System")
                    embed.timestamp = discord.utils.utcnow()
                    
                    view = CandyDropView(self)
                    message = await channel.send(embed=embed, view=view)
                    view.message = message
                    await interaction.response.send_message("🍬 Test candy drop sent!", ephemeral=True)
                else:
                    await interaction.response.send_message("Drop channel not found.", ephemeral=True)
            else:
                await interaction.response.send_message("GENERAL_CHAT_CHANNEL_ID not configured.", ephemeral=True)
                
        elif action == "records":
            await interaction.response.defer(ephemeral=True)
            records = await self.bot.event_transaction_model.get_all_records()
            
            if not records:
                await interaction.followup.send("No event transactions found.", ephemeral=True)
                return
            
            batch_size = 50
            total_records = len(records)
            
            for i in range(0, total_records, batch_size):
                batch = records[i:i + batch_size]
                batch_num = (i // batch_size) + 1
                total_batches = (total_records + batch_size - 1) // batch_size
                
                message_lines = [f"🍬 **Event Records - Batch {batch_num}/{total_batches}**\n"]
                
                for record in batch:
                    callsign = record['callsign'] or f"ID:{record['pilot_id']}"
                    date_str = record['transaction_date'].strftime('%Y-%m-%d %H:%M') if record['transaction_date'] else 'No Date'
                    message_lines.append(
                        f"ID:{record['id']} | {callsign} | PilotID:{record['pilot_id']} | {record['event_name']} | {record['amount']:+} {record['currency_name']} | {record['reason']} | {date_str}"
                    )
                
                message_lines.append(f"\n**Total Records:** {total_records}")
                await interaction.followup.send("\n".join(message_lines), ephemeral=True)
            
        elif action == "poll_pireps":
            await interaction.response.defer(ephemeral=True)
            
            try:
                accepted_pireps = await self.bot.pireps_model.get_accepted_pireps()
                rewards_given = 0
                
                for pirep in accepted_pireps:
                    if pirep.get('flightnum') == EVENT_CONFIG["MISSION_FLIGHTNUM"]:
                        if await self.bot.event_transaction_model.process_pirep_reward(pirep, self.bot.pilots_model):
                            pilot_data = await self.bot.pilots_model.get_pilot_by_id(pirep['pilotid'])
                            if pilot_data:
                                flight_info = {
                                    'departure': pirep.get('departure', 'Unknown'),
                                    'arrival': pirep.get('arrival', 'Unknown')
                                }
                                await self.log_candy_transaction(pilot_data, 1, f"PIREP Reward: #{pirep['pirep_id']}", flight_info=flight_info)
                                rewards_given += 1
                                
                await interaction.followup.send(f"✅ PIREP polling complete! Awarded {rewards_given} Halloween PIREP rewards.", ephemeral=True)
                
            except Exception as e:
                await interaction.followup.send(f"❌ Error polling PIREPs: {e}", ephemeral=True)

    @halloween.error
    async def on_halloween_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need administrator permissions to use this command.", ephemeral=True)
        else:
            await interaction.response.send_message(f"An error occurred: {error}", ephemeral=True)

    @app_commands.command(name="balance", description="Check your event currency balance.")
    async def balance(self, interaction: discord.Interaction):
        pilot_result = await self.bot.pilots_model.identify_pilot(interaction.user)
        
        if not pilot_result['success']:
            await interaction.response.send_message(pilot_result['error_message'], ephemeral=True)
            return
            
        pilot_record = pilot_result['pilot_data']
        pilot_id = pilot_record['id']
        
        balance = await self.bot.event_transaction_model.get_balance(pilot_id)
        top3_data = await self.bot.event_transaction_model.get_top_holders()
        
        message_parts = [
            f"**{pilot_record['callsign']}**, you currently have **{balance} {EVENT_CONFIG['CURRENCY_NAME']}** {EVENT_CONFIG['CURRENCY_EMOJI']}.",
            "",
            "🏆 **Top 3 Candy Holders:**"
        ]
        
        if top3_data:
            for i, holder in enumerate(top3_data, 1):
                emoji = ["🥇", "🥈", "🥉"][i-1]
                message_parts.append(f"{emoji} {holder['callsign']}: {holder['total_candy']} {EVENT_CONFIG['CURRENCY_NAME']}")
        else:
            message_parts.append("No candy holders yet!")
            
        await interaction.response.send_message("\n".join(message_parts), ephemeral=True)

    @tasks.loop(minutes=30)
    async def pirep_checker(self):
        if not self.tasks_started:
            return
            
        try:
            accepted_pireps = await self.bot.pireps_model.get_accepted_pireps()
            if not accepted_pireps:
                return
        except Exception as e:
            logger.error(f"Error in pirep_checker: {e}")
            return
        
        processed_count = 0
        for pirep in accepted_pireps:
            try:
                if pirep.get('flightnum') == EVENT_CONFIG["MISSION_FLIGHTNUM"]:
                    if await self.bot.event_transaction_model.process_pirep_reward(pirep, self.bot.pilots_model):
                        pilot_data = await self.bot.pilots_model.get_pilot_by_id(pirep['pilotid'])
                        if pilot_data:
                            flight_info = {
                                'departure': pirep.get('departure', 'Unknown'),
                                'arrival': pirep.get('arrival', 'Unknown')
                            }
                            await self.log_candy_transaction(pilot_data, 1, f"PIREP Reward: #{pirep['pirep_id']}", flight_info=flight_info)
                            processed_count += 1
                            
                if processed_count >= 10:
                    break
            except Exception as e:
                logger.error(f"Error processing pirep {pirep.get('pirep_id', 'unknown')}: {e}")
                continue

    @tasks.loop(count=1)
    async def candy_drop_scheduler(self):
        max_iterations = 10000
        iteration_count = 0
        
        while self.tasks_started and iteration_count < max_iterations:
            try:
                wait_seconds = random.uniform(2 * 3600, 6 * 3600)
                await asyncio.sleep(wait_seconds)

                if not self.tasks_started:
                    break
                    
                if self.general_chat_channel_id:
                    channel = self.bot.get_channel(self.general_chat_channel_id)
                    if channel:
                        try:
                            embed = discord.Embed(
                                title="🍭 Candy Drop Alert!",
                                description=f"A sweet surprise has appeared in the skies! {EVENT_CONFIG['CURRENCY_EMOJI']}\n"
                                          f"The first {EVENT_CONFIG['DROP_LIMIT']} pilots to tap below will receive **50-150 Candy**!\n\n"
                                          "⏰ Once you claim, you must wait 20 hours before claiming again —\n"
                                          "let's make sure everyone gets a chance to enjoy the treats!",
                                color=EVENT_CONFIG["EMBED_COLOR"]
                            )
                            embed.set_footer(text="QRV Halloween 2025 | Candy Drop System")
                            embed.timestamp = discord.utils.utcnow()
                            
                            view = CandyDropView(self)
                            message = await channel.send(embed=embed, view=view)
                            view.message = message
                        except (discord.HTTPException, discord.Forbidden) as e:
                            logger.error(f"Failed to send candy drop: {e}")
                            
                iteration_count += 1
                
            except Exception as e:
                logger.error(f"Error in candy_drop_scheduler: {e}")
                await asyncio.sleep(60)
                iteration_count += 1

    @pirep_checker.before_loop
    @candy_drop_scheduler.before_loop
    async def before_tasks(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    cog = SpecialEventsCog(bot)
    await bot.add_cog(cog)
    bot.add_view(CandyDropView(cog))
    
    if not cog.pirep_checker.is_running():
        cog.tasks_started = True
        cog.pirep_checker.start()
        
    if not cog.candy_drop_scheduler.is_running():
        cog.candy_drop_scheduler.start()
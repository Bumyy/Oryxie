import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, Set, List
import os
from dotenv import load_dotenv

load_dotenv()

CALLSIGN_PREFIX = "QRV"
RECRUITER_ROLE_ID = int(os.getenv("RECRUITER_ROLE_ROLE_ID"))

async def is_recruiter_or_has_admin_perm(interaction: discord.Interaction) -> bool:
    user = interaction.user
    
    if user.guild_permissions.administrator:
        return True

    for role in user.roles:
        if role.id == RECRUITER_ROLE_ID:
            return True
    
    return False


class CallsignFinderCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "❌ **Permission Denied**\nYou must either have the `Recruiter` role or `Administrator` permission to use this command.",
                ephemeral=True
            )
        else:
            response_message = "An unexpected error occurred. Please contact an administrator."
            if not interaction.response.is_done():
                await interaction.response.send_message(response_message, ephemeral=True)
            else:
                await interaction.followup.send(response_message, ephemeral=True)

    async def _find_available_in_range(self, start: int, end: int, taken_callsigns: Set[str]) -> List[str]:
        available = []
        for i in range(start, end + 1):
            formatted_callsign = f"{CALLSIGN_PREFIX}{i:03d}"
            if formatted_callsign not in taken_callsigns:
                available.append(formatted_callsign)

        return available

    @app_commands.command(name="callsign", description="Check callsign availability")
    @app_commands.check(is_recruiter_or_has_admin_perm)
    async def callsign(self, interaction: discord.Interaction):
        view = CallsignSearchView(self)
        await interaction.response.send_message("**Choose your callsign search type:**", view=view, ephemeral=True)


class CallsignSearchView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=300)
        self.cog = cog

    @discord.ui.select(
        placeholder="Choose search type...",
        options=[
            discord.SelectOption(label="Check Single Callsign", value="single", description="Check if one specific callsign is available"),
            discord.SelectOption(label="Check Multiple Callsigns", value="multiple", description="Check up to 5 callsigns at once"),
            discord.SelectOption(label="Quick Categories", value="category", description="Browse preset callsign ranges"),
            discord.SelectOption(label="Custom Range", value="range", description="Define your own number range")
        ]
    )
    async def search_type_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        if select.values[0] == "single":
            modal = SingleCallsignModal(self.cog)
            await interaction.response.send_modal(modal)
        elif select.values[0] == "multiple":
            modal = MultipleCallsignModal(self.cog)
            await interaction.response.send_modal(modal)
        elif select.values[0] == "category":
            view = CategorySelectView(self.cog)
            await interaction.response.edit_message(content="**Choose a callsign category:**", view=view)
        elif select.values[0] == "range":
            modal = CustomRangeModal(self.cog)
            await interaction.response.send_modal(modal)


class CategorySelectView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=300)
        self.cog = cog

    @discord.ui.select(
        placeholder="Choose category...",
        options=[
            discord.SelectOption(label="Staff (5-20)", value="staff"),
            discord.SelectOption(label="Ex-Staff (21-30)", value="ex_staff"),
            discord.SelectOption(label="Sapphire (31-40)", value="sapphire"),
            discord.SelectOption(label="Ruby (41-100)", value="ruby")
        ]
    )
    async def category_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer()
        
        ranges = {
            "staff": (5, 20, "Staff"),
            "ex_staff": (21, 30, "Ex-Staff"),
            "sapphire": (31, 40, "Sapphire"),
            "ruby": (41, 100, "Ruby")
        }
        
        category = select.values[0]
        start_range, end_range, title = ranges[category]
        taken_callsigns = await self.cog.bot.pilots_model.get_all_callsigns()
        available_list = await self.cog._find_available_in_range(start_range, end_range, taken_callsigns)
        
        message = f"**Available {title} Callsigns ({CALLSIGN_PREFIX}{start_range:03d} - {CALLSIGN_PREFIX}{end_range:03d})**"
        
        if not available_list:
            message += "\n\n❌ No available callsigns in this range."
        else:
            output_str = ", ".join(f"`{c}`" for c in available_list)
            message += f"\n\n{output_str}"
        
        # Add taken callsigns list for Staff category only
        if category == "staff":
            taken_in_range = []
            for i in range(start_range, end_range + 1):
                callsign = f"{CALLSIGN_PREFIX}{i:03d}"
                if callsign in taken_callsigns:
                    query = "SELECT name, callsign FROM pilots WHERE callsign = %s"
                    pilot = await self.cog.bot.db_manager.fetch_one(query, (callsign,))
                    if pilot:
                        name = pilot.get('name', 'Unknown')
                        taken_in_range.append(f"{callsign}: {name}")
            
            if taken_in_range:
                message += "\n\n**Taken Staff Callsigns:**\n" + "\n".join(taken_in_range)
            
        await interaction.followup.send(message)


class SingleCallsignModal(discord.ui.Modal):
    def __init__(self, cog):
        super().__init__(title="Check Single Callsign")
        self.cog = cog
        
    number = discord.ui.TextInput(
        label="Callsign Number",
        placeholder="Enter number (e.g., 005 for QRV005)",
        required=True,
        max_length=3
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        clean_number = self.number.value.strip().zfill(3)
        callsign_to_check = f"{CALLSIGN_PREFIX}{clean_number}"
        result = await self.cog.bot.pilots_model.get_pilot_by_callsign(callsign_to_check)
        
        if result:
            message = f"❌ **{callsign_to_check}** is **TAKEN**"
        else:
            message = f"✅ **{callsign_to_check}** is **AVAILABLE**"
            
        await interaction.followup.send(message)


class MultipleCallsignModal(discord.ui.Modal):
    def __init__(self, cog):
        super().__init__(title="Check Multiple Callsigns")
        self.cog = cog
        
    callsigns = discord.ui.TextInput(
        label="Callsign Numbers (comma separated)",
        placeholder="e.g., 005, 012, 025, 030, 045 (max 5)",
        required=True,
        max_length=50,
        style=discord.TextStyle.paragraph
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        callsign_numbers = [num.strip() for num in self.callsigns.value.split(',')]
        
        if len(callsign_numbers) > 5:
            await interaction.followup.send("❌ Maximum 5 callsigns allowed at once.")
            return
            
        results = []
        for num in callsign_numbers:
            if not num:
                continue
                
            try:
                clean_number = num.zfill(3)
                callsign_to_check = f"{CALLSIGN_PREFIX}{clean_number}"
                
                result = await self.cog.bot.pilots_model.get_pilot_by_callsign(callsign_to_check)
                
                if result:
                    results.append(f"❌ **{callsign_to_check}** - TAKEN")
                else:
                    results.append(f"✅ **{callsign_to_check}** - AVAILABLE")
                    
            except ValueError:
                results.append(f"❌ **{num}** - Invalid number")
        
        if results:
            message = "**Multiple Callsign Check Results:**\n\n" + "\n".join(results)
        else:
            message = "❌ No valid callsigns provided."
            
        await interaction.followup.send(message)


class CustomRangeModal(discord.ui.Modal):
    def __init__(self, cog):
        super().__init__(title="Custom Range Search")
        self.cog = cog
        
    start = discord.ui.TextInput(
        label="Start Number",
        placeholder="e.g., 5",
        required=True,
        max_length=3
    )
    
    end = discord.ui.TextInput(
        label="End Number",
        placeholder="e.g., 20",
        required=True,
        max_length=3
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        try:
            start_num = int(self.start.value)
            end_num = int(self.end.value)
            
            if start_num > end_num or start_num < 1 or end_num > 999:
                await interaction.followup.send("❌ Invalid range. Start must be less than end, and both must be between 1-999.")
                return
                
            taken_callsigns = await self.cog.bot.pilots_model.get_all_callsigns()
            available_list = await self.cog._find_available_in_range(start_num, end_num, taken_callsigns)
            
            message = f"**Available Callsigns ({CALLSIGN_PREFIX}{start_num:03d} - {CALLSIGN_PREFIX}{end_num:03d})**"
            
            if not available_list:
                message += "\n\n❌ No available callsigns in this range."
            else:
                output_str = ", ".join(f"`{c}`" for c in available_list)
                message += f"\n\n{output_str}"
                
            await interaction.followup.send(message)
            
        except ValueError:
            await interaction.followup.send("❌ Please enter valid numbers.")


async def setup(bot: commands.Bot):
    await bot.add_cog(CallsignFinderCog(bot))
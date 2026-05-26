import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os
import re
from database.pilots_model import PilotsModel

STAFF_ROLE_ID = 1090752933433450516

def get_ticket_creator_id(channel) -> int | None:
    """Helper to retrieve the ticket creator's Discord ID in a reboot-safe way."""
    if not channel:
        return None
    
    # 1. Try parsing from the channel topic
    if channel.topic:
        match = re.search(r"Ticket Creator:\s*(\d+)", channel.topic)
        if match:
            return int(match.group(1))
            
    # 2. Fallback: Search channel overwrites for non-staff members
    try:
        staff_role = channel.guild.get_role(STAFF_ROLE_ID)
        non_staff_members = []
        for target, overwrite in channel.overwrites.items():
            if isinstance(target, discord.Member):
                is_staff = staff_role in target.roles if staff_role else False
                if not is_staff:
                    non_staff_members.append(target)
        
        # Match channel name suffix
        for member in non_staff_members:
            if member.display_name.lower().replace(" ", "-") in channel.name.lower() or member.name.lower() in channel.name.lower():
                return member.id
                
        # Fallback to the first non-staff member found
        if non_staff_members:
            return non_staff_members[0].id
    except Exception as e:
        print(f"Error in get_ticket_creator_id fallback: {e}")
        
    return None

async def check_is_staff(user: discord.Member, guild: discord.Guild, client: discord.Client) -> bool:
    """Checks if a user is considered staff based on callsign range QRV001-QRV019, with role fallback."""
    if not user:
        return False
    
    # 1. Check database for callsign range QRV001 - QRV019
    try:
        cog = client.get_cog('TicketSystem')
        if cog and cog.pilots_model:
            pilot_data = await cog.pilots_model.get_pilot_by_discord_id(str(user.id))
            if pilot_data and pilot_data.get('callsign'):
                callsign = pilot_data['callsign'].upper()
                match = re.match(r'QRV(\d+)', callsign)
                if match:
                    number = int(match.group(1))
                    if 1 <= number <= 19:
                        return True
    except Exception as e:
        print(f"Error in check_is_staff callsign check: {e}")

    # 2. Fallback to Discord role check
    try:
        staff_role = guild.get_role(STAFF_ROLE_ID)
        return staff_role in user.roles if staff_role else False
    except Exception:
        return False

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='General Support', style=discord.ButtonStyle.primary, emoji='🛠️', custom_id='general_support')
    async def general_support(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            view = ConfirmTicketView('General Support')
            await interaction.response.send_message(
                "You are going to open a **General Support** ticket. Please tap confirm button to open.",
                view=view, ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"Error: {str(e)}", ephemeral=True)

    @discord.ui.button(label='Pirep Support', style=discord.ButtonStyle.secondary, emoji='✈️', custom_id='pirep_support')
    async def pirep_support(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            view = ConfirmTicketView('Pirep Support')
            await interaction.response.send_message(
                "You are going to open a **Pirep Support** ticket. Please tap confirm button to open.",
                view=view, ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"Error: {str(e)}", ephemeral=True)

    @discord.ui.button(label='LOA', style=discord.ButtonStyle.success, emoji='📋', custom_id='loa_ticket')
    async def loa_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            view = ConfirmTicketView('LOA')
            await interaction.response.send_message(
                "You are going to open a **LOA** ticket. Please tap confirm button to open.",
                view=view, ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"Error: {str(e)}", ephemeral=True)

class ConfirmTicketView(discord.ui.View):
    def __init__(self, ticket_type):
        super().__init__(timeout=60)
        self.ticket_type = ticket_type

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green, emoji='✅')
    async def confirm_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            category = interaction.channel.category
            
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            
            # Add staff role
            staff_role = interaction.guild.get_role(STAFF_ROLE_ID)
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True)
                
                # Add individual staff members
                for member in staff_role.members:
                    overwrites[member] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True)
            
            # Add callsign-based staff (QRV001 - QRV019) from database
            try:
                cog = interaction.client.get_cog('TicketSystem')
                if cog and cog.pilots_model:
                    query = "SELECT discordid, callsign FROM pilots WHERE status = 1 AND discordid IS NOT NULL AND discordid != ''"
                    records = await cog.pilots_model.db.fetch_all(query)
                    for record in records:
                        callsign = record['callsign']
                        if callsign:
                            match = re.match(r'QRV(\d+)', callsign.upper())
                            if match:
                                number = int(match.group(1))
                                if 1 <= number <= 19:
                                    member = interaction.guild.get_member(int(record['discordid']))
                                    if member:
                                        overwrites[member] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True)
            except Exception as e:
                print(f"Error adding database staff to ticket channel: {e}")
            
            ticket_channel = await interaction.guild.create_text_channel(
                name=f'{self.ticket_type.lower().replace(" ", "-")}-{interaction.user.display_name}',
                category=category,
                overwrites=overwrites,
                topic=f"Ticket Creator: {interaction.user.id} | Type: {self.ticket_type}"
            )
            
            embed = discord.Embed(
                title=f"🎫 {self.ticket_type} Ticket Created",
                description=f"Hello {interaction.user.mention}! Please describe your {self.ticket_type.lower()} issue.",
                color=0x800000
            )
            
            view = TicketControlView(interaction.user.id)
            await ticket_channel.send(embed=embed, view=view)
            
            await interaction.response.edit_message(
                content=f"✅ {self.ticket_type} ticket created: {ticket_channel.mention}",
                view=None
            )
        except Exception as e:
            await interaction.response.edit_message(
                content=f"Error creating ticket: {str(e)}",
                view=None
            )

class TicketControlView(discord.ui.View):
    def __init__(self, ticket_creator_id=None):
        super().__init__(timeout=None)
        self.ticket_creator_id = ticket_creator_id

    async def is_staff(self, user, guild, client):
        return await check_is_staff(user, guild, client)

    @discord.ui.button(label='Add User', style=discord.ButtonStyle.secondary, emoji='➕', custom_id='add_user')
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if not await self.is_staff(interaction.user, interaction.guild, interaction.client):
                return await interaction.response.send_message("Only staff can use this button.", ephemeral=True)
            
            view = AddUserModal()
            await interaction.response.send_modal(view)
        except Exception as e:
            await interaction.response.send_message(f"Error: {str(e)}", ephemeral=True)

    @discord.ui.button(label='Remove User', style=discord.ButtonStyle.secondary, emoji='➖', custom_id='remove_user')
    async def remove_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if not await self.is_staff(interaction.user, interaction.guild, interaction.client):
                return await interaction.response.send_message("Only staff can use this button.", ephemeral=True)
            
            channel = interaction.channel
            non_staff = []
            
            for target, overwrite in channel.overwrites.items():
                if isinstance(target, discord.Member):
                    is_member_staff = await self.is_staff(target, interaction.guild, interaction.client)
                    if not is_member_staff and overwrite.read_messages:
                        non_staff.append(target)
            
            if len(non_staff) < 2:
                return await interaction.response.send_message("Need at least 2 non-staff members to remove.", ephemeral=True)
            
            view = RemoveUserView(non_staff)
            await interaction.response.send_message("Select user to remove:", view=view, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: {str(e)}", ephemeral=True)

    @discord.ui.button(label='Close Ticket', style=discord.ButtonStyle.danger, emoji='🔒', custom_id='close_ticket')
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
            
            # Determine creator ID (with fallback helper)
            channel = interaction.channel
            creator_id = self.ticket_creator_id or get_ticket_creator_id(channel)
            
            # Allow staff OR ticket creator to close
            is_staff = await self.is_staff(interaction.user, interaction.guild, interaction.client)
            is_creator = creator_id is not None and interaction.user.id == creator_id
            
            if not is_staff and not is_creator:
                return await interaction.followup.send("Only staff or ticket creator can close this ticket.", ephemeral=True)
            
            # If the channel doesn't have a topic set yet, set it now so it persists!
            if creator_id and (not channel.topic or "Ticket Creator:" not in channel.topic):
                try:
                    current_topic = channel.topic or ""
                    new_topic = f"Ticket Creator: {creator_id}"
                    if current_topic:
                        new_topic = f"{new_topic} | {current_topic}"
                    await channel.edit(topic=new_topic)
                except Exception as e:
                    print(f"Error updating channel topic on close: {e}")
            
            # Remove ALL non-staff members from channel overwrites
            removed_members = []
            
            for target, overwrite in list(channel.overwrites.items()):
                if isinstance(target, discord.Member):
                    is_member_staff = await self.is_staff(target, interaction.guild, interaction.client)
                    if not is_member_staff:
                        await channel.set_permissions(target, overwrite=None)
                        removed_members.append(target)
            
            # Update view to show re-add and delete buttons
            view = ClosedTicketView(creator_id)
            
            description = "This ticket has been closed. Non-staff members have been removed from the channel."
            if removed_members:
                description += f"\n**Removed members:** {', '.join(m.mention for m in removed_members)}"
                
            embed = discord.Embed(
                title="🔒 Ticket Closed",
                description=description,
                color=0x800000
            )
            
            await interaction.followup.send(embed=embed, view=view)
            
        except Exception as e:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"Error closing ticket: {str(e)}", ephemeral=True)
                else:
                    await interaction.followup.send(f"Error closing ticket: {str(e)}", ephemeral=True)
            except Exception:
                pass


    


class AddUserModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Add User to Ticket")
        self.callsign_input = discord.ui.TextInput(
            label="Callsign (3 digits only)",
            placeholder="e.g., 777 for QRV777",
            max_length=3,
            min_length=3
        )
        self.add_item(self.callsign_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            callsign_digits = self.callsign_input.value
            full_callsign = f"QRV{callsign_digits}"
            
            # Get pilots model from bot
            pilots_model = interaction.client.get_cog('TicketSystem').pilots_model
            pilot_data = await pilots_model.get_pilot_by_callsign(full_callsign)
            
            if not pilot_data or not pilot_data.get('discordid'):
                return await interaction.response.send_message(f"No pilot found with callsign {full_callsign} or no Discord ID linked.", ephemeral=True)
            
            user = interaction.guild.get_member(int(pilot_data['discordid']))
            if not user:
                return await interaction.response.send_message(f"Discord user not found in server for callsign {full_callsign}.", ephemeral=True)
            
            # Check if user already has access
            for target, overwrite in interaction.channel.overwrites.items():
                if isinstance(target, discord.Member) and target.id == user.id and overwrite.read_messages:
                    return await interaction.response.send_message(f"{user.mention} already has access to this ticket.", ephemeral=True)
            
            # Show confirmation
            view = UserConfirmView(user, full_callsign, "add")
            await interaction.response.send_message(
                f"Do you want to add {user.mention} ({full_callsign}) to this ticket?",
                view=view, ephemeral=True
            )
            
        except Exception as e:
            await interaction.response.send_message(f"Error adding user: {str(e)}", ephemeral=True)

class UserConfirmView(discord.ui.View):
    def __init__(self, user, callsign, action_type="add"):
        super().__init__(timeout=60)
        self.user = user
        self.callsign = callsign
        self.action_type = action_type
    
    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green, emoji='✅')
    async def confirm_action(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if self.action_type == "add":
                await interaction.channel.set_permissions(self.user, read_messages=True, send_messages=True)
                
                # Rename channel to new user's name
                current_name = interaction.channel.name
                ticket_type = current_name.split('-')[0]  # Extract ticket type (e.g., 'general-support', 'pirep-support', 'loa')
                new_name = f"{ticket_type}-{self.user.display_name}"
                await interaction.channel.edit(name=new_name)
                
                await interaction.response.edit_message(
                    content=f"✅ Added {self.user.mention} ({self.callsign}) to the ticket and renamed channel.",
                    view=None
                )
            elif self.action_type == "enquiry":
                await self._create_enquiry_ticket(interaction)
        except Exception as e:
            await interaction.response.edit_message(
                content=f"Error: {str(e)}",
                view=None
            )
    
    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red, emoji='❌')
    async def cancel_action(self, interaction: discord.Interaction, button: discord.ui.Button):
        action_text = "adding user to ticket" if self.action_type == "add" else "creating enquiry ticket"
        await interaction.response.edit_message(
            content=f"❌ Cancelled {action_text}.",
            view=None
        )
    
    async def _create_enquiry_ticket(self, interaction):
        enquiry_type = getattr(self, 'enquiry_type', None)
        callsign = self.callsign.replace('QRV', '')
        
        category = interaction.channel.category
        
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        # Add staff role and members
        staff_role = interaction.guild.get_role(STAFF_ROLE_ID)
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True)
            
            # Add individual staff members
            for member in staff_role.members:
                overwrites[member] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True)
        
        # Add callsign-based staff (QRV001 - QRV019) from database
        try:
            cog = interaction.client.get_cog('TicketSystem')
            if cog and cog.pilots_model:
                query = "SELECT discordid, callsign FROM pilots WHERE status = 1 AND discordid IS NOT NULL AND discordid != ''"
                records = await cog.pilots_model.db.fetch_all(query)
                for record in records:
                    callsign = record['callsign']
                    if callsign:
                        match = re.match(r'QRV(\d+)', callsign.upper())
                        if match:
                            number = int(match.group(1))
                            if 1 <= number <= 19:
                                member = interaction.guild.get_member(int(record['discordid']))
                                if member:
                                    overwrites[member] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True)
        except Exception as e:
            print(f"Error adding database staff to ticket channel: {e}")
        
        if self.user:
            overwrites[self.user] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        
        ticket_type = f"{enquiry_type}-enquiry"
        # Use target user's name if available, otherwise use callsign
        channel_suffix = self.user.display_name if self.user else f"qrv{callsign}"
        
        target_user_id = self.user.id if self.user else interaction.user.id
        
        ticket_channel = await interaction.guild.create_text_channel(
            name=f'{ticket_type}-{channel_suffix}',
            category=category,
            overwrites=overwrites,
            topic=f"Ticket Creator: {target_user_id} | Type: {ticket_type}"
        )
        
        embed = discord.Embed(
            title=f"🎫 {enquiry_type.title()} Enquiry Ticket Created",
            description=f"Hello {interaction.user.mention}! Staff enquiry for QRV{callsign}. Please provide details about the {enquiry_type} enquiry.",
            color=0x800000
        )
        
        view = TicketControlView(target_user_id)
        await ticket_channel.send(embed=embed, view=view)
        
        await interaction.edit_original_response(
            content=f"✅ {enquiry_type.title()} enquiry ticket created for QRV{callsign}: {ticket_channel.mention}",
            view=None
        )

class RemoveUserView(discord.ui.View):
    def __init__(self, users):
        super().__init__(timeout=60)
        self.add_item(RemoveUserSelect(users))

class RemoveUserSelect(discord.ui.Select):
    def __init__(self, users):
        options = [
            discord.SelectOption(label=user.display_name, value=str(user.id))
            for user in users[:25]  # Discord limit
        ]
        super().__init__(placeholder="Select user to remove", options=options)
    
    async def callback(self, interaction: discord.Interaction):
        user_id = int(self.values[0])
        user = interaction.guild.get_member(user_id)
        
        if user:
            await interaction.channel.set_permissions(user, overwrite=None)
            await interaction.response.send_message(f"Removed {user.mention} from ticket.", ephemeral=True)

class ClosedTicketView(discord.ui.View):
    def __init__(self, ticket_creator_id=None):
        super().__init__(timeout=None)
        self.ticket_creator_id = ticket_creator_id

    @discord.ui.button(label='Re-add User', style=discord.ButtonStyle.success, emoji='➕', custom_id='readd_user')
    async def readd_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            is_staff = await check_is_staff(interaction.user, interaction.guild, interaction.client)
            if not is_staff:
                return await interaction.response.send_message("Only staff can re-add users.", ephemeral=True)
            
            # Re-add ticket creator to channel
            creator_id = self.ticket_creator_id or get_ticket_creator_id(interaction.channel)
            if creator_id:
                ticket_creator = interaction.guild.get_member(creator_id)
                if ticket_creator:
                    await interaction.channel.set_permissions(ticket_creator, read_messages=True, send_messages=True)
                    await interaction.response.send_message(f"Re-added {ticket_creator.mention} to the ticket.", ephemeral=True)
                else:
                    await interaction.response.send_message("Ticket creator not found.", ephemeral=True)
            else:
                await interaction.response.send_message("No ticket creator ID found.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error re-adding user: {str(e)}", ephemeral=True)

    @discord.ui.button(label='Delete Channel', style=discord.ButtonStyle.danger, emoji='🗑️', custom_id='delete_channel')
    async def delete_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            is_staff = await check_is_staff(interaction.user, interaction.guild, interaction.client)
            if not is_staff:
                return await interaction.response.send_message("Only staff can delete tickets.", ephemeral=True)
            
            await interaction.response.send_message("Deleting ticket in 3 seconds...", ephemeral=True)
            await asyncio.sleep(3)
            await interaction.channel.delete()
        except Exception as e:
            print(f"Error deleting channel: {e}")

class TicketSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pilots_model = None
    
    async def cog_load(self):
        # Get pilots model from database manager
        db_manager = getattr(self.bot, 'db_manager', None)
        if db_manager:
            self.pilots_model = PilotsModel(db_manager)
        
        # Add persistent views
        self.bot.add_view(TicketView())
        self.bot.add_view(TicketControlView())
        self.bot.add_view(ClosedTicketView(None))

    @app_commands.command(name="check_roles", description="Debug: Check your roles")
    async def check_roles(self, interaction: discord.Interaction):
        roles = [role.name for role in interaction.user.roles]
        await interaction.response.send_message(f"Your roles: {', '.join(roles)}", ephemeral=True)

    @app_commands.command(name="ticket_setup", description="Setup ticket system in current channel")
    @app_commands.describe(title="Title for the ticket embed", description="Description for the ticket embed")
    async def ticket_setup(self, interaction: discord.Interaction, title: str = "🎫 Support Tickets", 
                          description: str = "Click the button below to create a support ticket."):
        # Check if user has administrator permission
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("You need 'Administrator' permission to setup tickets.", ephemeral=True)
        
        # Respond immediately to prevent timeout
        await interaction.response.defer()
        
        try:
            embed = discord.Embed(
                title=title,
                description=description,
                color=0x800000  # Maroon color
            )
            
            # Try to load logo file
            logo_file = None
            try:
                logo_file = discord.File("assets/Qatar_virtual_logo_white.PNG", filename="Qatar_virtual_logo_white.PNG")
                embed.set_thumbnail(url="attachment://Qatar_virtual_logo_white.PNG")
            except Exception:
                pass  # Continue without logo if it fails
            
            embed.add_field(
                name="Ticket Types:",
                value="🛠️ **General Support** - General help and questions\n✈️ **Pirep Support** - Flight report issues\n📋 **LOA** - Leave of Absence requests",
                inline=False
            )
            embed.add_field(
                name="How it works:",
                value="• Choose your ticket type\n• Confirm to create private channel\n• Staff will be automatically added",
                inline=False
            )
            
            view = TicketView()
            
            if logo_file:
                await interaction.followup.send(embed=embed, view=view, file=logo_file)
            else:
                await interaction.followup.send(embed=embed, view=view)
            
        except Exception as e:
            await interaction.followup.send(f"Error setting up tickets: {str(e)}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(TicketSystem(bot))
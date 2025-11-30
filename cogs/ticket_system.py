import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from database.pilots_model import PilotsModel

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
            
            staff_names = ["staff", "executive", "manager"]
            for role_name in staff_names:
                staff_role = discord.utils.get(interaction.guild.roles, name=role_name)
                if staff_role:
                    overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True)
            
            ticket_channel = await interaction.guild.create_text_channel(
                name=f'{self.ticket_type.lower().replace(" ", "-")}-{interaction.user.display_name}',
                category=category,
                overwrites=overwrites
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

    def is_staff(self, user, guild):
        # Check for staff, executive, or manager roles (case insensitive)
        try:
            staff_names = ["staff", "executive", "manager"]
            user_roles = [role.name.lower() for role in user.roles]
            return any(role_name in user_roles for role_name in staff_names)
        except Exception:
            return False

    @discord.ui.button(label='Add User', style=discord.ButtonStyle.secondary, emoji='➕', custom_id='add_user')
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if not self.is_staff(interaction.user, interaction.guild):
                return await interaction.response.send_message("Only staff can use this button.", ephemeral=True)
            
            view = AddUserModal()
            await interaction.response.send_modal(view)
        except Exception as e:
            await interaction.response.send_message(f"Error: {str(e)}", ephemeral=True)

    @discord.ui.button(label='Remove User', style=discord.ButtonStyle.secondary, emoji='➖', custom_id='remove_user')
    async def remove_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if not self.is_staff(interaction.user, interaction.guild):
                return await interaction.response.send_message("Only staff can use this button.", ephemeral=True)
            
            channel = interaction.channel
            staff_names = ["staff", "executive", "manager"]
            non_staff = []
            
            for target, overwrite in channel.overwrites.items():
                if isinstance(target, discord.Member):
                    user_roles = [role.name.lower() for role in target.roles]
                    is_staff = any(role_name in user_roles for role_name in staff_names)
                    if not is_staff and overwrite.read_messages:
                        non_staff.append(target)
            
            if len(non_staff) < 2:
                return await interaction.response.send_message("Need at least 2 non-staff members to remove.", ephemeral=True)
            
            view = RemoveUserView(non_staff)
            await interaction.response.send_message("Select user to remove:", view=view, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: {str(e)}", ephemeral=True)

    @discord.ui.button(label='Close Ticket', style=discord.ButtonStyle.danger, emoji='🔒', custom_id='close_ticket')
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        print("DEBUG: Close ticket button clicked")
        try:
            await interaction.response.send_message("DEBUG: Starting close process...", ephemeral=True)
            print("DEBUG: Sent initial response")
            
            # Allow staff OR ticket creator to close
            is_staff = self.is_staff(interaction.user, interaction.guild)
            is_creator = interaction.user.id == self.ticket_creator_id
            print(f"DEBUG: is_staff={is_staff}, is_creator={is_creator}, user_id={interaction.user.id}, creator_id={self.ticket_creator_id}")
            
            if not is_staff and not is_creator:
                return await interaction.followup.send("Only staff or ticket creator can close this ticket.", ephemeral=True)
            
            print("DEBUG: Permission check passed")
            
            # Remove ticket creator from channel
            channel = interaction.channel
            print(f"DEBUG: Channel: {channel.name}")
            
            if self.ticket_creator_id:
                ticket_creator = interaction.guild.get_member(self.ticket_creator_id)
                print(f"DEBUG: Ticket creator: {ticket_creator}")
                if ticket_creator:
                    await channel.set_permissions(ticket_creator, overwrite=None)
                    print("DEBUG: Removed ticket creator permissions")
            
            print("DEBUG: About to create view and embed")
            
            # Update view to show re-add and delete buttons
            view = ClosedTicketView(self.ticket_creator_id)
            embed = discord.Embed(
                title="🔒 Ticket Closed",
                description="This ticket has been closed. The ticket creator has been removed from the channel.",
                color=0x800000  # Maroon color
            )
            
            print("DEBUG: About to edit original message")
            
            # Edit the original message
            await interaction.edit_original_response(content=None, embed=embed, view=view)
            print("DEBUG: Successfully edited message")
            
        except Exception as e:
            print(f"DEBUG: Exception occurred: {str(e)}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"Error closing ticket: {str(e)}", ephemeral=True)
                else:
                    await interaction.followup.send(f"Error closing ticket: {str(e)}", ephemeral=True)
            except Exception as e2:
                print(f"DEBUG: Failed to send error message: {str(e2)}")


    


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
                await interaction.response.edit_message(
                    content=f"✅ Added {self.user.mention} ({self.callsign}) to the ticket.",
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
        staff_names = ["staff", "executive", "manager"]
        
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        for role_name in staff_names:
            staff_role = discord.utils.get(interaction.guild.roles, name=role_name)
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True)
        
        if self.user:
            overwrites[self.user] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        
        ticket_type = f"{enquiry_type}-enquiry"
        ticket_channel = await interaction.guild.create_text_channel(
            name=f'{ticket_type}-{interaction.user.display_name}',
            category=category,
            overwrites=overwrites
        )
        
        embed = discord.Embed(
            title=f"🎫 {enquiry_type.title()} Enquiry Ticket Created",
            description=f"Hello {interaction.user.mention}! Staff enquiry for QRV{callsign}. Please provide details about the {enquiry_type} enquiry.",
            color=0x800000
        )
        
        view = TicketControlView(interaction.user.id)
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
    def __init__(self, ticket_creator_id):
        super().__init__(timeout=None)
        self.ticket_creator_id = ticket_creator_id

    @discord.ui.button(label='Re-add User', style=discord.ButtonStyle.success, emoji='➕', custom_id='readd_user')
    async def readd_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            staff_names = ["staff", "executive", "manager"]
            user_roles = [role.name.lower() for role in interaction.user.roles]
            is_staff = any(role_name in user_roles for role_name in staff_names)
            
            if not is_staff:
                return await interaction.response.send_message("Only staff can re-add users.", ephemeral=True)
            
            # Re-add ticket creator to channel
            if self.ticket_creator_id:
                ticket_creator = interaction.guild.get_member(self.ticket_creator_id)
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
            staff_names = ["staff", "executive", "manager"]
            user_roles = [role.name.lower() for role in interaction.user.roles]
            is_staff = any(role_name in user_roles for role_name in staff_names)
            
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
    
    @app_commands.command(name="staff_enquiry", description="Create staff enquiry ticket")
    @app_commands.describe(
        callsign="Callsign number (e.g., 777 for QRV777)",
        enquiry_type="Type of enquiry"
    )
    @app_commands.choices(enquiry_type=[
        app_commands.Choice(name="IFC Enquiry", value="ifc"),
        app_commands.Choice(name="Pirep Enquiry", value="pirep")
    ])
    async def staff_enquiry(self, interaction: discord.Interaction, callsign: str, enquiry_type: app_commands.Choice[str]):
        staff_names = ["staff", "executive", "manager"]
        user_roles = [role.name.lower() for role in interaction.user.roles]
        is_staff = any(role_name in user_roles for role_name in staff_names)
        
        if not is_staff:
            return await interaction.response.send_message("Only staff can use this command.", ephemeral=True)
        
        target_user = None
        full_callsign = f"QRV{callsign}"
        
        if self.pilots_model:
            pilot_data = await self.pilots_model.get_pilot_by_callsign(full_callsign)
            if pilot_data and pilot_data.get('discordid'):
                target_user = interaction.guild.get_member(int(pilot_data['discordid']))
        
        view = UserConfirmView(target_user, full_callsign, "enquiry")
        view.enquiry_type = enquiry_type.value
        
        user_info = f" and add {target_user.mention}" if target_user else " (pilot not found in database)"
        await interaction.response.send_message(
            f"Do you want to create a {enquiry_type.name} ticket for {full_callsign}{user_info}?",
            view=view, ephemeral=True
        )

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
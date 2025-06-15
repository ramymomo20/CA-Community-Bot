from ios_bot.config import *
from ios_bot.database_manager import get_team, delete_team, get_team_by_name
from ios_bot.announcements import announce_team_deleted

class ConfirmDeleteView(View):
    def __init__(self, author_id: int, guild_to_delete_id: int, guild_to_delete_name: str, leave_guild_after: bool):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.guild_to_delete_id = guild_to_delete_id
        self.guild_to_delete_name = guild_to_delete_name
        self.leave_guild_after = leave_guild_after
        self.confirmed = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger, custom_id="confirm_delete_team")
    async def confirm_button_callback(self, button: Button, interaction: discord.Interaction):
        self.confirmed = True
        await interaction.response.edit_message(content=f"✅ Deleting team '{self.guild_to_delete_name}'...", view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, custom_id="cancel_delete_team")
    async def cancel_button_callback(self, button: Button, interaction: discord.Interaction):
        self.confirmed = False
        await interaction.response.edit_message(content="Team deletion cancelled.", view=None)
        self.stop()

@bot.slash_command(
    name="delete_team",
    description="Delete a registered IOSCA team. Captains delete their own team; Admins can specify a team."
)
async def delete_team_command(ctx: ApplicationContext, team_name: Option(str, "Admin only: Specify team name to delete", required=False)):
    is_admin_attempt = bool(team_name)
    is_guild_admin = False

    target_guild_id = None
    target_guild_name = None
    leave_guild = False

    if is_admin_attempt:
        main_guild = bot.get_guild(MAIN_GUILD_ID)
        if not main_guild:
            await ctx.respond("❌ Critical error: Main guild not found by the bot. Admin action aborted.", ephemeral=True)
            return
        
        admin_user_member = main_guild.get_member(ctx.user.id)
        if not admin_user_member:
            await ctx.respond("❌ You must be a member of the main IOSCA server to perform this admin action.", ephemeral=True)
            return

        admin_role = main_guild.get_role(ADMIN_ROLE_ID)
        shaq_perm = main_guild.get_role(MY_PERM)

        if not admin_role:
            await ctx.respond("❌ Critical error: Admin role not found in the main guild. Admin action aborted.", ephemeral=True)
            return

        if admin_role not in admin_user_member.roles and shaq_perm not in admin_user_member.roles:
            await ctx.respond("❌ You do not have the required role in the main server to delete a team by name.", ephemeral=True)
            return
        
        is_guild_admin = True

        if not team_name: 
            await ctx.respond("❌ Admin delete requires a `team_name` to be specified.", ephemeral=True)
            return
        
        team_to_delete_data = await get_team_by_name(team_name)
        if not team_to_delete_data:
            await ctx.respond(f"❌ No team found with the name '{team_name}'.", ephemeral=True)
            return
        target_guild_id = team_to_delete_data['guild_id']
        target_guild_name = team_to_delete_data['guild_name']
        leave_guild = False 

    else: # Captain deleting their own team
        if not ctx.guild:
            await ctx.respond("This command must be used within the team's server to delete it, unless you are an admin specifying a team name.", ephemeral=True)
            return
        
        team_to_delete_data = await get_team(ctx.guild.id)
        if not team_to_delete_data:
            await ctx.respond("This server is not registered as an IOSCA team.", ephemeral=True)
            return
        
        if ctx.user.id != team_to_delete_data.get('captain_id'):
            await ctx.respond("❌ Only the registered Captain can delete this team.", ephemeral=True)
            return
        target_guild_id = ctx.guild.id
        target_guild_name = ctx.guild.name
        leave_guild = True

    if not target_guild_id or not target_guild_name:
        await ctx.respond("Error: Could not determine team to delete.", ephemeral=True)
        return

    view = ConfirmDeleteView(ctx.author.id, target_guild_id, target_guild_name, leave_guild_after=leave_guild)
    await ctx.respond(f"⚠️ **Warning:** Are you sure you want to delete the team '{target_guild_name}'? This action is irreversible.", view=view, ephemeral=True)
    
    await view.wait() # Wait for the view to stop (button clicked or timeout)

    if view.confirmed is True:
        if await delete_team(target_guild_id):
            response_message = f"✅ Team '{target_guild_name}' (ID: {target_guild_id}) has been successfully deleted from the database."
            await announce_team_deleted(
                team_name=target_guild_name,
                deleter_name=ctx.user.display_name,
                guild_id=target_guild_id
            )
            if leave_guild and ctx.guild and ctx.guild.id == target_guild_id: # Ensure bot is in the guild it needs to leave
                try:
                    await ctx.guild.leave()
                    response_message += " The bot has now left this server."
                except discord.HTTPException as e:
                    response_message += f" Could not leave the server automatically: {e}"
            try:
                await ctx.followup.send(response_message, ephemeral=False)
            except discord.HTTPException as e:
                if e.code == 50027: # Invalid Webhook Token
                    try:
                        await ctx.channel.send(response_message)
                    except discord.Forbidden:
                        print(f"Failed to send delete confirmation to channel {ctx.channel.id} for team {target_guild_name} due to missing permissions.")
                    except Exception as channel_send_e:
                        print(f"An unexpected error occurred when trying to send delete confirmation to channel {ctx.channel.id} for team {target_guild_name}: {channel_send_e}")
                else:
                    raise
        else:
            failure_message = f"❌ Failed to delete team '{target_guild_name}' from the database."
            try:
                await ctx.followup.send(failure_message, ephemeral=True)
            except discord.HTTPException as e:
                if e.code == 50027: # Invalid Webhook Token
                    try:
                        await ctx.channel.send(failure_message) # Public fallback if ephemeral followup fails
                    except discord.Forbidden:
                        print(f"Failed to send delete failure message to channel {ctx.channel.id} for team {target_guild_name} due to missing permissions.")
                    except Exception as channel_send_e:
                        print(f"An unexpected error occurred when trying to send delete failure message to channel {ctx.channel.id} for team {target_guild_name}: {channel_send_e}")
                else:
                    raise
    elif view.confirmed is None: # Timeout
        timeout_message = "Team deletion timed out."
        try:
            await ctx.followup.send(timeout_message, ephemeral=True)
        except discord.HTTPException as e:
            if e.code == 50027: # Invalid Webhook Token
                try:
                    await ctx.channel.send(timeout_message) # Public fallback
                except discord.Forbidden:
                    print(f"Failed to send delete timeout message to channel {ctx.channel.id} for team {target_guild_name} due to missing permissions.")
                except Exception as channel_send_e:
                    print(f"An unexpected error occurred when trying to send delete timeout message to channel {ctx.channel.id} for team {target_guild_name}: {channel_send_e}")
            else:
                raise
    # If view.confirmed is False, the view already sent "Team deletion cancelled." 
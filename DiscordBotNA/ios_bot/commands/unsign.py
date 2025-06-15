from ios_bot.config import *
from ios_bot.signup_manager import init_state, is_text_player, get_player_position, refresh_lineup, TextPlayer, get_channel_context, update_state
from .utils import delete_after_delay, move_sub_to_position

@bot.slash_command(
    name="unsign",
    description="Remove yourself or another player from a team or subs."
)
async def unsign_slash(
    ctx: ApplicationContext, 
    target_player_specifier: Option(str, "Player to unsign (@mention, ID, or text name). Leave blank for self.", required=False) = None
):
    await ctx.defer() # Defer publicly
    guild_id = ctx.guild_id
    channel_id = ctx.channel_id
    requesting_user = ctx.author

    channel_context = await get_channel_context(guild_id, channel_id)
    context_type = channel_context.get("type")

    if context_type == "not_matchmaking":
        await ctx.respond("❌ This command can only be used in a registered matchmaking channel.", ephemeral=True)
        return

    state = await init_state(guild_id, channel_id)
    if not state:
        await ctx.respond("Error: Channel state not found.", ephemeral=True)
        return

    # Create a local copy of the state to modify
    state_copy = dict(state)
    action_taken = False

    target_player_obj = requesting_user
    player_display_name = requesting_user.display_name
    is_other_player = False

    if target_player_specifier:
        is_other_player = True
        resolved_member = None
        try:
            converter = commands.MemberConverter()
            resolved_member = await converter.convert(ctx, target_player_specifier)
        except commands.MemberNotFound:
            if ctx.guild:
                resolved_member = discord.utils.get(ctx.guild.members, name=target_player_specifier)
                if not resolved_member:
                    resolved_member = discord.utils.get(ctx.guild.members, display_name=target_player_specifier)
        
        if resolved_member:
            target_player_obj = resolved_member
            player_display_name = resolved_member.display_name
            if resolved_member.id == requesting_user.id: # Specified self
                is_other_player = False 
        else:
            target_player_obj = TextPlayer(target_player_specifier)
            player_display_name = target_player_specifier
    
    signed_team_num, signed_pos = get_player_position(state_copy, target_player_obj)

    if signed_pos: # Player found in a position
        team_idx_to_unsign_from = signed_team_num - 1
        
        # Before unsigning, get the player object for the sub move logic
        player_data = state_copy["teams"][team_idx_to_unsign_from].get(signed_pos)
        player_being_unsigned = player_data['player'] if player_data else None

        state_copy["teams"][team_idx_to_unsign_from][signed_pos] = None
        action_taken = True
        
        original_team_name_desc = f"Team {signed_team_num}"
        if state_copy.get("context_type") in ["team_6s", "team_8s"] and signed_team_num == 1:
            original_team_name_desc = state_copy.get("team_name", "Your Team")

        response_description = f"❌ **{player_display_name}** unsigned from **{signed_pos}** on {original_team_name_desc}."
        
        # Attempt to move a sub to the now empty position
        moved_sub_msg = ""
        try:
            # move_sub_to_position expects team_number (1-indexed)
            moved_sub = await move_sub_to_position(state_copy, signed_pos, signed_team_num, ctx.channel)
            if moved_sub:
                moved_sub_display = moved_sub.mention if hasattr(moved_sub, 'mention') else moved_sub.name
                moved_sub_msg = f"\n↪️ {moved_sub_display} was moved from subs to fill {signed_pos} on {original_team_name_desc}."
                response_description += moved_sub_msg
        except Exception as e:
            # print(f"Error during move_sub_to_position: {e}") # Optional: log error
            pass # Continue even if move_sub fails

        unlink_embed = Embed(
            description=response_description,
            color=0xE74C3C # Red for removal/unlink
        )
        timestamp_now = datetime.now(timezone.utc)
        unlink_embed.set_footer(
            text=f"Requested by {requesting_user.display_name} • {timestamp_now:%I:%M %p}",
            icon_url=requesting_user.display_avatar.url if requesting_user.display_avatar else None
        )
        await ctx.followup.send(embed=unlink_embed) # Send as a public followup
    
    elif not is_text_player(target_player_obj) and target_player_obj in state_copy.get("subs", []):
        # Player is a Discord Member and found in subs
        state_copy["subs"].remove(target_player_obj)
        action_taken = True
        # Use ephemeral followup for private confirmation
        await ctx.followup.send(f"✅ {player_display_name} removed from subs.", ephemeral=True) 
    else:
        # Player not found in any position or in subs (if Discord member)
        await ctx.followup.send(f"⚠️ {player_display_name} is not currently signed up for a position or as a sub.", ephemeral=True)
        return # Exit early if no action taken

    if action_taken:
        update_state(channel_id, state_copy)

    # Refresh the lineup by editing the existing message
    await refresh_lineup(ctx.channel, author_override=requesting_user)

# This is the function called by LineupView's "Unsign" button
async def do_unsign(interaction: discord.Interaction, team_num_for_button: int = None):
    """Handles the unsign logic. Can be called from a button or /unsign command."""
    await interaction.response.defer() # Defer immediately
    
    player_to_unsign = interaction.user
    guild_id = interaction.guild_id
    channel_id = interaction.channel_id
    requesting_user = interaction.user

    channel_context = await get_channel_context(guild_id, channel_id)
    if channel_context.get("type") == "not_matchmaking":
        await interaction.followup.send("❌ This command/button only works in matchmaking channels.", ephemeral=True)
        return

    state = await init_state(guild_id, channel_id)
    if not state:
        await interaction.followup.send("Error: Channel state not found.", ephemeral=True)
        return

    state_copy = dict(state)
    action_taken = False
    signed_team_num, signed_pos = get_player_position(state_copy, player_to_unsign)

    if not signed_pos:
        # Check if they are a sub
        if player_to_unsign in state_copy.get("subs", []):
            state_copy["subs"].remove(player_to_unsign)
            action_taken = True
            await interaction.followup.send(f"✅ {player_to_unsign.display_name} removed from subs.", ephemeral=True)
            if action_taken:
                update_state(channel_id, state_copy)
            await refresh_lineup(interaction.channel, author_override=interaction.user)
            return
        await interaction.followup.send(f"⚠️ {player_to_unsign.display_name}, you are not currently signed up for a position or as a sub.", ephemeral=True)
        return

    # If called by a button on a specific team's LineupView, team_num_for_button is provided.
    # We should respect that if it matches the player's actual signed team.
    if team_num_for_button is not None and signed_team_num != team_num_for_button:
        await interaction.followup.send(
            f"⚠️ You clicked unsign for Team {team_num_for_button}, but you're signed on Team {signed_team_num} ({signed_pos}). Unsign from there or use `/unsign`.", 
            ephemeral=True
        )
        return

    state_copy["teams"][signed_team_num - 1][signed_pos] = None
    action_taken = True
    
    team_name_desc = f"Team {signed_team_num}"
    if state_copy.get("context_type") in ["team_6s", "team_8s"]:
        team_name_desc = state_copy.get("team_name", "your team")

    response_description = f"❌ {interaction.user.display_name} unsigned from **{signed_pos}** on {team_name_desc}."

    # Create and send the public confirmation embed
    unlink_embed = Embed(
        description=response_description,
        color=0xE74C3C
    )
    timestamp_now = datetime.now(timezone.utc)
    unlink_embed.set_footer(
        text=f"Requested by {requesting_user.display_name} • {timestamp_now:%I:%M %p}",
        icon_url=requesting_user.display_avatar.url if requesting_user.display_avatar else None
    )
    await interaction.followup.send(embed=unlink_embed)
    
    if action_taken:
        update_state(channel_id, state_copy)

    await refresh_lineup(interaction.channel, author_override=interaction.user)
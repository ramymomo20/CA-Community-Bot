from ios_bot.config import *
from ios_bot.signup_manager import init_state, is_player_signed, get_player_position, refresh_lineup, TextPlayer, is_text_player, get_channel_context, EIGHTS_POSITIONS, update_state
from .utils import delete_after_delay

# This view is presented after a user clicks "Sign" on a LineupView in a Team Channel (or from old /sign logic)
class PositionView(View):
    # Position grids for 8v8
    _eights_positions = [
        ["LW", "CF", "RW"],
        [None, "CM", None],
        ["LB", "CB", "RB"],
        [None, "GK", None]
    ]
    
    _sixes_positions = [
        ["LW", None, "RW"],
        [None, "CM", None],
        ["LB", None, "RB"],
        [None, "GK", None]
    ]

    def __init__(self, team_number: int, guild_id: int, channel_id: int, context_type: str, state: dict):
        super().__init__(timeout=60)
        self.team_number = team_number
        self.guild_id = guild_id
        self.channel_id = channel_id
        
        is_eights = context_type in ["main_8s", "team_8s"]
        is_sixes = context_type in ["main_6s", "team_6s"]
        
        positions_grid = self._eights_positions if is_eights else self._sixes_positions
        
        if not state:
            print(f"Warning: PositionView received None state for channel {channel_id}. Buttons may be incorrect.")
            team_state = {} 
        else:
            teams_list = state.get("teams", [])
            if (team_number - 1) < len(teams_list):
                team_state = teams_list[team_number - 1]
            else:
                print(f"Warning: team_number {team_number} out of range for teams list in PositionView. Channel: {channel_id}")
                team_state = {}

        for row_idx, row_list in enumerate(positions_grid):
            for col_idx, pos_name in enumerate(row_list):
                if pos_name is None:
                    button = Button(
                        label="⠀", # Invisible character for spacing
                        style=ButtonStyle.secondary,
                        custom_id=f"pos_empty_{row_idx}_{col_idx}_team{team_number}",
                        row=row_idx,
                        disabled=True
                    )
                else:
                    is_taken = team_state.get(pos_name) is not None
                    button = Button(
                        label=pos_name,
                        style=ButtonStyle.secondary if is_taken else ButtonStyle.primary,
                        custom_id=f"pos_select_{pos_name}_team{team_number}",
                        row=row_idx,
                        disabled=is_taken
                    )
                    if not is_taken:
                        button.callback = self.make_callback(pos_name)
                self.add_item(button)

    def make_callback(self, position_name: str):
        async def position_callback(interaction: Interaction):
            await interaction.response.defer(ephemeral=True)
            current_state_cb = await init_state(self.guild_id, self.channel_id)
            
            if not current_state_cb:
                await interaction.followup.send("❌ Invalid channel state. Please try again.", ephemeral=True)
                return
            
            existing_team_num, existing_pos = get_player_position(current_state_cb, interaction.user)
            if existing_pos:
                await interaction.followup.send(f"❌ You are already signed as {existing_pos} on Team {existing_team_num}", ephemeral=True)
                asyncio.create_task(delete_after_delay(interaction))
                return
                
            # Get a local copy of the state for modification
            state_copy = dict(current_state_cb)
            team_state_copy = state_copy["teams"][self.team_number - 1]

            if interaction.user in state_copy.get("subs", []):
                state_copy["subs"].remove(interaction.user)
                
            team_state_copy[position_name] = {
                "player": interaction.user,
                "signup_time": datetime.now(timezone.utc)
            }
            
            # Update the shared state with the modified copy
            update_state(self.channel_id, state_copy)

            team_name_desc = f"Team {self.team_number}"
            if current_state_cb.get("context_type") in ["team_6s", "team_8s"] and self.team_number == 1 :
                team_name_desc = current_state_cb.get("team_name", "Your Team")

            public_embed = Embed( # Changed variable name for clarity
                description=f"✅ Signed **{interaction.user.mention}** to **{position_name}** for {team_name_desc}!",
                color=0x2F3136 # Dark theme color
            )

            # Add footer with timestamp
            timestamp = datetime.now(timezone.utc).strftime("%I:%M %p")
            public_embed.set_footer(
                text=f"Requested by {interaction.user.name} • {timestamp}",
                icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None
            )
            
            # Send public confirmation to the channel
            await interaction.channel.send(embed=public_embed)
            await refresh_lineup(interaction.channel, author_override=interaction.user)
            asyncio.create_task(delete_after_delay(interaction))

        return position_callback

@bot.slash_command(
    name="sign",
    description="Sign up for a specific position in your team's channel."
)
async def sign(
    ctx: ApplicationContext,
    team: Option(int, "Which team to join (1 or 2 for main, 1 for own team channel)", min_value=1, max_value=2),
    position: Option(str, "Position to sign up for", choices=[]),
    member: Option(str, "Member to sign up (@mention, ID, or name)", required=False) = None
):
    guild_id = ctx.guild_id
    channel_id = ctx.channel_id
    requesting_user = ctx.author

    channel_context = await get_channel_context(guild_id, channel_id)
    context_type = channel_context.get("type")

    if context_type == "not_matchmaking":
        await ctx.respond("❌ This command can only be used in a registered matchmaking channel.", ephemeral=True)
        return

    # Determine valid positions based on channel type for dynamic choices
    valid_positions = []
    if context_type in ["main_8s", "team_8s"]:
        valid_positions = EIGHTS_POSITIONS
    elif context_type in ["main_6s", "team_6s"]:
        valid_positions = SIXES_POSITIONS
    else:
        # This case should ideally be caught by not_matchmaking, but as a fallback:
        await ctx.respond("❌ Cannot determine game type for this channel to list positions.", ephemeral=True)
        return

    # Dynamically update position choices if possible (pycord specific handling might differ)
    # Assuming 'position' is the second Option (index 1) after 'team' (index 0)
    try:
        if hasattr(ctx.command, 'options') and len(ctx.command.options) > 1:
             # Find the 'position' option by name, as order isn't guaranteed in the options list
            pos_option_index = -1
            for i, opt in enumerate(ctx.command.options):
                if opt.name == "position":
                    pos_option_index = i
                    break
            if pos_option_index != -1:
                ctx.command.options[pos_option_index].choices = [OptionChoice(name=pos, value=pos) for pos in valid_positions]
            else:
                # Fallback or warning if 'position' option not found by name
                pass # print("Warning: Could not find 'position' option to update choices.")

    except Exception as e:
        # print(f"Error updating position choices dynamically: {e}") # Optional: log error
        pass

    # Determine target player
    target_player_obj = requesting_user # Default to self
    player_display_name = requesting_user.display_name
    is_signing_other = False

    if member:
        is_signing_other = True # Assume signing other if member is specified
        resolved_member = None
        try:
            # Try MemberConverter for mentions or IDs
            converter = commands.MemberConverter()
            resolved_member = await converter.convert(ctx, member)
        except commands.MemberNotFound:
            # If not a valid mention/ID, try to find by name/nickname in the guild
            if ctx.guild: # Ensure guild context for searching members
                resolved_member = discord.utils.get(ctx.guild.members, name=member)
                if not resolved_member:
                    resolved_member = discord.utils.get(ctx.guild.members, display_name=member)

        if resolved_member:
            target_player_obj = resolved_member
            player_display_name = resolved_member.display_name
            if resolved_member.id == requesting_user.id:
                is_signing_other = False # Resolved to self
        else:
            # If no Discord member found, treat as TextPlayer
            target_player_obj = TextPlayer(member)
            player_display_name = member
            # is_signing_other remains True

    state = await init_state(guild_id, channel_id)
    if not state:
        await ctx.respond("Error initializing channel state. Cannot sign up.", ephemeral=True)
        return

    # Determine team_to_sign_idx and team_name_for_msg
    team_to_sign_idx = 0
    team_name_for_msg = ""

    if context_type in ["team_6s", "team_8s"]:
        if team != 1:
            await ctx.respond("❌ In your team channel, you can only sign for team 1 (your own team). Use this command in a main channel to specify Team 1 or Team 2.", ephemeral=True)
            return
        team_to_sign_idx = 0 # Their own team
        team_name_for_msg = state.get("team_name", "Your Team")
    elif context_type in ["main_6s", "main_8s"]:
        team_to_sign_idx = team - 1 # team is 1 or 2
        team_name_for_msg = f"Team {team}"
    else: # Should be caught by "not_matchmaking" or valid_positions logic
        await ctx.respond("❌ This command is not usable in this specific channel configuration for signing.", ephemeral=True)
        return

    # Validate position (already dynamically set or user types it)
    normalized_position = position.strip().upper()
    if normalized_position not in valid_positions: # Re-validate in case dynamic choices failed or user typed
        await ctx.respond(
            f"❌ Invalid position: `{position}`. Valid positions are: `{', '.join(valid_positions)}`",
            ephemeral=True
        )
        return

    # Ensure the team_to_sign_idx is valid for the state["teams"] list
    if not (0 <= team_to_sign_idx < len(state.get("teams", []))):
        await ctx.respond(f"❌ Team {team_to_sign_idx + 1} (index {team_to_sign_idx}) is not properly configured in the channel state.", ephemeral=True)
        return

    # Get a local copy to modify
    state_copy = dict(state)
    current_team_state_copy = state_copy["teams"][team_to_sign_idx]

    # Check if player is already signed (uses get_player_position which checks all teams and subs)
    signed_team_num_existing, signed_pos_existing = get_player_position(state_copy, target_player_obj)
    if signed_pos_existing:
        existing_team_name_display = "Unknown Team"
        if context_type in ["team_6s", "team_8s"] and signed_team_num_existing == 1:
            existing_team_name_display = state.get("team_name", "Your Team")
        elif context_type in [ "main_6s", "main_8s"]:
            existing_team_name_display = f"Team {signed_team_num_existing}"
        
        await ctx.respond(
            f"⚠️ {player_display_name} is already signed as **{signed_pos_existing}** on {existing_team_name_display}. Unsign first to change.", 
            ephemeral=True
        )
        return
    
    # Check if position is taken on the target team
    if current_team_state_copy.get(normalized_position) is not None:
        player_data = current_team_state_copy[normalized_position]
        taken_by = player_data['player']
        taken_by_display = taken_by.mention if hasattr(taken_by, 'mention') and not is_text_player(taken_by) else \
                           (taken_by.display_name if hasattr(taken_by, 'display_name') and not is_text_player(taken_by) else taken_by.name)
        await ctx.respond(f"❌ Position **{normalized_position}** on {team_name_for_msg} is already taken by {taken_by_display}.", ephemeral=True)
        return

    # Unsign player from subs if they were there
    if target_player_obj in state_copy.get("subs", []):
        state_copy["subs"].remove(target_player_obj)

    # Sign player to the position
    current_team_state_copy[normalized_position] = {
        "player": target_player_obj,
        "signup_time": datetime.now(timezone.utc)
    }

    # Update the shared state with the modified copy
    update_state(channel_id, state_copy)

    # Respond publicly
    await ctx.respond(
        f"✅ Signed {player_display_name} to **{normalized_position}** for **{team_name_for_msg}**!",
        ephemeral=True
    )
    await refresh_lineup(ctx.channel, author_override=requesting_user) # Refresh with requesting user as author
    asyncio.create_task(delete_after_delay(ctx.interaction))
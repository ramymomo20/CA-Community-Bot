from ios_bot.config import *
from ios_bot.signup_manager import init_state, is_player_signed, get_player_position, refresh_lineup, TextPlayer, is_text_player, get_channel_context, EIGHTS_POSITIONS, update_state
from .utils import delete_after_delay
from ios_bot.semaphores import signup_semaphore

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
    
    _fives_positions = [
        [None, "CF", None],
        ["LM", None, "RM"],
        [None, "CB", None],
        [None, "GK", None]
    ]

    def __init__(self, team_number: int, guild_id: int, channel_id: int, context_type: str, state: dict):
        super().__init__(timeout=60)
        self.team_number = team_number
        self.guild_id = guild_id
        self.channel_id = channel_id
        
        is_eights = context_type in ["main_8s", "team_8s"]
        is_sixes = context_type in ["main_6s", "team_6s"]
        is_fives = context_type in ["main_5s", "team_5s"]
        positions_grid = self._eights_positions if is_eights else self._sixes_positions if is_sixes else self._fives_positions
        
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
            
            # Use semaphore to prevent race conditions
            async with signup_semaphore:
                current_state_cb = await init_state(self.guild_id, self.channel_id)
                
                if not current_state_cb:
                    await interaction.followup.send("❌ Invalid channel state. Please try again.", ephemeral=True)
                    return
                
                teams = current_state_cb.get("teams", [])
                if (self.team_number - 1) >= len(teams):
                    await interaction.followup.send("Invalid team selection for this channel.", ephemeral=True)
                    return

                # Race guard: do not overwrite a slot that was filled milliseconds earlier.
                if teams[self.team_number - 1].get(position_name) is not None:
                    await interaction.followup.send(
                        f"`{position_name}` is already filled. Please pick another slot.",
                        ephemeral=True
                    )
                    asyncio.create_task(delete_after_delay(interaction))
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
            if current_state_cb.get("context_type") in ["team_5s", "team_6s", "team_8s"] and self.team_number == 1 :
                team_name_desc = current_state_cb.get("team_name", "Your Team")

            public_embed = Embed( # Changed variable name for clarity
                description=f"✅ Signed **{interaction.user.mention}** to **{position_name}** for {team_name_desc}!",
                color=discord.Color.green()
            )

            # Add footer with timestamp
            timestamp = datetime.now(timezone.utc).strftime("%I:%M %p")
            public_embed.set_footer(
                text=f"Requested by {interaction.user.name} • {timestamp}",
                icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None
            )
            
            # Send public confirmation to the channel
            await interaction.channel.send(embed=public_embed)
            await refresh_lineup(interaction.channel, force_new_message=True, author_override=interaction.user)
            asyncio.create_task(delete_after_delay(interaction))

        return position_callback


@bot.slash_command(
    name="sign",
    description="Sign a player to a specific position in the current matchmaking channel."
)
async def sign_slash(
    ctx: ApplicationContext,
    position: Option(str, "Position to sign (e.g., GK, LB, CM, CF, LW, RW, LM, RM)", required=True),
    member: Option(discord.Member, "Discord member to sign (optional)", required=False) = None,
    text_player: Option(str, "Text player name (optional)", required=False) = None
):
    await ctx.defer(ephemeral=True)

    # Validate target player input
    if member and text_player:
        await ctx.followup.send("❌ Please provide either a member or a text player name, not both.", ephemeral=True)
        return

    if text_player:
        player_obj = TextPlayer(text_player.strip())
    else:
        player_obj = member or ctx.author

    # Validate channel context
    channel_context = await get_channel_context(ctx.guild_id, ctx.channel_id)
    context_type = channel_context.get("type")
    if context_type not in ["main_5s", "main_6s", "main_8s", "team_5s", "team_6s", "team_8s"]:
        await ctx.followup.send("❌ This command can only be used in a registered 5v5, 6v6, or 8v8 matchmaking channel.", ephemeral=True)
        return

    # Normalize and validate position
    pos = position.strip().upper()
    if context_type in ["main_5s", "team_5s"]:
        valid_positions = FIVES_POSITIONS
    elif context_type in ["main_6s", "team_6s"]:
        valid_positions = SIXES_POSITIONS
    else:
        valid_positions = EIGHTS_POSITIONS

    if pos not in valid_positions:
        await ctx.followup.send(f"❌ Invalid position `{pos}` for this match type. Valid: {', '.join(valid_positions)}", ephemeral=True)
        return

    # Initialize or load state
    async with signup_semaphore:
        state = await init_state(ctx.guild_id, ctx.channel_id)
        if not state:
            await ctx.followup.send("❌ Error: could not initialize channel state.", ephemeral=True)
            return

        # Prevent double-sign
        existing_team_num, existing_pos = get_player_position(state, player_obj)
        if existing_pos:
            await ctx.followup.send(f"❌ {player_obj.display_name if not is_text_player(player_obj) else player_obj.display_name} is already signed as {existing_pos} on Team {existing_team_num}.", ephemeral=True)
            return

        # Decide which team slot to place into
        team_indices = [0]
        if context_type.startswith("main_"):
            if state.get("is_challenged_by_team_name"):
                team_indices = [0]
            else:
                team_indices = [0, 1] if len(state.get("teams", [])) > 1 else [0]

        placed_team_idx = None
        state_copy = dict(state)
        teams_list = state_copy.get("teams", [])

        for idx in team_indices:
            if idx >= len(teams_list):
                continue
            team_state = teams_list[idx]
            if team_state.get(pos) is None:
                # Remove from subs if present
                if player_obj in state_copy.get("subs", []):
                    state_copy["subs"].remove(player_obj)
                team_state[pos] = {
                    "player": player_obj,
                    "signup_time": datetime.now(timezone.utc)
                }
                placed_team_idx = idx
                break

        if placed_team_idx is None:
            if context_type.startswith("main_") and state.get("is_challenged_by_team_name"):
                await ctx.followup.send(f"❌ Position `{pos}` is already filled on Team 1 and Team 2 is unavailable due to an active challenge.", ephemeral=True)
            else:
                await ctx.followup.send(f"❌ Position `{pos}` is already filled.", ephemeral=True)
            return

        update_state(ctx.channel_id, state_copy)

    # Public confirmation
    team_name_desc = f"Team {placed_team_idx + 1}"
    if context_type in ["team_5s", "team_6s", "team_8s"]:
        team_name_desc = state.get("team_name", "Your Team")

    player_display = player_obj.mention if not is_text_player(player_obj) else player_obj.display_name
    public_embed = Embed(
        description=f"✅ Signed **{player_display}** to **{pos}** for {team_name_desc}!",
        color=discord.Color.green()
    )
    timestamp = datetime.now(timezone.utc).strftime("%I:%M %p")
    footer_icon = ctx.author.display_avatar.url if ctx.author.display_avatar else None
    public_embed.set_footer(text=f"Requested by {ctx.author.display_name} • {timestamp}", icon_url=footer_icon)

    await ctx.channel.send(embed=public_embed)
    await refresh_lineup(ctx.channel, force_new_message=True, author_override=ctx.author)
    await ctx.followup.send("✅ Sign completed.", ephemeral=True)

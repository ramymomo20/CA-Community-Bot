from ios_bot.config import *
from .database_manager import get_team
import multiprocessing
from datetime import datetime, timezone
import pytz

# This dictionary will store the state of each channel's signup.
# We use a Manager().dict() to ensure it's shared across processes.
signup_states: dict[int, dict] = multiprocessing.Manager().dict()

def get_all_channel_ids_with_state():
    """Returns a list of all channel IDs that have an active state."""
    return list(signup_states.keys())

# Dictionary to store independent state for each matchmaking channel
signup_states: dict[int, dict] = {}

# --- Unified Notification Cooldown (for /here and Highlight button) --- #
# Note: This will NOT be shared across processes with this implementation.
# If /here is used in one process, the cooldown won't be seen by another.
# For now, this is acceptable as the core issue is the signup state.
notification_cooldowns: dict[int, datetime] = {}
NOTIFICATION_COOLDOWN_MINUTES = 10

def check_notification_cooldown(channel_id: int) -> tuple[bool, int]:
    """
    Check if a notification (/here or highlight) can be sent in this channel.
    Returns (can_send, minutes_remaining)
    """
    now = datetime.now()
    last_used = notification_cooldowns.get(channel_id)
    
    if not last_used:
        notification_cooldowns[channel_id] = now
        return True, 0
        
    time_diff = now - last_used
    minutes_remaining = NOTIFICATION_COOLDOWN_MINUTES - (time_diff.total_seconds() / 60)
    
    if minutes_remaining <= 0:
        notification_cooldowns[channel_id] = now
        return True, 0
        
    return False, round(minutes_remaining)

class TextPlayer:
    """Class to handle non-mention players"""
    def __init__(self, name: str):
        self.name = name
        self.display_name = name
        self.mention = name  # For text players, mention is just their name
        self.id = None  # No Discord ID for text players

def is_text_player(player) -> bool:
    """Check if a player is a TextPlayer instance"""
    return isinstance(player, TextPlayer)

async def get_channel_context(guild_id: int, channel_id: int) -> dict:
    """
    Determines the matchmaking context of a given channel.
    Returns a dictionary with 'type' and other relevant details.
    """
    # Check Main Guild Channels first
    if guild_id == MAIN_GUILD_ID:
        # The EIGHTS_MAIN_MATCHMAKING_CHANNELS lists
        # in config.py are now directly populated by the discovery logic at startup.
        if channel_id in EIGHTS_MAIN_MATCHMAKING_CHANNELS:
            return {"type": "main_8s", "guild_id": guild_id, "channel_id": channel_id}
        elif channel_id in SIXES_MAIN_MATCHMAKING_CHANNELS:
            return {"type": "main_6s", "guild_id": guild_id, "channel_id": channel_id}
    
    # Check Registered Team Channels
    team_data = await get_team(guild_id)
    if team_data:
        # Ensure team_data is a dictionary
        if not isinstance(team_data, dict):
            # Log this situation or handle as an error more explicitly if needed
            print(f"Warning: get_team({guild_id}) returned non-dict: {team_data}")
            return {"type": "not_matchmaking", "guild_id": guild_id, "channel_id": channel_id}

        team_eights_channels = team_data.get('eights_channels', [])
        team_sixes_channels = team_data.get('sixes_channels', [])
        
        if channel_id in team_eights_channels:
            return {
                "type": "team_8s", 
                "guild_id": guild_id, 
                "channel_id": channel_id,
                "team_id": guild_id,
                "team_name": team_data.get('guild_name')
            }
        elif channel_id in team_sixes_channels:
            return {
                "type": "team_6s", 
                "guild_id": guild_id, 
                "channel_id": channel_id,
                "team_id": guild_id,
                "team_name": team_data.get('guild_name')
            }
            
    return {"type": "not_matchmaking", "guild_id": guild_id, "channel_id": channel_id}

async def init_state(guild_id: int, channel_id: int, force_new: bool = False) -> dict:
    """Initialize or get existing state for a channel based on its context."""
    channel_context = await get_channel_context(guild_id, channel_id)
    context_type = channel_context.get("type")

    if context_type == "not_matchmaking":
        # print(f"Debug: init_state called for non-matchmaking channel {channel_id} in guild {guild_id}")
        return None
        
    if context_type in ["main_8s", "team_8s"]:
        positions = EIGHTS_POSITIONS
    elif context_type in ["main_6s", "team_6s"]:
        positions = SIXES_POSITIONS
    else:
        # Should not happen if not_matchmaking is caught
        # print(f"Debug: init_state received unexpected context type {context_type}")
        return None

    # Initialize state if not already present for this channel_id
    # A copy is made from the proxy object to a local dict for modification,
    # and then the entire dict is reassigned to the proxy.
    if force_new or channel_id not in signup_states:
        new_state = {}
        if context_type in ["main_8s", "main_6s"]:
            # Main channels have two teams
            new_state = {
                "teams": [
                    {p: None for p in positions},
                    {p: None for p in positions},
                ],
                "message_ids": [None, None],
                "subs": [],
                "ready": [],
                "context_type": context_type,
                "guild_id": guild_id
            }
        elif context_type in ["team_8s", "team_6s"]:
            # Team channels have one team in their state
            new_state = {
                "teams": [
                    {p: None for p in positions} # Only one team
                ],
                "message_ids": [None],
                "subs": [],
                "ready": [],
                "context_type": context_type,
                "team_name": channel_context.get("team_name"),
                "guild_id": guild_id
            }
        if new_state:
            signup_states[channel_id] = new_state
            
    return signup_states.get(channel_id)

def clear_channel_state(channel_id: int):
    """Clear the state for a specific channel from the managed dict."""
    if channel_id in signup_states:
        del signup_states[channel_id]

def get_channel_state(channel_id: int) -> dict:
    """Get the current state of a channel without initializing if it doesn't exist"""
    return signup_states.get(channel_id)

def is_player_signed(state: dict | None, player) -> bool:
    """Check if a player is signed in any team"""
    if not isinstance(state, dict):
        return False
        
    for team in state.get("teams", []):
        if not isinstance(team, dict):
            continue
        for pos, player_data in team.items():
            if player_data:
                mem = player_data['player']
                if is_text_player(mem) and is_text_player(player):
                    if mem.name.lower() == player.name.lower():
                        return True
                elif not is_text_player(mem) and not is_text_player(player):
                    if mem.id == player.id:
                        return True
    return False

def get_player_position(state: dict | None, player) -> tuple[int | None, str | None]:
    """Get the team number and position of a signed player. Returns (team_num, position) or (None, None)"""
    if not isinstance(state, dict):
        return None, None
        
    for team_idx, team_data in enumerate(state.get("teams", [])):
        if not isinstance(team_data, dict):
            continue
        for pos, player_data in team_data.items():
            if player_data:
                mem = player_data['player']
                if is_text_player(mem) and is_text_player(player):
                    if mem.name.lower() == player.name.lower():
                        return team_idx + 1, pos
                elif not is_text_player(mem) and not is_text_player(player):
                    if mem.id == player.id:
                        return team_idx + 1, pos
    return None, None

class LineupView(View):
    def __init__(self, team_idx: int):
        super().__init__(timeout=None)
        self.team_idx = team_idx

    @discord.ui.button(label="Sign", style=discord.ButtonStyle.success, custom_id="lineup:sign")
    async def sign_button(self, button, interaction: Interaction):
        # Check context before proceeding
        channel_context = await get_channel_context(interaction.guild_id, interaction.channel_id)
        if channel_context.get("type") == "not_matchmaking":
            await interaction.response.send_message("❌ This button only works in matchmaking channels.", ephemeral=True)
            return
            
        # pop up the PositionView from sign.py
        from ios_bot.commands.sign import PositionView
        await interaction.response.send_message(
            "Select which slot to sign for…",
            view=PositionView(self.team_idx + 1, interaction.guild_id, interaction.channel_id, channel_context.get("type"), get_channel_state(interaction.channel_id)),
            ephemeral=True
        )

    @discord.ui.button(label="Unsign", style=discord.ButtonStyle.danger, custom_id="lineup:unsign")
    async def unsign_button(self, button, interaction: Interaction):
        channel_context = await get_channel_context(interaction.guild_id, interaction.channel_id)
        if channel_context.get("type") == "not_matchmaking":
            await interaction.response.send_message("❌ This button only works in matchmaking channels.", ephemeral=True)
            return
            
        from ios_bot.commands.unsign import do_unsign
        await do_unsign(interaction, self.team_idx + 1)

async def refresh_lineup(arg, force_new_message: bool = False, author_override: discord.Member = None, state_override: dict = None):
    """
    If `arg` is an ApplicationContext, uses ctx.respond & ctx.followup.
    If `arg` is a TextChannel, edits/sends the persistent messages.
    Adapts for single-team (team channels) or two-team (main channels) display.
    If `force_new_message` is True, old messages are ignored and new ones are sent, updating state.
    `author_override` can be used to specify the user for the footer if `arg` is not a context.
    """
    # Ensure TeamView is imported locally if it might cause circular imports at module level
    # from ios_bot.commands.utils import TeamView 

    is_ctx = isinstance(arg, discord.ApplicationContext)
    ctx = arg if is_ctx else None
    channel = arg.channel if is_ctx else arg

    # Determine author for footer: use override if provided, else from context, else None
    author_for_footer = author_override if author_override else (ctx.user if is_ctx else None)

    if state_override:
        state = state_override
    else:
        state = await init_state(channel.guild.id, channel.id)

    if not state:
        if is_ctx:
            await ctx.respond("❌ This command/button is not valid in this channel (not a matchmaking channel or error initializing state).", ephemeral=True)
        else:
            print(f"Warning: refresh_lineup called on non-matchmaking channel {channel.id} or state init failed.")
        return

    context_type = state.get("context_type")
    team_name_from_state = state.get("team_name", "Team") # Used for team_8s title
    challenged_by_team_name = state.get("is_challenged_by_team_name") # Check for main channel challenge state
    active_challenge_game_type_display = state.get("active_challenge_game_type", "").upper()

    embeds_and_views = []
    # If main channel is challenged, only process the first team's embed
    num_teams_to_display = 1 if context_type in ["main_6s", "main_8s"] and challenged_by_team_name else len(state["teams"])

    # Access active_challenges for title modification (though direct state flags are now primary)
    from ios_bot.challenge_manager import active_challenges

    for i in range(num_teams_to_display):
        team_data = state["teams"][i]
        
        # Determine positions based on context_type (8s)
        if context_type in ["main_6s", "team_6s"]:
            positions = SIXES_POSITIONS
            game_type_display = "6v6"
        elif context_type in ["main_8s", "team_8s"]:
            positions = EIGHTS_POSITIONS
            game_type_display = "8v8"

        desc_parts = []
        for pos in positions:
            player_data = team_data.get(pos)
            player = player_data['player'] if player_data else None
            
            player_display = "❔" # Default if no player
            if player:
                if not is_text_player(player):
                    player_display = player.mention
                else:
                    player_display = player.display_name # Use display_name for TextPlayer

            desc_parts.append(f"{pos} : {player_display}")

        description = " ".join(desc_parts)

        # Determine embed color
        embed_color = discord.Color.blue() # Default for team channels
        if context_type == "main_6s":
            if i == 0: # Team 1
                embed_color = discord.Color.blue()
            elif i == 1: # Team 2
                embed_color = discord.Color.red()
        elif context_type == "main_8s":
            if i == 0: # Team 1
                embed_color = discord.Color.blue()
            elif i == 1: # Team 2
                embed_color = discord.Color.red()

        emb = discord.Embed(title="Lineup", color=embed_color, description=description)
        

        # Set embed author and title based on context
        my_team_name_for_title = team_name_from_state # Default for team channels
        opponent_team_name_for_title = None
        opponent_guild_id_for_thumbnail = None

        # Find the accepted challenge for this channel
        for ch_data_val in active_challenges.values():
            if ch_data_val.get("status") == "accepted":
                if ch_data_val.get("initiating_channel_id") == channel.id:
                    # This channel is the initiator
                    opponent_guild_id_for_thumbnail = ch_data_val.get("opponent_guild_id")
                    break
                elif ch_data_val.get("opponent_channel_id") == channel.id:
                    # This channel is the opponent
                    opponent_guild_id_for_thumbnail = ch_data_val.get("initiating_guild_id")
                    break

        # If the opponent is the main guild, use MAIN_GUILD_ID
        if opponent_guild_id_for_thumbnail == MAIN_GUILD_ID:
            opponent_team_data = await get_team(MAIN_GUILD_ID)
        else:
            opponent_team_data = await get_team(opponent_guild_id_for_thumbnail) if opponent_guild_id_for_thumbnail else None

        opponent_icon_url = opponent_team_data['guild_icon'] if opponent_team_data and opponent_team_data.get('guild_icon') else None

        if opponent_icon_url:
            emb.set_thumbnail(url=opponent_icon_url)
        else:
            # fallback to own icon
            own_team_data = await get_team(channel.guild.id)
            if own_team_data and own_team_data.get('guild_icon'):
                emb.set_thumbnail(url=own_team_data['guild_icon'])

        # Set embed title and description for all cases
        
                # Set embed title and description for all cases
        if context_type in ["team_6s", "team_8s"]:
            guild_for_icon = bot.get_guild(channel.guild.id)
            if opponent_team_name_for_title and my_team_name_for_title:
                emb.title = f"**{my_team_name_for_title}** VS **{opponent_team_name_for_title}** ({game_type_display})"
                emb.description = f"{description}"
            else:
                emb.title = f"{my_team_name_for_title} Lineup ({game_type_display})"
                emb.description = f"{description}"
            if guild_for_icon and guild_for_icon.icon:
                emb.set_author(name=guild_for_icon.name, icon_url=guild_for_icon.icon.url)
                
        elif context_type in ["main_6s", "main_8s"]:
            main_guild_obj = bot.get_guild(MAIN_GUILD_ID)
            main_guild_name = main_guild_obj.name if main_guild_obj else "Main Guild"
            if challenged_by_team_name:
                emb.title = f"**{main_guild_name} Team** VS **{challenged_by_team_name}** ({active_challenge_game_type_display})"
                emb.description = f"{description}"
            elif opponent_team_name_for_title and my_team_name_for_title:
                emb.title = f"**{my_team_name_for_title}** VS **{opponent_team_name_for_title}** ({game_type_display})"
                emb.description = f"{description}"
            elif i == 0:
                emb.title = f"**{main_guild_name} Team 1** Signup ({game_type_display})"
                emb.description = f"{description}"
            elif i == 1:
                emb.title = f"**{main_guild_name} Team 2** Signup ({game_type_display})"
                emb.description = f"{description}"
            if main_guild_obj and main_guild_obj.icon:
                emb.set_author(name=main_guild_obj.name, icon_url=main_guild_obj.icon.url)
        else:
            # Fallback for unknown context
            emb.title = f"Lineup ({game_type_display})"
            emb.description = f"{description}"

        # Add subs field if any, and only if there are subs
        subs_list = state.get("subs", [])
        if subs_list:
            sub_mentions = [s.mention if not is_text_player(s) else s.display_name for s in subs_list]
            emb.add_field(name="Subs", value=" ".join(sub_mentions), inline=False)

        # Add footer with timestamp
        timestamp = datetime.now(timezone.utc).strftime("%I:%M %p")
        footer_text = f"Requested by {author_for_footer.display_name}" if author_for_footer else "No author"
        emb.set_footer(
            text=f"{footer_text} • {timestamp}",
            icon_url=author_for_footer.display_avatar.url if author_for_footer and author_for_footer.display_avatar else None
        )
        
        # Import TeamView locally to avoid circular dependency issues
        from ios_bot.commands.utils import TeamView 
        current_view = TeamView(team_number=i + 1)
        embeds_and_views.append({"embed": emb, "view": current_view})

    # Send or edit messages
    message_ids = state.get("message_ids", [])
    new_message_ids = [None] * len(message_ids) # Prepare for new IDs if sending new

    # If main channel is challenged, ensure only one message is handled
    if context_type in ["main_6s", "main_8s"] and challenged_by_team_name:
        if embeds_and_views: # Should be exactly one
            data = embeds_and_views[0]
            current_sent_msg_id = None
            try:
                new_msg = await channel.send(embed=data["embed"], view=data["view"])
                current_sent_msg_id = new_msg.id
            except discord.HTTPException as e_send:
                print(f"Failed to send new message (main challenged, force_new={force_new_message}): {e_send}")
            
            if not isinstance(state.get("message_ids"), list) or len(state["message_ids"]) == 0:
                state["message_ids"] = [None] # Initialize for one message

            state["message_ids"][0] = current_sent_msg_id
            
            if len(state["message_ids"]) > 1:
                 state["message_ids"][1] = None

    else: # Standard processing for team channels or non-challenged main channels
        temp_new_ids_for_state = [None] * len(embeds_and_views)
        for idx, data in enumerate(embeds_and_views):
            current_sent_id_for_embed = None
            try:
                new_msg = await channel.send(embed=data["embed"], view=data["view"])
                current_sent_id_for_embed = new_msg.id
            except discord.HTTPException as e_send:
                print(f"Failed to send new message (standard, force_new={force_new_message}): {e_send}")
            
            if idx < len(temp_new_ids_for_state):
                 temp_new_ids_for_state[idx] = current_sent_id_for_embed
        
        state["message_ids"] = temp_new_ids_for_state

    if is_ctx: # Check if we have a context to respond to
        # If ctx was deferred (which it should be if coming from /lineup),
        # we use followup.send for the ephemeral message.
        try:
            await ctx.followup.send("✅ Lineup refreshed!", ephemeral=True, delete_after=5)
        except discord.HTTPException as e:
            # This might happen if the interaction already had a followup sent (e.g. an error message from /lineup)
            # or if the interaction somehow truly expired despite deferral (less likely with followup).
            print(f"Error sending followup for lineup refresh: {e}")
            # As a last resort, if followup fails, and if we absolutely need to notify, 
            # we could try sending a new message to ctx.channel, but that's not ephemeral.
            # For an ephemeral confirmation, if followup fails, it's usually best to just log it.

async def format_lineup(team_state: dict, channel_id: int, guild_id: int = None) -> str:
    """Formats a single team's lineup into a string for embeds."""
    if not team_state:
        return "Lineup not available."
    
    lineup_parts = []
    # Assuming team_state is a dict of {position: player_data}
    for pos, player_data in sorted(team_state.items()):
        player = player_data['player'] if player_data else None
        player_display = "" # Default to empty string
        if player:
            player_display = player.display_name # Works for both Member and TextPlayer

        lineup_parts.append(f"`{pos}`: {player_display}")

    return "\n".join(lineup_parts) if lineup_parts else "Empty"

async def format_ready_message(state: dict, channel_id: int, challenge_data: dict = None, is_opponent_ready: bool = False) -> tuple[str, discord.Embed]:
    """Format the ready check message with current state, adapting for challenges."""
    context_type = state.get("context_type")
    initiating_team_name_from_state = state.get("team_name", "Team 1") # For team channels

    embed_title = "Match Ready Check!"
    current_signed_players_in_channel = 0 # Players signed in the channel where /ready is displayed
    total_players_needed_for_game = 0 # Target for the ready count message (e.g. 6 for initiator's team in challenge)

    if challenge_data: # Challenge mode
        game_type = challenge_data["game_type"]
        initiator_name = challenge_data["initiating_team_name"]
        opponent_name = challenge_data["opponent_team_name"]
        embed_title = f"Challenge: {initiator_name} vs {opponent_name} ({game_type.upper()}) - Ready Check"
        
        positions_for_game = SIXES_POSITIONS if game_type == "6s" else EIGHTS_POSITIONS

        players_per_team = len(positions_for_game)
        total_players_needed_for_game = players_per_team # Initiator's team needs their players ready

        # Initiator's lineup (from current channel's state)
        team1_lineup_str = await format_lineup(state["teams"][0], channel_id, state.get("guild_id"))
        current_signed_players_in_channel = sum(1 for p in state["teams"][0].values() if p)

        # Opponent's lineup (fetch their state)
        opponent_lineup_str = "Opponent Lineup: Not available or not ready."
        if is_opponent_ready:
            opponent_state = get_channel_state(challenge_data["opponent_channel_id"])
            if opponent_state:
                opp_team_idx = 0 if challenge_data["opponent_guild_id"] != MAIN_GUILD_ID else 1
                if len(opponent_state["teams"]) > opp_team_idx:
                    opponent_lineup_str = await format_lineup(opponent_state["teams"][opp_team_idx], challenge_data["opponent_channel_id"], challenge_data["opponent_guild_id"])
        
        embed = discord.Embed(title=embed_title, color=discord.Color.orange())
        embed.add_field(name=f"{initiator_name} (Your Team)", value=f"```\n{team1_lineup_str}```", inline=False)
        embed.add_field(name=f"{opponent_name} (Opponent) - Ready: {'✅ Yes' if is_opponent_ready else '❌ No'}", value=f"```\n{opponent_lineup_str}```", inline=False)

    else: # Standard matchmaking mode
        if context_type in ["main_8s", "team_8s"]:
            positions_for_game = EIGHTS_POSITIONS
            total_players_needed_for_game = EIGHTS_PLAYERS_NEEDED if context_type == "main_8s" else len(positions_for_game)
        elif context_type in ["main_6s", "team_6s"]:
            positions_for_game = SIXES_POSITIONS
            total_players_needed_for_game = SIXES_PLAYERS_NEEDED if context_type == "main_6s" else len(positions_for_game)

        team1_lineup_str = await format_lineup(state["teams"][0], channel_id, state.get("guild_id"))
        current_signed_players_in_channel = sum(1 for p in state["teams"][0].values() if p)
        team_1_display_name = initiating_team_name_from_state if context_type == "team_8s" else "Team 1"
        embed = discord.Embed(title=embed_title, color=0x2F3136)
        embed.add_field(name=team_1_display_name, value=f"```\n{team1_lineup_str}```", inline=True)

        if context_type in ["main_6s", "main_8s"] and len(state["teams"]) > 1:
            team2_lineup_str = await format_lineup(state["teams"][1], channel_id, state.get("guild_id"))
            embed.add_field(name="Team 2", value=f"```\n{team2_lineup_str}```", inline=True)
            current_signed_players_in_channel += sum(1 for p in state["teams"][1].values() if p)
        elif context_type in ["team_6s", "team_8s"]:
            # For single team view, ensure only one team field if no challenge
            pass # Team 1 field already added, opponent is CPU or via challenge

    subs_text = ", ".join(sub.display_name for sub in state.get("subs", []) if sub) if state.get("subs") else "No subs"
    embed.add_field(name="Subs", value=subs_text, inline=False)
    
    ready_players_mentions = [p.mention for p in state.get("ready", []) if not is_text_player(p) and hasattr(p, 'mention')]
    # In challenge mode, total_players_needed_for_game refers to the initiator's team size for the ready count.
    # In standard mode, it's the total for the match (1 or 2 teams).
    content = f"Players Ready ({len(ready_players_mentions)}/{total_players_needed_for_game if challenge_data else current_signed_players_in_channel}): {(' '.join(ready_players_mentions)) if ready_players_mentions else 'Waiting for players...'}"
    content += f"\nTotal Signed in this channel: {current_signed_players_in_channel}"
    if challenge_data:
        content += f"\nOpponent Team Lineup Ready: **{'Yes' if is_opponent_ready else 'No'}**"
    
    return content, embed 

# This is an alias for refresh_lineup to be used in other modules.
# It makes the import cleaner and hides the complex logic of the original function.
async def sm_refresh_lineup(channel, force_new_message: bool = False, author_override: discord.Member = None, state_override: dict = None):
    """A simple alias for refresh_lineup."""
    await refresh_lineup(channel, force_new_message=force_new_message, author_override=author_override, state_override=state_override)

async def clear_and_refresh_channel(channel: discord.TextChannel):
    """
    Atomically clears a channel's persistent state and posts a fresh, empty lineup message.
    This is the preferred method for tasks like daily clears.
    """
    # Create a new, empty state object and persist it temporarily.
    temp_state = await init_state(channel.guild.id, channel.id, force_new=True)

    # Immediately clear the state from the manager so it doesn't persist.
    clear_channel_state(channel.id)

    # Call the refresh function, passing the temporary (now-unlinked) state.
    # This will post a new message based on the empty state without re-persisting it.
    await sm_refresh_lineup(channel, force_new_message=True, state_override=temp_state)

def update_state(channel_id: int, new_state: dict):
    """Directly update the state for a channel. Use with caution."""
    signup_states[channel_id] = new_state 
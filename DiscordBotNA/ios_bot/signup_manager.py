from ios_bot.config import *
from ios_bot.db.teams import TeamOperations
from ios_bot.utils.name_utils import get_display_name
import multiprocessing
from datetime import datetime, timezone
import pytz
import ios_bot.config as config
import json

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

def _serialize_player(player):
    """Convert a player object to a JSON-safe dict."""
    if not player:
        return None
    if is_text_player(player):
        return {
            "id": None,
            "name": player.display_name,
            "is_text": True
        }
    return {
        "id": getattr(player, "id", None),
        "name": get_display_name(player, max_length=32),
        "is_text": False
    }

def serialize_lineup_state(state: dict | None) -> dict:
    """Serialize lineup state to JSON-safe payload for DB storage."""
    if not isinstance(state, dict):
        return {"teams": [], "subs": [], "context_type": None}

    teams_payload = []
    for team in state.get("teams", []):
        if not isinstance(team, dict):
            teams_payload.append({})
            continue
        team_payload = {}
        for pos, player_data in team.items():
            player = player_data.get("player") if isinstance(player_data, dict) else None
            team_payload[pos] = _serialize_player(player)
        teams_payload.append(team_payload)

    subs_payload = []
    for sub in state.get("subs", []):
        subs_payload.append(_serialize_player(sub))

    return {
        "teams": teams_payload,
        "subs": subs_payload,
        "context_type": state.get("context_type"),
    }

def is_lineup_empty(state: dict | None) -> bool:
    """Return True if all teams and subs are empty."""
    if not isinstance(state, dict):
        return True
    subs = state.get("subs") or []
    if subs:
        return False
    for team in state.get("teams", []):
        if not isinstance(team, dict):
            continue
        for player_data in team.values():
            if isinstance(player_data, dict) and player_data.get("player") is not None:
                return False
    return True

async def persist_lineup_snapshot(guild_id: int, channel_id: int, state: dict | None):
    """Persist lineup snapshot for a guild/channel if team exists."""
    try:
        team_row = await bot.db.teams.get_team(guild_id)
        if not team_row:
            return
        if is_lineup_empty(state):
            await bot.db.teams.upsert_lineup_snapshot(guild_id, channel_id, state.get("context_type") if state else None, None)
            return
        payload = serialize_lineup_state(state)
        await bot.db.teams.upsert_lineup_snapshot(guild_id, channel_id, state.get("context_type"), payload)
    except Exception:
        pass

async def get_channel_context(guild_id: int, channel_id: int) -> dict:
    """
    Determines the matchmaking context of a given channel.
    Returns a dictionary with 'type' and other relevant details.
    """
    # Check Main Guild Channels first
    if config.MAIN_GUILD_ID and guild_id == config.MAIN_GUILD_ID:
        # The EIGHTS_MAIN_MATCHMAKING_CHANNELS lists
        # in config.py are now directly populated by the discovery logic at startup.
        if channel_id in config.EIGHTS_MAIN_MATCHMAKING_CHANNELS:
            return {"type": "main_8s", "guild_id": guild_id, "channel_id": channel_id}
        elif channel_id in config.SIXES_MAIN_MATCHMAKING_CHANNELS:
            return {"type": "main_6s", "guild_id": guild_id, "channel_id": channel_id}
        elif channel_id in config.FIVES_MAIN_MATCHMAKING_CHANNELS:
            return {"type": "main_5s", "guild_id": guild_id, "channel_id": channel_id}
    
    # Check Registered Team Channels
    team_data = await bot.db.teams.get_team(guild_id)
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
        elif channel_id in team_data.get('fives_channels', []):
            return {
                "type": "team_5s", 
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
    elif context_type in ["main_5s", "team_5s"]:
        positions = FIVES_POSITIONS
    else:
        # Should not happen if not_matchmaking is caught
        # print(f"Debug: init_state received unexpected context type {context_type}")
        return None

    # Initialize state if not already present for this channel_id
    # A copy is made from the proxy object to a local dict for modification,
    # and then the entire dict is reassigned to the proxy.
    if force_new or channel_id not in signup_states:
        new_state = {}
        if context_type in ["main_8s", "main_6s", "main_5s"]:
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
        elif context_type in ["team_8s", "team_6s", "team_5s"]:
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
            
        # Initialize state to ensure it exists
        state = await init_state(interaction.guild_id, interaction.channel_id)
        if not state:
            await interaction.response.send_message("❌ Error: Could not get channel state for signing.", ephemeral=True)
            return
            
        # pop up the PositionView from sign.py
        from ios_bot.commands.sign import PositionView
        await interaction.response.send_message(
            "Select which slot to sign for…",
            view=PositionView(self.team_idx + 1, interaction.guild_id, interaction.channel_id, channel_context.get("type"), state),
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

    # Persist lineup snapshot on every refresh (non-empty only).
    await persist_lineup_snapshot(channel.guild.id, channel.id, state)

    context_type = state.get("context_type")
    team_name_from_state = state.get("team_name", "Team") # Used for team_8s title
    challenged_by_team_name = state.get("is_challenged_by_team_name") # Check for main channel challenge state
    embeds_and_views = []

    def _team_has_goalkeeper(team_lineup: dict | None) -> bool:
        if not isinstance(team_lineup, dict):
            return False
        gk_data = team_lineup.get("GK")
        if not gk_data:
            return False
        if isinstance(gk_data, dict):
            return gk_data.get("player") is not None
        return True

    # If main channel is challenged, only process the first team's embed
    num_teams_to_display = 1 if context_type in ["main_5s", "main_6s", "main_8s"] and challenged_by_team_name else len(state["teams"])

    # Access active_challenges for title modification (though direct state flags are now primary)
    from ios_bot.challenge_manager import active_challenges

    for i in range(num_teams_to_display):
        team_data = state["teams"][i]
        
        # Determine positions based on context_type (8s)
        if context_type in ["main_6s", "team_6s"]:
            positions = SIXES_POSITIONS
        elif context_type in ["main_8s", "team_8s"]:
            positions = EIGHTS_POSITIONS
        elif context_type in ["main_5s", "team_5s"]:
            positions = FIVES_POSITIONS

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
        elif context_type == "main_5s":
            if i == 0: # Team 1
                embed_color = discord.Color.blue()
            elif i == 1: # Team 2
                embed_color = discord.Color.red()

        emb = discord.Embed(color=embed_color, description=description)
        

        # Determine challenge context for VS line and GK status
        opponent_team_name_for_title = None
        opponent_has_gk = None
        for ch_data_val in active_challenges.values():
            if ch_data_val.get("status") != "accepted":
                continue
            if ch_data_val.get("initiating_channel_id") == channel.id:
                opponent_team_name_for_title = ch_data_val.get("opponent_team_name")
                opponent_state = get_channel_state(ch_data_val.get("opponent_channel_id"))
                if not opponent_state:
                    try:
                        opponent_state = await init_state(
                            ch_data_val.get("opponent_guild_id"),
                            ch_data_val.get("opponent_channel_id")
                        )
                    except Exception:
                        opponent_state = None
                if opponent_state and opponent_state.get("teams"):
                    # Main-guild challenge overlays are rendered from Team 1 (index 0), not the spare Team 2 slot.
                    opponent_has_gk = _team_has_goalkeeper(opponent_state["teams"][0])
                break
            if ch_data_val.get("opponent_channel_id") == channel.id:
                opponent_team_name_for_title = ch_data_val.get("initiating_team_name")
                opponent_state = get_channel_state(ch_data_val.get("initiating_channel_id"))
                if not opponent_state:
                    try:
                        opponent_state = await init_state(
                            ch_data_val.get("initiating_guild_id"),
                            ch_data_val.get("initiating_channel_id")
                        )
                    except Exception:
                        opponent_state = None
                if opponent_state and opponent_state.get("teams"):
                    opponent_has_gk = _team_has_goalkeeper(opponent_state["teams"][0])
                break

        # Set embed title and description for all cases
        if context_type in ["team_5s", "team_6s", "team_8s"]:
            guild_for_icon = bot.get_guild(channel.guild.id)
            emb.description = f"{description}"
            if opponent_team_name_for_title:
                if opponent_has_gk is None:
                    emb.description = f"{description}\nvs. {opponent_team_name_for_title}"
                else:
                    gk_text = "With GK" if opponent_has_gk else "No GK"
                    emb.description = f"{description}\nvs. {opponent_team_name_for_title} **{gk_text}**"
            if guild_for_icon and guild_for_icon.icon:
                emb.set_author(name="Team List", icon_url=guild_for_icon.icon.url)
            else:
                emb.set_author(name="Team List")
                
        elif context_type in ["main_5s", "main_6s", "main_8s"]:
            main_guild_obj = bot.get_guild(config.MAIN_GUILD_ID) if config.MAIN_GUILD_ID else None
            main_guild_name = main_guild_obj.name if main_guild_obj else "Main Guild"
            if challenged_by_team_name:
                if opponent_has_gk is None:
                    emb.description = f"{description}\nvs. {challenged_by_team_name}"
                else:
                    gk_text = "With GK" if opponent_has_gk else "No GK"
                    emb.description = f"{description}\nvs. {challenged_by_team_name} **{gk_text}**"
            elif opponent_team_name_for_title:
                if opponent_has_gk is None:
                    emb.description = f"{description}\nvs. {opponent_team_name_for_title}"
                else:
                    gk_text = "With GK" if opponent_has_gk else "No GK"
                    emb.description = f"{description}\nvs. {opponent_team_name_for_title} **{gk_text}**"
            else:
                emb.description = f"{description}"
            if i == 0:
                if main_guild_obj and main_guild_obj.icon:
                    emb.set_author(name="Team List", icon_url=main_guild_obj.icon.url)
                else:
                    emb.set_author(name="Team List")
            elif i == 1:
                if main_guild_obj and main_guild_obj.icon:
                    emb.set_author(name="#2 Team List", icon_url=main_guild_obj.icon.url)
                else:
                    emb.set_author(name="#2 Team List")

        # Add subs field if any, and only if there are subs
        subs_list = state.get("subs", [])
        if subs_list:
            sub_mentions = [s.mention if not is_text_player(s) else s.display_name for s in subs_list]
            emb.add_field(name="Subs", value=" ".join(sub_mentions), inline=False)

        # Add footer with timestamp
        timestamp = datetime.now(timezone.utc).strftime("%I:%M %p")
        main_guild_for_footer = bot.get_guild(config.MAIN_GUILD_ID) if config.MAIN_GUILD_ID else None
        footer_text = f"Requested by {author_for_footer.display_name}" if author_for_footer else (main_guild_for_footer.name if main_guild_for_footer else "Main Guild")
        emb.set_footer(
            text=f"{footer_text} • {timestamp}",
            icon_url=author_for_footer.display_avatar.url if author_for_footer and author_for_footer.display_avatar else (main_guild_for_footer.icon.url if main_guild_for_footer and main_guild_for_footer.icon else None)
        )
        
        # Import TeamView locally to avoid circular dependency issues
        from ios_bot.commands.utils import TeamView 
        current_view = TeamView(team_number=i + 1)
        embeds_and_views.append({"embed": emb, "view": current_view})

    # Send or edit messages
    message_ids = state.get("message_ids", [])
    new_message_ids = [None] * len(message_ids) # Prepare for new IDs if sending new

    # Handle message sending based on context
    if context_type in ["main_5s", "main_6s", "main_8s"] and challenged_by_team_name:
        # For challenged main channels, only show one message (main guild vs challenger)
        if embeds_and_views:  # Should be exactly one
            data = embeds_and_views[0]
            current_sent_msg_id = None
            
            # Delete the second message if it exists (Team 2 embed)
            if len(message_ids) > 1 and message_ids[1] is not None:
                try:
                    old_msg = await channel.fetch_message(message_ids[1])
                    await old_msg.delete()
                    print(f"Deleted second embed (Team 2) for challenged main channel {channel.id}")
                except discord.HTTPException as e:
                    print(f"Failed to delete second embed for challenged main channel: {e}")
            
            # Try to edit existing message first, then send new if needed
            if not force_new_message and len(message_ids) > 0 and message_ids[0] is not None:
                try:
                    existing_msg = await channel.fetch_message(message_ids[0])
                    await existing_msg.edit(embed=data["embed"], view=data["view"])
                    current_sent_msg_id = message_ids[0]  # Keep the same message ID
                    print(f"Edited existing message for challenged main channel {channel.id}")
                except discord.HTTPException as e:
                    print(f"Failed to edit existing message, sending new one: {e}")
                    try:
                        new_msg = await channel.send(embed=data["embed"], view=data["view"])
                        current_sent_msg_id = new_msg.id
                    except discord.HTTPException as e_send:
                        print(f"Failed to send new message (main challenged): {e_send}")
            else:
                # Send new message
                try:
                    new_msg = await channel.send(embed=data["embed"], view=data["view"])
                    current_sent_msg_id = new_msg.id
                except discord.HTTPException as e_send:
                    print(f"Failed to send new message (main challenged, force_new={force_new_message}): {e_send}")
            
            # Update message IDs - keep only one message for challenged main channel
            if not isinstance(state.get("message_ids"), list) or len(state["message_ids"]) == 0:
                state["message_ids"] = [None]
            
            state["message_ids"][0] = current_sent_msg_id
            
            # Clear the second message ID if it exists (this will cause the second embed to be deleted)
            if len(state["message_ids"]) > 1:
                state["message_ids"][1] = None
    else:
        # Standard processing for team channels or non-challenged main channels
        temp_new_ids_for_state = [None] * len(embeds_and_views)
        for idx, data in enumerate(embeds_and_views):
            current_sent_id_for_embed = None
            
            # Try to edit existing message first, then send new if needed
            if not force_new_message and idx < len(message_ids) and message_ids[idx] is not None:
                try:
                    existing_msg = await channel.fetch_message(message_ids[idx])
                    await existing_msg.edit(embed=data["embed"], view=data["view"])
                    current_sent_id_for_embed = message_ids[idx]  # Keep the same message ID
                    print(f"Edited existing message {idx + 1} for channel {channel.id}")
                except discord.HTTPException as e:
                    print(f"Failed to edit existing message {idx + 1}, sending new one: {e}")
                    try:
                        new_msg = await channel.send(embed=data["embed"], view=data["view"])
                        current_sent_id_for_embed = new_msg.id
                    except discord.HTTPException as e_send:
                        print(f"Failed to send new message (team {idx + 1}): {e_send}")
            else:
                # Send new message
                try:
                    new_msg = await channel.send(embed=data["embed"], view=data["view"])
                    current_sent_id_for_embed = new_msg.id
                except discord.HTTPException as e_send:
                    print(f"Failed to send new message (team {idx + 1}, force_new={force_new_message}): {e_send}")
            
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

async def format_lineup(team_state: dict, channel_id: int, guild_id: int = None) -> str:
    """Formats a single team's lineup into a string for embeds."""
    if not team_state:
        return "Lineup not available."
    
    lineup_parts = []
    # Assuming team_state is a dict of {position: player_data}
    for pos, player_data in team_state.items():
        player = player_data['player'] if player_data else None
        player_display = get_display_name(player, max_length=20)

        lineup_parts.append(f"`{pos}`: {player_display}")

    return "\n".join(lineup_parts) if lineup_parts else "Empty"

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

async def restore_lineups_from_db():
    """Restore lineup snapshots into memory and refresh embeds."""
    try:
        rows = await bot.db.teams.get_lineup_snapshots()
    except Exception:
        rows = []

    if not rows:
        return

    for row in rows:
        guild_id = row.get("guild_id")
        raw_lineup = row.get("lineup") or {}
        try:
            if isinstance(raw_lineup, str):
                raw_lineup = json.loads(raw_lineup)
        except Exception:
            raw_lineup = {}

        channel_id = row.get("channel_id")
        context_type = row.get("context_type") or raw_lineup.get("context_type")
        if not channel_id or not context_type:
            continue

        channel = bot.get_channel(channel_id)
        if not channel or not channel.guild:
            continue

        if context_type in ["main_8s", "team_8s"]:
            positions = EIGHTS_POSITIONS
        elif context_type in ["main_6s", "team_6s"]:
            positions = SIXES_POSITIONS
        elif context_type in ["main_5s", "team_5s"]:
            positions = FIVES_POSITIONS
        else:
            continue

        restored_state = {
            "teams": [],
            "message_ids": [None],
            "subs": [],
            "ready": [],
            "context_type": context_type,
            "guild_id": guild_id
        }

        teams_payload = raw_lineup.get("teams", [])
        for team_payload in teams_payload:
            team_dict = {p: None for p in positions}
            if isinstance(team_payload, dict):
                for pos in positions:
                    p_data = team_payload.get(pos)
                    if not p_data:
                        continue
                    if p_data.get("is_text"):
                        team_dict[pos] = {"player": TextPlayer(p_data.get("name") or "Unknown")}
                    else:
                        member = channel.guild.get_member(p_data.get("id")) if p_data.get("id") else None
                        if member:
                            team_dict[pos] = {"player": member}
                        else:
                            team_dict[pos] = {"player": TextPlayer(p_data.get("name") or "Unknown")}
            restored_state["teams"].append(team_dict)

        subs_payload = raw_lineup.get("subs", [])
        subs_list = []
        if isinstance(subs_payload, list):
            for s in subs_payload:
                if not isinstance(s, dict):
                    continue
                if s.get("is_text"):
                    subs_list.append(TextPlayer(s.get("name") or "Unknown"))
                else:
                    member = channel.guild.get_member(s.get("id")) if s.get("id") else None
                    if member:
                        subs_list.append(member)
                    else:
                        subs_list.append(TextPlayer(s.get("name") or "Unknown"))
        restored_state["subs"] = subs_list

        # Ensure main channels keep two team slots
        if context_type in ["main_5s", "main_6s", "main_8s"] and len(restored_state["teams"]) < 2:
            restored_state["teams"].append({p: None for p in positions})
            restored_state["message_ids"] = [None, None]

        signup_states[channel_id] = restored_state

        # Refresh the visible lineup after restoring
        try:
            await sm_refresh_lineup(channel, force_new_message=True)
        except Exception:
            pass

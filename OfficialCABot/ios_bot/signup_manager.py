from ios_bot.config import *
from ios_bot.db.teams import TeamOperations
from ios_bot.utils.name_utils import get_display_name, truncate_name
import asyncio
import multiprocessing
from datetime import datetime, timedelta, timezone
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
MAX_LINEUP_EMBED_DESCRIPTION = 4096
MAX_LINEUP_SUBS_FIELD = 1024


def _lineup_player_display(player, *, max_length: int = 24) -> str:
    if player is None:
        return "❔"
    return get_display_name(player, max_length=max_length)


def _lineup_player_embed_display(player) -> str:
    if player is None:
        return "❔"
    if is_text_player(player):
        return get_display_name(player, max_length=24)
    return player.mention


def _build_lineup_description(positions, team_data, opponent_line: str | None = None) -> str:
    parts = []
    for pos in positions:
        player_data = team_data.get(pos)
        player = player_data['player'] if player_data else None
        player_display = _lineup_player_embed_display(player)
        parts.append(f"{pos}: {player_display}")

    description = " ".join(parts)
    if opponent_line:
        description = f"{description}\n{opponent_line}"
    if len(description) <= MAX_LINEUP_EMBED_DESCRIPTION:
        return description

    safe_parts = []
    for part in parts:
        candidate = " ".join(safe_parts + [part])
        if len(candidate) > MAX_LINEUP_EMBED_DESCRIPTION:
            break
        safe_parts.append(part)
    description = " ".join(safe_parts)
    if opponent_line:
        remaining = MAX_LINEUP_EMBED_DESCRIPTION - len(description) - 1
        if remaining > 3:
            description = f"{description}\n{truncate_name(opponent_line, remaining)}"
    return description[:MAX_LINEUP_EMBED_DESCRIPTION]


def _build_subs_field(subs_list) -> str:
    sub_names = []
    for sub_player in subs_list:
        if is_text_player(sub_player):
            sub_names.append(get_display_name(sub_player, max_length=24))
        else:
            sub_names.append(sub_player.mention)
    value = " ".join(sub_names)
    if len(value) <= MAX_LINEUP_SUBS_FIELD:
        return value

    kept = []
    for name in sub_names:
        candidate = " ".join(kept + [name])
        if len(candidate) > MAX_LINEUP_SUBS_FIELD - 6:
            break
        kept.append(name)
    if not kept:
        return value[:MAX_LINEUP_SUBS_FIELD]
    remaining = max(len(sub_names) - len(kept), 0)
    suffix = f" +{remaining} more" if remaining else ""
    return (" ".join(kept) + suffix)[:MAX_LINEUP_SUBS_FIELD]


async def _get_db_handle():
    """Resolve the shared db handle even when startup ordering is delayed."""
    db_handle = getattr(bot, "db", None)
    if db_handle is not None:
        return db_handle

    from ios_bot.db import Database
    from ios_bot.db.connection import get_connection_string

    db_handle = Database(get_connection_string())
    await db_handle.initialize()
    setattr(bot, "db", db_handle)
    return db_handle


async def _delete_tracked_lineup_messages(channel, message_ids) -> None:
    """Best-effort cleanup for tracked lineup messages before posting fresh ones."""
    if not channel or not isinstance(message_ids, list):
        return

    for msg_id in message_ids:
        if not msg_id:
            continue
        try:
            old_msg = await channel.fetch_message(msg_id)
            await old_msg.delete()
        except discord.HTTPException:
            continue


def _lineup_view_debug_summary(view) -> str:
    if view is None:
        return "no-view"
    try:
        items = []
        for child in getattr(view, "children", []):
            items.append(
                {
                    "type": type(child).__name__,
                    "label": getattr(child, "label", None),
                    "custom_id": getattr(child, "custom_id", None),
                    "emoji": str(getattr(child, "emoji", None) or ""),
                }
            )
        return json.dumps(items, ensure_ascii=True)
    except Exception as exc:
        return f"view-summary-error:{exc!r}"


def _channel_debug_summary(channel) -> str:
    if channel is None:
        return "no-channel"
    try:
        me = None
        if getattr(channel, "guild", None) and getattr(channel.guild, "me", None):
            me = channel.guild.me
        elif getattr(channel, "guild", None) and getattr(bot, "user", None):
            me = channel.guild.get_member(bot.user.id)

        perms = channel.permissions_for(me) if me and hasattr(channel, "permissions_for") else None
        perm_summary = None
        if perms is not None:
            perm_summary = {
                "view_channel": perms.view_channel,
                "send_messages": getattr(perms, "send_messages", None),
                "embed_links": getattr(perms, "embed_links", None),
                "read_message_history": getattr(perms, "read_message_history", None),
                "use_external_emojis": getattr(perms, "use_external_emojis", None),
            }

        return json.dumps(
            {
                "channel_id": getattr(channel, "id", None),
                "channel_name": getattr(channel, "name", None),
                "channel_type": str(getattr(channel, "type", None)),
                "channel_class": type(channel).__name__,
                "guild_id": getattr(getattr(channel, "guild", None), "id", None),
                "bot_permissions": perm_summary,
            },
            ensure_ascii=True,
        )
    except Exception as exc:
        return f"channel-summary-error:{exc!r}"


async def _send_lineup_message_with_fallback(channel, *, embed, view, context_label: str):
    try:
        return await channel.send(embed=embed, view=view)
    except discord.HTTPException as exc:
        print(
            "Failed to send lineup message with view "
            f"({context_label}, channel={getattr(channel, 'id', None)}, "
            f"embed_desc_len={len(str(getattr(embed, 'description', '') or ''))}, "
            f"field_count={len(getattr(embed, 'fields', []))}, "
            f"channel_info={_channel_debug_summary(channel)}, "
            f"view_items={_lineup_view_debug_summary(view)}): {exc}"
        )
        if view is None:
            return None
        try:
            fallback_msg = await channel.send(embed=embed)
            print(
                "Sent lineup message without view after Discord rejected interactive payload "
                f"({context_label}, channel={getattr(channel, 'id', None)})"
            )
            return fallback_msg
        except discord.HTTPException as fallback_exc:
            print(
                "Failed to send lineup message even without view "
                f"({context_label}, channel={getattr(channel, 'id', None)}): {fallback_exc}"
            )
            return None

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
        # Persisted so a bot restart mid-match doesn't silently unlock a
        # lineup that's actually still being played -- see restore_lineups_from_db,
        # which reconciles this against ACTIVE_MATCH_CONTEXTS instead of just
        # trusting it forever.
        "lineup_locked": bool(state.get("lineup_locked")),
        "lineup_lock_reason": state.get("lineup_lock_reason"),
        "lineup_locked_at": state.get("lineup_locked_at"),
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


# refresh_lineup() calls persist_lineup_snapshot() on every single sign-up/
# unsign/sub click, which used to mean a DB round-trip (a get_team lookup
# plus an upsert) every single time regardless of whether the lineup
# actually changed. This tracks the last snapshot written per channel so
# a no-op refresh (e.g. re-rendering after a failed action) skips the DB
# entirely. Lives in-process only -- worst case after a restart is one
# redundant write on the next refresh, not a correctness issue.
_last_persisted_snapshot: dict[tuple[int, int], tuple[str | None, str | None]] = {}


async def persist_lineup_snapshot(guild_id: int, channel_id: int, state: dict | None):
    """Persist lineup snapshot for a guild/channel if team exists (skips the
    DB round-trip entirely if nothing changed since the last persist)."""
    context_type = state.get("context_type") if state else None
    payload = None if is_lineup_empty(state) else serialize_lineup_state(state)
    key = (guild_id, channel_id)
    signature = (context_type, payload)
    if _last_persisted_snapshot.get(key) == signature:
        return
    try:
        db_handle = await _get_db_handle()
        team_row = await db_handle.teams.get_team(guild_id)
        if not team_row:
            return
        await db_handle.teams.upsert_lineup_snapshot(guild_id, channel_id, context_type, payload)
        _last_persisted_snapshot[key] = signature
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
    db_handle = await _get_db_handle()
    try:
        team_data = await db_handle.teams.get_team(guild_id)
    except Exception as e:
        # get_team() already falls back to its last-known-good cached value
        # on a DB error -- this only triggers when that ALSO has nothing to
        # fall back to (e.g. this guild's team was never looked up yet this
        # process AND the DB is down right now). Flag it distinctly so
        # callers on the sign/unsign/sub/ready path can tell a legitimate
        # "not a matchmaking channel" apart from "we genuinely can't tell
        # right now" and give a clearer message instead of turning users away.
        print(f"[get_channel_context] DB error resolving team for guild {guild_id}: {e}")
        return {"type": "not_matchmaking", "guild_id": guild_id, "channel_id": channel_id, "db_error": True}

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
                "guild_id": guild_id,
                "lineup_locked": False,
                "lineup_lock_reason": None,
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
                "guild_id": guild_id,
                "lineup_locked": False,
                "lineup_lock_reason": None,
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


def is_lineup_locked(state: dict | None) -> bool:
    return bool(isinstance(state, dict) and state.get("lineup_locked"))


def get_lineup_lock_reason(state: dict | None) -> str:
    if not isinstance(state, dict):
        return "This lineup is temporarily locked."
    reason = str(state.get("lineup_lock_reason") or "").strip()
    return reason or "This lineup is temporarily locked while the match is being readied."

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

        description = _build_lineup_description(positions, team_data)

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
            emb.description = description
            if opponent_team_name_for_title:
                if opponent_has_gk is None:
                    emb.description = _build_lineup_description(positions, team_data, f"vs. {truncate_name(str(opponent_team_name_for_title), 80)}")
                else:
                    gk_text = "With GK" if opponent_has_gk else "No GK"
                    emb.description = _build_lineup_description(positions, team_data, f"vs. {truncate_name(str(opponent_team_name_for_title), 80)} **{gk_text}**")
            if guild_for_icon and guild_for_icon.icon:
                emb.set_author(name="Team List", icon_url=guild_for_icon.icon.url)
            else:
                emb.set_author(name="Team List")
                
        elif context_type in ["main_5s", "main_6s", "main_8s"]:
            main_guild_obj = bot.get_guild(config.MAIN_GUILD_ID) if config.MAIN_GUILD_ID else None
            main_guild_name = main_guild_obj.name if main_guild_obj else "Main Guild"
            if challenged_by_team_name:
                if opponent_has_gk is None:
                    emb.description = _build_lineup_description(positions, team_data, f"vs. {truncate_name(str(challenged_by_team_name), 80)}")
                else:
                    gk_text = "With GK" if opponent_has_gk else "No GK"
                    emb.description = _build_lineup_description(positions, team_data, f"vs. {truncate_name(str(challenged_by_team_name), 80)} **{gk_text}**")
            elif opponent_team_name_for_title:
                if opponent_has_gk is None:
                    emb.description = _build_lineup_description(positions, team_data, f"vs. {truncate_name(str(opponent_team_name_for_title), 80)}")
                else:
                    gk_text = "With GK" if opponent_has_gk else "No GK"
                    emb.description = _build_lineup_description(positions, team_data, f"vs. {truncate_name(str(opponent_team_name_for_title), 80)} **{gk_text}**")
            else:
                emb.description = description
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
            emb.add_field(name="Subs", value=_build_subs_field(subs_list), inline=False)

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

            if force_new_message:
                await _delete_tracked_lineup_messages(channel, message_ids)
            elif len(message_ids) > 1 and message_ids[1] is not None:
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
                    new_msg = await _send_lineup_message_with_fallback(
                        channel,
                        embed=data["embed"],
                        view=data["view"],
                        context_label="main-challenged-edit-fallback",
                    )
                    if new_msg is not None:
                        current_sent_msg_id = new_msg.id
            else:
                # Send new message
                new_msg = await _send_lineup_message_with_fallback(
                    channel,
                    embed=data["embed"],
                    view=data["view"],
                    context_label=f"main-challenged-force_new={force_new_message}",
                )
                if new_msg is not None:
                    current_sent_msg_id = new_msg.id
            
            # Update message IDs - keep only one message for challenged main channel
            if not isinstance(state.get("message_ids"), list) or len(state["message_ids"]) == 0:
                state["message_ids"] = [None]
            
            state["message_ids"][0] = current_sent_msg_id
            
            # Clear the second message ID if it exists (this will cause the second embed to be deleted)
            if len(state["message_ids"]) > 1:
                state["message_ids"][1] = None
    else:
        # Standard processing for team channels or non-challenged main channels
        if force_new_message:
            await _delete_tracked_lineup_messages(channel, message_ids)
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
                    new_msg = await _send_lineup_message_with_fallback(
                        channel,
                        embed=data["embed"],
                        view=data["view"],
                        context_label=f"team-{idx + 1}-edit-fallback",
                    )
                    if new_msg is not None:
                        current_sent_id_for_embed = new_msg.id
            else:
                # Send new message
                new_msg = await _send_lineup_message_with_fallback(
                    channel,
                    embed=data["embed"],
                    view=data["view"],
                    context_label=f"team-{idx + 1}-force_new={force_new_message}",
                )
                if new_msg is not None:
                    current_sent_id_for_embed = new_msg.id
            
            if idx < len(temp_new_ids_for_state):
                 temp_new_ids_for_state[idx] = current_sent_id_for_embed
        
        state["message_ids"] = temp_new_ids_for_state

    if is_ctx: # Check if we have a context to respond to
        # If ctx was deferred (which it should be if coming from /lineup),
        # we use followup.send for the ephemeral message.
        try:
            await ctx.followup.send("✅ Lineup refreshed!", ephemeral=True)
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
        player_display = _lineup_player_display(player, max_length=20)
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


def set_lineup_locked(channel_id: int, locked: bool, reason: str | None = None):
    state = get_channel_state(channel_id)
    if not isinstance(state, dict):
        return

    updated_state = dict(state)
    updated_state["lineup_locked"] = bool(locked)
    updated_state["lineup_lock_reason"] = str(reason or "").strip() if locked else None
    updated_state["lineup_locked_at"] = datetime.now(timezone.utc).isoformat() if locked else None
    update_state(channel_id, updated_state)

    # Persist promptly rather than waiting for the next incidental
    # refresh_lineup() call -- if the bot crashes in the gap between locking
    # and that next refresh, restore_lineups_from_db needs the lock to
    # already be on disk to avoid silently reopening a lineup for a match
    # that's still actually being played.
    guild_id = updated_state.get("guild_id")
    if guild_id:
        try:
            asyncio.get_running_loop().create_task(
                persist_lineup_snapshot(guild_id, channel_id, updated_state)
            )
        except RuntimeError:
            pass  # no running event loop -- next natural refresh will persist it

async def restore_lineups_from_db():
    """Restore lineup snapshots into memory and refresh embeds."""
    try:
        db_handle = await _get_db_handle()
        rows = await db_handle.teams.get_lineup_snapshots()
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

        # Reconcile a persisted lock against ACTIVE_MATCH_CONTEXTS instead of
        # trusting it forever. Surviving a restart is exactly what we want
        # when the match is genuinely still being played -- but a lock that's
        # gone stale (the match actually finished, or was abandoned before
        # ever completing) should self-heal here instead of permanently
        # blocking that channel until someone notices and runs a clear command.
        was_locked = bool(raw_lineup.get("lineup_locked"))
        lock_reason = raw_lineup.get("lineup_lock_reason")
        locked_at_raw = raw_lineup.get("lineup_locked_at")
        if was_locked:
            locked_at = None
            if locked_at_raw:
                try:
                    locked_at = datetime.fromisoformat(locked_at_raw)
                except Exception:
                    locked_at = None

            # IOSoccer matches don't run for hours -- a lock older than this
            # is almost certainly a crashed/abandoned match, not a live one.
            if locked_at is None or (datetime.now(timezone.utc) - locked_at) > timedelta(hours=3):
                print(f"[restore_lineups_from_db] Channel {channel_id}'s lock is stale (locked_at={locked_at_raw}); clearing it")
                was_locked = False
                lock_reason = None
            else:
                try:
                    open_context = await db_handle.matches.get_open_active_match_context_for_channel(channel_id)
                except Exception:
                    open_context = None
                if not open_context:
                    print(f"[restore_lineups_from_db] Channel {channel_id} was locked but no open match context still tracks it; clearing lock")
                    was_locked = False
                    lock_reason = None
                else:
                    print(
                        f"[restore_lineups_from_db] Channel {channel_id} lock confirmed against an open "
                        f"{open_context.get('game_type') or 'unknown-format'} match context "
                        f"({open_context.get('team1_name')} vs {open_context.get('team2_name')}); keeping it locked"
                    )

        restored_state["lineup_locked"] = was_locked
        restored_state["lineup_lock_reason"] = lock_reason if was_locked else None
        restored_state["lineup_locked_at"] = locked_at_raw if was_locked else None

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

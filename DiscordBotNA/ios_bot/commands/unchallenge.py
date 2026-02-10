from ios_bot.config import *
from ios_bot.challenge_manager import active_challenges
from ios_bot.signup_manager import get_channel_state, init_state, refresh_lineup as sm_refresh_lineup
import ios_bot.config as config

async def _clear_main_channel_flags(game_type: str, author: discord.Member | None = None) -> None:
    """Clear challenge flags for main guild channels for the given game type."""
    if not config.MAIN_GUILD_ID:
        return

    if game_type == "8s":
        channel_ids = list(config.EIGHTS_MAIN_MATCHMAKING_CHANNELS or [])
    elif game_type == "6s":
        channel_ids = list(config.SIXES_MAIN_MATCHMAKING_CHANNELS or [])
    else:
        channel_ids = list(config.FIVES_MAIN_MATCHMAKING_CHANNELS or [])

    for ch_id in channel_ids:
        try:
            state = await init_state(config.MAIN_GUILD_ID, ch_id, force_new=False)
            if not state:
                state = get_channel_state(ch_id)
            if state:
                state.pop("is_challenged_by_team_name", None)
                state.pop("active_challenge_game_type", None)
                # Drop old message IDs so a fresh embed is posted
                if isinstance(state.get("message_ids"), list):
                    state["message_ids"] = [None for _ in state["message_ids"]]
            ch = bot.get_channel(ch_id)
            if ch:
                # Best effort: delete old messages before posting new
                try:
                    if state and isinstance(state.get("message_ids"), list):
                        for msg_id in state["message_ids"]:
                            if msg_id:
                                try:
                                    msg = await ch.fetch_message(msg_id)
                                    await msg.delete()
                                except Exception:
                                    pass
                except Exception:
                    pass
                await sm_refresh_lineup(ch, force_new_message=True, author_override=author)
        except Exception:
            continue

@bot.slash_command(
    name="unchallenge",
    description="Cancel an outgoing challenge or leave an accepted one."
)
async def unchallenge_command(ctx: ApplicationContext):
    guild_id = ctx.guild_id
    channel_id = ctx.channel_id
    user_team_data = await bot.db.teams.get_team(guild_id)

    #print(f"[UNCHALLENGE DEBUG] Command run by guild: {guild_id}, channel: {channel_id}") # DEBUG

    if not user_team_data:
        await ctx.respond("This command can only be used from a registered IOSCA team's server.", ephemeral=True)
        return

    challenge_to_modify_id = None
    challenge_data = None
    action_type = None # "initiator_cancel" or "opponent_leave"

    # Find if the current team is an initiator or an opponent in an active/accepted challenge
    #print(f"[UNCHALLENGE DEBUG] Searching active_challenges ({len(active_challenges)} entries):") # DEBUG
    for ch_id, ch_d in active_challenges.items():
        #print(f"[UNCHALLENGE DEBUG] Checking challenge ID: {ch_id}, Details: {ch_d}") # DEBUG
        
        # Debugging the exact values and types for initiator check
        cond1 = ch_d.get("initiating_channel_id") == int(channel_id)
        cond2 = ch_d.get("initiating_guild_id") == int(guild_id)
        cond3 = ch_d["status"] in ["pending_broadcast", "pending_direct", "accepted"]

        # Case 1: User's team is the initiator
        if cond1 and cond2 and cond3:
            challenge_to_modify_id = ch_id
            challenge_data = ch_d
            action_type = "initiator_cancel"
            #print(f"[UNCHALLENGE DEBUG] Matched as INITIATOR for challenge ID: {ch_id}") # DEBUG
            break
        # Case 2: User's team is the opponent in an accepted challenge
        elif ch_d.get("opponent_channel_id") == int(channel_id) and \
             ch_d.get("opponent_guild_id") == int(guild_id) and \
             ch_d["status"] == "accepted":
            challenge_to_modify_id = ch_id
            challenge_data = ch_d
            action_type = "opponent_leave"
            #print(f"[UNCHALLENGE DEBUG] Matched as OPPONENT for challenge ID: {ch_id}") # DEBUG
            break

    # Fallback: allow initiator to unchallenge even if using a different channel
    if not challenge_to_modify_id:
        for ch_id, ch_d in active_challenges.items():
            if ch_d.get("initiating_guild_id") == int(guild_id) and ch_d.get("status") in ["pending_broadcast", "pending_direct", "accepted"]:
                challenge_to_modify_id = ch_id
                challenge_data = ch_d
                action_type = "initiator_cancel"
                break
            
    if not challenge_to_modify_id or not challenge_data:
        #print(f"[UNCHALLENGE DEBUG] No matching challenge found for guild {guild_id}, channel {channel_id}.") # DEBUG
        await ctx.respond("No active challenge found where your team is the initiator, or no accepted challenge found where your team is the opponent.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    initiating_team_name = challenge_data["initiating_team_name"]
    opponent_team_name = challenge_data.get("opponent_team_name", "The other team") # Opponent might not be set if pending
    original_status = challenge_data["status"]

    if action_type == "initiator_cancel":
        challenge_data["status"] = "cancelled_by_initiator"
        response_message = f"Challenge initiated by {initiating_team_name} has been cancelled."
        notification_to_opponent = f"The challenge from **{initiating_team_name}** has been cancelled by them."
        notification_to_main_guild = f"The accepted challenge from **{initiating_team_name}** (vs Main Guild) has been cancelled by the initiator."

    elif action_type == "opponent_leave":
        challenge_data["status"] = "cancelled_by_opponent"
        # opponent_team_name here is the current user's team name.
        # We get it directly from user_team_data for accuracy.
        current_user_team_name = user_team_data.get("guild_name", "Your team")
        response_message = f"Your team ({current_user_team_name}) has left the accepted challenge against {initiating_team_name}."
        notification_to_initiator = f"Team **{current_user_team_name}** has left the accepted challenge."
        # If opponent was Main Guild (should not happen for opponent_leave as main guild doesn't use /unchallenge this way)
        # This logic path is more for when the team *is* the opponent_guild_id, not the main guild itself.
    else: # Should not happen
        await ctx.followup.send("Internal error: Could not determine action type.", ephemeral=True)
        return

    # --- Revert Embeds and Notify ---

    # 1. Collect channels to refresh for any undo path
    channels_to_refresh = set()
    if challenge_data.get("initiating_channel_id"):
        channels_to_refresh.add(challenge_data["initiating_channel_id"])
    if challenge_data.get("opponent_channel_id"):
        channels_to_refresh.add(challenge_data["opponent_channel_id"])
    for ch_id in (challenge_data.get("broadcast_messages") or {}).keys():
        channels_to_refresh.add(ch_id)

    # 2. Revert Initiator's Embed + notify
    try:
        initiating_channel_obj = bot.get_channel(challenge_data["initiating_channel_id"])
        if initiating_channel_obj:
            if action_type == "opponent_leave" and notification_to_initiator:
                await initiating_channel_obj.send(notification_to_initiator)
    except Exception as e:
        print(f"Error notifying initiator on unchallenge: {e}")

    # 3. Handle Opponent's Side (if it was an accepted challenge)
    if original_status == "accepted":
        opponent_guild_id = challenge_data.get("opponent_guild_id")
        opponent_channel_id = challenge_data.get("opponent_channel_id")

        if opponent_guild_id and opponent_channel_id:
            # Case A: Opponent was a specific team
            if opponent_guild_id != MAIN_GUILD_ID:
                try:
                    opponent_channel_obj = bot.get_channel(opponent_channel_id)
                    if opponent_channel_obj:
                        if action_type == "initiator_cancel" and notification_to_opponent: # Notify opponent if initiator cancelled
                            await opponent_channel_obj.send(notification_to_opponent)
                except Exception as e:
                    print(f"Error reverting opponent team's embed or notifying: {e}")
            
            # Case B: Opponent was the Main Guild team
            elif opponent_guild_id == MAIN_GUILD_ID:
                try:
                    main_guild_match_channel = bot.get_channel(opponent_channel_id)
                    if main_guild_match_channel:
                        if action_type == "initiator_cancel" and notification_to_main_guild:
                             await main_guild_match_channel.send(notification_to_main_guild)
                        
                        # Reset main channel state to default two-team lineup and clear challenge flags
                        main_state = await init_state(MAIN_GUILD_ID, opponent_channel_id, force_new=False)
                        if not main_state:
                            main_state = get_channel_state(opponent_channel_id)
                        if main_state:
                            main_state.pop("is_challenged_by_team_name", None)
                            main_state.pop("active_challenge_game_type", None)
                            if isinstance(main_state.get("message_ids"), list):
                                main_state["message_ids"] = [None for _ in main_state["message_ids"]]

                    # Fallback: clear flags across main channels for this game type
                    await _clear_main_channel_flags(challenge_data.get("game_type", "5s"), author=ctx.author)
                except Exception as e:
                    print(f"Error reverting Main Guild embed on unchallenge: {e}")

    # 4. Clean up broadcast messages if it was a pending challenge cancelled by initiator
    if action_type == "initiator_cancel" and challenge_data.get("target_type") in ["broadcast", "team"] and original_status != "accepted":
        for bc_channel_id, bc_msg_id in challenge_data.get("broadcast_messages", {}).items():
            try:
                target_ch = bot.get_channel(bc_channel_id)
                if target_ch:
                    msg_to_edit = await target_ch.fetch_message(bc_msg_id)
                    await msg_to_edit.edit(content=f"The challenge from **{initiating_team_name}** has been cancelled.", embed=None, view=None)
            except Exception as e:
                print(f"Error cleaning broadcast msg {bc_msg_id} in {bc_channel_id}: {e}")
    
    challenge_data["broadcast_messages"] = {} # Clear anyway, as they are now stale

    # Ensure the challenge is removed from the active list
    if challenge_to_modify_id and challenge_to_modify_id in active_challenges:
        del active_challenges[challenge_to_modify_id]

    # 5. Refresh all related lineup embeds (best-effort)
    for ch_id in channels_to_refresh:
        try:
            ch = bot.get_channel(ch_id)
            if ch:
                state = get_channel_state(ch_id)
                if state:
                    state.pop("is_challenged_by_team_name", None)
                    state.pop("active_challenge_game_type", None)
                    if isinstance(state.get("message_ids"), list):
                        old_ids = list(state["message_ids"])
                        state["message_ids"] = [None for _ in state["message_ids"]]
                        for msg_id in old_ids:
                            if msg_id:
                                try:
                                    msg = await ch.fetch_message(msg_id)
                                    await msg.delete()
                                except Exception:
                                    pass
                await sm_refresh_lineup(ch, force_new_message=True, author_override=ctx.author)
        except Exception:
            continue

    await ctx.followup.send(response_message)

from ios_bot.config import *
from ios_bot.challenge_manager import active_challenges
from ios_bot.signup_manager import get_channel_state, init_state, refresh_lineup as sm_refresh_lineup, get_channel_context
from ios_bot.database_manager import get_team # Removed clear_challenge as it's not used from DB

@bot.slash_command(
    name="unchallenge",
    description="Cancel an outgoing challenge or leave an accepted one."
)
async def unchallenge_command(ctx: ApplicationContext):
    guild_id = ctx.guild_id
    channel_id = ctx.channel_id
    user_team_data = await get_team(guild_id)

    #print(f"[UNCHALLENGE DEBUG] Command run by guild: {guild_id}, channel: {channel_id}") # DEBUG

    if not user_team_data:
        await ctx.respond("❌ This command can only be used from a registered IOSCA team's server.", ephemeral=True)
        return

    channel_context = await get_channel_context(guild_id, channel_id)
    if channel_context.get("type") not in ["team_8s", "team_6s"]:
        await ctx.respond("❌ This command must be used from one of your team's registered matchmaking channels.", ephemeral=True)
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
            
    if not challenge_to_modify_id or not challenge_data:
        #print(f"[UNCHALLENGE DEBUG] No matching challenge found for guild {guild_id}, channel {channel_id}.") # DEBUG
        await ctx.respond("❌ No active challenge found where your team is the initiator, or no accepted challenge found where your team is the opponent.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    initiating_team_name = challenge_data["initiating_team_name"]
    opponent_team_name = challenge_data.get("opponent_team_name", "The other team") # Opponent might not be set if pending
    original_status = challenge_data["status"]

    if action_type == "initiator_cancel":
        challenge_data["status"] = "cancelled_by_initiator"
        response_message = f"✅ Challenge initiated by {initiating_team_name} has been cancelled."
        notification_to_opponent = f"The challenge from **{initiating_team_name}** has been cancelled by them."
        notification_to_main_guild = f"The accepted challenge from **{initiating_team_name}** (vs Main Guild) has been cancelled by the initiator."

    elif action_type == "opponent_leave":
        challenge_data["status"] = "cancelled_by_opponent"
        # opponent_team_name here is the current user's team name.
        # We get it directly from user_team_data for accuracy.
        current_user_team_name = user_team_data.get("guild_name", "Your team")
        response_message = f"✅ Your team ({current_user_team_name}) has left the accepted challenge against {initiating_team_name}."
        notification_to_initiator = f"Team **{current_user_team_name}** has left the accepted challenge."
        # If opponent was Main Guild (should not happen for opponent_leave as main guild doesn't use /unchallenge this way)
        # This logic path is more for when the team *is* the opponent_guild_id, not the main guild itself.
    else: # Should not happen
        await ctx.followup.send("Internal error: Could not determine action type.", ephemeral=True)
        return

    # --- Revert Embeds and Notify ---

    # 1. Revert Initiator's Embed
    try:
        initiating_channel_obj = bot.get_channel(challenge_data["initiating_channel_id"])
        if initiating_channel_obj:
            if action_type == "opponent_leave" and notification_to_initiator: # Notify initiator if opponent left
                 await initiating_channel_obj.send(notification_to_initiator)
            await sm_refresh_lineup(initiating_channel_obj, force_new_message=True, author_override=ctx.author)
    except Exception as e:
        print(f"Error reverting initiator's embed or notifying on unchallenge: {e}")

    # 2. Handle Opponent's Side (if it was an accepted challenge)
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
                        await sm_refresh_lineup(opponent_channel_obj, force_new_message=True, author_override=ctx.author)
                except Exception as e:
                    print(f"Error reverting opponent team's embed or notifying: {e}")
            
            # Case B: Opponent was the Main Guild team
            elif opponent_guild_id == MAIN_GUILD_ID:
                try:
                    main_guild_match_channel = bot.get_channel(opponent_channel_id)
                    if main_guild_match_channel:
                        if action_type == "initiator_cancel" and notification_to_main_guild:
                             await main_guild_match_channel.send(notification_to_main_guild)
                        
                        # Clear challenge flags from main channel state
                        main_channel_state = get_channel_state(opponent_channel_id)
                        if main_channel_state:
                            if "is_challenged_by_team_name" in main_channel_state:
                                del main_channel_state["is_challenged_by_team_name"]
                            if "active_challenge_game_type" in main_channel_state:
                                del main_channel_state["active_challenge_game_type"]
                            if len(main_channel_state.get("message_ids", [])) > 1 and main_channel_state["message_ids"][1] is None:
                                print(f"Info: Unchallenge (main) found message_ids[1] as None, refresh_lineup will send new.")
                        
                        await sm_refresh_lineup(main_guild_match_channel, force_new_message=True, author_override=ctx.author)
                except Exception as e:
                    print(f"Error reverting Main Guild embed on unchallenge: {e}")

    # 3. Clean up broadcast messages if it was a pending challenge cancelled by initiator
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

    await ctx.followup.send(response_message)
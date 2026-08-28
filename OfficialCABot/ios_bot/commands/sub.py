from ios_bot.config import *
from ios_bot.semaphores import get_channel_semaphore
from ios_bot.signup_manager import (
    get_channel_context,
    get_lineup_lock_reason,
    get_player_position,
    init_state,
    is_lineup_locked,
    is_player_signed,
    refresh_lineup,
    update_state,
)


@bot.slash_command(
    name="sub",
    description="Sign up as a substitute for the current matchmaking channel.",
)
async def sub(ctx: ApplicationContext):
    guild_id = ctx.guild_id
    channel_id = ctx.channel_id

    await ctx.defer(ephemeral=True)

    channel_context = await get_channel_context(guild_id, channel_id)
    if channel_context.get("type") == "not_matchmaking":
        if channel_context.get("db_error"):
            await ctx.followup.send("⚠️ The bot's database is temporarily unavailable, so this channel can't be verified right now. Please try again in a moment.", ephemeral=True)
            return
        await ctx.followup.send("This command can only be used in a registered matchmaking channel.", ephemeral=True)
        return

    player = ctx.author

    async with get_channel_semaphore(channel_id):
        state = await init_state(guild_id, channel_id)
        if not state:
            await ctx.followup.send("Error: Channel state not found or could not be initialized.", ephemeral=True)
            return

        if is_lineup_locked(state):
            await ctx.followup.send(get_lineup_lock_reason(state), ephemeral=True)
            return

        if is_player_signed(state, player):
            signed_team_num, signed_pos = get_player_position(state, player)
            team_name_desc = f"Team {signed_team_num}"
            if state.get("context_type") in ["team_5s", "team_6s", "team_8s"] and signed_team_num == 1:
                team_name_desc = state.get("team_name", "your team")
            await ctx.followup.send(
                f"You are already signed for **{signed_pos}** on {team_name_desc}. Unsign first if you want to be a sub.",
                ephemeral=True,
            )
            return

        subs = list(state.get("subs", []))
        if player in subs:
            await ctx.followup.send("You are already signed up as a substitute.", ephemeral=True)
            return

        state_copy = dict(state)
        state_copy.setdefault("subs", []).append(player)
        update_state(channel_id, state_copy)

    print(f"[SUB] {player.display_name} added to subs list. Total subs: {len(state_copy.get('subs', []))}")
    await ctx.followup.send(f"{player.mention} has been added to the substitutes list.", ephemeral=True)
    await refresh_lineup(ctx.channel, force_new_message=True, author_override=ctx.author)

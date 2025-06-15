from ios_bot.config import *
from ios_bot.signup_manager import init_state, is_player_signed, get_player_position, refresh_lineup, get_channel_context # Context utility

@bot.slash_command(
    name="sub",
    description="Sign up as a substitute for the current matchmaking channel."
)
async def sub(ctx: ApplicationContext):
    guild_id = ctx.guild_id
    channel_id = ctx.channel_id

    channel_context = await get_channel_context(guild_id, channel_id)
    if channel_context.get("type") == "not_matchmaking":
        await ctx.respond("❌ This command can only be used in a registered matchmaking channel.", ephemeral=True)
        return

    state = await init_state(guild_id, channel_id)
    if not state:
        await ctx.respond("Error: Channel state not found or could not be initialized.", ephemeral=True)
        return

    player = ctx.author

    # Check if already signed in a main position
    if is_player_signed(state, player):
        signed_team_num, signed_pos = get_player_position(state, player)
        team_name_desc = f"Team {signed_team_num}"
        if state.get("context_type") in ["team_6s", "team_8s"] and signed_team_num == 1:
            team_name_desc = state.get("team_name", "your team")
        await ctx.respond(f"⚠️ You are already signed for **{signed_pos}** on {team_name_desc}. Unsign first if you want to be a sub.", ephemeral=True)
        return

    # Check if already a sub
    if player in state.get("subs", []):
        await ctx.respond("⚠️ You are already signed up as a substitute.", ephemeral=True)
        return

    state.setdefault("subs", []).append(player)
    await ctx.respond(f"✅ {player.mention} has been added to the substitutes list!", ephemeral=True)
    
    # Then, refresh the public lineup which might show sub counts or lists via format_ready_message indirectly
    await refresh_lineup(ctx.channel) 
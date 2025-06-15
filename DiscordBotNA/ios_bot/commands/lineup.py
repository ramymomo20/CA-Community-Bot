from ios_bot.config import *
from ios_bot.signup_manager import init_state, refresh_lineup, get_channel_context
from .utils import delete_after_delay

@bot.slash_command(
    name="lineup",
    description="Display the current matchmaking lineup(s) for this channel."
)
async def lineup(ctx: ApplicationContext):
    """Refreshes and displays the lineup embeds for the current channel."""
    channel_context = await get_channel_context(ctx.guild_id, ctx.channel.id)
    
    if channel_context.get("type") == "not_matchmaking":
        await ctx.respond("❌ This command can only be used in a registered matchmaking channel.", ephemeral=True)
        return

    # Defer the interaction as refresh_lineup can take time and we want an ephemeral followup.
    await ctx.defer(ephemeral=True) 

    state = await init_state(ctx.guild_id, ctx.channel.id)
    if not state:
        # Interaction already deferred, so use followup
        await ctx.followup.send("❌ Invalid channel state. Could not initialize or retrieve.", ephemeral=True)
        return
        
    await refresh_lineup(ctx, force_new_message=True)
    # The refresh_lineup function will now handle sending the ephemeral "Lineup refreshed!" message if called with ctx.
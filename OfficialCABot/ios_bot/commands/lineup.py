from ios_bot.config import *
from ios_bot.signup_manager import init_state, refresh_lineup, get_channel_context
from .utils import try_defer_interaction


@bot.slash_command(
    name="lineup",
    description="Display the current matchmaking lineup(s) for this channel."
)
async def lineup(ctx: ApplicationContext):
    """Refreshes and displays the lineup embeds for the current channel."""
    if not await try_defer_interaction(ctx.interaction, ephemeral=True):
        return

    channel_context = await get_channel_context(ctx.guild_id, ctx.channel.id)
    if channel_context.get("type") == "not_matchmaking":
        if channel_context.get("db_error"):
            await ctx.followup.send(
                "⚠️ The bot's database is temporarily unavailable, so this channel can't be verified right now. Please try again in a moment.",
                ephemeral=True,
            )
            return
        await ctx.followup.send(
            "This command can only be used in a registered matchmaking channel.",
            ephemeral=True,
        )
        return

    state = await init_state(ctx.guild_id, ctx.channel.id)
    if not state:
        await ctx.followup.send(
            "Invalid channel state. Could not initialize or retrieve.",
            ephemeral=True,
        )
        return

    await refresh_lineup(ctx, force_new_message=True)
    # The refresh_lineup function will handle the ephemeral refresh confirmation.

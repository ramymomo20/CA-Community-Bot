from ios_bot.config import *
from ios_bot.signup_manager import check_notification_cooldown, get_channel_context

@bot.slash_command(
    name="here",
    description="Highlight everyone in the channel"
)
async def here(ctx: ApplicationContext):
    channel_context = await get_channel_context(ctx.guild_id, ctx.channel_id)
    if channel_context.get("type") == "not_matchmaking":
        return await ctx.respond(
            "❌ This command only works in a registered matchmaking channel.",
            ephemeral=True
        )

    can_send, minutes_remaining = check_notification_cooldown(ctx.channel_id)

    if can_send:
        await ctx.respond("@here", allowed_mentions=discord.AllowedMentions(everyone=True))
    else:
        await ctx.respond(f"❌ Please wait {minutes_remaining} minute(s).", ephemeral=True)
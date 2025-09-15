import time as clock
from ios_bot.config import *
from ios_bot.signup_manager import check_notification_cooldown, get_channel_context

# Simple rate limiting for /here command
here_rate_limits = {}  # {channel_id: [timestamps]}

def check_here_rate_limit(channel_id: int, max_requests: int = 2, time_window: float = 3.0) -> tuple[bool, float]:
    """
    Check if a /here command can be sent in a channel.
    Returns (can_proceed, wait_time)
    """
    now = clock.time()
    
    # Clean old timestamps
    if channel_id in here_rate_limits:
        here_rate_limits[channel_id] = [
            ts for ts in here_rate_limits[channel_id]
            if now - ts < time_window
        ]
    else:
        here_rate_limits[channel_id] = []
    
    # Check if we can proceed
    if len(here_rate_limits[channel_id]) < max_requests:
        here_rate_limits[channel_id].append(now)
        return True, 0.0
    
    # Calculate wait time
    oldest_timestamp = min(here_rate_limits[channel_id])
    wait_time = max(0.0, time_window - (now - oldest_timestamp))
    return False, wait_time

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
    can_here, wait_time = check_here_rate_limit(ctx.channel_id)

    if not can_here:
        await ctx.respond(f"⚠️ Please wait {wait_time:.1f} seconds before using /here again.", ephemeral=True)
        return

    if can_send:
        try:
            await ctx.respond("@here", allowed_mentions=discord.AllowedMentions(everyone=True))
        except discord.HTTPException as e:
            if e.status == 429:  # Rate limit error
                await ctx.respond("⚠️ Rate limit reached. Please wait a moment before trying again.", ephemeral=True)
                print(f"[HERE RATE LIMIT] Channel {ctx.channel_id}: HTTP {e.status}")
            else:
                await ctx.respond(f"❌ Error sending message: HTTP {e.status}", ephemeral=True)
                print(f"[HERE ERROR] HTTP {e.status}: {str(e)[:200]}...")
    else:
        await ctx.respond(f"❌ Please wait {minutes_remaining} minute(s).", ephemeral=True)
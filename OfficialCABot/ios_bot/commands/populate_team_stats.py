from ios_bot.config import *


# /populate_team_stats was removed -- it was an exact duplicate of
# /backfill_match_stats below (same backfill_match_team_links() call, just
# with the threshold hardcoded to 0.8, which is backfill_match_stats' own
# default).

@bot.slash_command(
    name="backfill_match_stats",
    description="Backfill match_stats home/away guild IDs using fuzzy name matching."
)
@commands.has_permissions(administrator=True)
async def backfill_match_stats(
    ctx: ApplicationContext,
    threshold: Option(float, "Fuzzy match threshold (0.6 - 0.95)", required=False, default=0.8)
):
    await ctx.defer(ephemeral=True)
    try:
        if threshold < 0.6 or threshold > 0.95:
            await ctx.followup.send("❌ Threshold must be between 0.6 and 0.95.", ephemeral=True)
            return

        result = await bot.db.matches.backfill_match_team_links(threshold=threshold)
        message = (
            f"Scanned {result['matches_scanned']} matches. "
            f"Updated {result['matches_updated']} matches "
            f"(home: {result['home_linked']}, away: {result['away_linked']})."
        )
        await ctx.followup.send(message, ephemeral=True)
    except Exception as e:
        await ctx.followup.send(f"Failed to backfill match stats: {e}", ephemeral=True)


@backfill_match_stats.error
async def backfill_match_stats_error(ctx: ApplicationContext, error: discord.DiscordException):
    if isinstance(error, commands.MissingPermissions):
        await ctx.respond("You need administrator permissions to use this command.", ephemeral=True)
    else:
        await ctx.respond(f"An unexpected error occurred: {error}", ephemeral=True)

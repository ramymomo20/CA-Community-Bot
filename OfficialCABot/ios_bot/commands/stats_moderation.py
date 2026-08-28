from ios_bot.config import *


@bot.slash_command(
    name="exclude_match_from_stats",
    description="[ADMIN] Exclude or restore an entire match from counted hub/rating stats."
)
@commands.has_permissions(administrator=True)
async def exclude_match_from_stats(
    ctx: ApplicationContext,
    match_id: Option(int, "Numeric MATCH_STATS id", required=True),
    exclude: Option(bool, "True to exclude, false to restore", required=False, default=True),
    reason: Option(str, "Optional moderation reason", required=False, default=""),
):
    await ctx.defer(ephemeral=True)

    try:
        result = await bot.db.matches.set_match_stats_exclusion(
            match_stats_id=match_id,
            excluded=exclude,
            reason=reason,
            updated_by_discord_id=ctx.user.id,
        )
        if not result:
            await ctx.followup.send(
                f"Match `{match_id}` was not found.",
                ephemeral=True,
            )
            return

        action = "excluded from" if exclude else "restored to"
        reason_text = f"\nReason: {result['reason']}" if result.get("reason") else ""
        await ctx.followup.send(
            (
                f"Match `{match_id}` ({result.get('home_team_name', 'Home')} vs "
                f"{result.get('away_team_name', 'Away')}) has been {action} counted stats."
                f"{reason_text}\nRun `/recalculate_ratings_only` when you want persisted ratings refreshed."
            ),
            ephemeral=True,
        )
    except Exception as e:
        await ctx.followup.send(f"Failed to update match exclusion: {e}", ephemeral=True)


@exclude_match_from_stats.error
async def exclude_match_from_stats_error(ctx: ApplicationContext, error: discord.DiscordException):
    if isinstance(error, commands.MissingPermissions):
        await ctx.respond("You need administrator permissions to use this command.", ephemeral=True)
    else:
        await ctx.respond(f"An unexpected error occurred: {error}", ephemeral=True)


@bot.slash_command(
    name="exclude_player_from_match_stats",
    description="[ADMIN] Exclude or restore one player's stats inside a specific match."
)
@commands.has_permissions(administrator=True)
async def exclude_player_from_match_stats(
    ctx: ApplicationContext,
    match_id: Option(int, "Numeric MATCH_STATS id", required=True),
    steam_id: Option(str, "Steam ID stored on the player row for that match", required=True),
    exclude: Option(bool, "True to exclude, false to restore", required=False, default=True),
    reason: Option(str, "Optional moderation reason", required=False, default=""),
):
    await ctx.defer(ephemeral=True)

    try:
        result = await bot.db.matches.set_player_match_stats_exclusion(
            match_stats_id=match_id,
            steam_id=steam_id,
            excluded=exclude,
            reason=reason,
            updated_by_discord_id=ctx.user.id,
        )
        if not result:
            await ctx.followup.send(
                f"No player row with Steam ID `{steam_id}` was found for match `{match_id}`.",
                ephemeral=True,
            )
            return

        action = "excluded from" if exclude else "restored to"
        player_label = result.get("player_name") or result.get("steam_id") or steam_id
        team_label = result.get("guild_team_name") or "Unknown Team"
        reason_text = f"\nReason: {result['reason']}" if result.get("reason") else ""
        await ctx.followup.send(
            (
                f"`{player_label}` ({team_label}) in match `{match_id}` has been {action} counted stats."
                f"{reason_text}\nRun `/recalculate_ratings_only` when you want persisted ratings refreshed."
            ),
            ephemeral=True,
        )
    except Exception as e:
        await ctx.followup.send(f"Failed to update player match exclusion: {e}", ephemeral=True)


@exclude_player_from_match_stats.error
async def exclude_player_from_match_stats_error(ctx: ApplicationContext, error: discord.DiscordException):
    if isinstance(error, commands.MissingPermissions):
        await ctx.respond("You need administrator permissions to use this command.", ephemeral=True)
    else:
        await ctx.respond(f"An unexpected error occurred: {error}", ephemeral=True)


@bot.slash_command(
    name="merge_player",
    description="[ADMIN] Merge a duplicate player profile into the one to keep."
)
@commands.has_permissions(administrator=True)
async def merge_player(
    ctx: ApplicationContext,
    keep: Option(discord.Member, "The Discord identity to keep", required=True),
    merge_from: Option(discord.Member, "The duplicate Discord identity to merge in and remove", required=True),
):
    await ctx.defer(ephemeral=True)

    if keep.id == merge_from.id:
        await ctx.followup.send("Those are the same Discord user.", ephemeral=True)
        return

    ok, message = await bot.db.players.merge_players(
        keep_discord_id=keep.id,
        merge_discord_id=merge_from.id,
        teams_ops=bot.db.teams,
    )
    if ok:
        await ctx.followup.send(f"✅ {message}", ephemeral=True)
    else:
        await ctx.followup.send(f"❌ {message}", ephemeral=True)


@merge_player.error
async def merge_player_error(ctx: ApplicationContext, error: discord.DiscordException):
    if isinstance(error, commands.MissingPermissions):
        await ctx.respond("You need administrator permissions to use this command.", ephemeral=True)
    else:
        await ctx.respond(f"An unexpected error occurred: {error}", ephemeral=True)


def setup(bot):
    return

import asyncio
from ios_bot.config import *

MAIN_GUILD_ALIASES = [
    "IOSoccer Central America A",
    "IOSoccer Central America B",
    "IOSoccer Central America",
    "IOSoccer",
    "Main Guild 6S Team",
    "IOSCA MIX A",
    "IOSCA MIX B",
    "Greece",
    "IOSCA A",
    "IOSCA B",
    "IOSCA",
]


async def _run_reevaluate(ctx, max_files: int, threshold: float):
    await ctx.followup.send("🔎 Starting background re-evaluation...", ephemeral=True)
    try:
        from ios_bot.ratings.compile_stats import (
            get_servers,
            get_processed_match_ids,
            download_match_files_from_server,
            process_match_files,
        )

        servers = await get_servers()
        processed_match_ids = await get_processed_match_ids()

        all_json_files = []
        for server in servers:
            files = await asyncio.to_thread(
                download_match_files_from_server,
                server,
                None,
                processed_match_ids,
            )
            all_json_files.extend(files)

        if max_files and max_files > 0 and len(all_json_files) > max_files:
            all_json_files = all_json_files[:max_files]

        imported_count = await process_match_files(all_json_files)

        teams = await bot.db.teams.get_all_teams()
        if MAIN_GUILD_ID:
            for alias in MAIN_GUILD_ALIASES:
                teams.append({"guild_id": MAIN_GUILD_ID, "guild_name": alias})

        backfill_stats = await bot.db.matches.backfill_match_team_links(
            teams=teams,
            threshold=threshold
        )

        player_backfill = await bot.db.matches.backfill_player_match_guild_ids()

        await ctx.followup.send(
            f"✅ Re-evaluation complete. Imported: {imported_count}\n"
            f"Match links updated: {backfill_stats.get('matches_updated', 0)}\n"
            f"Player records updated: {player_backfill.get('players_updated', 0)}",
            ephemeral=True
        )
    except Exception as e:
        await ctx.followup.send(f"❌ Re-evaluation failed: {e}", ephemeral=True)


@bot.slash_command(name="reevaluate_all_games", description="[ADMIN] Re-check SFTP for missing matches and relink teams")
@commands.has_permissions(administrator=True)
async def reevaluate_all_games(
    ctx,
    max_files: Option(int, "Max files to process (0 = no limit)", required=False, default=0),
    threshold: Option(float, "Fuzzy match threshold (0-1)", required=False, default=0.8),
):
    await ctx.defer(ephemeral=True)
    asyncio.create_task(_run_reevaluate(ctx, max_files, threshold))


def setup(bot):
    # Command registered by decorator.
    return

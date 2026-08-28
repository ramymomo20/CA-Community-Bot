import asyncio
from datetime import datetime, timezone

from ios_bot.config import *


_rebuild_in_progress = False


async def _safe_notify(ctx, message: str, *, ephemeral: bool = True):
    try:
        return await ctx.followup.send(message, ephemeral=ephemeral)
    except Exception:
        pass

    try:
        channel = getattr(ctx, "channel", None)
        user = getattr(ctx, "user", None)
        mention = f"{user.mention} " if user and hasattr(user, "mention") else ""
        if channel:
            await channel.send(f"{mention}{message}")
    except Exception:
        return


async def _find_existing_match(match_id: str, filename: str | None):
    row = await bot.db.matches.get_match_by_match_id(str(match_id))
    if row:
        return row
    if not filename:
        return None
    try:
        found = await bot.db.matches.pool.fetchrow(
            """
            SELECT *
            FROM MATCH_STATS
            WHERE source_filename = $1
            ORDER BY id DESC
            LIMIT 1
            """,
            str(filename),
        )
        return dict(found) if found else None
    except Exception:
        return None


async def _run_ratings_rebuild(ctx):
    import importlib

    recalc_cmd = importlib.import_module("ios_bot.commands.recalculate_all")

    db_handle = getattr(bot, "db", None)
    if db_handle is None:
        return {"skipped": "db handle unavailable"}

    perf_result = await recalc_cmd._rebuild_match_performance(db_handle)
    await recalc_cmd._regenerate_player_ratings()
    await recalc_cmd._recalculate_team_averages()
    return perf_result


async def _run_rebuild(
    ctx,
    max_files: int,
    import_missing: bool,
    recalculate_after: bool,
):
    global _rebuild_in_progress
    started_at = datetime.now(timezone.utc)
    try:
        from ios_bot.ratings.compile_stats import download_match_files_from_server, get_servers
        from ios_bot.utils.match_importer import MatchImporter

        await _safe_notify(ctx, "Starting in-place match data rebuild from SFTP JSONs...", ephemeral=True)

        servers = await get_servers()
        if not servers:
            await _safe_notify(ctx, "No SFTP servers are configured or reachable.", ephemeral=True)
            return

        all_json_files = []
        for server in servers:
            files = await asyncio.to_thread(
                download_match_files_from_server,
                server,
                None,
                set(),
            )
            all_json_files.extend(files)

        if max_files and max_files > 0:
            all_json_files = all_json_files[:max_files]

        importer = MatchImporter(bot.db)
        scanned = len(all_json_files)
        rebuilt = 0
        imported_missing = 0
        skipped_missing = 0
        failed = 0

        for match_data, _file_dt, match_id, filename in all_json_files:
            existing = await _find_existing_match(match_id, filename)
            try:
                if existing:
                    result_id = await importer.import_match_from_json(
                        match_data,
                        match_id_str=str(existing.get("match_id") or match_id),
                        source_filename=filename,
                        existing_match_stats_id=int(existing["id"]),
                        announce_completion=False,
                    )
                    if result_id:
                        rebuilt += 1
                    else:
                        failed += 1
                elif import_missing:
                    result_id = await importer.import_match_from_json(
                        match_data,
                        match_id_str=str(match_id),
                        source_filename=filename,
                        announce_completion=False,
                    )
                    if result_id:
                        imported_missing += 1
                    else:
                        failed += 1
                else:
                    skipped_missing += 1
            except Exception:
                failed += 1

        ratings_result = None
        if recalculate_after:
            await _safe_notify(ctx, "Match data rebuild finished. Recalculating match/player ratings...", ephemeral=True)
            ratings_result = await _run_ratings_rebuild(ctx)

        elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
        ratings_text = ""
        if isinstance(ratings_result, dict):
            ratings_text = (
                "\n"
                f"Match performance recalculated: {ratings_result.get('matches', 0)} matches / "
                f"{ratings_result.get('rows', 0)} player rows"
            )

        await _safe_notify(
            ctx,
            (
                "In-place rebuild complete.\n"
                f"JSON files scanned: {scanned}\n"
                f"Existing matches rebuilt: {rebuilt}\n"
                f"Missing matches imported: {imported_missing}\n"
                f"Missing matches skipped: {skipped_missing}\n"
                f"Failed: {failed}\n"
                f"Elapsed: {elapsed:.1f}s"
                f"{ratings_text}"
            ),
            ephemeral=False,
        )
    except Exception as e:
        await _safe_notify(ctx, f"In-place rebuild failed: {e}", ephemeral=False)
    finally:
        _rebuild_in_progress = False


@bot.slash_command(
    name="rebuild_match_data_from_json",
    description="[ADMIN] Reparse SFTP JSONs and update match/player/event data in place",
)
@commands.has_permissions(administrator=True)
async def rebuild_match_data_from_json(
    ctx,
    max_files: Option(int, "Max files to process (0 = no limit)", required=False, default=0),
    import_missing: Option(bool, "Import JSONs that do not already exist in MATCH_STATS", required=False, default=False),
    recalculate_after: Option(bool, "Recalculate match ratings and player ratings after rebuild", required=False, default=True),
):
    global _rebuild_in_progress
    if _rebuild_in_progress:
        await ctx.respond("A match data rebuild is already running.", ephemeral=True)
        return

    _rebuild_in_progress = True
    await ctx.defer(ephemeral=True)
    asyncio.create_task(
        _run_rebuild(
            ctx,
            max_files,
            import_missing,
            recalculate_after,
        )
    )


def setup(bot):
    return

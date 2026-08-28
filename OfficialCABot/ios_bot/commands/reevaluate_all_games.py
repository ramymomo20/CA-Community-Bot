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


async def _safe_notify(ctx, message: str, ephemeral: bool = True):
    """Send status updates even if the interaction webhook token expired."""
    try:
        return await ctx.followup.send(message, ephemeral=ephemeral)
    except Exception as send_err:
        err_code = getattr(send_err, "code", None)
        # 50027 Invalid Webhook Token / 10015 Unknown Webhook / 10062 Unknown Interaction
        if err_code not in (50027, 10015, 10062):
            # Try channel fallback for other transient cases too.
            pass

    try:
        channel = getattr(ctx, "channel", None)
        user = getattr(ctx, "user", None)
        mention = f"{user.mention} " if user and hasattr(user, "mention") else ""
        if channel:
            await channel.send(f"{mention}{message}")
    except Exception:
        # Last-resort: swallow so background task never crashes on notifier errors.
        return


async def _run_reevaluate(
    ctx,
    max_files: int,
    threshold: float,
    full_scan: bool,
    backfill_events: bool,
    overwrite_events: bool,
    backfill_match_events: bool,
):
    await _safe_notify(ctx, "Starting background re-evaluation...", ephemeral=True)
    try:
        from ios_bot.ratings.compile_stats import (
            download_match_files_from_server,
            get_processed_match_ids,
            get_servers,
            process_match_files,
        )
        from ios_bot.utils.json_parser import build_match_event_locations, build_player_event_timestamps

        servers = await get_servers()
        processed_match_ids = await get_processed_match_ids()

        all_json_files = []
        for server in servers:
            processed_filter = set() if full_scan else processed_match_ids
            files = await asyncio.to_thread(
                download_match_files_from_server,
                server,
                None,
                processed_filter,
            )
            all_json_files.extend(files)

        if max_files and max_files > 0 and len(all_json_files) > max_files:
            all_json_files = all_json_files[:max_files]

        import_candidates = all_json_files
        if full_scan:
            # In full scan mode, import only matches missing from DB.
            import_candidates = [f for f in all_json_files if f[2] not in processed_match_ids]

        imported_count = 0
        if import_candidates:
            imported_count = await process_match_files(import_candidates)

        teams = await bot.db.teams.get_all_teams()
        if MAIN_GUILD_ID:
            for alias in MAIN_GUILD_ALIASES:
                teams.append({"guild_id": MAIN_GUILD_ID, "guild_name": alias})

        backfill_stats = await bot.db.matches.backfill_match_team_links(
            teams=teams,
            threshold=threshold,
        )
        player_backfill = await bot.db.matches.backfill_player_match_guild_ids()

        events_matches_scanned = 0
        events_players_considered = 0
        events_rows_updated = 0
        match_events_matches_scanned = 0
        match_events_rows_stored = 0
        if backfill_events:
            for match_data, _file_dt, match_id, filename in all_json_files:
                events_map = build_player_event_timestamps(
                    match_data.get("matchData", {}).get("matchEvents", [])
                )
                if not events_map:
                    continue
                events_matches_scanned += 1
                result = await bot.db.matches.upsert_player_event_timestamps_for_match(
                    match_id_key=match_id,
                    player_event_timestamps=events_map,
                    source_filename=filename,
                    overwrite=overwrite_events,
                )
                events_players_considered += int(result.get("players_considered", 0))
                events_rows_updated += int(result.get("rows_updated", 0))

        if backfill_match_events:
            # Resolve the primary (match_id-based) lookup for every file in
            # one query instead of one fetchrow per file -- this loop can
            # cover the entire match history under a full scan. The
            # source_filename fallback below still runs per-file, but only
            # for whatever small minority doesn't resolve by match_id.
            candidate_match_ids = [str(match_id) for _, _, match_id, _ in all_json_files]
            lookup_by_match_id = {}
            if candidate_match_ids:
                rows = await bot.db.matches.pool.fetch(
                    "SELECT id, match_id FROM MATCH_STATS WHERE match_id = ANY($1::text[])",
                    candidate_match_ids,
                )
                lookup_by_match_id = {r["match_id"]: r for r in rows}

            for match_data, _file_dt, match_id, filename in all_json_files:
                event_rows = build_match_event_locations(match_data)
                if not event_rows:
                    continue

                lookup_row = lookup_by_match_id.get(str(match_id))
                if not lookup_row and filename:
                    lookup_row = await bot.db.matches.pool.fetchrow(
                        """
                        SELECT id, match_id
                        FROM MATCH_STATS
                        WHERE source_filename = $1
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        str(filename),
                    )
                if not lookup_row:
                    continue

                result = await bot.db.matches.replace_match_events(
                    match_stats_id=int(lookup_row["id"]),
                    match_id_str=str(lookup_row["match_id"]),
                    events=event_rows,
                )
                match_events_matches_scanned += 1
                match_events_rows_stored += int(result.get("inserted", 0))

        mode_text = "FULL_SCAN" if full_scan else "INCREMENTAL"
        await _safe_notify(
            ctx,
            f"Re-evaluation complete ({mode_text}).\n"
            f"Imported: {imported_count}\n"
            f"Match links updated: {backfill_stats.get('matches_updated', 0)}\n"
            f"Player guild links updated: {player_backfill.get('players_updated', 0)}\n"
            f"Event timestamp matches scanned: {events_matches_scanned}\n"
            f"Event timestamp players considered: {events_players_considered}\n"
            f"Event timestamp rows updated: {events_rows_updated}\n"
            f"Match event matches scanned: {match_events_matches_scanned}\n"
            f"Match event rows stored: {match_events_rows_stored}",
            ephemeral=True,
        )
    except Exception as e:
        await _safe_notify(ctx, f"Re-evaluation failed: {e}", ephemeral=True)


@bot.slash_command(
    name="reevaluate_all_games",
    description="[ADMIN] Re-check SFTP, relink teams, and backfill event timestamps",
)
@commands.has_permissions(administrator=True)
async def reevaluate_all_games(
    ctx,
    max_files: Option(int, "Max files to process (0 = no limit)", required=False, default=0),
    threshold: Option(float, "Fuzzy match threshold (0-1)", required=False, default=0.8),
    full_scan: Option(bool, "Scan all SFTP files (including already imported)", required=False, default=False),
    backfill_events: Option(bool, "Backfill player event timestamps from matchEvents", required=False, default=True),
    overwrite_events: Option(bool, "Overwrite existing event_timestamps instead of merge", required=False, default=False),
    backfill_match_events: Option(bool, "Backfill MATCH_EVENTS location rows from matchEvents", required=False, default=True),
):
    await ctx.defer(ephemeral=True)
    asyncio.create_task(
        _run_reevaluate(
            ctx,
            max_files,
            threshold,
            full_scan,
            backfill_events,
            overwrite_events,
            backfill_match_events,
        )
    )


def setup(bot):
    # Command registered by decorator.
    return

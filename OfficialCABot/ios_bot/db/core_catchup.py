"""
Incremental public -> core catch-up for matches/player-entries/events.

scripts/migrate_matches.py does the same value translation but scans every
row in public.match_stats/player_match_data/match_events on every run --
fine for a rare, manual full catch-up, but far too much egress to run on a
schedule (reading the whole, ever-growing table every tick would undo the
work already done to keep this project's Supabase egress low).

This version finds the highest public.match_stats.id already reflected in
core.matches and only reads rows newer than that, then scopes the
player-entry/event reads and even the post-insert core.matches lookup to
just those new matches' own ids -- a normal catch-up touches a handful of
rows, not the whole table, regardless of how large the tables get.

Kept in sync with scripts/migrate_matches.py's value-translation rules by
hand -- if one changes (a new column, a different legacy->canonical
mapping), the other needs the same edit.
"""
from __future__ import annotations

PARTICIPATION_MAP = {"started": "started", "substitute": "substitute", "on_bench": "bench"}
VALID_POSITIONS = {"GK", "LB", "CB", "RB", "CM", "LM", "RM", "LW", "RW", "CF"}


def _normalize_position(pos) -> str | None:
    p = (pos or "").strip().upper()
    return p if p in VALID_POSITIONS else None


async def sync_public_matches_to_core(pool) -> dict[str, int]:
    """Mirror any public.match_stats rows newer than what's already in
    core.matches, plus their player entries and events. Safe to call
    frequently -- a no-op (single cheap watermark query) when nothing new
    has happened since the last call."""
    watermark = await pool.fetchval(
        """
        SELECT COALESCE(MAX(m.id), 0)
        FROM public.match_stats m
        WHERE EXISTS (SELECT 1 FROM core.matches c WHERE c.external_match_id = m.match_id)
        """
    )

    matches = await pool.fetch(
        """
        SELECT id, match_id, datetime, home_guild_id, away_guild_id, home_score, away_score,
               game_type, extratime, penalties, comeback_flag, source_filename,
               created_at, updated_at, home_team_name, away_team_name
        FROM public.match_stats
        WHERE id > $1
        ORDER BY id
        """,
        watermark,
    )
    if not matches:
        return {"matches": 0, "player_entries": 0, "events": 0}

    guild_to_team = {
        r["discord_guild_id"]: r["team_id"]
        for r in await pool.fetch("SELECT discord_guild_id, team_id FROM core.teams")
    }
    steam_to_account = {
        r["steam_id_legacy"]: r["account_id"]
        for r in await pool.fetch("SELECT steam_id_legacy, account_id FROM core.account_steam_identities")
    }

    match_text_ids = [m["match_id"] for m in matches]
    match_int_ids = [m["id"] for m in matches]

    player_rows = await pool.fetch(
        "SELECT * FROM public.player_match_data WHERE match_id = ANY($1::text[])",
        match_text_ids,
    )
    event_rows = await pool.fetch(
        "SELECT * FROM public.match_events WHERE match_stats_id = ANY($1::int[])",
        match_int_ids,
    )

    match_columns = [
        "external_match_id", "played_at", "game_format", "home_team_id", "away_team_id",
        "home_team_name_snapshot", "away_team_name_snapshot", "home_score", "away_score",
        "went_extra_time", "went_penalties", "comeback_flag", "source_filename",
        "source_updated_at", "imported_at", "updated_at",
    ]
    match_rows = []
    for m in matches:
        home_team_id = guild_to_team.get(m["home_guild_id"])
        away_team_id = guild_to_team.get(m["away_guild_id"])
        match_rows.append((
            m["match_id"], m["datetime"], m["game_type"], home_team_id, away_team_id,
            m["home_team_name"] or "Unknown", m["away_team_name"] or "Unknown",
            m["home_score"] or 0, m["away_score"] or 0,
            bool(m["extratime"]), bool(m["penalties"]), bool(m["comeback_flag"]),
            m["source_filename"], m["updated_at"], m["created_at"], m["updated_at"],
        ))

    entry_rows: list[tuple] = []
    event_rows_batch: list[tuple] = []

    async with pool.acquire() as conn:
        async with conn.transaction():
            col_sql = ", ".join(match_columns)
            placeholders = ", ".join(f"${i}" for i in range(1, len(match_columns) + 1))
            await conn.executemany(
                f"""
                INSERT INTO core.matches ({col_sql}, competition_kind, match_status)
                VALUES ({placeholders}, 'matchmaking', 'final')
                ON CONFLICT (external_match_id) DO NOTHING
                """,
                match_rows,
            )

            id_rows = await conn.fetch(
                """
                SELECT match_id, external_match_id, home_team_id, away_team_id
                FROM core.matches
                WHERE external_match_id = ANY($1::text[])
                """,
                match_text_ids,
            )
            match_id_map_by_string = {r["external_match_id"]: r["match_id"] for r in id_rows}
            matches_by_new_id = {r["match_id"]: r for r in id_rows}
            match_id_map_by_intpk = {
                m["id"]: match_id_map_by_string[m["match_id"]]
                for m in matches
                if m["match_id"] in match_id_map_by_string
            }

            entry_columns = [
                "source_player_match_key", "match_id", "account_id", "team_id", "team_side",
                "steam_id_64_snapshot", "steam_id_legacy_snapshot", "player_name_snapshot",
                "position_code", "participation_status", "started_on_field",
                "goals", "assists", "second_assists", "shots", "shots_on_target",
                "passes_completed", "passes_attempted", "chances_created", "key_passes",
                "interceptions", "tackles", "tackles_completed", "fouls_committed", "fouls_suffered",
                "yellow_cards", "second_yellow_reds", "red_cards", "saves", "saves_caught",
                "goals_conceded", "offsides", "own_goals", "corners", "throw_ins", "free_kicks",
                "goal_kicks", "penalties_taken", "possession_pct", "seconds_played",
                "seconds_gk", "seconds_def", "seconds_mid", "seconds_atk", "distance_meters",
                "pass_accuracy_pct", "event_timestamps", "clutch_actions", "substitution_impact",
                "match_rating", "is_match_mvp", "mvp_score", "mvp_key_stats",
                "created_at", "updated_at",
            ]
            for p in player_rows:
                match_id = match_id_map_by_string.get(p["match_id"])
                if match_id is None:
                    continue

                team_id = guild_to_team.get(p["guild_id"])
                account_id = steam_to_account.get(p["steam_id"])
                participation = PARTICIPATION_MAP.get(p["status"], "unknown")

                match_row = matches_by_new_id.get(match_id)
                team_side = None
                if team_id is not None and match_row is not None:
                    if team_id == match_row["home_team_id"]:
                        team_side = "home"
                    elif team_id == match_row["away_team_id"]:
                        team_side = "away"

                entry_rows.append((
                    f"legacy:player_match_data:{p['id']}", match_id, account_id, team_id, team_side,
                    None, p["steam_id"], p["player_name"],
                    _normalize_position(p["position"]), participation, participation == "started",
                    p["goals"] or 0, p["assists"] or 0, p["second_assists"] or 0, p["shots"] or 0, p["shots_on_goal"] or 0,
                    p["passes_completed"] or 0, p["passes_attempted"] or 0, p["chances_created"] or 0, p["key_passes"] or 0,
                    p["interceptions"] or 0, p["tackles"] or 0, p["sliding_tackles_completed"] or 0, p["fouls"] or 0, p["fouls_suffered"] or 0,
                    p["yellow_cards"] or 0, 0, p["red_cards"] or 0, p["keeper_saves"] or 0, p["keeper_saves_caught"] or 0,
                    p["goals_conceded"] or 0, p["offsides"] or 0, p["own_goals"] or 0, p["corners"] or 0, p["throw_ins"] or 0, p["free_kicks"] or 0,
                    p["goal_kicks"] or 0, p["penalties"] or 0, float(p["possession"] or 0), p["time_played"] or 0,
                    p["time_gk"] or 0, p["time_def"] or 0, p["time_mid"] or 0, p["time_att"] or 0, float(p["distance_covered"] or 0),
                    float(p["pass_accuracy"] or 0), p["event_timestamps"] or "{}", p["clutch_actions"] or "[]", p["sub_impact"] or "{}",
                    p["match_rating"], bool(p["is_match_mvp"]), p["mvp_score"], p["mvp_key_stats"] or "[]",
                    p["created_at"], p["updated_at"],
                ))

            if entry_rows:
                col_sql = ", ".join(entry_columns)
                placeholders = ", ".join(f"${i}" for i in range(1, len(entry_columns) + 1))
                await conn.executemany(
                    f"""
                    INSERT INTO core.match_player_entries ({col_sql})
                    VALUES ({placeholders})
                    ON CONFLICT (source_player_match_key) DO NOTHING
                    """,
                    entry_rows,
                )

            event_columns = [
                "source_event_key", "match_id", "event_index", "event_type", "raw_event_type",
                "team_side", "period_label", "raw_second", "match_second", "minute_mark", "clock_label",
                "actor_account_id", "secondary_account_id", "tertiary_account_id",
                "actor_steam_id_legacy_snapshot", "secondary_steam_id_legacy_snapshot", "tertiary_steam_id_legacy_snapshot",
                "body_part", "x_raw", "y_raw", "x_norm", "y_norm", "payload", "created_at",
            ]
            for e in event_rows:
                match_id = match_id_map_by_intpk.get(e["match_stats_id"])
                if match_id is None:
                    continue
                p1 = steam_to_account.get(e["player1_steam_id"])
                p2 = steam_to_account.get(e["player2_steam_id"])
                p3 = steam_to_account.get(e["player3_steam_id"])
                event_rows_batch.append((
                    f"legacy:match_events:{e['id']}", match_id, e["event_index"], e["event_type"], e["raw_event"],
                    e["team"], e["period"], e["raw_second"], e["match_second"], e["minute"], e["clock"],
                    p1, p2, p3,
                    e["player1_steam_id"], e["player2_steam_id"], e["player3_steam_id"],
                    e["body_part"], e["x"], e["y"], e["norm_x"], e["norm_y"], e["raw_event_payload"] or "{}", e["created_at"],
                ))

            if event_rows_batch:
                col_sql = ", ".join(event_columns)
                placeholders = ", ".join(f"${i}" for i in range(1, len(event_columns) + 1))
                await conn.executemany(
                    f"""
                    INSERT INTO core.match_events ({col_sql})
                    VALUES ({placeholders})
                    ON CONFLICT (source_event_key) DO NOTHING
                    """,
                    event_rows_batch,
                )

    return {
        "matches": len(match_rows),
        "player_entries": len(entry_rows),
        "events": len(event_rows_batch),
    }

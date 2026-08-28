from __future__ import annotations

import asyncio
import bisect
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd
from dotenv import load_dotenv

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from ios_bot.db import Database
from ios_bot.db.connection import get_connection_string


env_path = project_root / ".env"
load_dotenv(env_path)

db = None

# The season/"main" rating is a recency-weighted, confidence-shrunk aggregate
# of the player's own per-match ratings (see ios_bot/utils/match_performance.py
# ::rate_player) in whichever role they play most -- NOT an independently
# recomputed percentile-of-raw-stats score. This keeps the headline number
# grounded in what actually happened on the pitch instead of an unrelated
# formula that can (and did) disagree with it.
BASELINE_RATING = float(os.getenv("BASELINE_RATING", "6.20"))  # matches rate_player's neutral base_score
MIN_OFFICIAL_APPEARANCES = max(1, int(os.getenv("MIN_OFFICIAL_RATING_APPEARANCES", "50")))
ROLE_RATING_HALF_LIFE_DAYS = max(1.0, float(os.getenv("ROLE_RATING_HALF_LIFE_DAYS", "365")))
ROLE_RATING_PRIOR_WEIGHT = max(0.1, float(os.getenv("ROLE_RATING_PRIOR_WEIGHT", "12")))
ROLE_RATING_STRETCH_FACTOR = max(1.0, float(os.getenv("ROLE_RATING_STRETCH_FACTOR", "1.3")))
MIN_ESTABLISHED_ROLE_APPEARANCES = max(1, int(os.getenv("ROLE_RATING_ESTABLISHED_APPEARANCES", "15")))

# Smooth (non-cliff) inactivity discount applied only to the *displayed*
# rating -- the underlying main_role_rating reflects true recent skill and is
# left untouched. Grows continuously with days since last match instead of
# jumping at fixed day thresholds (the old 7/14/30/60/90-day step function
# caused sudden multi-tenths swings overnight for no additional inactivity).
INACTIVITY_MAX_PENALTY = max(0.0, float(os.getenv("INACTIVITY_MAX_PENALTY", "1.15")))
# Inactivity is measured in the player's own TEAM's missed matches, not
# calendar days -- a league-wide lull (off-season, holidays) shouldn't dock
# anyone, since nobody actually missed a chance to play. Only counts against
# a player once their own team takes the field without them.
INACTIVITY_GRACE_MATCHES = max(0, int(os.getenv("INACTIVITY_GRACE_MATCHES", "2")))
INACTIVITY_DECAY_TAU_MATCHES = max(1.0, float(os.getenv("INACTIVITY_DECAY_TAU_MATCHES", "10")))

ROLE_ORDER = ("ATK", "MID", "DEF", "GK")
ROLE_PRIORITY = {role: idx for idx, role in enumerate(ROLE_ORDER)}
ROLE_TO_TIME_COL = {
    "ATK": "timeATT",
    "MID": "timeMID",
    "DEF": "timeDEF",
    "GK": "timeGK",
}
_LAST_GENERATE_ERROR: str | None = None


def _set_last_generate_error(message: str | None) -> None:
    global _LAST_GENERATE_ERROR
    _LAST_GENERATE_ERROR = str(message) if message else None


def get_last_generate_error() -> str | None:
    return _LAST_GENERATE_ERROR


async def init_db():
    global db
    if db is None:
        db = Database(get_connection_string())
        await db.initialize()
    return db


def _clamp(value: Any, low: float = 0.0, high: float = 1.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, numeric))


def _parse_json(raw: Any, fallback: Any):
    if raw is None:
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return fallback
        try:
            return json.loads(text)
        except Exception:
            return fallback
    return fallback


def map_position(cat: str) -> str:
    p = (cat or "").upper().strip()
    if p in ("LW", "CF", "RW"):
        return "ATK"
    if p in ("CM", "LM", "RM"):
        return "MID"
    if p in ("LB", "CB", "RB"):
        return "DEF"
    if p == "GK":
        return "GK"
    return "UNK"


def infer_general_position(row: pd.Series) -> str:
    mapped = map_position(str(row.get("Position") or ""))
    if mapped != "UNK":
        return mapped

    time_map = {
        "GK": pd.to_numeric(row.get("timeGK"), errors="coerce"),
        "DEF": pd.to_numeric(row.get("timeDEF"), errors="coerce"),
        "MID": pd.to_numeric(row.get("timeMID"), errors="coerce"),
        "ATK": pd.to_numeric(row.get("timeATT"), errors="coerce"),
    }
    best_role = max(time_map, key=lambda key: float(time_map.get(key) or 0.0))
    if float(time_map.get(best_role) or 0.0) > 0:
        return best_role
    return "MID"


def _rows_affected(status: str) -> int:
    try:
        return int(str(status).split()[-1])
    except Exception:
        return 0


def _db_acquire_ctx(db_handle):
    pool_wrapper = getattr(db_handle, "pool", None)
    if pool_wrapper is None:
        raise RuntimeError("Database handle has no pool")

    acquire_fn = getattr(pool_wrapper, "acquire", None)
    if callable(acquire_fn):
        return acquire_fn()

    raw_pool = getattr(pool_wrapper, "pool", None)
    if raw_pool is not None and hasattr(raw_pool, "acquire"):
        return raw_pool.acquire()

    raise RuntimeError("Database pool does not expose acquire()")


def _coerce_timestamp(value: Any) -> datetime | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.to_pydatetime()
    if isinstance(value, datetime):
        return value
    return None


def _role_seconds_for_row(row: pd.Series) -> float:
    role = str(row.get("generalPosition") or "MID").upper()
    role_seconds = pd.to_numeric(row.get(ROLE_TO_TIME_COL.get(role, "")), errors="coerce")
    role_seconds = float(role_seconds or 0.0)
    if role_seconds > 0:
        return role_seconds
    total_seconds = pd.to_numeric(row.get("timePlayed"), errors="coerce")
    return float(total_seconds or 0.0)


def _choose_main_role(role_payload: Dict[str, Dict[str, Any]]) -> str | None:
    candidates = [role for role in ROLE_ORDER if int(role_payload.get(role, {}).get("appearances") or 0) > 0]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda role: (
            int(role_payload[role].get("appearances") or 0),
            float(role_payload[role].get("minutes") or 0.0),
            -ROLE_PRIORITY[role],
        ),
    )


def _missed_team_matches(
    last_match_at: datetime | None,
    team_guild_id: str | None,
    team_calendars: dict[str, list[datetime]],
) -> int:
    """Count matches the player's own team played after their last appearance.

    Scoped to their team specifically (not the whole community) -- a
    league-wide lull doesn't count against anyone, since nobody's team
    actually took the field without them.
    """
    if last_match_at is None or not team_guild_id:
        return 0
    calendar = team_calendars.get(str(team_guild_id))
    if not calendar:
        return 0
    idx = bisect.bisect_right(calendar, last_match_at)
    return max(0, len(calendar) - idx)


def _inactivity_penalty(missed_matches: int) -> float:
    """Smooth (non-cliff) discount for the *displayed* rating only.

    Grows continuously with missed team matches instead of jumping at fixed
    thresholds -- one more missed game should never cause a sudden jump, and
    a couple of missed games in a normal week costs nothing at all.
    """
    if missed_matches <= INACTIVITY_GRACE_MATCHES:
        return 0.0
    ramped = missed_matches - INACTIVITY_GRACE_MATCHES
    return INACTIVITY_MAX_PENALTY * (1.0 - math.exp(-ramped / INACTIVITY_DECAY_TAU_MATCHES))


def _build_identity_records(df: pd.DataFrame) -> dict[str, Dict[str, Any]]:
    """Bucket each player's per-match ratings (see rate_player) by role.

    Unlike the old percentile-of-raw-stats system, this does not recompute a
    rating from box-score counts -- it just collects the (rating, days_ago)
    pairs that _compute_role_rating will later aggregate. The rating itself
    already lives on each match row via match_rating.
    """
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    player_records: dict[str, Dict[str, Any]] = {}

    for _, row in df.iterrows():
        match_rating = row.get("match_rating")
        if pd.isna(match_rating):
            continue
        role = str(row.get("generalPosition") or "").upper()
        if role not in ROLE_ORDER:
            continue

        steamid = str(row["Steam ID"])
        record = player_records.setdefault(
            steamid,
            {
                "steamid": steamid,
                "player": steamid,
                "total_appearances": 0,
                "total_minutes": 0,
                "last_match_at": None,
                "last_team_guild_id": None,
                "roles": {r: {"appearances": 0, "minutes": 0, "ratings": []} for r in ROLE_ORDER},
            },
        )
        record["player"] = str(row.get("Name") or steamid).strip() or steamid

        match_dt = _coerce_timestamp(row.get("datetime"))
        if match_dt is not None:
            if record["last_match_at"] is None or match_dt > record["last_match_at"]:
                record["last_match_at"] = match_dt
                guild_id = row.get("guild_id")
                record["last_team_guild_id"] = str(guild_id) if pd.notna(guild_id) else None
            days_ago = max(0.0, (now_utc - match_dt).total_seconds() / 86400.0)
        else:
            # Unknown match date: treat as fully aged out rather than phantom-recent.
            days_ago = 9999.0

        role_minutes = _role_seconds_for_row(row) / 60.0
        record["total_appearances"] += 1
        record["total_minutes"] += int(round(role_minutes))
        record["roles"][role]["appearances"] += 1
        record["roles"][role]["minutes"] += int(round(role_minutes))
        record["roles"][role]["ratings"].append((float(match_rating), days_ago))

    return player_records


def _population_pivot(player_records: dict[str, Dict[str, Any]]) -> float:
    """Center-point for the stretch transform: the community's own average
    recency-weighted rating among established players (>= MIN_ESTABLISHED_ROLE_APPEARANCES
    in a role), not the theoretical per-match neutral baseline. Stretching
    around the fixed baseline instead of this pivot would push nearly every
    regular player's rating upward uniformly, since most active players
    average meaningfully above a "did nothing special" baseline game.
    """
    means: list[float] = []
    for record in player_records.values():
        for role in ROLE_ORDER:
            ratings = record["roles"][role]["ratings"]
            if len(ratings) < MIN_ESTABLISHED_ROLE_APPEARANCES:
                continue
            w_sum = sum(0.5 ** (days_ago / ROLE_RATING_HALF_LIFE_DAYS) for _, days_ago in ratings)
            if w_sum <= 0:
                continue
            wr_sum = sum(rating * (0.5 ** (days_ago / ROLE_RATING_HALF_LIFE_DAYS)) for rating, days_ago in ratings)
            means.append(wr_sum / w_sum)
    return float(sum(means) / len(means)) if means else BASELINE_RATING


def _compute_role_rating(ratings: list[tuple[float, float]], pivot: float) -> float | None:
    """Recency-weighted, confidence-shrunk, stretch-adjusted average of a
    player's own match ratings in one role.

    - Recency weighting (365-day half-life) lets current form matter more
      than a year-old game without erasing season-long history.
    - Confidence shrinkage toward BASELINE_RATING (via ROLE_RATING_PRIOR_WEIGHT
      pseudo-games) handles small samples -- this also smoothly subsumes what
      used to be a separate, cliff-edged inactivity penalty on the raw
      rating: as a player goes quiet, their weighted evidence decays
      continuously and shrinkage takes back over, with no day-threshold jump.
    - The stretch factor restores visible separation between skill tiers
      that match_rating's own capping/compression otherwise compresses.
    """
    if not ratings:
        return None

    w_sum = sum(0.5 ** (days_ago / ROLE_RATING_HALF_LIFE_DAYS) for _, days_ago in ratings)
    if w_sum <= 0:
        weighted_mean = pivot
    else:
        wr_sum = sum(rating * (0.5 ** (days_ago / ROLE_RATING_HALF_LIFE_DAYS)) for rating, days_ago in ratings)
        weighted_mean = wr_sum / w_sum

    stretched_mean = pivot + (weighted_mean - pivot) * ROLE_RATING_STRETCH_FACTOR
    result = (w_sum * stretched_mean + ROLE_RATING_PRIOR_WEIGHT * BASELINE_RATING) / (w_sum + ROLE_RATING_PRIOR_WEIGHT)
    return round(max(3.0, min(10.0, result)), 2)


def _prepare_output_rows(
    player_records: dict[str, Dict[str, Any]],
    team_calendars: dict[str, list[datetime]] | None = None,
    preferred_roles: dict[str, str] | None = None,
) -> list[Dict[str, Any]]:
    pivot = _population_pivot(player_records)
    team_calendars = team_calendars or {}
    preferred_roles = preferred_roles or {}
    output_rows: list[Dict[str, Any]] = []

    for record in player_records.values():
        role_ratings: dict[str, float | None] = {}
        for role in ROLE_ORDER:
            role_ratings[role] = _compute_role_rating(record["roles"][role]["ratings"], pivot)

        main_role = _choose_main_role(record.get("roles", {}))
        # A player's own approved position preference (see /set_position) wins
        # over most-active, but only once they've actually got enough games
        # in that role to back it up -- otherwise a 3-game hot streak could
        # let someone declare their way into a flattering role.
        preferred = preferred_roles.get(str(record["steamid"]))
        if preferred and preferred in ROLE_ORDER:
            preferred_appearances = int(record.get("roles", {}).get(preferred, {}).get("appearances") or 0)
            if preferred_appearances >= MIN_ESTABLISHED_ROLE_APPEARANCES:
                main_role = preferred

        main_role_rating = role_ratings.get(main_role) if main_role else None
        display_main_role_rating = None
        if (
            int(record.get("total_appearances") or 0) >= MIN_OFFICIAL_APPEARANCES
            and main_role_rating is not None
        ):
            missed_matches = _missed_team_matches(
                record.get("last_match_at"),
                record.get("last_team_guild_id"),
                team_calendars,
            )
            display_main_role_rating = round(
                max(0.0, float(main_role_rating) - _inactivity_penalty(missed_matches)),
                2,
            )

        output_rows.append(
            {
                "steamid": record["steamid"],
                "player": record["player"],
                "main_role": main_role,
                "total_appearances": int(record.get("total_appearances") or 0),
                "total_minutes": int(record.get("total_minutes") or 0),
                "atk_rating": role_ratings.get("ATK"),
                "mid_rating": role_ratings.get("MID"),
                "def_rating": role_ratings.get("DEF"),
                "gk_rating": role_ratings.get("GK"),
                "atk_appearances": int(record["roles"]["ATK"].get("appearances") or 0),
                "mid_appearances": int(record["roles"]["MID"].get("appearances") or 0),
                "def_appearances": int(record["roles"]["DEF"].get("appearances") or 0),
                "gk_appearances": int(record["roles"]["GK"].get("appearances") or 0),
                "atk_minutes": int(record["roles"]["ATK"].get("minutes") or 0),
                "mid_minutes": int(record["roles"]["MID"].get("minutes") or 0),
                "def_minutes": int(record["roles"]["DEF"].get("minutes") or 0),
                "gk_minutes": int(record["roles"]["GK"].get("minutes") or 0),
                "main_role_rating": main_role_rating,
                "display_main_role_rating": display_main_role_rating,
                "rating": display_main_role_rating,
                "last_match_at": record.get("last_match_at"),
                "is_official_rating": bool(
                    int(record.get("total_appearances") or 0) >= MIN_OFFICIAL_APPEARANCES
                    and display_main_role_rating is not None
                ),
            }
        )

    output_rows.sort(
        key=lambda item: (
            item.get("display_main_role_rating") is None,
            -(float(item.get("display_main_role_rating") or 0.0)),
            str(item.get("player") or "").lower(),
        )
    )
    return output_rows


async def _write_ratings_to_db(final_output_rows: list[Dict[str, Any]]) -> None:
    async with _db_acquire_ctx(db) as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS PLAYER_RATING_HISTORY (
                id BIGSERIAL PRIMARY KEY,
                steam_id VARCHAR(255) NOT NULL,
                player_name VARCHAR(255),
                rating DECIMAL(4,2),
                atk_rating DECIMAL(4,2),
                mid_rating DECIMAL(4,2),
                def_rating DECIMAL(4,2),
                gk_rating DECIMAL(4,2),
                main_role VARCHAR(16),
                main_role_rating DECIMAL(4,2),
                display_main_role_rating DECIMAL(4,2),
                total_appearances INTEGER,
                total_minutes INTEGER,
                last_match_at TIMESTAMP,
                formula_version VARCHAR(64) NOT NULL DEFAULT 'role_based_v1',
                source VARCHAR(64) NOT NULL DEFAULT 'ratings_rebuild',
                rating_run_at TIMESTAMP NOT NULL DEFAULT NOW(),
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_player_rating_history_player_time
            ON PLAYER_RATING_HISTORY(steam_id, rating_run_at DESC)
            """
        )

        col_rows = await conn.fetch(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'iosca_players'
            """
        )
        col_meta = {str(r["column_name"]): dict(r) for r in col_rows}
        has_linked_jsonb = (
            "linked_steam_ids" in col_meta
            and str(col_meta["linked_steam_ids"].get("data_type")) == "jsonb"
        )
        name_col = "discord_name" if "discord_name" in col_meta else ("username" if "username" in col_meta else None)
        has_discord_col = "discord_id" in col_meta
        discord_is_text = has_discord_col and str(col_meta["discord_id"].get("data_type")) in ("character varying", "text")

        payload_order = [
            "rating",
            "total_appearances",
            "total_minutes",
            "atk_rating",
            "mid_rating",
            "def_rating",
            "gk_rating",
            "atk_appearances",
            "mid_appearances",
            "def_appearances",
            "gk_appearances",
            "atk_minutes",
            "mid_minutes",
            "def_minutes",
            "gk_minutes",
            "main_role",
            "main_role_rating",
            "display_main_role_rating",
            "last_match_at",
        ]
        db_payload_columns = [column for column in payload_order if column in col_meta]

        set_clauses = [f"{column} = ${idx}" for idx, column in enumerate(db_payload_columns, start=1)]
        if "rating_updated_at" in col_meta:
            set_clauses.append("rating_updated_at = CURRENT_TIMESTAMP")
        if "updated_at" in col_meta:
            set_clauses.append("updated_at = CURRENT_TIMESTAMP")
        set_sql = ", ".join(set_clauses)

        upsert_set_clauses = [f"{column} = EXCLUDED.{column}" for column in db_payload_columns]
        if "rating_updated_at" in col_meta:
            upsert_set_clauses.append("rating_updated_at = CURRENT_TIMESTAMP")
        if "updated_at" in col_meta:
            upsert_set_clauses.append("updated_at = CURRENT_TIMESTAMP")
        upsert_set_sql = ", ".join(upsert_set_clauses)

        steam_id_param = len(db_payload_columns) + 1
        if has_linked_jsonb:
            update_sql = f"""
                UPDATE iosca_players ip_target
                SET {set_sql}
                WHERE ip_target.steam_id = ${steam_id_param}
                   OR ip_target.steam_id IN (
                        SELECT linked.value
                        FROM iosca_players ip_owner
                        JOIN LATERAL jsonb_array_elements_text(COALESCE(ip_owner.linked_steam_ids, '[]'::jsonb)) AS linked(value) ON TRUE
                        WHERE ip_owner.steam_id = ${steam_id_param}
                   )
                   OR EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements_text(COALESCE(ip_target.linked_steam_ids, '[]'::jsonb)) AS linked(value)
                        WHERE linked.value = ${steam_id_param}
                   )
            """
        else:
            update_sql = f"""
                UPDATE iosca_players
                SET {set_sql}
                WHERE steam_id = ${steam_id_param}
            """

        updated = 0
        skipped_unmatched = 0
        skipped_samples: list[str] = []
        async with conn.transaction():
            rating_run_at = datetime.utcnow()
            for row in final_output_rows:
                steam_id = str(row["steamid"])
                payload = [row.get(column) for column in db_payload_columns]
                status = await conn.execute(update_sql, *payload, steam_id)
                touched = _rows_affected(status)
                if touched > 0:
                    updated += touched
                else:
                    skipped_unmatched += 1
                    if len(skipped_samples) < 10:
                        skipped_samples.append(steam_id)
                    continue

                await conn.execute(
                    """
                    INSERT INTO PLAYER_RATING_HISTORY (
                        steam_id,
                        player_name,
                        rating,
                        atk_rating,
                        mid_rating,
                        def_rating,
                        gk_rating,
                        main_role,
                        main_role_rating,
                        display_main_role_rating,
                        total_appearances,
                        total_minutes,
                        last_match_at,
                        formula_version,
                        source,
                        rating_run_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8,
                        $9, $10, $11, $12, $13, $14, $15, $16
                    )
                    """,
                    steam_id,
                    row.get("player"),
                    row.get("rating"),
                    row.get("atk_rating"),
                    row.get("mid_rating"),
                    row.get("def_rating"),
                    row.get("gk_rating"),
                    row.get("main_role"),
                    row.get("main_role_rating"),
                    row.get("display_main_role_rating"),
                    row.get("total_appearances"),
                    row.get("total_minutes"),
                    row.get("last_match_at"),
                    "role_based_v1",
                    "generate_player_ratings",
                    rating_run_at,
                )

    if skipped_unmatched:
        print(
            "Updated ratings: "
            f"{updated} rows updated, "
            f"{skipped_unmatched} rows skipped because no matching IOSCA_PLAYERS record was found."
        )
        if skipped_samples:
            print(f"Skipped Steam IDs sample: {', '.join(skipped_samples)}")
    else:
        print(f"Updated ratings: {updated} rows updated")


async def generate_player_ratings() -> bool:
    _set_last_generate_error(None)
    await init_db()

    # Deliberately light: the season rating is now built from each match's
    # already-computed match_rating (see ios_bot/utils/match_performance.py),
    # not recomputed from raw box-score counts, so this no longer needs the
    # full stat column list -- a meaningfully cheaper query at scale too.
    query = """
    SELECT
        COALESCE(NULLIF(p.canonical_steam_id, ''), pmd.steam_id) AS "Steam ID",
        COALESCE(
            NULLIF(p.discord_name, ''),
            NULLIF(pmd.player_name, ''),
            COALESCE(NULLIF(p.canonical_steam_id, ''), pmd.steam_id)
        ) AS "Name",
        pmd.position AS "Position",
        COALESCE(ms.match_id, ms.id::text, pmd.match_id::text) AS match_id,
        ms.datetime,
        pmd.status AS "status",
        pmd.match_rating,
        pmd.guild_id,
        pmd.time_played AS "timePlayed",
        pmd.time_gk AS "timeGK",
        pmd.time_def AS "timeDEF",
        pmd.time_mid AS "timeMID",
        pmd.time_att AS "timeATT"
    FROM counted_player_match_data pmd
    LEFT JOIN counted_match_stats ms
      ON (
           pmd.match_id::text = ms.match_id::text
           OR (CASE WHEN pmd.match_id::text ~ '^[0-9]+$' THEN pmd.match_id::bigint END) = ms.id::bigint
      )
    LEFT JOIN LATERAL (
        WITH candidates AS (
            SELECT
                ip.steam_id AS canonical_steam_id,
                ip.discord_name,
                EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(COALESCE(ip.linked_steam_ids, '[]'::jsonb)) AS linked(value)
                    WHERE linked.value = pmd.steam_id
                ) AS owns_alias,
                (ip.steam_id = pmd.steam_id) AS exact_match
            FROM iosca_players ip
            WHERE ip.steam_id = pmd.steam_id
               OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(COALESCE(ip.linked_steam_ids, '[]'::jsonb)) AS linked(value)
                    WHERE linked.value = pmd.steam_id
               )
        )
        SELECT canonical_steam_id, discord_name
        FROM candidates
        ORDER BY
            CASE
                WHEN owns_alias THEN 0
                WHEN exact_match THEN 1
                ELSE 2
            END,
            canonical_steam_id
        LIMIT 1
    ) p ON TRUE
    WHERE pmd.status NOT IN ('on_bench', 'bench', 'dnp', 'did_not_play')
    ORDER BY ms.datetime DESC NULLS LAST
    """

    try:
        rows = await db.pool.fetch(query)
        if not rows:
            _set_last_generate_error("No player match data found in database.")
            return False
    except Exception as e:
        _set_last_generate_error(f"Error fetching data: {e}")
        return False

    df = pd.DataFrame([dict(row) for row in rows])
    numeric_cols = ["timePlayed", "timeGK", "timeDEF", "timeMID", "timeATT", "match_rating"]
    for column in numeric_cols:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    for column in ("timePlayed", "timeGK", "timeDEF", "timeMID", "timeATT"):
        df[column] = df[column].fillna(0)

    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df["generalPosition"] = df.apply(infer_general_position, axis=1)

    # Per-team match calendars for missed-games inactivity tracking, and any
    # player-approved position preferences (see /set_position).
    team_calendars: dict[str, list[datetime]] = {}
    try:
        calendar_rows = await db.pool.fetch(
            "SELECT home_guild_id, away_guild_id, datetime FROM counted_match_stats WHERE datetime IS NOT NULL"
        )
        for row in calendar_rows:
            dt = row["datetime"]
            for guild_id in (row["home_guild_id"], row["away_guild_id"]):
                if guild_id is None:
                    continue
                team_calendars.setdefault(str(guild_id), []).append(dt)
        for guild_id in team_calendars:
            team_calendars[guild_id].sort()
    except Exception:
        team_calendars = {}

    preferred_roles: dict[str, str] = {}
    try:
        pref_rows = await db.pool.fetch(
            "SELECT steam_id, preferred_main_role FROM iosca_players WHERE preferred_main_role IS NOT NULL"
        )
        preferred_roles = {
            str(row["steam_id"]): str(row["preferred_main_role"]).upper()
            for row in pref_rows
            if row["preferred_main_role"]
        }
    except Exception:
        preferred_roles = {}

    player_records = _build_identity_records(df)
    final_output_rows = _prepare_output_rows(player_records, team_calendars, preferred_roles)
    final_output = pd.DataFrame(final_output_rows)

    output_file = Path(__file__).with_name("final_ratings.csv")
    final_output.to_csv(output_file, index=False)

    try:
        await _write_ratings_to_db(final_output_rows)
    except Exception as e:
        _set_last_generate_error(f"Error updating database: {e}")
        return False

    _set_last_generate_error(None)
    return True


async def update_team_average_ratings() -> bool:
    await init_db()
    try:
        teams = await db.teams.get_all_teams()
        for team in teams:
            guild_id = team.get("guild_id")
            if guild_id is None:
                continue
            await db.teams.update_team_average_rating(int(guild_id))
        return True
    except Exception as e:
        _set_last_generate_error(f"Error updating team averages: {e}")
        return False


async def main():
    ok = await generate_player_ratings()
    if not ok:
        print(get_last_generate_error() or "Failed to generate ratings")
        return
    await update_team_average_ratings()


if __name__ == "__main__":
    asyncio.run(main())

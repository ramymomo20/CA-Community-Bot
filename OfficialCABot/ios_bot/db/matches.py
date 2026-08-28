"""
Match operations for PostgreSQL database
"""

import json
import logging
import re
from ios_bot.config import MAIN_GUILD_ID
from typing import Optional, List, Dict, Any
from datetime import datetime
from .connection import DatabasePool
from .stats_moderation import ensure_stats_moderation_schema
from .utils import find_best_match
from .cache import QueryCache

logger = logging.getLogger(__name__)

_MATCHES_CACHE_TTL_SECONDS = 300


def _normalize_team_name(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    return re.sub(r"[^a-z0-9]+", "", raw)


class MatchOperations:
    """Handles all match-related database operations"""
    
    def __init__(self, pool: DatabasePool):
        self.pool = pool
        self._cache = QueryCache(safety_ttl_seconds=_MATCHES_CACHE_TTL_SECONDS)
        self._player_match_id_is_text = None
        self._player_match_has_event_timestamps = None
        self._has_source_filename = None
        self._has_match_id_unique = None
        self._match_stats_guild_id_is_text = None
        self._table_columns_cache: Dict[str, set[str]] = {}
        self._active_match_context_ready = False
        self._match_events_ready = False
        self._challenge_state_ready = False

    def invalidate_cache(self) -> None:
        """Call after any write that changes match results/lineups -- wipes
        every cached team-match-history/statistics entry."""
        self._cache.invalidate_prefix("matches:")

    async def ensure_stats_moderation_schema(self) -> None:
        async with self.pool.acquire() as conn:
            await ensure_stats_moderation_schema(conn)

    async def _match_stats_guild_id_expects_text(self) -> bool:
        """Detect whether match_stats.home_guild_id is stored as text."""
        if self._match_stats_guild_id_is_text is not None:
            return self._match_stats_guild_id_is_text

        try:
            row = await self.pool.fetchrow(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'match_stats'
                  AND column_name = 'home_guild_id'
                """
            )
            data_type = row['data_type'] if row else None
            self._match_stats_guild_id_is_text = data_type in ('character varying', 'text')
        except Exception as e:
            logger.error(f"Failed to detect match_stats.home_guild_id type: {e}")
            self._match_stats_guild_id_is_text = False

        return self._match_stats_guild_id_is_text

    async def _player_match_id_expects_text(self) -> bool:
        """Detect whether player_match_data.match_id is stored as text."""
        if self._player_match_id_is_text is not None:
            return self._player_match_id_is_text

        try:
            row = await self.pool.fetchrow(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'player_match_data'
                  AND column_name = 'match_id'
                """
            )
            data_type = row['data_type'] if row else None
            self._player_match_id_is_text = data_type in ('character varying', 'text')
        except Exception as e:
            logger.error(f"Failed to detect player_match_data.match_id type: {e}")
            self._player_match_id_is_text = False

        return self._player_match_id_is_text

    async def _resolve_player_match_id(self, match_id) -> object:
        """Return match_id in the correct type for player_match_data."""
        expects_text = await self._player_match_id_expects_text()
        if expects_text:
            if isinstance(match_id, str):
                return match_id
            try:
                row = await self.pool.fetchrow(
                    "SELECT match_id FROM MATCH_STATS WHERE id = $1",
                    match_id
                )
                if row and row.get('match_id'):
                    return row.get('match_id')
            except Exception:
                pass
            return str(match_id)

        # Numeric column: try to coerce to int for safety
        if isinstance(match_id, int):
            return match_id
        try:
            return int(match_id)
        except (TypeError, ValueError):
            return match_id

    async def _player_match_has_event_timestamps_column(self) -> bool:
        """Detect whether player_match_data.event_timestamps exists."""
        if self._player_match_has_event_timestamps is not None:
            return self._player_match_has_event_timestamps

        try:
            row = await self.pool.fetchrow(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'player_match_data'
                  AND column_name = 'event_timestamps'
                """
            )
            self._player_match_has_event_timestamps = bool(row)
        except Exception as e:
            logger.error(f"Failed to detect player_match_data.event_timestamps: {e}")
            self._player_match_has_event_timestamps = False

        return self._player_match_has_event_timestamps

    async def _get_table_columns(self, table_name: str) -> set[str]:
        """Return cached lower-case column names for a table."""
        key = str(table_name).lower()
        if key in self._table_columns_cache:
            return self._table_columns_cache[key]
        try:
            rows = await self.pool.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = $1
                """,
                key,
            )
            cols = {str(row["column_name"]).lower() for row in rows}
            self._table_columns_cache[key] = cols
            return cols
        except Exception as e:
            logger.error(f"Failed to fetch columns for {table_name}: {e}")
            return set()
    
    async def add_match(
        self,
        home_guild_id: Optional[int],
        away_guild_id: Optional[int],
        home_score: int,
        away_score: int,
        match_datetime: datetime,
        home_team_name: str,
        away_team_name: str,
        extratime: bool = False,
        penalties: bool = False,
        substitutions: Optional[List[Dict]] = None,
        home_lineup: Optional[List[Dict]] = None,
        away_lineup: Optional[List[Dict]] = None,
        match_id_str: Optional[str] = None,
        game_type: str = "8v8",
        source_filename: Optional[str] = None,
        match_summary_home: Optional[List[Dict[str, Any]]] = None,
        match_summary_away: Optional[List[Dict[str, Any]]] = None,
        comeback_flag: Optional[bool] = None,
    ) -> Optional[int]:
        """Add a new match to the database. guild_ids can be None for unregistered teams."""
        # Generate match_id if not provided
        if not match_id_str:
            home_id = home_guild_id or 0
            away_id = away_guild_id or 0
            match_id_str = f"{match_datetime.strftime('%Y%m%d%H%M%S')}_{home_id}_{away_id}"

        try:
            expects_text = await self._match_stats_guild_id_expects_text()
        except Exception:
            expects_text = False

        home_id_value = str(home_guild_id) if expects_text and home_guild_id is not None else home_guild_id
        away_id_value = str(away_guild_id) if expects_text and away_guild_id is not None else away_guild_id

        payload: Dict[str, Any] = {
            "match_id": match_id_str,
            "home_guild_id": home_id_value,
            "away_guild_id": away_id_value,
            "home_score": home_score,
            "away_score": away_score,
            "datetime": match_datetime,
            "home_team_name": home_team_name,
            "away_team_name": away_team_name,
            "extratime": extratime,
            "penalties": penalties,
            "substitutions": json.dumps(substitutions or []),
            "home_lineup": json.dumps(home_lineup or []),
            "away_lineup": json.dumps(away_lineup or []),
            "game_type": game_type,
        }

        table_cols = await self._get_table_columns("match_stats")
        optional_payload: Dict[str, Any] = {
            "source_filename": source_filename,
            "match_summary_home": json.dumps(match_summary_home or []),
            "match_summary_away": json.dumps(match_summary_away or []),
            "comeback_flag": comeback_flag,
        }
        for col, val in optional_payload.items():
            if col in table_cols and val is not None:
                payload[col] = val

        columns = list(payload.keys())
        placeholders = ", ".join(f"${idx}" for idx in range(1, len(columns) + 1))
        col_sql = ", ".join(columns)
        query = (
            f"INSERT INTO MATCH_STATS ({col_sql}) "
            f"VALUES ({placeholders}) "
            "ON CONFLICT (match_id) DO NOTHING "
            "RETURNING id"
        )
        params = [payload[col] for col in columns]
        
        try:
            db_id = await self.pool.fetchval(query, *params)
            if db_id:
                logger.info(f"✅ Match added: {home_guild_id} vs {away_guild_id} (ID: {db_id})")
                self.invalidate_cache()
            return db_id
        except Exception as e:
            logger.error(f"❌ Failed to add match: {e}")
            return None

    async def get_match_by_match_id(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Return the MATCH_STATS row for a given match_id (or None)."""
        try:
            row = await self.pool.fetchrow("SELECT * FROM MATCH_STATS WHERE match_id = $1", match_id)
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error fetching match by match_id {match_id}: {e}")
            return None

    async def update_match(
        self,
        match_stats_id: int,
        home_guild_id: Optional[int],
        away_guild_id: Optional[int],
        home_score: int,
        away_score: int,
        match_datetime: datetime,
        home_team_name: str,
        away_team_name: str,
        extratime: bool = False,
        penalties: bool = False,
        substitutions: Optional[List[Dict]] = None,
        home_lineup: Optional[List[Dict]] = None,
        away_lineup: Optional[List[Dict]] = None,
        match_id_str: Optional[str] = None,
        game_type: str = "8v8",
        source_filename: Optional[str] = None,
        match_summary_home: Optional[List[Dict[str, Any]]] = None,
        match_summary_away: Optional[List[Dict[str, Any]]] = None,
        comeback_flag: Optional[bool] = None,
    ) -> bool:
        """Update an existing match row in place while preserving MATCH_STATS.id."""
        try:
            match_stats_id = int(match_stats_id)
            expects_text = await self._match_stats_guild_id_expects_text()
        except Exception:
            expects_text = False

        home_id_value = str(home_guild_id) if expects_text and home_guild_id is not None else home_guild_id
        away_id_value = str(away_guild_id) if expects_text and away_guild_id is not None else away_guild_id

        payload: Dict[str, Any] = {
            "home_guild_id": home_id_value,
            "away_guild_id": away_id_value,
            "home_score": home_score,
            "away_score": away_score,
            "datetime": match_datetime,
            "home_team_name": home_team_name,
            "away_team_name": away_team_name,
            "extratime": extratime,
            "penalties": penalties,
            "substitutions": json.dumps(substitutions or []),
            "home_lineup": json.dumps(home_lineup or []),
            "away_lineup": json.dumps(away_lineup or []),
            "game_type": game_type,
        }
        if match_id_str:
            payload["match_id"] = match_id_str

        table_cols = await self._get_table_columns("match_stats")
        optional_payload: Dict[str, Any] = {
            "source_filename": source_filename,
            "match_summary_home": json.dumps(match_summary_home or []),
            "match_summary_away": json.dumps(match_summary_away or []),
            "comeback_flag": comeback_flag,
        }
        for col, val in optional_payload.items():
            if col in table_cols and val is not None:
                payload[col] = val
        if "updated_at" in table_cols:
            payload["updated_at"] = datetime.now()

        payload = {col: val for col, val in payload.items() if col in table_cols}
        if not payload:
            return False

        columns = list(payload.keys())
        assignments = ", ".join(f"{col} = ${idx}" for idx, col in enumerate(columns, start=1))
        query = f"UPDATE MATCH_STATS SET {assignments} WHERE id = ${len(columns) + 1}"
        params = [payload[col] for col in columns] + [match_stats_id]

        try:
            result = await self.pool.execute(query, *params)
            updated = bool(result and result.startswith("UPDATE ") and int(result.split()[-1]) > 0)
            if updated:
                self.invalidate_cache()
            return updated
        except Exception as e:
            logger.error(f"Failed to update match {match_stats_id}: {e}")
            return False

    async def match_exists(self, match_id: str) -> bool:
        """Lightweight existence check for a match_id."""
        try:
            val = await self.pool.fetchval("SELECT 1 FROM MATCH_STATS WHERE match_id = $1", match_id)
            return bool(val)
        except Exception as e:
            logger.error(f"Error checking match existence for {match_id}: {e}")
            return False

    async def _ensure_match_events_table(self) -> None:
        if self._match_events_ready:
            return

        await self.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS MATCH_EVENTS (
                id BIGSERIAL PRIMARY KEY,
                match_stats_id INTEGER NOT NULL REFERENCES MATCH_STATS(id) ON DELETE CASCADE,
                match_id VARCHAR(255) NOT NULL,
                event_index INTEGER NOT NULL,
                event_type VARCHAR(32) NOT NULL,
                raw_event VARCHAR(64),
                team VARCHAR(16),
                period VARCHAR(64),
                raw_second INTEGER,
                match_second INTEGER,
                minute INTEGER,
                clock VARCHAR(16),
                player1_steam_id VARCHAR(255),
                player2_steam_id VARCHAR(255),
                player3_steam_id VARCHAR(255),
                body_part INTEGER,
                x DOUBLE PRECISION,
                y DOUBLE PRECISION,
                norm_x DOUBLE PRECISION,
                norm_y DOUBLE PRECISION,
                field_min_x DOUBLE PRECISION,
                field_min_y DOUBLE PRECISION,
                field_max_x DOUBLE PRECISION,
                field_max_y DOUBLE PRECISION,
                raw_event_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE (match_stats_id, event_index)
            )
            """
        )
        await self.pool.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_match_events_match_type
            ON MATCH_EVENTS(match_stats_id, event_type)
            """
        )
        await self.pool.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_match_events_player_type
            ON MATCH_EVENTS(player1_steam_id, event_type)
            """
        )
        await self.pool.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_match_events_team_type
            ON MATCH_EVENTS(team, event_type)
            """
        )
        await self.pool.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_match_events_location
            ON MATCH_EVENTS(event_type, norm_x, norm_y)
            """
        )
        self._match_events_ready = True

    async def replace_match_events(
        self,
        match_stats_id: int,
        match_id_str: Optional[str],
        events: List[Dict[str, Any]],
        prune_existing: bool = True,
    ) -> Dict[str, int]:
        """Store event-location rows for a match, optionally pruning stale rows first."""
        await self._ensure_match_events_table()
        try:
            match_stats_id = int(match_stats_id)
        except Exception:
            return {"deleted": 0, "inserted": 0}

        if not match_id_str:
            row = await self.pool.fetchrow("SELECT match_id FROM MATCH_STATS WHERE id = $1", match_stats_id)
            match_id_str = str(row.get("match_id")) if row and row.get("match_id") else str(match_stats_id)

        deleted = 0
        if prune_existing:
            delete_result = await self.pool.execute(
                "DELETE FROM MATCH_EVENTS WHERE match_stats_id = $1",
                match_stats_id,
            )
            deleted = int(delete_result.split()[-1]) if delete_result and delete_result.startswith("DELETE ") else 0

        cleaned: List[tuple] = []
        for item in events or []:
            if not isinstance(item, dict):
                continue
            if not item.get("event_type"):
                continue
            cleaned.append(
                (
                    match_stats_id,
                    str(match_id_str),
                    item.get("event_index"),
                    item.get("event_type"),
                    item.get("raw_event"),
                    item.get("team"),
                    item.get("period"),
                    item.get("raw_second"),
                    item.get("match_second"),
                    item.get("minute"),
                    item.get("clock"),
                    item.get("player1_steam_id"),
                    item.get("player2_steam_id"),
                    item.get("player3_steam_id"),
                    item.get("body_part"),
                    item.get("x"),
                    item.get("y"),
                    item.get("norm_x"),
                    item.get("norm_y"),
                    item.get("field_min_x"),
                    item.get("field_min_y"),
                    item.get("field_max_x"),
                    item.get("field_max_y"),
                    json.dumps(item.get("raw_event_payload") or {}),
                )
            )

        if not cleaned:
            return {"deleted": deleted, "inserted": 0}

        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO MATCH_EVENTS (
                    match_stats_id, match_id, event_index, event_type, raw_event,
                    team, period, raw_second, match_second, minute, clock,
                    player1_steam_id, player2_steam_id, player3_steam_id, body_part,
                    x, y, norm_x, norm_y, field_min_x, field_min_y, field_max_x, field_max_y,
                    raw_event_payload
                )
                VALUES (
                    $1, $2, $3, $4, $5,
                    $6, $7, $8, $9, $10, $11,
                    $12, $13, $14, $15,
                    $16, $17, $18, $19, $20, $21, $22, $23,
                    $24::jsonb
                )
                ON CONFLICT (match_stats_id, event_index) DO UPDATE SET
                    event_type = EXCLUDED.event_type,
                    raw_event = EXCLUDED.raw_event,
                    team = EXCLUDED.team,
                    period = EXCLUDED.period,
                    raw_second = EXCLUDED.raw_second,
                    match_second = EXCLUDED.match_second,
                    minute = EXCLUDED.minute,
                    clock = EXCLUDED.clock,
                    player1_steam_id = EXCLUDED.player1_steam_id,
                    player2_steam_id = EXCLUDED.player2_steam_id,
                    player3_steam_id = EXCLUDED.player3_steam_id,
                    body_part = EXCLUDED.body_part,
                    x = EXCLUDED.x,
                    y = EXCLUDED.y,
                    norm_x = EXCLUDED.norm_x,
                    norm_y = EXCLUDED.norm_y,
                    field_min_x = EXCLUDED.field_min_x,
                    field_min_y = EXCLUDED.field_min_y,
                    field_max_x = EXCLUDED.field_max_x,
                    field_max_y = EXCLUDED.field_max_y,
                    raw_event_payload = EXCLUDED.raw_event_payload
                """,
                cleaned,
            )

        return {"deleted": deleted, "inserted": len(cleaned)}

    async def get_match_events(
        self,
        match_stats_id: int,
        event_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch stored event-location rows for a match."""
        await self._ensure_match_events_table()
        params: List[Any] = [int(match_stats_id)]
        query = "SELECT * FROM MATCH_EVENTS WHERE match_stats_id = $1"
        if event_types:
            cleaned_types = [str(item).strip().lower() for item in event_types if str(item).strip()]
            if cleaned_types:
                params.append(cleaned_types)
                query += " AND event_type = ANY($2::text[])"
        query += " ORDER BY COALESCE(match_second, raw_second, 0), event_index"

        rows = await self.pool.fetch(query, *params)
        return [dict(row) for row in rows]
    
    async def get_match(self, match_id: int) -> Optional[Dict[str, Any]]:
        """Get a match by ID"""
        query = """
        SELECT m.*,
               COALESCE(ht.guild_name, m.home_team_name, tf.home_name_raw) as home_team_name,
               COALESCE(at.guild_name, m.away_team_name, tf.away_name_raw) as away_team_name
        FROM MATCH_STATS m
        LEFT JOIN TOURNAMENT_FIXTURES tf ON tf.played_match_stats_id = m.id
        LEFT JOIN IOSCA_TEAMS ht ON m.home_guild_id = ht.guild_id
        LEFT JOIN IOSCA_TEAMS at ON m.away_guild_id = at.guild_id
        WHERE m.id = $1
        """
        row = await self.pool.fetchrow(query, match_id)
        
        if row:
            match_data = dict(row)
            match_data['home_lineup'] = json.loads(match_data.get('home_lineup', '[]'))
            match_data['away_lineup'] = json.loads(match_data.get('away_lineup', '[]'))
            return match_data
        return None
    
    async def get_matches_by_team(
        self,
        guild_id: int,
        limit: Optional[int] = 50,
        start_date: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Get all matches played by a specific team (cached until a match
        write invalidates it)."""
        cache_key = f"matches:by_team:{guild_id}:{limit}:{start_date.isoformat() if start_date else ''}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return [dict(m) for m in cached]

        aliases = []
        try:
            team_row = await self.pool.fetchrow(
                "SELECT guild_name, nicknames FROM IOSCA_TEAMS WHERE guild_id = $1",
                guild_id
            )
            if team_row:
                aliases.append(team_row.get("guild_name"))
                nicknames = team_row.get("nicknames")
                if isinstance(nicknames, str):
                    try:
                        nicknames = json.loads(nicknames)
                    except Exception:
                        nicknames = []
                if isinstance(nicknames, list):
                    aliases.extend([n for n in nicknames if n])
        except Exception:
            aliases = []

        patterns = [f"%{a}%" for a in aliases if a]
        query = """
        SELECT m.*,
               COALESCE(ht.guild_name, m.home_team_name) as home_team_name,
               COALESCE(at.guild_name, m.away_team_name) as away_team_name
        FROM MATCH_STATS m
        LEFT JOIN IOSCA_TEAMS ht ON m.home_guild_id = ht.guild_id
        LEFT JOIN IOSCA_TEAMS at ON m.away_guild_id = at.guild_id
        WHERE (m.home_guild_id = $1 OR m.away_guild_id = $1)
        """
        
        params = [guild_id]
        if patterns:
            query = query.replace("WHERE (m.home_guild_id = $1 OR m.away_guild_id = $1)",
                                  "WHERE ((m.home_guild_id = $1 OR m.away_guild_id = $1) OR (m.home_team_name ILIKE ANY($2::text[]) OR m.away_team_name ILIKE ANY($2::text[])))")
            params.append(patterns)
        if start_date:
            query += f" AND m.datetime >= ${len(params) + 1}"
            params.append(start_date)
            query += " ORDER BY m.datetime DESC"
            if limit:
                query += f" LIMIT ${len(params) + 1}"
                params.append(limit)
        else:
            query += " ORDER BY m.datetime DESC"
            if limit:
                query += f" LIMIT ${len(params) + 1}"
                params.append(limit)
        
        rows = await self.pool.fetch(query, *params)
        
        matches = []
        for row in rows:
            match_data = dict(row)
            match_data['home_lineup'] = json.loads(match_data.get('home_lineup', '[]'))
            match_data['away_lineup'] = json.loads(match_data.get('away_lineup', '[]'))
            matches.append(match_data)

        self._cache.set(cache_key, matches)
        return matches

    async def get_matches_between_teams(
        self,
        guild_id_1: int,
        guild_id_2: int,
        limit: int = 50,
        start_date: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Get all matches between two specific teams"""
        query = """
        SELECT m.*,
               ht.guild_name as home_team_name,
               at.guild_name as away_team_name
        FROM MATCH_STATS m
        LEFT JOIN IOSCA_TEAMS ht ON m.home_guild_id = ht.guild_id
        LEFT JOIN IOSCA_TEAMS at ON m.away_guild_id = at.guild_id
        WHERE ((m.home_guild_id = $1 AND m.away_guild_id = $2)
               OR (m.home_guild_id = $2 AND m.away_guild_id = $1))
        """
        
        params = [guild_id_1, guild_id_2]
        if start_date:
            query += " AND m.datetime >= $3"
            params.append(start_date)
            query += " ORDER BY m.datetime DESC LIMIT $4"
            params.append(limit)
        else:
            query += " ORDER BY m.datetime DESC LIMIT $3"
            params.append(limit)
        
        rows = await self.pool.fetch(query, *params)
        
        matches = []
        for row in rows:
            match_data = dict(row)
            match_data['home_lineup'] = json.loads(match_data.get('home_lineup', '[]'))
            match_data['away_lineup'] = json.loads(match_data.get('away_lineup', '[]'))
            matches.append(match_data)
        
        return matches
    
    async def get_matches_involving_teams(
        self,
        guild_ids: List[int],
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get matches involving any of the specified teams"""
        if not guild_ids:
            return []
        
        placeholders = ', '.join([f'${i+1}' for i in range(len(guild_ids))])
        query = f"""
        SELECT m.*,
               ht.guild_name as home_team_name,
               at.guild_name as away_team_name
        FROM MATCH_STATS m
        LEFT JOIN IOSCA_TEAMS ht ON m.home_guild_id = ht.guild_id
        LEFT JOIN IOSCA_TEAMS at ON m.away_guild_id = at.guild_id
        WHERE m.home_guild_id IN ({placeholders})
           OR m.away_guild_id IN ({placeholders})
        ORDER BY m.datetime DESC
        LIMIT ${len(guild_ids) * 2 + 1}
        """
        
        params = guild_ids + guild_ids + [limit]
        rows = await self.pool.fetch(query, *params)
        
        matches = []
        for row in rows:
            match_data = dict(row)
            match_data['home_lineup'] = json.loads(match_data.get('home_lineup', '[]'))
            match_data['away_lineup'] = json.loads(match_data.get('away_lineup', '[]'))
            matches.append(match_data)
        
        return matches
    
    async def get_player_match_data(self, match_id: int) -> List[Dict[str, Any]]:
        """Get all player statistics for a match"""
        match_id_value = await self._resolve_player_match_id(match_id)
        query = """
        SELECT pmd.*,
               COALESCE(p.discord_name, pmd.player_name, pmd.steam_id) as player_name
        FROM PLAYER_MATCH_DATA pmd
        LEFT JOIN IOSCA_PLAYERS p ON pmd.steam_id = p.steam_id
        WHERE pmd.match_id::text = $1::text
        ORDER BY pmd.goals DESC, pmd.assists DESC
        """
        rows = await self.pool.fetch(query, str(match_id_value))
        return [dict(row) for row in rows]
    
    async def get_player_stats_summary(
        self,
        player_discord_id: int
    ) -> Optional[Dict[str, Any]]:
        """Get aggregated statistics for a player"""
        query = """
        SELECT 
            COUNT(DISTINCT pmd.match_id) as matches_played,
            SUM(pmd.goals) as total_goals,
            SUM(pmd.assists) as total_assists,
            SUM(pmd.second_assists) as total_second_assists,
            SUM(pmd.shots) as total_shots,
            SUM(pmd.shots_on_goal) as total_shots_on_goal,
            SUM(pmd.passes_attempted) as total_passes,
            SUM(pmd.passes_completed) as total_passes_completed,
            SUM(pmd.tackles) as total_tackles,
            SUM(pmd.interceptions) as total_interceptions,
            SUM(pmd.key_passes) as total_key_passes,
            SUM(pmd.chances_created) as total_chances_created,
            SUM(pmd.offsides) as total_offsides,
            SUM(pmd.yellow_cards) as total_yellow_cards,
            SUM(pmd.red_cards) as total_red_cards,
            SUM(pmd.own_goals) as total_own_goals,
            SUM(pmd.fouls_suffered) as total_fouls_suffered,
            SUM(pmd.free_kicks) as total_free_kicks,
            SUM(pmd.penalties) as total_penalties,
            SUM(pmd.corners) as total_corners,
            SUM(pmd.throw_ins) as total_throwins,
            SUM(pmd.goal_kicks) as total_goal_kicks,
            SUM(pmd.fouls) as total_fouls
        FROM PLAYER_MATCH_DATA pmd
        LEFT JOIN IOSCA_PLAYERS p ON pmd.steam_id = p.steam_id
        WHERE p.discord_id = $1
        """
        
        try:
            result = await self.pool.fetchrow(query, player_discord_id)
            return dict(result) if result else None
        except Exception as e:
            logger.error(f"Error getting player stats summary: {e}")
            return None

    async def add_player_match_data(
        self,
        match_id: int,
        steam_id: str,
        guild_id: Optional[int] = None,
        player_name: Optional[str] = None,
        guild_team_name: Optional[str] = None,
        status: Optional[str] = None,
        position: str = None,
        goals: int = 0,
        assists: int = 0,
        second_assists: int = 0,
        shots: int = 0,
        shots_on_goal: int = 0,
        passes_completed: int = 0,
        passes_attempted: int = 0,
        chances_created: int = 0,
        key_passes: int = 0,
        interceptions: int = 0,
        tackles: int = 0,
        sliding_tackles_completed: int = 0,
        fouls: int = 0,
        yellow_cards: int = 0,
        red_cards: int = 0,
        keeper_saves: int = 0,
        keeper_saves_caught: int = 0,
        goals_conceded: int = 0,
        offsides: int = 0,
        own_goals: int = 0,
        fouls_suffered: int = 0,
        free_kicks: int = 0,
        penalties: int = 0,
        corners: int = 0,
        throw_ins: int = 0,
        goal_kicks: int = 0,
        possession: int = 0,
        time_played: int = 0,
        time_gk: int = 0,
        time_def: int = 0,
        time_mid: int = 0,
        time_att: int = 0,
        distance_covered: float = 0.0,
        pass_accuracy: float = 0.0,
        is_single_keeper: bool = False,
        opponent_conceded: int = 0,
        event_timestamps: Optional[Dict[str, List[int]]] = None,
        clutch_actions: Optional[List[Dict[str, Any]]] = None,
        sub_impact: Optional[Dict[str, Any]] = None,
        match_rating: Optional[float] = None,
        is_match_mvp: bool = False,
        mvp_score: Optional[float] = None,
        mvp_key_stats: Optional[List[str]] = None,
        update_existing: bool = False,
    ) -> bool:
        """Add player match data - stores all players by steam_id.
        
        Args:
            match_id: Match ID (integer, references match_stats.id)
            steam_id: Player's Steam ID
            guild_id: Optional - linked team guild ID (NULL if team not registered)
            position: Player's position (GK, LB, CB, etc.)
            All stat columns matching the database schema
        """
        try:
            # Determine correct type for match_id depending on the DB column type.
            # Avoid relying solely on cached detection; explicitly coerce here.
            try:
                expects_text = await self._player_match_id_expects_text()
            except Exception:
                expects_text = False

            if expects_text:
                # Resolve to match_stats.match_id when the FK is text-based
                match_id_value = await self._resolve_player_match_id(match_id)
            else:
                # Prefer integer when the column is numeric
                try:
                    match_id_value = int(match_id)
                except Exception:
                    match_id_value = match_id
            table_cols = await self._get_table_columns("player_match_data")
            payload: Dict[str, Any] = {
                "match_id": match_id_value,
                "steam_id": steam_id,
                "guild_id": guild_id,
                "position": position,
                "goals": goals,
                "assists": assists,
                "second_assists": second_assists,
                "shots": shots,
                "shots_on_goal": shots_on_goal,
                "passes_completed": passes_completed,
                "passes_attempted": passes_attempted,
                "chances_created": chances_created,
                "key_passes": key_passes,
                "interceptions": interceptions,
                "tackles": tackles,
                "sliding_tackles_completed": sliding_tackles_completed,
                "fouls": fouls,
                "yellow_cards": yellow_cards,
                "red_cards": red_cards,
                "keeper_saves": keeper_saves,
                "keeper_saves_caught": keeper_saves_caught,
                "goals_conceded": goals_conceded,
                "offsides": offsides,
                "own_goals": own_goals,
                "fouls_suffered": fouls_suffered,
                "free_kicks": free_kicks,
                "penalties": penalties,
                "corners": corners,
                "throw_ins": throw_ins,
                "goal_kicks": goal_kicks,
                "possession": possession,
                "time_played": time_played,
                "time_gk": time_gk,
                "time_def": time_def,
                "time_mid": time_mid,
                "time_att": time_att,
                "distance_covered": distance_covered,
                "pass_accuracy": pass_accuracy,
                "is_single_keeper": is_single_keeper,
                "opponent_conceded": opponent_conceded,
            }

            optional_payload: Dict[str, Any] = {
                "event_timestamps": json.dumps(event_timestamps or {}),
                "player_name": player_name,
                "guild_team_name": guild_team_name,
                "status": status,
                "clutch_actions": json.dumps(clutch_actions or []),
                "sub_impact": json.dumps(sub_impact or {}),
                "match_rating": match_rating,
                "is_match_mvp": is_match_mvp,
                "mvp_score": mvp_score,
                "mvp_key_stats": json.dumps(mvp_key_stats or []),
            }
            for col, val in optional_payload.items():
                if col in table_cols and val is not None:
                    payload[col] = val

            # Keep only columns that actually exist in runtime schema.
            payload = {col: val for col, val in payload.items() if col in table_cols}
            if not payload:
                logger.error("PLAYER_MATCH_DATA insert payload is empty after schema filtering")
                return False

            cols = list(payload.keys())
            placeholders = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
            if update_existing:
                existing = await self.pool.fetchrow(
                    """
                    SELECT id
                    FROM PLAYER_MATCH_DATA
                    WHERE match_id::text = $1::text
                      AND steam_id = $2
                    ORDER BY id
                    LIMIT 1
                    """,
                    str(match_id_value),
                    str(steam_id),
                )
                if existing:
                    update_cols = [col for col in cols if col not in {"match_id", "steam_id"}]
                    if update_cols:
                        assignments = ", ".join(
                            f"{col} = ${idx}" for idx, col in enumerate(update_cols, start=1)
                        )
                        params = [payload[col] for col in update_cols] + [int(existing["id"])]
                        await self.pool.execute(
                            f"UPDATE PLAYER_MATCH_DATA SET {assignments} WHERE id = ${len(update_cols) + 1}",
                            *params,
                        )
                    return True

            query = (
                f"INSERT INTO PLAYER_MATCH_DATA ({', '.join(cols)}) "
                f"VALUES ({placeholders}) "
                "ON CONFLICT DO NOTHING"
            )
            params = [payload[col] for col in cols]
            await self.pool.execute(query, *params)
            return True
        except Exception as e:
            logger.error(f"Failed to add player match data for {steam_id}: {e}")
            return False

    async def bulk_add_player_match_data(
        self,
        rows: List[Dict[str, Any]],
        *,
        update_existing: bool = False,
    ) -> int:
        """Insert many PLAYER_MATCH_DATA rows in one transaction."""
        if not rows:
            return 0

        try:
            try:
                expects_text = await self._player_match_id_expects_text()
            except Exception:
                expects_text = False

            first_match_id = rows[0].get("match_id")
            if expects_text:
                match_id_value = await self._resolve_player_match_id(first_match_id)
            else:
                try:
                    match_id_value = int(first_match_id)
                except Exception:
                    match_id_value = first_match_id

            table_cols = await self._get_table_columns("player_match_data")
            desired_columns = [
                "match_id",
                "steam_id",
                "guild_id",
                "position",
                "goals",
                "assists",
                "second_assists",
                "shots",
                "shots_on_goal",
                "passes_completed",
                "passes_attempted",
                "chances_created",
                "key_passes",
                "interceptions",
                "tackles",
                "sliding_tackles_completed",
                "fouls",
                "yellow_cards",
                "red_cards",
                "keeper_saves",
                "keeper_saves_caught",
                "goals_conceded",
                "offsides",
                "own_goals",
                "fouls_suffered",
                "free_kicks",
                "penalties",
                "corners",
                "throw_ins",
                "goal_kicks",
                "possession",
                "time_played",
                "time_gk",
                "time_def",
                "time_mid",
                "time_att",
                "distance_covered",
                "pass_accuracy",
                "is_single_keeper",
                "opponent_conceded",
                "event_timestamps",
                "player_name",
                "guild_team_name",
                "status",
                "clutch_actions",
                "sub_impact",
                "match_rating",
                "is_match_mvp",
                "mvp_score",
                "mvp_key_stats",
            ]
            insert_columns = [column for column in desired_columns if column in table_cols]
            if not insert_columns:
                logger.error("PLAYER_MATCH_DATA bulk insert payload is empty after schema filtering")
                return 0

            payload_rows: List[tuple[Any, ...]] = []
            for row in rows:
                raw_match_id = row.get("match_id")
                row_match_id = match_id_value
                if str(raw_match_id) != str(first_match_id):
                    row_match_id = await self._resolve_player_match_id(raw_match_id) if expects_text else raw_match_id

                payload = {
                    "match_id": row_match_id,
                    "steam_id": row.get("steam_id"),
                    "guild_id": row.get("guild_id"),
                    "position": row.get("position"),
                    "goals": row.get("goals", 0),
                    "assists": row.get("assists", 0),
                    "second_assists": row.get("second_assists", 0),
                    "shots": row.get("shots", 0),
                    "shots_on_goal": row.get("shots_on_goal", 0),
                    "passes_completed": row.get("passes_completed", 0),
                    "passes_attempted": row.get("passes_attempted", 0),
                    "chances_created": row.get("chances_created", 0),
                    "key_passes": row.get("key_passes", 0),
                    "interceptions": row.get("interceptions", 0),
                    "tackles": row.get("tackles", 0),
                    "sliding_tackles_completed": row.get("sliding_tackles_completed", 0),
                    "fouls": row.get("fouls", 0),
                    "yellow_cards": row.get("yellow_cards", 0),
                    "red_cards": row.get("red_cards", 0),
                    "keeper_saves": row.get("keeper_saves", 0),
                    "keeper_saves_caught": row.get("keeper_saves_caught", 0),
                    "goals_conceded": row.get("goals_conceded", 0),
                    "offsides": row.get("offsides", 0),
                    "own_goals": row.get("own_goals", 0),
                    "fouls_suffered": row.get("fouls_suffered", 0),
                    "free_kicks": row.get("free_kicks", 0),
                    "penalties": row.get("penalties", 0),
                    "corners": row.get("corners", 0),
                    "throw_ins": row.get("throw_ins", 0),
                    "goal_kicks": row.get("goal_kicks", 0),
                    "possession": row.get("possession", 0),
                    "time_played": row.get("time_played", 0),
                    "time_gk": row.get("time_gk", 0),
                    "time_def": row.get("time_def", 0),
                    "time_mid": row.get("time_mid", 0),
                    "time_att": row.get("time_att", 0),
                    "distance_covered": row.get("distance_covered", 0.0),
                    "pass_accuracy": row.get("pass_accuracy", 0.0),
                    "is_single_keeper": bool(row.get("is_single_keeper", False)),
                    "opponent_conceded": row.get("opponent_conceded", 0),
                    "event_timestamps": json.dumps(row.get("event_timestamps") or {}),
                    "player_name": row.get("player_name"),
                    "guild_team_name": row.get("guild_team_name"),
                    "status": row.get("status"),
                    "clutch_actions": json.dumps(row.get("clutch_actions") or []),
                    "sub_impact": json.dumps(row.get("sub_impact") or {}),
                    "match_rating": row.get("match_rating"),
                    "is_match_mvp": bool(row.get("is_match_mvp")),
                    "mvp_score": row.get("mvp_score"),
                    "mvp_key_stats": json.dumps(row.get("mvp_key_stats") or []),
                }
                payload_rows.append(tuple(payload.get(column) for column in insert_columns))

            placeholders = ", ".join(f"${index}" for index in range(1, len(insert_columns) + 1))
            query = (
                f"INSERT INTO PLAYER_MATCH_DATA ({', '.join(insert_columns)}) "
                f"VALUES ({placeholders}) "
                "ON CONFLICT DO NOTHING"
            )

            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    if update_existing:
                        await conn.execute(
                            """
                            DELETE FROM PLAYER_MATCH_DATA
                            WHERE match_id::text = $1::text
                            """,
                            str(match_id_value),
                        )
                    await conn.executemany(query, payload_rows)
            return len(payload_rows)
        except Exception as e:
            logger.error(f"Failed to bulk add player match data: {e}")
            return 0
    
    async def delete_match(self, match_id: int) -> bool:
        """Delete a match and all associated player data"""
        try:
            async with self.pool.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("DELETE FROM PLAYER_MATCH_DATA WHERE match_id = $1", match_id)
                    await conn.execute("DELETE FROM MATCH_STATS WHERE id = $1", match_id)
            logger.info(f"Match {match_id} deleted")
            self.invalidate_cache()
            return True
        except Exception as e:
            logger.error(f"Failed to delete match: {e}")
            return False

    async def set_match_stats_exclusion(
        self,
        match_stats_id: int,
        excluded: bool,
        reason: Optional[str] = None,
        updated_by_discord_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Toggle whether an entire match should count toward hub/rating stats."""
        try:
            async with self.pool.acquire() as conn:
                await ensure_stats_moderation_schema(conn)
                match_row = await conn.fetchrow(
                    """
                    SELECT id, match_id, datetime, home_team_name, away_team_name
                    FROM public.match_stats
                    WHERE id = $1
                    """,
                    int(match_stats_id),
                )
                if not match_row:
                    return None

                if excluded:
                    await conn.execute(
                        """
                        INSERT INTO public.match_stats_exclusions (
                            match_stats_id,
                            exclude_from_stats,
                            reason,
                            updated_by_discord_id,
                            updated_at
                        )
                        VALUES ($1, TRUE, NULLIF($2, ''), $3, NOW())
                        ON CONFLICT (match_stats_id) DO UPDATE
                        SET
                            exclude_from_stats = EXCLUDED.exclude_from_stats,
                            reason = EXCLUDED.reason,
                            updated_by_discord_id = EXCLUDED.updated_by_discord_id,
                            updated_at = NOW()
                        """,
                        int(match_stats_id),
                        str(reason or "").strip(),
                        updated_by_discord_id,
                    )
                else:
                    await conn.execute(
                        "DELETE FROM public.match_stats_exclusions WHERE match_stats_id = $1",
                        int(match_stats_id),
                    )

                summary = dict(match_row)
                summary["excluded"] = bool(excluded)
                summary["reason"] = str(reason or "").strip() or None
                return summary
        except Exception as e:
            logger.error(f"Failed to update match stats exclusion for {match_stats_id}: {e}")
            return None

    async def set_player_match_stats_exclusion(
        self,
        match_stats_id: int,
        steam_id: str,
        excluded: bool,
        reason: Optional[str] = None,
        updated_by_discord_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Toggle whether a specific player's row should count in a given match."""
        steam_key = str(steam_id or "").strip()
        if not steam_key:
            return None

        try:
            async with self.pool.acquire() as conn:
                await ensure_stats_moderation_schema(conn)
                match_row = await conn.fetchrow(
                    """
                    SELECT id, match_id, datetime, home_team_name, away_team_name
                    FROM public.match_stats
                    WHERE id = $1
                    """,
                    int(match_stats_id),
                )
                if not match_row:
                    return None

                player_row = await conn.fetchrow(
                    """
                    SELECT DISTINCT
                        pmd.steam_id,
                        pmd.player_name,
                        pmd.guild_team_name
                    FROM public.player_match_data pmd
                    LEFT JOIN public.match_stats ms
                      ON (
                           pmd.match_id::text = ms.match_id::text
                           OR (CASE WHEN pmd.match_id::text ~ '^[0-9]+$' THEN pmd.match_id::bigint END) = ms.id::bigint
                      )
                    WHERE ms.id = $1
                      AND lower(btrim(pmd.steam_id)) = lower(btrim($2))
                    ORDER BY pmd.player_name ASC NULLS LAST
                    LIMIT 1
                    """,
                    int(match_stats_id),
                    steam_key,
                )
                if not player_row:
                    return None

                canonical_steam_id = str(player_row.get("steam_id") or steam_key).strip()
                if excluded:
                    await conn.execute(
                        """
                        INSERT INTO public.player_match_stat_exclusions (
                            match_stats_id,
                            steam_id,
                            exclude_from_stats,
                            reason,
                            updated_by_discord_id,
                            updated_at
                        )
                        VALUES ($1, $2, TRUE, NULLIF($3, ''), $4, NOW())
                        ON CONFLICT (match_stats_id, steam_id) DO UPDATE
                        SET
                            exclude_from_stats = EXCLUDED.exclude_from_stats,
                            reason = EXCLUDED.reason,
                            updated_by_discord_id = EXCLUDED.updated_by_discord_id,
                            updated_at = NOW()
                        """,
                        int(match_stats_id),
                        canonical_steam_id,
                        str(reason or "").strip(),
                        updated_by_discord_id,
                    )
                else:
                    await conn.execute(
                        """
                        DELETE FROM public.player_match_stat_exclusions
                        WHERE match_stats_id = $1
                          AND lower(btrim(steam_id)) = lower(btrim($2))
                        """,
                        int(match_stats_id),
                        canonical_steam_id,
                    )

                summary = dict(match_row)
                summary["excluded"] = bool(excluded)
                summary["reason"] = str(reason or "").strip() or None
                summary["steam_id"] = canonical_steam_id
                summary["player_name"] = player_row.get("player_name")
                summary["guild_team_name"] = player_row.get("guild_team_name")
                return summary
        except Exception as e:
            logger.error(
                f"Failed to update player stats exclusion for match {match_stats_id}, steam {steam_key}: {e}"
            )
            return None
    
    async def add_manual_match(
        self,
        home_guild_id: int,
        away_guild_id: int,
        home_score: int,
        away_score: int,
        match_datetime: datetime,
        league_name: Optional[str] = None,
        notes: Optional[str] = None,
        is_forfeit: bool = False
    ) -> Optional[int]:
        """Add a manual match result"""
        return await self.add_match(
            home_guild_id=home_guild_id,
            away_guild_id=away_guild_id,
            home_score=home_score,
            away_score=away_score,
            match_datetime=match_datetime,
            home_team_name="Manual Home",
            away_team_name="Manual Away",
            extratime=False,
            penalties=False
        )
    
    async def add_forfeit(
        self,
        forfeiting_team_guild_id: int,
        opponent_team_guild_id: int,
        league_name: Optional[str] = None,
        forfeit_reason: Optional[str] = None
    ) -> Optional[int]:
        """Add a forfeit match (3-0 win for opponent)"""
        return await self.add_manual_match(
            home_guild_id=opponent_team_guild_id,
            away_guild_id=forfeiting_team_guild_id,
            home_score=3,
            away_score=0,
            match_datetime=datetime.now(),
            league_name=league_name,
            notes=f"Forfeit: {forfeit_reason}" if forfeit_reason else "Forfeit",
            is_forfeit=True
        )
    
    async def update_match_result(
        self,
        match_id: int,
        home_score: Optional[int] = None,
        away_score: Optional[int] = None
    ) -> bool:
        """Update an existing match result"""
        updates = []
        params = []
        param_count = 1
        
        if home_score is not None:
            updates.append(f"home_score = ${param_count}")
            params.append(home_score)
            param_count += 1
        
        if away_score is not None:
            updates.append(f"away_score = ${param_count}")
            params.append(away_score)
            param_count += 1
        
        if not updates:
            return False
        
        params.append(match_id)
        query = f"UPDATE MATCH_STATS SET {', '.join(updates)} WHERE id = ${param_count}"
        
        try:
            await self.pool.execute(query, *params)
            logger.info(f"Match {match_id} updated")
            return True
        except Exception as e:
            logger.error(f"Failed to update match: {e}")
            return False
    
    async def get_matches_involving_teams(
        self,
        guild_ids: List[int],
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get matches involving any of the specified teams"""
        if not guild_ids:
            return []
        
        placeholders = ', '.join([f'${i+1}' for i in range(len(guild_ids))])
        query = f"""
        SELECT m.*,
               ht.guild_name as home_team_name,
               at.guild_name as away_team_name
        FROM MATCH_STATS m
        LEFT JOIN IOSCA_TEAMS ht ON m.home_guild_id = ht.guild_id
        LEFT JOIN IOSCA_TEAMS at ON m.away_guild_id = at.guild_id
        WHERE m.home_guild_id IN ({placeholders})
           OR m.away_guild_id IN ({placeholders})
        ORDER BY m.datetime DESC
        LIMIT ${len(guild_ids) * 2 + 1}
        """
        
        params = guild_ids + guild_ids + [limit]
        rows = await self.pool.fetch(query, *params)
        
        matches = []
        for row in rows:
            match_data = dict(row)
            match_data['home_lineup'] = json.loads(match_data.get('home_lineup', '[]'))
            match_data['away_lineup'] = json.loads(match_data.get('away_lineup', '[]'))
            matches.append(match_data)
        
        return matches
    
    async def get_team_statistics(self, guild_id: int) -> Dict[str, Any]:
        """Get comprehensive team statistics (cached until a match write
        invalidates it)."""
        cache_key = f"matches:team_stats:{guild_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return dict(cached)

        team_name = None
        try:
            row = await self.pool.fetchrow("SELECT guild_name FROM IOSCA_TEAMS WHERE guild_id = $1", guild_id)
            if row:
                team_name = row.get("guild_name")
        except Exception:
            team_name = None
        matches = await self.get_matches_by_team(guild_id=guild_id, limit=None)
        
        total_matches = len(matches)
        wins = draws = losses = 0
        goals_for = goals_against = 0
        recent_matches = []
        
        for match in matches:
            try:
                home_score = match['home_score']
                away_score = match['away_score']
                
                if match['home_guild_id'] == guild_id:
                    goals_for += home_score
                    goals_against += away_score
                    if home_score > away_score:
                        wins += 1
                    elif home_score < away_score:
                        losses += 1
                    else:
                        draws += 1
                elif match['away_guild_id'] == guild_id:
                    goals_for += away_score
                    goals_against += home_score
                    if away_score > home_score:
                        wins += 1
                    elif away_score < home_score:
                        losses += 1
                    else:
                        draws += 1
                
                recent_matches.append(dict(match))
            except Exception:
                continue
        
        result = {
            'team_name': team_name,
            'total_matches': total_matches,
            'wins': wins,
            'draws': draws,
            'losses': losses,
            'goals_for': goals_for,
            'goals_against': goals_against,
            'goal_difference': goals_for - goals_against,
            'recent_matches': recent_matches[:10]
        }
        self._cache.set(cache_key, result)
        return result

    async def backfill_match_team_links(
        self,
        teams: Optional[List[Dict[str, Any]]] = None,
        threshold: float = 0.8
    ) -> Dict[str, int]:
        """Backfill match_stats guild IDs by fuzzy matching team names."""
        if teams is None:
            team_rows = await self.pool.fetch(
                "SELECT guild_id, guild_name FROM IOSCA_TEAMS"
            )
            teams = [dict(row) for row in team_rows]

        # Build exact-name map from IOSCA_TEAMS
        exact_name_map: Dict[str, int] = {}
        for t in teams:
            name = (t.get("guild_name") or "").strip()
            if name and t.get("guild_id"):
                exact_name_map[name.lower()] = t["guild_id"]
                normalized = _normalize_team_name(name)
                if normalized:
                    exact_name_map[normalized] = t["guild_id"]

        # TEAM_NAME_ALIASES covers main-guild variants and any other team's
        # known name variants (see ios_bot/utils/match_importer.py, which
        # consolidated what used to be a hardcoded main-guild-only list into
        # this table -- this was a third copy of that same hardcoded list).
        try:
            alias_rows = await self.pool.fetch("SELECT alias_norm, guild_id FROM TEAM_NAME_ALIASES")
        except Exception as e:
            logger.warning(f"Failed to load team name aliases for backfill: {e}")
            alias_rows = []
        alias_map = {row["alias_norm"]: row["guild_id"] for row in alias_rows if row.get("guild_id") is not None}

        if not teams:
            return {
                'matches_scanned': 0,
                'home_linked': 0,
                'away_linked': 0,
                'matches_updated': 0
            }

        match_rows = await self.pool.fetch(
            """
            SELECT id, home_team_name, away_team_name
            FROM MATCH_STATS
            WHERE (home_guild_id IS NULL OR away_guild_id IS NULL)
              AND home_team_name IS NOT NULL
              AND away_team_name IS NOT NULL
            """
        )

        expects_text = await self._match_stats_guild_id_expects_text()
        matches_scanned = 0
        home_linked = 0
        away_linked = 0
        matches_updated = 0

        name_cache: Dict[str, Optional[int]] = {}

        # First pass: figure out what needs updating, purely in memory --
        # no DB calls in this loop. Previously this loop issued one
        # UPDATE per match (individually acquiring/releasing a pool
        # connection each time); now every row's update is collected here
        # and applied in a single batched executemany() below.
        pending_updates: List[tuple] = []
        for match in match_rows:
            matches_scanned += 1
            home_update_id = None
            away_update_id = None

            home_name = match.get('home_team_name')
            away_name = match.get('away_team_name')

            if home_name:
                home_name_norm = _normalize_team_name(home_name)
                if home_name_norm in alias_map:
                    home_update_id = alias_map[home_name_norm]
                else:
                    exact_id = exact_name_map.get(str(home_name).strip().lower()) or exact_name_map.get(home_name_norm)
                    if exact_id is not None:
                        home_update_id = exact_id
                    elif home_name not in name_cache:
                        best_home = find_best_match(home_name, teams, threshold)
                        name_cache[home_name] = best_home['guild_id'] if best_home else None
                    if home_update_id is None:
                        home_update_id = name_cache[home_name]

            if away_name:
                away_name_norm = _normalize_team_name(away_name)
                if away_name_norm in alias_map:
                    away_update_id = alias_map[away_name_norm]
                else:
                    exact_id = exact_name_map.get(str(away_name).strip().lower()) or exact_name_map.get(away_name_norm)
                    if exact_id is not None:
                        away_update_id = exact_id
                    elif away_name not in name_cache:
                        best_away = find_best_match(away_name, teams, threshold)
                        name_cache[away_name] = best_away['guild_id'] if best_away else None
                    if away_update_id is None:
                        away_update_id = name_cache[away_name]

            if home_update_id is None and away_update_id is None:
                continue

            pending_updates.append((
                str(home_update_id) if expects_text and home_update_id is not None else home_update_id,
                str(away_update_id) if expects_text and away_update_id is not None else away_update_id,
                match['id'],
            ))
            if home_update_id is not None:
                home_linked += 1
            if away_update_id is not None:
                away_linked += 1

        if pending_updates:
            query = (
                "UPDATE MATCH_STATS "
                "SET home_guild_id = COALESCE($1, home_guild_id), "
                "away_guild_id = COALESCE($2, away_guild_id) "
                "WHERE id = $3 AND (home_guild_id IS NULL OR away_guild_id IS NULL)"
            )
            try:
                async with self.pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.executemany(query, pending_updates)
                matches_updated = len(pending_updates)
            except Exception as e:
                logger.error(f"Failed to batch-update match team links: {e}")

        return {
            'matches_scanned': matches_scanned,
            'home_linked': home_linked,
            'away_linked': away_linked,
            'matches_updated': matches_updated
        }

    async def backfill_matches_for_team(
        self,
        guild_id: int,
        guild_name: str,
        threshold: float = 0.8
    ) -> Dict[str, int]:
        """Backfill match_stats for a single team using fuzzy matching."""
        return await self.backfill_match_team_links(
            teams=[{'guild_id': guild_id, 'guild_name': guild_name}],
            threshold=threshold
        )

    async def backfill_player_match_guild_ids(self, limit_matches: int = 0) -> Dict[str, int]:
        """Backfill PLAYER_MATCH_DATA.guild_id based on match lineups."""
        expects_text = await self._player_match_id_expects_text()
        match_join = "pmd.match_id = m.match_id" if expects_text else "pmd.match_id = m.id"
        query = """
        SELECT m.id, m.match_id, m.home_guild_id, m.away_guild_id, m.home_lineup, m.away_lineup
        FROM MATCH_STATS m
        WHERE m.home_guild_id IS NOT NULL
          AND m.away_guild_id IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM PLAYER_MATCH_DATA pmd
              WHERE {match_join} AND pmd.guild_id IS NULL
          )
        ORDER BY m.datetime DESC
        """
        query = query.replace("{match_join}", match_join)
        if limit_matches and limit_matches > 0:
            query += f" LIMIT {int(limit_matches)}"

        rows = await self.pool.fetch(query)
        matches_scanned = 0

        # Collect every match's home/away update in memory first -- this
        # used to issue up to 2 individual UPDATEs per match (each
        # independently acquiring/releasing a pool connection), which is
        # expensive when a repair run (/reevaluate_all_games) touches many
        # matches at once. Same query shape for home and away, so both
        # batch into one executemany() call.
        pending_updates: List[tuple] = []
        for row in rows:
            matches_scanned += 1
            home_lineup = row.get("home_lineup") or []
            away_lineup = row.get("away_lineup") or []

            try:
                if isinstance(home_lineup, str):
                    home_lineup = json.loads(home_lineup)
                if isinstance(away_lineup, str):
                    away_lineup = json.loads(away_lineup)
            except Exception:
                home_lineup = []
                away_lineup = []

            home_ids = [p.get("steam_id") for p in home_lineup if isinstance(p, dict) and p.get("steam_id")]
            away_ids = [p.get("steam_id") for p in away_lineup if isinstance(p, dict) and p.get("steam_id")]
            match_key = str(row["match_id"]) if expects_text else row["id"]

            if home_ids:
                pending_updates.append((row["home_guild_id"], match_key, home_ids))
            if away_ids:
                pending_updates.append((row["away_guild_id"], match_key, away_ids))

        players_updated = 0
        if pending_updates:
            update_query = """
                UPDATE PLAYER_MATCH_DATA
                SET guild_id = $1
                WHERE match_id = $2
                  AND guild_id IS NULL
                  AND steam_id = ANY($3::text[])
            """
            try:
                async with self.pool.acquire() as conn:
                    async with conn.transaction():
                        before = await conn.fetchval("SELECT count(*) FROM PLAYER_MATCH_DATA WHERE guild_id IS NULL")
                        await conn.executemany(update_query, pending_updates)
                        after = await conn.fetchval("SELECT count(*) FROM PLAYER_MATCH_DATA WHERE guild_id IS NULL")
                players_updated = max(0, before - after)
            except Exception as e:
                logger.error(f"Failed to batch-update player_match_data guild_ids: {e}")

        return {
            "matches_scanned": matches_scanned,
            "players_updated": players_updated
        }

    async def upsert_player_event_timestamps_for_match(
        self,
        match_id_key: str,
        player_event_timestamps: Dict[str, Dict[str, List[int]]],
        source_filename: Optional[str] = None,
        overwrite: bool = False,
    ) -> Dict[str, int]:
        """Backfill/merge event_timestamps for one match using canonical match_id string."""
        if not player_event_timestamps:
            return {"players_considered": 0, "rows_updated": 0}

        has_column = await self._player_match_has_event_timestamps_column()
        if not has_column:
            return {"players_considered": len(player_event_timestamps), "rows_updated": 0}

        lookup_row = await self.pool.fetchrow(
            "SELECT id, match_id FROM MATCH_STATS WHERE match_id = $1 LIMIT 1",
            str(match_id_key),
        )
        if not lookup_row and source_filename:
            try:
                lookup_row = await self.pool.fetchrow(
                    """
                    SELECT id, match_id
                    FROM MATCH_STATS
                    WHERE source_filename = $1
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    str(source_filename),
                )
            except Exception:
                lookup_row = None

        match_candidates: List[str] = []
        if lookup_row:
            if lookup_row.get("match_id") is not None:
                match_candidates.append(str(lookup_row.get("match_id")))
            if lookup_row.get("id") is not None:
                match_candidates.append(str(lookup_row.get("id")))
        if match_id_key is not None:
            match_candidates.append(str(match_id_key))

        # Keep order and uniqueness.
        match_candidates = list(dict.fromkeys([m for m in match_candidates if str(m).strip()]))
        if not match_candidates:
            return {"players_considered": len(player_event_timestamps), "rows_updated": 0}

        if overwrite:
            query = """
            UPDATE PLAYER_MATCH_DATA
            SET event_timestamps = $3::jsonb
            WHERE match_id::text = ANY($1::text[])
              AND steam_id = $2
            """
        else:
            query = """
            UPDATE PLAYER_MATCH_DATA
            SET event_timestamps = COALESCE(event_timestamps, '{}'::jsonb) || $3::jsonb
            WHERE match_id::text = ANY($1::text[])
              AND steam_id = $2
            """

        # Collect one row per player first (pure in-memory), then apply all
        # of them in a single executemany() -- this used to be one UPDATE
        # per player (10-16+ individual pool acquires for a typical match),
        # multiplied across every match when /reevaluate_all_games runs.
        pending_updates: List[tuple] = []
        for steam_id, event_map in player_event_timestamps.items():
            if not steam_id:
                continue
            if not isinstance(event_map, dict):
                continue
            cleaned = {}
            for event_name, minutes in event_map.items():
                if not isinstance(minutes, list):
                    continue
                vals = []
                for minute in minutes:
                    try:
                        vals.append(int(minute))
                    except Exception:
                        continue
                if vals:
                    cleaned[str(event_name)] = vals
            if not cleaned:
                continue
            pending_updates.append((match_candidates, str(steam_id), json.dumps(cleaned)))

        rows_updated = 0
        if pending_updates:
            try:
                async with self.pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.executemany(query, pending_updates)
                rows_updated = len(pending_updates)
            except Exception as e:
                logger.error(
                    f"Failed to batch-backfill event_timestamps for match {match_id_key}: {e}"
                )

        return {
            "players_considered": len(player_event_timestamps),
            "rows_updated": rows_updated,
        }
    
    async def add_active_match(self, channel_id: int, team1_name: str, team2_name: str) -> bool:
        return await self.add_active_match_context(
            primary_channel_id=channel_id,
            team1_name=team1_name,
            team2_name=team2_name,
        )

    async def _ensure_active_match_context_table(self) -> None:
        if self._active_match_context_ready:
            return

        await self.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS ACTIVE_MATCH_CONTEXTS (
                id BIGSERIAL PRIMARY KEY,
                primary_channel_id BIGINT NOT NULL,
                secondary_channel_id BIGINT,
                team1_name VARCHAR(255) NOT NULL,
                team2_name VARCHAR(255) NOT NULL,
                team1_name_norm TEXT NOT NULL,
                team2_name_norm TEXT NOT NULL,
                team1_guild_id BIGINT,
                team2_guild_id BIGINT,
                game_type VARCHAR(10),
                source_kind VARCHAR(32) NOT NULL DEFAULT 'standard',
                tournament_id INTEGER,
                fixture_id INTEGER,
                schedule_id INTEGER,
                match_stats_id INTEGER REFERENCES MATCH_STATS(id) ON DELETE SET NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                finished_at TIMESTAMP
            )
            """
        )
        await self.pool.execute(
            """
            ALTER TABLE ACTIVE_MATCH_CONTEXTS
            ADD COLUMN IF NOT EXISTS tournament_id INTEGER
            """
        )
        await self.pool.execute(
            """
            ALTER TABLE ACTIVE_MATCH_CONTEXTS
            ADD COLUMN IF NOT EXISTS fixture_id INTEGER
            """
        )
        await self.pool.execute(
            """
            ALTER TABLE ACTIVE_MATCH_CONTEXTS
            ADD COLUMN IF NOT EXISTS schedule_id INTEGER
            """
        )
        await self.pool.execute(
            """
            ALTER TABLE ACTIVE_MATCH_CONTEXTS
            ADD COLUMN IF NOT EXISTS game_server_id INTEGER REFERENCES IOS_SERVERS(id) ON DELETE SET NULL
            """
        )
        await self.pool.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_active_match_contexts_server
            ON ACTIVE_MATCH_CONTEXTS(game_server_id, created_at DESC)
            WHERE finished_at IS NULL
            """
        )
        await self.pool.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_active_match_contexts_open_created
            ON ACTIVE_MATCH_CONTEXTS(created_at DESC)
            WHERE finished_at IS NULL
            """
        )
        await self.pool.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_active_match_contexts_name_pair
            ON ACTIVE_MATCH_CONTEXTS(team1_name_norm, team2_name_norm)
            """
        )
        await self.pool.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_active_match_contexts_guild_pair
            ON ACTIVE_MATCH_CONTEXTS(team1_guild_id, team2_guild_id)
            """
        )
        await self.pool.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_active_match_contexts_tournament_fixture
            ON ACTIVE_MATCH_CONTEXTS(tournament_id, fixture_id)
            """
        )
        self._active_match_context_ready = True

    async def add_active_match_context(
        self,
        primary_channel_id: int,
        team1_name: str,
        team2_name: str,
        *,
        secondary_channel_id: Optional[int] = None,
        team1_guild_id: Optional[int] = None,
        team2_guild_id: Optional[int] = None,
        game_type: Optional[str] = None,
        source_kind: str = "standard",
        tournament_id: Optional[int] = None,
        fixture_id: Optional[int] = None,
        schedule_id: Optional[int] = None,
        game_server_id: Optional[int] = None,
    ) -> bool:
        try:
            await self._ensure_active_match_context_table()
            await self.pool.execute(
                """
                INSERT INTO ACTIVE_MATCH_CONTEXTS (
                    primary_channel_id,
                    secondary_channel_id,
                    team1_name,
                    team2_name,
                    team1_name_norm,
                    team2_name_norm,
                    team1_guild_id,
                    team2_guild_id,
                    game_type,
                    source_kind,
                    tournament_id,
                    fixture_id,
                    schedule_id,
                    game_server_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                """,
                int(primary_channel_id),
                int(secondary_channel_id) if secondary_channel_id else None,
                str(team1_name or "").strip() or "Team 1",
                str(team2_name or "").strip() or "Team 2",
                _normalize_team_name(team1_name),
                _normalize_team_name(team2_name),
                int(team1_guild_id) if team1_guild_id is not None else None,
                int(team2_guild_id) if team2_guild_id is not None else None,
                str(game_type or "").strip() or None,
                str(source_kind or "standard").strip() or "standard",
                int(tournament_id) if tournament_id is not None else None,
                int(fixture_id) if fixture_id is not None else None,
                int(schedule_id) if schedule_id is not None else None,
                int(game_server_id) if game_server_id is not None else None,
            )
            logger.info(
                "Active match context logged: %s vs %s -> %s%s (source=%s tournament=%s fixture=%s schedule=%s)",
                team1_name,
                team2_name,
                primary_channel_id,
                f", {secondary_channel_id}" if secondary_channel_id else "",
                str(source_kind or "standard").strip() or "standard",
                tournament_id,
                fixture_id,
                schedule_id,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to log active match context: {e}")
            return False

    @staticmethod
    def _active_match_context_score(
        row: Dict[str, Any],
        *,
        home_name_norm: str,
        away_name_norm: str,
        home_guild_id: Optional[int],
        away_guild_id: Optional[int],
        game_type: Optional[str],
        game_server_id: Optional[int] = None,
    ) -> int:
        score = 0

        # Server identity is the strongest signal available: only one match
        # can actually be running on a given rented server at a time, so a
        # server match dominates team-name/guild scoring rather than just
        # adding to it -- this is what makes linking deterministic instead of
        # a fuzzy-name guess.
        row_server_id = row.get("game_server_id")
        if game_server_id is not None and row_server_id is not None and int(row_server_id) == int(game_server_id):
            score += 50

        row_team_norms = {
            str(row.get("team1_name_norm") or ""),
            str(row.get("team2_name_norm") or ""),
        }
        match_team_norms = {home_name_norm, away_name_norm}
        if home_name_norm and away_name_norm and row_team_norms == match_team_norms:
            score += 5

        row_guilds = {
            int(gid)
            for gid in (row.get("team1_guild_id"), row.get("team2_guild_id"))
            if gid is not None
        }
        match_guilds = {
            int(gid)
            for gid in (home_guild_id, away_guild_id)
            if gid is not None
        }
        if row_guilds and match_guilds and row_guilds == match_guilds:
            score += 7

        row_game_type = str(row.get("game_type") or "").strip().lower()
        match_game_type = str(game_type or "").strip().lower()
        if row_game_type and match_game_type and row_game_type == match_game_type:
            score += 1

        return score

    async def has_recent_open_match_context(self, within_hours: float = 3.0) -> bool:
        """True if a /ready was triggered recently and hasn't yet been
        matched to a completed import (finished_at IS NULL) -- i.e. someone
        is very likely mid-match right now. Used to drive adaptive SFTP
        polling: poll fast while this is true, back off when it's false.
        A tighter window than resolve_active_match_context's 24-hour lookup
        on purpose -- IOSoccer matches don't run for hours, so a context
        still open past a few hours is almost certainly abandoned/stale,
        not a sign of live activity.
        """
        try:
            await self._ensure_active_match_context_table()
            return bool(await self.pool.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1 FROM ACTIVE_MATCH_CONTEXTS
                    WHERE finished_at IS NULL
                      AND created_at >= NOW() - ($1::text || ' hours')::interval
                )
                """,
                str(within_hours),
            ))
        except Exception as e:
            logger.error(f"Failed to check for open match contexts: {e}")
            return True  # fail open -- prefer an unnecessary fast poll over missing a live match

    async def resolve_active_match_context(
        self,
        *,
        home_team_name: str,
        away_team_name: str,
        home_guild_id: Optional[int] = None,
        away_guild_id: Optional[int] = None,
        game_type: Optional[str] = None,
        source_kind: Optional[str] = None,
        require_tournament_fixture: bool = False,
        game_server_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            await self._ensure_active_match_context_table()
            rows = await self.pool.fetch(
                """
                SELECT *
                FROM ACTIVE_MATCH_CONTEXTS
                WHERE finished_at IS NULL
                  AND created_at >= NOW() - INTERVAL '24 hours'
                  AND ($1::TEXT IS NULL OR source_kind = $1)
                  AND (
                        $2::BOOLEAN = FALSE
                     OR (
                            tournament_id IS NOT NULL
                        AND fixture_id IS NOT NULL
                     )
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT 100
                """,
                str(source_kind or "").strip() or None,
                bool(require_tournament_fixture),
            )
        except Exception as e:
            logger.error(f"Failed to fetch active match contexts: {e}")
            return None

        home_name_norm = _normalize_team_name(home_team_name)
        away_name_norm = _normalize_team_name(away_team_name)
        best_row: Optional[Dict[str, Any]] = None
        best_score = 0

        for raw_row in rows:
            row = dict(raw_row)
            score = self._active_match_context_score(
                row,
                home_name_norm=home_name_norm,
                away_name_norm=away_name_norm,
                home_guild_id=home_guild_id,
                away_guild_id=away_guild_id,
                game_type=game_type,
                game_server_id=game_server_id,
            )
            if score > best_score:
                best_score = score
                best_row = row

        if not best_row or best_score <= 0:
            return None
        return best_row

    async def get_open_active_match_context_for_channel(self, channel_id: int) -> Optional[Dict[str, Any]]:
        """The most recent still-open (finished_at IS NULL, i.e. not yet
        matched to a completed import) active match context touching this
        channel as either side. Used on bot restart to check whether a
        lineup that was locked when the bot went down is still genuinely
        backed by a match the bot is tracking, versus a stale lock left over
        from a match that already finished or never really started.
        """
        try:
            await self._ensure_active_match_context_table()
            row = await self.pool.fetchrow(
                """
                SELECT *
                FROM ACTIVE_MATCH_CONTEXTS
                WHERE finished_at IS NULL
                  AND (primary_channel_id = $1 OR secondary_channel_id = $1)
                ORDER BY created_at DESC
                LIMIT 1
                """,
                int(channel_id),
            )
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to fetch open active match context for channel {channel_id}: {e}")
            return None

    async def resolve_active_match_announcement_channels(
        self,
        *,
        match_stats_id: int,
        home_team_name: str,
        away_team_name: str,
        home_guild_id: Optional[int] = None,
        away_guild_id: Optional[int] = None,
        game_type: Optional[str] = None,
        game_server_id: Optional[int] = None,
    ) -> list[int]:
        best_row = await self.resolve_active_match_context(
            home_team_name=home_team_name,
            away_team_name=away_team_name,
            home_guild_id=home_guild_id,
            away_guild_id=away_guild_id,
            game_type=game_type,
            game_server_id=game_server_id,
        )
        if not best_row:
            return []

        channel_ids: list[int] = []
        for value in [best_row.get("primary_channel_id"), best_row.get("secondary_channel_id")]:
            if value is None:
                continue
            try:
                channel_id = int(value)
            except Exception:
                continue
            if channel_id not in channel_ids:
                channel_ids.append(channel_id)

        try:
            await self.pool.execute(
                """
                UPDATE ACTIVE_MATCH_CONTEXTS
                SET match_stats_id = $1,
                    finished_at = NOW()
                WHERE id = $2
                """,
                int(match_stats_id),
                int(best_row["id"]),
            )
        except Exception as e:
            logger.error(f"Failed to mark active match context complete: {e}")

        return channel_ids

    async def _ensure_challenge_state_table(self) -> None:
        """Backing store for ios_bot.challenge_manager.active_challenges,
        which otherwise lives only as an in-process dict and loses every
        pending/accepted/starting challenge on a bot restart (unlike
        lineups, which already persist via TEAM_LINEUPS). Challenges are a
        low-frequency event (issue/accept/decline/cancel/start), not a
        per-click hot path like lineup refreshes, so writing on every
        transition here is fine -- no debouncing needed."""
        if self._challenge_state_ready:
            return
        await self.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS CHALLENGE_STATE (
                challenge_id TEXT PRIMARY KEY,
                payload JSONB NOT NULL,
                updated_at TIMESTAMP DEFAULT NOW()
            )
            """
        )
        self._challenge_state_ready = True

    async def save_challenge_state(self, challenge_id: str, challenge_data: dict) -> bool:
        """Upsert one challenge's full state. Call this after any mutation
        (issued/accepted/declined/cancelled/starting/resolved)."""
        try:
            await self._ensure_challenge_state_table()
            await self.pool.execute(
                """
                INSERT INTO CHALLENGE_STATE (challenge_id, payload, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (challenge_id) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    updated_at = NOW()
                """,
                str(challenge_id),
                json.dumps(challenge_data, default=str),
            )
            return True
        except Exception as e:
            logger.error(f"Failed to save challenge state {challenge_id}: {e}")
            return False

    async def delete_challenge_state(self, challenge_id: str) -> bool:
        """Remove a challenge once it's resolved (accepted-and-started,
        declined, cancelled, or expired) so it isn't reloaded on next boot."""
        try:
            await self._ensure_challenge_state_table()
            await self.pool.execute("DELETE FROM CHALLENGE_STATE WHERE challenge_id = $1", str(challenge_id))
            return True
        except Exception as e:
            logger.error(f"Failed to delete challenge state {challenge_id}: {e}")
            return False

    async def load_all_challenge_states(self) -> Dict[str, Any]:
        """Load every persisted challenge, for restoring active_challenges
        into memory on bot startup."""
        try:
            await self._ensure_challenge_state_table()
            rows = await self.pool.fetch("SELECT challenge_id, payload FROM CHALLENGE_STATE")
        except Exception as e:
            logger.error(f"Failed to load challenge states: {e}")
            return {}

        result = {}
        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (json.JSONDecodeError, TypeError):
                    continue
            result[row["challenge_id"]] = payload
        return result

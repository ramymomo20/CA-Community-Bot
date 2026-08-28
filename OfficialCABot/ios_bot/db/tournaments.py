"""
Tournament operations for PostgreSQL database
"""

import json
import logging
import os
import re
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone
from .connection import DatabasePool
from .utils import find_best_match, normalize_team_name

logger = logging.getLogger(__name__)

TOURNAMENT_AUTO_SYNC_REQUIRE_CONFIRMED_SCHEDULE = (
    os.getenv("TOURNAMENT_AUTO_SYNC_REQUIRE_CONFIRMED_SCHEDULE", "1").lower()
    in ("1", "true", "yes", "on")
)
TOURNAMENT_AUTO_SYNC_MATCH_WINDOW_HOURS = max(
    1,
    int(os.getenv("TOURNAMENT_AUTO_SYNC_MATCH_WINDOW_HOURS", "18") or 18),
)
DEFAULT_TOURNAMENT_LEAGUE_KEY = "A"
SUPPORTED_TOURNAMENT_LEAGUES = ("A", "B")
DEFAULT_TOURNAMENT_STAGE = "league"
SUPPORTED_TOURNAMENT_STAGES = (
    "league",
    "play_in",
    "quarterfinal",
    "semifinal",
    "third_place",
    "final",
    "consolation",
)


def _normalize_steam_id_key(steam_id: Any) -> str:
    """Normalize Steam IDs to legacy format for cross-table joins."""
    if steam_id is None:
        return ""
    s = str(steam_id).strip()
    if not s:
        return ""

    s_upper = s.upper()
    if s_upper.startswith("STEAM_"):
        parts = s.split(":")
        if len(parts) == 3:
            return f"STEAM_0:{parts[1]}:{parts[2]}"
        return s

    if s.startswith("[") and s.endswith("]") and ":" in s:
        try:
            account3 = int(s.split(":")[-1].strip("]"))
            acct_type = account3 % 2
            acct_num = (account3 - acct_type) // 2
            return f"STEAM_0:{acct_type}:{acct_num}"
        except Exception:
            return s

    if s.isdigit() and len(s) >= 16:
        try:
            sid64 = int(s)
            offset = sid64 - 76561197960265728
            acct_type = offset % 2
            acct_num = (offset - acct_type) // 2
            return f"STEAM_0:{acct_type}:{acct_num}"
        except Exception:
            return s

    return s


def _steam_id_aliases(steam_id: Any) -> List[str]:
    """Return comparable Steam ID aliases across legacy, 64-bit, and SteamID3 forms."""
    raw = str(steam_id or "").strip()
    if not raw:
        return []

    aliases: List[str] = []

    def _add(value: str) -> None:
        v = str(value or "").strip()
        if v and v not in aliases:
            aliases.append(v)

    _add(raw)
    legacy = _normalize_steam_id_key(raw)
    _add(legacy)

    if legacy.upper().startswith("STEAM_"):
        parts = legacy.split(":")
        if len(parts) == 3:
            try:
                acct_type = int(parts[1])
                acct_num = int(parts[2])
                account_id = acct_num * 2 + acct_type
                _add(f"[U:1:{account_id}]")
                _add(str(account_id + 76561197960265728))
            except Exception:
                pass

    return aliases


def _looks_like_steam_id(value: Any) -> bool:
    """Best-effort check to avoid showing raw Steam IDs as display names."""
    raw = str(value or "").strip()
    if not raw:
        return False
    upper = raw.upper()
    if upper.startswith("STEAM_"):
        return True
    if raw.startswith("[") and raw.endswith("]") and raw.upper().startswith("[U:"):
        return True
    return raw.isdigit() and len(raw) >= 16


def _normalize_tournament_league_key(value: Any, default: str = DEFAULT_TOURNAMENT_LEAGUE_KEY) -> str:
    raw = str(value or "").strip().upper()
    if raw in SUPPORTED_TOURNAMENT_LEAGUES:
        return raw
    if raw == "1":
        return "A"
    if raw == "2":
        return "B"
    return default


def _normalize_tournament_stage(value: Any, default: str = DEFAULT_TOURNAMENT_STAGE) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return default

    direct = raw.replace(" ", "_").replace("-", "_")
    if direct in SUPPORTED_TOURNAMENT_STAGES:
        return direct

    collapsed = re.sub(r"[^a-z0-9]+", "", raw)
    stage_aliases = {
        "league": "league",
        "playin": "play_in",
        "quarterfinal": "quarterfinal",
        "quarterfinals": "quarterfinal",
        "semifinal": "semifinal",
        "semifinals": "semifinal",
        "thirdplace": "third_place",
        "thirdplacegame": "third_place",
        "final": "final",
        "finals": "final",
        "consolation": "consolation",
    }
    return stage_aliases.get(collapsed, default)


def _is_league_stage(value: Any) -> bool:
    return _normalize_tournament_stage(value) == DEFAULT_TOURNAMENT_STAGE


def _default_stage_source(stage_type: Any, bracket_slot: Any = None) -> str:
    normalized = _normalize_tournament_stage(stage_type)
    slot = max(1, int(bracket_slot or 1))
    prefix_map = {
        "play_in": "PI",
        "quarterfinal": "QF",
        "semifinal": "SF",
        "third_place": "TP",
        "final": "F",
        "consolation": "CN",
        "league": "GW",
    }
    prefix = prefix_map.get(normalized, "FX")
    if normalized in {"third_place", "final", "consolation"}:
        return prefix
    return f"{prefix}{slot}"


class TournamentOperations:
    """Handles all tournament-related database operations"""

    def __init__(self, pool: DatabasePool):
        self.pool = pool
        self._player_match_id_is_text = None
        self._iosca_players_name_column = None
        self._has_tournament_player_stats_table = None
        self._tournament_league_schema_ready = None

    async def _ensure_tournament_league_schema(self) -> None:
        if self._tournament_league_schema_ready:
            return

        try:
            await self.pool.execute(
                """
                ALTER TABLE TOURNAMENTS
                ADD COLUMN IF NOT EXISTS league_count INTEGER NOT NULL DEFAULT 1
                """
            )
            await self.pool.execute(
                """
                UPDATE TOURNAMENTS
                SET league_count = 1
                WHERE league_count IS NULL OR league_count NOT IN (1, 2)
                """
            )
            await self.pool.execute(
                """
                ALTER TABLE TOURNAMENT_TEAMS
                ADD COLUMN IF NOT EXISTS league_key VARCHAR(1) NOT NULL DEFAULT 'A'
                """
            )
            await self.pool.execute(
                """
                UPDATE TOURNAMENT_TEAMS
                SET league_key = 'A'
                WHERE league_key IS NULL OR league_key NOT IN ('A', 'B')
                """
            )
            await self.pool.execute(
                """
                ALTER TABLE TOURNAMENT_FIXTURES
                ADD COLUMN IF NOT EXISTS league_key VARCHAR(1) NOT NULL DEFAULT 'A'
                """
            )
            await self.pool.execute(
                """
                UPDATE TOURNAMENT_FIXTURES
                SET league_key = 'A'
                WHERE league_key IS NULL OR league_key NOT IN ('A', 'B')
                """
            )
            await self.pool.execute(
                """
                ALTER TABLE TOURNAMENT_FIXTURES
                ADD COLUMN IF NOT EXISTS stage_type VARCHAR(32) NOT NULL DEFAULT 'league'
                """
            )
            await self.pool.execute(
                """
                ALTER TABLE TOURNAMENT_FIXTURES
                ADD COLUMN IF NOT EXISTS round_number INTEGER
                """
            )
            await self.pool.execute(
                """
                ALTER TABLE TOURNAMENT_FIXTURES
                ADD COLUMN IF NOT EXISTS bracket_slot INTEGER
                """
            )
            await self.pool.execute(
                """
                ALTER TABLE TOURNAMENT_FIXTURES
                ADD COLUMN IF NOT EXISTS home_source TEXT
                """
            )
            await self.pool.execute(
                """
                ALTER TABLE TOURNAMENT_FIXTURES
                ADD COLUMN IF NOT EXISTS away_source TEXT
                """
            )
            await self.pool.execute(
                """
                ALTER TABLE TOURNAMENT_FIXTURES
                ADD COLUMN IF NOT EXISTS winner_guild_id BIGINT
                """
            )
            await self.pool.execute(
                """
                ALTER TABLE TOURNAMENT_FIXTURES
                ADD COLUMN IF NOT EXISTS winner_to_fixture_id INTEGER
                """
            )
            await self.pool.execute(
                """
                ALTER TABLE TOURNAMENT_FIXTURES
                ADD COLUMN IF NOT EXISTS loser_to_fixture_id INTEGER
                """
            )
            await self.pool.execute(
                """
                UPDATE TOURNAMENT_FIXTURES
                SET stage_type = 'league'
                WHERE stage_type IS NULL OR btrim(stage_type) = ''
                """
            )
            await self.pool.execute(
                """
                UPDATE TOURNAMENT_FIXTURES
                SET stage_type = CASE
                    WHEN regexp_replace(lower(stage_type), '[^a-z0-9]+', '', 'g') = 'playin' THEN 'play_in'
                    WHEN regexp_replace(lower(stage_type), '[^a-z0-9]+', '', 'g') IN ('quarterfinal', 'quarterfinals') THEN 'quarterfinal'
                    WHEN regexp_replace(lower(stage_type), '[^a-z0-9]+', '', 'g') IN ('semifinal', 'semifinals') THEN 'semifinal'
                    WHEN regexp_replace(lower(stage_type), '[^a-z0-9]+', '', 'g') IN ('thirdplace', 'thirdplacegame') THEN 'third_place'
                    WHEN regexp_replace(lower(stage_type), '[^a-z0-9]+', '', 'g') IN ('final', 'finals') THEN 'final'
                    WHEN regexp_replace(lower(stage_type), '[^a-z0-9]+', '', 'g') = 'consolation' THEN 'consolation'
                    ELSE stage_type
                END
                WHERE stage_type IS NOT NULL
                  AND btrim(stage_type) <> ''
                """
            )
            await self.pool.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tournament_teams_league
                ON TOURNAMENT_TEAMS(tournament_id, league_key)
                """
            )
            await self.pool.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tournament_fixtures_league_week
                ON TOURNAMENT_FIXTURES(tournament_id, league_key, week_number)
                """
            )
            await self.pool.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tournament_fixtures_stage_round
                ON TOURNAMENT_FIXTURES(tournament_id, stage_type, round_number, bracket_slot, id)
                """
            )
            self._tournament_league_schema_ready = True
        except Exception as e:
            logger.error(f"Failed to ensure tournament league schema: {e}")
            raise

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

    async def _get_iosca_players_name_column(self) -> Optional[str]:
        """Detect preferred display-name column on IOSCA_PLAYERS across schema variants."""
        if self._iosca_players_name_column is not None:
            return self._iosca_players_name_column

        try:
            rows = await self.pool.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'iosca_players'
                  AND column_name IN ('discord_name', 'username')
                """
            )
            columns = {str(r.get("column_name")) for r in rows}
            if "discord_name" in columns:
                self._iosca_players_name_column = "discord_name"
            elif "username" in columns:
                self._iosca_players_name_column = "username"
            else:
                self._iosca_players_name_column = ""
        except Exception as e:
            logger.error(f"Failed to detect IOSCA_PLAYERS name column: {e}")
            self._iosca_players_name_column = ""

        return self._iosca_players_name_column or None

    async def _has_tournament_player_stats(self) -> bool:
        if self._has_tournament_player_stats_table is not None:
            return self._has_tournament_player_stats_table

        try:
            row = await self.pool.fetchrow(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'tournament_player_stats'
                """
            )
            self._has_tournament_player_stats_table = bool(row)
        except Exception as e:
            logger.error(f"Failed to detect TOURNAMENT_PLAYER_STATS table: {e}")
            self._has_tournament_player_stats_table = False

        return self._has_tournament_player_stats_table

    async def create_tournament(
        self,
        name: str,
        format: str,
        num_teams: int,
        created_by: Optional[int] = None,
        points_win: int = 3,
        points_draw: int = 1,
        points_loss: int = 0,
        league_count: int = 1,
    ) -> Optional[int]:
        await self._ensure_tournament_league_schema()
        league_count = 2 if int(league_count or 1) == 2 else 1
        query = """
        INSERT INTO TOURNAMENTS
            (name, format, num_teams, created_by, points_win, points_draw, points_loss, league_count)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id
        """
        try:
            return await self.pool.fetchval(
                query,
                name,
                format,
                num_teams,
                created_by,
                points_win,
                points_draw,
                points_loss,
                league_count,
            )
        except Exception as e:
            logger.error(f"Failed to create tournament: {e}")
            return None

    async def list_tournaments(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        await self._ensure_tournament_league_schema()
        if status:
            query = "SELECT * FROM TOURNAMENTS WHERE status = $1 ORDER BY created_at DESC"
            rows = await self.pool.fetch(query, status)
        else:
            query = "SELECT * FROM TOURNAMENTS ORDER BY created_at DESC"
            rows = await self.pool.fetch(query)
        return [dict(r) for r in rows]

    async def get_tournament(self, tournament_id: int) -> Optional[Dict[str, Any]]:
        await self._ensure_tournament_league_schema()
        row = await self.pool.fetchrow("SELECT * FROM TOURNAMENTS WHERE id = $1", tournament_id)
        return dict(row) if row else None

    async def end_tournament(self, tournament_id: int) -> bool:
        try:
            await self.pool.execute(
                "UPDATE TOURNAMENTS SET status = 'ended' WHERE id = $1",
                tournament_id
            )
            return True
        except Exception as e:
            logger.error(f"Failed to end tournament {tournament_id}: {e}")
            return False

    async def delete_tournament(self, tournament_id: int) -> bool:
        try:
            await self.pool.execute("DELETE FROM TOURNAMENTS WHERE id = $1", tournament_id)
            return True
        except Exception as e:
            logger.error(f"Failed to delete tournament {tournament_id}: {e}")
            return False

    async def add_teams(self, tournament_id: int, guild_ids: List[int], league_key: Optional[str] = None) -> int:
        """Add teams to tournament. Returns count added."""
        await self._ensure_tournament_league_schema()
        if not guild_ids:
            return 0

        tournament = await self.get_tournament(tournament_id)
        effective_league = _normalize_tournament_league_key(league_key)
        if tournament and int(tournament.get("league_count") or 1) <= 1:
            effective_league = DEFAULT_TOURNAMENT_LEAGUE_KEY

        teams_query = """
        SELECT guild_id, guild_name, guild_icon
        FROM IOSCA_TEAMS
        WHERE guild_id = ANY($1::bigint[])
        """
        teams = await self.pool.fetch(teams_query, guild_ids)
        if not teams:
            return 0

        # Batch both inserts across all teams instead of 2 individual
        # round trips per team (each independently acquiring a pool
        # connection) -- tournament setup can add a dozen-plus teams at once.
        team_params = [
            (tournament_id, team["guild_id"], team["guild_name"], team.get("guild_icon"), effective_league)
            for team in teams
        ]

        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    before = await conn.fetchval(
                        "SELECT count(*) FROM TOURNAMENT_TEAMS WHERE tournament_id = $1", tournament_id
                    )
                    await conn.executemany(
                        """
                        INSERT INTO TOURNAMENT_TEAMS
                            (tournament_id, guild_id, team_name_snapshot, team_icon_snapshot, league_key)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (tournament_id, guild_id) DO NOTHING
                        """,
                        team_params,
                    )
                    after = await conn.fetchval(
                        "SELECT count(*) FROM TOURNAMENT_TEAMS WHERE tournament_id = $1", tournament_id
                    )
            return max(0, after - before)
        except Exception as e:
            logger.error(f"Failed to batch-add teams to tournament {tournament_id}: {e}")
            return 0

    async def get_tournament_teams(self, tournament_id: int) -> List[Dict[str, Any]]:
        await self._ensure_tournament_league_schema()
        rows = await self.pool.fetch(
            """
            SELECT * FROM TOURNAMENT_TEAMS
            WHERE tournament_id = $1
            ORDER BY league_key ASC, team_name_snapshot ASC
            """,
            tournament_id
        )
        return [dict(r) for r in rows]

    async def get_tournament_team_ids(self, tournament_id: int, league_key: Optional[str] = None) -> List[int]:
        await self._ensure_tournament_league_schema()
        if league_key:
            rows = await self.pool.fetch(
                """
                SELECT guild_id
                FROM TOURNAMENT_TEAMS
                WHERE tournament_id = $1
                  AND guild_id IS NOT NULL
                  AND league_key = $2
                """,
                tournament_id,
                _normalize_tournament_league_key(league_key),
            )
        else:
            rows = await self.pool.fetch(
                "SELECT guild_id FROM TOURNAMENT_TEAMS WHERE tournament_id = $1 AND guild_id IS NOT NULL",
                tournament_id
            )
        return [r["guild_id"] for r in rows]

    async def _get_tournament_team_name_map(self, tournament_id: int) -> List[Dict[str, Any]]:
        await self._ensure_tournament_league_schema()
        try:
            rows = await self.pool.fetch(
                """
                SELECT tt.guild_id,
                       tt.team_name_snapshot as guild_name,
                       tt.league_key,
                       t.nicknames
                FROM TOURNAMENT_TEAMS tt
                LEFT JOIN IOSCA_TEAMS t
                  ON tt.guild_id = t.guild_id
                WHERE tt.tournament_id = $1
                """,
                tournament_id
            )
        except Exception:
            rows = await self.pool.fetch(
                """
                SELECT tt.guild_id,
                       tt.team_name_snapshot as guild_name,
                       tt.league_key
                FROM TOURNAMENT_TEAMS tt
                WHERE tt.tournament_id = $1
                """,
                tournament_id
            )
        candidates: List[Dict[str, Any]] = []
        for r in rows:
            data = dict(r)
            candidates.append({
                "guild_id": data["guild_id"],
                "guild_name": data.get("guild_name"),
                "league_key": _normalize_tournament_league_key(data.get("league_key")),
            })
            try:
                nicknames = data.get("nicknames")
                if isinstance(nicknames, str):
                    nicknames = json.loads(nicknames)
                if isinstance(nicknames, list):
                    for name in nicknames:
                        if name:
                            candidates.append({
                                "guild_id": data["guild_id"],
                                "guild_name": name,
                                "league_key": _normalize_tournament_league_key(data.get("league_key")),
                            })
            except Exception:
                continue
        return candidates

    def _resolve_team_id_by_name(
        self,
        match_name: Optional[str],
        team_ids: List[int],
        team_name_map: List[Dict[str, Any]],
        threshold: float = 0.8,
        league_key: Optional[str] = None,
    ) -> Optional[int]:
        """Resolve a team id using substring and fuzzy matching."""
        if not match_name:
            return None

        effective_league = _normalize_tournament_league_key(league_key) if league_key else None

        match_name_l = match_name.lower()
        best_sub = None
        best_len = 0

        for candidate in team_name_map:
            cand_id = candidate.get("guild_id")
            cand_name = candidate.get("guild_name")
            if effective_league and _normalize_tournament_league_key(candidate.get("league_key")) != effective_league:
                continue
            if cand_id not in team_ids or not cand_name:
                continue
            cand_l = cand_name.lower()
            if cand_l and cand_l in match_name_l:
                if len(cand_l) > best_len:
                    best_len = len(cand_l)
                    best_sub = cand_id

        if best_sub is not None:
            return best_sub

        candidate_pool = [
            item for item in team_name_map
            if item.get("guild_id") in team_ids
            and (not effective_league or _normalize_tournament_league_key(item.get("league_key")) == effective_league)
        ]
        best = find_best_match(match_name, candidate_pool, threshold=threshold)
        if best:
            return best.get("guild_id")
        return None

    def _clean_fixture_text_line(self, line: str) -> str:
        # Strip emoji tokens like :emoji: and normalize common pasted punctuation.
        line = re.sub(r":[^:\s]+:", "", line)
        line = line.replace("：", ":").replace("–", "-").replace("—", "-")
        line = re.sub(r"^[\s>*•\-]+\s*", "", line)
        line = re.sub(r"\s+", " ", line)
        return line.strip()

    def _parse_fixtures_text(self, text: str) -> List[Dict[str, Any]]:
        """Parse league and knockout fixture text into normalized fixture rows."""
        fixtures: List[Dict[str, Any]] = []
        current_week = None
        current_label = None
        current_league_key = DEFAULT_TOURNAMENT_LEAGUE_KEY
        current_knockout_header: Dict[str, Any] | None = None
        stage_pattern = (
            r"play[\s_-]?in|quarter[\s_-]?finals?|semi[\s_-]?finals?|"
            r"third[\s_-]?place(?:\s+game)?|finals?|consolation"
        )
        knockout_inline_re = re.compile(
            rf"^(?P<label>(?P<stage>{stage_pattern})(?:\s+(?P<slot>\d+))?)"
            rf"(?:\s*[:\-|]\s*|\s{{2,}})"
            rf"(?P<home>.+?)\s+(?:vs?\.?|v)\s+(?P<away>.+)$",
            flags=re.IGNORECASE,
        )
        knockout_header_re = re.compile(
            rf"^(?P<label>(?P<stage>{stage_pattern})(?:\s+(?P<slot>\d+))?)\s*$",
            flags=re.IGNORECASE,
        )
        matchup_re = re.compile(
            r"^(?P<home>.+?)\s+(?:vs?\.?|v)\s+(?P<away>.+)$",
            flags=re.IGNORECASE,
        )

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = self._clean_fixture_text_line(line)
            if not line:
                continue

            league_match = re.match(r"^(?:league|liga|group|division)\s*([ab12])\b", line, flags=re.IGNORECASE)
            if league_match:
                current_league_key = _normalize_tournament_league_key(league_match.group(1))
                if "jornada" not in line.lower():
                    current_knockout_header = None
                    continue

            knockout_match = knockout_inline_re.match(line)
            if knockout_match:
                stage_type = _normalize_tournament_stage(knockout_match.group("stage"))
                bracket_slot = max(1, int(knockout_match.group("slot") or 1))
                fixtures.append(
                    {
                        "league_key": current_league_key,
                        "week_number": None,
                        "week_label": str(knockout_match.group("label") or "").strip(),
                        "home_name": knockout_match.group("home").strip(" -\t"),
                        "away_name": knockout_match.group("away").strip(" -\t"),
                        "stage_type": stage_type,
                        "round_number": 1,
                        "bracket_slot": bracket_slot,
                        "home_source": _default_stage_source(stage_type, bracket_slot),
                        "away_source": _default_stage_source(stage_type, bracket_slot),
                    }
                )
                current_knockout_header = None
                continue

            week_match = re.search(r"Jornada\s+(\d+)", line, flags=re.IGNORECASE)
            if week_match:
                current_week = int(week_match.group(1))
                current_label = line
                current_knockout_header = None
                continue

            knockout_header_match = knockout_header_re.match(line)
            if knockout_header_match:
                current_knockout_header = {
                    "league_key": current_league_key,
                    "stage_type": _normalize_tournament_stage(knockout_header_match.group("stage")),
                    "week_label": str(knockout_header_match.group("label") or "").strip(),
                    "round_number": 1,
                    "bracket_slot": max(1, int(knockout_header_match.group("slot") or 1)),
                }
                current_week = None
                current_label = None
                continue

            matchup_match = matchup_re.match(line)
            if matchup_match:
                home_name = matchup_match.group("home").strip(" -\t")
                away_name = matchup_match.group("away").strip(" -\t")
                if home_name and away_name:
                    if current_knockout_header is not None:
                        stage_type = current_knockout_header["stage_type"]
                        fixtures.append(
                            {
                                "league_key": current_knockout_header["league_key"],
                                "week_number": None,
                                "week_label": current_knockout_header["week_label"],
                                "home_name": home_name,
                                "away_name": away_name,
                                "stage_type": stage_type,
                                "round_number": current_knockout_header["round_number"],
                                "bracket_slot": current_knockout_header["bracket_slot"],
                                "home_source": _default_stage_source(stage_type, current_knockout_header["bracket_slot"]),
                                "away_source": _default_stage_source(stage_type, current_knockout_header["bracket_slot"]),
                            }
                        )
                        current_knockout_header = None
                    elif current_week is not None:
                        fixtures.append(
                            {
                                "league_key": current_league_key,
                                "week_number": current_week,
                                "week_label": current_label or f"Jornada {current_week}",
                                "home_name": home_name,
                                "away_name": away_name,
                                "stage_type": DEFAULT_TOURNAMENT_STAGE,
                                "round_number": None,
                                "bracket_slot": None,
                                "home_source": None,
                                "away_source": None,
                            }
                        )

        return fixtures

    async def add_fixtures_from_text(self, tournament_id: int, text: str, threshold: float = 0.7) -> Dict[str, int]:
        """Parse and insert fixtures for a tournament."""
        await self._ensure_tournament_league_schema()
        fixtures = self._parse_fixtures_text(text)
        if not fixtures:
            return {"added": 0, "skipped": 0}

        tournament = await self.get_tournament(tournament_id)
        league_count = 2 if int((tournament or {}).get("league_count") or 1) == 2 else 1
        tournament_teams = await self.get_tournament_teams(tournament_id)
        all_teams = [
            {
                "guild_id": row.get("guild_id"),
                "guild_name": row.get("team_name_snapshot"),
                "league_key": _normalize_tournament_league_key(row.get("league_key")),
            }
            for row in tournament_teams
            if row.get("guild_id") and row.get("team_name_snapshot")
        ]
        if not all_teams:
            team_rows = await self.pool.fetch(
                "SELECT guild_id, guild_name FROM IOSCA_TEAMS"
            )
            all_teams = [dict(r) | {"league_key": DEFAULT_TOURNAMENT_LEAGUE_KEY} for r in team_rows]
        added = 0
        skipped = 0
        # Collected here, applied as one executemany() below instead of one
        # INSERT per parsed fixture line (each independently acquiring a
        # pool connection).
        fixture_params: List[tuple] = []

        for fixture in fixtures:
            parsed_league_key = fixture.get("league_key")
            week_number = fixture.get("week_number")
            week_label = fixture.get("week_label")
            home_name = fixture.get("home_name")
            away_name = fixture.get("away_name")
            stage_type = _normalize_tournament_stage(fixture.get("stage_type"))
            round_number = fixture.get("round_number")
            bracket_slot = fixture.get("bracket_slot")
            home_source = fixture.get("home_source")
            away_source = fixture.get("away_source")
            effective_league_key = (
                DEFAULT_TOURNAMENT_LEAGUE_KEY
                if league_count <= 1
                else _normalize_tournament_league_key(parsed_league_key)
            )
            team_pool = [
                team for team in all_teams
                if league_count <= 1 or _normalize_tournament_league_key(team.get("league_key")) == effective_league_key
            ]
            best_home = find_best_match(home_name, team_pool, threshold=threshold)
            best_away = find_best_match(away_name, team_pool, threshold=threshold)
            home_id = best_home["guild_id"] if best_home else None
            away_id = best_away["guild_id"] if best_away else None

            fixture_params.append((
                tournament_id, effective_league_key, week_number, week_label,
                home_id, away_id, home_name, away_name,
                stage_type, round_number, bracket_slot, home_source, away_source,
            ))

        if fixture_params:
            try:
                async with self.pool.acquire() as conn:
                    async with conn.transaction():
                        before = await conn.fetchval(
                            "SELECT count(*) FROM TOURNAMENT_FIXTURES WHERE tournament_id = $1", tournament_id
                        )
                        await conn.executemany(
                            """
                            INSERT INTO TOURNAMENT_FIXTURES
                                (
                                    tournament_id, league_key, week_number, week_label,
                                    home_guild_id, away_guild_id, home_name_raw, away_name_raw,
                                    stage_type, round_number, bracket_slot, home_source, away_source
                                )
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                            ON CONFLICT DO NOTHING
                            """,
                            fixture_params,
                        )
                        after = await conn.fetchval(
                            "SELECT count(*) FROM TOURNAMENT_FIXTURES WHERE tournament_id = $1", tournament_id
                        )
                added = max(0, after - before)
                skipped = len(fixture_params) - added
            except Exception as e:
                logger.error(f"Failed to batch-insert fixtures for tournament {tournament_id}: {e}")
                skipped = len(fixture_params)

        return {"added": added, "skipped": skipped}

    async def get_fixtures_for_week(self, tournament_id: int, week_number: int) -> List[Dict[str, Any]]:
        await self._ensure_tournament_league_schema()
        rows = await self.pool.fetch(
            """
            SELECT * FROM TOURNAMENT_FIXTURES
            WHERE tournament_id = $1 AND week_number = $2
            ORDER BY league_key ASC, id ASC
            """,
            tournament_id,
            week_number
        )
        return [dict(r) for r in rows]

    async def get_week_numbers(self, tournament_id: int) -> List[int]:
        await self._ensure_tournament_league_schema()
        rows = await self.pool.fetch(
            """
            SELECT DISTINCT week_number
            FROM TOURNAMENT_FIXTURES
            WHERE tournament_id = $1 AND week_number IS NOT NULL
            ORDER BY week_number ASC
            """,
            tournament_id
        )
        return [r["week_number"] for r in rows]

    async def get_fixture_sections(self, tournament_id: int) -> List[Dict[str, Any]]:
        await self._ensure_tournament_league_schema()
        rows = await self.pool.fetch(
            """
            SELECT DISTINCT
                COALESCE(stage_type, 'league') AS stage_type,
                week_number,
                round_number,
                bracket_slot,
                COALESCE(
                    NULLIF(week_label, ''),
                    CASE
                        WHEN COALESCE(NULLIF(lower(trim(stage_type)), ''), 'league') = 'league'
                             AND week_number IS NOT NULL
                        THEN CONCAT('Jornada ', week_number::text)
                        ELSE INITCAP(REPLACE(COALESCE(stage_type, 'league'), '_', ' '))
                    END
                ) AS week_label
            FROM TOURNAMENT_FIXTURES
            WHERE tournament_id = $1
            ORDER BY
                CASE COALESCE(NULLIF(lower(trim(stage_type)), ''), 'league')
                    WHEN 'league' THEN 0
                    WHEN 'play_in' THEN 1
                    WHEN 'quarterfinal' THEN 2
                    WHEN 'semifinal' THEN 3
                    WHEN 'third_place' THEN 4
                    WHEN 'final' THEN 5
                    WHEN 'consolation' THEN 6
                    ELSE 7
                END,
                week_number ASC NULLS LAST,
                round_number ASC NULLS LAST,
                bracket_slot ASC NULLS LAST,
                week_label ASC
            """,
            tournament_id,
        )
        return [dict(r) for r in rows]

    async def get_fixtures_for_section(
        self,
        tournament_id: int,
        *,
        stage_type: Any,
        week_number: Optional[int] = None,
        round_number: Optional[int] = None,
        bracket_slot: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        await self._ensure_tournament_league_schema()
        normalized_stage = _normalize_tournament_stage(stage_type)
        rows = await self.pool.fetch(
            """
            SELECT *
            FROM TOURNAMENT_FIXTURES
            WHERE tournament_id = $1
              AND COALESCE(NULLIF(lower(trim(stage_type)), ''), 'league') = $2
              AND (
                    ($3::int IS NULL AND week_number IS NULL)
                 OR week_number = $3
              )
              AND (
                    ($4::int IS NULL AND round_number IS NULL)
                 OR round_number = $4
              )
              AND (
                    ($5::int IS NULL AND bracket_slot IS NULL)
                 OR bracket_slot = $5
              )
            ORDER BY league_key ASC, week_number ASC NULLS LAST, bracket_slot ASC NULLS LAST, id ASC
            """,
            tournament_id,
            normalized_stage,
            week_number,
            round_number,
            bracket_slot,
        )
        return [dict(r) for r in rows]

    async def get_open_fixtures_for_team(self, tournament_id: int, guild_id: int) -> List[Dict[str, Any]]:
        await self._ensure_tournament_league_schema()
        rows = await self.pool.fetch(
            """
            SELECT f.*
            FROM TOURNAMENT_FIXTURES f
            WHERE f.tournament_id = $1
              AND COALESCE(f.is_played, FALSE) = FALSE
              AND COALESCE(f.is_draw_home, FALSE) = FALSE
              AND COALESCE(f.is_draw_away, FALSE) = FALSE
              AND COALESCE(f.is_forfeit_home, FALSE) = FALSE
              AND COALESCE(f.is_forfeit_away, FALSE) = FALSE
              AND COALESCE(f.is_active, TRUE) = TRUE
              AND NOT EXISTS (
                  SELECT 1 FROM TOURNAMENT_SCHEDULES s
                  WHERE s.fixture_id = f.id AND s.status IN ('pending', 'countered', 'confirmed')
              )
            ORDER BY f.league_key ASC, f.week_number ASC, f.id ASC
            """,
            tournament_id
        )
        fixtures = [dict(r) for r in rows]
        team = await self.pool.fetchrow("SELECT guild_name FROM IOSCA_TEAMS WHERE guild_id = $1", guild_id)
        team_name = team["guild_name"] if team else None
        if not team_name:
            return [f for f in fixtures if f.get("home_guild_id") == guild_id or f.get("away_guild_id") == guild_id]

        def matches_team(f):
            if f.get("home_guild_id") == guild_id or f.get("away_guild_id") == guild_id:
                return True
            names = [f.get("home_name_raw"), f.get("away_name_raw")]
            for n in names:
                if n and team_name.lower() in n.lower():
                    return True
            return False

        return [f for f in fixtures if matches_team(f)]

    async def get_open_fixtures_for_teams(self, tournament_id: int, guild_ids: List[int]) -> List[Dict[str, Any]]:
        await self._ensure_tournament_league_schema()
        normalized_guild_ids = [int(gid) for gid in guild_ids if gid is not None]
        if not normalized_guild_ids:
            return []

        rows = await self.pool.fetch(
            """
            SELECT f.*
            FROM TOURNAMENT_FIXTURES f
            WHERE f.tournament_id = $1
              AND COALESCE(f.is_played, FALSE) = FALSE
              AND COALESCE(f.is_draw_home, FALSE) = FALSE
              AND COALESCE(f.is_draw_away, FALSE) = FALSE
              AND COALESCE(f.is_forfeit_home, FALSE) = FALSE
              AND COALESCE(f.is_forfeit_away, FALSE) = FALSE
              AND COALESCE(f.is_active, TRUE) = TRUE
              AND NOT EXISTS (
                  SELECT 1 FROM TOURNAMENT_SCHEDULES s
                  WHERE s.fixture_id = f.id AND s.status IN ('pending', 'countered', 'confirmed')
              )
            ORDER BY f.league_key ASC, f.week_number ASC, f.id ASC
            """,
            tournament_id
        )
        fixtures = [dict(r) for r in rows]
        if not fixtures:
            return []

        team_rows = await self.pool.fetch(
            """
            SELECT guild_id, guild_name
            FROM IOSCA_TEAMS
            WHERE guild_id = ANY($1::bigint[])
            """,
            normalized_guild_ids,
        )
        team_name_by_id = {
            int(row["guild_id"]): str(row["guild_name"]).strip().lower()
            for row in team_rows
            if row.get("guild_id") is not None and row.get("guild_name")
        }
        guild_id_set = set(normalized_guild_ids)

        def matches_any_team(fixture: Dict[str, Any]) -> bool:
            home_guild_id = fixture.get("home_guild_id")
            away_guild_id = fixture.get("away_guild_id")
            if home_guild_id in guild_id_set or away_guild_id in guild_id_set:
                return True

            home_name_raw = str(fixture.get("home_name_raw") or "").strip().lower()
            away_name_raw = str(fixture.get("away_name_raw") or "").strip().lower()
            for team_name in team_name_by_id.values():
                if not team_name:
                    continue
                if (home_name_raw and team_name in home_name_raw) or (away_name_raw and team_name in away_name_raw):
                    return True
            return False

        return [fixture for fixture in fixtures if matches_any_team(fixture)]

    async def get_open_fixtures(self, tournament_id: int) -> List[Dict[str, Any]]:
        await self._ensure_tournament_league_schema()
        rows = await self.pool.fetch(
            """
            SELECT f.*
            FROM TOURNAMENT_FIXTURES f
            WHERE f.tournament_id = $1
              AND COALESCE(f.is_played, FALSE) = FALSE
              AND COALESCE(f.is_draw_home, FALSE) = FALSE
              AND COALESCE(f.is_draw_away, FALSE) = FALSE
              AND COALESCE(f.is_forfeit_home, FALSE) = FALSE
              AND COALESCE(f.is_forfeit_away, FALSE) = FALSE
              AND COALESCE(f.is_active, TRUE) = TRUE
              AND NOT EXISTS (
                  SELECT 1 FROM TOURNAMENT_SCHEDULES s
                  WHERE s.fixture_id = f.id AND s.status IN ('pending', 'countered', 'confirmed')
              )
            ORDER BY
                f.league_key ASC,
                CASE COALESCE(NULLIF(lower(trim(f.stage_type)), ''), 'league')
                    WHEN 'league' THEN 0
                    WHEN 'play_in' THEN 1
                    WHEN 'quarterfinal' THEN 2
                    WHEN 'semifinal' THEN 3
                    WHEN 'third_place' THEN 4
                    WHEN 'final' THEN 5
                    WHEN 'consolation' THEN 6
                    ELSE 7
                END,
                f.week_number ASC NULLS LAST,
                f.round_number ASC NULLS LAST,
                f.bracket_slot ASC NULLS LAST,
                f.id ASC
            """,
            tournament_id
        )
        return [dict(r) for r in rows]

    async def link_fixture_team_ids(self, tournament_id: Optional[int] = None) -> Dict[str, int]:
        """Backfill fixture home/away guild ids from IOSCA_TEAMS where names match."""
        await self._ensure_tournament_league_schema()
        where_sql = ""
        params: List[Any] = []
        if tournament_id is not None:
            where_sql = "AND f.tournament_id = $1"
            params.append(tournament_id)

        home_update = await self.pool.execute(
            f"""
            UPDATE TOURNAMENT_FIXTURES f
            SET home_guild_id = t.guild_id
            FROM IOSCA_TEAMS t
            WHERE f.home_guild_id IS NULL
              {where_sql}
              AND lower(trim(coalesce(f.home_name_raw, ''))) = lower(trim(coalesce(t.guild_name, '')))
            """,
            *params
        )
        away_update = await self.pool.execute(
            f"""
            UPDATE TOURNAMENT_FIXTURES f
            SET away_guild_id = t.guild_id
            FROM IOSCA_TEAMS t
            WHERE f.away_guild_id IS NULL
              {where_sql}
              AND lower(trim(coalesce(f.away_name_raw, ''))) = lower(trim(coalesce(t.guild_name, '')))
            """,
            *params
        )

        def _row_count(result: str) -> int:
            try:
                return int(str(result).split()[-1])
            except Exception:
                return 0

        return {
            "home_linked": _row_count(home_update),
            "away_linked": _row_count(away_update),
        }

    async def create_schedule_proposal(
        self,
        tournament_id: int,
        fixture_id: int,
        proposed_by: int,
        proposed_time: datetime,
        server_name: str
    ) -> Optional[int]:
        await self._ensure_tournament_league_schema()
        proposed_time = self._normalize_naive_utc(proposed_time)
        slot_start = proposed_time.replace(minute=0, second=0, microsecond=0)
        conflict = await self.pool.fetchval(
            """
            SELECT 1 FROM TOURNAMENT_SCHEDULES
            WHERE tournament_id = $1
              AND slot_start = $2
              AND server_name = $3
              AND status IN ('pending', 'countered', 'confirmed')
            """,
            tournament_id,
            slot_start,
            server_name
        )
        if conflict:
            return None

        try:
            return await self.pool.fetchval(
                """
                INSERT INTO TOURNAMENT_SCHEDULES
                    (tournament_id, fixture_id, proposed_by, proposed_time, slot_start, server_name, status, last_action_by)
                VALUES ($1, $2, $3, $4, $5, $6, 'pending', $3)
                RETURNING id
                """,
                tournament_id,
                fixture_id,
                proposed_by,
                proposed_time,
                slot_start,
                server_name
            )
        except Exception as e:
            logger.error(f"Failed to create schedule proposal: {e}")
            return None

    async def update_schedule(
        self,
        schedule_id: int,
        proposed_by: int,
        proposed_time: datetime,
        server_name: str,
        status: str
    ) -> bool:
        proposed_time = self._normalize_naive_utc(proposed_time)
        slot_start = proposed_time.replace(minute=0, second=0, microsecond=0)
        conflict = await self.pool.fetchval(
            """
            SELECT 1 FROM TOURNAMENT_SCHEDULES
            WHERE tournament_id = (SELECT tournament_id FROM TOURNAMENT_SCHEDULES WHERE id = $1)
              AND slot_start = $2
              AND server_name = $3
              AND status IN ('pending', 'countered', 'confirmed')
              AND id <> $1
            """,
            schedule_id,
            slot_start,
            server_name
        )
        if conflict:
            return False

        try:
            await self.pool.execute(
                """
                UPDATE TOURNAMENT_SCHEDULES
                SET proposed_by = $2,
                    proposed_time = $3,
                    slot_start = $4,
                    server_name = $5,
                    status = $6,
                    last_action_by = $2
                WHERE id = $1
                """,
                schedule_id,
                proposed_by,
                proposed_time,
                slot_start,
                server_name,
                status
            )
            return True
        except Exception as e:
            logger.error(f"Failed to update schedule {schedule_id}: {e}")
            return False

    async def set_schedule_metadata(self, schedule_id: int, expires_at: datetime | None, message_ids: dict | None) -> bool:
        expires_at = self._normalize_naive_utc(expires_at) if expires_at else None
        try:
            await self.pool.execute(
                """
                UPDATE TOURNAMENT_SCHEDULES
                SET proposal_expires_at = $2,
                    proposal_message_ids = $3
                WHERE id = $1
                """,
                schedule_id,
                expires_at,
                json.dumps(message_ids or {})
            )
            return True
        except Exception as e:
            logger.error(f"Failed to update schedule metadata {schedule_id}: {e}")
            return False

    async def clear_schedule_votes(self, schedule_id: int) -> None:
        try:
            await self.pool.execute(
                "DELETE FROM TOURNAMENT_SCHEDULE_VOTES WHERE schedule_id = $1",
                schedule_id
            )
        except Exception as e:
            logger.error(f"Failed to clear schedule votes {schedule_id}: {e}")

    async def record_schedule_vote(self, schedule_id: int, guild_id: int, user_id: int, vote: bool) -> bool:
        try:
            await self.pool.execute(
                """
                INSERT INTO TOURNAMENT_SCHEDULE_VOTES (schedule_id, guild_id, user_id, vote)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (schedule_id, guild_id, user_id)
                DO UPDATE SET vote = EXCLUDED.vote, updated_at = NOW()
                """,
                schedule_id,
                guild_id,
                user_id,
                vote
            )
            return True
        except Exception as e:
            logger.error(f"Failed to record schedule vote: {e}")
            return False

    async def get_schedule_vote_counts(self, schedule_id: int) -> Dict[int, Dict[str, int]]:
        rows = await self.pool.fetch(
            """
            SELECT guild_id,
                   SUM(CASE WHEN vote THEN 1 ELSE 0 END) AS yes_count,
                   SUM(CASE WHEN NOT vote THEN 1 ELSE 0 END) AS no_count
            FROM TOURNAMENT_SCHEDULE_VOTES
            WHERE schedule_id = $1
            GROUP BY guild_id
            """,
            schedule_id
        )
        counts: Dict[int, Dict[str, int]] = {}
        for row in rows:
            counts[int(row["guild_id"])] = {
                "yes": int(row.get("yes_count") or 0),
                "no": int(row.get("no_count") or 0),
            }
        return counts

    async def get_expired_pending_schedules(self) -> List[Dict[str, Any]]:
        await self._ensure_tournament_league_schema()
        rows = await self.pool.fetch(
            """
            SELECT s.*, f.league_key, f.home_guild_id, f.away_guild_id, f.home_name_raw, f.away_name_raw, f.week_number, f.week_label,
                   t.name as tournament_name
            FROM TOURNAMENT_SCHEDULES s
            JOIN TOURNAMENT_FIXTURES f ON f.id = s.fixture_id
            JOIN TOURNAMENTS t ON t.id = s.tournament_id
            WHERE s.status IN ('pending', 'countered')
              AND s.proposal_expires_at IS NOT NULL
              AND s.proposal_expires_at <= NOW()
            """
        )
        return [dict(r) for r in rows]

    async def set_schedule_status(self, schedule_id: int, status: str, actor_id: Optional[int] = None) -> bool:
        try:
            await self.pool.execute(
                """
                UPDATE TOURNAMENT_SCHEDULES
                SET status = $2,
                    last_action_by = COALESCE($3, last_action_by),
                    confirmed_at = CASE WHEN $2 = 'confirmed' THEN NOW() ELSE confirmed_at END
                WHERE id = $1
                """,
                schedule_id,
                status,
                actor_id
            )
            return True
        except Exception as e:
            logger.error(f"Failed to set schedule status: {e}")
            return False

    @staticmethod
    def _normalize_naive_utc(dt: datetime) -> datetime:
        """Convert aware datetime to naive UTC for DB timestamps without tz."""
        if isinstance(dt, datetime) and dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    async def mark_schedule_reminded(self, schedule_id: int) -> None:
        try:
            await self.pool.execute(
                "UPDATE TOURNAMENT_SCHEDULES SET reminder_sent_at = NOW() WHERE id = $1",
                schedule_id
            )
        except Exception as e:
            logger.error(f"Failed to mark schedule reminder: {e}")

    async def get_schedule(self, schedule_id: int) -> Optional[Dict[str, Any]]:
        await self._ensure_tournament_league_schema()
        row = await self.pool.fetchrow(
            """
            SELECT s.*, f.league_key, f.home_guild_id, f.away_guild_id, f.home_name_raw, f.away_name_raw, f.week_number, f.week_label,
                   t.name as tournament_name
            FROM TOURNAMENT_SCHEDULES s
            JOIN TOURNAMENT_FIXTURES f ON f.id = s.fixture_id
            JOIN TOURNAMENTS t ON t.id = s.tournament_id
            WHERE s.id = $1
            """,
            schedule_id
        )
        return dict(row) if row else None

    async def list_schedules(
        self,
        tournament_id: Optional[int] = None,
        status: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        await self._ensure_tournament_league_schema()
        query = """
        SELECT s.*, f.league_key, f.home_guild_id, f.away_guild_id, f.home_name_raw, f.away_name_raw, f.week_number, f.week_label,
               t.name as tournament_name
        FROM TOURNAMENT_SCHEDULES s
        JOIN TOURNAMENT_FIXTURES f ON f.id = s.fixture_id
        JOIN TOURNAMENTS t ON t.id = s.tournament_id
        WHERE 1=1
        """
        params: List[Any] = []
        if tournament_id is not None:
            params.append(tournament_id)
            query += f" AND s.tournament_id = ${len(params)}"
        if status:
            params.append(status)
            query += f" AND s.status = ${len(params)}"
        query += " ORDER BY s.proposed_time DESC"
        if limit:
            params.append(limit)
            query += f" LIMIT ${len(params)}"
        rows = await self.pool.fetch(query, *params)
        return [dict(r) for r in rows]

    async def get_upcoming_schedules(self, window_minutes: int = 15) -> List[Dict[str, Any]]:
        await self._ensure_tournament_league_schema()
        rows = await self.pool.fetch(
            """
            SELECT s.*,
                   f.league_key, f.home_guild_id, f.away_guild_id, f.home_name_raw, f.away_name_raw, f.week_label,
                   t.name as tournament_name
            FROM TOURNAMENT_SCHEDULES s
            JOIN TOURNAMENT_FIXTURES f ON f.id = s.fixture_id
            JOIN TOURNAMENTS t ON t.id = s.tournament_id
            WHERE s.status = 'confirmed'
              AND s.reminder_sent_at IS NULL
              AND s.proposed_time BETWEEN NOW() AND NOW() + ($1 * interval '1 minute')
            """,
            window_minutes
        )
        return [dict(r) for r in rows]

    async def cancel_schedules_for_day(self, tournament_id: int, day: datetime) -> int:
        await self._ensure_tournament_league_schema()
        day = self._normalize_naive_utc(day)
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        result = await self.pool.execute(
            """
            UPDATE TOURNAMENT_SCHEDULES
            SET status = 'cancelled'
            WHERE tournament_id = $1
              AND proposed_time >= $2
              AND proposed_time < $3
              AND status IN ('pending', 'countered', 'confirmed')
            """,
            tournament_id,
            start,
            end
        )
        if result and result.startswith("UPDATE "):
            return int(result.split()[-1])
        return 0

    async def add_forfeit(
        self,
        tournament_id: int,
        fixture_id: int,
        forfeiting_guild_id: int,
        winner_guild_id: int,
        created_by: int,
        score_forfeit: int = 10
    ) -> bool:
        fixture = await self.pool.fetchrow(
            """
            SELECT id, home_guild_id, away_guild_id
            FROM TOURNAMENT_FIXTURES
            WHERE tournament_id = $1 AND id = $2
            """,
            tournament_id,
            fixture_id,
        )
        if not fixture:
            return False

        home_id = fixture.get("home_guild_id")
        away_id = fixture.get("away_guild_id")
        is_forfeit_home = bool(home_id and int(home_id) == int(forfeiting_guild_id))
        is_forfeit_away = bool(away_id and int(away_id) == int(forfeiting_guild_id))

        # Fallback if caller passed winner/forfeit IDs and fixture IDs are partially missing:
        if not is_forfeit_home and not is_forfeit_away:
            if home_id and int(home_id) == int(winner_guild_id):
                is_forfeit_away = True
            elif away_id and int(away_id) == int(winner_guild_id):
                is_forfeit_home = True

        if is_forfeit_home == is_forfeit_away:
            logger.error(
                "Failed to add forfeit: fixture %s could not map forfeiting side (home=%s away=%s forfeiting=%s winner=%s)",
                fixture_id,
                home_id,
                away_id,
                forfeiting_guild_id,
                winner_guild_id,
            )
            return False

        try:
            await self.pool.execute(
                """
                UPDATE TOURNAMENT_FIXTURES
                SET is_played = TRUE,
                    is_active = FALSE,
                    is_draw_home = FALSE,
                    is_draw_away = FALSE,
                    is_forfeit_home = $2,
                    is_forfeit_away = $3,
                    forfeit_score = $4,
                    winner_guild_id = $5,
                    result_set_by = $6,
                    result_set_at = NOW(),
                    played_at = COALESCE(played_at, NOW())
                WHERE id = $1 AND tournament_id = $7
                """,
                fixture_id,
                is_forfeit_home,
                is_forfeit_away,
                int(score_forfeit or 10),
                winner_guild_id,
                created_by,
                tournament_id,
            )
        except Exception as e:
            logger.error(f"Failed to mark forfeited fixture as played ({fixture_id}): {e}")
            return False
        return True

    async def add_draw(
        self,
        tournament_id: int,
        fixture_id: int,
        created_by: int,
    ) -> bool:
        fixture = await self.pool.fetchrow(
            """
            SELECT id
            FROM TOURNAMENT_FIXTURES
            WHERE tournament_id = $1 AND id = $2
            """,
            tournament_id,
            fixture_id,
        )
        if not fixture:
            return False

        try:
            await self.pool.execute(
                """
                UPDATE TOURNAMENT_FIXTURES
                SET is_played = TRUE,
                    is_active = FALSE,
                    is_draw_home = TRUE,
                    is_draw_away = TRUE,
                    is_forfeit_home = FALSE,
                    is_forfeit_away = FALSE,
                    winner_guild_id = NULL,
                    result_set_by = $3,
                    result_set_at = NOW(),
                    played_at = COALESCE(played_at, NOW())
                WHERE id = $1 AND tournament_id = $2
                """,
                fixture_id,
                tournament_id,
                created_by,
            )
        except Exception as e:
            logger.error(f"Failed to mark drawn fixture as played ({fixture_id}): {e}")
            return False
        return True

    async def _mark_fixture_played_for_match(
        self,
        tournament_id: int,
        match_stats_id: int,
        played_at: Optional[datetime],
        home_id: int,
        away_id: int,
        preferred_fixture_id: Optional[int] = None
    ) -> Optional[int]:
        normalized_played_at = self._normalize_naive_utc(played_at) if played_at else None

        if preferred_fixture_id:
            updated_fixture = await self.pool.fetchval(
                """
                UPDATE TOURNAMENT_FIXTURES
                SET is_played = TRUE,
                    is_active = FALSE,
                    is_draw_home = FALSE,
                    is_draw_away = FALSE,
                    is_forfeit_home = FALSE,
                    is_forfeit_away = FALSE,
                    played_match_stats_id = $2,
                    played_at = COALESCE($3, played_at, NOW())
                WHERE id = $1
                  AND tournament_id = $4
                  AND COALESCE(is_played, FALSE) = FALSE
                  AND COALESCE(is_draw_home, FALSE) = FALSE
                  AND COALESCE(is_draw_away, FALSE) = FALSE
                  AND COALESCE(is_forfeit_home, FALSE) = FALSE
                  AND COALESCE(is_forfeit_away, FALSE) = FALSE
                  AND (
                        (home_guild_id = $5 AND away_guild_id = $6)
                     OR (home_guild_id = $6 AND away_guild_id = $5)
                  )
                RETURNING id
                """,
                preferred_fixture_id,
                match_stats_id,
                normalized_played_at,
                tournament_id,
                home_id,
                away_id,
            )
            return int(updated_fixture) if updated_fixture else None

        # Prefer exact home/away orientation.
        fixture_id = await self.pool.fetchval(
            """
            SELECT id
            FROM TOURNAMENT_FIXTURES
            WHERE tournament_id = $1
              AND COALESCE(is_played, FALSE) = FALSE
              AND COALESCE(is_draw_home, FALSE) = FALSE
              AND COALESCE(is_draw_away, FALSE) = FALSE
              AND COALESCE(is_forfeit_home, FALSE) = FALSE
              AND COALESCE(is_forfeit_away, FALSE) = FALSE
              AND home_guild_id = $2
              AND away_guild_id = $3
            ORDER BY week_number ASC NULLS LAST, id ASC
            LIMIT 1
            """,
            tournament_id,
            home_id,
            away_id
        )

        # Fallback if match teams are swapped in source row.
        if not fixture_id:
            fixture_id = await self.pool.fetchval(
                """
                SELECT id
                FROM TOURNAMENT_FIXTURES
                WHERE tournament_id = $1
                  AND COALESCE(is_played, FALSE) = FALSE
                  AND COALESCE(is_draw_home, FALSE) = FALSE
                  AND COALESCE(is_draw_away, FALSE) = FALSE
                  AND COALESCE(is_forfeit_home, FALSE) = FALSE
                  AND COALESCE(is_forfeit_away, FALSE) = FALSE
                  AND home_guild_id = $3
                  AND away_guild_id = $2
                ORDER BY week_number ASC NULLS LAST, id ASC
                LIMIT 1
                """,
                tournament_id,
                home_id,
                away_id
            )

        if not fixture_id:
            return None

        await self.pool.execute(
            """
            UPDATE TOURNAMENT_FIXTURES
            SET is_played = TRUE,
                is_active = FALSE,
                is_draw_home = FALSE,
                is_draw_away = FALSE,
                is_forfeit_home = FALSE,
                is_forfeit_away = FALSE,
                played_match_stats_id = $2,
                played_at = COALESCE($3, played_at, NOW())
            WHERE id = $1
            """,
            fixture_id,
            match_stats_id,
            normalized_played_at
        )
        return fixture_id

    async def find_ready_fixture_context(
        self,
        *,
        game_type: Optional[str],
        home_guild_id: Optional[int],
        away_guild_id: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        """Best-effort lookup for the live fixture being started right now."""
        if home_guild_id is None or away_guild_id is None:
            return None

        rows = await self.pool.fetch(
            """
            SELECT
                f.id AS fixture_id,
                f.tournament_id,
                f.week_number,
                sched.schedule_id,
                sched.schedule_status,
                sched.proposed_time
            FROM TOURNAMENT_FIXTURES f
            JOIN TOURNAMENTS t
              ON t.id = f.tournament_id
            LEFT JOIN LATERAL (
                SELECT
                    s.id AS schedule_id,
                    s.status AS schedule_status,
                    s.proposed_time
                FROM TOURNAMENT_SCHEDULES s
                WHERE s.fixture_id = f.id
                ORDER BY
                    CASE WHEN s.status = 'confirmed' THEN 0 ELSE 1 END,
                    COALESCE(s.confirmed_at, s.updated_at, s.created_at) DESC,
                    s.id DESC
                LIMIT 1
            ) sched ON TRUE
            WHERE t.status = 'active'
              AND ($1::TEXT IS NULL OR t.format = $1)
              AND COALESCE(f.is_played, FALSE) = FALSE
              AND COALESCE(f.is_draw_home, FALSE) = FALSE
              AND COALESCE(f.is_draw_away, FALSE) = FALSE
              AND COALESCE(f.is_forfeit_home, FALSE) = FALSE
              AND COALESCE(f.is_forfeit_away, FALSE) = FALSE
              AND (
                    (f.home_guild_id = $2 AND f.away_guild_id = $3)
                 OR (f.home_guild_id = $3 AND f.away_guild_id = $2)
              )
            ORDER BY f.week_number ASC NULLS LAST, f.id ASC
            """,
            str(game_type or "").strip() or None,
            int(home_guild_id),
            int(away_guild_id),
        )
        candidates = [dict(row) for row in rows]
        if not candidates:
            return None
        confirmed = [
            cand
            for cand in candidates
            if str(cand.get("schedule_status") or "").strip().lower() == "confirmed"
        ]

        if TOURNAMENT_AUTO_SYNC_REQUIRE_CONFIRMED_SCHEDULE:
            now_utc = datetime.now(timezone.utc)
            limit_seconds = TOURNAMENT_AUTO_SYNC_MATCH_WINDOW_HOURS * 3600
            confirmed_in_window: list[tuple[float, Dict[str, Any]]] = []
            for cand in confirmed:
                proposed_time = cand.get("proposed_time")
                if not isinstance(proposed_time, datetime):
                    continue
                proposed_utc = (
                    proposed_time.astimezone(timezone.utc)
                    if proposed_time.tzinfo
                    else proposed_time.replace(tzinfo=timezone.utc)
                )
                delta = abs((now_utc - proposed_utc).total_seconds())
                if delta <= limit_seconds:
                    confirmed_in_window.append((delta, cand))

            if len(confirmed_in_window) != 1:
                return None

            confirmed_in_window.sort(key=lambda item: item[0])
            return confirmed_in_window[0][1]

        if len(candidates) == 1:
            return candidates[0]
        if len(confirmed) == 1:
            return confirmed[0]
        return None

    async def _resolve_active_context_fixture_id(
        self,
        *,
        tournament_id: int,
        match: Dict[str, Any],
        home_id: int,
        away_id: int,
    ) -> Optional[int]:
        try:
            rows = await self.pool.fetch(
                """
                SELECT *
                FROM ACTIVE_MATCH_CONTEXTS
                WHERE fixture_id IS NOT NULL
                  AND tournament_id = $1
                  AND source_kind = 'tournament'
                  AND created_at >= NOW() - INTERVAL '24 hours'
                  AND (
                        finished_at IS NULL
                     OR match_stats_id IS NULL
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT 25
                """,
                int(tournament_id),
            )
        except Exception as e:
            logger.warning(
                "Failed to look up active tournament match context for tournament %s: %s",
                tournament_id,
                e,
            )
            return None

        home_name_norm = normalize_team_name(str(match.get("home_team_name") or ""))
        away_name_norm = normalize_team_name(str(match.get("away_team_name") or ""))

        best_fixture_id: Optional[int] = None
        best_score = -1
        match_game_type = str(match.get("game_type") or "").strip().lower()
        match_guilds = {int(gid) for gid in (home_id, away_id) if gid is not None}
        match_names = {home_name_norm, away_name_norm}

        for raw_row in rows:
            row = dict(raw_row)
            fixture_id = row.get("fixture_id")
            if fixture_id is None:
                continue

            score = 0
            row_guilds = {
                int(gid)
                for gid in (row.get("team1_guild_id"), row.get("team2_guild_id"))
                if gid is not None
            }
            if row_guilds and match_guilds and row_guilds == match_guilds:
                score += 10

            row_names = {
                normalize_team_name(str(row.get("team1_name_norm") or "")),
                normalize_team_name(str(row.get("team2_name_norm") or "")),
            }
            if row_names == match_names:
                score += 4

            row_game_type = str(row.get("game_type") or "").strip().lower()
            if row_game_type and match_game_type and row_game_type == match_game_type:
                score += 1

            if score > best_score:
                best_score = score
                best_fixture_id = int(fixture_id)

        if best_score <= 0:
            return None
        return best_fixture_id

    async def add_match_by_id(
        self,
        tournament_id: int,
        match_stats_id: int,
        name_match_threshold: float = 0.8,
        preferred_fixture_id: Optional[int] = None,
        tournament: Optional[Dict[str, Any]] = None,
        team_ids: Optional[set] = None,
        team_name_map: Optional[Dict[str, int]] = None,
    ) -> bool:
        """Add a single match to a tournament and update standings/player stats.

        Pass already-fetched `tournament`/`team_ids`/`team_name_map` when
        calling this in a loop over multiple matches for the same
        tournament (e.g. bulk-adding selected matches, or syncing a
        tournament) -- they don't change between matches, so re-fetching
        them on every call is pure waste."""
        if tournament is None:
            tournament = await self.get_tournament(tournament_id)
        if not tournament:
            return False

        match_row = await self.pool.fetchrow(
            "SELECT * FROM MATCH_STATS WHERE id = $1",
            match_stats_id
        )
        if not match_row:
            return False
        match = dict(match_row)

        # Ensure format matches tournament format
        if match.get("game_type") != tournament.get("format"):
            return False

        if team_ids is None:
            team_ids = await self.get_tournament_team_ids(tournament_id)
        if not team_ids:
            return False

        home_id = match.get("home_guild_id")
        away_id = match.get("away_guild_id")

        # If IDs missing or not in tournament, try name match
        if team_name_map is None:
            team_name_map = await self._get_tournament_team_name_map(tournament_id)
        if home_id not in team_ids and match.get("home_team_name"):
            home_id = self._resolve_team_id_by_name(
                match.get("home_team_name"),
                team_ids,
                team_name_map,
                threshold=name_match_threshold
            )
        if away_id not in team_ids and match.get("away_team_name"):
            away_id = self._resolve_team_id_by_name(
                match.get("away_team_name"),
                team_ids,
                team_name_map,
                threshold=name_match_threshold
            )

        if home_id not in team_ids or away_id not in team_ids:
            return False

        # If a fixture between these teams already has a manual outcome (forfeit/draw),
        # ignore this match for tournament stats.
        fixture_outcome_locked = await self.pool.fetchval(
            """
            SELECT 1
            FROM TOURNAMENT_FIXTURES f
            WHERE f.tournament_id = $1
              AND (
                    (f.home_guild_id = $2 AND f.away_guild_id = $3)
                 OR (f.home_guild_id = $3 AND f.away_guild_id = $2)
              )
              AND (
                    COALESCE(f.is_draw_home, FALSE) = TRUE
                 OR COALESCE(f.is_draw_away, FALSE) = TRUE
                 OR COALESCE(f.is_forfeit_home, FALSE) = TRUE
                 OR COALESCE(f.is_forfeit_away, FALSE) = TRUE
              )
            LIMIT 1
            """,
            tournament_id,
            home_id,
            away_id,
        )
        if fixture_outcome_locked:
            return False

        # Skip if this match is already linked to a fixture in this tournament.
        already_linked = await self.pool.fetchval(
            """
            SELECT 1
            FROM TOURNAMENT_FIXTURES
            WHERE tournament_id = $1
              AND played_match_stats_id = $2
            LIMIT 1
            """,
            tournament_id,
            match_stats_id,
        )
        if already_linked:
            return False

        if not preferred_fixture_id:
            preferred_fixture_id = await self._resolve_active_context_fixture_id(
                tournament_id=tournament_id,
                match=match,
                home_id=int(home_id),
                away_id=int(away_id),
            )

        fixture_id = None
        try:
            fixture_id = await self._mark_fixture_played_for_match(
                tournament_id=tournament_id,
                match_stats_id=match_stats_id,
                played_at=match.get("datetime"),
                home_id=home_id,
                away_id=away_id,
                preferred_fixture_id=preferred_fixture_id,
            )
            if not fixture_id:
                # If fixtures were added with names only, backfill team links and retry.
                await self.link_fixture_team_ids(tournament_id=tournament_id)
                fixture_id = await self._mark_fixture_played_for_match(
                    tournament_id=tournament_id,
                    match_stats_id=match_stats_id,
                    played_at=match.get("datetime"),
                    home_id=home_id,
                    away_id=away_id,
                    preferred_fixture_id=preferred_fixture_id,
                )
        except Exception as e:
            logger.error(f"Failed marking fixture completed for match {match_stats_id}: {e}")
            return False

        # A played match must be bound to a fixture in the unified schema.
        if not fixture_id:
            return False

        home_score = int(match.get("home_score", 0) or 0)
        away_score = int(match.get("away_score", 0) or 0)
        winner_guild_id = None
        if home_score > away_score:
            winner_guild_id = int(home_id)
        elif away_score > home_score:
            winner_guild_id = int(away_id)

        await self.pool.execute(
            """
            UPDATE TOURNAMENT_FIXTURES
            SET winner_guild_id = $2
            WHERE id = $1
            """,
            fixture_id,
            winner_guild_id,
        )

        await self._apply_match_to_player_stats(tournament_id, match, home_id, away_id)
        return True

    async def _apply_match_to_player_stats(self, tournament_id: int, match: Dict[str, Any], home_id: int, away_id: int):
        if not await self._has_tournament_player_stats():
            return

        match_id = match.get("match_id")
        if match_id is None:
            logger.error("Failed to aggregate player stats: match_id is missing.")
            return
        home_lineup = match.get("home_lineup") or []
        away_lineup = match.get("away_lineup") or []

        try:
            if isinstance(home_lineup, str):
                home_lineup = json.loads(home_lineup)
            if isinstance(away_lineup, str):
                away_lineup = json.loads(away_lineup)
        except Exception:
            home_lineup = []
            away_lineup = []

        home_ids = {p.get("steam_id") for p in home_lineup if isinstance(p, dict) and p.get("steam_id")}
        away_ids = {p.get("steam_id") for p in away_lineup if isinstance(p, dict) and p.get("steam_id")}

        expects_text = await self._player_match_id_expects_text()
        match_id_param = str(match_id) if expects_text else match.get("id")

        try:
            rows = await self.pool.fetch(
                """
                SELECT
                    pmd.steam_id,
                    pmd.guild_id,
                    pmd.goals,
                    pmd.assists,
                    pmd.second_assists,
                    pmd.keeper_saves,
                    pmd.tackles,
                    pmd.interceptions,
                    p.discord_id,
                    p.discord_name
                FROM PLAYER_MATCH_DATA pmd
                LEFT JOIN IOSCA_PLAYERS p ON p.steam_id = pmd.steam_id
                WHERE pmd.match_id = $1
                """,
                match_id_param
            )
        except Exception as e:
            logger.error(f"Failed to aggregate player stats for match {match_id}: {e}")
            return

        aggregates: Dict[tuple, Dict[str, Any]] = {}
        for row in rows:
            steam_id = row.get("steam_id")
            if not steam_id:
                continue

            team_guild_id = row.get("guild_id")
            if team_guild_id not in (home_id, away_id):
                if steam_id in home_ids:
                    team_guild_id = home_id
                elif steam_id in away_ids:
                    team_guild_id = away_id
                else:
                    continue

            key = (steam_id, team_guild_id)
            if key not in aggregates:
                aggregates[key] = {
                    "steam_id": steam_id,
                    "team_guild_id": team_guild_id,
                    "discord_id": row.get("discord_id"),
                    "discord_name": row.get("discord_name"),
                    "goals": 0,
                    "assists": 0,
                    "second_assists": 0,
                    "keeper_saves": 0,
                    "tackles": 0,
                    "interceptions": 0,
                    "matches_played": 0,
                }

            agg = aggregates[key]
            agg["goals"] += row.get("goals") or 0
            agg["assists"] += row.get("assists") or 0
            agg["second_assists"] += row.get("second_assists") or 0
            agg["keeper_saves"] += row.get("keeper_saves") or 0
            agg["tackles"] += row.get("tackles") or 0
            agg["interceptions"] += row.get("interceptions") or 0
            agg["matches_played"] += 1

        if aggregates:
            params = [
                (
                    tournament_id, agg["steam_id"], agg.get("discord_id"), agg.get("discord_name"),
                    agg["team_guild_id"], agg["goals"], agg["assists"], agg["second_assists"],
                    agg["keeper_saves"], agg["tackles"], agg["interceptions"], agg["matches_played"],
                )
                for agg in aggregates.values()
            ]
            try:
                async with self.pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.executemany(
                            """
                            INSERT INTO TOURNAMENT_PLAYER_STATS
                                (tournament_id, steam_id, discord_id, player_name, team_guild_id,
                                 goals, assists, second_assists, keeper_saves, tackles, interceptions, matches_played)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                            ON CONFLICT (tournament_id, steam_id, team_guild_id) DO UPDATE SET
                                goals = TOURNAMENT_PLAYER_STATS.goals + EXCLUDED.goals,
                                assists = TOURNAMENT_PLAYER_STATS.assists + EXCLUDED.assists,
                                second_assists = TOURNAMENT_PLAYER_STATS.second_assists + EXCLUDED.second_assists,
                                keeper_saves = TOURNAMENT_PLAYER_STATS.keeper_saves + EXCLUDED.keeper_saves,
                                tackles = TOURNAMENT_PLAYER_STATS.tackles + EXCLUDED.tackles,
                                interceptions = TOURNAMENT_PLAYER_STATS.interceptions + EXCLUDED.interceptions,
                                matches_played = TOURNAMENT_PLAYER_STATS.matches_played + EXCLUDED.matches_played
                            """,
                            params,
                        )
            except Exception as e:
                logger.error(f"Failed to batch-upsert tournament player stats: {e}")

    async def sync_matches_for_tournament(self, tournament_id: int) -> int:
        """Auto-add any qualifying matches to the tournament."""
        await self._ensure_tournament_league_schema()
        tournament = await self.get_tournament(tournament_id)
        if not tournament or tournament.get("status") != "active":
            return 0

        team_ids = await self.get_tournament_team_ids(tournament_id)
        if not team_ids:
            return 0
        team_name_map = await self._get_tournament_team_name_map(tournament_id)

        matches = await self.pool.fetch(
            """
            SELECT id, datetime, home_team_name, away_team_name, home_guild_id, away_guild_id
            FROM MATCH_STATS
            WHERE game_type = $1
              AND datetime >= $2
              AND NOT EXISTS (
                  SELECT 1
                  FROM TOURNAMENT_FIXTURES f
                  WHERE f.tournament_id = $3
                    AND f.played_match_stats_id = MATCH_STATS.id
              )
            ORDER BY datetime ASC
            """,
            tournament.get("format"),
            tournament.get("created_at"),
            tournament_id
        )

        async def _pick_autosync_fixture_id(
            home_gid: int,
            away_gid: int,
            match_dt: Optional[datetime],
        ) -> Optional[int]:
            rows = await self.pool.fetch(
                """
                SELECT
                    f.id,
                    (
                        SELECT s.proposed_time
                        FROM TOURNAMENT_SCHEDULES s
                        WHERE s.fixture_id = f.id
                          AND s.status = 'confirmed'
                        ORDER BY COALESCE(s.updated_at, s.created_at) DESC, s.id DESC
                        LIMIT 1
                    ) AS confirmed_time
                FROM TOURNAMENT_FIXTURES f
                WHERE f.tournament_id = $1
                  AND COALESCE(f.is_played, FALSE) = FALSE
                  AND COALESCE(f.is_draw_home, FALSE) = FALSE
                  AND COALESCE(f.is_draw_away, FALSE) = FALSE
                  AND COALESCE(f.is_forfeit_home, FALSE) = FALSE
                  AND COALESCE(f.is_forfeit_away, FALSE) = FALSE
                  AND (
                        (f.home_guild_id = $2 AND f.away_guild_id = $3)
                     OR (f.home_guild_id = $3 AND f.away_guild_id = $2)
                  )
                ORDER BY f.week_number ASC NULLS LAST, f.id ASC
                """,
                tournament_id,
                home_gid,
                away_gid,
            )
            candidates = [dict(r) for r in rows]
            if not candidates:
                return None

            # Optional strict mode: only auto-link when a confirmed schedule exists
            # and match time is close to that scheduled slot.
            if TOURNAMENT_AUTO_SYNC_REQUIRE_CONFIRMED_SCHEDULE:
                if not isinstance(match_dt, datetime):
                    return None
                match_utc = (
                    match_dt.astimezone(timezone.utc)
                    if match_dt.tzinfo
                    else match_dt.replace(tzinfo=timezone.utc)
                )
                limit_seconds = TOURNAMENT_AUTO_SYNC_MATCH_WINDOW_HOURS * 3600
                eligible: list[tuple[float, int]] = []
                for cand in candidates:
                    ctime = cand.get("confirmed_time")
                    if not isinstance(ctime, datetime):
                        continue
                    ctime_utc = (
                        ctime.astimezone(timezone.utc)
                        if ctime.tzinfo
                        else ctime.replace(tzinfo=timezone.utc)
                    )
                    delta = abs((match_utc - ctime_utc).total_seconds())
                    if delta <= limit_seconds:
                        eligible.append((delta, int(cand["id"])))
                if len(eligible) != 1:
                    return None
                eligible.sort(key=lambda item: item[0])
                return eligible[0][1]

            # Non-strict mode fallback: only auto-link when exactly one open fixture
            # exists for this pair.
            if len(candidates) != 1:
                return None
            return int(candidates[0]["id"])

        added = 0
        for row in matches:
            home_id = row.get("home_guild_id")
            away_id = row.get("away_guild_id")
            if home_id not in team_ids:
                home_id = self._resolve_team_id_by_name(row.get("home_team_name"), team_ids, team_name_map, threshold=0.8)
            if away_id not in team_ids:
                away_id = self._resolve_team_id_by_name(row.get("away_team_name"), team_ids, team_name_map, threshold=0.8)

            if home_id in team_ids and away_id in team_ids:
                preferred_fixture_id = await _pick_autosync_fixture_id(
                    int(home_id),
                    int(away_id),
                    row.get("datetime"),
                )
                if not preferred_fixture_id:
                    continue
                if await self.add_match_by_id(
                    tournament_id,
                    row["id"],
                    preferred_fixture_id=preferred_fixture_id,
                    tournament=tournament,
                    team_ids=team_ids,
                    team_name_map=team_name_map,
                ):
                    added += 1
        return added

    async def sync_matches_for_all_active(self) -> Dict[str, int]:
        """Auto-add matches for all active tournaments."""
        tournaments = await self.list_tournaments(status="active")
        result = {"tournaments": len(tournaments), "matches_added": 0}
        for t in tournaments:
            try:
                result["matches_added"] += await self.sync_matches_for_tournament(t["id"])
            except Exception as e:
                logger.error(f"Failed to sync matches for tournament {t['id']}: {e}")
        return result

    async def get_standings(self, tournament_id: int) -> List[Dict[str, Any]]:
        await self._ensure_tournament_league_schema()
        rows = await self.pool.fetch(
            """
            WITH teams AS (
                SELECT
                    tt.guild_id,
                    COALESCE(tt.league_key, 'A') AS league_key,
                    COALESCE(tt.team_name_snapshot, it.guild_name, CONCAT('Team ', tt.guild_id::text)) AS team_name_snapshot,
                    COALESCE(tt.team_icon_snapshot, it.guild_icon, '') AS team_icon
                FROM TOURNAMENT_TEAMS tt
                LEFT JOIN IOSCA_TEAMS it ON it.guild_id = tt.guild_id
                WHERE tt.tournament_id = $1
            ),
            fixture_base AS (
                SELECT
                    f.id,
                    COALESCE(f.league_key, 'A') AS league_key,
                    f.home_guild_id,
                    f.away_guild_id,
                    COALESCE(ht.guild_name, f.home_name_raw, '') AS home_name,
                    COALESCE(at.guild_name, f.away_name_raw, '') AS away_name,
                    f.played_match_stats_id,
                    COALESCE(f.is_draw_home, FALSE) AS is_draw_home,
                    COALESCE(f.is_draw_away, FALSE) AS is_draw_away,
                    COALESCE(f.is_forfeit_home, FALSE) AS is_forfeit_home,
                    COALESCE(f.is_forfeit_away, FALSE) AS is_forfeit_away,
                    COALESCE(f.forfeit_score, 10)::int AS forfeit_score
                FROM TOURNAMENT_FIXTURES f
                LEFT JOIN IOSCA_TEAMS ht ON ht.guild_id = f.home_guild_id
                LEFT JOIN IOSCA_TEAMS at ON at.guild_id = f.away_guild_id
                WHERE f.tournament_id = $1
                  AND COALESCE(NULLIF(lower(trim(f.stage_type)), ''), 'league') = 'league'
                  AND f.home_guild_id IS NOT NULL
                  AND f.away_guild_id IS NOT NULL
                  AND (
                        COALESCE(f.is_played, FALSE) = TRUE
                     OR f.played_match_stats_id IS NOT NULL
                     OR COALESCE(f.is_draw_home, FALSE) = TRUE
                     OR COALESCE(f.is_draw_away, FALSE) = TRUE
                     OR COALESCE(f.is_forfeit_home, FALSE) = TRUE
                     OR COALESCE(f.is_forfeit_away, FALSE) = TRUE
                  )
            ),
            played_matches AS (
                SELECT
                    fb.league_key,
                    fb.home_guild_id AS home_id,
                    fb.away_guild_id AS away_id,
                    CASE
                        WHEN m.home_guild_id = fb.home_guild_id THEN COALESCE(m.home_score, 0)::int
                        WHEN m.away_guild_id = fb.home_guild_id THEN COALESCE(m.away_score, 0)::int
                        WHEN regexp_replace(lower(COALESCE(m.home_team_name, '')), '[^a-z0-9]+', '', 'g')
                           = regexp_replace(lower(COALESCE(fb.home_name, '')), '[^a-z0-9]+', '', 'g')
                        THEN COALESCE(m.home_score, 0)::int
                        WHEN regexp_replace(lower(COALESCE(m.away_team_name, '')), '[^a-z0-9]+', '', 'g')
                           = regexp_replace(lower(COALESCE(fb.home_name, '')), '[^a-z0-9]+', '', 'g')
                        THEN COALESCE(m.away_score, 0)::int
                        ELSE COALESCE(m.home_score, 0)::int
                    END AS home_score,
                    CASE
                        WHEN m.home_guild_id = fb.away_guild_id THEN COALESCE(m.home_score, 0)::int
                        WHEN m.away_guild_id = fb.away_guild_id THEN COALESCE(m.away_score, 0)::int
                        WHEN regexp_replace(lower(COALESCE(m.home_team_name, '')), '[^a-z0-9]+', '', 'g')
                           = regexp_replace(lower(COALESCE(fb.away_name, '')), '[^a-z0-9]+', '', 'g')
                        THEN COALESCE(m.home_score, 0)::int
                        WHEN regexp_replace(lower(COALESCE(m.away_team_name, '')), '[^a-z0-9]+', '', 'g')
                           = regexp_replace(lower(COALESCE(fb.away_name, '')), '[^a-z0-9]+', '', 'g')
                        THEN COALESCE(m.away_score, 0)::int
                        ELSE COALESCE(m.away_score, 0)::int
                    END AS away_score
                FROM fixture_base fb
                JOIN MATCH_STATS m ON m.id = fb.played_match_stats_id
                WHERE COALESCE(fb.is_draw_home, FALSE) = FALSE
                  AND COALESCE(fb.is_draw_away, FALSE) = FALSE
                  AND COALESCE(fb.is_forfeit_home, FALSE) = FALSE
                  AND COALESCE(fb.is_forfeit_away, FALSE) = FALSE
            ),
            manual_draw_matches AS (
                SELECT
                    fb.league_key,
                    fb.home_guild_id AS home_id,
                    fb.away_guild_id AS away_id,
                    0::int AS home_score,
                    0::int AS away_score
                FROM fixture_base fb
                WHERE COALESCE(fb.is_draw_home, FALSE) = TRUE
                  AND COALESCE(fb.is_draw_away, FALSE) = TRUE
            ),
            manual_forfeit_matches AS (
                SELECT
                    fb.league_key,
                    fb.home_guild_id AS home_id,
                    fb.away_guild_id AS away_id,
                    CASE WHEN COALESCE(fb.is_forfeit_home, FALSE) THEN 0::int ELSE fb.forfeit_score END AS home_score,
                    CASE WHEN COALESCE(fb.is_forfeit_away, FALSE) THEN 0::int ELSE fb.forfeit_score END AS away_score
                FROM fixture_base fb
                WHERE COALESCE(fb.is_forfeit_home, FALSE) = TRUE
                   OR COALESCE(fb.is_forfeit_away, FALSE) = TRUE
            ),
            all_matches AS (
                SELECT * FROM played_matches
                UNION ALL
                SELECT * FROM manual_draw_matches
                UNION ALL
                SELECT * FROM manual_forfeit_matches
            ),
            team_rows AS (
                SELECT
                    league_key,
                    home_id AS guild_id,
                    1 AS matches_played,
                    CASE WHEN home_score > away_score THEN 1 ELSE 0 END AS wins,
                    CASE WHEN home_score = away_score THEN 1 ELSE 0 END AS draws,
                    CASE WHEN home_score < away_score THEN 1 ELSE 0 END AS losses,
                    home_score AS goals_for,
                    away_score AS goals_against
                FROM all_matches
                UNION ALL
                SELECT
                    league_key,
                    away_id AS guild_id,
                    1 AS matches_played,
                    CASE WHEN away_score > home_score THEN 1 ELSE 0 END AS wins,
                    CASE WHEN away_score = home_score THEN 1 ELSE 0 END AS draws,
                    CASE WHEN away_score < home_score THEN 1 ELSE 0 END AS losses,
                    away_score AS goals_for,
                    home_score AS goals_against
                FROM all_matches
            ),
            agg AS (
                SELECT
                    league_key,
                    guild_id,
                    SUM(matches_played)::int AS matches_played,
                    SUM(wins)::int AS wins,
                    SUM(draws)::int AS draws,
                    SUM(losses)::int AS losses,
                    SUM(goals_for)::int AS goals_for,
                    SUM(goals_against)::int AS goals_against
                FROM team_rows
                GROUP BY league_key, guild_id
            ),
            points_cfg AS (
                SELECT
                    COALESCE(t.points_win, 3)::int AS points_win,
                    COALESCE(t.points_draw, 1)::int AS points_draw,
                    COALESCE(t.points_loss, 0)::int AS points_loss
                FROM TOURNAMENTS t
                WHERE t.id = $1
            )
            SELECT
                t.guild_id,
                t.league_key,
                t.team_name_snapshot,
                t.team_icon,
                COALESCE(a.matches_played, 0) AS matches_played,
                COALESCE(a.wins, 0) AS wins,
                COALESCE(a.draws, 0) AS draws,
                COALESCE(a.losses, 0) AS losses,
                COALESCE(a.goals_for, 0) AS goals_for,
                COALESCE(a.goals_against, 0) AS goals_against,
                (COALESCE(a.goals_for, 0) - COALESCE(a.goals_against, 0)) AS goal_diff,
                (
                    COALESCE(a.wins, 0) * pc.points_win +
                    COALESCE(a.draws, 0) * pc.points_draw +
                    COALESCE(a.losses, 0) * pc.points_loss
                ) AS points
            FROM teams t
            CROSS JOIN points_cfg pc
            LEFT JOIN agg a ON a.guild_id = t.guild_id AND a.league_key = t.league_key
            ORDER BY t.league_key ASC, points DESC, goal_diff DESC, goals_for DESC, t.team_name_snapshot ASC
            """,
            tournament_id
        )
        return [dict(r) for r in rows]

    async def get_player_leaders(self, tournament_id: int) -> Dict[str, List[Dict[str, Any]]]:
        """Return top leaders with Discord identity resolved from IOSCA_PLAYERS.

        Prefer aggregating from source match rows linked through fixture results.
        This avoids stale values when TOURNAMENT_PLAYER_STATS is out of sync.

        All four metrics (goals/assists/keeper_saves/defenders) come from the
        same per-player aggregation pass and the same name-resolution lookup,
        instead of each metric running its own GROUP BY query plus its own
        IOSCA_PLAYERS lookup -- one shared query instead of four, since the
        underlying per-player totals are identical work either way.
        """
        pmd_match_id_is_text = await self._player_match_id_expects_text()
        pmd_match_join = "pmd.match_id::text = m.match_id::text" if pmd_match_id_is_text else "pmd.match_id = m.id"

        metric_exprs = {
            "goals": "COALESCE(pmd.goals, 0)",
            "assists": "COALESCE(pmd.assists, 0) + COALESCE(pmd.second_assists, 0)",
            "keeper_saves": "COALESCE(pmd.keeper_saves, 0)",
            "defenders": "COALESCE(pmd.tackles, 0) + COALESCE(pmd.interceptions, 0)",
        }

        # Source-of-truth aggregation from fixture-linked played matches --
        # every metric's total computed in one pass, one row per player.
        rows = await self.pool.fetch(
            f"""
            SELECT
                pmd.steam_id,
                SUM({metric_exprs['goals']}) AS goals_total,
                SUM({metric_exprs['assists']}) AS assists_total,
                SUM({metric_exprs['keeper_saves']}) AS keeper_saves_total,
                SUM({metric_exprs['defenders']}) AS defenders_total
            FROM TOURNAMENT_FIXTURES f
            JOIN MATCH_STATS m ON m.id = f.played_match_stats_id
            JOIN PLAYER_MATCH_DATA pmd ON {pmd_match_join}
            WHERE f.tournament_id = $1
              AND f.played_match_stats_id IS NOT NULL
            GROUP BY pmd.steam_id
            """,
            tournament_id
        )

        all_rows = [dict(r) for r in rows]
        for row in all_rows:
            row["player_name"] = None
            row["discord_id"] = None

        # Fallback to cached aggregation table when source fixture-linked rows are missing.
        if not all_rows and await self._has_tournament_player_stats():
            fallback_rows = await self.pool.fetch(
                """
                SELECT
                    steam_id,
                    MAX(player_name) AS player_name,
                    MAX(discord_id) AS discord_id,
                    SUM(goals) AS goals_total,
                    SUM(assists + second_assists) AS assists_total,
                    SUM(keeper_saves) AS keeper_saves_total,
                    SUM(tackles + interceptions) AS defenders_total
                FROM TOURNAMENT_PLAYER_STATS
                WHERE tournament_id = $1
                GROUP BY steam_id
                """,
                tournament_id
            )
            all_rows = [dict(r) for r in fallback_rows]

        if not all_rows:
            return {metric: [] for metric in metric_exprs}

        # Resolve Discord identity once for the union of players across all
        # four metrics, instead of once per metric.
        steam_keys: List[str] = []
        for row in all_rows:
            steam_keys.extend(_steam_id_aliases(row.get("steam_id")))
        steam_keys = list(dict.fromkeys([str(k).strip().lower() for k in steam_keys if str(k).strip()]))

        players_by_steam: Dict[str, Dict[str, Any]] = {}
        if steam_keys:
            name_col = await self._get_iosca_players_name_column()
            if name_col:
                player_query = f"""
                SELECT steam_id, discord_id, {name_col} AS discord_name
                FROM IOSCA_PLAYERS
                WHERE lower(trim(steam_id::text)) = ANY($1::text[])
                """
            else:
                player_query = """
                SELECT steam_id, discord_id, NULL::text AS discord_name
                FROM IOSCA_PLAYERS
                WHERE lower(trim(steam_id::text)) = ANY($1::text[])
                """
            player_rows = await self.pool.fetch(player_query, steam_keys)
            for p in player_rows:
                pd = dict(p)
                for sid_alias in _steam_id_aliases(pd.get("steam_id")):
                    key = str(sid_alias or "").strip().lower()
                    if key:
                        players_by_steam[key] = pd

        for row in all_rows:
            player = None
            for sid_alias in _steam_id_aliases(row.get("steam_id")):
                player = players_by_steam.get(str(sid_alias or "").strip().lower())
                if player:
                    break

            resolved_discord_id = (player.get("discord_id") if player else None) or row.get("discord_id")
            resolved_discord_name = player.get("discord_name") if player else None
            if not resolved_discord_name:
                candidate_name = row.get("player_name")
                if candidate_name and not _looks_like_steam_id(candidate_name):
                    resolved_discord_name = candidate_name

            row["discord_id"] = resolved_discord_id
            row["discord_name"] = resolved_discord_name

        # Each metric gets its own top-10 view (sorted by its own total),
        # built from the same shared rows rather than a separate query. The
        # original per-metric queries didn't filter out zero/negative totals
        # either (just ORDER BY total DESC LIMIT 25, then [:10] in Python),
        # so this doesn't filter them out here.
        result: Dict[str, List[Dict[str, Any]]] = {}
        for metric in metric_exprs:
            total_key = f"{metric}_total"
            ranked = sorted(all_rows, key=lambda r: r.get(total_key) or 0, reverse=True)[:10]
            result[metric] = [{**row, "total": row.get(total_key)} for row in ranked]

        return result

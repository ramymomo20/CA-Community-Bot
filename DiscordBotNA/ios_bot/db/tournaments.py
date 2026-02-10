"""
Tournament operations for PostgreSQL database
"""

import json
import logging
import re
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone
from .connection import DatabasePool
from .utils import find_best_match

logger = logging.getLogger(__name__)


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


class TournamentOperations:
    """Handles all tournament-related database operations"""

    def __init__(self, pool: DatabasePool):
        self.pool = pool
        self._player_match_id_is_text = None
        self._iosca_players_name_column = None

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

    async def create_tournament(
        self,
        name: str,
        format: str,
        num_teams: int,
        created_by: Optional[int] = None,
        points_win: int = 3,
        points_draw: int = 1,
        points_loss: int = 0,
    ) -> Optional[int]:
        query = """
        INSERT INTO TOURNAMENTS
            (name, format, num_teams, created_by, points_win, points_draw, points_loss)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
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
            )
        except Exception as e:
            logger.error(f"Failed to create tournament: {e}")
            return None

    async def list_tournaments(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if status:
            query = "SELECT * FROM TOURNAMENTS WHERE status = $1 ORDER BY created_at DESC"
            rows = await self.pool.fetch(query, status)
        else:
            query = "SELECT * FROM TOURNAMENTS ORDER BY created_at DESC"
            rows = await self.pool.fetch(query)
        return [dict(r) for r in rows]

    async def get_tournament(self, tournament_id: int) -> Optional[Dict[str, Any]]:
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

    async def add_teams(self, tournament_id: int, guild_ids: List[int]) -> int:
        """Add teams to tournament. Returns count added."""
        if not guild_ids:
            return 0

        teams_query = """
        SELECT guild_id, guild_name, guild_icon
        FROM IOSCA_TEAMS
        WHERE guild_id = ANY($1::bigint[])
        """
        teams = await self.pool.fetch(teams_query, guild_ids)
        if not teams:
            return 0

        added = 0
        for team in teams:
            try:
                await self.pool.execute(
                    """
                    INSERT INTO TOURNAMENT_TEAMS
                        (tournament_id, guild_id, team_name_snapshot, team_icon_snapshot)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (tournament_id, guild_id) DO NOTHING
                    """,
                    tournament_id,
                    team["guild_id"],
                    team["guild_name"],
                    team.get("guild_icon")
                )
                # Ensure standings row exists
                await self.pool.execute(
                    """
                    INSERT INTO TOURNAMENT_STANDINGS (tournament_id, guild_id)
                    VALUES ($1, $2)
                    ON CONFLICT (tournament_id, guild_id) DO NOTHING
                    """,
                    tournament_id,
                    team["guild_id"]
                )
                added += 1
            except Exception as e:
                logger.error(f"Failed to add team {team['guild_id']} to tournament {tournament_id}: {e}")
                continue
        return added

    async def get_tournament_teams(self, tournament_id: int) -> List[Dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT * FROM TOURNAMENT_TEAMS WHERE tournament_id = $1 ORDER BY team_name_snapshot ASC",
            tournament_id
        )
        return [dict(r) for r in rows]

    async def get_tournament_team_ids(self, tournament_id: int) -> List[int]:
        rows = await self.pool.fetch(
            "SELECT guild_id FROM TOURNAMENT_TEAMS WHERE tournament_id = $1 AND guild_id IS NOT NULL",
            tournament_id
        )
        return [r["guild_id"] for r in rows]

    async def _get_tournament_team_name_map(self, tournament_id: int) -> List[Dict[str, Any]]:
        try:
            rows = await self.pool.fetch(
                """
                SELECT tt.guild_id,
                       tt.team_name_snapshot as guild_name,
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
                       tt.team_name_snapshot as guild_name
                FROM TOURNAMENT_TEAMS tt
                WHERE tt.tournament_id = $1
                """,
                tournament_id
            )
        candidates: List[Dict[str, Any]] = []
        for r in rows:
            data = dict(r)
            candidates.append({"guild_id": data["guild_id"], "guild_name": data.get("guild_name")})
            try:
                nicknames = data.get("nicknames")
                if isinstance(nicknames, str):
                    nicknames = json.loads(nicknames)
                if isinstance(nicknames, list):
                    for name in nicknames:
                        if name:
                            candidates.append({"guild_id": data["guild_id"], "guild_name": name})
            except Exception:
                continue
        return candidates

    def _resolve_team_id_by_name(
        self,
        match_name: Optional[str],
        team_ids: List[int],
        team_name_map: List[Dict[str, Any]],
        threshold: float = 0.8
    ) -> Optional[int]:
        """Resolve a team id using substring and fuzzy matching."""
        if not match_name:
            return None

        match_name_l = match_name.lower()
        best_sub = None
        best_len = 0

        for candidate in team_name_map:
            cand_id = candidate.get("guild_id")
            cand_name = candidate.get("guild_name")
            if cand_id not in team_ids or not cand_name:
                continue
            cand_l = cand_name.lower()
            if cand_l and cand_l in match_name_l:
                if len(cand_l) > best_len:
                    best_len = len(cand_l)
                    best_sub = cand_id

        if best_sub is not None:
            return best_sub

        best = find_best_match(match_name, team_name_map, threshold=threshold)
        if best:
            return best.get("guild_id")
        return None

    def _clean_fixture_text_line(self, line: str) -> str:
        # Strip emoji tokens like :emoji:
        line = re.sub(r":[^:\s]+:", "", line)
        line = re.sub(r"\s+", " ", line)
        return line.strip()

    def _parse_fixtures_text(self, text: str) -> List[Tuple[int, str, str, str]]:
        """Return list of (week_number, week_label, home_name, away_name)."""
        fixtures = []
        current_week = None
        current_label = None

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = self._clean_fixture_text_line(line)

            week_match = re.search(r"Jornada\s+(\d+)", line, flags=re.IGNORECASE)
            if week_match:
                current_week = int(week_match.group(1))
                current_label = line
                continue

            if " vs " in line.lower():
                parts = re.split(r"\s+vs\s+", line, flags=re.IGNORECASE)
                if len(parts) >= 2:
                    home_name = parts[0].strip(" -•\t")
                    away_name = parts[1].strip(" -•\t")
                    if home_name and away_name and current_week is not None:
                        fixtures.append((current_week, current_label or f"Jornada {current_week}", home_name, away_name))

        return fixtures

    async def add_fixtures_from_text(self, tournament_id: int, text: str, threshold: float = 0.7) -> Dict[str, int]:
        """Parse and insert fixtures for a tournament."""
        fixtures = self._parse_fixtures_text(text)
        if not fixtures:
            return {"added": 0, "skipped": 0}

        team_rows = await self.pool.fetch(
            "SELECT guild_id, guild_name FROM IOSCA_TEAMS"
        )
        all_teams = [dict(r) for r in team_rows]
        added = 0
        skipped = 0

        for week_number, week_label, home_name, away_name in fixtures:
            best_home = find_best_match(home_name, all_teams, threshold=threshold)
            best_away = find_best_match(away_name, all_teams, threshold=threshold)
            home_id = best_home["guild_id"] if best_home else None
            away_id = best_away["guild_id"] if best_away else None

            try:
                await self.pool.execute(
                    """
                    INSERT INTO TOURNAMENT_FIXTURES
                        (tournament_id, week_number, week_label, home_guild_id, away_guild_id, home_name_raw, away_name_raw)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT DO NOTHING
                    """,
                    tournament_id,
                    week_number,
                    week_label,
                    home_id,
                    away_id,
                    home_name,
                    away_name
                )
                added += 1
            except Exception as e:
                logger.error(f"Failed to insert fixture {home_name} vs {away_name}: {e}")
                skipped += 1

        return {"added": added, "skipped": skipped}

    async def get_fixtures_for_week(self, tournament_id: int, week_number: int) -> List[Dict[str, Any]]:
        rows = await self.pool.fetch(
            """
            SELECT * FROM TOURNAMENT_FIXTURES
            WHERE tournament_id = $1 AND week_number = $2
            ORDER BY id ASC
            """,
            tournament_id,
            week_number
        )
        return [dict(r) for r in rows]

    async def get_week_numbers(self, tournament_id: int) -> List[int]:
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

    async def get_open_fixtures_for_team(self, tournament_id: int, guild_id: int) -> List[Dict[str, Any]]:
        rows = await self.pool.fetch(
            """
            SELECT f.*
            FROM TOURNAMENT_FIXTURES f
            WHERE f.tournament_id = $1
              AND COALESCE(f.is_played, FALSE) = FALSE
              AND COALESCE(f.is_active, TRUE) = TRUE
              AND NOT EXISTS (
                  SELECT 1 FROM TOURNAMENT_SCHEDULES s
                  WHERE s.fixture_id = f.id AND s.status IN ('pending', 'countered', 'confirmed')
              )
            ORDER BY f.week_number ASC, f.id ASC
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

    async def get_open_fixtures(self, tournament_id: int) -> List[Dict[str, Any]]:
        rows = await self.pool.fetch(
            """
            SELECT f.*
            FROM TOURNAMENT_FIXTURES f
            WHERE f.tournament_id = $1
              AND COALESCE(f.is_played, FALSE) = FALSE
              AND COALESCE(f.is_active, TRUE) = TRUE
              AND NOT EXISTS (
                  SELECT 1 FROM TOURNAMENT_SCHEDULES s
                  WHERE s.fixture_id = f.id AND s.status IN ('pending', 'countered', 'confirmed')
              )
            ORDER BY f.week_number ASC, f.id ASC
            """,
            tournament_id
        )
        return [dict(r) for r in rows]

    async def link_fixture_team_ids(self, tournament_id: Optional[int] = None) -> Dict[str, int]:
        """Backfill fixture home/away guild ids from IOSCA_TEAMS where names match."""
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
        rows = await self.pool.fetch(
            """
            SELECT s.*, f.home_guild_id, f.away_guild_id, f.home_name_raw, f.away_name_raw, f.week_number, f.week_label,
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
        row = await self.pool.fetchrow(
            """
            SELECT s.*, f.home_guild_id, f.away_guild_id, f.home_name_raw, f.away_name_raw, f.week_number, f.week_label,
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
        query = """
        SELECT s.*, f.home_guild_id, f.away_guild_id, f.home_name_raw, f.away_name_raw, f.week_number, f.week_label,
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
        rows = await self.pool.fetch(
            """
            SELECT s.*,
                   f.home_guild_id, f.away_guild_id, f.home_name_raw, f.away_name_raw, f.week_label,
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
        # prevent duplicate forfeits for fixture
        existing = await self.pool.fetchval(
            "SELECT 1 FROM TOURNAMENT_FORFEITS WHERE tournament_id = $1 AND fixture_id = $2",
            tournament_id,
            fixture_id
        )
        if existing:
            return False

        try:
            await self.pool.execute(
                """
                INSERT INTO TOURNAMENT_FORFEITS
                    (tournament_id, fixture_id, forfeiting_guild_id, winner_guild_id, score_forfeit, created_by)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                tournament_id,
                fixture_id,
                forfeiting_guild_id,
                winner_guild_id,
                score_forfeit,
                created_by
            )
        except Exception as e:
            logger.error(f"Failed to add forfeit: {e}")
            return False

        # Apply to standings as a 10-0 win
        await self._apply_match_to_standings(tournament_id, winner_guild_id, forfeiting_guild_id, score_forfeit, 0)
        try:
            await self.pool.execute(
                """
                UPDATE TOURNAMENT_FIXTURES
                SET is_played = TRUE,
                    is_active = FALSE,
                    played_at = NOW()
                WHERE id = $1
                """,
                fixture_id
            )
        except Exception as e:
            logger.error(f"Failed to mark forfeited fixture as played ({fixture_id}): {e}")
        return True

    async def _mark_fixture_played_for_match(
        self,
        tournament_id: int,
        match_stats_id: int,
        played_at: Optional[datetime],
        home_id: int,
        away_id: int
    ) -> Optional[int]:
        # Prefer exact home/away orientation.
        fixture_id = await self.pool.fetchval(
            """
            SELECT id
            FROM TOURNAMENT_FIXTURES
            WHERE tournament_id = $1
              AND COALESCE(is_played, FALSE) = FALSE
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
                played_match_stats_id = $2,
                played_at = COALESCE($3, played_at, NOW())
            WHERE id = $1
            """,
            fixture_id,
            match_stats_id,
            self._normalize_naive_utc(played_at) if played_at else None
        )
        return fixture_id

    async def add_match_by_id(self, tournament_id: int, match_stats_id: int, name_match_threshold: float = 0.8) -> bool:
        """Add a single match to a tournament and update standings/player stats."""
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

        team_ids = await self.get_tournament_team_ids(tournament_id)
        if not team_ids:
            return False

        home_id = match.get("home_guild_id")
        away_id = match.get("away_guild_id")

        # If IDs missing or not in tournament, try name match
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

        # Insert match (ignore if already exists)
        try:
            inserted = await self.pool.fetchval(
                """
                INSERT INTO TOURNAMENT_MATCHES
                    (tournament_id, match_stats_id, match_key, home_guild_id, away_guild_id,
                     home_score, away_score, game_type, played_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (tournament_id, match_stats_id) DO NOTHING
                RETURNING match_stats_id
                """,
                tournament_id,
                match["id"],
                match.get("match_id"),
                home_id,
                away_id,
                match.get("home_score", 0),
                match.get("away_score", 0),
                match.get("game_type"),
                match.get("datetime")
            )
        except Exception as e:
            logger.error(f"Failed to insert tournament match {match_stats_id}: {e}")
            return False

        if not inserted:
            return False

        # Update standings and player stats
        await self._apply_match_to_standings(tournament_id, home_id, away_id, match.get("home_score", 0), match.get("away_score", 0))
        await self._apply_match_to_player_stats(tournament_id, match, home_id, away_id)
        try:
            fixture_id = await self._mark_fixture_played_for_match(
                tournament_id=tournament_id,
                match_stats_id=match_stats_id,
                played_at=match.get("datetime"),
                home_id=home_id,
                away_id=away_id
            )
            if not fixture_id:
                # If fixtures were added with names only, backfill team links and retry.
                await self.link_fixture_team_ids(tournament_id=tournament_id)
                await self._mark_fixture_played_for_match(
                    tournament_id=tournament_id,
                    match_stats_id=match_stats_id,
                    played_at=match.get("datetime"),
                    home_id=home_id,
                    away_id=away_id
                )
        except Exception as e:
            logger.error(f"Failed marking fixture completed for match {match_stats_id}: {e}")
        return True

    async def _apply_match_to_standings(self, tournament_id: int, home_id: int, away_id: int, home_score: int, away_score: int):
        tournament = await self.get_tournament(tournament_id)
        if not tournament:
            return
        points_win = tournament.get("points_win", 3)
        points_draw = tournament.get("points_draw", 1)
        points_loss = tournament.get("points_loss", 0)

        if home_score > away_score:
            home_w, home_d, home_l = 1, 0, 0
            away_w, away_d, away_l = 0, 0, 1
            home_pts, away_pts = points_win, points_loss
        elif home_score < away_score:
            home_w, home_d, home_l = 0, 0, 1
            away_w, away_d, away_l = 1, 0, 0
            home_pts, away_pts = points_loss, points_win
        else:
            home_w, home_d, home_l = 0, 1, 0
            away_w, away_d, away_l = 0, 1, 0
            home_pts = away_pts = points_draw

        home_gf = home_score
        home_ga = away_score
        away_gf = away_score
        away_ga = home_score

        await self.pool.execute(
            """
            INSERT INTO TOURNAMENT_STANDINGS
                (tournament_id, guild_id, wins, draws, losses, goals_for, goals_against, goal_diff, points, matches_played)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 1)
            ON CONFLICT (tournament_id, guild_id) DO UPDATE SET
                wins = TOURNAMENT_STANDINGS.wins + EXCLUDED.wins,
                draws = TOURNAMENT_STANDINGS.draws + EXCLUDED.draws,
                losses = TOURNAMENT_STANDINGS.losses + EXCLUDED.losses,
                goals_for = TOURNAMENT_STANDINGS.goals_for + EXCLUDED.goals_for,
                goals_against = TOURNAMENT_STANDINGS.goals_against + EXCLUDED.goals_against,
                goal_diff = TOURNAMENT_STANDINGS.goal_diff + EXCLUDED.goal_diff,
                points = TOURNAMENT_STANDINGS.points + EXCLUDED.points,
                matches_played = TOURNAMENT_STANDINGS.matches_played + 1
            """,
            tournament_id,
            home_id,
            home_w,
            home_d,
            home_l,
            home_gf,
            home_ga,
            home_gf - home_ga,
            home_pts
        )

        await self.pool.execute(
            """
            INSERT INTO TOURNAMENT_STANDINGS
                (tournament_id, guild_id, wins, draws, losses, goals_for, goals_against, goal_diff, points, matches_played)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 1)
            ON CONFLICT (tournament_id, guild_id) DO UPDATE SET
                wins = TOURNAMENT_STANDINGS.wins + EXCLUDED.wins,
                draws = TOURNAMENT_STANDINGS.draws + EXCLUDED.draws,
                losses = TOURNAMENT_STANDINGS.losses + EXCLUDED.losses,
                goals_for = TOURNAMENT_STANDINGS.goals_for + EXCLUDED.goals_for,
                goals_against = TOURNAMENT_STANDINGS.goals_against + EXCLUDED.goals_against,
                goal_diff = TOURNAMENT_STANDINGS.goal_diff + EXCLUDED.goal_diff,
                points = TOURNAMENT_STANDINGS.points + EXCLUDED.points,
                matches_played = TOURNAMENT_STANDINGS.matches_played + 1
            """,
            tournament_id,
            away_id,
            away_w,
            away_d,
            away_l,
            away_gf,
            away_ga,
            away_gf - away_ga,
            away_pts
        )

    async def _apply_match_to_player_stats(self, tournament_id: int, match: Dict[str, Any], home_id: int, away_id: int):
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

        for agg in aggregates.values():
            try:
                await self.pool.execute(
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
                    tournament_id,
                    agg["steam_id"],
                    agg.get("discord_id"),
                    agg.get("discord_name"),
                    agg["team_guild_id"],
                    agg["goals"],
                    agg["assists"],
                    agg["second_assists"],
                    agg["keeper_saves"],
                    agg["tackles"],
                    agg["interceptions"],
                    agg["matches_played"]
                )
            except Exception as e:
                logger.error(f"Failed to upsert tournament player stats: {e}")

    async def sync_matches_for_tournament(self, tournament_id: int) -> int:
        """Auto-add any qualifying matches to the tournament."""
        tournament = await self.get_tournament(tournament_id)
        if not tournament or tournament.get("status") != "active":
            return 0

        team_ids = await self.get_tournament_team_ids(tournament_id)
        if not team_ids:
            return 0
        team_name_map = await self._get_tournament_team_name_map(tournament_id)

        matches = await self.pool.fetch(
            """
            SELECT id, home_team_name, away_team_name, home_guild_id, away_guild_id
            FROM MATCH_STATS
            WHERE game_type = $1
              AND datetime >= $2
              AND NOT EXISTS (
                  SELECT 1 FROM TOURNAMENT_MATCHES tm
                  WHERE tm.tournament_id = $3 AND tm.match_stats_id = MATCH_STATS.id
              )
            ORDER BY datetime ASC
            """,
            tournament.get("format"),
            tournament.get("created_at"),
            tournament_id
        )

        added = 0
        for row in matches:
            home_id = row.get("home_guild_id")
            away_id = row.get("away_guild_id")
            if home_id not in team_ids:
                home_id = self._resolve_team_id_by_name(row.get("home_team_name"), team_ids, team_name_map, threshold=0.8)
            if away_id not in team_ids:
                away_id = self._resolve_team_id_by_name(row.get("away_team_name"), team_ids, team_name_map, threshold=0.8)

            if home_id in team_ids and away_id in team_ids:
                if await self.add_match_by_id(tournament_id, row["id"]):
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
        rows = await self.pool.fetch(
            """
            SELECT ts.*, tt.team_name_snapshot
            FROM TOURNAMENT_STANDINGS ts
            LEFT JOIN TOURNAMENT_TEAMS tt
              ON ts.tournament_id = tt.tournament_id AND ts.guild_id = tt.guild_id
            WHERE ts.tournament_id = $1
            ORDER BY ts.points DESC, ts.goal_diff DESC, ts.goals_for DESC, tt.team_name_snapshot ASC
            """,
            tournament_id
        )
        return [dict(r) for r in rows]

    async def get_player_leaders(self, tournament_id: int) -> Dict[str, List[Dict[str, Any]]]:
        """Return top leaders with Discord identity resolved from IOSCA_PLAYERS.

        Prefer aggregating from source match rows linked through TOURNAMENT_MATCHES.
        This avoids stale values when TOURNAMENT_PLAYER_STATS is out of sync.
        """
        pmd_match_id_is_text = await self._player_match_id_expects_text()
        pmd_match_join = "pmd.match_id::text = m.match_id::text" if pmd_match_id_is_text else "pmd.match_id = m.id"

        async def _fetch_metric(total_expr: str) -> List[Dict[str, Any]]:
            # Source-of-truth aggregation from player_match_data tied to tournament matches.
            rows = await self.pool.fetch(
                f"""
                SELECT
                    pmd.steam_id,
                    SUM({total_expr}) AS total
                FROM TOURNAMENT_MATCHES tm
                JOIN MATCH_STATS m ON m.id = tm.match_stats_id
                JOIN PLAYER_MATCH_DATA pmd ON {pmd_match_join}
                WHERE tm.tournament_id = $1
                GROUP BY pmd.steam_id
                ORDER BY total DESC
                LIMIT 25
                """,
                tournament_id
            )

            leaders_rows = [dict(r) for r in rows]
            for row in leaders_rows:
                row["player_name"] = None
                row["discord_id"] = None

            # Fallback to cached aggregation table when tournament_matches isn't populated.
            if not leaders_rows:
                fallback_rows = await self.pool.fetch(
                    f"""
                    SELECT
                        steam_id,
                        MAX(player_name) AS player_name,
                        MAX(discord_id) AS discord_id,
                        SUM({total_expr}) AS total
                    FROM TOURNAMENT_PLAYER_STATS
                    WHERE tournament_id = $1
                    GROUP BY steam_id
                    ORDER BY total DESC
                    LIMIT 25
                    """,
                    tournament_id
                )
                leaders_rows = [dict(r) for r in fallback_rows]

            if not leaders_rows:
                return []

            steam_keys: List[str] = []
            for row in leaders_rows:
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

            for row in leaders_rows:
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

            return leaders_rows[:10]

        return {
            "goals": await _fetch_metric("COALESCE(pmd.goals, 0)"),
            "assists": await _fetch_metric("COALESCE(pmd.assists, 0) + COALESCE(pmd.second_assists, 0)"),
            "keeper_saves": await _fetch_metric("COALESCE(pmd.keeper_saves, 0)"),
            "defenders": await _fetch_metric("COALESCE(pmd.tackles, 0) + COALESCE(pmd.interceptions, 0)"),
        }

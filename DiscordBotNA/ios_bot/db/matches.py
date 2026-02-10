"""
Match operations for PostgreSQL database
"""

import json
import logging
from ios_bot.config import MAIN_GUILD_ID
from typing import Optional, List, Dict, Any
from datetime import datetime
from .connection import DatabasePool
from .utils import find_best_match

logger = logging.getLogger(__name__)


class MatchOperations:
    """Handles all match-related database operations"""
    
    def __init__(self, pool: DatabasePool):
        self.pool = pool
        self._player_match_id_is_text = None
        self._has_source_filename = None
        self._has_match_id_unique = None
        self._match_stats_guild_id_is_text = None

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
        source_filename: Optional[str] = None
    ) -> Optional[int]:
        """Add a new match to the database. guild_ids can be None for unregistered teams."""
        # Generate match_id if not provided
        if not match_id_str:
            home_id = home_guild_id or 0
            away_id = away_guild_id or 0
            match_id_str = f"{match_datetime.strftime('%Y%m%d%H%M%S')}_{home_id}_{away_id}"
        
        # Conditionally include source_filename column if it exists in the DB
        if self._has_source_filename is None:
            try:
                row = await self.pool.fetchrow(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'match_stats' AND column_name = 'source_filename'
                    """
                )
                self._has_source_filename = bool(row)
            except Exception:
                self._has_source_filename = False

        if self._has_source_filename and source_filename is not None:
            query = """
            INSERT INTO MATCH_STATS (
                match_id, home_guild_id, away_guild_id, home_score, away_score,
                datetime, home_team_name, away_team_name, extratime, penalties,
                substitutions, home_lineup, away_lineup, game_type, source_filename
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            ON CONFLICT (match_id) DO NOTHING
            RETURNING id
            """
            params = [
                match_id_str,
                home_guild_id,
                away_guild_id,
                home_score,
                away_score,
                match_datetime,
                home_team_name,
                away_team_name,
                extratime,
                penalties,
                json.dumps(substitutions or []),
                json.dumps(home_lineup or []),
                json.dumps(away_lineup or []),
                game_type,
                source_filename,
            ]
        else:
            query = """
            INSERT INTO MATCH_STATS (
                match_id, home_guild_id, away_guild_id, home_score, away_score,
                datetime, home_team_name, away_team_name, extratime, penalties,
                substitutions, home_lineup, away_lineup, game_type
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            ON CONFLICT (match_id) DO NOTHING
            RETURNING id
            """
            params = [
                match_id_str,
                home_guild_id,
                away_guild_id,
                home_score,
                away_score,
                match_datetime,
                home_team_name,
                away_team_name,
                extratime,
                penalties,
                json.dumps(substitutions or []),
                json.dumps(home_lineup or []),
                json.dumps(away_lineup or []),
                game_type,
            ]
        
        try:
            db_id = await self.pool.fetchval(query, *params)
            if db_id:
                logger.info(f"✅ Match added: {home_guild_id} vs {away_guild_id} (ID: {db_id})")
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

    async def match_exists(self, match_id: str) -> bool:
        """Lightweight existence check for a match_id."""
        try:
            val = await self.pool.fetchval("SELECT 1 FROM MATCH_STATS WHERE match_id = $1", match_id)
            return bool(val)
        except Exception as e:
            logger.error(f"Error checking match existence for {match_id}: {e}")
            return False
    
    async def get_match(self, match_id: int) -> Optional[Dict[str, Any]]:
        """Get a match by ID"""
        query = """
        SELECT m.*,
               ht.guild_name as home_team_name,
               at.guild_name as away_team_name
        FROM MATCH_STATS m
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
        """Get all matches played by a specific team"""
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
               ht.guild_name as home_team_name,
               at.guild_name as away_team_name
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
        query = """
        SELECT pmd.*,
               COALESCE(p.discord_name, p.username) as player_name
        FROM PLAYER_MATCH_DATA pmd
        LEFT JOIN IOSCA_PLAYERS p ON pmd.steam_id = p.steam_id
        WHERE pmd.match_id = $1
        ORDER BY pmd.goals DESC, pmd.assists DESC
        """
        rows = await self.pool.fetch(query, match_id)
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
            SUM(pmd.passes) as total_passes,
            SUM(pmd.passes_completed) as total_passes_completed,
            SUM(pmd.tackles) as total_tackles,
            SUM(pmd.interceptions) as total_interceptions,
            SUM(pmd.key_passes) as total_key_passes,
            SUM(pmd.chances_created) as total_chances_created,
            SUM(pmd.interceptions) as total_interceptions,
            SUM(pmd.offsides) as total_offsides,
            SUM(pmd.yellow_cards) as total_yellow_cards,
            SUM(pmd.red_cards) as total_red_cards,
            SUM(pmd.own_goals) as total_own_goals,
            SUM(pmd.fouls_suffered) as total_fouls_suffered,
            SUM(pmd.free_kicks) as total_free_kicks,
            SUM(pmd.penalties) as total_penalties,
            SUM(pmd.corners) as total_corners,
            SUM(pmd.throwins) as total_throwins,
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
        pass_accuracy: float = 0.0
    ) -> bool:
        """Add player match data - stores all players by steam_id.
        
        Args:
            match_id: Match ID (integer, references match_stats.id)
            steam_id: Player's Steam ID
            guild_id: Optional - linked team guild ID (NULL if team not registered)
            position: Player's position (GK, LB, CB, etc.)
            All stat columns matching the database schema
        """
        query = """
        INSERT INTO PLAYER_MATCH_DATA (
            match_id, steam_id, guild_id, position,
            goals, assists, second_assists, shots, shots_on_goal,
            passes_completed, passes_attempted, chances_created, key_passes,
            interceptions, tackles, sliding_tackles_completed,
            fouls, yellow_cards, red_cards,
            keeper_saves, keeper_saves_caught, goals_conceded,
            offsides, own_goals,
            fouls_suffered, free_kicks, penalties, corners, throw_ins, goal_kicks, possession,
            time_played, time_gk, time_def, time_mid, time_att,
            distance_covered, pass_accuracy
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 
                $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, 
                $21, $22, $23, $24, $25, $26, $27, $28, $29, $30, $31,
                $32, $33, $34, $35, $36, $37, $38)
        ON CONFLICT DO NOTHING
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
            await self.pool.execute(
                query,
                match_id_value,
                steam_id,
                guild_id,
                position,
                goals,
                assists,
                second_assists,
                shots,
                shots_on_goal,
                passes_completed,
                passes_attempted,
                chances_created,
                key_passes,
                interceptions,
                tackles,
                sliding_tackles_completed,
                fouls,
                yellow_cards,
                red_cards,
                keeper_saves,
                keeper_saves_caught,
                goals_conceded,
                offsides,
                own_goals,
                fouls_suffered,
                free_kicks,
                penalties,
                corners,
                throw_ins,
                goal_kicks,
                possession,
                time_played,
                time_gk,
                time_def,
                time_mid,
                time_att,
                distance_covered,
                pass_accuracy
            )
            return True
        except Exception as e:
            logger.error(f"Failed to add player match data for {steam_id}: {e}")
            return False
    
    async def delete_match(self, match_id: int) -> bool:
        """Delete a match and all associated player data"""
        try:
            async with self.pool.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("DELETE FROM PLAYER_MATCH_DATA WHERE match_id = $1", match_id)
                    await conn.execute("DELETE FROM MATCH_STATS WHERE id = $1", match_id)
            logger.info(f"Match {match_id} deleted")
            return True
        except Exception as e:
            logger.error(f"Failed to delete match: {e}")
            return False
    
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
            league_name=league_name,
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
        """Get comprehensive team statistics"""
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
        
        return {
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

        main_aliases = []
        if MAIN_GUILD_ID:
            main_aliases = [
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
            for alias in main_aliases:
                teams.append({"guild_id": MAIN_GUILD_ID, "guild_name": alias})

        main_alias_set = {a.lower() for a in main_aliases if a}

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
            WHERE home_guild_id IS NULL
               OR away_guild_id IS NULL
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

        for match in match_rows:
            matches_scanned += 1
            home_update_id = None
            away_update_id = None

            home_name = match.get('home_team_name')
            away_name = match.get('away_team_name')

            if home_name:
                home_name_l = str(home_name).strip().lower()
                if home_name_l in main_alias_set and MAIN_GUILD_ID:
                    home_update_id = MAIN_GUILD_ID
                else:
                    exact_id = exact_name_map.get(home_name_l)
                    if exact_id is not None:
                        home_update_id = exact_id
                    elif home_name not in name_cache:
                        best_home = find_best_match(home_name, teams, threshold)
                        name_cache[home_name] = best_home['guild_id'] if best_home else None
                    if home_update_id is None:
                        home_update_id = name_cache[home_name]

            if away_name:
                away_name_l = str(away_name).strip().lower()
                if away_name_l in main_alias_set and MAIN_GUILD_ID:
                    away_update_id = MAIN_GUILD_ID
                else:
                    exact_id = exact_name_map.get(away_name_l)
                    if exact_id is not None:
                        away_update_id = exact_id
                    elif away_name not in name_cache:
                        best_away = find_best_match(away_name, teams, threshold)
                        name_cache[away_name] = best_away['guild_id'] if best_away else None
                    if away_update_id is None:
                        away_update_id = name_cache[away_name]

            if home_update_id is None and away_update_id is None:
                continue

            params = [
                str(home_update_id) if expects_text else home_update_id,
                str(away_update_id) if expects_text else away_update_id,
                match['id'],
            ]
            query = (
                "UPDATE MATCH_STATS "
                "SET home_guild_id = COALESCE($1, home_guild_id), "
                "away_guild_id = COALESCE($2, away_guild_id) "
                "WHERE id = $3 AND (home_guild_id IS NULL OR away_guild_id IS NULL)"
            )

            try:
                result = await self.pool.execute(query, *params)
                if result and result.startswith("UPDATE "):
                    updated_count = int(result.split()[-1])
                else:
                    updated_count = 0
            except Exception as e:
                logger.error(f"Failed to update match {match['id']} team links: {e}")
                updated_count = 0

            if updated_count > 0:
                matches_updated += 1
                if home_update_id is not None:
                    home_linked += 1
                if away_update_id is not None:
                    away_linked += 1

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
        query = """
        SELECT m.id, m.home_guild_id, m.away_guild_id, m.home_lineup, m.away_lineup
        FROM MATCH_STATS m
        WHERE m.home_guild_id IS NOT NULL
          AND m.away_guild_id IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM PLAYER_MATCH_DATA pmd
              WHERE pmd.match_id = m.id AND pmd.guild_id IS NULL
          )
        ORDER BY m.datetime DESC
        """
        if limit_matches and limit_matches > 0:
            query += f" LIMIT {int(limit_matches)}"

        rows = await self.pool.fetch(query)
        matches_scanned = 0
        players_updated = 0

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

            if home_ids:
                result = await self.pool.execute(
                    """
                    UPDATE PLAYER_MATCH_DATA
                    SET guild_id = $1
                    WHERE match_id = $2
                      AND guild_id IS NULL
                      AND steam_id = ANY($3::text[])
                    """,
                    row["home_guild_id"],
                    (str(row["id"]) if expects_text else row["id"]),
                    home_ids
                )
                if result and result.startswith("UPDATE "):
                    players_updated += int(result.split()[-1])

            if away_ids:
                result = await self.pool.execute(
                    """
                    UPDATE PLAYER_MATCH_DATA
                    SET guild_id = $1
                    WHERE match_id = $2
                      AND guild_id IS NULL
                      AND steam_id = ANY($3::text[])
                    """,
                    row["away_guild_id"],
                    (str(row["id"]) if expects_text else row["id"]),
                    away_ids
                )
                if result and result.startswith("UPDATE "):
                    players_updated += int(result.split()[-1])

        return {
            "matches_scanned": matches_scanned,
            "players_updated": players_updated
        }
    
    async def add_active_match(self, channel_id: int, team1_name: str, team2_name: str) -> bool:
        """Legacy function for active match tracking (no longer uses ACTIVE_MATCHES table)"""
        logger.info(f"Active match logged: {team1_name} vs {team2_name} in channel {channel_id}")
        return True

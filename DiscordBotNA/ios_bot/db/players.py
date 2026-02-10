"""
Player operations for PostgreSQL database
"""

import logging
from typing import Optional, Dict, Any
from .connection import DatabasePool

logger = logging.getLogger(__name__)


class PlayerOperations:
    """Handles all player-related database operations"""
    
    def __init__(self, pool: DatabasePool):
        self.pool = pool
        self._discord_id_is_text = None
        self._name_column = None

    async def _discord_id_expects_text(self) -> bool:
        """Detect whether IOSCA_PLAYERS.discord_id is stored as text."""
        if self._discord_id_is_text is not None:
            return self._discord_id_is_text

        try:
            row = await self.pool.fetchrow(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'iosca_players'
                  AND column_name = 'discord_id'
                """
            )
            data_type = row['data_type'] if row else None
            self._discord_id_is_text = data_type in ('character varying', 'text')
        except Exception as e:
            logger.warning(f"Failed to detect iosca_players.discord_id type: {e}")
            self._discord_id_is_text = False

        return self._discord_id_is_text

    async def _coerce_discord_id(self, discord_id) -> object:
        """Coerce discord_id to the correct type for IOSCA_PLAYERS queries."""
        if discord_id is None:
            return None
        expects_text = await self._discord_id_expects_text()
        if expects_text:
            return str(discord_id)
        try:
            return int(discord_id)
        except (TypeError, ValueError):
            return discord_id

    async def _get_name_column(self) -> Optional[str]:
        """Detect whether IOSCA_PLAYERS uses discord_name or username."""
        if self._name_column is not None:
            return self._name_column or None

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
                self._name_column = "discord_name"
            elif "username" in columns:
                self._name_column = "username"
            else:
                self._name_column = ""
        except Exception as e:
            logger.warning(f"Failed to detect IOSCA_PLAYERS name column: {e}")
            self._name_column = ""

        return self._name_column or None
    
    async def register_player(self, discord_id: int, username: str, steam_id: str) -> bool:
        """Insert or update a player's registration"""
        try:
            discord_id_value = await self._coerce_discord_id(discord_id)
            name_column = await self._get_name_column()

            # If a row exists for this steam_id, update that row's discord_id (if empty)
            existing = await self.get_player_by_steam_id(steam_id)
            if existing:
                # Treat None, 0, or empty string as 'not filled' and allow overwrite.
                existing_discord = existing.get("discord_id")
                try:
                    existing_discord_int = int(existing_discord)
                except (TypeError, ValueError):
                    existing_discord_int = None

                is_placeholder = existing_discord_int is not None and existing_discord_int <= 0
                is_filled = (
                    existing_discord is not None
                    and str(existing_discord).strip() != ""
                    and not is_placeholder
                )

                # If filled by a different real discord id, refuse to overwrite
                if is_filled and existing_discord != discord_id_value:
                    logger.info(f"SteamID {steam_id} already linked to different Discord ID {existing_discord}")
                    return False

                # Otherwise update the row (covers empty/0 case or idempotent overwrite)
                if name_column:
                    update_q = f"""
                    UPDATE IOSCA_PLAYERS
                    SET discord_id = $1, {name_column} = $2, updated_at = CURRENT_TIMESTAMP
                    WHERE steam_id = $3
                    """
                    await self.pool.execute(update_q, discord_id_value, username, steam_id)
                else:
                    update_q = """
                    UPDATE IOSCA_PLAYERS
                    SET discord_id = $1, updated_at = CURRENT_TIMESTAMP
                    WHERE steam_id = $2
                    """
                    await self.pool.execute(update_q, discord_id_value, steam_id)
                logger.info(f"✅ Player updated for steam_id: {steam_id} -> discord {discord_id_value}")
                return True

            # No existing steam_id row.
            # Some deployments don't have a unique constraint on discord_id, so avoid ON CONFLICT.
            existing_by_discord = await self.get_player_by_discord_id(discord_id_value)
            if existing_by_discord:
                if name_column:
                    update_discord_q = f"""
                    UPDATE IOSCA_PLAYERS
                    SET {name_column} = $1,
                        steam_id = $2,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE discord_id = $3
                    """
                    await self.pool.execute(update_discord_q, username, steam_id, discord_id_value)
                else:
                    update_discord_q = """
                    UPDATE IOSCA_PLAYERS
                    SET steam_id = $1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE discord_id = $2
                    """
                    await self.pool.execute(update_discord_q, steam_id, discord_id_value)
            else:
                if name_column:
                    insert_q = f"""
                    INSERT INTO IOSCA_PLAYERS (discord_id, {name_column}, steam_id)
                    VALUES ($1, $2, $3)
                    """
                    await self.pool.execute(insert_q, discord_id_value, username, steam_id)
                else:
                    insert_q = """
                    INSERT INTO IOSCA_PLAYERS (discord_id, steam_id)
                    VALUES ($1, $2)
                    """
                    await self.pool.execute(insert_q, discord_id_value, steam_id)
            logger.info(f"✅ Player registered: {username} ({discord_id_value})")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to register player {username}: {e}")
            return False
    
    async def get_player_by_steam_id(self, steam_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a player by their SteamID"""
        query = "SELECT * FROM IOSCA_PLAYERS WHERE steam_id = $1"
        row = await self.pool.fetchrow(query, steam_id)
        return dict(row) if row else None
    
    async def get_player_by_discord_id(self, discord_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve a player's record by their Discord ID"""
        query = "SELECT * FROM IOSCA_PLAYERS WHERE discord_id = $1"
        discord_id_value = await self._coerce_discord_id(discord_id)
        row = await self.pool.fetchrow(query, discord_id_value)
        return dict(row) if row else None
    
    async def update_player_rating(self, discord_id: int, rating: float) -> bool:
        """Update a player's rating"""
        query = "UPDATE IOSCA_PLAYERS SET rating = $1, updated_at = CURRENT_TIMESTAMP WHERE discord_id = $2"
        
        try:
            discord_id_value = await self._coerce_discord_id(discord_id)
            await self.pool.execute(query, rating, discord_id_value)
            return True
        except Exception as e:
            logger.error(f"Failed to update player rating: {e}")
            return False
    
    async def get_player_stats(self, discord_id: int) -> Optional[Dict[str, Any]]:
        """Get player statistics including rating"""
        name_column = await self._get_name_column()
        if name_column:
            query = f"""
            SELECT discord_id, {name_column} AS username, steam_id, rating, created_at, updated_at
            FROM IOSCA_PLAYERS
            WHERE discord_id = $1
            """
        else:
            query = """
            SELECT discord_id, NULL::text AS username, steam_id, rating, created_at, updated_at
            FROM IOSCA_PLAYERS
            WHERE discord_id = $1
            """
        discord_id_value = await self._coerce_discord_id(discord_id)
        row = await self.pool.fetchrow(query, discord_id_value)
        return dict(row) if row else None
    
    async def get_top_players_by_rating(self, limit: int = 10) -> list:
        """Get top players by rating"""
        name_column = await self._get_name_column()
        if name_column:
            query = f"""
            SELECT discord_id, {name_column} AS username, rating
            FROM IOSCA_PLAYERS
            WHERE rating IS NOT NULL
            ORDER BY rating DESC
            LIMIT $1
            """
        else:
            query = """
            SELECT discord_id, NULL::text AS username, rating
            FROM IOSCA_PLAYERS
            WHERE rating IS NOT NULL
            ORDER BY rating DESC
            LIMIT $1
            """
        rows = await self.pool.fetch(query, limit)
        return [dict(row) for row in rows]
    
    async def player_exists(self, discord_id: int) -> bool:
        """Check if a player exists"""
        query = "SELECT EXISTS(SELECT 1 FROM IOSCA_PLAYERS WHERE discord_id = $1)"
        discord_id_value = await self._coerce_discord_id(discord_id)
        return await self.pool.fetchval(query, discord_id_value)
    
    async def delete_player(self, discord_id: int) -> bool:
        """Delete a player record"""
        query = "DELETE FROM IOSCA_PLAYERS WHERE discord_id = $1"
        
        try:
            discord_id_value = await self._coerce_discord_id(discord_id)
            await self.pool.execute(query, discord_id_value)
            logger.info(f"Player {discord_id_value} deleted")
            return True
        except Exception as e:
            logger.error(f"Failed to delete player: {e}")
            return False
    
    async def get_top_team_players(self, guild_id: int, limit: int = 10) -> list:
        """Get top players for a team based on match performance"""
        name_column = await self._get_name_column()
        player_name_expr = f"p.{name_column}" if name_column else "NULL::text"
        query = f"""
        SELECT 
            pmd.steam_id,
            COALESCE({player_name_expr}, pmd.player_name, pmd.steam_id::text) as player_name,
            COUNT(DISTINCT pmd.match_id) as matches_played,
            SUM(pmd.goals) as total_goals,
            SUM(pmd.assists) as total_assists,
            AVG(pmd.pass_accuracy) as avg_pass_accuracy
        FROM PLAYER_MATCH_DATA pmd
        LEFT JOIN IOSCA_PLAYERS p ON pmd.steam_id = p.steam_id
        WHERE pmd.guild_id = $1
        GROUP BY pmd.steam_id, COALESCE({player_name_expr}, pmd.player_name, pmd.steam_id::text)
        ORDER BY matches_played DESC, total_goals DESC
        LIMIT $2
        """
        
        try:
            rows = await self.pool.fetch(query, guild_id, limit)
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get top team players: {e}")
            return []

"""
Team operations for PostgreSQL database
"""

import json
import logging
from typing import Optional, List, Dict, Any
from .utils import find_best_match
from .connection import DatabasePool

logger = logging.getLogger(__name__)


class TeamOperations:
    """Handles all team-related database operations"""
    
    def __init__(self, pool: DatabasePool):
        self.pool = pool

    def _normalize_channel_ids_for_storage(self, values: Optional[List]) -> List[str]:
        """Store channel IDs as strings in JSONB to avoid type churn."""
        if not values:
            return []
        normalized = []
        for value in values:
            if value is None:
                continue
            try:
                normalized.append(str(int(value)))
            except (TypeError, ValueError):
                try:
                    normalized.append(str(value).strip())
                except Exception:
                    continue
        return [v for v in normalized if v]

    def _normalize_channel_ids_for_runtime(self, values: Optional[List]) -> List[int]:
        """Convert stored channel IDs to ints for runtime lookups."""
        if not values:
            return []
        normalized = []
        for value in values:
            try:
                normalized.append(int(value))
            except (TypeError, ValueError):
                continue
        return normalized
    
    async def add_team(
        self,
        guild_id: int,
        guild_name: str,
        guild_icon: Optional[str] = None,
        captain_id: Optional[int] = None,
        captain_name: Optional[str] = None,
        eights_channels: Optional[List] = None,
        sixes_channels: Optional[List] = None,
        fives_channels: Optional[List] = None,
        initial_players: Optional[List] = None,
        is_national_team: bool = False,
        is_mix_team: bool = False
    ) -> bool:
        """Add a new team to the database"""
        logger.info(f"Adding team: guild_id={guild_id}, guild_name={guild_name}, is_national={is_national_team}, is_mix={is_mix_team}")
        
        query = """
        INSERT INTO IOSCA_TEAMS (
            guild_id, guild_name, guild_icon, captain_id, captain_name,
            eights_channels, sixes_channels, fives_channels, players, is_national_team, is_mix_team
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """
        
        eights_channels = self._normalize_channel_ids_for_storage(eights_channels)
        sixes_channels = self._normalize_channel_ids_for_storage(sixes_channels)
        fives_channels = self._normalize_channel_ids_for_storage(fives_channels)
        initial_players = initial_players or []
        
        for player in initial_players:
            if 'steam_id' not in player:
                player['steam_id'] = None
        
        try:
            await self.pool.execute(
                query,
                guild_id,
                guild_name,
                guild_icon,
                captain_id,
                captain_name,
                json.dumps(eights_channels),
                json.dumps(sixes_channels),
                json.dumps(fives_channels),
                json.dumps(initial_players),
                is_national_team,
                is_mix_team
            )
            # Update average rating after creation
            try:
                await self.update_team_average_rating(guild_id)
            except Exception as e:
                logger.warning(f"Failed to update average rating for new team {guild_id}: {e}")
            logger.info(f"✅ Team {guild_name} added successfully")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to add team {guild_name}: {e}")
            return False
    
    async def get_team(self, guild_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve a team by its guild_id"""
        query = "SELECT * FROM IOSCA_TEAMS WHERE guild_id = $1"
        row = await self.pool.fetchrow(query, guild_id)
        
        if row:
            team_data = dict(row)
            team_data['eights_channels'] = self._normalize_channel_ids_for_runtime(
                json.loads(team_data.get('eights_channels', '[]'))
            )
            team_data['sixes_channels'] = self._normalize_channel_ids_for_runtime(
                json.loads(team_data.get('sixes_channels', '[]'))
            )
            team_data['fives_channels'] = self._normalize_channel_ids_for_runtime(
                json.loads(team_data.get('fives_channels', '[]'))
            )
            team_data['players'] = json.loads(team_data.get('players', '[]'))
            return team_data
        return None
    
    async def get_all_teams(self) -> List[Dict[str, Any]]:
        """Retrieve all registered teams"""
        query = "SELECT guild_id, guild_name, guild_icon FROM IOSCA_TEAMS ORDER BY guild_name ASC"
        rows = await self.pool.fetch(query)
        return [dict(row) for row in rows]
    
    async def get_all_teams_with_details(self) -> List[Dict[str, Any]]:
        """Retrieve all teams with full details, parsing JSON fields"""
        query = "SELECT * FROM IOSCA_TEAMS"
        rows = await self.pool.fetch(query)
        
        teams = []
        for row in rows:
            team = dict(row)
            try:
                team['eights_channels'] = self._normalize_channel_ids_for_runtime(
                    json.loads(team.get('eights_channels', '[]'))
                )
            except (json.JSONDecodeError, TypeError):
                team['eights_channels'] = []
            
            try:
                team['sixes_channels'] = self._normalize_channel_ids_for_runtime(
                    json.loads(team.get('sixes_channels', '[]'))
                )
            except (json.JSONDecodeError, TypeError):
                team['sixes_channels'] = []

            try:
                team['fives_channels'] = self._normalize_channel_ids_for_runtime(
                    json.loads(team.get('fives_channels', '[]'))
                )
            except (json.JSONDecodeError, TypeError):
                team['fives_channels'] = []
            
            try:
                team['players'] = json.loads(team.get('players', '[]'))
            except (json.JSONDecodeError, TypeError):
                team['players'] = []
            
            teams.append(team)
        
        return teams
    
    async def get_team_by_name(self, guild_name: str) -> Optional[Dict[str, Any]]:
        """Retrieve a team by its name (case-insensitive)"""
        query = "SELECT * FROM IOSCA_TEAMS WHERE LOWER(guild_name) = LOWER($1)"
        row = await self.pool.fetchrow(query, guild_name)
        
        if row:
            team_data = dict(row)
            team_data['eights_channels'] = self._normalize_channel_ids_for_runtime(
                json.loads(team_data.get('eights_channels', '[]'))
            )
            team_data['sixes_channels'] = self._normalize_channel_ids_for_runtime(
                json.loads(team_data.get('sixes_channels', '[]'))
            )
            team_data['fives_channels'] = self._normalize_channel_ids_for_runtime(
                json.loads(team_data.get('fives_channels', '[]'))
            )
            team_data['players'] = json.loads(team_data.get('players', '[]'))
            return team_data
        return None

    async def find_best_team_match(self, team_name: str, threshold: float = 0.8) -> Optional[Dict[str, Any]]:
        """Find the best team match by name similarity."""
        teams = await self.get_all_teams()
        return find_best_match(team_name, teams, threshold)

    async def upsert_lineup_snapshot(
        self,
        guild_id: int,
        channel_id: int,
        context_type: str,
        lineup_payload: Optional[Dict[str, Any]]
    ) -> bool:
        """Upsert latest lineup snapshot for a guild/channel."""
        try:
            if not lineup_payload:
                await self.pool.execute(
                    "DELETE FROM TEAM_LINEUPS WHERE guild_id = $1 AND channel_id = $2",
                    guild_id,
                    channel_id
                )
                return True

            await self.pool.execute(
                """
                INSERT INTO TEAM_LINEUPS (guild_id, channel_id, context_type, lineup, updated_at)
                VALUES ($1, $2, $3, $4, NOW())
                ON CONFLICT (guild_id, channel_id)
                DO UPDATE SET
                    context_type = EXCLUDED.context_type,
                    lineup = EXCLUDED.lineup,
                    updated_at = NOW()
                """,
                guild_id,
                channel_id,
                context_type,
                json.dumps(lineup_payload or {})
            )
            return True
        except Exception as e:
            logger.error(f"Failed to upsert lineup snapshot for guild {guild_id}: {e}")
            return False

    async def get_lineup_snapshots(self) -> List[Dict[str, Any]]:
        """Fetch all lineup snapshots."""
        try:
            rows = await self.pool.fetch("SELECT guild_id, channel_id, context_type, lineup FROM TEAM_LINEUPS")
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to fetch lineup snapshots: {e}")
            return []
    
    async def get_teams_by_captain_id(self, captain_id: int) -> List[Dict[str, Any]]:
        """Get all teams where the user is the captain"""
        query = "SELECT * FROM IOSCA_TEAMS WHERE captain_id = $1"
        rows = await self.pool.fetch(query, captain_id)
        
        teams = []
        for row in rows:
            team = dict(row)
            try:
                team['eights_channels'] = self._normalize_channel_ids_for_runtime(
                    json.loads(team.get('eights_channels', '[]'))
                )
            except (json.JSONDecodeError, TypeError):
                team['eights_channels'] = []
            
            try:
                team['sixes_channels'] = self._normalize_channel_ids_for_runtime(
                    json.loads(team.get('sixes_channels', '[]'))
                )
            except (json.JSONDecodeError, TypeError):
                team['sixes_channels'] = []

            try:
                team['fives_channels'] = self._normalize_channel_ids_for_runtime(
                    json.loads(team.get('fives_channels', '[]'))
                )
            except (json.JSONDecodeError, TypeError):
                team['fives_channels'] = []
            
            try:
                team['players'] = json.loads(team.get('players', '[]'))
            except (json.JSONDecodeError, TypeError):
                team['players'] = []
            
            teams.append(team)
        
        return teams
    
    async def update_team_players(self, guild_id: int, players_list: List[Dict]) -> bool:
        """Update the players list for a team"""
        query = "UPDATE IOSCA_TEAMS SET players = $1 WHERE guild_id = $2"
        players_json = json.dumps(players_list)
        
        try:
            await self.pool.execute(query, players_json, guild_id)
            try:
                await self.update_team_average_rating(guild_id)
            except Exception as e:
                logger.warning(f"Failed to update average rating for team {guild_id}: {e}")
            return True
        except Exception as e:
            logger.error(f"Failed to update team players: {e}")
            return False
    
    async def update_team_captain(self, guild_id: int, captain_id: int, captain_name: str) -> bool:
        """Update team captain"""
        query = "UPDATE IOSCA_TEAMS SET captain_id = $1, captain_name = $2 WHERE guild_id = $3"
        
        try:
            await self.pool.execute(query, captain_id, captain_name, guild_id)
            try:
                await self.update_team_average_rating(guild_id)
            except Exception as e:
                logger.warning(f"Failed to update average rating for team {guild_id}: {e}")
            return True
        except Exception as e:
            logger.error(f"Failed to update team captain: {e}")
            return False
    
    async def update_team_channels(
        self,
        guild_id: int,
        eights_channels: Optional[List] = None,
        sixes_channels: Optional[List] = None,
        fives_channels: Optional[List] = None
    ) -> bool:
        """Update matchmaking channels for a team"""
        if eights_channels is not None:
            query = "UPDATE IOSCA_TEAMS SET eights_channels = $1 WHERE guild_id = $2"
            try:
                normalized = self._normalize_channel_ids_for_storage(eights_channels)
                await self.pool.execute(query, json.dumps(normalized), guild_id)
            except Exception as e:
                logger.error(f"Failed to update eights channels: {e}")
                return False
        
        if sixes_channels is not None:
            query = "UPDATE IOSCA_TEAMS SET sixes_channels = $1 WHERE guild_id = $2"
            try:
                normalized = self._normalize_channel_ids_for_storage(sixes_channels)
                await self.pool.execute(query, json.dumps(normalized), guild_id)
            except Exception as e:
                logger.error(f"Failed to update sixes channels: {e}")
                return False

        if fives_channels is not None:
            query = "UPDATE IOSCA_TEAMS SET fives_channels = $1 WHERE guild_id = $2"
            try:
                normalized = self._normalize_channel_ids_for_storage(fives_channels)
                await self.pool.execute(query, json.dumps(normalized), guild_id)
            except Exception as e:
                logger.error(f"Failed to update fives channels: {e}")
                return False
        
        return True
    
    async def delete_team(self, guild_id: int) -> bool:
        """Delete a team by its guild_id"""
        query = "DELETE FROM IOSCA_TEAMS WHERE guild_id = $1"
        
        try:
            await self.pool.execute(query, guild_id)
            logger.info(f"Team {guild_id} deleted")
            return True
        except Exception as e:
            logger.error(f"Failed to delete team: {e}")
            return False
    
    async def team_exists(self, guild_id: int) -> bool:
        """Check if a team exists"""
        query = "SELECT EXISTS(SELECT 1 FROM IOSCA_TEAMS WHERE guild_id = $1)"
        return await self.pool.fetchval(query, guild_id)
    
    async def get_team_player_count(self, guild_id: int) -> int:
        """Get the number of players in a team"""
        team = await self.get_team(guild_id)
        if team and team.get('players'):
            return len(team['players'])
        return 0
    
    async def add_player_to_team(self, guild_id: int, player_data: Dict | int, player_name: Optional[str] = None, steam_id: Optional[str] = None) -> bool:
        """Add a player to a team's roster"""
        team = await self.get_team(guild_id)
        if not team:
            return False
        
        players = team.get('players', [])
        if isinstance(player_data, dict):
            if 'steam_id' not in player_data:
                player_data['steam_id'] = None
            discord_id = player_data.get('discord_id') or player_data.get('id')
        else:
            discord_id = int(player_data)
            player_data = {
                "discord_id": discord_id,
                "id": discord_id,
                "name": player_name or str(discord_id),
                "steam_id": steam_id
            }
        
        if any((p.get('discord_id') == discord_id or p.get('id') == discord_id) for p in players if isinstance(p, dict)):
            logger.warning(f"Player {discord_id} already in team {guild_id}")
            return False
        
        players.append(player_data)
        return await self.update_team_players(guild_id, players)
    
    async def remove_player_from_team(self, guild_id: int, discord_id: int) -> bool:
        """Remove a player from a team's roster"""
        team = await self.get_team(guild_id)
        if not team:
            return False
        
        players = team.get('players', [])
        players = [p for p in players if not (isinstance(p, dict) and (p.get('discord_id') == discord_id or p.get('id') == discord_id))]
        
        return await self.update_team_players(guild_id, players)
    
    async def get_all_teams_with_channels(self) -> List[Dict[str, Any]]:
        """Get all teams that have matchmaking channels configured"""
        teams = await self.get_all_teams_with_details()
        teams_with_channels = []
        
        for team in teams:
            eights_channels = team.get('eights_channels', [])
            sixes_channels = team.get('sixes_channels', [])
            fives_channels = team.get('fives_channels', [])
            
            if (
                (eights_channels and len(eights_channels) > 0)
                or (sixes_channels and len(sixes_channels) > 0)
                or (fives_channels and len(fives_channels) > 0)
            ):
                teams_with_channels.append(team)
        
        return teams_with_channels
    
    async def update_team_details(self, guild_id: int, **kwargs) -> bool:
        """Update team details dynamically (captain, guild_name, guild_icon)"""
        update_fields = []
        values = []
        param_count = 1
        
        allowed_fields = ['captain_id', 'captain_name', 'guild_name', 'guild_icon', 'press_channel_id']
        
        for field, value in kwargs.items():
            if field in allowed_fields:
                update_fields.append(f"{field} = ${param_count}")
                values.append(value)
                param_count += 1
        
        if not update_fields:
            return False
        
        values.append(guild_id)
        query = f"UPDATE IOSCA_TEAMS SET {', '.join(update_fields)} WHERE guild_id = ${param_count}"
        
        try:
            await self.pool.execute(query, *values)
            try:
                await self.update_team_average_rating(guild_id)
            except Exception as e:
                logger.warning(f"Failed to update average rating for team {guild_id}: {e}")
            return True
        except Exception as e:
            logger.error(f"Failed to update team details: {e}")
            return False

    async def update_team_average_rating(self, guild_id: int) -> Optional[float]:
        """Recalculate and store average rating for a single team."""
        team = await self.get_team(guild_id)
        if not team:
            return None

        players = team.get("players", []) or []
        discord_ids = set()
        steam_ids = set()

        for player in players:
            if isinstance(player, dict):
                if player.get("steam_id"):
                    steam_ids.add(player["steam_id"])
                if player.get("discord_id"):
                    discord_ids.add(player["discord_id"])
                elif player.get("id"):
                    discord_ids.add(player["id"])

        if not discord_ids and not steam_ids:
            await self.pool.execute(
                "UPDATE IOSCA_TEAMS SET average_rating = NULL WHERE guild_id = $1",
                guild_id
            )
            return None

        ratings = []
        try:
            discord_id_list = [str(d) for d in discord_ids] if discord_ids else []
            steam_id_list = list(steam_ids) if steam_ids else []
            rows = await self.pool.fetch(
                """
                SELECT rating FROM IOSCA_PLAYERS
                WHERE rating IS NOT NULL
                  AND (
                    (discord_id::text = ANY($1::text[]))
                    OR (steam_id = ANY($2::text[]))
                  )
                """,
                discord_id_list,
                steam_id_list
            )
            ratings = [r["rating"] for r in rows if r.get("rating") is not None]
        except Exception as e:
            logger.error(f"Error fetching ratings for team {guild_id}: {e}")

        if not ratings:
            await self.pool.execute(
                "UPDATE IOSCA_TEAMS SET average_rating = NULL WHERE guild_id = $1",
                guild_id
            )
            return None

        avg_rating = round(sum(ratings) / len(ratings), 2)
        await self.pool.execute(
            "UPDATE IOSCA_TEAMS SET average_rating = $1 WHERE guild_id = $2",
            avg_rating,
            guild_id
        )
        return avg_rating
    
    async def remove_duplicate_players_from_team(self, guild_id: int) -> Dict[str, Any]:
        """Remove duplicate players from a team's roster"""
        team = await self.get_team(guild_id)
        if not team or not team.get('players'):
            return {'removed_count': 0, 'original_count': 0, 'final_count': 0, 'duplicates': []}
        
        original_players = team['players']
        original_count = len(original_players)
        
        duplicates = []
        seen_ids = set()
        unique_players = []
        
        for player in original_players:
            if isinstance(player, dict) and 'discord_id' in player:
                player_id = player['discord_id']
                if player_id in seen_ids:
                    duplicates.append(player)
                else:
                    seen_ids.add(player_id)
                    unique_players.append(player)
        
        if len(unique_players) != original_count:
            await self.update_team_players(guild_id, unique_players)
        
        return {
            'removed_count': len(duplicates),
            'original_count': original_count,
            'final_count': len(unique_players),
            'duplicates': duplicates
        }
    
    async def enforce_team_player_limit(self, guild_id: int, max_players: int = 17) -> Dict[str, Any]:
        """Enforce maximum player limit for a team"""
        team = await self.get_team(guild_id)
        if not team or not team.get('players'):
            return {'removed_count': 0, 'original_count': 0, 'final_count': 0, 'removed_players': []}
        
        original_players = team['players']
        original_count = len(original_players)
        
        # First remove duplicates
        seen_ids = set()
        unique_players = []
        
        for player in original_players:
            if isinstance(player, dict) and 'discord_id' in player:
                player_id = player['discord_id']
                if player_id not in seen_ids:
                    seen_ids.add(player_id)
                    unique_players.append(player)
        
        unique_count = len(unique_players)
        
        if unique_count <= max_players:
            if unique_count != original_count:
                await self.update_team_players(guild_id, unique_players)
                return {
                    'removed_count': original_count - unique_count,
                    'original_count': original_count,
                    'final_count': unique_count,
                    'removed_players': [],
                    'note': 'Only duplicates removed'
                }
            return {'removed_count': 0, 'original_count': original_count, 'final_count': original_count, 'removed_players': []}
        
        # Enforce limit
        kept_players = unique_players[:max_players]
        removed_players = unique_players[max_players:]
        
        await self.update_team_players(guild_id, kept_players)
        
        return {
            'removed_count': len(removed_players),
            'original_count': original_count,
            'final_count': len(kept_players),
            'removed_players': removed_players,
            'note': f'Enforced {max_players}-player limit'
        }
    
    async def clean_team_players(self, guild_id: int, max_players: int = 17) -> Dict[str, Any]:
        """Remove duplicates and enforce player limit"""
        duplicate_result = await self.remove_duplicate_players_from_team(guild_id)
        if 'error' in duplicate_result:
            return duplicate_result
        
        limit_result = await self.enforce_team_player_limit(guild_id, max_players)
        if 'error' in limit_result:
            return limit_result
        
        return {
            'duplicates_removed': duplicate_result['removed_count'],
            'limit_enforced': limit_result['removed_count'],
            'original_count': duplicate_result['original_count'],
            'final_count': limit_result['final_count'],
            'total_removed': duplicate_result['removed_count'] + limit_result['removed_count']
        }
    
    async def clean_all_teams(self, max_players: int = 17) -> Dict[str, Any]:
        """Clean all teams: remove duplicates and enforce limits"""
        all_teams = await self.get_all_teams()
        if not all_teams:
            return {'teams_processed': 0, 'total_duplicates_removed': 0, 'total_limit_enforced': 0}
        
        total_duplicates_removed = 0
        total_limit_enforced = 0
        teams_processed = 0
        errors = []
        
        for team in all_teams:
            try:
                guild_id = team['guild_id']
                result = await self.clean_team_players(guild_id, max_players)
                
                if 'error' not in result:
                    total_duplicates_removed += result.get('duplicates_removed', 0)
                    total_limit_enforced += result.get('limit_enforced', 0)
                    teams_processed += 1
                else:
                    errors.append(f"Team {team.get('guild_name', 'Unknown')}: {result['error']}")
            except Exception as e:
                errors.append(f"Team {team.get('guild_name', 'Unknown')}: {str(e)}")
        
        return {
            'teams_processed': teams_processed,
            'total_teams': len(all_teams),
            'total_duplicates_removed': total_duplicates_removed,
            'total_limit_enforced': total_limit_enforced,
            'errors': errors
        }
    
    async def get_player_teams(self, discord_id: int) -> List[Dict[str, Any]]:
        """Get all teams a player belongs to"""
        all_teams = await self.get_all_teams_with_details()
        player_teams = []
        
        for team in all_teams:
            is_captain = team.get('captain_id') == discord_id
            
            if is_captain:
                player_teams.append({
                    'guild_id': team['guild_id'],
                    'name': team['guild_name'],
                    'guild_name': team['guild_name'],
                    'is_national_team': team.get('is_national_team', False),
                    'is_mix_team': team.get('is_mix_team', False),
                    'captain_id': team.get('captain_id')
                })
                continue
            
            players = team.get('players', [])
            for player in players:
                if isinstance(player, dict) and player.get('discord_id') == discord_id:
                    player_teams.append({
                        'guild_id': team['guild_id'],
                        'name': team['guild_name'],
                        'guild_name': team['guild_name'],
                        'is_national_team': team.get('is_national_team', False),
                        'is_mix_team': team.get('is_mix_team', False),
                        'captain_id': team.get('captain_id')
                    })
                    break
        
        return player_teams
    
    async def is_player_in_team_type(self, discord_id: int, team_type: str) -> bool:
        """Check if player is in a specific team type (club/national/mix)"""
        all_teams = await self.get_all_teams_with_details()
        
        for team in all_teams:
            is_national = team.get('is_national_team', False)
            is_mix = team.get('is_mix_team', False)
            
            if is_national:
                current_team_type = 'national'
            elif is_mix:
                current_team_type = 'mix'
            else:
                current_team_type = 'club'
            
            if current_team_type == team_type:
                is_captain = team.get('captain_id') == discord_id
                
                if is_captain:
                    return True
                
                players = team.get('players', [])
                for player in players:
                    if isinstance(player, dict) and player.get('discord_id') == discord_id:
                        return True
        
        return False
    
    def get_unique_player_ids(self, team_players: list) -> set:
        """Get unique player IDs from a team's player list"""
        unique_ids = set()
        for player in team_players:
            if isinstance(player, dict):
                discord_id = player.get('discord_id') or player.get('id')
                if discord_id:
                    unique_ids.add(discord_id)
            elif hasattr(player, 'id'):
                unique_ids.add(player.id)
        return unique_ids
    
    async def add_nickname_to_team(self, guild_id: int, nickname: str) -> bool:
        """Add a nickname to a team's nicknames list"""
        team = await self.get_team(guild_id)
        if not team:
            logger.warning(f"Team {guild_id} not found")
            return False
        
        # Don't add if it's the same as the main name
        if nickname.lower() == team['guild_name'].lower():
            return True
        
        # Parse existing nicknames
        current_nicknames = team.get('nicknames', [])
        if isinstance(current_nicknames, str):
            current_nicknames = json.loads(current_nicknames)
        
        # Add nickname if not already present (case-insensitive check)
        nickname_lower = nickname.lower()
        existing_lower = [n.lower() for n in current_nicknames]
        
        if nickname_lower not in existing_lower:
            current_nicknames.append(nickname)
            
            query = "UPDATE IOSCA_TEAMS SET nicknames = $1 WHERE guild_id = $2"
            try:
                await self.pool.execute(query, json.dumps(current_nicknames), guild_id)
                logger.info(f"Added nickname '{nickname}' to team '{team['guild_name']}'")
                return True
            except Exception as e:
                logger.error(f"Failed to add nickname: {e}")
                return False
        
        return True
    
    async def find_team_by_name_or_nickname(self, team_name: str) -> Optional[Dict[str, Any]]:
        """Find a team by exact match on guild_name or any of its nicknames"""
        # First try exact match on main name
        team = await self.get_team_by_name(team_name)
        if team:
            return team
        
        # Search in nicknames using PostgreSQL JSONB operators
        query = """
        SELECT * FROM IOSCA_TEAMS 
        WHERE captain_id != 0 
        AND nicknames IS NOT NULL
        AND nicknames::jsonb @> $1::jsonb
        """
        
        try:
            row = await self.pool.fetchrow(query, json.dumps([team_name]))
            if row:
                team_data = dict(row)
                team_data['eights_channels'] = json.loads(team_data.get('eights_channels', '[]'))
                team_data['sixes_channels'] = json.loads(team_data.get('sixes_channels', '[]'))
                team_data['players'] = json.loads(team_data.get('players', '[]'))
                team_data['nicknames'] = json.loads(team_data.get('nicknames', '[]'))
                return team_data
            return None
        except Exception as e:
            logger.error(f"Error finding team by nickname: {e}")
            return None
    
    async def get_all_team_names_for_team(self, guild_id: int) -> List[str]:
        """Get all possible names (main name + nicknames) for a team"""
        query = "SELECT guild_name, nicknames FROM IOSCA_TEAMS WHERE guild_id = $1"
        row = await self.pool.fetchrow(query, guild_id)
        
        if not row:
            return []
        
        all_names = [row['guild_name']]
        
        if row['nicknames']:
            nicknames = json.loads(row['nicknames']) if isinstance(row['nicknames'], str) else row['nicknames']
            all_names.extend(nicknames)
        
        return all_names

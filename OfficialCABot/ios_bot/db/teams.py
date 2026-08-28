"""
Team operations for PostgreSQL database
"""

import json
import logging
import re
from typing import Optional, List, Dict, Any
from .utils import find_best_match
from .connection import DatabasePool
from .cache import QueryCache
from ..hub_sync_signal import request_hub_sync_soon

logger = logging.getLogger(__name__)

# Safety-net TTL only -- every write path that touches IOSCA_TEAMS
# (add_team, update_team_players, update_team_captain, update_team_channels,
# update_team_details, update_team_average_rating, delete_team) calls
# _invalidate_teams_cache() right after it commits, so reads should never
# actually see this TTL expire in normal operation.
_TEAMS_CACHE_TTL_SECONDS = 600


def _normalize_alias(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


class TeamOperations:
    """Handles all team-related database operations"""

    def __init__(self, pool: DatabasePool):
        self.pool = pool
        self._cache = QueryCache(safety_ttl_seconds=_TEAMS_CACHE_TTL_SECONDS)

    def _invalidate_teams_cache(self) -> None:
        self._cache.invalidate_prefix("teams:")
        request_hub_sync_soon("team changed")

    def invalidate_cache(self) -> None:
        """Public entry point for callers outside this class (e.g. the batch
        rating-recalculation job, which updates average_rating via its own
        SQL rather than going through update_team_average_rating)."""
        self._invalidate_teams_cache()

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

    def _coerce_player_discord_id(self, value: Any) -> Optional[int]:
        try:
            if value is None:
                return None
            return int(str(value).strip())
        except Exception:
            return None

    def _ensure_captain_in_players(
        self,
        players_list: Optional[List[Dict]],
        captain_id: Any,
        captain_name: Optional[str],
    ) -> List[Dict]:
        players = list(players_list or [])
        captain_int = self._coerce_player_discord_id(captain_id)
        if not captain_int:
            return players

        for player in players:
            if not isinstance(player, dict):
                continue
            player_id = self._coerce_player_discord_id(player.get("discord_id"))
            if player_id is None:
                player_id = self._coerce_player_discord_id(player.get("id"))
            if player_id == captain_int:
                if not player.get("discord_id"):
                    player["discord_id"] = captain_int
                if not player.get("id"):
                    player["id"] = captain_int
                if captain_name and not player.get("name"):
                    player["name"] = captain_name
                if "steam_id" not in player:
                    player["steam_id"] = None
                return players

        players.append(
            {
                "discord_id": captain_int,
                "id": captain_int,
                "name": captain_name or str(captain_int),
                "steam_id": None,
            }
        )
        return players

    def _parse_vice_captain_ids(self, raw: Any) -> List[int]:
        """vice_captain_ids is stored as a JSONB array of raw Discord ids
        (not {id, name} dicts like the players list) -- normalize whatever
        shape comes back from the DB/JSON into a clean list of ints."""
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw else []
            except (json.JSONDecodeError, TypeError):
                raw = []
        if not isinstance(raw, (list, tuple, set)):
            return []
        result: List[int] = []
        for value in raw:
            coerced = self._coerce_player_discord_id(value)
            if coerced is not None:
                result.append(coerced)
        return result

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
        is_mix_team: bool = False,
        vice_captain_id: Optional[int] = None,
        vice_captain_name: Optional[str] = None,
    ) -> bool:
        """Add a new team to the database"""
        logger.info(f"Adding team: guild_id={guild_id}, guild_name={guild_name}, is_national={is_national_team}, is_mix={is_mix_team}")

        # ON CONFLICT reactivates a previously soft-deleted team for this
        # guild (see delete_team) instead of failing on the guild_id primary
        # key -- e.g. the bot was only briefly kicked and re-invited, or the
        # server registers again after folding.
        query = """
        INSERT INTO IOSCA_TEAMS (
            guild_id, guild_name, guild_icon, captain_id, captain_name,
            eights_channels, sixes_channels, fives_channels, players, is_national_team, is_mix_team,
            vice_captain_ids, is_active
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, TRUE)
        ON CONFLICT (guild_id) DO UPDATE SET
            guild_name = EXCLUDED.guild_name,
            guild_icon = EXCLUDED.guild_icon,
            captain_id = EXCLUDED.captain_id,
            captain_name = EXCLUDED.captain_name,
            eights_channels = EXCLUDED.eights_channels,
            sixes_channels = EXCLUDED.sixes_channels,
            fives_channels = EXCLUDED.fives_channels,
            players = EXCLUDED.players,
            is_national_team = EXCLUDED.is_national_team,
            is_mix_team = EXCLUDED.is_mix_team,
            vice_captain_ids = EXCLUDED.vice_captain_ids,
            is_active = TRUE,
            updated_at = CURRENT_TIMESTAMP
        """

        eights_channels = self._normalize_channel_ids_for_storage(eights_channels)
        sixes_channels = self._normalize_channel_ids_for_storage(sixes_channels)
        fives_channels = self._normalize_channel_ids_for_storage(fives_channels)
        initial_players = initial_players or []
        initial_players = self._ensure_captain_in_players(initial_players, captain_id, captain_name)

        vice_captain_int = self._coerce_player_discord_id(vice_captain_id)
        vice_captain_ids = [vice_captain_int] if vice_captain_int else []
        if vice_captain_int:
            initial_players = self._ensure_captain_in_players(initial_players, vice_captain_int, vice_captain_name)

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
                is_mix_team,
                json.dumps(vice_captain_ids),
            )
            self._invalidate_teams_cache()
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
        """Retrieve a team by its guild_id (cached until a team write invalidates it).

        Team config barely ever changes and this is on the hot path for
        every /sign, /unsign, /sub, /ready (get_channel_context resolves team
        channels through this) -- so on a DB error, fall back to the last
        successfully-fetched value for this guild instead of raising, rather
        than taking down matchmaking commands during a transient DB outage.
        """
        cache_key = f"teams:by_id:{guild_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return dict(cached) if cached else None

        try:
            query = "SELECT * FROM IOSCA_TEAMS WHERE guild_id = $1"
            row = await self.pool.fetchrow(query, guild_id)
        except Exception as e:
            stale = self._cache.get_last_good(cache_key)
            if stale is not None:
                logger.warning(f"get_team({guild_id}) DB fetch failed ({e}); serving last-known cached value")
                return dict(stale) if stale else None
            logger.error(f"get_team({guild_id}) DB fetch failed and no cached fallback is available: {e}")
            raise

        result: Optional[Dict[str, Any]] = None
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
            result = team_data

        self._cache.set(cache_key, result if result is not None else {})
        return result
    
    async def get_all_teams(self) -> List[Dict[str, Any]]:
        """Retrieve all registered teams (cached until a team write invalidates it)."""
        cached = self._cache.get("teams:all")
        if cached is not None:
            return list(cached)

        query = "SELECT guild_id, guild_name, guild_icon FROM IOSCA_TEAMS WHERE is_active ORDER BY guild_name ASC"
        rows = await self.pool.fetch(query)
        data = [dict(row) for row in rows]
        self._cache.set("teams:all", data)
        return data

    async def get_all_teams_with_details(self) -> List[Dict[str, Any]]:
        """Retrieve all teams with full details, parsing JSON fields (cached
        until a team write invalidates it)."""
        cached = self._cache.get("teams:all_with_details")
        if cached is not None:
            return list(cached)

        query = "SELECT * FROM IOSCA_TEAMS WHERE is_active"
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

        self._cache.set("teams:all_with_details", teams)
        return teams

    async def get_teams_by_ids(self, guild_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """Retrieve a set of teams keyed by guild_id (per-id cached; only
        cache misses hit the database, in one batched query)."""
        normalized_ids: List[int] = []
        for guild_id in guild_ids or []:
            try:
                normalized_ids.append(int(guild_id))
            except (TypeError, ValueError):
                continue

        normalized_ids = list(dict.fromkeys(normalized_ids))
        if not normalized_ids:
            return {}

        teams_by_id: Dict[int, Dict[str, Any]] = {}
        missing_ids: List[int] = []
        for guild_id in normalized_ids:
            cached = self._cache.get(f"teams:by_id:{guild_id}")
            if cached is not None:
                if cached:
                    teams_by_id[guild_id] = dict(cached)
            else:
                missing_ids.append(guild_id)

        if not missing_ids:
            return teams_by_id

        query = "SELECT * FROM IOSCA_TEAMS WHERE guild_id = ANY($1::bigint[])"
        rows = await self.pool.fetch(query, missing_ids)

        found_ids: set[int] = set()
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

            try:
                gid = int(team["guild_id"])
            except (KeyError, TypeError, ValueError):
                continue
            teams_by_id[gid] = team
            found_ids.add(gid)
            self._cache.set(f"teams:by_id:{gid}", team)

        for gid in missing_ids:
            if gid not in found_ids:
                self._cache.set(f"teams:by_id:{gid}", {})

        return teams_by_id

    async def get_team_by_name(self, guild_name: str) -> Optional[Dict[str, Any]]:
        """Retrieve a team by its name (case-insensitive, cached until a
        team write invalidates it)."""
        cache_key = f"teams:by_name:{str(guild_name or '').strip().lower()}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return dict(cached) if cached else None

        query = "SELECT * FROM IOSCA_TEAMS WHERE LOWER(guild_name) = LOWER($1)"
        row = await self.pool.fetchrow(query, guild_name)

        result: Optional[Dict[str, Any]] = None
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
            result = team_data

        self._cache.set(cache_key, result if result is not None else {})
        return result

    async def find_best_team_match(self, team_name: str, threshold: float = 0.8) -> Optional[Dict[str, Any]]:
        """Find the best team match by name similarity."""
        teams = await self.get_all_teams()
        return find_best_match(team_name, teams, threshold)

    async def get_team_by_alias(self, name: str) -> Optional[Dict[str, Any]]:
        """Look up a team via TEAM_NAME_ALIASES -- checked before fuzzy matching.

        Covers both main-guild variants (IOSCA, IOSCA A, country-name pickup
        labels, etc.) and any other team's known name variants, replacing the
        old approach of a hardcoded main-guild-only alias list in
        match_importer.py.
        """
        alias_norm = _normalize_alias(name)
        if not alias_norm:
            return None
        row = await self.pool.fetchrow(
            "SELECT guild_id FROM TEAM_NAME_ALIASES WHERE alias_norm = $1", alias_norm
        )
        if not row or row["guild_id"] is None:
            return None
        return await self.get_team(row["guild_id"])

    async def add_team_alias(self, guild_id: int, alias_name: str) -> bool:
        """Register a name variant that should resolve to this team, without
        needing to guess via fuzzy matching every time it's seen."""
        if not _normalize_alias(alias_name):
            return False
        try:
            # alias_norm is a generated column (normalize_team_name(alias_name));
            # don't set it directly.
            await self.pool.execute(
                """
                INSERT INTO TEAM_NAME_ALIASES (alias_name, guild_id)
                VALUES ($1, $2)
                ON CONFLICT (alias_norm) DO UPDATE SET guild_id = EXCLUDED.guild_id, updated_at = NOW()
                """,
                str(alias_name or "").strip(),
                int(guild_id),
            )
            return True
        except Exception as e:
            logger.error(f"Failed to add team alias '{alias_name}' -> {guild_id}: {e}")
            return False

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
        team = await self.get_team(guild_id)
        captain_id = team.get("captain_id") if team else None
        captain_name = team.get("captain_name") if team else None
        players_list = self._ensure_captain_in_players(players_list, captain_id, captain_name)
        players_json = json.dumps(players_list)
        
        try:
            await self.pool.execute(query, players_json, guild_id)
            self._invalidate_teams_cache()
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
            self._invalidate_teams_cache()
            # ensure_captain_in_roster() -> update_team_players() already
            # recalculates average_rating -- a second explicit call here was
            # pure duplicate work every time a captain changed.
            await self.ensure_captain_in_roster(guild_id)
            return True
        except Exception as e:
            logger.error(f"Failed to update team captain: {e}")
            return False

    async def update_team_vice_captain(
        self,
        guild_id: int,
        vice_captain_id: Optional[int],
        vice_captain_name: Optional[str] = None,
    ) -> bool:
        """Set (or clear, if vice_captain_id is None) the team's single
        vice-captain. Stored in vice_captain_ids (a JSONB list) for
        compatibility with the existing multi-VC-aware readers elsewhere,
        but this always writes at most one entry."""
        vice_captain_int = self._coerce_player_discord_id(vice_captain_id)
        vice_captain_ids = [vice_captain_int] if vice_captain_int else []

        try:
            await self.pool.execute(
                "UPDATE IOSCA_TEAMS SET vice_captain_ids = $1 WHERE guild_id = $2",
                json.dumps(vice_captain_ids),
                guild_id,
            )
            self._invalidate_teams_cache()
            # A vice-captain is only recognized as "on the team" elsewhere
            # (get_player_teams, is_player_in_team_type, roster displays) if
            # they're also in the players roster -- mirror how the captain
            # is guaranteed a roster entry.
            if vice_captain_int:
                team = await self.get_team(guild_id)
                if team:
                    players = self._ensure_captain_in_players(
                        team.get("players") or [], vice_captain_int, vice_captain_name
                    )
                    await self.update_team_players(guild_id, players)
            return True
        except Exception as e:
            logger.error(f"Failed to update team vice-captain: {e}")
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

        self._invalidate_teams_cache()
        return True

    async def delete_team(self, guild_id: int) -> bool:
        """Soft-delete a team: mark it inactive and free its regular roster
        players rather than destroying the row. Captain/vice_captain/channel
        data is left in place as a historical record in case the team comes
        back (e.g. the bot was only briefly removed from the Discord server,
        which is the main caller of this -- see on_guild_remove).

        Deactivated teams are excluded from get_all_teams/get_all_teams_with_details
        and from is_player_in_team_type, so freed players can immediately
        join another team of the same type."""
        query = """
        UPDATE IOSCA_TEAMS
        SET is_active = FALSE, players = $2, updated_at = CURRENT_TIMESTAMP
        WHERE guild_id = $1
        """

        try:
            await self.pool.execute(query, guild_id, json.dumps([]))
            self._invalidate_teams_cache()
            logger.info(f"Team {guild_id} deactivated (soft-deleted), roster cleared")
            return True
        except Exception as e:
            logger.error(f"Failed to deactivate team: {e}")
            return False

    async def reactivate_team(self, guild_id: int) -> bool:
        """Re-enable a previously soft-deleted team (e.g. the bot was
        re-invited to its Discord server, or a team registers again)."""
        try:
            await self.pool.execute(
                "UPDATE IOSCA_TEAMS SET is_active = TRUE, updated_at = CURRENT_TIMESTAMP WHERE guild_id = $1",
                guild_id,
            )
            self._invalidate_teams_cache()
            logger.info(f"Team {guild_id} reactivated")
            return True
        except Exception as e:
            logger.error(f"Failed to reactivate team: {e}")
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

    async def add_players_to_team(self, guild_id: int, players_data: List[Dict]) -> Dict[str, List]:
        """Add multiple players to a team's roster in one read-modify-write,
        instead of one round trip per player. Returns which discord_ids were
        actually added vs. skipped (already on the roster)."""
        team = await self.get_team(guild_id)
        if not team:
            return {"added": [], "skipped": [discord_id for discord_id in (p.get("discord_id") or p.get("id") for p in players_data) if discord_id]}

        players = list(team.get('players', []))
        existing_ids = {
            p.get('discord_id') or p.get('id')
            for p in players
            if isinstance(p, dict)
        }

        added: List[int] = []
        skipped: List[int] = []
        for player_data in players_data:
            if not isinstance(player_data, dict):
                continue
            if 'steam_id' not in player_data:
                player_data['steam_id'] = None
            discord_id = player_data.get('discord_id') or player_data.get('id')
            if discord_id is None:
                continue
            if discord_id in existing_ids:
                skipped.append(discord_id)
                continue
            players.append(player_data)
            existing_ids.add(discord_id)
            added.append(discord_id)

        if added:
            ok = await self.update_team_players(guild_id, players)
            if not ok:
                return {"added": [], "skipped": [d.get("discord_id") or d.get("id") for d in players_data if isinstance(d, dict)]}

        return {"added": added, "skipped": skipped}

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
            self._invalidate_teams_cache()
            # No average_rating recalc here: none of the allowed_fields above
            # (guild_name/guild_icon/press_channel_id, or captain_id/name --
            # which this method doesn't sync into the roster anyway) change
            # who's on the roster or their ratings. update_team_captain is
            # the path that actually needs to trigger a recalc.
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
            self._invalidate_teams_cache()
            return None

        ratings = []
        try:
            discord_id_list = [str(d) for d in discord_ids] if discord_ids else []
            steam_id_list = list(steam_ids) if steam_ids else []
            rows = await self.pool.fetch(
                """
                SELECT COALESCE(display_main_role_rating, rating) AS rating
                FROM IOSCA_PLAYERS
                WHERE COALESCE(display_main_role_rating, rating) IS NOT NULL
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
            self._invalidate_teams_cache()
            return None

        avg_rating = round(sum(ratings) / len(ratings), 2)
        await self.pool.execute(
            "UPDATE IOSCA_TEAMS SET average_rating = $1 WHERE guild_id = $2",
            avg_rating,
            guild_id
        )
        self._invalidate_teams_cache()
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

    async def ensure_captain_in_roster(self, guild_id: int) -> bool:
        """Guarantee captain exists in team players JSON."""
        team = await self.get_team(guild_id)
        if not team:
            return False
        players = self._ensure_captain_in_players(
            team.get("players") or [],
            team.get("captain_id"),
            team.get("captain_name"),
        )
        return await self.update_team_players(guild_id, players)

    async def ensure_all_captains_in_rosters(self) -> Dict[str, int]:
        """Ensure all teams include their captain in players JSON."""
        teams = await self.get_all_teams_with_details()
        processed = 0
        updated = 0
        errors = 0

        for team in teams:
            guild_id = team.get("guild_id")
            if guild_id is None:
                continue
            processed += 1
            try:
                before = team.get("players") or []
                after = self._ensure_captain_in_players(
                    before,
                    team.get("captain_id"),
                    team.get("captain_name"),
                )
                if json.dumps(before, sort_keys=True) != json.dumps(after, sort_keys=True):
                    ok = await self.update_team_players(guild_id, after)
                    if ok:
                        updated += 1
                    else:
                        errors += 1
            except Exception:
                errors += 1

        return {
            "teams_processed": processed,
            "teams_updated": updated,
            "errors": errors,
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
                    'image_url': team.get('guild_icon'),
                    'guild_icon': team.get('guild_icon'),
                    'is_national_team': team.get('is_national_team', False),
                    'is_mix_team': team.get('is_mix_team', False),
                    'captain_id': team.get('captain_id'),
                    'vice_captain_ids': self._parse_vice_captain_ids(team.get('vice_captain_ids')),
                })
                continue
            
            players = team.get('players', [])
            for player in players:
                if isinstance(player, dict) and player.get('discord_id') == discord_id:
                    player_teams.append({
                        'guild_id': team['guild_id'],
                        'name': team['guild_name'],
                        'guild_name': team['guild_name'],
                        'image_url': team.get('guild_icon'),
                        'guild_icon': team.get('guild_icon'),
                        'is_national_team': team.get('is_national_team', False),
                        'is_mix_team': team.get('is_mix_team', False),
                        'captain_id': team.get('captain_id'),
                        'vice_captain_ids': self._parse_vice_captain_ids(team.get('vice_captain_ids')),
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
    

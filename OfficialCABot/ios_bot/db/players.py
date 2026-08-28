"""
Player operations for PostgreSQL database
"""

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from .connection import DatabasePool
from .cache import QueryCache

logger = logging.getLogger(__name__)
ID64_BASE = 76561197960265728

# Safety-net TTL for the ratings leaderboard cache. Ratings only change via
# register_player/update_player_rating/delete_player in this class, or via
# the periodic recalculate_all batch job (which bypasses this class with its
# own SQL and calls invalidate_ratings_cache() explicitly when done).
_PLAYERS_CACHE_TTL_SECONDS = 600


def _normalize_legacy_steam_id(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None

    raw = raw.replace("STEAM0:", "STEAM_0:")
    if raw.upper().startswith("STEAM_"):
        parts = raw.split(":")
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            return f"STEAM_0:{int(parts[1]) % 2}:{int(parts[2])}"
        return None

    if raw.startswith("[") and raw.endswith("]") and raw.upper().startswith("[U:"):
        try:
            account_id = int(raw.split(":")[-1].rstrip("]"))
        except (TypeError, ValueError):
            return None
        y = account_id % 2
        z = (account_id - y) // 2
        return f"STEAM_0:{y}:{z}"

    if raw.isdigit() and len(raw) >= 16:
        try:
            steam64 = int(raw)
        except (TypeError, ValueError):
            return None
        if steam64 <= ID64_BASE:
            return None
        offset = steam64 - ID64_BASE
        y = offset % 2
        z = (offset - y) // 2
        return f"STEAM_0:{y}:{z}"

    return None


def _registration_token_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _normalize_discord_id_value(value) -> Optional[int]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            return None
        try:
            return int(digits)
        except (TypeError, ValueError):
            return None


def _is_unassigned_discord_value(value) -> bool:
    normalized = _normalize_discord_id_value(value)
    return normalized is None or normalized <= 0


class PlayerOperations:
    """Handles all player-related database operations"""
    
    def __init__(self, pool: DatabasePool):
        self.pool = pool
        self._discord_id_is_text = None
        self._name_column = None
        self._has_linked_steam_ids = None
        self._account_linking_schema_ready = False
        self._cache = QueryCache(safety_ttl_seconds=_PLAYERS_CACHE_TTL_SECONDS)

    def invalidate_ratings_cache(self) -> None:
        """Called after any ratings write, including bulk recalculation
        jobs that write via their own SQL outside this class."""
        self._cache.invalidate_prefix("players:")

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

    async def _has_linked_steam_ids_column(self) -> bool:
        """Detect whether IOSCA_PLAYERS.linked_steam_ids exists."""
        if self._has_linked_steam_ids is not None:
            return self._has_linked_steam_ids

        try:
            row = await self.pool.fetchrow(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'iosca_players'
                  AND column_name = 'linked_steam_ids'
                """
            )
            self._has_linked_steam_ids = bool(row)
        except Exception as e:
            logger.warning(f"Failed to detect iosca_players.linked_steam_ids: {e}")
            self._has_linked_steam_ids = False

        return self._has_linked_steam_ids

    async def _ensure_account_linking_schema(self) -> None:
        if self._account_linking_schema_ready:
            return

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS public.player_accounts (
                        account_id BIGSERIAL PRIMARY KEY,
                        hub_user_id BIGINT NULL UNIQUE,
                        display_name VARCHAR(255) NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS public.player_account_discord_ids (
                        account_discord_id BIGSERIAL PRIMARY KEY,
                        account_id BIGINT NOT NULL REFERENCES public.player_accounts(account_id) ON DELETE CASCADE,
                        discord_id VARCHAR(64) NOT NULL UNIQUE,
                        is_primary BOOLEAN NOT NULL DEFAULT FALSE,
                        verified_at TIMESTAMP NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_player_account_discord_ids_account
                    ON public.player_account_discord_ids(account_id, is_primary DESC)
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS public.player_account_steam_ids (
                        account_steam_id BIGSERIAL PRIMARY KEY,
                        account_id BIGINT NOT NULL REFERENCES public.player_accounts(account_id) ON DELETE CASCADE,
                        steam_id VARCHAR(255) NOT NULL UNIQUE,
                        steam_id_64 VARCHAR(32) NULL,
                        is_primary BOOLEAN NOT NULL DEFAULT FALSE,
                        verified_at TIMESTAMP NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_player_account_steam_ids_account
                    ON public.player_account_steam_ids(account_id, is_primary DESC)
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS public.player_registration_intents (
                        intent_id BIGSERIAL PRIMARY KEY,
                        discord_id VARCHAR(64) NOT NULL,
                        discord_name VARCHAR(255) NULL,
                        guild_id VARCHAR(64) NULL,
                        token_hash CHAR(64) NOT NULL UNIQUE,
                        expires_at TIMESTAMP NOT NULL,
                        used_at TIMESTAMP NULL,
                        consumed_by_hub_user_id BIGINT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_player_registration_intents_lookup
                    ON public.player_registration_intents(discord_id, expires_at DESC)
                    """
                )
                await conn.execute(
                    """
                    ALTER TABLE public.iosca_players
                    ADD COLUMN IF NOT EXISTS linked_steam_ids JSONB NOT NULL DEFAULT '[]'::jsonb
                    """
                )

        self._account_linking_schema_ready = True
        self._has_linked_steam_ids = True

    async def create_registration_intent(
        self,
        *,
        discord_id: int | str,
        discord_name: str | None = None,
        guild_id: int | str | None = None,
        ttl_seconds: int = 3600,
    ) -> str:
        await self._ensure_account_linking_schema()

        token = secrets.token_urlsafe(32)
        token_hash = _registration_token_hash(token)
        expires_at = datetime.utcnow() + timedelta(seconds=max(300, int(ttl_seconds)))
        await self.pool.execute(
            """
            INSERT INTO public.player_registration_intents (
                discord_id,
                discord_name,
                guild_id,
                token_hash,
                expires_at
            )
            VALUES ($1, $2, $3, $4, $5)
            """,
            str(discord_id).strip(),
            str(discord_name or "").strip() or None,
            str(guild_id).strip() if guild_id is not None else None,
            token_hash,
            expires_at,
        )
        return token

    async def get_registration_link_status(self, discord_id: int | str) -> Dict[str, Any]:
        await self._ensure_account_linking_schema()

        discord_key = str(discord_id).strip()
        player = await self.get_player_by_discord_id(discord_id)
        account_row = await self.pool.fetchrow(
            """
            SELECT
                account.account_id,
                EXISTS(
                    SELECT 1
                    FROM public.player_account_steam_ids steam
                    WHERE steam.account_id = account.account_id
                ) AS has_steam_identity
            FROM public.player_account_discord_ids identity
            JOIN public.player_accounts account ON account.account_id = identity.account_id
            WHERE identity.discord_id = $1
            LIMIT 1
            """,
            discord_key,
        )

        player_steam_id = str((player or {}).get("steam_id") or "").strip()
        linked_steam_ids = []
        if player and isinstance(player.get("linked_steam_ids"), list):
            linked_steam_ids = [str(value).strip() for value in player.get("linked_steam_ids") if str(value or "").strip()]

        return {
            "discord_id": discord_key,
            "linked": bool(player_steam_id or linked_steam_ids or (account_row and account_row.get("has_steam_identity"))),
            "has_player_row": bool(player),
            "player_steam_id": player_steam_id or None,
            "linked_steam_ids": linked_steam_ids,
            "has_account_identity": bool(account_row),
            "has_account_steam_identity": bool(account_row and account_row.get("has_steam_identity")),
        }
    
    async def register_player(self, discord_id: int, username: str, steam_id: str) -> bool:
        """Insert or update a player's registration.

        Runs on a single connection inside a transaction, holding a Postgres
        advisory lock keyed on the steam_id for the duration. Without this,
        two concurrent registrations for the same brand-new steam_id could
        both pass the "does a row exist yet" check before either had
        inserted, racing on the INSERT (steam_id is the primary key, so the
        loser would fail with a constraint error rather than corrupt data --
        but it would surface as a confusing registration failure instead of
        just working). The lock serializes concurrent calls for the same
        steam_id; calls for different steam_ids don't block each other.
        """
        try:
            discord_id_value = await self._coerce_discord_id(discord_id)
            discord_id_normalized = _normalize_discord_id_value(discord_id_value)
            name_column = await self._get_name_column()

            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", str(steam_id))

                    # If a row exists for this steam_id, update that row's discord_id (if empty)
                    existing = await self.get_player_by_steam_id(steam_id, conn=conn)
                    if existing:
                        # Treat None, 0, or empty string as 'not filled' and allow overwrite.
                        existing_discord = existing.get("discord_id")
                        existing_discord_normalized = _normalize_discord_id_value(existing_discord)
                        is_filled = not _is_unassigned_discord_value(existing_discord)

                        # If filled by a different real discord id, refuse to overwrite
                        if (
                            is_filled
                            and existing_discord_normalized is not None
                            and discord_id_normalized is not None
                            and existing_discord_normalized != discord_id_normalized
                        ):
                            logger.info(f"SteamID {steam_id} already linked to different Discord ID {existing_discord}")
                            return False

                        # Otherwise update the row (covers empty/0 case or idempotent overwrite)
                        if name_column:
                            update_q = f"""
                            UPDATE IOSCA_PLAYERS
                            SET discord_id = $1, {name_column} = $2, updated_at = CURRENT_TIMESTAMP
                            WHERE steam_id = $3
                            """
                            await conn.execute(update_q, discord_id_value, username, steam_id)
                        else:
                            update_q = """
                            UPDATE IOSCA_PLAYERS
                            SET discord_id = $1, updated_at = CURRENT_TIMESTAMP
                            WHERE steam_id = $2
                            """
                            await conn.execute(update_q, discord_id_value, steam_id)
                        logger.info(f"✅ Player updated for steam_id: {steam_id} -> discord {discord_id_value}")
                        self.invalidate_ratings_cache()
                        return True

                    # No existing steam_id row.
                    # Some deployments don't have a unique constraint on discord_id, so avoid ON CONFLICT.
                    existing_by_discord = await self.get_player_by_discord_id(discord_id_value, conn=conn)
                    if existing_by_discord:
                        if name_column:
                            update_discord_q = f"""
                            UPDATE IOSCA_PLAYERS
                            SET {name_column} = $1,
                                steam_id = $2,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE discord_id = $3
                            """
                            await conn.execute(update_discord_q, username, steam_id, discord_id_value)
                        else:
                            update_discord_q = """
                            UPDATE IOSCA_PLAYERS
                            SET steam_id = $1,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE discord_id = $2
                            """
                            await conn.execute(update_discord_q, steam_id, discord_id_value)
                    else:
                        if name_column:
                            insert_q = f"""
                            INSERT INTO IOSCA_PLAYERS (discord_id, {name_column}, steam_id)
                            VALUES ($1, $2, $3)
                            """
                            await conn.execute(insert_q, discord_id_value, username, steam_id)
                        else:
                            insert_q = """
                            INSERT INTO IOSCA_PLAYERS (discord_id, steam_id)
                            VALUES ($1, $2)
                            """
                            await conn.execute(insert_q, discord_id_value, steam_id)
                    logger.info(f"✅ Player registered: {username} ({discord_id_value})")
                    self.invalidate_ratings_cache()
                    return True

        except Exception as e:
            logger.error(f"❌ Failed to register player {username}: {e}")
            return False
    
    async def get_player_by_steam_id(self, steam_id: str, conn=None) -> Optional[Dict[str, Any]]:
        """Retrieve a player by their SteamID. Pass `conn` to run on an
        already-acquired connection (e.g. inside a transaction) instead of
        checking out a separate one from the pool -- caching is skipped in
        that case, since a caller inside an explicit transaction wants a
        real, consistent read (e.g. the advisory-locked register_player path)."""
        cache_key = f"players:by_steam:{steam_id}"
        if conn is None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return dict(cached) if cached else None

        if await self._has_linked_steam_ids_column():
            query = """
            SELECT *
            FROM IOSCA_PLAYERS
            WHERE steam_id = $1
               OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(COALESCE(linked_steam_ids, '[]'::jsonb)) AS linked(value)
                    WHERE linked.value = $1
               )
            LIMIT 1
            """
        else:
            query = "SELECT * FROM IOSCA_PLAYERS WHERE steam_id = $1"
        row = await (conn.fetchrow(query, steam_id) if conn is not None else self.pool.fetchrow(query, steam_id))
        result = dict(row) if row else None
        if conn is None:
            self._cache.set(cache_key, result if result is not None else {})
        return result

    async def link_secondary_steam_id(
        self,
        secondary_steam_id: str,
        primary_steam_id: Optional[str] = None,
        discord_id: Optional[int] = None,
    ) -> tuple[bool, str]:
        """Link a secondary Steam ID to an existing IOSCA_PLAYERS row."""
        sid = str(secondary_steam_id or "").strip()
        if not sid:
            return False, "Missing secondary Steam ID."
        if not await self._has_linked_steam_ids_column():
            return False, "Schema missing linked_steam_ids column."

        target_row: Optional[Dict[str, Any]] = None
        if primary_steam_id:
            target_row = await self.get_player_by_steam_id(str(primary_steam_id).strip())
        elif discord_id is not None:
            target_row = await self.get_player_by_discord_id(discord_id)
        else:
            return False, "Provide primary_steam_id or discord_id."

        if not target_row:
            return False, "Target player not found."

        primary_sid = str(target_row.get("steam_id") or "").strip()
        if not primary_sid:
            return False, "Target player has no primary Steam ID."
        if sid == primary_sid:
            return True, "Secondary Steam ID matches primary Steam ID."

        owner_row = await self.pool.fetchrow(
            """
            SELECT steam_id
            FROM IOSCA_PLAYERS
            WHERE steam_id = $1
               OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(COALESCE(linked_steam_ids, '[]'::jsonb)) AS linked(value)
                    WHERE linked.value = $1
               )
            LIMIT 1
            """,
            sid,
        )
        if owner_row:
            existing_owner = str(owner_row.get("steam_id") or "").strip()
            if existing_owner and existing_owner != primary_sid:
                return False, f"Steam ID already linked to another player ({existing_owner})."

        # If the secondary Steam ID exists as another primary row, remove that duplicate row.
        duplicate_primary = await self.pool.fetchrow(
            "SELECT steam_id FROM IOSCA_PLAYERS WHERE steam_id = $1",
            sid,
        )
        if duplicate_primary and str(duplicate_primary.get("steam_id") or "").strip() != primary_sid:
            await self.pool.execute("DELETE FROM IOSCA_PLAYERS WHERE steam_id = $1", sid)

        await self.pool.execute(
            """
            UPDATE IOSCA_PLAYERS
            SET linked_steam_ids = (
                    SELECT to_jsonb(ARRAY(
                        SELECT DISTINCT value
                        FROM (
                            SELECT jsonb_array_elements_text(COALESCE(linked_steam_ids, '[]'::jsonb)) AS value
                            UNION ALL
                            SELECT $1::text AS value
                        ) s
                        WHERE value IS NOT NULL AND btrim(value) <> ''
                        ORDER BY value
                    ))
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE steam_id = $2
            """,
            sid,
            primary_sid,
        )
        self.invalidate_ratings_cache()
        return True, f"Linked {sid} to {primary_sid}."
    
    async def get_player_by_discord_id(self, discord_id: int, conn=None) -> Optional[Dict[str, Any]]:
        """Retrieve a player's record by their Discord ID. Pass `conn` to run
        on an already-acquired connection instead of checking out a separate
        one from the pool -- caching is skipped in that case, same reasoning
        as get_player_by_steam_id."""
        discord_id_value = await self._coerce_discord_id(discord_id)
        cache_key = f"players:by_discord:{discord_id_value}"
        if conn is None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return dict(cached) if cached else None

        query = "SELECT * FROM IOSCA_PLAYERS WHERE discord_id = $1"
        row = await (conn.fetchrow(query, discord_id_value) if conn is not None else self.pool.fetchrow(query, discord_id_value))
        result = dict(row) if row else None
        if conn is None:
            self._cache.set(cache_key, result if result is not None else {})
        return result
    
    async def update_player_rating(self, discord_id: int, rating: float) -> bool:
        """Update a player's rating"""
        query = "UPDATE IOSCA_PLAYERS SET rating = $1, updated_at = CURRENT_TIMESTAMP WHERE discord_id = $2"
        
        try:
            discord_id_value = await self._coerce_discord_id(discord_id)
            await self.pool.execute(query, rating, discord_id_value)
            self.invalidate_ratings_cache()
            return True
        except Exception as e:
            logger.error(f"Failed to update player rating: {e}")
            return False
    
    async def get_player_stats(self, discord_id: int) -> Optional[Dict[str, Any]]:
        """Get player statistics including rating"""
        name_column = await self._get_name_column()
        if name_column:
            query = f"""
            SELECT discord_id, {name_column} AS username, steam_id, COALESCE(display_main_role_rating, rating) AS rating, created_at, updated_at
            FROM IOSCA_PLAYERS
            WHERE discord_id = $1
            """
        else:
            query = """
            SELECT discord_id, NULL::text AS username, steam_id, COALESCE(display_main_role_rating, rating) AS rating, created_at, updated_at
            FROM IOSCA_PLAYERS
            WHERE discord_id = $1
            """
        discord_id_value = await self._coerce_discord_id(discord_id)
        row = await self.pool.fetchrow(query, discord_id_value)
        return dict(row) if row else None
    
    async def get_top_players_by_rating(self, limit: int = 10) -> list:
        """Get top players by rating (cached until ratings change)."""
        cache_key = f"players:top_rating:{limit}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return list(cached)

        name_column = await self._get_name_column()
        if name_column:
            query = f"""
            SELECT discord_id, {name_column} AS username, COALESCE(display_main_role_rating, rating) AS rating
            FROM IOSCA_PLAYERS
            WHERE COALESCE(display_main_role_rating, rating) IS NOT NULL
            ORDER BY rating DESC
            LIMIT $1
            """
        else:
            query = """
            SELECT discord_id, NULL::text AS username, COALESCE(display_main_role_rating, rating) AS rating
            FROM IOSCA_PLAYERS
            WHERE COALESCE(display_main_role_rating, rating) IS NOT NULL
            ORDER BY rating DESC
            LIMIT $1
            """
        rows = await self.pool.fetch(query, limit)
        data = [dict(row) for row in rows]
        self._cache.set(cache_key, data)
        return data

    async def get_player_rating_snapshot(self, steam_id: str) -> Optional[Dict[str, Any]]:
        """Best-available rating snapshot for a steam_id or any of its linked
        alts (cached until ratings change -- used by /view_player)."""
        cache_key = f"players:rating_snapshot:{steam_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return dict(cached) if cached else None

        row = await self.pool.fetchrow(
            """
            SELECT
                ip.steam_id,
                ip.display_main_role_rating,
                ip.rating,
                ip.main_role_rating,
                ip.main_role,
                ip.atk_rating,
                ip.mid_rating,
                ip.def_rating,
                ip.gk_rating,
                COALESCE(ip.total_appearances, 0) AS total_appearances,
                COALESCE(ip.updated_at, ip.registered_at) AS sort_updated_at
            FROM IOSCA_PLAYERS ip
            WHERE ip.steam_id = $1
               OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(COALESCE(ip.linked_steam_ids, '[]'::jsonb)) AS linked(value)
                    WHERE linked.value = $1
               )
            ORDER BY
                CASE
                    WHEN COALESCE(ip.display_main_role_rating, ip.rating, ip.main_role_rating) IS NOT NULL THEN 0
                    WHEN ip.atk_rating IS NOT NULL
                      OR ip.mid_rating IS NOT NULL
                      OR ip.def_rating IS NOT NULL
                      OR ip.gk_rating IS NOT NULL
                        THEN 1
                    ELSE 2
                END,
                CASE WHEN ip.steam_id = $1 THEN 0 ELSE 1 END,
                COALESCE(ip.total_appearances, 0) DESC,
                COALESCE(ip.updated_at, ip.registered_at) DESC NULLS LAST
            LIMIT 1
            """,
            steam_id,
        )

        result: Optional[Dict[str, Any]] = None
        if row:
            role_ratings = {
                "ATK": float(row["atk_rating"]) if row.get("atk_rating") is not None else None,
                "MID": float(row["mid_rating"]) if row.get("mid_rating") is not None else None,
                "DEF": float(row["def_rating"]) if row.get("def_rating") is not None else None,
                "GK": float(row["gk_rating"]) if row.get("gk_rating") is not None else None,
            }
            available_role_ratings = [value for value in role_ratings.values() if value is not None]
            resolved_rating = next(
                (
                    float(value)
                    for value in (
                        row.get("display_main_role_rating"),
                        row.get("rating"),
                        row.get("main_role_rating"),
                    )
                    if value is not None
                ),
                None,
            )
            if resolved_rating is None and available_role_ratings:
                resolved_rating = max(available_role_ratings)

            result = {
                "steam_id": row.get("steam_id"),
                "rating": resolved_rating,
                "main_role": row.get("main_role"),
                "role_ratings": role_ratings,
            }

        self._cache.set(cache_key, result if result is not None else {})
        return result

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
            self.invalidate_ratings_cache()
            logger.info(f"Player {discord_id_value} deleted")
            return True
        except Exception as e:
            logger.error(f"Failed to delete player: {e}")
            return False

    async def merge_players(
        self,
        *,
        keep_discord_id: Optional[int] = None,
        keep_steam_id: Optional[str] = None,
        merge_discord_id: Optional[int] = None,
        merge_steam_id: Optional[str] = None,
        teams_ops=None,
    ) -> tuple[bool, str]:
        """Merge two IOSCA_PLAYERS rows that turned out to be the same person.

        The row resolved from keep_discord_id/keep_steam_id survives; the row
        resolved from merge_discord_id/merge_steam_id is folded into it:
        - the merge row's steam_id (and any of its own linked_steam_ids) are
          added to the kept row's linked_steam_ids
        - career stat totals (appearances/minutes, per-role breakdowns) are
          summed onto the kept row
        - any team roster entry (players list, captain_id, vice_captain_ids)
          referencing the merge row's discord_id or steam_id is repointed to
          the kept identity, via teams_ops (pass bot.db.teams) so roster
          caches invalidate correctly
        - the merge row is deleted (by steam_id, its real primary key --
          never by discord_id, since a corrupt duplicate could theoretically
          share a discord_id with the row we're keeping)

        Ratings are deliberately NOT recomputed here. Run /recalculate_all
        afterward so the rating comes from the actual combined match
        history, rather than trying to average two already-computed
        ratings, which isn't how the formula works.
        """
        keep_row = None
        if keep_steam_id:
            keep_row = await self.get_player_by_steam_id(str(keep_steam_id).strip())
        elif keep_discord_id is not None:
            keep_row = await self.get_player_by_discord_id(keep_discord_id)
        if not keep_row:
            return False, "Could not find the player to keep."

        merge_row = None
        if merge_steam_id:
            merge_row = await self.get_player_by_steam_id(str(merge_steam_id).strip())
        elif merge_discord_id is not None:
            merge_row = await self.get_player_by_discord_id(merge_discord_id)
        if not merge_row:
            return False, "Could not find the player to merge."

        keep_sid = str(keep_row.get("steam_id") or "").strip()
        merge_sid = str(merge_row.get("steam_id") or "").strip()
        if not keep_sid or not merge_sid:
            return False, "Both players need a primary Steam ID to merge."
        if keep_sid == merge_sid:
            return False, "Those are the same player row already."

        # Fold merge's steam identities into keep's linked_steam_ids.
        def _parse_linked(row):
            raw = row.get("linked_steam_ids") or []
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw) if raw else []
                except (json.JSONDecodeError, TypeError):
                    raw = []
            return [str(v).strip() for v in raw if str(v or "").strip()]

        merged_linked = _parse_linked(keep_row)
        for sid in [merge_sid] + _parse_linked(merge_row):
            if sid and sid != keep_sid and sid not in merged_linked:
                merged_linked.append(sid)

        # Decide the surviving discord_id: prefer keep's if it's a real,
        # already-assigned id; otherwise fall back to merge's.
        keep_discord = keep_row.get("discord_id")
        if _is_unassigned_discord_value(keep_discord):
            keep_discord = merge_row.get("discord_id")
        keep_discord_value = await self._coerce_discord_id(keep_discord) if keep_discord else keep_discord

        numeric_fields = [
            "total_appearances", "total_minutes",
            "atk_appearances", "mid_appearances", "def_appearances", "gk_appearances",
            "atk_minutes", "mid_minutes", "def_minutes", "gk_minutes",
        ]
        summed = {
            field: int(keep_row.get(field) or 0) + int(merge_row.get(field) or 0)
            for field in numeric_fields
        }

        name_column = await self._get_name_column()
        set_clauses = [
            "linked_steam_ids = $1",
            "discord_id = $2",
        ] + [f"{field} = ${i + 3}" for i, field in enumerate(numeric_fields)]
        params = [json.dumps(merged_linked), keep_discord_value] + [summed[f] for f in numeric_fields]
        params.append(keep_sid)

        try:
            await self.pool.execute(
                f"UPDATE IOSCA_PLAYERS SET {', '.join(set_clauses)}, updated_at = CURRENT_TIMESTAMP WHERE steam_id = ${len(params)}",
                *params,
            )
            # Real primary-key delete -- never by discord_id, which a
            # corrupt duplicate could share with the row we're keeping.
            await self.pool.execute("DELETE FROM IOSCA_PLAYERS WHERE steam_id = $1", merge_sid)
            self.invalidate_ratings_cache()
        except Exception as e:
            logger.error(f"Failed to merge players {keep_sid} <- {merge_sid}: {e}")
            return False, f"Database error during merge: {e}"

        teams_touched = 0
        if teams_ops is not None:
            merge_discord_norm = _normalize_discord_id_value(merge_row.get("discord_id"))
            try:
                all_teams = await teams_ops.get_all_teams_with_details()
            except Exception as e:
                logger.warning(f"Could not scan team rosters during player merge: {e}")
                all_teams = []

            for team in all_teams:
                changed = False
                players = team.get("players") or []
                for p in players:
                    if not isinstance(p, dict):
                        continue
                    p_discord = _normalize_discord_id_value(p.get("discord_id") or p.get("id"))
                    p_steam = str(p.get("steam_id") or "").strip()
                    if (merge_discord_norm and p_discord == merge_discord_norm) or (p_steam and p_steam == merge_sid):
                        if keep_discord_value is not None:
                            p["discord_id"] = keep_discord_value
                            p["id"] = keep_discord_value
                        p["steam_id"] = keep_sid
                        changed = True
                if changed:
                    try:
                        await teams_ops.update_team_players(team["guild_id"], players)
                        teams_touched += 1
                    except Exception as e:
                        logger.warning(f"Failed to repoint roster for team {team.get('guild_id')}: {e}")

                if team.get("captain_id") == merge_row.get("discord_id") and keep_discord_value is not None:
                    try:
                        await teams_ops.update_team_captain(team["guild_id"], keep_discord_value, keep_row.get(name_column) if name_column else None)
                    except Exception as e:
                        logger.warning(f"Failed to repoint captain for team {team.get('guild_id')}: {e}")

        summary = (
            f"Merged steam_id {merge_sid} into {keep_sid}. "
            f"linked_steam_ids now has {len(merged_linked)} entr{'y' if len(merged_linked) == 1 else 'ies'}. "
            f"Repointed {teams_touched} team roster(s). "
            f"Run /recalculate_all next so ratings reflect the combined match history."
        )
        return True, summary
    
    async def get_top_team_players(self, guild_id: int, limit: int = 10) -> list:
        """Get top players for a team based on match performance.

        Cached on a TTL-only basis (no explicit write-triggered invalidation
        wired in here, since new match rows land via MatchOperations, a
        different class) -- a few minutes of staleness after a fresh import
        is an acceptable trade-off for a leaderboard-style read."""
        cache_key = f"players:top_team:{guild_id}:{limit}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return list(cached)

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
            data = [dict(row) for row in rows]
            self._cache.set(cache_key, data, ttl_seconds=300)
            return data
        except Exception as e:
            logger.error(f"Failed to get top team players: {e}")
            return []

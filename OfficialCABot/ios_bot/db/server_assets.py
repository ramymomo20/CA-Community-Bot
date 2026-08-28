"""
Server asset operations for guild-level role/emoji mappings.
"""

import logging
from typing import Any, Dict, List, Optional

from .connection import DatabasePool

logger = logging.getLogger(__name__)


class ServerAssetOperations:
    """Handles CRUD operations for SERVER_ASSETS."""

    def __init__(self, pool: DatabasePool):
        self.pool = pool

    async def upsert_asset(
        self,
        guild_id: int,
        asset_type: str,
        asset_key: str,
        discord_id: Optional[str] = None,
        asset_name: Optional[str] = None,
        raw_value: Optional[str] = None,
        created_by: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        query = """
        INSERT INTO SERVER_ASSETS (
            guild_id, asset_type, asset_key, discord_id, asset_name, raw_value, created_by, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
        ON CONFLICT (guild_id, asset_type, asset_key)
        DO UPDATE SET
            discord_id = EXCLUDED.discord_id,
            asset_name = EXCLUDED.asset_name,
            raw_value = EXCLUDED.raw_value,
            created_by = EXCLUDED.created_by,
            updated_at = NOW()
        RETURNING *
        """
        try:
            row = await self.pool.fetchrow(
                query,
                int(guild_id),
                str(asset_type).strip().lower(),
                str(asset_key).strip().lower(),
                str(discord_id).strip() if discord_id is not None else None,
                asset_name,
                raw_value,
                int(created_by) if created_by is not None else None,
            )
            return dict(row) if row else None
        except Exception as e:
            logger.error("Failed to upsert server asset: %s", e)
            return None

    async def delete_asset(self, guild_id: int, asset_type: str, asset_key: str) -> bool:
        query = """
        DELETE FROM SERVER_ASSETS
        WHERE guild_id = $1
          AND asset_type = $2
          AND asset_key = $3
        """
        try:
            result = await self.pool.execute(
                query,
                int(guild_id),
                str(asset_type).strip().lower(),
                str(asset_key).strip().lower(),
            )
            return str(result).upper().startswith("DELETE") and not str(result).endswith(" 0")
        except Exception as e:
            logger.error("Failed to delete server asset: %s", e)
            return False

    async def list_assets(self, guild_id: int, asset_type: Optional[str] = None) -> List[Dict[str, Any]]:
        if asset_type:
            query = """
            SELECT *
            FROM SERVER_ASSETS
            WHERE guild_id = $1 AND asset_type = $2
            ORDER BY asset_type, asset_key
            """
            rows = await self.pool.fetch(query, int(guild_id), str(asset_type).strip().lower())
        else:
            query = """
            SELECT *
            FROM SERVER_ASSETS
            WHERE guild_id = $1
            ORDER BY asset_type, asset_key
            """
            rows = await self.pool.fetch(query, int(guild_id))
        return [dict(row) for row in rows]

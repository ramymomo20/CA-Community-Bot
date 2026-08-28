from __future__ import annotations

from typing import Any


MATCH_LINK_SQL = """
(
    pmd.match_id::text = ms.match_id::text
    OR (CASE WHEN pmd.match_id::text ~ '^[0-9]+$' THEN pmd.match_id::bigint END) = ms.id::bigint
)
"""


async def ensure_stats_moderation_schema(conn: Any) -> None:
    """Create moderation tables and filtered views for counted match stats."""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public.match_stats_exclusions (
            match_stats_id INTEGER PRIMARY KEY REFERENCES public.match_stats(id) ON DELETE CASCADE,
            exclude_from_stats BOOLEAN NOT NULL DEFAULT TRUE,
            reason TEXT,
            updated_by_discord_id BIGINT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public.player_match_stat_exclusions (
            match_stats_id INTEGER NOT NULL REFERENCES public.match_stats(id) ON DELETE CASCADE,
            steam_id VARCHAR(255) NOT NULL,
            exclude_from_stats BOOLEAN NOT NULL DEFAULT TRUE,
            reason TEXT,
            updated_by_discord_id BIGINT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            PRIMARY KEY (match_stats_id, steam_id)
        )
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_match_stats_exclusions_enabled
        ON public.match_stats_exclusions (exclude_from_stats)
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_match_stat_exclusions_match
        ON public.player_match_stat_exclusions (match_stats_id)
        """
    )
    await conn.execute("CREATE SCHEMA IF NOT EXISTS hub")
    await conn.execute(
        """
        CREATE OR REPLACE VIEW public.counted_match_stats AS
        SELECT ms.*
        FROM public.match_stats ms
        LEFT JOIN public.match_stats_exclusions mse
          ON mse.match_stats_id = ms.id
        WHERE NOT COALESCE(mse.exclude_from_stats, FALSE)
        """
    )
    await conn.execute(
        f"""
        CREATE OR REPLACE VIEW public.counted_player_match_data AS
        SELECT pmd.*
        FROM public.player_match_data pmd
        LEFT JOIN public.match_stats ms
          ON {MATCH_LINK_SQL}
        LEFT JOIN public.match_stats_exclusions mse
          ON mse.match_stats_id = ms.id
        LEFT JOIN public.player_match_stat_exclusions pmse
          ON pmse.match_stats_id = ms.id
         AND lower(btrim(pmse.steam_id)) = lower(btrim(pmd.steam_id))
        WHERE NOT COALESCE(mse.exclude_from_stats, FALSE)
          AND NOT COALESCE(pmse.exclude_from_stats, FALSE)
        """
    )
    await conn.execute(
        """
        CREATE OR REPLACE VIEW hub.match_stats AS
        SELECT *
        FROM public.counted_match_stats
        """
    )
    await conn.execute(
        """
        CREATE OR REPLACE VIEW hub.player_match_data AS
        SELECT *
        FROM public.counted_player_match_data
        """
    )

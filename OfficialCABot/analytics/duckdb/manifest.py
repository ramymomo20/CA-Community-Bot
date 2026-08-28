from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SnapshotSpec:
    source_name: str
    source_sql: str
    target_schema: str
    target_table: str
    primary_keys: tuple[str, ...]
    mode: str = "incremental"
    watermark_column: str | None = "updated_at"


SNAPSHOT_SPECS: tuple[SnapshotSpec, ...] = (
    SnapshotSpec(
        source_name="core.v_account_primary_identities",
        source_sql="""
            SELECT *
            FROM core.v_account_primary_identities
        """,
        target_schema="core_cache",
        target_table="account_primary_identities",
        primary_keys=("account_id",),
        mode="full",
        watermark_column=None,
    ),
    SnapshotSpec(
        source_name="core.player_ratings_current",
        source_sql="""
            SELECT *
            FROM core.player_ratings_current
        """,
        target_schema="core_cache",
        target_table="player_ratings_current",
        primary_keys=("account_id",),
        mode="full",
        watermark_column=None,
    ),
    SnapshotSpec(
        source_name="core.team_ratings_current",
        source_sql="""
            SELECT *
            FROM core.team_ratings_current
        """,
        target_schema="core_cache",
        target_table="team_ratings_current",
        primary_keys=("team_id",),
        mode="full",
        watermark_column=None,
    ),
    SnapshotSpec(
        source_name="hub.homepage_snapshots",
        source_sql="""
            SELECT *
            FROM hub.homepage_snapshots
        """,
        target_schema="hub_cache",
        target_table="homepage_snapshots",
        primary_keys=("snapshot_key",),
        watermark_column="generated_at",
    ),
    SnapshotSpec(
        source_name="hub.player_summaries",
        source_sql="""
            SELECT *
            FROM hub.player_summaries
        """,
        target_schema="hub_cache",
        target_table="player_summaries",
        primary_keys=("account_id",),
    ),
    SnapshotSpec(
        source_name="hub.player_profiles",
        source_sql="""
            SELECT *
            FROM hub.player_profiles
        """,
        target_schema="hub_cache",
        target_table="player_profiles",
        primary_keys=("account_id",),
    ),
    SnapshotSpec(
        source_name="hub.team_summaries",
        source_sql="""
            SELECT *
            FROM hub.team_summaries
        """,
        target_schema="hub_cache",
        target_table="team_summaries",
        primary_keys=("team_id",),
    ),
    SnapshotSpec(
        source_name="hub.team_profiles",
        source_sql="""
            SELECT *
            FROM hub.team_profiles
        """,
        target_schema="hub_cache",
        target_table="team_profiles",
        primary_keys=("team_id",),
    ),
    SnapshotSpec(
        source_name="hub.match_summaries",
        source_sql="""
            SELECT *
            FROM hub.match_summaries
        """,
        target_schema="hub_cache",
        target_table="match_summaries",
        primary_keys=("match_id",),
    ),
    SnapshotSpec(
        source_name="hub.match_details",
        source_sql="""
            SELECT *
            FROM hub.match_details
        """,
        target_schema="hub_cache",
        target_table="match_details",
        primary_keys=("match_id",),
    ),
    SnapshotSpec(
        source_name="hub.tournament_summaries",
        source_sql="""
            SELECT *
            FROM hub.tournament_summaries
        """,
        target_schema="hub_cache",
        target_table="tournament_summaries",
        primary_keys=("tournament_id",),
    ),
    SnapshotSpec(
        source_name="hub.tournament_details",
        source_sql="""
            SELECT *
            FROM hub.tournament_details
        """,
        target_schema="hub_cache",
        target_table="tournament_details",
        primary_keys=("tournament_id",),
    ),
    SnapshotSpec(
        source_name="hub.rankings_snapshots",
        source_sql="""
            SELECT *
            FROM hub.rankings_snapshots
        """,
        target_schema="hub_cache",
        target_table="rankings_snapshots",
        primary_keys=("ranking_key",),
    ),
    SnapshotSpec(
        source_name="hub.media_summaries",
        source_sql="""
            SELECT *
            FROM hub.media_summaries
        """,
        target_schema="hub_cache",
        target_table="media_summaries",
        primary_keys=("media_asset_id",),
    ),
)

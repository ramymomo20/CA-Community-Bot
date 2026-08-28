# Canonical Schema Deployment

This repository now has one schema source of truth:

- `schema.txt`

This file must be synchronized into:

- `migrations/FINAL_MERGED_SCHEMA.sql`

## Core model

- `core.accounts` is the canonical person record.
- One person can have many Discord identities and many Steam identities.
- Ratings belong to `account_id`, not to a Steam ID row.
- `core.teams`, `core.team_channels`, and `core.team_memberships` replace JSON roster/channel blobs.
- `hub.*` tables remain read-optimized publication tables for the website.

## Deployment flow

1. Edit `schema.txt`.
2. Run `python scripts/sync_canonical_schema.py`.
3. Apply the schema with `python scripts/apply_canonical_schema.py`.
4. Refresh the low-cost DuckDB cache with `python -m analytics.duckdb.refresh_cache`.

## DuckDB + Polars role

PostgreSQL remains the source of truth for:

- bot writes
- match ingestion
- identity linking
- ratings
- tournament state

DuckDB becomes the cheap local read cache for:

- hub page payloads
- ranking snapshots
- player/team profile snapshots
- offline analytics
- batch exports

Polars is used in the cache refresh step to normalize fetched PostgreSQL rows into efficient columnar frames before registering them in DuckDB.

## Why this reduces cost

- The hub can read from a local DuckDB snapshot instead of continuously hitting Supabase.
- The cache refresh can run on a schedule that matches community activity windows.
- Only selected hub/core snapshot tables are copied into the cache.
- Incremental refreshes rely on `updated_at` or `generated_at` watermarks where available.

## Recommended refresh cadence

- `hub.match_summaries`, `hub.match_details`: every 5-10 minutes during active hours only
- `hub.player_summaries`, `hub.player_profiles`: hourly during active hours
- `hub.team_summaries`, `hub.team_profiles`: every 6-12 hours
- `hub.tournament_*`, `hub.rankings_snapshots`, `hub.homepage_snapshots`: on publish or every 15-30 minutes while active
- `core.player_ratings_current`, `core.team_ratings_current`: full refresh after each ratings run

## Important rule

If the schema changes in the future, `schema.txt` and `migrations/FINAL_MERGED_SCHEMA.sql` must be updated together. The sync script exists to enforce that discipline.

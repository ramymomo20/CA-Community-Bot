# Canonical Deployment Runbook

This repository now assumes a canonical account-first database design.

- operational source of truth: `core.*`
- hub read model: `hub.*`
- analytics cache: DuckDB snapshots built from canonical reads

## Current state

- bot runtime should read and write only canonical operational tables
- hub sync should publish only from canonical `core.*` and canonical `hub.*` summaries
- old public operational tables are not part of the supported runtime path anymore

## Safe deployment order

1. Apply [migrations/FINAL_MERGED_SCHEMA.sql](C:/Users/narub/OneDrive%20-%20Personal/OneDrive/Documents/Projects%20&%20Other%20Works/Python/Projects/CA-Community-Bot/DiscordBotNA/migrations/FINAL_MERGED_SCHEMA.sql).
2. Deploy the updated bot code.
3. Deploy the hub backend sync code.
4. Refresh hub mirror tables from canonical summaries.
5. Verify registration, team management, ratings, match ingestion, tournament reads, and hub pages.
6. Migrate historical rows into `core.*` if production data still lives elsewhere.
7. After verification, retire unused old tables outside the canonical schema.

## Commands to run

From repo root:

1. `python scripts/sync_canonical_schema.py`
2. `python scripts/apply_canonical_schema.py`
3. `python -m analytics.duckdb.refresh_cache`

## Migration phases

1. `discord_guilds_and_assets`
2. `accounts_and_identities`
3. `teams_and_memberships`
4. `game_servers_and_credentials`
5. `matches_and_player_entries`
6. `ratings_snapshots`
7. `tournaments`
8. `media`
9. `hub_snapshots`

## Verification checklist

- canonical row counts match expectations
- player registration resolves to one account per real person
- team rosters and captain links resolve correctly
- imported matches land in `core.matches` and `core.match_player_entries`
- ratings rebuild succeeds from canonical match history
- hub sync succeeds from canonical source data
- DuckDB refresh completes without fallback logic

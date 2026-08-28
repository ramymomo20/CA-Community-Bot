# Canonical Schema Mapping

This repository now uses a single account-first operational schema:

- bot source of truth: `core.*`
- hub publication layer: `hub.*`
- analytics cache layer: DuckDB snapshots derived from canonical reads

## Identities and players

- person record: `core.accounts`
- Discord identities: `core.account_discord_identities`
- Steam identities: `core.account_steam_identities`
- aliases and name history: `core.account_aliases`
- current player ratings: `core.player_ratings_current`
- player rating checkpoints: `core.player_rating_snapshots`
- hub player card data: `hub.player_summaries`
- hub player profile payloads: `hub.player_profiles`

## Registration and account linking

- registration tokens: `core.registration_intents`
- link and merge audit trail: `core.account_link_audit`

## Teams

- team identity: `core.teams`
- team channels: `core.team_channels`
- roster and staff memberships: `core.team_memberships`
- current team ratings: `core.team_ratings_current`
- team rating checkpoints: `core.team_rating_snapshots`
- hub team card data: `hub.team_summaries`
- hub team profile payloads: `hub.team_profiles`

## Servers and ingestion

- game server records: `core.game_servers`
- connection credentials: `core.game_server_credentials`
- import runs: `core.match_import_runs`
- source file tracking: `core.match_sources`

## Matches and events

- match headers: `core.matches`
- origin routing context: `core.match_origin_contexts`
- player entries per match: `core.match_player_entries`
- normalized match events: `core.match_events`
- excluded sources and matches: `core.excluded_match_sources`, `core.excluded_matches`

## Tournaments

- tournament metadata: `core.tournaments`
- phases and groups: `core.tournament_phases`
- team entries: `core.tournament_entries`
- fixtures: `core.tournament_fixtures`
- current standings: `core.tournament_standings_current`
- hub tournament summaries: `hub.tournament_summaries`
- hub tournament detail payloads: `hub.tournament_details`

## Media

- source assets: `core.media_assets`
- related account links: `core.media_asset_accounts`
- hub media summaries: `hub.media_summaries`

## Hub snapshots

- homepage stories: `hub.homepage_snapshots`
- rankings payloads: `hub.rankings_snapshots`
- mirrored read state: `hub.snapshot_runs`

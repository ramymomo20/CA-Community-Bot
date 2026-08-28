# IOSCA Rating Workflow Review

This document reflects the current canonical workflow used by the bot and hub.

## Runtime pipeline

1. Stats refresh is triggered by scheduled tasks or admin actions.
2. Source JSON files are discovered from configured game servers.
3. Match payloads are parsed and imported into canonical match storage.
4. Canonical player entries and events are written for each match.
5. Ratings are regenerated from canonical match history.
6. Team averages and hub summaries are refreshed from canonical outputs.

## Canonical source tables

- match headers: `core.matches`
- player entries: `core.match_player_entries`
- events: `core.match_events`
- current ratings: `core.player_ratings_current`
- rating snapshots: `core.player_rating_snapshots`
- current team ratings: `core.team_ratings_current`
- hub player summaries: `hub.player_summaries`
- hub team summaries: `hub.team_summaries`
- hub match summaries: `hub.match_summaries`

## Important properties

- one real person should resolve to one `core.accounts` row
- multiple Discord IDs or Steam IDs attach as identities to the same account
- ratings are rebuilt from canonical match history, not ad hoc aggregates
- hub reads from canonical hub summaries instead of operational write tables

## Operational checks

- imported matches appear in `core.matches`
- every participating player appears in `core.match_player_entries`
- rating runs create or update `core.rating_runs`
- current ratings land in `core.player_ratings_current`
- daily checkpoints land in `core.player_rating_snapshots`
- hub sync reads canonical summaries and updates the public hub mirrors

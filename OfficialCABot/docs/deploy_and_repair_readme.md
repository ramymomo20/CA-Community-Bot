# Deploy And Repair Readme

This is the practical runbook for deploying the canonical bot/database setup and for repairing links after the first boot.

## What this assumes

- canonical schema lives in `core.*`
- hub read models live in `hub.*`
- bot code is already updated to use the canonical schema
- old runtime tables are no longer the supported write path

## Important files

- [migrations/FINAL_MERGED_SCHEMA.sql](C:/Users/narub/OneDrive%20-%20Personal/OneDrive/Documents/Projects%20&%20Other%20Works/Python/Projects/CA-Community-Bot/DiscordBotNA/migrations/FINAL_MERGED_SCHEMA.sql)
- [migrations/2026_08_07_seed_canonical_bootstrap.sql](C:/Users/narub/OneDrive%20-%20Personal/OneDrive/Documents/Projects%20&%20Other%20Works/Python/Projects/CA-Community-Bot/DiscordBotNA/migrations/2026_08_07_seed_canonical_bootstrap.sql)
- [migrations/2026_08_08_seed_canonical_players.sql](C:/Users/narub/OneDrive%20-%20Personal/OneDrive/Documents/Projects%20&%20Other%20Works/Python/Projects/CA-Community-Bot/DiscordBotNA/migrations/2026_08_08_seed_canonical_players.sql)
- [migrations/2026_08_08_backfill_team_leadership_from_legacy.sql](C:/Users/narub/OneDrive%20-%20Personal/OneDrive/Documents/Projects%20&%20Other%20Works/Python/Projects/CA-Community-Bot/DiscordBotNA/migrations/2026_08_08_backfill_team_leadership_from_legacy.sql)
- [migrations/2026_08_08_backfill_team_memberships.sql](C:/Users/narub/OneDrive%20-%20Personal/OneDrive/Documents/Projects%20&%20Other%20Works/Python/Projects/CA-Community-Bot/DiscordBotNA/migrations/2026_08_08_backfill_team_memberships.sql)
- [migrations/2026_08_08_seed_international_rydon_cup.sql](C:/Users/narub/OneDrive%20-%20Personal/OneDrive/Documents/Projects%20&%20Other%20Works/Python/Projects/CA-Community-Bot/DiscordBotNA/migrations/2026_08_08_seed_international_rydon_cup.sql)
- [migrations/2026_08_22_backfill_legacy_matches_and_events.sql](C:/Users/narub/OneDrive%20-%20Personal/OneDrive/Documents/Projects%20&%20Other%20Works/Python/Projects/CA-Community-Bot/DiscordBotNA/migrations/2026_08_22_backfill_legacy_matches_and_events.sql)
- [toggle_hub_site.py](C:/Users/narub/OneDrive%20-%20Personal/OneDrive/Documents/Projects%20&%20Other%20Works/Python/Projects/CA-Community-Bot/DiscordBotNA/toggle_hub_site.py)

## Environment before first boot

Make sure these are set:

- `DISCORD_BOT_TOKEN`
- `SUPABASE_DB_URL` or `SUPABASE_POOLER_URL`
- `WEBHOOK_SECRET`
- `ENABLE_WEBHOOK_SERVER=1` if you want webhook listener on
- `WEBHOOK_PORT=5000` or your chosen port
- `IOSCA_HUB_ENABLED=0` if you want to boot the bot without starting the hub backend

## Recommended first deployment order

1. Create or reset the target schema in Supabase.
2. Apply `FINAL_MERGED_SCHEMA.sql`.
3. Apply canonical seed files for guilds, assets, teams, players, tournaments, and game servers.
4. Apply team leadership and team membership backfills.
5. Apply the legacy match migration if you want old match history in immediately.
6. Disable hub updates before first live boot:
   - `python toggle_hub_site.py disable "first canonical deploy"`
7. Start the bot.
8. Let startup finish completely.
9. Run repair/admin commands in the order below.
10. Test one real match flow.
11. Re-enable hub only after bot import/rating flow is healthy:
   - `python toggle_hub_site.py enable`

## First boot admin command order

Run these after the bot is online.

1. `/sync_sftp_matches`
   Use this to import recent missing SFTP matches into the canonical tables.

2. `/rebuild_match_data_from_json`
   Use this to reparse stored JSON and rewrite canonical match, player, and event rows in place.

3. `/reevaluate_all_games`
   Use this to:
   - relink match team IDs
   - backfill player guild/team links
   - backfill player event timestamps
   - backfill canonical match event rows

4. `/recalculate_all`
   Use this after imports and relinks so player ratings, team ratings, and downstream match performance are recalculated from canonical history.

## Repair commands and what they do

### `/sync_sftp_matches`

Use when:

- recent matches were never imported
- bot was offline
- SFTP JSON files exist but DB rows do not

### `/rebuild_match_data_from_json`

Use when:

- canonical match rows exist but player/event details are stale
- importer logic changed and old rows need rebuild
- you want to refresh DB rows directly from stored JSON

### `/reevaluate_all_games`

Use when:

- team links are wrong or missing
- player guild/team links are wrong or missing
- event timestamps are missing
- canonical `core.match_events` rows need to be rebuilt

This is the strongest repair command right now.

### `/backfill_match_links`

Use when:

- match home and away teams are not linked correctly
- exact/fuzzy team-name matching needs to be rerun

### `/link_tournament_fixture_match`

Use when:

- a real played match exists
- a tournament fixture exists
- the automatic link did not happen

This is the manual tournament fixture-to-match repair command.

### `/recalculate_all`

Use when:

- canonical matches/player entries are correct
- ratings need to be recomputed cleanly

This is the final step after heavy repair or migration.

## Suggested first live verification

After the command sequence above, verify:

1. `/view_teams` loads without errors.
2. `/view_player` loads a migrated player correctly.
3. `/view_match` loads one migrated match correctly.
4. One tournament fixture can be viewed and linked.
5. One fresh SFTP import lands in:
   - `core.matches`
   - `core.match_player_entries`
   - `core.match_events`
6. One ratings rebuild completes without fatal errors.

## Hub control

### Disable hub updates

```powershell
python toggle_hub_site.py disable "maintenance"
```

### Check status

```powershell
python toggle_hub_site.py status
```

### Re-enable

```powershell
python toggle_hub_site.py enable
```

When disabled:

- hub refresh tasks are skipped
- hub story/read model refreshes are skipped
- bot runtime still works

## Notes about IOSCA main guild matches

- `IOSoccer Central America`
- `IOSoccer Central America A`
- `IOSoccer Central America B`

These aliases currently map to the main guild identity, not separate canonical teams.

So:

- this is good if you want all mix stats to belong to IOSCA
- this is not enough if you want `IOSCA A` and `IOSCA B` to behave like two fully separate canonical teams

## If something goes wrong

Use this recovery order:

1. disable hub
2. run `/sync_sftp_matches`
3. run `/rebuild_match_data_from_json`
4. run `/reevaluate_all_games`
5. run `/recalculate_all`
6. inspect one player, one team, one match, one fixture

## Short practical deploy summary

1. apply schema
2. apply seed/backfill SQL
3. disable hub
4. boot bot
5. sync missing matches
6. rebuild match data from JSON
7. reevaluate all games
8. recalculate all ratings
9. verify one live flow
10. enable hub

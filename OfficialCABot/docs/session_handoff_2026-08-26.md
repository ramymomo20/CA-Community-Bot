# Session handoff — CA Community Bot (IOSCA)

Written mid-session so work can continue after a context compaction without
confusion. Read this fully before assuming anything about repo state.

## Where this session started

User pasted a large handoff brief (`CLAUDE_CODE_HANDOFF.md` in repo root)
describing a canonical `core`/`hub` schema migration effort. Key correction
made early: that brief's understanding of `core`/`hub` was **stale** —
`core`/`hub` already contained real leftover data from a previously failed
migration attempt (via a different AI tool), not empty scaffolding as the
brief assumed. That leftover data was **wiped** (`DROP SCHEMA core/hub
CASCADE`) with the user's explicit go-ahead, since `public.*` is the real
production source of truth/backup. Confirmed no cross-schema FK/view
dependencies before dropping, so `public` was never at risk.

**The live bot in production (SparkedHost VPS) reads/writes the `public`
schema exclusively.** `core`/`hub` is a separate, parallel migration track —
nothing in `ios_bot`'s actual runtime has been repointed to `core` yet. All
the live-bot code changes described below (Steam ID parsing, `/ready` flow,
caching, team lifecycle, player merge) target `public.*` and the actual
`ios_bot/` Python source, since that's what's actually running.

## Part 1 — the core/hub migration (separate parallel track, COMPLETE)

Ran in this order, each phase verified before moving to the next:

1. **Schema rebuild**: `migrations/2026_08_26_canonical_core_hub_schema.sql`
   — the proposed schema from `docs/canonical_schema_v2_proposed_2026-08-03.sql`,
   applied for real this time, with three fixes: (a) club/national team
   exclusivity is now scoped per team_type via a `team_type_snapshot` column
   + trigger (captain/vice_captain exempt in both scopes — a player can hold
   one club + one national membership at once, unlimited mix), (b) added
   `core.transfer_requests`, (c) `core.game_server_credentials` now has
   `password_encrypted BYTEA` instead of plaintext (app-side encrypt/decrypt
   wiring is NOT done — separate follow-up). Also added a real
   `core.team_aliases` table (see Part 2).

2. **Accounts** (`scripts/migrate_accounts_identities.py`): 502
   `public.iosca_players` rows → 493 `core.accounts`, via union-find over
   `linked_steam_ids` (which already encoded which placeholder rows were the
   same person) rather than naive column copying. 9 rows merged into 8
   accounts. Zero conflicts.

3. **Teams** (`scripts/migrate_teams.py`): 36 teams, 61 channels, 43
   memberships. Per user decision: club rosters intentionally NOT copied
   (only captain/vice_captain — old rosters had heavy cross-team overlap,
   25/86 tracked players were on >1 "club" team). National/mix copied in
   full (zero conflicts there).

4. **Matches** (`scripts/migrate_matches.py`): 2,010 matches, 25,427 player
   entries, 11,674 events. Hit and fixed a real bug (54 columns / 53
   placeholders in the INSERT — caught before any data was written, clean
   transaction rollback confirmed).

5. **Tournaments** (`scripts/migrate_tournaments.py`): 3 tournaments → 8
   phases (league groups A/B + playoff/final/consolation — something the
   flat legacy schema couldn't represent), 24 entries, 73 fixtures, 51
   schedules. Added bracket-progression + forfeit-score columns to
   `core.tournament_fixtures` and tournament/fixture/schedule linkage
   columns to `core.match_origin_contexts` (both were in the original
   proposal's gaps).

6. **Team-name backfill** (`scripts/backfill_team_aliases.py` +
   `scripts/fix_backfill_team_dupes.py` + `scripts/relink_fixture_entries.py`):
   mined `match_stats` for team names that never resolved to a registered
   guild_id. 32 country names + "Central America" → aliased to the main
   guild (1,283 matches recovered). Two judgment-call aliases user
   confirmed (Bulls-B→Bulls FC, Promise FC→Promise Academy). **Caught my own
   mistake here**: initially claimed some unresolved names (Claro Riders,
   Dinasty SC, etc.) were already-registered teams — that was wrong, based
   on confusing leftover placeholder data from the dropped failed-migration
   schema with real data. Corrected by cross-checking against the real
   `core.teams` list, found 3 of 7 "new" teams were actually renamed
   duplicates of already-registered teams (merged via
   `fix_backfill_team_dupes.py`), one (Velocity SC) was missing from my own
   list by mistake (added via a follow-up script), and one more
   (Natural Born Crackheads) turned out to be a real registered team that
   played real fixtures but was never added to that tournament's roster in
   the source data (backfilled its entry).

All migration scripts support `--dry-run` (default) / `--apply` and are
idempotent-ish (`ON CONFLICT DO NOTHING` where relevant) — safe to re-read
for reference. One deliberately unresolved loose end: a single never-played
scheduled fixture ("Mexico" vs "Colombia") touches a naming collision
("Mexico" is claimed by both a registered national team "Mexico CA" and a
separate registered club literally named "Mexico") — zero real-match impact,
left alone on purpose.

**core/hub migration is DONE.** Nothing further planned there unless the
user asks to actually cut the live bot over to reading `core` instead of
`public` — that would be a large, separate task, not started.

## Part 2 — live bot fixes (public schema + ios_bot/ source, ONGOING)

All of this targets the actual production code path.

### Done:

- **JSON truncation bug fixed** (`ios_bot/ratings/compile_stats.py`): the
  reported error `Expecting value: line 1 column 102400 (char 102399)` was
  a known Paramiko footgun — `sftp.open(filename,'r')` + `f.prefetch()` +
  bare `json.load(f)` can return a truncated buffer instead of blocking for
  the whole file (102400 = exactly 100×1024, a prefetch chunk boundary).
  Added `_read_remote_json()`: reads in an explicit chunk loop, verifies
  byte count against `sftp.stat().st_size` before parsing. Fixed at both
  call sites (`download_match_files_from_server` and its `_resilient`
  variant).

- **Steam ID conversion verified correct** — `convert_steam_id()` in
  `ios_bot/utils/json_parser.py` handles `[U:1:X]` SteamID3 → `STEAM_0:Y:Z`
  correctly (`Y=X%2, Z=(X-Y)/2`), matches the formula independently used in
  the migration scripts. No fix needed, just confirmed.

- **`/ready` flow determinism** — the real fix for "how does the bot know
  if a played match is a tournament/mix/challenge": the match JSON has NO
  IDs at all, only `matchInfo.serverName`/`startTime`/team-name strings
  typed in-game. So determinism has to come from bridging /ready-time state
  to import-time, not from the JSON. Added `game_server_id` to
  `active_match_contexts` (was being discarded even though `/ready`'s
  `MapSelect` already knows the exact server it just ran RCON against —
  `ios_bot/commands/ready.py`). Server match now dominates the context
  scoring in `resolve_active_match_context` (+50 vs +7 for guild match,
  `ios_bot/db/matches.py`), since only one match can run on a given rented
  server at a time. Added `ios_servers.in_game_server_name` (distinct from
  the admin label — example showed "Georgia (Beta)" internally vs.
  `"[IOSCA] Atlanta GA"` in the actual match JSON) + `/set_server_ingame_name`
  admin command. **User still needs to run this once per server** with the
  real serverName string before this is fully live.

- **Team-name alias system consolidated**: replaced two hardcoded copies of
  an 11-item main-guild alias list in `match_importer.py` with real lookups
  against `public.team_name_aliases` (which existed but was never queried —
  dead table duplicating the hardcode). Expanded it with the same 33
  country names approved for the core migration (45 rows total). Removed
  three dead nickname functions from `db/teams.py`
  (`add_nickname_to_team`/`find_team_by_name_or_nickname`/
  `get_all_team_names_for_team`) — referenced a `nicknames` column that
  never existed on `iosca_teams`, would have errored if ever called, zero
  callers found. User confirmed: "we dont really use nicknames... feel free
  to remove that."

- **Caching made event-driven, not blind-TTL** (`ios_bot/db/cache.py`, new
  shared `QueryCache` class): invalidated by the write that actually
  changed something; TTL is a safety net only. Wired into:
  - `db/teams.py` — all 7 functions that write `IOSCA_TEAMS` now invalidate
    (`add_team`, `update_team_players/captain/channels/details/average_rating`,
    `delete_team`). Public `invalidate_cache()` added for external callers.
  - `db/servers.py` — `get_all_servers`/`get_all_servers_with_details`
    (hit on every single `/ready` call) now cached, invalidated on
    add/update/delete/activate/deactivate/set_ingame_server_name.
  - `db/players.py` — ratings leaderboard (`get_top_players_by_rating`)
    cached, invalidated by `register_player`/`update_player_rating`/
    `delete_player`, and by the batch `/recalculate_all` job (which writes
    ratings via its own SQL bypassing this class — hooked explicitly via
    `invalidate_ratings_cache()`/`teams.invalidate_cache()` calls added
    directly inside `_regenerate_player_ratings()`/`_recalculate_team_averages()`
    in `commands/recalculate_all.py`, so both the manual command and the
    daily scheduled task in `tasks.py` are covered — they share those
    functions).

- **DB pool config bug fixed** (`ios_bot/db/connection.py`): was hardcoding
  `min_size=5, max_size=20`, silently ignoring `.env`'s
  `DB_POOL_MIN_SIZE=3`/`DB_POOL_MAX_SIZE=10`/`DB_POOL_COMMAND_TIMEOUT`
  ("Recommended settings for Nano tier" — never actually applied). Now
  reads from env with the old hardcoded values as fallback defaults.

- **Player identity merge** — real gap found: "one Discord identity gains a
  second Steam ID" was already handled (`link_secondary_steam_id`, used in
  `register_me.py`), but "two already-separate player rows are actually the
  same person" had no mechanism at all. Built `merge_players()` in
  `db/players.py` + `/merge_player` admin command
  (`commands/stats_moderation.py`): folds Steam identities into
  `linked_steam_ids`, sums career stat totals, repoints team roster/captain
  entries referencing the old identity (via `teams_ops` param, so it goes
  through the normal cache-invalidating write path), deletes the duplicate
  row **by steam_id (real PK), never by discord_id** (a corrupt duplicate
  could theoretically share a discord_id with the row being kept).
  Deliberately does NOT try to recompute ratings inline — tells the admin
  to run `/recalculate_all` after, since averaging two already-computed
  ratings isn't how the rating formula works.

- **Team lifecycle**: added `is_active` to `public.iosca_teams`.
  `delete_team()` now soft-deletes (sets `is_active=FALSE`, clears the
  `players` roster to free those players for another team) instead of hard
  `DELETE`. This matters more than it sounds — `ios_bot/__init__.py`'s
  `on_guild_remove` handler calls `delete_team` automatically **any time the
  bot loses access to a Discord server for any reason**, including an
  accidental kick — that was permanently destroying team data before this
  fix. `add_team` now upserts (`ON CONFLICT (guild_id) DO UPDATE ... SET
  is_active=TRUE`) so a team can cleanly reactivate/re-register instead of
  hitting a primary-key error. Added `/reactivate_team` admin command
  (`commands/team_management.py`). Added an 85%-similarity name guard on
  both team-registration code paths (`commands/team_registration.py`) so a
  near-duplicate team name gets caught before creation, using the already-
  existing `find_best_team_match` fuzzy matcher.
  `is_player_in_team_type` automatically respects `is_active` now too, since
  it calls `get_all_teams_with_details()` which now filters inactive teams.

### Done (this round):

1. **Lineup persist debounced.** `signup_manager.py`'s
   `persist_lineup_snapshot()` was firing a DB round-trip (get_team lookup +
   upsert) on literally every `refresh_lineup()` call (every sign-up/unsign/
   sub click). Added an in-process `_last_persisted_snapshot` dict tracking
   the last-written (context_type, payload) signature per (guild_id,
   channel_id); the DB call is now skipped entirely when nothing changed.
   Restart-safe: worst case is one redundant write on the first refresh
   after a restart.

2. **Challenge persistence built.** Added `CHALLENGE_STATE` table + CRUD
   (`save_challenge_state`/`delete_challenge_state`/`load_all_challenge_states`)
   to `db/matches.py` (`MatchOperations`), same JSONB-blob-per-key shape as
   `TEAM_LINEUPS`. `challenge_manager.py`'s `active_challenges` is now a
   `_PersistentChallengeDict(defaultdict)` subclass whose `__setitem__`/
   `__delitem__` auto-fire a background persist/delete (via
   `asyncio.get_running_loop().create_task(...)`, tracked in a
   `_background_tasks` set so they can't be GC'd mid-flight) — this covers
   the ~14 `active_challenges[id] = {...}` / `del active_challenges[id]`
   call sites across `commands/challenge.py`/`unchallenge.py`/`ready.py`
   *without* needing to touch each one. Two in-place-mutation exceptions
   found by grepping for `active_challenges[x][y] =` and `_data["..."] =`
   patterns not followed by a reassignment/delete:
   - `ready.py` line ~549 (exception-handler rollback, mutates then neither
     reassigns nor deletes) — fixed with an explicit
     `await persist_challenge_state(active_challenge_id)` call.
   - `challenge.py`'s decline flow (~line 406) and `unchallenge.py`'s cancel
     flow (~lines 114/120) mutate status in place then `del
     active_challenges[...]` shortly after in the same function — the
     `__delitem__` hook cleans these up correctly, so the brief
     mutate-then-not-yet-persisted window only matters if the bot crashes in
     that exact sub-request instant. Judged low-risk enough to leave rather
     than add more explicit calls; revisit if it ever actually bites.
   - `ios_bot/__init__.py`'s `on_ready` **used to actively wipe
     `active_challenges.clear()`** on every restart (comment: "so teams can
     re-challenge") — replaced with `load_persisted_challenges()`. Note:
     this only restores the raw challenge dicts into memory, not a full
     re-render of associated Discord embeds/messages (unlike
     `restore_lineups_from_db()`, which does rehydrate live embeds) — a
     restored challenge is functionally live again (commands checking
     `active_challenges` will find it), but its Discord messages won't be
     refreshed/reposted automatically. Could be extended to match the
     lineup-restore polish level if that gap matters in practice.

### Still open:

1. **`executemany`** — user pointed out `conn.executemany(...)` is already
   used directly (via `self.pool.acquire()` + raw asyncpg connection) in a
   couple of hot spots: `db/matches.py` (`bulk_add_player_match_data`, the
   match-events bulk insert) and `tasks.py`. `DatabasePool` itself
   (`db/connection.py`) has no `executemany` wrapper method — callers reach
   into the raw pool. Worth using this existing pattern for any *future*
   bulk-write work rather than looping single-row awaits (the migration
   scripts in Part 1 did loop single-row awaits — fine there, one-off
   backfills, not hot live-bot paths). Nothing currently needs converting.

2. User wants continued focus on "caching and being professional with how
   we hit the DB" **generally** — the lineup-debounce and challenge-persist
   items above were the two concrete asks, both done now, but there may be
   other hot paths worth auditing (e.g. anything else that writes/reads on
   every button click rather than on real state changes). Haven't done a
   broader sweep beyond signup/challenge state yet.

3. **Hub egress is explicitly out of scope for now** — user said "besides
   the hub, we haven't even touched that yet," meaning: don't go work on
   hub-side egress next, this thread is about the bot's own DB usage.

4. **`/recalculate_all` egress** — answered as an informational question
   (not a task): full-history query (~25K+ rows) in one bulk read, but only
   triggered manually or once daily via `refresh_all_player_ratings_daily`
   in `tasks.py` — bounded by design already, will just grow linearly with
   match history over time. Not asked to change this.

## Part 3 — "what's next" discussion + Phase 0 safety cleanup (ONGOING)

User asked for a prioritization opinion on: Cogs/services rewrite vs.
core/hub schema cutover vs. DB connection lifecycle audit vs. general
efficiency vs. something more glaring. Recommendation given (user agreed,
said proceed):

1. **Close out Phase 0 safety items first** (small, fast, real risk sitting
   exposed since the start of this engagement) -- IN PROGRESS, see below.
2. **DB connection lifecycle audit next** -- NOT STARTED. Specific lead
   already identified: every `DatabasePool.execute/fetch/fetchrow/fetchval`
   call does its own independent `pool.acquire()`/release, so any code
   doing several sequential queries (loops, multi-step handlers) checks a
   connection in and out of the pool per-query instead of reusing one
   across the batch -- real contention risk with only 3-10 pooled
   connections (Nano tier) under concurrent Discord interactions. Worth
   auditing hot paths for this.
3. **Cogs/services rewrite** -- deliberately deferred. Good idea,
   pre-approved earlier in this engagement, but recommended NOT starting it
   yet: a ~30-file mechanical restructure on top of everything changed in
   Parts 1-2 (which hasn't been deployed/exercised by real traffic yet)
   risks tangling bugs from two unrelated changes together. Do this as its
   own clean pass once the current pile is stable/deployed.
4. **`core`/`hub` schema cutover** -- deliberately last. Highest-risk item:
   rewriting every `db/*.py` module's queries against a schema that's had
   zero real bot traffic, on top of an already-large pending changeset.
   Needs its own dedicated, unhurried pass once everything else settles.

### Phase 0 items fixed this round:

- **`BOT_ID` NameError fixed** (`ios_bot/config.py` `get_invite_link()`,
  used by `/help`): was falling back to an undefined name `BOT_ID` if
  `bot.user` was ever falsy -- guaranteed crash if that path was ever hit.
  Now falls back to `CLIENT_ID` from `.env` via the existing
  `_optional_int_env()` helper (confirmed against the user's actual `.env`
  contents -- `CLIENT_ID` is the Discord application's client ID, the
  correct value for an OAuth invite link).

- **Webhook secret now fails closed** (`ios_bot/webhook_server.py`, both
  `/webhook/match-insert` and `/webhook/sourcecord`): was falling back to
  the literal hardcoded string `'your-secret-key-here'` if `WEBHOOK_SECRET`
  was unset. **Confirmed via the user's actual `.env` contents that
  `WEBHOOK_SECRET` is not set there at all** (only `IOSCA_HUB_WEBHOOK_TOKEN`
  exists, a different, unrelated variable) -- so unless it's set separately
  on the production VPS (the repo's `.env` is never deployed there, per the
  original engagement brief), the webhook endpoint was very likely trusting
  a publicly-known string as its entire auth mechanism. Added
  `_get_webhook_secret()`: returns `None` (and logs an error once) if unset,
  and both call sites now explicitly reject when `expected_secret is None`
  rather than relying on `!=` comparison alone (which would have let a
  request with *no* `X-Webhook-Secret` header through if the secret was
  unset, since `None != None` is `False`).
  **IMPORTANT / needs user follow-up**: if `WEBHOOK_SECRET` truly isn't set
  on the VPS either, this change means the webhook endpoint will now
  reject ALL requests (fail closed) until the user sets a real
  `WEBHOOK_SECRET` value in the production environment. This is correct
  behavior, but could look like "webhook match announcements stopped
  working" if not communicated -- **flag this to the user prominently when
  reporting status, don't let it be a silent surprise.**

- **`register_player` race condition fixed** (`ios_bot/db/players.py`):
  was a classic check-then-act (`get_player_by_steam_id` check, then
  separate UPDATE/INSERT) with each step on its own independently-acquired
  connection -- two concurrent registrations for the same brand-new
  steam_id could both pass the "does it exist" check before either
  inserted. Since `steam_id` is `IOSCA_PLAYERS`'s primary key this couldn't
  actually corrupt data (the loser would just get a constraint-violation
  error), but it was a real, confusing failure mode under concurrency.
  Fixed by wrapping the whole function in one `pool.acquire()` +
  `conn.transaction()`, holding `pg_advisory_xact_lock(hashtext(steam_id))`
  for the duration -- serializes concurrent calls for the *same* steam_id
  without blocking calls for different ones. `get_player_by_steam_id`/
  `get_player_by_discord_id` both gained an optional `conn=` parameter so
  they can run on the already-acquired connection instead of checking out
  a separate one (backward compatible -- existing callers passing no `conn`
  are unaffected).

### Still open from Phase 0 (lower priority, not started):

- Plaintext game-server credentials hardcoded in
  `scripts/generate_seed_canonical_bootstrap.py` (~lines 39-49, per the
  original engagement brief). Judged lower priority: this is a one-off
  seed/migration script tied to the (now-superseded, see Part 1) canonical
  schema work, not a live bot code path that runs automatically.
- **Supabase Postgres password still needs rotating.** Flagged as the #1
  priority at the very start of this entire engagement (a connection string
  briefly leaked into a *prior* session's transcript via a Python
  traceback, before this session began) -- this is a dashboard action only
  the user can take (Supabase Project Settings -> Database -> Reset
  Database Password), Claude cannot do this. **No confirmation received
  that this has happened yet across this whole session. Keep asking /
  reminding until confirmed** -- this is the single most important
  outstanding item, unrelated to any of the code work above.

## Part 4 — DB connection lifecycle audit (DONE for the write-batching pass)

Built an AST-based scanner (`for`/`async for` loops containing an
`await pool.X(...)` or `await conn.X(...)` call not already wrapped in a
`.transaction()` block) and ran it across all of `db/*.py` and
`commands/*.py`. Found and fixed 8 real N+1 round-trip patterns (each was
previously acquiring/releasing a separate pool connection *per loop
iteration*):

**Write-side (batched via a single `conn.executemany()` inside one
transaction, replacing a `self.pool.execute()` per iteration):**
- `db/matches.py` `backfill_match_team_links` (fires on every `/register_team`
  via `backfill_matches_for_team` -- could be 100+ individual UPDATEs before,
  now one `executemany`). Also fixed a **third** hardcoded copy of the
  main-guild alias list found here while in the function -- replaced with a
  `TEAM_NAME_ALIASES` query, consistent with the Part 2 consolidation.
- `db/matches.py` `backfill_player_match_guild_ids` (up to 2 UPDATEs per
  match scanned -- admin repair command, `/reevaluate_all_games`).
- `db/matches.py` `upsert_player_event_timestamps_for_match` (one UPDATE per
  player per match -- same repair command, multiplied across every match it
  touches).
- `db/tournaments.py` `add_teams` (2 INSERTs per team being added to a
  tournament).
- `db/tournaments.py` the `TOURNAMENT_PLAYER_STATS` aggregation upsert loop
  (one INSERT..ON CONFLICT per distinct player in a tournament).
- `db/tournaments.py` `add_fixtures_from_text` (one INSERT per parsed
  fixture line when an admin pastes a schedule).

Where the original code counted affected rows via parsing `"UPDATE N"` from
each individual `execute()` result (not available from `executemany()`),
switched to either (a) counting from the in-memory params already computed
before the batch (exact, no query needed), or (b) a single before/after
`SELECT count(*)` around the batch (exact, one extra query instead of N).
Noted in comments at each site which approach was used and why.

**Read-side (batched via a single `... = ANY($1::array[])` query, replacing
a `fetchrow()` per iteration):**
- `commands/sync_sftp_matches.py` (`/sync_sftp_matches`) -- was doing one
  fetchrow per SFTP file just to check if it's already imported (up to
  `limit`, default 200).
- `commands/check_players.py` (`/check_players`) -- one fetchrow per signed
  player to look up their steam_id.
- `commands/reevaluate_all_games.py` -- one fetchrow per match JSON file
  (potentially the *entire* match history under a full scan) to resolve
  match_id -> internal MATCH_STATS.id before backfilling event locations.
  Batched the primary match_id-based lookup; the source_filename fallback
  (for whatever doesn't resolve that way) still runs per-file, since it's
  meant to be a rare minority.

### Found but deliberately NOT fixed (lower priority / higher risk to touch):

- `commands/recalculate_all.py`'s `_rebuild_match_performance` (~line 154):
  loops over every distinct match (~2,010 currently) doing a `fetch()` per
  match on an already-acquired single connection (`conn`, not `self.pool` --
  so it's *not* doing per-iteration pool acquire/release, just sequential
  round trips on one connection). Lower priority because: (a) it already
  avoids the worse pool-exhaustion version of this problem, (b) it's a
  daily-scheduled-or-manual job, not a per-interaction hot path, (c) it's
  complex ratings-critical logic -- didn't want to restructure it without
  more room to verify correctness carefully. Worth a dedicated look later
  if it ever becomes a real bottleneck.
- Did not do a genuinely exhaustive sweep of literally every `commands/*.py`
  file -- the AST scanner covers the `for`-loop-with-DB-call pattern
  specifically; there could be other inefficiencies (e.g. sequential-but-
  not-looped calls, N+1 patterns hidden behind helper function calls) not
  caught by this specific scan. `commands/populate_team_stats.py` and
  `commands/unchallenge.py` failed to parse with this particular scanner
  (BOM / encoding artifact at the start of the file) and were skipped --
  worth a manual look if continuing this audit.

### Known-but-not-yet-fixed side findings (not urgent, noted in case they come up):

- `generate_ratings.py`'s `generate_player_ratings()` has ~500 lines of
  unreachable dead code after an early `return` (real logic delegates to
  `role_based_ratings.py`). Not fixed, just noted.
- `/recalculate_all` (manual or the daily scheduled
  `refresh_all_player_ratings_daily` in `tasks.py`) does a full-history
  read of `player_match_data`/`match_stats` (~25K+ rows) in one query every
  time — inherent to "recalculate from scratch," not a bug, but will grow
  linearly with match history. Not urgent at current volume; if it becomes
  one, the fix is incremental rating updates instead of full recompute.

## Conventions established this session (follow these going forward)

- Always `python -c "import ast; ast.parse(...)"` every edited file before
  moving on — caught real bugs this way (the 54/53 column mismatch, the
  `if True:` leftover from a sloppy replace_all).
- Dry-run-first pattern for any data-writing script (`--dry-run` default,
  `--apply` to execute) — used throughout Part 1, worth keeping for any
  future bulk data work.
- Verify claims against the live DB before repeating them back — got burned
  once this session by trusting stale in-context assumptions (the "already
  registered teams" mistake in Part 1) and once by assuming no persistence
  existed for lineups when it actually does. Check first.
- `.env` conventions: `SUPABASE_DB_URL` is the pooler connection actually
  used by the bot; `DB_POOL_*` vars are meant to be respected (now are).

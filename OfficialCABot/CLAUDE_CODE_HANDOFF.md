# CA Community Bot — handoff brief for Claude Code

Paste this whole document as your first message to Claude Code in the repo at:
`C:\Users\narub\OneDrive - Personal\OneDrive\Documents\Projects & Other Works\Python\Projects\CA-Community-Bot\OfficialCABot`

This is a continuation of an engineering engagement that started in Claude (Cowork). Read this fully before touching anything — it captures decisions already made, work already done, and a security incident you need to act on first.

---

## 0. Do this first (in order)

1. **Rotate the Supabase Postgres password.** During diagnostics, a connection string including the plaintext password was briefly printed into a prior session's transcript via a Python traceback. Nothing else saw it, but treat it as burned — rotate it in the Supabase dashboard and update wherever it's referenced (local `.env`, and the VPS's separately-managed production environment — see §4) before doing anything else.
2. **Do not attempt a big-bang schema migration.** A prior migration attempt (via a different AI tool) against the canonical `core`/`hub` schema failed badly — lost functionality, data type errors, the Hub site wouldn't turn off. The plan below is deliberately incremental for that reason.
3. Confirm you can reach the Supabase Postgres instance directly from wherever you're running (this repo's `.env` has `SUPABASE_DB_URL`/`SUPABASE_POOLER_URL` — neither the user's local shell nor the prior cloud session could reach it over the network, so this may need testing fresh).

---

## 1. What this bot is

A Discord bot (pycord 2.7, not vanilla discord.py) for the IOSCA (IOSoccer Central America) community: matchmaking/signups, tournaments, player stats/ratings, server integration. The README claims "4,000+ users" — that's stale/misleading. **Design and reason about scale as ~100 active competitive players.** There's also a companion "Hub" website (React frontend on GitHub Pages + a FastAPI backend) showing public stats, backed by the same Supabase Postgres instance in a separate schema.

Stack, confirmed by reading code (not the README):
- Bot DB: Postgres on Supabase via `asyncpg`, real connection pool (`ios_bot/db/connection.py`). MySQL is fully gone from the bot's code path (a comment in `ios_bot/config.py` says so), though `aiomysql` is still a dead line in root `requirements.txt`.
- Hub DB: same Supabase project, separate schema (`iosca_hub_production` by default). `ioscahub.github.io/backend/migrations/*.sql` (mysql, numbered 001–005) is legacy/abandoned — its own file header says Postgres is the live source. `ioscahub.github.io/backend/postgres_migrations/001_hub_postgres_schema.sql` is the real target for that layer.
- Live/authoritative bot schema today = `migrations/SCHEMA.json` (flat `public` schema, ~29 tables, legacy shouty-case names like `IOSCA_TEAMS`/`MATCH_STATS` referenced throughout the query code — harmless under Postgres case-folding, just inconsistent style).
- Deployment: `.github/workflows/bot-deploy.yml` runs an `ast.parse` syntax check only (no real tests) then SFTPs straight to production on every push to `main` — no staging gate. `scripts/deploy_bot_sftp.py`'s `INCLUDE_PATHS` does **not** include `.env` — production's actual environment variables are managed separately on the VPS, not from this repo. There's also a second, manual, Windows-only PowerShell deploy path (`scripts/stage_provider_deploy.ps1`) staging into `.deploy_stage/` for a different host — that directory is a stale mirror, not a second source of truth, and can be confusing to read from since it looks like a second migration history.
- `.git` at the mounted folder root appeared empty/non-functional from the prior session's remote-devices bridge (looked like a OneDrive sync quirk). Verify git works normally for you locally before relying on `git diff`/`git status` history from that prior session — the 3 edits below were verified by direct string-match + `ast.parse`, not `git diff`, because git wasn't usable from that bridge.

Full architecture audit (published, still current): **https://claude.ai/code/artifact/a212aeaa-4be9-4364-a59f-c7564b0b6cdd**

---

## 2. Already done — do not redo these

Three code edits were made directly in this repo (verified with `ast.parse`, matching the syntax check the CI runs) as the first "egress reduction" pass, per the user's priority call:

1. **`ios_bot/db/teams.py`** — `get_all_teams()` and `get_all_teams_with_details()` now use a 30-second in-memory TTL cache instead of re-querying the full `IOSCA_TEAMS` table on every fuzzy-match/lookup call. Time-bounded only (not invalidated on write — that table has 16+ write methods, hooking all of them was judged higher risk than a bounded 30s staleness window).
2. **`ioscahub.github.io/backend/app/main.py`** — `_run_live_sync_poller` now skips its Postgres query entirely when `app.state.live_sync_broker.connections` is empty. Previously it queried unconditionally every `HUB_LIVE_SYNC_POLL_SECONDS` (default 5s) forever, even with zero `/ws/live` clients connected — this was the steadiest egress cost found in the whole system.
3. **`ios_bot/tasks.py`** — `hub_force_full_sync_seconds` default bumped from `43200` (12h) to `86400` (24h). This full-table resync exists as a safety net mainly to catch deletions the incremental (`updated_at`-driven) sync can't see — doubling the interval doubles the worst-case staleness window for deletions specifically. Overridable via the `HUB_FORCE_FULL_SYNC_SECONDS` env var.

Still open from the egress pass (not yet done):
- HTTP `Cache-Control`/`ETag` headers on the Hub API's read-only GET endpoints. Needs a careful pass first to confirm no endpoint varies by session/cookie before applying a blanket policy.
- Decision on `analytics/duckdb/` — a local snapshot-cache layer (`manifest.py`, `refresh_cache.py`, `schema.sql`) that was already built, explicitly to stop the Hub hitting Supabase directly, and has **never been scheduled to run**. Wire it up or retire it deliberately; don't leave it half-built.

Also confirmed and safe to clean up whenever convenient: `runtime/hub_site_disabled.flag` is dead. `toggle_hub_site.py` (the script that would have written/read it) no longer exists anywhere in the repo — only a stale `.pyc` remains in `__pycache__`. Nothing reads the flag; the Hub runs in production regardless of its presence. User confirmed this matches reality (hub is up).

---

## 3. The canonical schema decision — the big open piece

The user was told by a different AI tool ("Codex") that a from-scratch `core`/`hub` schema redesign was "the best," attempted a migration onto it, and it went badly (see §0.2). The full proposed schema is saved in this repo at **`docs/canonical_schema_v2_proposed_2026-08-03.sql`** (marked PROPOSED, NOT YET APPLIED in its own header) — read it before doing any schema work.

**Assessment: the design is good.** Account-first identity (`core.accounts` + `core.account_discord_identities` + `core.account_steam_identities`, replacing steam-id-first loose linking), normalized team channels instead of JSON arrays, relational match facts (`core.matches`/`core.match_player_entries`/`core.match_events`) instead of flat stat blobs, ratings versioned by run, and `hub.*` as pure derived read-model snapshot tables (a genuinely good fit for the egress goal — the Hub API can read one flat summary row instead of joining `core` tables live). The problem was never the schema — it's that migrating onto it was attempted as one big-bang cutover instead of an incremental, verified rollout.

**Concrete landmines identified (likely root causes of the prior failure):**
- **ID type strictness.** Legacy code has inconsistent Discord/Steam ID typing — `ios_bot/db/matches.py` has runtime flags like `_player_match_id_is_text`/`_match_stats_guild_id_is_text` because legacy rows mix TEXT and numeric ID storage. The new schema is strict `BIGINT` everywhere. Any legacy row with a non-clean-integer value (leading zeros, junk, empty string) will fail a naive migration.
- **Team membership constraint is wrong as written, and the user gave the real rule.** `core.team_memberships` has `uq_team_membership_single_active_non_captain`, a partial unique index treating ALL non-captain memberships as mutually exclusive bot-wide. **Actual business rule confirmed by the user:** a player can hold one active "official" (maps to the schema's `club` team_type) membership AND one active `national` membership simultaneously, plus unlimited `mix` team memberships, and can be captain of any number of teams regardless of type. This constraint needs to change to per-team-type exclusivity: add a denormalized `team_type` snapshot column to `core.team_memberships` (synced via trigger from `core.teams.team_type`), then two separate partial unique indexes — one scoped to `team_type = 'club'`, one scoped to `team_type = 'national'` — both `WHERE left_at IS NULL AND is_active AND membership_role <> 'captain'`, leaving `mix` (and, unless told otherwise, `allstar`/`community`) unrestricted. **Not yet confirmed with the user:** whether `vice_captain` should be exempted from exclusivity the same way `captain` is (the schema currently only exempts `captain`), and how `allstar`/`community` team types should behave (assumed unrestricted like `mix` — confirm before finalizing).
- **Missing table:** there is no equivalent of the legacy `TRANSFER_REQUESTS` table anywhere in the canonical schema, despite the user wanting an easy player-transfer flow. Add a `core.transfer_requests` table (proposed → accepted/declined → applied lifecycle) pointing at `account_id`/`team_id`.
- **Team channels flattening risk.** Legacy stores `eights_channels`/`sixes_channels`/`fives_channels` as JSONB arrays per team; canonical normalizes to `core.team_channels` rows with a **globally unique** `discord_channel_id`. If any channel ID is ever reused across two teams in the real data, a straight migration fails on that constraint — needs checking (see §4, unanswered diagnostic).
- **Identity resolution must happen before match data migrates.** `core.match_player_entries.account_id` is nullable, but legacy match rows keyed by steam_id need to resolve to `core.accounts` via `core.account_steam_identities` first. This is exactly what `scripts/recover_iosca_players_from_match_data.py` was reaching for, but it's incomplete.
- **Tournament remodel is the single biggest, riskiest piece.** Legacy flat `tournament_fixtures`/`tournament_data` → canonical `tournaments`/`tournament_phases`/`tournament_entries`/`tournament_fixtures`/`tournament_standings_current`. This is a full rewrite of the largest, most tangled subsystem in the bot (`ios_bot/commands/tournaments.py` is 3,651 lines; `ios_bot/db/tournaments.py` is 2,634 lines, and it already runs ~17 ad hoc `ALTER TABLE`/`CREATE INDEX` statements from application code at runtime — treat that file as needing a real rewrite, not a patch). Do this subsystem last, after accounts/teams/matches are proven.
- **"Hub wouldn't turn off" during the prior attempt** is very likely explained by §2's dead-code finding — `toggle_hub_site.py` no longer exists, so whatever the prior tool was trying to flip was probably already broken independent of the schema work.

**Other issues worth fixing regardless of adoption:**
- `core.game_server_credentials.password_plaintext` stores real server passwords in plaintext. `pgcrypto` is already loaded as an extension in this same schema file — wrap this column with `pgp_sym_encrypt`/`pgp_sym_decrypt` instead of storing plaintext.
- Inconsistent soft-delete convention across tables (some use `is_active`, others don't; no universal `deleted_at`).

**Recommended process (agreed direction, not yet executed):**
Do NOT big-bang migrate. Every table in the canonical schema file is `CREATE TABLE IF NOT EXISTS core.*`/`hub.*` — purely additive, doesn't touch the legacy `public` schema tables at all, so it can be created safely alongside what's live. Sequence:
1. Verify assumptions against real production data first (see §4 — this was attempted and blocked, needs to happen before writing real migration logic).
2. Write a **real** identity-resolution/backfill script — `scripts/migrate_public_to_canonical.py` today is a no-op stub (prints `[pending] <step>` for each phase, docstring literally says "implement each step deliberately before using this against production data"), and `scripts/sync_canonical_schema.py` is broken (reads a `schema.txt` at repo root that doesn't exist).
3. Migrate one subsystem at a time, in this order: **accounts/identities → teams → matches → tournaments (last, riskiest)**. Diff-validate migrated data against the legacy source before any read path cuts over to the new tables.
4. Only retire legacy `public.*` tables once each subsystem is fully verified running in production on the new tables.

---

## 4. Diagnostics still needed — blocked, needs your help to unblock

To verify the landmines above against real data (not guesses), the prior session tried to connect directly to Supabase Postgres and could not, from either the user's local machine (no general network egress from that shell at all) or the prior cloud session (network egress allowlist didn't reach Supabase's Postgres ports — TCP connects to the pooler host timed out on both 5432 and 6543). **This is the incident referenced in §0.1** — a connection string briefly leaked into that session's own transcript via a Python traceback during this failed attempt.

If you have working network access to Supabase from wherever you're running, connect directly and adapt the queries below. Otherwise, ask the user to paste these into Supabase's own SQL editor (100% read-only) and report back the results:

```sql
-- 1. What tables actually exist, and roughly how big they are
SELECT schemaname, relname AS table_name, n_live_tup AS approx_rows
FROM pg_stat_user_tables
WHERE schemaname IN ('public', 'core', 'hub')
ORDER BY schemaname, relname;

-- 2. The shape of the teams table (columns) — needed before writing
--    the roster-overlap and channel-uniqueness checks precisely
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'iosca_teams'
ORDER BY ordinal_position;
```

Once you have those two results, write and run the follow-up diagnostics these were meant to unblock:
- **Roster overlap**: parse the `players` JSONB column on `iosca_teams` and count how many discord IDs currently appear on more than one team's roster simultaneously (informs how much cleanup the new team-type-scoped constraint from §3 will require before it can be applied).
- **ID type consistency**: sample discord_id/steam_id-bearing columns across `iosca_teams`, `iosca_players`, `match_stats`/`player_match_data` for non-integer-parseable values (leading zeros, junk, empty strings) — this quantifies how bad the `BIGINT` migration problem in §3 actually is.
- **Channel ID uniqueness**: check whether any Discord channel ID appears in more than one team's channel arrays — this would break the canonical schema's global-unique constraint on `team_channels.discord_channel_id`.
- **`TRANSFER_REQUESTS` existence/volume**: confirm the legacy table name and row count so the new `core.transfer_requests` table (see §3) can be sized/designed to actually hold that history.

Also still unresolved: **whether there's a current Supabase backup/point-in-time-recovery snapshot** before any real writes happen into `core`/`hub`. The user didn't directly answer this — they confirmed instead that building additively in `core`/`hub` schemas (not touching `public`) is their preferred approach, which is already the plan. Since nothing has been written to `core`/`hub` yet, there's nothing to roll back yet — but **get an explicit backup confirmation before the first real INSERT into those schemas.**

---

## 5. Command architecture — approved, ready to start

Today, every file in `ios_bot/commands/` (~30 files) decorates a plain function against a shared module-level `bot` object at import time — there's no `discord.Cog` anywhere. Registration is a side effect of import, and the list of what gets imported lives in one hand-maintained file, `ios_bot/commands/__init__.py` (34 lines, one import per command) — miss adding a line there and a new command silently doesn't exist, no error. `ios_bot/__init__.py` already has a `load_extensions()`/Cog-style loader mechanism sitting unused (`_extensions = []` is empty) — converting to real Cogs finishes something already half-built, it doesn't invent a new pattern.

**User has approved doing this as one pass, all ~30 files now** (not gradually alongside each schema subsystem) — it's judged pure structural risk, independent of any data/schema decision. Go ahead and start this whenever you're ready; it doesn't need to wait on §3/§4.

Concretely: convert each `ios_bot/commands/*.py` file to a `discord.Cog` subclass with a `setup(bot)` entrypoint (pycord 2.7 syntax), replace the empty `_extensions` list in `ios_bot/__init__.py` with a real directory-scan autoloader (`bot.load_extension` per discovered cog module), and centralize the repeated boilerplate found across command files while you're in there: `has_permissions(...)` checks are repeated bare ~44 times instead of centralized, and `discord.Embed(...)` success/error construction is repeated ad hoc ~37 times instead of using a shared builder. `ios_bot/commands/tournaments.py` (3,651 lines) is the single biggest file and the strongest split candidate once it's in Cog form — consider breaking it into something like `tournament_admin.py` / `tournament_scheduling.py` / `tournament_views.py` along the seams that fall out of the conversion.

---

## 6. Full roadmap (for reference — phases 0–1 partially done, 2 approved to start, 3–4 pending §4)

- **Phase 0 — safety pass**: rotate the Supabase password (§0.1, urgent), confirm/fix the webhook secret fallback (`ios_bot/webhook_server.py:82,170` falls back to the literal string `'your-secret-key-here'` if `WEBHOOK_SECRET` is unset — check whether it's actually set on the **production VPS** directly, since the repo's local `.env` is never deployed there and isn't representative), same check for the Hub's `HUB_SESSION_SECRET` fallback (`ioscahub.github.io/backend/app/config.py:76`, falls back to `WEBHOOK_SECRET` first, then a hardcoded dev string), rotate/relocate the plaintext game-server credentials hardcoded in `scripts/generate_seed_canonical_bootstrap.py` (~lines 39–49), fix the undefined `BOT_ID` reference in `ios_bot/config.py:80` (latent `NameError`), add a transaction around `PlayerOperations.register_player` (`ios_bot/db/players.py:330-406` — currently check-then-act with no lock).
- **Phase 1 — egress**: 3 of the identified fixes done (§2). Still open: HTTP cache headers on the Hub API, DuckDB snapshot cache decision.
- **Phase 2 — dynamic commands**: approved, ready to start (§5).
- **Phase 3 — schema consolidation & data migration**: the big one, blocked on §4's diagnostics and the two small open confirmations in §3 (vice_captain exemption, allstar/community handling).
- **Phase 4 — Hub modernization**: once schema is settled — simplify `sync.py`'s two-schema dance, land on one caching strategy instead of two half-built ones (app cache + dormant DuckDB layer), frontend visual pass.

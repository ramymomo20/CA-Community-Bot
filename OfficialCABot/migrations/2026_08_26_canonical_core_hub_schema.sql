-- IOSCA CANONICAL SOURCE SCHEMA
-- Date: 2026-08-03
--
-- This file replaces the old ad hoc schema dump and is intended to be the
-- new source-of-truth schema for the bot and hub.
--
-- Design goals:
-- 1. Canonical identity is account-first, not steam_id-first.
-- 2. Core operational data lives in schema `core`.
-- 3. Public hub data lives in schema `hub` as derived snapshots.
-- 4. Match facts are relational and analytics-friendly.
-- 5. Team channels and rosters are normalized, not stored in JSON arrays.
-- 6. Ratings are versioned by run and published separately from raw match facts.
-- 7. Game server connection secrets remain in the database because the current
--    operational model explicitly depends on them being centrally managed there.
--
-- Notes:
-- - Migration from the legacy schema is intentionally deferred.
-- - Historical migrations may remain in the repository, but this file is the
--   intended target architecture going forward.
-- - One person owns one account row, many Discord identities, many Steam
--   identities, and exactly one current public rating record.
--
-- STATUS: APPLIED 2026-08-26. This is the real, executed migration derived
-- from docs/canonical_schema_v2_proposed_2026-08-03.sql (kept there as the
-- original proposal). Before this ran, core/hub in Supabase held leftover
-- data from an earlier failed migration attempt via a different AI tool --
-- that data was dropped (DROP SCHEMA core/hub CASCADE) with the user's
-- explicit go-ahead, since public.* remains the real source of truth/backup.
--
-- Changes made vs. the original proposal, per user decisions on 2026-08-26:
-- 1. core.team_memberships gets a team_type_snapshot column (synced from
--    core.teams.team_type via trigger) so the single-active-membership
--    constraint is scoped per team type instead of bot-wide. A player may
--    hold one active club membership AND one active national membership
--    simultaneously, plus unlimited mix/allstar/community memberships.
--    captain AND vice_captain are exempt from exclusivity in both scopes.
-- 2. Added core.transfer_requests (no legacy data to backfill -- confirmed
--    no TRANSFER_REQUESTS-style table exists anywhere in public schema).
-- 3. core.game_server_credentials.password_plaintext replaced with
--    password_encrypted BYTEA, meant for pgp_sym_encrypt/pgp_sym_decrypt
--    with an application-held key (never stored in this table). Wiring the
--    bot's game-server credential read/write path to use this is separate,
--    not-yet-done follow-up work.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS hub;

-- ---------------------------------------------------------------------------
-- Shared helpers
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION core.touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- Global app configuration and Discord metadata
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.app_config (
    config_key CITEXT PRIMARY KEY,
    config_value JSONB NOT NULL,
    description TEXT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS core.discord_guilds (
    discord_guild_id BIGINT PRIMARY KEY,
    guild_name TEXT NOT NULL,
    guild_icon_url TEXT NULL,
    owner_discord_user_id BIGINT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS core.discord_assets (
    asset_id BIGSERIAL PRIMARY KEY,
    discord_guild_id BIGINT NULL REFERENCES core.discord_guilds(discord_guild_id) ON DELETE CASCADE,
    asset_type TEXT NOT NULL CHECK (
        asset_type IN (
            'role',
            'channel',
            'category',
            'message',
            'emoji',
            'webhook',
            'link',
            'other'
        )
    ),
    asset_key CITEXT NOT NULL,
    discord_object_id BIGINT NULL,
    external_url TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (discord_guild_id, asset_type, asset_key)
);

CREATE INDEX IF NOT EXISTS idx_discord_assets_type_key
    ON core.discord_assets(asset_type, asset_key);

CREATE TRIGGER trg_discord_guilds_touch
    BEFORE UPDATE ON core.discord_guilds
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

CREATE TRIGGER trg_discord_assets_touch
    BEFORE UPDATE ON core.discord_assets
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

-- ---------------------------------------------------------------------------
-- Accounts and identities
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.accounts (
    account_id BIGSERIAL PRIMARY KEY,
    hub_user_id BIGINT NULL UNIQUE,
    display_name TEXT NULL,
    avatar_url TEXT NULL,
    bio TEXT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS core.account_discord_identities (
    discord_identity_id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES core.accounts(account_id) ON DELETE CASCADE,
    discord_user_id BIGINT NOT NULL UNIQUE,
    username TEXT NULL,
    global_name TEXT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    is_verified BOOLEAN NOT NULL DEFAULT TRUE,
    verified_at TIMESTAMPTZ NULL,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_account_primary_discord_identity
    ON core.account_discord_identities(account_id)
    WHERE is_primary = TRUE;

CREATE INDEX IF NOT EXISTS idx_account_discord_identities_account
    ON core.account_discord_identities(account_id, is_primary DESC);

CREATE TABLE IF NOT EXISTS core.account_steam_identities (
    steam_identity_id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES core.accounts(account_id) ON DELETE CASCADE,
    steam_id_64 BIGINT NOT NULL UNIQUE,
    steam_id_legacy TEXT NOT NULL UNIQUE,
    steam_profile_name TEXT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_verified BOOLEAN NOT NULL DEFAULT TRUE,
    verified_at TIMESTAMPTZ NULL,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_account_primary_steam_identity
    ON core.account_steam_identities(account_id)
    WHERE is_primary = TRUE;

CREATE INDEX IF NOT EXISTS idx_account_steam_identities_account
    ON core.account_steam_identities(account_id, is_primary DESC);

CREATE TABLE IF NOT EXISTS core.account_aliases (
    alias_id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES core.accounts(account_id) ON DELETE CASCADE,
    alias_type TEXT NOT NULL CHECK (
        alias_type IN (
            'in_game_name',
            'discord_display_name',
            'steam_profile_name',
            'manual_alias'
        )
    ),
    alias_value TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'system',
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (account_id, alias_type, alias_value)
);

CREATE INDEX IF NOT EXISTS idx_account_aliases_value
    ON core.account_aliases(alias_type, alias_value);

CREATE TABLE IF NOT EXISTS core.registration_intents (
    intent_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash CHAR(64) NOT NULL UNIQUE,
    discord_user_id BIGINT NOT NULL,
    discord_name TEXT NULL,
    discord_guild_id BIGINT NULL REFERENCES core.discord_guilds(discord_guild_id) ON DELETE SET NULL,
    consumed_by_hub_user_id BIGINT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_registration_intents_discord
    ON core.registration_intents(discord_user_id, expires_at DESC);

CREATE TABLE IF NOT EXISTS core.account_link_audit (
    audit_id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NULL REFERENCES core.accounts(account_id) ON DELETE SET NULL,
    action_type TEXT NOT NULL CHECK (
        action_type IN (
            'register',
            'link_discord',
            'unlink_discord',
            'link_steam',
            'unlink_steam',
            'merge_account',
            'split_account',
            'manual_override'
        )
    ),
    actor_account_id BIGINT NULL REFERENCES core.accounts(account_id) ON DELETE SET NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER trg_accounts_touch
    BEFORE UPDATE ON core.accounts
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

CREATE TRIGGER trg_account_discord_identities_touch
    BEFORE UPDATE ON core.account_discord_identities
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

CREATE TRIGGER trg_account_steam_identities_touch
    BEFORE UPDATE ON core.account_steam_identities
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

-- ---------------------------------------------------------------------------
-- Teams and team memberships
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.teams (
    team_id BIGSERIAL PRIMARY KEY,
    discord_guild_id BIGINT NULL UNIQUE REFERENCES core.discord_guilds(discord_guild_id) ON DELETE SET NULL,
    team_type TEXT NOT NULL CHECK (
        team_type IN ('club', 'national', 'mix', 'allstar', 'community')
    ),
    name TEXT NOT NULL,
    short_name TEXT NULL,
    slug CITEXT NOT NULL UNIQUE,
    crest_url TEXT NULL,
    primary_color TEXT NULL,
    secondary_color TEXT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_teams_name
    ON core.teams(name);

CREATE INDEX IF NOT EXISTS idx_teams_type
    ON core.teams(team_type, is_active);

CREATE TABLE IF NOT EXISTS core.team_channels (
    team_channel_id BIGSERIAL PRIMARY KEY,
    team_id BIGINT NOT NULL REFERENCES core.teams(team_id) ON DELETE CASCADE,
    discord_channel_id BIGINT NOT NULL UNIQUE,
    channel_kind TEXT NOT NULL CHECK (
        channel_kind IN (
            'matchmaking_5v5',
            'matchmaking_6v6',
            'matchmaking_8v8',
            'press',
            'schedule',
            'media',
            'other'
        )
    ),
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (team_id, channel_kind, discord_channel_id)
);

CREATE INDEX IF NOT EXISTS idx_team_channels_lookup
    ON core.team_channels(team_id, channel_kind, is_active);

CREATE TABLE IF NOT EXISTS core.team_memberships (
    team_membership_id BIGSERIAL PRIMARY KEY,
    team_id BIGINT NOT NULL REFERENCES core.teams(team_id) ON DELETE CASCADE,
    account_id BIGINT NOT NULL REFERENCES core.accounts(account_id) ON DELETE CASCADE,
    membership_role TEXT NOT NULL CHECK (
        membership_role IN ('captain', 'vice_captain', 'player', 'manager', 'coach', 'reserve')
    ),
    team_type_snapshot TEXT NOT NULL CHECK (
        team_type_snapshot IN ('club', 'national', 'mix', 'allstar', 'community')
    ),
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    left_at TIMESTAMPTZ NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_team_membership_current
    ON core.team_memberships(team_id, account_id)
    WHERE left_at IS NULL;

-- One active club membership at a time (captain/vice_captain exempt).
CREATE UNIQUE INDEX IF NOT EXISTS uq_team_membership_club_exclusive
    ON core.team_memberships(account_id)
    WHERE left_at IS NULL
      AND is_active = TRUE
      AND team_type_snapshot = 'club'
      AND membership_role NOT IN ('captain', 'vice_captain');

-- One active national membership at a time (captain/vice_captain exempt).
-- Club and national exclusivity are independent, so a player can hold one
-- of each simultaneously. mix/allstar/community are never restricted.
CREATE UNIQUE INDEX IF NOT EXISTS uq_team_membership_national_exclusive
    ON core.team_memberships(account_id)
    WHERE left_at IS NULL
      AND is_active = TRUE
      AND team_type_snapshot = 'national'
      AND membership_role NOT IN ('captain', 'vice_captain');

CREATE INDEX IF NOT EXISTS idx_team_memberships_account
    ON core.team_memberships(account_id, is_active);

CREATE INDEX IF NOT EXISTS idx_team_memberships_role
    ON core.team_memberships(team_id, membership_role, is_active);

CREATE OR REPLACE FUNCTION core.sync_team_membership_type_snapshot()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    SELECT team_type INTO NEW.team_type_snapshot
    FROM core.teams
    WHERE team_id = NEW.team_id;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_team_memberships_sync_type
    BEFORE INSERT OR UPDATE OF team_id ON core.team_memberships
    FOR EACH ROW EXECUTE FUNCTION core.sync_team_membership_type_snapshot();

CREATE OR REPLACE FUNCTION core.cascade_team_type_to_memberships()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.team_type IS DISTINCT FROM OLD.team_type THEN
        UPDATE core.team_memberships
        SET team_type_snapshot = NEW.team_type
        WHERE team_id = NEW.team_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_teams_cascade_type_to_memberships
    AFTER UPDATE OF team_type ON core.teams
    FOR EACH ROW EXECUTE FUNCTION core.cascade_team_type_to_memberships();

CREATE TABLE IF NOT EXISTS core.transfer_requests (
    transfer_request_id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES core.accounts(account_id) ON DELETE CASCADE,
    from_team_id BIGINT NULL REFERENCES core.teams(team_id) ON DELETE SET NULL,
    to_team_id BIGINT NOT NULL REFERENCES core.teams(team_id) ON DELETE CASCADE,
    requested_by_account_id BIGINT NULL REFERENCES core.accounts(account_id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (
        status IN ('proposed', 'accepted', 'declined', 'cancelled', 'applied')
    ),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ NULL,
    applied_at TIMESTAMPTZ NULL,
    notes TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_transfer_requests_account
    ON core.transfer_requests(account_id, status);

CREATE INDEX IF NOT EXISTS idx_transfer_requests_to_team
    ON core.transfer_requests(to_team_id, status);

CREATE TRIGGER trg_teams_touch
    BEFORE UPDATE ON core.teams
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

CREATE TRIGGER trg_team_channels_touch
    BEFORE UPDATE ON core.team_channels
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

CREATE TABLE IF NOT EXISTS core.team_lineup_snapshots (
    team_lineup_snapshot_id BIGSERIAL PRIMARY KEY,
    team_id BIGINT NOT NULL REFERENCES core.teams(team_id) ON DELETE CASCADE,
    discord_channel_id BIGINT NOT NULL,
    context_type TEXT NOT NULL,
    lineup_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (team_id, discord_channel_id)
);

-- ---------------------------------------------------------------------------
-- Game server metadata and ingestion bookkeeping
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.game_servers (
    game_server_id BIGSERIAL PRIMARY KEY,
    server_key CITEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    region_code TEXT NULL,
    host TEXT NULL,
    game_port INTEGER NULL,
    query_port INTEGER NULL,
    rcon_port INTEGER NULL,
    game_format TEXT NULL CHECK (
        game_format IS NULL OR game_format IN ('5v5', '6v6', '8v8')
    ),
    provider_name TEXT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS core.game_server_credentials (
    game_server_credential_id BIGSERIAL PRIMARY KEY,
    game_server_id BIGINT NOT NULL REFERENCES core.game_servers(game_server_id) ON DELETE CASCADE,
    credential_kind TEXT NOT NULL CHECK (
        credential_kind IN ('sftp', 'ssh', 'rcon', 'query', 'api', 'other')
    ),
    credential_label CITEXT NOT NULL,
    host TEXT NULL,
    port INTEGER NULL,
    username TEXT NULL,
    password_encrypted BYTEA NULL, -- pgp_sym_encrypt'd; decrypt with pgp_sym_decrypt() using an app-held key, never stored here
    private_key_pem TEXT NULL,
    private_key_passphrase TEXT NULL,
    api_token TEXT NULL,
    extra_secret JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_validated_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (game_server_id, credential_kind, credential_label)
);

CREATE INDEX IF NOT EXISTS idx_game_server_credentials_server_kind
    ON core.game_server_credentials(game_server_id, credential_kind, is_active DESC);

CREATE TABLE IF NOT EXISTS core.match_import_runs (
    import_run_id BIGSERIAL PRIMARY KEY,
    game_server_id BIGINT NULL REFERENCES core.game_servers(game_server_id) ON DELETE SET NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL,
    status TEXT NOT NULL DEFAULT 'running' CHECK (
        status IN ('running', 'succeeded', 'failed', 'partial')
    ),
    files_seen INTEGER NOT NULL DEFAULT 0,
    files_imported INTEGER NOT NULL DEFAULT 0,
    matches_imported INTEGER NOT NULL DEFAULT 0,
    errors_count INTEGER NOT NULL DEFAULT 0,
    notes JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS core.match_sources (
    match_source_id BIGSERIAL PRIMARY KEY,
    import_run_id BIGINT NULL REFERENCES core.match_import_runs(import_run_id) ON DELETE SET NULL,
    game_server_id BIGINT NULL REFERENCES core.game_servers(game_server_id) ON DELETE SET NULL,
    source_key TEXT NOT NULL UNIQUE,
    payload_sha256 CHAR(64) NULL,
    storage_uri TEXT NULL,
    source_filename TEXT NULL,
    source_match_external_id TEXT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'imported', 'skipped', 'failed')
    ),
    skip_reason TEXT NULL,
    imported_match_id BIGINT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_match_sources_status
    ON core.match_sources(status, created_at DESC);

CREATE TRIGGER trg_game_servers_touch
    BEFORE UPDATE ON core.game_servers
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

CREATE TRIGGER trg_game_server_credentials_touch
    BEFORE UPDATE ON core.game_server_credentials
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

CREATE TRIGGER trg_match_sources_touch
    BEFORE UPDATE ON core.match_sources
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

-- ---------------------------------------------------------------------------
-- Match facts
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.matches (
    match_id BIGSERIAL PRIMARY KEY,
    external_match_id TEXT NOT NULL UNIQUE,
    game_server_id BIGINT NULL REFERENCES core.game_servers(game_server_id) ON DELETE SET NULL,
    played_at TIMESTAMPTZ NOT NULL,
    game_format TEXT NOT NULL CHECK (game_format IN ('5v5', '6v6', '8v8')),
    competition_kind TEXT NOT NULL CHECK (
        competition_kind IN (
            'matchmaking',
            'tournament',
            'friendly',
            'scrim',
            'showmatch',
            'other'
        )
    ),
    match_status TEXT NOT NULL DEFAULT 'final' CHECK (
        match_status IN ('final', 'void', 'excluded')
    ),
    home_team_id BIGINT NULL REFERENCES core.teams(team_id) ON DELETE SET NULL,
    away_team_id BIGINT NULL REFERENCES core.teams(team_id) ON DELETE SET NULL,
    home_team_name_snapshot TEXT NOT NULL,
    away_team_name_snapshot TEXT NOT NULL,
    home_score INTEGER NOT NULL DEFAULT 0,
    away_score INTEGER NOT NULL DEFAULT 0,
    went_extra_time BOOLEAN NOT NULL DEFAULT FALSE,
    went_penalties BOOLEAN NOT NULL DEFAULT FALSE,
    comeback_flag BOOLEAN NOT NULL DEFAULT FALSE,
    winning_team_id BIGINT NULL REFERENCES core.teams(team_id) ON DELETE SET NULL,
    source_filename TEXT NULL,
    source_updated_at TIMESTAMPTZ NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_matches_played_at
    ON core.matches(played_at DESC);

CREATE INDEX IF NOT EXISTS idx_matches_home_team
    ON core.matches(home_team_id, played_at DESC);

CREATE INDEX IF NOT EXISTS idx_matches_away_team
    ON core.matches(away_team_id, played_at DESC);

CREATE INDEX IF NOT EXISTS idx_matches_format_kind
    ON core.matches(game_format, competition_kind, played_at DESC);

CREATE TABLE IF NOT EXISTS core.match_origin_contexts (
    match_origin_context_id BIGSERIAL PRIMARY KEY,
    match_id BIGINT NULL REFERENCES core.matches(match_id) ON DELETE CASCADE,
    primary_channel_id BIGINT NOT NULL,
    secondary_channel_id BIGINT NULL,
    source_kind TEXT NOT NULL DEFAULT 'standard' CHECK (
        source_kind IN ('standard', 'challenge', 'scheduled', 'manual')
    ),
    home_team_id BIGINT NULL REFERENCES core.teams(team_id) ON DELETE SET NULL,
    away_team_id BIGINT NULL REFERENCES core.teams(team_id) ON DELETE SET NULL,
    home_team_name_norm TEXT NOT NULL,
    away_team_name_norm TEXT NOT NULL,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_match_origin_contexts_open
    ON core.match_origin_contexts(opened_at DESC)
    WHERE resolved_at IS NULL;

CREATE TABLE IF NOT EXISTS core.match_player_entries (
    match_player_entry_id BIGSERIAL PRIMARY KEY,
    source_player_match_key TEXT NULL UNIQUE,
    match_id BIGINT NOT NULL REFERENCES core.matches(match_id) ON DELETE CASCADE,
    account_id BIGINT NULL REFERENCES core.accounts(account_id) ON DELETE SET NULL,
    team_id BIGINT NULL REFERENCES core.teams(team_id) ON DELETE SET NULL,
    team_side TEXT NULL CHECK (team_side IN ('home', 'away')),
    steam_id_64_snapshot BIGINT NULL,
    steam_id_legacy_snapshot TEXT NULL,
    player_name_snapshot TEXT NULL,
    position_code TEXT NULL CHECK (
        position_code IS NULL OR position_code IN (
            'GK', 'LB', 'CB', 'RB', 'CM', 'LM', 'RM', 'LW', 'RW', 'CF'
        )
    ),
    participation_status TEXT NOT NULL DEFAULT 'started' CHECK (
        participation_status IN ('started', 'substitute', 'bench', 'unknown')
    ),
    started_on_field BOOLEAN NOT NULL DEFAULT FALSE,
    goals INTEGER NOT NULL DEFAULT 0,
    assists INTEGER NOT NULL DEFAULT 0,
    second_assists INTEGER NOT NULL DEFAULT 0,
    shots INTEGER NOT NULL DEFAULT 0,
    shots_on_target INTEGER NOT NULL DEFAULT 0,
    passes_completed INTEGER NOT NULL DEFAULT 0,
    passes_attempted INTEGER NOT NULL DEFAULT 0,
    chances_created INTEGER NOT NULL DEFAULT 0,
    key_passes INTEGER NOT NULL DEFAULT 0,
    interceptions INTEGER NOT NULL DEFAULT 0,
    tackles INTEGER NOT NULL DEFAULT 0,
    tackles_completed INTEGER NOT NULL DEFAULT 0,
    fouls_committed INTEGER NOT NULL DEFAULT 0,
    fouls_suffered INTEGER NOT NULL DEFAULT 0,
    yellow_cards INTEGER NOT NULL DEFAULT 0,
    second_yellow_reds INTEGER NOT NULL DEFAULT 0,
    red_cards INTEGER NOT NULL DEFAULT 0,
    saves INTEGER NOT NULL DEFAULT 0,
    saves_caught INTEGER NOT NULL DEFAULT 0,
    goals_conceded INTEGER NOT NULL DEFAULT 0,
    offsides INTEGER NOT NULL DEFAULT 0,
    own_goals INTEGER NOT NULL DEFAULT 0,
    corners INTEGER NOT NULL DEFAULT 0,
    throw_ins INTEGER NOT NULL DEFAULT 0,
    free_kicks INTEGER NOT NULL DEFAULT 0,
    goal_kicks INTEGER NOT NULL DEFAULT 0,
    penalties_taken INTEGER NOT NULL DEFAULT 0,
    possession_pct NUMERIC(6,2) NOT NULL DEFAULT 0,
    seconds_played INTEGER NOT NULL DEFAULT 0,
    seconds_gk INTEGER NOT NULL DEFAULT 0,
    seconds_def INTEGER NOT NULL DEFAULT 0,
    seconds_mid INTEGER NOT NULL DEFAULT 0,
    seconds_atk INTEGER NOT NULL DEFAULT 0,
    distance_meters NUMERIC(12,2) NOT NULL DEFAULT 0,
    pass_accuracy_pct NUMERIC(6,2) NOT NULL DEFAULT 0,
    event_timestamps JSONB NOT NULL DEFAULT '{}'::jsonb,
    clutch_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    substitution_impact JSONB NOT NULL DEFAULT '{}'::jsonb,
    match_rating NUMERIC(5,2) NULL,
    is_match_mvp BOOLEAN NOT NULL DEFAULT FALSE,
    mvp_score NUMERIC(6,2) NULL,
    mvp_key_stats JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_match_player_entries_match
    ON core.match_player_entries(match_id, team_side, match_rating DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_match_player_entries_account
    ON core.match_player_entries(account_id, match_id DESC);

CREATE INDEX IF NOT EXISTS idx_match_player_entries_team
    ON core.match_player_entries(team_id, match_id DESC);

CREATE INDEX IF NOT EXISTS idx_match_player_entries_steam_snapshot
    ON core.match_player_entries(steam_id_legacy_snapshot);

CREATE TABLE IF NOT EXISTS core.match_player_position_segments (
    position_segment_id BIGSERIAL PRIMARY KEY,
    match_player_entry_id BIGINT NOT NULL REFERENCES core.match_player_entries(match_player_entry_id) ON DELETE CASCADE,
    team_side TEXT NOT NULL CHECK (team_side IN ('home', 'away')),
    position_code TEXT NOT NULL CHECK (
        position_code IN ('GK', 'LB', 'CB', 'RB', 'CM', 'LM', 'RM', 'LW', 'RW', 'CF')
    ),
    start_second INTEGER NOT NULL DEFAULT 0,
    end_second INTEGER NOT NULL DEFAULT 0,
    duration_seconds INTEGER GENERATED ALWAYS AS (GREATEST(end_second - start_second, 0)) STORED,
    slot_order INTEGER NOT NULL DEFAULT 0,
    started_segment BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_match_player_segments_entry
    ON core.match_player_position_segments(match_player_entry_id, start_second);

CREATE TABLE IF NOT EXISTS core.match_events (
    match_event_id BIGSERIAL PRIMARY KEY,
    source_event_key TEXT NOT NULL UNIQUE,
    match_id BIGINT NOT NULL REFERENCES core.matches(match_id) ON DELETE CASCADE,
    event_index INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'goal',
            'assist',
            'save',
            'miss',
            'yellow',
            'second_yellow',
            'red',
            'own_goal',
            'sub_on',
            'sub_off',
            'other'
        )
    ),
    raw_event_type TEXT NULL,
    team_side TEXT NULL CHECK (team_side IN ('home', 'away')),
    team_id BIGINT NULL REFERENCES core.teams(team_id) ON DELETE SET NULL,
    period_label TEXT NULL,
    raw_second INTEGER NULL,
    match_second INTEGER NULL,
    minute_mark INTEGER NULL,
    clock_label TEXT NULL,
    actor_account_id BIGINT NULL REFERENCES core.accounts(account_id) ON DELETE SET NULL,
    secondary_account_id BIGINT NULL REFERENCES core.accounts(account_id) ON DELETE SET NULL,
    tertiary_account_id BIGINT NULL REFERENCES core.accounts(account_id) ON DELETE SET NULL,
    actor_steam_id_legacy_snapshot TEXT NULL,
    secondary_steam_id_legacy_snapshot TEXT NULL,
    tertiary_steam_id_legacy_snapshot TEXT NULL,
    body_part INTEGER NULL,
    x_raw DOUBLE PRECISION NULL,
    y_raw DOUBLE PRECISION NULL,
    x_norm DOUBLE PRECISION NULL,
    y_norm DOUBLE PRECISION NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (match_id, event_index)
);

CREATE INDEX IF NOT EXISTS idx_match_events_match_type
    ON core.match_events(match_id, event_type, event_index);

CREATE INDEX IF NOT EXISTS idx_match_events_actor
    ON core.match_events(actor_account_id, event_type);

CREATE INDEX IF NOT EXISTS idx_match_events_team
    ON core.match_events(team_id, event_type);

CREATE TABLE IF NOT EXISTS core.match_exclusions (
    match_id BIGINT PRIMARY KEY REFERENCES core.matches(match_id) ON DELETE CASCADE,
    excluded_by_account_id BIGINT NULL REFERENCES core.accounts(account_id) ON DELETE SET NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS core.match_player_exclusions (
    match_player_entry_id BIGINT PRIMARY KEY REFERENCES core.match_player_entries(match_player_entry_id) ON DELETE CASCADE,
    excluded_by_account_id BIGINT NULL REFERENCES core.accounts(account_id) ON DELETE SET NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER trg_matches_touch
    BEFORE UPDATE ON core.matches
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

CREATE TRIGGER trg_match_player_entries_touch
    BEFORE UPDATE ON core.match_player_entries
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

-- ---------------------------------------------------------------------------
-- Challenges and live scheduling runtime
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.challenge_requests (
    challenge_request_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    issued_by_account_id BIGINT NULL REFERENCES core.accounts(account_id) ON DELETE SET NULL,
    initiating_team_id BIGINT NOT NULL REFERENCES core.teams(team_id) ON DELETE CASCADE,
    opponent_team_id BIGINT NULL REFERENCES core.teams(team_id) ON DELETE SET NULL,
    game_format TEXT NOT NULL CHECK (game_format IN ('5v5', '6v6', '8v8')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'accepted', 'declined', 'expired', 'cancelled', 'completed')
    ),
    initiating_channel_id BIGINT NULL,
    opponent_channel_id BIGINT NULL,
    proposed_server_id BIGINT NULL REFERENCES core.game_servers(game_server_id) ON DELETE SET NULL,
    accepted_match_id BIGINT NULL REFERENCES core.matches(match_id) ON DELETE SET NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NULL,
    accepted_at TIMESTAMPTZ NULL,
    resolved_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_challenge_requests_open
    ON core.challenge_requests(status, issued_at DESC);

-- ---------------------------------------------------------------------------
-- Ratings
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.rating_runs (
    rating_run_id BIGSERIAL PRIMARY KEY,
    formula_version TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'scheduled',
    status TEXT NOT NULL DEFAULT 'running' CHECK (
        status IN ('running', 'succeeded', 'failed', 'partial')
    ),
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL,
    notes TEXT NULL
);

CREATE TABLE IF NOT EXISTS core.player_ratings_current (
    account_id BIGINT PRIMARY KEY REFERENCES core.accounts(account_id) ON DELETE CASCADE,
    rating_run_id BIGINT NOT NULL REFERENCES core.rating_runs(rating_run_id) ON DELETE RESTRICT,
    main_role TEXT NULL CHECK (main_role IS NULL OR main_role IN ('ATK', 'MID', 'DEF', 'GK')),
    rating NUMERIC(5,2) NULL,
    atk_rating NUMERIC(5,2) NULL,
    mid_rating NUMERIC(5,2) NULL,
    def_rating NUMERIC(5,2) NULL,
    gk_rating NUMERIC(5,2) NULL,
    main_role_rating NUMERIC(5,2) NULL,
    display_main_role_rating NUMERIC(5,2) NULL,
    total_appearances INTEGER NOT NULL DEFAULT 0,
    total_minutes INTEGER NOT NULL DEFAULT 0,
    atk_appearances INTEGER NOT NULL DEFAULT 0,
    mid_appearances INTEGER NOT NULL DEFAULT 0,
    def_appearances INTEGER NOT NULL DEFAULT 0,
    gk_appearances INTEGER NOT NULL DEFAULT 0,
    atk_minutes INTEGER NOT NULL DEFAULT 0,
    mid_minutes INTEGER NOT NULL DEFAULT 0,
    def_minutes INTEGER NOT NULL DEFAULT 0,
    gk_minutes INTEGER NOT NULL DEFAULT 0,
    inactivity_penalty NUMERIC(6,2) NOT NULL DEFAULT 0,
    last_match_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_player_ratings_current_display
    ON core.player_ratings_current(display_main_role_rating DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_player_ratings_current_role
    ON core.player_ratings_current(main_role, display_main_role_rating DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS core.player_rating_snapshots (
    player_rating_snapshot_id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES core.accounts(account_id) ON DELETE CASCADE,
    rating_run_id BIGINT NULL REFERENCES core.rating_runs(rating_run_id) ON DELETE SET NULL,
    snapshot_kind TEXT NOT NULL CHECK (
        snapshot_kind IN ('initial', 'daily', 'weekly', 'season_end', 'milestone', 'manual')
    ),
    snapshot_date DATE NOT NULL,
    main_role TEXT NULL CHECK (main_role IS NULL OR main_role IN ('ATK', 'MID', 'DEF', 'GK')),
    rating NUMERIC(5,2) NULL,
    atk_rating NUMERIC(5,2) NULL,
    mid_rating NUMERIC(5,2) NULL,
    def_rating NUMERIC(5,2) NULL,
    gk_rating NUMERIC(5,2) NULL,
    main_role_rating NUMERIC(5,2) NULL,
    display_main_role_rating NUMERIC(5,2) NULL,
    display_rating_delta NUMERIC(5,2) NULL,
    total_appearances INTEGER NOT NULL DEFAULT 0,
    total_minutes INTEGER NOT NULL DEFAULT 0,
    last_match_at TIMESTAMPTZ NULL,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (account_id, snapshot_kind, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_player_rating_snapshots_account_time
    ON core.player_rating_snapshots(account_id, snapshot_date DESC, captured_at DESC);

CREATE TABLE IF NOT EXISTS core.team_ratings_current (
    team_id BIGINT PRIMARY KEY REFERENCES core.teams(team_id) ON DELETE CASCADE,
    rating_run_id BIGINT NOT NULL REFERENCES core.rating_runs(rating_run_id) ON DELETE RESTRICT,
    average_rating NUMERIC(5,2) NULL,
    weighted_average_rating NUMERIC(5,2) NULL,
    active_player_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_team_ratings_current_average
    ON core.team_ratings_current(average_rating DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS core.team_rating_snapshots (
    team_rating_snapshot_id BIGSERIAL PRIMARY KEY,
    team_id BIGINT NOT NULL REFERENCES core.teams(team_id) ON DELETE CASCADE,
    rating_run_id BIGINT NULL REFERENCES core.rating_runs(rating_run_id) ON DELETE SET NULL,
    snapshot_kind TEXT NOT NULL CHECK (
        snapshot_kind IN ('daily', 'weekly', 'season_end', 'milestone', 'manual')
    ),
    snapshot_date DATE NOT NULL,
    average_rating NUMERIC(5,2) NULL,
    weighted_average_rating NUMERIC(5,2) NULL,
    average_rating_delta NUMERIC(5,2) NULL,
    active_player_count INTEGER NOT NULL DEFAULT 0,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (team_id, snapshot_kind, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_team_rating_snapshots_team_time
    ON core.team_rating_snapshots(team_id, snapshot_date DESC, captured_at DESC);

CREATE TRIGGER trg_player_ratings_current_touch
    BEFORE UPDATE ON core.player_ratings_current
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

CREATE TRIGGER trg_team_ratings_current_touch
    BEFORE UPDATE ON core.team_ratings_current
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

-- ---------------------------------------------------------------------------
-- Tournaments
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.tournaments (
    tournament_id BIGSERIAL PRIMARY KEY,
    slug CITEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    format_kind TEXT NOT NULL CHECK (
        format_kind IN ('league', 'cup', 'hybrid', 'playoff')
    ),
    game_format TEXT NOT NULL CHECK (game_format IN ('5v5', '6v6', '8v8')),
    status TEXT NOT NULL CHECK (
        status IN ('draft', 'scheduled', 'active', 'completed', 'archived')
    ),
    league_count INTEGER NOT NULL DEFAULT 1,
    points_win INTEGER NOT NULL DEFAULT 3,
    points_draw INTEGER NOT NULL DEFAULT 1,
    points_loss INTEGER NOT NULL DEFAULT 0,
    starts_at TIMESTAMPTZ NULL,
    ends_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS core.tournament_phases (
    tournament_phase_id BIGSERIAL PRIMARY KEY,
    tournament_id BIGINT NOT NULL REFERENCES core.tournaments(tournament_id) ON DELETE CASCADE,
    phase_key TEXT NOT NULL,
    phase_name TEXT NOT NULL,
    phase_type TEXT NOT NULL CHECK (
        phase_type IN ('league', 'group', 'playoff', 'final', 'consolation')
    ),
    phase_order INTEGER NOT NULL DEFAULT 0,
    standings_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    bracket_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (tournament_id, phase_key)
);

CREATE TABLE IF NOT EXISTS core.tournament_entries (
    tournament_entry_id BIGSERIAL PRIMARY KEY,
    tournament_id BIGINT NOT NULL REFERENCES core.tournaments(tournament_id) ON DELETE CASCADE,
    team_id BIGINT NOT NULL REFERENCES core.teams(team_id) ON DELETE RESTRICT,
    seed INTEGER NULL,
    entry_status TEXT NOT NULL DEFAULT 'active' CHECK (
        entry_status IN ('active', 'withdrawn', 'eliminated', 'completed')
    ),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tournament_id, team_id)
);

CREATE TABLE IF NOT EXISTS core.tournament_fixtures (
    tournament_fixture_id BIGSERIAL PRIMARY KEY,
    tournament_id BIGINT NOT NULL REFERENCES core.tournaments(tournament_id) ON DELETE CASCADE,
    tournament_phase_id BIGINT NOT NULL REFERENCES core.tournament_phases(tournament_phase_id) ON DELETE CASCADE,
    home_entry_id BIGINT NULL REFERENCES core.tournament_entries(tournament_entry_id) ON DELETE SET NULL,
    away_entry_id BIGINT NULL REFERENCES core.tournament_entries(tournament_entry_id) ON DELETE SET NULL,
    round_number INTEGER NULL,
    matchday_number INTEGER NULL,
    leg_number INTEGER NULL,
    scheduled_at TIMESTAMPTZ NULL,
    played_match_id BIGINT NULL REFERENCES core.matches(match_id) ON DELETE SET NULL,
    fixture_status TEXT NOT NULL DEFAULT 'scheduled' CHECK (
        fixture_status IN ('scheduled', 'active', 'played', 'void', 'cancelled')
    ),
    result_type TEXT NOT NULL DEFAULT 'normal' CHECK (
        result_type IN ('normal', 'draw', 'home_forfeit', 'away_forfeit', 'double_forfeit')
    ),
    score_home INTEGER NULL,
    score_away INTEGER NULL,
    bracket_slot TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_tournament_fixtures_phase_schedule
    ON core.tournament_fixtures(tournament_phase_id, matchday_number, scheduled_at);

CREATE INDEX IF NOT EXISTS idx_tournament_fixtures_match
    ON core.tournament_fixtures(played_match_id);

CREATE TABLE IF NOT EXISTS core.tournament_standings_current (
    tournament_phase_id BIGINT NOT NULL REFERENCES core.tournament_phases(tournament_phase_id) ON DELETE CASCADE,
    team_id BIGINT NOT NULL REFERENCES core.teams(team_id) ON DELETE CASCADE,
    matches_played INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    draws INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    goals_for INTEGER NOT NULL DEFAULT 0,
    goals_against INTEGER NOT NULL DEFAULT 0,
    goal_difference INTEGER NOT NULL DEFAULT 0,
    points INTEGER NOT NULL DEFAULT 0,
    form_last_5 TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tournament_phase_id, team_id)
);

CREATE INDEX IF NOT EXISTS idx_tournament_standings_rank
    ON core.tournament_standings_current(
        tournament_phase_id,
        points DESC,
        goal_difference DESC,
        goals_for DESC
    );

CREATE TRIGGER trg_tournaments_touch
    BEFORE UPDATE ON core.tournaments
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

CREATE TRIGGER trg_tournament_phases_touch
    BEFORE UPDATE ON core.tournament_phases
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

CREATE TRIGGER trg_tournament_fixtures_touch
    BEFORE UPDATE ON core.tournament_fixtures
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

CREATE TRIGGER trg_tournament_standings_current_touch
    BEFORE UPDATE ON core.tournament_standings_current
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

CREATE TABLE IF NOT EXISTS core.tournament_schedules (
    tournament_schedule_id BIGSERIAL PRIMARY KEY,
    tournament_id BIGINT NOT NULL REFERENCES core.tournaments(tournament_id) ON DELETE CASCADE,
    tournament_fixture_id BIGINT NOT NULL REFERENCES core.tournament_fixtures(tournament_fixture_id) ON DELETE CASCADE,
    proposed_by_discord_user_id BIGINT NOT NULL,
    last_action_by_discord_user_id BIGINT NULL,
    proposed_time TIMESTAMPTZ NOT NULL,
    slot_start TIMESTAMPTZ NOT NULL,
    server_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'countered', 'confirmed', 'cancelled', 'expired')
    ),
    proposal_expires_at TIMESTAMPTZ NULL,
    proposal_message_ids JSONB NOT NULL DEFAULT '{}'::jsonb,
    confirmed_at TIMESTAMPTZ NULL,
    reminder_sent_at TIMESTAMPTZ NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tournament_schedules_lookup
    ON core.tournament_schedules(tournament_id, status, proposed_time);

CREATE INDEX IF NOT EXISTS idx_tournament_schedules_fixture
    ON core.tournament_schedules(tournament_fixture_id, status);

CREATE TABLE IF NOT EXISTS core.tournament_schedule_votes (
    tournament_schedule_vote_id BIGSERIAL PRIMARY KEY,
    tournament_schedule_id BIGINT NOT NULL REFERENCES core.tournament_schedules(tournament_schedule_id) ON DELETE CASCADE,
    team_id BIGINT NULL REFERENCES core.teams(team_id) ON DELETE SET NULL,
    discord_user_id BIGINT NOT NULL,
    vote BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tournament_schedule_id, discord_user_id)
);

CREATE INDEX IF NOT EXISTS idx_tournament_schedule_votes_schedule
    ON core.tournament_schedule_votes(tournament_schedule_id, team_id);

CREATE TRIGGER trg_tournament_schedules_touch
    BEFORE UPDATE ON core.tournament_schedules
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

CREATE TRIGGER trg_tournament_schedule_votes_touch
    BEFORE UPDATE ON core.tournament_schedule_votes
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

-- ---------------------------------------------------------------------------
-- Media
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.media_assets (
    media_asset_id BIGSERIAL PRIMARY KEY,
    media_kind TEXT NOT NULL CHECK (
        media_kind IN ('image', 'video', 'clip', 'highlight', 'download', 'other')
    ),
    title TEXT NOT NULL,
    description TEXT NULL,
    public_url TEXT NOT NULL,
    thumbnail_url TEXT NULL,
    storage_uri TEXT NULL,
    uploaded_by_account_id BIGINT NULL REFERENCES core.accounts(account_id) ON DELETE SET NULL,
    match_id BIGINT NULL REFERENCES core.matches(match_id) ON DELETE SET NULL,
    team_id BIGINT NULL REFERENCES core.teams(team_id) ON DELETE SET NULL,
    account_id BIGINT NULL REFERENCES core.accounts(account_id) ON DELETE SET NULL,
    tournament_id BIGINT NULL REFERENCES core.tournaments(tournament_id) ON DELETE SET NULL,
    duration_seconds INTEGER NULL,
    file_size_bytes BIGINT NULL,
    mime_type TEXT NULL,
    visibility TEXT NOT NULL DEFAULT 'public' CHECK (
        visibility IN ('public', 'private', 'unlisted')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_media_assets_match
    ON core.media_assets(match_id);

CREATE INDEX IF NOT EXISTS idx_media_assets_account
    ON core.media_assets(account_id);

CREATE INDEX IF NOT EXISTS idx_media_assets_team
    ON core.media_assets(team_id);

CREATE INDEX IF NOT EXISTS idx_media_assets_visibility_kind
    ON core.media_assets(visibility, media_kind, created_at DESC);

CREATE TABLE IF NOT EXISTS core.media_tags (
    media_asset_id BIGINT NOT NULL REFERENCES core.media_assets(media_asset_id) ON DELETE CASCADE,
    tag CITEXT NOT NULL,
    PRIMARY KEY (media_asset_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_media_tags_tag
    ON core.media_tags(tag);

CREATE TRIGGER trg_media_assets_touch
    BEFORE UPDATE ON core.media_assets
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

-- ---------------------------------------------------------------------------
-- Canonical convenience views
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW core.v_account_primary_identities AS
SELECT
    a.account_id,
    a.hub_user_id,
    COALESCE(a.display_name, sd.global_name, sd.username, ss.steam_profile_name) AS display_name,
    sd.discord_user_id AS primary_discord_user_id,
    ss.steam_id_64 AS primary_steam_id_64,
    ss.steam_id_legacy AS primary_steam_id_legacy,
    a.avatar_url,
    a.is_active,
    a.created_at,
    a.updated_at
FROM core.accounts a
LEFT JOIN core.account_discord_identities sd
    ON sd.account_id = a.account_id
   AND sd.is_primary = TRUE
LEFT JOIN core.account_steam_identities ss
    ON ss.account_id = a.account_id
   AND ss.is_primary = TRUE;

CREATE OR REPLACE VIEW core.v_team_current_captains AS
SELECT
    t.team_id,
    t.name,
    m.account_id,
    m.membership_role
FROM core.teams t
JOIN core.team_memberships m
    ON m.team_id = t.team_id
   AND m.left_at IS NULL
   AND m.is_active = TRUE
   AND m.membership_role IN ('captain', 'vice_captain');

CREATE OR REPLACE VIEW core.v_match_scoreboards AS
SELECT
    m.match_id,
    m.external_match_id,
    m.played_at,
    m.game_format,
    m.competition_kind,
    m.match_status,
    m.home_team_id,
    COALESCE(th.name, m.home_team_name_snapshot) AS home_team_name,
    m.home_score,
    m.away_team_id,
    COALESCE(ta.name, m.away_team_name_snapshot) AS away_team_name,
    m.away_score,
    m.went_extra_time,
    m.went_penalties,
    m.comeback_flag
FROM core.matches m
LEFT JOIN core.teams th ON th.team_id = m.home_team_id
LEFT JOIN core.teams ta ON ta.team_id = m.away_team_id;

-- ---------------------------------------------------------------------------
-- Hub publication layer
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hub.sync_state (
    sync_key TEXT PRIMARY KEY,
    last_synced_at TIMESTAMPTZ NULL,
    last_source_updated_at TIMESTAMPTZ NULL,
    rows_synced INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'idle' CHECK (
        status IN ('idle', 'running', 'succeeded', 'failed')
    ),
    error_message TEXT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hub.snapshot_runs (
    snapshot_run_id BIGSERIAL PRIMARY KEY,
    snapshot_kind TEXT NOT NULL CHECK (
        snapshot_kind IN (
            'bootstrap',
            'homepage',
            'players',
            'teams',
            'matches',
            'tournaments',
            'rankings',
            'media'
        )
    ),
    source_rating_run_id BIGINT NULL REFERENCES core.rating_runs(rating_run_id) ON DELETE SET NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL,
    status TEXT NOT NULL DEFAULT 'running' CHECK (
        status IN ('running', 'succeeded', 'failed', 'partial')
    ),
    notes JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS hub.profile_overrides (
    owner_type TEXT NOT NULL CHECK (
        owner_type IN ('account', 'team', 'discord_user')
    ),
    owner_key TEXT NOT NULL,
    display_name TEXT NULL,
    avatar_url TEXT NULL,
    banner_url TEXT NULL,
    bio TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (owner_type, owner_key)
);

CREATE TABLE IF NOT EXISTS hub.homepage_snapshots (
    snapshot_key TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    snapshot_run_id BIGINT NULL REFERENCES hub.snapshot_runs(snapshot_run_id) ON DELETE SET NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hub.player_summaries (
    account_id BIGINT PRIMARY KEY REFERENCES core.accounts(account_id) ON DELETE CASCADE,
    primary_discord_user_id BIGINT NULL,
    primary_steam_id_64 BIGINT NULL,
    display_name TEXT NOT NULL,
    avatar_url TEXT NULL,
    current_team_id BIGINT NULL REFERENCES core.teams(team_id) ON DELETE SET NULL,
    current_team_name TEXT NULL,
    primary_position TEXT NULL,
    rating NUMERIC(5,2) NULL,
    atk_rating NUMERIC(5,2) NULL,
    mid_rating NUMERIC(5,2) NULL,
    def_rating NUMERIC(5,2) NULL,
    gk_rating NUMERIC(5,2) NULL,
    appearances INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    draws INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    goals INTEGER NOT NULL DEFAULT 0,
    assists INTEGER NOT NULL DEFAULT 0,
    interceptions INTEGER NOT NULL DEFAULT 0,
    saves INTEGER NOT NULL DEFAULT 0,
    mvp_awards INTEGER NOT NULL DEFAULT 0,
    last_match_at TIMESTAMPTZ NULL,
    snapshot_run_id BIGINT NULL REFERENCES hub.snapshot_runs(snapshot_run_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hub_player_summaries_rating
    ON hub.player_summaries(rating DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS hub.player_profiles (
    account_id BIGINT PRIMARY KEY REFERENCES core.accounts(account_id) ON DELETE CASCADE,
    payload JSONB NOT NULL,
    snapshot_run_id BIGINT NULL REFERENCES hub.snapshot_runs(snapshot_run_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hub.team_summaries (
    team_id BIGINT PRIMARY KEY REFERENCES core.teams(team_id) ON DELETE CASCADE,
    discord_guild_id BIGINT NULL,
    name TEXT NOT NULL,
    short_name TEXT NULL,
    crest_url TEXT NULL,
    captain_account_id BIGINT NULL REFERENCES core.accounts(account_id) ON DELETE SET NULL,
    captain_name TEXT NULL,
    average_rating NUMERIC(5,2) NULL,
    wins INTEGER NOT NULL DEFAULT 0,
    draws INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    goals_for INTEGER NOT NULL DEFAULT 0,
    goals_against INTEGER NOT NULL DEFAULT 0,
    player_count INTEGER NOT NULL DEFAULT 0,
    team_type TEXT NOT NULL,
    snapshot_run_id BIGINT NULL REFERENCES hub.snapshot_runs(snapshot_run_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hub_team_summaries_rating
    ON hub.team_summaries(average_rating DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS hub.team_profiles (
    team_id BIGINT PRIMARY KEY REFERENCES core.teams(team_id) ON DELETE CASCADE,
    payload JSONB NOT NULL,
    snapshot_run_id BIGINT NULL REFERENCES hub.snapshot_runs(snapshot_run_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hub.match_summaries (
    match_id BIGINT PRIMARY KEY REFERENCES core.matches(match_id) ON DELETE CASCADE,
    external_match_id TEXT NOT NULL,
    played_at TIMESTAMPTZ NOT NULL,
    game_format TEXT NOT NULL,
    competition_label TEXT NOT NULL,
    home_team_id BIGINT NULL REFERENCES core.teams(team_id) ON DELETE SET NULL,
    home_team_name TEXT NOT NULL,
    away_team_id BIGINT NULL REFERENCES core.teams(team_id) ON DELETE SET NULL,
    away_team_name TEXT NOT NULL,
    home_score INTEGER NOT NULL,
    away_score INTEGER NOT NULL,
    went_extra_time BOOLEAN NOT NULL DEFAULT FALSE,
    went_penalties BOOLEAN NOT NULL DEFAULT FALSE,
    comeback_flag BOOLEAN NOT NULL DEFAULT FALSE,
    mvp_account_id BIGINT NULL REFERENCES core.accounts(account_id) ON DELETE SET NULL,
    mvp_name TEXT NULL,
    mvp_rating NUMERIC(5,2) NULL,
    snapshot_run_id BIGINT NULL REFERENCES hub.snapshot_runs(snapshot_run_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hub_match_summaries_played
    ON hub.match_summaries(played_at DESC);

CREATE TABLE IF NOT EXISTS hub.match_details (
    match_id BIGINT PRIMARY KEY REFERENCES core.matches(match_id) ON DELETE CASCADE,
    payload JSONB NOT NULL,
    snapshot_run_id BIGINT NULL REFERENCES hub.snapshot_runs(snapshot_run_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hub.tournament_summaries (
    tournament_id BIGINT PRIMARY KEY REFERENCES core.tournaments(tournament_id) ON DELETE CASCADE,
    slug CITEXT NOT NULL,
    name TEXT NOT NULL,
    format_kind TEXT NOT NULL,
    game_format TEXT NOT NULL,
    status TEXT NOT NULL,
    teams_count INTEGER NOT NULL DEFAULT 0,
    current_winner_team_id BIGINT NULL REFERENCES core.teams(team_id) ON DELETE SET NULL,
    snapshot_run_id BIGINT NULL REFERENCES hub.snapshot_runs(snapshot_run_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hub.tournament_details (
    tournament_id BIGINT PRIMARY KEY REFERENCES core.tournaments(tournament_id) ON DELETE CASCADE,
    payload JSONB NOT NULL,
    snapshot_run_id BIGINT NULL REFERENCES hub.snapshot_runs(snapshot_run_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hub.rankings_snapshots (
    ranking_key TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    snapshot_run_id BIGINT NULL REFERENCES hub.snapshot_runs(snapshot_run_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hub.media_summaries (
    media_asset_id BIGINT PRIMARY KEY REFERENCES core.media_assets(media_asset_id) ON DELETE CASCADE,
    media_kind TEXT NOT NULL,
    title TEXT NOT NULL,
    thumbnail_url TEXT NULL,
    public_url TEXT NOT NULL,
    duration_seconds INTEGER NULL,
    uploader_name TEXT NULL,
    snapshot_run_id BIGINT NULL REFERENCES hub.snapshot_runs(snapshot_run_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hub_media_summaries_kind
    ON hub.media_summaries(media_kind, updated_at DESC);

-- ---------------------------------------------------------------------------
-- Additions applied 2026-08-26 during the matches/tournaments migration pass:
-- fixture bracket progression + forfeit score, and context->tournament linkage
-- (mirrors legacy public.tournament_fixtures / public.active_match_contexts
-- fields that the original proposal omitted).
-- ---------------------------------------------------------------------------

ALTER TABLE core.tournament_fixtures
    ADD COLUMN IF NOT EXISTS forfeit_score INTEGER NULL,
    ADD COLUMN IF NOT EXISTS home_source TEXT NULL,
    ADD COLUMN IF NOT EXISTS away_source TEXT NULL,
    ADD COLUMN IF NOT EXISTS winner_to_fixture_id BIGINT NULL REFERENCES core.tournament_fixtures(tournament_fixture_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS loser_to_fixture_id BIGINT NULL REFERENCES core.tournament_fixtures(tournament_fixture_id) ON DELETE SET NULL;

ALTER TABLE core.match_origin_contexts
    ADD COLUMN IF NOT EXISTS tournament_id BIGINT NULL REFERENCES core.tournaments(tournament_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS tournament_fixture_id BIGINT NULL REFERENCES core.tournament_fixtures(tournament_fixture_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS tournament_schedule_id BIGINT NULL REFERENCES core.tournament_schedules(tournament_schedule_id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------------
-- Added 2026-08-26: real per-team alias table, consolidating the legacy
-- public.team_name_aliases table (main-guild-only, never actually queried by
-- ios_bot) and the hardcoded main_guild_aliases list in match_importer.py
-- into one mechanism any team can use. Checked before fuzzy name matching.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.team_aliases (
    team_alias_id BIGSERIAL PRIMARY KEY,
    team_id BIGINT NOT NULL REFERENCES core.teams(team_id) ON DELETE CASCADE,
    alias_norm CITEXT NOT NULL UNIQUE,
    alias_display TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_team_aliases_team ON core.team_aliases(team_id);

-- ---------------------------------------------------------------------------
-- End of canonical schema
-- ---------------------------------------------------------------------------

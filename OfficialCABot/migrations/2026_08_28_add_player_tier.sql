-- Adds a player tier (Premier / Pro) to IOSCA_PLAYERS.
--
-- Needed for the new D1/D2 league structure: D2's loan rules cap a team at
-- two loan players from D1, in one of two combinations only (1 Premier + 1
-- Pro, or 2 Pro -- a goalkeeper counts as Pro). Staff assigns each player's
-- tier manually via /set_player_tier; there is no automatic derivation from
-- rating.
--
-- public.* remains the live bot's real source of truth (see
-- 2026_08_26_canonical_core_hub_schema.sql) -- this targets public to match
-- where the bot actually reads/writes today.

ALTER TABLE public.IOSCA_PLAYERS
    ADD COLUMN IF NOT EXISTS player_tier VARCHAR(10);

ALTER TABLE public.IOSCA_PLAYERS
    DROP CONSTRAINT IF EXISTS iosca_players_player_tier_check;

ALTER TABLE public.IOSCA_PLAYERS
    ADD CONSTRAINT iosca_players_player_tier_check
    CHECK (player_tier IS NULL OR player_tier IN ('premier', 'pro'));

-- Lets a tournament opt out of the global TOURNAMENT_AUTO_SYNC_REQUIRE_CONFIRMED_SCHEDULE
-- gate that sync_matches_for_tournament() checks in ios_bot/db/tournaments.py.
--
-- That gate exists to stop auto-sync from guessing wrong when a team could
-- plausibly have more than one live fixture against the same opponent at
-- once (e.g. group stage + playoffs) -- it only trusts a match-to-fixture
-- link when a confirmed /schedule proposal exists near the match's actual
-- time. D1/D2's fixtures were bulk-imported from an external calendar
-- rather than proposed/confirmed through the bot, so they have zero
-- confirmed-schedule rows and the gate silently blocks every auto-link.
--
-- For a straight round-robin league, "there's exactly one still-open
-- fixture for this pair" is already unambiguous on its own -- NULL here
-- means "use the global env var default" (preserves today's behavior for
-- every existing tournament), FALSE means "skip the confirmed-schedule
-- requirement, non-strict exactly-one-open-fixture matching is enough."

ALTER TABLE public.TOURNAMENTS
    ADD COLUMN IF NOT EXISTS auto_sync_require_confirmed_schedule BOOLEAN DEFAULT NULL;

from ios_bot.config import *
from ios_bot.signup_manager import (
    get_all_channel_ids_with_state,
    clear_and_refresh_channel,
    get_channel_state,
    update_state,
    refresh_lineup as sm_refresh_lineup,
)
from ios_bot.challenge_manager import active_challenges
from ios_bot.hub_sync_signal import has_pending_request, pending_reason, clear_pending_request
from ios_bot.db.core_catchup import sync_public_matches_to_core
from pathlib import Path
import subprocess
import sys
import asyncio
import os
import importlib
from typing import Any

# Track background tasks to prevent overlapping executions
refresh_statistics_task = None
stats_refresh_seconds = max(15, int(os.getenv("STATS_REFRESH_SECONDS", "60")))
stats_refresh_respects_quiet_window = os.getenv("STATS_REFRESH_RESPECTS_QUIET_WINDOW", "0").strip().lower() in {"1", "true", "yes", "on"}
# Adaptive SFTP polling: stats_refresh_seconds (above) is the "someone's
# probably mid-match" fast interval. When no /ready has opened a match
# context recently, the poll backs off to this much slower interval instead
# of hammering every game server's SFTP with a fresh connection every tick
# around the clock regardless of activity.
stats_refresh_idle_seconds = max(stats_refresh_seconds, int(os.getenv("STATS_REFRESH_IDLE_SECONDS", "600")))
stats_refresh_activity_window_hours = float(os.getenv("STATS_REFRESH_ACTIVITY_WINDOW_HOURS", "3"))
_stats_refresh_current_mode = "fast"  # "fast" or "idle" -- tracks which interval is currently active
# Define the target time in Eastern Time (New York)
est_timezone = pytz.timezone('EST')
clear_time = time(5, 0, 0, tzinfo=est_timezone)
CHALLENGE_INACTIVITY_HOURS = int(os.getenv("CHALLENGE_INACTIVITY_HOURS", "6"))
ratings_refresh_timezone = pytz.timezone(os.getenv("RATINGS_REFRESH_TIMEZONE", "America/New_York"))
ratings_refresh_hour = int(os.getenv("RATINGS_REFRESH_HOUR", "4"))
ratings_refresh_minute = int(os.getenv("RATINGS_REFRESH_MINUTE", "0"))
ratings_refresh_time = time(ratings_refresh_hour, ratings_refresh_minute, 0, tzinfo=ratings_refresh_timezone)
ratings_refresh_running = False
heavy_task_timezone = pytz.timezone(os.getenv("HEAVY_TASK_TIMEZONE", "America/New_York"))
heavy_task_quiet_start_hour = int(os.getenv("HEAVY_TASK_QUIET_START_HOUR", "3"))
heavy_task_quiet_end_hour = int(os.getenv("HEAVY_TASK_QUIET_END_HOUR", "11"))
hub_incremental_sync_seconds = max(
    60,
    int(os.getenv("HUB_INCREMENTAL_SYNC_SECONDS", os.getenv("HUB_MYSQL_INCREMENTAL_SYNC_SECONDS", "900"))),
)
hub_sync_enabled = os.getenv("IOSCA_HUB_SYNC_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
hub_sync_running = False
# Forced full resync is a safety net to reconcile rows the incremental
# (updated_at-driven) sync can miss -- most notably deletions. Bumped
# 24h -> 7d: additions/edits no longer depend on this at all now that
# hub_sync_signal.request_hub_sync_soon() triggers an (incremental) sync
# within one scheduler tick of any team/match/tournament/player write, so
# stretching this only affects how long a *deletion* takes to disappear
# from the Hub -- accepted tradeoff for a ~100-player community's egress
# budget. Override via env if that tradeoff isn't right for you.
hub_force_full_sync_seconds = max(900, int(os.getenv("HUB_FORCE_FULL_SYNC_SECONDS", "604800")))
hub_force_full_sync_last_completed_at = None
hub_sync_last_completed_at = None
hub_sync_timezone = pytz.timezone(os.getenv("HUB_SYNC_TIMEZONE", "America/New_York"))
hub_sync_active_start_hour = int(os.getenv("HUB_SYNC_ACTIVE_START_HOUR", "13"))
hub_sync_active_end_hour = int(os.getenv("HUB_SYNC_ACTIVE_END_HOUR", "3"))
hub_sync_outside_active_window = os.getenv("HUB_SYNC_OUTSIDE_ACTIVE_WINDOW", "0").strip().lower() in {"1", "true", "yes", "on"}
hub_inactive_sync_seconds = max(
    900,
    int(os.getenv("HUB_INACTIVE_SYNC_SECONDS", "3600")),
)
hub_sync_scheduler_poll_seconds = max(
    60,
    int(os.getenv("HUB_SYNC_SCHEDULER_POLL_SECONDS", "60")),
)
hub_avatar_sync_seconds = max(3600, int(os.getenv("HUB_AVATAR_SYNC_SECONDS", "21600")))
hub_avatar_refresh_after_seconds = max(3600, int(os.getenv("HUB_AVATAR_REFRESH_AFTER_SECONDS", "604800")))
hub_avatar_sync_max_remote_fetches = max(0, int(os.getenv("HUB_AVATAR_SYNC_MAX_REMOTE_FETCHES", "8")))
hub_avatar_sync_remote_fetch_delay_seconds = max(
    0.0,
    float(os.getenv("HUB_AVATAR_SYNC_REMOTE_FETCH_DELAY_SECONDS", "0.35")),
)
hub_avatar_sync_running = False
hub_avatar_sync_last_completed_at = None
hub_link_backfill_interval_seconds = max(900, int(os.getenv("HUB_LINK_BACKFILL_INTERVAL_SECONDS", "21600")))
hub_link_backfill_last_completed_at = None
hub_immediate_refresh_requires_active_window = os.getenv("HUB_IMMEDIATE_REFRESH_REQUIRES_ACTIVE_WINDOW", "1").strip().lower() in {"1", "true", "yes", "on"}
hub_story_refresh_requires_active_window = os.getenv("HUB_STORY_REFRESH_REQUIRES_ACTIVE_WINDOW", "1").strip().lower() in {"1", "true", "yes", "on"}
hub_auth_dm_poll_seconds = max(30, int(os.getenv("HUB_AUTH_DM_POLL_SECONDS", "60")))
_hub_sync_import_error = None
_hub_sync_create_hub_pool = None
_hub_sync_sync_all = None
_hub_sync_schema = None


# Nothing previously watched whether a scheduled background task kept
# failing -- everything just print()s to a log nobody's actively watching.
# That's exactly how the Hub sync's force_full pass stayed silently broken
# for an unknown stretch this session, discovered by accident during
# unrelated work. This tracks consecutive failures per task name and posts
# to the admin/confirmed-schedule channel once a task crosses a threshold,
# then periodically (not every cycle) while it stays broken.
_consecutive_task_failures: dict[str, int] = {}
_ALERT_AFTER_CONSECUTIVE_FAILURES = 3
_ALERT_REPEAT_EVERY_N_FAILURES = 10


async def _notify_admin_of_task_trouble(task_name: str, detail: str) -> None:
    try:
        channel_id = CONFIRMED_SCHEDULE_CHANNEL_ID
        if not channel_id:
            return
        channel = bot.get_channel(channel_id)
        if not channel:
            return
        role_ping = f"<@&{ADMIN_ROLE_ID}> " if ADMIN_ROLE_ID else ""
        await channel.send(f"{role_ping}⚠️ **{task_name}** {detail}")
    except Exception as notify_error:
        print(f"Failed to send admin alert for {task_name}: {notify_error!r}")


async def _record_task_outcome(task_name: str, *, success: bool, error: str | None = None) -> None:
    """Call with success=True on every clean run (resets the streak) and
    success=False with the error on every failure. Only actually notifies
    once the failure streak crosses the alert threshold, and then only every
    Nth failure after that -- not on every single cycle of a prolonged outage.
    """
    if success:
        _consecutive_task_failures[task_name] = 0
        return
    count = _consecutive_task_failures.get(task_name, 0) + 1
    _consecutive_task_failures[task_name] = count
    if count == _ALERT_AFTER_CONSECUTIVE_FAILURES or (
        count > _ALERT_AFTER_CONSECUTIVE_FAILURES and count % _ALERT_REPEAT_EVERY_N_FAILURES == 0
    ):
        await _notify_admin_of_task_trouble(
            task_name,
            f"has failed {count} times in a row. Latest error: {error or 'unknown'}",
        )


def _resolve_hub_backend_dir() -> Path | None:
    root = Path(__file__).resolve().parents[1]
    configured = os.getenv("IOSCA_HUB_BACKEND_DIR")
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = (root / candidate).resolve()
        if (candidate / "app" / "sync.py").exists():
            return candidate
    candidate = root / "ioscahub.github.io" / "backend"
    if (candidate / "app" / "sync.py").exists():
        return candidate
    return None


def _load_hub_sync_components():
    global _hub_sync_import_error, _hub_sync_create_hub_pool, _hub_sync_sync_all, _hub_sync_schema

    if _hub_sync_create_hub_pool and _hub_sync_sync_all and _hub_sync_schema:
        return _hub_sync_create_hub_pool, _hub_sync_sync_all, _hub_sync_schema
    if _hub_sync_import_error is not None:
        raise _hub_sync_import_error

    backend_dir = _resolve_hub_backend_dir()
    if backend_dir is None:
        _hub_sync_import_error = RuntimeError("Hub backend directory not found for Hub sync task.")
        raise _hub_sync_import_error

    backend_path = str(backend_dir)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)

    try:
        hub_db = importlib.import_module("app.db")
        hub_config = importlib.import_module("app.config")
        hub_sync = importlib.import_module("app.sync")
        _hub_sync_create_hub_pool = hub_db.create_hub_postgres_pool
        _hub_sync_sync_all = hub_sync.sync_all
        _hub_sync_schema = str(hub_config.HUB_POSTGRES_SCHEMA)
    except Exception as exc:
        _hub_sync_import_error = exc
        raise

    return _hub_sync_create_hub_pool, _hub_sync_sync_all, _hub_sync_schema


def _should_refresh_hub_avatars(now_utc: datetime | None = None) -> bool:
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if hub_avatar_sync_last_completed_at is None:
        return True
    return (now_utc - hub_avatar_sync_last_completed_at).total_seconds() >= hub_avatar_sync_seconds


def _should_force_full_hub_sync(now_utc: datetime | None = None) -> bool:
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if hub_force_full_sync_last_completed_at is None:
        return True
    return (now_utc - hub_force_full_sync_last_completed_at).total_seconds() >= hub_force_full_sync_seconds


def _is_within_hub_sync_active_window(now_local: datetime | None = None) -> bool:
    if now_local is None:
        now_local = datetime.now(hub_sync_timezone)

    start = hub_sync_active_start_hour % 24
    end = hub_sync_active_end_hour % 24
    hour = now_local.hour

    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _get_hub_sync_interval_seconds(now_local: datetime | None = None) -> int:
    if _is_within_hub_sync_active_window(now_local):
        return hub_incremental_sync_seconds
    return hub_inactive_sync_seconds


def _should_run_hub_link_backfill(now_utc: datetime | None = None) -> bool:
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if hub_link_backfill_last_completed_at is None:
        return True
    return (now_utc - hub_link_backfill_last_completed_at).total_seconds() >= hub_link_backfill_interval_seconds


def _get_cached_avatar_user(discord_id: int):
    user = bot.get_user(discord_id)
    if user is not None:
        return user

    for guild in bot.guilds:
        member = guild.get_member(discord_id)
        if member is not None:
            return member

    return None


async def _resolve_avatar_user(discord_id: int, *, allow_remote_fetch: bool = True):
    user = _get_cached_avatar_user(discord_id)
    if user is not None:
        return user

    if not allow_remote_fetch:
        return None

    try:
        return await bot.fetch_user(discord_id)
    except Exception:
        return None


async def _refresh_hub_player_avatars(hub_pool, hub_schema: str) -> dict[str, int]:
    if not hasattr(bot, "db") or not bot.db:
        return {
            "players_processed": 0,
            "avatars_upserted": 0,
            "players_skipped": 0,
            "remote_fetches": 0,
            "deferred_remote_fetches": 0,
        }

    rows = await bot.db.pool.fetch(
        """
        SELECT
            trim(steam_id::text) AS steam_id,
            trim(discord_id::text) AS discord_id
        FROM IOSCA_PLAYERS
        WHERE steam_id IS NOT NULL
          AND trim(steam_id::text) <> ''
          AND discord_id IS NOT NULL
          AND trim(discord_id::text) <> ''
        """
    )

    discord_ids = [
        str(row.get("discord_id") or "").strip()
        for row in rows
        if str(row.get("discord_id") or "").strip()
    ]
    existing_overrides = {}
    if discord_ids:
        async with hub_pool.acquire() as conn:
            override_rows = await conn.fetch(
                f"""
                SELECT owner_key, avatar_url, updated_at
                FROM "{hub_schema}".hub_profile_overrides
                WHERE owner_type = 'discord_user'
                  AND owner_key = ANY($1::text[])
                """,
                discord_ids,
            )
        existing_overrides = {
            str(row.get("owner_key")): {
                "avatar_url": row.get("avatar_url"),
                "updated_at": row.get("updated_at"),
            }
            for row in override_rows
        }

    processed = 0
    skipped = 0
    deferred_remote_fetches = 0
    remote_fetches = 0
    upsert_rows = []
    now_utc = datetime.now(timezone.utc)

    for row in rows:
        steam_id = str(row.get("steam_id") or "").strip()
        discord_id_raw = str(row.get("discord_id") or "").strip()
        if not steam_id or not discord_id_raw:
            skipped += 1
            continue

        existing_override = existing_overrides.get(discord_id_raw)
        existing_updated_at = existing_override.get("updated_at") if existing_override else None
        existing_avatar_url = str(existing_override.get("avatar_url") or "").strip() if existing_override else ""
        if existing_updated_at is not None:
            if existing_updated_at.tzinfo is None:
                existing_updated_at = existing_updated_at.replace(tzinfo=timezone.utc)
            age_seconds = (now_utc - existing_updated_at).total_seconds()
        else:
            age_seconds = None
        needs_refresh = (
            not existing_avatar_url
            or age_seconds is None
            or age_seconds >= hub_avatar_refresh_after_seconds
        )
        if not needs_refresh:
            skipped += 1
            continue

        try:
            discord_id = int(discord_id_raw)
        except ValueError:
            skipped += 1
            continue

        processed += 1
        user = _get_cached_avatar_user(discord_id)
        if user is None:
            if remote_fetches >= hub_avatar_sync_max_remote_fetches:
                deferred_remote_fetches += 1
                skipped += 1
                continue
            user = await _resolve_avatar_user(discord_id, allow_remote_fetch=True)
            remote_fetches += 1
            if hub_avatar_sync_remote_fetch_delay_seconds > 0:
                await asyncio.sleep(hub_avatar_sync_remote_fetch_delay_seconds)

        if user is None or not getattr(user, "display_avatar", None):
            skipped += 1
            continue

        display_name = getattr(user, "display_name", None) or getattr(user, "name", None) or steam_id
        avatar_url = str(user.display_avatar.url)
        upsert_rows.append(("player", steam_id, display_name, avatar_url))
        upsert_rows.append(("discord_user", discord_id_raw, display_name, avatar_url))

    if upsert_rows:
        async with hub_pool.acquire() as conn:
            await conn.executemany(
                f"""
                INSERT INTO "{hub_schema}".hub_profile_overrides (owner_type, owner_key, display_name, avatar_url, updated_at)
                VALUES ($1, $2, $3, $4, NOW())
                ON CONFLICT (owner_type, owner_key) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    avatar_url = EXCLUDED.avatar_url,
                    updated_at = NOW()
                """,
                upsert_rows,
            )

    return {
        "players_processed": processed,
        "avatars_upserted": len(upsert_rows),
        "players_skipped": skipped,
        "remote_fetches": remote_fetches,
        "deferred_remote_fetches": deferred_remote_fetches,
    }


async def _run_hub_sync_once(*, force_full: bool = False) -> dict[str, Any] | None:
    global hub_sync_enabled, hub_sync_running, hub_avatar_sync_running
    global hub_avatar_sync_last_completed_at, hub_force_full_sync_last_completed_at, hub_sync_last_completed_at
    global hub_link_backfill_last_completed_at

    if not hub_sync_enabled:
        return None
    if hub_sync_running:
        return None
    if not hasattr(bot, "db") or not bot.db:
        return None

    try:
        create_hub_pool, sync_all, hub_schema = _load_hub_sync_components()
    except Exception as e:
        print(f"Error loading Hub sync components: {e}")
        print("Hub incremental sync disabled until restart.")
        hub_sync_enabled = False
        return None

    hub_sync_running = True
    hub_pool = None
    try:
        if force_full or _should_run_hub_link_backfill():
            try:
                await bot.db.matches.backfill_match_team_links(threshold=0.8)
                hub_link_backfill_last_completed_at = datetime.now(timezone.utc)
            except Exception as backfill_error:
                print(f"Error backfilling match links before Hub sync: {backfill_error!r}")

        hub_pool = await create_hub_pool()
        results = await sync_all(bot.db.pool, hub_pool, force_full=force_full)
        total_rows = sum(result.rows for result in results)
        avatar_sync_result = None
        if not hub_avatar_sync_running and _should_refresh_hub_avatars():
            hub_avatar_sync_running = True
            try:
                avatar_sync_result = await _refresh_hub_player_avatars(hub_pool, hub_schema)
                hub_avatar_sync_last_completed_at = datetime.now(timezone.utc)
            finally:
                hub_avatar_sync_running = False

        if force_full:
            hub_force_full_sync_last_completed_at = datetime.now(timezone.utc)
        hub_sync_last_completed_at = datetime.now(timezone.utc)
        await _record_task_outcome("Hub sync", success=True)

        print(
            "Hub sync complete: "
            f"{len(results)} table syncs / {total_rows} rows mirrored."
            f"{' [forced full sync]' if force_full else ''}"
        )
        if avatar_sync_result is not None:
            print(
                "Hub avatar sync complete: "
                f"{avatar_sync_result.get('players_processed', 0)} players processed / "
                f"{avatar_sync_result.get('avatars_upserted', 0)} override rows upserted / "
                f"{avatar_sync_result.get('remote_fetches', 0)} remote fetches / "
                f"{avatar_sync_result.get('deferred_remote_fetches', 0)} deferred."
            )

        return {
            "results": results,
            "total_rows": total_rows,
            "avatar_sync_result": avatar_sync_result,
            "force_full": force_full,
        }
    except Exception as e:
        print(f"Error during Hub sync: {e!r}")
        await _record_task_outcome("Hub sync", success=False, error=repr(e))
        return None
    finally:
        if hub_pool is not None:
            try:
                await hub_pool.close()
            except Exception as close_error:
                print(f"Error closing Hub sync pool: {close_error!r}")
        hub_sync_running = False


def get_matchmaking_activity_snapshot(
    *,
    min_players: int = 4,
    recent_signup_minutes: int = 45,
) -> dict[str, int | bool]:
    now = datetime.now(timezone.utc)
    max_players_in_lineup = 0
    active_channels = 0
    recent_signups = 0

    for channel_id in get_all_channel_ids_with_state():
        state = get_channel_state(channel_id)
        if not isinstance(state, dict):
            continue

        channel_has_activity = False
        for team in state.get("teams", []):
            if not isinstance(team, dict):
                continue
            filled = 0
            for player_data in team.values():
                if isinstance(player_data, dict) and player_data.get("player") is not None:
                    filled += 1
                    signup_time = player_data.get("signup_time")
                    if signup_time and (now - signup_time) <= timedelta(minutes=recent_signup_minutes):
                        recent_signups += 1
                        channel_has_activity = True
            if filled > max_players_in_lineup:
                max_players_in_lineup = filled
            if filled >= min_players:
                channel_has_activity = True

        for sub_obj in state.get("subs", []):
            if sub_obj is not None:
                channel_has_activity = True

        if channel_has_activity:
            active_channels += 1

    return {
        "active_channels": active_channels,
        "max_players_in_lineup": max_players_in_lineup,
        "recent_signups": recent_signups,
        "has_pressure": bool(active_channels > 0 or max_players_in_lineup >= min_players or recent_signups > 0),
    }


def _is_in_heavy_task_quiet_window(now_local: datetime | None = None) -> bool:
    if now_local is None:
        now_local = datetime.now(heavy_task_timezone)

    start = heavy_task_quiet_start_hour % 24
    end = heavy_task_quiet_end_hour % 24
    hour = now_local.hour

    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _get_heavy_task_skip_reason(task_name: str) -> str | None:
    if _is_in_heavy_task_quiet_window():
        return (
            f"quiet window active "
            f"({heavy_task_quiet_start_hour:02d}:00-{heavy_task_quiet_end_hour:02d}:00 {heavy_task_timezone.zone})"
        )
    return None


# --- Task State ---
@tasks.loop(time=clear_time)
async def clear_all_lineups():
    """A scheduled task to clear all matchmaking lineups daily at 5 AM Eastern Time."""
    try:        
        # Clean up stale challenges before clearing lineups to avoid lingering challenge overlays.
        await cleanup_stale_challenges()

        all_channel_ids = get_all_channel_ids_with_state()
        cleared_count = 0
        if not all_channel_ids:
            return
        else:
            for channel_id in all_channel_ids:
                try:
                    channel = bot.get_channel(channel_id)
                    if channel and isinstance(channel, discord.TextChannel):
                        await clear_and_refresh_channel(channel)
                        cleared_count += 1
                        await asyncio.sleep(1) # Avoid rate-limiting
                except Exception as e:
                    print(f"Error clearing lineup for channel {channel_id}: {e}")
        
    except Exception as e:
        print(f"Critical error in clear_all_lineups task: {e}")
        # Don't re-raise - let the bot continue running

async def cleanup_stale_challenges():
    """Remove challenges that are older than 24 hours."""
    try:
        from datetime import timedelta

        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=CHALLENGE_INACTIVITY_HOURS)
        stale_challenges = []
        
        for challenge_id, challenge in list(active_challenges.items()):
            last_touch = (
                challenge.get("accepted_timestamp")
                if challenge.get("status") == "accepted"
                else challenge.get("timestamp")
                or challenge.get("challenge_issued_at")
                or challenge.get("challenge_time")
            )
            if isinstance(last_touch, str):
                try:
                    last_touch = datetime.fromisoformat(last_touch)
                except Exception:
                    last_touch = None

            if not last_touch:
                continue

            if last_touch.tzinfo is None:
                now = datetime.now()
                cutoff = now - timedelta(hours=CHALLENGE_INACTIVITY_HOURS)
                if last_touch < cutoff:
                    stale_challenges.append(challenge_id)
            else:
                if last_touch < cutoff_time:
                    stale_challenges.append(challenge_id)
        
        if stale_challenges:
            for challenge_id in stale_challenges:
                try:
                    challenge = active_challenges.get(challenge_id)
                    if not challenge:
                        continue

                    # Clear any challenge flags in channel state.
                    for ch_id in filter(None, [challenge.get("initiating_channel_id"), challenge.get("opponent_channel_id")]):
                        state = get_channel_state(ch_id)
                        if state:
                            state.pop("is_challenged_by_team_name", None)
                            state.pop("active_challenge_game_type", None)
                            update_state(ch_id, state)

                        channel = bot.get_channel(ch_id)
                        if channel and isinstance(channel, discord.TextChannel):
                            try:
                                await sm_refresh_lineup(channel, force_new_message=True)
                            except Exception as e:
                                print(f"Error refreshing lineup during stale challenge cleanup for channel {ch_id}: {e}")

                    # Clean up broadcast challenge messages, if any.
                    for bc_channel_id, bc_msg_id in (challenge.get("broadcast_messages") or {}).items():
                        try:
                            bc_channel = bot.get_channel(bc_channel_id)
                            if bc_channel:
                                msg = await bc_channel.fetch_message(bc_msg_id)
                                await msg.edit(content="This challenge expired due to inactivity.", embed=None, view=None)
                        except Exception:
                            pass

                    del active_challenges[challenge_id]
                except KeyError:
                    pass  # Challenge already removed
        else:
            print("No stale challenges found.")
            
    except Exception as e:
        print(f"Error cleaning up stale challenges: {e}")


async def cleanup_expired_schedule_proposals():
    try:
        if not hasattr(bot, "db") or not bot.db:
            return
        schedules = await bot.db.tournaments.get_expired_pending_schedules()
        if not schedules:
            return
        from ios_bot.commands import tournaments as tournaments_cmd
        for schedule in schedules:
            try:
                await tournaments_cmd.handle_expired_schedule_proposal(schedule)
            except Exception as e:
                print(f"Error expiring schedule proposal {schedule.get('id')}: {e}")
    except Exception as e:
        print(f"Error during schedule proposal cleanup: {e}")


@tasks.loop(minutes=1)
async def check_inactive_players():
    """A task to automatically remove inactive players from lineups."""
    try:
        inactive_threshold = timedelta(minutes=180)
        now = datetime.now(timezone.utc)
        
        # Get a list of all channels with active lineups
        all_channel_ids = get_all_channel_ids_with_state()
        if not all_channel_ids:
            return

        for channel_id in all_channel_ids:
            try:
                state = get_channel_state(channel_id)
                if not state:
                    continue
                    
                # Make a local copy to check and modify
                state_copy = dict(state)
                state_modified = False

                # Safety Check: Do not remove players if the channel is part of an accepted challenge
                is_in_accepted_challenge = False
                for challenge in active_challenges.values():
                    if challenge.get("status") == "accepted":
                        if channel_id in [challenge.get("initiating_channel_id"), challenge.get("opponent_channel_id")]:
                            is_in_accepted_challenge = True
                            break
                if is_in_accepted_challenge:
                    continue

                channel = bot.get_channel(channel_id)
                if not channel:
                    continue

                for team in state_copy.get("teams", []):
                    for position, player_data in list(team.items()):
                        if player_data and player_data.get("signup_time"):
                            player_to_check = player_data['player']
                            signup_time = player_data["signup_time"]
                            time_diff = now - signup_time
                            
                            if time_diff > inactive_threshold:
                                # Unsign the player
                                team[position] = None
                                state_modified = True
                                
                                # Announce the removal
                                embed = Embed(
                                    description=f"Unsigning **{player_to_check.display_name}** from **{position}** for inactivity.",
                                    color=0x808080 # grey
                                )
                                timestamp = datetime.now(timezone.utc).strftime("%I:%M %p")
                                embed.set_footer(
                                    text=f"Automated AFK Check • {timestamp}",
                                    icon_url=bot.user.display_avatar.url if bot.user.display_avatar else None
                                )
                                try:
                                    await channel.send(embed=embed)
                                except Exception as e:
                                    print(f"Error during inactivity removal announcement in channel {channel_id}: {e}")
                
                if state_modified:
                    update_state(channel_id, state_copy)
                    try:
                        # Refresh the lineup to show the change
                        await sm_refresh_lineup(channel, force_new_message=True)
                    except Exception as e:
                        print(f"Error during inactivity removal refresh in channel {channel_id}: {e}")
            except Exception as e:
                print(f"Error processing channel {channel_id} for inactive players: {e}")
    except Exception as e:
        print(f"Critical error in check_inactive_players task: {e}")
        # Don't re-raise - let the bot continue running

async def _run_stats_compilation():
    """Background task to compile stats without blocking the main loop."""
    try:        
        # Import and run compile_stats
        try:
            from ios_bot.ratings.compile_stats import main as compile_stats_main
            await compile_stats_main(quiet=True)
        except Exception as compile_error:
            print(f"❌ Stats compilation error: {compile_error}", flush=True)
            import traceback
            traceback.print_exc()
            return  # Don't continue if compilation failed        
    except Exception as e:
        print(f"❌ An unexpected error occurred during stats compilation: {e}")
        import traceback
        traceback.print_exc()

@tasks.loop(seconds=stats_refresh_seconds)
async def refresh_statistics():
    """A task to refresh the player and match statistics and sync with database.
    Runs as a background task to avoid blocking other scheduled tasks.

    Adaptive interval: previously this polled every game server's SFTP on a
    flat interval 24/7 regardless of whether anyone was actually playing --
    a fresh SSH/SFTP connection per server, per tick, almost always finding
    nothing new. Now it checks for a recent open /ready match context (see
    MatchOperations.has_recent_open_match_context) and switches its own
    interval between the fast (stats_refresh_seconds) and idle
    (stats_refresh_idle_seconds) rates via change_interval() accordingly.
    The compilation itself still runs on every tick either way (so a match
    started outside /ready is still eventually picked up) -- only the
    *frequency* changes.
    """
    global refresh_statistics_task, _stats_refresh_current_mode

    try:
        if stats_refresh_respects_quiet_window:
            skip_reason = _get_heavy_task_skip_reason("refresh_statistics")
            if skip_reason:
                return

        try:
            db_handle = getattr(bot, "db", None)
            is_active = True
            if db_handle is not None and getattr(db_handle, "matches", None) is not None:
                is_active = await db_handle.matches.has_recent_open_match_context(
                    within_hours=stats_refresh_activity_window_hours
                )
            desired_mode = "fast" if is_active else "idle"
            if desired_mode != _stats_refresh_current_mode:
                new_seconds = stats_refresh_seconds if desired_mode == "fast" else stats_refresh_idle_seconds
                refresh_statistics.change_interval(seconds=new_seconds)
                _stats_refresh_current_mode = desired_mode
                print(
                    f"📡 SFTP poll interval switched to {desired_mode} ({new_seconds}s) -- "
                    f"{'active match context found' if is_active else 'no recent match activity'}"
                )
        except Exception as interval_err:
            print(f"⚠️ Failed to evaluate adaptive polling interval: {interval_err}")

        # Check if previous stats compilation is still running
        if refresh_statistics_task and not refresh_statistics_task.done():
            return

        # Start stats compilation as a background task
        refresh_statistics_task = asyncio.create_task(_run_stats_compilation())

    except Exception as e:
        print(f"❌ An unexpected error occurred while starting stats refresh task: {e}")
        import traceback
        traceback.print_exc()

    # Never re-raise exceptions from this task to prevent bot crashes

@tasks.loop(minutes=7)
async def auto_sync_tournaments():
    """Auto-add matches to active tournaments."""
    try:
        skip_reason = _get_heavy_task_skip_reason("auto_sync_tournaments")
        if skip_reason:
            return
        if not hasattr(bot, "db") or not bot.db:
            return
        result = await bot.db.tournaments.sync_matches_for_all_active()
        if result.get("matches_added", 0) > 0:
            print(f"🏆 Tournament auto-sync added {result['matches_added']} match(es).")
        await _record_task_outcome("Tournament auto-sync", success=True)
    except Exception as e:
        print(f"❌ Error during tournament auto-sync: {e!r}")
        await _record_task_outcome("Tournament auto-sync", success=False, error=repr(e))

@tasks.loop(minutes=1)
async def schedule_match_reminders():
    """Notify teams/admins 15 minutes before scheduled matches."""
    try:
        skip_reason = _get_heavy_task_skip_reason("schedule_match_reminders")
        if skip_reason:
            return
        if not hasattr(bot, "db") or not bot.db:
            return
        schedules = await bot.db.tournaments.get_upcoming_schedules(window_minutes=15)
        if not schedules:
            return

        team_ids = []
        for sched in schedules:
            for guild_id in (sched.get("home_guild_id"), sched.get("away_guild_id")):
                if guild_id is None:
                    continue
                try:
                    team_ids.append(int(guild_id))
                except Exception:
                    continue
        teams_by_id = await bot.db.teams.get_teams_by_ids(team_ids)

        for sched in schedules:
            schedule_id = sched.get("id")
            home_guild_id = sched.get("home_guild_id")
            away_guild_id = sched.get("away_guild_id")
            proposed_time = sched.get("proposed_time")
            tournament_name = sched.get("tournament_name") or "Tournament"
            week_label = sched.get("week_label") or "Jornada"
            server_name = sched.get("server_name") or "Server"

            ts = int(proposed_time.timestamp()) if proposed_time else None
            time_str = f"<t:{ts}:t>" if ts else "N/A"
            date_str = f"<t:{ts}:D>" if ts else "N/A"
            embed = Embed(
                title="⏰ Match Reminder",
                description=f"{tournament_name} • {week_label}\n{date_str} {time_str}\nServer: {server_name}",
                color=discord.Color.gold()
            )

            # Notify confirmed matches channel + admin role
            if CONFIRMED_SCHEDULE_CHANNEL_ID:
                ch = bot.get_channel(CONFIRMED_SCHEDULE_CHANNEL_ID)
                if ch:
                    role_ping = f"<@&{ADMIN_ROLE_ID}>" if ADMIN_ROLE_ID else "@here"
                    await ch.send(content=f"{role_ping} Match starts in 15 minutes.", embed=embed)

            # Notify both team channels
            for gid in [home_guild_id, away_guild_id]:
                if not gid:
                    continue
                try:
                    team = teams_by_id.get(int(gid))
                except Exception:
                    team = None
                if not team:
                    continue
                channel_ids = (team.get("sixes_channels") or []) + (team.get("eights_channels") or []) + (team.get("fives_channels") or [])
                for ch_id in channel_ids[:1]:
                    channel = bot.get_channel(ch_id)
                    if channel:
                        await channel.send(content="@everyone Match starts in 15 minutes.", embed=embed)

            await bot.db.tournaments.mark_schedule_reminded(schedule_id)
    except Exception as e:
        print(f"❌ Error during schedule reminders: {e!r}")

@tasks.loop(minutes=15)
async def backfill_match_links():
    """Periodically link MATCH_STATS home/away guild IDs via name matching."""
    try:
        skip_reason = _get_heavy_task_skip_reason("backfill_match_links")
        if skip_reason:
            return
        if not hasattr(bot, "db") or not bot.db:
            return
        await bot.db.matches.backfill_match_team_links(threshold=0.8)
        await _record_task_outcome("Match link backfill", success=True)
    except Exception as e:
        print(f"Error during match link backfill: {e!r}")
        await _record_task_outcome("Match link backfill", success=False, error=repr(e))


@tasks.loop(seconds=hub_sync_scheduler_poll_seconds)
async def sync_hub_incremental():
    skip_reason = _get_heavy_task_skip_reason("sync_hub_incremental")
    if skip_reason:
        return

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(hub_sync_timezone)
    active_window = _is_within_hub_sync_active_window(now_local)

    # A team/match/tournament/player write just landed and asked for a sync
    # ahead of schedule -- run on this tick regardless of interval or active
    # window, instead of making that change wait for the next scheduled poll.
    requested = has_pending_request()

    if not requested and not active_window and not hub_sync_outside_active_window:
        return

    if not requested:
        interval_seconds = _get_hub_sync_interval_seconds(now_local)
        if hub_sync_last_completed_at is not None:
            elapsed_seconds = (now_utc - hub_sync_last_completed_at).total_seconds()
            if elapsed_seconds < interval_seconds:
                return

    if requested:
        reason = pending_reason()
        clear_pending_request()
        if reason:
            print(f"Hub sync requested early: {reason}")

    if hasattr(bot, "db") and bot.db:
        try:
            catchup_result = await sync_public_matches_to_core(bot.db.pool)
            if catchup_result["matches"] > 0:
                print(
                    "Core catch-up: "
                    f"{catchup_result['matches']} match(es), "
                    f"{catchup_result['player_entries']} player entries, "
                    f"{catchup_result['events']} events."
                )
            await _record_task_outcome("Core catch-up", success=True)
        except Exception as e:
            print(f"Error during core catch-up: {e!r}")
            await _record_task_outcome("Core catch-up", success=False, error=repr(e))

    should_force_full = active_window and _should_force_full_hub_sync(now_utc)
    await _run_hub_sync_once(force_full=should_force_full)


async def _refresh_registered_team_metadata() -> dict[str, int]:
    if not hasattr(bot, "db") or not bot.db:
        return {"teams_processed": 0, "teams_updated": 0, "teams_skipped": 0}

    teams = await bot.db.teams.get_all_teams_with_details()
    processed = 0
    updated = 0
    skipped = 0

    for team in teams:
        guild_id = team.get("guild_id")
        if guild_id is None:
            continue

        processed += 1
        guild = bot.get_guild(int(guild_id))
        if guild is None:
            skipped += 1
            continue

        desired_name = str(guild.name or "").strip() or str(team.get("guild_name") or "").strip()
        desired_icon = str(guild.icon.url) if getattr(guild, "icon", None) else None
        current_name = str(team.get("guild_name") or "").strip() or None
        current_icon = str(team.get("guild_icon") or "").strip() or None

        if desired_name == current_name and desired_icon == current_icon:
            continue

        ok = await bot.db.teams.update_team_details(
            int(guild_id),
            guild_name=desired_name,
            guild_icon=desired_icon,
        )
        if ok:
            updated += 1
        else:
            skipped += 1

    return {
        "teams_processed": processed,
        "teams_updated": updated,
        "teams_skipped": skipped,
    }


async def _refresh_hub_story_models() -> bool:
    if not hasattr(bot, "db") or not bot.db:
        return False
    if hub_story_refresh_requires_active_window and not _is_within_hub_sync_active_window():
        return False
    try:
        async with bot.db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL statement_timeout = '300s'")
                await asyncio.wait_for(
                    conn.execute("SELECT hub.refresh_hub_v2_all()"),
                    timeout=320,
                )
        return True
    except asyncio.TimeoutError:
        print("Error refreshing hub V2 read models: timed out after 320 seconds")
        return False
    except Exception as e:
        print(f"Error refreshing hub V2 read models: {e!r}")
        return False


async def run_immediate_hub_refresh(
    *,
    reason: str = "manual",
    force_full: bool = False,
    refresh_story_models: bool = True,
) -> dict[str, Any]:
    if hub_immediate_refresh_requires_active_window and not _is_within_hub_sync_active_window():
        print(f"Immediate Hub refresh skipped outside active window: reason={reason}.")
        return {
            "hub_sync_result": None,
            "hub_refresh_ok": False,
        }

    hub_sync_result = await _run_hub_sync_once(force_full=force_full)
    hub_refresh_ok = False
    if refresh_story_models:
        hub_refresh_ok = await _refresh_hub_story_models()

    print(
        "Immediate Hub refresh complete: "
        f"reason={reason}, "
        f"sync={'ok' if hub_sync_result else 'skipped'}, "
        f"hub refresh={'ok' if hub_refresh_ok else 'skipped'}."
    )
    return {
        "hub_sync_result": hub_sync_result,
        "hub_refresh_ok": hub_refresh_ok,
    }


def _build_hub_auth_challenge_url(token: str) -> str:
    base = (
        os.getenv("IOSCA_HUB_API_PUBLIC_BASE_URL")
        or os.getenv("IOSCA_HUB_API_PUBLIC_URL")
        or "http://localhost:8000"
    ).strip()
    if base.endswith("/api"):
        base = base[:-4]
    return f"{base.rstrip('/')}/api/auth/challenge/{token}"


async def _deliver_pending_hub_auth_dms() -> None:
    skip_reason = _get_heavy_task_skip_reason("deliver_pending_hub_auth_dms")
    if skip_reason:
        return
    if hub_immediate_refresh_requires_active_window and not _is_within_hub_sync_active_window():
        return
    if not hasattr(bot, "db") or not bot.db:
        return

    try:
        _, _, hub_schema = _load_hub_sync_components()
    except Exception:
        return

    rows = await bot.db.pool.fetch(
        f"""
        SELECT challenge_id, target_discord_id, challenge_token_plain, provider, display_name, expires_at
        FROM "{hub_schema}".hub_auth_link_challenges
        WHERE status = 'pending'
          AND dm_sent_at IS NULL
          AND challenge_token_plain IS NOT NULL
          AND expires_at > NOW()
        ORDER BY created_at ASC
        LIMIT 10
        """
    )
    if not rows:
        return

    for row in rows:
        discord_id = str(row.get("target_discord_id") or "").strip()
        token = str(row.get("challenge_token_plain") or "").strip()
        if not discord_id or not token:
            continue

        try:
            user = bot.get_user(int(discord_id)) or await bot.fetch_user(int(discord_id))
            if user is None:
                continue

            approval_url = _build_hub_auth_challenge_url(token)
            provider = str(row.get("provider") or "account").title()
            display_name = str(row.get("display_name") or "identity").strip()
            await user.send(
                "IOSCA Hub account link request\n"
                f"A browser session asked to link `{provider}` account `{display_name}` to your hub profile.\n"
                f"If that was you, approve it here:\n{approval_url}"
            )
            await bot.db.pool.execute(
                f"""
                UPDATE "{hub_schema}".hub_auth_link_challenges
                SET dm_sent_at = NOW(),
                    challenge_token_plain = NULL
                WHERE challenge_id = $1
                """,
                int(row["challenge_id"]),
            )
        except Exception as e:
            print(f"Error delivering hub auth DM for challenge {row.get('challenge_id')}: {e!r}")


@tasks.loop(seconds=hub_auth_dm_poll_seconds)
async def deliver_hub_auth_dms():
    try:
        await _deliver_pending_hub_auth_dms()
    except Exception as e:
        print(f"Error polling hub auth DMs: {e!r}")


@tasks.loop(time=ratings_refresh_time)
async def refresh_all_player_ratings_daily():
    """Daily ratings refresh at the configured end-of-day maintenance time."""
    global ratings_refresh_running

    if ratings_refresh_running:
        return

    if not hasattr(bot, "db") or not bot.db:
        return

    try:
        recalc_cmd = importlib.import_module("ios_bot.commands.recalculate_all")
    except Exception as e:
        print(f"Error importing recalculate_all module for daily ratings refresh: {e}")
        return

    if getattr(recalc_cmd, "_recalculation_in_progress", False):
        return

    ratings_refresh_running = True
    recalc_cmd._recalculation_in_progress = True
    try:
        print("Starting daily ratings refresh...")
        team_refresh_result = await _refresh_registered_team_metadata()
        perf_result = await recalc_cmd._rebuild_match_performance(bot.db)
        role_sync_result = await recalc_cmd._regenerate_player_ratings()
        await recalc_cmd._recalculate_team_averages()
        hub_sync_result = None
        hub_refresh_ok = False
        if _is_within_hub_sync_active_window():
            hub_sync_result = await _run_hub_sync_once(force_full=True)
            hub_refresh_ok = await _refresh_hub_story_models()
        print(
            "Daily ratings refresh complete: "
            f"{perf_result.get('matches', 0)} matches / {perf_result.get('rows', 0)} rows, "
            f"{team_refresh_result.get('teams_updated', 0)} team metadata updates, "
            f"rating roles={role_sync_result.get('members_updated', 0) if isinstance(role_sync_result, dict) else 0} updated, "
            f"hub sync={'ok' if hub_sync_result else 'skipped'}, "
            f"hub refresh={'ok' if hub_refresh_ok else 'skipped'}."
        )
    except Exception as e:
        print(f"Error during daily ratings refresh: {e!r}")
    finally:
        recalc_cmd._recalculation_in_progress = False
        ratings_refresh_running = False

# Removed: check_and_announce_new_matches task - now handled by Supabase webhook

@clear_all_lineups.before_loop
async def before_clear_all_lineups():
    """Ensures the bot is ready before the task loop starts."""
    try:
        await bot.wait_until_ready()
    except Exception as e:
        print(f"Error initializing lineup clear task: {e}")

@check_inactive_players.before_loop
async def before_check_inactive_players():
    """Ensures the bot is ready before the task loop starts."""
    try:
        await bot.wait_until_ready()
    except Exception as e:
        print(f"Error initializing inactive players check task: {e}")

@refresh_statistics.before_loop
async def before_refresh_statistics():
    """Ensures the bot is ready before the task loop starts."""
    try:
        await bot.wait_until_ready()
    except Exception as e:
        print(f"Error initializing statistics refresh task: {e}")

@auto_sync_tournaments.before_loop
async def before_auto_sync_tournaments():
    """Ensures the bot is ready before tournament sync starts."""
    try:
        await bot.wait_until_ready()
    except Exception as e:
        print(f"Error initializing tournament auto-sync task: {e}")

@schedule_match_reminders.before_loop
async def before_schedule_match_reminders():
    try:
        await bot.wait_until_ready()
    except Exception as e:
        print(f"Error initializing schedule reminders: {e}")

@backfill_match_links.before_loop
async def before_backfill_match_links():
    try:
        await bot.wait_until_ready()
    except Exception as e:
        print(f"Error initializing match link backfill task: {e}")

@sync_hub_incremental.before_loop
async def before_sync_hub_incremental():
    try:
        await bot.wait_until_ready()
    except Exception as e:
        print(f"Error initializing Hub incremental sync task: {e}")

@refresh_all_player_ratings_daily.before_loop
async def before_refresh_all_player_ratings_daily():
    try:
        await bot.wait_until_ready()
    except Exception as e:
        print(f"Error initializing daily ratings refresh task: {e}")

@deliver_hub_auth_dms.before_loop
async def before_deliver_hub_auth_dms():
    try:
        await bot.wait_until_ready()
    except Exception as e:
        print(f"Error initializing hub auth DM delivery task: {e}")

# Removed: before_check_and_announce_new_matches - task no longer needed

def setup_tasks():
    """Starts all scheduled tasks."""
    try:
        clear_all_lineups.start()
    except Exception as e:
        print(f"Error starting lineup clear task: {e}")
    
    try:
        check_inactive_players.start()
    except Exception as e:
        print(f"Error starting AFK checker task: {e}")
    
    try:
        refresh_statistics.start()
    except Exception as e:
        print(f"Error starting stats refresh task: {e}")

    try:
        auto_sync_tournaments.start()
    except Exception as e:
        print(f"Error starting tournament auto-sync task: {e}")

    try:
        schedule_match_reminders.start()
    except Exception as e:
        print(f"Error starting schedule reminder task: {e}")

    try:
        backfill_match_links.start()
    except Exception as e:
        print(f"Error starting match link backfill task: {e}")

    try:
        sync_hub_incremental.start()
    except Exception as e:
        print(f"Error starting Hub sync task: {e}")

    try:
        refresh_all_player_ratings_daily.start()
    except Exception as e:
        print(f"Error starting daily ratings refresh task: {e}")

    try:
        deliver_hub_auth_dms.start()
    except Exception as e:
        print(f"Error starting hub auth DM delivery task: {e}")

    print("Task initialization completed.")

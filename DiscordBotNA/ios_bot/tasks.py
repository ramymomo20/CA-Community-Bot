from ios_bot.config import *
from ios_bot.signup_manager import (
    get_all_channel_ids_with_state,
    clear_and_refresh_channel,
    get_channel_state,
    update_state,
    refresh_lineup as sm_refresh_lineup,
)
from ios_bot.challenge_manager import active_challenges
import subprocess
import sys
import asyncio
import os

# Track background tasks to prevent overlapping executions
refresh_statistics_task = None
# Define the target time in Eastern Time (New York)
est_timezone = pytz.timezone('EST')
clear_time = time(5, 0, 0, tzinfo=est_timezone)
CHALLENGE_INACTIVITY_HOURS = int(os.getenv("CHALLENGE_INACTIVITY_HOURS", "6"))

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
            await compile_stats_main()
        except Exception as compile_error:
            print(f"❌ Stats compilation error: {compile_error}", flush=True)
            import traceback
            traceback.print_exc()
            return  # Don't continue if compilation failed        
    except Exception as e:
        print(f"❌ An unexpected error occurred during stats compilation: {e}")
        import traceback
        traceback.print_exc()

@tasks.loop(minutes=1)
async def refresh_statistics():
    """A task to refresh the player and match statistics and sync with database.
    Runs as a background task to avoid blocking other scheduled tasks.
    """
    global refresh_statistics_task
    
    try:
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
        if not hasattr(bot, "db") or not bot.db:
            return
        result = await bot.db.tournaments.sync_matches_for_all_active()
        if result.get("matches_added", 0) > 0:
            print(f"🏆 Tournament auto-sync added {result['matches_added']} match(es).")
    except Exception as e:
        print(f"❌ Error during tournament auto-sync: {e}")

@tasks.loop(minutes=1)
async def schedule_match_reminders():
    """Notify teams/admins 15 minutes before scheduled matches."""
    try:
        if not hasattr(bot, "db") or not bot.db:
            return
        schedules = await bot.db.tournaments.get_upcoming_schedules(window_minutes=15)
        if not schedules:
            return

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
                team = await bot.db.teams.get_team(gid)
                if not team:
                    continue
                channel_ids = (team.get("sixes_channels") or []) + (team.get("eights_channels") or []) + (team.get("fives_channels") or [])
                for ch_id in channel_ids[:1]:
                    channel = bot.get_channel(ch_id)
                    if channel:
                        await channel.send(content="@everyone Match starts in 15 minutes.", embed=embed)

            await bot.db.tournaments.mark_schedule_reminded(schedule_id)
    except Exception as e:
        print(f"❌ Error during schedule reminders: {e}")

@tasks.loop(minutes=15)
async def backfill_match_links():
    """Periodically link MATCH_STATS home/away guild IDs via name matching."""
    try:
        if not hasattr(bot, "db") or not bot.db:
            return
        await bot.db.matches.backfill_match_team_links(threshold=0.8)
    except Exception as e:
        print(f"Error during match link backfill: {e}")


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

    print("Task initialization completed.")

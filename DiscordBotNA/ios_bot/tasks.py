from ios_bot.config import *
from ios_bot.signup_manager import get_all_channel_ids_with_state, clear_and_refresh_channel, get_channel_state, update_state, refresh_lineup as sm_refresh_lineup
from ios_bot.challenge_manager import active_challenges
from ios_bot.database_manager import get_team_by_name, execute_query, sync_databases, is_secondary_db_available, get_all_servers_with_details
import subprocess
import sys
import csv
import asyncio

# Define the target time in Eastern Time (New York)
est_timezone = pytz.timezone('EST')
clear_time = time(5, 0, 0, tzinfo=est_timezone)

# --- Task State ---
# This set will keep track of the datetimes of matches that have already been announced
announced_match_datetimes = set()

@tasks.loop(time=clear_time)
async def clear_all_lineups():
    """A scheduled task to clear all matchmaking lineups daily at 25 AM Eastern Time."""
    try:
        print(f"[{datetime.now(est_timezone).strftime('%Y-%m-%d %H:%M:%S %Z')}] Running daily lineup clear...")
        
        all_channel_ids = get_all_channel_ids_with_state()
        if not all_channel_ids:
            print("No active channel states found to clear.")
        else:
            cleared_count = 0
            for channel_id in all_channel_ids:
                try:
                    channel = bot.get_channel(channel_id)
                    if channel and isinstance(channel, discord.TextChannel):
                        await clear_and_refresh_channel(channel)
                        cleared_count += 1
                        await asyncio.sleep(1) # Avoid rate-limiting
                except Exception as e:
                    print(f"Error clearing lineup for channel {channel_id}: {e}")

        print(f"Daily lineup clear finished. Cleared {cleared_count} channels.")
        
        # Clean up stale challenges during the daily clear
        print(f"[{datetime.now(est_timezone).strftime('%Y-%m-%d %H:%M:%S %Z')}] Cleaning up stale challenges...")
        await cleanup_stale_challenges()
        
    except Exception as e:
        print(f"Critical error in clear_all_lineups task: {e}")
        # Don't re-raise - let the bot continue running

async def cleanup_stale_challenges():
    """Remove challenges that are older than 24 hours."""
    try:
        from datetime import timedelta
        
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24)
        stale_challenges = []
        
        for challenge_id, challenge in list(active_challenges.items()):
            challenge_time = challenge.get('challenge_time')
            if challenge_time and challenge_time < cutoff_time:
                stale_challenges.append(challenge_id)
        
        if stale_challenges:
            for challenge_id in stale_challenges:
                try:
                    del active_challenges[challenge_id]
                    print(f"  - Removed stale challenge: {challenge_id}")
                except KeyError:
                    pass  # Challenge already removed
            
            print(f"Cleaned up {len(stale_challenges)} stale challenges.")
        else:
            print("No stale challenges found.")
            
    except Exception as e:
        print(f"Error cleaning up stale challenges: {e}")

@tasks.loop(minutes=15)
async def check_inactive_players():
    """A task to automatically remove inactive players from lineups."""
    try:
        print(f"[{datetime.now(est_timezone).strftime('%Y-%m-%d %H:%M:%S %Z')}] Checking for inactive players...")
        inactive_threshold = timedelta(minutes=120)
        now = datetime.now(timezone.utc)
        
        # Get a list of all channels with active lineups
        all_channel_ids = get_all_channel_ids_with_state()
        if not all_channel_ids:
            print("  - No active channels with lineups found. Skipping.")
            return

        print(f"  - Found {len(all_channel_ids)} channel(s) with active lineups: {all_channel_ids}")

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
                            print(f"  - Channel {channel_id} is in an accepted challenge. Skipping AFK check.")
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
                                    color=0xFF0000
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

@tasks.loop(minutes=15)  # Changed from 5 minutes to 15 to reduce load and prevent startup issues
async def refresh_statistics():
    """A task to refresh the player and match statistics and sync with database."""
    try:
        print(f"[{datetime.now(est_timezone).strftime('%Y-%m-%d %H:%M:%S %Z')}] Running scheduled stats refresh...")
        
        # First, run the compilation script to update CSV files
        script_path = os.path.join(os.path.dirname(__file__), 'ratings', 'compile_stats.py')
        python_executable = sys.executable  # Use the same python that's running the bot

        proc = await asyncio.to_thread(
            subprocess.run,
            [python_executable, script_path],
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout to prevent hanging
        )
        
        if proc.returncode == 0:
            print("✅ Stats compilation script finished successfully.")
            if proc.stdout:
                print("Script output:\n", proc.stdout)
                
            # Import CSV data to database using new simplified method (with timeout protection)
            print("🚀 Importing ALL CSV data to database (0 for unregistered teams)...")
            try:
                # Add timeout to prevent hanging during scheduled sync
                from ios_bot.database_manager import import_all_csv_data_with_nulls
                success = await asyncio.wait_for(import_all_csv_data_with_nulls(), timeout=300.0)  # 5 minute timeout
                if success:
                    print("✅ Fast database import completed successfully.")
                    
                    # Print simple health status
                    try:
                        match_count = await execute_query("SELECT COUNT(*) as count FROM MATCH_STATS", fetchone=True)
                        player_count = await execute_query("SELECT COUNT(*) as count FROM PLAYER_MATCH_DATA", fetchone=True)
                        mapping_count = await execute_query("SELECT COUNT(*) as count FROM TEAM_NAME_MAPPINGS", fetchone=True)
                        
                        print(f"📊 Database Status:")
                        print(f"  - Matches: {match_count['count'] if match_count else 0}")
                        print(f"  - Player Records: {player_count['count'] if player_count else 0}")
                        print(f"  - Team Mappings: {mapping_count['count'] if mapping_count else 0}")
                    except Exception as e:
                        print(f"⚠️ Could not get database statistics: {e}")
                    
                    # Generate player ratings after successful database update
                    try:
                        print("🌟 Generating player ratings...")
                        from ios_bot.ratings.Rating_Generator.weekly_ratings import generate_weekly_player_ratings
                        await generate_weekly_player_ratings()
                    except Exception as rating_error:
                        print(f"❌ Error during ratings generation: {rating_error}")
                        # Don't fail the whole task if ratings fail
                else:
                    print("❌ Fast database import failed.")
            except asyncio.TimeoutError:
                print("❌ Fast database import timed out after 5 minutes during scheduled task.")
            except Exception as e:
                print(f"❌ Error during fast database import: {e}")
        else:
            print(f"❌ Stats compilation script finished with return code: {proc.returncode}")
            if proc.stderr:
                print("Script errors:\n", proc.stderr)

    except subprocess.TimeoutExpired:
        print("❌ Stats compilation script timed out after 5 minutes")
    except FileNotFoundError:
        print(f"❌ Error: The script at {script_path} was not found.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Error executing stats compilation script:")
        print(f"Return Code: {e.returncode}")
        print(f"Output:\n{e.stdout}")
        print(f"Error Output:\n{e.stderr}")
    except Exception as e:
        print(f"❌ An unexpected error occurred while running the stats refresh task: {e}")
    
    # Never re-raise exceptions from this task to prevent bot crashes

@tasks.loop(minutes=1)  # Changed from 30 seconds to 1 minute to reduce Discord API load
async def check_and_announce_new_matches():
    """Checks for new match results and announces them in the correct channels."""
    try:
        summaries_path = os.path.join(os.path.dirname(__file__), 'ratings', 'match_summaries.csv')
        if not os.path.exists(summaries_path):
            return # No summaries file yet, nothing to do.

        try:
            with open(summaries_path, 'r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                all_matches = list(reader)
        except (IOError, StopIteration):
            return # File is empty or being written to, try again next minute

        for match in all_matches:
            try:
                match_dt = match.get('datetime')
                if not match_dt or match_dt in announced_match_datetimes:
                    continue

                # Found a new match, process it
                print(f"[Match Announcer] Found new match to announce: {match['home_team']} vs {match['away_team']}")
                home_team_name = match['home_team']
                away_team_name = match['away_team']
                
                # Determine the target channels for the announcement
                target_channel_ids = set()

                # Always add the central fixtures channel
                if FIXTURES_CHANNEL_ID:
                    target_channel_ids.add(FIXTURES_CHANNEL_ID)
                    
                # Create the embed
                score = match['scoreline'].replace('-', ' - ')
                embed = discord.Embed(
                    title="Match Concluded",
                    description=f"**FULL TIME: {home_team_name} {score} {away_team_name}**\n\nThe match overview is now available to view: use `/view_match` to see the full stats.",
                    color=discord.Color.blue()
                )
                timestamp = datetime.now(timezone.utc).strftime("%I:%M %p")
                embed.set_footer(text=f"Match Concluded • {timestamp}")

                # Post the embed to all target channels
                for channel_id in target_channel_ids:
                    try:
                        channel = bot.get_channel(channel_id)
                        if channel:
                            await channel.send(embed=embed)
                    except Exception as e:
                        print(f"[Match Announcer] Failed to send announcement to channel {channel_id}: {e}")
                
                # Mark this match as announced
                announced_match_datetimes.add(match_dt)
                
                # The database sync happens in refresh_statistics task, so no need to duplicate here
                
            except Exception as e:
                print(f"[Match Announcer] Error processing match: {e}")
    except Exception as e:
        print(f"Critical error in check_and_announce_new_matches task: {e}")
        # Don't re-raise - let the bot continue running

@clear_all_lineups.before_loop
async def before_clear_all_lineups():
    """Ensures the bot is ready before the task loop starts."""
    try:
        await bot.wait_until_ready()
        print("Scheduled lineup clear task is ready.")
    except Exception as e:
        print(f"Error initializing lineup clear task: {e}")

@check_inactive_players.before_loop
async def before_check_inactive_players():
    """Ensures the bot is ready before the task loop starts."""
    try:
        await bot.wait_until_ready()
        print("Inactive players check task is ready.")
    except Exception as e:
        print(f"Error initializing inactive players check task: {e}")

@refresh_statistics.before_loop
async def before_refresh_statistics():
    """Ensures the bot is ready before the task loop starts."""
    try:
        await bot.wait_until_ready()
        print("Statistics refresh task is ready.")
    except Exception as e:
        print(f"Error initializing statistics refresh task: {e}")

@check_and_announce_new_matches.before_loop
async def before_check_and_announce_new_matches():
    """Ensures the bot is ready and pre-fills the announced matches set."""
    try:
        await bot.wait_until_ready()
        print("Match announcer task is ready. Pre-filling history...")
        # Pre-fill the set of announced matches to avoid re-posting on startup
        summaries_path = os.path.join(os.path.dirname(__file__), 'ratings', 'match_summaries.csv')
        if os.path.exists(summaries_path):
            try:
                with open(summaries_path, 'r', newline='', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if row.get('datetime'):
                            announced_match_datetimes.add(row['datetime'])
                print(f"Pre-filled {len(announced_match_datetimes)} previously recorded matches.")
            except Exception as e:
                print(f"Error pre-filling match history: {e}")
    except Exception as e:
        print(f"Error initializing match announcer task: {e}")

def setup_tasks():
    """Starts all scheduled tasks."""
    try:
        clear_all_lineups.start()
        print("Daily lineup clear task started.")
    except Exception as e:
        print(f"Error starting lineup clear task: {e}")
    
    try:
        check_inactive_players.start()
        print("AFK checker task started.")
    except Exception as e:
        print(f"Error starting AFK checker task: {e}")
    
    try:
        refresh_statistics.start()
        print("Stats refresh task started.")
    except Exception as e:
        print(f"Error starting stats refresh task: {e}")
    
    try:
        check_and_announce_new_matches.start()
        print("Match announcer task started.")
    except Exception as e:
        print(f"Error starting match announcer task: {e}")
    
    try:
        scheduled_database_sync.start()
        print("Scheduled database sync task started (4 AM EST daily).")
    except Exception as e:
        print(f"Error starting scheduled database sync task: {e}")
    
    print("Task initialization completed.")

@tasks.loop(time=time(hour=4, minute=0, tzinfo=est_timezone))  # 4:00 AM EST
async def scheduled_database_sync():
    """Automatically sync databases at 4 AM EST every day."""
    try:
        print(f"[{datetime.now(est_timezone).strftime('%Y-%m-%d %H:%M:%S %Z')}] Starting scheduled database synchronization...")
        
        # Check if secondary database is available
        if not is_secondary_db_available():
            print("⚠️ Secondary database not configured. Skipping scheduled sync.")
            return
        
        # Perform synchronization from primary to secondary
        sync_result = await sync_databases(
            source_db='primary',
            target_db='secondary',
            sync_structure=True,
            sync_data=True
        )
        
        if sync_result['success']:
            duration = sync_result['end_time'] - sync_result['start_time']
            print(f"✅ Scheduled database sync completed successfully!")
            print(f"📊 Synced {sync_result['tables_synced']}/{sync_result['total_tables']} tables in {duration.total_seconds():.2f} seconds")
            
            if sync_result['errors']:
                print(f"⚠️ {len(sync_result['errors'])} errors occurred during sync:")
                for error in sync_result['errors'][:5]:  # Show first 5 errors
                    print(f"  - {error}")
        else:
            print(f"❌ Scheduled database sync failed!")
            if sync_result['errors']:
                print("Errors:")
                for error in sync_result['errors'][:5]:  # Show first 5 errors
                    print(f"  - {error}")
        
    except Exception as e:
        print(f"❌ Error during scheduled database sync: {e}")
        # Don't re-raise - let the bot continue running
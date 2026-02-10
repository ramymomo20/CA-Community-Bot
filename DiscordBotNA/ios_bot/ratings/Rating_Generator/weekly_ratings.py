"""
Weekly Player Ratings System
Handles automatic generation of player ratings and team average calculations.
"""

import asyncio
import subprocess
import sys
import os
from datetime import datetime
import pytz

# Import bot instance for database access
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from ios_bot.config import bot

async def update_team_average_ratings():
    """Calculate and update average ratings for all teams."""
    try:
        print("🔄 Calculating team average ratings...")
        
        # Get player ratings from database
        ratings_query = "SELECT steam_id, rating FROM IOSCA_PLAYERS WHERE rating IS NOT NULL"
        ratings_records = await bot.db.pool.fetch(ratings_query)
        
        if not ratings_records:
            print("❌ No player ratings found in database. Cannot calculate team averages.")
            return
        
        # Convert to dict for easy lookup
        ratings_dict = {r['steam_id']: r['rating'] for r in ratings_records}
        
        # Get all teams from database
        teams = await bot.db.teams.get_all_teams()
        
        for team in teams:
            try:
                team_players = team.get('players', [])
                if not team_players:
                    continue
                
                # Get Steam IDs for this team's players
                team_steam_ids = []
                for player in team_players:
                    if isinstance(player, dict) and player.get('steam_id'):
                        team_steam_ids.append(player['steam_id'])
                
                if not team_steam_ids:
                    continue
                
                # Calculate average rating for this team
                team_ratings = [ratings_dict[sid] for sid in team_steam_ids if sid in ratings_dict]
                
                if len(team_ratings) > 0:
                    avg_rating = sum(team_ratings) / len(team_ratings)
                    
                    # Update team in database with average rating
                    await bot.db.pool.execute(
                        "UPDATE IOSCA_TEAMS SET average_rating = $1 WHERE guild_id = $2",
                        round(avg_rating, 2), team['guild_id']
                    )
                    
                    print(f"  - {team['guild_name']}: {len(team_ratings)} players, avg rating: {avg_rating:.2f}")
                else:
                    print(f"  - {team['guild_name']}: No players with ratings found")
                    
            except Exception as e:
                print(f"  - Error calculating average for {team.get('guild_name', 'Unknown')}: {e}")
        
        print("✅ Team average ratings updated successfully.")
        
    except Exception as e:
        print(f"❌ Error updating team average ratings: {e}")

async def generate_weekly_player_ratings():
    """Generate weekly player ratings and update team averages."""
    try:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Running weekly player ratings generation...")
        
        # First, run the ratings generation script
        script_path = os.path.join(os.path.dirname(__file__), 'generate_ratings.py')
        python_executable = sys.executable

        proc = await asyncio.to_thread(
            subprocess.run,
            [python_executable, script_path],
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout
        )
        
        if proc.returncode == 0:
            print("✅ Weekly player ratings generation completed successfully.")
            if proc.stdout:
                print("Script output:\n", proc.stdout)
            
            # Now calculate and update team average ratings
            await update_team_average_ratings()
            
        else:
            print(f"❌ Weekly ratings generation failed with return code: {proc.returncode}")
            if proc.stderr:
                print("Script errors:\n", proc.stderr)

    except subprocess.TimeoutExpired:
        print("❌ Weekly ratings generation timed out after 10 minutes")
    except FileNotFoundError:
        print(f"❌ Error: The ratings generation script at {script_path} was not found.")
    except Exception as e:
        print(f"❌ An unexpected error occurred during weekly ratings generation: {e}")

async def initialize_weekly_ratings():
    """Initialize the weekly ratings system. Column already exists in database."""
    print("✅ Weekly ratings system initialized (average_rating column already exists)")
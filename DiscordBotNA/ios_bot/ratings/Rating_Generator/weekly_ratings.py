"""
Weekly Player Ratings System
Handles automatic generation of player ratings and team average calculations.
"""

import asyncio
import subprocess
import sys
import os
import pandas as pd
from datetime import datetime
import pytz

# Import database functions
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from database_manager import execute_query, execute_query_optimized, get_all_teams_with_details, database

async def add_average_rating_column_to_teams():
    """Add average_rating column to IOSCA_TEAMS table if it doesn't exist."""
    print("🔧 Adding average_rating column to IOSCA_TEAMS table...")
    
    try:
        # Check if column already exists
        check_query = """
        SELECT COUNT(*) as count 
        FROM information_schema.columns 
        WHERE table_schema = %s 
        AND table_name = 'IOSCA_TEAMS' 
        AND column_name = 'average_rating'
        """
        
        result = await execute_query_optimized(
            check_query, 
            (database,), 
            fetchone=True
        )
        
        if result and result['count'] == 0:
            # Column doesn't exist, add it
            alter_query = """
            ALTER TABLE IOSCA_TEAMS 
            ADD COLUMN average_rating DECIMAL(4,2) DEFAULT NULL
            """
            
            await execute_query(alter_query, commit=True)
            print("✅ Added average_rating column to IOSCA_TEAMS table")
        else:
            print("ℹ️ average_rating column already exists in IOSCA_TEAMS table")
            
    except Exception as e:
        print(f"❌ Error adding average_rating column: {e}")

async def update_team_average_ratings():
    """Calculate and update average ratings for all teams."""
    try:
        print("🔄 Calculating team average ratings...")
        
        # Read the generated ratings file
        ratings_path = os.path.join(os.path.dirname(__file__), 'final_ratings.csv')
        if not os.path.exists(ratings_path):
            print("❌ Ratings file not found. Cannot calculate team averages.")
            return
        
        ratings_df = pd.read_csv(ratings_path)
        
        # Get all teams from database
        teams = await get_all_teams_with_details()
        
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
                team_ratings = ratings_df[ratings_df['steamid'].isin(team_steam_ids)]['finalRating']
                
                if len(team_ratings) > 0:
                    avg_rating = team_ratings.mean()
                    
                    # Update team in database with average rating
                    await execute_query(
                        "UPDATE IOSCA_TEAMS SET average_rating = %s WHERE guild_id = %s",
                        (round(avg_rating, 2), team['guild_id']),
                        commit=True
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

# Initialize the database column when this module is imported
async def initialize_weekly_ratings():
    """Initialize the weekly ratings system by ensuring the database column exists."""
    await add_average_rating_column_to_teams() 
import pandas as pd
import numpy as np
import os
import sys
import asyncio
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

# Import database connection
from ios_bot.db import Database
from dotenv import load_dotenv

# Load environment variables
env_path = project_root / '.env'
load_dotenv(env_path)

# Initialize database connection
SUPABASE_DB_URL = os.getenv('SUPABASE_DB_URL')
db = None

async def init_db():
    """Initialize database connection"""
    global db
    if db is None:
        db = Database(SUPABASE_DB_URL)
        await db.initialize()
    return db

def zscore(s: pd.Series) -> pd.Series:
    """Calculate z-score normalization"""
    μ, σ = s.mean(), s.std(ddof=0)
    return (s - μ) / σ if σ > 0 else pd.Series(0, index=s.index)

def map_position(cat: str) -> str:
    """Map position to general category"""
    p = (cat or "").upper()
    if p in ('LW','CF','RW'):        return 'ATK'
    if p in ('CM'): return 'MID'
    if p in ('LB','CB','RB'):  return 'DEF'
    if 'GK' in p:                         return 'GK'
    return 'FLX'

def sigmoid(x):
    """Sigmoid function to map z-scores to [0,1]"""
    return 1 / (1 + np.exp(-x))

async def generate_player_ratings():
    """
    Generate player ratings directly from PostgreSQL database
    """
    
    # Initialize database connection
    await init_db()
    
    print(f"📖 Fetching player stats from database...")
    
    # Query to get all player match data with team info
    query = """
    SELECT 
        pmd.steam_id as "Steam ID",
        p.discord_name as "Name",
        pmd.position as "Position",
        COALESCE(ms.match_id, ms.id::text, pmd.match_id::text) as match_id,
        ms.datetime,
        t.guild_name as "Team Name",
        pmd.red_cards as "redCards",
        pmd.yellow_cards as "yellowCards",
        pmd.fouls,
        pmd.tackles as "slidingTackles",
        pmd.sliding_tackles_completed as "slidingTacklesCompleted",
        pmd.goals_conceded as "goalsConceded",
        pmd.shots,
        pmd.shots_on_goal as "shotsOnGoal",
        pmd.passes_completed as "passesCompleted",
        pmd.interceptions,
        pmd.offsides,
        pmd.goals,
        pmd.own_goals as "ownGoals",
        pmd.assists,
        pmd.passes_attempted as "passes",
        pmd.keeper_saves as "keeperSaves",
        pmd.distance_covered as "distanceCovered",
        pmd.keeper_saves_caught as "keeperSavesCaught",
        pmd.chances_created as "chancesCreated",
        pmd.second_assists as "secondAssists",
        pmd.key_passes as "keyPasses"
    FROM PLAYER_MATCH_DATA pmd
    LEFT JOIN MATCH_STATS ms ON pmd.match_id::text = COALESCE(ms.match_id::text, ms.id::text)
    LEFT JOIN IOSCA_PLAYERS p ON pmd.steam_id = p.steam_id
    LEFT JOIN IOSCA_TEAMS t ON pmd.guild_id = t.guild_id
    ORDER BY ms.datetime DESC NULLS LAST
    """
    
    try:
        rows = await db.pool.fetch(query)
        if not rows:
            print("❌ No player match data found in database!")
            return False
        
        # Convert to DataFrame
        df = pd.DataFrame([dict(row) for row in rows])
        print(f"� Loaded {len(df)} player match records from database")
    except Exception as e:
        print(f"❌ Error fetching data from database: {e}")
        return False
    
    # Filter out rows where Team Name is 'N/A' (players not in teams)
    df = df[df['Team Name'] != 'N/A'].copy()
    
    print(f"📊 Processing {len(df)} valid player records...")
    
    # No time-based weighting - calculate from ALL matches equally
    # This allows ratings to be calculated from scratch and updated incrementally
    df['weight'] = 1.0
    
    # Define stat columns to aggregate (only columns that exist in schema)
    stat_columns = [
        'redCards', 'yellowCards', 'fouls', 'foulsSuffered',
        'slidingTackles', 'slidingTacklesCompleted', 'goalsConceded',
        'shots', 'shotsOnGoal', 'passesCompleted', 'interceptions',
        'offsides', 'goals', 'ownGoals', 'assists', 'passes',
        'freeKicks', 'penalties', 'corners', 'throwIns',
        'keeperSaves', 'goalKicks', 'possession', 'distanceCovered',
        'keeperSavesCaught', 'keyPasses', 'chancesCreated', 'secondAssists'
    ]
    
    # Ensure all stat columns are numeric
    for col in stat_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    
    # Simple aggregation - sum all stats from all matches
    # No time-based weighting for cumulative calculation
    
    # Group by Steam ID and aggregate - sum all stats from all matches
    print("🔄 Aggregating player statistics from ALL matches...")
    agg_dict = {stat: 'sum' for stat in stat_columns if stat in df.columns}
    agg_dict.update({
        'Name': 'first',
        'Position': lambda s: s.mode().iloc[0] if not s.mode().empty else "",
        'match_id': 'nunique'  # Count unique matches
    })
    
    grouped = (
        df
        .groupby('Steam ID', as_index=False)
        .agg(agg_dict)
    )
    
    # Rename columns for consistency
    grouped = grouped.rename(columns={
        'Steam ID': 'steamid',
        'Name': 'player',
        'Position': 'position',
        'match_id': 'appearances'
    })
    
    # Ensure appearances is at least 1
    grouped['appearances'] = grouped['appearances'].clip(lower=1)
    
    # Apply position mapping
    grouped["generalPosition"] = grouped["position"].apply(map_position)
    
    print("⚽ Calculating performance metrics...")
    
    # Build composite metrics
    grouped["attackDeeds"] = (
          grouped["assists"]
        + grouped["secondAssists"]
        + grouped["goals"]
    )
    grouped["attackMistakes"] = grouped["shots"] - grouped["shotsOnGoal"] + grouped["offsides"]
    
    grouped["defenseDeeds"] = (
          grouped["interceptions"]
        + grouped["slidingTacklesCompleted"] * grouped["appearances"]
    )
    grouped["defenseMistakes"] = (
          grouped["fouls"]
        + grouped["ownGoals"]
        + grouped["goalsConceded"]
        + grouped["redCards"]
        + grouped["yellowCards"]
    )
    
    grouped["keeperDeeds"] = grouped["keeperSaves"] + grouped["keeperSavesCaught"]
    grouped["keeperMistakes"] = grouped["goalsConceded"]
    
    grouped["assister"] = grouped["assists"] + grouped["secondAssists"]
    grouped["passer"] = grouped["passesCompleted"]
    grouped["passerMistakes"] = grouped["passes"] - grouped["passesCompleted"]
    
    grouped["lapses"] = grouped["fouls"] + grouped["redCards"] + grouped["yellowCards"]
    
    # Z-score normalize composite metrics
    print("📊 Normalizing performance metrics...")
    comps = [
        "attackDeeds", "attackMistakes",
        "defenseDeeds", "defenseMistakes",
        "keeperDeeds", "keeperMistakes",
        "assister", "passer", "passerMistakes", "lapses"
    ]
    norm = pd.DataFrame({f: zscore(grouped[f]) for f in comps})
    
    # Calculate positional raw scores
    print("🎯 Calculating positional ratings...")
    
    raw_atk = (
        0.65*norm["attackDeeds"] - 0.30*norm["attackMistakes"]
      + 0.30*norm["assister"] + 0.25*norm["passer"]
      + 0.15*norm["defenseDeeds"] - 0.10*norm["defenseMistakes"]
      - 0.25*norm["lapses"] - 0.30*norm["passerMistakes"]
    )
    raw_mid = (
        0.65*norm["assister"] + 0.30*norm["passer"]
      + 0.25*norm["defenseDeeds"] - 0.30*norm["defenseMistakes"]
      + 0.15*norm["attackDeeds"] - 0.20*norm["lapses"] - 0.40*norm["passerMistakes"]
    )
    raw_def = (
        0.65*norm["defenseDeeds"] - 0.40*norm["defenseMistakes"]
      + 0.30*norm["passer"] + 0.30*norm["assister"]
      + 0.25*norm["attackDeeds"] - 0.20*norm["passerMistakes"]
    )
    raw_gk = (
        0.65*norm["keeperDeeds"] - 0.30*norm["keeperMistakes"]
      + 0.35*norm["passer"] + 0.35*norm["assister"] - 0.15*norm["lapses"]
      - 0.50*norm["passerMistakes"]
    )
    
    grouped["raw_score"] = np.select(
        [
          grouped["generalPosition"] == "ATK",
          grouped["generalPosition"] == "MID",
          grouped["generalPosition"] == "DEF",
          grouped["generalPosition"] == "GK",
        ],
        [raw_atk, raw_mid, raw_def, raw_gk],
        default=0.0
    )
    
    # Apply penalties and peer comparisons
    print("⚖️ Applying penalties and adjustments...")
    
    γ = 0.5
    pos_factor = {"ATK": 1.5, "MID": 1.5, "DEF": 1.0, "GK": 1.0}
    pen_base = 1 - np.exp(-γ * grouped["lapses"])
    grouped["mistakePenalty"] = pen_base * grouped["generalPosition"].map(pos_factor)
    
    pos_mean = grouped.groupby("generalPosition")["raw_score"].transform("mean")
    pos_std = grouped.groupby("generalPosition")["raw_score"].transform("std").replace(0, 1)
    grouped["peerDiffZ"] = (grouped["raw_score"] - pos_mean) / pos_std
    
    α, β = 0.30, 0.10
    grouped["adjusted_score"] = (
         grouped["raw_score"]
       + α * grouped["peerDiffZ"]
       - β * grouped["mistakePenalty"]
    )
    
    grouped["adj_z"] = zscore(grouped["adjusted_score"])
    grouped["adj_z"] = grouped["adj_z"].clip(-2, 2)
    
    grouped["adj_norm"] = sigmoid(grouped["adj_z"])
    
    # Calculate reliability weight based on appearances
    rel = grouped["appearances"].clip(lower=1) / (grouped["appearances"] + 2)
    
    # Final rating calculation
    grouped["finalRating"] = (
        5 + 4.9 * rel * grouped["adj_norm"]
    ).round(2)
    
    # Prepare final output
    output_columns = [
        "steamid", "player", "position", "appearances", "finalRating"
    ]
    
    final_output = grouped[output_columns].copy()
    
    # Save results to CSV
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_file = os.path.join(script_dir, "final_ratings.csv")
    final_output.to_csv(output_file, index=False)
    
    print(f"✅ Successfully generated ratings for {len(final_output)} players")
    print(f"📊 Saved to: {output_file}")
    
    # Update ratings in database
    print("💾 Updating ratings in database...")
    try:
        for _, row in final_output.iterrows():
            await db.pool.execute(
                """
                UPDATE IOSCA_PLAYERS 
                SET rating = $1 
                WHERE steam_id = $2
                """,
                float(row['finalRating']),
                str(row['steamid'])
            )
        print(f"✅ Updated {len(final_output)} player ratings in database")
    except Exception as e:
        print(f"⚠️ Error updating database: {e}")
    return True

async def main():
    """
    Main function to generate player ratings
    """
    print("🚀 Starting Player Rating Generation")
    
    if not await generate_player_ratings():
        print("❌ Failed to generate ratings")
        return
    
    print("🎉 Player rating generation completed successfully!")

if __name__ == "__main__":
    asyncio.run(main())
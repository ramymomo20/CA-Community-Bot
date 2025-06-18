import pandas as pd
import numpy as np
import os
from datetime import datetime

def zscore(s: pd.Series) -> pd.Series:
    """Calculate z-score normalization"""
    μ, σ = s.mean(), s.std(ddof=0)
    return (s - μ) / σ if σ > 0 else pd.Series(0, index=s.index)

def map_position(cat: str) -> str:
    """Map position to general category"""
    p = (cat or "").upper()
    if p in ('LW','CF','RW','ST'):        return 'ATK'
    if p in ('LM','CM','RM','CAM','CDM'): return 'MID'
    if p in ('LB','CB','RB','LWB','RWB'):  return 'DEF'
    if 'GK' in p:                         return 'GK'
    return 'FLX'

def sigmoid(x):
    """Sigmoid function to map z-scores to [0,1]"""
    return 1 / (1 + np.exp(-x))

def generate_player_ratings():
    """
    Generate player ratings directly from player_stats.csv
    """
    
    # Read the original player_stats.csv
    input_file = "player_stats.csv"
    if not os.path.exists(input_file):
        print(f"❌ Error: {input_file} not found!")
        return False
        
    print(f"📖 Reading {input_file}...")
    df = pd.read_csv(input_file)
    
    # Filter out rows where Team Name is 'N/A' (players not in teams)
    df = df[df['Team Name'] != 'N/A'].copy()
    
    print(f"📊 Processing {len(df)} valid player records...")
    
    # Parse match dates
    df['matchDate'] = pd.to_datetime(df['datetime'], errors='coerce')
    df['matchDate'] = df['matchDate'].fillna(pd.Timestamp.today())
    
    # Compute age in days for weighting
    max_date = df['matchDate'].max()
    df['age_days'] = (max_date - df['matchDate']).dt.total_seconds() / 86400
    
    # Apply time-based weights
    span = max_date - df['matchDate'].min()
    if span < pd.Timedelta(days=7):
        df['weight'] = 1.0
    else:
        λ = 0.1
        df['weight'] = np.exp(-λ * df['age_days'])
    
    # Define stat columns to aggregate
    stat_columns = [
        'redCards', 'yellowCards', 'fouls', 'foulsSuffered',
        'slidingTackles', 'slidingTacklesCompleted', 'goalsConceded',
        'shots', 'shotsOnGoal', 'passesCompleted', 'interceptions',
        'offsides', 'goals', 'ownGoals', 'assists', 'passes',
        'freeKicks', 'penalties', 'corners', 'throwIns',
        'keeperSaves', 'goalKicks', 'possession', 'distanceCovered',
        'keeperSavesCaught', 'chancesCreated', 'secondAssists', 'keyPasses'
    ]
    
    # Ensure all stat columns are numeric
    for col in stat_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    
    # Prepare aggregation dictionary
    agg_dict = {}
    
    # Weighted sum for raw stats
    for stat in stat_columns:
        if stat in df.columns:
            agg_dict[stat] = lambda s, stat=stat: (s * df.loc[s.index, 'weight']).sum()
    
    # Special aggregations
    agg_dict.update({
        "avgPossession": lambda s: np.average(s, weights=df.loc[s.index, 'weight']),
        "passCompletionPct": lambda s: np.average(
            (df.loc[s.index, 'passesCompleted'] / df.loc[s.index, 'passes'].replace(0, 1)) * 100,
            weights=df.loc[s.index, 'weight']
        ),
        "appearances": lambda s: df.loc[s.index, 'match_id'].nunique(),
        "position": lambda s: s.mode().iloc[0] if not s.mode().empty else "",
        "player": "first",
    })
    
    # Group by Steam ID and aggregate
    print("🔄 Aggregating player statistics...")
    grouped = (
        df
        .groupby('Steam ID', as_index=False)
        .agg({
            **{stat: agg_dict.get(stat, 'sum') for stat in stat_columns if stat in df.columns},
            'Name': 'first',
            'Position': lambda s: s.mode().iloc[0] if not s.mode().empty else "",
            'match_id': 'nunique'  # This will be renamed to appearances
        })
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
        + grouped["shotsOnGoal"]
        + grouped["chancesCreated"]
    )
    grouped["attackMistakes"] = grouped["shots"] - grouped["shotsOnGoal"]
    
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
      + 0.20*norm["assister"] + 0.20*norm["passer"]
      + 0.05*norm["defenseDeeds"] - 0.15*norm["defenseMistakes"]
      + 0.05*norm["lapses"] - 0.20*norm["passerMistakes"]
    )
    raw_mid = (
        0.65*norm["assister"] + 0.30*norm["passer"]
      + 0.20*norm["defenseDeeds"] - 0.20*norm["defenseMistakes"]
      + 0.05*norm["attackDeeds"] - 0.15*norm["lapses"] - 0.40*norm["passerMistakes"]
    )
    raw_def = (
        0.65*norm["defenseDeeds"] - 0.40*norm["defenseMistakes"]
      + 0.25*norm["passer"] + 0.20*norm["assister"]
      + 0.20*norm["attackDeeds"] - 0.30*norm["passerMistakes"]
    )
    raw_gk = (
        0.60*norm["keeperDeeds"] - 0.35*norm["keeperMistakes"]
      + 0.25*norm["passer"] + 0.20*norm["assister"] + 0.15*norm["lapses"]
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
    pos_factor = {"ATK": 0.8, "MID": 1.0, "DEF": 1.2, "GK": 1.5}
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
    
    # Save results
    # Save in the same directory as this script (Rating Generator folder)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_file = os.path.join(script_dir, "final_ratings.csv")
    final_output.to_csv(output_file, index=False)
    
    print(f"✅ Successfully generated ratings for {len(final_output)} players")
    print(f"📊 Saved to: {output_file}")
    
    # Show statistics
    print("\n📈 Rating Distribution:")
    print(f"   Average Rating: {final_output['finalRating'].mean():.2f}")
    print(f"   Highest Rating: {final_output['finalRating'].max():.2f}")
    print(f"   Lowest Rating: {final_output['finalRating'].min():.2f}")
    
    print("\n🎯 By Position:")
    for pos in sorted(final_output['position'].unique()):
        pos_data = final_output[final_output['position'] == pos]
        print(f"   {pos}: {len(pos_data)} players, avg {pos_data['finalRating'].mean():.2f}")
    
    print("\n🏆 Top 10 Players:")
    top_players = final_output.nlargest(10, 'finalRating')[['player', 'position', 'finalRating', 'appearances']]
    for _, player in top_players.iterrows():
        print(f"   {player['player']} ({player['position']}) - {player['finalRating']} ({player['appearances']} apps)")
    
    return True

def main():
    """
    Main function to generate player ratings
    """
    print("🚀 Starting Player Rating Generation")
    
    if not generate_player_ratings():
        print("❌ Failed to generate ratings")
        return
    
    print("🎉 Player rating generation completed successfully!")

if __name__ == "__main__":
    # Set up paths - script is in Rating Generator, data is in parent ratings directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ratings_dir = os.path.dirname(script_dir)  # Go up one level from Rating Generator to ratings
    os.chdir(ratings_dir)  # Change to ratings directory to find player_stats.csv
    main() 
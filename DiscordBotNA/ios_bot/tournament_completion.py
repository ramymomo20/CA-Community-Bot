"""
Tournament completion functionality for calculating awards and finishing tournaments.
"""

import os
import json
import csv
from typing import Dict, List, Optional, Any

async def complete_tournament_with_awards(tournament_id: int):
    """
    Complete a tournament by calculating awards and updating the database.
    Returns a dict with success status and results.
    """
    try:
        from ios_bot.database_manager import (
            get_tournament_by_id_v2, get_tournament_leagues, get_tournament_league_table,
            get_tournament_matches, get_tournament_teams_v2, execute_query
        )
        
        # Get tournament info
        tournament = await get_tournament_by_id_v2(tournament_id)
        if not tournament:
            return {'success': False, 'error': 'Tournament not found'}
        
        if tournament.get('is_completed'):
            return {'success': False, 'error': 'Tournament is already completed'}
        
        # Get tournament leagues for standings
        tournament_leagues = await get_tournament_leagues(tournament_id)
        if not tournament_leagues:
            return {'success': False, 'error': 'No leagues found in tournament'}
        
        # Calculate league standings and get top teams
        league_results = {}
        all_top_teams = []
        
        for league in tournament_leagues:
            league_name = league['league_name']
            league_standings = await get_tournament_league_table(tournament_id, league_name)
            
            # Sort by points, then goal difference, then goals for
            league_standings.sort(key=lambda x: (x['points'], x['goal_difference'], x['goals_for']), reverse=True)
            
            # Get top 3 teams for this league
            top_teams_this_league = league_standings[:3]
            all_top_teams.extend([team['guild_id'] for team in top_teams_this_league])
            
            league_results[league_name] = {
                'standings': league_standings,
                'champion': league_standings[0]['guild_name'] if league_standings else None,
                'runner_up': league_standings[1]['guild_name'] if len(league_standings) > 1 else None,
                'third_place': league_standings[2]['guild_name'] if len(league_standings) > 2 else None
            }
        
        # Calculate player awards
        player_awards = await calculate_tournament_player_awards(tournament_id, all_top_teams)
        
        # Update tournament database with results
        tournament_champion = None
        tournament_runner_up = None
        tournament_third_place = None
        
        # If single league, use those results directly
        if len(tournament_leagues) == 1:
            league_name = tournament_leagues[0]['league_name']
            league_result = league_results[league_name]
            tournament_champion = league_result['champion']
            tournament_runner_up = league_result['runner_up']
            tournament_third_place = league_result['third_place']
        else:
            # Multiple leagues - determine overall winner from league champions
            league_champions = []
            for league_name, league_result in league_results.items():
                if league_result['champion']:
                    # Get the champion's full stats
                    champion_stats = next(
                        (team for team in league_result['standings'] if team['guild_name'] == league_result['champion']),
                        None
                    )
                    if champion_stats:
                        champion_stats['league_name'] = league_name
                        league_champions.append(champion_stats)
            
            # Sort league champions by points, goal difference, goals for
            league_champions.sort(key=lambda x: (x['points'], x['goal_difference'], x['goals_for']), reverse=True)
            
            if league_champions:
                tournament_champion = league_champions[0]['guild_name']
                tournament_runner_up = league_champions[1]['guild_name'] if len(league_champions) > 1 else None
                tournament_third_place = league_champions[2]['guild_name'] if len(league_champions) > 2 else None
        
        # Prepare awards data for database
        awards_data = {
            'champion': tournament_champion,
            'runner_up': tournament_runner_up,
            'third_place': tournament_third_place
        }
        
        # Add player awards
        if player_awards.get('mvp'):
            awards_data['mvp'] = player_awards['mvp']['name']
        if player_awards.get('top_scorer'):
            awards_data['top_scorer'] = player_awards['top_scorer']['name']
        if player_awards.get('top_assister'):
            awards_data['top_assister'] = player_awards['top_assister']['name']
        if player_awards.get('top_defender'):
            awards_data['top_defender'] = player_awards['top_defender']['name']
        if player_awards.get('top_goalkeeper'):
            awards_data['top_goalkeeper'] = player_awards['top_goalkeeper']['name']
        
        # Complete the tournament
        query = """
        UPDATE TOURNAMENTS_V2 
        SET is_completed = TRUE, end_date = CURRENT_TIMESTAMP, awards = %s
        WHERE id = %s
        """
        success = await execute_query(query, (json.dumps(awards_data), tournament_id), commit=True)
        
        if success:
            return {
                'success': True,
                'league_results': league_results,
                'player_awards': player_awards,
                'tournament_awards': awards_data
            }
        else:
            return {'success': False, 'error': 'Failed to update tournament database'}
    
    except Exception as e:
        print(f"Error completing tournament: {e}")
        return {'success': False, 'error': str(e)}

async def calculate_tournament_player_awards(tournament_id: int, top_team_guild_ids: List[int]) -> Dict[str, Any]:
    """
    Calculate player awards for a tournament with bias toward top teams.
    """
    try:
        from ios_bot.database_manager import get_tournament_matches, get_tournament_teams_v2
        
        # Get tournament matches
        tournament_matches = await get_tournament_matches(tournament_id)
        if not tournament_matches:
            return {}
        
        # Get match IDs for this tournament
        tournament_match_ids = [match['match_id'] for match in tournament_matches]
        
        # Load player stats from CSV
        player_stats_path = os.path.join(os.path.dirname(__file__), 'ratings', 'player_stats.csv')
        if not os.path.exists(player_stats_path):
            print(f"Player stats CSV not found: {player_stats_path}")
            return {}
        
        # Read player stats for tournament matches
        tournament_player_stats = []
        team_name_to_guild_id = {}
        
        # Get team mappings
        tournament_teams = await get_tournament_teams_v2(tournament_id)
        for team in tournament_teams:
            team_name_to_guild_id[team['guild_name']] = team['guild_id']
        
        with open(player_stats_path, 'r', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row.get('match_id') in tournament_match_ids:
                    # Add guild_id to player stats
                    team_name = row.get('Team Name', '')
                    row['guild_id'] = team_name_to_guild_id.get(team_name, 0)
                    tournament_player_stats.append(row)
        
        if not tournament_player_stats:
            print("No player stats found for tournament matches")
            return {}
        
        # Calculate awards
        awards = {}
        
        # MVP calculation (using existing MVP logic with top team bias)
        mvp_result = get_tournament_mvp(tournament_player_stats, top_team_guild_ids)
        if mvp_result:
            awards['mvp'] = mvp_result
        
        # Top Scorer (goals with top team bias)
        top_scorer = get_tournament_top_scorer(tournament_player_stats, top_team_guild_ids)
        if top_scorer:
            awards['top_scorer'] = top_scorer
        
        # Top Assister (assists with top team bias)
        top_assister = get_tournament_top_assister(tournament_player_stats, top_team_guild_ids)
        if top_assister:
            awards['top_assister'] = top_assister
        
        # Top Defender (using defensive metrics with top team bias)
        top_defender = get_tournament_top_defender(tournament_player_stats, top_team_guild_ids)
        if top_defender:
            awards['top_defender'] = top_defender
        
        # Top Goalkeeper (using keeper metrics with top team bias)
        top_goalkeeper = get_tournament_top_goalkeeper(tournament_player_stats, top_team_guild_ids)
        if top_goalkeeper:
            awards['top_goalkeeper'] = top_goalkeeper
        
        return awards
    
    except Exception as e:
        print(f"Error calculating tournament player awards: {e}")
        return {}

def get_tournament_mvp(player_stats: List[Dict], top_team_guild_ids: List[int]) -> Optional[Dict]:
    """Calculate tournament MVP using existing MVP logic with top team bias."""
    if not player_stats:
        return None
    
    # Group stats by match_id to calculate per-match MVP
    matches_by_id = {}
    for stat in player_stats:
        match_id = stat.get('match_id')
        if match_id not in matches_by_id:
            matches_by_id[match_id] = []
        matches_by_id[match_id].append(stat)
    
    # Calculate MVP score for each player across all matches
    player_mvp_scores = {}
    
    for match_id, match_stats in matches_by_id.items():
        # Use the existing MVP calculation for this match
        from ios_bot.commands.view_match import get_mvp
        mvp_result = get_mvp(match_stats)
        
        if mvp_result and mvp_result != "No data available" and mvp_result != "No valid players found":
            # Parse MVP result to get player name and score
            try:
                # MVP result format: "`Player Name` (**Position**) : `Score/10` - stats"
                parts = mvp_result.split('`')
                if len(parts) >= 4:
                    player_name = parts[1]
                    score_part = parts[3]
                    score = float(score_part.split('/')[0])
                    
                    # Find the player's team
                    player_team_guild_id = None
                    for stat in match_stats:
                        if stat.get('Name') == player_name:
                            player_team_guild_id = stat.get('guild_id', 0)
                            break
                    
                    # Apply top team bias
                    if player_team_guild_id in top_team_guild_ids:
                        score *= 1.2  # 20% bonus for top team players
                    
                    if player_name not in player_mvp_scores:
                        player_mvp_scores[player_name] = {'total_score': 0, 'matches': 0, 'team_guild_id': player_team_guild_id}
                    
                    player_mvp_scores[player_name]['total_score'] += score
                    player_mvp_scores[player_name]['matches'] += 1
            except Exception as e:
                print(f"Error parsing MVP result: {e}")
                continue
    
    # Find the player with the highest average MVP score
    if player_mvp_scores:
        best_player = max(
            player_mvp_scores.items(),
            key=lambda x: x[1]['total_score'] / x[1]['matches']
        )
        
        # Get team name
        team_name = "Unknown Team"
        if best_player[1]['team_guild_id']:
            team_info = next(
                (stat for stat in player_stats if stat.get('guild_id') == best_player[1]['team_guild_id']),
                None
            )
            if team_info:
                team_name = team_info.get('Team Name', 'Unknown Team')
        
        return {
            'name': best_player[0],
            'team': team_name,
            'average_score': best_player[1]['total_score'] / best_player[1]['matches'],
            'matches_played': best_player[1]['matches']
        }
    
    return None

def get_tournament_top_scorer(player_stats: List[Dict], top_team_guild_ids: List[int]) -> Optional[Dict]:
    """Calculate tournament top scorer with top team bias."""
    if not player_stats:
        return None
    
    # Aggregate goals by player
    player_goals = {}
    
    for stat in player_stats:
        player_name = stat.get('Name', '')
        team_guild_id = stat.get('guild_id', 0)
        
        if not player_name:
            continue
        
        goals = int(float(stat.get('goals', 0)))
        
        if player_name not in player_goals:
            player_goals[player_name] = {
                'goals': 0,
                'team_guild_id': team_guild_id,
                'team_name': stat.get('Team Name', 'Unknown Team')
            }
        
        player_goals[player_name]['goals'] += goals
    
    # Apply top team bias and find top scorer
    for player_name, data in player_goals.items():
        if data['team_guild_id'] in top_team_guild_ids:
            data['goals'] *= 1.1  # 10% bonus for top team players
    
    # Find top scorer
    if player_goals:
        top_scorer = max(player_goals.items(), key=lambda x: x[1]['goals'])
        return {
            'name': top_scorer[0],
            'goals': int(top_scorer[1]['goals']),
            'team': top_scorer[1]['team_name']
        }
    
    return None

def get_tournament_top_assister(player_stats: List[Dict], top_team_guild_ids: List[int]) -> Optional[Dict]:
    """Calculate tournament top assister with top team bias."""
    if not player_stats:
        return None
    
    # Aggregate assists by player
    player_assists = {}
    
    for stat in player_stats:
        player_name = stat.get('Name', '')
        team_guild_id = stat.get('guild_id', 0)
        
        if not player_name:
            continue
        
        assists = int(float(stat.get('assists', 0)))
        
        if player_name not in player_assists:
            player_assists[player_name] = {
                'assists': 0,
                'team_guild_id': team_guild_id,
                'team_name': stat.get('Team Name', 'Unknown Team')
            }
        
        player_assists[player_name]['assists'] += assists
    
    # Apply top team bias and find top assister
    for player_name, data in player_assists.items():
        if data['team_guild_id'] in top_team_guild_ids:
            data['assists'] *= 1.1  # 10% bonus for top team players
    
    # Find top assister
    if player_assists:
        top_assister = max(player_assists.items(), key=lambda x: x[1]['assists'])
        return {
            'name': top_assister[0],
            'assists': int(top_assister[1]['assists']),
            'team': top_assister[1]['team_name']
        }
    
    return None

def get_tournament_top_defender(player_stats: List[Dict], top_team_guild_ids: List[int]) -> Optional[Dict]:
    """Calculate tournament top defender with top team bias."""
    if not player_stats:
        return None
    
    # Filter for defensive positions
    defensive_positions = ['LB', 'CB', 'RB']
    defender_stats = [stat for stat in player_stats if stat.get('Position') in defensive_positions]
    
    if not defender_stats:
        return None
    
    # Calculate defensive score for each player
    player_defensive_scores = {}
    
    for stat in defender_stats:
        player_name = stat.get('Name', '')
        team_guild_id = stat.get('guild_id', 0)
        
        if not player_name:
            continue
        
        # Calculate defensive score (similar to existing best defender logic)
        interceptions = int(float(stat.get('interceptions', 0)))
        tackles_completed = int(float(stat.get('slidingTacklesCompleted', 0)))
        passes_completed = int(float(stat.get('passesCompleted', 0)))
        goals_conceded = int(float(stat.get('goalsConceded', 0)))
        
        # Defensive score calculation
        defensive_score = (interceptions * 2.0 + tackles_completed * 1.5 + passes_completed * 0.01 - goals_conceded * 0.5)
        
        if player_name not in player_defensive_scores:
            player_defensive_scores[player_name] = {
                'score': 0,
                'matches': 0,
                'team_guild_id': team_guild_id,
                'team_name': stat.get('Team Name', 'Unknown Team')
            }
        
        player_defensive_scores[player_name]['score'] += defensive_score
        player_defensive_scores[player_name]['matches'] += 1
    
    # Apply top team bias and find top defender
    for player_name, data in player_defensive_scores.items():
        if data['team_guild_id'] in top_team_guild_ids:
            data['score'] *= 1.2  # 20% bonus for top team players
    
    # Find top defender (by average defensive score)
    if player_defensive_scores:
        top_defender = max(
            player_defensive_scores.items(),
            key=lambda x: x[1]['score'] / x[1]['matches']
        )
        return {
            'name': top_defender[0],
            'team': top_defender[1]['team_name'],
            'average_score': top_defender[1]['score'] / top_defender[1]['matches']
        }
    
    return None

def get_tournament_top_goalkeeper(player_stats: List[Dict], top_team_guild_ids: List[int]) -> Optional[Dict]:
    """Calculate tournament top goalkeeper with top team bias."""
    if not player_stats:
        return None
    
    # Filter for goalkeeper position
    goalkeeper_stats = [stat for stat in player_stats if stat.get('Position') == 'GK']
    
    if not goalkeeper_stats:
        return None
    
    # Calculate goalkeeper score for each player
    player_gk_scores = {}
    
    for stat in goalkeeper_stats:
        player_name = stat.get('Name', '')
        team_guild_id = stat.get('guild_id', 0)
        
        if not player_name:
            continue
        
        # Calculate goalkeeper score (similar to existing best goalkeeper logic)
        saves = int(float(stat.get('keeperSaves', 0)))
        saves_caught = int(float(stat.get('keeperSavesCaught', 0)))
        goals_conceded = int(float(stat.get('goalsConceded', 0)))
        
        # Goalkeeper score calculation
        total_saves = saves + saves_caught
        gk_score = total_saves * 2.0 - goals_conceded * 1.0
        
        if player_name not in player_gk_scores:
            player_gk_scores[player_name] = {
                'score': 0,
                'saves': 0,
                'goals_conceded': 0,
                'matches': 0,
                'team_guild_id': team_guild_id,
                'team_name': stat.get('Team Name', 'Unknown Team')
            }
        
        player_gk_scores[player_name]['score'] += gk_score
        player_gk_scores[player_name]['saves'] += total_saves
        player_gk_scores[player_name]['goals_conceded'] += goals_conceded
        player_gk_scores[player_name]['matches'] += 1
    
    # Apply top team bias and find top goalkeeper
    for player_name, data in player_gk_scores.items():
        if data['team_guild_id'] in top_team_guild_ids:
            data['score'] *= 1.2  # 20% bonus for top team players
    
    # Find top goalkeeper (by average goalkeeper score)
    if player_gk_scores:
        top_goalkeeper = max(
            player_gk_scores.items(),
            key=lambda x: x[1]['score'] / x[1]['matches']
        )
        return {
            'name': top_goalkeeper[0],
            'team': top_goalkeeper[1]['team_name'],
            'average_score': top_goalkeeper[1]['score'] / top_goalkeeper[1]['matches'],
            'saves': top_goalkeeper[1]['saves'],
            'goals_conceded': top_goalkeeper[1]['goals_conceded']
        }
    
    return None 
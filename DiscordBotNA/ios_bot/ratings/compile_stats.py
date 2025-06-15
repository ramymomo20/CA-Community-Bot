import json
import csv
from collections import Counter
import paramiko
import re
from datetime import datetime
import os
import sys

# --- Path fix ---
# Add the project root to the Python path to allow imports from ios_bot
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# --- End Path fix ---

# --- SFTP Configuration ---
SERVERS = [
    {
        "host": "199.127.62.217", "port": 8822, "user": "kevina", "pass": "43CHso",
        "dir": "/199.127.62.217_27015/iosoccer/statistics"
    },
    {
        "host": "199.127.63.12", "port": 8822, "user": "kevina", "pass": "43CHso",
        "dir": "/199.127.63.12_27045/iosoccer/statistics"
    }
]
# ------------------------

# --- Configuration ---
# Use absolute paths to ensure the script can be run from anywhere
script_dir = os.path.dirname(os.path.abspath(__file__))
player_stats_filename = os.path.join(script_dir, 'player_stats.csv')
match_summaries_filename = os.path.join(script_dir, 'match_summaries.csv')
last_processed_date_filename = os.path.join(script_dir, 'last_processed_date.txt')

def get_last_processed_date():
    """Reads the last processed date from the file."""
    if not os.path.exists(last_processed_date_filename):
        # If file doesn't exist, try to get it from match_summaries.csv
        last_dt = get_last_match_datetime()
        if last_dt:
            save_last_processed_date(last_dt)
            return last_dt
        return None
    try:
        with open(last_processed_date_filename, 'r') as f:
            date_str = f.read().strip()
            return datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
    except (IOError, ValueError):
        # If file is corrupted, try to get it from match_summaries.csv
        last_dt = get_last_match_datetime()
        if last_dt:
            save_last_processed_date(last_dt)
            return last_dt
        return None

def save_last_processed_date(date):
    """Saves the last processed date to the file."""
    try:
        with open(last_processed_date_filename, 'w') as f:
            f.write(date.strftime('%Y-%m-%d %H:%M:%S'))
        print(f"Saved last processed date: {date}")
    except IOError as e:
        print(f"Warning: Could not save last processed date: {e}")

def get_last_match_datetime():
    """Reads the match summaries CSV and returns the datetime of the most recent match."""
    if not os.path.exists(match_summaries_filename):
        return None
    last_dt = None
    try:
        with open(match_summaries_filename, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    dt = datetime.strptime(row['datetime'], '%Y-%m-%d %H:%M:%S')
                    if last_dt is None or dt > last_dt:
                        last_dt = dt
                except (ValueError, KeyError):
                    continue
    except (IOError, StopIteration):
        return None
    return last_dt

def load_existing_data():
    """
    Loads existing data from both CSVs.
    Returns a set of processed match_ids and the full player_stats data.
    """
    processed_match_ids = set()
    if os.path.exists(match_summaries_filename):
        try:
            with open(match_summaries_filename, 'r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                # Ensure match_id column exists before trying to read it
                if 'match_id' in reader.fieldnames:
                    for row in reader:
                        processed_match_ids.add(row['match_id'])
        except (IOError, StopIteration, KeyError) as e:
            print(f"Warning: Could not properly read existing match summaries. {e}")

    player_stats = []
    header = []
    if os.path.exists(player_stats_filename):
        try:
            with open(player_stats_filename, 'r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                header = reader.fieldnames
                player_stats = list(reader)
        except (IOError, StopIteration):
            pass # File might be empty
            
    return processed_match_ids, player_stats, header if header else None

def calculate_player_scores(player_data, stat_map):
    """
    Calculates the summary scores for a single player for one match.
    - Attacker Score: Raw number of goals.
    - Playmaker Score: Sum of assists, second assists, chances created, and key passes.
    - Defender Score: Sum of interceptions and completed sliding tackles, minus fouls committed.
    - Goalkeeper Score: Sum of saves and caught saves, minus goals conceded.
    """
    player_stats = [0] * len(stat_map)
    for period in player_data['matchPeriodData']:
        for i, stat_val in enumerate(period['statistics']):
            player_stats[i] += stat_val
    
    scores = {
        'attacker': player_stats[stat_map.get('goals', 0)],
        'playmaker': (player_stats[stat_map.get('assists', 0)] + 
                      player_stats[stat_map.get('secondAssists', 0)] + 
                      player_stats[stat_map.get('chancesCreated', 0)] + 
                      player_stats[stat_map.get('keyPasses', 0)]),
        'defender': (player_stats[stat_map.get('interceptions', 0)] + 
                     player_stats[stat_map.get('slidingTacklesCompleted', 0)] - 
                     player_stats[stat_map.get('fouls', 0)]),
        'goalkeeper': (player_stats[stat_map.get('keeperSaves', 0)] + 
                       player_stats[stat_map.get('keeperSavesCaught', 0)] - 
                       player_stats[stat_map.get('goalsConceded', 0)])
    }
    return scores

def main():
    print("--- Starting Stats Compilation ---")

    # --- Load existing data FIRST ---
    processed_match_ids, player_stats_data, player_stats_header = load_existing_data()
    print(f"Found {len(processed_match_ids)} previously processed matches.")

    # Get the last processed date
    last_processed_date = get_last_processed_date()
    if last_processed_date:
        print(f"Last processed match date: {last_processed_date}")
    else:
        print("No last processed date found. Will process all available matches.")

    # --- SFTP Connection and file filtering ---
    new_json_files = []
    for server in SERVERS:
        transport, sftp = None, None
        try:
            print(f"Connecting to {server['host']}...")
            transport = paramiko.Transport((server['host'], server['port']))
            transport.connect(username=server['user'], password=server['pass'])
            sftp = paramiko.SFTPClient.from_transport(transport)
            sftp.chdir(server['dir'])
            
            # Get all JSON files and their dates
            server_files = []
            for filename in sftp.listdir():
                if not filename.endswith('.json'):
                    continue
                
                try:
                    # Extract datetime from filename
                    datetime_str = '_'.join(filename.split('_')[:2])
                    file_dt = datetime.strptime(datetime_str, '%Y.%m.%d_%Hh.%Mm.%Ss')
                    
                    # Skip files older than the last processed date
                    if last_processed_date and file_dt <= last_processed_date:
                        continue
                    
                    # Skip if match_id already processed
                    match_id = filename.replace('.json', '').replace('.', '').replace('_', '').replace('h', '').replace('m', '').replace('s', '')
                    if match_id in processed_match_ids:
                        continue
                    
                    server_files.append((filename, file_dt))
                except (ValueError, IndexError) as e:
                    print(f"  -> Skipping {filename}: Could not parse datetime. Error: {e}")
                    continue
            
            # Sort files by date (newest first)
            server_files.sort(key=lambda x: x[1], reverse=True)
            print(f"Found {len(server_files)} new files on {server['host']}")
            
            # Process files in order
            for filename, file_dt in server_files:
                try:
                    with sftp.open(filename, 'r') as f:
                        f.prefetch()
                        match_data = json.load(f)
                        match_id = filename.replace('.json', '').replace('.', '').replace('_', '').replace('h', '').replace('m', '').replace('s', '')
                        new_json_files.append((match_data, file_dt, match_id, filename))
                except Exception as e:
                    print(f"  -> Error reading {filename}: {e}")
                    continue
                    
        except Exception as e:
            print(f"Could not connect or process files on {server['host']}: {e}")
        finally:
            if sftp: sftp.close()
            if transport: transport.close()

    if not new_json_files:
        print("No new match files found. Exiting.")
        return

    # Sort all files by date (newest first)
    new_json_files.sort(key=lambda x: x[1], reverse=True)
    
    # --- Schema Management: Build a master header ---
    master_stat_types = set()
    for data, _, _, _ in new_json_files:
        try:
            master_stat_types.update(data['matchData']['statisticTypes'])
        except KeyError:
            continue

    base_header = ['match_id', 'datetime', 'Steam ID', 'Name', 'Team Name', 'Opponent Team Name', 'Team Side', 'Position']
    
    if player_stats_header:
        master_stat_types.update(player_stats_header[8:])
    
    final_player_stats_header = base_header + sorted(list(master_stat_types))
    
    rewrite_needed = False
    if not player_stats_header or set(final_player_stats_header) != set(player_stats_header):
        if player_stats_header and set(final_player_stats_header) != set(player_stats_header):
            print("Schema change detected. Player stats file will be completely rewritten.")
        rewrite_needed = True

    # --- Main Processing Loop ---
    new_match_summaries = []
    new_player_stats = []
    latest_processed_date = last_processed_date
    for data, match_dt, match_id, filename in new_json_files:
        if match_id in processed_match_ids:
            continue
        try:
            match_datetime_str = match_dt.strftime('%Y-%m-%d %H:%M:%S')
            data = data['matchData']

            # --- KeeperBot Check ---
            is_bot_game = False
            for player in data.get('players', []):
                info = player.get('info', {})
                if info.get('steamId') == 'BOT' or info.get('name') == 'KeeperBotHome':
                    is_bot_game = True
                    break
            if is_bot_game:
                continue
            # --- End KeeperBot Check ---

            # --- Game Type Check ---
            map_name = data.get('matchInfo', {}).get('mapName', '')
            game_type = '6v6'
            if re.search(r'8v8', map_name, re.IGNORECASE):
                game_type = '8v8'
            # --- End Game Type Check ---
            
            current_stat_types = data['statisticTypes']
            
            stat_map = {name: i for i, name in enumerate(current_stat_types)}
            team_name_map = {team['matchTotal']['side']: team['matchTotal']['name'] for team in data['teams']}
            home_team, away_team = team_name_map.get('home', 'N/A'), team_name_map.get('away', 'N/A')
            
            # Robustly find home/away scores using the statistics array
            home_score = 0
            away_score = 0
            try:
                # The 13th element (index 12) is 'goals'
                goals_index = 12 
                for team in data['teams']:
                    if team.get('matchTotal', {}).get('side') == 'home':
                        home_score = team['matchTotal']['statistics'][goals_index]
                    elif team.get('matchTotal', {}).get('side') == 'away':
                        away_score = team['matchTotal']['statistics'][goals_index]
                scoreline = f"{home_score}-{away_score}"
            except (IndexError, KeyError) as e:
                print(f"  -> Skipping scoreline for {filename}: Could not find goals in statistics array. Error: {e}")
                scoreline = "N/A"

            best_performers = {
                'attacker': {'name': 'N/A', 'steamId': '', 'score': -1}, 'playmaker': {'name': 'N/A', 'steamId': '', 'score': -1},
                'defender': {'name': 'N/A', 'steamId': '', 'score': -999}, 'goalkeeper': {'name': 'N/A', 'steamId': '', 'score': -999}
            }
            
            all_player_scores = []

            for player in data['players']:
                steam_id, player_name = player['info']['steamId'], player['info']['name']
                
                player_team_name = 'N/A'
                player_opponent_team_name = 'N/A'
                player_side = 'N/A'
                if player.get('matchPeriodData'):
                    player_side = player['matchPeriodData'][0]['info']['team']
                    player_team_name = team_name_map.get(player_side, 'N/A')
                    player_opponent_team_name = team_name_map.get('away' if player_side == 'home' else 'home', 'N/A')

                player_match_stats = [0] * len(current_stat_types)
                for period in player['matchPeriodData']:
                    for i, stat_val in enumerate(period['statistics']):
                        player_match_stats[i] += stat_val
                
                scores = {
                    'attacker': player_match_stats[stat_map['goals']] if 'goals' in stat_map else 0,
                    'playmaker': sum(player_match_stats[stat_map[s]] for s in ['assists', 'secondAssists', 'chancesCreated', 'keyPasses'] if s in stat_map),
                    'defender': (player_match_stats[stat_map['interceptions']] if 'interceptions' in stat_map else 0) + \
                                (player_match_stats[stat_map['slidingTacklesCompleted']] if 'slidingTacklesCompleted' in stat_map else 0) - \
                                (player_match_stats[stat_map['fouls']] if 'fouls' in stat_map else 0),
                    'goalkeeper': (player_match_stats[stat_map['keeperSaves']] if 'keeperSaves' in stat_map else 0) + \
                                  (player_match_stats[stat_map['keeperSavesCaught']] if 'keeperSavesCaught' in stat_map else 0) - \
                                  (player_match_stats[stat_map['goalsConceded']] if 'goalsConceded' in stat_map else 0)
                }

                all_player_scores.append({
                    'steam_id': steam_id, 'player_name': player_name,
                    'team_name': player_team_name, 'scores': scores
                })

                for category, score in scores.items():
                    if score > best_performers[category]['score']:
                        best_performers[category] = {'name': player_name, 'steamId': steam_id, 'score': score}

                new_player_stat_row = {
                    'match_id': match_id,
                    'datetime': match_datetime_str,
                    'Steam ID': steam_id,
                    'Name': player_name,
                    'Team Name': player_team_name,
                    'Opponent Team Name': player_opponent_team_name,
                    'Team Side': player_side,
                    'Position': player['matchPeriodData'][0]['info']['position'] if player.get('matchPeriodData') else 'N/A',
                }
                for i, stat_name in enumerate(current_stat_types):
                    new_player_stat_row[stat_name] = player_match_stats[i]
                new_player_stats.append(new_player_stat_row)

            def format_performer(cat):
                perf = best_performers[cat]
                return f"{perf['name']} : {perf['steamId']} : {perf['score']}" if perf['name'] != 'N/A' else 'N/A'

            new_match_summaries.append({
                'match_id': match_id,
                'datetime': match_datetime_str, 'home_team': home_team, 'away_team': away_team, 'scoreline': scoreline,
                'game_type': game_type,
                'best_attacker': format_performer('attacker'), 'best_playmaker': format_performer('playmaker'),
                'best_defender': format_performer('defender'), 'best_goalkeeper': format_performer('goalkeeper'),
                'all_player_scores': json.dumps(all_player_scores)
            })
            processed_match_ids.add(match_id)
            
            # Update the latest processed date
            if latest_processed_date is None or match_dt > latest_processed_date:
                latest_processed_date = match_dt

        except (KeyError, ValueError, IndexError) as e:
            print(f"  -> Skipping match {filename} due to processing error: {e}")
            continue

    # --- Write updates to CSV files ---
    if new_match_summaries:
        is_new_file = not os.path.exists(match_summaries_filename)
        with open(match_summaries_filename, 'a', newline='', encoding='utf-8') as f:
            header = list(new_match_summaries[0].keys())
            writer = csv.DictWriter(f, fieldnames=header)
            if is_new_file:
                writer.writeheader()
            writer.writerows(new_match_summaries)
        print("Done.")

    if new_player_stats:
        if rewrite_needed:
            all_stats = player_stats_data + new_player_stats
            with open(player_stats_filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=final_player_stats_header, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(all_stats)
        else:
            is_new_file = not os.path.exists(player_stats_filename)
            with open(player_stats_filename, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=final_player_stats_header, extrasaction='ignore')
                if is_new_file:
                    writer.writeheader()
                writer.writerows(new_player_stats)
        print("Done.")
    
    # Save the latest processed date
    if latest_processed_date:
        save_last_processed_date(latest_processed_date)
        print(f"Updated last processed date to: {latest_processed_date}")
    
    print("\n--- Stats Compilation Finished ---")

if __name__ == "__main__":
    main()

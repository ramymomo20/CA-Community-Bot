import json
import csv
from collections import Counter, defaultdict, deque
import paramiko
import re
from datetime import datetime
import os
import sys
import asyncio

# --- Path fix ---
# Add the project root to the Python path to allow imports from ios_bot
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# --- End Path fix ---

# Import database manager
from ios_bot.database_manager import get_servers_for_compile_stats

# --- Configuration ---
# Use absolute paths to ensure the script can be run from anywhere
script_dir = os.path.dirname(os.path.abspath(__file__))
player_stats_filename = os.path.join(script_dir, 'player_stats.csv')
match_summaries_filename = os.path.join(script_dir, 'match_summaries.csv')
last_processed_date_filename = os.path.join(script_dir, 'last_processed_date.txt')

def get_servers_sync():
    """Get servers from database synchronously."""
    try:
        # Create a new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        servers = loop.run_until_complete(get_servers_for_compile_stats())
        loop.close()
        
        if not servers:
            print("Warning: No servers found in database with SFTP details.")
            print("Make sure servers have been added with SFTP IP, host username, and host password.")
            return []
        
        for server in servers:
            print(f"  - {server['host']}:{server['port']} (user: {server['user']})")
        
        return servers
    except Exception as e:
        print(f"Error getting servers from database: {e}")
        print("Falling back to empty server list.")
        return []

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

def analyze_lineups(match_data):
    """
    Analyzes the lineup changes throughout a match and returns initial lineups, 
    final lineups, and substitution summary.
    """
    players = match_data["players"]
    format = match_data["matchInfo"]["format"]

    # Determine position order based on format
    if format == 8:
        POSITION_ORDER = ["GK", "LB", "CB", "RB", "CM", "LW", "CF", "RW"]
    else:
        POSITION_ORDER = ["GK", "LB", "RB", "CM", "LW", "RW"]
    

    # Gather all periods for all players
    periods = []
    for player in players:
        info = player["info"]
        name = info["name"]
        steamid = info["steamId"]
        for mpd in player.get("matchPeriodData", []):
            mpd_info = mpd["info"]
            team = mpd_info["team"]
            position = mpd_info["position"]
            start = mpd_info["startSecond"]
            end = mpd_info["endSecond"]
            periods.append({
                "name": name,
                "steamid": steamid,
                "team": team,
                "position": position,
                "start": start,
                "end": end
            })

    if not periods:
        return {}, {}, []

    # Find total match time
    total_match_time = max(p["end"] for p in periods)

    # Build timeline of all changes
    timeline = []
    for p in periods:
        timeline.append((p["start"], "in", p))
        timeline.append((p["end"], "out", p))
    timeline.sort(key=lambda x: (x[0], 0 if x[1] == "in" else 1))

    # Initial lineup at t=0
    initial_lineup = defaultdict(dict)
    for p in periods:
        if p["start"] == 0:
            initial_lineup[p["team"]][p["position"]] = (p["name"], p["steamid"])

    # Track current lineup and player positions
    current_lineup = defaultdict(dict)
    player_positions = {}  # (name, steamid) -> (team, pos)
    seen_players = set()
    subbed_in_players = set()
    initial_starters = set()
    sub_in_for = dict()  # (name, steamid) -> (name, steamid) they replaced
    swap_events = set()  # (t, team, pos1, pos2) to avoid duplicate swap prints

    # Track a queue of players who left each position and have not yet been replaced
    left_queue = defaultdict(deque)  # (team, pos) -> deque of (name, steamid, time)

    for team in initial_lineup:
        for pos in initial_lineup[team]:
            current_lineup[team][pos] = initial_lineup[team][pos]
            player_positions[initial_lineup[team][pos]] = (team, pos)
            seen_players.add(initial_lineup[team][pos])
            initial_starters.add(initial_lineup[team][pos])

    # To avoid duplicate messages
    printed_events = set()
    substitution_pairs = []  # (left_player, subbed_in_player, position, team, time_left, time_in)

    for idx, (t, action, p) in enumerate(timeline):
        team = p["team"]
        pos = p["position"]
        name = p["name"]
        steamid = p["steamid"]
        player_key = (name, steamid)
        key = (team, pos)
        event_id = (t, action, name, steamid, team, pos)
        if event_id in printed_events:
            continue
        printed_events.add(event_id)

        # Detect swaps: look ahead for another "in" at the same time for the same team
        if action == "in" and t > 0:
            # Find if another player is also "in" at this time for the same team
            for jdx in range(idx + 1, len(timeline)):
                t2, action2, p2 = timeline[jdx]
                if t2 != t or action2 != "in":
                    break
                team2 = p2["team"]
                pos2 = p2["position"]
                name2 = p2["name"]
                steamid2 = p2["steamid"]
                # Only allow swaps within the same team and different positions
                if team2 == team and pos2 != pos:
                    # Check if both positions were occupied before
                    if pos in current_lineup[team] and pos2 in current_lineup[team]:
                        prev1 = current_lineup[team][pos]
                        prev2 = current_lineup[team][pos2]
                        # If player1 is subbing into pos2 and player2 is subbing into pos1, it's a swap
                        if (name2, steamid2) == prev1 and (name, steamid) == prev2:
                            swap_id = (t, team, pos, pos2)
                            if swap_id not in swap_events:
                                sub_in_for[(name, steamid)] = prev2
                                sub_in_for[(name2, steamid2)] = prev1
                                subbed_in_players.add((name, steamid))
                                subbed_in_players.add((name2, steamid2))
                            break
            if 'msg' in locals():
                # Perform the swap in the lineup
                prev1 = current_lineup[team][pos]
                prev2 = current_lineup[team][pos2]
                current_lineup[team][pos] = (name, steamid)
                current_lineup[team][pos2] = (name2, steamid2)
                player_positions[(name, steamid)] = (team, pos)
                player_positions[(name2, steamid2)] = (team, pos2)
                seen_players.add((name, steamid))
                seen_players.add((name2, steamid2))
                continue  # Skip the rest of the logic for this event

        if action == "in":
            if key in current_lineup[team]:
                prev_name, prev_steamid = current_lineup[team][pos]
                prev_key = (prev_name, prev_steamid)
                if prev_key != player_key:
                    if player_key in seen_players:
                        pass
                    else:
                        subbed_in_players.add(player_key)
                        sub_in_for[player_key] = prev_key
                    seen_players.add(player_key)
                else:
                    # Same player, possibly re-entering (rare)
                    pass
            else:
                # If position was empty, always pop from left_queue if available
                if left_queue[(team, pos)]:
                    left_name, left_steamid, left_time = left_queue[(team, pos)].popleft()
                    sub_in_for[player_key] = (left_name, left_steamid)
                    subbed_in_players.add(player_key)
                    # Track the substitution pair
                    substitution_pairs.append((
                        (left_name, left_steamid),
                        (name, steamid),
                        pos,
                        team,
                        left_time,
                        t
                    ))
                else:
                    sub_in_for[player_key] = None
                    subbed_in_players.add(player_key)
                seen_players.add(player_key)
            current_lineup[team][pos] = (name, steamid)
            player_positions[player_key] = (team, pos)
        elif action == "out":
            if key in current_lineup[team] and current_lineup[team][pos][0] == name:
                del current_lineup[team][pos]
                if player_key in player_positions:
                    del player_positions[player_key]
                left_queue[(team, pos)].append((name, steamid, t))

    # Find match end time
    match_end_time = total_match_time

    # 1. Find all players who left early (last endSecond < match_end_time)
    players_left_early = defaultdict(list)  # team -> list of (name, steamid)
    players_joined_late = defaultdict(list)  # team -> list of (name, steamid, startSecond)
    player_periods = defaultdict(list)  # (team, steamid) -> list of (start, end, pos)

    for player in players:
        info = player["info"]
        name = info["name"]
        steamid = info["steamId"]
        periods_this_player = []
        for mpd in player.get("matchPeriodData", []):
            mpd_info = mpd["info"]
            team = mpd_info["team"]
            pos = mpd_info["position"]
            start = mpd_info["startSecond"]
            end = mpd_info["endSecond"]
            periods_this_player.append((start, end, pos))
            player_periods[(team, steamid)].append((start, end, pos))
        if periods_this_player:
            last_end = max(e for s, e, p in periods_this_player)
            first_start = min(s for s, e, p in periods_this_player)
            team = player.get("matchPeriodData", [{}])[0].get("info", {}).get("team", None)
            if last_end < match_end_time and team:
                players_left_early[team].append((name, steamid))
            if first_start > 0 and team:
                players_joined_late[team].append((name, steamid, first_start))

    # 2. For each team, pair new joiners with leavers in order
    sub_pairs = {}  # (team, name, steamid) -> (left_name, left_steamid)
    for team in players_left_early:
        left_queue = deque(players_left_early[team])
        for join_name, join_steamid, join_start in sorted(players_joined_late[team], key=lambda x: x[2]):
            if left_queue:
                left_name, left_steamid = left_queue.popleft()
                sub_pairs[(team, join_name, join_steamid)] = (left_name, left_steamid)

    # 3. Output initial lineups, final lineups, and substitute summary as tuples
    def get_lineup_dict(lineup):
        result = {}
        for team in ["away", "home"]:
            result[team] = []
            for pos in POSITION_ORDER:
                if pos in lineup[team]:
                    name, steamid = lineup[team][pos]
                    result[team].append((pos, name, steamid))
                else:
                    result[team].append((pos, None, None))
        return result

    initial_lineups = get_lineup_dict(initial_lineup)
    final_lineups = get_lineup_dict(current_lineup)
    substitute_summary = []
    for (team, join_name, join_steamid), (left_name, left_steamid) in sub_pairs.items():
        substitute_summary.append((
            team,
            (left_name, left_steamid),
            (join_name, join_steamid)
        ))

    return initial_lineups, final_lineups, substitute_summary

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
    for server in get_servers_sync():
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
            not_proper_game = False
            format = data.get('matchInfo', {}).get('format')
            if format == 6:
                game_type = '6v6'
            if format == 8:
                game_type = '8v8'
            else:
                not_proper_game = True
                break
            if not_proper_game:
                continue
            # --- End Game Type Check ---
            
            # --- Lineup Analysis ---
            initial_lineups, final_lineups, substitution_summary = analyze_lineups(data)
            
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

                # Determine player position more robustly
                player_position = 'N/A'
                if player.get('matchPeriodData'):
                    # Collect all positions from all periods to find the most common one
                    positions = []
                    for period in player['matchPeriodData']:
                        pos = period.get('info', {}).get('position')
                        if pos and pos != 'N/A' and str(pos).lower() not in ['nan', 'null', 'none', '']:
                            positions.append(pos)
                    
                    if positions:
                        # Find the most common position
                        from collections import Counter
                        position_counter = Counter(positions)
                        player_position = position_counter.most_common(1)[0][0]

                new_player_stat_row = {
                    'match_id': match_id,
                    'datetime': match_datetime_str,
                    'Steam ID': steam_id,
                    'Name': player_name,
                    'Team Name': player_team_name,
                    'Opponent Team Name': player_opponent_team_name,
                    'Team Side': player_side,
                    'Position': player_position,
                }
                for i, stat_name in enumerate(current_stat_types):
                    new_player_stat_row[stat_name] = player_match_stats[i]
                new_player_stats.append(new_player_stat_row)

            new_match_summaries.append({
                'match_id': match_id,
                'datetime': match_datetime_str, 
                'home_team': home_team, 
                'away_team': away_team, 
                'scoreline': scoreline,
                'game_type': game_type,
                'initial_lineups': json.dumps(initial_lineups),
                'final_lineups': json.dumps(final_lineups),
                'substitution_summary': json.dumps(substitution_summary)
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

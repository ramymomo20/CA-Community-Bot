"""
Complete compile_stats.py with full iosca_bot parser integration.

Features:
- Bot game detection (KeeperBot filtering)
- Game type validation (6v6/8v8 only)
- Substitution tracking and parsing
- Position time tracking (seconds at each position)
- Enhanced player stats aggregation
- Match validation (proper starting conditions)
- Direct PostgreSQL import via MatchImporter
"""

import json
import paramiko
import re
from datetime import datetime
import os
import sys
import asyncio
from pathlib import Path
import logging
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict

# --- Path fix ---
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# --- End Path fix ---

# Import database and match importer
from ios_bot.db import Database
from ios_bot.utils.match_importer import MatchImporter
from dotenv import load_dotenv

# Load environment variables
load_dotenv(os.path.join(project_root, '.env'))

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

# Setup logging - reduce verbosity
logging.basicConfig(level=logging.WARNING)
logging.getLogger('paramiko').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Configuration ---
script_dir = os.path.dirname(os.path.abspath(__file__))
last_processed_date_filename = os.path.join(script_dir, 'last_processed_date.txt')


class MatchValidator:
    """Validates match data for proper game conditions."""
    
    # Position definitions by format
    POSITIONS_5V5 = ['GK', 'CB', 'LM', 'RM', 'CF']
    POSITIONS_6V6 = ['GK', 'LB', 'RB', 'CM', 'LW', 'RW']
    POSITIONS_8V8 = ['GK', 'LB', 'CB', 'RB', 'CM', 'LW', 'CF', 'RW']
    
    # Minimum players required (including GK)
    MIN_PLAYERS_5V5 = 9   # 5v5 = 10 players minimum (5 per side)
    MIN_PLAYERS_6V6 = 10  # 6v6 = 12 players minimum (6 per side)
    MIN_PLAYERS_8V8 = 15  # 8v8 = 16 players minimum (8 per side)
    
    @staticmethod
    def validate_match_start(match_data: dict, game_format: int) -> Tuple[bool, str]:
        """Validate that the match had proper starting conditions.
        
        Returns:
            Tuple of (is_valid, reason)
        """
        try:
            # Get lineup at kickoff (t=0)
            kickoff_lineup = MatchValidator._get_lineup_at_time(match_data, 0)
            
            # Check minimum players
            if game_format not in [5, 6, 8]:
                return False, f"Unsupported game format: {game_format}"
            elif game_format == 5:
                min_required = MatchValidator.MIN_PLAYERS_5V5
            elif game_format == 6:
                min_required = MatchValidator.MIN_PLAYERS_6V6
            else:  # game_format == 8
                min_required = MatchValidator.MIN_PLAYERS_8V8
            
            total_players = len(kickoff_lineup['home']) + len(kickoff_lineup['away'])
            
            if total_players < min_required:
                return False, f"Insufficient players at kickoff: {total_players}/{min_required} required"
            
            # Check for at least one goalkeeper total (either team)
            home_has_gk = any(p['position'] == 'GK' for p in kickoff_lineup['home'])
            away_has_gk = any(p['position'] == 'GK' for p in kickoff_lineup['away'])
            
            if not home_has_gk and not away_has_gk:
                return False, "No goalkeeper found at kickoff (at least 1 required)"
            
            return True, "Valid match start"
        except Exception as e:
            return False, f"Validation error: {e}"
    
    @staticmethod
    def _get_lineup_at_time(match_data: dict, time_seconds: int) -> Dict[str, List[Dict[str, str]]]:
        """Get the lineup (players on field) at a specific time."""
        lineup = {'home': [], 'away': []}
        
        players = match_data.get('matchData', {}).get('players', [])
        
        for player_data in players:
            info = player_data.get('info', {})
            steam_id = info.get('steamId', '')
            name = info.get('name', 'Unknown')
            
            # Check all period segments
            for period in player_data.get('matchPeriodData', []):
                period_info = period.get('info', {})
                start = period_info.get('startSecond', 0)
                end = period_info.get('endSecond', 0)
                team = period_info.get('team', '')
                position = period_info.get('position', '')
                
                # Check if player was on field at this time
                if start <= time_seconds < end and team in ['home', 'away']:
                    lineup[team].append({
                        'steamId': steam_id,
                        'name': name,
                        'position': position
                    })
                    break  # Only count once per player
        
        return lineup


class EnhancedMatchParser:
    """Enhanced parser with substitution tracking and position time analysis."""
    
    @staticmethod
    def parse_match_with_details(match_data: dict) -> Optional[Dict[str, Any]]:
        """Parse match with full details including substitutions and position times.
        
        Returns:
            Dict with:
            - match_info: Basic match information
            - players: Enhanced player data with position times
            - substitutions: List of substitution events
            - lineups: Starting and final lineups
        """
        try:
            match_data_obj = match_data.get('matchData', {})
            match_info = match_data_obj.get('matchInfo', {})
            stat_types = match_data_obj.get('statisticTypes', [])
            players_data = match_data_obj.get('players', [])
            teams = match_data_obj.get('teams', [])
            
            if len(teams) < 2:
                return None
            
            # Extract basic match info
            game_format = match_info.get('format', 0)
            start_time = match_info.get('startTime')
            end_time = match_info.get('endTime')
            
            home_team = teams[0]['matchTotal']
            away_team = teams[1]['matchTotal']
            home_score = home_team['statistics'][12]  # Goals stat index
            away_score = away_team['statistics'][12]
            
            # Build enhanced player data
            players = EnhancedMatchParser._build_player_data(players_data, stat_types)
            
            # Extract substitutions
            substitutions = EnhancedMatchParser._extract_substitutions(players)
            
            # Get starting and final lineups
            starting_lineup = EnhancedMatchParser._get_lineup_at_time(players_data, 0)
            final_lineup = EnhancedMatchParser._get_lineup_at_time(players_data, end_time - start_time if end_time and start_time else 5400)
            
            return {
                'match_info': {
                    'format': game_format,
                    'start_time': start_time,
                    'end_time': end_time,
                    'duration': (end_time - start_time) if end_time and start_time else 0,
                    'home_team': home_team['name'],
                    'away_team': away_team['name'],
                    'home_score': home_score,
                    'away_score': away_score,
                },
                'players': players,
                'substitutions': substitutions,
                'lineups': {
                    'starting': starting_lineup,
                    'final': final_lineup
                }
            }
        except Exception as e:
            logger.error(f"Error in enhanced parsing: {e}")
            return None
    
    @staticmethod
    def _build_player_data(players_data: List[dict], stat_types: List[str]) -> Dict[str, Dict]:
        """Build enhanced player data with position time tracking."""
        players = {}
        empty_stats = lambda: {k: 0 for k in stat_types}
        
        for player_entry in players_data:
            info = player_entry.get('info', {})
            steam_id = info.get('steamId', '')
            name = info.get('name', 'Unknown')
            
            if not steam_id or steam_id == 'Bot' or name == 'KeeperBotHome':
                continue
            
            if steam_id not in players:
                players[steam_id] = {
                    'steam_id': steam_id,
                    'name': name,
                    'teams_played_for': set(),
                    'stats_by_team': {
                        'home': empty_stats(),
                        'away': empty_stats(),
                        'overall': empty_stats(),
                    },
                    'position_seconds_by_team': {
                        'home': defaultdict(int),
                        'away': defaultdict(int),
                        'overall': defaultdict(int),
                    },
                    'started': False,
                    'first_appearance_time': None,
                    'main_position_by_team': {'home': None, 'away': None},
                    'main_position_overall': None,
                }
            
            # Process each period segment
            for period in player_entry.get('matchPeriodData', []):
                period_info = period.get('info', {})
                stats = period.get('statistics', [])
                
                start = period_info.get('startSecond', 0)
                end = period_info.get('endSecond', 0)
                team = period_info.get('team', '')
                position = period_info.get('position', '')
                
                if team not in ['home', 'away']:
                    continue
                
                players[steam_id]['teams_played_for'].add(team)
                
                # Track first appearance
                if players[steam_id]['first_appearance_time'] is None or start < players[steam_id]['first_appearance_time']:
                    players[steam_id]['first_appearance_time'] = start
                
                # Check if started (present at kickoff within 10 seconds)
                if start < 10 and end > 0:
                    players[steam_id]['started'] = True
                
                # Track time on position
                secs = max(0, end - start)
                players[steam_id]['position_seconds_by_team'][team][position] += secs
                players[steam_id]['position_seconds_by_team']['overall'][position] += secs
                
                # Aggregate stats
                for i, stat_name in enumerate(stat_types):
                    val = int(stats[i]) if i < len(stats) else 0
                    players[steam_id]['stats_by_team'][team][stat_name] += val
                    players[steam_id]['stats_by_team']['overall'][stat_name] += val
        
        # Compute main positions
        for steam_id, p in players.items():
            for team in ['home', 'away']:
                pos_map = p['position_seconds_by_team'][team]
                if pos_map:
                    best = sorted(pos_map.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                    p['main_position_by_team'][team] = best
            
            overall_map = p['position_seconds_by_team']['overall']
            if overall_map:
                best_overall = sorted(overall_map.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                p['main_position_overall'] = best_overall
            
            # Convert sets/defaultdicts to normal types
            p['teams_played_for'] = sorted(list(p['teams_played_for']))
            p['position_seconds_by_team']['home'] = dict(p['position_seconds_by_team']['home'])
            p['position_seconds_by_team']['away'] = dict(p['position_seconds_by_team']['away'])
            p['position_seconds_by_team']['overall'] = dict(p['position_seconds_by_team']['overall'])
        
        return players
    
    @staticmethod
    def _extract_substitutions(players: Dict[str, Dict]) -> List[Dict]:
        """Extract substitution events from player data."""
        substitutions = []
        
        # Track players who switched teams mid-match
        for steam_id, player in players.items():
            if len(player['teams_played_for']) > 1:
                # Player switched teams - this is a substitution
                substitutions.append({
                    'player_steam_id': steam_id,
                    'player_name': player['name'],
                    'teams': player['teams_played_for'],
                    'first_appearance': player['first_appearance_time']
                })
        
        return substitutions
    
    @staticmethod
    def _get_lineup_at_time(players_data: List[dict], time_seconds: int) -> Dict[str, List[Dict]]:
        """Get lineup at specific time."""
        lineup = {'home': [], 'away': []}
        
        for player_entry in players_data:
            info = player_entry.get('info', {})
            steam_id = info.get('steamId', '')
            name = info.get('name', 'Unknown')
            
            if name == 'KeeperBotHome':
                continue
            
            for period in player_entry.get('matchPeriodData', []):
                period_info = period.get('info', {})
                start = period_info.get('startSecond', 0)
                end = period_info.get('endSecond', 0)
                team = period_info.get('team', '')
                position = period_info.get('position', '')
                
                if start <= time_seconds < end and team in ['home', 'away']:
                    lineup[team].append({
                        'steam_id': steam_id,
                        'name': name,
                        'position': position
                    })
                    break
        
        return lineup


def is_bot_game(match_data: dict) -> bool:
    """Check if match contains KeeperBot (bot game)."""
    try:
        players = match_data.get('matchData', {}).get('players', [])
        for player in players:
            info = player.get('info', {})
            name = info.get('name', '')
            if 'KeeperBot' in name or name == 'KeeperBotHome' or info.get('steamId', '') == 'Bot':
                return True
        return False
    except Exception:
        return False


def is_valid_format(match_data: dict) -> bool:
    """Check if match format is valid (8v8 or 6v6)."""
    try:
        format_num = match_data.get('matchData', {}).get('matchInfo', {}).get('format')
        return format_num in [8, 6, 5]
    except Exception:
        return False


async def get_servers():
    """Get servers from database asynchronously."""
    await init_db()
    try:
        servers = await db.servers.get_servers_for_compile_stats()
        
        if not servers:
            print("⚠️ No servers found in database with SFTP details.", flush=True)
            return []
        
        return servers
    except Exception as e:
        print(f"❌ Error getting servers from database: {e}", flush=True)
        return []


async def get_last_processed_date():
    """Get the last processed date from database (most recent match datetime)."""
    await init_db()
    try:
        query = "SELECT MAX(datetime) as last_match FROM MATCH_STATS"
        result = await db.pool.fetchrow(query)
        
        if result and result['last_match']:
            return result['last_match']
        
        # Fallback to file if database is empty
        if os.path.exists(last_processed_date_filename):
            with open(last_processed_date_filename, 'r') as f:
                date_str = f.read().strip()
                return datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
        
        return None
    except Exception as e:
        logger.error(f"Error getting last processed date: {e}")
        return None


async def save_last_processed_date(date):
    """Save the last processed date to file (backup)."""
    try:
        with open(last_processed_date_filename, 'w') as f:
            f.write(date.strftime('%Y-%m-%d %H:%M:%S'))
        logger.info(f"Saved last processed date: {date}")
    except IOError as e:
        logger.warning(f"Could not save last processed date: {e}")


def download_match_files_from_server(server_config, last_processed_dt, processed_match_ids):
    """Download and parse match JSON files from a single server via SFTP.
    
    Returns list of tuples: (match_data, file_dt, match_id, filename)
    Mimics original compile_stats.py SFTP logic - reads directly from SFTP without temp files.
    """
    new_json_files = []
    sftp = None
    transport = None
    
    try:
        host = server_config['host']
        port = server_config['port']  # SFTP port (8822)
        username = server_config['user']
        password = server_config.get('pass')
        # Directory path is pre-computed in get_servers_for_compile_stats() using address port
        remote_dir = server_config['dir']
        
        print(f"   🔌 Connecting to {host}:{port} as {username}...", flush=True)
        
        transport = paramiko.Transport((host, port))
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        
        print(f"   ✅ Connected! Changing to directory: {remote_dir}", flush=True)
        
        try:
            sftp.chdir(remote_dir)
            files = sftp.listdir()
        except FileNotFoundError:
            print(f"   ❌ Directory not found: {remote_dir}", flush=True)
            return new_json_files
        
        json_files = [f for f in files if f.endswith('.json')]
        print(f"   📁 Found {len(json_files)} JSON files", flush=True)
        
        skipped_old = 0
        skipped_processed = 0
        server_files = []
        newest_file = None  # Track most recent file by filename datetime
        
        for filename in json_files:
            try:
                # Extract datetime from filename (YYYY.MM.DD_HHh.MMm.SSs.json)
                datetime_str = '_'.join(filename.split('_')[:2])
                file_dt = datetime.strptime(datetime_str, '%Y.%m.%d_%Hh.%Mm.%Ss')
                
                # Generate match_id (remove extensions and special chars)
                match_id = filename.replace('.json', '').replace('.', '').replace('_', '').replace('h', '').replace('m', '').replace('s', '')
                
                # Track the most recent file regardless of cutoff
                if newest_file is None or file_dt > newest_file[1]:
                    newest_file = (filename, file_dt, match_id)
                
                # Skip if already processed
                if match_id in processed_match_ids:
                    skipped_processed += 1
                    continue
                
                # Skip if older than last processed date
                if last_processed_dt and file_dt <= last_processed_dt:
                    skipped_old += 1
                    continue
                
                server_files.append((filename, file_dt, match_id))
                
            except (ValueError, IndexError) as e:
                logger.error(f"  ✗ Could not parse datetime from {filename}: {e}")
                continue

        # Always include the most recent file if it isn't in the DB yet
        if newest_file:
            newest_filename, newest_dt, newest_match_id = newest_file
            if newest_match_id not in processed_match_ids:
                if not any(m_id == newest_match_id for _, _, m_id in server_files):
                    server_files.append((newest_filename, newest_dt, newest_match_id))
                    print(f"   🔎 Forcing latest file check: {newest_filename}", flush=True)
        
        if skipped_old > 0 or skipped_processed > 0:
            print(f"   ⏭️ Skipped {skipped_old} old, {skipped_processed} already processed", flush=True)
        
        # Sort files by date (newest first, like original)
        server_files.sort(key=lambda x: x[1], reverse=True)
        print(f"   📊 {len(server_files)} new files to read", flush=True)
        
        # Read files directly from SFTP (no temp files)
        read_count = 0
        error_count = 0
        for filename, file_dt, match_id in server_files:
            try:
                with sftp.open(filename, 'r') as f:
                    f.prefetch()
                    match_data = json.load(f)
                    new_json_files.append((match_data, file_dt, match_id, filename))
                    read_count += 1
                    # Log progress every 100 files
                    if read_count % 100 == 0:
                        print(f"   📖 Read {read_count}/{len(server_files)} files...", flush=True)
            except Exception as e:
                error_count += 1
                continue
        
        if error_count > 0:
            print(f"   ⚠️ {error_count} files failed to read", flush=True)
        
    except Exception as e:
        print(f"   ❌ SFTP Error: {e}", flush=True)
    finally:
        if sftp:
            sftp.close()
        if transport:
            transport.close()
    
    return new_json_files


async def get_processed_match_ids():
    """Get set of already processed match IDs from database."""
    await init_db()
    try:
        query = "SELECT match_id FROM MATCH_STATS"
        results = await db.pool.fetch(query)
        processed = {row['match_id'] for row in results}
        try:
            skip_rows = await db.pool.fetch("SELECT match_id FROM MATCH_IMPORT_SKIPS")
            processed |= {row['match_id'] for row in skip_rows}
        except Exception:
            pass
        return processed
    except Exception as e:
        logger.error(f"Error getting processed match IDs: {e}")
        return set()


async def record_match_skip(match_id: str, filename: str, reason: str, match_datetime: Optional[datetime] = None) -> None:
    """Record a skipped match so it won't be reprocessed."""
    await init_db()
    try:
        await db.pool.execute(
            """
            INSERT INTO MATCH_IMPORT_SKIPS (match_id, filename, reason, match_datetime)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (match_id) DO UPDATE SET
                filename = EXCLUDED.filename,
                reason = EXCLUDED.reason,
                match_datetime = COALESCE(EXCLUDED.match_datetime, MATCH_IMPORT_SKIPS.match_datetime),
                skipped_at = NOW()
            """,
            match_id,
            filename,
            reason,
            match_datetime
        )
    except Exception as e:
        logger.warning(f"Failed to record skipped match {match_id}: {e}")


async def process_match_files(file_info_list):
    """Process match JSON files with full validation and import to database.
    
    Args:
        file_info_list: List of tuples (match_data, file_dt, match_id, filename)
    """
    if not file_info_list:
        logger.info("No new match files to process.")
        return 0
    
    logger.info(f"\n--- Processing {len(file_info_list)} match files ---")
    
    await init_db()
    importer = MatchImporter(db)
    imported_count = 0
    skipped_bot = 0
    skipped_format = 0
    skipped_invalid_start = 0
    skipped_error = 0
    latest_datetime = None
    
    # Sort by datetime (oldest first for proper chronological import)
    file_info_list.sort(key=lambda x: x[1])
    
    for match_data, file_dt, match_id, filename in file_info_list:
        try:
            print(f"\nProcessing: {filename} (date: {file_dt})", flush=True)
            logger.info(f"\nProcessing: {filename} (date: {file_dt})")
            
            # Check for bot games
            if is_bot_game(match_data):
                print(f"  ⊘ Skipped: Bot game detected", flush=True)
                logger.info(f"  ⊘ Skipped: Bot game detected")
                skipped_bot += 1
                continue
            
            # Check format (8v8 or 6v6)
            if not is_valid_format(match_data):
                print(f"  ⊘ Skipped: Invalid format (not 8v8 or 6v6)", flush=True)
                logger.info(f"  ⊘ Skipped: Invalid format (not 8v8 or 6v6)")
                skipped_format += 1
                continue
            
            # Validate match start conditions
            game_format = match_data.get('matchData', {}).get('matchInfo', {}).get('format')
            is_valid, reason = MatchValidator.validate_match_start(match_data, game_format)
            
            if not is_valid:
                print(f"  ⊘ Skipped: {reason}", flush=True)
                logger.info(f"  ⊘ Skipped: {reason}")
                if "Insufficient players at kickoff" in reason:
                    await record_match_skip(match_id, filename, reason, file_dt)
                skipped_invalid_start += 1
                continue
            
            # Parse with enhanced parser for detailed data
            enhanced_data = EnhancedMatchParser.parse_match_with_details(match_data)
            
            if not enhanced_data:
                print(f"  ✗ Failed to parse match data", flush=True)
                logger.warning(f"  ✗ Failed to parse match data")
                skipped_error += 1
                continue
            
            # Import match using MatchImporter (pass the raw match_data dict and match_id for deduplication)
            print(f"  → Attempting to import match...", flush=True)
            imported_match_id = await importer.import_match_from_json(match_data, match_id_str=match_id)
            
            if imported_match_id:
                imported_count += 1
                print(f"  ✓ Imported match ID: {imported_match_id}", flush=True)
                print(f"    Players: {len(enhanced_data['players'])}", flush=True)
                logger.info(f"  ✓ Imported match ID: {imported_match_id}")
                logger.info(f"    Players: {len(enhanced_data['players'])}")
                logger.info(f"    Substitutions: {len(enhanced_data['substitutions'])}")
                
                # Track latest datetime
                if latest_datetime is None or file_dt > latest_datetime:
                    latest_datetime = file_dt
            else:
                print(f"  ✗ Failed to import (may be duplicate or teams not found)", flush=True)
                logger.warning(f"  ✗ Failed to import (may be duplicate or teams not found)")
                skipped_error += 1
                
        except Exception as e:
            logger.error(f"  ✗ Error processing {filename}: {e}")
            skipped_error += 1
            continue
    
    # Update last processed date
    if latest_datetime:
        await save_last_processed_date(latest_datetime)
    
    logger.info(f"\n--- Import Complete ---")
    logger.info(f"  Total processed: {len(file_info_list)}")
    logger.info(f"  Successfully imported: {imported_count}")
    logger.info(f"  Skipped (bot games): {skipped_bot}")
    logger.info(f"  Skipped (invalid format): {skipped_format}")
    logger.info(f"  Skipped (invalid start): {skipped_invalid_start}")
    logger.info(f"  Skipped (errors): {skipped_error}")
    
    return imported_count


async def main():
    """Main function to run the stats compilation."""
    import time
    start_time = time.time()
    
    print("=== Match Stats Compilation ===", flush=True)
    
    # Get servers from database
    print("🔍 Fetching servers from database...", flush=True)
    servers = await get_servers()
    print(f"📊 Found {len(servers)} servers", flush=True)
    if not servers:
        print("❌ No servers configured. Exiting.", flush=True)
        return
    
    print(f"✅ Server list: {[s.get('name', 'Unknown') for s in servers]}", flush=True)
    
    # Get last processed date
    last_processed_dt = await get_last_processed_date()
    if last_processed_dt:
        print(f"📅 Last processed match: {last_processed_dt}", flush=True)
    else:
        print("📅 No previous matches found. Processing all available matches.", flush=True)
    
    # Get already processed match IDs from database
    processed_match_ids = await get_processed_match_ids()
    print(f"📊 Already processed: {len(processed_match_ids)} matches", flush=True)
    
    # Download and read match files from all servers (run in thread pool to not block event loop)
    all_json_files = []
    print(f"\n🌐 Connecting to {len(servers)} server(s) via SFTP...", flush=True)
    for server in servers:
        print(f"\n📡 Processing server: {server.get('name', 'Unknown')}", flush=True)
        print(f"   Host: {server.get('host')}:{server.get('port')}", flush=True)
        print(f"   User: {server.get('user')}", flush=True)
        print(f"   Directory: {server.get('dir')}", flush=True)
        # Run blocking SFTP operations in thread pool to not block Discord heartbeat
        files = await asyncio.to_thread(download_match_files_from_server, server, last_processed_dt, processed_match_ids)
        print(f"   ✅ Retrieved {len(files)} new match files", flush=True)
        all_json_files.extend(files)
    
    if not all_json_files:
        print("\n✓ No new matches to process. Database is up to date.", flush=True)
        return
    
    print(f"\n📁 Total new files to process: {len(all_json_files)}", flush=True)
    
    # Process and import matches to database
    imported_count = await process_match_files(all_json_files)
    
    elapsed = time.time() - start_time
    print(f"\n✓ Compilation complete! Imported {imported_count} matches in {elapsed:.1f}s", flush=True)


if __name__ == '__main__':
    asyncio.run(main())

"""JSON match file parser.

Parses match JSON files from game servers to extract player and match statistics.
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
import json, re
from pathlib import Path
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)


def convert_steam_id(steam_id: str) -> str:
    """
    Convert Steam ID to legacy STEAM_0:X:Y format.
    Handles both [U:1:accountid] and STEAM_0:X:Y formats.
    
    Args:
        steam_id: Steam ID in any format
    
    Returns:
        Steam ID in STEAM_0:X:Y format
    """
    if not steam_id:
        return steam_id

    s = str(steam_id).strip()
    
    ID64_BASE = 76561197960265728

    STEAM_LEGACY_RE = re.compile(r'^STEAM_\d+:\d+:\d+$', re.IGNORECASE)
    STEAM3_RE = re.compile(r'^\[.*:(?P<id>\d+)\]$')
    STEAM64_RE = re.compile(r'^\d{16,20}$')

    # If already a legacy steam id, normalize universe to 0 and return
    if STEAM_LEGACY_RE.match(s):
        parts = s.split(":")  # STEAM_X:Y:Z
        # parts[0] is 'STEAM_X'
        acct_type = parts[1]
        acct_num = parts[2]
        return f"STEAM_0:{acct_type}:{acct_num}"

    # SteamID3 like: [U:1:12345] or [g:1:12345]
    m = STEAM3_RE.match(s)
    if m:
        account3 = int(m.group("id"))
        acct_type = account3 % 2
        acct_num = (account3 - acct_type) // 2
        return f"STEAM_0:{acct_type}:{acct_num}"

    # SteamID64 (numeric)
    if STEAM64_RE.match(s):
        try:
            sid64 = int(s)
        except ValueError:
            raise ValueError(f"Invalid numeric SteamID64: {s}")
        if sid64 <= ID64_BASE:
            raise ValueError(f"SteamID64 appears too small: {s}")

        offset = sid64 - ID64_BASE
        acct_type = offset % 2
        acct_num = (offset - acct_type) // 2
        return f"STEAM_0:{acct_type}:{acct_num}"

    # Try to be permissive: detect SteamID3 variants without brackets or with different labels
    alt_m = re.search(r'(?P<id>\d{3,})$', s)
    if alt_m:
        # fallback: interpret trailing large number as account3 and convert
        val = int(alt_m.group("id"))
        acct_type = val % 2
        acct_num = (val - acct_type) // 2
        return f"STEAM_0:{acct_type}:{acct_num}"

    raise ValueError(f"Unable to parse Steam ID: {steam_id}")

class MatchJSONParser:
    """Parser for IOSoccer match JSON files."""
    
    # Position definitions by format
    POSITIONS_5V5 = ['GK', 'CB', 'LM', 'RM', 'CF']
    POSITIONS_6V6 = ['GK', 'LB', 'RB', 'CM', 'LW', 'RW']
    POSITIONS_8V8 = ['GK', 'LB', 'CB', 'RB', 'LM', 'CM', 'RM', 'CF']
    
    # Minimum players required (including GK)
    MIN_PLAYERS_5V5 = 9   # 5v5 = 10 players minimum (5 per side)
    MIN_PLAYERS_6V6 = 10  # 6v6 = 12 players minimum (6 per side)
    MIN_PLAYERS_8V8 = 14  # 8v8 = 16 players minimum (8 per side)
    
    def __init__(self, json_path: str):
        self.json_path = Path(json_path)
        self.data: Dict[str, Any] = {}
        self.match_info: Dict[str, Any] = {}
        self.players: List[Dict[str, Any]] = []
        self.teams: Dict[str, Any] = {}
        self.format: int = 0
        self.stat_types: List[str] = []
        
    def parse(self) -> bool:
        """Parse the JSON file and extract match data."""
        try:
            with open(self.json_path, 'r', encoding='utf-8') as f:
                self.data = json.load(f)
            
            match_data = self.data.get('matchData', {})
            self.match_info = match_data.get('matchInfo', {})
            self.format = self.match_info.get('format', 0)
            self.stat_types = match_data.get('statisticTypes', [])
            
            # Extract team data
            teams = match_data.get('teams', [])
            for team in teams:
                side = team.get('matchTotal', {}).get('side', '')
                if side:
                    self.teams[side] = team
            
            # Extract player data
            self.players = match_data.get('players', [])
            
            return True
        except Exception as e:
            print(f"Error parsing JSON file {self.json_path}: {e}")
            return False
    
    def get_match_date(self) -> Optional[datetime]:
        """Get the match start date/time."""
        start_time = self.match_info.get('startTime')
        if start_time:
            return datetime.utcfromtimestamp(start_time)
        return None
    
    def get_positions_for_format(self) -> List[str]:
        """Get the correct positions list for the match format."""
        if self.format == 6:
            return self.POSITIONS_6V6
        elif self.format == 8:
            return self.POSITIONS_8V8
        elif self.format == 5:
            return self.POSITIONS_5V5
        else:
            # Default to 6v6 positions if unknown
            return None
    
    def validate_match_start(self) -> Tuple[bool, str]:
        """Validate that the match had proper starting conditions.
        
        Returns:
            Tuple of (is_valid, reason)
        """
        # Get lineup at kickoff (t=0)
        kickoff_lineup = self._get_lineup_at_time(0)
        
        # Check minimum players
        if self.format == 5:
            min_required = self.MIN_PLAYERS_5V5
        elif self.format == 6:
            min_required = self.MIN_PLAYERS_6V6
        elif self.format == 8:
            min_required = self.MIN_PLAYERS_8V8

        total_players = len(kickoff_lineup['home']) + len(kickoff_lineup['away'])
        
        if total_players < min_required:
            return False, f"Insufficient players at kickoff: {total_players}/{min_required} required"
        
        # Check for at least one goalkeeper total (either team)
        home_has_gk = any(p['position'] == 'GK' for p in kickoff_lineup['home'])
        away_has_gk = any(p['position'] == 'GK' for p in kickoff_lineup['away'])
        
        if not home_has_gk and not away_has_gk:
            return False, "No goalkeeper found at kickoff (at least 1 required)"
        
        return True, "Valid match start"
    
    def _get_lineup_at_time(self, time_seconds: int) -> Dict[str, List[Dict[str, str]]]:
        """Get the lineup (players on field) at a specific time.
        
        Args:
            time_seconds: Time in seconds from match start
            
        Returns:
            Dict with 'home' and 'away' keys, each containing list of players
        """
        lineup = {'home': [], 'away': []}
        
        for player_data in self.players:
            info = player_data.get('info', {})
            steam_id = convert_steam_id(info.get('steamId', ''))
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

def parse_match_json(json_data: dict) -> Optional[Dict[str, Any]]:
    """Parse match JSON file and extract all relevant data.
    
    Args:
        json_data: Parsed JSON dict from match file
    
    Returns:
        Dict with match data, player stats, and metadata
    """
    try:
        match_data = json_data.get('matchData', {})
        
        if not match_data:
            logger.warning("No matchData found in JSON")
            return None
        
        match_info = match_data.get('matchInfo', {})
        game_format = match_info.get('format')
        teams = match_data.get('teams', [])
        players = match_data.get('players', [])
        events = match_data.get('matchEvents', [])
        
        if len(teams) < 2:
            logger.warning("Less than 2 teams found in match")
            return None
        
        home_team = teams[0]['matchTotal']
        away_team = teams[1]['matchTotal']
        
        match_type = match_info.get('type', 'Unknown')
        start_time = match_info.get('startTime')
        end_time = match_info.get('endTime')
        server_name = match_info.get('serverName', 'Unknown')
        map_name = match_info.get('mapName', 'Unknown')
        
        match_datetime = None
        if start_time:
            match_datetime = datetime.utcfromtimestamp(start_time)
        
        home_score = home_team['statistics'][12]
        away_score = away_team['statistics'][12]
        
        parsed_players = []
        for player in players:
            player_info = player.get('info', {})
            steam_id = convert_steam_id(player_info.get('steamId', ''))
            player_name = player_info.get('name', 'Unknown')
            
            if not steam_id:
                continue
            
            match_periods = player.get('matchPeriodData', [])
            
            total_stats = aggregate_player_stats(match_periods)
            
            if total_stats:
                parsed_players.append({
                    'steam_id': steam_id,
                    'name': player_name,
                    'stats': total_stats
                })
        
        # Detect extratime and penalties
        periods = match_info.get('periods', 0)
        last_period_name = match_info.get('lastPeriodName', '').upper()
        
        # Penalties detection: either period name contains PENALTIES or we have 5+ periods
        has_penalties = 'PENALTIES' in last_period_name or periods >= 5
        
        # Extratime detection: more than 2 regular periods (but not just penalties)
        # If penalties occurred, extratime also occurred (periods 3-4 before penalties in period 5)
        has_extratime = periods > 2
        
        # Detect substitutions
        substitutions = detect_substitutions(json_data)
        
        return {
            'match_type': match_type,
            'datetime': match_datetime,
            'server_name': server_name,
            'map_name': map_name,
            'home_team': home_team['name'],
            'away_team': away_team['name'],
            'game_format': game_format,
            'home_score': home_score,
            'away_score': away_score,
            'home_side': home_team['side'],
            'away_side': away_team['side'],
            'players': parsed_players,
            'events': events,
            'duration': (end_time - start_time) if end_time and start_time else 0,
            'extratime': has_extratime,
            'penalties': has_penalties,
            'substitutions': substitutions
        }
    
    except Exception as e:
        logger.error(f"Error parsing match JSON: {e}", exc_info=True)
        return None


def aggregate_player_stats(match_periods: List[dict]) -> Optional[Dict[str, Any]]:
    """Aggregate player statistics across all match periods.
    
    Args:
        match_periods: List of match period data for a player
    
    Returns:
        Dict with aggregated statistics
    """
    if not match_periods:
        return None

    stat_names = [
        'redCards', 'yellowCards', 'fouls', 'foulsSuffered',
        'slidingTackles', 'slidingTacklesCompleted', 'goalsConceded',
        'shots', 'shotsOnGoal', 'passesCompleted', 'interceptions',
        'offsides', 'goals', 'ownGoals', 'assists', 'passes',
        'freeKicks', 'penalties', 'corners', 'throwIns',
        'keeperSaves', 'goalKicks', 'possession', 'distanceCovered',
        'keeperSavesCaught', 'keyPasses', 'chancesCreated', 'secondAssists'
    ]
    
    aggregated = {name: 0 for name in stat_names}
    
    total_time = 0
    positions_played = {}
    last_team = None
    
    for period in match_periods:
        period_info = period.get('info', {})
        stats = period.get('statistics', [])
        
        if len(stats) != len(stat_names):
            continue
        
        for i, stat_name in enumerate(stat_names):
            aggregated[stat_name] += stats[i]
        
        start = period_info.get('startSecond', 0)
        end = period_info.get('endSecond', 0)
        time_played = end - start
        total_time += time_played
        
        position = period_info.get('position', 'Unknown')
        if position not in positions_played:
            positions_played[position] = 0
        positions_played[position] += time_played
        
        last_team = period_info.get('team')
    
    primary_position = max(positions_played.items(), key=lambda x: x[1])[0] if positions_played else 'Unknown'
    
    aggregated['time_played'] = total_time
    aggregated['position'] = primary_position
    aggregated['team'] = last_team
    aggregated['positions_played'] = positions_played
    
    return aggregated


def extract_team_players(json_data: dict, team_side: str) -> List[str]:
    """Extract Steam IDs of all players on a specific team.
    
    Args:
        json_data: Parsed JSON dict
        team_side: 'home' or 'away'
    
    Returns:
        List of Steam IDs
    """
    players = json_data.get('matchData', {}).get('players', [])
    team_players = []
    
    for player in players:
        player_info = player.get('info', {})
        steam_id = convert_steam_id(player_info.get('steamId', ''))
        
        if not steam_id:
            continue
        
        match_periods = player.get('matchPeriodData', [])
        
        for period in match_periods:
            period_info = period.get('info', {})
            if period_info.get('team') == team_side:
                team_players.append(steam_id)
                break
    
    return team_players


def get_match_id_from_filename(filename: str) -> str:
    """Extract match ID from filename.
    
    Filename format: YYYY.MM.DD_HHhMMmSSs_teamA-vs-teamB_score1-score2.json
    
    Args:
        filename: Match JSON filename
    
    Returns:
        Match ID string
    """
    return filename.replace('.json', '')


def detect_substitutions(json_data: dict) -> List[Dict[str, Any]]:
    """Detect substitutions from match period data.
    
    Analyzes player position changes over time to identify when players
    enter or leave the match (substitutions).
    
    Args:
        json_data: Parsed JSON dict from match file
        
    Returns:
        List of substitution events with format:
        [{
            'time': seconds,
            'team': 'home' or 'away',
            'player_in': {'steam_id': str, 'name': str, 'position': str},
            'player_out': {'steam_id': str, 'name': str, 'position': str}
        }]
    """
    match_data = json_data.get('matchData', {})
    players_data = match_data.get('players', [])
    
    # Track player presence by team and time
    team_timeline = {'home': {}, 'away': {}}  # {time: {position: steam_id}}
    player_info = {}  # {steam_id: name}
    
    # Build timeline of player positions
    for player_entry in players_data:
        info = player_entry.get('info', {})
        steam_id = convert_steam_id(info.get('steamId', ''))
        name = info.get('name', 'Unknown')
        
        if not steam_id:
            continue
            
        player_info[steam_id] = name
        
        for period in player_entry.get('matchPeriodData', []):
            period_info = period.get('info', {})
            start = period_info.get('startSecond', 0)
            end = period_info.get('endSecond', 0)
            team = period_info.get('team', '')
            position = period_info.get('position', '')
            
            if team not in ['home', 'away']:
                continue
            
            # Record player at start and end times
            for t in [start, end]:
                if t not in team_timeline[team]:
                    team_timeline[team][t] = {}
                team_timeline[team][t][position] = steam_id
    
    # Detect substitutions by comparing consecutive time snapshots
    substitutions = []
    
    for team in ['home', 'away']:
        times = sorted(team_timeline[team].keys())
        
        for i in range(1, len(times)):
            prev_time = times[i - 1]
            curr_time = times[i]
            
            if curr_time == 0 or curr_time <= 10:  # Skip kickoff period
                continue
            
            prev_players = set(team_timeline[team][prev_time].values())
            curr_players = set(team_timeline[team][curr_time].values())
            
            players_out = prev_players - curr_players
            players_in = curr_players - prev_players
            
            # Match ins and outs by position if possible
            if players_out and players_in:
                for steam_out in players_out:
                    # Find position of outgoing player
                    pos_out = None
                    for pos, sid in team_timeline[team][prev_time].items():
                        if sid == steam_out:
                            pos_out = pos
                            break
                    
                    # Find incoming player for same position
                    steam_in = None
                    for pos, sid in team_timeline[team][curr_time].items():
                        if sid in players_in and pos == pos_out:
                            steam_in = sid
                            break
                    
                    if steam_in:
                        substitutions.append({
                            'time': curr_time,
                            'team': team,
                            'player_out': {
                                'steam_id': steam_out,
                                'name': player_info.get(steam_out, 'Unknown'),
                                'position': pos_out
                            },
                            'player_in': {
                                'steam_id': steam_in,
                                'name': player_info.get(steam_in, 'Unknown'),
                                'position': pos_out
                            }
                        })
                        players_in.remove(steam_in)
    
    return substitutions


def build_enhanced_player_data(json_data: dict) -> Dict[str, Dict[str, Any]]:
    """Build enhanced player data with aggregated stats by team.
    
    Based on temp_parser.py logic - provides complete player statistics
    aggregated by team (home/away/overall) with position tracking.
    
    Args:
        json_data: Parsed JSON dict from match file
        
    Returns:
        Dict mapping steam_id to player data with:
        - statsByTeam: {home:{...}, away:{...}, overall:{...}}
        - positionSecondsByTeam: {home:{pos:secs}, away:{pos:secs}, overall:{pos:secs}}
        - mainPositionOverall, mainPositionByTeam
        - teamsPlayedFor, started, firstAppearanceTime
    """
    match_data = json_data.get('matchData', {})
    stat_types = match_data.get('statisticTypes', [])
    players_data = match_data.get('players', [])
    
    players = {}
    empty_stats = lambda: {k: 0 for k in stat_types}
    
    for player_entry in players_data:
        info = player_entry.get('info', {})
        steam_id = convert_steam_id(info.get('steamId', ''))
        name = info.get('name', 'Unknown')
        
        if not steam_id:
            continue
        
        if steam_id not in players:
            players[steam_id] = {
                'playerId': steam_id,
                'name': name,
                'teamsPlayedFor': set(),
                'statsByTeam': {
                    'home': empty_stats(),
                    'away': empty_stats(),
                    'overall': empty_stats(),
                },
                'positionSecondsByTeam': {
                    'home': defaultdict(int),
                    'away': defaultdict(int),
                    'overall': defaultdict(int),
                },
                'started': False,
                'firstAppearanceTime': None,
                'mainPositionByTeam': {'home': None, 'away': None},
                'mainPositionOverall': None,
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
            
            players[steam_id]['teamsPlayedFor'].add(team)
            
            # Track first appearance
            if players[steam_id]['firstAppearanceTime'] is None or start < players[steam_id]['firstAppearanceTime']:
                players[steam_id]['firstAppearanceTime'] = start
            
            # Check if started (present at kickoff within 10 seconds)
            if start < 10 and end > 0:
                players[steam_id]['started'] = True
            
            # Track time on position
            secs = max(0, end - start)
            players[steam_id]['positionSecondsByTeam'][team][position] += secs
            players[steam_id]['positionSecondsByTeam']['overall'][position] += secs
            
            # Aggregate stats
            for i, stat_name in enumerate(stat_types):
                val = int(stats[i]) if i < len(stats) else 0
                players[steam_id]['statsByTeam'][team][stat_name] += val
                players[steam_id]['statsByTeam']['overall'][stat_name] += val
    
    # Compute main positions
    for steam_id, p in players.items():
        for team in ['home', 'away']:
            pos_map = p['positionSecondsByTeam'][team]
            if pos_map:
                best = sorted(pos_map.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                p['mainPositionByTeam'][team] = best
        
        overall_map = p['positionSecondsByTeam']['overall']
        if overall_map:
            best_overall = sorted(overall_map.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            p['mainPositionOverall'] = best_overall
        
        # Convert sets/defaultdicts to normal types
        p['teamsPlayedFor'] = sorted(list(p['teamsPlayedFor']))
        p['positionSecondsByTeam']['home'] = dict(p['positionSecondsByTeam']['home'])
        p['positionSecondsByTeam']['away'] = dict(p['positionSecondsByTeam']['away'])
        p['positionSecondsByTeam']['overall'] = dict(p['positionSecondsByTeam']['overall'])
    
    return players

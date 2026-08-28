"""JSON match file parser.

Parses match JSON files from game servers to extract player and match statistics.
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
import json, re
import math
from pathlib import Path
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
    MIN_PLAYERS_5V5 = 8   # 5v5 = 10 players minimum (5 per side)
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
        events = match_data.get('matchEvents') or match_data.get('events') or json_data.get('matchEvents') or []
        
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
        
        # Build richer lineup/substitution payload.
        lineup_payload = parse_lineups_and_substitutions(json_data)
        substitutions = lineup_payload.get("substitutions", []) or []

        # Build summaries and derived metrics for downstream persistence.
        summaries_payload = build_match_summaries_and_derived(json_data, lineup_payload=lineup_payload)
        possession_pct = compute_player_possession_percent(json_data)
        player_derived = summaries_payload.get("player_derived", {}) or {}
        for sid in set(list(player_derived.keys()) + list(possession_pct.keys())):
            row = player_derived.setdefault(sid, {})
            row["possession"] = possession_pct.get(sid, 0.0)

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
            'events': events if isinstance(events, list) else [],
            'duration': (end_time - start_time) if end_time and start_time else 0,
            'extratime': has_extratime,
            'penalties': has_penalties,
            'substitutions': substitutions,
            'lineup_analysis': lineup_payload,
            'match_summary_home': summaries_payload.get("match_summary_home", []),
            'match_summary_away': summaries_payload.get("match_summary_away", []),
            'comeback_flag': summaries_payload.get("comeback_flag", False),
            'player_derived': player_derived,
        }
    
    except Exception as e:
        logger.error(f"Error parsing match JSON: {e}", exc_info=True)
        return None


def _safe_convert_steam_id(raw_steam_id: Any) -> Optional[str]:
    """Convert Steam ID safely; return None for invalid/empty values."""
    raw = str(raw_steam_id or "").strip()
    if not raw:
        return None
    try:
        return convert_steam_id(raw)
    except Exception:
        return None


def _coerce_numeric(value: Any) -> Optional[float]:
    """Best-effort numeric conversion for event clock values."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None

    # "74'" or "74m" -> 74
    simple = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*['mM]?", text)
    if simple:
        try:
            return float(simple.group(1))
        except Exception:
            return None

    return None


def _event_to_minute(event: dict) -> Optional[int]:
    """Resolve event minute from mixed schemas (seconds, minute, clock text)."""
    if not isinstance(event, dict):
        return None

    second_keys = (
        "second",
        "seconds",
        "elapsedSecond",
        "elapsedSeconds",
        "gameSecond",
        "matchSecond",
        "timeSeconds",
    )
    minute_keys = ("minute", "min", "matchMinute", "gameMinute")

    for key in second_keys:
        sec_val = _coerce_numeric(event.get(key))
        if sec_val is None:
            continue
        sec_int = int(sec_val)
        if sec_int < 0:
            return None
        return max(1, sec_int // 60)

    for key in minute_keys:
        minute_val = _coerce_numeric(event.get(key))
        if minute_val is None:
            continue
        minute_int = int(minute_val)
        if minute_int < 0:
            return None
        return max(1, minute_int)

    # Some feeds only expose a generic "time". Handle both "MM:SS" and numeric.
    raw_time = event.get("time")
    if isinstance(raw_time, str):
        raw_time = raw_time.strip()
        if raw_time:
            # "74:30" or "90+3:10" -> minute component
            if ":" in raw_time:
                minute_part = raw_time.split(":", 1)[0].strip()
                extra_match = re.fullmatch(r"(\d+)\s*\+\s*(\d+)", minute_part)
                if extra_match:
                    minute = int(extra_match.group(1)) + int(extra_match.group(2))
                    return max(1, minute)
                minute_num = _coerce_numeric(minute_part)
                if minute_num is not None:
                    return max(1, int(minute_num))

            time_num = _coerce_numeric(raw_time)
            if time_num is not None:
                # Heuristic: high values are probably seconds, low are minutes.
                if time_num > 180:
                    return max(1, int(time_num) // 60)
                return max(1, int(time_num))

    return None


def _period_base_seconds(period_name: Optional[str]) -> int:
    """Baseline second offset for continuous match clock mapping."""
    p = str(period_name or "").upper()
    if p == "FIRST HALF":
        return 0
    if p == "SECOND HALF":
        return 45 * 60
    if p == "EXTRA TIME FIRST HALF":
        return 90 * 60
    if p == "EXTRA TIME SECOND HALF":
        return 105 * 60
    if p == "PENALTIES":
        return 120 * 60
    return 0


def _build_match_clock_mapper(events: List[dict]) -> Dict[str, int]:
    """Infer per-period raw second starting points from event feed."""
    starts: Dict[str, int] = {"FIRST HALF": 0}
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        period = str(ev.get("period") or "").upper()
        if not period:
            continue
        sec = _safe_int(ev.get("second"), None)
        if sec is None:
            continue
        if period not in starts:
            starts[period] = sec
        else:
            starts[period] = min(starts[period], sec)
    return starts


def _clock_payload(raw_second: int, period: Optional[str], period_starts: Dict[str, int]) -> Dict[str, Any]:
    """Convert period-local seconds into continuous match clock payload."""
    period_name = str(period or "").upper()
    base = _period_base_seconds(period_name)
    raw_second_int = max(0, _safe_int(raw_second))

    # Newer IOSoccer feeds expose absolute match seconds even when a period label is
    # present. Older/local feeds can expose seconds relative to the period start.
    if base > 0 and raw_second_int < base:
        match_second = raw_second_int + base
    else:
        match_second = raw_second_int

    mins, secs = divmod(match_second, 60)
    return {
        "second": int(match_second),
        "minute": float(round(match_second / 60.0, 2)),
        "clock": f"{mins:02d}:{secs:02d}",
        "period": period_name or None,
    }


def _round_minute_from_second(match_second: int) -> int:
    """Round to minute with +1 only when seconds are strictly over 30."""
    sec = max(0, _safe_int(match_second))
    base_minute = sec // 60
    remainder = sec % 60
    if remainder > 30:
        base_minute += 1
    return int(base_minute)


def _event_name_key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").strip().upper())


def _field_bounds_payload(match_info: Dict[str, Any]) -> Optional[Dict[str, float]]:
    field_min = match_info.get("fieldMin") or {}
    field_max = match_info.get("fieldMax") or {}
    if not isinstance(field_min, dict) or not isinstance(field_max, dict):
        return None

    min_x = _safe_float(field_min.get("x"), None)
    min_y = _safe_float(field_min.get("y"), None)
    max_x = _safe_float(field_max.get("x"), None)
    max_y = _safe_float(field_max.get("y"), None)
    if None in {min_x, min_y, max_x, max_y}:
        return None
    if max_x == min_x or max_y == min_y:
        return None

    return {
        "min_x": float(min_x),
        "min_y": float(min_y),
        "max_x": float(max_x),
        "max_y": float(max_y),
    }


def _event_location_payload(event: dict, field_bounds: Optional[Dict[str, float]]) -> Dict[str, Any]:
    start_pos = (event or {}).get("startPosition") or {}
    if not isinstance(start_pos, dict):
        return {}

    x = _safe_float(start_pos.get("x"), None)
    y = _safe_float(start_pos.get("y"), None)
    if x is None or y is None:
        return {}

    payload: Dict[str, Any] = {
        "x": float(x),
        "y": float(y),
    }
    if field_bounds:
        width = field_bounds["max_x"] - field_bounds["min_x"]
        height = field_bounds["max_y"] - field_bounds["min_y"]
        norm_x = (float(x) - field_bounds["min_x"]) / width
        norm_y = (float(y) - field_bounds["min_y"]) / height
        payload.update(
            {
                "norm_x": round(max(0.0, min(1.0, norm_x)), 6),
                "norm_y": round(max(0.0, min(1.0, norm_y)), 6),
                "field_min_x": field_bounds["min_x"],
                "field_min_y": field_bounds["min_y"],
                "field_max_x": field_bounds["max_x"],
                "field_max_y": field_bounds["max_y"],
            }
        )
    return payload


def _canonical_match_event_type(event_name: Any) -> Optional[str]:
    event_key = _event_name_key(event_name)
    if event_key == "GOAL":
        return "goal"
    if event_key in {"OWNGOAL"}:
        return "own_goal"
    if event_key in {"MISS", "SHOTMISS", "MISSEDSHOT"}:
        return "miss"
    if event_key == "SAVE":
        return "save"
    if event_key in {"YELLOW", "YELLOWCARD"}:
        return "yellow"
    if event_key in {"SECONDYELLOW", "SECONDYELLOWCARD"}:
        return "second_yellow"
    if event_key in {"RED", "REDCARD"} or ("RED" in event_key and "CARD" in event_key):
        return "red"
    return None


def build_match_event_locations(json_data: dict) -> List[Dict[str, Any]]:
    """Build one-row-per-event location payload for field visualizations."""
    match_data = json_data.get("matchData", {}) or {}
    match_info = match_data.get("matchInfo", {}) or {}
    events = match_data.get("matchEvents") or match_data.get("events") or json_data.get("matchEvents") or []
    if not isinstance(events, list):
        return []

    field_bounds = _field_bounds_payload(match_info)
    period_starts = _build_match_clock_mapper(events)
    output: List[Dict[str, Any]] = []

    for idx, event in enumerate(events):
        if not isinstance(event, dict):
            continue

        raw_event = str(event.get("event") or event.get("type") or "").strip()
        event_type = _canonical_match_event_type(raw_event)
        if not event_type:
            continue

        raw_second = _safe_int(event.get("second"), None)
        clock = _clock_payload(raw_second, event.get("period"), period_starts) if raw_second is not None else None
        minute = _round_minute_from_second(clock["second"]) if clock else _event_to_minute(event)
        location = _event_location_payload(event, field_bounds)

        p1 = _safe_convert_steam_id(event.get("player1SteamId", event.get("playerSteamId")))
        p2 = _safe_convert_steam_id(event.get("player2SteamId", event.get("assistSteamId")))
        p3 = _safe_convert_steam_id(event.get("player3SteamId"))

        output.append(
            {
                "event_index": idx,
                "event_type": event_type,
                "raw_event": raw_event,
                "team": str(event.get("team") or "").strip().lower() or None,
                "period": clock.get("period") if clock else str(event.get("period") or "").strip().upper() or None,
                "raw_second": raw_second,
                "match_second": clock.get("second") if clock else None,
                "minute": minute,
                "clock": clock.get("clock") if clock else None,
                "player1_steam_id": p1,
                "player2_steam_id": p2,
                "player3_steam_id": p3,
                "body_part": _safe_int(event.get("bodyPart"), None),
                "x": location.get("x"),
                "y": location.get("y"),
                "norm_x": location.get("norm_x"),
                "norm_y": location.get("norm_y"),
                "field_min_x": location.get("field_min_x"),
                "field_min_y": location.get("field_min_y"),
                "field_max_x": location.get("field_max_x"),
                "field_max_y": location.get("field_max_y"),
                "raw_event_payload": event,
            }
        )

    return output


def build_player_event_timestamps(match_events: List[dict]) -> Dict[str, Dict[str, List[int]]]:
    """Build per-player event minute map from match events.

    Returns:
        {
            "STEAM_0:X:Y": {
                "goal": [17, 37],
                "assist": [25],
                "yellow": [29],
                "red": [88],
                "save": [62]
            }
        }
    """
    per_player: Dict[str, Dict[str, List[int]]] = defaultdict(lambda: defaultdict(list))
    period_starts = _build_match_clock_mapper(match_events or [])

    for event in match_events or []:
        if not isinstance(event, dict):
            continue

        event_name_raw = str(event.get("event") or event.get("type") or "").strip()
        event_name = event_name_raw.upper()
        event_key = re.sub(r"[^A-Z0-9]+", "", event_name)
        raw_second = _safe_int(event.get("second"), None)
        if raw_second is not None:
            clock = _clock_payload(raw_second, event.get("period"), period_starts)
            minute = _round_minute_from_second(clock["second"])
        else:
            minute = _event_to_minute(event)
        if not event_name or minute is None:
            continue

        player1 = _safe_convert_steam_id(
            event.get("player1SteamId", event.get("playerSteamId"))
        )
        player2 = _safe_convert_steam_id(
            event.get("player2SteamId", event.get("assistSteamId"))
        )
        player3 = _safe_convert_steam_id(event.get("player3SteamId"))

        if event_key == "GOAL":
            if player1:
                per_player[player1]["goal"].append(minute)
            if player2 and player2 != player1:
                per_player[player2]["assist"].append(minute)
            if player3 and player3 not in {player1, player2}:
                per_player[player3]["second_assist"].append(minute)
            continue

        if event_key == "ASSIST":
            if player1:
                per_player[player1]["assist"].append(minute)
            continue

        if event_key in {"YELLOWCARD", "YELLOW"}:
            if player1:
                per_player[player1]["yellow"].append(minute)
            continue

        red_event_keys = {"REDCARD", "RED"}
        if event_key in red_event_keys or ("RED" in event_key and "CARD" in event_key):
            if player1:
                per_player[player1]["red"].append(minute)
            continue

        if event_key in {"SECONDYELLOW", "SECONDYELLOWCARD"}:
            if player1:
                per_player[player1]["second_yellow"].append(minute)
                per_player[player1]["yellow"].append(minute)
                per_player[player1]["red"].append(minute)
            continue

        if event_key == "SAVE":
            if player1:
                per_player[player1]["save"].append(minute)
            continue

        if event_key in {"OWNGOAL"}:
            if player1:
                per_player[player1]["own_goal"].append(minute)

    normalized: Dict[str, Dict[str, List[int]]] = {}
    for steam_id, event_map in per_player.items():
        normalized[steam_id] = {key: [int(v) for v in values] for key, values in event_map.items() if values}

    return normalized


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


def _extract_player_segments(player_obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract normalized period segments for one player."""
    segments: List[Dict[str, Any]] = []
    for entry in player_obj.get("matchPeriodData", []) or []:
        info = entry.get("info", {}) or {}
        team = info.get("team")
        position = info.get("position")
        if team not in {"home", "away"} or not position:
            continue
        start = _safe_int(info.get("startSecond"), 0)
        end = _safe_int(info.get("endSecond"), 0)
        if end <= start:
            continue
        segments.append(
            {
                "team": team,
                "position": str(position).upper(),
                "start": start,
                "end": end,
            }
        )

    segments.sort(key=lambda s: (s["start"], s["end"]))
    normalized: List[Dict[str, Any]] = []
    prev_end: Optional[int] = None
    for seg in segments:
        st = seg["start"]
        en = seg["end"]
        if prev_end is not None and st < prev_end:
            st = prev_end
        if en <= st:
            continue
        normalized.append({**seg, "start": st, "end": en})
        prev_end = en if prev_end is None else max(prev_end, en)

    merged: List[Dict[str, Any]] = []
    for seg in normalized:
        if not merged:
            merged.append(seg)
            continue
        last = merged[-1]
        # Merge adjacent/overlapping same team+position slices to avoid noisy churn.
        if (
            last["team"] == seg["team"]
            and last["position"] == seg["position"]
            and seg["start"] <= last["end"] + 5
        ):
            last["end"] = max(last["end"], seg["end"])
        else:
            merged.append(seg)
    return merged


def _collect_player_records(json_data: dict) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    players = json_data.get("matchData", {}).get("players", [])
    for player_entry in players:
        info = player_entry.get("info", {}) or {}
        steam_id = _safe_convert_steam_id(info.get("steamId"))
        if not steam_id:
            continue
        records[steam_id] = {
            "player_id": steam_id,
            "steam_id": steam_id,
            "name": info.get("name") or "Unknown",
            "segments": _extract_player_segments(player_entry),
            "raw": player_entry,
        }
    return records


def _build_slot_timeline(records: Dict[str, Dict[str, Any]]) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    """Build occupancy timeline for each (team, position) slot."""
    boundaries: set[int] = set()
    slots: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for rec in records.values():
        for seg in rec.get("segments", []):
            boundaries.add(seg["start"])
            boundaries.add(seg["end"])
            slots[(seg["team"], seg["position"])].append(
                {"player_id": rec["player_id"], **seg}
            )

    if not boundaries:
        return {}

    times = sorted(boundaries)
    positions = sorted({key[1] for key in slots})
    for side in ("home", "away"):
        for pos in positions:
            slots.setdefault((side, pos), [])

    out: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for slot, segs in slots.items():
        intervals: List[Dict[str, Any]] = []
        for i in range(len(times) - 1):
            st = times[i]
            en = times[i + 1]
            if en <= st:
                continue

            contenders: List[Tuple[int, str]] = []
            for seg in segs:
                overlap = min(en, seg["end"]) - max(st, seg["start"])
                if overlap > 0:
                    contenders.append((overlap, seg["player_id"]))
            player = sorted(contenders, reverse=True)[0][1] if contenders else None
            if intervals and intervals[-1]["player_id"] == player:
                intervals[-1]["end"] = en
            else:
                intervals.append({"start": st, "end": en, "player_id": player})
        out[slot] = intervals
    return out


def _classify_player_record(rec: Dict[str, Any], match_start: int, start_grace: int = 30) -> Dict[str, Any]:
    """Classify player participation and team/position allocation."""
    team_seconds: Dict[str, int] = defaultdict(int)
    position_seconds: Dict[str, int] = defaultdict(int)
    starts: List[int] = []
    switches = 0
    last_team = None
    for seg in rec.get("segments", []):
        dur = _safe_int(seg["end"]) - _safe_int(seg["start"])
        if dur <= 0:
            continue
        team_seconds[seg["team"]] += dur
        position_seconds[seg["position"]] += dur
        starts.append(_safe_int(seg["start"]))
        if last_team is not None and seg["team"] != last_team:
            switches += 1
        last_team = seg["team"]

    total = sum(team_seconds.values())
    played = total > 0
    started = played and any(st <= match_start + start_grace for st in starts)
    status = "on_bench" if not played else ("started" if started else "substitute")
    main_team = max(team_seconds, key=team_seconds.get) if team_seconds else None
    main_position = max(position_seconds, key=position_seconds.get) if position_seconds else None

    gk_seconds = position_seconds.get("GK", 0)
    home_seconds = team_seconds.get("home", 0)
    away_seconds = team_seconds.get("away", 0)
    # Suppress GK side-flip noise when one keeper effectively played both sides.
    is_single_keeper = (
        played
        and gk_seconds >= int(0.8 * total)
        and home_seconds > 0
        and away_seconds > 0
        and switches >= 8
    )

    return {
        "status": status,
        "started": started,
        "total_seconds": total,
        "team_seconds": dict(team_seconds),
        "position_seconds": dict(position_seconds),
        "main_team": main_team,
        "main_position": main_position,
        "other_positions": {
            key: val
            for key, val in sorted(position_seconds.items(), key=lambda kv: kv[1], reverse=True)
            if key != main_position
        },
        "played_for_both_teams": home_seconds > 0 and away_seconds > 0,
        "is_single_keeper": is_single_keeper,
    }


def parse_lineups_and_substitutions(json_data: dict) -> Dict[str, Any]:
    """Build initial lineups and richer substitution metadata using slot timeline logic."""
    records = _collect_player_records(json_data)
    all_segments = [seg for rec in records.values() for seg in rec.get("segments", [])]
    if not all_segments:
        return {
            "starting_lineups": {"home": {}, "away": {}},
            "starting_players": {"home": [], "away": []},
            "substitutions": [],
            "players": {},
            "slot_timeline": {},
        }

    match_start = min(seg["start"] for seg in all_segments)
    slot_timeline = _build_slot_timeline(records)
    id_to_name = {pid: rec.get("name") or "Unknown" for pid, rec in records.items()}

    player_meta = {pid: _classify_player_record(rec, match_start) for pid, rec in records.items()}
    single_keeper_ids = {pid for pid, meta in player_meta.items() if meta.get("is_single_keeper")}

    lineups: Dict[str, Dict[str, Optional[Dict[str, str]]]] = {"home": {}, "away": {}}
    starters = {"home": set(), "away": set()}
    substitutions: List[Dict[str, Any]] = []

    for (team, position), intervals in slot_timeline.items():
        if not intervals:
            lineups[team][position] = None
            continue

        first = intervals[0]
        if first.get("player_id"):
            pid = first["player_id"]
            lineups[team][position] = {
                "steam_id": pid,
                "name": id_to_name.get(pid, "Unknown"),
                "position": position,
                "started": True,
            }
            starters[team].add(pid)
        else:
            lineups[team][position] = None

        for i in range(1, len(intervals)):
            prev_i = intervals[i - 1]
            curr_i = intervals[i]
            if prev_i.get("player_id") == curr_i.get("player_id"):
                continue

            out_id = prev_i.get("player_id")
            in_id = curr_i.get("player_id")
            stint = _safe_int(curr_i.get("end")) - _safe_int(curr_i.get("start"))

            if out_id is None and in_id is not None:
                kind = "fill_empty"
            elif out_id is not None and in_id is None:
                kind = "vacated"
            else:
                next_return = None
                for j in range(i + 1, len(intervals)):
                    if intervals[j].get("player_id") == out_id:
                        next_return = intervals[j].get("start")
                        break
                returned_quick = (
                    next_return is not None
                    and (_safe_int(next_return) - _safe_int(curr_i.get("start"))) <= 300
                )
                if stint < 120 and returned_quick:
                    kind = "temporary_swap"
                elif stint < 180:
                    kind = "short_swap"
                else:
                    kind = "proper_sub"

            if position == "GK" and (out_id in single_keeper_ids or in_id in single_keeper_ids):
                continue

            substitutions.append(
                {
                    "time": _safe_int(curr_i.get("start")),
                    "team": team,
                    "position": position,
                    "out_player": {
                        "steam_id": out_id,
                        "name": id_to_name.get(out_id, "Unknown") if out_id else None,
                    },
                    "in_player": {
                        "steam_id": in_id,
                        "name": id_to_name.get(in_id, "Unknown") if in_id else None,
                    },
                    "new_stint_seconds": stint,
                    "kind": kind,
                    "is_position_swap": False,
                    "chain_id": None,
                    "chain_step": None,
                }
            )

    by_team_time: Dict[Tuple[str, int], List[int]] = defaultdict(list)
    for idx, sub in enumerate(substitutions):
        by_team_time[(sub["team"], _safe_int(sub["time"]))].append(idx)
    for idxs in by_team_time.values():
        for i in idxs:
            a = substitutions[i]
            out_a = (a.get("out_player") or {}).get("steam_id")
            in_a = (a.get("in_player") or {}).get("steam_id")
            if not out_a or not in_a:
                continue
            for j in idxs:
                if i == j:
                    continue
                b = substitutions[j]
                out_b = (b.get("out_player") or {}).get("steam_id")
                in_b = (b.get("in_player") or {}).get("steam_id")
                if out_a == in_b and in_a == out_b:
                    substitutions[i]["is_position_swap"] = True
                    substitutions[j]["is_position_swap"] = True

    chain_counter = 1
    for side in ("home", "away"):
        idxs = [
            i
            for i, sub in enumerate(substitutions)
            if sub.get("team") == side
            and (sub.get("out_player") or {}).get("steam_id")
            and (sub.get("in_player") or {}).get("steam_id")
        ]
        idxs.sort(key=lambda i: _safe_int(substitutions[i].get("time")))
        used: set[int] = set()
        for idx in idxs:
            if idx in used:
                continue
            chain = [idx]
            curr_player = (substitutions[idx].get("in_player") or {}).get("steam_id")
            curr_time = _safe_int(substitutions[idx].get("time"))
            for j in idxs:
                if j <= idx:
                    continue
                next_time = _safe_int(substitutions[j].get("time"))
                if next_time - curr_time > 180:
                    break
                out_id = (substitutions[j].get("out_player") or {}).get("steam_id")
                if out_id == curr_player:
                    chain.append(j)
                    curr_player = (substitutions[j].get("in_player") or {}).get("steam_id")
                    curr_time = next_time
            if len(chain) >= 2:
                chain_id = f"chain_{chain_counter}"
                chain_counter += 1
                for step, chain_idx in enumerate(chain, start=1):
                    substitutions[chain_idx]["chain_id"] = chain_id
                    substitutions[chain_idx]["chain_step"] = step
                    used.add(chain_idx)

    players_out = {}
    for pid, rec in records.items():
        players_out[pid] = {
            "steam_id": pid,
            "name": rec.get("name"),
            **player_meta.get(pid, {}),
        }

    starting_players = {
        side: [
            {"steam_id": pid, "name": id_to_name.get(pid, "Unknown")}
            for pid in sorted(players)
        ]
        for side, players in starters.items()
    }

    slot_timeline_export = {
        f"{team}:{position}": intervals for (team, position), intervals in sorted(slot_timeline.items())
    }

    return {
        "starting_lineups": lineups,
        "starting_players": starting_players,
        "substitutions": sorted(
            substitutions,
            key=lambda item: (_safe_int(item.get("time")), item.get("team", ""), item.get("position", "")),
        ),
        "players": players_out,
        "slot_timeline": slot_timeline_export,
    }


def _event_side(event: Dict[str, Any], player_meta: Dict[str, Dict[str, Any]]) -> Optional[str]:
    side = str(event.get("team") or "").lower()
    if side in {"home", "away"}:
        return side
    p1 = _safe_convert_steam_id(event.get("player1SteamId", event.get("playerSteamId")))
    if p1 and player_meta.get(p1):
        return player_meta[p1].get("main_team")
    return None


def compute_player_possession_percent(json_data: dict) -> Dict[str, float]:
    """Compute per-player possession estimate as normalized percent in-match."""
    match_data = json_data.get("matchData", {})
    stat_types = [str(s) for s in (match_data.get("statisticTypes") or [])]
    players = match_data.get("players", [])
    if not players or not stat_types:
        return {}

    idx = None
    for candidate in ("possession", "Possession", "ballPossession"):
        if candidate in stat_types:
            idx = stat_types.index(candidate)
            break
    if idx is None:
        return {}

    raw_by_player: Dict[str, float] = defaultdict(float)
    for player in players:
        info = player.get("info", {}) or {}
        steam_id = _safe_convert_steam_id(info.get("steamId"))
        if not steam_id:
            continue
        for period in player.get("matchPeriodData", []) or []:
            stats = period.get("statistics", []) or []
            if idx < len(stats):
                raw_by_player[steam_id] += _safe_float(stats[idx], 0.0)

    total = sum(v for v in raw_by_player.values() if v > 0)
    if total <= 0:
        return {sid: 0.0 for sid in raw_by_player}
    return {sid: round((value / total) * 100.0, 2) for sid, value in raw_by_player.items()}


def compute_player_shot_metrics(json_data: dict) -> Dict[str, Dict[str, Optional[float]]]:
    """Compute per-player shot distance/angle averages.

    Priority:
    1) Statistic-type indices (when provided by the match payload).
    2) Fallback from shot event start positions in match events.
    """
    match_data = json_data.get("matchData", {})
    stat_types = [str(s) for s in (match_data.get("statisticTypes") or [])]
    players = match_data.get("players", [])
    events = (
        match_data.get("matchEvents")
        or match_data.get("events")
        or json_data.get("matchEvents")
        or []
    )

    def _from_events() -> Dict[str, Dict[str, Optional[float]]]:
        info = match_data.get("matchInfo", {}) or {}
        fmin = info.get("fieldMin", {}) or {}
        fmax = info.get("fieldMax", {}) or {}
        min_y = _safe_float(fmin.get("y"), -2000.0)
        max_y = _safe_float(fmax.get("y"), 2000.0)

        accum_events: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {"dist_sum": 0.0, "angle_sum": 0.0, "count": 0.0}
        )

        for ev in (events or []):
            ev_key = _event_name_key((ev or {}).get("event") or (ev or {}).get("type"))
            if ev_key not in {"GOAL", "MISS", "OWNGOAL", "SAVE", "SHOT", "BLOCKEDSHOT"}:
                continue

            if ev_key == "SAVE":
                raw_sid = ev.get("player2SteamId") or ev.get("playerSteamId")
            else:
                raw_sid = ev.get("player1SteamId") or ev.get("playerSteamId")
            sid = _safe_convert_steam_id(raw_sid)
            if not sid:
                continue

            start_pos = (ev or {}).get("startPosition") or {}
            if not isinstance(start_pos, dict):
                continue
            if "x" not in start_pos or "y" not in start_pos:
                continue

            x = _safe_float(start_pos.get("x"), None)
            y = _safe_float(start_pos.get("y"), None)
            if x is None or y is None:
                continue

            goal_y = max_y if abs(max_y - y) <= abs(min_y - y) else min_y
            dx = abs(x)
            dy = abs(goal_y - y)
            dist = math.sqrt((dx * dx) + (dy * dy))
            angle_deg = math.degrees(math.atan2(dx, max(dy, 1e-9)))

            accum_events[sid]["dist_sum"] += dist
            accum_events[sid]["angle_sum"] += angle_deg
            accum_events[sid]["count"] += 1.0

        out_events: Dict[str, Dict[str, Optional[float]]] = {}
        for sid, row in accum_events.items():
            cnt = row.get("count", 0.0)
            if cnt <= 0:
                continue
            out_events[sid] = {
                "shot_distance_avg": round(row["dist_sum"] / cnt, 2),
                "shot_angle_avg": round(row["angle_sum"] / cnt, 2),
            }
        return out_events

    # If stat table is unavailable, fallback entirely to event geometry.
    if not players or not stat_types:
        return _from_events()

    def _idx(candidates: List[str]) -> Optional[int]:
        for cand in candidates:
            if cand in stat_types:
                return stat_types.index(cand)
        return None

    shots_idx = _idx(["shots", "Shots"])
    dist_idx = _idx(["shotDistanceAvg", "shot_distance_avg", "shotsDistanceAvg", "shotDistance"])
    angle_idx = _idx(["shotAngleAvg", "shot_angle_avg", "shotsAngleAvg", "shotAngle"])

    if dist_idx is None and angle_idx is None:
        return _from_events()

    accum: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {
            "dist_weighted_sum": 0.0,
            "dist_weight": 0.0,
            "angle_weighted_sum": 0.0,
            "angle_weight": 0.0,
        }
    )

    for player in players:
        info = player.get("info", {}) or {}
        steam_id = _safe_convert_steam_id(info.get("steamId"))
        if not steam_id:
            continue

        for period in player.get("matchPeriodData", []) or []:
            stats = period.get("statistics", []) or []
            shots = 0.0
            if shots_idx is not None and shots_idx < len(stats):
                shots = max(0.0, _safe_float(stats[shots_idx], 0.0))
            weight = shots if shots > 0 else 1.0

            if dist_idx is not None and dist_idx < len(stats):
                dist_val = _safe_float(stats[dist_idx], None)
                if dist_val is not None:
                    accum[steam_id]["dist_weighted_sum"] += dist_val * weight
                    accum[steam_id]["dist_weight"] += weight
            if angle_idx is not None and angle_idx < len(stats):
                angle_val = _safe_float(stats[angle_idx], None)
                if angle_val is not None:
                    accum[steam_id]["angle_weighted_sum"] += angle_val * weight
                    accum[steam_id]["angle_weight"] += weight

    out: Dict[str, Dict[str, Optional[float]]] = {}
    for sid, data in accum.items():
        dist_avg = (
            round(data["dist_weighted_sum"] / data["dist_weight"], 2)
            if data["dist_weight"] > 0
            else None
        )
        angle_avg = (
            round(data["angle_weighted_sum"] / data["angle_weight"], 2)
            if data["angle_weight"] > 0
            else None
        )
        out[sid] = {
            "shot_distance_avg": dist_avg,
            "shot_angle_avg": angle_avg,
        }

    # Patch nulls with event-derived geometry when available.
    fallback = _from_events()
    for sid, geo in fallback.items():
        base = out.setdefault(sid, {"shot_distance_avg": None, "shot_angle_avg": None})
        if base.get("shot_distance_avg") is None:
            base["shot_distance_avg"] = geo.get("shot_distance_avg")
        if base.get("shot_angle_avg") is None:
            base["shot_angle_avg"] = geo.get("shot_angle_avg")

    return out


def build_match_summaries_and_derived(json_data: dict, lineup_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build side summaries + derived flags (comeback, clutch, sub impact)."""
    match_data = json_data.get("matchData", {})
    events = sorted(
        match_data.get("matchEvents", []) or match_data.get("events", []) or [],
        key=lambda e: _safe_int((e or {}).get("second"), 0),
    )
    period_starts = _build_match_clock_mapper(events)
    player_meta = (lineup_payload or {}).get("players", {}) if lineup_payload else {}

    players_data = match_data.get("players", []) or []
    player_names = {}
    for p in players_data:
        info = p.get("info", {}) or {}
        sid = _safe_convert_steam_id(info.get("steamId"))
        if sid:
            player_names[sid] = info.get("name") or "Unknown"

    teams = match_data.get("teams", []) or []
    home_name = teams[0].get("matchTotal", {}).get("name", "Home") if len(teams) > 0 else "Home"
    away_name = teams[1].get("matchTotal", {}).get("name", "Away") if len(teams) > 1 else "Away"

    def _score_from_team(idx_team: int) -> int:
        try:
            stats = teams[idx_team].get("matchTotal", {}).get("statistics", []) or []
            return _safe_int(stats[12], 0)
        except Exception:
            return 0

    final_home = _score_from_team(0)
    final_away = _score_from_team(1)

    summary_players: Dict[str, Dict[str, Dict[str, Any]]] = {
        "home": {},
        "away": {},
    }
    per_player_clutch: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    per_player_sub_impact: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"window_seconds": 300, "events": [], "summary": defaultdict(int)}
    )

    non_penalty_seconds = []
    for ev in events:
        period = str(ev.get("period") or "").upper()
        if period == "PENALTIES":
            continue
        sec = _safe_int(ev.get("second"), None)
        if sec is None:
            continue
        non_penalty_seconds.append(_clock_payload(sec, period, period_starts)["second"])
    clutch_start = 85 * 60

    running_home = 0
    running_away = 0

    sub_events = []
    for sub in (lineup_payload or {}).get("substitutions", []) or []:
        kind = str((sub or {}).get("kind") or "").lower()
        if kind != "proper_sub":
            continue
        in_player = (sub.get("in_player") or {}).get("steam_id")
        team = str(sub.get("team") or "").lower()
        second = _safe_int(sub.get("time"), None)
        if in_player and second is not None and team in {"home", "away"}:
            sub_events.append((in_player, team, second))

    for event in events:
        ev_key = _event_name_key(event.get("event") or event.get("type"))
        if not ev_key:
            continue

        raw_second = _safe_int(event.get("second"), None)
        clock = _clock_payload(raw_second, event.get("period"), period_starts) if raw_second is not None else None
        event_second = _safe_int(clock.get("second"), 0) if clock else 0
        event_minute = _round_minute_from_second(event_second)

        p1 = _safe_convert_steam_id(event.get("player1SteamId", event.get("playerSteamId")))
        p2 = _safe_convert_steam_id(event.get("player2SteamId", event.get("assistSteamId")))

        side = _event_side(event, player_meta)
        scoring_side = side
        if ev_key == "OWNGOAL" and side in {"home", "away"}:
            scoring_side = "away" if side == "home" else "home"

        score_before = {"home": running_home, "away": running_away}
        team_state_before = None
        if scoring_side == "home":
            team_state_before = "drawing" if running_home == running_away else ("losing" if running_home < running_away else "winning")
        elif scoring_side == "away":
            team_state_before = "drawing" if running_away == running_home else ("losing" if running_away < running_home else "winning")

        if ev_key == "GOAL":
            if scoring_side == "home":
                running_home += 1
            elif scoring_side == "away":
                running_away += 1
        elif ev_key == "OWNGOAL":
            if scoring_side == "home":
                running_home += 1
            elif scoring_side == "away":
                running_away += 1

        def _add_summary_player(event_side: Optional[str], steam_id: Optional[str], field_name: str, payload_extra: Optional[Dict[str, Any]] = None):
            if event_side not in {"home", "away"} or not steam_id:
                return
            row = summary_players[event_side].setdefault(
                steam_id,
                {
                    "steam_id": steam_id,
                    "name": player_names.get(steam_id, "Unknown"),
                    "side": event_side,
                    "goals": [],
                    "goal_details": [],
                    "yellow_cards": [],
                    "second_yellow_cards": [],
                    "red_cards": [],
                    "own_goals": [],
                },
            )
            if field_name in {"goals", "yellow_cards", "second_yellow_cards", "red_cards", "own_goals"}:
                row[field_name].append(event_minute)
            if field_name == "goals":
                goal_detail = {
                    "minute": event_minute,
                    "second": event_second,
                    "clock": clock.get("clock") if clock else None,
                    "assist_steam_id": p2,
                    "assist_name": player_names.get(p2, "Unknown") if p2 else None,
                }
                if payload_extra:
                    goal_detail.update(payload_extra)
                row["goal_details"].append(goal_detail)

        if ev_key == "GOAL":
            _add_summary_player(scoring_side, p1, "goals")
        elif ev_key in {"YELLOWCARD", "YELLOW"}:
            _add_summary_player(side, p1, "yellow_cards")
        elif ev_key in {"SECONDYELLOW", "SECONDYELLOWCARD"}:
            _add_summary_player(side, p1, "yellow_cards")
            _add_summary_player(side, p1, "second_yellow_cards")
        if ev_key in {"REDCARD", "RED", "SECONDYELLOW", "SECONDYELLOWCARD"}:
            _add_summary_player(side, p1, "red_cards")
        if ev_key == "OWNGOAL":
            _add_summary_player(side, p1, "own_goals")

        # Sub impact: events within 5 minutes after a player comes in.
        for in_player, in_team, in_second in sub_events:
            if event_second < in_second or event_second > in_second + 300:
                continue
            if side != in_team and scoring_side != in_team:
                continue
            if ev_key not in {"GOAL", "OWNGOAL", "YELLOWCARD", "YELLOW", "REDCARD", "RED", "SECONDYELLOW", "SECONDYELLOWCARD"}:
                continue
            sub_row = per_player_sub_impact[in_player]
            sub_row["events"].append(
                {
                    "type": ev_key,
                    "minute": event_minute,
                    "second": event_second,
                    "clock": clock.get("clock") if clock else None,
                    "side": in_team,
                }
            )
            if ev_key == "GOAL":
                sub_row["summary"]["goals"] += 1
            elif ev_key == "OWNGOAL":
                sub_row["summary"]["own_goals"] += 1
            elif ev_key in {"YELLOWCARD", "YELLOW", "SECONDYELLOW", "SECONDYELLOWCARD"}:
                sub_row["summary"]["yellow_cards"] += 1
            if ev_key in {"REDCARD", "RED", "SECONDYELLOW", "SECONDYELLOWCARD"}:
                sub_row["summary"]["red_cards"] += 1

        # Clutch actions: final 10 min (excluding penalties), team was drawing/losing,
        # and team finished non-losing.
        in_clutch_window = event_second >= clutch_start and str(event.get("period") or "").upper() != "PENALTIES"
        team_finished_non_losing = (
            (scoring_side == "home" and final_home >= final_away)
            or (scoring_side == "away" and final_away >= final_home)
        )
        if (
            in_clutch_window
            and p1
            and scoring_side in {"home", "away"}
            and team_state_before in {"losing", "drawing"}
            and team_finished_non_losing
        ):
            per_player_clutch[p1].append(
                {
                    "type": ev_key,
                    "minute": event_minute,
                    "second": event_second,
                    "clock": clock.get("clock") if clock else None,
                    "side": scoring_side,
                    "score_before": score_before,
                    "team_state_before": team_state_before,
                    "filter": "clutch_non_losing_window",
                }
            )

    home_summary_list = list(summary_players["home"].values())
    away_summary_list = list(summary_players["away"].values())

    # Conservative comeback inference from event progression.
    comeback_flag = False
    running_h = 0
    running_a = 0
    for event in events:
        ev_key = _event_name_key(event.get("event") or event.get("type"))
        side = _event_side(event, player_meta)
        scoring_side = side
        if ev_key == "OWNGOAL" and side in {"home", "away"}:
            scoring_side = "away" if side == "home" else "home"
        if ev_key in {"GOAL", "OWNGOAL"}:
            if scoring_side == "home":
                running_h += 1
            elif scoring_side == "away":
                running_a += 1
            if final_home > final_away and running_h < running_a:
                comeback_flag = True
            if final_away > final_home and running_a < running_h:
                comeback_flag = True

    player_derived: Dict[str, Dict[str, Any]] = {}
    for sid, meta in player_meta.items():
        sub_impact = per_player_sub_impact.get(sid, {"window_seconds": 300, "events": [], "summary": {}})
        summary_obj = sub_impact.get("summary") or {}
        if isinstance(summary_obj, defaultdict):
            summary_obj = dict(summary_obj)
        player_derived[sid] = {
            "status": meta.get("status"),
            "started": bool(meta.get("started")),
            "main_team": meta.get("main_team"),
            "main_position": meta.get("main_position"),
            "clutch_actions": per_player_clutch.get(sid, []),
            "sub_impact": {
                "window_seconds": sub_impact.get("window_seconds", 300),
                "events": sub_impact.get("events", []),
                "summary": summary_obj,
            },
        }

    return {
        "home_team": home_name,
        "away_team": away_name,
        "match_summary_home": home_summary_list,
        "match_summary_away": away_summary_list,
        "comeback_flag": bool(comeback_flag),
        "player_derived": player_derived,
    }


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

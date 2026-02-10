"""
Utility functions for database operations
Team name similarity matching and other helpers
"""

import re
from difflib import SequenceMatcher
from typing import Optional, Dict, Any


def normalize_team_name(name: str) -> str:
    """
    Normalize a team name for comparison by:
    - Converting to lowercase
    - Removing special characters
    - Removing extra whitespace
    """
    if not name:
        return ""
    
    # Convert to lowercase
    name = name.lower()
    
    # Remove special characters but keep spaces
    name = re.sub(r'[^\w\s]', '', name)
    
    # Remove extra whitespace
    name = ' '.join(name.split())
    
    return name


def calculate_similarity(name1: str, name2: str) -> float:
    """
    Calculate similarity score between two team names.
    Returns a float between 0.0 and 1.0, where 1.0 is an exact match.
    
    Uses multiple strategies:
    1. Exact match after normalization
    2. One name contains the other
    3. Sequence matching (edit distance)
    """
    if not name1 or not name2:
        return 0.0
    
    # Normalize both names
    norm1 = normalize_team_name(name1)
    norm2 = normalize_team_name(name2)
    
    # Exact match
    if norm1 == norm2:
        return 1.0
    
    # One contains the other (high score)
    if norm1 in norm2 or norm2 in norm1:
        shorter = min(len(norm1), len(norm2))
        longer = max(len(norm1), len(norm2))
        return 0.9 * (shorter / longer)
    
    # Sequence matching
    return SequenceMatcher(None, norm1, norm2).ratio()


def find_best_match(target_name: str, candidate_names: list, threshold: float = 0.8) -> Optional[Dict[str, Any]]:
    """
    Find the best matching team name from a list of candidates.
    
    Args:
        target_name: The team name to match
        candidate_names: List of dicts with 'guild_id' and 'guild_name'
        threshold: Minimum similarity score to consider a match (0.0 to 1.0)
    
    Returns:
        Dict with 'guild_id', 'guild_name', and 'similarity' if match found, else None
    """
    if not target_name or not candidate_names:
        return None
    
    best_match = None
    best_score = 0.0
    
    for candidate in candidate_names:
        candidate_name = candidate.get('guild_name', '')
        if not candidate_name:
            continue
        
        score = calculate_similarity(target_name, candidate_name)
        
        if score > best_score:
            best_score = score
            best_match = {
                'guild_id': candidate['guild_id'],
                'guild_name': candidate_name,
                'similarity': score
            }
    
    if best_match and best_score >= threshold:
        return best_match
    
    return None


def safe_get_string(data: dict, key: str, default: str = '') -> str:
    """
    Safely get a string value from a dictionary, handling NaN and None values.
    Used for CSV data parsing.
    """
    value = data.get(key, default)
    
    if value is None:
        return default
    
    # Handle pandas NaN
    if isinstance(value, float):
        import math
        if math.isnan(value):
            return default
    
    # Convert to string and strip
    str_value = str(value).strip()
    
    # Check if it's the string 'nan'
    if str_value.lower() == 'nan':
        return default
    
    return str_value


def parse_scoreline(scoreline: str) -> tuple:
    """
    Parse a scoreline string like "3-2" into (home_score, away_score).
    Returns (None, None) if parsing fails.
    """
    if not scoreline:
        return (None, None)
    
    try:
        parts = scoreline.split('-')
        if len(parts) == 2:
            home_score = int(parts[0].strip())
            away_score = int(parts[1].strip())
            return (home_score, away_score)
    except (ValueError, AttributeError):
        pass
    
    return (None, None)


def format_datetime(dt) -> Optional[str]:
    """
    Format a datetime object to ISO format string.
    Returns None if input is None or invalid.
    """
    if dt is None:
        return None
    
    try:
        return dt.isoformat()
    except (AttributeError, ValueError):
        return None


def clean_steam_id(steam_id: str) -> Optional[str]:
    """
    Clean and validate a Steam ID.
    Returns None if invalid.
    """
    if not steam_id:
        return None
    
    # Remove whitespace
    steam_id = str(steam_id).strip()
    
    # Check if it's a valid Steam ID (17 digits)
    if steam_id.isdigit() and len(steam_id) == 17:
        return steam_id
    
    return None


def truncate_string(text: str, max_length: int = 255) -> str:
    """
    Truncate a string to a maximum length.
    """
    if not text:
        return ""
    
    text = str(text)
    if len(text) <= max_length:
        return text
    
    return text[:max_length]

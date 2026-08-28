"""
Complete view_match.py - Reads from PostgreSQL with full MVP calculation and substitution support.
"""
from itertools import zip_longest
from datetime import datetime
from ios_bot.config import *
from ios_bot.utils.name_utils import truncate_name, format_player_with_stats
from ios_bot.utils.match_performance import get_mvp_data as _shared_get_mvp_data
import ios_bot.config as config
import logging
import json
import asyncio
import os
import re
import time
from typing import Any
from io import BytesIO
from zoneinfo import ZoneInfo
from urllib.parse import quote

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

logger = logging.getLogger(__name__)

EMOJI_GOAL = "\u26BD"
EMOJI_ASSIST = "\U0001F45F"
EMOJI_SAVE = "\U0001F9E4"
EMOJI_RED = "\U0001F7E5"
EMOJI_YELLOW = "\U0001F7E8"
EMOJI_SUB = "\U0001F504"
EMOJI_TROPHY = "\U0001F3C6"
EMOJI_DEFENDER = "\U0001F6E1\ufe0f"
EMOJI_GK = "\U0001F9E4"
CARD_WIDTH = 1200
CARD_HEIGHT = 675
CARD_BG = (40, 41, 45)
CARD_TEXT = (245, 245, 245)
CARD_MUTED_TEXT = (210, 210, 210)
HUB_MATCH_PAGE_URL_BASE = os.getenv(
    "IOSCA_HUB_MATCH_PAGE_URL_BASE",
    "https://ramymomo20.github.io/ioscahub.github.io/#/matches",
).strip()
HUB_MAIN_PAGE_URL = os.getenv(
    "IOSCA_HUB_MAIN_PAGE_URL",
    "https://ramymomo20.github.io/ioscahub.github.io/#/",
).strip()
HUB_MATCH_SHORT_BASE = os.getenv(
    "IOSCA_HUB_MATCH_SHORT_BASE",
    "https://iosca_hub/match/id=",
).strip()
HUB_API_PUBLIC_URL = os.getenv("IOSCA_HUB_API_PUBLIC_URL", "https://iosca-api.sparked.network/api").strip()
HUB_ANNOUNCE_ALLOW_PIL_FALLBACK = os.getenv("IOSCA_ANNOUNCE_ALLOW_PIL_FALLBACK", "1").lower() in ("1", "true", "yes", "on")
HUB_MATCH_CAPTURE_TIMEOUT_MS = int(os.getenv("IOSCA_HUB_MATCH_CAPTURE_TIMEOUT_MS", "45000"))
HUB_MATCH_RENDER_WAIT_MS = int(os.getenv("IOSCA_HUB_MATCH_RENDER_WAIT_MS", "15000"))
_HUB_CAPTURE_DISABLED_REASON: str | None = None
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HUB_ICONS_DIR = os.path.join(
    PROJECT_ROOT,
    "iosca_hub_github",
    "ioscahub.github.io",
    "frontend",
    "assets",
    "icons",
)
EVENT_ICON_FILES = {
    "goal": "soccer-ball-icon.png",
    "own_goal": "soccer-ball-icon.png",
    "red": "red-card-icon.png",
    "yellow": "yellow-card-icon.png",
}
EVENT_ICON_CACHE: dict[tuple[str, int], Image.Image | None] = {}


async def _safe_interaction_message(
    interaction: discord.Interaction,
    message: str,
    *,
    ephemeral: bool = True,
    view: discord.ui.View | None = None,
    embed: discord.Embed | None = None,
):
    """Send a response or followup without failing on expired interaction tokens."""
    try:
        if interaction.response.is_done():
            return await interaction.followup.send(message, ephemeral=ephemeral, view=view, embed=embed)
        return await interaction.response.send_message(message, ephemeral=ephemeral, view=view, embed=embed)
    except (discord.InteractionResponded, discord.NotFound):
        pass
    except Exception:
        logger.warning("view_match interaction send failed", exc_info=True)

    try:
        channel = getattr(interaction, "channel", None)
        if channel is None:
            return None
        user = getattr(interaction, "user", None)
        prefix = f"{user.mention} " if user and hasattr(user, "mention") else ""
        return await channel.send(content=f"{prefix}{message}", view=view, embed=embed)
    except Exception:
        logger.warning("view_match channel fallback send failed", exc_info=True)
        return None

def _normalize_steam_id(steam_id: str) -> str:
    """Normalize Steam IDs to legacy format for matching."""
    if not steam_id:
        return ""
    s = str(steam_id).strip()

    # Already legacy
    if s.upper().startswith("STEAM_"):
        parts = s.split(":")
        if len(parts) == 3:
            return f"STEAM_0:{parts[1]}:{parts[2]}"
        return s

    # SteamID3 like [U:1:12345]
    if s.startswith("[") and s.endswith("]") and ":" in s:
        try:
            account3 = int(s.split(":")[-1].strip("]"))
            acct_type = account3 % 2
            acct_num = (account3 - acct_type) // 2
            return f"STEAM_0:{acct_type}:{acct_num}"
        except Exception:
            return s

    # Steam64 numeric
    if s.isdigit() and len(s) >= 16:
        try:
            sid64 = int(s)
            offset = sid64 - 76561197960265728
            acct_type = offset % 2
            acct_num = (offset - acct_type) // 2
            return f"STEAM_0:{acct_type}:{acct_num}"
        except Exception:
            return s

    return s


def _build_ansi_scoreline(home_team: str, home_score: int, away_score: int, away_team: str) -> str:
    reset = "\u001b[0m"
    green = "\u001b[2;32m"
    red = "\u001b[2;31m"
    yellow = "\u001b[2;33m"
    if home_score > away_score:
        home_color, away_color = green, red
    elif away_score > home_score:
        home_color, away_color = red, green
    else:
        home_color = away_color = yellow
    return f"{home_team} {home_color}{int(home_score)}{reset} - {away_color}{int(away_score)}{reset} {away_team}"


def _normalize_name_for_match(name: str) -> str:
    """Normalize player names for loose matching."""
    if not name:
        return ""
    normalized = "".join(ch.lower() for ch in str(name) if ch.isalnum())
    return normalized


def _steam_id_aliases(steam_id: str) -> list[str]:
    """Build comparable Steam ID aliases across legacy, Steam64, and SteamID3 forms."""
    raw = str(steam_id or "").strip()
    if not raw:
        return []

    aliases: list[str] = []

    def _add(value: str):
        v = str(value or "").strip()
        if v and v not in aliases:
            aliases.append(v)

    _add(raw)
    legacy = _normalize_steam_id(raw)
    _add(legacy)

    if legacy.upper().startswith("STEAM_"):
        parts = legacy.split(":")
        if len(parts) == 3:
            try:
                y = int(parts[1])
                z = int(parts[2])
                account_id = z * 2 + y
                _add(f"[U:1:{account_id}]")
                _add(str(account_id + 76561197960265728))
            except Exception:
                pass

    return aliases

_MATCH_LIST_CACHE: dict[str, Any] = {"data": None, "expires_at": 0.0}
_MATCH_LIST_CACHE_TTL_SECONDS = 60


async def get_matches():
    """Get all matches from database, sorted by most recent.

    TTL-only cache (60s): this scans and JSON-parses every match in the
    database with no LIMIT, and was previously being re-run in full on every
    single match-selection click in the UI on top of the initial listing --
    a short cache absorbs both without needing a write-triggered invalidation
    hook wired all the way from the match importer into this module.
    """
    now = time.monotonic()
    if _MATCH_LIST_CACHE["data"] is not None and now < _MATCH_LIST_CACHE["expires_at"]:
        return _MATCH_LIST_CACHE["data"]

    query_full = """
    SELECT 
        m.id as match_id,
        m.match_id as match_id_str,
        m.datetime,
        m.home_guild_id,
        m.away_guild_id,
        m.home_score,
        m.away_score,
        m.home_lineup,
        m.away_lineup,
        m.substitutions,
        m.match_summary_home,
        m.match_summary_away,
        m.comeback_flag,
        m.extratime,
        m.penalties,
        COALESCE(ht.guild_name, m.home_team_name) as home_team_name,
        COALESCE(at.guild_name, m.away_team_name) as away_team_name
    FROM MATCH_STATS m
    LEFT JOIN IOSCA_TEAMS ht ON m.home_guild_id = ht.guild_id
    LEFT JOIN IOSCA_TEAMS at ON m.away_guild_id = at.guild_id
    ORDER BY m.datetime DESC
    """
    query_min = """
    SELECT 
        m.id as match_id,
        m.match_id as match_id_str,
        m.datetime,
        m.home_guild_id,
        m.away_guild_id,
        m.home_score,
        m.away_score,
        COALESCE(ht.guild_name, m.home_team_name) as home_team_name,
        COALESCE(at.guild_name, m.away_team_name) as away_team_name
    FROM MATCH_STATS m
    LEFT JOIN IOSCA_TEAMS ht ON m.home_guild_id = ht.guild_id
    LEFT JOIN IOSCA_TEAMS at ON m.away_guild_id = at.guild_id
    ORDER BY m.datetime DESC
    """
    query_with_lineups = """
    SELECT 
        m.id as match_id,
        m.match_id as match_id_str,
        m.datetime,
        m.home_guild_id,
        m.away_guild_id,
        m.home_score,
        m.away_score,
        m.home_lineup,
        m.away_lineup,
        m.substitutions,
        m.extratime,
        m.penalties,
        COALESCE(ht.guild_name, m.home_team_name) as home_team_name,
        COALESCE(at.guild_name, m.away_team_name) as away_team_name
    FROM MATCH_STATS m
    LEFT JOIN IOSCA_TEAMS ht ON m.home_guild_id = ht.guild_id
    LEFT JOIN IOSCA_TEAMS at ON m.away_guild_id = at.guild_id
    ORDER BY m.datetime DESC
    """
    
    try:
        matches = await bot.db.pool.fetch(query_full)
        has_lineups = True
    except Exception as e_full:
        try:
            matches = await bot.db.pool.fetch(query_with_lineups)
            has_lineups = True
        except Exception as e_mid:
            try:
                matches = await bot.db.pool.fetch(query_min)
                has_lineups = False
            except Exception as e_min:
                raise RuntimeError(f"get_matches failed: full={e_full} | mid={e_mid} | min={e_min}") from e_min
    
    # Get team names for each match
    result = []
    for match in matches:
        home_lineup = match.get('home_lineup', []) if has_lineups else []
        away_lineup = match.get('away_lineup', []) if has_lineups else []
        if isinstance(home_lineup, str):
            try:
                home_lineup = json.loads(home_lineup)
            except Exception:
                home_lineup = []
        if isinstance(away_lineup, str):
            try:
                away_lineup = json.loads(away_lineup)
            except Exception:
                away_lineup = []
        match_summary_home = match.get('match_summary_home', []) if has_lineups else []
        match_summary_away = match.get('match_summary_away', []) if has_lineups else []
        if isinstance(match_summary_home, str):
            try:
                match_summary_home = json.loads(match_summary_home)
            except Exception:
                match_summary_home = []
        if isinstance(match_summary_away, str):
            try:
                match_summary_away = json.loads(match_summary_away)
            except Exception:
                match_summary_away = []
        result.append({
            'match_id': str(match['match_id']),
            'match_id_str': match.get('match_id_str'),
            'datetime': match['datetime'],
            'home_team': match.get('home_team_name') or 'Home Team',
            'away_team': match.get('away_team_name') or 'Away Team',
            'home_guild_id': match.get('home_guild_id'),
            'away_guild_id': match.get('away_guild_id'),
            'scoreline': f"{match['home_score']}-{match['away_score']}",
            'substitutions': match.get('substitutions', []),
            'match_summary_home': match_summary_home,
            'match_summary_away': match_summary_away,
            'comeback_flag': bool(match.get('comeback_flag', False)),
            'home_lineup': home_lineup,
            'away_lineup': away_lineup,
            'extratime': match.get('extratime', False) if has_lineups else False,
            'penalties': match.get('penalties', False) if has_lineups else False
        })

    _MATCH_LIST_CACHE["data"] = result
    _MATCH_LIST_CACHE["expires_at"] = time.monotonic() + _MATCH_LIST_CACHE_TTL_SECONDS
    return result


async def get_player_stats_for_match_id(match_id: int, match_id_str: str | None = None):
    """Get all player stats for a specific match from database (cached
    under the shared matches cache, so a match write invalidates it same
    as every other cached match-detail lookup)."""
    cache_key = f"matches:view_stats:{match_id}"
    cached = bot.db.matches._cache.get(cache_key)
    if cached is not None:
        return [dict(row) for row in cached]

    match_id_value = match_id
    match_id_text_value = match_id_str or str(match_id)
    try:
        row = await bot.db.pool.fetchrow(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'player_match_data'
              AND column_name = 'match_id'
            """
        )
        data_type = row['data_type'] if row else None
        if data_type in ('character varying', 'text'):
            match_id_value = match_id_text_value
    except Exception:
        match_id_value = match_id

    query = """
    SELECT 
        pmd.*,
        p.discord_id as discord_id,
        p.discord_name as discord_name,
        p.steam_id as player_steam_id
    FROM PLAYER_MATCH_DATA pmd
    LEFT JOIN IOSCA_PLAYERS p ON pmd.steam_id = p.steam_id
    WHERE pmd.match_id = $1
    """
    
    rows = await bot.db.pool.fetch(query, match_id_value)

    # Build a fallback lookup so viewing still resolves players even when IDs are
    # stored in different Steam formats across tables (legacy/Steam64/SteamID3).
    alias_keys: list[str] = []
    for row in rows:
        sid = row.get("steam_id") or row.get("player_steam_id")
        for alias in _steam_id_aliases(sid):
            key = alias.strip().lower()
            if key:
                alias_keys.append(key)
    alias_keys = list(dict.fromkeys(alias_keys))

    players_by_alias: dict[str, dict] = {}
    if alias_keys:
        try:
            player_rows = await bot.db.pool.fetch(
                """
                SELECT *
                FROM IOSCA_PLAYERS
                WHERE lower(trim(steam_id::text)) = ANY($1::text[])
                """,
                alias_keys
            )
            for player_row in player_rows:
                player_data = dict(player_row)
                for alias in _steam_id_aliases(player_data.get("steam_id")):
                    key = alias.strip().lower()
                    if key:
                        players_by_alias[key] = player_data
        except Exception:
            players_by_alias = {}

    guild_ids: list[int] = []
    for row in rows:
        guild_id = row.get("guild_id")
        try:
            if guild_id is not None:
                guild_ids.append(int(guild_id))
        except Exception:
            continue
    teams_by_id = await bot.db.teams.get_teams_by_ids(guild_ids) if guild_ids else {}
    
    # Convert to dict format matching old CSV structure
    result = []
    for row in rows:
        steam_val = row.get('player_steam_id') or row.get('steam_id') or ''
        guild_id = row.get('guild_id')
        team = None
        try:
            if guild_id is not None:
                team = teams_by_id.get(int(guild_id))
        except Exception:
            team = None

        matched_player = None
        if steam_val:
            for alias in _steam_id_aliases(steam_val):
                matched_player = players_by_alias.get(alias.strip().lower())
                if matched_player:
                    break

        resolved_discord_name = row.get('discord_name')
        if not resolved_discord_name and matched_player:
            resolved_discord_name = (
                matched_player.get("discord_name")
                or matched_player.get("username")
            )

        resolved_discord_id = row.get('discord_id')
        if resolved_discord_id in (None, "") and matched_player:
            resolved_discord_id = matched_player.get("discord_id")

        if not row.get('player_steam_id') and matched_player and matched_player.get("steam_id"):
            steam_val = matched_player.get("steam_id")

        name_val = resolved_discord_name or row.get('player_name') or row.get('name') or 'Unknown'
        
        result.append({
            'match_id': str(row['match_id']),
            'Name': name_val,
            'player_name': row.get('player_name') or name_val,
            'Steam ID': steam_val,
            'event_timestamps': row.get('event_timestamps') or {},
            'discord_id': resolved_discord_id,
            'guild_id': guild_id,
            'team_side': row.get('team_side'),
            'Team Name': (team.get('guild_name') if team else None) or row.get('guild_team_name') or 'Unknown',
            'Position': row['position'] or '',
            'status': row.get('status') or '',
            'clutchActions': row.get('clutch_actions') or [],
            'subImpact': row.get('sub_impact') or {},
            'goals': row['goals'] or 0,
            'shots': row['shots'] or 0,
            'shotsOnGoal': row['shots_on_goal'] or 0,
            'passesCompleted': row['passes_completed'] or 0,
            'passesAttempted': row['passes_attempted'] or 0,
            'tackles': row['tackles'] or 0,
            'assists': row['assists'] or 0,
            'secondAssists': row.get('second_assists', 0),
            'chancesCreated': row.get('chances_created', 0),
            'keyPasses': row.get('key_passes', 0),
            'interceptions': row.get('interceptions', 0),
            'slidingTacklesCompleted': row.get('sliding_tackles_completed', 0),
            'fouls': row.get('fouls', 0),
            'freeKicks': row.get('free_kicks', 0),
            'penalties': row.get('penalties', 0),
            'corners': row.get('corners', 0),
            'throwins': row.get('throw_ins', row.get('throwins', 0)),
            'goalKicks': row.get('goal_kicks', 0),
            'keeperSaves': row.get('keeper_saves', 0),
            'keeperSavesCaught': row.get('keeper_saves_caught', 0),
            'goalsConceded': row.get('goals_conceded', 0),
            'yellowCards': row.get('yellow_cards', 0),
            'redCards': row.get('red_cards', 0),
            'passesCompleted': row.get('passes_completed', 0),
            'shotsOnGoal': row.get('shots_on_goal', 0),
            'foulsSuffered': row.get('fouls_suffered', 0),
            'offsides': row.get('offsides', 0),
            'ownGoals': row.get('own_goals', 0),
            'distanceCovered': row.get('distance_covered', 0),
            'passAccuracy': row.get('pass_accuracy', 0),
            'match_rating': row.get('match_rating'),
            'is_match_mvp': bool(row.get('is_match_mvp')),
            'mvp_score': row.get('mvp_score'),
            'mvp_key_stats': _normalize_mvp_key_stats(row.get('mvp_key_stats')),
        })

    bot.db.matches._cache.set(cache_key, result)
    return result


def _stats_bucket_signature(rows: list[dict]) -> set[tuple[str, str, str]]:
    signature = set()
    for row in rows or []:
        steam_id = str(row.get("Steam ID") or "").strip().lower()
        name = str(row.get("Name") or "").strip().lower()
        guild_id = str(row.get("guild_id") or "").strip().lower()
        team_side = str(row.get("team_side") or "").strip().lower()
        signature.add((steam_id or name, guild_id, team_side))
    return signature


def _group_stats_by_team_side(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    home_rows = []
    away_rows = []
    for row in rows or []:
        side = str(row.get("team_side") or "").strip().lower()
        if side == "home":
            home_rows.append(row)
        elif side == "away":
            away_rows.append(row)
    return home_rows, away_rows


def normalize_value(value, min_val, max_val):
    """Normalize a value between 0 and 1."""
    if max_val == min_val:
        return 1.0
    return (value - min_val) / (max_val - min_val)


def _normalize_mvp_key_stats(raw_value):
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            return []
        try:
            raw_value = json.loads(text)
        except Exception:
            return [text]
    if isinstance(raw_value, list):
        out = []
        for item in raw_value:
            text = str(item or "").strip()
            if text:
                out.append(text)
        return out
    text = str(raw_value or "").strip()
    return [text] if text else []


def _get_mvp_data(player_stats):
    """Return MVP payload used by both embed and lineup highlighting."""
    persisted = [p for p in (player_stats or []) if p.get("is_match_mvp")]
    if persisted:
        best = max(
            persisted,
            key=lambda p: float(p.get("mvp_score") if p.get("mvp_score") is not None else (p.get("match_rating") or 0)),
        )
        return {
            "name": str(best.get("Name") or best.get("player_name") or "Unknown"),
            "position": str(best.get("Position") or "").upper(),
            "score": float(best.get("mvp_score") if best.get("mvp_score") is not None else (best.get("match_rating") or 0)),
            "stats": _normalize_mvp_key_stats(best.get("mvp_key_stats")),
        }
    return _shared_get_mvp_data(player_stats)


def get_mvp(player_stats):
    """
    Calculate MVP using a realistic, football-based scoring system.
    Base score starts at 5.5/10, with bonuses and penalties applied.
    10/10 ratings are extremely rare and reserved for legendary performances.
    """
    mvp = _get_mvp_data(player_stats)
    if not mvp:
        return "No valid players found"

    stats_values = _normalize_mvp_key_stats(mvp.get('stats'))
    stats_display = " | ".join(stats_values) if stats_values else "Solid performance"
    return f"`{mvp['name']}` (**{mvp['position']}**) : `{mvp['score']:.1f}/10` - {stats_display}"


def _get_best_defender_data(player_stats):
    """Get the best defender payload based on interceptions and slide tackles."""
    if not player_stats:
        return None
        
    defenders = [p for p in player_stats if p['Position'] in ['LB', 'CB', 'RB']]
    if not defenders:
        return None
        
    defender_stats = []
    for defender in defenders:
        interceptions = int(float(defender.get('interceptions', 0)))
        slide_tackles = int(float(defender.get('slidingTacklesCompleted', 0)))
        total = interceptions + slide_tackles
        defender_stats.append({
            'name': defender['Name'],
            'interceptions': interceptions,
            'slide_tackles': slide_tackles,
            'total': total
        })
    
    defender_stats.sort(key=lambda x: x['total'], reverse=True)
    return defender_stats[0]


def get_best_defender(player_stats):
    """Get the best defender based on interceptions and slide tackles."""
    best_defender = _get_best_defender_data(player_stats)
    if not best_defender:
        return "No defenders found"
    return f"`{best_defender['name']}`  :|: **Interceptions:** `{best_defender['interceptions']}` **Successful Slide Tackles:** `{best_defender['slide_tackles']}`"


def get_best_goalkeeper(player_stats):
    """Finds the best GK if there are two, based on save ratio."""
    keepers = [p for p in player_stats if p.get('Position') == 'GK']
    if len(keepers) < 2:
        return None

    best_gk = None
    max_gk_score = -1

    for gk in keepers:
        try:
            saves = int(float(gk.get('keeperSaves', 0))) + int(float(gk.get('keeperSavesCaught', 0)))
            conceded = int(float(gk.get('goalsConceded', 0)))
            gk_score = saves / (conceded + 1)
            if gk_score > max_gk_score:
                max_gk_score = gk_score
                best_gk = gk.get('Name', 'N/A')
        except (ValueError, TypeError):
            continue

    return best_gk


def _iter_lineup_entries(lineup_data):
    """Yield (pos, name, steam_id, started) from lineup_data with mixed shapes."""
    if not lineup_data:
        return
    for item in lineup_data:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            yield item[0], item[1], item[2], True
            continue
        if isinstance(item, dict):
            pos = item.get("position") or item.get("pos")
            name = item.get("name") or item.get("player_name") or item.get("discord_name")
            steam_id = item.get("steam_id") or item.get("steamId")
            started = item.get("started")
            if started is None:
                started = True
            if pos or name or steam_id:
                yield pos or "", name or "", steam_id or "", bool(started)
                continue
        # Unknown shape: skip safely


def _filter_stats_for_lineup(player_stats, lineup_data, team_name=None, team_guild_id=None):
    """Pick the best subset of stats for one side of the match."""
    if not player_stats:
        return []

    lineup_steam_ids = set()
    lineup_names = set()
    for _pos, name, steamid, _started in _iter_lineup_entries(lineup_data):
        if steamid:
            raw = str(steamid).strip()
            if raw:
                lineup_steam_ids.add(raw)
                lineup_steam_ids.add(_normalize_steam_id(raw))
        if name:
            raw_name = str(name).strip().lower()
            lineup_names.add(raw_name)
            lineup_names.add(_normalize_name_for_match(raw_name))

    # 1) Strongest signal: guild_id on player stats.
    if team_guild_id is not None:
        by_guild = [
            p for p in player_stats
            if p.get("guild_id") is not None and str(p.get("guild_id")) == str(team_guild_id)
        ]
        if by_guild:
            return by_guild

    # 2) Match by steam IDs present in lineup.
    if lineup_steam_ids:
        by_steam = []
        for p in player_stats:
            sid = p.get("Steam ID")
            if not sid:
                continue
            sid_s = str(sid).strip()
            sid_n = _normalize_steam_id(sid_s)
            if sid_s in lineup_steam_ids or sid_n in lineup_steam_ids:
                by_steam.append(p)
        if by_steam:
            return by_steam

    # 3) Fallback by lineup names.
    if lineup_names:
        by_name = []
        for p in player_stats:
            pname = str(p.get("Name") or "").strip().lower()
            pname_norm = _normalize_name_for_match(pname)
            if pname and (
                pname in lineup_names
                or pname_norm in lineup_names
                or any(pname in n or n in pname for n in lineup_names if n)
                or any(pname_norm in n or n in pname_norm for n in lineup_names if n)
            ):
                by_name.append(p)
        if by_name:
            return by_name

    # 4) Last fallback by team label.
    if team_name:
        by_team = [p for p in player_stats if str(p.get("Team Name") or "") == str(team_name)]
        if by_team:
            return by_team

    return []


def format_team_lineup(
    team_name,
    lineup_data,
    position_order,
    player_stats=None,
    substitution_summary=None,
    home_team_name=None,
    away_team_name=None,
    team_guild_id=None,
    highlight_names=None,
    mvp_name=None,
):
    """Formats a single team's lineup with stats using the lineup data structure."""
    lines = []
    
    # Create a mapping of steam_id to player stats
    steam_id_to_stats = {}
    name_to_stats = {}
    if player_stats:
        for player in player_stats:
            raw_id = str(player.get('Steam ID') or "").strip()
            if raw_id:
                steam_id_to_stats[raw_id] = player
                steam_id_to_stats[_normalize_steam_id(raw_id)] = player
            name_key = str(player.get('Name') or "").strip().lower()
            if name_key:
                name_to_stats[name_key] = player
                name_to_stats[_normalize_name_for_match(name_key)] = player
    
    # Create a set of players who were subbed out
    subbed_out_players = set()
    if substitution_summary and home_team_name and away_team_name:
        for sub in substitution_summary:
            team_side, (left_name, left_steamid), (join_name, join_steamid) = sub
            if team_side == "home" and team_name == home_team_name:
                subbed_out_players.add(left_steamid)
            elif team_side == "away" and team_name == away_team_name:
                subbed_out_players.add(left_steamid)
    
    pos_to_player = {}
    has_started_flag = False
    for pos, name, steamid, started in _iter_lineup_entries(lineup_data):
        if not pos:
            continue
        if started is False:
            has_started_flag = True
            continue
        if started is True:
            has_started_flag = True
        if pos not in pos_to_player and (name or steamid):
            pos_to_player[pos] = (name, steamid)

    if not pos_to_player and has_started_flag:
        # Fallback: if all entries were marked not started, include first per position
        for pos, name, steamid, _started in _iter_lineup_entries(lineup_data):
            if pos and pos not in pos_to_player and (name or steamid):
                pos_to_player[pos] = (name, steamid)

    # Use a stable ordering across formats; include only positions that exist
    master_order = [
        'GK', 'LB', 'CB', 'RB',
        'LM', 'CM', 'RM', 'LW', 'CF', 'RW'
    ]
    ordered_positions = [p for p in master_order if p in pos_to_player]
    # Append any uncommon positions not in master_order
    for pos in pos_to_player.keys():
        if pos not in ordered_positions:
            ordered_positions.append(pos)

    normalized_highlight_names = set()
    for n in (highlight_names or []):
        if n:
            normalized_highlight_names.add(_normalize_name_for_match(str(n)))
    normalized_mvp_name = _normalize_name_for_match(str(mvp_name)) if mvp_name else ""

    for pos in ordered_positions:
        name, steamid = pos_to_player.get(pos, ("", ""))
        if name or steamid:
            stats = []
            player = None
            red_cards = 0

            if steamid:
                player = steam_id_to_stats.get(steamid)
                if not player:
                    player = steam_id_to_stats.get(_normalize_steam_id(steamid))

            if not player and name:
                lookup_name = str(name).strip().lower()
                player = name_to_stats.get(lookup_name)
                if not player:
                    player = name_to_stats.get(_normalize_name_for_match(lookup_name))

            if player:
                if int(float(player.get('goals', 0))) > 0:
                    stats.append(f"{EMOJI_GOAL}x{int(float(player['goals']))}")
                if int(float(player.get('assists', 0))) > 0:
                    stats.append(f"{EMOJI_ASSIST}x{int(float(player['assists']))}")
                if int(float(player.get('keeperSaves', 0))) > 0:
                    stats.append(f"{EMOJI_SAVE}x{int(float(player['keeperSaves']))}")

                red_cards = int(float(player.get('redCards', 0)))
                yellow_cards = int(float(player.get('yellowCards', 0)))
                if red_cards > 0:
                    stats.append(EMOJI_RED)
                elif yellow_cards > 0:
                    stats.append(EMOJI_YELLOW)

            sub_symbol = f"{EMOJI_SUB} " if steamid and steamid in subbed_out_players else ""
            formatted_name = format_player_with_stats(name or steamid or "-", stats, max_name_length=18)
            normalized_player_name = _normalize_name_for_match(str(name or ""))
            is_award_player = normalized_player_name in normalized_highlight_names if normalized_player_name else False
            is_mvp = normalized_player_name == normalized_mvp_name if normalized_player_name and normalized_mvp_name else False

            if is_mvp:
                formatted_name = f"{EMOJI_TROPHY} {formatted_name}"

            if is_award_player:
                lines.append(f"+{pos}: {sub_symbol}{formatted_name}")
            elif red_cards >= 1:
                lines.append(f"-{pos}: {sub_symbol}{formatted_name}")
            else:
                lines.append(f"{pos}: {sub_symbol}{formatted_name}")
        else:
            lines.append(f"{pos}: -")

    return "\n".join(lines)


def format_substitutions(substitution_summary, player_stats):
    """Formats the substitution summary with player stats."""
    if not substitution_summary:
        return "No substitutions"

    steam_id_to_stats = {}
    for player in player_stats:
        steam_id_to_stats[player['Steam ID']] = player

    sub_lines = []
    for i, sub in enumerate(substitution_summary):
        team_side, (left_name, left_steamid), (join_name, join_steamid) = sub

        left_stats = steam_id_to_stats.get(left_steamid, {})
        join_stats = steam_id_to_stats.get(join_steamid, {})

        left_stats_str = []
        if int(float(left_stats.get('goals', 0))) > 0:
            left_stats_str.append(f"{EMOJI_GOAL}x{int(float(left_stats['goals']))}")
        if int(float(left_stats.get('assists', 0))) > 0:
            left_stats_str.append(f"{EMOJI_ASSIST}x{int(float(left_stats['assists']))}")
        if int(float(left_stats.get('keeperSaves', 0))) > 0:
            left_stats_str.append(f"{EMOJI_SAVE}x{int(float(left_stats['keeperSaves']))}")

        left_red_cards = int(float(left_stats.get('redCards', 0)))
        left_yellow_cards = int(float(left_stats.get('yellowCards', 0)))
        if left_red_cards > 0:
            left_stats_str.append(EMOJI_RED)
        elif left_yellow_cards > 0:
            left_stats_str.append(EMOJI_YELLOW)

        join_stats_str = []
        if int(float(join_stats.get('goals', 0))) > 0:
            join_stats_str.append(f"{EMOJI_GOAL}x{int(float(join_stats['goals']))}")
        if int(float(join_stats.get('assists', 0))) > 0:
            join_stats_str.append(f"{EMOJI_ASSIST}x{int(float(join_stats['assists']))}")
        if int(float(join_stats.get('keeperSaves', 0))) > 0:
            join_stats_str.append(f"{EMOJI_SAVE}x{int(float(join_stats['keeperSaves']))}")

        join_red_cards = int(float(join_stats.get('redCards', 0)))
        join_yellow_cards = int(float(join_stats.get('yellowCards', 0)))
        if join_red_cards > 0:
            join_stats_str.append(EMOJI_RED)
        elif join_yellow_cards > 0:
            join_stats_str.append(EMOJI_YELLOW)

        left_stats_display = " ".join(left_stats_str) if left_stats_str else ""
        join_stats_display = " ".join(join_stats_str) if join_stats_str else ""

        team_display = "Home" if team_side == "home" else "Away"
        sub_lines.append(f"({i+1}) {team_display}: {left_name} {EMOJI_SUB} {join_name}")
        if left_stats_display or join_stats_display:
            sub_lines.append(f"    {left_name}: {left_stats_display} | {join_name}: {join_stats_display}")

    return "\n".join(sub_lines)


async def get_player_mention(performer_str: str):
    """
    Parses performer string, finds player in DB, and returns a mention or name with score.
    """
    if performer_str == 'N/A':
        return 'N/A', True
    
    parts = performer_str.split(' : ')
    name = parts[0]
    steam_id = parts[1] if len(parts) > 1 else None
    score = parts[2] if len(parts) > 2 else None
    score_found = score is not None
    
    score_display = ""
    if score_found:
        try:
            score_num = float(score)
            score_display = f" {{{int(score_num) if score_num.is_integer() else round(score_num, 2)}}}"
        except ValueError:
            score_display = ""
    
    if steam_id:
        player = await bot.db.players.get_player_by_steam_id(steam_id)
        if player and player.get('discord_id'):
            mention = f"<@{player['discord_id']}>"
            return f"{mention} ({name}{score_display})", score_found
    
    return f"{name}{score_display}", score_found


def _coerce_match_datetime(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _format_card_time_date(match_dt: datetime | None) -> tuple[str, str]:
    timezone_name = os.getenv("MAIN_GUILD_TIMEZONE", "America/New_York")
    tz = ZoneInfo(timezone_name)
    if match_dt is None:
        now_local = datetime.now(tz)
        return now_local.strftime("%H:%M"), f"{now_local.month}/{now_local.day}/{str(now_local.year)[-2:]}"

    if match_dt.tzinfo is None:
        match_dt = match_dt.replace(tzinfo=ZoneInfo("UTC"))
    local_dt = match_dt.astimezone(tz)
    time_label = local_dt.strftime("%H:%M")
    date_label = f"{local_dt.month}/{local_dt.day}/{str(local_dt.year)[-2:]}"
    return time_label, date_label


def _load_card_font(size: int, bold: bool = False):
    preferred = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
    ]
    for path in preferred:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, size: int, bold: bool = False, min_size: int = 18):
    current = size
    while current >= min_size:
        font = _load_card_font(current, bold=bold)
        bbox = draw.textbbox((0, 0), text, font=font)
        width = bbox[2] - bbox[0]
        if width <= max_width:
            return font
        current -= 2
    return _load_card_font(min_size, bold=bold)


def _draw_centered_text(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    draw.text((x - width // 2, y - height // 2), text, font=font, fill=fill)


def _safe_stat_int(player: dict, key: str) -> int:
    try:
        return int(float(player.get(key, 0) or 0))
    except Exception:
        return 0


def _safe_minute_list(values) -> list[int]:
    out: list[int] = []
    if isinstance(values, (int, float, str)):
        values = [values]
    elif isinstance(values, (tuple, set)):
        values = list(values)
    if isinstance(values, list):
        for v in values:
            try:
                minute = int(float(v))
            except Exception:
                continue
            if minute > 0:
                out.append(minute)
    return sorted(out)


def _get_player_event_minutes(player: dict, event_key: str) -> list[int]:
    """Read per-player event minutes from event_timestamps JSON."""
    raw = player.get("event_timestamps") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        return []

    normalized_raw = {}
    for rk, rv in raw.items():
        nk = re.sub(r"[^a-z0-9]+", "", str(rk or "").lower())
        if nk:
            normalized_raw[nk] = rv

    aliases = {
        "goal": ["goal", "goals"],
        "own_goal": ["own_goal", "own_goals", "owngoal", "owngoals"],
        "red": ["red", "red_card", "red_cards", "redcard", "redcards", "straight_red", "second_yellow_red"],
        "yellow": ["yellow", "yellow_card", "yellow_cards", "yellowcard", "yellowcards"],
    }

    for key in aliases.get(event_key, [event_key]):
        minutes = _safe_minute_list(raw.get(key))
        if minutes:
            return minutes
        nkey = re.sub(r"[^a-z0-9]+", "", str(key or "").lower())
        minutes = _safe_minute_list(normalized_raw.get(nkey))
        if minutes:
            return minutes
    return []


def _format_minute_csv(minutes: list[int]) -> str:
    return ", ".join(f"{m}'" for m in minutes)


def _parse_json_like(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return fallback
    return fallback


def _build_match_derived_summary(team_stats: list[dict]) -> dict:
    summary = {
        "starts": 0,
        "subs": 0,
        "bench": 0,
        "clutch_events": 0,
        "sub_events": 0,
        "sub_goals": 0,
        "sub_own_goals": 0,
    }
    for player in team_stats or []:
        status = str(player.get("status") or "").lower()
        if status == "started":
            summary["starts"] += 1
        elif status == "substitute":
            summary["subs"] += 1
        elif status == "on_bench":
            summary["bench"] += 1

        clutch_actions = _parse_json_like(player.get("clutchActions") or player.get("clutch_actions"), [])
        if isinstance(clutch_actions, list):
            summary["clutch_events"] += len(clutch_actions)

        sub_impact = _parse_json_like(player.get("subImpact") or player.get("sub_impact"), {})
        if isinstance(sub_impact, dict):
            events = sub_impact.get("events")
            if isinstance(events, list):
                summary["sub_events"] += len(events)
            impact_summary = sub_impact.get("summary")
            if isinstance(impact_summary, dict):
                try:
                    summary["sub_goals"] += int(float(impact_summary.get("goals", 0) or 0))
                    summary["sub_own_goals"] += int(float(impact_summary.get("own_goals", 0) or 0))
                except Exception:
                    pass

    return summary


def _build_team_announcement_items(team_stats: list[dict], max_items: int = 6) -> list[dict]:
    event_entries = []

    for player in team_stats or []:
        name = str(player.get("Name") or "Unknown").strip()
        if not name:
            name = "Unknown"

        # Goals
        goal_minutes = _get_player_event_minutes(player, "goal")
        if not goal_minutes:
            goal_count = _safe_stat_int(player, "goals")
            if goal_count > 0:
                goal_minutes = [0] * goal_count
        if goal_minutes:
            text = truncate_name(
                f"{name} {_format_minute_csv([m for m in goal_minutes if m > 0])}" if any(m > 0 for m in goal_minutes) else f"{name} x{len(goal_minutes)}",
                max_length=42,
            )
            first_minute = min([m for m in goal_minutes if m > 0], default=999)
            event_entries.append((first_minute, name.lower(), {"kind": "goal", "text": text}))

        # Own goals
        og_minutes = _get_player_event_minutes(player, "own_goal")
        if not og_minutes:
            og_count = _safe_stat_int(player, "ownGoals")
            if og_count > 0:
                og_minutes = [0] * og_count
        if og_minutes:
            base = f"{name} {_format_minute_csv([m for m in og_minutes if m > 0])}" if any(m > 0 for m in og_minutes) else f"{name} x{len(og_minutes)}"
            text = truncate_name(f"{base} (OG)", max_length=42)
            first_minute = min([m for m in og_minutes if m > 0], default=999)
            event_entries.append((first_minute, name.lower(), {"kind": "own_goal", "text": text}))

        # Red cards
        red_minutes = _get_player_event_minutes(player, "red")
        if not red_minutes:
            red_count = _safe_stat_int(player, "redCards")
            if red_count > 0:
                red_minutes = [0] * red_count
        if red_minutes:
            text = truncate_name(
                f"{name} {_format_minute_csv([m for m in red_minutes if m > 0])}" if any(m > 0 for m in red_minutes) else f"{name} x{len(red_minutes)}",
                max_length=42,
            )
            first_minute = min([m for m in red_minutes if m > 0], default=999)
            event_entries.append((first_minute, name.lower(), {"kind": "red", "text": text}))

        # Yellow cards
        yellow_minutes = _get_player_event_minutes(player, "yellow")
        if not yellow_minutes:
            yellow_count = _safe_stat_int(player, "yellowCards")
            if yellow_count > 0:
                yellow_minutes = [0] * yellow_count
        if yellow_minutes:
            text = truncate_name(
                f"{name} {_format_minute_csv([m for m in yellow_minutes if m > 0])}" if any(m > 0 for m in yellow_minutes) else f"{name} x{len(yellow_minutes)}",
                max_length=42,
            )
            first_minute = min([m for m in yellow_minutes if m > 0], default=999)
            event_entries.append((first_minute, name.lower(), {"kind": "yellow", "text": text}))

    event_entries.sort(key=lambda x: (x[0], x[1]))
    items = [x[2] for x in event_entries]
    if not items:
        return []
    if len(items) > max_items:
        remaining = len(items) - (max_items - 1)
        items = items[:max_items - 1] + [{"kind": "more", "text": f"+{remaining} more"}]
    return items


def _draw_goal_event_icon(draw: ImageDraw.ImageDraw, x: int, y: int):
    r = 7
    draw.ellipse((x - r, y - r, x + r, y + r), fill=(245, 245, 245), outline=(70, 70, 70), width=1)
    draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(56, 56, 56))


def _draw_red_event_icon(draw: ImageDraw.ImageDraw, x: int, y: int):
    draw.rounded_rectangle((x - 6, y - 8, x + 6, y + 8), radius=2, fill=(226, 67, 67))


def _draw_yellow_event_icon(draw: ImageDraw.ImageDraw, x: int, y: int):
    draw.rounded_rectangle((x - 6, y - 8, x + 6, y + 8), radius=2, fill=(235, 204, 68))


def _draw_own_goal_event_icon(draw: ImageDraw.ImageDraw, x: int, y: int):
    # Simple outlined goal ball to distinguish own goals from regular goals.
    r = 7
    draw.ellipse((x - r, y - r, x + r, y + r), outline=(245, 245, 245), width=2)
    draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(245, 245, 245))


def _get_event_icon(kind: str, size: int = 16) -> Image.Image | None:
    key = (kind, size)
    if key in EVENT_ICON_CACHE:
        return EVENT_ICON_CACHE[key]

    filename = EVENT_ICON_FILES.get(kind)
    if not filename:
        EVENT_ICON_CACHE[key] = None
        return None

    path = os.path.join(HUB_ICONS_DIR, filename)
    try:
        icon = Image.open(path).convert("RGBA")
        icon = ImageOps.contain(icon, (size, size), Image.Resampling.LANCZOS)
        EVENT_ICON_CACHE[key] = icon
        return icon
    except Exception:
        EVENT_ICON_CACHE[key] = None
        return None


def _draw_centered_event_line(image: Image.Image, draw: ImageDraw.ImageDraw, center_x: int, y: int, item: dict, font):
    kind = str(item.get("kind") or "")
    text = str(item.get("text") or "")
    if not text:
        return

    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]
    icon_image = _get_event_icon(kind, size=16) if kind in {"goal", "red", "yellow", "own_goal"} else None
    icon_w = icon_image.width if icon_image else (14 if kind in {"goal", "red", "yellow", "own_goal"} else 0)
    gap = 10 if icon_w else 0
    group_w = icon_w + gap + text_w
    start_x = int(center_x - (group_w / 2))

    if kind == "red":
        color = (226, 76, 76)
    elif kind == "yellow":
        color = (235, 221, 130)
    elif kind == "own_goal":
        color = (220, 220, 220)
    elif kind == "more":
        color = CARD_MUTED_TEXT
    else:
        color = CARD_TEXT

    text_x = start_x + (icon_w + gap if icon_w else 0)
    text_y = int(y - (text_h / 2) - text_bbox[1])
    draw.text((text_x, text_y), text, font=font, fill=color)

    if icon_image is not None:
        image.alpha_composite(icon_image, (start_x, int(y - icon_image.height / 2)))
    else:
        if kind == "goal":
            _draw_goal_event_icon(draw, start_x + (icon_w // 2), y)
        elif kind == "own_goal":
            _draw_own_goal_event_icon(draw, start_x + (icon_w // 2), y)
        elif kind == "red":
            _draw_red_event_icon(draw, start_x + (icon_w // 2), y)
        elif kind == "yellow":
            _draw_yellow_event_icon(draw, start_x + (icon_w // 2), y)


async def _download_image_bytes(url: str | None) -> bytes | None:
    if not url:
        return None

    def _fetch():
        response = requests.get(url, timeout=12)
        response.raise_for_status()
        return response.content

    try:
        return await asyncio.to_thread(_fetch)
    except Exception:
        return None


def _paste_logo(
    base: Image.Image,
    logo_bytes: bytes | None,
    center_x: int,
    center_y: int,
    logo_size: tuple[int, int] = (190, 190),
):
    if logo_bytes:
        try:
            logo = Image.open(BytesIO(logo_bytes)).convert("RGBA")
        except Exception:
            logo = None
    else:
        logo = None

    if logo is None:
        placeholder = Image.new("RGBA", logo_size, (70, 70, 70, 255))
        pdraw = ImageDraw.Draw(placeholder)
        pdraw.ellipse((6, 6, logo_size[0] - 6, logo_size[1] - 6), outline=(170, 170, 170, 255), width=3)
        base.alpha_composite(placeholder, (center_x - logo_size[0] // 2, center_y - logo_size[1] // 2))
        return

    fitted = ImageOps.contain(logo, logo_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", logo_size, (0, 0, 0, 0))
    paste_x = (logo_size[0] - fitted.width) // 2
    paste_y = (logo_size[1] - fitted.height) // 2
    canvas.alpha_composite(fitted, (paste_x, paste_y))
    base.alpha_composite(canvas, (center_x - logo_size[0] // 2, center_y - logo_size[1] // 2))


async def _get_tournament_name_for_match(match_stats_id: int) -> str:
    try:
        row = await bot.db.pool.fetchrow(
            """
            SELECT t.name AS tournament_name
            FROM TOURNAMENT_FIXTURES f
            JOIN TOURNAMENTS t ON t.id = f.tournament_id
            WHERE f.played_match_stats_id = $1
            ORDER BY f.id DESC
            LIMIT 1
            """,
            match_stats_id,
        )
        if row and row.get("tournament_name"):
            return str(row.get("tournament_name"))
    except Exception:
        pass
    return "Match Result"


async def _get_fixture_team_names_for_match(match_stats_id: int) -> tuple[str | None, str | None]:
    try:
        row = await bot.db.pool.fetchrow(
            """
            SELECT home_name_raw, away_name_raw
            FROM TOURNAMENT_FIXTURES
            WHERE played_match_stats_id = $1
            ORDER BY id DESC
            LIMIT 1
            """,
            match_stats_id,
        )
    except Exception:
        return None, None

    if not row:
        return None, None

    home_name = str(row.get("home_name_raw") or "").strip() or None
    away_name = str(row.get("away_name_raw") or "").strip() or None
    return home_name, away_name


async def _resolve_team_icon_url(team_name: str | None, guild_id: int | None):
    team = None
    if guild_id:
        try:
            team = await bot.db.teams.get_team(int(guild_id))
        except Exception:
            team = None
    if not team and team_name:
        try:
            team = await bot.db.teams.get_team_by_name(str(team_name))
        except Exception:
            team = None
    if team and team.get("guild_icon"):
        return team.get("guild_icon")

    main_guild = bot.get_guild(config.MAIN_GUILD_ID) if config.MAIN_GUILD_ID else None
    if main_guild and main_guild.icon:
        return main_guild.icon.url
    return None


async def build_match_announcement_card(
    tournament_name: str,
    match_dt: datetime | None,
    home_team_name: str,
    away_team_name: str,
    home_score: int,
    away_score: int,
    home_logo_url: str | None,
    away_logo_url: str | None,
    home_event_items: list[dict] | None = None,
    away_event_items: list[dict] | None = None,
) -> BytesIO:
    image = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (5, 12, 36, 255))
    draw = ImageDraw.Draw(image)

    # Background layers for a cleaner scoreboard look.
    vertical = Image.new("RGBA", (1, CARD_HEIGHT), color=0)
    vdraw = ImageDraw.Draw(vertical)
    for y in range(CARD_HEIGHT):
        t = y / max(1, CARD_HEIGHT - 1)
        vdraw.point((0, y), fill=(4, int(12 + 20 * t), int(40 + 25 * t), 255))
    vertical = vertical.resize((CARD_WIDTH, CARD_HEIGHT))
    image.alpha_composite(vertical, (0, 0))

    panel_rect = (16, 16, CARD_WIDTH - 16, CARD_HEIGHT - 16)
    draw.rounded_rectangle(panel_rect, radius=24, fill=(3, 14, 44, 230), outline=(26, 68, 130, 255), width=2)

    top_font = _load_card_font(23, bold=False)
    status_font = _load_card_font(22, bold=False)
    tourney_font = _fit_font(draw, tournament_name or "Match Result", max_width=780, size=40, bold=False, min_size=22)
    date_font = _load_card_font(27, bold=False)
    score_font = _load_card_font(86, bold=True)

    _, date_text = _format_card_time_date(match_dt)
    draw.text((34, 28), f"{tournament_name or 'Match Result'}", font=top_font, fill=(142, 206, 255))
    draw.text((CARD_WIDTH - 132, 28), "Full-time", font=status_font, fill=(222, 236, 255))
    _draw_centered_text(draw, CARD_WIDTH // 2, 58, tournament_name or "Match Result", tourney_font, (238, 246, 255))
    _draw_centered_text(draw, CARD_WIDTH // 2, 95, date_text, date_font, (179, 205, 233))

    left_logo_x = 220
    right_logo_x = CARD_WIDTH - 220
    logos_y = 220
    team_name_y = 330
    score_y = 216
    home_logo_bytes, away_logo_bytes = await asyncio.gather(
        _download_image_bytes(home_logo_url),
        _download_image_bytes(away_logo_url),
    )
    _paste_logo(image, home_logo_bytes, left_logo_x, logos_y, logo_size=(176, 176))
    _paste_logo(image, away_logo_bytes, right_logo_x, logos_y, logo_size=(176, 176))

    score_text = f"{int(home_score)} - {int(away_score)}"
    _draw_centered_text(draw, CARD_WIDTH // 2, score_y, score_text, score_font, (220, 236, 255))

    team_font_left = _fit_font(draw, home_team_name, max_width=400, size=56, bold=True, min_size=24)
    team_font_right = _fit_font(draw, away_team_name, max_width=400, size=56, bold=True, min_size=24)
    _draw_centered_text(draw, left_logo_x, team_name_y, home_team_name, team_font_left, (236, 244, 255))
    _draw_centered_text(draw, right_logo_x, team_name_y, away_team_name, team_font_right, (236, 244, 255))

    divider_y = 384
    draw.line((24, divider_y, CARD_WIDTH - 24, divider_y), fill=(30, 72, 132), width=2)

    event_font = _fit_font(draw, "Player Name 90', 90'", max_width=420, size=30, bold=False, min_size=20)
    left_items = home_event_items or []
    right_items = away_event_items or []
    base_y = 430
    line_step = 36
    for idx, item in enumerate(left_items[:6]):
        _draw_centered_event_line(image, draw, left_logo_x, base_y + idx * line_step, item, event_font)
    for idx, item in enumerate(right_items[:6]):
        _draw_centered_event_line(image, draw, right_logo_x, base_y + idx * line_step, item, event_font)

    footer_font = _load_card_font(20, bold=False)
    footer_text = _coerce_match_datetime(match_dt).strftime("%b %d, %Y %I:%M %p") if _coerce_match_datetime(match_dt) else date_text
    _draw_centered_text(draw, CARD_WIDTH // 2, CARD_HEIGHT - 30, footer_text, footer_font, (124, 178, 228))

    output = BytesIO()
    image.convert("RGB").save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def _normalize_match_payload(raw_match: dict) -> dict:
    home_lineup = raw_match.get("home_lineup", [])
    away_lineup = raw_match.get("away_lineup", [])

    if isinstance(home_lineup, str):
        try:
            home_lineup = json.loads(home_lineup)
        except Exception:
            home_lineup = []
    if isinstance(away_lineup, str):
        try:
            away_lineup = json.loads(away_lineup)
        except Exception:
            away_lineup = []

    match_id_value = raw_match.get("id") if raw_match.get("id") is not None else raw_match.get("match_id")
    return {
        "match_id": str(match_id_value),
        "match_id_str": raw_match.get("match_id") if isinstance(raw_match.get("match_id"), str) else raw_match.get("match_id_str"),
        "datetime": raw_match.get("datetime"),
        "home_team": raw_match.get("home_team") or raw_match.get("home_team_name") or "Home Team",
        "away_team": raw_match.get("away_team") or raw_match.get("away_team_name") or "Away Team",
        "home_guild_id": raw_match.get("home_guild_id"),
        "away_guild_id": raw_match.get("away_guild_id"),
        "scoreline": f"{int(raw_match.get('home_score') or 0)}-{int(raw_match.get('away_score') or 0)}",
        "substitutions": raw_match.get("substitutions", []),
        "home_lineup": home_lineup,
        "away_lineup": away_lineup,
        "extratime": bool(raw_match.get("extratime", False)),
        "penalties": bool(raw_match.get("penalties", False)),
    }


def _build_substitution_summary(substitutions_raw):
    if not substitutions_raw:
        return []
    if isinstance(substitutions_raw, str):
        try:
            substitutions_raw = json.loads(substitutions_raw)
        except Exception:
            return []
    if isinstance(substitutions_raw, dict):
        # Backward compatibility for older payload wrappers.
        wrapped = substitutions_raw.get("proper_subs")
        if not isinstance(wrapped, list):
            wrapped = substitutions_raw.get("substitutions")
        substitutions_raw = wrapped if isinstance(wrapped, list) else []
    if not isinstance(substitutions_raw, list):
        return []

    summary = []
    for event in substitutions_raw:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "").strip().lower()
        if kind and kind != "proper_sub":
            continue
        team = str(event.get("team", "")).lower()
        if team not in ("home", "away"):
            continue
        # Support both legacy (player_out/player_in) and current (out_player/in_player) shapes.
        player_out = event.get("player_out") if isinstance(event.get("player_out"), dict) else {}
        if not player_out:
            player_out = event.get("out_player") if isinstance(event.get("out_player"), dict) else {}
        player_in = event.get("player_in") if isinstance(event.get("player_in"), dict) else {}
        if not player_in:
            player_in = event.get("in_player") if isinstance(event.get("in_player"), dict) else {}
        left_name = player_out.get("name") or "Unknown"
        left_steam = player_out.get("steam_id") or ""
        join_name = player_in.get("name") or "Unknown"
        join_steam = player_in.get("steam_id") or ""
        summary.append((team, (str(left_name), str(left_steam)), (str(join_name), str(join_steam))))
    return summary


async def build_match_detail_embed(
    match_data: dict,
    requester: discord.abc.User,
    include_title: bool = True,
    branding_mode: str = "requester",
) -> discord.Embed:
    selected_match_id = int(match_data["match_id"])
    home_team_name = match_data["home_team"]
    away_team_name = match_data["away_team"]
    home_display_name = f"{home_team_name} (Home)" if str(home_team_name) == str(away_team_name) else home_team_name
    away_display_name = f"{away_team_name} (Away)" if str(home_team_name) == str(away_team_name) else away_team_name
    scoreline = str(match_data["scoreline"]).replace("-", " - ")

    match_player_stats = await get_player_stats_for_match_id(
        selected_match_id,
        match_data.get("match_id_str"),
    )

    home_lineup = match_data.get("home_lineup", [])
    away_lineup = match_data.get("away_lineup", [])
    if isinstance(home_lineup, str):
        try:
            home_lineup = json.loads(home_lineup)
        except Exception:
            home_lineup = []
    if isinstance(away_lineup, str):
        try:
            away_lineup = json.loads(away_lineup)
        except Exception:
            away_lineup = []

    substitution_summary = _build_substitution_summary(match_data.get("substitutions"))

    embed = discord.Embed(color=discord.Color.dark_orange())
    if include_title:
        embed.title = f"`{home_team_name}`  **{scoreline}**  `{away_team_name}`"

    home_team_info = await bot.db.teams.get_team_by_name(home_team_name)
    away_team_info = await bot.db.teams.get_team_by_name(away_team_name)
    main_guild = bot.get_guild(config.MAIN_GUILD_ID) if config.MAIN_GUILD_ID else None
    main_guild_icon = main_guild.icon.url if main_guild and main_guild.icon else None

    home_icon_url = home_team_info.get("guild_icon") if home_team_info else main_guild_icon
    away_icon_url = away_team_info.get("guild_icon") if away_team_info else main_guild_icon

    score_nums = scoreline.split(" - ")
    try:
        home_score_num = int(score_nums[0])
        away_score_num = int(score_nums[1])
    except Exception:
        home_score_num = 0
        away_score_num = 0

    if home_score_num > away_score_num:
        embed.set_thumbnail(url=home_icon_url)
    elif away_score_num > home_score_num:
        embed.set_thumbnail(url=away_icon_url)
    else:
        embed.set_thumbnail(url=home_icon_url)

    mvp_data = _get_mvp_data(match_player_stats)
    if mvp_data:
        mvp_stats_values = _normalize_mvp_key_stats(mvp_data.get('stats'))
        mvp_stats_display = " | ".join(mvp_stats_values) if mvp_stats_values else "Solid performance"
        mvp_name = f"`{mvp_data['name']}` (**{mvp_data['position']}**) : `{mvp_data['score']:.1f}/10` - {mvp_stats_display}"
    else:
        mvp_name = "No valid players found"
    mvp_player_name = mvp_data.get("name") if mvp_data else None
    embed.add_field(name="🏆 MVP", value=mvp_name, inline=False)

    best_defender_data = _get_best_defender_data(match_player_stats)
    best_defender_player_name = best_defender_data.get("name") if best_defender_data else None
    best_gk_name = get_best_goalkeeper(match_player_stats)
    award_highlight_names = [mvp_player_name, best_defender_player_name, best_gk_name]

    home_stats = []
    away_stats = []

    if home_lineup and away_lineup:
        position_order = ["GK", "LB", "CB", "RB", "CM", "LW", "CF", "RW"]

        home_stats = _filter_stats_for_lineup(
            match_player_stats,
            home_lineup,
            team_name=home_team_name,
            team_guild_id=match_data.get("home_guild_id"),
        )
        away_stats = _filter_stats_for_lineup(
            match_player_stats,
            away_lineup,
            team_name=away_team_name,
            team_guild_id=match_data.get("away_guild_id"),
        )
        if not home_stats:
            home_stats = match_player_stats
        if not away_stats:
            away_stats = match_player_stats

        home_lineup_formatted = format_team_lineup(
            home_team_name,
            home_lineup,
            position_order,
            home_stats,
            substitution_summary,
            home_team_name,
            away_team_name,
            team_guild_id=match_data.get("home_guild_id"),
            highlight_names=award_highlight_names,
            mvp_name=mvp_player_name,
        )
        away_lineup_formatted = format_team_lineup(
            away_team_name,
            away_lineup,
            position_order,
            away_stats,
            substitution_summary,
            home_team_name,
            away_team_name,
            team_guild_id=match_data.get("away_guild_id"),
            highlight_names=award_highlight_names,
            mvp_name=mvp_player_name,
        )

        embed.add_field(name=f"{home_display_name}'s Lineup", value=f"```diff\n{home_lineup_formatted}```", inline=True)
        embed.add_field(name=f"{away_display_name}'s Lineup", value=f"```diff\n{away_lineup_formatted}```", inline=True)

        if substitution_summary:
            subs_text = format_substitutions(substitution_summary, match_player_stats)
            embed.add_field(name="🔄 SUBS", value=f"```{subs_text}```", inline=False)
    else:
        embed.add_field(name="Players", value="Detailed lineup data not available for this match.", inline=False)

    if not home_stats:
        home_stats = [p for p in match_player_stats if p.get("guild_id") == match_data.get("home_guild_id")]
    if not away_stats:
        away_stats = [p for p in match_player_stats if p.get("guild_id") == match_data.get("away_guild_id")]

    home_derived = _build_match_derived_summary(home_stats)
    away_derived = _build_match_derived_summary(away_stats)

    def _derived_text(side_summary: dict) -> str:
        return (
            f"S/Sub/B: `{side_summary['starts']}/{side_summary['subs']}/{side_summary['bench']}`\n"
            f"Clutch: `{side_summary['clutch_events']}` | Sub impact: `{side_summary['sub_events']}`\n"
            f"Sub G/OG: `{side_summary['sub_goals']}/{side_summary['sub_own_goals']}`"
        )

    embed.add_field(
        name="📘 Derived Metrics Guide",
        value=(
            "`S/Sub/B` = Started / Substitute / On Bench\n"
            "`Clutch` = Late decisive actions while drawing or losing\n"
            "`Sub impact` = Events shortly after substitutions\n"
            "`Sub G/OG` = Goals / Own Goals in sub-impact windows"
        ),
        inline=False
    )
    embed.add_field(name=f"🧠 {home_display_name} Derived", value=_derived_text(home_derived), inline=False)
    embed.add_field(name=f"🧠 {away_display_name} Derived", value=_derived_text(away_derived), inline=False)

    summary_home = match_data.get("match_summary_home") or []
    summary_away = match_data.get("match_summary_away") or []
    def _summary_counts(summary_rows) -> tuple[int, int, int, int]:
        goals = yellows = reds = own_goals = 0
        if not isinstance(summary_rows, list):
            return goals, yellows, reds, own_goals
        for row in summary_rows:
            if not isinstance(row, dict):
                continue
            goals += len(row.get("goals") or [])
            yellows += len(row.get("yellow_cards") or [])
            reds += len(row.get("red_cards") or [])
            own_goals += len(row.get("own_goals") or [])
        return goals, yellows, reds, own_goals

    hg, hy, hr, hog = _summary_counts(summary_home)
    ag, ay, ar, aog = _summary_counts(summary_away)
    comeback_text = "Yes" if bool(match_data.get("comeback_flag")) else "No"
    embed.add_field(
        name="📈 Match Summary",
        value=(
            f"Comeback: `{comeback_text}` (winner came from behind)\n"
            f"{home_display_name}: `G{hg} Y{hy} R{hr} OG{hog}`\n"
            f"{away_display_name}: `G{ag} Y{ay} R{ar} OG{aog}`"
        ),
        inline=False
    )

    if best_defender_data:
        best_defender_name = (
            f"`{best_defender_data['name']}`  :|: "
            f"**Interceptions:** `{best_defender_data['interceptions']}` "
            f"**Successful Slide Tackles:** `{best_defender_data['slide_tackles']}`"
        )
    else:
        best_defender_name = "No defenders found"
    embed.add_field(name="🛡️ Best Defender", value=best_defender_name, inline=False)
    if best_gk_name:
        embed.add_field(name="🧤 Best Goalkeeper", value=best_gk_name, inline=False)

    embed.add_field(
        name="IOSCA Hub",
        value=f"[Visit the IOSCA Hub to view this match.]({_build_hub_match_url(selected_match_id)})",
        inline=False,
    )

    if branding_mode == "bot" and bot.user:
        bot_icon = bot.user.display_avatar.url if bot.user.display_avatar else None
        embed.set_author(name=bot.user.name, icon_url=bot_icon)
        embed.set_footer(text=bot.user.name, icon_url=bot_icon)
    else:
        embed.set_author(name=f"{requester.name}", icon_url=requester.display_avatar.url if requester.display_avatar else None)
        embed.set_footer(text=f"Requested by {requester.name}")
    return embed


class MatchSelect(Select):
    def __init__(self, matches_on_page, all_matches=None):
        # Keep a reference to the already-fetched full match list (held by
        # the parent MatchHistoryView) so selecting an option doesn't need to
        # re-query every match in the database just to find this one by id.
        self.all_matches = all_matches if all_matches is not None else matches_on_page
        options = []
        for match in matches_on_page:
            home_team = match['home_team']
            away_team = match['away_team']
            score = match['scoreline'].replace('-', ' - ')
            dt = match.get('datetime')
            if isinstance(dt, datetime):
                date = dt.strftime('%b %d, %Y')
            else:
                try:
                    date = datetime.strptime(str(dt), '%Y-%m-%d %H:%M:%S').strftime('%b %d, %Y')
                except Exception:
                    date = "Unknown"
            
            label = truncate_name(f"{home_team} vs {away_team} ({score})", max_length=100)
            description = truncate_name(f"Played on {date}", max_length=100)

            options.append(discord.SelectOption(
                label=label,
                description=description,
                value=match['match_id']
            ))
        super().__init__(placeholder="Select a match to view details...", options=options)
    
    async def callback(self, interaction: discord.Interaction):
        try:
            try:
                await interaction.response.defer()
            except discord.InteractionResponded:
                pass
            except discord.NotFound:
                return
            selected_match_id = int(self.values[0])

            match_data = next((m for m in self.all_matches if int(m['match_id']) == selected_match_id), None)
            if not match_data:
                # Fall back to a full refetch only if the in-memory list is
                # somehow stale/missing the selection (e.g. long-lived view).
                all_matches = await get_matches()
                match_data = next((m for m in all_matches if int(m['match_id']) == selected_match_id), None)

            if not match_data:
                await _safe_interaction_message(interaction, "Could not find the selected match. Please try again.")
                return

            embed = await build_match_detail_embed(match_data, interaction.user)
            await interaction.edit_original_response(embed=embed)
        except Exception as e:
            logger.warning("view_match select failed: %s", e)
            try:
                await _safe_interaction_message(interaction, "Failed to load match details.")
            except Exception:
                pass


class MatchHistoryView(View):
    def __init__(self, interaction, all_matches):
        super().__init__(timeout=180)
        self.interaction = interaction
        self.all_matches = all_matches
        self.current_page = 0
        self.matches_per_page = 25
        self.total_pages = (len(self.all_matches) - 1) // self.matches_per_page + 1
        
        self.prev_page_button = Button(label="Previous", style=discord.ButtonStyle.grey)
        self.next_page_button = Button(label="Next", style=discord.ButtonStyle.grey)
        
        self.prev_page_button.callback = self.prev_page_callback
        self.next_page_button.callback = self.next_page_callback
        
        self.update_view()
    
    def update_view(self):
        """Clears and adds items for the current page."""
        self.clear_items()
        start_index = self.current_page * self.matches_per_page
        end_index = start_index + self.matches_per_page
        matches_on_page = self.all_matches[start_index:end_index]
        
        self.add_item(MatchSelect(matches_on_page, self.all_matches))
        self.add_item(self.prev_page_button)
        self.add_item(self.next_page_button)
        self.update_button_states()
    
    def update_button_states(self):
        """Disables/enables previous/next buttons based on the current page."""
        self.prev_page_button.disabled = self.current_page == 0
        self.next_page_button.disabled = self.current_page >= self.total_pages - 1
        self.prev_page_button.label = f"Page {self.current_page + 1}/{self.total_pages}"
    
    async def prev_page_callback(self, interaction: discord.Interaction):
        self.current_page -= 1
        self.update_view()
        await interaction.response.edit_message(view=self)
    
    async def next_page_callback(self, interaction: discord.Interaction):
        self.current_page += 1
        self.update_view()
        await interaction.response.edit_message(view=self)


@bot.slash_command(name="view_match", description="View past match summaries.")
async def view_match(interaction: discord.Interaction):
    """Displays a paginated and selectable list of past matches."""
    try:
        try:
            await interaction.response.defer()
        except discord.InteractionResponded:
            pass
        except discord.NotFound:
            return

        all_matches = await get_matches()
        if not all_matches:
            await _safe_interaction_message(interaction, "No match data is available at the moment.")
            return
        
        view = MatchHistoryView(interaction, all_matches)
        await _safe_interaction_message(interaction, "Please select a match to view its summary.", view=view, ephemeral=False)
    except Exception as e:
        try:
            from ios_bot.error_logger import log_error
            log_error(e, context={"command": "view_match"}, user_id=interaction.user.id, guild_id=interaction.guild_id, command="view_match")
        except Exception:
            pass
        try:
            await _safe_interaction_message(interaction, "Failed to load matches.")
        except Exception:
            pass


async def _list_played_fixture_matches(limit: int = 300) -> list[dict]:
    rows = await bot.db.pool.fetch(
        """
        SELECT
            fixture_id,
            tournament_id,
            tournament_name,
            week_number,
            week_label,
            match_stats_id,
            home_team_name,
            away_team_name,
            home_score,
            away_score,
            match_datetime
        FROM (
            SELECT
                f.id AS fixture_id,
                f.tournament_id,
                t.name AS tournament_name,
                f.week_number,
                f.week_label,
                f.played_match_stats_id AS match_stats_id,
                COALESCE(ht.guild_name, f.home_name_raw, m.home_team_name) AS home_team_name,
                COALESCE(at.guild_name, f.away_name_raw, m.away_team_name) AS away_team_name,
                m.home_score,
                m.away_score,
                m.datetime AS match_datetime,
                ROW_NUMBER() OVER (
                    PARTITION BY f.played_match_stats_id
                    ORDER BY f.id DESC
                ) AS rn
            FROM TOURNAMENT_FIXTURES f
            JOIN TOURNAMENTS t ON t.id = f.tournament_id
            JOIN MATCH_STATS m ON m.id = f.played_match_stats_id
            LEFT JOIN IOSCA_TEAMS ht ON ht.guild_id = f.home_guild_id
            LEFT JOIN IOSCA_TEAMS at ON at.guild_id = f.away_guild_id
            WHERE f.played_match_stats_id IS NOT NULL
              AND COALESCE(t.status, '') = 'active'
        ) ranked
        WHERE rn = 1
        ORDER BY match_datetime DESC NULLS LAST, match_stats_id DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]


def _build_hub_match_url(match_stats_id: int) -> str:
    base = (HUB_MATCH_PAGE_URL_BASE or "https://ramymomo20.github.io/ioscahub.github.io/#/matches").rstrip("/")
    return f"{base}/{quote(str(int(match_stats_id)), safe='')}"


def _build_hub_main_page_url() -> str:
    base = HUB_MAIN_PAGE_URL or "https://ramymomo20.github.io/ioscahub.github.io/#/"
    return base.strip() or "https://ramymomo20.github.io/ioscahub.github.io/#/"


def _build_hub_match_display_url(match_stats_id: int) -> str:
    base = (HUB_MATCH_SHORT_BASE or "https://iosca_hub/match/id=").strip()
    if not base:
        base = "https://iosca_hub/match/id="
    return f"{base}{int(match_stats_id)}"


def _build_hub_match_markdown_link(match_stats_id: int, label: str | None = None) -> str:
    display_url = label or _build_hub_match_display_url(match_stats_id)
    target_url = _build_hub_match_url(match_stats_id)
    return f"[{display_url}]({target_url})"


async def _capture_hub_match_card(match_stats_id: int) -> BytesIO | None:
    global _HUB_CAPTURE_DISABLED_REASON

    if _HUB_CAPTURE_DISABLED_REASON is None:
        _HUB_CAPTURE_DISABLED_REASON = "pil_only_renderer"
        logger.info("announce_match: external hub screenshot capture disabled; using PIL renderer")
    return None


async def _post_match_announcement(match_stats_id: int, requester: discord.abc.User, target_channel: discord.abc.Messageable) -> tuple[bool, str]:
    try:
        match_row = await bot.db.matches.get_match(int(match_stats_id))
    except Exception as e:
        logger.error("announce_match: failed to fetch match %s: %s", match_stats_id, e)
        match_row = None

    if not match_row:
        return False, f"Match `{match_stats_id}` was not found."

    match_payload = _normalize_match_payload(match_row)
    home_score = int(match_row.get("home_score") or 0)
    away_score = int(match_row.get("away_score") or 0)
    home_team_name = match_payload.get("home_team") or "Home Team"
    away_team_name = match_payload.get("away_team") or "Away Team"
    if str(home_team_name).strip() == str(away_team_name).strip():
        fixture_home_name, fixture_away_name = await _get_fixture_team_names_for_match(int(match_stats_id))
        if fixture_home_name and fixture_away_name and fixture_home_name != fixture_away_name:
            home_team_name = fixture_home_name
            away_team_name = fixture_away_name

    match_player_stats = await get_player_stats_for_match_id(
        int(match_payload.get("match_id") or match_stats_id),
        match_payload.get("match_id_str"),
    )
    side_home_stats, side_away_stats = _group_stats_by_team_side(match_player_stats)
    home_stats = _filter_stats_for_lineup(
        match_player_stats,
        match_payload.get("home_lineup") or [],
        team_name=home_team_name,
        team_guild_id=match_payload.get("home_guild_id"),
    )
    away_stats = _filter_stats_for_lineup(
        match_player_stats,
        match_payload.get("away_lineup") or [],
        team_name=away_team_name,
        team_guild_id=match_payload.get("away_guild_id"),
    )
    if not home_stats and side_home_stats:
        home_stats = side_home_stats
    if not away_stats and side_away_stats:
        away_stats = side_away_stats
    if not home_stats and match_payload.get("home_guild_id") is not None:
        home_stats = [
            p for p in match_player_stats
            if p.get("guild_id") is not None and str(p.get("guild_id")) == str(match_payload.get("home_guild_id"))
        ]
    if not away_stats and match_payload.get("away_guild_id") is not None:
        away_stats = [
            p for p in match_player_stats
            if p.get("guild_id") is not None and str(p.get("guild_id")) == str(match_payload.get("away_guild_id"))
        ]
    if (
        home_stats
        and away_stats
        and _stats_bucket_signature(home_stats) == _stats_bucket_signature(away_stats)
        and side_home_stats
        and side_away_stats
        and _stats_bucket_signature(side_home_stats) != _stats_bucket_signature(side_away_stats)
    ):
        home_stats = side_home_stats
        away_stats = side_away_stats

    home_event_items = _build_team_announcement_items(home_stats)
    away_event_items = _build_team_announcement_items(away_stats)

    tournament_name = await _get_tournament_name_for_match(int(match_stats_id))
    home_logo_url, away_logo_url = await asyncio.gather(
        _resolve_team_icon_url(home_team_name, match_payload.get("home_guild_id")),
        _resolve_team_icon_url(away_team_name, match_payload.get("away_guild_id")),
    )

    hub_main_url = _build_hub_main_page_url()
    hub_match_link = _build_hub_match_markdown_link(int(match_stats_id), label="IOSCA Hub")
    score_block = _build_ansi_scoreline(home_team_name, home_score, away_score, away_team_name)
    announce_embed = discord.Embed(
        title="⚽ Match Concluded",
        description=(
            f"**FULL TIME:**\n```ansi\n{score_block}\n```\n"
            f"The match overview is now available to view on {hub_match_link}."
        ),
        color=discord.Color.from_rgb(255, 255, 255),
    )
    if bot.user:
        bot_icon = bot.user.display_avatar.url if bot.user.display_avatar else None
        announce_embed.set_author(name=bot.user.name, icon_url=bot_icon)
        announce_embed.set_footer(text=bot.user.name, icon_url=bot_icon)
    else:
        announce_embed.set_footer(text="IOSCA")

    card_buffer = None
    try:
        card_buffer = await build_match_announcement_card(
            tournament_name=tournament_name,
            match_dt=_coerce_match_datetime(match_payload.get("datetime")),
            home_team_name=home_team_name,
            away_team_name=away_team_name,
            home_score=home_score,
            away_score=away_score,
            home_logo_url=home_logo_url,
            away_logo_url=away_logo_url,
            home_event_items=home_event_items,
            away_event_items=away_event_items,
        )
    except Exception as e:
        logger.warning("announce_match: failed to build PIL card for match %s: %s", match_stats_id, e)

    if card_buffer is not None:
        image_name = f"match_card_{int(match_stats_id)}.png"
        announce_embed.set_image(url=f"attachment://{image_name}")
        await target_channel.send(embed=announce_embed, file=discord.File(fp=card_buffer, filename=image_name))
    else:
        await target_channel.send(embed=announce_embed)
    return True, f"Announcement posted in {target_channel.mention}."


class AnnounceMatchSelect(Select):
    def __init__(self, rows_on_page: list[dict], author_id: int):
        self.author_id = author_id
        options = []
        for row in rows_on_page:
            tournament_name = str(row.get("tournament_name") or "Tournament")
            home = str(row.get("home_team_name") or "Home")
            away = str(row.get("away_team_name") or "Away")
            home_score = int(row.get("home_score") or 0)
            away_score = int(row.get("away_score") or 0)
            week = row.get("week_label") or (f"Jornada {row.get('week_number')}" if row.get("week_number") else "Jornada")
            dt = row.get("match_datetime")
            if isinstance(dt, datetime):
                date_text = dt.strftime("%b %d, %Y %H:%M UTC")
            else:
                date_text = "Date unknown"
            label = truncate_name(f"{tournament_name} | {home} {home_score}-{away_score} {away}", max_length=100)
            description = truncate_name(f"{week} • {date_text}", max_length=100)
            value = str(int(row["match_stats_id"]))
            options.append(discord.SelectOption(label=label, description=description, value=value))
        super().__init__(placeholder="Select a match to announce...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("You are not authorized to use this menu.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            selected_match_id = int(self.values[0])
        except Exception:
            await interaction.followup.send("Invalid match selection.", ephemeral=True)
            return

        ok, message = await _post_match_announcement(selected_match_id, interaction.user, interaction.channel)
        await interaction.followup.send(message, ephemeral=True)


class AnnounceMatchPickerView(View):
    def __init__(self, author_id: int, rows: list[dict]):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.rows = rows
        self.page = 0
        self.page_size = 25
        self.prev_button = Button(label="Previous", style=discord.ButtonStyle.secondary)
        self.next_button = Button(label="Next", style=discord.ButtonStyle.secondary)
        self.prev_button.callback = self.prev_page
        self.next_button.callback = self.next_page
        self.update_view()

    def update_view(self):
        self.clear_items()
        start = self.page * self.page_size
        end = start + self.page_size
        page_rows = self.rows[start:end]
        self.add_item(AnnounceMatchSelect(page_rows, self.author_id))
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = end >= len(self.rows)
        total_pages = max(1, (len(self.rows) - 1) // self.page_size + 1)
        self.prev_button.label = f"Page {self.page + 1}/{total_pages}"

    async def prev_page(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("You are not authorized to use this menu.", ephemeral=True)
            return
        self.page = max(0, self.page - 1)
        self.update_view()
        await interaction.response.edit_message(view=self)

    async def next_page(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("You are not authorized to use this menu.", ephemeral=True)
            return
        self.page = min((len(self.rows) - 1) // self.page_size, self.page + 1)
        self.update_view()
        await interaction.response.edit_message(view=self)


@bot.slash_command(name="announce_match", description="Post a match result graphic with scorers and red cards.")
@commands.has_permissions(administrator=True)
async def announce_match(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)

    try:
        played_rows = await _list_played_fixture_matches(limit=300)
    except Exception as e:
        logger.error("announce_match: failed to list played fixtures: %s", e)
        played_rows = []

    if not played_rows:
        await ctx.followup.send("No played matches were found under active tournaments.", ephemeral=True)
        return

    view = AnnounceMatchPickerView(ctx.user.id, played_rows)
    await ctx.followup.send("Select a match to announce:", view=view, ephemeral=True)

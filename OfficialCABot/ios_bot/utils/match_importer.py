"""
Match JSON importer for ios_bot using iosca_bot parser.
Imports match data from JSON files into PostgreSQL database.
"""

import logging
from ios_bot.config import MAIN_GUILD_ID
import ios_bot.config as config_module
from typing import Optional, Dict, Any, List
from datetime import datetime
from pathlib import Path
import sys
import discord

# Import the json_parser from ios_bot
from ios_bot.utils.json_parser import (
    parse_match_json,
    build_enhanced_player_data,
    build_player_event_timestamps,
    build_match_event_locations,
)
from ios_bot.utils.match_performance import rate_player, get_mvp_data

logger = logging.getLogger(__name__)


class MatchImporter:
    """Imports match data from JSON files into the database."""
    
    def __init__(self, db):
        """
        Initialize the match importer.
        
        Args:
            db: Database instance (ios_bot.db.Database)
        """
        self.db = db
    
    async def import_match_from_json(
        self,
        json_data: dict,
        match_id_str: Optional[str] = None,
        league_name: Optional[str] = None,
        source_filename: Optional[str] = None,
        existing_match_stats_id: Optional[int] = None,
        announce_completion: bool = True,
    ) -> Optional[int]:
        """
        Import a match from parsed JSON data.
        
        Args:
            json_data: Parsed JSON dict from match file
            league_name: Optional league name
            
        Returns:
            Match ID if successful, None otherwise
        """
        try:
            # Parse match data using iosca_bot parser
            match_data = parse_match_json(json_data)
            if not match_data:
                logger.error("Failed to parse match JSON")
                return None
            
            home_name_raw = (match_data.get('home_team') or "").strip()
            away_name_raw = (match_data.get('away_team') or "").strip()

            home_team = None
            away_team = None
            home_guild_id = None
            away_guild_id = None

            # Find teams by name (optional - matches can be imported without registered teams)
            if home_guild_id is None:
                home_team = await self.db.teams.get_team_by_name(home_name_raw)
            if away_guild_id is None:
                away_team = await self.db.teams.get_team_by_name(away_name_raw)

            # Use guild_id if team is registered, otherwise attempt fuzzy match
            if home_guild_id is None:
                home_guild_id = home_team['guild_id'] if home_team else None
            if away_guild_id is None:
                away_guild_id = away_team['guild_id'] if away_team else None

            # Known name variants (main-guild aliases like "IOSCA A", country
            # pickup labels, or any other team's alternate spellings) checked
            # next, before falling back to fuzzy matching -- deterministic
            # instead of a similarity guess for anything already seen before.
            if home_guild_id is None:
                aliased_home = await self.db.teams.get_team_by_alias(home_name_raw)
                if aliased_home:
                    home_guild_id = aliased_home['guild_id']
            if away_guild_id is None:
                aliased_away = await self.db.teams.get_team_by_alias(away_name_raw)
                if aliased_away:
                    away_guild_id = aliased_away['guild_id']

            if home_guild_id is None:
                best_home = await self.db.teams.find_best_team_match(home_name_raw, threshold=0.8)
                if best_home:
                    home_guild_id = best_home['guild_id']
                    logger.info(
                        f"Fuzzy matched home team '{match_data['home_team']}' -> "
                        f"'{best_home['guild_name']}' ({best_home['similarity']:.2f})"
                    )

            if away_guild_id is None:
                best_away = await self.db.teams.find_best_team_match(away_name_raw, threshold=0.8)
                if best_away:
                    away_guild_id = best_away['guild_id']
                    logger.info(
                        f"Fuzzy matched away team '{match_data['away_team']}' -> "
                        f"'{best_away['guild_name']}' ({best_away['similarity']:.2f})"
                    )
            
            home_registered = home_guild_id is not None
            away_registered = away_guild_id is not None

            # Server identity is the strongest signal for linking this played
            # match back to whatever /ready registered before kickoff -- see
            # resolve_active_match_context's game_server_id scoring.
            game_server_id = None
            try:
                in_game_server_name = (
                    json_data.get("matchData", {}).get("matchInfo", {}).get("serverName")
                )
                if in_game_server_name and hasattr(self.db, "servers") and self.db.servers:
                    server_row = await self.db.servers.get_server_by_ingame_name(in_game_server_name)
                    if server_row:
                        game_server_id = server_row.get("id")
            except Exception as server_err:
                logger.warning(f"Could not resolve game_server_id from match JSON: {server_err}")

            if not home_registered or not away_registered:
                logger.info(
                    f"Importing match with unregistered teams: "
                    f"Home={match_data['home_team']} (registered={home_registered}), "
                    f"Away={match_data['away_team']} (registered={away_registered})"
                )
            
            # Build enriched player/lineup payload.
            enhanced_players = build_enhanced_player_data(json_data)
            player_event_timestamps = build_player_event_timestamps(match_data.get("events", []))
            match_event_locations = build_match_event_locations(json_data)
            lineup_analysis = match_data.get("lineup_analysis") or {}
            player_derived = match_data.get("player_derived") or {}
            match_summary_home = match_data.get("match_summary_home") or []
            match_summary_away = match_data.get("match_summary_away") or []
            comeback_flag = bool(match_data.get("comeback_flag"))
            raw_substitutions = match_data.get("substitutions") or []

            # Persist only true substitutions to keep payload size stable.
            proper_substitutions: List[Dict[str, Any]] = []
            for sub in raw_substitutions:
                if str((sub or {}).get("kind") or "").lower() != "proper_sub":
                    continue
                proper_substitutions.append(
                    {
                        "time": int((sub or {}).get("time") or 0),
                        "team": (sub or {}).get("team"),
                        "position": (sub or {}).get("position"),
                        "kind": "proper_sub",
                        "out_player": (sub or {}).get("out_player") or {},
                        "in_player": (sub or {}).get("in_player") or {},
                    }
                )

            home_lineup = []
            away_lineup = []
            starter_ids = {"home": set(), "away": set()}

            def _collect_side_lineup(side: str) -> List[Dict[str, Any]]:
                output: List[Dict[str, Any]] = []
                starters_map = ((lineup_analysis.get("starting_lineups") or {}).get(side) or {})
                players_meta = lineup_analysis.get("players") or {}
                known_ids = set()

                for pos, entry in starters_map.items():
                    if not isinstance(entry, dict):
                        continue
                    steam_id = entry.get("steam_id") or entry.get("player_id")
                    if not steam_id:
                        continue
                    starter_ids[side].add(steam_id)
                    known_ids.add(steam_id)
                    output.append(
                        {
                            "steam_id": steam_id,
                            "name": entry.get("name") or enhanced_players.get(steam_id, {}).get("name") or "Unknown",
                            "position": str(entry.get("position") or pos or "Unknown").upper(),
                            "started": True,
                        }
                    )

                # Add remaining participating players on this side as non-starters.
                for steam_id, p in enhanced_players.items():
                    teams_played = p.get("teamsPlayedFor", [])
                    if side not in teams_played:
                        continue
                    if steam_id in known_ids:
                        continue
                    derived = player_derived.get(steam_id, {})
                    status = str(derived.get("status") or "substitute")
                    output.append(
                        {
                            "steam_id": steam_id,
                            "name": p.get("name", "Unknown"),
                            "position": str(
                                p.get("mainPositionByTeam", {}).get(side)
                                or derived.get("main_position")
                                or "Unknown"
                            ).upper(),
                            "started": status == "started",
                        }
                    )
                    known_ids.add(steam_id)

                # Include explicit on-bench rows when they are clearly assigned to this side.
                for steam_id, meta in players_meta.items():
                    if steam_id in known_ids:
                        continue
                    if meta.get("main_team") != side:
                        continue
                    status = str(meta.get("status") or "")
                    if status != "on_bench":
                        continue
                    output.append(
                        {
                            "steam_id": steam_id,
                            "name": meta.get("name") or enhanced_players.get(steam_id, {}).get("name") or "Unknown",
                            "position": str(meta.get("main_position") or "Unknown").upper(),
                            "started": False,
                        }
                    )
                    known_ids.add(steam_id)

                return output

            home_lineup = _collect_side_lineup("home")
            away_lineup = _collect_side_lineup("away")
            
            # Determine game type from player count
            total_players = len(home_lineup) + len(away_lineup)
            num_players_per_side = match_data['game_format']
            full_game_type = f"{num_players_per_side}v{num_players_per_side}"
            
            if existing_match_stats_id is not None:
                match_id = int(existing_match_stats_id)
                updated = await self.db.matches.update_match(
                    match_stats_id=match_id,
                    home_guild_id=home_guild_id,
                    away_guild_id=away_guild_id,
                    home_score=match_data['home_score'],
                    away_score=match_data['away_score'],
                    match_datetime=match_data['datetime'],
                    home_team_name=match_data['home_team'],
                    away_team_name=match_data['away_team'],
                    extratime=match_data.get('extratime', False),
                    penalties=match_data.get('penalties', False),
                    substitutions=proper_substitutions,
                    home_lineup=home_lineup,
                    away_lineup=away_lineup,
                    match_id_str=match_id_str,
                    source_filename=source_filename,
                    game_type=full_game_type,
                    match_summary_home=match_summary_home,
                    match_summary_away=match_summary_away,
                    comeback_flag=comeback_flag,
                )
                if not updated:
                    logger.error("Failed to update existing match %s", match_id)
                    return None
            else:
                # Add match to database
                match_id = await self.db.matches.add_match(
                    home_guild_id=home_guild_id,
                    away_guild_id=away_guild_id,
                    home_score=match_data['home_score'],
                    away_score=match_data['away_score'],
                    match_datetime=match_data['datetime'],
                    home_team_name=match_data['home_team'],
                    away_team_name=match_data['away_team'],
                    extratime=match_data.get('extratime', False),
                    penalties=match_data.get('penalties', False),
                    substitutions=proper_substitutions,
                    home_lineup=home_lineup,
                    away_lineup=away_lineup,
                    match_id_str=match_id_str,
                    source_filename=source_filename,
                    game_type=full_game_type,
                    match_summary_home=match_summary_home,
                    match_summary_away=match_summary_away,
                    comeback_flag=comeback_flag,
                )
            
            if not match_id:
                logger.error("Failed to add match to database")
                return None
            
            logger.info(f"✅ Match imported: {match_data['home_team']} {match_data['home_score']}-{match_data['away_score']} {match_data['away_team']}")

            try:
                if match_event_locations and hasattr(self.db.matches, "replace_match_events"):
                    result = await self.db.matches.replace_match_events(
                        match_stats_id=match_id,
                        match_id_str=match_id_str,
                        events=match_event_locations,
                        prune_existing=existing_match_stats_id is None,
                    )
                    logger.info(
                        "Stored %s match event location rows for match %s",
                        result.get("inserted", 0),
                        match_id,
                    )
            except Exception as event_err:
                logger.warning(f"Match event location persistence skipped for {match_id}: {event_err}")
            
            # Import player match data - stores ALL players by steam_id regardless of registration
            await self._import_player_stats(
                match_id=match_id,  # Integer match ID from database
                match_datetime=match_data['datetime'],
                enhanced_players=enhanced_players,
                home_team_name=match_data['home_team'],
                away_team_name=match_data['away_team'],
                home_guild_id=home_guild_id,
                away_guild_id=away_guild_id,
                home_score=match_data['home_score'],
                away_score=match_data['away_score'],
                player_event_timestamps=player_event_timestamps,
                player_derived=player_derived,
                lineup_analysis=lineup_analysis,
                update_existing=existing_match_stats_id is not None,
            )

            # If /ready started this as a tournament server, consume that stored
            # fixture context first so the imported match binds to the scheduled
            # fixture instead of relying on later pair-matching heuristics.
            try:
                if hasattr(self.db, "matches") and self.db.matches and hasattr(self.db, "tournaments") and self.db.tournaments:
                    tournament_context = await self.db.matches.resolve_active_match_context(
                        home_team_name=match_data['home_team'],
                        away_team_name=match_data['away_team'],
                        home_guild_id=home_guild_id,
                        away_guild_id=away_guild_id,
                        game_type=full_game_type,
                        source_kind="tournament",
                        require_tournament_fixture=True,
                        game_server_id=game_server_id,
                    )
                    if tournament_context:
                        context_tournament_id = tournament_context.get("tournament_id")
                        context_fixture_id = tournament_context.get("fixture_id")
                        if context_tournament_id and context_fixture_id:
                            linked = await self.db.tournaments.add_match_by_id(
                                int(context_tournament_id),
                                int(match_id),
                                preferred_fixture_id=int(context_fixture_id),
                            )
                            logger.info(
                                "Tournament context link for imported match %s: tournament=%s fixture=%s linked=%s",
                                match_id,
                                context_tournament_id,
                                context_fixture_id,
                                linked,
                            )
            except Exception as context_err:
                logger.warning(f"Direct tournament context link skipped after import {match_id}: {context_err}")

            # Keep tournament linkage up-to-date after each imported match.
            try:
                if hasattr(self.db, "tournaments") and self.db.tournaments:
                    await self.db.tournaments.sync_matches_for_all_active()
            except Exception as sync_err:
                logger.warning(f"Tournament match sync skipped after import: {sync_err}")

            # Push freshly imported matches into the Hub immediately so Discord
            # announcements and hub pages stay in sync with tournament results.
            try:
                from ios_bot import tasks as tasks_module
                await tasks_module.run_immediate_hub_refresh(
                    reason=f"match_import:{match_id}",
                    refresh_story_models=False,
                )
            except Exception as hub_refresh_err:
                logger.warning(f"Immediate Hub refresh skipped after import {match_id}: {hub_refresh_err}")

            if announce_completion:
                try:
                    await self._announce_imported_match_completion(match_id, game_server_id=game_server_id)
                except Exception as announce_err:
                    logger.warning(f"Post-import match announcement skipped for {match_id}: {announce_err}")

            return match_id

        except Exception as e:
            logger.error(f"Error importing match from JSON: {e}", exc_info=True)
            return None

    async def _announce_imported_match_completion(self, match_stats_id: int, game_server_id: Optional[int] = None) -> None:
        try:
            match_row = await self.db.matches.get_match(int(match_stats_id))
        except Exception as e:
            logger.warning(f"Could not fetch imported match {match_stats_id} for announcement: {e}")
            return

        if not match_row:
            return

        channel_ids = await self.db.matches.resolve_active_match_announcement_channels(
            match_stats_id=int(match_stats_id),
            home_team_name=str(match_row.get("home_team_name") or ""),
            away_team_name=str(match_row.get("away_team_name") or ""),
            home_guild_id=match_row.get("home_guild_id"),
            away_guild_id=match_row.get("away_guild_id"),
            game_type=match_row.get("game_type"),
            game_server_id=game_server_id,
        )
        if not channel_ids:
            channel_ids = await self._fallback_announcement_channel_ids(match_row)
        if not channel_ids:
            return

        try:
            from ios_bot import bot
            from ios_bot.commands.view_match import _post_match_announcement
        except Exception as e:
            logger.warning(f"Could not import bot announcement helpers: {e}")
            return

        if not getattr(bot, "user", None):
            return

        for channel_id in channel_ids:
            try:
                channel = bot.get_channel(int(channel_id))
                if channel is None:
                    fetched = await bot.fetch_channel(int(channel_id))
                    channel = fetched if isinstance(fetched, discord.abc.Messageable) else None
                if channel is None:
                    continue
                ok, message = await _post_match_announcement(int(match_stats_id), bot.user, channel)
                if not ok:
                    logger.warning(
                        "Match %s announcement to channel %s failed: %s",
                        match_stats_id,
                        channel_id,
                        message,
                    )
            except Exception as e:
                logger.warning(
                    "Match %s announcement to channel %s errored: %s",
                    match_stats_id,
                    channel_id,
                    e,
                )

    async def _fallback_announcement_channel_ids(self, match_row: Dict[str, Any]) -> list[int]:
        """Fallback routing when exact ready-channel context cannot be matched."""
        game_type = str(match_row.get("game_type") or "").strip().lower()
        channel_ids: list[int] = []

        def _dedupe_add(value: Any) -> None:
            try:
                channel_id = int(value)
            except Exception:
                return
            if channel_id > 0 and channel_id not in channel_ids:
                channel_ids.append(channel_id)

        def _channel_list_for_team(team_data: Dict[str, Any] | None) -> list[int]:
            if not isinstance(team_data, dict):
                return []
            if game_type == "6v6":
                return list(team_data.get("sixes_channels") or [])
            if game_type == "8v8":
                return list(team_data.get("eights_channels") or [])
            if game_type == "5v5":
                return list(team_data.get("fives_channels") or [])
            return []

        async def _resolve_team_channels(guild_id: Any) -> list[int]:
            try:
                gid = int(guild_id)
            except Exception:
                return []
            if MAIN_GUILD_ID and gid == int(MAIN_GUILD_ID):
                if game_type == "6v6":
                    return list(config_module.SIXES_MAIN_MATCHMAKING_CHANNELS or [])
                if game_type == "8v8":
                    return list(config_module.EIGHTS_MAIN_MATCHMAKING_CHANNELS or [])
                if game_type == "5v5":
                    return list(config_module.FIVES_MAIN_MATCHMAKING_CHANNELS or [])
                return []
            try:
                team = await self.db.teams.get_team(gid)
            except Exception:
                team = None
            return _channel_list_for_team(team)

        async def _resolve_team_channels_by_name(team_name: Any) -> list[int]:
            name_raw = str(team_name or "").strip()
            if not name_raw:
                return []

            team = None
            try:
                team = await self.db.teams.get_team_by_name(name_raw)
            except Exception:
                team = None

            if not team:
                try:
                    team = await self.db.teams.get_team_by_alias(name_raw)
                except Exception:
                    team = None

            if not team:
                try:
                    team = await self.db.teams.find_best_team_match(name_raw, threshold=0.8)
                except Exception:
                    team = None

            return _channel_list_for_team(team)

        home_channels = await _resolve_team_channels(match_row.get("home_guild_id"))
        away_channels = await _resolve_team_channels(match_row.get("away_guild_id"))
        for ch_id in home_channels[:1]:
            _dedupe_add(ch_id)
        for ch_id in away_channels[:1]:
            _dedupe_add(ch_id)

        if not channel_ids:
            home_name_channels = await _resolve_team_channels_by_name(match_row.get("home_team_name"))
            away_name_channels = await _resolve_team_channels_by_name(match_row.get("away_team_name"))
            for ch_id in home_name_channels[:1]:
                _dedupe_add(ch_id)
            for ch_id in away_name_channels[:1]:
                _dedupe_add(ch_id)

        if channel_ids:
            return channel_ids

        # Last-resort default for unmatched 6v6 mixes: main discord 6v6-b if available.
        if game_type == "6v6":
            main_sixes = list(config_module.SIXES_MAIN_MATCHMAKING_CHANNELS or [])
            if len(main_sixes) >= 2:
                return [int(main_sixes[1])]
            if main_sixes:
                return [int(main_sixes[0])]
        elif game_type == "8v8":
            main_eights = list(config_module.EIGHTS_MAIN_MATCHMAKING_CHANNELS or [])
            if main_eights:
                return [int(main_eights[0])]
        elif game_type == "5v5":
            main_fives = list(config_module.FIVES_MAIN_MATCHMAKING_CHANNELS or [])
            if main_fives:
                return [int(main_fives[0])]

        return []
    
    async def _import_player_stats(
        self,
        match_id: str,
        match_datetime: datetime,
        enhanced_players: Dict[str, Dict[str, Any]],
        home_team_name: str,
        away_team_name: str,
        home_guild_id: Optional[int],
        away_guild_id: Optional[int],
        home_score: int,
        away_score: int,
        player_event_timestamps: Optional[Dict[str, Dict[str, List[int]]]] = None,
        player_derived: Optional[Dict[str, Dict[str, Any]]] = None,
        lineup_analysis: Optional[Dict[str, Any]] = None,
        update_existing: bool = False,
    ):
        """Import player statistics for a match - stores ALL players by steam_id regardless of registration.
        
        Players are stored with their steam_id so they can be linked later when they register.
        Teams are stored by name, with guild_id being NULL if team is not registered.
        """
        players_imported = 0
        player_rows: List[Dict[str, Any]] = []
        player_derived = player_derived or {}
        lineup_players = (lineup_analysis or {}).get("players") or {}

        for steam_id, player_data in enhanced_players.items():
            try:
                # Determine which team the player played for
                teams_played = player_data.get('teamsPlayedFor', [])
                if not teams_played:
                    continue
                
                # Use the first team they played for (or 'home' if multiple)
                primary_team = teams_played[0] if len(teams_played) == 1 else 'home'
                
                # Set team info based on side
                if primary_team == 'home':
                    team_name = home_team_name
                    opponent_team_name = away_team_name
                    team_guild_id = home_guild_id
                    opponent_guild_id = away_guild_id
                else:
                    team_name = away_team_name
                    opponent_team_name = home_team_name
                    team_guild_id = away_guild_id
                    opponent_guild_id = home_guild_id
                
                # Get player's main position
                derived = player_derived.get(steam_id, {})
                lineup_meta = lineup_players.get(steam_id, {})
                position = (
                    player_data.get('mainPositionByTeam', {}).get(primary_team)
                    or derived.get("main_position")
                    or lineup_meta.get("main_position")
                    or 'Unknown'
                )
                
                # Use the 'overall' bucket (home+away combined), not just the
                # primary-team bucket -- a player who appears on both sides in
                # one match (most commonly a single keeper covering both
                # goals) would otherwise have half their match silently
                # dropped, since primary_team only ever names one side.
                stats = player_data.get('statsByTeam', {}).get('overall', {})
                position_times = player_data.get('positionSecondsByTeam', {}).get('overall', {})

                # Calculate total time played and position times (in seconds)
                time_gk = position_times.get('GK', 0)
                time_def = position_times.get('LB', 0) + position_times.get('CB', 0) + position_times.get('RB', 0)
                time_mid = position_times.get('CM', 0)
                time_att = position_times.get('LW', 0) + position_times.get('RW', 0) + position_times.get('CF', 0)
                time_played = time_gk + time_def + time_mid + time_att

                # A "single keeper" covered goal for both sides this match --
                # they're responsible for twice the exposure of a normal
                # (double-keeper) match, which the rating formula accounts for.
                gk_seconds_by_side = player_data.get('positionSecondsByTeam', {})
                is_single_keeper = (
                    gk_seconds_by_side.get('home', {}).get('GK', 0) > 0
                    and gk_seconds_by_side.get('away', {}).get('GK', 0) > 0
                )
                # The other side's goals conceded (i.e. how many my own team
                # scored) -- used to judge whether this keeper faced a
                # tougher-than-average workload relative to their opposite number.
                opponent_conceded = away_score if primary_team == 'home' else home_score
                
                # Calculate pass accuracy
                passes_attempted = stats.get('passes', 0)
                passes_completed = stats.get('passesCompleted', 0)
                pass_accuracy = (passes_completed / max(passes_attempted, 1)) * 100

                # Build row payload first so possession can be normalized across the whole match.
                event_timestamps = (player_event_timestamps or {}).get(steam_id, {})
                status_value = str(
                    derived.get("status")
                    or lineup_meta.get("status")
                    or ("started" if player_data.get("started") else "substitute")
                )
                if status_value not in {"started", "substitute", "on_bench"}:
                    status_value = "substitute"

                # Pull optional metrics from derived payload first, then stat fallbacks.
                player_rows.append(
                    {
                        "match_id": match_id,
                        "steam_id": steam_id,
                        "guild_id": team_guild_id,
                        "guild_team_name": team_name,
                        "player_name": player_data.get("name", "Unknown"),
                        "status": status_value,
                        "position": str(position).upper(),
                        "goals": stats.get('goals', 0),
                        "assists": stats.get('assists', 0),
                        "second_assists": stats.get('secondAssists', 0),
                        "shots": stats.get('shots', 0),
                        "shots_on_goal": stats.get('shotsOnGoal', 0),
                        "passes_completed": passes_completed,
                        "passes_attempted": passes_attempted,
                        "chances_created": stats.get('chancesCreated', 0),
                        "key_passes": stats.get('keyPasses', 0),
                        "interceptions": stats.get('interceptions', 0),
                        "tackles": stats.get('slidingTackles', 0),
                        "sliding_tackles_completed": stats.get('slidingTacklesCompleted', 0),
                        "fouls": stats.get('fouls', 0),
                        "yellow_cards": stats.get('yellowCards', 0),
                        "red_cards": stats.get('redCards', 0),
                        "keeper_saves": stats.get('keeperSaves', 0),
                        "keeper_saves_caught": stats.get('keeperSavesCaught', 0),
                        "goals_conceded": stats.get('goalsConceded', 0),
                        "offsides": stats.get('offsides', 0),
                        "own_goals": stats.get('ownGoals', 0),
                        "fouls_suffered": stats.get('foulsSuffered', 0),
                        "free_kicks": stats.get('freeKicks', 0),
                        "penalties": stats.get('penalties', 0),
                        "corners": stats.get('corners', 0),
                        "throw_ins": stats.get('throwIns', 0),
                        "goal_kicks": stats.get('goalKicks', 0),
                        "possession_raw": stats.get('possession', 0),
                        "time_played": time_played,
                        "time_gk": time_gk,
                        "time_def": time_def,
                        "time_mid": time_mid,
                        "time_att": time_att,
                        "distance_covered": stats.get('distanceCovered', 0),
                        "pass_accuracy": pass_accuracy,
                        "is_single_keeper": is_single_keeper,
                        "opponent_conceded": opponent_conceded,
                        "event_timestamps": event_timestamps,
                        "clutch_actions": derived.get("clutch_actions", []),
                        "sub_impact": derived.get("sub_impact", {}),
                    }
                )
                
            except Exception as e:
                logger.error(f"Error importing player stats for {steam_id}: {e}")
                continue

        existing_ids = {row.get("steam_id") for row in player_rows}
        for steam_id, meta in lineup_players.items():
            if steam_id in existing_ids:
                continue
            status_value = str(meta.get("status") or "").lower()
            if status_value != "on_bench":
                continue
            side = str(meta.get("main_team") or "").lower()
            if side not in {"home", "away"}:
                continue

            team_guild_id = home_guild_id if side == "home" else away_guild_id
            derived_row = player_derived.get(steam_id) or {}
            row = {
                "match_id": match_id,
                "steam_id": steam_id,
                "guild_id": team_guild_id,
                "guild_team_name": home_team_name if side == "home" else away_team_name,
                "player_name": meta.get("name", "Unknown"),
                "status": "on_bench",
                "position": str(meta.get("main_position") or "Unknown").upper(),
                "goals": 0,
                "assists": 0,
                "second_assists": 0,
                "shots": 0,
                "shots_on_goal": 0,
                "passes_completed": 0,
                "passes_attempted": 0,
                "chances_created": 0,
                "key_passes": 0,
                "interceptions": 0,
                "tackles": 0,
                "sliding_tackles_completed": 0,
                "fouls": 0,
                "yellow_cards": 0,
                "red_cards": 0,
                "keeper_saves": 0,
                "keeper_saves_caught": 0,
                "goals_conceded": 0,
                "offsides": 0,
                "own_goals": 0,
                "fouls_suffered": 0,
                "free_kicks": 0,
                "penalties": 0,
                "corners": 0,
                "throw_ins": 0,
                "goal_kicks": 0,
                "possession_raw": 0.0,
                "time_played": 0,
                "time_gk": 0,
                "time_def": 0,
                "time_mid": 0,
                "time_att": 0,
                "distance_covered": 0.0,
                "pass_accuracy": 0.0,
                "is_single_keeper": False,
                "opponent_conceded": 0,
                "event_timestamps": (player_event_timestamps or {}).get(steam_id, {}),
                "clutch_actions": derived_row.get("clutch_actions", []),
                "sub_impact": derived_row.get("sub_impact", {}),
            }
            player_rows.append(row)

        # Convert raw possession into match share percent per player.
        total_raw_possession = sum(
            max(0.0, float(row.get("possession_raw") or 0.0))
            for row in player_rows
        )
        for row in player_rows:
            raw_pos = max(0.0, float(row.get("possession_raw") or 0.0))
            row["possession"] = round((raw_pos / total_raw_possession) * 100.0, 2) if total_raw_possession > 0 else 0.0

        for row in player_rows:
            # Persist per-match performance for faster views/hub lookups.
            row_rating = rate_player(row)
            row["match_rating"] = round(float(row_rating), 2) if isinstance(row_rating, (int, float)) else None
            row["is_match_mvp"] = False
            row["mvp_score"] = None
            row["mvp_key_stats"] = []

        mvp_payload = get_mvp_data(player_rows)
        if isinstance(mvp_payload, dict):
            mvp_name = str(mvp_payload.get("name") or "").strip().lower()
            mvp_pos = str(mvp_payload.get("position") or "").strip().upper()
            mvp_score = mvp_payload.get("score")
            mvp_stats = mvp_payload.get("stats") if isinstance(mvp_payload.get("stats"), list) else []
            for row in player_rows:
                row_name = str(row.get("player_name") or "").strip().lower()
                row_pos = str(row.get("position") or "").strip().upper()
                if row_name == mvp_name and (not mvp_pos or row_pos == mvp_pos):
                    row["is_match_mvp"] = True
                    row["mvp_score"] = round(float(mvp_score), 2) if isinstance(mvp_score, (int, float)) else None
                    row["mvp_key_stats"] = mvp_stats[:6]
                    break

        players_imported = await self.db.matches.bulk_add_player_match_data(
            player_rows,
            update_existing=update_existing,
        )
        
        logger.info(f"Imported {players_imported} player records for match {match_id}")
        
        # Ratings are refreshed by scheduled task, not per imported match.
    
    async def import_match_from_file(
        self,
        json_path: str,
        league_name: Optional[str] = None
    ) -> Optional[int]:
        """
        Import a match from a JSON file.
        
        Args:
            json_path: Path to JSON file
            league_name: Optional league name
            
        Returns:
            Match ID if successful, None otherwise
        """
        try:
            import json
            with open(json_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            
            return await self.import_match_from_json(json_data, league_name=league_name)
            
        except Exception as e:
            logger.error(f"Error reading JSON file {json_path}: {e}")
            return None
    
    async def import_matches_from_directory(
        self,
        directory: str,
        league_name: Optional[str] = None,
        pattern: str = "*.json"
    ) -> Dict[str, Any]:
        """
        Import all matches from a directory.
        
        Args:
            directory: Directory containing JSON files
            league_name: Optional league name
            pattern: File pattern to match (default: *.json)
            
        Returns:
            Dict with import statistics
        """
        directory_path = Path(directory)
        json_files = list(directory_path.glob(pattern))
        
        stats = {
            'total_files': len(json_files),
            'imported': 0,
            'failed': 0,
            'skipped': 0,
            'match_ids': []
        }
        
        logger.info(f"Found {len(json_files)} JSON files in {directory}")
        
        for json_file in json_files:
            try:
                match_id = await self.import_match_from_file(
                    str(json_file),
                    league_name
                )
                
                if match_id:
                    stats['imported'] += 1
                    stats['match_ids'].append(match_id)
                else:
                    stats['failed'] += 1
                    
            except Exception as e:
                logger.error(f"Error processing {json_file}: {e}")
                stats['failed'] += 1
        
        logger.info(
            f"Import complete: {stats['imported']} imported, "
            f"{stats['failed']} failed, {stats['skipped']} skipped"
        )
        
        return stats


async def import_match_json(db, json_path: str, league_name: Optional[str] = None) -> Optional[int]:
    """
    Convenience function to import a single match.
    
    Args:
        db: Database instance
        json_path: Path to JSON file
        league_name: Optional league name
        
    Returns:
        Match ID if successful, None otherwise
    """
    importer = MatchImporter(db)
    return await importer.import_match_from_file(json_path, league_name)

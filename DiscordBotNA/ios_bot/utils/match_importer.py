"""
Match JSON importer for ios_bot using iosca_bot parser.
Imports match data from JSON files into PostgreSQL database.
"""

import logging
from ios_bot.config import MAIN_GUILD_ID
from typing import Optional, Dict, Any, List
from datetime import datetime
from pathlib import Path
import sys

# Import the json_parser from ios_bot
from ios_bot.utils.json_parser import parse_match_json, build_enhanced_player_data

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
        source_filename: Optional[str] = None
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
            
            # Map known Main Guild aliases before DB lookup
            main_guild_aliases = {
                "iosoccer central america a",
                "iosoccer central america b",
                "iosoccer central america",
                "iosoccer",
                "main guild 6s team",
                "iosca mix a",
                "iosca mix b",
                "greece",
                "iosca a",
                "iosca b",
                "iosca",
            }

            home_name_raw = (match_data.get('home_team') or "").strip()
            away_name_raw = (match_data.get('away_team') or "").strip()
            home_name_norm = home_name_raw.lower()
            away_name_norm = away_name_raw.lower()

            home_team = None
            away_team = None
            home_guild_id = None
            away_guild_id = None

            if MAIN_GUILD_ID and home_name_norm in main_guild_aliases:
                home_guild_id = MAIN_GUILD_ID
            if MAIN_GUILD_ID and away_name_norm in main_guild_aliases:
                away_guild_id = MAIN_GUILD_ID

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

            if not home_registered or not away_registered:
                logger.info(
                    f"Importing match with unregistered teams: "
                    f"Home={match_data['home_team']} (registered={home_registered}), "
                    f"Away={match_data['away_team']} (registered={away_registered})"
                )
            
            # Build lineups from player data
            enhanced_players = build_enhanced_player_data(json_data)
            home_lineup = []
            away_lineup = []
            
            for steam_id, player_data in enhanced_players.items():
                if 'home' in player_data['teamsPlayedFor']:
                    home_lineup.append({
                        'steam_id': steam_id,
                        'name': player_data['name'],
                        'position': player_data['mainPositionByTeam'].get('home', 'Unknown'),
                        'started': player_data['started']
                    })
                if 'away' in player_data['teamsPlayedFor']:
                    away_lineup.append({
                        'steam_id': steam_id,
                        'name': player_data['name'],
                        'position': player_data['mainPositionByTeam'].get('away', 'Unknown'),
                        'started': player_data['started']
                    })
            
            # Determine game type from player count
            total_players = len(home_lineup) + len(away_lineup)
            num_players_per_side = match_data['game_format']
            full_game_type = f"{num_players_per_side}v{num_players_per_side}"
            
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
                substitutions=match_data.get('substitutions', []),
                home_lineup=home_lineup,
                away_lineup=away_lineup,
                match_id_str=match_id_str,
                source_filename=source_filename,
                game_type=full_game_type
            )
            
            if not match_id:
                logger.error("Failed to add match to database")
                return None
            
            logger.info(f"✅ Match imported: {match_data['home_team']} {match_data['home_score']}-{match_data['away_score']} {match_data['away_team']}")
            
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
                away_score=match_data['away_score']
            )
            
            return match_id
            
        except Exception as e:
            logger.error(f"Error importing match from JSON: {e}", exc_info=True)
            return None
    
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
        away_score: int
    ):
        """Import player statistics for a match - stores ALL players by steam_id regardless of registration.
        
        Players are stored with their steam_id so they can be linked later when they register.
        Teams are stored by name, with guild_id being NULL if team is not registered.
        """
        players_imported = 0
        
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
                position = player_data.get('mainPositionByTeam', {}).get(primary_team, 'Unknown')
                
                # Get stats for the team they played for
                stats = player_data.get('statsByTeam', {}).get(primary_team, {})
                position_times = player_data.get('positionSecondsByTeam', {}).get(primary_team, {})
                
                # Calculate total time played and position times (in seconds)
                time_gk = position_times.get('GK', 0)
                time_def = position_times.get('LB', 0) + position_times.get('CB', 0) + position_times.get('RB', 0)
                time_mid = position_times.get('CM', 0)
                time_att = position_times.get('LW', 0) + position_times.get('RW', 0) + position_times.get('CF', 0)
                time_played = time_gk + time_def + time_mid + time_att
                
                # Calculate pass accuracy
                passes_attempted = stats.get('passes', 0)
                passes_completed = stats.get('passesCompleted', 0)
                pass_accuracy = (passes_completed / max(passes_attempted, 1)) * 100

                # Add player match data - stores by steam_id, guild_id can be NULL
                await self.db.matches.add_player_match_data(
                    match_id=match_id,
                    steam_id=steam_id,
                    guild_id=team_guild_id,
                    position=position,
                    goals=stats.get('goals', 0),
                    assists=stats.get('assists', 0),
                    second_assists=stats.get('secondAssists', 0),
                    shots=stats.get('shots', 0),
                    shots_on_goal=stats.get('shotsOnGoal', 0),
                    passes_completed=passes_completed,
                    passes_attempted=passes_attempted,
                    chances_created=stats.get('chancesCreated', 0),
                    key_passes=stats.get('keyPasses', 0),
                    interceptions=stats.get('interceptions', 0),
                    tackles=stats.get('slidingTackles', 0),
                    sliding_tackles_completed=stats.get('slidingTacklesCompleted', 0),
                    fouls=stats.get('fouls', 0),
                    yellow_cards=stats.get('yellowCards', 0),
                    red_cards=stats.get('redCards', 0),
                    keeper_saves=stats.get('keeperSaves', 0),
                    keeper_saves_caught=stats.get('keeperSavesCaught', 0),
                    goals_conceded=stats.get('goalsConceded', 0),
                    offsides=stats.get('offsides', 0),
                    own_goals=stats.get('ownGoals', 0),
                    fouls_suffered=stats.get('foulsSuffered', 0),
                    free_kicks=stats.get('freeKicks', 0),
                    penalties=stats.get('penalties', 0),
                    corners=stats.get('corners', 0),
                    throw_ins=stats.get('throwIns', 0),
                    goal_kicks=stats.get('goalKicks', 0),
                    possession=stats.get('possession', 0),
                    time_played=time_played,
                    time_gk=time_gk,
                    time_def=time_def,
                    time_mid=time_mid,
                    time_att=time_att,
                    distance_covered=stats.get('distanceCovered', 0),
                    pass_accuracy=pass_accuracy
                )
                
                players_imported += 1
                
            except Exception as e:
                logger.error(f"Error importing player stats for {steam_id}: {e}")
                continue
        
        logger.info(f"Imported {players_imported} player records for match {match_id}")
        
        # Update player ratings incrementally after match import
        try:
            from ios_bot.ratings.Rating_Generator.generate_ratings import generate_player_ratings
            logger.info("Updating player ratings after match import...")
            await generate_player_ratings()
            logger.info("Player ratings updated successfully")
        except Exception as e:
            logger.error(f"Error updating player ratings: {e}")
            # Don't fail the entire import if rating calculation fails
    
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
            
            return await self.import_match_from_json(json_data, league_name)
            
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

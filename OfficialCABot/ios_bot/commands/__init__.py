from .clear import clear
from .help import help
from .translate_english import translate_english
from .translate_spanish import translate_spanish
from .lineup import lineup
from .batch_register import batch_register
from .sign import sign_slash
from .unsign import unsign_slash
from .ready import ready_slash, ready_tournament_match_slash
from . import utils  # Import utils module for shared functions
from .sub import sub
from .here import here
from .team_registration import register_team
from .team_players import register_players, remove_player
from .team_management import delete_team_command, reactivate_team_command
from .team_view import view_teams_command
from .challenge import challenge_command # Added challenge
from .unchallenge import unchallenge_command # Added unchallenge
from .edit_team import edit_team_channels_command
from .server_status import server_status
from .request_sub import request_sub
from .view_player import view_player
from .register_me import register_me
from .view_match import view_match
from .server_management import edit_servers
from .check_players import check_players
from .populate_team_stats import backfill_match_stats
from .recalculate_all import recalculate_all
from .reevaluate_all_games import reevaluate_all_games
from .rebuild_match_data import rebuild_match_data_from_json
from .sync_sftp_matches import sync_sftp_matches
from .tournaments import create_tournament, view_tournament
from .server_assets import server_assets_command
from .stats_moderation import exclude_match_from_stats, exclude_player_from_match_stats, merge_player
from .set_position import set_position
from .set_player_tier import set_player_tier

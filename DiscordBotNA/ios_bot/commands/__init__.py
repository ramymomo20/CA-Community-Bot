from .clear import clear
from .help import help
from .translate_english import translate_english
from .translate_spanish import translate_spanish
from .lineup import lineup
from .batch_register import batch_register
from .sign import sign_slash
from .unsign import unsign_slash
from .ready import ready_slash
from . import utils  # Import utils module for shared functions
from .sub import sub
from .here import here
from .team_registration import register_team
from .team_players import register_players, remove_player
from .team_management import delete_team_command
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
from .populate_team_stats import populate_team_stats
from .recalculate_all import recalculate_all
from .reevaluate_all_games import reevaluate_all_games
from .sync_sftp_matches import sync_sftp_matches
from .tournaments import create_tournament, view_tournament

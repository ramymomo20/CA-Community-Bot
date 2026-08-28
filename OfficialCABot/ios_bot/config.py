import discord
from discord.ext import commands, tasks
from discord.ui import View, Select, Button, Modal, InputText
from discord import Option, SelectOption, Embed, ButtonStyle, ApplicationContext, Interaction, Member, TextChannel
import random, time, asyncio, datetime, requests, re, json, csv, os, pytz
from datetime import datetime, timezone, timedelta, time
import pandas as pd
from rcon.source import Client
from requests.exceptions import RequestException
# MySQL imports removed - now using PostgreSQL via asyncpg
# deep-translator's GoogleTranslator wraps the same free Google Translate
# endpoint as googletrans, but is actively maintained and doesn't carry
# googletrans's known asyncio event-loop conflicts. googletrans was also
# never a declared dependency (missing from requirements.txt) despite being
# imported at module load time here -- a missing package would crash the
# entire bot on startup, not just the translate commands.
from deep_translator import GoogleTranslator

# Load environment variables from .env file if it exists
def load_env_file():
    """Load environment variables from .env file if it exists"""
    env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    if os.path.exists(env_file):
        print(f"Loading environment variables from {env_file}")
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    # Remove quotes if present
                    value = value.strip('"\'')
                    os.environ[key] = value
        print("Environment variables loaded from .env file")
    else:
        print("No .env file found, using system environment variables")

# Load .env file before accessing environment variables
load_env_file()


def _optional_int_env(name: str):
    value = str(os.getenv(name, "")).strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        print(f"Invalid integer environment variable for {name}: {value}")
        return None

# Bot setup with required permissions
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True  # Required for guild join events
bot = discord.Bot(intents=intents)

# Guild and Channel Configuration
# These will be loaded from database during bot initialization
MAIN_GUILD_ID = None  # Will be set from database
FIXTURES_CHANNEL_ID = None  # Will be set from database
RESULTS_CHANNEL_ID = None  # Will be set from database
ADMIN_ROLE_ID = None  # Will be set from database
TEAM_LEADER_ID = None  # Will be set from database
CONFIRMED_SCHEDULE_CHANNEL_ID = None  # Will be set from database
CAPTAINS_CHANNEL_ID = None  # Will be set from database
MAIN_CHALLENGE_ANNOUNCEMENT_CHANNEL_ID = _optional_int_env("MAIN_CHALLENGE_ANNOUNCEMENT_CHANNEL_ID")
OTHER_CHALLENGE_ANNOUNCEMENT_CHANNEL_ID = _optional_int_env("OTHER_CHALLENGE_ANNOUNCEMENT_CHANNEL_ID")

def get_invite_link():
    """Generate the bot's invite link"""
    # bot.user.id and the Discord application's CLIENT_ID are the same value
    # for a bot account; CLIENT_ID is the .env fallback for when this is
    # called before login completes. Previously fell back to an undefined
    # `BOT_ID` name -- a guaranteed NameError if bot.user was ever falsy here.
    client_id = bot.user.id if bot.user else _optional_int_env("CLIENT_ID")
    return f"https://discord.com/api/oauth2/authorize?client_id={client_id}&permissions={8}&scope=bot%20applications.commands"

# Constants
FIVES_MAIN_MATCHMAKING_CHANNELS = []
SIXES_MAIN_MATCHMAKING_CHANNELS = []
EIGHTS_MAIN_MATCHMAKING_CHANNELS = []
FIVES_POSITIONS = ["GK", "CB", "LM", "RM", "CF"]  # Make sure these are uppercase
SIXES_POSITIONS = ["GK", "LB", "RB", "CM", "LW", "RW"]  # Make sure these are uppercase
EIGHTS_POSITIONS = ["GK", "LB", "CB","RB", "CM", "LW", "CF", "RW"]  # Make sure these are uppercase
FIVES_PLAYERS_NEEDED = 10  # Number of players needed for a full match
SIXES_PLAYERS_NEEDED = 12  # Number of players needed for a full match
EIGHTS_PLAYERS_NEEDED = 16  # Number of players needed for a full match

# Main Matchmaking Channels
EIGHTS_CHANNEL_REGEX_PATTERN = r"8v8"
SIXES_CHANNEL_REGEX_PATTERN = r"6v6"
FIVES_CHANNEL_REGEX_PATTERN = r"5v5"

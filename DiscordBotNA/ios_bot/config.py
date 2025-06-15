import discord
from discord.ext import commands, tasks
from discord.ui import View, Select, Button, Modal, InputText
from discord import Option, SelectOption, Embed, ButtonStyle, ApplicationContext, Interaction, Member, TextChannel
import random, time, asyncio, datetime, requests, re, json, csv, os, pytz
from datetime import time, datetime, timezone, timedelta
import pandas as pd
from rcon.source import Client
from requests.exceptions import RequestException
import mysql.connector
from mysql.connector import Error
from googletrans import Translator

# Bot setup with required permissions
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True  # Required for guild join events

bot = discord.Bot(intents=intents)

# Bot configuration
BOT_ID = ""  # Replace this with your bot's client ID
# BOT_ADMIN_IDS = [] # Add your Discord User ID here, e.g., [123456789012345678] # Commented out as per new requirement
MAIN_GUILD_ID = 6 # Your main Discord server ID
ADMIN_ROLE_ID = 1 # Role ID in MAIN_GUILD_ID that grants admin delete privileges for teams
MY_PERM = 1

# Required permissions for the bot
REQUIRED_PERMISSIONS = discord.Permissions(
    manage_messages=True,
    send_messages=True,
    read_messages=True,
    embed_links=True,
    attach_files=True,
    read_message_history=True,
    mention_everyone=True,
    add_reactions=True,
    use_external_emojis=True,
    manage_roles=True
)

def get_invite_link():
    """Generate the bot's invite link"""
    client_id = bot.user.id if bot.user else BOT_ID
    return f"https://discord.com/api/oauth2/authorize?client_id={client_id}&permissions={8}&scope=bot%20applications.commands"


link = 'https://docs.google.com/spreadsheets/d/1DInBbtsCXE3kBJR2CtLSmdDsE9EP7n1nvI0GqYJ2exY/edit?usp=sharing'
token = ''

# Database Info
host=""
port=3306
user=""
password=""
database=""
charset='utf8mb4'
collation='utf8mb4_general_ci'

# Constants
SIXES_MAIN_MATCHMAKING_CHANNELS = []
EIGHTS_MAIN_MATCHMAKING_CHANNELS = []
SIXES_POSITIONS = ["GK", "LB", "RB", "CM", "LW", "RW"]  # Make sure these are uppercase
EIGHTS_POSITIONS = ["GK", "LB", "CB","RB", "CM", "LW", "CF", "RW"]  # Make sure these are uppercase
SIXES_PLAYERS_NEEDED = 12  # Number of players needed for a full match
EIGHTS_PLAYERS_NEEDED = 16  # Number of players needed for a full match

TITLE = "🤖 `CA Community Bot`"
DESCRIPTION = "I manage the IOSCA community and I work the ratings system."
HOW_TO_USE = "🤔 How to use me?"
COMMANDS = "⌨️ Available commands"
ADD = "🤝 Copyright (c) 2025 **CA Community Bot**\n*THIS SOFTWARE IS PROVIDED WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED.*" 
FOOTER_TEXT = "If you require further assistance, directly message: @shaq#6096"
FOOTER_URL = "https://imgur.com/ylgPvo4.jpeg"

USE_MESSAGE = ""
USE_MESSAGE += f"**1**. Get a feel for the bot by using the different commands. This bot uses your 18-digit unique discord id to tie your account with your rating.\n"
USE_MESSAGE += f"**2**. If for any case the bot doesn't work for you, even if you have a rating. I'll have to update your discord id.\n"
USE_MESSAGE += f"**3**. This bot is still a WIP, but we hope to constantly update this to include new commands and added functionality.\n"
USE_MESSAGE += f"**4**. Let me know if there are any bugs or new features you would like to see changed/added!\n"
USE_MESSAGE += f"**5**. Enjoy! Or not.\n"
USE_MESSAGE += "\u200b"

ADD_MESSAGE = ""
ADD_MESSAGE += ""
ADD_MESSAGE += "\u200b"

#* Success messages ---------------------------------------------------------------------------------------------
DELETED_MSG = "Successfully deleted the messages."
SENT_DM = "DM has been sent!"

# Input errors
YOUR_ACCOUNT_NOT_FOUND = "We do not have your account in your system. Please contact shaq to fix this."
ACCOUNT_NOT_FOUND = "That account is not in the system. Please contact shaq to fix this."
INVALID_PERMISSIONS = "You do not have the permission to do that."
NON_EXISTENT = "That account is invalid."

# --- Guild & Channel IDs ---
GUILD_ID = 1 # Main Guild ID (IOSoccer Central America)
MAIN_GUILD_ID = 1 # Explicitly named for clarity

# --- RCON Server Configuration ---
# This list can be dynamically appended to by the /edit_main_server command.
# Changes made by the command are for the current session only and will be lost on restart.
RCON_SERVERS = [
    {"name": "Florida", "address": "199.127.63", "password": ""},
    {"name": "Georgia", "address": "199.127.62", "password": ""},
    {"name": "Snowy's Georgia", "address": "199.127.62", "password": ""}
]

# Main Matchmaking Channels
EIGHTS_CHANNEL_REGEX_PATTERN = r"8v8"
SIXES_CHANNEL_REGEX_PATTERN = r"6v6"

# Channel for announcing started Team vs Team or Team vs Main Guild challenges
# also to announce when teams are created, deleted, or when bot is invited.
MAIN_CHALLENGE_ANNOUNCEMENT_CHANNEL_ID = 1
OTHER_CHALLENGE_ANNOUNCEMENT_CHANNEL_ID = 1

FIXTURES_CHANNEL_ID = 1

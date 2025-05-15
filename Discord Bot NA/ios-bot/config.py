import discord
from discord.ext import commands
from discord import Option
import random

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = discord.Bot(intents=intents)

link = 'https://docs.google.com/spreadsheets/d/1DInBbtsCXE3kBJR2CtLSmdDsE9EP7n1nvI0GqYJ2exY/edit?usp=sharing'

TITLE = "🤖 `NA Community Bot`"
DESCRIPTION = "I manage the IOSNA community and I work the ratings system."
HOW_TO_USE = "🤔 How to use me?"
COMMANDS = "⌨️ Available commands"
ADD = "🤝 Copyright (c) 2023 **NA Community Bot**\n*THIS SOFTWARE IS PROVIDED WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED.*" 
FOOTER_TEXT = "If you require further assistance, directly message: @shaq#6096"
FOOTER_URL = "https://imgur.com/ylgPvo4.jpeg"

COMMANDS_MESSAGE = ""
COMMANDS_MESSAGE += f"**1**. `/prompt [question]`, ask the bot anything and you shall receive a GPT 3.5 answer.\n"
COMMANDS_MESSAGE += f"**2**. `/8_ball [question]`, ask the bot anything too and you shall receive an 8-ball like answer.\n"
COMMANDS_MESSAGE += f"**3**. `/joke`, (AT YOUR OWN RISK) the bot will give you a joke.\n"
COMMANDS_MESSAGE += f"**4**. `/my_rating` to view your rating. However, if you do not have one, or if you created a new discord account, this will not work.\n"
COMMANDS_MESSAGE += f"**5**. `/view_rating @name` to view someone else's rating.\n"
COMMANDS_MESSAGE += f"**6**. `/top_na` to view the top 10 best players in NA.\n"
COMMANDS_MESSAGE += f"**7**. `/ratings (GK, DEF, MID, ATK)` to view the ratings by position using the list of positions in the parentheses.\n"
COMMANDS_MESSAGE += f"**8**. `/motm [url]`, receive the MOTM of the match given the url.\n"
COMMANDS_MESSAGE += f"**9**. `/view_league_table`, click on a country and you will receive their league table.\n"
COMMANDS_MESSAGE += f"**10**. `/view_top_scorers`, click on a country and you will receive their top scorers.\n"
COMMANDS_MESSAGE += f"**11**. `/view_tournament`, Choose a tournament to see past winners and countries hosted.\n"
COMMANDS_MESSAGE += f""
COMMANDS_MESSAGE += "\u200b"


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

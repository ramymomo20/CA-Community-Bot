import discord

from .commands import *
from .config import token, bot

@bot.event
async def on_ready():
    print("================ Successful login")
    await bot.change_presence(status=discord.Status.online, activity=discord.Activity(type=discord.ActivityType.watching, name= "Your Performances..."))

bot.run(token)
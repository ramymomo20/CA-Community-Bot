import discord
from discord.ext import commands
from discord import Option
from ..config import *

@bot.slash_command(name = 'get_id',description="Get the ID")
@commands.has_role("🎗️ Community Helper")
async def get_id(ctx, user: Option(discord.Member, "Enter an @", required = True)):
    await ctx.respond(user.id, ephemeral=True)

@get_id.error
async def get_id_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.respond(NON_EXISTENT, ephemeral=True)
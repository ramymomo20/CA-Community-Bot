from ios_bot.config import *

@bot.slash_command(name='clear',description='Clear a # of Messages')
@commands.has_permissions(manage_messages=True)    # for the user
@commands.bot_has_permissions(manage_messages=True)  # for the bot

async def clear(ctx, num: int):
    await ctx.channel.purge(limit=num)
    await ctx.respond(DELETED_MSG, ephemeral=True)

@clear.error
async def clear_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.respond(INVALID_PERMISSIONS, ephemeral=True)
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.respond("I don't have permission to delete messages.", ephemeral=True)

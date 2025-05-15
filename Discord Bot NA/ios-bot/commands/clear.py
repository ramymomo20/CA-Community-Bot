from ..config import *

@bot.slash_command(name = 'clear', description = "Clear a # of Messages")
@commands.has_permissions(manage_messages=True)
async def clear(ctx, num: int):
    await ctx.channel.purge(limit = num)
    await ctx.respond(DELETED_MSG, ephemeral=True)

@clear.error
async def clear_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.respond(INVALID_PERMISSIONS, ephemeral=True)

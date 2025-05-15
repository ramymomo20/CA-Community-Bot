import discord
from ..config import *

@bot.slash_command(name='help', description='Get some help.')
async def help(ctx):

        help_embed = discord.Embed(
            title=TITLE,
            description=DESCRIPTION,
            type="rich",
            color=discord.Color.orange()
        )

        help_embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url)

        help_embed.add_field(
            name=HOW_TO_USE,
            value=USE_MESSAGE
        )

        help_embed.add_field(
            name=COMMANDS,
            value=COMMANDS_MESSAGE,
            inline=False
        )

        help_embed.add_field(
            name=ADD,
            value=ADD_MESSAGE,
            inline=False
        )

        help_embed.set_footer(
            text=FOOTER_TEXT,
            icon_url=FOOTER_URL
        )
        await ctx.author.send(embed=help_embed)
        await ctx.respond(SENT_DM, ephemeral=True)
from ..config import *
from player_ratings.bot_logic import *

@bot.slash_command(name = 'top_na',description="View the 10 Best Players in NA")
async def top_na(ctx):
        best_player = highest(data1).highestnames()[0].replace('1.',"")
        rates = '\n'.join(map(str, highest(data1).highestratings()))
        players = '\n'.join(map(str, highest(data1).highestnames()))
        positions = '\n'.join(map(str, highest(data1).highestpos()))

        embed=discord.Embed(title="Top 10 NA Players", url=link, description="\n **------------------------** \n", color=discord.Color.og_blurple())
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar.url)
        embed.set_thumbnail(url="https://imgur.com/ylgPvo4.jpeg")
        embed.add_field(name="Player", value=f'`{players}`', inline=True)
        embed.add_field(name="Position", value=f'`{positions}`', inline=True)
        embed.add_field(name="Rating", value=f'`{rates}`', inline=True)
        embed.add_field(name=f"The Very Best in NA:`{best_player}`", value='**------------------------**', inline=False)
        embed.set_footer(text=f"Requested by {ctx.author.name}")
        await ctx.respond(embed=embed)
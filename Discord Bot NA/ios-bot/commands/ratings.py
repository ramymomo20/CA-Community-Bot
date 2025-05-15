import discord
from player_ratings.bot_logic import *
from discord import Option
from ..config import *

@bot.slash_command(name = 'my_rating',description="View Your Rating")
async def my_rating(ctx):

        userid = ctx.author.id
        userid = str(userid)

        name1 = list(data.Name)
        pos = list(data.Position)
        rate = list(data.Rating)
        ids = list(data.ID)
        improved = list(data.Improved)
        x = [str(i) for i in ids]


        dict1 = {x[a]:(pos[a],rate[a],name1[a],improved[a]) for a in range(len(name1))}

        if userid not in dict1:
            await ctx.respond(YOUR_ACCOUNT_NOT_FOUND,ephemeral=True)
        else:
            col = discord.Color.greyple()

            if dict1[userid][3] == 100:
                tex = 'You are a new addition!'
            else:
                if dict1[userid][3] < 0:
                    num = int(dict1[userid][3])
                    tex = f"```diff\n-{num*-1} from last rating.```"
                if dict1[userid][3] == 0:
                    tex = "```fix\n0 from last rating.```"
                if dict1[userid][3] > 0:
                    num = int(dict1[userid][3])
                    tex = f"```diff\n+{num} from last rating.```"


            if dict1[userid][1] >= 90:
                col = discord.Color.nitro_pink()
            if dict1[userid][1] in range(82,89):
                col = discord.Color.gold()
            if dict1[userid][1] in range(74,81):
                col = discord.Color.dark_gold()
            if dict1[userid][1] in range(69, 73):
                col = discord.Color.light_grey()
            if dict1[userid][1] <= 68:
                col = discord.Color.dark_red()

            embed=discord.Embed(title="IOS NA Ratings", url=link, description="\n **------------------------** \n", color=col)
            embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar.url)
            embed.set_thumbnail(url="https://imgur.com/ylgPvo4.jpeg")
            embed.add_field(name="Player", value=f'`{dict1[userid][2]}`', inline=True)
            embed.add_field(name="Position", value=f'`{dict1[userid][0]}`', inline=True)
            embed.add_field(name="Rating", value=f'`{dict1[userid][1]}`', inline=True)
            embed.add_field(name="Change", value=tex,inline=False)
            embed.set_footer(text=f"Requested by {ctx.author.name}")
            await ctx.respond(embed=embed)
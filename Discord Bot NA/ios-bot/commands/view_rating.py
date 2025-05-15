from discord import Option
from discord.ext import commands
from discord.ui import Button, View

import asyncio
from ..config import *
from player_ratings.bot_logic import *

@bot.slash_command(name = 'view_rating',description="View Someone's Rating")
async def view_rating(ctx, user: Option(discord.Member, "Enter an @", required = True)):
        userid = str(user.id)
        member = await bot.fetch_user(user.id)

        name1 = list(data.Name)
        pos = list(data.Position)
        rate = list(data.Rating)
        ids = list(data.ID)
        improved = list(data.Improved)
        x = [str(i) for i in ids]

        dict1 = {x[a]:(pos[a],rate[a],name1[a], improved[a]) for a in range(len(name1))}

        if userid not in dict1:
            embed = discord.Embed(
            	title="Rating Not Found!",
            	description="Currently this user has no rating.",
                color=discord.Color.red())
            embed.set_author(name=member.name, icon_url=member.display_avatar.url)
            embed.set_thumbnail(url="https://imgur.com/ylgPvo4.jpeg")
            embed.set_footer(text=f"Requested by {ctx.author.name}")
            await ctx.respond(embed=embed, ephemeral=True)
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
            embed.set_author(name=dict1[userid][2], icon_url=member.display_avatar.url)
            embed.set_thumbnail(url="https://imgur.com/ylgPvo4.jpeg")
            embed.add_field(name="Player", value=f'`{dict1[userid][2]}`', inline=True)
            embed.add_field(name="Position", value=f'`{dict1[userid][0]}`', inline=True)
            embed.add_field(name="Rating", value=f'`{dict1[userid][1]}`', inline=True)
            embed.add_field(name="Change", value=tex,inline=False)
            embed.set_footer(text=f"Requested by {ctx.author.name}")
            await ctx.respond(embed=embed)
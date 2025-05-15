from ..config import *
import asyncio, sys
from jokeapi import Jokes

@bot.slash_command(name = 'joke',description="Ask the Bot for a joke.")
async def joke(ctx):
	j = await Jokes()
	joke = await j.get_joke()
	response = joke["joke"] if joke["type"] == "single" else f'{joke["setup"]}\n{joke["delivery"]}'

	if sys.platform.startswith('win'):
		asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
	embed = discord.Embed(title='Receive a Joke',color=discord.Color.random())
	embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url)
	embed.set_thumbnail(url="https://imgur.com/ylgPvo4.jpeg")
	embed.add_field(name='Response: ',value=f'{response}', inline=False)
	embed.set_footer(text=f"Requested by {ctx.author.name}")
	await ctx.respond(embed=embed)
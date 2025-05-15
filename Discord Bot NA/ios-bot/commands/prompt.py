from ..config import *
from chatgpt.app import askgpt

@bot.slash_command(name = 'prompt',description="Ask ChatGPT Whatever.")
async def prompt(ctx, question, interaction):
    if question == '':
        return ctx.respond('You input nothing...', ephemeral=True)
    embed = discord.Embed(title='Response',color=discord.Color.random())
    embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url)
    embed.set_thumbnail(url="https://imgur.com/ylgPvo4.jpeg")
    embed.add_field(name='The Bot Says: ',value=f'{askgpt(question)}', inline=False)
    embed.set_footer(text=f"Requested by {ctx.author.name}")
    await interaction.followup.send(embed=embed)
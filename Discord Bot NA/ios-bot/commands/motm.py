from ..config import *
import requests

@bot.slash_command(name = 'motm',description="Find the Man of the Match of last game.")
async def motm(ctx, url):
    headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/113.0',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'Origin': 'https://www.iosoccer.com',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Referer': 'https://www.iosoccer.com/',
    'Cookie': '.AspNetCore.Cookies=CfDJ8Gt4SK4My8FEoo11MDVR7Vtre81xowZNjFGonP0ACD68Ejmlz7-VCO04Am5A8PUKUBM6z0x6xdll3Ic2F75-dWiwYvCEJtV2762cgxOJNAv075rNmgjwfr8vmBBVx8-lerEQ9AKN8GuEpeWVdaLvt4Eg2C_gl38-rrZZKlK0wWvtXx0O1Xd1erUfCha_fRqY709LPNPao8QCpQT21vsMNrJEBW_-KtzyhLvDQ4GXWQTLXhfKb-PM6FCYC8DOCF9TCNfKB-1RxQAhSrbb-owvCzp-REdAheGdnmOltLddWpKgjFxbZr4WSY9XTN1elwCDmryvxdB7LlvyT8Va0yMPx3Vs81L6816ECVn4CzlI__W4FCgswxbPdXSKFNbhAJRQxDSw0w41FXvO6Eo_DTQd3gykoDewTeMWChBfTf3M1gf4JUsaAwD8QEsIkbsefg3lRkvgW4wF2r9txjNhpm72Cpi9ew7ADDvgnmucvIVastdUsCYHT40iZKZ-b0DSigL1FPZ1rw2LnzQWNd1Qk9YkUvq3jtfSrMFwiCr-jJy7Rpc3',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-site',
    'Sec-GPC': '1',
    'TE': 'trailers'
    }

    match_id = url.split("/")[-1]
    url = f'https://iosoccer.com:44380/api/match/{match_id}/player-of-the-match'

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        json_data = response.json()
    else:
        await ctx.respond(f'Request failed with status code: {response.status_code}')
        
    embed = discord.Embed(title="Last Game's Statistics", color=discord.Color.random())
    embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url)
    embed.set_thumbnail(url="https://imgur.com/ylgPvo4.jpeg")
    embed.add_field(name='Man of the Match:', value=f"{json_data['playerName']}", inline=True)
    if int(json_data['keeperSaves']) == 0:
        embed.add_field(name='Goals:', value=f"{json_data['goals']}", inline=False)
        embed.add_field(name='Assists:', value=f"{json_data['assists']}", inline=False)
    else:
        embed.add_field(name='Saves:', value=f"{json_data['keeperSaves']}", inline=False)
        embed.add_field(name='Conceded:', value=f"{json_data['goalsConceded']}", inline=False)
    embed.set_footer(text=f"Provided by {ctx.guild.name}")
    await ctx.respond(embed=embed)
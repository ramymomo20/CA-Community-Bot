from ios_bot.config import *

@bot.slash_command(name="help", description="View all available commands")
async def help(ctx):
    embed = discord.Embed(title=TITLE, description=DESCRIPTION, color=0x2F3136)
    embed.add_field(name=HOW_TO_USE, value=USE_MESSAGE, inline=False)
    
    # Split commands into multiple fields
    commands_part1 = (
        "**1**. `/sign [team] [position] @name` to sign up for a position on a team.\n"
        "**2**. `/unsign [@name]` to remove someone from their position.\n"
        "**3**. `/ready` to start a match when teams are ready.\n"
        "**4**. `/sub` to substitute a player during a match.\n"
    )
    
    commands_part2 = (
        "**5**. `/motm [url]` to vote for Man of the Match.\n"
        "**6**. `/review_match` to submit match results and ratings.\n"
        "**7**. `/lineup` to view the current match lineup.\n"
        "**8**. `/clear` to clear messages in a channel.\n"
        "**9**. `/help` to view this message again.\n"
        "**10**. `/invite` to get the bot's invite link.\n"
        "**11**. `/translate_english text` to translate text to English.\n"
    )
    
    commands_part3 = (
        "**12**. `/translate_spanish text` to translate text to Spanish.\n"
        "**13**. `/search_team` to search for a team.\n"
        "**14**. `/get_id @name` to get a user's Discord ID.\n"
    )
    
    embed.add_field(name="⌨️ Available Commands (1/3)", value=commands_part1, inline=False)
    embed.add_field(name="⌨️ Available Commands (2/3)", value=commands_part2, inline=False)
    embed.add_field(name="⌨️ Available Commands (3/3)", value=commands_part3, inline=False)
    embed.add_field(name=ADD, value=ADD_MESSAGE, inline=False)
    embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_URL)
    await ctx.respond(embed=embed, ephemeral=True)

@bot.slash_command(name="invite", description="Get the bot's invite link")
async def invite(ctx):
    REQUIRED_ROLE_ID = 1135332947747164272
    main_guild = bot.get_guild(MAIN_GUILD_ID)

    if not main_guild:
        await ctx.respond("❌ Could not verify your role in the main guild. Please try again later.", ephemeral=True)
        return
    
    member = main_guild.get_member(ctx.author.id)

    if not member or not any(role.id == REQUIRED_ROLE_ID for role in member.roles):
        await ctx.respond("❌ Only authorized users in the main Discord can invite this bot to other servers.", ephemeral=True)
        return


    # Create button view
    view = discord.ui.View()
    # Add invite button with dynamic invite link
    view.add_item(
        discord.ui.Button(
            label="Add to Server",
            url=get_invite_link(),
            style=discord.ButtonStyle.url
        )
    )
    
    embed = discord.Embed(
        title="🤖 Invite IOSCA Community Bot",
        description="Click the button below to add the bot to your server.\n\n**Requirements:**\n• You must have 'Manage Server' permission in the server\n• The server must be a gaming community",
        color=0x2F3136
    )
    embed.set_footer(text="For support, contact: @shaq#6096")
    await ctx.respond(embed=embed, view=view, ephemeral=True)
from ios_bot.config import *
from ios_bot.announcements import announce_team_created

def auto_detect_matchmaking_channels(guild: discord.Guild):
    """Auto-detect 5v5/6v6/8v8 channels by regex patterns."""
    fives = []
    sixes = []
    eights = []

    try:
        fives_re = re.compile(FIVES_CHANNEL_REGEX_PATTERN, re.IGNORECASE)
        sixes_re = re.compile(SIXES_CHANNEL_REGEX_PATTERN, re.IGNORECASE)
        eights_re = re.compile(EIGHTS_CHANNEL_REGEX_PATTERN, re.IGNORECASE)
    except re.error:
        # Fallback to simple patterns if regex invalid
        fives_re = re.compile(r"5v5", re.IGNORECASE)
        sixes_re = re.compile(r"6v6", re.IGNORECASE)
        eights_re = re.compile(r"8v8", re.IGNORECASE)

    for ch in guild.text_channels:
        if not ch.permissions_for(guild.me).send_messages:
            continue
        if fives_re.search(ch.name):
            fives.append(ch.id)
        if sixes_re.search(ch.name):
            sixes.append(ch.id)
        if eights_re.search(ch.name):
            eights.append(ch.id)

    return fives, sixes, eights

class ChannelSelect(Select):
    def __init__(self, channels: list[TextChannel], channel_type: str, max_selectable: int = 10):
        options = [SelectOption(label=channel.name, value=str(channel.id)) for channel in channels]
        if not options:
            options.append(SelectOption(label=f"No text channels found for {channel_type}", value="no_channels"))
        super().__init__(
            placeholder=f"Select {channel_type} matchmaking channel(s)... (Optional)", 
            min_values=0, 
            max_values=min(len(options), max_selectable) if options[0].value != "no_channels" else 1, 
            options=options
        )
        self.channel_type = channel_type

    async def callback(self, interaction: discord.Interaction):
        selected_ids = [int(val) for val in self.values if val != "no_channels"]
        if self.channel_type == "8v8":
            self.view.eights_channels_selected = selected_ids
        elif self.channel_type == "6v6":
            self.view.sixes_channels_selected = selected_ids
        elif self.channel_type == "5v5":
            self.view.fives_channels_selected = selected_ids
        self.disabled = True # Disable after selection
        # Check if all selections are done and then proceed
        await interaction.response.edit_message(view=self.view) 
        # We need a way to submit the whole form, perhaps a button in the view

@bot.slash_command(
    name="register_team",
    description="Register your server as an IOSCA team and set up matchmaking channels."
)
@commands.has_permissions(manage_guild=True)
async def register_team(
    ctx,
    captain: Option(discord.Member, "Optional: set a different captain for the team", required=False) = None,
    team_type: Option(str, "Team type: club, national, or mix", required=False) = None,
    vice_captain: Option(discord.Member, "Optional: set a vice-captain for the team", required=False) = None,
):
    guild = ctx.guild
    if not guild:
        await ctx.respond("This command can only be used in a server.", ephemeral=True)
        return

    # Check if team already registered (a soft-deleted/inactive team for
    # this guild is allowed through -- add_team reactivates it).
    existing_team = await bot.db.teams.get_team(guild.id)
    if existing_team and existing_team.get("is_active", True):
        await ctx.respond(f"This server ('{guild.name}') is already registered as a team.", ephemeral=True)
        return

    similar_team = await bot.db.teams.find_best_team_match(guild.name, threshold=0.85)
    if similar_team and similar_team.get("guild_id") != guild.id:
        await ctx.respond(
            f"⚠️ '{guild.name}' looks very similar to the already-registered team "
            f"'{similar_team['guild_name']}' ({similar_team['similarity']:.0%} match). "
            "If this is meant to be a different team, please use a more distinct server name. "
            "If it's the same team, contact an admin instead of re-registering.",
            ephemeral=True,
        )
        return

    await ctx.defer(ephemeral=True)

    # Determine captain
    from ios_bot.commands.utils import fetch_member_live
    captain_member = captain or await fetch_member_live(guild, ctx.author.id) or ctx.author
    captain_id = captain_member.id
    captain_name = captain_member.display_name

    # Determine team type
    team_type_value = (team_type or "club").strip().lower()
    is_national_team = team_type_value == "national"
    is_mix_team = team_type_value == "mix"

    # Auto-detect channels by regex
    fives_channels, sixes_channels, eights_channels = auto_detect_matchmaking_channels(guild)

    guild_id = guild.id
    guild_name = guild.name
    guild_icon_url = str(guild.icon.url) if guild.icon else ""

    initial_players = [{"id": captain_id, "name": captain_name}]

    try:
        success = await bot.db.teams.add_team(
            guild_id=guild_id,
            guild_name=guild_name,
            guild_icon=guild_icon_url,
            captain_id=captain_id,
            captain_name=captain_name,
            sixes_channels=sixes_channels,
            eights_channels=eights_channels,
            fives_channels=fives_channels,
            initial_players=initial_players,
            is_national_team=is_national_team,
            is_mix_team=is_mix_team,
            vice_captain_id=vice_captain.id if vice_captain else None,
            vice_captain_name=vice_captain.display_name if vice_captain else None,
        )
    except Exception as e:
        from ios_bot.error_logger import log_error
        log_error(e, context={
            "guild_id": guild_id,
            "guild_name": guild_name,
            "captain_id": captain_id,
            "sixes_channels": sixes_channels,
            "eights_channels": eights_channels,
            "fives_channels": fives_channels,
            "initial_players": initial_players,
            "is_national_team": is_national_team,
            "is_mix_team": is_mix_team
        }, user_id=ctx.author.id, guild_id=guild_id, command="register_team")
        success = False

    if success:
        # Register captain as player directly
        try:
            team_data = await bot.db.teams.get_team(guild_id)
            if team_data:
                players = team_data.get('players', [])
                if not any(p.get('discord_id') == captain_id for p in players):
                    players.append({"id": captain_id, "name": captain_name})
                    await bot.db.teams.update_team_players(guild_id, players)
        except Exception as e:
            print(f"⚠️ Error during player registration: {e}")

        # Backfill match links for this newly registered team
        try:
            await bot.db.matches.backfill_matches_for_team(
                guild_id=guild_id,
                guild_name=guild_name,
                threshold=0.8
            )
        except Exception as e:
            print(f"Warning: failed to backfill match links for team {guild_id}: {e}")

    if success:
        if is_national_team:
            team_type_str = "National Team"
        elif is_mix_team:
            team_type_str = "Mix Team"
        else:
            team_type_str = "Club Team"
        embed = discord.Embed(title="✅ Team Registration Successful!", color=discord.Color.green())
        embed.description = f"**{guild_name}** has been registered as a **{team_type_str}**."
        embed.add_field(name="Captain", value=captain_name, inline=True)
        if vice_captain:
            embed.add_field(name="Vice-Captain", value=vice_captain.display_name, inline=True)

        if eights_channels:
            embed.add_field(name="8v8 Channels", value=", ".join([f"<#{ch_id}>" for ch_id in eights_channels]), inline=False)
        if sixes_channels:
            embed.add_field(name="6v6 Channels", value=", ".join([f"<#{ch_id}>" for ch_id in sixes_channels]), inline=False)
        if fives_channels:
            embed.add_field(name="5v5 Channels", value=", ".join([f"<#{ch_id}>" for ch_id in fives_channels]), inline=False)

        await ctx.followup.send(embed=embed, ephemeral=True)
        await announce_team_created(
            team_name=guild_name,
            creator_name=captain_name,
            guild_id=guild_id
        )
    else:
        await ctx.followup.send("❌ Team registration failed. Please check console for errors.", ephemeral=True)

@register_team.error
async def register_team_error(ctx: ApplicationContext, error: discord.DiscordException):
    try:
        if isinstance(error, commands.MissingPermissions):
            user_name = ctx.author.name
            user_id = ctx.author.id
            channel_name = ctx.channel.name
            channel_id = ctx.channel.id
            
            print(f"[PERMISSION ERROR] User '{user_name}' (ID: {user_id}) "
                  f"attempted to use /register_team in channel '{channel_name}' (ID: {channel_id}) "
                  f"without 'Manage Server' permission.")
            
            await ctx.respond("You are missing the 'Manage Server' permission required to run this command.", ephemeral=True)
        elif isinstance(error, AttributeError) and "'str' object has no attribute 'id'" in str(error):
            from ios_bot.error_logger import log_error
            log_error(error, context={
                "command": "register_team",
                "error_type": "AttributeError"
            }, user_id=ctx.author.id, guild_id=ctx.guild_id, command="register_team")
            
            print(f"[ATTRIBUTE ERROR] User '{ctx.author.name}' (ID: {ctx.author.id}) "
                  f"encountered an AttributeError in /register_team")
            await ctx.respond("❌ Error: An error occurred during registration. Please try again.", ephemeral=True)
        else:
            # Log all other errors
            from ios_bot.error_logger import log_error
            log_error(error, context={
                "command": "register_team",
                "error_type": type(error).__name__
            }, user_id=ctx.author.id, guild_id=ctx.guild_id, command="register_team")
            
            print(f"An unexpected error occurred with /register_team: {error}")
            await ctx.respond(f"An unexpected error occurred: {error}", ephemeral=True)
    except Exception as e:
        # Fallback error handling
        print(f"Critical error in register_team error handler: {e}")
        await ctx.respond("A critical error occurred. Please try again or contact an administrator.", ephemeral=True)

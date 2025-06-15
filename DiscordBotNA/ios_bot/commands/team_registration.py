from ios_bot.config import *
from ios_bot.database_manager import add_team, get_team
from ios_bot.announcements import announce_team_created

class TeamTypeSelectView(View):
    def __init__(self, author_id: int, guild: discord.Guild, vice_captain: discord.Member):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.guild = guild
        self.vice_captain = vice_captain
        self.is_national_team = None

        # Add buttons for team type selection
        self.add_item(Button(label="Club Team", style=ButtonStyle.primary, custom_id="team_type_club"))
        self.add_item(Button(label="National Team", style=ButtonStyle.secondary, custom_id="team_type_national"))

        # Add callbacks
        self.children[0].callback = self.on_team_type_selected
        self.children[1].callback = self.on_team_type_selected

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    async def on_team_type_selected(self, interaction: discord.Interaction):
        await interaction.response.defer()
        custom_id = interaction.data['custom_id']
        self.is_national_team = (custom_id == "team_type_national")
        
        # Proceed to the next step
        registration_view = RegistrationView(self.author_id, self.guild, self.vice_captain, self.is_national_team)
        await interaction.edit_original_response(
            content="Please provide the following details for team registration:", 
            view=registration_view
        )

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
        self.disabled = True # Disable after selection
        # Check if all selections are done and then proceed
        await interaction.response.edit_message(view=self.view) 
        # We need a way to submit the whole form, perhaps a button in the view

class RegistrationView(View):
    def __init__(self, author_id: int, guild: discord.Guild, vice_captain: discord.Member, is_national_team: bool):
        super().__init__(timeout=180) # 3 minutes timeout
        self.author_id = author_id
        self.guild = guild
        self.vice_captain_id = vice_captain.id
        self.vice_captain_name = vice_captain.display_name
        self.is_national_team = is_national_team
        self.eights_channels_selected = []
        self.sixes_channels_selected = []

        # 8v8 Channel Selection (only channels with '8' in the name, max 25)
        text_channels_8s = [ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages and re.search(r'8', ch.name)]
        text_channels_8s = text_channels_8s[:25]
        self.eights_select = ChannelSelect(text_channels_8s, "8v8", max_selectable=2)
        self.add_item(self.eights_select)
        
        # 6v6 Channel Selection (only channels with '6' in the name, max 25)
        text_channels_6s = [ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages and re.search(r'6', ch.name)]
        text_channels_6s = text_channels_6s[:25]
        self.sixes_select = ChannelSelect(text_channels_6s, "6v6", max_selectable=2)
        self.add_item(self.sixes_select)
        
        # Submit Button
        self.submit_button = discord.ui.Button(label="Complete Registration", style=discord.ButtonStyle.success, custom_id="submit_registration")
        self.submit_button.callback = self.submit_callback
        self.add_item(self.submit_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    async def submit_callback(self, interaction: discord.Interaction):
        captain_id = self.author_id
        captain_member = self.guild.get_member(captain_id)
        captain_name = captain_member.display_name
        guild_id = self.guild.id
        guild_name = self.guild.name
        guild_icon_url = str(self.guild.icon.url) if self.guild.icon else None

        # Only allow players who have access to at least one selected matchmaking channel and are not already captain or vice captain
        allowed_channels = set(self.eights_channels_selected + self.sixes_channels_selected)
        selected_channels = [self.guild.get_channel(ch_id) for ch_id in allowed_channels if self.guild.get_channel(ch_id)]
        eligible_members = [
            m for m in self.guild.members
            if not m.bot
            and m.id not in [captain_id, self.vice_captain_id]
            and any(
                ch.permissions_for(m).view_channel
                for ch in selected_channels
                if isinstance(ch, TextChannel)
            )
        ]
        initial_players = [
            {"id": captain_id, "name": captain_name},
            {"id": self.vice_captain_id, "name": self.vice_captain_name}
        ]
        # Optionally, add eligible_members to initial_players if you want to pre-register them

        # Prevent duplicate registration
        if await get_team(guild_id):
            await interaction.response.send_message(f"Team '{guild_name}' (this server) is already registered.", ephemeral=True)
            self.stop()
            return

        success = await add_team(
            guild_id=guild_id,
            guild_name=guild_name,
            guild_icon=guild_icon_url,
            captain_id=captain_id,
            captain_name=captain_name,
            vice_captain_id=self.vice_captain_id,
            vice_captain_name=self.vice_captain_name,
            eights_channels=self.eights_channels_selected,
            sixes_channels=self.sixes_channels_selected,
            initial_players=initial_players,
            is_national_team=self.is_national_team
        )

        if success:
            team_type_str = "National Team" if self.is_national_team else "Club Team"
            embed = discord.Embed(title="✅ Team Registration Successful!", color=discord.Color.green())
            embed.description = f"**{guild_name}** has been registered as a **{team_type_str}**."
            embed.add_field(name="Captain", value=captain_name, inline=True)
            embed.add_field(name="Vice Captain", value=self.vice_captain_name, inline=True)
            if self.eights_channels_selected:
                embed.add_field(name="8v8 Channels", value=", ".join([f"<#{ch_id}>" for ch_id in self.eights_channels_selected]), inline=False)
            if self.sixes_channels_selected:
                embed.add_field(name="6v6 Channels", value=", ".join([f"<#{ch_id}>" for ch_id in self.sixes_channels_selected]), inline=False)
            await interaction.response.edit_message(content=None, embed=embed, view=None)
            # Send announcement
            await announce_team_created(
                team_name=guild_name, 
                creator_name=captain_name, # Or interaction.user.display_name if preferred as the command issuer
                guild_id=guild_id
            )
        else:
            await interaction.response.edit_message(content="❌ Team registration failed. Please check console for errors.", embed=None, view=None)
        self.stop()

@bot.slash_command(
    name="register_team",
    description="Register your server as an IOSCA team and set up matchmaking channels."
)
@commands.has_permissions(manage_guild=True)
async def register_team(
    ctx: ApplicationContext,
    vice_captain: Option(discord.Member, "Select your vice captain (autocomplete)", required=True)
):
    guild = ctx.guild
    if not guild:
        await ctx.respond("This command can only be used in a server.", ephemeral=True)
        return

    # --- Validation ---
    if ctx.author.id == vice_captain.id:
        await ctx.respond("❌ The captain cannot also be the vice-captain. Please select a different user.", ephemeral=True)
        return

    # Check if team already registered
    if await get_team(guild.id):
        await ctx.respond(f"This server ('{guild.name}') is already registered as a team.", ephemeral=True)
        return

    view = TeamTypeSelectView(ctx.author.id, guild, vice_captain)
    await ctx.respond("First, what type of team are you registering?", view=view, ephemeral=True)

@register_team.error
async def register_team_error(ctx: ApplicationContext, error: discord.DiscordException):
    if isinstance(error, commands.MissingPermissions):
        user_name = ctx.author.name
        user_id = ctx.author.id
        channel_name = ctx.channel.name
        channel_id = ctx.channel.id
        
        print(f"[PERMISSION ERROR] User '{user_name}' (ID: {user_id}) "
              f"attempted to use /register_team in channel '{channel_name}' (ID: {channel_id}) "
              f"without 'Manage Server' permission.")
        
        await ctx.respond("You are missing the 'Manage Server' permission required to run this command.", ephemeral=True)
    else:
        # Optionally, handle other errors or re-raise them
        print(f"An unexpected error occurred with /register_team: {error}")
        await ctx.respond(f"An unexpected error occurred: {error}", ephemeral=True)
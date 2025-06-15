from ios_bot.config import *
from ios_bot.database_manager import get_team, get_all_teams

class TeamSelectView(View):
    def __init__(self, author_id: int, teams_data: list):
        super().__init__(timeout=180)
        self.author_id = author_id

        options = []
        if teams_data:
            for team in teams_data:
                if isinstance(team, dict) and 'guild_name' in team and 'guild_id' in team:
                    label = team.get('guild_name', f"ID: {team['guild_id']}")
                    description = f"ID: {team['guild_id']}"
                    options.append(SelectOption(label=label, value=str(team['guild_id']), description=description))
                else:
                    print(f"Skipping malformed team data: {team}")

        if not options:
            options.append(SelectOption(label="No teams available", value="no_teams_placeholder", description="No teams found to display."))
            
        self.team_select_menu = Select(
            placeholder="Select a team to view details...",
            min_values=1,
            max_values=1,
            options=options,
            disabled= (not options or options[0].value == "no_teams_placeholder")
        )
        self.team_select_menu.callback = self.select_callback
        self.add_item(self.team_select_menu)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        
        selected_guild_id = self.team_select_menu.values[0]
        if selected_guild_id == "no_teams_placeholder":
            await interaction.followup.send("No team selected or available.", ephemeral=True)
            return

        team_details = await get_team(int(selected_guild_id))

        if not team_details:
            await interaction.followup.send(content="Error: Could not fetch details for the selected team.", ephemeral=True)
            return

        embed = Embed(title=f"Team Details", color=discord.Color.blue())
        embed.set_author(name=team_details.get('guild_name', 'Unknown Team'), 
                         icon_url=team_details.get('guild_icon') if team_details.get('guild_icon') else None)
        
        if team_details.get('guild_icon'):
            embed.set_thumbnail(url=team_details.get('guild_icon'))
        
        embed.add_field(name="Captain", value=f"{team_details.get('captain_name', 'N/A')} (<@{team_details.get('captain_id', 'N/A')}>)", inline=True)
        embed.add_field(name="Vice Captain", value=f"{team_details.get('vice_captain_name', 'N/A')} (<@{team_details.get('vice_captain_id', 'N/A')}>)", inline=True)
        
        players_list = team_details.get('players', [])
        if players_list:
            player_mentions = []
            for player_info in players_list:
                if isinstance(player_info, dict):
                    player_id = player_info.get('id')
                    player_name = player_info.get('name', 'Unknown Player')
                    if player_id:
                        # Attempt to fetch the member to get their current name and mention
                        member = interaction.guild.get_member(player_id)
                        if member:
                             # Format: @mention (DisplayName)
                            player_mentions.append(f"{member.mention} ({member.display_name})")
                        else:
                            # Fallback if the player is no longer in the server
                            player_mentions.append(f"{player_name}")
                    else:
                        player_mentions.append(player_name)

            embed.add_field(name=f"Registered Players ({len(player_mentions)})", value="\n".join(player_mentions) if player_mentions else "No players listed.", inline=False)
        else:
            embed.add_field(name="Players", value="No players listed.", inline=False)
        
        embed.set_footer(text=f"Requested by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)
        embed.timestamp = datetime.now(timezone.utc)

        await interaction.edit_original_response(embed=embed, view=None)

@bot.slash_command(
    name="view_teams",
    description="View a list of registered IOSCA teams and their details."
)
async def view_teams_command(ctx: ApplicationContext):
    teams = await get_all_teams()
    if not teams:
        await ctx.respond("No teams are currently registered.", ephemeral=True)
        return
    
    view = TeamSelectView(ctx.author.id, teams)
    await ctx.respond("Registered IOSCA Teams:", view=view, ephemeral=False) 
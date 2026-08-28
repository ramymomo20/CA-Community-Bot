from ios_bot.config import *
from ios_bot.utils.name_utils import truncate_name
import os
import json
from urllib.parse import quote


def _parse_vice_captain_ids(raw) -> list:
    """vice_captain_ids is stored as a JSONB list of raw Discord ids."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            raw = []
    if not isinstance(raw, (list, tuple, set)):
        return []
    result = []
    for value in raw:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


HUB_TEAM_PAGE_URL_BASE = os.getenv(
    "IOSCA_HUB_TEAM_PAGE_URL_BASE",
    "https://ramymomo20.github.io/ioscahub.github.io/#/teams",
).strip()


def _build_hub_team_url(team_id: int) -> str:
    base = (HUB_TEAM_PAGE_URL_BASE or "https://ramymomo20.github.io/ioscahub.github.io/#/teams").rstrip("/")
    return f"{base}/{quote(str(int(team_id)), safe='')}"

class TeamSelectView(View):
    def __init__(self, author_id: int, teams_data: list):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.all_teams = teams_data
        self.current_page = 0
        self.teams_per_page = 24  # Leave room for navigation option if needed
        self.total_pages = (len(teams_data) - 1) // self.teams_per_page + 1 if teams_data else 1

        self.update_view()

    def update_view(self):
        """Update the view with current page of teams."""
        self.clear_items()

        options = []
        
        if self.all_teams:
            # Get teams for current page
            start_idx = self.current_page * self.teams_per_page
            end_idx = start_idx + self.teams_per_page
            page_teams = self.all_teams[start_idx:end_idx]
            
            for team in page_teams:
                if isinstance(team, dict) and 'guild_name' in team and 'guild_id' in team:
                    label = team.get('guild_name', f"ID: {team['guild_id']}")[:100]  # Discord label limit
                    description = f"Team ID: {team['guild_id']}"[:100]  # Discord description limit
                    options.append(SelectOption(label=label, value=str(team['guild_id']), description=description))
                else:
                    print(f"Skipping malformed team data: {team}")
            
            # Add navigation option if there are multiple pages
            if self.total_pages > 1:
                nav_label = f"📄 Page {self.current_page + 1}/{self.total_pages}"
                nav_desc = "Click to see navigation buttons"
                options.append(SelectOption(
                    label=nav_label,
                    value="navigation",
                    description=nav_desc,
                    emoji="📄"
                ))

        if not options:
            options.append(SelectOption(
                label="No teams available", 
                value="no_teams_placeholder", 
                description="No teams found to display."
            ))
            
        # Create the select menu
        self.team_select_menu = Select(
            placeholder=f"Select a team to view details... (Page {self.current_page + 1}/{self.total_pages})" if self.total_pages > 1 else "Select a team to view details...",
            min_values=1,
            max_values=1,
            options=options,
            disabled=(not options or options[0].value == "no_teams_placeholder")
        )
        self.team_select_menu.callback = self.select_callback
        self.add_item(self.team_select_menu)
        
        # Add navigation buttons if there are multiple pages
        if self.total_pages > 1:
            prev_button = Button(
                label="◀️ Previous",
                style=discord.ButtonStyle.secondary,
                disabled=self.current_page == 0
            )
            prev_button.callback = self.previous_page
            self.add_item(prev_button)
            
            next_button = Button(
                label="Next ▶️",
                style=discord.ButtonStyle.secondary,
                disabled=self.current_page >= self.total_pages - 1
            )
            next_button.callback = self.next_page
            self.add_item(next_button)

    async def previous_page(self, interaction: discord.Interaction):
        """Go to previous page of teams."""
        self.current_page -= 1
        self.update_view()
        
        embed = discord.Embed(
            title="🏆 IOSCA Teams",
            description=f"Select a team to view details and statistics.\nPage {self.current_page + 1} of {self.total_pages}",
            color=discord.Color.blue()
        )
        
        await interaction.response.edit_message(embed=embed, view=self)

    async def next_page(self, interaction: discord.Interaction):
        """Go to next page of teams."""
        self.current_page += 1
        self.update_view()
        
        embed = discord.Embed(
            title="🏆 IOSCA Teams",
            description=f"Select a team to view details and statistics.\nPage {self.current_page + 1} of {self.total_pages}",
            color=discord.Color.blue()
        )
        
        await interaction.response.edit_message(embed=embed, view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    async def select_callback(self, interaction: discord.Interaction):
        try:
            selected_value = self.team_select_menu.values[0]
            
            if selected_value == "no_teams_placeholder":
                await interaction.response.send_message("No team selected or available.", ephemeral=True)
                return
            elif selected_value == "navigation":
                await interaction.response.send_message("Use the navigation buttons below to browse pages.", ephemeral=True)
                return

            await interaction.response.defer(ephemeral=False)
            
            team_details = await bot.db.teams.get_team(int(selected_value))

            if not team_details:
                await interaction.followup.send(content="Error: Could not fetch details for the selected team.", ephemeral=True)
                return

            # Create team info view with navigation buttons
            view = TeamInfoView(team_details)
            await view.show_team_info(interaction)
        except Exception as e:
            from ios_bot.error_logger import log_error
            log_error(e, context={
                "selected_value": selected_value,
                "interaction_guild_id": interaction.guild_id,
                "interaction_channel_id": interaction.channel_id
            }, user_id=interaction.user.id, guild_id=interaction.guild_id, command="view_teams")
            await interaction.followup.send(content="❌ Error loading team details. Please try again.", ephemeral=True)


    async def on_timeout(self):
        """Handle timeout by disabling all items."""
        for item in self.children:
            item.disabled = True

class TeamInfoView(View):
    def __init__(self, team):
        super().__init__(timeout=300)
        self.team = team
        
        # Add navigation buttons
        stats_button = Button(label="📊 View Statistics", style=discord.ButtonStyle.success)
        stats_button.callback = self.show_stats
        self.add_item(stats_button)
        
        matches_button = Button(label="⚽ Recent Matches", style=discord.ButtonStyle.primary)
        matches_button.callback = self.show_matches
        self.add_item(matches_button)

    async def show_team_info(self, interaction):
        """Show basic team information."""
        team = self.team
        try:
            await bot.db.teams.update_team_average_rating(team['guild_id'])
            team = await bot.db.teams.get_team(team['guild_id']) or team
            self.team = team
        except Exception:
            pass
        
        embed = Embed(title=f"🏆 {team['guild_name']}", color=discord.Color.gold())
        embed.set_author(name=team.get('guild_name', 'Unknown Team'), 
                         icon_url=team.get('guild_icon') if team.get('guild_icon') else None)
        
        if team.get('guild_icon'):
            embed.set_thumbnail(url=team.get('guild_icon'))
        
        # Get captain and vice captain display names instead of mentions  
        captain_display = team.get('captain_name', 'N/A')
        captain_id = team.get('captain_id')
        if captain_id:
            captain_member = interaction.guild.get_member(captain_id)
            if captain_member:
                captain_display = captain_member.display_name
        
        vice_captain_ids = _parse_vice_captain_ids(team.get('vice_captain_ids'))
        vice_captain_id = vice_captain_ids[0] if vice_captain_ids else None
        vice_captain_display = None
        if vice_captain_id:
            vice_captain_member = interaction.guild.get_member(vice_captain_id)
            if vice_captain_member:
                vice_captain_display = vice_captain_member.display_name

        embed.add_field(name="👑 Captain", value=captain_display, inline=True)
        if vice_captain_display:
            embed.add_field(name="🎖️ Vice-Captain", value=vice_captain_display, inline=True)
        embed.add_field(name="📋 Team ID", value=str(team['guild_id']), inline=True)
        
        # Add average team rating if available
        avg_rating = team.get('average_rating')
        if avg_rating:
            embed.add_field(name="⭐ Average Rating", value=f"{avg_rating:.2f}", inline=True)
        else:
            embed.add_field(name="⭐ Average Rating", value="N/A", inline=True)
        
        # Player count and channel info
        players_list = team.get('players', [])
        total_players = len(players_list) + (1 if team.get('captain_id') else 0)
        embed.add_field(name="👥 Total Players", value=str(total_players), inline=True)

        # Player list with role emojis - include captain and vice captain
        player_mentions = []
        processed_players = set()  # Track processed player IDs to avoid duplicates
        
        # First, add captain and vice captain if they're not already in the players list
        if captain_id and captain_id not in processed_players:
            captain_member = interaction.guild.get_member(captain_id)
            if captain_member:
                player_mentions.append(f"{captain_member.display_name} 👑")
                processed_players.add(captain_id)
        
        if vice_captain_id and vice_captain_id not in processed_players:
            vice_captain_member = interaction.guild.get_member(vice_captain_id)
            if vice_captain_member:
                player_mentions.append(f"{vice_captain_member.display_name} 👑")
                processed_players.add(vice_captain_id)

        # Then add all other players from the players list
        if players_list:
            for player_info in players_list:
                if isinstance(player_info, dict):
                    player_id = player_info.get('id')
                    player_name = player_info.get('name', 'Unknown Player')
                    if player_id and player_id not in processed_players:
                        # Attempt to fetch the member to get their current name and mention
                        member = interaction.guild.get_member(player_id)
                        if member:
                            player_mentions.append(f"{member.display_name}")
                        else:
                            # Fallback if the player is no longer in the server
                            player_mentions.append(f"{player_name}")
                        processed_players.add(player_id)
                    elif not player_id:
                        player_mentions.append(player_name)

        # Use simple display format without character limit concerns
        player_text = "\n".join(player_mentions) if player_mentions else "No players listed."
        embed.add_field(name=f"Registered Players ({len(player_mentions)})", value=player_text, inline=False)
        embed.add_field(
            name="IOSCA Hub",
            value=f"[Visit the IOSCA Hub to view this team.]({_build_hub_team_url(team['guild_id'])})",
            inline=False,
        )
        
        embed.set_footer(text=f"Use the buttons below to view statistics and match history", icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)
        embed.timestamp = datetime.now(timezone.utc)

        # Handle interaction properly depending on its state
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=embed, view=self)
            else:
                await interaction.response.edit_message(embed=embed, view=self)
        except discord.errors.NotFound:
            # Interaction expired, send a new message
            await interaction.followup.send(embed=embed, view=self, ephemeral=True)
        except Exception as e:
            # Fallback: try to send a new message
            try:
                await interaction.followup.send(embed=embed, view=self, ephemeral=True)
            except:
                # Last resort: just send content without view
                await interaction.followup.send(content="❌ Team info display error. Please try again.", ephemeral=True)

    async def show_stats(self, interaction):
        """Show team statistics."""
        try:
            await interaction.response.defer()
            try:
                await bot.db.teams.update_team_average_rating(self.team['guild_id'])
            except Exception:
                pass
            
            # Get team statistics
            stats = await bot.db.matches.get_team_statistics(self.team['guild_id'])
            top_players = await bot.db.players.get_top_team_players(self.team['guild_id'], 5)
            
            if not stats:
                await interaction.edit_original_response(content="❌ No statistics found for this team.", embed=None, view=None)
                return
            
            embed = discord.Embed(
                title=f"📊 {self.team['guild_name']} - Statistics",
                color=discord.Color.green()
            )
            
            if self.team.get('guild_icon'):
                embed.set_thumbnail(url=self.team['guild_icon'])
            
            # Match statistics - use the flat structure
            total_matches = stats.get('total_matches', 0)
            wins = stats.get('wins', 0)
            draws = stats.get('draws', 0)
            losses = stats.get('losses', 0)
            win_rate = (wins / total_matches * 100) if total_matches > 0 else 0
            
            embed.add_field(
                name="⚽ Match Record (All-Time)",
                value=(
                    f"**Total:** {total_matches}\n"
                    f"**Wins:** {wins}\n"
                    f"**Draws:** {draws}\n"
                    f"**Losses:** {losses}\n"
                    f"**Win Rate:** {win_rate:.1f}%\n"
                    f"_Source: MATCH_STATS (separate from tournament fixture standings)_"
                ),
                inline=True
            )
            
            # Goal statistics - use the flat structure
            goals_for = stats.get('goals_for', 0)
            goals_against = stats.get('goals_against', 0)
            goal_difference = stats.get('goal_difference', 0)
            
            embed.add_field(
                name="🥅 Goals",
                value=f"**For:** {goals_for}\n**Against:** {goals_against}\n**Difference:** {goal_difference:+d}",
                inline=True
            )
            
            # Player statistics - simplified for now since we don't have player totals in the current structure
            embed.add_field(
                name="👥 Team Summary",
                value=f"**Team:** {stats.get('team_name', 'Unknown')}\n**Active:** Yes",
                inline=True
            )
            
            # Top players
            if top_players:
                player_text = ""
                for i, player in enumerate(top_players, 1):
                    # Handle different possible structures of top_players
                    if isinstance(player, dict):
                        player_name = player.get('player_name', 'Unknown')
                        matches_played = player.get('matches_played', 0)
                        avg_goals = player.get('avg_goals', 0)
                        player_text += f"{i}. **{player_name}** - {matches_played} matches (avg: {avg_goals:.1f} goals)\n"
                    else:
                        player_text += f"{i}. **{str(player)}**\n"
                
                embed.add_field(
                    name="🌟 Top 5 Players",
                    value=player_text[:1024] if player_text else "No player data available",
                    inline=False
                )
            
            # Recent matches removed from stats view (use the Matches button instead)
            embed.add_field(
                name="IOSCA Hub",
                value=f"[Visit the IOSCA Hub to view this team.]({_build_hub_team_url(self.team['guild_id'])})",
                inline=False,
            )
            
            # Create back button view
            view = TeamStatsView(self.team)
            
            await interaction.edit_original_response(embed=embed, view=view)
            
        except Exception as e:
            await interaction.edit_original_response(content=f"❌ Error loading statistics: {str(e)}", embed=None, view=None)

    async def show_matches(self, interaction):
        """Show recent matches."""
        try:
            await interaction.response.defer()
            matches = await bot.db.matches.get_matches_by_team(self.team['guild_id'], limit=None)
            view = TeamMatchesPaginationView(self.team, matches, page_size=10)
            await interaction.edit_original_response(embed=view.build_embed(), view=view)
        except Exception as e:
            await interaction.edit_original_response(content=f"❌ Error loading matches: {str(e)}", embed=None, view=None)

    async def on_timeout(self):
        """Handle timeout by disabling the view."""
        for item in self.children:
            item.disabled = True

class TeamStatsView(View):
    def __init__(self, team):
        super().__init__(timeout=300)
        self.team = team
        
        # Back button
        back_button = Button(label="← Back to Team", style=discord.ButtonStyle.secondary, emoji="🔙")
        back_button.callback = self.back_to_team
        self.add_item(back_button)
    
    async def back_to_team(self, interaction):
        """Return to team info view."""
        try:
            await interaction.response.defer()
            view = TeamInfoView(self.team)
            await view.show_team_info(interaction)
        except discord.errors.InteractionResponded:
            # Interaction already responded to, create new view
            view = TeamInfoView(self.team)
            await view.show_team_info(interaction)
        except Exception as e:
            await interaction.followup.send(content="❌ Error returning to team view. Please try again.", ephemeral=True)
    
    async def on_timeout(self):
        """Handle timeout by disabling the view."""
        for item in self.children:
            item.disabled = True

class TeamMatchesPaginationView(View):
    def __init__(self, team, matches, page_size: int = 10):
        super().__init__(timeout=300)
        self.team = team
        self.matches = matches
        self.page_size = page_size
        self.page = 0
        self.prev_button = Button(label="Previous", style=discord.ButtonStyle.secondary)
        self.next_button = Button(label="Next", style=discord.ButtonStyle.secondary)
        self.back_button = Button(label="Back to Team", style=discord.ButtonStyle.secondary, emoji="🔙")
        self.prev_button.callback = self.prev_page
        self.next_button.callback = self.next_page
        self.back_button.callback = self.back_to_team
        self._sync_buttons()
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.back_button)

    def _sync_buttons(self):
        total_pages = max(1, (len(self.matches) - 1) // self.page_size + 1)
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= total_pages - 1
        self.prev_button.label = f"Page {self.page + 1}/{total_pages}"

    def build_embed(self):
        team_id = self.team.get('guild_id')
        start = self.page * self.page_size
        end = start + self.page_size
        page_matches = self.matches[start:end]

        embed = discord.Embed(
            title=f"Matches - {self.team['guild_name']}",
            color=discord.Color.blue()
        )
        if self.team.get('guild_icon'):
            embed.set_thumbnail(url=self.team['guild_icon'])

        if not page_matches:
            embed.description = "No matches found for this team in the database."
            return embed

        lines = []
        for match in page_matches:
            home_id = match.get('home_guild_id')
            away_id = match.get('away_guild_id')
            home_name = match.get('home_team_name') or match.get('home_team_display_name') or 'Home'
            away_name = match.get('away_team_name') or match.get('away_team_display_name') or 'Away'
            if home_id == team_id:
                opponent = away_name
            elif away_id == team_id:
                opponent = home_name
            else:
                opponent = away_name
            opponent = truncate_name(opponent, max_length=20)
            scoreline = f"{match.get('home_score', 0)}-{match.get('away_score', 0)}"
            dt = match.get('datetime')
            date_str = dt.strftime('%Y-%m-%d') if hasattr(dt, 'strftime') else str(dt)[:10]
            lines.append(f"vs {opponent} ({scoreline}) ({date_str})")

        embed.description = "\n".join(lines)
        embed.add_field(
            name="IOSCA Hub",
            value=f"[Visit the IOSCA Hub to view this team.]({_build_hub_team_url(self.team['guild_id'])})",
            inline=False,
        )
        embed.set_footer(text=f"Showing {len(page_matches)} of {len(self.matches)} matches")
        return embed

    async def prev_page(self, interaction):
        await interaction.response.defer()
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        await interaction.edit_original_response(embed=self.build_embed(), view=self)

    async def next_page(self, interaction):
        await interaction.response.defer()
        self.page += 1
        self._sync_buttons()
        await interaction.edit_original_response(embed=self.build_embed(), view=self)

    async def back_to_team(self, interaction):
        try:
            await interaction.response.defer()
            view = TeamInfoView(self.team)
            await view.show_team_info(interaction)
        except discord.errors.InteractionResponded:
            view = TeamInfoView(self.team)
            await view.show_team_info(interaction)
        except Exception:
            await interaction.followup.send(content="❌ Error returning to team view. Please try again.", ephemeral=True)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

@bot.slash_command(
    name="view_teams",
    description="View a list of registered teams and their details."
)
async def view_teams_command(
    ctx: ApplicationContext,
    prune_missing_guilds: Option(
        bool,
        "Admin only: remove teams whose Discord guild no longer exists for this bot",
        required=False,
        default=False,
    ) = False,
):
    await ctx.defer(ephemeral=False)

    if prune_missing_guilds and not ctx.author.guild_permissions.administrator:
        await ctx.followup.send("Admin permission required to prune missing guild teams.", ephemeral=True)
        return

    try:
        cleanup_result = await bot.db.teams.clean_all_teams(max_players=17)
        if cleanup_result:
            processed = cleanup_result.get("total_teams_processed", cleanup_result.get("teams_processed", 0))
            duplicates = cleanup_result.get("total_duplicates_removed", 0)
            limited = cleanup_result.get("total_limit_enforced", 0)
            if duplicates > 0 or limited > 0:
                print(
                    f"Auto-cleanup completed: {processed} teams processed, "
                    f"{duplicates} duplicates removed, {limited} over-limit removals"
                )
    except Exception as e:
        print(f"Auto-cleanup failed: {e}")

    missing_teams = []
    removed_count = 0
    try:
        detailed = await bot.db.teams.get_all_teams_with_details()
        for team in detailed:
            team_guild_id = team.get("guild_id")
            if team_guild_id is None:
                continue
            guild_obj = bot.get_guild(int(team_guild_id))
            if guild_obj is None:
                try:
                    guild_obj = await bot.fetch_guild(int(team_guild_id))
                except Exception:
                    guild_obj = None
            if guild_obj is None:
                missing_teams.append(team)

        if prune_missing_guilds and missing_teams:
            for team in missing_teams:
                if await bot.db.teams.delete_team(int(team.get("guild_id"))):
                    removed_count += 1
    except Exception as e:
        print(f"Guild existence validation failed: {e}")

    teams = await bot.db.teams.get_all_teams()
    if not teams:
        await ctx.followup.send("No teams are currently registered.", ephemeral=True)
        return

    view = TeamSelectView(ctx.author.id, teams)
    embed = discord.Embed(
        title="IOSCA Teams",
        description="Select a team to view details and statistics.\n"
        + (f"Page 1 of {view.total_pages}" if view.total_pages > 1 else f"Total teams: {len(teams)}"),
        color=discord.Color.blue(),
    )

    if view.total_pages > 1:
        embed.add_field(
            name="Navigation",
            value="Use the dropdown to select teams or navigation buttons to browse pages.",
            inline=False,
        )

    if missing_teams:
        preview = ", ".join(str(t.get("guild_name") or t.get("guild_id")) for t in missing_teams[:5])
        if prune_missing_guilds:
            embed.add_field(
                name="Missing Guild Cleanup",
                value=f"Removed {removed_count}/{len(missing_teams)} missing teams.\nExamples: {preview}",
                inline=False,
            )
        else:
            embed.add_field(
                name="Missing Team Guilds Detected",
                value=(
                    f"{len(missing_teams)} teams reference guilds the bot cannot access.\n"
                    f"Examples: {preview}\n"
                    f"Run `/view_teams prune_missing_guilds:true` as admin to remove them."
                ),
                inline=False,
            )

    embed.set_footer(text=f"Total teams: {len(teams)}")
    await ctx.followup.send(embed=embed, view=view, ephemeral=False)

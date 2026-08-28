from ios_bot.config import *
import os
from collections import Counter
from datetime import datetime, timezone
import json
from urllib.parse import quote


HUB_PLAYER_PAGE_URL_BASE = os.getenv(
    "IOSCA_HUB_PLAYER_PAGE_URL_BASE",
    "https://ramymomo20.github.io/ioscahub.github.io/#/players",
).strip()


def _build_hub_player_url(steam_id: str) -> str:
    base = (HUB_PLAYER_PAGE_URL_BASE or "https://ramymomo20.github.io/ioscahub.github.io/#/players").rstrip("/")
    return f"{base}/{quote(str(steam_id).strip(), safe='')}"


def _format_distance_km(value: object) -> str:
    try:
        meters = float(value or 0)
    except Exception:
        meters = 0.0
    km = meters / 1000.0
    return f"{km:.2f} km"

class PlayerStatsView(discord.ui.View):
    def __init__(self, user, club_team_info, national_team_info, mix_team_info, club_team_position, club_team_stats, club_team_appearances, national_team_position, national_team_stats, national_team_appearances, mix_team_position, mix_team_stats, mix_team_appearances, all_time_pos, all_time_stats, total_appearances, player_rating, steam_id):
        super().__init__(timeout=180)
        self.user = user
        self.club_team_info = club_team_info
        self.national_team_info = national_team_info
        self.mix_team_info = mix_team_info
        self.club_team_position = club_team_position
        self.club_team_stats = club_team_stats
        self.club_team_appearances = club_team_appearances
        self.national_team_position = national_team_position
        self.national_team_stats = national_team_stats
        self.national_team_appearances = national_team_appearances
        self.mix_team_position = mix_team_position
        self.mix_team_stats = mix_team_stats
        self.mix_team_appearances = mix_team_appearances
        self.all_time_pos = all_time_pos
        self.all_time_stats = all_time_stats
        self.total_appearances = total_appearances
        self.player_rating = player_rating
        self.steam_id = steam_id
        self.current_page = 0

        # Define available pages based on what data we have
        self.pages = ["all_time"]  # Always show all-time stats
        
        if self.club_team_info:
            self.pages.append("club")
        if self.national_team_info:
            self.pages.append("national")
        if self.mix_team_info:
            self.pages.append("mix")
        if self.total_appearances > 0:  # Only show weekly if player has stats
            self.pages.append("weekly")

    def _add_hub_field(self, embed: discord.Embed):
        embed.add_field(
            name="IOSCA Hub",
            value=f"[Visit the IOSCA Hub to view this player.]({_build_hub_player_url(self.steam_id)})",
            inline=False,
        )
    
    async def create_club_stats_embed(self):
        """Create the detailed club team stats embed (Page 2)"""
        is_captain = self.club_team_info and self.club_team_info.get('captain_id') == self.user.id
        color = discord.Color.gold() if is_captain else discord.Color.blue()
        
        embed = discord.Embed(
            title=f"📊 {self.user.display_name} - Club Team Stats",
            color=color
        )
        
        # Set team image as thumbnail if available
        if self.club_team_info and self.club_team_info.get('image_url'):
            embed.set_thumbnail(url=self.club_team_info['image_url'])
        
        embed.set_author(name=self.user.display_name, icon_url=self.user.display_avatar.url)
        
        if self.club_team_info:
            team_name = self.club_team_info.get('name') or self.club_team_info.get('guild_name', 'Unknown Team')
            is_vice_captain = self.user.id in (self.club_team_info.get('vice_captain_ids') or [])
            if is_captain:
                role_text = " (CAPTAIN)"
            elif is_vice_captain:
                role_text = " (VICE-CAPTAIN)"
            else:
                role_text = ""
            embed.add_field(name="**Team**", value=f"`{team_name}`{role_text}", inline=True)
        else:
            embed.add_field(name="**Team**", value="FREE AGENT", inline=True)
            
        embed.add_field(name="**Position**", value=f"`{self.club_team_position or 'N/A'}`", inline=True)
        embed.add_field(name="**Appearances**", value=f"`{self.club_team_appearances}`", inline=True)        
        
        # Add player rating as a non-inline field
        if self.player_rating is not None:
            embed.add_field(name="🌟 **Player Rating**", value=f"`{self.player_rating:.2f}/10.0`", inline=False)
        else:
            embed.add_field(name="🌟 **Player Rating**", value="`Not Available`", inline=False)
        
        if self.club_team_stats and self.club_team_appearances > 0:
            # Attacking stats
            attacking_stats = [
                f"**Goals:** `{int(float(self.club_team_stats.get('goals', 0)))}`",
                f"**Assists:** `{int(float(self.club_team_stats.get('assists', 0)))}`",
                f"**2nd Assists:** `{int(float(self.club_team_stats.get('secondAssists', 0)))}`",
                f"**Shots:** `{int(float(self.club_team_stats.get('shots', 0)))}`",
                f"**Shots on Goal:** `{int(float(self.club_team_stats.get('shotsOnGoal', 0)))}`",
                f"**Offsides:** `{int(float(self.club_team_stats.get('offsides', 0)))}`"
            ]
            embed.add_field(name="⚽ **Attacking**", value="\n".join(attacking_stats), inline=True)
            
            # Playmaking stats
            playmaking_stats = [
                f"**Chances Created:** `{int(float(self.club_team_stats.get('chancesCreated', 0)))}`",
                f"**Key Passes:** `{int(float(self.club_team_stats.get('keyPasses', 0)))}`",
                f"**Passes:** `{int(float(self.club_team_stats.get('passes', 0)))}`",
                f"**Passes Completed:** `{int(float(self.club_team_stats.get('passesCompleted', 0)))}`",
                f"**Corners:** `{int(float(self.club_team_stats.get('corners', 0)))}`",
                f"**Free Kicks:** `{int(float(self.club_team_stats.get('freeKicks', 0)))}`"
            ]
            
            # Calculate pass completion percentage
            passes = int(float(self.club_team_stats.get('passes', 0)))
            passes_completed = int(float(self.club_team_stats.get('passesCompleted', 0)))
            pass_rate = f"{passes_completed/passes:.1%}" if passes > 0 else "0%"
            playmaking_stats.append(f"**Pass Rate:** `{pass_rate}`")
            
            embed.add_field(name="🎯 **Playmaking**", value="\n".join(playmaking_stats), inline=True)
            
            # Defensive stats
            defensive_stats = [
                f"**Interceptions:** `{int(float(self.club_team_stats.get('interceptions', 0)))}`",
                f"**Tackles:** `{int(float(self.club_team_stats.get('slidingTacklesCompleted', 0)))}`",
                f"**Tackle Attempts:** `{int(float(self.club_team_stats.get('slidingTackles', 0)))}`",
                f"**Fouls:** `{int(float(self.club_team_stats.get('fouls', 0)))}`",
                f"**Fouls Suffered:** `{int(float(self.club_team_stats.get('foulsSuffered', 0)))}`",
                f"**Own Goals:** `{int(float(self.club_team_stats.get('ownGoals', 0)))}`"
            ]
            embed.add_field(name="🛡️ **Defensive**", value="\n".join(defensive_stats), inline=True)
            
            # Goalkeeper stats
            saves = int(float(self.club_team_stats.get('keeperSaves', 0)))
            goals_conceded = int(float(self.club_team_stats.get('goalsConceded', 0)))
            total_shots_faced = goals_conceded + saves
            save_rate = (saves / total_shots_faced * 100) if total_shots_faced > 0 else 0
            
            goalkeeper_stats = [
                f"**Saves:** `{saves}`",
                f"**Saves Caught:** `{int(float(self.club_team_stats.get('keeperSavesCaught', 0)))}`",
                f"**Goals Conceded:** `{goals_conceded}`",
                f"**Save Rate:** `{save_rate:.1f}%`"
            ]
            embed.add_field(name="🥅 **Goalkeeper**", value="\n".join(goalkeeper_stats), inline=True)
            
            # Discipline & Physical stats
            discipline_stats = [
                f"**Yellow Cards:** `{int(float(self.club_team_stats.get('yellowCards', 0)))}`",
                f"**Red Cards:** `{int(float(self.club_team_stats.get('redCards', 0)))}`",
                f"**Penalties:** `{int(float(self.club_team_stats.get('penalties', 0)))}`",
                f"**Distance Covered:** `{_format_distance_km(self.club_team_stats.get('distanceCovered', 0))}`",
                f"**Possession:** `{(int(float(self.club_team_stats.get('possession'))) / (self.club_team_appearances * 10)):.2f}%`"
            ]
            embed.add_field(name="📋 **Discipline & Physical**", value="\n".join(discipline_stats), inline=True)
            embed.add_field(name="🧠 **Derived Impact**", value=format_derived_metrics(self.club_team_stats), inline=True)
        else:
            embed.add_field(name="**Stats**", value="No competitive stats found for this team.", inline=False)
        self._add_hub_field(embed)
            
        embed.set_footer(text="Page 2/3 - Club Team Stats • Use buttons to navigate")
        return embed
    
    async def create_all_time_stats_embed(self):
        """Create the all-time stats embed (Page 1)"""
        is_captain_of_any_team = any(team.get('captain_id') == self.user.id for team in [self.club_team_info, self.national_team_info] if team)
        color = discord.Color.gold() if is_captain_of_any_team else discord.Color.blue()
        
        embed = discord.Embed(
            title=f"📈 {self.user.display_name} - All-Time Stats",
            color=color
        )
        
        embed.set_author(name=f"{self.user.display_name}", icon_url=self.user.display_avatar.url)
        
        embed.add_field(name="**Most Played Position**", value=f"`{self.all_time_pos or 'N/A'}`", inline=True)
        embed.add_field(name="**Total Appearances**", value=f"`{self.total_appearances}`", inline=True)
        embed.add_field(name="**Teams**", value="All Teams Combined", inline=True)
        
        # Add player rating as a non-inline field
        if self.player_rating is not None:
            embed.add_field(name="🌟 **Player Rating**", value=f"`{self.player_rating:.2f}/10.0`", inline=False)
        else:
            embed.add_field(name="🌟 **Player Rating**", value="`Not Available`", inline=False)
        
        if self.all_time_stats:
            # Attacking stats
            attacking_stats = [
                f"**Goals:** `{int(float(self.all_time_stats.get('goals', 0)))}`",
                f"**Assists:** `{int(float(self.all_time_stats.get('assists', 0)))}`",
                f"**2nd Assists:** `{int(float(self.all_time_stats.get('secondAssists', 0)))}`",
                f"**Shots:** `{int(float(self.all_time_stats.get('shots', 0)))}`",
                f"**Shots on Goal:** `{int(float(self.all_time_stats.get('shotsOnGoal', 0)))}`",
                f"**Offsides:** `{int(float(self.all_time_stats.get('offsides', 0)))}`"
            ]
            embed.add_field(name="⚽ **Attacking**", value="\n".join(attacking_stats), inline=True)
            
            # Playmaking stats
            playmaking_stats = [
                f"**Chances Created:** `{int(float(self.all_time_stats.get('chancesCreated', 0)))}`",
                f"**Key Passes:** `{int(float(self.all_time_stats.get('keyPasses', 0)))}`",
                f"**Passes:** `{int(float(self.all_time_stats.get('passes', 0)))}`",
                f"**Passes Completed:** `{int(float(self.all_time_stats.get('passesCompleted', 0)))}`",
                f"**Corners:** `{int(float(self.all_time_stats.get('corners', 0)))}`",
                f"**Free Kicks:** `{int(float(self.all_time_stats.get('freeKicks', 0)))}`"
            ]
            
            # Calculate pass completion percentage
            passes = int(float(self.all_time_stats.get('passes', 0)))
            passes_completed = int(float(self.all_time_stats.get('passesCompleted', 0)))
            pass_rate = f"{passes_completed/passes:.1%}" if passes > 0 else "0%"
            playmaking_stats.append(f"**Pass Rate:** `{pass_rate}`")
            
            embed.add_field(name="🎯 **Playmaking**", value="\n".join(playmaking_stats), inline=True)
            
            # Defensive stats
            defensive_stats = [
                f"**Interceptions:** `{int(float(self.all_time_stats.get('interceptions', 0)))}`",
                f"**Tackles:** `{int(float(self.all_time_stats.get('slidingTacklesCompleted', 0)))}`",
                f"**Tackle Attempts:** `{int(float(self.all_time_stats.get('slidingTackles', 0)))}`",
                f"**Fouls:** `{int(float(self.all_time_stats.get('fouls', 0)))}`",
                f"**Fouls Suffered:** `{int(float(self.all_time_stats.get('foulsSuffered', 0)))}`",
                f"**Own Goals:** `{int(float(self.all_time_stats.get('ownGoals', 0)))}`"
            ]
            embed.add_field(name="🛡️ **Defensive**", value="\n".join(defensive_stats), inline=True)
            
            # Goalkeeper stats
            saves = int(float(self.all_time_stats.get('keeperSaves', 0)))
            goals_conceded = int(float(self.all_time_stats.get('goalsConceded', 0)))
            total_shots_faced = goals_conceded + saves
            save_rate = (saves / total_shots_faced * 100) if total_shots_faced > 0 else 0
            
            goalkeeper_stats = [
                f"**Saves:** `{saves}`",
                f"**Saves Caught:** `{int(float(self.all_time_stats.get('keeperSavesCaught', 0)))}`",
                f"**Goals Conceded:** `{goals_conceded}`",
                f"**Save Rate:** `{save_rate:.1f}%`"
            ]
            embed.add_field(name="🥅 **Goalkeeper**", value="\n".join(goalkeeper_stats), inline=True)
            
            # Discipline & Physical stats
            discipline_stats = [
                f"**Yellow Cards:** `{int(float(self.all_time_stats.get('yellowCards', 0)))}`",
                f"**Red Cards:** `{int(float(self.all_time_stats.get('redCards', 0)))}`",
                f"**Penalties:** `{int(float(self.all_time_stats.get('penalties', 0)))}`",
                f"**Distance Covered:** `{_format_distance_km(self.all_time_stats.get('distanceCovered', 0))}`",
                f"**Possession:** `{(int(float(self.all_time_stats.get('possession', 0))) / (self.total_appearances * 10)):.2f}%`"
            ]
            embed.add_field(name="📋 **Discipline & Physical**", value="\n".join(discipline_stats), inline=True)
            embed.add_field(name="🧠 **Derived Impact**", value=format_derived_metrics(self.all_time_stats), inline=True)
        else:
            embed.add_field(name="**Stats**", value="No competitive stats found.", inline=False)
        self._add_hub_field(embed)
            
        embed.set_footer(text="Page 1/3 - All-Time Stats • Use buttons to navigate")
        return embed


    async def create_mix_team_stats_embed(self):
        """Create the detailed mix team stats embed"""
        is_captain = self.mix_team_info and self.mix_team_info.get('captain_id') == self.user.id
        color = discord.Color.green() if is_captain else discord.Color.orange()
        
        embed = discord.Embed(
            title=f"🎯 {self.user.display_name} - Mix Team Stats",
            color=color
        )
        
        # Set team image as thumbnail if available
        if self.mix_team_info and self.mix_team_info.get('image_url'):
            embed.set_thumbnail(url=self.mix_team_info['image_url'])
        
        embed.set_author(name=self.user.display_name, icon_url=self.user.display_avatar.url)
        
        if self.mix_team_info:
            team_name = self.mix_team_info.get('name') or self.mix_team_info.get('guild_name', 'Unknown Team')
            is_vice_captain = self.user.id in (self.mix_team_info.get('vice_captain_ids') or [])
            if is_captain:
                role_text = " (CAPTAIN)"
            elif is_vice_captain:
                role_text = " (VICE-CAPTAIN)"
            else:
                role_text = ""
            embed.add_field(name="**Team**", value=f"`{team_name}`{role_text}", inline=True)
        else:
            embed.add_field(name="**Team**", value="FREE AGENT", inline=True)
            
        embed.add_field(name="**Position**", value=f"`{self.mix_team_position or 'N/A'}`", inline=True)
        embed.add_field(name="**Appearances**", value=f"`{self.mix_team_appearances}`", inline=True)
        
        # Add player rating as a non-inline field
        if self.player_rating is not None:
            embed.add_field(name="🌟 **Player Rating**", value=f"`{self.player_rating:.2f}/10.0`", inline=False)
        else:
            embed.add_field(name="🌟 **Player Rating**", value="`Not Available`", inline=False)
        
        if self.mix_team_stats and self.mix_team_appearances > 0:
            # Attacking stats
            attacking_stats = [
                f"**Goals:** `{int(float(self.mix_team_stats.get('goals', 0)))}`",
                f"**Assists:** `{int(float(self.mix_team_stats.get('assists', 0)))}`",
                f"**2nd Assists:** `{int(float(self.mix_team_stats.get('secondAssists', 0)))}`",
                f"**Shots:** `{int(float(self.mix_team_stats.get('shots', 0)))}`",
                f"**Shots on Goal:** `{int(float(self.mix_team_stats.get('shotsOnGoal', 0)))}`",
                f"**Offsides:** `{int(float(self.mix_team_stats.get('offsides', 0)))}`"
            ]
            embed.add_field(name="⚽ **Attacking**", value="\n".join(attacking_stats), inline=True)
            
            # Playmaking stats
            playmaking_stats = [
                f"**Chances Created:** `{int(float(self.mix_team_stats.get('chancesCreated', 0)))}`",
                f"**Key Passes:** `{int(float(self.mix_team_stats.get('keyPasses', 0)))}`",
                f"**Passes:** `{int(float(self.mix_team_stats.get('passes', 0)))}`",
                f"**Passes Completed:** `{int(float(self.mix_team_stats.get('passesCompleted', 0)))}`",
                f"**Corners:** `{int(float(self.mix_team_stats.get('corners', 0)))}`",
                f"**Free Kicks:** `{int(float(self.mix_team_stats.get('freeKicks', 0)))}`"
            ]
            
            # Calculate pass completion percentage
            passes = int(float(self.mix_team_stats.get('passes', 0)))
            passes_completed = int(float(self.mix_team_stats.get('passesCompleted', 0)))
            pass_rate = f"{passes_completed/passes:.1%}" if passes > 0 else "0%"
            playmaking_stats.append(f"**Pass Rate:** `{pass_rate}`")
            
            embed.add_field(name="🎯 **Playmaking**", value="\n".join(playmaking_stats), inline=True)
            
            # Defensive stats
            defensive_stats = [
                f"**Interceptions:** `{int(float(self.mix_team_stats.get('interceptions', 0)))}`",
                f"**Tackles:** `{int(float(self.mix_team_stats.get('slidingTacklesCompleted', 0)))}`",
                f"**Tackle Attempts:** `{int(float(self.mix_team_stats.get('slidingTackles', 0)))}`",
                f"**Fouls:** `{int(float(self.mix_team_stats.get('fouls', 0)))}`",
                f"**Fouls Suffered:** `{int(float(self.mix_team_stats.get('foulsSuffered', 0)))}`",
                f"**Own Goals:** `{int(float(self.mix_team_stats.get('ownGoals', 0)))}`"
            ]
            embed.add_field(name="🛡️ **Defensive**", value="\n".join(defensive_stats), inline=True)
            
            temp_saves = int(float(self.mix_team_stats.get('keeperSaves')))
            temp_caught = int(float(self.mix_team_stats.get('keeperSavesCaught')))
            temp_conceded = int(float(self.mix_team_stats.get('goalsConceded')))
            
            # Goalkeeper stats (if applicable)
            if temp_saves > 0 or temp_conceded > 0:
                save_rate = f"{temp_saves/(temp_saves + temp_conceded):.1%}" if (temp_saves + temp_conceded) > 0 else "0%"
                goalkeeper_stats = [
                    f"**Saves:** `{temp_saves}`",
                    f"**Saves Caught:** `{temp_caught}`",
                    f"**Goals Conceded:** `{temp_conceded}`",
                    f"**Save Rate:** `{save_rate}`"
                ]
                embed.add_field(name="🥅 **Goalkeeper**", value="\n".join(goalkeeper_stats), inline=True)
        self._add_hub_field(embed)
        embed.set_footer(text="Mix Team Stats • SteamID: {self.steam_id}")
        embed.timestamp = datetime.now(timezone.utc)
        
        return embed
        
    async def create_national_team_stats_embed(self):
        """Create the detailed national team stats embed"""
        is_captain = self.national_team_info and self.national_team_info.get('captain_id') == self.user.id
        color = discord.Color.red() if is_captain else discord.Color.dark_red()
        
        embed = discord.Embed(
            title=f"🏆 {self.user.display_name} - National Team Stats",
            color=color
        )
        
        # Set team image as thumbnail if available
        if self.national_team_info and self.national_team_info.get('image_url'):
            embed.set_thumbnail(url=self.national_team_info['image_url'])
        
        embed.set_author(name=self.user.display_name, icon_url=self.user.display_avatar.url)
        
        if self.national_team_info:
            team_name = self.national_team_info.get('name') or self.national_team_info.get('guild_name', 'Unknown Team')
            if is_captain:
                role_text = " (CAPTAIN)"
            else:
                role_text = ""
            embed.add_field(name="**Team**", value=f"`{team_name}`{role_text}", inline=True)
        else:
            embed.add_field(name="**Team**", value="FREE AGENT", inline=True)
            
        embed.add_field(name="**Position**", value=f"`{self.national_team_position or 'N/A'}`", inline=True)
        embed.add_field(name="**Appearances**", value=f"`{self.national_team_appearances}`", inline=True)
        
        # Add player rating as a non-inline field
        if self.player_rating is not None:
            embed.add_field(name="🌟 **Player Rating**", value=f"`{self.player_rating:.2f}/10.0`", inline=False)
        else:
            embed.add_field(name="🌟 **Player Rating**", value="`Not Available`", inline=False)
        
        if self.national_team_stats and self.national_team_appearances > 0:
            # Attacking stats
            attacking_stats = [
                f"**Goals:** `{int(float(self.national_team_stats.get('goals', 0)))}`",
                f"**Assists:** `{int(float(self.national_team_stats.get('assists', 0)))}`",
                f"**2nd Assists:** `{int(float(self.national_team_stats.get('secondAssists', 0)))}`",
                f"**Shots:** `{int(float(self.national_team_stats.get('shots', 0)))}`",
                f"**Shots on Goal:** `{int(float(self.national_team_stats.get('shotsOnGoal', 0)))}`",
                f"**Offsides:** `{int(float(self.national_team_stats.get('offsides', 0)))}`"
            ]
            embed.add_field(name="⚽ **Attacking**", value="\n".join(attacking_stats), inline=True)
            
            # Playmaking stats
            playmaking_stats = [
                f"**Chances Created:** `{int(float(self.national_team_stats.get('chancesCreated', 0)))}`",
                f"**Key Passes:** `{int(float(self.national_team_stats.get('keyPasses', 0)))}`",
                f"**Passes:** `{int(float(self.national_team_stats.get('passes', 0)))}`",
                f"**Passes Completed:** `{int(float(self.national_team_stats.get('passesCompleted', 0)))}`",
                f"**Corners:** `{int(float(self.national_team_stats.get('corners', 0)))}`",
                f"**Free Kicks:** `{int(float(self.national_team_stats.get('freeKicks', 0)))}`"
            ]
            
            # Calculate pass completion percentage
            passes = int(float(self.national_team_stats.get('passes', 0)))
            passes_completed = int(float(self.national_team_stats.get('passesCompleted', 0)))
            pass_rate = f"{passes_completed/passes:.1%}" if passes > 0 else "0%"
            playmaking_stats.append(f"**Pass Rate:** `{pass_rate}`")
            
            embed.add_field(name="🎯 **Playmaking**", value="\n".join(playmaking_stats), inline=True)
            
            # Defensive stats
            defensive_stats = [
                f"**Interceptions:** `{int(float(self.national_team_stats.get('interceptions', 0)))}`",
                f"**Tackles:** `{int(float(self.national_team_stats.get('slidingTacklesCompleted', 0)))}`",
                f"**Tackle Attempts:** `{int(float(self.national_team_stats.get('slidingTackles', 0)))}`",
                f"**Fouls:** `{int(float(self.national_team_stats.get('fouls', 0)))}`",
                f"**Fouls Suffered:** `{int(float(self.national_team_stats.get('foulsSuffered', 0)))}`",
                f"**Own Goals:** `{int(float(self.national_team_stats.get('ownGoals', 0)))}`"
            ]
            embed.add_field(name="🛡️ **Defensive**", value="\n".join(defensive_stats), inline=True)
            
            temp_saves = int(float(self.national_team_stats.get('keeperSaves')))
            temp_caught = int(float(self.national_team_stats.get('keeperSavesCaught')))
            temp_conceded = int(float(self.national_team_stats.get('goalsConceded')))
            
            # Goalkeeper stats (if applicable)
            if temp_saves > 0 or temp_conceded > 0:
                save_rate = f"{temp_saves/(temp_saves + temp_conceded):.1%}" if (temp_saves + temp_conceded) > 0 else "0%"
                goalkeeper_stats = [
                    f"**Saves:** `{temp_saves}`",
                    f"**Saves Caught:** `{temp_caught}`",
                    f"**Goals Conceded:** `{temp_conceded}`",
                    f"**Save Rate:** `{save_rate}`"
                ]
                embed.add_field(name="🥅 **Goalkeeper**", value="\n".join(goalkeeper_stats), inline=True)
        self._add_hub_field(embed)
        embed.set_footer(text="National Team Stats • SteamID: {self.steam_id}")
        embed.timestamp = datetime.now(timezone.utc)
        
        return embed
        
    async def create_weekly_breakdown_embed(self):
        """Create the weekly breakdown embed (Page 3) - placeholder for now"""
        is_captain_of_any_team = any(team.get('captain_id') == self.user.id for team in [self.club_team_info, self.national_team_info] if team)
        color = discord.Color.gold() if is_captain_of_any_team else discord.Color.blue()
        
        embed = discord.Embed(
            title=f"📅 {self.user.display_name} - Weekly Breakdown",
            color=color
        )
        
        embed.set_author(name=f"{self.user.display_name}", icon_url=self.user.display_avatar.url)
        embed.add_field(name="**Status**", value="Coming Soon", inline=False)
        embed.add_field(name="**Description**", value="Week by week performance analysis will be available here.", inline=False)
        self._add_hub_field(embed)
        embed.set_footer(text="Page 3/3 - Weekly Breakdown • Use buttons to navigate")
        return embed

    @discord.ui.button(label="◀️ Previous", style=discord.ButtonStyle.grey, disabled=True)
    async def previous_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        self.current_page -= 1
        await self.update_page(interaction)

    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.grey)
    async def next_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        self.current_page += 1
        await self.update_page(interaction)

    async def update_page(self, interaction: discord.Interaction):
        # Update button states
        self.previous_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page == 2)
        
        # Get the appropriate embed
        if self.current_page == 0:
            embed = await self.create_all_time_stats_embed()
        elif self.current_page == 1:
            embed = await self.create_club_stats_embed()
        else:  # self.current_page == 2
            embed = await self.create_weekly_breakdown_embed()
            
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        # Disable all buttons when the view times out
        for item in self.children:
            item.disabled = True
        
        # Try to delete the message gracefully
        try:
            if hasattr(self, 'message') and self.message:
                await self.message.delete()
        except discord.NotFound:
            # Message was already deleted
            pass
        except discord.Forbidden:
            # Bot doesn't have permission to delete the message
            # Just disable the buttons (already done above)
            pass
        except Exception as e:
            # Log any other errors but don't crash
            print(f"Error during timeout cleanup: {e}")

# Stats are now read from database instead of CSV

async def get_player_rating(steam_id):
    """Get player rating snapshot from IOSCA_PLAYERS by steam_id (cached)."""
    try:
        return await bot.db.players.get_player_rating_snapshot(steam_id)
    except Exception as e:
        print(f"Error reading player rating from database: {e}")
        return None

async def get_player_stats_from_db(steam_id):
    """Get player stats from database by steam_id."""
    try:
        # Query all player match data for this steam_id
        query = """
        WITH target_accounts AS (
            SELECT COALESCE(array_agg(DISTINCT sid), ARRAY[$1::text]) AS steam_ids
            FROM (
                SELECT ip.steam_id AS sid
                FROM IOSCA_PLAYERS ip
                WHERE ip.steam_id = $1
                   OR EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements_text(COALESCE(ip.linked_steam_ids, '[]'::jsonb)) AS linked(value)
                        WHERE linked.value = $1
                   )
                UNION ALL
                SELECT linked.value AS sid
                FROM IOSCA_PLAYERS ip
                JOIN LATERAL jsonb_array_elements_text(COALESCE(ip.linked_steam_ids, '[]'::jsonb)) AS linked(value) ON TRUE
                WHERE ip.steam_id = $1
                   OR EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements_text(COALESCE(ip.linked_steam_ids, '[]'::jsonb)) AS own_linked(value)
                        WHERE own_linked.value = $1
                   )
            ) scoped
        )
        SELECT 
            pmd.match_id,
            ms.datetime,
            pmd.steam_id,
            COALESCE(NULLIF(p.discord_name, ''), NULLIF(pmd.player_name, ''), pmd.steam_id) as name,
            CASE 
                WHEN pmd.guild_id = ms.home_guild_id THEN ht.guild_name
                WHEN pmd.guild_id = ms.away_guild_id THEN at.guild_name
                ELSE 'Unknown'
            END as team_name,
            CASE 
                WHEN pmd.guild_id = ms.home_guild_id THEN at.guild_name
                WHEN pmd.guild_id = ms.away_guild_id THEN ht.guild_name
                ELSE 'Unknown'
            END as opponent_team_name,
            CASE 
                WHEN pmd.guild_id = ms.home_guild_id THEN 'home'
                WHEN pmd.guild_id = ms.away_guild_id THEN 'away'
                ELSE 'unknown'
            END as team_side,
            pmd.position,
            pmd.passes_completed,
            pmd.passes_attempted,
            pmd.shots,
            pmd.shots_on_goal,
            pmd.offsides,
            pmd.corners,
            pmd.throw_ins,
            pmd.goal_kicks,
            pmd.own_goals,
            pmd.distance_covered,
            pmd.pass_accuracy,
            pmd.possession,
            pmd.free_kicks,
            pmd.penalties,
            pmd.goals,
            pmd.assists,
            pmd.second_assists,
            pmd.chances_created,
            pmd.key_passes,
            pmd.interceptions,
            pmd.tackles,
            pmd.sliding_tackles_completed,
            pmd.fouls,
            pmd.fouls_suffered,
            pmd.keeper_saves,
            pmd.keeper_saves_caught,
            pmd.goals_conceded,
            pmd.yellow_cards,
            pmd.red_cards,
            pmd.player_name,
            pmd.status,
            pmd.clutch_actions,
            pmd.sub_impact
        FROM PLAYER_MATCH_DATA pmd
        LEFT JOIN MATCH_STATS ms
          ON (
               pmd.match_id::text = ms.match_id::text
               OR (CASE WHEN pmd.match_id::text ~ '^[0-9]+$' THEN pmd.match_id::bigint END) = ms.id::bigint
          )
        LEFT JOIN LATERAL (
            SELECT ip.discord_name
            FROM IOSCA_PLAYERS ip
            WHERE ip.steam_id = pmd.steam_id
               OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(COALESCE(ip.linked_steam_ids, '[]'::jsonb)) AS linked(value)
                    WHERE linked.value = pmd.steam_id
               )
            ORDER BY CASE WHEN ip.steam_id = pmd.steam_id THEN 0 ELSE 1 END
            LIMIT 1
        ) p ON TRUE
        LEFT JOIN IOSCA_TEAMS ht ON ms.home_guild_id = ht.guild_id
        LEFT JOIN IOSCA_TEAMS at ON ms.away_guild_id = at.guild_id
        CROSS JOIN target_accounts ta
        WHERE pmd.steam_id = ANY(ta.steam_ids)
        ORDER BY ms.datetime DESC
        """
        
        rows = await bot.db.pool.fetch(query, steam_id)
        
        if not rows:
            return None, [], 0
        
        # Convert to list format matching old CSV structure
        header = ['match_id', 'datetime', 'Steam ID', 'Name', 'Team Name', 'Opponent Team Name', 
                  'Team Side', 'Position', 'passes_completed', 'passes_attempted', 'shots', 'shots_on_goal', 
                  'offsides', 'corners', 'throw_ins', 'goal_kicks', 'own_goals', 'distance_covered', 'pass_accuracy', 
                  'possession', 'free_kicks', 'penalties', 'goals', 'assists', 'second_assists', 'chances_created',
                  'key_passes', 'interceptions', 'tackles', 'sliding_tackles_completed', 'fouls', 'fouls_suffered',
                  'keeper_saves', 'keeper_saves_caught', 'goals_conceded', 'yellow_cards', 'red_cards',
                  'player_name', 'status', 'clutch_actions', 'sub_impact']
        
        player_rows = []
        unique_matches = set()

        for row in rows:
            # Build a dict per-row keyed by the header so callers can use row.get(...)
            values = [
                str(row['match_id']),
                row['datetime'].strftime('%Y-%m-%d %H:%M:%S') if row['datetime'] else '',
                row['steam_id'] or '',
                row['name'] or 'Unknown',
                row['team_name'] or 'Unknown',
                row['opponent_team_name'] or 'Unknown',
                row['team_side'] or 'unknown',
                row['position'] or '',
                str(row['passes_completed'] or 0),
                str(row['passes_attempted'] or 0),
                str(row['shots'] or 0),
                str(row['shots_on_goal'] or 0),
                str(row['offsides'] or 0),
                str(row['corners'] or 0),
                str(row['throw_ins'] or 0),
                str(row['goal_kicks'] or 0),
                str(row['own_goals'] or 0),
                str(row['distance_covered'] or 0),
                str(row['pass_accuracy'] or 0),
                str(row['possession'] or 0),
                str(row['free_kicks'] or 0),
                str(row['penalties'] or 0),
                str(row['goals'] or 0),
                str(row['assists'] or 0),
                str(row['second_assists'] or 0),
                str(row['chances_created'] or 0),
                str(row['key_passes'] or 0),
                str(row['interceptions'] or 0),
                str(row['tackles'] or 0),
                str(row['sliding_tackles_completed'] or 0),
                str(row['fouls'] or 0),
                str(row['fouls_suffered'] or 0),
                str(row['keeper_saves'] or 0),
                str(row['keeper_saves_caught'] or 0),
                str(row['goals_conceded'] or 0),
                str(row['yellow_cards'] or 0),
                str(row['red_cards'] or 0),
                row.get('player_name') or '',
                row.get('status') or '',
                json.dumps(row.get('clutch_actions') or []),
                json.dumps(row.get('sub_impact') or {})
            ]

            # Map header -> value so existing code using row.get(...) works
            row_dict = {header[i]: values[i] for i in range(min(len(header), len(values)))}

            # Add camelCase aliases expected by the stats display/aggregation code
            alias_map = {
                "passes_completed": "passesCompleted",
                "passes_attempted": "passes",
                "shots_on_goal": "shotsOnGoal",
                "second_assists": "secondAssists",
                "chances_created": "chancesCreated",
                "key_passes": "keyPasses",
                "sliding_tackles_completed": "slidingTacklesCompleted",
                "fouls_suffered": "foulsSuffered",
                "keeper_saves": "keeperSaves",
                "keeper_saves_caught": "keeperSavesCaught",
                "goals_conceded": "goalsConceded",
                "yellow_cards": "yellowCards",
                "red_cards": "redCards",
                "own_goals": "ownGoals",
                "distance_covered": "distanceCovered",
                "free_kicks": "freeKicks",
                "goal_kicks": "goalKicks",
                "pass_accuracy": "passAccuracy",
                "tackles": "slidingTackles",
                "throw_ins": "throwIns",
                "player_name": "playerName",
                "clutch_actions": "clutchActions",
                "sub_impact": "subImpact"
            }
            for source_key, dest_key in alias_map.items():
                if source_key in row_dict and dest_key not in row_dict:
                    row_dict[dest_key] = row_dict[source_key]
            player_rows.append(row_dict)

            # Track unique match ids (use original value for accuracy)
            unique_matches.add(row['match_id'])
        
        return header, player_rows, len(unique_matches)
        
    except Exception as e:
        print(f"Error reading player stats from database: {e}")
        return None, [], 0

def calculate_all_time_stats(player_stats_rows):
    """Aggregates all player stats from provided rows and determines the most played position."""
    if not player_stats_rows:
        return None, None

    # Define numeric stat fields to be summed
    numeric_fields = [
        'Matches Played', 'goals', 'assists', 'secondAssists', 'offsides',
        'chancesCreated', 'keyPasses', 'interceptions', 'slidingTacklesCompleted',
        'fouls', 'ownGoals', 'passesCompleted', 'passes', 'keeperSaves',
        'keeperSavesCaught', 'goalsConceded', 'corners', 'distanceCovered',
        'foulsSuffered', 'freeKicks', 'goalKicks', 'penalties', 'possession',
        'redCards', 'shots', 'shotsOnGoal', 'slidingTackles', 'yellowCards'
    ]
    
    # Initialize aggregated stats
    aggregated_stats = {field: 0 for field in numeric_fields}
    aggregated_stats.update({
        "starts": 0,
        "substituteApps": 0,
        "benchApps": 0,
        "clutchActionEvents": 0,
        "subImpactEvents": 0,
        "subImpactGoals": 0,
        "subImpactOwnGoals": 0,
    })
    position_counter = Counter()

    def _parse_json_value(raw_value, fallback):
        if raw_value is None:
            return fallback
        if isinstance(raw_value, (dict, list)):
            return raw_value
        if isinstance(raw_value, str):
            try:
                return json.loads(raw_value)
            except Exception:
                return fallback
        return fallback

    for row in player_stats_rows:
        for field in numeric_fields:
            try:
                # Get value, default to '0', convert to float then int to handle "X.0" cases
                aggregated_stats[field] += int(float(row.get(field, '0')))
            except (ValueError, TypeError):
                continue # Skip if value is not a valid number

        status_value = str(row.get("status") or "").lower()
        if status_value == "started":
            aggregated_stats["starts"] += 1
        elif status_value == "substitute":
            aggregated_stats["substituteApps"] += 1
        elif status_value == "on_bench":
            aggregated_stats["benchApps"] += 1

        clutch_actions = _parse_json_value(row.get("clutchActions", row.get("clutch_actions")), [])
        if isinstance(clutch_actions, list):
            aggregated_stats["clutchActionEvents"] += len(clutch_actions)

        sub_impact = _parse_json_value(row.get("subImpact", row.get("sub_impact")), {})
        if isinstance(sub_impact, dict):
            events = sub_impact.get("events")
            if isinstance(events, list):
                aggregated_stats["subImpactEvents"] += len(events)
            summary = sub_impact.get("summary")
            if isinstance(summary, dict):
                try:
                    aggregated_stats["subImpactGoals"] += int(float(summary.get("goals", 0) or 0))
                    aggregated_stats["subImpactOwnGoals"] += int(float(summary.get("own_goals", 0) or 0))
                except (ValueError, TypeError):
                    pass
        
        position = row.get('Position')
        if position and str(position).lower() not in ['nan', 'n/a', 'null', 'none', '']:
            position_counter[position] += 1

    # Determine the most common position
    most_common_position = position_counter.most_common(1)[0][0] if position_counter else "N/A"
    
    return most_common_position, aggregated_stats

def calculate_team_specific_stats(player_stats_rows, team_name):
    """Aggregates player stats from provided rows for a specific team and counts appearances."""
    if not player_stats_rows or not team_name:
        return None, None, 0

    # Filter rows for the specific team
    team_rows = [row for row in player_stats_rows if row.get('Team Name') == team_name]
    
    if not team_rows:
        return None, None, 0

    # Define numeric stat fields to be summed
    numeric_fields = [
        'goals', 'assists', 'secondAssists', 'offsides',
        'chancesCreated', 'keyPasses', 'interceptions', 'slidingTacklesCompleted',
        'fouls', 'ownGoals', 'passesCompleted', 'passes', 'keeperSaves',
        'keeperSavesCaught', 'goalsConceded', 'corners', 'distanceCovered',
        'foulsSuffered', 'freeKicks', 'goalKicks', 'penalties', 'possession',
        'redCards', 'shots', 'shotsOnGoal', 'slidingTackles', 'yellowCards'
    ]
    
    # Initialize aggregated stats
    aggregated_stats = {field: 0 for field in numeric_fields}
    aggregated_stats.update({
        "starts": 0,
        "substituteApps": 0,
        "benchApps": 0,
        "clutchActionEvents": 0,
        "subImpactEvents": 0,
        "subImpactGoals": 0,
        "subImpactOwnGoals": 0,
    })
    position_counter = Counter()
    unique_matches = set()

    def _parse_json_value(raw_value, fallback):
        if raw_value is None:
            return fallback
        if isinstance(raw_value, (dict, list)):
            return raw_value
        if isinstance(raw_value, str):
            try:
                return json.loads(raw_value)
            except Exception:
                return fallback
        return fallback

    for row in team_rows:
        # Count unique matches for appearances
        unique_matches.add(row.get('match_id', ''))
        
        for field in numeric_fields:
            try:
                # Get value, default to '0', convert to float then int to handle "X.0" cases
                aggregated_stats[field] += int(float(row.get(field, '0')))
            except (ValueError, TypeError):
                continue # Skip if value is not a valid number

        status_value = str(row.get("status") or "").lower()
        if status_value == "started":
            aggregated_stats["starts"] += 1
        elif status_value == "substitute":
            aggregated_stats["substituteApps"] += 1
        elif status_value == "on_bench":
            aggregated_stats["benchApps"] += 1

        clutch_actions = _parse_json_value(row.get("clutchActions", row.get("clutch_actions")), [])
        if isinstance(clutch_actions, list):
            aggregated_stats["clutchActionEvents"] += len(clutch_actions)

        sub_impact = _parse_json_value(row.get("subImpact", row.get("sub_impact")), {})
        if isinstance(sub_impact, dict):
            events = sub_impact.get("events")
            if isinstance(events, list):
                aggregated_stats["subImpactEvents"] += len(events)
            summary = sub_impact.get("summary")
            if isinstance(summary, dict):
                try:
                    aggregated_stats["subImpactGoals"] += int(float(summary.get("goals", 0) or 0))
                    aggregated_stats["subImpactOwnGoals"] += int(float(summary.get("own_goals", 0) or 0))
                except (ValueError, TypeError):
                    pass
        
        position = row.get('Position')
        if position and str(position).lower() not in ['nan', 'n/a', 'null', 'none', '']:
            position_counter[position] += 1

    # Determine the most common position for this team
    most_common_position = position_counter.most_common(1)[0][0] if position_counter else "N/A"
    
    # Count of unique matches = appearances for this team
    team_appearances = len(unique_matches)
    
    return most_common_position, aggregated_stats, team_appearances

def format_stats(position, stats_row, appearances):
    """Formats the stats string based on the player's position."""
    appearances_str = f"**Appearances:** `{appearances}`"
    
    # Helper to safely get and format stats
    def get_stat(key):
        return int(float(stats_row.get(key, '0')))

    # Calculate pass completion
    passes_completed = get_stat('passesCompleted')
    total_passes = get_stat('passes')
    pass_completion_str = f"`{passes_completed / total_passes:.2%}`" if total_passes > 0 else "`0%`"
    
    pos_lower = position.upper()
    
    if pos_lower in {'LW', 'CF', 'RW'}:
        stats_list = [
            f"**Goals:** `{get_stat('goals')}`",
            f"**Assists:** `{get_stat('assists')}`",
            f"**2nd Assists:** `{get_stat('secondAssists')}`",
            f"**Offsides:** `{get_stat('offsides')}`"
        ]
    elif pos_lower == 'CM':
        stats_list = [
            f"**Goals:** `{get_stat('goals')}`",
            f"**Assists:** `{get_stat('assists')}`",
            f"**2nd Assists:** `{get_stat('secondAssists')}`",
            f"**Chances Created:** `{get_stat('chancesCreated')}`",
            f"**Key Passes:** `{get_stat('keyPasses')}`"
        ]
    elif pos_lower in {'LB', 'CB', 'RB'}:
        stats_list = [
            f"**Interceptions:** `{get_stat('interceptions')}`",
            f"**Tackles:** `{get_stat('slidingTacklesCompleted')}`",
            f"**Fouls:** `{get_stat('fouls')}`",
            f"**Own Goals:** `{get_stat('ownGoals')}`",
            f"**Pass %:** {pass_completion_str}"
        ]
    elif pos_lower == 'GK':
        stats_list = [
            f"**Saves:** `{get_stat('keeperSaves')}`",
            f"**Saves Caught:** `{get_stat('keeperSavesCaught')}`",
            f"**Goals Conceded:** `{get_stat('goalsConceded')}`",
            f"**Pass %:** {pass_completion_str}"
        ]
    else:
        return "No stats available for this position."
        
    return f"{appearances_str}\n" + "\n".join(stats_list)


def format_derived_metrics(stats_row):
    """Format parser-derived metrics for embed presentation."""
    if not stats_row:
        return "No derived metrics available."

    starts = int(float(stats_row.get("starts", 0) or 0))
    subs = int(float(stats_row.get("substituteApps", 0) or 0))
    bench = int(float(stats_row.get("benchApps", 0) or 0))
    clutch_events = int(float(stats_row.get("clutchActionEvents", 0) or 0))
    sub_impact_events = int(float(stats_row.get("subImpactEvents", 0) or 0))
    sub_goals = int(float(stats_row.get("subImpactGoals", 0) or 0))
    sub_own_goals = int(float(stats_row.get("subImpactOwnGoals", 0) or 0))

    return "\n".join([
        f"**Status (S/Sub/B):** `{starts}/{subs}/{bench}`",
        f"**Clutch Actions:** `{clutch_events}`",
        f"**Sub Impact Events:** `{sub_impact_events}`",
        f"**Sub Impact (G/OG):** `{sub_goals}/{sub_own_goals}`",
    ])

@bot.slash_command(name="view_player", description="View a player's stats and teams.")
async def view_player(interaction: discord.Interaction, user: discord.Member):
    """Shows a player's teams and stats."""
    await view_player_logic(interaction, user)


@bot.message_command(name="View Player", name_localizations={"en-US": "View Player", "es-ES": "Ver jugador"})
async def view_player_message(ctx, message: discord.Message):
    """Context menu (right-click message) -> view the player who posted the message."""
    # Use the message author as the target user
    if not message or not message.author:
        await ctx.respond("Could not determine message author.", ephemeral=True)
        return

    await view_player_logic(ctx, message.author)

async def view_player_logic(interaction: discord.Interaction, user: discord.Member):
    """Shows a player's teams and stats."""
    await interaction.response.defer()

    try:
        # 1. Get Player's SteamID from our own DB
        player_record = await bot.db.players.get_player_by_discord_id(str(user.id))
        if not player_record or not player_record.get('steam_id'):
            await interaction.followup.send(
                f"{user.mention} has not registered their SteamID. They can do so using `/player_register`.",
                ephemeral=True
            )
            return

        steam_id = player_record['steam_id']

        # 2. Get Player's Stats from Database, Teams, and Rating
        header, player_stats_rows, total_appearances = await get_player_stats_from_db(steam_id)
        player_teams = await bot.db.teams.get_player_teams(str(user.id))
        player_rating_snapshot = await get_player_rating(steam_id)
        player_rating = player_rating_snapshot.get("rating") if isinstance(player_rating_snapshot, dict) else None
        profile_steam_id = player_rating_snapshot.get("steam_id") if isinstance(player_rating_snapshot, dict) else None
        if not profile_steam_id:
            profile_steam_id = steam_id
    except Exception as e:
        print(f"view_player DB error: {e!r}")
        await interaction.followup.send(
            "Database is temporarily unavailable. Please try again in a minute.",
            ephemeral=True
        )
        return

    # Exit if player has no teams and no stats to show
    if not player_teams and not player_stats_rows:
        await interaction.followup.send(
            f"No teams or match stats found for {user.mention} (SteamID: `{steam_id}`).",
            ephemeral=True
        )
        return
        
    if not header:
        await interaction.followup.send(
            "The stats file seems to be missing or corrupted. Please contact an admin.",
            ephemeral=True
        )
        return

    club_team_info = next((team for team in player_teams if not team['is_national_team'] and not team['is_mix_team']), None)
    national_team_info = next((team for team in player_teams if team['is_national_team']), None)
    mix_team_info = next((team for team in player_teams if team['is_mix_team']), None)

    # Get team-specific stats for club team
    club_team_position, club_team_stats, club_team_appearances = None, None, 0
    if club_team_info:
        club_team_position, club_team_stats, club_team_appearances = calculate_team_specific_stats(
            player_stats_rows, 
            club_team_info['name']
        )
        
    # Get team-specific stats for national team
    national_team_position, national_team_stats, national_team_appearances = None, None, 0
    if national_team_info:
        national_team_position, national_team_stats, national_team_appearances = calculate_team_specific_stats(
            player_stats_rows, 
            national_team_info['name']
        )
        
    # Get team-specific stats for mix team
    mix_team_position, mix_team_stats, mix_team_appearances = None, None, 0
    if mix_team_info:
        mix_team_position, mix_team_stats, mix_team_appearances = calculate_team_specific_stats(
            player_stats_rows, 
            mix_team_info['name']
        )
        
    # Get all-time stats
    all_time_pos, all_time_stats = None, None
    if player_stats_rows:
        all_time_pos, all_time_stats = calculate_all_time_stats(player_stats_rows)
        
    # Create the paginated view
    view = PlayerStatsView(
        user=user,
        club_team_info=club_team_info,
        national_team_info=national_team_info,
        mix_team_info=mix_team_info,
        club_team_position=club_team_position,
        club_team_stats=club_team_stats,
        club_team_appearances=club_team_appearances,
        national_team_position=national_team_position,
        national_team_stats=national_team_stats,
        national_team_appearances=national_team_appearances,
        mix_team_position=mix_team_position,
        mix_team_stats=mix_team_stats,
        mix_team_appearances=mix_team_appearances,
        all_time_pos=all_time_pos,
        all_time_stats=all_time_stats,
        total_appearances=total_appearances,
        player_rating=player_rating,
        steam_id=profile_steam_id
    )
    
    # Start with the first page (All-Time Stats)
    embed = await view.create_all_time_stats_embed()
    message = await interaction.followup.send(embed=embed, view=view)
    
    # Store the message reference in the view for timeout cleanup
    view.message = message

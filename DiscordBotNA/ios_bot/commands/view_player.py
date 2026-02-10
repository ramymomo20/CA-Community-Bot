from ios_bot.config import *
import os
import io
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO
from collections import Counter
import tempfile
from datetime import datetime, timezone

class PlayerStatsView(discord.ui.View):
    def __init__(self, user, club_team_info, national_team_info, mix_team_info, club_team_position, club_team_stats, club_team_appearances, national_team_position, national_team_stats, national_team_appearances, mix_team_position, mix_team_stats, mix_team_appearances, all_time_pos, all_time_stats, total_appearances, card_path, player_rating, steam_id):
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
        self.card_path = card_path
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
            is_vice_captain = self.club_team_info.get('vice_captain_id') == self.user.id
            if is_captain:
                role_text = " (CAPTAIN)"
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
                f"**Distance Covered:** `{int(float(self.club_team_stats.get('distanceCovered', 0)))} meters`",
                f"**Possession:** `{(int(float(self.club_team_stats.get('possession'))) / (self.club_team_appearances * 10)):.2f}%`"
            ]
            embed.add_field(name="📋 **Discipline & Physical**", value="\n".join(discipline_stats), inline=True)
        else:
            embed.add_field(name="**Stats**", value="No competitive stats found for this team.", inline=False)
            
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
                f"**Distance Covered:** `{int(float(self.all_time_stats.get('distanceCovered', 0)))} meters`",
                f"**Possession:** `{(int(float(self.all_time_stats.get('possession', 0))) / (self.total_appearances * 10)):.2f}%`"
            ]
            embed.add_field(name="📋 **Discipline & Physical**", value="\n".join(discipline_stats), inline=True)
        else:
            embed.add_field(name="**Stats**", value="No competitive stats found.", inline=False)
            
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
            is_vice_captain = self.mix_team_info.get('vice_captain_id') == self.user.id
            if is_captain:
                role_text = " (CAPTAIN)"
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
        embed.set_footer(text="Page 3/3 - Weekly Breakdown • Use buttons to navigate")
        return embed

    @discord.ui.button(label="◀️ Previous", style=discord.ButtonStyle.grey, disabled=True)
    async def previous_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        self.current_page -= 1
        await self.update_page(interaction)

    @discord.ui.button(label="🖼️ Player Card", style=discord.ButtonStyle.primary)
    async def player_card_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.card_path and os.path.exists(self.card_path):
            try:
                with open(self.card_path, 'rb') as f:
                    file = discord.File(f, filename='player_card.png')
                    await interaction.response.send_message(file=file, ephemeral=True)
            except Exception as e:
                print(f"Error sending player card: {e}")
                await interaction.response.send_message("Failed to load player card image.", ephemeral=True)
        else:
            await interaction.response.send_message("Player card image not available.", ephemeral=True)

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
            # Clean up the temporary card file if it exists
            if self.card_path and os.path.exists(self.card_path):
                try:
                    os.unlink(self.card_path)
                except:
                    pass  # Ignore file deletion errors
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
BRONZE_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), '..', 'ratings', 'player card', 'bronze.png')
SILVER_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), '..', 'ratings', 'player card', 'silver.png')
GOLD_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), '..', 'ratings', 'player card', 'gold.png')

async def get_player_rating(steam_id):
    """Get player rating from IOSCA_PLAYERS.rating by steam_id."""
    try:
        row = await bot.db.pool.fetchrow(
            "SELECT rating FROM IOSCA_PLAYERS WHERE steam_id = $1",
            steam_id
        )
        if row and row.get("rating") is not None:
            return float(row["rating"])
        return None
    except Exception as e:
        print(f"Error reading player rating from database: {e}")
        return None

async def get_player_stats_from_db(steam_id):
    """Get player stats from database by steam_id."""
    try:
        # Query all player match data for this steam_id
        query = """
        SELECT 
            pmd.match_id,
            ms.datetime,
            pmd.steam_id,
            p.discord_name as name,
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
            pmd.red_cards
        FROM PLAYER_MATCH_DATA pmd
        LEFT JOIN MATCH_STATS ms ON pmd.match_id = ms.match_id
        LEFT JOIN IOSCA_PLAYERS p ON pmd.steam_id = p.steam_id
        LEFT JOIN IOSCA_TEAMS ht ON ms.home_guild_id = ht.guild_id
        LEFT JOIN IOSCA_TEAMS at ON ms.away_guild_id = at.guild_id
        WHERE pmd.steam_id = $1
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
                  'keeper_saves', 'keeper_saves_caught', 'goals_conceded', 'yellow_cards', 'red_cards']
        
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
                str(row['red_cards'] or 0)
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
                "throw_ins": "throwIns"
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
    position_counter = Counter()

    for row in player_stats_rows:
        for field in numeric_fields:
            try:
                # Get value, default to '0', convert to float then int to handle "X.0" cases
                aggregated_stats[field] += int(float(row.get(field, '0')))
            except (ValueError, TypeError):
                continue # Skip if value is not a valid number
        
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
    position_counter = Counter()
    unique_matches = set()

    for row in team_rows:
        # Count unique matches for appearances
        unique_matches.add(row.get('match_id', ''))
        
        for field in numeric_fields:
            try:
                # Get value, default to '0', convert to float then int to handle "X.0" cases
                aggregated_stats[field] += int(float(row.get(field, '0')))
            except (ValueError, TypeError):
                continue # Skip if value is not a valid number
        
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

async def download_image(url, size=(100, 100)):
    """Download an image from URL and resize it."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        img = Image.open(io.BytesIO(response.content))
        img = img.convert('RGBA')
        img = img.resize(size, Image.Resampling.LANCZOS)
        return img
    except Exception as e:
        print(f"Error downloading image from {url}: {e}")
        return None

async def generate_player_card(user, club_team_info, national_team_info, team_position, team_stats, team_appearances, player_rating=None):
    """Generate a player card image with dynamic data."""
    try:
        # Load the template image and determine rating display
        
        # Use actual player rating from the database if available
        if player_rating is not None:
            # Convert from rating scale to display scale for the card
            # Scale the rating: multiply by 10 for display (5.0 -> 50, 10.0 -> 100)
            display_rating = player_rating * 10
            rating = f"{display_rating:.1f}"
            
            # Choose template based on rating - cover all possible values
            if display_rating >= 80:
                CARD_TEMPLATE_PATH = GOLD_TEMPLATE_PATH
            elif display_rating >= 60:
                CARD_TEMPLATE_PATH = SILVER_TEMPLATE_PATH
            else:
                CARD_TEMPLATE_PATH = BRONZE_TEMPLATE_PATH
        else:
            rating = "0"  # Default rating if no data available
            CARD_TEMPLATE_PATH = BRONZE_TEMPLATE_PATH
            
        if not os.path.exists(CARD_TEMPLATE_PATH):
            print(f"Template image not found at: {CARD_TEMPLATE_PATH}")
            return None
            
        template = Image.open(CARD_TEMPLATE_PATH).convert('RGBA')
        draw = ImageDraw.Draw(template)
        
        # Get template dimensions
        template_width, template_height = template.size
        
        # Fallback to default fonts
        title_font = ImageFont.load_default(size=32)
        name_font = ImageFont.load_default(size=28)
        text_font = ImageFont.load_default(size=32)
        small_font = ImageFont.load_default(size=22)
        tiny_font = ImageFont.load_default(size=24)
        
        # Try to load bold font for Steam ID
        try:
            bold_font = ImageFont.truetype("arialbd.ttf", 28)  # Bold Arial
        except:
            bold_font = name_font  # Fallback to regular font
        

        
        # Download and place user avatar - positioned to completely cover the square
        avatar_size = 165  # Keep size the same
        avatar_img = await download_image(user.display_avatar.url, (avatar_size, avatar_size))
        if avatar_img:
            # Create circular mask for avatar
            mask = Image.new('L', (avatar_size, avatar_size), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, avatar_size, avatar_size), fill=400)
            
            # Apply mask to make avatar circular
            avatar_img.putalpha(mask)
            
            # Lower the avatar to completely cover the yellow square
            avatar_x = (template_width - avatar_size) // 2  # Center horizontally
            avatar_y = 70  # Lowered from 50 to completely cover the square
            
            # Create white outline for avatar
            outline_size = avatar_size + 6  # Slightly larger for outline
            outline_x = avatar_x - 3  # Offset to center the outline
            outline_y = avatar_y - 3  # Offset to center the outline
            
            # Draw white circle outline
            draw.ellipse([outline_x, outline_y, outline_x + outline_size, outline_y + outline_size], 
                        outline='white', width=3)
            
            template.paste(avatar_img, (avatar_x, avatar_y), avatar_img)
        
        # Adjusted coordinates - moved text more inward and repositioned
        # Position (top-left area) - moved more inward
        position_coords = (80, 145)
        
        # Rating (top-right area) - moved more inward
        rating_coords = (template_width - 80, 145)
        
        # Team name (bottom-left area) - moved more inward
        team_coords = (80, 285)
        
        # National team (bottom-right area) - moved more inward 
        national_team_coords = (template_width - 50, 285)
        
        # Steam ID (bottom center) - moved up slightly
        steamid_coords = (template_width // 2, template_height - 110)
        
        # Draw position with better styling
        draw.text(position_coords, team_position, fill='#000000', font=text_font, anchor='lm')
        
        # Draw rating with emphasis
        draw.text(rating_coords, rating, fill='#000000', font=title_font, anchor='rm')
        
        # Draw team name
        team_name = club_team_info['name'] if club_team_info else "No Team"
        draw.text(team_coords, team_name, fill='#000000', font=small_font, anchor='lm')
        
        # Add club team image below team name if available
        if club_team_info and club_team_info.get('image_url'):
            try:
                team_img_size = 60  # Size for team logo
                club_img = await download_image(club_team_info['image_url'], (team_img_size, team_img_size))
                if club_img:
                    # Calculate text width to center the image below team name
                    text_bbox = draw.textbbox((0, 0), team_name, font=small_font)
                    text_width = text_bbox[2] - text_bbox[0]
                    
                    # Center the team image below the team name text
                    club_img_x = team_coords[0] + (text_width // 2) - (team_img_size // 2)
                    club_img_y = team_coords[1] + 20  # Below team name
                    template.paste(club_img, (club_img_x, club_img_y), club_img)
            except Exception as e:
                print(f"Error loading club team image: {e}")
        
        # Draw national team
        national_name = national_team_info['name'] if national_team_info else "No National Team"
        draw.text(national_team_coords, national_name, fill='#000000', font=small_font, anchor='rm')
        
        # Add national team image below national team name if available
        if national_team_info and national_team_info.get('image_url'):
            try:
                team_img_size = 60  # Size for team logo
                national_img = await download_image(national_team_info['image_url'], (team_img_size, team_img_size))
                if national_img:
                    # Calculate text width to center the image below national team name
                    text_bbox = draw.textbbox((0, 0), national_name, font=small_font)
                    text_width = text_bbox[2] - text_bbox[0]
                    
                    # Center the team image below the national team name text (accounting for right-alignment)
                    national_img_x = national_team_coords[0] - (text_width // 2) - (team_img_size // 2)
                    national_img_y = national_team_coords[1] + 20  # Below team name
                    template.paste(national_img, (national_img_x, national_img_y), national_img)
            except Exception as e:
                print(f"Error loading national team image: {e}")
        
        # Draw Steam ID/Username at bottom (bold and uppercase)
        steam_display = user.name.upper()  # Convert to uppercase
        draw.text(steamid_coords, steam_display, fill='#000000', font=bold_font, anchor='mm')
        
        # Save to temporary file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
        template.save(temp_file.name, 'PNG')
        temp_file.close()
        
        return temp_file.name
        
    except Exception as e:
        print(f"Error generating player card: {e}")
        return None

@bot.slash_command(name="view_player", description="View a player's stats and teams.")
async def view_player(interaction: discord.Interaction, user: discord.Member):
    """Shows a player card with their teams and stats."""
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
    """Shows a player card with their teams and stats."""
    await interaction.response.defer()

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
    player_rating = await get_player_rating(steam_id)

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
        
    # Use club team stats as primary, fallback to national team, then all-time
    primary_position = club_team_position or national_team_position or all_time_pos
    primary_stats = club_team_stats or national_team_stats or all_time_stats
    primary_appearances = club_team_appearances or national_team_appearances or total_appearances

    # Generate the player card image
    card_path = await generate_player_card(
        user, 
        club_team_info, 
        national_team_info, 
        primary_position, 
        primary_stats, 
        primary_appearances,
        player_rating
    )
    
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
        card_path=card_path,
        player_rating=player_rating,
        steam_id=steam_id
    )
    
    # Start with the first page (All-Time Stats)
    embed = await view.create_all_time_stats_embed()
    message = await interaction.followup.send(embed=embed, view=view)
    
    # Store the message reference in the view for timeout cleanup
    view.message = message

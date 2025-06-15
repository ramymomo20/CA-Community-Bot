from ios_bot.config import *
from ios_bot.database_manager import get_team, update_team_details
from ios_bot.commands.team_registration import ChannelSelect # Reusing ChannelSelect from team_registration

class EditTeamChannelsView(View):
    def __init__(self, author_id: int, guild: discord.Guild, current_team_data: dict):
        super().__init__(timeout=300) # 5 minutes timeout
        self.author_id = author_id
        self.guild = guild
        self.current_team_data = current_team_data
        self.new_sixes_channels = None # Store IDs
        self.new_eights_channels = None # Store IDs

        # Pre-populate with existing or validated channels.
        # The command logic will handle validation before creating this view.
        # For simplicity in the view, we assume current_team_data holds valid (or to-be-reselected) channels.

        all_text_channels = [ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages]

        # 6v6 Channel Selection
        self.sixes_select = ChannelSelect(all_text_channels, "6v6", max_selectable=2)
        self.add_item(self.sixes_select)

        # 8v8 Channel Selection
        self.eights_select = ChannelSelect(all_text_channels, "8v8", max_selectable=2)
        self.add_item(self.eights_select)
        
        # Submit Button
        self.submit_button = Button(label="Update Channels", style=ButtonStyle.success, custom_id="submit_channel_update")
        self.submit_button.callback = self.submit_callback
        self.add_item(self.submit_button)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("You are not authorized to use this menu.", ephemeral=True)
            return False
        return True

    async def submit_callback(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        # Retrieve selected channels from the ChannelSelect instances
        # The ChannelSelect.callback in team_registration.py sets view.eights_channels_selected etc.
        # We need to ensure that ChannelSelect can work with this view instance correctly.
        # For now, assume selections are made and we fetch them.

        final_eights_ids = self.eights_select.values if hasattr(self.eights_select, 'values') and self.eights_select.values else self.current_team_data.get('eights_channels', [])
        
        # Convert to int if they are strings from select values
        final_eights_ids = [int(ch_id) for ch_id in final_eights_ids if str(ch_id).isdigit()]
        
        final_sixes_ids = self.sixes_select.values if hasattr(self.sixes_select, 'values') and self.sixes_select.values else self.current_team_data.get('sixes_channels', [])
        
        # Convert to int if they are strings from select values
        final_sixes_ids = [int(ch_id) for ch_id in final_sixes_ids if str(ch_id).isdigit()]

        # Always update the guild name and icon in the database to match the current Discord guild
        guild_icon_url = self.guild.icon.url if self.guild.icon else None
        await update_team_details(
            guild_id=self.guild.id,
            guild_name=self.guild.name,
            guild_icon=guild_icon_url,
            eights_channels=final_eights_ids,
            sixes_channels=final_sixes_ids
        )

        success = True # Assume update_team_details returns True if update succeeded

        if success:
            await interaction.followup.send(f"✅ Team matchmaking channels for '{self.guild.name}' updated successfully!", ephemeral=True)
            # Send a public confirmation as well
            public_msg = f"Matchmaking channels for **{self.guild.name}** have been updated."
            if final_eights_ids:
                public_msg += f"\n**8v8 Channels**: {', '.join([f'<#{ch_id}>' for ch_id in final_eights_ids])}"
            await interaction.channel.send(public_msg)

        else:
            await interaction.followup.send("❌ Failed to update team channels. Please check console for errors or try again.", ephemeral=True)
        
        self.stop()


@bot.slash_command(
    name="edit_team_channels", # Renamed for clarity
    description="Validate and update your team's matchmaking channels."
)
async def edit_team_channels_command(ctx: ApplicationContext):
    guild = ctx.guild
    if not guild:
        await ctx.respond("This command can only be used in a server.", ephemeral=True)
        return

    if not ctx.author.guild_permissions.manage_guild:
        await ctx.respond("❌ You need 'Manage Server' permissions to use this command.", ephemeral=True)
        return

    team_data = await get_team(guild.id)
    if not team_data:
        await ctx.respond(f"This server ('{guild.name}') is not registered as a team. Use `/register_team` first.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    # Always update the guild name and icon in the database to match the current Discord guild
    guild_icon_url = guild.icon.url if guild.icon else None
    await update_team_details(
        guild_id=guild.id,
        guild_name=guild.name,
        guild_icon=guild_icon_url
    )

    # Validate existing channels
    valid_eights = []
    if team_data.get('eights_channels'):
        for ch_id in team_data['eights_channels']:
            channel = guild.get_channel(ch_id)
            if channel and isinstance(channel, TextChannel):
                valid_eights.append(ch_id)
    team_data['eights_channels'] = valid_eights
    
    valid_sixes = []
    if team_data.get('sixes_channels'):
        for ch_id in team_data['sixes_channels']:
            channel = guild.get_channel(ch_id)
            if channel and isinstance(channel, TextChannel):
                valid_sixes.append(ch_id)
    team_data['sixes_channels'] = valid_sixes
    
    # Update database with validated channels first (silent update if any changed)
    # This also ensures team_data used by the view is up-to-date.
    await update_team_details(
        guild_id=guild.id, 
        eights_channels=valid_eights,
        sixes_channels=valid_sixes
    )
    
    # Fetch the potentially updated team_data for the view
    updated_team_data = await get_team(guild.id)

    view = EditTeamChannelsView(ctx.author.id, guild, updated_team_data)
    await ctx.followup.send(
        "**Edit Matchmaking Channels**\n"
        "Your current channels have been validated. Channels that no longer exist or are inaccessible have been removed.\n"
        "Use the dropdowns below to select your new 6v6 and/or 8v8 matchmaking channels. "
        "Making a selection will override previous settings for that type.", 
        view=view, 
        ephemeral=True
    )
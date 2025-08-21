from ios_bot.config import *
from ios_bot.database_manager import *

# Only keep the cleanup placeholder command - remove all others
@bot.slash_command(
    name="cleanup_placeholder_teams",
    description="Remove all placeholder teams with 'Unknown' captains (admin only)."
)
async def cleanup_placeholder_teams_command(ctx):
    """Remove placeholder teams that were auto-created from CSV parsing."""
    
    # Check admin permissions
    if not ctx.author.id == 464590153994731561:
        await ctx.respond(
            "❌ You need administrator permissions to use this command.",
            ephemeral=True
        )
        return
    
    # Defer the response since this might take a while
    await ctx.defer()
    
    try:
        # Get count of placeholder teams first
        placeholder_count = await execute_query(
            "SELECT COUNT(*) as count FROM IOSCA_TEAMS WHERE captain_name = 'Unknown' OR captain_id = 0",
            fetchone=True
        )
        
        if not placeholder_count or placeholder_count['count'] == 0:
            await ctx.followup.send("✅ No placeholder teams found to clean up.")
            return
        
        # Create confirmation embed
        embed = discord.Embed(
            title="🗑️ Cleanup Placeholder Teams",
            description=f"Found **{placeholder_count['count']} placeholder teams** with 'Unknown' captains.\n\n"
                       f"This will:\n"
                       f"• Remove all teams with captain_name = 'Unknown' or captain_id = 0\n"
                       f"• Keep all real registered teams intact\n"
                       f"• Preserve all match statistics and player data\n\n"
                       f"⚠️ **This action cannot be undone!**",
            color=discord.Color.orange()
        )
        
        # Create buttons
        view = View(timeout=300)
        
        confirm_button = Button(
            label="🗑️ Confirm Cleanup",
            style=ButtonStyle.danger,
            custom_id="confirm_cleanup"
        )
        
        cancel_button = Button(
            label="❌ Cancel",
            style=ButtonStyle.secondary,
            custom_id="cancel_cleanup"
        )
        
        async def confirm_callback(button_interaction):
            if button_interaction.user.id != ctx.author.id:
                await button_interaction.response.send_message("❌ Only the command author can confirm this action.", ephemeral=True)
                return
            
            # Disable buttons
            confirm_button.disabled = True
            cancel_button.disabled = True
            
            # Update the message to show processing
            processing_embed = discord.Embed(
                title="🔄 Processing Cleanup...",
                description="Removing placeholder teams, please wait...",
                color=discord.Color.blue()
            )
            await button_interaction.response.edit_message(embed=processing_embed, view=view)
            
            try:
                # Perform the cleanup
                result = await cleanup_placeholder_teams()
                
                if result:
                    # Get final count
                    final_count = await execute_query(
                        "SELECT COUNT(*) as count FROM IOSCA_TEAMS WHERE captain_name != 'Unknown' AND captain_id != 0",
                        fetchone=True
                    )
                    
                    success_embed = discord.Embed(
                        title="✅ Cleanup Complete",
                        description=f"Successfully removed **{placeholder_count['count']} placeholder teams**.\n\n"
                                   f"Remaining registered teams: **{final_count['count'] if final_count else 0}**\n\n"
                                   f"All match statistics and player data have been preserved.",
                        color=discord.Color.green()
                    )
                    await button_interaction.edit_original_response(embed=success_embed, view=None)
                else:
                    error_embed = discord.Embed(
                        title="❌ Cleanup Failed",
                        description="An error occurred during cleanup. Please check the logs.",
                        color=discord.Color.red()
                    )
                    await button_interaction.edit_original_response(embed=error_embed, view=None)
                    
            except Exception as e:
                error_embed = discord.Embed(
                    title="❌ Cleanup Error",
                    description=f"An error occurred: {str(e)}",
                    color=discord.Color.red()
                )
                await button_interaction.edit_original_response(embed=error_embed, view=None)
        
        async def cancel_callback(button_interaction):
            if button_interaction.user.id != ctx.author.id:
                await button_interaction.response.send_message("❌ Only the command author can cancel this action.", ephemeral=True)
                return
            
            cancel_embed = discord.Embed(
                title="❌ Cleanup Cancelled",
                description="No teams were removed.",
                color=discord.Color.red()
            )
            await button_interaction.response.edit_message(embed=cancel_embed, view=None)
        
        confirm_button.callback = confirm_callback
        cancel_button.callback = cancel_callback
        
        view.add_item(confirm_button)
        view.add_item(cancel_button)
        
        await ctx.followup.send(embed=embed, view=view)
        
    except Exception as e:
        await ctx.followup.send(f"❌ Error getting placeholder team count: {str(e)}")


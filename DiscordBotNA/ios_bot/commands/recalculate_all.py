"""
Slash command to recalculate all player ratings from scratch.
This command clears all existing rating data and recalculates everything
from the beginning based on all match data in the database.
"""
from ios_bot.config import *
import logging

logger = logging.getLogger(__name__)


_recalculation_in_progress = False


async def _send_status(interaction: discord.Interaction, message: str):
    """Send a status update message."""
    embed = discord.Embed(
        description=message,
        color=discord.Color.blue(),
        timestamp=datetime.now(datetime.timezone.utc)
    )
    await interaction.followup.send(embed=embed)


async def _clear_player_ratings():
    """Clear all player ratings from IOSCA_PLAYERS table."""
    try:
        await bot.db.pool.execute("UPDATE IOSCA_PLAYERS SET rating = NULL")
        logger.info("Cleared all player ratings")
    except Exception as e:
        logger.error(f"Error clearing player ratings: {e}")
        raise


async def _regenerate_player_ratings():
    """Regenerate all player ratings from ALL match data."""
    try:
        from ios_bot.ratings.Rating_Generator.generate_ratings import generate_player_ratings
        await generate_player_ratings()
        logger.info("Regenerated all player ratings from complete match history")
    except Exception as e:
        logger.error(f"Error regenerating player ratings: {e}")
        raise


async def _pause_tasks():
    """Pause all scheduled tasks and return list of paused tasks."""
    from ios_bot.tasks import refresh_statistics
    paused_tasks = []
    try:
        if refresh_statistics.is_running():
            refresh_statistics.cancel()
            paused_tasks.append('refresh_statistics')
            logger.info("Paused refresh_statistics task")
        return paused_tasks
    except Exception as e:
        logger.error(f"Error pausing tasks: {e}")
        return paused_tasks


async def _resume_tasks(paused_tasks):
    """Resume previously paused tasks."""
    from ios_bot.tasks import refresh_statistics
    try:
        if 'refresh_statistics' in paused_tasks and not refresh_statistics.is_running():
            refresh_statistics.start()
            logger.info("Resumed refresh_statistics task")
    except Exception as e:
        logger.error(f"Error resuming tasks: {e}")
        raise


async def _get_match_count():
    """Get total match count for status reporting."""
    try:
        return await bot.db.pool.fetchval("SELECT COUNT(*) FROM MATCH_STATS")
    except Exception as e:
        logger.error(f"Error getting match count: {e}")
        return None


async def _run_full_recalculation(interaction: discord.Interaction):
    """Run the full recalculation process."""
    global _recalculation_in_progress
    from ios_bot import tasks
    tasks_paused = []
    try:
        start_time = datetime.now()
        matches_processed = None

        await _send_status(interaction, "⏸️ Pausing scheduled tasks...")
        tasks_paused = await _pause_tasks()

        await _send_status(interaction, "🗑️ Clearing existing player ratings...")
        await _clear_player_ratings()

        await _send_status(interaction, "🌟 Regenerating player ratings from all matches...")
        await _regenerate_player_ratings()
        matches_processed = await _get_match_count()

        await _send_status(interaction, "▶️ Resuming scheduled tasks...")
        await _resume_tasks(tasks_paused)

        elapsed = (datetime.now() - start_time).total_seconds()
        completion_embed = discord.Embed(
            title="✅ Recalculation Complete",
            description=(
                f"Successfully recalculated all player ratings!\n\n"
                f"**Statistics:**\n"
                f"• Matches processed: {matches_processed if matches_processed is not None else 'N/A'}\n"
                f"• Time elapsed: {elapsed:.1f} seconds\n\n"
                f"All player ratings have been updated.\n"
                f"▶️ **Scheduled tasks resumed**"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(datetime.timezone.utc)
        )
        await interaction.followup.send(embed=completion_embed)
    except Exception as e:
        logger.error(f"Error during full recalculation: {e}", exc_info=True)
        try:
            await _resume_tasks(tasks_paused)
        except Exception:
            pass
        error_embed = discord.Embed(
            title="❌ Recalculation Failed",
            description=f"An error occurred during recalculation:\n```{str(e)}```\n\nScheduled tasks have been resumed.",
            color=discord.Color.red(),
            timestamp=datetime.now(datetime.timezone.utc)
        )
        await interaction.followup.send(embed=error_embed)
    finally:
        _recalculation_in_progress = False
    
@bot.slash_command(
    name="recalculate_all",
    description="[ADMIN] Recalculate all player ratings from scratch"
)
@commands.has_permissions(administrator=True)
async def recalculate_all(ctx):
    """Recalculate all player ratings from scratch."""
    global _recalculation_in_progress
    if _recalculation_in_progress:
        await ctx.respond("❌ A recalculation is already in progress. Please wait for it to complete.", ephemeral=True)
        return

    embed = discord.Embed(
        title="⚠️ Recalculate Ratings",
        description=(
            "This will **clear all existing player ratings** "
            "and recalculate everything from scratch based on all match data.\n\n"
            "This operation may take several minutes depending on the number of matches.\n\n"
            "**Are you sure you want to proceed?**"
        ),
        color=discord.Color.orange()
    )
    view = ConfirmRecalculationView(ctx.user)
    await ctx.respond(embed=embed, view=view, ephemeral=True)


class ConfirmRecalculationView(discord.ui.View):
    """View with confirmation buttons for recalculation."""
    
    def __init__(self, user: discord.User):
        super().__init__(timeout=60.0)
        self.user = user
    
    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Confirm and start recalculation."""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "❌ Only the user who initiated this command can confirm.",
                ephemeral=True
            )
            return
        
        # Disable buttons
        for item in self.children:
            item.disabled = True
        
        await interaction.response.edit_message(view=self)
        
        global _recalculation_in_progress
        _recalculation_in_progress = True
        status_embed = discord.Embed(
            title="🔄 Recalculation Started",
            description="Starting full recalculation of player ratings...\n\n⏸️ **Scheduled tasks paused during recalculation**",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        await interaction.followup.send(embed=status_embed)
        asyncio.create_task(_run_full_recalculation(interaction))
        
        self.stop()
    
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Cancel recalculation."""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "❌ Only the user who initiated this command can cancel.",
                ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title="❌ Recalculation Cancelled",
            description="The recalculation has been cancelled.",
            color=discord.Color.red()
        )
        
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()


def setup(bot):
    # Command is registered via @bot.slash_command at import time.
    return


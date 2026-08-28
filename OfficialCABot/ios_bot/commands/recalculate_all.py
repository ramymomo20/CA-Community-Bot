"""
Slash command to recalculate all player ratings from scratch.
This command clears all existing rating data and recalculates everything
from the beginning based on all match data in the database.
"""
from ios_bot.config import *
import asyncio
import logging
import json
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


_recalculation_in_progress = False

# Kill switch for the season-rating pipeline (ios_bot/ratings/Rating_Generator).
# Defaults OFF: a rating formula/methodology change should never silently take
# effect in production (overwriting every player's visible rating, and
# potentially their Discord rank role via sync_rating_roles) just because the
# code got deployed. Flip RATINGS_SEASON_PIPELINE_ENABLED=1 once the new
# ratings have been reviewed and are trusted to go live.
RATINGS_SEASON_PIPELINE_ENABLED = os.getenv("RATINGS_SEASON_PIPELINE_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


async def _ensure_db(interaction: discord.Interaction):
    """Ensure a db handle exists on the running bot instance."""
    db_handle = getattr(getattr(interaction, "client", None), "db", None) or getattr(bot, "db", None)
    if db_handle is not None:
        return db_handle

    from ios_bot.db import Database
    from ios_bot.db.connection import get_connection_string

    db_handle = Database(get_connection_string())
    await db_handle.initialize()

    if getattr(interaction, "client", None) is not None:
        setattr(interaction.client, "db", db_handle)
    setattr(bot, "db", db_handle)
    return db_handle


async def _safe_notify(interaction: discord.Interaction, message: str, *, color=discord.Color.blue(), title: str | None = None, ephemeral: bool = True):
    """Send a status update and survive expired interaction/webhook tokens."""
    embed = discord.Embed(
        title=title or "Ratings Update",
        description=message,
        color=color,
        timestamp=datetime.now(timezone.utc)
    )
    try:
        return await interaction.followup.send(embed=embed, ephemeral=ephemeral)
    except Exception:
        pass

    try:
        channel = getattr(interaction, "channel", None)
        user = getattr(interaction, "user", None)
        mention = f"{user.mention} " if user and hasattr(user, "mention") else ""
        if channel:
            await channel.send(content=f"{mention}{message}", embed=embed)
    except Exception:
        return


async def _send_status(interaction: discord.Interaction, message: str):
    """Send a status update message."""
    await _safe_notify(interaction, message)


async def _regenerate_player_ratings():
    """Regenerate all player ratings from ALL match data."""
    if not RATINGS_SEASON_PIPELINE_ENABLED:
        logger.info(
            "Skipped season-rating regeneration: RATINGS_SEASON_PIPELINE_ENABLED is disabled."
        )
        return {
            "ok": True,
            "skipped": "ratings pipeline disabled (RATINGS_SEASON_PIPELINE_ENABLED=0)",
            "members_updated": 0,
            "members_unchanged": 0,
        }
    try:
        from ios_bot.ratings.Rating_Generator.generate_ratings import (
            generate_player_ratings,
            get_last_generate_error,
        )
        from ios_bot.ratings.rating_role_sync import sync_rating_roles
        ok = await generate_player_ratings()
        if not ok:
            detail = get_last_generate_error() or "unknown generator failure"
            raise RuntimeError(f"generate_player_ratings() returned False: {detail}")
        role_sync_result = await sync_rating_roles(bot, getattr(bot, "db", None))
        logger.info("Regenerated all player ratings from complete match history")
        db_handle = getattr(bot, "db", None)
        if db_handle is not None and getattr(db_handle, "players", None) is not None:
            db_handle.players.invalidate_ratings_cache()
        return role_sync_result
    except Exception as e:
        logger.error(f"Error regenerating player ratings: {e}")
        raise


async def _rebuild_match_performance(db_handle):
    """Persist per-match ratings/MVP into PLAYER_MATCH_DATA for all matches."""
    if not RATINGS_SEASON_PIPELINE_ENABLED:
        logger.info(
            "Skipped match_rating rebuild: RATINGS_SEASON_PIPELINE_ENABLED is disabled."
        )
        return {"matches": 0, "rows": 0, "skipped": "ratings pipeline disabled (RATINGS_SEASON_PIPELINE_ENABLED=0)"}
    try:
        from ios_bot.utils.match_performance import rate_player, get_mvp_data

        if not hasattr(db_handle.pool, "pool") or db_handle.pool.pool is None:
            return {"matches": 0, "rows": 0, "skipped": "db pool unavailable"}

        async with db_handle.pool.pool.acquire() as conn:
            cols = await conn.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'player_match_data'
                """
            )
            have = {str(r["column_name"]).lower() for r in cols}
            required = {"match_rating", "is_match_mvp", "mvp_score", "mvp_key_stats"}
            if not required.issubset(have):
                missing = sorted(required - have)
                return {"matches": 0, "rows": 0, "skipped": f"missing columns: {', '.join(missing)}"}

            match_rows = await conn.fetch(
                """
                SELECT DISTINCT match_id::text AS match_id
                FROM player_match_data
                ORDER BY match_id::text
                """
            )

            updated_rows = 0
            processed_matches = 0
            for mr in match_rows:
                match_id = str(mr["match_id"])
                rows = await conn.fetch(
                    """
                    SELECT
                        id,
                        guild_id,
                        player_name,
                        position,
                        status,
                        goals,
                        assists,
                        second_assists,
                        shots,
                        shots_on_goal,
                        passes_completed,
                        passes_attempted,
                        chances_created,
                        key_passes,
                        interceptions,
                        tackles,
                        sliding_tackles_completed,
                        fouls,
                        yellow_cards,
                        red_cards,
                        keeper_saves,
                        keeper_saves_caught,
                        goals_conceded,
                        offsides,
                        own_goals,
                        fouls_suffered,
                        possession,
                        clutch_actions,
                        sub_impact
                    FROM player_match_data
                    WHERE match_id::text = $1
                    ORDER BY id
                    """,
                    match_id,
                )
                if not rows:
                    continue

                players = [dict(r) for r in rows]
                for p in players:
                    rating = rate_player(p)
                    p["match_rating"] = round(float(rating), 2) if isinstance(rating, (int, float)) else None
                    p["is_match_mvp"] = False
                    p["mvp_score"] = None
                    p["mvp_key_stats"] = []

                mvp = get_mvp_data(players)
                if isinstance(mvp, dict):
                    mvp_name = str(mvp.get("name") or "").strip().lower()
                    mvp_pos = str(mvp.get("position") or "").strip().upper()
                    mvp_score = mvp.get("score")
                    mvp_stats = mvp.get("stats") if isinstance(mvp.get("stats"), list) else []
                    for p in players:
                        p_name = str(p.get("player_name") or "").strip().lower()
                        p_pos = str(p.get("position") or "").strip().upper()
                        if p_name == mvp_name and (not mvp_pos or p_pos == mvp_pos):
                            p["is_match_mvp"] = True
                            p["mvp_score"] = round(float(mvp_score), 2) if isinstance(mvp_score, (int, float)) else None
                            p["mvp_key_stats"] = mvp_stats[:6]
                            break

                async with conn.transaction():
                    for p in players:
                        await conn.execute(
                            """
                            UPDATE player_match_data
                            SET
                                match_rating = $1,
                                is_match_mvp = $2,
                                mvp_score = $3,
                                mvp_key_stats = $4::jsonb
                            WHERE id = $5
                            """,
                            p.get("match_rating"),
                            bool(p.get("is_match_mvp")),
                            p.get("mvp_score"),
                            json.dumps(p.get("mvp_key_stats") or []),
                            int(p["id"]),
                        )
                        updated_rows += 1

                processed_matches += 1

        return {"matches": processed_matches, "rows": updated_rows, "skipped": None}
    except Exception as e:
        logger.error(f"Error rebuilding per-match performance: {e}", exc_info=True)
        raise


async def _recalculate_team_averages():
    """Recompute team average ratings from IOSCA_PLAYERS ratings."""
    try:
        from ios_bot.ratings.Rating_Generator.generate_ratings import update_team_average_ratings
        await update_team_average_ratings()
        logger.info("Recomputed team average ratings")
        db_handle = getattr(bot, "db", None)
        if db_handle is not None and getattr(db_handle, "teams", None) is not None:
            db_handle.teams.invalidate_cache()
    except Exception as e:
        logger.error(f"Error recomputing team average ratings: {e}")
        raise


async def _pause_tasks():
    """Pause all scheduled tasks and return list of paused tasks."""
    from ios_bot.tasks import refresh_statistics, auto_sync_tournaments, schedule_match_reminders, backfill_match_links
    paused_tasks = []
    try:
        if refresh_statistics.is_running():
            refresh_statistics.cancel()
            paused_tasks.append('refresh_statistics')
            logger.info("Paused refresh_statistics task")
        if auto_sync_tournaments.is_running():
            auto_sync_tournaments.cancel()
            paused_tasks.append('auto_sync_tournaments')
            logger.info("Paused auto_sync_tournaments task")
        if schedule_match_reminders.is_running():
            schedule_match_reminders.cancel()
            paused_tasks.append('schedule_match_reminders')
            logger.info("Paused schedule_match_reminders task")
        if backfill_match_links.is_running():
            backfill_match_links.cancel()
            paused_tasks.append('backfill_match_links')
            logger.info("Paused backfill_match_links task")
        return paused_tasks
    except Exception as e:
        logger.error(f"Error pausing tasks: {e}")
        return paused_tasks


async def _resume_tasks(paused_tasks):
    """Resume previously paused tasks."""
    from ios_bot.tasks import refresh_statistics, auto_sync_tournaments, schedule_match_reminders, backfill_match_links
    try:
        if 'refresh_statistics' in paused_tasks and not refresh_statistics.is_running():
            refresh_statistics.start()
            logger.info("Resumed refresh_statistics task")
        if 'auto_sync_tournaments' in paused_tasks and not auto_sync_tournaments.is_running():
            auto_sync_tournaments.start()
            logger.info("Resumed auto_sync_tournaments task")
        if 'schedule_match_reminders' in paused_tasks and not schedule_match_reminders.is_running():
            schedule_match_reminders.start()
            logger.info("Resumed schedule_match_reminders task")
        if 'backfill_match_links' in paused_tasks and not backfill_match_links.is_running():
            backfill_match_links.start()
            logger.info("Resumed backfill_match_links task")
    except Exception as e:
        logger.error(f"Error resuming tasks: {e}")
        raise


async def _get_match_count(db_handle):
    """Get total match count for status reporting."""
    try:
        return await db_handle.pool.fetchval("SELECT COUNT(*) FROM MATCH_STATS")
    except Exception as e:
        logger.error(f"Error getting match count: {e}")
        return None


async def _run_full_recalculation(interaction: discord.Interaction):
    """Run the full recalculation process."""
    global _recalculation_in_progress
    tasks_paused = []
    try:
        start_time = datetime.now(timezone.utc)
        matches_processed = None
        perf_result = None
        role_sync_result = None

        await _send_status(interaction, "Pausing scheduled tasks...")
        tasks_paused = await _pause_tasks()
        db_handle = await _ensure_db(interaction)

        await _send_status(interaction, "Rebuilding per-match ratings and MVP flags...")
        perf_result = await _rebuild_match_performance(db_handle)

        await _send_status(interaction, "Regenerating player ratings from all matches...")
        role_sync_result = await _regenerate_player_ratings()

        await _send_status(interaction, "Recomputing team average ratings...")
        await _recalculate_team_averages()
        matches_processed = await _get_match_count(db_handle)

        await _send_status(interaction, "Resuming scheduled tasks...")
        await _resume_tasks(tasks_paused)

        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        await _safe_notify(
            interaction,
            (
                "Full recalculation completed.\n\n"
                f"- Matches processed: {matches_processed if matches_processed is not None else 'N/A'}\n"
                f"- Match performance backfill: {perf_result['matches']} matches / {perf_result['rows']} rows\n"
                f"- Rating role sync: {role_sync_result['members_updated'] if role_sync_result else 0} updated, {role_sync_result['members_unchanged'] if role_sync_result else 0} unchanged\n"
                f"- Time elapsed: {elapsed:.1f} seconds\n"
                "- Scheduled tasks resumed."
            ),
            title="Recalculation Complete",
            color=discord.Color.green(),
            ephemeral=False,
        )
    except Exception as e:
        logger.error(f"Error during full recalculation: {e}", exc_info=True)
        try:
            await _resume_tasks(tasks_paused)
        except Exception:
            pass
        await _safe_notify(
            interaction,
            f"Full recalculation failed: `{str(e)}`\nScheduled tasks were resumed.",
            title="Recalculation Failed",
            color=discord.Color.red(),
            ephemeral=False,
        )
    finally:
        _recalculation_in_progress = False


async def _run_ratings_only_recalculation(interaction: discord.Interaction):
    """Recalculate player/team ratings only, without task pause or data clearing."""
    global _recalculation_in_progress
    try:
        start_time = datetime.now(timezone.utc)
        db_handle = await _ensure_db(interaction)
        match_count = await _get_match_count(db_handle)
        role_sync_result = None

        await _send_status(interaction, "Regenerating player ratings from all matches...")
        role_sync_result = await _regenerate_player_ratings()

        await _send_status(interaction, "Recomputing team average ratings...")
        await _recalculate_team_averages()

        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        await _safe_notify(
            interaction,
            (
                "Ratings-only recalculation completed.\n\n"
                f"- Matches in database: {match_count if match_count is not None else 'N/A'}\n"
                f"- Rating role sync: {role_sync_result['members_updated'] if role_sync_result else 0} updated, {role_sync_result['members_unchanged'] if role_sync_result else 0} unchanged\n"
                f"- Time elapsed: {elapsed:.1f} seconds"
            ),
            title="Ratings Recalculation Complete",
            color=discord.Color.green(),
            ephemeral=False,
        )
    except Exception as e:
        logger.error(f"Error during ratings-only recalculation: {e}", exc_info=True)
        await _safe_notify(
            interaction,
            f"Ratings-only recalculation failed: `{str(e)}`",
            title="Ratings Recalculation Failed",
            color=discord.Color.red(),
            ephemeral=False,
        )
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
        await ctx.respond("A recalculation is already in progress. Please wait for it to complete.", ephemeral=True)
        return

    embed = discord.Embed(
        title="Recalculate Ratings",
        description=(
            "This will recalculate all player ratings from scratch based on all match data, "
            "overwriting current values.\n\n"
            "This operation may take several minutes depending on the number of matches.\n\n"
            "Are you sure you want to proceed?"
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
                "Only the user who initiated this command can confirm.",
                ephemeral=True
            )
            return

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(view=self)

        global _recalculation_in_progress
        _recalculation_in_progress = True
        await _safe_notify(
            interaction,
            "Starting full recalculation of player ratings. Scheduled tasks will be paused during recalculation.",
            title="Recalculation Started",
            color=discord.Color.blue(),
            ephemeral=True,
        )
        asyncio.create_task(_run_full_recalculation(interaction))

        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Cancel recalculation."""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "Only the user who initiated this command can cancel.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Recalculation Cancelled",
            description="The recalculation has been cancelled.",
            color=discord.Color.red()
        )

        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()


@bot.slash_command(
    name="recalculate_ratings_only",
    description="[ADMIN] Recompute player/team ratings from all current match data (lighter)"
)
@commands.has_permissions(administrator=True)
async def recalculate_ratings_only(ctx):
    """Lightweight ratings recalculation without task pausing or rating clears."""
    global _recalculation_in_progress
    if _recalculation_in_progress:
        await ctx.respond("A recalculation is already in progress. Please wait for it to complete.", ephemeral=True)
        return

    await ctx.respond(
        "Starting ratings-only recalculation in the background. You will receive progress updates here.",
        ephemeral=True,
    )

    _recalculation_in_progress = True
    interaction = getattr(ctx, "interaction", None)
    if interaction is None:
        _recalculation_in_progress = False
        await ctx.followup.send("Could not start ratings-only recalculation: missing interaction context.", ephemeral=True)
        return

    asyncio.create_task(_run_ratings_only_recalculation(interaction))


def setup(bot):
    # Command is registered via @bot.slash_command at import time.
    return



from ios_bot.config import *

import json

# Maps the specific position a player picks to the broad rating category
# used by the season rating pipeline (see ios_bot/ratings/Rating_Generator).
POSITION_TO_ROLE = {
    "GK": "GK",
    "LB": "DEF",
    "CB": "DEF",
    "RB": "DEF",
    "CM": "MID",
    "LM": "MID",
    "RM": "MID",
    "LW": "ATK",
    "RW": "ATK",
    "CF": "ATK",
}

# Must match ROLE_RATING_ESTABLISHED_APPEARANCES / MIN_ESTABLISHED_ROLE_APPEARANCES
# in role_based_ratings.py -- this is the same bar a role has to clear before
# it's trusted as a "real" sample instead of a hot streak.
MIN_APPEARANCES_FOR_PREFERENCE = 15


@bot.slash_command(
    name="set_position",
    description="Request your main rating be based on a specific position instead of your most-played one.",
)
async def set_position(
    ctx,
    position: Option(
        str,
        "Position to be rated as",
        choices=["GK", "LB", "CB", "RB", "CM", "LM", "RM", "LW", "RW", "CF"],
        required=True,
    ),
):
    await ctx.defer(ephemeral=True)

    player = await bot.db.players.get_player_by_discord_id(ctx.author.id)
    if not player or not player.get("steam_id"):
        await ctx.followup.send(
            "You don't have a registered player profile yet -- use `/player_register` first.",
            ephemeral=True,
        )
        return

    steam_id = str(player["steam_id"])
    linked_raw = player.get("linked_steam_ids")
    linked_ids = []
    if linked_raw:
        try:
            parsed = json.loads(linked_raw) if isinstance(linked_raw, str) else linked_raw
            if isinstance(parsed, list):
                linked_ids = [str(v) for v in parsed]
        except Exception:
            linked_ids = []
    all_steam_ids = list({steam_id, *linked_ids})

    role = POSITION_TO_ROLE[position]

    role_positions = [pos for pos, r in POSITION_TO_ROLE.items() if r == role]
    rows = await bot.db.pool.fetch(
        """
        SELECT count(*) AS appearances
        FROM counted_player_match_data
        WHERE steam_id = ANY($1::text[])
          AND upper(position) = ANY($2::text[])
          AND status NOT IN ('on_bench', 'bench', 'dnp', 'did_not_play')
        """,
        all_steam_ids,
        role_positions,
    )
    appearances = int(rows[0]["appearances"]) if rows else 0

    if appearances < MIN_APPEARANCES_FOR_PREFERENCE:
        needed = MIN_APPEARANCES_FOR_PREFERENCE - appearances
        await ctx.followup.send(
            f"❌ Request denied. You have **{appearances}** appearances as {role} "
            f"({position} counts toward {role}), but need at least "
            f"**{MIN_APPEARANCES_FOR_PREFERENCE}** before that position can become your main rating. "
            f"Play **{needed}** more {role} games and try again.",
            ephemeral=True,
        )
        return

    await bot.db.pool.execute(
        "UPDATE IOSCA_PLAYERS SET preferred_position = $1, preferred_main_role = $2, updated_at = CURRENT_TIMESTAMP WHERE steam_id = $3",
        position,
        role,
        steam_id,
    )
    bot.db.players.invalidate_ratings_cache()

    await ctx.followup.send(
        f"✅ Request approved. Your main rating will now be based on **{role}** "
        f"(you have {appearances} appearances there). This takes effect on the next ratings run.",
        ephemeral=True,
    )


def setup(bot):
    return

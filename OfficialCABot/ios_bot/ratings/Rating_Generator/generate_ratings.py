from __future__ import annotations

import asyncio

_LAST_GENERATE_ERROR: str | None = None


def _set_last_generate_error(message: str | None) -> None:
    global _LAST_GENERATE_ERROR
    _LAST_GENERATE_ERROR = str(message) if message else None


def get_last_generate_error() -> str | None:
    return _LAST_GENERATE_ERROR


async def generate_player_ratings() -> bool:
    from ios_bot.ratings.Rating_Generator.role_based_ratings import (
        generate_player_ratings as _generate_player_ratings_v2,
        get_last_generate_error as _get_last_generate_error_v2,
    )

    ok = await _generate_player_ratings_v2()
    _set_last_generate_error(_get_last_generate_error_v2())
    return ok


async def update_team_average_ratings() -> bool:
    from ios_bot.ratings.Rating_Generator.role_based_ratings import (
        get_last_generate_error as _get_last_generate_error_v2,
        update_team_average_ratings as _update_team_average_ratings_v2,
    )

    ok = await _update_team_average_ratings_v2()
    _set_last_generate_error(_get_last_generate_error_v2())
    return ok


async def main():
    from ios_bot.ratings.Rating_Generator.role_based_ratings import main as _main_v2

    await _main_v2()


if __name__ == "__main__":
    asyncio.run(main())

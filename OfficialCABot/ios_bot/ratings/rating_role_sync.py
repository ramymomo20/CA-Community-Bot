from __future__ import annotations

import logging
import os
from typing import Any, Dict

from ios_bot import config


logger = logging.getLogger(__name__)
RATING_ROLE_SYNC_ENABLED = os.getenv("IOSCA_RATING_ROLE_SYNC_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}

RATING_ROLE_KEYS = ("star_role", "d1_role", "d2_role", "d3_role", "d4_role")
RATING_ROLE_THRESHOLDS = (
    ("star_role", float(os.getenv("RATING_ROLE_STAR_MIN", "9.0"))),
    ("d1_role", float(os.getenv("RATING_ROLE_D1_MIN", "8.3"))),
    ("d2_role", float(os.getenv("RATING_ROLE_D2_MIN", "7.5"))),
    ("d3_role", float(os.getenv("RATING_ROLE_D3_MIN", "6.7"))),
    ("d4_role", float("-inf")),
)


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        text = str(value).strip()
        if not text or not text.isdigit():
            return None
        return int(text)
    except Exception:
        return None


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _target_role_key_for_rating(rating: float | None) -> str | None:
    if rating is None:
        return None
    for asset_key, minimum in RATING_ROLE_THRESHOLDS:
        if rating >= minimum:
            return asset_key
    return None


async def sync_rating_roles(bot_instance, db_handle=None) -> Dict[str, Any]:
    """
    Keep one division role in sync for players with an official display rating.

    Players without an official rating are intentionally left untouched.
    """
    summary: Dict[str, Any] = {
        "guild_id": None,
        "rated_players": 0,
        "members_found": 0,
        "members_updated": 0,
        "members_unchanged": 0,
        "members_not_found": 0,
        "members_skipped": 0,
        "missing_asset_keys": [],
        "missing_guild_roles": [],
        "errors": [],
        "ok": False,
    }

    if not RATING_ROLE_SYNC_ENABLED:
        summary["ok"] = True
        summary["errors"].append("rating role sync disabled")
        logger.info("Rating role sync skipped: IOSCA_RATING_ROLE_SYNC_ENABLED is disabled.")
        return summary

    if bot_instance is None:
        summary["errors"].append("bot unavailable")
        return summary

    db_handle = db_handle or getattr(bot_instance, "db", None)
    if db_handle is None:
        summary["errors"].append("database unavailable")
        return summary

    main_guild_id = _coerce_int(getattr(config, "MAIN_GUILD_ID", None))
    summary["guild_id"] = main_guild_id
    if main_guild_id is None:
        summary["errors"].append("main guild id unavailable")
        return summary

    guild = bot_instance.get_guild(main_guild_id)
    if guild is None:
        summary["errors"].append(f"main guild {main_guild_id} not cached")
        return summary

    assets = await db_handle.server_assets.list_assets(main_guild_id, "role")
    asset_map = {
        str(asset.get("asset_key") or "").strip().lower(): asset
        for asset in assets
    }

    configured_role_ids: dict[str, int] = {}
    for asset_key in RATING_ROLE_KEYS:
        asset = asset_map.get(asset_key)
        role_id = _coerce_int(asset.get("discord_id") if asset else None)
        if role_id is None:
            summary["missing_asset_keys"].append(asset_key)
            continue
        role = guild.get_role(role_id)
        if role is None:
            summary["missing_guild_roles"].append(asset_key)
            continue
        configured_role_ids[asset_key] = role_id

    managed_role_ids = set(configured_role_ids.values())
    if not managed_role_ids:
        summary["errors"].append("no managed rating roles configured")
        return summary

    rows = await db_handle.pool.fetch(
        """
        SELECT discord_id, steam_id, display_main_role_rating
        FROM iosca_players
        WHERE display_main_role_rating IS NOT NULL
          AND discord_id IS NOT NULL
        ORDER BY display_main_role_rating DESC NULLS LAST, steam_id ASC
        """
    )

    summary["rated_players"] = len(rows)
    for row in rows:
        member_id = _coerce_int(row.get("discord_id"))
        rating = _coerce_float(row.get("display_main_role_rating"))
        target_key = _target_role_key_for_rating(rating)

        if member_id is None or target_key is None:
            summary["members_skipped"] += 1
            continue

        target_role_id = configured_role_ids.get(target_key)
        if target_role_id is None:
            summary["members_skipped"] += 1
            continue

        member = guild.get_member(member_id)
        if member is None:
            try:
                member = await guild.fetch_member(member_id)
            except Exception:
                summary["members_not_found"] += 1
                continue

        summary["members_found"] += 1
        current_managed_ids = {role.id for role in member.roles if role.id in managed_role_ids}
        desired_ids = {target_role_id}
        if current_managed_ids == desired_ids:
            summary["members_unchanged"] += 1
            continue

        new_roles = [role for role in member.roles if role.id not in managed_role_ids]
        target_role = guild.get_role(target_role_id)
        if target_role is None:
            summary["members_skipped"] += 1
            continue
        new_roles.append(target_role)

        try:
            await member.edit(
                roles=new_roles,
                reason=f"IOSCA rating role sync ({rating:.2f})",
            )
            summary["members_updated"] += 1
        except Exception as exc:
            summary["members_skipped"] += 1
            if len(summary["errors"]) < 10:
                summary["errors"].append(f"{member_id}: {exc}")

    summary["ok"] = True
    logger.info(
        "Rating role sync complete: guild=%s rated=%s updated=%s unchanged=%s not_found=%s skipped=%s",
        summary["guild_id"],
        summary["rated_players"],
        summary["members_updated"],
        summary["members_unchanged"],
        summary["members_not_found"],
        summary["members_skipped"],
    )
    if summary["missing_asset_keys"]:
        logger.warning("Rating role sync missing asset keys: %s", ", ".join(summary["missing_asset_keys"]))
    if summary["missing_guild_roles"]:
        logger.warning("Rating role sync missing guild roles: %s", ", ".join(summary["missing_guild_roles"]))
    if summary["errors"]:
        logger.warning("Rating role sync errors: %s", "; ".join(summary["errors"]))
    return summary

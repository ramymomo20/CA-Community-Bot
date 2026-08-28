"""Shared match performance helpers (MVP + per-player rating)."""

from __future__ import annotations

import json
from typing import Any


POSITION_WEIGHTS = {
    "GK": {
        "keeperSaves": 0.25,
        "keeperSavesCaught": 0.20,
        "passesCompleted": 0.05,
        "assists": 0.30,
        "secondAssists": 0.20,
        "keyPasses": 0.15,
        "goalsConceded": -0.30,
        "ownGoals": -0.80,
        "redCards": -1.50,
        "yellowCards": -0.25,
        "fouls": -0.12,
    },
    "DEF": {
        "interceptions": 0.25,
        "slidingTacklesCompleted": 0.20,
        "goals": 0.45,
        "assists": 0.25,
        "secondAssists": 0.12,
        "keyPasses": 0.10,
        "passesCompleted": 0.006,
        "keeperSaves": 0.10,
        "goalsConceded": -0.20,
        "ownGoals": -1.00,
        "fouls": -0.18,
        "yellowCards": -0.30,
        "redCards": -2.00,
    },
    "MID": {
        "assists": 0.35,
        "secondAssists": 0.20,
        "keyPasses": 0.12,
        "goals": 0.40,
        "passesCompleted": 0.008,
        "interceptions": 0.15,
        "slidingTacklesCompleted": 0.12,
        "shotsOnGoal": 0.10,
        "chancesCreated": 0.15,
        "fouls": -0.18,
        "yellowCards": -0.25,
        "redCards": -1.80,
        "ownGoals": -0.90,
    },
    "FWD": {
        "goals": 0.40,
        "assists": 0.30,
        "shotsOnGoal": 0.08,
        "keyPasses": 0.12,
        "secondAssists": 0.15,
        "chancesCreated": 0.20,
        "foulsSuffered": 0.05,
        "passesCompleted": 0.005,
        "interceptions": 0.08,
        "fouls": -0.15,
        "yellowCards": -0.30,
        "redCards": -1.70,
        "ownGoals": -0.95,
        "offsides": -0.08,
    },
}

# ✅ Add LM/RM to MID
POSITION_CATEGORIES = {
    "GK": ["GK"],
    "DEF": ["LB", "CB", "RB"],
    "MID": ["CM", "LM", "RM"],
    "FWD": ["LW", "CF", "RW"],
}

KEY_ALIASES = {
    "Name": ["Name", "player_name", "discord_name", "name"],
    "Position": ["Position", "position"],
    "status": ["status"],
    "goals": ["goals"],
    "shots": ["shots"],
    "assists": ["assists"],
    "secondAssists": ["secondAssists", "second_assists"],
    "keyPasses": ["keyPasses", "key_passes"],
    "keeperSaves": ["keeperSaves", "keeper_saves"],
    "keeperSavesCaught": ["keeperSavesCaught", "keeper_saves_caught"],
    "passesCompleted": ["passesCompleted", "passes_completed"],
    "passesAttempted": ["passesAttempted", "passes_attempted"],
    "goalsConceded": ["goalsConceded", "goals_conceded"],
    "ownGoals": ["ownGoals", "own_goals"],
    "redCards": ["redCards", "red_cards"],
    "yellowCards": ["yellowCards", "yellow_cards"],
    "fouls": ["fouls"],
    "interceptions": ["interceptions"],
    "slidingTacklesCompleted": ["slidingTacklesCompleted", "sliding_tackles_completed"],
    "shotsOnGoal": ["shotsOnGoal", "shots_on_goal"],
    "chancesCreated": ["chancesCreated", "chances_created"],
    "foulsSuffered": ["foulsSuffered", "fouls_suffered"],
    "offsides": ["offsides"],
    "possession": ["possession"],
    "clutchActions": ["clutchActions", "clutch_actions"],
    "subImpact": ["subImpact", "sub_impact"],
    "timePlayed": ["timePlayed", "time_played"],
    "distanceCovered": ["distanceCovered", "distance_covered"],
    "timeGK": ["timeGK", "time_gk"],
    "timeDEF": ["timeDEF", "time_def"],
    "timeMID": ["timeMID", "time_mid"],
    "timeATT": ["timeATT", "time_att"],
    "isSingleKeeper": ["isSingleKeeper", "is_single_keeper"],
    "opponentConceded": ["opponentConceded", "opponent_conceded"],
}


def _player_value(player: dict[str, Any], key: str, default: Any = 0) -> Any:
    for alias in KEY_ALIASES.get(key, [key]):
        if alias in player and player.get(alias) is not None:
            return player.get(alias)
    return default


def _num(player: dict[str, Any], key: str) -> float:
    try:
        return float(_player_value(player, key, 0) or 0)
    except (ValueError, TypeError):
        return 0.0


def _name(player: dict[str, Any]) -> str:
    return str(_player_value(player, "Name", "Unknown") or "Unknown")


def _position(player: dict[str, Any]) -> str:
    return str(_player_value(player, "Position", "") or "").upper()


def _status(player: dict[str, Any]) -> str:
    return str(_player_value(player, "status", "") or "").strip().lower()


def _json_like(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return fallback
        try:
            return json.loads(text)
        except Exception:
            return fallback
    return fallback


def _clutch_bonus(player: dict[str, Any], pos_category: str) -> float:
    actions = _json_like(_player_value(player, "clutchActions", []), [])
    if not isinstance(actions, list) or not actions:
        return 0.0

    score = 0.0
    for ev in actions:
        if not isinstance(ev, dict):
            continue

        etype = str(ev.get("type", "")).upper()
        state = str(ev.get("team_state_before", "")).lower()
        # ✅ safer minute parsing
        minute = float(ev.get("minute", 0) or 0)

        if etype == "GOAL":
            base = 0.22
        elif etype == "ASSIST":
            base = 0.16
        elif etype == "SAVE":
            base = 0.14
        elif etype in {"YELLOWCARD", "YELLOW"}:
            base = -0.05
        elif etype in {"REDCARD", "RED"}:
            base = -0.15
        else:
            base = 0.05

        if state == "losing":
            state_mult = 1.25
        elif state == "drawing":
            state_mult = 1.00
        else:
            state_mult = 0.90

        # Slight bump for very late events.
        late_mult = 1.0 + 0.20 * max(0.0, min(1.0, (minute - 85.0) / 15.0))
        score += base * state_mult * late_mult

    pos_mult = {"GK": 0.95, "DEF": 1.00, "MID": 1.05, "FWD": 1.10}.get(pos_category, 1.0)
    return max(-0.45, min(0.90, score * pos_mult))


def _sub_impact_bonus(player: dict[str, Any], pos_category: str) -> float:
    sub_impact = _json_like(_player_value(player, "subImpact", {}), {})
    if not isinstance(sub_impact, dict):
        return 0.0

    summary = sub_impact.get("summary")
    events = sub_impact.get("events")
    if not isinstance(summary, dict):
        summary = {}
    if not isinstance(events, list):
        events = []

    goals = float(summary.get("goals", 0) or 0)
    own_goals = float(summary.get("own_goals", 0) or 0)
    red_cards = float(summary.get("red_cards", 0) or 0)
    yellow_cards = float(summary.get("yellow_cards", 0) or 0)
    event_bonus = min(len(events), 8) * 0.02

    raw = (
        goals * 0.20
        - own_goals * 0.25
        - red_cards * 0.25
        - yellow_cards * 0.06
        + event_bonus
    )
    pos_mult = {"GK": 0.90, "DEF": 0.95, "MID": 1.00, "FWD": 1.05}.get(pos_category, 1.0)
    return max(-0.35, min(0.45, raw * pos_mult))


def _pass_possession_bonus(player: dict[str, Any], pos_category: str) -> float:
    passes_attempted = max(0.0, _num(player, "passesAttempted"))
    passes_completed = max(0.0, _num(player, "passesCompleted"))
    if passes_attempted < 8:
        return 0.0

    pass_acc = passes_completed / max(1.0, passes_attempted)
    possession_raw = max(0.0, _num(player, "possession"))
    possession_pct = possession_raw * 100.0 if possession_raw <= 1.0 else possession_raw

    bonus = 0.0
    if pass_acc >= 0.80 and possession_pct >= 10.0:
        bonus = 0.08 + min(0.27, (pass_acc - 0.80) * 0.90 + (possession_pct - 10.0) * 0.010)
    elif pass_acc >= 0.75 and possession_pct >= 8.0:
        bonus = 0.03 + min(0.12, (pass_acc - 0.75) * 0.55 + (possession_pct - 8.0) * 0.005)

    pos_mult = {"GK": 0.70, "DEF": 1.05, "MID": 1.10, "FWD": 0.95}.get(pos_category, 1.0)
    return max(0.0, min(0.40, bonus * pos_mult))


def _pass_accuracy_penalty(player: dict[str, Any]) -> float:
    passes_attempted = max(0.0, _num(player, "passesAttempted"))
    passes_completed = max(0.0, _num(player, "passesCompleted"))
    if passes_attempted < 8:
        return 0.0

    pass_acc = passes_completed / max(1.0, passes_attempted)
    if pass_acc < 0.25:
        return -0.60
    if pass_acc <= 0.35:
        return -0.50
    if pass_acc < 0.50:
        return -0.40
    return 0.0


def _status_bonus(player: dict[str, Any], pos_category: str) -> float:
    status = _status(player)
    if status in {"on_bench", "bench", "dnp", "did_not_play"}:
        return -1.00
    if status == "started":
        return 0.12
    if status == "substitute":
        return 0.05
    return 0.0


def _gk_save_ratio_bonus(saves: float, goals_conceded: float) -> float:
    """
    ✅ GK special: reward "carry" games.
    - 4x saves/conceded: big boon
    - 3x: medium boon
    - 2x: small boon
    Clean sheet: only bonus if they actually made saves.
    """
    gc = max(0.0, goals_conceded)
    s = max(0.0, saves)

    # Clean sheet bonus only if tested
    if gc == 0:
        if s >= 3:
            return 0.45
        return 0.0

    ratio = s / max(1.0, gc)
    if ratio >= 4.0:
        return 0.55
    if ratio >= 3.0:
        return 0.35
    if ratio >= 2.0:
        return 0.15
    return 0.0


def _legendary_bonus(player: dict[str, Any], pos_category: str) -> float:
    """Rare upside for truly exceptional matches."""
    goals = _num(player, "goals")
    assists = _num(player, "assists")
    second_assists = _num(player, "secondAssists")
    key_passes = _num(player, "keyPasses")
    chances_created = _num(player, "chancesCreated")
    interceptions = _num(player, "interceptions")
    tackles = _num(player, "tackles")
    sliding = _num(player, "slidingTacklesCompleted")
    saves = _num(player, "keeperSaves") + _num(player, "keeperSavesCaught")
    conceded = _num(player, "goalsConceded")
    passes_attempted = max(0.0, _num(player, "passesAttempted"))
    passes_completed = max(0.0, _num(player, "passesCompleted"))
    pass_acc = passes_completed / max(1.0, passes_attempted) if passes_attempted > 0 else 0.0
    possession_raw = max(0.0, _num(player, "possession"))
    possession_pct = possession_raw * 100.0 if possession_raw <= 1.0 else possession_raw

    if pos_category == "FWD":
        if goals >= 6 and assists >= 2:
            return 0.95
        if goals >= 4 and (goals + assists) >= 5 and chances_created >= 2:
            return 0.60
        return 0.0

    if pos_category == "MID":
        if (goals + assists + second_assists) >= 7 and pass_acc >= 0.84 and possession_pct >= 12.0:
            return 0.80
        if (goals + assists) >= 5 and key_passes >= 4 and pass_acc >= 0.80:
            return 0.52
        return 0.0

    if pos_category == "DEF":
        if interceptions >= 12 and (tackles + sliding) >= 9 and conceded <= 1:
            return 0.70
        if interceptions >= 10 and (tackles + sliding) >= 7 and conceded <= 1:
            return 0.48
        return 0.0

    if pos_category == "GK":
        ratio = saves / max(1.0, conceded)
        if conceded == 0 and saves >= 8:
            return 0.70
        if saves >= 12 and ratio >= 4.0:
            return 0.90
        if saves >= 10 and ratio >= 3.0:
            return 0.55
        return 0.0

    return 0.0


def _production_scale(player: dict[str, Any]) -> float:
    """Scale factor to normalize raw counting-stat production by time on pitch.

    A player who plays 90 minutes should not be out-rated by a starter's raw
    totals just because they played longer, and a 10-minute cameo shouldn't
    have its stats extrapolated to a full match (a single late goal would
    otherwise look like a 90-minute-worthy haul). Effective minutes are
    clamped to [30, 90]: below 30, we treat the player as if they'd played 30
    (caps the extrapolation multiplier at 3x); above 90 (extra time), no
    discount is applied for playing longer than a regulation match.
    Only the *positive* production terms are scaled -- cards/fouls/conceded
    goals are left as raw counts, since those are tied to what actually
    happened on the pitch, not an accumulation-over-time rate.
    """
    minutes = _num(player, "timePlayed") / 60.0
    if minutes <= 0:
        return 1.0
    effective_minutes = max(30.0, min(90.0, minutes))
    return 90.0 / effective_minutes


def _position_discipline_adjustment(player: dict[str, Any], pos_category: str) -> float:
    """Penalize drifting out of your assigned role, using the per-match
    position-time split (timeGK/timeDEF/timeMID/timeATT) already recorded
    for every player.

    - A nominal defender/midfielder who spent most of the match functionally
      playing attack gets their attacking output discounted -- they were
      playing out of position, not doing their job exceptionally well.
    - A nominal attacker with zero defensive involvement across the whole
      match gets a small, deliberately gentle penalty (pure poachers who
      score are still valuable -- this isn't meant to punish finishing).
    """
    time_gk = _num(player, "timeGK")
    time_def = _num(player, "timeDEF")
    time_mid = _num(player, "timeMID")
    time_att = _num(player, "timeATT")
    total_role_time = time_gk + time_def + time_mid + time_att
    if total_role_time <= 0:
        return 0.0

    own_time = {"GK": time_gk, "DEF": time_def, "MID": time_mid, "FWD": time_att}.get(pos_category, 0.0)
    purity = own_time / total_role_time

    adjustment = 0.0

    if pos_category in {"DEF", "MID"} and purity < 0.70:
        dilution = min(1.0, max(0.0, (0.70 - purity) / 0.70))
        goals = _num(player, "goals")
        assists = _num(player, "assists")
        adjustment -= dilution * (goals * 0.20 + assists * 0.10)

    if pos_category == "FWD":
        interceptions = _num(player, "interceptions")
        tackles = _num(player, "tackles")
        sliding = _num(player, "slidingTacklesCompleted")
        defensive_time_share = time_def / total_role_time
        if interceptions == 0 and tackles == 0 and sliding == 0 and defensive_time_share < 0.05:
            adjustment -= 0.10

    return adjustment


def rate_player(player: dict[str, Any]) -> float | None:
    pos = _position(player)
    pos_category = next((cat for cat, positions in POSITION_CATEGORIES.items() if pos in positions), None)
    if not pos_category:
        return None
    if _status(player) in {"on_bench", "bench", "dnp", "did_not_play"}:
        return None

    base_score = 6.20

    scale = _production_scale(player)

    goals = _num(player, "goals") * scale
    assists = _num(player, "assists") * scale
    second_assists = _num(player, "secondAssists") * scale
    shots_on_goal = _num(player, "shotsOnGoal") * scale
    chances_created = _num(player, "chancesCreated") * scale
    key_passes = _num(player, "keyPasses") * scale
    passes_completed = _num(player, "passesCompleted") * scale
    interceptions = _num(player, "interceptions") * scale
    tackles = _num(player, "tackles") * scale
    sliding_tackles = _num(player, "slidingTacklesCompleted") * scale
    saves = _num(player, "keeperSaves") * scale
    saves_caught = _num(player, "keeperSavesCaught") * scale
    fouls_suffered = _num(player, "foulsSuffered") * scale
    distance_covered = _num(player, "distanceCovered") * scale

    # Raw (unscaled) counts for discrete achievement/threshold checks below --
    # those describe what literally happened in the match (a hat-trick is 3
    # actual goals, not an extrapolated rate) and must not be inflated by the
    # minutes-normalization scale factor.
    goals_raw = _num(player, "goals")
    assists_raw = _num(player, "assists")
    interceptions_raw = _num(player, "interceptions")
    saves_raw = _num(player, "keeperSaves")
    saves_caught_raw = _num(player, "keeperSavesCaught")
    key_passes_raw = _num(player, "keyPasses")
    chances_created_raw = _num(player, "chancesCreated")

    # Cards, fouls, and goals conceded are tied to what actually happened on
    # the pitch, not an accumulation-over-time rate -- left unscaled by minutes.
    goals_conceded = _num(player, "goalsConceded")
    own_goals = _num(player, "ownGoals")
    red_cards = _num(player, "redCards")
    yellow_cards = _num(player, "yellowCards")
    fouls = _num(player, "fouls")
    offsides = _num(player, "offsides")

    # Per-90 work-rate contribution, on top of position-specific weights below.
    # Weight is deliberately small (correlation with match quality is real but
    # modest, ~0.15-0.22) and only applies to outfield positions.
    work_rate = 0.0 if pos_category == "GK" else max(0.0, distance_covered / 1000.0 - 4.0) * 0.05

    # Keep per-match ratings centered around 6-7 by capping positives and negatives.
    # Weights below are calibrated against real per-position correlations
    # between each stat's per-90 rate and match_rating/goal-differential
    # (see docs/session_handoff_2026-08-26.md for the underlying analysis).
    if pos_category == "GK":
        positive = min(
            2.45,
            saves * 0.30
            + saves_caught * 0.18
            + assists * 0.28
            + key_passes * 0.06
            + passes_completed * 0.003,
        )
    elif pos_category == "DEF":
        positive = min(
            2.25,
            interceptions * 0.30
            + tackles * 0.08
            + sliding_tackles * 0.16
            + goals * 0.62
            + assists * 0.27
            + key_passes * 0.10
            + passes_completed * 0.015
            + work_rate,
        )
    elif pos_category == "MID":
        positive = min(
            2.35,
            goals * 0.60
            + assists * 0.50
            + second_assists * 0.25
            + chances_created * 0.16
            + key_passes * 0.10
            + passes_completed * 0.08
            + interceptions * 0.06
            + tackles * 0.05
            + shots_on_goal * 0.09
            + work_rate,
        )
    else:  # FWD
        positive = min(
            2.50,
            goals * 0.80
            + assists * 0.45
            + second_assists * 0.20
            + shots_on_goal * 0.16
            + key_passes * 0.08
            + chances_created * 0.13
            + fouls_suffered * 0.05
            + work_rate,
        )

    conceded_pen = 0.0
    if pos_category == "GK":
        # Single keepers cover both goals in a match instead of just their
        # own side, so the same defensive competence naturally racks up
        # roughly double the raw conceded count -- halve it before applying
        # the penalty so it lands the same as a double-keeper facing
        # equivalent difficulty on one side.
        is_single_keeper = bool(_player_value(player, "isSingleKeeper", False))
        effective_conceded = (goals_conceded / 2.0) if is_single_keeper else goals_conceded

        # Workload dampening: if this side conceded clearly more than the
        # opponent's side, this keeper faced a tougher night -- ease the
        # per-goal penalty a bit rather than judging them the same as a
        # keeper who had an easy game defensively.
        opponent_conceded = _num(player, "opponentConceded")
        workload_ratio = 0.0
        total_both_sides = effective_conceded + opponent_conceded
        if total_both_sides > 0:
            workload_ratio = (effective_conceded - opponent_conceded) / total_both_sides
        workload_dampen = 1.0 - 0.15 * max(0.0, min(1.0, workload_ratio))

        # Very slight decrease for the first 2-3 goals conceded, accelerating
        # (quadratic) after that -- conceding 1-3 in a fast-paced, high-scoring
        # game mode barely dents the rating, but a bad night (6+) is treated
        # as genuinely bad.
        conceded_pen = (
            0.07 * min(effective_conceded, 3.0)
            + 0.18 * max(0.0, effective_conceded - 3.0) ** 2
        ) * workload_dampen
    elif pos_category == "DEF":
        conceded_pen = goals_conceded * 0.10
    elif pos_category == "MID":
        conceded_pen = goals_conceded * 0.08

    negative = min(
        3.20,
        conceded_pen
        + own_goals * 1.80
        + red_cards * 1.80
        + yellow_cards * 0.35
        + fouls * 0.08
        + (offsides * 0.06 if pos_category == "FWD" else 0.0),
    )

    final_score = base_score + positive - negative

    # Context bonuses
    final_score += _clutch_bonus(player, pos_category)
    final_score += _sub_impact_bonus(player, pos_category)
    final_score += _pass_possession_bonus(player, pos_category)
    final_score += _pass_accuracy_penalty(player)
    final_score += _status_bonus(player, pos_category)
    final_score += _position_discipline_adjustment(player, pos_category)
    saves_total_raw = saves_raw + saves_caught_raw

    # Big attacking feats
    if goals_raw >= 3:
        final_score += 0.45
    elif goals_raw >= 2 and assists_raw >= 2:
        final_score += 0.35

    # GK carry bonuses from save/conceded ratio.
    if pos_category == "GK":
        final_score += _gk_save_ratio_bonus(saves_total_raw, goals_conceded)

    # Modest clean-sheet bump for defensive roles.
    if pos_category in {"GK", "DEF"} and goals_conceded == 0:
        final_score += 0.20

    # Explicit contribution boosts to reward key outcomes.
    if goals_raw >= 1:
        final_score += 0.25 + min(1.10, max(0.0, goals_raw - 1.0) * 0.22)
    if assists_raw >= 1:
        final_score += 0.16 + min(0.75, max(0.0, assists_raw - 1.0) * 0.18)

    if pos_category in {"DEF", "MID"} and interceptions_raw >= 5:
        final_score += 0.20 + min(0.42, max(0.0, interceptions_raw - 5.0) * 0.06)

    if pos_category == "GK":
        save_ratio = saves_total_raw / max(1.0, goals_conceded)
        if save_ratio >= 2.0:
            final_score += min(0.45, (save_ratio - 2.0) * 0.20 + 0.12)

    # Keep "average" band when a player has little impact.
    if pos_category != "GK":
        low_impact = (
            goals_raw == 0
            and assists_raw == 0
            and interceptions_raw < 3
            and key_passes_raw < 3
            and chances_created_raw < 3
        )
        if low_impact:
            final_score = min(final_score, 6.80)
    else:
        gk_low_impact = (
            saves_total_raw < 6
            and (saves_total_raw / max(1.0, goals_conceded)) < 2.0
            and assists_raw == 0
        )
        if gk_low_impact:
            final_score = min(final_score, 6.60)

    # Rare upside lane for monster games.
    final_score += _legendary_bonus(player, pos_category)

    # Compression tuned so 9+ can happen, 10 remains rare.
    if final_score > 8.70:
        final_score = 8.70 + (final_score - 8.50) * 0.80
    if final_score > 9.0:
        final_score = 9.0 + (final_score - 8.80) * 0.50
    if final_score > 9.50:
        final_score = 9.50 + (final_score - 9.30) * 0.35

    return max(3.00, min(10.00, final_score))


def get_mvp_data(player_stats: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not player_stats:
        return None

    team_strength: dict[str, float] = {}
    for player in player_stats:
        gid = str(player.get("guild_id") or "").strip()
        if not gid:
            continue
        team_strength[gid] = team_strength.get(gid, 0.0) + (
            _num(player, "goals") * 1.10
            + _num(player, "assists") * 0.35
            - _num(player, "redCards") * 0.50
            - _num(player, "ownGoals") * 0.60
            - _num(player, "goalsConceded") * 0.12
        )
    ordered_teams = sorted(team_strength.items(), key=lambda item: item[1], reverse=True)
    winner_guild = ordered_teams[0][0] if ordered_teams else None
    loser_guild = ordered_teams[-1][0] if len(ordered_teams) >= 2 else None

    player_scores = []
    for player in player_stats:
        pos = _position(player)
        pos_category = next((cat for cat, vals in POSITION_CATEGORIES.items() if pos in vals), None)
        if not pos_category:
            continue
        if _status(player) in {"on_bench", "bench", "dnp", "did_not_play"}:
            continue

        score = rate_player(player)
        if score is None:
            continue

        # Slightly favor players from the winning side when selecting MVP.
        gid = str(player.get("guild_id") or "").strip()
        if gid and winner_guild and gid == winner_guild:
            score += 0.12
        elif gid and loser_guild and gid == loser_guild:
            score -= 0.05

        key_stats = []
        for stat, weight in POSITION_WEIGHTS[pos_category].items():
            val = _num(player, stat)
            contribution = val * weight
            if val > 0 and weight > 0 and contribution > 0.25:
                key_stats.append(f"{stat}: {int(val)}")

        clutch_bonus = _clutch_bonus(player, pos_category)
        sub_bonus = _sub_impact_bonus(player, pos_category)
        if clutch_bonus > 0.15:
            key_stats.append(f"clutch: +{clutch_bonus:.2f}")
        if sub_bonus > 0.06:
            key_stats.append(f"sub impact: +{sub_bonus:.2f}")

        pass_pos_bonus = _pass_possession_bonus(player, pos_category)
        pass_attempts = _num(player, "passesAttempted")
        pass_completed = _num(player, "passesCompleted")
        pass_acc = pass_completed / max(1.0, pass_attempts) if pass_attempts > 0 else 0.0
        possession_raw = max(0.0, _num(player, "possession"))
        possession_pct = possession_raw * 100.0 if possession_raw <= 1.0 else possession_raw
        if pass_pos_bonus > 0.08 and pass_acc >= 0.80 and possession_pct >= 10.0:
            key_stats.append(f"{int(round(pass_acc * 100))}% pass, {possession_pct:.1f}% poss")

        goals = _num(player, "goals")
        assists = _num(player, "assists")
        saves = _num(player, "keeperSaves") + _num(player, "keeperSavesCaught")
        goals_conceded = _num(player, "goalsConceded")

        if goals == 3:
            key_stats.append("Hat-trick!")
        elif goals >= 2 and assists >= 2:
            key_stats.append("Prime Playmaker")

        legendary = _legendary_bonus(player, pos_category)
        if legendary >= 0.85:
            key_stats.append("Legendary game")
        elif legendary >= 0.50:
            key_stats.append("Dominant performance")

        if pos == "GK":
            ratio_bonus = _gk_save_ratio_bonus(saves, goals_conceded)
            if ratio_bonus >= 0.55:
                key_stats.append("GK carry (4x saves)")
            elif ratio_bonus >= 0.35:
                key_stats.append("Strong GK (3x saves)")
            elif ratio_bonus >= 0.15:
                key_stats.append("Good GK (2x saves)")

        if pos in {"GK", "LB", "CB", "RB"} and goals_conceded == 0:
            key_stats.append("Clean sheet")

        player_scores.append(
            {
                "name": _name(player),
                "position": pos,
                "score": score,
                "stats": key_stats[:4],
            }
        )

    if not player_scores:
        return None

    player_scores.sort(key=lambda x: x["score"], reverse=True)
    return player_scores[0]

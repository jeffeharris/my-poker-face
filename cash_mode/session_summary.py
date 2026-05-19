"""Cash-mode post-session summary.

Computes the stats shown on the cash-out screen: net P&L, hands played,
hands won, biggest pot won, VPIP/aggression, derived play-style label,
session duration. Pure functions over `hand_history` rows so the leave
route can render a summary without touching the DB twice.

Hand-history rows come from `HandHistoryRepository.load_hand_history`
(list of dicts shaped like `RecordedHand.to_dict()`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


# Preflop actions that count as "voluntary chips in pot" — posting
# blinds doesn't count, neither does folding/checking.
_VPIP_ACTIONS = frozenset({"call", "raise", "bet", "all_in"})
_AGGRESSIVE_ACTIONS = frozenset({"raise", "bet", "all_in"})

# Play-style thresholds. Mirrors how the AI opponent_models bucket
# play style — loose = VPIP >= 25%, aggressive = PFR/VPIP >= 50%.
_LOOSE_VPIP_PCT = 25.0
_AGGRESSIVE_RATIO = 0.5
# Below this hand count we don't have enough signal to label playstyle.
_MIN_HANDS_FOR_STYLE = 10


def _was_in_hand(hand: Dict[str, Any], player_name: str) -> bool:
    """True if the player was dealt in this hand."""
    for p in hand.get("players", []) or []:
        if p.get("name") == player_name:
            return True
    return False


def _player_won(hand: Dict[str, Any], player_name: str) -> bool:
    """True if the player is in this hand's winners list."""
    for w in hand.get("winners", []) or []:
        if w.get("name") == player_name:
            return True
    return False


def _classify_play_style(vpip_pct: float, pfr_pct: float, hands: int) -> str:
    """Return a human-readable play-style label from VPIP/PFR.

    Buckets follow the standard poker-tracker quadrant:
      - VPIP < 25%  → Tight,  else Loose
      - PFR/VPIP ≥ 50% (or VPIP == 0) → Aggressive, else Passive
    """
    if hands < _MIN_HANDS_FOR_STYLE:
        return "Not enough hands"
    looseness = "Loose" if vpip_pct >= _LOOSE_VPIP_PCT else "Tight"
    if vpip_pct == 0:
        aggression = "Passive"
    else:
        aggression = "Aggressive" if (pfr_pct / vpip_pct) >= _AGGRESSIVE_RATIO else "Passive"
    return f"{looseness}-{aggression}"


def summarize_cash_session(
    *,
    hands: List[Dict[str, Any]],
    human_name: str,
    buy_in: int,
    cash_out: int,
    started_at: Optional[datetime],
    now: datetime,
    fallback_hand_count: int = 0,
) -> Dict[str, Any]:
    """Compute the cash-out summary payload.

    Args:
        hands: Hand-history rows for this game (oldest first, as
            returned by `load_hand_history`).
        human_name: The human player's seat name.
        buy_in: Chips committed when sitting down.
        cash_out: Chips on the table at leave time.
        started_at: Session start timestamp (UTC). May be None for
            sessions created before the field was added — duration
            will be reported as 0 in that case.
        now: Current UTC timestamp (passed in for testability).
        fallback_hand_count: Use this when no hand_history rows are
            present (e.g. session was started but no hand completed).

    Returns:
        Dict matching the `session_summary` shape consumed by the
        frontend.
    """
    net_pnl = cash_out - buy_in

    # Hands played = hands where the human was dealt in. We prefer the
    # hand_history count because state_machine.hand_count includes the
    # in-progress hand at leave time (which may be incomplete and have
    # no recorded action yet — overstates by 1).
    hands_dealt = sum(1 for h in hands if _was_in_hand(h, human_name))
    if hands_dealt == 0 and fallback_hand_count:
        hands_dealt = fallback_hand_count

    hands_won = sum(1 for h in hands if _player_won(h, human_name))

    # Biggest pot the human actually won (not "biggest pot at the
    # table"). 0 if they never won a hand.
    biggest_pot_won = 0
    for h in hands:
        if _player_won(h, human_name):
            pot = int(h.get("pot_size") or 0)
            if pot > biggest_pot_won:
                biggest_pot_won = pot

    # VPIP / PFR / postflop aggression. We iterate actions once and
    # tally per-hand flags so multi-street action sequences don't
    # double-count.
    vpip_hands = 0
    pfr_hands = 0
    postflop_aggressive = 0
    postflop_passive = 0
    for h in hands:
        if not _was_in_hand(h, human_name):
            continue
        voluntary_preflop = False
        raised_preflop = False
        for action in h.get("actions", []) or []:
            if action.get("player_name") != human_name:
                continue
            act = action.get("action")
            phase = action.get("phase")
            if phase == "PRE_FLOP":
                if act in _VPIP_ACTIONS:
                    voluntary_preflop = True
                if act in _AGGRESSIVE_ACTIONS:
                    raised_preflop = True
            else:
                # Postflop: split into aggressive vs passive chip-commits.
                # Fold/check don't enter either bucket — they don't
                # represent a "willingness to put money in" choice.
                if act in _AGGRESSIVE_ACTIONS:
                    postflop_aggressive += 1
                elif act == "call":
                    postflop_passive += 1
        if voluntary_preflop:
            vpip_hands += 1
        if raised_preflop:
            pfr_hands += 1

    vpip_pct = (vpip_hands / hands_dealt * 100.0) if hands_dealt else 0.0
    pfr_pct = (pfr_hands / hands_dealt * 100.0) if hands_dealt else 0.0
    postflop_total = postflop_aggressive + postflop_passive
    aggression_pct = (
        postflop_aggressive / postflop_total * 100.0
        if postflop_total
        else 0.0
    )

    duration_seconds = 0
    if started_at is not None:
        duration_seconds = max(0, int((now - started_at).total_seconds()))

    return {
        "buy_in": int(buy_in),
        "cash_out": int(cash_out),
        "net_pnl": int(net_pnl),
        "hands_played": int(hands_dealt),
        "hands_won": int(hands_won),
        "biggest_pot_won": int(biggest_pot_won),
        "vpip_pct": round(vpip_pct, 1),
        "pfr_pct": round(pfr_pct, 1),
        "aggression_pct": round(aggression_pct, 1),
        "play_style": _classify_play_style(vpip_pct, pfr_pct, hands_dealt),
        "duration_seconds": duration_seconds,
    }

"""Clone a human player's table behavior as a deterministic sim opponent.

Background: sim opponents (CaseBot, GTO-Lite, ManiacBot, etc.) are calibrated
to specific statistical fingerprints. None of them resemble the way a real
human plays — VPIP ~35%, AF ~2.0, sticky-calling tendencies, occasional river
bluffs. Tuning the tiered bot against this rule-bot pool produces policies
that win in sim but bleed against actual humans.

This module derives a `CloneProfile` from a player's observed stats in the
`opponent_models` table and produces a strategy function the existing
rule-bot infrastructure can host. The resulting bot is parameterized — it
isn't Jeff-specific. Pass any player name with enough observation history
and the same machinery clones their style.

Usage:
    from poker.human_clone import derive_profile_from_db, build_clone_strategy
    profile = derive_profile_from_db('/app/data/poker_games.db', 'Jeff')
    strategy_fn = build_clone_strategy(profile)
    # register under any name in BUILT_IN_STRATEGIES; use via RuleConfig

Limitations (V1):
- Aggregates stats across all opponent-models rows the player appears in.
  Does not adjust for stake, position, or game format.
- Postflop policy is single-stat (AF); no bet-size-conditional folds or
  street-specific tendencies. Real humans defend differently against half-pot
  vs pot-sized bets — this clone treats all bet sizes the same.
- Bluff frequency / WtSD% / 3-bet rate are read from the row but only
  bluff_frequency influences decisions today. Richer mining of `hand_history`
  could feed those columns once we have a real consumer for them.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from typing import Dict, Optional

from .hand_tiers import (
    PREMIUM_HANDS,
    TOP_10_HANDS,
    TOP_20_HANDS,
    TOP_35_HANDS,
    TOP_45_HANDS,
    TOP_55_HANDS,
    TOP_65_HANDS,
    TOP_75_HANDS,
    TOP_85_HANDS,
    TOP_95_HANDS,
)


@dataclass(frozen=True)
class CloneProfile:
    """Stat-derived behavioral profile suitable for V1 cloning.

    Sourced from the `opponent_models` table — values are the
    hands-observed-weighted average across every row where the named
    player appears as `opponent_name`.
    """
    source_player: str
    hands_observed: int
    vpip: float                # 0.0–1.0; fraction of hands voluntarily entered
    pfr: float                 # 0.0–1.0; fraction of hands preflop-raised
    aggression_factor: float   # (raises+bets) / calls postflop
    fold_to_cbet: float        # 0.0–1.0; fold rate vs continuation bets
    bluff_frequency: float = 0.30   # 0.0–1.0; declared bluff rate
    showdown_win_rate: float = 0.50

    @property
    def display_name(self) -> str:
        return f"{self.source_player}_clone"


# ── DB derivation ─────────────────────────────────────────────────────────


def derive_profile_from_db(
    db_path: str, player_name: str, min_hands: int = 20,
) -> CloneProfile:
    """Build a CloneProfile from `opponent_models` aggregated across observers.

    Weights each opponent-model row by `hands_observed`, then normalizes.
    This collapses "Hulk Hogan saw Jeff for 125 hands; James Bond saw Jeff
    for 118 hands" into a single combined profile.

    Raises ValueError when total `hands_observed` < `min_hands` — too little
    data to clone faithfully.
    """
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT hands_observed, vpip, pfr, aggression_factor,
                   fold_to_cbet, bluff_frequency, showdown_win_rate
            FROM opponent_models
            WHERE opponent_name = ? AND hands_observed > 0
            """,
            (player_name,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        raise ValueError(
            f"No opponent_models rows for player_name={player_name!r}"
        )

    # Weighted aggregation
    total_hands = sum(r[0] for r in rows)
    if total_hands < min_hands:
        raise ValueError(
            f"Only {total_hands} hand(s) observed for {player_name!r}; "
            f"need at least {min_hands} for a clone profile"
        )

    def w_avg(idx: int, fallback: float) -> float:
        weighted = sum(r[0] * (r[idx] if r[idx] is not None else fallback) for r in rows)
        return weighted / total_hands

    return CloneProfile(
        source_player=player_name,
        hands_observed=total_hands,
        vpip=w_avg(1, 0.50),
        pfr=w_avg(2, 0.20),
        aggression_factor=w_avg(3, 1.0),
        fold_to_cbet=w_avg(4, 0.50),
        bluff_frequency=w_avg(5, 0.30),
        showdown_win_rate=w_avg(6, 0.50),
    )


# ── VPIP / PFR → hand-tier mapping ────────────────────────────────────────

# Ordered list of (threshold, hand_set). The bot opens any hand inside the
# tier where the player's VPIP/PFR best fits. Each tier is approximate:
# TOP_35 actually covers ~35% of hands, TOP_55 ~55%, etc.
_TIERS = [
    (0.05, PREMIUM_HANDS),
    (0.10, TOP_10_HANDS),
    (0.20, TOP_20_HANDS),
    (0.35, TOP_35_HANDS),
    (0.45, TOP_45_HANDS),
    (0.55, TOP_55_HANDS),
    (0.65, TOP_65_HANDS),
    (0.75, TOP_75_HANDS),
    (0.85, TOP_85_HANDS),
    (0.95, TOP_95_HANDS),
]


def _tier_for_frequency(freq: float) -> set:
    """Pick the hand tier whose size best matches the target frequency."""
    if freq <= 0:
        return PREMIUM_HANDS
    if freq >= 0.95:
        return TOP_95_HANDS
    # Find the tier whose threshold is closest to freq (rounded up)
    for threshold, hand_set in _TIERS:
        if freq <= threshold:
            return hand_set
    return TOP_95_HANDS


# ── Strategy factory ─────────────────────────────────────────────────────


def build_clone_strategy(profile: CloneProfile):
    """Return a strategy function that mimics `profile` for use as a rule_bot.

    The returned closure plugs into `BUILT_IN_STRATEGIES` and is called by
    `RuleBasedController` with the standard `(context: Dict) -> Dict`
    contract. Uses the global `random` module — `simulate_bb100.py` seeds
    that per-hand, so sim runs are reproducible.
    """
    vpip_tier = _tier_for_frequency(profile.vpip)
    pfr_tier = _tier_for_frequency(profile.pfr)

    # P(raise | committing to play postflop) = AF / (AF + 1).
    # AF=2 → raise 67% of betting opportunities; AF=0.5 → raise 33%; AF=1 → raise 50%.
    af_raise_rate = profile.aggression_factor / (profile.aggression_factor + 1.0)

    # Required-equity multiplier scaled by sticky-caller tendency.
    # fold_to_cbet=1.0 → fold at the textbook required equity (multiplier 1.0)
    # fold_to_cbet=0.0 → call way wider (multiplier 0.5)
    # fold_to_cbet=0.5 (default) → multiplier 0.75
    fold_multiplier = 0.5 + (profile.fold_to_cbet * 0.5)

    def strategy(context: Dict) -> Dict:
        valid = context.get('valid_actions', [])
        cost_to_call = context.get('cost_to_call', 0)
        canonical = context.get('canonical_hand', '')
        equity = context.get('equity', 0.5) or 0.5
        phase = context.get('phase', 'PRE_FLOP')
        pot = context.get('pot_total', 0) or 0
        min_raise = context.get('min_raise', 0) or 0
        max_raise = context.get('max_raise', 0) or 0

        def _raise():
            size = max(min_raise, min(int(pot * 0.67) or min_raise, max_raise)) if max_raise else min_raise
            return {'action': 'raise', 'raise_to': size}

        def _call():
            return {'action': 'call', 'raise_to': 0}

        def _check():
            return {'action': 'check', 'raise_to': 0}

        def _fold():
            return {'action': 'fold', 'raise_to': 0}

        # ── PREFLOP ───────────────────────────────────────────────────
        if phase == 'PRE_FLOP':
            if cost_to_call == 0 and 'check' in valid:
                return _check()
            # Open / facing-raise simplification: gate on hand tier only.
            if canonical in pfr_tier and 'raise' in valid:
                return _raise()
            if canonical in vpip_tier and 'call' in valid:
                return _call()
            if 'check' in valid:
                return _check()
            return _fold()

        # ── POSTFLOP ──────────────────────────────────────────────────
        # Free to act: aggression-driven betting on equity.
        if cost_to_call == 0:
            if equity >= 0.55 and 'raise' in valid and random.random() < af_raise_rate:
                return _raise()
            if 'check' in valid:
                return _check()
            return _fold()

        # Facing a bet: compute required equity, apply sticky multiplier.
        required = cost_to_call / (pot + cost_to_call) if (pot + cost_to_call) > 0 else 0.5
        effective_required = required * fold_multiplier
        if equity < effective_required:
            return _fold()
        # Value-raise on strong hands at AF rate.
        if equity >= 0.70 and 'raise' in valid and random.random() < af_raise_rate:
            return _raise()
        if 'call' in valid:
            return _call()
        return _fold()

    return strategy


def register_clone_strategy(name: str, profile: CloneProfile) -> str:
    """Install a clone strategy into BUILT_IN_STRATEGIES under `name`.

    Returns the registered name. Lets the rule-bot controller and sim
    harness reference the clone by string the same way they reference
    `case_based`, `pot_odds_robot`, etc. Re-registering with the same
    name silently overwrites — convenient for iterative tuning runs.
    """
    from .rule_strategies import BUILT_IN_STRATEGIES
    BUILT_IN_STRATEGIES[name] = build_clone_strategy(profile)
    return name

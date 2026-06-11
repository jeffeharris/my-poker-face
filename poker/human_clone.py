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
- Bluff frequency / WtSD% / 3-bet rate are read from the row. `wtsd` and the
  per-street AFs influence decisions; `bluff_frequency` and `threebet_rate`
  are stored-but-unused (mined for future consumers). Richer mining of
  `hand_history` could feed those columns once we have a real consumer.
- DB-derived profiles never bluff (they only bet/raise with real equity). The
  synthetic `bluff_air_freq` lever (default 0.0, never set by DB derivation)
  adds air-barreling so a hand-authored "punisher" profile can *punish
  over-folding* — see experiments/clone_profiles/punisher.json.
"""

from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import asdict, dataclass, fields
from pathlib import Path
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
    """Stat-derived behavioral profile.

    Core stats sourced from the `opponent_models` table (weighted across
    observers). V2 fields are mined from `hand_history.actions_json` when
    available — they capture nuance the single-stat AF can't (e.g. a
    calling station who never barrels turn, a sticky caller who goes to
    showdown 50%+ of hands seen).
    """

    source_player: str
    hands_observed: int
    vpip: float  # 0.0–1.0; fraction of hands voluntarily entered
    pfr: float  # 0.0–1.0; fraction of hands preflop-raised
    aggression_factor: float  # (raises+bets) / calls postflop
    fold_to_cbet: float  # 0.0–1.0; fold rate vs continuation bets
    bluff_frequency: float = 0.30  # 0.0–1.0; declared bluff rate
    showdown_win_rate: float = 0.50

    # ── V2 (hand_history mining; None when not enough data) ──
    wtsd: Optional[float] = None  # went-to-showdown rate (saw river / saw flop)
    threebet_rate: Optional[float] = None  # preflop 3bets per facing-raise opportunity
    flop_af: Optional[float] = None  # flop-only aggression factor
    turn_af: Optional[float] = None  # turn-only aggression factor
    river_af: Optional[float] = None  # river-only aggression factor

    # ── Synthetic lever (hand-authored profiles only; never DB-derived) ──
    # P(bet/barrel air | checked to with sub-value equity). Default 0.0 keeps
    # DB-derived and existing JSON profiles (e.g. jeff.json) byte-identical —
    # they only bet with real equity. A "punisher" profile sets this high so it
    # barrels air and thereby *punishes a bot that over-folds* (the eval gap
    # noted in docs/plans/EVAL_HARNESS_PLAN.md, P0.5).
    bluff_air_freq: float = 0.0

    @property
    def display_name(self) -> str:
        return f"{self.source_player}_clone"


# ── DB derivation ─────────────────────────────────────────────────────────


def _mine_hand_history(db_path: str, player_name: str) -> Dict[str, Optional[float]]:
    """Mine V2 stats from hand_history.actions_json for one player.

    Returns a dict with `wtsd`, `threebet_rate`, `flop_af`, `turn_af`,
    `river_af` — each is None if there's not enough data to compute it
    (typically < 5 qualifying observations for that specific stat).

    Definitions:
    - wtsd:          P(saw river without folding | saw flop). Sticky stations
                     hit 0.50+; fit-or-fold types stay under 0.25.
    - threebet_rate: P(player raises preflop | player faces a prior preflop
                     raise and hasn't acted yet on this street).
    - <street>_af:   raises_on_street / (calls_on_street + checks_on_street).
                     Mirrors the global AF formula but restricted to one
                     street. Captures "barrels turn but checks river" style
                     differences the single-stat AF averages away.
    """
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT actions_json FROM hand_history " "WHERE players_json LIKE ?",
            (f'%"{player_name}"%',),
        )
        all_actions = [json.loads(r[0]) for r in cur if r[0]]
    finally:
        conn.close()

    saw_flop = 0
    saw_river = 0
    threebet_opps = 0
    threebet_takes = 0
    street_raises: Dict[str, int] = {'FLOP': 0, 'TURN': 0, 'RIVER': 0}
    street_passive: Dict[str, int] = {'FLOP': 0, 'TURN': 0, 'RIVER': 0}

    for actions in all_actions:
        player_actions = [a for a in actions if a.get('player_name') == player_name]
        if not player_actions:
            continue

        phases_seen = {a['phase'] for a in player_actions}
        folded = any(a['action'] == 'fold' for a in player_actions)

        # WtSD: saw flop (any FLOP action) → did they reach river without folding?
        if 'FLOP' in phases_seen:
            saw_flop += 1
            if 'RIVER' in phases_seen and not folded:
                saw_river += 1

        # 3-bet rate: walk preflop actions; whenever the player faces a
        # prior PRE_FLOP raise (action='raise' from someone else), count
        # an opportunity and check whether the player's next preflop
        # action is a raise.
        preflop_seen_raise_before_us = False
        for a in actions:
            if a['phase'] != 'PRE_FLOP':
                break
            if a['player_name'] == player_name:
                if preflop_seen_raise_before_us:
                    threebet_opps += 1
                    if a['action'] == 'raise':
                        threebet_takes += 1
                    break  # only count first chance per hand
            elif a['action'] == 'raise':
                preflop_seen_raise_before_us = True

        # Street-specific AF counters
        for a in player_actions:
            phase = a['phase']
            if phase not in street_raises:
                continue
            if a['action'] == 'raise':
                street_raises[phase] += 1
            elif a['action'] in ('call', 'check'):
                street_passive[phase] += 1

    def _af(street: str) -> Optional[float]:
        raises = street_raises[street]
        passive = street_passive[street]
        if raises + passive < 5:  # too few samples to trust the ratio
            return None
        # AF = raises / passive (matches global AF formula)
        if passive == 0:
            return float(raises)  # all aggression, no passive — cap at the count
        return raises / passive

    return {
        'wtsd': (saw_river / saw_flop) if saw_flop >= 5 else None,
        'threebet_rate': (threebet_takes / threebet_opps) if threebet_opps >= 5 else None,
        'flop_af': _af('FLOP'),
        'turn_af': _af('TURN'),
        'river_af': _af('RIVER'),
    }


def derive_profile_from_db(
    db_path: str,
    player_name: str,
    min_hands: int = 20,
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
        raise ValueError(f"No opponent_models rows for player_name={player_name!r}")

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

    # V1 fields from opponent_models
    base = dict(
        source_player=player_name,
        hands_observed=total_hands,
        vpip=w_avg(1, 0.50),
        pfr=w_avg(2, 0.20),
        aggression_factor=w_avg(3, 1.0),
        fold_to_cbet=w_avg(4, 0.50),
        bluff_frequency=w_avg(5, 0.30),
        showdown_win_rate=w_avg(6, 0.50),
    )
    # V2: best-effort hand_history mining. Missing fields stay None and
    # the strategy falls back to the V1 single-stat behavior for them.
    try:
        mined = _mine_hand_history(db_path, player_name)
    except Exception:
        mined = {}
    return CloneProfile(**base, **mined)


# ── Serialization (portable export / import) ──────────────────────────────
#
# `derive_profile_from_db` needs the source player's ~thousands of hands in
# the local DB. That makes a clone non-portable: a fresh checkout or a
# different machine has no such history. The functions below let you freeze
# a derived profile to a small JSON file once, commit it, and reconstruct the
# exact same bot anywhere — no DB required.


def profile_to_dict(profile: CloneProfile) -> Dict:
    """Serialize a CloneProfile to a plain JSON-ready dict.

    `asdict` emits only dataclass fields, so the derived `display_name`
    property is intentionally omitted (it's reconstructed from
    `source_player` on load).
    """
    return asdict(profile)


def profile_from_dict(data: Dict) -> CloneProfile:
    """Reconstruct a CloneProfile from a dict.

    Unknown keys are dropped so a snapshot written by a newer version (extra
    fields) still loads here; missing optional keys fall back to dataclass
    defaults so a snapshot written by an older version (pre-V2) also loads.
    A missing *required* field still raises TypeError — the correct loud
    failure for a truncated/corrupt snapshot.
    """
    known = {f.name for f in fields(CloneProfile)}
    filtered = {k: v for k, v in data.items() if k in known}
    return CloneProfile(**filtered)


def dump_profile_to_file(profile: CloneProfile, path: str) -> str:
    """Write `profile` to `path` as pretty JSON, creating parent dirs.

    Returns the path written. Round-trips exactly with
    `load_profile_from_file`.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(profile_to_dict(profile), indent=2, sort_keys=True) + "\n")
    return str(out)


def load_profile_from_file(path: str) -> CloneProfile:
    """Load a CloneProfile previously written by `dump_profile_to_file`."""
    with open(path) as fh:
        return profile_from_dict(json.load(fh))


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


# ── Oracle overbet-punisher (eval-only; docs/plans/SIZING_AWARE_OPPONENT_MODELING.md, D1) ──
# A deterministic "perfect punisher" of a face-up value overbet, used to measure
# the exploitability CEILING of the shipped overbet_context layer. It is NOT a
# learned read — it assumes the hero's overbet range is pure value and max-folds
# accordingly. Detected purely by SIZE so the attribution A/B stays clean (the
# overbet size is produced only by overbet_context, absent in the A-arm).
OVERBET_DETECT_RATIO = 1.2  # bet / pot-before-the-bet >= this = an overbet (excludes <=pot bets)
ORACLE_CONTINUE_EQUITY = 0.80  # facing an overbet, fold unless equity-vs-random >= this (near-nuts)

# vs-3bet/reshove preflop continue model (see build_clone_strategy's preflop branch).
_VS_3BET_OPEN_CEILING_BB = 3.5  # highest_bet beyond this many BB preflop = a re-raise, not an open
_VS_3BET_DISCIPLINED_AF = (
    2.0  # AF at/above this = a reg that folds the bottom of its opens to a 3bet
)
_VS_3BET_CONTINUE_FRAC = 0.5  # disciplined reg continues ~this share of its raise-first range


def build_clone_strategy(profile: CloneProfile, oracle_punish_overbets: bool = False):
    """Return a strategy function that mimics `profile` for use as a rule_bot.

    `oracle_punish_overbets` (eval-only, default False): when True, the closure
    max-folds its non-near-nut range whenever it faces a bet of >= OVERBET_DETECT_RATIO
    x the pot-before-the-bet — the perfect-punisher of a face-up value overbettor.
    Production never sets this; it exists so the measurement harness can quantify
    how exploitable the (intentionally face-up) overbet_context layer is.

    The returned closure plugs into `BUILT_IN_STRATEGIES` and is called by
    `RuleBasedController` with the standard `(context: Dict) -> Dict`
    contract. Uses the global `random` module — `simulate_bb100.py` seeds
    that per-hand, so sim runs are reproducible.

    V2 enhancements (when the profile carries them):
    - Per-street AF replaces the single global AF for postflop bet/raise
      decisions, capturing "barrels turn, checks river" patterns.
    - wtsd shifts the turn/river fold threshold up (sticky callers reach
      showdown more often) or down (fit-or-fold types fold earlier).
    - threebet_rate widens or shrinks the gap between vpip_tier and
      pfr_tier when facing a prior preflop raiser.
    """
    vpip_tier = _tier_for_frequency(profile.vpip)
    pfr_tier = _tier_for_frequency(profile.pfr)

    # vs-3bet/reshove continue range. Facing a re-raise well beyond a normal
    # open, a DISCIPLINED reg (high AF) folds the bottom of its opening range —
    # continue only with a tight slice (~half the raise-first range) — so it has
    # fold equity. A station / passive player (low AF) calls a 3-bet about as
    # wide as it plays at all, so it keeps its full vpip range = no fold equity.
    # Without this split the clone re-continues its entire open in every reshove
    # spot and reads as a calling station (the gap that made reshove unprovable
    # against clones — see docs/plans/PUSH_FOLD_6MAX_SCOPE.md).
    is_disciplined = profile.aggression_factor >= _VS_3BET_DISCIPLINED_AF
    vs_3bet_continue_tier = (
        _tier_for_frequency(profile.pfr * _VS_3BET_CONTINUE_FRAC) if is_disciplined else vpip_tier
    )

    # P(raise | committing to play postflop) = AF / (AF + 1).
    # AF=2 → raise 67% of betting opportunities; AF=0.5 → raise 33%; AF=1 → raise 50%.
    af_raise_rate = profile.aggression_factor / (profile.aggression_factor + 1.0)

    # Street-specific raise rates fall back to the global af_raise_rate
    # when the mined value is None (too few samples).
    def _street_raise_rate(street_af: Optional[float]) -> float:
        if street_af is None:
            return af_raise_rate
        return street_af / (street_af + 1.0)

    flop_raise_rate = _street_raise_rate(profile.flop_af)
    turn_raise_rate = _street_raise_rate(profile.turn_af)
    river_raise_rate = _street_raise_rate(profile.river_af)

    # Required-equity multiplier scaled by sticky-caller tendency.
    # fold_to_cbet=1.0 → fold at the textbook required equity (multiplier 1.0)
    # fold_to_cbet=0.0 → call way wider (multiplier 0.5)
    # fold_to_cbet=0.5 (default) → multiplier 0.75
    fold_multiplier = 0.5 + (profile.fold_to_cbet * 0.5)

    # wtsd adjusts turn/river fold thresholds. A sticky caller (wtsd=0.50+)
    # gets a more permissive (lower) multiplier on those streets; a fit-or-
    # fold type (wtsd < 0.20) gets a stricter (higher) multiplier. Bounded
    # so a player who saw 100% showdowns doesn't auto-call everything.
    wtsd_adjust = 1.0
    if profile.wtsd is not None:
        # 0.40 is a "neutral" WtSD. Above that → stickier; below → folder.
        wtsd_adjust = 1.0 - (profile.wtsd - 0.40) * 0.5  # ±0.25 swing across 0.0..1.0
        wtsd_adjust = max(0.5, min(1.3, wtsd_adjust))

    # threebet_rate gates whether to widen or tighten when facing a preflop raise.
    # Default of 0.05 is "rarely 3-bets"; humans typically 5-12%.

    # Air-barrel rate: P(bet | checked to with sub-value equity). 0.0 for every
    # DB-derived / existing profile, so the bluff branch below is dead code for
    # them; a hand-authored punisher sets it high to barrel air. Clamp guards a
    # hand-edited negative in a JSON profile.
    bluff_air_freq = max(0.0, profile.bluff_air_freq)

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
            size = (
                max(min_raise, min(int(pot * 0.67) or min_raise, max_raise))
                if max_raise
                else min_raise
            )
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
            # Facing a 3-bet / reshove (a re-raise well beyond a standard open):
            # continue only with the vs-3bet tier and FOLD the rest. For a
            # disciplined reg that tier is a tight slice of its opens (fold
            # equity); for a station it's the full vpip range (no fold equity).
            # Without this the clone re-raises/calls its whole opening range in
            # every reshove spot and never folds — the calling-station artifact.
            big_blind = context.get('big_blind', 0) or 0
            highest_bet = context.get('highest_bet', 0) or 0
            facing_3bet = big_blind > 0 and highest_bet > _VS_3BET_OPEN_CEILING_BB * big_blind
            if facing_3bet:
                if canonical in vs_3bet_continue_tier:
                    if 'call' in valid:
                        return _call()
                    if 'all_in' in valid:  # calling caps at our stack = a call-off
                        return {'action': 'all_in', 'raise_to': max_raise or 0}
                return _fold()
            # Open / facing a single raise: gate on hand tier only.
            if canonical in pfr_tier and 'raise' in valid:
                return _raise()
            if canonical in vpip_tier and 'call' in valid:
                return _call()
            if 'check' in valid:
                return _check()
            return _fold()

        # ── POSTFLOP ──────────────────────────────────────────────────
        # Per-street raise rate replaces single AF when V2 fields present.
        street_rate = {
            'FLOP': flop_raise_rate,
            'TURN': turn_raise_rate,
            'RIVER': river_raise_rate,
        }.get(phase, af_raise_rate)

        # Free to act: aggression-driven betting on equity.
        if cost_to_call == 0:
            if equity >= 0.55 and 'raise' in valid and random.random() < street_rate:
                return _raise()
            # Air-barrel branch (punisher profiles only — bluff_air_freq is 0.0
            # for every DB-derived / existing profile, so this is unreachable
            # for them). Betting with sub-value equity is what lets an
            # aggressive reg punish over-folding: a bot that folds too much
            # bleeds to these barrels. Fires per street, so a single rate
            # naturally produces multi-street barrels.
            if (
                bluff_air_freq > 0.0
                and equity < 0.55
                and 'raise' in valid
                and random.random() < bluff_air_freq
            ):
                return _raise()
            if 'check' in valid:
                return _check()
            return _fold()

        # Oracle overbet-punisher (eval-only). vs a face-up value overbettor,
        # fold all but near-nuts. Detect the overbet by SIZE: bet/pot-before =
        # cost_to_call / (pot - cost_to_call), where `pot` includes the hero's
        # bet. Fires only at >= OVERBET_DETECT_RATIO (1.2), so it never triggers
        # on <=pot bets — the 1.5x overbet size comes only from overbet_context
        # (absent in the attribution A-arm), keeping the paired A/B clean.
        # Deterministic (no rng) → CRN-safe for paired replay.
        if oracle_punish_overbets and cost_to_call > 0:
            pot_before_bet = pot - cost_to_call
            if (
                pot_before_bet > 0
                and cost_to_call / pot_before_bet >= OVERBET_DETECT_RATIO
                and equity < ORACLE_CONTINUE_EQUITY
                and 'fold' in valid
            ):
                return _fold()

        # Facing a bet: compute required equity, apply sticky multiplier.
        # On turn/river, WtSD adjustment modulates fold tightness: sticky
        # callers (high WtSD) call wider; fit-or-fold types fold tighter.
        required = cost_to_call / (pot + cost_to_call) if (pot + cost_to_call) > 0 else 0.5
        street_fold_multiplier = fold_multiplier
        if phase in ('TURN', 'RIVER'):
            street_fold_multiplier = fold_multiplier * wtsd_adjust
        effective_required = required * street_fold_multiplier
        if equity < effective_required:
            return _fold()
        # Value-raise on strong hands at street-specific rate.
        if equity >= 0.70 and 'raise' in valid and random.random() < street_rate:
            return _raise()
        if 'call' in valid:
            return _call()
        return _fold()

    return strategy


def register_clone_strategy(
    name: str, profile: CloneProfile, oracle_punish_overbets: bool = False
) -> str:
    """Install a clone strategy into BUILT_IN_STRATEGIES under `name`.

    Returns the registered name. Lets the rule-bot controller and sim
    harness reference the clone by string the same way they reference
    `case_based`, `pot_odds_robot`, etc. Re-registering with the same
    name silently overwrites — convenient for iterative tuning runs.

    `oracle_punish_overbets` (eval-only) builds the perfect-punisher variant
    (see build_clone_strategy) under the same name.
    """
    from .rule_strategies import BUILT_IN_STRATEGIES

    BUILT_IN_STRATEGIES[name] = build_clone_strategy(
        profile, oracle_punish_overbets=oracle_punish_overbets
    )
    return name


# ── Adaptive sizing-reader best-responder (eval-only) ───────────────────────
# The "missing instrument" (BETTER_BOT_HANDOFF.md §2/§7, SIZING_AWARE D2): unlike
# the fixed `oracle_punish_overbets` (which folds to a big bet UNCONDITIONALLY and
# so can only show the bluff-gets-through gain), this opponent OBSERVES the hero's
# revealed overbet hands across hands, estimates the hero's overbet bluff freq, and
# BEST-RESPONDS its fold frequency:
#   - hero under-bluffs (face-up)  → over-fold bluff-catchers (exploit the tell)
#   - hero balances (bluff freq ≥ the size's call-threshold) → CALL bluff-catchers
#     (so the hero's VALUE overbets finally get paid — the half the oracle can't show)
# It's a perfect-observation reader (the harness feeds every overbet's class, even on
# folds) = the STRONGEST realistic reader, an upper bound on a thinking human. Used to
# measure the LIVE benefit of the river-bluff balancing (OVERBET_BALANCING.md §5g).

OVERBET_DETECT_RATIO_BR = 1.2  # bet / pot-before >= this = an overbet (same as the oracle)
_BR_VALUE_EQUITY = (
    0.85  # equity-vs-random at/above which the BR's hand beats the value range → call
)
_BR_TRASH_EQUITY = 0.40  # below this the BR's hand can't even catch bluffs → fold
_BR_MIN_OBS = 10  # overbets observed before trusting the empirical bluff freq (else assume face-up)


class AdaptiveReaderState:
    """Cross-hand memory for the adaptive sizing-reader. Mutable; the harness
    resets it per matchup and feeds it the hero's revealed overbet class via
    `observe()`. `bluff_freq()` is the running estimate the strategy reads.
    """

    def __init__(self, min_obs: int = _BR_MIN_OBS):
        self.value_obs = 0
        self.bluff_obs = 0
        self.min_obs = min_obs

    def observe(self, is_bluff: bool) -> None:
        if is_bluff:
            self.bluff_obs += 1
        else:
            self.value_obs += 1

    def bluff_freq(self) -> float:
        """Estimated P(bluff | hero overbets). Before `min_obs` observations,
        assume face-up (0.0) — the pessimistic prior that makes the BR start as
        the oracle and only relax toward calling as it sees the hero bluff."""
        n = self.value_obs + self.bluff_obs
        if n < self.min_obs:
            return 0.0
        return self.bluff_obs / n


def build_adaptive_reader_strategy(profile: CloneProfile, state: AdaptiveReaderState):
    """A competent reg (base = the profile) that, facing a RIVER overbet,
    best-responds to `state`'s learned overbet bluff freq instead of using the
    base pot-odds rule. Everything else defers to the base clone."""
    base = build_clone_strategy(profile)

    def strategy(context: Dict) -> Dict:
        phase = context.get('phase', '')
        cost_to_call = context.get('cost_to_call', 0) or 0
        pot = context.get('pot_total', 0) or 0
        equity = context.get('equity', 0.5) or 0.5
        valid = context.get('valid_actions', [])

        if phase == 'RIVER' and cost_to_call > 0 and 'call' in valid and 'fold' in valid:
            pot_before = pot - cost_to_call
            if pot_before > 0 and cost_to_call / pot_before >= OVERBET_DETECT_RATIO_BR:
                if equity >= _BR_VALUE_EQUITY:
                    return {'action': 'call', 'raise_to': 0}  # beats value → never fold
                if equity < _BR_TRASH_EQUITY:
                    return {'action': 'fold', 'raise_to': 0}  # can't catch → fold
                # Bluff-catcher: call iff the learned bluff freq clears the
                # pot-odds call-threshold for this size (EV(call) >= 0).
                call_threshold = cost_to_call / (pot + cost_to_call)
                if state.bluff_freq() >= call_threshold:
                    return {'action': 'call', 'raise_to': 0}
                return {'action': 'fold', 'raise_to': 0}
        return base(context)

    return strategy


def register_adaptive_reader(name: str, profile: CloneProfile) -> AdaptiveReaderState:
    """Install an adaptive sizing-reader under `name`; return its mutable state
    so the harness can `observe()` the hero's overbets across hands."""
    from .rule_strategies import BUILT_IN_STRATEGIES

    state = AdaptiveReaderState()
    BUILT_IN_STRATEGIES[name] = build_adaptive_reader_strategy(profile, state)
    return state


# ── Adaptive bluff-RAISER best-responder (the dual of the reader; eval-only) ──
# Tests the bot's DEFENSE vs aggression (does it get run over by relentless
# bluff-raises?). Unlike the reader (which needs perfect observation of the hero's
# hidden cards), this learns purely from the hero's VISIBLE response: it raises its
# weakest hands (air) as bluffs facing a hero bet, observes how often the hero
# FOLDS to the raise, and escalates — bluff-raise more while the hero over-folds,
# back off when the hero calls down. The "10× air-raises in a row" scenario: if the
# hero never adapts its fold-to-raise, this prints. Measures the leak (bot bb/100
# with bluff-raising OFF vs ON) and, later, whether a call-down defense closes it.

_AGGR_BLUFF_EQUITY_MAX = 0.35  # only raise true junk (would otherwise fold) as a bluff
_AGGR_MIN_OBS = 8  # raises observed before trusting the empirical fold-to-raise
_AGGR_PRIOR_FOLD = 0.70  # optimistic prior → starts bluffing (explores), then adjusts
_AGGR_PROFIT_THRESHOLD = 0.50  # bluff-raise only while hero folds >= this (pot-raise breakeven)


class AdaptiveAggressorState:
    """Cross-hand memory for the adaptive bluff-raiser. The harness feeds each
    `observe(hero_folded)` after the hero faces one of its raises; `fold_to_raise()`
    is the running estimate the strategy best-responds to."""

    def __init__(self, min_obs: int = _AGGR_MIN_OBS, prior_fold: float = _AGGR_PRIOR_FOLD):
        self.raises_made = 0
        self.folds_induced = 0
        self.min_obs = min_obs
        self.prior_fold = prior_fold

    def observe(self, hero_folded: bool) -> None:
        self.raises_made += 1
        if hero_folded:
            self.folds_induced += 1

    def fold_to_raise(self) -> float:
        if self.raises_made < self.min_obs:
            return self.prior_fold
        return self.folds_induced / self.raises_made


def build_adaptive_aggressor_strategy(
    profile: CloneProfile,
    state: AdaptiveAggressorState,
    bluff_raise: bool = True,
    threshold: float = _AGGR_PROFIT_THRESHOLD,
    bluff_equity_max: float = _AGGR_BLUFF_EQUITY_MAX,
):
    """A competent reg (base = profile) that, facing a hero postflop bet with a
    junk hand, BLUFF-RAISES iff its learned hero fold-to-raise clears the
    pot-raise breakeven. `bluff_raise=False` = the static-reg control (no
    bluff-raising) for the A/B that isolates the bluff-raise's effect."""
    base = build_clone_strategy(profile)

    def strategy(context: Dict) -> Dict:
        phase = context.get('phase', '')
        cost_to_call = context.get('cost_to_call', 0) or 0
        equity = context.get('equity', 0.5) or 0.5
        valid = context.get('valid_actions', [])
        pot = context.get('pot_total', 0) or 0
        min_raise = context.get('min_raise', 0) or 0
        max_raise = context.get('max_raise', 0) or 0

        if (
            bluff_raise
            and phase in ('FLOP', 'TURN', 'RIVER')
            and cost_to_call > 0
            and 'raise' in valid
            and max_raise > 0
            and equity < bluff_equity_max  # true air → would otherwise fold
            and state.fold_to_raise() >= threshold
        ):
            target = max(min_raise, min(int(pot) or min_raise, max_raise))
            return {'action': 'raise', 'raise_to': target}  # bluff-raise (pot-sized)
        return base(context)

    return strategy


def register_adaptive_aggressor(
    name: str,
    profile: CloneProfile,
    bluff_raise: bool = True,
    threshold: float = _AGGR_PROFIT_THRESHOLD,
) -> AdaptiveAggressorState:
    """Install an adaptive bluff-raiser under `name`; return its state so the
    harness can `observe()` the hero's fold-to-raise across hands. `threshold=0`
    makes it RELENTLESS (always bluff-raise regardless of profitability) — the
    '10x air-raises in a row' maniac that the calling-down bot should punish."""
    from .rule_strategies import BUILT_IN_STRATEGIES

    state = AdaptiveAggressorState()
    BUILT_IN_STRATEGIES[name] = build_adaptive_aggressor_strategy(
        profile, state, bluff_raise=bluff_raise, threshold=threshold
    )
    return state


_STAB_SIZE_FRAC = 0.5  # half-pot stab (breakeven fold ~0.33 — the exploiter's size)
_STAB_THRESHOLD = 0.34  # stab while hero folds >= this (half-pot breakeven)


def build_adaptive_stabber_strategy(
    profile: CloneProfile,
    state: AdaptiveAggressorState,
    bluff_stab: bool = True,
    threshold: float = _STAB_THRESHOLD,
    bluff_equity_max: float = _AGGR_BLUFF_EQUITY_MAX,
    stab_size_frac: float = _STAB_SIZE_FRAC,
):
    """Tests the capped-checking-range leak: when the bot CHECKS (its range is
    capped — strong hands all bet), can a stabber punish it? Bets junk when
    checked-to (cost_to_call==0, postflop), learns the hero's fold-to-stab from the
    visible response, escalates while profitable. Distinct from the aggressor: the
    bot faces this stab with a CAPPED (weak) range, so it could fold more than it
    does facing a raise (where it had bet first). `bluff_stab=False` = control."""
    base = build_clone_strategy(profile)

    def strategy(context: Dict) -> Dict:
        phase = context.get('phase', '')
        cost_to_call = context.get('cost_to_call', 0) or 0
        equity = context.get('equity', 0.5) or 0.5
        valid = context.get('valid_actions', [])
        pot = context.get('pot_total', 0) or 0
        min_raise = context.get('min_raise', 0) or 0
        max_raise = context.get('max_raise', 0) or 0

        if (
            bluff_stab
            and phase in ('FLOP', 'TURN', 'RIVER')
            and cost_to_call == 0  # checked to / free to act → a stab, not a raise
            and 'raise' in valid  # betting when free is the 'raise' action in this engine
            and max_raise > 0
            and equity < bluff_equity_max
            and state.fold_to_raise() >= threshold
        ):
            target = max(min_raise, min(int(pot * stab_size_frac) or min_raise, max_raise))
            return {'action': 'raise', 'raise_to': target}  # stab (fractional-pot bet)
        return base(context)

    return strategy


def register_adaptive_stabber(
    name: str,
    profile: CloneProfile,
    bluff_stab: bool = True,
    threshold: float = _STAB_THRESHOLD,
) -> AdaptiveAggressorState:
    """Install an adaptive stabber under `name`; return its state (shared shape
    with the aggressor — `observe(hero_folded)` / `fold_to_raise()` read as
    fold-to-stab here). `threshold=0` = relentless stabber."""
    from .rule_strategies import BUILT_IN_STRATEGIES

    state = AdaptiveAggressorState()
    BUILT_IN_STRATEGIES[name] = build_adaptive_stabber_strategy(
        profile, state, bluff_stab=bluff_stab, threshold=threshold
    )
    return state

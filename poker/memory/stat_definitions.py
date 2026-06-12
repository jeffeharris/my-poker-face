"""Source of truth for opponent-stat *definitions* — the formulas and the
counter rules, as pure functions with no state.

Why this module exists
-----------------------
The same family of opponent stats (VPIP, PFR, AF, postflop AF, fold-to-cbet,
call-rate, WTSD, saw-flop, per-street AF, per-opportunity VPIP/PFR, limp-rate)
used to be hand-implemented at ~6 sites with divergent denominators, and they
drifted. That drift repeatedly blocked exploitation-detector work (authored
clone stats ≠ live-observed; an all-folder reading the same call-rate as a
station). This module is the one place the *definition* of each stat lives, so
a change here changes the definition everywhere. Design + evidence:
`docs/technical/OPPONENT_STAT_SOURCE_OF_TRUTH.md`.

Scope (deliberately narrow): this owns the **formulas** (numerator/denominator)
and the **event/counter rules** ("what counts as a voluntary action / saw-flop").
It does NOT own storage, lifecycle, sample-size gates, or neutral-prior defaults
— those are legitimately per-consumer and stay at the call site. Consumers pass
in counts and gate the sample themselves; these functions just divide the way
everyone agreed to divide.

The action vocabulary
---------------------
The live game/web path can emit a postflop ``'bet'`` (LLM/chaos bots, humans);
the tiered-bot / sim / review paths never do (they emit ``'raise'`` for any
wager — see `poker/bounded_options.py`). The canonical sets below include
``'bet'`` so they are the correct **superset** everywhere: adding it where it
never occurs is a no-op, omitting it where it does (the old per-site sets) was
the latent drift.

Adding a new tendency stat (the contract)
-----------------------------------------
New tendency/exploit work (e.g. the short-stack iso-over-limper exploit's
limper-profiling stats — ``fold_to_iso``, limp-then-call/fold) MUST add its
formula here, not inline it again. Three steps:
  1. Counter: add the field + its increment predicate. If the predicate is a
     membership test on action/phase, define the canonical set here so every
     feeder (live model, sim, cash recorder, backfill) counts it the same way.
  2. Formula: add a pure ``num/den`` function here with a docstring naming the
     exact numerator and denominator.
  3. Feed: wire every feeder through (1) and (2). ``limp_rate`` (live model
     `opponent_model.py`) is the worked example of this shape.
"""

from __future__ import annotations

# ── Canonical action / phase vocabularies ─────────────────────────────────
# Membership tests that every feeder shares so "what counts" can't drift.

VOLUNTARY_PREFLOP_ACTIONS = frozenset({"call", "raise", "bet", "all_in"})
"""Preflop actions that count as voluntarily putting money in (the VPIP set).
A blind post is involuntary and excluded by the caller (``is_voluntary`` flag),
not by this set."""

PFR_ACTIONS = frozenset({"raise", "all_in"})
"""Preflop actions that count as a preflop raise (the PFR set). No first-in
``'bet'`` exists preflop — the only voluntary aggression is raise/all-in."""

AGGRESSIVE_ACTIONS = frozenset({"bet", "raise", "all_in"})
"""Any aggressive wager (the AF numerator set). Includes ``'bet'`` — a postflop
first-in bet IS aggression. The AF *denominator* (passive actions) is consumer-
specific: the live global/postflop AF counts calls only; clone per-street AF
counts calls + checks. Both are valid; see ``aggression_factor``."""

POSTFLOP_PHASES = frozenset({"FLOP", "TURN", "RIVER"})
"""Phases that imply the player saw the flop (the saw-flop / per-street key)."""


def is_voluntary_preflop(action: str) -> bool:
    """True if `action` is a voluntary preflop pot entry (VPIP). Caller still
    gates on the not-a-blind / once-per-hand conditions."""
    return action in VOLUNTARY_PREFLOP_ACTIONS


def is_pfr_action(action: str) -> bool:
    """True if `action` is a preflop raise (PFR)."""
    return action in PFR_ACTIONS


def is_aggressive_action(action: str) -> bool:
    """True if `action` is an aggressive wager (the AF numerator)."""
    return action in AGGRESSIVE_ACTIONS


def is_postflop_phase(phase: str) -> bool:
    """True if `phase` is a postflop street (FLOP/TURN/RIVER) — i.e. the player
    saw the flop."""
    return phase in POSTFLOP_PHASES


# ── Pure ratio formulas ────────────────────────────────────────────────────
# Each names its exact numerator and denominator. Denominator-zero handling:
# `safe_ratio` returns `default`; the named wrappers below pass `default=0.0`
# and rely on the caller to gate the no-sample / neutral-prior case (that prior
# is consumer-specific, so it stays at the call site).


def safe_ratio(numerator: float, denominator: float, *, default: float = 0.0) -> float:
    """`numerator / denominator`, returning `default` when `denominator <= 0`.
    The shared guard so no site re-invents the divide-by-zero check."""
    if denominator <= 0:
        return default
    return numerator / denominator


def aggression_factor(
    bet_raise: int,
    call: int,
    *,
    zero_call_cap: float,
    no_action_default: float = 1.0,
) -> float:
    """Aggression factor = bets+raises / calls.

    Unifies the live global AF, the live postflop AF, and the clone per-street
    AF — they differ only in `zero_call_cap`:
      - no actions observed (`bet_raise + call == 0`) → `no_action_default`
        (1.0 neutral).
      - calls observed → `bet_raise / call`.
      - aggression but zero calls → `min(bet_raise, zero_call_cap)`. The live
        model passes `CONFIG.signal_thresholds.medium_af_postflop` here so a
        noisy zero-call sample can't masquerade as an extreme maniac; the clone
        per-street AF passes `float('inf')` to keep its "cap at the raw count"
        behaviour (`float(bet_raise)`).

    `call` is whatever the caller defined as the passive denominator: the live
    model passes calls-only; clone per-street passes calls + checks. Both are
    valid AF definitions — this function does not choose for them.
    """
    if bet_raise + call == 0:
        return no_action_default
    if call == 0:
        return min(float(bet_raise), zero_call_cap)
    return bet_raise / call


def vpip(vpip_count: int, denom: int) -> float:
    """VPIP = voluntary pot entries / hands. `denom` is hands-dealt (preferred)
    or hands-observed (fallback) — the caller picks."""
    return safe_ratio(vpip_count, denom)


def pfr(pfr_count: int, denom: int) -> float:
    """PFR = preflop raises / hands (same `denom` as VPIP)."""
    return safe_ratio(pfr_count, denom)


def all_in_frequency(all_in_count: int, denom: int) -> float:
    """All-in frequency = all-ins / hands."""
    return safe_ratio(all_in_count, denom)


def fold_to_cbet(folds: int, faced: int) -> float:
    """Fold-to-cbet = folds when facing a c-bet / c-bets faced."""
    return safe_ratio(folds, faced)


def showdown_win_rate(won: int, showdowns: int) -> float:
    """W$SD = showdowns won / showdowns reached."""
    return safe_ratio(won, showdowns)


def wtsd(showdowns: int, saw_flop: int, *, clamp: bool = True) -> float:
    """WTSD = showdowns reached / hands that saw the flop.

    `clamp` keeps it in [0, 1] (the live model clamps so a rare all-in-preflop
    showdown that bumps the numerator without a postflop action can't push it
    above 1.0). The clone path passes `clamp=False` and a `saw_river` proxy
    numerator. Caller gates the sample size and the no-flop case
    (`safe_ratio` returns 0.0 at `saw_flop == 0`)."""
    ratio = safe_ratio(showdowns, saw_flop)
    return min(1.0, ratio) if clamp else ratio


def call_rate_facing_bet(calls: int, facing_bet_opportunities: int) -> float:
    """Stickiness axis = calls / facing-bet opportunities (the "doesn't fold"
    signal; the remainder of the denominator is folds + raises)."""
    return safe_ratio(calls, facing_bet_opportunities)


def all_in_per_facing_bet(all_ins_facing_bet: int, facing_bet_opportunities: int) -> float:
    """Response-aggression axis = all-ins facing a bet / facing-bet opportunities."""
    return safe_ratio(all_ins_facing_bet, facing_bet_opportunities)


def postflop_jam_open_rate(jam_opens: int, open_opportunities: int) -> float:
    """Open-aggression axis = postflop opening jams / postflop open opportunities."""
    return safe_ratio(jam_opens, open_opportunities)


def vpip_per_voluntary_opportunity(voluntary_actions: int, voluntary_opportunities: int) -> float:
    """Player-count-stable VPIP = voluntary preflop actions / voluntary
    opportunities. Diverges from raw `vpip` (which is hands-normalized and
    scales ~1/N with player count); this is what the exploitation detector
    reads."""
    return safe_ratio(voluntary_actions, voluntary_opportunities)


def pfr_per_open_opportunity(open_raises: int, open_opportunities: int) -> float:
    """Player-count-stable PFR = preflop open-raises / open opportunities.
    Numerator is dedicated open-raise count (not raw `pfr_count`, which also
    ticks for 3-bets and would exceed 1.0 for an always-raising opponent)."""
    return safe_ratio(open_raises, open_opportunities)


def limp_rate(limps: int, open_opportunities: int) -> float:
    """Limp rate = limps / open opportunities (the worked example of the
    add-a-stat contract; see module docstring)."""
    return safe_ratio(limps, open_opportunities)


# ── Running mean + polarization (non-ratio shapes) ─────────────────────────


def mean(total: float, count: int, *, default: float = 0.0) -> float:
    """Running mean = sum / count, returning `default` at count <= 0. The shared
    shape behind the equity-at-action means (equity when betting/raising/calling,
    big-bet vs small-bet equity) and any windowed average."""
    return safe_ratio(total, count, default=default)


def polarization(high_side: float, low_side: float) -> float:
    """Polarization = high-bucket value − low-bucket value. The shared shape
    behind `sizing_polarization_score` (big-bet equity − small-bet equity) and
    the aggression-polarization signal (equity when raising − equity when
    calling). Positive ⇒ the opponent reserves the high bucket for strength
    (face-up / polar). Caller owns the per-bucket sample gate (return a neutral
    0.0 below it)."""
    return high_side - low_side


# ── Postflop tendency rates (named for discoverability) ─────────────────────
# Each is a `safe_ratio` over a documented denominator. They live here so the
# full opponent-stat vocabulary is in one place, even where (today) only the
# live model consumes them — surfacing them invites reuse.


def fold_to_big_bet(folds: int, big_bets_faced: int) -> float:
    """P(fold | facing a large/jam-sized bet). High ⇒ over-folder to attack."""
    return safe_ratio(folds, big_bets_faced)


def stab_frequency(stabs: int, checked_to_opportunities: int) -> float:
    """P(bets | checked to postflop) — the stab rate; the checked-to dual of
    fold_to_big_bet."""
    return safe_ratio(stabs, checked_to_opportunities)


def cbet_attempt_rate(cbet_attempts: int, seen_as_pfr: int) -> float:
    """P(continuation-bets flop | was the preflop raiser and saw the flop)."""
    return safe_ratio(cbet_attempts, seen_as_pfr)


def barrel_frequency(barrels: int, barrel_opportunities: int) -> float:
    """P(fires a second barrel | had the opportunity) — turn barrel rate."""
    return safe_ratio(barrels, barrel_opportunities)


def third_barrel_frequency(third_barrels: int, third_barrel_opportunities: int) -> float:
    """P(fires a third barrel | had the opportunity) — river barrel rate."""
    return safe_ratio(third_barrels, third_barrel_opportunities)


def flop_check_then_barrel_rate(check_barrels: int, check_barrel_opportunities: int) -> float:
    """P(checks flop then bets a later street | checked the flop as aggressor) —
    the delayed-cbet / check-then-barrel tell."""
    return safe_ratio(check_barrels, check_barrel_opportunities)


# ── Iso-over-limper (SCAFFOLDING — feeders not yet wired) ───────────────────
# Definitions for the short-stack iso-over-limper exploit (a tendency/exploit
# being built on a sibling branch — docs/plans chart-opportunity census: the top
# preflop gap is first-in OVER A LIMPER). The FORMULAS + the exact denominators
# are agreed here so that work plugs into one home instead of inlining a 7th copy.
#
# Counter rules the feeder must add (none wired today — these functions are pure
# definitions and have NO live consumer yet):
#   - An OPEN-LIMP = a preflop `call` with NO prior raise (folded-to-limp, not a
#     limp-behind a raise). `limp_rate` (above) already keys off this.
#   - FACED-AN-ISO = after the player open-limped, a later preflop RAISE
#     (`is_pfr_action`) put them to a decision (the isolation raise). Increment
#     `_limp_faced_iso` once per such hand.
#   - The limper's response to that iso then increments exactly one of:
#       fold → `_limp_fold_to_iso` | call → `_limp_call_iso` | reraise (3-bet,
#       `is_pfr_action`) → `_limp_reraise_iso`.
# A high `fold_to_iso` limper is the profitable iso target (raise to take it
# down); a high `limp_reraise_rate` limper is a limp-trapper (iso lighter at your
# peril). These three partition the faced-iso denominator.


def fold_to_iso(limp_folds_to_iso: int, limp_faced_iso: int) -> float:
    """P(open-limper folds | faced an isolation raise). The iso-target signal."""
    return safe_ratio(limp_folds_to_iso, limp_faced_iso)


def limp_call_rate(limp_calls_iso: int, limp_faced_iso: int) -> float:
    """P(open-limper calls the iso | faced an isolation raise)."""
    return safe_ratio(limp_calls_iso, limp_faced_iso)


def limp_reraise_rate(limp_reraises_iso: int, limp_faced_iso: int) -> float:
    """P(open-limper 3-bets the iso | faced an isolation raise) — the limp-trap /
    limp-reraise signal; high ⇒ iso lighter at your peril."""
    return safe_ratio(limp_reraises_iso, limp_faced_iso)

"""Human player prestige/reputation — the sandbox-scoped scoreboard stat.

Cash mode's only scoreboard was bankroll, which is volatile and says
nothing about *who you are at the table*. Prestige adds a second axis the
world can (eventually) respond to. v1 is a **read-only scoreboard** — none
of this is injected into core AI decision thresholds (the legibility
guardrail from the attractiveness work).

Two axes, derived from data the game already records:

  - ``renown``  ∈ [0, 1] — *how much of a figure are you?* Fame magnitude,
    largely behaviour-agnostic. **Ratchets**: the recorder stores
    ``max(computed, running_peak)``, so a downswing can't erase the career
    record. Built from breadth (how many AIs know you), tenure, the highest
    stake tier you've reached, beating respected opponents, and winning at
    high stakes. A beloved legend and an infamous villain are *both* high
    renown.
  - ``regard``  ∈ [-1, 1] — *how does the room feel about you?* The valence,
    beloved ↔ reviled. **Swings** with behaviour and partially decays as
    ``heat`` decays (heat is projected on read). An aggregate over the
    human's INBOUND relationship edges (every AI's view of them).

The two axes give four quadrants — Beloved Legend / Infamous Villain /
Up-and-comer / Disliked Nobody — that a future world-response layer reads.

This module is Flask-free and repo-injected so it's unit-testable with pure
fakes. Every tunable knob is a named module-level constant; the formula is
**illustrative, not locked** (see the spec) — tune the weights here, the
component breakdown is persisted so the effect is inspectable.

Spec: docs/plans/CASH_MODE_PLAYER_PRESTIGE.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List

from cash_mode.stakes_ladder import STAKES_ORDER

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunable constants — change weights HERE only, never inline.
# ---------------------------------------------------------------------------

# Renown component weights. Each component contributes at most its weight;
# the weights sum to 1.0 so a maxed-out player saturates at renown = 1.0.
W_BREADTH = 0.25  # how many AIs "know" you
W_TENURE = 0.20  # time on the felt
W_STAKE_TIER = 0.25  # highest stake tier reached/sustained
W_BEAT_RESPECTED = 0.20  # beating opponents who respect you
W_HIGH_STAKES = 0.10  # a winning session at the high tables

# Saturation points for the unbounded inputs.
BREADTH_CAP = 12  # met this many distinct AIs → full breadth credit
TENURE_HANDS_CAP = 2000  # played this many sandbox hands → full tenure credit

# A session at one of these labels that booked a profit earns the
# high-stakes bonus (binary — you've won at the big game or you haven't).
HIGH_STAKES_LABELS = frozenset({"$200", "$1000"})

# Regard formula weights, applied per inbound edge then averaged.
#   term_o = (likability_o - 0.5)·W_LIK + (respect_o - 0.5)·W_RESP - heat_o·W_HEAT
REGARD_W_LIKABILITY = 1.0  # warmth dominates the valence
REGARD_W_RESPECT = 0.5  # respect tilts it but doesn't define it
REGARD_W_HEAT = 1.0  # hostility (heat) pulls it negative

# Quadrant decision thresholds.
RENOWN_HIGH_THRESHOLD = 0.40  # renown ≥ this → "high renown" (a figure)
REGARD_WARM_THRESHOLD = 0.05  # regard ≥ this → "warm"; below → "hostile"

# Quadrant labels — single source of truth (kept in lockstep with the
# ReputationQuadrant union in react/.../components/cash/types.ts).
QUADRANT_BELOVED_LEGEND = "Beloved Legend"
QUADRANT_INFAMOUS_VILLAIN = "Infamous Villain"
QUADRANT_UP_AND_COMER = "Up-and-comer"
QUADRANT_DISLIKED_NOBODY = "Disliked Nobody"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReputationScore:
    """A computed prestige snapshot — immutable and fully explainable.

    The ``renown_*`` / ``regard_*`` fields are the (already-weighted)
    contributions each input made, so ``renown`` ≈ Σ renown_* (pre-ratchet)
    and ``regard`` ≈ Σ regard_* (pre-clamp). They're persisted so the panel
    and debugging can show *why* a score is what it is.
    """

    renown: float  # [0, 1], ratcheted
    regard: float  # [-1, 1]
    quadrant: str  # one of the QUADRANT_* constants

    renown_breadth: float
    renown_tenure: float
    renown_stake_tier: float
    renown_beat_respected: float
    renown_high_stakes: float

    regard_likability: float
    regard_respect: float
    regard_heat: float

    opponent_count: int
    computed_at: str  # ISO-8601 UTC (trailing Z)


def iso_utc(now: datetime) -> str:
    """Render a (naive UTC) datetime as an explicit ISO-8601 UTC string.

    Mirrors the holdings recorder so the prune cutoff and the stored
    ``captured_at`` compare lexically. Idempotent if a ``Z``/offset is
    already present.
    """
    s = now.isoformat()
    if s.endswith("Z") or "+" in s[10:]:
        return s
    return s + "Z"


# ---------------------------------------------------------------------------
# Pure classifier
# ---------------------------------------------------------------------------


def quadrant_label(renown: float, regard: float) -> str:
    """Map the two axes to one of the four quadrant labels."""
    high = renown >= RENOWN_HIGH_THRESHOLD
    warm = regard >= REGARD_WARM_THRESHOLD
    if high and warm:
        return QUADRANT_BELOVED_LEGEND
    if high and not warm:
        return QUADRANT_INFAMOUS_VILLAIN
    if not high and warm:
        return QUADRANT_UP_AND_COMER
    return QUADRANT_DISLIKED_NOBODY


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# Hook 3 (chat tone): one-line table-talk tone hints, keyed by quadrant.
# Only the HIGH-renown quadrants get a hint — a low-renown player isn't yet a
# figure the room reacts to, so we stay silent there (matches the
# relationship-prompt "skip neutral opponents" philosophy and keeps the prompt
# clean). These strings are pure prompt FLAVOR: they only ever reach the
# ExpressionGenerator's user-prompt suffix, never action selection.
_REPUTATION_CHAT_TONE = {
    QUADRANT_BELOVED_LEGEND: (
        "TABLE REPUTATION: This player is a celebrated figure at these stakes — "
        "widely known, respected, and well-liked. Let genuine warmth or a note "
        "of deference color how you address them in your table talk."
    ),
    QUADRANT_INFAMOUS_VILLAIN: (
        "TABLE REPUTATION: This player is notorious here — a big name the room "
        "fears and resents. Players love to take shots at them. Let an edge of "
        "needling, wariness, or open hostility color how you address them in "
        "your table talk."
    ),
}


def reputation_chat_tone(quadrant: str) -> str:
    """One-line table-talk tone hint for the human's reputation quadrant.

    Hook 3 of the prestige system. Only the high-renown quadrants (Beloved
    Legend, Infamous Villain) return a hint — a low-renown player isn't a
    figure the room reacts to yet, so the up-and-comer / disliked-nobody
    quadrants (and any unknown label) return "". Flavor only: the string is
    appended to the AI's narration prompt, never the action math.
    """
    return _REPUTATION_CHAT_TONE.get(quadrant, "")


# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------


def compute_prestige(
    *,
    owner_id: str,
    sandbox_id: str,
    now: datetime,
    relationship_repo: Any,
    cash_session_repo: Any,
    renown_peak: float = 0.0,
) -> ReputationScore:
    """Derive a ``ReputationScore`` from current DB state.

    Flask-free and repo-injected; every per-source read is wrapped so a
    single broken repo degrades that input to zero rather than failing the
    whole recompute (this runs on the world ticker and must never throw).

    ``renown_peak`` is the previously persisted ratchet ceiling — the caller
    reads it (``PrestigeSnapshotsRepository.load_renown_peak``) and passes it
    in; renown is returned as ``max(computed, renown_peak)``. This function
    does NOT write to the DB.

    ``relationship_repo`` supplies both the inbound relationship edges
    (``load_inbound_relationships``) and the cash pair stats
    (``list_cash_pair_stats_for_observer``). ``cash_session_repo`` supplies
    completed sessions (``list_completed_for_sandbox``).
    """
    # --- Inbound relationship edges (every AI's view of the human). ---
    # Used by BOTH regard (the aggregate sentiment) and renown's
    # beat-respected term (does a beaten opponent respect you?), so we read
    # it once.
    inbound: Dict[str, Any] = {}
    try:
        inbound = relationship_repo.load_inbound_relationships(owner_id, now=now)
    except Exception:
        logger.warning("prestige: inbound relationship load failed for %s", owner_id)

    # --- Cash pair stats (breadth + which opponents the human is up on). ---
    pair_stats: List[Any] = []
    try:
        pair_stats = relationship_repo.list_cash_pair_stats_for_observer(
            owner_id, sandbox_id=sandbox_id
        )
    except Exception:
        logger.warning("prestige: cash pair stats load failed for %s", owner_id)

    # --- Completed sessions (tenure, highest tier, high-stakes win). ---
    sessions: List[Any] = []
    try:
        sessions = cash_session_repo.list_completed_for_sandbox(owner_id, sandbox_id)
    except Exception:
        logger.warning("prestige: session load failed for %s", owner_id)

    # ---- Renown: breadth ----
    breadth = sum(1 for s in pair_stats if (s.hands_played_cash or 0) > 0)
    c_breadth = W_BREADTH * min(1.0, breadth / float(BREADTH_CAP))

    # ---- Renown: tenure ----
    tenure_hands = sum((s.hands_played or 0) for s in sessions)
    c_tenure = W_TENURE * min(1.0, tenure_hands / float(TENURE_HANDS_CAP))

    # ---- Renown: highest stake tier reached ----
    max_tier_rank = 0
    for s in sessions:
        try:
            max_tier_rank = max(max_tier_rank, STAKES_ORDER.index(s.stake_label))
        except (ValueError, AttributeError):
            continue
    max_rank = len(STAKES_ORDER) - 1
    c_stake_tier = W_STAKE_TIER * (max_tier_rank / float(max_rank)) if max_rank > 0 else 0.0

    # ---- Renown: beating respected opponents ----
    # For each opponent the human is up on, credit the respect that opponent
    # holds for the human (high inbound respect from someone you've beaten ≈
    # "beat a respected opponent" — beating raises their mirror respect).
    # Average the respect-quality across beaten opponents.
    beaten = [s.opponent_id for s in pair_stats if (s.cumulative_pnl or 0) > 0]
    c_beat_respected = 0.0
    if beaten:
        quality_sum = 0.0
        for pid in beaten:
            state = inbound.get(pid)
            if state is not None:
                # respect 0.5 → 0.0 credit, respect 1.0 → 1.0 credit.
                quality_sum += _clamp((state.respect - 0.5) * 2.0, 0.0, 1.0)
        c_beat_respected = W_BEAT_RESPECTED * (quality_sum / len(beaten))

    # ---- Renown: high-stakes win ----
    c_high_stakes = 0.0
    for s in sessions:
        if (
            s.stake_label in HIGH_STAKES_LABELS
            and s.player_take_home is not None
            and s.total_buy_in is not None
            and s.player_take_home > s.total_buy_in
        ):
            c_high_stakes = W_HIGH_STAKES
            break

    raw_renown = (
        c_breadth + c_tenure + c_stake_tier + c_beat_respected + c_high_stakes
    )
    computed_renown = _clamp(raw_renown, 0.0, 1.0)
    renown = max(renown_peak, computed_renown)  # ratchet

    # ---- Regard: average over inbound edges (one pass) ----
    regard_lik = 0.0
    regard_resp = 0.0
    regard_heat = 0.0
    opponent_count = len(inbound)
    if opponent_count:
        lik_sum = resp_sum = heat_sum = 0.0
        for st in inbound.values():
            lik_sum += st.likability - 0.5
            resp_sum += st.respect - 0.5
            heat_sum += st.heat
        regard_lik = lik_sum / opponent_count * REGARD_W_LIKABILITY
        regard_resp = resp_sum / opponent_count * REGARD_W_RESPECT
        regard_heat = -heat_sum / opponent_count * REGARD_W_HEAT

    regard = _clamp(regard_lik + regard_resp + regard_heat, -1.0, 1.0)

    return ReputationScore(
        renown=round(renown, 4),
        regard=round(regard, 4),
        quadrant=quadrant_label(renown, regard),
        renown_breadth=round(c_breadth, 4),
        renown_tenure=round(c_tenure, 4),
        renown_stake_tier=round(c_stake_tier, 4),
        renown_beat_respected=round(c_beat_respected, 4),
        renown_high_stakes=round(c_high_stakes, 4),
        regard_likability=round(regard_lik, 4),
        regard_respect=round(regard_resp, 4),
        regard_heat=round(regard_heat, 4),
        opponent_count=opponent_count,
        computed_at=iso_utc(now),
    )

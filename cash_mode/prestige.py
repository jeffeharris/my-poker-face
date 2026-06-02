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
from dataclasses import dataclass, field
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

    # --- v2 (Renown-v2) OPTIONAL extension -------------------------------
    # These are appended AFTER every v1 field with defaults so the frozen
    # dataclass stays positional-compatible and the v1 positional/keyword
    # repo.record() (which reads only the 14 fields above) is unaffected. A
    # v1 caller constructs ReputationScore(...) exactly as before; a v2 caller
    # fills these in. ``formula_version`` discriminates the two (v1 renown is
    # [0,1] capped; v2 renown is uncapped) so a consumer never compares across
    # versions. Persistence of these columns is DEFERRED (see
    # docs/plans/CASH_MODE_PLAYER_PRESTIGE.md — "v2 implemented" note).
    renown_v2: float = 0.0           # uncapped v2 total (Σ v2 components)
    renown_scalps: float = 0.0       # ★ renown-weighted scalps
    renown_top1: float = 0.0         # ★ time-at-#1 + peak net worth standing
    renown_peak_worth: float = 0.0   # ★ peak net worth (separate component)
    renown_backing: float = 0.0      # ★ kingmaker / backing (field-relative)
    renown_legendary: float = 0.0    # ★ legendary nuggets
    renown_apex: float = 0.0         # winner-vs-roster premium
    high_renown_cut: float = 0.0     # field-relative "high renown" threshold
    formula_version: int = 1         # 1 = v1 (capped), 2 = v2 (uncapped)


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


def refill_affinity(quadrant: str, state: Any) -> float:
    """How drawn a candidate AI is to sit at the human's table (hook 1).

    Used by the hand-boundary seat refill to reorder the eligible pool so
    *who sits with you* reflects your reputation — the human-keyed "table
    pull." Higher = seated sooner.

    Only the two high-renown quadrants reorder (the room reacts to a figure);
    the low-renown quadrants and unknown labels return 0.0 (neutral — no
    reordering, the existing deterministic order stands):

      - Beloved Legend → warm admirers lead: ``(likability−0.5)+(respect−0.5)``,
        so the legend's table fills with the AIs who like/respect them.
      - Infamous Villain → the dethrone draw: ``heat``, so AIs with a score to
        settle (a rival cohort) cycle in first while the cold/neutral room
        hangs back. (Deprioritized, not refused — the table still fills so the
        human always has opponents; the "avoidance" is relative ordering.)

    `state` is the candidate's INBOUND relationship edge toward the human
    (likability/respect/heat); None → neutral defaults (no edge yet).
    """
    if state is None:
        likability, respect, heat = 0.5, 0.5, 0.0
    else:
        likability, respect, heat = state.likability, state.respect, state.heat
    if quadrant == QUADRANT_BELOVED_LEGEND:
        return (likability - 0.5) + (respect - 0.5)
    if quadrant == QUADRANT_INFAMOUS_VILLAIN:
        return heat
    return 0.0


def reputation_demeanor_stimulus(quadrant: str):
    """Coarse table-demeanor stimulus for the human's reputation quadrant.

    Hook 4 (AI demeanor). Maps the quadrant to the coarse stimulus
    `PlayerPsychology.react_to_table_reputation` understands — only the two
    high-renown quadrants move the room:
      - Infamous Villain → ``'intimidating'`` (rattles low-poise opponents).
      - Beloved Legend   → ``'reassuring'`` (loosens them up).
    Returns ``None`` for the low-renown quadrants and unknown labels (no
    demeanor effect).
    """
    if quadrant == QUADRANT_INFAMOUS_VILLAIN:
        return "intimidating"
    if quadrant == QUADRANT_BELOVED_LEGEND:
        return "reassuring"
    return None


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


# ===========================================================================
# RENOWN v2 — field-aware, uncapped, concave scoreboard layer (ADDITIVE).
# ===========================================================================
#
# This is a faithful port of the balance-validated offline scorer
# `scripts/renown_v2_scorer.py` (Rung-1 PASS: the four ★ routes each reach
# high renown; no single-driver dominance; wall-clock denomination beats the
# volume treadmill; the quadrant cut = max(top-X%, k×median)). The math here
# is byte-for-byte the same formula; the scorer remains the oracle and the
# new tests assert parity against it.
#
# v2 is purely ADDITIVE: the v1 `compute_prestige` + absolute `quadrant_label`
# above are the live human entry point and are unchanged. v2 introduces a new
# PURE, FIELD-WIDE entry point `score_renown_field(entities)` that scores all
# entities together in one two-pass call (field medians + the relative
# high-renown cut are computed once over the whole field), and a repo-injected
# degrade-to-zero builder `build_renown_inputs_from_repos`.
#
# LEGIBILITY GUARDRAIL (preserved verbatim from v1): the v2 layer is likewise
# SIDE-CHANNEL ONLY. No renown value ever enters generate_bounded_options,
# OptionProfile thresholds, or any controller decision path. Its only
# consumers are the same 4 reputation hooks (table refill ordering, sponsor
# backing gate, chat-tone prompt suffix, once-per-hand demeanor nudge) plus
# the lobby panel — all of which switch on the quadrant STRING, never on the
# raw renown number.
#
# DEFERRED (NOT in this slice — see docs/plans/CASH_MODE_PLAYER_PRESTIGE.md):
#   (A) prestige_snapshots schema change to persist AI renown (v122 table,
#       load_renown_peak MAX ratchet — risky migration, must be sim-validated).
#   (B) ticker surgery to compute the whole field every cycle and persist the
#       human's v2 columns (CYCLE_BUDGET_MS=250ms, O(N) DB-read-heavy with 50+
#       AIs — must be docker-exec stress-validated).
#   (C) flipping RENOWN_V2_ENABLED so the 4 hooks read quadrant_label_relative.
#   (D) frontend ReputationPanel branch on formula_version for the uncapped
#       gauge.
# ---------------------------------------------------------------------------

import math
from typing import Dict as _Dict, List as _List, Tuple as _Tuple

# --- v2 weights / tunables (verbatim from the scorer's Weights defaults) ----
W_SCALP = 4.0
SCALP_BASE = 0.3           # a nobody's scalp is worth this fraction
SCALP_QUALITY = 1.0        # ...a TOP-of-field victim's scalp this much more
W_TOP1 = 0.8               # sqrt(ticks at #1)
W_PEAK_WORTH = 0.6         # log1p(peak_net_worth / unit)
WORTH_UNIT = 5000.0
W_BACKING = 3.0            # log1p(volume/median) + profit bonus
BACKING_UNIT = 10000.0
W_LEGENDARY = 1.5
W_TENURE_V2 = 0.5
W_BREADTH_V2 = 9.0
BREADTH_PER_OPP_CAP_HANDS = 200.0   # concavity knee per opponent (hands mode)
W_STAKES_V2 = 0.4
W_APEX = 0.4
APEX_UNIT = 50000.0
HIGH_RENOWN_TOP_FRACTION = 0.10  # "figures" = the top decile of the field by renown
VOLUME_DENOMINATOR = "wallclock"    # the design ideal (anti-treadmill governor)

# stake tiers low->high for stakes-mastery depth credit (mirror the scorer).
_V2_STAKE_ORDER: _Tuple[str, ...] = ("$2", "$10", "$50", "$200", "$1000")

# Denominator PRODUCTION actually scores under. The design default above is
# `wallclock` (validated in the Rung-1 sim, where wall-clock had real per-entity
# variance), and the offline scorer + the parity tests keep that default so the
# anti-treadmill lever stays proven. But on the REAL field the only wall-clock
# proxy available — distinct `holdings_snapshots` ticks — is near-uniform
# (every entity is stamped each tick; CV ~0.16, median == max), so `wallclock`
# denomination is DEGENERATE there: it flattens the volume drivers, the
# field-relative `high_cut` then exceeds the field maximum, and NO entity
# (not even the rank-#1 human) classifies as a "figure" — a regression from v1.
# `hands` gives correct results (the human reads as Infamous Villain, matching
# v1; the field shows a small set of figures), and the hands-"treadmill" is
# inert on real data: hand-count is uncorrelated with performance (ρ≈0.05) AND
# AI hand-volumes are negligible, so renown is driven by backing / wealth
# standing / scalps, not grinding. Revisit if a real seat-occupancy time signal
# ever replaces the presence proxy. See docs/captains-log/renown/ for the data.
PROD_VOLUME_DENOMINATOR = "hands"


@dataclass
class WeightsV2:
    """v2 weights — the only thing a sweep would vary. Defaults = design point.

    Mirrors `renown_v2_scorer.Weights` field-for-field so the oracle and prod
    can't drift on the math (only the symbol names differ)."""
    w_scalp: float = W_SCALP
    scalp_base: float = SCALP_BASE
    scalp_quality: float = SCALP_QUALITY
    w_top1: float = W_TOP1
    w_peak_worth: float = W_PEAK_WORTH
    worth_unit: float = WORTH_UNIT
    w_backing: float = W_BACKING
    backing_unit: float = BACKING_UNIT
    w_legendary: float = W_LEGENDARY
    w_tenure: float = W_TENURE_V2
    w_breadth: float = W_BREADTH_V2
    breadth_per_opp_cap_hands: float = BREADTH_PER_OPP_CAP_HANDS
    w_stakes: float = W_STAKES_V2
    w_apex: float = W_APEX
    apex_unit: float = APEX_UNIT
    volume_denominator: str = VOLUME_DENOMINATOR
    high_renown_top_fraction: float = HIGH_RENOWN_TOP_FRACTION
    stake_order: _Tuple[str, ...] = _V2_STAKE_ORDER


@dataclass
class RenownInputsV2:
    """v2 inputs — SYMMETRIC: every field populated for a human or an AI alike.

    Field names mirror the data sources compute_prestige already reads
    (cash_pair_stats, completed sessions, inbound relationship edges) plus the
    v2 additions (scalps, time-at-#1, backing, legendary nuggets, wall-clock).
    A faithful copy of `renown_v2_scorer.RenownInputs`."""
    label: str = ""

    # --- ★ scalps: {victim_id: times_busted}. Weighted by victim renown. ---
    scalps: _Dict[str, int] = field(default_factory=dict)

    # --- ★ time at #1 net worth (standing) ---
    ticks_at_number_one: int = 0
    peak_net_worth: float = 0.0

    # --- ★ kingmaker / backing ---
    backing_volume: float = 0.0
    backing_profit: float = 0.0

    # --- ★ legendary hands ---
    legendary_points: float = 0.0

    # --- volume-ish drivers (WALL-CLOCK denominated by design) ---
    wall_clock_hours: float = 0.0
    total_hands: int = 0
    breadth_opponents: _Dict[str, int] = field(default_factory=dict)

    # --- stakes mastery: {stake_label: hands_at_that_tier} ---
    stakes_hands: _Dict[str, int] = field(default_factory=dict)

    # --- apex: net chips vs the whole roster (can be negative) ---
    roster_net: float = 0.0

    # --- regard inputs (orthogonal to renown; shown for the quadrant) ---
    regard_likability: float = 0.0
    regard_respect: float = 0.0
    regard_heat: float = 0.0


@dataclass
class FieldContextV2:
    """Field-level aggregates needed to make drivers field-relative."""
    median_backing_volume: float = 0.0
    median_breadth_depth: float = 0.0


@dataclass
class FieldRenown:
    """Per-entity result from the field-wide v2 scorer."""
    components: _Dict[str, float]
    renown_total: float
    victim_percentile: float   # this entity's own field renown percentile
    high_cut: float            # the field-wide high-renown cut (same for all)


# --- concave accrual helpers (verbatim port) --------------------------------


def _v2_sqrt(x: float) -> float:
    return math.sqrt(max(0.0, x))


def _v2_log1p(x: float) -> float:
    return math.log1p(max(0.0, x))


def _v2_relative(raw: float, median: float, fallback_unit: float) -> float:
    """Field-relative concave contribution: log1p(raw / median).

    median entity → log1p(1)=0.69; 10× median → 2.4; 0.1× → 0.095. Falls back
    to an absolute unit only if the field has no positive values (median==0).
    Verbatim from `renown_v2_scorer._relative`."""
    denom = median if median > 0 else fallback_unit
    return _v2_log1p(raw / denom)


def _breadth_depth_sum_v2(inp: RenownInputsV2, w: WeightsV2) -> float:
    """Raw breadth depth (Σ per-opponent depth) BEFORE field-relativisation.

    Verbatim from `renown_v2_scorer._breadth_depth_sum`."""
    total = 0.0
    for hands_vs in inp.breadth_opponents.values():
        if w.volume_denominator == "wallclock":
            if inp.total_hands > 0:
                opp_hours = inp.wall_clock_hours * (hands_vs / inp.total_hands)
                total += _v2_sqrt(opp_hours)
        else:  # 'hands' — the naive treadmill counterfactual
            total += _v2_sqrt(min(hands_vs, w.breadth_per_opp_cap_hands))
    return total


def renown_scalp_points(
    scalps: _Dict[str, int],
    victim_percentile: _Dict[str, float],
    w: WeightsV2 = WeightsV2(),
) -> float:
    """Isolated, unit-testable scalp driver (RAW points, before w.w_scalp).

    pts = Σ_v log1p(count_v) · (scalp_base + scalp_quality · percentile_v).
    Per-victim quality is bounded to [scalp_base, scalp_base+scalp_quality]
    even though total renown is uncapped. Extracted so the term can be tested
    against the oracle independently of the full component sum; called by
    compute_components_v2 (which then multiplies by w.w_scalp)."""
    scalp_pts = 0.0
    for vid, count in scalps.items():
        pct = victim_percentile.get(vid, 0.0)
        quality = w.scalp_base + w.scalp_quality * pct
        scalp_pts += _v2_log1p(count) * quality
    return scalp_pts


def compute_components_v2(
    inp: RenownInputsV2,
    w: WeightsV2,
    victim_percentile: _Dict[str, float],
    fctx: FieldContextV2,
) -> _Dict[str, float]:
    """Return {driver_name: points}. Sum = total renown (uncapped).

    Verbatim port of `renown_v2_scorer.compute_components`. ``victim_percentile``
    maps entity_id -> its field renown percentile in [0,1] (from the previous
    pass); ``fctx`` carries field medians so backing/breadth are field-relative.
    """
    c: _Dict[str, float] = {}

    # ★ Renown-weighted scalps (extracted into renown_scalp_points).
    c["scalps"] = w.w_scalp * renown_scalp_points(inp.scalps, victim_percentile, w)

    # ★ Time at #1 + peak net worth (standing; ratchets).
    c["top1"] = w.w_top1 * _v2_sqrt(inp.ticks_at_number_one)
    c["peak_worth"] = w.w_peak_worth * _v2_log1p(inp.peak_net_worth / w.worth_unit)

    # ★ Kingmaker / backing — FIELD-RELATIVE volume + profit bonus.
    backing = _v2_relative(inp.backing_volume, fctx.median_backing_volume, w.backing_unit)
    backing += 0.5 * _v2_relative(
        max(0.0, inp.backing_profit), fctx.median_backing_volume, w.backing_unit
    )
    c["backing"] = w.w_backing * backing

    # ★ Legendary nuggets.
    c["legendary"] = w.w_legendary * _v2_sqrt(inp.legendary_points)

    # --- volume-ish drivers ---
    tenure_input = (
        inp.wall_clock_hours if w.volume_denominator == "wallclock" else inp.total_hands
    )
    c["tenure"] = w.w_tenure * _v2_sqrt(tenure_input)
    raw_breadth = _breadth_depth_sum_v2(inp, w)
    c["breadth"] = w.w_breadth * _v2_relative(raw_breadth, fctx.median_breadth_depth, 1.0)

    # Stakes mastery: depth at each tier, tiers weighted by their rank.
    stakes_pts = 0.0
    n = max(1, len(w.stake_order) - 1)
    for label, hands in inp.stakes_hands.items():
        try:
            rank = w.stake_order.index(label)
        except ValueError:
            continue
        tier_weight = 0.5 + (rank / n)
        if w.volume_denominator == "wallclock" and inp.total_hands > 0:
            depth = inp.wall_clock_hours * (hands / inp.total_hands)
        else:
            depth = hands
        stakes_pts += tier_weight * _v2_sqrt(depth)
    c["stakes"] = w.w_stakes * stakes_pts

    # Apex: net-positive vs the whole roster (a winner's premium; concave).
    apex = inp.roster_net / w.apex_unit
    c["apex"] = w.w_apex * (_v2_sqrt(apex) if apex > 0 else 0.0)

    return c


def total_renown_v2(components: _Dict[str, float]) -> float:
    return sum(components.values())


def _median_v2(values: _List[float]) -> float:
    """Median over the given values; 0.0 for an empty list. Verbatim port."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _percentile_map(renowns: _Dict[str, float]) -> _Dict[str, float]:
    """entity_id -> fraction of the field with strictly lower renown ∈ [0,1].

    Rank-based (robust to an uncapped outlier). Verbatim port. Guards n<=1."""
    n = len(renowns)
    if n <= 1:
        return {eid: 0.0 for eid in renowns}
    out: _Dict[str, float] = {}
    vals = list(renowns.values())
    for eid, r in renowns.items():
        lower = sum(1 for v in vals if v < r)
        out[eid] = lower / (n - 1)
    return out


def _field_context_v2(entities: _Dict[str, RenownInputsV2], w: WeightsV2) -> FieldContextV2:
    """Field medians for the relativised drivers, over POSITIVE values only.

    Verbatim port of `renown_v2_scorer._field_context`."""
    backing = [i.backing_volume for i in entities.values() if i.backing_volume > 0]
    breadth = [
        d for d in (_breadth_depth_sum_v2(i, w) for i in entities.values()) if d > 0
    ]
    return FieldContextV2(
        median_backing_volume=_median_v2(backing),
        median_breadth_depth=_median_v2(breadth),
    )


def percentile_cut(renowns: _List[float], top_fraction: float) -> float:
    """The renown value at the top_fraction boundary. Verbatim port."""
    if not renowns:
        return 0.0
    ordered = sorted(renowns, reverse=True)
    idx = max(0, min(len(ordered) - 1, int(round(top_fraction * len(ordered))) - 1))
    return ordered[idx]


def high_renown_cut(renowns: _List[float], w: WeightsV2 = WeightsV2()) -> float:
    """Relative 'high renown' threshold = the top-X% renown boundary.

    A pure rank-based percentile, so it self-scales with the field and is robust
    to the renown scale's SHAPE. Every driver in `compute_components_v2` is
    concave (sqrt / log1p), so renown is intentionally thin-tailed — on a real
    field the max sits only ~2.6× the median. The retired `k×median` floor was a
    MULTIPLICATIVE gate on that additive-concave scale: with k=3 it exceeded the
    field maximum (no entity ever classified as a figure), and it got HARDER as
    the field grew more accomplished (the median rose). A percentile has neither
    failure mode — it always names the top decile, whatever the spread. Verbatim
    port of `renown_v2_scorer.high_renown_cut`."""
    return percentile_cut(renowns, w.high_renown_top_fraction)


def score_renown_field(
    entities: _Dict[str, RenownInputsV2], weights: WeightsV2 = WeightsV2()
) -> _Dict[str, FieldRenown]:
    """Pure, field-wide v2 scorer. The single AI-symmetric entry point.

    Two-pass (per `renown_v2_scorer.score_field`): seed victim_percentile=0,
    compute renowns, build the rank-based percentile map, then re-score so
    scalps weight by the victim's field percentile. One refinement pass is
    plenty (not a fixed point). Field medians + the relative high-renown cut
    are computed ONCE over the whole field and applied to every entity.

    Returns {entity_id: FieldRenown(components, renown_total, victim_percentile,
    high_cut)}. Humans and AIs feed identical RenownInputsV2."""
    w = weights
    fctx = _field_context_v2(entities, w)  # raw-input medians; pass-invariant
    victim_percentile = {eid: 0.0 for eid in entities}
    scored: _Dict[str, _Dict[str, float]] = {}
    renowns: _Dict[str, float] = {}
    for _ in range(2):  # one refinement pass
        scored = {
            eid: compute_components_v2(inp, w, victim_percentile, fctx)
            for eid, inp in entities.items()
        }
        renowns = {eid: total_renown_v2(c) for eid, c in scored.items()}
        victim_percentile = _percentile_map(renowns)

    cut = high_renown_cut(list(renowns.values()), w)
    return {
        eid: FieldRenown(
            components=scored[eid],
            renown_total=renowns[eid],
            victim_percentile=victim_percentile.get(eid, 0.0),
            high_cut=cut,
        )
        for eid in entities
    }


def regard_of_v2(inp: RenownInputsV2) -> float:
    """Orthogonal valence axis (unchanged shape from v1), for the quadrant.

    Verbatim port of `renown_v2_scorer.regard_of`."""
    return inp.regard_likability + 0.5 * inp.regard_respect - inp.regard_heat


# --- v2 relative quadrant classifier ----------------------------------------


def quadrant_label_relative(renown: float, regard: float, high_cut: float) -> str:
    """Field-relative quadrant classifier — same 4 QUADRANT_* constants as v1.

    The ONLY difference from v1's `quadrant_label` is the high-renown test:
    ``renown >= high_cut`` (field-relative, self-scaling) instead of the
    absolute 0.40 threshold. The 4 hooks keep working unchanged because they
    switch on the returned quadrant STRING, never the numeric threshold. The
    warm/hostile valence split reuses v1's REGARD_WARM_THRESHOLD."""
    high = renown >= high_cut
    warm = regard >= REGARD_WARM_THRESHOLD
    if high and warm:
        return QUADRANT_BELOVED_LEGEND
    if high and not warm:
        return QUADRANT_INFAMOUS_VILLAIN
    if not high and warm:
        return QUADRANT_UP_AND_COMER
    return QUADRANT_DISLIKED_NOBODY


# --- repo-injected, degrade-to-zero builder ---------------------------------


def build_renown_inputs_from_repos(
    *,
    entity_id: str,
    sandbox_id: str,
    now: datetime,
    relationship_repo: Any,
    cash_session_repo: Any,
    cash_scalps_repo: Any = None,
    holdings_repo: Any = None,
    stake_repo: Any = None,
) -> RenownInputsV2:
    """Map live DB state to a RenownInputsV2 — repo-injected, degrade-to-zero.

    AI-symmetric: ``entity_id`` is the owner_id for the human or the
    personality_id for an AI (raw, no 'ai:'/'player:' prefix — mirrors
    cash_pair_stats / cash_scalps). Each data source is wrapped in its own
    try/except so a broken repo zeroes ONLY that input and never throws — the
    same per-source contract as compute_prestige (this is intended to run on
    the world ticker eventually and must never break it).

    Sources (the SAME ones the v1 compute + the offline Rung-2 load_field use):
      - breadth_opponents / roster_net  ← cash_pair_stats (relationship_repo)
      - peak_net_worth / ticks_at_#1 / wall_clock_hours ← holdings_snapshots
      - backing_volume / backing_profit ← settled stakes (stake_repo)
      - stakes_hands / total_hands / tenure ← completed cash_sessions
      - regard_* ← inbound relationship edges (relationship_repo)
      - scalps ← cash_scalps_repo.list_for_eliminator(sandbox_id, entity_id)

    NOTE: holdings/stake source mapping is best-effort and tolerant of method
    absence (a None or method-less repo simply zeroes that input) because the
    persisted AI holdings/stake surfaces are part of the DEFERRED stage; the
    builder is forward-compatible and never assumes a method exists. See
    docs/plans/CASH_MODE_PLAYER_PRESTIGE.md.
    """
    out = RenownInputsV2(label=entity_id)

    # --- breadth + roster_net from cash pair stats --------------------------
    try:
        pair_stats = relationship_repo.list_cash_pair_stats_for_observer(
            entity_id, sandbox_id=sandbox_id
        )
        breadth: _Dict[str, int] = {}
        roster_net = 0.0
        for s in pair_stats or []:
            hands = int(getattr(s, "hands_played_cash", 0) or 0)
            opp = getattr(s, "opponent_id", None)
            if opp is not None and hands > 0:
                breadth[opp] = hands
            roster_net += float(getattr(s, "cumulative_pnl", 0) or 0)
        out.breadth_opponents = breadth
        out.roster_net = roster_net
    except Exception:
        logger.warning("renown_v2: pair stats load failed for %s", entity_id)

    # --- regard edges from inbound relationships ----------------------------
    try:
        inbound = relationship_repo.load_inbound_relationships(entity_id, now=now)
        n = len(inbound) if inbound else 0
        if n:
            lik = sum((st.likability - 0.5) for st in inbound.values()) / n
            resp = sum((st.respect - 0.5) for st in inbound.values()) / n
            heat = sum(st.heat for st in inbound.values()) / n
            out.regard_likability = lik
            out.regard_respect = resp
            out.regard_heat = heat
    except Exception:
        logger.warning("renown_v2: inbound relationship load failed for %s", entity_id)

    # --- tenure / stakes_hands / total_hands from completed sessions --------
    try:
        sessions = cash_session_repo.list_completed_for_sandbox(entity_id, sandbox_id)
        stakes_hands: _Dict[str, int] = {}
        total_hands = 0
        for s in sessions or []:
            hands = int(getattr(s, "hands_played", 0) or 0)
            total_hands += hands
            label = getattr(s, "stake_label", None)
            if label is not None and hands > 0:
                stakes_hands[label] = stakes_hands.get(label, 0) + hands
        out.stakes_hands = stakes_hands
        out.total_hands = total_hands
    except Exception:
        logger.warning("renown_v2: session load failed for %s", entity_id)

    # --- scalps from the (already-live) cash_scalps counter -----------------
    try:
        if cash_scalps_repo is not None:
            rows = cash_scalps_repo.list_for_eliminator(sandbox_id, entity_id)
            out.scalps = {vid: int(count) for vid, count in (rows or [])}
    except Exception:
        logger.warning("renown_v2: scalps load failed for %s", entity_id)

    # --- peak net worth / ticks-at-#1 / wall-clock from holdings ------------
    # DEFERRED surface — tolerant of an absent repo / method (zeroes the input).
    try:
        if holdings_repo is not None and hasattr(holdings_repo, "load_renown_inputs"):
            h = holdings_repo.load_renown_inputs(sandbox_id, entity_id) or {}
            out.peak_net_worth = float(h.get("peak_net_worth", 0.0) or 0.0)
            out.ticks_at_number_one = int(h.get("ticks_at_number_one", 0) or 0)
            out.wall_clock_hours = float(h.get("wall_clock_hours", 0.0) or 0.0)
    except Exception:
        logger.warning("renown_v2: holdings load failed for %s", entity_id)

    # --- backing volume / profit from settled stakes -----------------------
    # DEFERRED surface — tolerant of an absent repo / method (zeroes the input).
    try:
        if stake_repo is not None and hasattr(stake_repo, "load_backing_totals"):
            b = stake_repo.load_backing_totals(sandbox_id, entity_id) or {}
            out.backing_volume = float(b.get("backing_volume", 0.0) or 0.0)
            out.backing_profit = float(b.get("backing_profit", 0.0) or 0.0)
    except Exception:
        logger.warning("renown_v2: backing load failed for %s", entity_id)

    return out

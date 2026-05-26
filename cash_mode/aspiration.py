"""Aspiration-ask trigger math.

Pure functions only — no I/O, no DB access. Composed at call time
from inputs the lobby refresh loop already has (BorrowerProfile,
bankroll snapshot, stakes ladder). The integration is in
`cash_mode/lobby.py`'s refresh path.

Spec: `docs/plans/CASH_MODE_AI_ASPIRATION_ASK.md` Commit 2.
"""

from __future__ import annotations

# --- Tunable constants -------------------------------------------------------

DEFAULT_BASE_RATE: float = 0.002
"""Per-tick floor probability when both multipliers sit at 1.0.

Calibrated against an expected 22 tick/s sim cadence + 60s simulated
per-AI cooldown. For Napoleon-class (aspiration_bias ≈ 0.88, peak
wealth_gap = 2.0) this yields ~0.007 per tick = ~70 attempts over
10k ticks before cooldowns. Lower this to suppress activity; raise
to densify."""

MAX_ASPIRATION_PROB: float = 0.05
"""Ceiling on a single tick's aspiration probability. Defends against
config combinations that would otherwise produce >5% per-tick rates,
which compound to ladder-climb spam under 22 tick/s cadence."""

ASPIRATION_BIAS_FACTOR_SCALE: float = 2.0
"""Maps `aspiration_bias ∈ [0, 1]` to a multiplier in `[0, 2]`. With
the default `aspiration_bias = 0.5`, the bias factor is 1.0 — the
baseline. Climbers go up to 2×, grinders to 0×."""

WEALTH_GAP_PEAK: float = 0.5
"""Where the wealth_gap_factor peaks (bell-curve center). 0.5 means
"half-way to a properly-rolled bankroll at the next tier" is the
sweet spot for aspiration — far below is too poor to commit, far
above means the AI is already well-rolled and doesn't need leverage."""

WEALTH_GAP_PEAK_MULTIPLIER: float = 2.0
"""Height of the wealth_gap_factor bell-curve at its peak. The curve
falls linearly to 0 at ratio 0 and ratio 1 (the bell-curve shape
described in the spec)."""

WEALTH_GAP_SAFE_BUY_IN_COUNT: int = 5
"""How many buy-ins at the next tier count as "properly rolled". The
gap target is `SAFE_BUY_IN_COUNT × target_min_buy_in`; an AI's
bankroll is compared against THAT, not against `target_min_buy_in`
directly. Without the multiplier, every AI with bankroll above the
next-tier min reads as self-fundable (ratio ≥ 1, factor = 0) — but
real poker calls for several buy-ins of cushion before sitting
comfortably. 5 is a conservative-but-not-paranoid choice; matches
the lower end of standard bankroll-management heuristics
(15-30 buy-ins for serious play, 5+ for "comfortable enough to take
a shot")."""


# --- Pure helpers ------------------------------------------------------------


def aspiration_bias_factor(bias: float) -> float:
    """Linear map of `aspiration_bias` in [0, 1] to a multiplier in [0, 2].

    A `bias` of 0 returns 0 — short-circuits the whole trigger for
    personalities marked as never aspiring (Buddha, Lincoln, the four
    refusers per the borrower_profile loader). A `bias` of 0.5
    returns 1.0 (baseline). A `bias` of 1.0 returns 2.0 (eager).

    Inputs outside [0, 1] clamp to the band before scaling — defensive
    against malformed JSON overrides that escaped clamping at write
    time.
    """
    clamped = max(0.0, min(1.0, float(bias)))
    return ASPIRATION_BIAS_FACTOR_SCALE * clamped


def wealth_gap_factor(bankroll: int, target_min_buy_in: int) -> float:
    """Bell-curve over how rolled the AI is for the next tier.

    The "target" is `SAFE_BUY_IN_COUNT × target_min_buy_in` — a
    properly-rolled position at the next tier, not just one min
    buy-in. Real poker treats a 1-buy-in bankroll as terrible
    bankroll management; AIs should reflect that. The ratio of
    current bankroll to this target drives the bell curve:

      - **Ratio < ~0.25** (far below safe-roll): bankroll is too
        thin to commit. Stakers wouldn't back the ask anyway —
        capacity gate would filter them. Factor → 0.
      - **Ratio ≈ 0.5** (halfway to safe-roll): they're close. A
        stake bridges the gap and lets them sit comfortably at the
        target tier. Factor = 2 (peak).
      - **Ratio ≥ 1.0** (already safe-rolled): they can climb on
        their own via the normal `stake_up` movement path. Factor → 0.

    Returns 0 for degenerate inputs rather than dividing by zero.
    """
    if target_min_buy_in <= 0 or bankroll < 0:
        return 0.0
    target = WEALTH_GAP_SAFE_BUY_IN_COUNT * target_min_buy_in
    ratio = bankroll / target
    if ratio <= 0.0 or ratio >= 1.0:
        return 0.0
    # Bell curve centered at WEALTH_GAP_PEAK, linear to 0 at the edges.
    # |ratio - 0.5| ∈ [0, 0.5], multiplied by 4 to span [0, 2],
    # subtracted from peak height (1.0).
    distance_from_peak = abs(ratio - WEALTH_GAP_PEAK)
    height = max(0.0, 1.0 - 4.0 * distance_from_peak)
    return WEALTH_GAP_PEAK_MULTIPLIER * height


def compute_aspiration_probability(
    *,
    aspiration_bias: float,
    bankroll: int,
    target_min_buy_in: int,
    base_rate: float = DEFAULT_BASE_RATE,
) -> float:
    """Per-tick probability that this AI should attempt an aspiration ask.

    Composes the bias factor and wealth-gap factor against the floor
    rate; clamps to `MAX_ASPIRATION_PROB`. Zero `bias` short-circuits
    to 0 without ever multiplying — preserves the locked decision
    that `willing=False` personalities never ask.

    Returns 0 for any input that would produce a non-positive product
    (e.g. negative bankroll, zero target buy-in) — the trigger never
    fires in degenerate state.
    """
    bias_part = aspiration_bias_factor(aspiration_bias)
    if bias_part <= 0:
        return 0.0
    gap_part = wealth_gap_factor(bankroll, target_min_buy_in)
    if gap_part <= 0:
        return 0.0
    probability = base_rate * bias_part * gap_part
    return min(MAX_ASPIRATION_PROB, max(0.0, probability))

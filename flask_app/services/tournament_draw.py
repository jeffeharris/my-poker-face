"""The tournament "draw" — why an AI chooses to leave a cash table for a
tournament (the cash→tournament migration; see docs/plans/TOURNAMENTS_AS_A_DRAW.md).

This module is the PURE policy core: given each candidate persona's attributes,
it scores how strongly the tournament pulls them and ranks the top-N field. No
I/O, no Flask, no repos — the effectful builder that materializes `DrawInputs`
from repos lives separately (Phase B3), so the scoring formula stays trivially
unit-testable and the sim-tuning loop is fast.

The draw blends four terms (weights are sim-tunable — these are starting values):

    score = w_prize·prize_appeal
          + w_renown·renown_appeal
          + w_field·field_appeal
          - w_comfort·cash_comfort

  - prize_appeal  — the overlay-funded prize relative to the persona's OWN
                    bankroll. A small-bankroll persona sees a huge prize → pulled
                    hard; a rich grinder barely notices. This both drives the
                    draw AND aligns with the bank's redistribution goal (chips
                    flow toward the players who'll chase them).
  - renown_appeal — the renown/regard ON OFFER for winning, scaled by the
                    persona's status appetite and by how much upside they have
                    (a low-renown persona has more to gain by making a name).
  - field_appeal  — are high-renown "bigs" already likely in the field? A small
                    fish is pulled by the chance to sit with them; a big isn't.
  - cash_comfort  — a damp: a persona winning / settled deep at a good cash seat
                    resists the draw.

All terms are clamped to [0, 1] so the weights are the only magnitude knobs.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


def _clamp01(x: float) -> float:
    """Clamp to [0, 1]."""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


@dataclass(frozen=True)
class DrawInputs:
    """One persona's attributes for the draw score. Plain data — the effectful
    builder fills these from the bankroll / prestige / cash repos."""

    personality_id: str
    own_bankroll: int  # chips the persona has to their name
    own_renown: float  # 0..1, the persona's current field-relative renown
    status_appetite: float  # 0..1, status-seeking trait (scales renown pull)
    prize_pool: int  # chips the overlay funds for this tournament
    renown_on_offer: float  # 0..1, renown/regard a win grants (per-tournament)
    field_top_renown: float  # 0..1, highest renown likely in the field (bigs?)
    cash_comfort: float  # 0..1, 1 = winning / settled deep at a good cash seat


@dataclass(frozen=True)
class DrawWeights:
    """Per-term weights. Sim-tune; defaults are a starting shape."""

    prize: float = 0.40
    renown: float = 0.25
    field: float = 0.15
    cash_comfort: float = 0.20


DEFAULT_WEIGHTS = DrawWeights()


def score_draw(inp: DrawInputs, weights: DrawWeights = DEFAULT_WEIGHTS) -> float:
    """Pure draw score for one persona. Higher = pulled harder toward the
    tournament. Not normalized to any fixed range (weights set the scale), but
    each underlying term is in [0, 1]."""
    # The prize relative to your own bankroll. min(1) so a tiny-bankroll fish
    # (prize >> bankroll) maxes the term rather than dominating unboundedly.
    prize_appeal = _clamp01(inp.prize_pool / max(1, inp.own_bankroll))
    # Renown on offer, scaled by appetite AND remaining upside (low-renown
    # personas have the most to gain by making a name).
    renown_appeal = _clamp01(inp.renown_on_offer * inp.status_appetite * (1.0 - inp.own_renown))
    # Playing with the bigs pulls those who aren't bigs themselves.
    field_appeal = _clamp01(inp.field_top_renown * (1.0 - inp.own_renown))
    comfort = _clamp01(inp.cash_comfort)
    return (
        weights.prize * prize_appeal
        + weights.renown * renown_appeal
        + weights.field * field_appeal
        - weights.cash_comfort * comfort
    )


def rank_field(
    candidates: list[DrawInputs],
    field_size: int,
    weights: DrawWeights = DEFAULT_WEIGHTS,
    rng: random.Random | None = None,
    noise_sigma: float = 0.03,
) -> list[str]:
    """Return the `personality_id`s of the top `field_size` draws, highest first.

    A small Gaussian jitter (`noise_sigma`, on the [0,1]-ish score scale) breaks
    ties and keeps successive Main Events from fielding the identical cast when
    scores cluster. Pass `rng=None` for the deterministic (no-noise) ranking —
    used by tests and any caller that wants reproducibility."""
    if field_size <= 0 or not candidates:
        return []

    def _jittered(inp: DrawInputs) -> float:
        base = score_draw(inp, weights)
        if rng is None or noise_sigma <= 0:
            return base
        return base + rng.gauss(0.0, noise_sigma)

    # Sort by (jittered) score desc; stable tie-break on personality_id so a
    # no-rng ranking is fully deterministic.
    ranked = sorted(
        candidates,
        key=lambda inp: (-_jittered(inp), inp.personality_id),
    )
    return [inp.personality_id for inp in ranked[:field_size]]

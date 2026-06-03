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

import logging
import random
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


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


# --- The effectful builder (Phase B3) --------------------------------------
#
# Everything above is the pure scoring core. Below is the ONE non-pure thing in
# this module: it reads the live repos to materialize `DrawInputs`. It's kept
# behind lazy imports so the scoring core stays import-clean and trivially
# unit-testable (no Flask / repo deps at module load), per the module docstring.


# Max renown a Main-Event win grants, before per-persona scaling. Phase D sizes
# the real grant-on-win; this is the draw-time appetite knob (sim-tunable).
DEFAULT_RENOWN_ON_OFFER = 1.0


@dataclass(frozen=True)
class DrawContext:
    """The repos `build_draw_inputs` reads, bundled so the invite offer path
    threads ONE optional dependency instead of five. Inert unless
    `TOURNAMENT_DRAW_ENABLED` (the caller flag-gates; this is just the wiring)."""

    personality_repo: Any
    bankroll_repo: Any
    prestige_repo: Any
    cash_table_repo: Any
    ledger_repo: Any
    weights: DrawWeights = DEFAULT_WEIGHTS


def _seated_chips_by_pid(cash_table_repo, sandbox_id: str) -> dict:
    """Map currently-cash-seated AI `personality_id` → their seat chip stack.
    Best-effort: {} when unwired or the scan fails (→ 0 cash_comfort, no draw
    damp — the safe direction: an unread comfort pulls the persona slightly
    harder, it never wrongly pins them to a seat)."""
    if cash_table_repo is None:
        return {}
    try:
        out: dict = {}
        for tbl in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
            for slot in tbl.seats:
                if slot.get('kind') == 'ai' and slot.get('personality_id'):
                    out[slot['personality_id']] = slot.get('chips') or 0
        return out
    except Exception:  # noqa: BLE001
        logger.exception("draw: cash-seat scan failed for sandbox=%s", sandbox_id)
        return {}


def build_draw_inputs(
    ctx: DrawContext,
    *,
    sandbox_id: str,
    owner_id: Optional[str],
    field_size: int,
    buy_in: int = 0,
    starting_stack: int = 10_000,
) -> list[DrawInputs]:
    """Materialize one `DrawInputs` per eligible persona from the live repos.

    Reads are best-effort per term: a persona missing a bankroll / renown / ego
    row degrades to a neutral value rather than dropping the candidate, and any
    repo that throws degrades that whole term to its neutral default (the draw
    still ranks on the terms that did read). Returns [] only when the eligible
    pool itself is empty.
    """
    from cash_mode.economy_flags import RENOWN_V2_PERSIST_AI
    from flask_app.services import tournament_economy_service as econ

    pool = (
        ctx.personality_repo.list_eligible_for_cash_mode(user_id=owner_id)
        if ctx.personality_repo is not None
        else []
    )
    pids = [row['personality_id'] for row in pool if row.get('personality_id')]
    if not pids:
        return []

    # Prize pool — one read for the whole field (the same number for every
    # candidate; only prize/own_bankroll varies per persona). Autonomous baseline
    # (no human buy-in): this is a scoring input, not the actual funding.
    prize_pool = 0
    if ctx.ledger_repo is not None:
        try:
            prize_pool = econ.plan_funding(
                ledger_repo=ctx.ledger_repo,
                sandbox_id=sandbox_id,
                field_size=field_size,
                buy_in=buy_in,
                human_in=False,
            ).prize_pool
        except Exception:  # noqa: BLE001 — degrade to 0 prize_appeal
            logger.exception("draw: prize_pool read failed for sandbox=%s", sandbox_id)

    # Renown (renown v2). The peaks are uncapped raw points; field-normalize to
    # 0..1. When AI renown isn't persisted there's no renown economy, so BOTH
    # renown terms drop to 0 and the draw falls back to prize - comfort (the
    # documented graceful degradation).
    peaks: dict = {}
    if RENOWN_V2_PERSIST_AI and ctx.prestige_repo is not None:
        try:
            peaks = ctx.prestige_repo.load_renown_v2_peaks(sandbox_id, entity_kind="ai") or {}
        except Exception:  # noqa: BLE001
            logger.exception("draw: renown peaks read failed for sandbox=%s", sandbox_id)
    max_renown = max(peaks.values(), default=0.0)
    field_top_renown = 1.0 if max_renown > 0 else 0.0
    renown_on_offer = DEFAULT_RENOWN_ON_OFFER if max_renown > 0 else 0.0

    # Status appetite (the `ego` anchor) — side-effect-free batch read; neutral
    # 0.5 for any persona without a parseable anchor.
    ego: dict = {}
    if ctx.personality_repo is not None and hasattr(ctx.personality_repo, "load_ego_by_ids"):
        try:
            ego = ctx.personality_repo.load_ego_by_ids(pids)
        except Exception:  # noqa: BLE001
            logger.exception("draw: ego read failed")

    seat_chips = _seated_chips_by_pid(ctx.cash_table_repo, sandbox_id)

    inputs: list[DrawInputs] = []
    for pid in pids:
        bankroll = 0
        if ctx.bankroll_repo is not None:
            try:
                bankroll = (
                    ctx.bankroll_repo.load_ai_bankroll_current(pid, sandbox_id=sandbox_id) or 0
                )
            except Exception:  # noqa: BLE001
                logger.exception("draw: bankroll read failed for pid=%s", pid)
        own_renown = _clamp01(peaks.get(pid, 0.0) / max_renown) if max_renown > 0 else 0.0
        chips = seat_chips.get(pid)
        cash_comfort = _clamp01(chips / starting_stack) if chips and starting_stack > 0 else 0.0
        inputs.append(
            DrawInputs(
                personality_id=pid,
                own_bankroll=int(bankroll),
                own_renown=own_renown,
                status_appetite=ego.get(pid, 0.5),
                prize_pool=int(prize_pool),
                renown_on_offer=renown_on_offer,
                field_top_renown=field_top_renown,
                cash_comfort=cash_comfort,
            )
        )
    return inputs

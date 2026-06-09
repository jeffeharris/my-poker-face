"""Per-PLAYER preflop bet-SIZING personalities (the learnable size tell).

The frequency-shaping workstream made archetypes readable by how *often* they
3-bet. The obvious next step — a characteristic raise *size* per archetype (nit
min-raises, maniac overbets) — would make size readable too, but in the **wrong**
way: get 3-bet *once* and you instantly know the type (the Stacked/Poki caricature
that a serious player cracked in ~40 hands). See docs/plans/SIZING_TENDENCIES.md.

So sizing is a per-PLAYER personality with real within-player variance: a single
3-bet is ambiguous, and the signature only emerges after watching a player across
many hands + showdowns. This module is the **substrate** (Sequencing P1):

  * `SizingPersonality` — the immutable per-player structure (P1 carries only
    `base_size_bias`; P2+ palette behaviors plug into the same struct).
  * `sample_sizing_personality(anchors, persona_seed)` — deterministic,
    persona-seeded draw of `base_size_bias` from a per-archetype mean ± real
    spread (σ large enough that same-archetype players visibly differ, so absolute
    size is NOT type-diagnostic — the anti-caricature property). Stable across
    calls for the same persona (the Nemesis "perceived memory" property: a player
    always sizes the same way, so a read you earn stays true).
  * `resolve_size_multiplier(sizing_personality, context)` — consulted at the
    point the raise size is computed (action_mapper). P1 returns the
    `base_size_bias` center (context-independent); the `SizeContext` shape is
    defined now so P2+ (size_by_strength, position_blind, tilt_escalation, …) can
    extend it without touching the seam.

**Order of operations** (action_mapper): chart token (e.g. raise_3x) × **this size
multiplier (personality center)** → live jitter ±12% → human-round. The tendency
sets the center; jitter + rounding give the realistic wobble + clean amounts.

**Frequency-neutral by construction:** this only scales the *magnitude* of a raise;
it never touches which action fires. A multiplier of exactly 1.0 is a perfect
no-op (the deterministic sim / Baseline-GTO reference stays byte-identical).
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

LAYER = 'sizing_tendencies'

# Multiplier band. A sampled personality may lean small (min-raisey) or big
# (overbetty), but never collapses a raise or balloons it past sane bounds — the
# clamp in _compute_raise_to is the final safety net, but we keep the *center*
# realistic so jitter + rounding compose cleanly. ±~25% is the believable spread
# of human go-to sizings; the per-archetype means sit well inside it.
SIZE_MULT_MIN = 0.80
SIZE_MULT_MAX = 1.25


@dataclass(frozen=True)
class SizingPersonality:
    """A player's immutable preflop sizing personality.

    P1 carries only ``base_size_bias`` — the player's default open/3-bet size
    multiplier (1.0 = exactly the chart token). P2+ palette behaviors
    (``size_by_strength``, ``polarized_size``, ``position_blind``,
    ``tilt_escalation``, ``anchor_number``) attach here as additional immutable
    fields / a frozen ``behaviors`` map; ``resolve_size_multiplier`` will consult
    them. Until then ``behaviors`` is empty and only ``base_size_bias`` is read.

    A multiplier of exactly 1.0 (``base_size_bias == 1.0``, no behaviors) is the
    deterministic no-op: ``resolve_size_multiplier`` returns 1.0 and sizing is
    byte-identical to the pre-sizing-personality path.
    """

    base_size_bias: float = 1.0
    # Reserved for P2+ palette behaviors ((name, strength), …), same shape as
    # spot_tendencies. Empty in P1 — fully wired, intentionally unused.
    behaviors: Tuple[Tuple[str, float], ...] = ()

    @staticmethod
    def neutral() -> "SizingPersonality":
        """The identity personality — size multiplier is always 1.0 (no-op).

        Used for the Baseline-GTO reference and any controller without persona
        anchors, so the deterministic sim stays exact.
        """
        return SizingPersonality(base_size_bias=1.0, behaviors=())

    @property
    def is_neutral(self) -> bool:
        return self.base_size_bias == 1.0 and not self.behaviors


@dataclass(frozen=True)
class SizeContext:
    """Context handed to ``resolve_size_multiplier`` at the sizing seam.

    P1 consults NONE of these (the multiplier is context-independent — just the
    base bias). The shape is defined now so the P2+ palette behaviors can read it
    without changing the seam signature:

      * ``scenario`` — the preflop node scenario ('rfi' / 'vs_open' / 'vs_3bet' /
        'vs_4bet'); lets a behavior size differently for opens vs 3-bets.
      * ``hand_strength`` — the controller's preflop hand-strength class
        ('strong' / 'not_strong'); the consumer for ``size_by_strength``.
      * ``position`` — the hero's seat ('UTG' … 'BTN' / 'SB' / 'BB'); the consumer
        for ``position_blind``.
      * ``emotional_state`` — the psychology emotional-state label; the consumer
        for ``tilt_escalation``.
      * ``big_blind`` — blind unit, for ``anchor_number`` (fixate on one amount).
    """

    scenario: Optional[str] = None
    hand_strength: Optional[str] = None
    position: Optional[str] = None
    emotional_state: Optional[str] = None
    big_blind: int = 0


# ── Per-archetype sampling palette ─────────────────────────────────────
#
# (mean, sigma) for the base_size_bias draw, keyed by deviation-profile key. The
# MEAN leans by archetype (a maniac's go-to is a touch bigger, a nit's a touch
# smaller) but the SIGMA is large enough that same-archetype players visibly
# differ and the per-archetype distributions OVERLAP heavily — a given size maps
# to many types (many-to-many), which is what kills the one-shot caricature. The
# means are deliberately MILD (within ~±8% of 1.0) so the lean is a tendency you
# earn over many hands, not a tell you read in one orbit; the spread dominates the
# lean. Keys mirror DEVIATION_PROFILES; unknown keys fall back to ARCHETYPE_DEFAULT.
ARCHETYPE_SIZE_BIAS: Dict[str, Tuple[float, float]] = {
    # nit / rock: lean a hair small (the cautious min-raisey default), wide spread.
    'nit': (0.94, 0.08),
    'rock': (0.96, 0.08),
    # tag: balanced reg — centered, but still a real per-player spread.
    'tag': (1.00, 0.08),
    # calling_station / weak_fish: recreational, no disciplined default → centered
    # with the WIDEST spread (some min-raise, some splash big — unpredictable size).
    'calling_station': (1.00, 0.10),
    'weak_fish': (1.00, 0.11),
    # lag: aggressive reg, leans a touch big.
    'lag': (1.04, 0.09),
    # maniac: leans biggest (the overbet tendency) but still overlapping — some
    # maniacs min-raise to induce. Spread is wide so it's not a constant.
    'maniac': (1.06, 0.10),
}
# Fallback for keys not in the table (measurement-only profiles, future archetypes).
ARCHETYPE_DEFAULT: Tuple[float, float] = (1.00, 0.08)


def _persona_seed(persona_seed) -> int:
    """Derive a stable integer RNG seed from a persona identifier.

    Uses a content hash of the identifier so the same persona (by name / id)
    always samples the SAME sizing personality across calls and sessions — the
    "he always makes it 7" stability the design needs. Accepts an int (used as-is)
    or any stringifiable id (hashed). Does NOT touch the global RNG (CLAUDE.md
    "Pure Functions Without Side Effects").
    """
    if isinstance(persona_seed, int):
        return persona_seed
    h = hashlib.sha256(str(persona_seed).encode('utf-8')).hexdigest()
    return int(h[:16], 16)


def parse_sizing_tendencies(raw) -> Tuple[Tuple[str, float], ...]:
    """Normalize a personality config's ``sizing_tendencies`` to canonical form.

    Mirrors ``parse_spot_tendencies``: accepts a list/tuple of ``[name, strength]``
    pairs (JSON arrays from personalities.json) or ``((name, strength), …)``;
    ``None``/empty → ``()``. This is the per-personality override lane so a
    specific character can later pin a signature sizing behavior independent of
    the archetype-sampled default. In P1 the lane is fully wired but stock personas
    carry no ``sizing_tendencies`` key (the sampled ``base_size_bias`` is the
    default), so this returns ``()`` for them.
    """
    if not raw:
        return ()
    return tuple((str(name), float(strength)) for name, strength in raw)


def sample_sizing_personality(
    anchors,
    persona_seed,
    archetype_key: Optional[str] = None,
    sizing_tendencies: Tuple[Tuple[str, float], ...] = (),
) -> SizingPersonality:
    """Draw a deterministic, persona-seeded per-player sizing personality.

    P1 draws ONLY ``base_size_bias`` from the archetype's (mean, sigma) in
    ``ARCHETYPE_SIZE_BIAS`` using a LOCAL ``random.Random`` seeded from
    ``persona_seed`` — so the draw is stable for a given persona (same seed → same
    bias) and varies across personas, with NO global-RNG side effect.

    Args:
        anchors: personality anchors (carries baseline_looseness/aggression; used
            to classify the archetype when ``archetype_key`` isn't supplied).
        persona_seed: stable persona identifier (player_name / persona id / int).
            Hashed to seed the local RNG so the personality is consistent.
        archetype_key: explicit deviation-profile key (e.g. 'maniac'). Preferred
            when known (handles loadouts like ``weak_fish`` that anchor
            classification can't see); falls back to anchor classification.
        sizing_tendencies: P2+ override-lane behaviors ((name, strength), …),
            carried through onto the returned struct's ``behaviors``. Empty in P1.

    Returns:
        A frozen ``SizingPersonality``. Clamped to [SIZE_MULT_MIN, SIZE_MULT_MAX].
    """
    if archetype_key is None and anchors is not None:
        # Local import to avoid a module-load cycle (deviation_profiles imports
        # archetypes; this stays leaf-level).
        try:
            from .deviation_profiles import select_deviation_profile_key

            archetype_key = select_deviation_profile_key(anchors)
        except Exception:
            archetype_key = None

    mean, sigma = ARCHETYPE_SIZE_BIAS.get(archetype_key or '', ARCHETYPE_DEFAULT)

    rng = random.Random(_persona_seed(persona_seed))
    bias = rng.gauss(mean, sigma)
    bias = max(SIZE_MULT_MIN, min(bias, SIZE_MULT_MAX))

    return SizingPersonality(
        base_size_bias=bias,
        behaviors=tuple(sizing_tendencies),
    )


def resolve_size_multiplier(
    sizing_personality: Optional[SizingPersonality],
    context: Optional[SizeContext] = None,
) -> float:
    """Resolve the preflop raise-size multiplier for the current decision.

    P1: returns the personality's ``base_size_bias`` (context-independent). The
    ``context`` is accepted now so P2+ palette behaviors can layer
    strength/position/emotion adjustments on top without changing this signature.

    A ``None`` personality (or the neutral one) returns exactly 1.0 — the
    deterministic no-op that keeps the Baseline-GTO reference byte-identical.
    """
    if sizing_personality is None:
        return 1.0
    return float(sizing_personality.base_size_bias)

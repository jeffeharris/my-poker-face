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
#
# The band clamps the COMPOSED multiplier too: base_size_bias × any palette factor
# (P2: size_by_strength) is clamped back into [MIN, MAX] so a big-biased recreational
# player with a strong hand can't balloon the raise past sane bounds (and the
# small-biased weak-hand corner can't collapse it). The clamp is generous enough
# that the strength split stays clearly visible inside it (see SIZE_BY_STRENGTH_*).
SIZE_MULT_MIN = 0.80
SIZE_MULT_MAX = 1.25

# size_by_strength (the recreational "big = strong" tell, Sequencing P2). When a
# persona carries this behavior, the preflop multiplier scales UP for a strong hand
# and DOWN for a not-strong hand by `gap × strength`, composed multiplicatively on
# top of base_size_bias. `gap` is the FULL strong-vs-weak swing at strength 1.0; the
# half-gap is applied in each direction so the per-player CENTER (mean over a
# strength-balanced hand mix) stays ≈ base_size_bias — the absolute size is still
# not type-diagnostic, only its CORRELATION with showdown strength is the tell.
#
# GAP is deliberately kept BELOW the live ±12% sizing jitter band so the strong and
# weak size distributions OVERLAP heavily — a single observed raise is ambiguous and
# the read only resolves over many raises + showdowns ("legible but not instant",
# docs/plans/SIZING_TENDENCIES.md §Validation). A larger gap (e.g. 0.30) cleanly
# SEPARATES the bands and reads in a handful of opens — the Stacked/Poki caricature
# the design exists to avoid (validated via scripts/sizing_hands_to_read.py: gap 0.30
# → AUC≈0.95 by ~4 observed opens; gap 0.10 → the read emerges over dozens). The
# leak stays a realistic, modest EV cost rather than a giant one.
SIZE_BY_STRENGTH_GAP = 0.10
SIZE_BY_STRENGTH = 'size_by_strength'

# Archetype-weighted sampling probabilities for palette behaviors, keyed by
# deviation-profile key (P2: only `size_by_strength`). The recreational tiers carry
# the obvious tell (they ARE the skill gradient — the fish you can read over time);
# the competent / disciplined archetypes stay clean and unreadable on size. Putting
# the amateur tell on a "reg" is the Drivatar "learned the bad habits" mistake the
# design warns against (docs/plans/SIZING_TENDENCIES.md §Drivatars), so tag and the
# disciplined tiers (nit/rock/lag/maniac) are ZERO here — regs carry no size tell.
# (P3 gives the strong regs the *advanced* polarized_size instead.)
SIZE_BY_STRENGTH_WEIGHTS: Dict[str, float] = {
    'calling_station': 0.65,
    'weak_fish': 0.70,
    # Disciplined / competent — clean, no learnable size tell.
    'nit': 0.0,
    'rock': 0.0,
    'tag': 0.0,
    'lag': 0.0,
    'maniac': 0.0,
}
# Unknown / measurement-only keys default to clean (no tell) — never import the
# leak onto a profile we don't explicitly mark recreational.
SIZE_BY_STRENGTH_WEIGHT_DEFAULT: float = 0.0


@dataclass(frozen=True)
class SizingPersonality:
    """A player's immutable preflop sizing personality.

    ``base_size_bias`` is the player's default open/3-bet size multiplier
    (1.0 = exactly the chart token). ``behaviors`` carries sampled/pinned palette
    behaviors as ``((name, strength), …)`` (same shape as spot_tendencies);
    ``resolve_size_multiplier`` consults them. P2 ships ``size_by_strength`` (the
    recreational "big = strong" tell); P3 adds ``polarized_size``,
    ``position_blind``, ``tilt_escalation``, ``anchor_number``.

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
        sizing_tendencies: per-personality override-lane behaviors
            ((name, strength), …). When supplied they PIN the behaviors explicitly
            (a specific character's signature) and the archetype-weighted palette
            draw is skipped — the override lane wins. Empty → archetype-weighted draw.

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
    # base_size_bias draw FIRST so P1's persona-seeded bias values are byte-unchanged
    # (the palette draw below consumes the RNG stream only AFTER this — the committed
    # P1 determinism tests + histogram must still hold).
    bias = rng.gauss(mean, sigma)
    bias = max(SIZE_MULT_MIN, min(bias, SIZE_MULT_MAX))

    # Palette behaviors. An explicit override pins them (the per-character signature
    # lane); otherwise draw the archetype-weighted palette (P2: only size_by_strength).
    if sizing_tendencies:
        behaviors = tuple(sizing_tendencies)
    else:
        behaviors = _sample_palette_behaviors(rng, archetype_key)

    return SizingPersonality(
        base_size_bias=bias,
        behaviors=behaviors,
    )


def _sample_palette_behaviors(
    rng: random.Random, archetype_key: Optional[str]
) -> Tuple[Tuple[str, float], ...]:
    """Draw 0–N palette behaviors by archetype-weighted probability (Sequencing P2).

    P2 ships ONE palette behavior — ``size_by_strength`` — carried only by the
    recreational tiers (calling_station / weak_fish); the disciplined / competent
    archetypes draw nothing (regs stay clean, no learnable size tell). Strength is
    fixed at 1.0 for the sampled behavior (the per-player magnitude variety already
    comes from base_size_bias + the live jitter); a specific character wanting a
    softer/sharper tell uses the explicit ``sizing_tendencies`` override lane.

    Consumes the RNG stream AFTER the base_size_bias draw, so P1 bias values are
    byte-unchanged. Returns ``()`` for clean archetypes.
    """
    p = SIZE_BY_STRENGTH_WEIGHTS.get(archetype_key or '', SIZE_BY_STRENGTH_WEIGHT_DEFAULT)
    if p > 0.0 and rng.random() < p:
        return ((SIZE_BY_STRENGTH, 1.0),)
    return ()


def _behavior_strength(personality: SizingPersonality, name: str) -> Optional[float]:
    """The strength of a palette behavior on the personality, or None if absent."""
    for bname, strength in personality.behaviors:
        if bname == name:
            return float(strength)
    return None


def resolve_size_multiplier(
    sizing_personality: Optional[SizingPersonality],
    context: Optional[SizeContext] = None,
) -> float:
    """Resolve the preflop raise-size multiplier for the current decision.

    Starts from the personality's ``base_size_bias`` (the per-player center), then
    folds in any context-driven palette behaviors:

      * ``size_by_strength`` (P2): when the persona carries it AND
        ``context.hand_strength`` is set, scales the multiplier UP for a 'strong'
        hand and DOWN for a 'not_strong' hand, by ``half-gap × strength`` in each
        direction (so the per-player center is preserved and only the size↔strength
        CORRELATION is the tell). Composed multiplicatively on the base bias and the
        result clamped to [SIZE_MULT_MIN, SIZE_MULT_MAX].

    ``context is None`` or ``context.hand_strength is None`` (or no behavior) →
    behaves exactly as P1 (base bias only). A ``None`` personality (or the neutral
    one) returns exactly 1.0 — the deterministic no-op that keeps the Baseline-GTO
    reference byte-identical.
    """
    if sizing_personality is None:
        return 1.0
    mult = float(sizing_personality.base_size_bias)

    if context is not None and context.hand_strength is not None:
        s = _behavior_strength(sizing_personality, SIZE_BY_STRENGTH)
        if s is not None:
            half_gap = 0.5 * SIZE_BY_STRENGTH_GAP * s
            if context.hand_strength == 'strong':
                mult *= 1.0 + half_gap
            else:  # 'not_strong' (or any non-strong class) → size down
                mult *= 1.0 - half_gap

    return max(SIZE_MULT_MIN, min(mult, SIZE_MULT_MAX))

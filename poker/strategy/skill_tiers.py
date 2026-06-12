"""Named skill tiers for tiered (`sharp`) bots — a preset bundle over the
existing per-instance intensity scalars, so the field has range from genuinely
sharp to believably mediocre. See docs/plans/PLAYER_SKILL_SPECTRUM.md.

Skill owns the **adaptive / discipline axis only**: how hard the bot reads and
exploits opponents, how balanced (un-face-up) its river is, how well it defends
its capped check range, and how big it sizes its value bets. Preflop width
charts (`archetype_preflop_tables`) and postflop personality distortion
(`DEVIATION_PROFILES`) are SEPARATE per-archetype axes that compose with this —
a believable weak player is a loose chart (already) + low skill intensities
(here).

Each tier sets four plain controller attributes post-construction (the same
seam the eval harness already drives):

  - ``exploitation_strength``  — scales the whole opponent-exploitation layer
  - ``river_bluff_fraction``   — share of give-up air promoted to a balancing
                                 river bet (0.0 = face-up river)
  - ``stab_defense_intensity`` — fold→call shift vs detected stabbers
                                 (0.0 = over-folds the capped check range)
  - ``overbet_fraction``       — share of value bet-mass sized as an overbet

``adaptation_bias`` is intentionally NOT set here. It lives on the frozen
``PersonalityAnchors`` (authored per-personality in personalities.json) and
already multiplies into the exploitation product alongside
``exploitation_strength`` (``adaptation_bias × … × exploitation_strength``,
see exploitation.py). Driving the adaptive axis through ``exploitation_strength``
alone avoids double-counting that product and leaves each persona's authored
personality intact. (PLAYER_SKILL_SPECTRUM.md, Decision 1.)

Reconciliation with the code (PLAYER_SKILL_SPECTRUM.md): the spec's draft table
assumed today's bot was a ``reg`` at ``exploitation_strength=0.7`` with a
sharper ``shark`` above it. The constructor defaults are actually
``(1.0, 1.0, 0.5, 1.0)`` — i.e. today's production bot is ALREADY at the
validated ceiling. So ``shark`` == today's defaults (no headroom above it,
"no sharper than validated"), and ``reg``/``weak_reg``/``rec`` are progressively
WEAKER new tiers below it. ``DEFAULT_SKILL_TIER`` is therefore ``shark`` and
applying it is a no-op — nothing changes until a non-default tier is assigned
(Phase 4 roster work).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillTier:
    """A coherent bundle of skill-intensity scalars. Every value is at or below
    the validated ceiling (``shark``); weakening can only make a bot more
    exploitable/face-up, which is the intent — so the weak tiers need no eval."""

    name: str
    exploitation_strength: float
    river_bluff_fraction: float
    stab_defense_intensity: float
    overbet_fraction: float
    # Disciplined fold-to-3bet vs a value-heavy 3-bettor — the per-player default
    # for TieredBotController.vs3bet_exploit (over-fold the marginal continue when
    # the villain under-bluffs). An EXPLOITATION behaviour, so it grades with this
    # tier (unlike push_fold_nash, a binary elite weapon that's curated per-persona).
    # A sticky rec doesn't make the read (0.0); a shark folds disciplined (0.85).
    vs3bet_exploit: float = 0.5


# The ladder, sharpest → weakest. `shark` equals the TieredBotController
# constructor defaults (the validated ceiling); each weaker tier is monotone
# non-increasing in every knob. Values illustrative — Phase 3 validates the
# ladder is cleanly monotone against the adaptive reader/stabber instruments
# and tunes from there.
SKILL_TIERS = {
    # reads + balances + defends at full validated intensity — today's bot.
    'shark': SkillTier(
        'shark',
        exploitation_strength=1.0,
        river_bluff_fraction=1.0,
        stab_defense_intensity=0.5,
        overbet_fraction=1.0,
        vs3bet_exploit=0.85,
    ),
    # solid: softer reads, still balanced + defends.
    'reg': SkillTier(
        'reg',
        exploitation_strength=0.7,
        river_bluff_fraction=1.0,
        stab_defense_intensity=0.5,
        overbet_fraction=1.0,
        vs3bet_exploit=0.55,
    ),
    # half-baked: semi-face-up river, soft adapt, half-hearted defense + sizing.
    'weak_reg': SkillTier(
        'weak_reg',
        exploitation_strength=0.4,
        river_bluff_fraction=0.5,
        stab_defense_intensity=0.25,
        overbet_fraction=0.5,
        vs3bet_exploit=0.3,
    ),
    # rec: face-up river, over-folds to stabs, barely adapts, no overbets.
    'rec': SkillTier(
        'rec',
        exploitation_strength=0.1,
        river_bluff_fraction=0.0,
        stab_defense_intensity=0.0,
        overbet_fraction=0.0,
        vs3bet_exploit=0.0,
    ),
}

# The default/no-op tier: equals the constructor defaults, so leaving a bot at
# the default tier preserves today's behavior exactly (and never stomps
# post-construction customization — see apply_skill_tier).
DEFAULT_SKILL_TIER = 'shark'


# adaptation_bias → tier band cutoffs. The hand-authored roster
# (personalities.json, PLAYER_SKILL_SPECTRUM.md Phase 4) keyed each persona's
# `skill` to its `anchors.adaptation_bias`, quantized to four values: shark=0.70,
# reg=0.50, weak_reg=0.30-0.40, rec=0.15. These cutoffs sit at the midpoints
# between those bands, so a persona authored at a roster value lands in its
# roster tier. Centralized here so both the LLM-persona generator
# (poker/personality_generator.py) and the DB backfill derive `skill` the same
# way — and a derived skill can never contradict the anchors it came from.
_ADAPTATION_BIAS_BANDS = (
    (0.60, 'shark'),
    (0.45, 'reg'),
    (0.225, 'weak_reg'),
)


def skill_tier_for_adaptation_bias(adaptation_bias) -> str:
    """Map a persona's ``anchors.adaptation_bias`` to a named skill tier.

    Mirrors how the authored roster assigned `skill` (see _ADAPTATION_BIAS_BANDS).
    A higher adaptation_bias (the persona reads/adjusts more) earns a sharper
    tier. Returns one of the keys in ``SKILL_TIERS``.

    ``None`` (no adaptation_bias on the anchors at all) falls back to
    ``DEFAULT_SKILL_TIER`` — i.e. today's no-op ceiling, so an info-free persona
    is never silently weakened. In practice personas always carry anchors (the
    generator's ``_default_anchors`` sets adaptation_bias=0.50 → ``reg``), so the
    None branch is purely defensive.
    """
    if adaptation_bias is None:
        return DEFAULT_SKILL_TIER
    for cutoff, tier in _ADAPTATION_BIAS_BANDS:
        if adaptation_bias >= cutoff:
            return tier
    return 'rec'


def apply_skill_tier(controller, tier: str = DEFAULT_SKILL_TIER) -> None:
    """Set the skill-intensity fields on ``controller`` from the named ``tier``.

    Called at build time AFTER construction (production: tiered_factory; eval:
    the harness), mirroring how the fish path sets ``_deviation_profile`` /
    ``skip_equity_in_analysis`` post-build.

    The default tier (``shark``) is a deliberate no-op: its values already equal
    the constructor defaults, so writing them would be byte-identical EXCEPT it
    would clobber any field a caller customized between construction and here
    (e.g. the fish path tweaks ``overbet_fraction``). Skipping the write keeps
    "default changes nothing" literally true. (PLAYER_SKILL_SPECTRUM.md, Decision 3.)

    Raises:
        KeyError: if ``tier`` is not a known tier name.
    """
    if tier == DEFAULT_SKILL_TIER:
        return
    spec = SKILL_TIERS[tier]  # KeyError on unknown tier — fail loud, not silent
    controller.exploitation_strength = spec.exploitation_strength
    controller.river_bluff_fraction = spec.river_bluff_fraction
    controller.stab_defense_intensity = spec.stab_defense_intensity
    controller.overbet_fraction = spec.overbet_fraction

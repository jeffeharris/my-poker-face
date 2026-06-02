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


# The ladder, sharpest → weakest. `shark` equals the TieredBotController
# constructor defaults (the validated ceiling); each weaker tier is monotone
# non-increasing in every knob. Values illustrative — Phase 3 validates the
# ladder is cleanly monotone against the adaptive reader/stabber instruments
# and tunes from there.
SKILL_TIERS = {
    # reads + balances + defends at full validated intensity — today's bot.
    'shark': SkillTier('shark', exploitation_strength=1.0, river_bluff_fraction=1.0, stab_defense_intensity=0.5, overbet_fraction=1.0),
    # solid: softer reads, still balanced + defends.
    'reg': SkillTier('reg', exploitation_strength=0.7, river_bluff_fraction=1.0, stab_defense_intensity=0.5, overbet_fraction=1.0),
    # half-baked: semi-face-up river, soft adapt, half-hearted defense + sizing.
    'weak_reg': SkillTier('weak_reg', exploitation_strength=0.4, river_bluff_fraction=0.5, stab_defense_intensity=0.25, overbet_fraction=0.5),
    # rec: face-up river, over-folds to stabs, barely adapts, no overbets.
    'rec': SkillTier('rec', exploitation_strength=0.1, river_bluff_fraction=0.0, stab_defense_intensity=0.0, overbet_fraction=0.0),
}

# The default/no-op tier: equals the constructor defaults, so leaving a bot at
# the default tier preserves today's behavior exactly (and never stomps
# post-construction customization — see apply_skill_tier).
DEFAULT_SKILL_TIER = 'shark'


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

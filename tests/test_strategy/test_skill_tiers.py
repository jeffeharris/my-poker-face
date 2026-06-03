"""Tests for the player skill spectrum (PLAYER_SKILL_SPECTRUM.md, Phase 2).

The skill tier is a preset bundle over four per-instance intensity scalars. The
helper sets them post-construction; the default (`shark`) tier is a no-op so
"nothing changes until a non-default tier is assigned" holds literally.
"""

import pytest

from poker.strategy.skill_tiers import (
    DEFAULT_SKILL_TIER,
    SKILL_TIERS,
    SkillTier,
    apply_skill_tier,
    skill_tier_for_adaptation_bias,
)

# The four intensity fields a tier owns.
_FIELDS = (
    'exploitation_strength',
    'river_bluff_fraction',
    'stab_defense_intensity',
    'overbet_fraction',
)


class _Bot:
    """Stand-in for a controller — apply_skill_tier only sets plain attributes."""


def test_default_tier_is_shark():
    assert DEFAULT_SKILL_TIER == 'shark'
    assert DEFAULT_SKILL_TIER in SKILL_TIERS


def test_shark_equals_validated_ceiling():
    # The reconciliation: shark == today's TieredBotController constructor
    # defaults (exploitation getattr-default 1.0, river_bluff 1.0, stab 0.5,
    # overbet 1.0). If a constructor default changes, this should change with it.
    shark = SKILL_TIERS['shark']
    assert (
        shark.exploitation_strength,
        shark.river_bluff_fraction,
        shark.stab_defense_intensity,
        shark.overbet_fraction,
    ) == (1.0, 1.0, 0.5, 1.0)


@pytest.mark.parametrize('tier', ['reg', 'weak_reg', 'rec'])
def test_apply_sets_four_fields_from_spec(tier):
    bot = _Bot()
    apply_skill_tier(bot, tier)
    spec = SKILL_TIERS[tier]
    for field in _FIELDS:
        assert getattr(bot, field) == getattr(spec, field)


def test_default_tier_is_a_noop():
    # shark must not write anything — so a value customized post-construction
    # (e.g. the fish path tweaking overbet_fraction) survives.
    bot = _Bot()
    bot.overbet_fraction = 0.42
    apply_skill_tier(bot, 'shark')
    assert bot.overbet_fraction == 0.42
    # and it doesn't materialize the other fields out of thin air
    for field in ('exploitation_strength', 'river_bluff_fraction', 'stab_defense_intensity'):
        assert not hasattr(bot, field)


def test_default_arg_is_noop():
    # Calling with no tier arg uses the default → same no-op guarantee.
    bot = _Bot()
    apply_skill_tier(bot)
    assert not any(hasattr(bot, f) for f in _FIELDS)


def test_unknown_tier_raises():
    with pytest.raises(KeyError):
        apply_skill_tier(_Bot(), 'genius')


def test_ladder_is_monotone_non_increasing():
    # shark >= reg >= weak_reg >= rec for every knob — the precondition that
    # makes the spectrum a trustworthy strength ladder.
    order = ['shark', 'reg', 'weak_reg', 'rec']
    for field in _FIELDS:
        values = [getattr(SKILL_TIERS[t], field) for t in order]
        assert values == sorted(values, reverse=True), f'{field} not monotone: {values}'


def test_no_tier_exceeds_the_ceiling():
    # No tier is "sharper than validated": every value is <= shark's.
    ceiling = SKILL_TIERS['shark']
    for tier in SKILL_TIERS.values():
        for field in _FIELDS:
            assert getattr(tier, field) <= getattr(ceiling, field)


def test_tier_name_matches_key():
    for key, spec in SKILL_TIERS.items():
        assert isinstance(spec, SkillTier)
        assert spec.name == key


# --- skill_tier_for_adaptation_bias (roster band map) ---


@pytest.mark.parametrize(
    "adaptation_bias,expected",
    [
        # The four authored-roster band centers land in their own tier.
        (0.70, 'shark'),
        (0.50, 'reg'),
        (0.40, 'weak_reg'),
        (0.30, 'weak_reg'),
        (0.15, 'rec'),
        # Extremes.
        (1.0, 'shark'),
        (0.0, 'rec'),
    ],
)
def test_band_map_roster_values(adaptation_bias, expected):
    assert skill_tier_for_adaptation_bias(adaptation_bias) == expected


@pytest.mark.parametrize(
    "adaptation_bias,expected",
    [
        # On/around the cutoffs (>= is the sharper side).
        (0.60, 'shark'),
        (0.5999, 'reg'),
        (0.45, 'reg'),
        (0.4499, 'weak_reg'),
        (0.225, 'weak_reg'),
        (0.2249, 'rec'),
    ],
)
def test_band_map_cutoff_boundaries(adaptation_bias, expected):
    assert skill_tier_for_adaptation_bias(adaptation_bias) == expected


def test_band_map_none_falls_back_to_default_tier():
    # No information -> today's no-op ceiling, never silently weakened.
    assert skill_tier_for_adaptation_bias(None) == DEFAULT_SKILL_TIER


def test_band_map_always_returns_a_known_tier():
    for ab in (None, 0.0, 0.1, 0.225, 0.3, 0.45, 0.5, 0.6, 0.7, 1.0):
        assert skill_tier_for_adaptation_bias(ab) in SKILL_TIERS


def test_band_map_is_monotone_non_decreasing_in_strength():
    # Higher adaptation_bias must never yield a WEAKER tier (lower
    # exploitation_strength). Walk the range and assert strength is monotone.
    order = ['rec', 'weak_reg', 'reg', 'shark']  # weakest -> sharpest
    prev_rank = -1
    for i in range(0, 101):
        ab = i / 100.0
        rank = order.index(skill_tier_for_adaptation_bias(ab))
        assert rank >= prev_rank, f"tier dropped at adaptation_bias={ab}"
        prev_rank = rank

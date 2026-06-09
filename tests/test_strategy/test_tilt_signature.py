"""Tilt behavioral signature §4 (TILT_EXCURSION_DESIGN.md): compute_trait_offsets'
emotional direction.

OFF (default) => state-driven (tilted=aggressive for everyone, shaken=passive).
ON (TILT_SIGNATURE_ENABLED) => under a TILT state the direction is character-driven
by risk_identity: risk-seekers SPEW (aggressive), risk-averse COLLAPSE (passive).
Overconfident (a confidence state, not tilt) is unaffected. Magnitude is unchanged
— only the direction flips — so no new double-count with the other tilt layers.
"""

import os
from types import SimpleNamespace
from unittest import mock

import numpy as np

from poker.psychology_model import PersonalityAnchors
from poker.strategy.deviation_profiles import DEVIATION_PROFILES
from poker.strategy.personality_modifier import compute_trait_offsets

FLAG = 'TILT_SIGNATURE_ENABLED'
ACTIONS = ['fold', 'call', 'raise_2.5bb']  # fold / passive / aggressive
RAISE = ACTIONS.index('raise_2.5bb')
CALL = ACTIONS.index('call')
PROFILE = DEVIATION_PROFILES['tag']


def _anchors(risk: float) -> PersonalityAnchors:
    return PersonalityAnchors(
        baseline_aggression=0.5,
        baseline_looseness=0.5,
        ego=0.5,
        poise=0.3,
        expressiveness=0.5,
        risk_identity=risk,
        adaptation_bias=0.5,
        baseline_energy=0.5,
        recovery_rate=0.15,
    )


def _offsets(state: str, risk: float, flag: str) -> np.ndarray:
    es = SimpleNamespace(state=state, intensity=0.6, severity='moderate')
    with mock.patch.dict(os.environ, {FLAG: flag}):
        return compute_trait_offsets(ACTIONS, _anchors(risk), es, PROFILE)


def test_low_risk_tilted_collapses_when_on():
    """Risk-averse + tilted: off leans aggressive (state map), on flips to passive
    (collapse) — raise penalized, call boosted relative to off."""
    off = _offsets('tilted', risk=0.2, flag='0')
    on = _offsets('tilted', risk=0.2, flag='1')
    assert on[RAISE] < off[RAISE]
    assert on[CALL] > off[CALL]


def test_high_risk_tilted_spews_unchanged():
    """Risk-seeker + tilted is aggressive both ways -> identical offsets."""
    off = _offsets('tilted', risk=0.8, flag='0')
    on = _offsets('tilted', risk=0.8, flag='1')
    assert np.allclose(on, off)


def test_high_risk_shaken_flips_to_spew():
    """Risk-seeker + shaken: off is passive (state map), on flips to aggressive
    (spew) — raise boosted relative to off."""
    off = _offsets('shaken', risk=0.8, flag='0')
    on = _offsets('shaken', risk=0.8, flag='1')
    assert on[RAISE] > off[RAISE]


def test_overconfident_unaffected_by_signature():
    """Overconfident is a confidence state, not tilt -> state map both ways."""
    off = _offsets('overconfident', risk=0.2, flag='0')
    on = _offsets('overconfident', risk=0.2, flag='1')
    assert np.allclose(on, off)

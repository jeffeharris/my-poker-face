"""Probability-rolled tilt-episode duration in PlayerPsychology.recover()
(EMOTIONAL_SYSTEM_BALANCE.md §4.2, flag EMOTIONAL_REBALANCE_ENABLED).

On tilt entry the persona rolls D = "hands held in tilt" (steep in 1-poise, capped),
near-freezes composure recovery for D hands, then briskly climbs out. This pins the
*mechanism*: a hothead gets a long, sustained, but bounded episode; a high-poise monk
rolls ~0; off-flag the hold is inert (normal recovery). Frequency *fit* (E[D] vs PRD
bands) lives in the EXP_009 sims, not here.

We patch the gate FUNCTION (mirrors test_tilt_persistence.py) rather than os.environ.
"""

import statistics
from unittest import mock

from poker.player_psychology import (
    TILT_EPISODE_MAX_HANDS,
    TILT_LINE,
    PlayerPsychology,
)

GATE = 'poker.player_psychology._emotional_rebalance_enabled'


def _persona(poise: float) -> dict:
    return {
        'anchors': {
            'baseline_aggression': 0.6,
            'baseline_looseness': 0.4,
            'ego': 0.5,
            'poise': poise,
            'expressiveness': 0.6,
            'risk_identity': 0.6,
            'adaptation_bias': 0.5,
            'baseline_energy': 0.5,
            'recovery_rate': 0.15,
            'self_belief': 0.5,
        }
    }


def _hands_held(poise: float, enabled: bool) -> int:
    """Knock composure just below the tilt line (where plain recovery would climb out
    in a hand or two), then count hands recover() keeps it below. With the episode
    mechanism on, this is ~the rolled D; off it is the prompt plain recovery."""
    psy = PlayerPsychology.from_personality_config('P', _persona(poise))
    psy.axes = psy.axes.update(composure=TILT_LINE - 0.02)
    with mock.patch(GATE, return_value=enabled):
        held = 0
        for _ in range(30):
            psy.recover()
            if psy.axes.composure < TILT_LINE:
                held += 1
            else:
                break
    return held


def _mean_held(poise: float, enabled: bool, trials: int = 8) -> float:
    return statistics.mean(_hands_held(poise, enabled) for _ in range(trials))


def test_episode_extends_tilt_when_on_vs_off():
    """A hothead's tilt lasts materially longer with the mechanism on than off."""
    on = _mean_held(0.25, enabled=True)
    off = _mean_held(0.25, enabled=False)
    assert on > off + 2  # the held episode clearly outlasts plain recovery


def test_episode_scales_with_poise():
    """Low-poise hothead gets long episodes; high-poise monk rolls ~0."""
    hothead = _mean_held(0.25, enabled=True)
    monk = _mean_held(0.85, enabled=True)
    assert hothead > monk
    assert monk <= 4  # a monk's episode is short/near-zero


def test_episode_is_bounded():
    """No rolled episode exceeds the believability ceiling (non-chronic)."""
    psy = PlayerPsychology.from_personality_config('P', _persona(0.0))  # max proneness
    with mock.patch(GATE, return_value=True):
        for _ in range(20):
            d = psy._roll_tilt_episode_len()
            assert 0 <= d <= TILT_EPISODE_MAX_HANDS


def test_off_flag_inert():
    """Flag off: recover() does not hold tilt — composure climbs out promptly."""
    held = _hands_held(0.25, enabled=False)
    assert held <= 6  # plain recovery, no episode hold

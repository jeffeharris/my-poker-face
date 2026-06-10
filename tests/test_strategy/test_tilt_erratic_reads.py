"""Tilt coupling §4 (TILT_EXCURSION_DESIGN.md): _zone_to_tilt_factor.

OFF (default) => the legacy deterministic cliff (1.0 / 0.5 / 0.0). ON
(TILT_ERRATIC_READS_ENABLED) => an erratic random taper scaled by tilt intensity,
one draw per decision (memoized on the threaded emotional_state), no hard 0.0.
"""

import os
from types import SimpleNamespace
from unittest import mock

from poker.tiered_bot_controller import TieredBotController

FLAG = 'TILT_ERRATIC_READS_ENABLED'


class _RNG:
    def __init__(self, v: float):
        self._v = v

    def random(self) -> float:
        return self._v


def _controller(roll: float) -> TieredBotController:
    c = TieredBotController.__new__(TieredBotController)
    c.rng = _RNG(roll)
    return c


def _shift(state: str, intensity: float = 0.8):
    return SimpleNamespace(state=state, severity='extreme', intensity=intensity)


def test_off_is_the_legacy_cliff():
    with mock.patch.dict(os.environ, {FLAG: '0'}):
        c = _controller(roll=0.0)
        assert c._zone_to_tilt_factor(None) == 1.0
        assert c._zone_to_tilt_factor(_shift('composed')) == 1.0
        assert c._zone_to_tilt_factor(_shift('tilted')) == 0.5
        assert c._zone_to_tilt_factor(_shift('overconfident')) == 0.5
        assert c._zone_to_tilt_factor(_shift('shaken')) == 0.0
        assert c._zone_to_tilt_factor(_shift('dissociated')) == 0.0


def test_on_no_hard_zero_and_tapers_with_intensity():
    with mock.patch.dict(os.environ, {FLAG: '1'}):
        # worst-case draw (1.0) at extreme intensity 0.8 -> 1 - 0.8 = 0.2, never 0.0
        c = _controller(roll=1.0)
        assert abs(c._zone_to_tilt_factor(_shift('shaken', 0.8)) - 0.2) < 1e-9
        # composed is always 1.0 regardless of flag
        assert c._zone_to_tilt_factor(_shift('composed')) == 1.0


def test_on_best_draw_is_full_strength():
    with mock.patch.dict(os.environ, {FLAG: '1'}):
        c = _controller(roll=0.0)  # draw 0 -> factor 1.0 (trusts the read this time)
        assert c._zone_to_tilt_factor(_shift('tilted', 0.5)) == 1.0


def test_on_memoized_once_per_decision():
    """Same emotional_state object (one decision) -> same factor across layers;
    a new object (next decision) re-draws."""
    rolls = iter([0.5, 0.9])
    c = TieredBotController.__new__(TieredBotController)
    c.rng = SimpleNamespace(random=lambda: next(rolls))
    with mock.patch.dict(os.environ, {FLAG: '1'}):
        s1 = _shift('tilted', 0.6)
        f1a = c._zone_to_tilt_factor(s1)
        f1b = c._zone_to_tilt_factor(s1)  # same object -> cached, no second draw
        assert f1a == f1b
        s2 = _shift('tilted', 0.6)  # new object -> re-draw (consumes the 0.9)
        f2 = c._zone_to_tilt_factor(s2)
        assert f2 != f1a

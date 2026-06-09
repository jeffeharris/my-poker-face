"""Tilt-excursion persistence in PlayerPsychology.recover() (TILT_EXCURSION_DESIGN.md).

Flag-gated (TILT_PERSISTENCE_ENABLED): slow-recovery-while-tilted + second-wind
escape. Off => recover() is byte-identical to the old behaviour. Fit/validation of
the *parameters* lives in experiments/measure_zone_distribution.py; these tests pin
the *mechanism* and the inert-when-off guarantee.
"""

import os
import random
import statistics
from unittest import mock

from poker.player_psychology import (
    TILT_LINE,
    TILT_SECOND_WIND_K,
    PlayerPsychology,
)

FLAG = 'TILT_PERSISTENCE_ENABLED'

# A low-poise "hothead" — tilts readily, so a couple of bad beats cross the line.
_HOTHEAD = {
    'anchors': {
        'baseline_aggression': 0.6,
        'baseline_looseness': 0.4,
        'ego': 0.5,
        'poise': 0.30,
        'expressiveness': 0.6,
        'risk_identity': 0.6,
        'adaptation_bias': 0.5,
        'baseline_energy': 0.5,
        'recovery_rate': 0.15,
        'self_belief': 0.5,
    }
}


def _hothead() -> PlayerPsychology:
    random.seed(0)  # determinism if construction ever touches the RNG
    return PlayerPsychology.from_personality_config('Hot', _HOTHEAD)


def _drive_below_line(psy: PlayerPsychology, beats: int = 3) -> PlayerPsychology:
    for _ in range(beats):
        psy.apply_pressure_event('bad_beat')
    assert psy.axes.composure < TILT_LINE
    return psy


def test_flag_off_recovery_unchanged_and_no_new_state():
    """Off => composure recovery is the normal (faster) rate and no streak state."""
    with mock.patch.dict(os.environ, {FLAG: '0'}):
        psy = _drive_below_line(_hothead())
        pre = psy.axes.composure
        psy.recover()
        delta_off = psy.axes.composure - pre
        assert getattr(psy, '_tilt_streak', None) is None  # inert: no new attribute

    with mock.patch.dict(os.environ, {FLAG: '1'}):
        psy2 = _drive_below_line(_hothead())
        pre2 = psy2.axes.composure
        psy2.recover()
        delta_on = psy2.axes.composure - pre2

    assert abs(pre - pre2) < 1e-9  # identical starting point
    assert delta_off > delta_on > 0  # drag slows the climb-out while tilted


def test_second_wind_accelerates_after_K_hands():
    """Once stuck below the line for K hands, recovery jumps to the brisk escape."""
    with mock.patch.dict(os.environ, {FLAG: '1'}):
        psy = _hothead()
        for _ in range(6):  # drive deep so the slow drag keeps it below for >K hands
            psy.apply_pressure_event('bad_beat')
        deltas = []
        for _ in range(TILT_SECOND_WIND_K + 5):
            pre = psy.axes.composure
            psy.recover()
            deltas.append(psy.axes.composure - pre)

    early = deltas[:TILT_SECOND_WIND_K]  # dragged (slow)
    late = deltas[TILT_SECOND_WIND_K:]  # second wind has fired (brisk)
    assert max(late) > 2 * statistics.median(early)


def test_streak_resets_when_back_above_line():
    with mock.patch.dict(os.environ, {FLAG: '1'}):
        psy = _drive_below_line(_hothead(), beats=1)
        psy.recover()
        assert psy._tilt_streak >= 1  # accruing while tilted
        for _ in range(25):  # win back above the line
            psy.apply_pressure_event('big_win')
            psy.recover()
            if psy.axes.composure >= TILT_LINE:
                break
        psy.recover()
        assert psy.axes.composure >= TILT_LINE
        assert psy._tilt_streak == 0  # reset on climb-out

"""Tilt telegraph (TILT_EXCURSION_DESIGN.md §4): on entering a tilt episode, a
probabilistic Layer-3 trigger that hands the LLM the tilt state + a loose
suggestion (own words, not a fixed line) and forces a spoken beat. Flag-gated;
frequency-neutral. These pin `_compute_tilt_telegraph`."""

import os
from types import SimpleNamespace
from unittest import mock

from poker.tiered_bot_controller import TieredBotController

FLAG = 'TILT_TELEGRAPH_ENABLED'


class _RNG:
    """Deterministic stub: random() returns a fixed value."""

    def __init__(self, v: float):
        self._v = v

    def random(self) -> float:
        return self._v


def _controller(
    roll: float, pressure_source: str = 'bad_beat', nemesis=None
) -> TieredBotController:
    c = TieredBotController.__new__(TieredBotController)
    c.player_name = 'Hot'
    c.rng = _RNG(roll)
    c.psychology = SimpleNamespace(
        composure_state=SimpleNamespace(pressure_source=pressure_source, nemesis=nemesis)
    )
    c._was_tilted = False
    return c


_TILTED = SimpleNamespace(state='tilted')


def test_off_is_inert():
    """Inert-when-off is a guarantee about OUTPUT: no telegraph, no forced speech."""
    with mock.patch.dict(os.environ, {FLAG: '0'}):
        c = _controller(roll=0.0)
        assert c._compute_tilt_telegraph(_TILTED) == ''


def test_off_path_still_tracks_entry_edge():
    """The transition flag is tracked even when off, so flipping the flag on
    mid-episode is NOT mistaken for a fresh entry (no spurious one-time telegraph)."""
    c = _controller(roll=0.0)
    with mock.patch.dict(os.environ, {FLAG: '0'}):
        assert c._compute_tilt_telegraph(_TILTED) == ''  # off: no output
        assert c._was_tilted is True  # but the entry edge IS recorded
    with mock.patch.dict(os.environ, {FLAG: '1'}):
        # flag flipped on while STILL in the same tilt episode -> not a fresh entry
        assert c._compute_tilt_telegraph(_TILTED) == ''


def test_fires_on_entry_with_cause_and_no_stat_leak():
    with mock.patch.dict(os.environ, {FLAG: '1'}):
        c = _controller(roll=0.0, pressure_source='bad_beat')  # roll < prob -> fires
        out = c._compute_tilt_telegraph(_TILTED)
    assert 'rattled' in out.lower()
    assert 'bad beat' in out.lower()  # the cause is surfaced
    assert 'own words' in out.lower()  # varied-phrasing instruction (not a fixed line)
    assert not any(ch.isdigit() for ch in out)  # no raw stats/numbers


def test_fires_once_per_entry():
    with mock.patch.dict(os.environ, {FLAG: '1'}):
        c = _controller(roll=0.0)
        first = c._compute_tilt_telegraph(_TILTED)
        second = c._compute_tilt_telegraph(_TILTED)  # still tilted, not a fresh entry
    assert first != ''
    assert second == ''


def test_roll_can_miss():
    with mock.patch.dict(os.environ, {FLAG: '1'}):
        c = _controller(roll=0.99)  # roll >= prob -> miss
        assert c._compute_tilt_telegraph(_TILTED) == ''


def test_not_tilted_no_telegraph():
    with mock.patch.dict(os.environ, {FLAG: '1'}):
        c = _controller(roll=0.0)
        assert c._compute_tilt_telegraph(SimpleNamespace(state='composed')) == ''


def test_nemesis_is_filled():
    with mock.patch.dict(os.environ, {FLAG: '1'}):
        c = _controller(roll=0.0, pressure_source='nemesis_loss', nemesis='Alice')
        out = c._compute_tilt_telegraph(_TILTED)
    assert 'Alice' in out
    assert '{nemesis}' not in out

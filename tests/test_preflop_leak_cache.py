#!/usr/bin/env python3
"""Unit tests for the count-keyed preflop-leak report cache."""

from flask_app.services import preflop_leak_cache


def test_hit_then_count_invalidation():
    preflop_leak_cache.clear()
    calls = [0]

    def build():
        calls[0] += 1
        return {'v': calls[0]}

    a = preflop_leak_cache.get_or_compute(('o', 'all', 500), 10, build)
    b = preflop_leak_cache.get_or_compute(('o', 'all', 500), 10, build)  # same count → hit
    assert a is b
    assert calls[0] == 1
    c = preflop_leak_cache.get_or_compute(('o', 'all', 500), 11, build)  # count bumped → recompute
    assert calls[0] == 2
    assert c['v'] == 2


def test_keys_are_isolated():
    preflop_leak_cache.clear()
    calls = [0]

    def build():
        calls[0] += 1
        return {}

    preflop_leak_cache.get_or_compute(('o', 'deep', 500), 5, build)
    preflop_leak_cache.get_or_compute(('o', 'short', 500), 5, build)  # different depth
    preflop_leak_cache.get_or_compute(('o', 'all', 250), 5, build)  # different window
    assert calls[0] == 3


def test_clear():
    preflop_leak_cache.clear()
    calls = [0]

    def build():
        calls[0] += 1
        return {}

    preflop_leak_cache.get_or_compute(('o', 'all', 500), 1, build)
    preflop_leak_cache.clear()
    preflop_leak_cache.get_or_compute(('o', 'all', 500), 1, build)  # recompute after clear
    assert calls[0] == 2

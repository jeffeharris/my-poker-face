"""Lean casino fish lifecycle (CASINO_RESEED_ON_SPENT): fewer, leaner fish."""

from __future__ import annotations

import random

import pytest

from cash_mode import casino_provisioning as cp, economy_flags


@pytest.fixture(autouse=True)
def _reset():
    saved = economy_flags.CASINO_RESEED_ON_SPENT
    yield
    economy_flags.CASINO_RESEED_ON_SPENT = saved


def test_fish_cap_default():
    economy_flags.CASINO_RESEED_ON_SPENT = False
    assert cp._fish_cap('$2') == cp.CASINO_FISH_MAX
    assert cp._fish_cap('$50') == cp.CASINO_FISH_MAX


def test_fish_cap_lean_one_except_two_dollar():
    economy_flags.CASINO_RESEED_ON_SPENT = True
    assert cp._fish_cap('$2') == 2  # two at the entry stake
    assert cp._fish_cap('$10') == 1
    assert cp._fish_cap('$50') == 1


def test_prefund_mults_default():
    economy_flags.CASINO_RESEED_ON_SPENT = False
    assert cp._prefund_mults() == (cp.FISH_PREFUND_MIN_MULT, cp.FISH_PREFUND_MAX_MULT)
    assert cp._prefund_mults(whale=True) == (cp.WHALE_PREFUND_MIN_MULT, cp.WHALE_PREFUND_MAX_MULT)


def test_prefund_mults_lean():
    economy_flags.CASINO_RESEED_ON_SPENT = True
    assert cp._prefund_mults() == (cp.FISH_PREFUND_MIN_MULT_LEAN, cp.FISH_PREFUND_MAX_MULT_LEAN)
    assert cp._prefund_mults(whale=True) == (
        cp.WHALE_PREFUND_MIN_MULT_LEAN,
        cp.WHALE_PREFUND_MAX_MULT_LEAN,
    )


def test_fish_prefund_leaner_on_flag():
    rng = random.Random(0)
    economy_flags.CASINO_RESEED_ON_SPENT = False
    fat = cp._fish_prefund(5_000, random.Random(0))  # $50 max-buy-in
    economy_flags.CASINO_RESEED_ON_SPENT = True
    lean = cp._fish_prefund(5_000, random.Random(0))
    assert lean < fat  # 1.5-2.0x vs 2.5-3.6x
    # leanest possible (1.5x) still positive
    assert cp._fish_prefund(5_000, rng) > 0


def test_whale_prefund_leaner_on_flag():
    economy_flags.CASINO_RESEED_ON_SPENT = False
    fat = cp._fish_prefund(20_000, random.Random(1), whale=True)  # 10-18x = 200-360k
    economy_flags.CASINO_RESEED_ON_SPENT = True
    lean = cp._fish_prefund(20_000, random.Random(1), whale=True)  # 2.0-2.5x = 40-50k
    assert lean < fat
    assert lean <= 20_000 * cp.WHALE_PREFUND_MAX_MULT_LEAN

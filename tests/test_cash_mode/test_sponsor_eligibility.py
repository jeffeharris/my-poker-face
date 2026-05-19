"""Tests for the sponsor-eligibility gate in cash_routes.

The eligibility rule (per design discussion):
  sponsor-eligible at stake N iff
    bankroll < stake N's min_buy_in
    AND (N is the lowest stake OR bankroll >= stake N-1's min_buy_in)

This closes two exploits / shapes UX:
  - Blocks the "preserve capital" play: you can't sponsor at a stake
    you could already self-afford (would be strictly +EV vs self-fund).
  - Forces step-by-step climbing: at $200 bankroll you can only
    sponsor at $10 (next tier up), not jump to $1000.
"""

from __future__ import annotations

import pytest

from cash_mode.stakes_ladder import is_sponsor_eligible as _is_sponsor_eligible

# Buy-in mins per stake (40 BB × big_blind):
#   $2    → 80
#   $10   → 400
#   $50   → 2_000
#   $200  → 8_000
#   $1000 → 40_000


class TestSelfAffordableBlocked:
    """If the player can already self-afford the stake, no sponsor."""

    def test_blocks_when_bankroll_above_min(self):
        # 500 chips at $10 (min 400) → self-affordable → no sponsor.
        assert _is_sponsor_eligible(500, "$10") is False

    def test_blocks_when_bankroll_exactly_min(self):
        # Boundary: bankroll == min_buy_in → self-affordable → no sponsor.
        assert _is_sponsor_eligible(400, "$10") is False

    def test_blocks_high_bankroll_at_low_stake(self):
        # 5000 chips at $2 → way above min, no sponsor.
        assert _is_sponsor_eligible(5_000, "$2") is False


class TestOneTierUp:
    """Player can sponsor exactly one tier above what they self-afford."""

    def test_200_chips_eligible_at_10_dollar(self):
        # 200 chips: self-affords $2 (min 80). $10 is one tier up
        # (200 < $10's min 400 AND 200 >= $2's min 80). Eligible.
        assert _is_sponsor_eligible(200, "$10") is True

    def test_500_chips_eligible_at_50_dollar(self):
        # 500 chips: self-affords $10 (min 400). $50 is one tier up
        # (500 < 2000 AND 500 >= 400). Eligible.
        assert _is_sponsor_eligible(500, "$50") is True

    def test_3000_chips_eligible_at_200_dollar(self):
        # 3000 chips: self-affords $50 (min 2000). $200 is one tier up.
        assert _is_sponsor_eligible(3_000, "$200") is True

    def test_10000_chips_eligible_at_1000_dollar(self):
        # 10000 chips: self-affords $200 (min 8000). $1000 is one tier up.
        assert _is_sponsor_eligible(10_000, "$1000") is True


class TestSkipTierBlocked:
    """Can't sponsor more than one tier above self-affordable."""

    def test_200_chips_blocked_at_50_dollar(self):
        # 200 chips: self-affords $2 (min 80). $50 is TWO tiers up
        # (200 < $50's min 2000 BUT 200 < $10's min 400). Blocked.
        assert _is_sponsor_eligible(200, "$50") is False

    def test_200_chips_blocked_at_1000_dollar(self):
        # 200 chips: trying to skip from $2 to $1000. Blocked hard.
        assert _is_sponsor_eligible(200, "$1000") is False

    def test_500_chips_blocked_at_200_dollar(self):
        # 500 chips: self-affords $10. $200 is two tiers up. Blocked.
        assert _is_sponsor_eligible(500, "$200") is False


class TestLowestTierEdgeCase:
    """At the lowest tier ($2), there's no 'tier below' check."""

    def test_50_chips_eligible_at_2_dollar(self):
        # 50 chips < $2's min 80 → eligible (lowest tier, no prev check).
        assert _is_sponsor_eligible(50, "$2") is True

    def test_zero_chips_eligible_at_2_dollar(self):
        # 0 chips → still eligible at the lowest stake. This is the
        # entry-point for a fully-busted player to keep playing.
        assert _is_sponsor_eligible(0, "$2") is True

    def test_zero_chips_blocked_above_lowest(self):
        # 0 chips at $10 → BLOCKED. They have to take a $2 sponsor
        # first, climb their way up.
        assert _is_sponsor_eligible(0, "$10") is False


class TestInvalidInput:
    def test_returns_false_for_unknown_stake(self):
        assert _is_sponsor_eligible(1_000, "$99999") is False

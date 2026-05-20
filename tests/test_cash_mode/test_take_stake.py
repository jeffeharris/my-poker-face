"""Phase 4 Commit 2: AI-borrow movement decision tests.

Two surfaces under test:

1. `find_ai_staker_for` — pure picker that filters candidates by
   lender willingness, capacity, and relationship axes, then returns
   one at random (or None if no candidate qualifies).

2. `refresh_table_roster` `take_stake` interception — when a peer AI
   is willing and able, an AI's `forced_leave` decision is overridden
   to `take_stake`. The borrower's seat refills to principal, the
   pre-bust chips return to bankroll via a `from_seat` BankrollChange,
   and a `StakeCreationChange` is emitted carrying the deal terms.
"""

from __future__ import annotations

import os
import random
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from cash_mode.lender_profile import (
    BORROWER_PROFILE_DEFAULTS,
    BorrowerProfile,
    LENDER_PROFILE_DEFAULTS,
    LenderProfile,
)
from cash_mode.movement import (
    StakeCreationChange,
    find_ai_staker_for,
    refresh_table_roster,
)
from cash_mode.tables import CashTableState, ai_slot, open_slot


ANCHOR = datetime(2026, 5, 20, 12, 0, 0)


def _willing_lender(
    *, max_pct=0.5, respect_floor=-0.5, heat_ceiling=0.7,
) -> LenderProfile:
    return LenderProfile(
        willing=True,
        max_loan_pct_of_bankroll=max_pct,
        floor_anchor=1.20,
        rate_anchor=0.30,
        respect_floor=respect_floor,
        heat_ceiling=heat_ceiling,
    )


class TestFindAIStakerFor(unittest.TestCase):
    """Pure picker — no I/O, all inputs as callbacks."""

    def _profile_lookup(self, profiles):
        def _lookup(pid):
            return profiles.get(pid, LENDER_PROFILE_DEFAULTS)
        return _lookup

    def _bankroll_lookup(self, bankrolls):
        def _lookup(pid):
            return bankrolls.get(pid)
        return _lookup

    def _rel_lookup(self, rels):
        def _lookup(observer, opponent):
            return rels.get((observer, opponent))
        return _lookup

    def test_returns_none_when_no_candidates(self):
        match = find_ai_staker_for(
            borrower_id="bust_ai",
            principal=80,
            candidate_pids=[],
            lender_profile_lookup=self._profile_lookup({}),
            bankroll_lookup=self._bankroll_lookup({}),
            relationship_lookup=self._rel_lookup({}),
            rng=random.Random(1),
        )
        self.assertIsNone(match)

    def test_returns_none_when_all_unwilling(self):
        unwilling = LenderProfile(
            willing=False, max_loan_pct_of_bankroll=0.5,
            floor_anchor=1.0, rate_anchor=0.3,
            respect_floor=-1.0, heat_ceiling=1.0,
        )
        match = find_ai_staker_for(
            borrower_id="bust_ai",
            principal=80,
            candidate_pids=["napoleon"],
            lender_profile_lookup=self._profile_lookup({"napoleon": unwilling}),
            bankroll_lookup=self._bankroll_lookup({"napoleon": 5_000}),
            relationship_lookup=self._rel_lookup({}),
            rng=random.Random(1),
        )
        self.assertIsNone(match)

    def test_returns_match_when_bankroll_covers_principal(self):
        # Napoleon has 5,000 bankroll, max_loan_pct=0.5 → capacity 2,500.
        # Principal 80 fits easily.
        match = find_ai_staker_for(
            borrower_id="bust_ai",
            principal=80,
            candidate_pids=["napoleon"],
            lender_profile_lookup=self._profile_lookup({
                "napoleon": _willing_lender(),
            }),
            bankroll_lookup=self._bankroll_lookup({"napoleon": 5_000}),
            relationship_lookup=self._rel_lookup({}),
            rng=random.Random(1),
        )
        self.assertIsNotNone(match)
        staker_id, profile = match
        self.assertEqual(staker_id, "napoleon")
        self.assertTrue(profile.willing)

    def test_filters_by_max_loan_pct_capacity(self):
        # Tiny bankroll relative to principal — fails the capacity gate.
        match = find_ai_staker_for(
            borrower_id="bust_ai",
            principal=2_000,
            candidate_pids=["broke_ai"],
            lender_profile_lookup=self._profile_lookup({
                "broke_ai": _willing_lender(max_pct=0.05),
            }),
            # 1,000 × 0.05 = 50 < 2,000.
            bankroll_lookup=self._bankroll_lookup({"broke_ai": 1_000}),
            relationship_lookup=self._rel_lookup({}),
            rng=random.Random(1),
        )
        self.assertIsNone(match)

    def test_respects_respect_floor(self):
        # Lender's respect for borrower is -0.8, below floor -0.5.
        match = find_ai_staker_for(
            borrower_id="bust_ai",
            principal=80,
            candidate_pids=["napoleon"],
            lender_profile_lookup=self._profile_lookup({
                "napoleon": _willing_lender(respect_floor=-0.5),
            }),
            bankroll_lookup=self._bankroll_lookup({"napoleon": 5_000}),
            relationship_lookup=self._rel_lookup({
                ("napoleon", "bust_ai"): (0.5, -0.8, 0.0),
            }),
            rng=random.Random(1),
        )
        self.assertIsNone(match)

    def test_respects_heat_ceiling(self):
        match = find_ai_staker_for(
            borrower_id="bust_ai",
            principal=80,
            candidate_pids=["napoleon"],
            lender_profile_lookup=self._profile_lookup({
                "napoleon": _willing_lender(heat_ceiling=0.6),
            }),
            bankroll_lookup=self._bankroll_lookup({"napoleon": 5_000}),
            relationship_lookup=self._rel_lookup({
                ("napoleon", "bust_ai"): (0.5, 0.5, 0.9),
            }),
            rng=random.Random(1),
        )
        self.assertIsNone(match)

    def test_excludes_borrower_from_candidates(self):
        # Even if borrower's own id appears in candidates, never matches itself.
        match = find_ai_staker_for(
            borrower_id="bust_ai",
            principal=80,
            candidate_pids=["bust_ai"],
            lender_profile_lookup=self._profile_lookup({
                "bust_ai": _willing_lender(),
            }),
            bankroll_lookup=self._bankroll_lookup({"bust_ai": 5_000}),
            relationship_lookup=self._rel_lookup({}),
            rng=random.Random(1),
        )
        self.assertIsNone(match)


class TestTakeStakeInRefreshRoster(unittest.TestCase):
    """Integration of `take_stake` into refresh_table_roster."""

    def _make_table(self, busting_chips: int = 10) -> CashTableState:
        # Two AI seats: busting AI + a peer staker.
        return CashTableState(
            table_id="test-table",
            stake_label="$2",
            seats=[
                ai_slot("bust_ai", busting_chips),  # below 0.3 × 80 = 24
                ai_slot("napoleon", 100),
                open_slot(),
                open_slot(),
                open_slot(),
                open_slot(),
            ],
        )

    def _common_kwargs(self, **overrides):
        defaults = {
            "idle_pool": [],
            "eligible_candidates": [],
            "seated_globally": set(),
            "bankroll_lookup": lambda pid: 5_000,
            "buy_in_lookup": lambda pid: 80,
            "rng": random.Random(1),
            "now": ANCHOR,
            "stake_idx": 0,
            "table_min_buy_in": 80,
            "table_max_buy_in": 200,
            "next_tier_min_buy_in": 400,
        }
        defaults.update(overrides)
        return defaults

    def test_without_callbacks_forced_leave_unchanged(self):
        # Pre-Phase-4 callers (no callbacks) → bust AI still leaves.
        table = self._make_table()
        result = refresh_table_roster(table, **self._common_kwargs())
        self.assertEqual(result.decisions.get("bust_ai"), "forced_leave")
        self.assertEqual(len(result.stake_creations), 0)

    def test_with_unwilling_borrower_falls_back_to_forced_leave(self):
        table = self._make_table()
        unwilling = BorrowerProfile(willing=False)
        result = refresh_table_roster(
            table,
            **self._common_kwargs(
                borrower_profile_lookup=lambda pid: unwilling,
                lender_profile_lookup=lambda pid: _willing_lender(),
                relationship_lookup=lambda o, p: None,
                stake_label="$2",
            ),
        )
        self.assertEqual(result.decisions.get("bust_ai"), "forced_leave")
        self.assertEqual(len(result.stake_creations), 0)

    def test_with_no_willing_staker_falls_back_to_forced_leave(self):
        # Borrower willing; peer (napoleon) is unwilling.
        table = self._make_table()
        unwilling_lender = LenderProfile(
            willing=False, max_loan_pct_of_bankroll=0.5,
            floor_anchor=1.0, rate_anchor=0.3,
            respect_floor=-1.0, heat_ceiling=1.0,
        )
        result = refresh_table_roster(
            table,
            **self._common_kwargs(
                borrower_profile_lookup=lambda pid: BORROWER_PROFILE_DEFAULTS,
                lender_profile_lookup=lambda pid: unwilling_lender,
                relationship_lookup=lambda o, p: None,
                stake_label="$2",
            ),
        )
        self.assertEqual(result.decisions.get("bust_ai"), "forced_leave")
        self.assertEqual(len(result.stake_creations), 0)

    def test_take_stake_fires_when_peer_willing(self):
        table = self._make_table(busting_chips=10)
        result = refresh_table_roster(
            table,
            **self._common_kwargs(
                borrower_profile_lookup=lambda pid: BORROWER_PROFILE_DEFAULTS,
                lender_profile_lookup=lambda pid: _willing_lender(),
                relationship_lookup=lambda o, p: None,
                stake_label="$2",
            ),
        )
        self.assertEqual(result.decisions.get("bust_ai"), "take_stake")
        self.assertEqual(len(result.stake_creations), 1)
        sc = result.stake_creations[0]
        self.assertEqual(sc.borrower_id, "bust_ai")
        self.assertEqual(sc.staker_id, "napoleon")
        self.assertEqual(sc.principal, 80)
        self.assertEqual(sc.stake_label, "$2")
        self.assertEqual(sc.cut, 0.30)

    def test_take_stake_refills_seat_to_principal(self):
        table = self._make_table(busting_chips=10)
        result = refresh_table_roster(
            table,
            **self._common_kwargs(
                borrower_profile_lookup=lambda pid: BORROWER_PROFILE_DEFAULTS,
                lender_profile_lookup=lambda pid: _willing_lender(),
                relationship_lookup=lambda o, p: None,
                stake_label="$2",
            ),
        )
        # Seat 0 (bust_ai) refilled to min_buy_in (80).
        seat = result.new_table.seats[0]
        self.assertEqual(seat["kind"], "ai")
        self.assertEqual(seat["personality_id"], "bust_ai")
        self.assertEqual(seat["chips"], 80)

    def test_take_stake_emits_from_seat_for_busting_chips(self):
        # Borrower's pre-bust chips return to their bankroll via the
        # normal from_seat path. Without this, the chip-conservation
        # math breaks (the seat refill adds principal, but the original
        # chips never went anywhere).
        table = self._make_table(busting_chips=15)
        result = refresh_table_roster(
            table,
            **self._common_kwargs(
                borrower_profile_lookup=lambda pid: BORROWER_PROFILE_DEFAULTS,
                lender_profile_lookup=lambda pid: _willing_lender(),
                relationship_lookup=lambda o, p: None,
                stake_label="$2",
            ),
        )
        from_seat = [
            bc for bc in result.bankroll_changes
            if bc.direction == "from_seat" and bc.personality_id == "bust_ai"
        ]
        self.assertEqual(len(from_seat), 1)
        self.assertEqual(from_seat[0].amount, 15)

    def test_take_stake_with_zero_chips_emits_no_from_seat(self):
        # Bust AI at 0 chips — no from_seat (nothing to return). The
        # seat still refills to principal.
        table = self._make_table(busting_chips=0)
        result = refresh_table_roster(
            table,
            **self._common_kwargs(
                borrower_profile_lookup=lambda pid: BORROWER_PROFILE_DEFAULTS,
                lender_profile_lookup=lambda pid: _willing_lender(),
                relationship_lookup=lambda o, p: None,
                stake_label="$2",
            ),
        )
        self.assertEqual(result.decisions.get("bust_ai"), "take_stake")
        from_seat = [
            bc for bc in result.bankroll_changes
            if bc.direction == "from_seat" and bc.personality_id == "bust_ai"
        ]
        self.assertEqual(len(from_seat), 0)
        # Seat still refilled to principal.
        self.assertEqual(result.new_table.seats[0]["chips"], 80)


if __name__ == '__main__':
    unittest.main()

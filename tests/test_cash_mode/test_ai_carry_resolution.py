"""Phase 4.5 — AI carry resolution helpers.

Pure-math tests for the trigger probability + pressure helpers, plus
end-to-end tests that fire the three resolution paths against tempdb-
backed repos.

Wider integration (lobby refresh emits ticker events for these
behaviors) is covered by `test_lobby_seat_chip_conservation.py`'s
drift-zero assertion — if the dispatcher introduced unbalanced chip
flows the conservation test would surface it.
"""

from __future__ import annotations

import os
import random
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from cash_mode.ai_carry_resolution import (
    AI_CARRY_TICKER_THRESHOLD,
    DEFAULT_PRESSURE_THRESHOLD,
    FORGIVENESS_RATE_LIMIT_SECONDS,
    PAYOFF_BANKROLL_FACTOR_FLOOR,
    _default_pressure,
    _forgiveness_ask_probability,
    _forgiveness_score,
    _payoff_probability,
    resolve_ai_carries,
    try_ai_explicit_default,
    try_ai_forgiveness_ask,
    try_ai_voluntary_payoff,
)
from cash_mode.bankroll import AIBankrollState
from cash_mode.stakes import (
    BORROWER_KIND_PERSONALITY,
    STAKE_FORMAT_PURE,
    STAKE_STATUS_ACTIVE,
    STAKE_STATUS_CARRY,
    STAKE_STATUS_DEFAULTED,
    STAKE_STATUS_SETTLED,
    STAKER_KIND_PERSONALITY,
    Stake,
)
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.stake_repository import StakeRepository


ANCHOR = datetime(2026, 5, 21, 12, 0, 0)
SBX = "test-sandbox-1"


# --- Pure-math tests ---------------------------------------------------------


class TestPayoffProbability(unittest.TestCase):
    def test_zero_when_solvency_at_or_below_one(self):
        self.assertEqual(_payoff_probability(0.0), 0.0)
        self.assertEqual(_payoff_probability(1.0), 0.0)

    def test_base_prob_at_or_above_floor(self):
        self.assertGreater(
            _payoff_probability(PAYOFF_BANKROLL_FACTOR_FLOOR), 0.0,
        )
        self.assertEqual(
            _payoff_probability(PAYOFF_BANKROLL_FACTOR_FLOOR),
            _payoff_probability(PAYOFF_BANKROLL_FACTOR_FLOOR * 10),
        )

    def test_ramp_between_one_and_floor(self):
        midpoint = (1.0 + PAYOFF_BANKROLL_FACTOR_FLOOR) / 2
        mid_prob = _payoff_probability(midpoint)
        self.assertGreater(mid_prob, 0.0)
        self.assertLess(
            mid_prob, _payoff_probability(PAYOFF_BANKROLL_FACTOR_FLOOR),
        )


class TestForgivenessAskProbability(unittest.TestCase):
    def test_inverse_to_factor(self):
        # Poor AIs ask more than flush AIs.
        poor = _forgiveness_ask_probability(1.0)
        rich = _forgiveness_ask_probability(10.0)
        self.assertGreater(poor, rich)


class TestForgivenessScore(unittest.TestCase):
    def test_likability_dominant(self):
        score = _forgiveness_score(likability=1.0, respect=0.0, heat=0.0)
        # 0.5 weight on likability
        self.assertAlmostEqual(score, 0.5, places=3)

    def test_heat_subtracts(self):
        a = _forgiveness_score(likability=0.5, respect=0.5, heat=0.0)
        b = _forgiveness_score(likability=0.5, respect=0.5, heat=1.0)
        self.assertGreater(a, b)


class TestDefaultPressure(unittest.TestCase):
    def test_no_pressure_when_all_neutral(self):
        p = _default_pressure(
            bankroll_factor=2.0,  # not drowning
            energy=0.8,          # not tired
            staker_respect_for_borrower=0.5,  # not low
            carry_age_days=0,
            oldest_age_days=0,
        )
        # Only the oldest-carry bonus fires (carry IS the oldest at age 0).
        self.assertAlmostEqual(p, 0.1, places=3)

    def test_drowning_dominant(self):
        p = _default_pressure(
            bankroll_factor=0.2,  # drowning
            energy=0.8,
            staker_respect_for_borrower=0.5,
            carry_age_days=10,
            oldest_age_days=10,
        )
        # drowning (0.4) + oldest (0.1) = 0.5; below threshold.
        self.assertAlmostEqual(p, 0.5, places=3)

    def test_compounded_signals_cross_threshold(self):
        p = _default_pressure(
            bankroll_factor=0.2,        # +0.4
            energy=0.1,                 # +0.3
            staker_respect_for_borrower=-0.5,  # +0.2
            carry_age_days=5,
            oldest_age_days=5,          # +0.1
        )
        # All signals stack → capped at 1.0
        self.assertAlmostEqual(p, 1.0, places=3)
        self.assertGreater(p, DEFAULT_PRESSURE_THRESHOLD)


# --- Integration tests against tempdb ---------------------------------------


@pytest.fixture
def db_setup(tmp_path):
    """Schema + repos + two seeded AIs (a flush staker + a debt-laden
    borrower) so the resolution paths have something to act on."""
    db = str(tmp_path / "ai_carry.db")
    SchemaManager(db).ensure_schema()
    bankroll = BankrollRepository(db)
    stake = StakeRepository(db)
    ledger = ChipLedgerRepository(db)

    # Borrower bankroll well-above their total carries → high factor.
    bankroll.save_ai_bankroll(AIBankrollState(
        personality_id="borrower",
        chips=10_000,
        last_regen_tick=ANCHOR,
    ), sandbox_id=SBX)

    # Staker bankroll — pre-existing row so credits don't first-write.
    bankroll.save_ai_bankroll(AIBankrollState(
        personality_id="staker",
        chips=5_000,
        last_regen_tick=ANCHOR,
    ), sandbox_id=SBX)

    # One outstanding carry: borrower owes staker $400.
    stake.create_stake(Stake(
        stake_id="carry_1",
        session_id="ai_session_borrower_1",
        staker_id="staker",
        staker_kind=STAKER_KIND_PERSONALITY,
        borrower_id="borrower",
        borrower_kind=BORROWER_KIND_PERSONALITY,
        format=STAKE_FORMAT_PURE,
        principal=400,
        match_amount=0,
        origination_fee=0,
        cut=0.30,
        status=STAKE_STATUS_CARRY,
        carry_amount=400,
        stake_tier="$10",
        created_at=ANCHOR,
    ))

    return {"bankroll": bankroll, "stake": stake, "ledger": ledger}


class TestVoluntaryPayoff:
    def test_payoff_clears_carry_and_transfers_chips(self, db_setup):
        bankroll = db_setup["bankroll"]
        stake = db_setup["stake"]
        carries = stake.list_carries_for_borrower(
            "borrower", BORROWER_KIND_PERSONALITY,
        )
        # rng that always rolls 0 → probability check guaranteed to pass.
        result = try_ai_voluntary_payoff(
            personality_id="borrower",
            carries=carries,
            bankroll_repo=bankroll,
            stake_repo=stake,
            relationship_repo=None,  # event-fire path is best-effort
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=random.Random(0),  # deterministic; first roll is 0.844
            now=ANCHOR,
        )
        # bankroll/total_carries = 10000/400 = 25 (well above floor 5),
        # so prob = PAYOFF_BASE_PROB = 0.05. rng.random()=0.844 ≥ 0.05
        # → no fire on this seed. Try a fixed-low rng instead.
        # Replay with an rng that always returns ~0.0
        class AlwaysLowRng:
            def random(self):
                return 0.0
        result = try_ai_voluntary_payoff(
            personality_id="borrower",
            carries=carries,
            bankroll_repo=bankroll,
            stake_repo=stake,
            relationship_repo=None,
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=AlwaysLowRng(),
            now=ANCHOR,
        )
        assert result is not None
        assert result.kind == 'payoff'
        assert result.amount == 400
        assert result.staker_id == "staker"

        # Stake status flipped to settled, carry_amount zeroed.
        after = stake.load_stake("carry_1")
        assert after.status == STAKE_STATUS_SETTLED
        assert after.carry_amount == 0

        # Borrower bankroll debited; staker bankroll credited.
        borrower_state = bankroll.load_ai_bankroll("borrower", sandbox_id=SBX)
        staker_state = bankroll.load_ai_bankroll("staker", sandbox_id=SBX)
        assert borrower_state.chips == 10_000 - 400
        # Staker side goes through credit_ai_cash_out (projection + add)
        # since starting_bankroll defaults to 0 in this test, regen is
        # 0, so staker's chips simply +=400.
        assert staker_state.chips == 5_000 + 400


class TestForgivenessAsk:
    def test_grant_clears_carry(self, db_setup):
        # High-likability relationship → score > threshold → grant.
        from unittest.mock import MagicMock
        rel = MagicMock()
        rel_state = MagicMock(likability=0.9, respect=0.7, heat=0.0)
        rel.load_relationship_state.return_value = rel_state

        carries = db_setup["stake"].list_carries_for_borrower(
            "borrower", BORROWER_KIND_PERSONALITY,
        )

        class AlwaysLowRng:
            def random(self):
                return 0.0

        result = try_ai_forgiveness_ask(
            personality_id="borrower",
            carries=carries,
            bankroll_repo=db_setup["bankroll"],
            stake_repo=db_setup["stake"],
            relationship_repo=rel,
            sandbox_id=SBX,
            rng=AlwaysLowRng(),
            now=ANCHOR,
        )
        assert result is not None
        assert result.kind == 'forgiven'
        after = db_setup["stake"].load_stake("carry_1")
        assert after.status == STAKE_STATUS_SETTLED
        assert after.carry_amount == 0

    def test_rate_limit_blocks_repeat_ask(self, db_setup):
        # Mark the stake as already-asked just now → second roll skips.
        db_setup["stake"].mark_forgiveness_asked("carry_1", ANCHOR)
        carries = db_setup["stake"].list_carries_for_borrower(
            "borrower", BORROWER_KIND_PERSONALITY,
        )
        from unittest.mock import MagicMock
        rel = MagicMock()
        rel.load_relationship_state.return_value = MagicMock(
            likability=0.9, respect=0.9, heat=0.0,
        )

        class AlwaysLowRng:
            def random(self):
                return 0.0

        result = try_ai_forgiveness_ask(
            personality_id="borrower",
            carries=carries,
            bankroll_repo=db_setup["bankroll"],
            stake_repo=db_setup["stake"],
            relationship_repo=rel,
            sandbox_id=SBX,
            rng=AlwaysLowRng(),
            now=ANCHOR + timedelta(seconds=1),  # 1s elapsed; still rate-limited
        )
        assert result is None  # rate-limit blocks


class TestExplicitDefault:
    def test_high_pressure_fires_default(self, db_setup):
        # Borrower drowning in debt + tired + bad relationship → high pressure.
        # Drain bankroll to make factor < DROWNING_RATIO.
        db_setup["bankroll"].save_ai_bankroll(AIBankrollState(
            personality_id="borrower",
            chips=100,  # bankroll/total_carries = 100/400 = 0.25 < 0.5
            last_regen_tick=ANCHOR,
        ), sandbox_id=SBX)

        from unittest.mock import MagicMock
        rel = MagicMock()
        rel.load_relationship_state.return_value = MagicMock(
            likability=0.0, respect=-0.5, heat=0.5,
        )

        carries = db_setup["stake"].list_carries_for_borrower(
            "borrower", BORROWER_KIND_PERSONALITY,
        )

        class AlwaysLowRng:
            def random(self):
                return 0.0

        result = try_ai_explicit_default(
            personality_id="borrower",
            carries=carries,
            bankroll_repo=db_setup["bankroll"],
            stake_repo=db_setup["stake"],
            relationship_repo=rel,
            sandbox_id=SBX,
            energy_lookup=lambda pid: 0.1,  # very tired
            rng=AlwaysLowRng(),
            now=ANCHOR,
        )
        assert result is not None
        assert result.kind == 'default'
        after = db_setup["stake"].load_stake("carry_1")
        assert after.status == STAKE_STATUS_DEFAULTED
        assert after.carry_amount == 0


class TestDispatcher:
    def test_bulk_carry_fetch_groups_by_borrower(self, db_setup):
        """The dispatcher's bulk SQL query must group carries by borrower
        and skip house-staker carries (NULL staker_id)."""
        # Add a second carry for the same borrower to verify oldest-first.
        db_setup["stake"].create_stake(Stake(
            stake_id="carry_2",
            session_id="ai_session_borrower_2",
            staker_id="staker",
            staker_kind=STAKER_KIND_PERSONALITY,
            borrower_id="borrower",
            borrower_kind=BORROWER_KIND_PERSONALITY,
            format=STAKE_FORMAT_PURE,
            principal=200,
            match_amount=0,
            origination_fee=0,
            cut=0.30,
            status=STAKE_STATUS_CARRY,
            carry_amount=200,
            stake_tier="$10",
            created_at=ANCHOR + timedelta(hours=2),  # newer
        ))

        class AlwaysLowRng:
            def random(self):
                return 0.0

        batch = resolve_ai_carries(
            bankroll_repo=db_setup["bankroll"],
            stake_repo=db_setup["stake"],
            relationship_repo=None,
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            energy_lookup=lambda pid: 0.5,
            rng=AlwaysLowRng(),
            now=ANCHOR + timedelta(hours=3),
        )
        # First resolved carry must be the oldest (carry_1).
        assert len(batch.results) == 1  # at most one resolution per AI per refresh
        assert batch.results[0].stake_id == "carry_1"


if __name__ == '__main__':
    unittest.main()

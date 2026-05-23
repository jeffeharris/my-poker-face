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
    PAYOFF_EVENT_BASE_RATE,
    PAYOFF_MIN_EAGERNESS_FRACTION,
    PAYOFF_MIN_PAYMENT,
    _affordability_gate,
    _carry_age_factor,
    _default_pressure,
    _forgiveness_ask_probability,
    _forgiveness_score,
    _hold_pull,
    _pay_pull,
    _payoff_payment_amount,
    _payoff_probability,
    _payoff_score,
    _wealth_gap_factor,
    resolve_ai_carries,
    try_ai_explicit_default,
    try_ai_forgiveness_ask,
    try_ai_voluntary_payoff,
)
from cash_mode.bankroll import AIBankrollState
from cash_mode.bankroll import PlayerBankrollState
from cash_mode.stakes import (
    BORROWER_KIND_PERSONALITY,
    STAKE_FORMAT_PURE,
    STAKE_STATUS_ACTIVE,
    STAKE_STATUS_CARRY,
    STAKE_STATUS_DEFAULTED,
    STAKE_STATUS_SETTLED,
    STAKER_KIND_HUMAN,
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


class TestCarryAgeFactor(unittest.TestCase):
    """Linear ramp from 0 at fresh to 1.0 at PAYOFF_AGE_RAMP_DAYS."""

    def _make_stake(self, created_at):
        return Stake(
            stake_id="x", session_id="s", staker_id="a",
            staker_kind=STAKER_KIND_PERSONALITY,
            borrower_id="b", borrower_kind=BORROWER_KIND_PERSONALITY,
            format=STAKE_FORMAT_PURE, principal=100,
            match_amount=0, origination_fee=0, cut=0.3,
            status=STAKE_STATUS_CARRY, carry_amount=100,
            stake_tier="$10", created_at=created_at,
        )

    def test_zero_at_fresh(self):
        s = self._make_stake(ANCHOR)
        self.assertAlmostEqual(_carry_age_factor(s, ANCHOR), 0.0)

    def test_half_at_seven_days(self):
        s = self._make_stake(ANCHOR)
        self.assertAlmostEqual(
            _carry_age_factor(s, ANCHOR + timedelta(days=7)), 0.5, places=2,
        )

    def test_saturates_at_ramp(self):
        s = self._make_stake(ANCHOR)
        self.assertAlmostEqual(
            _carry_age_factor(s, ANCHOR + timedelta(days=14)), 1.0,
        )
        self.assertAlmostEqual(
            _carry_age_factor(s, ANCHOR + timedelta(days=365)), 1.0,
        )


class TestWealthGapFactor(unittest.TestCase):
    def test_zero_below_target(self):
        self.assertEqual(_wealth_gap_factor(100, 200), 0.0)

    def test_zero_at_exactly_target(self):
        self.assertEqual(_wealth_gap_factor(200, 200), 0.0)

    def test_saturates_at_five_times_target(self):
        self.assertAlmostEqual(_wealth_gap_factor(1000, 200), 1.0)
        self.assertAlmostEqual(_wealth_gap_factor(10_000, 200), 1.0)

    def test_zero_when_target_unknown(self):
        self.assertEqual(_wealth_gap_factor(1000, 0), 0.0)


class TestPayoffScore(unittest.TestCase):
    """Composite score under archetype scenarios."""

    def test_conscientious_with_urgent_debt_pays(self):
        score = _payoff_score(
            payoff_eagerness=0.9,
            pay_pull=0.9,   # old high-heat carry
            hold_pull=0.5,  # mid aspiration + flush wealth
        )
        self.assertGreater(score, 0.6)

    def test_gambler_with_urgent_debt_holds(self):
        score = _payoff_score(
            payoff_eagerness=0.1,
            pay_pull=0.9,
            hold_pull=0.5,
        )
        # eagerness × pay_pull = 0.09; (1−eagerness) × hold_pull = 0.45
        # → score collapses to 0 (clamped from -0.36).
        self.assertEqual(score, 0.0)

    def test_baseline_balanced_pulls_zero(self):
        score = _payoff_score(
            payoff_eagerness=0.5,
            pay_pull=0.5,
            hold_pull=0.5,
        )
        # 0.5×0.5 − 0.5×0.5 = 0
        self.assertEqual(score, 0.0)

    def test_no_hold_pull_pure_eagerness(self):
        # When there's no climb attraction (no surplus or no aspiration),
        # score is just eagerness × pay_pull.
        score = _payoff_score(
            payoff_eagerness=0.4,
            pay_pull=0.6,
            hold_pull=0.0,
        )
        self.assertAlmostEqual(score, 0.24)


class TestAffordabilityGate(unittest.TestCase):
    def test_blocks_when_headroom_below_min_payment(self):
        # The gate now allows partial payments: it blocks only when
        # bankroll−floor is below PAYOFF_MIN_PAYMENT ($50). Tiny
        # headroom (under $50) would just produce noise payments.
        from cash_mode.ai_carry_resolution import (
            PAYOFF_MIN_PAYMENT,
            _min_tier_buy_in_buffer,
        )
        floor = _min_tier_buy_in_buffer()
        self.assertFalse(
            _affordability_gate(floor + PAYOFF_MIN_PAYMENT - 1, 1000)
        )

    def test_allows_when_headroom_at_or_above_min_payment(self):
        from cash_mode.ai_carry_resolution import (
            PAYOFF_MIN_PAYMENT,
            _min_tier_buy_in_buffer,
        )
        floor = _min_tier_buy_in_buffer()
        # Exactly at the minimum — gate allows; partial payment of
        # $50 will fire.
        self.assertTrue(_affordability_gate(floor + PAYOFF_MIN_PAYMENT, 1000))
        self.assertTrue(_affordability_gate(floor + 10_000, 1000))

    def test_blocks_when_carry_is_zero(self):
        # Defensive: a zero carry can't fire payoff.
        self.assertFalse(_affordability_gate(10_000, 0))


class TestCarryPenaltyProbability(unittest.TestCase):
    def test_no_carries_no_penalty(self):
        from cash_mode.ai_carry_resolution import carry_penalty_probability
        self.assertEqual(carry_penalty_probability(0), 1.0)

    def test_one_carry_halves(self):
        from cash_mode.ai_carry_resolution import carry_penalty_probability
        self.assertEqual(carry_penalty_probability(1), 0.5)

    def test_four_carries_steeply_blocks(self):
        from cash_mode.ai_carry_resolution import carry_penalty_probability
        self.assertAlmostEqual(carry_penalty_probability(4), 0.0625)


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


class _RelHeat:
    """Minimal relationship_repo stub returning a configurable heat."""

    def __init__(self, heat: float = 0.0):
        self.heat = heat

    def load_relationship_state(self, *, observer_id, opponent_id, now):
        from unittest.mock import MagicMock
        return MagicMock(likability=0.5, respect=0.5, heat=self.heat)


class _AlwaysLowRng:
    def random(self):
        return 0.0


class TestVoluntaryPayoff:
    def test_payoff_clears_carry_and_transfers_chips(self, db_setup):
        from cash_mode.bankroll import AIBankrollState
        bankroll = db_setup["bankroll"]
        stake = db_setup["stake"]
        # Score-driven model: a fresh, zero-heat carry against default
        # eagerness/aspiration has pay_pull = 0 and hold_pull > 0, so
        # the score is 0 and no payoff fires regardless of the rng.
        # An aging high-heat carry crosses the threshold; that's what
        # this test exercises.
        carries = stake.list_carries_for_borrower(
            "borrower", BORROWER_KIND_PERSONALITY,
        )
        old_carry_now = ANCHOR + timedelta(days=20)  # past PAYOFF_AGE_RAMP_DAYS

        # Pin both bankroll rows' last_regen_tick to old_carry_now so
        # regen doesn't fire during projection — keeps the post-payoff
        # chip arithmetic regen-independent.
        bankroll.save_ai_bankroll(AIBankrollState(
            personality_id="borrower",
            chips=10_000,
            last_regen_tick=old_carry_now,
        ), sandbox_id=SBX)
        bankroll.save_ai_bankroll(AIBankrollState(
            personality_id="staker",
            chips=5_000,
            last_regen_tick=old_carry_now,
        ), sandbox_id=SBX)

        result = try_ai_voluntary_payoff(
            personality_id="borrower",
            carries=carries,
            bankroll_repo=bankroll,
            stake_repo=stake,
            relationship_repo=_RelHeat(heat=0.9),  # angry staker
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=old_carry_now,
            base_rate=PAYOFF_EVENT_BASE_RATE,  # event-gated trigger
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
        # Staker chips += 400 (no regen — last_regen_tick was pinned).
        assert staker_state.chips == 5_000 + 400

    def test_fresh_low_heat_carry_does_not_fire(self, db_setup):
        """Baseline AI with a fresh, low-heat carry shouldn't pay off
        even at event base_rate — score collapses to 0."""
        bankroll = db_setup["bankroll"]
        stake = db_setup["stake"]
        carries = stake.list_carries_for_borrower(
            "borrower", BORROWER_KIND_PERSONALITY,
        )
        result = try_ai_voluntary_payoff(
            personality_id="borrower",
            carries=carries,
            bankroll_repo=bankroll,
            stake_repo=stake,
            relationship_repo=_RelHeat(heat=0.0),
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=ANCHOR,
            base_rate=PAYOFF_EVENT_BASE_RATE,
        )
        assert result is None
        after = stake.load_stake("carry_1")
        assert after.status == STAKE_STATUS_CARRY  # still owes

    def test_affordability_gate_blocks_payoff(self, db_setup):
        """Affordability gate refuses payoff when even the minimum
        partial payment ($50) would breach the cheapest tier's seat
        floor. Higher-headroom AIs would partial-pay instead — see
        TestPartialPayoff for that path."""
        from cash_mode.bankroll import AIBankrollState
        from cash_mode.ai_carry_resolution import (
            PAYOFF_MIN_PAYMENT,
            _min_tier_buy_in_buffer,
        )
        bankroll = db_setup["bankroll"]
        stake = db_setup["stake"]
        old_now = ANCHOR + timedelta(days=20)

        # Headroom below PAYOFF_MIN_PAYMENT — gate blocks entirely.
        floor = _min_tier_buy_in_buffer()
        bankroll.save_ai_bankroll(AIBankrollState(
            personality_id="borrower",
            chips=floor + PAYOFF_MIN_PAYMENT - 1,
            last_regen_tick=old_now,
        ), sandbox_id=SBX)

        carries = stake.list_carries_for_borrower(
            "borrower", BORROWER_KIND_PERSONALITY,
        )
        result = try_ai_voluntary_payoff(
            personality_id="borrower",
            carries=carries,
            bankroll_repo=bankroll,
            stake_repo=stake,
            relationship_repo=_RelHeat(heat=0.9),
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=old_now,
            base_rate=PAYOFF_EVENT_BASE_RATE,
        )
        assert result is None
        after = stake.load_stake("carry_1")
        assert after.status == STAKE_STATUS_CARRY
        assert after.carry_amount == 400  # untouched


class TestPayoffPaymentAmount(unittest.TestCase):
    """Pure-math coverage of `_payoff_payment_amount` across archetypes
    + edge cases. The fraction lerps PAYOFF_MIN_EAGERNESS_FRACTION→1.0
    with payoff_eagerness, then caps at the outstanding carry."""

    def test_conscientious_pays_full_when_affordable(self):
        from cash_mode.ai_carry_resolution import _min_tier_buy_in_buffer
        floor = _min_tier_buy_in_buffer()
        # 1.0 eagerness × max_affordable, capped at carry_amount.
        payment = _payoff_payment_amount(
            bankroll_chips=floor + 10_000,
            carry_amount=500,
            payoff_eagerness=1.0,
        )
        self.assertEqual(payment, 500)

    def test_gambler_pays_partial(self):
        from cash_mode.ai_carry_resolution import _min_tier_buy_in_buffer
        floor = _min_tier_buy_in_buffer()
        # 0.0 eagerness commits PAYOFF_MIN_EAGERNESS_FRACTION (0.4)
        # of max_affordable = 1000. 0.4 × 1000 = 400.
        payment = _payoff_payment_amount(
            bankroll_chips=floor + 1000,
            carry_amount=2000,
            payoff_eagerness=0.0,
        )
        self.assertEqual(payment, 400)

    def test_baseline_lerps_midpoint(self):
        from cash_mode.ai_carry_resolution import _min_tier_buy_in_buffer
        floor = _min_tier_buy_in_buffer()
        # 0.5 eagerness → 0.4 + 0.5×0.6 = 0.7 of max_affordable.
        # max=1000 → desired=700; carry=2000 → payment=700.
        payment = _payoff_payment_amount(
            bankroll_chips=floor + 1000,
            carry_amount=2000,
            payoff_eagerness=0.5,
        )
        self.assertEqual(payment, 700)

    def test_caps_at_carry_amount(self):
        from cash_mode.ai_carry_resolution import _min_tier_buy_in_buffer
        floor = _min_tier_buy_in_buffer()
        # Desired = 0.7 × 5000 = 3500, but carry only $200 — payment
        # caps at the carry so we don't over-pay.
        payment = _payoff_payment_amount(
            bankroll_chips=floor + 5000,
            carry_amount=200,
            payoff_eagerness=0.5,
        )
        self.assertEqual(payment, 200)

    def test_zero_when_below_minimum_payment(self):
        from cash_mode.ai_carry_resolution import _min_tier_buy_in_buffer
        floor = _min_tier_buy_in_buffer()
        # Headroom = $30 < PAYOFF_MIN_PAYMENT ($50) → 0.
        payment = _payoff_payment_amount(
            bankroll_chips=floor + 30,
            carry_amount=1000,
            payoff_eagerness=1.0,
        )
        self.assertEqual(payment, 0)


class TestPartialPayoff:
    """Integration: partial payments leave the carry open + don't fire
    STAKE_REPAID. Subsequent full clears do both."""

    def test_partial_payment_keeps_status_carry(self, db_setup):
        """Carry > what the AI will pay → status stays carry,
        carry_amount decremented by payment, no STAKE_REPAID event."""
        from cash_mode.bankroll import AIBankrollState, PlayerBankrollState
        from cash_mode.ai_carry_resolution import _min_tier_buy_in_buffer
        import json
        bankroll = db_setup["bankroll"]
        stake = db_setup["stake"]
        old_now = ANCHOR + timedelta(days=20)

        # Retire the existing carry_1 ($400) and create a bigger
        # human-staker carry so the partial path is exercised. Use a
        # human staker so we can also verify the player_bankroll
        # routing handles partial payment correctly.
        stake.update_status("carry_1", STAKE_STATUS_SETTLED)
        bankroll.save_player_bankroll(PlayerBankrollState(
            player_id="human_partial_1", chips=2_000, starting_bankroll=5_000,
        ))
        stake.create_stake(Stake(
            stake_id="big_carry",
            session_id="player_session_borrower_partial",
            staker_id="human_partial_1",
            staker_kind=STAKER_KIND_HUMAN,
            borrower_id="borrower",
            borrower_kind=BORROWER_KIND_PERSONALITY,
            format=STAKE_FORMAT_PURE,
            principal=2000,
            match_amount=0,
            origination_fee=0,
            cut=0.30,
            status=STAKE_STATUS_CARRY,
            carry_amount=2000,
            stake_tier="$10",
            created_at=ANCHOR,
        ))

        # Mid-range eagerness so the score gate passes AND the
        # payment fraction is partial (not 100%).
        with bankroll._get_connection() as conn:
            conn.execute(
                "INSERT INTO personalities (personality_id, name, config_json) "
                "VALUES (?, ?, ?)",
                ("borrower", "Borrower", json.dumps({
                    "borrower_profile": {
                        "willing": True,
                        "aspiration_bias": 0.5,
                        "payoff_eagerness": 0.5,
                    },
                })),
            )

        floor = _min_tier_buy_in_buffer()
        bankroll.save_ai_bankroll(AIBankrollState(
            personality_id="borrower",
            chips=floor + 1000,  # max_affordable = 1000
            last_regen_tick=old_now,
        ), sandbox_id=SBX)

        carries = stake.list_carries_for_borrower(
            "borrower", BORROWER_KIND_PERSONALITY,
        )
        result = try_ai_voluntary_payoff(
            personality_id="borrower",
            carries=carries,
            bankroll_repo=bankroll,
            stake_repo=stake,
            relationship_repo=_RelHeat(heat=0.9),
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=old_now,
            base_rate=PAYOFF_EVENT_BASE_RATE,
        )
        # eagerness 0.5 → fraction 0.7 → payment = 0.7 × 1000 = 700;
        # carry is $2000 so payment caps at 700 (partial).
        assert result is not None
        assert result.amount == 700

        after = stake.load_stake("big_carry")
        assert after.status == STAKE_STATUS_CARRY  # still owes
        assert after.carry_amount == 2000 - 700  # 1300 remaining

        # Human staker credited with the partial.
        human = bankroll.load_player_bankroll("human_partial_1")
        assert human.chips == 2_000 + 700

        # update_payouts captured the partial too — running total of
        # what the staker has been paid (none yet → 700) and the
        # mirror on the borrower side (−700).
        assert after.staker_payout == 700
        assert after.borrower_payout == -700

    def test_full_clear_fires_settled_status(self, db_setup):
        """When the AI is conscientious-enough that the payment caps
        at the full carry, status flips to settled and the carry
        clears in one shot."""
        from cash_mode.bankroll import AIBankrollState
        from cash_mode.ai_carry_resolution import _min_tier_buy_in_buffer
        import json
        bankroll = db_setup["bankroll"]
        stake = db_setup["stake"]
        old_now = ANCHOR + timedelta(days=20)

        # Conscientious AI; ample bankroll well above carry.
        with bankroll._get_connection() as conn:
            conn.execute(
                "INSERT INTO personalities (personality_id, name, config_json) "
                "VALUES (?, ?, ?)",
                ("borrower", "Borrower", json.dumps({
                    "borrower_profile": {
                        "willing": True,
                        "aspiration_bias": 0.3,  # low pull-to-climb
                        "payoff_eagerness": 1.0,  # full conscientious
                    },
                })),
            )

        floor = _min_tier_buy_in_buffer()
        bankroll.save_ai_bankroll(AIBankrollState(
            personality_id="borrower",
            chips=floor + 5000,
            last_regen_tick=old_now,
        ), sandbox_id=SBX)
        bankroll.save_ai_bankroll(AIBankrollState(
            personality_id="staker",  # carry_1 is to AI-staker "staker"
            chips=5_000,
            last_regen_tick=old_now,
        ), sandbox_id=SBX)

        carries = stake.list_carries_for_borrower(
            "borrower", BORROWER_KIND_PERSONALITY,
        )
        result = try_ai_voluntary_payoff(
            personality_id="borrower",
            carries=carries,
            bankroll_repo=bankroll,
            stake_repo=stake,
            relationship_repo=_RelHeat(heat=0.9),
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=old_now,
            base_rate=PAYOFF_EVENT_BASE_RATE,
        )
        assert result is not None
        assert result.amount == 400  # the full $400 carry
        after = stake.load_stake("carry_1")
        assert after.status == STAKE_STATUS_SETTLED
        assert after.carry_amount == 0


class TestHumanStakerPayoffRouting:
    """Regression: human-staker carries credit player_bankroll_state, not
    a phantom AI bankroll row keyed by the human's owner_id."""

    def test_payoff_credits_player_bankroll(self, db_setup):
        bankroll = db_setup["bankroll"]
        stake = db_setup["stake"]

        # Seed a human player bankroll the credit should land on.
        bankroll.save_player_bankroll(PlayerBankrollState(
            player_id="human_owner_1",
            chips=2_000,
            starting_bankroll=5_000,
        ))

        # Replace the AI-staker carry with a human-staker one for the
        # same borrower (carry_1 was created in db_setup).
        stake.update_status("carry_1", STAKE_STATUS_SETTLED)  # retire it
        stake.create_stake(Stake(
            stake_id="human_carry_1",
            session_id="player_session_borrower_42",
            staker_id="human_owner_1",
            staker_kind=STAKER_KIND_HUMAN,
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

        carries = stake.list_carries_for_borrower(
            "borrower", BORROWER_KIND_PERSONALITY,
        )

        result = try_ai_voluntary_payoff(
            personality_id="borrower",
            carries=carries,
            bankroll_repo=bankroll,
            stake_repo=stake,
            relationship_repo=_RelHeat(heat=0.9),
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=ANCHOR + timedelta(days=20),
            base_rate=PAYOFF_EVENT_BASE_RATE,
        )
        assert result is not None
        assert result.kind == 'payoff'
        assert result.amount == 400

        # Stake settled.
        after = stake.load_stake("human_carry_1")
        assert after.status == STAKE_STATUS_SETTLED
        assert after.carry_amount == 0

        # Human bankroll credited (the bug was that this didn't happen).
        human = bankroll.load_player_bankroll("human_owner_1")
        assert human is not None
        assert human.chips == 2_000 + 400

        # And no phantom AI bankroll row got created keyed by the human's id.
        phantom = bankroll.load_ai_bankroll("human_owner_1", sandbox_id=SBX)
        assert phantom is None

    def test_payoff_skipped_when_player_bankroll_missing(self, db_setup):
        """Pre-flight refuses to debit the AI when the human row is absent —
        without it we'd vaporize chips from the universe."""
        bankroll = db_setup["bankroll"]
        stake = db_setup["stake"]

        stake.update_status("carry_1", STAKE_STATUS_SETTLED)
        stake.create_stake(Stake(
            stake_id="human_carry_2",
            session_id="player_session_borrower_43",
            staker_id="missing_human",  # no player_bankroll_state row
            staker_kind=STAKER_KIND_HUMAN,
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

        carries = stake.list_carries_for_borrower(
            "borrower", BORROWER_KIND_PERSONALITY,
        )
        borrower_before = bankroll.load_ai_bankroll(
            "borrower", sandbox_id=SBX,
        ).chips

        result = try_ai_voluntary_payoff(
            personality_id="borrower",
            carries=carries,
            bankroll_repo=bankroll,
            stake_repo=stake,
            relationship_repo=_RelHeat(heat=0.9),
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=ANCHOR + timedelta(days=20),
            base_rate=PAYOFF_EVENT_BASE_RATE,
        )
        assert result is None
        # Borrower bankroll untouched — no debit happened.
        borrower_after = bankroll.load_ai_bankroll(
            "borrower", sandbox_id=SBX,
        ).chips
        assert borrower_after == borrower_before
        # Stake still a carry.
        after = stake.load_stake("human_carry_2")
        assert after.status == STAKE_STATUS_CARRY


class TestHumanStakerForgivenessConsent:
    """v110 consent flow: AI surfaces a pending forgiveness ask to
    the human staker (status stays carry, pending_forgiveness_ask is
    stamped). Auto-grant must NEVER fire on human-staker carries —
    silent void would erase the player's chips without their say."""

    def test_human_staker_carry_stamps_pending_ask(self, db_setup):
        stake = db_setup["stake"]
        stake.update_status("carry_1", STAKE_STATUS_SETTLED)
        stake.create_stake(Stake(
            stake_id="human_carry_3",
            session_id="player_session_borrower_44",
            staker_id="human_owner_1",
            staker_kind=STAKER_KIND_HUMAN,
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

        from unittest.mock import MagicMock
        rel = MagicMock()
        # Generous relationship — under the old auto-grant path this
        # would have flipped status=settled instantly. Under v110 it
        # stamps a pending ask instead.
        rel.load_relationship_state.return_value = MagicMock(
            likability=0.95, respect=0.95, heat=0.0,
        )

        carries = stake.list_carries_for_borrower(
            "borrower", BORROWER_KIND_PERSONALITY,
        )
        result = try_ai_forgiveness_ask(
            personality_id="borrower",
            carries=carries,
            bankroll_repo=db_setup["bankroll"],
            stake_repo=stake,
            relationship_repo=rel,
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=ANCHOR,
        )
        # Returns a pending result so the dispatcher skips the
        # explicit-default branch for this AI on the same tick.
        assert result is not None
        assert result.kind == 'forgiveness_pending'
        assert result.amount == 400
        # Carry still open; pending ask stamped; rate-limit stamped.
        after = stake.load_stake("human_carry_3")
        assert after.status == STAKE_STATUS_CARRY
        assert after.carry_amount == 400
        assert after.pending_forgiveness_ask is not None
        assert after.forgiveness_last_asked is not None
        # Listable via the staker-side helper.
        pending = stake.list_pending_forgiveness_for_staker("human_owner_1")
        assert len(pending) == 1
        assert pending[0].stake_id == "human_carry_3"

    def test_existing_pending_ask_skips_repeat(self, db_setup):
        """Once an ask is pending, the AI doesn't stack a second one
        even if the rate-limit window has passed."""
        stake = db_setup["stake"]
        stake.update_status("carry_1", STAKE_STATUS_SETTLED)
        stake.create_stake(Stake(
            stake_id="human_carry_4",
            session_id="player_session_borrower_45",
            staker_id="human_owner_1",
            staker_kind=STAKER_KIND_HUMAN,
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
        # Pre-stamp a pending ask (e.g. from an earlier tick).
        stake.update_pending_forgiveness_ask("human_carry_4", ANCHOR)

        from unittest.mock import MagicMock
        rel = MagicMock()
        rel.load_relationship_state.return_value = MagicMock(
            likability=0.95, respect=0.95, heat=0.0,
        )
        carries = stake.list_carries_for_borrower(
            "borrower", BORROWER_KIND_PERSONALITY,
        )
        result = try_ai_forgiveness_ask(
            personality_id="borrower",
            carries=carries,
            bankroll_repo=db_setup["bankroll"],
            stake_repo=stake,
            relationship_repo=rel,
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=ANCHOR + timedelta(days=30),  # past rate-limit window
        )
        assert result is None  # already pending; don't re-ask


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
        and skip house-staker carries (NULL staker_id). The score model
        needs aged carries + heat for payoff to clear the per-tick base
        rate (0.005); we set both up so the test is deterministic."""
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
            created_at=ANCHOR + timedelta(days=1),  # newer
        ))

        batch = resolve_ai_carries(
            bankroll_repo=db_setup["bankroll"],
            stake_repo=db_setup["stake"],
            relationship_repo=_RelHeat(heat=0.9),
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            energy_lookup=lambda pid: 0.5,
            rng=_AlwaysLowRng(),
            now=ANCHOR + timedelta(days=30),
        )
        # First resolved carry must be the oldest (carry_1).
        assert len(batch.results) == 1  # at most one resolution per AI per refresh
        assert batch.results[0].stake_id == "carry_1"


if __name__ == '__main__':
    unittest.main()

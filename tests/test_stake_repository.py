"""Tests for the v98 StakeRepository.

CRUD round-trip + status transitions + per-borrower / per-staker carry
lookups. Uses a tempdb fixture so each test starts from a clean
schema; same pattern as `tests/test_chip_ledger.py`.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cash_mode.stakes import (
    BORROWER_KIND_HUMAN,
    BORROWER_KIND_PERSONALITY,
    STAKE_FORMAT_HOUSE,
    STAKE_FORMAT_MATCH_SHARE,
    STAKE_FORMAT_PURE,
    STAKE_STATUS_ACTIVE,
    STAKE_STATUS_CARRY,
    STAKE_STATUS_DEFAULTED,
    STAKE_STATUS_SETTLED,
    STAKER_KIND_HOUSE,
    STAKER_KIND_PERSONALITY,
    Stake,
)
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.stake_repository import StakeRepository

ANCHOR = datetime(2026, 5, 19, 12, 0, 0)


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / "test.db")
        SchemaManager(db_path).ensure_schema()
        yield StakeRepository(db_path)


def _make_stake(
    *,
    stake_id: str = "stk-1",
    session_id: str = "sess-1",
    staker_id="napoleon",
    staker_kind: str = STAKER_KIND_PERSONALITY,
    borrower_id: str = "alice",
    borrower_kind: str = BORROWER_KIND_HUMAN,
    format: str = STAKE_FORMAT_PURE,
    principal: int = 400,
    match_amount: int = 0,
    origination_fee: int = 20,
    cut: float = 0.20,
    status: str = STAKE_STATUS_ACTIVE,
    carry_amount: int = 0,
    stake_tier: str = "$10",
    created_at: datetime = ANCHOR,
    settled_at=None,
    sandbox_id=None,
) -> Stake:
    return Stake(
        stake_id=stake_id,
        session_id=session_id,
        staker_id=staker_id,
        staker_kind=staker_kind,
        borrower_id=borrower_id,
        borrower_kind=borrower_kind,
        format=format,
        principal=principal,
        match_amount=match_amount,
        origination_fee=origination_fee,
        cut=cut,
        status=status,
        carry_amount=carry_amount,
        stake_tier=stake_tier,
        created_at=created_at,
        settled_at=settled_at,
        sandbox_id=sandbox_id,
    )


class TestCreateAndLoad:
    def test_round_trip_personality_stake(self, repo):
        stake = _make_stake()
        repo.create_stake(stake)
        loaded = repo.load_stake("stk-1")
        assert loaded == stake

    def test_round_trip_house_stake_with_null_staker_id(self, repo):
        stake = _make_stake(
            staker_id=None,
            staker_kind=STAKER_KIND_HOUSE,
            format=STAKE_FORMAT_HOUSE,
            origination_fee=0,
            cut=0.40,
        )
        repo.create_stake(stake)
        loaded = repo.load_stake("stk-1")
        assert loaded == stake
        assert loaded.staker_id is None

    def test_round_trip_match_share(self, repo):
        stake = _make_stake(
            format=STAKE_FORMAT_MATCH_SHARE,
            principal=200,
            match_amount=200,
            origination_fee=0,
            cut=0.45,
        )
        repo.create_stake(stake)
        loaded = repo.load_stake("stk-1")
        assert loaded.match_amount == 200
        assert loaded.cut == 0.45

    def test_round_trip_with_settled_at(self, repo):
        settled_at = ANCHOR + timedelta(hours=1)
        stake = _make_stake(
            status=STAKE_STATUS_SETTLED,
            settled_at=settled_at,
        )
        repo.create_stake(stake)
        loaded = repo.load_stake("stk-1")
        assert loaded.settled_at == settled_at

    def test_missing_stake_returns_none(self, repo):
        assert repo.load_stake("does-not-exist") is None


class TestLoadActiveForSession:
    def test_returns_the_active_stake(self, repo):
        active = _make_stake(stake_id="stk-active", session_id="sess-1")
        settled = _make_stake(
            stake_id="stk-settled",
            session_id="sess-2",
            status=STAKE_STATUS_SETTLED,
            settled_at=ANCHOR,
        )
        repo.create_stake(active)
        repo.create_stake(settled)
        loaded = repo.load_active_for_session("sess-1")
        assert loaded is not None
        assert loaded.stake_id == "stk-active"

    def test_returns_none_when_no_active(self, repo):
        settled = _make_stake(
            stake_id="stk-settled",
            session_id="sess-1",
            status=STAKE_STATUS_SETTLED,
            settled_at=ANCHOR,
        )
        repo.create_stake(settled)
        assert repo.load_active_for_session("sess-1") is None

    def test_returns_none_for_unknown_session(self, repo):
        assert repo.load_active_for_session("never-existed") is None


class TestLoadActiveForBorrower:
    """Phase 4 Commit 3: lookup by borrower_id (AI sessions have
    synthesized session_ids, so this is the way to find their active
    stake at leave-time)."""

    def test_returns_active_stake_for_human_borrower(self, repo):
        active = _make_stake(
            stake_id="stk-h",
            session_id="sess-h",
            borrower_id="alice",
            borrower_kind=BORROWER_KIND_HUMAN,
        )
        repo.create_stake(active)
        loaded = repo.load_active_for_borrower("alice", BORROWER_KIND_HUMAN)
        assert loaded is not None
        assert loaded.stake_id == "stk-h"

    def test_borrower_kind_filter(self, repo):
        # Same id, different borrower kind — must not collide.
        human_stake = _make_stake(
            stake_id="stk-h",
            session_id="sess-h",
            borrower_id="napoleon",
            borrower_kind=BORROWER_KIND_HUMAN,
        )
        ai_stake = _make_stake(
            stake_id="stk-ai",
            session_id="sess-ai",
            borrower_id="napoleon",
            borrower_kind=BORROWER_KIND_PERSONALITY,
        )
        repo.create_stake(human_stake)
        repo.create_stake(ai_stake)
        assert (
            repo.load_active_for_borrower(
                "napoleon",
                BORROWER_KIND_HUMAN,
            ).stake_id
            == "stk-h"
        )
        assert (
            repo.load_active_for_borrower(
                "napoleon",
                BORROWER_KIND_PERSONALITY,
            ).stake_id
            == "stk-ai"
        )

    def test_returns_none_when_no_active_stake(self, repo):
        settled = _make_stake(
            stake_id="stk-old",
            session_id="sess-old",
            borrower_id="alice",
            borrower_kind=BORROWER_KIND_HUMAN,
            status=STAKE_STATUS_SETTLED,
            settled_at=ANCHOR,
        )
        repo.create_stake(settled)
        assert (
            repo.load_active_for_borrower(
                "alice",
                BORROWER_KIND_HUMAN,
            )
            is None
        )

    def test_returns_most_recent_when_multiple_active(self, repo):
        # Shouldn't happen post-Phase-4 (the lobby's borrower-profile
        # gate prevents double-take-stake) — but the read path stays
        # deterministic if the invariant slips.
        older = _make_stake(
            stake_id="stk-old",
            session_id="sess-old",
            borrower_id="alice",
            borrower_kind=BORROWER_KIND_HUMAN,
            created_at=ANCHOR,
        )
        newer = _make_stake(
            stake_id="stk-new",
            session_id="sess-new",
            borrower_id="alice",
            borrower_kind=BORROWER_KIND_HUMAN,
            created_at=ANCHOR + timedelta(hours=1),
        )
        repo.create_stake(older)
        repo.create_stake(newer)
        loaded = repo.load_active_for_borrower(
            "alice",
            BORROWER_KIND_HUMAN,
        )
        assert loaded.stake_id == "stk-new"


class TestSandboxScoping:
    """2026-06-09 cross-sandbox mint guard. AI personas exist in every
    sandbox, so a stake funds `seat:ai:<sandbox>:<borrower>` and must
    settle against that SAME seat. `load_active_for_borrower(sandbox_id=...)`
    must only return a stake originated in that sandbox (or a legacy NULL
    row), so a world-tick processing sandbox B can't load — and drain — a
    stake originated in sandbox A.
    """

    def test_round_trips_sandbox_id(self, repo):
        stake = _make_stake(borrower_kind=BORROWER_KIND_PERSONALITY, sandbox_id="sb-A")
        repo.create_stake(stake)
        assert repo.load_stake("stk-1").sandbox_id == "sb-A"

    def test_scoped_lookup_finds_same_sandbox(self, repo):
        repo.create_stake(
            _make_stake(
                stake_id="stk-A",
                borrower_id="honey_badger",
                borrower_kind=BORROWER_KIND_PERSONALITY,
                sandbox_id="sb-A",
            )
        )
        loaded = repo.load_active_for_borrower(
            "honey_badger", BORROWER_KIND_PERSONALITY, sandbox_id="sb-A"
        )
        assert loaded is not None
        assert loaded.stake_id == "stk-A"

    def test_scoped_lookup_excludes_other_sandbox(self, repo):
        # The leak: stake originated in sb-A; a tick in sb-B must NOT find it
        # (settling it would drain sb-B's never-funded seat → mint).
        repo.create_stake(
            _make_stake(
                stake_id="stk-A",
                borrower_id="honey_badger",
                borrower_kind=BORROWER_KIND_PERSONALITY,
                sandbox_id="sb-A",
            )
        )
        assert (
            repo.load_active_for_borrower(
                "honey_badger", BORROWER_KIND_PERSONALITY, sandbox_id="sb-B"
            )
            is None
        )

    def test_each_sandbox_settles_its_own_stake(self, repo):
        # Same persona staked in two sandboxes simultaneously (the per-sandbox
        # model). Each scoped lookup returns its own row, never the other's.
        repo.create_stake(
            _make_stake(
                stake_id="stk-A",
                session_id="sess-A",
                borrower_id="honey_badger",
                borrower_kind=BORROWER_KIND_PERSONALITY,
                sandbox_id="sb-A",
            )
        )
        repo.create_stake(
            _make_stake(
                stake_id="stk-B",
                session_id="sess-B",
                borrower_id="honey_badger",
                borrower_kind=BORROWER_KIND_PERSONALITY,
                sandbox_id="sb-B",
            )
        )
        a = repo.load_active_for_borrower(
            "honey_badger", BORROWER_KIND_PERSONALITY, sandbox_id="sb-A"
        )
        b = repo.load_active_for_borrower(
            "honey_badger", BORROWER_KIND_PERSONALITY, sandbox_id="sb-B"
        )
        assert a.stake_id == "stk-A"
        assert b.stake_id == "stk-B"

    def test_legacy_null_row_still_findable_from_any_sandbox(self, repo):
        # Pre-fix rows have sandbox_id NULL — they stay findable so they
        # settle out under the old global behavior rather than orphaning.
        repo.create_stake(
            _make_stake(
                stake_id="stk-legacy",
                borrower_id="honey_badger",
                borrower_kind=BORROWER_KIND_PERSONALITY,
                sandbox_id=None,
            )
        )
        loaded = repo.load_active_for_borrower(
            "honey_badger", BORROWER_KIND_PERSONALITY, sandbox_id="sb-anything"
        )
        assert loaded is not None
        assert loaded.stake_id == "stk-legacy"

    def test_unscoped_lookup_is_global(self, repo):
        # No sandbox_id → original global behavior (human-borrower paths).
        repo.create_stake(
            _make_stake(
                stake_id="stk-A",
                borrower_id="honey_badger",
                borrower_kind=BORROWER_KIND_PERSONALITY,
                sandbox_id="sb-A",
            )
        )
        loaded = repo.load_active_for_borrower("honey_badger", BORROWER_KIND_PERSONALITY)
        assert loaded is not None
        assert loaded.stake_id == "stk-A"


class TestListCarriesForBorrower:
    def test_returns_all_carries_for_borrower(self, repo):
        # Same borrower, two carries (different stakers), one settled.
        carry_a = _make_stake(
            stake_id="stk-a",
            session_id="sess-1",
            staker_id="napoleon",
            status=STAKE_STATUS_CARRY,
            carry_amount=120,
            created_at=ANCHOR,
        )
        carry_b = _make_stake(
            stake_id="stk-b",
            session_id="sess-2",
            staker_id="bezos",
            status=STAKE_STATUS_CARRY,
            carry_amount=80,
            created_at=ANCHOR + timedelta(hours=1),
        )
        settled = _make_stake(
            stake_id="stk-c",
            session_id="sess-3",
            status=STAKE_STATUS_SETTLED,
            settled_at=ANCHOR,
        )
        repo.create_stake(carry_a)
        repo.create_stake(carry_b)
        repo.create_stake(settled)

        carries = repo.list_carries_for_borrower(
            "alice",
            BORROWER_KIND_HUMAN,
        )
        assert len(carries) == 2
        # Oldest first, per the ORDER BY in the repo.
        assert carries[0].stake_id == "stk-a"
        assert carries[1].stake_id == "stk-b"

    def test_borrower_kind_filter(self, repo):
        # Same id, different borrower kind — must not collide.
        human_carry = _make_stake(
            stake_id="stk-human",
            borrower_id="zeus",
            borrower_kind=BORROWER_KIND_HUMAN,
            status=STAKE_STATUS_CARRY,
            carry_amount=50,
        )
        ai_carry = _make_stake(
            stake_id="stk-ai",
            session_id="sess-2",
            borrower_id="zeus",
            borrower_kind=BORROWER_KIND_PERSONALITY,
            status=STAKE_STATUS_CARRY,
            carry_amount=70,
        )
        repo.create_stake(human_carry)
        repo.create_stake(ai_carry)

        humans = repo.list_carries_for_borrower("zeus", BORROWER_KIND_HUMAN)
        assert [s.stake_id for s in humans] == ["stk-human"]
        ais = repo.list_carries_for_borrower("zeus", BORROWER_KIND_PERSONALITY)
        assert [s.stake_id for s in ais] == ["stk-ai"]


class TestListCarriesForStaker:
    def test_returns_carries_owed_to_staker(self, repo):
        owed_to_napoleon_a = _make_stake(
            stake_id="stk-1",
            session_id="sess-1",
            staker_id="napoleon",
            borrower_id="alice",
            status=STAKE_STATUS_CARRY,
            carry_amount=100,
        )
        owed_to_napoleon_b = _make_stake(
            stake_id="stk-2",
            session_id="sess-2",
            staker_id="napoleon",
            borrower_id="bob",
            status=STAKE_STATUS_CARRY,
            carry_amount=60,
            created_at=ANCHOR + timedelta(hours=1),
        )
        owed_to_bezos = _make_stake(
            stake_id="stk-3",
            session_id="sess-3",
            staker_id="bezos",
            borrower_id="alice",
            status=STAKE_STATUS_CARRY,
            carry_amount=200,
        )
        repo.create_stake(owed_to_napoleon_a)
        repo.create_stake(owed_to_napoleon_b)
        repo.create_stake(owed_to_bezos)

        nap_carries = repo.list_carries_for_staker("napoleon")
        assert len(nap_carries) == 2
        assert {s.stake_id for s in nap_carries} == {"stk-1", "stk-2"}

    def test_house_staker_id_returns_empty(self, repo):
        # House stakes never carry (locked decision #3) — but if a
        # caller asks anyway, the NULL-comparison should silently
        # return nothing rather than crash.
        carries = repo.list_carries_for_staker(None)  # type: ignore[arg-type]
        assert carries == []


class TestUpdateStatus:
    def test_transition_active_to_settled(self, repo):
        repo.create_stake(_make_stake())
        ok = repo.update_status(
            "stk-1",
            STAKE_STATUS_SETTLED,
            settled_at=ANCHOR + timedelta(hours=2),
        )
        assert ok is True
        loaded = repo.load_stake("stk-1")
        assert loaded.status == STAKE_STATUS_SETTLED
        assert loaded.settled_at == ANCHOR + timedelta(hours=2)

    def test_transition_without_settled_at_preserves_it(self, repo):
        # The explicit-default action (Phase 2) flips status without
        # touching settled_at — let the original value stand.
        original = _make_stake(
            status=STAKE_STATUS_CARRY,
            settled_at=ANCHOR + timedelta(hours=1),
        )
        repo.create_stake(original)
        repo.update_status("stk-1", STAKE_STATUS_DEFAULTED)
        loaded = repo.load_stake("stk-1")
        assert loaded.status == STAKE_STATUS_DEFAULTED
        assert loaded.settled_at == ANCHOR + timedelta(hours=1)

    def test_unknown_stake_returns_false(self, repo):
        assert repo.update_status("ghost", STAKE_STATUS_SETTLED) is False


class TestUpdateCarryAmount:
    def test_sets_carry_amount(self, repo):
        repo.create_stake(_make_stake())
        repo.update_carry_amount("stk-1", 250)
        loaded = repo.load_stake("stk-1")
        assert loaded.carry_amount == 250

    def test_can_zero_a_carry(self, repo):
        repo.create_stake(
            _make_stake(
                status=STAKE_STATUS_CARRY,
                carry_amount=300,
            )
        )
        repo.update_carry_amount("stk-1", 0)
        loaded = repo.load_stake("stk-1")
        assert loaded.carry_amount == 0

    def test_unknown_stake_returns_false(self, repo):
        assert repo.update_carry_amount("ghost", 100) is False


class TestListStakesForSession:
    def test_returns_all_stakes_oldest_first(self, repo):
        s_old = _make_stake(
            stake_id="stk-old",
            session_id="sess-1",
            status=STAKE_STATUS_SETTLED,
            settled_at=ANCHOR,
            created_at=ANCHOR,
        )
        s_new = _make_stake(
            stake_id="stk-new",
            session_id="sess-1",
            created_at=ANCHOR + timedelta(hours=1),
        )
        repo.create_stake(s_old)
        repo.create_stake(s_new)

        rows = repo.list_stakes_for_session("sess-1")
        assert [s.stake_id for s in rows] == ["stk-old", "stk-new"]


class TestHasDefaultedStake:
    """First-hand signal for the dossier credit-history reveal."""

    def test_true_when_borrower_defaulted_on_staker(self, repo):
        repo.create_stake(
            _make_stake(
                stake_id="d1",
                staker_id="guest_jeff",
                staker_kind=STAKER_KIND_PERSONALITY,
                borrower_id="socrates",
                status=STAKE_STATUS_DEFAULTED,
            )
        )
        assert repo.has_defaulted_stake("guest_jeff", "socrates") is True

    def test_false_without_a_default(self, repo):
        repo.create_stake(
            _make_stake(
                stake_id="s1",
                staker_id="guest_jeff",
                borrower_id="socrates",
                status=STAKE_STATUS_SETTLED,
            )
        )
        assert repo.has_defaulted_stake("guest_jeff", "socrates") is False

    def test_false_for_other_pairs(self, repo):
        repo.create_stake(
            _make_stake(
                stake_id="d1",
                staker_id="guest_jeff",
                borrower_id="socrates",
                status=STAKE_STATUS_DEFAULTED,
            )
        )
        # Different borrower / different staker → no first-hand signal.
        assert repo.has_defaulted_stake("guest_jeff", "plato") is False
        assert repo.has_defaulted_stake("guest_other", "socrates") is False


class TestSetResolution:
    def test_set_and_read_back(self, repo):
        repo.create_stake(_make_stake(stake_id="r1", status=STAKE_STATUS_DEFAULTED))
        assert repo.set_resolution("r1", "bankruptcy") is True
        assert repo.load_stake("r1").resolution == "bankruptcy"

    def test_default_resolution_is_none(self, repo):
        repo.create_stake(_make_stake(stake_id="r2"))
        assert repo.load_stake("r2").resolution is None

    def test_set_resolution_missing_row(self, repo):
        assert repo.set_resolution("nope", "bankruptcy") is False

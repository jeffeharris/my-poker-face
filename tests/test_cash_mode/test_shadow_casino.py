"""Phase-1 dual-write shadow coverage for casino provisioning.

Verifies the `presence_shadow.shadow_transition(...)` calls added *after* the
authoritative `save_table` / bankroll writes in
`cash_mode/casino_provisioning.py` fire with the correct Presence events,
entity ids, and seat arguments — and that they are fully gated by the
`economy_flags.PRESENCE_SHADOW_WRITE_ENABLED` kill switch.

Design of the harness (why it does NOT spin up the real cash DB):

  * The thing under test is the *shadow* half — the additive transitions, not
    the (already-covered) authoritative seat/bankroll writes. So we drive the
    real seat functions with in-memory fakes that mimic only the repo methods
    those functions actually call (observed in the source: ``list_all_tables``,
    ``save_table``, ``save_ai_bankroll`` …) and a fake
    ``entity_presence_repo`` that records every ``persist_transition`` exactly
    as the live ``flask_app.extensions`` singleton would receive it.
  * The fake presence repo *replays each recorded transition through the real
    pure machine* (``cash_mode.presence.transition``) so the asserted end
    state (SEATED with the right seat / back to POOL) is validated by the same
    legality rules production will use at the flip — a recorded-but-illegal
    trajectory would raise here, catching a bad mapping.

Isolation: no global mutation that leaks (the flag + extensions singleton are
restored via monkeypatch), no real DB, no network. See ``tests/CLAUDE.md``.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest

from cash_mode import economy_flags
from cash_mode.presence import (
    IllegalPresenceTransition,
    Presence,
    PresenceEvent,
    PresenceState,
    ai_entity_id,
    offline,
    transition,
)

# Unmarked on purpose: this is a fast, pure-unit test (in-memory fakes, no DB,
# no app) — it should run in the `--quick` loop. Matches the unmarked majority
# of tests/test_cash_mode/. (`pytest.mark.unit` is not a registered marker.)


NOW = datetime(2026, 5, 31, tzinfo=timezone.utc)
SANDBOX = "sb-shadow-test"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakePresenceRepo:
    """Records persist_transition calls and replays them through the real pure
    machine so the resulting state is legality-checked.

    Call shape mirrors what `presence_shadow.shadow_transition` forwards:
        persist_transition(entity_id, sandbox_id, event,
                           table_id=, seat_index=, updated_at=)
    """

    def __init__(self) -> None:
        # ordered log of (entity_id, sandbox_id, event, table_id, seat_index)
        self.calls: list[tuple] = []
        self._state: dict[tuple, PresenceState] = {}

    def persist_transition(
        self,
        entity_id,
        sandbox_id,
        event,
        *,
        table_id=None,
        seat_index=None,
        updated_at=None,
    ):
        self.calls.append((entity_id, sandbox_id, event, table_id, seat_index))
        key = (entity_id, sandbox_id)
        current = self._state.get(key) or offline(entity_id, sandbox_id)
        # Replay through the real machine — raises on an illegal trajectory.
        self._state[key] = transition(
            current,
            event,
            table_id=table_id,
            seat_index=seat_index,
            updated_at=updated_at,
        )
        return len(self.calls)

    def state_of(self, entity_id, sandbox_id) -> PresenceState:
        return self._state.get((entity_id, sandbox_id)) or offline(entity_id, sandbox_id)


@pytest.fixture
def shadow_on(monkeypatch):
    """Enable the kill switch and route shadow writes to a recording repo."""
    monkeypatch.setattr(
        economy_flags, "PRESENCE_SHADOW_WRITE_ENABLED", True, raising=False
    )
    repo = FakePresenceRepo()
    # shadow_transition() resolves the repo from flask_app.extensions when no
    # repo= is passed; the call sites don't pass one, so patch the lookup.
    import flask_app.extensions as extensions

    monkeypatch.setattr(extensions, "entity_presence_repo", repo, raising=False)
    return repo


@pytest.fixture
def shadow_off(monkeypatch):
    monkeypatch.setattr(
        economy_flags, "PRESENCE_SHADOW_WRITE_ENABLED", False, raising=False
    )
    repo = FakePresenceRepo()
    import flask_app.extensions as extensions

    monkeypatch.setattr(extensions, "entity_presence_repo", repo, raising=False)
    return repo


class FakeCashTableRepo:
    def __init__(self, tables):
        self._tables = {t.table_id: t for t in tables}

    def list_all_tables(self, *, sandbox_id):
        return list(self._tables.values())

    def save_table(self, table, *, sandbox_id, now=None):
        self._tables[table.table_id] = table

    def delete_table(self, table_id, *, sandbox_id):
        self._tables.pop(table_id, None)

    # `is_closing()` (used by _shed_excess_fish) reads this; None == not closing.
    def get_closing_countdown(self, table_id, *, sandbox_id=None):
        return None


class FakeBankrollRepo:
    def __init__(self, chips_by_pid=None):
        self._chips = dict(chips_by_pid or {})

    def load_ai_bankroll(self, personality_id, *, sandbox_id=None):
        from cash_mode.bankroll import AIBankrollState

        return AIBankrollState(
            personality_id=personality_id,
            chips=self._chips.get(personality_id, 0),
            last_regen_tick=NOW,
        )

    def save_ai_bankroll(self, state, *, sandbox_id=None):
        self._chips[state.personality_id] = state.chips

    def load_personality_knobs(self, personality_id):
        # Fish carry bankroll_rate=0 (projected == stored), so the seat debit
        # commits no regen — keeps the refill funding path a clean transfer.
        # Real signature returns a BankrollKnobs (attribute-accessed in the
        # debit), so return one rather than a dict.
        from cash_mode.bankroll import BankrollKnobs

        return BankrollKnobs(
            starting_bankroll=0,
            bankroll_rate=0,
            buy_in_multiplier=1.0,
            stake_comfort_zone="$2",
        )


class FakeLedgerRepo:
    """Accepts any ledger call and reports success (truthy row id).

    `compute_bank_pool_reserves` reads `sum_destructions_by_reason` /
    `sum_creations_by_reason` (both dict-returning); we report a fat deposit so
    the real `_prefund_fish_from_pool` funding path (reserve check → draw) is
    satisfied and the refill SEED→SIT transition actually fires rather than
    short-circuiting on an empty pool. Every other ledger call (the various
    `record_casino_seat_*`) returns a truthy row id via `__getattr__`.
    """

    def __init__(self, bank_pool_balance: int = 10_000_000):
        self.calls = []
        self._pool = bank_pool_balance

    # Bank-pool reserve = Σ(deposits) − Σ(draws); report deposits only so the
    # virtual pool reads as `_pool` chips deep.
    def sum_destructions_by_reason(self, *, sandbox_id=None):
        from cash_mode.closed_economy import BANK_POOL_DEPOSIT_REASONS

        return {r: self._pool for r in BANK_POOL_DEPOSIT_REASONS}

    def sum_creations_by_reason(self, *, sandbox_id=None):
        return {}

    def __getattr__(self, _name):
        def _ok(*a, **k):
            self.calls.append((_name, a, k))
            return 1

        return _ok


def _make_casino_table(table_id, seats):
    from cash_mode.tables import CashTableState

    return CashTableState(
        table_id=table_id,
        stake_label="$2",
        seats=seats,
        created_at=NOW,
        last_activity_at=NOW,
        name=f"Casino — {table_id}",
        table_type="casino",
    )


def _preseat(repo, pid, table_id="casino-2-001", seat_index=0):
    """Drive the recording repo to SEATED for `pid` (SEED → SIT) so a later
    RETURN_TO_POOL is a legal edge — mirroring reality where the AI was a
    POOL-funded seat before it left. Returns the entity id. The SEED/SIT rows
    are NOT part of what each RETURN_TO_POOL test asserts on (those filter to
    the trailing event), so this only sets up a legal pre-state.
    """
    e = ai_entity_id(pid)
    repo.persist_transition(e, SANDBOX, PresenceEvent.SEED)
    repo.persist_transition(
        e, SANDBOX, PresenceEvent.SIT, table_id=table_id, seat_index=seat_index
    )
    return e


# --------------------------------------------------------------------------- #
# RETURN_TO_POOL on reclaim
# --------------------------------------------------------------------------- #


def test_reclaim_shadows_return_to_pool(shadow_on):
    """A zombie (unresolved) AI seat reclaimed → shadow RETURN_TO_POOL → POOL."""
    from cash_mode.casino_provisioning import _reclaim_zombie_casino_seats
    from cash_mode.tables import open_slot

    zombie_pid = "tourist-deadbeef"
    seats = [open_slot() for _ in range(6)]
    seats[2] = {"kind": "ai", "personality_id": zombie_pid, "chips": 0}
    table = _make_casino_table("casino-2-001", seats)

    repo = shadow_on
    entity = _preseat(repo, zombie_pid, seat_index=2)  # legal pre-state (SEATED)
    before = len(repo.calls)
    n = _reclaim_zombie_casino_seats(
        FakeCashTableRepo([table]),
        FakeLedgerRepo(),
        sandbox_id=SANDBOX,
        valid_pids=set(),  # zombie_pid not valid → unresolved → reclaimed
        fish_ids=set(),
        now=NOW,
    )

    assert n == 1
    events = [c for c in repo.calls[before:] if c[0] == entity]
    assert events, "expected a shadow transition for the reclaimed seat"
    assert events[-1][2] is PresenceEvent.RETURN_TO_POOL
    # pool return must clear seat args
    assert events[-1][3] is None and events[-1][4] is None
    assert repo.state_of(entity, SANDBOX).state is Presence.POOL


def test_reclaim_no_rows_when_flag_off(shadow_off):
    from cash_mode.casino_provisioning import _reclaim_zombie_casino_seats
    from cash_mode.tables import open_slot

    seats = [open_slot() for _ in range(6)]
    seats[2] = {"kind": "ai", "personality_id": "tourist-x", "chips": 0}
    table = _make_casino_table("casino-2-001", seats)

    repo = shadow_off
    _reclaim_zombie_casino_seats(
        FakeCashTableRepo([table]),
        FakeLedgerRepo(),
        sandbox_id=SANDBOX,
        valid_pids=set(),
        fish_ids=set(),
        now=NOW,
    )
    assert repo.calls == [], "flag OFF must record zero shadow rows"


# --------------------------------------------------------------------------- #
# RETURN_TO_POOL on bankroll drain
# --------------------------------------------------------------------------- #


def test_drain_shadows_return_to_pool(shadow_on):
    from cash_mode.casino_provisioning import _drain_fish_bankroll_to_pool

    pid = "fish_alpha"
    repo = shadow_on
    entity = _preseat(repo, pid)  # legal pre-state (SEATED) before it leaves
    before = len(repo.calls)
    returned, stranded = _drain_fish_bankroll_to_pool(
        FakeBankrollRepo({pid: 500}),
        FakeLedgerRepo(),
        personality_id=pid,
        sandbox_id=SANDBOX,
        now=NOW,
        reason_detail="casino_teardown",
    )
    assert (returned, stranded) == (500, 0)
    assert [c[2] for c in repo.calls[before:] if c[0] == entity] == [
        PresenceEvent.RETURN_TO_POOL
    ]
    assert repo.state_of(entity, SANDBOX).state is Presence.POOL


def test_drain_no_shadow_when_no_chips(shadow_on):
    """A fish with no bankroll is a no-op: no chip move, no shadow row."""
    from cash_mode.casino_provisioning import _drain_fish_bankroll_to_pool

    pid = "fish_empty"
    repo = shadow_on
    _drain_fish_bankroll_to_pool(
        FakeBankrollRepo({pid: 0}),
        FakeLedgerRepo(),
        personality_id=pid,
        sandbox_id=SANDBOX,
        now=NOW,
        reason_detail="casino_teardown",
    )
    assert repo.calls == []


def test_drain_no_rows_when_flag_off(shadow_off):
    from cash_mode.casino_provisioning import _drain_fish_bankroll_to_pool

    repo = shadow_off
    _drain_fish_bankroll_to_pool(
        FakeBankrollRepo({"fish_a": 500}),
        FakeLedgerRepo(),
        personality_id="fish_a",
        sandbox_id=SANDBOX,
        now=NOW,
        reason_detail="casino_teardown",
    )
    assert repo.calls == []


# --------------------------------------------------------------------------- #
# RETURN_TO_POOL on shed
# --------------------------------------------------------------------------- #


def test_shed_shadows_return_to_pool(shadow_on):
    from cash_mode.casino_provisioning import CASINO_FISH_MAX, _shed_excess_fish
    from cash_mode.tables import ai_slot_fish, open_slot

    # Seat (MAX + 1) stamped fish so exactly one is shed.
    seats = [open_slot() for _ in range(6)]
    fish_pids = [f"fish_{i}" for i in range(CASINO_FISH_MAX + 1)]
    for i, pid in enumerate(fish_pids):
        seats[i] = ai_slot_fish(pid, 200)
    table = _make_casino_table("casino-2-001", seats)

    repo = shadow_on
    # Legal pre-state: every seated fish is SEATED before the shed.
    for i, pid in enumerate(fish_pids):
        _preseat(repo, pid, seat_index=i)
    before = len(repo.calls)
    shed = _shed_excess_fish(
        FakeCashTableRepo([table]),
        FakeLedgerRepo(),
        sandbox_id=SANDBOX,
        now=NOW,
    )
    assert shed == 1
    pool_returns = [
        c for c in repo.calls[before:] if c[2] is PresenceEvent.RETURN_TO_POOL
    ]
    assert len(pool_returns) == 1
    shed_entity = pool_returns[0][0]
    assert repo.state_of(shed_entity, SANDBOX).state is Presence.POOL


# --------------------------------------------------------------------------- #
# SEED + SIT on refill (seat-map match)
# --------------------------------------------------------------------------- #


def test_refill_shadows_seed_then_sit(shadow_on):
    from cash_mode.casino_provisioning import _refill_one_fish
    from cash_mode.tables import open_slot

    seats = [open_slot() for _ in range(6)]
    table = _make_casino_table("casino-2-001", seats)

    repo = shadow_on
    fish_id = "fish_refill"
    # Empty fish bankroll (the un-seated invariant) + a deep bank pool
    # (FakeLedgerRepo's default reserve) → the real funding path
    # (pool → prefund → buy-in debit) succeeds and the seat write lands, so
    # the shadow SEED→SIT fires. The shadow only mirrors a *successful* seat,
    # which is the behaviour under test.
    result = _refill_one_fish(
        table,
        stake_label="$2",
        fish_buy_in=200,
        table_max_buy_in=200,
        chip_ledger_repo=FakeLedgerRepo(),
        cash_table_repo=FakeCashTableRepo([table]),
        bankroll_repo=FakeBankrollRepo(),
        sandbox_id=SANDBOX,
        rng=random.Random(7),
        now=NOW,
        already_seated=set(),
        fish_ids={fish_id},
    )

    # With a funded pool the refill must succeed and seat the fish.
    assert result is not None, "refill should seat the fish given a funded pool"
    entity = ai_entity_id(fish_id)
    events = [c for c in repo.calls if c[0] == entity]
    assert [c[2] for c in events] == [PresenceEvent.SEED, PresenceEvent.SIT]
    sit = events[-1]
    assert sit[3] == "casino-2-001"  # table_id
    assert sit[4] is not None  # seat_index
    final = repo.state_of(entity, SANDBOX)
    assert final.state is Presence.SEATED
    assert final.table_id == "casino-2-001"
    assert final.seat_index == sit[4]


# --------------------------------------------------------------------------- #
# Harness self-guard: an illegal trajectory raises in the replay machine
# --------------------------------------------------------------------------- #


def test_replay_machine_rejects_double_sit():
    """SIT-from-SEATED is illegal, so a buggy mapping that re-seats without a
    return would raise — proving the harness validates trajectory legality."""
    repo = FakePresenceRepo()
    e = ai_entity_id("x")
    repo.persist_transition(e, SANDBOX, PresenceEvent.SEED)
    repo.persist_transition(e, SANDBOX, PresenceEvent.SIT, table_id="t", seat_index=0)
    with pytest.raises(IllegalPresenceTransition):
        repo.persist_transition(e, SANDBOX, PresenceEvent.SIT, table_id="t", seat_index=1)

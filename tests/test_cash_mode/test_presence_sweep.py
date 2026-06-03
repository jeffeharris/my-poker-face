"""R3 — deletion-time presence sweeps (free_human_seat_on_delete /
sweep_presence_on_persona_delete). They run under PRESENCE_AUTHORITY_ENABLED and
open the entity's seat (driving GO_OFFLINE / RETURN_TO_POOL), making the orphans
that _free_ghost_human_seats / _reclaim_zombie_casino_seats sweep unrepresentable.
"""

from __future__ import annotations

import pytest

from cash_mode.presence import ai_entity_id, player_entity_id
from cash_mode.presence_sweep import (
    free_human_seat_on_delete,
    sweep_presence_on_persona_delete,
)
from cash_mode.tables import CashTableState, ai_slot, human_slot, open_slot
from poker.repositories import create_repos

SB = "sweep-sb"
TID = "cash-tbl-sweep"


@pytest.fixture
def repos(tmp_path):
    return create_repos(str(tmp_path / "sweep.db"))


@pytest.fixture
def authority_on(monkeypatch):
    monkeypatch.setattr("cash_mode.economy_flags.PRESENCE_AUTHORITY_ENABLED", True)


def _save(repos, seats):
    # CashTableState requires exactly TABLE_SEAT_COUNT (6) slots.
    padded = list(seats) + [open_slot() for _ in range(6 - len(seats))]
    repos["cash_table_repo"].save_table(
        CashTableState(table_id=TID, stake_label="$10", seats=padded), sandbox_id=SB
    )


def _seated_ids(repos):
    return {s.entity_id for s in repos["entity_presence_repo"].list_for_sandbox(SB) if s.is_seated}


class TestFreeHumanSeatOnDelete:
    def test_opens_seat_and_clears_presence(self, repos, authority_on):
        # Seat a human (save_table under authority drives the SIT → SEATED row).
        _save(repos, [human_slot("guest_x", 500), open_slot(), open_slot()])
        assert player_entity_id("guest_x") in _seated_ids(repos)

        freed = free_human_seat_on_delete(owner_id="guest_x", sandbox_id=SB, repos=repos)

        assert freed == 1
        table = repos["cash_table_repo"].load_table(TID, sandbox_id=SB)
        assert table.seats[0]["kind"] == "open"
        assert player_entity_id("guest_x") not in _seated_ids(repos)

    def test_noop_when_authority_off(self, repos):
        _save(repos, [human_slot("guest_x", 500), open_slot()])
        # Authority off → sweep is inert (returns 0, leaves the seat).
        assert free_human_seat_on_delete(owner_id="guest_x", sandbox_id=SB, repos=repos) == 0

    def test_ignores_other_owners_seat(self, repos, authority_on):
        _save(repos, [human_slot("guest_x", 500), open_slot()])
        assert free_human_seat_on_delete(owner_id="someone_else", sandbox_id=SB, repos=repos) == 0
        table = repos["cash_table_repo"].load_table(TID, sandbox_id=SB)
        assert table.seats[0]["kind"] == "human"  # untouched


class TestSweepPresenceOnPersonaDelete:
    def test_opens_ai_seat_and_clears_presence(self, repos, authority_on):
        _save(repos, [ai_slot("zeus", 1000), open_slot(), open_slot()])
        assert ai_entity_id("zeus") in _seated_ids(repos)

        swept = sweep_presence_on_persona_delete(personality_id="zeus", repos=repos)

        assert swept == 1
        table = repos["cash_table_repo"].load_table(TID, sandbox_id=SB)
        assert table.seats[0]["kind"] == "open"
        assert ai_entity_id("zeus") not in _seated_ids(repos)

    def test_noop_when_authority_off(self, repos):
        _save(repos, [ai_slot("zeus", 1000), open_slot()])
        assert sweep_presence_on_persona_delete(personality_id="zeus", repos=repos) == 0

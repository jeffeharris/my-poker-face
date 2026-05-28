"""Unit tests for the burst-event aggregation in `cash_mode/lobby.py`.

Phase 0 spike + Commits 4-5 hand-event design (doc Q6 resolution):
when a single lobby refresh fires multiple sim hands at one table
(catch-up burst on long absence), the ticker would otherwise be
flooded with N copies of the same event type per table. The cap
is one event per type per table per refresh, plus a single
`burst_summary` line aggregating the rest.

These tests exercise `_emit_burst_events` directly with synthetic
`HandSimResult` lists and a stub personality_repo so the
aggregation logic is tested without requiring a real cash-mode
lobby setup.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from typing import Dict, List, Optional
from unittest.mock import MagicMock

from cash_mode.activity import (
    EVENT_ALL_IN,
    EVENT_BIG_LOSS,
    EVENT_BIG_WIN,
    EVENT_BURST_SUMMARY,
    EVENT_BUST,
    clear_events,
    recent_events,
)
from cash_mode.full_sim import (
    HAND_EVENT_ALL_IN,
    HAND_EVENT_BUST,
    HandEvent,
    HandSimResult,
)
from cash_mode.lobby import _emit_burst_events


def _make_table(table_id: str = "cash-table-10-001", stake: str = "$10", name=None):
    """A minimal stand-in for CashTableState with just the fields the
    emission code reads. Avoids dragging the full table dataclass +
    seat validation into these aggregation tests. `name` is set explicitly
    (not left to MagicMock auto-attr, which would be a truthy mock and leak
    into the feed location label)."""
    t = MagicMock()
    t.table_id = table_id
    t.stake_label = stake
    t.name = name
    return t


def _personality_repo_with(name_by_id: Dict[str, str]) -> MagicMock:
    """Stub `personality_repo.load_personality_by_id` to return a dict
    with the display name. Returns None for unknown ids."""
    repo = MagicMock()

    def _load(pid: str) -> Optional[dict]:
        if pid in name_by_id:
            return {"name": name_by_id[pid]}
        return None

    repo.load_personality_by_id.side_effect = _load
    return repo


def _hand_result(
    *,
    winner: Optional[str] = None,
    loser: Optional[str] = None,
    delta: int = 0,
    big_event: bool = False,
    hand_events: Optional[List[HandEvent]] = None,
) -> HandSimResult:
    return HandSimResult(
        new_seats=[],
        winner_pid=winner,
        loser_pid=loser,
        delta=delta,
        big_event=big_event,
        hand_events=hand_events or [],
        pot=delta,
        showdown_hands=None,
    )


class TestEmitBurstEvents(unittest.TestCase):
    def setUp(self):
        clear_events()
        self.now = datetime(2026, 5, 19, 12, 0, 0)
        self.repo = _personality_repo_with(
            {
                "p-napoleon": "Napoleon",
                "p-lincoln": "Abraham Lincoln",
                "p-buddha": "Buddha",
            }
        )
        self.table = _make_table()

    def test_empty_burst_emits_nothing(self):
        _emit_burst_events(
            table=self.table,
            sim_results=[],
            personality_repo=self.repo,
            now=self.now,
        )
        assert recent_events(limit=10) == []

    def test_single_no_big_event_emits_nothing(self):
        results = [_hand_result(winner="p-napoleon", loser="p-lincoln", delta=200)]
        _emit_burst_events(
            table=self.table,
            sim_results=results,
            personality_repo=self.repo,
            now=self.now,
        )
        # No big_event, no hand_events, single hand → nothing surfaced.
        assert recent_events(limit=10) == []

    def test_single_big_event_emits_win_loss_pair_no_summary(self):
        results = [
            _hand_result(
                winner="p-napoleon",
                loser="p-lincoln",
                delta=1200,
                big_event=True,
            ),
        ]
        _emit_burst_events(
            table=self.table,
            sim_results=results,
            personality_repo=self.repo,
            now=self.now,
        )

        types = [e.type for e in recent_events(limit=10)]
        # Big_win + big_loss pair from the headline emission.
        assert EVENT_BIG_WIN in types
        assert EVENT_BIG_LOSS in types
        # No summary — only one hand fired.
        assert EVENT_BURST_SUMMARY not in types

    def test_multi_hand_burst_emits_one_win_and_summary(self):
        """The cap is one big_win + one big_loss per table per burst,
        and a single summary regardless of how many were compressed."""
        results = [
            _hand_result(
                winner="p-napoleon",
                loser="p-lincoln",
                delta=400,
                big_event=True,
            ),
            _hand_result(
                winner="p-buddha",
                loser="p-napoleon",
                delta=1200,
                big_event=True,  # bigger → headline
            ),
            _hand_result(
                winner="p-lincoln",
                loser="p-buddha",
                delta=600,
                big_event=True,
            ),
        ]
        _emit_burst_events(
            table=self.table,
            sim_results=results,
            personality_repo=self.repo,
            now=self.now,
        )

        events = recent_events(limit=10)
        types = [e.type for e in events]

        # Exactly one big_win and one big_loss — headline is the
        # largest delta hand (Buddha winning $1200).
        assert types.count(EVENT_BIG_WIN) == 1
        assert types.count(EVENT_BIG_LOSS) == 1
        big_win_evt = next(e for e in events if e.type == EVENT_BIG_WIN)
        assert big_win_evt.name == "Buddha"

        # Summary event present because 3 hands fired.
        assert types.count(EVENT_BURST_SUMMARY) == 1

    def test_burst_hand_events_capped_to_one_per_type(self):
        """Even if every hand in a burst produces a bust event, only
        the first one surfaces — the rest are summarized."""
        bust_events_each = [
            HandEvent(type=HAND_EVENT_BUST, personality_id="p-lincoln", amount=5000),
        ]
        results = [
            _hand_result(
                winner="p-napoleon",
                loser="p-lincoln",
                delta=5000,
                big_event=True,
                hand_events=bust_events_each,
            ),
            _hand_result(
                winner="p-buddha",
                loser="p-lincoln",
                delta=4000,
                big_event=True,
                hand_events=bust_events_each,  # second bust — should be dropped
            ),
        ]
        _emit_burst_events(
            table=self.table,
            sim_results=results,
            personality_repo=self.repo,
            now=self.now,
        )

        types = [e.type for e in recent_events(limit=10)]
        # Exactly one BUST event despite two in the burst.
        assert types.count(EVENT_BUST) == 1

    def test_burst_summary_picks_top_net_leader(self):
        """The summary's `name` field should be the personality with
        the biggest cumulative net change across the burst."""
        results = [
            _hand_result(winner="p-napoleon", loser="p-lincoln", delta=300, big_event=True),
            _hand_result(winner="p-napoleon", loser="p-buddha", delta=400, big_event=True),
            _hand_result(winner="p-napoleon", loser="p-lincoln", delta=500, big_event=True),
        ]
        # Napoleon won 1200 net; Lincoln lost 800; Buddha lost 400.
        _emit_burst_events(
            table=self.table,
            sim_results=results,
            personality_repo=self.repo,
            now=self.now,
        )

        summary = next(
            (e for e in recent_events(limit=10) if e.type == EVENT_BURST_SUMMARY),
            None,
        )
        assert summary is not None
        assert summary.name == "Napoleon"

    def test_unknown_personality_falls_back_quietly(self):
        """Unknown personality_ids shouldn't crash the emission —
        the ticker is best-effort."""
        repo = _personality_repo_with({})  # no name maps
        results = [
            _hand_result(
                winner="p-mystery",
                loser="p-other",
                delta=1000,
                big_event=True,
            ),
        ]
        # Doesn't raise.
        _emit_burst_events(
            table=self.table,
            sim_results=results,
            personality_repo=repo,
            now=self.now,
        )
        # And doesn't emit (winner_name resolves to None → skipped).
        assert recent_events(limit=10) == []


class TestSingleHandSummary(unittest.TestCase):
    """The single-hand (live) path collapses a hand's beats into ONE
    composed `primary` line and demotes the atomic win/all-in/bust events
    to `primary=False` (kept for per-AI filtering, hidden from the ticker)."""

    def setUp(self):
        clear_events()
        self.now = datetime(2026, 5, 28, 12, 0, 0)
        self.repo = _personality_repo_with(
            {"p-scrooge": "Scrooge", "p-r2": "R2-D2", "p-c3": "C-3PO"}
        )
        self.table = _make_table()

    def _emit(self, r: HandSimResult):
        _emit_burst_events(
            table=self.table,
            sim_results=[r],
            personality_repo=self.repo,
            now=self.now,
        )
        return recent_events(limit=10)

    def test_exactly_one_primary_row_per_hand(self):
        evs = self._emit(
            _hand_result(winner="p-scrooge", loser="p-r2", delta=1200, big_event=True)
        )
        primary = [e for e in evs if e.primary]
        assert len(primary) == 1
        # Atomic pair still recorded for filtering, just hidden.
        assert {e.type for e in evs if not e.primary} == {EVENT_BIG_WIN, EVENT_BIG_LOSS}

    def test_shove_and_bust_fold_into_one_sentence(self):
        evs = self._emit(
            _hand_result(
                winner="p-scrooge",
                loser="p-r2",
                delta=1200,
                big_event=True,
                hand_events=[
                    HandEvent(type=HAND_EVENT_ALL_IN, personality_id="p-scrooge", amount=1200),
                    HandEvent(type=HAND_EVENT_BUST, personality_id="p-r2", amount=600),
                ],
            )
        )
        primary = [e for e in evs if e.primary]
        assert len(primary) == 1
        msg = primary[0].message
        assert "shoved all-in" in msg and "busting R2-D2" in msg
        # Reuses the all_in type so the ticker picks the shove icon.
        assert primary[0].type == EVENT_ALL_IN

    def test_multiway_busts_listed_together(self):
        evs = self._emit(
            _hand_result(
                winner="p-scrooge",
                loser="p-r2",
                delta=3000,
                big_event=True,
                hand_events=[
                    HandEvent(type=HAND_EVENT_BUST, personality_id="p-r2", amount=1500),
                    HandEvent(type=HAND_EVENT_BUST, personality_id="p-c3", amount=1500),
                ],
            )
        )
        primary = [e for e in evs if e.primary]
        assert len(primary) == 1
        assert "R2-D2 and C-3PO" in primary[0].message

    def test_named_table_shows_familiar_name_with_bracketed_stake(self):
        # A named table surfaces its familiar lobby name with the stake in
        # brackets so players know where to find it.
        self.table = _make_table(stake="$50", name="The Lodge")
        evs = self._emit(
            _hand_result(winner="p-scrooge", loser="p-r2", delta=1200, big_event=True)
        )
        msg = next(e for e in evs if e.primary).message
        assert "The Lodge [$50]" in msg
        # The bare stake stays on the structured field for filtering/grouping.
        assert all(e.stake_label == "$50" for e in evs)

    def test_unnamed_table_falls_back_to_stake_phrase(self):
        self.table = _make_table(stake="$50", name=None)
        evs = self._emit(
            _hand_result(winner="p-scrooge", loser="p-r2", delta=1200, big_event=True)
        )
        msg = next(e for e in evs if e.primary).message
        assert "the $50 table" in msg

    def test_bust_only_hand_headlines_the_bust(self):
        # Small pot (no big_event) where someone still busts.
        evs = self._emit(
            _hand_result(
                winner="p-scrooge",
                loser="p-r2",
                delta=120,
                big_event=False,
                hand_events=[
                    HandEvent(type=HAND_EVENT_BUST, personality_id="p-r2", amount=120),
                ],
            )
        )
        primary = [e for e in evs if e.primary]
        assert len(primary) == 1
        assert primary[0].type == EVENT_BUST
        assert "busted out" in primary[0].message


class TestDealerRotationInBurst(unittest.TestCase):
    """The lobby walks the dealer button through the seated AIs in
    real engine order across a burst — one rotation per sim hand.
    This matters for seat-choice UX (UTG vs CO vs BTN positioning
    depends on the dealer location, not a cosmetic counter).

    Storage is `CashTableState.dealer_idx` (schema v96+), persisted
    via the `cash_tables.dealer_idx` column — see migration v96.
    """

    def test_button_walks_one_seat_per_burst_hand(self):
        from cash_mode.lobby import _next_occupied_seat
        from cash_mode.tables import CashTableState, ai_slot, open_slot

        # Build a 4-AI table; track the button across 6 hands of burst.
        table = CashTableState(
            table_id="cash-table-2-001",
            stake_label="$2",
            seats=[ai_slot(f"pid-{i}", 5_000) for i in range(4)] + [open_slot(), open_slot()],
        )

        # Simulate the lobby's per-hand rotation by hand. We don't
        # need to run actual play_one_hand here — the unit under
        # test is "lobby advances the dealer in real engine order"
        # which is fully captured by _next_occupied_seat plus the
        # table.dealer_idx mutation.
        visited: List[int] = []
        for _ in range(6):
            nxt = _next_occupied_seat(
                table.seats,
                start_after=table.dealer_idx,
            )
            assert nxt is not None
            table.dealer_idx = nxt
            visited.append(nxt)

        # Starting at dealer_idx=0 (the default for a fresh table),
        # _next_occupied_seat advances to 1 first. Six rotations:
        # 1 -> 2 -> 3 -> 0 -> 1 -> 2 (wraps clockwise, skipping
        # open seats 4 and 5).
        assert visited == [1, 2, 3, 0, 1, 2]

    def test_button_self_heals_when_dealer_seat_opens(self):
        """If an AI leaves the dealer seat between refreshes, the
        next get_dealer_index call should advance to the next
        occupied seat instead of pointing at an empty slot."""
        from cash_mode.lobby import get_dealer_index
        from cash_mode.tables import (
            CashTableState,
            ai_slot,
            open_slot,
        )

        seats = [
            ai_slot("pid-0", 5_000),
            ai_slot("pid-1", 5_000),
            ai_slot("pid-2", 5_000),
            ai_slot("pid-3", 5_000),
            open_slot(),
            open_slot(),
        ]
        # Pin the button to seat 2, then "leave" — seat 2 becomes open.
        table = CashTableState(
            table_id="cash-table-2-001",
            stake_label="$2",
            seats=seats,
            created_at=datetime(2026, 5, 19, 12, 0, 0),
            dealer_idx=2,
        )
        table.seats[2] = open_slot()

        # Read should self-heal to the next occupied seat (which is
        # seat 3 since the cached one is no longer an AI).
        idx = get_dealer_index(table)
        assert idx in {0, 1, 3}
        assert table.seats[idx]["kind"] == "ai"


class TestDealerIdxRoundTrip(unittest.TestCase):
    """Pin the schema v96 round trip: dealer_idx written via save_table
    surfaces back on the next load_table. The lobby relies on this so
    the button position survives backend restart."""

    def test_dealer_idx_persists_across_save_load(self):
        import os
        import tempfile

        from cash_mode.tables import CashTableState, ai_slot, open_slot
        from poker.repositories import create_repos

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            repos = create_repos(db_path)
            repo = repos["cash_table_repo"]

            table = CashTableState(
                table_id="cash-table-2-001",
                stake_label="$2",
                seats=[ai_slot(f"pid-{i}", 5_000) for i in range(4)] + [open_slot(), open_slot()],
                dealer_idx=3,
            )
            repo.save_table(table, sandbox_id="test-sandbox-1")

            loaded = repo.load_table("cash-table-2-001", sandbox_id="test-sandbox-1")
            assert loaded is not None
            assert loaded.dealer_idx == 3
        finally:
            try:
                os.unlink(db_path)
            except FileNotFoundError:
                pass

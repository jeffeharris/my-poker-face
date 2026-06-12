"""Unit tests for the "last stand" predator signal in `cash_mode/lobby.py`.

When a seated AI's reserve bankroll drops below what they'd need to
rebuy anywhere, their entire playable bankroll is on the table — one
busted stack from going fully broke. The lobby surfaces this as an
`EVENT_LAST_STAND` ticker row so the player can target a vulnerable
seat. These tests exercise the detection helper, the once-per-episode
dedup, and the event emission in isolation (stub repos / plain dict
seats) — no full lobby refresh required.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from typing import Dict, Optional
from unittest.mock import MagicMock

from cash_mode import lobby
from cash_mode.activity import (
    EVENT_LAST_STAND,
    clear_events,
    format_last_stand_message,
    format_player_last_stand_message,
    recent_events,
)
from cash_mode.lobby import (
    _committed_seated_ais,
    _emit_last_stand_events,
    _select_new_last_stands,
)


def _table(table_id="cash-table-50-001", stake="$50", seats=None):
    """Minimal table stand-in — the detection helper only reads
    `.seats` (a list of plain dict slots)."""
    t = MagicMock()
    t.table_id = table_id
    t.stake_label = stake
    t.seats = seats or []
    return t


def _ai(pid, chips):
    return {"kind": "ai", "personality_id": pid, "chips": chips}


def _personality_repo_with(name_by_id: Dict[str, str]) -> MagicMock:
    repo = MagicMock()

    def _load(pid: str) -> Optional[dict]:
        return {"name": name_by_id[pid]} if pid in name_by_id else None

    repo.load_personality_by_id.side_effect = _load
    return repo


class TestCommittedSeatedAis(unittest.TestCase):
    def setUp(self):
        # Strict $0: only "broke" (reserve 0) is on its last stand. "thin"
        # still has a reserve to fall back on, so busting it wouldn't crash
        # them out — not a last stand.
        self.reserve = {"broke": 0, "thin": 40, "flush": 5000}
        self.reserve_lookup = lambda pid: self.reserve.get(pid)

    def test_flags_zero_reserve_seated_ai_with_chips(self):
        table = _table(seats=[_ai("broke", 500), _ai("flush", 500)])
        out = _committed_seated_ais(table, reserve_lookup=self.reserve_lookup)
        self.assertEqual(out, {"broke": 500})

    def test_nonzero_reserve_is_not_committed(self):
        # A reserve of any size means a busted stack doesn't crash them
        # out — they'd go idle and side-hustle back. Not a last stand.
        table = _table(seats=[_ai("thin", 250)])
        out = _committed_seated_ais(table, reserve_lookup=self.reserve_lookup)
        self.assertEqual(out, {})

    def test_excludes_zero_chip_seats(self):
        # A seat with no chips isn't "on the table" — it's about to be
        # vacated; the bust/leave events cover that beat.
        table = _table(seats=[_ai("broke", 0)])
        out = _committed_seated_ais(table, reserve_lookup=self.reserve_lookup)
        self.assertEqual(out, {})

    def test_excludes_non_ai_and_unknown_reserve(self):
        table = _table(
            seats=[
                {"kind": "open"},
                {"kind": "human", "personality_id": "owner-1", "chips": 500},
                _ai("ghost", 500),  # no reserve row -> lookup returns None
            ]
        )
        out = _committed_seated_ais(
            table,
            reserve_lookup=self.reserve_lookup,  # "ghost" absent -> None
        )
        self.assertEqual(out, {})


class TestLastStandReserveIsPostDebitNotDoubleCounted(unittest.TestCase):
    """Regression: the last-stand scan must read the POST-debit bankroll, not
    re-subtract the burst's already-debited committed buy-ins.

    `_bankroll_lookup` (used DURING a burst to size reloads) returns
    `_available_buyin_capacity(current, committed)` = ``max(0, current - committed)``,
    which is correct while the buy-ins are planned-but-not-yet-debited. After
    `_apply_bankroll_transfers` debits those buy-ins, the DB balance is already
    ``pre_debit - committed``. Reusing `_bankroll_lookup` for the last-stand scan
    subtracts ``committed`` a SECOND time, so a solvent AI reads as $0 reserve and
    trips a false predator signal. `refresh_unseated_tables` therefore hands the
    scan `_seated_reserve_lookup` (the raw post-debit balance) instead.
    """

    def test_double_subtracting_committed_buyins_is_a_false_last_stand(self):
        pre_debit_reserve = 1000
        rebuy = 500  # committed during the burst, then debited from the DB
        post_debit_db = pre_debit_reserve - rebuy  # 500 — what's really left off-table
        committed = {"reloader": rebuy}

        table = _table(seats=[_ai("reloader", 500)])

        # The DURING-burst lookup, wrongly reused after the debit has landed:
        # max(0, 500 - 500) = 0 -> the solvent AI is falsely flagged.
        buggy_lookup = lambda pid: lobby._available_buyin_capacity(
            post_debit_db, committed.get(pid, 0)
        )
        self.assertEqual(
            _committed_seated_ais(table, reserve_lookup=buggy_lookup),
            {"reloader": 500},
            "guard tripwire: the double-counting lookup should produce the "
            "false positive this fix removes",
        )

        # The fix (`_seated_reserve_lookup`): read the post-debit balance
        # directly, no second subtraction -> the real $500 reserve is seen and
        # the AI is correctly NOT a last stand.
        fixed_lookup = lambda pid: post_debit_db
        self.assertEqual(
            _committed_seated_ais(table, reserve_lookup=fixed_lookup),
            {},
        )


class TestSelectNewLastStands(unittest.TestCase):
    def setUp(self):
        lobby._last_stand_announced.clear()
        self.addCleanup(lobby._last_stand_announced.clear)

    def test_fires_once_then_suppresses_steady_state(self):
        first = _select_new_last_stands("sbx", {"a", "b"})
        self.assertEqual(first, {"a", "b"})
        # Same set next refresh -> nothing new.
        second = _select_new_last_stands("sbx", {"a", "b"})
        self.assertEqual(second, set())

    def test_recovery_then_re_entry_re_triggers(self):
        _select_new_last_stands("sbx", {"a"})
        # "a" recovered (or left) -> drops from the announced set.
        self.assertEqual(_select_new_last_stands("sbx", set()), set())
        # ...and re-entering the committed state fires again.
        self.assertEqual(_select_new_last_stands("sbx", {"a"}), {"a"})

    def test_sandboxes_are_isolated(self):
        self.assertEqual(_select_new_last_stands("sbx1", {"a"}), {"a"})
        # A different sandbox hasn't seen "a" yet -> it fires there too.
        self.assertEqual(_select_new_last_stands("sbx2", {"a"}), {"a"})


class TestEmitLastStandEvents(unittest.TestCase):
    def setUp(self):
        clear_events()
        self.now = datetime(2026, 5, 25, 12, 0, 0)
        self.repo = _personality_repo_with({"p-napoleon": "Napoleon"})

    def test_records_event_with_predator_message(self):
        _emit_last_stand_events(
            candidates={"p-napoleon": ("cash-table-50-001", "$50", "The Lodge")},
            personality_repo=self.repo,
            now=self.now,
            sandbox_id="sbx",
        )
        events = recent_events(sandbox_id="sbx")
        self.assertEqual(len(events), 1)
        evt = events[0]
        self.assertEqual(evt.type, EVENT_LAST_STAND)
        self.assertEqual(evt.personality_id, "p-napoleon")
        self.assertEqual(evt.stake_label, "$50")
        self.assertEqual(evt.reason, "")
        self.assertIn("Napoleon", evt.message)
        self.assertIn("$50", evt.message)
        # Familiar table name surfaces with the stake in brackets.
        self.assertIn("The Lodge [$50]", evt.message)

    def test_unknown_personality_is_skipped(self):
        _emit_last_stand_events(
            candidates={"p-ghost": ("cash-table-50-001", "$50", "The Lodge")},
            personality_repo=self.repo,
            now=self.now,
            sandbox_id="sbx",
        )
        self.assertEqual(recent_events(sandbox_id="sbx"), [])


class TestFormatters(unittest.TestCase):
    def test_ai_message_names_seat_and_stake(self):
        msg = format_last_stand_message("Bezos", "$200")
        self.assertIn("Bezos", msg)
        self.assertIn("$200", msg)

    def test_player_message_is_second_person(self):
        msg = format_player_last_stand_message("$10")
        self.assertIn("Your", msg)
        self.assertIn("$10", msg)


if __name__ == "__main__":
    unittest.main()

"""Unit tests for `flask_app.services.holdings_view` — net-worth view.

Covers the per-entity net-worth snapshot (chips + stakes receivable −
stakes outstanding, plus vice / side-hustle), the scoped-vs-unscoped
gating, the per-entity ledger aggregation, and the snapshot-backed
history (grouping, ranking, window auto-fit, requires-sandbox).
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from cash_mode.bankroll import AIBankrollState, PlayerBankrollState
from flask_app.services.holdings_view import (
    _aggregate_ledger_by_entity,
    _net_worth_for,
    compute_holdings_history,
    compute_holdings_snapshot,
    record_holdings_snapshot,
)
from poker.repositories import create_repos

SANDBOX = 'sb-test-0001'
OTHER_SANDBOX = 'sb-other-0002'


def _insert_ledger(
    db_path, *, source, sink, amount, reason, sandbox_id, created_at='2026-05-25 12:00:00'
):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO chip_ledger_entries
                (created_at, source, sink, amount, reason, sandbox_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (created_at, source, sink, amount, reason, sandbox_id),
        )


def _insert_stake(
    db_path,
    *,
    stake_id,
    staker_id,
    borrower_id,
    status,
    principal=0,
    match_amount=0,
    carry_amount=0,
    staker_kind='personality',
    borrower_kind='personality',
):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO stakes (
                stake_id, session_id, staker_id, staker_kind,
                borrower_id, borrower_kind, format, principal, match_amount,
                origination_fee, cut, status, carry_amount, stake_tier,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pure', ?, ?, 0, 0.5, ?, ?, '$10', ?)
            """,
            (
                stake_id,
                f'sess_{stake_id}',
                staker_id,
                staker_kind,
                borrower_id,
                borrower_kind,
                principal,
                match_amount,
                status,
                carry_amount,
                '2026-05-25 10:00:00',
            ),
        )


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        self.repos = create_repos(self.db_path)
        self.bankroll_repo = self.repos['bankroll_repo']
        self.personality_repo = self.repos['personality_repo']
        self.user_repo = self.repos['user_repo']
        self.stake_repo = self.repos['stake_repo']
        self.snaps = self.repos['holdings_snapshots_repo']

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    def _seed_ai(self, pid, chips, sandbox_id=SANDBOX):
        # last_regen_tick=None → projection returns stored chips verbatim,
        # so net-worth math is deterministic (no regen drift in the test).
        self.bankroll_repo.save_ai_bankroll(
            AIBankrollState(personality_id=pid, chips=chips, last_regen_tick=None),
            sandbox_id=sandbox_id,
        )


class TestNetWorthFor(unittest.TestCase):
    """The pure column-block builder — dual-key lookup + arithmetic."""

    def test_composes_net_worth_and_dual_keys(self):
        out = _net_worth_for(
            'ai:x',
            'x',
            1000,
            receivables={'x': 200},  # keyed by bare id
            outstanding={'x': 50},  # keyed by bare id
            vice={'ai:x': 30},  # keyed by ledger entity_id
            side_hustle={'ai:x': 10},
        )
        self.assertEqual(out['receivable'], 200)
        self.assertEqual(out['outstanding'], 50)
        self.assertEqual(out['net_worth'], 1000 + 200 - 50)
        self.assertEqual(out['vice_spent'], 30)
        self.assertEqual(out['side_hustle_earned'], 10)

    def test_missing_keys_default_zero(self):
        out = _net_worth_for(
            'player:p',
            'p',
            500,
            receivables={},
            outstanding={},
            vice={},
            side_hustle={},
        )
        self.assertEqual(out['net_worth'], 500)
        self.assertEqual(out['receivable'], 0)
        self.assertEqual(out['vice_spent'], 0)


class TestAggregateLedgerByEntity(_Base):
    def test_groups_by_side_and_scopes_to_sandbox(self):
        # Vice: entity is the source (paid the bank).
        _insert_ledger(
            self.db_path,
            source='ai:scrooge',
            sink='central_bank',
            amount=300,
            reason='vice_spending',
            sandbox_id=SANDBOX,
        )
        _insert_ledger(
            self.db_path,
            source='ai:scrooge',
            sink='central_bank',
            amount=200,
            reason='vice_spending',
            sandbox_id=SANDBOX,
        )
        # Different sandbox — must be excluded.
        _insert_ledger(
            self.db_path,
            source='ai:scrooge',
            sink='central_bank',
            amount=999,
            reason='vice_spending',
            sandbox_id=OTHER_SANDBOX,
        )
        # Side hustle: entity is the sink (received from the bank).
        _insert_ledger(
            self.db_path,
            source='central_bank',
            sink='ai:bob',
            amount=120,
            reason='side_hustle_earning',
            sandbox_id=SANDBOX,
        )

        vice = _aggregate_ledger_by_entity(self.db_path, 'vice_spending', 'source', SANDBOX)
        self.assertEqual(vice, {'ai:scrooge': 500})

        hustle = _aggregate_ledger_by_entity(self.db_path, 'side_hustle_earning', 'sink', SANDBOX)
        self.assertEqual(hustle, {'ai:bob': 120})


class TestStakeAggregates(_Base):
    def test_receivable_sums_active_and_carry_excludes_house(self):
        # A stakes B: active principal+match, plus a carry receivable.
        _insert_stake(
            self.db_path,
            stake_id='s1',
            staker_id='A',
            borrower_id='B',
            status='active',
            principal=100,
            match_amount=20,
        )
        _insert_stake(
            self.db_path,
            stake_id='s2',
            staker_id='A',
            borrower_id='B',
            status='carry',
            carry_amount=50,
        )
        # House stake (staker_id NULL) — must not surface as a receivable.
        _insert_stake(
            self.db_path,
            stake_id='s3',
            staker_id=None,
            borrower_id='C',
            status='active',
            principal=999,
            staker_kind='house',
        )

        recv = self.stake_repo.aggregate_receivables_by_staker()
        self.assertEqual(recv.get('A'), 170)
        self.assertNotIn(None, recv)

        owed = self.stake_repo.aggregate_outstanding_by_borrower()
        self.assertEqual(owed.get('B'), 50)  # only carry rows are debt
        self.assertNotIn('C', owed)  # active is the staker's claim


class TestComputeHoldingsSnapshot(_Base):
    def test_scoped_has_net_worth_block(self):
        self._seed_ai('don_quixote', 1000)
        _insert_stake(
            self.db_path,
            stake_id='s1',
            staker_id='don_quixote',
            borrower_id='someone',
            status='active',
            principal=200,
        )
        _insert_stake(
            self.db_path,
            stake_id='s2',
            staker_id='lender',
            borrower_id='don_quixote',
            status='carry',
            carry_amount=80,
        )
        _insert_ledger(
            self.db_path,
            source='ai:don_quixote',
            sink='central_bank',
            amount=40,
            reason='vice_spending',
            sandbox_id=SANDBOX,
        )

        snap = compute_holdings_snapshot(
            bankroll_repo=self.bankroll_repo,
            personality_repo=self.personality_repo,
            user_repo=self.user_repo,
            stake_repo=self.stake_repo,
            db_path=self.db_path,
            sandbox_id=SANDBOX,
        )
        self.assertTrue(snap['net_worth_scoped'])
        row = next(r for r in snap['rows'] if r['id'] == 'don_quixote')
        self.assertEqual(row['projected_chips'], 1000)
        self.assertEqual(row['receivable'], 200)
        self.assertEqual(row['outstanding'], 80)
        self.assertEqual(row['net_worth'], 1000 + 200 - 80)
        self.assertEqual(row['vice_spent'], 40)

    def test_unscoped_is_chips_only(self):
        self._seed_ai('don_quixote', 1000)
        snap = compute_holdings_snapshot(
            bankroll_repo=self.bankroll_repo,
            personality_repo=self.personality_repo,
            user_repo=self.user_repo,
            stake_repo=self.stake_repo,
            db_path=self.db_path,
            sandbox_id=None,
        )
        self.assertFalse(snap['net_worth_scoped'])
        row = snap['rows'][0]
        self.assertNotIn('net_worth', row)
        self.assertIn('projected_chips', row)

    def test_player_row_gets_net_worth_when_scoped(self):
        self.bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id='guest_jeff',
                chips=890,
                starting_bankroll=200,
            )
        )
        _insert_stake(
            self.db_path,
            stake_id='s1',
            staker_id='guest_jeff',
            borrower_id='ai_friend',
            status='carry',
            carry_amount=300,
            staker_kind='human',
        )
        snap = compute_holdings_snapshot(
            bankroll_repo=self.bankroll_repo,
            personality_repo=self.personality_repo,
            user_repo=self.user_repo,
            stake_repo=self.stake_repo,
            db_path=self.db_path,
            sandbox_id=SANDBOX,
        )
        row = next(r for r in snap['rows'] if r['id'] == 'guest_jeff')
        self.assertEqual(row['kind'], 'player')
        self.assertEqual(row['net_worth'], 890 + 300)


class _StubSnaps:
    """Minimal snapshots_repo stand-in for history-shaping tests."""

    def __init__(self, points):
        self._points = points

    def series_since(self, since_iso, *, sandbox_id):
        return [p for p in self._points if p['captured_at'] >= since_iso]


class TestComputeHoldingsHistory(unittest.TestCase):
    def test_requires_sandbox_when_none(self):
        out = compute_holdings_history(
            snapshots_repo=_StubSnaps([]),
            personality_repo=None,
            user_repo=None,
            days=30,
            sandbox_id=None,
        )
        self.assertTrue(out['requires_sandbox'])
        self.assertEqual(out['series'], [])

    def test_groups_ranks_and_autofits(self):
        now = datetime(2026, 5, 25, 12, 0, 0)
        t0 = (now - timedelta(hours=2)).isoformat() + 'Z'
        t1 = (now - timedelta(hours=1)).isoformat() + 'Z'
        points = [
            {
                'entity_id': 'ai:rich',
                'kind': 'ai',
                'captured_at': t0,
                'net_worth': 500,
                'chips': 500,
                'receivable': 0,
                'outstanding': 0,
            },
            {
                'entity_id': 'ai:rich',
                'kind': 'ai',
                'captured_at': t1,
                'net_worth': 900,
                'chips': 900,
                'receivable': 0,
                'outstanding': 0,
            },
            {
                'entity_id': 'ai:poor',
                'kind': 'ai',
                'captured_at': t1,
                'net_worth': 100,
                'chips': 100,
                'receivable': 0,
                'outstanding': 0,
            },
        ]
        out = compute_holdings_history(
            snapshots_repo=_StubSnaps(points),
            personality_repo=None,
            user_repo=None,
            days=30,
            now=now,
            sandbox_id=SANDBOX,
        )
        self.assertFalse(out['requires_sandbox'])
        self.assertEqual(out['series_total'], 2)
        # Ranked by current (latest) net worth descending.
        self.assertEqual([s['entity_id'] for s in out['series']], ['ai:rich', 'ai:poor'])
        self.assertEqual(out['series'][0]['current_net_worth'], 900)
        # Auto-fit: x-domain starts at the earliest recorded point, not the
        # 30-day-ago window edge.
        self.assertEqual(out['since'], t0)


class TestRecordSnapshotRoundTrip(_Base):
    def test_record_then_history(self):
        self._seed_ai('hero', 1000)
        _insert_stake(
            self.db_path,
            stake_id='s1',
            staker_id='hero',
            borrower_id='other',
            status='active',
            principal=200,
        )

        written = record_holdings_snapshot(
            snapshots_repo=self.snaps,
            bankroll_repo=self.bankroll_repo,
            personality_repo=self.personality_repo,
            user_repo=self.user_repo,
            stake_repo=self.stake_repo,
            db_path=self.db_path,
            sandbox_id=SANDBOX,
        )
        self.assertGreaterEqual(written, 1)

        out = compute_holdings_history(
            snapshots_repo=self.snaps,
            personality_repo=self.personality_repo,
            user_repo=self.user_repo,
            days=30,
            sandbox_id=SANDBOX,
        )
        hero = next(s for s in out['series'] if s['entity_id'] == 'ai:hero')
        self.assertEqual(hero['current_net_worth'], 1200)  # 1000 chips + 200 recv


if __name__ == '__main__':
    unittest.main()

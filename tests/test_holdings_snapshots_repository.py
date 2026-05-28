"""Unit tests for `HoldingsSnapshotsRepository` (schema v116).

Covers record / series_since ordering + grouping inputs, per-sandbox
isolation, the window's lexical `captured_at >= since` filter,
`latest_captured_at`, and retention `prune`.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from poker.repositories import create_repos

SB = 'sb-1'
OTHER = 'sb-2'


def _row(entity_id, net_worth, chips, *, kind='ai', receivable=0, outstanding=0, sandbox_id=SB):
    return {
        'sandbox_id': sandbox_id,
        'entity_id': entity_id,
        'kind': kind,
        'net_worth': net_worth,
        'chips': chips,
        'receivable': receivable,
        'outstanding': outstanding,
    }


class TestHoldingsSnapshotsRepository(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        self.repo = create_repos(self.tmp.name)['holdings_snapshots_repo']

    def tearDown(self):
        try:
            os.unlink(self.tmp.name)
        except FileNotFoundError:
            pass

    def test_record_returns_count_and_empty_is_noop(self):
        self.assertEqual(self.repo.record([], captured_at='2026-05-25T12:00:00Z'), 0)
        n = self.repo.record(
            [_row('ai:a', 100, 100), _row('ai:b', 50, 50)],
            captured_at='2026-05-25T12:00:00Z',
        )
        self.assertEqual(n, 2)

    def test_series_since_orders_and_filters_window(self):
        self.repo.record([_row('ai:a', 100, 100)], captured_at='2026-05-25T10:00:00Z')
        self.repo.record([_row('ai:a', 200, 200)], captured_at='2026-05-25T11:00:00Z')
        self.repo.record([_row('ai:a', 300, 300)], captured_at='2026-05-25T12:00:00Z')

        # Window cuts off the first point.
        rows = self.repo.series_since('2026-05-25T10:30:00Z', sandbox_id=SB)
        self.assertEqual([r['net_worth'] for r in rows], [200, 300])
        # Ordered ascending by captured_at within the entity.
        self.assertEqual(rows[0]['captured_at'], '2026-05-25T11:00:00Z')

    def test_series_since_isolates_sandbox(self):
        self.repo.record(
            [_row('ai:a', 100, 100, sandbox_id=SB)], captured_at='2026-05-25T12:00:00Z'
        )
        self.repo.record(
            [_row('ai:z', 999, 999, sandbox_id=OTHER)], captured_at='2026-05-25T12:00:00Z'
        )
        rows = self.repo.series_since('2026-05-25T00:00:00Z', sandbox_id=SB)
        self.assertEqual([r['entity_id'] for r in rows], ['ai:a'])

    def test_latest_captured_at(self):
        self.assertIsNone(self.repo.latest_captured_at(SB))
        self.repo.record([_row('ai:a', 1, 1)], captured_at='2026-05-25T10:00:00Z')
        self.repo.record([_row('ai:a', 2, 2)], captured_at='2026-05-25T12:00:00Z')
        self.assertEqual(self.repo.latest_captured_at(SB), '2026-05-25T12:00:00Z')

    def test_series_for_entity_filters_entity_and_returns_chips_and_net_worth(self):
        # Two entities, three captures; chips diverge from net_worth so we
        # prove the focused read returns both columns for the right entity.
        self.repo.record(
            [_row('player:p1', 500, 700), _row('ai:a', 100, 100)],
            captured_at='2026-05-25T10:00:00Z',
        )
        self.repo.record(
            [_row('player:p1', 600, 650), _row('ai:a', 100, 100)],
            captured_at='2026-05-25T11:00:00Z',
        )
        self.repo.record(
            [_row('player:p1', 400, 900)],
            captured_at='2026-05-25T12:00:00Z',
        )

        pts = self.repo.series_for_entity(
            sandbox_id=SB,
            entity_id='player:p1',
            since_iso='2026-05-25T00:00:00Z',
        )
        # Only p1's points, oldest → newest, carrying chips + net_worth.
        self.assertEqual([p['chips'] for p in pts], [700, 650, 900])
        self.assertEqual([p['net_worth'] for p in pts], [500, 600, 400])
        self.assertEqual(pts[0]['captured_at'], '2026-05-25T10:00:00Z')

    def test_series_for_entity_window_and_sandbox_isolation(self):
        self.repo.record(
            [_row('player:p1', 1, 100, sandbox_id=SB)],
            captured_at='2026-05-25T10:00:00Z',
        )
        self.repo.record(
            [_row('player:p1', 2, 200, sandbox_id=SB)],
            captured_at='2026-05-25T12:00:00Z',
        )
        # Same entity id in another sandbox must not bleed in.
        self.repo.record(
            [_row('player:p1', 9, 999, sandbox_id=OTHER)],
            captured_at='2026-05-25T12:00:00Z',
        )
        pts = self.repo.series_for_entity(
            sandbox_id=SB,
            entity_id='player:p1',
            since_iso='2026-05-25T11:00:00Z',
        )
        self.assertEqual([(p['chips'], p['net_worth']) for p in pts], [(200, 2)])

    def test_series_for_entity_empty_when_no_points(self):
        self.assertEqual(
            self.repo.series_for_entity(
                sandbox_id=SB, entity_id='player:nobody', since_iso='2026-01-01T00:00:00Z'
            ),
            [],
        )

    def test_prune_deletes_old_rows(self):
        self.repo.record([_row('ai:a', 1, 1)], captured_at='2026-04-01T00:00:00Z')
        self.repo.record([_row('ai:a', 2, 2)], captured_at='2026-05-25T00:00:00Z')
        deleted = self.repo.prune('2026-05-01T00:00:00Z')
        self.assertEqual(deleted, 1)
        remaining = self.repo.series_since('2026-01-01T00:00:00Z', sandbox_id=SB)
        self.assertEqual([r['net_worth'] for r in remaining], [2])


if __name__ == '__main__':
    unittest.main()

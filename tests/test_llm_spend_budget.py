#!/usr/bin/env python3
"""Unit tests for the PRH-2 LLM spend reader (UsageTracker.get_recent_spend).

This is the *read* side of the global/per-owner spend kill-switch. The gate that
compares this total against the configured ceiling lands in a follow-up step;
here we only validate that the reader sums the right rows, respects the rolling
window and the per-owner filter, caches with a short TTL, and fails open.
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from core.llm import tracking
from core.llm.tracking import UsageTracker


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class TestRecentSpendReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        self._create_api_usage_table(self.tmp.name)
        self.tracker = UsageTracker(db_path=self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    @staticmethod
    def _create_api_usage_table(db_path: str) -> None:
        """Minimal api_usage table — only the columns the reader touches."""
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE api_usage (
                    id INTEGER PRIMARY KEY,
                    created_at TIMESTAMP,
                    owner_id TEXT,
                    call_type TEXT,
                    provider TEXT,
                    model TEXT,
                    status TEXT,
                    estimated_cost REAL
                )
            """)

    def _insert(self, created_at: str, owner_id, cost):
        with sqlite3.connect(self.tmp.name) as conn:
            conn.execute(
                "INSERT INTO api_usage (created_at, owner_id, call_type, provider, "
                "model, status, estimated_cost) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (created_at, owner_id, 'player_decision', 'openai', 'gpt-5-nano', 'ok', cost),
            )

    # ------------------------------------------------------------------
    def test_global_sum_over_window(self):
        now = datetime.now(timezone.utc)
        self._insert(_iso(now), 'alice', 0.10)
        self._insert(_iso(now), 'bob', 0.25)
        self._insert(_iso(now), 'alice', 0.05)

        self.assertAlmostEqual(self.tracker.get_recent_spend(), 0.40, places=6)

    def test_per_owner_filter(self):
        now = datetime.now(timezone.utc)
        self._insert(_iso(now), 'alice', 0.10)
        self._insert(_iso(now), 'bob', 0.25)
        self._insert(_iso(now), 'alice', 0.05)

        self.assertAlmostEqual(self.tracker.get_recent_spend(owner_id='alice'), 0.15, places=6)
        self.assertAlmostEqual(self.tracker.get_recent_spend(owner_id='bob'), 0.25, places=6)
        self.assertEqual(self.tracker.get_recent_spend(owner_id='nobody'), 0.0)

    def test_rows_outside_window_excluded(self):
        now = datetime.now(timezone.utc)
        self._insert(_iso(now), 'alice', 1.00)  # recent
        self._insert(_iso(now - timedelta(hours=48)), 'alice', 9.99)  # stale

        self.assertAlmostEqual(self.tracker.get_recent_spend(), 1.00, places=6)
        # Widening the window picks the stale row back up.
        self.assertAlmostEqual(self.tracker.get_recent_spend(window_hours=72), 10.99, places=6)

    def test_null_cost_counts_as_zero(self):
        now = datetime.now(timezone.utc)
        self._insert(_iso(now), 'alice', 0.10)
        self._insert(_iso(now), 'alice', None)  # missing pricing row

        self.assertAlmostEqual(self.tracker.get_recent_spend(), 0.10, places=6)

    def test_empty_table_is_zero(self):
        self.assertEqual(self.tracker.get_recent_spend(), 0.0)

    # ------------------------------------------------------------------
    # Caching
    # ------------------------------------------------------------------
    def test_cache_holds_within_ttl(self):
        now = datetime.now(timezone.utc)
        self._insert(_iso(now), 'alice', 0.10)

        first = self.tracker.get_recent_spend()
        self.assertAlmostEqual(first, 0.10, places=6)

        # Add spend behind the cache's back — a read within the TTL must not see it.
        self._insert(_iso(now), 'alice', 5.00)
        self.assertAlmostEqual(self.tracker.get_recent_spend(), 0.10, places=6)

        # Explicit invalidation forces a recompute.
        self.tracker.invalidate_spend_cache()
        self.assertAlmostEqual(self.tracker.get_recent_spend(), 5.10, places=6)

    def test_cache_recomputes_after_ttl(self):
        now = datetime.now(timezone.utc)
        self._insert(_iso(now), 'alice', 0.10)
        self.assertAlmostEqual(self.tracker.get_recent_spend(), 0.10, places=6)

        self._insert(_iso(now), 'alice', 2.00)
        # Force the TTL window to zero so the cached entry is always considered stale.
        with patch.object(tracking, 'SPEND_CACHE_TTL', 0):
            self.assertAlmostEqual(self.tracker.get_recent_spend(), 2.10, places=6)

    def test_global_and_owner_caches_are_independent(self):
        now = datetime.now(timezone.utc)
        self._insert(_iso(now), 'alice', 0.10)
        self._insert(_iso(now), 'bob', 0.20)

        self.assertAlmostEqual(self.tracker.get_recent_spend(), 0.30, places=6)
        self.assertAlmostEqual(self.tracker.get_recent_spend(owner_id='alice'), 0.10, places=6)

    # ------------------------------------------------------------------
    # Fail-open
    # ------------------------------------------------------------------
    def test_fails_open_on_db_error(self):
        # A path with no api_usage table → OperationalError → must return 0.0,
        # never raise, so a DB hiccup can't freeze the game.
        missing = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        missing.close()
        try:
            tracker = UsageTracker(db_path=missing.name)
            self.assertEqual(tracker.get_recent_spend(), 0.0)
            self.assertEqual(tracker.get_recent_spend(owner_id='alice'), 0.0)
        finally:
            os.unlink(missing.name)


if __name__ == '__main__':
    unittest.main()

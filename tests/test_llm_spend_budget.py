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
from unittest.mock import MagicMock, patch

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

    # ------------------------------------------------------------------
    # Eager spend-cache bump (keeps the budget gate from lagging the TTL)
    # ------------------------------------------------------------------
    def test_bump_reflects_in_cached_read(self):
        self.assertEqual(self.tracker.get_recent_spend(), 0.0)  # warm (None, 24)
        self.tracker._bump_spend_cache(None, 0.50)
        self.assertAlmostEqual(self.tracker.get_recent_spend(), 0.50, places=6)

    def test_bump_targets_global_and_matching_owner_only(self):
        self.tracker.get_recent_spend()  # warm global
        self.tracker.get_recent_spend(owner_id='alice')
        self.tracker.get_recent_spend(owner_id='bob')

        self.tracker._bump_spend_cache('alice', 1.00)

        self.assertAlmostEqual(self.tracker.get_recent_spend(), 1.00, places=6)  # global bumped
        self.assertAlmostEqual(self.tracker.get_recent_spend(owner_id='alice'), 1.00, places=6)
        self.assertEqual(self.tracker.get_recent_spend(owner_id='bob'), 0.0)  # untouched

    def test_bump_ignores_none_or_nonpositive_cost(self):
        self.tracker.get_recent_spend()
        self.tracker._bump_spend_cache(None, None)
        self.tracker._bump_spend_cache(None, 0.0)
        self.tracker._bump_spend_cache(None, -5.0)
        self.assertEqual(self.tracker.get_recent_spend(), 0.0)

    def test_bump_is_superseded_by_ttl_recompute(self):
        # The bump is an eager correction between recomputes — once the TTL
        # elapses the cache recomputes from the DB (no rows here) and discards it.
        self.tracker.get_recent_spend()
        self.tracker._bump_spend_cache(None, 9.99)
        self.assertAlmostEqual(self.tracker.get_recent_spend(), 9.99, places=6)
        with patch.object(tracking, 'SPEND_CACHE_TTL', 0):
            self.assertEqual(self.tracker.get_recent_spend(), 0.0)

    def test_record_bumps_cache_without_reread(self):
        # record() folds the inserted cost into the warm cache so the gate sees
        # it immediately, rather than lagging behind by up to SPEND_CACHE_TTL.
        self.tracker.get_recent_spend(owner_id='alice')  # warm (alice, 24)
        self.tracker.get_recent_spend()  # warm (None, 24) at 0
        with (
            patch.object(self.tracker, '_insert_usage', return_value=0.40),
            patch.object(self.tracker, '_log_stats'),
        ):
            self.tracker.record(MagicMock(), owner_id='alice')
        self.assertAlmostEqual(self.tracker.get_recent_spend(), 0.40, places=6)
        self.assertAlmostEqual(self.tracker.get_recent_spend(owner_id='alice'), 0.40, places=6)

    # ------------------------------------------------------------------
    # Round-trip — locks the created_at format contract between writer & reader
    # ------------------------------------------------------------------
    def test_record_to_get_recent_spend_round_trip(self):
        """Writing via UsageTracker.record (real created_at format) must be
        readable by get_recent_spend (lexicographic created_at >= cutoff).

        Uses the *real* migrated api_usage schema (not the minimal test table)
        so this exercises the production write path end-to-end. If a future
        change drifts record()'s timestamp format, the seed-rows-directly tests
        above would still pass but real spend would no longer be summed — this
        belt-and-suspenders catches that contract break.
        """
        from poker.repositories import create_repos

        real_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        real_db.close()
        repos = create_repos(real_db.name)  # runs migrations → real api_usage schema
        try:
            tracker = UsageTracker(db_path=real_db.name)

            fake_response = MagicMock(spec=tracking.LLMResponse)
            fake_response.provider = 'openai'
            fake_response.model = 'gpt-5-nano'
            fake_response.status = 'ok'
            fake_response.latency_ms = 12
            fake_response.input_tokens = 10
            fake_response.output_tokens = 5
            fake_response.cached_tokens = 0
            fake_response.reasoning_tokens = 0
            fake_response.reasoning_effort = None
            fake_response.max_tokens = None
            fake_response.finish_reason = 'stop'
            fake_response.error_code = None
            fake_response.error_message = None
            fake_response.request_id = None
            fake_response.image_count = 0
            fake_response.size = None

            # Patch cost calc so we don't need pricing rows; record() writes the
            # row (real created_at) and bumps the cache.
            with (
                patch.object(
                    tracker,
                    '_calculate_cost',
                    return_value=tracking.UsageTracker.CostResult(cost=0.25, pricing_ids={}),
                ),
                patch.object(tracker, '_log_stats'),
            ):
                tracker.record(
                    fake_response, call_type=tracking.CallType.PLAYER_DECISION, owner_id='alice'
                )

            # Force a fresh DB read (bypass the record() cache bump) to prove the
            # persisted created_at is within the reader's lexicographic window.
            tracker.invalidate_spend_cache()
            self.assertAlmostEqual(tracker.get_recent_spend(), 0.25, places=6)
            self.assertAlmostEqual(tracker.get_recent_spend(owner_id='alice'), 0.25, places=6)
        finally:
            for repo in repos.values():
                if hasattr(repo, 'close'):
                    repo.close()
            os.unlink(real_db.name)

    # ------------------------------------------------------------------
    # NULL-cost scanner (find_recent_null_cost_combos)
    # ------------------------------------------------------------------
    def test_find_recent_null_cost_combos_groups_by_sku(self):
        now = datetime.now(timezone.utc)
        # Three NULL-cost rows: 2 for openai/gpt-5-nano, 1 for groq/llama-3.1
        self._insert(_iso(now), 'alice', None)
        self._insert(_iso(now), 'bob', None)
        # change provider/model on the third — need a custom insert
        with sqlite3.connect(self.tmp.name) as conn:
            conn.execute(
                "INSERT INTO api_usage (created_at, owner_id, call_type, provider, "
                "model, status, estimated_cost) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_iso(now), 'alice', 'player_decision', 'groq', 'llama-3.1', 'ok', None),
            )
        # A priced row of the same SKU must NOT show up.
        self._insert(_iso(now), 'alice', 0.50)

        combos = self.tracker.find_recent_null_cost_combos()
        combos_by_sku = {(p, m): n for (p, m, n) in combos}
        self.assertEqual(combos_by_sku.get(('openai', 'gpt-5-nano')), 2)
        self.assertEqual(combos_by_sku.get(('groq', 'llama-3.1')), 1)

    def test_find_recent_null_cost_combos_window_filters_old_rows(self):
        now = datetime.now(timezone.utc)
        self._insert(_iso(now), 'alice', None)  # recent — should appear
        self._insert(_iso(now - timedelta(hours=48)), 'alice', None)  # stale — must not

        combos = self.tracker.find_recent_null_cost_combos()
        self.assertEqual(combos, [('openai', 'gpt-5-nano', 1)])

    def test_find_recent_null_cost_combos_fails_open(self):
        # Missing api_usage table → returns []; never raises.
        missing = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        missing.close()
        try:
            tracker = UsageTracker(db_path=missing.name)
            self.assertEqual(tracker.find_recent_null_cost_combos(), [])
        finally:
            os.unlink(missing.name)


class TestWarnMissingPricingRows(unittest.TestCase):
    """The startup wrapper that turns NULL-cost SKUs into loud boot warnings."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        with sqlite3.connect(self.tmp.name) as conn:
            conn.execute("""
                CREATE TABLE api_usage (
                    id INTEGER PRIMARY KEY, created_at TIMESTAMP, owner_id TEXT,
                    call_type TEXT, provider TEXT, model TEXT, status TEXT,
                    estimated_cost REAL
                )
            """)
        self.tracker = UsageTracker(db_path=self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_warns_per_missing_sku(self):
        from flask_app import config as app_config

        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.tmp.name) as conn:
            conn.execute(
                "INSERT INTO api_usage (created_at, provider, model, status, estimated_cost) "
                "VALUES (?, 'openai', 'gpt-5-nano', 'ok', NULL)",
                (now,),
            )

        with (
            patch.object(tracking.UsageTracker, 'get_default', return_value=self.tracker),
            self.assertLogs('flask_app.config', level='WARNING') as logs,
        ):
            app_config.warn_missing_pricing_rows()

        self.assertTrue(any('gpt-5-nano' in m and 'NULL' in m for m in logs.output))

    def test_silent_when_all_rows_priced(self):
        from flask_app import config as app_config

        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.tmp.name) as conn:
            conn.execute(
                "INSERT INTO api_usage (created_at, provider, model, status, estimated_cost) "
                "VALUES (?, 'openai', 'gpt-5-nano', 'ok', 0.01)",
                (now,),
            )

        with patch.object(tracking.UsageTracker, 'get_default', return_value=self.tracker):
            # No WARNING expected — assertLogs would fail if none emitted, so we
            # assert via a manual handler instead.
            import logging as _logging

            records = []
            handler = _logging.Handler()
            handler.emit = records.append
            logger = _logging.getLogger('flask_app.config')
            logger.addHandler(handler)
            try:
                app_config.warn_missing_pricing_rows()
            finally:
                logger.removeHandler(handler)
        self.assertEqual([r for r in records if r.levelno >= _logging.WARNING], [])


if __name__ == '__main__':
    unittest.main()

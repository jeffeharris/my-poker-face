"""Unit tests for `CashScalpsRepository` (schema v132).

Covers the upsert increment, per-victim breakdown (what renown-weighting
consumes), total_for aggregation, the victims_of ("who's hunting me") view,
sandbox isolation, record_many batch, and last_at storage.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from poker.repositories import create_repos

SB = "sb-1"
OTHER_SB = "sb-2"


class TestCashScalpsRepository(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = create_repos(self.tmp.name)["cash_scalps_repo"]

    def tearDown(self):
        try:
            os.unlink(self.tmp.name)
        except FileNotFoundError:
            pass

    def test_empty_reads(self):
        self.assertEqual(self.repo.total_for(SB, "ace"), 0)
        self.assertEqual(self.repo.list_for_eliminator(SB, "ace"), [])
        self.assertEqual(self.repo.victims_of(SB, "fish"), [])

    def test_record_upsert_increments(self):
        self.repo.record(SB, "ace", "fish", now="2026-06-01T00:00:00Z")
        self.assertEqual(self.repo.total_for(SB, "ace"), 1)
        self.repo.record(SB, "ace", "fish", now="2026-06-01T00:01:00Z")
        self.assertEqual(self.repo.total_for(SB, "ace"), 2)
        # one row, count=2 (not two rows)
        self.assertEqual(self.repo.list_for_eliminator(SB, "ace"), [("fish", 2)])

    def test_per_victim_breakdown_desc(self):
        for _ in range(3):
            self.repo.record(SB, "ace", "fish")
        self.repo.record(SB, "ace", "donk")
        self.assertEqual(self.repo.list_for_eliminator(SB, "ace"), [("fish", 3), ("donk", 1)])
        self.assertEqual(self.repo.total_for(SB, "ace"), 4)

    def test_victims_of(self):
        self.repo.record(SB, "ace", "fish")
        self.repo.record(SB, "ace", "fish")
        self.repo.record(SB, "blackbeard", "fish")
        # who busted fish, and how often
        self.assertEqual(self.repo.victims_of(SB, "fish"), [("ace", 2), ("blackbeard", 1)])

    def test_sandbox_isolation(self):
        self.repo.record(SB, "ace", "fish")
        self.repo.record(OTHER_SB, "ace", "fish")
        self.assertEqual(self.repo.total_for(SB, "ace"), 1)
        self.assertEqual(self.repo.total_for(OTHER_SB, "ace"), 1)
        # a third sandbox is untouched
        self.assertEqual(self.repo.total_for("sb-3", "ace"), 0)

    def test_record_many_batch(self):
        scalps = [("ace", "fish"), ("ace", "donk"), ("ace", "fish")]
        n = self.repo.record_many(SB, scalps, now="2026-06-01T00:00:00Z")
        self.assertEqual(n, 3)
        self.assertEqual(self.repo.total_for(SB, "ace"), 3)
        self.assertEqual(self.repo.list_for_eliminator(SB, "ace"), [("fish", 2), ("donk", 1)])

    def test_last_at_stored(self):
        self.repo.record(SB, "ace", "fish", now="2026-06-01T12:34:56Z")
        with self.repo._get_connection() as conn:
            row = conn.execute(
                "SELECT last_at FROM cash_scalps WHERE sandbox_id=? AND "
                "eliminator_id=? AND victim_id=?",
                (SB, "ace", "fish"),
            ).fetchone()
        self.assertEqual(row["last_at"], "2026-06-01T12:34:56Z")


if __name__ == "__main__":
    unittest.main()

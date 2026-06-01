"""Unit tests for `PrestigeSnapshotsRepository` (schema v121).

Covers record → load_latest round-trip (all columns), per-(sandbox, owner)
isolation, the renown-peak MAX (the ratchet's read side), the
oldest→newest `series_since` window, and retention `prune`.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import dataclass

from poker.repositories import create_repos

SB = "sb-1"
OTHER_SB = "sb-2"
OWNER = "guest_jeff"
OTHER_OWNER = "guest_kim"


@dataclass(frozen=True)
class _Score:
    """Minimal duck-typed stand-in for cash_mode.prestige.ReputationScore."""

    renown: float = 0.0
    regard: float = 0.0
    quadrant: str = "Up-and-comer"
    renown_breadth: float = 0.0
    renown_tenure: float = 0.0
    renown_stake_tier: float = 0.0
    renown_beat_respected: float = 0.0
    renown_high_stakes: float = 0.0
    regard_likability: float = 0.0
    regard_respect: float = 0.0
    regard_heat: float = 0.0
    opponent_count: int = 0


class TestPrestigeSnapshotsRepository(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = create_repos(self.tmp.name)["prestige_snapshots_repo"]

    def tearDown(self):
        try:
            os.unlink(self.tmp.name)
        except FileNotFoundError:
            pass

    def _record(self, *, at, renown=0.0, regard=0.0, quadrant="Up-and-comer", sandbox=SB, owner=OWNER, **kw):
        self.repo.record(
            captured_at=at,
            sandbox_id=sandbox,
            owner_id=owner,
            score=_Score(renown=renown, regard=regard, quadrant=quadrant, **kw),
        )

    def test_load_latest_none_when_empty(self):
        self.assertIsNone(self.repo.load_latest(SB, OWNER))

    def test_record_and_load_latest_round_trip(self):
        self._record(
            at="2026-05-29T12:00:00Z",
            renown=0.42,
            regard=-0.31,
            quadrant="Infamous Villain",
            renown_breadth=0.1,
            renown_tenure=0.2,
            renown_stake_tier=0.12,
            regard_heat=-0.4,
            opponent_count=7,
        )
        row = self.repo.load_latest(SB, OWNER)
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row["renown"], 0.42)
        self.assertAlmostEqual(row["regard"], -0.31)
        self.assertEqual(row["quadrant"], "Infamous Villain")
        self.assertAlmostEqual(row["renown_breadth"], 0.1)
        self.assertAlmostEqual(row["regard_heat"], -0.4)
        self.assertEqual(row["opponent_count"], 7)
        self.assertEqual(row["captured_at"], "2026-05-29T12:00:00Z")

    def test_load_latest_returns_newest(self):
        self._record(at="2026-05-29T10:00:00Z", renown=0.1, quadrant="Up-and-comer")
        self._record(at="2026-05-29T12:00:00Z", renown=0.3, quadrant="Beloved Legend")
        self._record(at="2026-05-29T11:00:00Z", renown=0.2, quadrant="Up-and-comer")
        row = self.repo.load_latest(SB, OWNER)
        self.assertEqual(row["captured_at"], "2026-05-29T12:00:00Z")
        self.assertEqual(row["quadrant"], "Beloved Legend")

    def test_isolation_by_sandbox_and_owner(self):
        self._record(at="2026-05-29T12:00:00Z", renown=0.9, sandbox=SB, owner=OWNER)
        self._record(at="2026-05-29T12:00:00Z", renown=0.1, sandbox=OTHER_SB, owner=OWNER)
        self._record(at="2026-05-29T12:00:00Z", renown=0.5, sandbox=SB, owner=OTHER_OWNER)
        self.assertAlmostEqual(self.repo.load_latest(SB, OWNER)["renown"], 0.9)
        self.assertAlmostEqual(self.repo.load_latest(OTHER_SB, OWNER)["renown"], 0.1)
        self.assertAlmostEqual(self.repo.load_latest(SB, OTHER_OWNER)["renown"], 0.5)

    def test_load_renown_peak(self):
        self.assertEqual(self.repo.load_renown_peak(SB, OWNER), 0.0)
        self._record(at="2026-05-29T10:00:00Z", renown=0.4)
        self._record(at="2026-05-29T11:00:00Z", renown=0.7)
        self._record(at="2026-05-29T12:00:00Z", renown=0.55)  # a dip after the peak
        self.assertAlmostEqual(self.repo.load_renown_peak(SB, OWNER), 0.7)
        # Peak is scoped — another sandbox doesn't see it.
        self.assertEqual(self.repo.load_renown_peak(OTHER_SB, OWNER), 0.0)

    def test_series_since_orders_and_windows(self):
        self._record(at="2026-05-29T10:00:00Z", renown=0.1, regard=0.0)
        self._record(at="2026-05-29T11:00:00Z", renown=0.2, regard=0.1)
        self._record(at="2026-05-29T12:00:00Z", renown=0.3, regard=-0.1)
        series = self.repo.series_since("2026-05-29T10:30:00Z", sandbox_id=SB, owner_id=OWNER)
        self.assertEqual([p["captured_at"] for p in series],
                         ["2026-05-29T11:00:00Z", "2026-05-29T12:00:00Z"])
        self.assertAlmostEqual(series[0]["renown"], 0.2)

    def test_prune(self):
        self._record(at="2026-05-01T00:00:00Z", renown=0.1)
        self._record(at="2026-05-29T00:00:00Z", renown=0.2)
        deleted = self.repo.prune("2026-05-15T00:00:00Z")
        self.assertEqual(deleted, 1)
        row = self.repo.load_latest(SB, OWNER)
        self.assertEqual(row["captured_at"], "2026-05-29T00:00:00Z")


if __name__ == "__main__":
    unittest.main()

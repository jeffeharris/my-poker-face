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

    # --- v2 (schema v133) ---------------------------------------------------

    def test_record_defaults_to_v1_with_null_v2_columns(self):
        self._record(at="2026-06-01T12:00:00Z", renown=0.3)
        row = self.repo.load_latest(SB, OWNER)
        self.assertEqual(row["formula_version"], "v1")
        self.assertIsNone(row["renown_v2"])
        self.assertIsNone(row["high_cut"])
        self.assertIsNone(row["renown_v2_components"])

    def test_record_v2_fields_round_trip(self):
        self.repo.record(
            captured_at="2026-06-01T12:00:00Z",
            sandbox_id=SB,
            owner_id=OWNER,
            score=_Score(renown=0.42, regard=-0.2, quadrant="Infamous Villain"),
            formula_version="v2",
            renown_v2=54.9,
            victim_percentile=0.987,
            high_cut=36.7,
            renown_v2_components={"scalps": 21.4, "backing": 12.0},
            field_size=80,
        )
        row = self.repo.load_latest(SB, OWNER)
        self.assertEqual(row["formula_version"], "v2")
        self.assertAlmostEqual(row["renown_v2"], 54.9)
        self.assertAlmostEqual(row["victim_percentile"], 0.987)
        self.assertAlmostEqual(row["high_cut"], 36.7)
        self.assertEqual(row["field_size"], 80)
        # quadrant column is the CONSUMED one (the caller's choice).
        self.assertEqual(row["quadrant"], "Infamous Villain")
        # components serialised as JSON.
        import json
        self.assertEqual(json.loads(row["renown_v2_components"]),
                         {"scalps": 21.4, "backing": 12.0})

    def test_load_renown_v2_peak_ratchets_independent_of_v1(self):
        # v1-only rows are ignored by the v2 peak (NULL renown_v2).
        self._record(at="2026-06-01T09:00:00Z", renown=0.9)
        self.assertEqual(self.repo.load_renown_v2_peak(SB, OWNER), 0.0)
        # v2 rows ratchet on their own scale; a dip can't lower the peak.
        for at, rv2 in (("10:00", 40.0), ("11:00", 58.0), ("12:00", 50.0)):
            self.repo.record(
                captured_at=f"2026-06-01T{at}:00Z", sandbox_id=SB, owner_id=OWNER,
                score=_Score(renown=0.5), formula_version="v2", renown_v2=rv2,
            )
        self.assertAlmostEqual(self.repo.load_renown_v2_peak(SB, OWNER), 58.0)
        self.assertEqual(self.repo.load_renown_v2_peak(OTHER_SB, OWNER), 0.0)

    # --- v2 AI-entity persistence (schema v139, Stage A) --------------------

    def test_record_ai_many_round_trip(self):
        n = self.repo.record_ai_many(
            sandbox_id=SB,
            captured_at="2026-06-02T12:00:00Z",
            rows=[
                {"owner_id": "napoleon", "renown_v2": 41.2, "regard": -0.5,
                 "quadrant": "Infamous Villain", "victim_percentile": 0.91,
                 "high_cut": 30.0, "components": {"scalps": 18.0}, "field_size": 12},
                {"owner_id": "deadpool", "renown_v2": 12.0, "regard": 0.3,
                 "quadrant": "Up-and-comer", "victim_percentile": 0.4,
                 "high_cut": 30.0, "components": {"breadth": 6.0}, "field_size": 12},
            ],
        )
        self.assertEqual(n, 2)
        row = self.repo.load_latest(SB, "napoleon", entity_kind="ai")
        self.assertIsNotNone(row)
        self.assertEqual(row["entity_kind"], "ai")
        self.assertEqual(row["formula_version"], "v2")
        self.assertAlmostEqual(row["renown_v2"], 41.2)
        self.assertAlmostEqual(row["regard"], -0.5)
        self.assertEqual(row["quadrant"], "Infamous Villain")
        self.assertEqual(row["field_size"], 12)
        # AI rows are v2-native: the v1 capped renown column is 0, not the v2 value.
        self.assertAlmostEqual(row["renown"], 0.0)
        import json
        self.assertEqual(json.loads(row["renown_v2_components"]), {"scalps": 18.0})

    def test_record_ai_many_empty_is_noop(self):
        self.assertEqual(
            self.repo.record_ai_many(sandbox_id=SB, captured_at="x", rows=[]), 0)

    def test_human_load_latest_never_matches_ai_rows(self):
        # The owner_id-as-subject invariant: a human read (default 'player')
        # must never return an AI row, even in the same sandbox.
        self._record(at="2026-06-02T12:00:00Z", renown=0.3, quadrant="Up-and-comer")
        self.repo.record_ai_many(
            sandbox_id=SB, captured_at="2026-06-02T12:00:00Z",
            rows=[{"owner_id": "napoleon", "renown_v2": 41.2,
                   "quadrant": "Infamous Villain"}],
        )
        # Human row is the human's, unaffected.
        self.assertEqual(self.repo.load_latest(SB, OWNER)["quadrant"], "Up-and-comer")
        # An AI personality is invisible to the default ('player') read...
        self.assertIsNone(self.repo.load_latest(SB, "napoleon"))
        # ...but present under its own kind.
        self.assertEqual(
            self.repo.load_latest(SB, "napoleon", entity_kind="ai")["quadrant"],
            "Infamous Villain")

    def test_load_renown_v2_peaks_batched_ratchet(self):
        # Two AIs, each captured twice with a dip — the batched read returns the
        # MAX per entity, scoped to 'ai', omitting the human.
        self._record(at="2026-06-02T09:00:00Z", renown=0.9)  # a human v1 row
        self.repo.record_ai_many(
            sandbox_id=SB, captured_at="2026-06-02T10:00:00Z",
            rows=[{"owner_id": "napoleon", "renown_v2": 40.0, "quadrant": "x"},
                  {"owner_id": "deadpool", "renown_v2": 10.0, "quadrant": "x"}],
        )
        self.repo.record_ai_many(
            sandbox_id=SB, captured_at="2026-06-02T11:00:00Z",
            rows=[{"owner_id": "napoleon", "renown_v2": 33.0, "quadrant": "x"},  # dip
                  {"owner_id": "deadpool", "renown_v2": 12.0, "quadrant": "x"}],
        )
        peaks = self.repo.load_renown_v2_peaks(SB, "ai")
        self.assertEqual(set(peaks), {"napoleon", "deadpool"})
        self.assertAlmostEqual(peaks["napoleon"], 40.0)
        self.assertAlmostEqual(peaks["deadpool"], 12.0)
        # Sandbox-scoped, kind-scoped.
        self.assertEqual(self.repo.load_renown_v2_peaks(OTHER_SB, "ai"), {})

    def test_load_latest_field_percentiles(self):
        # The latest cycle's victim_percentile for every entity (AI + human),
        # keyed by raw id — the B4 marquee read.
        self.assertEqual(self.repo.load_latest_field_percentiles(SB), {})
        # Human row (v2) + two AI rows at the same capture timestamp.
        self.repo.record(
            captured_at="2026-06-02T10:00:00Z", sandbox_id=SB, owner_id=OWNER,
            score=_Score(renown=0.5), formula_version="v2", renown_v2=60.0,
            victim_percentile=0.95,
        )
        self.repo.record_ai_many(
            sandbox_id=SB, captured_at="2026-06-02T10:00:00Z",
            rows=[{"owner_id": "napoleon", "renown_v2": 40.0, "quadrant": "x",
                   "victim_percentile": 0.7},
                  {"owner_id": "deadpool", "renown_v2": 10.0, "quadrant": "x",
                   "victim_percentile": 0.3}],
        )
        pcts = self.repo.load_latest_field_percentiles(SB)
        self.assertEqual(set(pcts), {OWNER, "napoleon", "deadpool"})
        self.assertAlmostEqual(pcts[OWNER], 0.95)
        self.assertAlmostEqual(pcts["napoleon"], 0.7)
        # A newer cycle supersedes the old percentiles (latest captured_at wins).
        self.repo.record_ai_many(
            sandbox_id=SB, captured_at="2026-06-02T11:00:00Z",
            rows=[{"owner_id": "napoleon", "renown_v2": 55.0, "quadrant": "x",
                   "victim_percentile": 0.88}],
        )
        pcts2 = self.repo.load_latest_field_percentiles(SB)
        self.assertEqual(set(pcts2), {"napoleon"})  # only the newest cycle's rows
        self.assertAlmostEqual(pcts2["napoleon"], 0.88)


if __name__ == "__main__":
    unittest.main()

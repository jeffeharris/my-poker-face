"""Integration test for the Renown-v2 ticker wiring (stage C).

Proves the live path end-to-end on a seeded temp DB, no real data needed:
  flag ON  → `_maybe_v2_overlay` scores the field, returns the field-relative
             quadrant + v2 fields → persisted via `record(formula_version='v2')`
             → `_reputation_payload_from_snapshot` exposes the v2 payload.
  flag OFF → overlay returns None (the v1-only row is written), so the flip is a
             clean kill switch.

The 4 reputation hooks read the persisted `quadrant` STRING, so persisting the
relative quadrant is what makes them follow — that's covered by asserting the
quadrant column carries the v2 classification.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime

import pytest

from cash_mode import economy_flags
from cash_mode.prestige import compute_prestige
from flask_app import extensions
from flask_app.routes.cash_routes import _reputation_payload_from_snapshot
from flask_app.services import ticker_service
from poker.repositories import create_repos

pytestmark = [pytest.mark.flask, pytest.mark.integration]

SB = "sb-1"
HUMAN = "guest"


class TestRenownV2Wiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repos = create_repos(self.tmp.name)
        self._seed()
        # Point the extensions globals the ticker reads at our temp repos.
        self._saved = {
            k: getattr(extensions, k, None)
            for k in ("renown_field_repo", "prestige_snapshots_repo",
                      "relationship_repo", "cash_session_repo")
        }
        extensions.renown_field_repo = self.repos["renown_field_repo"]
        extensions.prestige_snapshots_repo = self.repos["prestige_snapshots_repo"]
        extensions.relationship_repo = self.repos["relationship_repo"]
        extensions.cash_session_repo = self.repos["cash_session_repo"]
        self._flag = economy_flags.RENOWN_V2_ENABLED
        self._persist_ai_flag = economy_flags.RENOWN_V2_PERSIST_AI

    def tearDown(self):
        economy_flags.RENOWN_V2_ENABLED = self._flag
        economy_flags.RENOWN_V2_PERSIST_AI = self._persist_ai_flag
        for k, v in self._saved.items():
            setattr(extensions, k, v)
        try:
            os.unlink(self.tmp.name)
        except FileNotFoundError:
            pass

    def _seed(self):
        """A tiny field where the human dominates → a clear 'figure'."""
        with self.repos["renown_field_repo"]._get_connection() as c:
            # human is up on many opponents (breadth + roster_net); two rival
            # AIs with thin activity → human tops the field by a wide margin.
            rows = [(SB, HUMAN, f"opp{i}", 400, 40) for i in range(8)]
            rows += [(SB, "rivalA", HUMAN, -50, 5), (SB, "rivalB", HUMAN, -50, 5)]
            c.executemany(
                "INSERT INTO cash_pair_stats (sandbox_id, observer_id, opponent_id, "
                "cumulative_pnl, hands_played_cash) VALUES (?,?,?,?,?)", rows,
            )
            # inbound heat → the human reads hostile (Infamous Villain when high).
            c.executemany(
                "INSERT INTO relationship_states (observer_id, opponent_id, "
                "likability, respect, heat) VALUES (?,?,?,?,?)",
                [(f"opp{i}", HUMAN, 0.3, 0.6, 0.7) for i in range(8)],
            )

    def _v1_score(self):
        return compute_prestige(
            owner_id=HUMAN, sandbox_id=SB, now=datetime(2026, 6, 1, 12, 0, 0),
            relationship_repo=self.repos["relationship_repo"],
            cash_session_repo=self.repos["cash_session_repo"],
        )

    def test_overlay_returns_none_when_flag_off(self):
        economy_flags.RENOWN_V2_ENABLED = False
        out = ticker_service._maybe_v2_overlay(
            HUMAN, SB, self._v1_score(), datetime(2026, 6, 1, 12, 0, 0))
        self.assertIsNone(out)

    def test_overlay_scores_field_when_flag_on(self):
        economy_flags.RENOWN_V2_ENABLED = True
        out = ticker_service._maybe_v2_overlay(
            HUMAN, SB, self._v1_score(), datetime(2026, 6, 1, 12, 0, 0))
        self.assertIsNotNone(out)
        self.assertIn(out["quadrant"], (
            "Beloved Legend", "Infamous Villain", "Up-and-comer", "Disliked Nobody"))
        self.assertGreater(out["renown_v2"], 0.0)
        self.assertGreaterEqual(out["field_size"], 3)  # human + 2 rivals + opps
        self.assertIn("breadth", out["components"])

    def test_persist_and_payload_round_trip_v2(self):
        economy_flags.RENOWN_V2_ENABLED = True
        now = datetime(2026, 6, 1, 12, 0, 0)
        v1 = self._v1_score()
        out = ticker_service._maybe_v2_overlay(HUMAN, SB, v1, now)
        from dataclasses import replace
        score = replace(v1, quadrant=out["quadrant"])
        self.repos["prestige_snapshots_repo"].record(
            captured_at=score.computed_at, sandbox_id=SB, owner_id=HUMAN, score=score,
            formula_version="v2", renown_v2=out["renown_v2"],
            victim_percentile=out["victim_percentile"], high_cut=out["high_cut"],
            renown_v2_components=out["components"], field_size=out["field_size"],
        )
        snap = self.repos["prestige_snapshots_repo"].load_latest(SB, HUMAN)
        payload = _reputation_payload_from_snapshot(snap)
        self.assertEqual(payload["formula_version"], "v2")
        self.assertEqual(payload["quadrant"], out["quadrant"])  # the CONSUMED quadrant
        self.assertAlmostEqual(payload["renown_v2"], out["renown_v2"])
        self.assertEqual(payload["field_size"], out["field_size"])
        self.assertIn("breadth", payload["renown_v2_components"])

    def test_v1_payload_has_no_v2_block(self):
        # A v1 (flag-off) row exposes formula_version but no v2 fields.
        v1 = self._v1_score()
        self.repos["prestige_snapshots_repo"].record(
            captured_at=v1.computed_at, sandbox_id=SB, owner_id=HUMAN, score=v1)
        snap = self.repos["prestige_snapshots_repo"].load_latest(SB, HUMAN)
        payload = _reputation_payload_from_snapshot(snap)
        self.assertEqual(payload["formula_version"], "v1")
        self.assertNotIn("renown_v2", payload)

    # --- per-AI fan-out (RENOWN_V2_PERSIST_AI, Stage A) ---------------------

    def test_no_ai_rows_when_persist_flag_off(self):
        # RENOWN_V2_ENABLED on, PERSIST_AI off → human dict carries no ai_rows.
        economy_flags.RENOWN_V2_ENABLED = True
        economy_flags.RENOWN_V2_PERSIST_AI = False
        out = ticker_service._maybe_v2_overlay(
            HUMAN, SB, self._v1_score(), datetime(2026, 6, 1, 12, 0, 0))
        self.assertNotIn("ai_rows", out)

    def test_ai_rows_built_and_persist_round_trip(self):
        # Both flags on → the overlay returns one ai_row per non-human field
        # entity (the two rivals), and record_ai_many persists them so each is
        # readable under entity_kind='ai' while the human read is unaffected.
        economy_flags.RENOWN_V2_ENABLED = True
        economy_flags.RENOWN_V2_PERSIST_AI = True
        now = datetime(2026, 6, 1, 12, 0, 0)
        out = ticker_service._maybe_v2_overlay(HUMAN, SB, self._v1_score(), now)
        ai_rows = out["ai_rows"]
        self.assertEqual({r["owner_id"] for r in ai_rows}, {"rivalA", "rivalB"})
        for r in ai_rows:
            self.assertIn(r["quadrant"], (
                "Beloved Legend", "Infamous Villain",
                "Up-and-comer", "Disliked Nobody"))
            self.assertIn("breadth", r["components"])

        repo = self.repos["prestige_snapshots_repo"]
        n = repo.record_ai_many(sandbox_id=SB, captured_at="2026-06-01T12:00:00Z",
                                rows=ai_rows)
        self.assertEqual(n, 2)
        snap = repo.load_latest(SB, "rivalA", entity_kind="ai")
        self.assertEqual(snap["entity_kind"], "ai")
        self.assertEqual(snap["formula_version"], "v2")
        # The human read (default 'player') never sees the AI row.
        self.assertIsNone(repo.load_latest(SB, "rivalA"))


if __name__ == "__main__":
    unittest.main()

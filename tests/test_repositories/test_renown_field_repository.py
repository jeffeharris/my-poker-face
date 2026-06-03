"""Unit tests for `RenownFieldRepository` (schema v133).

Pins the batched field-input read's SEMANTICS on a crafted fixture: the entity
set (observers with cash activity + the human), the per-tick #1 net-worth
standing, presence as wall-clock, backing from settled stakes, per-tier session
hands, inbound regard averaging, and scalps. The byte-for-byte parity against
the offline oracle on real data is checked separately by
`scripts/renown_field_parity.py` (real DB lives outside the container).
"""

from __future__ import annotations

import os
import tempfile
import unittest

from poker.repositories import create_repos

SB = "sb-1"
HUMAN = "guest"


class TestRenownFieldRepository(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repos = create_repos(self.tmp.name)
        self.repo = self.repos["renown_field_repo"]
        self._seed()

    def tearDown(self):
        try:
            os.unlink(self.tmp.name)
        except FileNotFoundError:
            pass

    def _seed(self):
        with self.repo._get_connection() as c:
            # cash_pair_stats: entities = observers with hands>0 (+ human).
            # guest is up on villain (+500/30h), down to fish (−100/10h);
            # villain has its own row so it's a field entity too.
            c.executemany(
                "INSERT INTO cash_pair_stats "
                "(sandbox_id, observer_id, opponent_id, cumulative_pnl, hands_played_cash) "
                "VALUES (?,?,?,?,?)",
                [
                    (SB, HUMAN, "villain", 500, 30),
                    (SB, HUMAN, "fish", -100, 10),
                    (SB, "villain", HUMAN, 200, 30),
                ],
            )
            # holdings_snapshots: prefixed ids; villain #1 at t1, guest #1 at t2.
            c.executemany(
                "INSERT INTO holdings_snapshots "
                "(sandbox_id, entity_id, kind, net_worth, chips, captured_at) "
                "VALUES (?,?,?,?,?,?)",
                [
                    (SB, "player:guest", "player", 1000, 1000, "2026-06-01T00:00:00Z"),
                    (SB, "ai:villain", "ai", 2000, 2000, "2026-06-01T00:00:00Z"),
                    (SB, "player:guest", "player", 3000, 3000, "2026-06-01T00:05:00Z"),
                    (SB, "ai:villain", "ai", 1500, 1500, "2026-06-01T00:05:00Z"),
                ],
            )
            # stakes: guest staked 10k, settled +2k; villain staked 5k, unsettled.
            # (the loader reads only staker_id/principal/status/staker_payout;
            # the rest satisfy NOT NULL constraints.)
            c.executemany(
                "INSERT INTO stakes (stake_id, session_id, staker_id, staker_kind, "
                "borrower_id, borrower_kind, format, principal, cut, status, "
                "stake_tier, created_at, staker_payout) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        "st1",
                        "se1",
                        HUMAN,
                        "player",
                        "x",
                        "ai",
                        "full",
                        10000,
                        0.5,
                        "settled",
                        "$2",
                        "2026-06-01T00:00:00Z",
                        12000,
                    ),
                    (
                        "st2",
                        "se2",
                        "villain",
                        "ai",
                        "y",
                        "ai",
                        "full",
                        5000,
                        0.5,
                        "active",
                        "$2",
                        "2026-06-01T00:00:00Z",
                        None,
                    ),
                ],
            )
            # cash_sessions: per-tier hands for the human (AIs have none).
            c.executemany(
                "INSERT INTO cash_sessions "
                "(session_id, sandbox_id, owner_id, stake_label, initial_buy_in, "
                "total_buy_in, started_at, hands_played, ended_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        "cs1",
                        SB,
                        HUMAN,
                        "$2",
                        200,
                        200,
                        "2026-06-01T00:30:00Z",
                        100,
                        "2026-06-01T01:00:00Z",
                    ),
                    (
                        "cs2",
                        SB,
                        HUMAN,
                        "$10",
                        1000,
                        1000,
                        "2026-06-01T01:30:00Z",
                        50,
                        "2026-06-01T02:00:00Z",
                    ),
                ],
            )
            # inbound regard edges toward the human (opponent_id == target).
            c.executemany(
                "INSERT INTO relationship_states "
                "(observer_id, opponent_id, likability, respect, heat) VALUES (?,?,?,?,?)",
                [
                    ("a", HUMAN, 0.7, 0.6, 0.2),
                    ("b", HUMAN, 0.5, 0.5, 0.0),
                ],
            )
            # scalps: guest busted fish ×3, villain ×1.
            c.executemany(
                "INSERT INTO cash_scalps "
                "(sandbox_id, eliminator_id, victim_id, count) VALUES (?,?,?,?)",
                [
                    (SB, HUMAN, "fish", 3),
                    (SB, HUMAN, "villain", 1),
                ],
            )

    def test_entity_set_is_observers_plus_human(self):
        field = self.repo.build_inputs(SB, HUMAN)
        # fish is only an opponent/victim, never an observer → not an entity.
        self.assertEqual(set(field), {HUMAN, "villain"})

    def test_human_drivers(self):
        h = self.repo.build_inputs(SB, HUMAN)[HUMAN]
        self.assertEqual(h.breadth_opponents, {"villain": 30, "fish": 10})
        self.assertEqual(h.total_hands, 40)
        self.assertEqual(h.roster_net, 400.0)  # 500 − 100
        self.assertEqual(h.peak_net_worth, 3000.0)
        self.assertEqual(h.ticks_at_number_one, 1)  # #1 at t2 only
        self.assertEqual(h.wall_clock_hours, 2.0)  # 2 distinct ticks (presence)
        self.assertEqual(h.backing_volume, 10000.0)
        self.assertEqual(h.backing_profit, 2000.0)  # 12000 − 10000
        self.assertEqual(h.stakes_hands, {"$2": 100, "$10": 50})
        self.assertEqual(h.scalps, {"fish": 3, "villain": 1})
        # inbound regard averages of (val − 0.5); heat is the raw mean.
        self.assertAlmostEqual(h.regard_likability, ((0.7 - 0.5) + 0.0) / 2)
        self.assertAlmostEqual(h.regard_respect, ((0.6 - 0.5) + 0.0) / 2)
        self.assertAlmostEqual(h.regard_heat, (0.2 + 0.0) / 2)

    def test_villain_standing_and_unsettled_backing(self):
        v = self.repo.build_inputs(SB, HUMAN)["villain"]
        self.assertEqual(v.ticks_at_number_one, 1)  # #1 at t1
        self.assertEqual(v.peak_net_worth, 2000.0)
        self.assertEqual(v.backing_volume, 5000.0)
        self.assertEqual(v.backing_profit, 0.0)  # unsettled → no profit
        self.assertEqual(v.scalps, {})  # villain busted no one

    def test_human_always_present_even_with_no_activity(self):
        field = self.repo.build_inputs(SB, "ghost")  # ghost has no rows
        self.assertIn("ghost", field)
        self.assertEqual(field["ghost"].total_hands, 0)


if __name__ == "__main__":
    unittest.main()

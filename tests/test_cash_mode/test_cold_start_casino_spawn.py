"""Cold-start integration test for the ephemeral-tourist casino flow.

Verifies the property that motivated EPHEMERAL_TOURISTS: a fresh
sandbox with NO pre-seeded fish bankrolls can spawn a casino purely
from vice deposits + the on-demand tourist factory. Before this work,
`load_fish_ids` required existing `ai_bankroll_state` rows, creating
a chicken-and-egg that left casinos permanently unable to spawn in
fresh web sandboxes.

The test is intentionally end-to-end: it drives the same resolver
the lobby refresh calls, so any regression in the cold-start path
will surface here.

Spec: docs/plans/CASH_MODE_EPHEMERAL_TOURISTS.md §"Validation"
"""

from __future__ import annotations

import random
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from cash_mode.bankroll import AIBankrollState
from cash_mode.casino_provisioning import (
    CASINO_FISH_PER_TABLE,
    CASINO_SPAWN_THRESHOLDS,
    resolve_casino_provisioning,
)
from cash_mode.closed_economy import (
    compute_bank_pool_reserves,
    seed_bank_pool,
)
from cash_mode.tourist_factory import TOURIST_TEMPLATES
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager
from poker.rule_strategies import FishLeak


ANCHOR = datetime(2026, 5, 23, 12, 0, 0)
SBX = "cold-start-sandbox"


class TestColdStartCasinoSpawn(unittest.TestCase):
    """Fresh sandbox → vice fills pool → casino spawns with tourists."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = str(Path(self._tmpdir.name) / "cold_start.db")
        SchemaManager(db_path).ensure_schema()
        self.bankroll = BankrollRepository(db_path)
        self.tables = CashTableRepository(db_path)
        self.ledger = ChipLedgerRepository(db_path)
        self.personality = PersonalityRepository(db_path)

    def tearDown(self):
        try:
            self.ledger.close()
        except Exception:
            pass
        self._tmpdir.cleanup()

    def test_fresh_sandbox_with_no_fish_can_spawn_casino(self):
        """The critical regression test: NO `ai_bankroll_state` rows
        exist anywhere in this sandbox. Before EPHEMERAL_TOURISTS this
        would have early-returned at `load_fish_ids` empty-check and
        produced no spawns. With the factory, the pool depth alone is
        enough to spawn a casino full of tourists."""
        # Verify the sandbox is truly cold — no bankroll rows.
        pids = list(self.bankroll.iter_personality_ids_with_bankrolls(sandbox_id=SBX))
        self.assertEqual(pids, [], "test fixture should be a fresh sandbox")
        self.assertEqual(compute_bank_pool_reserves(self.ledger, sandbox_id=SBX), 0)

        # Seed the bank pool above the $2 threshold (simulates the
        # outcome of vice deposits accumulating over time).
        seed_bank_pool(
            self.ledger,
            sandbox_id=SBX,
            amount=CASINO_SPAWN_THRESHOLDS["$2"] * 2,
        )

        batch = resolve_casino_provisioning(
            cash_table_repo=self.tables,
            bankroll_repo=self.bankroll,
            chip_ledger_repo=self.ledger,
            sandbox_id=SBX,
            rng=random.Random(42),
            now=ANCHOR,
        )

        # Exactly one casino at $2 should spawn (the only stake whose
        # threshold is met). The $10 stake's 50k threshold isn't.
        self.assertEqual(
            len(batch.spawns), 1,
            f"expected exactly one casino spawn, got {batch.spawns}",
        )
        spawn = batch.spawns[0]
        self.assertEqual(spawn.stake_label, "$2")
        self.assertEqual(len(spawn.fish_seated), CASINO_FISH_PER_TABLE)
        # Synthetic pids — never seen these before.
        for pid in spawn.fish_seated:
            self.assertTrue(
                pid.startswith("tourist-"),
                f"expected synthetic tourist pid, got {pid!r}",
            )

    def test_spawned_tourists_carry_valid_leaks(self):
        """Each seated tourist must have a `fish_leak` from its template's
        candidate pool. Catches regressions in the factory→seat handoff."""
        seed_bank_pool(
            self.ledger, sandbox_id=SBX,
            amount=CASINO_SPAWN_THRESHOLDS["$2"] * 2,
        )
        resolve_casino_provisioning(
            cash_table_repo=self.tables,
            bankroll_repo=self.bankroll,
            chip_ledger_repo=self.ledger,
            sandbox_id=SBX,
            rng=random.Random(7),
            now=ANCHOR,
        )
        casino = next(
            t for t in self.tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        )
        templates_by_key = {t.key: t for t in TOURIST_TEMPLATES}
        for seat in casino.seats:
            if seat.get("kind") != "ai":
                continue
            inline = seat["ephemeral_personality"]
            template = templates_by_key.get(inline["template_key"])
            self.assertIsNotNone(
                template,
                f"unknown template_key {inline['template_key']}",
            )
            picked = FishLeak(inline["fish_leak"])
            self.assertIn(
                picked, template.candidate_leaks,
                f"{template.key} produced leak {picked} not in candidate_leaks",
            )

    def test_no_fish_bankroll_rows_created_during_spawn(self):
        """Tourists are stateless — spawning must not create any
        `ai_bankroll_state` rows. This is the actual mechanism that
        avoids the pre-EPHEMERAL_TOURISTS cold-start trap."""
        seed_bank_pool(
            self.ledger, sandbox_id=SBX,
            amount=CASINO_SPAWN_THRESHOLDS["$2"] * 2,
        )
        resolve_casino_provisioning(
            cash_table_repo=self.tables,
            bankroll_repo=self.bankroll,
            chip_ledger_repo=self.ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
        )
        # The pool seed creates one synthetic donor pid (`sim_donor_*`),
        # which is not a tourist. No tourist pids should appear.
        bankroll_pids = list(
            self.bankroll.iter_personality_ids_with_bankrolls(sandbox_id=SBX)
        )
        tourist_pids = [p for p in bankroll_pids if p.startswith("tourist-")]
        self.assertEqual(
            tourist_pids, [],
            f"tourists should not have bankroll rows; found {tourist_pids}",
        )

    def test_full_roundtrip_returns_pool_to_initial_depth(self):
        """End-to-end conservation: seed → spawn → teardown with all
        chips still on tourist seats → pool returns to original depth.
        Catches any path where the seed+return arithmetic breaks."""
        initial_seed = CASINO_SPAWN_THRESHOLDS["$2"] * 2
        seed_bank_pool(self.ledger, sandbox_id=SBX, amount=initial_seed)
        initial_pool = compute_bank_pool_reserves(self.ledger, sandbox_id=SBX)
        self.assertEqual(initial_pool, initial_seed)

        resolve_casino_provisioning(
            cash_table_repo=self.tables,
            bankroll_repo=self.bankroll,
            chip_ledger_repo=self.ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
        )

        # Force teardown: return residuals + delete the row.
        from cash_mode.casino_provisioning import _return_seat_residuals_to_pool
        casino = next(
            t for t in self.tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        )
        _returned, stranded = _return_seat_residuals_to_pool(
            casino, chip_ledger_repo=self.ledger,
            sandbox_id=SBX, reason_detail="test_roundtrip",
        )
        self.assertEqual(stranded, 0)
        self.tables.delete_table(casino.table_id, sandbox_id=SBX)

        # Pool should be exactly back to the initial seed amount.
        final_pool = compute_bank_pool_reserves(self.ledger, sandbox_id=SBX)
        self.assertEqual(
            final_pool, initial_seed,
            f"drift: initial {initial_seed}, final {final_pool}",
        )


if __name__ == "__main__":
    unittest.main()

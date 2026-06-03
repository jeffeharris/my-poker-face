"""Unit + integration tests for `cash_mode.seat_registry.SeatOccupancyRegistry`.

Phase 1 of the cash seat-invariant hardening introduced
`SeatOccupancyRegistry` as a behavior-preserving, audited drop-in for the raw
`seated_globally: Set[str]`. These tests pin:

  1. The registry's own semantics (seat / vacate / vacate_or_retain / unions /
     collision counting / set-compat operators).
  2. That a real seeded refresh sim records ZERO collisions — i.e. the migration
     introduced no phantom double-seat and the registry's view matches reality.

The collision check (#2) reuses the Phase 0 harness's sandbox builder so it
drives the REAL `refresh_unseated_tables` (the actual bug surface), then asserts
no ERROR record was emitted by the registry's logger across the run.
"""

import logging
import random

import pytest

from cash_mode.lobby import refresh_unseated_tables
from cash_mode.seat_registry import SeatOccupancyRegistry

from .test_seat_occupancy_invariants import (
    ANCHOR,
    SANDBOX,
    SEEDS,
    TICKS_PER_SEED,
    _build_sandbox,
)

REGISTRY_LOGGER = "cash_mode.seat_registry"


# --------------------------------------------------------------------------
# Unit tests — registry semantics
# --------------------------------------------------------------------------


def test_seat_new_adds():
    reg = SeatOccupancyRegistry()
    reg.seat("a")
    assert "a" in reg
    assert reg.contains("a")
    assert reg.collision_count == 0
    assert reg.snapshot() == frozenset({"a"})


def test_seat_duplicate_noops_logs_and_counts(caplog):
    reg = SeatOccupancyRegistry({"a"}, label="unit")
    with caplog.at_level(logging.ERROR, logger=REGISTRY_LOGGER):
        reg.seat("a")
    # State unchanged (set.add no-op-on-duplicate parity).
    assert reg.snapshot() == frozenset({"a"})
    # Collision logged + counted.
    assert reg.collision_count == 1
    errors = [r for r in caplog.records if r.name == REGISTRY_LOGGER and r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "double-seat" in errors[0].getMessage()
    # A second duplicate bumps the counter again.
    reg.seat("a")
    assert reg.collision_count == 2


def test_vacate_present_and_absent():
    reg = SeatOccupancyRegistry({"a", "b"})
    reg.vacate("a")
    assert "a" not in reg
    assert "b" in reg
    # Absent vacate is a no-op (set.discard parity), no raise.
    reg.vacate("missing")
    assert reg.snapshot() == frozenset({"b"})
    assert reg.collision_count == 0


def test_vacate_or_retain_keeps_pid_no_collision(caplog):
    reg = SeatOccupancyRegistry({"a"})
    with caplog.at_level(logging.DEBUG, logger=REGISTRY_LOGGER):
        reg.vacate_or_retain("a", retain_reason="take_stake")
    # pid still seated; no collision recorded.
    assert "a" in reg
    assert reg.collision_count == 0
    # Even retaining a pid that is NOT seated must not seat it nor collide.
    reg.vacate_or_retain("ghost", retain_reason="take_stake")
    assert "ghost" not in reg
    assert reg.collision_count == 0


def test_add_without_collision_check_overlap_adds_no_collision(caplog):
    reg = SeatOccupancyRegistry({"a"}, label="unit")
    with caplog.at_level(logging.DEBUG, logger=REGISTRY_LOGGER):
        reg.add_without_collision_check({"a", "b"})
    # Overlapping pid added (idempotent) + new pid added; NOT a collision.
    assert reg.snapshot() == frozenset({"a", "b"})
    assert reg.collision_count == 0
    # Overlap logged at DEBUG, not ERROR.
    errors = [r for r in caplog.records if r.name == REGISTRY_LOGGER and r.levelno == logging.ERROR]
    assert not errors


def test_ior_routes_to_union_no_collision():
    reg = SeatOccupancyRegistry({"a"})
    reg |= {"a", "b", "c"}
    assert isinstance(reg, SeatOccupancyRegistry)
    assert reg.snapshot() == frozenset({"a", "b", "c"})
    assert reg.collision_count == 0


def test_update_routes_to_union_no_collision():
    reg = SeatOccupancyRegistry({"a"})
    reg.update(x for x in ("a", "b"))  # generator, like the hand-boundary site
    assert reg.snapshot() == frozenset({"a", "b"})
    assert reg.collision_count == 0


def test_set_compat_aliases_and_protocols():
    reg = SeatOccupancyRegistry()
    # `.add` is the seat alias (collision-aware); `.discard` is vacate.
    reg.add("a")
    reg.add("a")  # duplicate -> collision via alias
    assert reg.collision_count == 1
    reg.discard("a")
    assert "a" not in reg
    # __iter__ / __len__ work for any set-style consumer.
    reg.add("x")
    reg.add("y")
    assert len(reg) == 2
    assert set(reg) == {"x", "y"}


# --------------------------------------------------------------------------
# Integration — zero collisions during a real seeded refresh sim
# --------------------------------------------------------------------------


@pytest.mark.simulation
@pytest.mark.parametrize("seed", SEEDS)
def test_refresh_records_zero_collisions(tmp_path, seed, caplog):
    """Drive the REAL refresh across ticks; the registry must log NO ERROR.

    A registry ERROR record means a double-seat (`seat()` on an already-seated
    pid) surfaced during the migrated path — exactly the ghost-seat anomaly the
    wrapper exists to make loud. Zero ERROR records => the migration introduced
    no phantom double-seat and the registry's view matched reality.
    """
    repos, _ = _build_sandbox(tmp_path)
    rng = random.Random(seed)

    with caplog.at_level(logging.ERROR, logger=REGISTRY_LOGGER):
        for _ in range(TICKS_PER_SEED):
            refresh_unseated_tables(
                cash_table_repo=repos["cash_table_repo"],
                personality_repo=repos["personality_repo"],
                bankroll_repo=repos["bankroll_repo"],
                chip_ledger_repo=repos["chip_ledger_repo"],
                rng=rng,
                now=ANCHOR,
                sandbox_id=SANDBOX,
                vice_mode="off",
                vice_repo=None,
                side_hustle_repo=None,
                stake_repo=None,
                relationship_repo=None,
                human_headroom=0,
            )

    collisions = [
        r for r in caplog.records if r.name == REGISTRY_LOGGER and r.levelno == logging.ERROR
    ]
    assert not collisions, (
        f"[seed {seed}] SeatOccupancyRegistry recorded {len(collisions)} "
        f"collision(s) during refresh: {[r.getMessage() for r in collisions]}"
    )

"""Tests for the held Director rake policy (DIRECTOR_POLICY_HOLD).

The reserve-gated rake schedule is held for POLICY_WINDOW_SECONDS and recomputed
only in the lobby refresh, so the per-hand rake reads a cached value instead of
re-running the ledger `signal()` scan every hand. These tests assert:
  - the held value is returned per-hand and survives reserve changes within the
    window (the whole point of the hold);
  - a recompute after the window picks up the new band;
  - the flag-off path is byte-identical (always computes live);
  - a cold cache (no refresh yet) falls through to a live compute.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from cash_mode import director_policy, economy_flags
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager

SBX = "test-director-policy"


@pytest.fixture
def ledger_repo(tmp_path):
    path = str(tmp_path / "director_policy.db")
    SchemaManager(path).ensure_schema()
    r = ChipLedgerRepository(path)
    yield r
    r.close()


@pytest.fixture(autouse=True)
def _reset():
    saved = (economy_flags.RAKE_RESERVE_GATED, economy_flags.DIRECTOR_POLICY_HOLD)
    director_policy.reset_cache()
    yield
    (economy_flags.RAKE_RESERVE_GATED, economy_flags.DIRECTOR_POLICY_HOLD) = saved
    director_policy.reset_cache()


def _seed(repo, *, holdings, pool):
    repo.record('central_bank', 'player:p', holdings, 'player_seed', sandbox_id=SBX)
    if pool:
        repo.record('ai:rich', 'central_bank', pool, 'bank_pool_deposit', sandbox_id=SBX)


def _add_reserve(repo, amount):
    """Push the reserve up with another pool deposit after the initial seed."""
    repo.record('ai:rich', 'central_bank', amount, 'bank_pool_deposit', sandbox_id=SBX)


def test_read_without_refresh_is_none():
    assert director_policy.director_rake_policy("never-seen") is None


def test_refresh_caches_the_schedule(ledger_repo):
    economy_flags.RAKE_RESERVE_GATED = True
    economy_flags.DIRECTOR_POLICY_HOLD = True
    # ratio ≈ 0.042 (low band) → {1000, 200} @ 3%.
    _seed(ledger_repo, holdings=100_000, pool=4_000)
    params = director_policy.refresh_director_policy(SBX, ledger_repo, datetime(2026, 6, 4))
    assert params == (frozenset({1000, 200}), 0.03)
    assert director_policy.director_rake_policy(SBX) == (frozenset({1000, 200}), 0.03)


def test_hold_survives_reserve_change_within_window(ledger_repo):
    economy_flags.RAKE_RESERVE_GATED = True
    economy_flags.DIRECTOR_POLICY_HOLD = True
    t0 = datetime(2026, 6, 4, 12, 0, 0)
    # Boot in the low band.
    _seed(ledger_repo, holdings=100_000, pool=4_000)
    director_policy.refresh_director_policy(SBX, ledger_repo, t0)

    # Reserves jump into the flush band, but it's still within the window.
    _add_reserve(ledger_repo, 25_000)
    within = t0 + timedelta(seconds=economy_flags.POLICY_WINDOW_SECONDS - 1)
    director_policy.refresh_director_policy(SBX, ledger_repo, within)

    # Per-hand read still returns the HELD low-band schedule, not the live flush one.
    held = economy_flags.resolve_rake_params(ledger_repo, SBX)
    assert held == (frozenset({1000, 200}), 0.03)
    # A live (_fresh) read sees the new flush band — proving the hold, not a stale ledger.
    assert economy_flags.resolve_rake_params(ledger_repo, SBX, _fresh=True) == (
        frozenset({1000}),
        0.02,
    )


def test_recompute_after_window_picks_up_new_band(ledger_repo):
    economy_flags.RAKE_RESERVE_GATED = True
    economy_flags.DIRECTOR_POLICY_HOLD = True
    t0 = datetime(2026, 6, 4, 12, 0, 0)
    _seed(ledger_repo, holdings=100_000, pool=4_000)
    director_policy.refresh_director_policy(SBX, ledger_repo, t0)

    _add_reserve(ledger_repo, 25_000)  # → flush band
    after = t0 + timedelta(seconds=economy_flags.POLICY_WINDOW_SECONDS + 1)
    director_policy.refresh_director_policy(SBX, ledger_repo, after)

    assert economy_flags.resolve_rake_params(ledger_repo, SBX) == (frozenset({1000}), 0.02)


def test_flag_off_computes_live_every_call(ledger_repo):
    economy_flags.RAKE_RESERVE_GATED = True
    economy_flags.DIRECTOR_POLICY_HOLD = False
    # Even with a stale held value cached, the hold-off path ignores it.
    _seed(ledger_repo, holdings=100_000, pool=4_000)
    director_policy.refresh_director_policy(SBX, ledger_repo, datetime(2026, 6, 4))
    _add_reserve(ledger_repo, 25_000)  # → flush band, live
    assert economy_flags.resolve_rake_params(ledger_repo, SBX) == (frozenset({1000}), 0.02)


def test_cold_cache_falls_through_to_live(ledger_repo):
    economy_flags.RAKE_RESERVE_GATED = True
    economy_flags.DIRECTOR_POLICY_HOLD = True
    # No refresh has run → per-hand read must still rake correctly (live), not None.
    _seed(ledger_repo, holdings=100_000, pool=4_000)
    assert economy_flags.resolve_rake_params(ledger_repo, SBX) == (frozenset({1000, 200}), 0.03)

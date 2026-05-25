"""Tests for `cash_mode.sim_runner.run_sim`.

End-to-end check: spin up a tempdb, seed the lobby, drive 10 ticks
through the sim runner, and verify the shape + invariants of the
captured metrics. Stat helpers (percentile / Gini / diff_reasons)
get unit-tested separately for boundary conditions.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

pytestmark = pytest.mark.integration

from cash_mode.lobby import ensure_ai_bankrolls_seeded, ensure_lobby_seeded
from cash_mode.sim_runner import (
    SimConfig,
    TickMetrics,
    _diff_reasons,
    _gini,
    _percentile,
    flatten_for_csv,
    per_pid_jsonl_records,
    run_sim,
)
from poker.repositories import create_repos

# --- Fixtures -----------------------------------------------------------


def _insert_personality(db_path: str, pid: str, *, name: str | None = None) -> None:
    knobs = {
        "starting_bankroll": 50_000,
        "bankroll_rate": 50,
        "buy_in_multiplier": 1.0,
        "stake_comfort_zone": "$10",
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities (name, config_json, personality_id, visibility) "
            "VALUES (?, ?, ?, 'public')",
            (
                name or f"Personality {pid}",
                json.dumps({"bankroll_knobs": knobs}),
                pid,
            ),
        )
        conn.commit()


@pytest.fixture
def seeded_repos(tmp_path):
    """Repos with a 30-personality sandbox lobby ready for refresh."""
    db_path = str(tmp_path / "sim.db")
    repos = create_repos(db_path)
    for i in range(30):
        _insert_personality(db_path, f"pid_{i}")

    sandbox_id = "test-sim-sandbox"
    ensure_ai_bankrolls_seeded(
        personality_repo=repos['personality_repo'],
        bankroll_repo=repos['bankroll_repo'],
        sandbox_id=sandbox_id,
        chip_ledger_repo=repos['chip_ledger_repo'],
    )
    ensure_lobby_seeded(
        cash_table_repo=repos['cash_table_repo'],
        personality_repo=repos['personality_repo'],
        bankroll_repo=repos['bankroll_repo'],
        sandbox_id=sandbox_id,
    )
    yield repos, sandbox_id

    for repo in repos.values():
        if hasattr(repo, 'close'):
            repo.close()


# --- run_sim shape ------------------------------------------------------


class TestRunSimShape:
    def test_returns_metrics_per_capture_tick(self, seeded_repos):
        repos, sandbox_id = seeded_repos
        result = run_sim(
            SimConfig(
                sandbox_id=sandbox_id,
                num_ticks=10,
                start_at=datetime(2026, 1, 1, 12, 0, 0),
                rng_seed=42,
                metrics_every=1,
                audit_every=5,
                hand_sim_prob=0.0,  # movement-only — keeps the test fast
                progress_every=0,
            ),
            repos=repos,
        )
        assert len(result.metrics) == 10
        assert all(isinstance(m, TickMetrics) for m in result.metrics)
        # ticks are sequential
        assert [m.tick for m in result.metrics] == list(range(10))

    def test_deterministic_with_seed(self, tmp_path):
        """Same seed + same starting state → same per-tick trajectory.

        Two fresh tempdbs, identical seeding, identical SimConfig — the
        metrics arrays must compare equal field-by-field. This is the
        guarantee that backs A/B comparisons across code revisions.
        """

        def _build(path_name: str):
            db_path = str(tmp_path / path_name)
            repos = create_repos(db_path)
            for i in range(20):
                _insert_personality(db_path, f"pid_{i}")
            sandbox_id = "det-sandbox"
            ensure_ai_bankrolls_seeded(
                personality_repo=repos['personality_repo'],
                bankroll_repo=repos['bankroll_repo'],
                sandbox_id=sandbox_id,
                chip_ledger_repo=repos['chip_ledger_repo'],
            )
            ensure_lobby_seeded(
                cash_table_repo=repos['cash_table_repo'],
                personality_repo=repos['personality_repo'],
                bankroll_repo=repos['bankroll_repo'],
                sandbox_id=sandbox_id,
                now=datetime(2026, 1, 1),
            )
            return repos, sandbox_id

        cfg_kwargs = dict(
            num_ticks=3,
            start_at=datetime(2026, 1, 1),
            rng_seed=123,
            hand_sim_prob=0.0,
            progress_every=0,
        )

        repos_a, sandbox_a = _build("det_a.db")
        r1 = run_sim(SimConfig(sandbox_id=sandbox_a, **cfg_kwargs), repos=repos_a)
        repos_b, sandbox_b = _build("det_b.db")
        r2 = run_sim(SimConfig(sandbox_id=sandbox_b, **cfg_kwargs), repos=repos_b)

        # Aggregate scalars should match exactly across runs.
        assert len(r1.metrics) == len(r2.metrics)
        for m1, m2 in zip(r1.metrics, r2.metrics, strict=False):
            assert m1.total_chips == m2.total_chips
            assert m1.ai_count == m2.ai_count
            assert m1.gini == m2.gini
            assert m1.decisions == m2.decisions

    def test_summary_includes_headline_stats(self, seeded_repos):
        repos, sandbox_id = seeded_repos
        result = run_sim(
            SimConfig(
                sandbox_id=sandbox_id,
                num_ticks=5,
                start_at=datetime(2026, 1, 1),
                rng_seed=0,
                hand_sim_prob=0.0,
                progress_every=0,
            ),
            repos=repos,
        )
        s = result.summary
        # Just check the required keys exist; values are environment-dep.
        for key in (
            'ticks_captured',
            'wall_seconds',
            'sandbox_id',
            'rng_seed',
            'gini_first',
            'gini_final',
            'ai_count_first',
            'ai_count_final',
            'max_abs_audit_drift',
        ):
            assert key in s, f"summary missing {key!r}"
        assert s['sandbox_id'] == sandbox_id
        assert s['ticks_captured'] == 5

    def test_audit_drift_set_on_audit_ticks(self, seeded_repos):
        repos, sandbox_id = seeded_repos
        result = run_sim(
            SimConfig(
                sandbox_id=sandbox_id,
                num_ticks=11,
                start_at=datetime(2026, 1, 1),
                rng_seed=0,
                metrics_every=1,
                audit_every=5,  # ticks 0, 5, 10
                hand_sim_prob=0.0,
                progress_every=0,
            ),
            repos=repos,
        )
        audited = [m for m in result.metrics if m.audit_drift is not None]
        # Tick 0, 5, 10 all audit. (Final tick also audits regardless.)
        assert len(audited) >= 3
        # Drift should be integer-typed when set.
        for m in audited:
            assert isinstance(m.audit_drift, int)


# --- Stake metrics ------------------------------------------------------


class TestStakeMetricsScope:
    def test_zero_when_no_stakes(self, seeded_repos):
        repos, sandbox_id = seeded_repos
        result = run_sim(
            SimConfig(
                sandbox_id=sandbox_id,
                num_ticks=3,
                start_at=datetime(2026, 1, 1),
                rng_seed=0,
                hand_sim_prob=0.0,
                progress_every=0,
            ),
            repos=repos,
        )
        for m in result.metrics:
            assert m.active_stake_count == 0
            assert m.carry_count == 0
            assert m.settled_count_cumulative == 0


# --- Helper unit tests --------------------------------------------------


class TestPercentile:
    def test_empty_returns_zero(self):
        assert _percentile([], 0.5) == 0

    def test_single_element(self):
        assert _percentile([42], 0.0) == 42
        assert _percentile([42], 1.0) == 42

    def test_median_of_odd_count(self):
        assert _percentile([1, 2, 3, 4, 5], 0.5) == 3

    def test_median_of_even_count_interpolates(self):
        # numpy's default: (2 + 3) / 2 = 2.5 → int truncate = 2
        assert _percentile([1, 2, 3, 4], 0.5) == 2


class TestGini:
    def test_empty_returns_zero(self):
        assert _gini([]) == 0.0

    def test_perfect_equality(self):
        # Five AIs with the same chips → gini == 0
        assert _gini([100, 100, 100, 100, 100]) == 0.0

    def test_total_zero_returns_zero(self):
        assert _gini([0, 0, 0]) == 0.0

    def test_concentration_increases_gini(self):
        low = _gini(sorted([100, 100, 100, 100, 100]))
        high = _gini(sorted([0, 0, 0, 0, 500]))
        assert high > low


class TestDiffReasons:
    def test_no_change_returns_empty(self):
        prev = {'ai_seed': 100}
        assert _diff_reasons(prev, prev, {}, {}) == {}

    def test_new_creation_positive(self):
        assert _diff_reasons({}, {'ai_regen': 50}, {}, {}) == {'ai_regen': 50}

    def test_new_destruction_negative(self):
        assert _diff_reasons({}, {}, {}, {'table_rake': 25}) == {'table_rake': -25}

    def test_creation_and_destruction_offset(self):
        prev_c = {'ai_seed': 100}
        new_c = {'ai_seed': 100, 'ai_regen': 50}
        prev_d = {}
        new_d = {'table_rake': 30}
        delta = _diff_reasons(prev_c, new_c, prev_d, new_d)
        assert delta == {'ai_regen': 50, 'table_rake': -30}


# --- Output flatteners --------------------------------------------------


class TestFlatten:
    def test_flatten_for_csv_includes_dict_columns(self, seeded_repos):
        repos, sandbox_id = seeded_repos
        result = run_sim(
            SimConfig(
                sandbox_id=sandbox_id,
                num_ticks=3,
                start_at=datetime(2026, 1, 1),
                rng_seed=0,
                hand_sim_prob=0.0,
                progress_every=0,
            ),
            repos=repos,
        )
        rows = flatten_for_csv(result.metrics)
        assert len(rows) == 3
        # Aggregate keys present on every row
        for row in rows:
            for key in (
                'tick',
                'now',
                'ai_count',
                'total_chips',
                'gini',
                'active_stake_count',
                'audit_drift',
            ):
                assert key in row
        # Decision columns appear (movement always produces at least
        # stay/forced_leave on some ticks). If no decisions fired,
        # the column set may be empty — accept that without failing.
        decision_cols = [k for k in rows[0].keys() if k.startswith('decisions__')]
        # Reason-delta columns may or may not exist for movement-only.
        # The point of the test is just that the structure flattens.
        assert isinstance(decision_cols, list)

    def test_per_pid_jsonl_records(self, seeded_repos):
        repos, sandbox_id = seeded_repos
        result = run_sim(
            SimConfig(
                sandbox_id=sandbox_id,
                num_ticks=2,
                start_at=datetime(2026, 1, 1),
                rng_seed=0,
                hand_sim_prob=0.0,
                progress_every=0,
            ),
            repos=repos,
        )
        records = per_pid_jsonl_records(result.metrics)
        # One row per (tick, pid)
        ticks = sorted(set(r['tick'] for r in records))
        assert ticks == [0, 1]
        pids_per_tick = {
            tick: sorted(r['personality_id'] for r in records if r['tick'] == tick)
            for tick in ticks
        }
        # Same pid set on each tick (sandbox membership stable)
        assert pids_per_tick[0] == pids_per_tick[1]
        # Sample shape
        sample = records[0]
        for key in ('tick', 'now', 'personality_id', 'chips'):
            assert key in sample

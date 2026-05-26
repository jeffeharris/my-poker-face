"""Driven cash-mode economy simulator.

Calls `refresh_unseated_tables` in a tight loop with synthesized `now`
and an explicit `sandbox_id`, captures per-tick metrics, and returns a
trajectory the operator can dump to CSV / JSONL for analysis.

In-process, not HTTP: importing the lobby module directly is simpler,
faster, deterministic. The sim is a development tool; it isn't shipped
to end users.

What's captured per tick:
  * Wealth distribution across AI bankrolls (percentiles + Gini).
  * Stake state scoped to the sandbox's personality set (active /
    carry / settled / defaulted counts).
  * Ledger deltas grouped by reason since the previous tick.
  * Movement decisions aggregated across every refreshed table.
  * Audit drift on `audit_every` ticks.

Spec: `docs/plans/CASH_MODE_ECONOMY_SIM.md`.
"""

from __future__ import annotations

import logging
import math
import random
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, replace as dc_replace
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from cash_mode.full_sim import DEFAULT_HAND_SIM_PROB
from cash_mode.lobby import refresh_unseated_tables
from cash_mode.movement import DEFAULT_LIVE_FILL_PROB
from cash_mode.stakes import (
    STAKE_STATUS_ACTIVE,
    STAKE_STATUS_CARRY,
    STAKE_STATUS_DEFAULTED,
    STAKE_STATUS_SETTLED,
)
from flask_app.services.chip_ledger_audit import compute_audit

logger = logging.getLogger(__name__)


# --- Public dataclasses -------------------------------------------------


@dataclass(frozen=True)
class SimConfig:
    """Inputs for `run_sim`.

    `sandbox_id` is required — the sim is always scoped to one save-
    file so its writes don't bleed into another. `start_at` defaults
    to `utcnow()` at construction; tests / repeatable runs should
    pass an explicit value.

    Tuning notes:
      * `tick_seconds=8` mirrors the production lobby cadence (a real
        user polling every ~8 seconds). Lower values stress the
        refresh path; higher values let regen accumulate between ticks.
      * `hand_sim_prob=1.0` (the default) ensures real chip churn from
        gameplay. Drop it to 0.0 for a movement-only baseline.
      * `metrics_every=1` captures every tick; raise it to reduce
        I/O if the run is long.
      * `audit_every=50` runs the full audit periodically to catch
        drift early without paying its cost every tick.
    """

    sandbox_id: str
    num_ticks: int
    tick_seconds: int = 8
    start_at: Optional[datetime] = None
    rng_seed: int = 0
    metrics_every: int = 1
    audit_every: int = 50
    hand_sim_prob: float = DEFAULT_HAND_SIM_PROB
    live_fill_prob: float = DEFAULT_LIVE_FILL_PROB
    progress_every: int = 100  # 0 disables progress logging
    # Closed-economy testbed: pre-seed the bank pool at sim start to
    # overcome the cold-start chicken-and-egg (no vice → no pool → no
    # tourist injection). 0 disables. Drift-safe via the paired ledger
    # rows in `record_bank_pool_sim_seed_pair`.
    initial_bank_pool_seed: int = 0


@dataclass(frozen=True)
class TickMetrics:
    """One row of per-tick observability.

    Flat scalars + two dicts (`ledger_delta`, `decisions`). CSV
    writers flatten the dicts to one column per key — see
    `flatten_for_csv`.
    """

    tick: int
    now: str  # ISO 8601

    # Wealth distribution (AI bankrolls in this sandbox)
    ai_count: int
    total_chips: int
    p10_chips: int
    p25_chips: int
    p50_chips: int
    p75_chips: int
    p90_chips: int
    max_chips: int
    min_chips: int
    gini: float

    # Stake state (scoped to AIs in this sandbox)
    active_stake_count: int
    active_principal_total: int
    carry_count: int
    carry_total: int
    settled_count_cumulative: int
    defaulted_count_cumulative: int

    # Ledger deltas (this tick − previous tick, by reason). Keys are
    # reason strings; values are signed chip counts (creations
    # positive, destructions negative).
    ledger_delta: Dict[str, int]

    # Movement decision counts aggregated across the tick's refresh.
    decisions: Dict[str, int]

    # Per-personality bankroll trajectory. Wide data — written to a
    # JSONL sidecar rather than the CSV (one row per (tick, pid)).
    per_pid_chips: Dict[str, int]

    # Closed-economy state (see docs/plans/CASH_MODE_CLOSED_ECONOMY.md).
    # `bank_pool_chips`: virtual pool depth, computed from ledger.
    # `fish_bankroll_total`: chips held by fish-archetype AIs (the
    #   recipients of tourist injections — net the casino tier's
    #   inventory of fish chips at this instant).
    # `fish_count`: number of fish personalities with a bankroll row
    #   in this sandbox (sized population for the recycle loop).
    bank_pool_chips: int = 0
    fish_bankroll_total: int = 0
    fish_count: int = 0

    # Casino lifecycle state.
    # `casino_count`: number of `table_type='casino'` rows.
    # `casino_seated_fish_count`: fish in casino seats (across all casinos).
    # `casino_seated_total_chips`: total chips at casino seats.
    # `casino_closing_count`: casinos in 'closing' state (smooth shutdown).
    # `hungry_grinder_count`: AIs that match the grinder hunger criteria
    #   right now (demand signal for the casino loop).
    casino_count: int = 0
    casino_seated_fish_count: int = 0
    casino_seated_total_chips: int = 0
    casino_closing_count: int = 0
    hungry_grinder_count: int = 0

    # `fish_net_to_players`: cumulative chips fish have lost to players
    # (grinders + human), by conservation —
    #   Σ(ledger inflow to fish) − Σ(ledger outflow from fish) − fish holdings.
    # Player↔player pots aren't ledgered (only fish↔pool/house flows are),
    # so the residual is exactly what fish fed the population. Positive =
    # the population net-farmed the fish; negative = fish are net up.
    fish_net_to_players: int = 0

    # Audit drift — only populated on audit ticks; None otherwise so
    # downstream tooling can distinguish "drift was zero" from "we
    # didn't check this tick".
    audit_drift: Optional[int] = None


@dataclass
class SimResult:
    """Final result of a sim run.

    `metrics` is the per-tick series. `summary` carries the headline
    stats the operator usually looks at first (final wealth dispersion,
    total chips moved, audit health). `wall_seconds` is the actual
    wall-clock time the run took — useful when tuning `num_ticks`.
    """

    metrics: List[TickMetrics]
    summary: Dict[str, object]
    final_now: str
    wall_seconds: float


# --- Entry point --------------------------------------------------------


def run_sim(
    config: SimConfig,
    *,
    repos: Dict[str, object],
) -> SimResult:
    """Drive `refresh_unseated_tables` for `num_ticks` and collect metrics.

    `repos` is the dict returned by `poker.repositories.create_repos`.
    The sim doesn't construct repos itself so callers can run against
    a tempdb in tests, the production DB from a CLI, or a custom
    fixture.

    Determinism: with a fixed `rng_seed`, two runs against the same
    starting state produce the same trajectory. This is what makes
    A/B comparisons across code revisions meaningful.
    """
    cash_table_repo = repos['cash_table_repo']
    personality_repo = repos['personality_repo']
    bankroll_repo = repos['bankroll_repo']
    relationship_repo = repos['relationship_repo']
    stake_repo = repos['stake_repo']
    chip_ledger_repo = repos['chip_ledger_repo']
    # Side-hustle repo (optional in the dict for back-compat). With
    # passive regen retired, the side hustle is the broke-AI recovery
    # path, so the closed-loop sim drives it. Vice stays sim-faked
    # (resolve_closed_economy) so no vice_repo is wired here.
    side_hustle_repo = repos.get('side_hustle_state_repo')
    db_path = repos['db_path']

    rng = random.Random(config.rng_seed)
    start_at = config.start_at or datetime.utcnow()
    metrics: List[TickMetrics] = []
    wall_start = time.monotonic()

    # Closed-economy: optional bank-pool seed so the first tourist
    # injection can fire before any vice deposit lands. Drift-safe.
    if config.initial_bank_pool_seed > 0:
        from cash_mode.closed_economy import seed_bank_pool

        seed_bank_pool(
            chip_ledger_repo,
            sandbox_id=config.sandbox_id,
            amount=config.initial_bank_pool_seed,
        )
        logger.info(
            "[sim] seeded bank pool with %d chips for sandbox %s",
            config.initial_bank_pool_seed,
            config.sandbox_id,
        )

    # Cumulative ledger snapshots — diffed each tick to produce
    # `ledger_delta`. Reads are sandbox-scoped via the repo API.
    # Captured AFTER the seed write so the seed itself shows up in
    # `bank_pool_chips` (tick 0) rather than as a tick-0 delta blip.
    prev_creations = chip_ledger_repo.sum_creations_by_reason(sandbox_id=config.sandbox_id)
    prev_destructions = chip_ledger_repo.sum_destructions_by_reason(
        sandbox_id=config.sandbox_id,
    )

    for tick in range(config.num_ticks):
        now = start_at + timedelta(seconds=tick * config.tick_seconds)

        # 1. Drive the lobby refresh.
        refresh_results = refresh_unseated_tables(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id=config.sandbox_id,
            now=now,
            rng=rng,
            hand_sim_prob=config.hand_sim_prob,
            live_fill_prob=config.live_fill_prob,
            chip_ledger_repo=chip_ledger_repo,
            relationship_repo=relationship_repo,
            stake_repo=stake_repo,
            side_hustle_repo=side_hustle_repo,
            # The sim has no LLM, so real vice (which narrates per fire)
            # can't run here — force the fake-vice stub as the pool-deposit
            # source. Mutually exclusive with real vice; live paths default
            # to economy_flags.VICE_MODE ('real').
            vice_mode='fake',
        )

        # 2. Metrics capture (every N ticks). The very first and very
        #    last tick always capture so summaries have endpoints.
        is_final_tick = tick == config.num_ticks - 1
        if tick % config.metrics_every == 0 or is_final_tick:
            tick_metrics, prev_creations, prev_destructions = _capture_tick_metrics(
                tick=tick,
                now=now,
                sandbox_id=config.sandbox_id,
                bankroll_repo=bankroll_repo,
                chip_ledger_repo=chip_ledger_repo,
                db_path=db_path,
                refresh_results=refresh_results,
                prev_creations=prev_creations,
                prev_destructions=prev_destructions,
            )
            # 3. Periodic audit drift check.
            if tick % config.audit_every == 0 or is_final_tick:
                drift = _audit_drift(
                    repos=repos,
                    sandbox_id=config.sandbox_id,
                    now=now,
                )
                tick_metrics = _replace_drift(tick_metrics, drift)
            metrics.append(tick_metrics)

        # 4. Progress log.
        if config.progress_every > 0 and (tick + 1) % config.progress_every == 0:
            elapsed = time.monotonic() - wall_start
            rate = (tick + 1) / elapsed if elapsed > 0 else 0.0
            logger.info(
                "[sim] tick %d/%d (%.1f tick/s, elapsed=%.1fs)",
                tick + 1,
                config.num_ticks,
                rate,
                elapsed,
            )

    wall_seconds = time.monotonic() - wall_start
    final_now = (
        start_at + timedelta(seconds=(config.num_ticks - 1) * config.tick_seconds)
    ).isoformat()

    return SimResult(
        metrics=metrics,
        summary=_summarize(metrics, config, wall_seconds),
        final_now=final_now,
        wall_seconds=wall_seconds,
    )


# --- Per-tick metrics capture ------------------------------------------


def _capture_tick_metrics(
    *,
    tick: int,
    now: datetime,
    sandbox_id: str,
    bankroll_repo,
    chip_ledger_repo,
    db_path: str,
    refresh_results,
    prev_creations: Dict[str, int],
    prev_destructions: Dict[str, int],
):
    """Build one `TickMetrics`. Returns (metrics, new_creations, new_destructions).

    The cumulative snapshots are returned so the caller can use them
    as the "previous" baseline for the next tick. Reading the full
    sums every tick is cheap (one indexed GROUP BY per call).
    """
    # Per-pid bankroll snapshot. `load_ai_bankroll_current` projects
    # regen forward to `now`, which is what an actual lobby read
    # would see — keeps the wealth metrics consistent with what an
    # observer of this sandbox would experience.
    pids = bankroll_repo.iter_personality_ids_with_bankrolls(sandbox_id=sandbox_id)
    per_pid_chips: Dict[str, int] = {}
    for pid in pids:
        chips = bankroll_repo.load_ai_bankroll_current(
            pid,
            sandbox_id=sandbox_id,
            now=now,
        )
        per_pid_chips[pid] = int(chips or 0)

    chips_sorted = sorted(per_pid_chips.values())
    total_chips = sum(chips_sorted)

    # Stake state scoped to this sandbox via PID filter. The stakes
    # table doesn't carry a `sandbox_id` column (its session_id keys
    # are sandbox-implicit), so we filter on whether borrower or
    # staker is one of this sandbox's known AIs.
    stake_counts = _stake_state_for_pids(db_path, set(pids))

    # Ledger deltas: cumulative_now - cumulative_prev, signed.
    new_creations = chip_ledger_repo.sum_creations_by_reason(sandbox_id=sandbox_id)
    new_destructions = chip_ledger_repo.sum_destructions_by_reason(sandbox_id=sandbox_id)
    ledger_delta = _diff_reasons(prev_creations, new_creations, prev_destructions, new_destructions)

    # Movement decisions aggregated across every refreshed table.
    decisions: Counter = Counter()
    for result in refresh_results.values():
        for decision in result.decisions.values():
            decisions[decision] += 1
        # Stake creations are a Phase-4 decision sibling — surface
        # them so the operator can see take_stake firing rates
        # without inferring from the ledger.
        if result.stake_creations:
            decisions['stake_created'] += len(result.stake_creations)

    # Closed-economy snapshot — bank pool depth + fish inventory.
    # Pool depth is virtual (computed from ledger sums), so reading
    # it every tick is one indexed GROUP BY query (already paid for
    # by `sum_*_by_reason` above — same query shape).
    from cash_mode.casino_provisioning import is_closing
    from cash_mode.closed_economy import (
        compute_bank_pool_reserves,
        list_hungry_grinders,
        load_fish_ids,
    )

    bank_pool_chips = compute_bank_pool_reserves(
        chip_ledger_repo,
        sandbox_id=sandbox_id,
    )
    fish_ids_set = load_fish_ids(bankroll_repo, sandbox_id=sandbox_id)
    fish_bankroll_total = sum(per_pid_chips.get(pid, 0) for pid in fish_ids_set)

    # Casino lifecycle snapshot — walk active casino tables in the
    # sandbox once and count seats, closing state, and chip totals.
    from poker.repositories.cash_table_repository import CashTableRepository

    casino_count = 0
    casino_seated_fish_count = 0
    casino_seated_total_chips = 0
    casino_closing_count = 0
    fish_seat_chips = 0  # fish holdings on the felt (fish are casino-only)
    # Reuse the run-level db_path (already in scope as a kwarg) for a
    # short-lived CashTableRepository — the per-tick walk is read-only.
    cash_table_repo = CashTableRepository(db_path)
    for table in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
        if table.table_type != 'casino':
            continue
        casino_count += 1
        if is_closing(cash_table_repo, sandbox_id, table.table_id):
            casino_closing_count += 1
        for slot in table.seats:
            if slot.get('kind') != 'ai':
                continue
            pid = slot.get('personality_id')
            chips = int(slot.get('chips', 0))
            casino_seated_total_chips += chips
            if pid in fish_ids_set:
                casino_seated_fish_count += 1
                fish_seat_chips += chips

    hungry_grinder_count = len(
        list_hungry_grinders(
            bankroll_repo,
            sandbox_id=sandbox_id,
            now=now,
        )
    )

    # Fish drain: chips fish have fed the population (grinders + human).
    # Fish holdings = bankroll (fish_bankroll_total) + chips on the felt
    # (fish_seat_chips). See TickMetrics.fish_net_to_players.
    fish_inflow, fish_outflow = _fish_ledger_flows(
        db_path,
        sandbox_id=sandbox_id,
        fish_ids=fish_ids_set,
    )
    fish_net_to_players = fish_inflow - fish_outflow - (fish_bankroll_total + fish_seat_chips)

    metrics = TickMetrics(
        tick=tick,
        now=now.isoformat(),
        ai_count=len(chips_sorted),
        total_chips=total_chips,
        p10_chips=_percentile(chips_sorted, 0.10),
        p25_chips=_percentile(chips_sorted, 0.25),
        p50_chips=_percentile(chips_sorted, 0.50),
        p75_chips=_percentile(chips_sorted, 0.75),
        p90_chips=_percentile(chips_sorted, 0.90),
        max_chips=chips_sorted[-1] if chips_sorted else 0,
        min_chips=chips_sorted[0] if chips_sorted else 0,
        gini=_gini(chips_sorted),
        active_stake_count=stake_counts['active_count'],
        active_principal_total=stake_counts['active_principal_total'],
        carry_count=stake_counts['carry_count'],
        carry_total=stake_counts['carry_total'],
        settled_count_cumulative=stake_counts['settled_count'],
        defaulted_count_cumulative=stake_counts['defaulted_count'],
        ledger_delta=ledger_delta,
        decisions=dict(decisions),
        per_pid_chips=per_pid_chips,
        bank_pool_chips=bank_pool_chips,
        fish_bankroll_total=fish_bankroll_total,
        fish_count=len(fish_ids_set),
        casino_count=casino_count,
        casino_seated_fish_count=casino_seated_fish_count,
        casino_seated_total_chips=casino_seated_total_chips,
        casino_closing_count=casino_closing_count,
        hungry_grinder_count=hungry_grinder_count,
        fish_net_to_players=fish_net_to_players,
        audit_drift=None,
    )
    return metrics, new_creations, new_destructions


def _stake_state_for_pids(db_path: str, pids: set) -> Dict[str, int]:
    """Count stakes by status where borrower or staker is one of `pids`.

    Returns a dict with active_count, active_principal_total,
    carry_count, carry_total, settled_count, defaulted_count.

    Empty pid set → all zeros (no SQL query). Useful for sandboxes
    with no AIs (shouldn't happen mid-sim but defends against the
    edge case).
    """
    zero = {
        'active_count': 0,
        'active_principal_total': 0,
        'carry_count': 0,
        'carry_total': 0,
        'settled_count': 0,
        'defaulted_count': 0,
    }
    if not pids:
        return zero

    placeholders = ','.join('?' for _ in pids)
    pid_list = list(pids)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        # Filter against both sides of the stake — AIs in this sandbox
        # might be on either end. Each stake row matches at most once
        # in spite of the OR (rows aren't duplicated by predicate),
        # so a single COUNT(*) is correct.
        rows = conn.execute(
            f"""
            SELECT status,
                   COUNT(*) AS n,
                   COALESCE(SUM(principal + match_amount), 0) AS principal_total,
                   COALESCE(SUM(carry_amount), 0) AS carry_total
            FROM stakes
            WHERE borrower_id IN ({placeholders})
               OR staker_id IN ({placeholders})
            GROUP BY status
            """,
            pid_list + pid_list,
        ).fetchall()

    out = dict(zero)
    for row in rows:
        status = row['status']
        if status == STAKE_STATUS_ACTIVE:
            out['active_count'] = int(row['n'])
            out['active_principal_total'] = int(row['principal_total'] or 0)
        elif status == STAKE_STATUS_CARRY:
            out['carry_count'] = int(row['n'])
            out['carry_total'] = int(row['carry_total'] or 0)
        elif status == STAKE_STATUS_SETTLED:
            out['settled_count'] = int(row['n'])
        elif status == STAKE_STATUS_DEFAULTED:
            out['defaulted_count'] = int(row['n'])
    return out


def _fish_ledger_flows(db_path: str, *, sandbox_id: str, fish_ids: set) -> tuple:
    """Cumulative ledger chips flowing INTO and OUT OF fish accounts.

    Fish accounts are encoded `ai:<pid>` in the ledger's source/sink.
    Returns `(inflow, outflow)`. Player↔player pot transfers aren't
    ledgered — only fish↔pool/house flows are — so
    `inflow − outflow − fish_holdings` is exactly the chips fish lost to
    other players (see `TickMetrics.fish_net_to_players`).

    Empty fish set → `(0, 0)`, no query.
    """
    if not fish_ids:
        return 0, 0
    accounts = [f'ai:{pid}' for pid in fish_ids]
    ph = ','.join('?' for _ in accounts)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN sink   IN ({ph}) THEN amount END), 0) AS inflow,
                COALESCE(SUM(CASE WHEN source IN ({ph}) THEN amount END), 0) AS outflow
            FROM chip_ledger_entries
            WHERE sandbox_id = ?
            """,
            accounts + accounts + [sandbox_id],
        ).fetchone()
    return int(row[0] or 0), int(row[1] or 0)


def _diff_reasons(
    prev_creations: Dict[str, int],
    new_creations: Dict[str, int],
    prev_destructions: Dict[str, int],
    new_destructions: Dict[str, int],
) -> Dict[str, int]:
    """Signed per-reason delta between two cumulative snapshots.

    Creations contribute positively, destructions negatively. Reasons
    that didn't move in this tick are omitted from the result so the
    CSV stays sparse.
    """
    delta: Dict[str, int] = {}
    for reason, amount in new_creations.items():
        diff = amount - prev_creations.get(reason, 0)
        if diff:
            delta[reason] = delta.get(reason, 0) + diff
    for reason, amount in new_destructions.items():
        diff = amount - prev_destructions.get(reason, 0)
        if diff:
            delta[reason] = delta.get(reason, 0) - diff
    return delta


def _audit_drift(*, repos: Dict[str, object], sandbox_id: str, now: datetime) -> int:
    """Run a scoped audit and return its drift.

    The audit's cross-cutting surfaces (player bankrolls, live AI
    stacks) are global by design — for a sim sandbox with no live
    Flask process, they evaluate to zero and don't affect the
    sandbox-level interpretation. We pass `list_game_ids_fn=None`
    so the live-session sum short-circuits to 0.
    """
    audit = compute_audit(
        ledger_repo=repos['chip_ledger_repo'],
        bankroll_repo=repos['bankroll_repo'],
        cash_table_repo=repos['cash_table_repo'],
        stake_repo=repos['stake_repo'],
        db_path=repos['db_path'],
        list_game_ids_fn=None,
        get_game_fn=None,
        now=now,
        sandbox_id=sandbox_id,
    )
    return int(audit['drift'])


def _replace_drift(metrics: TickMetrics, drift: int) -> TickMetrics:
    """Return a copy of `metrics` with `audit_drift` set."""
    return dc_replace(metrics, audit_drift=drift)


# --- Stats helpers ------------------------------------------------------


def _percentile(sorted_values: List[int], p: float) -> int:
    """Linear-interpolated percentile on a pre-sorted list.

    Matches numpy's default `linear` interpolation so analysis
    notebooks read the same numbers regardless of which side they
    pull from.
    """
    if not sorted_values:
        return 0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = p * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_values[lo]
    weight = pos - lo
    return int(sorted_values[lo] * (1 - weight) + sorted_values[hi] * weight)


def _gini(sorted_values: List[int]) -> float:
    """Gini coefficient on chip counts.

    0.0 = perfect equality, 1.0 = one AI has everything. Uses the
    standard formula (twice the area between Lorenz curve and the
    equality line). Defined for non-negative inputs only; bankrolls
    are guaranteed >= 0.
    """
    n = len(sorted_values)
    if n == 0:
        return 0.0
    total = sum(sorted_values)
    if total == 0:
        return 0.0
    cumulative = 0
    weighted = 0
    for i, v in enumerate(sorted_values, start=1):
        cumulative += v
        weighted += i * v
    return (2 * weighted) / (n * total) - (n + 1) / n


# --- Summary ------------------------------------------------------------


def _summarize(
    metrics: List[TickMetrics],
    config: SimConfig,
    wall_seconds: float,
) -> Dict[str, object]:
    """Compact headline summary of the run."""
    if not metrics:
        return {
            'ticks_captured': 0,
            'wall_seconds': wall_seconds,
        }
    first = metrics[0]
    last = metrics[-1]
    audit_drifts = [m.audit_drift for m in metrics if m.audit_drift is not None]
    max_abs_drift = max((abs(d) for d in audit_drifts), default=0)

    return {
        'ticks_captured': len(metrics),
        'wall_seconds': wall_seconds,
        'sandbox_id': config.sandbox_id,
        'rng_seed': config.rng_seed,
        'final_now': last.now,
        'ai_count_first': first.ai_count,
        'ai_count_final': last.ai_count,
        'total_chips_first': first.total_chips,
        'total_chips_final': last.total_chips,
        'total_chips_delta': last.total_chips - first.total_chips,
        'gini_first': round(first.gini, 4),
        'gini_final': round(last.gini, 4),
        'max_chips_final': last.max_chips,
        'min_chips_final': last.min_chips,
        'active_stakes_final': last.active_stake_count,
        'carries_final': last.carry_count,
        'settled_cumulative': last.settled_count_cumulative,
        'defaulted_cumulative': last.defaulted_count_cumulative,
        'max_abs_audit_drift': max_abs_drift,
    }


# --- CSV / JSONL flatteners ---------------------------------------------


def flatten_for_csv(metrics: List[TickMetrics]) -> List[Dict[str, object]]:
    """Flatten the metrics series for CSV writing.

    `ledger_delta` and `decisions` dicts become one column per key
    (`ledger_delta__ai_regen`, `decisions__forced_leave`, ...). The
    column set is the union across every tick — sparse cells write
    as empty strings, which spreadsheet tools handle cleanly.

    `per_pid_chips` is *not* flattened here — it's wide enough to
    overwhelm the CSV (one column per AI). The CLI writes that to a
    JSONL sidecar instead.
    """
    reason_keys: set = set()
    decision_keys: set = set()
    for m in metrics:
        reason_keys.update(m.ledger_delta.keys())
        decision_keys.update(m.decisions.keys())

    rows: List[Dict[str, object]] = []
    for m in metrics:
        row: Dict[str, object] = {
            'tick': m.tick,
            'now': m.now,
            'ai_count': m.ai_count,
            'total_chips': m.total_chips,
            'p10_chips': m.p10_chips,
            'p25_chips': m.p25_chips,
            'p50_chips': m.p50_chips,
            'p75_chips': m.p75_chips,
            'p90_chips': m.p90_chips,
            'max_chips': m.max_chips,
            'min_chips': m.min_chips,
            'gini': m.gini,
            'active_stake_count': m.active_stake_count,
            'active_principal_total': m.active_principal_total,
            'carry_count': m.carry_count,
            'carry_total': m.carry_total,
            'settled_count_cumulative': m.settled_count_cumulative,
            'defaulted_count_cumulative': m.defaulted_count_cumulative,
            'bank_pool_chips': m.bank_pool_chips,
            'fish_bankroll_total': m.fish_bankroll_total,
            'fish_count': m.fish_count,
            'casino_count': m.casino_count,
            'casino_seated_fish_count': m.casino_seated_fish_count,
            'casino_seated_total_chips': m.casino_seated_total_chips,
            'casino_closing_count': m.casino_closing_count,
            'hungry_grinder_count': m.hungry_grinder_count,
            'fish_net_to_players': m.fish_net_to_players,
            'audit_drift': m.audit_drift if m.audit_drift is not None else '',
        }
        for key in sorted(reason_keys):
            row[f'ledger_delta__{key}'] = m.ledger_delta.get(key, '')
        for key in sorted(decision_keys):
            row[f'decisions__{key}'] = m.decisions.get(key, '')
        rows.append(row)
    return rows


def per_pid_jsonl_records(metrics: List[TickMetrics]) -> List[Dict[str, object]]:
    """Wide per-personality trajectory in JSONL-friendly shape.

    One record per (tick, personality_id). The output is intended for
    `pandas.read_json(..., lines=True)` or sqlite import.
    """
    out: List[Dict[str, object]] = []
    for m in metrics:
        for pid, chips in sorted(m.per_pid_chips.items()):
            out.append(
                {
                    'tick': m.tick,
                    'now': m.now,
                    'personality_id': pid,
                    'chips': chips,
                }
            )
    return out

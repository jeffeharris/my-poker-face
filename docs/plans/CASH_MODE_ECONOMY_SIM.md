---
purpose: Plan for a driven economy simulator that runs the cash-mode lobby refresh in a loop against a designated sandbox, captures per-tick metrics, and paired updates to the admin chip-ledger view so we can interpret what the sim produces.
type: design
created: 2026-05-21
last_updated: 2026-05-21
---

# Cash Mode — Economy Sim + Sandbox-Aware Observability

> **Why this exists:** The current sandbox already shows real
> imbalances (Zeus at $210k drifting upward, four AIs stuck at $0,
> 44 personalities running on default config, 502k chips destroyed
> by the now-removed cap_clamp). The design constants in the vice
> spending and staker incentives docs are educated guesses, not
> measured values. We need empirical evidence — what does the
> economy look like after 1000 ticks of driven simulation? — and
> we need observability good enough to read the results.

## Two paired pieces

This plan covers two efforts that ship together:

1. **The sim harness** — a driven loop that calls
   `refresh_unseated_tables` against a designated sandbox without
   human polling, captures per-tick metrics, exports time-series
   data for analysis.

2. **Sandbox-aware chip-ledger view** — the admin audit currently
   computes a cross-sandbox aggregate. The backend audit function
   already accepts `sandbox_id`, but the route and the frontend
   panel don't. We surface this so we can drill into a sim sandbox
   and watch it move.

They're paired because the sim is useless without observability and
the observability gains nothing without sim data to look at.

## The motivating snapshot

Current sandbox (one of one, ~95 AIs, ~30 days of light activity):

- **Wealth dispersion is high.** Zeus $210k vs Annie Oakley $327 → 643× spread. cap_clamp destroyed $502k before it was removed; nothing has replaced that sink.
- **Stake activity is light.** 19 total stake rows (16 settled, 3 active, 0 carries). 9 of those are human→AI (Phase 5 in use); 9 are AI↔AI (Phase 4 take_stake firing). Sample size too small to draw economy conclusions.
- **Four AIs at $0 chips.** Three have NULL `starting_bankroll` (config gap); one (Alice) was seeded then lost it all without recovering.
- **44 of 97 personalities lack bankroll_knobs config.** Running on defaults. Muddies any sim that doesn't fix this first.
- **table_rake firing.** 1,883 events, $35k total destroyed. Quiet but compounding.
- **No carries, no defaults.** Hard to evaluate Phase 4.5 priority without ever seeing one in the wild.

The sandbox tells us what we don't know: how does this economy
behave under sustained load when constants are tuned for an
assumed activity rate that hasn't actually been observed?

## Sim harness design

### Architecture: in-process, not HTTP

The lobby refresh is pure Python (no HTTP layer). The sim imports
`cash_mode.lobby.refresh_unseated_tables` and calls it directly
with synthesized `now` and an explicit `sandbox_id`. No auth
bypass needed, no Flask context, no HTTP roundtrip per tick.

This makes 1000 ticks fast (probably 50-200 seconds end-to-end
depending on full sim cost) and deterministic when seeded.

### Inputs

```python
@dataclass
class SimConfig:
    sandbox_id: str            # target sandbox (existing or fresh)
    num_ticks: int             # how many refresh cycles to run
    tick_seconds: int = 8      # simulated time per tick
    start_at: datetime = ...   # synthesized "now" at tick 0
    rng_seed: int = 0          # deterministic randomness
    metrics_every: int = 1     # capture every N ticks (1 = every tick)
    audit_every: int = 50      # full-audit run every N ticks
    hand_sim_prob: float = ... # passthrough to refresh_unseated_tables
    live_fill_prob: float = ...
    disable_llm: bool = True   # short-circuits LLM-driven features
```

`disable_llm=True` is critical — vice narration (once shipped) and
any future LLM-driven AI behaviors would otherwise dominate sim
cost. The sim runs with mocked / templated responses.

### Per-tick loop

```python
def run_sim(config: SimConfig) -> SimResult:
    now = config.start_at
    rng = random.Random(config.rng_seed)
    metrics: List[TickMetrics] = []

    for tick in range(config.num_ticks):
        # 1. Advance synthesized time.
        now = config.start_at + timedelta(seconds=tick * config.tick_seconds)

        # 2. Drive the lobby refresh.
        refresh_unseated_tables(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id=config.sandbox_id,
            now=now,
            rng=rng,
            relationship_repo=relationship_repo,
            stake_repo=stake_repo,
            chip_ledger_repo=chip_ledger_repo,
            hand_sim_prob=config.hand_sim_prob,
            live_fill_prob=config.live_fill_prob,
        )

        # 3. Capture metrics every N ticks.
        if tick % config.metrics_every == 0:
            metrics.append(capture_tick_metrics(
                tick=tick, now=now,
                sandbox_id=config.sandbox_id,
                bankroll_repo=bankroll_repo,
                stake_repo=stake_repo,
                chip_ledger_repo=chip_ledger_repo,
            ))

        # 4. Periodic audit to catch drift.
        if tick % config.audit_every == 0:
            audit = compute_audit(..., sandbox_id=config.sandbox_id)
            if abs(audit["drift"]) > AUDIT_DRIFT_TOLERANCE:
                logger.warning(
                    "Drift exceeded at tick %d: %d", tick, audit["drift"],
                )

    return SimResult(metrics=metrics, final_now=now)
```

### Metrics per tick

```python
@dataclass(frozen=True)
class TickMetrics:
    tick: int
    now: str  # ISO

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
    gini: float    # wealth concentration

    # Stake state
    active_stake_count: int
    active_principal_total: int
    carry_count: int
    carry_total: int
    settled_count_cumulative: int
    defaulted_count_cumulative: int

    # Ledger deltas (this tick vs previous tick, by reason)
    ledger_delta: Dict[str, int]  # {'ai_regen': +1200, 'cap_clamp': -300, ...}

    # Decision counts (cumulative across this tick's refresh)
    decisions: Dict[str, int]  # {'forced_leave': 2, 'take_stake': 1, ...}

    # Audit
    audit_drift: Optional[int]  # only on audit ticks
```

### Output format

CSV one row per tick, columns matching `TickMetrics` (with `ledger_delta` and `decisions` flattened to one column per reason / decision type). Easy to load into pandas / sqlite / spreadsheets.

JSON sidecar with config + final summary stats (mean wealth movement, time-to-first-carry, distribution shape changes over time, etc.).

### CLI entry point

```bash
python -m scripts.run_economy_sim \
    --sandbox-id 771fb2e6-0d35-4aa5-94af-9acd97c671bc \
    --ticks 1000 \
    --metrics-every 1 \
    --audit-every 50 \
    --rng-seed 42 \
    --out sim-output/baseline.csv
```

Default sandbox-id flag could resolve to "freshest available" — but
for baseline runs we want a specific sandbox (probably a fresh one
seeded for the test).

### Fresh-sandbox setup helper

A companion script seeds a sandbox with N personalities at their
default `starting_bankroll`s and writes the lobby skeleton (5
stake-tier tables). Run once before the sim to get a known-state
starting point.

```bash
python -m scripts.seed_sim_sandbox --name "sim-baseline-v1" \
    --personalities 30 --tables-per-stake 1
```

Returns the sandbox_id to feed into `run_economy_sim`.

## Sandbox-aware observability

The backend audit (`compute_audit`) already accepts a `sandbox_id`
parameter (added in v103). The route and frontend don't surface it.
Both are small fixes.

### Backend route change

`GET /api/admin/chip-ledger/audit` currently calls `compute_audit`
without `sandbox_id`, returning the cross-sandbox total. Update to:

```python
@chip_ledger_bp.route('/api/admin/chip-ledger/audit')
@_admin_required
def chip_ledger_audit():
    sandbox_id = request.args.get('sandbox_id')  # None → cross-sandbox
    data = compute_audit(
        ...,
        sandbox_id=sandbox_id,
    )
    return jsonify(data)
```

Optional query param. Same shape, just scoped. Also add a sibling
route to list sandboxes for the UI dropdown:

```python
@chip_ledger_bp.route('/api/admin/sandboxes')
@_admin_required
def list_sandboxes():
    sandboxes = sandbox_repo.list_all()
    return jsonify({'sandboxes': sandboxes})
```

### Frontend updates to `ChipLedgerPanel`

Add a sandbox-select dropdown at the top of the panel:
- Default option: "All sandboxes (admin view)"
- Each sandbox listed with its display name + last-activity timestamp
- Selection re-fetches `/api/admin/chip-ledger/audit?sandbox_id=...`
- The displayed totals + entry list update per sandbox

The recent-entries view (`/api/admin/chip-ledger/recent`) also
needs the sandbox filter — same shape, same query param.

### Sim-integration affordance

Once both pieces ship, the admin UI gains an extra capability: open
a sim sandbox, watch its audit numbers refresh as the sim runs (or
after the sim finishes for batch analysis). A "Last sim run"
indicator showing tick count + finish time would be nice but not
strictly required.

## What we'd learn from a baseline run

Running the sim against a fresh-seeded sandbox at current code
(post-Phase-4, pre-vice-spending, pre-Phase-4.5) should reveal:

**Likely observations:**

1. **Wealth concentration grows monotonically.** Without vice spending or cap_clamp, the top decile accumulates. Gini coefficient should drift upward over time.

2. **The "stuck at zero" pattern propagates.** Once an AI hits 0 chips, do they ever recover? Phase 4's take_stake mechanic should bring them back at some rate; the sim measures whether it actually does.

3. **Carry creation rate is low.** Current stakes settle cleanly because most sessions are short and profitable on average. We expect to finally see some carries form under sustained simulation.

4. **table_rake is significant or marginal.** 1,883 events / $35k in the live sandbox suggests it's firing a lot but at small amounts. Sim with constant rate would tell us the steady-state drain.

5. **Audit drift stays at zero.** Or doesn't. Either way, important to know.

**Possible surprises** (the actual value of the sim):

- Wealth distribution stabilizes at some unexpected equilibrium (rake balances regen at a specific tier)
- A specific personality keeps winning / losing systematically (gameplay imbalance)
- take_stake fires more or less often than expected
- AI bankrolls converge near `starting_bankroll` (the comfort point already works)
- AI bankrolls diverge wildly even at steady state

The point of the sim is that we don't know which of these is real
until we measure it.

## Implementation commits

Five commits, ordered for incremental shippability:

**Commit 1: Backend audit route accepts sandbox_id**
- `GET /api/admin/chip-ledger/audit?sandbox_id=...` — optional query param
- Sibling `GET /api/admin/sandboxes` lists available sandboxes
- The audit function already supports this; just wire through the route
- Tests: route with no sandbox_id returns cross-sandbox; with sandbox_id returns scoped data; unknown sandbox_id returns empty audit cleanly
- Smallest commit; ships first because it's the precondition for everything else

**Commit 2: Frontend sandbox dropdown in ChipLedgerPanel**
- Add sandbox-select dropdown
- Re-fetch audit + recent-entries on sandbox change
- Default to "All sandboxes" preserves current behavior
- Loading + error states; sensible fallback when no sandboxes exist
- TypeScript types extended for sandbox metadata

**Commit 3: Sim harness — `cash_mode/sim_runner.py`**
- `SimConfig` + `TickMetrics` + `SimResult` dataclasses
- `run_sim(config) -> SimResult` main loop
- `capture_tick_metrics(...)` reads bankroll/stake/ledger state into a TickMetrics
- Includes the `disable_llm` flag and how it short-circuits
- Tests: dummy 10-tick run against a known sandbox; metrics shape correct; deterministic with seed

**Commit 4: CLI scripts**
- `scripts/seed_sim_sandbox.py` — seed a fresh sandbox for the sim
- `scripts/run_economy_sim.py` — CLI wrapper around `run_sim`, writes CSV + JSON summary
- Documentation in CLAUDE.md or a `docs/guides/` how-to
- Tests: end-to-end CLI invocation produces expected output files

**Commit 5: Analysis notebook / quickstart (optional)**
- Jupyter notebook in `notebooks/` loading the sim CSV
- Standard plots: wealth distribution over time, carry accumulation, gini coefficient, top-personality trajectories
- A README explaining how to run a baseline + interpret results
- Could ship later as needs emerge

A reasonable stopping point is after Commit 4 — we have the sim,
we can run it from CLI, we get CSV out, we can analyze in any tool.
Commit 5 is convenience.

## Locked decisions

1. **In-process, not HTTP.** Importing the lobby module directly is simpler, faster, deterministic. The sim is a development tool, not a product feature.

2. **Deterministic via RNG seed.** Same seed → same trajectory. Critical for comparing different code revisions ("did adding vice spending change the wealth distribution?").

3. **Disable LLM by default.** Vice narration and any future LLM-driven AI calls are mocked out at sim time. The sim measures mechanics; flavor doesn't affect economy.

4. **Sandbox filter on the audit is optional, not mandatory.** Default (no param) preserves cross-sandbox admin view. Adding scoping doesn't break the current admin workflow.

5. **Sim doesn't ship with default behavior changes.** It runs current code as-is. Tuning new code requires re-running the sim — that's the workflow.

## Open questions

1. **Should the sim drive `play_one_hand` (full sim) or skip to movement-only?** Full sim is realistic but adds I/O cost per tick (hand engine + maybe LLM if controllers go that path). Movement-only is faster but doesn't exercise the actual chip flow from gameplay. Probably full sim with `hand_sim_prob = 1.0` for the baseline (we want real chip churn), but a "movement-only" mode could be useful for quick iteration on lobby-level constants.

2. **Should metrics include per-personality trajectories?** Capturing N timeseries per personality (one bankroll trace each) is more data but enables "Zeus drift" visualizations. CSV gets wide. JSON-lines might be a better format. Decide based on intended analysis.

3. **Should we capture LLM call counts even when disabled?** If vice spending uses LLMs in production, the sim should report "vice fires that WOULD HAVE called the LLM" so cost projections are accurate. Mock LLM returns templated text but the counter increments.

4. **Does the sim itself need observability?** A progress bar, tick rate, ETA. For 1000 ticks at ~100ms each, the sim runs ~100 seconds — tolerable without a progress display, but a real-time print every 100 ticks would be nice.

5. **Pre-sim cleanup task (Task 26).** Should the sim refuse to run when too many personalities have NULL bankroll_knobs config? Or just run and let the analysis flag it. Probably the latter — the sim shouldn't gate on cleanup state; that's the operator's call.

## Files this plan touches

| File | Change | Commit |
|---|---|---|
| `flask_app/routes/chip_ledger_routes.py` | Accept `sandbox_id` query param; new `/api/admin/sandboxes` route | 1 |
| `react/.../admin/ChipLedgerPanel.tsx` | Sandbox dropdown + re-fetch logic | 2 |
| `react/.../admin/api.ts` (or equivalent) | Audit/recent fetchers accept sandbox_id | 2 |
| `cash_mode/sim_runner.py` (new) | SimConfig, TickMetrics, SimResult, run_sim, capture_tick_metrics | 3 |
| `scripts/seed_sim_sandbox.py` (new) | Sandbox seeder helper | 4 |
| `scripts/run_economy_sim.py` (new) | CLI wrapper | 4 |
| `tests/test_sim_runner.py` (new) | Unit tests for sim harness | 3 |
| `notebooks/economy_sim_baseline.ipynb` (new, optional) | Analysis notebook | 5 |

No schema migrations. No new database tables. The sim writes to
existing tables (ai_bankroll_state, stakes, chip_ledger_entries)
through existing repo APIs. CSV output is a file artifact.

## Spec status

Parked alongside the AI vice spending and staker incentives docs as
a precondition for tuning either. Suggested ship order:

1. **Phase 5 finish** (closes player loop) — separable from sim work
2. **Backfill bankroll_knobs config** (Task 26) — cleanup before sim baseline
3. **This plan: Commits 1-4** — observability + sim harness
4. **Baseline sim run** — capture pre-vice-spending economy state as the comparison
5. **Vice spending Commits 1+2** — informed by baseline
6. **Re-run sim** — validate vice bounds Zeus drift
7. **Staker incentives + Phase 4.5** — sim becomes the tuning tool for each

The sim is most valuable as an iteration tool: ship a candidate
mechanic, run the sim, compare to baseline, adjust constants,
re-run. Without it we're tuning blind.

## Why this matters

The cash mode economy has now grown to the point where small
mechanic changes have hard-to-predict aggregate effects. Adding
vice spending without measurement could swing the steady-state
wealth distribution dramatically in either direction. Adding
staker incentives could create unexpected matchmaking patterns
that lock the cast into stable pairs.

The current sandbox is one data point — interesting but
insufficient. A driven sim gives us **comparable data points**
across code versions, configuration changes, and constant tunings.
That's the difference between "we shipped vice spending and hope
it works" and "we shipped vice spending and the gini coefficient
trends matched our prediction within 5%."

This is the kind of tool that, once built, makes every subsequent
economy design decision faster and more confident. It's also the
kind of tool that's easy to defer "until we need it" — and then
discover six months later we wish we'd built it before all the
constants were already set.

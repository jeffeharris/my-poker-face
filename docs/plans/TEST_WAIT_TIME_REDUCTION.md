---
purpose: Plan to reduce developer wait time from tests while preserving confidence
type: plan
created: 2026-05-14
last_updated: 2026-05-22
---

# Test Wait Time Reduction

## Goal

Reduce the amount of time developers spend waiting on tests by separating fast
correctness checks from slower integration and poker-quality validation runs.

The main principle: local development should run the smallest useful test set.
Full-suite and simulation validation should still exist, but they should not be
part of every edit/test loop.

## Current Problem

The project has several different kinds of verification mixed together:

- small pure-function unit tests,
- controller and strategy integration tests,
- Flask/API tests,
- TypeScript checks,
- replay/tournament/simulation validation,
- broad full-suite coverage runs.

These are useful, but they have very different feedback-loop costs. When they
are treated as one undifferentiated suite, developers wait too long for changes
that only affect one small subsystem.

## Test Tiers

Adopt explicit tiers and use them consistently in local development, PR
handoff, and CI.

| Tier | Purpose | Target Runtime | When to Run |
|---|---|---:|---|
| Tier 0 | Import, compile, smoke checks | `<10s` | Constantly while editing |
| Tier 1 | Focused unit tests for touched module | `<30s` | Every implementation loop |
| Tier 2 | Relevant subsystem tests | `1-3 min` | Before handoff / before PR |
| Tier 3 | Full Python quick suite + TypeScript | `5-10 min` | Before PR / CI |
| Tier 4 | Full suite with coverage | CI budget | CI / pre-merge |
| Tier 5 | Poker simulation and benchmark validation | manual/scheduled | Strategy-quality validation |

## Developer Workflow

Use this default workflow:

1. During implementation, run Tier 0 or Tier 1 only.
2. Before handoff, run the relevant Tier 2 subsystem suite.
3. Before PR, run quick Python checks plus TypeScript checks.
4. Before merge, rely on CI for full coverage.
5. Run simulation validation only when changing strategy behavior.

For TieredBot work:

| Change Area | Local Test Target |
|---|---|
| Hand classification | hand-classification and postflop-classifier tests |
| Math floor / defense floor | math-floor, bluff-catch, value-override tests |
| Exploitation offsets | exploitation and playstyle-rule tests |
| Controller pipeline | TieredBot controller and intervention-trace tests |
| Bot quality | simulation scripts, not normal pytest loop |

## Proposed Commands

Add focused Make targets so developers do not need to remember long commands.

```makefile
test-unit:
	python3 -m pytest -q tests/test_strategy tests/test_preflop_classification.py

test-strategy-fast:
	python3 -m pytest -q \
		tests/test_strategy/test_math_floor.py \
		tests/test_strategy/test_value_override.py \
		tests/test_strategy/test_bluff_catch_gate.py \
		tests/test_strategy/test_playstyle_rule_families.py \
		tests/test_strategy/test_postflop_classifier.py

test-tiered:
	python3 -m pytest -q tests/test_strategy/test_tiered_bot_controller.py tests/test_strategy/test_tiered_bot_exploitation.py

test-routes-fast:
	python3 -m pytest -q tests/test_game_route_auth.py tests/test_experiment_routes.py tests/test_admin_experiment_route_auth.py

test-last:
	python3 -m pytest -q --lf

test-fail-first:
	python3 -m pytest -q --ff

validate-bots:
	python3 experiments/phase_8_diagnostics.py
```

These targets should be refined after measuring actual runtime.

## Pytest Markers

Make slow tests opt-in for the local quick loop.

Recommended markers:

```python
@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.simulation
@pytest.mark.llm
@pytest.mark.flask
```

Policy:

- `slow`: anything that materially increases local wait time.
- `integration`: crosses module boundaries or requires app/database setup.
- `simulation`: runs hands, tournaments, replays, or benchmark loops.
- `llm`: touches live or mocked LLM-heavy paths.
- `flask`: route, auth, Socket.IO, or app wiring tests.

Update the quick runner to exclude expensive tests:

```bash
pytest -m "not slow and not integration and not simulation and not llm"
```

## Parallel Execution

Use `pytest-xdist` for test groups that are safe to parallelize:

```bash
python3 -m pytest -q -n auto
```

Before enabling it globally:

- identify tests that mutate shared files or databases,
- isolate temp directories,
- make mutable fixtures function-scoped,
- make immutable expensive fixtures session-scoped.

## Fixture Caching

Audit repeated setup costs:

- strategy table loading,
- HU table loading,
- config parsing,
- state machine construction,
- app/database setup.

Use session-scoped fixtures for immutable data:

```python
@pytest.fixture(scope="session")
def strategy_table():
    return load_strategy_table()
```

Do not share mutable game state across tests. Share loaded tables/configs, then
construct fresh state machines per test.

## Simulation Validation Policy

Poker simulations are quality validation, not normal unit tests.

Keep these as explicit commands:

```bash
python3 experiments/phase_8_diagnostics.py
python3 experiments/simulate_bb100.py
python3 experiments/analyze_intervention_traces.py
```

Use them when changing:

- strategy tables,
- exploitation offsets,
- hand-strength classification,
- value override,
- bluff-catch behavior,
- math floor,
- opponent modeling.

Do not require full simulation validation for unrelated Flask, repository,
frontend, or documentation changes.

## CI Structure

Recommended CI jobs:

1. `python-fast`
   - quick unit tests,
   - no slow/integration/simulation/llm markers.

2. `python-integration`
   - Flask/routes/repositories/state-machine integration.

3. `typescript`
   - TypeScript type checks and frontend unit tests.

4. `python-full`
   - full suite with coverage.

5. `bot-validation`
   - scheduled or manually triggered simulation validation.

This gives fast PR feedback without losing the deeper checks.

## Measurement Plan

Before and after changes, record:

- full suite runtime,
- `--quick` runtime,
- strategy-fast runtime,
- slowest 20 tests,
- fixture setup time hotspots,
- CI job wall time.

Useful commands:

```bash
python3 -m pytest --durations=20
python3 -m pytest -q --lf
python3 -m pytest -q --ff
```

Track runtime improvements in this doc or a short follow-up report.

## Definition of Done

- Developers have documented fast targets for common work areas.
- Slow/simulation tests are marked and excluded from the default quick loop.
- Strategy changes can be iterated with focused tests under a short feedback
  budget.
- Simulation validation remains available but is opt-in/manual/scheduled.
- CI provides fast initial signal and deeper confidence in separate jobs.


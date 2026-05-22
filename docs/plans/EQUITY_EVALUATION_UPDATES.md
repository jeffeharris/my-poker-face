---
purpose: Plan to make equity calculation tie-correct, range-aware, eval7-grounded, and replayable for independent decision evaluation
type: design
created: 2026-05-15
last_updated: 2026-05-15
---

# Equity Evaluation Updates

## TL;DR

The equity system is useful but not yet a reliable independent measuring
stick. The highest-ROI work is not more sophisticated ranges first. It is:

- Fix tie handling in the duplicated decision-analyzer equity code.
- Use range equity for EV scoring when range data exists.
- Move all live equity math through one shared eval7-backed service.
- Add an offline evaluator that re-scores saved decisions with versioned
  assumptions.
- Preserve random-hand equity, static-range equity, observed-range equity, and
  evaluator metadata side by side so experiments can be compared apples-to-
  apples.

The goal is to separate **bot decision-making** from **decision judging**. A
strategy change should be evaluated against a stable scorer, and a scorer
change should be backtestable against the same frozen decision corpus.

## Context

There are currently two related but different equity paths:

- `poker/equity_calculator.py` calculates all-player equity using eval7 and
  correctly splits ties in the Monte Carlo path.
- `poker/decision_analyzer.py` has duplicated hero-only Monte Carlo methods:
  `calculate_equity_vs_random()` and `calculate_equity_vs_ranges()`.

The duplicated analyzer methods are the ones that drive decision quality and
EV scoring. They currently have two important limitations:

1. Ties count as full hero wins because the code only checks whether an
   opponent has a strictly higher score.
2. `equity_vs_ranges` is calculated and stored, but `ev_call` and
   `determine_optimal_action()` mostly use `analysis.equity`, which is random-
   opponent equity.

The range model itself is directionally good for gameplay and coaching:

- position buckets exist;
- observed VPIP/PFR can be used after a minimum hand sample;
- preflop action narrows ranges for open-raise, call, 3-bet, and 4-bet;
- card removal is applied when sampling combos;
- opponent ranges are captured in `opponent_ranges_json`.

But it is not independent enough to serve as the only benchmark. It uses
project-specific static buckets and homegrown postflop connection weighting.
That is acceptable for live strategy hints, but offline evaluation should make
the assumptions explicit, versioned, and comparable against eval7/PokerStove-
style range calculations.

## External Standard

Use eval7 as the neutral hand evaluator and range parser wherever possible.

eval7 supports:

- `eval7.Card`
- `eval7.evaluate()`
- `eval7.HandRange`
- exact and Monte Carlo hand-vs-range helpers in the Python/Cython package

The industry-standard shape for equity tools is:

- exact enumeration where cheap;
- Monte Carlo where exact range-vs-range is too expensive;
- PokerStove-style range notation;
- combo removal;
- tie splitting;
- reproducible iteration counts or seeds;
- visible assumptions about opponent ranges.

This plan does not require building a solver. It requires making our evaluator
deterministic, auditable, and hard to accidentally couple to live bot behavior.

## Goals

A completed first version should:

- Produce tie-correct equity for random and range-based calculations.
- Use a single shared equity service for live analyzer and offline evaluator
  code paths.
- Prefer `equity_vs_ranges` for EV and quality scoring when range data exists,
  while retaining random equity as a baseline field.
- Add eval7 `HandRange` support as the canonical interchange format for
  evaluator ranges.
- Add an offline evaluator that can re-score historical captured decisions
  without replaying full games.
- Version evaluator assumptions so old decisions can be re-scored by new
  evaluator versions.
- Make experiment reports distinguish "bot got better" from "the evaluator
  changed."

## Non-Goals

- Do not implement a full GTO solver.
- Do not tune every range bucket before fixing correctness.
- Do not remove the current range heuristics; preserve them as one evaluator
  mode.
- Do not make live bot behavior depend on the offline evaluator.
- Do not require external services for evaluation.

## Proposed Architecture

### 1. Shared equity service

Introduce a small service module, for example:

```text
poker/equity_service.py
```

Responsibilities:

- parse and normalize cards;
- calculate hero equity vs random hands;
- calculate hero equity vs range strings;
- calculate all-player equity for known hands;
- split ties correctly;
- return sample metadata;
- expose deterministic seeding for offline runs.

Proposed result shape:

```python
@dataclass(frozen=True)
class HeroEquityResult:
    equity: float
    wins: float
    losses: float
    ties: float
    tie_probability: float
    sample_count: int
    valid_sample_count: int
    skipped_sample_count: int = 0
    mode: str = "monte_carlo"
    seed: int | None = None
```

Tie handling should give split-pot credit:

```python
if hero_score == best_score:
    equity_credit = 1.0 / len(winners)
```

The existing `EquityCalculator` can either delegate to this service or become
the service. The key requirement is that `DecisionAnalyzer` stops carrying its
own duplicate equity simulation logic.

### 2. Range representation

Keep the existing internal set-of-canonical-hands functions, but add conversion
to eval7/PokerStove-style strings:

```python
def canonical_set_to_range_string(hands: set[str]) -> str:
    return ", ".join(sorted(hands))
```

Initial version can be simple. Later versions can compress:

```text
AA, KK, QQ, JJ, TT -> TT+
AKs, AQs, AJs -> AJs+
```

Store both forms where useful:

- `opponent_ranges_json`: current list/set representation for debugging
- `opponent_range_strings_json`: eval7-compatible strings used by evaluator

### 3. Evaluator modes

Support multiple scoring modes over the same decision:

| Mode | Purpose |
|---|---|
| `random_v1` | Baseline random-opponent equity. Useful sanity check. |
| `static_position_v1` | Position-only ranges. Independent of observed stats. |
| `action_aware_v1` | Position + preflop action narrowing. |
| `observed_stats_v1` | Current VPIP/PFR/aggression-based model. |
| `eval7_range_v1` | eval7 `HandRange`-backed hand-vs-range calculation. |

The live analyzer should not need every mode. The offline evaluator should.

### 4. Offline evaluator

Add an offline module, for example:

```text
experiments/offline_decision_evaluator.py
```

or, if it becomes core infrastructure:

```text
poker/evaluation/offline_decision_evaluator.py
```

Input:

- captured decision rows;
- hero cards;
- board;
- pot and call cost;
- legal action/menu data if available;
- opponent positions;
- opponent action context;
- observed opponent stats snapshot if available;
- action taken.

Output:

```text
capture_id
evaluator_version
equity_random
equity_static_position
equity_action_aware
equity_observed_stats
equity_used_for_ev
required_equity
ev_call
recommended_action
action_taken
decision_grade
reason_codes
range_inputs_json
sample_count
seed
created_at
```

This can initially write JSON/CSV under `experiments/results/`. If it becomes
useful in the admin UI, add a repository/table later.

## Sequencing

### Phase 1: Correctness and consolidation

Priority: highest.

Deliverables:

- Fix tie splitting in `DecisionAnalyzer.calculate_equity_vs_random()`.
- Fix tie splitting in `DecisionAnalyzer.calculate_equity_vs_ranges()`.
- Track valid iterations separately from requested iterations in range-based
  simulation.
- Fix `EquityCalculator._calculate_exact_equity().tie_probability` so a tied
  completed board reports `1.0`, not `1 / winner_count`.
- Add regression tests for:
  - chopped board;
  - known tie against random hand;
  - multiway tie;
  - range simulation with skipped invalid samples.

Acceptance criteria:

- A board-only straight/flush/full-house chop does not produce 100% hero
  equity unless hero is the sole winner.
- Random and range equity paths use the same tie policy.
- Existing pressure/equity tracker behavior remains stable except for corrected
  tie metadata.

### Phase 2: Range equity drives EV when available

Priority: high.

Deliverables:

- Add `equity_used_for_ev` to `DecisionAnalysis`.
- Set `equity_used_for_ev = equity_vs_ranges` when range data exists and the
  calculation succeeds.
- Fall back to `equity` when range data is missing or invalid.
- Preserve both equity values for reporting.
- Update EV calculation and `determine_optimal_action()` to use
  `equity_used_for_ev`.
- Add tests showing the same decision can grade differently when range equity
  and random equity diverge.

Acceptance criteria:

- Decision quality can be explained by the exact equity source used.
- Analytics can still compare random equity vs range equity.
- No caller loses the old `equity` field.

### Phase 3: eval7 range support

Priority: high.

Deliverables:

- Add conversion from internal canonical range sets to eval7-compatible range
  strings.
- Add a range equity path that accepts explicit range strings.
- Use `eval7.HandRange` for parsing and combo generation where possible.
- Keep custom multiway simulation if eval7's helper coverage is heads-up only
  or inconvenient for current needs.
- Add benchmark tests for stable hand-vs-range spots:
  - `AA` vs random;
  - `AKs` vs top 10%;
  - medium pair vs two overcards;
  - dominated ace vs strong ace range;
  - flop draw vs made-hand range.

Acceptance criteria:

- Evaluator can print the exact range string used for each opponent.
- A fixed seed produces reproducible Monte Carlo outputs.
- Exact or high-sample eval7 results can be used as a local benchmark.

### Phase 4: Offline evaluator v1

Priority: high for experiment quality.

Deliverables:

- Add a CLI/script that loads captured decisions and writes evaluator output.
- Support at least `random_v1`, `static_position_v1`, and `observed_stats_v1`.
- Version every evaluator run.
- Store `reason_codes`, not just final grades.
- Add a small frozen fixture corpus in tests.

Example command:

```bash
python3 -m experiments.offline_decision_evaluator \
  --limit 500 \
  --evaluator-version equity_eval_v1 \
  --out experiments/results/equity_eval_v1.json
```

Acceptance criteria:

- The same captured decision corpus can be re-scored without replaying games.
- Two bot versions can be compared under the same evaluator version.
- Two evaluator versions can be compared over the same bot decisions.

### Phase 5: Calibration and reporting

Priority: medium.

Deliverables:

- Add reports that summarize:
  - high-equity checks;
  - folds with equity above required equity;
  - calls below required equity;
  - random-vs-range equity deltas;
  - decision quality by phase and position;
  - decision quality by opponent archetype.
- Add confidence bands or sample metadata for Monte Carlo outputs.
- Compare static-position, action-aware, and observed-stat range models over
  the same corpus.
- Identify spots where range model assumptions dominate the decision grade.

Acceptance criteria:

- Experiment reports can say whether an improvement is visible under multiple
  evaluator modes.
- Large evaluator disagreements are inspectable by capture ID.

## ROI

### Highest ROI

- Tie correction. Small patch, large correctness impact.
- `equity_used_for_ev`. Makes range work affect actual grading.
- Offline evaluator. Converts experiments from "live score changed" to
  repeatable backtests.

### Medium ROI

- eval7 range strings. Improves transparency and makes external comparison
  easier.
- exact enumeration for cheap streets/spots. Reduces Monte Carlo noise.
- sample metadata and seeds. Makes score drift diagnosable.

### Lower ROI for v1

- sophisticated Bayesian postflop range updates;
- per-combo mixed frequencies;
- solver-like bet/raise EV modeling;
- admin UI before the CLI proves useful.

## Risks

### Risk 1: Evaluator circularity

If the live bot uses the same evaluator assumptions to decide and to grade, it
can optimize to the scoring rule instead of playing better poker.

Mitigation:

- Keep offline evaluator separate from live strategy.
- Compare multiple evaluator modes.
- Preserve random equity as a baseline sanity check.

### Risk 2: Range confidence is overstated

Observed VPIP/PFR from short samples can make range equity look more precise
than it is.

Mitigation:

- Store `hands_observed`.
- Keep minimum sample gates.
- Report range model source and confidence.
- Compare static-position mode against observed-stats mode.

### Risk 3: Monte Carlo noise hides regressions

Low iteration counts can change grades near thresholds.

Mitigation:

- Use fixed seeds for offline runs.
- Use higher iterations offline than live.
- Add confidence intervals or at least sample counts.
- Use exact enumeration where cheap.

### Risk 4: Multiway range exactness gets expensive

Full multiway range-vs-range exact enumeration can be expensive.

Mitigation:

- Use Monte Carlo for multiway v1.
- Prioritize tie-correctness, seeds, and large enough samples.
- Exact-enumerate heads-up and late-street spots first.

## Open Questions

- Should offline evaluator output live in SQLite immediately, or start as JSON
  under `experiments/results/`?
- Should `equity_vs_ranges` become the default EV source for all analyzer
  callers, or only when `PromptConfig.use_enhanced_ranges` is true?
- What minimum observed-hand sample should mark `observed_stats_v1` as trusted
  in reports: current 15 hands, or a higher offline threshold?
- Do we want one evaluator grade or multiple side-by-side grades in scorecards?

## Validation Plan

Run validation in three layers:

1. Unit tests for equity math:
   - ties;
   - card removal;
   - skipped samples;
   - known complete-board results.
2. Golden spot tests:
   - fixed hands and ranges;
   - deterministic seeds;
   - tolerances for Monte Carlo modes.
3. Replay evaluation:
   - select a frozen corpus of captured decisions;
   - score it with old analyzer logic and new evaluator logic;
   - inspect the largest grade deltas manually;
   - use the same corpus for future strategy changes.

## Success Metrics

- Zero known tie-overcount cases in tests.
- Every analyzed decision records which equity source drove EV.
- Offline evaluator can re-score at least 500 historical decisions from a
  single command.
- Experiment reports include random-vs-range deltas.
- Strategy PRs can cite frozen-corpus evaluator results without rerunning full
  games.


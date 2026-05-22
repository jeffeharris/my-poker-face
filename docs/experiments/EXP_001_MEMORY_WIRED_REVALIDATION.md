---
purpose: Re-run the earlier wealth-concentration experiments with `AIMemoryManager` wired into the sim, to determine whether our "maniacs always dominate" findings were genuine economic dynamics or artifacts of disarmed bots.
type: experiment
status: planned
hypothesis_summary: With opponent-modeling wired into the sim, defensive exploitation rules will fire and break the maniac-dominance pattern we observed in disarmed-bot runs.
created: 2026-05-22
last_updated: 2026-05-22
---

# Experiment 001 — Memory-Wired Re-Validation

> **Why this exists:** Earlier in our cash-mode investigation we
> concluded "maniac archetypes always dominate at the top tier
> regardless of starting position." That conclusion came from
> 10+ sim runs spanning 6 different setups (aspire-v2, qoh-at-50,
> wealthy-at-50, scrooge-at-1000, exp A/B/C, nerfed-maniac). On
> 2026-05-22 we discovered that `AIMemoryManager` /
> `OpponentModelManager` were never wired into `cash_mode/full_sim.py`.
> `analyze_interventions.py` showed `exploitation::hyper_aggressive`
> firing 0 times across 9,548 decisions. Every defensive rule was
> inert. Our "maniacs dominate" finding was running on bots that
> never adjusted to opponents — an artifact, not a finding.
>
> This experiment determines whether that artifact was the WHOLE
> story or PART of the story. If memory wiring breaks the
> concentration pattern, we have to discard most of our prior
> conclusions and start over. If it doesn't, the structural
> dynamics we identified are real and survive bot improvement.

## Hypothesis

**H1 (primary):** With memory wiring active, the maniac-dominance
pattern observed in the original `aspire-v2` 10k run will be
significantly weakened. Specifically:

- Final Gini drops by ≥ 0.05 absolute (from 0.75 toward ≤ 0.70)
- Top-1 share of positive gains drops by ≥ 15 percentage points
  (from 96.6% toward ≤ 80%)
- queen_of_hearts's final bankroll growth drops by ≥ 50% (from
  +1.39M toward ≤ +700k)

**H2 (secondary):** Defensive exploitation rules will fire at
meaningful frequency, NOT zero. Specifically:

- `exploitation::hyper_aggressive` fires ≥ 50 times per 1000 ticks
  per non-maniac AI on average
- `induce_override` fires ≥ 1 time over the full 10k run

**H3 (null-validating):** If memory wiring meaningfully changes
maniac play patterns, maniacs' winning streaks shorten. Specifically:

- The single largest individual gain (max_chips_final delta)
  drops by ≥ 30%

**Falsifier:** If H1, H2, and H3 are all met → memory wiring is the
mechanism, our prior findings need revision. If H2 holds but H1
and H3 do not → defensive rules fire but don't break the pattern
(structural concentration is real, just dampened by defense). If
H2 doesn't hold → memory wiring is plumbed but ineffective; need
to investigate why.

## What we're testing

Single variable change: `AIMemoryManager` wired into
`cash_mode/full_sim.py:_play_one_hand_inner`. Specifically:

- `play_one_hand` retrieves a session-scoped `AIMemoryManager`
  keyed by `sandbox_id` from a module-level cache
- Each AI controller gets `opponent_model_manager` + `memory_manager`
  attributes set before the hand runs
- `memory_manager.on_hand_start(...)` fires before `_run_hand`
- `memory_manager.on_action(...)` fires after every accepted action
  inside `_run_hand`

Everything else identical to aspire-v2's original setup.

## Setup

**Sandbox:** fresh, seeded via `scripts/seed_sim_sandbox.py` with
default knobs. Same baseline AI bankrolls, same lobby seed
distribution (no per-AI overrides).

**Sim config:**

```python
SimConfig(
    sandbox_id=<fresh>,
    num_ticks=10_000,
    hand_sim_prob=1.0,
    metrics_every=10,
    audit_every=500,
    rng_seed=42,           # SAME as aspire-v2
)
```

**Wiring status:** memory is on by default in `_build_controller`
after the POC commit. No flag needed to enable; presence of
`sandbox_id` argument triggers the wiring.

**Output destination:**
`/app/data/sim_exp001_memory_wired/run1.{csv,pids.jsonl,summary.json}`

## Measurements

**Primary metrics (used for H1):**

- `gini_final` — wealth-concentration measure at tick 9990
- `t1_pct` — top-1 winner's share of all positive gains
- `max_chips_final` — largest individual chip count
- queen_of_hearts net delta

**Bot-quality metrics (used for H2):**

- Total `exploitation::hyper_aggressive` fires across the run
- Per-archetype fire rate for that rule (rock/tag/lag/maniac)
- `induce_override` total fires
- Total `value_override` fires

**Diagnostic metrics (used for H3):**

- Per-archetype final P&L distribution
- Per-archetype VPIP / PFR / postflop fold rate (vs baseline)

**Captured via:** `scripts/sim_experiments/trace_sim_v2.py` running
in parallel with the production sim, OR a separate analytical pass
on the resulting CSV + JSONL. Trace is optional; the summary stats
are sufficient to validate H1.

## Comparison data

| Run | Source | Gini | Max | Top-1% | QoH delta |
|---|---|---|---|---|---|
| **aspire-v2 (baseline)** | `/app/data/sim_aspire_10k_v2/` | 0.754 | 1,520k | 96.6% | +1,389,526 |
| **exp001 (memory wired)** | TBD | TBD | TBD | TBD | TBD |

## Caveats / Known Confounders

1. **POC limitations.** Memory wiring is currently
   `on_hand_start` + `on_action` only — no `on_hand_complete`.
   Relationship/persistence updates that normally fire at hand
   end don't fire in this run. May affect some longer-arc dynamics
   (relationship-driven exploitation, narrative events).

2. **Cold-start period.** The first 50-200 hands of each session
   have insufficient opponent stats to trigger most exploitation
   rules. Our 10k tick run produces ~10-30k hands, so cold-start
   should fade within the first few hundred ticks. But if a
   maniac runs hot in the first 50 hands, they can accumulate
   chips before defense rules activate.

3. **`recent_aggressor_name` propagation.** Some exploitation
   rules read from `memory_manager.recent_aggressor_name`. Our
   POC sets this through `on_action`, which depends on the
   manager's internal state-machine working correctly across
   street transitions. Untested at scale in this code path.

4. **Single-seed run.** rng_seed=42 only. Variance across seeds
   could be enough to either show "the pattern broke" or "the
   pattern held" by chance. **If H1 results are within ±10pp of
   the falsifier thresholds, run with seeds [43, 44, 45] to
   confirm.**

5. **Same lobby seat distribution.** `ensure_lobby_seeded` uses
   an unseeded `random.Random()` for seat positions, so the actual
   AI roster at each table differs between runs even with the
   same sim rng_seed. We can't directly compare AI-by-AI
   trajectories — only aggregate metrics.

## Validation criteria

**Outcomes we'll act on:**

| Outcome | Decision |
|---|---|
| H1 + H2 + H3 all met | Discard prior wealth-tuning conclusions; the maniac-dominance finding was a bot-quality artifact. Re-baseline the economy work. |
| H2 met, H1 partial (Gini drop but smaller than predicted) | Memory wiring helps but doesn't eliminate concentration. The structural finding stands but is dampened. Continue economy work with memory-wired as the new baseline. |
| H2 met, H1 not met | Defensive rules fire but don't shift outcomes meaningfully. The dominance is structural, not bot-quality. Memory wiring is correct but insufficient. |
| H2 not met | Memory wiring is plumbed but ineffective — find out why before drawing any conclusions. Possible causes: `on_action` data shape mismatch, manager not retaining state across hands, etc. |

## Results

*To be filled after running.*

## Conclusion

*To be filled after analysis.*

## Decisions made / next steps

*To be filled after conclusion.*

---

## Format notes (for future experiments)

This doc structure is the template. Future experiments should:

1. **YAML frontmatter:** `type: experiment`, `status: planned|in-progress|complete`, plus a one-line `hypothesis_summary` so doc indexes are scannable.
2. **One numbered hypothesis per testable claim.** Use H1, H2, H3 etc. with quantitative thresholds, not vibes.
3. **Falsifier section.** What outcome would tell us the hypothesis was wrong? Important — keeps us honest.
4. **Caveats / Known Confounders.** List ahead of time what could make the result misleading. We didn't do this enough on the wealth-tuning work, which is why we kept treating each result as decisive when sample sizes were thin.
5. **Validation criteria table** mapping outcomes to actions. Decisions made up-front, not after seeing the data, are what keeps us honest about Goodhart drift.
6. **Results + Conclusion + Decisions sections** filled in AFTER. Don't compose them while running.
7. **File naming:** `EXP_NNN_<short_name>.md`, monotonically increasing. Lets future docs reference earlier experiments unambiguously.

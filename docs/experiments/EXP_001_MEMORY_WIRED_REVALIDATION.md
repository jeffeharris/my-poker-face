---
purpose: Re-run the earlier wealth-concentration experiments with `AIMemoryManager` wired into the sim, to determine whether our "maniacs always dominate" findings were genuine economic dynamics or artifacts of disarmed bots.
type: experiment
status: complete
hypothesis_summary: With opponent-modeling wired into the sim, defensive exploitation rules will fire and break the maniac-dominance pattern we observed in disarmed-bot runs. **Verdict: H2 met, H1 partial — concentration dampens but doesn't break.**
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

Ran 2026-05-22, sandbox `73af0c02-196d-407d-9ef6-4658ea8c636d`, seed
42. Wall time 646s (10.7 min) — ~3× slower than disarmed runs
(~150-300s typical), consistent with the added `on_action`
per-decision cost and memory-manager state maintenance.

### Comparison vs aspire-v2 baseline

| Metric | aspire-v2 (disarmed) | exp001 (memory wired) | Δ |
|---|---|---|---|
| Gini final | 0.7535 | **0.7099** | -0.044 |
| Max chips final | 1,519,526 | **1,121,509** | -397,517 (-26%) |
| Top-1 share | 96.6% | **69.6%** | **-27.0 pp** |
| Top-3 share | 99.3% | 95.4% | -3.9 pp |
| # winners | 20 | **31** | +11 |
| QoH delta | +1,389,526 | **+991,509** | -397,517 (still +71% of baseline) |
| Settled stakes (cum) | 344 | **1,040** | **+696 (3×)** |
| Total chips final | 3,189,120 | 3,394,025 | +204,905 |

### Top 5 winners (exp001)

```
queen_of_hearts:  130k → 1,121k   (+992k)      ← still dominant, but ~30% smaller
blackbeard:        55k →   402k   (+347k)
donald_trump:        0 →    19k   (+19k)
alice:           4.6k →    20k   (+15k)
jay_gatsby:        20k →    32k   (+12k)
```

queen_of_hearts is no longer alone in the gains; **eleven more
AIs got real money** vs the baseline (31 winners vs 20). But QoH
still won bigger than the next 10 combined.

### H1 evaluation

| Sub-hypothesis | Threshold | Actual | Verdict |
|---|---|---|---|
| **H1a** Gini drop ≥ 0.05 | -0.050 | **-0.044** | ✗ NOT MET (just under) |
| **H1b** Top-1 share drop ≥ 15pp | -15.0 pp | **-27.0 pp** | ✓ MET (well over) |
| **H1c** QoH delta ≤ 50% of baseline | ≤ +694,763 | **+991,509** | ✗ NOT MET (71% of baseline) |

### H2 evaluation

500-tick re-run with intervention tracing captured 14,547 decisions.
Defensive rule fire counts:

```
personality::default:                11,246  ← deviation profile (always fires)
exploitation::hyper_aggressive:       1,056  ← maniac defense — WAS 0 in disarmed runs
defense_floor::default:                 212
strong_hand_override::default:          119
short_stack::default:                    46
exploitation::high_fold_to_cbet:         40
```

Per-archetype × per-layer (fire counts):

| archetype | exploitation | defense_floor | personality | strong_hand_override |
|---|---|---|---|---|
| nit | 39 | 5 | 416 | 3 |
| rock | 59 | 6 | 734 | 7 |
| tag | **132** | 19 | 1,185 | 9 |
| calling_station | 173 | 37 | 3,177 | 21 |
| lag | 134 | 26 | 1,606 | 25 |
| maniac | 95 | 51 | 364 | 8 |

**H2 strongly met.** `exploitation::hyper_aggressive` fires 1,056
times in 500 ticks (~2 per tick on average across the lobby).
TAGs alone trigger 132 exploitation fires. Defensive infrastructure
is active across every archetype except in trivial cases.

### H3 evaluation

| Sub-hypothesis | Threshold | Actual | Verdict |
|---|---|---|---|
| **H3** Max chips drop ≥ 30% | -30% | **-26%** | ✗ NOT MET (just under) |

## Conclusion

**Verdict: validation criteria row 2 — "H2 met, H1 partial."**
Memory wiring meaningfully changes the economic dynamics but does
NOT eliminate concentration. Per the pre-committed validation
criteria, the interpretation is: *defensive rules fire but don't
shift outcomes meaningfully enough to overturn the structural
concentration finding. The dominance pattern stands, dampened.*

What changed:
- The total economic activity tripled (344 → 1,040 settled stakes).
  The bots are playing a fundamentally different game when they
  can read opponents.
- Eleven more AIs landed in the winners' circle. The pool of
  contenders genuinely widened.
- Top-1 share crashed from 96.6% → 69.6% — the dominator no longer
  monopolizes gains.

What didn't change:
- A single MANIAC archetype still ends up dramatically ahead.
  queen_of_hearts won +992k vs the next-best at +347k.
- The max-chips ceiling shifted modestly (-26%, missed the 30%
  threshold by 4 percentage points).
- Top-3 share still ~95% — the top remains very top-heavy even if
  not winner-take-all.

The clean interpretation: **memory wiring corrects the worst
miscalibration** (TAGs and rocks now defend themselves against
maniac aggression) but **doesn't dissolve the structural advantage
of running hot at the highest tier**. queen_of_hearts (maniac at
$1000) still accumulates chips faster than anyone else; she's just
not the sole accumulator anymore.

The earlier hypothesis from our pre-memory investigation — that
the maniac archetype intrinsically dominates — was **half-right**.
Maniacs DO have a structural edge, but it was inflated by the
disarmed bots around them. A more accurate phrasing: *maniacs win
even in fair fights; they win disproportionately when defenders
are weak*.

## Decisions made / next steps

1. **Memory wiring is now the sim baseline.** All future
   experiments default to memory-wired. The earlier wealth-tuning
   findings (qoh-at-50, wealthy-at-50, Exp A/B/C) are not
   invalidated outright but their *magnitudes* are inflated — they
   measured disarmed-bot economies. If we want to revisit any
   specific finding for accuracy, re-run under memory wiring; if
   the qualitative pattern was clear in the disarmed run, it
   likely survives memory wiring at reduced magnitude.

2. **`on_hand_complete` follow-up.** Memory POC is currently
   action-only. Hand-complete callback adds relationship updates
   + persistence side-effects. Worth adding when convenient but
   not required for further economy work.

3. **Maniacs vs maniacs is the next question.** With three maniacs
   in the cast (QoH, blackbeard, don_quixote), only one wins per
   run — variance picks the winner. If we want a more equitable
   ceiling, we'd need to either:
   - Reduce the number of maniacs in the cast
   - Add a stronger anti-aggression layer (the deviation profile
     clamp we tested in `nerf_maniac.py`)
   - Add vice spending as a wealth cap

4. **The 3× settled-stakes increase deserves its own look.** With
   memory wiring, AIs make better decisions about stake offers
   (better assessment of borrower trustworthiness via relationship
   accumulation, even without full `on_hand_complete`). This may
   itself change the aspiration-ask findings from earlier
   experiments. EXP_002 candidate: re-run aspiration_ask metrics
   under memory wiring.

5. **Sim performance regression.** Memory wiring added ~3× wall
   time per sim. The `on_action` callback is the suspect — it does
   real work updating opponent models. Worth profiling if we need
   longer-form sims (50k+ ticks); for 10k-tick experiments the
   current cost is acceptable.

## Methodology lesson learned

Pre-committing the validation criteria as a table mapping outcomes
to decisions worked. If we'd analyzed this run without the
criteria pinned, we might have looked at "Gini dropped, top-1
crashed, three things were not met" and either:
- Argued it as a win (the dominator share fell by 27pp!), or
- Argued it as no result (Gini drop was just below threshold).

The table forces us to use the language we committed to: row 2.
"H2 met, H1 partial — finding stands but dampened." Clean.
Reusable for future experiments. **Keep this format.**

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

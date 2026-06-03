---
purpose: Tune the four tournaments-as-a-draw DrawWeights so the drawn Main Event field is redistribution-aligned, and confirm every term actually moves the ranking
type: experiment
status: planned
hypothesis_summary: There exists a DrawWeights vector that pulls a field whose median bankroll is ≤ 80% of the eligible-pool median, with all four draw terms measurably firing
created: 2026-06-03
last_updated: 2026-06-03
---

# Experiment 007 — Tournament Draw Weights

> **Why this exists:** the whole tournaments-as-a-draw feature (phases A–D) is
> built but flag-gated off and has never run with `TOURNAMENT_DRAW_ENABLED` on.
> The draw scorer ships with placeholder weights (`prize=0.40, renown=0.25,
> field=0.15, cash_comfort=0.20`) that were never tuned against a real persona
> pool. Before flipping the flag we need to know: can these weights actually pull
> a *redistribution-aligned* field (poorer personas chasing the prize, the bank's
> chips flowing downward), and does each of the four terms even move the ranking
> on a real pool — or are some inert (e.g. renown when `RENOWN_V2_PERSIST_AI` is
> off)?

## Hypothesis

**H1 (primary):** There exists a `DrawWeights` vector in the swept grid for which
the drawn field's **median bankroll is ≤ 80% of the eligible-pool median
bankroll** on the reference pool — i.e. the draw skews the field meaningfully
toward the poorer half (redistribution-aligned), as the prize term intends.

- The winning vector beats the current default (`.40/.25/.15/.20`) on the
  redistribution metric, OR the default already clears the 80% bar.
- The skew is driven by `prize_appeal` (poorer personas score higher), not by an
  artifact of the pool — verified by the per-term contribution (H3).

**H2 (secondary, reported but NOT a hard gate — per the owner's call):** the
winning vector doesn't degenerate the field — successive seeds still field a
varied cast (variety), settled deep-stacked seated personas tend to resist
(comfort), and high-renown bigs still appear when renown data is present. These
are measured and weighed in the final choice, but a vector is not *rejected*
for missing them; redistribution (H1) + a firing mechanism (H3) are the gates.

**H3 (null-validating — HARD gate):** each of the four terms (`prize`, `renown`,
`field`, `cash_comfort`) measurably moves the ranking on the reference pool —
zeroing a term changes the drawn field. A term that contributes ~0 (e.g. renown
on a pool with no `renown_v2` data) is *untunable on that pool*, and tuning its
weight there is meaningless.

**Falsifier:**
- **H1 false** — no vector in the grid gets the drawn-field median to ≤ 80% of
  the pool median. Then weights alone can't redistribution-skew this pool; the
  formula (or the pool's bankroll spread) is the lever, not the weights.
- **H3 false for a term** — that term is inert on the pool. Do NOT conclude "this
  weight doesn't matter"; conclude "this pool can't tune it" and rerun on a pool
  that carries the relevant signal (e.g. renown needs `RENOWN_V2_PERSIST_AI` +
  history) before touching that weight.

## What we're testing

The four `DrawWeights` (`prize`, `renown`, `field`, `cash_comfort`) in
`flask_app/services/tournament_draw.py` — currently `prize=0.40, renown=0.25,
field=0.15, cash_comfort=0.20`. Single variable: the weight vector. Everything
else (the pure `score_draw`/`rank_field` math, the candidate pool snapshot) held
fixed. Baseline = the current defaults.

## Setup

**Sandbox:** <how it's seeded / which DB snapshot the candidate pool is drawn
from — real dev-DB persona bankroll/renown/cash-seat snapshot, or the synthetic
representative pool. Specify which.>

**Sim config / experiment parameters:**

```python
# Pure tuning loop — no full tournaments needed (the draw is pure):
#   pool -> build_draw_inputs(ctx) -> rank_field(weights, seeds) -> metrics.
# Sweep grid over the weight simplex; for each weight vector + each seed,
# rank the field and aggregate the metrics below.
# Harness: scripts/sim_tournament_draw_weights.py  (force-added past gitignore)
WEIGHT_GRID = ...   # e.g. prize/renown/field/cash_comfort over {0.1..0.6}
SEEDS = range(...)  # field-variety needs many seeds
FIELD_SIZE = 18     # DEFAULT_MAIN_EVENT
```

**Wiring status / preconditions:** the feature is flag-gated OFF
(`TOURNAMENT_DRAW_ENABLED`). The tuning loop calls the PURE scorer directly, so
no flag flip is needed. The renown terms degrade to 0 unless
`RENOWN_V2_PERSIST_AI` is on AND the pool snapshot carries renown-v2 peaks — so
the renown-weight axis is only meaningful on a renown-bearing pool (note which
runs have renown data).

**Output destination:** <file path for the sweep results, e.g.
`docs/experiments/data/EXP_007_*.json` / a results table>

## Measurements

**Primary metrics (used for H1):**

- **Redistribution alignment** — drawn-field median bankroll vs the eligible
  pool median (the draw should pull the POORER half: drawn-median < pool-median
  by some margin).
- **Comfort resistance** — fraction of deep-stacked, currently-seated personas
  that AVOID the draw (settled winners should resist).

**Secondary metrics (used for H2):**

- **Field variety** — distinct top-N fields across seeds (successive Main Events
  shouldn't field the identical cast; e.g. ≥ K distinct fields over S seeds).
- **Bigs pulled** — when renown data is present, are high-renown personas
  represented in the field (the marquee draw)?

**Diagnostic metrics (used for H3 / context):**

- Per-term contribution to the score (prize_appeal / renown_appeal /
  field_appeal / cash_comfort) across the pool — confirms each term actually
  moves the ranking (the mechanism fires).

**Captured via:** `scripts/sim_tournament_draw_weights.py` (to be built).

## Comparison data

| Run | Source | redistrib (drawn vs pool median) | comfort-resist % | field variety | bigs pulled |
|---|---|---|---|---|---|
| **baseline (current weights)** | `prize=.40 renown=.25 field=.15 comfort=.20` | TBD | TBD | TBD | TBD |
| **TOURNAMENT_DRAW_WEIGHTS** | TBD | TBD | TBD | TBD | TBD |

## Caveats / Known Confounders

<Fill in BEFORE running. Candidates:>

1. **Pool snapshot is a single point in time** — bankroll/renown distributions
   shift across a season; a weight vector tuned to one snapshot may not
   generalise. Consider ≥2 snapshots (early vs mid-season).
2. **Renown axis is inert without data** — if `RENOWN_V2_PERSIST_AI` is off or
   the snapshot has no v2 peaks, the renown + field terms contribute 0, so the
   renown weight can't be tuned on that pool. Don't read "renown weight doesn't
   matter" from a renown-less pool.
3. **Pure-scorer tuning ≠ live behaviour** — this loop tunes WHO gets drawn, not
   the downstream economic effect (the actual chip redistribution depends on
   tournament play + payout). A promising weight vector still needs a hands-on /
   ticker sim before flipping the flag.
4. **`cash_comfort` proxy is seat-stack depth only** (no true net-winnings) —
   "comfort resistance" measures resistance to a deep stack, not to a hot streak.
5. **Goodhart risk** — over-tuning redistribution could starve the field of
   bigs (no marquee), killing the field-appeal pull on fish. The variety + bigs
   metrics guard against this; weigh all four metrics together, not just
   redistribution.

## Validation criteria

**Outcomes we'll act on:**

| Outcome | Decision |
|---|---|
| H1 + H2 + H3 all met | <adopt the winning weight vector as the new `DEFAULT_WEIGHTS`; proceed to a live/ticker sim before flipping the flag> |
| H2 met, H1 partial | <intermediate response — e.g. adopt the best-on-redistribution vector, note the trade-off> |
| H2 met, H1 not met | <weaker but still informative — document that the current pool can't be redistribution-skewed by weights alone; revisit the formula> |
| H2 not met | <debug the mechanism (per-term contributions) before drawing any conclusions> |

## Results

**Pre-run / setup-validation (2026-06-03, `scripts/sim_tournament_draw_weights.py`):**
the harness runs against the real dev pool (sandbox `d9a1…`, 89 personas) and
immediately surfaced two things the formal run must account for:

- **3 of 4 terms are inert on the raw dev pool.** `prize_pool` resolves to **0**
  (the sandbox bank isn't flush → no overlay), and `own_renown` is **0** for all
  (no `renown_v2` data; `RENOWN_V2_PERSIST_AI` off). So `prize`, `renown`, and
  `field` all fail H3 (zero ranking change); only `cash_comfort` fires.
  Redistribution is pinned at **0.921** regardless of weights. **The falsifier
  fired on the setup run** — weights can't be tuned where the signals are dead.
- **With a realistic overlay (`--prize-pool 50000`), prize fires** and
  redistribution drops to **0.618** (already < the 0.80 H1 target) — *at the
  default weights*. But the sweep returns the SAME field for every weight vector,
  because a 50k prize saturates `prize_appeal → 1.0` for everyone below 50k
  (pool median bankroll is 6.5k), leaving only `cash_comfort` + a few whales to
  differentiate. So **overlay size relative to the bankroll distribution is a
  lever co-equal with the weights**, and `prize_appeal` saturation flattens the
  weight sensitivity.

**Implications for the formal run** (not yet done): tune against a pool where the
terms vary *smoothly* — (a) sweep overlay size alongside weights so `prize_appeal`
isn't saturated (an overlay near the pool median differentiates the low-mid
range), and (b) supply renown data (`RENOWN_V2_PERSIST_AI` on + history, or a
synthetic renown overlay) to make the `renown`/`field` axes tunable. On the raw
flag-off dev pool, the only honest conclusion is "this pool can't tune 3 of 4
weights" — exactly what H3's falsifier predicted.

*Formal sweep results to be filled after running on a signal-bearing pool.*

## Conclusion

*To be filled after analysis.*

## Decisions made / next steps

*To be filled after conclusion.*

---
purpose: Spec for the chart-graded preflop coaching system — grade a human's real play against the bot's solver charts, surface leaks, nudge live, drill, and measure
type: spec
created: 2026-05-31
last_updated: 2026-05-31
---

# Chart-graded preflop coaching

Grades a human's **real** preflop decisions against the **same solver charts the
TieredBot plays from** (`poker/strategy/data/preflop_*.json`), by **frequency
deviation over a sample**. Closes the coach loop end-to-end:

```
play → grade (chart) → surface (review panel + in-game nudge)
                              ↓
   measure follow-through ← capture ← drill the exact leak
```

Companion to `docs/plans/TRAINING_MODE.md`. Postflop stays on heuristic-mining;
this is preflop only (the one place we have solver data → an honest GTO baseline).

## The key insight (inverted leverage)

The bot already carries full `rfi` / `vs_open` / `vs_3bet` / `vs_4bet` charts at
multiple depths. The win was **not** authoring new charts — it was pointing the
*coach* at the charts the bot already uses. Diagnosis and practice now share one
standard: you're graded against what your opponents play.

## Grading model

For each decision: reconstruct `PreflopNode(scenario, position, opener, hand)`,
look up the chart → `{fold, call, raise}` (raise-size variants folded into one
`raise` bucket), and compare **your frequency over a sample** to the chart's.

A **leak** is a node-class where your frequency diverges grossly, with
recurrence (sample gate) and a confidence tier. Four plain-language kinds:

| kind | meaning |
|---|---|
| `limp` | open spot, you flat-call a *playable* hand the chart raises-or-folds |
| `too_loose` | you play a hand the chart folds |
| `over_fold` | you fold a hand the chart continues with |
| `too_passive` | you flat where the chart strongly raises (the faced-raise/3-bet signal) |

**Grouping (primary = position aggregate).** Exact `(scenario, position, hand)`
grouping is far too sparse at realistic volume (every hand is ~n=1). The headline
unit is the `(scenario, position)` aggregate with the chart expectation
**normalized to the hands actually held** — volume-efficient, gives signal from a
few dozen hands. Exact-hand is a finer tier surfaced only when a hand repeats.

**Confidence tiers.** `n < CONFIRM_MIN_SEEN (6)` → `watching` (could be variance);
`≥ 6` → `confirmed`. Never claims "clean" without an eligible-volume group.

## Depth-aware reference (`preflop_reference.py`)

Mirrors the bot's `_select_preflop_table`: 2-handed → HU chart; otherwise nearest
depth bucket (25 / 50 / 100bb) for the hands. Short-stack push/fold (≤15bb) is
HU-only and needs live all-in context, so ≤15bb **multiway** is out of scope for
grading (disclosed, not mis-graded against a deep chart).

## Components

| File | Role |
|---|---|
| `poker/strategy/preflop_reference.py` | depth-aware chart resolver, `bucket_actions` |
| `flask_app/services/coach_chart_leaks.py` | pure grading core (kinds, tiers, coverage) |
| `flask_app/services/coach_chart_data.py` | load + reconstruct node context; `get_owner_chart_leak_set` (live recall) |
| `flask_app/services/coach_drill.py` | sample spots + grade answers (the practice half) |
| `flask_app/services/coach_engine.py` | `_annotate_known_preflop_leak` (in-game recall) |
| `poker/repositories/coach_repository.py` | `record_tip` / `get_tip_effectiveness` |

## Provenance: backfill vs capture-forward

- **Capture-forward (exact):** schema **v123** adds `preflop_node_key` to
  `player_decision_analysis`, written at decision time via the bot's
  `build_preflop_node` (pre-action state). Exact opener + `vs_3bet`.
- **Backfill (approximate):** reconstruct from `player_position` + `cost_to_call`
  vs BB (`current_ante`) + `opponent_positions`. Clean for `rfi`/`vs_open`;
  `vs_3bet` skipped, opener averaged.

## Surfaces

- **Review** — `GET /api/coach/preflop-leaks` (VPIP bars for orientation +
  chart leaks); `POST /api/coach/preflop-leaks/feedback` (Assistant-tier coach).
- **In-game nudge** — `_annotate_known_preflop_leak` matches the live spot
  against `get_owner_chart_leak_set` (confirmed-only, throttled once/spot/session),
  surfaced via `PROACTIVE_TIP_PROMPT`.
- **Drill** — `GET /api/coach/drill` (top confirmed leak or explicit spot) +
  `POST /api/coach/drill/answer` (graded vs chart). UI: `PreflopDrill.tsx`.
- **Measurement** — schema **v124** `coach_tips` logs every served proactive tip
  + the leak nudge that fired; `get_tip_effectiveness` joins to
  `player_decision_analysis` (did the next decision follow the solver line?).
  Surfaced on the review panel (self) and the admin "Coach Metrics" tab (global).

## Honest scope / known limitations

- **GTO baseline, not exploit-aware.** Deliberate adjustments vs weak players read
  as deviations. Stated in the UI.
- **Short-stack multiway** (≤15bb, >2 players) → skipped (no clean reference).
- **Drill teaches the 100bb 6-max baseline** regardless of the player's stakes.
- **Effectiveness = compliance, not causal** — "did they follow after a nudge?",
  not yet nudged-vs-not.
- Backfill `vs_3bet` skipped / opener averaged (capture-forward fixes going forward).

## Next steps

- Nudged-vs-baseline causal cut (the data is now captured for it).
- Progress-over-time ("watch your leak shrink").
- Postflop leak-mining (separate; no solver charts there).
- Drill any spot (not just confirmed leaks) for new users.

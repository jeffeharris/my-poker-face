---
purpose: Design for a typed drill framework so the coach can serve several small drill types (chart, pot-odds, push/fold, outs, hand-rank) over one runner
type: design
created: 2026-06-01
last_updated: 2026-06-01
---

# Coach drill framework

Generalize the single preflop-chart drill into a **typed drill runner** so we can
add small drills cheaply, each as a generator + grader + a render branch — instead
of a new endpoint + component per drill. Design only; not built.

Builds on the existing drill: `flask_app/services/coach_drill.py`
(`sample_drill_spots` / `grade_drill_answer` / `pick_drill_leak`),
`GET|POST /api/coach/drill[/answer]`, and `PreflopDrill.tsx`.

## Principle: a drill = generator + grader + spot renderer

The shared *runner* never changes per drill: fetch a batch of spots → present one
at a time → submit an answer → show feedback → tally a score → complete. Each
**drill type** plugs in three things:

1. **build(params) → spots** — generate a batch of typed spots (+ optional title).
2. **grade(spot, answer) → verdict** — grade one answer server-side (never trust
   the client). The verdict carries a normalized `outcome` (`good` | `ok` | `miss`)
   so the runner tallies uniformly, plus type-specific detail for the feedback.
3. **SpotView (frontend)** — render the spot + the answer controls + the graded
   feedback for that type.

## Backend: a type registry

`coach_drill.py` becomes a registry keyed by `type`:

```
DRILLS = {
  'chart':    DrillType(build_chart_spots,   grade_chart),     # the current drill
  'potodds':  DrillType(build_potodds_spots,  grade_potodds),
  'pushfold': DrillType(build_pushfold_spots, grade_pushfold),
  'outs':     DrillType(build_outs_spots,     grade_outs),
  'handrank': DrillType(build_handrank_spots, grade_handrank),
}
```

The current `sample_drill_spots`/`grade_drill_answer` become `build_chart_spots`/
`grade_chart` (renamed, behavior unchanged).

## API (generalize the existing routes, back-compatible)

- `GET /api/coach/drill?type=<type>&<type params>` → `{type, title, spots: [spot]}`.
  Default `type='chart'`; the chart type keeps today's `?scenario=&position=` /
  auto-pick-top-leak behavior, so existing URLs and the leak-panel "Drill" buttons
  are unchanged.
- `POST /api/coach/drill/answer` body `{type, spot, answer}` → verdict.
  (Generalized from today's chart-specific `{scenario, position, hand, action}`.)

Grading recomputes from the spot server-side; the client's submitted spot is
validated/echoed, not trusted for the answer.

## Spot + answer + grading per type

| type | spot fields | answer | ground truth | verdict detail |
|---|---|---|---|---|
| **chart** | scenario, position, hand, depth_bb, num_players | fold/call/raise | solver charts (`reference_strategy`) | your_freq, chart_freq, primary (existing) |
| **potodds** | hand, board[], pot, bet | call/fold | eval7 equity vs pot-odds required equity | equity, required_equity, +EV? |
| **pushfold** | hand, position, eff_bb | jam/fold | Nash `push_fold` chart (`lookup_push_fold_action`, HU) | chart_action, eff_bb |
| **outs** | hand, board[] | number (multiple-choice) | eval7 outs/equity | outs, equity |
| **handrank** | handA, handB | A/B (stronger) | eval7 preflop all-in equity | equityA, equityB |

All grading is **math/solver-grounded** (eval7 + the solver/push-fold charts). No
postflop *solver* grading — postflop here is only the math drills (pot-odds, outs
on a board); "what's the GTO postflop action" stays out of scope (it's the
separate postflop project).

## Frontend: a typed runner

`PreflopDrill.tsx` is refactored into a generic `DrillRunner` owning the shared
loop + score + completion + the spot/type picker. Per-type rendering delegates to
small components registered in a frontend map:

```
const DRILL_VIEWS = {
  chart:    ChartSpot,     // shorthand + cards + Fold/Call/Raise (today's UI)
  potodds:  PotOddsSpot,   // board + pot/bet + Call/Fold
  pushfold: PushFoldSpot,  // hand + "Xbb" + Jam/Fold
  outs:     OutsSpot,      // board + multiple-choice
  handrank: HandRankSpot,  // two hands, pick stronger
}
```

A `SpotView` gets `{spot, onAnswer, grade}` and renders the spot, the answer
controls, and (once answered) the feedback. The runner stays drill-agnostic.

## Scoring

Each grader returns a normalized `outcome` ∈ {good, ok, miss} so the runner's
"X/Y solid" tally is uniform. Mapping is per type (chart already has good/thin/leak
→ good/ok/miss; pot-odds correct/incorrect → good/miss; etc.).

## Spot generation notes

- **potodds**: sample plausible pot/bet/board/hand near the decision boundary (mix
  of clear calls, clear folds, and close spots) so it's instructive, not trivial.
- **outs**: boards with a recognizable draw (flush/OESD/gutshot/overcards).
- **handrank**: pick two hands with a meaningful equity gap (avoid near-coinflips
  early; can tighten as a difficulty knob).
- Randomness is per-request (runtime, not a workflow script), seedable for tests.

## Leak-targeting (later)

`chart` already auto-picks the player's top leak. Other types could be
leak-seeded later (e.g. pot-odds drilled from the player's −EV-call decisions the
`decision_analyzer` already flags). v1: types are self-contained / spot-picked.

## Migration / back-compat

- `chart` IS the current drill (rename internals, keep behavior + URLs + tests).
- Add `type` to the endpoints, default `chart`.
- The drill page gains a drill-type selector alongside the existing spot picker.

## Phasing

- **P1** — framework + registry; refactor the current drill into the `chart`
  type; add **pot-odds** (the highest-value new skill, solid ground truth).
- **P2** — **push/fold** (reuses the Nash chart; HU).
- **P3** — **outs** + **hand-rank** (quiz-style, eval7).
- Postflop solver-graded drills: out of scope (separate project).

## Risks / open questions

- **Spot realism** (pot-odds especially): a bad generator makes trivial or
  unrealistic spots. Mitigate by sampling around the boundary + a few clear ones.
- **eval7 cost**: equity calcs are fast (used live already); grading server-side
  per answer is fine.
- **Difficulty**: do we want a difficulty knob (e.g., closer pot-odds, tighter
  hand-rank gaps)? Proposed: ship fixed v1, add difficulty later.
- **Answer-trust**: the client submits the spot it was given; we recompute the
  grade from it. Acceptable (it's practice, not ranked) — or re-issue spot ids
  server-side if we ever care.

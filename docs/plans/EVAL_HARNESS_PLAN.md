---
purpose: Prioritized plan to build a discriminating eval for the tiered bot, so chart/strategy changes can be judged "correct" vs merely "station-beating" before more charts are authored
type: design
created: 2026-05-25
last_updated: 2026-05-25
---

# Eval Harness Plan — make the yardstick trustworthy

> **Why now:** Per `CHART_COVERAGE_AND_GENERATION.md` ("Next concrete step")
> and the whole tiered-bot investigation, **the eval is the binding
> constraint — not the charts.** Every postflop win to date was measured vs
> *exploitable* opponents, so we cannot tell whether a change is *correct* or
> just *station-beating*. That ambiguity **gates whether finer charts (3BP,
> medium-SPR, LLM-refined grids) are worth authoring at all.** Build the eval
> first. Read with the `tieredbot-bb100-lookup-tables` memory note.

## The problem

All current opponents reward the *same* thing — value extraction from a
caller — and punish nothing:
- **`Jeff_clone`** (the "unlock") is a **calling station** (vpip .39 / **ftc
  .45** / WtSD .59). It *inflates* "stop under-betting value" fixes and
  **cannot punish over-folding, reward bluffing, or reward balance.**
- **`gto` / `mix` rule bots** are **always-calling** — the postflop fix scored
  `+200` vs them (conservation-verified: real chips, just exploiting
  always-callers).

So a bb/100 gain vs these can mean "the change is correct" **or** "the change
extracts more from a station." We can't distinguish them. This is the wall the
entire investigation kept hitting (multiway "passivity" was an artifact; the
push/fold table washed; the SPR coverage hole hid under a "−4.2, 100bb is fine"
baseline; H2 was +EV vs value-bots but −EV vs the bluffier read of Jeff). **The
recurring failure mode is an eval that can't see the thing we changed.**

## The plan (prioritized by cost / ROI)

The key distinction: **relative** evals ("is the new version better than the
old?") are cheap and gate authoring; **absolute** evals ("is the bot actually
good?") are the final word and cost more.

### P0 — Champion-vs-Challenger (relative, cheap, do FIRST)
Run the **current bot vs the changed bot head-to-head** at one table (some
seats champion = change OFF, some challenger = change ON). The better strategy
**wins chips off the worse one** → net bb/100 of challenger-vs-champion = the
improvement. **Discriminating by construction** (the opponent is a *coherent*
strategy, not a station) and **immune to station-inflation** — there's no
station, they're playing each other.

Two flavors:
- **Flag-gated changes (trivial):** the recent work is already flag-gated
  (`enable_multistreet_context`, `enable_value_bet_floor`). Just set the flag
  per-controller — champion seats off, challenger seats on. ~no new code.
- **Chart-file changes (small build):** load two strategy tables and assign
  **per-controller** (`make_controller` currently assigns one
  `strategy_table` to all). Add a per-seat table arg. Mirrors the per-controller
  toggle pattern used on the `push-fold-6max` branch.

Metric: challenger net bb/100 vs champion, **paired seeds**, ≥3 seeds; require
CI-clear positive (watch for per-seat sign-disagreement = noise, as in the
push/fold A/B). **This becomes the gate every chart/strategy change must pass.**

### P0.5 — A non-station clone (absolute, cheap)
Author a **punishing** clone profile to complement Jeff (loose-passive): an
aggressive/disciplined "winning reg" — folds correctly (so we can't bluff-less
our way to value), **bluffs/barrels** (so it *punishes over-folding*), and
value-bets thin (so it *punishes over-calling*). Mirror
`experiments/clone_profiles/jeff.json`; register via the portable-clone path
(`_ensure_clone_registered` / the `human_clone` derive logic — works without a
populated DB). Run it alongside `jeff` in `measure_passivity` as a second
opponent type so wins must hold vs *both* a station and a punisher (or they're
overfit).

### P1 — Full WTA-SNG runner (absolute, gold standard, bigger build)
The honest final eval and the one that matches the real game: **escalating
blinds, elimination, play-to-one-winner, win-rate** (not fixed-depth bb/100).
Exercises the depth progression (100→50→25→push-fold) that fixed-depth runs
miss, and rewards survival/accumulation correctly (WTA ⇒ chip-EV = $EV).
Sequence **after** P0/P0.5 — it's the most work; don't block early iteration on it.

## How they fit together
- **P0** answers "is this change an improvement?" — cheap, run on every change,
  **gates chart authoring**.
- **P0.5 + P1** answer "is the bot actually good / correct?" — run periodically
  and at milestones.
- Keep the **existing** `jeff` + `gto`/`mix` runs as a generalization band (a
  win must not *regress* vs any opponent type), and always watch
  `--leak-report` + per-hand-class postflop splits (confirm the change expresses
  *intent*, not just moves bb/100).

## Risks
- **Champion-vs-challenger is relative** — both versions can share a blind spot
  it won't reveal (e.g. both fold the nuts → head-to-head looks even). Pair it
  with the absolute evals for blind-spot coverage.
- **Clone fidelity** — a stats profile isn't a real player; the punisher clone
  is a proxy. Good enough to catch over-folding/over-calling, not a substitute
  for the SNG runner / real data.
- **SNG runner scope** — escalating blinds + elimination + multi-table-ish
  bookkeeping is real work; scope it tightly (single-table WTA first).

## References (verify file:line — point-in-time 2026-05-25)
- `experiments/measure_passivity.py` — the current eval harness (`--opponents
  jeff|gto|mix`, `--stack-bb`, `--leak-report`, ProcessPool paired seeds).
- `experiments/simulate_bb100.py` — `make_controller` (assigns one
  `strategy_table` to all seats — the hook to make per-controller); the
  flag-gating pattern (`enable_multistreet_context`, `enable_value_bet_floor`).
- `experiments/clone_profiles/jeff.json` + the portable-clone registration
  (`_ensure_clone_registered`; `poker/human_clone.py` derive/register).
- `push-fold-6max` branch — per-controller push/fold toggle (the pattern for
  per-seat config in the harness).
- `docs/plans/CHART_COVERAGE_AND_GENERATION.md` — the chart work this eval gates.
- `docs/plans/STRUCTURAL_PASSIVITY_PLAN.md` §3 — the Tier-A/B/C baseline metrics
  (this doc is the *opponent/harness* half; that doc is the *metrics* half).

## Recommended order for the fresh context
1. **P0 champion-vs-challenger, flag flavor** (trivial) — re-judge the *already
   shipped* flag-gated changes (multistreet H2, value-bet floor, SPR fallback)
   head-to-head: are they improvements vs the bot itself, or only vs stations?
   This alone re-grounds every result so far.
2. **P0 chart-file flavor** (per-controller table) — so chart-file changes
   (depth charts, future SPR/3BP slices) are judgeable the same way.
3. **P0.5 punisher clone** — add the non-station opponent; re-run the shipped
   changes vs it. Anything that only wins vs the station is overfit.
4. **Gate all further chart authoring** (P2 3BP, medium-SPR, LLM grids in
   `CHART_COVERAGE`) behind P0/P0.5.
5. **P1 SNG runner** when ready — the final word.

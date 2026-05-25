---
purpose: Scope and build-vs-buy analysis for generating solver-derived lookup charts (the "~32 sets of charts") across player counts, stack depths, action histories, and bet sizings
type: design
created: 2026-05-24
last_updated: 2026-05-25
---

# Solver Chart Scope — the full lookup-table program

> **Provenance.** This doc reconstructs a planning discussion from
> **2026-05-17** that was never written up (only its compressed summary
> survived, in `docs/vision/NEXT_PHASE_VISION.md` → Bucket 6). The source
> conversation is Claude Code session
> `c987d5d0-c14d-4994-80e9-51d79f04043c` (fork: `330caf2c-…`) in the
> `my-poker-face-tieredbot-messages` project. The "~32 sets of charts"
> recollection maps to the **comprehensive scope (~30 solves)** /
> **5-depth (35 solves)** framings below.

## Why this exists (the actual leak)

The bot's decision engine reads hand-authored **100 BB** strategy tables
(`preflop_100bb_6max.json`, `preflop_100bb_hu.json`, `postflop_strategies.json`).
The live game is **winner-take-all sit-and-go**. A SNG starts deep and
blinds escalate, so by 3–4 handed stacks are commonly **25–50 BB
effective**, and heads-up it's often **15–25 BB each**.

> The bot is playing 100bb-tuned tables at 25bb-effective stacks for most
> of the consequential decisions.

At 25 BB effective: implied odds collapse, speculative hands lose value,
some hands cross into push/fold, SPR drops fast. A 100 BB range here calls
marginal hands that should fold, raises too small, and misses push/fold
spots. **The exploitation layer can't fix this — the base tables are wrong
for the stack depth.** Solver-generated, depth-correct charts are the
structural fix.

## The unit: one solve = one full chart (not one hand)

The cost driver is the **solve count**, not the chart count. One solve =
one CFR run against one game tree, and it emits strategy for **every**
decision point inside that tree:

- **One 6-max preflop solve** produces RFI for every position **plus** all
  the vs-open / vs-3bet / vs-4bet defender×opener pairings — hundreds of
  decision points from one run. It also *subsumes* the HU and 3-way
  preflop spots as subtrees (e.g. "everyone folds to SB, BB to act" is the
  HU preflop spot), so **you only need one preflop solve, not three.**
  (Confidence: medium-high; depends on the card abstraction treating the
  HU subtree like a standalone HU solve — verify when building.)
- **One postflop solve** for a given (player count, action history, depth)
  produces strategy for **every** flop/turn/river runout inside that
  subtree — thousands of decision points. A 12-hour solve is "12 hours for
  the whole table," not "12 hours per hand."

So "sets of charts" ≫ solve count. The numbers below count **solves**.

## The dimensions that multiply

| Dimension | Values | Notes |
|---|---|---|
| **Player count** | HU · 3-way · (4-way, skippable) · 6-max | Postflop diverges per count; preflop folds into one 6-max solve |
| **Stack depth** | 30 / 50 / 100 / 150 / 200 BB | Each depth is a **fresh tree** — multiplies linearly, no reuse in standard CFR |
| **Action history** (postflop only) | SRP · 3-bet pot · 4-bet pot · limped pot | Different starting pot + ranges → separate solve each |
| **Bet sizing / overbets** | preflop 2.5bb open, 3x 3-bet, 2.2x 4-bet; postflop 33/67/100/jam | Overbets (125/150/200%) **don't add scenarios** — they multiply solve *time* within a node ~2–4×. Scope surgically (river-only is cheapest) |

## Scope tiers (cash, solve counts + compute)

| Tier | Composition | Solves | Est. compute |
|---|---|---|---|
| **Minimal** | 1 HU preflop + 3 HU postflop (SRP/3BP/limped) + 1 3-way preflop + 2 3-way postflop + 1 6-max preflop | **~8** | — |
| **Comprehensive** (3 depths, light overbets) | HU 4×3 + 3-way 3×3 + 6-max preflop ×3, +~30% overbet overhead | **~30** ← the "~32" | — |
| **Full** (5 depths, full overbets) | HU 7×5 + 3-way 5×5 + 6-max preflop ×5, +50–100% overbets | ~100–130 | — |

Stack-depth grid in isolation (linear multiply):

| Depths | Solves | Compute |
|---|---|---|
| 100 BB only | **7** | 4–8 days |
| 50 / 100 / 200 BB | **21** | 12–24 days |
| 5 depths | **35** | 20–40 days |

## Decision for THIS game (6-handed, WTA SNG)

- **6-handed**: no scope change — 6-max preflop solving is the standard
  target anyway; 3-way postflop solves stay valid (most pots are ≤3-way by
  the flop); 6-way postflop is the cuttable item.
- **Winner-take-all is a major simplification**: WTA ⇒ chip EV = $ EV
  throughout ⇒ **ICM does not apply**. The entire ICM-aware CFR addition
  (budgeted ~1–2 weeks eng + compute) **goes away**. No bubble, no pay
  jumps, no late-stage preservation incentive — just maximize chip EV.
- **What WTA SNG actually needs**: chip-EV solves at **multiple stack
  depths** (this is the part the current 100bb-only tables get wrong).
  Antes still widen preflop ranges, so ante-on vs ante-off is a real axis
  if we want it precise.
- **Recommended sequencing** (de-risk before spending compute): solve
  **100 BB first**, validate the bot improves, then add the short-stack
  depths (the ones SNGs actually reach: ~50, ~25–30 BB, plus published
  push/fold for <15 BB) in a second pass. Comprehensive depth coverage is
  worth waiting on until the foundation is proven.

## Build vs buy (tooling landscape)

The combination we want — **scriptable multi-way preflop + postflop solver
output as JSON** — **does not exist off the shelf.** Every option fails on
scope, license, or workflow:

| Tool | ~Price | Fit / limitation |
|---|---|---|
| **PioSolver (Edge)** | ~$1099 | Industry-standard postflop, scriptable + Python in *Edge tier only*; **HU-only preflop**; GUI-first, proprietary `.cfr` output |
| **MonkerSolver** | ~$700+ | Only commercial solver with first-class **multi-way preflop**; CLI/batch scripting; less polished, thinner docs |
| **GTO+** | ~$75/qtr or ~$249 | Cheaper Pio alternative, CLI + Python automation; **HU-only preflop** |
| **TexasSolver** (orig.) | free (AGPL) | Forkable but **HU-postflop only**. TexasSolver**GPU** is closed-source binary, *not* open (its `6max_range/` folder is most likely opponent-range definitions for HU solves, not a true multi-way solver — verify with dev) |

**Build-your-own (CFR)** is viable and the timeline was walked back from
"1–2 years to match Pio at full scope" to a much smaller target: you don't
need to *match Pio*, you need *good-enough output for this specific game*.
Core algorithm is **Counterfactual Regret Minimization** (regret-matching
over info sets; average strategy → Nash for 2-player zero-sum). WTA removes
the ICM-CFR complication entirely.

## Early validation checkpoints (cheap kill-switches, in build order)

Each catches a different failure class before compute is spent:

1. **Day 1–2 — Kuhn poker** converges to its closed-form Nash equilibrium
   (the canonical "is my CFR loop correct" test). Cost: a few hours.
   Catches gross algorithm bugs.
2. **Day 3–5 — Leduc poker** matches published reference strategies
   (larger toy game, multi-street). Catches abstraction/multi-street bugs.
3. Then scale to real trees; validate the bot's bb/100 actually improves at
   the target stack depths before expanding depth coverage.

## Relationship to existing docs

- `docs/vision/NEXT_PHASE_VISION.md` — Bucket 6 (Solver) is the compressed
  roadmap version of this discussion; the granular multiplication math here
  is what that bucket summarized.
- `docs/technical/TIERED_BOT_ARCHITECTURE.md` — the v1 engine that *consumes*
  these charts (v1 scope is deliberately narrow: 6-max preflop + HU-SRP-flop
  postflop, 100 BB only). This doc is the v2+ program that fills the
  "NOT in v1 scope" gaps (solved turn/river, multiway, short-stack depths).

## Status

Planning/scoping only — **no solver build has started.** Next decision gate
is the solver-viability question in `NEXT_PHASE_VISION.md` Bucket 6 (a
Cepheus/Kuhn match pilot) before committing to a build.

## MEASURED: the short-stack leak is real and large (2026-05-25)

The "100bb tables at 25bb effective" premise was previously an untested
hypothesis. It is now **measured** (`experiments/measure_passivity.py --stack-bb`,
Baseline vs the Jeff_clone human model, 3000 × seeds 42/142/242, fixed-depth
proxy — per-hand reset, not full SNG dynamics):

| Effective stack | bb/100 | AggFactor |
|---|---|---|
| 100bb | **−4.2** | 0.27 |
| 50bb | **−18.8** | 0.16 |
| 25bb | **−21.8** | 0.06 |
| 15bb | **−2.1** | 0.01 |

**The leak is concentrated at 25–50bb (−18 to −22 bb/100), ~4–5× the 100bb
leak.** This confirms the premise — and refines the target:
- It is **NOT a push/fold problem**: 15bb is already near break-even (jam/fold
  is forgiving + small stacks compress the loss). Published <15bb push/fold
  charts are therefore **low priority**.
- The money is lost in the **25–50bb middle** — real shallow-SPR poker (preflop
  ranges too loose, raises too small, marginal calls, missed commit/jam spots).
  The bot also gets *less* aggressive as stacks shorten (AF 0.27→0.06), the
  opposite of correct.
- So the highest-value chart target is **depth-correct 25–50bb** strategy
  (preflop + postflop), NOT the <15bb push/fold table that `PUSH_FOLD_6MAX_SCOPE`
  scoped first.

**Recommended next (de-risked, before committing to the full solver):**
1. **Diagnose** *what* the bot does wrong at 25–50bb (leak surface / per-action
   breakdown at those depths) to scope whether a depth-tuned hand-authored
   chart + sizing/commit heuristics recovers most of it cheaply.
2. If a hand-authored 25–50bb chart materially closes the gap, that may suffice
   (the 100bb→fix pattern). If not, that's the concrete justification for a
   25–50bb **solve** (the doc's "then add short-stack depths" pass).
3. The fixed-depth proxy here understates/overstates vs true SNG dynamics; a
   **full WTA-SNG runner** (escalating blinds, elimination, win-rate) is the
   honest final eval — build it once a 25–50bb fix is in hand to validate.

Harness: `measure_passivity.py` now takes `--stack-bb` (effective depth knob).
The full-SNG runner is not yet built.

### DIAGNOSED: the leak is *zero preflop depth-adjustment* (2026-05-25)

Added preflop instrumentation (`measure_passivity` PREFLOP section: VPIP/PFR/
jam%/avg-open + by-scenario split). Swept Jeff by depth — the result is
unambiguous: **preflop play is byte-identical at 100/50/25bb.**

| Depth | VPIP | PFR | jam% | avg open | vs_open f/c/r/jam | vs_3bet f/c/r/jam |
|---|---|---|---|---|---|---|
| 100bb | 18% | 14% | 0.4% | 3.3bb | 69/18/13/0 | 79/12/9/0 |
| 50bb  | 18% | 14% | 0.4% | 3.3bb | 69/18/13/0 | 79/12/9/0 |
| 25bb  | 18% | 14% | 0.4% | 3.3bb | 69/18/13/0 | 79/12/9/0 |
| 15bb  | 17% | 14% | **7.1%** | 3.2bb | 68/18/7/**7** | 85/11/0/4 |

**The bot has no depth-awareness above 15bb.** It opens to 3.3bb (= 13% of a
25bb stack), ~never jams, and flat-calls 18% vs opens (a commitment error when
shallow). Only the `<20bb` short-stack heuristic kicks in at 15bb (and weakly).
Postflop compounds it: the small opens make low-SPR flops the bot plays
deep-passive (at 25bb it checks the nuts 89% unopened, raises facing a bet 1%;
AggFactor 0.27→0.06 across depths).

**Scope of the fix (preflop is the dominant lever):** depth-correct preflop
charts at ~50bb and ~25bb — polarized 3-bet/open **jam** ranges, larger relative
sizing, less flat-calling — plus low-SPR postflop commit logic. Because the
current behavior is *zero adjustment*, even a **coarse hand-authored depth chart
should recover most of the −18 to −22** (the cheap 100bb→fix pattern); a 25–50bb
**solve** is the principled ceiling, justified only if the hand-authored pass
stalls. <15bb push/fold remains low priority (15bb already ~break-even).

---
purpose: Recommendation on whether to replace the rule-bot casino fish with the unified tiered calling_station archetype
type: design
created: 2026-05-29
last_updated: 2026-05-29
---

# Should casino fish be the tiered `calling_station` instead of a rule bot?

## TL;DR — Recommendation: **(C) Hybrid**, medium-high confidence

Make the casino fish a **tiered `calling_station` base** (one unified decision
engine), and re-express the deliberate readable leaks as **`spot_tendencies`**
on the fish personas (the system is already built for exactly this — see
`PERSONALITY_LEAK_WIRING.md`). Do **not** do a naive lift-and-shift that throws
away the leaks (that loses real product value), and do **not** keep the parallel
rule-bot path indefinitely (it's a maintenance fork that the rest of the game
systems already route around).

Confidence is medium-high on the *direction* (unify the engine — the architecture
makes this clean and low-risk) and medium on the *exact leak fidelity* (one
specific tell — honest size=strength bet sizing — has **no** spot-tendency
equivalent today and would be lost or need new code; see Trade-offs).

This is a deliberate, staged migration, not a one-line swap. The economy,
seating, teardown, and movement layers are all controller-agnostic, so the blast
radius is small and contained to controller *construction*.

---

## Why this is even on the table

We just built a **unified width-tier table system** (`PERSONALITY_PRICING_AND_VARIETY.md`,
"VARIETY WIDENING — BUILT") that produces a *true* calling station inside the
same logit-bounded tiered engine every other career opponent uses. Measured
(Baseline hero on the station table, vs a Baseline roster): **VPIP 45 / PFR 16 /
postflop AggFactor 0.26 / payoff ~79%** — textbook loose-passive caller — and its
paired-CRN combo price is **−51 bb/100 (CI-clear)**: a genuine, priced losing
fish.

Meanwhile the casino fish are still a **separate code path**: the `fish` strategy
in `poker/rule_strategies.py` run via `RuleBotController`, with hand-coded leak
variants. The user's framing: *"now that we have true calling stations, maybe
that's a more true way to stick to the game systems"* — i.e. one decision engine,
not two.

---

## Current implementation (mapped)

**The fish strategy.** `poker/rule_strategies.py::_strategy_fish` — a loose-passive
calling station that value-bets strong hands with **honest, monotonic, unbalanced
sizing** (`FISH_BET_NUTS 0.66` > `FISH_BET_STRONG 0.50` > `FISH_BET_MEDIUM 0.40`;
"a fish that bets has something"), never raises a bet and never bluffs at
baseline. On top sit **deliberate, identifiable leaks** (`FishLeak` enum): 7
passive/calling leaks (calls_down_top_pair, chases_any_draw, limps_every_hand,
pot_committed_early, overvalues_face_cards, doesnt_believe_big_bets,
calls_river_light) + 4 aggression leaks (spite_raises_when_losing,
bets_strong_transparently, spews_bluffs, sticky_then_pops).

**Personas.** `poker/personalities.json` has **9 fish** (Vacation Greg,
Bachelorette Brenda, Cruise Carl, Birthday Bobby, After Hours Trent, Lucky Mona,
Slots Linda, Golf Trip Brad, Freddie Fratboy), **each with a distinct
`fish_leak`** — a real, hand-curated leak catalogue. Each entry carries BOTH:
- `archetype: "fish"` — the **economy/seating key**, and
- `rule_strategy: "fish"` + `fish_leak: "<leak>"` — the **controller-routing key**.

Crucially, the 4 fish currently seeded in the live DB all carry anchors that
already **classify as `calling_station`** (looseness 0.75–0.95, aggression
0.15–0.30 → `select_deviation_profile_key` → `calling_station`). They are routed
to the rule bot *only* because of the `rule_strategy: 'fish'` field — strip that
field and `assign_bot` sends them straight to the tiered calling_station.

**How fish are seated + funded (controller-agnostic).** `cash_mode/casino_provisioning.py`
writes seats via `ai_slot_fish(pid, chips)` which stamps `archetype='fish'`. That
stamp — plus `config_json.archetype == 'fish'` — is the **single source of truth**
for "this seat is a fish" across:
- `cash_mode/closed_economy.py::load_fish_ids` (economy accounting / drain metrics),
- `personality_repository.list_fish_for_cash_mode` (seat-eligibility pool),
- teardown / movement / predator-retention paths.

**None of these key on controller type or on `rule_strategy`.** The accounting,
seating, refill, and teardown machinery does not care whether a fish thinks with
a rule bot or a tiered bot. This is the central architectural fact that makes the
migration safe.

**Where the controller is actually built (the only things that change):**
1. `flask_app/routes/cash_routes.py` ~810 — the sit route (`rule_strategy_override == "fish"` → `RuleBotController(strategy="fish", fish_leak=...)`).
2. `flask_app/handlers/game_handler.py` ~1983 — mid-session live-fill (mirrors #1).
3. `flask_app/handlers/game_handler.py` ~466 — **restore/cold-load**: fish are saved as `bot_type="fish"`, which falls through the generic `else` → `RuleBotController(strategy="fish")` **with no `fish_leak`** (a pre-existing latent bug: cold-loaded fish lose their leak).

Plus `cash_mode/full_sim.py::_build_controller` (the sim mirror of prod) for
sim-parity. (`flask_app/routes/game_routes.py` ~1526 also builds a RuleBotController,
but that's the custom-game `casebot`/`gto_lite` training path — fish do not reach it.)

---

## What swapping to tiered `calling_station` entails

**Routing.** Fish personas already have `calling_station`-classifying anchors. The
swap is: at the 3 controller-construction points (+ the sim mirror), build a tiered controller
(`build_tiered_controller`, expression disabled) instead of `RuleBotController`,
for `archetype == 'fish'` seats. (Cleanest long-term: drop `rule_strategy:'fish'`
from the personas so the normal `assign_bot` path classifies them as
calling_station; but keep `archetype:'fish'` — that's the economy key.)

**Does tiered need anything the rule bot didn't?**
- **LLM/expression:** No. `build_tiered_controller(..., expression_enabled=False)`
  → zero LLM calls. Decisions are table lookups. (Both controllers already build
  an `Assistant` at init via the shared `AIPlayerController` base, but neither
  calls the LLM on the decision path for fish.)
- **Psychology anchors:** Tiered *wants* anchors (drives the deviation profile +
  width table). Fish already have them. ✓
- **Opponent model:** `adaptive_overbet` would need one, but fish don't get
  attacks — irrelevant. The station's *passive* behavior needs no opponent model.
- **Equity MC:** Tiered runs a per-decision Monte Carlo equity calc (~200–500 ms)
  unless `skip_equity_in_analysis` is set. The rule fish computes a cheaper
  made-hand classification. **At casino scale this is the one real cost delta** —
  see Trade-offs. Mitigation: the equity calc only feeds the decision *analyzer*,
  not the table decision; it can be skipped on fish exactly as sims do.

**Economy/accounting:** Indifferent (keys on `archetype='fish'`, not controller).
No migration needed. ✓

---

## Behavioral fidelity — measured comparison

Method: `experiments/measure_passivity --hero <name> --opponents
Baseline,Baseline,Baseline,Baseline,Baseline --hands 1500 --seeds 42,3042,6042`
(tiered Baseline roster, no equity MC, ~30× faster than gto/clone rosters).
**The bb/100 here is a 1-vs-5-Baseline roster artifact (over-rewards aggression,
over-punishes passivity) — read the loss DIRECTION, not the magnitude.** VPIP /
PFR / AggFactor / payoff are ~opponent-independent and are the real signal.

| hero | engine | VPIP | PFR | postflop AF* | payoff | bb/100 (artifact) |
|---|---|---|---|---|---|---|
| **Calling Station** | tiered | **45%** | **16%** | **0.261** | **79%** | −68 (−115/−40/−48) |
| Fish (baseline) | rule bot | 99% | 0% | 0.000 | 74% | −110 |
| Fish-Transparent | rule bot | 99% | 0% | 0.000 | 81% | −212 |
| Fish-Spew | rule bot | 99% | 0% | 0.000 | 71% | −68 |
| Fish-Sticky | rule bot | 99% | 0% | 0.000 | 82% | −244 |

\* AF = aggressive / (check+call) — **not** PokerTracker AF; use the ordering, not the
absolute value. 4500 hands (tiered) / 6383 decisions (rule fish), 1500×3 seeds. **All
five are net losers** (the intended direction). bb/100 magnitudes are a 1-vs-5-Baseline
roster artifact — directional only.

Tiered Calling Station facing-bet split (a graduated caller): air 62% fold / 27% call;
weak_made 35% fold / 61% call; medium_made 12% fold / 80% call; strong_made 0% fold /
70% call / 30% raise; nuts 79% call / 21% raise.

### Reading the comparison

- **Both shapes are net-losing fish — direction confirmed.** The tiered station is a
  textbook caller (VPIP 45 / PFR 16 / AF 0.26 / payoff 79%), matching the prior
  4500-hand variety measurement exactly.
- **The rule fish is MORE extreme than a real station — arguably *too* fishy.** In
  this 1-vs-5-Baseline harness it reads **VPIP 99 / PFR 0 / AF 0.000** with **0
  "unopened" decisions**: the Baselines always have initiative, so the fish's honest
  value-betting branch *never fires* and it degenerates into a near-pure
  call-everything / never-fold machine — closer to `always_call` than to a textbook
  calling station. (Partly a harness artifact: in a real casino mix with grinders the
  fish *would* sometimes be checked-to and value-bet. But it confirms the rule fish
  sits at the passive extreme, not the calibrated-station middle.)
- **The tiered station folds air and grades its calls by hand class** (air 62% fold,
  weak_made 35% fold, made hands call/raise) — a *more realistic, more readable*
  caller than the rule fish's flat 99% call. For a human learning to value-bet thin
  against a station, the tiered version is the better teacher: it actually folds the
  bottom of its range, so betting your good hands is rewarded and bluffing is
  punished — exactly the lesson. The rule fish (calls everything) teaches "never
  bluff, always value bet" but nothing about range.
- **Payoff rates are comparable** (tiered 79% vs rule fish 71–82%) — the core "pays
  off value bets" property the exploiter targets is present in both.
- **Caveat:** the rule fish's 99/0 here overstates its passivity because the harness
  denies it initiative. Don't read the rule fish as literally never value-betting in
  production — read it as "sits at the passive extreme, with a value-bet tell that
  only surfaces when checked to." The tiered station's profile is harness-robust
  (it's the same vs any roster).

---

## Exploitability / skill-gradient fit

This is the strongest argument for unifying. The variety program's whole point is
that **weak bots are exploitable leaks the built exploiters (value overbet,
multistreet barrel) punish — closing leak↔exploiter loops a human can also learn.**

- The value-overbet exploiter (`exploit_bb100.py`, the `value_vs_station` rule,
  +42 bb/100 vs payers) **explicitly targets a station that pays off** and reads
  its target via **frequency stats (vpip / fold-to-cbet / AF)**. The tiered
  calling_station is a leak *measured in the same engine the exploiter reads*, so
  the loop is closed by construction.
- The rule-bot fish's leaks are hand-coded in a **separate decision path** the
  frequency detectors were never validated against. The `_sticky` spot tendency
  (river bluff-catch over-call, priced −1.87 bb/100) is *literally* the
  Fish-Sticky leak re-expressed in the unified engine, and the doc names it as
  the exact spot the value overbet is "designed to punish." Keeping the rule fish
  means the showcase exploit loop runs against a bot that isn't in the system the
  exploit reasons about.
- A tiered fish also gets the **defenses/attacks composition model** for free
  (`PERSONALITY_LEAK_WIRING.md`): a fish is `{2–3 leaks · guardrail on · no
  attacks}` — a clean, *priced* handicap on the same axes as every other tier,
  rather than a bespoke ladder of `if leak == ...` branches.

Net: **the tiered calling_station is a strictly better exploit target** for the
tooling we actually built.

---

## Trade-offs

**Unification / maintainability — favors swap.** One decision engine. The rest of
the game already routes *around* the rule bot (it's a special-case fork in 3
places, with a latent cold-load bug that drops the leak). Deleting that fork
removes special-casing and a bug class. `assign_bot`'s own docstring frames fish
as the awkward upstream exception.

**Behavioral fidelity — the real cost, mixed.**
- *Loose-passive caller shape:* preserved (measured below — the station is a
  textbook caller).
- *The honest size=strength bet-sizing tell* (`FISH_BET_NUTS/STRONG/MEDIUM`,
  monotonic, unbalanced): **lost.** The tiered station uses solver-derived
  (balanced) sizing from the strategy table, and **there is no sizing-tell
  spot_tendency** in the registry (the 9 tendencies reshape action
  *frequencies*, not bet *sizes*). This was a deliberate, recognizable "fish that
  bets has something" tell. Re-creating it needs either a new sizing-tendency hook
  or accepting its loss. (Counterpoint: the adaptive-overbet attacker that would
  punish a sizing tell doesn't fire / has no reader in the field today — the
  pricing doc shelved it — so the *practical* exploit value of the sizing tell is
  currently ~0. The loss is mostly cosmetic/character today.)
- *The 9 distinct hand-coded leaks* (calls_down_top_pair, chases_any_draw,
  limps_every_hand, etc.) map only partially onto the 9 spot_tendencies
  (sticky, over_bluff, under_bluff, slowplay, fit_or_fold, give_up_turn,
  auto_cbet, over_fold_2nd_barrel, donk_when_weak). The *passive caller* leaks are
  largely already baked into the calling_station envelope (high VPIP, low
  fold-to-cbet, sticky payoff); the *aggression* leaks (spew, transparent,
  spite-raise) have partial analogues (over_bluff, auto_cbet) but not 1:1.
  `limps_every_hand` has no limp in the charts at all. So a faithful port is a
  per-persona mapping exercise, not a free lunch.

**Cost / latency at casino scale — favors keeping a cheap path, mitigable.** The
tiered equity MC (~200–500 ms/decision) is the one scale risk. Mitigation:
`skip_equity_in_analysis = True` on fish (exactly what sims do) — the equity calc
only feeds the analyzer, not the decision. With it off, tiered fish are
table-lookup fast like the rule bot. LLM cost is zero either way (expression off).

**Economy / accounting — neutral.** Controller-agnostic (keys on
`archetype='fish'`). No migration. ✓

**Calibration risk — flag.** The station table + the −51 combo price are measured
at **100bb 6-max**. Casino tables seat fish at **~40bb buy-ins** (`min_buy_in`,
40 BB) among 4–5 grinders. The tiered bot has depth charts (50/25bb) but the
*archetype width tables* are 100bb; whether the station stays a clean −EV caller
at 40bb casino depth is **unmeasured** and must be re-validated before cutover.
The rule fish, being depth-agnostic rules, sidesteps this (it just calls), which
is part of why it was chosen originally.

---

## Migration sketch (for option C)

1. **Keep `archetype:'fish'`** on every fish persona (economy/seating key —
   do not touch).
2. **Re-express leaks as `spot_tendencies`** per persona in `personalities.json`,
   mapping each `fish_leak` to its closest tendency (per
   `PERSONALITY_LEAK_WIRING.md`'s archetype→leak table): the can't-fold callers →
   `["sticky", s]`; the spewers → `["over_bluff", s]` / `["auto_cbet", s]`; etc.
   Accept that a couple (limps_every_hand, transparent sizing) have no clean
   analogue — decide per persona whether to drop, approximate, or build a new
   tendency. Remove `rule_strategy:'fish'` and `fish_leak`.
3. **Route fish to tiered** at the 3 construction points: where
   `rule_strategy_override == "fish"` is checked today, instead build
   `build_tiered_controller(..., expression_enabled=False)` and set
   `skip_equity_in_analysis = True`. Simplest: delete the special-case branches
   entirely and let `assign_bot` classify the (now calling_station-anchored)
   fish — but verify the restore path (`bot_type` save/restore) and the
   cold-load leak-drop bug are handled (saving `bot_type="sharp"` for fish fixes
   the restore branch automatically).
4. **Mirror in `cash_mode/full_sim.py::_build_controller`** so sim-parity holds.
5. **Re-validate before cutover:**
   - Re-measure the calling_station (and each leak-carrying fish persona) at
     **40bb casino depth**, not just 100bb — confirm it stays a net loser and a
     recognizable caller.
   - Run a `cash_mode` closed-economy sim and confirm `fish_net_to_players` stays
     positive (fish still feed the population) and chip conservation holds.
   - Confirm the value-overbet exploiter still extracts from the tiered fish (the
     leak↔exploiter loop) at casino depth.

### "A calling_station fish persona" looks like
```json
"Vacation Greg": {
  "archetype": "fish",                       // economy/seating key — KEEP
  "anchors": { "baseline_looseness": 0.85, "baseline_aggression": 0.15, ... },  // → calling_station
  "spot_tendencies": [["sticky", 0.7]]       // the re-expressed leak (replaces fish_leak)
  // rule_strategy + fish_leak removed
}
```

---

## Honest provenance

- **Measured (this memo):** the passivity/VPIP/PFR/AF/payoff/bb100 comparison
  table below (1500 hands × 3 seeds vs 5×Baseline).
- **Cited (prior work):** the −51 bb/100 combo price, the +42 value-overbet edge,
  the −1.87 sticky price (from `PERSONALITY_PRICING_AND_VARIETY.md` paired-CRN
  runs).
- **Inferred from code (not run):** the controller-agnostic economy claim (read
  `load_fish_ids` / `list_fish_for_cash_mode` / `ai_slot_fish`); the 3
  migration points; the cold-load leak-drop bug; the equity-MC latency delta; the
  lost size=strength sizing tell. The **40bb-depth calibration risk is
  explicitly unmeasured** and is the gating item before any cutover.

---
purpose: Executable plan + hard-won findings for building a genuinely better poker bot — start here next context
type: guide
created: 2026-05-30
last_updated: 2026-05-30
---

# Build a better bot — NEW-CONTEXT START HERE

> **Read this first.** A long session built `CaseBotV2` (a strong fish-hunter,
> now the promoted `casebot` bot type) and then tried five ways to make it
> "smarter." **Every one made it worse.** The reason that keeps recurring is the
> single most important thing to understand before writing any more bot code, and
> it points at the real next step. Full numbers: `docs/eval_results/VARIETY_VALIDATION_RESULTS.md`.
> Narrative: `docs/captains-log/lookup-tables/variety-validation-and-deploy.md`.

## TL;DR — the one finding that governs everything

**Our entire opponent pool is exploitable (fish, maniacs, stations, leaky
clones). Against an exploitable pool, a naive aggressive exploiter beats every
"accurate"/balanced approach — because the naivety happens to align with the
exploit.** Concretely, CaseBotV2's vs-random equity has two "errors": it
*overstates* hand strength (→ value-bets more → callers pay) and *ignores*
opponent aggression (→ calls down → catches bluffers). Both errors are +EV vs a
leaky field. Every "fix" that corrected them pulled the bot toward *balance*, and
balance under-exploits fish.

So **"better" is ambiguous and you must pick which one you mean (see §1) before
building anything** — because "beats our fish pool harder" and "is good poker /
robust vs a competent human" are *opposite* directions in this codebase.

## What we have (the map)

- **`CaseBotV2`** = `_strategy_case_based_v2` (`poker/rule_strategies.py`).
  Promoted: the `casebot` bot type now uses it (`flask_app/routes/game_routes.py`
  + `flask_app/handlers/game_handler.py`). It is v1 (`case_based`) + bigger pots
  with strong hands (value-raise premium/strong preflop, overbet them postflop).
  Beats v1 4–12× vs the human clones. **Playstyle: ~95% VPIP preflop (a calling
  station — almost never folds preflop), but disciplined postflop (value-bet,
  call down by pot odds, fold air to bets).** It's a fish-hunter, NOT good poker.
- **Eval tools (reusable):**
  - `experiments/casebot_gauntlet.py` — one scorecard, HU + 6-max vs every field.
  - `experiments/variety_eval.py` — archetype × depth × field sweeps.
  - **The AB-battery pattern** (the right way to measure "is X better"): candidate
    hero vs v1×5 head-to-head + vs the **clone profiles** (`jeff`=calls-down human,
    `punisher`=reg), 6-max, many seeds. NOT absolute bb/100 vs caricatures.
    Parallelize on Hetzner: one `docker compose run` per (hero,field) cell, fanned
    across cores (see `/root/ab2.sh` pattern in the captain's log; `docs/EVAL_RUNNER.md`).
  - `ARCHETYPE_STATS` (`experiments/simulate_bb100.py`) — perfect-read opponent
    stats per archetype (for range-aware experiments).
- **Clones:** `experiments/clone_profiles/jeff.json` (calls-down human, vpip 0.39),
  `punisher.json` (reg: folds correctly + barrels air). These are the most
  realistic opponents we have — measure against them.
- **Perf:** equity MC is 64 sims now (`calculate_quick_equity`, `poker/controllers.py`),
  ~5× faster than the old 300, decisions unchanged. Live showdown-equity telemetry
  is async (`enable_async_equity_telemetry`, `poker/memory/memory_manager.py`).

## Dead ends — do NOT repeat these (all measured, all lost)

1. **Tighten CaseBot / make it raise-or-fold.** Regressed in every cell incl. vs
   the punisher (+457→+5). Tightening under-extracts from the leaky pool.
2. **`made_tier` instead of MC equity** (deterministic, 7× faster). Made CaseBotV2
   *lose* to maniacs (−96, all seeds) — a categorical map can't reproduce the
   per-board nuance, and calibrating for callers over-valued hands into the
   maniac's range. Reverted. (`equity_from_made_tier`, kept unwired.)
3. **Range-aware equity** (`calculate_equity_vs_ranges` / `get_opponent_range`).
   WORSE everywhere, collapsed vs maniac (+150→+0.6): the range model's
   *aggression adjustment treats a maniac's bets as a STRONG range* → folds to its
   bluffs. (`CaseBotRange` / `use_range_equity`, kept as documented dead end.)
4. **Anti-maniac "tight defense" reg** (defend blinds wide + call down + raise
   back). Made the maniac's edge WORSE (+102→+352): the tiered maniac has a real
   value range, so wide-calling pays it off and raising back gets stacked. You
   can't out-tight a maniac. (`Reg`/`RegVsManiac`/`reg_adaptive`.)
5. **The tiered `hyper_aggressive` exploitation layer.** Inert (paired CRN null,
   TAG −9.3/Nit −1.9 CI∋0). It widens calls vs all-ins/own-opens but has NO
   blind/steal-defense (`fold_to_open`/PHASE_8_1 unimplemented) — defends the wrong
   street, and is clamped near GTO so it can't deviate far enough to matter.
6. **Chasing HU bb/100.** It swings ±60 across samples (CaseBot's per-decision MC
   makes HU hands slow → can't sim enough). HU is unmeasurable at feasible scale —
   measure 6-max + paired comparisons instead.

Common thread: **#1–#3 add accuracy/discipline → balance → under-exploit. #4–#5
assume the opponent is strong (maniac bets = strength) → wrong vs a leaky field.**

## §1 — DECIDE what "better" means (ask Jeff, gate everything on this)

Three different targets, *opposite* directions:
- **(A) Harder fish-extraction** — beat our casino pool by more. CaseBotV2 already
  near-maxes this; gains are marginal and risk overfitting to caricatures.
- **(B) Robust / not-exploitable-by-a-human** — survives a competent human who
  3-bets it and stops paying its overbets. CaseBotV2 FAILS this (95% VPIP). This is
  "good poker," and it requires *tightening*, which costs (A).
- **(C) Adaptive** — plays (A) vs fish and (B) vs competent, switching on a read.

**Recommend (C), but it is blocked on the keystone below.** Whatever the target,
the eval can't currently tell (A) from (B) because **we have no competent opponent
in the pool** — that's the real gap.

## §2 — KEYSTONE: build a competent/balanced opponent (do this FIRST)

Everything this session "looked invincible" because the eval only contains fish.
You cannot build or even *measure* a better/robust bot without an opponent that
**punishes "play 95% and call down"**: one that 3-bets a loose limper, folds
correctly to overbets (denies thin value), value-bets thin, and doesn't pay off.

Options, cheapest first:
1. **A rule-based "Reg+" / TAG-pro**: tight raise-or-fold preflop (~22%),
   3-bet/iso a loose opener, c-bet + barrel as the aggressor, value-bet thin, FOLD
   to overbets without a hand (don't pay off), bluff-catch only vs detected
   aggression. (The `Reg` base from this session is a start — but it was passive
   postflop; it needs initiative + correct folding.) **Validate it actually
   punishes CaseBot**: a competent reg should make CaseBotV2's 95% VPIP *lose* (or
   at least stop printing). If `Reg+` beats CaseBotV2 head-to-head, you finally
   have a real yardstick.
2. **Lean on the tiered solver bot as the balanced ref** — it already plays
   chart-based (not 95% VPIP). Measure CaseBotV2 vs `Baseline`/`TAG` *with proper
   defense*; the issue is the tiered bots over-fold (the `fold_to_open` gap). If
   you fix blind-defense in the tiered bot (the unimplemented PHASE_8_1), it
   becomes the competent ref.
3. **A GTO-ish HU bot** (longer): a small solver/approx for a balanced baseline.

**Success test for the keystone:** a field/opponent that beats CaseBotV2 (or holds
it to ~0). Until something does, every "better bot" claim is measuring fish-hunting.

## §3 — THEN build the better bot (gated on §1 + §2)

If (C) adaptive: keep CaseBotV2's naive value/call-down as the *default* (it wins
vs fish) and add **type-aware switches that are INVERTED from the range model**:
- detect **bluffer/maniac** (high AF + high VPIP, ~8-hand read) → call down EVEN
  wider, never fold to barrels, don't bluff. (NOT "respect the aggression" — the
  range model's fatal error.)
- detect **value-bettor/reg** (low VPIP, folds to aggression, doesn't over-bluff)
  → tighten preflop, stop the face-up overbets, fold to their bets, balance more.
  This is the (B) profile, only switched on vs a competent read.
- The classifier exists in spirit (`_is_maniac_read`); the missing piece is real
  opponent stats in the harness (the sim disables them — see §4). The profiles are
  the unit; the read just selects one. This is Jeff's original "hybrid playstyles /
  detection invokes a different archetype" idea — it's correct, the prototypes just
  switched to the *wrong* profiles (tight-defense vs maniac instead of call-down).

If (B) robust: a competent TAG-pro that you accept extracts LESS from fish but
can't be run over by a human. (This is just §2's `Reg+` deployed.)

## §4 — Measurement methodology (don't relearn these the hard way)

- **Measure candidate-vs-incumbent at a full table** + vs the **clones**. NOT
  absolute bb/100 vs caricatures (CaseBot near-maxes those; a sounder bot extracts
  LESS and looks worse).
- **6-max only for stable signal.** HU is ±60 noise. Maniac cells are noisy
  (sign-disagreement); use ≥6 seeds and read the trend, not one cell.
- **Opponent modeling is OFF in `measure_passivity`** (the hero gets `{}` stats) —
  so adaptive bots can't classify there. To test adaptation you must either feed
  perfect-read stats (`ARCHETYPE_STATS`, the concept ceiling) or use a harness that
  populates opponent models (`exploit_bb100` attaches+feeds them; `full_sim` wires
  `AIMemoryManager`). **Pick the harness before building the adaptive bot.**
- **Hetzner** (`poker-bot-optimization` only, tear down): provision ccx63, rsync,
  `docker compose build backend`, run the parallel AB battery, fetch, delete. The
  clone/maniac cells are the slow ones; fan them across cores.
- 64-sim MC is sufficient for rule-bot bucketing (validated). Don't go to
  `made_tier` (dead end #2).

## §5 — The honest strategic note for Jeff

You keep hitting the same wall ("I want adaptation/exploitation, can't build
GTO") because **off-the-shelf sophistication (GTO ranges, solver tables, accurate
equity) all pull toward *balance*, and balance under-exploits a leaky pool.** The
thing that exploits is a *deliberate, type-aware deviation* — which is exactly
what CaseBot's naive aggression accidentally is. The route to a genuinely better
*and* robust bot is: (1) get a competent opponent so the eval stops rewarding pure
fish-hunting, then (2) make the bot *switch* between the fish-hunter profile and a
disciplined profile on a read — keeping the naive call-down for the fish and
adding real folding only when a competent opponent is detected. Build the
yardstick before the bot.

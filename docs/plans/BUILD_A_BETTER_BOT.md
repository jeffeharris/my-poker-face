---
purpose: Executable plan + hard-won findings for building a genuinely better poker bot — start here next context
type: guide
created: 2026-05-30
last_updated: 2026-05-31
---

# Build a better bot — NEW-CONTEXT START HERE

> **Read this first.** A long session built `CaseBotV2` (a strong fish-hunter,
> now the promoted `casebot` bot type) and then tried five ways to make it
> "smarter." **Every one made it worse.** The reason that keeps recurring is the
> single most important thing to understand before writing any more bot code, and
> it points at the real next step. Full numbers: `docs/eval_results/VARIETY_VALIDATION_RESULTS.md`.
> Narrative: `docs/captains-log/lookup-tables/variety-validation-and-deploy.md`.

## ✅ 2026-05-31 — KEYSTONE BUILT: `RegPlus` beats CaseBotV2 (and everything else)

The keystone (§2) is **done**. `RegPlus` (`_strategy_reg_plus` in
`poker/rule_strategies.py`, archetype `RegPlus`) is the competent opponent that
punishes "play 95% and call down." It **beats CaseBotV2** and is positive vs the
entire eval field:

| Cell (RegPlus as hero, 1000h × 3 seeds) | bb/100 | plain `Reg` was |
|---|---|---|
| HU vs CaseBotV2 | **+102** | −88 |
| 6max vs 5×CaseBotV2 | **+38** | −126 |
| HU vs jeff_clone (calls-down human) | **+115** | — |
| 6max vs 5×jeff_clone | **+192** | — |
| HU vs punisher_clone (competent reg) | **+60** | — |
| 6max vs 5×punisher_clone (the target-B cell) | **+120** | — |
| Gauntlet worst cell (6max vs 5×TAG) | **+0.0** | — |
| Gauntlet mean (all 11 cells) | **+67** | — |

Inverse confirms it: **CaseBotV2 vs a table of 5×RegPlus = −199 bb/100** (was
+378 vs plain Reg). A RegPlus table turns the fish-hunter into a big loser.

**Why it works (and how it refutes part of this doc's premise).** The recipe was
NOT to balance CaseBotV2 (every dead-end below did that and lost). It was:
- **keep** CaseBotV2's value-extraction — overbet premium/strong when checked to
  (the calling pool pays → Station +180, jeff +192); AND
- **add** folding discipline — *fold to a polarized big bet* (`bet_over_pot ≥ 0.8`)
  instead of paying it off, and **never bluff-barrel a caller** (give up air).

The asymmetry is the whole game: when *RegPlus* overbets, the station pays; when
the station overbets, RegPlus folds. CaseBotV2 calls down → it pays off RegPlus's
value but RegPlus doesn't return the favor. **So "discipline ≠ balance":** a static
bot CAN be both robust (worst cell +0.0 vs 5 TAGs, beats the competent clone +120)
AND fish-extracting (+180 vs stations). The §5 thesis ("balance under-exploits a
leaky pool") is right about *balance* — but RegPlus isn't balanced, it's a
value-extractor with a fold button.

**Caveat / the one residual leak → motivates §3 (C).** RegPlus folds medium to
overbets because in our eval *no opponent overbet-BLUFFS* (CaseBotV2 only overbets
value; the punisher barrels but RegPlus's call-down catches it). A thinking HUMAN
who notices RegPlus over-folds to big bets would overbet-bluff it. That residual
exploit is exactly what the adaptive call-down sub-case (§3, the maniac/aggression
read) is for — and it's unmeasurable in our current pool, so building it needs the
opponent-model harness (§4). **RegPlus is a strong static "better bot" today AND
the competent profile the adaptive bot switches to.**

## TL;DR — the one finding that governs everything

**Our entire opponent pool is exploitable (fish, maniacs, stations, leaky
clones). Against an exploitable pool, a naive aggressive exploiter beats every
"accurate"/balanced approach — because the naivety happens to align with the
exploit.** Concretely, CaseBotV2's vs-random equity has two "errors": it
*overstates* hand strength (→ value-bets more → callers pay) and *ignores*
opponent aggression (→ calls down → catches bluffers). Both errors are +EV vs a
leaky field. Every "fix" that corrected them pulled the bot toward *balance*, and
balance under-exploits fish.

**TARGET (Jeff decided): (B) robust — a bot a competent human can't run over —
achieved via (C) adaptation** (fish-hunt the fish, switch to a disciplined profile
when a competent opponent is detected). A single static "robust" strategy would
under-extract from the fish that fill the game, and we can't build GTO — so
robustness has to come from the *switch*. The keystone (§2) is still the
gate: you can't prove robustness without a competent opponent to fail against.

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

## §1 — TARGET (DECIDED by Jeff): B, achieved via C

The goal is **(B) robust — not exploitable by a competent human** (CaseBotV2
fails this: its 95% VPIP gets run over by anyone who 3-bets it and stops paying
its overbets). **And (C) adaptive is REQUIRED to get there.** The logic:
- A *single static* "robust" strategy (a balanced TAG-pro) is robust but extracts
  far less from the fish that make up the actual game — it leaves money on the
  table, and we can't build true GTO anyway.
- So the practical route to "can't be run over by a human" is **detect the
  competent opponent and switch to a disciplined profile**, while keeping the
  fish-hunter profile for the fish. Robustness comes from the *switch*, not from
  playing one balanced strategy everywhere.

Reference targets for the build:
- **(A) Harder fish-extraction** — NOT the goal (CaseBotV2 near-maxes it; marginal).
- **(B) Robust vs a competent human** — THE GOAL.
- **(C) Adaptive (fish-hunt fish, discipline vs competent)** — THE MECHANISM for B.

This is still blocked on the keystone (§2): you cannot *prove* robustness (B)
without a competent opponent to fail against, and (C)'s classifier must detect
exactly that opponent to switch. So §2 produces both the yardstick AND the
profile the adaptive bot switches to.

## §2 — KEYSTONE: build a competent/balanced opponent ✅ DONE (RegPlus, 2026-05-31)

> **Resolved.** Option 1 below (a rule-based Reg+) was built as `RegPlus` and
> passes the success test decisively — see the top-of-doc result table. The plain
> `Reg` lost to CaseBotV2 because it (1) under-extracted (0.66 value bets), (2)
> paid off the overbets, and (3) nitted itself out of the fish table preflop.
> `RegPlus` fixes all three. The rest of this section is the original reasoning,
> kept for the record.



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

## §3 — THEN build the B-via-C bot (gated on §1 + §2)

The bot is a **profile-switcher with two profiles + a classifier:**

1. **Fish-hunter profile = CaseBotV2** (exists). Default. Naive value/call-down
   that crushes fish.
2. **Competent profile = §2's `Reg+`** (the keystone). This is the SAME bot you
   build as the yardstick — it does double duty: it's both the opponent that
   *proves* robustness AND the profile the adaptive bot *becomes* vs a competent
   read. Disciplined: tight raise-or-fold, fold to overbets (no pay-off), value-bet
   thin, don't spew. This profile is what makes the bot un-runnable-over.
3. **Classifier — and use OUTCOME-based detection, not the range model** (which
   reads aggression as strength and fails, dead end #3). The cleanest, most direct
   signal of "am I facing a competent player" is **how my own bets resolve**:
   - my overbets keep getting **called** + opponent pays off river value → FISH →
     stay in fish-hunter mode (overbet, value-town, call down).
   - my overbets get **folded to** / opponent 3-bets me / opponent doesn't pay off
     → COMPETENT → switch to `Reg+` mode (tighten, stop the face-up overbets, fold
     correctly). This needs ~10-20 hands of outcome history per opponent, not a
     range model.
   - Also keep the **maniac sub-case** (high AF + high VPIP read) → call down EVEN
     wider, never fold to barrels — INVERTED from the range model.

This is Jeff's original "hybrid playstyles / detection invokes a different
archetype" instinct — correct; the prototypes just (a) switched to the *wrong*
profile vs maniacs (tight-defense, dead end #4 — should be call-down), and (b)
tried a *range model* for the read (dead end #3 — should be outcome-based). The
profiles are the unit; the read selects one.

**Robustness acceptance test (the definition of done):** vs §2's `Reg+`, the
adaptive bot (in competent mode) is NOT exploited — `Reg+` can't beat it the way
it beats vanilla CaseBotV2. AND vs the fish/clones it still extracts (fish-hunter
mode). Both must hold. If `Reg+` still crushes the adaptive bot, the competent
profile isn't disciplined enough — iterate the profile, not the classifier.

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

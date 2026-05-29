---
purpose: Scoping the option of letting a spot-tendency leak bypass the math/defense floors so over-fold / over-aggression leaks can price genuinely -EV (and be exploitable/teachable)
type: design
created: 2026-05-29
last_updated: 2026-05-29
---

# Floor-Defeating Leaks

> Investigation-only scoping doc. No production code was changed. The POC used an
> in-process monkeypatch (throwaway `/tmp/floor_poc.py`); the floor source files
> were never edited on disk.

## TL;DR

- The postflop floors (`defense_floor`, then `math_floor`) run **after** the
  `spot_tendencies` layer and re-add pot-odds-mandated calls. By code reading
  they are the cap that re-converts an over-fold's added fold mass back to call.
- **The POC supports the floor-is-cap hypothesis directionally, but is NOT
  CI-clear**, and surfaced a *second* reason the over-fold is free that's at
  least as important: **the leak almost never fires a divergent action.** Across
  rosters only 0.3–3.5% of hands diverge between leak-ON and leak-OFF. Neutering
  both floors moved the measured leak cost in the −EV direction in every roster
  (jeff +0.93, punisher +0.88, maniac +6.22 bb/100), with the swing largest where
  the leak fires most (maniac), exactly as the hypothesis predicts — but each
  individual number's 95% CI straddles 0 at 6k hands. So: mechanism confirmed by
  code + consistent direction in sim, magnitude not yet pinned.
- A genuinely −EV over-fold leak is *plausibly* achievable by letting the tendency
  opt out of the floor, **but** the bigger lever is fire-rate: the leak needs to
  reach a divergent action far more often before floor-exemption matters. The
  floor is necessary-but-not-sufficient to explain the free pricing.
- **Recommendation: conditional GO, but reorder the work.** First raise the leak's
  fire-rate / widen its gate so it actually changes the action; only then add the
  per-decision `floor_exempt` flag (option A) and re-price. Build is small (~½ day
  for the flag) but should not ship before a CI-clear −EV number exists. **Do not**
  weaken the floor globally or reorder it for everyone.

## 1. Pipeline map (postflop path, `tiered_bot_controller._get_postflop_decision`)

Layer order is defined in `poker/strategy/intervention_trace.py:119` (`_LAYER_ORDER`).
The postflop pipeline runs these steps in order (line refs in
`poker/tiered_bot_controller.py`):

| # | Step | Layer (`layer_order`) | Line | Notes |
|---|------|----|------|-------|
| 4 | multiway adjustment | (base) | 893–913 | pre-personality |
| 5 | personality distortion (`modify_strategy`) | personality (0) | 922–939 | global-scalar, spot-blind |
| 6 | river bluff guardrail (`apply_river_bluff_guardrail`) | (inline) | 947–959 | river only; caps air bet freq |
| **6.b** | **spot tendencies (`apply_spot_tendencies`)** | **spot_tendencies (0)** | **1005–1031** | the leak layer; gated on `profile.spot_tendencies` |
| 6a | exploitation (`_apply_exploitation`) | exploitation (1) | 1034–1043 | |
| 6a.45 | induce override | (2) | 1052–1063 | |
| 6a.5 | value override | (2) | 1069–1079 | |
| 6a.5b | bluff-catch override | (3) | 1093–1110 | |
| 6a.5b.2 | multistreet context | multistreet_context | 1121–1155 | |
| 6a.5b.3 | overbet context | overbet_context | 1165–1198 | |
| **6a.5c** | **defense floor (`apply_defense_floor`)** | **defense_floor (4)** | **1214–1238** | pumps `call` for made hands at favorable price |
| 6a.6 | short-stack heuristic | short_stack | 1243–1251 | |
| 6a.7 | postflop commit | postflop_commit | 1258–1266 | |
| **6b** | **math floor (`apply_pot_odds_floor`)** | **math_floor (6)** | **1271–1278** | VETO → 100% call/jam on pot-odds mandate |
| 7 | sample action | — | 1281 | |

Key ordering facts:

- **`over_bluff` manifests** because the river bluff guardrail (step 6) runs
  *before* the spot layer (step 6.b). A pumped river bluff is never re-capped →
  the leak survives to the sample. (`spot_tendencies.py:413–454` documents this
  asymmetry.)
- **`fit_or_fold` / over-folds are capped** because the spot layer (6.b) runs
  *before* both floors (6a.5c, 6b). Any fold mass the leak adds to a hand the
  floor protects is re-converted to `call` downstream → the leak only actually
  folds the near-neutral hands the floor leaves alone → ~free. This is exactly
  the symmetric inverse of the over_bluff case.

### What the floors guarantee (the re-adders)

**`defense_floor`** (`poker/strategy/defense_floor.py`) — the dominant cap for a
flop over-fold. `apply_defense_floor` (line 286) pumps `call` up to a target
(`_floor_target_call_prob`, the §2 matrix, line 113) when hero **faces a bet**,
no upstream override fired, and the made-hand class is good enough at the price:

- near/actual nuts at required_equity ≤ 45% → call ≥ 0.95
- strong+ / non_nut_strong at ≤ 35% → call ≥ 0.80
- medium+ at ≤ 20% → call ≥ 0.80

It only ever *raises* `call` (`_redistribute_to_call_target`, line 175, pulls the
delta from non-call mass — i.e. from the fold the leak just pumped). So the
defense floor structurally undoes an over-fold of any made hand that's a
priced-in continue. `air` and `bluff_catcher` rows are explicit no-floor — which
is why the *narrow* air-only over-fold priced free even without a floor (folding
air is correct), and the *wide* (equity-hand) over-fold is what the floor catches.

**`math_floor`** (`poker/strategy/math_floor.py`) — `apply_pot_odds_floor`
(line 48) is a VETO: when one of three arithmetic conditions holds it replaces
the whole distribution with 100% `call` (or `jam`):

- short stack (`stack_bb < 3`)
- pot committed (`player_bet > player_stack`)
- tiny pot odds (`cost/(cost+pot) ≤ 5%` AND `cost < 5 BB`)

On a 100bb flop facing a normal c-bet these rarely fire, so for `fit_or_fold` the
**defense floor is the binding cap**, not the math floor. The math floor matters
for short/committed regimes and for the `sticky`/`over_fold_2nd_barrel` turn-commit
spots.

## 2. The floor-is-the-cap POC

**Hypothesis:** neutering both floors lets `fit_or_fold` finally price −EV.

**Method (single-process paired CRN).** `experiments/ab_node_attribution._run_seed`
runs the per-hand paired-CRN loop (`_run_one_hand`) in-process. Run it directly
(not via the module's `ProcessPoolExecutor`, which a parent monkeypatch can't
reach) with:

- Arm A = hero `fit_or_fold:0.8` ENABLED (`disable=∅`)
- Arm B = hero `fit_or_fold` DISABLED (`disable={('spot_tendencies','fit_or_fold')}`)
- `hero_spot=(('fit_or_fold',0.8),)`, `--hero TAG`, roster `jeff` (station), HU, 100bb.

`_run_seed` returns paired delta `(B − A)` = (no-leak) − (leak) = **cost of the
leak** in bb/100 (positive ⇒ leak is −EV). Then re-run with both floors
monkeypatched to no-ops:

- `poker.tiered_bot_controller.apply_pot_odds_floor` (module-level import) → no-op
- `poker.strategy.math_floor.apply_pot_odds_floor` (source) → no-op
- `poker.strategy.defense_floor.apply_defense_floor` (source; the controller does a
  **local** `from .strategy.defense_floor import apply_defense_floor` at line 1214,
  so patching the source module is required — a controller-namespace patch misses it)

2 seeds × 3000 hands = 6,000 paired hands per (roster × floor-state).

**Result** (leak_cost = bb/100 the leak loses; +ve ⇒ leak is −EV):

```
roster      floor-state   diverged    leak_cost      95% CI
jeff        LIVE          19  (0.3%)   -0.73          [-1.77, +0.30]
jeff        NEUTERED      26  (0.4%)   +0.20          [-1.59, +1.99]   delta +0.93
punisher    LIVE          33  (0.6%)   -3.19          [-7.34, +0.95]
punisher    NEUTERED      52  (0.9%)   -2.31          [-7.16, +2.53]   delta +0.88
maniac      LIVE         117  (1.9%)   -1.49          [-8.57, +5.58]
maniac      NEUTERED     210  (3.5%)   +4.73          [-5.70, +15.17]  delta +6.22
```

**Interpretation (honest):**

1. **Direction confirms the hypothesis, magnitude does not (yet).** In all three
   rosters, removing the floors shifts the leak's cost in the −EV direction
   (positive delta), and the shift is largest for the maniac — the opponent that
   c-bets most, so the leak's facing-flop-c-bet gate fires most. That's exactly
   the signature the floor-is-cap hypothesis predicts. But every individual CI
   straddles 0 at 6k hands, so none of the six numbers is on its own significant.
2. **The dominant reason the over-fold prices free is fire-rate, not just the
   floor.** Divergence is 0.3–3.5% — the leak almost never changes the bot's
   action. Two compounding causes: (a) hero must *face a flop c-bet* for the gate
   to fire, which is rare vs a passive station (jeff) and only modest vs a reg;
   (b) even when it fires, the floor re-adds the priced-in calls so the *resulting
   action* often matches leak-OFF. Removing the floor raises divergence (jeff
   0.3→0.4, punisher 0.6→0.9, maniac 1.9→3.5%) — direct evidence the floor was
   silently undoing fold mass — but the absolute fire-rate stays low.
3. **Conclusion:** the floor IS a cap (code + the consistent neutered-vs-live
   divergence increase prove the re-add is real), but it is *not the whole story*.
   A teachable −EV over-fold needs both (a) a wider/more-frequent gate and (b)
   floor exemption. Floor exemption alone, at the current fire-rate, won't move
   the bot's bottom line enough to matter — consistent with why `fit_or_fold`
   priced free in the original program measurements.

The procedure (for re-running): the throwaway lives at `/tmp/floor_poc.py`
(single-process, single-roster) or run the multi-roster two-pass inline; copy
into the backend with `docker compose cp` then
`docker compose exec -T backend python -u ...`. Use `python -u` — buffered stdout
from a *backgrounded* `docker compose exec` was observed to silently drop print
output (foreground or `-u` is reliable). The monkeypatch targets:
`poker.tiered_bot_controller.apply_pot_odds_floor` (module-level),
`poker.strategy.math_floor.apply_pot_odds_floor` (source), and
`poker.strategy.defense_floor.apply_defense_floor` (source — the controller's
local import means the source module is the only effective patch point).

## 3. Minimal safe design

Three options were considered:

| Option | Mechanism | Verdict |
|--------|-----------|---------|
| **A. Per-decision `floor_exempt` flag** | The spot tendency, when it fires an over-fold/over-aggression leak, marks the decision (e.g. returns/sets a flag on the controller's per-decision snapshot). `apply_defense_floor` (and optionally `apply_pot_odds_floor`) reads it and no-ops for that decision only. | **PICK THIS** |
| B. Run select leaks AFTER the floor | Move a second `apply_spot_tendencies` pass (leak-only) to after step 6b. | Rejected — duplicates the layer, breaks the clean single-pass trace ordering, and re-introduces the "floor can't see the leak" problem for the math VETO (which would then run *before* the leak and the leak would re-fold a committed hand → can resolve to an illegal/incoherent action). |
| C. Floor-level allowlist keyed on active tendency | The floor inspects `profile.spot_tendencies` and skips itself when a fold-type tendency is configured. | Rejected — too coarse (disables the floor for the whole hand/all spots, not the leak's specific spot) and couples the floor to the tendency catalog. |

**Why A is least invasive:**

- The floors already accept a `disable_rules` ablation set
  (`apply_defense_floor(..., disable_rules=...)`, `apply_pot_odds_floor(..., disable_rules=...)`).
  The exemption is the **same shape** as the existing ablation hook, just sourced
  per-decision from the leak instead of from an experiment config. Minimal new
  surface.
- It's **decision-scoped**: the flag is set only when the leak handler actually
  fires (returns a non-identity strategy on its target spot), so the floor stays
  fully active on every other decision in the same hand.
- It threads through the existing trace plumbing — the floor's no-op trace can
  carry a `reason_code='leak_exempt'`, keeping the intervention trace honest
  about *why* the floor stood down (important for the attribution gate and for the
  captain's-log audit trail).

**Concrete plumbing (smallest version):**

1. Add an opt-in per-tendency property (e.g. a `_FLOOR_DEFEATING` set in
   `spot_tendencies.py` listing `{fit_or_fold, over_fold_2nd_barrel}` — the
   over-fold leaks whose −EV the floor currently erases). `apply_spot_tendencies`
   returns an extra `floor_exempt: bool` (True iff a floor-defeating tendency
   actually fired this decision).
2. Controller stashes that on the per-decision snapshot (it already builds
   `_last_pipeline_snapshot`).
3. Pass it into `apply_defense_floor` as a new `leak_exempt: bool = False` kwarg
   (and into `apply_pot_odds_floor` only if turn-commit leaks like
   `over_fold_2nd_barrel`/`sticky` are in scope). When True, emit a no-op
   `leak_exempt` trace and pass the strategy through.
4. Default everywhere is `False` → byte-identical for every non-leak controller
   and the strong personalities.

This is ~30–50 lines across `spot_tendencies.py`, `defense_floor.py`,
`math_floor.py`, and the controller call sites, plus tests. Owned-file caveat:
all four are in the parallel session's edit-lock set, so this build must be
sequenced with that session — it cannot land from here.

## 4. Correctness risk / blast radius

The floors exist to stop the bot making −EV folds/calls (architectural invariant
#3, `math_floor.py:15`). Deliberately defeating them is intended for a *leak
personality* and dangerous if it bleeds elsewhere. Containment:

| Risk | Contained by |
|------|--------------|
| Floor weakens for strong personalities / default bots | Flag defaults `False`; only set when a **floor-defeating tendency fires**. Strong personas carry no such tendency → flag never True. |
| Floor weakens for the SAME personality on non-leak spots | Flag is **per-decision**, set only on the tendency's exact gated spot (e.g. `fit_or_fold` = flop, facing_bet, weak/medium/air). Every other decision keeps the floor. |
| A floor-exempt over-fold becomes an *illegal/incoherent* action | Over-fold only ever moves mass onto `fold` (a legal action whenever facing a bet). Unlike option B, the math VETO still runs *after* the leak in option A, so a genuinely pot-committed spot is still rescued — the exemption should be scoped to the **defense** floor first; only extend to the math VETO for turn-commit leaks after measuring, because defeating the math floor can fold a pot-committed hand (a −EV that's *too* large / incoherent, not a teachable leak). |
| Exemption silently spreads via config | The floor-defeating set is an explicit allowlist in code (`_FLOOR_DEFEATING`), not a free-form config field. Adding a tendency to it is a reviewed code change, priced through the attribution gate first. |
| Loss of observability | The floor emits a `leak_exempt` no-op trace, so the attribution gate and post-hand analysis still see *that the floor stood down and why*. |
| Over-strong leak (un-teachable) | Cap the leak's realized −EV by keeping the existing `max_per_action_shift` bound on the tendency itself — the exemption removes the floor's re-add, but the leak still can't move more than the profile's per-action budget, so the bleed is bounded and tunable via `strength`. |

The single most important guardrail: **scope the exemption to the defense floor
first.** The defense floor is a *soft* pump (it nudges call probability); defeating
it produces a bounded, realistic over-fold. The math floor is a *hard VETO* on
arithmetic certainties; defeating it can produce grossly −EV / borderline-illegal
folds that are a bug, not a personality. Treat math-floor exemption as a separate,
later, separately-priced decision.

## 5. Go / No-Go

**Conditional GO, sequenced** — the floor exemption is the right *mechanism* but
the wrong *first step*. Do this order:

1. **First, raise the leak's fire-rate / widen the gate** (cheap, no owned-file
   risk to the floor): the POC shows the over-fold diverges on only 0.3–3.5% of
   hands, so floor exemption can't matter much until the leak actually reaches a
   divergent action more often. Re-price `fit_or_fold` at a wider gate (e.g. higher
   strength, or include facing-bet spots beyond the flop) through the attribution
   gate. If it can't be made to fire materially more often without becoming a
   different leak, **No-Go on this leak** and pick a higher-fire-rate one (`sticky`
   / `over_fold_2nd_barrel`, which fire on the turn-commit the math VETO guards).
2. **Then build the defense-floor `floor_exempt` path** (option A) and re-price.
   - **Size:** ~½ day. ~30–50 LOC across `spot_tendencies.py` (return + allowlist),
     `defense_floor.py` (kwarg + no-op branch), `tiered_bot_controller.py` (thread
     the flag), plus unit tests in `tests/test_strategy/test_spot_tendencies.py`
     and a defense-floor exemption test. (All five files are parallel-session-owned
     — sequence with that session.)
   - **Gate:** the same paired-CRN attribution this POC used, at a sample large
     enough to clear CI (the maniac arm needs ~3–5× more hands given its variance;
     budget ≥ 30k diverging hands, i.e. tens of seeds, before trusting a number).
     Ship only if the exempted leak prices a **stable, CI-clear, bounded** −EV vs
     the realistic field (station + reg), and the OFF arm stays byte-identical.
   - **Defer:** math-floor exemption (turn-commit leaks) to a separate, later,
     separately-priced pass — it carries the incoherent-fold risk (§4).

**Why not full GO / why not No-Go:** the program's value is variety + *teachable*
exploits, and a free leak isn't teachable — so defeating the cap is a legitimate
goal (argues GO). But the POC shows the floor is necessary-not-sufficient: at the
current fire-rate, exemption alone won't produce a teachable edge, and the floors
are a load-bearing correctness guarantee whose defeat must be minimal,
decision-scoped, and gated (argues *conditional* + *sequenced*, not a blank GO).

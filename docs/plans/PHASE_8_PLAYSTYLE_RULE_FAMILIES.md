---
purpose: Plan to define and gate new exploitation rule families (value-vs-station, steal/pressure) by archetype playstyle
type: design
created: 2026-05-13
last_updated: 2026-05-13T22:00:00
---

# Phase 8: Playstyle-gated rule families

## Sequencing & cross-plan dependencies

This plan ships **after** the spot-aware exploitation migration:

1. [Phase 6.6](PHASE_6_6_CBET_PLUS_CONFIDENCE.md) — HU c-bet +
   confidence-weighted offsets (shipped).
2. [Phase 6.7a](PHASE_6_7_OPPONENT_SPOTS.md) — `OpponentSpot`,
   `select_primary_aggressor`, `aggregate_from_spots`, postflop
   aggressor tracking (shipped).
3. [Phase 6.7b Part A](PHASE_6_7_OPPONENT_SPOTS.md#multiway-c-bet) —
   conservative multiway c-bet (shipped).
4. **Phase 8 (this plan)** — value-vs-station + steal/pressure rule
   families, playstyle-gated activation.

Phase 8 is independent of [Phase 7.5](PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md)
(adjustment-layer widening for HU vs maniac regression). 7.5 addresses
the documented `-130 to -200 bb/100` HU leak; Phase 8 adds new rules
on top of the existing spot infrastructure. Either can ship first;
7.5 has higher expected bb/100 impact and is recommended first.

### Why this was carved out of 6.7b

Phase 6.7b originally bundled two items:

- **Part A**: conservative multiway c-bet using the existing
  `high_fold_to_cbet` intensity. Small, low-risk extension of 6.6
  using 6.7a spot infrastructure. **Shipped as part of 6.7b.**
- **Part B (this plan)**: playstyle-gated rule families. Requires
  defining new rule semantics (`value_vs_station`, `steal_pressure`)
  that don't exist anywhere in the codebase. This is new-rule-
  development, not selection-correctness migration — distinct scope.

Mixing them in one plan obscured both. This plan owns Part B.

## Context

After 6.7a + 6.7b Part A, the exploitation pipeline knows:

- Which opponent drives a facing-aggression decision
  (`select_primary_aggressor`).
- Which opponents are continuing in a multiway flop (`aggregate_from_spots`
  with the 60% rule).
- Which opponents are all-in, foldy, or unknown.

What it does NOT yet do:

- Exploit calling stations on value bets. The hyper_passive rule today
  raises bet/raise probability uniformly, but doesn't size up against
  the right opponent or dampen the bet when other opponents have
  stronger ranges.
- Steal from blinds left to act behind hero with their playstyle in
  mind. Preflop opens today are driven entirely by the chart +
  personality + position; no rule looks at "the player in the BB right
  now folds 75% of the time to opens."

Phase 8 adds two new rule families that consume the spot model and
gates them by hero's playstyle so the bot doesn't try to be all
exploitation patterns at once.

## Empirical baselines (2026-05-13 sweep)

Concrete numbers Phase 8 needs to move:

### HU vs CaseBot — every architecture loses

Full HU sweep, 1000 hands per matchup, seed=42, `adaptation_bias=0.85`:

| Hero | bb/100 vs CaseBot | 95% CI | Note |
|---|---:|---|---|
| **FoldyBot** | **−35.6** | [−44, −27] | Best non-mirror result — by NOT spewing |
| CallStation | −45.1 | [−64, −26] | |
| Rock | −54.2 | [−116, +8] | |
| Nit | −64.8 | [−131, +2] | |
| CaseBot (mirror) | −70.4 | [−244, +103] | |
| **TAG** | **−77.8** | [−152, −4] | |
| LAG | −112.1 | [−207, −17] | |
| Baseline | −119.7 | [−192, −47] | Pure chart, no exploitation |
| GTO-Lite | −197.6 | [−241, −154] | |
| Maniac | −265.2 | [−379, −151] | |
| **ManiacBot** | **−701.1** | [−783, −619] | Bluffing a station = donation |

**Key insight: FoldyBot beating TAG by ~42 bb/100 vs CaseBot is a
damning signal.** A bot built specifically to BE exploited
outperforms the architecturally sophisticated TAG against a station,
because it doesn't try to bluff or extract — it just calls cheap
preflop and folds tight postflop. The exploitation pipeline as it
stands today (hyper_passive rule firing on every CaseBot decision)
adds spew without compensating value extraction. **This is the core
gap Phase 8 must close.**

### 6-max TAG vs CaseBot-heavy mix — per-opponent decomposition

`TAG + 2×CaseBot + 2×ABCBot + GTO-Lite`, 500 hands,
`adaptation_bias=0.85`:

| Opponent | seed=42 (BB) | seed=142 (BB) | seed=242 (BB) | Pattern |
|---|---:|---:|---:|---|
| ABCBot01 | +709 | +1248 | +1807 | TAG wins |
| ABCBot02 | +1636 | +1438 | +1258 | TAG wins big |
| GTO-Lite | +34 | +67 | +228 | Flat (balanced) |
| CaseBot01 | −238 | −415 | −1335 | TAG loses |
| CaseBot02 | −2103 | −1714 | −1387 | TAG loses heavily |
| **Headline** | **−60.3** | **−16.0** | **−96.8** | Wide seed variance |

**3-seed baseline: mean −57.7 bb/100 (range −16 to −97).** The
headline varies because CaseBot drain (~−2400 BB mean) and
ABCBot+GTO gain (~+2800 BB mean) are similar in magnitude with
opposite signs, so net flips around. **Phase 8 target: move TAG's
mean toward 0 by reducing the CaseBot drain while preserving the
ABCBot edge.** This is the headline regression number to beat.

The headline bb/100 varies widely with seed but the **per-opponent
pattern is rock-solid**: TAG wins from every non-CaseBot opponent
and bleeds to CaseBots. Net loss size depends almost entirely on
how much chip volume runs through the CaseBot seats.

### Adding ManiacBot to the table doesn't fix it

`TAG + 2×CaseBot + LAG + Nit + ManiacBot`, seed=42: TAG +2335 BB
from ManiacBot (Phase 6.5 value override fires 110 times),
+481 BB from LAG, +406 BB from Nit, but **−3550 BB combined from
the two CaseBots** → headline −65.6 bb/100. **The CaseBot deficit
is the dominant loss term across every mix we've measured.**

### The hyper_passive ↔ CaseBot interaction (most important finding)

CaseBot's profile vs TAG in 6-max:
`VPIP=0.89, PFR=0.03, AF=0.40-0.48, all_in%=0.09-0.14, f2cbet=0.31-0.93`

The `hyper_passive` rule today fires because CaseBot has
`VPIP > 0.60 AND AF < 0.80`. It applies offsets:

- `+0.3 × multiplier` to all raise-like actions
- `−0.2 × multiplier` to fold

Against a pure calling station with `all_in% = 0`, reducing fold
mass is harmless — the station never punishes hero's marginal calls.
But CaseBot **jams 9-14% of hands**. Those jams are real hands
(AF=0.5 means CaseBot raises slightly more than it calls postflop;
its jams aren't bluffs). When TAG's fold mass is reduced and CaseBot
jams, TAG calls more often than the chart prescribes. TAG loses the
big pots.

Phase 8's `value_vs_station` extracts more **when TAG has a strong
hand** but does nothing about hyper_passive's spew **when TAG has
marginals against a station that occasionally jams**. The plan
needs to address both halves — see "Risks / known interactions"
below.

## Resolved Decisions

- New rule families are gated **deterministically** by playstyle. The
  action choice itself stays probabilistic through the existing
  strategy distributions and exploitation intensities.
- Inactive rule families stay diagnostic-only until the priority
  family's validation is stable. This keeps regression risk bounded
  per archetype.
- Existing `compute_pattern_intensity` patterns (hyper_aggressive,
  hyper_passive, tight_nit, high_fold_to_cbet) are not changed by
  this plan. Phase 8 introduces two NEW rule families with their own
  detection thresholds; they sit alongside the existing patterns.
- Phase 8 must not regress 6-max-vs-rules bb/100. The playstyle gate
  exists specifically to avoid spewy behavior on archetypes that
  shouldn't be running the rule.
- **`value_vs_station` is hand-strength gated** (STRONG_MADE / NUTS
  only). The empirical sweep showed the existing exploit machinery
  already over-bets weak hands vs stations; Phase 8 must not amplify
  that. Hand-strength gating makes Phase 8 symmetric with Phase 6.5
  (which is strong-vs-aggressive): together they form
  "strong-hand-vs-extreme-opponent" coverage.
- **Initial ship does NOT modify `hyper_passive`.** Risk #1 below
  documents the interaction problem but the recommended first ship
  is `value_vs_station` alone, instrumented to track co-fires with
  `hyper_passive`. Data-driven decision on whether to suppress
  `hyper_passive`'s fold-mass reduction in Phase 8.1.
- **Validate against per-opponent chip transfer, not just headline
  bb/100.** Three-seed mean baseline ranges −97 to −16 bb/100 (CI
  ±~47) but per-opponent contribution is much tighter (CaseBot drain
  ±~300 BB; ABCBot gain ±~450 BB).

## Goal — definition of done

1. Two new rule families implemented and tested in isolation:
   - `value_vs_station`: value-bet sizing/frequency adjustment using
     spots' max call-too-wide intensity, dampened by tightest
     remaining opponent.
   - `steal_pressure`: preflop steal sizing/frequency adjustment using
     players-left-to-act-behind spots, with extra weight on blinds.
2. Playstyle-gated activation:
   - `Nit`, `Rock`, `TAG`: `value_vs_station` enabled, `steal_pressure`
     diagnostic-only.
   - `LAG`, `Maniac`: `steal_pressure` enabled, `value_vs_station`
     diagnostic-only.
   - `Baseline`, `CaseBot`, unknown/custom: pick whichever rule family
     has the clearest diagnostic opportunity volume from sweep data.
3. Diagnostics per rule family + playstyle. Operator can see by
   archetype how often each family was eligible, fired, and what the
   driver opponent was.
4. No regression in:
   - Phase 6.5 value override behavior.
   - Phase 6.6 HU c-bet behavior.
   - Phase 6.7a spot construction and aggregator selection.
   - Phase 6.7b Part A multiway c-bet firing rate.
5. **Concrete bb/100 targets** (vs the empirical baselines documented
   below):
   - **TAG vs CaseBot HU (1000 hands, seed=42)**: move from
     `−77.8 bb/100 [CI −152, −4]` toward FoldyBot's `−35.6`. Stretch:
     statistically indistinguishable from FoldyBot.
   - **TAG vs CaseBot-heavy 6-max mix (3-seed mean of 500 hands each:
     42, 142, 242)**: move from baseline `−57.7 bb/100` (range
     [−97, −16]) toward 0. Stretch: net positive mean.
   - **Per-opponent decomposition** (more reliable than headline due
     to seed variance): CaseBot drain shrinks from `~−2400 BB` mean
     toward `~−1000 BB`; ABCBot gain stays at or above `~+2800 BB`
     mean.
   - No archetype's HU-vs-balanced (GTO-Lite) result moves by more
     than ±20 bb/100 from Phase 7 baseline.

## Rule semantics

### `value_vs_station`

**Hand-strength gated** — mirrors Phase 6.5's value-override
pattern (strong-vs-aggressive) but for the strong-vs-passive
spot. The empirical evidence (FoldyBot beating TAG by 42 bb/100
vs CaseBot) is unambiguous: **the existing exploit machinery
already over-bets weak hands against stations**. Phase 8 must
NOT add more frequency on marginals — it must add value
extraction specifically when hero is ahead.

Fires when ALL of:

- Hero's hand strength classifies as **STRONG_MADE or NUTS**
  (reuse the existing `HandStrengthClass` from `value_override.py`).
  Specifically NOT `medium_made` / `weak_made` / `air` — those go
  through the chart and stay there.
- Hero has a legal `bet_*` or `raise_*` action.
- At least one active continuing opponent has
  `vpip > VPIP_LOOSE` AND `aggression_factor < AF_PASSIVE` AND
  adequate sample. Reuse archetype thresholds.
- Hero is not facing a live bet (open-action spot).

Action:

- Compute "value upside" as the max call-too-wide intensity across
  active opponents.
- Compute "field safety factor" from the tightest or most aggressive
  remaining opponent. If the tightest opponent has `vpip < VPIP_TIGHT`
  with adequate sample, dampen the value bet by some factor (e.g.
  0.5–0.7 of the upside intensity).
- Net offset: increase bet_* by `0.3 × multiplier × upside × safety`,
  reduce check by half of that. Conservative until validation supports
  bigger.

Important constraints:

- **The hand-strength gate is non-negotiable.** Without it the rule
  amplifies the existing hyper_passive overspew on weak hands (see
  "Risks / known interactions" below). The gate makes Phase 8
  complementary to Phase 6.5: 6.5 is strong-vs-aggressive, 8 is
  strong-vs-passive — symmetric coverage.
- A station behind doesn't erase the risk of a tight caller's stronger
  range. The dampener is what prevents this rule from spewing into
  cooler spots even with strong hands.
- Sizing distinction is out of scope for v1 — keep the rule as
  bet/raise frequency only. Sizing tier selection (bet_33 vs bet_67
  vs bet_100) is a follow-up after firing rate validates.
- Multiway-aware: when multiple stations are continuing, the upside
  intensity is the **max** across them (stations love to call). The
  safety factor uses the **min** profile (tightest opponent in the
  field).

### `steal_pressure`

Fires when:

- Hero is preflop, open spot (no live raise, position warrants a
  steal — late position or blinds).
- At least one player left to act behind hero is the BB or SB.
- Players-behind have high fold-to-3-bet or generally tight VPIP.

Action:

- Compute "steal opportunity" intensity from players-left-to-act spots
  only. Folded players, players already all-in, and players who have
  already acted on this street are excluded from this rule's input.
- Net offset: increase raise_* probability proportional to intensity,
  weighted heavier when blinds are tight.

Important constraints:

- This rule must NOT fire when hero is already facing a bet/raise.
- The folded-player exclusion is critical — if `Player1` already
  folded preflop this hand, their tendency shouldn't contribute to
  hero's stealing decision against Players 2/3.

### `can_act_behind` and `has_position_on_hero` (6.7a deferred fields)

`OpponentSpot` already has `can_act_behind` and `has_position_on_hero`
fields, but 6.7a leaves them at `False` (the steal rule wasn't yet
defined). Phase 8 populates them in `_build_opponent_spots`:

- `can_act_behind`: True when the opponent has not yet acted on the
  current betting round AND has not folded. Requires the controller
  to know seat order and whose turn has come. Today this can be
  derived from `game_state.current_player_idx` and seat traversal.
- `has_position_on_hero`: already populated for postflop position
  signals; the steal rule uses the preflop variant (player_idx > hero
  on the action order).

## Playstyle classification

Reuse `TieredBotController.archetype_name` for the gate (already
returns one of `'nit'`, `'rock'`, `'tag'`, `'lag'`, `'maniac'`,
`'baseline'`, `'calling_station'`).

```python
VALUE_VS_STATION_PLAYSTYLES = frozenset({'nit', 'rock', 'tag', 'calling_station'})
STEAL_PRESSURE_PLAYSTYLES   = frozenset({'lag', 'maniac'})
# Baseline / unknown: configurable, default to value_vs_station.
```

Rule activation logic:

```python
def is_value_vs_station_enabled(archetype: str) -> bool:
    return archetype in VALUE_VS_STATION_PLAYSTYLES

def is_steal_pressure_enabled(archetype: str) -> bool:
    return archetype in STEAL_PRESSURE_PLAYSTYLES
```

Inactive rule families still run their detection logic and emit
diagnostics; they just don't contribute offsets.

## Diagnostics

Counters per archetype + rule family:

- `value_vs_station_eligible_<archetype>`
- `value_vs_station_fired_<archetype>` (zero unless playstyle gate
  passes)
- `value_vs_station_diagnostic_only_<archetype>` (eligible but
  gated off by playstyle)
- `steal_pressure_eligible_<archetype>`
- `steal_pressure_fired_<archetype>`
- `steal_pressure_diagnostic_only_<archetype>`
- `playstyle_gated_rule_family_<archetype>` (value_vs_station /
  steal_pressure / diagnostic_only)

This makes it easy to see "Nit had 200 steal opportunities but the
playstyle gate suppressed them" vs "TAG fired value_vs_station 30
times this run."

## Migration plan

1. Define detection helpers (`is_value_vs_station_spot`,
   `is_steal_pressure_spot`) as pure functions on
   `(spots, decision_context, hero_archetype)`. No behavior change.
2. Define offset functions for each family. Plug them into
   `compute_exploitation_offsets` as new branches, gated on the
   archetype-specific activation predicate.
3. Populate `OpponentSpot.can_act_behind` from the controller. Verify
   it agrees with the existing action-order logic.
4. Validation: run 6-max vs rule mix at multiple `adaptation_bias`
   values, confirm firing rates match expectation per archetype, no
   regression in spot-aware diagnostics.
5. After validation, decide whether to swap the active family for any
   archetype based on observed bb/100 impact. The plan starts with
   the conservative defaults above.

## Tests

- Detection: `value_vs_station` detection on (station active, foldy
  inactive, tight caller, mixed); `steal_pressure` detection on
  (blinds left to act behind, players already folded excluded,
  hero-facing-bet suppression).
- Offset shape: each family produces the right action direction
  (value-bet increases bet_* in value spots; steal increases raise_*
  in open spots).
- Gating: for each playstyle bucket, verify the right family fires
  and the other family stays diagnostic-only.
- `can_act_behind` derivation: action order respected, folded players
  excluded, players who have already acted in the current round
  excluded.

## Validation

Mechanism counters:

- All per-archetype `*_eligible` and `*_fired` counters non-zero in
  appropriate sims.
- `diagnostic_only` counter present and non-zero for the inactive
  family (proves the gate is doing its job).
- No double-firing: `value_vs_station_fired_lag` should always be 0
  by construction; same for `steal_pressure_fired_nit`.

Outcome gates:

- 6-max vs rule mix (TAG, Nit, LAG, Maniac, Baseline) at
  `adaptation_bias=0.85`: each archetype's bb/100 vs baseline is
  stable within ±20 bb/100 from Phase 6.7 baseline.
- HU vs ManiacBot, HU vs GTO-Lite: unchanged within noise (Phase 8
  rules are mostly multiway-relevant; HU should be unaffected).
- No regression on Phase 6.5 value-override counters or Phase 6.6
  c-bet counters.

## Risks / known interactions

### Risk #1: `hyper_passive` makes things worse vs stations that occasionally jam

The empirical sweep exposes a structural problem **independent of
this plan** that Phase 8 must coexist with cleanly:

The existing `hyper_passive` exploit fires on every CaseBot decision
(`VPIP=0.89, AF=0.4` → both gates trip). It applies:

- `+0.3 × multiplier` to all raise-like actions (including
  bluff-raise spots)
- `−0.2 × multiplier` to fold

Against a pure calling station with `all_in_frequency = 0`, reducing
fold mass is harmless. Against CaseBot, which jams 9-14% of hands
postflop with real holdings, **reducing fold mass loses big pots**.
This is most of TAG's −2400 BB mean drain vs CaseBot seats.

Phase 8's `value_vs_station` does NOT solve this. It adds frequency
on STRONG hands but doesn't undo `hyper_passive`'s spew on MARGINAL
hands. Three options for handling the interaction:

1. **Suppress `hyper_passive`'s fold-mass reduction** when
   `value_vs_station` is enabled AND the same opponent is the
   driver. Keeps `hyper_passive`'s raise-frequency push (which is
   complementary to `value_vs_station`) but removes the
   fold-frequency reduction (which is the part that punishes hero
   on marginal hands vs occasional-jammer stations). Add this as a
   modification inside `compute_exploitation_offsets` — when both
   rules would fire, blend the fold offset (e.g. min with
   `value_vs_station`'s `0` rather than `hyper_passive`'s `−0.2`).
2. **Add a "passive-with-jams" pattern** that detects
   `vpip > 0.60 AND all_in_freq > 0.05 AND AF < 0.80` and
   suppresses `hyper_passive` for those opponents. This is a Phase
   7.5 territory move (sizing/all-in-aware exploitation) rather
   than Phase 8, so flagging it as cross-cut work.
3. **Defer.** Ship `value_vs_station` as designed, accept that
   `hyper_passive` still spews on marginals, validate that
   `value_vs_station`'s extraction on strong hands compensates.
   If it doesn't (FoldyBot's −35.6 stays unattainable), add
   option 1 in Phase 8.1.

**Recommended: ship option 3 first, instrument the
`hyper_passive` vs `value_vs_station` co-fire pattern, then
decide on options 1 or 2 from the data.** This keeps the
behavior-change envelope tight for the first Phase 8 ship.

### Risk #2: per-opponent decomposition is much tighter than headline bb/100

The 3-seed baseline shows the headline ranging −16 to −97 bb/100,
but the per-opponent contribution is tight: CaseBot drain stays
in [−2722, −2129] BB and ABCBot+GTO gain stays in
[+2379, +3293] BB. **Validate Phase 8 against per-opponent chip
transfer, not just headline bb/100.** A run where CaseBot drain
shrinks from −2400 to −800 but ABCBot gain stays flat is the win,
even if headline noise hides it.

### Risk #3: 6.5 / 8 mutual exclusivity

Phase 6.5 fires for `STRONG hand vs hyper_aggressive opponent`.
Phase 8 fires for `STRONG hand vs passive station`. A hand class
can't be both "vs aggressive" and "vs passive" at the same
opponent, but multiway pots could have both. Add a controller-level
guard: if 6.5 already replaced the strategy this decision, skip
Phase 8 entirely. The override path should never run twice on the
same decision.

## Effort estimate

- Detection + offset functions for both rule families: 1.5 days.
- `can_act_behind` derivation + integration: 0.5 day.
- Playstyle gating + diagnostics: 0.5 day.
- Tests: 1 day.
- Validation across multiple archetypes: 1 day.

Expected total: 4–5 days.

## Open questions

- Should `Calling Station` archetype get `value_vs_station` or its
  own behavior? Listed with conservative styles for now because it
  shouldn't be pressured into steals, but its passive call tendency
  means it can't drive value bets without a sizing model.
- Should the playstyle gate be configurable per experiment (e.g.
  enable both rule families for `Baseline` to A/B test outcomes)?
  Probably yes for validation, default off in production.
- Sizing tier selection for value bets is deferred. When does it
  become important enough to add — after 6-max regression evidence,
  or only if Phase 7.5's sizing work doesn't already address it?

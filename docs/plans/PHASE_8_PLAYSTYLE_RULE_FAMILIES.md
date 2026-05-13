---
purpose: Plan to define and gate new exploitation rule families (value-vs-station, steal/pressure) by archetype playstyle
type: design
created: 2026-05-13
last_updated: 2026-05-13
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
5. 6-max bb/100 vs rule mix: no archetype regresses materially.
   Target: stable within ±20 bb/100 vs Phase 6.7 baseline per
   archetype.

## Rule semantics

### `value_vs_station`

Fires when:

- Hero is value-betting (postflop made hand, no live bet, has a legal
  bet/raise action).
- The most call-happy continuing opponent has `vpip > VPIP_LOOSE`
  AND `aggression_factor < AF_PASSIVE`. Reuse archetype thresholds.
- At least one opponent has adequate sample.

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

- A station behind doesn't erase the risk of a tight caller's stronger
  range. The dampener is what prevents this rule from spewing into
  cooler hands.
- Sizing distinction is out of scope for v1 — keep the rule as
  bet/raise frequency only. Sizing tier selection (bet_33 vs bet_67
  vs bet_100) is a follow-up after firing rate validates.

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

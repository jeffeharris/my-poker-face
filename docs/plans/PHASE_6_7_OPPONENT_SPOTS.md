---
purpose: Plan to replace aggregate multiway exploitation with independent opponent spot models
type: design
created: 2026-05-13
last_updated: 2026-05-13T20:00:00
---

# Phase 6.7: Independent opponent spots for multiway exploitation

## Sequencing & cross-plan dependencies

This plan ships **second** in the opponent-modeling sequence:

1. [Phase 6.6](PHASE_6_6_CBET_PLUS_CONFIDENCE.md) — HU c-bet +
   confidence-weighted offsets. Must land first; 6.7 extends the
   `DecisionContext` and stats it introduces.
2. **Phase 6.7 (this plan)** — independent multiway opponent spots.
3. [Phase 7.5](PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md) — adjustment-layer
   widening. Should consume this plan's `select_primary_aggressor()` helper
   and `OpponentSpot` model rather than re-introducing aggressor-awareness
   separately. See Phase 7.5 §Risks #3 (per-aggressor stats for bluff-catch).

Shared types this plan touches:

- `DecisionContext` in `poker/strategy/exploitation.py` —
  6.7 adds `facing_aggressor_name` on top of 6.6's
  `is_flop_as_preflop_aggressor` and `active_opponent_count`. 7.5 will then
  add `bet_size_pot_ratio`.
- `AggregatedOpponentStats` — 6.7 does NOT add fields; it consumes the
  6.6-extended struct via `aggregate_from_spots()` for compatibility.

Coordination notes:

- `aggregate_from_spots()` must preserve the existing
  `aggregate_active_opponents()` semantics (multiway 60% rule) so unmigrated
  rules — including 7.5's bluff-catch gate when it consumes aggregate stats —
  see identical behavior during migration.
- `select_primary_aggressor()` is the helper 7.5 needs for its
  per-aggressor bluff-catch gate. If 7.5 ships before 6.7, 7.5 will need a
  lightweight stand-in; better to land 6.7 first.

## Context

The current exploitation pipeline mostly reasons from one selected opponent or
from aggregate active-opponent stats. That is good enough for heads-up and for
some facing-aggression spots, but it is strategically wrong in multiway pots.

In poker, exploitative adjustments should target the decision-relevant opponent:

- Facing a bet or raise: start with the aggressor's tendencies, then constrain by
  players still behind.
- Betting multiway: use the opponents who can continue against the bet, not a
  table average.
- Bluffing multiway: a bluff needs enough fold equity across the whole field.
  One foldy opponent does not justify bluffing into a calling station.
- Value betting multiway: one calling station can increase value, but other
  opponents' stronger ranges still cap sizing and frequency.
- Preflop steals: use the players left to act behind hero, especially blinds, not
  all seated opponents.

Phase 6.7 introduces an explicit opponent-spot layer so each exploitation rule
can choose the correct opponent set for its poker context.

## Resolved Decisions

- Multiway c-bet v1 should follow the general poker recommendation: bluff
  conservatively and require the relevant continuing opponents to overfold.
- Unknown or low-sample opponents block multiway c-bet bluffing in v1. We can
  relax this later if diagnostics show it is too tight.
- Use the minimum c-bet-fold intensity across eligible opponents rather than an
  average. The weakest folding opponent is usually the limiting factor.
- 6.7a should fix selection correctness first. Do not tune exploitation
  frequencies or enable net-new multiway bluffing in 6.7a unless a correctness
  fix requires a small compensating cap. Multiway c-bet enablement is 6.7b
  Part A.
- 6.7b is intentionally narrow: **Part A** is conservative multiway c-bet
  using existing `high_fold_to_cbet` intensity (shipped). **Part B**
  (playstyle-gated `value_vs_station` and `steal_pressure` rule families)
  is carved out as [Phase 8](PHASE_8_PLAYSTYLE_RULE_FAMILIES.md) because
  it requires defining new rule semantics that don't exist anywhere in
  the codebase — distinct scope from selection-correctness migration.
- `aggregate_from_spots()` must preserve the existing
  `aggregate_active_opponents()` semantics during migration, including its
  multiway 60% rule. Compatibility aggregate stats should not quietly switch to
  naive averages while some rules still consume aggregate stats.
- Add a new `select_primary_aggressor()` helper. Do not replace or reuse
  `_identify_recent_aggressor()` for this job because the existing helper
  treats ties as "no aggressor" without using live-aggression metadata. The
  new helper resolves strictly-highest-bet cases unambiguously, then for
  tied-highest-bet cases falls through `is_aggressor` → `recent_aggressor_name`
  → return `None` (aggregate fallback). It must NOT pick an arbitrary
  highest-bet caller or all-in participant.
- `OpponentSpot` carries `committed_this_hand` (in addition to
  `committed_this_street`) so `aggregate_from_spots()` can run the existing
  60% rule on hand-level totals. Do not pass a parallel `money_committed`
  map into the helper; spots are the single source of truth.
- Add accepted-action postflop aggressor tracking before relying on
  `is_aggressor` or `recent_aggressor_name`. Phase 6.6 only tracks the last
  preflop aggressor for c-bet context; 6.7 needs per-street live aggressor
  metadata for tied-bet disambiguation.
- After 6.7a selection correctness, rule migration order is playstyle-gated,
  not random. The detailed playstyle-gating policy (which rule family fires
  for which archetype) lives in [Phase 8](PHASE_8_PLAYSTYLE_RULE_FAMILIES.md);
  this plan no longer specifies it.

## Goal

A completed first version of 6.7 should:

- Build independent `OpponentSpot` records for each active opponent at decision
  time.
- Preserve existing aggregate behavior as a compatibility fallback.
- Move facing-aggression exploitation to the actual aggressor rather than the
  active-table aggregate.
- Move existing heads-up c-bet exploitation to opponent spots without changing
  behavior in 6.7a.
- Define conservative multiway c-bet behavior for 6.7b, after spot diagnostics
  confirm the selection layer is correct.
- Add diagnostics that show which opponent or opponent set drove each exploit.
- Keep Phase 6.6 heads-up behavior stable.

## Proposed Model

Add a small immutable structure near the exploitation strategy layer or controller
context builder:

```python
@dataclass(frozen=True)
class OpponentSpot:
    name: str
    stats: AggregatedOpponentStats
    is_active: bool
    is_aggressor: bool = False
    is_all_in: bool = False
    current_bet: int = 0
    stack: int = 0
    committed_this_street: int = 0
    committed_this_hand: int = 0
    can_act_behind: bool = False
    has_position_on_hero: bool = False
```

`committed_this_hand` is the hand-level total (`player.total_bet` when
available, falling back to `player.bet`). This is the same value the existing
`tiered_bot_controller._get_money_committed()` builds, and it is required
input for the multiway 60% rule that `aggregate_from_spots()` must preserve.
`committed_this_street` is kept separately for future per-street logic.

`is_aggressor` is not inferred from equal bet amounts. It is set only from
accepted-action tracking: the last player to make a bet/raise/all-in on the
current betting street. Reset it on each new street and at hand start.

Extend `DecisionContext` with spot-level metadata:

```python
@dataclass(frozen=True)
class DecisionContext:
    is_preflop: bool = False
    facing_all_in: bool = False
    facing_big_bet: bool = False
    is_flop_as_preflop_aggressor: bool = False
    active_opponent_count: int = 0
    facing_aggressor_name: Optional[str] = None
```

Keep aggregate stats available:

```python
def aggregate_from_spots(spots: Sequence[OpponentSpot]) -> AggregatedOpponentStats:
    ...
```

`aggregate_from_spots()` is a compatibility bridge, not a new statistical
policy. It should preserve the current `aggregate_active_opponents()` behavior,
including the existing multiway 60% rule and weighting choices, so unmigrated
rules behave the same while the spot-aware migration is in progress.

The 60% rule needs hand-level money committed, which is why
`OpponentSpot.committed_this_hand` is required. The helper computes the
dominant-opponent check from those values directly; it should NOT take a
separate `money_committed: Dict[str, float]` argument, since that would create
a second source of truth that can drift from the spots.

This lets migration happen one rule at a time instead of rewriting the whole
exploitation system at once.

## Rule Semantics

### Facing bet or raise

Use the opponent whose `current_bet` equals the live highest bet and who caused
the action hero is facing. This avoids the short-stack bug class where an
unrelated all-in player changes the response to a deep-stack aggressor.

Before this helper is used for behavior, add postflop live-aggressor tracking to
the action-recording path:

- On each street transition and hand start, clear `recent_aggressor_name`.
- On accepted postflop `bet`, `raise`, or `all_in`, set
  `recent_aggressor_name` to that player.
- When building spots, mark exactly that player's active spot as
  `is_aggressor=True` if they are still tied for the highest live bet.
- Do not use controller intent before action validation as the source.

Disambiguation rules when multiple players are tied at the highest bet:

1. If exactly one tied spot has `is_aggressor=True` (the live aggressor flag
   sourced from `MemoryManager`'s last-aggressor tracking from Phase 6.6,
   extended postflop), return that spot.
2. Else if `recent_aggressor_name` is provided and matches a tied spot,
   return that spot.
3. Else return `None` — no unambiguous primary aggressor. Caller falls back
   to the compatibility aggregate path (`aggregate_from_spots()`) and the
   diagnostic logs the spot as ambiguous.

Rule 3 deliberately gives up on selection rather than picking an arbitrary
tied opponent. Falling back to "any highest-bet active opponent" can land on
a passive caller or an unrelated all-in participant and drive exploitation
from the wrong stats. The aggregate fallback is intentionally conservative
and is the same path unmigrated multiway rules already use.

Implement this as a new helper, for example:

```python
def select_primary_aggressor(
    spots: Sequence[OpponentSpot],
    highest_current_bet: int,
    recent_aggressor_name: Optional[str],
) -> Optional[OpponentSpot]:
    ...
```

Do not replace `_identify_recent_aggressor()` with this helper. The existing
helper has tie-returning-`None` semantics that may be useful elsewhere, but they
are not correct for the strictly-highest-bet case above. The new helper's
return-`None` cases are explicitly enumerated (rule 3 above), not the
result of an early-return on any tie.

### Heads-up c-bet

Use the single active opponent's spot. This should match Phase 6.6 behavior.

### Multiway c-bet

**6.7a**: does not enable new multiway c-bet bluffing. It only moves the
existing heads-up c-bet behavior to opponent spots and logs multiway
opportunities via the `multiway_cbet_opportunity_logged` counter.

**6.7b Part A** (shipped): conservative multiway c-bet using the existing
`high_fold_to_cbet` intensity. Gates:

- Hero is the last preflop aggressor (`is_flop_as_preflop_aggressor`).
- No bet currently facing hero.
- At least two active opponents.
- Every active opponent has `fold_to_cbet > 0.60` AND
  `cbet_faced_count >= 5`.
- No active opponent is all-in. An all-in player cannot fold, so pure
  bluff fold equity is zero against that part of the field.

Intensity is the `min` across active opponents (the weakest folding
opponent caps the bluff EV). Implemented as
`compute_multiway_cbet_intensity()` in `poker/strategy/exploitation.py`;
fires through `compute_exploitation_offsets` via a new
`multiway_cbet_intensity` parameter.

### Value betting, preflop steals, and playstyle-gated rule priority

**Carved out of 6.7b and deferred to [Phase 8](PHASE_8_PLAYSTYLE_RULE_FAMILIES.md).**
Defining `value_vs_station` and `steal_pressure` rule families requires
new rule semantics that don't exist in the codebase today, which is
distinct scope from the selection-correctness migration this plan
covers. See Phase 8 for the playstyle gating policy
(Nit/Rock/TAG → value-vs-station first; LAG/Maniac → steal/pressure
first; Baseline/custom follow diagnostic volume) and for `value_vs_
station` / `steal_pressure` rule semantics including the field-safety
dampener.

## Migration Plan

1. Add accepted-action postflop aggressor tracking to the memory/action-recording
   path: reset on hand/street start; update on accepted postflop
   `bet`/`raise`/`all_in`; expose `recent_aggressor_name` for spot building.
   No exploitation behavior change.
2. Add `OpponentSpot` construction in `tiered_bot_controller.py` alongside the
   existing aggregate stats path. Add `aggregate_from_spots()` with preserved
   `aggregate_active_opponents()` semantics. No behavior change.
3. Add diagnostics that dump active spots, selected aggressor, ambiguous
   aggressor spots, and selected exploitation driver in validation logs.
4. Move facing-aggression selection from aggregate stats to
   `select_primary_aggressor(spots, highest_current_bet)`.
5. Move Phase 6.6 heads-up c-bet to use the single opponent spot.
6. Stop after 6.7a validation. Confirm no behavior drift beyond intended
   selection correctness fixes.
7. **6.7b Part A** (shipped): add conservative multiway c-bet using minimum
   fold-to-cbet intensity, with unknown, low-sample, station, or all-in
   opponents suppressing the bluff. Keep frequency and sizing constants
   unchanged except where required to preserve safety. Tighten
   `multiway_cbet_opportunity_logged` to mirror the rule's all-in
   suppression and add `fired_multiway_cbet` counter.
8. Playstyle-gated rule migration and new rule families are deferred to
   [Phase 8](PHASE_8_PLAYSTYLE_RULE_FAMILIES.md). 6.7 is complete after
   step 7.
9. Deprecate aggregate active-opponent exploitation once rule coverage is
   explicit and tests cover the migrated paths.

## Tests

Add focused tests before changing broad behavior:

- Builds one `OpponentSpot` per active non-hero player.
- Folded and busted players are excluded from active continuing-opponent sets.
- Accepted postflop `bet`, `raise`, and `all_in` update
  `recent_aggressor_name`; street/hand transitions clear it.
- Facing all-in selects the actual live aggressor, not an unrelated short stack.
- `select_primary_aggressor()` strictly-highest-bet case returns that spot
  even when other opponents are tied at a lower bet.
- `select_primary_aggressor()` tied-highest-bet case with one `is_aggressor=True`
  spot returns that spot.
- `select_primary_aggressor()` tied-highest-bet case with no `is_aggressor`
  flag and no `recent_aggressor_name` returns `None`; caller falls through
  to `aggregate_from_spots()` and the diagnostic counter for ambiguous spots
  increments.
- `aggregate_from_spots()` reproduces `aggregate_active_opponents()`
  field-by-field for every shared field given the same opponent set:
  dominant-opponent (60% rule) case and weighted-average case both match. New
  6.6 fields (`fold_to_cbet`, `cbet_faced_count`) get explicit equivalence
  assertions rather than relying on literal byte-for-byte object comparison.
- Heads-up c-bet behavior matches Phase 6.6.
- Multiway c-bet does not fire when one opponent is foldy and another is a
  station (6.7b Part A).
- Multiway c-bet does not fire when any continuing opponent is all-in
  (6.7b Part A).
- Multiway c-bet fires weakly when all continuing opponents have high
  fold-to-cbet samples (6.7b Part A, not 6.7a).
- Preflop steal and playstyle-gated rule family tests live in
  [Phase 8](PHASE_8_PLAYSTYLE_RULE_FAMILIES.md).

## Validation

Use mechanism counters before outcome claims:

- Count spot-built decisions.
- Count selected aggressor decisions.
- Count ambiguous-aggressor decisions (tied highest bet, no aggressor flag,
  no recent aggressor name → aggregate fallback).
- Count multiway c-bet opportunities logged
  (`multiway_cbet_opportunity_logged`) and actually fired
  (`fired_multiway_cbet`). Playstyle-gated rule family counters are
  Phase 8 scope.
- Count exploit-driver names by pattern.

Outcome validation should compare against Phase 6.6:

- No regression in heads-up c-bet spots after 6.7a or 6.7b Part A.
- No regression in Phase 6.5 value override behavior.
- 6.7a should have no net-new multiway c-bet firing. 6.7b Part A multiway
  c-bet firing rate should be low at first; correctness is more important
  than volume.
- 6-max bb/100 should not drop materially while diagnostics confirm fewer
  aggregate-driven false positives.

## Effort Estimate

First clean version: 2-3 days.

- Spot construction and diagnostics: 0.5 day.
- Facing-aggressor selection migration: 0.5 day.
- C-bet spot migration and conservative multiway policy: 0.5-1 day.
- Tests: 0.5-1 day.
- Validation: 0.5 day.

Robust version with richer diagnostics, preflop steal migration, and sizing
policy for value betting: 4-5 days.

## Open Questions

- None blocking 6.7a. For 6.7b+, diagnostics can still tune thresholds and
  decide when to enable the non-priority rule family for each playstyle.

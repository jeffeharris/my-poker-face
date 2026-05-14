---
purpose: Reference for the TieredBot postflop decision-quality system (hand classifier, archetype classifier, defense floor, bluff reduction, offset budgets, diagnostics)
type: reference
created: 2026-05-14
last_updated: 2026-05-14
---

# TieredBot Decision Quality

This is the post-decision-quality state of the TieredBot postflop
pipeline. For *why* each piece exists, see the design doc at
`docs/plans/TIEREDBOT_DECISION_QUALITY.md`. This reference shows
*what* is in the code today, where, and how the pieces fit
together.

## Pipeline order

Postflop decisions flow through these layers (each emits an
`InterventionTrace`; see `poker/strategy/intervention_trace.py`):

```
0  personality          — modify_strategy (table → personality-distorted)
1  exploitation         — hyper_aggressive / hyper_passive / tight_nit /
                          high_fold_to_cbet / multiway_cbet offsets
1  value_vs_station     — Phase 8: +bet_* on strong+ vs hyper_passive opps
1  steal_pressure       — Phase 8: +raise_* in preflop open spots vs nits
1  bluff_reduction      — §5: -bet_*/raise_* on air vs station opps
2  strong_hand_override — Phase 6.5: replaces strategy with call/raise
                          for strong+ vs hyper_aggressive
3  bluff_catch_override — Phase 7.5: pot-odds-conditional {call, fold} for
                          medium/weak vs EXTREME aggressors
4  defense_floor        — §2: pumps call probability for legitimate made
                          hands at favorable prices (matrix-gated)
5  short_stack          — Phase 6 Step B: suppress medium-raise mass below
                          20 BB effective stack
6  math_floor           — pot-committed / short-stack veto
```

Layers 1 (exploitation cluster) all write offsets that combine
into a single L1-bounded shift. Layers 2-4 can each *replace* the
strategy distribution; downstream layers defer when an upstream
override fired (`prior_layer_fired`).

The §5.5 per-rule offset budget framework applies *between* the
layer-1 rules' contributions and the trace emission step — see
"Per-rule offset budgets" below.

## §1 board-aware hand classification

`poker/strategy/hand_classification.py`

The classifier extracts three orthogonal axes from
`(hole_cards, community_cards)`:

| Axis | Values | Meaning |
|---|---|---|
| `hand_class` | `nuts` / `strong_made` / `medium_made` / `weak_made` / `air` | Post-downgrade strategic strength (the *only* label the legacy postflop strategy table reads) |
| `nut_status` | `actual_nuts` / `near_nuts` / `non_nut_strong` / `bluff_catcher` | Nut-ness independent of hand class (a top pair on a 4-Broadway board is `medium_made` + `bluff_catcher`) |
| `danger_flags` | frozenset of named flags | Board + hand-vs-board danger signals |

Danger flag names (constants in `hand_classification.py`):

- `paired_board` — any pair on board
- `trips_on_board` — three of a rank on board
- `four_straight_board` — 4 ranks within a 4-rank window
- `four_flush_board` — 4 cards of one suit on board
- `higher_straight_possible` — hero has straight, 4-rank board window means opp could hold a higher straight rank
- `higher_flush_possible` — hero has non-nut flush, top flush card unblocked
- `full_house_possible` — paired board + hero below FH

The classifier *downgrades* the raw `made_tier` when the hand's
nut status doesn't match its rank: `nuts + non_nut_strong →
strong_made`, `nuts + bluff_catcher → medium_made`, `strong_made
+ bluff_catcher → medium_made`. Downstream consumers (`value_override`,
`bluff_catch_override`, defense floor, strategy table) see the
corrected `hand_class` without consumer-side changes.

API:
- `classify_hand(hole, community) -> (made_tier, draw_modifier)`
  — legacy 2-tuple, returns the downgraded `made_tier`
- `classify_hand_full(hole, community) -> HandClassification`
  — full dataclass with all axes plus `simplify_hand_class` output

The `PostflopNode` (`poker/strategy/nodes.py`) carries `nut_status`
and `danger_flags` as fields, but they're *excluded* from `.key`
so strategy-table lookups stay stable.

## §1.5a unified opponent archetype classifier

`poker/strategy/exploitation.py::classify_opponent_archetype`

Single label per opponent, composed from the existing `_is_<pattern>`
detectors:

| Label | Composition |
|---|---|
| `'hyper_aggressive'` | `_is_hyper_aggressive` (AF > 5.0 OR all_in_freq > 0.30) |
| `'sticky_jammer'` | `_is_passive_with_jams` (hyper_passive + all_in > 0.05) |
| `'pure_station'` | `_is_hyper_passive` without `passive_with_jams` |
| `None` | cold-start (hands < `MIN_HANDS_DEFAULT = 15`) OR no detector matches |

Precedence is documented in the function: `hyper_aggressive`
first, then `sticky_jammer`, then `pure_station`. The §1.5b
extended taxonomy (`maniac`/`lag`/`tag`/`nit`/`rock`/`balanced`)
is *deferred* and ships only when a consumer rule needs richer
labels. See the plan doc §1.5 for the deferral rationale.

**Precedence limitation**: an opponent that satisfies *both*
`_is_hyper_aggressive` (via `all_in_freq > 0.30`) and
`_is_hyper_passive` (via VPIP+AF) gets labeled `hyper_aggressive`
— the `_is_hyper_passive` signal is hidden. This is why
`compute_value_vs_station_intensity` and
`detect_passive_with_jams_in_field` deliberately stay on the
direct detectors rather than the unified label. See the plan
doc's "Precedence limitation" note for why.

Consumer migration: `value_override._should_apply_value_override`
gates on `classify_opponent_archetype(stats) == 'hyper_aggressive'`
instead of the legacy `classify_detected_patterns` check.
Behavior-faithful by construction (the conditions are equivalent
for the `hyper_aggressive` label).

Diagnostic counter: `archetype_classified_<label>` in the
`_tally_exploitation_event` path. Past-min-hands opponents that
match no detector get bucketed as `'unmatched'`; cold-start
decisions get `'cold_start'`.

## §4 bet-size classification

`poker/strategy/bet_size_classification.py`

Maps a faced bet to one of four buckets keyed on **required
equity** (the canonical pot-odds input):

| Bucket | Required equity |
|---|---|
| `small` | ≤ 20% |
| `medium` | 20-35% |
| `large` | 35-50% |
| `jam` | > 50% OR `facing_all_in=True` |

`required_equity = call_amount / (pot_before_bet + 2*call_amount)`.
Asymptotes to 0.5 — no standard postflop call structure produces
> 50% required equity, so the `jam` bucket primarily flags
all-in shoves regardless of price.

`DecisionContext` (in `exploitation.py`) carries `bet_bucket:
Optional[str]` and `required_equity: float` fields. Populated
once at the top of `_build_decision_context`; consumed by §2
defense floor + post-hand diagnostics. Independent of the
legacy `bet_size_pot_ratio` band logic in
`value_override._base_call_prob` — that matrix stays put for
backwards compatibility with Phase 7.5 bluff-catch behavior.

## §2 defense floor

`poker/strategy/defense_floor.py`

Pumps call probability for legitimate made hands at favorable
prices that upstream rules left fold-heavy.

Matrix (top-down, first match wins):

| Condition | Target call prob |
|---|---|
| `hand_class == air` | no floor (explicit exit) |
| `nut_status == bluff_catcher` | no floor — defers to §7.5 bluff_catch |
| `req ≤ 45%` AND `nut_status ∈ {near_nuts, actual_nuts}` | 0.95 (strong) |
| `req ≤ 35%` AND (strong+ class OR `non_nut_strong`) | 0.80 (keep alive) |
| `req ≤ 20%` AND `hand_class ∈ {medium_made, …, nuts}` | 0.80 (keep alive) |

Skip conditions:
- `facing_bet=False` (no bet to face)
- `prior_layer_fired=True` (value_override or bluff_catch already
  replaced the distribution — don't double-up)
- `'call' not in strategy.action_probabilities`

Board-danger dampener: each board-only flag (`paired_board`,
`four_straight_board`, `four_flush_board`) scales the gap between
current and target call prob by `1 - 0.15` per flag, floor at 40%
of the un-dampened move. Hand-specific flags
(`higher_straight_possible`, `full_house_possible`,
`higher_flush_possible`) are *not* applied as dampeners — they're
already encoded in `nut_status`, so counting them again would
double-dampen.

Redistribution math: when firing, bumps `call` to the dampened
target and scales down non-call actions proportionally. Total mass
stays at 1.

**Rejected experiment**: a candidate "jam-price value-call" row
(req ≤ 50% + `non_nut_strong` + strong+ class → 0.65) was
implemented and tested against the 1000×5 sim; the extra calls
were net-negative (~-3.5 bb/100). CaseBot's actual jam range is
tighter than the assumed "wide jam range", so folding
`non_nut_strong` to jams is correct. See the
`ROW_KEEP_ALIVE_MEDIUM_MAX_REQ` comment block in defense_floor.py.
A future archetype-gated variant (only fire for `pure_station` /
`lag` / `maniac`) could plausibly revive this when those
opponents enter the benchmark set.

## §5 bluff reduction vs stations

`poker/strategy/exploitation.py` — `('bluff_reduction', 'default')` rule

Mirror of `value_vs_station`: same station detection (reuses
`compute_value_vs_station_intensity`), inverse hand-strength
gate. Fires when hero has an air-class hand
(`air_no_draw` / `air_strong_draw`) AND a station is in the field.

Offsets per action (scaled by `phase_8_multiplier *
bluff_reduction_intensity`):

| Action pattern | Offset |
|---|---|
| `bet_*`, `raise_*` | −0.20 |
| `check` | +0.10 |
| `fold` | +0.05 |

Magnitude is intentionally smaller than `value_vs_station`'s +0.30
to leave headroom for stacking with the legacy `hyper_passive`
rule (+0.30 raise-like, −0.20 fold). Hand-class gate enforced by
the controller: passes `bluff_reduction_intensity=0` for non-air
hands. Mutually exclusive with `value_vs_station` by hand-class.

## §5.5 per-rule offset budgets

`poker/strategy/exploitation.py::MAX_L1_SHIFT_BY_RULE`

Each rule that contributes to `compute_exploitation_offsets`
declares a maximum L1 shift it's allowed to add. After all rule
branches contribute, a post-pass walks `rule_offsets`, computes
L1 per rule, and proportionally scales any rule that exceeds
budget.

Budgets (post-§7 validation tuning):

| Rule | MAX_L1_SHIFT |
|---|---|
| `hyper_aggressive` | 1.10 |
| `hyper_passive` | 0.80 |
| `tight_nit` | 0.50 |
| `high_fold_to_cbet` | 1.60 |
| `multiway_cbet` | 1.60 |
| `value_vs_station` | 1.20 |
| `steal_pressure` | 0.50 |
| `bluff_reduction` | 1.30 |

Values sized to current rule maximums + headroom — this is a
**safety net**, not a re-calibration. The framework catches
future drift or stacking anomalies; current rules ship within
their budgets and no `budget_clamped_*` events appear under
normal sim conditions.

Trace surface: when a rule is clamped, `trace.inputs` gains
`budget_clamped=True`, `budget_clamp_scale`, `budget_pre_clamp_l1`,
`budget_max_l1`.

Phase 7.5's three-tier clamp remains the *outermost* safety
net (operates on the combined distribution); per-rule budgets are
an inner bound. Tightening budgets to actively shape behavior
is future tuning work, not part of the §5.5 framework ship.

## §6 diagnostics

The controller's `_last_pipeline_snapshot` is the canonical
per-decision context dump. Populated piecewise across the
pipeline; consumed by `casebot_breakdown` for post-hand analytics.

Snapshot fields (all populated for postflop decisions):

| Field | Source | Section |
|---|---|---|
| `hand_strength` | `_classify_postflop_hand_strength` | existing |
| `nut_status` | PostflopNode.nut_status | §1 |
| `danger_flags` | PostflopNode.danger_flags | §1 |
| `bet_bucket` | DecisionContext.bet_bucket | §4 |
| `required_equity` | DecisionContext.required_equity | §4 |
| `opponent_archetype` | classify_opponent_archetype (set in `_tally_exploitation_event`) | §6 |

The archetype field uses `'unmatched'` for past-min-hands
opponents that match no detector, and `'cold_start'` for the
aggregate-cold-start early-return path (hands < 15).

The `InterventionTrace` extension proposed in the original plan
(per-decision context attached to traces) is **deferred**. The
pipeline snapshot is the source of truth today; promoting it to
a trace payload becomes valuable when cross-replay analytics
need traces to carry per-decision context.

### casebot_breakdown reports (`experiments/casebot_breakdown.py`)

Three aggregated reports post-§6:

1. **Postflop folds by `(phase, hand_class, nut_status, bet_bucket)`**
   (`print_multi_axis_breakdown`) — surfaces fold concentration
   patterns. Most useful for "is the §2 floor missing a leak?"
2. **Postflop folds by opponent archetype**
   (`print_archetype_breakdown`) — answers "do we fold more vs
   sticky_jammer than pure_station?"
3. **Value-bet + bluff frequency**
   (`print_value_and_bluff_freq_breakdown`) — aggressive% for
   `strong_made`/`nuts` (value-bet rate) and `air_*` (bluff rate)
   grouped by archetype. Verifies §5 bluff_reduction is working
   at the rate level.

Per-example captured-hand printouts now include the strategic
context line (`nut_status`, `bet_bucket`, `required_equity`,
`opponent_archetype`, `danger_flags`) for manual inspection.

## §7 validation coverage

Each scenario from the original §7 list maps to existing tests
(no new framework was built; see plan doc §7 for the audit):

| Scenario | Test file |
|---|---|
| Paired boards | `tests/test_strategy/test_hand_classification.py` |
| 4-straight / 4-flush boards | same |
| Top pair at small prices | `tests/test_strategy/test_defense_floor.py` |
| Marginal bluff-catchers vs large bets | `tests/test_strategy/test_section_3_passive_archetype_behavior.py` |
| Strong hands vs passive opponents | same |
| Air vs low-fold opponents | `tests/test_strategy/test_bluff_reduction.py` |
| Short-stack / low-SPR | `tests/test_strategy/test_short_stack.py` (Phase 6 Step B) |

Tracked metrics — see §6 above for where each is captured. The
formerly soft gaps (sim-aggregated value-bet / bluff frequency)
are now in `print_value_and_bluff_freq_breakdown`.

## Sim baseline + key findings

TAG vs CaseBot HU, 1000 hands × 5 seeds:

- **Aggregate**: -71.3 bb/100 (improvement of ~25 bb/100 from the
  -96.8 baseline before this work)
- **Value-bet rate** (`strong_made`/`nuts` vs `pure_station`):
  ~53% — consistent
- **Bluff rate** (`air_no_draw` vs `pure_station`): 18.4% (§5
  working — well below the typical 25-30% baseline)
- **Showdown wins**: +171.4 bb/100 contribution
- **Showdown losses**: -104.8 bb/100 contribution

Largest remaining fold buckets (multi-axis report):

| Bucket | bb/100 | Real leak? |
|---|---|---|
| `flop air_no_draw bluff_catcher medium` | -16.4 | No — correct folds; the price paid to reach the flop is the upstream signal |
| `river strong_made non_nut_strong jam` | -15.5 | No — sim rejected the candidate fix; folds are correct vs CaseBot's tight jam range |
| `flop air_no_draw bluff_catcher small` | -13.1 | No — correct folds |
| `river strong_made non_nut_strong small` | -5.7 | **Maybe** — cheap-price strong-hand folds. Worth investigating if §2 row 4 isn't firing or if a downstream layer overrides |

## Rejected experiments (don't re-try without new context)

- **Jam-price value-call row for §2** (`req ≤ 50% +
  non_nut_strong + strong+ → 0.65`): tested, -3.5 bb/100 in
  1000×5 sim, reverted. CaseBot's jam range tighter than assumed.
  See defense_floor.py comment block.
- **Phase 8.1b global fold-mass suppression** (pre-this-work): a
  blanket suppression of folds vs all hyper_passive opponents.
  Bled bb/100 by calling marginals into wide jam ranges.
  Replaced by hand-class-gated defense floor in §2.

## Parked follow-ups

- **§1.5b extended archetype taxonomy** — adds `maniac`, `lag`,
  `tag`, `nit`, `rock`, `balanced` labels. Ships per-consumer
  demand (no current rule needs them).
- **§5.5 budget tightening** — current budgets are safety nets
  sized to rule maximums; tighter values would actively shape
  behavior. Needs sim evidence of a stacking event to justify.
- **Snapshot-based scenario-replay framework** — capture
  fixtures during sim runs, replay via
  `poker/strategy/replay.py`. Becomes valuable when §1.5b or
  post-ship tuning needs cross-iteration regression checking.

## File pointers

| Concern | File |
|---|---|
| Hand classifier | `poker/strategy/hand_classification.py` |
| Archetype classifier | `poker/strategy/exploitation.py` |
| Bet-size classifier | `poker/strategy/bet_size_classification.py` |
| Defense floor | `poker/strategy/defense_floor.py` |
| Bluff reduction | `poker/strategy/exploitation.py` (rule in `compute_exploitation_offsets_with_traces`) |
| Offset budgets | `poker/strategy/exploitation.py::MAX_L1_SHIFT_BY_RULE` |
| Pipeline orchestration | `poker/tiered_bot_controller.py::_get_postflop_decision` |
| Layer order / trace canonical names | `poker/strategy/intervention_trace.py` |
| Diagnostics report | `experiments/casebot_breakdown.py` |
| Design rationale | `docs/plans/TIEREDBOT_DECISION_QUALITY.md` |

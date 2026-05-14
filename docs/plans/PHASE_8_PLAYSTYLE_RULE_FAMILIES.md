---
purpose: Plan to define and gate new exploitation rule families (value-vs-station, steal/pressure) by archetype playstyle
type: design
created: 2026-05-13
last_updated: 2026-05-14
---

## v1 implementation status (2026-05-14)

Shipped in the v1 PR:

- `compute_value_vs_station_intensity(spots)` and
  `compute_steal_pressure_intensity(spots)` as pure functions in
  `poker/strategy/exploitation.py`.
- `compute_exploitation_offsets` extended with
  `value_vs_station_intensity` and `steal_pressure_intensity` scalar
  parameters and matching offset branches.
- `is_value_vs_station_enabled` / `is_steal_pressure_enabled`
  frozenset gates.
- `OpponentSpot.is_blind` field. `can_act_behind` populated inside
  `_build_opponent_spots` from `Player.has_acted` (much simpler than
  the seat-traversal sketch in the original plan body — see "Resolved
  implementation choices" below).
- `_apply_exploitation` extended with optional `hand_strength`
  parameter; postflop caller computes once via
  `_classify_postflop_hand_strength(node)` and passes through.
- `_tally_playstyle_rule_event()` on the controller; called after
  `_apply_value_override` in both preflop and postflop paths to track
  eligible / enabled_eligible / fired / superseded_by_override /
  diagnostic_only counters per archetype + family.
- Tests in `tests/test_strategy/test_playstyle_rule_families.py`
  (50 tests). Naming is behavior-driven, not phase-numbered.

**`steal_pressure` ships disabled** — `STEAL_PRESSURE_PLAYSTYLES = frozenset()`
so no archetype enables it. Piping + diagnostics are live, so the
counters tell us how often it WOULD have fired across a sim run. The
proper activation (and the hyper_passive co-fire question from
Risk #1) lives in `PHASE_8_1_TRACKING_AND_HYPER_PASSIVE.md`.

### Resolved implementation choices

| Question | Resolution |
|---|---|
| `value_vs_station` intensity formula | `upside × safety` where upside = max hyper_passive intensity over stations and safety = `1 - VVS_SAFETY_WEIGHT × max tight_nit intensity over non-stations` (VVS_SAFETY_WEIGHT=0.5). Hardcode existing thresholds; expose only VVS_SAFETY_WEIGHT as module constant. |
| `steal_pressure` magnitude | `0.15` (half of value_vs_station's `0.30`) — the VPIP+PFR proxy is weak; conservative until fold_to_open lands. |
| `can_act_behind` derivation | `not p.is_folded and not p.is_all_in and not p.has_acted` — `Player.has_acted` is reset by the state machine on every accepted raise, so this naturally handles BB option and 3-bet re-opens without seat traversal. |
| Override-supersedes diagnostic | `_last_value_override_fired` stashed on controller (mirrors existing `_last_clamp_tier` pattern), read by post-override tally helper. Less invasive than changing `_apply_value_override` return signature. |
| Eligibility counter computation | Pure intensity helpers run regardless of playstyle gate; gate masks the value passed to `compute_exploitation_offsets` to 0 for inactive archetypes. This makes the diagnostic split (eligible / enabled_eligible / diagnostic_only) cheap to maintain. |
| Steal_pressure blind weighting | `OpponentSpot.is_blind` (single bool, derived from `game_state.small_blind_idx` / `big_blind_idx`). Weight is 1.5× per blind defender in the intensity helper. |
| Test naming | `tests/test_strategy/test_playstyle_rule_families.py` — phase-number-free. |

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
5. **Concrete v1 bb/100 targets** (intentionally modest — see
   Risk #1 below; most of the CaseBot drain comes from
   `hyper_passive` not from missing value extraction, so v1 alone
   cannot reach FoldyBot's −35.6 floor):
   - **No regression**: TAG vs CaseBot HU mean stays ≥ `−77.8 bb/100`
     (the current baseline); 6-max 3-seed mean stays ≥ `−57.7`.
   - **Strong-hand extraction visible in diagnostics**:
     `value_vs_station_fired` non-zero in 6-max runs, with `~5-15%`
     of decisions hitting the gate.
   - **Moderate measurable improvement**: per-opponent CaseBot drain
     shrinks from `~−2400 BB` mean toward `~−1800 to −2000 BB` mean
     (10-25% improvement), without the ABCBot gain dropping below
     `~+2500 BB`.
   - HU-vs-balanced (GTO-Lite): no archetype moves by more than
     ±20 bb/100 from Phase 7 baseline.

6. **Phase 8.1 stretch targets** (require enabling `hyper_passive`
   fold-mass suppression — see Risk #1; gated on v1 diagnostics):
   - TAG vs CaseBot HU mean moves toward `−35.6` (FoldyBot floor).
   - 6-max 3-seed mean moves toward 0 (net flat or positive).
   - Per-opponent CaseBot drain shrinks from `~−2400 BB` toward
     `~−1000 BB` while ABCBot gain stays at or above `~+2800 BB`.

## Rule semantics

### `value_vs_station`

**Hand-strength gated** — mirrors Phase 6.5's value-override
pattern (strong-vs-aggressive) but for the strong-vs-passive
spot. The empirical evidence (FoldyBot beating TAG by 42 bb/100
vs CaseBot) is unambiguous: **the existing exploit machinery
already over-bets weak hands against stations**. Phase 8 must
NOT add more frequency on marginals — it must add value
extraction specifically when hero is ahead.

The controller (not `compute_exploitation_offsets`) is responsible
for the gate. `value_vs_station` is **postflop-only** and reuses
`_classify_postflop_hand_strength(node)` — the same classifier
Phase 6.5 already uses for its postflop override path. See
"Architecture" below for the data-flow. The preflop classifier
(`_classify_preflop_hand_strength`) returns a different
two-class enum (STRONG / NOT_STRONG) and is **not** used by
Phase 8.

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

### `steal_pressure` (v1: tightness-proxy)

**Important caveat for v1**: `OpponentTendencies` does NOT currently
track `fold_to_open`, `fold_to_steal`, or `fold_to_3bet`. The only
defending-tightness signal available today is VPIP. **Using tight
VPIP alone is a weak proxy** — a TAG opponent (VPIP=0.20) folds wide
to opens preflop but 3-bets back aggressively. Stealing into a
TAG defender is −EV.

v1 ships as **"tightness-proxy steal" with conservative thresholds**
explicitly to avoid false steals into tight-aggressive defenders.
The proper fix (add `fold_to_open` / `fold_to_3bet` counters to
`OpponentTendencies`) is a Phase 8.2 follow-up that requires new
hand-level state in `MemoryManager` (track preflop opens by seat,
attribute defending responses). Documented but not scoped here.

Fires when ALL of:

- Hero is preflop, open spot (no live raise, position warrants a
  steal — late position or blinds).
- Every player left to act behind hero is either (a) NOT in the
  defender bucket OR (b) in the "looks tight" bucket:
  `vpip < VPIP_TIGHT` AND `pfr < PFR_LOOSE` (the second clause is
  the defense-against-false-positives — tight-passive players
  fold to steals; tight-aggressive players 3-bet back).
- At least one player left to act behind hero is the BB or SB.

Action:

- Compute "steal opportunity" intensity from players-left-to-act
  spots only. Folded players, players already all-in, and players
  who have already acted on this street are excluded from this
  rule's input.
- Net offset: increase raise_* probability proportional to intensity,
  weighted heavier when blinds meet the tight-AND-passive filter.
- **Conservative v1 magnitude**: cap the net offset at `0.15` (half
  of `value_vs_station`'s `0.30`) until validation supports more.
  The proxy is weak; the magnitude reflects that uncertainty.

Important constraints:

- This rule must NOT fire when hero is already facing a bet/raise.
- The folded-player exclusion is critical — if `Player1` already
  folded preflop this hand, their tendency shouldn't contribute to
  hero's stealing decision against Players 2/3.
- The PFR upper-bound clause (`pfr < PFR_LOOSE`) is what prevents
  steals into TAG defenders. If we later add `fold_to_3bet`, the
  PFR proxy can relax.

### `can_act_behind` and `has_position_on_hero` (6.7a deferred fields)

`OpponentSpot` already has `can_act_behind` and `has_position_on_hero`
fields, but 6.7a leaves them at `False` (the steal rule wasn't yet
defined). Phase 8 populates them in `_build_opponent_spots`:

- `can_act_behind`: True when the opponent **has not yet acted on the
  current betting round** AND has not folded. **Preflop action order
  is NOT seat order** — blinds act last (UTG → ... → BTN → SB → BB
  in 6-max); after a raise, action continues from the seat to the
  left of the raiser, with the raiser themselves locked out of
  acting again unless re-raised. **Use the state machine's action
  queue, not seat traversal.** Concretely: derive from
  `game_state.has_acted_this_round` (or equivalent — verify against
  `poker.poker_state_machine` for the canonical "who's left to act"
  signal) plus the live `current_player_idx`. Do not assume
  `idx > hero_idx` means "acts behind preflop" — that breaks for
  blind seats.
- `has_position_on_hero`: already populated for postflop position
  signals. For postflop, position is straight seat order from the
  button. For preflop, the steal rule cares about action-order
  position (who will act after hero in this round), which is what
  `can_act_behind` captures. Keep `has_position_on_hero` as the
  postflop-relevant flag; the steal rule reads `can_act_behind`
  for its preflop-specific semantics.
- **Verification test required**: after implementing
  `can_act_behind`, add a test that walks through a 6-max preflop
  round (BTN raises, SB and BB still to act) and asserts that BTN's
  spot has `can_act_behind=False` while SB and BB have
  `can_act_behind=True`. The seat-traversal-naive implementation
  would get this wrong.

## Playstyle classification

Reuse `TieredBotController.archetype_name` for the gate (already
returns one of `'nit'`, `'rock'`, `'tag'`, `'lag'`, `'maniac'`,
`'baseline'`, `'calling_station'`).

```python
VALUE_VS_STATION_PLAYSTYLES = frozenset({'nit', 'rock', 'tag'})
STEAL_PRESSURE_PLAYSTYLES   = frozenset({'lag', 'maniac'})
# Baseline / calling_station / unknown: neither rule enabled in v1.
# - Baseline: pure-chart reference, no exploitation by design.
# - Calling Station hero: archetype itself is a station; the value
#   extraction the rule provides requires a sizing model the
#   archetype's chart doesn't have. Defer until sizing-aware
#   exploitation lands (Phase 7.5 sizing work or later).
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

Counters per archetype + rule family. Naming distinguishes the
two gates (stat-eligibility vs playstyle-enablement) so the
identity below is well-defined.

`value_vs_station`:

- `value_vs_station_eligible_<archetype>`: stats + spots + hand
  strength all qualify (intensity would be > 0). Counted
  **before** the playstyle gate, so this includes decisions that
  end up `diagnostic_only`.
- `value_vs_station_enabled_eligible_<archetype>`: subset of
  eligible where the playstyle gate is ON (intensity actually
  flows into `compute_exploitation_offsets`).
- `value_vs_station_fired_<archetype>`: subset of
  enabled_eligible where the resulting offsets survived to the
  final strategy (Phase 6.5 override did NOT replace this
  decision).
- `value_vs_station_superseded_by_override_<archetype>`: subset
  of enabled_eligible where Phase 6.5 replaced the strategy and
  Phase 8's offsets were discarded. See Risk #3.
- `value_vs_station_diagnostic_only_<archetype>`: subset of
  eligible where the playstyle gate is OFF.

`steal_pressure`:

- `steal_pressure_eligible_<archetype>`
- `steal_pressure_enabled_eligible_<archetype>`
- `steal_pressure_fired_<archetype>` (no override interaction —
  Phase 6.5 doesn't fire preflop on station-style opponents)
- `steal_pressure_diagnostic_only_<archetype>`

Cross-family:

- `playstyle_gated_rule_family_<archetype>` (value_vs_station /
  steal_pressure / diagnostic_only)

**Identities that should hold by construction** (testable):

```
eligible        = enabled_eligible + diagnostic_only
enabled_eligible = fired + superseded_by_override   # value_vs_station
enabled_eligible = fired                            # steal_pressure
```

So `eligible = fired + superseded_by_override + diagnostic_only`
for `value_vs_station`. Add a regression test that asserts both
identities at the end of a sim run.

This makes it easy to see "Nit had 200 steal opportunities but the
playstyle gate suppressed them" (diagnostic_only) vs "TAG fired
value_vs_station 30 times this run" (fired) vs "TAG had 50
value_vs_station opportunities but 40 of them were absorbed by
Phase 6.5's override" (superseded_by_override).

## Architecture

Phase 8 follows the **Phase 6.7b Part A pattern** that's already
proven in the codebase: the pure offset function stays pure, the
controller pre-computes scalar intensities from spots + archetype +
hand strength, then passes those scalars into
`compute_exploitation_offsets`. **The function signature gains two
new scalar parameters** (`value_vs_station_intensity`,
`steal_pressure_intensity`) — not new collection-typed inputs.

### Where Phase 8 runs in the controller pipeline

Today's pipeline (`tiered_bot_controller._get_postflop_decision`):

```
chart → personality → exploitation → value_override → short_stack → math_floor
                       └─ Phase 8 fires HERE, inside _apply_exploitation
```

`value_vs_station` is an offset-shaped rule (frequency shift, not
strategy replacement), so it lives in the exploitation step
alongside the existing patterns and multiway c-bet.

**`value_vs_station` is postflop-only.** It gates on
`HandStrengthClass.STRONG_MADE` / `NUTS`, which are values produced
by `_classify_postflop_hand_strength`. The preflop classifier
(`_classify_preflop_hand_strength`) returns the different
`STRONG`/`NOT_STRONG` two-class enum used by Phase 6.5's preflop
value-override path; Phase 8 does NOT use that classifier.
`steal_pressure` is preflop-only by construction and does not
consume hand strength.

### Interaction with Phase 6.5 value_override (supersedes)

**Important: when Phase 6.5 fires, it REPLACES the strategy that
exploitation produced.** Pipeline order is `exploitation →
value_override`, so any offsets Phase 8 emits inside
`_apply_exploitation` are discarded if `_apply_value_override` then
fires on the same decision.

In practice this is rare: Phase 6.5 fires only when opponent is
`hyper_aggressive`; Phase 8's `value_vs_station` fires only when
opponent is `vpip > VPIP_LOOSE AND AF < AF_PASSIVE`. Those
profiles are mutually exclusive at the per-opponent level. But in
multiway with mixed opponents — or when stat detection straddles a
threshold — both gates can pass on the same decision.

When that happens:

- **Phase 6.5 wins (semantically correct).** Override is the
  stronger signal: hero has a strong hand AND faces aggression →
  collapse to the override's call/raise distribution rather than
  emit a frequency nudge that gets thrown away.
- **Phase 8's intensity is still computed and tallied separately.**
  Add a `value_vs_station_superseded_by_override` counter that
  increments when both gates passed but override won. Distinguishes
  "rule had no opportunity" from "rule had an opportunity but
  override took it." Critical for diagnosing whether Phase 8 is
  pulling its weight.
- `value_vs_station_fired` counts decisions where the offset
  contributed AND survived (i.e. override didn't fire that
  decision). The simplest implementation: controller already calls
  `_tally_exploitation_event` from inside `_apply_exploitation`; for
  Phase 8 the tally moves to AFTER `_apply_value_override` returns,
  with a `value_override_fired` flag passed back from the override
  step so the tally can choose between `value_vs_station_fired` and
  `value_vs_station_superseded_by_override`.

### Controller-side intensity computation

The controller knows things `compute_exploitation_offsets` cannot:
- Hero's hand strength (postflop only — via
  `_classify_postflop_hand_strength`; computed in the caller, NOT
  inside `_apply_exploitation`)
- Hero's archetype (`self.archetype_name`)
- All opponent spots (`_build_opponent_spots`)
- The action queue (for `can_act_behind`)
- `game_state` directly (for `call_amount`, legal actions, etc.)

The controller computes the family intensities BEFORE calling
`compute_exploitation_offsets`. `_apply_exploitation` is extended
to accept a new optional `hand_strength` parameter computed by the
caller — currently `_get_postflop_decision` and the preflop path
build `node` / `canonical_hand` themselves, so they're the right
place to compute hand-strength too:

```python
# In _get_postflop_decision, after building node:
hand_strength = self._classify_postflop_hand_strength(node)
modified_strategy = self._apply_exploitation(
    modified_strategy, game_state, player_idx, valid_actions,
    anchors, emotional_state,
    hand_strength=hand_strength,     # NEW Phase 8 param
)

# In the preflop path:
modified_strategy = self._apply_exploitation(
    ...,
    hand_strength=None,              # value_vs_station is postflop-only
)
```

Inside `_apply_exploitation`, AFTER spots are built:

```python
archetype = self.archetype_name
call_amount = getattr(game_state, 'call_amount', 0) or 0
has_bet_legal = any(
    a == 'bet' or a.startswith('bet_') or a == 'raise' or a == 'all_in'
    for a in valid_actions
)

value_vs_station_intensity = 0.0
if (
    is_value_vs_station_enabled(archetype)
    and hand_strength in {HandStrengthClass.STRONG_MADE.value,
                          HandStrengthClass.NUTS.value}
    and call_amount == 0
    and has_bet_legal
):
    value_vs_station_intensity = compute_value_vs_station_intensity(spots)

steal_pressure_intensity = 0.0
if (
    is_steal_pressure_enabled(archetype)
    and decision_context.is_preflop
    and call_amount == 0   # open spot — no live raise to face
    and has_bet_legal
):
    steal_pressure_intensity = compute_steal_pressure_intensity(spots)

offsets = compute_exploitation_offsets(
    stats=stats, ...,
    multiway_cbet_intensity=multiway_cbet_intensity,
    value_vs_station_intensity=value_vs_station_intensity,
    steal_pressure_intensity=steal_pressure_intensity,
)
```

Notes:

- `call_amount`, `has_bet_legal` are derived from `game_state` and
  `valid_actions` inline — **not** stored on `DecisionContext`. The
  `decision_context` fields used here (`is_preflop`,
  `facing_aggressor_name`, `is_flop_as_preflop_aggressor`) already
  exist from Phase 6.6/6.7a.
- `hand_strength` is the existing `HandStrengthClass` enum from
  `value_override.py`; use `.value` for string comparison since
  the classifiers return string values, not enum members.
- `compute_steal_pressure_intensity` stays **pure** —
  `(spots) -> float`. It reads `spot.can_act_behind` directly. The
  action-queue logic (preflop is not seat order — see Risk #5)
  lives in `_build_opponent_spots`, which already takes
  `game_state` and is the natural place to compute action-queue
  membership. Keeping the intensity helper pure matches the pattern
  for `compute_multiway_cbet_intensity` and
  `compute_value_vs_station_intensity`, and keeps
  `poker/strategy/exploitation.py` free of controller dependencies.

Inside `compute_exploitation_offsets`, the new branches mirror the
multiway c-bet shape:

```python
if value_vs_station_intensity > 0.0:
    scale = multiplier * value_vs_station_intensity
    for action in available_actions:
        if action.startswith('bet_'):
            offsets[action] = offsets.get(action, 0.0) + 0.3 * scale
    if 'check' in available_actions:
        offsets['check'] = offsets.get('check', 0.0) - 0.15 * scale
```

This keeps `compute_exploitation_offsets` referentially transparent
and unit-testable in isolation, matching the pattern from Phase 6.7b
Part A.

## Migration plan

1. Add `compute_value_vs_station_intensity(spots) -> float` and
   `compute_steal_pressure_intensity(spots) -> float` as pure
   functions in `poker/strategy/exploitation.py`. Both read only
   from spots; the action-queue logic lives in
   `_build_opponent_spots` where it can consume `game_state`
   without polluting the pure exploitation module. No behavior
   change yet — these are unused until step 5.
2. Extend `compute_exploitation_offsets` signature with two new
   optional scalar parameters (`value_vs_station_intensity=0.0`,
   `steal_pressure_intensity=0.0`). Add the two new branches inside.
   Existing call sites unaffected.
3. Add `is_value_vs_station_enabled(archetype)` and
   `is_steal_pressure_enabled(archetype)` pure helpers (frozenset
   lookups).
4. Extend `_apply_exploitation` signature with optional
   `hand_strength: Optional[str] = None` parameter. Update both
   callers (`_get_postflop_decision` passes
   `_classify_postflop_hand_strength(node)`; preflop path passes
   `None`).
5. Inside `_apply_exploitation`, compute the two new intensities
   from spots + hand_strength + archetype + game_state (gate values
   like `call_amount`, `has_bet_legal` derived inline from
   `game_state`/`valid_actions` — NOT new `DecisionContext` fields).
   Thread them through to `compute_exploitation_offsets`.
6. Populate `OpponentSpot.can_act_behind` correctly inside
   `_build_opponent_spots` (which already takes `game_state`). This
   is where the action-queue logic lives — preflop action order is
   not seat order (see Risk #5). Use the state machine's action
   queue, not seat traversal. Once spots carry the correct flag,
   `compute_steal_pressure_intensity(spots)` becomes purely
   spot-driven and stays controller-agnostic.
7. **Diagnostic ordering for value_vs_station fired counter.**
   `_tally_exploitation_event` currently runs inside
   `_apply_exploitation`. For Phase 8, the
   `value_vs_station_fired` counter needs to know whether
   `_apply_value_override` ran AFTER and replaced the strategy
   (which would discard Phase 8's offsets). Two implementations:
   - **Option A (recommended)**: have `_apply_value_override`
     return a `(strategy, fired: bool)` tuple. After it returns,
     the controller calls a Phase-8-specific tally helper that
     increments `value_vs_station_fired` or
     `value_vs_station_superseded_by_override` based on the flag.
   - **Option B**: split `_tally_exploitation_event` into a
     pre-tally (counters that don't depend on override) and a
     post-tally (the Phase 8 fire vs superseded distinction),
     called after `_apply_value_override`.
   Either works; Option A is closer to the existing diagnostic
   tally call sites.
8. Validation: run 6-max vs rule mix at multiple `adaptation_bias`
   values + the 3-seed CaseBot-heavy mix. Confirm firing rates match
   expectation per archetype, no regression in spot-aware diagnostics.
9. After validation, decide whether to enable Phase 8.1 (hyper_passive
   fold-mass suppression) based on observed CaseBot drain. See
   "Risks / known interactions" for the gate.

## Tests

- **Pure-function intensity helpers** (in
  `tests/test_strategy/test_opponent_spots.py` or a new
  `test_phase_8.py`):
  - `compute_value_vs_station_intensity(spots)` returns 0 when no
    opponent matches, max-of-station-intensities when one matches,
    dampened by tightest opponent when a tight player is also active.
  - `compute_steal_pressure_intensity(spots)` returns
    0 when no eligible player is left to act, scales with blind
    tightness, **returns 0 when any player-behind has high PFR**
    (the false-steal guard).
- **Hand-strength gate** (controller-level): `value_vs_station`
  intensity is 0 when hand_strength is `medium_made` / `weak_made` /
  `air`. The empirical insight (existing exploit machinery spews on
  weak hands) demands this test exist before the feature ships.
- **`compute_exploitation_offsets` integration**: with the new scalar
  parameters non-zero, offsets land on the expected actions (bet_*
  positive, check negative for value_vs_station; raise_* positive
  for steal_pressure). With both zero, behavior is byte-identical
  to today.
- **Playstyle gating**: for each archetype bucket, verify the right
  family fires and the other stays diagnostic-only.
  `value_vs_station_fired_lag` should always be 0 by construction;
  `steal_pressure_fired_nit` should always be 0.
- **`can_act_behind` preflop semantics** (the seat-order-naive
  pitfall): walk through a 6-max preflop round (UTG folds, MP folds,
  CO folds, BTN raises). Verify SB and BB have `can_act_behind=True`,
  BTN has `can_act_behind=False`. Then BB 3-bets — verify only BTN
  has `can_act_behind=True` (action is on BTN to call/fold/raise).
- **6.5 / 8 ordering (override supersedes)**: when both rules'
  gates pass on the same decision, Phase 6.5 wins because it runs
  AFTER exploitation and replaces the strategy entirely. Verify:
  Phase 8's offsets are emitted into the exploitation result, but
  the final strategy returned to the sampler matches Phase 6.5's
  override output, not the exploitation-with-offsets result. The
  Phase-8 fired counter increments
  `value_vs_station_superseded_by_override` (not `fired`) in this
  case.
- **No regression**: full Phase 6.7a + Phase 6.7b Part A test suite
  passes unchanged with both new intensities at 0.

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

### Risk #3: 6.5 / 8 ordering — override supersedes, not the reverse

Phase 6.5 fires for `STRONG hand vs hyper_aggressive opponent`.
Phase 8 fires for `STRONG hand vs passive station`. The opponent
profiles are mutually exclusive at the per-opponent level, but
multiway pots could have both, and stat detection at threshold
boundaries means the gates can occasionally co-fire on the same
decision.

**Pipeline order is `exploitation → value_override`.** When both
gates pass:

- Phase 8 emits offsets inside `_apply_exploitation`. The offsets
  ARE applied to the strategy at that point.
- `_apply_value_override` then runs. If 6.5's gate also passes, it
  REPLACES the strategy entirely. **Phase 8's offsets are
  discarded.**
- This is semantically correct: override is the stronger signal
  (strong hand + aggression → collapse to override's call/raise
  distribution).

The risk is **diagnostic, not behavioral**. Without explicit
handling, `value_vs_station_fired` would increment whenever the
offsets were emitted, regardless of whether they survived the
override step. That makes the diagnostic misleading.

**Resolution**: split the fired counter into two:

- `value_vs_station_fired`: counts decisions where offsets were
  emitted AND survived (override did not fire that decision).
- `value_vs_station_superseded_by_override`: counts decisions
  where Phase 8 would have contributed offsets but override
  replaced the strategy.

The tally requires `_apply_value_override` to expose whether it
fired (see Migration step 7). With both counters present, operator
can answer "did Phase 8 actually contribute" vs "did Phase 8's
opportunity get absorbed by 6.5."

The semantic guard (skip Phase 8 entirely when 6.5 will fire) is
NOT recommended: it would require evaluating override eligibility
before exploitation, which inverts the current pipeline and adds
two-pass complexity. Computing Phase 8 offsets and then discarding
them via override is cheap.

## Effort estimate

- Detection + offset functions for both rule families: 1.5 days.
- `can_act_behind` derivation + integration: 0.5 day.
- Playstyle gating + diagnostics: 0.5 day.
- Tests: 1 day.
- Validation across multiple archetypes: 1 day.

Expected total: 4–5 days.

## Open questions

- Should the playstyle gate be configurable per experiment (e.g.
  enable both rule families for `Baseline` to A/B test outcomes)?
  Probably yes for validation, default off in production.
- Sizing tier selection for value bets is deferred. When does it
  become important enough to add — after 6-max regression evidence,
  or only if Phase 7.5's sizing work doesn't already address it?
- Phase 8.2 scope: when do we add `fold_to_open` /
  `fold_to_3bet` counters to `OpponentTendencies`? They require
  new hand-level state in `MemoryManager` (track preflop opens by
  seat, attribute defending responses). Cost is moderate; payoff
  is making `steal_pressure` exit "tightness-proxy v1" status.

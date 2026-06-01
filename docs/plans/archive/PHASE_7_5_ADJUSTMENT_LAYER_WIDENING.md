---
purpose: Plan to widen the adjustment layer so the tiered bot realizes equity vs confirmed extreme opponents (maniacs)
type: design
created: 2026-05-13
last_updated: 2026-05-13T18:00:00
---

# Phase 7.5: Adjustment-layer widening

## Sequencing & cross-plan dependencies

This plan ships **last** in the opponent-modeling sequence and depends on
both predecessors landing first:

1. [Phase 6.6](PHASE_6_6_CBET_PLUS_CONFIDENCE.md) — HU c-bet +
   confidence-weighted offsets. **Required before 7.5.** 6.6 extends
   `AggregatedOpponentStats` (`fold_to_cbet`, `cbet_faced_count`) and
   `DecisionContext` (`is_flop_as_preflop_aggressor`,
   `active_opponent_count`); 7.5 stacks its fields on top.
2. [Phase 6.7a](PHASE_6_7_OPPONENT_SPOTS.md) — independent multiway opponent
   spots, **selection-correctness slice only**. **Required before 7.5.**
   6.7a ships `OpponentSpot`, `select_primary_aggressor()`, and
   `aggregate_from_spots()`, all of which 7.5 consumes. 6.7b (multiway
   c-bet enablement) is independent of 7.5 and can ship before or after.
3. **Phase 7.5 (this plan)** — three-tier exploitation clamp + bluff-catch
   override + opportunity-normalized stats. Requires 6.7a but NOT 6.7b.

Shared types this plan touches (incrementally, on top of 6.6 + 6.7):

- `AggregatedOpponentStats` in `poker/strategy/exploitation.py` —
  7.5 adds `all_in_per_facing_bet`, `facing_bet_opportunities`,
  `postflop_jam_open_rate`, `postflop_open_opportunities`,
  `aggression_factor_postflop` on top of 6.6's `fold_to_cbet`,
  `cbet_faced_count`.
- `DecisionContext` in `poker/strategy/exploitation.py` —
  7.5 adds `bet_size_pot_ratio` on top of 6.6's HU c-bet flags and 6.7's
  `facing_aggressor_name`.

Coordination notes:

- **`_pick_max_total_shift` lives in `tiered_bot_controller.py` (line 519
  today) and implements a two-tier clamp.** This plan's `_determine_clamp`
  in `exploitation.py` replaces it; add a Files-to-Modify entry for the
  controller deletion when implementing.
- **`compute_pattern_intensity()` (Phase 6.6) and `_determine_clamp()` (this
  plan) coexist at different pipeline layers** — intensity scales offsets;
  the clamp caps the L1 shift. They are not redundant. Step 0 instrumentation
  should confirm the interaction: at MEDIUM tier with AF in [4, 5], the
  hyper_aggressive intensity from 6.6 is still zero (its ramp starts at
  AF=5), so MEDIUM clamp permits movement that intensity gates to zero —
  expected behavior, but worth verifying.
- **`all_in_per_facing_bet` + `postflop_jam_open_rate` together replace
  `all_in_frequency` semantically.** Phase 6.6's hyper_aggressive ramp
  still reads `all_in_frequency` (per-hand). When this plan ships, the
  6.6 ramp migrates to **`max(all_in_per_facing_bet, postflop_jam_open_rate)`**
  — MAX, not AND — so first-in jammers and response jammers are each
  detected when their dominant expression crosses threshold:
  - `all_in_per_facing_bet` = response-aggression axis (jams when
    facing a bet)
  - `postflop_jam_open_rate` = open-aggression axis (first-in jams /
    overbet shoves into a no-bet pot)
  A maniac who first-in-jams every flop has
  `all_in_per_facing_bet = 0` because they never faced a bet — the
  open-rate axis catches them via MAX. Both signals also feed the
  signal-OR test in `_determine_clamp` (§Item 2) for tier
  classification. Step 0 calibration measures both distributions so
  the new thresholds are data-driven.
- **AF raw-count fallback cap** (lands with Item 2's commit, NOT Step 0 —
  see §"AF raw-count fallback edge case" for the rationale). Pins
  `aggression_factor` at `MEDIUM_AF_THRESHOLD` for opponents with zero
  calls observed. Phase 6.6's AF-axis intensity will then be flat for
  those opponents — the all-in axis still drives detection. Document
  this in 6.6's intensity ramp docstring. The new
  `aggression_factor_postflop` field (added in Step 0) has the cap from
  day one; only the legacy `aggression_factor` needed the deferral.
- **Item 3 (postflop opponent-awareness, deferred)** should consume Phase
  6.7's `OpponentSpot`/`select_primary_aggressor()` for the
  `bettor_archetype` axis rather than re-deriving aggressor identification.
  Diagnostics shipping in Step 0 should already log the spot's identified
  aggressor.
- **`aggregate_from_spots()` must learn 7.5's new fields.** 6.7a ships
  `aggregate_from_spots()` knowing only about the 6.6-extended
  `AggregatedOpponentStats`. When 7.5 lands its five new fields
  (`all_in_per_facing_bet`, `postflop_jam_open_rate`,
  `facing_bet_opportunities`, `postflop_open_opportunities`,
  `aggression_factor_postflop`), it must EXTEND `aggregate_from_spots()`
  in `poker/strategy/exploitation.py` to aggregate them too — otherwise
  the fallback path (when `select_primary_aggressor()` returns None) sees
  zero-valued new fields and the tier classifier silently misfires.
  The extension preserves the 6.7a aggregation semantics (60% rule on
  hand-level money committed) and adds field-by-field aggregation for
  the new fields using the same weighting policy. **This work is owned
  by 7.5 (not 6.7) and is listed in Files-to-Modify below.**
- **First-in vs response jam disambiguation depends on 6.7a tracking.**
  `_postflop_jam_opens` (the new 7.5 counter) increments only for
  first-in postflop jams, not response jams. To tell them apart, the
  recording code reads 6.7a's per-street `recent_aggressor_name`: if
  null at the moment of opponent's all-in action, it's a first-in jam
  (counter +1); if non-null and not the opponent themselves, it's a
  response jam (handled by `_all_ins_facing_bet` instead). 6.7a is a
  hard prerequisite for the open-jam counter to be correct.

## Codex review history

Plan reviewed by Codex twice on 2026-05-13. Revisions incorporated below.

### Round 1 revisions

- **Step 0 instrumentation** as prerequisite (was implicit)
- **Bluff-catch splits pot-odds-conditional**, not fixed
- **Three-tier exploitation ramp** (0.4 / 0.6 / 0.8), not binary
- **Opportunity-normalized stats** — `all_in_per_facing_bet` replaces
  per-hand denominator; AF raw-count fallback capped
- **Item 1 explicitly consumes Item 2's envelope**
- **bb/100 targets as directional bands**, not commitments
- **Item 3 partially un-deferred** — diagnostics ship now

### Round 2 revisions

- **Board-danger / street dampener on bluff-catch** — high call rates
  on dangerous rivers (four-flush, four-straight) are reckless; new
  texture multiplier suppresses bluff-catch call probability when
  board threatens hero's made-hand class
- **Confidence decay** — tier can ratchet DOWN when recent stats
  diverge from accumulated stats (handles opponent behavior shifts
  and recovery from early-sample noise)
- **Per-street bb/100 reporting** in validation — global bb/100 can
  hide river spew compensated by turn improvements
- **Counterfactual action logging** in Item 3 diagnostics — log the
  specific alternative action, not just "would diverge"
- **"Supersedes" reframed as "selects within envelope"** + test that
  override output doesn't exceed active tier's max adjustment
- **Externalize thresholds to config** — AF / all-in / sample
  thresholds live in a config file, not as Python constants, so
  Step 0 calibration can update them without code changes
- **Benchmark-bot prior** — known-extreme archetypes (`ManiacBot`,
  optionally others) can be classified extreme on first contact
  rather than waiting for the sample threshold (validation only —
  the prior is config-gated and off by default in production)
- **AF should be postflop-only** for the extreme-tier signal —
  preflop jammy behavior (e.g. short-stack 3-bet jams) pollutes the
  meaning when we're trying to read postflop bluff-frequency
- **`all_in_per_facing_bet` is the canonical name and denominator** —
  numerator is all-ins by opponent when facing a bet; denominator is
  facing-bet opportunities (not all decisions; not all hands). Earlier
  drafts used `all_in_per_decision`; that name is retired

## Context (read before starting)

Phase 7 shipped HU preflop charts that play approximately balanced
chart-vs-chart (Baseline at -35.9 bb/100 vs GTO-Lite — well within
mirror-match noise). See
[`docs/analysis/PHASE_7_HU_RESULTS.md`](../analysis/PHASE_7_HU_RESULTS.md)
for full numbers.

**But vs ManiacBot, every archetype regresses by 130-200 bb/100**
including Baseline (the pure chart with no personality distortion).
Same structural pattern appears in the GTO-Lite sweep: ManiacBot
beats GTO-Lite by +990 bb/100. Balanced players fold too often
against opponents who never fold themselves.

The chart isn't broken — the **adjustment layer** is too small. The
existing pipeline (`poker/tiered_bot_controller.py:140-269`) is:

```
chart → modify_strategy (personality)
      → apply_exploitation_offsets (Phase 6)
      → compute_value_override_strategy (Phase 6.5, strong hands only)
      → apply_short_stack_heuristics (Phase 6 Step B)
      → apply_pot_odds_floor
```

Today's behaviors that bleed bb/100 vs a confirmed maniac:

1. **`exploitation` is capped at L1 shift 0.4** — even when we *know*
   opponent is hyper-aggressive with high sample confidence, we only
   move our distribution ~40% toward the exploit response.

2. **`value_override` only fires for strong hands** — its
   `_OVERRIDE_TRIGGER_CLASSES = {nuts, strong_made, strong}`. Vs an
   extreme aggressor, **medium-strength hands also need bluff-catch
   behavior**.

3. **Postflop classifier is opponent-blind** — same node ("facing-bet
   on a wet flop") has very different EV depending on bettor archetype.

## Goal — definition of done

A working Phase 7.5 produces these observable outcomes:

1. **HU vs ManiacBot improves materially** for Baseline, TAG, LAG, Nit
   across 3 seeds. The improvement is reported as a directional band,
   not a target delta — Codex flagged the specific deltas as too
   prescriptive. Required: each archetype's bb/100 vs ManiacBot is
   meaningfully above its Phase 7 baseline (CIs don't overlap with the
   current baseline) across the seed average. Stretch: any improvement
   toward Phase 6.5 baselines is good, but we don't commit to specific
   gaps.

2. **HU vs GTO-Lite does not regress** for Baseline beyond -80 bb/100
   (currently -35.9). If our wider clamps over-fire on balanced
   opponents, we'll see it here first.

3. **6-max behavior unchanged**: existing 6-max-vs-rules gates pass.

4. **No regression in existing strategy tests** (currently 344
   passing).

5. **Adjustment-layer firing rates are observable and reasonable**:
   - Strong-hand override fires 5-15% of decisions overall
   - Bluff-catch override fires < 5% vs GTO-Lite, 15-30% vs ManiacBot
   - Extreme exploit tier fires < 5% vs GTO-Lite, ~60% vs ManiacBot
     (after sample threshold reached)

6. **Diagnostic spot log exists** even before Item 3 ships — logs
   spots where opponent is extreme AND hero has bluff-catcher AND
   facing large aggression, so we can cluster failed spots and decide
   whether Item 3 is worth the effort.

## Approach overview

**Codex-recommended order**: instrumentation first, then exploitation
clamp (the safer change), then the bigger override change, then
validate combined.

```
Step 0: Instrumentation + opportunity-norm stats + config loader
        (1.5 days, ships first, behavior-neutral within noise)
        — adds new fields and counters; legacy AF formula untouched.
        Confirm 6-max regression test passes after Step 0 lands
        (no archetype regresses > 5 bb/100). See "Step 0 behavior
        check" in Validation.
   ↓
Step 0.5: Run Phase-7-baseline sweep with instrumentation on
          to produce the calibration table. Sweep also produces
          the seed-matched Phase 7 baseline logs that Step 4
          validation will compare against. **Analysis only — runs
          the Step 0 code paths, produces data files, makes NO
          changes to running code or config.**
   ↓
        ← Human edits phase_7_5_config.yaml from calibration data ←
          (manual step, NOT runtime; happens between Step 0.5
          and Item 2 commit. Item 2's commit is what reads the
          updated config and changes behavior.)
   ↓
Item 2: Three-tier exploitation clamp + legacy AF raw-count cap
        (0.75 day) — uses calibrated thresholds; AF cap intentionally
        lands here, not in Step 0, so the (minor) behavior shift
        ships with the intentional Item 2 changes.
   ↓ (smoke-test against GTO-Lite to confirm no false fires)
Item 1: Bluff-catch override w/ pot-odds + board-danger dampener
        (1.5 days) — gated on Item 2's EXTREME tier
   ↓
Combined validation: 3 seeds × 2 opponents × per-street bb/100
   ↓
Decide on Item 3 based on residual leak measurement
```

The reordering matters: Item 2 is a smaller behavioral change that
exercises the same exploit pathway. If Item 2 alone over-fires on
balanced opponents, we'll learn that before committing to the bigger
Item 1 change. If Item 2 underwhelms vs ManiacBot, Item 1 picks up
the rest.

Step 0.5 is new in v3 per Codex round 2: thresholds must be
calibrated from real distributions before behavior changes ship, not
hardcoded guesses validated in-flight.

## Step 0: Instrumentation (prerequisite)

Before any behavior change, ship a per-decision diagnostic that
records WHICH adjustment fired and WHAT the input stats were. This
lets us:
- Confirm firing rates match expectations
- Cluster "spots where we did the wrong thing" for replay analysis
- Diagnose interactions between items 1, 2, and the existing pipeline

### Schema

Extend the existing `player_decision_analysis` table (or add sidecar
fields):

| Column | Type | Description |
|---|---|---|
| `strong_hand_override_fired` | bool | Existing override |
| `bluff_catch_override_fired` | bool | NEW (Item 1) |
| `exploit_clamp_tier` | str | 'none' / 'default' / 'medium' / 'extreme' (NEW Item 2) |
| `clamp_tier_winning_axis` | str | NEW: which signal axis triggered the tier — 'af_postflop' / 'all_in_per_facing_bet' / 'postflop_jam_open_rate' / 'benchmark_prior' / 'none'. When multiple axes cross threshold simultaneously, the highest-margin axis wins (deterministic tie-break). |
| `stats_source` | str | NEW: where the stats came from for this decision — 'per_aggressor' / 'aggregate' / 'none'. Distinct from `winning_axis`: an aggregate-fallback decision can still record which axis crossed within the aggregated stats. |
| `clamp_tier_ratcheted_down` | bool | NEW: did confidence decay reduce the tier this decision? |
| `opponent_af_at_decision` | float | Bettor's AF at decision time |
| `opponent_af_postflop` | float | NEW: AF computed from postflop actions only (excludes preflop jams) |
| `opponent_all_in_per_facing_bet` | float | NEW: all-ins / facing-bet opportunities (response-aggression axis) |
| `opponent_facing_bet_opportunities` | int | NEW: facing-bet sample count |
| `opponent_postflop_jam_open_rate` | float | NEW: open-jams / postflop **open opportunities** (no live bet, legal bet/all-in available) — open-aggression axis |
| `opponent_postflop_open_opportunities` | int | NEW: postflop sample count where opponent had a legal bet/all-in into a no-bet pot (not strictly "first to act") |
| `opponent_hands_observed` | int | Existing |
| `hand_strength_class` | str | nuts/strong_made/medium_made/weak_made/air/etc |
| `facing_bet_size_pot_ratio` | float | NEW: bet size / pot when applicable |
| `board_texture` | str | NEW: dry_high/wet_rainbow/four_flush/etc (for board-danger dampener) |
| `street` | str | NEW: flop/turn/river (for per-street reporting + dampener) |
| `street_chip_delta` | int | NEW: chip delta attributable to this street's decisions (for per-street bb/100 attribution) |
| `counterfactual_action` | str | NEW: what an opponent-aware lookup would have done (Item 3 diagnostic) |
| `counterfactual_action_rationale` | str | NEW: short label e.g. "vs maniac, weak_made calls instead of folds" |

### Stat-definition glossary (precise denominators)

These are the new fields added to `OpponentTendencies` in
`poker/memory/opponent_model.py`. **Each stat's denominator is
specified to avoid the ambiguity that motivated the round-2 review.**

| Stat | Numerator | Denominator |
|---|---|---|
| `all_in_per_facing_bet` | All-in raises by opponent when facing a bet | Times opponent faced a bet (had `fold/call/raise` available) — **response-aggression axis** |
| `postflop_jam_open_rate` | Postflop all-in opens (first-in jam, overbet shove into a no-bet pot) | Postflop spots where opponent had a legal bet/all-in available with NO live bet facing them and no prior bet on this street yet — **open-aggression axis** |
| `aggression_factor_postflop` | (bet + raise + all-in) on flop/turn/river | calls on flop/turn/river |
| `facing_bet_opportunities` | — | Count of opponent's decisions where `fold` was an option |
| `postflop_open_opportunities` | — | Count of opponent's postflop decisions where: (a) no live bet faces them yet on this street AND (b) a legal bet or all-in action is available. **Not literal "first-to-act"** — a player who checks behind another check still has this opportunity, because their decision could have been a bet. Captures the "opportunity to open aggression" semantics. |
| `betting_decisions_postflop` | — | Total postflop fold/check/call/raise/all-in by opponent |

Preflop all-ins and preflop AF still computed (used elsewhere), but
the **tier-classification logic uses postflop-only signals** to avoid
the pollution Codex flagged.

The two all-in axes (`all_in_per_facing_bet` and
`postflop_jam_open_rate`) measure different facets of the same trait.
Real maniacs may show up on either or both. The tier classification
treats them as alternatives — if either crosses its threshold, the
signal-OR test passes. A reviewer correctly flagged that a
response-only stat would miss first-in jammers; the two-axis design
fixes this.

### Confidence-decay / tier ratchet-down

Tier can DECREASE when recent stats diverge from accumulated stats —
the bot was briefly fooled by early aggression but later evidence
moderates the read. Implementation: keep a sliding-window stat
alongside the cumulative stat, and downgrade the tier if the recent
window doesn't support it.

**Update cadence**: the sliding-window counters are maintained
**incrementally per opponent event** (push the new event onto the
deque, pop the oldest if window is full). `_determine_clamp` is
called **per decision** and reads the current window state — no
recomputation cost at decision time. Concretely:

- When `OpponentTendencies.update_from_action(...)` is invoked,
  the same call appends to the sliding-window deque.
- When the deque exceeds `window_size`, the oldest entry is
  popped and its contribution subtracted from the window counters.
- `_determine_clamp` at decision time sees a consistent window
  snapshot — no async update concerns because all updates flow
  through `update_from_action` synchronously.

This means tier changes can happen mid-hand if the window crosses a
threshold during opponent action, which is intentional: if opponent
jams 3 times in 4 decisions, we want the tier to reflect that
quickly.

```python
@dataclass
class TierDecayParams:
    window_size: int = 50  # recent betting decisions
    # If recent window stats fail to support the current tier's
    # signal thresholds, ratchet down one tier.
    require_recent_window_full: int = 30  # min window samples before decay applies

# In _determine_clamp:
def _determine_clamp(
    stats: AggregatedOpponentStats,
    recent_stats: Optional[AggregatedOpponentStats] = None,
) -> Tuple[float, ClampTier]:
    """Pick clamp + tier with optional ratchet-down on stale signal."""
    base_tier = _classify_tier_cumulative(stats)
    if recent_stats and recent_stats.betting_decisions_postflop >= 30:
        recent_tier = _classify_tier_cumulative(recent_stats)
        # Cap base_tier at recent_tier — can't be more aggressive than
        # recent evidence supports
        return min(base_tier, recent_tier)
    return base_tier
```

This addresses the case where opponent jammed 4 times in the first 20
hands (early extreme classification) and then plays normally for 100
hands — the tier comes down once the recent window outweighs the
early sample.

### Benchmark-bot prior (validation use only)

To avoid burning hands in the warm-up phase during validation, an
optional config flag elevates known-extreme archetypes to the
EXTREME tier on first contact:

```python
# In poker/strategy/exploitation.py or new config file
CONFIRMED_EXTREME_ARCHETYPES = frozenset({
    'ManiacBot',   # synthetic test bot, by definition extreme
    # Add others only after empirical confirmation
})

# Default OFF in production — only enabled in benchmark sweeps via
# environment variable or experiment config flag.
PHASE_7_5_USE_BENCHMARK_PRIOR = os.environ.get(
    'POKER_USE_BENCHMARK_PRIOR', '0'
) == '1'
```

**Production stays evidence-based** — no priors on real opponents,
only on synthetic test fixtures. This is purely a validation
accelerator.

### Replay-spot taxonomy

A "diagnostic spot" is logged when ALL of:
- Opponent stats meet the EXTREME thresholds (defined below in Item 2)
- Hero has a non-strong made hand (medium_made or weak_made)
- Hero faces a bet (`fold` in available actions)

For each diagnostic spot, log the **counterfactual action** — not
just "current action would diverge from opponent-aware lookup," but
the SPECIFIC alternative action and a short rationale label. This
makes the diagnostic data directly translatable into implementation
rules for Item 3 later. Example:

```json
{
  "spot_id": "g123_h45_river_decision_2",
  "current_action": "fold",
  "counterfactual_action": "call",
  "counterfactual_rationale": "medium_made + extreme_aggressor + small_bet → bluff-catch",
  "hand_strength": "medium_made",
  "opponent_tier": "extreme",
  "bet_size_pot_ratio": 0.45,
  "board_texture": "wet_rainbow"
}
```

### Outputs

- New analysis script `experiments/analyze_adjustment_firings.py`:
  reads the trace, prints firing rates per opponent/archetype,
  per-street bb/100 attribution, emits a per-spot diagnostic file
  with counterfactual actions
- Validation gate: firing rate sanity (see Goal §5)
- Calibration table output: distribution of AF, all_in_per_facing_bet,
  facing_bet_opportunities for each opponent archetype, so threshold
  values in `phase_7_5_config.yaml` can be set from data rather than
  guessed

## Item 2: Three-tier exploitation clamp

### Today

`poker/strategy/exploitation.py:51`:

```python
DEFAULT_MAX_TOTAL_SHIFT = 0.4
```

L1 cap applied unconditionally in `apply_exploitation_offsets` (line
257). Means even with the strongest possible detection (AF=10,
all-in=0.50, 500 hands), total probability mass moved is capped at 0.4.

### Phase 7.5 change

Three tiers, keyed on **two-axis confidence**: sample size AND signal
strength. Both axes must reach a threshold for the next tier to fire.

**All thresholds live in `poker/strategy/data/phase_7_5_config.yaml`,
not as Python constants.** This lets Step 0 calibration update them
from real data without code changes.

```yaml
# poker/strategy/data/phase_7_5_config.yaml — initial placeholders.
# Final values set by Step 0 calibration; this file is the single
# source of truth that exploitation.py loads at import time.
exploitation_clamps:
  default_max_total_shift: 0.4
  medium_max_total_shift:  0.6
  extreme_max_total_shift: 0.8

sample_thresholds:
  # facing_bet_opportunities (NOT hands_observed, NOT all decisions)
  medium_min_opportunities:  60
  extreme_min_opportunities: 120

signal_thresholds:
  # AF computed from POSTFLOP actions only — preflop jams pollute
  # the postflop-bluff-frequency signal we're trying to read.
  medium_af_postflop:     4.0
  extreme_af_postflop:    6.0
  # Response-aggression axis: all-ins per facing-bet opportunity.
  medium_all_in_per_facing_bet:  0.15
  extreme_all_in_per_facing_bet: 0.30
  # Open-aggression axis: first-in jams / overbet shoves per
  # postflop open opportunity. Catches the first-in-jammer maniac
  # whose all_in_per_facing_bet is 0 (they never face a bet).
  medium_postflop_jam_open_rate:  0.10
  extreme_postflop_jam_open_rate: 0.20

tier_decay:
  # Recent-window-based ratchet-down. See Confidence-decay section.
  window_size: 50
  require_recent_window_full: 30

benchmark_prior:
  enabled: false   # production default; flipped on per-experiment
  confirmed_extreme_archetypes:
    - ManiacBot
```

Python constants are loaded from this file at import time. Tests can
override via a fixture that patches the loaded config.

New helper:

```python
def _determine_clamp(
    stats: AggregatedOpponentStats,
    recent_stats: Optional[AggregatedOpponentStats] = None,
    bettor_archetype: Optional[str] = None,
) -> Tuple[float, ClampTier]:
    """Pick the L1 clamp based on sample + signal confidence.

    Logic:
    1. Benchmark prior shortcut: if config.benchmark_prior.enabled
       and bettor_archetype is in confirmed_extreme_archetypes,
       return EXTREME tier immediately (validation accelerator).
    2. Otherwise classify cumulative stats:
       - Sample axis: facing_bet_opportunities meets threshold
         (REQUIRED — no signal without enough samples). Use
         postflop_open_opportunities as a fallback sample gate
         when the opponent is the type that mostly opens rather
         than faces bets.
       - Signal axis: ANY of these crosses its threshold qualifies
         the tier:
           a. af_postflop ≥ tier threshold
           b. all_in_per_facing_bet ≥ tier threshold (response-jamming)
           c. postflop_jam_open_rate ≥ tier threshold (open-jamming)
         Rationale: these three stats are different expressions of
         the same trait (willingness to put chips in without strong
         holdings). Real maniacs vary in HOW they express it — some
         raise constantly, some respond-jam, some first-in-jam.
         Sample is required; any signal axis qualifies.
       - For tier classification: pick the HIGHEST tier whose sample
         threshold is met AND whose any-signal test passes.
    3. If recent_stats supplied AND recent window has enough samples
       (≥ tier_decay.require_recent_window_full), cap the cumulative
       tier at the recent-window tier — so opponent behavior shifts
       are picked up.

    Returns (clamp_value, tier_enum, winning_axis) for instrumentation.
    The winning_axis is a string identifying which signal triggered
    the tier ('af_postflop', 'all_in_per_facing_bet',
    'postflop_jam_open_rate', 'benchmark_prior', or 'none' for
    DEFAULT tier). When multiple axes cross simultaneously, the
    highest-margin axis wins (deterministic tie-break: prefer the
    axis with the largest stat/threshold ratio).
    """
```

**AND vs OR rationale**: an earlier draft used AND on the AF and
all-in signal axes — a reviewer flagged that this would miss common
maniacs who raise constantly but rarely jam. Subsequent review also
flagged that the original `all_in_per_facing_bet` only captured
response-jamming, missing first-in jammers. The current design uses
THREE signal axes (af_postflop, all_in_per_facing_bet,
postflop_jam_open_rate), all combined via OR. Each is a different
expression of the same trait; any one crossing threshold qualifies
given sufficient sample. The sample axis is the only hard requirement
(no signal from 5 hands), and we accept either facing-bet samples
OR postflop-open samples for it (an opponent who only ever opens
should be classifiable by their open behavior).

### Opportunity-normalization (addresses Codex's biggest concern)

Today's `all_in_frequency = all_ins / hands_dealt` in
`poker/memory/opponent_model.py:149`. This is per-hand, not per
facing-bet opportunity. A player who jams 20% of hands they're dealt
but never sees a flop has 0.20 all-in-freq; a player who sees flops
60% of the time and jams every facing-bet decision has 0.20
all-in-freq too — but the signals are very different.

**Required Step 0 change** (in `poker/memory/opponent_model.py`):

```python
# New fields on OpponentTendencies (Step 0)
# Response-aggression counters:
_facing_bet_opportunities: int = 0   # incremented when opponent has fold-or-call decision
_all_ins_facing_bet: int = 0         # subset: when opponent's response is all-in

# Open-aggression counters:
_postflop_open_opportunities: int = 0  # incremented when opponent is first to act on flop/turn/river
_postflop_jam_opens: int = 0           # subset: when that first-to-act action is all-in (open jam)

# Derived stats — response axis: "prone to jamming when facing
# aggression"; open axis: "prone to first-in jamming when given the
# initiative." A real maniac shows up on either or both.
@property
def all_in_per_facing_bet(self) -> float:
    if self._facing_bet_opportunities == 0:
        return 0.0
    return self._all_ins_facing_bet / self._facing_bet_opportunities

@property
def postflop_jam_open_rate(self) -> float:
    if self._postflop_open_opportunities == 0:
        return 0.0
    return self._postflop_jam_opens / self._postflop_open_opportunities
```

Threading: `AggregatedOpponentStats` (in `exploitation.py:57`) gets
new `all_in_per_facing_bet`, `facing_bet_opportunities`,
`postflop_jam_open_rate`, and `postflop_open_opportunities` fields;
manager aggregator computes them weighted same as existing stats.
Existing `all_in_frequency` (per-hand) is kept for backward compat —
Phase 6.6's hyper_aggressive ramp still reads it. When Phase 7.5
ships, the 6.6 ramp migrates to a **combined-via-MAX** signal:
`combined_all_in_rate = max(all_in_per_facing_bet, postflop_jam_open_rate)`.

MAX, not AND: these two stats measure different expressions of the
same trait (response-jamming vs open-jamming). A response-jammer with
`all_in_per_facing_bet=0.4` and `postflop_jam_open_rate=0` is just as
extreme as an open-jammer with the reverse profile — AND would
under-detect both kinds. MAX promotes whichever expression is
strongest. This is consistent with the OR-semantics in
`_determine_clamp` (each axis qualifies the tier independently); the
6.6 ramp gets a single scalar to threshold against, hence MAX rather
than literal disjunction. (See Sequencing notes at top of file.)

### AF raw-count fallback edge case — moved to Item 2 (was Step 0)

Today: when `_call_count == 0` and `_bet_raise_count > 0`, AF falls
back to `_bet_raise_count` (raw count, not a ratio). This breaks the
"AF > N is extreme" semantic — a player with 6 raises and 0 calls in
10 hands gets AF=6, indistinguishable from a player with 60 raises
and 10 calls (a real maniac).

**A reviewer correctly flagged: capping this fallback in Step 0
would change behavior immediately**, because
`classify_detected_patterns()` (used by today's exploitation and
strong-hand value override) consumes the field. To preserve Step 0's
"no behavior change" property, the cap is **moved to Item 2's ship**.

Step 0 ADDS the new postflop-only AF stat
(`aggression_factor_postflop`) but leaves the existing
`aggression_factor` formula untouched. Item 2's commit cuts over to
the new postflop AF for tier classification AND applies the
raw-count cap to the legacy `aggression_factor` in the same change,
so any behavior shift from the cap lands together with the
intentional new behavior from Items 1+2.

Implementation in Item 2 commit:

```python
# In OpponentTendencies._recalculate_stats — landed with Item 2
if self._call_count == 0:
    if self._bet_raise_count == 0:
        self.aggression_factor = 1.0
    else:
        # No calls observed — cap AF at the medium threshold rather
        # than letting raw count drive an extreme classification.
        self.aggression_factor = min(
            float(self._bet_raise_count),
            MEDIUM_AF_THRESHOLD,
        )
else:
    self.aggression_factor = self._bet_raise_count / self._call_count
```

The new `aggression_factor_postflop` field (Step 0) has the same cap
logic from day one — there's no legacy consumer to protect, so no
deferral needed.

### Why three tiers instead of binary

Codex flagged 0.4 → 0.8 as too large a single-step permission change.
0.6 mid-tier gives us a soft landing — if the bot starts mis-firing
behavior, we see it at MEDIUM before going to EXTREME, and we can
adjust the thresholds for the higher tier without losing all the
adjustment.

## Item 1: Bluff-catch override with pot-odds conditioning

### Today

`poker/strategy/value_override.py:107` gates on
`hand_strength not in _OVERRIDE_TRIGGER_CLASSES`. Trigger classes are
`{nuts, strong_made, strong}`. Medium and weak hands never reach the
override path; they always go through exploitation offsets only.

### Phase 7.5 change

Add a fourth trigger regime: `medium_made` and `weak_made` when
`facing_bet` AND opponent is at the EXTREME tier (defined by Item 2 —
same thresholds). The override REWRITES strategy to "bluff-catch"
according to pot-odds.

```python
BLUFF_CATCH_TRIGGER_CLASSES = frozenset({
    HandStrengthClass.MEDIUM_MADE.value,
    HandStrengthClass.WEAK_MADE.value,
})
```

### Pot-odds-conditional splits with board-danger dampener

Instead of fixed 80/20-50/50, the call probability is computed from
the bet size as a fraction of pot, **then dampened by board texture
and street**. Codex flagged "80% call vs pot-size bet on a river
four-flush board" as reckless — the dampener fixes it.

```python
def _bluff_catch_call_probability(
    hand_strength: str,
    bet_size_pot_ratio: float,   # bet / pot_before_bet
    street: str,                 # 'flop' | 'turn' | 'river'
    board_texture: str,          # postflop classifier texture bucket
) -> float:
    """Compute call probability conditioned on pot-odds, hand class,
    street, and board texture.

    Pot-odds for calling: required_equity = bet / (pot + 2*bet)
    where `pot` is the pot BEFORE opponent's bet.
      - 1/3 pot bet → need 20% equity (0.33 / 1.66)
      - 1/2 pot bet → need 25% equity (0.50 / 2.00)
      - pot-size bet → need 33% equity (1.0 / 3.0)
      - 2x pot bet  → need 40% equity (2.0 / 5.0)
      - jam (assume 3x pot) → need ~43% equity (3.0 / 7.0)

    Equity estimates (rough — see note below):
      - medium_made vs wide c-bet range: ~55%
      - weak_made vs wide c-bet range:   ~35%

    The split approaches 100% call when our equity well exceeds
    required (small bets, safe boards) and approaches fold for large
    bets on dangerous boards.

    Note on equity numbers: 55% / 35% are coarse poker-theory
    averages and likely OVERSTATE equity in many real spots (vs an
    aggressor's actual range that includes some value). The function
    is BEHAVIORAL — it's saying "stop overfolding versus confirmed
    over-aggression in spots where our bluff-catcher likely has
    equity" — not a literal equity calculation. Board-danger dampener
    is the safety net for spots where the equity assumption breaks
    down. Step 0 instrumentation collects showdown data that can
    refine these numbers in a future calibration pass.

    Sanity check using corrected pot-odds: weak_made at 35% equity
    facing a 0.67-pot bet (28% required) is only marginally +EV at
    raw odds, so the base call rate of 0.40 is the right order of
    magnitude — the dampener can push it lower on dangerous boards.
    medium_made at 55% equity facing a 2x-pot bet (40% required) is
    still positive at raw odds, so 0.50 base × dampener is defensible.
    """
    # Base call probability from pot-odds × hand class
    base = _base_call_prob(hand_strength, bet_size_pot_ratio)
    # Dampener for dangerous boards / late streets
    dampener = _board_danger_dampener(street, board_texture, hand_strength)
    return base * dampener


def _base_call_prob(hand_strength: str, bet_size_pot_ratio: float) -> float:
    if hand_strength == HandStrengthClass.MEDIUM_MADE.value:
        if bet_size_pot_ratio <= 0.50:
            return 0.95         # small bet, easy bluff-catch
        elif bet_size_pot_ratio <= 1.00:
            return 0.80         # pot-size, still positive
        elif bet_size_pot_ratio <= 2.00:
            return 0.50         # large bet, marginal
        else:
            return 0.20         # huge bet / jam, mostly fold
    elif hand_strength == HandStrengthClass.WEAK_MADE.value:
        if bet_size_pot_ratio <= 0.33:
            return 0.70
        elif bet_size_pot_ratio <= 0.67:
            return 0.40
        else:
            return 0.10
    return 0.0


def _board_danger_dampener(
    street: str, board_texture: str, hand_strength: str,
) -> float:
    """Return a multiplier in [0.0, 1.0] applied to the base call prob.

    The dampener tightens our bluff-catch when:
    - It's the river (no more cards; equity is realized)
    - Board threatens to outrun a medium-made hand (four-flush,
      four-straight, paired board where weak_made is dominated)
    - Hand class can't beat obvious value (weak_made on a paired board)

    Returned multiplier values are CONSERVATIVE — better to fold a
    bluff-catch in a marginal spot than to bleed chips on the river.
    """
    dampener = 1.0

    # Street dampener — river is harshest because there's no equity
    # to realize and bluff-catch becomes a pure showdown decision.
    if street == 'river':
        dampener *= 0.6
    elif street == 'turn':
        dampener *= 0.9

    # Texture dampener — boards that "complete" obvious draws
    # threaten our made-hand class. Texture buckets come from
    # postflop_classifier.classify_texture_bucket.
    dangerous_textures = {
        'four_flush',       # opponent's flush draw got there
        'four_straight',    # opponent's straight draw got there
        'paired_high',      # weak_made (low pair) is dominated
        'monotone',         # already-completed flush
    }
    if board_texture in dangerous_textures:
        dampener *= 0.5

    # weak_made on a paired board is structurally dominated — cap.
    if (hand_strength == HandStrengthClass.WEAK_MADE.value
        and 'paired' in board_texture):
        dampener *= 0.5

    return dampener
```

**Behavioral envelope after dampening:**

| Spot | Base | Dampener | Final |
|---|---|---|---|
| medium_made, 0.5 pot bet, flop, dry | 0.95 | 1.0 | 0.95 |
| medium_made, 1.0 pot bet, turn, wet rainbow | 0.80 | 0.9 | 0.72 |
| medium_made, 1.0 pot bet, river, four-flush | 0.80 | 0.6 × 0.5 = 0.30 | 0.24 |
| medium_made, 2.0 pot bet, river, monotone | 0.50 | 0.6 × 0.5 = 0.30 | 0.15 |
| weak_made, 0.33 pot bet, flop, dry | 0.70 | 1.0 | 0.70 |
| weak_made, 0.67 pot bet, river, paired | 0.40 | 0.6 × 0.5 × 0.5 = 0.15 | 0.06 |

The river+four-flush case (which Codex flagged) drops from "80% call"
to "24% call" after dampening — much safer.

Bet-size is read from `DecisionContext` — needs a new field
`bet_size_pot_ratio: float`. Street and board_texture come from the
postflop node already in scope at the override decision point.

### Sample threshold

Bluff-catch fires only when **Item 2's EXTREME tier also fires**. The
sample gate is satisfied by **whichever axis is the winning_axis for
that opponent's tier classification**, not specifically by
`facing_bet_opportunities`:

- If `winning_axis == 'af_postflop'` or `'all_in_per_facing_bet'`:
  gate satisfied by `facing_bet_opportunities ≥ EXTREME_MIN_OPPORTUNITIES`
  (the sample that informs both AF_postflop and response-jam stats).
- If `winning_axis == 'postflop_jam_open_rate'`: gate satisfied by
  `postflop_open_opportunities ≥ EXTREME_MIN_OPPORTUNITIES` (the
  sample for the first-in jam axis). An opponent who first-in-jams
  every flop but has never faced a bet can still reach EXTREME via
  this axis — their facing-bet sample is zero, but their open-jam
  sample is non-zero.
- If `winning_axis == 'benchmark_prior'`: no sample gate (the prior
  itself is the assertion that sample isn't required).

Codex flagged the original 100-hand threshold as too loose for
postflop-specific signals — the per-axis sample gate fixes this while
also unblocking first-in jammers from being trapped by a non-applicable
facing-bet sample requirement.

### Per-aggressor stats threading (consumes 6.7a)

The tier classification for the bluff-catch gate must read **the
specific aggressor's stats**, not the aggregate `AggregatedOpponentStats`.
In a 3-way pot vs a maniac + a passive caller, the aggregate dilutes
the maniac's signal and the gate fails to fire when it should — or,
worse in the other direction, the aggregate could overstate maniac
behavior when the passive caller has been quiet but is still in the
hand.

Threading at the call site (in `tiered_bot_controller.py`):

```python
# At the bluff-catch decision point (postflop, facing a bet)
spots = build_opponent_spots(game_state, hero_idx)   # 6.7a helper
aggressor_spot = select_primary_aggressor(            # 6.7a helper
    spots,
    highest_current_bet=game_state.highest_bet,
    recent_aggressor_name=mm.recent_aggressor_name,
)

if aggressor_spot is None:
    # Ambiguous spot per 6.7a rule 3 (tied bets, no aggressor flag,
    # no recent aggressor name). Fall back to aggregate stats for
    # the tier check — same compatibility path other unmigrated
    # rules use. Bluff-catch is allowed to fire from aggregate
    # stats but with stricter gating (see "Aggregate fallback" below).
    stats_for_tier = aggregate_from_spots(spots)
    aggressor_name = None
else:
    stats_for_tier = aggressor_spot.stats
    aggressor_name = aggressor_spot.name

clamp, tier, winning_axis = _determine_clamp(
    stats=stats_for_tier,
    recent_stats=..., bettor_archetype=aggressor_name,
)
```

**Aggregate fallback for bluff-catch**: when `select_primary_aggressor`
returns None, the aggregate path is allowed but with one additional
gate — **all continuing opponents** must have at least MEDIUM-tier
sample counts. Otherwise the override doesn't fire (back to table
strategy). Rationale: when we can't identify the specific aggressor,
the safety net is "every continuing opponent has enough sample for
us to be confident the aggregate read isn't being driven by a single
noisy player." Diagnostic logs `stats_source = 'aggregate'` in this
branch. `clamp_tier_winning_axis` still records the signal axis that
crossed (within the aggregated stats), so the diagnostic captures
both "which stats" and "which signal."

### Multiway pot suppression

Phase 6.7 multiway c-bet plan suppresses bluffs when any continuing
opponent is a station / unknown / all-in. Bluff-catch deserves the
same kind of suppression: calling down with a marginal made hand in
a multiway pot is structurally worse than HU because a passive
caller likely has showdown value that beats our weak/medium pair.

**Multiway suppression rule for bluff-catch:**

```python
def should_apply_bluff_catch_override(
    spots, hand_strength, decision_context, ..., aggressor_spot,
):
    # ... existing gates (tier, hand class, facing_bet, tilt) ...

    # Phase 7.5 multiway suppressor — counts continuing opponents
    # (active, not folded, not the aggressor themselves).
    continuing = [
        s for s in spots
        if s.is_active and not s.is_folded
        and (aggressor_spot is None or s.name != aggressor_spot.name)
    ]

    # Heads-up (zero continuing opponents besides the aggressor):
    # bluff-catch is allowed at full pot-odds-table strength.
    if len(continuing) == 0:
        return True  # HU path — original logic applies

    # Multiway with at least one continuing opponent:
    # Bluff-catch suppressed if ANY continuing opponent is:
    #   (a) all-in (can't fold, our equity calc was vs aggressor only)
    #   (b) a station (high VPIP, low fold-frequency) — likely has
    #       showdown value, dominates our weak_made/medium_made
    #   (c) tight/unknown (insufficient sample) — could be a slowplay
    for opp in continuing:
        if opp.is_all_in:
            return False
        if _is_station(opp.stats):
            return False
        if opp.stats.facing_bet_opportunities < MEDIUM_MIN_OPPORTUNITIES:
            return False  # low-sample opponent in the pot — too risky
    return True


def _is_station(stats: AggregatedOpponentStats) -> bool:
    """Detect call-station tendencies that dominate our bluff-catch."""
    return (
        stats.vpip > 0.55
        and stats.aggression_factor < 1.5
        and stats.hands_observed >= MEDIUM_MIN_OPPORTUNITIES
    )
```

**Why this rule shape (matches 6.7's c-bet suppression):** symmetric
suppression on both sides of the maniac. When we bluff-catch the
maniac, the passive caller's range plays the same role as a
"continuing opponent" in 6.7's c-bet logic — they cap our equity by
having showdown value we can't beat.

The multiway suppression only blocks the OVERRIDE — the underlying
strategy table still runs, so hero defaults to whatever the chart says
for `medium_made` / `weak_made` facing a bet (typically fold-heavy at
those classifications). We don't FORCE folds in multiway; we just
don't OVERRIDE folds with calls.

### Envelope semantics — bluff-catch selects within the tier-expanded envelope

Codex's round-2 reframe: "supersedes" makes it sound like value_
override erases everything exploitation did. The accurate framing is
that **value_override selects the final action within the clamp-
expanded policy envelope** — exploitation widened the space of
permissible distribution shapes (via the higher clamp tier), and
value_override picks one specific shape from that space.

Concretely:
- The EXTREME-tier clamp authorizes a maximum L1 shift of 0.8 from
  the table baseline.
- When bluff-catch fires, it writes a specific distribution
  (e.g. 80% call / 20% fold) for the current decision.
- The bluff-catch distribution **must satisfy the active tier's
  clamp** — its L1 distance from the table baseline can't exceed
  EXTREME_MAX_TOTAL_SHIFT (= 0.8).

Why this matters: a pathological case is if bluff-catch wrote a
distribution that exceeded the active envelope, the bot would have
"more permission" via the override than the exploit tier intended.
Empirically the override distributions (0.8/0.2, 0.5/0.5) fit
comfortably within an 0.8 L1 envelope, but the constraint should be
enforced in code, not just in writing.

**Implementation:**

```python
# In compute_bluff_catch_strategy:
proposed = StrategyProfile(action_probabilities={'call': call_prob, 'fold': 1 - call_prob})
# Enforce tier envelope: the proposed distribution must not exceed
# the active clamp's L1 distance from the table baseline.
clamped = _clamp_to_envelope(proposed, baseline, max_total_shift)
return clamped
```

**Test:** `test_bluff_catch_override.py` adds a test that with a
DEFAULT-tier clamp (= 0.4), a "100% call" bluff-catch on a hand whose
baseline is "100% fold" gets clamped back to ≤ 0.4 L1 distance from
baseline (i.e. ~70% fold / 30% call), not the full override
distribution. With EXTREME-tier clamp (= 0.8), the same override
fits comfortably.

In the normal Phase 7.5 flow (bluff-catch only fires when EXTREME
tier is active), this clamp is never the binding constraint. The
test exists to defend against future config changes that might lower
the EXTREME clamp without realizing the override needs the headroom.

### Mutual exclusivity with strong-hand override

A hand class can't be both `strong_made` and `medium_made`, so the
two overrides are guaranteed mutually exclusive. Controller wires
them sequentially — strong-hand override first, then bluff-catch.
First one to fire wins.

## Item 3: Postflop classifier opponent-awareness (deferred implementation, diagnostics ship)

### Implementation: deferred

The chart-level change (adding `bettor_archetype` to `PostflopNode`
and authoring per-archetype entries) is deferred behind validation of
Items 1+2.

### Diagnostics: ship in Step 0

Per Codex's note, even with implementation deferred, we instrument:

- For each postflop decision, log the bettor's archetype as
  classified by Item 2's tiering (none/default/medium/extreme).
- For each "diagnostic spot" (extreme opponent + bluff-catcher hand +
  facing bet), log:
  - What the controller decided
  - The **counterfactual action** an opponent-aware lookup would
    have made (specific action label, not "would diverge")
  - A short rationale string suitable for clustering

The diagnostic doesn't need a separate strategy table yet — just a
small heuristic function `counterfactual_opponent_aware_action(node,
opponent_tier) → action` that encodes what we'd do if the postflop
chart had opponent-aware entries. Output goes to the
`counterfactual_action` and `counterfactual_action_rationale` columns
in the per-decision capture (see Step 0 schema).

This converts "Item 3 might be needed" into a measurable claim: after
Items 1+2 ship, we can count how often controller_action diverges
from counterfactual_action AND opponent_tier=extreme. If divergence
is rare (< 5% of extreme-tier decisions), Item 3 isn't worth pursuing.
If common (> 20%), Item 3 ships next.

**Decision rule for Item 3 implementation (post-validation):**
- Residual leak vs ManiacBot < 30 bb/100: Item 3 not worth complexity
- Residual leak 30-50 bb/100: revisit, consider lower-cost
  alternatives (just adjust postflop classifier probabilities, no
  new chart axis)
- Residual leak > 50 bb/100: do Item 3

## Tests

### Unit tests

`tests/test_strategy/test_opportunity_normalized_stats.py` (Step 0)
- `all_in_per_facing_bet` correctly computed from
  `_all_ins_facing_bet / _facing_bet_opportunities`
- `postflop_jam_open_rate` correctly computed from
  `_postflop_jam_opens / _postflop_open_opportunities`
- `_postflop_jam_opens` increments only when opponent's first-to-act
  postflop decision is all-in; does NOT count response-jams (those
  go into `_all_ins_facing_bet`)
- **Preflop all-ins excluded from postflop counters**: opponent
  jams preflop → `_postflop_jam_opens` stays at 0,
  `_postflop_open_opportunities` stays at 0. Only postflop
  streets (flop / turn / river) advance the postflop counters.
- `_postflop_open_opportunities` increments whenever opponent has
  a postflop decision with NO live bet facing them on this street
  AND a legal bet/all-in available — not strictly "first to act."
  Includes check-behind scenarios (e.g. BTN checking behind a BB
  check on the flop) where opponent could have bet but chose not to.
  Excludes any decision where opponent already faces a bet.
  (check/bet/all-in into a no-bet pot all count as opportunities;
  only opening jams increment the numerator)
- **Per-opponent isolation**: stats updates on opponent A don't
  leak into opponent B's `OpponentTendencies` (each opponent has
  their own counter struct; aggregator weights per-opponent
  contributions but doesn't merge counters across opponents)
- **Missing-field tolerance**: when `OpponentTendencies` is
  deserialized from an older record that predates Phase 7.5 and
  lacks the new counter fields, the new fields default to 0 and
  derived properties return 0.0 — no crash, no NaN. (No formal
  migration path needed; old records just lose their accumulated
  history for the new axes, which is the intended behavior since
  the data wasn't captured before.)
- `aggression_factor_postflop` excludes preflop bet/raise/all-in
  counts (computed from postflop-only counters); has the AF
  raw-count cap from day one (this field is new, no legacy consumer)
- Backward compat: per-hand `all_in_frequency` still produced;
  legacy `aggression_factor` formula UNCHANGED in Step 0
- Sliding-window counters reset and accumulate correctly across
  windowed boundaries

`tests/test_strategy/test_legacy_af_cap.py` (Item 2 — NOT Step 0)
- Legacy `aggression_factor` raw-count fallback caps at
  `MEDIUM_AF_THRESHOLD` when call_count is 0, no longer reports raw
  count as ratio. (This test ships with Item 2 because that's where
  the cap actually lands.)
- **Intended behavior shift**: for an opponent with 10 raises and 0
  calls (pre-cap AF = 10.0, post-cap AF = MEDIUM_AF_THRESHOLD = 4.0),
  `classify_detected_patterns` DROPS `'hyper_aggressive'` from the
  result (since 4.0 < HYPER_AGG_AF_THRESHOLD = 5.0). The
  `'hyper_aggressive'` pattern in this case is reinstated only if
  the opponent's `all_in_frequency` independently crosses
  HYPER_AGG_ALL_IN_FREQ_THRESHOLD (= 0.30) — i.e. the OR-form of the
  hyper_aggressive detector. This is INTENDED — the cap is the
  whole point — and downstream impact (Phase 6.5 value override may
  not fire as often on these specific edge-case opponents) is
  acceptable because no-calls / many-raises is the exact noisy-
  signal case we wanted to suppress.
- `'hyper_aggressive'` still fires for opponents with non-zero
  call_count + AF ≥ 5.0 — the cap only affects the call_count=0
  edge case.

`tests/test_strategy/test_exploitation_three_tier_clamp.py` (Item 2)
- `_determine_clamp` returns DEFAULT when sample below MEDIUM threshold
- Returns MEDIUM when sample ≥ MEDIUM threshold AND (AF_postflop ≥
  MEDIUM OR all_in_per_facing_bet ≥ MEDIUM OR
  postflop_jam_open_rate ≥ MEDIUM) — sample required, any signal
  axis qualifies
- Returns EXTREME when sample ≥ EXTREME threshold AND any one of the
  three signal axes ≥ EXTREME
- Returns DEFAULT when sample insufficient even if signals are strong
- **High-AF-only maniac**: sample ≥ EXTREME, AF_postflop = 8.0,
  all_in_per_facing_bet = 0.05, postflop_jam_open_rate = 0.02 →
  returns EXTREME, winning_axis = 'af_postflop' (signal-OR catches
  this case; winning_axis identifies which signal triggered)
- **High-response-jam-only opponent**: sample ≥ EXTREME,
  AF_postflop = 2.0, all_in_per_facing_bet = 0.45,
  postflop_jam_open_rate = 0.02 → returns EXTREME,
  winning_axis = 'all_in_per_facing_bet'
- **First-in-jammer-only opponent** (the previously missed case):
  facing_bet_opportunities below threshold BUT postflop_open_
  opportunities ≥ EXTREME, AF_postflop = 2.5,
  all_in_per_facing_bet = 0.0 (never faced a bet),
  postflop_jam_open_rate = 0.30 → returns EXTREME via the
  open-jam axis and the open-opportunities sample fallback,
  winning_axis = 'postflop_jam_open_rate'
- **Tie-break determinism**: AF_postflop = 7.0 (EXTREME margin
  1.17×), all_in_per_facing_bet = 0.45 (EXTREME margin 1.5×),
  postflop_jam_open_rate = 0.40 (EXTREME margin 2.0×) → returns
  EXTREME, winning_axis = 'postflop_jam_open_rate' (largest
  stat/threshold ratio wins tie-break deterministically)
- **Tier decay (round 2)**: recent-window tier caps cumulative tier —
  setup with cumulative=EXTREME and recent=DEFAULT → returns DEFAULT
- **Tier decay via postflop_jam_open_rate cool-down**: cumulative
  jam_open_rate = 0.35 (EXTREME, from many early jams), recent
  window jam_open_rate = 0.05 (opponent stopped jamming) →
  `_determine_clamp` returns DEFAULT, not EXTREME. The winning-axis
  field records 'none' since no signal supports the current tier.
- **Benchmark prior (round 2)**: when config enables prior and bettor
  archetype is ManiacBot, returns EXTREME on first decision (no
  sample requirement)
- **Benchmark prior off**: same input but config flag false →
  returns DEFAULT (waiting for samples)
- `apply_exploitation_offsets` with each tier produces progressively
  wider distributions

`tests/test_strategy/test_bluff_catch_override.py` (Item 1)
- `_base_call_prob` returns the expected value across the bet-size /
  hand-class matrix (parameterized table test)
- **`_board_danger_dampener` (round 2)**: returns 1.0 on flop+dry,
  0.6 on river+rainbow, 0.3 on river+four_flush, 0.15 on weak_made+
  paired_high
- `_bluff_catch_call_probability` correctly composes base × dampener
- **Envelope clamp (round 2)**: with `max_total_shift=0.4`, a
  bluff-catch that would write 100% call (from 100% fold baseline)
  gets clamped to ≤ 0.4 L1 distance from baseline
- **Envelope clamp at EXTREME tier**: with `max_total_shift=0.8`,
  same override fits without clamping
- `should_apply_bluff_catch_override` returns True only when ALL
  gates pass (medium/weak_made + facing_bet + EXTREME tier +
  adaptation bias gate)
- Returns False for: strong hands (use existing path), weak draw,
  open spot, balanced opponent, MEDIUM-tier opponent, low sample
- **Multiway suppression — all-in opponent**: aggressor is EXTREME,
  hero has medium_made, one continuing opponent is all-in →
  override returns False (can't fold an all-in stack)
- **Multiway suppression — station**: aggressor is EXTREME, hero has
  medium_made, one continuing opponent has VPIP=0.70 / AF=1.0 /
  hands_observed=150 → `_is_station` returns True → override
  returns False
- **Multiway suppression — low-sample**: aggressor is EXTREME, one
  continuing opponent has facing_bet_opportunities=20 → override
  returns False (insufficient read on third party)
- **Multiway allow**: aggressor is EXTREME, all continuing opponents
  are above sample threshold AND not stations AND not all-in →
  override returns True
- **HU path unchanged**: aggressor is EXTREME, zero continuing
  opponents (HU pot) → multiway suppression skipped → override
  returns True under existing logic

`tests/test_strategy/test_bluff_catch_per_aggressor.py` (Item 1)
- `select_primary_aggressor` returns a specific spot → tier
  classification uses that spot's stats, NOT the aggregate
- `select_primary_aggressor` returns None (ambiguous spot) → falls
  back to `aggregate_from_spots()` for tier classification, BUT
  bluff-catch requires all continuing opponents to be at MEDIUM
  sample threshold or higher (stricter than the per-aggressor path)
- Diagnostic captures `stats_source = 'aggregate'` when the
  fallback path fires; `clamp_tier_winning_axis` still records the
  signal axis (e.g. `'af_postflop'`) within the aggregated stats —
  the two fields are independent dimensions

`tests/test_strategy/test_aggregate_from_spots_phase75_fields.py` (Step 0)
- Aggregating spots with the 60%-dominant opponent rule returns the
  dominant opponent's values verbatim for `all_in_per_facing_bet`,
  `postflop_jam_open_rate`, and `aggression_factor_postflop`
- Opportunity-count fields (`facing_bet_opportunities`,
  `postflop_open_opportunities`) use **MIN across active spots** in
  the non-60%-rule path, consistent with 6.7a's treatment of
  `hands_observed` and `cbet_faced_count`. Rationale: limiting
  sample is the bottleneck for confidence.
- Float rate fields (`all_in_per_facing_bet`, `postflop_jam_open_rate`,
  `aggression_factor_postflop`) use **equal-weight average across
  active spots** in the non-60%-rule path, matching the 6.7a
  aggregator's policy for `vpip`/`pfr`/`aggression_factor`/`fold_to_cbet`.
  This is intentionally consistent with legacy — NOT sample-weighted.
  Step 0 calibration data will show if sample-weighting produces
  better signal; if so, that's a 7.5 follow-up, not part of v1.
- Empty spots list → all new fields default to 0.0 / 0

`tests/test_memory/test_postflop_jam_open_counter.py` (Step 0)
- Opponent jams as first-to-act on flop (recent_aggressor_name is
  None at jam time) → `_postflop_jam_opens` += 1,
  `_all_ins_facing_bet` unchanged
- Opponent jams in response to a bet (recent_aggressor_name is the
  bettor at jam time) → `_all_ins_facing_bet` += 1,
  `_postflop_jam_opens` unchanged
- Opponent jams preflop → neither counter increments (postflop-only
  scope)
- Street transition resets recent_aggressor_name correctly per 6.7a
  contract; verify a flop-first-in-jam is detected as first-in even
  if there was a preflop raiser

### Integration tests

`tests/test_strategy/test_tiered_bot_bluff_catch.py`
- ManiacBot-pattern stats + medium_made flop + facing-1/3-pot dry
  board → controller decides `call` with high probability (≥ 80%)
- Same stats + facing-pot-size + river + four-flush board →
  controller folds more often (dampener pulls call rate down to ~24%)
- Same stats + facing-2x-pot → controller folds more often (~50/50)
- Same stats + strong_made → controller still hits strong-hand
  override (no regression)
- GTO-Lite-pattern stats (AF~2, no all-ins) + medium_made →
  controller does NOT fire bluff-catch (sample below thresholds)
- **Per-street attribution (round 2)**: end-to-end test verifies
  per-decision capture records the street and street_chip_delta so
  validation analysis can group bb/100 per street
- **Multiway scenario (this revision)**: 3-handed flop, hero has
  medium_made facing a bet from ManiacBot, one passive opponent
  (VPIP=0.65, AF=0.9) called the bet → controller folds (multiway
  station suppressor blocks the override; underlying chart says
  fold) rather than calling. Same scenario HU (no passive caller)
  → controller calls
- **Per-aggressor tier (this revision)**: 3-handed flop, hero faces
  a bet from a NIT (AF_postflop=0.8) but a maniac is in the pot
  behind — `select_primary_aggressor` returns the NIT (they made
  the bet), tier classification uses NIT stats, override does NOT
  fire (NIT isn't EXTREME). Aggregate stats would have looked
  EXTREME from the maniac contribution; per-aggressor reading
  prevents the false fire.

### Existing test regression

All 344 strategy tests + 27 HU chart/routing tests pass unchanged.

## Validation (combined, not isolated)

Per Codex: validate Items 1+2 together, not separately. The signals
are entangled.

### Primary sweep: HU vs ManiacBot

**The matched-seed gate requires baseline AND candidate runs at the
same seeds.** Only seed 42 was captured during Phase 7 validation;
seeds 142 and 242 are missing. Step 0.5 (the calibration sweep)
should be configured to use the same seed set, providing the
matched-seed Phase 7 baseline as a side effect.

**What matched seeds DO and DON'T guarantee** (clarifying limitation):

- ✓ Same initial deck shuffle per hand (`create_deck(random_seed=seed)`)
- ✓ Same dealer rotation across hands (`dealer_idx = hand_num % 2`
  is deterministic given `--hands`)
- ✓ Same seat order (player names array is fixed in
  `simulate_bb100.run_matchup`)
- ✗ NOT the same trajectory through the hand. If Phase 7 folds
  preflop and Phase 7.5 calls, the boards reached, opponent actions
  taken, and stack states all diverge from that point. So hand-by-
  hand outcomes are NOT directly comparable.

This is fine for the matched-seed delta gate because:
- The opponent's *initial action distribution* is identical across
  runs (same seed → same RNG state for the opponent)
- Variance from initial-condition luck is cancelled out (the gate
  is on Δ_i, not on absolute bb/100)
- We accept that the gate is a measure of "did the change improve
  *over the same starting positions*," not "did the change improve
  *the same hands*"

Required setup (already true in `simulate_bb100.py`, but document it
to lock the invariant):
- `--hands` is identical in both runs (same hand count → same
  dealer rotation depth)
- Player name order in the controllers list is identical
- `--big-blind` and `--stack` identical
- No code change between runs that touches seat assignment or
  dealer math

Concretely:

```bash
# Step 0.5: Phase 7 baseline at seeds 142, 242 (seed 42 already in
# /tmp/phase7_hu/seed42_bias05.log from the original Phase 7 sweep).
# Run BEFORE Items 1+2 ship (i.e. immediately after Step 0 lands).
mkdir -p /tmp/phase7_baseline
for seed in 142 242; do
  docker exec my-poker-face-hybrid-ai-backend-1 \
    python -m experiments.simulate_bb100 \
    --hands 2000 --seed $seed --opponent ManiacBot --adaptation-bias 0.05 \
    > /tmp/phase7_baseline/maniac_seed${seed}.log 2>&1 &
done
wait

# Step 4 candidate sweep (after Items 1+2 ship): same seed set.
mkdir -p /tmp/phase7_5
for seed in 42 142 242; do
  docker exec my-poker-face-hybrid-ai-backend-1 \
    python -m experiments.simulate_bb100 \
    --hands 2000 --seed $seed --opponent ManiacBot --adaptation-bias 0.05 \
    > /tmp/phase7_5/maniac_seed${seed}.log 2>&1 &
done
wait
```

Analysis script consumes both directories:

```bash
python -m experiments.analyze_adjustment_firings \
  --baseline-dir /tmp/phase7_baseline \
  --candidate-dir /tmp/phase7_5 \
  --baseline-seed42 /tmp/phase7_hu/seed42_bias05.log
```

The script pairs seeds across directories, computes `Δ_i` per
archetype, and emits the per-street + per-archetype delta table.

**Gates (matched-seed delta, directional, not delta-committed):**

Run Phase 7 baseline and Phase 7.5 candidate with the **same seed set**
(42, 142, 242). This pairs the runs — each seed produces one
`(phase_7, phase_7_5)` pair per archetype, so we compare them as
**matched samples**, not independent groups. Compute the per-seed
delta `Δ_i = phase_7_5_i - phase_7_i` for each seed.

Per archetype:
- **Mean delta is positive**: `mean(Δ_i) > 0` across all three seeds.
- **No seed regresses materially**: every individual `Δ_i > -30` bb/100.
  (Rationale: even one seed with a -50 bb/100 regression is a real
  signal that the change hurts in some game states.)
- **Mean delta CI excludes zero** (preferred but not required at
  3 seeds): bootstrap or t-test on the three matched deltas; report
  the 90% CI in the analysis output. With only 3 seeds the CI is
  wide, so "mean > 0 and no seed materially regresses" is the
  primary gate; CI is reported for transparency, not used as a hard
  bar.
- **Maniac archetype** can have `mean(Δ) ≥ -10` (allow modest
  regression since the personality is already extreme; the
  adjustment layer is designed for opponents that *we* should treat
  as maniacs, not for *being* one).

The earlier draft used `phase_7_5 > phase_7 + 1.96*sigma`, which a
reviewer correctly flagged as not well-defined: it ignored Phase 7
baseline uncertainty and conflated within-sweep CI with between-run
variance. The matched-seed delta gate above is more robust because
it cancels seed-specific opponent luck (same opponent samples in
both runs) and explicitly handles multi-seed variance.

**Per-street bb/100 reporting** (Codex round 2):
Global bb/100 improvements can hide per-street regressions — e.g.
better turn folds compensating for worse river spew. The analysis
script must report bb/100 *per street* alongside the totals:

| Hero | Total | Preflop | Flop | Turn | River |
|---|---|---|---|---|---|

Reading guidance:
- River bb/100 must improve OR stay within noise — bluff-catch
  override that adds river spew is a failure mode.
- Flop+turn improvements with river regression suggests the dampener
  isn't tight enough on rivers.
- Per-archetype river bb/100 vs ManiacBot in Phase 7 baseline: TBD
  (Step 0 instrumentation will produce this).

### Stability sweep: HU vs GTO-Lite

```bash
docker exec my-poker-face-hybrid-ai-backend-1 \
  python -m experiments.simulate_bb100 \
  --hands 2000 --seed 42 --opponent GTO-Lite --adaptation-bias 0.05
```

**Gates:**
- Baseline stays within [-80, +0] bb/100 vs GTO-Lite (currently -36).
- Other archetypes don't regress by more than 30 bb/100 from current
  Phase 7 numbers vs GTO-Lite.
- Bluff-catch override firing rate < 5% (instrumented).
- Extreme clamp tier firing rate < 5% (instrumented).

### Step 0 behavior check (run after Step 0 lands, before Step 0.5)

Step 0 ADDS new fields and counters but does not change the legacy
`aggression_factor` formula — so behavior should be neutral within
noise. Confirm before proceeding to Step 0.5:

```bash
docker compose exec backend python -m pytest tests/test_strategy/ -q
# All existing tests must pass.

docker exec my-poker-face-hybrid-ai-backend-1 \
  python -m experiments.simulate_bb100 \
  --hands 2000 --seed 42 --opponent ManiacBot --adaptation-bias 0.05
# Each archetype's bb/100 must match the Phase 7 baseline within
# ±5 bb/100. Larger deviations indicate Step 0 unintentionally
# changed behavior — investigate before proceeding.
```

The AF raw-count cap (which DOES change behavior) lives in Item 2,
not Step 0, exactly to keep this check clean.

### 6-max regression: 6-max-vs-rules

Re-run the 6-max-vs-rules harness and verify no archetype regresses
by more than 30 bb/100 vs the Phase 6.5 baselines.

### Diagnostic firing-rate sanity

From Step 0 instrumentation, after sweep completion:
- Strong-hand override: 5-15% of decisions overall
- Bluff-catch override: 15-30% of decisions vs ManiacBot; < 5% vs
  GTO-Lite
- Extreme clamp tier: ~60% of decisions vs ManiacBot after the
  120-opportunity threshold is hit; < 5% vs GTO-Lite

If bluff-catch fires often vs GTO-Lite, thresholds are too loose →
tighten before retesting.

## Risks / gotchas

1. **Bluff-catch is asymmetric**: calling vs a value bet costs ~1 pot;
   folding to a bluff costs 0. The pot-odds conditioning mitigates
   this, but the EV is sensitive to the *opponent's actual betting
   range*, which we estimate from raw stats. The EXTREME tier
   threshold is high (sample ≥ 120 facing-bet decisions) precisely
   to reduce this read-error risk.

2. **Tilt suppression**: today's `tilt_factor` multiplies
   `adaptation_bias`. Both new gates honor the same suppression. Test:
   bluff-catch should NOT fire when hero is tilted (`tilt_factor`
   below the gating floor).

3. **Multiway hands**: stats are aggregated across active opponents
   with a 60% rule. Vs multiway with one maniac + one nit, the
   aggregated AF/all-in stats can paint a misleading picture. The
   plan addresses this two ways (see §"Per-aggressor stats threading"
   and §"Multiway pot suppression" under Item 1):
   - Tier classification uses 6.7a's `select_primary_aggressor()`
     to read the SPECIFIC aggressor's stats, not the aggregate.
   - Override fires only when ALL continuing opponents (besides the
     aggressor) are above sample threshold AND not stations AND not
     all-in. This mirrors 6.7's multiway c-bet suppression rule.

4. **AF inflation by personality bots**: our own Maniac archetype has
   high AF too. When Maniac plays vs ManiacBot, both view each other
   as extreme and bluff-catch widely. The sweep should confirm Maniac
   doesn't gain disproportionate bb/100 — if it does, the gate has
   over-fired.

5. **Postflop classifier blind spot**: Item 3 deferred. The bluff-
   catch override approximates "vs maniac, my pair is good" but the
   underlying strategy table still treats all `facing_bet, medium_
   made` spots identically. Step 0's diagnostic log catches the
   residual leaks.

6. **Threshold calibration**: the AF/all-in/jam-open/sample
   thresholds in Item 2 are placeholders. Step 0 instrumentation
   will tell us where ManiacBot actually lands (AF_postflop
   distribution, all_in_per_facing_bet distribution,
   postflop_jam_open_rate distribution, facing_bet_opportunities
   and postflop_open_opportunities counts). Final thresholds
   calibrated *after* Step 0 ships and runs once, *before* Items 1+2
   ship.

## Effort estimate (v3, after Codex round 2)

| Step | Implementation | Tests | Validation |
|---|---|---|---|
| 0: Instrumentation + opportunity-norm stats + sliding-window + config loader | 1.5 days | 0.75 day | (data collection + calibration table) |
| 0 diagnostics: counterfactual logging + analyze script | 0.75 day | 0.25 day | — |
| 2: Three-tier clamp + tier decay + benchmark prior | 0.75 day | 0.5 day | (shared sweep with Item 1) |
| 1: Bluff-catch override w/ pot-odds + board-danger dampener + envelope clamp | 1.5 days | 0.75 day | 1 day |

**Total: 7-8 days.** Up from 5-6 in v2; the round-2 additions
(board-danger dampener, tier decay, per-street validation, config
externalization, counterfactual logging) each add small increments
that sum to ~1.5 extra days.

Sequencing constraint: Step 0 must complete BEFORE Items 1+2 ship,
because Step 0 produces the calibration table that sets the actual
threshold values. Implementing Item 1+2 against placeholder
thresholds and then calibrating in-flight risks shipping wrong-tuned
behavior.

## Out of scope

- **Item 3 implementation** (postflop classifier opponent-awareness
  chart change). Diagnostics only this phase.
- **Confidence-weighted firing for non-aggressive patterns** (Phase
  6.6 covers tight_nit / hyper_passive). Separate plan.
- **Per-opponent range modeling** in the deep sense (modeling the
  specific cards opponent might hold). Far bigger scope.
- **Tilt-aware bluff-catch sizing** — when hero is mildly tilted (not
  full suppression), should we still bluff-catch but with tighter
  probability? Defer.

## Files to modify

| File | Action | Description |
|---|---|---|
| `poker/strategy/data/phase_7_5_config.yaml` | **NEW** | All thresholds (clamps, sample, signal, decay, prior) — single source of truth |
| `poker/strategy/phase_7_5_config.py` | **NEW** | Config loader (YAML → typed dataclass), with test fixture support |
| `poker/memory/opponent_model.py` (Step 0) | Modify | Add counters: `_facing_bet_opportunities`, `_all_ins_facing_bet`, `_postflop_bet_raise_count`, `_postflop_call_count`, `_postflop_open_opportunities`, `_postflop_jam_opens`. Add properties: `all_in_per_facing_bet`, `postflop_jam_open_rate`, `aggression_factor_postflop` (the postflop AF has the raw-count cap from day one). Legacy `aggression_factor` formula UNCHANGED in Step 0 |
| `poker/memory/opponent_model.py` (Item 2) | Modify | Apply raw-count cap to legacy `aggression_factor` (was Step 0, now lands with Item 2 to keep Step 0 behavior-neutral) |
| `poker/memory/opponent_model.py` (sliding window) | Modify | Add recent-window counters (window_size from config) for tier ratchet-down |
| `poker/strategy/exploitation.py` | Modify | `_determine_clamp` with two-axis gating + benchmark prior + tier decay; AggregatedOpponentStats adds postflop-AF + opportunity-normalized fields |
| `poker/strategy/exploitation.py` (DecisionContext) | Modify | Add `bet_size_pot_ratio` field |
| `poker/strategy/exploitation.py` (`aggregate_from_spots()`) | Modify | **Owned by 7.5, not 6.7.** Extend the 6.7a-shipped aggregator to also aggregate 7.5's new fields (`all_in_per_facing_bet`, `postflop_jam_open_rate`, `facing_bet_opportunities`, `postflop_open_opportunities`, `aggression_factor_postflop`). Preserves 6.7a's 60% rule + weighting policy; just adds field-by-field aggregation for the new fields. |
| `poker/strategy/value_override.py` | Modify | Add `should_apply_bluff_catch_override` (with multiway suppression + station detection), `compute_bluff_catch_strategy`, `_bluff_catch_call_probability`, `_base_call_prob`, `_board_danger_dampener`, `_clamp_to_envelope`, `_is_station` helper; new `BLUFF_CATCH_TRIGGER_CLASSES` |
| `poker/tiered_bot_controller.py` | Modify | Wire bluff-catch override after strong-hand; **call 6.7a's `select_primary_aggressor()` to get per-aggressor stats for tier classification, with `aggregate_from_spots()` fallback when ambiguous**; thread tier from `_determine_clamp`; populate new DecisionContext fields; emit per-decision diagnostic |
| `poker/memory/opponent_model.py` (jam-open counter wiring) | Modify | When incrementing `_postflop_jam_opens` / `_postflop_open_opportunities`, **read 6.7a's per-street `recent_aggressor_name`** to distinguish first-in jams (counter +1) from response jams (already handled by `_all_ins_facing_bet`). 6.7a's tracking is the source of truth. |
| `poker/persistence.py` or analysis schema | Modify | Add new diagnostic columns (see Step 0 schema) |
| `poker/strategy/counterfactual.py` | **NEW** | Heuristic opponent-aware action lookup for Item 3 diagnostics |
| `experiments/analyze_adjustment_firings.py` | **NEW** | Step 0 firing-rate + per-street bb/100 + calibration table output |
| `tests/test_strategy/test_opportunity_normalized_stats.py` | **NEW** | Step 0 stats tests (denominators, postflop-AF, AF raw-count cap) |
| `tests/test_strategy/test_exploitation_three_tier_clamp.py` | **NEW** | Item 2 tests (tier gating, decay ratchet-down, benchmark prior) |
| `tests/test_strategy/test_bluff_catch_override.py` | **NEW** | Item 1 unit tests (pot-odds matrix, dampener, envelope clamp, multiway suppression, station detection) |
| `tests/test_strategy/test_bluff_catch_per_aggressor.py` | **NEW** | Verifies per-aggressor stats threading via `select_primary_aggressor()` + aggregate fallback when spot is ambiguous |
| `tests/test_strategy/test_aggregate_from_spots_phase75_fields.py` | **NEW** | Verifies 7.5's `aggregate_from_spots()` extension aggregates the new fields correctly (60% rule case + weighted-average case for each new field) |
| `tests/test_memory/test_postflop_jam_open_counter.py` | **NEW** | Verifies first-in vs response jam disambiguation using 6.7a's `recent_aggressor_name` (first-in → `_postflop_jam_opens` +1, response → `_all_ins_facing_bet` +1 instead) |
| `tests/test_strategy/test_tiered_bot_bluff_catch.py` | **NEW** | Combined integration test (controller end-to-end) including multiway suppression scenarios |
| `docs/analysis/PHASE_7_5_RESULTS.md` | **NEW** | Validation findings (post-implementation) |

## Reproducibility

Start from commit `a599b27b` or later. Phase 7 chart + routing must be
in place; this plan extends the adjustment layer on top of it.

Baseline numbers to compare against:
- HU vs ManiacBot: see [`PHASE_7_HU_RESULTS.md`](../analysis/PHASE_7_HU_RESULTS.md) table
- HU vs GTO-Lite: same source
- 6-max-vs-rules: see [`PHASE_6_VALUE_OVERRIDE_RESULTS.md`](../analysis/PHASE_6_VALUE_OVERRIDE_RESULTS.md)

## Remaining open questions

After two rounds of Codex review, these are the items NOT yet resolved
that will be answered during implementation rather than in the plan:

1. **Calibrated threshold values.** Step 0 instrumentation will
   produce the AF / all-in-per-facing-bet / facing-bet-opportunities
   distributions for each opponent archetype. The placeholder values
   in `phase_7_5_config.yaml` will be replaced with calibrated values
   before Items 1+2 ship to production. The calibration output is a
   first-class deliverable of Step 0.

2. **Should bluff-catch fire at MEDIUM tier?** Plan currently gates
   bluff-catch on EXTREME tier only. After Step 0 instrumentation
   produces the diagnostic-spot taxonomy, we'll see how often a
   MEDIUM-tier opponent presents bluff-catcher spots. If those spots
   are common AND the bluff-catch dampener already keeps the calls
   safe, we may loosen the gate to MEDIUM. Decision deferred until
   we have data.

3. **Texture bucket coverage for the board-danger dampener.** The
   dampener references `four_flush`, `four_straight`, `paired_high`,
   `monotone` texture buckets. The current postflop classifier
   produces a set of textures; confirm during implementation that
   these names match (the names in `postflop_classifier.py` may
   differ — implementation will reconcile).

4. **Equity numbers in bluff-catch are coarse.** medium_made=55%,
   weak_made=35% are poker-theory hand-waves, not empirical equity
   vs an actual maniac's c-bet range. Step 0 diagnostic data could
   later be used to fit these numbers from observed showdowns. Out
   of scope for v1; tracked as a future calibration pass.

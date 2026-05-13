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
2. [Phase 6.7](PHASE_6_7_OPPONENT_SPOTS.md) — independent multiway opponent
   spots. **Required before 7.5.** 6.7 ships `OpponentSpot` and
   `select_primary_aggressor()`, which 7.5's bluff-catch override consumes
   (see §Risks #3 — per-aggressor stats requirement).
3. **Phase 7.5 (this plan)** — three-tier exploitation clamp + bluff-catch
   override + opportunity-normalized stats.

Shared types this plan touches (incrementally, on top of 6.6 + 6.7):

- `AggregatedOpponentStats` in `poker/strategy/exploitation.py` —
  7.5 adds `all_in_per_facing_bet`, `facing_bet_opportunities`,
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
- **`all_in_per_facing_bet` replaces `all_in_frequency` semantically.**
  Phase 6.6's hyper_aggressive ramp still reads `all_in_frequency` (per-hand).
  When this plan ships, migrate 6.6's ramp threshold (`0.30 → 0.70`) onto
  `all_in_per_facing_bet` and recalibrate, since the denominator is now
  facing-bet opportunities (not hands). Add this migration to the Step 0
  calibration table output so the new threshold value is data-driven.
- **AF raw-count fallback cap** (this plan's Step 0 change to
  `OpponentTendencies._recalculate_stats`) pins hyper_aggressive AF at
  `MEDIUM_AF_THRESHOLD` for opponents with zero calls observed. Phase 6.6's
  AF-axis intensity will then be flat for those opponents — the all-in axis
  still drives detection. Document this in the intensity ramp's docstring.
- **Item 3 (postflop opponent-awareness, deferred)** should consume Phase
  6.7's `OpponentSpot`/`select_primary_aggressor()` for the
  `bettor_archetype` axis rather than re-deriving aggressor identification.
  Diagnostics shipping in Step 0 should already log the spot's identified
  aggressor.

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
        (1.5 days, ships first, NO behavior change)
   ↓
Step 0.5: Run one Phase-7-baseline sweep with instrumentation on
          to produce the calibration table (no behavior change)
   ↓
        ← UPDATE phase_7_5_config.yaml from calibration data ←
   ↓
Item 2: Three-tier exploitation clamp (0.75 day) — uses calibrated
        thresholds, not placeholders
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
| `clamp_tier_ratcheted_down` | bool | NEW: did confidence decay reduce the tier this decision? |
| `opponent_af_at_decision` | float | Bettor's AF at decision time |
| `opponent_af_postflop` | float | NEW: AF computed from postflop actions only (excludes preflop jams) |
| `opponent_all_in_per_facing_bet` | float | NEW: all-ins / facing-bet opportunities (NOT all decisions, NOT hands) |
| `opponent_facing_bet_opportunities` | int | NEW: facing-bet sample count for this opponent |
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
| `all_in_per_facing_bet` | All-in raises by opponent | Times opponent faced a bet from someone (i.e. had `fold/call/raise` to choose from) |
| `aggression_factor_postflop` | (bet + raise + all-in) on flop/turn/river | calls on flop/turn/river |
| `facing_bet_opportunities` | — | Count of opponent's decisions where `fold` was an option |
| `betting_decisions_postflop` | — | Total postflop fold/check/call/raise/all-in by opponent |

Preflop all-ins and preflop AF still computed (used elsewhere), but
the **tier-classification logic uses postflop-only signals** to avoid
the pollution Codex flagged.

### Confidence-decay / tier ratchet-down

Tier can DECREASE when recent stats diverge from accumulated stats —
the bot was briefly fooled by early aggression but later evidence
moderates the read. Implementation: keep a sliding-window stat
alongside the cumulative stat, and downgrade the tier if the recent
window doesn't support it.

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
  # all-ins per facing-bet opportunity (NOT per hand, NOT per
  # decision — see Stat-definition glossary above).
  medium_all_in_per_facing_bet:  0.15
  extreme_all_in_per_facing_bet: 0.30

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
         (REQUIRED — no signal without enough samples).
       - Signal axis: af_postflop meets threshold OR
         all_in_per_facing_bet meets threshold (EITHER one
         qualifies). Rationale: a maniac who bets/raises constantly
         but rarely jams is still extreme; requiring both stats to
         cross would miss this case. The two signals are different
         expressions of the same trait; require sample, allow either
         signal.
       - For tier classification: pick the HIGHEST tier whose sample
         threshold is met AND whose either-signal test passes.
    3. If recent_stats supplied AND recent window has enough samples
       (≥ tier_decay.require_recent_window_full), cap the cumulative
       tier at the recent-window tier — so opponent behavior shifts
       are picked up.

    Returns (clamp_value, tier_enum) for instrumentation.
    """
```

**AND vs OR rationale**: an earlier draft used AND on both signal
axes — a Codex reviewer flagged that this would miss common maniacs
who raise constantly but rarely jam (high `af_postflop`, modest
`all_in_per_facing_bet`). Both stats measure the same underlying
trait (willingness to put chips in without strong holdings); either
crossing its threshold is sufficient evidence given enough sample.
Sample size remains required (no signal from 5 hands), but the two
signals are now treated as **alternative evidence**, not joint
requirements.

### Opportunity-normalization (addresses Codex's biggest concern)

Today's `all_in_frequency = all_ins / hands_dealt` in
`poker/memory/opponent_model.py:149`. This is per-hand, not per
facing-bet opportunity. A player who jams 20% of hands they're dealt
but never sees a flop has 0.20 all-in-freq; a player who sees flops
60% of the time and jams every facing-bet decision has 0.20
all-in-freq too — but the signals are very different.

**Required Step 0 change** (in `poker/memory/opponent_model.py`):

```python
# New fields on OpponentTendencies
_facing_bet_opportunities: int = 0   # incremented when opponent has fold-or-call decision
_all_ins_facing_bet: int = 0         # subset: when opponent's response is all-in

# Derived stat — the canonical "is this opponent prone to overbets / jams
# when facing aggression" signal. Denominator is facing-bet opportunities,
# NOT all decisions (which would include free checks) and NOT hands
# (which would include hands where opponent never faced a bet).
@property
def all_in_per_facing_bet(self) -> float:
    if self._facing_bet_opportunities == 0:
        return 0.0
    return self._all_ins_facing_bet / self._facing_bet_opportunities
```

Threading: `AggregatedOpponentStats` (in `exploitation.py:57`) gets
new `all_in_per_facing_bet` and `facing_bet_opportunities` fields;
manager aggregator computes them weighted same as existing stats.
Existing `all_in_frequency` (per-hand) is kept for backward compat —
Phase 6.6's hyper_aggressive ramp still reads it. When Phase 7.5
ships, the 6.6 ramp threshold should migrate from `all_in_frequency`
to `all_in_per_facing_bet` (see Sequencing notes at top of file).

### AF raw-count fallback edge case

Today: when `_call_count == 0` and `_bet_raise_count > 0`, AF falls
back to `_bet_raise_count` (raw count, not a ratio). This breaks the
"AF > N is extreme" semantic — a player with 6 raises and 0 calls in
10 hands gets AF=6, indistinguishable from a player with 60 raises
and 10 calls (a real maniac).

**Required Step 0 change**: when call_count is 0 but bet_raise_count >
0, return a **conservative** AF that doesn't trigger extreme tiers
without enough calls. Simplest fix:

```python
# In _recalculate_stats:
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

The downstream tiering then correctly says "this opponent might be
extreme, but we don't have call samples to confirm — stay at MEDIUM
clamp."

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
      - 1/3 pot bet → need 17% equity
      - pot-size bet → need 25% equity
      - 2x pot bet  → need 29% equity
      - jam (assume 3x pot) → need ~33% equity

    Equity estimates (rough — see note below):
      - medium_made vs wide c-bet range: ~55%
      - weak_made vs wide c-bet range:   ~35%

    The split approaches 100% call when our equity well exceeds
    required (small bets, safe boards) and approaches fold for large
    bets on dangerous boards.

    Note on equity numbers: 55% / 35% are coarse poker-theory
    averages. The function is BEHAVIORAL — it's saying "stop
    overfolding versus confirmed over-aggression in spots where our
    bluff-catcher likely has equity" — not a literal equity
    calculation. Board-danger dampener is the safety net for spots
    where the equity assumption breaks down (four-flush rivers, etc.).
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

Bluff-catch fires only when **Item 2's EXTREME tier also fires**. This
means the same `facing_bet_opportunities ≥ EXTREME_MIN_OPPORTUNITIES`
(= 120) threshold gates both. Codex flagged the original 100-hand
threshold as too loose for postflop-specific signals — using
EXTREME-tier opportunities (which are facing-bet samples specifically)
fixes this.

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
- `aggression_factor_postflop` excludes preflop bet/raise/all-in
  counts (computed from postflop-only counters)
- AF raw-count fallback caps at `MEDIUM_AF_THRESHOLD` when call_count
  is 0, no longer reports raw count as ratio
- Backward compat: per-hand `all_in_frequency` still produced
- Sliding-window counters reset and accumulate correctly across
  windowed boundaries

`tests/test_strategy/test_exploitation_three_tier_clamp.py` (Item 2)
- `_determine_clamp` returns DEFAULT when sample below MEDIUM threshold
- Returns MEDIUM when sample ≥ MEDIUM threshold AND (AF_postflop ≥
  MEDIUM OR all_in_per_facing_bet ≥ MEDIUM) — sample required, either
  signal qualifies
- Returns EXTREME when sample ≥ EXTREME threshold AND (AF_postflop ≥
  EXTREME OR all_in_per_facing_bet ≥ EXTREME)
- Returns DEFAULT when sample insufficient even if signals are strong
- **High-AF-only maniac**: sample ≥ EXTREME, AF_postflop = 8.0,
  all_in_per_facing_bet = 0.05 → returns EXTREME (signal-OR semantics
  catches this case; the earlier AND draft would have missed it)
- **High-all-in-only opponent**: sample ≥ EXTREME, AF_postflop = 2.0,
  all_in_per_facing_bet = 0.45 → returns EXTREME (same rationale,
  flipped axis)
- **Tier decay (round 2)**: recent-window tier caps cumulative tier —
  setup with cumulative=EXTREME and recent=DEFAULT → returns DEFAULT
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

### Existing test regression

All 344 strategy tests + 27 HU chart/routing tests pass unchanged.

## Validation (combined, not isolated)

Per Codex: validate Items 1+2 together, not separately. The signals
are entangled.

### Primary sweep: HU vs ManiacBot

```bash
for seed in 42 142 242; do
  docker exec my-poker-face-hybrid-ai-backend-1 \
    python -m experiments.simulate_bb100 \
    --hands 2000 --seed $seed --opponent ManiacBot --adaptation-bias 0.05 \
    > /tmp/phase7_5/maniac_seed${seed}.log 2>&1 &
done
wait
```

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
   plan: bluff-catch should require the SPECIFIC aggressor (per-
   aggressor stats, not aggregate) to trigger. Step 0 instrumentation
   captures per-aggressor stats; the gate uses them.

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

6. **Threshold calibration**: the AF/all-in/sample thresholds in
   Item 2 are placeholders. Step 0 instrumentation will tell us
   where ManiacBot actually lands (AF distribution, all-in-per-dec
   distribution). Final thresholds calibrated *after* Step 0 ships
   and runs once, *before* Items 1+2 ship.

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
| `poker/memory/opponent_model.py` | Modify | Add `_facing_bet_opportunities`, `_all_ins_facing_bet`, `_postflop_bet_raise_count`, `_postflop_call_count` counters; add `all_in_per_facing_bet`, `aggression_factor_postflop` properties; cap AF raw-count fallback at MEDIUM threshold |
| `poker/memory/opponent_model.py` (sliding window) | Modify | Add recent-window counters (window_size from config) for tier ratchet-down |
| `poker/strategy/exploitation.py` | Modify | `_determine_clamp` with two-axis gating + benchmark prior + tier decay; AggregatedOpponentStats adds postflop-AF + opportunity-normalized fields |
| `poker/strategy/exploitation.py` (DecisionContext) | Modify | Add `bet_size_pot_ratio`, `street`, `board_texture` fields |
| `poker/strategy/value_override.py` | Modify | Add `should_apply_bluff_catch_override`, `compute_bluff_catch_strategy`, `_bluff_catch_call_probability`, `_base_call_prob`, `_board_danger_dampener`, `_clamp_to_envelope`; new `BLUFF_CATCH_TRIGGER_CLASSES` |
| `poker/tiered_bot_controller.py` | Modify | Wire bluff-catch override after strong-hand; thread tier from `_determine_clamp` into exploitation + override; populate new DecisionContext fields; emit per-decision diagnostic |
| `poker/persistence.py` or analysis schema | Modify | Add new diagnostic columns (see Step 0 schema) |
| `poker/strategy/counterfactual.py` | **NEW** | Heuristic opponent-aware action lookup for Item 3 diagnostics |
| `experiments/analyze_adjustment_firings.py` | **NEW** | Step 0 firing-rate + per-street bb/100 + calibration table output |
| `tests/test_strategy/test_opportunity_normalized_stats.py` | **NEW** | Step 0 stats tests (denominators, postflop-AF, AF raw-count cap) |
| `tests/test_strategy/test_exploitation_three_tier_clamp.py` | **NEW** | Item 2 tests (tier gating, decay ratchet-down, benchmark prior) |
| `tests/test_strategy/test_bluff_catch_override.py` | **NEW** | Item 1 unit tests (pot-odds matrix, dampener, envelope clamp) |
| `tests/test_strategy/test_tiered_bot_bluff_catch.py` | **NEW** | Combined integration test (controller end-to-end) |
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

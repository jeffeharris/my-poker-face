---
purpose: Plan to widen the adjustment layer so the tiered bot realizes equity vs confirmed extreme opponents (maniacs)
type: design
created: 2026-05-13
last_updated: 2026-05-13T16:30:00
---

# Phase 7.5: Adjustment-layer widening

## Codex review notes (round 1)

This plan was reviewed by Codex on 2026-05-13. Key revisions incorporated:

- **Step 0 instrumentation** is now a prerequisite to behavior changes
  (was: implicit, embedded in items)
- **Bluff-catch splits are pot-odds-conditional**, not fixed 80/20-50/50
- **Three-tier exploitation ramp** (0.4 / 0.6 / 0.8), not binary tier
- **Opportunity-normalized stats** — `all_in_frequency` recomputed per
  betting-decision (today: per hand-dealt); AF must account for raw-
  count fallback edge case
- **Item 1 explicitly consumes Item 2's envelope** — exploitation
  widens the allowed adjustment; value_override (when triggered)
  supersedes it rather than stacking
- **bb/100 targets are directional bands**, not commitments
- **Item 3 partially un-deferred** — ship the diagnostic logging now
  even if the postflop chart change waits

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
Step 0: Instrumentation (1 day, ships first, no behavior change)
   ↓
Item 2: Three-tier exploitation clamp (0.5 day)
   ↓ (smoke-test against GTO-Lite to confirm no false fires)
Item 1: Bluff-catch override w/ pot-odds conditioning (1-2 days)
   ↓
Combined validation across 3 seeds × 2 opponents
   ↓
Item 3 diagnostics (Phase 7.5 scope), Item 3 implementation deferred
```

The reordering matters: Item 2 is a smaller behavioral change that
exercises the same exploit pathway. If Item 2 alone over-fires on
balanced opponents, we'll learn that before committing to the bigger
Item 1 change. If Item 2 underwhelms vs ManiacBot, Item 1 picks up
the rest.

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
| `opponent_af_at_decision` | float | Bettor's AF at decision time |
| `opponent_all_in_per_decision` | float | NEW: per-betting-decision rate (not per-hand) |
| `opponent_facing_bet_opportunities` | int | NEW: number of facing-bet samples for this opponent |
| `opponent_hands_observed` | int | Existing |
| `hand_strength_class` | str | nuts/strong_made/medium_made/weak_made/air/etc |
| `facing_bet_size_pot_ratio` | float | NEW: bet size / pot when applicable |

### Replay-spot taxonomy

A "diagnostic spot" is logged when ALL of:
- Opponent stats meet the EXTREME thresholds (defined below in Item 2)
- Hero has a non-strong made hand (medium_made or weak_made)
- Hero faces a bet (`fold` in available actions)

This lets us later answer: "in the spots where Item 1+2 *could* have
overridden, what did the bot actually do, and what was the outcome?"

### Outputs

- New analysis script `experiments/analyze_adjustment_firings.py`:
  reads the trace, prints firing rates per opponent/archetype,
  emits a per-spot summary file
- Validation gate: firing rate sanity (see Goal §5)

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

```python
DEFAULT_MAX_TOTAL_SHIFT = 0.4       # cold start / weak detection
MEDIUM_MAX_TOTAL_SHIFT  = 0.6       # confirmed aggressive
EXTREME_MAX_TOTAL_SHIFT = 0.8       # confirmed extreme aggressive

# Sample thresholds — use facing_bet_opportunities (NOT hands_observed)
# for postflop-relevant signal. See "Opportunity-normalization" below.
MEDIUM_MIN_OPPORTUNITIES   = 60
EXTREME_MIN_OPPORTUNITIES  = 120

# Signal-strength thresholds — calibrated empirically from ManiacBot
# stats observed in Phase 7 sweeps (need to measure during Step 0).
# Initial values below are placeholders; Step 0 instrumentation will
# show us where ManiacBot actually lands.
MEDIUM_AF_THRESHOLD     = 4.0       # vs current HYPER_AGG = 5.0
MEDIUM_ALL_IN_PER_DEC   = 0.15      # opportunity-normalized
EXTREME_AF_THRESHOLD    = 6.0
EXTREME_ALL_IN_PER_DEC  = 0.30      # opportunity-normalized
```

New helper:

```python
def _determine_clamp(
    stats: AggregatedOpponentStats,
    facing_bet_opportunities: int,
) -> float:
    """Pick the L1 clamp based on sample + signal confidence.

    Two-axis gating — both axes required for next tier:
      - Sample axis: facing-bet opportunities (NOT raw hands_observed,
        because postflop signal needs postflop spots)
      - Signal axis: AF AND all-in-per-decision (both, not either —
        a high-AF/zero-all-in player is different from a maniac, and
        the strongest tier requires evidence of BOTH)

    Returns DEFAULT/MEDIUM/EXTREME based on whichever tier's both-axis
    test passes. Defaults to DEFAULT.
    """
```

### Opportunity-normalization (addresses Codex's biggest concern)

Today's `all_in_frequency = all_ins / hands_dealt` in
`poker/memory/opponent_model.py:149`. This is per-hand, not per
betting-decision. A player who jams 20% of hands they're dealt but
never sees a flop has 0.20 all-in-freq; a player who sees flops 60%
of the time and jams every flop has 0.20 all-in-freq too — but the
signals are very different.

**Required Step 0 change** (in `poker/memory/opponent_model.py`):

```python
# New field on OpponentTendencies
_betting_decisions: int = 0  # incremented on each fold/check/call/raise/all-in

# Derived stat
@property
def all_in_per_decision(self) -> float:
    if self._betting_decisions == 0:
        return 0.0
    return self._all_in_count / self._betting_decisions
```

Threading: `AggregatedOpponentStats` (in `exploitation.py:57`) needs
a new `all_in_per_decision` field; manager aggregator computes it
weighted same as `all_in_frequency`. Existing `all_in_frequency` is
kept for backward compat but the NEW thresholds use the new field.

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

### Pot-odds-conditional splits (addresses Codex's biggest design concern)

Instead of fixed 80/20-50/50, the call probability is computed from
the bet size as a fraction of pot. The math is:

```python
def _bluff_catch_call_probability(
    hand_strength: str,
    bet_size_pot_ratio: float,  # bet / pot_before_bet
) -> float:
    """Compute call probability conditioned on pot-odds and hand class.

    Pot-odds for calling: required_equity = bet / (pot + 2*bet)
      - 1/3 pot bet → call costs 0.33 to win 1.66 → need 17% equity
      - pot-size bet → call costs 1.0 to win 3.0  → need 25% equity
      - 2x pot bet  → call costs 2.0 to win 5.0  → need 29% equity
      - jam (assume 3x pot) → need ~33% equity

    Our equity with a bluff-catcher vs a confirmed maniac's c-bet
    range is roughly:
      - medium_made vs wide c-bet range: ~55% equity
      - weak_made vs wide c-bet range:   ~35% equity

    The split should approach 100% call when our equity well exceeds
    required (small bets) and approach 50/50 or fold for large bets
    where required equity creeps up.
    """
    if hand_strength == HandStrengthClass.MEDIUM_MADE.value:
        # Equity advantage stays positive up to pot-sized bets.
        if bet_size_pot_ratio <= 0.50:
            return 0.95         # small bet, easy bluff-catch
        elif bet_size_pot_ratio <= 1.00:
            return 0.80         # pot-size bet, still positive
        elif bet_size_pot_ratio <= 2.00:
            return 0.50         # large bet, marginal
        else:
            return 0.20         # huge bet / jam, mostly fold
    elif hand_strength == HandStrengthClass.WEAK_MADE.value:
        # Less equity, fold faster.
        if bet_size_pot_ratio <= 0.33:
            return 0.70
        elif bet_size_pot_ratio <= 0.67:
            return 0.40
        else:
            return 0.10
    return 0.0  # not in trigger classes
```

Bet-size is read from `DecisionContext` — needs a new field
`bet_size_pot_ratio: float` populated by the controller from
`(highest_bet - hero_bet) / pot_before_bet`.

### Sample threshold

Bluff-catch fires only when **Item 2's EXTREME tier also fires**. This
means the same `facing_bet_opportunities ≥ EXTREME_MIN_OPPORTUNITIES`
(= 120) threshold gates both. Codex flagged the original 100-hand
threshold as too loose for postflop-specific signals — using
EXTREME-tier opportunities (which are facing-bet samples specifically)
fixes this.

### Envelope semantics (addresses Codex's multiplication concern)

Item 1 and Item 2 do NOT compose multiplicatively. The ordering is
already sequential in the controller:

```python
# Today's pipeline (poker/tiered_bot_controller.py:204-217)
modified = apply_exploitation_offsets(...)  # uses Item 2's clamp
modified = compute_value_override_strategy(...)  # supersedes if fired
```

If Item 1 triggers, it **replaces** the strategy. Item 2's offsets
were applied to a strategy that gets thrown away. The plan makes
this explicit in code comments and in the docstring of
`compute_bluff_catch_strategy`. No additional code change needed —
the semantics already work this way.

What we add: a unit test that confirms when bluff-catch fires, the
resulting distribution is determined by bluff-catch alone, not the
result of exploitation+bluff-catch compounding.

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
  facing bet), log what the controller decided vs. what an
  opponent-aware lookup *would have* said.

The diagnostic doesn't need a separate strategy table yet — just a
"what would we do if the chart key included `bettor_archetype`?"
heuristic comparison. We can emit a JSON log of these and decide
whether the residual leak after Items 1+2 justifies the bigger
Item 3 chart change.

If residual leak is < 30 bb/100, Item 3 is probably not worth the
complexity. If > 50, do Item 3.

## Tests

### Unit tests

`tests/test_strategy/test_opportunity_normalized_stats.py` (Step 0)
- `all_in_per_decision` correctly computed from
  `_all_in_count / _betting_decisions`
- AF raw-count fallback caps at `MEDIUM_AF_THRESHOLD` when call_count
  is 0, no longer reports raw count as ratio
- Backward compat: `all_in_frequency` (per-hand) still produced

`tests/test_strategy/test_exploitation_three_tier_clamp.py` (Item 2)
- `_determine_clamp` returns DEFAULT when below MEDIUM thresholds
- Returns MEDIUM when sample ≥ 60 AND AF ≥ 4 AND all-in-per-dec ≥ 0.15
- Returns EXTREME when sample ≥ 120 AND AF ≥ 6 AND all-in-per-dec ≥ 0.30
- Returns DEFAULT (not MEDIUM) when one axis is missing
- `apply_exploitation_offsets` with each tier produces progressively
  wider distributions

`tests/test_strategy/test_bluff_catch_override.py` (Item 1)
- `_bluff_catch_call_probability` returns the expected value across
  the bet-size/hand-class matrix (parameterized table test)
- `should_apply_bluff_catch_override` returns True only when ALL gates
  pass (medium/weak_made + facing_bet + EXTREME tier + adaptation
  bias gate)
- Returns False for: strong hands (use existing path), weak draw,
  open spot, balanced opponent, MEDIUM-tier opponent (not yet
  EXTREME), low sample

### Integration tests

`tests/test_strategy/test_tiered_bot_bluff_catch.py`
- ManiacBot-pattern stats + medium_made flop + facing-1/3-pot →
  controller decides `call` with high probability (≥ 80%)
- Same stats + facing-2x-pot → controller folds more often (~50/50)
- Same stats + strong_made → controller still hits strong-hand
  override (no regression)
- GTO-Lite-pattern stats (AF~2, no all-ins) + medium_made →
  controller does NOT fire bluff-catch (sample below thresholds)

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

**Gates (directional, not delta-committed):**
- All four key archetypes (Baseline, TAG, LAG, Nit) improve by a
  margin larger than their CI half-width vs the Phase 7 baseline.
  That is, `phase_7_5 > phase_7 + 1.96*sigma` per archetype.
- No archetype regresses below Phase 7 baseline.
- Maniac archetype improves OR stays within noise (its personality is
  already wide; adjustment-layer effect should be smaller).

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

## Effort estimate (revised)

| Step | Implementation | Tests | Validation |
|---|---|---|---|
| 0: Instrumentation + per-decision capture + opportunity-norm stats | 1 day | 0.5 day | (none; data collection only) |
| 2: Three-tier exploitation clamp | 0.5 day | 0.5 day | (shared sweep with Item 1) |
| 1: Bluff-catch override w/ pot-odds | 1-2 days | 0.5 day | 1 day |
| 3 diagnostic log: opponent-aware spot detection | 0.5 day | 0.25 day | (none; diagnostic only) |

**Total: 5-6 days.** Was 3 days in v1 — the increase is from the
instrumentation prerequisite and the pot-odds conditioning detail.

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
| `poker/memory/opponent_model.py` | Modify | Add `_betting_decisions` counter + `all_in_per_decision` property; cap AF raw-count fallback at MEDIUM_AF_THRESHOLD |
| `poker/strategy/exploitation.py` | Modify | Add `MEDIUM_*` / `EXTREME_*` thresholds + `_determine_clamp` helper; AggregatedOpponentStats adds `all_in_per_decision` + `facing_bet_opportunities` |
| `poker/strategy/value_override.py` | Modify | Add `should_apply_bluff_catch_override` + `compute_bluff_catch_strategy` + `_bluff_catch_call_probability`; new `BLUFF_CATCH_TRIGGER_CLASSES` |
| `poker/tiered_bot_controller.py` | Modify | Wire bluff-catch override after strong-hand; thread `_determine_clamp` value into exploitation; populate `DecisionContext.bet_size_pot_ratio` |
| `poker/strategy/exploitation.py` (DecisionContext) | Modify | Add `bet_size_pot_ratio` field |
| `poker/persistence.py` or analysis schema | Modify | Add new diagnostic columns to per-decision analysis |
| `experiments/analyze_adjustment_firings.py` | **NEW** | Step 0 firing-rate analysis script |
| `tests/test_strategy/test_opportunity_normalized_stats.py` | **NEW** | Step 0 stats tests |
| `tests/test_strategy/test_exploitation_three_tier_clamp.py` | **NEW** | Item 2 tests |
| `tests/test_strategy/test_bluff_catch_override.py` | **NEW** | Item 1 unit tests |
| `tests/test_strategy/test_tiered_bot_bluff_catch.py` | **NEW** | Combined integration test |
| `docs/analysis/PHASE_7_5_RESULTS.md` | **NEW** | Validation findings (post-implementation) |

## Reproducibility

Start from commit `a599b27b` or later. Phase 7 chart + routing must be
in place; this plan extends the adjustment layer on top of it.

Baseline numbers to compare against:
- HU vs ManiacBot: see [`PHASE_7_HU_RESULTS.md`](../analysis/PHASE_7_HU_RESULTS.md) table
- HU vs GTO-Lite: same source
- 6-max-vs-rules: see [`PHASE_6_VALUE_OVERRIDE_RESULTS.md`](../analysis/PHASE_6_VALUE_OVERRIDE_RESULTS.md)

## Open questions for round-2 review

For codex round 2:
1. Are the placeholder thresholds (AF=4/6, all-in-per-dec=0.15/0.30,
   sample=60/120) plausible starting points, given that Step 0 will
   calibrate them with real data before items ship?
2. Is the pot-odds-conditional bluff-catch table (the
   `_bluff_catch_call_probability` matrix) defensible? Equity
   estimates (medium_made = 55% vs maniac c-bet range, weak_made =
   35%) are hand-wave numbers from poker theory — should they be
   grounded in actual equity calculations against the observed
   opponent range?
3. Is the "EXTREME tier gates bluff-catch" coupling too tight? Could
   we want bluff-catch to fire at MEDIUM tier with tighter pot-odds
   conditions?
4. Anything in the instrumentation schema that should be added or
   removed?

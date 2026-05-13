---
purpose: Plan to build heads-up specific preflop strategy charts to fix the chart-mismatch leak
type: design
created: 2026-05-13
last_updated: 2026-05-13T15:30:00
---

## Implementation decisions (resolved 2026-05-13)

- **No new `HUPreflopNode` dataclass.** HU chart reuses the existing `PreflopNode` /
  `StrategyTable` types and the existing JSON schema. Only two things change:
  (a) a second JSON file (`preflop_100bb_hu.json`) loaded into a second
  `StrategyTable` instance, and (b) routing in `TieredBotController` picks
  the HU table when `len(game_state.players) == 2`. `get_6max_position`
  already returns `'SB'`/`'BB'` for HU, so the node builder is unchanged.
- **Single opening sizing: `raise_3bb`.** Action vocabulary already supports
  it (see `_RAISE_ACTIONS` in `strategy_table.py`). No 2.5x and no preflop
  limps in v1. SB VPIP target band correspondingly tightens to ~55-70%
  (3bb opens narrower than 2.5x).
- Step 1 (HUPreflopNode) below is **superseded** — leave the section for
  history but the implementation skips it. Step 3 (classifier) likewise
  reuses existing `build_preflop_node` unchanged.

# Phase 7: HU preflop charts

## Context (read before starting)

Phase 6 + 6.5 + Step B mostly fixed the "aggressive humans farm us" problem.
TAG goes from -62 bb/100 to +28 bb/100 vs the 5-rule_bot mix. Strong hands
get committed correctly via value override. Marginal hands get widened via
offsets. Short stacks get push/fold via the heuristic.

**But every tiered archetype still LOSES net HU vs ManiacBot at 100 BB**
(-90 to -195 bb/100 across archetypes per the HU validation in
`docs/analysis/PHASE_6_VALUE_OVERRIDE_RESULTS.md`).

Even at short stacks (12 BB), most archetypes are still net-losing HU
(only Maniac and LAG go positive). The reason is **structural**: our
strategy table is calibrated for 6-max preflop ranges, and HU ranges are
fundamentally different. The Phase 6 plan documents this as the
"chart-mismatch leak":

> Even Baseline (Layer 1 only, no personality distortion) loses -53.1 bb/100
> vs GTO-Lite HU. The 6-max preflop charts simply don't transfer to HU
> ranges.

The fix: build HU-specific preflop charts and route to them when the table
size is 2.

## What "HU preflop ranges" means

In HU, only 2 players. Each hand:
- Small blind (SB) acts first preflop, also is the dealer/button
- Big blind (BB) acts last preflop

Both players see EVERY hand from a fixed seat. There's no "early position
fold range" — there's only the button-vs-bb range.

Standard HU opening strategy at 100 BB cash depth is more nuanced than
"SB opens 80% to 3bb":

- **Modern HU uses smaller opens.** 2x or 2.5x BB is more common than 3x.
  Some Nash-influenced strategies even include limping with a portion of
  the range. A pure-3bb open range that's too wide gets attacked by
  3-bets from BB.
- **Mix of opening sizes**: e.g. 2.5x with most of the range, 3bb with
  some bluffs/value, limp with a small bluff-catching subset.
- **SB raise-or-fold VPIP target: ~65-80% depending on sizing.** Tighter
  if opens are 3bb+, wider if 2-2.5x.
- **BB defends ~50-65% of opens** (call + 3-bet), depending on opener
  sizing.

Compare to 6-max where UTG opens ~15%, button opens ~40%, BB defends maybe
30-40% vs button. **The 6-max button range is the lower bound of what HU
SB should play.** Our current HU behavior uses that BUTTON range, which
is much too tight.

### Why this matters quantitatively

When TAG plays HU on the button:
- Current: opens ~40% of hands (button range from 6-max chart)
- Correct: opens ~75% of hands
- Misses ~35% of profitable opens → folds away EV every hand

When BB:
- Current: defends maybe 30% (BB-vs-button range)
- Correct: defends ~65%
- Folds ~35% of profitable defends → bleeds blinds away

That's the bulk of the -190 bb/100 we see HU vs ManiacBot. Override and
exploitation help when we DO play hands, but we're folding too much
preflop to start with.

## Goal — definition of done

A working Phase 7 produces these observable outcomes:

1. **HU detection at decision time**: `TieredBotController._get_ai_decision`
   identifies "this is HU" (2 active players) and routes to HU charts
   instead of the 6-max table.

2. **HU SB VPIP target: 65-80%** (depending on chart sizing convention).
   TAG acting from SB in HU plays 65-80% of hands voluntarily. Currently
   TAG opens ~30-40%. The target range is wide because the exact number
   depends on what sizing the chart uses (3bb opens narrower than 2.5x).
   The mechanism gate is "VPIP is clearly higher than the 6-max button
   number," not a specific percentage.

3. **HU BB defense range 50-65%**: TAG facing an SB open defends 50-65%
   of hands (call + 3-bet), again depending on facing-open sizing.

4. **Net bb/100 vs ManiacBot HU directionally improves** for Nit, TAG,
   LAG. Currently all four archetypes lose -90 to -195 bb/100. Targets
   (per Codex review — bb/100 is noisy with 3-seed × 2000-hand sweeps,
   so use as direction-only signal): TAG improves by ≥30 bb/100, LAG ≥30,
   Nit ≥20. Maniac archetype unchanged.
   **Primary gate is the action-distribution metric (#2/#3); bb/100 is
   secondary.**

5. **6-max behavior unchanged**: existing strategy table still used when
   2 < active players ≤ 6. All current 6-max-vs-rules gates still pass.

6. **No regression**: all existing tests pass (currently 354+ in strategy/memory).

## Approach overview

```
Existing pipeline:
    base = strategy_table.lookup(node)    # 6-max only
    modified = modify_strategy(...)
    exploited = apply_exploitation_offsets(...)
    ...

Phase 7 adds:
    if num_active_players == 2:
        base = hu_strategy_table.lookup(hu_node)
    else:
        base = strategy_table.lookup(node)    # existing 6-max
    modified = modify_strategy(...)
    ... rest unchanged
```

The downstream pipeline (personality, exploitation, value override,
short-stack, math floor) operates identically on the HU strategy. The only
change is the source of the baseline preflop distribution.

## Concrete design

### Step 1: HU preflop node structure

Currently `poker/strategy/nodes.py` has `PreflopNode`:
```python
@dataclass
class PreflopNode:
    hand: str             # canonical, e.g. 'AKs'
    position: str         # 'UTG', 'MP', 'CO', 'BTN', 'SB', 'BB'
    scenario: str         # 'rfi' (raise-first-in), 'vs_open', '3bet', etc.
    opener_position: str  # for vs_open scenarios
```

For HU, simpler:
```python
@dataclass
class HUPreflopNode:
    hand: str
    position: str          # 'SB' or 'BB' only
    scenario: str          # 'rfi', 'vs_open', '3bet', '4bet'
```

Position is always SB or BB. Scenario captures the action sequence.

### Step 2: HU strategy table

New file: `poker/strategy/hu_strategy_table.py`. Mirror of
`strategy_table.py` but with HU-specific entries.

Two options for generating the chart data:

**Option A: Solver-derived (preferred)**
Use existing tools (PioSolver, GTO+, etc.) to generate Nash HU charts at
100 BB depth. Export as a CSV mapping `(hand, position, scenario) →
{action: probability}`. Load into a `StrategyTable` at startup.

This is the technically correct approach but requires:
- Access to a solver
- Time to run analyses for each scenario
- Data engineering to import

**Option B: Hand-crafted from poker theory**
Use published HU starting hand charts (e.g., from PokerStove, Cardrunners,
or known Nash-equilibrium tables). Manually encode them as Python dicts.

**FIRST DELIVERABLE before any chart data entry** (per Codex review):
write `poker/strategy/data/hu_preflop_chart_README.md` documenting:
1. The specific chart source used (URL, book reference, or solver output)
2. The stack depth assumed (typically 100 BB)
3. The opening sizing convention (3bb, 2.5x, mixed?)
4. How facing-3bet and facing-4bet ranges are derived
5. Limit cases (e.g. "for hands not in source, fold by default")

Without this, the chart is unauditable and impossible to recreate.
Reviewers can't tell if a specific entry is correct because the spec is
ambiguous. Make the source explicit before writing data.

Less precise but FAST. For 169 canonical hands × 2 positions × 4 scenarios
(rfi, vs_open, 3bet, 4bet), that's ~1350 entries.

**Codex flagged this is more involved than "a few hours of data entry."**
The published charts vary by sizing, depth, and source. The implementer
will need to:
1. Pick a SPECIFIC chart source and stack-depth/sizing assumption (e.g.
   "HRC HU cash, 100BB, 2.5x SB open")
2. Translate the source format (often called/raised/folded categories)
   into our action probabilities
3. Define limit cases for "AKo, BB, vs 4-bet" where source charts may be
   silent or assume push/fold
4. Handle the action vocabulary mismatch — our resolver uses 'raise_3bb',
   'raise_4x', etc. The chart needs to emit one of those, not whatever
   the source publication uses
5. Validate via spot-check that distribution sums to 1.0 per entry

Realistic estimate for high-quality hand-authored chart: 2-3 days of
focused work, not a few hours. Calibration after validation runs adds
1-2 more days.

**Recommendation: B for v1, plan to upgrade to A later.** Treat Option
B as a STARTING POINT that will need 1-2 calibration rounds based on
validation data, not a finished artifact.

The data structure mirrors `strategy_table.py`:
```python
HU_PREFLOP_TABLE = {
    PreflopNode(hand='AA', position='SB', scenario='rfi').key:
        StrategyProfile({'raise_3bb': 0.95, 'jam': 0.05}),
    PreflopNode(hand='AA', position='SB', scenario='vs_open').key:
        # Actually this can't happen in HU — SB acts first.
        # 'vs_open' from SB would mean facing a 3-bet, which is a different node.
    ...
}
```

Note: HU scenarios are simpler. SB scenarios: rfi (open), facing-3bet,
facing-4bet. BB scenarios: facing-open (defend), facing-4bet.

### Step 3: HU node builder

New function in `poker/strategy/preflop_classifier.py` (or a new
`hu_preflop_classifier.py`):

```python
def build_hu_preflop_node(game_state, player_idx, canonical_hand) -> HUPreflopNode:
    """Build an HU preflop node from game state.

    Position is determined by dealer_idx — in HU, dealer = SB. The non-dealer
    is BB. Scenario is determined by raises_this_round and action history.
    """
    ...
```

### Step 4: Controller routing

**Codex flagged a subtle bug in the original routing condition:**
`num_active_players == 2` is true in many 6-max postflop spots (after
folds). The correct gate is **the table mode at hand start**, not
non-folded count at decision time.

Use **total seated players** (the count that started this hand):

```python
# Detect HU at the TABLE level — not post-fold count
# game_state.players contains ALL players seated this hand, including
# folded ones (is_folded=True). num seated = len(game_state.players).
num_seated = len(game_state.players)
is_hu = num_seated == 2

if is_hu:
    hu_node = build_hu_preflop_node(game_state, player_idx, canonical_hand)
    base_strategy = self.hu_strategy_table.lookup_with_fallback(hu_node, valid_actions)
else:
    node = build_preflop_node(game_state, player_idx, canonical_hand)
    base_strategy = self.strategy_table.lookup_with_fallback(node, valid_actions)

# ... rest of pipeline unchanged
```

Edge cases (verify in tests):
- 6-max hand with 4 folds before hero acts → `num_seated == 6` → uses
  6-max chart correctly
- HU sim runs (`run_matchup` creates 2-player game state) → `num_seated == 2`
  → uses HU chart
- Tournament: when only 2 players remain (heads-up final), `num_seated == 2`
  → uses HU chart (correct — tournament HU and cash HU use similar
  preflop strategy at 100 BB; short-stack heuristic handles depth)

Constructor: add `hu_strategy_table` alongside `strategy_table`. Loaded
once at module init (like the current strategy_table).

### Step 5: Postflop HU?

Postflop doesn't have the same chart-mismatch issue — postflop classifier
uses board texture + position + SPR, which are independent of table size.
**Postflop unchanged for Phase 7.**

But there's a related consideration: postflop ranges in HU vs 6-max are
wider, so opponent's range when they bet flop is different. This is partly
addressed by exploitation (per-aggressor stats already use the opponent's
specific tendencies). Real fix would be HU-specific postflop charts but
that's much bigger scope. Leave for now.

### Step 6: Personality distortion interaction

`modify_strategy` distorts the baseline based on personality anchors. This
distortion is the same regardless of source chart. The HU chart's wider
ranges + personality distortion will produce wider opens for loose
archetypes and tighter opens for nits — same as 6-max distortion logic.

Should be fine. Tests should confirm Nit doesn't open 80% just because
the HU chart says SB opens 80% — Nit's personality distortion should tighten
it back toward ~50% (still wider than current 25%).

## Chart specification (data format)

For each (hand × position × scenario) tuple, the chart specifies action
probabilities. The available actions need to match what the strategy table
already emits:

- Preflop opens (rfi): `raise_2.5bb`, `raise_3bb`, `raise_3x`, `call` (limp,
  rare in HU), `fold`
- Facing open (BB vs SB): `call`, `raise_3x` (3-bet), `raise_4x`, `fold`
- Facing 3-bet (SB vs BB 3-bet): `call`, `raise_4x` (4-bet), `jam`, `fold`
- Facing 4-bet: `call`, `jam`, `fold`

Suggested data file layout: CSV in `poker/strategy/data/hu_preflop_chart.csv`:

```csv
hand,position,scenario,raise_3bb,jam,call,fold
AA,SB,rfi,0.95,0.05,0.0,0.0
AA,BB,vs_open,0.0,0.0,0.0,0.0       # placeholder — AA never folds, see actual chart
22,SB,rfi,0.70,0.0,0.0,0.30
22,BB,vs_open,0.0,0.05,0.55,0.40
72o,SB,rfi,0.0,0.0,0.0,1.00
...
```

**Important** (per Codex review): every row's probabilities must sum
to exactly 1.0. The example above keeps AA,SB,rfi at `{raise_3bb: 0.95,
jam: 0.05}` for clarity but real entries must have all columns summing
to 1.0. Validate this in the chart loader test, not just per-action
tests.

Plus a small Python loader.

## Tests

New unit tests:
- `tests/test_strategy/test_hu_strategy_table.py`:
  - All 169 hands present in chart at each position × scenario combo
  - Probabilities sum to 1.0 per entry (within float tolerance) — this
    is the **strict** per-row gate
  - AA/KK opens 95%+ from SB (raise + jam combined; sum of aggressive
    action probabilities)
  - 72o opens 0% from SB (or close to it; allow up to 5% if chart
    includes occasional bluff opens)
  - **Sizing-dependent open-range gate**: the SB opening "range total"
    (sum of P(raise) + P(call/limp) across all 169 hands, weighted by
    hand frequency) falls within the sizing-determined target band:
    - 3bb-only opens chart: total VPIP target 55-70%
    - 2.5x-only opens chart: total VPIP target 65-80%
    - Mixed sizing chart: target derived from chosen mix
  - BB defense range (P(call) + P(raise) summed across hands): 50-65%
    (sizing-dependent, similar logic)

  Test should explicitly check whichever band matches the chart's
  chosen sizing convention. **First step before tests**: document the
  chosen sizing convention in `poker/strategy/data/hu_preflop_chart_README.md`
  (see below) and write tests against that.

- `tests/test_strategy/test_tiered_bot_hu_routing.py`:
  - 2-player game state → uses HU table
  - 3-player game state → uses 6-max table
  - When HU active, button position is SB
  - Personality distortion still applies to HU base

## Validation

Run the HU sweep (already exists):
```bash
for bias in 0.05 0.85; do
  for seed in 42 142 242; do
    docker exec my-poker-face-hybrid-ai-backend-1 \
      python -m experiments.simulate_bb100 \
      --hands 2000 --seed $seed --opponent ManiacBot \
      --adaptation-bias $bias \
      > /tmp/phase7_hu/hu_bias${bias}_seed${seed}.log 2>&1 &
  done
done
wait
```

**Codex flagged: bb/100 is too noisy for primary gates.** Use action
distribution as the primary gate and bb/100 as direction-only.

### Primary gates: action distribution

For each archetype at bias=0.05 (control — measures the chart, not
exploitation):

| Metric | Pre-Phase-7 (current) | Phase 7 target |
|---|---|---|
| TAG SB VPIP | ~30-40% | 55-75% (depends on chart sizing) |
| TAG BB defense rate | ~30% | 50-65% |
| TAG SB PFR | similar to VPIP | similar to VPIP (most opens raise) |

Validation extracts these distribution metrics from the per-decision
trace (`analyze_6max_vs_rules.py` already prints VPIP/PFR/AF). If
the SB VPIP doesn't move into the target range, the CHART is wrong
and bb/100 changes are meaningless.

### Secondary gates: bb/100 direction

Compare to the Phase 6.5 HU baselines (from
`docs/analysis/PHASE_6_VALUE_OVERRIDE_RESULTS.md`):

| Hero | Phase 6.5 HU (treatment) | Phase 7 direction-only target |
|---|---|---|
| Calling Station | -116.7 | improves (≥ 20 bb/100) |
| Rock | -87.7 | improves or stays similar |
| Nit | -104.6 | improves by ≥ 20 bb/100 |
| TAG | -135.8 | improves by ≥ 30 bb/100 |
| LAG | -171.2 | improves by ≥ 30 bb/100 |
| Maniac | -119.9 | unchanged within noise (Maniac already plays wide) |

These bb/100 targets are direction-only and intentionally modest. Codex
flagged the original "within -30 of GTO-Lite" target as too ambitious
for hand-authored charts plus existing postflop logic.

For tighter signal, increase to 5000+ hands × 5 seeds in a follow-up
sweep if first pass shows directional but ambiguous signal.

### Optional: vs GTO-Lite HU sanity check

```bash
docker exec my-poker-face-hybrid-ai-backend-1 \
  python -m experiments.simulate_bb100 \
  --hands 2000 --seed 42 --opponent GTO-Lite --adaptation-bias 0.85
```

GTO-Lite is more disciplined than ManiacBot — better test of "are HU
charts plausibly correct" without exploitation muddling the signal.

## Risks / gotchas

1. **Chart quality matters.** A hand-crafted chart will have inaccuracies.
   Validation will catch gross errors (e.g., if 72o opens 100% accidentally)
   but subtle mispricing will only show in long-run sims. Be ready to
   iterate on specific hands based on per-decision logs.

2. **Personality distortion may over-amplify the chart change.** If the
   HU chart is ~2x wider than 6-max button range, a "loose" archetype
   distortion makes it ~3x wider. May need to tune deviation profiles
   for HU specifically. Watch validation closely.

3. **Sample size for action distributions.** With many entries having
   small probabilities (e.g. `{'raise_3bb': 0.7, 'jam': 0.05, 'call':
   0.15, 'fold': 0.10}`), the action mapper needs to handle the rare
   actions correctly. Verify resolve_preflop_sizing works for all
   actions used in HU.

4. **The validation gates use `simulate_bb100.py` HU mode**, which
   runs all archetypes vs the opponent. Make sure each archetype's
   anchors interact correctly with HU charts. Specifically: Nit's
   strong distortion vs an already-wide HU chart should still produce
   a tight-but-wider-than-6max Nit.

5. **Postflop SPR will be different.** Wider preflop ranges mean lower
   SPR on average postflop. The postflop classifier should still work
   because it computes SPR from current pot/stack at decision time. But
   the postflop strategy table entries that key on SPR buckets may have
   different frequencies. Watch for degraded postflop play.

## Effort estimate

Codex flagged the original estimate as optimistic. Updated:

- Chart data entry (Option B, hand-crafted): **2-3 days**. 1350 entries,
  using known Nash HU charts as reference. The chart is more than typing
  — need to pick a specific sizing convention, translate source formats,
  fill in scenario combinations the source is silent on, and validate
  per-entry probability sums.
- Chart loader + StrategyTable wrapper: **0.5 day**
- HU node builder + classifier: **0.5 day**
- Controller routing logic + tests: **1 day**
- Validation runs + analysis + chart calibration: **2-3 days**. First-pass
  validation will surface chart issues; expect 1-2 calibration rounds.
- Doc updates: **0.5 day**

**Total: 6-8 days** (was 4-7). Most variance is in chart calibration —
first-pass charts will surface issues that require iteration.

## Out of scope

- Solver-derived charts (Option A). Manual chart is sufficient for v1; can
  upgrade later.
- HU-specific postflop charts. Same architecture concern but bigger scope.
  Postflop classifier handles it adequately via SPR / texture buckets.
- Multi-stack-depth HU charts (different ranges for 20bb vs 100bb HU). Short-
  stack heuristic (Phase 6 Step B) already handles depth-aware action shaping.
  HU charts are calibrated for 100 BB; below that, short-stack heuristic
  kicks in correctly.
- Tournament HU (different from cash HU because ICM). Cash-style HU charts
  are appropriate for our use case.

## Files to create / modify

| File | Action | Description |
|---|---|---|
| `poker/strategy/hu_strategy_table.py` | **NEW** | HU-specific strategy table |
| `poker/strategy/data/hu_preflop_chart.csv` | **NEW** | Chart data |
| `poker/strategy/data/hu_preflop_chart_README.md` | **NEW** | Source/sizing convention spec — write BEFORE chart data entry |
| `poker/strategy/hu_preflop_classifier.py` | **NEW** (or extend existing) | Build HUPreflopNode from game state |
| `poker/strategy/nodes.py` | Modify | Add HUPreflopNode dataclass |
| `poker/tiered_bot_controller.py` | Modify | Route to HU table when num_players=2 |
| `flask_app/handlers/tiered_factory.py` | Modify | Load HU table alongside main table |
| `tests/test_strategy/test_hu_strategy_table.py` | **NEW** | Chart validation tests |
| `tests/test_strategy/test_tiered_bot_hu_routing.py` | **NEW** | Routing tests |
| `experiments/simulate_bb100.py` | Modify? | Maybe add HU-specific counter outputs |

## Cross-plan note

If Phase 6.6 (c-bet exploitation + confidence-weighted firing) ships
first, re-baseline these HU numbers before starting Phase 7. Phase 6.6
changes how exploitation fires at HU spots (per-aggressor stats from
ManiacBot), which will shift the bb/100 numbers in the validation table
above.

Conversely, if Phase 7 ships first, the wider HU preflop ranges will
change c-bet sample rates and postflop spots — re-baseline Phase 6.6
validation after Phase 7.

The two plans are mechanically independent but their validation numbers
are entangled.

## Reproducibility setup (for the next session)

Start from this commit or later:
```
67b947bd docs: short-stack heuristic smoke validation
```

The complete Phase 6 + 6.5 + Step B foundation should be in place. See
`docs/analysis/PHASE_6_VALUE_OVERRIDE_RESULTS.md` for the full pipeline
diagram and validated bb/100 baselines to compare against.

The HU validation baselines table from Phase 6.5 is the most important
reference — it shows where each archetype lands HU vs ManiacBot before
Phase 7. Phase 7's goal is to close that gap structurally (proper charts)
rather than patch-around-it (more exploitation).

## Reference: known HU starting hand ranges

For chart data entry, useful published references:

- **Nash push/fold ranges** (10-15 BB depth): well-defined equilibrium.
  Use for the SB-jam scenarios at short stacks.
- **HU cash Nash approximations** (100 BB depth):
  - Mathematics of Poker (Chen & Ankenman) — analytical
  - Modern Poker Theory (Acevedo) — readable
  - Online resources: WizardOfOdds.com HU charts, PocketFives Nash tables
- **General principles**:
  - SB open ~ 75% of hands. Skip only the absolute worst (32o, 42o, 52o,
    62o, 72o, 83o, 92o, 73o, T2o).
  - BB defend ~ 65% facing 3 BB open. Tighter facing 4 BB (~50%).
  - BB 3-bet for value: TT+, AQ+, AKs+. Light 3-bet: A2s-A5s, K9s+, etc.
  - SB call 3-bet: medium pairs, suited connectors, suited aces.
  - SB 4-bet for value: QQ+, AK. Bluff 4-bet: A5s, K5s.

Encode probabilities such that the SUM of (raise + call) at each entry
matches the desired VPIP for that hand at that position.

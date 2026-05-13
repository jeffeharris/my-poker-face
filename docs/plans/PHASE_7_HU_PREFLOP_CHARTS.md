---
purpose: Plan to build heads-up specific preflop strategy charts to fix the chart-mismatch leak
type: design
created: 2026-05-13
last_updated: 2026-05-13
---

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

Standard HU opening ranges (Nash-derived for cash, approximate):
- SB opens ~70-80% of hands (raise)
- BB defends ~60-70% of opens (call or 3-bet)
- 3-bet ranges, 4-bet ranges, etc. are wider than 6-max equivalents

Compare to 6-max where UTG opens ~15%, button opens ~40%, BB defends maybe
30-40% vs button. **The 6-max button range is roughly what HU SB plays.**
Currently our HU SB uses the BUTTON range from a 6-max chart, which is
much too tight.

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

2. **HU SB opening range ≈70-80% VPIP**: TAG (or any archetype) acting from
   SB in HU plays ~70-80% of hands voluntarily. Currently TAG opens ~30-40%.

3. **HU BB defense range ≈60-70%**: TAG facing an SB open defends ~60-70%
   of hands (call + 3-bet).

4. **Net bb/100 vs ManiacBot HU breaks even or positive** for at least Nit,
   TAG, LAG (which are the "honest poker" archetypes). Currently all four
   archetypes lose -90 to -195 bb/100. Target: TAG ≥ -50, LAG ≥ +0, Nit ≥ -80.
   Maniac archetype is already strong by design — target unchanged.

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

Less precise but FAST. For 169 canonical hands × 2 positions × 4 scenarios
(rfi, vs_open, 3bet, 4bet), that's ~1350 entries. A few hours of data entry.

**Recommendation: B for v1, plan to upgrade to A later.**

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

In `TieredBotController._get_ai_decision` (around line 156-205 preflop path):

```python
# Detect HU
num_active = sum(1 for p in game_state.players if not p.is_folded)
is_hu = num_active == 2

if is_hu:
    hu_node = build_hu_preflop_node(game_state, player_idx, canonical_hand)
    base_strategy = self.hu_strategy_table.lookup_with_fallback(hu_node, valid_actions)
else:
    node = build_preflop_node(game_state, player_idx, canonical_hand)
    base_strategy = self.strategy_table.lookup_with_fallback(node, valid_actions)

# ... rest of pipeline unchanged
```

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
hand,position,scenario,raise_3bb,raise_4x,call,fold
AA,SB,rfi,0.95,0.0,0.0,0.0
AA,BB,vs_open,0.0,0.85,0.15,0.0
22,SB,rfi,0.7,0.0,0.0,0.30
22,BB,vs_open,0.0,0.05,0.55,0.40
72o,SB,rfi,0.0,0.0,0.0,1.00
...
```

Plus a small Python loader.

## Tests

New unit tests:
- `tests/test_strategy/test_hu_strategy_table.py`:
  - All 169 hands present in chart at each position
  - Probabilities sum to 1.0 per entry (within float tolerance)
  - AA/KK opens 95%+ from SB
  - 72o opens 0% from SB
  - SB opening range total ≥ 70% of hands
  - BB defense range total ≥ 60%

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

Compare to the Phase 6.5 HU baselines (from
`docs/analysis/PHASE_6_VALUE_OVERRIDE_RESULTS.md`):

| Hero | Phase 6.5 HU (treatment) | Phase 7 target |
|---|---|---|
| Calling Station | -116.7 | better — should improve since CS opens wider |
| Rock | -87.7 | mild |
| Nit | -104.6 | ≥ -80 target |
| TAG | -135.8 | ≥ -50 target |
| LAG | -171.2 | ≥ 0 target |
| Maniac | -119.9 | unchanged (Maniac already plays wide) |

Run vs GTO-Lite HU too (more disciplined opponent — better test of "are
HU charts actually correct"):
```bash
docker exec my-poker-face-hybrid-ai-backend-1 \
  python -m experiments.simulate_bb100 \
  --hands 2000 --seed 42 --opponent GTO-Lite --adaptation-bias 0.85
```

Phase 6.5 baseline HU vs GTO-Lite (from `TIERED_VS_RULE_BOTS_REPORT.md`):
Maniac -65.7. With HU charts, expect ALL archetypes within -30 of GTO-Lite
(meaningful but not crushing loss).

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

- Chart data entry (Option B, hand-crafted): **1-2 days**. 1350 entries,
  using known Nash HU charts as reference. Most repetitive but needs
  careful entry to avoid typos.
- Chart loader + StrategyTable wrapper: **0.5 day**
- HU node builder + classifier: **0.5 day**
- Controller routing logic + tests: **1 day**
- Validation runs + analysis + chart calibration: **1-2 days**
- Doc updates: **0.5 day**

**Total: 4-7 days.** Most variance is in chart calibration — first-pass
charts may need refinement based on validation data.

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
| `poker/strategy/hu_preflop_classifier.py` | **NEW** (or extend existing) | Build HUPreflopNode from game state |
| `poker/strategy/nodes.py` | Modify | Add HUPreflopNode dataclass |
| `poker/tiered_bot_controller.py` | Modify | Route to HU table when num_players=2 |
| `flask_app/handlers/tiered_factory.py` | Modify | Load HU table alongside main table |
| `tests/test_strategy/test_hu_strategy_table.py` | **NEW** | Chart validation tests |
| `tests/test_strategy/test_tiered_bot_hu_routing.py` | **NEW** | Routing tests |
| `experiments/simulate_bb100.py` | Modify? | Maybe add HU-specific counter outputs |

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

---
purpose: Progress notes and next steps for range gate VPIP differentiation
type: guide
created: 2026-02-13
last_updated: 2026-02-13
---

# Range Gate: Progress & Next Steps

## What We Did (2026-02-13)

### 1. Extended Hand Tiers (`poker/hand_tiers.py`)
Added TOP_45 (76 hands), TOP_55 (93 hands), TOP_65 (110 hands), TOP_75 (127 hands)
following standard poker equity rankings. `is_hand_in_range()` cascade extended.

### 2. Replaced Position Clamps with Offsets (`poker/range_guidance.py`)
Old `POSITION_CLAMPS` mapped looseness linearly into tight min/max bounds
(e.g., early: 8-35%). This compressed all archetypes into a narrow range.
New `POSITION_OFFSETS` use looseness directly as range_pct with position shifts:
- early=-0.15, middle=-0.08, late=0.00, button=+0.05, SB/BB=-0.05
- Napoleon (0.79): EP=64%, BTN=84%. Buddha (0.22): EP=7%, BTN=27%.

### 3. Merged `hybrid-ai` Branch
Key changes merged:
- **Preflop equity now uses Monte Carlo** (`calculate_equity_vs_ranges`) instead
  of static tier buckets. QJo gets ~42% equity vs opponents, not flat 0.40.
- Style hints rewritten to lead with fold guidance (reduces TAG over-VPIP)
- Scorecard script (`experiments/scorecard.py`) for experiment quality metrics
- `shuffle_seating` config option removes position bias in experiments

### Experiment Results

| Experiment | Range Gate | Spread | Notes |
|------------|-----------|--------|-------|
| 113860 (old clamps) | OFF | 18.3pp | Baseline |
| 113860 (old clamps) | ON | 11.3pp | Gate COMPRESSED spread |
| 113861 (new offsets) | OFF | 20.0pp | Slightly better baseline |
| 113861 (new offsets) | ON | 19.7pp | Gate no longer compresses |

## Remaining Bottleneck: In-Range EV Labels

The range gate correctly marks hands as in-range for loose players, but
**the EV labels still treat in-range marginal hands as foldable.**

Example: Napoleon with QJo in-range, ~40% equity, facing 1BB open:
- FOLD: equity (40%) > required (28%) → [-EV] but **(recommended)**
- CALL: equity < required * 1.5 → [marginal]
- RAISE: equity (40%) < raise_plus_ev (0.55) → [-EV]

LLM sees "FOLD [-EV] (recommended)" and folds. VPIP stays low.

### Root Cause
`generate_bounded_options()` in `poker/bounded_options.py` (lines 520-640):
- `apply_range_bias` only fires for OUT-of-range hands (biases DOWN)
- There's NO upward bias for IN-range hands
- EV labels use raw equity vs pot odds — tight GTO math
- A LAG's in-range QJo gets the same EV labels as a TAG's in-range QJo

## Next Steps (Priority Order)

### 1. Profile-Aware EV Label Visibility
**Core insight**: EV labels are GTO math signals. A LAG doesn't fold QJo because
it's "-EV against pot odds" — they think "I have position, fire away." The `[-EV]`
tag next to a nudge phrase like "Fire away" contradicts the nudge, and the LLM
follows the math signal every time.

**Design**: Add `show_ev_labels: bool` to `OptionProfile`:
- TAG/Rock profiles: `show_ev_labels=True` — they think in math terms
- LAG/Station profiles: `show_ev_labels=False` — nudge phrases ARE the guidance
- `generate_bounded_options()` still computes honest EV internally (needed for
  math blocking, fallback selection, `_get_best_fallback_option()`)
- `_build_lean_prompt()` checks the profile flag when rendering — either shows
  `[+EV]` or omits the bracket entirely

**Result**: LAG sees options like:
```
1. FOLD  Junk hand. Save ammo.
2. CALL  Stay in the action.
3. RAISE 2BB  Fire away.
```
Instead of:
```
1. FOLD  [-EV]  Junk hand. Save ammo.
2. CALL  [marginal]  Stay in the action.
3. RAISE 2BB  [-EV]  Fire away.
```

**Files**: `poker/bounded_options.py` (OptionProfile), `poker/hybrid_ai_controller.py`
(`_build_lean_prompt`), `poker/nudge_phrases.py` (already correct, no changes needed)

### 2. Test Monte Carlo Preflop Equity
The hybrid-ai merge added Monte Carlo preflop equity (`calculate_equity_vs_ranges`
with empty board). Run a quick experiment to verify QJo now gets ~42-48% equity
vs opponent ranges instead of the old flat 0.40. This alone may shift some EV
labels from `-EV` to `marginal`/`+EV` for better hands.

### 3. Validate with A/B Experiment
Run `rangegate_ab_wider_tiers.json` config with EV label visibility changes.
Expected: Napoleon VPIP 60-80%, Joan 35-50%, Buddha 25-40%, 30+pp spread.
Use `shuffle_seating: true` to remove position bias confound.

### 4. Consider Profile-Aware (recommended) Tag
Currently all profiles use the same logic for the "(recommended)" tag on fold.
Could make this profile-aware: LAG profiles never recommend fold for in-range hands.
This may be unnecessary if EV label hiding (step 1) solves the problem.

## Key Files
- `poker/hand_tiers.py` — tier sets and `is_hand_in_range()`
- `poker/range_guidance.py` — `looseness_to_range_pct()`, `POSITION_OFFSETS`
- `poker/bounded_options.py` — `generate_bounded_options()`, EV labeling logic
- `poker/hybrid_ai_controller.py` — `_compute_range_data()`, `_build_rule_context()`
- `tests/test_psychology_v2.py` — `TestPositionOffsets`
- `tests/test_bounded_options_v2.py` — bounded options tests (195)

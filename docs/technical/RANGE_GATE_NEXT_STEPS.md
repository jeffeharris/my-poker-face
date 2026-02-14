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

### 1. Add In-Range EV Boost for Preflop
When `in_range=True` and `phase='PRE_FLOP'`, boost EV labels to encourage playing:
- **Call**: If in-range and equity > 30%, upgrade from `marginal` to `+EV`
  with rationale "Call X BB - in your opening range"
- **Raise**: If in-range and equity > profile threshold, upgrade raise labels
- **Fold**: If in-range with reasonable equity (>30%), change from
  `[-EV] (recommended)` to just `[-EV]` (remove recommended flag)
- This is the inverse of the existing out-of-range bias

### 2. Validate with A/B Experiment
Run `rangegate_ab_wider_tiers.json` config (already exists) with the EV boost.
Expected: Napoleon VPIP 60-80%, Joan 35-50%, Buddha 25-40%, 30+pp spread.

### 3. Test with Monte Carlo Equity
The hybrid-ai merge added Monte Carlo preflop equity. Run experiment to verify
QJo now gets ~42-48% equity instead of flat 0.40. This alone might fix some
of the EV label issues since more hands will clear the +EV thresholds.

### 4. Consider Profile-Aware (recommended) Tag
Currently all profiles use the same logic for the "(recommended)" tag on fold.
Could make this profile-aware: LAG profiles never recommend fold for in-range hands.

## Key Files
- `poker/hand_tiers.py` — tier sets and `is_hand_in_range()`
- `poker/range_guidance.py` — `looseness_to_range_pct()`, `POSITION_OFFSETS`
- `poker/bounded_options.py` — `generate_bounded_options()`, EV labeling logic
- `poker/hybrid_ai_controller.py` — `_compute_range_data()`, `_build_rule_context()`
- `tests/test_psychology_v2.py` — `TestPositionOffsets`
- `tests/test_bounded_options_v2.py` — bounded options tests (195)

---
purpose: Progress notes and next steps for range gate VPIP differentiation
type: guide
created: 2026-02-13
last_updated: 2026-02-15
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

## What We Did (2026-02-14)

### 1. Board Read Injection (`poker/board_analyzer.py`, `poker/hybrid_ai_controller.py`)
TAG/default profiles now see a 1-line board texture read in postflop lean prompts.
Suppressed in extreme tilted/shaken/dissociated states. 68 new tests, all passing.
Committed as `aa461cab`.

### 2. `nudge_show_ev` Flag (`poker/prompt_config.py`)
New `PromptConfig` field that shows EV labels alongside nudge phrases when
`composed_nudges=True`. Committed as `94e33138`.

### 3. Nudge/Rangegate 4-Arm A/B (Experiment 113863)

| Variant | Napoleon (LAG) | Alice (TAG) | Joan (TAG) | Buddha (TP) | Spread |
|---------|---------------|-------------|------------|-------------|--------|
| lean-nudges (nudges only) | 73.4% | 33.5% | 33.0% | 26.2% | **47.7pp** |
| lean-raw-ev (EV only) | 61.8% | 40.9% | 40.0% | 41.0% | **21.5pp** |
| lean-nudges-ev (both) | 58.5% | 21.4% | 24.1% | 18.8% | **40.0pp** |
| lean-nudges-rangegate | 80.3% | 36.3% | 34.6% | 25.9% | **54.4pp** |

**Key findings:**
- **EV labels act as a GTO anchor** — they flatten all players toward ~40% VPIP (21.5pp spread).
  Buddha/Alice/Joan all ~40% with raw EV, losing their tight personality completely.
- **Nudges alone** give strong spread (47.7pp) because the LLM follows personality-specific phrases.
- **Nudges + EV** produces odd effects: tight players get ULTRA-tight (Buddha 18.8%) because
  `[-EV]` + "Discipline pays" double-reinforces folding. Napoleon also drops (58.5%).
- **Nudges + rangegate is the clear winner** (54.4pp): Napoleon 80.3% — range gate tells him
  his marginal hands ARE in range, and nudges give personality-specific "fire away" guidance.
- **Decision quality trade-off**: lean-raw-ev has lowest blunder rate (27.8%) and EV lost (16.2k).
  Nudges-rangegate has higher blunder rate (41.2%) but much better personality differentiation.
  lean-nudges-ev is worst of all worlds — highest blunder rate (49.2%) WITH compressed Napoleon VPIP.

**Postflop fold rates** — lean-raw-ev has uniformly low postflop folds (3-6% for all players).
lean-nudges-rangegate shows better postflop differentiation (3.6% Napoleon vs 16.4% Buddha).

**Conclusion**: Profile-aware EV label visibility is confirmed as the next priority. The experiment
proves EV labels should be hidden for LAG profiles (nudges are sufficient) and shown for TAG/Rock
profiles (they benefit from math signals). Nudges + rangegate without EV labels is the target config.

### 4. Profile-Aware EV Label Visibility (Experiment 113867)

Implemented `show_ev_labels: bool` on `OptionProfile` and `style_hint: str` consolidation
(replacing standalone `STYLE_HINTS` dict). `PromptConfig.show_ev_labels: Optional[bool]`
provides A/B override (None=defer to profile, True/False=force).

Committed as `cc73d1e0`. Config: `experiments/configs/profile_ev_gating_test.json`.

3-way test: always-show vs per-profile vs always-hide (all using nudges+rangegate baseline).

| Variant | Napoleon (LAG) | Joan (TAG) | Alice (LP) | Buddha (TP) | Spread | Avg VPIP |
|---------|---------------|------------|------------|-------------|--------|----------|
| ev-always-show | 56.4% | 22.1% | 18.1% | 16.5% | 39.9pp | 28.3% |
| **ev-per-profile** | **72.7%** | **19.9%** | **23.9%** | **23.3%** | **52.8pp** | **35.0%** |
| ev-always-hide | 77.5% | 32.7% | 30.0% | 32.9% | 47.5pp | 43.3% |

**Key findings:**
- **Per-profile wins on spread** (52.8pp) — beats both always-show (39.9pp) and always-hide (47.5pp).
- **EV labels anchor tight players correctly**: TAG Joan 19.9% in per-profile (sees EV labels)
  vs 32.7% in always-hide. Math signals help tight players stay tight.
- **Hiding EV liberates LAG**: Napoleon 56.4% → 72.7% when EV hidden. GTO anchor removal
  lets loose players express aggression.
- **Always-hide inflates everyone**: avg VPIP 43.3%, even tight players play 30%+. Per-profile
  gives better bottom differentiation.
- **Combines best of both worlds**: LAG/LP get personality-driven loose play without math
  anchoring, while TAG/TP stay disciplined with EV label guidance.

**Progression across experiments:**

| Experiment | Config | Spread | Notes |
|------------|--------|--------|-------|
| 113861 | Range gate + offsets | 19.7pp | Offsets alone |
| 113863 | Nudges + rangegate | 54.4pp | Best before profile EV |
| 113867 | Nudges + rangegate + **per-profile EV** | **52.8pp** | Comparable spread, better structure |

Per-profile EV gating matches nudges-only spread while producing better-structured VPIP
distribution (tight players legitimately tight, not just nudge-driven). This is now the
recommended default config.

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

### ~~1. Profile-Aware EV Label Visibility~~ ✓ DONE
Implemented and validated in experiment 113867. See section above.

### 2. Test Monte Carlo Preflop Equity
The hybrid-ai merge added Monte Carlo preflop equity (`calculate_equity_vs_ranges`
with empty board). Run a quick experiment to verify QJo now gets ~42-48% equity
vs opponent ranges instead of the old flat 0.40. This alone may shift some EV
labels from `-EV` to `marginal`/`+EV` for better hands.

### 3. Validate with A/B Experiment
Run `rangegate_ab_wider_tiers.json` config with EV label visibility changes.
Expected: Napoleon VPIP 60-80%, Joan 35-50%, Buddha 25-40%, 30+pp spread.
Use `shuffle_seating: true` to remove position bias confound.

### 4. Board Read in Experiments
Board read feature is committed but not yet tested in an experiment. Design an
experiment with `board_read` to see if it affects postflop fold rates for TAG profiles.

### 5. Consider Profile-Aware (recommended) Tag
Currently all profiles use the same logic for the "(recommended)" tag on fold.
Could make this profile-aware: LAG profiles never recommend fold for in-range hands.
Experiment 113863 suggests this may be unnecessary if EV label hiding (step 1) solves
the problem — lean-nudges-rangegate already achieves 54.4pp spread without it.

## Key Files
- `poker/hand_tiers.py` — tier sets and `is_hand_in_range()`
- `poker/range_guidance.py` — `looseness_to_range_pct()`, `POSITION_OFFSETS`
- `poker/bounded_options.py` — `generate_bounded_options()`, EV labeling logic
- `poker/hybrid_ai_controller.py` — `_compute_range_data()`, `_build_rule_context()`
- `tests/test_psychology_v2.py` — `TestPositionOffsets`
- `tests/test_bounded_options_v2.py` — bounded options tests (195)

---
purpose: Calibration audit for HEADS_UP_POSITION_OFFSETS applied as equity values in T1-34
type: reference
created: 2026-05-15
last_updated: 2026-05-15
---

# T1-34 — HU equity offset calibration check

## Verdict: GATE BEHIND FLAG / DEMOTE TO T2

`HEADS_UP_POSITION_OFFSETS` (`button +0.30`, `big_blind +0.20`) were designed as **range-percentage offsets** ("play 30pp wider"), not equity adjustments. Reusing them as equity offsets in `generate_bounded_options()` is a category error confirmed by inline code comments. The magnitudes are 3-7x larger than the actual positional equity advantage in HU. BB +0.20 is theoretically backwards (OOP = equity cost, not gain). No solver, GTO, or equity-simulation data supports these values in an equity context.

## Spec source audit

`docs/technical/BOUNDED_OPTIONS_DECISION_FRAMEWORK.md:135-142` declares the values in a two-row table with **zero citations**. No solver output, GTO charts, equity calculation results, poker literature, or empirical sim data is referenced.

The definitive evidence is the inline comment at `poker/range_guidance.py:32-35`:

```python
HEADS_UP_POSITION_OFFSETS: Dict[str, float] = {
    'button': +0.30,        # BTN/SB in HU: play 30pp wider
    'small_blind': +0.30,   # SB acts first preflop in HU
    'big_blind': +0.20,     # BB: play 20pp wider
}
```

"Play 30pp wider" means **range percentage**, not equity. These constants were designed with exactly one purpose: widening hand-selection ranges in HU.

## Git / eval archaeology

- `docs/analysis/HEADS_UP_EVAL_REPORT.md` (Experiments 26-33, 2026-02-15/16): validated offsets for **VPIP** behavior. Experiment 28 result: TAG (Sun Tzu) VPIP went 51.4% (4-player) → 66.3% (HU), +14.9pp. Called "working as designed."
- `experiments/configs/heads_up_eval.json` hypothesis explicitly states: "HU games will show **15-25pp higher VPIP** across all archetypes due to wider position offsets (+0.30 vs +0.05)." — VPIP hypothesis, not equity.
- No commit, notebook, experiment config, or analysis doc generated these numbers from equity simulation. No A/B test exists of these values as equity adjustments.

The February 2026 validation is **not evidence** for the proposed equity use.

## Theoretical sanity check

### Real positional equity advantage in HU (100bb)

- GTO equilibrium: button wins ~54% of pots. Average equity delta from position: **+0.04 to +0.08**.
- High-end postflop equity realization advantage: **+0.10 to +0.13** for button across all boards.

### What +0.30 actually does

A BTN player with 40% raw equity becomes **70% effective** — a 2-tier hand upgrade, not a positional correction.

```python
# Selected HU preflop equities vs average opponent (approximate)
# Default raise_plus_ev threshold = 0.50
hands = {
    'AKs': 0.67,  # Strong — offset irrelevant (already above threshold)
    '87s': 0.54,  # Marginal → 0.84 effective (enormous overstatement)
    '22':  0.50,  # Coin-flip → 0.80 (treated as near-monster)
    'K7o': 0.48,  # Slightly behind → 0.78 (massive overcorrection)
    '72o': 0.38,  # Trash → 0.68 (gets +EV raise label — wrong)
}
# Any hand with raw equity >= 0.20 earns a +EV raise label after offset.
# A correct positional equity offset would be +0.05 to +0.10, not +0.30.
```

### BB +0.20 is directionally wrong postflop

BB is OOP postflop in HU. OOP costs equity realization (~-0.05 to -0.10 vs raw equity). BB defending wide preflop is already captured by the range-bias-disable that is currently implemented. Adding +0.20 to BB's equity in the options generator means BB gets +EV raise labels in spots where it is actually behind.

## Why demote, not implement

1. The constants are range-percentage offsets repurposed as equity offsets — a category error.
2. No solver / GTO / equity simulation data supports +0.30/+0.20 in an equity context.
3. Magnitudes are 3-7x larger than actual positional equity advantage.
4. BB +0.20 is directionally backwards for postflop decisions.
5. The existing HU implementation (range-bias disable, monster threshold 0.75, `heads_up_raise_plus_ev`/`heads_up_raise_neutral` overrides, nudge phrases) already addresses the HU aggression gap through correct mechanisms.
6. Current HU behavior was validated as "working as designed" in Feb 2026. No empirical evidence of a gap T1-34 must fix.

If the HU aggression gap is real after production observation, the correct immediate lever is the existing `heads_up_raise_plus_ev`/`heads_up_raise_neutral` profile overrides — purpose-built and already wired.

## Config flag design (if pursued)

Add to `poker/prompt_config.py`:

```python
# In PromptConfig dataclass:
hu_equity_offset: bool = False  # Apply HEADS_UP_POSITION_OFFSETS to effective equity in HU
```

Default `False` — no behavior change. New field; no migration required.

### A/B experiment config

```json
{
  "name": "hu_equity_offset_ab",
  "description": "A/B: HU equity offset enabled vs disabled. Measures aggression, VPIP, fold rate.",
  "num_tournaments": 20,
  "hands_per_tournament": 50,
  "num_players": 2,
  "model": "gpt-5-nano",
  "personalities": ["Sun Tzu", "Blackbeard"],
  "player_types": {"Sun Tzu": {"type": "lean"}, "Blackbeard": {"type": "lean"}},
  "control": {
    "label": "no-offset",
    "prompt_config": {
      "style_aware_options": true,
      "composed_nudges": true,
      "hu_equity_offset": false
    }
  },
  "variants": [
    {
      "label": "with-offset",
      "prompt_config": {
        "style_aware_options": true,
        "composed_nudges": true,
        "hu_equity_offset": true
      }
    }
  ]
}
```

### Success criteria for promoting to default

| Metric | Threshold |
|---|---|
| TAG HU VPIP | 60-75% (must not regress from current ~66%) |
| TAG HU PFR | 50-70% (currently ~53%) |
| LAG HU VPIP | 90-99% (currently ~94% — must not inflate beyond 99%) |
| TAG-LAG VPIP gap | ≥ 20pp (archetype differentiation preserved) |
| BTN fold rate | ≥ 5% (offset must not eliminate all folds) |
| CaseBot win rate | No worse than offset-off baseline |

If with-offset TAG VPIP exceeds 80% or fold rate drops below 3%, the magnitude is too large — halve it (+0.15 BTN / +0.10 BB) before promoting.

BB offset should be separately evaluated for postflop phases, where it is theoretically unjustified and may need to be 0.0 or negative.

## Recommended action

| Item | Action |
|---|---|
| T1-34 | **Demote to T2.** Do not block main merge. |
| `HEADS_UP_POSITION_OFFSETS` in equity | Do not apply without A/B gating. |
| Immediate HU aggression fix (if needed) | Use existing `heads_up_raise_plus_ev`/`heads_up_raise_neutral` overrides per profile. |
| Future | Add `hu_equity_offset: bool = False` to `PromptConfig`, run A/B, promote only if success criteria met. |
| BB offset specifically | If ever implemented, consider 0.0 or negative for postflop phases. |

## Cross-references

- `poker/range_guidance.py:31-36` — original constants and inline comments
- `docs/technical/BOUNDED_OPTIONS_DECISION_FRAMEWORK.md:130-142` — spec (uncited)
- `docs/analysis/HEADS_UP_EVAL_REPORT.md:269-279` — what the Feb 2026 eval validated (VPIP, not equity)
- `experiments/configs/heads_up_eval.json:4` — hypothesis stated as VPIP
- `docs/triage/HU_EQUITY_OFFSET_PLAN.md:155-157` — plan's own risk acknowledgment
- `poker/prompt_config.py:83-96` — existing PromptConfig flags (pattern reference)
- `poker/bounded_options.py:596-619` — proposed insertion point

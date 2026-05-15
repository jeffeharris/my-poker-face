---
purpose: Implementation plan for applying HU positional equity offsets in generate_bounded_options (T1-34)
type: spec
created: 2026-05-15
last_updated: 2026-05-15
---

# T1-34 — HU positional equity offset implementation plan

## Summary

The spec (`docs/technical/BOUNDED_OPTIONS_DECISION_FRAMEWORK.md:135-142`) mandates `HEADS_UP_POSITION_OFFSETS` (button +0.30, big_blind +0.20) be applied to effective equity in `generate_bounded_options()`. The offset exists in `poker/range_guidance.py` but is consumed only by `looseness_to_range_pct()` for range-percentage calculations — never for the equity value that drives EV labeling. The two paths are completely separate; **zero double-application risk**.

## Equity flow

```
HybridAIController._build_rule_context()
  calculate_equity_vs_ranges(hole_cards, community_cards, ...)   ← Monte Carlo, raw equity
  context['equity'] = equity                                      ← stored in rule_context dict

generate_bounded_options(context, profile, phase, ...)
  raw_equity = context['equity']                                  ← READ HERE (line 605)
  [MISSING: hu_offset not applied]
  equity consumed by:
    _should_block_fold(context)        reads context['equity'] (line 272)
    _should_block_call(context)        reads context['equity'] (line 319)
    call EV zone (line 629)            local equity var
    check penalty/value (lines 640-650) local equity var
    fold EV labels (lines 678-684)     local equity var
    _get_raise_options(context, ...)   reads context['equity'] (line 373)
    raise EV labels (lines 733-735)    local equity var
    all-in EV (lines 789-791)          local equity var

HEADS_UP_POSITION_OFFSETS (range_guidance.py)
  looseness_to_range_pct()             used here ONLY for range_pct calculation
  _compute_range_data()                calls looseness_to_range_pct
  context['in_range'], ['range_pct']   range gate — NOT equity
```

## Architecture decision

**Local `eff_equity` + patched context for callees.** Compute `eff_equity` at the top of `generate_bounded_options()` after the existing HU raise-threshold block. Replace the `equity` local variable binding. For the three callee functions that read `context['equity']` directly (`_should_block_fold`, `_should_block_call`, `_get_raise_options`), pass a shallow-copied `eff_context = {**context, 'equity': eff_equity}`. Keep original `context` for `apply_emotional_window_shift()` — its `_reapply_math_blocking` safety net should use raw equity.

**Why this scope:**
- Single insertion point, no new function parameters
- No mutation of caller-provided dict
- Safety net stays conservative

**Why not upstream** (in `_build_rule_context` or `_compute_range_data`): those callers don't know whether the consumer wants offset equity for decisions or raw equity for other purposes. `generate_bounded_options` is the correct scope — it already owns the HU-specific logic (threshold overrides, range-bias disable, monster threshold).

**Offset applies to all phases** (preflop and postflop). Spec specifies the offset without phase restriction. Button positional advantage exists postflop. The existing HU raise threshold overrides are preflop-only by design (separate concern).

## Code sketch

Insert at `poker/bounded_options.py` after line 601 (after HU raise threshold block), before line 603 (`options = []`). Add import at module top.

```python
# New import at module top (line 17, after hand_tiers import):
from .range_guidance import HEADS_UP_POSITION_OFFSETS, _game_position_to_range_key

# Inside generate_bounded_options(), insert after line 601:
# --- HU positional equity offset ---
raw_equity = context.get('equity', 0.5)
eff_equity = raw_equity
if is_heads_up:
    _pos = context.get('position') or ''
    _pos_key = _game_position_to_range_key(_pos) if _pos else ''
    _hu_offset = HEADS_UP_POSITION_OFFSETS.get(_pos_key, 0.0)
    eff_equity = max(0.0, min(1.0, raw_equity + _hu_offset))
    if _hu_offset != 0.0:
        logger.debug(
            f"[BOUNDED] HU equity offset: {raw_equity:.2f} + {_hu_offset:+.2f} "
            f"= {eff_equity:.2f} (pos={_pos_key})"
        )

# Replace line 605:
# OLD: equity = context.get('equity', 0.5)
# NEW:
equity = eff_equity
eff_context = {**context, 'equity': eff_equity} if eff_equity != raw_equity else context

# Replace callee calls:
# line 617: _should_block_fold(context, profile)   →  _should_block_fold(eff_context, profile)
# line 618: _should_block_call(context)            →  _should_block_call(eff_context)
# line 723: _get_raise_options(context, ...)       →  _get_raise_options(eff_context, ...)
# apply_emotional_window_shift stays: context (not eff_context)
```

Total diff: ~18 lines changed, 1 new import.

## Double-application check

| Path | Uses `HEADS_UP_POSITION_OFFSETS`? | Uses equity? |
|---|---|---|
| `looseness_to_range_pct()` | YES (range_pct only) | No |
| `_compute_range_data()` | Via `looseness_to_range_pct` (range_pct) | No |
| `generate_bounded_options()` (current) | No | YES (raw) |
| `generate_bounded_options()` (proposed) | No (reads result from new block) | YES (eff_equity) |

The two systems share the constant dict name but touch different outputs. No overlap.

## Interaction with HU profile overrides

The raise threshold overrides (`heads_up_raise_plus_ev`, `heads_up_raise_neutral`) lower the equity bar to earn a +EV raise label. The equity offset raises the apparent hand strength. They are **complementary**:

| Scenario | Raw Equity | Offset | Eff Equity | Threshold (default `hu_raise_plus_ev=0.50`) | Result |
|---|---|---|---|---|---|
| BTN, no fix | 0.40 | none | 0.40 | 0.50 | -EV raise |
| BTN, offset only | 0.40 | +0.30 | 0.70 | 0.50 | +EV raise (fixed) |
| BTN, threshold only (LAG) | 0.40 | none | 0.40 | 0.35 | neutral raise |
| BTN, both (LAG) | 0.40 | +0.30 | 0.70 | 0.35 | +EV raise |
| BB, no fix | 0.35 | none | 0.35 | 0.50 | -EV raise |
| BB, offset only | 0.35 | +0.20 | 0.55 | 0.50 | +EV raise (fixed) |

The offset resolves the primary aggression gap. The threshold overrides provide fine-grained style tuning on top.

## Test plan

Add to `tests/test_bounded_options_v2.py`:

```python
def test_hu_btn_equity_offset_surfaces_plus_ev_raise():
    """BTN with 40% raw equity gets +EV raise after +0.30 HU offset."""
    context = {
        'equity': 0.40, 'pot_total': 200, 'cost_to_call': 0,
        'player_stack': 1000, 'stack_bb': 10, 'big_blind': 100,
        'min_raise': 200, 'max_raise': 1000, 'valid_actions': ['check', 'raise'],
        'num_opponents': 1, 'position': 'button', 'phase': 'PRE_FLOP',
    }
    options = generate_bounded_options(context, STYLE_PROFILES['default'])
    raises = [o for o in options if o.action == 'raise']
    assert any(o.ev_estimate == '+EV' for o in raises)

def test_hu_bb_equity_offset():
    """BB with 35% raw equity gets +EV raise after +0.20 HU offset."""
    context = {**base, 'equity': 0.35, 'position': 'big_blind', 'num_opponents': 1}
    options = generate_bounded_options(context)
    assert any(o.ev_estimate == '+EV' for o in options if o.action == 'raise')

def test_no_hu_offset_multiway():
    """No offset applied when num_opponents > 1."""
    context = {**base, 'equity': 0.40, 'position': 'button', 'num_opponents': 2}
    options = generate_bounded_options(context)
    assert not any(o.ev_estimate == '+EV' for o in options if o.action == 'raise')

def test_equity_cap_at_1():
    """Offset never pushes effective equity above 1.0."""
    context = {**base, 'equity': 0.80, 'position': 'button', 'num_opponents': 1}
    options = generate_bounded_options(context)
    assert options
```

## Risks

**Over-aggressive button play.** BTN +0.30 is large. A BTN with 25% raw equity becomes 55% effective — may surface +EV raises on weak hands. Mitigation: `fold_equity_multiplier` and `_should_block_fold` math still gate catastrophic outcomes. Monitor fold/raise ratios in experiment data after deploy.

**BB defense over-passivity masked.** BB +0.20 surfaces more +EV calls/raises even in spots BB is behind. This is correct — BB in HU defends wide — but may look wrong in post-experiment analysis if annotators don't account for the offset.

**TieredBot and RuleBot unaffected.** `TieredBotController` uses solver strategy tables and does not call `generate_bounded_options()`. `RuleBotController` does not either. Fix benefits only `standard` (Hybrid) and `lean`. Any HU aggression gap in TieredBot is a separate issue.

**Position field may be None.** `context.get('position')` can be `None` if `_build_rule_context` fails to find the player in `table_positions`. The `if _pos else ''` guard handles this — offset defaults to 0.0.

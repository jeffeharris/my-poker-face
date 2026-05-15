---
purpose: Fix plan for action_mapper underestimating legal minimum raises in re-raise scenarios
type: reference
created: 2026-05-15
last_updated: 2026-05-15
---

# T2-38 — Strategy mapper min-raise fix plan

**Severity:** T2 (correctness — tiered bot over-commits chips in re-raise spots)

## Summary

Both `resolve_preflop_sizing` and `resolve_postflop_sizing` compute the minimum legal raise as `highest_bet + big_blind`. Poker rules require the raise increment to equal at least the size of the previous raise. The engine's `BettingContext.min_raise_to` uses `highest_bet + min_raise_amount` (= `highest_bet + last_raise_amount`) and silently sanitizes the bot's illegal amount upward, causing the bot to commit more chips than the sampled strategy intended.

## Callsites

| File | Line | Wrong expression |
|---|---|---|
| `poker/strategy/action_mapper.py` | 49 | `min_raise = highest_bet + big_blind` (preflop) |
| `poker/strategy/action_mapper.py` | 102 | `min_raise = highest_bet + big_blind` (postflop) |

No other files in the strategy module share this pattern. Other controllers read `game_state.min_raise_amount` directly (`controllers.py:1056`, `rule_based_controller.py:219`, `lean_bounded_controller.py:103`).

## Correct formula

No-limit hold'em rule: the minimum re-raise increment equals the preceding raise size in the same betting round.

```
min_raise_to = highest_bet + last_raise_amount
             = highest_bet + game_state.min_raise_amount   # (property at poker_game.py:170)
```

`game_state.min_raise_amount` returns `self.last_raise_amount`:
- Initialized to `current_ante` (BB) at hand start — `poker_game.py:709`, `start_new_hand():779`
- Reset to `current_ante` at each new betting round — `poker_state_machine.py:232`
- Updated to the actual raise increment on each raise — `poker_game.py:516`

When no raise has occurred in the current round, `last_raise_amount == BB == big_blind`, so the wrong and correct formulas agree (bug is dormant). After any raise, `last_raise_amount` exceeds BB and the bug fires.

**Concrete 3-bet example:**

```
BB = 100
Villain raises to 300  →  last_raise_amount = 200, highest_bet = 300

Wrong min:    300 + 100 = 400
Correct min:  300 + 200 = 500

Bot samples raise_2.5bb → target = round(2.5 × 100) = 250 → clamped to wrong_min = 400
Engine sanitizes 400 → 500
Bot over-commits 100 chips vs. sampled strategy intention.
```

The divergence gap `(wrong_min, correct_min)` = `(highest_bet + BB, highest_bet + last_raise_amount)` is zero until a raise occurs in the round. Worst case: small BB-relative or sub-minimum multiplier raises in 3-bet/4-bet spots.

## Code sketch

Both callsites are identical one-line fixes. The `big_blind` variable is still needed for BB-relative multiplier calculations; only `min_raise` changes.

**`resolve_preflop_sizing` — `action_mapper.py:48-49`:**
```python
# Before
highest_bet = game_state.highest_bet
min_raise = highest_bet + big_blind

# After
highest_bet = game_state.highest_bet
min_raise = highest_bet + game_state.min_raise_amount
```

**`resolve_postflop_sizing` — `action_mapper.py:101-102`:**
```python
# Same pattern
min_raise = highest_bet + game_state.min_raise_amount
```

For postflop open bets (`bet_` branch, `highest_bet = 0`), `last_raise_amount` was just reset to `current_ante` (BB) at the start of the betting round, so the fix produces the same value as the current code. **No behavior change for open bets; behavior changes only in re-raise spots.**

## Engine reference

- `poker/betting_context.py:43` — `min_raise_to = self.highest_bet + self.min_raise_amount`
- `poker/betting_context.py:85–92` — `validate_and_sanitize()` bumps sub-minimum raises to `min_raise_to`, logging: `"Amount $X below minimum, adjusted to $Y"`
- `poker/poker_game.py:498` — `player_raise()` calls `validate_and_sanitize()` before executing

This correction path silently fixes illegal amounts today, masking the bug.

## DB evidence

The tiered bot (`sharp`) writes no `prompt_captures` rows — solver tables only. Sanitization events log at DEBUG, not persisted. No column captures pre-sanitization raise_to.

Closest proxy for other bots:

```sql
SELECT player_name, phase, raise_amount, action_taken, created_at
FROM prompt_captures
WHERE action_taken = 'raise'
ORDER BY created_at DESC
LIMIT 40;
```

Cannot distinguish sanitized from intentional raises without pre-sanitization value. Direct evidence requires adding instrumentation at `validate_and_sanitize()` or logging the original `raise_to` before calling `player_raise()`.

## Side effects on sampling distribution

The strategy table samples abstract actions (`raise_2.5bb`, `raise_3x`) using EV-labelled options. **Option generation does not reference `min_raise`.** Sampling distribution unaffected. Divergence is purely at execution time: only when the computed concrete amount falls in the gap `[wrong_min, correct_min)` does the engine commit a different amount than the bot modeled.

## Test plan

Create `tests/test_action_mapper.py`:

```python
def test_preflop_3bet_uses_last_raise_amount():
    game_state = make_game_state(highest_bet=300, min_raise_amount=200, current_ante=100)
    result = resolve_preflop_sizing('raise_2.5bb', game_state, player)
    assert result.action == 'raise'
    assert result.raise_to >= 500  # not 400

def test_preflop_open_raise_unchanged():
    game_state = make_game_state(highest_bet=100, min_raise_amount=100, current_ante=100)
    result = resolve_preflop_sizing('raise_3x', game_state, player)
    assert result.raise_to == 300  # same as before fix

def test_postflop_reraise():
    game_state = make_game_state(highest_bet=400, min_raise_amount=300, current_ante=100)
    result = resolve_postflop_sizing('raise_100', game_state, player)
    assert result.raise_to >= 700  # 400 + 300

def test_postflop_open_bet_unchanged():
    game_state = make_game_state(highest_bet=0, min_raise_amount=100, current_ante=100)
    result = resolve_postflop_sizing('bet_half_pot', game_state, player)
    # last_raise_amount was just reset to BB; identical to current

def test_jam_unaffected():
    # all_in actions bypass min_raise clamp
    result = resolve_preflop_sizing('jam', game_state, player)
    assert result.action == 'all_in'
```

## Risks

**Low — `big_blind` variable still needed.** It's the base for BB-relative multiplier calculations (`raise_Nbb`). Do not remove it; only `min_raise` changes.

**Low — postflop `bet_` branch clamp.** The shared `min_raise` variable also clamps open bets. After fix, `min_raise = 0 + last_raise_amount = 0 + BB` (due to round reset). Identical to current code. No behavior change.

**None — no other component changes.** `BettingContext`, `player_raise`, strategy tables, and the tiered bot controller pipeline are all unmodified.

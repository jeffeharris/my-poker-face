# AI Decision Quality Improvement Plan

Based on analysis in `AI_DECISION_QUALITY_REPORT.md`, this document outlines the implementation plan and testing strategy.

---

## 1. Raise Cap Implementation (Game Logic)

### Problem
- 7.8% of betting rounds have 3+ raises, some with 21 raises
- Mistral-small has 30.4% raise war rate
- Creates unrealistic betting sequences

### Solution
Add `MAX_RAISES_PER_ROUND = 4` constant, unlimited for heads-up.

### Changes Required

**poker/poker_game.py:**
1. Add `raises_this_round: int = 0` to `PokerGameState` dataclass (line ~98)
2. Modify `player_raise()` to increment counter
3. Modify `player_all_in()` to increment counter when it's effectively a raise
4. Modify `current_player_options` property to exclude 'raise' when cap reached

**poker/poker_state_machine.py:**
1. Reset `raises_this_round = 0` when starting new betting round (line ~190)

### Testing Strategy

**Positive tests (should change):**
- Replay captures from extreme raise wars (7+ raises) - should now cap at 4
- Run small tournament with mistral-small and verify reduced raise war rate

**Negative tests (should NOT change):**
- Normal games with <4 raises per round - behavior unchanged
- Heads-up games - should still allow unlimited raises
- All-in situations - ensure all_in is still available even at raise cap

---

## 2. Bluff Check Prompt Guidance

### Problem
- AI goes all-in on river with 0-5% equity as "bluff"
- Gordon Ramsay: all-in with 4♦2♥ (0.9% equity), lost $4,906
- Inner monologue: "An all-in will scare him off"

### Solution
Add conditional `bluff_check` guidance when:
- Phase is RIVER
- Action involves large bet or all-in
- Hand strength is weak (no pair or worse)

### Proposed Prompt Text
```yaml
bluff_check: |-
  ⚠️ RIVER BLUFF WARNING:
  You're considering a big bet/all-in with a weak hand on the river.
  - Against opponents who've called multiple bets, fold equity is LOW
  - River bluffs require villain to fold - they rarely do after investing
  - With showdown approaching, value bets beat bluffs
  - If you can't beat a bluff, you probably shouldn't bluff
  - "Aggressive personality" doesn't mean bluffing with nothing
```

### Trigger Conditions
```python
def should_add_bluff_check(phase, hand_strength, action_type, pot_committed_ratio):
    return (
        phase == 'RIVER' and
        hand_strength in ('weak', 'marginal', 'no pair') and
        action_type in ('raise', 'all_in') and
        pot_committed_ratio < 0.5  # Not already pot-committed
    )
```

### Testing Strategy

**Test captures (bad all-ins):**
| ID | Player | Equity | EV Lost | Expected Result |
|----|--------|--------|---------|-----------------|
| 119342 | Batman | 3.6% | $7,184 | Should fold/check instead |
| 120580 | Gordon Ramsay | 0.9% | $4,906 | Should fold/check instead |
| 121617 | Gordon Ramsay | 0.2% | $3,928 | Should fold/check instead |
| 114534 | Donald Trump | 0.9% | $3,820 | Should fold/check instead |

**Validation:**
1. Replay each capture with `bluff_check` guidance
2. Verify action changes from all_in to fold/check
3. Check inner_monologue shows understanding of bluff risk

**Negative tests (should NOT change):**
- Value all-ins with strong hands (pairs, sets, straights)
- Semi-bluff all-ins on earlier streets with equity
- Short stack all-ins (survival mode)

---

## 3. Made Hand Prompt Guidance

### Problem
- AI misreads hand strength on board
- Dave Chappelle folded FOUR OF A KIND JACKS (100% equity)
- Buddha folded STRAIGHT FLUSH (98.8% equity)
- AI thinks it has "second pair" when it has quads

### Root Cause
Prompt shows "Your Cards: [J♥, 2♠]" but doesn't evaluate the combined hand.

### Solution
Add post-flop hand evaluation to prompt when board is dealt.

### Proposed Prompt Addition
```
Your made hand: Four of a Kind (Jacks) - MONSTER HAND
```

### Changes Required

**poker/prompt_manager.py or controllers.py:**
1. When building decision prompt, evaluate player's hand + board
2. Include made hand description in prompt

**poker/hand_evaluator.py:**
1. Add function to return human-readable hand description
2. Example: `evaluate_made_hand(hole_cards, board) -> "Straight Flush (9-high)"`

### Testing Strategy

**Test captures (folded monster hands):**
| ID | Player | Actual Hand | Equity | EV Lost | Expected |
|----|--------|-------------|--------|---------|----------|
| 1240 | Dave Chappelle | Four Jacks | 100% | $21,250 | Should raise/call |
| 118902 | Buddha | Straight Flush | 98.8% | $17,956 | Should raise/call |
| 127015 | Phil Ivey | Q-high Flush | 81.6% | $28,386 | Should raise/call |

**Validation:**
1. Add made hand info to prompt
2. Replay captures - verify AI recognizes hand strength
3. Action should change from fold to call/raise

**Negative tests (should NOT change):**
- Hands where AI correctly evaluated strength
- Pre-flop decisions (no board yet)
- Marginal hands where folding was correct

---

## 4. Testing Framework

### Replay Test Script Enhancement

Add batch testing capability to `replay_with_guidance.py`:

```python
def batch_test_guidance(guidance_name: str, capture_ids: List[int], expected_action: str):
    """Test guidance against multiple captures, report success rate."""
    results = []
    for cid in capture_ids:
        capture = get_capture(cid)
        result = replay_decision(capture, GUIDANCE_VARIANTS[guidance_name])
        results.append({
            'id': cid,
            'original': capture['action_taken'],
            'new': result['action'],
            'expected': expected_action,
            'success': result['action'] == expected_action
        })
    return results
```

### Pre/Post Comparison

For each change, run:
1. **Before**: Record baseline behavior on test set
2. **After**: Record new behavior with guidance
3. **Compare**: Verify improvements, check for regressions

### Regression Test Suite

Create a "golden set" of captures where current behavior is CORRECT:
- Good folds (weak hand, no pot odds)
- Good calls (pot odds justify)
- Good raises (value betting)
- Good all-ins (short stack, strong hand)

Run these after each change to ensure no regressions.

---

## 5. Implementation Order

1. **Raise Cap** (game logic) - simplest, lowest risk
2. **Bluff Check** (prompt guidance) - targeted fix, easy to test
3. **Made Hand** (prompt enhancement) - more complex, requires evaluator changes

---

## 6. Success Metrics

| Issue | Current Rate | Target |
|-------|--------------|--------|
| Raise wars (3+) | 7.8% | < 3% |
| Bad all-ins (<15% equity) | 11.4% | < 5% |
| Folding monster hands | ~20 cases | 0 |
| Pot-committed folds | 670 | < 100 |

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | Jan 2026 | Initial plan |

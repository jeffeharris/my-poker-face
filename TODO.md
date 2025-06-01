# TODO List for My Poker Face

## HandEvaluator Issues

| Issue | Description | Status | Priority | Notes |
|-------|-------------|--------|----------|-------|
| Flush Bug | Flush evaluation returns all cards of suit instead of best 5 | ✅ Fixed | High | Fixed in `_check_flush()` - now returns only best 5 cards |
| Return Value Inconsistency | `_check_straight()` returns 3 values on failure but 5 on success; other methods always return 5 | ✅ Fixed | High | Fixed - now all methods consistently return 5 values: (bool, hand_values, kicker_values, suit, description) |
| Missing Unit Tests | HandEvaluator lacks comprehensive unit tests | 🔴 Open | High | Need tests for all hand types and edge cases |
| Kicker Handling | Inconsistent kicker value handling across methods | 🔴 Open | Medium | Some methods return single kicker in list, others return multiple |
| Ace-Low Straight | No support for A-2-3-4-5 straight | 🔴 Open | Medium | Common poker hand not recognized |
| Hand Rank Values | Hand rankings start at 1 (Royal Flush) instead of standard poker rankings | 🔴 Open | Low | Could cause confusion when comparing to standard poker resources |

## Other Code Issues

| Issue | Description | Status | Priority | Location |
|-------|-------------|--------|----------|----------|
| All-in Bug | Mentioned bug with flush after all-in | 🟡 Partially Fixed | High | `hand_evaluator.py:47-52` - flush fix may resolve this |
| Missing Error Handling | `determine_winner` lacks error handling | 🔴 Open | High | `poker_game.py:610` |
| Hardcoded Secret Key | Security vulnerability in Flask app | 🔴 Open | High | `flask_app/ui_web.py` |
| AI Chat Disabled | AI chat functionality commented out | 🔴 Open | Medium | `poker_game.py:190` |
| Player Hand Reset | TODO about resetting folded player's hand | 🔴 Open | Medium | `poker_game.py:416` |
| Betting Round Logic | Needs refinement | 🔴 Open | Medium | `poker_state_machine.py:163` |
| Hand Reset Verification | Missing verification | 🔴 Open | Medium | `poker_state_machine.py:204` |
| Remove NONE Action | NONE should not be a PlayerAction | 🔴 Open | Low | `poker_action.py:13` |
| PokerAction Tests | Missing unit tests | 🔴 Open | Medium | `poker_action.py:16` |
| Hole Card Evaluation | Re-introduce for AI decisions | 🔴 Open | Medium | `poker_player.py:318` |
| Position Tracking | Decision needed on implementation | 🔴 Open | Low | `poker_player.py:379` |
| Assistant to_dict Bug | Uses `__name__` instead of `type` | 🔴 Open | Medium | `core/assistants.py:211` |

## Testing Gaps

| Test Area | Description | Status | Priority |
|-----------|-------------|--------|----------|
| HandEvaluator Unit Tests | Complete test coverage for all hand types | 🔴 Open | High |
| Side Pot Calculations | Test complex multi-way all-in scenarios | 🔴 Open | High |
| State Machine Transitions | Test all valid/invalid state transitions | 🔴 Open | High |
| WebSocket Integration | Test real-time game updates | 🔴 Open | Medium |
| AI Decision Making | Test AI player logic | 🔴 Open | Medium |
| Error Recovery | Test game recovery from errors | 🔴 Open | Medium |

## Legend
- ✅ Fixed/Complete
- 🟡 In Progress/Partially Fixed
- 🔴 Open/Not Started
- 🟢 Won't Fix/Deferred
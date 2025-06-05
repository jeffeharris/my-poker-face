# Known Bugs

## 1. Loading Saved Games Missing Elasticity Data
**Status**: Fixed  
**Severity**: Medium  
**Description**: When loading a saved game from the database, the elasticity manager and pressure tracking systems are not restored, causing the elasticity debug panel and pressure stats to not work for loaded games.

**Steps to Reproduce**:
1. Start a new game
2. Play a few hands
3. Refresh the page (which loads from save)
4. Click "Show Debug" or "Show Stats"
5. Panels show no data

**Expected**: Elasticity and pressure systems should be initialized when loading saved games.

**Potential Fix**: Add elasticity manager and pressure detector initialization in the game loading logic (`api_game_state` and `game` routes).

---

## 2. Game Crashes at Showdown Due to Card Type Mismatch
**Status**: Fixed  
**Severity**: High  
**Description**: The game crashes when evaluating hands at showdown because cards are sometimes dicts and sometimes Card objects, causing AttributeError in HandEvaluator.

**Error**:
```
AttributeError: 'dict' object has no attribute 'value'. Did you mean: 'values'?
  File "/home/jeffh/projects/my-poker-face/poker/hand_evaluator.py", line 53, in __init__
    self.ranks = [card.value for card in cards]
```

**Steps to Reproduce**:
1. Play a hand to showdown
2. Game crashes when detecting pressure events

**Root Cause**: Mixed card representations - some parts of the code use Card objects while others use dict representations.

**Potential Fix**: Update `pressure_detector.py` to handle both Card objects and dict representations when creating HandEvaluator instances.

---

## 3. Pressure Stats Event Names Overwriting Player Names
**Status**: Fixed  
**Severity**: Low  
**Description**: In the pressure stats tracking, event names (like "fold_under_pressure") are being stored instead of player names in the leaderboards.

**Steps to Reproduce**:
1. Play several hands
2. Open "Show Stats"
3. Look at "Biggest Winners" - shows action names instead of player names

**Root Cause**: In `detect_and_apply_pressure`, the event tuple structure is `(event_name, [affected_players])` but it's being parsed incorrectly.

**Potential Fix**: Correct the tuple unpacking in the stats recording logic.
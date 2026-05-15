---
purpose: Pre-main blocker — c-bet detector silently drops all-in flop c-bets, undercounting fold-to-cbet
type: reference
created: 2026-05-15
last_updated: 2026-05-15
---

# C-bet detector excludes all-in as a valid c-bet

**Severity:** T1 (correctness — opponent model stats wrong)
**Confidence:** 95%
**Discovered:** pre-main review, 2026-05-15
**File:** `poker/memory/cbet_detector.py:145`

## The bug

C-bet detection in `poker/memory/cbet_detector.py` has three steps. Step 3 (which sets `_cbet_made` and builds the facing-opponent set) has an action-type filter:

```python
if (
    phase == 'FLOP'
    and action in ('bet', 'raise')   # <-- 'all_in' is missing
    and player_name == self._preflop_raiser
    and not self._cbet_made
):
```

Steps 1 (preflop aggressor tracking) and 2 (PFR-attempt tracking) correctly include `all_in`. The step-3 omission means: a preflop raiser who shoves the flop has their PFR-attempt recorded (step 2 fires with `attempted=True`), but `_cbet_made` stays False and `_players_facing_cbet` is never built. Opponents who face that all-in c-bet never get a `fold_to_cbet` response emitted.

## Why it matters

- Low-SPR play (short stacks, deep pots) frequently sees all-in c-bets.
- These all-in flops are exactly when fold-to-cbet stats are most diagnostic.
- Bots and coaches downstream consume `fold_to_cbet_pct` to classify opponents. Systematic undercount means:
  - Opponents who fold to all-in c-bets look like calling stations.
  - Bluff-frequency calibration is wrong against these players.
  - Coach advice misreads opponent profiles.

## Fix

```python
if (
    phase == 'FLOP'
    and action in ('bet', 'raise', 'all_in')   # include all_in
    and player_name == self._preflop_raiser
    and not self._cbet_made
):
```

## Test plan

Add to `tests/test_memory/`:
```python
def test_cbet_detector_counts_all_in_shove():
    detector = CbetDetector()
    detector.observe_action('A', 'raise', 'PRE_FLOP')
    detector.observe_action('B', 'call', 'PRE_FLOP', is_facing_bet=True)
    detector.observe_action('A', 'all_in', 'FLOP')  # shove the flop
    responses = detector.observe_action('B', 'fold', 'FLOP')
    assert any(r.player_name == 'B' and r.folded for r in responses)
```

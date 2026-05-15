---
purpose: Empirical proof that T2-54 test failure is stale-test, not regression — pre/post archetype comparison with deterministic scenario traces
type: reference
created: 2026-05-15
last_updated: 2026-05-15
---

# T2-54 — empirical regression check

**Verdict: STALE TEST. Definitively not a regression.**

Addresses Codex's pushback: "stale test needs proof — compare pre/post distributions across tight, loose, aggressive, passive archetypes."

## Method

`create_mock_response` in the failing test is a pure deterministic function over personality JSON data. No LLM call is made — the mock short-circuits at `Assistant.chat`. Running with seed=42 is equivalent to static trace: read aggression from JSON, apply the threshold tree, read the action.

**Data sources:**
- Pre-merge: `/home/jeffh/projects/my-poker-face/poker/personalities.json` (main branch, `personality_traits.aggression`)
- Post-merge: `poker/personalities.json` (current branch, `anchors.baseline_aggression`)

`create_mock_response` handles both formats (test file lines 36-42).

## Pre/Post anchor data — 4 archetypes

| Personality | Archetype | Pre aggression | Post `baseline_aggression` | Delta | Post `baseline_looseness` |
|---|---|---|---|---|---|
| Ebenezer Scrooge | tight-passive | 0.20 | 0.45 | **+0.25** | 0.22 |
| Bob Ross | tight-passive | 0.10 | 0.10 | 0 | 0.18 |
| Blackbeard | loose-aggressive | 0.90 | 0.90 | 0 | 0.87 |
| Queen of Hearts | tight-aggressive | 0.95 | 0.95 | 0 | 0.88 |

## Broader sample — 8 additional characters

| Personality | Pre | Post | Delta |
|---|---|---|---|
| Abraham Lincoln | 0.30 | 0.40 | +0.10 |
| Buddha | 0.30 | 0.15 | -0.15 |
| Alice | 0.30 | 0.25 | -0.05 |
| A Mime | 0.50 | 0.50 | 0 |
| Winston Churchill | 0.60 | 0.60 | 0 |
| Napoleon | 0.80 | 0.80 | 0 |
| The Honey Badger | 0.95 | 0.95 | 0 |
| Wicked Witch | 0.80 | 0.80 | 0 |

**Pattern:** bidirectional, per-character. Not a systematic offset. Scrooge +0.25, Buddha -0.15. Most unchanged.

## Scenario trace (deterministic)

Decision tree (`create_mock_response` lines 44-58):
1. `aggression > 0.8` → raise
2. `aggression < 0.3` → fold
3. `bluff_tendency > 0.6` → raise
4. else → call

Post-merge `bluff_tendency` = `baseline_aggression × 0.6 + baseline_looseness × 0.4`

### Pre-merge results (main branch)

| Personality | aggression | bluff_tendency | Action | Assertion |
|---|---|---|---|---|
| Scrooge | 0.20 | 0.20 | fold (branch 2) | `assertEqual fold` → PASS |
| Bob Ross | 0.10 | 0.30 | fold (branch 2) | `assertEqual fold` → PASS |
| Blackbeard | 0.90 | 0.80 | raise (branch 1) | `assertIn [raise,call]` → PASS |
| Queen of Hearts | 0.95 | 0.60 | raise (branch 1) | `assertEqual raise` → PASS |

### Post-merge results (current branch)

| Personality | aggression | bluff_tendency | Action | Assertion |
|---|---|---|---|---|
| Scrooge | 0.45 | 0.45×0.6+0.22×0.4 = 0.358 | call (branch 4) | `assertEqual fold` → **FAIL** |
| Bob Ross | 0.10 | 0.10×0.6+0.18×0.4 = 0.132 | fold (branch 2) | `assertEqual fold` → PASS |
| Blackbeard | 0.90 | 0.90×0.6+0.87×0.4 = 0.888 | raise (branch 1) | `assertIn [raise,call]` → PASS |
| Queen of Hearts | 0.95 | 0.95×0.6+0.88×0.4 = 0.922 | raise (branch 1) | `assertEqual raise` → PASS |

## Relative ordering — preserved or flattened?

Codex's critical question: are tight characters still more passive than loose ones?

- Pre-merge rank: `Bob Ross=fold = Scrooge=fold < Blackbeard=raise = QoH=raise`
- Post-merge rank: `Bob Ross=fold < Scrooge=call < Blackbeard=raise = QoH=raise`

**Post-merge ordering is more differentiated, not flattened.** Scrooge moved from fold to call — still strictly less aggressive than Blackbeard/QoH. Relative ordering is preserved and refined.

## Why Scrooge changed (semantic interpretation)

The 9-anchor refactor decomposed single-axis `aggression` into two orthogonal concepts:
- `baseline_looseness` — hand selection width (how often the player enters pots)
- `baseline_aggression` — in-pot betting tendency

Scrooge's tightness is now correctly encoded in `baseline_looseness=0.22` (very tight range). Raising `baseline_aggression` to 0.45 reflects that within his small set of entered pots, he is not purely passive — he holds and calls with strong hands. The old `aggression=0.20` conflated hand-selection tightness with in-pot passivity. The new value is semantically more accurate.

## Verdict: STALE TEST

Evidence:

1. **Only Scrooge changed** among the 4 archetypes; 3 of 4 are identical post-merge.
2. **Broader sample shows bidirectional changes** — not a systematic upward shift.
3. **Tight characters did not uniformly become less foldy** — Bob Ross (also tight-passive) is unchanged at 0.10.
4. **Post-merge relative ordering is preserved and more differentiated.**
5. **Scrooge's change is semantically correct** — looseness now carries "tight", aggression carries "in-pot behavior".

One character failing while 3 pass is the signature of data drift, not a systematic regression.

## Test fix — apply Option A

Replace `tests/test_personality_responses.py:244-247` assertions with:

```python
self.assertEqual(results["Queen of Hearts"]["action"], "raise")   # aggression=0.95
self.assertIn(results["Blackbeard"]["action"], ["raise", "call"]) # aggression=0.90
self.assertEqual(results["Bob Ross"]["action"], "fold")           # aggression=0.10
self.assertEqual(results["Ebenezer Scrooge"]["action"], "call")   # aggression=0.45 (mid-range)

# Diversity invariant: not all personalities returned the same action
actions = [r["action"] for r in results.values()]
self.assertGreater(len(set(actions)), 1, "All personalities returned identical actions")
```

Update inline comment from "Low aggression (0.2) = fold" to "Mid aggression (0.45) = call; tightness is in baseline_looseness=0.22".

## Guard test to prevent recurrence

```python
def test_personality_anchors_within_test_bounds(self):
    """Fail explicitly when anchor data drifts past create_mock_response zone boundaries."""
    bounds = {
        "Ebenezer Scrooge": (0.30, 0.80),  # call zone
        "Bob Ross":          (0.00, 0.30),  # fold zone
        "Blackbeard":        (0.80, 1.00),  # raise zone
        "Queen of Hearts":   (0.80, 1.00),  # raise zone
    }
    for name, (lo, hi) in bounds.items():
        p = AIPokerPlayer(name=name, starting_money=10000)
        cfg = p.personality_config
        agg = cfg.get('anchors', {}).get('baseline_aggression',
              cfg.get('personality_traits', {}).get('aggression', 0.5))
        self.assertGreaterEqual(agg, lo, f"{name}: aggression {agg} < floor {lo}")
        self.assertLessEqual(agg, hi, f"{name}: aggression {agg} > ceiling {hi}")
```

## Key files

- `tests/test_personality_responses.py:44-57` — deterministic mock decision tree
- `tests/test_personality_responses.py:244` — stale `assertEqual("fold")` assertion
- `poker/personalities.json:985-999` — Scrooge post-merge anchors
- `/home/jeffh/projects/my-poker-face/poker/personalities.json:800-820` — Scrooge pre-merge `personality_traits`
- `tests/conftest.py:56-80` — `load_personality_from_json` (dual-format resolution)
- `docs/triage/PERSONALITY_DETERMINISM_INVESTIGATION.md` — prior agent's root-cause write-up (confirmed)

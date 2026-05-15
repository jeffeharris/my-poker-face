---
purpose: T2-54 root cause — personality determinism test regression is stale test assertion, not game-logic bug
type: reference
created: 2026-05-15
last_updated: 2026-05-15
---

> **✅ VALIDATED BY `PERSONALITY_REGRESSION_EMPIRICAL_CHECK.md`.**
> Round-2 investigation confirmed this verdict empirically by comparing pre/post anchor data across 12 characters and tracing the deterministic mock decision tree. Only Scrooge changed among the 4 archetypes; broader sample is bidirectional (Buddha went DOWN -0.15); relative ordering preserved and more differentiated post-merge. Implementation should follow the empirical-check doc, which also adds an anchor-zone guard test to prevent recurrence.

# T2-54 — Personality determinism regression investigation

**Verdict:** Stale test assertion, not a bug in game logic.

## Summary

The test `tests/test_personality_responses.py:244 :: test_same_scenario_different_responses` asserts Ebenezer Scrooge returns `"fold"` when facing a bet with pocket 7s on a K-Q-J board. It now returns `"call"`. The failure is caused by **personality data drift**: Scrooge's `baseline_aggression` was raised from ~0.2 to **0.45** during the 9-anchor psychology refactor, crossing the test's hardcoded mock fold threshold (`aggression < 0.3`). No corresponding update was made to the test.

## Test description

`tests/test_personality_responses.py:201-247` iterates four personalities (Ebenezer Scrooge, Blackbeard, Queen of Hearts, Bob Ross) and calls `create_mock_response(player_name, "facing_bet")`. This helper reads `baseline_aggression` from `personalities.json` and routes to an action via hardcoded thresholds:

```python
if aggression > 0.8:    → raise
elif aggression < 0.3:  → fold     # ← Scrooge used to land here (aggression≈0.2)
elif bluff_tendency > 0.6: → raise
else:                   → call     # ← Scrooge now lands here (aggression=0.45)
```

The mock return value flows through `Assistant.chat` (mocked) and returns from `get_player_response`. Line 244 assertion: `action == "fold"`, with inline comment "Low aggression (0.2) = fold".

## Root cause

**Personality data changed; the test's threshold did not.**

In `poker/personalities.json:990`, Ebenezer Scrooge's `baseline_aggression` is currently **0.45**. The test's comment says "Low aggression (0.2) = fold" — encoding the value at time of writing. The 9-anchor psychology refactor (merged into `development` in the 330-commit range) raised Scrooge's `baseline_aggression` to 0.45 to reflect a more nuanced characterization: miserly/tight in entry frequency (looseness=0.22) but not purely passive once in a pot.

Current computed values:
- `aggression = 0.45` — does not satisfy `> 0.8` or `< 0.3`
- `bluff_tendency = 0.45 * 0.6 + 0.22 * 0.4 = 0.358` — does not satisfy `> 0.6`
- Falls through to `action = "call"`

The other three personalities still pass their assertions:
- Bob Ross: `baseline_aggression = 0.10` → `< 0.3` → fold ✓
- Blackbeard: `baseline_aggression = 0.90` → `> 0.8` → raise ✓
- Queen of Hearts: `baseline_aggression = 0.95` → `> 0.8` → raise ✓

## Verdict — stale test, not a bug

The new behavior (`call` for Scrooge) is **more correct** relative to the current personality design. Scrooge at aggression=0.45 is described as "miserly and tight, guards every chip", which is well-reflected in his low looseness (0.22) — he rarely enters pots. But within pots he entered, mid-range aggression means he does not auto-fold to pressure. The personality data change was intentional (psychology refactor tuning), and the test's hardcoded threshold became stale.

This is **not a regression in game behavior** — it is a test encoding a personality-specific constant that was valid at time of writing but has since drifted.

## Fix plan

**Do not blindly change `assertEqual` to `"call"`.** The correct fix depends on what invariant the test is meant to protect.

### Option A (recommended) — Assert diversity, not specific actions

The test's true intent is "personalities produce different responses to the same scenario". Rewrite assertions to verify the four players don't all return the same action, and that high-aggression players take more aggressive actions than low-aggression players:

```python
# High aggression → aggressive action
self.assertIn(results["Blackbeard"]["action"], ["raise", "call"])
self.assertEqual(results["Queen of Hearts"]["action"], "raise")
# Low aggression → passive action
self.assertEqual(results["Bob Ross"]["action"], "fold")
# Scrooge: medium aggression, call is correct
self.assertEqual(results["Ebenezer Scrooge"]["action"], "call")
# Verify diversity
actions = [r["action"] for r in results.values()]
self.assertGreater(len(set(actions)), 1, "All personalities returned the same action")
```

### Option B — Lower the fold threshold in the mock

Change `aggression < 0.3` to `aggression < 0.5` in `create_mock_response`. Makes Scrooge fold again. Paper-over — would re-break if personality data is tuned again.

### Option C — Pin personality data in the test

Have `create_mock_response` use fixed trait values instead of reading from `personalities.json`. Decouples the test from personality configuration, but loses the integration-y aspect.

**Recommendation:** Option A. Cleanest, most resilient, and matches the test's actual intent.

## Prevention

Root structural issue: `create_mock_response` couples mock-decision thresholds to live personality data without asserting compatibility.

1. Add docstring to `create_mock_response` listing the assumed aggression ranges per personality with a note to update when anchors change.
2. Consider a separate `test_personality_data_within_expected_ranges` test that asserts each named personality's aggression anchor is within the range assumed by the mock decision tree. Explicit failure on drift.
3. When tuning anchor values in `personalities.json`, check `tests/test_personality_responses.py` for hardcoded threshold dependencies.

## Key files

- `tests/test_personality_responses.py:44-57` — mock decision tree
- `tests/test_personality_responses.py:244` — failing assertion
- `poker/personalities.json:985-999` — Scrooge anchors (`baseline_aggression: 0.45`)
- `tests/conftest.py:56-80` — `load_personality_from_json` helper
- `poker/poker_player.py:453-479` — `get_player_response` (mock routes through here)

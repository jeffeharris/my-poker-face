---
purpose: Tech-debt note — replace the implicit value/bluff weight-threshold API in depth derivation with an explicit intent tag
type: design
created: 2026-06-11
last_updated: 2026-06-11
status: proposed
---

# Depth derivation reads 3-bet *intent* from a weight threshold (implicit API)

## The problem

`generate_depth_charts.t_vs_open` decides how a hand behaves at shallower stacks
by **inferring whether a raise is value or a bluff from its weight**:

```python
VALUE_RAISE_THRESHOLD = 0.50
is_value = raise_ >= VALUE_RAISE_THRESHOLD   # → jams the whole continue range at 25bb
```

So a single magic number, `0.50`, is the contract between the 100bb chart's
*composition* and the depth charts' *derivation*. That is an **implicit API**:
the chart author has to encode "this is a value 3-bet" / "this is a bluff" by
choosing a raise weight on the correct side of a cliff they can't see, and the
depth code silently reinterprets that weight as intent.

### Why it bites

- **Range composition is constrained by a derived artifact.** When building the
  `vs_open` generator (`build_vs_open.py`, PREFLOP_DEFENSE_REGEN §3) the merged
  vs-wide-open construction couldn't freely set 3-bet frequencies: any bluff
  hand had to stay `< 0.50` or it would (wrongly) jam at 25bb, and any value
  hand had to stay `≥ 0.50` or it would (wrongly) be dropped. The 100bb shape is
  hostage to a 25bb derivation rule.
- **The boundary hand is ambiguous.** A greedy budget fill can hand a *value*
  hand a partial weight near the cliff; at, say, `0.40` it is silently demoted
  to "bluff" for depth purposes even though it was selected as value.
- **No one can audit intent.** `raise_3x: 0.45` does not say whether the author
  meant a frequent bluff or a thin value bet — only the cliff guesses.

## The fix

Add an **explicit intent tag** to the chart cell and have depth derivation read
it directly instead of inferring from weight:

```json
"AJs": {"raise_3x": 0.85, "call": 0.1, "fold": 0.05, "intent": "value"}
"A5s": {"raise_3x": 0.35, "fold": 0.65, "intent": "bluff"}
```

```python
# generate_depth_charts
is_value = cell.get("intent") == "value"   # explicit; weight no longer overloaded
```

Then the 100bb composition and the shallow-stack derivation are decoupled: a
generator can set any frequency it wants and separately declare intent. Migration
is mechanical — the generators already *know* each hand's role (they assign it),
so they emit the tag for free; depth derivation switches from `>= 0.50` to the
tag with a one-line fallback for legacy cells.

Scope: **`vs_open` only.** Only `t_vs_open` infers value/bluff from the raise
weight (`raise_3x >= 0.50`). `t_vs_3bet` gates on the **fold** weight instead
(`fold >= J25_VS3BET_FOLD_GATE`, currently 0.50) — a continuous "do I continue"
signal with no ambiguous band, so it does *not* have this implicit-API problem
and does not need the tag. (An earlier version of this doc wrongly lumped
`vs_3bet` in — `vs_3bet`'s 4-bet bluffs are kept depth-safe by carrying
`fold >= the gate`, not by a sub-threshold raise weight.)

## Interim guard (until the tag lands)

`build_vs_open._lint` refuses to write any node with a 3-bet weight in the
ambiguous band **(0.45, 0.50)** — bluffs sit at `≤ 0.45` (margin from the cliff),
value at `≥ 0.50`. The generator enforces this by making 3-bet weights bimodal
(`BLUFF_RAISE_W = 0.35`, `VALUE_RAISE_W = 0.85`). This keeps the implicit API
unambiguous in practice without yet fixing the root cause. When the tag lands,
this lint is replaced by a tag-presence/consistency check and `VALUE_RAISE_W`
is free to be any frequency.

Belongs in `poker/strategy/lints.py` (VALIDATION_SUITE_SPEC §1) once that module
exists; currently inlined in the generator as a write-time refusal.

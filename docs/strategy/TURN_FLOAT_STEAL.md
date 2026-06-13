---
purpose: The turn float-and-steal exploit (H3) — measured from real hand data, built as a multistreet override, shipped live (skill-graded, no flag)
type: design
created: 2026-06-13
last_updated: 2026-06-13
---

# Turn float-and-steal (H3)

Exploit the **one-and-done c-bettor**: a villain c-bets the flop a lot but rarely
double-barrels. When such a villain c-bets the flop, the hero floats in position, and
the villain then **checks the turn** (gives up), the hero should **bet to steal** —
especially with air that can't win at showdown. This is the mirror of the existing H1
barrel-continuation (which continues the hero's *own* aggression); H3 attacks the
*opponent's* give-up.

## Measured first — from real hand data, not sim

Per [[feedback_measure_spot_before_building]], the spot was measured before building —
and here the **historical decision corpus** (`player_decision_analysis` +
`hand_history`) was a better instrument than sim: real opponents, no rule-bot artifacts,
no harness limits. Tool: `scripts/measure_turn_steal.py` (read-only).

Isolating the true give-up line (villain c-bet flop, hero floated IP, villain checked
the turn, hero holds air/weak):

- **The hero checks back 77%** of these turns (steals only 23%).
- When the hero *does* bet, the **villain folds 48%**.

48% fold equity makes betting **air** clearly +EV vs checking it back (air checked back
≈ 0 EV — no showdown value). So the 77% check-back is a real missed-steal leak for the
air portion. (Broader signal: turn, IP, checked-to-hero — the hero bets air 28% /
weak_made 26%, vs strong_made 79% / nuts 91%.)

This is a **rare spot (~0.5% of hands)**, so — like the limper iso — bb/100 can't resolve
it (it sits under the short-stack noise floor). The per-decision fold equity from real
data is the right instrument, and it confirms the edge.

## Build (multistreet H3)

`poker/strategy/multistreet_context.py` — a new **H3 steal branch** in
`apply_multistreet_context`, a sibling of the `air_barrel` branch (which barrels turn
air when the hero *was* the aggressor):

- **Signal:** `MultiStreetSignals.opp_cbet_flop` (added) — did the opp c-bet the flop.
  With `was_prev_street_aggressor=False` (hero floated) + a turn `unopened` node (villain
  checked), that's the give-up line.
- **Gates:** `steal_target>0`, a **foldable-villain read** (`fold_to_big_bet >= 0.45`,
  reusing `_resolve_river_bluff_ftbb` — never bluff into a station), hero floated, opp
  c-bet flop, checked-to (`unopened`), HU (`active_count<=2`), turn only, hand class in
  `H3_STEAL_CLASSES = {air_no_draw, air_strong_draw}` (pure fold-equity steals — no
  showdown value to protect).
- **Effect:** `_pump_bet` air to the `steal_target` bet frequency.

`steal_target` is the skill-graded **`steal_turn_target`** knob (shark 0.55, reg 0.40,
weak_reg 0.20, rec 0.0) — resolved in the controller (`_resolve_steal_turn_target`),
wired through `_layer_multistreet_context`. **No feature flag** — gated only by
`target>0` + the foldable read (a graded read, not a dormant boolean, like
`vs3bet_exploit`); rides on `enable_multistreet_context` (on by default). Rec/knob-0 is
byte-identical to the old give-up; sims/tests bypass `__init__` → 0 → no-op.

## Validation

- **Unit** (`tests/test_strategy/test_multistreet_context.py`): 13 H3 branch tests
  (fires on the give-up line / air classes; skips when sticky, no read, hero-aggressor,
  no-cbet, multiway, facing-bet, off-turn, made-hand, ablated) + 2 controller-integration
  tests (the pipeline reaches the branch via `derive_signals` + the ftbb read). All green.
- **Spot + EV:** the real-data measurement above (`scripts/measure_turn_steal.py`).
- No bb/100 sim: the spot is too rare to resolve in aggregate (the limper lesson); the
  per-decision real-data fold equity is the instrument.

## Status: LIVE (skill-graded, no flag)

The per-decision edge is real but the spot is rare, so the aggregate bb/100 impact is
small — value is a sharper, more believable bot that doesn't give up its air to a
one-and-done c-bettor. Open extensions: `weak_made` (has showdown value — murkier EV, so
left out of v1); the OOP probe-lead variant; multiway.

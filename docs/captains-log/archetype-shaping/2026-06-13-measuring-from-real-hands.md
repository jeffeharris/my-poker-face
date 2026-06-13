---
purpose: Captain's log — building the turn float-and-steal, and discovering the historical decision corpus is a better measuring instrument than sim
type: guide
created: 2026-06-13
last_updated: 2026-06-13
---

# Measuring from real hands

The float-and-steal started as the "cheapest" of the exploit ideas — the barrel stats
already existed, so I billed it as a quick offset like the preflop ones. Digging in, it
wasn't: it's an in-position 6-max postflop play (in heads-up the aggressor is the IP
player, so the floater can't steal a check), the clean gate needs the cross-street line
(`was_prev_street_aggressor`), and that line only populates in production and the
champion_challenger harness — not the `simulate_bb100` harness the preflop work used. I
was about to plumb a whole champion_challenger-based measurement when Jeff asked the
question that reframed it:

> *"is it possible to look at historic data from prod or dev?"*

Yes — and it was the better instrument. The dev DB has 79k `player_decision_analysis`
rows, each carrying the full `strategy_pipeline_snapshot_json` (the `node_key` encodes
`street|position|...|made_tier|...|facing_action|spr` + the resolved action), and 20k
`hand_history` rows whose `actions_json` reconstructs the entire line. So I could measure
the *exact* spot from real hands the bot actually played — no rule-bots squeezing
unrealistically tight, no harness that drops the multistreet line, no slow sim.

Isolating the true give-up line (villain c-bet flop, hero floated IP, villain checked
the turn) with an air/weak hero: **the hero checks back 77%**, and when it does bet, **the
villain folds 48%**. 48% fold equity makes betting air clearly +EV over checking it back
(air has no showdown value). The leak was real, and I'd measured it without simulating a
single hand.

## Why sim was the wrong reach here

Two reasons the historical data beat sim for this:

1. **The spot is ~0.5% of hands.** A stochastic firing probe I wrote found *zero*
   instances in 150 hands — it would have needed thousands of slow hands to see the spot,
   and bb/100 over it sits under the short-stack noise floor (the limper lesson again).
   The historical corpus already *contains* the rare spot at scale.
2. **The sim opponents don't exhibit the leak believably.** The rule-bot "maniacs" squeeze
   tighter than real ones (the squeeze-defense lesson); the give-up line in real data came
   from real opponents, whatever they were.

The confirm-it-fires gate still mattered, but the right tool for *that* was a
deterministic controller-integration test (set the give-up signals, assert the pipeline
reaches the steal branch) — not a sim hoping to stumble into a 0.5% spot.

## The build

H3 in `multistreet_context.py`, the exact mirror of the existing `air_barrel` branch:
that one barrels turn air when the hero *was* the aggressor; H3 bets turn air when the
hero *floated* and the opp gave up. It even reuses the same foldable-villain read
(`fold_to_big_bet`) so it never bluffs into a station. Skill-graded knob, no feature
flag — shipped live per Jeff's standing "stop with the gates."

## The lesson

When the question is "does this spot happen, and what does the bot do there," the
historical decision corpus is often a better answer than a sim — especially for rare
spots and for opponent behavior a rule-bot can't fake. The pipeline snapshot is already
persisted on every decision; `hand_history.actions_json` reconstructs the line. I'd been
reaching for sim by habit and almost plumbed a harness I didn't need. Banked:
`scripts/measure_turn_steal.py` and the `feedback_measure_spot_before_building` memory now
note the real-data path. Measure first — and check whether the measurement already exists
in the data before you build a simulator to generate it.

---
purpose: Vision for heat-driven "grudge tilt" — turning the relationship system's heat axis into a believable, exploitable behavioral leak (localized tunnel vision on a rival) rather than an EV exploit, with the measured challenges that shape it.
type: vision
created: 2026-06-13
last_updated: 2026-06-13
---

# Grudge Tilt — when an AI plays the man, not the table

## The one-line idea

When an AI has built up **heat** against a specific opponent, it should start
**playing that opponent instead of the pot** — tunnel-visioning on the rival at
the expense of everyone else at the table. Not "play better against them" — play
*worse*, in the specific, human way a grudge makes you play worse.

## Why this, and why it's a reversal

The first attempt (`relationship_modifier`) treated heat as an **exploit**: a rival
read that made you chase value harder, bluff more *for profit*. That was wrong on
both axes — it isn't how grudges work, and it rode a logit-offset channel that
[was proven behaviorally inert](../guides/STRATEGY_LAYER_VALIDATION.md) (forced to
maximum on every decision, it moved play 0.0pp). It was retired.

The reversal that makes it work: **heat is a leak, not an edge.** A real grudge is
localized tilt — you fixate on beating one person and stop seeing the rest of the
field:

- You bluff-raise to make *him* fold — and stack off to the quiet player behind
  you who actually has it.
- You hero-call *him* light because you won't be bluffed by *him* — while a third
  player is repping the nuts and means it.
- You isolate and attack *him* and forget there's a table.

Every grudge blowup is the same bug: **collapse the whole pot into a heads-up duel
with the rival.** That's *one* mechanism, and the chaos (bad bluffs, light calls,
over-commits) falls out of it for free. It's not a bag of distortions — it's a
single "who am I playing against?" error.

## How it would plug in (cleanly)

The decision pipeline already takes the two inputs this hijacks:

1. **Who do I read against?** — there's already a primary-aggressor selection
   (`select_primary_aggressor` / `primary_spot`). Grudge tilt overrides it: read
   against the *rival*, even when someone else is the bettor.
2. **How much do I respect the field?** — there are already multiway brakes
   ("don't bluff if a station's live," multiway range-tightening). Grudge tilt
   *suppresses* them: act like it's HU vs the rival.

Strength = `heat × (1 − poise)`, **gated on the existing poise anchor** so a serene
pro barely budges and a hothead tunnels hard. That ties it to the psychology system
(it's the inverse of the existing tilt gate, which *suppresses* exploitation — this
one *adds* a leak) and makes rivalry play differently per persona.

## The payoff: a real construct ⇄ counter loop

This isn't just flavor. A grudge-tilted AI **is** an over-bluffer against its
rival — which is exactly the leak the
[bluff-catch exploit we shipped](../captains-log/lookup-tables/does-the-bluff-catch-win-chips.md)
detects and punishes (+14.8 bb/100 HU). So heat would *generate* the exploitable
behavior the rest of the bot is built to attack. And because the tilted AI ignores
**everyone but the rival**, the leaked spots pay off *any* third player, not just
the rival.

For a human at the table that's a genuine, earned skill: **needle the AI, become
its nemesis, and it tilts into you** — while a smart third player quietly profits
from being ignored. Rivalries become visible, exploitable, and emergent: the same
persona plays differently depending on *who it's heated at and who else is seated.*
That's the variance we're after — discovered, not scripted.

## The challenges (measured, not guessed)

We ran the relationship distribution sim (5,000 continuous hands, 6 personas) to
ground this before building. The system *functions* — heat is reachable — but it's
**too hot, too undifferentiated, and driven by the wrong events.**

### 1. Heat over-saturates in a grind
Median heat **0.84**, p95 **1.0**, **40% of pairs pinned ≥ 0.9**. With today's 0.5
"rival" threshold, a grinding AI would tunnel on *everyone* — and if everyone's a
nemesis, no one is. (The sim is a worst case: same players, rebought forever, no
rotation; real lobbies rotate and would sit lower. But the mechanism holds.)

**Direction:** read heat **relative**, not by threshold — fixate on the *single
hottest live opponent* (argmax), scaled by their heat. The "play one man" mechanic
naturally wants argmax anyway, and it dodges most of the saturation problem.

### 2. There's no in-session cooling
Heat decay is all in real-world **days** (plateau 7d, half-life 14d). Within a
single session — even thousands of hands — heat **never time-decays**; the only
in-session cooling is weak events (`big_win` −0.10, `hero_call` −0.05) that lose
the race to the heating events. Cross-session, a pinned grudge stays above the
rival line for **~3 weeks** and doesn't fully fade for **~2 months**.

**Direction:** add an in-session cooling term (a small per-hand heat bleed, or
stronger cooling events) so one long grind doesn't pin the whole table to nemesis.
This is the "cool it down" gap, quantified.

### 3. Heat is driven by attrition, not drama
The workhorse is `big_loss` (+0.15) — "I've lost pots to you," accumulated. The
*characterful* events are rare or unwired: `bluffed_off`, `nemesis`, `cooler`,
`rival` all fire a handful of times, and **`bad_beat` (+0.30, the biggest driver)
never fired at all** (it needs equity history the sim doesn't feed); taunts need
chat. So today a grudge reads as a loss counter, not a story.

**Direction:** weight heat toward the dramatic events (bad beat, bluffed-off,
taunt) over `big_loss` attrition, and wire `bad_beat` so the spicy driver actually
fires. A grudge should be *earned by a moment*, not by erosion.

## Honest assessment

The consumer side (the tunnel-vision lever) is a clean, bounded build on hooks that
already exist. The **harder, more valuable work is on the heat *source*** —
cooling and event-weighting — because that's what decides whether the feature lands
as "1–2 real rivals per player" (great) or "everyone hates everyone" (noise). It's
a bigger lift than the bluff-catch exploit was, and it touches the psychology /
relationship subsystems, not just the strategy layer.

It's also **−EV by design** — a flaw, capped and poise-gated so a hothead doesn't
become a free ATM for the whole table. That's the same believability-over-EV call
the project has made before (e.g. the polarized vs-3bet gradient). The point isn't
to win more; it's to lose like a human, on purpose, in a way a sharp opponent can
read and punish.

## Cheapest next step

Prototype the **argmax-relative read + a per-hand cooling term** and re-run the
distribution sim. If we can make heat land as a small number of genuine rivals per
player instead of a saturated table, the construct is worth building on the
target-selection + multiway-gate hooks. If it stays saturated, we learn the source
needs rework before the consumer is worth anything — cheaply, before writing the
lever.

## Related

- Counter side (shipped): [bluff-catch exploit](../captains-log/lookup-tables/does-the-bluff-catch-win-chips.md)
- Why the offset-channel version failed: [strategy-layer validation playbook](../guides/STRATEGY_LAYER_VALIDATION.md)
- Tendency model (construct ⇄ counter): `docs/strategy/TENDENCY_CONTRACT.md`, `docs/strategy/EXPLOIT_CATALOG.md`
- Heat mechanics: `RelationshipState` / `project_heat` (`poker/memory/opponent_model.py`), event→axis table (`poker/memory/relationship_events.py`), detector wiring (`poker/memory/hand_outcome_detector.py`)
- Measurement tool: `scripts/sim_experiments/relationship_distribution_sim.py`

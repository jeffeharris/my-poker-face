---
purpose: Design for heterogeneous per-AI exploiter "loadouts" plus learned mark-selection — the symmetric dual of the per-archetype leak system
type: design
created: 2026-05-29
last_updated: 2026-05-29
---

# Predator loadouts & "learn your mark"

> **Status: idea / design sketch, NOT built.** This is the symmetric other half of the
> variety program. The leak side is built (per-archetype width tables + spot tendencies,
> `PERSONALITY_PRICING_AND_VARIETY.md`). This doc sketches the exploiter side and the
> discipline that keeps it from being cosmetic.

## The idea (one line)

Each AI carries a **specific subset of the exploit toolkit** (a *loadout*), not the whole
thing, and **learns over time which opponents its weapon beats** — "this fish is *my* mark."
A shark whose weapon is "snap off bluffers" hunts the over-bluffers; one whose weapon is
"value-bet stations to death" hunts the callers. Predators drift toward the prey their tool
devours. That is a *story* ("this shark has your number"), not just a stat — which is the
whole point of a poker game with AI personalities.

## Why it's the dual of the leak system

We made the **leak** side heterogeneous: each archetype/personality has its own weak spots
(a nit folds too much, a station can't fold, a maniac over-aggresses). The natural dual is a
heterogeneous **exploiter** side: each AI carries a different *menu of counters*. The
project's catalog already frames the system as three move-types — **leak / adaptive-exploiter
/ defense** — that "a bot is composed from a menu of." This doc is: *actually give each bot a
different menu*, and let it discover its best matchups by playing.

## It's mostly composition, not new machinery

Three pieces already exist; the work is wiring, not invention.

1. **The exploit axes** — `poker/strategy/exploitation.py` already detects and counters:
   `value_vs_station` (callers → thin value + overbet), `hyper_aggressive` (aggressors →
   tighten opens, widen calls), `high_fold_to_cbet` (over-folders → barrel), `multiway_cbet`,
   plus the river bluff guardrail (defense). **Today they fire all-or-nothing** through one
   `exploitation_strength`. **The loadout extension:** a per-personality map enabling a
   *subset* of axes (e.g. `{value_vs_station: 1.0, high_fold_to_cbet: 0.0, ...}`), exactly
   like the per-personality `spot_tendencies` override already merged into
   `TieredBotController.deviation_profile`.
2. **The per-opponent reads** — `OpponentModelManager` already accumulates per-opponent
   frequency stats (vpip / fold-to-cbet / AF / WtSD) and matures to confidence ~100 hands.
3. **The persistence/relationship layer** — cash-mode dossiers / `cash_pair_stats` / the
   social-relationship system already persist per-opponent history across sessions.

**"Learn which fish is my mark"** = per-opponent **edge estimate**: for each opponent the AI
has read, estimate "how well does *my* loadout's matchup do against *this* player?" and
preferentially target / sit with / attack the opponents where that estimate is highest. That
is the **matchup graph, drawn online** — the pricing doc's finding that "the skill gradient
isn't a scalar, it's a matchup graph" (sticky is −1.87 vs a value-bettor but **+2.12 vs a
bluffer**; over_bluff is **−7.6 vs a caller**, free vs a reg) made into table behavior.

## The discipline (the hard part — learned the hard way)

**Stock loadouts ONLY from matchups that *convert* — firing ≠ extracting.** The maniac
loop-closing test (`PERSONALITY_PRICING_AND_VARIETY.md`) proved a detector can fire on 10.7%
of hands and extract **$0**. And the leak×counter matrix showed only **river/commit** leaks
have fixed counters that bite; early-street leaks are floor-capped, and aggression-defense is
inert (the static defense/math floors already refuse to over-fold). So the weapons that make
a *real* mark-finding dynamic are the measured-converting ones:

| Weapon (loadout axis) | Prey (the mark) | Evidence | Use? |
|---|---|---|---|
| value-overbet + thin value (`value_vs_station`) | sticky / pays-off caller | **+16 CI-clear** (the one demonstrated closed loop), +42 vs payers | ✅ |
| over-call / bluff-catch | over-bluffer | mirror of over_bluff's **−7.6 vs a caller** | ✅ |
| barrel relentlessly (`high_fold_to_cbet`) | over-folder to c-bets | partial; floor-capped on early streets | ⚠ situational |
| "call more vs aggression" (`hyper_aggressive`) | maniac | **inert** — bot already defends statically | ❌ |
| barrel the fit-or-folder | fit-or-fold | floor-capped, ~free | ❌ |

A loadout system stocked with the ❌/⚠ weapons produces a *cosmetic* game where everyone
*thinks* they have a mark and nobody extracts. The matchup graph must be drawn from the cells
we've **measured** as real (the ✅ rows), and expanded only as new converting counters are
priced.

## Practical constraints

- **Reads must mature + persist.** ~100 hands for a confident read; in cash mode dossiers
  persist, so learning lives at the **persona** level — NOT the session. Rotating casino seats
  won't give a bot enough hands vs one fish in a single sitting, so the "my mark" memory has to
  be the persisted per-pair history, not in-session state.
- **It needs exploitable prey in the field.** This is *why* it was parked before: a homogeneous
  field gives the exploiters no target. The variety work (true calling stations, the sticky/
  over-bluff leaks) is the precondition — now there ARE marks. The fish→calling-station
  switchover (`FISH_AS_CALLING_STATION.md`) makes the casino fish a real, in-engine `value_vs_
  station` target, which is the cleanest first prey.
- **Don't let it make the strong bots weaker.** A loadout is *additive* counters on detection;
  with no confident read it must be a no-op (the exploitation layer already ramps from 0 on
  confidence), so an un-read table plays the baseline strategy.

## Sketch of the build (when picked up)

1. **Loadout config:** a per-personality `exploit_loadout: {axis: strength}` map (mirror the
   `spot_tendencies` per-personality override path). Default = current behavior (all axes at the
   archetype's `exploitation_strength`). A predator gets a *subset* at full strength, the rest 0.
2. **Per-opponent edge estimate:** from `OpponentModelManager` reads, score each known opponent
   on the AI's enabled axes (e.g. a `value_vs_station`-loadout bot scores a high-WtSD/low-fold
   opponent as a strong mark). Persist per pair.
3. **Mark-driven behavior:** surface the edge estimate where it can act — seat/table selection
   (drift toward your marks), attention/aggression weighting at the table, and the
   social/relationship layer (the narrative "shark has your number"). Start with the table
   decision; it composes with the existing cash movement system.
4. **Validate each weapon converts** before shipping it in a loadout — paired-CRN vs the prey
   it targets (the ✅ table above is the seed; price any new axis the same way).

## Pointers

- Leak side (built): `PERSONALITY_PRICING_AND_VARIETY.md`, `PERSONALITY_LEAK_WIRING.md`.
- The matchup-graph finding + the maniac inert result: `PERSONALITY_PRICING_AND_VARIETY.md`
  ("Leak × counter matrix" + "Loop-closing test").
- The detector/counter axes: `poker/strategy/exploitation.py`; reads:
  `poker/memory/opponent_model.py`.
- First prey: `FISH_AS_CALLING_STATION.md`.

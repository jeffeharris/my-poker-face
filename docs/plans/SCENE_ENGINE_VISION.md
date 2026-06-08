---
purpose: Vision for graduating the Scene-0 rig into a first-class, reusable Scene Engine — a director that treats the live poker game like a film set, with branching (choose-your-own-adventure) narrative as the core (intentionally NOT an implementation plan)
type: vision
created: 2026-06-03
last_updated: 2026-06-03
---

# The Scene Engine — vision

> **This is a vision doc, on purpose.** It sets direction, principles, and
> priorities. It does **not** decide the how — that's the plan doc this will be
> refined into. Where it names files/functions, that's orientation for a fresh
> context, not a spec. Open questions for the plan author are at the end.

## North Star

> **Author a scene as a declaration; the engine deals it, runs the cast, lets the
> player's choices steer the story, tells that story, survives a refresh, and
> proves itself in CI — before a human ever plays it.**

Scene-0 proved the *idea*: a rigged, narrated poker set-piece is a powerful
storytelling primitive. The vision is to graduate that from a **Scene-0-specific
contraption** into a reusable **Scene Engine** — a *director* that treats the live
poker game like a **film set**: it can lock everything down (rig the cards, script
every cast move) or relax control and let the table improv, and either way the
player's decisions **branch the story**. The substrate for tutorials, story beats,
"boss-fight" set-pieces, and the M3 training lounge. Adding a scene should be
*describing* it, not wiring a new contraption.

## Why now

We're about to build *more* scenes. Today the foundation is good but only
Scene-0 exercises it, and in a shape that won't carry the next ones: the
"judge" only knows **fold / don't fold**, a scene **can't be tested** without a
human playing it, and the cast/narration model assumes one hero + one mentor +
one fish on rails. Reinforce the foundation — make it a real director with
branching and a control dial — *then* scale scenes onto it cheaply.

## What we already have (keep + build on)

Sound and reusable — the vision *extends* these, doesn't replace them:

- **Rotation-immune deck rig** — `provide_hand_holes(holes_by_name, board)` keys
  scripted holes by player *name*, resolved at deal time against the live
  post-button-rotation seating, validating collisions at build time. The right
  primitive.
- **Generic scene substrate** — `cash_mode/table_scenes.py` (`TableScene` +
  registry) and the driver in `flask_app/handlers/game_handler.py` (`_init_scene`
  / `_advance_scene` / `_scene_scripted_action` / `_complete_scene`).
- **Cold-load durability** — scene position in `career_progress.scene_progress`;
  a restart/eviction resumes mid-scene.
- **Conservation-safe cast funding** — cast buy-ins/top-ups debit each persona's
  own bankroll; nothing is minted. Scripted tables are inert to the world economy
  and the generic seat-fill (the "dad jokes" class).
- **Stack-capped scripted bets** (cast never busts by accident) with a `bust_ok`
  escape for an authored finale.

## What a scene should be able to BE (the aspiration)

The same declared *thing* should express all of these:

- **A tutorial hand** — fixed cards, the mentor explaining the principle (Scene-0).
- **A branching story beat** — *"He's representing the nuts. You believe him?"* →
  the player folds / calls / raises and the **story forks** to a different next
  beat and a different reaction. Choose-your-own-adventure with poker as the
  choice mechanism.
- **A loosely-controlled set-piece** — rig the villain's cards but let him improv;
  or free cards but the rival *always* 3-bets you; or everything free and the
  engine just narrates and branches on whatever actually happened.

The throughline: **declarative scenes; the engine directs** — deal, cast, route on
the player's choice, narrate, persist, verify.

## Capability gaps (honest, from the code)

1. **The "judge" is a binary grader, not a branch router.** `_advance_scene`
   computes `passed = folded if pass_when=="folded" else not folded`;
   `correct_action` is declared but **never read**. Every hand collapses to
   fold-or-not, and the *next* beat can't depend on the *choice*.
2. **A scene can't be tested end-to-end.** The finale ("Larry busts to 0") needs a
   *manual* playtest because scene execution is entangled with the live Flask
   betting loop. Every future scene inherits this.
3. **Cast control is per-*phase*, not per-*decision*** (`fish_plan`/`mentor_plan`,
   a closed intent vocabulary), so you can't sequence "call, then re-raise."
4. **Narration is lifecycle-coupled and cast-shaped** — beats are fields on the
   hand fired at fixed points; only the fish has a per-street hook, the floater
   hardcodes `"Sal Monroe"`, roles are fixed hero/mentor/fish.
5. **The script is a flat list walked by `scene_idx`** — no branching.
6. **No scene-level validation** — an inconsistent scene is only caught at runtime.

## Reinforcement pillars (priority order)

Vision-level — *what capability we want and why*. The plan decides the shape.

### Pillar 1 — Testability first (the foundation)
A scene should be **drivable to completion in-process and asserted in CI**: deal →
resolve the scripted cast → inject the hero's choice → route → inspect the next
beat + the narration emitted. Highest leverage because it removes "ships on
faith": it closes the Scene-0 finale gap *and* de-risks every future scene, and it
makes branching authorable (you can test each fork). **If we do one thing, this.**

### Pillar 2 — Scene as a beat graph; the judge is a branch router
Replace the flat list + binary judge with a **graph of beats** whose edges are
**conditions on the player's choice and/or the hand outcome**:

```
Beat "the standoff":
  setup: rig hero KK, villain AA, bricks       # controlled here
  narrate: Sal — "He's representing the nuts. You believe him?"
  branches:
    - on fold  → "the lay-down"   (Sal: "Disciplined. That's the read.")
    - on call  → "the cooler"     (showdown; Sal: "Ooh. Cold deck, kid.")
    - on raise → "the hero"       (Sal: "...you maniac.")
    - default  → "the lay-down"   (graceful fallback — always present)
```

Each target beat can branch again → arbitrary-depth CYOA. Two edge-condition
flavors, both needed: on the **choice** (fold/call/raise — pure narrative, works
fully rigged) and on the **outcome** (won/lost/busted/by how much — matters once
control is relaxed). The current binary `pass_when` is just the degenerate 2-edge
case. **Keep the router stage-agnostic: it routes on "a player choice," and a
poker action is one *kind* of choice** (see Boundaries — this is what lets a future
cutscene system reuse the same branching machinery).

### Pillar 3 — Control as a dial (the film-set metaphor)
Control is **orthogonal axes**, not one switch — the director dials each
independently, per scene or per beat:

| Dial | Locked ("on a set") | Relaxed ("improv") |
|---|---|---|
| **Deck** | rigged holes + board | real shuffle |
| **Cast** | scripted intents per decision | bots play naturally |
| **Hero/flow** | constrained to a choice set | free to do anything |
| **Narration** | authored beats | reactive/templated beats |

"Locked set" = all dialed up (Scene-0's teaching hands today). "Improv" = all down
(the engine just watches and narrates at moments). The interesting scenes are
**mixes**. The pieces already exist *implicitly* (`rigged: bool` per hand, cast
plan optional per phase, fallback-to-bot when no plan) — make them **first-class
and declarative**.

**The hard part (the stretch goal): improv + narrative.** When you dial control
down, the narration can't assume the rigged outcome, so you need *conditional /
templated beats* ("won → X, lost → Y, coolered → Z"), *outcome-bucket* branches
(not exact-card), and a *fallback edge on every node* so an unanticipated player
move (you relaxed control and they jammed) degrades gracefully — *"Sal raises an
eyebrow… your funeral"* — instead of wedging. Full lock is easy; coherent
full-improv is the ambitious end of the dial, not the first milestone.

### Pillar 4 — Authoring safety & narration as data
- **Validation** at registration/test time: cards collision-free (lift the
  deal-time check earlier), every cast intent resolvable, every beat has a
  reachable/total set of branches incl. a default, declared outcomes consistent
  with the rig. Catch authoring mistakes in CI, not a playtest.
- **Narration as a data timeline** keyed to *triggers* (street dealt, action
  taken, showdown, beat entered) and to *roles* (not a hardcoded mentor), so any
  scene's narrator + cast speak at the right moments and the floater renders any
  mentor. Generalize the `fish_streets` precedent.

## Design principles

- **Declarative scenes, imperative engine.** A scene is data, not a procedure.
- **Choices route; they don't grade.** In a narrative scene there's no "wrong"
  answer — the player's decision forks the story. (Correctness-grading is a
  *separate, pluggable* judge; see Boundaries.)
- **Provable before playable.** Nothing ships that can't be driven + asserted in a
  test. Every fork is a test.
- **Conservation-safe by construction.** Scenes move chips between real bankrolls,
  never mint — keep this true as scenes get richer.
- **Cold-load is the normal case.** Position becomes a node id + any branch flags;
  a refresh mid-scene resumes cleanly.
- **Scenes are inert outside their flow.** A scripted table is invisible to the
  world economy and untouched by generic seat-fill/movement (the "dad jokes"
  class) — treat any new world-touching path as needing a scene skip.
- **Graceful degradation.** A missing line, a refused cast move, an unanticipated
  player choice, a model timeout — degrade (fallback edge, silence, sensible
  default), never wedge the table.

## Boundaries (what this is NOT — siblings, not overloads)

Two things look adjacent but should stay *outside* the rig, sharing only what's
genuinely shared:

- **Cutscenes (e.g. the Lucky Stack waitress intake) are NOT scenes.** The rig's
  stage is the **poker table** (a live state machine, dealt hands, a cast, betting,
  hand-boundary pacing, the felt UI). A cutscene is a poker-less **modal** —
  dialogue + choices, no cards, no cast, choice-driven pacing. Hosting it in the
  rig means faking an empty game or bolting on a "no-poker beat," which contorts
  the engine and dissolves the "treat the live game like a set" model. **They are
  siblings that share a *narrative substrate*** (the beat graph + choice branching
  + the `DramaticText` print renderer + cold-load), not one engine. Design
  implication: keep the branching layer (Pillar 2) **stage-agnostic** so a future
  cutscene system can reuse it — but build only the poker stage now. The existing
  `LuckyStackIntake` modal stays bespoke until a *second* cutscene justifies
  extracting the shared core (YAGNI).
- **Coach-replay / drill grading is a deferred sibling.** It shares the rig
  substrate (rigged hands, cast, the runner) but differs in the judge: it *grades*
  a line against a target and emits feedback, where the narrative judge *routes* on
  choice. Make the **judge pluggable** — a `NarrativeRouter` (focus) and a future
  `CoachGrader` are two implementations of "evaluate the player's action → decide
  what's next" — so coaching is a clean later add-on, not built now.

## Non-goals (YAGNI until a scene needs them)

- **Data-driven (JSON/YAML) authoring** for non-engineers — Python descriptors are
  fine until there are *many* scenes (revisit when the M3 catalog is large).
- **Arbitrary N-player rigged casts / per-street board-reveal branching** — add
  when a concrete scene requires it.
- **A visual scene editor.** Out of scope.
- *(Note: branching itself is now CORE, not a non-goal — see Pillar 2.)*

## What "good enough to reuse" looks like

1. A new scene is **authored as a declaration** and **driven green in a unit
   test** — every fork covered — without touching the engine.
2. A scene **branches on the player's choice** (fold/call/raise → different next
   beat), not just fold-or-not.
3. A scene can sit **anywhere on the control dial** — fully rigged, fully improv,
   or a mix — and still narrate coherently (at least: graceful fallback when
   relaxed).
4. An **inconsistent scene fails in CI**, not a playtest.
5. A **second mentor + cast shape** narrates correctly with no Sal-specific code.
6. The **Scene-0 finale is asserted in a test** (the canary that Pillar 1 landed).

## Open questions for the plan author

- **Beat-graph + condition shape.** How are edges declared — `on: {choice: 'call'}`
  / `on: {outcome: 'lost'}` / a predicate? How is the graph authored (node ids +
  edges, or nested)? Start with choice-only edges and add outcome edges?
- **Stage-agnostic router seam.** What's the minimal "choice" abstraction the
  router keys on, so a poker action and a (future) dialogue option are both just
  choices?
- **Headless runner boundary.** Extract a pure `SceneRunner` the Flask handler
  delegates to? Where's the seam vs the state machine? How does a test inject the
  hero's choice (a scripted hero plan? a callback?) without coupling to the live
  action API?
- **Control-dial representation.** Per-beat flags for deck/cast/hero/narration, or
  a "control level" preset with overrides? How does "relaxed cast" hand back to the
  bots cleanly?
- **Improv narration.** How far to go on conditional/templated beats + outcome
  buckets in the first pass vs. punting (only support fully-rigged narration first)?
- **Judge plug point.** Where does the pluggable `NarrativeRouter` / `CoachGrader`
  interface live so both share the runner?
- **First-pass scope.** Likely Pillar 1 (testability) + the choice-edge slice of
  Pillar 2, fully-rigged only, leaving outcome edges / control dial / improv /
  validation / coach-grader as follow-ups. Confirm.

## Orientation for a fresh context (code pointers)

- Rig seam: `poker/poker_state_machine.py` — `provide_hand_holes`,
  `_deck_from_scripted_holes`, `provide_hand_deck` (legacy seat-indexed).
- Scene registry/descriptor: `cash_mode/table_scenes.py` (`TableScene`).
- Scene-0 script + scripted-action resolver: `cash_mode/career_scene.py`
  (`Scene0Hand`, `SCENE0_SCRIPT`, `resolve_scripted_action`, `fish_streets`).
- Driver + judge: `flask_app/handlers/game_handler.py` — `_init_scene`,
  `_advance_scene` (the binary judge to replace), `_scene_scripted_action`,
  `_complete_scene`, `_scene_top_up_cast`, the per-street fish hook in
  `handle_phase_cards_dealt`.
- Narrative renderer (shared with cutscenes): `react/.../components/shared/
  DramaticText.tsx`; `SalFloater.tsx` (felt stage), `LuckyStackIntake.tsx` (modal
  stage / cutscene).
- Persistence: `career_progress.scene_progress`
  (`poker/repositories/career_progress_repository.py`).
- Tests today: `tests/test_scripted_deck_seam.py`,
  `tests/test_cash_mode/test_career_scene.py`, `test_scene0_intable.py`.

## Related docs
- `docs/plans/CASH_MODE_CAREER_PROGRESSION.md` — the Circuit design canon (where
  scenes are consumed).
- `docs/plans/CASH_MODE_CAREER_M1_HANDOFF.md` — current state, risks, next steps.
- `docs/plans/CASH_MODE_FAMOUS_HANDS_LIBRARY.md` — content for authored hands.
- `docs/captains-log/circuit-progression/` — how the rig was actually built (the
  seat-rotation bug, cold-load, the finale-testability gap).

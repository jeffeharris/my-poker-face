---
purpose: Vision for graduating the Scene-0 rig into a first-class, reusable Scene Engine — what scenes should be able to be, the principles, and the reinforcement pillars (intentionally NOT an implementation plan)
type: vision
created: 2026-06-03
last_updated: 2026-06-03
---

# The Scene Engine — vision

> **This is a vision doc, on purpose.** It sets direction, principles, and
> priorities. It does **not** decide the how — that's the plan doc this will be
> refined into. Where it names files/functions, that's orientation for a fresh
> context, not a spec. Open questions for the plan author are collected at the end.

## North Star

> **Author a scene as a declaration; the engine deals it, runs the cast, grades
> what the player actually did, tells the story, survives a refresh, and proves
> itself in CI — before a human ever plays it.**

Scene-0 proved the *idea*: a rigged, narrated, graded poker set-piece is a
powerful teaching and storytelling primitive. The vision is to graduate that from
a **Scene-0-specific contraption** into a **reusable Scene Engine** — the
substrate for tutorials, coaching drills, story beats, "boss-fight" set-pieces,
and the M3 training lounge. Adding a scene should be *describing* it, not wiring a
new contraption each time.

## Why now

We're about to build *more* scenes (M3 drills, the endgame mentor arc, future
narrative beats). Right now the foundation is good but **load-bearing in ways that
only Scene-0 exercises**: the judge only knows "fold / don't fold," the full loop
can't be tested without a human playing it, and the cast/narration model assumes
one hero + one mentor + one fish. If we pour more scenes onto that as-is, each one
ships on faith and each new requirement re-opens the engine. Reinforce the
foundation first, then scale scenes onto it cheaply.

## What we already have (keep + build on)

These are sound and reusable — the vision *extends* them, doesn't replace them:

- **Rotation-immune deck rig** — `provide_hand_holes(holes_by_name, board)` keys
  scripted holes by player *name*, resolved at deal time against the live
  post-button-rotation seating, and validates collisions at build time. This is
  the right primitive.
- **Generic scene substrate** — `cash_mode/table_scenes.py` (`TableScene`
  descriptor + registry keyed by table_id) and the generic driver in
  `flask_app/handlers/game_handler.py` (`_init_scene` / `_advance_scene` /
  `_scene_scripted_action` / `_complete_scene`). A new scene already = register a
  `TableScene`.
- **Cold-load durability** — scene position persisted in `career_progress.
  scene_progress`; a restart/eviction resumes mid-scene.
- **Conservation-safe cast funding** — cast buy-ins/top-ups debit each persona's
  own (sandbox-scoped) bankroll; nothing is minted. The scripted top-up rebuys the
  fish; the generic refill now skips scene tables (the "dad jokes" class — see
  `CASH_MODE_CAREER_M1_HANDOFF.md` risks).
- **Stack-capped scripted bets** so the cast never busts by accident, with a
  `bust_ok` escape for an authored finale.

## What a scene should be able to BE (the aspiration)

A mature engine should make these all the *same kind of thing*, declared the same
way:

- **A tutorial hand** — fixed cards, a clear right play, the mentor explaining the
  principle (Scene-0 today).
- **A coaching drill** — "you flopped a set on a wet board vs a maniac; play it."
  Graded on the actual line, not just whether you folded.
- **A story beat / set-piece** — a scripted confrontation with a rival, a "prove
  yourself" hand, an endgame-mentor moment — where the *narrative* (who says what,
  when) is as authored as the cards.
- **A reactive moment** — the cast and narrator respond to *what the player did*
  (called the bluff / folded the winner / hero-called), not just the end state.

The throughline: **declarative authoring + the engine owns execution** (deal,
cast, judge, narrate, persist, verify).

## Capability gaps (honest, from the code)

The reusability ceiling today, in priority order:

1. **The judge only knows "fold / don't fold."** `_advance_scene` grades
   `passed = folded if pass_when=="folded" else not folded`. `correct_action` is
   declared on the hand but **never read**. So every lesson collapses to fold-or-
   not — it can't express "you should *call*," "value-bet three streets," "check
   back," or a multi-decision line.
2. **A scene can't be tested end-to-end.** The finale ("Larry busts to 0") still
   needs a *manual* playtest because scene execution is entangled with the live
   Flask betting loop. Every future scene inherits this — it ships unverified
   until a human plays it.
3. **Cast control is per-*phase*, not per-*decision*.** `fish_plan`/`mentor_plan`
   map `phase → intent` from a closed vocabulary (`fold/limp/passive/bluff/bet/
   shove`); the same intent re-fires each time the cast acts that street, so you
   can't sequence "call, then re-raise."
4. **Narration is lifecycle-coupled and cast-shaped.** Beats are string fields on
   the hand fired at fixed driver points; only the fish has a per-street hook
   (`fish_streets`), the mentor has none, the floater hardcodes `"Sal Monroe"`,
   and the role model is fixed at hero/mentor/fish.
5. **The script is a flat list walked by `scene_idx`** — no branching or
   adaptation (e.g. insert a remedial hand on failure).
6. **No scene-level validation** — an inconsistent scene (rigged outcome
   contradicts `pass_when`, cast plan unresolvable given the holes) is only caught
   at runtime, in a playtest.

## Reinforcement pillars (priority order)

Vision-level — *what capability we want and why*. The plan decides the shape.

### Pillar 1 — Testability first (the foundation)
A scene should be **drivable to completion in-process and asserted in CI**: deal →
resolve the scripted cast → apply a forced/scripted hero action → advance →
inspect the judged result and the narration emitted. This is the highest-leverage
investment because it removes "ships on faith": it closes the Scene-0 finale gap
*and* de-risks every future scene. It also unlocks fast iteration (author a scene,
watch it run green) and turns the M3 drill catalog into something testable rather
than hand-checked. **If we do one thing, it's this.**

### Pillar 2 — Expressiveness (teach more than fold/not-fold)
The judge should grade **what the player actually did** against a *declared* win
condition, and the cast should be sequenceable per decision. Concretely as a
*capability*: a hand declares "the right play is X" (an action, a line, or a
predicate over the hero's decisions), and the engine evaluates the recorded action
history against it. Wire up the already-present `correct_action`; let `pass_when`
become expressive. This is what lets the engine deliver the *variety* the
famous-hands library implies (Moneymaker's call, Seidel's lay-down, a value line,
a disciplined fold) instead of one binary.

### Pillar 3 — Authoring safety & narration as data
Two ergonomics wins that compound as scene-count grows:
- **Validation** — a scene can be checked at registration/test time: cards
  collision-free (lift the existing deal-time check earlier), every cast intent
  resolvable, the rigged outcome consistent with the declared win condition, cast
  roles matching the script. Catch authoring mistakes in CI, not a playtest.
- **Narration as a data timeline** — beats keyed to streets/events and to *roles*
  (not a hardcoded mentor), so any scene's narrator + cast can speak at the right
  moments, and the floater renders any mentor. The `fish_streets` hook is the
  precedent to generalize.

## Design principles

The values the engine should optimize for (and that the plan should preserve):

- **Declarative scenes, imperative engine.** Authors describe *what*; the engine
  owns *how*. A scene is data, not a procedure.
- **Provable before playable.** Nothing ships that can't be driven + asserted in a
  test. Testability is a first-class feature, not an afterthought.
- **Conservation-safe by construction.** Chips move between real bankrolls; scenes
  never mint. (Already true — keep it true as scenes get richer.)
- **Cold-load is the normal case.** A refresh / eviction / >2h idle mid-scene must
  resume cleanly. Position + rig state are persisted; assume it happens.
- **Scenes are inert outside their flow.** A scripted table is invisible to the
  world economy and untouched by generic seat-fill/movement. Treat any new
  world-touching path as needing a scene skip (the "dad jokes" class).
- **One role model, N roles.** hero/mentor/fish today; the engine shouldn't bake
  the count in where a future scene needs a rival or a second fish.
- **Graceful degradation.** A missing line, a refused cast move, a model timeout —
  a scene should degrade (silence, a sensible fallback), never wedge the table.

## Non-goals (YAGNI until a scene needs them)

Naming these keeps the plan honest:

- **A full branching/adaptive narrative engine.** Linear-with-optional-remedial is
  plenty until a scene demands real branching.
- **Data-driven (JSON/YAML) authoring** for non-engineers. Python descriptors are
  fine until there are *many* scenes; revisit when the M3 catalog is large.
- **Arbitrary N-player rigged casts / per-street board-reveal branching.** Add
  when a concrete scene requires it, not speculatively.
- **A visual scene editor.** Way out of scope.

## What "good enough to reuse" looks like

We'll know the engine has graduated when:

1. A new scene can be **authored as a declaration** and **driven green in a unit
   test** without touching the engine.
2. A scene can **grade a real line** (call/raise/check/fold across streets), not
   just fold-or-not.
3. An **inconsistent scene fails in CI**, not in a playtest.
4. A **second mentor + cast shape** narrates correctly with no Sal-specific code.
5. The **Scene-0 finale is asserted in a test** (the canary that Pillar 1 landed).

## Open questions for the plan author

The decisions a plan/jr should make next (not decided here on purpose):

- **Win-condition shape.** How declarative should the judge's target be — an enum
  action, a per-street action list, a predicate over recorded decisions, or a
  small DSL? Start minimal (wire `correct_action` as a per-street action) and grow?
- **Headless runner boundary.** Where does the scene loop live so it's testable
  *and* shared with the Flask handler — extract a pure `SceneRunner` the handler
  delegates to? What's the seam against the state machine vs the handler?
- **Hero-action injection in tests.** How does a test supply the hero's decisions
  (a scripted hero plan? a callback?) without coupling to the live action API?
- **Cast control granularity.** Keep per-phase intents and add an optional
  per-decision override, or move wholesale to a decision list?
- **Narration timeline.** Generalize `fish_streets` into a role-agnostic
  `{role, trigger}` beat list? What triggers exist (street dealt, action taken,
  showdown, judged)?
- **Scope of the first pass.** Likely Pillar 1 (testability) + the `correct_action`
  slice of Pillar 2, leaving validation/narration/branching as follow-ups. Confirm.

## Orientation for a fresh context (code pointers)

- Rig seam: `poker/poker_state_machine.py` — `provide_hand_holes`,
  `_deck_from_scripted_holes`, `provide_hand_deck` (legacy seat-indexed).
- Scene registry/descriptor: `cash_mode/table_scenes.py` (`TableScene`).
- Scene-0 script + scripted-action resolver: `cash_mode/career_scene.py`
  (`Scene0Hand`, `SCENE0_SCRIPT`, `resolve_scripted_action`, `fish_streets`).
- Driver: `flask_app/handlers/game_handler.py` — `_init_scene`, `_advance_scene`
  (the judge), `_scene_scripted_action`, `_complete_scene`, `_scene_top_up_cast`,
  the per-street fish hook in `handle_phase_cards_dealt`.
- Persistence: `career_progress.scene_progress` (`poker/repositories/
  career_progress_repository.py`).
- Tests today: `tests/test_scripted_deck_seam.py`,
  `tests/test_cash_mode/test_career_scene.py`, `test_scene0_intable.py`.

## Related docs
- `docs/plans/CASH_MODE_CAREER_PROGRESSION.md` — the Circuit design canon (where
  scenes are consumed: Act-1 tutorial, M3 lounge, endgame mentor arc).
- `docs/plans/CASH_MODE_CAREER_M1_HANDOFF.md` — current state, risks, next steps.
- `docs/plans/CASH_MODE_FAMOUS_HANDS_LIBRARY.md` — the content the richer judge
  needs to teach.
- `docs/captains-log/circuit-progression/` — how the rig was actually built (the
  seat-rotation bug, cold-load, the finale-testability gap).

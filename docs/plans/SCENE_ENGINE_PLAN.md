---
purpose: Concrete first-pass plan for the Scene Engine — the headless SceneRunner (Pillar 1, testability-first) plus authoring validation, refined from SCENE_ENGINE_VISION.md
type: spec
created: 2026-06-03
last_updated: 2026-06-03
---

# Scene Engine — Pillar 1 (testability) implementation plan

> **STATUS: IMPLEMENTED (2026-06-03).** `cash_mode/scene_runner.py` (`run_scene`,
> the pure helpers, `validate_scene`) shipped; `game_handler` delegates to the
> shared helpers; `tests/test_cash_mode/test_scene_runner.py` is green (1238
> cash_mode tests, ruff clean). Notes vs the plan below: the per-fork hero
> provider is `hero_by_lesson` (keyed by `hand.lesson`, not hand index); the runner
> mirrors the live `_scene_top_up_cast` in-memory (seeds the mentor 2× the fish so
> the finale busts) and reports `chips_injected` / `conserved` for conservation
> checks. **Building the runner immediately paid for itself: it surfaced two real
> finale bugs** (the `shove` raise-to under-shoot and `passive` not calling off an
> `all_in`-only spot) that the live-only path had hidden — both fixed in the shared
> `resolve_scripted_action`. Judge stays binary (the choice-edge router slice of
> Pillar 2 is still deferred). See the M1 handoff session-4 note.

Refines `docs/plans/SCENE_ENGINE_VISION.md` into a buildable first pass. Scope is
deliberately narrow: **make a scene drivable to completion in-process and
asserted in CI**, and **make an inconsistent scene fail in CI, not a playtest**.
Everything else in the vision (beat-graph branching, the control dial, improv
narration, a pluggable coach-grader) is explicitly deferred — but the seam is cut
so they slot in later.

## Goal (success criteria from the vision)

- **#6** The Scene-0 **finale is asserted in a test** — the canary that Pillar 1
  landed: driven headlessly to graduation, Larry busts to 0, the completion
  effect (`career_first_vouch`) fires, the graduation lines are emitted.
- **#4** An **inconsistent scene fails in CI** (validation), not a playtest.
- A new scene (or a fork of an existing one) can be **driven green in a unit
  test** without booting Flask.

Non-goals this pass: branching graph, outcome edges, the control dial, improv
narration, coach-grader, frontend floater generalization. (Tracked in the vision.)

## The core problem

Scene execution today is **entangled with the live Flask betting loop**. The
scene logic lives as four hooks in `flask_app/handlers/game_handler.py`, each
taking `(game_id, game_data, state_machine)` and reaching into Flask singletons
(`send_message`, `socketio`, `career_progress_repo`, `bankroll_repo`,
`game_state_service`):

| Hook | Where it fires | What it does |
|---|---|---|
| `_init_scene` | first AI turn at a scene table (`run_ai_action`) | resolve roles/seats, restore or start position, opening lines |
| `_scene_scripted_action` | each AI decision (`run_ai_action`) | override the cast's action from the script |
| per-street fish tell | `handle_phase_cards_dealt` | `fish_streets[phase]` line |
| `_advance_scene` | hand boundary (HAND_OVER processing) | **judge** the finished hand, narrate the verdict, advance the index, top-up cast, rig the next hand, complete the scene |

Because the *judge* and *which-line-to-say* logic is embedded inside
`_advance_scene` alongside the Flask I/O, there's no way to assert it without a
human playing — and the finale (`bust_ok`) can only be confirmed by hand.

## The seam (the design decision)

**Extract the pure scene logic into shared functions that BOTH the live handler
and a new headless runner call.** This is the crux: the test must exercise the
*same* judge/narration code the live game runs, or it proves nothing.

### New module: `cash_mode/scene_runner.py` (Flask-free)

Pure data + a driver. Two layers:

**1. Pure decision helpers** (the shared core — extracted from `_advance_scene`):

```python
def judge_hand(hand, hero_folded: bool) -> bool | None:
    """None if the hand carries no lesson; else passed per pass_when."""

def verdict_line(hand, passed: bool) -> str:        # sal_pass / sal_fail
def setup_lines(hand) -> tuple[str, str]:           # (sal_setup, fish_setup)
def fish_street_line(hand, phase_name) -> str       # fish_streets[phase]
def holes_by_name(hand, roles) -> dict              # role→cards → name→cards for the rig
```

These are tiny, total, and side-effect-free. `game_handler` is refactored to
**call these** for its decisions and keep ONLY the Flask I/O (the `send_message`
/ repo / socketio calls). No behavior change to the live game — same lines, same
judge, same rig — just sourced from one place.

**2. The headless runner** (`run_scene`):

```python
@dataclass
class NarrationEvent:
    trigger: str        # 'hand_open' | 'street' | 'verdict' | 'fish_react' | 'graduation'
    role: str           # 'mentor' | 'fish'
    line: str
    hand_idx: int

@dataclass
class SceneResult:
    final_stacks: dict[str, int]      # name -> stack
    busted: list[str]                 # names at 0 after the scene
    passed: int                       # teaching hands the hero passed
    hands_played: int
    completed: bool
    on_complete: str | None           # the scene's completion-effect key, if reached
    narration: list[NarrationEvent]   # the full ordered timeline

def run_scene(
    scene: TableScene,
    *,
    hero_choice,                      # (hand, scene_state, game_state) -> {'action','amount'}
    seats=...,                        # optional explicit seating; default human first
    max_hands=None,
) -> SceneResult:
    ...
```

The runner owns a real `PokerStateMachine` and drives it exactly like
`progress_game` does, minus Flask:

- `run_until_player_action()` to reach each decision (it auto-advances phase
  transitions, dealing the rigged deck the previous boundary provided);
- at an **AI** decision → `_scene_scripted_action`'s pure core
  (`resolve_scripted_action`, already pure) or a safe default (check/fold);
- at the **hero** decision → call `hero_choice(...)` (the test's injected line),
  validated against `current_player_options`;
- commit via `poker.poker_game.play_turn`;
- on a new street → record the `fish_street_line`;
- on **HAND_OVER** → run the shared judge + record the verdict/react events +
  the cast top-up (against an in-memory bankroll stub, or skipped) + rig the next
  hand via `holes_by_name` + `provide_hand_holes`;
- when the script is exhausted → record the graduation lines and set
  `on_complete`.

Narration goes to an **in-memory list** (the `NarrationEvent` timeline) instead
of `send_message`. Persistence and the real vouch DB-write are **not** invoked —
the runner reports `on_complete` as data; asserting the vouch *fires* is the
live handler's job (already covered by `TestCareerMentorStake` /
`_fire_career_first_vouch` tests). This keeps the runner Flask-free.

### Cast funding in the runner

The live `_scene_top_up_cast` debits real bankrolls. The runner can't (no DB),
and doesn't need to: it seeds the cast with healthy starting stacks up front
(mentor deep enough to cover the finale, fish at a soft-spot stack) so the
script plays. The finale's `bust_ok` then transfers the fish's chips to Sal —
**conservation holds in-memory** (no mint), which is exactly what we assert.

## Validation (the second deliverable)

`validate_scene(scene) -> list[str]` (errors; empty = valid), runnable at
registration and in a CI test:

- every rigged hand has a **5-card board**;
- **no duplicate card** across all holes + board in a hand (lift the deal-time
  collision check earlier — today it only raises inside `build_hand_deck` at
  deal time);
- every `fish_plan` / `mentor_plan` intent is a **known intent**
  (`resolve_scripted_action`'s vocabulary) and size tag is in `SIZE_FRAC`;
- `pass_when ∈ {folded, not_folded}`;
- a hand with a `lesson` has both `sal_pass` and `sal_fail` (no silent verdict);
- roles referenced in `holes` are in the scene's `cast` (+ `hero`).

A test asserts `validate_scene(SCENE0) == []` and that a deliberately-broken
scene (dup card, bad intent, missing verdict) is caught.

## Test list (`tests/test_cash_mode/test_scene_runner.py`)

1. **Finale canary** — `run_scene(SCENE0, hero_choice=fold-everything)` →
   `result.busted == ['Loose Larry']`, Larry's final stack 0, Sal's stack rose
   by ~Larry's lost chips (conservation), `on_complete == 'career_first_vouch'`,
   `completed is True`, graduation lines present in the timeline.
2. **Per-lesson forks** (the choice routing the judge already does):
   - value: hero stays → `passed` counts it, `sal_pass` in timeline;
   - bluff-catch: hero calls → pass; hero folds → `sal_fail`;
   - discipline: hero folds → pass; hero calls → `sal_fail`.
3. **Per-street fish tell** — discipline hand emits the FLOP `fish_streets` line
   and nothing on the turn (timeline assertion, headless).
4. **Conservation** — total chips across the cast constant across the scene
   (modulo the deliberate finale transfer).
5. **Validation** — `validate_scene(SCENE0) == []`; broken scenes are rejected.
6. **Shared-core parity** — the live `_advance_scene` judge matches
   `scene_runner.judge_hand` (guards against future divergence).

## Files

- **new** `cash_mode/scene_runner.py` — pure helpers + `run_scene` + `validate_scene`.
- **edit** `flask_app/handlers/game_handler.py` — `_advance_scene`,
  `_scene_scripted_action`, `handle_phase_cards_dealt` delegate to the pure
  helpers (no behavior change).
- **new** `tests/test_cash_mode/test_scene_runner.py`.
- **edit** `cash_mode/table_scenes.py` — optionally call `validate_scene` on
  `register()` (log-only, non-fatal) so authoring mistakes surface early.

## Risks / watch-items

- **Don't change live behavior.** The refactor must keep Scene-0 playing
  identically. The existing `test_scene0_intable.py` suite is the guard — keep it
  green throughout.
- **Driving the state machine headlessly** must match `progress_game`'s
  sequencing (`run_until_player_action`, HAND_OVER detection, next-hand deal
  consuming the provided deck). Mirror it; don't reinvent.
- **Hero-choice injection** must validate against `current_player_options` so a
  test can't drive an illegal action and get a misleading green.
- The runner is the seam future pillars hang on: keep `judge_hand` shaped so a
  branch **router** can replace the binary later (it already returns a value, not
  a void side-effect).

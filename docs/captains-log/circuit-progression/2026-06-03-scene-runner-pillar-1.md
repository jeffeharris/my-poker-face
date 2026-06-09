---
purpose: Narrative log of building the headless SceneRunner (Scene Engine Pillar 1) and the two finale bugs it immediately caught
type: reference
created: 2026-06-03
last_updated: 2026-06-03
---

# Session 4 — the SceneRunner earns its keep on day one

## Where I picked up

The M1 handoff's top "next step" was **verify the Scene-0 finale live** — the one
thing automated tests supposedly couldn't cover: play to graduation, watch Sal
bust Larry to 0, confirm the comp-return + mentor-stake handoff. But the two
commits made earlier that same day were the **Scene Engine vision** docs, which
reframe exactly that step: Pillar 1 (testability-first) is a headless runner that
asserts the finale *in CI* instead of by hand. Asked Jeff which thread to pull; he
picked the runner ("it would help with validation, so let's do that first").

## The design call: a shared core, not a parallel one

The trap with a "headless driver" is that it becomes a *second* implementation of
the scene logic that drifts from the live one — so a green test proves nothing
about the real game. So the seam I cut was: pull the **judge**, the
**narration-line selection**, the **scripted-action resolution**, and the
**rig-by-name** out of `game_handler._advance_scene` into pure functions in a new
`cash_mode/scene_runner.py`, and make the live handler **delegate** to them. The
runner and the live game now run the *same* code; the runner just swaps Flask I/O
(send_message / socketio / repos) for an in-memory narration timeline.

`run_scene` drives a real `PokerStateMachine` exactly like `progress_game` does,
minus Flask. Getting that right took three corrections, each a thing I'd assumed
the state machine did for me and didn't:

1. **The player doesn't advance on its own.** First run hung on hand 0 — the
   current player never moved off "You". The state machine's `advance_state`
   handles *phase* transitions, not the within-round hand-off; the live handler
   calls `advance_to_next_active_player(game_state)` after every `play_turn`. Added
   that and the hand flowed.
2. **HAND_OVER vs. dealing the next hand.** I needed to stop at HAND_OVER to rig
   the *next* hand before it deals, but `run_until_player_action` blows past
   HAND_OVER into the next deal. Switched to `run_until([HAND_OVER])` for in-hand
   advancing, and a deliberate "step off HAND_OVER, then run" to start each next
   hand (so the rig lands first).
3. **Run-it-out.** When everyone's all-in the machine sets `run_it_out=True` and
   hands control back rather than dealing the board itself — `progress_game` has a
   whole UI-paced block that, at its core, just forces the next phase
   (RIVER→SHOWDOWN, else DEALING_CARDS). Mirrored that one core line.

## The payoff: two finale bugs that "verify live" had been hiding

This is the part that justified the whole detour. With the runner working, the
finale **didn't bust Larry** — he kept a sliver (525 chips). Two bugs, both in the
shared `resolve_scripted_action`, both invisible without a deterministic driver:

- **`shove` under-shot.** It returned `raise to int(stack)`. But the engine's raise
  amount is the raise-*TO* total, so when Sal already had a posted bet on the
  street, "raise to stack" left that posted amount behind — Sal and Larry both
  ended with exactly their posted bet. Fixed: `shove` takes the explicit `all_in`
  action (which commits the whole stack regardless of the posted bet).
- **`passive` couldn't call off an all-in.** Facing Sal's shove, Larry's legal
  options were `['fold', 'all_in']` — there is **no `'call'`** when the call would
  commit your whole stack; the engine surfaces it as `all_in`. `passive` only knew
  `call`, so it returned None → fell through to fold. The fish literally could not
  call the shove. Fixed: under `bust_ok`, `passive` takes `all_in` to call off.

Both fixes flow back into the **live** game (shared code), so the live finale that
the handoff said to "verify live" was, in fact, broken in two ways — and would
have looked fine in a casual playtest (Larry loses most of his stack; who counts
the last 525?). The runner counts.

## What shipped

- `cash_mode/scene_runner.py`: `run_scene` → `SceneResult` (final stacks, busted,
  passed count, completion key, full narration timeline, `conserved`), the pure
  helpers, `hero_intent` / `hero_by_lesson` providers, and `validate_scene`.
- `game_handler` delegates judge / scripted-action / narration / rig to the shared
  helpers (no behaviour change for the live game beyond the two finale fixes).
- `tests/test_cash_mode/test_scene_runner.py`: finale canary, per-lesson forks
  (pass-all / fail-all / mixed), the discipline flop fish tell, conservation across
  hero lines, validation (dup card / bad intent / missing verdict / short board),
  and a judge-parity test. Updated the two `test_career_scene` shove/passive tests
  that had encoded the buggy behaviour.

1238 cash_mode tests green, ruff clean.

## Honest leftovers

- The runner deliberately doesn't fire the real vouch (a DB write) or the
  comp-return / mentor-stake socket handoff — those stay the live handler's job and
  have their own tests. A *one-time* live smoke of that tail is still worth doing.
- The judge is still binary (fold / not-fold). Making it the beat **router** (the
  choice-edge slice of Pillar 2) is the next real capability; I shaped `judge_hand`
  to return a value, not a side-effect, so a router can slot in without touching
  callers — but I didn't build it.
- The in-memory cast top-up mirrors the live one well enough to bust Larry
  reliably, but it's a stand-in, not the bankroll path; if the live top-up logic
  changes, the runner's seed/targets need to track it.

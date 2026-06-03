---
purpose: Scope a shared hook that hydrates (and flushes) live-game controller psychology from the persisted per-persona emotional state
type: design
created: 2026-06-03
last_updated: 2026-06-03
---

# Persona Psychology Hydration Hook — Scope

> **TRIAGE ref:** T3-77 (Tier 3). Live tables seat personas at baseline mood
> while the world remembers their real emotional state — connect the two.

## TL;DR

A persona's emotional state (energy / composure / confidence / tilt) is
persisted per persona in `ai_bankroll_state.emotional_state_json` (schema v97)
and evolves off-screen via the lobby sim. But when that persona **sits down at a
live table** — cash *or* tournament — its in-game controller starts at the
persona's *baseline* anchors, not the mood the world left it in. And the live
game **never writes its emotional changes back** to that blob. So the persisted
mood is read by the lobby card display and the off-screen sim only; the live felt
neither consumes nor updates it. Promote the sim's existing
`_hydrate_psychology` / `_flush_psychology` into a shared hook and wire it into
the live cash seat build and the tournament builder.

## Current behavior (verified)

There are **two** psychology stores that don't talk to each other on the live
felt:

| Store | Scope | Who reads/writes |
|-------|-------|------------------|
| `ai_bankroll_state.emotional_state_json` (v97) | **per persona** (`personality_id` + `sandbox_id`) | Off-screen sim hydrates + flushes (`cash_mode/full_sim.py`); lobby reads it for card emotion (`cash_routes.py:3379,5449,5970`); vice / side-hustle mutate it; recovers toward baseline while idle (`movement.py:283`) |
| per-game `psychology_json` / `controller_state` | **per game** | Live game saves after actions; cold-load restores it (`game_handler.py:461`) |

**The gap, confirmed by grep:**
- `_build_cash_game` (`cash_routes.py:622`) builds each AI controller via
  `build_controller` (lines 836, 852) and **never** hydrates from the persona
  blob — only the lobby *display* path reads it. → a persona that's been tilting
  all over the lobby sits down at your table **calm at baseline**.
- `save_emotional_state_json` appears **only** in `cash_mode/` (sim, vice,
  side-hustle) — **never** in `flask_app/`. → whatever mood a persona builds
  while playing *you* is lost to the world the moment the game ends.
- The tournament builder (`tournament_game_builder.py`) is the same: baseline
  seat, no hydrate, no flush (and `cash_mode=False`).

Net: the live table is psychologically disconnected from the persona's world
life in **both** directions.

## The reference implementation (already exists)

`cash_mode/full_sim.py` already does exactly this for the off-screen sim and is
almost generic as written — it only depends on a duck-typed `bankroll_repo`,
`personality_id`, `sandbox_id`, and the controller:

- `_hydrate_psychology(controller, pid, bankroll_repo, sandbox_id)` (line 427):
  reads the blob, `PlayerPsychology.from_dict(state_dict, personality_config)`,
  replaces `controller.psychology` in place. No-ops on NULL/parse-fail.
- `_serialize_psychology` (484) / `_flush_psychology` (501) / `_maybe_flush_psychology`
  (529): write `controller.psychology.to_dict()` back to the blob, with a
  per-controller flush cadence (`PSYCHOLOGY_FLUSH_EVERY_HANDS`).

## Proposed change

### 1. Promote the hook to a shared module

Move `_hydrate_psychology` / `_serialize_psychology` / `_flush_psychology`
(+ `_maybe_flush_psychology`) into a provider-neutral module, e.g.
`cash_mode/psychology_persistence.py` (or `poker/` if we want it cash-package
free), with neutral log tags (drop `[FULL_SIM]`). `full_sim.py` imports them
(behaviour-identical — verify with the existing cash sim bucket). Public surface:

```
hydrate_persona_psychology(controller, personality_id, bankroll_repo, sandbox_id) -> None
flush_persona_psychology(controller, personality_id, bankroll_repo, sandbox_id) -> None
```

### 2. Wire into the live cash seat build

In `_build_cash_game` (`cash_routes.py`), after each AI controller is built
(both the fish branch ~846 and the standard branch ~864), call
`hydrate_persona_psychology(controller, pid, bankroll_repo, sandbox_id)` for
seats with a real `pid`. `pid`, `bankroll_repo`, and the cash `sandbox_id` are
all already in scope. Human seat is skipped (no persona blob; identity is
`owner_id`).

### 3. Flush back from the live cash game

Add `flush_persona_psychology` at the live cash **leave/settle** boundary —
`_leave_table_locked` (`cash_routes.py:4459`) and the bust/vacate path — so a
persona that ran hot against you carries that mood back into the world. Leave-time
flush (vs per-hand) is the safe default: it avoids racing the off-screen sim's
writes for the same `(pid, sandbox_id)` and bounds state loss to one session.

### 4. Wire hydrate-only into the tournament builder

In `tournament_game_builder.py`, hydrate real-persona seats from the blob
(`sandbox_id` already resolved at line ~223) so the circuit's characters show up
to play you in the mood the world left them in. **Do NOT flush back from a
tournament** — a tournament is a separate event with reset chips; bleeding
tournament tilt into the persona's cash-world blob is undesirable. One-way
(read) for tournaments, two-way (read+write) for cash. *(Design decision — flag
for confirmation.)*

## Design decisions / things to get right

- **D1 — Cold-load must NOT hydrate.** Hydration belongs only on a *fresh* seat
  build (`_build_cash_game` / tournament builder). Cold-load
  (`restore_ai_controllers` → `game_handler.py:461`) must keep restoring the
  *per-game* `psychology_json`, or a mid-session reload would clobber the
  evolved in-game mood with the (staler) persona blob. The two paths are already
  separate; keep them so.
- **D2 — Cash two-way, tournament one-way.** See §4.
- **D3 — Concurrency.** A seated persona isn't simultaneously played by the
  off-screen sim (the sim plays *unseated* tables), so the main race is the
  flush boundary. Leave-time flush + best-effort error handling (already in the
  reference) is sufficient for v1; a CAS/last-writer guard is overkill.
- **D4 — Idle recovery on read.** The blob is recovered toward baseline while
  idle, but recovery is computed at read in some paths (`movement.py:1116`;
  `cash_routes.py:236` notes "Decay-on-read is a TODO"). Decide whether
  hydration applies idle recovery before seating (a rested persona sits down
  recovered) or trusts the last flushed value. v1: trust last flush (matches the
  lobby display); note the refinement.
- **D5 — Sandbox scoping.** The blob is keyed `(personality_id, sandbox_id)`.
  Both call sites already resolve the right `sandbox_id`; pass it through, never
  default to None (would cross-contaminate sandboxes).
- **D6 — Human seat.** No persona blob; skip (loop already skips `is_human`).

## Risks

- **R1 — Behaviour change is player-visible.** Opponents will now sometimes open
  a session already tilted/steaming. That's the goal, but it changes difficulty
  feel; worth a flag (e.g. reuse an existing cash psychology flag) if we want a
  kill switch for the first rollout.
- **R2 — Refactor of `full_sim`.** Moving the hook risks a behaviour drift in
  the sim. Mitigate: keep signatures identical, re-export, verify the cash sim
  bucket is bit/decision-stable (the project's standard equivalence gate).
- **R3 — Double counting `_maybe_flush` cadence attr.** The per-controller hand
  counter attr is sim-specific; the live path should use leave-time flush, not
  the cadence, to avoid coupling to sim constants.

## Sequencing

1. Promote hook to shared module; `full_sim` imports it; verify sim equivalence.
2. Hydrate in `_build_cash_game` (both branches). Manually verify a lobby-tilted
   persona sits down tilted on the live felt.
3. Flush in `_leave_table_locked` + bust/vacate. Verify a session that tilts a
   persona updates the lobby card emotion after leaving.
4. Hydrate-only in `tournament_game_builder`. Verify circuit personas enter the
   Main Event in their world mood; confirm no write-back to the cash blob.

## Test strategy

- Round-trip: seed `emotional_state_json` for a pid → build a live cash game →
  assert the controller's `psychology` matches (not baseline).
- Flush: tilt a controller in a live game → leave → assert the blob updated.
- Tournament one-way: hydrate present, flush absent (assert blob unchanged after
  a tournament that moved the persona's mood).
- Cold-load isolation (D1): mid-session reload restores per-game psychology, does
  NOT re-hydrate from the persona blob.
- Sim equivalence after the module move (R2).

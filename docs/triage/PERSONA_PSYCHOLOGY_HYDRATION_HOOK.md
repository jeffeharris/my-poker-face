---
purpose: Scope a shared hook that hydrates (and flushes) live-game controller psychology from the persisted per-persona emotional state
type: design
created: 2026-06-03
last_updated: 2026-06-03
---

# Persona Psychology Hydration Hook — Scope

> **TRIAGE ref:** T3-77 (Tier 3). Live tables seat personas at baseline mood
> while the world remembers their real emotional state — connect the two.
>
> **STATUS: IMPLEMENTED (2026-06-03, branch `tournaments`).** Shipped in three
> commits: shared module (`refactor(psych)`), live cash two-way
> (`feat(cash)`), cash-world tournament two-way (`feat(tournament)`). Tests:
> 8 hook round-trip + 2 completion-gate, cash slice 343 + tournament bucket 287
> green. **Deferred refinements** (none block the feature): see §Deferred below.

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

### 4. Wire the tournament builder — gated on cash-world, not on "is a tournament"

The unit of psychological continuity is the **cash world**, not the table type.
Emotional state persists across the cash world *including tournaments played in
it*; a tournament played outside the cash world starts from baseline.

- **Cash-world tournament** (the Circuit / Main Event — registered via
  `tournament_routes`, so it's sandbox-scoped and economy-bound: bankroll
  buy-in via `econ.apply_buy_in`, `ai:<pid>` payouts): **two-way**, identical to
  a cash table. Hydrate real-persona seats on build; flush on completion /
  bust / leave. Chips reset per tournament (economic), but *mood is continuous*
  with the cash world — a persona you tilt in the Main Event carries that mood
  back out, and arrives already in whatever mood the world left it.
- **Non-cash tournament** (a standard single-table `TournamentSession` from
  `game_routes`, experiments, synthetic `/register` fields — no economy
  binding): **neither** hydrate nor flush. Baseline.

**Gate signal:** the cash-world / economy binding (the same one that drives
bankroll buy-in and `ai:<pid>` payout — a resolved cash sandbox + economy-bound
session), **not** the chip-PnL `cash_mode` flag (which is off for tournaments by
design). If no single explicit signal exists on the session / game_data today,
add one (e.g. `is_cash_world: bool`) so the gate is read, not inferred — psychology
continuity should track economic continuity exactly.

## Design decisions / things to get right

- **D1 — Cold-load must NOT hydrate.** Hydration belongs only on a *fresh* seat
  build (`_build_cash_game` / tournament builder). Cold-load
  (`restore_ai_controllers` → `game_handler.py:461`) must keep restoring the
  *per-game* `psychology_json`, or a mid-session reload would clobber the
  evolved in-game mood with the (staler) persona blob. The two paths are already
  separate; keep them so.
- **D2 — Continuity follows the cash world, not the table type.** Cash tables
  and cash-world (Circuit) tournaments persist two-way; non-cash tournaments
  start at baseline and persist nothing. Gate on the economy binding, not the
  `cash_mode` chip flag. See §4. *(Confirmed by owner 2026-06-03.)*
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
4. Gate the tournament builder on the cash-world signal. For a cash-world
   (Circuit) tournament: hydrate on build + flush on completion/bust/leave
   (two-way). For a non-cash tournament: do nothing (baseline). Verify a
   cash-world persona enters the Main Event in its world mood AND that a session
   that moved its mood updates the lobby card after the tournament; verify a
   non-cash tournament neither reads nor writes the blob.

## Deferred refinements (post-implementation)

Tracked here so they're not silently lost; none block the shipped feature.
(Balanced-in re-hydrate and per-vacate flush are now closed — see below. Only
idle decay-on-read remains.)

- ~~**Balanced-in personas mid-tournament don't re-hydrate.**~~ **CLOSED
  (2026-06-03).** `reconcile_live_table` now hydrates a genuinely-new
  real-persona seat from the cash world (gated on a cash-world persona field +
  resolved sandbox, threaded via `game_data['tournament_sandbox_id']`; fresh
  build, not cold-load). +3 reconcile-hydration tests.
- ~~**Cash AI that leaves before the human isn't flushed individually.**~~
  **CLOSED (2026-06-03).** `_remove_departed_ais_from_game` flushes a departing
  AI's mood to the persona blob before dropping it, so a persona carries the mood
  onward even when it leaves mid-session. Race-free (a seated persona isn't
  sim-played). +2 tests.
- **Idle decay-on-read not applied at hydrate (D4).** Hydration trusts the last
  flushed blob; it doesn't re-apply idle recovery toward baseline at seat time.
  Matches the lobby display's current behaviour (`cash_routes.py` "Decay-on-read
  is a TODO").

## Test strategy

- Round-trip: seed `emotional_state_json` for a pid → build a live cash game →
  assert the controller's `psychology` matches (not baseline).
- Flush: tilt a controller in a live game → leave → assert the blob updated.
- Cash-world (Circuit) tournament two-way: hydrate on build AND flush on
  completion (assert blob updated after a tournament that moved the mood).
- Non-cash tournament baseline: neither hydrate nor flush (assert controller
  starts at baseline and blob is unchanged).
- Cold-load isolation (D1): mid-session reload restores per-game psychology, does
  NOT re-hydrate from the persona blob.
- Sim equivalence after the module move (R2).

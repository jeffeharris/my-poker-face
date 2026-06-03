---
purpose: Narrative log of wiring AI↔AI relationship evolution into the cash lobby sim
type: guide
created: 2026-06-02
last_updated: 2026-06-02
---

## Addendum — STACK_DOMINANCE wired in (same day)

Jeff asked whether the relationship-simming is a default/env setting to
remember for migrations. Answer: no. No feature flag gates it (unconditional
code); `relationship_states` (v87) + `cash_pair_stats` (v87/v109) are base
schema created by `ensure_schema()` in every env incl. fresh; the only
prerequisite is the standard DB path config every env already has. The
`_db_path` fix is code, not config. Just deploy.

Then: "wire in stack dominance as long as the leave pressure is also wired
in." Initial read was "dominated short stack should feel pressure to leave" —
wrong. Jeff clarified he meant the *deep* stack ("stack too big for the
table") should feel leave pressure, and that it already exists. It does: the
`stake_up` term in `compute_leave_pressure` (`movement.py:249-252,270`) —
`seat_over_tier = ai_chips/max_bi - 1` plus the wealth "slumming" climb —
routes an over-cap stack to `stake_up`/`take_break`, and movement already runs
per sim hand via `refresh_table_roster` ← `refresh_unseated_tables`. So the
behavioral outlet was present; STACK_DOMINANCE just adds the *others'*
resentment toward the deep stack.

Wired it: threaded `table_max_buy_in` through `play_one_hand` →
`_play_one_hand_inner` → `set_table_max_buy_in()` per-hand before
`on_hand_complete` (manager is per-sandbox, shared across stakes, so it's
set per-hand not once). Lobby passes the `table_max_buy_in` it already
computes at `lobby.py:1574`. Detector heat is threshold-gated (≥1.5× cap) +
saturation-capped, so low blast radius.

Note: lobby.py already carries a pre-existing ruff I001 (unsorted import
block, lines 23-38) on HEAD — left untouched; my change is one line.

# Lobby sim — relationships evolving off-screen

## Where it started

Jeff was looking at a live cash game (`cash-wSJs3s2aqePyOAyk2LapOg`) in the
debug tool and saw most relationships pinned at 0.5/0.5 with no heat. First
question: is that legit?

It was. The relationship system (`relationship_states`, keyed by
`(observer_id, opponent_id)` — no game/sandbox scope, so values are global
per-pair and persist across games) only writes a row once a *qualifying
event* fires: big-pot showdowns, bluffs, hero calls, coolers, chat events,
staking, stack-dominance. No row → neutral defaults. His game was ~5 min old;
only `abraham_lincoln→edgar_allan_poe` had clashed (heat 0.25). His own pair
rows with those 5 opponents didn't exist yet. The DB at large was healthy
(188/190 rows moved off defaults, incl. his heat 0.80 toward Frida Kahlo).

## The real ask

"Cash lobby sim should be simming the relationships — that should be evolving
in games you're not in." Correct, and a known-deferred gap: the full-sim
handoff doc (`CASH_MODE_FULL_SIM_HANDOFF.md:219-221`) explicitly flagged
AI↔AI relationship persistence as a consideration, but the build folded in
emotional-state + opponent-model persistence and left relationships out.

The lobby sim (`full_sim.py`) already wired the *opponent-model* half of the
memory pipeline (`on_hand_start` + `on_action` + flush) but never called
`set_relationship_repo()` nor `on_hand_complete()` — the only thing that runs
relationship detection.

## What the background investigation changed

Before coding I ran an agent to scope full hand_history parity. Two findings
reshaped the "lean" plan:

1. The dominant per-hand cost in `on_hand_complete` is **not** the DB write —
   it's `_record_showdown_equity_at_actions` (inline eval7, `iterations=400`
   per postflop showdown action), gated on `was_showdown`, not persistence.
   Just *calling* `on_hand_complete` pays it, synchronously, on the lobby
   thread. → Added a `record_showdown_equity` flag to skip it in sim.
2. `hand_recorder.completed_hands` appends every hand on a per-sandbox manager
   that lives for the whole process → unbounded leak. → Clear it after each
   call.

Recommendation (kept): **lean permanently**. Sim hand_history is write-only
telemetry nothing reads; full parity would add unbounded GB/month to the live
SQLite file and write-lock contention, for no consumer.

## The wrong turn that turned out to be a real bug

Wired everything, wrote an end-to-end test (heads-up, 15 BB stacks so all-ins
clear the `pot > 0.75·avg` big-pot gate, 120 hands, assert `cash_pair_stats`
+ moved `relationship_states`). It failed: 0 cash_pair_stats rows.

Root cause: `full_sim.py` resolved the DB path via `bankroll_repo._db_path`
behind a `hasattr(..., '_db_path')` guard. **No repo in the codebase defines
`_db_path`** — `BaseRepository` uses `self.db_path`. So the guard was always
False and `db_path_for_memory` was always `None`. That silently disabled not
just my relationship wiring but the *pre-existing* opponent-model
persistence/restore + flush in the lobby sim — dead since it shipped (the
in-memory accumulation within a process masked it). Fixed to
`getattr(bankroll_repo, 'db_path', None)` with `_db_path` fallback. Test
went green.

This is the "verify the premise / check real health signals" lesson again:
the feature looked wired, but the load-bearing path was a no-op.

## State

- Changed: `cash_mode/full_sim.py`, `poker/memory/memory_manager.py`,
  `tests/test_cash_mode/test_full_sim.py`. Uncommitted.
- `cash_mode/` + `test_memory/` + relationship-repo suites green; ruff clean.
- Lean by construction: no `set_hand_history_repo` (so `_persistence=None`,
  no per-hand hand_history INSERT), `record_showdown_equity=False`,
  `equity_history=None` (no BAD_BEAT), `skip_commentary=True`.
- Deferred for v1: STACK_DOMINANCE in sim (needs per-table `max_buy_in`; the
  per-sandbox manager spans tables of differing stakes), and BAD_BEAT (needs
  pre-river equity we don't compute on this path).

## Worth a second look

- The `db_path` fix *activates* opponent-model flush every 10 hands in the
  lobby sim that was previously dead — intended design, but it's a real
  behavior change (now actually writing opponent models to the live DB).
- STACK_DOMINANCE: if wanted, thread the table cap into `_play_one_hand_inner`
  and call `set_table_max_buy_in()` per-hand before `on_hand_complete`.

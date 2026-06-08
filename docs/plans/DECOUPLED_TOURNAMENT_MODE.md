---
purpose: Design for a standalone "Tournament Mode" — on-demand multi-table tournaments fully isolated from the persistent cash world (no money, emotion, renown, or shared stats)
type: design
created: 2026-06-08
last_updated: 2026-06-08
---

# Decoupled Tournament Mode

> **Status:** plan / not yet built. Supersedes the broken tournaments-menu
> "Main Event" button (which still calls the removed `/api/tournament/register`
> → 405).

## Why this exists

There are currently **two different things both called "Main Event":**

1. **Cash-circuit Main Event** — the economy-gated *invite* (bank flush +
   cooldown), offered in the **cash lobby**. Deeply coupled to the persistent
   world: buy-in from bankroll, prize → bankroll, persona mood carried in/out,
   renown granted on finish. This is working and stays as-is.
2. **Tournaments-menu "Main Event"** — a **standalone, on-demand** multi-table
   tournament the player starts from the Tournaments menu (`TournamentMenu` →
   `onMultiTable` → `/tournament` → `TournamentPage`/`TournamentLobby`, with a
   field-size/table-size form). Its backend route `/api/tournament/register`
   was **removed** in `130ee314` (it seated the human as a synthetic `P01`),
   leaving the UI orphaned → **405**.

This doc defines #2 as a clean, **isolated Tournament Mode**: real-persona
identities for flavor, but **no wires to the persistent world**. Everyone starts
at baseline; nothing the tournament does leaks back out.

## Design: an isolated mode

A standalone tournament is a self-contained exhibition. Four seams are cut vs.
the cash-circuit event:

| Seam | Coupled today | Decoupled mode |
|---|---|---|
| **Money** | `apply_buy_in` from bankroll; prize pool funded from bank; payout → bankroll | `buy_in=0`, no prize pool, no payout |
| **Emotion** | personas hydrated from `emotional_state_json` (T3-77) and flushed back on finish | baseline mood for all personas; nothing flushed back |
| **Renown** | `grant_on_payout` on completion (gated on `TOURNAMENT_DRAW_ENABLED`) | none |
| **Stats / history** | persona durable stats (opponent models, relationship axes, `cash_pair_stats`) updated | **tournament-mode stats only**; no writes to cash/global persona stats |

Everyone (human + personas) starts at **baseline**: fresh `starting_stack`,
neutral psychology, no prior-history context.

## Where the couplings live (seams to cut)

- **Money:** `flask_app/services/tournament_spawn.py::create_human_tournament`
  (`apply_buy_in`, `plan_funding`); `flask_app/handlers/tournament_completion.py`
  (payout schedule).
- **Emotion:** `flask_app/handlers/tournament_game_builder.py` — hydration is
  gated on `real_persona_ids` (≈ lines 215/254/276-294,
  `hydrate_persona_psychology`). Baseline = skip hydration even for a
  real-persona field.
- **Renown:** `tournament_completion.py` (`grant_on_payout` / `payout_breakdown`,
  gated on `economy_flags.TOURNAMENT_DRAW_ENABLED`).
- **Stats / history:** controllers are wired to durable `session_memory` +
  `opponent_model_manager` in the builder (≈ lines 270-272); `on_hand_complete`
  updates opponent models / relationship axes / `cash_pair_stats`. Decoupled =
  ephemeral memory, **no durable persona-stat writes**. Tournament career stats
  (`tournament_repo.get_career_stats`) are **already separate** and stay.
  `hand_history` rows are game-id-scoped and are fine to keep (they're how the
  felt/recap work — and feed the recorder repro, see below).

## Engine choice

- **A — `decoupled=True` on the live human-tournament path (recommended).**
  Thread an `exhibition`/`decoupled` flag onto the tournament *session/record*,
  set it on spawn, and check it at each seam (spawn skips buy-in/funding;
  builder skips hydration + uses ephemeral memory; completion skips
  payout/renown; stats writes gated off). Reuses the exact live path the human
  already plays (game builder → `progress_game` → hand recording → run-out
  sequencer), so the felt, animations, and recap all "just work."
- **B — build on the headless engine** (`tournament/run.py` /
  `director.build_initial_state`). Decoupled by nature (synthetic field, no
  economy), but it's an **AI-only headless sim** — it does not drive a live
  human-played table over sockets. **Unsuitable** for a mode the human plays in.

→ **Go with A.** The decoupling is "skip the world side-effects on the existing
live path," not a new engine.

## Implementation sketch (Option A)

1. **Session flag.** Add a `decoupled`/`exhibition` boolean to the tournament
   session/registry record. One source of truth checked at every seam.
2. **Backend route.** `POST /api/tournament/spawn` (new, on-demand): resolves
   owner + sandbox, leaves any cash seat, holds the sandbox lock, calls
   `create_human_tournament(..., buy_in=0, decoupled=True, field_size,
   table_size, starting_stack)`, returns `{tournament_id}` → client `/sit`.
   (Do **not** revive `/register`.)
3. **Spawn (`create_human_tournament`).** When `decoupled`: skip `apply_buy_in`
   / `plan_funding` (prize pool 0), still draft the real-persona field, stamp
   `decoupled` on the session.
4. **Builder.** When `session.decoupled`: skip `hydrate_persona_psychology`
   (baseline mood) and wire controllers to **ephemeral** memory (no durable
   opponent-model / relationship persistence).
5. **Completion.** When `decoupled`: skip payout + renown; still produce
   standings / finishing positions for display + tournament-mode stats.
6. **Stats.** Gate the durable persona-stat writes (opponent models,
   relationship axes, `cash_pair_stats`) on `not decoupled`. Keep tournament
   career stats + `hand_history`.
7. **Frontend.** Repoint `TournamentPage.handleRegister` from the (currently
   wrongly-wired) invite flow to `tournamentApi.spawn(body)` with the lobby's
   field/table size. (Reverts the interim invite-flow patch.)

## Review hardening (Codex, 2026-06-08)

A Codex review caught gaps the seam map alone missed — all about the flag
needing to survive **cold-load** and **cross-worker**, not just the fresh build:

- **[High] Cold-load re-couples.** `sandbox_id=None` only fixes the *fresh*
  builder. On reload/resume, `game_routes.py` (~756) re-derives a sandbox for any
  `game_id.startswith("tourney-")` and re-wires the relationship repo (~924) —
  silently re-coupling a decoupled tournament. **Required:** persist `decoupled`
  on `TournamentSession.to_dict/from_dict` (in `session_json`) and **gate the
  cold-load path** on it (skip sandbox resolution + repo wiring + lifetime fold
  + psychology save) before any of that work runs.
- **[High] Active-guard exemption must be persistent, not in-memory.** The
  one-active/lobby checks (which today only exclude `resolver_kind=='single'`)
  must read the persisted `decoupled` flag so a decoupled row can't block the
  cash-circuit Main Event invite after rehydrate. Also audit
  `active_participant_pids()` so decoupled personas aren't marked unavailable to
  cash games / real Main Event drafts (they're isolated copies).
- **[Med] Bypass economy/sandbox work *early*.** Skip funding + sandbox-lock
  work before resolving sandbox context, not after.
- **[Med] Display suppression.** Completion payload still reports
  `renown_enabled` from the global flag even at `prize_pool=0`. Force
  `renown_enabled=False` and suppress purse/payout/renown affordances (API + UI)
  for decoupled exhibitions.
- **[Med] Spawn route semantics.** Validate field/table sizes; if the persona
  pool is smaller than `field_size`, return the actual field size; define the
  cash-seat-leave behavior; decide whether decoupled participants touch cash
  whereabouts at all (default: no).
- **Confirmed sound:** `payout_status='skipped'` blocks payouts + renown (the
  grant lives inside the payout block).

### Engine collapse (quick-start STT ↔ Main Event MTT): DEFER

Codex verdict: do **not** unify now. Quick-start (`/api/new-game` →
`single_table_tournament.build_session_for_new_game`) keeps the live
`PokerStateMachine` authoritative for blinds and supports casual knobs + custom
opponent selection; the MTT path uses a boundary-driven blind clock + persona
drafting; and `single_table` cannot be inferred from `field_size==table_size`
(explicit mode flag required). Ship the decoupled MTT spawn first; later factor a
shared factory with explicit `single_table`/`multi_table` modes. (An STT *is*
conceptually a 1-table MTT — the collapse is right eventually, just not in this
change.)

## Relation to the recorder bug (#9)

Decoupled tournaments still use the same live hand-recording path, so the
**dropped-actions bug (#9)** — actions silently lost when `current_hand` is None
during MTT hand boundaries — still applies and still needs fixing. The loud
drop-log is already in place (`hand_history.record_action`); once a standalone
tournament can be started on demand, it's the clean repro vehicle for #9.

## Decisions (resolved 2026-06-08)

- **Flag storage:** carry `decoupled` in the **`session_json` blob** (alongside
  `single_table`) — no schema migration (avoids cross-worktree migration
  collisions), survives cold-load. Propagated into the registry record.
- **Two master levers** (from exploration — much smaller than 4 separate cuts):
  - Builder sets **`sandbox_id = None`** when `session.decoupled` → no-ops ALL
    durable persona writes at once (psychology hydration carry-in,
    `relationship_states`, `opponent_observation_lifetime` fold, and the
    completion flush-back — every one is already `sandbox_id`-gated). Also set
    `game_data['tournament_is_persona_field'] = False` / `tournament_sandbox_id
    = None` to belt-and-suspenders the flush.
  - Spawn skips `plan_funding`/`apply_buy_in` and stamps
    **`payout_status='skipped'`** → all three payout call-sites and the renown
    grant (which lives inside the payout block) become no-ops automatically.
    (`buy_in=0` alone is NOT enough — a flush bank still draws an overlay.)
- **Ephemeral memory:** no `NullMemoryManager` needed — not wiring the
  relationship repo + leaving `sandbox_id=None` IS the ephemeral path.
- **Stats surface → SAME tournament career stats.** Tournament career stats are
  already separate from cash; decoupled results write there like any tournament
  (no `update_career_stats` gate). `hand_history` kept (game-id-scoped).
- **Field draft → real sandbox personas at baseline.** Reuse
  `create_human_tournament` drafting; baseline mood via the `sandbox_id=None`
  lever. Field/table size from the lobby form.
- **Coexistence → EXEMPT from the one-active-per-owner guard.** A decoupled
  tournament must not be blocked by, nor shadow/block, the cash-circuit Main
  Event. Requires the active-tournament guard (`create_human_tournament`) and
  the lobby's `find_active_for_owner` to ignore `decoupled` rows (today
  `find_active_for_owner` already excludes `resolver_kind='single'`; add a
  `decoupled` exclusion alongside it).

## Out of scope

- The cash-circuit invite Main Event (unchanged).
- The headless AI-only tournament sim engine (unchanged).
- Narrative-beats / commentary work (separate track; EXP_008).

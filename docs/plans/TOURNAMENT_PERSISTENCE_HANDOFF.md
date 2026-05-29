---
purpose: Implementation handoff for multi-table tournament persistence + an inventory of all remaining tournament work
type: design
created: 2026-05-29
last_updated: 2026-05-29
---

> **Update 2026-05-29:** P1 #2 (frontend live tournament events) is now done —
> see that item below. Persistence (P1 #1) remains the next piece.

# Multi-Table Tournament — Persistence Handoff + Remaining Work

Handoff for a fresh context. The multi-table tournament feature is built and works
**in a stable server process** through Phase 2c (live human play). The next piece
is **persistence**, and there's a backlog beyond it. Read
`docs/plans/MULTI_TABLE_TOURNAMENT_PLAN.md` first for the vision/architecture.

Branch: `tournaments` (commits local, not pushed as of 2026-05-29).

---

## Where things stand (done + working)

- **Headless engine** (`tournament/`): `TournamentDirector`, `TournamentField`,
  `Seating`/`SeatingManager` (break/balance/final-table), `BlindSchedule`,
  `TournamentSession`. Pure, conservation-asserted, fully unit-tested.
- **Core engine fix**: `determine_winner` side-pot dead-money leak (lone-eligible
  side tier) — fixed + regression test. Benefits all game modes.
- **Phase 2a — API**: `flask_app/services/tournament_registry.py` (in-memory) +
  `flask_app/routes/tournament_routes.py` (register / lobby / sit / standings /
  advance / play-out / leave).
- **Phase 2b — Standings UI**: `react/.../components/tournament/` (broadcast
  tournament-clock aesthetic), route `/tournament`, entry on the tournament menu.
- **Phase 2c — Live bridge**: `tournament_game_builder.build_tournament_game`
  (builds the human's live table; no-LLM tiered seats; omits `tournament_tracker`
  + `cash_mode` so the single-table elimination/cash paths early-return);
  `tournament_handler` (`coordinate_after_human_hand`, `reconcile_live_table`,
  `advance_tournament_after_hand`); gated hook in
  `game_handler.handle_evaluating_hand_phase`; `POST /api/tournament/<id>/sit`;
  frontend live-play wiring (register → sit → `/game/:id`; back → standings hub).
- **Verified**: the live loop is proven by `tests/test_tournament/test_live_play_integration.py`
  (drives the human through real `progress_game` hands; settles on the human
  every hand, conserves chips, reaches terminal, no redeal loop). 64 tournament
  tests green.

### The gap this doc addresses

The **`TournamentSession` lives only in the in-memory `tournament_registry`**, and
the saved game row has **no link back to its tournament**. So:

- Re-entering from the menu only works while the same server process is alive
  (the in-memory registry survives navigation but not restart/eviction).
- Opening the saved `tourney-…` game by any other route cold-loads it as a plain
  6-player game — the hook's gate (`game_data['tournament_session']`) is empty, so
  it's "not connected to a tournament anymore."
- The dev server runs `socketio.run(..., debug=True)` → the Werkzeug auto-reloader
  restarts on every `.py` save, wiping the in-memory game + session. (This caused
  the "redealing / no action" churn during development; persistence makes
  tournaments survive it, same as cash sessions cold-load today.)

---

## Persistence — implementation spec

Goal: a tournament is **durably re-enterable** (survives navigation, 2h TTL
eviction, server restart/reload), and opening its live game **reconnects** it to
the tournament. Model mirrors how cash sessions persist + cold-load.

### 1. Schema (one migration, bump `schema_manager.py` version)

```sql
CREATE TABLE tournaments (
    tournament_id TEXT PRIMARY KEY,
    owner_id      TEXT NOT NULL,
    game_id       TEXT,                       -- human's live table (NULL until sit)
    status        TEXT NOT NULL,              -- 'active' | 'complete'
    resolver_kind TEXT NOT NULL DEFAULT 'fake',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    session_json  TEXT NOT NULL               -- serialized TournamentSession
);
CREATE INDEX idx_tournaments_owner  ON tournaments(owner_id, status);
CREATE INDEX idx_tournaments_game   ON tournaments(game_id);
```

`session_json` is the source of truth for the meta-layer; the live per-table hand
state stays in the existing `games` row (saved by `game_repo.save_game`).

### 2. Serialization (pure, test first)

Add `to_dict` / `from_dict` to the pure types (no engine deps):

- `TournamentConfig.to_dict/from_dict` — field_size, table_size, starting_stack,
  seed, starting_big_blind, blind_growth, rounds_per_level, field_archetypes,
  max_rounds.
- `TournamentField.to_dict/from_dict` — starting_stack, entries, stacks,
  eliminations (`Elimination` is a frozen dataclass → list of dicts).
- `Seating.to_dict/from_dict` + `Table` — table_size, tables `[{table_id, seats:
  [pid|null], button}]`.
- `TournamentSession.to_dict()` — config + entries + human_id + rounds +
  `_hand_counter` + field + seating. `TournamentSession.from_dict(d, ai_resolver)`
  rebuilds, taking the resolver as an arg (resolvers are NOT serialized; rebuild
  from `resolver_kind` — `EngineHandResolver(entries)` or `FakeHandResolver()`).
- On rehydrate, **assert `field.chip_sum() == field_size * starting_stack`** (the
  conservation invariant catches a corrupt/partial restore immediately).

### 3. Repository

`poker/repositories/tournament_session_repository.py` (or extend
`tournament_repository.py`): `save(record)`, `load(tournament_id)`,
`find_active_for_owner(owner_id)`, `find_by_game_id(game_id)`,
`set_status(tournament_id, status)`, `set_game_id(tournament_id, game_id)`.

### 4. Save points (persist on the beats the state changes)

The world is **player-gated** — the session only changes at these moments, so
persist at each:

- **register** (`tournament_routes.register_tournament`): insert the row (status
  `active`, `game_id` NULL).
- **sit** (`build_tournament_game` / sit route): `set_game_id`.
- **hand boundary** (`tournament_game_builder.tournament_hand_boundary`, after
  `advance_tournament_after_hand`): re-`save` the session_json; flip status to
  `complete` on COMPLETE/HUMAN_OUT-then-finished. **This is the critical one** —
  it captures field/seating/standings after every advance.
- **leave** (`tournament_routes.leave_tournament`): `set_status('complete')` or
  delete.

Make `tournament_registry` **write-through**: `get`/`find_active_for_owner` fall
back to the repo on a memory miss and rehydrate into memory.

### 5. Cold-load rehydration (the wiring that reconnects an opened game)

Find the cold-load path (GET `/api/game-state` / `_authorize_game_access` /
wherever cash games cold-load — `game_routes.py`, search the `is_cash_game`
branch). Add a tournament branch gated on the `tourney-` id prefix:

1. `repo.find_by_game_id(game_id)` → tournament row.
2. Rebuild the resolver from `resolver_kind`.
3. `TournamentSession.from_dict(session_json, resolver)`.
4. Re-attach to `game_data`: `tournament_session`, `tournament_id`,
   `tournament_table_id` (= `session.human_table.table_id`), `tournament_human_id`.

This mirrors cash's cold-load session restore. After this, the gated hook fires
again and the game is "connected" once more.

### 6. Lobby reads through the store

`tournament_routes.get_lobby` / `find_active_for_owner` read from the repo (via
the write-through registry) so re-entry works after a restart.

### 7. Tests

- Pure round-trip: `to_dict`→`from_dict` equals (incl. conservation assert).
- Repo save/load on a temp DB.
- Cold-load: build a tournament game, evict it from `game_state_service`, hit the
  game-state load path, assert `tournament_session` is restored and a subsequent
  boundary advances correctly.
- Re-entry: register → sit → (simulate eviction) → `GET /api/tournament/lobby`
  still returns the active tournament with correct standings.

### Gotchas / invariants

- **Conservation**: assert on every rehydrate; never let a restore silently drop
  chips.
- **Ghost-seat class** (the project's recurring bug): relocation/reconcile already
  rebuilds the live roster from field truth — keep it that way; don't hand-mutate
  seats during restore.
- **Don't drift**: the live `games` row and the `tournaments` row are saved
  separately; persist them together at the hand boundary so a crash between the
  two can't desync stacks. (The hook already runs inside `progress_game`'s game
  lock — persist there.)
- **resolver_kind** must round-trip (engine vs fake) so re-entry keeps the same
  AI-table fidelity.
- Don't persist controllers / sockets / the resolver object — rebuild them.

### Files to touch

`poker/repositories/schema_manager.py` (migration) ·
`poker/repositories/tournament_session_repository.py` (new) ·
`tournament/{session,field,seating,config}.py` (to_dict/from_dict) ·
`flask_app/services/tournament_registry.py` (write-through) ·
`flask_app/routes/tournament_routes.py` (persist on register/sit/leave; lobby
reads store) · `flask_app/handlers/tournament_game_builder.py` (persist on
boundary + a `rehydrate_tournament_session(game_data, game_id)` helper) ·
`flask_app/routes/game_routes.py` (cold-load branch) · tests under
`tests/test_tournament/`.

---

## Everything else still left (prioritized)

### P1 — durability + live UX (finish the live experience)
1. **Persistence** (this doc).
2. **Frontend live tournament events** — **DONE (2026-05-29).** The MTT events
   were renamed to the `mtt_` namespace (`mtt_update` / `mtt_relocated` /
   `mtt_eliminated` / `mtt_complete`) to avoid colliding with the legacy
   single-table `tournament_complete` (different payload, feeds the
   `TournamentResult` overlay). New `react/.../hooks/useTournamentEvents.ts`
   consumes them on the game-page socket (joined to the lobby room on connect):
   relocation toast, and a delayed route to the standings hub on bust/complete
   (the `TournamentStandings` hub already renders the busted rank + champion
   band — that *is* the end screen). Wired in `PokerTable` + `MobilePokerTable`.
   `TournamentPage` also subscribes to `mtt_update` to keep the hub fresh during
   a play-out (cash `Lobby.tsx` socket pattern). Backend emit sites:
   `tournament_game_builder._emit_tournament` + `tournament_routes._emit_update`.
3. **Human seat display polish**: the live table seat is the field id (e.g.
   `P01`); the human is identified by `is_human` (works), but show "You" and hide
   the archetype label for the human at both the table and standings.

### P2 — economy (original Phase 3/4)
4. **Buy-ins / prize pool / payouts**: bankroll pays the buy-in → flat
   tournament-chip stack; payout settles to bankroll. The **tournament is a ledger
   counterparty** (`buy-ins == winnings + rake`) running alongside the funny-money
   `sum(stacks)==field×start` invariant — see the plan doc's "tournament as a
   ledger actor" section. Reuses the stakes system for staking-into-entries.

### P3 — circuit / recurring (original Phase 5)
5. **Daily / circuit tournaments**: a scheduler over the existing
   `create_tournament`/register entry point; recurring events the player plans
   around. Career-mode integration carries relationships/history in and out
   ("real social context, fake chips" — see plan doc).

### P4 — meta (original Phase 6)
6. **Prestige / achievements**: deep runs / final tables / wins feed
   `cash_mode/prestige.py` (add a `renown_tournament` component) and the
   `ACHIEVEMENTS_SYSTEM.md` `tournament` category.
7. **Cross-table ticker drama**: tournament event types in the activity ticker +
   the interhand "Meanwhile…" surface (chip leader, table breaks, pay jumps,
   bubble, knockouts).

### P5 — realism / scale / cleanup
8. **Seat realism**: dead-button rule (currently button just moves to the next
   occupied seat — documented simplification in `tournament/seating.py`).
9. **Archetype flavor at the human's live table**: currently plain `sharp` tiered
   bots; map field archetypes (TAG/LAG/…) or real personalities onto the live
   opponents.
10. **Scale**: validate 100+ entrant / 10+ table fields; tune default blind
    structures; configurable custom tournaments (the engine already takes
    arbitrary field/table sizes).
11. **Endpoint hygiene**: `POST /api/tournament/<id>/advance` is a dev/simulation
    affordance — decide whether to keep (gated to non-live) or remove now that
    live play exists. `play-out` is real (post-bust spectate / fast-forward).
12. **Dev caveat (not a feature)**: `debug=True` reloader wipes in-memory games on
    `.py` saves — affects cash too; persistence (P1.1) makes tournaments survive
    it. Don't edit backend files while someone is mid-hand on the dev server.

---

## Quick reference — key files

| Area | File |
|---|---|
| Engine (pure) | `tournament/{director,session,field,seating,blinds,config}.py` |
| Live builder + hook | `flask_app/handlers/tournament_game_builder.py`, `flask_app/handlers/tournament_handler.py` |
| Hand-boundary gate | `flask_app/handlers/game_handler.py` (`handle_evaluating_hand_phase`, the `tournament_session` block) |
| Registry / routes | `flask_app/services/tournament_registry.py`, `flask_app/routes/tournament_routes.py` |
| Frontend | `react/react/src/components/tournament/`, `utils/gameId.ts`, `game/GamePage.tsx` |
| Tests | `tests/test_tournament/` (incl. `test_live_play_integration.py`, slow) |
| Vision/architecture | `docs/plans/MULTI_TABLE_TOURNAMENT_PLAN.md` |
| Build narrative | `docs/captains-log/tournaments/multi-table-tournament-engine.md` |

---
purpose: Resume handoff for finishing tournament persistence Layer C (uncommitted) after an unstable tool session
type: guide
created: 2026-05-29
last_updated: 2026-05-29
---

# Tournament Persistence — Layer C Resume Handoff

A fresh-context handoff. Tournament persistence (P1 #1) is **almost done**: layers
A and B are committed; layer C is **fully written on disk but uncommitted and
unverified** because the tool environment kept dropping the Bash classifier
mid-run. Your job is small: **run three test files, fix anything red, then
commit.** Do NOT rewrite the code from scratch — it's already there and was
hand-reviewed. Verify, then commit.

> ⚠️ Why this handoff exists: during the build session the tool channel
> intermittently **fabricated** tool output (fake passing test runs, fake commit
> confirmations) during instability windows. Everything below was re-verified
> against `git` ground truth and direct file reads. **Trust nothing you don't
> re-run yourself.** Verify each claim with a real command before relying on it.

## Branch / commit state (verified via git)

Branch: `tournaments`. Recent commits (HEAD first):

```
ff89ab88 feat(tournament): persistence layer B — schema v123 + session repository
a367217c feat(tournament): persistence layer A — engine serialization + P1#3 standings
1864fd5b feat(tournament): frontend live MTT events (P1 #2)
39bb2c28 docs(tournament): persistence implementation handoff + remaining-work inventory
```

**Uncommitted working tree (this is Layer C — verify with `git status`):**
```
 M flask_app/handlers/tournament_game_builder.py
 M flask_app/services/tournament_registry.py
 M flask_app/routes/tournament_routes.py
 M flask_app/extensions.py
?? tests/test_tournament/test_registry_persistence.py
```

All commits are **local — nothing pushed**.

## What's already DONE and committed

- **Layer A (`a367217c`)** — pure engine serialization. `to_dict`/`from_dict` on
  `TournamentConfig`, `TournamentField` (+`Elimination`), `Seating` (+`Table`),
  `TournamentSession`. `from_dict(d, ai_resolver)` rebuilds (resolver passed in,
  not serialized) and **asserts chip conservation**. Tests:
  `tests/test_tournament/test_persistence.py` (8 tests — verified green during
  the session: round-trip, mid-game, human-out→play-out, deterministic
  continuation, corruption tripwire). Also P1 #3 standings tweak
  (`TournamentStandings.tsx`: archetype tag gated on `!seat.is_human`).
- **Layer B (`ff89ab88`)** — schema + repo. `schema_manager.py`:
  `SCHEMA_VERSION = 123`, `tournaments` table added to BOTH `_init_db` (~line
  1250, after `tournament_tracker`) and `_migrate_v123_create_tournaments`
  (~line 6417) + entry in the `migrations` dict (~line 2020). New
  `poker/repositories/tournament_session_repository.py`
  (`TournamentSessionRepository`: save/load/find_active_for_owner/
  find_by_game_id/set_status/set_game_id/delete). Tests:
  `tests/test_tournament/test_session_repository.py` (8 tests — verified green;
  `8 passed`). A fresh `ensure_schema()` was verified to build the table (8
  cols) + both indexes at DB version 123.
- **Layer C frontend (`1864fd5b`)** — the MTT live socket events
  (`useTournamentEvents.ts`, `mtt_*` namespace). Already done/committed.

## What's WRITTEN but UNCOMMITTED (Layer C backend — your task)

These edits are on disk (verified by reading the files). They wire durable
persistence into the live app. Re-read each region to confirm before committing.

1. **`flask_app/extensions.py`** — added `tournament_session_repo` global (near
   the other `*_repo = None` lines, ~line 78), added it to the `global`
   declaration inside `init_persistence()`, and **constructed it directly**
   (it's NOT part of `create_repos`) right after `event_repository`:
   ```python
   from poker.repositories.tournament_session_repository import (
       TournamentSessionRepository,
   )
   tournament_session_repo = TournamentSessionRepository(db_path)
   ```
   (NOTE: the real `extensions.py` builds repos via `create_repos(db_path)` in
   `init_persistence()`, NOT via simple `Repo(DB_PATH)` module-level calls.
   That's why an earlier assumed edit failed — this is the correct pattern.)

2. **`flask_app/services/tournament_registry.py`** — rewritten to be
   write-through. New helpers `_repo()` (live-reads
   `flask_app.extensions.tournament_session_repo`, returns None if unset →
   memory-only), `_rebuild_resolver()`, `_rehydrate()`. `get()` and
   `find_active_for_owner()` fall back to the repo on a memory miss and
   rehydrate into memory. New `persist_session(...)` and `persist(tid)` writes
   (status derived from `session.is_complete()`). `delete()` also removes the
   DB row. Records now carry a `'game_id'` key.

3. **`flask_app/routes/tournament_routes.py`** — `registry.persist(...)` at 4
   save points (verified count = 4): register (after `put`, with
   `'game_id': None` added to the record), sit (after setting `rec['game_id']`,
   and passes `resolver_kind=rec.get('resolver_kind','fake')` to
   `build_tournament_game`), advance, play-out.

4. **`flask_app/handlers/tournament_game_builder.py`** —
   `build_tournament_game(...)` gained a `resolver_kind: str = 'fake'` param;
   game_data gained `"tournament_resolver_kind": resolver_kind` (line ~149);
   new `_persist_boundary(game_id, game_data)` called from
   `tournament_hand_boundary` right after `_emit_tournament` (the critical save
   point — fires every live hand boundary inside `progress_game`'s lock).
   **Also added `import logging` + `logger = logging.getLogger(__name__)`** at
   the top — the module had NO logger and `_persist_boundary`'s except branch
   calls `logger.exception(...)`, so without this a persist failure would raise
   `NameError` and break the hand boundary. (This `logger` import + assignment
   IS present at the top of the file as of this handoff — verified at lines
   16-24; it's part of the uncommitted builder diff and must be staged with
   Layer C.)

5. **`tests/test_tournament/test_registry_persistence.py`** (untracked, 7
   tests) — write-through re-entry tests. Fixture monkeypatches
   `flask_app.extensions.tournament_session_repo` onto a temp-DB repo and calls
   `registry.clear()`. Covers: re-entry after eviction via find_active + get,
   cold rehydrate of resolver_kind, play→persist→re-enter parity,
   complete→status flip drops from active, game_id survives eviction, delete
   clears the row, memory-only fallback when no repo wired.

## Exactly what to do (resume steps)

Run these **one at a time** (the env was flaky with parallel/batched calls;
sequential was reliable). All tests run in Docker.

1. Confirm the backend container is up:
   ```bash
   docker compose ps backend
   # if not running:  docker compose start backend
   ```

2. Sanity: the whole import chain loads.
   ```bash
   docker compose exec -T backend python -c "import flask_app.extensions; from flask_app.services import tournament_registry; from flask_app.handlers import tournament_game_builder; from flask_app.routes import tournament_routes; print('IMPORTS_OK')"
   ```

3. Run the three persistence test files (A + B are committed but re-run to be
   safe; C is the new one):
   ```bash
   docker compose exec -T backend python -m pytest tests/test_tournament/test_persistence.py tests/test_tournament/test_session_repository.py tests/test_tournament/test_registry_persistence.py -p no:cacheprovider
   ```
   Expect ~23 passed (8 + 8 + 7). If `test_registry_persistence.py` fails on
   the monkeypatch, confirm `_repo()` in the registry reads
   `flask_app.extensions.tournament_session_repo` **live** (via
   `from flask_app.extensions import tournament_session_repo` inside the
   function, or `import flask_app.extensions as ext; ext.tournament_session_repo`)
   — not a module-top `from ... import` (which would bind None at import time).

4. Run the full tournament suite minus the known slow flake:
   ```bash
   docker compose exec -T backend python -m pytest tests/test_tournament/ -p no:cacheprovider --deselect tests/test_tournament/test_live_play_integration.py::test_human_plays_real_hands_to_a_terminal_state
   ```

5. Run the live-play integration on its own (it now fires `_persist_boundary`
   through real `progress_game` — the real end-to-end check; ~2-3 min, and it's
   a KNOWN RNG flake — passes on re-run, so retry once if it trips an
   assertion):
   ```bash
   timeout 320 docker compose exec -T backend python -m pytest "tests/test_tournament/test_live_play_integration.py::test_human_plays_real_hands_to_a_terminal_state" -p no:cacheprovider
   ```

6. If all green, commit Layer C (stage exactly these 5 files):
   ```bash
   git add flask_app/extensions.py flask_app/services/tournament_registry.py \
     flask_app/routes/tournament_routes.py \
     flask_app/handlers/tournament_game_builder.py \
     tests/test_tournament/test_registry_persistence.py
   ```
   Commit message (adjust the test counts to what you actually saw):
   ```
   feat(tournament): persistence layer C — write-through registry + save points

   Layer C makes a multi-table tournament durable + re-enterable across
   navigation, TTL eviction, and restart (P1 #1), via the registry + repo
   from layers A/B. Re-entry works through the lobby/standings/sit routes;
   in-place HTTP cold-load of an evicted live tourney- URL is deferred
   (GamePage routes those 404s to the standings hub, where Resume rebuilds
   the live table from the rehydrated session).

   - tournament_registry: repo-backed reads (get / find_active_for_owner
     fall back to the repo and rehydrate into memory); persist /
     persist_session writes; delete clears the row. Memory-only when no
     repo is wired.
   - extensions: tournament_session_repo global, constructed in
     init_persistence (directly, like event_repository).
   - tournament_routes: persist on register / sit / advance / play-out;
     sit passes resolver_kind to the builder.
   - tournament_game_builder: resolver_kind in game_data + _persist_boundary
     at every hand boundary (inside progress_game's lock); added module
     logger. Best-effort — in-memory session stays authoritative on failure.

   Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
   ```

7. Verify it actually committed (the env faked this before):
   ```bash
   git cat-file -e HEAD:tests/test_tournament/test_registry_persistence.py && echo IN_HEAD
   git status --porcelain | cat   # expect empty (or only settings.local.json)
   ```

8. Update `docs/plans/TOURNAMENT_PERSISTENCE_HANDOFF.md`: mark P1 #1 (and P1 #2,
   #3) DONE, note the layer breakdown and the one deferred slice (HTTP
   cold-load). The header note edit was started but may be uncommitted/partial —
   check it. Commit the doc separately.

## Known gotchas (all real, learned this session)

- **`extensions.py` uses `create_repos(db_path)`**, not module-level
  `Repo(DB_PATH)`. `tournament_session_repo` is built directly in
  `init_persistence()` like `event_repository` (it's not in `create_repos`).
- **Live-read the repo in the registry.** `init_persistence()` reassigns the
  module global from `None`, and the tests monkeypatch it — so `_repo()` must
  read `flask_app.extensions.tournament_session_repo` at call time, not bind it
  at import.
- **`schema_manager.py` migration shape:** `migrations: Dict[int, tuple]`
  mapping `version -> (method, description)`; migration methods take
  `conn: sqlite3.Connection`. Fresh DBs get the table from `_init_db`; existing
  DBs from `_migrate_v123_create_tournaments`. Both must exist (they do).
- **`base_repository.BaseRepository._get_connection()`** is a context manager
  that auto-commits on clean exit / rolls back on error — the repo methods rely
  on that (no explicit `conn.commit()`).
- **Live-play integration test is a pre-existing RNG flake** — it can trip
  `terminal` / `human_turns > 3` / `rounds > 0` on some seeds; passes on
  re-run. Not related to persistence.
- **Conservation invariant**: `from_dict` asserts
  `field.chip_sum() == field_size * starting_stack`. If a rehydrate raises
  `AssertionError`, the stored blob is corrupt — don't "fix" by removing the
  assert.

## Deferred (NOT required to call P1 done)

- **In-place HTTP cold-load** of an evicted live `tourney-` game URL in
  `game_routes.py` (the `tourney-` branch in the game-state load path that
  rehydrates `game_data['tournament_session']`). Not needed: GamePage already
  routes a 404 on a `tourney-` id back to `/tournament` (the standings hub),
  where Resume calls `sit` and rebuilds the live table from the rehydrated
  session. Only wire it if deep-linking straight to a cold `tourney-` URL
  becomes a real need.

## After P1: next up is P2 (economy)

Buy-ins / prize pool / payouts — tournament as a ledger counterparty
(`buy-ins == winnings + rake`) alongside the funny-money
`sum(stacks)==field×start` invariant. See the main handoff
(`TOURNAMENT_PERSISTENCE_HANDOFF.md`) "Everything else still left" section and
`MULTI_TABLE_TOURNAMENT_PLAN.md`.

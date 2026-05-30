---
purpose: Design for collapsing the single-table TournamentTracker and multi-table TournamentSession into one tournament wrapper with a unified completion path
type: design
created: 2026-05-30
last_updated: 2026-05-30
status: COMPLETE — 3A + 3B + 3C shipped & verified; TournamentTracker retired
---

# Tournament Unification — Step 3: One Wrapper, One Completion Path

## Goal

Treat **every** game as a tournament. The "old" single-table game becomes the
degenerate case of a multi-table tournament — a one-table field that never
breaks or balances. After this step there is a single tournament wrapper type, a
single durable record, a single load path, and a single completion surface.

This is the third and largest step of a three-step plan. **Steps 1 and 2 are
already shipped** (branch `tournaments`); this doc covers the deferred step 3.

## Progress (2026-05-30)

- **Step 3A — unify completion — DONE & verified.** `flask_app/handlers/tournament_completion.py`
  derives the tracker-shaped result dict from a `TournamentSession`
  (`build_completion_result`) and `finalize_tournament` is the one side-effect
  path (save result row + human career stats, idempotent, optional
  `tournament_complete` emit). Wired into the MTT boundary so multi-table games
  now record career stats AND show the same `TournamentComplete` screen (3A.2;
  `useTournamentEvents.onComplete` no longer auto-routes to the hub).
- **Step 3B — single games on a 1-table session — DONE & verified.** Every new
  game (`api_new_game`) now builds a real `TournamentSession.for_single_table`
  from its actual players (no `TournamentTracker`). A light passive boundary
  (`flask_app/handlers/single_table_tournament.py::single_table_hand_boundary`)
  folds each finished hand into the field, emits per-elimination beats, and ends
  the game at the human's terminal moment — the live state machine still owns
  play AND blinds (it self-escalates), so single-table play is unchanged.
  Sessions persist to the `tournaments` table (`resolver_kind='single'`, real
  blob) and rehydrate on cold-load. Engine gained a named-player construction
  path (`build_initial_state(entries=…)`, `TournamentSession.for_single_table`,
  `fold_live_hand`). The final-hand banner + AI spectator commentary read the
  session (wrapper-agnostic). Verified: live create (real session, no tracker,
  chips conserved), unit tests (session/boundary/completion), and an in-process
  `progress_game` run to completion writing career stats once.
- **Step 3C — retire `TournamentTracker` — DONE.** Deleted `poker/tournament_tracker.py`,
  `handle_eliminations`/`check_tournament_complete` + their call sites, the
  `save_tournament_tracker` repo method, and all live tracker branches
  (commentary, final-hand banner, per-hand save). Cold-load now builds a session
  for every non-cash game — migrating a legacy saved-tracker blob into a session
  (`single_table_tournament.session_from_legacy_tracker`) when present, else
  seeding fresh. The `tournament_tracker` DB table + `load_tournament_tracker`
  are kept read-only for that migration. Cash isolation is now structural (no
  session ⇒ no tournament completion). The 6 tracker-dependent test files were
  updated; tracker unit-tests were dropped (the logic lives in `TournamentField`
  / `build_completion_result`, covered by the tournament suite). Also stubbed the
  leaking psychology-narration LLM call in the integration tests and hardened
  them against the import-copy/xdist pollution.

## Where we are (steps 1 & 2, done)

- **Step 1 — load reconciliation + stop the leak.**
  - Cold-load re-attaches the `TournamentSession` for a multi-table table
    (`game_routes.py`, gated on a non-`single` `tournaments` row keyed by
    `game_id`), and suppresses the spurious single-table tracker that the
    generic loader used to build for MTT games. Without this, an evicted or
    restarted MTT table lost `game_data['tournament_session']` and the field
    silently froze (verified: a restarted table advanced 10 rounds / 20 hands,
    chips conserved).
  - The saved-games list (`/api/games`) now excludes `tourney-` tables (they
    resume only through the tournament lobby), matching the existing `cash-`
    exclusion.
- **Step 2 — wrap single games as tournaments (envelope).**
  - Every ordinary game now gets a lightweight `tournaments` row
    (`resolver_kind='single'`, `tournament_id='single-<game_id>'`) — written on
    create, lazy-written on cold-load, deleted with the game. See
    `flask_app/services/tournament_registry.py::persist_single_envelope`.
  - These envelopes are **identity/index records only**. They are NOT rehydrated
    into a `TournamentSession` and NOT attached to `game_data`, so the
    single-table game still runs on its `TournamentTracker` and the legacy
    `TournamentResult` completion screen. MTT-only queries
    (`find_active_for_owner`) filter `resolver_kind='single'` out so the lobby is
    never confused.

The envelope is deliberate groundwork for this step: the rows already exist;
step 3 upgrades what they *contain* and *drive*.

## The two wrappers today

| | Single-table game | Multi-table tournament |
|---|---|---|
| Wrapper type | `poker/tournament_tracker.py::TournamentTracker` | `tournament/session.py::TournamentSession` |
| Stored in | `game_data['tournament_tracker']`, persisted in the `games` blob (`save_tournament_tracker`) | `tournaments` table (`TournamentSessionRepository`) |
| Eliminations | tracker `EliminationEvent`s for one table | `field` eliminations across N tables |
| Completion gate | `game_handler.handle_eliminations` / `check_tournament_complete` key off `'tournament_tracker' in game_data` | `game_handler:3490` dispatches on `game_data.get('tournament_session') is not None` → `tournament_hand_boundary` |
| Completion event | `tournament_complete` → `TournamentResult` / `TournamentComplete` screen | `mtt_complete` + standings (separate, **incompatible** payload — see the comment in `tournament_game_builder._emit_tournament`) |

The handler dispatch is the crux: **attaching a session to a game flips its
completion onto the MTT path.** That is why step 2 could not put a real session
on single games without dragging completion-unification along — it belongs here.

## Proposed end state

1. **`TournamentSession` is the only wrapper.** A single-table game is
   `TournamentConfig(field_size = table_size = N)` with one `Table` that never
   triggers a break/balance. `TournamentTracker` is retired; its
   responsibilities (per-table eliminations, finishing position, hand count) are
   already a strict subset of the field/seating/elimination model in
   `TournamentSession` + `tournament/field.py`.

2. **One durable record.** The `tournaments` row holds a full serialized
   `TournamentSession` for every game. `resolver_kind='single'` keeps its
   meaning (a one-table tournament, no AI-table resolver needed) but the
   `session_json` becomes a real session instead of the `{single: true}`
   placeholder. `save_tournament_tracker` / `load_tournament_tracker` and the
   `games`-blob tracker field are removed.

3. **One load path.** Cold-load always: look up the `tournaments` row by
   `game_id` → rehydrate the session → attach it. The step-1 branch that chooses
   "tracker vs session" collapses to "always session." `find_active_for_owner`
   no longer needs the `!= 'single'` filter for *type* reasons (every row is a
   real session); it may still filter `single` for *lobby* reasons (the
   multi-table lobby only resumes multi-table events) — decide explicitly.

4. **One completion surface.** Resolve the `tournament_complete` /
   `TournamentResult` vs `mtt_complete` / standings split. Options:
   - **(a) Converge on standings.** A one-table tournament completes through the
     same standings/`mtt_complete` path; the "you won / final results" screen
     becomes the 1-table rendering of the standings hub (heads-up final table =
     the only table). Simplest model; biggest UX change to the single-game end
     screen.
   - **(b) Keep `TournamentResult` as the final-table view.** Both single- and
     multi-table tournaments end on the same `TournamentResult`-style screen,
     fed by a unified completion payload derived from the session standings.
     Preserves the current single-game end UX; requires building one payload
     shape both screens accept.

   **Recommendation:** (b) — unify the *payload* (session standings → one
   completion shape) and let the final-table screen render it, rather than
   forcing the single-game end UX onto the MTT standings hub.

## Migration

No real user data exists yet (pre-release), so this can be a clean cutover
rather than a careful backfill:

- New games create a 1-table `TournamentSession` directly (replace the
  `TournamentTracker` construction in `api_new_game`).
- Legacy single games: lazy-upgrade on load — synthesize a 1-table session from
  the current `games` state (and the old tracker blob if present) the first time
  the game is opened, replacing the `{single: true}` envelope's `session_json`.
- Dev DB: optional one-shot script to upgrade existing `single-*` envelopes; or
  just let them lazy-upgrade.

## Risks / watch-items

- **Completion regressions.** The single-game end screen, career-stats
  recording (`tournament_results` / `tournament_standings` tables, KO counts,
  nemesis), and the `tournament_complete` socket consumers in `usePokerGame` all
  read the tracker-era shape. Each needs to be re-pointed at the unified payload.
- **Heads-up / short-handed final table.** A 1-table session must drive blinds,
  button, and completion correctly with no other tables to balance against — the
  field/seating code currently assumes a multi-table context in places.
- **Performance.** Every quick-play game would spin up the field engine. Confirm
  the 1-table path is cheap (no AI-table resolution work when `field_size ==
  table_size`).
- **Determinism / seeds.** `build_tournament_game` derives the deck seed from
  `session.config.seed * 100_003 + session.rounds`; single games currently use
  their own seed path. Unify without breaking hand-replay seeds.

## Acceptance

- One wrapper type in the codebase (`TournamentTracker` deleted or reduced to a
  thin shim).
- A single game and a multi-table tournament load through the exact same code
  path and both end on the same completion surface.
- Career stats, KO/nemesis tracking, and hand replay still work for single games.
- Full tournament + flask suites green; sim-validated chip conservation.

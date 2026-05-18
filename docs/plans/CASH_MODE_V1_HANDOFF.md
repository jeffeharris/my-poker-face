---
purpose: Implementation handoff for Track B step 3 (Cash mode v1 — single table, persistent bankroll, AI bankrolls + regen, integrated with the Phase 3 relationship layer)
type: guide
created: 2026-05-18
last_updated: 2026-05-18
---

# Cash Mode v1 — Handoff

This doc gets a fresh context up to speed on what's done, what's
next, and the smallest set of files to read before touching code.
Treat the canonical design doc (`CASH_MODE_AND_RELATIONSHIPS.md`
Part 2) as the source of truth for *what* should land; this doc is
the *how*, anchored to the current state of the codebase.

## Status at handoff

**Phase 3 (relationship layer): complete and wired live.** The
relationship event vocabulary fires from gameplay in both
production paths (Flask game handler + experiment runner). Track B
step 3 unblocks here.

| Phase | Status |
|---|---|
| Track B step 1 — Personality ID migration | ✅ shipped on `phase-1` |
| Track B step 2 — Relationship layer (Phase 1 + 2 + 3) | ✅ shipped on `phase-1`; 320 memory tests green |
| Track B step 3 — Cash mode v1 | ⏳ this handoff |

What's in place that v1 cash mode builds on:

- **Schema v87** (`poker/repositories/schema_manager.py`):
  `relationship_states` and `cash_pair_stats` tables exist. No
  bankroll tables yet — v1 adds them as schema v88+.
- **`OpponentModelManager.record_event`**
  (`poker/memory/opponent_model.py`): single legal mutation entry
  point for relationship axes. ID-keyed (personality_id).
- **`HandOutcomeDetector`**
  (`poker/memory/hand_outcome_detector.py`): emits `BIG_WIN`,
  `BIG_LOSS`, `HERO_CALL`, `BLUFFED_OFF`, `BAD_BEAT` from
  completed hands. Wired into `AIMemoryManager.on_hand_complete`.
- **`AIMemoryManager.set_relationship_repo(repo, cash_mode=...)`**
  (`poker/memory/memory_manager.py`): the activation point. **v1
  cash games will pass `cash_mode=True`** to enable
  `cash_pair_stats` writes alongside relationship state.
- **`RelationshipRepository.apply_cash_pair_pnl`**
  (`poker/repositories/relationship_repository.py`): bilateral
  cumulative_pnl + hands_played_cash writes. Already used by the
  dispatch path when cash_mode is true.
- **Personality ID resolution**: `personality_repo.resolve_name_to_personality_id`
  is in production code paths. The Flask game handler and
  experiment runner both register IDs at session start.

## What v1 must do

From `CASH_MODE_AND_RELATIONSHIPS.md` Part 2 §"v1 scope":

1. **Single cash table** with selectable stake + buy-in. No
   concurrent tables (lobby is v2).
2. **Persistent player bankroll** with fresh-grant on full bust.
   Lives in a new persistence row keyed on `player_id`.
3. **Persistent per-personality AI bankrolls** with projection-on-
   read regen (same pattern as `project_heat`). Lives in a new
   persistence row keyed on `personality_id`. Knobs come from
   `personalities.json` → `PersonalityRecord`.
4. **Sit/leave/top-up between hands** with the exact accounting
   order in Part 2 §"Bankroll accounting order". Order matters —
   the doc enumerates 8 specific events and what moves where.
5. **Mid-hand quit → forfeit table stack** to the pot, split among
   remaining players.
6. **60-second disconnect grace window** (auto-check/fold during
   the window; reconnect resumes; expiry treats as quit).
7. **Hard bust** semantics for player (fresh grant, no cooldown)
   and AI (real-time gated on regen reaching min buy-in).
8. **AI session behavior: bust-only**. No stop-loss / stop-win in
   v1 (deferred to v2).
9. **Relationship layer integration**:
   - `AIMemoryManager.set_relationship_repo(repo, cash_mode=True)`
     at session setup.
   - `cumulative_pnl` updates flow through the existing dispatch
     path (already wired — just enable cash_mode).
   - Rivalry-seek seating **deferred to v2** because v1 has no
     lobby. The `project_heat` reader is in place when v2 needs
     it.

## Files to read first (smallest set for context)

In rough order of importance:

1. `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` **Part 2** — the
   canonical spec. Pay attention to:
   - §"v1 scope" — what's in vs out
   - §"Data model" — dataclass shapes
   - §"Bankroll accounting order" — the 8-event table; this is
     the most bug-prone surface in v1
   - §"Bankroll regen (pure projection on read)" — mirrors
     `project_heat`
   - §"v1 architectural invariants" — the constraints that make
     v1 a foundation, not a dead-end

2. `poker/memory/memory_manager.py` lines 99–162 — the Phase 3
   `_process_relationship_events` + `set_relationship_repo`
   surface. Cash mode v1 calls this with `cash_mode=True`.

3. `poker/repositories/relationship_repository.py` —
   `apply_cash_pair_pnl` (bilateral cumulative_pnl writes). This
   is what `cash_pair_stats` writes go through. **Already
   wired**; v1 just enables it.

4. `poker/repositories/schema_manager.py` lines 360–390 — current
   `relationship_states` and `cash_pair_stats` schemas, plus the
   schema-version pattern v1 follows for adding bankroll tables.

5. `flask_app/handlers/game_handler.py:handle_evaluating_hand_phase`
   (around line 1080–1140) — where `on_hand_complete` runs in the
   Flask path, including the existing `equity_history` → BAD_BEAT
   wiring. v1's cash-table handler will share this pattern but
   bracket it with sit/leave/topup state transitions.

6. `experiments/run_ai_tournament.py:_setup_game` — pattern for
   how `set_relationship_repo` gets wired in the experiment
   runner. v1 ships a cash-mode equivalent for autonomous play.

7. `docs/plans/RELATIONSHIP_PHASE_3_HANDOFF.md` — the predecessor
   handoff. Useful for the operating-notes pattern this doc
   inherits.

## Suggested commit breakdown (~6–8 commits)

**Commit 1: Schema v88 — bankroll tables + personality bankroll knobs**
- Add `ai_bankroll_state` (personality_id PK, chips, last_regen_tick)
  and `player_bankroll_state` (player_id PK, chips,
  starting_bankroll).
- Add `bankroll_cap`, `bankroll_rate`, `buy_in_multiplier`,
  `stop_loss_buy_ins`, `stop_win_buy_ins`, `stake_comfort_zone`
  columns to the `personalities` table.
- Re-seed migration for `personalities.json` to populate the new
  knob columns. Default values per Part 2 §"Bankroll knob storage".
- Repository: `BankrollRepository` with `load_ai_bankroll`,
  `save_ai_bankroll`, `load_player_bankroll`, `save_player_bankroll`,
  plus a `project_bankroll` pure function (mirrors `project_heat`).
- Tests: schema round-trip, projection unit tests, missing-row
  defaults.

**Commit 2: `cash_mode/` package skeleton + `CashTable` state**
- New package `cash_mode/` with `__init__.py`.
- `cash_mode/table.py` — `CashTable` dataclass per Part 2 data
  model. In-memory only (table itself isn't persisted in v1; just
  the bankrolls + relationship state).
- `cash_mode/seating.py` — sit/leave/topup state transitions.
  Block when `hand_in_progress=True`. **Strictly follow the
  accounting order table** — this is the load-bearing correctness
  surface; tests should exercise every row of the 8-event table.
- Tests: every accounting event with both happy-path and rollback
  scenarios.

**Commit 3: Hand orchestration — single-table loop**
- `cash_mode/session.py` — runs hands sequentially at one table.
  Calls into the existing hand engine. Memory manager is set up
  with `cash_mode=True`.
- Bust handling per Part 2 §"Bust semantics": AI ineligible until
  `project_bankroll` clears `min_buy_in × buy_in_multiplier`;
  player gets fresh grant.
- Tests: 10-hand simulated session, verify chip conservation
  across sit/leave/bust/topup events, verify `cash_pair_stats`
  updates fire.

**Commit 4: Mid-hand quit + disconnect grace**
- 60-second auto-check/fold window on disconnect.
- Mid-hand quit forfeits table stack to the pot.
- Tests: explicit-quit and timeout paths both produce the same
  side-pot allocation as a normal fold-out from that street.
  This is the trickiest correctness surface after the accounting
  order table.

**Commit 5: Flask routes — cash mode entry + table page**
- New blueprint `flask_app/routes/cash_routes.py` or extend
  `game_routes.py` — pattern decision based on how cleanly
  cash-mode game state separates from existing per-game state.
- `/api/cash/start` (pick stake, sit), `/api/cash/leave`,
  `/api/cash/topup`, `/api/cash/state`.
- Socket.IO events: hand progression reuses existing
  `update_and_emit_game_state` if possible.
- Tests: route smoke tests with patched auth, similar pattern to
  `tests/test_experiment_routes.py`.

**Commit 6: React UI for cash mode (entry screen + table)**
- Cash-mode home: bankroll display, "Pick a stake" with the
  stakes-ladder dropdown, "Sit at table" button.
- Table page: reuses the existing poker table view if possible.
  Topup / leave buttons between hands.
- Probably the largest commit. Could split into entry/table/lobby
  sub-commits if scope gets unwieldy.

**Commit 7: Integration sanity script + smoke test**
- `scripts/cash_mode_sanity_check.py` analogous to
  `scripts/phase3_sanity_check.py`. Runs a multi-hand session
  with synthetic AI bots, asserts:
  - Bankrolls debit/credit correctly across sit/leave/topup
  - Bust → fresh grant path
  - `cash_pair_stats` cumulative PnL matches end-of-session chip
    delta
  - `relationship_states` populates from real cash play

**Commit 8: Docs sweep + retire deferred-trigger language in detector docstrings**
- Update `docs/vision/NEXT_PHASE_VISION.md` Bucket 5 status
  flags.
- Update the "v2 unblocks" notes in
  `CASH_MODE_AND_RELATIONSHIPS.md` Part 2.
- Any docstrings that still reference "cash mode v1 not yet
  shipped" or similar.

Stop after commit 8 unless the user asks for more. v2 (lobby,
rivalry-seek, stop-loss/stop-win) is its own handoff.

## Phase 3 inheritance — what to watch for

Phase 3 surfaced some patterns + gotchas worth carrying forward:

- **Stable IDs, never display names.** Cash mode persistence
  (bankrolls, `cash_pair_stats`) must key on `personality_id` /
  `player_id`. Display names collide and rename — IDs don't. The
  Phase 3 `OpponentModelManager._name_to_id` registry pattern is
  the prior art.

- **Projection on read for time-based state.** `project_heat` is
  the existing template for `project_bankroll`. Stored
  `last_regen_tick` updates only on real events (sit-down,
  win/loss); reads project from elapsed time.

- **Bilateral writes in one transaction.**
  `RelationshipRepository.apply_cash_pair_pnl` already does this
  for `cash_pair_stats`. Don't bypass it with two separate
  `save_*` calls.

- **The folded-card strip subtlety.** Phase 3 had to fix an
  experiment-runner strip that broke `BLUFFED_OFF` detection
  (commit `2e69f862`). Cash mode reuses the same hand pipeline,
  so the same bracketed strip/restore is already in place — but
  if cash mode adds its own pre-`on_hand_complete` hook, watch
  out for re-introducing the bug.

- **`hands_played_cash` semantics**: today it increments per
  BIG_WIN dispatch (i.e., per qualifying chip-flow pair) via
  `dispatch_events`. Per the design doc spec, it should arguably
  increment **per cash-mode hand per pair at the table**
  regardless of whether the hand triggered a BIG_WIN. v1 is the
  natural moment to revisit this — the dispatcher could grow a
  parameter, or cash mode could call into a dedicated per-hand
  pair-incrementer alongside the event dispatch. Flag in the
  Codex/PR review.

- **Equity computation is per-hand and already paid for.** Both
  production paths (Flask + experiments) compute equity history
  for every hand. Cash mode v1 should pass it into
  `on_hand_complete` the same way (already wired) — BAD_BEAT
  will fire in cash games for free.

## Test patterns to follow

- `pytestmark = pytest.mark.integration` at module level when
  tests need a DB (matches Phase 3 convention).
- `SchemaManager(path).ensure_schema()` in a `tmp_path`-based
  fixture for fresh DBs per test.
- `tests/test_memory/test_dispatch_events.py` is the closest
  prior art for cash_pair_stats round-trip tests.
- `tests/test_memory/test_relationship_integration.py` is the
  closest prior art for end-to-end pipeline tests.
- For UI work: Playwright via the MCP server, smoke-test the
  entry screen + a 5-hand cash session.

## Operating notes

- **Branch: `phase-1`** unless an explicit cash-mode branch is
  cut. Both Track A and Track B have been pushing here; conflicts
  remain clean because the file sets are mostly disjoint. Pull
  before starting.

- **No new feature flags for cash mode itself.** v1 ships
  alongside SNG mode in production from day one (zero production
  users today). The `apply_relationship_modifier` flag remains
  the relationship-layer containment lever; cash mode shouldn't
  add another gate.

- **Codex assist for spec compliance.** The cash accounting order
  table is the kind of surface where mid-implementation
  `/codex-assist ask` checks earn their keep — catches missed
  rows before tests are written.

- **Schema migrations are forward-only.** v87 added the
  relationship tables; v88+ adds bankroll. Once a migration ships
  to production it stays.

- **Existing user games don't migrate to cash mode.** Cash mode
  is a new entry path; the SNG/tournament flow stays exactly as
  it is. No backfill plan needed.

## Done criteria

Cash mode v1 is complete when:

1. A player can pick a stake from the home screen, sit at a cash
   table with a chosen buy-in, play hands against AI personalities,
   leave between hands, top up, and bust.
2. Player and AI bankrolls persist across game restarts.
3. AI bankroll regen via `project_bankroll` matches the Part 2
   formula in tests.
4. The 8-event accounting order table passes every row.
5. Mid-hand quit / disconnect grace produces correct side-pot
   allocations.
6. `cash_pair_stats.cumulative_pnl` matches end-of-session chip
   delta in the sanity script.
7. `relationship_states` continues to populate from cash play
   (same events as SNG mode, no regression).
8. New unit + integration tests pass; no Phase 3 regressions.

Once done, v2 (multi-table lobby, AI table selection priorities,
rivalry-seek seating, stop-loss/stop-win) is unblocked. The
relationship layer's cash-mode payoff (rivalry-seek) lands in v2.

## References

- Canonical spec: `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 2
- Predecessor handoff: `docs/plans/RELATIONSHIP_PHASE_3_HANDOFF.md`
- Roadmap: `docs/vision/NEXT_PHASE_VISION.md` Bucket 5
- Phase 3 commits worth scanning before starting:
  - `5debbda7` — HandOutcomeDetector skeleton
  - `97dbf22b` — dispatch_events + cash_pair_stats wiring
  - `43bd1718` — Phase 3 integration into AIMemoryManager
  - `3e39f032` — production-path wiring of `set_relationship_repo`
  - `2e69f862` — folded-card strip fix (don't reintroduce)
  - `10752e3f` — BAD_BEAT detection (equity-history wiring pattern)
  - `d472591d` — Flask equity → `on_hand_complete` forward

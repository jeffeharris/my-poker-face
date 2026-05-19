---
purpose: Implementation handoff for Relationship Phase 3 (HandOutcomeDetector + live event population from gameplay)
type: guide
created: 2026-05-18
last_updated: 2026-05-18
---

# Relationship Phase 3 — Handoff

This doc gets a fresh context up to speed on what's done, what's
next, and the smallest set of files to read before touching code.
Treat the canonical design doc (`CASH_MODE_AND_RELATIONSHIPS.md`
Part 1) as the source of truth for *what* should land; this doc
is the *how*, anchored to the current state of the codebase.

## Status at handoff

**Track B step 2 (Relationship Phases 1–2): complete.** Phase 3 is the
last piece before Track B step 3 (Cash mode v1) can begin.

| Phase | Commits | Tests | Status |
|---|---|---|---|
| Phase 1 — data layer | 6 | 142 new | ✅ shipped |
| Phase 2 — controller seam | 1 | 18 new | ✅ shipped |
| Phase 3 — live population | 0 | — | ⏳ this handoff |

The relationship-axis read path is already wired through
`_apply_exploitation` behind the `apply_relationship_modifier=True`
controller flag. **What it currently reads, no real gameplay writes
to.** Phase 3 closes that loop: hands resolve → `HandOutcomeDetector`
maps the outcomes to `RelationshipEvent` values → `record_event()`
mutates state → next decision's modifier reflects the new state.

## What Phase 3 must do

From `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 1 §"Input
sources" and §"Cash pair stats":

1. **`HandOutcomeDetector` class** that consumes a completed hand
   and emits a sequence of `(actor_id, target_id, RelationshipEvent,
   impact_score, narrative, hand_summary)` tuples. The detector
   reads the hand record + existing pressure/equity signals; it does
   not invent new ones.

2. **Event mappings** for at least these (start small, expand later):
   - `BIG_WIN` / `BIG_LOSS` — actor won/lost a pot above the
     existing big-pot threshold (`MomentAnalyzer.BIG_POT_RATIO`
     already in the codebase). Emit one per (winner, loser) pair
     using the multiway chip-flow allocation below.
   - `BAD_BEAT` — actor was favorite at the final betting round
     (use `hand_equity` rows) and lost.
   - `BLUFFED_OFF` — actor folded postflop to an opponent whose
     showdown equity would have been weaker (requires the opponent
     to have been forced to fold or the bot to have showdown
     visibility; otherwise skip).
   - `HERO_CALL` — actor called a bet and the opponent's revealed
     hand at showdown was weaker.
   - Other events (`DOMINATED_SHOWDOWN`, `STRONG_FOLD_SHOWN`) can
     follow in later commits — don't gold-plate.

3. **Multiway chip-flow allocation** for `BIG_WIN` / `BIG_LOSS`:
   per the design doc, "for each (winner, loser) pair the winner's
   net gain is split proportionally to each loser's chip
   contribution to the pots the winner collected. Side pots resolve
   independently — each side pot has its own (winner, loser) PnL
   pairs."

   The same allocation feeds `cumulative_pnl` and
   `hands_played_cash` in `cash_pair_stats` (so the detector should
   call into both the relationship layer and the cash pair stats
   repo in cash-mode hands).

4. **Dedup** keyed on `(hand_id, actor_id, target_id, event)` so a
   second pass through the same hand record doesn't double-emit.
   The mechanism is the caller's choice — easiest is a `set()` on
   the detector instance keyed by the tuple, populated when each
   event emits, checked before emitting.

5. **Integration into hand resolution.** The current hand-completion
   path is `MemoryManager.complete_hand` in
   `poker/memory/memory_manager.py` (search for `was_showdown` /
   `observe_showdown` for the existing showdown handling). The
   detector hooks here, alongside the existing
   `commentary_generator.extract_notable_events` call. Do *not* run
   the detector inside the decision-time path — it's a post-hand
   thing.

## Files to read first (smallest set for context)

In rough order of importance:

1. `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 1 §"Input
   sources" (lines 341–399 or so), §"Symmetry: bilateral updates"
   (lines 148–156), and §"Cash pair stats" (lines 80–93). The
   canonical spec.

2. `poker/memory/relationship_events.py` — the enum, dispatch
   tables, and `RelationshipEvent.from_string` for the quarantine
   path. Don't redefine event types; use what's there.

3. `poker/memory/opponent_model.py` — focus on:
   - `RelationshipState` and `CashPairStats` dataclasses (around
     line 850–920)
   - `OpponentModelManager.record_event()` (toward the end of the
     manager class). **This is the only legal mutation entry point;
     `HandOutcomeDetector` must emit through it.**
   - `MemorableHand` and `add_memorable_hand` for context on the
     in-memory sidecar that record_event already populates.

4. `poker/memory/memory_manager.py` lines around 391–430 — the
   existing `complete_hand` flow that handles showdown,
   `observe_showdown`, and event extraction. The detector wires
   alongside this code.

5. `poker/memory/hand_history.py` — `RecordedHand`, `RecordedAction`
   data shapes. The detector reads from these.

6. `poker/memory/pressure_detector.py` — existing pressure-event
   signals (`is_big_pot`, etc.). The detector reuses these
   thresholds; do not reinvent.

7. `poker/repositories/relationship_repository.py` — to understand
   the `cash_pair_stats` read/write surface.

8. `tests/test_memory/test_record_event.py` — test patterns for
   anything that calls into `OpponentModelManager.record_event`.
   Follow this style.

9. `tests/test_repositories/test_relationship_repository.py` — for
   repo round-trip patterns.

## Suggested commit breakdown (3–4 commits)

**Commit 1: `HandOutcomeDetector` skeleton + simple events**
- New file: `poker/memory/hand_outcome_detector.py`
- Define the class with one public method:
  `detect_events(recorded_hand: RecordedHand) -> List[DetectedEvent]`
  where `DetectedEvent` is a dataclass carrying the tuple shape.
- Implement `BIG_WIN` / `BIG_LOSS` detection (existing big-pot
  threshold), without multiway allocation yet — just emit on the
  single (winner, loser) pair for now.
- Tests: a few hand records that trigger / don't trigger.

**Commit 2: Multiway chip-flow allocation**
- Add a helper that takes a list of pots (each with winners +
  contributions) and produces a list of (winner_id, loser_id,
  chips_won) tuples. Side pots are independent.
- Wire into `HandOutcomeDetector` so `BIG_WIN` / `BIG_LOSS` events
  use the allocation (one event pair per chip-flow tuple).
- Tests: heads-up case, 3-way main pot only, 3-way with one side
  pot, all-in collision.

**Commit 3: cash_pair_stats updates + emission**
- Add a `dispatch_events(events, manager, cash_pair_repo, hand_id,
  now)` function (or method) that calls `manager.record_event` for
  each event and, when in cash mode, also updates `cash_pair_stats`
  via the repo (cumulative_pnl from the chip-flow allocation,
  hands_played_cash + 1). Bilateral cash_pair_stats writes happen
  in one transaction.
- Dedup: `set` keyed on `(hand_id, actor_id, target_id, event)`.
- Tests: emission round-trip through repo, dedup blocks double-
  emit, cash_pair_stats writes both sides correctly.

**Commit 4: Integration into `MemoryManager.complete_hand`**
- Add the detector + dispatch call into the hand-completion path,
  guarded by config / mode so non-cash games still update
  relationship state but skip cash_pair_stats.
- The cash-mode determination probably keys on existing game-mode
  state; if not, leave a TODO and ship the cash-stats path behind
  an explicit boolean flag for now.
- Tests: a complete-hand fixture that walks an end-to-end pass,
  verifying relationship state changes and (in cash mode)
  cumulative_pnl changes.

Stop after commit 4 unless the user explicitly asks for more.
Don't try to implement every event taxonomy entry in this pass —
ship the load-bearing ones (`BIG_WIN`, `BIG_LOSS`, plus one or two
showdown-revealed events) and leave the rest as TODOs with named
entry points.

## Test patterns to follow

From the Phase 1/2 work:

- `pytestmark = pytest.mark.integration` at module level when tests
  need a DB. The fixture pattern is in
  `tests/test_repositories/test_relationship_repository.py`.
- Use `SchemaManager(path).ensure_schema()` in a `tmp_path`-based
  fixture for fresh DBs per test.
- Mock or stub hand records where the detector reads from them;
  use real `RecordedHand` instances for the integration test in
  commit 4.
- Run tests via `docker compose exec backend python -m pytest <path>`
  for the inner loop. The `--quick` runner via `scripts/test.py`
  works too.
- Inner-loop test scope for Phase 3 work: `tests/test_memory/` and
  selectively `tests/test_repositories/test_relationship_repository.py`.

## Operating notes

- **Branch: `phase-1`.** Track A and Track B both push here. Pull
  before starting; another commit from Track A may have landed.
  Conflicts have been clean so far — both tracks mostly touch
  disjoint files. `poker/memory/memory_manager.py` is the one file
  where Track A has been adding equity-tracking instrumentation and
  Track B is about to add detector wiring; if both touch the same
  lines, the merge is straightforward (additive changes inside
  `complete_hand`). Check `git status` for uncommitted Track A work
  in that file before starting Commit 4.

- **Cadence: trigger-based.** Sync at gates only; otherwise ship.
  Commit 4's `MemoryManager` integration is potentially the
  biggest scope-discovery surface — if it grows beyond `complete_hand`
  changes (e.g., needs new mode-detection plumbing), surface that
  before going further.

- **Schema: at v87, no new tables needed for Phase 3.** Dedup is
  in-memory; relationship_states + cash_pair_stats already exist.

- **No new feature flags** — the `apply_relationship_modifier` flag
  on the controller handles the seam backout case. Phase 3 just
  populates state; if it misbehaves, turning the flag off is the
  containment lever (state still accumulates harmlessly, just isn't
  read by the controller).

- **Don't touch the controller's `_apply_exploitation` path.** Phase
  3 is purely on the write side. Phase 2 already wired the read
  side and nothing in Phase 3 needs to change that.

## Done criteria

Phase 3 is complete when:

1. `HandOutcomeDetector` lives in `poker/memory/hand_outcome_detector.py`
   and detects at minimum `BIG_WIN`, `BIG_LOSS`, plus one showdown-
   revealed event (`HERO_CALL` is the easiest; `BAD_BEAT` is
   reasonable if hand_equity is available).
2. Multiway chip-flow allocation matches the design doc rule
   (per-side-pot independent allocation).
3. Cash-mode hands write `cumulative_pnl` and `hands_played_cash`
   updates to `cash_pair_stats` via the same allocation.
4. Dedup prevents double-emission within a single hand resolution.
5. Integration in `MemoryManager.complete_hand` fires the detector
   exactly once per completed hand.
6. Tests cover all four commits and pass.

Once done, Track B step 3 (Cash mode v1) is unblocked. Sample the
existing relationship state in a sim (run a TieredBot tournament
with a small N, query `relationship_states` after) as a sanity
check before declaring done — Phase 1/2 only had unit tests; Phase
3 is the first time the system actually populates from real play.

## References

- Canonical spec: `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 1
- Roadmap: `docs/vision/NEXT_PHASE_VISION.md` Bucket 4
- Phase 1 + 2 commits (read these for context on what shipped):
  - `bc732f6e` — RelationshipEvent enum + dispatch tables
  - `34ebe0dd` — MemorableHand rename
  - `35cf567a` — RelationshipState + project_heat
  - `2c8749c9` — Schema v87 + RelationshipRepository
  - `7050a22b` — OpponentModelManager.record_event
  - `234528de` — get_relationship_modifier
  - `7f07fe8f` — Phase 2 controller seam

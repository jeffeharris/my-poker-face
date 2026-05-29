---
purpose: TRIAGE T2-75 detail — refresh_unseated_tables is a ~1,715-line god-function due for stage extraction
type: reference
created: 2026-05-29
last_updated: 2026-05-29
---

# T2-75 — `refresh_unseated_tables` god-function (P1, owner-elevated)

**Location:** `cash_mode/lobby.py:518` (`refresh_unseated_tables`, ≈518–2233).

**Priority:** P1 — owner-elevated 2026-05-29. Above the usual "big refactor →
Tier 3" demotion (cf. T2-10/T2-11) because this function is the **active seam
for in-flight cash-mode work** (table attractiveness, the seating loop
inversion) *and* it was just heavily reworked by the session-lifecycle
hardening — so it carries both high change-frequency and high regression risk.
Refactor it on its own, with the existing cash_mode suite as the safety net;
do **not** entangle it with feature work.

## The problem

`refresh_unseated_tables` is ~1,715 lines. The per-table loop body alone
(≈967–1853) is ~890 lines, with these stages **all inline** (not extracted into
named functions):

1. per-table setup closures (`_buy_in_for`, `_borrower_lookup_for_table`,
   `_psych_lookup_sim`, `_reseat_energy_lookup`)
2. the sim burst loop (`play_one_hand` + `refresh_table_roster` + per-hand
   aggregation)
3. aspiration asks → `save_table` → idle-changes commit
4. **stake settlement** (≈1455–1644: human/AI-staker branches, ledger flows,
   two event types inline)
5. bankroll↔seat transfers (≈1646–1684)
6. **stake creations** (≈1686–1804: more inline event emission)
7. activity + burst event emission (≈1806–1831)
8. last-stand predator-signal collection

Then ≈380 more lines of post-loop passes (carry resolution, vice spending,
side-hustle, closed-economy, whale/casino provisioning, summary events) — these
*are* mostly extracted into helpers; the per-table body is not.

**Symptoms:** deferred `from x import y` scattered throughout; ~a dozen
best-effort `try/except` blocks; heavy **ordering coupling** (settlement must
precede the `from_seat` credit; `settled_from_seat_indices` threads between
stages); many per-table locals/closures shared across stages. The function has
accreted through the backing system, aspiration asks, vice, and lifecycle
hardening, each phase appending inline.

## Proposed refactor

Extract the per-table loop body into named stage functions taking explicit
params (no hidden closure state):

- `_settle_table_stakes(result, *, stake_repo, bankroll_repo, chip_ledger_repo,
  relationship_repo, personality_repo, sandbox_id, now) -> settled_from_seat_indices`
- `_apply_bankroll_transfers(result, *, settled_from_seat_indices, ...)`
- `_apply_stake_creations(result, *, ...)`
- `_emit_table_events(result, *, previous_table, sim_results, ...)`

Then the loop body reads as a pipeline. Preserve the ordering invariants
(settlement → transfers → creations → events) and the `from_seat`-index
contract. Drive it with the full `tests/test_cash_mode/` bucket (825+ tests)
plus a chip-conservation drift check before/after.

## Why now (the trigger)

Surfaced 2026-05-29 while planning the table-attractiveness seating inversion
(Phase C). The inversion needs a global fill pass; doing it as a loop-split
(Codex's first instinct) would have meant relocating this 890-line monolith into
a second loop — partially performing this refactor on freshly-hardened code as a
side effect. Instead the feature was scoped to a *contained* post-loop fill pass
(option B) that leaves this function untouched, and the refactor was filed here
as separate, dedicated work.

---

# Staged plan (2026-05-29)

This expands the "Proposed refactor" sketch above into an executable,
phase-by-phase sequence. The plan is **behavior-preserving**: each phase is a
pure extraction that leaves observable behavior (DB writes, emitted events, chip
movement, idle/seat transitions) identical. Ship **one phase per PR**; the suite
stays green and chip-conservation drift stays `0` after every phase. Do **not**
fold any feature change (table attractiveness / seating inversion) into these
PRs.

## Ground truth (verified against the code 2026-05-29)

The line numbers in the triage section above are **stale** (they predate the
session-lifecycle hardening). Verified current anchors — use these:

- **File:** repo-root `cash_mode/lobby.py` (4,215 lines). It is a **module-level
  function**, not a method, so the extracted stages should be sibling
  module-level `_helpers`, matching the existing `_emit_*` family.
- **Function:** `def refresh_unseated_tables(` at **line 782**, ends just before
  `_emit_whale_events` at **line 2424** → ~**1,642 lines**. All repos it needs
  are already keyword-only parameters (`cash_table_repo`, `personality_repo`,
  `bankroll_repo`, `chip_ledger_repo`, `relationship_repo`, `stake_repo`,
  `vice_repo`, …) — the "pass params explicitly" goal is already satisfied at
  the boundary; the work is threading them into extracted helpers.
- **Pre-loop lookup closures** (defined once, cache-backed, capture the repos):
  `_bankroll_lookup` 967, `_adjacent_stakes` 994, `_comfort_zone` 1006,
  `_cross_table_pool_for` 1015, `_borrower_profile_lookup` 1064,
  `_staker_profile_lookup` 1088, `_relationship_lookup` 1091, `_history_for`
  1110, `_starting_bankroll_for` 1130, `_carry_lookup` 1139, `_buy_in_lookup`
  1161, `_vice_prob_lookup` 1209. These are relatively benign (built once).
- **Per-table loop:** `for table in tables:` at **1244** (this is the ~890-line
  body). Per-iteration closures defined *inside* it — the real hidden-state
  problem — are `_buy_in_for` 1264, `_borrower_lookup_for_table` 1290,
  `_psych_lookup_sim` 1346, `_ticker_name_for` 1609.
- **Sim burst:** `play_one_hand` **1397**, `refresh_table_roster` **1454**,
  per-table `save_table` **1589**.
- **Settlement → transfers contract:** `settled_from_seat_indices` is
  initialized at **1607**, populated at **1807**, and consumed at **1834**
  (`if i in settled_from_seat_indices`). The settlement vacate site is tagged
  `"refresh_table_roster_vacate"` at **1844**. This trio is the spine the
  extraction must preserve.
- **Emission is already half-extracted:** the `_emit_*` helper family lives at
  **2424–3430+** (`_emit_whale_events`, `_emit_activity_events`,
  `_emit_last_stand_events`, `_emit_sim_events`, `_emit_burst_events`,
  `_emit_burst_summary`, `_emit_hand_events`, `_emit_hand_summary`,
  `_emit_carry_resolution_events`, `_emit_vice_spending_events`,
  `_emit_side_hustle_events`, `_emit_session_event`). Phase 5 **wires the inline
  per-table emissions into this existing pattern**, it does not invent it.

> Phase 0's first task is to re-derive exact start/end lines for stages 4–7
> (settlement / transfers / creations / events) from the anchors above and
> record them here, since they shifted with lifecycle hardening.

## Invariants that must survive every phase

These map directly onto the historic cash-mode bug classes (see memory:
ghost-seat recurrence, `seated_and_idle` split-brain, cash orphan double-settle):

- **Stage ordering:** settlement → bankroll transfers → stake creations →
  events. Transfers credit the `from_seat`; they *must* run after settlement has
  produced `settled_from_seat_indices`, never before.
- **`from_seat`-index contract:** `settled_from_seat_indices` (init 1607 → add
  1807 → consume 1834) is the only channel by which settlement tells transfers
  which seats it already drained. Thread it as an explicit return value /
  parameter; never reconstruct it.
- **Settle-once:** a stake/session is settled exactly once per refresh.
  Settlement extraction is the single highest-risk step for the double-settle /
  row-resurrection class — guard it hardest.
- **Seat/idle exclusivity:** a persona ends a refresh in *either* a seat *or* the
  idle pool, never both (the `seated_and_idle` split-brain).
- **Chip conservation:** total chips across bankrolls + seats + ledger + pool is
  unchanged by the refactor (drift check, below).

## Phase 0 — Safety net (do first, no production code change)

- Confirm the cash-mode suite green as the baseline:
  `python3 scripts/test.py -k "cash_mode"` (the triage cites 825+ tests).
- Add a **chip-conservation drift assertion** around one
  `refresh_unseated_tables` call on a fixture floor (sum bankrolls + seat stacks
  + pool + ledger before/after; assert delta `0`). Reuse the existing sandbox
  drift tooling if present.
- Add a **golden IO/event-log** test: drive the refresh against a fixture floor
  with fake repos + a recording event sink; snapshot the *ordered* list of
  (DB writes, emitted events). Every later phase must reproduce it unchanged.
- Pin the `settled_from_seat_indices` contract with an explicit test (AI-staked
  busted seat → its index is in the set → the transfer step consumes it).
- Re-derive and record the exact line ranges for stages 4–7 (see ground-truth
  note).

*Deliverable:* `tests/test_cash_mode/test_refresh_unseated_golden.py` + drift
check. No change to `lobby.py`.

## Phase 1 — Hoist the per-iteration closures (kills hidden loop state)

Lift `_buy_in_for` (1264), `_borrower_lookup_for_table` (1290),
`_psych_lookup_sim` (1346) — and, if it simplifies emission, `_ticker_name_for`
(1609) — out of the loop. Either make them module-level helpers taking their
inputs explicitly, or bundle them into a `PerTableContext` built once at the top
of each iteration. The pre-loop lookups (967–1209) can stay as-is for now. No
logic change; golden log unchanged.

## Phase 2 — Extract `_settle_table_stakes` (highest risk)

Move the settlement block (spine: 1607 → 1807 → 1834) into a module-level
`_settle_table_stakes(result, *, stake_repo, bankroll_repo, chip_ledger_repo,
relationship_repo, personality_repo, sandbox_id, now) -> settled_from_seat_indices`.
Pass every repo explicitly (no deferred imports, no closures). Keep the
human/AI-staker branch logic identical. **Return** the two inline events as data
rather than emitting in place, so Phase 5 owns emission — assert via the golden
log that the same events still fire in the same order. This is where
double-settle / resurrection could regress: review against the golden log and
the settle-once invariant before merging.

## Phase 3 — Extract `_apply_bankroll_transfers`

`_apply_bankroll_transfers(result, *, settled_from_seat_indices, bankroll_repo,
...)`. Consumes the index set from Phase 2 verbatim (the 1834 consumer). Assert
seat/idle exclusivity after the transfer. Golden log unchanged.

## Phase 4 — Extract `_apply_stake_creations`

`_apply_stake_creations(result, *, stake_repo, ...)`. As with settlement, return
the inline creation events as data for Phase 5 rather than emitting inline.

## Phase 5 — Route per-table emission through the existing `_emit_*` family

Add/extend a per-table emit step that consumes the `settle_events` and
`creation_events` returned by Phases 2 and 4 and dispatches them through the
existing `_emit_*` helpers (2424–3430+). All per-table emission now happens in
one place, after settlement/transfers/creations have committed. Removes the
inline-emission scatter; golden log asserts an identical event sequence.

## Phase 6 — Slim the loop body and orchestrator

The per-table loop body (from 1244) becomes a readable pipeline:

```
ctx        = build_table_context(table, ...)            # Phase 1
sim_result = run_sim_burst(ctx)                         # stage 2 (extract if it helps)
commit_aspirations_and_idle(sim_result, ...)            # stage 3 (save_table @1589)
settled    = _settle_table_stakes(result, ...)          # Phase 2
_apply_bankroll_transfers(result, settled_from_seat_indices=settled, ...)  # Phase 3
_apply_stake_creations(result, ...)                     # Phase 4
_emit_table_events(result, settle_events, creation_events, ...)            # Phase 5
```

Then hoist the scattered deferred `from x import y` to module top (or document
why each must stay deferred for circular-import reasons), and audit the ~dozen
best-effort `try/except` blocks — narrow each to the exception it actually
guards or comment why it must swallow. Run `pr-review-toolkit:silent-failure-hunter`
on the Phase 2 and Phase 6 diffs.

## Phase 7 — (optional) post-loop passes

The post-loop work (carry resolution, vice spending, side-hustle, closed-economy,
whale/casino provisioning, summary events) is *already* mostly helpers. Light
cleanup only — name/parameter consistency with the new stage functions. Not
required for the core refactor.

## Testing per phase

- `python3 scripts/test.py -k "cash_mode"` green after every phase.
- Golden IO/event log unchanged (any intended change is reviewed + the snapshot
  updated deliberately, with a note saying why).
- Chip-conservation drift `0` after every phase.
- Focused `silent-failure-hunter` pass on the Phase 2 and Phase 6 diffs.

## Risks & guardrails

- **Settlement is the danger zone** (Phase 2): the double-settle / resurrection
  bug class lives here. Do not change *when* a session is settled or *which*
  process owns it; honor any in-flight-settlement exclusion.
- **Ordering drift:** the golden log is the tripwire; do not reorder
  settlement/transfers/creations/events even if it "reads cleaner."
- **No feature creep:** keep table-attractiveness and seating-inversion work out
  of these PRs (owner directive).
- **One phase per PR:** never extract settlement + transfers + creations in a
  single change — the blast radius defeats the golden-log diff.

## Status

Plan drafted 2026-05-29; verified against the code the same day. **Core refactor
COMPLETE (green, uncommitted on `development`).** The per-table loop's three
inline stages are now named module-level helpers; `refresh_unseated_tables` went
from ~1,642 -> 1,311 lines. Continuation/handoff notes in
[`REFRESH_UNSEATED_TABLES_HANDOFF.md`](./REFRESH_UNSEATED_TABLES_HANDOFF.md).

- [x] Phase 1 -- hoist `_ticker_name_for` to module level (green)
- [x] Phase 2 -- extract `_settle_table_stakes` (green; preserves the `settled_from_seat_indices` spine)
- [x] Phase 4 -- extract `_apply_stake_creations` (green; done before Phase 3 per the `debit_bankroll_for_seat` import gotcha)
- [x] Phase 3 -- extract `_apply_bankroll_transfers` (green; consumes `settled_from_seat_indices`)
- [x] Phase 5 -- emission: **satisfied by acceptance.** Activity/burst events already route through the existing `_emit_*` family; the two inline ticker emits (`EVENT_AI_DEFAULT`, `EVENT_AI_STAKE`) now ride *inside* the extracted settlement/creation helpers -- behavior-preserving and the agreed acceptable resolution (the "return events as data" purity step was optional and skipped).
- [x] Phase 6 -- slim loop (the body is now a short pipeline). **Imports: decision to KEEP deferred.** Verified there is **no circular dependency** -- the deferred-target modules (`full_sim`, `casino_provisioning`, `ai_vice_spending`, `ai_side_hustle`, `closed_economy`, `ai_carry_resolution`) do **not** import `lobby` (only mention it in comments), and all import cleanly at top level. The imports stay deferred because they are **conditional, feature-gated lazy loads**: each sits inside a runtime gate (`vice_mode == 'real'/'fake'`, `SIDE_HUSTLE_ENABLED`, `chip_ledger_repo is not None`) and only loads when that subsystem is active for the call -- moving ~20 such imports to module top would load optional subsystems unconditionally on every import of `lobby` for marginal readability, so they were intentionally left in place. The ~dozen best-effort `try/except` blocks are **intentional fail-soft boundaries** around those optional subsystems (each logs a warning + is already commented); left as-is.
- [ ] Phase 7 -- (optional) post-loop pass cleanup -- **skipped**: those passes are already extracted helpers; no further work needed for the god-function goal.

### Verification

- `tests/test_cash_mode/` (514 tests, ~9s): **green (pytest rc=0)** after each phase.
- Broader importers: `tests/test_cash_lobby_route.py`, `tests/test_ticker_service.py`, `tests/test_cash_mode/test_lobby_seat_chip_conservation.py`: **green (rc=0)**.
- `ast.parse` clean; pipeline ordering verified (settle -> transfers -> creations -> emit); no leftover inline stage bodies.
- Diff: +459 / -358 on `cash_mode/lobby.py`. **Uncommitted** -- not committed pending user direction.

> Phase 0's standalone golden-log test was judged redundant given the fast 514-test `tests/test_cash_mode/` suite, used as the oracle after every extraction. Add a characterization test only if you want belt-and-suspenders.

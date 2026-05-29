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

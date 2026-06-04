---
purpose: Scope the chip-custody atomic-write unit-of-work that closes the int↔ledger divergence at its source (T3-82 Tier-2)
type: design
created: 2026-06-04
last_updated: 2026-06-04
---

# Chip-custody atomic writes (T3-82 Tier-2)

## Status (2026-06-04)

Implemented on branch `chip-custody-atomic-uow` (off `development`):

- **A — primitives:** `BaseRepository.transaction()` (re-entrant, depth-aware
  `_get_connection()`), `save_player_bankroll(conn=)`, `chip_unit_of_work()`,
  and `conn=` threaded through every ledger helper used below.
- **B — hottest:** `credit_ai_cash_out`, `debit_bankroll_for_seat`. ✅
- **C — faucets/sinks/carry:** side-hustle, real vice, fake vice, carry
  voluntary-payoff. ✅
- **Bankruptcy:** `try_ai_bankruptcy` (two-pass: chips in the txn, stake_repo
  discharges after). ✅
- **D — casino:** fish prefund/drain. ✅  **D — tournament:** payouts
  (`apply_payout_on_complete` + `reconcile_stuck_payout`). ✅

**Deferred (documented, low-frequency, covered by Phase E reconcile):**
- **Tournament buy-in** (`apply_buy_in`) — a deliberate
  debit→`session_repo`→ledger→verify saga with explicit debit-reversal;
  already conservation-safe, crash-atomicity needs a saga rewrite.
- **Human `cash_routes`** sit/rebuy/leave + stake origination — many
  interleaved seat/session/table/presence writes per route.

**The hard constraint we hit (the lock lesson):** a unit-of-work may NOT span
another repo's write to the same SQLite file — the open writer transaction on
`bankroll_repo`'s connection deadlocks a `stake_repo`/`session_repo`/etc. write
(single-writer + 5s busy_timeout → "database is locked"). So each UoW span must
contain ONLY `bankroll_repo` int writes + ledger rows (which ride that same
connection via `conn=`). Where other-repo writes interleave, either split into
passes (bankruptcy) or defer (tournament buy-in, human routes). This is why the
remaining paths are deferred rather than force-wrapped.

## Problem

The AI/player **bankroll int** (the served authority) and its **`chip_ledger_entries`
row** are written in **separate commits on separate per-repo thread-local
connections**. On a crash, restart, or concurrent read in that ~ms window, one
lands without the other. Across hundreds of thousands of sim transactions this
accumulates small, mixed-sign drift between stored and ledger-derived balances
(observed: ~24k abs across 14/134 AIs in one sandbox; ~0.01–0.03% of gross
flow). This is the root cause behind the AI-side stored-vs-derived divergence —
not a discrete leak, an **atomicity gap**. It is the standing blocker on ever
treating the ledger as a read authority and on a clean `audit_ledger_completeness`.

## What already exists (do NOT rebuild)

CP-19 (`development` @ `25241838`, originally local branch
`chip-custody-atomic-writes`) shipped the **seam**:

- `ChipLedgerRepository.record(..., conn=None)` — when `conn` is given, the
  INSERT runs on the caller's connection (joins their transaction) instead of
  opening its own. All `record_*` helpers in `core/economy/ledger.py` thread
  `conn=` through (`record_ai_seed`, `record_ai_regen`, `record_ai_buy_in`,
  `record_ai_cash_out`, transfers, …).
- `BankrollRepository.save_ai_bankroll(..., conn=None)` — the int upsert + the
  first-write `ai_seed` commit on ONE connection when `conn` is passed. **This is
  the template** every chokepoint below should follow.
- Casino prefund/drain got logical-failure backstops (credit/zero the int only
  if the ledger write landed).

`BaseRepository._get_connection()` is a per-repo, thread-local, auto-committing
context manager (WAL, 5s busy timeout). Each repo instance has its OWN
connection to the same DB file — so atomicity means running BOTH writes on ONE
of those connections (CP-19 uses the bankroll repo's).

## Goal / non-goal

- **Goal:** every (int write + its paired ledger row[s]) commits in ONE
  transaction, so a failure rolls back both — divergence stops accumulating.
- **Non-goal:** flipping `CHIP_CUSTODY_DERIVE_READS` on in prod. It does not
  scale (O(rows/account) per read, no index) and stays a dev tripwire. The
  end-state is **int-as-read + periodic reconcile**; atomic writes make that
  reconcile converge to ~0 instead of fighting fresh drift.

## Chokepoint inventory (int + ledger pairs to make atomic)

Each currently does `save_ai_bankroll(new int)` then a SEPARATE `record_*` on the
ledger repo's connection. Hottest first (by transaction volume → divergence
contribution):

| # | Site | Int write | Paired ledger row(s) |
|---|------|-----------|----------------------|
| 1 | `cash_mode/bankroll.py` `credit_ai_cash_out` | save_ai_bankroll | `ai_regen` + `ai_cash_out` |
| 2 | `cash_mode/bankroll.py` `debit_bankroll_for_seat` | save_ai_bankroll | `ai_regen` + `ai_buy_in` |
| 3 | `cash_mode/ai_side_hustle.py` ~618 | save_ai_bankroll | `side_hustle_earning` |
| 4 | `cash_mode/ai_vice_spending.py` ~1053 | save_ai_bankroll | `vice_spending` / `bank_pool_deposit` |
| 5 | `cash_mode/closed_economy.py` ~506 | save_ai_bankroll | fake-vice / grinder transfer |
| 6 | `cash_mode/ai_carry_resolution.py` ~689 | save_ai_bankroll | `ai_regen` (borrower debit) |
| 7 | `cash_mode/ai_carry_resolution.py` ~1389 | save_ai_bankroll | regen / `stake_payoff` |
| 8 | `cash_mode/casino_provisioning.py` 573/634/1759 | save_ai_bankroll | `casino_seat_seed` / `_return` (backstopped, not yet atomic) |
| 9 | `flask_app/services/tournament_economy_service.py` 324/489 | save_ai_bankroll | `tournament_buy_in` / `_payout` / `_overlay` / `_return` |
| 10 | `cash_mode/lobby.py` 377/5071/5089 | save_ai_bankroll | seed / credit |
| 11 | `flask_app/handlers/game_handler.py` 1087 | save_ai_bankroll | regen / cash_out |
| 12 | `flask_app/routes/cash_routes.py` (human) `player_buy_in` / `player_cash_out` + the new `stake_fund` / `stake_payoff` | save_player_bankroll | the matching transfer row |

(#12 needs `save_player_bankroll(conn=)` added — it has no `conn` param yet.)

## Design

1. **`BaseRepository.transaction()`** — public context manager yielding the
   thread-local connection inside a single explicit transaction (no per-statement
   commit; commit on clean exit, rollback on exception). Re-entrancy guard so a
   nested call reuses the open txn instead of double-committing.
2. **`save_player_bankroll(..., conn=None)`** — mirror `save_ai_bankroll`.
3. **`chip_unit_of_work(primary_repo)`** helper — yields a real connection when
   `primary_repo` is a real `BaseRepository` on the same DB file as the ledger
   repo, else yields `None`. Chokepoints pass `conn` to both writes; on `None`
   they fall back to today's separate-write behavior. **This is the test-double
   escape hatch** the TRIAGE note flags ("reaching `_get_connection()` cross-repo
   breaks doubles") — MagicMock/fake repos get `None` and keep working.
4. **Convert each chokepoint** to:
   ```python
   with chip_unit_of_work(bankroll_repo) as conn:
       bankroll_repo.save_ai_bankroll(state, sandbox_id=sb, conn=conn)
       chip_ledger.record_ai_cash_out(..., conn=conn)   # rides the same txn
   ```

## Hard parts / risks (call out before coding)

- **Partial-failure semantics change.** Today a ledger-write failure is swallowed
  (best-effort) and leaves the int moved → divergence. Under the UoW a ledger
  failure rolls back the int too → the chip move *didn't happen*, and the caller
  must handle it (e.g. `debit_bankroll_for_seat` already refuses + unwinds the
  seat; `credit_ai_cash_out` on a cash-out failure is harder — the AI is leaving
  a seat that's being torn down). Decide per-site: roll-back-and-refuse vs.
  log-and-accept-divergence. Atomicity is only real where the caller can unwind.
- **Same-DB-file assumption.** Running the ledger INSERT on the bankroll repo's
  connection only works if both repos point at the same file. Assert
  `bankroll_repo.db_path == chip_ledger_repo.db_path` in `chip_unit_of_work`;
  fall back to separate writes (today's behavior) if not.
- **Connection re-entrancy.** The thread-local conn is shared across a repo's
  methods; an outer `transaction()` plus an inner `_get_connection()` would
  commit early. Need the re-entrancy guard, and audit chokepoints that already
  sit inside another `with _get_connection()`.
- **WAL write-lock duration.** Longer transactions hold the single writer lock;
  the world-tick thread + request threads can contend (5s busy_timeout → possible
  timeouts under load). Keep each UoW to the minimal two/three statements.
- **`from_seat=False` stake paths.** The just-landed human/AI `stake_payoff`
  writes (lobby `settle_departed_ai_stake`, carry resolution) must thread `conn`
  too, or they reintroduce a split commit.

## Phasing

- **A — primitives:** `transaction()`, `save_player_bankroll(conn=)`,
  `chip_unit_of_work` + unit tests (incl. forced-ledger-failure → int rolled back;
  test-double → graceful fallback).
- **B — hottest two:** `credit_ai_cash_out` + `debit_bankroll_for_seat`. These
  dominate volume; converting them should stop ~all fresh drift. Validate with a
  sim burst (assert derive==stored growth ≈ 0).
- **C — faucets/sinks:** side-hustle, vice, closed-economy, carry resolution.
- **D — casino + tournament + human (#8,#9,#12)**.
- **E — converge + guard:** one-time reconciliation of the *existing* residue
  (per-(pid,sandbox) adjusting `ledger_reconciliation` rows, like the human-staking
  backfill) + a periodic `audit_ledger_completeness` job; optionally a CI-only
  `DERIVE_READS=1` assertion test so regressions can't silently return.

## Testing

- **Atomicity:** monkeypatch the ledger `record` to raise mid-UoW; assert the int
  did NOT persist (transaction rolled back).
- **Conservation growth:** run an N-hand sim burst on a temp DB, assert
  `derive_ai_balance == stored` for every (pid, sandbox) before and after (Δ
  divergence == 0). This is the regression that proves the gap is closed.
- **Test doubles:** existing cash_mode suite (MagicMock repos) stays green via the
  `None`-conn fallback.
- Full `cash_mode` + `test_repositories` buckets.

## Effort

Medium–large. A ≈ 0.5d, B ≈ 0.5d + validation, C ≈ 0.5d, D ≈ 1d, E ≈ 0.5d.
Phase B alone captures most of the value (the two highest-volume paths).

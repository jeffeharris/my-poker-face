---
purpose: Diagnosis and professional remediation plan for the cash-economy chip-mint leak (seat double-drain), to be executed by finishing the chip-custody and stake state machines
type: design
created: 2026-06-12
last_updated: 2026-06-12
---

# Chip-Mint Leak — Seat Double-Drain (Handoff)

## ✅ RESOLUTION (2026-06-12) — FIXED & PROVEN

The leak is closed by **completing chip-custody Phase 4** (the ledger is now the
single, authoritative record of every chip) and enforcing the conservation law at
the cash-out chokepoint. No band-aids, no clamp-as-fix.

**What shipped:**
1. **Per-hand P&L is ledgered** (`hand_pnl` reason + `record_hand_pnl` /
   `record_hand_pnl_redistribution` in `core/economy/ledger.py`), wired into BOTH
   the headless sim (`cash_mode/full_sim.py`) and the live engine
   (`flask_app/handlers/game_handler.py::_record_player_table_hand_pnl`). So
   `balance_of(seat)` tracks the live stack **continuously**, not just at
   buy-in/cash-out — the seat IS a true ledger view.
2. **The conservation law at the cash-out chokepoint** (`cash_mode/bankroll.py::
   credit_ai_cash_out`): a seat cash-out can never remove more chips than the seat
   holds (`balance_of(seat:ai)`). Because per-hand P&L is ledgered, this is inert
   in correct operation and bites only on a SECOND vacate path (bounds it to 0) —
   the **structural kill** for the double-drain. A genuine over-drain warns loudly.
   `settle_ai_seat()` is the clean explicit-settle primitive built on this.
3. **Bankroll is derived** (`CHIP_CUSTODY_ENABLED` + `CHIP_CUSTODY_DERIVE_READS`
   GRADUATED → always-on, env kill switch removed; mirrors
   `PRESENCE_AUTHORITY_ENABLED`). The ledger is the served authority; the int is a
   cache. No dual-path / rollback code.

**Why a chokepoint guard, not the presence-latch settle from the plan below:**
draining `balance_of(seat)` is *self-idempotent* — the seat balance IS the latch,
so the mint dies regardless of which/how-many vacate paths fire, without plumbing
repos through `save_table`'s ~23 call sites (lower risk, same invariant).

**Proof (run it):** `make validate-economy-conservation` (and
`tests/test_cash_mode/test_seat_conservation.py`). The validator
(`scripts/validate_chip_custody.py`, now seat-aware) runs a churned economy sim
and asserts at every checkpoint: AI accounts reconcile (derived==stored), **no
negative seat**, **Σseat==Σstacks**, **global Σnon-bank == −central_bank**. Latest:
PASS over 400 ticks, all residuals 0.

**Still open (separate, lower priority):** recovering the ~5.87M already minted in
prod sandbox `bfa7050b…` (a one-time `phantom_clawback`) — the user chose
"land + verify the fix first." The fix above stops the bleeding; the stock recovery
is a follow-up against a backed-up DB.

The original diagnosis + plan is retained below for context.

---


## TL;DR

Prod cash sandbox `bfa7050b-5762-4ff3-8551-1781f367ee74` (Jeff's "My Casino")
is minting chips. Authoritative `compute_audit` drift ≈ **−2.74M net** (actual
holdings exceed ledger); the gross signature is an **aggregate AI-seat ledger
balance of ≈ −5.87M** (and growing in episodic bursts). The leak is **fully
attributed** (residual-zero decomposition) and **proven** (not inferred).

**Root cause:** a single `seat:ai:<sandbox>:<pid>` ledger account is **funded by
one path** but **drained by three non-mutually-exclusive cash-out paths**, with
**no single-settle guard and no authoritative seat balance**. The same
accumulated chips get cashed out more than once → mint.

**This is the ghost-seat / double-settle bug class** (recurring; see
`feedback_cash_seat_double_seat_recurrence`). The professional fix is NOT a
`min(stack, balance)` clamp — it is to **finish the two state machines** whose
deferred keystones are the reason this is possible:
- chip-custody **Phase 4 (seats-as-view)** — make the ledger seat the authority.
- stake **SETTLING→SETTLED conservation keystone** — enforce, don't alarm.

## The proof (all data-backed, reproducible against the sandbox)

1. **Residual-zero drift decomposition.** `drift = Δai + Δplayer + Δseat + loans
   − reconciliation − human_seat`. Computed against prod: every term is noise
   except **Δseat_ai = +5.6M**, and the sum equals the authoritative audit drift
   to the chip with **residual = 0**. ⇒ the entire drift is the AI seat accounts;
   nothing else mints. (This is the bar: a non-zero residual would mean an
   unidentified second leak.)
2. **Per-seat conservation.** Across 106 AI seats: 37 net-negative ("winner")
   seats sum **−7.8M**; 69 net-positive ("loser") seats sum **+2.5M**; aggregate
   **−5.26M** (→ −5.87M now). **Zero** seats have a cash-out with no buy-in. A
   closed economy requires `Σ outflow ≤ Σ inflow` (open seats are positive), so
   the −5.87M is minted chips, full stop.
3. **Per-hand loop ruled out at runtime.** A temporary conservation probe
   (hot-patched into `refresh_unseated_tables`) stayed **silent through a −332K
   burst** across 13 active tables ⇒ the headless hand sim (`full_sim` /
   `fake_sim`) and per-hand movement **conserve**. The mint is NOT per-hand.
4. **Site attribution.** The minting cash-outs carry `site=seated_table_vacate`
   and `cash_leave_cashout` (the human-table paths) — concentrated in the
   personas seated at Jeff's table (carnegie lifetime gap −790K, alexander
   −917K = ~1.7M of the −5.87M between just those two).
5. **Double-drain confirmed.** For `andrew_carnegie`: **8 back-to-back
   `ai_cash_out` events with NO `ai_buy_in` between them**, e.g.
   `cash_leave_cashout 17,603` → `cash_leave_cashout 26,403` (same path twice,
   6 min apart), and `cash_leave_cashout 123,967` → `refresh_table_roster_vacate
   131,589`. Each extra drain credits the bankroll chips the seat did not hold.

## Root cause in code

A `seat:ai:<sandbox>:<pid>` account (one per AI per sandbox — single-presence is
*assumed* but not *enforced*):

- **Funded by one path:** `cash_mode/bankroll.py::debit_bankroll_for_seat` →
  `record_ai_buy_in` (`ai:<pid>` → `seat:ai:...`).
- **Drained by three paths**, none of which checks the seat balance or marks the
  seat settled:
  1. `refresh_table_roster_vacate` — lobby-table leave (`cash_mode/lobby.py`,
     credit loop ~`game_handler.py` / lobby apply; `credit_ai_cash_out`).
  2. `seated_table_vacate` — AI removed from the human's live game
     (`flask_app/handlers/game_handler.py:2687`, `_credit_departed_ai_bankrolls`).
  3. `cash_leave_cashout` — the human leaves the session and **every** seated AI's
     `player.stack` is cashed out (`flask_app/routes/cash_routes.py:5366`).
- **Per-hand P&L is never ledgered** (chip-custody Phase 1 deliberate choice — it
  "nets inside the seat balance and settles at cash-out";
  `CASH_MODE_CHIP_CUSTODY_HANDOFF.md:115`). So the game-state stack and the ledger
  seat balance diverge by design, and the cash-out amount (`player.stack` /
  `from_seat`) is taken from game-state with **no authoritative seat balance to
  bound it**.

Because nothing makes the three vacate paths mutually exclusive or idempotent, an
AI that crosses the human-table ↔ lobby boundary (the rotating grinders do this
constantly) gets cashed out by more than one path per funding.

## Why the state machines are the answer (and were the gap all along)

Both subsystems were built **up to but not including** the phase that enforces
conservation:

- **chip-custody** (`docs/plans/CASH_MODE_CHIP_CUSTODY_HANDOFF.md`): Phases 1–3, 5
  shipped; **Phase 4 (seats-as-view) "NOT done (deliberately)"** — the phase that
  makes the ledger `seat:` balance the **authority** and the live stack a derived
  view. Without it there is no single source of truth for "chips at this seat,"
  so a double-drain is representable.
- **stake** (`docs/plans/CASH_MODE_STAKE_STATE_MACHINE.md`): the funding-side fix
  shipped (#217/#235), but the **keystone — guarded `SETTLING → SETTLED` with a
  conservation assert** — ships only as the alarm-only guard
  `assert_stake_funding_reached_borrower_seat` with
  `STAKE_SETTLE_GUARD_ENFORCE=0`. The doc itself names the real invariant:
  *"aggregate seat balance ≥ 0."*

## Remediation plan (professional; phased; no shortcuts)

Guiding invariant — make it true **by construction**, not by a downstream clamp:

> A `seat:ai` account is funded by exactly one buy-in transition and drained by
> exactly one settle transition. `balance_of(seat:ai) ≥ 0` at all times. Chips at
> a seat have ONE authority: the ledger seat balance.

**Phase A — Unify + single-settle the cash-out (stops the bleeding, correctly).**
Collapse the three vacate paths into ONE guarded settle transition on the seat
lifecycle. The transition: CAS on seat status (OPEN→FUNDED→ACTIVE→SETTLING→
SETTLED); the first settle drains the seat exactly once; any second caller finds
SETTLED and no-ops. This is the seat sibling of the stake machine's one-shot
`SETTLING` CAS. All of `refresh_table_roster_vacate`, `seated_table_vacate`,
`cash_leave_cashout` call it; none drains directly.

**Phase B — Make the ledger seat authoritative (chip-custody Phase 4 slice).**
The cash-out amount must come from the seat's ledger balance, not a separately
tracked game-state stack. Minimum viable slice: at each session/hand boundary,
reconcile the seat ledger to the realized stack with a *ledgered* transition, so
the two can never silently diverge and the settle drains a real balance. This is
the deferred-but-now-required Phase 4 work, scoped to what conservation needs.

**Phase C — Enforce the stake keystone.** Route AI-stake settlement through the
guarded `SETTLING→SETTLED` transition with the conservation assert; set
`STAKE_SETTLE_GUARD_ENFORCE=1`. (The stake double-cash via `ai_stake_settle_*`
shares the same root and must funnel through the same single-settle.)

**Phase D — Recover the stock.** One-time `phantom_clawback`: a real
`central_bank` **destruction** debiting the over-credited AI bankrolls until
`actual == ledger`. NOT another `ledger_reconciliation` transfer (that is
bank-neutral and only repaints derived reads — it is what failed on 2026-06-08).
Run against a backed-up DB (`docs/...SQLITE_WAL_BACKUP` discipline).

**Phase E — Permanent guard.** Daily/continuous alarm: `min(seat:ai ledger
balance) ≥ 0` and `compute_audit` drift within tolerance. Any regression of the
seat-conservation invariant surfaces same-day instead of N sandboxes later.

## Key files

- `core/economy/ledger.py` — ledger vocabulary, `record_ai_buy_in` /
  `record_ai_cash_out` / `record_ledger_reconciliation`, `balance_of` helpers.
- `cash_mode/bankroll.py` — `debit_bankroll_for_seat` (buy-in), `credit_ai_cash_out`
  (the drain — currently un-guarded; the single-settle transition lands here or
  wraps it).
- `flask_app/handlers/game_handler.py:2687` — `_credit_departed_ai_bankrolls`
  (`seated_table_vacate`).
- `flask_app/routes/cash_routes.py:5366` — human-leave cash-out
  (`cash_leave_cashout`).
- `cash_mode/lobby.py` — `refresh_unseated_tables` / `refresh_table_roster` lobby
  vacate (`refresh_table_roster_vacate`).
- `cash_mode/stake_lifecycle.py` — `assert_stake_funding_reached_borrower_seat`,
  the guard to enforce.
- Docs: `CASH_MODE_CHIP_CUSTODY_HANDOFF.md` (Phase 4), `CASH_MODE_STAKE_STATE_MACHINE.md`
  (settle keystone), `flask_app/services/chip_ledger_audit.py::compute_audit`.

## Prod state at handoff (2026-06-12)

- Nothing committed. The diagnostic probe was hot-patched into the running
  container and **already wiped** by the CI runner (`github-runner`) re-syncing
  `/opt/poker` on its periodic redeploy. Working tree reverted to clean `main`.
- Deploys = self-hosted GitHub Actions runner; container is recreated on its
  schedule, so **the only durable change path is git → PR → runner deploy.**
- A session-scoped Monitor was watching the cumulative seat-ledger every 20 min
  (heartbeat ≈ −5.87M, episodic bursts of −60K/hr). It ends with the session.
  Re-create the heartbeat (host-side, read-only) with:
  ```python
  import sqlite3
  from collections import defaultdict
  c = sqlite3.connect('file:/opt/poker/data/poker_games.db?mode=ro', uri=True).cursor()
  sb = 'bfa7050b-5762-4ff3-8551-1781f367ee74'
  d = defaultdict(int)
  for s, k, a in c.execute("SELECT source,sink,amount FROM chip_ledger_entries "
      "WHERE sandbox_id=? AND (source LIKE 'seat:ai:%' OR sink LIKE 'seat:ai:%')", (sb,)):
      if k.startswith('seat:ai:'): d['i'] += a
      if s.startswith('seat:ai:'): d['o'] += a
  print('cumulative_seat_ledger =', d['i'] - d['o'])  # ≈ -5.87M; should rise to ~0 after fix+clawback
  ```
- `ssh root@178.156.202.136`; DB inside container `/app/data/poker_games.db`,
  on host `/opt/poker/data/poker_games.db`.

## Note on stale memory

`project_aspire_funding_phantom_chips` says "diagnosed, NOT fixed" — that is
**stale**: the aspire *funding* bug WAS merged (#217/#235, verified in code). The
live leak is the **seat double-drain** described here, a different (broader)
mechanism. The funding fix was real but incomplete; the conservation keystone was
never finished.

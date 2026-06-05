---
purpose: The double-entry chip ledger — how every chip movement in the closed economy is auditable, how bankroll/seat balances derive from it, and the conservation invariants it enforces
type: architecture
created: 2026-06-03
last_updated: 2026-06-03
---

# Chip Custody — The Ledger-as-Authority Model

This is the authoritative reference for the chip ledger. The
[economy doc](CASH_MODE_ECONOMY.md) describes the closed-loop *behaviour*
(pool, vice, tourists); this doc owns the *custody substrate* underneath it:
the append-only event log, the account vocabulary, the two cutover flags, and
the conservation invariants the audit checks against.

## The one-sentence model

`chip_ledger_entries` is an **append-only event log** of every chip
creation, destruction, and transfer. The `chips` int on the bankroll tables is
a **cache** the ledger can re-derive. Chip conservation is the invariant:

```
Σ(creations)  −  Σ(destructions)  ==  Σ(all held balances)
```

Any non-zero difference is **drift** — the signal that chips moved without a
ledger row. The ledger is never reconciled by editing rows; drift is surfaced,
not papered over (`chip_ledger_repository.py:27-29` — "no updates, no deletes").

## Why a ledger at all (the "why")

The custody work was prompted by a real chip loss: a human's cash buy-in was
silently swept. The root insight, from the design retrospective
(`docs/captains-log/development/chip-custody-cutover.md`), was that a chip held
in a row that gets deleted is *forfeited with no trace* — there was no
transaction history to prove a buy-in ever happened. The ledger makes every
buy-in/cash-out a balanced pair so a deletion can never strand chips invisibly.

A second retrospective reframing from the same log: the alarming **"32.6M gap"**
in early audits was **not missing chips** — it was *cancelling per-account noise*
(AI-vs-AI table P&L moving between accounts with no ledger row). The fix was to
ledger the seat buy-in/cash-out transfers so per-account gaps collapse; it was
never a conservation hole. (This is log-attributed rationale, not a code claim.)

## 1. The table: `chip_ledger_entries`

Schema created in migration **v93** (`schema_manager.py:5467`); a nullable
`sandbox_id` column was added in **v103** (`schema_manager.py:6008`).

| Column | Notes |
|---|---|
| `entry_id` | `INTEGER PRIMARY KEY AUTOINCREMENT` |
| `created_at` | `TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP` |
| `source` | account chips leave |
| `sink` | account chips arrive |
| `amount` | `INTEGER NOT NULL CHECK (amount >= 0)` — direction is `source`/`sink`, never a negative amount (`schema_manager.py:5472`) |
| `reason` | one of `LEDGER_REASONS` / `TRANSFER_REASONS` |
| `context_json` | optional JSON blob (call-site metadata) |
| `sandbox_id` | v103; `NULL` for pre-v103 / migration rows |

Indexes match the audit queries: `created_at DESC` (window scans) and `reason`
(per-reason breakdowns) (`schema_manager.py:5478-5482`).

**Repository:** `poker/repositories/chip_ledger_repository.py`. Insert-and-read
only. Two methods carry the system:

- `record(...)` (`chip_ledger_repository.py:32`) — appends one row. Takes an
  optional `conn`: when given, the INSERT runs on the **caller's open
  connection** so the ledger row commits in the *same* transaction as the
  caller's bankroll-int write (closes the two-commit divergence window).
  `conn=None` opens and commits its own connection — the historical default
  (`chip_ledger_repository.py:59-78`).
- `balance_of(account, *, sandbox_id)` (`chip_ledger_repository.py:81`) —
  `Σ(amount where sink=account) − Σ(amount where source=account)`. This is the
  D2 substrate: a bankroll as the sum of its ledger parcels. **Scope asymmetry:**
  `sandbox_id` given → sum that sandbox only (AI accounts are per-save-file);
  `sandbox_id=None` → sum across all sandboxes (a human's bankroll roams with
  them — design point D6) (`chip_ledger_repository.py:91-98`).

## 2. The vocabulary layer: `core/economy/ledger.py`

Call sites never touch the repository directly — they go through
`core/economy/ledger.py`. Two reasons, stated at the module top
(`ledger.py:4-12`):

1. **Vocabulary stability.** Reason strings live in `LEDGER_REASONS`; an unknown
   reason is *rejected* so a typo becomes a test failure, not silent drift.
2. **Swap point.** A future reserves-aware central bank replaces the write path
   without changing any call site's signature.

### Account constructors (canonical forms)

Use the constructors, never raw f-strings — they `raise` on empty IDs that an
f-string would let through silently.

| Constructor | Form | Role |
|---|---|---|
| `bank()` | `central_bank` | the chip universe source/sink (`ledger.py:220`) |
| `player(owner_id)` | `player:<owner_id>` | a human's bankroll (`ledger.py:225`) |
| `ai(personality_id)` | `ai:<personality_id>` | an AI's bankroll (`ledger.py:232`) |
| `seat(game_id)` | `seat:<game_id>` | a human's live cash-seat chips — transfer-only (`ledger.py:239`) |
| `ai_seat(sandbox_id, pid)` | `seat:ai:<sandbox_id>:<pid>` | an AI's live cash-seat chips — transfer-only (`ledger.py:270`) |
| `tournament(tid)` | `tournament:<tid>` | prize escrow (`ledger.py:254`) |

**Why AI seats key by `(sandbox_id, personality_id)` not `game_id`:** humans have
one live game per session, so `game_id` uniquely identifies their seat. World/sim
AIs churn `cash_tables` with no per-AI `game_id`, so the AI seat account is keyed
by sandbox+persona. Because an AI is seated at most one cash seat per sandbox
(single-presence), this balance is exactly that one AI's at-table chips — per-entity
custody without chasing callers (`ledger.py:270-289`; rationale also in the
captain's log).

### Two write paths

`record()` (`ledger.py:334`) — creations and destructions. Exactly **one** side
must be `central_bank` (`ledger.py:401-409`). Validates reason ∈ `LEDGER_REASONS`
(`ledger.py:369`) and `amount >= 0` (`ledger.py:390`). On a *validation* failure it
logs WARNING and returns `None` — never kills a chip-moving path. On a real DB-write
failure (validation already passed) it logs **ERROR** because the bankroll int
likely committed without its ledger row = drift risk (`ledger.py:421-439`).

`record_transfer()` (`ledger.py:442`) — pure movements between two non-bank
surfaces. **Neither** side may be `central_bank` (`ledger.py:493`). Reason must be
in `TRANSFER_REASONS`. Conservation-neutral: transfer rows are *invisible* to the
creation/destruction sums, so they never enter the drift math. They exist solely as
the human-readable chip statement (the audit trail the silent-forfeiture bug
exposed as missing).

### Reason taxonomy (`LEDGER_REASONS`, `ledger.py:34-156`)

**Creations** (`central_bank → X`): `player_seed`, `ai_seed`, `ai_regen`,
`house_stake_issue`, `pre_ledger_universe`, `tourist_injection`,
`side_hustle_earning`, `casino_seat_seed`, `tournament_overlay`,
`bank_pool_sim_seed`.

**Destructions** (`X → central_bank`): `cap_clamp` *(deprecated — historical
rows only; `bankroll_cap` was retired when `starting_bankroll` became a regen
target not a ceiling, `ledger.py:66-71`)*, `house_stake_settle`, `table_rake`,
`bank_pool_deposit`, `vice_spending`, `tournament_return`, `casino_seat_return`,
`informant_unlock`, `forgive_balance` *(annotation row, amount=0)*.

**Transfers** (`TRANSFER_REASONS`, `ledger.py:164-174`, no bank side):
`player_buy_in`, `player_cash_out`, `ai_buy_in`, `ai_cash_out`, `stake_payoff`,
`tournament_buy_in`, `tournament_payout`.

### Pool accounting

The recyclable bank pool is *virtual* — derived from reason sums:

```
pool_depth = Σ(BANK_POOL_DEPOSIT_REASONS) − Σ(BANK_POOL_DRAW_REASONS)
```

- `BANK_POOL_DEPOSIT_REASONS` (`ledger.py:190`): `bank_pool_deposit`,
  `vice_spending`, `casino_seat_return`, `table_rake`, `informant_unlock`,
  `tournament_return`. (`table_rake` recycles into the pool rather than
  evaporating — same `winner → central_bank` direction, only its *classification*
  moved, `ledger.py:185-189`.)
- `BANK_POOL_DRAW_REASONS` (`ledger.py:204`): `tourist_injection`,
  `casino_seat_seed`, `side_hustle_earning`, `tournament_overlay`.

## 3. The two cutover flags

Both live in `cash_mode/economy_flags.py` and default `False`, mirroring the
Presence machine's `PRESENCE_AUTHORITY_ENABLED` env pattern. They split "record
the ledger" from "trust the ledger for reads".

### `CHIP_CUSTODY_ENABLED` (`economy_flags.py:253`, env `CHIP_CUSTODY_ENABLED`)

Gates the **AI side** of the ledger (the "Cut 2 parity" work — humans were
always-on). When `True`:

- `debit_bankroll_for_seat` records an `ai → seat:ai:<sb>:<pid>` transfer
  (`ai_buy_in`).
- `credit_ai_cash_out` records a `seat:ai:<sb>:<pid> → ai` transfer
  (`ai_cash_out`) — but only for real seat cash-outs (`from_seat=True`).
- `_settle_orphan_seat_to_bankroll` (boot-sweep reaper) runs its
  settle-before-delete logic.
- `settle_ai_bankroll_to_pool_on_delete` recycles AI chips on persona delete.

When `False`, all of those are guarded no-ops. **The bankroll ints still move
correctly — only the ledger recording is skipped.** Dev sets
`CHIP_CUSTODY_ENABLED=1` in `.env` (gitignored); the committed compose default
stays `0`, so production is unaffected.

### `CHIP_CUSTODY_DERIVE_READS` (`economy_flags.py:265`, env `CHIP_CUSTODY_DERIVE_READS`)

Gates **D2** — whether bankroll *reads* derive from the ledger or return the
stored int. When `True`, `BankrollRepository._derived_or_cached_ai_chips`
(`bankroll_repository.py:87`) returns `balance_of(ai(pid), sandbox_id)` and logs
a `[CHIP_CUSTODY] ... cache divergence` WARNING if derived ≠ stored — **the
ledger wins** (`bankroll_repository.py:104-113`). Requires `CHIP_CUSTODY_ENABLED`
and a backfilled DB.

**Why it's deferred:** the int is the transaction-consistent hot-path cache. The
bankroll int is written *first*, then the ledger row is appended immediately
after; a derived read in that sub-millisecond window would be momentarily stale.
The flip to derive-reads waits on a double-read audit proving int==derived
(`economy_flags.py:255-265`).

## 4. How bankroll & seat balances derive from the ledger

### The two AI chokepoints — `cash_mode/bankroll.py`

**`debit_bankroll_for_seat`** (`bankroll.py:388`) — AI sits down (lobby seed,
live-fill, casino spawn, player-route sit). Flow when a ledger repo is present:
project the stored bankroll forward through elapsed regen via `project_bankroll`;
if `projected < amount`, **REFUSE** (return `None`) — never clamp-to-zero, which
created phantom chips. Commit any regen delta as a `record_ai_regen` creation,
write the reduced int, then — under the custody gate (`bankroll.py:522-535`) —
`record_ai_buy_in` writes the `ai → seat:ai:<sb>:<pid>` transfer.

**`credit_ai_cash_out`** (`bankroll.py:155`) — AI leaves (win/loss/bust).
Projects, credits `effective_stack` back to the int, records any regen, then —
under the gate (`bankroll.py:285-296`, requiring `from_seat=True`) —
`record_ai_cash_out` writes the `seat:ai:<sb>:<pid> → ai` transfer.

**The `from_seat` discriminator** (`bankroll.py:164`, default `True`):
`credit_ai_cash_out` is *overloaded* — it handles both real seat cash-outs AND
stake/carry payoffs (chips from a borrower or funding player, no seat). The
discriminator prevents double-counting: `from_seat=True` records `ai_cash_out`;
the payoff path records `stake_payoff` at the caller instead. (The handoff's "exactly
two helper functions" framing glossed this; the log corrected it.)

### First-write seed atomicity — `save_ai_bankroll`

`BankrollRepository.save_ai_bankroll` (`bankroll_repository.py:125`) is the upsert
chokepoint. On the *first-ever* `(personality_id, sandbox_id)` write, it UPSERTs
the bankroll row and — **on the same connection `c`** — calls `record_ai_seed`
with `conn=c` (`bankroll_repository.py:155-191`). The int and its
`central_bank → ai` seed entry commit atomically: no bankroll row without a seed,
no seed without a row. (This closed the `CASH_MODE_ECONOMY.md` Known-Issue where a
new sandbox created AI chips from thin air with no audit trail.)

### D2 read wiring

`BankrollRepository.chip_ledger_repo` is `None` by default
(`bankroll_repository.py:85`); `create_repos` sets it via
`bankroll_repo.chip_ledger_repo = chip_ledger_repo`
(`poker/repositories/__init__.py:62`). Both `load_ai_bankroll` and the player path
route through `_derived_or_cached_ai_chips` /
`_derived_or_cached_player_chips`, so they transparently switch to the ledger when
`CHIP_CUSTODY_DERIVE_READS` is on. The scope asymmetry is resolved in one place —
`derive_ai_balance` scopes per-sandbox, `derive_player_balance` sums across all
(`ledger.py:300-331`).

### The human side (always-on, no flag)

The human chip statement predates custody and has no flag guard
(`flask_app/routes/cash_routes.py`):

- **Sit-down** → `record_player_buy_in` writes `player:<id> → seat:<game_id>`
  (`cash_routes.py:1465`). Rebuy/top-up does the same (`cash_routes.py:558`).
- **Leave** → `record_player_cash_out` writes `seat:<game_id> → player:<id>`
  (`cash_routes.py:4802`). A bust (0 take-home) writes no row — the unpaired
  buy-in *is* the bust record.

## 5. Settle-before-delete (Phase 3)

The structural guarantee that a row deletion can never forfeit chips.

**`_settle_orphan_seat_to_bankroll`** (`cash_mode/lobby.py:4066`): a sit that
committed a `player_buy_in` but errored before its session row landed leaves chips
in `seat:<game_id>` with no cash-out. Deleting that row would strand them. So the
reaper, under the custody gate (`lobby.py:4094-4100`):

1. `balance_of(seat(game_id), sandbox_id=None)` — `game_id` is globally unique so
   `None` scope is safe (`lobby.py:4105`).
2. If `bal <= 0`, return 0.
3. Load the player bankroll. **If missing, log WARNING and return 0 — the balance
   is LEFT in the ledger, never forfeited** (`lobby.py:4108-4118`).
4. Credit the recovered chips to the bankroll and write a `player_cash_out`
   transfer, closing the seat balance.

**`_boot_sweep_stale_cash_rows`** (`cash_mode/lobby.py:4146`) calls this reaper
(`lobby.py:4289`) for each stale cash row, *after* skipping resumable sessions and
settling busted stakes, *before* deleting the `games` row. **Invariant:** a
non-empty `seat:` balance can leave only via a `player_cash_out` transfer; nothing
silently zeroes it.

**Persona deletion (Phase 5):** `settle_ai_bankroll_to_pool_on_delete`
(`bankroll.py:307`) runs *before* `delete_personality`
(`flask_app/routes/personality_routes.py:411`). For each `(pid, sandbox)` with
chips > 0 it records a `casino_seat_return` (ai → central_bank recyclable
destruction) and zeroes the row. Gated on `CHIP_CUSTODY_ENABLED`
(`bankroll.py:332`). Chip forfeiture on deletion is structurally impossible when
custody is on.

## 6. The conservation invariants

Computed by `flask_app/services/chip_ledger_audit.py:compute_audit`
(`chip_ledger_audit.py:30`), exposed at `/api/admin/chip-ledger/audit`.

**I1 — chip conservation:**

```
ledger_outstanding = Σ(creations) − Σ(destructions)              # ledger_audit.py:92
actual_outstanding = player_bankrolls
                   + ai_bankrolls_stored
                   + cash_table_seats_ai
                   + active_loans_principal
                   + live_session_ai_stacks
                   + live_session_human_stacks                    # ledger_audit.py:141-148
drift = ledger_outstanding − actual_outstanding                   # ledger_audit.py:220
```

Any non-zero drift means chips moved without a ledger entry. The audit also
reports `drift_reliable` (`ledger_audit.py:201`): false when a live-session stack
couldn't be read, so a transient read error doesn't masquerade as real drift.

**I2 — transfers are drift-invisible.** The seven `TRANSFER_REASONS` are excluded
from the creation/destruction sums; they move chips between surfaces both already
counted in `actual_outstanding`, so they cannot change drift.

**I3 — first-write atomicity.** The `ai_seed` row and the bankroll upsert commit
on one SQLite connection; a crash rolls back both together
(`bankroll_repository.py:155-191`).

**I4 — settle-before-delete.** A non-empty `seat:<game_id>` balance can only exit
via a `player_cash_out` transfer (`lobby.py:4066`).

**I5 — escrow nets to zero.** After payouts + rake a `tournament:<id>` escrow
balances to 0 (the escrow-balance invariant, `ledger.py:254-267`).

## 7. Authority today vs end-state

On dev (`CHIP_CUSTODY_ENABLED=1`, `CHIP_CUSTODY_DERIVE_READS=0`):

- **Write authority:** the bankroll int is written first, the ledger row appended
  immediately after. The int is the transaction-consistent hot-path read.
- **Ledger:** complete audit trail; reads default to the int to avoid the
  sub-millisecond stale window.
- **End-state (`CHIP_CUSTODY_DERIVE_READS=1`):** the ledger becomes the read
  authority and any divergence is logged. Deliberately deferred until a backfill
  audit confirms int==derived.

### What is *not* yet derived (Phase 4 deferred)

`cash_tables.seats[].chips` is the **live per-hand stack**; the ledger `seat:`
balance only moves at buy-in/cash-out. **They agree only at session boundaries.**
Per the design log, making `cash_tables.seats` a pure ledger-derived view mid-hand
would require ledgering per-hand P&L (hot-path SQLite cost) or formally accepting
that the live stack and the committed custody balance are *different facts*. The
foundation is treated as complete *at boundaries*; the live stack remains a
legitimate cache. (Log-attributed decision, not a code claim.)

## Related

- [`REPOSITORIES.md`](REPOSITORIES.md) — `ChipLedgerRepository` / `chip_ledger_entries`
  row, and the `bankroll_repo.chip_ledger_repo` wiring.
- [`CASH_MODE_ECONOMY.md`](CASH_MODE_ECONOMY.md) — the closed-loop behaviour
  (pool/vice/tourists) that sits on top of this ledger.
- `docs/plans/CASH_MODE_CHIP_CUSTODY_HANDOFF.md` — phase-by-phase spec + STATUS.
- `docs/captains-log/development/chip-custody-cutover.md` — design rationale
  narrative (the "why" woven in above is sourced here; treat as retrospective,
  not code-verified).

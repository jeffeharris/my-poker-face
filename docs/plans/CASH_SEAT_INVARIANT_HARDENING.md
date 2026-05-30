---
purpose: Design + phased plan to harden cash-mode seat-occupancy invariants (the ghost-seat bug class) via a SeatOccupancyRegistry and atomic chip/seat commits
type: design
created: 2026-05-29
last_updated: 2026-05-30
---

# Cash Seat-Invariant Hardening

## Why this exists

Cash mode has a recurring **ghost-seat / chip-conservation** bug class: an AI
gets seated at two tables at once, an AI/chips vanish, or chips are minted from
air. Multiple prior fixes addressed *instances*; this plan attacks the two
*structural* root causes so the class stops recurring.

Two root issues:

1. **Scattered seat-occupancy invariant.** `seated_globally` ("who is seated
   somewhere right now") is a plain `Set[str]` created once in
   `refresh_unseated_tables` and threaded **by reference** through
   `refresh_table_roster` and `_process_global_greedy_fills`, which mutate it in
   place. Correctness depends on every code path remembering to `.add` on seat
   and `.discard` on vacate. Any missed mutation silently breaks the
   one-seat-per-AI invariant.
2. **Partial-commit windows.** Several flows vacate a seat or debit a bankroll
   *before* a dependent operation completes, so an exception leaves an AI/chips
   in an inconsistent state (worst case: minted chips).

## Status

- **Phase 0 (invariant/conservation harness): DONE** —
  `tests/test_cash_mode/test_seat_occupancy_invariants.py` (seeded, `simulation`-marked,
  LLM-free). Drives the real `refresh_unseated_tables` across 40 seeds × 6 ticks and asserts,
  after every tick: (A) no `personality_id` seated at >1 table, (B) exact integer chip
  conservation (`Σ seat-chips + Σ ai-bankroll-stored`). **Diagnostic result: invariants HOLD
  on current code (40/40 pass).** A probe confirmed it's non-vacuous (seated set churned in
  13/20 ticks, cash-outs +75…+8000).
  - **Scope caveat (important):** the harness deliberately disables the exact paths where the
    historical ghost-seat bugs lived, so a green result **narrows but does not close** the bug
    class. NOT exercised: (1) the live human-seated hand-boundary / cold-load seam
    (`live_seated_pids`), (2) the staking/aspiration credit path — **Window A runs only when
    `stake_repo` is wired, and the harness passes `stake_repo=None`, so the chip-minting window
    is untested**, (3) fish (closed pool-funded economy), (4) vice/side-hustle credit paths.
  - **Implication for rollout:** the `seated_globally` mutation discipline (Phase 1's concern)
    looks sound on the headless path → Phase 1 is *guard/hardening*, not a live-bug fix.
    The atomicity windows — especially **Window A (Phase 2)** — remain the real correctness
    risk and are NOT covered here; each needs its own targeted fail→pass reproduction test, and
    the harness should be EXTENDED to the live-seated/aspiration paths to truly close the class.
- **Phase 1 (SeatOccupancyRegistry): DONE** (`42070939`) — drop-in wrapper, log-and-continue
  on double-seat, zero collisions across the seeded sim. Behavior-preserving hardening.
- **Phase 2 (Window A — aspiration mint): DONE** (`eb68fce0`) — reproduced (debit failure by
  raise OR None return minted `principal`; create_stake failure lost staker chips); reordered
  financial-first, structural-last with staker refund. fail→pass tests, success path identical.
- **Phase 3 (Window B — seed mint): DONE** (`4b001417`) — reproduced (refused debit left an AI
  seated-but-unfunded → mint); reordered debit-first, drop-on-fail. Fixed an existing test that
  encoded the bug (unfunded fixtures → funded, mirroring production).
- **Phase 4 (Window C — offer_stake race/orphan): DONE** (`58a8d2d7`) — reproduced (player
  debited above the lock → stranded on a raced seat; create_stake orphan); moved all commits
  inside the sandbox lock after seat re-verify + create_stake rollback (un-seat + refund).
  fail→pass tests, success path identical.

All landed on `development`; each verified bit-/behavior-identical on its success path via the
Phase 0 harness staying 40/40 + a stash-proven fail→pass reproduction; full cash bucket green
(925). **Accepted residuals** (single-connection SQLite, no cross-repo txn): a rollback step
that itself fails is logged loudly and left to the chip-ledger audit; the cold-start
debit-ok/save_table-fail case (Window B) self-heals on the next hand-boundary live-fill.

---

## 1. Enumeration

### 1.1 `seated_globally` sites

**Construction (re-materialize from DB):**
- `cash_mode/lobby.py:282-286` — `ensure_lobby_seeded`: local `set()`, never exported (uniqueness within the seed loop only).
- `cash_mode/lobby.py:429-436` — `_global_seated_set(tables)`: scans all tables, returns a new `Set[str]`. Called from `refresh_unseated_tables` and `game_handler.py`.
- `cash_mode/lobby.py:922` — `refresh_unseated_tables`: `seated_globally = _global_seated_set(tables)`, then `|= live_seated_pids` at ~931.
- `flask_app/handlers/game_handler.py:1535-1536` — hand-boundary refresh: `_global_seated_set([...all tables except current...])` then `.update(synced_seats)`.

**Mutations:**
| Site | File:line | Op | Notes |
|---|---|---|---|
| greedy fill | `lobby.py:786` | `.add` | after debit + seat write — **mutates the object created at lobby.py:922 (by-ref)** |
| roster vacate | `movement.py:1327` | `.discard` | Step-1 vacate path (all decisions except stay/rebuy/take_stake) |
| roster fill | `movement.py:1469` | `.add` | live-fill loop — **mutates caller's set (by-ref)** |
| take_stake | `movement.py:1257-1258` | *(none)* | **intentional** no-discard: AI stays seated, refilled with principal |

**Reads (membership):** `lobby.py:352, 684, 690`; `movement.py:1398, 1426, 1450`.

**Core fragility:** one `Set` object flows `lobby.py:922 → refresh_table_roster (mutates) → _process_global_greedy_fills (mutates)`. Works only while every vacate `.discard`s and every fill `.add`s. The `take_stake` no-discard is correct-but-undocumented-in-code (only a comment).

### 1.2 Partial-commit windows

**Window A — `_process_aspiration_asks` (`lobby.py:~4266-4303`)** *(highest risk — can MINT chips)*
Current order: (1) seat vacated in memory → (2) chip-return `BankrollChange` queued → (3) staker debited inline → (4) `create_stake`.
If (3) raises (caught + `continue`), (1) and (2) already happened: the asker is credited `seat_chips + principal` but **no one was debited the `principal`** → principal chips minted; stake row never written.

**Window B — `ensure_lobby_seeded` (`lobby.py:~348-416`)** *(cold-start only)*
Per-AI `debit_bankroll_for_seat` (391) then `seated_globally.add` (377); `save_table` (416) after the loop. If `save_table` raises, multiple AIs are debited but no table row exists → chips drained without seats; next boot may re-seed under-funded.

**Window C — `offer_stake_to_ai` (`cash_routes.py:~3854-3950`)** *(human-facing route)*
Order: player debit (3864) → optional AI fee credit → optional AI match debit → `save_table` under sandbox lock (3927) → `create_stake` (3933). If `save_table` fails (seat raced) the player is already debited (chips gone, no seat); if `create_stake` fails after `save_table`, the AI is seated with no backing record → settlement later credits the AI the full amount, player gets nothing. Code comment at ~3858 already acknowledges the non-atomicity.

---

## 2. Proposed `SeatOccupancyRegistry`

New file `cash_mode/seat_registry.py`. A thin, audited, **per-refresh** wrapper
(not a global/singleton) that replaces the raw `Set[str]` and makes illegal
mutations loud.

```python
class SeatOccupancyRegistry:
    def __init__(self, initial: set[str], *, label: str = ""): ...
    def seat(self, pid: str) -> None:        # replaces .add; logs error + no-ops on double-seat
    def vacate(self, pid: str) -> None:      # replaces .discard (no-op when absent)
    def vacate_or_retain(self, pid, *, retain_reason): ...  # explicit no-op for take_stake
    def contains(self, pid: str) -> bool: ...
    def snapshot(self) -> frozenset: ...
    def add_without_collision_check(self, pids: set[str]) -> None:  # live_seated union (cold-load overlap is expected)
    @property
    def collision_count(self) -> int: ...    # tests assert == 0
    # transition compat:
    def __contains__(self, pid) -> bool: ...
    def __ior__(self, other) -> "SeatOccupancyRegistry": ...  # routes |= to add_without_collision_check
```

Design intent: `.seat()` makes a forgotten/duplicate add **loud** (logged,
counted) rather than silent; `take_stake`'s deliberate non-discard becomes an
explicit, grep-able, logged `vacate_or_retain(...)` instead of relying on a
comment. Start with **log-and-continue** in production (don't raise), so the
first deployment is a diagnostic pass.

Call-site changes: wrap at construction in `refresh_unseated_tables` (922) and
`game_handler.py` (1535); `.add→.seat` at `lobby.py:786` and `movement.py:1469`;
`.discard→.vacate` at `movement.py:1327`; add `vacate_or_retain` at the
take_stake branch. `_global_seated_set` stays a plain helper (wrapped by callers).

---

## 3. Atomicity fixes (correct failure modes; chips conserved)

Constraint: SQLite, single-connection repos, no cross-repo transactions. Goal =
**correct failure modes** (no minting/loss, audit-detectable), via op-reordering
(financial ops before structural) + manual rollback on the last step.

- **Window A (`_process_aspiration_asks`):** reorder → (1) debit staker → (2)
  `create_stake` (on failure, credit staker back) → (3) vacate seat + queue
  chip-return. Once both financial ops commit, the seat vacate is a pure
  in-memory mutation with no independent failure.
- **Window B (`ensure_lobby_seeded`):** build the full `CashTableState` in
  memory, debit each AI (dropping any whose debit fails from the seats), then
  `save_table` once. Residual debit-ok/save-fail is cold-start only and
  audit-detectable; boot re-seed is idempotent (`if existing is not None: continue`).
- **Window C (`offer_stake_to_ai`):** move the player debit **inside** the
  sandbox lock after re-verifying the seat is open (closes the race), and wrap
  `create_stake` with rollback (un-seat + refund) on failure.

---

## 4. Phased rollout (each phase gated by the Phase 0 harness)

- **Phase 0 — invariant tests first (no prod change).** Seeded movement/refresh
  sim asserting no-double-seat + chip-conservation after each tick; plus a test
  reproducing Window A's mint-on-debit-failure. *(In progress.)*
- **Phase 1 — `SeatOccupancyRegistry`** (logging-only; behavior-preserving).
  Gate: Phase 0 + existing cash buckets stay green; `collision_count == 0` in the sim.
- **Phase 2 — Window A atomicity** (the chip-minting fix). Gate: the Window-A
  test flips fail→pass; conservation audit before/after.
- **Phase 3 — Window B** (cold-start seed ordering). Gate: save_table-failure test.
- **Phase 4 — Window C** (`offer_stake_to_ai` lock + rollback). Gate:
  `test_cash_sponsor_routes` + a create_stake-failure rollback test.

**Must NOT change:** the `take_stake` no-discard (intentional); the
`human_headroom` seat-reservation logic; the `defer_freshly_vacated_live_fill`
path; `_global_seated_set` as a plain helper.

---

## 5. Test / validation strategy

- `tests/test_cash_mode/test_seat_occupancy_invariants.py` — seeded sim (e.g.
  25–50 seeds × N ticks), in-process repos, no LLM; after each tick assert no
  `personality_id` in >1 AI seat and `seat_chips + bankroll_chips (+ pool) ==
  initial_total` within tolerance. Mark `simulation`.
- `tests/test_cash_mode/test_seat_registry.py` — unit tests for the wrapper.
- `tests/test_cash_mode/test_aspiration_atomicity.py` — mock the staker debit to
  raise; assert seat NOT vacated, no `BankrollChange` queued, staker unchanged.
- `tests/test_cash_mode/test_offer_stake_rollback.py` — mock `create_stake` to
  raise; assert player refunded + seat reverted to open.
- Run the existing chip-ledger conservation audit on the dev DB before/after
  Phases 2–4.

---

## 6. Risk

- **High (real chip flows):** Window A reorder (was capable of minting),
  Window C debit-inside-lock + rollback.
- **Medium:** registry replacing the raw set may *log* previously-silent
  collisions on first deploy (desired, but surprising) — treat Phase 1's first
  deployment as a diagnostic pass before Phase 2.
- **Low:** new `seat_registry.py` (additive); `vacate_or_retain` (cosmetic/logging).

**Out of scope (different bug classes):** cold-load `cash_table_id` binding
(fixed in `d860af0c`), the `vice_spending` leak, out-of-process settlement row
resurrection (session-lifecycle hardening), and two-request same-sandbox races
on the ticker path (no lock; this plan makes failures louder, not impossible).

---

## Key references
- `cash_mode/lobby.py` — `_global_seated_set:429`, `refresh_unseated_tables:809/922`, greedy `.add:786`, `_process_aspiration_asks:~3957/4266`, `ensure_lobby_seeded:~224/348`
- `cash_mode/movement.py` — `refresh_table_roster`, vacate `.discard:1327`, fill `.add:1469`, take_stake no-discard `:1257`
- `flask_app/handlers/game_handler.py:1535-1536` — hand-boundary refresh
- `flask_app/routes/cash_routes.py:~3854-3950` — `offer_stake_to_ai` (Window C)
- `tests/test_cash_mode/test_cash_table_idle_invariant.py` — existing chokepoint-invariant pattern to mirror

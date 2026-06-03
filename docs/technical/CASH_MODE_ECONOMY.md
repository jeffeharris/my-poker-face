---
purpose: Technical reference for the cash-mode chip economy as implemented — pools, flow paths, conservation invariant, audit, and tuning levers.
type: reference
created: 2026-05-19
last_updated: 2026-06-03
---

# Cash Mode Economy

This is the consolidated reference for **how chips flow in cash mode**.
Every chip-moving code path is documented here with its source pool,
sink pool, and (for creation/destruction events) which ledger reason
fires. If you want to know "where do these chips come from?" or "what
happens to those chips on event X?" this is the doc.

The economy was built incrementally across 8 implementation handoffs
in `docs/plans/`; this doc captures the system **as built**, not as
originally specified. Discrepancies between this doc and any
individual handoff should be resolved in favor of this doc + the
code (handoffs were forward-looking).

This doc is the **accounting** layer (where chips come from / go, the
conservation invariant, the audit). For the **policy** layer — the
wealth levers (vice / side-hustle / grinder-hunger / rake) and the
`own_start` vs `field_liquid` reference model that decides who gains and
loses over time — see `CASH_MODE_WEALTH_LEVERS.md`.

## The conservation invariant

```
central_bank.reserves + Σ player_bankrolls + Σ ai_bankrolls
    + Σ cash_table_seats + Σ live_session_stacks
    + Σ active_loan_principals
    = constant (the universe)
```

There is no explicit `central_bank` row — the bank is a conceptual
source/sink referenced in ledger entries. Its "reserves" are derived:

```
central_bank.reserves
    = Σ ledger.destructions  -  Σ ledger.creations  +  initial_universe_seed
```

where `initial_universe_seed` is the one-shot `pre_ledger_universe`
ledger entry written at schema v94 migration time. Every chip
**creation** (universe inflates) is a `central_bank → X` ledger entry.
Every chip **destruction** (universe deflates) is `X → central_bank`.

A **pure transfer** (entity-to-entity, universe unchanged) leaves
`actual_outstanding` unchanged regardless of whether it is ledgered.
Historically these wrote NO ledger entry. **As of the chip-custody
cutover (`CHIP_CUSTODY_ENABLED`, on dev), AI seat buy-in / cash-out
transfers ARE written** — as `TRANSFER_REASONS` entries
(`core/economy/ledger.py:164`), which are invisible to the
creation/destruction drift math but make the AI's at-table chips a
*derivable* ledger balance. The full custody substrate — schema, account
vocabulary, the two cutover flags, settle-before-delete, and the conservation
invariants — is documented in [`CHIP_CUSTODY_LEDGER.md`](CHIP_CUSTODY_LEDGER.md)
(the authoritative ledger doc); see also [Chip custody](#chip-custody-ledger-as-the-chip-authority)
below. Transfers that are still not ledgered (player sit-down, pot
redistribution) remain in the table at [Pure transfers](#pure-transfers-universe-unchanged).

The audit endpoint (`/api/admin/chip-ledger/audit`) computes the
invariant in both directions and reports `drift = ledger_outstanding -
actual_outstanding`. **`drift == 0` is the correctness signal.** Non-
zero drift means some chip movement bypassed the ledger.

## Chip pools

| Pool | Live representation | Persistence |
|---|---|---|
| **Player bankroll** | `PlayerBankrollState.chips` per `owner_id` | `player_bankroll_state` table |
| **AI bankroll (stored)** | `AIBankrollState.chips`, as of `last_regen_tick` | `ai_bankroll_state` table |
| **AI bankroll (projected)** | `project_bankroll(stored, starting_bankroll, bankroll_rate, now)` — regens *toward* `starting_bankroll`, no ceiling; returns chips verbatim when `REGEN_ENABLED` is False (`bankroll.py:111-152`) | virtual — computed on read, persisted on write events |
| **Cash table seats (AI)** | `seats_json[i].chips` for each `kind: 'ai'` slot | `cash_tables.seats_json` |
| **Live session AI stacks** | `state_machine.game_state.players[i].stack` for non-human players in active games | in-memory only, lives during a player's session |
| **Active loan principals** | active stake principal summed from `StakeRepository` (restricted to *human* borrowers in the audit). The legacy `player_bankroll_state.active_loan_*` columns were dropped in schema v99 (`chip_ledger_audit.py:57`) | DB |
| **Idle pool** | `cash_idle_pool` table — AIs not currently seated; their chips still live in `ai_bankroll_state` | DB |

All five "pool" rows above contribute to `actual_outstanding` in the
audit. **`uncommitted_ai_regen`** (the diff between projected and
stored AI bankrolls) is subtracted from the total because regen
hasn't yet been ledgered.

### Pool sizing (HISTORICAL snapshot — 2026-05-19)

> **Dated.** These figures are a point-in-time snapshot from before the
> chip-custody cutover, the side-hustle faucet, casino/tourist seeding,
> and the tournament economy all landed. Treat the table as illustrative
> of pool *shape*, not current magnitudes. For live numbers run the audit
> (`GET /api/admin/chip-ledger/audit`). The `% of universe` split in
> particular no longer holds.

For context, after the lobby-seed leak fix landed:

| Pool | Approx chips | % of universe |
|---|---|---|
| AI bankrolls projected | ~642k | 72% |
| Cash table seats (AI) | ~187k | 21% |
| Player bankroll (solo dev) | ~81k | 9% |
| Active loans | 0 | 0% |
| Live session stacks | 0-30k | 0-3% (transient) |

Universe total: ~900k chips. The 53 personalities × median cap of
10k = ~530k baseline AI capacity, but the high-cap tail (Bezos at
250k, Buffett at 200k) lifts the projected total well above that.

## Flow paths

### Sources (chips enter the universe)

Creation reasons live in `core/economy/ledger.py:LEDGER_REASONS`
(lines 34-57). Each is a `central_bank → X` or `bank_pool → X` entry.

| Reason | Trigger | Writer |
|---|---|---|
| `pre_ledger_universe` | One-shot at schema v94 migration | `_migrate_v94_chip_ledger_pre_ledger_universe` in `schema_manager.py` |
| `player_seed` | First-time player entry into cash mode (`load_player_bankroll` returns None) | `_load_or_seed_player_bankroll` in `flask_app/routes/cash_routes.py` |
| `ai_seed` | First `ai_bankroll_state` write per sandbox (closes old Known Issue §2) | `record_ai_seed` (`ledger.py:872`), fired from `bankroll_repository.py:184` and `lobby.py:396` |
| `ai_regen` | A `save_ai_bankroll` where committed regen > previously stored | `credit_ai_cash_out` (`bankroll.py:270`) AND `debit_bankroll_for_seat` (`bankroll.py:477`) — both commit pending regen when a ledger repo is present. **Off by default**: `REGEN_ENABLED = False` (`economy_flags.py:74`), so this fires ~never; the side hustle is the live faucet |
| `side_hustle_earning` | The active faucet: bank pool → broke AI bankroll (replaces passive regen) | per `docs/plans/CASH_MODE_SIDE_HUSTLE.md`; `SIDE_HUSTLE_ENABLED = True` (`economy_flags.py:78`) |
| `tourist_injection` | Closed-economy refill: bank pool → fish bankroll | `ledger.py:42` |
| `casino_seat_seed` | Bank pool → fish seat chips at casino spawn | `ledger.py:48` |
| `tournament_overlay` | Bank pool → `tournament:<id>` escrow (house overlay; a pool DRAW) | `record_tournament_overlay` (`ledger.py:748`) |
| `bank_pool_sim_seed` | Sim-only: `central_bank → synthetic donor` to inflate the pool | `ledger.py:60` |
| `house_stake_issue` | Borrower accepts a house-archetype stake offer | `sponsor_and_sit` route in `cash_routes.py` |

> **Faucet shift.** Passive `ai_regen` was retired in favour of the
> active side hustle (`REGEN_ENABLED = False`, `SIDE_HUSTLE_ENABLED =
> True`). `project_bankroll` returns `state.chips` unchanged when regen
> is off (`bankroll.py:148`). See `CASH_MODE_WEALTH_LEVERS.md` for the
> policy rationale (who gains/loses over time).

> **Resolved:** the old "initial AI bankroll seeding isn't ledgered"
> leak (former Known Issue §2) is closed by the `ai_seed` reason above.

### Sinks (chips leave the universe)

Most sinks recycle into the bank pool rather than destroying chips
outright (`BANK_POOL_DEPOSIT_REASONS`, `ledger.py:190`) — they fund the
faucets above (closed economy). True `central_bank` destructions are
rare.

| Reason | Trigger | Writer |
|---|---|---|
| `table_rake` | Per-hand pot skim (sim + live) → bank pool; recyclable | `record_table_rake` (`ledger.py:968`); re-sourced to the seat account under chip custody |
| `vice_spending` | Real AI voluntary spend-down → bank pool (`VICE_MODE='real'`) | `ledger.py:83` |
| `bank_pool_deposit` | Stub vice / operator-driven deposit → bank pool | `ledger.py:79` |
| `casino_seat_return` | AI bankroll → bank pool at casino teardown (mirror of `casino_seat_seed`) | `ledger.py:96` |
| `tournament_return` | `tournament:<id>` escrow → bank pool (mirrors `tournament_overlay`) | `record_tournament_return` (`ledger.py:779`) |
| `informant_unlock` | Player → bank pool; chips spent buying a dossier | `record_informant_unlock` (`ledger.py:845`) |
| `house_stake_settle` | Leave-time settlement of an active house-archetype stake; floor repayment + staker cut go to the bank | `leave_table` in `cash_routes.py` → `settle_loan_on_leave` in `cash_mode/loan_settlement.py` |
| `forgive_balance` | Annotation only (amount=0). Recorded when a borrower leaves with chips < stake floor and the remaining house-stake principal is forgiven | `settle_loan_on_leave` |
| `cap_clamp` | **DEPRECATED — historical entries only.** Was bankroll overflow above `bankroll_cap`. The destruction path was removed when `starting_bankroll` semantics shifted from ceiling to target — winnings are kept, not evaporated (`bankroll.py:196-198`; `ledger.py:66-71`) | (no longer emitted) |

### Pure transfers (universe unchanged)

These move chips between two non-bank entities. Both sides are counted
in `actual_outstanding`, so the creation/destruction math balances
regardless of ledger involvement. The **AI seat** transfers are now
ledgered as `TRANSFER_REASONS` under chip custody (marked below); the
rest still write no entry.

| Movement | Mechanism | File | Ledgered? |
|---|---|---|---|
| Player bankroll → table stack (sit-down) | `_build_cash_game` debits bankroll | `cash_routes.py` | no |
| Player bankroll ↔ table stack (top-up) | `top_up` route | `cash_routes.py` | no |
| Table stack → player bankroll (leave) | `leave_table` after loan settlement | `cash_routes.py` | no |
| AI bankroll → seat chips (live-fill / buy-in) | `debit_bankroll_for_seat` (`bankroll.py:514`) | called from `refresh_unseated_tables` / `ensure_lobby_seeded` in `cash_mode/lobby.py` | `ai_buy_in` (custody) |
| Seat chips → AI bankroll (movement vacate) | `credit_ai_cash_out` (`bankroll.py:290`) | called from `refresh_unseated_tables` | `ai_cash_out` (custody) |
| AI lender bankroll → player table stack (personality sponsor loan) | `sponsor_and_sit` route's personality-loan branch | `cash_routes.py` | no |
| Player table stack → AI lender bankroll (loan settle, personality loan) | `settle_loan_on_leave` personality-loan branch | `loan_settlement.py` | no |
| Sim/in-game pot redistribution between AI seats | Inside `play_one_hand` (full sim) or `roll_fake_hand` (deprecated by full sim but still in tree) | `cash_mode/full_sim.py`, `cash_mode/fake_sim.py` | no |
| In-game player pot ↔ AI pot | Standard hand engine (`poker/poker_state_machine.py`) | not cash-mode-specific | no |

### The credit / debit chokepoints

`credit_ai_cash_out` and `debit_bankroll_for_seat` (`cash_mode/bankroll.py`)
are the two bankroll write surfaces. Both:

1. **Move the bankroll int** — credit raises it by `seat_chips`, debit
   lowers it by the buy-in. This is the pure-transfer half; both sides
   are counted in the audit so the universe is unchanged.
2. **Commit pending regen** when a ledger repo is present, firing an
   `ai_regen` entry (`bankroll.py:270`, `:477`). Since `REGEN_ENABLED`
   is False, committed regen is ~always zero in practice.
3. **Record the seat transfer under chip custody** — debit fires
   `ai_buy_in` (`bankroll.py:529`), credit fires `ai_cash_out`
   (`bankroll.py:290`). Both gate on `CHIP_CUSTODY_ENABLED`, a non-None
   `sandbox_id`, and a present ledger repo.

`credit_ai_cash_out` carries a `from_seat` discriminator
(`bankroll.py:164`): a real seat cash-out (`from_seat=True`) records
`ai_cash_out`, while a staker-funded payoff with no seat behind it
records `stake_payoff` instead (`record_stake_payoff`, `ledger.py:643`).
The legacy third behaviour — destroying overflow above a hard
`bankroll_cap` via `cap_clamp` — was **removed** when the knob became a
regen target rather than a ceiling (`bankroll.py:196-198`).

## Chip custody: ledger as the chip authority

The **chip-custody cutover** (flag `CHIP_CUSTODY_ENABLED`,
`economy_flags.py:253`; committed default `False`, set to `1` in dev
`.env`) makes the ledger the authority for **AI** chip ownership: an
AI's at-table chips become a *derivable* ledger balance keyed to a seat
account `seat:ai:<sandbox>:<pid>`, exactly as a human's table stack is.
This closes the "bankroll is an unauditable mutable integer" gap and
makes the chip-forfeiture bug class auditable.

This section is a **summary + pointer**. The full mechanics — the seat
account model, the D2 derived-bankroll reads, seats-as-view (Phase 4,
deferred), and the deletion-integrity work — are NOT duplicated here.
Canonical sources:

- **Mechanics + phased plan:** `docs/plans/CASH_MODE_CHIP_CUSTODY_HANDOFF.md`
  and `docs/plans/CASH_MODE_CHIP_CUSTODY_SCOPE.md`.
- **Reason vocabulary:** `core/economy/ledger.py` (`TRANSFER_REASONS`,
  `record_ai_buy_in`, `record_ai_cash_out`, `record_ai_seed`).
- **Design rationale:** `docs/captains-log/development/chip-custody-cutover.md`.

> The authoritative custody reference is
> [`CHIP_CUSTODY_LEDGER.md`](CHIP_CUSTODY_LEDGER.md) (the append-only event
> log, the account vocabulary, the two cutover flags, and the conservation
> invariants). The plan/scope docs above carry the phased mechanics.

What it changes for *this* doc's accounting:

| Aspect | Pre-custody | Under `CHIP_CUSTODY_ENABLED` |
|---|---|---|
| AI buy-in (`debit_bankroll_for_seat`) | pure transfer, no entry | writes `ai_buy_in` transfer (`bankroll.py:529`) |
| AI vacate (`credit_ai_cash_out`) | pure transfer, no entry | writes `ai_cash_out` transfer (`bankroll.py:290`) |
| First AI bankroll row per sandbox | unledgered creation (old Known Issue §2) | writes `ai_seed` creation (`record_ai_seed`, `ledger.py:872`; fired from `bankroll_repository.py:184`, `lobby.py:396`) |
| Staker payoff via `credit_ai_cash_out` | folded into the cash-out | split out as `stake_payoff` via the `from_seat` discriminator |
| AI bankroll value | the stored int | int stays the hot-path cache; `balance_of` / `derive_ai_balance` can derive it from ledger parcels (read gated behind `CHIP_CUSTODY_DERIVE_READS`, default OFF) |

**Design rationale** (from `chip-custody-cutover.md`, a point-in-time log —
code wins on any conflict):

- The `from_seat` discriminator exists because `credit_ai_cash_out` was
  *already* overloaded for stake/carry payoffs that have no seat behind
  them; routing those as `stake_payoff` keeps seat transfers clean.
- The seat identity `seat:ai:<sandbox>:<pid>` was chosen because the two
  bankroll chokepoints already knew those keys and **one AI = one seat**
  (single presence).
- **Phase 4 (seats-as-view) was deferred:** `cash_tables.seats` holds the
  live per-hand stack while the ledger `seat:` balance only moves at
  buy-in / cash-out — they agree at *session boundaries*, not mid-hand.
  So the live seat stack, not the ledger seat balance, remains the
  mid-hand source of truth.
- The ~32.6M "gap" that motivated the work was *mostly cancelling
  per-account noise* (winners offsetting losers), not missing chips; the
  backfill (`scripts/backfill_chip_custody.py`) reconciled dev to
  LEDGER COMPLETE.

> **Conservation note (why this matters operationally):** because
> `debit_bankroll_for_seat` lowers the bankroll, any script that
> overwrites a seat row without crediting the displaced AIs *loses* their
> chips. Treat every AI-seat teardown as a chip-conservation operation —
> credit the bankroll back (this is the lesson behind the cold-load
> seat-orphan self-heal; see
> `docs/captains-log/development/cash-coldload-seat-orphan.md`).

## Audit semantics

The audit's response shape (`GET /api/admin/chip-ledger/audit`,
admin-only):

```jsonc
{
  "ledger_totals": {
    "chips_created":   Σ amount where source = 'central_bank',
    "chips_destroyed": Σ amount where sink   = 'central_bank',
    "outstanding":     chips_created - chips_destroyed
  },
  "actual_totals": {
    "player_bankrolls":      Σ player_bankroll_state.chips,
    "ai_bankrolls_stored":   Σ ai_bankroll_state.chips,
    "ai_bankrolls_projected": Σ project_bankroll(state, now),
    "uncommitted_ai_regen":  ai_bankrolls_projected - ai_bankrolls_stored,
    "cash_table_seats_ai":   Σ seats_json[i].chips where kind == 'ai',
    "active_loans_principal": Σ active stake principal (human borrowers) from StakeRepository,
    "live_session_ai_stacks": Σ Player.stack for non-human players
                              in game_state_service games,
    "actual_outstanding":    ai_bankrolls_projected
                             + cash_table_seats_ai
                             + player_bankrolls
                             + active_loans_principal
                             + live_session_ai_stacks
                             - uncommitted_ai_regen
  },
  "drift": ledger.outstanding - actual.actual_outstanding,
  "by_reason": { reason: Σ_signed_amount, ... },
  "by_reason_window_24h": { ... }
}
```

`drift == 0` is correctness. Non-zero drift means a chip movement
bypassed the ledger.

> **Dated drift figure.** The "~1M pre-fix drift" reported here in May
> 2026 was **reconciled by the chip-custody backfill** (2026-06-01):
> `scripts/backfill_chip_custody.py` brought dev to **LEDGER COMPLETE —
> AI 1239/1239 + Player 4/4** (was 339/1239), per
> `docs/captains-log/development/chip-custody-cutover.md`. The captain's
> log notes that the apparent 32.6M gap was *mostly cancelling
> per-account noise* (winners offsetting losers), not genuinely missing
> chips. Do not trust the ~1M number; run the audit for the current
> value.

## Tuning levers

Every knob that affects the economy and where it lives:

### Stakes ladder (`cash_mode/stakes.py`)

```python
STAKES_LADDER = {"$2": {"big_blind": 2}, "$10": {...}, ...}
MIN_BUY_IN_BB = 40
MAX_BUY_IN_BB = 100
```

`STAKES_ORDER` is derived from `STAKES_LADDER.keys()` — adding a new
stake is one edit. Frontend `STAKES` literal-union in
`react/.../cash/types.ts` must be edited in lockstep.

### AI bankroll knobs (`personalities.json`, per personality)

Each personality has `bankroll_knobs`:

```jsonc
{
  "starting_bankroll": 4000-250000,    // regen TARGET, not a ceiling (see below)
  "bankroll_rate": 100-3500,           // chips/day regen (dormant — REGEN_ENABLED=False)
  "buy_in_multiplier": 1.0-1.5,        // multiplies table min_buy_in to get this AI's buy-in
  "stop_loss_buy_ins": 2-5,            // v2 (unused in v1.5)
  "stop_win_buy_ins": 3-10,            // v2 (unused in v1.5)
  "stake_comfort_zone": "$2"-"$1000"   // preferred stake; selection priority
}
```

> **Knob renamed + semantics flipped.** The field is now
> `starting_bankroll` (`BankrollKnobs`, `bankroll.py:92`), **not**
> `bankroll_cap`, and it is a regen *target*, not a hard ceiling: when
> chips are at or above it, regen is dormant and chips are returned
> verbatim; **winnings above it are kept** (`bankroll.py:121-126`). The
> legacy `bankroll_cap` JSON key is still accepted as a read alias
> (`bankroll_repository.py:655-660`). This is why `cap_clamp` was retired
> — there is no ceiling to clamp against.

Editable via admin UI (Path A added the route + panel:
`/api/personality/<name>/bankroll-knobs`).

Defaults (when `bankroll_knobs` is absent) are in
`cash_mode/bankroll.py:BANKROLL_KNOB_DEFAULTS` (line 101):
`starting_bankroll=10_000`, `rate=500`, `multiplier=1.0`,
`comfort_zone="$10"`.

### Movement pressure model (`cash_mode/movement.py`)

The old per-decision probability constants (`DEFAULT_STAKE_UP_PROB`,
`DEFAULT_TAKE_BREAK_PROB`, `DEFAULT_BORED_MOVE_PROB`) **no longer exist**.
Movement was redesigned into a continuous *pressure-accumulation* model:
each AI's per-hand leave probability is `pressure / (pressure + LEAVE_K)`
(`movement.py:55`, `:360`), where `pressure` is a weighted sum of signals
rather than a binary "won big / lost big" gate.

The weights (`movement.py:57-87`):

```python
W_STAKE_UP = 0.5        # stack ≥ max_buy_in → eager to book the win
W_SHORT = 0.6           # stack < min_buy_in → tilt walk or rebuy
W_DETACHED = 0.3        # hands spent folding too much ('detached' zone)
W_TENURE = 0.2          # tired (low energy)
W_SLUM = 0.01           # wealth-over-tier "I'm slumming, move up" signal
W_DEAD = 0.4            # table deadness (0 juicy → 1 all-shark)
LEAVE_K = 2.0           # curve shape: at pressure=1.0, leave prob ≈ 0.33
FORCED_LEAVE_RATIO = 0.3  # stack ≤ this × min_buy_in → forced_leave (the
                          # only surviving hard threshold)
```

The one surviving fill constant: `DEFAULT_LIVE_FILL_PROB = 0.05`
(`movement.py:111`) — chance an open seat fills per tick (was `0.15`).
Entry point: `evaluate_ai_movement`.

### Sim cadence (`cash_mode/full_sim.py`)

```python
DEFAULT_HAND_SIM_PROB = 0.25         # baseline per-table per-tick chance of one sim hand
DEFAULT_BURST_THRESHOLD_SECONDS = 30 # below this, single hand; above, burst
DEFAULT_BURST_PACING_SECONDS = 20    # one hand per N seconds of gap
DEFAULT_BURST_HAND_CAP = 30          # max hands per table per refresh
DEFAULT_BIG_EVENT_THRESHOLD_BB = 8   # pot ≥ N BB → big_win/big_loss event
```

### Player starting bankroll (`flask_app/routes/cash_routes.py`)

```python
DEFAULT_PLAYER_STARTING_BANKROLL = 200
```

Tight by design — gets the player into the sponsor flow quickly.

### Sponsor archetype pool (`cash_mode/sponsor_offers.py`)

Six archetypes parameterized by table buy-in window:
- Friendly Boost: `min_buy_in`, floor 1.00, rate 0.20
- Square Deal: `min × 1.5`, floor 1.10, rate 0.25
- The Premium: `max × 0.5`, floor 1.30, rate 0.00
- Skin in the Game: `max × 0.7`, floor 1.15, rate 0.15
- Whale Backer: `max`, floor 1.00, rate 0.50
- Loan Shark: `max × 0.8`, floor 1.30, rate 0.40

### Personality lender profile (`personalities.json`, per personality — Path B)

```jsonc
{
  "staker_profile": {
    "willing": true,
    "max_loan_pct_of_bankroll": 0.05-0.15,
    "floor_anchor": 1.00-1.40,
    "rate_anchor": 0.15-0.45,
    "respect_floor": -0.5,
    "heat_ceiling": 0.7
  }
}
```

### Sponsor eligibility

Implemented in `cash_mode/stakes.py:is_sponsor_eligible`:
- `bankroll < this tier's min_buy_in`
- AND (`tier is lowest` OR `bankroll >= prev tier's min_buy_in`)

Strict "one tier above self-affordable" gate.

## Observability

### Ledger reasons (`core/economy/ledger.py`)

The reason vocabulary has three frozensets, all in
`core/economy/ledger.py`:

| Set | Purpose | Counts toward drift? |
|---|---|---|
| `LEDGER_REASONS` (line 34) | Creation/destruction events — inflate or deflate the universe | yes |
| `TRANSFER_REASONS` (line 164) | Pure entity↔entity moves — `ai_buy_in`, `ai_cash_out`, `stake_payoff` | no (universe unchanged) |
| `BANK_POOL_DEPOSIT_REASONS` / `BANK_POOL_DRAW_REASONS` (lines 190, 204) | Tag which destructions recycle into the bank pool vs. which creations draw from it (closed economy) | — |

The Sources and Sinks tables above enumerate the live members. Adding a
new reason requires editing the relevant frozenset first — unknown
reasons are rejected at write time (defensive). `cap_clamp` remains a
member of `LEDGER_REASONS` but is **DEPRECATED — historical entries
only** (`ledger.py:66-71`).

### Convenience constructors

`core.economy.ledger.bank()`, `player(owner_id)`, `ai(personality_id)`
format source/sink strings canonically. Use these instead of f-string
formatting — they validate inputs (non-empty owner/pid).

### Where to find the audit

- API: `GET /api/admin/chip-ledger/audit` (admin only).
- Service: `flask_app/services/chip_ledger_audit.py:compute_audit`.
- Admin UI: "Chip economy" panel in the admin dashboard
  (`react/.../admin/ChipLedgerPanel.tsx`).

## Known issues / asymmetries

### 1. Historical drift (~1M chips) — RESOLVED

> **Resolved 2026-06-01.** This item described a ~-1M drift from the
> pre-instrumentation lobby-seed leak (ledger ~63k vs actual ~1M as of
> May 2026). The chip-custody backfill reconciled it: dev now audits
> **LEDGER COMPLETE — AI 1239/1239 + Player 4/4**
> (`scripts/backfill_chip_custody.py`;
> `docs/captains-log/development/chip-custody-cutover.md`). The headline
> "32.6M gap" was largely cancelling per-account noise, not missing
> chips. Run the audit for the current value; do not cite the ~1M figure.

### 2. New AI bankroll rows aren't ledgered — RESOLVED

> **Resolved.** Closed by the `ai_seed` creation reason
> (`record_ai_seed`, `ledger.py:872`, fired from
> `bankroll_repository.py:184` and `lobby.py:396`) — the first
> `ai_bankroll_state` write per sandbox now fires a creation entry. See
> [Chip custody](#chip-custody-ledger-as-the-chip-authority).

### 3. Player has no cap

AI bankrolls regen toward `starting_bankroll` (max 250k across
personalities) but have **no hard ceiling** (winnings above target are
kept). Player bankroll likewise has no cap. A consistently-winning
player accumulates chips without bound, eventually outscaling every AI
in the cast.

Since `cap_clamp` was retired, chips leaving the AI pool are no longer
destroyed — they recycle to the bank pool (rake / vice) and re-enter via
the faucets, or land in the player pool with no symmetric sink. The
player-side inflation gap remains structural.

Fix path: **endgame economy chip sinks** (see Part 3 of
`CASH_MODE_AND_RELATIONSHIPS.md` — staking busted AIs, character
unlocks, private home games). Not yet built.

### 4. AI bankroll regen committed only at write events

Regen accrues virtually (read via `project_bankroll`); it becomes a
real ledger entry only when a write event (`credit_ai_cash_out` or
`debit_bankroll_for_seat`) commits it. For an AI that never gets written
(sits at the same table forever, never busts, never vacated), regen
accumulates uncommitted.

`uncommitted_ai_regen` is subtracted from `actual_outstanding` in
the audit so this doesn't show as drift. But it does mean: if you
look at `ai_bankrolls_stored`, you're seeing chips as of the last
write — possibly hours stale. Use `ai_bankrolls_projected` for
live snapshots.

> **Mostly moot now:** with `REGEN_ENABLED = False` (`economy_flags.py:74`),
> passive regen is off, so `uncommitted_ai_regen` is ~zero in practice.
> The active faucet is the side hustle, which writes its own
> `side_hustle_earning` creation entries directly.

### 5. Personality loans moved to the stakes table — RESOLVED

> **Resolved.** This item described loans living in
> `player_bankroll_state.active_loan_*` columns (single loan per player).
> Those columns were **dropped in schema v99** once the Backing /
> stakes-table cutover completed; active stakes now live in
> `StakeRepository` with multi-loan support
> (`bankroll_repository.py:568`, `:593`). `PlayerBankrollState`
> (`bankroll.py:53`) no longer carries `active_loan_*` fields. See
> `docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md` for the system that
> replaced it.

### 6. Live session AI stacks are read from `game_state_service`

The audit reads `game_state.players[i].stack` for non-human players
in any active cash game. If the game state goes stale (e.g., backend
restart drops `game_state_service.games`), those chips drop out of
`actual_outstanding` without a ledger entry, briefly inflating
drift.

In practice: cash sessions are in-memory only by design (the
spec's "v1 architectural invariants"), and a stale game is cleaned
up by `kill_all_cash_sessions` at boot. The transient drift during
a backend restart self-resolves.

## Endgame economy (the unwritten future)

`docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 3 sketches the
chip-sink mechanisms intended to close the player-side inflation
gap (item 3 above). None of it is built. Touchstones:

- **Staking busted AIs** — durable contract, "you fund Napoleon at
  the $50 table, take 20% of his winnings, eat 100% of his losses."
  Creates a player chip sink and a player-as-backer role.
- **Private home games** — player owns a table with custom invite
  list. Per-session run costs.
- **Character unlocks** — chip-priced one-time per-personality
  availability flag.
- **Hand-of-fame** — UI on existing `MemorableHand` data.
- **Heads-up gauntlet** — defeat-every-celebrity achievement.
- **Soft cap on stakes** — $1000 is the top of the ladder, don't
  go infinite.

The chip ledger v0 is the foundation for any of these — they all
need to declare their source/sink so the conservation invariant
holds.

## Related files (read for deeper context)

| File | What's inside |
|---|---|
| `cash_mode/bankroll.py` | `AIBankrollState`, `PlayerBankrollState`, `project_bankroll`, `credit_ai_cash_out`, `debit_bankroll_for_seat` |
| `cash_mode/stakes.py` | `STAKES_LADDER`, `STAKES_ORDER`, `is_sponsor_eligible`, `table_buy_in_window` |
| `cash_mode/sponsor_offers.py` | Archetype pool, `compute_offers_for_table`, `compute_personality_offers` (Path B) |
| `cash_mode/loan_settlement.py` | `settle_loan_on_leave` — the load-bearing leave-time math |
| `cash_mode/lobby.py` | `ensure_lobby_seeded`, `refresh_unseated_tables`, `kill_all_cash_sessions` |
| `cash_mode/movement.py` | `evaluate_ai_movement`, `refresh_table_roster`, `BankrollChange` |
| `cash_mode/full_sim.py` | `play_one_hand`, controller cache integration, burst math |
| `cash_mode/activity.py` | Event ring buffer + reason vocabulary for lobby ticker |
| `core/economy/ledger.py` | `LEDGER_REASONS`, `record_*` helpers, source/sink constructors |
| `flask_app/services/chip_ledger_audit.py` | `compute_audit` |
| `flask_app/routes/cash_routes.py` | All `/api/cash/*` routes; `_build_cash_game`, `leave_table` |
| `poker/repositories/bankroll_repository.py` | DB CRUD for player + AI bankrolls + per-personality knobs |
| `poker/repositories/cash_table_repository.py` | DB CRUD for `cash_tables`, `cash_idle_pool` |
| `poker/repositories/chip_ledger_repository.py` | DB CRUD for `chip_ledger_entries` |

## Related plans (for what's NOT built)

| Plan | What it adds |
|---|---|
| `docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md` | Persistent loans, reputation enforcement, tab UI, AI borrowers |
| `docs/plans/CASH_MODE_FULL_SIM_HANDOFF.md` | (Shipped) Real cardplay at unseated tables |
| `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 3 | Endgame economy — chip sinks, character unlocks |

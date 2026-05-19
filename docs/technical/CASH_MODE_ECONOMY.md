---
purpose: Technical reference for the cash-mode chip economy as implemented — pools, flow paths, conservation invariant, audit, and tuning levers.
type: reference
created: 2026-05-19
last_updated: 2026-05-19
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
Every **pure transfer** (entity-to-entity, universe unchanged) writes
NO ledger entry by design.

The audit endpoint (`/api/admin/chip-ledger/audit`) computes the
invariant in both directions and reports `drift = ledger_outstanding -
actual_outstanding`. **`drift == 0` is the correctness signal.** Non-
zero drift means some chip movement bypassed the ledger.

## Chip pools

| Pool | Live representation | Persistence |
|---|---|---|
| **Player bankroll** | `PlayerBankrollState.chips` per `owner_id` | `player_bankroll_state` table |
| **AI bankroll (stored)** | `AIBankrollState.chips`, as of `last_regen_tick` | `ai_bankroll_state` table |
| **AI bankroll (projected)** | `project_bankroll(stored, cap, rate, now)`, capped at `bankroll_cap` | virtual — computed on read, persisted on write events |
| **Cash table seats (AI)** | `seats_json[i].chips` for each `kind: 'ai'` slot | `cash_tables.seats_json` |
| **Live session AI stacks** | `state_machine.game_state.players[i].stack` for non-human players in active games | in-memory only, lives during a player's session |
| **Active loan principals** | `loans.outstanding_principal` (post-Backing Phase 1) OR `player_bankroll_state.active_loan_amount` (pre-Backing) | DB |
| **Idle pool** | `cash_idle_pool` table — AIs not currently seated; their chips still live in `ai_bankroll_state` | DB |

All five "pool" rows above contribute to `actual_outstanding` in the
audit. **`uncommitted_ai_regen`** (the diff between projected and
stored AI bankrolls) is subtracted from the total because regen
hasn't yet been ledgered.

### Pool sizing (as of 2026-05-19)

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

| Reason | Trigger | Writer |
|---|---|---|
| `pre_ledger_universe` | One-shot at schema v94 migration | `_migrate_v94_chip_ledger_pre_ledger_universe` in `schema_manager.py` |
| `player_seed` | First-time player entry into cash mode (`load_player_bankroll` returns None) | `_load_or_seed_player_bankroll` in `flask_app/routes/cash_routes.py` |
| `ai_regen` | Any `save_ai_bankroll` call where projected > previous stored | `credit_ai_cash_out` in `cash_mode/bankroll.py` (the canonical write surface that fires the ledger entry); raw `bankroll_repo.save_ai_bankroll` calls don't |
| `house_loan_issue` | Player accepts an anonymous archetype sponsor offer | `sponsor_and_sit` route in `cash_routes.py` |

**Notably absent:** initial bankroll seeding for AIs. AIs come into
existence via `personalities.json` seeding and `ai_bankroll_state`
rows are first written when the AI sits at a table. The
`pre_ledger_universe` entry covered the bankroll-state rows that
existed at v94 migration time; any AI personality created after
that point gets their first bankroll row implicitly via sit-down /
seed flows, and *that's currently not instrumented as a creation
source*. Treat this as a known minor leak — every new
ai_bankroll_state row is a creation source that doesn't fire a
ledger entry. The audit's drift will surface it.

### Sinks (chips leave the universe)

| Reason | Trigger | Writer |
|---|---|---|
| `cap_clamp` | `credit_ai_cash_out` writes a bankroll where pre-clamp value > `bankroll_cap`; overflow is destroyed | `credit_ai_cash_out` in `cash_mode/bankroll.py` |
| `house_loan_settle` | Leave-time settlement of an active house-archetype loan; both the floor repayment and the sponsor cut go to the bank | `leave_table` in `cash_routes.py` → `settle_loan_on_leave` in `cash_mode/loan_settlement.py` |
| `forgive_balance` | Annotation only (amount=0). Recorded when a player leaves with chips < loan floor and the remaining house-loan principal is forgiven | `settle_loan_on_leave` |

### Pure transfers (no ledger entry — universe unchanged)

These move chips between two non-bank entities. Both sides are
counted in `actual_outstanding`, so the math balances without ledger
involvement.

| Movement | Mechanism | File |
|---|---|---|
| Player bankroll → table stack (sit-down) | `_build_cash_game` debits bankroll | `cash_routes.py` |
| Player bankroll ↔ table stack (top-up) | `top_up` route | `cash_routes.py` |
| Table stack → player bankroll (leave) | `leave_table` after loan settlement | `cash_routes.py` |
| AI bankroll → seat chips (live-fill, initial seed) | `debit_bankroll_for_seat` in `cash_mode/bankroll.py` | called from `refresh_unseated_tables` and `ensure_lobby_seeded` in `cash_mode/lobby.py` |
| Seat chips → AI bankroll (movement vacate) | `credit_ai_cash_out` (which DOES fire `ai_regen` + `cap_clamp` ledger entries because it commits regen and may clamp; the seat-credit portion itself is the pure transfer) | called from `refresh_unseated_tables` |
| AI lender bankroll → player table stack (personality sponsor loan) | `sponsor_and_sit` route's personality-loan branch | `cash_routes.py` |
| Player table stack → AI lender bankroll (loan settle, personality loan) | `settle_loan_on_leave` personality-loan branch | `loan_settlement.py` |
| Sim/in-game pot redistribution between AI seats | Inside `play_one_hand` (full sim) or `roll_fake_hand` (deprecated by full sim but still in tree) | `cash_mode/full_sim.py`, `cash_mode/fake_sim.py` |
| In-game player pot ↔ AI pot | Standard hand engine (`poker/poker_state_machine.py`) | not cash-mode-specific |

### The "credit" path's hybrid nature

`credit_ai_cash_out` is the most subtle helper to understand because
it does *three things* in one call:

1. **Pure transfer**: `seat_chips` flow from a non-bank source
   (table stack or vacating seat) into the AI's bankroll.
2. **Ledgered creation**: any accumulated regen (`projected -
   stored`) is committed at this moment, firing an `ai_regen` entry.
3. **Ledgered destruction**: if the post-credit bankroll would exceed
   `bankroll_cap`, the overflow is clamped away and fires a
   `cap_clamp` entry.

This is by design — regen is "virtual" until a write event commits
it, and writes always run cap-clamp because the cap is a hard
ceiling. Every credit-side event therefore reconciles regen and
cap-clamp as side effects.

The debit-side pair (`debit_bankroll_for_seat`) is deliberately
*not* hybrid — it preserves `last_regen_tick` so uncommitted regen
keeps accruing rather than committing at debit time. This asymmetry
is intentional: commits happen at credit events, when the bankroll's
final shape is needed.

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
    "active_loans_principal": Σ active_loan_amount for active loans,
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
bypassed the ledger. As of 2026-05-19 the deployment carries ~1M of
pre-fix drift from the historical lobby-seed leak (see "Known issues"
below); the leak is closed forward but the back-log wasn't
reconciled.

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
  "bankroll_cap": 4000-250000,         // hard ceiling
  "bankroll_rate": 100-3500,           // chips/day regen
  "buy_in_multiplier": 1.0-1.5,        // multiplies table min_buy_in to get this AI's buy-in
  "stop_loss_buy_ins": 2-5,            // v2 (unused in v1.5)
  "stop_win_buy_ins": 3-10,            // v2 (unused in v1.5)
  "stake_comfort_zone": "$2"-"$1000"   // preferred stake; selection priority
}
```

Editable via admin UI (Path A added the route + panel:
`/api/personality/<name>/bankroll-knobs`).

Defaults (when `bankroll_knobs` is absent) are in
`cash_mode/bankroll.py:BANKROLL_KNOB_DEFAULTS`:
`cap=10_000`, `rate=500`, `multiplier=1.0`, `comfort_zone="$10"`.

### Movement probabilities (`cash_mode/movement.py`)

```python
DEFAULT_STAKE_UP_PROB = 0.30        # chance of climbing tiers after winning big
DEFAULT_TAKE_BREAK_PROB = 0.10      # chance of moving to idle after winning big
DEFAULT_BORED_MOVE_PROB = 0.015     # base per-hand chance of bored move
DEFAULT_LIVE_FILL_PROB = 0.15       # chance an open seat fills per tick
```

Movement thresholds (not constants; check `evaluate_ai_movement`):
- **Won big**: `chips >= 2.0 × buy_in`
- **Lost big**: `chips <= 0.3 × buy_in`

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
  "lender_profile": {
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

### Ledger reasons (`core/economy/ledger.py:LEDGER_REASONS`)

The full vocabulary, with directions:

| Reason | Direction | Purpose |
|---|---|---|
| `player_seed` | bank → player | First-time player entry |
| `ai_regen` | bank → ai | Committed bankroll regen |
| `house_loan_issue` | bank → player | Anonymous sponsor loan accepted |
| `pre_ledger_universe` | bank → universe | One-shot v94 seed |
| `cap_clamp` | ai → bank | Overflow above bankroll_cap |
| `house_loan_settle` | player → bank | Leave-time house loan repayment + cut |
| `forgive_balance` | n/a (amount=0) | Audit annotation only |

Adding a new reason requires editing `LEDGER_REASONS` first — unknown
reasons are rejected at write time (defensive).

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

### 1. Historical drift (~1M chips)

The chip-ledger landed at schema v93 with a one-shot
`pre_ledger_universe` entry equal to the chip totals at that moment.
But for ~6 weeks before today's leak-fix, every lobby seed + live-fill
event minted seat chips without instrumentation. That accumulated
drift now sits at ~-1M (ledger sees ~63k outstanding, actual is ~1M).

The leak is closed forward (`f04e048b`). The historical drift can be
reconciled with a one-shot migration: sum each AI's current
`cash_table_seats[i].chips`, debit each AI's bankroll by that amount
(retroactively applying the pure-transfer rule). Not yet done.

### 2. New AI bankroll rows aren't ledgered

When a personality first sits at a cash table, the `ai_bankroll_state`
row is implicitly created via `bankroll_repo.save_ai_bankroll(...)`
without firing a `player_seed`-style ledger event. The
`pre_ledger_universe` seed covered the rows that existed at migration
time, but new personalities added since are creation sources that
don't fire ledger entries.

Mitigation: surveys of `actual_outstanding` vs `ledger_outstanding`
will surface this as drift. Fix path: a new `ai_seed` reason fired
the first time an `ai_bankroll_state` row is inserted.

### 3. Player has no cap

AI bankrolls cap at `bankroll_cap` (max 250k across personalities).
Player bankroll has **no cap**. A consistently-winning player
accumulates chips without bound, eventually outscaling every AI in
the cast.

This is a structural inflation source the central bank can't fix
because the chips ARE leaving the AI pool (correctly recorded via
`cap_clamp` on AI cash-out) but ALSO landing in the player pool with
no symmetric clamp.

Fix path: **endgame economy chip sinks** (see Part 3 of
`CASH_MODE_AND_RELATIONSHIPS.md` — staking busted AIs, character
unlocks, private home games). Not yet built.

### 4. AI bankroll regen committed only at credit-side writes

Regen accrues virtually (read via `project_bankroll`); it only
becomes a real ledger entry when something calls `credit_ai_cash_out`.
For an AI that never gets credited (sits at the same table forever,
never busts, never gets vacated), regen accumulates uncommitted.

`uncommitted_ai_regen` is subtracted from `actual_outstanding` in
the audit so this doesn't show as drift. But it does mean: if you
look at `ai_bankrolls_stored`, you're seeing chips as of the last
write — possibly hours stale. Use `ai_bankrolls_projected` for
live snapshots.

### 5. Personality loans aren't tracked in `loans` table yet

Path B's personality-loan path uses `player_bankroll_state.active_loan_*`
fields (single loan per player). Backing Phase 1 (not yet built) moves
loans to a dedicated table with multi-loan support and persistent
debt across sessions.

Current state: personality loans settle at leave (same as v1
sponsorship); the only thing different from house loans is whose
bankroll the chips flow to/from.

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

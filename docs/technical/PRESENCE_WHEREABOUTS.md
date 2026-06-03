---
purpose: The cash-mode Presence state machine (entity_presence) and the Whereabouts read view — how actor location is made unrepresentable-when-wrong, and how seating/economy consume it
type: architecture
created: 2026-06-03
last_updated: 2026-06-03
---

# Presence & Whereabouts

Cash mode has to answer one question per actor, every tick: **where is this entity
right now?** Historically four tables each held a partial answer (`cash_tables`
seat map, `cash_idle_pool`, `ai_side_hustle_state`, `ai_vice_state`), so
contradictions — `seated_and_idle`, `double_seat`, ghost seats — were possible *by
construction*. They were detected after the fact and swept, not prevented (this
bug class recurred enough to earn its own MEMORY entries).

The fix is two layers, complementary not redundant:

| Layer | File | Side | Role |
|---|---|---|---|
| **Presence machine** | `cash_mode/presence.py` | write | Authoritative FSM: one row per actor, contradictions structurally impossible |
| **Whereabouts** | `cash_mode/whereabouts.py` | read | Union view across the legacy tables for the lobby/admin panel; drives no writes |

The machine is the enforcement layer; Whereabouts is the human-readable report. The
design rationale below is sourced from
`docs/captains-log/development/presence-shadow-cutover-step2.md` (point-in-time;
code wins on any conflict).

---

## Presence states

Defined as `PresenceState_` (alias `Presence`) at `cash_mode/presence.py:62`.

| State | Meaning | Carries seat? |
|---|---|---|
| `OFFLINE` | Not present in this sandbox. Stored as the **absence of a row** (no row == offline). | No |
| `SEATED` | At a table; the **only** state that may carry `table_id` + `seat_index` (both mandatory). | Yes |
| `IDLE` | Present, between tables (the AI idle pool). | No |
| `SIDE_HUSTLE` | AI off-grid earning — a projection of `ai_side_hustle_state`. | No |
| `VICE` | AI off-grid spending — a projection of `ai_vice_state`. | No |
| `POOL` | Origin for pool-funded casino AI (fish). No `OFFLINE`/bankroll analogue; on leaving a seat such an AI returns to `POOL`, not `IDLE`. | No |

The value type is a **frozen dataclass** `PresenceState` (`presence.py:179`). Its
`__post_init__` (`presence.py:199`) enforces the seat invariant: `SEATED` requires
both seat fields non-None; every other state requires both None. Constructing a
non-seated row that carries a seat raises `IllegalPresenceTransition` — *"a
non-seated entity holding a seat is exactly the ghost-seat bug this machine
forbids."*

Entity IDs follow the chip-ledger convention (`presence.py:241`, `:246`):
`player:<owner_id>` for humans, `ai:<personality_id>` for AI (fish and regular
personas both use `ai:`). This is a **different** presence concept from
`flask_app/services/presence.py`, which tracks Socket.IO *connection* presence
(noted explicitly at `presence.py:32`).

---

## Legal transition table

The single source of truth is `LEGAL_TRANSITIONS` (`presence.py:120`). Any
`(state, event)` not in the map raises `IllegalPresenceTransition`. The forbidden
contradictions are visible by their *absence*: every value is one `PresenceState_`
(no edge lands in two states), and `SIT`-from-`SEATED` is **not present** — an
entity cannot take a second seat without `LEAVE`-ing the first.

| From | Event | To |
|---|---|---|
| `OFFLINE` | `SIT` | `SEATED` |
| `OFFLINE` | `SEED` | `POOL` |
| `POOL` | `SIT` | `SEATED` |
| `POOL` | `RETURN_TO_POOL` | `POOL` (idempotent re-seed cleanup) |
| `POOL` | `GO_OFFLINE` | `OFFLINE` |
| `IDLE` | `SIT` | `SEATED` |
| `IDLE` | `RESEAT` | `SEATED` |
| `IDLE` | `START_HUSTLE` | `SIDE_HUSTLE` |
| `IDLE` | `START_VICE` | `VICE` |
| `IDLE` | `GO_OFFLINE` | `OFFLINE` |
| `SEATED` | `LEAVE` | `IDLE` |
| `SEATED` | `GO_OFFLINE` | `OFFLINE` |
| `SEATED` | `RETURN_TO_POOL` | `POOL` |
| `SIDE_HUSTLE` | `END_OFFGRID` | `IDLE` |
| `VICE` | `END_OFFGRID` | `IDLE` |

`PresenceEvent` is defined at `presence.py:86`. The pure `transition()`
(`presence.py:261`) additionally checks seat-argument consistency:
`_SEAT_REQUIRING_EVENTS = {SIT, RESEAT}` (`presence.py:147`) must supply
`table_id` + `seat_index`; `_SEAT_CLEARING_EVENTS` (`presence.py:150`, the other
seven) must not. `transition()` never reads a clock — `updated_at` is
caller-supplied; the persistence layer sets it.

**Why humans `GO_OFFLINE`, not `LEAVE`** (per the cutover log): `LEAVE` routes to
`IDLE`, which is the AI idle-pool concept. A human leaving cash mode has cashed out
of the sandbox entirely — there is no human idle pool — so the human departure
event is `GO_OFFLINE`, keeping `IDLE` AI-only.

---

## The `entity_presence` table

Created by migration **v128** (`schema_manager.py:7061`, registered at `:2202`):

```sql
CREATE TABLE entity_presence (
    entity_id   TEXT NOT NULL,
    sandbox_id  TEXT NOT NULL DEFAULT 'default',
    state       TEXT NOT NULL,
    table_id    TEXT,
    seat_index  INTEGER,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity_id, sandbox_id),
    CHECK (state IN ('offline','seated','idle','side_hustle','vice','pool')),
    CHECK ( (state = 'seated' AND table_id IS NOT NULL AND seat_index IS NOT NULL)
         OR (state <> 'seated' AND table_id IS NULL AND seat_index IS NULL) )
)
```

Two structural guards make the bug classes **unrepresentable in the database**, not
just in the application layer:

- **`seated_and_idle` / two-state contradictions** — the compound PK
  `(entity_id, sandbox_id)` permits exactly one row per actor per sandbox.
- **`double_seat`** — the partial unique index `idx_entity_presence_seat`
  (`schema_manager.py:7081`) over `(sandbox_id, table_id, seat_index) WHERE state =
  'seated'` forbids two entities sharing one physical seat. Non-seated rows have
  NULL seat fields and are excluded. The `IntegrityError` this raises is what
  propagates under authority mode (below) to roll back a bad seat write.

`OFFLINE` is the absence of a row; saving `OFFLINE` deletes the row
(`presence_transitions.py:131`, `_write_state`).

**Satellite: `cash_idle_metadata`** (migration **v129**, `schema_manager.py:7119`,
registered at `:2206`). PK `(personality_id, sandbox_id)`, carries `reason` /
`target_stake` / `left_at`. These are the *movement-routing* fields (why did you
leave? where do you want to sit next?) that drive the idle-candidate filter in
`cash_mode/movement.py`. They are deliberately kept **off** the pure machine — the
same philosophy that forbids non-seated rows from carrying seat fields rejects
IDLE-only payload on a generic presence row.

Repository: `EntityPresenceRepository`
(`poker/repositories/entity_presence_repository.py`) — `load` / `save` /
`persist_transition` / `seated_rows_for_entity`.

---

## The two flags

Both are read **live** (via `getattr` on the module) so a runtime flip or a test
monkeypatch takes effect without re-import.

### `PRESENCE_SHADOW_WRITE_ENABLED` — now terminal

`economy_flags.py:220`, default **False**. The dual-write validation phase: seat /
idle / hustle / vice writers *also* mirror into `entity_presence`, best-effort
(try/except-wrapped, can never break the real write). Its purpose was to prove the
machine tracked reality on live traffic *before* the authority flip — which is now
done. It remains as a kill switch / fallback but the system runs on authority.

The shadow phase taught two lessons recorded in the cutover log:

- The validation harness had to inject `extensions.entity_presence_repo` into the
  sim explicitly; without it every shadow write was a silent no-op and the audit
  compared an empty table against itself — a **false green** caught before it
  mattered (audit with `--checkpoints`, not just an end-of-run snapshot).
- The original lobby reconcile-diff saw a seat go empty but did **not** emit
  `LEAVE` for the vacated entity, leaving stale `SEATED` rows that blocked the
  rightful new occupant's `SIT` (the `IntegrityError` was swallowed in shadow). The
  fix — `LEAVE` every entity the engine has `SEATED` at a table no longer in the
  new seat map — is the departures pass below.

### `PRESENCE_AUTHORITY_ENABLED` — default on

`economy_flags.py:234`, default **True** (committed as
`_env_flag("PRESENCE_AUTHORITY_ENABLED", True)`). When on, `entity_presence` is the
authoritative location record:

- The `save_table` chokepoint drives transitions **inside the same SQLite
  transaction** (presence + seat map commit atomically).
- A `double_seat` `IntegrityError` (or any `IllegalPresenceTransition`) propagates
  and rolls back the entire `save_table`, rejecting the bad write.
- `list_idle` derives the idle pool from `entity_presence WHERE state='idle'`
  rather than reading `cash_idle_pool` directly
  (`cash_table_repository.py:705`, `:751`).

The flag stays env-overridable for production rollback; the soak on dev showed zero
persistent divergences under real churn. `presence_shadow.is_enabled()`
(`presence_shadow.py:55`) returns True when **either** flag is set — so off-grid
(hustle/vice) keeps mirroring after the seat machine went authoritative, and the
legacy per-call-site reconciles gate off harmlessly (below).

---

## The write chokepoint

Every seat write flows through `CashTableRepository.save_table`. Inside its
transaction, after writing the `cash_tables` row, it calls
`emit_presence_transitions_for_save` on the same connection
(`cash_table_repository.py:427`). That engine
(`presence_transitions.py:275`) diffs the prior `seats_json` blob against the new
seat map and emits the minimal legal transitions:

1. **Departures** (`presence_transitions.py:309`): for every entity presence has
   `SEATED` at this table (`SELECT … WHERE state='seated'`) that is no longer in the
   new map at the same seat, emit a departure derived from the slot it left
   (`_departure_event`, `:263`):
   - `player:` → `GO_OFFLINE` (human cashes out)
   - `archetype='fish'` slot → `RETURN_TO_POOL` (pool-funded)
   - any other AI → `LEAVE` (→ IDLE) + write a `cash_idle_metadata` row.

   Clearing departures first frees the seat in the partial-unique index so arrivals
   can't collide.

2. **Arrivals** (`presence_transitions.py:330`): for each desired occupant not
   already correctly seated, promote through a legal precursor before `SIT`:
   - `SEATED` elsewhere (a move) → `LEAVE` first
   - `SIDE_HUSTLE` / `VICE` → `END_OFFGRID` first (→ IDLE)
   - `OFFLINE` fish (`archetype='fish'`) → `SEED` first (→ POOL)
   - then `SIT` with `table_id` + `seat_index`, then delete any
     `cash_idle_metadata` row.

In **authority** mode (`raise_on_integrity=True`) an `IntegrityError` /
`IllegalPresenceTransition` propagates and rolls back. In **shadow** mode both are
logged and swallowed (`presence_transitions.py:196`, `:380`).

**Why the chokepoint, not ~30 call-site rewrites** (per the log): all call-site
shadow reconciles gate on the flag and go dormant when authority is on, so one
writer inside the transaction covers every path — AI churn, casino spawn/wind-down,
the human sit/leave routes — and closes the cross-connection TOCTOU window. The
legacy `_shadow_reconcile_table` (`lobby.py:156`) explicitly **returns early** when
authority is on (`lobby.py:202`), making the per-call-site reconciles harmless
no-ops.

---

## Deletion-time sweeps

`cash_mode/presence_sweep.py` closes orphans at the *source* (the delete) rather
than sweeping them later. Both are best-effort and gated on
`PRESENCE_AUTHORITY_ENABLED` (`presence_sweep.py:26`):

- `free_human_seat_on_delete` (`:49`) — when a game row is deleted (reaper/purge),
  opens the human's persisted cash seat via `save_table`, which drives `GO_OFFLINE`
  atomically. Replaces the retired `_free_ghost_human_seats` reconciler.
- `sweep_presence_on_persona_delete` (`:93`) — when a persona is deleted, finds all
  `SEATED` rows via `seated_rows_for_entity`, **returns the seat's residual chips to
  the bank pool first** (`_return_seat_chips_to_pool`, `:162` — only opens the seat
  if the return succeeds, so chips never vanish), then opens each seat (drives
  `RETURN_TO_POOL` / `GO_OFFLINE`). Replaces the retired
  `_reclaim_zombie_casino_seats` reconciler.

Read-only audit: `cash_mode/presence_consistency.py:50`
(`check_presence_seat_consistency`) reports three violation kinds —
`presence_seated_no_slot`, `seat_entity_mismatch`, `slot_no_presence` — used by
`scripts/audit_presence_divergence.py` and tests. It never writes;
`reserved` slots are excluded (a sponsorship hold is not a seated presence). On a
live DB a non-empty result may be a ~2s ticker transient (the seat write and
presence commit are atomic, but a cross-connection snapshot can straddle them) —
double-read and keep only persistent violations.

---

## How seating and the economy consume it

The world ticker (`flask_app/services/ticker_service.py`) runs every
`BASE_TICK_SECONDS = 2.0` (`:41`), budget-capped at `CYCLE_BUDGET_MS = 250.0`
(`:42`) across active sandboxes. A sandbox is "active" when its owner has a live
Socket.IO session or was seen within `ACTIVE_TTL_SECONDS = 60.0`
(`flask_app/services/presence.py:35` — the connection tracker, distinct from this
machine). Per tick, `_tick_sandbox` (`ticker_service.py:376`) ultimately calls
`refresh_unseated_tables` (`lobby.py:1095`), whose passes each terminate at a
`save_table` and therefore drive presence atomically:

| Tick pass | Presence effect |
|---|---|
| Off-grid expiry (vice / hustle timers elapse) | `END_OFFGRID` (→ IDLE), state row deleted |
| Per-table AI-vs-AI burst → movement leaves | `LEAVE` → IDLE (or `RETURN_TO_POOL` for fish) via `save_table` |
| Vice / side-hustle fire on idle AIs | `START_VICE` / `START_HUSTLE` (→ off-grid) |
| Global greedy fill (`_process_global_greedy_fills`, `lobby.py:768`) | `SIT` → SEATED; idle candidates filtered via `list_idle`, which under authority reads `entity_presence WHERE state='idle'` |

The seating layer therefore consumes presence as its **idle candidate source** and
its **occupancy authority**: greedy fill matches truly-idle AIs (not stale
`cash_idle_pool` rows) to open seats by attractiveness score, and the `SIT` only
commits if the partial-unique index accepts it. Net result under authority: at the
commit boundary of each `save_table`, `entity_presence` is in sync with the
`cash_tables` seat map; the gap during active churn is at most one tick (~2s).

**Human sit/leave** (`flask_app/.../cash_routes.py`): a sponsorship hold is a
`reserved` slot — a *distinct seat kind*, not `human` (per the
`cash-sponsorship-seat-hold.md` log: reusing `human` would reintroduce the
ghost-seat ambiguity a TTL sweep can't disambiguate; `reserved` carries no presence
row). `sponsor-and-sit` claims the hold and `save_table` → `SIT` → SEATED. Leave
sets the slot `open` → engine emits `GO_OFFLINE` → presence row deleted.

---

## Whereabouts read view

`build_whereabouts()` (`whereabouts.py:236`) unions five legacy stores into one
record per personality (it does **not** read the presence machine; under authority
its idle source — `list_idle` — happens to derive from `entity_presence`):
seated slots across `cash_tables`, idle pool, active+expired side hustle, active+
expired vice, and tournament participants/invites.

**Status precedence** (`whereabouts.py:363`): `TOURNAMENT` > `SEATED` >
`SIDE_HUSTLE` > `VICE` > `IDLE` > `TOURNAMENT_BOUND` > `UNKNOWN`. Status constants
at `whereabouts.py:46`. Note `idle + off-grid` is the *normal* forced-leave
representation (a broke AI on a hustle stays in `cash_idle_pool` with
`reason='forced_leave'`) and is deliberately **not** flagged stuck — off-grid
outranks idle in display.

**Stuck flags** (`whereabouts.py:64`–`:107`):

| Hard (invariant violations) | Soft (temporal / expected) |
|---|---|
| `STUCK_DOUBLE_SEAT` | `STUCK_OVERDUE_HUSTLE` |
| `STUCK_SEATED_AND_IDLE` | `STUCK_OVERDUE_VICE` |
| `STUCK_SEATED_AND_OFFGRID` | `STUCK_STALE_IDLE` (`DEFAULT_STALE_IDLE_SECONDS = 30*60`, `:114`) |
| `STUCK_UNKNOWN_PERSONALITY` | `STUCK_SEATED_TOO_LONG` (`DEFAULT_SEATED_TOO_LONG_SECONDS = 3*60*60`, `:121`) |
| `STUCK_NO_BANKROLL` | `STUCK_TOURNAMENT_BOUND_AND_SEATED` |
| `STUCK_SEATED_AND_TOURNAMENT` | |

Under authority the hard flags should never fire — they are now diagnostics for a
wiring regression rather than expected drift.

---

## See also

- [`CASH_MODE_SEATING_ATTRACTIVENESS.md`](CASH_MODE_SEATING_ATTRACTIVENESS.md) —
  how greedy fill scores and matches idle AIs to open seats (the consumer of the
  IDLE projection).
- [`CASH_MODE_ECONOMY.md`](CASH_MODE_ECONOMY.md) — bankroll/pool flow, vice and
  side-hustle economics behind the off-grid states.
- `docs/captains-log/development/presence-shadow-cutover-step2.md` — shadow →
  authority flip narrative and the §C dedup/false-green lessons.
- `docs/captains-log/development/cash-sponsorship-seat-hold.md` — why `reserved` is
  a distinct seat kind.

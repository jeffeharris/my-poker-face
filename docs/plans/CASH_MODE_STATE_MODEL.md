---
purpose: Design for unifying cash-mode presence and chip ownership behind two enforced state machines, eliminating the ghost-seat / orphan / silent-forfeiture bug classes
type: design
created: 2026-05-30
last_updated: 2026-06-06
reviewed: 2026-05-30 (codex-assist + code-architect + code-explorer fact-check)
---

# Cash Mode State Model

## Status

**PARTIALLY IMPLEMENTED (updated 2026-06-01).** This doc is no longer a pure
draft — significant pieces have shipped. Current state:

- **Cut 1 — freeze-forever reaper guard: SHIPPED** (the stale-session watchdog no
  longer zeros an active/paused session — the bug that lost the original
  $2k/$8k buy-in). Behavioural guard.
- **Cut 2 — human chip statement: SHIPPED** (`record_transfer` /
  `record_player_buy_in` / `record_player_cash_out` in `core/economy/ledger.py`;
  human buy-in/cash-out is now auditable). A statement, not the custody machine.
- **Presence machine (§5.1): COMPLETE (2026-06-06).** The full cutover landed —
  shadow (Phase 1) → §C dedup (Phase 2) → authority flip (Phase 3) → **legacy
  `cash_idle_pool` cache retired (schema v152)**. `PRESENCE_AUTHORITY_ENABLED` is
  now hardwired `True` (no env override; the rollback escape hatch is gone).
  `entity_presence` is the permanent authority for seats AND idle; idle is read
  from it joined with the `cash_idle_metadata` satellite. Validated in the
  authority-mode divergence sim (0 unexpected). See
  `docs/plans/CASH_MODE_PRESENCE_PHASE3_FLIP.md` and
  `docs/plans/CASH_PRESENCE_CUTOVER_HANDOFF.md`.
- **Chip-custody machine (§5.2): MOSTLY BUILT + FLIPPED ON DEV (2026-06-01).**
  The second machine. **Done + cut over on dev** (`CHIP_CUSTODY_ENABLED=1` in dev
  `.env`, committed default OFF): AI ledger parity (`ai↔seat` transfers at the two
  bankroll chokepoints), D2 ledger-derived bankroll (`balance_of` +
  `derive_*_balance`, int as cache), the structural settle-before-delete reaper
  (`AT_TABLE` exits only via a settlement transfer), and conservation-safe persona
  deletion. The dev DB was backfilled and audits **LEDGER COMPLETE** (AI 1239/1239
  + Player 4/4). **Remaining: the seats-as-view storage demotion (D1) — blocked on
  a design decision (the live stack in `cash_tables.seats[].chips` ≠ the ledger
  `seat:` balance mid-hand, because per-hand P&L isn't ledgered).** See
  `docs/plans/CASH_MODE_CHIP_CUSTODY_HANDOFF.md` (STATUS) +
  `CASH_MODE_CHIP_CUSTODY_SCOPE.md`.
- **Presence read-side polish: DEFERRED** — seat-map projection (§6/Phase 3 "table
  as projection") and actual reconciler deletion are evidence-gathered but held
  (see the Phase-3 doc's "Reconciler retirement" section).

The pivotal table-as-projection decision (§6, D1) is ACCEPTED. `cash_idle_pool`
is fully retired (its demotion went all the way to a DROP — presence + the
`cash_idle_metadata` satellite are the idle authority). `cash_tables` is still a
written cache for seat *payload* (chips/seated_at), but occupancy is projected
from presence on the read paths; its full storage demotion remains deferred.

## 1. Why this exists

Cash mode keeps shipping *reconcilers*: functions whose entire job is to detect
or repair a contradiction between two stores that should never have disagreed.
A scan on 2026-05-30 found ~12 of them (§8) plus a whole vocabulary of "stuck"
states (`seated_and_idle`, `double_seat`, `seated_and_offgrid`, `stale_idle`)
that exist only to *name* contradictions.

Each reconciler fixes an instance. None fixes the class, because the class is:
**there is no authoritative state — storage location stands in for state, and
storage can disagree with itself.**

Two production incidents motivate the work:

- **Silent forfeiture (chips).** A human's $2,000 and (earlier) $8,000 cash
  buy-ins were zeroed by the stale-session watchdog
  (`cash_mode/lobby.py:_boot_sweep_stale_cash_rows`). The session went cold
  (browser closed >4h), the in-memory game was evicted, the `games` row — the
  *only* durable record of the table stack — was deleted, and the session was
  finalised at `final_chips_at_table=0, player_take_home=0`. Conservation-correct
  (no chips minted) and player-hostile (the player's chips were never returned).
  Unauditable, because **there is no human-side ledger** (`chip_ledger_entries`
  logs AI entities only).

- **Split-brain (presence).** Players and AIs show as `seated_and_idle` — seated
  at a table *and* resting in the idle pool simultaneously. `whereabouts.py`
  shipped as a detector for this and immediately surfaced 11 live instances.

These are the same disease in two organs: **chip ownership** and **entity
presence**. This doc proposes one authoritative state machine for each.

## 2. The diagnostic principle

An entity needs a state machine when it shows all three of:

1. a string vocabulary that is *almost* an enum but isn't enforced,
2. state smeared across multiple tables, and
3. one or more reconcilers whose job is to repair disagreement between them.

A real machine makes the disagreement **unrepresentable**, so the reconciler
stops existing. We use this as the inclusion test (§8) and, importantly, the
*exclusion* test — pure GC/janitors (retention sweep, capture cleanup, pricing
sync) are NOT symptoms and stay as-is.

## 3. Invariants (the contract the code is checked against)

These are the properties every code path must preserve. They are the point of
the doc — state them first, check every path against them.

- **I1 — Conservation.** The total chips in the universe is constant across every
  transition. Every chip movement is a transfer between two owners, double-entry
  logged. **This requires an explicit owner taxonomy** (review note — I1 is a
  slogan without it): the known owners/sinks are `player_bankroll`, `ai_bankroll`,
  `table_stack`, `pot`, `bank/house_pool`, `rake_sink`, `loan/stake_escrow`,
  `stake_obligation`. The ledger already models several of these
  (`bank`, `player:<id>`, `ai:<id>`, rake); the taxonomy must be completed and
  every existing creation/destruction concept (AI regen, rake, vice, loan
  principal, stake settle) mapped to a transfer before I1 is enforceable.
  (Today `chip_ledger_entries` covers AI fully and humans PARTIALLY — it logs
  `player_seed`, `house_stake_issue/settle`, `table_rake`, `informant_unlock`,
  but NOT the human buy-in debit or cash-out credit, which mutate
  `player_bankroll_state` directly. Closing those two gaps is Phase 0.)
- **I2 — No destruction by absence.** A human's chips are never reduced by
  inactivity, disconnect, reboot, or janitor. The only way chips leave a player
  is losing a hand or an explicit cash-out the player initiated.
- **I3 — Single presence.** An entity (human or AI) is in exactly one presence
  state at any instant. `seated_and_idle` and `double_seat` are unrepresentable,
  not merely detected.
- **I4 — Single authority per fact.** Each fact (where an entity is; what state a
  chip is in) has exactly one writer. Everything else is a read-model derived
  from it.
- **I5 — Auditability.** Every presence transition and every chip movement is
  recorded with enough context to answer "what happened to X and when" in one
  query. Silent forfeiture becomes alertable.
- **I6 — Idempotent terminal transitions.** Settling/closing twice is a no-op,
  not a double-credit. (The existing `ended_at IS NULL` guard is the pattern;
  generalise it.)

## 4. Scope

**In scope (Tiers 1 & 2 from the inventory):**

- Player presence / whereabouts (human + AI): seated / idle / side-hustle / vice.
- Chip custody: bankroll ↔ committed ↔ at-table ↔ settling.
- Cash session lifecycle (the coordinator that drives the other two).
- Stake / backing lifecycle (active / carry / settled / defaulted).
- AI off-grid (side-hustle / vice) — folds into presence.
- AI casino seat occupancy — the AI application of the same two machines.

**Out of scope (Tier 3):** poker `PokerPhase` (already a clean machine — the
model to copy), coach skill/mode enums, emotional quadrant, clamp tier, sponsor
offer TTL, and all pure janitors.

## 5. The two machines + coordinator

Tiers 1 & 2 collapse into **two** machines over a shared transition log, plus the
session as coordinator. They are deliberately entity-agnostic — the *same*
machines govern humans and AIs.

### 5.1 Presence machine — "where is this actor?"

Single state per entity, explicit transitions.

```
            sit                  voluntary leave / cash-out
OFFLINE ───────────▶ SEATED@table ──────────────▶ IDLE
   ▲                    │   ▲                        │
   │ (human) tab away:  │   │ re-seat                │ start hustle/vice
   │ table FREEZES,     │   │                        ▼
   │ seat HELD,         │   └──── resume ───┐  SIDE_HUSTLE / VICE
   │ NOT released ──────┘                   │        │ end (timer)
   │                                        │        ▼
   └─ (only AI/explicit) ──────────────────┘      IDLE
```

NOTE (D4): a human going absent does NOT transition to OFFLINE with the seat
released — that was the rejected online-poker model. Their table freezes with the
seat held and chips left `AT_TABLE`; see §5.4. OFFLINE-with-release applies to AI
entities and to an explicit human cash-out only.

- States: `OFFLINE`, `SEATED@<table,seat>`, `IDLE`, `SIDE_HUSTLE`, `VICE`.
- One writer. `cash_idle_pool`, `ai_side_hustle_state`, `ai_vice_state`, and the
  occupancy half of `cash_tables` become **projections** of this machine, not
  independent stores.
- Kills: `seated_and_idle`, `double_seat`, `seated_and_offgrid`, `stale_idle`,
  `overdue_hustle`, `overdue_vice` (the `overdue_*` flags become a normal
  timer-driven `SIDE_HUSTLE→IDLE` transition the machine owns).

### 5.2 Chip-custody machine — "what state is this money in, and who owns it?"

Per chip-parcel (a buy-in / stack belonging to one entity at one place).

```
IN_BANKROLL ──commit buy-in──▶ COMMITTED_TO_SEAT ──hand starts──▶ AT_TABLE
     ▲                                                                │
     │                                                                │ leave /
     │                                                                │ disconnect /
     │                                                                │ janitor
     └──────────────── BACK_IN_BANKROLL ◀───── SETTLING ◀────────────┘
```

- States: `IN_BANKROLL`, `COMMITTED_TO_SEAT`, `AT_TABLE`, `SETTLING`,
  (terminal re-entry to `IN_BANKROLL`).
- **There is no transition from `AT_TABLE` to nothing.** The reaper bug is
  structurally impossible: the only exit from `AT_TABLE` is
  `→ SETTLING → IN_BANKROLL`. The janitor may *trigger* that transition; it
  cannot skip it.
- Every transition writes a **double-entry row to a unified ledger** (extend
  `chip_ledger_entries` to humans, or a parallel `player_chip_ledger`).
  Satisfies I1, I2, I5.
- Bankroll stops being a bare mutable integer (`player_bankroll_state.chips`);
  it becomes the sum of `IN_BANKROLL` parcels — i.e. derived from the ledger,
  with the integer as an optional cache. (Decision D2, §9.)

### 5.3 Session — the coordinator

`cash_sessions` already has the most machine-like vocabulary in the codebase:
`active / paused / abandoning / closed / broken`, with `closed_status`
(`left / boot_swept / stale_swept / ghost_cleanup`). **Do not rebuild it —
promote it** to an enforced machine and *actually wire* the reserved
`paused` / `abandoning` states.

The session drives the other two on lifecycle events:

| Event | Presence transition | Chip-custody transition |
|---|---|---|
| sit | `→ SEATED` | `IN_BANKROLL → COMMITTED_TO_SEAT → AT_TABLE` |
| voluntary leave / cash-out | `SEATED → IDLE`/`OFFLINE` | `AT_TABLE → SETTLING → IN_BANKROLL` |
| **disconnect / look away (human)** | `SEATED` held; table **FROZEN** | chips stay `AT_TABLE` (no transition) |
| reaper / boot sweep | (must skip a human's frozen table) | **no chip transition ever** — GC dead rows only |
| resume | re-enter the frozen hand | chips already `AT_TABLE` |

The key policy (D4): **a human's table freezes on absence — it is never sat out,
never settled by a timer.** No hand advances without the player; the seat and
chips stay exactly as they were. Because chips live as `AT_TABLE` parcels in the
ledger (D2), the freeze is safe for any duration: even if the in-memory game is
evicted (~30s) and the stale `games` row is later GC'd, the chips are recorded
and the hand is resumable. Settlement happens **only** on an explicit
player-initiated leave/cash-out. This deliberately rejects the online-poker
sit-out model (where an absent player's table keeps playing) in favour of
"the world revolves around the player" — see §5.4.

### 5.4 The freeze model — two tiers of "world"

There are two distinct "worlds", and they are governed separately:

- **The ambient world** (every table *except* the one you're at) is
  presence-gated. It advances only while you're actively looking (beacon within
  30s) and pauses ~30s after you background/close. This already works today.
- **Your table** is sacred. It does not advance without you and is never sat out
  or settled by absence. On look-away it **freezes** mid-hand; on return you
  resume exactly where you were.

Safety comes from chip durability, not from a timer: chips at your frozen table
are `AT_TABLE` parcels in the ledger, independent of the in-memory game (D4a:
freeze forever in a single-player sandbox). The reaper's chip-settling job is
*deleted* — it may only GC rows for sessions already settled by an explicit player
leave, and even then it touches no chips.

> **SOUNDNESS GAP — found in review (2026-05-30), MUST fix before build.**
> Chip-safety ≠ hand-resumability. The ledger reconstructs chip *ownership*, not
> hand *state* (hole cards, board, pot, current actor, deck/RNG). Hand state lives
> ONLY in `games.game_state_json` (`serialization.py`); `cash_table_id` /
> `cash_seat_index` aren't even in that JSON — they're recovered from
> `cash_sessions` by `_restore_cash_table_binding`. Today the reaper deletes the
> `games` row **by age** (`updated_at > 4h`), NOT by session state. So under naive
> freeze, an absent player gets chips back but the **hand is gone**.
> **Required fix (cheap, two-line):** `_boot_sweep_stale_cash_rows` must skip any
> `games` row whose `cash_session.session_state == 'active'` (not just rows
> currently in memory via `skip_game_ids`). Combined with ledger-safe chips, this
> makes indefinite freeze actually resumable. This guard is itself a strong
> candidate for the very first change shipped (see §7 Phase 1 / §10 80% fix).

This is the inverse of online poker (where the table keeps dealing and blinds you
off). Here the world revolves around the player.

## 6. PIVOTAL DECISION — table as projection, not a machine

**Recommendation: the cash table is NOT its own state machine. Its seat map is a
derived read-model of Presence ∩ Chip-custody, never written independently.**

Rationale: the ghost-seat / `seated_and_idle` class *is* a table-state bug. It
exists because `cash_tables.seat_map` and `cash_idle_pool` are **both
authoritative and can disagree**. Make Presence the single authority for "who is
seated" and Chip-custody the authority for "how many chips are in that seat", and
a seat becomes the join of the two — with no second writer to disagree with.
Ghost seats become unrepresentable (I3, I4).

"Leaving table state out" only works *with* this demotion. Leaving the seat_map
as an independent store would preserve the bug. The table's genuinely-own data
(exists / stake level / teardown) is static provisioning config — fine as plain
data, no machine needed.

Cost: this is the **highest-effort** part. Every current writer of `seat_map`
must be rerouted through Presence (§8 lists them). Sequenced as its own migration
phase (§7, Phase 3) with conservation checks.

**Sign-off needed:** accept table-as-projection, or keep table authoritative
(and accept that ghost-seat reconcilers stay). Everything downstream assumes
the former.

### 6.1 Concurrency & atomicity (review-mandated section)

The codebase already serializes cash mutations with two lock families in
`game_state_service`: `get_game_lock(game_id)` (game progress) and
`get_sandbox_lock(sandbox_id)` (seat mutations — held at ~10 sites in
`cash_routes.py` and in `ticker_service.py:305`). Deployment is single-worker
(`-w 1`).

Design rule: **the Presence and Chip-custody machines are PURE state-transition
functions; the CALLER holds the sandbox lock for the duration of a transition.**
The machines do not acquire locks themselves (they're called from both request
threads and the ticker thread). A "transition" that spans presence + chip-custody
+ session must run inside one `get_sandbox_lock` critical section so the three
commit atomically. This means the machines *enforce legal state*, but *atomicity*
is still the caller's contract — state this explicitly rather than implying the
machine guarantees it.

### 6.2 Scoping, sims, and entity-agnosticism (review-mandated)

- **Sandbox-scoped.** Every entity exists per `sandbox_id` (it appears 100+ times
  in `lobby.py` alone). The Presence machine's key is `(personality_id|owner_id,
  sandbox_id)` — an entity can be SEATED in one sandbox and IDLE in another.
- **Sim paths.** `cash_mode/full_sim.py` is the highest-volume seat writer
  (AI-only hands every few seconds via the ticker, with no `CashSession`). The
  design must explicitly state which invariants sim transitions enforce. Default:
  sims drive Presence/Custody for AI entities through the same machines, but must
  not pay per-transition SQLite cost on the hot path — batch or in-memory-then-flush.
- **Career/scripted seeding.** The `circuit-progression` work seeds scripted
  rosters by writing `cash_tables` directly — another seat writer Phase 3 must
  route through Presence. Inventory it before Phase 3.
- **Pool-funded AI seats** have no real `IN_BANKROLL`/`OFFLINE` analogue; the
  entity-agnostic machine must define their custody source (the bank pool) and a
  null/`POOL` presence origin so casino provisioning fits without contortion.

## 7. Migration (phased — see §10 for the recommended minimal first cut)

0. **Ledger first.** Build the unified human+AI chip ledger as the substrate
   for everything else. No old-shape migration needed (D0) — but seed it from
   current balances so it's authoritative from day one. Restitution (D5) is the
   acceptance test: replay the two incidents from backups and confirm the ledger
   reconstructs every chip, including the swept buy-ins and the ~3,957 drift.
1. **Chip-custody machine, write-side.** Route all chip movement through it;
   bankroll becomes ledger-derived. Make the reaper settle-not-zero (closes the
   active money-loss bug — highest user value, do early).
2. **Presence machine, write-side.** Single authority; `cash_idle_pool` /
   hustle / vice tables become projections. Kills the idle split-brain.
3. **Table demotion.** Reroute every `seat_map` writer through Presence; seat_map
   becomes a projection. Kills ghost-seat. (Pivotal decision §6.)
   **REVISED SCOPE (review):** this is NOT a mechanical reroute of ~10
   reconcilers. There are **~30 `save_table` callsites across 4 modules**
   (`cash_mode/lobby.py`, `cash_mode/casino_provisioning.py`,
   `flask_app/routes/cash_routes.py`, `flask_app/handlers/game_handler.py`), and
   the primary writers are the sit / leave / reseat / provisioning / hand-boundary
   paths where seat writes are co-mingled with validation, game-build, and
   economic ops in the same function bodies. This is a ground-up rewrite of those
   paths, ~4–6 weeks, **HIGH risk**, and is **NOT independently shippable** — a
   half-migrated state has two seat writers, which is exactly the bug. Must land
   as one large migration. This is the single most expensive part of the plan and
   is NOT required to stop the money-loss bug (see §10).
4. **Promote session machine.** Enforce transitions; wire `paused`/`abandoning`;
   implement disconnect→pause→resume.
5. **Stake lifecycle** onto the same substrate.
6. **Retire reconcilers.** Delete each function in §8 as its bug class becomes
   unrepresentable. `whereabouts.py` degrades from detector to trivial read.

## 8. Inventory — what maps where

State vocabularies found (the implicit machines):

- Presence: `seated / idle / side_hustle / vice / unknown` + stuck flags
  `seated_and_idle, double_seat, seated_and_offgrid, stale_idle,
  seated_too_long, overdue_hustle, overdue_vice` (`cash_mode/whereabouts.py`).
- Session: `active / paused / abandoning / closed / broken` + `closed_status:
  left / boot_swept / stale_swept / ghost_cleanup` (`cash_mode/cash_sessions.py`).
- Stake: `active / carry / settled / defaulted`, `staker_kind`, `borrower_kind`,
  formats `pure / match_share / house`, 5× `DIRECTION_*` flow constants
  (`cash_mode/stake_*.py`).
- Chip custody: **no vocabulary today** — implied by storage location.

Reconcilers (each retired when its class becomes unrepresentable):

| Function | File | Repairs | Retired by phase |
|---|---|---|---|
| `_boot_sweep_stale_cash_rows` | cash_mode/lobby.py:3676 | orphan rows + (wrongly) zeroes chips | 1, 4 |
| `_free_ghost_human_seats` | flask_app/routes/cash_routes.py:411 | ghost human seats | 2, 3 |
| `_reclaim_zombie_casino_seats` | cash_mode/casino_provisioning.py:371 | zombie AI seats | 2, 3 |
| `_restore_cash_table_binding` | flask_app/handlers/game_handler.py:1235 | lost cash_table_id on cold-load | 2 |
| `reseat` / `reseat_readiness` / `_persist_reseat_recovery` | cash_routes.py:2481, movement.py:264, lobby.py:542 | idle→seat re-entry | 2 |
| `cleanup_orphan_cash_games` / `_purge_other_cash_rows` | cash_routes.py:259/336 | duplicate/orphan session rows | 4 |
| `_warm_cash_game_for_leave` | cash_routes.py:4352 | rehydrate orphan for settlement | 1, 4 |
| `_cleanup_stale_games` | game_state_service.py:38 | in-memory eviction | (keep — pure TTL GC) |
| `_drain_fish_bankroll_to_pool` | casino_provisioning.py:697 | fish chips → pool | 1 |
| `whereabouts.py` (whole module) | cash_mode/whereabouts.py | detects all presence contradictions | 2 → trivial read |

Stores that become projections: `cash_idle_pool`, `ai_side_hustle_state`,
`ai_vice_state`, occupancy half of `cash_tables`.

NOT symptoms (stay as-is): `run_retention_sweep`, `cleanup_old_captures`,
`_cleanup_http_client`, `sync_*` loaders, `_cleanup_stale_games` (TTL eviction
of an in-memory cache is legitimate).

### 8.1 Corrections from fact-check (2026-05-30)

The doc was drafted partly from memory; an independent source audit corrected:

- `chip_ledger_entries` is **not** AI-only — it logs several human events; the
  real gap is the human buy-in/cash-out cycle (folded into I1 / Phase 0 above).
- There **is** a client visibility handler (`usePokerGame.ts:875` reconnects the
  socket on tab-visible), so presence isn't purely socket connect/disconnect.
- Stake flow has **7** `DIRECTION_*` constants, not 5.
- Whereabouts has two more stuck flags than listed: `unknown_personality`,
  `no_bankroll`.
- Reaper mechanism, TTLs (14400s / 300s), non-presence-gated watchdog, bare-int
  `player_bankroll_state`, session/stake vocabularies, presence TTL (60s) — all
  verified TRUE at cited lines.

## 9. Decisions (resolved 2026-05-30)

- **D0 — No backward compat for game *save-state*; economy data still migrates.**
  No in-flight games need their old shape preserved. BUT (review correction) the
  live DB holds a running economy — `player_bankroll_state`, `ai_bankroll_state`,
  `cash_sessions`, `cash_tables`, `chip_ledger_entries` (schema at v127). "Greenfield"
  therefore means: add new tables at the next schema version and **backfill from
  current balances** so the ledger is authoritative from day one, then deprecate
  the old shape. It is deferred-and-backfilled migration, not zero migration.
  Restitution (D5) is a separate one-time recovery from backups.
- **D1 — Table as projection. ACCEPTED (occupancy only); payload demotion
  deferred (downgraded 2026-06-01).** Presence is the single authority for *who is
  where*; `cash_tables.seats_json` is a written CACHE for the seat payload (chips,
  archetype, seated_at), committed atomically with presence inside `save_table`.
  Ghost seats are unrepresentable not by derivation but by presence's partial-
  unique seat index (a would-be double-seat write raises `IntegrityError` → the
  `save_table` transaction rolls back). A *true* derived view would require every
  payload field to get a durable home (chips → the ledger; archetype/seated_at → a
  satellite) — high-risk, no bug-class gain over the IntegrityError guard, so it's
  deferred. The read-only `assert_presence_seat_consistency`
  (`cash_mode/presence_consistency.py`) documents/monitors the invariant the
  system now enforces by construction. See `CASH_MODE_PRESENCE_READSIDE_COMPLETION.md`
  + `CASH_MODE_TECH_DEBT.md` §3. (§6.)
- **D2 — Ledger-derived bankroll. ACCEPTED (Option A).** Money moves only by
  writing a balanced ledger entry; bankroll = sum of `IN_BANKROLL` parcels, with
  the integer as an optional cache. Conservation (I1) is enforced, not audited.
  The ~3,957 drift in the human's live balance is precisely the failure mode this
  prevents.
- **D3 — Unified ledger. ACCEPTED.** One `chip_ledger_entries` for humans + AI,
  so conservation is checkable in one place. (No privacy/isolation reason to
  split surfaced.)
- **D4 — Your table FREEZES; it is never sat out or settled by absence.**
  See §5.4. This *replaces* the earlier online-poker "settle-on-disconnect"
  model. The world (other tables) is presence-gated and already pauses ~30s after
  the tab is backgrounded; your table is sacred and frozen. Chips stay `AT_TABLE`
  in the ledger, durable independent of the in-memory game and the `games` row,
  so an indefinite freeze is safe.
  - **D4a — long-term abandonment: FREEZE FOREVER (Option A).** Single-player
    sandbox; no one is waiting for the seat, so the player is never forced up.
    Chips sit safely `AT_TABLE` indefinitely. (Option B — eventual *safe*
    stand-up settling chips to bankroll, never zero — kept as a fallback if a
    frozen table is ever found to harm the broader economy.)
  - **D4b — optional refinement (not load-bearing):** an activity timer that
    pauses the world after ~10 min even with the tab open-but-idle, on top of the
    presence beacon. Nice-to-have, deferred.
- **D5 — Restitution folded into Phase 0. ACCEPTED (Option A).** The two
  incidents are the worked test of the ledger: build it, replay history, and it
  must reconstruct every chip including the swept buy-ins. If it can't, the
  ledger design is wrong. (Simplified: the live economy is resettable test data,
  so restitution = make the bankroll whole on reset; no backup archaeology
  required unless the exact figure is wanted for the captain's-log narrative.)
- **D6 — Cash mode is SINGLE-PLAYER by design. CONFIRMED.** One human per
  sandbox; sandbox : human : world : game are all 1 : 1 (+ AI fill). Multiplayer
  lives in the separate non-cash game rooms and is out of scope. This validates
  the freeze-forever model (§5.4): no other human is ever waiting for the seat,
  so an indefinitely frozen table harms no one. The Presence machine is still
  built entity-agnostic (humans + AIs share it), but the *human* side may assume
  one human per sandbox. If shared-economy multiplayer is ever wanted, the
  freeze model becomes a solo-table special case and shared tables use sit-out —
  a future redesign, explicitly not designed-for now.
- **D7 — History is permanent.** Cash `games` rows (hand history) are kept
  forever; the reaper/GC must never delete a row carrying a resumable session,
  and even dead-row GC must preserve hand history (revisit: GC may need to mean
  "mark closed + de-index", not "delete", to honour D7 — see Phase 1 note).

### Empirical findings (2026-05-30, verified from source)

How presence actually works (no guesses — every claim has a cite):

- **Driven by Socket.IO connect/disconnect**, not an HTTP beacon. `connect` →
  `presence.mark_active`; `disconnect` → `presence.mark_inactive`
  (`flask_app/routes/game_routes.py:2185-2218`). There is no client visibility
  handler.
- **Grace TTL is 60s** — `ACTIVE_TTL_SECONDS = 60.0` (`presence.py:35`). After the
  last socket drops, the owner stays "active" for 60s, then is pruned and the
  ticker stops advancing that sandbox.
- **HTTP fallback keeps the world alive without a socket:** `GET /api/cash/lobby`
  calls `presence.touch()`, and `Lobby.tsx` polls every `LOBBY_REFRESH_INTERVAL_MS`
  (`Lobby.tsx:359`). So an open lobby tab keeps ticking even if the websocket fails.

How long the world runs after you look away (answering the D4 question):

- **Tab closed:** `disconnect` fires → world keeps ticking through the 60s grace,
  then stops.
- **Tab backgrounded:** BROWSER-DEPENDENT, not yet measured. Desktop browsers
  often keep a backgrounded websocket open (world may keep running); mobile
  browsers typically suspend JS and drop the socket after an OS-determined
  interval, then the 60s grace applies. Needs per-platform measurement before we
  state a number. (TODO before relying on any specific value.)

The decoupling that caused the bug:

- **The reaper is NOT presence-gated.** The ticker loop calls
  `_maybe_run_stale_session_watchdog` every 5 min server-side, regardless of
  whether anyone is present (`ticker_service.py:164,186`). It zeroes tables cold
  >4h even with no one online. So *however long the world runs after you look
  away is irrelevant to chip safety* — the chip-destroyer is a separate timer.
  Pausing the world never protected the chips because the destroyer was never part
  of the world. Under the freeze model (§5.4) this is moot: the reaper loses its
  chip-settling job entirely.

## 10. Recommended first cut — the "80% fix" (review-driven)

Three independent reviews agree the money-loss and most split-brain bugs can be
killed WITHOUT the expensive table-as-projection demotion (Phase 3). Recommended
sequencing to stop the bleeding fast, then pursue the clean end-state:

**Cut 1 — stop the bleeding (days, low risk):**
1. Reaper guard: `_boot_sweep_stale_cash_rows` skips `games` rows whose
   `cash_session.session_state == 'active'` (the §5.4 soundness fix). The reaper
   may no longer infer `chips=0` from a missing/old row — it settles only from a
   durable stack or not at all.
2. Session-close idempotency/terminal guards (generalise the `ended_at IS NULL`
   pattern) so no path double-settles or zero-settles a reachable session.

**Cut 2 — make it auditable (SHIPPED 2026-05-30):**
3. Add a human chip *statement*: `player_buy_in` / `player_cash_out` transfer
   rows in `chip_ledger_entries` (`player:<id>` <-> `seat:<game_id>`), via a new
   `record_transfer` (the bank-only `record()` rejects bank-less rows by design).
   Buy-in rows fire at ALL bankroll->seat movements (self-funded sit-down +
   rebuy + top-up via the shared `_increment_cash_session_buy_in` chokepoint);
   cash-out fires at leave for both staked and self-funded. (Review fix: the
   first pass shipped cash-out-only — a one-sided `seat:` account — because a
   half-applied edit dropped the buy-in wiring. Caught by code review, now
   covered by a seat-account-balances regression test.)
   **Key correction found during Cut 2:** human buy-in/cash-out is ALREADY
   conservation-safe — the audit counts both `player_bankrolls` (debit) and
   `live_session_human_stacks` (seat), so it's a transfer, not a mint. These rows
   change NOTHING in the drift math (neither side is `central_bank`); they exist
   purely as the readable transaction history the silent-forfeiture bug exposed as
   missing. So Cut 2 is a *statement*, not a conservation fix (I1 already held for
   humans). Restitution (D5): the economy is resettable test data, so make the
   bankroll whole on reset; the past loss predates the statement and isn't
   reconstructable from it, but all future sessions are now auditable.

**Cut 3 — kill split-brain cheaply (partial Phase 2):**
4. A single `entity_presence` row per `(entity, sandbox)` with a DB uniqueness
   constraint, replacing `cash_idle_pool` as the occupancy authority — makes
   `seated_and_idle` unrepresentable without yet demoting the whole seat map.

**Later — the clean end-state:** full Presence/Custody machines + table-as-
projection (Phase 3) + reconciler retirement. Worth doing, but it is the 20% that
costs 80% of the effort; it should not block Cuts 1–3.

This does not change any of the locked decisions (D1–D5) — it stages them so the
production money-loss is fixed in days, not after a multi-week refactor.

## 11. Worked examples (the two incidents under the new model)

- **The swept $2k (freeze model).** Close the tab → ambient world pauses ~30s
  later; **your table freezes mid-hand**, seat held, chips remain `AT_TABLE`
  parcels in the ledger. Hours pass. The in-memory game is evicted and even if
  the stale `games` row is GC'd, the chips are still recorded `AT_TABLE`. The
  reaper has **no transition that can zero them** — its chip-settling job no
  longer exists. You return → resume the exact hand. **Chips never at risk.**
  I2 holds. (Contrast: today the reaper finalised at 0 and deleted the only
  record of the stack.)
- **`seated_and_idle`.** Unrepresentable: presence is one state. The idle pool is
  a projection — it cannot assert "idle" about an entity the presence machine has
  as `SEATED`.

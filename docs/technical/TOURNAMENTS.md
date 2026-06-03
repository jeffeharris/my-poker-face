---
purpose: Architecture of the multi-table tournament engine and the tournaments-as-a-draw economy (reserve/vacate/spawn, draw scoring, renown grant, funding) — including which layers are flag-gated inert today
type: architecture
created: 2026-06-03
last_updated: 2026-06-03
---

# Tournaments

There are two systems wearing one name. The **engine** (`tournament/`) is a pure,
chip-conserving multi-table orchestrator that plays out a field with no LLM and no
I/O — it is live today behind the standalone `/tournament` UI and as the single-table
"envelope" every non-cash game gets. Layered on top is **tournaments-as-a-draw**: an
economy mechanic where AI personas *leave cash tables* to enter a Main Event, pulled
by a draw score, funded by draining the bank's excess reserves, paying renown to the
winners. That second system is **built but flag-gated OFF** — every claim below marks
what is live vs. inert.

The framing — "a tournament is a controlled chip-redistribution event the bank
triggers when it's FLUSH, not a calendar event" — comes from the design plan
(`docs/plans/TOURNAMENTS_AS_A_DRAW.md`, `docs/captains-log/tournaments/`). The code is
the ground truth where they disagree.

Cross-links: chip pools / conservation / ledger reasons live in
[`CASH_MODE_ECONOMY.md`](CASH_MODE_ECONOMY.md); how a persona's location (seated /
idle / tournament-bound) is made unrepresentable-when-wrong lives in
[`PRESENCE_WHEREABOUTS.md`](PRESENCE_WHEREABOUTS.md).

## 1. Layer map

| Layer | Where | Role |
|---|---|---|
| Pure engine | `tournament/` | Field, seating, hand-resolver protocol, prize math, name resolution. No Flask, no DB. |
| Services | `flask_app/services/tournament_*.py` | Registry, spawn, invite lifecycle, draw scoring, economy (real chips), renown, world-tick hook, field draft. |
| Persistence | `poker/repositories/tournament_*_repository.py` | `tournaments` + `tournament_invites` tables; legacy career-stat tables. |
| Cash seam | `cash_mode/movement.py`, `lobby.py`, `whereabouts.py` | `called_up` leave primitive + reserved/on-tournament whereabouts. |
| Economy signal | `core/economy/economy_signal.py` | The FLUSH/NEUTRAL/EMPTY chairman that decides *when* and *how big*. |
| Frontend | `react/.../MainEventCard.tsx` | Lobby invite card (polls `GET /api/tournament/invite`). |

## 2. The engine (`tournament/`)

The engine has no poker dependency — `TournamentDirector` talks only to a
`HandResolver` protocol (`tournament/director.py:52`). One round = one hand at every
table, results folded into field-wide standings, then a rebalance.

**Hand resolvers.** `FakeHandResolver` (`director.py:74`) is a deterministic,
chip-conserving stand-in: every seated player posts the big blind, the pot is awarded
to a stack-weighted random winner so chip leaders win more and the field converges.
It is *not poker* — and that is the point. Per the design notes, v1 autonomous
tournaments carry **0 LLM cost** because the persona's poker skill is irrelevant to
funny-money hands; only its economic identity matters for payout routing
(`docs/captains-log/tournaments/`). The real engine variant is `EngineHandResolver`
(`engine_resolver.py`, no-LLM bots). The resolver contract is the only invariant:
returned stacks sum to input stacks (`director.py:59`).

**Conservation.** `TournamentField.assert_conservation()` fires after every round
(`tournament/field.py`); the per-table resolver result is also guarded. This is the
engine's load-bearing invariant — same discipline as the cash ledger
([`CASH_MODE_ECONOMY.md`](CASH_MODE_ECONOMY.md)).

**Seating.** `SeatingManager.rebalance()` (`tournament/seating.py:167`) applies
classic MTT rules: drop empty tables → final-table consolidation → break smallest
and balance. A `Table` holds `seats: list[str | None]` and a button *seat index*
that snaps past empties.

**Field identity is `personality_id`, not display name.** `build_initial_state`
(`director.py:24`) generates synthetic `P01..PNN` ids for headless runs, or takes an
ordered `entries: {player_id -> archetype}` map of real personas. Per the design
notes, `personality_id` is the stable economic address (it keys `ai_bankroll_state`,
`prestige_snapshots`, relationships); display names are resolved at *read* time via
`resolve_display_name` (`tournament/identity.py:41`). The same function refuses to
`.title()`-mangle real names on the standings path (`humanize_fallback=False`) but
humanizes a bare slug (`sun_tzu → "Sun Tzu"`) on the felt. Human seats never route
through `load_personality_by_id` — they return `owner_name` verbatim (career
invariant). Note: `entries`' *value* is a solver-archetype string (e.g.
`"calling_station"`) the fake resolver consumes, **not** the persona's name.

**Prize math** (`tournament/economy.py`, pure, zero I/O):
- `PAYOUT_FRACTION = 0.30` (`economy.py:21`) — `paid_places_for` pays ~top 30%.
- `DEFAULT_PAYOUT_CURVE = (0.38, 0.24, 0.15)` (`economy.py:26`) — 38/24/15% front, the
  rest split equally; `compute_payout_schedule` adds the integer rounding residual to
  1st place so the schedule sums **exactly** to the prize pool (escrow nets to 0,
  `economy.py:83`).
- Distinct from the session's `IN_THE_MONEY_FRACTION = 0.15` (`session.py:43`) — a
  **display-only** ITM cutoff that predates real payouts. Two different fractions,
  intentionally.

## 3. The live-human seam (`session.py`)

`TournamentSession` wraps the headless engine for the player path.

- **Pacing.** `PACING_CHOICES = (0, 1, 1, 2)` (`session.py:37`) — per human hand, each
  AI table plays a jittered count with mean exactly 1.0, so the field tracks the human
  without drifting.
- **Player-gated time.** Per the design notes, when the human is *in* a tournament
  there is **no background advance** — backing out to standings pauses the whole
  field. The world tick only advances *autonomous* (no-human) tournaments. This was a
  deliberate product call: the human's position is too consequential to shift while
  they read standings.
- **Field locked at spawn.** `__init__` builds entries/field/seating once; there is no
  "add participant" API. This constraint is *why* the draw mechanic must be
  RESERVE → VACATE → SPAWN (§5): the cast is chosen before spawn, vacating completes
  before spawn, spawn is atomic. "Trickle in" can never mean incrementally seating a
  live field.
- **Serialization.** `to_dict`/`from_dict` persist entries but **not** the resolver
  (rebuilt from `resolver_kind`). `from_dict` re-passes saved `entries` so a
  real-persona cold-load doesn't regenerate synthetic `P##` ids.

## 4. Services & persistence

### Registry (`tournament_registry.py`)
Process-local `dict` + per-tournament lock, with DB cold-load fallback rehydrating
via `TournamentSession.from_dict`. Every **non-cash** game also gets a lightweight
`tournaments` row with `resolver_kind='single'` (the single-table "envelope") so all
games share one identity table; these are **not** rehydrated as MTT sessions and are
excluded from `find_active_for_owner`.

### Persistence
- `tournaments` table — `tournament_session_repository.py`. Economy columns
  (`buy_in, rake, bank_overlay, prize_pool, payout_status`) written by `set_economy`
  *separately* from `save()` so routine hand-boundary saves never wipe funding data.
  `active_participant_pids` is recency-bounded by `EXCLUSION_MAX_AGE_HOURS = 6`
  (`tournament_session_repository.py:35`) so an abandoned tournament doesn't ghost-seat
  its field forever.
- `tournament_invites` table — `tournament_invite_repository.py`. Status lifecycle
  `offered → accepted | declined | expired`. **Schema v148** added `reserved_pids` +
  `vacated_pids` JSON columns (`schema_manager.py:7370`, `SCHEMA_VERSION = 148` at
  `schema_manager.py:339`). Reads guard on `PRAGMA table_info` so a pre-v148 DB returns
  an empty set rather than throwing (`reserved_pids_for_owner`).
- `tournament_repository.py` — **legacy** single-table career stats
  (`tournament_results`, `tournament_standings`, `player_career_stats`). Pre-MTT, still
  live for career tracking after completion.

### Spawn (`tournament_spawn.py`)
Two creation paths plus the per-tick advance:
- `spawn_autonomous_tournament` — no human, `FakeHandResolver`; every finisher credited
  as `ai:<pid>`.
- `create_human_tournament` — human in seat 0, charges buy-in via `apply_buy_in`.
- `advance_autonomous_tournament` / `settle_autonomous_tournament` — one round per tick,
  settle on completion.
- `draft_exclusions` — the double-presence guard: union of cash-seated pids +
  active-tournament participants + open-invite reserved pids. **Fails closed** — any
  scan failure raises `DraftScanError` and aborts the spawn rather than risk
  double-presence.

Orphan guard: both creation paths **delete their just-written `active` row** if funding
fails, so a failed registration leaves nothing that would block re-offer under the
one-active-per-owner rule.

## 5. Tournaments-as-a-draw: RESERVE → VACATE → SPAWN

The cash→tournament migration mechanic. **All of §5–§7 below is gated OFF by default**
(see §8). The phases (A–D, per `docs/plans/TOURNAMENTS_AS_A_DRAW.md`):

```
FLUSH bank ──► OFFER invite ──► RESERVE draw field ──► trickle VACATE cash seats ──► SPAWN at expiry ──► payout + renown
   (signal)      (chairman)        (Phase B)              (Phase A+C)                  (expire_due)        (Phase D)
```

### RESERVE — draw scoring (Phase B, `tournament_draw.py`)
At `offer()`, `_reserve_draw_field` (`tournament_invites.py:96`) scores the eligible
pool and stores the top-`field_size` as the invite's `reserved_pids`. The score
(`score_draw`, `tournament_draw.py:80`) is four clamped-[0,1] terms:

```
score = w_prize·prize_appeal + w_renown·renown_appeal + w_field·field_appeal − w_comfort·cash_comfort
```

- `prize_appeal = clamp(prize_pool / own_bankroll)` — a tiny-bankroll fish maxes it.
- `renown_appeal = renown_on_offer · status_appetite · (1 − own_renown)` — low-renown
  personas have the most to gain.
- `field_appeal = field_top_renown · (1 − own_renown)` — playing with the bigs pulls
  those who aren't bigs.
- `cash_comfort` (subtracted) = `seat_chips / starting_stack`. Per the design notes,
  this is a **seat-depth proxy**, not net winnings (no net-winnings signal exists at
  offer time): deep at a good seat = comfortable = harder to pull.

Default weights `DrawWeights(prize=0.40, renown=0.25, field=0.15, cash_comfort=0.20)`
(`tournament_draw.py:71`) are **starting values, not sim-tuned** — see §9.
`rank_field` (`tournament_draw.py:101`) adds Gaussian jitter (`noise_sigma=0.03`) so
successive Main Events don't field the identical cast when scores cluster; `rng=None`
gives a deterministic ranking for tests. `build_draw_inputs` (`tournament_draw.py:178`)
is the one effectful function: it reads renown peaks only when `RENOWN_V2_PERSIST_AI`
is also on, and degrades gracefully on any repo failure.

### VACATE — the `called_up` primitive (Phase A + C)
`cash_mode/movement.py:202` defines `CALLED_UP = "called_up"`. At `movement.py:1357`,
when `called_up_pids and pid in called_up_pids`, the movement decision is
**unconditionally** set to `CALLED_UP`, overriding fish coercion, predator retention,
take-stake interception, and rebuy. The persona vacates, settles to bankroll, and does
**not** rejoin the idle pool (it's not re-seatable). The lobby threads this via
`refresh_all_tables_roster(called_up_pids=…)`.

The source of `called_up_pids` is `bound_pids` (`tournament_invites.py:63`), which
returns the open invite's `reserved_pids` **only** when `TOURNAMENT_DRAW_ENABLED` AND
the invite has an `expires_at` (`open_invite_for_gather`, `tournament_invites.py:40`).
`game_handler._tournament_bound_pids` (`game_handler.py:142`) wires it into both the
seat-fill exclusion and the force-leave set.

**No-stranding guarantee:** the `expires_at` gate means an invite that *will* spawn is
the only one that pulls personas off cash. An invite kept open indefinitely
(`expires_at = NULL`) is never gathered, so a vacated persona never lands in limbo with
no tournament to join. `whereabouts.py` surfaces this state:
`STATUS_TOURNAMENT_BOUND = "tournament_bound"` (`whereabouts.py:55`) plus `reserved` /
`reserved_expired` / `on_tournament` sets (`whereabouts.py:307+`) — see
[`PRESENCE_WHEREABOUTS.md`](PRESENCE_WHEREABOUTS.md).

### SPAWN
`expire_due` (sandbox-scoped) fires at the registration window's end and spawns the
autonomous tournament. The reserved field is drafted first via
`tournament_field.select_persona_field`; the remainder is shuffled in.

### Renown grant on payout (Phase D, `tournament_renown.py`)
`grant_on_payout` (`tournament_renown.py:134`, `DEFAULT_WIN_RENOWN = 1.0` at
`tournament_renown.py:36`) credits renown to in-the-money finishers, using the **same**
`paid_places_for` as the chip payout so paid-places never diverge. Linear curve from
1st (`base`) down to the bubble. It runs **inside the once-only payout block** (the
`claim_payout` CAS, §6), in its own `try/except` *after* chips distribute — so it fires
exactly once and a grant failure can never strand chips or affect payout status.

## 6. Real-chip economy (`tournament_economy_service.py`)

The sole real-chip authority. Every method requires the caller to hold
`get_sandbox_lock(sandbox_id)`. Funding plan comes from the economy chairman
(`plan_funding` → `economy_signal.tournament_funding`).

**Buy-in** (`apply_buy_in`): debit human bankroll → `set_economy` (stamps
`payout_status`) → write ledger rows → verify escrow balance; re-credits and raises on
any failure.

**Payout** (`apply_payout_on_complete`, `tournament_economy_service.py:205`) — the I6
idempotent terminal transition:
- Guard: `payout_status == 'pending'` (anything else = no-op, `:256`).
- CAS `pending → in_progress` via `claim_payout` (`tournament_session_repository.py:247`,
  atomic `UPDATE … WHERE payout_status='pending'`). The first caller wins; the loser
  no-ops. This is the authoritative double-settle guard — the cash double-settle lesson
  applied: **status flag flips before any bankroll write**.
- Three finisher kinds (`:228`): the human → global player bankroll; a real AI persona
  (`pid in real_persona_ids`) → its `ai:<pid>` bankroll (the actual redistribution:
  overlay → persona → back through cash tables); a synthetic `P##` seat → swept to bank
  (`tournament_return`, keeps escrow at 0).
- Ledger row written **first**, then bankroll cache. Never raises — a mid-flight failure
  leaves `in_progress` for a reconcile pass.

**Reconcile** (`reconcile_stuck_payout`): resumes `in_progress` payouts, ledger-
authoritative (pays only `owed − already_paid` per sink). Deliberately does **not**
grant renown — on a crash-after-grant window a re-grant would double-bump the ratcheted
renown peak; per the design notes, a one-off skip on a rare crash beats any double-bump
risk.

## 7. When & how big — the economy chairman (`economy_signal.py`)

One `EconomyState` snapshot, read once per decision under the sandbox lock, drives both
the tournament overlay *and* the cash-rake schedule, so the two levers can't oscillate
against each other.

Regimes bucketed by `reserves / holdings`:
- `FLUSH_SETPOINT = 0.08` (`economy_signal.py:48`) — at/above → distribute.
- `EMPTY_SETPOINT = 0.02` (`economy_signal.py:53`) — at/below → refill via rake.
- A cold/empty universe reports NEUTRAL, not EMPTY (`signal()`, `:125`).

**Funding** (`tournament_funding`, `:154`):
- **FLUSH** → `overlay = min(max(0, reserves − FLUSH_SETPOINT·holdings), OVERLAY_CAP)`,
  rake 0. This is **drain-to-setpoint**: each event resets reserves to the setpoint, a
  self-limiting sawtooth. `OVERLAY_CAP = 250_000` (`:66`) so no single event empties the
  coffers.
- **NEUTRAL** → buy-ins only. **EMPTY** → refill rake.
- `ai_buy_in_total` is 0 in v1 (AI seats are bank-distributed via overlay, not charged).
  Escrow contract: `prize_pool == human_buy_in + ai_buy_in_total + bank_overlay − rake`.

> **Why drain-to-setpoint, not per-tick percentage.** Per `EXP_006` §6 (cited in
> `economy_signal.py:54–62`): the per-tick `reserves × OVERLAY_DRAIN_PCT` law (0.02,
> retained as `OVERLAY_DRAIN_PCT` for reference) is **~225× too weak** across the 30-min
> cooldown — the bank balloons (slope ~99 chips/tick). Drain-to-setpoint held the band
> (slope ~6–12, 3 seeds, conservation-clean). The constants transfer from EXP_006; the
> *cadence* still needs re-validation before flipping on.

**When** (`should_offer_event`, `:257`): offer iff **FLUSH and cooldown elapsed**.
NEUTRAL/EMPTY offer nothing in v1. The default offer is a freeroll —
`DEFAULT_MAIN_EVENT = EventSpec(field_size=18, table_size=6, starting_stack=10_000,
buy_in=0)` (`:239`). `MAIN_EVENT_COOLDOWN_SECONDS = 1800` (30 min, `:245`);
`MAIN_EVENT_REGISTRATION_WINDOW_SECONDS = 600` (10 min, `:254`).

**World-tick hook** (`ticker_service._maybe_tick_tournament`, `ticker_service.py:712`,
flag-gated): per active sandbox under its lock — (a) `expire_due` + `maybe_offer_main_event`,
(b) `advance_owner_tournament` (one round, settle if complete, collect structural
beats). A separate payout-reconcile watchdog (`_maybe_run_payout_reconcile_watchdog`,
`ticker_service.py:243`) runs every `PAYOUT_RECONCILE_INTERVAL_SECONDS = 300`
(`ticker_service.py:129`). Lock discipline: the ticker holds only the sandbox lock (no
registry lock) to avoid inversion with the `/advance` route.

## 8. Gating flags — what is live vs. inert

All in `cash_mode/economy_flags.py`.

| Flag | Default | Gates | Code |
|---|---|---|---|
| `TOURNAMENT_CIRCUIT_ENABLED` | **False** | World-tick invite sweep + autonomous advance + payout watchdog | `economy_flags.py:283` |
| `TOURNAMENT_DRAW_ENABLED` | **False** | Draw reservation, trickle-vacate (`called_up`), `open_invite_for_gather`, `grant_on_payout` | `economy_flags.py:296` |
| `RENOWN_V2_PERSIST_AI` | **False** | Whether the draw scorer reads AI renown peaks (else prize+comfort only) | `economy_flags.py:325` |

**LIVE today (no flag):** the full MTT engine, session, seating, registry, single-table
envelopes, routes, completion, and `TournamentRepository` career-stat writes. The lobby
poll `GET /api/tournament/invite` and `MainEventCard.tsx` already create/serve invites;
the poll itself fires `maybe_offer_main_event` + `expire_due`. `draft_exclusions` and
`claim_payout` CAS are flag-independent.

**DORMANT until `TOURNAMENT_CIRCUIT_ENABLED`:** ticker-driven invite sweep, autonomous
per-tick advance, the 5-min payout-reconcile watchdog.

**DORMANT until `TOURNAMENT_DRAW_ENABLED`:** the draw scorer's field reservation,
trickle-vacate via `called_up_pids` (so `reserved_pids` is always NULL and spawn falls
back to a random draft), `open_invite_for_gather`, and `grant_on_payout` renown bumps.

## 9. Not yet tuned

Per the design notes (`docs/plans/TOURNAMENTS_AS_A_DRAW.md`, MEMORY), these are starting
values awaiting a sim pass (EXP_007 planned, not run):
- Draw weights `(prize=0.40, renown=0.25, field=0.15, comfort=0.20)`.
- Renown magnitude `DEFAULT_WIN_RENOWN = 1.0`.
- The overlay *cadence* (per-tournament vs the per-tick the sim validated) needs
  re-validation before either thermostat flips on in production.

## 10. Invariants (load-bearing)

| Invariant | Mechanism |
|---|---|
| Chip conservation per round | `TournamentField.assert_conservation()` + per-table resolver guard |
| No double-presence (cash vs tournament) | `draft_exclusions` fails closed (`DraftScanError`); `active_participant_pids` recency-bounded 6h |
| Payout idempotency / no double-settle | `claim_payout` CAS `pending→in_progress`; status flips before any bankroll write |
| Escrow nets to 0 | `compute_payout_schedule` residual → 1st; synthetic-seat share swept to bank |
| Renown never strands chips | `grant_on_payout` in its own try/except, after chips, inside the once-block; reconcile skips it |
| No persona vacated into limbo | `open_invite_for_gather` requires `expires_at IS NOT NULL` |
| Failed registration leaves no orphan | spawn/create delete their just-written active row on funding failure |

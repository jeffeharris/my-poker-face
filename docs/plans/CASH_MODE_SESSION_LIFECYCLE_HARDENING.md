---
purpose: Hardening plan for cash-session lifecycle — orphans, partial-teardown ghosts, and the "can't sit at a new table" wedge — laying out atomicity, reconciliation, observability, and the explicit state machine that production needs.
type: design
created: 2026-05-28
last_updated: 2026-05-28
---

# Cash Mode: Session Lifecycle Hardening

> **Status (2026-05-28): Tier 1 + Tier 2 + Tier 3 (minus 3.2) + Tier 4
> (4.2 + 4.3) SHIPPED on this branch.** Only the outbox-pattern teardown
> (3.2) and the inline resume-error card (4.1) remain planned. Triggered
> by a wedge where `guest_jeff` had an
> orphan `cash--7j9cUI...` row that blocked every new sit with "A cash
> session is already active. Leave first." while Resume itself
> two-toasted-then-worked. Cleanup was applied via
> `scripts/cleanup_orphan_cash_session.py` (see incident notes below).
> The underlying class — five tables that must agree, no atomicity, no
> reconciler — keeps producing orphans. This doc is the path to
> "production never sees this."
>
> **What landed (2026-05-28):**
> - **T1.1** lobby "End session" button (`Lobby.tsx`) + leave route now
>   cold-loads a DB-only session and settles it (`_warm_cash_game_for_leave`
>   in `cash_routes.py`) instead of zeroing it.
> - **T1.2** `sponsor_and_sit` persists `cash_table_id`/`cash_seat_index`
>   (`cash_routes.py:~1976`).
> - **T1.3** the cross-table ghost-seat sweep is de-nested so it runs on
>   every leave, including NULL-`cash_table_id` sessions (`cash_routes.py`).
> - **T1.4** resume retries a 404 up to 2× with backoff before bouncing
>   (`usePokerGame.ts`).
> - **T2.1** shipped as an **idempotency guard** (not full cross-repo
>   ACID — see the note in §Tier 2): a leave on an already-finalized
>   session does cleanup-only and never re-credits. This is the guard
>   that would have prevented the phantom-chip double-settle.
> - **T2.2** boot-time orphan sweep (`_boot_sweep_stale_cash_rows` in
>   `cash_mode/lobby.py`, wired through `kill_all_cash_sessions` + the
>   boot hook in `flask_app/__init__.py`): past-TTL `cash-*` rows are
>   settled + deleted at boot; fresh rows preserved for resume.
> - **T2.3** stale-session watchdog (`_maybe_run_stale_session_watchdog`
>   in `ticker_service.py`): the same sweep on a 5-min cadence, skipping
>   in-memory games (resurrection guard), so orphans created between
>   reboots self-clear.
>
> Tests: `test_leave_clears_orphan_seats.py` (+cold-settle, +ghost-fallback,
> +finalized-no-resettle, +NULL-cash_table_id), `test_cash_sponsor_routes.py`
> (+persist cash_table_id), `test_lobby_seeding.py` (+boot sweep class),
> `test_stale_session_watchdog.py` (new). All green; TS + lint clean.
>
> **NOTE on T2.1 scope:** a literal cross-table ACID transaction was
> assessed and deferred — every repo holds its own thread-local SQLite
> connection (`base_repository.py`), so true atomicity would need a
> connection-sharing refactor across all repos. The idempotency guard +
> boot sweep + watchdog together deliver the *convergence* guarantee
> (no double-settle, orphans self-heal) the literal transaction was
> meant to provide. The full outbox/transaction work moves to Tier 3.2.
>
> **What landed for Tier 3 (2026-05-28, schema v119 + v120):**
> - **3.1** explicit `session_state` on `cash_sessions`
>   (`active`/`paused`/`abandoning`/`closed`/`broken`, backfilled from
>   `ended_at`). The sit guard (`_find_active_cash_game_id` →
>   `_cash_session_blocks`) now respects it: a `closed`/`broken` session
>   whose `cash-*` games row lingers no longer wedges new sits;
>   legacy/no-row sessions stay blocking (fail-safe). `finalise` flips
>   state→`closed`; teardown failures (leave route + sweep) flip
>   →`broken` so a partial teardown converges instead of wedging.
> - **3.3** persisted `cash_session_events` table + repo
>   `record_event`/`list_events`; emitted on `started`
>   (record_cash_session_start), `left_clean`/`left_ghost` (leave),
>   `swept` (boot + watchdog, tagged by source), `broken`. Feeds the
>   planned admin orphan-counter (4.3). Distinct from the cosmetic
>   in-memory `cash_mode/activity.py` ring buffer.
> - **3.4** `last_load_error` column, stamped on the `/api/game-state`
>   cold-load 500 path. `set_last_load_error` clears it on success.
>
> Tests: `test_cash_session_repository.py` (+state default / finalise→closed
> / set_session_state / last_load_error / events), new
> `test_cash_session_state_guard.py` (guard ignores closed/broken, blocks
> active/legacy), watchdog test updated for the `stale_swept` tag. 107-test
> cash sweep green. **3.2 (outbox saga) deliberately deferred** — the
> idempotency guard already gives convergence; the saga is a large rewrite
> with its own regression surface.
>
> **What landed for Tier 4 (2026-05-28):**
> - **4.3** admin "Session lifecycle" card on the Chip Economy tab,
>   backed by a new `GET /api/admin/chip-ledger/lifecycle` endpoint that
>   aggregates the Tier-3 `cash_session_events` stream over a window
>   (`event_counts`) plus the current `session_state` distribution
>   (`state_counts`). Headlines started/left/swept/broke over the window
>   + outstanding `broken` count (alert-styled when >0 — the wedge class
>   this plan targets). Closes the observability loop: orphans are now
>   *visible* in prod, not just self-healing.
> - **4.2** lobby Resume bar shows "paused Xm/Xh/Xd ago" from a new
>   `seated_since` field on `/api/cash/lobby` (durable cash_sessions
>   `started_at`, so it works for cold sessions too). The lobby route now
>   loads the session row once for both `seated_since` and the cold-path
>   table/stake fallback.
>
> **4.1 deferred** — the T1.4 retry already absorbs the common transient,
> and bouncing to /cash on a genuinely-gone session is reasonable; the
> inline Retry/End-session card is low marginal value. Tests:
> `test_cash_session_repository` (+event_counts/state_counts),
> `test_cash_lobby_route` cold-session test extended for `seated_since`.
> 116-test cash sweep green; tsc + eslint clean.
>
> Companion to [[CASH_MODE_BACKING_SYSTEM_HANDOFF]] (stake settlement
> math, source-of-truth) and [[../technical/CASH_MODE_FISH_AS_PERSONAS]]
> (persona-funded bankrolls, which the AI cash-out loop credits).
> Does **not** redesign the leave settlement math — that part works.
> What it redesigns is the *lifecycle* the math lives inside.

## The problem this doc fixes

A "cash session" today is five rows across five tables that must all
agree to represent one logical truth ("the player is seated"):

| Table | What it stores | Today's gap |
|---|---|---|
| `games` | The live `state_machine` blob (cash-* prefix) | Survives reboot; sit guard reads it → 409s any new sit |
| `cash_sessions` | Buy-in, sponsor principal, table_id, closed_status, summary | `cash_table_id=NULL` on every sponsor session ([`cash_routes.py:1976`](../../flask_app/routes/cash_routes.py#L1976)) |
| `cash_tables` (seat slot) | Per-seat `{kind: human, personality_id, chips}` | Persisted on sit; freed only on a *successful* leave path |
| `stakes` | Active sponsor stake principal, cut, status | Stays `active` until settle; carry/default both consistent |
| `player_bankroll_state` + `ai_bankroll_state` | Chips currency for both sides | Updated procedurally inside leave; not atomic |

The lifecycle is procedural code, not a state machine. Cleanup does N
sequential writes across N tables. Any process death (Flask reload,
worker crash, raised exception inside leave) between writes leaves
divergent state. The sit guard reads from one of the five tables (the
`games` row) — so a partial cleanup that dropped 4/5 but missed `games`
makes you appear "still seated" forever.

The cold-session-wedge fix shipped on 2026-05-26
([[../../memory/project_cash_cold_session_wedge]]) made the orphan
*visible* in the lobby (good) but didn't add a way to *escape* it
without first successfully loading it (gap). Hence today's wedge.

### Why this keeps happening (mechanism)

In rough priority order:

1. **Backend restart with an active session.**
   [`kill_all_cash_sessions`](../../cash_mode/lobby.py) at boot drops
   in-memory copies on purpose ("resume on reboot is by design"). The
   DB row survives, nothing drives it forward until the next
   `/api/game-state/<id>` GET. If the user never navigates back to it
   (because the lobby's only escape was "click Resume then Leave
   Table" and Resume itself was flaky), the orphan persists.

2. **`progress_game` exception during a street transition.** Game
   persisted in a transitional state; nothing drives it forward.
   Pre-existing class — see
   [[../../memory/project_ephemeral_tourists_coldload_bugs]] for prior
   chained cold-load bugs that left orphans behind.

3. **Teardown crash mid-leave.** Leave succeeded at bankroll/seat
   updates but never reached `game_repo.delete_game(game_id)`. The
   open root from [[../../memory/project_cash_cold_session_wedge]]:
   *"The deeper teardown-failure (why sponsored mid-hand abandons
   leave session+stake+game all active) was NOT fixed."*

4. **Existing latent bugs around `cash_table_id`.** `sponsor-and-sit`
   writes `cash_sessions.cash_table_id = NULL`
   ([`cash_routes.py:1976`](../../flask_app/routes/cash_routes.py#L1976)),
   then the leave path nests the cross-table ghost-seat sweep inside
   `if cash_table_id is not None:`
   ([`cash_routes.py:4256`](../../flask_app/routes/cash_routes.py#L4256))
   — so sponsor-session leaves never sweep their lobby seat. The
   today-orphan landed in `cash_tables` seat #2 holding
   `kind: human, personality_id: guest_jeff` and stayed there until
   the cleanup script ran `_free_ghost_human_seats` manually.

## Design (four tiers, ship in order)

The tiers are independent — each is shippable and load-bearing on its
own. Tier 1 is a today-level escape valve; Tier 4 is observability.
The real architectural answer is Tier 2 + Tier 3 together.

### Tier 1 — User-facing escape valve (small, ship next)

The goal: any orphan a user can see must also be one they can clear in
one tap, without first having to successfully resume.

**1.1 "End session" action on the lobby Resume bar.**
Frontend: a secondary button next to "Resume your $XX session" that
POSTs `/api/cash/leave`. Backend: route already handles cold sessions
via its memory-miss branch
([`cash_routes.py:3931`](../../flask_app/routes/cash_routes.py#L3931))
— takes the `closed_status='ghost_cleanup'` path with `chips_at_table=0`,
sponsor_repaid=0, zero refund.

Decision needed up-front: should "End session" from the lobby
**(a)** run the full leave-settlement path (cold-load the game first
to get the real `chips_at_table`, then run normal settle) or
**(b)** take the ghost-cleanup path (fast, no settlement, sponsor eats
the loss)? **Recommendation: (a)**, with (b) as a fallback if the
cold-load fails or times out. Otherwise users learn to abandon
under-water sessions to dodge stake carries — incentive-incompatible.

**1.2 Patch `sponsor_and_sit` to persist `cash_table_id` / `cash_seat_index`.**
[`cash_routes.py:1976`](../../flask_app/routes/cash_routes.py#L1976)
already has both values in scope (sets them on `game_data` at
[`:1922`](../../flask_app/routes/cash_routes.py#L1922)). One-line
addition to the `_record_cash_session_start` call. Independently
correct; also unblocks the ghost-seat fix below.

**1.3 De-nest the cross-table ghost-seat sweep in leave.**
[`cash_routes.py:4256`](../../flask_app/routes/cash_routes.py#L4256):
`_free_ghost_human_seats` is currently inside `if cash_table_id is not None:`.
Move it after the if-block so it always runs on leave. This is what
today's cleanup script had to do manually.

**1.4 Retry-then-degrade on resume's first 404.**
[`usePokerGame.ts:656`](../../react/react/src/hooks/usePokerGame.ts#L656):
add 1–2 retries with 250–500 ms backoff before firing `handleGameGone()`.
Removes the "two toasts then it worked" UX. Doesn't mask permanent
404s (after retries exhaust, the existing toast + redirect fires).

**Test surface (Tier 1):**
- `test_cash_lobby_route.py::test_end_session_clears_orphan` — POST
  to the new endpoint with a DB-only `cash-*` row → verify the row
  is gone, `cash_sessions.closed_status` is set, and a follow-up
  `/api/cash/sit` succeeds.
- `test_cash_routes.py::test_sponsor_and_sit_persists_cash_table_id`
  — sponsor-flow happy path → asserts `cash_sessions.cash_table_id`
  equals the table the player sat at.
- `test_cash_routes.py::test_leave_frees_ghost_seat_on_null_cash_table_id`
  — regression for the nested-if bug; create a session with
  `cash_table_id=None`, call leave, assert seat is freed.

### Tier 2 — Atomicity and reconciliation (this sprint)

The goal: a process death anywhere inside teardown converges to a
correct final state. No "partial teardown" lingers.

**2.1 Transactional teardown.** Today, `_leave_table_locked` does
sequential writes across `stakes`, `player_bankroll_state`,
`ai_bankroll_state`, `cash_sessions`, `cash_tables`, `games`. Each is
its own SQLite transaction; a `KeyboardInterrupt` / `SIGKILL` / raised
exception between any two leaves divergent state.

Wrap the entire teardown body (settlement → finalize_cash_session →
bankroll writes → seat free → delete_game) in one
`BEGIN IMMEDIATE; ... COMMIT;` so partial state is impossible. The
repos already speak SQLite; this means giving them an optional
"in-this-transaction" hook (or, simpler: drive the transaction from
`_leave_table_locked` and pass the connection down).

Tradeoff: settle_stake_on_leave logs to `chip_ledger` (`forgive_balance`,
`house_stake_settle`). Ledger writes inside the same transaction is
fine; if we want them outside (so a settlement-side failure can't
poison the audit trail) the ledger needs an outbox pattern. **Default
recommendation: same transaction.** Simpler and matches the rest of
the cash mode invariants.

**2.2 Boot-time orphan sweep, not just memory wipe.**
[`kill_all_cash_sessions`](../../cash_mode/lobby.py) at boot today
drops in-memory copies and reconciles orphan *human seats* — that's
it; it leaves `cash-*` rows alone (deliberate, so resume-on-reboot
works). Extend the sweep:

For every `cash-*` row in `games`:

- If `updated_at` within freshness TTL (recommend 30 min) AND
  `game_repo.load_game(game_id)` succeeds → leave alone, eligible for
  Resume.
- If `updated_at` is stale OR `load_game` fails OR the game is in a
  state the recovery path can't auto-advance (no AI controllers
  restorable, all-in-runout stuck without `recover_stuck_runout`
  resolving it) → treat as a *broken* session. Run leave-settlement
  with `chips_at_table=0` (ghost cleanup), mark `cash_sessions.closed_status='boot_swept'`,
  delete the `games` row. Lobby never surfaces it.

This is the **converge-on-truth-at-boot** principle. Whatever
inconsistent state a crash left behind, boot is where it gets
resolved.

**2.3 Stale-session watchdog (heartbeat).** Hook into the existing
world ticker. Every N minutes: for every `cash_sessions` row with
`ended_at IS NULL` AND `updated_at < now − TTL` AND not in
`game_state_service.games` → run ghost cleanup. Adds a third layer
behind (1) explicit user leave and (2) boot sweep — catches sessions
that go stale between boots (active app, just abandoned tab).

**Test surface (Tier 2):**
- `test_cash_routes.py::test_leave_atomic_on_settle_failure` — patch
  `settle_stake_on_leave` to raise after the bankroll write, assert
  no rows changed (rolled back).
- `test_cash_mode/test_lobby.py::test_boot_sweep_clears_stale_orphan`
  — DB has a `cash-*` row older than TTL with no in-memory copy,
  boot runs, row is gone.
- `test_cash_mode/test_lobby.py::test_boot_sweep_preserves_fresh_orphan`
  — DB has a fresh `cash-*` row, boot runs, row is preserved (resume
  path still works).

### Tier 3 — Lifecycle as a real state machine (next sprint)

The goal: replace "five-table consistency by convention" with an
explicit, observable, testable state machine. This is the production
shape; Tier 2 buys time, this is the real fix.

**3.1 `cash_sessions.session_state` column.**

Migration: add `session_state TEXT NOT NULL DEFAULT 'active'`.

State values + valid transitions:

| State | Meaning | Allowed transitions |
|---|---|---|
| `active` | In-memory game, player at the table | `paused`, `abandoning`, `closed`, `broken` |
| `paused` | DB-only, resumable (cold reboot path) | `active` (on resume), `abandoning`, `closed`, `broken` |
| `abandoning` | Teardown in flight | `closed` (success), `broken` (teardown failure) |
| `closed` | Settled, all rows consistent | (terminal) |
| `broken` | Cleanup couldn't converge | (terminal — admin attention) |

The sit guard then reads `session_state IN ('active', 'paused')`
instead of "does any `cash-*` row exist". A `broken` row stops
blocking new sits but stays for diagnosis.

The lobby's `has_active_session` reads `state IN ('active','paused')`.

Cleanup is a *transition to `closed`*, observable and testable —
orthogonal to which physical rows still exist.

**3.2 Outbox-pattern teardown.** Today's leave path is a procedural
sequence the caller drives. Convert to:

1. Leave route: validate, then `INSERT INTO pending_teardowns (game_id, owner_id, requested_at)`.
2. Transition `cash_sessions.session_state` → `abandoning`.
3. A worker (or in-request, with retry) drains `pending_teardowns`
   idempotently: each step keyed on `(game_id, step_name)`.
4. On full drain: transition → `closed`, delete the row.
5. On retry exhaustion: transition → `broken`, alert.

Idempotent because each step is keyed; survives process death because
the row stays in `pending_teardowns` across restarts.

Migration adds one table:

```sql
CREATE TABLE pending_teardowns (
  game_id TEXT NOT NULL,
  step_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  requested_at TIMESTAMP NOT NULL,
  last_attempt_at TIMESTAMP,
  error_message TEXT,
  PRIMARY KEY (game_id, step_name)
);
```

Steps (one row each per teardown): `settle_stake`, `credit_ais`,
`free_seats`, `finalize_session`, `delete_game`, `purge_others`.

This is the **standard outbox pattern** for multi-table operations
that must converge. It's exactly the right shape for "five tables,
one logical truth, must agree."

**3.3 Structured lifecycle events.** Define an enum:

```
cash.session.started
cash.session.paused
cash.session.resumed
cash.session.left_clean
cash.session.left_ghost
cash.session.orphan_detected
cash.session.orphan_swept
cash.session.broken
```

One event per transition, written to a structured log (existing
`event_repository` table works). Replaces the scattered
`logger.info("[CASH] Left ...")` calls. Lets queries like *"how many
orphans per week?"* and *"what's the boot-sweep rate?"* exist.

**Test surface (Tier 3):**
- `test_cash_sessions_state.py` — full state-transition table:
  `active → paused` (on memory eviction), `paused → active` (on
  resume), `active → abandoning → closed` (clean leave),
  `abandoning → broken` (retry exhaust).
- `test_teardown_outbox.py` — idempotency: re-run the worker over a
  half-drained outbox, verify no double-credit.
- `test_lifecycle_events.py` — every state transition emits exactly
  one structured event.

### Tier 4 — UX resilience + observability polish

The goal: the user never sees a silent failure; the admin never needs
log archaeology.

**4.1 Inline error UX, not toast-and-bounce.** Today a single 404 on
`/api/game-state/<id>` → toast + redirect to `/cash`. Replace with an
inline "Couldn't load this session" card with Retry / End session /
Get help buttons. Two transient failures shouldn't strand the user.

**4.2 Lobby surfaces session age + last-update timestamp.** Resume
bar shows "Paused 1h ago at $200 — Resume / End session" instead of
just "Resume your $200 session". Helps the user reason about whether
to resume or abandon.

**4.3 Admin orphan-counter widget.** Sourced from the lifecycle
events stream (Tier 3). Counts: orphans detected per hour, boot
sweeps per restart, `broken` sessions outstanding. Goes on the same
admin panel as the chip-economy widgets.

**4.4 `cash_sessions.last_load_error` column.** When cold-load
500s, stash error class + timestamp + traceback summary. Lets
production debugging skip log archaeology.

## What this doc deliberately does **not** redesign

- **Leave-time stake settlement math** — `settle_stake_on_leave` is
  correct; see [[CASH_MODE_BACKING_SYSTEM_HANDOFF]] for that surface.
- **Cash-table seat structure** — the
  `{kind, personality_id, chips}` slot dicts work; we just need them
  freed on every cleanup path.
- **AI bankroll model** — `ai_bankroll_state` is fine; the leave
  loop's `credit_ai_cash_out` is the right shape.
- **`game_state_service.games`** — in-memory map is fine; the issue
  is *what drives sessions out of that map* (eviction, restart) and
  the lack of a *reconciler* on the way back in.

## Build order

1. **Tier 1 (1–2 days):** ship the user-facing escape valve + the two
   bug fixes. Today's wedge stops being reachable; future orphans
   become a one-tap clear instead of a stuck-forever block.
2. **Tier 2 (1 sprint):** transactional teardown + boot sweep +
   watchdog. Future orphans become rare; the ones that do appear
   self-clear within minutes.
3. **Tier 4.1 + 4.2 (parallel with Tier 2):** inline error UX, lobby
   age display. Small frontend work, mostly read-only.
4. **Tier 3 (next sprint):** explicit state machine + outbox +
   structured events. The state machine column + outbox migrations
   need their own test pass; structured events feed Tier 4.3.
5. **Tier 4.3 + 4.4 (parallel with Tier 3):** orphan counter + load
   errors column. Observability layer.

## Incident notes (2026-05-28)

Today's wedge specifics, for future archaeology:

- **Orphan:** `cash--7j9cUI_JR_WA4BUhc-Avw`, `guest_jeff`, $200
  sponsor session, principal $20,000, staker `bill_clinton` (12%
  cut, pure format).
- **Cause:** session started 01:21:32, last touched 01:22:15 (43-sec
  window). Game state was preflop-to-flop transition, `awaiting_action=False`.
  Most likely killed by a backend restart during dev iteration.
- **Symptom (today):** Resume worked after 2 failed retries with
  toast "Your cash session ended — back to the cash menu"; every
  `/api/cash/sit` 409'd with "A cash session is already active."
- **Cleanup:** ran
  [`scripts/cleanup_orphan_cash_session.py`](../../scripts/cleanup_orphan_cash_session.py)
  — warmed game into a separate process's memory, ran the production
  leave path (`_leave_table_locked`), explicitly ran
  `_free_ghost_human_seats` to work around the nested-if bug.
  Settlement: stake → `carry`, carry_amount=$2,692 (jeff still owes
  bill_clinton $2,692 — surfaces via the existing wallet/carry UI).
  bill_clinton bankroll +17,308, blackbeard +40,277, jeff unchanged
  (pure sponsor session, no own chips invested). Rollback at
  `data/orphan_cleanup_rollback_2026-05-28T025510966811.json`.
- **Hardening status:** none of Tier 1-4 are landed yet. The
  cleanup script is reusable: pass `--game-id` and `--owner-id` for
  any future wedged session, but keep the failures coming until at
  least Tier 1 ships so we learn the failure modes.

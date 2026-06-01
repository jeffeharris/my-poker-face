---
purpose: Design + fresh-context handoff for completing the read side of the cash-mode Presence cutover — what "finishing" actually means given the payload constraint, the maintainability verdict, and a concrete phased plan
type: design
created: 2026-06-01
last_updated: 2026-06-01
---

# Cash Mode — Presence Read-Side Completion (design + handoff)

Pick up here cold. This designs the remaining read-side work for the Presence
cutover and answers the harder question behind it: **given presence can't subsume
`cash_tables.seats` and the reconcilers can't be trivially deleted, is there a
data-architecture problem — and what's the genuinely maintainable target?**

Companion docs: `CASH_MODE_STATE_MODEL.md` (the two-machine design + invariants),
`CASH_MODE_PRESENCE_PHASE3_FLIP.md` (the authority flip + soak findings),
`CASH_MODE_CHIP_CUSTODY_SCOPE.md` (the unbuilt second machine).

## TL;DR

- The Presence **write/authority** side is DONE and flipped on dev (authoritative
  for seat occupancy + idle reads). The "read-side completion" is smaller and
  different than the design's "table as projection" implied.
- **The crux constraint:** `cash_tables.seats` carries load-bearing payload —
  `chips`, `archetype='fish'`, `seated_at` — that presence does NOT hold and that
  has **no other durable store** (the bankroll is the *off-table* chip store;
  `seats.chips` is the *only* on-table store). So a *pure* "derive seats from
  presence" projection is not viable. Any projection is a MERGE of presence
  occupancy + this payload.
- **Verdict (the maintainability question):** there is a real smell, but it
  decomposes into three distinct issues with three distinct fixes — and the
  pragmatic plan below *structurally* fixes the one that actually causes the
  reconcilers, while explicitly deferring the two that are larger investments.
  The current layered model is a **pragmatically fine end-state**, not a fire.

## The data-architecture smell, decomposed

The inability to delete the reconcilers or project the seat map is the tell. It
is **three** problems, not one:

1. **Occupancy is duplicated** (`entity_presence` + `cash_tables.seats`). Root
   cause: presence was deliberately built as a **pure location-only** machine
   (no payload) — elegant for the `seated_and_idle` invariant, but it forces
   `cash_tables.seats` to remain as the payload store, so "who is at seat X"
   lives in two places. The purity decision *created* the duplication. **The plan
   ACCEPTS this** (occupancy-authority + payload-cache) rather than chasing a
   costly full projection.
2. **Chips are modeled 3–4 ways** (`seats.chips`, the ledger `seat(game_id)`
   balance, the live game `Player.stack`, the bankroll int). This is exactly what
   the **chip-custody machine** consolidates (ledger-derived chips). **Deferred —
   it's the separately-scoped second machine** (`CASH_MODE_CHIP_CUSTODY_SCOPE.md`),
   not part of the presence read side.
3. **Cross-aggregate deletes leave dangling references** (delete a `games` row or
   a persona → an orphaned `cash_tables` seat → a reconciler sweeps it). This is a
   **referential-integrity** gap, NOT a state-machine gap. The reconcilers are
   compensating for the absence of deletion-time cleanup. **The plan FIXES this
   structurally** via presence-cleanup hooks at the two deletion sites — which is
   what then lets the reconcilers retire.

So: the pragmatic completion solves #3 (the reconciler smell) for real, formally
accepts #1, and points #2 at its own scope. The *fully-normalized* alternative
(§"Deeper alternative") is a real option if you want it, but it's a bigger bet
with little marginal safety over what's already enforced.

## Seat-map projection — the recommendation

**Do NOT pursue a true projection. Adopt "occupancy-authority / payload-cache" as
the formal end-state, and downgrade design decision D1.**

- `entity_presence` = the single authority for *who is where*.
- `cash_tables.seats_json` = a written cache for *the seat payload* (chips,
  archetype, seated_at), written atomically with presence inside `save_table`'s
  transaction (already the case under `emit_presence_transitions_for_save`).
- Ghost/double seats are already **unrepresentable** under authority: a seat write
  that would create one is rejected by presence's partial-unique index →
  `IntegrityError` → the whole `save_table` rolls back. That guard is the
  projection's bug-kill, without the derivation.
- **Action:** downgrade `CASH_MODE_STATE_MODEL.md` D1 from "ACCEPTED" to
  "ACCEPTED (occupancy only); payload demotion deferred to the chip-custody
  machine." Add a read-only **consistency assertion** (below) that documents the
  invariant the system now enforces by construction.

A *true* projection would require every payload field to get a durable home
(chips→ledger/`seat_stacks` = the chip-custody machine; archetype/seated_at→a
presence satellite). That's 4–6 weeks of high-risk migration for no bug-class gain
over the IntegrityError guard. Not worth it.

### The consistency assertion (the concrete "projection" deliverable)
A pure read-only checker — no schema change — that documents/monitors the
invariant: every `entity_presence` SEATED row ⇔ a non-open/non-reserved slot at
that `(table_id, seat_index)` in `cash_tables`, and vice versa. Lives in
`cash_mode/presence_transitions.py` (or a new `presence_consistency.py`); reused
by `scripts/audit_presence_divergence.py` and tests.

## Reconciler retirement — the two cross-system hooks (the real #3 fix)

The reconcilers fired **0× over a multi-hour authority soak** but are kept because
they guard deletions presence doesn't see. Add the hooks, soak, then delete:

- **`delete_game` hook** (`poker/repositories/game_repository.py:300`): on cash
  game-row deletion, emit `GO_OFFLINE` for `player:<owner_id>` + clear the
  `cash_tables` seat. `delete_game` takes only `game_id` today; add an optional
  `sandbox_id` (no-op when absent → safe for non-cash deletions); the 6 call
  sites in `cash_routes.py` have it in scope. → unblocks deleting
  `_free_ghost_human_seats` (`cash_routes.py:411`).
- **`delete_personality` sweep**: persona deletion is routed through
  `flask_app/routes/personality_routes.py:393` (`delete_personality(name)` →
  `extensions.personality_repo.delete_personality`). Add
  `cash_mode/presence_sweep.py:sweep_presence_on_persona_delete(pid, *,
  sandbox_id, repos)` called from that route BEFORE the delete: if the AI is
  SEATED, `save_table` the seat open (drives `RETURN_TO_POOL`/`GO_OFFLINE`); keep
  `personality_repository` repo-free. → unblocks deleting
  `_reclaim_zombie_casino_seats` (`casino_provisioning.py:374`).
- **`_restore_cash_table_binding`** (`game_handler.py:1235`): already presence-
  first under authority — done. Its `cash_sessions` fallback stays until all
  pre-flip games have ended (it's the only game_id→table binding for those).

Retirement order: add hook → soak (confirm continued 0 fires) → reduce the
reconciler to a logging stub for one cycle → delete. These are referential-
integrity fixes; they make the orphan **unrepresentable**, not just swept.

## whereabouts — STATUS from presence, DETAIL overlay

`build_whereabouts` (`cash_mode/whereabouts.py:198`) unions four legacy tables.
Under authority, switch STATUS to a single `entity_presence` query and overlay
DETAIL from the payload stores (seat chips/seated_at from `cash_tables`; idle
reason/target from `cash_idle_metadata` — already presence-derived via
`list_idle`; off-grid from `ai_*_state`). The HARD flags (`seated_and_idle`,
`double_seat`) become structurally absent → the function becomes a cache-vs-
presence **consistency monitor** (log if they ever fire). Gate on
`PRESENCE_AUTHORITY_ENABLED`; authority-off path unchanged.

> **CAVEAT (verified):** off-grid writers (`ai_side_hustle.py:410/506`,
> `ai_vice_spending.py:640/1098`) use `presence_shadow.shadow_transition` —
> **best-effort, never raises** — even under authority. So presence's
> `SIDE_HUSTLE`/`VICE` state can lag/desync (observed: a stale `side_hustle` for
> an expired hustle on an idle sandbox). **whereabouts' presence-first STATUS for
> off-grid is therefore unreliable** until those writers are promoted to
> authoritative (`persist_transition`, fail-loud) OR whereabouts keeps reading
> `ai_*_state` directly for off-grid STATUS (recommended: derive seated/idle from
> presence, keep off-grid STATUS from the legacy tables, during the soak — and
> emit both as `status`/`legacy_status` to surface divergence).

## Deeper alternative (if you want the genuinely-normalized model)

Only worth it if "one source of truth" is a goal in itself; it does not kill a
bug class the guard doesn't already cover. The clean target:
- **One seated-parcel authority that carries the payload** (chips/archetype/
  seated_at on the seated row, or a `seat_state` table presence projects from) →
  `cash_tables.seats` becomes a true derived VIEW (kills smell #1). Open question:
  does folding payload onto the seated row break the pure-machine invariant?
  Likely not — the compound PK + partial-unique index still hold; payload columns
  are just nullable-when-not-seated, the same shape `cash_idle_metadata` already
  uses as a satellite. A `seat_state` satellite (presence stays pure, payload
  beside it, view joins them) is the lower-risk form.
- **Ledger-derived chips** = the chip-custody machine (smell #2).
- **FK cascade / deletion integrity** = the hooks above (smell #3), the one piece
  shared with the pragmatic plan.

Verdict: the pragmatic plan IS the recommended end-state for now. The normalized
model is a deliberate future investment (good no-users window), tracked but not
required.

## Build sequence (each independently testable; all behind `PRESENCE_AUTHORITY_ENABLED`)

- **R1 — consistency assertion + D1 downgrade** (1–2 days, ~zero risk): add
  `assert_presence_seat_consistency(conn, sandbox_id)`; wire into
  `audit_presence_divergence.py`; update D1 wording; one round-trip test.
- **R2 — whereabouts presence-first** (2–3 days, low risk): new
  `_build_whereabouts_presence()` gated on the flag; **keep off-grid STATUS from
  `ai_*_state`** per the caveat; test with the flag monkeypatched.
- **R3a — `delete_game` GO_OFFLINE hook** (low risk): `sandbox_id` param + hook +
  update 6 call sites + test (delete game → no SEATED presence row).
- **R3b — `delete_personality` sweep** (low risk): `presence_sweep.py` wired into
  `personality_routes.py:393` + test (delete seated persona → seat open, no row).
- **R4 — retire reconcilers** (after R3 soaks at 0 fires): stub → confirm →
  delete `_free_ghost_human_seats` + `_reclaim_zombie_casino_seats`; mark
  `CASH_MODE_STATE_MODEL.md §8` retired.
- **(optional R5) off-grid writers → authoritative** — promote the 4
  `shadow_transition` calls to fail-loud `persist_transition` under authority, so
  off-grid presence is reliable and whereabouts R2 can use presence for ALL
  states. Closes the OFFGRID_STALE residual.

Reuse: `scripts/validate_presence_shadow.py` / `audit_presence_divergence.py` /
`backfill_presence.py` and `tests/test_cash_mode/test_presence_cutover.py` +
`conftest.py` (the autouse flag-reset fixture — see gotchas).

## Files

CREATE: `cash_mode/presence_sweep.py` (R3b); optionally `cash_mode/presence_consistency.py` (R1).
MODIFY: `cash_mode/whereabouts.py` (R2), `poker/repositories/game_repository.py` + `flask_app/routes/cash_routes.py` 6 sites (R3a), `flask_app/routes/personality_routes.py` (R3b), `scripts/audit_presence_divergence.py` + `cash_mode/presence_transitions.py` (R1), `cash_mode/ai_side_hustle.py` + `ai_vice_spending.py` (optional R5), `docs/plans/CASH_MODE_STATE_MODEL.md` (D1 + §8), `tests/test_cash_mode/test_presence_cutover.py`.
DO NOT TOUCH: `emit_presence_transitions_for_save`, `save_table` wiring, `economy_flags` (all complete).

## Risks (ranked)
1. **Off-grid presence is best-effort** → whereabouts R2 must not trust presence for SIDE_HUSTLE/VICE STATUS (caveat above). Lowest-risk: derive only seated/idle from presence in R2.
2. **`delete_game` sandbox_id propagation** — verify each of the 6 call sites; non-cash deletes pass `None` (no-op, safe).
3. **`assert_presence_seat_consistency` is tests/audit-only** — don't put the cross-join on a hot path.

## Handoff — current state, gotchas, where to look

- **Branch `development`**, pushed through `e0310262` (+ this doc). Tree clean.
- **Dev flip is now DURABLE (resolved 2026-06-01).** `PRESENCE_AUTHORITY_ENABLED=1`
  is in `.env` (gitignored, dev-only) → restarts preserve authority. The committed
  compose default is still `0` and nothing is committed, so **prod is unaffected**;
  the real cutover is committing the default to `1` deliberately when there are
  users. (Earlier it was inline-env-only and reverted on restart — a backend
  restart came back OFF mid-work, which is why `.env` was set.)
- **Hot-reload + bad files = crash.** The backend (`FLASK_DEBUG=1`) auto-reloads;
  a transient syntax/merge-conflict marker in ANY imported file crash-stops it
  (seen: a stale `<<<<<<< HEAD` in `poker/memory/opponent_model.py` that was NOT
  in the clean tree — `git status` was clean; a restart loaded the good code).
  If the backend is down, check `docker compose logs backend` for an
  IndentationError/conflict marker before assuming your change broke it.
- **Tests run INSIDE the authority-on container**, so the env leaks into pytest.
  `tests/test_cash_mode/conftest.py` has an autouse fixture that resets both
  presence flags per test — rely on it; tests that exercise a mode set the flag
  explicitly.
- **Audit the live state:** `docker compose exec backend python -m
  scripts.audit_presence_divergence --db-path /app/data/poker_games.db --out
  /tmp/a.json` (read-only). Use a **double-read** (two snapshots ~6s apart, keep
  only divergences in both) to filter ticker-race transients — single snapshots
  false-flag because the world ticker mutates `cash_tables` every 2s.
- **No users in prod** — so prod deploy buys nothing yet; the no-users window is
  the right time for the riskier read-side pieces (low blast radius). Don't rank
  prod deploy until users are on the horizon.
- **Read first:** this doc → `CASH_MODE_PRESENCE_PHASE3_FLIP.md` (mechanism +
  reconciler-retirement evidence) → `cash_mode/presence_transitions.py` (the
  engine) → `cash_mode/whereabouts.py` (R2 target).

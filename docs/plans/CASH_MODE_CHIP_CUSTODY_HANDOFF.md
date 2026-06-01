---
purpose: Fresh-context handoff for building the chip-custody foundation — make the ledger the chip authority (AI parity → derived bankroll → seats-as-view → deletion integrity), starting from a measured gate
type: guide
created: 2026-06-01
last_updated: 2026-06-01
---

# Cash Mode — Chip-Custody Foundation: Handoff

Start here cold. The **Presence** machine (actor location) is built and flipped on
dev; this hands off its twin — the **chip authority** — which is the real
tech-debt payoff. Read this top-to-bottom, then `CASH_MODE_CHIP_CUSTODY_SCOPE.md`
(the scope + the measurement) and `CASH_MODE_STATE_MODEL.md` (the vision + invariants).

## The goal (one sentence)
Make the **ledger** (`core/economy/ledger.py` + `chip_ledger_entries`) a COMPLETE
double-entry record of every chip movement, so **bankroll becomes derived** (not a
bare int), **"chips at a seat" = a ledger balance** (not duplicated in
`cash_tables.seats`), conservation (invariant I1) is **enforced not asserted**,
and the chip-forfeiture bug class becomes **structurally impossible**
(`AT_TABLE` can only exit via `SETTLING`).

## What's already true (don't re-litigate)
- **Presence cutover: DONE on dev** (authoritative for seat occupancy + idle
  reads), behind `PRESENCE_AUTHORITY_ENABLED`. NOT in prod (no users → no point
  yet). Its read-side polish + reconciler deletion are designed + deferred
  (`CASH_MODE_PRESENCE_READSIDE_COMPLETION.md`). Leave it alone unless it blocks you.
- **Cut 1** (freeze-forever reaper guard) and **Cut 2** (human buy-in/cash-out
  ledgered as `player↔seat` transfers) shipped. The original chip-loss is fixed +
  auditable. This foundation is **defense-in-depth + the clean architecture**, not
  a live fire.
- Branch `development`, pushed (HEAD ~`2ff044d0`). Tree clean.

## The measured gate (run this first, it's your North Star)
`scripts/audit_ledger_completeness.py` derives each entity's balance from the
ledger (Σ sink − Σ source) and compares to the stored bankroll. Latest dev run:
- **Humans: 3/4 reconcile** (Cut 2 made the human side derivable). One small
  `guest_jeff` gap (1,643) worth chasing but minor.
- **AI: 339/1239 reconcile; ~32.6M total abs gap.** AI balances often derive
  NEGATIVE — only central-bank flows (seed/rake/vice) are ledgered; **AI
  buy-in/cash-out/table-P&L are not.** This 32.6M gap → ~0 is the definition of
  done for Phase 1.

Run: `docker compose exec backend python -m scripts.audit_ledger_completeness
--db-path /app/data/poker_games.db --out /tmp/lc.json` (read-only).

## Phase 1 — AI ledger parity (the big one; precise chokepoints)
AI chips bifurcate through exactly **two helper functions** — wire the ledger
transfer INSIDE them (single chokepoint, like Cut 2 but cleaner; do NOT chase
callers):

- `cash_mode/bankroll.py:debit_bankroll_for_seat` (~line 277) — AI **buy-in**.
  Today records only `record_ai_regen` (central-bank regen commit). **Add an
  `ai → seat` transfer** (a new `record_ai_buy_in`, mirroring
  `core/economy/ledger.py:record_player_buy_in`). `chip_ledger_repo` is already a
  param here.
- `cash_mode/bankroll.py:credit_ai_cash_out` (~line 155) — AI **leave/bust
  cash-out**. Today records only `record_ai_regen`. **Add a `seat → ai` transfer**
  (a new `record_ai_cash_out`, mirroring `record_player_cash_out`). On a bust
  (0 take-home) write no row — the absent cash_out paired with a buy_in IS the
  bust record (same convention as humans).
- **Per-hand P&L does NOT get its own entries.** It nets inside the seat balance
  and settles at cash-out (buy-in = `ai→seat`; final settle = `seat→ai` net of
  winnings). The table conserves internally between seats. Keep the ledger a
  buy-in/settle pair per session.

**FIRST design decision (blocker — decide before coding):** the human `seat:`
account is keyed by `game_id` (`seat:<game_id>`). World/sim AIs churn
`cash_tables` with **no per-AI live game_id**, so AI needs a different seat-account
identity — almost certainly **`seat:<sandbox_id>:<table_id>:<seat_index>`** (or
`seat:<table_id>` if per-table pooling is acceptable). Per-entity custody wants
per-seat granularity so the balance is one AI's stack, not a pooled table. Pick
this, add it to `ledger.seat_*` helpers, and keep it consistent with how the
backfill + audit read it.

**Phasing within Phase 1 (reuse the Presence playbook):**
1. New env-gated flag `economy_flags.CHIP_CUSTODY_ENABLED` (mirror
   `PRESENCE_AUTHORITY_ENABLED`, default OFF, `_env_flag`).
2. **Shadow/dual-write:** add the `ai↔seat` transfers in the two helpers, gated +
   conservation-neutral (the bankroll int still moves; the transfer just records
   it). The seat-account balance now mirrors the AI's on-table chips.
3. **Backfill:** seed `seat:` balances + reconcile historical AI bankroll into the
   ledger for existing sandboxes (mirror `scripts/backfill_presence.py`). Without
   it the audit gap closes only for go-forward movements.
4. **Validate:** re-run `audit_ledger_completeness.py` until AI reconciles → ~0;
   run **paired-probe** conservation sims (see gotchas) to prove no drift.

## Phases 2–5 (after Phase 1 reconciles)
2. **D2 — derived bankroll:** add `balance_of(account, sandbox_id)` to the ledger
   repo (`Σ sink − Σ source`); make bankroll reads derive from it, int as a cache;
   audit every `.chips` reader. **Resolve the asymmetry:** `player_bankroll_state`
   is GLOBAL (no `sandbox_id`); `ai_bankroll_state` is per-sandbox — pick one model.
3. **Structural reaper:** `_boot_sweep_stale_cash_rows` must SETTLE a non-empty
   `seat:` balance back to bankroll before deleting a row — never zero it. Turns
   Cut 1's behavioral guard structural.
4. **Seats-as-view:** chips come from the ledger `seat:` balance; `archetype`/
   `seated_at` move to a `seat_state` satellite; `cash_tables.seats` becomes a
   derived VIEW. Kills the occupancy/payload duplication. (See the read-side doc
   for the full `table.seats` reader inventory — every reader of `chips`/
   `archetype`/`seated_at` must be repointed.)
5. **Deletion integrity + retire reconcilers:** `GO_OFFLINE`/settle hooks at
   `delete_game` (`game_repository.py:300`) and `delete_personality`
   (`personality_routes.py:393`) → then delete `_free_ghost_human_seats` +
   `_reclaim_zombie_casino_seats` structurally.

## Validation harness (reuse, don't reinvent)
- `scripts/audit_ledger_completeness.py` — the Phase-1 gate (derived vs stored).
- `scripts/validate_presence_shadow.py` / `audit_presence_divergence.py` — the
  shadow→audit pattern to copy for the custody shadow.
- `scripts/backfill_presence.py` — the backfill pattern.
- `reference_cash_sim_ab_paired` (memory): same-seed cash-sim A/B is RNG-desync
  noise for decision-gate changes — use a **within-run paired probe** (monkeypatch
  the fn in the caller's namespace) + static blast-radius. **Back up the DB once +
  copy; never redirect docker stdout to the root-owned `data/`.**
- `tests/test_cash_mode/test_presence_cutover.py` + `conftest.py` — the test
  patterns (incl. the autouse flag-reset fixture — see gotchas).

## DEV STATE + GOTCHAS (the loaded-context stuff a fresh start would miss)
- **The Presence flip is FRAGILE on dev.** `PRESENCE_AUTHORITY_ENABLED` is set
  ONLY via env on the running container (`PRESENCE_AUTHORITY_ENABLED=1 docker
  compose up -d backend`); the committed compose default is `0` and it's NOT in
  `.env`. A plain restart/recreate **reverts authority to OFF** (it happened this
  session). If you add `CHIP_CUSTODY_ENABLED`, expect the same fragility — set it
  the same way, and consider committing defaults / a `.env` only deliberately.
- **Hot-reload crashes on any bad file.** `FLASK_DEBUG=1` auto-reloads; a transient
  syntax error / merge-conflict marker in ANY imported file crash-stops the
  backend (seen: a stale `<<<<<<< HEAD` in `opponent_model.py` that was NOT in the
  clean tree — `git status` was clean; a restart fixed it). If the backend is
  down, check `docker compose logs backend` for IndentationError/conflict markers
  before assuming your change broke it. When editing a file that registers then
  defines (e.g. a schema migration), **append the body before the registration**
  or the reloader catches the half-edit.
- **Tests run INSIDE the (authority-on) container** → the env leaks into pytest.
  `tests/test_cash_mode/conftest.py` has an autouse fixture resetting the presence
  flags per test; add `CHIP_CUSTODY_ENABLED` to it too. Run via
  `docker compose exec backend python -m pytest ... -o addopts=""` (the default
  addopts can suppress the summary line).
- **Audit live state with a DOUBLE-READ.** Single snapshots false-flag because the
  world ticker mutates `cash_tables`/balances every ~2s; take two reads ~6s apart
  and keep only divergences present in both.
- **Pre-existing ~16 / 2.27M vice/seed rounding drift** lives in the vice/casino-
  seed paths — it is NOT yours; diagnose-don't-chase. Don't let it block a
  custody conservation check (account for it).
- **`scripts/` is gitignored** — `git add -f` to commit a script.
- **The migration doc was fiction once.** Verify every function name/line against
  source before relying on it (this handoff's line numbers were grep-verified
  2026-06-01 but drift over time — re-grep).
- **No users in prod** → high risk tolerance, this is the right window for the
  risky chip refactor; don't deploy to prod for its own sake.

## Doc map (read order)
1. This handoff.
2. `CASH_MODE_CHIP_CUSTODY_SCOPE.md` — scope + the ledger-projection decision +
   the completeness measurement.
3. `CASH_MODE_STATE_MODEL.md` — the two-machine vision, invariants (esp. I1), D0–D7.
4. `CASH_MODE_PRESENCE_PHASE3_FLIP.md` + `CASH_MODE_PRESENCE_READSIDE_COMPLETION.md`
   — how the Presence twin was built (the playbook + harness you're reusing).
5. Code: `core/economy/ledger.py` (accounts + `record`/`record_transfer` + the
   `record_player_buy_in/cash_out` to mirror), `cash_mode/bankroll.py` (the two
   Phase-1 chokepoints), `poker/repositories/chip_ledger_repository.py`.

## First session plan (suggested)
1. Re-run `audit_ledger_completeness.py` to confirm the gate (baseline).
2. Decide the AI `seat:` account identity (the blocker above).
3. Add `record_ai_buy_in`/`record_ai_cash_out` to `ledger.py` + the
   `CHIP_CUSTODY_ENABLED` flag; wire them (gated) into the two `bankroll.py`
   helpers; unit-test conservation.
4. Backfill + paired-probe sim + re-run the completeness audit → drive AI gap to ~0.
That's Phase 1. Then 2–5.

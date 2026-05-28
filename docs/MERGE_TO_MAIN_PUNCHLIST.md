---
purpose: Prioritized, scoped punch list for merging the development branch back to main (production)
type: reference
created: 2026-05-26
last_updated: 2026-05-26
---

# Merge `development` → `main`: Punch List

> **What this is.** A consolidated, severity-tiered checklist for bringing the
> long-lived `development` branch back to `main`. Built from four scoped
> `pr-review-toolkit` agent reviews (cash mode, schema/persistence, Flask
> error-handling, React) plus a divergence/lint/test baseline. Every code
> finding below was verified against the actual source, not just the diff.
>
> **Format note.** IDs are merge-scoped (`M0`/`M1`/`M2`/`M3`, `CUT`, `CLR`).
> Scope = rough effort (**S** ≈ <1h, **M** ≈ a few hours, **L** ≈ a day+).
> Once we land this, items can fold into the standing `TRIAGE.md`.

---

## TL;DR — the honest state

The branch is **much closer to mergeable than its size suggests.** It's 818
commits / 1,218 files, but **169K of that is generated JSON strategy data** and
the bulk of the rest is **one feature: cash/career mode.** The two scariest
things about a months-long merge both came back clean:

- ✅ **Migrations are safe.** A v70 production-clone DB forward-migrated to v116
  cleanly and idempotently. The destructive migrations only touch cash-mode
  tables that *don't exist on production yet*.
- ✅ **SQL is parameterized and route auth is consistent** across cash mode (no
  IDOR, no f-string injection). The cold-load endpoint and world ticker are
  notably well-hardened.

The real work is: **(0)** make CI green again (it's red today), **(1)** two
correctness blockers, **(2)** a cluster of cash-mode concurrency / chip-
conservation fixes, and **(3)** product-clarity + simplification decisions —
including your difficulty-selection question, which has a concrete answer below.

---

## Tier 0 — CI gate (must be green before the PR can merge)

`main`'s merge gate requires: `ruff format --check`, `ruff check`, frontend
`lint/format/typecheck/build/vitest`, full `pytest`, and Playwright E2E.
**Today `development` fails its own gate.**

| ID | Issue | Location | Fix | Scope |
|----|-------|----------|-----|-------|
| M0-1 | `ruff format --check` fails (8 files) | `experiments/sng_runner.py`, `poker/strategy/strategy_table.py`, `poker/tiered_bot_controller.py`, 5 tests | `ruff format .` — pure autofix, last 24h of commits skipped the formatter | **S** |
| M0-2 | `ruff check` fails (2 errors) | `experiments/sng_runner.py:56` (F401 unused `TERMINAL_PHASES`), `tests/test_strategy/test_pot_type_3bp.py:9` (I001 import order) | `ruff check --fix .` | **S** |
| M0-3 | **Real test failure (deterministic):** `test_admin_can_list_sandboxes` asserts sandbox order `[Alpha, Beta]`; query is `ORDER BY created_at ASC` and the two rows share a `created_at` tick → unstable tie-break, fails even run alone | test `tests/test_chip_ledger_routes.py:269`; query `poker/repositories/sandbox_repository.py:151` | Add a stable secondary sort key: `ORDER BY created_at ASC, name ASC` (also a genuine, if minor, fix for the admin list). | **S** |
| M0-4 | **Known pre-existing artifact:** `test_non_owner_cannot_join` passes alone, fails only under parallel `-n auto` — the documented `test_websocket_auth` mock-leak that corrupts a later `create_app`. Not a product regression, but it **does** break CI. | `tests/test_websocket_auth.py::TestOnJoinAuth::test_non_owner_cannot_join` | Fix the test-isolation leak (reset the patched `authorization_service`/app mock in teardown). Tracked separately in standing triage. | **M** |
| M0-5 | **Frontend `eslint` gate fails (`--max-warnings=0`): 7 pre-existing warnings.** All `react-hooks/exhaustive-deps`. Not from the controller-cleanup work. | `cash/ActivityTicker.tsx`, `cash/CashControls.tsx`, `cash/MobileCashSheet.tsx`, `cash/SponsorModal.tsx`, `cash/StakeOfferModal.tsx`, `game/PokerTable/PokerTable.tsx`, `mobile/MobilePokerTable.tsx` | Resolve each dep-array warning (add/remove the flagged dep, or a scoped `eslint-disable-next-line` with a reason). | **S–M** |

> Frontend `tsc` typecheck is **clean** and `prettier --check` is **clean**.
> `eslint` is **red** (M0-5, pre-existing). Vitest / Playwright not yet run in
> this pass — run them as part of clearing Tier 0.

---

## Tier 1 — Blockers (correctness / data integrity; fix before merge)

| ID | Issue | Location | Impact & Fix | Scope |
|----|-------|----------|--------------|-------|
| M1-1 | **Unlocked `top_up`/`rebuy` mutate `game_state` outside the per-game lock** — races the hand engine (`progress_game` mutates the same state *under* the lock; `leave_table` documents exactly this hazard and takes the lock). | `flask_app/routes/cash_routes.py:4230` (top_up), `:2146` (rebuy) | A top-up interleaving with hand progression clobbers hand state or loses the chip add → bankroll/stack drift on a live table. **Fix:** wrap the read-modify-write in `with game_state_service.get_game_lock(game_id):` and re-read `state_machine.game_state` inside the lock, mirroring `leave_table` (`:3603`). | **S** |
| M1-2 | **One corrupt row silently resets ALL AIs' psychology on restore** — the loader builds its dict via a per-row comprehension; a single bad `psychology_json`/`tilt_state_json` row throws, and the wholesale `except Exception` (warning-level) leaves both dicts empty, so every AI reverts to default tilt/emotion. This is the exact documented cold-load failure class. | catch: `flask_app/handlers/game_handler.py:280-284`; loaders: `poker/repositories/game_repository.py:672-675` & `:545-549` | A session quietly resumes with all tilt/emotional history wiped, behind a `logger.warning`. **Fix:** move the try/except *inside* the per-row loop (log+skip the bad row, restore the good ones); escalate the outer catch to `logger.error(exc_info=True)` and distinguish "no saved state" from "load failed." | **M** |

---

## Tier 2 — Should-fix (cash-mode conservation/concurrency + UX robustness)

These are the recurring **ghost-seat** and **chip-mint** bug classes — treat as
a *class*, audit every entry/exit path, don't just patch the instance.

| ID | Issue | Location | Impact & Fix | Scope |
|----|-------|----------|--------------|-------|
| M2-1 | **Non-atomic seat claim races the world ticker** (check-then-write across a DB yield point; ticker's `refresh_unseated_tables` live-fills the same seats with no shared lock; "last-write-wins" conceded in a comment). Ghost-seat class. | `flask_app/routes/cash_routes.py:1268-1279` (sit), `:3340-3429` (offer_stake_to_ai) | A human claim clobbering a just-placed live-fill AI strands that AI's already-debited buy-in (phantom chips). **Fix:** conditional DB write (`UPDATE … WHERE seat still open`, 0 rows → 409) **or** a per-sandbox lock the ticker also holds. | **M** |
| M2-2 | **`_refill_cash_seats` mints `starting_bankroll` with no `ai_seed` ledger row** — calls `save_ai_bankroll(...)` without `chip_ledger_repo`, so the first-write audit entry never fires (regen delta is 0). | `flask_app/handlers/game_handler.py:834-836, 880` | Chips enter the economy with no ledger entry → conservation drift. **Fix:** pass `chip_ledger_repo=chip_ledger_repo` to the `save_ai_bankroll` call (the lobby seed path at `cash_mode/lobby.py:167-170` already does this). | **S** |
| M2-3 | **AI rebuy clamp-to-zero phantom-chip leak** — seats the full `change.amount` but debits `max(0, projected - amount)`; if `projected < amount` it mints the difference. Reintroduces the leak `debit_bankroll_for_seat` was written to replace. | `flask_app/handlers/game_handler.py:1333-1357` | Chips minted on insufficient bankroll. **Fix:** route through `cash_mode.bankroll.debit_bankroll_for_seat(..., chip_ledger_repo=...)` which refuses on insufficiency; on refusal, don't bump the seat. | **S** |
| M2-4 | **`payoff_stake` double-debit on concurrent submit** — read-check-debit-then-flip-status with no lock and a non-conditional status UPDATE. | `flask_app/routes/cash_routes.py:2342-2400` | Two concurrent requests both pass the `status=='carry'` check → player double-debited, staker double-credited. **Fix:** conditional transition `UPDATE stakes SET status='settled' WHERE stake_id=? AND status='carry'`; move chips only if 1 row affected. | **S** |
| M2-5 | **Bankroll load failure renders as "$0 bankroll" with no error** — `try: load_player_bankroll() except Exception: pass` leaves `bankroll_chips=0`; same shape masks a failed loan load as "no loan." | `flask_app/handlers/game_handler.py:685-686, 704-705` | A transient DB lock shows the player's money as gone and gates top-up/rebuy, indistinguishable from genuinely broke. **Fix:** log the failure; send `bankroll_unavailable: true` (or omit the field) so the UI shows "couldn't load balance" instead of a false $0. | **S** |
| M2-6 | **Corrupt `personalities.json` reported as "Personality not found"** — broad `except Exception: pass` swallows `JSONDecodeError`/`KeyError`/IO alike. | `flask_app/routes/personality_routes.py:122-123` | A broken catalog masquerades as every built-in character missing. **Fix:** catch narrowly, `logger.error` the path, return 500 for genuine load failures; keep clean 404 for a true miss. | **S** |
| M2-7 | **`/cash` (Lobby) route has no scoped `ErrorBoundary`** — the `/game/:gameId` route has one with a "Return to Menu" fallback; the lobby (most complex new surface) relies only on the app-root boundary. | `react/react/src/App.tsx:554-561` | A single bad field in a lobby payload white-screens the whole SPA instead of offering scoped recovery. **Fix:** wrap `<Lobby />` in an `ErrorBoundary` with a `/menu` fallback, mirroring the game route. | **S** |
| M2-8 | **"Controller" picker is coupled to — and silently cleared by — the LLM "Use Game Defaults" toggle.** Picking a deterministic bot (CaseBot/GTO-Lite, which use no LLM) requires turning OFF defaults; toggling defaults back ON deletes `opponentBotTypes[name]`. | `react/react/src/components/menus/CustomGameConfig.tsx:1213-1240`, `resetOpponentConfig` `:389-400` | Confusing + lossy: the controller choice the user just made is discarded. **Fix:** make the Controller picker independent of the defaults toggle; don't disable it; don't clear `opponentBotTypes` in `resetOpponentConfig`. (See also CLR-1.) | **S** |

---

## Tier 3 — Nice-to-have / follow-ups (can fast-follow the merge)

| ID | Issue | Location | Note |
|----|-------|----------|------|
| M3-1 | Desktop `CashControls` leaves "Confirm leave" latched indefinitely (mobile resets it; desktop never does) — a stray later tap abandons the table | `react/.../cash/CashControls.tsx:138-174` | Add cancel affordance / auto-reset on next action or hand transition |
| M3-2 | `CashOutSummary` `.toFixed()` on stat fields with no fallback (currently safe — builder always emits `0.0`, but trusts a cold-load contract) | `react/.../cash/CashOutSummary.tsx:143-145` | Cheap insurance: `(summary.vpip_pct ?? 0).toFixed(1)` |
| M3-3 | LLM client collapses all failures to `content=""` + `status="error"` — safe only if every caller checks `status` | `core/llm/client.py:268-288` | Audit AI-decision call sites to branch on `status`, not feed `""` downstream |
| M3-4 | `_resolve_pace` and the `_find_game_data` dossier scan swallow errors **unlogged** | `flask_app/services/ticker_service.py:175-176`; `flask_app/routes/character_routes.py:218-219` | Add `logger.warning` so a persistent failure isn't invisible (low risk; both are display-only) |
| M3-5 | `apply_chip_flows` is documented as the canonical dispatcher but **doesn't exist**; `cash_routes.py:3855` hand-rolls dispatch inline | `cash_mode/stake_chip_flow.py:27-31` | Implement it and route the leave handler through it, **or** fix the docstring |
| M3-6 | `cash_mode/seating.py` — the pure, atomic sit/leave/topup transitions are **dead code**; live routes use hand-rolled dict-slot mutations that lack those guarantees (root of M1-1/M2-1) | `cash_mode/seating.py` | Either wire the routes onto this layer (best long-term fix for the concurrency class) or delete it so it doesn't imply guarantees |
| M3-7 | The 4 destructive migrations (v99/v102/v105/v109) are safe **only because cash mode never shipped** — assumption is invisible to a future reader | `poker/repositories/schema_manager.py:5225,5319,5419,5762` | Add a one-line "one-time pre-launch destructive step; must not re-run after cash data accrues" note. v98 docstring also references a backfill that doesn't exist — correct it |
| M3-8 | `gameId.startsWith('cash-')` for back-nav / 404 recovery (2 sites) — mirrors an established backend convention (6+ backend sites), so low-risk, but the FE/BE contract is implicit | `react/.../game/GamePage.tsx:24,42` | Promote to a shared, commented constant. (Already in `TRIAGE.md` Tier 2.) |
| M3-9 | Lobby comments say "polling every 8s" but `LOBBY_REFRESH_INTERVAL_MS = 25000` | `react/.../cash/Lobby.tsx:270,273`; `IdleStakablePanel.tsx:69` | Comment rot — update to 25s |

---

## Cuts to simplify (decisions, not bugs)

These reduce surface area before it hits production. Each is a **keep / park /
rip** decision, not a defect.

| ID | Candidate | Evidence | Recommendation |
|----|-----------|----------|----------------|
| CUT-1 | **Hide internal eval/training bots from the player-facing Custom Game menu** — CaseBot, GTO-Lite, BaselineSolver are deterministic benchmark bots; `game_mode` silently does nothing for them | `CustomGameConfig.tsx:1232-1238` ("Training bots" optgroup) | Gate the "Training bots" optgroup behind an admin/experiment flag. Keep standard/chaos/lean/sharp for players. |
| CUT-2 | **~8 `PromptConfig` flags default `False`** (parked features): `relationship_context`, `gto_equity`, `gto_verdict`, `composed_nudges`, `preflop_range_gate`, `hu_equity_offset`, `use_simple_response_format` | `poker/prompt_config.py:77-112` | Per flag: promote to default-on (ship), or delete the flag + dead branch (rip). Shipping dead-off flags to main is pure surface area. |
| CUT-3 | **99 new docs, 53 in `docs/plans/`** — mostly phase/scratch planning (PHASE_6/7/8, multiple cash-mode handoffs, ~2.3K-line trace plans) | `docs/plans/` | Move completed/superseded plans to `docs/plans/archive/` (or delete). Keep only live specs. Cuts reviewer noise in the PR enormously. |
| CUT-4 | **`security_best_practices_report.md` sits in repo root** | root | Move under `docs/` (or delete if it was a one-off generated report). Root should stay lean (the new `CONTRIBUTING.md`/`CODE_OF_CONDUCT.md`/`pyproject.toml`/`.pre-commit-config.yaml` are good additions — keep). |
| CUT-5 | **Note, not a cut:** the 6-controller lineup *looks* redundant but `poker/CLAUDE.md` documents it as intentional (chaos/standard/lean/sharp/casebot/gto_lite), and `rule_based_controller.py` is a back-compat shim. **Don't delete controllers** — the real issue is which is the *default* (see CLR-1). | `poker/CLAUDE.md` "Bot Controller Lineup" | Leave the lineup; resolve the default. |

---

## Clarity needed

| ID | Question | Finding | Recommendation |
|----|----------|---------|----------------|
| CLR-1 | **"What is the point of difficulty selection in custom games now?"** | Custom games have **three overlapping difficulty-ish knobs**: (a) game-level **Game Mode** (casual/standard/competitive/pro) → loads a *prompt preset* from `config/game_modes.yaml`; (b) per-opponent **Game Mode Override** → same, per opponent; (c) per-opponent **Controller** (`bot_type`) → picks the *engine class*. Axes (a/b) tune prompt richness; (c) picks the engine — both read as "strength" to a player, and `game_mode` is silently ignored for deterministic bots. | Collapse to **one** legible axis. Proposal: a single per-opponent "Opponent" picker that bundles engine+preset into named tiers (e.g. *Casual / Balanced / Sharp*), with the raw engine/preset controls moved to an "advanced" disclosure or admin-only. |
| CLR-2 | **The default opponent is the weakest-relevant engine.** | `bot_type` defaults to `'standard'` → **HybridAIController** (the *parked* bounded-options path). The project's measured-strongest AI — the **tiered lookup-table bot — is `'sharp'`** (`_BOT_TYPE_ALIASES={'tiered':'sharp'}`, `game_routes.py:1328`) and is **not** the default. So a normal custom game faces the parked engine unless the user knows to pick "sharp." | **Decide the production default.** If tiered is the bet (per project direction), make `sharp`/tiered the default opponent and reframe "standard" accordingly. This is the single highest-leverage clarity fix. |

---

## Merge strategy / process

- **History is tangled** — 818 commits with **49 merge commits** woven in. A
  plain `git merge development` drags all of it onto `main`. Options:
  1. **Squash-merge the whole branch** into one commit on `main` (cleanest
     `main` history; loses granular attribution — fine for a solo project).
  2. **Curated feature-by-feature** PRs (cash mode, tiered AI, eval harness as
     separate squashes) — more work, but reviewable and revertible per feature.
  3. Recommendation: **(1)** given the size and that it's effectively one
     coherent release; tag the pre-merge `main` so the old state is recoverable.
- **One-time destructive migrations:** v99/v102/v105/v109 are safe on first
  deploy but must **never re-run** after cash data accrues. The deploy already
  backs up the DB before migrating (`deploy.yml`) — confirm that step succeeds
  on the cash-mode launch deploy specifically.
- **Run the gates not yet exercised** this pass: frontend `vitest`, `npm run
  build`, and Playwright E2E before opening the PR.

---

## What's already good (de-risked — don't re-litigate)

- **Migrations**: v70→v116 forward-migrates cleanly + idempotently against a
  production-clone DB; destructive steps only hit not-yet-on-prod cash tables.
- **SQL**: fully parameterized across cash mode (the `','.join('?'…)` patterns
  are safe). No injection surface found.
- **Route auth**: cash routes consistently resolve owner from session and gate
  with the 404-leak-avoidance ownership guard — no client-`game_id` IDOR.
- **Settlement/bankroll math** (`stake_settlement.py`, `bankroll.py`) is
  conservation-clean; the residual chip issues are in the *handler refill/rebuy*
  paths (M2-2/M2-3), not the core math.
- **Cold-load endpoint** (`game_routes.py:559-960`) and **world ticker**
  (`ticker_service.py`) are well-hardened — loud logging, surfaced 500s,
  fail-safe directions — showing the prior 3-chained-failure incident was learned from.
- **`base_repository.retry_on_lock`** is structurally correct (the old broken
  retry contextmanager is gone).

---

## Suggested sequencing

1. **Tier 0** (M0-1..M0-4) — get CI green. Mostly autofix + 2 small test fixes. *~½ day.*
2. **Tier 1** (M1-1, M1-2) — the two correctness blockers. *~½ day.*
3. **Tier 2 cash cluster** (M2-1..M2-6) — best done together as a "cash-mode
   concurrency & conservation" pass; consider wiring onto `seating.py` (M3-6)
   as the structural fix. *~1–2 days.*
4. **CLR-1 / CLR-2** — decide the difficulty model and default bot **before**
   merge (they change user-facing behavior; cheaper to settle now). *~discussion + S.*
5. **CUT-1..CUT-4** — trim surface area; makes the PR itself reviewable. *~½ day.*
6. Merge (squash + tag). Fast-follow Tier 3.

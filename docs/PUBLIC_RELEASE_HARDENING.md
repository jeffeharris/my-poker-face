---
purpose: Severity-tiered production-hardening punch list for a public release, synthesized from three read-only audits (chip-economy conservation, cold-load/session-resume, LLM cost/abuse)
type: reference
created: 2026-05-27
last_updated: 2026-05-27
---

# Public-Release Hardening Punch List

> Synthesized from three focused read-only audits run 2026-05-27 — **chip-economy
> conservation**, **cold-load / session-resume integrity**, and **LLM cost / abuse
> surface** — plus verification of the two Critical findings against source.
> IDs are `PRH-*`. Each finding cites file:line and a recommended fix. This is a
> *hardening* list for going public; it is not a merge blocker for the
> development→main merge (that has its own punch list).

## TL;DR — the honest posture

**Auth and the ledger architecture are genuinely solid; the risk is in two
seams.** A prior "security report" flagged 10 auth holes — all verified **false
positives** (blueprint-level `register_admin_guard`, `_authorize_game_access`,
socket owner checks, signed guest cookie, cookie hardening are all in place and
tested). The chip ledger is real and well-designed (fixed reason vocab,
central-bank-on-one-side rule, per-sandbox conservation audit). But:

1. **There is no spend backstop of any kind, and one paid endpoint is
   unauthenticated** → unbounded public LLM/image spend is the single most
   urgent pre-launch gap.
2. **The cash-mode "seated table" path diverges from both the unseated-lobby
   path and the cold-load builder** → a live chip-destruction leak *and* a
   reintroduced tournament-misroute, both untested. This is the project's
   long-running ghost-seat / in-memory-vs-persisted bug class, still live.

---

## Critical (fix before public launch)

| ID | Finding | Location | Fix |
|----|---------|----------|-----|
| **PRH-1** | **Unauthenticated avatar regeneration → unbounded paid image spend.** `POST /api/avatar/<name>/regenerate` has **no auth check** (its sibling `generate_character_images_endpoint` returns 401; this one doesn't), `emotions` defaults to all ~10, each fans out one `generate_image` call against the default paid provider (`IMAGE_PROVIDER=openai`, `dall-e-2`). Gate is only 10/hr **per IP** → rotating IPs = unbounded. Verified against source. | `flask_app/routes/image_routes.py:352`; provider default `core/llm/config.py:38` | Add the same `current_user`→401 check as the sibling endpoint (ideally admin-gate it); default `emotions` to a single priority emotion; fold under the global budget (PRH-2). |
| **PRH-2** | **No global/per-user/per-day LLM spend ceiling or kill-switch anywhere.** `UsageTracker.record()` only *writes* `api_usage` rows; nothing reads them to throttle. Any overrun (abuse, organic spike, runaway bug) runs until the provider's own billing limit or a human notices. | `core/llm/client.py` `complete`/`generate_image`; `core/llm/tracking.py:141` | Add a pre-call spend gate: read rolling `SUM(estimated_cost)` from `api_usage` against an env `LLM_GLOBAL_DAILY_BUDGET_USD` (and optional per-`owner_id` cap); short-circuit to the deterministic fallback when exceeded. Highest-leverage single addition. |
| **PRH-3** | **Voluntary AI departure from the human's seated table destroys the seat stack** (no bankroll credit, no ledger row). Fires on `bored_move`/`stake_up`/`take_break` (all with chips>0) — i.e. exactly when *winning* AIs leave. The unseated-lobby path credits these correctly; the seated handler never walks `result.bankroll_changes`. Drives ledger drift monotonically positive. Untested. | `flask_app/handlers/game_handler.py:1766-1809` (`_remove_departed_ais_from_game`), reached from `_refresh_lobby_table_for_session:1384`; cf. correct path `cash_mode/lobby.py:1556-1583` | After `refresh_table_roster`, walk `result.bankroll_changes` and apply each `from_seat` via `credit_ai_cash_out(..., chip_ledger_repo=...)` for departed pids (keyed on `personality_id`, not the name map — see PRH-13). Add a conservation regression test. |
| **PRH-4** | **Cold-loaded cash games get a `tournament_tracker` → reintroduced "cash bust → tournament misroute," now sticky.** The cold-load builder assigns `'tournament_tracker'` unconditionally (verified: line 836, no `is_cash_game` guard), violating the "cash games have no tracker" contract that `handle_eliminations`/`check_tournament_complete` rely on to no-op. After a restart/TTL-eviction (`GAME_TTL_HOURS=2`), the next human bust shows a tournament "Nth place" GAME_OVER instead of the rebuy modal — and the bad tracker is persisted. Untested. | `flask_app/routes/game_routes.py:830-847` (assign) + `:797-812` (build); guards `game_handler.py:2032,2428,2634` | In the cold-load builder, omit the `tournament_tracker` key for `cash-` games (mirror warm path `cash_routes.py:955`). Belt-and-suspenders: also gate the elimination/tournament call sites on `game_data.get('cash_mode')`. Add a cold-load-cash regression test. |

---

## High

| ID | Finding | Location | Fix |
|----|---------|----------|-----|
| **PRH-5** | **Rebuy debit-refusal still mints chips on the *persisted* table** (a residual gap in this session's M2-3 fix). `_apply_rebuys` now correctly skips the live `Player.stack` bump when `debit_bankroll_for_seat` refuses, but `save_table(result.new_table)` runs unconditionally afterward and persists the pre-bumped seat chips. Narrow (needs bankroll to drop between movement decision and apply) but it *mints*. | `flask_app/handlers/game_handler.py:1346-1362` + `_apply_rebuys:1474-1511` | On refusal, revert that seat in `result.new_table` to its pre-rebuy chips before `save_table` (or return refused indices and revert prior to persistence). |
| **PRH-6** | **The conservation audit can't serve as a production tripwire** — it sums live *AI* seat stacks but has no `live_session_human_stacks` term, so a seated human shows as negative drift, masking real leaks (e.g. PRH-3). Also reports live AI chips as 0 after a restart. | `flask_app/services/chip_ledger_audit.py:131-137,352-389` | Add a `live_session_human_stacks` term so a seated human nets to zero; treat the live-stack source as a gating condition. Then non-zero drift becomes an actionable alarm. |
| **PRH-7** | **Guest quota is trivially resettable.** Clearing the `guest_id` cookie mints a fresh guest with a fresh 50-hand / 15-personality-gen quota. `generate_personality`/`generate_theme` require only `current_user` (a guest qualifies) and use the expensive ASSISTANT tier. | `poker/guest_limits.py:28`; `poker/auth.py` guest branch; `personality_routes.py:642,473` | Key guest quota on `guest_tracking_id`+IP rather than the resettable `guest_id`; require real auth (or a much lower lifetime cap) for personality/theme generation; fold under PRH-2. |
| **PRH-8** | **Default in-game play makes a paid (FAST-tier) narration call per AI decision**, bounded only by `RATE_LIMIT_GAME_ACTION` (60/min, per IP, in-memory). A scripted owner sustains tens of thousands of calls/hr with no spend cap. (The `sharp`/tiered default is LLM-free for the *decision* but fires Layer-3 narration.) | `poker/tiered_bot_controller.py:3124`; `game_handler.py:3501`; `game_routes.py:1685` | Primarily addressed by PRH-2 (per-user cap). Confirm the narration gate is conservative for guests; `fast_forward` already swaps to a zero-LLM controller — consider that under budget pressure. |
| **PRH-9** | **`build_cash_mode_payload` never populates `active_loan`** because no code ever sets `game_data['game_id']`, so the gated loan-load block is always skipped. A staked player never sees the leave-breakdown panel in the in-game HUD (warm *and* cold paths — not cold-specific). | `flask_app/handlers/game_handler.py:715,740-755`; consumer `react/.../cash/CashControls.tsx:213` | Stamp `game_data['game_id']` (at `set_game`-time or in both builders) or pass `game_id` into `build_cash_mode_payload`. Verify against the live frontend (loan may still surface via `/api/cash/state`). |

---

## Medium

| ID | Finding | Location | Fix |
|----|---------|----------|-----|
| **PRH-10** | **Rate limits silently degrade to in-memory** if `REDIS_URL` is unset/unreachable (warning, not fatal). Safe today only because prod runs `-w 1`; any scale to `-w 2+` makes every per-IP limit per-worker (N×) and spawns N world tickers. | `flask_app/extensions.py:149-175`; `presence.py`; `docker-compose.prod.yml:40` | In production, make Redis **required** (fail startup if set-but-unreachable). Document/enforce `-w 1` until presence+ticker have a shared store + single-elected ticker. |
| **PRH-11** | **Best-effort ledger writes can desync after the chip move commits** — vice/side-hustle/regen write the bankroll first, then a swallow-on-failure `record_*`. A ledger-write failure leaves a real, unledgered chip move → drift. | `cash_mode/ai_vice_spending.py:908-940`; `ai_side_hustle.py:551-582`; `bankroll.py:253-266` | Write the ledger row in the same transaction as the bankroll write (shared SQLite db_path), or add a reconciliation sweep; at minimum alert on these WARNING logs. |
| **PRH-12** | **Only `GET /api/game-state` cold-loads** — socket `on_join`, socket+HTTP `player_action` silently drop/404 on a memory miss. Correct *iff* the client always GETs state first; fragile under reconnect storms / action-races-state after a restart. | `game_routes.py:2107,2140,1701` | Have the action path attempt the same cold-load (or return a structured "reload" signal) so it's self-healing rather than GET-order-dependent. |
| **PRH-13** | **Departed-AI credit (once PRH-3 is fixed) must not re-derive from the name→pid map** — that map has a history of desync (the ghost-seat class), which would re-strand the chips. | `game_handler.py:1784-1788` | Drive the credit off `result.bankroll_changes` (keyed by stable `personality_id`). Folds into PRH-3. |
| **PRH-14** | **World-ticker narration is a continuous background spend tied to mere presence** (keep the lobby polled → your sandbox keeps ticking). Well-bounded today (sim hands are LLM-free; vice/hustle capped at 2/refresh, FAST-tier, probability+duration gated), so low urgency. | `ticker_service.py`; `cash_routes.py:4620`; `*_narration.py` | Cap total concurrent active sandboxes; make narration respect the PRH-2 budget gate. |
| **PRH-15** | **In-session coach review history is memory-only** (`coach_session_memory`) — lost on restart/eviction. Degraded UX, no crash. | `game_routes.py:393-396` | Persist it (or rebuild from coach/decision-analysis repos on cold-load), or document as accepted loss. |
| **PRH-16** | **Vice debit uses `new_chips = max(0, projected - amount)`** — defensive today (a floor guard should keep it non-negative), but the same mint-shaped clamp the old rebuy bug had; if the guard ever drifts it silently mints. | `cash_mode/ai_vice_spending.py:908` | Replace the clamp with an assert/skip-on-insufficient (don't paper over with `max(0,...)`). |
| **PRH-17** | **Runaway-debt / garnish path conserves** (verified) but is the most arithmetically intricate area and lacks a dedicated conservation test. | `stake_settlement.py`; `movement.py:746`; `cash_routes.py:4094` | Add a test that runs bust→carry→partial-payoff→default through the real route and asserts `drift==0` at each step. |

---

## Cross-cutting themes (root causes — fixing these kills clusters)

1. **The seated-table path diverges from its siblings.** PRH-3 (seated departure doesn't credit), PRH-5 (rebuy persists a mint), PRH-4 (cold-load builder ≠ warm cash builder), PRH-13 (name-map desync) all stem from the *seated/hand-boundary* cash path being hand-rolled separately from the unseated-lobby path and the warm builder. Consolidating "apply a `RosterRefreshResult` to game state + table + bankrolls + ledger" into one shared function (the unseated path already does it right) would close PRH-3/-5/-13 together.
2. **No spend backstop + unauthenticated/resettable entry points compound.** PRH-2 (no ceiling) × PRH-1 (unauth image gen) × PRH-7 (resettable guest) = unbounded anonymous spend. PRH-2 is the universal mitigation; do it first.
3. **The cold path is untested.** No test pairs cash mode with the cold-load entry point; no conservation regression on seated departure. This is the exact "green unit tests miss the runtime cold path" pattern behind multiple prior incidents — add cold-load-cash and conservation regression tests.

## Already de-risked (no action — don't re-litigate)

- **Authz**: all 10 prior "security report" findings are false positives — admin guards (`register_admin_guard` on every admin/debug/experiment blueprint → 401), game-ownership (`_authorize_game_access` on every mutation), socket owner checks, signed+UUID guest identity, cookie hardening (`HTTPONLY`/`SAMESITE`/`SECURE`), preset/personality IDOR — all in place and tested. See `docs/security_best_practices_report.md`.
- **Chip-ledger architecture**: real ledger, fixed reason vocab, central-bank-on-one-side enforcement, per-sandbox audit. The leaks are integration gaps, not architecture. `debit_bankroll_for_seat` (refuses, doesn't clamp), `credit_ai_cash_out`, the `ai_seed` first-write hook, casino provisioning, side-hustle, payoff CAS, rake — all conserve.
- **The rumored `vice_spending` leak is NOT present in this tree** — the vice path is correctly ledgered here.
- **Ticker sim hands are LLM-free** (TieredBot/RuleBot, `llm_config={}`); admin-tier LLM endpoints all guarded; chat-suggestions/coaching owner+rate-limited; human chat doesn't fan out AI calls.
- **Cold-load HTTP game-state path is battle-hardened** — controllers, psychology (with per-row corruption guard), opponent models, sandbox scoping, mid-all-in-runout recovery.

## Suggested sequencing

1. **PRH-2 + PRH-1** — the spend kill-switch + close the unauth image hole. *Existential financial risk; do first.*
2. **PRH-4 + PRH-3** — the two cash correctness Criticals (misroute + chip-destruction).
3. **PRH-5 + PRH-9** — Highs riding the same files (persisted-mint gap, active_loan).
4. **PRH-6 + the test gaps** — make the conservation audit a real tripwire; add cold-load-cash + conservation regression tests.
5. **PRH-10** — require Redis in prod.
6. Remaining Mediums as fast-follow. Consider the theme-1 path consolidation as the durable fix.

## Not yet audited (proposed second wave)

- **Silent-failure / LLM-error resilience** — M3-3 from the merge list is unaddressed (`LLMClient` collapses failures to `content="" + status="error"`, safe only if every caller checks `status`); plus provider timeout/down behavior (does a hand hang?).
- **Concurrency/deadlock under real load** — the per-game + new per-sandbox locks (both non-reentrant) under concurrent users; lock-ordering and ticker-vs-request starvation.

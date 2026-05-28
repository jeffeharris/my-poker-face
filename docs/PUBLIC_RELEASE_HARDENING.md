---
purpose: Severity-tiered production-hardening punch list for a public release, synthesized from three read-only audits (chip-economy conservation, cold-load/session-resume, LLM cost/abuse)
type: reference
created: 2026-05-27
last_updated: 2026-05-28
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

## Resolution status (updated 2026-05-28)

**All Critical / High / Medium items are resolved on `prep-for-main`.** The
detail below is the original as-of-2026-05-27 audit; this table is the
authoritative scorecard. Per-item rationale lives in the cited commit messages
(and `docs/PRH_1_2_IMPLEMENTATION.md` for PRH-1/2). The second- and third-wave
audits are now complete (see the two results sections at the bottom): the
second wave (`PRH-18…24`) found **no deadlock** but a **stalled-provider hang**
class; the third wave (`PRH-25…35`, public *mobile* launch) surfaced the
real go-public blockers — **the spend cap ships disabled, guest minting is
unthrottled, there's no content moderation or alerting**. **Landed since:**
PRH-25 (budget armed in prod — $50 global / $5 owner), PRH-28 (webhook alert
handler), PRH-26 (guest-minting throttle + signed tracking cookie + guests
forced to `sharp` + password stub 501'd), and PRH-27's guest chat policy (free
text locked; quick-chat once per turn). **All Tier-0 blockers are now closed.**
Still open: the rest of PRH-27 (moderation on *authed* chat/names + public
personalities) and the Tier-1/2 items (backups, single-worker CPU ceiling,
client-side PRH-12, table-growth retention).

| ID | Status | Landed in |
|----|--------|-----------|
| PRH-1 | ✅ admin-gated both paid image POST routes | `f631873a` |
| PRH-2 | ✅ global + per-owner LLM spend kill-switch (gate, NULL-cost warn, owner_id threaded through all paid paths) | `f631873a`, `f9dc6c1f`, `00e67aa5` |
| PRH-3 | ✅ seated AI departure credits bankroll + ledger (keyed on personality_id) | `0fa847f9` |
| PRH-4 | ✅ cold-load omits tournament_tracker for cash; cash_mode belt-and-suspenders on elimination/complete | `0fa847f9` |
| PRH-5 | ✅ rebuy uses `debit_bankroll_for_seat` (refuses + skips seat bump; no mint clamp) | `338220e3` |
| PRH-6 | ✅ audit `live_session_human_stacks` term (composes with active_loans_principal) + `drift_reliable` gate | `6c57c574` |
| PRH-7 | ✅ guest-gated personality/theme gen + HMAC(IP) tracking fallback | `aed87755` |
| PRH-8 | ✅ confirmed conservative (no code): PRH-2 per-owner cap binds narration via owner_id; guests bounded by `GUEST_MAX_HANDS=50`; `fully_silent` gate; fast_forward zero-LLM | n/a (verified) |
| PRH-9 | ✅ `set_game` stamps `game_id` → `active_loan` HUD populates | `6c57c574` |
| PRH-10 | ✅ require Redis in prod (fail startup if set-but-unreachable) | `aed87755` |
| PRH-11 | ✅ central ledger DB-write failure escalated to ERROR `[LEDGER] DRIFT RISK` (alertable; audit is reconciliation) | `6c57c574` |
| PRH-12 | ✅ self-healing cold-load: 409 `RELOAD_REQUIRED` on action + `reload_required` socket emit on persisted-but-evicted | `aed87755` |
| PRH-13 | ✅ folded into PRH-3 (credit driven off `bankroll_changes`, not the name map) | `0fa847f9` |
| PRH-14 | ✅ `MAX_ACTIVE_SANDBOXES_PER_CYCLE` caps ticker fan-out (post-rotation, fair) | `aed87755` |
| PRH-15 | ✅ coach review history persisted (schema v118 + repo save/load + restore-on-miss at both read sites) | `aed87755` |
| PRH-16 | ✅ vice debit drops the `max(0, …)` mint clamp (floor guard is the real skip) | `6c57c574` |
| PRH-17 | ✅ carry-path conservation test (payoff/default drift==0 via the real helpers) | `6c57c574` |

Optional future enhancement (PRH-8 "consider", not a gap): auto-flip
`fast_forward` (zero-LLM) under budget pressure so flavor degrades gracefully
instead of erroring.

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

## Second-wave audit results (2026-05-28)

The two "proposed second wave" investigations below were run read-only against
source. **Headline: there is no deadlock** (verified lock inventory + ordering),
but **there is a real "stalled provider hangs things" class** — the in-game and
ticker LLM calls inherit a **600-second** read timeout with up to **3 retries**,
all synchronous and held under locks. These are *findings with recommended
fixes*, not landed fixes. New IDs `PRH-18…22`.

### Audit A — LLM-down / timeout: does a hand hang? **Yes — up to ~30–60 min.**

Runtime model (verified): prod is `gunicorn -k geventwebsocket…GeventWebSocketWorker
-w 1 --timeout 120` (`docker-compose.prod.yml:40`); dev is Werkzeug `flask run`
+ `async_mode='threading'` (`extensions.py:51`). Every provider shares one httpx
client with `connect=10s, read=LLM_HTTP_TIMEOUT` where **`LLM_HTTP_TIMEOUT`
defaults to `600.0`** (`core/llm/providers/http_client.py:13,23`). `complete()`
takes **no per-call timeout override** (`core/llm/client.py:89`) and retries
transient errors **`max_retries=2` → 3 attempts** (`client.py:175,182-200`);
provider timeouts/connection errors are classified retryable
(`providers/openai.py:124`, `anthropic.py:168`). So per failure mode:

- **Silent stall** (TCP open, no bytes — overloaded provider, black-holed proxy,
  partition): each attempt blocks the full **600s read**, ×3 = **30 min** for one
  `complete()`. This is the dangerous one.
- **Hard down** (connection refused): connect fails in ~10s ×3 ≈ 30s. Tolerable.
- **Rate-limited (429)**: ~30s sleeps between attempts ≈ 60–90s.

| ID | Finding | Location | Recommended fix |
|----|---------|----------|-----------------|
| **PRH-18** | **A silent provider stall hangs a single hand for ~30 min (≈60 min for full-LLM bots).** The AI decision is synchronous inside `progress_game` under the per-game lock; on a stall it eats the 600s×3 budget. For full-LLM bots (`chaos`/`standard`/`lean`) it then runs a **second** full LLM call (recovery, see PRH-19) → another 30 min. The default `sharp` bot's *decision* is LLM-free, but its Layer-3 narration (`expression_generator.complete()`, `expression_generator.py:144`) carries the same 600s exposure (catch-all → `_empty()`, but only *after* the timeout). The existing `FallbackActionSelector` fallback is real but only fires once the budget is exhausted, so it bounds-but-does-not-prevent the hang. | `core/llm/providers/http_client.py:13,23`; `core/llm/client.py:175`; decision call `poker/controllers.py:1285`; lock `flask_app/handlers/game_handler.py:3269` | Add a **short, env-configurable per-call timeout for in-game calls** (decision + narration, e.g. 15–30s) distinct from the 600s default appropriate for batch/experiment work; thread it through `LLMClient.complete(timeout=…)` → the provider call. Optionally cap total decision wall-clock. |
| **PRH-19** | **`status=="error"` is not distinguished from malformed JSON, so a transport failure triggers a *second* full LLM call.** On timeout `complete()` returns `content="" status="error"` (`client.py:299-314`); the decision caller never inspects `status`/`error_code` — empty content raises `AIResponseError`, which routes into the recovery branch that makes **another** `chat_full` call against the same down provider (`controllers.py:1296-1311` → `:1358`). Doubles the hang and wastes a paid call. (This is the unaddressed M3-3 "every caller must check `status`" item, made concrete.) | `poker/controllers.py:1296-1311,1358`; `core/llm/client.py:299-314` | Check `llm_response.status`/`error_code` first; on a transport error go **straight to fallback** (skip the recovery LLM call). Reserve recovery for genuine malformed-JSON-from-a-live-provider. |
| **PRH-20**(obs) | **Neither the gunicorn `--timeout 120` nor the client-facing proxy bounds the hang.** For the gevent worker the timeout tracks the worker *heartbeat*, which a cooperatively-yielding stalled socket read does **not** trip → the request runs to the 600s httpx read. (If it *did* trip, the single `-w 1` worker would be killed → full outage + in-memory game eviction — also bad.) The frontend nginx (`react/react/nginx.conf`) sets no `proxy_read_timeout` (→ 60s default), so the browser 504s at ~60s while the backend keeps running; the user sees a "frozen" game that silently un-sticks up to ~30 min later. | `docker-compose.prod.yml:40`; `react/react/nginx.conf:13-25` | The per-call LLM timeout (PRH-18) is the real backstop — don't rely on worker/proxy timeouts. |

### Audit B — Concurrency / deadlock under load: **no deadlock; real lock-held-across-LLM starvation.**

Verified lock inventory and **every** acquire site. Two non-reentrant
`threading.Lock` families: **per-game** (`game_state_service.py:16`, via
`get_game_lock`) and **per-sandbox** (`:25`, via `get_sandbox_lock`, added for
seat-blob serialization). Ordering across all call sites:

- **Game-lock holders never take the sandbox lock**: `progress_game`
  (`game_handler.py:3270`, `blocking=False`), and the blocking `with lock:`
  endpoints top-up (`cash_routes.py:2135`), leave (`:3853`), rebuy (`:4444`).
- **Sandbox-lock holders never take the game lock** — *except* the leave path,
  which nests **game→sandbox** consistently (`leave` holds the game lock at
  `:3853` → `_leave_table_locked` → `get_sandbox_lock` at `:4337`).
- No path takes **sandbox→game**, so there is **no opposing order → no cycle**.
  No path re-acquires a lock it already holds → **no self-deadlock** on the
  non-reentrant locks. **Deadlock risk: none found.**

The real risk is **a slow/stalled LLM call held *under* a lock** (the Audit-A
600s exposure intersecting these locks):

| ID | Finding | Location | Recommended fix |
|----|---------|----------|-----------------|
| **PRH-21** | **One stalled vice/hustle narration freezes the *entire* world ticker for *all* users (~30 min).** The ticker is a single shared background loop; `_tick_sandbox` holds the per-sandbox lock across `refresh_unseated_tables`, which fires **synchronous** `narrate_vice`/`narrate_side_hustle` LLM calls ("Each fire is a sync narration call", `cash_mode/lobby.py:1845`). A stall blocks that one greenlet → no other sandbox advances, and that sandbox's human seat ops block the whole time. `CYCLE_BUDGET_MS=250` is checked only *between* sandboxes (`ticker_service.py:171`) so it cannot interrupt a blocking I/O call. Low probability per tick (narration is probability/duration gated, ≤2/refresh, FAST-tier) but **unbounded blast radius** when it hits. | `ticker_service.py:218`; `cash_mode/lobby.py:1845,1872,1951`; `cash_mode/vice_narration.py:119` | Don't make LLM calls while holding the sandbox lock — the lock only needs to guard the seat read-modify-write. Generate narration **outside** the lock, or off-thread via the existing `ThreadPoolExecutor` (`cash_mode/leave_narrative.py:327` already does this for leave narration). PRH-18's per-call timeout caps the worst case regardless. |
| **PRH-22** | **A stalled AI turn blocks the human's *leave/top-up/rebuy*, not just the hand.** `progress_game` holds the per-game lock across the AI decision/narration; top-up (`:2135`), **leave** (`:3853`), and rebuy (`:4444`) acquire that same lock with a **blocking** `with lock:`. So when the table looks frozen and the user hits Leave — the natural escape — that request also hangs for the full timeout window. Worse, leave then itself runs `refresh_unseated_tables` (more sync narration) under both locks. The existing `leave_requested` cooperative-cancel only checks *between* AI orbits (`game_handler.py:3290`), not mid-LLM-call. | `flask_app/handlers/game_handler.py:3269-3270,3290`; `cash_routes.py:2135,3853,4444` | PRH-18 (per-call timeout) is the primary mitigation. Optionally let leave abort an in-flight decision (cancellation token checked around the LLM call), so "get me out" is always fast. |
| **PRH-23**(low) | **TTL cleanup mutates the global game dicts without `_game_locks_lock` and can pop a lock an in-flight request holds.** `_cleanup_stale_games` pops `games`/`game_locks`/`game_last_access` (`game_state_service.py:38-47`) unguarded; `get_game_lock` creates under `_game_locks_lock` (`:150-153`). If a game were evicted while a request held its lock, the next request would mint a **new** lock for the same id → two concurrent progressions. Very low likelihood (2h-idle games aren't being progressed; stale list is built before popping, so no iterate-while-mutate), but a latent correctness gap. | `flask_app/services/game_state_service.py:38-47,150-153` | Take `_game_locks_lock` in cleanup; skip eviction when `lock.locked()`. |
| **PRH-24**(obs) | **async-mode mismatch.** `async_mode='threading'` (`extensions.py:51`) under a gevent-websocket worker works only because the worker monkey-patches threading into greenlets; it is not the flask-socketio-recommended `gevent` pairing, and the dev path (Werkzeug) is a third model. Not a deadlock, but it governs how all of the above behave under load and is worth standardizing alongside the PRH-10 `-w 1` constraint. | `flask_app/extensions.py:51`; `docker-compose.prod.yml:40` | Standardize on one async model for prod (likely `async_mode='gevent'`) and document it next to the Redis/`-w 1` requirement. |

**The single highest-leverage fix for both audits is PRH-18** — a short per-call
LLM timeout for in-game/ticker calls. It bounds the hand hang (A), the ticker
freeze (PRH-21), and the leave/top-up/rebuy block (PRH-22) in one stroke;
PRH-19/21/22 are then refinements rather than the safety net.

---

## Third-wave audit results (2026-05-28) — public *mobile* launch

Three further read-only scans aimed specifically at going public on mobile:
**operational resilience / data durability**, **mobile-network resilience +
funnel abuse**, and **content safety / secrets / observability**. The encouraging
part first — verified solid, *do not re-litigate*: Google OAuth + admin guards +
debug/replay/interrogate endpoints all gated; `SECRET_KEY` fails-closed in prod,
no key/`str(e)` secret leakage, debug verbosity off; the SQLite PRAGMA baseline
(WAL + `busy_timeout=5000` + retry-on-lock); the Socket.IO reconnect config
(infinite retry, 1–5s backoff, foreground resync, gone-game latch); the in-game
loop is socket-push not interval-polled (low reconnect-storm exposure); cold-load
rebuilds a mid-hand game faithfully. New IDs `PRH-25…35`. These are findings;
**PRH-27's guest-free-chat lock is the only one landed this session** (see below).

### Tier 0 — launch blockers

| ID | Finding | Location | Recommended fix |
|----|---------|----------|-----------------|
| **PRH-25** ✅ | **[ARMED in prod]** `docker-compose.prod.yml` now sets `LLM_GLOBAL_DAILY_BUDGET_USD=50` / `LLM_PER_OWNER_DAILY_BUDGET_USD=5` (override via host env). Original finding: **the spend kill-switch ships *disabled*, and the per-owner cap can't bind anonymous abuse.** `LLM_GLOBAL_DAILY_BUDGET_USD` and `LLM_PER_OWNER_DAILY_BUDGET_USD` both **default to 0 = off**; with no env config at launch there is neither a cap nor the per-user backstop PRH-2 implies. Worse, the per-owner cap keys on `owner_id`, which for a guest *is the guest_id* — a fresh guest = fresh budget bucket — so only the **global** cap can stop a botnet. | `flask_app/config.py:98-99`; per-owner query `core/llm/tracking.py:313-316`; guest owner_id `game_routes.py:1337` | **Arm `LLM_GLOBAL_DAILY_BUDGET_USD` before launch** — the only ceiling a guest cannot reset. (Code: done — PRH-2. Ops: set the env var.) |
| **PRH-26** ✅ | **[LANDED]** Original: **guest minting is unauthenticated, un-CAPTCHA'd, and quota is trivially reset**; any guest could opt into the costliest `chaos`/`standard` bots; the password branch minted limit-free sessions. **Fixes:** (1) `RATE_LIMIT_GUEST_LOGIN` (default `60/hr`) caps *fresh* guest mints per IP via `exempt_when=_guest_minting_request` — returning guests + password logins exempt, so CGNAT/legit users aren't hit; (2) the `guest_tracking_id` cookie is now **signed** + resolved through `resolve_guest_tracking_id` (signed cookie → IP-derived stable id → none), so a forged/cleared cookie can't mint a fresh quota; (3) `_guest_safe_bot_types` forces guests to the LLM-free `sharp` bot; (4) the username/password branch returns **501** (`PASSWORD_LOGIN_UNAVAILABLE`). | `poker/auth.py` (`_guest_login_limit`, `resolve_guest_tracking_id`, login 501), `flask_app/config.py` `RATE_LIMIT_GUEST_LOGIN`, `game_routes.py` `_guest_safe_bot_types` | Done. (CAPTCHA/email-verification still optional belt-and-suspenders if abuse persists.) |
| **PRH-27** ◑ | **No content moderation on an unmoderated UGC+LLM pipeline.** In-game chat is appended verbatim into the AI's decision prompt (prompt-injection / offensive-output / cost surface); personality/theme *names* are interpolated into generation prompts; owner-flippable **public** personalities expose user names + LLM bios to all users. **PARTIALLY MITIGATED (guest chat policy landed):** guest **free-text** chat is sign-in-gated (`GUEST_FREE_CHAT_ENABLED`, default off; `check_guest_free_chat` on both the HTTP and socket send paths); guests keep **quick-chat** (bounded tone vocabulary) **once per turn** (the per-turn cap is `check_guest_message_limit`); client locks the keyboard tab and defaults guests to quick-chat. **Residual:** a scripted guest could attach a valid `tone` to arbitrary text (bounded to 1/turn + the budget cap) — closed only by the moderation work below. **Public personality sharing is now admin-only (landed):** the visibility endpoint requires admin to set `public`/`disabled` (non-admin owners may only set `private`); the UI hides the publish option for non-admins; and `save_personality` now **preserves** an existing row's `visibility`/`owner_id` on re-save — fixing a latent bug where editing an avatar/visual-identity (`generator.set_avatar_description`/`set_reference_image_id`, `character_images` retry) silently published + orphaned a private personality. Still open: a moderation/length-cap pass on *authed* chat + names and prompt delimiting (per-session chat injection — mostly self-affecting, lower sev). | chat→prompt `message_handler.py:123`→`controllers.py:559`; names `personality_generator.py:392`; **publish gate** `personality_routes.py` visibility route + `personality_repository.save_personality` (preserve) | Remaining: moderation pass + length cap on authed chat/names; wrap user content in explicit prompt delimiters. |
| **PRH-28** ✅ | **[handler landed + admin-configurable]** `flask_app/services/alerting.py` `WebhookAlertHandler` POSTs ERROR+ logs and the `[LEDGER]`/`[LLM BUDGET]` signals to the alert webhook (Slack-compatible; Discord via `/slack`); non-blocking, throttled, recursion-safe; **no-op until a URL is set**. The URL resolves from the **admin DB setting `ALERT_WEBHOOK_URL`** (Admin → Settings → Alerting, masked/secret, takes effect live with no redeploy) over the `ALERT_WEBHOOK_URL` env var; the handler is always attached so it can be enabled at runtime. Original finding: **no alerting — every safety signal is log-only with nobody watching.** No Sentry/webhook/email anywhere; `[LEDGER] DRIFT RISK` literally comments "surface loudly for alerting" but nothing does. Combined with PRH-25 (cap off) you would not *know* an overrun or drift happened. | `core/economy/ledger.py:243`; `core/llm/budget.py:97-127`; `flask_app/config.py:108-125` | Wire one alerting sink (Sentry or a Slack-webhook log handler) on ERROR + `[LEDGER]`/`[LLM BUDGET]` prefixes. Cheap; it's what makes every other control real. |

### Tier 1 — high

| ID | Finding | Location | Recommended fix |
|----|---------|----------|-----------------|
| **PRH-29** | **Backups are WAL-unsafe and on-box only.** `deploy.sh` does a plain `cp` of a live WAL database (corrupt-prone — see the MEMORY note), only at deploy time, to the same disk. Single-box disk failure = total loss of the chip economy + accounts + history. | `deploy.sh:43-48`; WAL `base_repository.py:107-111` | Use the sqlite `.backup` API + `integrity_check`, schedule it (cron), ship off-box (Storage Box/S3); keep ≥7 daily. |
| **PRH-30** | **The single worker's real ceiling is CPU, not connections.** Every AI decision runs a synchronous **2000-iteration equity Monte Carlo** inline (`decision_analyzer.py:230`, GIL-bound, never yields) plus ~4 full-game-JSON write transactions (`game_handler.py:3801-3821`). Practical ceiling: *low-tens* of concurrent active hands before turn latency shows. | `poker/decision_analyzer.py:230`; `flask_app/handlers/game_handler.py:3307,3801-3821` | Sample/async or lower-iteration the decision-quality analysis (it's analytics, not gameplay); coalesce the per-action triple-save to hand boundaries. Biggest throughput win. |
| **PRH-31** | **The PRH-12 self-heal is dead on the client.** Backend emits 409 `RELOAD_REQUIRED` + a `reload_required` socket event, but the React client listens for *neither* — a tap right after a reconnect/eviction is a silently-dropped action that looks like a dead button. Mobile-specific (OS drops sockets on every backgrounding/handoff). Partly masked by reconnect-refresh + a 30s watchdog. | `react/.../usePokerGame.ts:885` (generic throw, no 409 handling); zero `reload_required` listeners | Handle the 409 (refetch + retry once) and add the `reload_required` socket listener. |
| **PRH-32** | **Two tables grow unbounded and store user content verbatim forever.** Prod sets `LLM_PROMPT_CAPTURE=all` + `LLM_PROMPT_RETENTION_DAYS=0` (writes full prompts incl. user chat per decision, no cleanup); `api_usage` has no retention at all. Disk-fill *and* a privacy footprint. | `docker-compose.prod.yml:31-32`; `core/llm/tracking.py:494`; cleanup exists but unscheduled `prompt_capture_repository.py:670` | Set finite `LLM_PROMPT_RETENTION_DAYS` (and/or `all_except_decisions`), add a scheduled `api_usage` purge + `VACUUM`. |

### Tier 2 — medium / polish

| ID | Finding | Location | Recommended fix |
|----|---------|----------|-----------------|
| **PRH-33** | **Chat `sender` is client-controlled and unbounded in length.** `sender` is taken from the request and not forced to the seat name (UI spoof + the spoofed line enters the AI prompt as if another player said it); no server-side length cap on chat content (client caps at 200, server does not). | `game_routes.py:2312,1937` (sender); no length guard either path | Force `sender` server-side to the authenticated seat name; reject/truncate content over ~500 chars. |
| **PRH-34** | **Thread-local SQLite connections are never closed per request** — `base_repository.py` stores a connection in `threading.local()` per repo with a `close()` that nothing calls (no `teardown` hook). Leaked connections hold WAL readers and fds over long uptime. | `poker/repositories/base_repository.py:95-115` | Register `teardown_appcontext`/socket-disconnect cleanup, or use a real pool. |
| **PRH-35** | **No structured logging / request correlation / error dashboard.** stdout `basicConfig` only; no JSON formatter, per-request ID, log shipping, or error tracking (only Docker healthchecks). A silent error class can run for days. | `flask_app/__init__.py:17-20`; `docker-compose.prod.yml` | JSON formatter + per-request ID middleware; ship stdout to an aggregator. Folds into PRH-28. |

### Corrections / open questions

- **The "gevent worker doesn't monkey-patch" alarm is a false positive.** One scan flagged it Critical, but gunicorn's gevent worker calls `monkey.patch_all()` *itself* in `init_process` (library, not repo — hence the empty grep), so prod I/O almost certainly yields cooperatively. The real residual is just the non-standard `async_mode='threading'` pairing — **this is PRH-24, not a new finding.** A one-line runtime check (`socket.socket.__module__` inside the live worker) closes it.
- **Native vs mobile-web — RESOLVED (2026-05-28): shipping as mobile-web/PWA, no native app store presence for now.** This drops the Apple/Google *simulated-gambling* review surface (age gating, no-real-money attestations, regional store rules) from the launch path. Revisit if traction warrants a native build — the owner's intent is that it be a genuinely native experience at that point, not a wrapped webview, which would reopen the store-policy + native-crash-reporting work.

**Tier-0 status:** ~~PRH-25 (arm the budget)~~ ✅ → ~~PRH-28 (alerting sink)~~ ✅ (set `ALERT_WEBHOOK_URL` to activate) → ~~PRH-26 (guest-minting throttle + bot/auth gating)~~ ✅ → ~~PRH-27 guest chat policy~~ ✅. **All Tier-0 blockers closed.** Remaining go-public polish: the rest of PRH-27 (moderation on *authed* chat/names + public personalities — the residual prompt-injection surface), then Tier 1 (backups PRH-29, single-worker CPU ceiling PRH-30, client-side PRH-12 self-heal PRH-31, table-growth retention PRH-32).

**Two ops actions to finish activating what's shipped:** set the alert webhook — now via **Admin → Settings → Alerting** (no redeploy) or the `ALERT_WEBHOOK_URL` env (PRH-28 is a no-op until one is set) — and confirm the `LLM_GLOBAL_DAILY_BUDGET_USD`/`LLM_PER_OWNER_DAILY_BUDGET_USD` defaults suit the launch (PRH-25).

---

## Fourth-wave audit results (2026-05-28) — skeptical public-web security posture

Read-only follow-up focused on generic public webapp release risk: browser/session
security, admin bootstrap, deployment defaults, security headers, and paid-call
abuse. The encouraging part first: production already refuses wildcard CORS,
guest cookies and guest-tracking cookies are signed, password login is disabled,
admin/debug/experiment blueprints are guarded, game/socket owner checks exist,
prod compose runs gunicorn, and Redis-backed rate limiting fails closed when
Redis is configured but unreachable. The items below are the biggest remaining
opportunities before exposing the app to broad public traffic. New IDs
`PRH-36…41`; PRH-32 remains the prompt-capture retention finding.

| ID | Finding | Location | Recommended fix |
|----|---------|----------|-----------------|
| **PRH-36** | **No first-class CSRF protection for cookie-authenticated state changes.** The app relies on session/guest cookies with `SameSite=Lax` and `credentials: include`; that is useful defense-in-depth, but not a full CSRF control. There are many state-changing `POST`/`PUT`/`DELETE` routes across auth, game actions, cash economy, coach, admin, and prompt tooling. | Cookie settings `poker/auth.py:70-75`; frontend credentialed auth `react/react/src/hooks/useAuth.tsx:85-87`; state-changing routes grep surface includes `game_routes.py`, `cash_routes.py`, `admin_dashboard_routes.py`, `coach_routes.py`, `personality_routes.py` | Add a CSRF token pattern for every cookie-authenticated state-changing route. Practical path: issue a non-HttpOnly CSRF cookie or `/api/auth/csrf` token, require `X-CSRF-Token` on mutating requests, exempt only CORS preflight and explicitly documented machine-to-machine endpoints. Add route tests for reject/accept behavior. |
| **PRH-37** | **Auth JWTs are stored in `localStorage`, increasing blast radius of any future XSS.** Guest login returns a bearer token and the frontend persists it in browser-readable storage, then sends it as `Authorization` on `/api/auth/me`. Since the app already uses HttpOnly cookies, the bearer token is mostly redundant for browser flows and is easier to steal if an XSS or third-party script issue appears. | Token generation `poker/auth.py:154-156,613-616`; token read/write `react/react/src/hooks/useAuth.tsx:63-83,179-182,211-212` | Prefer server/session-cookie auth for browser flows and remove `localStorage` bearer-token use. If API tokens are needed later, make them separately issued, scoped, short-lived, and never stored in long-lived browser storage. |
| **PRH-38** | **Admin bootstrap still permits a guest-id admin in production config.** Route guards are solid, but `INITIAL_ADMIN_EMAIL` accepts values beginning with `guest_`; the repo then assigns the admin group to that id. This was convenient for dev, but production admin should be tied to a verified OAuth identity, not a guest namespace. | Bootstrap `poker/repositories/user_repository.py:402-431`; guest admin exception `poker/repositories/user_repository.py:287-290`; startup hook `flask_app/extensions.py:354-358` | In production, reject `INITIAL_ADMIN_EMAIL` values that start with `guest_`. Require a Google/OAuth-backed email, document a break-glass admin recovery flow, and consider step-up controls for admin actions such as IP allowlisting, Cloudflare Access, or separate admin allowlist. |
| **PRH-39** | **Security headers/CSP are not visible in the app or frontend nginx config.** The frontend nginx config proxies `/api` and `/socket.io` but does not set CSP, clickjacking protection, `nosniff`, referrer policy, or permissions policy. Inline scripts in `index.html` mean a strict CSP will need either nonces/hashes or moving those scripts into bundled code. | `react/react/nginx.conf:1-33`; inline scripts `react/react/index.html:59-95`; app static route `flask_app/__init__.py:243-255` | Set production security headers at the edge/nginx/Caddy: `Content-Security-Policy` with `frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, and `Permissions-Policy`. Move inline scripts into bundled JS or use CSP nonces/hashes. Verify headers with a live curl/browser check. |
| **PRH-40** | **The backend container is not production-safe by default if compose overrides are missed.** `docker-compose.prod.yml` correctly runs gunicorn, but the base Dockerfile still defaults to `flask run`, and the module entrypoint runs SocketIO with `debug=True` plus `allow_unsafe_werkzeug=True` when invoked directly. A mistaken deploy path could expose the dev server/debug behavior. | Base image default `Dockerfile:39-40`; direct entrypoint `flask_app/ui_web.py:21-23`; safe prod override `docker-compose.prod.yml:50` | Make the image default production-safe: default CMD to gunicorn, keep Werkzeug/debug only behind explicit dev compose or `FLASK_ENV=development`, and fail closed if `debug=True` is requested in production. Also run the container as a non-root user. |
| **PRH-41** | **Paid-call abuse controls are improved but still need per-feature quotas and abuse telemetry.** PRH-25/26 give global/per-owner budgets and guest throttling, which are the right backstops. Remaining public-launch risk is authenticated-user abuse of expensive paths: coaching, image generation, custom personalities/themes, prompt replay/interrogate for admins, and high-frequency game sessions. | Budget config `flask_app/config.py:103-130`; prod defaults `docker-compose.prod.yml:40-49`; paid-call capture/gate paths in `core/llm/client.py`; image/admin/coach routes across `flask_app/routes/` | Add per-user/day and per-feature quotas for LLM calls, image calls, coach calls, and concurrent sessions. Alert on spend velocity and quota spikes, not only absolute budget exhaustion. Keep provider-side billing limits low for launch week. |

**Highest leverage from this wave:** PRH-36 (CSRF) + PRH-37 (remove browser-readable bearer tokens) form the browser-session hardening baseline; PRH-39/40 make the deployment safer when traffic and operational mistakes happen; PRH-38/41 reduce the blast radius of admin and paid-call abuse.


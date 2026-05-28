---
purpose: Self-contained implementation handoff for PRH-1 (close unauth image-spend hole) and PRH-2 (global LLM spend kill-switch) from the public-release hardening audit
type: guide
created: 2026-05-28
last_updated: 2026-05-28
---

> **STATUS: IMPLEMENTED (2026-05-28).** PRH-1 and PRH-2 (all four steps) are
> done, tested, and ruff-clean. Decision B was confirmed with the user as
> **full tiered degradation, gated on the silent-failure audit running first**;
> that audit ran and cleared the decision path. See "## Resolution" at the
> bottom for what shipped and the audit findings.

# Implementation Handoff — PRH-1 & PRH-2 (cost/abuse hardening)

> **For a fresh Claude instance.** This is everything you need to implement the
> two highest-priority public-launch fixes without re-deriving context. Findings
> background: `docs/PUBLIC_RELEASE_HARDENING.md` (entries PRH-1, PRH-2). All
> file:line locations below were verified against source on the prep-for-main
> branch. Tests run in Docker: `docker compose exec -T backend python -m pytest <path> -p no:cacheprovider -p no:warnings`. Lint: `docker compose exec -T backend ruff format <f> && ruff check <f>`.

## Threat model (why these matter — do not "skip because it's internal")

The production deploy is a same-origin browser SPA (relative `API_URL`,
`SOCKET_URL = window.location.origin`) fronted by Caddy → Flask on `:5000`.
There is **no backend-for-frontend / secret proxy**. So **every `/api/*`
endpoint the frontend can reach is a public HTTP endpoint** — an attacker
replays the SPA's calls with curl/devtools. "The frontend is the only thing
exposed" is NOT a security boundary: the backend auth check and the spend cap
ARE the boundaries. (Proof: `regenerate_avatar` is already only called from the
admin UI, yet it's the #1 cost hole because the route itself is unguarded.)

---

## PRH-1 — close the unauthenticated paid-image endpoint

**Problem (verified):** `POST /api/avatar/<personality_name>/regenerate`
(`flask_app/routes/image_routes.py:352`) has **no auth check** and fans out one
paid image generation per emotion (defaults to all ~10) against
`IMAGE_PROVIDER=openai` / `dall-e-2` (`core/llm/config.py:38`). Gate is only
`RATE_LIMIT_REGENERATE_AVATAR` = 10/hr **per IP** → rotating IPs = unbounded.

**Key constraint:** `image_bp` is a **mix** — 9 player-facing GET routes that
*serve* avatars/grids in-game (`serve_avatar`, `serve_full_avatar`,
`serve_character_image`, `get_character_grid`, `list_emotions`, etc.) MUST stay
open, plus 2 POST *generation* routes that spend money. So **DO NOT**
`register_admin_guard(image_bp)` (it would break in-game avatar display). Guard
the two POST routes per-route.

**Decision (locked):** admin-gate both POST generation routes. The only caller
of `regenerate_avatar` is the admin `PersonalityManager.tsx`; image generation
is a content-authoring action, not gameplay.

**Changes:**
1. `regenerate_avatar` (`image_routes.py:352`) — add admin permission. Mirror the
   pattern in `flask_app/routes/admin_dashboard_routes.py:11,46`:
   `from poker.authorization import require_permission` then a module-level
   `_admin_only = require_permission('can_access_admin_tools')` and
   `@_admin_only` on the route (below the `@image_bp.route` / above the def).
2. `generate_character_images_endpoint` (`image_routes.py:508`) — today it only
   requires *any* `current_user` (a guest qualifies → audit H1). Tighten to the
   same admin guard for consistency. (If the product ever wants players to
   self-generate images, revisit — but default to admin for launch.)
3. Optional: keep the all-emotions default (it's the intended admin "regenerate
   the whole set" flow; the abuse vector is gone once it's admin-only).

**Test:** add cases (mirror `tests/test_admin_experiment_route_auth.py` /
`tests/test_chip_ledger_routes.py` auth patterns): unauthenticated → 401/403,
non-admin authenticated → 403, admin → proceeds (mock the image generator so no
real paid call). Confirm the GET serve routes still work unauthenticated.

---

## PRH-2 — global (+ per-user) LLM spend kill-switch

**Problem (verified):** Nothing reads cost to throttle. `UsageTracker.record()`
(`core/llm/tracking.py:141`) only *writes* `api_usage` rows (with
`estimated_cost`, `owner_id`, `created_at`, `call_type` — `tracking.py:340-398`).
Any overrun runs until the provider's own billing limit.

**Hook point (clean):** `LLMClient.complete(...)` (`core/llm/client.py:88`) and
`LLMClient.generate_image(...)` (`:320`) already receive `call_type`, `owner_id`,
`game_id`. `LLMResponse` (`core/llm/response.py:8`) has `status` (`"ok"`/`"error"`)
and a `.failed` property (`status=="error" or not content`); the image variant
has `.failed` too. A blocked call returns an `LLMResponse(status="error",
content="")` — well-behaved callers already branch on `.failed`/`.status`.

### Build order (steps 1–2 are identical regardless of Decision B; do them first)

**Step 1 — config (`flask_app/config.py`, env-var pattern as at `:63`):**
- `LLM_GLOBAL_DAILY_BUDGET_USD` (e.g. default a safe number or `0`=disabled-with-warning).
- `LLM_PER_OWNER_DAILY_BUDGET_USD` (optional second layer).
- Decide a clear "disabled" sentinel (0 or unset) and log loudly at startup if disabled.

**Step 2 — budget reader (new helper, near `UsageTracker`):**
- `SUM(estimated_cost)` over a rolling 24h (`created_at >= now-24h`) globally, and
  the same with `WHERE owner_id = ?` for the per-owner layer.
- **Cache the totals in memory with a short TTL** (~30s, or recompute every N
  calls) so you don't add a DB read to every LLM call on the hot decision path.
- **Fail-open**: if the budget query errors, allow the call and log loudly — a
  cost backstop must never freeze the game over a DB hiccup.
- Caveat: `estimated_cost` is NULL when a model's pricing row is missing → those
  rows count as $0 and slip the cap. Ensure pricing rows exist for prod models
  (`api_usage`/pricing tables), or treat NULL as a nominal non-zero.

### Step 3 — the gate behavior — ⚠️ CONFIRM DECISION B WITH THE USER FIRST

When over budget, in `complete()`/`generate_image()` before dispatching:

- **Recommended: tiered degradation.** Shed *cosmetic/background* spend first,
  let *decisions* fall back to the deterministic engine. Classify by `CallType`
  (`core/llm/tracking.py:64`):
  - **Shed when over budget** (return blocked response): `IMAGE_GENERATION`,
    `IMAGE_DESCRIPTION`, `COMMENTARY`, `CHAT_SUGGESTION`, `TARGETED_CHAT`,
    `POST_ROUND_CHAT`, `PERSONALITY_GENERATION`/`_PREVIEW`, `THEME_GENERATION`,
    `NARRATION_CLEANUP`, `VICE_NARRATION`, `SIDE_HUSTLE_NARRATION`, `COACHING`,
    `CATEGORIZATION`.
  - **Allow / let fall back**: `PLAYER_DECISION`. (The default `sharp`/tiered bot
    is already LLM-free for decisions — only its Layer-3 narration calls the LLM —
    so degradation mostly just drops "flavor"; `chaos`/`standard` decision calls
    fall back via `bounded_options._get_best_fallback_option`.)
  - Admin/experiment types (`DEBUG_*`, `EXPERIMENT_*`) are already admin-gated.
- **Alternative: block everything** — simpler, but can stall `chaos`/`standard`
  LLM-decision bots mid-hand. Only pick this if degradation is deemed too risky.

**⚠️ Dependency:** tiered degradation is only safe if callers handle a blocked
`LLMResponse` gracefully (don't feed `content=""` downstream as a passive
action). This is merge-list finding **M3-3** (LLM client collapses failures to
`content="" + status="error"`, "safe only if every caller checks status").
**Before relying on degradation, audit the decision-path callers** (search for
`.complete(` / `Assistant(...).chat(` usages that don't check `.failed`/`status`)
— or run the queued "silent-failure / LLM-error resilience" audit. If that audit
isn't done, prefer the conservative path: block only the clearly-cosmetic types
(images, narration, suggestions, generation) and leave decision/commentary
untouched for now.

**Step 4 — MVP scope:** env var(s) + the gate + a loud log when tripped. Defer
the admin dashboard panel (today's spend vs cap) and 80%-alerting — the
`api_usage` data already supports building those later.

**Test:** unit-test the budget reader (seed `api_usage` rows, assert the sum +
cache behavior + fail-open). Test the gate: with a tiny cap, a sheddable
`CallType` returns a blocked `LLMResponse` without dispatching; `PLAYER_DECISION`
behaves per Decision B; with budget disabled (sentinel) everything passes.

---

## Decisions already locked (don't re-ask)
- PRH-1: admin-gate both POST generation routes; per-route (not blueprint-wide).
- PRH-2: global **and** per-owner daily caps; enforce centrally in `LLMClient`;
  fail-open; cached running total; MVP scope (no dashboard yet).

## The one decision to confirm before Step 3
- **Decision B:** tiered degradation (recommended) vs block-everything — and
  whether the silent-failure audit runs first (it gates how aggressively
  decision/commentary calls can be shed). Raise this with the user early.

## What's verified vs. needs checking
- Verified: the unauth on `regenerate_avatar`; `image_bp` route mix; the
  `complete`/`generate_image` signatures + `LLMResponse` shape; `CallType`
  members; `UsageTracker.record` writes `estimated_cost`/`owner_id`/`created_at`.
- Check yourself: exact `api_usage` schema (column names) for the SUM query;
  whether `Assistant` (the stateful wrapper) routes through `LLMClient.complete`
  (so the gate covers it) or needs its own hook; the decision-path callers'
  handling of `.failed` (Decision-B dependency).

---

## Resolution (implemented 2026-05-28)

### PRH-1 — shipped
- `flask_app/routes/image_routes.py`: module-level
  `_admin_only = require_permission('can_access_admin_tools')`; `@_admin_only`
  on **both** POST generation routes (`regenerate_avatar`,
  `generate_character_images_endpoint`). The blueprint stays open (GET serve
  routes unguarded). The old "any `current_user`" check on
  `generate_character_images_endpoint` was removed (superseded by the admin
  guard); the now-unused `auth_manager` import was dropped.
- Tests: `tests/test_image_route_auth.py` — unauth→401, non-admin→403,
  admin→proceeds (generator mocked, no paid call), GET serve routes stay open.
  Disables the route limiter in setUp (mirrors `test_bot_type_dispatch.py`) so
  the tight per-hour caps don't trip when the full suite shares one process.

### PRH-2 — shipped (all 4 steps; Decision B = full tiered)
- **Step 1 (config):** `flask_app/config.py` — `LLM_GLOBAL_DAILY_BUDGET_USD`,
  `LLM_PER_OWNER_DAILY_BUDGET_USD` (sentinel: ≤0 / unset = that layer disabled),
  `_read_budget_usd` parser, and `log_llm_budget_status()` (loud startup log,
  ARMED vs DISABLED). Wired into `create_app`.
- **Step 2 (reader):** `UsageTracker.get_recent_spend(owner_id=None,
  window_hours=24)` in `core/llm/tracking.py` — rolling-window
  `COALESCE(SUM(estimated_cost),0)` (global + per-owner), 30s TTL cache keyed on
  `(owner_id, window_hours)`, **fails open to $0** on DB error.
  `invalidate_spend_cache()` for tests / post-write freshness.
- **Step 3 (gate):** `core/llm/budget.py` — `SpendGate` (configured at startup
  via `configure_spend_limits`, disabled by default; `core.llm` never imports
  `flask_app`). `LLMClient.complete()` and `generate_image()` short-circuit
  **before any provider dispatch** when over budget, returning a failed
  `LLMResponse`/`ImageResponse` (`status="error"`, `error_code="budget_exceeded"`,
  empty content) and a loud log. **Log-only, no `api_usage` row** for blocked
  calls (matches the MVP "loud log when tripped"). Global ceiling checked before
  per-owner.
- **Step 4 (MVP scope):** env vars + gate + loud log. Dashboard / 80%-alert
  deferred as planned.
- Tests: `tests/test_llm_spend_budget.py` (reader: sum, per-owner filter, window
  cutoff, NULL-cost=$0, cache hold/invalidate/TTL, fail-open) and
  `tests/test_llm_spend_gate.py` (gate logic + `LLMClient` integration: over
  budget blocks before dispatch, disabled passes through, image block, per-owner
  isolation). Integration tests reset the process-wide gate in tearDown.

### Decision B & the silent-failure audit (the gate dependency)
Confirmed answers to the two open items in "## What's verified":
- **`Assistant` routes through `LLMClient.complete`** (`core/llm/assistant.py:188`
  via `chat`/`chat_full`), so the single gate in `complete()` covers stateless
  *and* AI-player calls. No separate hook needed.
- **Decision-path callers degrade safely on a blocked (`content=""`) response**
  — so shedding `PLAYER_DECISION` is safe (full tiered):
  - `standard` (HybridAIController): `parse_json_response("")` raises
    `AIResponseError` → `response_dict=None` → `_get_best_fallback_option` picks
    the best **+EV** bounded option (not a blind check/fold).
  - `chaos` (AIPlayerController): blocked → `MALFORMED_JSON` → blocked recovery
    → `FallbackActionSelector.select_action(MIMIC_PERSONALITY, valid_actions,…)`
    = deterministic, valid-action-aware action.
  - `lean` (LeanBoundedController): inherits the Hybrid +EV fallback. (The
    `MEMORY.md` "choice='N' → CHECK passive leak" note predates the current
    EV-aware fallback.)
  - default `sharp`/tiered bot: LLM-free for the decision; its only LLM call is
    `COMMENTARY` narration (`_run_expression_layer`, `tiered_factory.py:72`),
    fully `try/except → return None` with the decision already finalized.
  None of the paths leak a passive action or crash on a blocked response.

### `CallType` shedding classification (`core/llm/budget.py`)
- **cosmetic** (vanish harmlessly): `IMAGE_GENERATION`, `IMAGE_DESCRIPTION`,
  `COMMENTARY`, `CHAT_SUGGESTION`, `TARGETED_CHAT`, `POST_ROUND_CHAT`,
  `PERSONALITY_GENERATION`/`_PREVIEW`, `THEME_GENERATION`, `NARRATION_CLEANUP`,
  `VICE_NARRATION`, `SIDE_HUSTLE_NARRATION`, `COACHING`, `CATEGORIZATION`.
- **decision** (falls back to deterministic engine): `PLAYER_DECISION`.
- When over budget the gate blocks the call regardless of class (it is a hard
  financial backstop); the class drives the log line and documents *why*
  blocking is safe. `classify_shed()` is the labeling helper.

### To operate
Set `LLM_GLOBAL_DAILY_BUDGET_USD` (and optionally
`LLM_PER_OWNER_DAILY_BUDGET_USD`) in the environment; the startup log confirms
ARMED/DISABLED. Caveat from the original plan still holds: `estimated_cost` is
NULL when a model's pricing row is missing → those rows count as $0 and slip the
cap, so ensure pricing rows exist for prod models.

### Known limitations
- **Per-owner cap depends on `owner_id` propagation.** The gate only consults
  the per-owner ceiling when the caller passes an `owner_id`. Several
  generation paths — notably the paid image-generation routes (now admin-gated
  by PRH-1) and a handful of background commentary/narration paths — call
  `LLMClient` without threading an `owner_id` through. **For those calls the
  per-owner layer does not bind, but the global cap still does** (its SUM
  counts rows with NULL `owner_id`). Implication: always arm
  `LLM_GLOBAL_DAILY_BUDGET_USD` — `LLM_PER_OWNER_DAILY_BUDGET_USD` alone
  cannot bound the owner-less paths. Threading `owner_id` through
  `poker/character_images.py` and the narration paths would tighten this, at
  the cost of multi-file changes deferred from this MVP.
- **Spend cache is eager-bumped between TTL recomputes.** `UsageTracker.record`
  folds each call's `estimated_cost` into the cached totals immediately (see
  `_bump_spend_cache`), so the gate sees new spend without waiting for the
  cache TTL to expire. The bump preserves each entry's original timestamp,
  so the periodic DB recompute still happens on schedule and supersedes any
  drift. Worst-case residual slippage is bounded by concurrent in-flight
  calls (whose cost has not yet been recorded), not by the cache TTL.

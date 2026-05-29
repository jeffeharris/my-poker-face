---
purpose: Standing reference for the application's security stance and policy — the principles we commit to, the controls in place by domain, known gaps, and the operator checklist
type: reference
created: 2026-05-29
last_updated: 2026-05-29
---

# Security Posture & Policy

The standing "where do we stand on security" reference for My Poker Face. It has
two halves: the **policy** (the rules we commit to — the yardstick) and the
**posture** (the controls actually in place today, by domain, plus known gaps).
Where the posture falls short of a policy, that's listed under **Known gaps** as
a deliberate, tracked *exception* — not an oversight.

- This is a *posture* doc (current state). The **severity-tiered remediation
  tracker** is [`docs/PUBLIC_RELEASE_HARDENING.md`](../PUBLIC_RELEASE_HARDENING.md)
  (IDs `PRH-*`) — that's authoritative for per-item status; this doc links to it.
- The earlier point-in-time review is
  [`docs/security_best_practices_report.md`](../security_best_practices_report.md)
  — all ten of its findings were verified **false positives / already
  remediated**; kept for history.
- Rate-limit specifics live in [`RATE_LIMITING.md`](RATE_LIMITING.md).

Legend: ✅ in place · ◑ partial · ❌ known gap.

## Threat model (what we're actually protecting)

- **No real money.** Chips are play-money; the chip ledger is an internal
  conservation invariant, not a financial system. The genuine cost is **paid
  LLM / image API spend**, so cost-abuse is the top financial risk.
- **User-generated content meets an LLM and other humans.** Chat, profile bios,
  and personality/theme names are user free text that is (a) fed into LLM
  prompts (prompt-injection / offensive-output surface) and (b) in multiplayer /
  shared-personality cases, shown to other users.
- **Public, low-friction signup.** Guests are created from a signed cookie with
  no email; the anonymous population is the abuse surface for spend and content.
- **Single self-hosted box** (Hetzner) behind a reverse proxy — no horizontal
  redundancy; availability and backups matter.

## Security policy (the rules we commit to)

Normative principles — the yardstick the posture below is measured against.
Stated out loud so decisions are explicit and gaps are visible. Where current
state falls short, it's a tracked *exception* in **Known gaps**, not an accident.

1. **Moderate all non-admin user input that reaches an LLM or another user.**
   Any user-supplied **free text** that is fed into an LLM prompt **or** shown to
   other users MUST pass server-side moderation (`moderate_text`) before it is
   persisted or used, whenever the field is reachable by a **non-admin**.
   Bounded enums (e.g. quick-chat tones) and server-generated text are exempt.
   **Admins are exempt for now** — a deliberate, time-boxed trust decision (low
   volume, trusted accounts); revisit if admin accounts proliferate or once
   step-up admin auth exists.

2. **Server is authoritative; never trust the client for security or cost.**
   Ownership, gating, rate limits, and length caps are enforced server-side;
   client-side validation is UX only. Every user-supplied field that is persisted
   or sent to an LLM has a server-side length cap.

3. **Default-deny authorization.** Every state-changing route and socket event
   verifies authentication + ownership/permission server-side. New
   admin/debug/experiment blueprints register the admin guard. Read paths may be
   open; mutations never are.

4. **Anonymous users get the most-restricted defaults.** Guests: LLM-free bots,
   no free-text chat, no publishing, and quotas keyed on a signed / IP-derived id
   — never a resettable client value. Expensive or cross-user actions require a
   real (Google) account.

5. **No paid LLM/image call without the budget gate.** Every paid call routes
   through the spend kill-switch and threads `owner_id` for the per-owner cap.
   Default to the cheapest tier; expensive paths are opt-in for authenticated
   users only.

6. **Cross-user / public content is held to a higher bar.** Content shown to
   other users (public personalities, multiplayer chat, table-visible bios) is
   moderated, and publishing to a shared surface requires **admin** action.
   Default-private.

7. **Fail in the safe direction — deliberately, per control.**
   - Best-effort gates (moderation, the spend read) **fail open** on a
     *dependency outage* so a hiccup never blocks a legitimate user — but a
     *positive signal* **fails closed** (flagged → reject; over budget → block).
   - Required infrastructure (Redis in prod) **fails closed at startup** —
     refuse to boot misconfigured rather than silently degrade.

8. **Secrets stay server-side.** Secrets come only from env or admin DB settings
   (masked on read); never logged, never returned to clients; production refuses
   to start without a required secret; debug verbosity is off in prod.

9. **Safety-critical signals must be alertable, not just logged.** Ledger drift,
   budget trips, and lifecycle breakage carry an alert prefix and reach a wired
   sink — silence is not success.

10. **Retain user content minimally.** Verbatim user content (chat captures,
    bios) has a finite retention; store the minimum; keep an off-box backup.

> **Current policy exceptions** (see Known gaps for the fix + tracking ID):
> prompt capture is retained indefinitely in prod and backups are on-box only
> (policy 10); there's no first-class CSRF token (policy 3, for cookie-authed
> mutations).

## Authentication & identity ✅ (mostly)

| Control | State | Where |
|---|---|---|
| Google OAuth (only real-account provider); CSRF-state with 10-min expiry; session regenerated pre-auth | ✅ | `poker/auth.py` |
| Guest identity = **signed** `guest_id` cookie (HMAC via app `SECRET_KEY`); forged/format-only cookies rejected | ✅ | `poker/auth.py` `_sign_guest_id`/`_unsign_guest_id` |
| Guest **hand-quota** cookie (`guest_tracking_id`) is signed; forged/cleared → IP-derived stable id (can't mint a fresh quota) | ✅ (PRH-26) | `poker/auth.py` `resolve_guest_tracking_id` |
| Fresh-guest minting rate-limited per IP (`RATE_LIMIT_GUEST_LOGIN`, returning guests exempt) | ✅ (PRH-26) | `poker/auth.py` `_guest_login_limit` |
| Username/password login | ✅ disabled (501) — the stub used to mint limit-free sessions | `poker/auth.py` login |
| Cookie hardening: `HttpOnly`, `SameSite=Lax`, `Secure` in prod | ✅ | `poker/auth.py` `init_app` |
| Browser auth is **cookie-only** — no `localStorage` bearer JWT issued/stored/sent; session + signed guest cookies carry auth (sockets use `withCredentials`). Server still accepts a Bearer header for non-browser clients (dormant). | ✅ (PRH-37) | `poker/auth.py` login; `useAuth.tsx`, `UsageStatsProvider.tsx` |

## Authorization ✅

| Control | State | Where |
|---|---|---|
| Admin/debug/experiment blueprints blanket-guarded (`register_admin_guard` → 401/403) | ✅ | `flask_app/route_utils.py`; `require_permission('can_access_admin_tools')` |
| Game mutations checked for ownership/admin | ✅ | `_authorize_game_access` |
| Socket events verify owner/admin before acting | ✅ | `game_routes.py` socket handlers |
| Paid image-generation POST routes admin-gated | ✅ (PRH-1) | `image_routes.py` `_admin_only` |
| Publishing a personality (cross-user content) is **admin-only**; non-admin owners may only set `private`; `save_personality` preserves visibility/owner on re-save | ✅ (PRH-27) | `personality_routes.py` visibility route; `personality_repository.save_personality` |
| Admin bootstrap (`INITIAL_ADMIN_EMAIL`) still accepts a `guest_`-namespaced id in prod | ❌ | PRH-38 |
| No first-class CSRF token on cookie-authed state-changing routes (relies on `SameSite=Lax`) | ❌ | PRH-36 |

## Secrets handling ✅

- `SECRET_KEY` **fails startup** if unset in production (dev-only fallback). — `flask_app/config.py`
- Debug verbosity is gated: the global error handler returns only a generic message in prod; full `type(e)`/`str(e)` only when `is_development`. — `flask_app/__init__.py`
- Provider API keys are read from env and never serialized into responses; no key appears in error payloads. (Minor: some routes still echo `str(e)` — non-secret internal text; polish, not a leak.)

## Rate limiting ✅

- Flask-Limiter, **Redis-backed and required in production** — a configured-but-unreachable Redis **fails startup** (PRH-10) rather than silently degrading to per-worker in-memory limits. Per-IP key function. See [`RATE_LIMITING.md`](RATE_LIMITING.md).
- Per-route caps: new-game, game-action, polling, chat-suggestions, image/personality/theme generation, and fresh guest-login (PRH-26). — `flask_app/config.py` `RATE_LIMIT_*`
- **Constraint:** presence + the world-ticker assume a single elected worker; prod runs `-w 1`. Scaling to `-w 2+` needs a shared presence/ticker store first (PRH-10).

## LLM / image spend controls ✅ (armed in prod)

- A process-wide **spend kill-switch** reads rolling 24h `SUM(estimated_cost)` from `api_usage` against a **global** and optional **per-owner** daily USD ceiling, short-circuiting before any provider dispatch. — `core/llm/budget.py` (`SpendGate`), gate in `core/llm/client.py`
- **Armed in prod** (`docker-compose.prod.yml`: `LLM_GLOBAL_DAILY_BUDGET_USD=50`, `LLM_PER_OWNER_DAILY_BUDGET_USD=5`; override via host env). Disabled by default elsewhere (dev/sims).
- **Graceful degradation:** over-budget *cosmetic* calls (avatars, commentary, chat suggestions, narration) simply vanish; `PLAYER_DECISION` falls back to the deterministic engine; the default `sharp` bot is LLM-free for decisions. A blocked call never stalls a hand.
- **Caveat:** the global cap is the only ceiling a guest can't reset (per-owner keys on the guest's resettable `owner_id`). Per-feature/per-user quotas are not yet layered in (PRH-41).

## Content moderation & UGC ✅ (substantially)

- **Guest free-text chat is sign-in-gated** (`GUEST_FREE_CHAT_ENABLED`, default off); guests keep bounded **quick-chat** once per turn. — `poker/guest_limits.py` `check_guest_free_chat`; both send paths in `game_routes.py`
- **`moderate_text`** (OpenAI free `omni-moderation-latest`) **gates** — synchronously, reject-before-persist — the user free text that reaches an LLM or other users:
  - profile **bio** + human **avatar prompt** — `flask_app/routes/profile_routes.py`
  - authed **in-game chat** (+ a 500-char length cap, `CHAT_TOO_LONG`) — `game_routes.py` `_player_chat_rejection`
  - personality / theme **name + description** generation — `personality_routes.py` `_moderation_error`
  - AI-personality **image inputs** — `avatar_description` + `visual_identity` (identity/appearance/apparel), on all three write paths (`create_personality`, `update_personality`, `update_avatar_description`) — `personality_routes.py` `_personality_image_text` + `_moderation_error`
  - Policy: **fail-closed** on a positive hit (→ `400 MODERATION_REJECTED`), **fail-open** on outage (8s timeout, `max_retries=0` — never hangs the request), no-op without `OPENAI_API_KEY` / `MODERATION_ENABLED=false`. — `core/moderation.py`
- ◑ **Remaining (optional):** explicit prompt-*delimiting* of user content (defense-in-depth on top of moderation) and forcing chat `sender` server-side (PRH-33).

## Image-generation safety ◑

- **Output:** the default `IMAGE_PROVIDER=openai` (dall-e-2) does its own server-side content moderation (`content_policy_violation` is caught + retried with a safe archetype identity), and a `NEGATIVE_PROMPT` (nsfw/anime/etc. blocklist) is appended to every generation. — `poker/character_images.py`
- **Input:** the human avatar prompt **is** text-moderated, and so now are the **AI-personality `avatar_description` / `visual_identity`** inputs (`PUT /api/personality/<name>/avatar-description`, `create_personality` / `update_personality` config) — screened by `_personality_image_text` + `_moderation_error` before they reach the (paid) image pipeline. This closes the last PRH-27 moderation gap and removes the prior dependence on the provider's own moderation, so a future `IMAGE_PROVIDER=pollinations` (weak moderation) switch no longer reopens an input hole. ✅

## Observability & alerting ✅ (handler) / ◑ (broader)

- **Webhook alert handler** forwards ERROR-level logs + the `[LEDGER] DRIFT RISK`, `[LLM BUDGET]`, and `[CASH LIFECYCLE]` signals to a Slack-compatible webhook — non-blocking, throttled, recursion-safe. URL is **admin-configurable at runtime** (Admin → Settings → Alerting, DB setting over `ALERT_WEBHOOK_URL` env). No-op until a URL is set. — `flask_app/services/alerting.py` (PRH-28)
- ◑ No structured logging / per-request correlation id / error dashboard (PRH-35); no per-feature abuse telemetry (PRH-41).

## Data handling & persistence ◑

- SQLite with WAL + `busy_timeout=5000` + retry-on-lock (sound baseline). — `poker/repositories/base_repository.py`
- ❌ **Backups are WAL-unsafe and on-box only** (deploy-time `cp`); no off-box copy (PRH-29).
- ❌ **Unbounded growth + verbatim retention:** prod runs `LLM_PROMPT_CAPTURE=all` with `LLM_PROMPT_RETENTION_DAYS=0` (full prompts incl. user chat kept forever); `api_usage` has no retention (PRH-32). Disk-fill + a privacy footprint.
- In-memory game state is reconstructable from per-action DB saves via cold-load; loss on restart is bounded to sub-second in-flight progress.

## Deployment ◑

| Aspect | State |
|---|---|
| `gunicorn -k geventwebsocket…GeventWebSocketWorker -w 1 --timeout 120`; `ProxyFix` + forced HTTPS in prod | ✅ |
| CORS: explicit origin allowlist; wildcard refused in production | ✅ |
| `async_mode='threading'` under a gevent worker — non-standard pairing; confirm prod monkey-patch + standardize | ◑ PRH-24 |
| Base `Dockerfile` still defaults to `flask run` and the module entrypoint runs Werkzeug with `debug=True`/`allow_unsafe_werkzeug=True`; container runs as root | ❌ PRH-40 |
| No security headers / CSP at the edge (frontend nginx sets none; `index.html` has inline scripts) | ❌ PRH-39 |

## Known gaps — roadmap

Tracked with detail + fixes in [`PUBLIC_RELEASE_HARDENING.md`](../PUBLIC_RELEASE_HARDENING.md):

- **Web-session hardening:** CSRF tokens (PRH-36). *(Dropping the `localStorage` bearer JWT — PRH-37 — is done.)*
- **Edge/deploy:** security headers + CSP (PRH-39); production-safe image default + non-root container (PRH-40); admin-bootstrap not via guest namespace (PRH-38); standardize the async model (PRH-24).
- **Abuse depth:** per-user/per-feature quotas + abuse telemetry on top of the global budget (PRH-41).
- **Ops/data:** off-box WAL-safe backups (PRH-29); capture/`api_usage` retention (PRH-32); the single-worker CPU ceiling (PRH-30); client-side cold-load self-heal (PRH-31).
- **Content (optional):** prompt-delimiting of user content (defense-in-depth) + server-forced chat `sender` (PRH-33). *AI-personality image-input moderation is now landed — PRH-27 is fully closed.*

## Operator checklist (to *activate* shipped controls)

1. **`ALERT_WEBHOOK_URL`** — set it (Admin → Settings → Alerting, or env). PRH-28 is a no-op until then.
2. **Budget ceilings** — confirm `LLM_GLOBAL_DAILY_BUDGET_USD` / `LLM_PER_OWNER_DAILY_BUDGET_USD` suit launch traffic; keep the provider's own billing cap low for launch week.
3. **`REDIS_URL`** — required in prod (startup fails if set-but-unreachable). Keep `-w 1` until presence/ticker have a shared store.
4. **`IMAGE_PROVIDER`** — `openai` (dall-e-2) is the default; avatar-description / `visual_identity` inputs are now text-moderated, so `pollinations` no longer reopens an unmoderated input hole (output still leans on the provider, so prefer `openai`).
5. **Prompt capture** — set a finite `LLM_PROMPT_RETENTION_DAYS` for prod (PRH-32).
6. **`SECRET_KEY` / `JWT_SECRET_KEY`** — set strong values (startup enforces `SECRET_KEY`).

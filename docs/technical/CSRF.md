---
purpose: How the double-submit-cookie CSRF control works end-to-end (issue, echo, validate), its config flag, exemptions, and the frontend helper
type: reference
created: 2026-06-03
last_updated: 2026-06-03
---

# CSRF Protection (PRH-36)

My Poker Face authenticates state changes with cookies (Flask session +
signed guest cookies, all `SameSite=Lax`). Cookie auth is the precondition for
CSRF: a browser will attach those cookies to a cross-site request the attacker
forged. PRH-36 adds a first-class **double-submit-cookie** control on top of
`SameSite=Lax` defense-in-depth, since `SameSite=Lax` has known gaps
(top-level POST navigations, older browsers).

The whole backend mechanism lives in one file:
[`flask_app/csrf.py`](../../flask_app/csrf.py) (122 lines). The frontend half is
[`react/react/src/utils/csrf.ts`](../../react/react/src/utils/csrf.ts) (71 lines).
This doc maps the contract between them and the "why" behind the trade-offs.

See also [`SECURITY_POSTURE.md`](SECURITY_POSTURE.md) (control table line 126,
ops constraint line 208) and the audit record in
[`docs/PUBLIC_RELEASE_HARDENING.md`](../PUBLIC_RELEASE_HARDENING.md) (PRH-36).

## Why double-submit, not a synchronizer token

> Source: `PUBLIC_RELEASE_HARDENING.md` (PRH-36 audit narrative). Point-in-time
> rationale; the mechanism below is code-verified.

The double-submit pattern needs **no server-side token store** — no DB row, no
per-token session slot. The backend just sets a cookie and compares it to a
header on the way in. A synchronizer-token would have required minting, storing,
and looking up a token server-side. The audit also chose a single global `fetch`
wrapper over threading a header through the ~100 mutating call sites, so no
future call site can silently miss it.

## The flow

| Step | Where | What happens |
|------|-------|--------------|
| 1. Bootstrap | `main.tsx:13` | `installCsrfFetch()` runs at module top level, before any provider's fetch-on-mount fires |
| 2. First GET | any `/api/*` GET | reaches the backend with no token cookie yet |
| 3. Issue cookie | `csrf.py:105` `_csrf_set_cookie` (`after_request`) | if `csrf_token` cookie absent, set a fresh `secrets.token_urlsafe(32)` |
| 4. Echo header | `csrf.ts:59-65` | wrapped `fetch` reads the cookie and sets `X-CSRF-Token` on mutating `/api/*` requests |
| 5. Validate | `csrf.py:68` `_csrf_protect` (`before_request`) | `hmac.compare_digest(cookie, header)` — mismatch/absence → `403 CSRF_FAILED` |

The defense: an attacker's cross-origin page **cannot read** the same-origin
`csrf_token` cookie (`csrf.py:78` reads `request.cookies`; browser policy blocks
cross-origin JS reads), so it cannot set the matching header. The header is
absent or wrong → rejected.

## Backend

### Issuance — `_csrf_set_cookie` (`csrf.py:105`)

`after_request` hook. Sets the cookie only when absent (`csrf.py:112`) — it is
**set-once-per-browser, not rotated per response**. Rotation would break
concurrent tabs. Cookie attributes (`csrf.py:113-121`):

| Attribute | Value | Why |
|-----------|-------|-----|
| value | `secrets.token_urlsafe(32)` | unguessable |
| `max_age` | `30 * 24 * 60 * 60` (30 days) | matches the guest cookies |
| `httponly` | `False` | **load-bearing** — the SPA JS must read it to echo it |
| `secure` | `not config.is_development` | HTTPS-only in prod |
| `samesite` | `'Lax'` | mirrors session/guest cookies |
| `path` | `/` | site-wide |

The `httponly=False` trade-off is intentional: an XSS that can read this cookie
already has full session access, so making the cookie JS-readable costs nothing
in that threat model.

### Validation — `_csrf_protect` (`csrf.py:68`)

`before_request` hook. Guard chain:

1. Short-circuit if `CSRF_PROTECTION_ENABLED` is falsy (`csrf.py:71`) — read
   **live** from `current_app.config`, not captured at startup, so tests flip it
   per-app.
2. Short-circuit if `_is_protected_request()` is False (`csrf.py:73`).
3. Read `cookie_token` (`request.cookies`) and `header_token`
   (`request.headers`) by configured names (`csrf.py:76-79`).
4. If either is missing or `hmac.compare_digest(...)` fails (`csrf.py:84-88`) →
   `403` JSON `{"success": false, "error": "...", "code": "CSRF_FAILED"}`
   (`csrf.py:96-102`). The compare is constant-time.

### What counts as protected — `_is_protected_request` (`csrf.py:49`)

ALL of:

- `request.method` in `_MUTATING_METHODS = {'POST','PUT','PATCH','DELETE'}` (`csrf.py:41`)
- `request.path` starts with `/api/` (`csrf.py:54`) — API only, not static assets
- path NOT in `_EXEMPT_EXACT = {'/api/auth/login'}` (`csrf.py:45`)
- path does NOT start with any `_EXEMPT_PREFIXES = ('/api/auth/google/',)` (`csrf.py:46`)

`OPTIONS` preflight is implicitly exempt: it isn't in `_MUTATING_METHODS`.

### Wiring

`init_csrf(app)` is called once in `create_app()` at
[`flask_app/__init__.py:112-114`](../../flask_app/__init__.py), after
`init_extensions`, before route registration. It is **always** called; the
enable/disable toggle lives inside the hooks (`csrf.py:71`), not at the wiring
site. `init_csrf` seeds `CSRF_PROTECTION_ENABLED`, `CSRF_COOKIE_NAME`,
`CSRF_HEADER_NAME` into `app.config` via `setdefault` (`csrf.py:64-66`) so tests
override per-app without touching module state.

## The flag — `CSRF_PROTECTION_ENABLED`

Defined in [`flask_app/config.py:71-74`](../../flask_app/config.py). Reads the
env var of the same name; default is `'false'` when `is_development` is true,
`'true'` otherwise. Cookie/header names are constants:
`CSRF_COOKIE_NAME = 'csrf_token'` (`config.py:75`),
`CSRF_HEADER_NAME = 'X-CSRF-Token'` (`config.py:76`).

`is_development` is true when `FLASK_ENV=development` or `FLASK_DEBUG=1`.

**Why OFF in dev (and tests).** The dev SPA runs on `:5173` and the Flask API on
`:5000` — different origins. A non-HttpOnly cookie set by `:5000` is not readable
by JS on `:5173`, so the frontend could never attach a matching header and every
mutation would `403`. The test suite sets `FLASK_ENV=development`, so the gate is
off there by default — see the isolation note below.

**Ops constraint** (`SECURITY_POSTURE.md:208`): the SPA must be served
**same-origin** as the API (it is, via nginx) for the cookie to be JS-readable.
If a cross-origin frontend is ever introduced, the token must be delivered in a
response body instead of relying on `document.cookie`.

## Exemptions

| Route / pattern | Validated? | Cookie still issued? | Why exempt |
|---|---|---|---|
| `OPTIONS *` | no | n/a | CORS preflight — not a mutating method |
| `POST /api/auth/login` | no | yes | auth bootstrap / guest login; runs before any cookie exists, so gating it would lock out new visitors |
| `/api/auth/google/*` | no | yes | external OAuth redirect carrying its own `state` token (see below) |

Exempt routes still receive the token cookie via `after_request`; only the
header **check** is skipped.

## Relationship to the OAuth state token

Google OAuth has its own, independent CSRF defense — a **synchronizer** token,
not double-submit. [`poker/auth.py:293-294`](../../poker/auth.py) stores
`secrets.token_urlsafe(32)` in `session['oauth_state']` on
`/api/auth/google/login`, and the callback verifies the `state` query param
against it (`auth.py:319-325`) with a **10-minute expiry** (`auth.py:70`,
`328-332`). This is server-side synchronized state, complementary to (not
redundant with) the double-submit cookie: it covers the external redirect loop
that bypasses the cookie-readable check, which is exactly why the
`/api/auth/google/*` prefix is exempt from the double-submit gate.

## Frontend helper — `csrf.ts`

`installCsrfFetch()` (`csrf.ts:46`) monkeypatches `window.fetch` once
(idempotent via `window.__csrfFetchInstalled`, `csrf.ts:48-50`). The wrapper
injects `X-CSRF-Token` only when BOTH (`csrf.ts:59`):

- `method` is in `MUTATING_METHODS = {'POST','PUT','PATCH','DELETE'}` (`csrf.ts:19`)
- `isApiRequest(url)` is true (`csrf.ts:29`)

`isApiRequest` returns true only for same-origin `/api/*` URLs or URLs prefixed
by `config.API_URL` whose remainder starts with `/api/` (`csrf.ts:31-36`). This
**scope guard is critical**: it prevents leaking the token to third-party URLs.
When both match, it reads the cookie via `readCookie('csrf_token')`
(`csrf.ts:23`, a `document.cookie` regex parse), sets the header if not already
present, and forces `credentials: 'include'` (`csrf.ts:60-64`). With no token
(dev / pre-bootstrap) it is a harmless no-op.

Bootstrap is at [`react/react/src/main.tsx:7,13`](../../react/react/src/main.tsx)
— called at module top level before `AuthProvider`/`UsageStatsProvider`/
`DeckPackProvider` mount and fire their fetches.

## Socket.IO

Not covered by this gate. Socket.IO is a separate transport guarded by the CORS
origin allowlist in `flask_app/extensions.py`, called out in the `csrf.py`
module docstring (`csrf.py:25-26`).

## Tests

[`tests/test_csrf.py`](../../tests/test_csrf.py) — 10 tests, marked
`pytest.mark.flask` (`test_csrf.py:19`). They build a minimal app with
`init_csrf()` directly (not `create_app()`) to skip full-app boot. Coverage:
cookie issued on GET (`:70`), POST without header rejected (`:75`), matching
header passes (`:82`), mismatched header rejected (`:88`), missing cookie
rejected even with header (`:95`), `/api/auth/login` exempt (`:102`),
`/api/auth/google/callback` exempt (`:110`), `OPTIONS` exempt (`:116`), GET never
gated (`:122`), disabled flag allows unprotected mutations (`:128`).

**Isolation gotcha.** Because the default flag is derived from `is_development`,
toggling `is_development` in one test can flip `CSRF_PROTECTION_ENABLED` for the
whole worker and 403 other tests' POSTs. Use per-app config override
(`init_csrf` + `app.config['CSRF_PROTECTION_ENABLED']`), not module-level
patching. See `tests/test_config_secret_key.py` for the documented hazard.

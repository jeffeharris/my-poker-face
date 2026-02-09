# Security Best Practices Report

## Executive Summary
This review found multiple high-impact authorization gaps in backend routes and Socket.IO handlers. The biggest risk is that several admin/debug/experiment endpoints are callable without authentication or permission checks, enabling data exposure and costly background job execution.

For a hobby app that you plan to share publicly, I recommend fixing the Critical and High findings before launch. Medium findings can follow in a second pass.

---

## Critical Findings

### SBP-001: Unauthenticated admin pricing APIs allow arbitrary pricing changes/deletions
- Rule ID: FLASK-CSRF-001 / FLASK authz baseline
- Severity: Critical
- Location: `flask_app/routes/admin_dashboard_routes.py:965`, `flask_app/routes/admin_dashboard_routes.py:985`, `flask_app/routes/admin_dashboard_routes.py:1025`, `flask_app/routes/admin_dashboard_routes.py:1049`
- Evidence: Pricing routes are defined without `@_dev_only` / `@require_permission('can_access_admin_tools')` unlike surrounding admin routes.
- Impact: Any internet client can alter or delete pricing rows, corrupt cost analytics, and potentially break pricing-dependent behavior.
- Fix: Add admin permission guard decorator to all `/admin/pricing*` endpoints.
- Mitigation: Restrict `/admin/*` at edge (Caddy) with auth/IP allowlist until code fix is deployed.
- False positive notes: None in app code; these handlers are currently unguarded.

### SBP-002: Prompt debug endpoints are fully unauthenticated and expose sensitive capture data + trigger LLM replays
- Rule ID: FLASK-AUTHZ (missing route authorization)
- Severity: Critical
- Location: `flask_app/routes/prompt_debug_routes.py:33`, `flask_app/routes/prompt_debug_routes.py:218`, `flask_app/routes/prompt_debug_routes.py:303`, `flask_app/routes/prompt_debug_routes.py:485`
- Evidence: No auth/permission decorators on capture listing, replay/interrogate, stats, cleanup.
- Impact: External users can read internal prompt captures/history, run paid LLM replay/interrogation calls, and delete capture data.
- Fix: Require admin permission for all `/api/prompt-debug/*` and `/api/game/<game_id>/decision-quality` debug-style endpoints.
- Mitigation: Disable these routes in production behind an env flag.
- False positive notes: None in app code.

### SBP-003: Experiment and replay experiment control APIs are unauthenticated (cost and integrity risk)
- Rule ID: FLASK-AUTHZ
- Severity: Critical
- Location: `flask_app/routes/experiment_routes.py:2237`, `flask_app/routes/experiment_routes.py:2515`, `flask_app/routes/experiment_routes.py:2548`, `flask_app/routes/replay_experiment_routes.py:60`, `flask_app/routes/replay_experiment_routes.py:173`
- Evidence: Create/launch/pause/resume/archive endpoints have no authz decorator.
- Impact: Anyone can launch expensive background experiments, alter experiment state, and read experiment outputs.
- Fix: Require `can_access_admin_tools` (or dedicated experiment permission) for all experiment/replay routes.
- Mitigation: Temporarily block these routes at reverse proxy for non-admin callers.
- False positive notes: None in app code.

---

## High Findings

### SBP-004: Core HTTP game mutation endpoints do not enforce game ownership
- Rule ID: FLASK-AUTHZ
- Severity: High
- Location: `flask_app/routes/game_routes.py:993`, `flask_app/routes/game_routes.py:1063`, `flask_app/routes/game_routes.py:1091`, `flask_app/routes/game_routes.py:1146`, `flask_app/routes/game_routes.py:1159`, `flask_app/routes/game_routes.py:1184`, `flask_app/routes/game_routes.py:1193`
- Evidence: Endpoints perform actions/messages/retry/delete/end-game without checking `current_user.id == game.owner_id`.
- Impact: Anyone who obtains a `game_id` can play actions, inject chat, or delete/end a game.
- Fix: Centralize `require_game_owner(game_id)` and apply to all game-specific read/write routes.
- Mitigation: Increase game ID entropy (already strong) is not enough; enforce authz server-side.
- False positive notes: Not mitigated by frontend checks.

### SBP-005: Socket.IO events `send_message` and `progress_game` lack owner checks; CORS allows any origin
- Rule ID: FLASK-AUTHZ / cross-origin websocket hardening
- Severity: High
- Location: `flask_app/routes/game_routes.py:1348`, `flask_app/routes/game_routes.py:1373`, `flask_app/extensions.py:32`
- Evidence: `handle_send_message` and `on_progress_game` execute with no owner validation; SocketIO configured `cors_allowed_origins="*"`.
- Impact: Malicious clients can spam or force-progress games if `game_id` is known.
- Fix: Mirror `player_action` owner checks in all socket handlers; restrict Socket.IO allowed origins in production.
- Mitigation: Edge firewall/rate limits by path are only partial protection.
- False positive notes: None.

### SBP-006: Guest identity is trivially impersonable (`guest_<sanitized_name>`) and restored directly from cookie
- Rule ID: FLASK-SESS-002 / auth integrity
- Severity: High
- Location: `poker/auth.py:317`, `poker/auth.py:321`, `poker/auth.py:355`
- Evidence: Guest IDs are deterministic by display name, and `guest_id` cookie is trusted to recreate user identity.
- Impact: Guest-user collisions and easy impersonation of other guest accounts/games.
- Fix: Generate random guest IDs (UUID) and store display name separately; do not derive identity from mutable name.
- Mitigation: Short guest session lifetimes and reduced privileges until fixed.
- False positive notes: This is especially relevant since guest gameplay persistence exists.

### SBP-007: Experiment chat sessions use fallback owner `'anonymous'`, causing cross-user session leakage/tampering
- Rule ID: FLASK-AUTHZ/data isolation
- Severity: High
- Location: `flask_app/routes/experiment_routes.py:1640`, `flask_app/routes/experiment_routes.py:1689`, `poker/repositories/experiment_repository.py:727`
- Evidence: Session ownership key defaults to `'anonymous'` if `session['owner_id']` is absent; latest-session lookup is by owner_id.
- Impact: Different users can read/archive each other’s latest experiment design session.
- Fix: Bind owner to authenticated user ID (or per-session random UUID stored server-side), never global `'anonymous'`.
- Mitigation: Disable “resume latest” endpoint until ownership is corrected.
- False positive notes: `session['owner_id']` is not set elsewhere in current codebase.

---

## Medium Findings

### SBP-008: Personality metadata and mutation endpoints miss authz checks on some routes
- Rule ID: FLASK-AUTHZ
- Severity: Medium
- Location: `flask_app/routes/personality_routes.py:85`, `flask_app/routes/personality_routes.py:199`, `flask_app/routes/personality_routes.py:315`
- Evidence: `GET /api/personality/<name>`, avatar description update, and reference image update endpoints do not enforce owner/admin checks.
- Impact: Unauthorized read/changes to personality configuration details.
- Fix: Require authenticated owner or admin for private personality reads/writes.
- Mitigation: Restrict these endpoints to authenticated users at minimum.
- False positive notes: Depends on intended public visibility model, but write endpoints should still be protected.

### SBP-009: Prompt preset ID-based GET/PUT/DELETE lack ownership checks
- Rule ID: FLASK-AUTHZ
- Severity: Medium
- Location: `flask_app/routes/prompt_preset_routes.py:104`, `flask_app/routes/prompt_preset_routes.py:132`, `flask_app/routes/prompt_preset_routes.py:209`
- Evidence: Route handlers fetch/update/delete by `preset_id` without validating requester ownership (except system preset guard).
- Impact: IDOR risk: users can read/modify/delete other users’ presets if IDs are discovered.
- Fix: Enforce `owner_id == current_user.id` or admin permission for per-ID operations.
- Mitigation: Use unguessable IDs only as defense-in-depth, not as authorization.
- False positive notes: List endpoint is owner-filtered, but ID endpoints are not.

### SBP-010: Flask session cookie hardening is not explicitly set for production
- Rule ID: FLASK-SESS-001
- Severity: Medium
- Location: `poker/auth.py:44`, `poker/auth.py:45`
- Evidence: Session lifetime is set, but `SESSION_COOKIE_SECURE/HTTPONLY/SAMESITE` are not explicitly configured in app init.
- Impact: Risk of insecure cookie transport/handling if defaults or deployment assumptions differ.
- Fix: In production config set `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE='Lax'` (and keep secure false only for local HTTP dev).
- Mitigation: Enforce HTTPS + HSTS at edge after validating deployment behavior.
- False positive notes: Some defaults may already be safe, but explicit secure config is recommended.

---

## Additional Notes
- No direct SQL injection primitives were found in reviewed route handlers; SQL query construction mostly uses parameterized values.
- Test helper routes are behind `ENABLE_TEST_ROUTES=true`, which is a good pattern if production keeps it disabled.

## Recommended Fix Order (Practical)
1. Lock down admin/debug/experiment/replay endpoints with permission checks (SBP-001/002/003).
2. Add strict game ownership checks for all game HTTP + Socket.IO mutation events (SBP-004/005).
3. Fix guest identity model and experiment chat ownership isolation (SBP-006/007).
4. Address medium IDOR/cookie-hardening issues (SBP-008/009/010).

# TRIAGE: Pre-Release Tech Debt & Code Quality Review

> **Purpose**: Net-new findings from exhaustive codebase review. Separate from `TODO.md`.
> **Scope**: Full repo — Python backend, React frontend, Docker/DevOps, tests, scripts, docs.
> **Lens**: Pre-release punch list. Triaged by what blocks or embarrasses a release.

---

## Tier 1: Must-Fix Before Release

Issues that could cause incorrect behavior, crashes, or embarrassing UX for real users.

### Correctness Bugs

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T1-01 | `from_dict` copy-paste bug | `poker/poker_action.py:50-52` | Passes `dict_data['player_action']` as both player name and action. Player name gets replaced with action string. (Dead code today — but if ever called, would corrupt state.) | **FIXED** — deleted dead code file |
| T1-02 | Hand evaluator sort bug | `poker/hand_evaluator.py:871-873` | Calls `sorted()` on already-sorted values, breaking element-by-element comparison. Can determine wrong winner in edge cases. | **FIXED** — removed redundant `sorted()` wrapper |
| T1-03 | Two-pair kicker calculation | `poker/hand_evaluator.py:165` | Returns single kicker value instead of proper list. Wrong winner determination in two-pair scenarios. | **DISMISSED** — single-element kicker list is correct for 5-card hand |
| T1-04 | `_check_two_pair` includes trips | `poker/hand_evaluator.py:162` | Uses `count >= 2` which matches trips/quads. Should be `== 2` for pair detection. | **FIXED** — changed `>= 2` to `== 2` |
| T1-05 | Raise validation bypass | `poker/poker_game.py:488` | `validate_and_sanitize()` called but sanitized amount not enforced. AI can pass invalid raise amounts. | **DISMISSED** — sanitized_amount IS enforced in `player_raise()` |
| T1-06 | Max raise logic non-standard | `poker/controllers.py:728` | `max_raise = min(player_stack, max_opponent_stack, pot * 2)` — pot×2 cap is not standard poker rules. Artificially limits raises. | **FIXED** — removed artificial pot×2 cap |
| T1-07 | `player_idx=0` falsy bug | `poker/poker_game.py:394` | `player_idx = player_idx or game_state.current_player_idx` — `player_idx=0` (first player) incorrectly defaults. Should use `if player_idx is None:`. | **FIXED** — changed `or` to `is None` check |
| T1-08 | Infinite loop risk | `poker/poker_game.py:618-645` | `get_next_active_player_idx` returns inactive player if no active players found. Can cause invalid game states. | **DISMISSED** — replaced by T1-21 |
| T1-09 | Missing `max_winnable` data | `poker/controllers.py:806-818` | Enricher callback builds `max_winnable` but `all_players_bets` isn't available in scope. Can crash or give wrong pot odds. | **DISMISSED** — `game_state` correctly in closure scope |
| T1-10 | Name collision in player comparison | `poker/poker_game.py:437` | `reset_player_action_flags` compares players by `name` instead of index. Fails if two players share a name. | **FIXED** — uses enumerate + index instead of name |
| T1-21 | `get_next_active_player_idx` returns invalid | `poker/poker_game.py:618-645` | Refactored to return `Optional[int]` — returns `None` when no active players found. | **FIXED** — new item replacing T1-08 |

### UX Blockers

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T1-11 | No React error boundaries | All React components | Single component error crashes entire app to white screen. No graceful degradation. | **FIXED** — created ErrorBoundary.tsx component |
| T1-12 | Socket memory leak | `react/src/hooks/useSocket.ts:19-24` | `onConnect`/`onDisconnect` listeners added but never removed in cleanup. Memory leak on remount. | **DISMISSED** — `socket.disconnect()` removes all listeners |
| T1-13 | Missing loading/error states | `react/src/App.tsx:136-174` | `handleQuickPlay` only shows loading on success path. Errors fail silently — user clicks, nothing happens. | **FIXED** — added toast notifications |
| T1-14 | No offline detection | React app | No `navigator.onLine` check or offline banner. Network drop → frozen UI with no feedback. | **FIXED** — created useOnlineStatus hook |
| T1-15 | Silent API failures | All React fetch calls | Failed API calls logged to console only. User never informed. Need toast notification system. | **FIXED** — installed react-hot-toast, added Toaster |

### Security Basics

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T1-16 | Predictable game IDs | `flask_app/routes/game_routes.py:189-191` | `generate_game_id()` uses `time.time() * 1000`. Easily guessable. Use `secrets.token_urlsafe()`. | **FIXED** — switched to `secrets.token_urlsafe(16)` |
| T1-17 | No input validation on actions | `flask_app/routes/game_routes.py:979-1024` | `/api/game/<game_id>/action` doesn't validate player exists, it's their turn, or action is valid. Negative amounts allowed. | **FIXED** — created shared validation.py |
| T1-18 | Unprotected WebSocket handlers | `flask_app/routes/game_routes.py:1198-1286` | `on_join`, `handle_player_action` — no auth checks. Anyone can join any game and take actions. | **FIXED** — added owner + is_human auth checks |
| T1-19 | SECRET_KEY regenerated on restart | `flask_app/config.py:18` | Falls back to `os.urandom(32).hex()` if not set. Invalidates all sessions on restart. | **FIXED** — stable default in dev, required in prod |
| T1-20 | SQL injection risk in admin | `flask_app/routes/admin_dashboard_routes.py:1724` | Table names via f-string interpolation. Whitelist check exists but pattern is dangerous. | **DISMISSED** — hardcoded whitelist is robust |

---

## Tier 2: Should-Fix Before Release

Issues that won't crash but indicate quality problems that could bite early users.

### Architecture & Design

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T2-01 | Immutable/mutable confusion | `poker/poker_state_machine.py:458-485` | State machine provides BOTH immutable methods (`with_game_state()`) and mutable setters. Callers can't reason about behavior. Pick one paradigm. | |
| T2-02 | Adapter reimplements core logic | `flask_app/game_adapter.py:20-44` | `current_player_options` duplicated with incomplete version (missing raise caps, heads-up rules, BB special case). Should delegate to core. | |
| T2-03 | Global mutable game state | `flask_app/services/game_state_service.py:14-17` | **Consolidated into T2-29 (multi-worker scaling).** Per-game locks already in place. Remaining gaps only matter with multiple workers. | |
| T2-04 | Config scattered across 6+ locations | `poker/config.py`, `core/llm/config.py`, `flask_app/config.py`, `react/src/config.ts`, `.env`, DB `app_settings` | No single source of truth. Settings can conflict. | |
| T2-05 | DB connection created per config lookup | `flask_app/config.py:44-94` | Config getter functions like `get_default_provider()` instantiate `GamePersistence()` on every call. New DB connection per lookup. | **FIXED** — @lru_cache shared instance |
| T2-06 | Three layers of caching, no invalidation | localStorage + in-memory dict + SQLite | Game state cached at three levels with no clear invalidation strategy. Stale data bugs likely. | |
| T2-07 | AI controller state can desync | `flask_app/handlers/game_handler.py:107-200` | AI conversation history, personality state, psychology stored separately from game state. Can desync. | |

### Code Quality

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T2-08 | `PokerAction` class entirely unused | `poker/poker_action.py` | Entire file is dead code (zero imports). Game uses plain dicts. Remove file. | **FIXED** — file deleted as part of T1-01 |
| T2-09 | O(n²) player flag reset | `poker/poker_game.py:422-437` | Calls `update_player` per player in loop, each creating new tuple copy. Build new tuple in one pass. | **FIXED** — single-pass tuple comprehension |
| T2-10 | `controllers.py` is 1794-line god object | `poker/controllers.py:554-1794` | `AIPlayerController` has 7 responsibilities: LLM, prompts, memory, analysis, resilience, evaluation, normalization. Split into services. | |
| T2-11 | `usePokerGame` hook is 588 lines | `react/src/hooks/usePokerGame.ts` | Socket management, state, messages, winners, tournaments all in one. Impossible to unit test. Split into focused hooks. | |
| T2-12 | 120 console.log statements in production | 38 React files | No logging levels. Sensitive data in browser console. Performance overhead. | **FIXED** — created logger utility, deleted ~25 debug statements, converted ~70 console calls |
| T2-13 | `any` types erode TypeScript safety | 32 occurrences in 16 files | `community_cards?: any[]`, `winnerInfo: any`, `[key: string]: any`. Defeats purpose of TypeScript. | **FIXED** — zero `: any` type annotations remain in codebase |
| T2-14 | Shuffle mutates module-level list | `poker/utils.py:86` | `random.shuffle(celebrities_list)` mutates in-place. Use `random.sample()` instead. | **FIXED** — uses `random.sample()` |
| T2-15 | `setup_helper.py` references non-existent file | `setup_helper.py:123` | Says `python working_game.py` — file doesn't exist. Script is broken. | **FIXED** — deleted dead file |

### Reliability

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T2-16 | No LLM retry at provider level | All text LLM providers | Player decisions are well-covered: `@with_ai_fallback` (3 retries + exponential backoff + circuit breaker) and `_get_ai_decision` (correction prompt → personality fallback). Image providers (Pollinations/Runware) also have retries. **Actual gap**: non-decision calls (commentary, chat suggestions, personality/theme generation) have no retry — transient 500s or timeouts silently fail. Lower priority than originally assessed. | **FIXED** — added retry (2 retries, exponential backoff) in `LLMClient.complete()` for all call types |
| T2-17 | HTTP client never closed | `core/llm/providers/http_client.py:10-17` | Module-level `httpx.Client()` singleton has no shutdown hook. Connection leak in long-running processes. | **FIXED** — added `atexit` cleanup handler |
| T2-18 | UsageTracker singleton not thread-safe | `core/llm/tracking.py:84-110` | `_instance` check-and-set has race condition. Multiple threads can create multiple instances. | **FIXED** — added threading.Lock for singleton |
| T2-19 | Unbounded game state memory growth | `flask_app/services/game_state_service.py:14-16` | `games` dict stores all active games. Abandoned games never evicted. No TTL or LRU. | **FIXED** — added 2-hour TTL with auto-cleanup |
| T2-20 | Unbounded message list growth | `flask_app/handlers/message_handler.py:82-126` | Messages appended without limit. Long games accumulate unbounded messages in memory. | **FIXED** — capped at 200 entries, trim on append |
| T2-21 | Race condition in game state updates | `flask_app/handlers/game_handler.py:1005-1098` | **Consolidated into T2-29 (multi-worker scaling).** Per-game locking already in place. Remaining gaps only matter with multiple workers. | |
| T2-22 | Conversation memory trims by count, not tokens | `core/llm/conversation.py:58-61` | Trims at 15 messages regardless of token count. Long system prompts + 15 messages can exceed context limits. | **DISMISSED** — memory cleared each turn, usage is only 6.6% of 128k context |

### Frontend Quality

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T2-23 | No frontend tests at all | `react/react/` | Zero `.test.tsx` files. No unit, integration, or E2E tests. Regressions undetected. | |
| T2-24 | Missing ARIA labels | All interactive elements | Only 49 ARIA attributes across 21 files vs 1275+ interactive elements. Screen reader users blocked. | |
| T2-25 | No keyboard navigation for poker actions | PokerTable components | Mouse/touch only. Keyboard-only users can't play. | |
| T2-26 | No code splitting | `react/src/App.tsx:286-371` | All routes imported synchronously. Admin panel code loaded for all users (~500KB+ unnecessary). | **FIXED** — React.lazy for 11 route components; core path (GamePage, GameMenu, LoginForm) stays eager |
| T2-27 | `GameContext` violates SoC | `react/src/contexts/GameContext.tsx` | WebSocket, HTTP API, state management, message dedup all in one file. Hard to test or debug. | |
| T2-28 | Duplicate socket event handling | `GameContext.tsx` + `usePokerGame.ts` | Both handle socket events independently. Confusing ownership, potential conflicts. | |

### DevOps

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T2-29 | Multi-worker scaling (consolidates T2-03, T2-21) | `docker-compose.prod.yml:39`, `flask_app/extensions.py:30`, `flask_app/services/game_state_service.py`, `flask_app/handlers/game_handler.py` | Single gevent worker handles current load fine via green threads. **When ready to scale (30+ concurrent games or tournaments + live users):** (1) Add `message_queue=REDIS_URL` to `SocketIO()` init — Redis already running in prod. (2) Fix `async_mode='threading'` → auto-detect. (3) Bump to `-w 2`. (4) Audit `get_game()`/`set_game()` thread safety under real multi-worker load — per-game locks exist but dict-level ops rely on GIL. (5) Review `progress_game()` locking under concurrent workers. ~3-5 files, main risk is integration testing. | |
| T2-30 | No frontend health check | `docker-compose.prod.yml:51-57` | Frontend service has no healthcheck. Docker can't auto-recover if nginx crashes. | **FIXED** — added curl health check on nginx |
| T2-31 | No deploy rollback mechanism | `deploy.sh:29-30` | Previous containers destroyed before testing new ones. Failed deploy = downtime. | **FIXED** — tag images before build, auto-rollback on failed health check |
| T2-32 | Migration runs after health check | `.github/workflows/deploy.yml:100-107` | App goes live, THEN migration runs. If migration fails, app has wrong schema. | **FIXED** — reordered: migrations run before health check |
| T2-33 | Production includes dev dependencies | `Dockerfile:13` | `pip install -r requirements.txt` includes pytest in production image. | **FIXED** — split into requirements.txt + requirements-dev.txt, Dockerfile uses build arg |
| T2-34 | No pre-deploy database backup | `.github/workflows/deploy.yml:92-95` | Deploy has no backup step. Failed migration = data loss risk. | **FIXED** — added backup step before build |

---

## Tier 3: Post-Release Tech Debt

Issues to address once live, during ongoing development.

### Testing Gaps

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T3-01 | No `conftest.py` | `tests/` | No shared fixtures. Each test duplicates DB setup, mock configs, test data. | **FIXED** — added `conftest.py` with shared fixtures and `pytest.ini` |
| T3-02 | 19 poker modules with zero tests | `controllers.py`, `authorization.py`, `auth.py`, `response_validator.py`, + 15 more | Critical game logic completely untested. | |
| T3-03 | Placeholder tests with no assertions | `test_poker_game_mutations.py:101-117` | Methods have `pass` or only comments. False coverage. | **FIXED** — removed `TestPropertyMutationPatterns` class (zero-assertion pattern demos) |
| T3-04 | Skipped tests not tracked | `test_prompt_golden_path.py:192` | `@unittest.skip` with no GitHub issue. Tests forgotten. | **FIXED** — updated test to match current archetype system |
| T3-05 | Mixed unittest and pytest patterns | All test files | No standardization. Can't use pytest features consistently. | |
| T3-06 | No test coverage reporting | CI/CD pipeline | No `pytest-cov`, no coverage enforcement. Don't know what's tested. | **FIXED** — added `pytest-cov` with 40% floor (`--cov-fail-under=40`) |
| T3-07 | DB connection leaks in tests | `test_persistence.py:26-32` | `tearDown` unlinks file without closing DB connection first. | **DISMISSED** — `GamePersistence` uses `with sqlite3.connect()` per operation; no persistent connection to leak |
| T3-08 | No experiment integration tests | `experiments/` | 11 files, 0 integration tests. Tournament runner untested end-to-end. | |

### Performance & Scalability

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T3-09 | No SQLite connection pooling | `poker/persistence.py` | New connection for every operation. Performance bottleneck under load. | |
| T3-10 | Synchronous LLM calls block threads | `core/llm/client.py:145-211` | LLM API calls are synchronous. Block entire thread pool. | |
| T3-11 | Frontend re-renders on every socket event | React components | Full game state replacement triggers unnecessary re-renders. No `React.memo`. | |
| T3-12 | No pagination on game list | `flask_app/routes/game_routes.py:194` | Hardcoded `limit=10`, no offset support. | **FIXED** — added `limit` and `offset` query params (max 100), persistence layer supports offset |
| T3-13 | Hardcoded 600s HTTP timeout | `core/llm/providers/http_client.py:16` | 10-minute timeout for all operations. Can't configure per-request. | **FIXED** — configurable via `LLM_HTTP_TIMEOUT` env var |

### Code Organization

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T3-14 | Python/TypeScript types manually synced | `poker/poker_game.py` ↔ `react/src/types/game.ts` | No automated validation. `has_acted` exists in Python but is an internal game state flag — frontend doesn't need it. Types are currently in sync for all user-facing fields. Remains a maintenance risk long-term. | |
| T3-15 | Card formatting duplicated across languages | `flask_app/routes/game_routes.py:100-114` + `react/src/utils/cards.ts` | Changes require updating both Python and TypeScript. | **FIXED** — replaced inline `card_to_string` in game_routes with import from shared `card_utils`. Cross-language duplication remains inherent to the architecture. |
| T3-16 | DB path logic duplicated 3+ times | `core/llm/tracking.py:34-52`, `flask_app/config.py:98-101`, `scripts/dbq.py:27-31` | Each with different fallback paths. Include hardcoded absolute paths. | **FIXED** — consolidated to canonical version in `flask_app/config.py` |
| T3-17 | Schema version hardcoded, no migrations | `poker/persistence.py:20-39` | `SCHEMA_VERSION = 58` with manual migration comments. No Alembic or equivalent. | |
| T3-18 | Circular import workarounds | `flask_app/__init__.py:35-36`, `capture_config.py`, `persistence.py` | Lazy imports to avoid circular deps indicate architectural coupling. | |
| T3-19 | Inconsistent error response format | Flask routes | Mix of `{'error': str}`, `{'success': False}`, `{'message': str}`, `{'status': 'error'}`. | |
| T3-20 | `GET` allowed on destructive endpoint | `flask_app/routes/game_routes.py:1114` | `/api/end_game/<game_id>` accepts both GET and POST. GET should never mutate. | **FIXED** — POST only |

### Documentation & DX

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T3-21 | Outdated React CLAUDE.md | `react/CLAUDE.md` | Describes future architecture (FastAPI, Zustand) not current (Flask, hooks). | **FIXED** — rewritten to reflect actual stack, structure, and patterns |
| T3-22 | No API documentation | Flask routes | No OpenAPI/Swagger spec. Frontend devs must read Python code. | |
| T3-23 | Env var docs spread across 3 files | `.env.example`, `CLAUDE.md`, `DEVOPS.md` | Inconsistent and potentially conflicting. | |
| T3-24 | No `.editorconfig` | Project root | No editor settings for tabs/spaces, line endings. | **FIXED** — added `.editorconfig` |
| T3-25 | No dependabot or renovate | Missing `.github/dependabot.yml` | Dependency updates manual. Security patches could be missed. | **FIXED** — added `.github/dependabot.yml` |
| T3-26 | `__pycache__` files committed | `tests/` | `.gitignore` incomplete. Merge conflicts on cache files. | **FIXED** — `.gitignore` already covers `__pycache__/`, no cached files in repo |
| T3-27 | Makefile uses deprecated `docker-compose` | `Makefile:10-46` | Should use `docker compose` (v2). Won't work on newer systems. | **FIXED** — updated to `docker compose` v2 |
| T3-28 | GitHub Actions allows 50 lint warnings | `.github/workflows/deploy.yml:57` | `--max-warnings=50` should be 0 for clean codebase. | **FIXED** — set `--max-warnings=0` |

### Security (Non-Urgent)

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T3-29 | No CSRF protection | All POST endpoints | No CSRF tokens on state-changing endpoints. Combined with `cors_allowed_origins="*"` in dev. | |
| T3-30 | No rate limiting on socket events | Socket handlers | HTTP routes have rate limiting but socket.io events don't. Client can spam actions. | **FIXED** — added `@socket_rate_limit` decorator to all 4 socket handlers |
| T3-31 | No rate limiting on expensive AI endpoints | `personality_routes.py:346`, `image_routes.py:286` | `/api/generate-theme` makes LLM calls with no rate limit. Could drain API credits. | **FIXED** — added `@limiter.limit()` to generate-theme, regenerate-avatar, generate-character-images |
| T3-32 | Prompt injection risk | `poker/prompt_manager.py:43-87` | User-provided names/messages go into LLM prompts with minimal sanitization. | |
| T3-33 | CORS wildcard with credentials in dev | `flask_app/extensions.py:54-72` | `CORS(app, supports_credentials=True, origins=re.compile(r'.*'))` in dev mode. | **FIXED** — dev CORS pinned to localhost:5173/5174 + homehub:* pattern |
| T3-34 | Missing content-type validation on uploads | `admin_dashboard_routes.py:452-525` | Image upload trusts `file.content_type` from client. No magic byte validation. | **FIXED** — validates magic bytes (PNG/JPEG/GIF/WebP), overrides client content_type |

---

## Summary Statistics

| Tier | Total | Fixed | Dismissed | Open |
|------|-------|-------|-----------|------|
| **Tier 1: Must-Fix** | 21 | 13 | 7 | 0 |
| **Tier 2: Should-Fix** | 34 | 18 | 1 | 15 |
| **Tier 3: Post-Release** | 34 | 19 | 1 | 14 |
| **Total** | **89** | **50** | **9** | **29** |

## Key Architectural Insight

The most pervasive issue is the **immutable/mutable hybrid** in the state machine. The codebase claims functional/immutable architecture but provides mutable compatibility layers (`game_state.setter`, `advance_state()`). This creates confusion about which API to use and makes reasoning about state changes difficult. The recommendation: fully commit to immutability by removing all mutable interfaces, or accept mutability and simplify. The current hybrid gets the downsides of both.

---

## Legend
- **Tier 1**: Blocks or embarrasses release — fix before shipping
- **Tier 2**: Quality issues that bite early — fix before or shortly after release
- **Tier 3**: Tech debt to address during ongoing development
- **FIXED**: Resolved in ralph-wiggum work
- **DISMISSED**: Investigated and determined to be a false positive

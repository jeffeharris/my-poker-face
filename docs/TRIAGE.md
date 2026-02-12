---
purpose: Pre-release tech debt and code quality review findings triaged by release-blocking severity
type: reference
created: 2025-06-15
last_updated: 2026-02-12
---

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
| T1-22 | Missing ownership checks on game REST endpoints | `flask_app/routes/game_routes.py:350`, `flask_app/routes/game_routes.py:979`, `flask_app/routes/game_routes.py:1049`, `flask_app/routes/game_routes.py:1132`, `flask_app/routes/game_routes.py:1145`, `flask_app/routes/game_routes.py:1170`, `flask_app/routes/game_routes.py:1179` | Several state-mutating/read endpoints trust only `game_id` from URL and never verify current user owns that game. Any authenticated or unauthenticated caller with a valid game ID can read state/messages/LLM configs, submit actions/messages, or delete/end games. | **FIXED** — added shared owner-or-admin guard across game REST endpoints and aligned lifecycle by requiring auth on `/api/new-game` (prevents unreachable ownerless games). Verified by `tests/test_game_route_auth.py` (8 passed). |
| T1-23 | Unauthenticated SocketIO handlers still allow game manipulation | `flask_app/routes/game_routes.py:1334`, `flask_app/routes/game_routes.py:1359` | `send_message` and `progress_game` socket handlers have rate limits but no owner/auth checks (unlike `join_game` and `player_action`). Clients can emit to arbitrary game IDs and force progression or inject chat. | **FIXED** — `send_message` and `progress_game` now enforce owner-or-admin authorization and game existence checks before acting. Verified by `tests/test_websocket_auth.py` (15 passed). |
| T1-24 | Debug/experiment APIs exposed without admin authorization | `flask_app/routes/prompt_debug_routes.py:33`, `flask_app/routes/capture_label_routes.py:17`, `flask_app/routes/experiment_routes.py:1376`, `flask_app/routes/replay_experiment_routes.py:23` | Entire debug/experiment surfaces are unguarded by `require_permission('can_access_admin_tools')`. This exposes prompt captures and allows launching expensive background experiments/replays without admin access. | **FIXED** — enforced `can_access_admin_tools` at blueprint level for prompt debug, capture labels, experiments, and replay experiments. Added coverage in `tests/test_admin_experiment_route_auth.py` (6 passed) and updated experiment endpoint suites to run under admin auth mocks. |
| T1-25 | Prompt preset write operations bypass auth and ownership | `flask_app/routes/prompt_preset_routes.py:48`, `flask_app/routes/prompt_preset_routes.py:132`, `flask_app/routes/prompt_preset_routes.py:209`, `poker/repositories/prompt_preset_repository.py:200`, `poker/repositories/prompt_preset_repository.py:223` | Preset create/update/delete endpoints have no auth requirement, and repository update/delete queries are only keyed by `id` (no `owner_id`). Any caller who knows a preset ID can modify/delete another user's preset. Anonymous callers can also create presets with `owner_id=NULL`. | **FIXED** — create/update/delete now require authenticated user, enforce owner-or-admin authorization, and use owner-scoped repository mutations for non-admin requests |
| T1-26 | Guest identity is forgeable and collides across users | `poker/auth.py:317`, `poker/auth.py:355` | Guest ID is deterministic from display name (`guest_<sanitized_name>`) and restored from a plain `guest_id` cookie with only prefix validation. Attackers can forge guest cookies or choose the same name to assume another guest identity and access their games. | |
| T1-27 | Experiment chat sessions leak across anonymous users | `flask_app/routes/experiment_routes.py:1640`, `flask_app/routes/experiment_routes.py:1689`, `poker/repositories/experiment_repository.py:727` | Chat sessions are stored/looked up under `owner_id = session.get('owner_id', 'anonymous')`, but `owner_id` is never set elsewhere, so unrelated anonymous users share the same owner bucket. `chat/latest` can return another user's design session and config history. | |

---

## Tier 2: Should-Fix Before Release

Issues that won't crash but indicate quality problems that could bite early users.

### Architecture & Design

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T2-35 | Psychology system too complex | `poker/player_psychology.py`, `poker/elasticity_manager.py`, `poker/emotional_state.py`, `poker/tilt_modifier.py` | Current system has 4 elastic traits → 4 emotional dimensions → avatar emotion, plus separate tilt tracking. Hard to understand and maintain. **Proposal**: Replace with 5 poker-native traits (tightness, aggression, confidence, composure, table_talk) that directly map to behavior. Emotion derived from confidence × composure. Tilt = low composure. See [POKER_NATIVE_PSYCHOLOGY.md](/docs/plans/POKER_NATIVE_PSYCHOLOGY.md). | |
| T2-01 | ~~Immutable/mutable confusion~~ | `poker/poker_state_machine.py` | **Demoted to Tier 3** — see T3-35. Inner core is genuinely immutable; mutable wrapper is a thin convenience layer. Cognitive overhead only, no bug risk. | |
| T2-02 | Adapter reimplements core logic | `flask_app/game_adapter.py:20-44` | `current_player_options` duplicated with incomplete version (missing raise caps, heads-up rules, BB special case). Should delegate to core. | **FIXED** — adapter already delegated; moved `awaiting_action`/`run_it_out` guards to `validation.py` where they belong |
| T2-03 | Global mutable game state | `flask_app/services/game_state_service.py:14-17` | **Consolidated into T2-29 (multi-worker scaling).** Per-game locks already in place. Remaining gaps only matter with multiple workers. | **DISMISSED** — consolidated into T2-29 (now T3-40) |
| T2-04 | ~~Config scattered across 6+ locations~~ | `poker/config.py`, `core/llm/config.py`, `flask_app/config.py`, `react/src/config.ts`, `.env`, DB `app_settings` | **Demoted to Tier 3** — see T3-36. On investigation: each file has a distinct role (game constants, LLM defaults, Flask settings, frontend, env vars, runtime overrides). Clear priority hierarchy (DB > env > hardcoded). Separation is intentional to avoid circular imports. No bugs from conflicts. | |
| T2-05 | DB connection created per config lookup | `flask_app/config.py:44-94` | Config getter functions like `get_default_provider()` instantiate `GamePersistence()` on every call. New DB connection per lookup. | **FIXED** — @lru_cache shared instance |
| T2-06 | Three layers of caching, no invalidation | localStorage + in-memory dict + SQLite | Game state cached at three levels with no clear invalidation strategy. Stale data bugs likely. | **DISMISSED** — layers serve distinct purposes and are properly synchronized: localStorage is optimistic UI (always refetched from API on mount), in-memory has 2h TTL (T2-19), SQLite is source of truth written after every action. No stale-state bugs observed. |
| T2-07 | AI controller state can desync | `flask_app/handlers/game_handler.py:107-200` | AI conversation history, personality state, psychology stored separately from game state. Can desync. | **DISMISSED** — separation is intentional (immutable game state vs mutable AI learning). Both saved/loaded together per action with per-game locking. Psychology unified into single `PlayerPsychology` dict. No desync observed. |

### Code Quality

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T2-08 | `PokerAction` class entirely unused | `poker/poker_action.py` | Entire file is dead code (zero imports). Game uses plain dicts. Remove file. | **FIXED** — file deleted as part of T1-01 |
| T2-09 | O(n²) player flag reset | `poker/poker_game.py:422-437` | Calls `update_player` per player in loop, each creating new tuple copy. Build new tuple in one pass. | **FIXED** — single-pass tuple comprehension |
| T2-10 | ~~`controllers.py` is 1794-line god object~~ | `poker/controllers.py:554-1794` | `AIPlayerController` has 7 responsibilities: LLM, prompts, memory, analysis, resilience, evaluation, normalization. Split into services. | **Demoted to Tier 3** — see T3-41. Large refactor with limited pre-release ROI. |
| T2-11 | ~~`usePokerGame` hook is 588 lines~~ | `react/src/hooks/usePokerGame.ts` | Socket management, state, messages, winners, tournaments all in one. Impossible to unit test. Split into focused hooks. | **Demoted to Tier 3** — see T3-42. Concerns are tightly coupled; splitting creates ref sync issues. Internal cleanup (extract pure functions) is better approach. |
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
| T2-21 | Race condition in game state updates | `flask_app/handlers/game_handler.py:1005-1098` | **Consolidated into T2-29 (multi-worker scaling).** Per-game locking already in place. Remaining gaps only matter with multiple workers. | **DISMISSED** — consolidated into T2-29 (now T3-40) |
| T2-22 | Conversation memory trims by count, not tokens | `core/llm/conversation.py:58-61` | Trims at 15 messages regardless of token count. Long system prompts + 15 messages can exceed context limits. | **DISMISSED** — memory cleared each turn, usage is only 6.6% of 128k context |

### Frontend Quality

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T2-23 | ~~No frontend tests at all~~ | `react/react/` | Zero `.test.tsx` files. No unit, integration, or E2E tests. Regressions undetected. | **Demoted to Tier 3** — see T3-37. Some test coverage now exists; remaining gaps are post-release work. |
| T2-24 | ~~Missing ARIA labels~~ | All interactive elements | Only 49 ARIA attributes across 21 files vs 1275+ interactive elements. Screen reader users blocked. | **Demoted to Tier 3** — see T3-38. Accessibility improvements are ongoing post-release work. |
| T2-25 | ~~No keyboard navigation for poker actions~~ | PokerTable components | Mouse/touch only. Keyboard-only users can't play. | **Demoted to Tier 3** — see T3-39. Accessibility improvements are ongoing post-release work. |
| T2-26 | No code splitting | `react/src/App.tsx:286-371` | All routes imported synchronously. Admin panel code loaded for all users (~500KB+ unnecessary). | **FIXED** — React.lazy for 11 route components; core path (GamePage, GameMenu, LoginForm) stays eager |
| T2-27 | `GameContext` violates SoC | `react/src/contexts/GameContext.tsx` | WebSocket, HTTP API, state management, message dedup all in one file. Hard to test or debug. | **DISMISSED** — file deleted in T2-28; SoC concern for `usePokerGame` tracked by T2-11 |
| T2-28 | Duplicate socket event handling | `GameContext.tsx` + `usePokerGame.ts` | Both handle socket events independently. Confusing ownership, potential conflicts. | **FIXED** — deleted unused `GameContext.tsx`; `usePokerGame` is the sole socket handler |

### DevOps

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T2-29 | ~~Multi-worker scaling~~ (consolidates T2-03, T2-21) | `docker-compose.prod.yml:39`, `flask_app/extensions.py:30`, `flask_app/services/game_state_service.py`, `flask_app/handlers/game_handler.py` | **Demoted to Tier 3** — see T3-40. Single worker handles current load fine. Only matters at 30+ concurrent games. | |
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
| T3-07 | DB connection leaks in tests | `test_persistence.py:26-32` | `tearDown` unlinks file without closing DB connection first. | **FIXED** — BaseRepository now uses thread-local connection reuse with explicit `close()` for cleanup |
| T3-08 | No experiment integration tests | `experiments/` | 11 files, 0 integration tests. Tournament runner untested end-to-end. | |
| T3-37 | Expand frontend test coverage | `react/react/` | Some test coverage now exists but gaps remain. Add unit tests for hooks, components, and game logic utilities. *(Demoted from T2-23)* | |

### Accessibility

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T3-38 | Missing ARIA labels | All interactive elements | Only 49 ARIA attributes across 21 files vs 1275+ interactive elements. Screen reader users blocked. *(Demoted from T2-24)* | |
| T3-39 | No keyboard navigation for poker actions | PokerTable components | Mouse/touch only. Keyboard-only users can't play. *(Demoted from T2-25)* | |

### Performance & Scalability

> **See also**: [SCALING.md](/docs/technical/SCALING.md) for comprehensive scaling thresholds, migration paths, and horizontal scaling guidance.

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T3-09 | No SQLite connection pooling | `poker/persistence.py` | New connection for every operation. Performance bottleneck under load. | **FIXED** — BaseRepository uses thread-local connection reuse with WAL mode |
| T3-10 | Synchronous LLM calls block threads | `game_handler.py:1360`, `controllers.py:868/931`, `core/llm/client.py` | LLM calls are synchronous but concurrency works in practice: SocketIO uses `async_mode='threading'` (each connection gets its own thread), experiments spawn daemon threads, avatars run in background threads. Multiple games/tournaments run concurrently fine. Real risk is thread pool exhaustion under very high load (many concurrent AI decisions). Overlaps with T3-40 (multi-worker scaling). Low priority unless scaling significantly. | |
| T3-11 | Frontend re-renders on every socket event | `usePokerGame.ts:126`, `PokerTable.tsx`, all game components | Zero `React.memo` in game components. Every socket event (5-10/sec during play) replaces entire `gameState` object, re-rendering all ~50+ components including cards, player seats, stats, messages. Fix in phases: (1) `React.memo` on leaf components — 30-50% reduction, (2) split `gameState` into multiple `useState` hooks — 70-80% reduction, (3) consider Zustand for selector-based subscriptions. | |
| T3-12 | No pagination on game list | `flask_app/routes/game_routes.py:194` | Hardcoded `limit=10`, no offset support. | **FIXED** — added `limit` and `offset` query params (max 100), persistence layer supports offset |
| T3-13 | Hardcoded 600s HTTP timeout | `core/llm/providers/http_client.py:16` | 10-minute timeout for all operations. Can't configure per-request. | **FIXED** — configurable via `LLM_HTTP_TIMEOUT` env var |
| T3-40 | Multi-worker scaling | `docker-compose.prod.yml`, `flask_app/extensions.py`, `game_state_service.py`, `game_handler.py` | Single worker handles current load. When scaling to 30+ concurrent games: (1) add `message_queue=REDIS_URL` to SocketIO init, (2) fix `async_mode`, (3) bump workers, (4) audit thread safety, (5) review `progress_game()` locking. Consolidates T2-03, T2-21. *(Demoted from T2-29)* | |

### Code Organization

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T3-14 | Python/TypeScript types manually synced | `poker/poker_game.py` ↔ `react/src/types/game.ts` | No automated validation. `has_acted` exists in Python but is an internal game state flag — frontend doesn't need it. Types are currently in sync for all user-facing fields. Remains a maintenance risk long-term. | |
| T3-15 | Card formatting duplicated across languages | `flask_app/routes/game_routes.py:100-114` + `react/src/utils/cards.ts` | Changes require updating both Python and TypeScript. | **FIXED** — replaced inline `card_to_string` in game_routes with import from shared `card_utils`. Cross-language duplication remains inherent to the architecture. |
| T3-16 | DB path logic duplicated 3+ times | `core/llm/tracking.py:34-52`, `flask_app/config.py:98-101`, `scripts/dbq.py:27-31` | Each with different fallback paths. Include hardcoded absolute paths. | **FIXED** — consolidated to canonical version in `flask_app/config.py` |
| T3-17 | Schema version hardcoded, no migrations | `poker/repositories/schema_manager.py` | `SCHEMA_VERSION = 62` with manual migration methods. No Alembic or equivalent. | |
| T3-18 | Circular import workarounds | `flask_app/__init__.py:35-36`, `capture_config.py` | Lazy imports to avoid circular deps indicate architectural coupling. | **FIXED** — persistence.py eliminated; repos imported directly |
| T3-19 | Inconsistent error response format | 15 files in `flask_app/routes/` | 219 error responses split 50/50: `{'error': ...}` (109, used by global error handlers) vs `{'success': False, 'error': ...}` (107, mostly admin routes). Standardizing to `{'error': ...}` requires ~107 changes across 7 files (admin_dashboard_routes.py alone has 61). Frontend may check `success: false`. | |
| T3-35 | God Object: `GamePersistence` was 9,000 lines | `poker/persistence.py` → `poker/repositories/` | Split into 10 domain repositories. Facade removed, all callers updated. | **FIXED** |
| T3-36 | Remove `GamePersistence` facade after repo extraction | `poker/persistence.py`, 40+ caller files | After repo extraction, update all callers to import repos directly and remove the facade class. | **FIXED** |
| T3-37 | `ExperimentRepository` is a replacement god class | `poker/repositories/experiment_repository.py` | At 3,630 lines with 58 methods across 7 concerns (prompt captures, playground captures, decision analysis, presets, labels, experiment lifecycle/chat, replay). Split into at least 3 repositories: `CaptureRepository`, `ExperimentLifecycleRepository`, `ReplayRepository`. Also contains 4 methods duplicated from `GameRepository` and 6 raw `sqlite3.connect()` calls bypassing `BaseRepository`. | **FIXED** — split into 6 focused repositories (#131) |
| T3-20 | `GET` allowed on destructive endpoint | `flask_app/routes/game_routes.py:1114` | `/api/end_game/<game_id>` accepts both GET and POST. GET should never mutate. | **FIXED** — POST only |
| T3-35 | Dual API on state machine wrapper | `poker/poker_state_machine.py:336-539` | Outer `PokerStateMachine` exposes both mutable (`advance_state()`, `game_state` setter) and immutable (`advance()`, `with_game_state()`) APIs. Inner `ImmutableStateMachine` core is genuinely pure — mutable setters just reassign `self._state` with new frozen instances. Cleanup: remove duplicate immutable methods from outer class, keep mutable wrapper only. *(Demoted from T2-01)* | |
| T3-36 | Config naming & documentation | 6 config locations | Config spread across `poker/config.py`, `core/llm/config.py`, `flask_app/config.py`, `react/src/config.ts`, `.env`, DB `app_settings` is intentional (avoids circular imports), but naming is inconsistent (e.g., `.env` uses `OPENAI_MODEL`, DB uses `DEFAULT_MODEL`). Improvements: unify setting names in `.env.example`, document priority hierarchy (DB > env > hardcoded). *(Demoted from T2-04)* | **FIXED** — renamed `OPENAI_MODEL` → `DEFAULT_MODEL` in `.env.example` and `config.py` (with legacy fallback), removed undocumented `OPENAI_FAST_MODEL`, added priority hierarchy docs, documented all model tier env vars |
| T3-41 | Split `AIPlayerController` god object | `poker/controllers.py:554-1794` | 7 responsibilities: LLM, prompts, memory, analysis, resilience, evaluation, normalization. Extract into focused service classes. *(Demoted from T2-10)* | |
| T3-42 | Clean up `usePokerGame` hook | `react/src/hooks/usePokerGame.ts` | 661-line hook with tightly coupled concerns. Splitting into separate hooks creates ref sync issues. Better approach: extract pure functions (message dedup, avatar caching), organize socket handlers into named setup functions. *(Demoted from T2-11)* | |
| T3-43 | `experiment_routes.py` god route file | `flask_app/routes/experiment_routes.py` | 3,044 lines, 25 endpoints, 42+ functions mixing 5+ concerns: experiment lifecycle, AI assistant chat, live monitoring, config validation, background thread management, SQL tool execution. Split into `experiment_routes.py` (CRUD), `experiment_lifecycle_routes.py`, `experiment_assistant_routes.py`, `experiment_monitoring_routes.py`. | |
| T3-44 | `schema_manager.py` monolith | `poker/repositories/schema_manager.py` | 2,949 lines. Single `_init_db()` contains CREATE TABLE for all 25+ tables (~1,500 lines) plus 63 migration methods (most are no-ops). Schema and migrations tightly coupled. Extends T3-17. Split schema definitions by domain and archive historical migrations. | |
| T3-45 | `run_ai_tournament.py` god script | `experiments/run_ai_tournament.py` | 2,513 lines, 13 classes. Single file contains dataclass definitions, rate limiting, worker thread management, game simulation loop, pause/resume coordination, AI interpretation, CLI parsing. `AITournamentRunner` alone is ~1,200 lines. Split into config, runner, worker, simulator, and CLI modules. | |
| T3-46 | `admin_dashboard_routes.py` oversized | `flask_app/routes/admin_dashboard_routes.py` | 1,794 lines, 34 endpoints mixing admin UI redirects, analytics, model management, data export, and complex SQL query builders (~400 lines). Move analytics to dedicated service layer; extract model management to separate route file. | |
| T3-47 | `game_handler.py` mixed responsibilities | `flask_app/handlers/game_handler.py` | 1,465 lines, 21 top-level functions. Named "handler" but contains game loop logic, avatar reactions, pressure detection, tournament completion, AI commentary generation. **Hotspot**: `handle_evaluating_hand_phase()` is 215 lines doing 14+ operations (winner determination, pot award, showdown prep, async commentary spawn, pressure events, memory update, coaching progression, eliminations, tournament check, psychology recovery, new hand setup, guest tracking). `progress_game()` is 165 lines with ~100 lines of run-it-out logic (nested conditionals, sleep delays, reaction scheduling). **Split into**: `game_loop.py` (progress_game, phase transitions), `hand_completion.py` (handle_evaluating_hand_phase, showdown, winners), `ai_action_handler.py` (handle_ai_action, decision execution), `tournament_handler.py` (eliminations, completion), `runout_handler.py` (run-it-out reveals, reaction scheduling), `commentary_handler.py` (generate_ai_commentary, memory feeding). | |
| T3-55 | Duplicated psychology pipeline between experiments and game handler | `poker/psychology_pipeline.py` | **FIXED**: Removed ~480 lines of duplicated code across two files; replaced with shared 520-line module. Both `game_handler.py` and `run_ai_tournament.py` invoke the unified `PsychologyPipeline`. Unified divergences: opponent logic, session_context, key_moment, clear_hand_bluff_likelihood, and state persistence. | FIXED |
| T3-56 | Per-action wiring duplicated between experiment runner and Flask | `experiments/run_ai_tournament.py:1078`, `flask_app/handlers/message_handler.py:74` | `memory_manager.on_action()` (opponent model tracking, c-bet detection) is called in two separate places with duplicated logic for extracting phase name, active players, and pot total from game state. Same for `controller.opponent_model_manager` wiring (3 places in Flask game_routes, 1 in experiment runner). Any new per-action hook (e.g., bet sizing patterns) must be added in both places. Extract into a shared `on_player_action(memory_manager, player_name, action, amount, game_state, state_machine)` function. | |
| T3-57 | `_store_result` silently drops DB write failures | `experiments/run_replay_experiment.py:433` | `_store_result` catches all exceptions, logs them, and continues. Results are permanently lost with no indication in the experiment summary. An experiment could silently lose 15-20% of results (e.g., from transient DB locks) and report as "completed". Track storage failure count and surface in summary, or re-raise after retry. | |
| T3-58 | Replay repo UPDATE methods succeed silently on nonexistent IDs | `poker/repositories/replay_experiment_repository.py:191-216` | `update_experiment_status` and `complete_experiment` execute UPDATE without checking `cursor.rowcount`. If `experiment_id` doesn't exist, zero rows are updated and the caller believes success. `complete_experiment` losing the summary is a data loss event. Check rowcount and raise/warn on zero affected rows. | |
| T3-59 | `create_replay_experiment` silently links nonexistent capture IDs | `poker/repositories/replay_experiment_repository.py:76-100` | When linking captures, if a `capture_id` doesn't exist in `prompt_captures`, `original_action` is set to `None` and a phantom row is still inserted into `replay_experiment_captures`. Experiment appears to have N captures but only some exist. Log warning and skip missing captures, or raise. | |
| T3-60 | JSON parse failure in `_replay_capture` produces `success=True` | `experiments/run_replay_experiment.py:342-348` | When LLM returns non-JSON, the response is caught and action set to `'unknown'`, but `ReplayResult` is marked `success=True`. Inflates "actions changed" counts and corrupts quality metrics. The `parse_error` key in `result_data` is never checked downstream. Mark as `success=False` or exclude from aggregate metrics. | |

### Frontend Code Organization

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T3-48 | `DecisionAnalyzer.tsx` oversized component | `react/react/src/components/admin/DecisionAnalyzer/DecisionAnalyzer.tsx` | 2,026 lines — largest React component. Single component handles list view, detail view, replay mode, interrogation mode, JSON export, pagination. 50+ useState hooks, 30+ useEffect hooks. Split into `DecisionList`, `DecisionDetail`, `ReplayMode`, `InterrogationMode` with extracted hooks. | |
| T3-49 | `PersonalityManager.tsx` oversized component | `react/react/src/components/admin/PersonalityManager.tsx` | 1,989 lines with 16 inline sub-components. Mixes personality CRUD, trait editing with elasticity sliders, avatar image management, verbal/physical tics editor, collapsible section logic (duplicated 5+ times), mobile layouts. 20+ useState hooks. Extract sub-components into separate files. | |
| T3-50 | `UnifiedSettings.tsx` settings overload | `react/react/src/components/admin/UnifiedSettings.tsx` | 1,252 lines managing 5 settings categories (models, capture, storage, pricing, appearance) in one component. Category switching intertwined with data fetching. Extract each category into its own component, keep `UnifiedSettings` as thin switcher (~200 lines). | |
| T3-51 | `ConfigPreview.tsx` kitchen sink | `react/react/src/components/admin/ExperimentDesigner/ConfigPreview.tsx` | 1,148 lines handling form view, JSON view, config validation, version history, personality selection, prompt preset loading, seed word generation, launch logic. Split into `ConfigFormView`, `ConfigJsonView`, and extracted hooks for validation/versions/seeds. | |
| T3-52 | Duplicated collapsible section pattern | `PersonalityManager.tsx`, `UnifiedSettings.tsx`, `ConfigPreview.tsx` | Same collapsible section UI logic repeated across 3+ admin components. Also duplicated mobile filter sheet boilerplate. Extract to shared `CollapsibleSection.tsx` and `MobileFilterSheet.tsx` components. | |
| T3-53 | Personality name collision — UUID primary keys | `poker/repositories/personality_repository.py`, `schema_manager.py` | `personalities` table uses `name TEXT UNIQUE` as the lookup key. Two users cannot independently create personalities with the same name — `INSERT OR REPLACE` overwrites silently. Full fix requires UUID primary keys with `UNIQUE(name, owner_id)`, migrating all FK references (`avatar_images.personality_name`), API routes, and frontend state. Current mitigation: reject creates if name already exists (409 error). | |
| T3-54 | Psychology amount uses pot size, not actual loss | `flask_app/handlers/game_handler.py:481` | `amount = -pot_size` for all non-winners, but folders only lost their contribution (blinds/bets), not the entire pot. A pre-flop folder who posted 1 BB gets `amount = -pot_size` (e.g., -150) instead of their actual loss (-50). Inflates loss amounts for psychology/tilt calculations, potentially triggering incorrect `big_loss` or `crippled` events. Fix: track each player's pot contribution and use that as their loss amount. | **FIXED** — now uses `game_state.pot.get(player.name, 0)` for actual contribution; winners get net profit (winnings - contribution) |

### Documentation & DX

| ID | Issue | Location | Description | Status |
|----|-------|----------|-------------|--------|
| T3-21 | Outdated React CLAUDE.md | `react/CLAUDE.md` | Describes future architecture (FastAPI, Zustand) not current (Flask, hooks). | **FIXED** — rewritten to reflect actual stack, structure, and patterns |
| T3-22 | No API documentation | Flask routes | No OpenAPI/Swagger spec. Frontend devs must read Python code. | |
| T3-23 | Env var docs spread across 3 files | `.env.example`, `CLAUDE.md`, `DEVOPS.md` | Inconsistent and potentially conflicting. | **FIXED** — CLAUDE.md and DEVOPS.md now point to `.env.example` as canonical reference |
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
| **Tier 1: Must-Fix** | 21 | 15 | 6 | 0 |
| **Tier 2: Should-Fix** | 27 | 20 | 6 | 1 |
| **Tier 3: Post-Release** | 60 | 27 | 1 | 32 |
| **Total** | **108** | **62** | **13** | **33** |

## Key Architectural Insight

The state machine uses an **immutable/mutable hybrid** pattern. On deeper investigation, this is more intentional than it first appears: the inner `ImmutableStateMachine` core is genuinely pure (frozen dataclass, pure transition functions), while the outer `PokerStateMachine` provides a mutable-style convenience API for Flask handlers. The mutable setters just reassign `self._state` with new frozen instances — no actual mutation of immutable objects. The main downside is cognitive overhead (two APIs on the same class), not correctness risk. A cleanup would remove the duplicate immutable methods from the outer class, but this is low priority.

---

## Legend
- **Tier 1**: Blocks or embarrasses release — fix before shipping
- **Tier 2**: Quality issues that bite early — fix before or shortly after release
- **Tier 3**: Tech debt to address during ongoing development
- **FIXED**: Resolved in ralph-wiggum work
- **DISMISSED**: Investigated and determined to be a false positive

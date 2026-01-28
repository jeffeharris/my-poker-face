# Implementation Plan

> Ordered by dependency. Ralph picks the first unchecked task each iteration.
> Check boxes when complete. Add notes in brackets if dismissed or failed.

## Phase 1: Pure Backend Fixes (no dependencies)

- [x] T1-01: Delete `poker/poker_action.py` (verify zero imports, delete file)
- [x] T1-07: Fix `player_idx=0` falsy bug in `poker/poker_game.py:394`
- [x] T1-10: Fix name collision in `reset_player_action_flags` — use enumerate + index comparison
- [x] T1-16: Replace predictable game IDs with `secrets.token_urlsafe(16)`
- [x] T1-19: Fix SECRET_KEY — require in prod, stable default in dev

## Phase 2: Re-verify Items (investigate, fix or dismiss)

- [x] T1-02: RE-VERIFY hand evaluator sort bug (line reference was wrong)
- [x] T1-03: RE-VERIFY two-pair kicker calculation [Dismissed - kicker list with one element is correct for two-pair]
- [x] T1-04: RE-VERIFY + fix `_check_two_pair` count >= 2 -> == 2
- [x] T1-05: RE-VERIFY raise validation bypass (trace HTTP + socket paths) [Dismissed - sanitized_amount IS enforced inside player_raise; gaps are covered by T1-17/T1-18]
- [x] T1-09: RE-VERIFY missing max_winnable data (check closure scope) [Dismissed - game_state is correctly in closure scope, all_players_bets computed correctly]
- [x] T1-12: RE-VERIFY socket memory leak in useSocket.ts [Dismissed - socket.disconnect() removes all listeners; no accumulation]
- [x] T1-20: RE-VERIFY SQL injection in admin (check whitelist guard) [Dismissed - whitelist is hardcoded and guards all f-string SQL]

## Phase 3: Backend Logic (more complex, some interdependency)

- [x] T1-06: Remove pot x 2 raise cap from controllers.py and game_handler.py
- [x] T1-08: Investigate get_next_active_player_idx — trace callers, add ValueError
- [x] T1-17: Add shared input validation for player actions (both HTTP + socket)
- [x] T1-18: Add owner + is_human auth checks to WebSocket handlers

## Phase 4: Frontend (toast is prerequisite for others)

- [x] T1-15: Install react-hot-toast, add Toaster to App.tsx
- [x] T1-14: Add offline detection with toast notifications
- [x] T1-13: Add loading/error states to handleQuickPlay with toasts
- [x] T1-11: Add React error boundaries (top-level + per-route)

## Phase 5: Tier 2 Mechanical Fixes (no dependencies)

- [x] T2-14: Fix `get_celebrities()` shuffle mutation — use `random.sample()` instead of in-place shuffle
- [ ] T2-15: Delete dead `setup_helper.py` (references non-existent `working_game.py`)
- [ ] T2-09: Refactor `reset_player_action_flags` to single-pass tuple comprehension (O(n) instead of O(n²))
- [ ] T2-17: Add `atexit` cleanup for shared HTTP client in `core/llm/providers/http_client.py`
- [ ] T2-18: Add thread-safe double-checked locking to `UsageTracker.get_default()`

## Phase 6: Tier 2 Resource & Cleanup Fixes

- [ ] T2-05: Cache `GamePersistence` in config getters — `@lru_cache` shared instance
- [ ] T2-12: Remove debug console.log statements — create logger utility, delete noise, convert rest
- [ ] T2-19: Add TTL-based eviction for in-memory game state (2-hour expiry)
- [ ] T2-20: Cap in-memory message list at 200 entries with trim on append
- [x] T2-22: Conversation memory token trim [Dismissed - memory cleared each turn, usage is 6.6% of 128k context]

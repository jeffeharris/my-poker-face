# Implementation Plan

> Ordered by dependency. Ralph picks the first unchecked task each iteration.
> Check boxes when complete. Add notes in brackets if dismissed or failed.

## Phase 1: Pure Backend Fixes (no dependencies)

- [x] T1-01: Delete `poker/poker_action.py` (verify zero imports, delete file)
- [x] T1-07: Fix `player_idx=0` falsy bug in `poker/poker_game.py:394`
- [ ] T1-10: Fix name collision in `reset_player_action_flags` — use enumerate + index comparison
- [ ] T1-16: Replace predictable game IDs with `secrets.token_urlsafe(16)`
- [ ] T1-19: Fix SECRET_KEY — require in prod, stable default in dev

## Phase 2: Re-verify Items (investigate, fix or dismiss)

- [ ] T1-02: RE-VERIFY hand evaluator sort bug (line reference was wrong)
- [ ] T1-03: RE-VERIFY two-pair kicker calculation
- [ ] T1-04: RE-VERIFY + fix `_check_two_pair` count >= 2 -> == 2
- [ ] T1-05: RE-VERIFY raise validation bypass (trace HTTP + socket paths)
- [ ] T1-09: RE-VERIFY missing max_winnable data (check closure scope)
- [ ] T1-12: RE-VERIFY socket memory leak in useSocket.ts
- [ ] T1-20: RE-VERIFY SQL injection in admin (check whitelist guard)

## Phase 3: Backend Logic (more complex, some interdependency)

- [ ] T1-06: Remove pot x 2 raise cap from controllers.py and game_handler.py
- [ ] T1-08: Investigate get_next_active_player_idx — trace callers, add ValueError
- [ ] T1-17: Add shared input validation for player actions (both HTTP + socket)
- [ ] T1-18: Add owner + is_human auth checks to WebSocket handlers

## Phase 4: Frontend (toast is prerequisite for others)

- [ ] T1-15: Install react-hot-toast, add Toaster to App.tsx
- [ ] T1-14: Add offline detection with toast notifications
- [ ] T1-13: Add loading/error states to handleQuickPlay with toasts
- [ ] T1-11: Add React error boundaries (top-level + per-route)

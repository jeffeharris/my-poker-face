---
purpose: Scoped proof-of-concept plan and demo script for async poker with friends
type: guide
created: 2026-06-12
last_updated: 2026-06-12
---

> **Status (2026-06-12):** P1–P4 backend implemented and tested
> (`tests/test_repositories/test_async_friends_repos.py`,
> `tests/test_membership_service.py`, `tests/test_async_game_routes.py`,
> `tests/test_turn_notify.py`, `tests/test_device_routes.py`). P5 (frontend +
> iOS push registration) and the manual on-device APNs check remain.

# Async Poker With Friends — PoC Plan

Companion to the architecture in `docs/design/ASYNC_FRIENDS_DESIGN.md`. This is
the smallest end-to-end slice that proves async multiplayer works, plus what we
deliberately defer.

## PoC slice

**2 humans + 2 AI-fill seats in one shared async game.**

Why include AI-fill rather than a pure 2-human table: the novel behavior to
prove is *advancing AI turns when nobody is connected, then notifying the next
human*. A heads-up human-only game never makes an AI decision with no one
watching, so it under-tests the hard part. Timeouts, by contrast, are a policy
layer on already-working turn state — pure deferral, no architectural risk.

Must work:
- Both humans act **only on their own turn** (membership + per-turn auth).
- AI seats resolve via a **background orbit with no human connected**, persisting
  every step.
- State is **resumable from the DB** (evict from the in-memory registry, reload,
  identical phase/stacks/turn).
- One working notification path: **APNs "it's your turn"** when the target is
  offline.

## Explicitly deferred

- Turn **timeouts / auto-fold** (store `turn_deadline`, don't enforce — needs a
  scheduler).
- Android push, email, web push.
- First-class reserved-empty-seat engine model (use AI-placeholder→swap).
- Invite expiry/revocation polish, multi-use limits.
- Multi-server / distributed lock (single-server only).
- Spectators, mid-game leave/rejoin beyond basic seat claim.

## Phase checklist

- [x] **P1 — Schema + repos.** Migration `20260612_1200_async_friends`
      (`game_members`, `game_invites`, `user_devices`, `games` async/turn
      columns); `MembershipRepository`; `DeviceRepository`; `GameRepository`
      async-meta methods (`set_async_flag`, `set_turn_state`, `mark_turn_notified`,
      `get_async_meta`).
- [x] **P2 — Membership-aware auth.** `flask_app/services/membership_service.py`
      (`is_member` with owner fallback, `is_users_turn`/`resolve_turn_user`);
      swapped `owner_id` checks at `game_routes.py` `_authorize_game_access`,
      `on_join`, `player_action` (socket + REST, with per-turn gate),
      `send_message`, `progress_game`. Owner/admin retained for config/delete.
- [x] **P3 — Async lifecycle + AI-fill.** `flask_app/routes/async_game_routes.py`
      (`new`, `invite`, `join`, `mine`); extracted `build_and_persist_game(...)`
      from `api_new_game`; AI→`HumanSeat` swap on claim
      (`async_game_service.claim_open_seat`).
- [x] **P4 — Background progression + notifications.** Notify hook at the
      human-turn break in `progress_game` (`turn_notify.notify_turn_if_offline`,
      presence-gated, one push per turn); `flask_app/services/notifications/*`
      (channel ABC + APNs channel + dispatcher); `flask_app/routes/device_routes.py`;
      APNs env knobs in `.env.example` + `OPS_RUNBOOK.md`. **Note:** async games
      already advance correctly because `progress_game` runs inline in the action
      request and stops at the next human's turn regardless of who's connected;
      moving it to `socketio.start_background_task` is a deferred responsiveness
      optimization (so the actor's request returns before AI turns resolve).
- [ ] **P5 — Frontend.** Disable action UI when `current_turn_user_id !== me`;
      render multiple human seats; async lobby badge from `/api/async-game/mine`;
      join-by-link; `@capacitor/push-notifications` → `/api/devices/register`;
      iOS `aps-environment` entitlement.

## Demo / verification script

Automated (`python3 scripts/test.py async_friends`, mock the LLM per
`tests/CLAUDE.md`):

1. **Membership + seat mapping** — create an async game (2 human + 2 AI); A joins
   seat 0, B joins seat 1 via invite code. Assert `game_members` rows and
   `players[i].seat_id.owner_id`.
2. **Turn auth** — on A's turn, B's `player_action` → rejected; A's → accepted.
   Flip the turn, assert the inverse.
3. **Background orbit** — A acts; with no socket connected, the background orbit
   runs the 2 AI seats, `save_game` is called more than once, and the game lands
   on B. Evict from the registry, `load_game`, assert identical phase/stacks/turn.
4. **Notification trigger** — mock `NotificationChannel`; assert
   `notify_turn(game_id, B)` fires exactly once when B is offline, and not when B
   is "connected" (presence stub).
5. **Resume** — cold-load via `GET /api/game-state` as B; B sees correct state
   and legal options for their seat.

Manual mobile (APNs can't run in CI):

6. On a **physical iOS device**, sign in, register the real device token from
   Capacitor's Push plugin via `/api/devices/register`. From the web client as A,
   act into B's turn with the iOS app backgrounded. Verify the "it's your turn"
   push arrives and its deep link opens to B's turn.

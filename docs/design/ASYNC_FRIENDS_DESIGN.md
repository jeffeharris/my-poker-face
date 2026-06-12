---
purpose: Architecture and feasibility for async multiplayer "poker by mail" with friends
type: design
created: 2026-06-12
last_updated: 2026-06-12
---

# Async Poker With Friends — Design & Feasibility

## Problem & vision

Today a game is strictly **one human vs N AIs**, driven by a synchronous loop
(`progress_game`) that assumes the acting human is connected on a live socket.
We want an **async mode**: friends share ONE game and play turn-by-turn over
days — "chess by mail", on phones — while still being able to play live when
everyone's online. Players get a push when it's their turn (and, later, when a
hand ends). iOS first (a Capacitor app already ships); Android and email follow.

## Feasibility: the hard part is already done

The poker engine is genuinely async-shaped, so this is mostly plumbing, not an
engine rewrite:

- **Single-step, pure progression.** `advance_state_pure`
  (`poker/poker_state_machine.py:447`) advances one step and never loops
  internally; `run_until_player_action` bounds itself. A game pauses cleanly at
  any player's turn.
- **Full DB round-trip.** `save_game` / `load_game`
  (`poker/repositories/game_repository.py:54-165`) serialize the *entire* game
  (phase, hand count, blind config, deck seed). A game is reconstructable from
  its `games` row at any time, on any device.
- **Per-step persistence already exists.** `progress_game`
  (`flask_app/handlers/game_handler.py:4414`) `save_game`s after every settled
  step, not just at hand boundaries — so "resumable mid-hand" is essentially
  free.
- **Multi-human identity already modeled.** `poker/table/seat.py` gives every
  seat a typed `HumanSeat(owner_id)` / `PersonaSeat(pid)`. Nothing in the engine
  assumes a single human; the *mapping* seat→user is what's missing at the app
  layer.
- **Accounts are production-ready.** Google OAuth + guest sessions, native JWT,
  `users` / `user_preferences` tables, guest→account migration.

The three real gaps: **multi-human membership + per-turn authorization**,
**progression decoupled from a live socket**, and a **notification layer**
(none exists today).

## Architecture

### 1. Membership model (new table; don't overload `owner_id`)

`owner_id` stays "table creator/admin" (config/delete). Membership and seat
ownership become a separate `game_members` ledger
(migration `20260612_1200_async_friends`):

```
game_members(game_id, user_id, seat_index, role['owner'|'member'],
             status['invited'|'joined'|'left'], display_name, joined_at)
```

- **Identity source of truth stays in the engine.** Each human seat is
  `Player(is_human=True, seat_id=HumanSeat(user_id))`, which serializes inside
  `games.game_state_json`. `players[seat_index].seat_id.owner_id` is the
  authoritative seat→user link; `game_members` is the human-readable index +
  invite ledger that authorization and the lobby read.
- **AI-fill seats** are ordinary `PersonaSeat` AI players. A reserved-but-
  unclaimed seat starts as an AI placeholder and is swapped to a `HumanSeat`
  between hands when a friend joins (reusing the in-place tuple swap from
  `_refill_cash_seats`, `flask_app/handlers/game_handler.py:1080`). A first-class
  "empty seat" engine concept (skipped by `advance_to_next_active_player`) is
  deferred — it adds engine surface for no PoC benefit.

### 2. Authorization (the largest migration surface)

Replace the `owner_id == user.id` checks (`flask_app/routes/game_routes.py:97`,
socket `on_join` ~2632, socket+REST `player_action` ~2661/2106) with one
`flask_app/services/membership_service.py`:

- `is_member(game_id, user_id)` — joined member or admin; **falls back to
  `owner_id == user_id`** so every existing single-human game authorizes
  unchanged (an owner is trivially a member). This is the key backward-compat
  mitigation.
- `is_users_turn(game_state, user_id)` —
  `players[current_player_idx].seat_id.owner_id == user_id`.

GET / join require `is_member`; actions require `is_member` **and**
`is_users_turn`. This is stricter and more correct than today — with N humans we
must verify it's *this* human's turn, not merely "a human's".

### 3. Progression decoupled from the socket

The novel runtime behavior: advance AI turns when *nobody* is connected, then
notify the next human. Keep the `progress_game` loop body unchanged (it already
locks non-blocking, runs AI inline, persists per step, and emits to a room — a
room with zero sockets simply drops the emit). Two changes:

- After a human acts, the action route schedules the orbit **non-blocking** via
  `socketio.start_background_task(progress_game, game_id)` for async games, so
  the actor gets an immediate response and AI turns grind in the background.
- At the human-turn break, resolve the next turn user; if they have no live
  socket (`flask_app/services/presence.py`), call `notify_turn(game_id, user_id)`.

**No separate worker / Celery / DB poller** — premature for a single-server
SQLite deploy. The per-game `threading.Lock` already serializes a game's writes;
per-step saves already make it crash-safe. See *Scaling boundary* below.

### 4. Turn state (read cache + notify trigger)

Denormalized columns on `games`: `is_async`, `current_turn_user_id`,
`turn_started_at`, `turn_deadline` (stored, **not enforced** yet),
`last_notified_turn_at`. Written by the progression layer at a human-turn break
(`GameRepository.set_turn_state(..., advance_turn_clock=True)`), never by
`save_game` (which runs on every incidental save and must not move the clock).
`game_state_json` remains the source of truth; these make the lobby badge and
notify decision a cheap indexed read.

### 5. Notification layer (APNs now, abstracted)

```
flask_app/services/notifications/
  channel.py     # NotificationChannel ABC
  apns_channel.py# token-based .p8 JWT auth
  dispatcher.py  # notify_turn(game_id, user_id): best-effort, never raises
```

- `user_devices(user_id, platform, token, ...)` keyed `(user_id, token)`; prune
  on APNs 410 Unregistered (`DeviceRepository.remove`).
- `POST /api/devices/register` (bearer auth, same path as native login).
- `notify_turn` is **idempotent per (game_id, turn_started_at)** via
  `last_notified_turn_at`, and gated on presence (skip if the target has a live
  socket). Android (FCM) and email are future `NotificationChannel` subclasses.

### 6. Live + async coexistence

Same code path, differing only by who's connected. Connected → live
`update_game_state` emits (it's just multiplayer live play; `is_users_turn`
blocks out-of-turn actions). Offline target → emit dropped, APNs fires. App open
→ existing cold-load in `GET /api/game-state/<id>` rebuilds from the DB row, then
`join_game` for live updates. The existing `state_version` stamp guards stale
reconnect frames.

## Scaling boundary (explicitly out of scope)

The in-process per-game lock + per-step save is correct **on a single server**.
Going multi-server would require: (a) replacing the lock with a DB advisory lock
or a `games.advancing_token` compare-and-swap, and (b) turning the background
task into a queue consumer. Document, don't build. SQLite in WAL holds for
friend-scale concurrency; many simultaneously-advancing async games is the point
where Postgres / a write-serializing queue becomes necessary.

## Risks

| Risk | Mitigation |
|---|---|
| Auth migration breadth (`owner_id ==` in several places) | Route every check through `membership_service`; `is_member` falls back to `owner_id`; regression-test legacy single-human games. |
| Two humans acting near-simultaneously | `is_users_turn` checks the live `current_player_idx` right before `play_turn`; only one player is ever current. Hold the per-game lock around `play_turn`+save in the async path. |
| SQLite write contention | WAL + `@retry_on_lock` + per-game serialization. Multi-server is the hard boundary. |
| 2h in-memory eviction vs days-long games | Expected; cold-load rebuilds. Background task must cold-load if evicted, not early-return on `get_game()==None`. |
| APNs setup | Apple Dev .p8 key + Key ID + Team ID + bundle ID + `aps-environment` entitlement; sandbox vs prod hosts/tokens differ; **physical device required**; prune 410 tokens. |
| Notification spam / double-fire | Idempotent per `(game_id, turn_started_at)`; gated on presence. |
| Seat-claim race | Per-game lock + re-check seat open before swap. |

## Implementation phases

1. **Schema + repos** (additive, low risk) — migration, `MembershipRepository`,
   `DeviceRepository`, `GameRepository` async-meta methods. *(done)*
2. **Membership-aware auth** — `membership_service`; swap the `owner_id` checks.
3. **Async lifecycle + AI-fill** — `async_game_routes` (new/invite/join/mine),
   `build_new_game` extraction, seat-claim swap.
4. **Background progression + notifications** — `start_background_task` orbit,
   notify hook, `notifications/*`, `device_routes`, APNs env knobs.
5. **Frontend** — disable action UI off-turn, multi-human seats, async lobby
   badge, join-by-link, Capacitor push registration + iOS entitlement.

The PoC slice and demo script live in `docs/plans/ASYNC_FRIENDS_POC.md`.

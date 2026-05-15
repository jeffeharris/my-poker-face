---
purpose: Pre-main blocker — all coach routes accept game_id without verifying caller owns it
type: reference
created: 2026-05-15
last_updated: 2026-05-15
---

# All coach routes missing game-ownership check

**Severity:** T1 (must-fix before main)
**Confidence:** 100% (manually verified)
**Discovered:** pre-main review, 2026-05-15
**Files:** `flask_app/routes/coach_routes.py:51, 72, 148, 164, 183, 252, 295`

## What's exposed

Every coach route under `/api/coach/<game_id>/...` validates the `can_access_coach` permission (via `_coach_required`) but then directly fetches `game_state_service.get_game(game_id)` — **without ever confirming `game_data['owner_id'] == current_user_id`**.

Affected endpoints:
- `GET /api/coach/<game_id>/stats` (coach_stats)
- `POST /api/coach/<game_id>/ask` (coach_ask)
- `GET /api/coach/<game_id>/config` (coach_config_get)
- `POST /api/coach/<game_id>/config` (coach_config)
- `POST /api/coach/<game_id>/hand-review` (coach_hand_review)
- `GET /api/coach/<game_id>/progression` (coach_progression)
- `GET /api/coach/<game_id>/onboarding` (coach_onboarding)

## Attack scenarios

Any authenticated user with `can_access_coach` permission can:

1. **Read another player's hand history** — `coach_hand_review` returns the LLM review of the most recently completed hand, which includes both players' hole cards from the showdown.
2. **Read computed hand stats and equity** — `coach_stats` exposes pot odds, equity, opponent reads, board texture, etc.
3. **Inject coaching questions into a victim's session** — `coach_ask` makes LLM calls against the victim's game, polluting their coach_assistant memory.
4. **Override another player's coach mode** — `coach_config` POST changes the mode to `off`, ruining their coaching experience mid-session.
5. **Mix progression data** — `compute_coaching_data_with_progression` writes skill-progression updates based on game context the attacker didn't play.

## How the gap exists

The `can_access_coach` permission gates whether a user can use the coach feature *at all* (a paid-tier check). It says nothing about which game they can access. The `current_user.id` is correctly read for the *progression* writes (those go to the caller's profile), but the `game_id` is attacker-controlled and reads from the *victim's* game.

Lines 188-194 of `coach_hand_review` show the pattern:
```python
game_data = game_state_service.get_game(game_id)  # <-- victim's game
if not game_data:
    return jsonify({'error': 'Game not found'}), 404
player_name = _get_human_player_name(game_data)  # <-- victim's player name
```

No owner check between these lines and the LLM call that follows.

## Fix

Add a shared helper at the top of `coach_routes.py`:

```python
def _require_game_owner(game_id: str, game_data: dict):
    """Reject if caller doesn't own game_id (or isn't admin)."""
    user_id = _get_current_user_id()
    owner_id = (game_data or {}).get('owner_id')
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    if owner_id and user_id != owner_id and not _is_admin_user(user_id):
        return jsonify({'error': 'Permission denied'}), 403
    return None
```

Call from each handler right after the `get_game()` lookup:

```python
game_data = game_state_service.get_game(game_id)
if not game_data:
    return jsonify({'error': 'Game not found'}), 404
forbidden = _require_game_owner(game_id, game_data)
if forbidden:
    return forbidden
```

The same `_authorize_game_access` exists in `game_routes.py` — alternative is to import it (or move to `route_utils.py`).

## Test plan

1. Add `tests/test_coach_route_auth.py` mirroring `tests/test_game_route_auth.py`.
2. For each of the 7 endpoints, verify:
   - Owner gets 200/expected response.
   - Non-owner with `can_access_coach` gets 403.
   - Admin gets 200 (bypass).
   - Unauthenticated gets 401.

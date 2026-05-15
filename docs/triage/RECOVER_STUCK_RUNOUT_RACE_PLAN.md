---
purpose: Fix plan for concurrent GET race in recover_stuck_runout and unbounded run_until_player_action loop
type: reference
created: 2026-05-15
last_updated: 2026-05-15
---

# T1-33 / T2-57 — `recover_stuck_runout` race condition + iteration cap

**Severity:** T1 (data divergence on every cold restore when `run_it_out=True`)

## Concurrency Map

```
Thread A (tab reload)                    Thread B (open tab poll)
─────────────────────────────────────────────────────────────────
get_game(game_id) → None                 get_game(game_id) → None
load_game() → base_state_machine_A       load_game() → base_state_machine_B
build current_game_data_A                build current_game_data_B
                             ↕  RACE WINDOW OPENS HERE
recover_stuck_runout(sm_A)               recover_stuck_runout(sm_B)
  mutates sm_A._state_machine              mutates sm_B._state_machine
  calls run_until_player_action()          calls run_until_player_action()
  ← non-deterministic settle point →      ← may diverge from A →
save_game(sm_A) → DB write               save_game(sm_B) → DB write (overwrites A)
set_game(game_id, data_A) → mem          set_game(game_id, data_B) → mem (drops A's
                                           ai_controllers, memory_manager, etc.)
                             ↕  RACE WINDOW CLOSES
progress_game() → operates on            progress_game() → operates on data_B
  stale data_A ref (if any)                (B's objects now in mem)
```

**Trigger paths** that can produce two concurrent GETs for the same game:

1. Browser tab reload overlapping background poll interval (React polls every few seconds)
2. React Strict Mode double-invocation of `useEffect` in dev (fires two GETs before first response)
3. Socket.IO reconnect storm — client reconnects and re-fetches state while the disconnect handler already triggered a fetch
4. Two browser tabs open to the same game_id

The game cache (`game_state_service.games`) is a plain `dict` with no atomic compare-and-swap. `set_game` (game_state_service.py:92-100) is a bare dict assignment — not locked.

## Precise race window

Window opens at `game_routes.py:464` (`game_repo.load_game`) and closes at `game_routes.py:578` (`set_game`). Any two threads that both observe `get_game() → None` before either calls `set_game` will both load from DB, both mutate separate in-memory state machines, and the later `set_game` call silently replaces the earlier one — discarding the live `ai_controllers` and `memory_manager` trees built by the first thread.

Secondary window: after `set_game`, `progress_game` (game_routes.py:588-591) is called. If two threads both call `progress_game`, the second is dropped by the `blocking=False` guard (game_handler.py:1431-1433) — this is safe. The dangerous divergence is the DB/memory split before `set_game`.

## Recommended fix — Lock + Re-check

Acquire `get_game_lock(game_id)` around the entire DB load block, and re-check `get_game(game_id)` after acquiring the lock. The second thread finds the game already in memory and skips loading entirely. Release the lock before calling `progress_game` (required because `progress_game` uses `blocking=False` on the same lock — holding it makes the call a no-op).

**Rationale over alternatives:**
- Idempotency-only — `recover_stuck_runout` would still run twice on two separate objects, and `set_game` still clobbers. Not sufficient.
- Locking only around `set_game` — too late; both threads have already run recovery and saved to DB.
- Full lock around load+recover+save+set_game with re-check — eliminates the window entirely with minimal added latency (lock held < 1s on warm DB reads; cold restores are already slow).

## Code sketch

In `flask_app/routes/game_routes.py`, replace lines 457-591:

```python
if not current_game_data:
    _lock = game_state_service.get_game_lock(game_id)
    _should_advance = False
    _advance_reason = ""
    with _lock:
        # Re-check: a concurrent thread may have loaded while we waited
        current_game_data = game_state_service.get_game(game_id)
        if not current_game_data:
            try:
                owner_info = game_repo.get_game_owner_info(game_id) or {}
                owner_id = owner_info.get('owner_id')
                owner_name = owner_info.get('owner_name')

                base_state_machine = game_repo.load_game(game_id)
                if base_state_machine:
                    state_machine = StateMachineAdapter(base_state_machine)
                    # ... (all existing restore logic unchanged) ...

                    if recover_stuck_runout(state_machine):
                        game_repo.save_game(game_id, state_machine._state_machine,
                                            owner_id, owner_name)

                    game_state_service.set_game(game_id, current_game_data)

                    game_state = state_machine.game_state
                    current_player = game_state.current_player
                    logger.debug(f"[LOAD] Game {game_id} loaded ...")

                    if not game_state.awaiting_action:
                        _should_advance = True
                        _advance_reason = "not awaiting action"
                    elif game_state.awaiting_action and not current_player.is_human:
                        _should_advance = True
                        _advance_reason = f"AI turn: {current_player.name}"
                else:
                    return jsonify({'error': 'Game not found'}), 404
            except Exception as e:
                logger.error(f"[LOAD] Error loading game {game_id}: {str(e)}", exc_info=True)
                return jsonify({'error': 'Failed to load game'}), 500
    # Lock released — progress_game can now acquire it
    if _should_advance:
        logger.debug(f"[LOAD] Advancing game {game_id}: {_advance_reason}")
        progress_game(game_id)
```

No change needed in `recover_stuck_runout` itself — the lock in the caller is sufficient.

## T2-57 — Iteration cap for `run_until_player_action`

`PokerStateMachine.run_until_player_action` (poker_state_machine.py:528-535) loops `while not self.awaiting_action: self.advance_state()` with no bound. `StateMachineAdapter.run_until_player_action` (game_adapter.py:82-84) delegates unconditionally. Called at game_handler.py:1415 after recovery, and at game_routes.py:1139 on new game start.

**Recommendation:** Cap at 50 iterations (5 streets × ~10 phases per street is generous headroom; the actual poker game needs at most ~15 state advances to go from INITIALIZING to first player action).

```python
def run_until_player_action(self, max_iterations: int = 50) -> 'PokerStateMachine':
    iterations = 0
    while not self.awaiting_action:
        self.advance_state()
        iterations += 1
        if iterations >= max_iterations:
            logger.error(
                f"[RUNOUT] run_until_player_action hit cap ({max_iterations}) "
                f"at phase={self.phase.name}. State may be stuck."
            )
            break
    return self
```

Same cap in `StateMachineAdapter.run_until` (game_adapter.py:73-80) which already has a `break` on `awaiting_action` but no absolute bound.

## Test plan

### Unit test — re-check pattern prevents double recovery

```python
# tests/test_recover_race.py
import threading
from unittest.mock import patch

def test_concurrent_get_state_loads_once(tmp_db):
    """Two concurrent GETs for the same game should only load from DB once."""
    load_call_count = []

    original_load = game_repo.load_game
    def counting_load(game_id):
        load_call_count.append(game_id)
        return original_load(game_id)

    with app.test_client() as client, \
         patch('flask_app.routes.game_routes.game_repo.load_game', side_effect=counting_load):
        barrier = threading.Barrier(2)
        results = []
        def fetch():
            barrier.wait()
            r = client.get(f'/api/game-state/{STUCK_GAME_ID}')
            results.append(r.status_code)

        threads = [threading.Thread(target=fetch) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()

    assert len(load_call_count) == 1, f"DB loaded {len(load_call_count)} times, want 1"
    assert all(s == 200 for s in results)
```

### Race with concurrent.futures — verify phase doesn't diverge

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def test_race_no_state_divergence(client, stuck_game_in_db):
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(client.get, f'/api/game-state/{stuck_game_in_db}')
                for _ in range(4)]
        responses = [f.result() for f in as_completed(futs)]
    phases = {r.get_json().get('phase') for r in responses if r.status_code == 200}
    assert len(phases) == 1, f"Phase diverged: {phases}"
```

### Iteration cap test

```python
def test_run_until_player_action_cap():
    sm = PokerStateMachine.__new__(PokerStateMachine)
    sm.awaiting_action = False
    sm.advance_state = MagicMock()  # never sets awaiting_action
    sm.phase = PokerPhase.PRE_FLOP
    sm.run_until_player_action(max_iterations=10)
    assert sm.advance_state.call_count == 10
```

## Risks

**Deadlock potential:** None. `get_game_lock` creates a `threading.Lock` per game_id, not a global lock. Lock acquisition order: `get_game_lock(game_id)` first (load block), then (after release) inside `progress_game`. No two locks ever held simultaneously.

**Lock acquisition order:** `game_state_service._game_locks_lock` (meta-lock at game_state_service.py:137) held briefly only inside `get_game_lock`. Never held while holding a game-level lock. No cycle possible.

**Latency:** Lock held for DB restore duration (~100-300ms). Concurrent GETs on the same game_id queue. Only taken on cold load (cache miss). Normal GET (cache hit, game_routes.py:450-455) unaffected.

**Return inside `with`:** `return jsonify({'error': 'Game not found'}), 404` inside the `with` block releases the lock cleanly. No leak.

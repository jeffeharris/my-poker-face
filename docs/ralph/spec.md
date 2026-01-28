# Tier 1 Task Specifications

> Every task below has been reviewed through bidirectional planning.
> Design decisions are final. Ralph should execute, not redesign.

---

## T1-01: Delete `poker_action.py` (dead code)

**Type**: Delete dead code
**File**: `poker/poker_action.py`
**Merged with**: T2-08

### Problem
The entire `poker_action.py` file is dead code. The `PokerAction` class is never imported anywhere. The `from_dict` method has a copy-paste bug (passes `player_action` as both player name and action) which proves it has never been called.

### Action
1. Grep the entire codebase for `poker_action` and `PokerAction` imports
2. If zero imports found, delete the file
3. Run existing tests to confirm nothing breaks

### Acceptance Criteria
- [ ] Grep confirms zero imports of `poker_action` or `PokerAction`
- [ ] File `poker/poker_action.py` is deleted
- [ ] All existing tests pass

---

## T1-02: Hand evaluator sort bug (RE-VERIFY)

**Type**: Re-verify finding
**File**: `poker/hand_evaluator.py`

### Problem (claimed)
TRIAGE says line 871-873 has a `sorted()` call on already-sorted values that breaks element-by-element comparison, causing wrong winner determination. However, the file is only 177 lines. The line reference is wrong and the finding may be hallucinated.

### Action
1. Read `poker/hand_evaluator.py` end-to-end
2. Search for any `sorted()` call that operates on values that are already sorted, or where sorting would break intended comparison order
3. Check all return values from `_check_*` methods — are `hand_values` and `kicker_values` consistently structured for comparison?
4. If a real bug is found, fix it and write a test with specific hands that trigger wrong winner
5. If no bug exists, dismiss with detailed explanation

### Acceptance Criteria
- [ ] Full file read and analyzed
- [ ] Finding confirmed or dismissed with evidence
- [ ] If confirmed: fix applied + test showing correct winner determination
- [ ] If dismissed: spec file documents why the finding is false

---

## T1-03: Two-pair kicker calculation (RE-VERIFY)

**Type**: Re-verify finding
**File**: `poker/hand_evaluator.py:161-168`

### Problem (claimed)
TRIAGE says two-pair returns "single kicker value instead of proper list." The code at line 165-166:
```python
kicker = sorted([card for card in self.ranks if card not in pairs], reverse=True)[0]
kickers = [kicker]
```
Returns `[kicker]` — a list with one element. For two-pair in a 5-card hand (pair, pair, kicker), one kicker IS correct.

### Action
1. Verify how hand comparison works — does the comparison logic correctly handle two-pair vs two-pair where the kicker decides the winner?
2. Write a test: two players with same two pairs (e.g., Kings and Tens) but different kickers
3. If comparison works correctly, dismiss the finding
4. If comparison fails on kicker tiebreak, fix the comparison logic

### Acceptance Criteria
- [ ] Test exists: same two-pair, different kickers, correct winner determined
- [ ] Finding confirmed or dismissed with test evidence

---

## T1-04: `_check_two_pair` uses `count >= 2` (RE-VERIFY then fix)

**Type**: Re-verify + fix
**File**: `poker/hand_evaluator.py:162`

### Problem
Line 162: `pairs = [rank for rank, count in self.rank_counts.items() if count >= 2]`
Uses `>= 2` which matches counts of 3 (trips) and 4 (quads). The evaluation order (quads -> full house -> flush -> straight -> trips -> two-pair) means trips SHOULD be caught before reaching `_check_two_pair`. However, `== 2` is more correct and defensive.

### Action
1. Verify the evaluation order in the main evaluate method — confirm quads/full-house/trips are checked before two-pair
2. Change `count >= 2` to `count == 2` at line 162
3. Also check `_check_one_pair` at line 171 for the same issue: `if count >= 2` should be `== 2`
4. Write a test to ensure two-pair detection doesn't false-match trips

### Acceptance Criteria
- [ ] `_check_two_pair` uses `count == 2`
- [ ] `_check_one_pair` uses `count == 2`
- [ ] Test: hand with trips is NOT detected as two-pair
- [ ] All existing hand evaluator tests still pass

---

## T1-05: Raise validation bypass (RE-VERIFY)

**Type**: Re-verify finding
**Files**: `poker/poker_game.py:486-510`, `flask_app/routes/game_routes.py:979-1024`, `flask_app/routes/game_routes.py:1215-1256`

### Problem (claimed)
TRIAGE says `validate_and_sanitize()` is called but sanitized amount is not enforced. Looking at the code, `sanitized_amount` IS used for subsequent calculations (lines 496, 500-501). The real gap may be that the HTTP/socket handlers call `play_turn()` directly with raw user input without going through `BettingContext.validate_and_sanitize()`.

### Action
1. Trace the HTTP handler path: `api_player_action` -> `play_turn()` -> `player_raise()` — does the raw `amount` from the request body reach `player_raise` without validation?
2. Trace the socket handler path: `handle_player_action` -> `play_turn()` -> same question
3. Check: does `player_raise()` call `validate_and_sanitize()` internally? (It does at line 490)
4. If validation happens in `player_raise()`, the finding is about the handlers not validating early enough (overlaps with T1-17)
5. Document findings

### Acceptance Criteria
- [ ] Both code paths traced and documented
- [ ] If validation gap found: fix applied (may overlap with T1-17)
- [ ] If finding is false positive: dismissed with trace evidence

---

## T1-06: Remove pot x 2 raise cap

**Type**: Fix
**Files**: `poker/controllers.py:728`, `flask_app/handlers/game_handler.py:1205`

### Problem
Two locations cap max raise at `pot * 2`, which is not standard No-Limit Hold'em rules:
1. `controllers.py:728`: `max_raise = min(player_stack, max_opponent_stack, game_state.pot['total'] * 2)` — used in AI decision context
2. `game_handler.py:1205`: `max_raise = min(current_player.stack, state_machine.game_state.pot.get('total', 0) * 2)` — used in AI error fallback

The core enforcement (`BettingContext.max_raise_to = player_current_bet + player_stack`) is correct. These pot*2 caps only affect AI correction prompts and fallback random actions.

### Action
1. In `controllers.py:728`: Change to `max_raise = min(player_stack, max_opponent_stack)`. Remove `pot * 2` term.
2. In `game_handler.py:1205`: Change to `max_raise = current_player.stack`. Remove `pot * 2` term.
3. Write a test that verifies the AI controller calculates correct max_raise when pot is small but stacks are large

### Acceptance Criteria
- [ ] No `pot * 2` or `pot['total'] * 2` in raise cap calculations
- [ ] Test: pot=100, stack=5000 -> max_raise is NOT capped at 200
- [ ] Existing tests pass

---

## T1-07: `player_idx=0` falsy bug

**Type**: Fix
**File**: `poker/poker_game.py:394`

### Problem
Line 394: `player_idx = player_idx or game_state.current_player_idx`
Python falsy evaluation: `0 or X` evaluates to `X`. When `player_idx=0` (first player), this incorrectly defaults to `current_player_idx` instead of using index 0.

### Action
1. Change line 394 to:
```python
if player_idx is None:
    player_idx = game_state.current_player_idx
```
2. Write a test that calls `place_bet` with `player_idx=0` when `current_player_idx` is different (e.g., 2)
3. Verify player at index 0 is the one who gets the bet applied

### Acceptance Criteria
- [ ] `place_bet(game_state, amount, player_idx=0)` uses player at index 0
- [ ] No `or` pattern for optional index parameters
- [ ] Test explicitly passes `player_idx=0` with a different `current_player_idx`

---

## T1-08: `get_next_active_player_idx` returns inactive player (INVESTIGATE)

**Type**: Investigate + fix
**File**: `poker/poker_game.py:618-645`

### Problem
When no active player is found, the function returns `starting_idx` — which may be an inactive player. This could cause:
- Betting rounds starting with inactive players
- Infinite loops advancing to the same player
- Eliminated players set as dealer

### Action
1. Trace all callers:
   - `set_betting_round_start_player` (line 562, 565)
   - `advance_to_next_active_player` (line 651)
   - Dealer assignment (line 728)
2. For each caller, determine: can the game state reach a point where there are zero active players when this function is called?
3. Check: does the state machine prevent reaching these callers when all players are inactive?
4. Regardless of reachability, change the fallback to raise `ValueError("No active players found")` — fail loudly if assumptions are wrong
5. Write test: game state with all players folded/eliminated -> verify ValueError

### Acceptance Criteria
- [ ] All callers documented with reachability analysis
- [ ] Function raises ValueError when no active players found
- [ ] Test verifies ValueError is raised
- [ ] Existing tests still pass (confirming the state is normally unreachable)

---

## T1-09: Missing `max_winnable` data (RE-VERIFY)

**Type**: Re-verify finding
**File**: `poker/controllers.py:800-818`

### Problem (claimed)
TRIAGE says `all_players_bets` isn't available in scope. Looking at line 808:
```python
all_players_bets = [(p.bet, p.is_folded) for p in game_state.players]
```
`game_state` IS in scope (captured by the closure from line 781). This looks like a false positive.

### Action
1. Read the full enricher callback (`make_enricher` at line 791 and the `enrich_capture` inner function)
2. Verify `game_state` is in the closure scope
3. Verify `calculate_max_winnable` receives correct parameters
4. If false positive, dismiss with explanation
5. If there's a subtle scoping issue (e.g., stale closure), fix it

### Acceptance Criteria
- [ ] Enricher callback code path fully traced
- [ ] Finding confirmed or dismissed with evidence

---

## T1-10: Name collision in player comparison

**Type**: Fix
**File**: `poker/poker_game.py:428-437`

### Problem
`reset_player_action_flags` compares players by `.name` (line 434) and uses `.index(player)` (line 435). If two players share a name, `index()` returns the first match, and name comparison would reset the wrong players.

Current code:
```python
for player in game_state.players:
    if player.name != game_state.current_player.name or not exclude_current_player:
        game_state = game_state.update_player(player_idx=game_state.players.index(player),
                                              has_acted=False)
```

### Action
1. Refactor to use `enumerate()` for index-safe iteration:
```python
for idx, player in enumerate(game_state.players):
    if idx != game_state.current_player_idx or not exclude_current_player:
        game_state = game_state.update_player(player_idx=idx, has_acted=False)
```
2. Add name uniqueness validation in `initialize_game_state` — raise ValueError if duplicate names
3. Write test: create game, call `reset_player_action_flags`, verify correct players reset by index

### Acceptance Criteria
- [ ] `reset_player_action_flags` uses `enumerate()` + index comparison
- [ ] `initialize_game_state` rejects duplicate player names
- [ ] Test verifies correct flag reset by index
- [ ] Test verifies duplicate name rejection

---

## T1-11: Add React error boundaries

**Type**: New component
**Files**: Create `react/react/src/components/ErrorBoundary.tsx`, modify `react/react/src/App.tsx`

### Problem
No error boundary components exist. A single component error crashes the entire app to a white screen.

### Action
1. Create `ErrorBoundary.tsx` — a class component (required for error boundaries in React):
   - Catches render errors in child tree
   - Fallback UI: "Something went wrong" message + "Reload" button
   - Accepts optional `fallbackAction` prop for custom recovery (e.g., "Return to Menu")
   - Logs error to console
2. Wrap in `App.tsx`:
   - Top-level `<ErrorBoundary>` around `<Routes>` — catches everything
   - Per-route `<ErrorBoundary>` around game page with "Return to Menu" action
3. Write test: component that throws -> verify error boundary shows fallback

### Acceptance Criteria
- [ ] `ErrorBoundary.tsx` created with class component pattern
- [ ] Top-level boundary wraps `<Routes>` in App.tsx
- [ ] Game route has its own boundary with "Return to Menu" option
- [ ] Test: throwing component shows fallback, not white screen

---

## T1-12: Socket memory leak (RE-VERIFY)

**Type**: Re-verify finding
**File**: `react/react/src/hooks/useSocket.ts:19-24`

### Problem (claimed)
TRIAGE says `onConnect`/`onDisconnect` listeners are added but never removed. Looking at the code:
- Line 19-25: listeners added via `socket.on('connect', ...)` and `socket.on('disconnect', ...)`
- Line 27-29: cleanup calls `socket.disconnect()`
- `socket.disconnect()` in socket.io-client destroys the socket and removes all listeners

### Action
1. Verify socket.io-client behavior: does `disconnect()` remove all event listeners?
2. Check if the socket is reused after disconnect (it shouldn't be since `socketRef.current` is set to a new socket on each effect run)
3. If `disconnect()` cleans up everything, dismiss as false positive
4. If listeners could accumulate (e.g., effect re-runs without cleanup), add explicit `socket.off()` calls before disconnect

### Acceptance Criteria
- [ ] Socket.io-client disconnect behavior documented
- [ ] Finding confirmed or dismissed
- [ ] If confirmed: explicit cleanup added

---

## T1-13: Missing loading/error states in handleQuickPlay

**Type**: Fix
**File**: `react/react/src/App.tsx:136-174`

### Problem
`handleQuickPlay` catches errors but only logs to console. User clicks "Quick Play", it fails, nothing visible happens. Same for `handleStartCustomGame`.

### Action
1. Import toast from `react-hot-toast` (installed by T1-15)
2. In catch blocks, show toast: `toast.error('Failed to create game. Please try again.')`
3. In non-ok response handling, show specific error from response body if available
4. Verify loading state (`isCreatingGame`) is correctly set in all paths

**Depends on**: T1-15 (toast system must be installed first)

### Acceptance Criteria
- [ ] `handleQuickPlay` shows toast on API error
- [ ] `handleStartCustomGame` shows toast on API error
- [ ] Non-ok responses show error message from response body
- [ ] Loading state correctly managed in all paths

---

## T1-14: No offline detection

**Type**: New feature
**File**: Create hook or add to `react/react/src/App.tsx`

### Problem
No `navigator.onLine` check or `online`/`offline` event listeners. Network drop results in a frozen UI with no feedback.

### Action
1. Create a `useOnlineStatus` hook (or inline in App.tsx):
   - Track `navigator.onLine` state
   - Listen for `online` and `offline` window events
   - On offline: show persistent toast (`toast.error('Connection lost', { duration: Infinity })`)
   - On online: dismiss toast, optionally show "Back online" success toast
2. Add to App.tsx at the root level

**Depends on**: T1-15 (toast system)

### Acceptance Criteria
- [ ] Offline state detected and displayed to user
- [ ] Online recovery dismisses the banner
- [ ] Hook or component cleans up event listeners on unmount

---

## T1-15: Silent API failures — install toast system

**Type**: New dependency + component
**Files**: `react/react/package.json`, `react/react/src/App.tsx`

### Problem
No notification/toast system exists. API errors are logged to console only.

### Action
1. Install `react-hot-toast`: add to `package.json` dependencies
2. Add `<Toaster />` component in `App.tsx` at the root level, outside of Routes
3. Configure with reasonable defaults (position: top-right, duration: 4000ms)
4. Document usage pattern: `import toast from 'react-hot-toast'; toast.error('message')`

**Note**: This is a prerequisite for T1-13 and T1-14. Do this first.

### Acceptance Criteria
- [ ] `react-hot-toast` in package.json dependencies
- [ ] `<Toaster />` rendered in App.tsx
- [ ] Toast can be triggered from any component via `toast()` function
- [ ] Test: import and call toast, verify it renders

---

## T1-16: Predictable game IDs

**Type**: Fix
**File**: `flask_app/routes/game_routes.py:189-191`

### Problem
```python
def generate_game_id() -> str:
    return str(int(time.time() * 1000))
```
Game IDs are based on `time.time()` — easily guessable. An attacker can predict game IDs by knowing the approximate creation time.

### Action
1. Replace with:
```python
import secrets

def generate_game_id() -> str:
    return secrets.token_urlsafe(16)
```
2. Returns a 22-character URL-safe random string
3. Verify no code assumes game IDs are numeric (check parseInt, Number(), int() calls on game_id)
4. Write test: generate 100 IDs, verify uniqueness and no time-based pattern

### Acceptance Criteria
- [ ] `generate_game_id` uses `secrets.token_urlsafe(16)`
- [ ] No code assumes numeric game IDs
- [ ] Test: 100 IDs are unique and non-sequential
- [ ] Existing tests pass (game IDs may be hardcoded in fixtures — check)

---

## T1-17: No input validation on actions

**Type**: Fix
**Files**: `flask_app/routes/game_routes.py` (lines 979-1024 and 1215-1256)

### Problem
Both HTTP and socket handlers pass raw user input to `play_turn()`. The `play_turn` function uses `getattr()` with the action string to find a function — invalid input causes AttributeError crashes.

### Action
1. Create shared validation function (in `flask_app/validation.py` or inline):
```python
def validate_player_action(game_state, action, amount):
    """Returns (is_valid, error_message)."""
    if not game_state.current_player.is_human:
        return False, "Not human player's turn"
    if action not in game_state.current_player_options:
        return False, f"Invalid action: {action}"
    if action == 'raise' and (not isinstance(amount, (int, float)) or amount < 0):
        return False, "Invalid raise amount"
    return True, ""
```
2. Apply to HTTP handler (`api_player_action`) before `play_turn()`
3. Apply to socket handler (`handle_player_action`) before `play_turn()`
4. Return 400 error (HTTP) or emit error event (socket) for invalid input
5. Write test: submit invalid action, negative amount, action when AI's turn

### Acceptance Criteria
- [ ] Shared validation function exists
- [ ] HTTP handler validates before `play_turn()`
- [ ] Socket handler validates before `play_turn()`
- [ ] Test: invalid action string returns error
- [ ] Test: negative raise amount returns error
- [ ] Test: action during AI turn returns error

---

## T1-18: Unprotected WebSocket handlers

**Type**: Fix
**File**: `flask_app/routes/game_routes.py:1198-1286`

### Problem
Socket event handlers `on_join` and `handle_player_action` have no authentication checks. Anyone who knows a game ID can join the room and take actions.

### Action
1. In `on_join`:
```python
user = auth_manager.get_current_user()
game_data = game_state_service.get_game(game_id_str)
if not game_data:
    return
owner_id = game_data.get('owner_id')
if user and user.get('id') != owner_id:
    return  # Not your game
```
2. In `handle_player_action`:
```python
user = auth_manager.get_current_user()
game_data = game_state_service.get_game(game_id)
if not game_data:
    return
owner_id = game_data.get('owner_id')
if not user or user.get('id') != owner_id:
    return  # Not your game
current_player = game_data['state_machine'].game_state.current_player
if not current_player.is_human:
    return  # Not a human's turn
```
3. Owner check + is_human is sufficient. No name matching needed (one human per game).
4. `auth_manager` is already available in the module — imported for HTTP routes.
5. Write test: attempt action from non-owner session, verify rejection.

### Acceptance Criteria
- [ ] `on_join` checks game ownership
- [ ] `handle_player_action` checks ownership + is_human turn
- [ ] Non-owner socket events are rejected silently
- [ ] Test: non-owner action attempt is rejected
- [ ] Existing game flow still works for owner

---

## T1-19: SECRET_KEY regenerated on restart

**Type**: Fix
**File**: `flask_app/config.py:18`

### Problem
```python
SECRET_KEY = os.environ.get('SECRET_KEY', os.urandom(32).hex())
```
If `SECRET_KEY` is not set in environment, a random one is generated on every restart, invalidating all existing sessions.

### Action
1. Replace with:
```python
if is_development:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-not-for-production')
else:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        raise RuntimeError(
            "SECRET_KEY environment variable is required in production. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
```
2. Write test: verify dev mode uses stable default, prod mode raises without env var

### Acceptance Criteria
- [ ] Dev mode: stable default SECRET_KEY
- [ ] Prod mode: raises RuntimeError if SECRET_KEY not set
- [ ] Error message includes generation command
- [ ] Test covers both modes

---

## T1-20: SQL injection in admin (RE-VERIFY)

**Type**: Re-verify finding
**File**: `flask_app/routes/admin_dashboard_routes.py:1715-1739`

### Problem (claimed)
Table names used via f-string interpolation at line 1724:
```python
cursor = conn.execute(f'SELECT COUNT(*) as cnt FROM "{table}"')
```
However, there IS a whitelist check at line 1719:
```python
if table not in allowed_tables:
    continue
```

### Action
1. Read the full function to verify the whitelist is applied before the f-string
2. Verify `allowed_tables` is a hardcoded list (not user-controlled)
3. Check if there's any code path that could bypass the whitelist check
4. If the guard is robust, dismiss as false positive
5. If there's a bypass, add parameterized query (note: table names can't be parameterized in SQLite, so whitelist is the correct approach)

### Acceptance Criteria
- [ ] Whitelist guard verified as robust
- [ ] No bypass path exists
- [ ] Finding confirmed or dismissed with evidence

---

# Tier 2 Task Specifications (Mechanical Fixes)

> Selected Tier 2 items that are well-scoped, mechanical, and safe for autonomous execution.
> Design decisions are final. Ralph should execute, not redesign.

---

## T2-14: Shuffle mutates module-level list

**Type**: Fix
**File**: `poker/utils.py:83-87`

### Problem
`get_celebrities()` mutates the module-level `CELEBRITIES_LIST` constant in place:
```python
def get_celebrities(shuffled: bool = False):
    """Retrieve the list of celebrities."""
    celebrities_list = CELEBRITIES_LIST      # reference, NOT a copy
    random.shuffle(celebrities_list) if shuffled else None
    return celebrities_list
```
`celebrities_list = CELEBRITIES_LIST` creates a reference to the same list object. `random.shuffle()` mutates in place, permanently altering the module-level constant. Subsequent calls without `shuffled=True` return the shuffled order. This breaks experiment reproducibility and violates the codebase's functional programming patterns.

### Callers affected
- `flask_app/routes/game_routes.py:866` — game creation (shuffled=True)
- `flask_app/routes/personality_routes.py:363, 489` — theme generation (no shuffle, gets mutated list)
- `experiments/run_ai_tournament.py:563` — tournament init (no shuffle, gets mutated list)
- `tests/test_persistence.py:104` — tests (shuffled=True)

### Action
1. Replace the function body with:
```python
def get_celebrities(shuffled: bool = False):
    """Retrieve the list of celebrities."""
    if shuffled:
        return random.sample(CELEBRITIES_LIST, len(CELEBRITIES_LIST))
    return list(CELEBRITIES_LIST)
```
2. `random.sample()` returns a new list without mutating the original
3. `list()` returns a defensive copy for the non-shuffled case
4. Write test: call `get_celebrities(shuffled=True)` twice, verify `CELEBRITIES_LIST` is not mutated between calls

### Acceptance Criteria
- [ ] `CELEBRITIES_LIST` is never mutated by `get_celebrities()`
- [ ] Shuffled call returns all celebrities in random order
- [ ] Non-shuffled call returns original order
- [ ] Test: call shuffled, then verify module-level list unchanged
- [ ] Existing tests pass

---

## T2-15: Delete dead `setup_helper.py`

**Type**: Delete dead code
**File**: `setup_helper.py`

### Problem
`setup_helper.py` is a legacy script from the pre-Docker console-game era. It references `python working_game.py` (line 123) which no longer exists. The file is never imported or referenced anywhere in active code. The project now uses Docker Compose (`make up` / `docker compose up`).

### Verification
- `working_game.py` does not exist in the repo
- Zero imports of `setup_helper` anywhere
- Not referenced in Makefile, docker-compose, or any scripts
- Current setup instructions in README.md and CLAUDE.md use Docker

### Action
1. Grep the codebase for `setup_helper` imports — confirm zero
2. Delete `setup_helper.py`
3. Run existing tests to confirm nothing breaks

### Acceptance Criteria
- [ ] Grep confirms zero imports of `setup_helper`
- [ ] File `setup_helper.py` is deleted
- [ ] All existing tests pass

---

## T2-09: O(n²) player flag reset

**Type**: Refactor
**File**: `poker/poker_game.py` — `reset_player_action_flags` function

### Problem
After Ralph's T1-10 fix, the function uses `enumerate()` correctly but still calls `update_player()` in a loop. Each call creates a new tuple of all players + a new `PokerGameState`, resulting in O(n) intermediate objects. The codebase documents single-pass tuple comprehensions as the preferred pattern (see CLAUDE.md "Immutable Updates" section).

Current code (post T1-10):
```python
for idx, player in enumerate(game_state.players):
    if idx != game_state.current_player_idx or not exclude_current_player:
        game_state = game_state.update_player(player_idx=idx, has_acted=False)
return game_state
```

### Action
1. Replace with single-pass tuple comprehension:
```python
def reset_player_action_flags(game_state: PokerGameState, exclude_current_player: bool = False):
    """
    Sets all player action flags to False. Current player can be excluded from this action when they are betting and
    just other players should be reset.
    """
    updated_players = tuple(
        player if (exclude_current_player and idx == game_state.current_player_idx)
        else player.update(has_acted=False)
        for idx, player in enumerate(game_state.players)
    )
    return game_state.update(players=updated_players)
```
2. Run existing T1-10 tests (`test_reset_flags_uses_index_not_name`, `test_reset_flags_resets_all_when_not_excluding`) to verify identical behavior
3. No new tests needed — existing tests cover the behavior. Optionally add a test verifying the function returns a single new GameState (not n intermediate ones).

### Acceptance Criteria
- [ ] Function uses single tuple comprehension (no loop with `update_player`)
- [ ] Existing T1-10 tests pass unchanged
- [ ] All existing tests pass

---

## T2-17: HTTP client never closed

**Type**: Fix
**File**: `core/llm/providers/http_client.py`

### Problem
Module-level `httpx.Client()` singleton has no shutdown hook. The client maintains up to 20 keepalive connections that are never explicitly closed. While the OS reclaims sockets on process exit, proper cleanup prevents resource warnings and is necessary if worker count increases.

Current code:
```python
shared_http_client = httpx.Client(
    limits=httpx.Limits(
        max_connections=100,
        max_keepalive_connections=20,
        keepalive_expiry=300.0,
    ),
    timeout=httpx.Timeout(connect=10.0, read=600.0, write=600.0, pool=600.0),
)
```

Used by 7 LLM providers: openai, anthropic, groq, deepseek, mistral, xai, google.

### Action
1. Add `atexit` cleanup to `core/llm/providers/http_client.py`:
```python
import atexit

def _cleanup_http_client():
    """Close shared HTTP client on process exit."""
    try:
        shared_http_client.close()
    except Exception:
        pass

atexit.register(_cleanup_http_client)
```
2. Write test: verify `atexit` handler is registered (check `atexit._run_exitfuncs` or mock `atexit.register` on import)

### Acceptance Criteria
- [ ] `atexit.register` called for `shared_http_client.close`
- [ ] Cleanup function handles exceptions gracefully (no crash on double-close)
- [ ] Test verifies cleanup is registered
- [ ] All existing tests pass

---

## T2-18: UsageTracker singleton not thread-safe

**Type**: Fix
**File**: `core/llm/tracking.py` — `UsageTracker.get_default()` classmethod

### Problem
The singleton pattern has a race condition:
```python
@classmethod
def get_default(cls) -> "UsageTracker":
    """Get or create the default singleton tracker."""
    if cls._instance is None:      # Thread A checks
        cls._instance = cls()       # Thread B also checks before A sets
    return cls._instance
```
Multiple threads (e.g., `ThreadPoolExecutor` in `experiments/run_ai_tournament.py`) can create separate instances. Impact: redundant pricing caches and stale cache after invalidation (admin updates pricing, only one instance gets invalidated).

### Action
1. Add double-checked locking:
```python
import threading

class UsageTracker:
    _instance: Optional["UsageTracker"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_default(cls) -> "UsageTracker":
        """Get or create the default singleton tracker (thread-safe)."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
```
2. Add `_instance_lock = threading.Lock()` as a class variable
3. Write test: spawn 10 threads calling `get_default()` concurrently, verify all return the same instance (same `id()`)

### Acceptance Criteria
- [ ] `get_default()` uses double-checked locking with `threading.Lock`
- [ ] `_instance_lock` is a class-level `threading.Lock()`
- [ ] Test: 10 concurrent threads all get same instance
- [ ] All existing tests pass

---

## T2-05: DB connection created per config lookup

**Type**: Fix
**Files**: `flask_app/config.py:52-101`, `poker/character_images.py:42-59`

### Problem
Four config getter functions in `flask_app/config.py` and two in `poker/character_images.py` each instantiate a new `GamePersistence()` on every call. `GamePersistence.__init__` opens **3 DB connections** (WAL mode, init schema, check migrations) before the actual query opens a 4th. These functions are called during game creation and AI image generation.

Affected functions:
- `flask_app/config.py`: `get_default_provider()`, `get_default_model()`, `get_assistant_provider()`, `get_assistant_model()`
- `poker/character_images.py`: `get_image_provider()`, `get_image_model()`

### Action
1. Add a cached persistence getter in `flask_app/config.py`:
```python
from functools import lru_cache

@lru_cache(maxsize=1)
def _get_config_persistence():
    """Get a shared persistence instance for config lookups."""
    from poker.persistence import GamePersistence
    return GamePersistence()
```
2. Replace `GamePersistence()` in all 4 config getters with `_get_config_persistence()`:
```python
def get_default_provider() -> str:
    p = _get_config_persistence()
    db_value = p.get_setting('DEFAULT_PROVIDER', '')
    if db_value:
        return db_value
    return 'openai'
```
3. Apply the same pattern to `poker/character_images.py` — add a local `@lru_cache` getter and replace both functions.
4. Write test: call `get_default_provider()` twice, verify `GamePersistence` was only constructed once (mock `GamePersistence.__init__` and check call count).

### Acceptance Criteria
- [ ] `flask_app/config.py` config getters share a single cached `GamePersistence` instance
- [ ] `poker/character_images.py` getters share a single cached `GamePersistence` instance
- [ ] Test: two calls to a config getter only construct `GamePersistence` once
- [ ] All existing tests pass

---

## T2-12: Remove debug console.log statements from production

**Type**: Fix + new utility
**Files**: `react/react/src/utils/logger.ts` (new), 38 React source files

### Important Note
Earlier Tier 1 tasks (T1-11, T1-13, T1-14, T1-15) added new React files and modified `App.tsx`. **Do NOT rely on the line numbers or exact counts below** — they were measured before those changes. Instead, do a fresh `grep -rn "console\." react/react/src/ --include="*.ts" --include="*.tsx"` to get current counts and locations before starting.

### Problem
120+ console statements across 38+ React files: ~48 `console.log` (debug noise), ~62 `console.error` (legitimate error handling), ~8 `console.warn`, ~2 `console.debug`. Debug logs fire on every WebSocket event, cluttering the browser console and potentially exposing game state data. An existing `config.ENABLE_DEBUG` flag exists but is unused for logging.

### Action

**Step 1**: Create `react/react/src/utils/logger.ts`:
```typescript
import { config } from '../config';

export const logger = {
  debug(...args: unknown[]): void {
    if (config.ENABLE_DEBUG) console.debug(...args);
  },
  log(...args: unknown[]): void {
    if (config.ENABLE_DEBUG) console.log(...args);
  },
  warn(...args: unknown[]): void {
    console.warn(...args);
  },
  error(...args: unknown[]): void {
    console.error(...args);
  },
};
```

**Step 2**: Delete outright (~25-30 statements) — pure debug noise:
- WebSocket routine logs in `usePokerGame.ts` (lines 95, 100, 104, 291, etc.)
- `[ExperimentDesigner]` flow tracing (5 statements)
- `[DEBUG]` test function logs (2 statements)
- All `CSSDebugger.tsx` logs (9 statements)
- `[PWA]` event logs (2 statements)
- `GameSelector` fetch logs (2 statements)
- `PokerTable.tsx:129` human turn debug object

**Step 3**: Convert remaining `console.log` to `logger.log()` (~20 statements):
- Debug panel WebSocket updates
- Admin panel state changes
- Queued action execution logs

**Step 4**: Convert `console.error` to `logger.error()` and `console.warn` to `logger.warn()` across all files.

**Step 5**: Verify with `grep -r "console\." react/react/src/ --include="*.ts" --include="*.tsx"` — only `logger.ts` should contain raw console calls.

**Testing**: Run `npx tsc --noEmit` to verify TypeScript compiles. No runtime test needed (logger is a pass-through).

### Acceptance Criteria
- [ ] `react/react/src/utils/logger.ts` created with debug-gated logging
- [ ] Zero raw `console.log` statements remain outside `logger.ts`
- [ ] `console.error` and `console.warn` converted to `logger.error()` / `logger.warn()`
- [ ] Debug noise statements deleted (not converted)
- [ ] TypeScript compiles without errors
- [ ] Grep confirms no raw console usage outside logger

---

## T2-19: Unbounded game state memory growth

**Type**: Fix
**File**: `flask_app/services/game_state_service.py`

### Problem
The module-level `games` dict stores all active games but has no eviction mechanism. Games are removed only when the frontend calls `/api/end_game`. Abandoned games (browser closed, crash, disconnect) remain in memory indefinitely. Each game is ~200-500 KB, growing to 1-5 MB for long tournaments. The codebase already has a TTL pattern in `flask_app/routes/experiment_routes.py:1885-1910`.

### Action
1. Add TTL tracking and cleanup to `flask_app/services/game_state_service.py`:
```python
from datetime import datetime, timedelta

game_last_access: Dict[str, datetime] = {}
GAME_TTL_HOURS = 2

def _cleanup_stale_games():
    """Remove games not accessed within GAME_TTL_HOURS."""
    cutoff = datetime.now() - timedelta(hours=GAME_TTL_HOURS)
    stale_keys = [k for k, t in game_last_access.items() if t < cutoff]
    for key in stale_keys:
        games.pop(key, None)
        game_locks.pop(key, None)
        game_last_access.pop(key, None)
```
2. Update `get_game()` to call `_cleanup_stale_games()` and track access time
3. Update `set_game()` to call `_cleanup_stale_games()` and track access time
4. Update `delete_game()` to also clean up `game_locks` and `game_last_access`
5. Write test: create game, mock time to 3 hours later, call `get_game()` on a different game, verify stale game evicted. Then access evicted game again and verify lazy reload from SQLite works (this is the existing pattern at `game_routes.py:256-368`).

### Acceptance Criteria
- [ ] `game_last_access` dict tracks last access time per game
- [ ] `_cleanup_stale_games()` evicts games older than `GAME_TTL_HOURS`
- [ ] `get_game()` and `set_game()` trigger lazy cleanup
- [ ] `delete_game()` also cleans up `game_locks` and `game_last_access`
- [ ] Test: stale game evicted after TTL
- [ ] All existing tests pass

---

## T2-20: Unbounded message list growth

**Type**: Fix
**File**: `flask_app/handlers/message_handler.py`

### Problem
`send_message()` appends to `game_data['messages']` without any limit. A typical tournament generates 1,000-4,000 messages (15-25 per hand × 50-200 hands). The DB already caps at 100 messages on load (`persistence.load_messages(game_id, limit=100)`), but the in-memory list grows unbounded during active play. AI players only use the last 8 messages (`AI_MESSAGE_CONTEXT_LIMIT = 8`).

### Action
1. Add a constant and trim logic in `flask_app/handlers/message_handler.py`:
```python
MAX_MESSAGES_IN_MEMORY = 200
```
2. After the append at line 125, add trimming:
```python
game_messages.append(new_message)

# Trim to prevent unbounded growth
if len(game_messages) > MAX_MESSAGES_IN_MEMORY:
    game_messages = game_messages[-MAX_MESSAGES_IN_MEMORY:]
    game_data['messages'] = game_messages
```
3. Write test: create a game, add 250 messages via `send_message`, verify only the last 200 remain in `game_data['messages']`.

### Acceptance Criteria
- [ ] `MAX_MESSAGES_IN_MEMORY` constant defined (200)
- [ ] Message list trimmed after append when exceeding limit
- [ ] Test: 250 messages added, only last 200 retained
- [ ] All existing tests pass

---

## T2-22: Conversation memory token trim — DISMISS

**Type**: Dismissed (not a real problem)

### Investigation Summary
The concern was that trimming by message count (15) rather than token count could exceed LLM context limits. Investigation found:
- `MEMORY_TRIM_KEEP_EXCHANGES = 0` — memory is already **cleared each turn**
- Even at max 15 messages, total usage is ~8.4k tokens vs 128k+ context windows (6.6%)
- Modern models (gpt-5-nano, claude-sonnet-4-5, llama-3.1) all have 128k-200k context
- No context overflow errors in production or tests
- Extensive analysis in `tests/test_message_history_impact.py` confirms the design is intentional

**No action needed.** Mark as dismissed in implementation plan.

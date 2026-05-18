---
purpose: SUPERSEDED — original wiring plan for the CashSession parallel-orchestrator architecture; v1 was rewritten to use the tournament flow directly
type: design
created: 2026-05-18
last_updated: 2026-05-18
---

# Cash Mode v1 — Wiring Plan (SUPERSEDED)

**⚠️  This document is historical.** It describes the
`CashSession` parallel-orchestrator architecture that v1 was
initially built on. After live playtest revealed that the parallel
orchestrator kept hitting tournament-shaped integration seams
(action route, on_join, save_game, controller wiring), v1 was
rewritten in commit `b2a0ad36` to build directly on the tournament
flow. Cash games are now tournament-shape games with a
`cash_mode=True` flag on `game_data` — see
`CASH_MODE_V1_HANDOFF.md` and the route at
`flask_app/routes/cash_routes.py` for the current architecture.

Kept on disk as a record of the codex-vetted design pass and the
ten concerns that prompted the rewrite. Useful context for the
sponsorship work or any future cash-mode refactor.

---

This is the integration design for the cash-mode hand orchestration
commit. It's a proposal — open to redirection before code lands.

**Revised 2026-05-18 after codex critique.** Ten concerns flagged;
all integrated. The most consequential reversals were (1) syncing
`CashTable.stacks` directly from post-hand `Player.stack` rather than
delta-arithmetic, (2) AI bankroll = off-table only (settlement does
not touch it), (3) `sit_down_ai` is a sibling of `sit_down`, not a
synthetic-wrapper reuse, and (4) the per-hand memory lifecycle is
broader than `on_hand_complete` — see "Memory recording lifecycle"
below.

Companion docs:
- `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 2 (canonical spec)
- `docs/plans/CASH_MODE_V1_HANDOFF.md` (implementation handoff)
- Commits already landed: `613c0e9b` (schema), `8b245280` (table+seating), `de9f7479` (config_json pivot), `3fd21f03` (personality knobs), `8bfe87cb` (rename + plan)

## Constraints

1. **Reuse the existing hand engine.** `play_turn` / `advance_to_next_active_player` / `determine_winner` / `award_pot_winnings` in `poker/poker_game.py`. Don't fork the state machine.
2. **Bridge persistent `CashTable.stacks` and transient `PokerGameState.players[i].stack`.** The hand engine doesn't know about persistent stacks; cash mode is responsible for translating one to the other every hand.
3. **Bypass tournament-bust assumptions.** Three existing mechanisms terminate the game on player elimination (see "Tournament-bust bypass" below). Cash mode must short-circuit all three.
4. **Use the existing Phase 3 relationship dispatch path verbatim.** Cash mode flips `set_relationship_repo(cash_mode=True)` — that's the only new wiring; `cash_pair_stats` writes already fire from `_process_relationship_events`.

## Module layout

```
cash_mode/
├── __init__.py         # (existing) exports
├── bankroll.py         # (existing) AIBankrollState, PlayerBankrollState, project_bankroll
├── table.py            # (existing) CashTable, new_table
├── seating.py          # (existing) 8-row accounting transitions
├── session.py          # NEW — CashSession driving one table's hand loop
└── seat_filler.py      # NEW — selects eligible AI to fill open seats between hands
```

Nothing else is added or modified at the cash-mode-package level. The hand engine, state machine, memory manager — all consumed verbatim.

## CashSession lifecycle

```python
class CashSession:
    """One human + N AI at one cash table, hand loop owned here."""

    def __init__(
        self,
        table: CashTable,
        player_bankroll: PlayerBankrollState,
        bankroll_repo: BankrollRepository,
        relationship_repo: RelationshipRepository,
        personality_repo: PersonalityRepository,
        # ... controller factory / LLM config etc.
    ):
        self.table = table
        self.bankroll_repo = bankroll_repo
        self.memory_manager = AIMemoryManager(...)
        self.memory_manager.set_relationship_repo(
            relationship_repo, cash_mode=True,     # <-- THE WIRING POINT
        )
        # ...

    def run_hand(self) -> HandResult: ...
    def sit_player(self, seat_index: int, buy_in: int) -> None: ...
    def leave_player(self) -> None: ...
    def top_up_player(self, amount: int) -> None: ...
```

### Per-hand iteration

1. **Fill open seats** (`seat_filler.fill_seats(table, ...)`) — see "Fill-seats algorithm" below for the precise rule. AI seat-downs go through **`sit_down_ai`** (new sibling of `sit_down` — see "AI bankroll shim" below), which writes `last_regen_tick = now` on the resulting `AIBankrollState` and persists via `BankrollRepository.save_ai_bankroll`.
2. **Block sit/leave/topup**: `table = table.with_hand_in_progress(True)`.
3. **Build `PokerGameState`** from the table:
   - Players: one `Player` per occupied seat. `stack = table.stack_of(seat_id)`. `is_human = (seat_id == PLAYER_SEAT_ID)`. `Player.name = personality.display_name` for AI; the hand engine keys pot/player lookup by name (`poker/poker_game.py:310, 424`) and display-name collisions break pot allocation — see "Fill-seats algorithm" for the uniqueness enforcement.
   - Controllers: instantiated **once per seated stint**, not per hand (see "Controller lifecycle" below).
   - Seed-deterministic deck if the session config carries a seed (matches experiment runner's pattern).
4. **Memory: on_hand_start + record_blinds**: see "Memory recording lifecycle" — the manager needs these BEFORE the hand runs.
5. **Run hand**: `state_machine.run_until([PokerPhase.EVALUATING_HAND])` interleaved with `controller.decide_action()` and `memory_manager.on_action(...)` / `on_community_cards(...)` per the existing `run_hand` template. **Stop exactly at the `EVALUATING_HAND` phase — DO NOT call `advance_state()` past it**: the `evaluating_hand_transition` (`poker/poker_state_machine.py:291`) already calls `award_pot_winnings` internally. Double-settlement is a known bug class. Asserted by an explicit test in commit 3.
6. **Settle**: `determine_winner(game_state)` + `award_pot_winnings(game_state, winner_info)`. `PokerGameState.players[i].stack` now reflects post-hand chips. Cash mode performs this manually (the state machine stopped before its automatic settlement transition).
7. **Sync `CashTable.stacks` from post-hand `Player.stack`**: for each seat, `table = table.with_stack(seat_id, player.stack)`. **No delta arithmetic** — codex flagged that a naive `delta = post - pre` would miss chips committed during the hand (blinds, bets) because `place_bet` already debited `Player.stack` before `run_until` returned. The post-hand `Player.stack` already accounts for blinds + bets + payout, so it's the authoritative final table stack. The `apply_settlement` function from commit 2 is now used only by `mid_hand_quit` rollback and external chip-flow events, not the normal hand-settlement path.
8. **Memory: on_hand_complete (UNCHANGED dispatch path)**: `self.memory_manager.on_hand_complete(recorded_hand, equity_history=...)`. Because we called `set_relationship_repo(cash_mode=True)` at construction, `cash_pair_stats` writes fire automatically through the Phase 3 dispatch. **No new dispatch code in cash_mode/.** The detector reads `RecordedHand` (built during the hand via `on_action`), not `CashTable.stacks`, so settlement-before-dispatch is safe.
9. **Bust handling**: for each seat where `table.stack_of(seat_id) == 0`:
   - **AI**: `seating.bust_at_table(table, seat_id)` clears the seat. The AI's `AIBankrollState.chips` is **unchanged by this** — settlement updated the table stack, and the bankroll was already debited at sit-down. The personality is now ineligible for re-seating until `load_ai_bankroll_current >= min_buy_in × multiplier` (gated by `last_regen_tick` set at sit-down; passive regen starts from there).
   - **Player**: `seating.bust_at_table(table, seat_id)` clears the seat. If `player_bankroll.chips == 0` AND no other table presence, fire `seating.full_bankroll_bust(player_bankroll)` for a fresh grant.
10. **Persist**: `bankroll_repo.save_player_bankroll(...)` only if the player bankroll changed this hand (fresh grant, top-up, sit-down, leave). AI bankrolls **don't change at settlement** — they only change on sit/leave events. So this step is a single `save_player_bankroll` call (conditional) plus the AI save that already happened at sit-down in step 1.
11. **Unblock**: `table = table.with_hand_in_progress(False)`.
12. **Increment hand_number** (monotonic within the session — see "Hand-number policy" below) and return to the caller (router / human input wait / autonomous loop).

### Between hands

- **Sit**: route calls `session.sit_player(seat, buy_in)` → `seating.sit_down`.
- **Top up**: route calls `session.top_up_player(amount)` → `seating.top_up`.
- **Leave**: route calls `session.leave_player()` → `seating.leave_table`. Session continues with AI-only hands? **v1 design call: NO.** When the human leaves, the cash session terminates (no AI-vs-AI background play in v1 per spec §"v3 adds: AI-vs-AI background simulation"). Resume happens by starting a new `CashSession`.

## AI bankroll shim — resolved: sibling function

Decided: **add `seating.sit_down_ai` as a sibling of `sit_down`** (Option B from the original draft). Codex's argument: `sit_down` returns `PlayerBankrollState`, which can't carry `last_regen_tick`. A synthetic-wrapper reuse (Option A) leaves the "remember to set the timestamp after" as an implicit invariant in cash_mode/session.py rather than a structural one — and that's exactly the kind of "remember to" that becomes a real bug under maintenance pressure.

`sit_down_ai` is structurally identical to `sit_down` but:
- Takes `AIBankrollState` (not `PlayerBankrollState`).
- Returns `Tuple[CashTable, AIBankrollState]` with `last_regen_tick = now` on the new state.
- Same `hand_in_progress=True` block, same `min_buy_in`/`max_buy_in` enforcement, same occupied-seat / already-seated / insufficient-bankroll checks.
- The `chips` field semantics matches: AI `chips` is **off-table bankroll**, debited by `buy_in` at sit-down; the bankroll never moves at settlement.

The accounting invariant codex flagged (concern #5) is now structural:

> `AIBankrollState.chips` is **off-table bankroll only**. Hand
> settlement updates `CashTable.stacks`, never `AIBankrollState`.
> Only sit-down / leave / top-up move chips between bankroll and
> table stack — same as the human path.

This means `sit_down_ai` only fires at fill-seats time (commit 3) and at AI leave (which v1 only does via bust; leave-while-winning is v2's stop-win logic). No per-hand AI bankroll write — concern #5 fully resolved.

## Memory recording lifecycle

Codex flagged that the plan under-specifies the per-hand memory pipeline. The full lifecycle (which both `experiments/run_ai_tournament.py:run_hand` and `flask_app/handlers/game_handler.py:progress_game` already implement) is:

1. **Before the hand**: `memory_manager.on_hand_start(game_state, hand_number)` — `HandHistoryRecorder.start_hand` snapshots players, cards, stacks (`poker/memory/hand_history.py:300`).
2. **After blinds posted**: `memory_manager.record_blinds(...)` — records SB / BB contributions for the chip-flow allocator.
3. **Per action**: `memory_manager.on_action(player_name, action, amount, game_state)` (`poker/memory/memory_manager.py:358`) — feeds opponent models, tendencies, and the chip-flow allocator that drives BIG_WIN detection.
4. **Per street**: `memory_manager.on_community_cards(community_cards, phase)` — for equity tracking and BAD_BEAT detection.
5. **After settlement**: `memory_manager.on_hand_complete(recorded_hand, equity_history=...)` — fires `HandOutcomeDetector` + `dispatch_events` (Phase 3 dispatch path).

Cash mode's `run_hand` must call all five. Skipping any of (1)–(4) silently degrades `cash_pair_stats`, opponent tendencies, and BAD_BEAT detection — the relationship layer goes quiet without crashing, which is the worst kind of failure.

The cleanest implementation: copy the inner shape of `AITournamentRunner.run_hand` (lines ~1050–1340 of `run_ai_tournament.py`), strip the tournament-completion logic, and substitute the post-settlement cash-table sync at step 7.

## Fill-seats algorithm

Codex flagged that the plan handwaves over what "eligible AI pool" means. Explicit spec:

```
def fill_seats(table: CashTable, *, repos, now) -> CashTable:
    """Fill all open seats with eligible AI personalities."""

    occupied_names = {table.seats[i] for i in range(table.seat_count)
                      if table.seats[i] is not None}

    # Source: personalities visible to this session.
    # v1 ships from personalities.json + DB-seeded personalities;
    # use PersonalityRepository.list_personalities then filter.
    # NOTE: list_personalities currently returns display names, not
    # personality_ids. Either add a list_personality_ids method or
    # resolve in the seat filler. The filler keys on personality_id.
    candidates = repos.personality.list_eligible_for_cash_mode(
        visibility="public",  # v1: skip private/disabled
    )

    # Filter:
    eligible = []
    for personality in candidates:
        if personality.display_name in occupied_names:
            continue  # already at table — no two seats per personality
        if personality.id == PLAYER_SEAT_ID:
            continue  # the literal "player" sentinel
        current = repos.bankroll.load_ai_bankroll_current(
            personality.id, now=now,
        )
        knobs = repos.bankroll.load_personality_knobs(personality.id)
        threshold = int(table.min_buy_in * knobs.buy_in_multiplier)
        if current is None or current < threshold:
            continue  # can't afford to sit
        eligible.append((personality, current, knobs))

    # Deterministic ordering: sort by personality_id ASCII order.
    # Stable so test runs are reproducible. Random ordering can land
    # in v2 once rivalry-seek seating is in play.
    eligible.sort(key=lambda t: t[0].id)

    # Fill open seats ascending.
    open_indices = list(table.open_seats())
    for seat_index in open_indices:
        if not eligible:
            break
        personality, current, knobs = eligible.pop(0)
        buy_in = int(table.min_buy_in * knobs.buy_in_multiplier)
        buy_in = min(buy_in, table.max_buy_in)  # clamp to table cap
        # sit_down_ai handles bankroll debit + last_regen_tick + persistence
        table, _new_ai_state = sit_down_ai(
            table, seat_index, personality.id, buy_in,
            ai_bankroll=AIBankrollState(personality.id, current, now=None),
            now=now,
        )

    return table
```

Codex's concern #9 (display-name collision): the spec already says display names CAN collide for user-custom personalities. Two safeguards:
- The "already seated" filter dedupes by display name, so the same personality can't sit twice.
- If two *different* personality_ids share a display name AND both are eligible, the second gets skipped at "occupied_names" — the seat goes unfilled this hand. v1's seeded corpus has no collisions; user-custom personalities can collide but are rare. v2 needs a "make name unique within table" suffix scheme; flag for then.

## Hand-number policy

Codex flagged that the `HandOutcomeDetector` deduplicates events in-memory by `(hand_number, actor_id, target_id, event)` (`poker/memory/hand_outcome_detector.py:106`). A session restart that resets `hand_number` to 1 while reusing the same `AIMemoryManager` would suppress events whose tuples collide with prior-session hands.

**v1 policy:** hand_number is monotonic across the entire `CashSession` lifetime. A new session starts at hand_number = 1 with a fresh `AIMemoryManager` (and therefore a fresh detector dedup set). Session restart = new memory manager, no risk of stale dedup state.

The hand_history table's `UNIQUE(game_id, hand_number)` constraint (`poker/repositories/schema_manager.py:263`) handles uniqueness DB-side: cash sessions get their own `game_id`, so even a session that runs 10,000 hands is fine. If we ever persist `CashSession` across process restarts (v2 crash recovery), the on-disk hand_number checkpoint resumes monotonicity — flag for then.

## Controller lifecycle

Codex's concern #10: controllers must be per seated stint, not per hand.

**v1 policy:**
- AI sits → controller instantiated, attached to the session's `AIMemoryManager` (so opponent models / hand plan / etc. persist across hands at this seat).
- Each hand: `controller.current_hand_number = N` (mirrors the experiment runner's update at `run_ai_tournament.py:1089`).
- AI busts → controller discarded.
- AI leaves (v2 stop-win) → controller discarded.

The session module maintains a `Dict[seat_id, Controller]` keyed by personality_id (NOT seat_index — if a personality re-seats at a different seat next session, the controller-state mapping survives even if the seat-index doesn't). This dict gets rebuilt at fill-seats time.

## Tournament-bust bypass

The explorer's report flagged three mechanisms that exit on player bust. Cash mode handles each:

| Mechanism | Source | Cash-mode response |
|---|---|---|
| `handle_eliminations` setting `GAME_OVER` for human bust | `flask_app/handlers/game_handler.py:593` | Cash session **doesn't call `handle_eliminations`**. Bust handling lives in step 9 above. |
| `check_tournament_complete` firing on last-player-standing | `flask_app/handlers/game_handler.py:917` | Cash session **doesn't attach a `TournamentTracker`**, so this call is a no-op. |
| Experiment runner's `active_players <= 1` precheck | `experiments/run_ai_tournament.py:1078` | Cash session **doesn't reuse the experiment runner loop**. Its own loop (`run_hand`) replaces the precheck with the "fill seats" step. |

Cash mode runs its own `progress_game`-equivalent loop. It does NOT shell out to the experiment runner. The Flask routes (commit 5) call into `CashSession.run_hand` directly.

## Persistence surfaces touched per hand

| Surface | Repository | When |
|---|---|---|
| `AIBankrollState` per seated AI | `BankrollRepository.save_ai_bankroll` | After settlement, for every AI seat whose stack changed |
| `PlayerBankrollState` | `BankrollRepository.save_player_bankroll` | After settlement, only if player's bankroll changed (sit-down, leave, top-up, full-bust) |
| `cash_pair_stats` | `RelationshipRepository.apply_cash_pair_pnl` (via dispatch) | Automatic via Phase 3 dispatch — no new cash-mode code |
| `relationship_states` | `OpponentModelManager.record_event` | Automatic via Phase 3 dispatch — no new cash-mode code |
| `CashTable` itself | none in v1 | In-memory only. v2 may persist for crash recovery. |
| Per-hand stats / hand history | existing pipeline | Unchanged |

## What v1 doesn't ship (per spec)

- Stop-loss / stop-win (knobs persist but unused — `stop_loss_buy_ins` etc. read but no enforcement)
- Rivalry-seek seating (v2 — needs the lobby)
- Multi-table concurrent play (v2)
- AI-vs-AI background play (v3)
- Disconnect grace handling (commit 4 of this v1)
- Mid-hand quit (commit 4)

Commits 4-8 layer on top. Commit 3's success criterion: a 10-hand simulated session runs hands, applies settlement, busts/refills, and `cash_pair_stats` cumulative_pnl matches end-of-session chip delta between pairs.

## Open questions (post-codex)

Resolved by codex review:
- ~~Q5 hand-number reset~~ → monotonic within session, see "Hand-number policy".
- ~~Q6 settlement-vs-dispatch ordering~~ → detector reads `RecordedHand` not `CashTable.stacks`; settlement-before-dispatch is safe.
- ~~AI bankroll shim Option A vs B~~ → Option B (`sit_down_ai`).

Still open:

1. **`set_relationship_repo` once per session, or once per hand?** Once per session is the natural fit — the `OpponentModelManager` and dispatch state are per-session anyway. But the Flask game-load path re-instantiates the manager (per `game_routes.py:528-535`). Cash mode doesn't have a "load saved game" surface in v1, so this is moot for now; flag if it becomes relevant in commit 5.

2. **What `Player.name` does each AI seat use?** Proposal: `Player.name = personality.display_name`; the `personality_id` is set via the existing `set_personality_id` hook (`memory_manager._name_to_id` registry). Display-name collision risk in user-custom personalities — see Fill-seats algorithm's safeguard. v2 needs a unique-name-within-table suffix scheme.

3. **Equity history for `cash_pair_stats` / BAD_BEAT detection.** Cash session must call `EquityTracker` per the existing pattern. Memory: see `project_phase_b_2026_05_18.md` for the related sim-side wiring gap; cash mode is a separate path so no concern there.

4. **Player identity for `PlayerBankrollState.player_id`.** Flask supplies `user_id` (Google OAuth) or `guest_id`. Cash session takes it as a constructor param. Worth confirming the user system always provides one before commit 5.

5. **Personality eligibility scope.** `fill_seats` calls `list_eligible_for_cash_mode(visibility="public")` — this method doesn't exist yet on `PersonalityRepository` (`list_personalities` returns display names, not IDs, and doesn't filter for cash eligibility). Commit 3 adds it OR extends `list_personalities` to return IDs and visibility info. Lean toward a new method to avoid breaking existing callers.

## Commit-3 deliverables

- `cash_mode/seating.py` — **add `sit_down_ai`** sibling function (per the resolved shim decision). New tests in `tests/test_cash_mode/test_seating.py` for `sit_down_ai`.
- `cash_mode/session.py` — `CashSession` class with `run_hand`, `play_session`, `sit_player`, `leave_player`, `top_up_player`. Implements the full memory recording lifecycle.
- `cash_mode/seat_filler.py` — `fill_seats(table, ..., now)` helper per "Fill-seats algorithm" above.
- `poker/repositories/personality_repository.py` — **add `list_eligible_for_cash_mode`** (returns personality_id + display_name + visibility, filtered to public for v1). Keep the existing `list_personalities` intact.
- `cash_mode/__init__.py` — re-exports.
- `tests/test_cash_mode/test_session.py` — 10-hand simulated session, chip conservation, bust + refill paths, `cash_pair_stats` matches chip delta, `relationship_states` populates, **double-settlement guard test** (asserts `evaluating_hand_transition` is NOT entered by the session loop).

No changes to:
- `poker/poker_state_machine.py`
- `poker/poker_game.py`
- `poker/memory/memory_manager.py` (no `cash_mode=True` re-entry path needed)
- `poker/memory/hand_outcome_detector.py`
- `flask_app/handlers/game_handler.py` (commit 5 adds routes, not handler changes)
- `experiments/run_ai_tournament.py` (cash mode doesn't reuse experiment runner)

## Test scope for commit 3

- **Smoke**: 10-hand session runs end-to-end, no exceptions.
- **Chip conservation**: sum of (bankrolls + table stacks) is invariant across a 10-hand session, modulo any `full_bankroll_bust` grants (which add chips).
- **Bust + refill**: AI loses entire stack → seat clears → next hand a different AI fills the seat (or same AI returns if regen'd, given a fake time-jump).
- **Player bust**: player loses entire bankroll → fresh grant fires.
- **`cash_pair_stats`**: after a multi-hand session, the (winner, loser) pair's cumulative_pnl matches the sum of chip-flow deltas between them.
- **`relationship_states`**: BIG_WIN events fire from cash hands and write to `relationship_states` (already covered by Phase 3 tests; cash session tests confirm the wiring is live).
- **Double-settlement guard**: assert the cash session's loop calls `run_until([PokerPhase.EVALUATING_HAND])` and never `advance_state()` past it. Implementation: snapshot `state_machine.current_phase` after the inner loop returns; must equal `PokerPhase.EVALUATING_HAND` (not `INITIALIZING_HAND` of the next hand). A separate assert: `pot.winnings_awarded` flag (if it exists) is False at the entry to manual `award_pot_winnings`.
- **Memory lifecycle**: assert `on_hand_start`, `record_blinds`, `on_action`, `on_community_cards`, `on_hand_complete` fire in order across one hand. Spy/mock instrumentation on the memory manager; verify call count + ordering.
- **AI bankroll only moves at sit/leave/topup**: assert that after a 5-hand session where no AI sits or leaves, every AI's `AIBankrollState.chips` equals the value at session start (last_regen_tick may have advanced from regen reads, but stored chips don't move).

## Failure modes worth thinking about

- **AI runs out at the same instant the human leaves.** Session terminates cleanly; persisted state reflects the final stack distribution.
- **Sit-down race**: two simultaneous sit calls for the same seat. v1 single-threaded session — not a real concern. Flag for v2 when concurrent tables share the AI pool.
- **`award_pot_winnings` zero-sum violation**: if the engine has a bug that adds/loses chips, the chip-conservation test will catch it.
- **`CashTable.stacks` drift from `Player.stack`**: settlement converts the latter back into the former; if conversion has an off-by-one (e.g., forgets a returned-chips refund), conservation test catches it.

---
purpose: Proposed wiring for cash mode v1 commit 3 — how the cash-mode session loop integrates with the existing hand engine
type: design
created: 2026-05-18
last_updated: 2026-05-18
---

# Cash Mode v1 — Wiring Plan (commit 3)

This is the integration design for the cash-mode hand orchestration
commit. It's a proposal — open to redirection before code lands.

Companion docs:
- `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 2 (canonical spec)
- `docs/plans/CASH_MODE_V1_HANDOFF.md` (implementation handoff)
- Commits already landed: `613c0e9b` (schema), `8b245280` (table+seating), `de9f7479` (config_json pivot), `3fd21f03` (personality knobs)

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

1. **Fill open seats** (`seat_filler.fill_seats(table, ...)`)
   - For each `None` slot in `table.seats`, pick an AI personality whose `load_ai_bankroll_current(...) >= min_buy_in × buy_in_multiplier`.
   - Apply `sit_down(table, seat_index, personality_id, buy_in, ai_bankroll_as_pseudo_player)`. AI buy-in via `min_buy_in × buy_in_multiplier` per personality knobs.
   - **AI bankroll write path** — at AI sit-down, debit `AIBankrollState.chips` by the buy-in, set `last_regen_tick = now`, persist via `BankrollRepository.save_ai_bankroll`. Reuse `seating.sit_down` shape but the bankroll argument is an `AIBankrollState`-shaped wrapper (small adapter; see "AI bankroll shim" below).
2. **Block sit/leave/topup**: `table = table.with_hand_in_progress(True)`.
3. **Build `PokerGameState`** from the table:
   - Players: one `Player` per occupied seat. `stack = table.stack_of(seat_id)`. `is_human = (seat_id == PLAYER_SEAT_ID)`.
   - Controllers: AI seats get `HybridAIController` (or whatever the session config picks); human seat has no controller, awaits SocketIO input.
   - Seed-deterministic deck if the session config carries a seed (matches experiment runner's pattern).
4. **Run hand**: `state_machine.run_until([PokerPhase.EVALUATING_HAND])` interleaved with `controller.decide_action()` per the existing `run_hand` template.
5. **Snapshot pre-settlement stacks**: `start_stacks = {seat_id: stack ...}`.
6. **Settle**: `determine_winner` + `award_pot_winnings`. `PokerGameState.players[i].stack` reflects post-hand chips.
7. **Apply settlement to `CashTable`**: for each seat, compute `delta = post_stack - start_stack`, call `apply_settlement(table, seat_id, delta)`. Pure functions; table updates locally.
8. **Memory dispatch (UNCHANGED)**: `self.memory_manager.on_hand_complete(recorded_hand, equity_history=...)`. Because we called `set_relationship_repo(cash_mode=True)` at construction, `cash_pair_stats` writes fire automatically. **No new dispatch code in cash_mode/.**
9. **Bust handling**: for each seat where `table.stack_of(seat_id) == 0`:
   - **AI**: `seating.bust_at_table(table, seat_id)` clears the seat. Persist `AIBankrollState` (chips = 0, last_regen_tick = now) — regen starts from "now" so the AI is gated by `load_ai_bankroll_current >= min_buy_in × multiplier` next hand.
   - **Player**: `seating.bust_at_table(table, seat_id)` clears the seat. If `player_bankroll.chips == 0` AND no other table presence, fire `seating.full_bankroll_bust(player_bankroll)` for a fresh grant.
10. **Persist bankrolls**: `bankroll_repo.save_player_bankroll(...)`, `save_ai_bankroll(...)` for each touched personality.
11. **Unblock**: `table = table.with_hand_in_progress(False)`.
12. **Return** to the caller (router / human input wait / autonomous loop).

### Between hands

- **Sit**: route calls `session.sit_player(seat, buy_in)` → `seating.sit_down`.
- **Top up**: route calls `session.top_up_player(amount)` → `seating.top_up`.
- **Leave**: route calls `session.leave_player()` → `seating.leave_table`. Session continues with AI-only hands? **v1 design call: NO.** When the human leaves, the cash session terminates (no AI-vs-AI background play in v1 per spec §"v3 adds: AI-vs-AI background simulation"). Resume happens by starting a new `CashSession`.

## AI bankroll shim

`seating.sit_down` was designed for the human's `PlayerBankrollState` (it expects `.chips`, `.player_id`, `.starting_bankroll`). AI sit-down is structurally identical (debit `chips` by `buy_in`, set stack) but the bankroll surface is `AIBankrollState` (no `starting_bankroll`, has `last_regen_tick`).

**Options:**
- **(A) Reuse `sit_down` with a small wrapper**: build a `PlayerBankrollState(player_id=personality_id, chips=current_projected, starting_bankroll=0)` for the AI, call `sit_down`, then extract the new `chips` and write back to `AIBankrollState`. Cheap; slight semantic abuse of `PlayerBankrollState`.
- **(B) Add `seating.sit_down_ai` as a sibling**: takes `AIBankrollState`, mirrors the same accounting but writes `last_regen_tick = now` on the new state. Cleaner separation but duplicates ~20 lines.

**Recommend (A) for v1.** The shim is purely internal to the session module; downstream callers never see the synthetic `PlayerBankrollState`. (B) is the right move if a third bankroll type ever materializes.

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

## Open questions for review

1. **`set_relationship_repo` once per session, or once per hand?** Once per session is the natural fit — the `OpponentModelManager` and dispatch state are per-session anyway. But the Flask game-load path re-instantiates the manager (per `game_routes.py:528-535`). Cash mode doesn't have a "load saved game" surface in v1, so this is moot for now; flag if it becomes relevant in commit 5.

2. **What `Player.name` does each AI seat use?** The hand engine uses `player.name` as an identifier throughout (stat tracking, hand history, etc.). Cash mode keys on `personality_id`. Proposal: `Player.name = personality.display_name`; the `personality_id` is set via the existing `set_personality_id` hook (`memory_manager._name_to_id` registry from Phase 3 work). Personality display names are unique within the seeded set, so collisions don't happen.

3. **Equity history for `cash_pair_stats` / BAD_BEAT detection.** Both Flask handler and experiment runner pass `equity_history` into `on_hand_complete`. Cash session must do the same — call `EquityTracker` per the existing pattern. Memory: see `project_phase_b_2026_05_18.md` for the related sim-side wiring gap; cash mode is a separate path, no concern.

4. **Player identity for `PlayerBankrollState.player_id`.** Cash mode keys on a `player_id` string. The Flask app's user system supplies this (Google OAuth user id, or guest_id for unauthenticated sessions). Cash session takes it as a constructor param — no new identity scheme needed. Worth confirming the user system always provides one before commit 5.

5. **Hand-number reset behavior.** Tournaments reset hand_number per game; cash mode runs effectively forever at one table. Proposal: hand_number increments continuously within a `CashSession`, resets on session restart. Aligns with how cash games work IRL ("hand #1247 of this session"). The hand-history table doesn't care — it keys on `(game_id, hand_number)`, and cash mode passes a unique `game_id` per session.

6. **Settlement timing vs. memory dispatch order.** Current order in `handle_evaluating_hand_phase`: `award_pot_winnings` → psychology snapshot → `on_hand_complete` → emit. The cash session's apply_settlement-then-bust-detection sits between `award_pot_winnings` and `on_hand_complete` — does that break any invariants the existing dispatch code assumes? Specifically, does `HandOutcomeDetector` read `player.stack` before or after the cash-table delta is applied? If it reads `player.stack`, we're fine (already updated by `award_pot_winnings`). If it reads `CashTable.stacks`, ordering matters. Worth a quick check of `hand_outcome_detector.py`.

## Commit-3 deliverables

- `cash_mode/session.py` — `CashSession` class with `run_hand`, `play_session`, `sit_player`, `leave_player`, `top_up_player`.
- `cash_mode/seat_filler.py` — `fill_seats(table, eligible_ais, ...)` helper.
- `cash_mode/__init__.py` — re-exports.
- `tests/test_cash_mode/test_session.py` — 10-hand simulated session, chip conservation, bust + refill paths, `cash_pair_stats` matches chip delta, `relationship_states` populates.

No changes to:
- `poker/poker_state_machine.py`
- `poker/poker_game.py`
- `poker/memory/memory_manager.py`
- `flask_app/handlers/game_handler.py` (commit 5 adds routes, not handler changes)
- `experiments/run_ai_tournament.py` (cash mode doesn't reuse experiment runner)

## Test scope for commit 3

- **Smoke**: 10-hand session runs end-to-end, no exceptions.
- **Chip conservation**: sum of (bankrolls + table stacks) is invariant across a 10-hand session, modulo any `full_bankroll_bust` grants (which add chips).
- **Bust + refill**: AI loses entire stack → seat clears → next hand a different AI fills the seat (or same AI returns if regen'd, given a fake time-jump).
- **Player bust**: player loses entire bankroll → fresh grant fires.
- **`cash_pair_stats`**: after a multi-hand session, the (winner, loser) pair's cumulative_pnl matches the sum of chip-flow deltas between them.
- **`relationship_states`**: BIG_WIN events fire from cash hands and write to `relationship_states` (already covered by Phase 3 tests; cash session tests confirm the wiring is live).

## Failure modes worth thinking about

- **AI runs out at the same instant the human leaves.** Session terminates cleanly; persisted state reflects the final stack distribution.
- **Sit-down race**: two simultaneous sit calls for the same seat. v1 single-threaded session — not a real concern. Flag for v2 when concurrent tables share the AI pool.
- **`award_pot_winnings` zero-sum violation**: if the engine has a bug that adds/loses chips, the chip-conservation test will catch it.
- **`CashTable.stacks` drift from `Player.stack`**: settlement converts the latter back into the former; if conversion has an off-by-one (e.g., forgets a returned-chips refund), conservation test catches it.

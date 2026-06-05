---
purpose: Technical reference for the cash-mode full sim — AI-only poker hands run at unseated tables during lobby refresh, with persistent psychology and dealer rotation. Covers architecture, invariants, performance, limitations, and follow-on opportunities.
type: reference
created: 2026-05-19
last_updated: 2026-06-03
---

# Cash Mode — Full Sim

The **full sim** is the cardplay engine behind the lobby's
"the world feels alive" affordance. While the player is browsing
the multi-table lobby — or away entirely with the tab closed — the
AI seats at unseated tables play real hands of poker against each
other. Chips actually move; tilt actually builds; the dealer
button actually rotates; and the lobby ticker surfaces hand-level
drama (big wins, bust-outs, all-ins) drawn from real outcomes.

This doc describes the system **as shipped**. For the design
journey and rationale behind individual choices, see
[CASH_MODE_FULL_SIM_HANDOFF.md](../plans/CASH_MODE_FULL_SIM_HANDOFF.md).

## Primary goal

Make the lobby feel like a populated, evolving room instead of a
list of frozen seats. Sub-goals:

1. **Honest chip movement** — chips at unseated tables shift due
   to real poker outcomes, not random uniform deltas. When a
   player sits down, they inherit a stack distribution that
   reflects how the table has actually been running.
2. **Persistent AI psychology** — an AI on a 3-bad-beat streak at
   $50 stays tilted across sim ticks, across player sessions, and
   across backend restarts. Their next interaction with the
   player reflects that history.
3. **Visible drama** — busts, all-ins, big pots all surface to the
   lobby's activity ticker via the same `LobbyEvent` ring buffer
   that `join` / `leave` events already use.
4. **Real dealer position** — table-card UIs show the dealer
   button on the seat that just dealt, so a player picking a seat
   knows what poker position (UTG / CO / BTN / SB / BB) the open
   seats correspond to.

## Where it runs

The sim is **read-driven**, not daemon-driven. Each call to
`GET /api/cash/lobby` runs `cash_mode.lobby.refresh_unseated_tables`
which, for every table that has no human seated:

1. Computes how many hands should fire (`hand_burst_count`).
2. Runs them sequentially via `cash_mode.full_sim.play_one_hand`.
3. Persists the resulting seat chip counts + dealer index.
4. Translates hand outcomes into lobby ticker events.

Two reasons there's no background daemon:

- **Zero cost when no one's looking.** If the lobby tab isn't
  open, no compute happens.
- **Polling client is already there.** The frontend polls
  `/api/cash/lobby` every 8 seconds. That's the natural tick;
  sim hands ride it.

Cost: tables don't tick when no client is connected. The
catch-up burst (below) absorbs the resulting gap when the player
returns.

## Architecture

```
GET /api/cash/lobby
  ↓
flask_app.routes.cash_routes.get_lobby
  ↓
cash_mode.lobby.refresh_unseated_tables (per table without a human)
  │
  ├── 1. compute burst_n = hand_burst_count(gap_seconds, ...)
  │
  ├── 2. for hand in range(burst_n):
  │       ├── rotate dealer (next occupied seat)
  │       ├── play_one_hand(seats, sandbox_id=..., starting_dealer_seat_idx=...,
  │       │                 bankroll_repo=..., chip_ledger_repo=..., table_id=...,
  │       │                 table_max_buy_in=..., controller_cache=...)
  │       │     │   (sandbox_id is REQUIRED — keys psychology + ledger + memory)
  │       │     ├── snapshot global random; reseed from hand rng
  │       │     ├── build PokerGameState(record_snapshots=False)
  │       │     ├── controller cache: get_or_create_tracked(pid, factory)
  │       │     │     └── on miss: hydrate_persona_psychology() reads
  │       │     │           ai_bankroll_state.emotional_state_json keyed
  │       │     │           (personality_id, sandbox_id) + idle energy recovery
  │       │     ├── wire per-sandbox AIMemoryManager (opponent models + relationships)
  │       │     │     └── on_hand_start → run hand → on_hand_complete
  │       │     │           (record_showdown_equity=False; completed_hands cleared)
  │       │     ├── run engine to EVALUATING_HAND, await pot award
  │       │     ├── _apply_rake_to_winner() → chip ledger table_rake (if repo wired)
  │       │     ├── every PSYCHOLOGY_FLUSH_EVERY_HANDS hands: flush psychology + opponent models
  │       │     ├── compute headline pair, hand events (BUST, ALL_IN), dealer_seat_idx
  │       │     └── return HandSimResult; restore global random
  │       └── mutate table.seats + table.dealer_idx from result
  │
  ├── 3. refresh_table_roster (movement decisions for AIs whose chips drifted)
  │
  ├── 4. cash_table_repo.save_table (persists seats, dealer_idx, last_activity_at)
  │
  └── 5. _emit_burst_events:
        ├── pick headline big_win/big_loss across the burst
        ├── _emit_hand_events: cap to one ALL_IN + one BUST per table per burst
        └── _emit_burst_summary: "...and N more hands at $X — Napoleon +$220 net"
```

## Core entry point

```python
# cash_mode/full_sim.py

def play_one_hand(
    seats: List[dict],          # cash-table seat dicts (kind/personality_id/chips)
    *,
    big_blind: int,
    rng: random.Random,
    sandbox_id: str,            # REQUIRED — keys psychology, ledger rake, memory
    max_pot_bb: int = DEFAULT_MAX_POT_BB,                      # unused in Phase 2; caller compat
    big_event_threshold_bb: int = DEFAULT_BIG_EVENT_THRESHOLD_BB,  # overridable big_event gate
    name_for: Callable[[str], str] = _default_name_for,
    controller_cache: Optional[LruControllerCache] = None,
    starting_dealer_seat_idx: Optional[int] = None,
    bankroll_repo: Optional[Any] = None,    # drives psychology hydrate/flush
    chip_ledger_repo: Optional[Any] = None, # drives table_rake destruction
    table_id: Optional[str] = None,         # ledger context only
    table_max_buy_in: Optional[int] = None, # opponent-model normalization
) -> HandSimResult: ...
```

`sandbox_id` is **required** (`cash_mode/full_sim.py:600`). Every cash-world
write — psychology, ledger rake, opponent models — is sandbox-scoped to prevent
cross-contamination between sandboxes; a sim that omitted it would run green and
write nothing meaningful. The repo types are now `Optional[Any]` (not the
concrete `BankrollRepository`) so the function stays import-light.

**I/O contract.** No SocketIO emits and no LLM calls — but the "no DB writes"
claim from v1 is no longer accurate. With repos wired, `play_one_hand` can write:

- **Psychology** — `flush_persona_psychology` to `ai_bankroll_state.emotional_state_json`
  every `PSYCHOLOGY_FLUSH_EVERY_HANDS` hands (needs `bankroll_repo`).
- **Chip ledger** — `_apply_rake_to_winner` records a `table_rake` row
  (`cash_mode/full_sim.py:948`, needs `chip_ledger_repo` + `RAKE_ENABLED`).
- **Opponent models / relationships** — `memory_manager.on_hand_complete` +
  periodic `save_opponent_models` (needs a `db_path`-bearing `bankroll_repo`).

When all repos are `None` (the common test path) it is side-effect-free apart from
the global-RNG snapshot/restore. The caller (lobby refresh loop) still owns
seat-chip persistence and event emission.

### `HandSimResult` shape

| Field | Type | Notes |
|---|---|---|
| `new_seats` | `List[dict]` | Seats list with mutated chip counts (deep-copied from input — never mutates caller's seats) |
| `winner_pid` / `loser_pid` | `Optional[str]` | Headline pair: max-gain vs max-loss personality |
| `delta` | `int` | Winner's net chip gain (always ≥ 0) |
| `big_event` | `bool` | `True` when `delta >= big_blind × big_event_threshold_bb` (caller param, defaults to `DEFAULT_BIG_EVENT_THRESHOLD_BB` = 8 BB) |
| `pot` | `int` | Total chips moved (sum of positive deltas across all seats) |
| `hand_events` | `List[HandEvent]` | BUST / ALL_IN events detected from the post-hand state |
| `dealer_seat_idx` | `Optional[int]` | The cash-table seats index of who held the button this hand |
| `showdown_hands` | `Optional[List[ShowdownHand]]` | Reserved for future SUCKOUT detection; currently None |

## Controller cache

The Phase 0 spike measured `TieredBotController` construction at
~77 ms per instance (most of which is `AIPokerPlayer.__init__`
constructing an `Assistant` even though full sim never invokes
the LLM). With 6 seats × 4-5 tables × bursts of up to 30 hands,
rebuilding controllers per hand would blow the 500 ms
lobby-response budget on the very first refresh.

`cash_mode.controller_cache.LruControllerCache` is a generic
LRU keyed by `personality_id`:

- **`max_size=50`** by default — sized for 5 stakes × 6 seats =
  30 active slots plus headroom for recently-departed AIs.
- **`get_or_create_tracked(pid, factory) → (T, bool)`** returns
  `(value, was_miss)`. Hits promote to MRU; misses build via the
  factory and evict the LRU entry when over capacity.
- **`get(pid)`** is a peek that does NOT change LRU order (test
  inspection only).
- **Not thread-safe** — full sim runs on the Flask request
  thread.

The default cache is a process-level singleton lazily
initialized inside `_get_default_controller_cache()`. Tests pass
their own cache to keep instances isolated.

## Dealer rotation

The dealer button is **load-bearing for seat-choice UX**: a
player picking an open seat needs to know what poker position
(UTG / CO / BTN / SB / BB) that seat would be in for the upcoming
hand. The button must reflect the **real engine dealer**, not a
cosmetic counter.

Mechanism:

1. `CashTableState.dealer_idx` (schema v96) stores the seat index
   of the most recent dealer. Persists across backend restart.
2. `refresh_unseated_tables` computes the next dealer per hand
   via `_next_occupied_seat(seats, start_after=table.dealer_idx)`
   and passes it as `starting_dealer_seat_idx` to `play_one_hand`.
3. `play_one_hand` maps the seat index into the engine's
   `players` array (compacted to AI seats only) and sets
   `PokerGameState.current_dealer_idx`, so SB/BB get posted from
   the correct seats and betting order is honest.
4. The post-hand `HandSimResult.dealer_seat_idx` reports who
   actually held the button (handles the "starting hint pointed
   at a now-open seat" fall-back, where the engine drops back to
   player 0).
5. Lobby writes `r.dealer_seat_idx` back to `table.dealer_idx`,
   `cash_table_repo.save_table` persists.

`get_dealer_index(table)` is the read-side helper used by the
lobby route's response serializer. It returns `table.dealer_idx`
when the seat is occupied; otherwise self-heals to the next
occupied seat (an AI may have left the dealer seat between
refreshes).

## Psychology persistence

AIs running real hands develop emotional state — tilted after a
bad beat, confident after a hot streak. Without persistence:

- A backend restart wipes every AI's psychology back to defaults.
- LRU eviction of a controller silently loses its state.
- The lobby's per-seat `emotion` field would default to
  "confident" for every unseated AI, breaking the
  emotion-tinted-border affordance.

### Shared module: `cash_mode/psychology_persistence.py`

Hydrate / flush / serialize were **promoted verbatim** out of `full_sim.py`
into `cash_mode/psychology_persistence.py` so the off-screen sim, the live cash
seat build, and the cash-world (Circuit) tournament builder share **one**
implementation. `full_sim.py` re-imports the two it calls directly — hydrate +
flush — under their historical private aliases (`cash_mode/full_sim.py:42-45`);
`serialize_persona_psychology` is called only internally by `flush_persona_psychology`,
so full_sim no longer imports it (its old `_serialize_psychology` alias survives
only in a comment at `cash_mode/full_sim.py:471`):

| Public name | Old private alias | Imported into full_sim? | Purpose |
|---|---|---|---|
| `hydrate_persona_psychology(controller, personality_id, bankroll_repo, sandbox_id)` | `_hydrate_psychology` | yes (`full_sim.py:44`) | Read blob → `PlayerPsychology.from_dict` → idle energy recovery |
| `flush_persona_psychology(controller, personality_id, bankroll_repo, sandbox_id)` | `_flush_psychology` | yes (`full_sim.py:43`) | Serialize live state → `save_emotional_state_json` |
| `serialize_persona_psychology(controller) → Optional[str]` | `_serialize_psychology` | no (only called by flush) | `to_dict() → json.dumps`, None if no psychology |

All three take `sandbox_id` as a required arg (hydrate/flush) — callers MUST pass
the resolved sandbox, never None (`psychology_persistence.py:17-18`).

### Persistence column

Schema v97 adds `ai_bankroll_state.emotional_state_json TEXT NULL`:

- **Keyed on `(personality_id, sandbox_id)`** — the single-pid save/load paths
  use `WHERE personality_id = ? AND sandbox_id = ?`
  (`bankroll_repository.py:229,264`); the batched `_for_pids` read scopes by
  `WHERE sandbox_id = ? AND personality_id IN (...)`
  (`bankroll_repository.py:364-365`). A mood is per-persona *per sandbox*, so
  the same celebrity carries an independent mood in each sandbox.
- Serialized via `PlayerPsychology.to_dict() → json.dumps()`.
- NULL means "no persisted state yet" → fresh defaults on hydrate.

The column lives on `ai_bankroll_state` because the persistence
cadences for chips and psychology are independent — chips write
on sit/leave/move events, psychology writes every 10 sim hands —
but they share the same `(personality_id, sandbox_id)` key.

### Idle energy recovery (decay-on-read)

`_apply_idle_energy_recovery` (`psychology_persistence.py:96-124`) runs at the
end of `hydrate_persona_psychology`. A persisted mood is a frozen snapshot — no
hands fire while a persona is idle — so on each cache-miss read it computes
`idle_seconds` from the blob's `last_updated` and springs the `energy` axis
toward `anchors.baseline_energy` via `cash_mode.movement.project_idle_energy`
(the same projection the lobby uses). **Only `energy` recovers on idle;**
confidence / composure / tilt carry the last snapshot until live hands move them.
Best-effort: a missing/unparseable `last_updated` skips recovery.

### Cache discipline

- **Hydrate-on-miss**: `play_one_hand` uses
  `controller_cache.get_or_create_tracked(pid, factory)`. When
  `was_miss=True`, `hydrate_persona_psychology(controller, pid,
  bankroll_repo, sandbox_id)` reads `emotional_state_json` and reconstructs
  the controller's `psychology` via `PlayerPsychology.from_dict`, then applies
  idle energy recovery. Call **only** on a fresh seat build, never on cold-load
  (cold-load restores per-game `psychology_json`; re-hydrating the persona blob
  there would clobber the evolved in-game mood — `psychology_persistence.py:50-52`).
- **Periodic flush**: every `PSYCHOLOGY_FLUSH_EVERY_HANDS = 10`
  sim hands per AI, `flush_persona_psychology` serializes the live state
  back to the column. The counter lives on the controller
  (`_full_sim_hand_count` attribute, `_SIM_HAND_COUNTER_ATTR`), incremented by
  `_maybe_flush_psychology` after each hand.
- **All repo I/O is best-effort** — malformed JSON, save failures,
  or a missing repo all log at debug and fall back to defaults
  rather than blocking hands.

### Lobby emotion resolver

`/api/cash/lobby` returns an `emotion` string per AI seat.
Resolution priority (in `flask_app.routes.cash_routes.get_lobby`):

1. **Live in-memory state** — for AIs at the player's currently-
   active cash table, `active_emotions[name]` reads
   `controller.emotional_state.get_display_emotion()`.
2. **Persisted state** — for AIs at tables the player isn't at,
   `unseated_emotions[pid]` resolves via
   `_resolve_emotion_from_blob` → `PlayerPsychology.from_dict` →
   `get_display_emotion()`. Backed by the batched
   `bankroll_repo.load_emotional_state_json_for_pids(pids, sandbox_id=...)` so
   the lobby response stays a single query for unseated emotions
   regardless of seat count.
3. **`"confident"` default** — AIs that have never been touched
   by sim, or whose blob failed to parse.

## Hand events + lobby ticker

`_detect_hand_events` inspects the post-hand engine state and
emits structured `HandEvent` records for:

- **BUST** — final chips ≤ 0. Survives into the next refresh tick
  where the existing `forced_leave` movement path removes the
  seat from the table. The bust moment itself is the more
  dramatic ticker beat, so we emit it immediately.
- **ALL_IN** — engine `is_all_in` flag still set at hand end. The
  flag persists through pot award until
  `reset_game_state_for_new_hand` runs, so reading it post-award
  correctly captures "someone went all-in this hand" regardless
  of whether they won or lost.

Deferred to future commits:

- **SUCKOUT** — needs per-street equity history.
- **NICE_POT** — redundant with the existing `big_win` threshold
  emission; kept in the event vocabulary for future
  differentiation.

### Per-burst event cap

The doc's Q6 resolution: at most one event per type per table
per refresh, plus a single `burst_summary` event when more than
one hand fired.

| Event | Cap | Selection rule |
|---|---|---|
| `big_win` / `big_loss` | 1 pair per burst per table | Largest `delta` among `big_event=True` hands |
| `all_in` | 1 per burst per table | First in burst |
| `bust` | 1 per burst per table | First in burst (BUST subsumes ALL_IN if same player) |
| `burst_summary` | 1 per burst per table | Only when `len(sim_results) > 1` |

`burst_summary` carries the personality with the largest
cumulative net delta across the burst:
`"...and 24 more hands at $50 — Napoleon +$1,200 net"`.

## Catch-up burst

When the player closes the tab for an hour and returns, the
read-driven cadence would otherwise tick only one hand on their
return — the world would look frozen.

`hand_burst_count(gap_seconds, base_prob, rng)`:

| Gap | Behavior |
|---|---|
| `< DEFAULT_BURST_THRESHOLD_SECONDS` (30 s) | Probability gate: returns 0 or 1 based on `base_prob` |
| `≥ 30 s` | Burst: `floor(gap_seconds / DEFAULT_BURST_PACING_SECONDS)` hands |
| Any gap | Capped at `DEFAULT_BURST_HAND_CAP = 30` |

Numbers come from the Phase 0 spike: per-hand cost is ~4 ms
warm, so 30 hands × 4 tables = ~480 ms — fits the 500 ms lobby
response budget even at the cap.

`gap_seconds` is `(now - table.last_activity_at).total_seconds()`.
The lobby uses the same `last_activity_at` it already bumps on
every save — no new schema needed.

## Determinism + RNG hygiene

### Hermetic global random

Several modules downstream of `play_one_hand`
(`equity_calculator`, `chattiness_manager`, expression filtering)
call `random.x()` without a seeded RNG. Without isolation:

1. State leaks from `play_one_hand` into the rest of the process.
2. Two calls with the same hand `rng` produce different outcomes
   whenever the global RNG happens to be in a different position.

Fix: `play_one_hand` snapshots `random.getstate()` on entry,
reseeds from the hand `rng`, and restores on exit. The cost is
two `getstate`/`setstate` calls per hand (~µs). The proper fix —
threading an explicit `rng` through every decision-pipeline
call — is out of scope.

### Cross-call determinism (limited)

- ✅ **Same starting seats + same hand rng + fresh cache** →
  same outcome (pinned by
  `test_full_sim.py::test_determinism_with_fresh_cache`).
- ❌ **Same starting seats + same hand rng + warm cache** does
  NOT guarantee the same outcome. Cached controllers accumulate
  psychology / memory state across hands by design — that's the
  whole point of persistent psychology — so replaying a hand
  against a different cache state can diverge.

The lobby never needs cross-call determinism; it just runs the
hands forward.

### Controller RNG reseeding

Inside `play_one_hand`, each cached controller's
`controller.rng` is re-seeded from the hand `rng` at the start of
the hand:

```python
ctrl.rng = random.Random(rng.randrange(2**32))
```

This makes per-hand outcomes reproducible from the hand `rng`
regardless of whether the controller was a cache hit or miss.

## Invariants

### Chip conservation

Absent rake, `play_one_hand` neither creates nor destroys chips:
the total across `seats` (sum of AI seat chips + open seats which
carry 0) is preserved across a hand. Verified by
`test_chip_conservation` over many seeds. **The one exception is
the rake:** when `chip_ledger_repo` is wired and `RAKE_ENABLED`,
`_apply_rake_to_winner` (`cash_mode/full_sim.py:522`) skims
`economy_flags.compute_rake(pot, big_blind)` off the winning
seat's stack, clamped to the winner's net win for the hand
("no win, no drop"). That amount leaves the seats and is recorded
as a `table_rake` ledger row.

### Audit neutrality (with rake)

The rake debits the **seat** account, not the bankroll: under
chip custody the at-table chips live in `chip_ledger.ai_seat(
sandbox_id, pid)`, so the rake is sourced from there
(`full_sim.py:581-585`). Debiting the bankroll account `ai:<pid>`
instead would desync the ledger-derived bankroll from the stored
int — the chips never left the bankroll. (Pre-custody it falls
back to `chip_ledger.ai(pid)`, the historical approximation.) The
rake reason stays `table_rake`, so bank-pool depth accounting is
unchanged and the rake recycles to the pool per the closed-economy
model rather than being destroyed. Apart from rake, full-sim hands
move chips **within seats only** — never across the seat ↔
bankroll boundary; the only other audit-relevant movement is
`cash_mode/movement.py:refresh_table_roster` returning a leaver's
chips via `credit_ai_cash_out`, unchanged by full sim.

### Memory flatness

Long-running sim must NOT grow heap monotonically. The
`PokerGameState` snapshots tuple maintained by
`ImmutableStateMachine` was the original leak (~25 MB / 1000
hands measured in the Phase 0 spike). Mechanism:
`play_one_hand` builds its state machine with
`record_snapshots=False`, which short-circuits
`advance_state_pure`'s snapshot append entirely. Pinned by
`test_full_sim.py::TestMemoryFlatness` — 1000 hands must grow
heap by < 5 MB.

### Math-floor abstract-action consistency

`poker/strategy/math_floor.py`'s short-stack rule now emits the
abstract action `'jam'` (not engine-level `'all_in'`) so
`resolve_preflop_sizing` / `resolve_postflop_sizing` can map it.
Pre-fix, the tournament runner silently swallowed a
`ValueError: Unknown abstract action: 'all_in'` via fold-on-error
on ~0.02% of decisions. The fix landed in the spike fallout
commit (`baf7f0e6`).

## Performance

Phase 0 spike (6 personalities, BB=100, 100 BB starting stacks,
backend container):

| Scenario | Hands/sec | ms/hand |
|---|---|---|
| Warm table (controllers reused) | 227 | 4.4 |
| Lobby burst (4 tables × 25 hands, cached) | 253 | 4.0 |
| Cold per hand (rebuild controllers each hand) | 2.1 | 477 |

**Setup cost dominates.** ~460 ms per table, ~77 ms per
controller (Assistant construction). Cache hit is ~free, so warm
throughput is ~100× cold. This is why the controller cache is
load-bearing, not optional.

**Strategy table load** is a one-shot ~30 ms paid once per
process.

**Memory**: imports settle at ~135 MB; warm 1000-hand sim
finishes ~145 MB (delta < 5 MB) once snapshot recording is off.

## Limitations

### Currently known + intentional

1. **Partial wall-clock psychology decay.** Only the `energy` axis
   recovers on idle read (see *Idle energy recovery* above). The
   other axes — confidence, composure, tilt — are still read
   verbatim, so a bad-beat tilt persisted at midnight carries to
   noon on those axes if no sim hand has touched that AI in
   between. In practice the catch-up burst fires gameplay-driven
   recovery hands before any player sees stale state.

2. **No on-evict flush.** Periodic flush every 10 hands caps the
   eviction state-loss window at < 10 hands of staleness. With
   `max_size=50` and ~30 active AIs, eviction is rare; this trade
   keeps the cache generic.

3. **No hand history persistence at unseated tables.** Sim hands
   feed opponent models + relationships (via `on_hand_complete`
   with `record_showdown_equity=False`, and `completed_hands` is
   cleared each hand so no `hand_history` rows accumulate) but do
   not write `hand_history` or `personality_snapshots`. v2 might
   add it if a replay UI ships.

4. **No SUCKOUT event detection.** Would need per-street equity
   tracking. Plausible follow-up if drama-event volume needs
   richer signals.

5. **Single-process only.** The controller cache and the
   per-sandbox `AIMemoryManager` cache (`_session_memory_managers`)
   are per-process singletons. Multi-worker deployments would need
   a shared cache (Redis-backed) or a movement protocol; v1 ships
   single-process.

### Defensive limits (likely fine but worth noting)

6. **30-hand burst cap.** Multi-hour absences hit the cap; we
   trade realism for response-time guarantee. The
   `burst_summary` event tells the player "the world advanced"
   without enumerating every hand.

7. **`_MAX_ACTIONS_PER_HAND = 200`.** Safety ceiling for stuck-
   loop bugs. A 6-handed hand rarely exceeds ~40 actions, so 200
   has comfortable margin.

8. **Cache thrash above 50 distinct AIs/process.** With 5 stakes
   × 6 seats = 30 baseline plus idle-pool churn, we're well under
   the cap; if the lobby grows to dozens of tables, the cap
   should grow with it.

## Opportunities

In rough priority order:

### Shipped since v1

- **AI-to-AI opponent modeling during sim.** A per-sandbox
  `AIMemoryManager` is now wired into every controller
  (`_session_memory_managers` cache; `on_hand_start` /
  `on_hand_complete` per hand), so opponent stats + relationship
  (cash_pair_stats PnL) updates evolve at unseated tables, not
  just player-vs-AI tables. Models flush every
  `PSYCHOLOGY_FLUSH_EVERY_HANDS` hands via `save_opponent_models`.
- **Wall-clock decay-on-read (energy only).** Implemented as
  `_apply_idle_energy_recovery` using
  `cash_mode.movement.project_idle_energy`. See *Idle energy
  recovery*; extending it to confidence/composure/tilt is still open.

### Near-term (would meaningfully sharpen v1)

1. **SUCKOUT event detection.** Needs an equity snapshot at each
   street transition inside `_run_hand`. The lobby ticker
   surface is already designed to accept it (the constant
   `HAND_EVENT_SUCKOUT` exists). Estimated 0.5 days.

2. **On-evict psychology flush.** Add `on_evict: Callable[[str,
   T], None]` to `LruControllerCache`; wire it on the default
   cache to call `flush_persona_psychology`. Closes the < 10 hand
   eviction-loss window. Estimated 0.25 days.

### Medium-term (v2 territory)

3. **Hand history persistence at unseated tables.** Write sim
   hand_history rows so a player can replay "what hands did
   Napoleon play while I was away." Needs careful DB volume
   budgeting (10× write multiplier).

4. **Rivalry-seek seating.** AIs proactively move to the
   player's table when `project_heat(player) > threshold`. The
   relationship layer's payoff. Adds a movement decision option
   in `cash_mode/movement.py:evaluate_ai_movement`.

5. **Multi-active-table per stake.** v1 ships one table per
   stake; schema admits more. The seeding pass + lobby UX would
   need extensions.

### Far-term (likely separate product slice)

6. **Spectator mode.** Watch a sim'd hand at an unseated table
   in real time. New UI surface; the engine path is ready.

7. **Cross-process / cross-host sim.** Redis-backed event bus +
   distributed controller cache, so a multi-worker deployment
   can share sim state.

8. **Background timer-driven sim.** A low-frequency server-side
    timer (every 5 min?) fires `refresh_unseated_tables` even
    when no lobby client is connected. Would close the
    "no-tab-open = world freezes" gap, at the cost of always-on
    compute.

## File index

| Path | Purpose |
|---|---|
| `cash_mode/full_sim.py` | Entry point (`play_one_hand`), hand execution, hand-event detection, rake skim, per-sandbox `AIMemoryManager` wiring |
| `cash_mode/psychology_persistence.py` | Shared `hydrate_persona_psychology` / `flush_persona_psychology` / `serialize_persona_psychology` + idle energy decay-on-read; imported by full_sim, live cash seat build, cash-world tournament builder |
| `cash_mode/controller_cache.py` | Generic LRU keyed by personality_id |
| `cash_mode/lobby.py` | `refresh_unseated_tables`, burst orchestration, event emission, dealer rotation |
| `cash_mode/activity.py` | `LobbyEvent` ring buffer, event type constants, message formatters |
| `cash_mode/tables.py` | `CashTableState.dealer_idx` field |
| `flask_app/routes/cash_routes.py` | `_resolve_emotion_from_blob`, 3-tier emotion priority, `dealer_index` serialization |
| `poker/repositories/cash_table_repository.py` | Persists `dealer_idx` (schema v96) |
| `poker/repositories/bankroll_repository.py` | `save_emotional_state_json` / `load_emotional_state_json` / `..._for_pids` — all keyed `(personality_id, sandbox_id)` (schema v97) |
| `cash_mode/economy_flags.py` | `RAKE_ENABLED`, `compute_rake`, `CHIP_CUSTODY_ENABLED` (gate the rake skim) |
| `core/economy/ledger.py` | `record_table_rake`, `ai_seat` / `ai` accounts (rake destination) |
| `poker/poker_state_machine.py` | `record_snapshots=False` flag |
| `poker/strategy/math_floor.py` | Emits `'jam'` not `'all_in'` (Phase 0 fallout fix) |
| `scripts/full_sim_spike.py` | Phase 0 throughput / memory benchmark (gitignored) |

## Related docs

- [CASH_MODE_FULL_SIM_HANDOFF.md](../plans/CASH_MODE_FULL_SIM_HANDOFF.md) — design history, commit-by-commit handoff, locked decisions
- [CASH_MODE_ECONOMY.md](CASH_MODE_ECONOMY.md) — chip flow paths, conservation invariant, audit
- [CASH_MODE_AND_RELATIONSHIPS.md](../plans/CASH_MODE_AND_RELATIONSHIPS.md) Part 2 — cash mode architecture, AI table selection
- [CASH_MODE_BACKING_SYSTEM_HANDOFF.md](../plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md) — Phase 4 (AI as borrowers) hooks into the real bankroll dynamics full sim produces
- [PSYCHOLOGY_DESIGN.md](PSYCHOLOGY_DESIGN.md) — `PlayerPsychology` / `EmotionalState` model the sim persists

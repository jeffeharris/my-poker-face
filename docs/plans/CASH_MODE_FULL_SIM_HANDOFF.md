---
purpose: Historical handoff for full background simulation — captures the design rationale and commit-by-commit plan that led to shipping. For "how the sim works now," read CASH_MODE_FULL_SIM.md.
type: guide
created: 2026-05-19
last_updated: 2026-05-19
---

# Cash Mode — Full Sim Handoff (SHIPPED)

> **Status: SHIPPED 2026-05-19.** All commits 1-7 landed; the doc
> sweep (commit 8) is this update. For the current technical
> reference — architecture, invariants, performance, limitations,
> follow-on opportunities — read
> [CASH_MODE_FULL_SIM.md](../technical/CASH_MODE_FULL_SIM.md).
> This handoff stays as the historical record of design choices
> and the iterative path the implementation took.

This doc supersedes `CASH_MODE_PATH_C_DESIGN.md` (which was
written before any of the lobby/sim work shipped and is now
historical). Read this for the design journey and per-commit
plan; read the technical doc above for the system as built.

> **Related:** the [chip ledger](CASH_MODE_CHIP_LEDGER_HANDOFF.md)
> instruments every chip creation / destruction in cash mode. Once
> full sim is running unattended sessions at scale, the audit
> endpoint is the canary for whether AI regen rates are inflating
> the economy faster than `cap_clamp` and `house_loan_settle` are
> pulling chips back. Check the audit panel after the first full
> day of background sim runs.

## What's shipped (the foundation)

The full sim builds on a stack of smaller increments:

| Layer | Status | What it gives us |
|---|---|---|
| Cash mode v1 | Shipped | Single-table sit/leave/topup |
| Sponsorship + paths A/B | Shipped | Sponsor loans, AI-personality lenders, real AI bankrolls |
| Lobby v1.5 | Shipped | Persistent `cash_tables`, idle pool, multi-table view, seat picker, roster shuffle on lobby read |
| Activity ticker | Shipped | In-memory `LobbyEvent` ring buffer surfaced as the lobby's activity feed |
| **Fake-sim lite** | **Shipped** | **Zero-sum chip movement between AIs at unseated tables, on each lobby read. Drives `big_win` / `big_loss` events. The honest-by-construction layer: fake chip moves persist; ratification happens on player sessions.** |

**Key insight from this evolution:** every increment was a
*surface*-level change before becoming a *behavior*-level change.
The ticker existed before fake-sim filled it. Fake-sim mutates
real state before full sim makes those mutations cardplay-driven.
The full sim follows the same pattern — it replaces the random
chip delta inside `roll_fake_hand` with actual hand outcomes,
without touching the event surface, ratification model, or chip
conservation invariants.

## Phase 0 spike — DONE (2026-05-19)

Spike script (`scripts/full_sim_spike.py`, gitignored per Phase 0
spec) ran 6 personalities with BB=100 and 100 BB starting stacks
inside the backend container. Three scenarios:

| Scenario | Hands/sec | ms/hand |
|---|---|---|
| Warm table (1000 hands, controllers reused) | 227 | 4.4 |
| Lobby burst (4 tables × 25 hands, cached) | 253 | 4.0 |
| Cold per hand (rebuild controllers each hand) | 2.1 | 477 |

- **Setup cost** is the dominant variable: ~460 ms per table,
  ~77 ms per `TieredBotController` (an `Assistant` gets
  constructed even though no LLM is used).
- **Strategy table** loads as a one-shot 30 ms.
- **Memory**: ~135 MB after imports, +25 MB across 1000 warm
  hands, finishes ~222 MB. Most growth is state-machine snapshot
  accumulation — `advance_state_pure` appends to a tuple that
  never gets pruned.

### What this locks for the design

1. **Burst-on-read is fine.** Doc's prior threshold was
   ≥50 hands/sec; warm path is ~227/sec. Bursting 25 hands × 4
   tables ≈ 400 ms total — comfortably under the 500 ms lobby-
   response budget. Per-table burst is ~100 ms.
2. **Controller cache is load-bearing, not optional.** Was Phase
   6 ("pure optimization"); is now Phase 2's prerequisite. Cold
   per-hand setup is 100× slower than warm; even one uncached
   refresh of 4 tables blows the budget. **Cache from day one.**
3. **No background daemon needed.** Read-driven cadence math
   holds. The world advances when someone is watching it.
4. **Memory pruning is a new phase.** The state-machine snapshot
   leak isn't a problem for player-visible sessions (one hand at
   a time, snapshot lives until leave) but IS a problem for
   long-running sim ticks. Either prune snapshots inside
   `play_one_hand` after the hand completes, or have the sim
   harness copy only the final state and drop the rest. New
   commit slotted as Phase 2.5 below.

## What "full sim" means after fake-sim lite

Three concrete changes over fake-sim:

1. **Replace `roll_fake_hand` with `play_one_hand`.** Same input
   (table seats + big blind), same output shape (new_seats with
   winner/loser chip mutations + event metadata). Internals change
   from a random uniform pot to an actual hand: shuffled deck,
   dealt hole cards, betting rounds, board run-out, showdown.
   Pot size emerges from betting rather than `rng.randint`.

2. **Use `TieredBotController` for the cardplay.** No LLM —
   solver-table-driven decisions. Estimate: ~50–200 hands/sec on
   a single core with all bookkeeping. (See "Open questions" for
   the spike we want before locking this in.)

3. **Hand-level events join the surface.** Not just big_win /
   big_loss — now we can surface drama like all-ins, river
   suckouts, dominated showdowns. Same `LobbyEvent` shape, new
   `type` values.

The activity ticker, chip conservation, ratification model,
movement helpers, idle pool, and roster shuffle all keep working
unchanged. Full sim is the cardplay engine; everything else is
already in place.

## Architecture

### Hand-simulation entry point

```python
# cash_mode/full_sim.py — replaces cash_mode/fake_sim.py at the call site

@dataclass(frozen=True)
class HandSimResult:
    """Same shape as FakeHandResult, plus hand-level event details."""
    new_seats: List[dict]
    winner_pid: Optional[str]
    loser_pid: Optional[str]
    delta: int                    # net chip change for the headline pair
    big_event: bool
    # Extras only present when full sim runs:
    hand_events: List[HandEvent]  # e.g., ALL_IN, RIVER_SUCKOUT, DOMINATED
    pot: int
    showdown_hands: Optional[List[ShowdownHand]]

def play_one_hand(
    seats: List[dict],
    *,
    big_blind: int,
    rng: random.Random,
) -> HandSimResult:
    """Run a single AI-only hand. No LLM, no SocketIO, no DB writes."""
```

### Cadence — read-driven, not daemon-driven

Same model as fake-sim and the v1.5 movement: hands run inside
`refresh_unseated_tables`, which is called from `GET /api/cash/lobby`.
**Why no background daemon?** Two reasons:

1. **Cheap when no one's looking.** If no player has the lobby
   open, no compute happens. Background daemons burn cycles even
   on empty servers.

2. **Already a polling client.** The frontend already polls the
   lobby every 8s. That's the natural tick. The world advances
   when someone is watching it.

The cost is realism: tables don't tick if no client is connected.
If that bothers you long-term, add a low-frequency server-side
timer (every 5 min?) that fires the same `refresh_unseated_tables`
call. v1 of full sim doesn't need it.

### Catch-up on long absence

If the player closes the tab for an hour and returns, the
read-driven cadence ticks only on their return — they see one
hand of movement, not 50. Two options:

- **A: Burst-tick on return.** First lobby read after a long gap
  runs N hands (proportional to the gap, capped at maybe 30
  hands per table). Generates a flurry of events as the world
  "catches up."

- **B: Statistical advance.** For gaps > some threshold (10
  minutes?), skip card-by-card sim and apply distributional chip
  drift instead. Cheap; not "honest by construction" but the
  player wasn't watching.

**Recommendation: A, with a per-call cap.** Hand simulation is
fast enough that bursting 30 hands per table on first read is a
~1-second wait, which is reasonable. Cap the catch-up to avoid
absurd compute on multi-hour gaps. Bonus: bursting *real* hands
keeps the chip distributions honest where statistical advance
would drift them.

### Event surface — additive

Existing event types (`join`, `leave`, `big_win`, `big_loss`)
keep working unchanged. Full sim adds:

- `all_in` — "Napoleon shoves with KK at $50"
- `suckout` — "Bezos rivers a flush vs Napoleon's set at $200"
- `bust` — "Trump busts out of $50" (when AI's stack hits 0
  during a hand, not just between)
- `nice_pot` — "Lincoln scoops a $1,400 pot at $50" (alternative
  framing of big_win; or merge into big_win)

Apply the same threshold gate from fake-sim: small pots tick chips
quietly without ticker spam.

### Psychology at unseated tables — IN SCOPE (decided 2026-05-19)

AIs running real hands at unseated tables develop emotional state
— tilted after a bad beat, confident after a hot run. Discarding
that state at the end of each sim tick would mean:

- The lobby's `emotion` field on unseated AI seats would stay
  forever "confident" (the default planted when avatars shipped).
  Emotion-tinted card borders would never light up from sim
  activity.
- AI behavior at unseated tables would be psychology-less — they
  play tighter when tilted IRL, but sim AIs would always play
  baseline. Real chip movements without the emotional dynamics
  that drive interesting outcomes.
- The relationship layer's player↔AI persistence (heat / respect
  / likability) wouldn't extend symmetrically to AI↔AI when
  Backing Phase 4 (AI borrowers) ships.

**The decision: persist emotional state across sim ticks.**
Folded into the main scope (was Phase 6 optional). Concrete
implementation:

**Persistence layer.** New column on `ai_bankroll_state` (schema
v95 or whatever's next): `emotional_state_json TEXT NULL`. Mirrors
the existing `controller_state.psychology_json` precedent
(schema v83 — unified PlayerPsychology serialization). NULL means
"no state — treat as fresh confident."

**Cache + persist discipline.** The controller cache (Phase 1)
holds live controller instances; emotional state lives on the
controller object. Discipline:

- **Cache hit**: use the in-memory controller's state directly.
- **Cache miss**: hydrate controller from `ai_bankroll_state.emotional_state_json`
  (or fresh if NULL). Add to cache.
- **Cache eviction (LRU)**: serialize state back to
  `emotional_state_json` before evicting.
- **After every N sim hands per AI** (say N=10): periodic flush
  to avoid losing state on restart between eviction events.

This keeps the in-memory path fast (no DB writes on hot path)
while surviving backend restarts and cache evictions.

**Lobby route.** `/api/cash/lobby` already returns `emotion` per
AI seat. Today the resolver falls back to "confident" for AIs at
unseated tables. After this work: resolver reads
`emotional_state_json` (deserialize, project, return current
state's display name). Cache the result inside the request to
avoid N DB queries per render.

**Open sub-question (still unresolved):** does psychology decay
over wall-clock time at unseated tables, or only during sim hands?
Tilt from a bad beat at midnight shouldn't still be in effect at
noon. The existing `project_heat` pattern from the relationship
layer is the obvious template; apply similar projection-on-read
to emotional state so stale tilt fades naturally. Defer to
implementation — agent picks a starting decay constant from
existing psychology code.

## Phase / commit breakdown (~8 commits, post-spike + scope adds)

Updated order: controller cache moved earlier (load-bearing per
spike), snapshot pruning added as Phase 2.5, emotional state
persistence folded into main scope (was optional Phase 6), and
dealer rotation tracking added to Commit 2 + lobby UI in Commit 5
per 2026-05-19 design discussion. Phase 0 spike is DONE — see
"Phase 0 spike — DONE" section above.

Estimated effort: ~6-7 days total (was 5 pre-scope-add).

**Commit 1: `cash_mode/full_sim.py` skeleton + controller cache** — SHIPPED (`9bcd0beb`)
- `HandSimResult` dataclass (mirroring `FakeHandResult` shape +
  extras).
- `play_one_hand(seats, big_blind, rng)` — initial implementation
  that just delegates to `roll_fake_hand`. Lets us swap the call
  site once and iterate the implementation.
- **Controller cache lands here, not later.** New module
  `cash_mode/controller_cache.py` with an LRU keyed by
  `personality_id` (bound ~50 entries). Even though Phase 1
  doesn't use real controllers yet, putting the cache in first
  means Phase 2's hand-engine integration plugs into a working
  cache from its first commit — no risk of forgetting it and
  blowing the latency budget on the first burst.
- Tests: parity with `roll_fake_hand` outputs; cache eviction
  works; cache hit returns the same controller instance.

**Commit 2: Real hand engine integration + dealer rotation** — SHIPPED (`9bcd0beb` engine, `8b63e3c1` dealer sync, `a33a137d` schema v96)
- `play_one_hand` now constructs a minimal `GameState`, seats
  the AIs at it with their persisted chip counts, runs the hand
  engine until showdown, captures the result.
- AIs come from the controller cache (Commit 1). New
  controllers cost ~77 ms each per the spike; cache hits are
  effectively free.
- Returns the actual pot size, winner/loser, mutated seats,
  plus the **updated dealer_idx** (rotated to the next occupied
  seat clockwise per standard poker convention).
- **Schema**: add `dealer_idx INTEGER NOT NULL DEFAULT 0` to
  `cash_tables`. Required because `play_one_hand` needs the
  dealer position to assign SB/BB and determine betting order;
  the value persists between sim ticks so the rotation reads
  honestly across reads.
- `refresh_unseated_tables` writes the rotated `dealer_idx`
  back via `cash_table_repo.save_table` along with the seats.
- **No SocketIO emits**, **no DB writes** beyond the
  caller-driven `cash_table_repo.save_table`. The sim is
  pure-ish.
- Tests: end-to-end hand runs; chip conservation; no leaked
  side effects; dealer rotates correctly across N hands
  including skips when a seat is open.

**Commit 2.5: Snapshot pruning in `play_one_hand`** — SHIPPED via different mechanism (`baf7f0e6` adds `record_snapshots=False` flag to `ImmutableStateMachine`; `play_one_hand` uses it; `d2df222c` adds the tracemalloc test pinning < 5 MB heap growth over 1000 hands)
- Spike found `advance_state_pure` appends to a snapshots tuple
  that never gets pruned (~25 MB / 1000 hands). For player-
  visible sessions this never hits a wall — one hand at a time,
  snapshot dropped on leave. For background sim ticking
  indefinitely, it leaks.
- Two paths: (a) prune snapshots inside `play_one_hand` after
  the hand resolves (snapshot only used during the hand, not
  needed for the sim's output) or (b) have the sim harness
  construct a fresh `GameState` per hand and never accumulate.
- (a) is preferred — fewer allocations per hand.
- Tests: memory profile across 1000 hands stays flat (use
  `tracemalloc` snapshot diff; tolerance ±5 MB).

**Commit 3: Schema for emotional state persistence + cache discipline** — SHIPPED (`dabae3f0` schema v97, `5fc1a10a` cache hydrate/flush). On-evict flush deliberately deferred — see CASH_MODE_FULL_SIM.md "Opportunities."
- New migration: add `emotional_state_json TEXT NULL` to
  `ai_bankroll_state`. Mirrors v83's `controller_state.psychology_json`
  precedent.
- Controller cache (Commit 1) gains hydrate-on-miss / serialize-
  on-evict: cache miss reads `emotional_state_json` and
  reconstructs the controller's state from it; LRU eviction
  writes the current state back before dropping the controller.
- Periodic flush helper: every N=10 sim hands per AI, flush state
  back to DB even without eviction (guards against restart loss
  between LRU events).
- Tests: hydrate → mutate → evict → re-hydrate round trip
  preserves state; flush cadence works; NULL column treats AI
  as fresh-confident.

**Commit 4: Swap fake-sim for full sim at the call site** — SHIPPED (`9bcd0beb`). Kwarg renamed `fake_hand_prob` → `hand_sim_prob`; `_emit_fake_sim_events` → `_emit_sim_events`.
- `cash_mode/lobby.py:refresh_unseated_tables` imports
  `play_one_hand` instead of `roll_fake_hand`. The function
  signatures match by construction.
- Same probability gate (`fake_hand_prob` → rename to
  `hand_sim_prob`?), same per-table flow.
- Existing big_win / big_loss event emission keeps working.
- With Commit 3 in place, sim hands now mutate persisted
  emotional state — tilted AIs stay tilted across ticks.
- Tests: lobby refresh end-to-end now runs real hands; an AI
  taking a bad beat ends the refresh with non-confident state
  persisted.

**Commit 5: Lobby emotion + dealer indicators** — SHIPPED (`d2df222c` emotion resolver, `8b63e3c1` dealer sync, `ddeafaec` frontend UI badge). Wall-clock decay-on-read deliberately deferred — see CASH_MODE_FULL_SIM.md "Opportunities."
- `/api/cash/lobby` response:
  - Emotion resolver: for AIs at unseated tables, read
    `emotional_state_json` instead of defaulting to "confident".
    Apply projection-on-read decay (project_heat-style — pick a
    starting decay constant from existing psychology code, say
    "tilt half-life of 30 minutes").
  - Add `dealer_idx` to the table-level payload so the
    frontend knows which seat to mark.
- Cache resolution inside the request to avoid N DB queries
  per render.
- Frontend `<TableCard>`:
  - Emotion-tinted ring CSS already handles all emotion strings
    — no work needed on that surface.
  - **Add small dealer indicator** on the seat at `dealer_idx`
    ("D" badge, ~14px circle in a corner of the seat tile).
    Bonus: SB/BB indicators on the next two occupied seats
    clockwise. Keep it subtle on the lobby card; the in-game
    table already has its own dealer button treatment.
- Tests: lobby response reflects persisted emotional state per
  AI; decay applies between two reads N minutes apart; dealer
  index round-trips correctly; UI dealer badge renders on the
  expected seat.

**Commit 6: Hand-level events** — SHIPPED (`9bcd0beb`). BUST + ALL_IN detected; SUCKOUT deferred (needs per-street equity tracking — see CASH_MODE_FULL_SIM.md "Opportunities").
- After `play_one_hand` returns, inspect `hand_events` and emit
  corresponding `LobbyEvent`s.
- New event types in `cash_mode/activity.py`: `all_in`,
  `suckout`, `bust`, etc.
- Per locked Q6 decision: per-burst per-table cap (≤1 of each
  notable event type per table per burst) + optional
  `burst_summary` event for compressed activity.
- Frontend handles the new event types with appropriate styling.

**Commit 7: Catch-up burst on long-gap reads** — SHIPPED (`9bcd0beb`). `hand_burst_count` reads `last_activity_at` directly; no new schema needed.
- Track `last_refresh_at` per table in `cash_tables`.
- If a refresh happens > N seconds after last refresh, burst-tick
  multiple hands (capped at 30 per table) instead of just one.
- Per spike: 25 hands × 4 tables ≈ 400 ms — fits 500 ms budget.
- Tests: gap > threshold triggers burst; cap respected; emotional
  state persists across burst boundaries.

**Commit 8: Docs sweep** — SHIPPED (this commit). New
[CASH_MODE_FULL_SIM.md](../technical/CASH_MODE_FULL_SIM.md) is
the canonical technical reference going forward.
- Mark this handoff shipped.
- Update `CASH_MODE_AND_RELATIONSHIPS.md` Part 2 §"AI table
  selection" to reflect the actual cadence.
- Cross-link from `CASH_MODE_BACKING_SYSTEM_HANDOFF.md` (AI
  defaults now happen during sim, not just during player
  sessions).

## Open questions

1. **Hand simulation speed.** The spike answers this. If hands
   are ≥50/sec, burst-on-read is fine. If <10/sec, we may need
   a background worker after all (or settle for statistical
   advance on long gaps).

2. **Where does the sim run?** ~~In the Flask request thread~~
   **Answered by spike.** Request thread. 4-table × 25-hand
   burst = ~400 ms, fits the 500 ms budget. Background worker
   only becomes necessary if we ship Phase 6 (psychology at
   unseated tables) with per-hand emotional state updates that
   drive the per-hand cost up materially.

3. ~~**Does the player see live sim while at a table?**~~
   **Decided 2026-05-19: lobby only.** No SocketIO emit to the
   in-game client for off-table sim events. The in-game
   experience stays focused on the current hand; off-table
   activity is something you check by going back to the lobby.
   Keeps the implementation simpler (no in-game toast UI) and
   the in-table UX clean.

4. **Hand history persistence at unseated tables.** Real hands
   produce hand-history events that today persist to
   `hand_history` and `personality_snapshots`. Should sim hands
   also persist? Pro: full audit trail, "what hands did Napoleon
   play while I was away" replay. Con: 10× the DB write volume.
   Recommend: skip persistence for sim hands in v1, add later if
   replay UI ships.

5. **Memory / opponent model updates.** Real hands update each
   AI's memory of opponents. Should sim hands do the same? If yes,
   AI psychology builds opponent models even when no player is
   present, which makes the "Napoleon plays sharper against Bezos
   after he stiffed him" dynamic *real*. If no, the AI economy
   is more lobotomized than the player-vs-AI one. Recommend yes
   for memory updates but skip the heavy snapshot persistence.

6. ~~**Headline event selection.**~~ **Decided 2026-05-19:
   select-or-summarize, specifics open.** A burst that fires
   25 hands per table × 4 tables can emit 100+ events; the
   ticker shows 5. Some compression is needed; the exact shape
   is left to the implementer. Two viable approaches:

   - **Per-burst per-table cap** (simpler): emit at most 1
     `big_win` + 1 `big_loss` + 1 `bust` + 1 `all_in` per table
     per burst, even if 25 hands fire. Plus 1 optional summary
     event ("...and 4 more hands at $50") if anything was
     compressed. ~3 lines of logic in `_emit_fake_sim_events`'s
     successor.
   - **Type-weighted slot retention** (more complex): cap the
     visible ticker to N slots, but reserve at least 1 slot
     per event type so a burst can't bury all join/leave/etc.
     Implemented at ring-buffer read time, not write time.

   Recommended: per-burst per-table cap. Cheap, predictable,
   does not require ticker-side awareness of burst events.
   Land a summary event format like:
   `{type: 'burst_summary', table_id, message: '4 more hands
   at $50 — Napoleon +$220 net'}`.

## Risks to flag before starting

- ~~**Performance unknown.**~~ **Resolved by spike.** 227
  hands/sec warm; 400 ms per 4-table burst; comfortably under
  budget.

- **Test combinatorics.** Real hands have many possible outcomes
  (showdown winners, suckouts, all-ins). Pure-function tests
  should pin a seed and assert specific chip movements rather
  than try to enumerate the hand-engine surface.

- **Determinism in tests.** Hand outcomes depend on deck shuffle.
  Pin RNG. Use the same `random.Random(seed)` pattern existing
  cash-mode tests use.

- **AI controller side effects.** Live game controllers do things
  like emit SocketIO events, write to memory managers, write to
  prompt_captures. Sim controllers MUST be configured to skip
  all of that. There's likely a "headless" mode to find or build.

- **Interaction with active player sessions.** What if the player
  is *at* a table, sim runs at the OTHER tables, and a big-win
  event fires during a betting round? The frontend should
  surface it without disrupting the live hand — maybe as a small
  toast / activity bubble rather than a full modal.

- **Memory leak in `advance_state_pure`.** Spike caught this.
  Phase 2.5 (snapshot pruning) addresses it. Without that
  commit, sim ticks would leak ~25 MB per 1000 hands — survivable
  in a dev session, problematic in long-running deployments.

## Suggested ship order across the three v2+ tracks (revised post-spike)

```
Lobby v1.5      ✓ shipped
Activity ticker ✓ shipped
Fake-sim lite   ✓ shipped
Chip ledger v0  ✓ shipped
Full Sim Phase 0 spike  ✓ done (this doc)
---
Full Sim Commits 1-8     → real cardplay at unseated tables
                           with persistent psychology + dealer
                           rotation + lobby visual updates
                           (~6-7 days)
Backing Phase 1          → persistent loans foundation     (~2 days)
Backing Phase 3          → tab UI                          (~1 day)
Backing Phase 2          → reputation enforcement
Backing Phase 4          → AI as borrowers (hooks into real hands)
```

**Why Full Sim moved ahead of Backing 1+3:** the spike de-risked
the biggest unknown — hand simulation is 227 hands/sec warm,
well above the 50/sec threshold the doc had set as the
"go vs background-worker" line. The ratification model from
fake-sim carries forward unchanged; the ledger (just shipped)
will track real hand outcomes from day one, giving more useful
tuning data than fake-sim drift. The user specifically called
out wanting "events from other tables while I'm in the lobby" —
full sim delivers that with real cardplay.

Backing Phase 4 (AI borrowers) still lands after Full Sim
because the loan-take trigger genuinely needs AIs losing at
unseated tables to be meaningful. With fake-sim drift, AI
losses are uniform random; with real hands, they correlate
with bankroll, psychology, and opponent — which is the texture
that makes "Napoleon needs a loan" feel like a real economic
event.

Alternative: ship Backing Phase 1+3 first if persistent-debt
playtest value feels higher-leverage than world-feels-alive. The
two tracks are independent at the data layer (loans ride on the
existing `player_bankroll_state.active_loan_*` fields until
Backing Phase 1 replaces them with a `loans` table; full sim
operates on `cash_tables.seats_json` chips). Either order
works; the spike just made Full Sim cheaper to commit to.

## Files to read first

1. **This doc** — design above.
2. **`docs/plans/CASH_MODE_PATH_C_DESIGN.md`** — older / stale,
   but useful for the broader Path C context (rivalry seek,
   spectator mode, etc., that this handoff defers).
3. **`docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md`** — the
   parallel work; same ratification model.
4. **`cash_mode/fake_sim.py`** — the function full sim replaces.
   Match its interface and pure-function discipline.
5. **`cash_mode/lobby.py:refresh_unseated_tables`** — the call
   site that swaps.
6. **`cash_mode/activity.py`** — event types add cleanly here.
7. **`poker/tiered_bot_controller.py`** — the cardplay engine
   we'll use (no LLM).
8. **`experiments/run_ai_tournament.py`** — closest existing
   AI-only harness; reference pattern for the spike.
9. **`poker/poker_state_machine.py`** — entry points for hand
   advance; may need a `tick_silent()` variant that suppresses
   SocketIO and persistence side effects.

## What we deliberately defer (still future work)

- **Rivalry-seek seating.** AIs proactively moving to the player's
  table when heat is high. Listed in the older Path C doc;
  doable on top of full sim by adding a movement decision option,
  but not in this handoff's scope.
- **Spectator mode.** Watching a sim'd hand at an unseated table
  in real time. Cool but its own UI surface.
- **Multi-active-table per stake.** v1.5 ships one table per
  stake; the schema admits more without redesign but the seeding
  and lobby UX need passes.
- **Cross-process / cross-host sim.** Single-process only for
  v1; Redis-backed event bus or queue would land if we deploy
  multi-worker.

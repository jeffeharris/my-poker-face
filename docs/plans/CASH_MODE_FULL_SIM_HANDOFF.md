---
purpose: Implementation handoff for full background simulation — replace fake-sim chip drift with actual AI-only poker hands at unseated tables, surface hand-level drama to the lobby. Captures design rationale from the lobby v1.5 + fake-sim lite work, plus Phase 0 spike findings (2026-05-19).
type: guide
created: 2026-05-19
last_updated: 2026-05-19
---

# Cash Mode — Full Sim Handoff

This doc supersedes `CASH_MODE_PATH_C_DESIGN.md` (which was
written before any of the lobby/sim work shipped and is now
historical). Read this for the current implementation plan, the
older doc for the original conception.

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

### Psychology at unseated tables

This is the new question full sim introduces. AIs running real
hands have emotional state — tilted after a bad beat, confident
after a win. Currently, AI psychology lives on the controller
object created at game start; the controller is destroyed when
the player leaves.

Two choices:

- **A: Stateless sim.** Each tick, spin up fresh controllers,
  run one hand, throw them away. AIs have no emotional memory
  between sim ticks at unseated tables. Cheap and simple.

- **B: Persistent psychology per table.** Cash table state grows
  to include AI emotional state. The emotional state survives
  across sim ticks; an AI on a 3-bad-beat streak at $50 is
  visibly tilted when the player walks up.

**Recommendation: A for full sim v1.** Persistent psychology
adds a lot of state to track and serialize. Get the cardplay
working first; layer in emotional persistence later if it feels
needed. The lobby's `emotion` field can default to "confident"
for AIs at unseated tables (today's behavior).

## Phase / commit breakdown (~7 commits, post-spike)

Updated order: controller cache moved earlier (load-bearing per
spike), snapshot pruning added as Phase 2.5. Phase 0 spike is
DONE — see "Phase 0 spike — DONE" section above.

**Commit 1: `cash_mode/full_sim.py` skeleton + controller cache**
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

**Commit 2: Real hand engine integration**
- `play_one_hand` now constructs a minimal `GameState`, seats
  the AIs at it with their persisted chip counts, runs the hand
  engine until showdown, captures the result.
- AIs come from the controller cache (Commit 1). New
  controllers cost ~77 ms each per the spike; cache hits are
  effectively free.
- Returns the actual pot size, winner/loser, mutated seats.
- **No SocketIO emits**, **no DB writes** beyond the
  caller-driven `cash_table_repo.save_table`. The sim is
  pure-ish.
- Tests: end-to-end hand runs; chip conservation; no leaked
  side effects.

**Commit 2.5: Snapshot pruning in `play_one_hand`**
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

**Commit 3: Swap fake-sim for full sim at the call site**
- `cash_mode/lobby.py:refresh_unseated_tables` imports
  `play_one_hand` instead of `roll_fake_hand`. The function
  signatures match by construction.
- Same probability gate (`fake_hand_prob` → rename to
  `hand_sim_prob`?), same per-table flow.
- Existing big_win / big_loss event emission keeps working.
- Tests: lobby refresh end-to-end now runs real hands.

**Commit 4: Hand-level events**
- After `play_one_hand` returns, inspect `hand_events` and emit
  corresponding `LobbyEvent`s.
- New event types in `cash_mode/activity.py`: `all_in`,
  `suckout`, `bust`, etc.
- Threshold-gated to avoid spam.
- Frontend handles the new event types with appropriate styling.

**Commit 5: Catch-up burst on long-gap reads**
- Track `last_refresh_at` per table in `cash_tables`.
- If a refresh happens > N seconds after last refresh, burst-tick
  multiple hands (capped at 30 per table) instead of just one.
- Per spike: 25 hands × 4 tables ≈ 400 ms — fits 500 ms budget.
- Tests: gap > threshold triggers burst; cap respected.

**Commit 6: AI psychology at unseated tables (optional, v2 of full sim)**
- Persist emotional state per AI per table (or per AI globally?).
- Surface in the lobby's `emotion` field per seat.
- This is where the table's emotion-tinted borders start lighting
  up from ambient world activity, not just live games.

**Commit 7: Docs sweep**
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

3. **Does the player see live sim while at a table?** Today, the
   player's own table doesn't run sim ticks (the hand-boundary
   refresh hook handles it). The other tables tick during their
   browsing. Should the lobby show "Napoleon won big at $200"
   notifications while the player is at the $10 table? Probably
   yes — same socket events the in-table modal uses, fired from
   the sim. Adds a `LobbyEvent` socket emit on top of the read-
   surface mechanism.

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

6. **Headline event selection.** When the lobby ticker shows 5
   events, which 5? If sim generates 30 events per tick during a
   burst, the buffer fills with the burst's events and pushes
   out older join/leave events. May want type-weighted retention
   (keep at least 1 join, 1 leave, 1 big_win in the visible set).

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
Full Sim Commits 1-5     → real cardplay at unseated tables (~5 days)
Backing Phase 1          → persistent loans foundation     (~2 days)
Backing Phase 3          → tab UI                          (~1 day)
Backing Phase 2          → reputation enforcement
Backing Phase 4          → AI as borrowers (hooks into real hands)
Full Sim Commit 6        → AI psychology at unseated tables
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

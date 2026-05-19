---
purpose: Implementation handoff for full background simulation — replace fake-sim chip drift with actual AI-only poker hands at unseated tables, surface hand-level drama to the lobby. Captures design rationale from the lobby v1.5 + fake-sim lite work.
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

## Phase / commit breakdown (~8 commits)

**Phase 0: Spike (no commit, ~half a day)**
- 50-line script that runs 1000 AI-only hands of cash poker
  between 6 personalities using `TieredBotController` directly,
  no SocketIO, no DB writes. Measure: hands/sec, memory growth,
  controller setup cost.
- Locks the cadence + per-tick hand count decisions before
  committing to a design.
- Likely answers: "burst 20 hands per table per tick is fine,"
  "controller setup is the slow part — cache controllers per
  personality if we tick hands at the same table multiple times."

**Commit 1: `cash_mode/full_sim.py` skeleton**
- `HandSimResult` dataclass (mirroring `FakeHandResult` shape +
  extras).
- `play_one_hand(seats, big_blind, rng)` — initial implementation
  that just delegates to `roll_fake_hand`. Lets us swap the call
  site once and iterate the implementation.
- Tests: parity with `roll_fake_hand` outputs.

**Commit 2: Real hand engine integration**
- `play_one_hand` now constructs a minimal `GameState`, seats
  the AIs at it with their persisted chip counts, runs the hand
  engine until showdown, captures the result.
- AIs use `TieredBotController` instances. Cache by personality_id
  if the spike says it matters.
- Returns the actual pot size, winner/loser, mutated seats.
- **No SocketIO emits**, **no DB writes** beyond the
  caller-driven `cash_table_repo.save_table`. The sim is pure-ish.
- Tests: end-to-end hand runs; chip conservation; no leaked side
  effects.

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
- Generates the "world advanced while you were away" effect.
- Tests: gap > threshold triggers burst; cap respected.

**Commit 6: Controller cache**
- If the spike showed controller setup is hot, add a
  `TieredBotController` cache keyed by personality_id with bounded
  size (LRU, max ~50 entries).
- Pure optimization — no behavior change.

**Commit 7: AI psychology at unseated tables (optional, v2 of full sim)**
- Persist emotional state per AI per table (or per AI globally?).
- Surface in the lobby's `emotion` field per seat.
- This is where the table's emotion-tinted borders start lighting
  up from ambient world activity, not just live games.

**Commit 8: Docs sweep**
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

2. **Where does the sim run?** In the Flask request thread (today's
   pattern, blocks the lobby response for a few hundred ms during
   burst) or in a background thread (responds fast, sim catches
   up async)? Lean toward request-thread for v1 simplicity. If a
   burst takes >500ms in playtest, move to background.

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

- **Performance unknown.** Hand engine + controller setup × many
  hands per tick × 4 unseated tables = the lobby response time
  budget is real. The Phase 0 spike is non-negotiable.

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

## Why ship this after backing system Phase 1+3

Sequencing matters:

- Full sim makes AI-vs-AI hands real → which makes AI bankrolls
  shift independently of player sessions → which makes the
  backing system's "Napoleon needs a loan because he's been losing"
  story *true*.
- Backing system Phase 1 establishes the persistent loan
  substrate that AI-vs-AI hands feed.
- Phase 3 (tab UI) gives the player a window into the economy
  that sim makes dynamic.

If full sim ships *before* backing Phase 1, you get richer chip
movement but it doesn't compound with debt/reputation. If it
ships *after*, you get a genuinely living economy where AIs play,
borrow, lose, default, refuse — all visible, all consistent.

## Suggested ship order across the three v2+ tracks

```
Lobby v1.5      ✓ shipped
Activity ticker ✓ shipped
Fake-sim lite   ✓ shipped
---
Backing Phase 1 → persistent loans foundation
Backing Phase 3 → tab UI
Full sim phases 0–6 → real cardplay at unseated tables
Backing Phase 2 → reputation enforcement
Backing Phase 4 → AI as borrowers (now hooks into real hands)
Full sim phase 7 → AI psychology at unseated tables
```

That sequence keeps each step coherent. Backing Phase 1+3 first
because tab visibility before consequences. Full sim before
Backing Phase 4 because AI borrowers depend on AIs running real
hands (their "I need a loan" trigger comes from actual losses,
not fake-sim drift). Backing Phase 4 then enables AI psychology
at unseated tables to read meaningfully (Phase 7).

Alternative: full sim phases 0–3 (the spike + cardplay) before
Backing Phase 1, so the backing system gets built on real chip
dynamics from day one. Defensible — Backing Phase 1 isn't *broken*
on fake-sim data, just less interesting. Pick based on which
feels more important to playtest first.

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

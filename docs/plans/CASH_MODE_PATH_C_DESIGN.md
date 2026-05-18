---
purpose: Design notes for cash mode Path C — multi-table lobby, AI-only background simulation when the player leaves a table, rivalry-driven seating. Pre-handoff; captures intent + open questions before scope is locked.
type: design
created: 2026-05-18
last_updated: 2026-05-18
---

# Cash Mode — Path C Design: Multi-Table Lobby + Background Sim

This is the **biggest architectural step** for cash mode. v1 is
single-table per owner; the relationship layer is wired but its
seating payoff (rivalry-seek) needs multiple tables to mean
anything. Path C ships the lobby, the AI-only background sim, and
the rivalry-driven movement logic.

**Status:** design phase. This doc is not yet a handoff — open
questions in §"Decisions needed" must be resolved first. Once those
are settled, the doc gets restructured into a commit-by-commit
handoff like Paths A and B.

## Vision

The player walks into a virtual cardroom. They see a list (or
visual representation) of tables, each with a stake label and a
list of who's sitting. They see Napoleon at the $50 table — they
remember they took $2k off him last week. They sit down to face
him. Hands fly. Napoleon busts; he stands up, the seat refills with
Bezos.

The player leaves the $50 table to go grind at $10 for a bit. The
$50 table **keeps playing**. When the player returns to the lobby,
Napoleon is back (he regenned over the last hour), now at the $200
table. The player can chase him there or wait. The world is alive.

## Three pieces to design

1. **Multi-table state model** — how tables exist, who's at them,
   when AI-only tables run hands.
2. **Background sim** — actually running hands without the player.
   Tiered bot, no LLM, no chat, bounded compute.
3. **AI movement** — when and why AIs leave tables, change stakes,
   sit elsewhere.

Plus the cross-cutting concern: **time**. How does it pass? When
do we sim? What does the lobby show?

## 1. Multi-table state model

### Tables as first-class objects

Already an invariant declared in `CASH_MODE_AND_RELATIONSHIPS.md`
§"v1 architectural invariants": *Tables are first-class objects
with their own state.* Path C makes this concrete:

- New schema: `cash_tables` table — one row per active table.
  Columns: `table_id`, `stake_label`, `seat_count`, `seats_json`
  (array of `{personality_id, stack, seated_at}`), `last_hand_at`,
  `hand_count`, `state` (`active`, `recently_finished`, `closed`).
- Tables persist; they're not in-memory only like cash sessions
  were. The lobby needs to show the same tables across player
  logins.
- A "session" (the player sitting at one) is a temporary subscription
  to one of these persisted tables — the player occupies a seat,
  the table's `seats_json` reflects it, and the player's owner_id
  is associated with the table for routing.

### Lobby shape (v1)

| Stake | Active tables | Sample tables to maintain |
|---|---|---|
| $2    | 2-3 | always running, lowest stakes |
| $10   | 2-3 | most active tier |
| $50   | 1-2 | named AIs preferred |
| $200  | 1   | celebrity table |
| $1000 | 0-1 | only when whales have the bankroll |

**Maintenance loop** (runs every N minutes): for each tier, ensure
the target number of active tables exists. If too few, spin one up
with eligible AIs. If too many (e.g., a table busted out below
quorum), close it.

### Open: do tables ever fully close?

Two models:
- **A: Persistent tables.** A $50 table at "seat 3" exists forever;
  AIs come and go but the table is durable. Lobby shows a stable
  fixture.
- **B: Ephemeral tables.** Tables spin up when AIs gather; close
  when below quorum (~2 active seats). Lobby shows a fluid list.

(B) feels more alive but adds noise. (A) is simpler — fixed grid,
seats turn over. **Decision needed.**

## 2. Background sim — running AI hands without the player

### The simplification

Once we strip the LLM out, a hand is **pure-Python state machine
work** running maybe 100ms per hand even with all the bookkeeping
(deck shuffle, betting rounds, evaluations, settlement). With
psychology and full prompts off, a tiered-bot controller picks an
action via solver lookup → faster still.

**Estimate:** ~50-200 hands/sec on a single core, depending on
psychology + bookkeeping overhead. Fast enough that a 30-minute
absence ≈ 100k hands of catch-up sim — which is far more than the
player would care about. So sim cadence becomes the actual cost
driver, not the hand engine.

### Existing infrastructure to lean on

- **`experiments/run_ai_tournament.py`** — already runs AI-only
  hands at scale. Tournament shape, not cash shape, but the
  dispatch + hand engine is identical.
- **`poker.tiered_bot_controller.TieredBotController`** — the
  no-LLM, solver-table-driven controller. Exactly what we want.
- **`poker.rule_bot_controller.RuleBotController`** — even simpler,
  pure rules. Use this for the lowest stakes (chaos noise) and
  tiered_bot for higher stakes (more believable play).

### Two flavors of sim

**Live sim — runs while the lobby is open.**
- Player is browsing /cash menu, viewing the lobby.
- Background tasks tick each active table once per N seconds (say,
  1 hand per 2 seconds) so the player sees "Napoleon at $50, just
  won pot of $400."
- Reads are cheap (lobby polls table state); writes are
  cheap (state transitions). The hand engine drops the LLM call —
  no API latency, no rate limits.

**Catch-up sim — fakes the elapsed time when the player returns.**
- Player leaves the lobby (closes tab, navigates away).
- When they return, the gap between "last lobby view" and "now"
  becomes a chunk of fictional hands.
- Two execution models for the gap:
  - **a) Actually sim it.** Run hands for N seconds at server-tick-rate
    starting from saved state. For a 1-hour gap at 1 hand / 2 sec,
    that's 1800 hands per table. At ~100ms/hand (with psychology
    + memory) = 3 minutes of wall clock per table. **Too slow at
    scale** for a single user re-loading.
  - **b) Statistically advance.** Don't simulate — apply
    distributions: AI bankrolls regen normally; AIs probabilistically
    bust or stand up based on session-length distributions; new
    AIs seat in. The lobby shows plausible state without
    re-deriving every hand.

  Probably (b) for the long gap, (a) for short ones. **Threshold
  decision needed** (e.g., < 60 seconds elapsed → run live tick
  forward; > 60 seconds → statistical advance).

### How time passes

Three options for the "what time is it at the table" question:

- **Real wall-clock.** Tables ran for the wall-clock interval since
  last lobby view. Simple; aligns with `project_bankroll`'s regen
  clock; but cards-in-hand have a wall-clock duration that's
  arbitrary.
- **Hands as the clock.** Each table advances some number of hands
  per minute; the player's time-in-lobby converts to hands. Decouples
  from wall-clock; but then bankroll regen and table-time diverge.
- **Hybrid.** Wall-clock for bankrolls (they regen on the real
  calendar), hands-per-minute for table activity (live sim ticks at
  a controlled rate). **Recommended.**

### Open: should the player be able to "spectate" a sim?

Spectator mode is a different UI surface. Cool, but scope creep.
Defer to a later doc. v1 of Path C: lobby shows summary state
(who's there, recent big hand), no live spectate.

## 3. AI movement — when AIs leave tables and where they go

### Triggers for AI to stand up

In single-table v1, AIs are bust-only. Path C needs to expand:

- **Bust** — stack=0, replaced by `_refill_cash_seats` (existing).
- **Stop-loss** — `stop_loss_buy_ins × buy_in` lost in this session.
  Already in `BankrollKnobs`, just need to consume it.
- **Stop-win** — `stop_win_buy_ins × buy_in` won. Same.
- **Stake-comfort drift** — AI's bankroll grew enough that
  `bankroll > comfort_zone_threshold × N`; they shop up to a
  higher table.
- **Rivalry seek** — player sat at table X; rival-AIs (high heat
  toward this player) get a probabilistic chance to move to X.
- **Boredom / time** — AI has been at the same table for K hands;
  small chance to relocate.

### When AI sits at a new table

After standing up:
1. Compute their projected bankroll.
2. Rank stakes by: (a) within affordability, (b) within comfort
   zone, (c) rivalry-seek bias toward player's table.
3. Pick by weighted random.
4. Open a seat at the chosen table (or wait for one).

### Distributions for movement

This is the **central design knob**. If every AI moves too often,
the lobby is chaos. Too rarely, it's static. Some starting numbers:

| Event | Probability/Frequency |
|---|---|
| Stop-loss trigger | ~3 buy-ins lost (per knobs) |
| Stop-win trigger  | ~5 buy-ins won (per knobs) |
| Stake drift up    | 2-5% per hand once bankroll affords next tier |
| Rivalry seek      | When player sits, 10-30% of high-heat AIs relocate within ~5 min |
| Boredom move      | 0.5% per hand at same table |

Calibrate from playtest. Should feel like "the world moves at a
human pace" — you see *something* changing every minute or two but
not constantly.

## Connection to Paths A and B

- **Path A unlocks this.** AI bankrolls need to track winnings
  (Path A) for stake-drift-up and stop-win to work.
- **Path B's lender events** ride atop this. A busted AI at table
  X can take a loan from another AI at table Y (AI-to-AI lending,
  v2 of Path B). The relationship event fires when the loan is
  granted; if the borrowing AI defaults, the lender's `respect`
  toward them drops, affecting AI-to-AI dynamics.
- **AIs as borrowers** (the user mentioned this as a follow-up).
  An AI at full bust could take a sponsorship loan from another AI
  (or the house). Their bankroll regen comes via loan repayment
  + winnings instead of pure daily trickle. **Open: do AI loans
  ride the same `active_loan_*` fields the player has, or a
  separate AI-debts table?**

## Decisions needed before turning this into a handoff

1. **Persistent vs ephemeral tables** (§1).
2. **Catch-up sim threshold** — wall-clock seconds at which we
   stop live-ticking and switch to statistical advance (§2).
3. **Hands-per-minute rate for the live sim** (§2). Affects how
   "alive" the lobby feels.
4. **AI movement distribution tuning** (§3) — calibrate after
   first playtest.
5. **Spectator mode** — in scope or not (§2).
6. **AI debt model** — does AI-as-borrower reuse the player loan
   columns or get its own?

## What we already know works

- AI bankroll projection (`project_bankroll`) is real-time
  by design — bankrolls regen on wall-clock with no background
  job. Good for catch-up.
- Tiered bot doesn't need warmup; can run hands immediately.
- `cash_pair_stats` table (schema v87) already tracks player ↔ AI
  P&L; would extend to AI ↔ AI if we want AI memory of AI rivalries.

## What we know doesn't work yet

- Concurrent tables in `game_state_service` aren't tested. The
  service can hold multiple games but the cash-mode flow assumed
  one per owner.
- The hand engine's state machine has no "tick this table forward
  one hand" entry point that bypasses the SocketIO emit path.
  Background sim needs an emit-suppressed run.
- Persistence of cash table state across restarts isn't designed
  (v1 explicitly says in-memory only).

## Suggested next step

Run a **spike**: build a 50-line script that runs 1000 hands of
AI-only cash poker between 6 personalities using `TieredBotController`,
no LLM, no SocketIO. Measure: hands/sec, memory growth, where state
flows. That spike tells us whether (a) catching up 1 hour of sim
is feasible vs (b) statistical advance is required.

After the spike, the open questions above narrow significantly,
and this doc gets restructured into a commit-by-commit handoff.

## Files that will matter

- **`experiments/run_ai_tournament.py`** — closest existing AI-only
  harness; pattern source for the spike.
- **`poker/tiered_bot_controller.py`** — the no-LLM controller.
- **`poker/poker_state_machine.py`** — entry points for hand
  advance; may need a `tick_silent()` variant that suppresses
  SocketIO.
- **`flask_app/services/game_state_service.py`** — concurrency model
  for many tables, table TTL.
- **`cash_mode/`** — new modules: `tables.py` (persistent table
  state), `lobby.py` (table-list maintenance), `sim.py` (the
  background tick loop).

## Related docs

- `CASH_MODE_AND_RELATIONSHIPS.md` — Part 2 §"AI table selection
  — deferred to v2" + §"v1 architectural invariants" for the
  upstream design.
- `CASH_MODE_PATH_A_HANDOFF.md` — must ship first.
- `CASH_MODE_PATH_B_HANDOFF.md` — sponsorship hooks that Path C
  extends to AI-to-AI lending.

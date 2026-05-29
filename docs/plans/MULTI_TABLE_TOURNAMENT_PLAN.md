---
purpose: Vision, architecture, and phased plan for multi-table (WSOP-style) tournaments, starting with a headless multi-table engine
type: design
created: 2026-05-29
last_updated: 2026-05-29
---

# Multi-Table Tournaments — Plan

## TL;DR

Build a **multi-table tournament (MTT) capability**, WSOP-style, where a field of
players spread across several tables thins out — tables balance and break, players
get moved, and the field consolidates to a final table and a winner.

We already have ~80% of the rails (single-table engine, sandbox isolation,
`TournamentTracker`, the world ticker, staking, prestige). The missing piece is an
**orchestration layer above the per-table game loop**.

**Phase 1 (this plan's primary scope):** a correct, fast, fully-testable **headless
AI-vs-AI multi-table engine** — table balancing, table breaking, a synchronized blind
clock, global standings, final table. No economy, no circuit, no live human yet.
Lives in the **tournaments** area only.

Everything else (live human table, cross-table ticker drama, buy-ins/prizes/staking,
daily circuit, prestige/achievements) is explicitly **later phases** — designed for,
not built now.

---

## Vision (the full arc)

This is where it's all heading. Phase 1 is only the foundation.

1. **Multi-table tournaments** — expand single-table WTA/SNG into true MTTs. A big field,
   many tables, players moving as the crowd thins, a final table, a champion.
2. **The field feels alive** — the **activity ticker** carries cross-table updates to the
   player: "Table 6 just broke," "Chip leader: Doyle, 142k," "You're on the money bubble,"
   pay-jump alerts, knockouts. The interhand "Meanwhile…" surface narrates the rest of the
   field while you wait.
3. **Circuit / daily events** — once the engine is solid, tournaments plug into a circuit:
   a recurring daily tournament players can plan around.
4. **Economy** — players **buy in**, get **staked into** entries by AIs or other backers,
   and **win big**. Prize pools, payouts, the money bubble.
5. **Prestige & achievements** — tournament results feed **renown/regard** (deep runs,
   final tables, championships) and unlock **achievements** (first final table, champion,
   bubble survivor, bounty hunter, …).

This plan keeps that arc in view but **builds the engine first**, because every later
layer depends on a correct multi-table simulation underneath it.

---

## Phase 1 — Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| First cut | **Engine only** | Prove the hard part end-to-end before economy/UI |
| Build order | **Headless AI-only sim first**, then wire human in | Deterministic, testable; de-risks the orchestrator |
| Field size | **Mid: 18–45 entrants, 2–5 tables** (configurable) | Big enough to exercise balance + break; cheap to iterate |
| Field controllers | **Tiered bots — hard requirement: 0 LLM cost when no human is present** | Fast, free, fully simulatable; LLM personas come later |
| Economy | **None in v1** (funny-money chips, no buy-ins/payouts) | Decouple from the cash conservation ledger |
| Home | **Tournaments section only** — not circuit, not the cash sandbox | Keep blast radius small; circuit is a later thin layer |

**Implied chip model (when economy lands):** funny-money tournament chips. Bankroll pays
the buy-in, the player gets a flat starting stack of tournament-only chips, and only the
**payout** touches the bankroll again. v1 has no buy-in at all — every entrant just gets
the flat starting stack.

## Sandbox & data boundaries (v1 vs career integration)

A tournament crosses two independent ledgers, and they're treated differently:

| Ledger | v1 (standalone) | Career / circuit follow-on |
|---|---|---|
| **Social** (relationships, history, who-met-whom, prestige drivers) | **Ignored** — run the whole thing in a throwaway sandbox world; no external relationships to carry | **Carried in** (AIs remember you, rivalries + reputation come along) and **carried out** (relationship/history deltas flow back to the career sandbox) |
| **Chip** (stacks) | Isolated **funny money**; conservation invariant holds throughout | Still isolated **funny money** inside the tournament; only the **buy-in** (debit) and **payout** (credit) cross into the real bankroll |

**The key invariant for career mode: "real social context, fake chips."** A career-mode
tournament imports the player's relationship/history context so it *feels* connected to their
world, but plays on an isolated tournament-chip universe. The career lobby keeps displaying
the player's **actual net worth and bankroll the entire time** — the funny-money stack never
masquerades as real chips. The only two moments the real bankroll moves are buy-in (out) and
payout (in).

Why this matters: it keeps tournament chips entirely off the cash conservation ledger (the
historical source of ghost-chip bugs), while still letting tournaments feed the social systems
(prestige, achievements, rivalries) that make a career mode meaningful.

### The tournament as a ledger actor (economy phase)

When the economy lands, the tournament itself becomes a **ledger counterparty** in the *real*-chip
ledger — an escrow/house entity, much like the casino/bank pool is today. It **receives buy-ins**,
holds the **prize pool** in escrow, **pays out winnings** to finishers per the payout structure, and
**retains rake** (skimmed to the house/bank pool). This means **two conservation statements run at
once**:

- **In-tournament (funny money):** `sum(stacks) == field_size × starting_stack` — holds for the
  entire event; rake and the bank pool never touch in-game chips.
- **Real-chip ledger:** `sum(buy-ins collected) == sum(winnings paid) + rake retained` — the
  tournament escrow nets to zero against bankrolls + house pool.

The two ledgers meet at exactly two boundary conversions: **buy-in** (real chips → a funny-money
starting stack on entry) and **payout** (final placement → real chips on exit). Rake comes off the
real buy-in collection, never the in-game chips. Slots into the existing chip-ledger / bank-pool
infrastructure as a new counterparty.

**Open for the career phase (non-blocking now):** which in-tournament events generate
relationship/history deltas on carry-out (e.g. do funny-money beats/knockouts move
likability/respect/heat the way cash hands do?), and exactly which prestige components a deep
run / final table / championship feeds.

---

## What already exists (reuse map)

The single biggest finding from the codebase exploration: this is an **assembly job**, not
a from-scratch build. Per-table poker, isolation, elimination tracking, and the broadcast
bus all exist.

| Need | What exists today | What's missing |
|---|---|---|
| Per-table poker engine | `PokerStateMachine` (`poker/poker_state_machine.py`), immutable `PokerGameState` (`poker/poker_game.py`); `reset_game_state_for_new_hand` already drops 0-stack players | Run **N** of them under one director |
| Headless fast loop | `experiments/sng_runner.py::play_sng()` + `run_cc_hand()` — the cleanest single-table WTA driver, no Flask/socket | Generalize to multi-table |
| AI tournament runner | `experiments/run_ai_tournament.py::AITournamentRunner` — parallel runs, A/B, heartbeats | It's single-table-per-tournament; no balance/break/transfer |
| Blind escalation | `BlindConfig` on the state machine (growth / hands_per_level / max_blind), fires in `hand_over_transition` | Per-table + hand-count-driven today; needs a **shared clock** |
| Elimination → standings | `poker/tournament_tracker.py::TournamentTracker` — eliminations, finishing position, standings, `get_result()` | Per-`game_id` / per-table only; promote to **field-wide** |
| Isolated world | **Sandbox** (`sandbox_id`-scoped everything); `cash_tables` is `(table_id, sandbox_id)`-keyed with a `table_type` discriminator (`lobby`/`casino`/`private`) | Ephemeral sandbox per tournament; add `table_type='tournament'` when persistence is needed |
| Cross-table broadcast | World ticker (`flask_app/services/ticker_service.py`) + `cash_mode/activity.py` ring buffer + the `lobby:{owner_id}` room every socket joins | Add tournament event types (no plumbing change) |
| Buy-ins / staking | `stakes` system: `principal`/`cut`/`carry`, house+AI+player stakers, offer/accept flow (`cash_mode/player_staking.py`) | Bind a stake to a tournament entry |
| Prestige | `cash_mode/prestige.py::compute_prestige()` — cleanly extensible | Add a `renown_tournament` component |
| Achievements | **Spec'd, unbuilt** (`docs/plans/ACHIEVEMENTS_SYSTEM.md`), already has a `tournament` category | Build the engine (separate effort) |
| Result persistence | `tournament_results` / `tournament_standings` / `player_career_stats` (per `game_id`) | Add a tournament-level (multi-table) result schema |

---

## Architecture (Phase 1)

### Core abstraction: `TournamentDirector`

A new orchestrator that sits **above** the per-table game loop. It owns the tournament; each
table is an ordinary single-table `PokerStateMachine` it drives.

```
TournamentDirector
  ├── config: field_size, table_size, starting_stack, blind_structure
  ├── tables: list[TableHandle]   # each wraps one PokerStateMachine
  ├── tracker: TournamentField    # field-wide standings + chip counts
  ├── clock:  BlindClock          # shared level schedule
  └── seating: SeatingManager     # balance / break / final-table (pure)

  run() loop, one "round" at a time:
    1. for each table: play one hand (headless, via the sng_runner-style driver)
    2. collect eliminations from every table this round
    3. tracker.apply(eliminations)        # assign global finishing positions
    4. seating.rebalance(tables, tracker) # balance, break, form final table
    5. clock.advance(round_index)         # bump blind level on schedule
    6. stop when one player remains → standings
```

### The "round" primitive (pacing)

In a real WSOP MTT, blinds are **time-based**, so every table is on the same level
regardless of how many hands it has played. The headless analog is a **round**:
roughly one hand per table per round. The blind clock is a function of the round
counter, so all tables stay on the same level.

**Pacing when the live human table is wired in (decided):** AI tables move at *roughly the
same speed as the human's table*, so the field feels like it's happening **in time with
you**, not pre-computed or stalled. The mechanism: per round, each AI table plays **0, 1,
or 2 hands** (jittered) against the human's single hand. This keeps the field loosely
synchronized — fast/short tables can breathe ahead, slow ones lag a hand — without making
the human a strict bottleneck or letting AI tables sprint to the finish. A human hand is
the clock tick; the 0/1/2 allowance is the elasticity around it. (Headless-only runs have
no human, so every table simply plays one hand per round.)

### `TournamentField` (promote `TournamentTracker` to field-wide)

Today's `TournamentTracker` tracks one table's `_active_players` set and assigns
finishing position from *that table's* remaining count. The MTT version tracks the
**whole field**:

- which player sits at which table (seat assignments),
- global active count (across all tables),
- per-player chip counts,
- eliminations with a **global** finishing position (= total players remaining across all
  tables at the moment of the bust),
- a defined **tiebreak for simultaneous busts** in the same round: the player who *started
  the hand with more chips* finishes higher. (In sequential headless play this is naturally
  ordered, but we pin the rule so it's deterministic and matches the live path later.)

`get_result()` / `get_standings()` generalize directly.

### `SeatingManager` (the genuinely new logic)

Pure functions over `(seating, eliminations) → moves`. Three responsibilities, classic MTT
rules:

- **Balance**: keep table sizes within 1 of each other. When the gap exceeds 1, move the
  correct player (standard rule: the player who would next post the big blind) from the
  largest table to the smallest's open seat.
- **Break**: when the field can fit on fewer tables (`remaining ≤ (num_tables−1) × table_size`),
  break the **shortest** table and redistribute its players to open seats across the rest.
- **Final table**: when `remaining ≤ table_size`, consolidate everyone onto one table.

Kept pure and seat-model-agnostic so it's unit-testable in isolation and reusable by the live
path. **This is where the ghost-seat bug class will try to reappear** (see Risks) — every move
goes through a single atomic seat-transfer primitive with a conservation assertion.

### Blind structure

A declarative level schedule (level → small/big/ante + duration-in-rounds), configurable.
Reuses the spirit of `BlindConfig` but as a shared, level-indexed clock rather than a
per-table hand counter.

### What Phase 1 deliberately does NOT touch

- No Flask routes, no Socket.IO, no live human (Step 3 of the build order).
- No `cash_tables` persistence — headless tables are in-memory structures. (Persistence is
  added only when resume / the live human needs it.)
- No buy-ins, prize pools, payouts, or bankroll interaction.
- No ticker, prestige, achievements, or circuit wiring.

---

## Build order (Phase 1, sequenced)

1. **Headless engine core** — `TournamentDirector`, `TournamentField`, `SeatingManager`,
   `BlindClock`. Drive existing single-table state machines with rule/tiered bots. Pure,
   seeded-deterministic, no I/O. Unit tests for balance/break/final-table + standings;
   full-sim smoke tests at 18–45 entrants.
2. **Results + observability** — a multi-table result record (tournament-level, spanning
   tables) and standings. A CLI/sim entry point so tournaments can be run and inspected
   like the existing experiment runner. (Doubles as an MTT-scale **eval harness** — see
   Opportunities.)
3. **(Phase 2 boundary) Live human table** — swap one table's loop for the live
   `progress_game()`/socket path; relocate the human across tables as tables break; the live
   table paces the round clock. This is where the standalone tournament UI begins.

Steps 1–2 are the meat of this plan. Step 3 opens the next phase.

---

## Build status (2026-05-29)

Phase 1 Step 1 (headless engine core) is **scaffolded** in the `tournament/` package and
green:

- `seating.py` (`SeatingManager`: break / balance / final-table — pure), `field.py`
  (`TournamentField`: standings, finishing-position tiebreak, conservation), `blinds.py`
  (round-indexed clock), `config.py`, `director.py` (`TournamentDirector` + pluggable
  `HandResolver` + deterministic `FakeHandResolver`), `engine_resolver.py` (real engine,
  no-LLM tiered/rule bots), `run.py` (CLI).
- 23 unit tests in `tests/test_tournament/` (seating, standings, full-run, reproducibility);
  18- and 24-entrant tournaments run to a single winner on both the fake and real-engine
  resolvers with conservation asserted every round.

**The conservation invariant immediately earned its keep:** the engine path exposed a real,
pre-existing core bug in `poker/poker_game.py::determine_winner` — folded players' *dead
money* was stranded when only one live player remained eligible for a side tier (≈2% of
unequal-stack hands; invisible to existing evals because they use equal stacks and never
assert exact conservation). Fixed so the lone live player wins the dead money (only truly
uncalled excess returned), plus a post-loop safety sweep for any residual; regression test
added to `tests/test_pot_distribution.py`. This fix benefits **all** game modes.

## Risks

- **Ghost-seat / double-seat bug class (highest risk).** Moving players between tables is
  exactly the seat-lifecycle surface that has bitten this project repeatedly. Mitigation:
  a *single* atomic seat-transfer primitive, no ad-hoc seat mutation, and a hard
  **conservation invariant** — because there's no rake mid-tournament, `sum(all stacks)`
  must equal `field_size × starting_stack` at every round boundary. Assert it after every
  rebalance; it's a cheap, total correctness check that catches lost/duplicated players and
  chips instantly.
- **Standings correctness on simultaneous busts.** Two players busting in the same round on
  different tables need a deterministic finishing-position tiebreak (pinned: more chips at
  hand start finishes higher). Easy to get subtly wrong; covered by targeted tests.
- **Resume / cold-load divergence.** A long MTT is a long-lived object. This project's
  recurring failure mode is cold-load paths that diverge from the live path
  (cash-bust misroute, seat orphan, tournament_tracker misroute). When persistence lands,
  reconstruct the tournament through the *same* code path as live, and round-trip-test it.
- **Concurrency (live phase only).** Parallel live tables + per-table locks + a shared clock
  is genuinely hard. Headless-first sidesteps it (sequential rounds); we confront it only at
  Step 3, with the round primitive as the coordination point.
- **LLM cost & latency (later).** Full LLM personalities across a large field is expensive
  and slow. v1 avoids it entirely (rule bots); the tiered-fidelity plan (LLM only on the
  human's table + a few featured tables) is the intended answer when personas return.
- **Ticker ring-buffer overflow (later).** `activity.py` is an in-memory `deque(maxlen=50)`.
  A multi-table field generates many simultaneous events; naive emission would blow the
  buffer. The ticker must surface only top-drama tournament beats, not every hand.
- **Scope creep.** The vision is large and seductive. The discipline of this plan is: ship a
  correct headless engine first; resist pulling economy/circuit/prestige forward.

---

## Opportunities

- **The engine is also an eval harness.** A headless MTT that runs full fields of rule/tiered
  bots is a natural extension of the existing eval program — MTT-scale, ICM-adjacent bot
  evaluation, A/B of strategies under tournament pressure. Synergy with `run_ai_tournament.py`
  and the champion/challenger work.
- **A clean conservation invariant.** Funny-money tournament chips with no mid-event rake give
  a dead-simple, total correctness check (`sum(stacks)` constant) — a luxury the cash ledger
  never had.
- **Sandbox isolation = safe blast radius.** Running tournaments in their own ephemeral world
  keeps them away from the cash conservation ledger and the live casino.
- **The broadcast bus is ready.** The world ticker + `lobby:{owner_id}` room already reaches
  the player in lobby *and* in-game. Cross-table drama is "add event types," not "build
  plumbing."
- **The economy vocabulary already exists.** The stakes system (`principal`/`cut`/`carry`,
  house/AI/player stakers, offer/accept) maps almost directly onto buy-in staking when that
  phase arrives.
- **Prestige & achievements have a home.** `compute_prestige()` extends cleanly with a
  tournament component; the achievements spec already has a `tournament` category. Deep runs,
  final tables, and championships become first-class reputation drivers.
- **Circuit is a thin layer.** With a clean `create_tournament(config)` entry point, a
  daily/circuit scheduler is a small wrapper, not a rebuild.

---

## Future phases (designed-for, not built now)

- **Phase 2 — Live human table + standalone UI.** Wire the live loop into one table; relocate
  the human; build the tournament lobby/bracket/standings surface.
- **Phase 3 — Cross-table drama.** Tournament event types in the ticker (chip leader, table
  break, pay jump, bubble, knockout); the "Meanwhile…" interhand surface; final-table ceremony.
- **Phase 4 — Economy.** Buy-ins, prize structure, payouts to bankroll, staking-into-entries
  (reusing the stakes system), the money bubble.
- **Phase 5 — Circuit.** Scheduled/daily recurring tournaments over the `create_tournament`
  entry point.
- **Phase 6 — Prestige & achievements.** Tournament results feed renown/regard; achievement
  unlocks for milestones.

---

## Open questions (non-blocking for Phase 1)

1. **Blind structure defaults** — what level schedule (durations, ante introduction) feels
   right for an 18–45 field that finishes in a reasonable number of rounds? Tunable; start
   with a turbo-ish schedule for fast iteration.
2. **Table size** — 6-max (matches the existing 6-seat model everywhere) or 9-handed (more
   WSOP-authentic)? Recommendation: 6-max for v1 to reuse existing assumptions; make it config.
3. **Field bot mix** — uniform tiered bots, or a spread of archetypes (TAG/LAG/rock/fish) for
   realistic chip dynamics and more interesting eliminations? Recommendation: a configurable
   archetype mix; it makes the sim more lifelike and doubles as eval signal.
4. **Persistence granularity** — when Step 2/3 needs persistence, reuse `cash_tables` with
   `table_type='tournament'`, or a dedicated `tournament_tables` schema? Lean: dedicated
   schema, since tournament tables have different lifecycle/semantics than cash tables.
5. **Deterministic seeding** — one master seed → per-table/per-hand derived seeds, so a whole
   tournament is reproducible (critical for debugging balance/break and for eval).

---

## Key files (where this work lands / draws from)

| File | Role |
|---|---|
| `experiments/sng_runner.py` | Cleanest single-table headless WTA loop — the blueprint for the per-table driver |
| `experiments/run_ai_tournament.py` | AI tournament runner (parallel, A/B, heartbeats) — the harness sibling |
| `poker/tournament_tracker.py` | `TournamentTracker` — generalize to field-wide `TournamentField` |
| `poker/poker_state_machine.py` | `PokerStateMachine`, `BlindConfig`, `hand_over_transition` (blind escalation, 0-stack drop) |
| `poker/poker_game.py` | Immutable `PokerGameState`, `reset_game_state_for_new_hand` |
| `poker/repositories/tournament_repository.py` | Existing single-table result schema — extend for multi-table results |
| `cash_mode/activity.py` / `flask_app/services/ticker_service.py` | Ticker bus (Phase 3 drama) |
| `cash_mode/prestige.py` | `compute_prestige()` (Phase 6) |
| `docs/plans/ACHIEVEMENTS_SYSTEM.md` | Achievements spec (Phase 6) |
| `docs/plans/CASH_MODE_MULTI_TABLE_LOBBY.md` | Prior art: how `cash_tables` already supports N tables / `table_type` |

---

## Definition of done (Phase 1)

- A headless `TournamentDirector` runs an 18–45 entrant, 2–5 table tournament to completion
  using rule/tiered bots, with synchronized blinds.
- Tables **balance** (sizes within 1) and **break** correctly; the field consolidates to a
  final table; a single champion remains.
- Field-wide **standings** are correct, including simultaneous-bust tiebreaks.
- The chip-conservation invariant (`sum(stacks) == field_size × starting_stack`) holds at
  every round boundary, asserted in tests.
- Pure unit tests for balance/break/final-table + standings; full-tournament sim smoke tests;
  deterministic under a master seed.
- No Flask/socket/DB/economy coupling.

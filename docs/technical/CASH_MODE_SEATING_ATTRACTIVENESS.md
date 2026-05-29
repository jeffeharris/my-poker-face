---
purpose: Technical reference for cash-mode AI seating as implemented — the movement/attractiveness loop inversion, the scoring formula, the seating flow, conservation, and tuning levers.
type: reference
created: 2026-05-29
last_updated: 2026-05-29
---

# Cash Mode Seating & Table Attractiveness

How AI players decide **whether to leave a table** and **which table to sit
at** in cash mode, as shipped on the `prestige` branch (Phases A–C of
[`docs/plans/CASH_MODE_TABLE_ATTRACTIVENESS.md`](/docs/plans/CASH_MODE_TABLE_ATTRACTIVENESS.md)).

The design doc is the *why*; this is the *what's built*. For the deferred
"occupant / social (marquee) prestige" layer — a v2 feature with its own
`renown` stat — see the design doc's "Deferred to v2" section; none of it is
in the code.

## The model in one paragraph

Seating is **AI-centric**: an idle AI scores every table it could sit at by an
**attractiveness** function and greedily takes the most attractive affordable
open seat. This replaced the old **table-centric** model (each table rolled a
per-seat coin and grabbed the first qualifying idle AI). Movement off a table
("push") and selection of a table ("pull") are two separate stages:

- **Push** — `evaluate_ai_movement` decides stay / leave / rebuy / stake-up per
  seated AI from its own pressure (stack, energy, tenure, wealth, dead-table).
- **Pull** — a single global pass ranks idle AIs over open tables by
  `table_attractiveness` and seats them, recomputing occupancy between picks so
  sharks spread across fish rather than dogpiling.

## Components

| Piece | Location | Role |
|---|---|---|
| Pure scoring + greedy core | `cash_mode/attractiveness.py` | `room_prestige`, `wealth`, `wealth_over_tier`, `stake_fit`, `hunger`, `table_deadness`, `base_attractor`, `table_attractiveness`, `assign_seats_greedy` + `SeatSeeker`/`FillableTable`. No I/O, no rng — deterministic math. |
| Leave pressure / movement decision | `cash_mode/movement.py` | `compute_leave_pressure`, `evaluate_ai_movement`, `_coerce_fish_movement`, `_coerce_predator_retention`, `refresh_table_roster` (Step 1 = movement; Step 2 fill is gated off by `enable_live_fill=False`). |
| Seating orchestration | `cash_mode/lobby.py` | `refresh_unseated_tables` (per-table movement burst loop) → `_process_global_greedy_fills` (the global pull pass). |
| Stake ladder | `cash_mode/stakes_ladder.py` | `STAKES_ORDER` (`$2…$1000`), `table_buy_in_window` (40bb min / 100bb max). |

## The attractiveness score

`table_attractiveness(table, ai)` (`cash_mode/attractiveness.py`):

```
attractiveness =
      venue_appeal                                   # casino < lobby (baseline desirability)
    × base_attractor                                 # stake-fit + wealth-driven room-prestige climb
    × (1 + W_HUNGER · hunger(ai) · bait_present)     # low bankroll amplifies the fish/whale pull
    × (W_FISH · fish_stacks + W_WHALE · whale_stacks + BASE_DRAW)   # how much meat is on the table
    − W_CROWD · other_grinders                       # self-balancing crowd penalty

base_attractor = stake_fit(ai, table) + W_CLIMB · room_prestige(table) · wealth(ai)
```

Term by term:

- **`stake_fit`** — peaks at the AI's *fit center* (its `stake_comfort_zone`
  anchor dragged `ANCHOR_DRIFT` of the way toward what its bankroll can
  comfortably afford) and tapers with tier-distance. A nit grinds low even when
  flush; a winner who runs up a stack drifts upward — without abandoning
  character.
- **`room_prestige · wealth` (the climb)** — `room_prestige` is the tier-
  normalized glamour (`$2`≈0 → `$1000`≈1, squared). `wealth` is the AI's
  absolute richness (0→1, log-scaled across the ladder). The product is
  meaningful only for *rich AIs at high tiers*, so it pulls the wealthy *up*
  toward the Pit while a broke AI (`wealth≈0`) stays anchored. This is the
  entirety of "prestige" in v1 — two numbers that already exist, no new stat.
- **Fish/whale draw** — seat chips of seated fish/whales, normalized to
  *stacks* (÷ table max buy-in) so the term is scale-stable across stakes. A
  whale is a fish seated at a **lobby** table (regular fish are casino-only);
  it weighs heavier (`W_WHALE > W_FISH`) and, being deep, dominates.
- **`hunger` multiplier** — continuous 0→1 desperation (bankroll vs starting);
  a near-broke grinder is pulled to bait far harder than a flush one. Gated on
  `bait_present` (a fish **or** whale).
- **`W_CROWD`** — load-bearing: penalizes each additional grinder so the pull
  reaches an equilibrium (sharks spread across fish ∝ the meat) instead of
  dogpiling one table.
- **`venue_appeal`** — the casino is the low-rent public grind room:
  `CASINO_VENUE_APPEAL` (< 1) makes it less attractive in general, so AIs prefer
  an equivalent lobby table. The fish draw rides *over* the penalty (a fishy
  casino out-pulls a dead lobby table), and the score stays positive so a casino
  is the always-open fallback when nothing else has a seat.

## The seating flow

`refresh_unseated_tables` (`cash_mode/lobby.py`) runs once per lobby refresh
(human-seated tables are handled by the hand-boundary hook instead):

1. **Per-table movement burst** — for each non-human table, simulate the
   catch-up burst (`play_one_hand`) and run `refresh_table_roster(...,
   enable_live_fill=False)`. Step 1 applies each seated AI's movement decision
   (vacate / rebuy / stake-up / take-stake / go-vice); **no fill happens here.**
   Per-table deadness is computed and fed into each AI's `MovementContext`.
2. **Global greedy fill** — `_process_global_greedy_fills` runs once after the
   loop:
   - Build a `FillableTable` per table: `grinder_count`, `fish_chips` /
     `whale_chips` (a lobby fish → whale), `venue_appeal` from `table_type`, and
     the **usable open seats** = open now AND open at the pre-burst start (so a
     seat vacated *this* refresh isn't re-filled this tick — "empty for a tick").
   - Build `SeatSeeker`s from the idle pool + eligible-never-seated AIs, each
     rolled against the **seek-rate** and gated (per-table leave cooldown, idle
     recovery, target-stake stickiness) into `allowed_table_ids`.
   - `assign_seats_greedy` seats each seeker (most-desperate-first) at its
     `argmax` affordable open table, decrementing occupancy between picks.
   - Apply: fund each seat with an inline `debit_bankroll_for_seat`, place the
     AI only on a successful debit, remove its idle row, mutate `seated_globally`,
     `save_table`, and emit JOIN activity events.

### Push: leave pressure

`compute_leave_pressure` returns five weighted components summed into a total;
leave probability is `total / (total + LEAVE_K)`. The dominant component picks
the direction (`evaluate_ai_movement`):

| Component | Source | Routes to |
|---|---|---|
| `short` | stack < min buy-in | rebuy or take_break |
| `stake_up` | **stronger of** seat-stack-over-tier **or** wealth-over-tier (the "slumming" climb, past `SLUM_DEADZONE`) | stake_up if next tier affordable, else take_break |
| `detached` | folding too much | bored_move |
| `tenure` | low energy | bored_move |
| `dead` | `W_DEAD · table_deadness` — a casino that's lost its fish | bored_move (go find action) |

Two coercions run after the raw decision:
- **`_coerce_fish_movement`** — fish stay/reload until bust (or storm off when tilted).
- **`_coerce_predator_retention`** — a grinder at a fish table doesn't drift off
  (`bored_move`→`stay`) until worn down (`energy < CASINO_PREDATOR_FATIGUE_FLOOR`)
  **or** rich enough to graduate (`wealth_over_tier ≥ PRESTIGE_RETENTION_OVERRIDE`,
  which converts the drift to `stake_up` — up, not sideways). The wealth release
  also fixes the historical hoarding bug (a winning grinder's energy never drops,
  so the energy gate alone never fired).

### `table_deadness`

`table_deadness(is_casino, has_fish, grinder_count)` → 0 unless a **casino** has
**no fish**, then rises with the stuck-grinder crowd (saturating at
`CASINO_DEAD_GRINDER_SCALE`). Tables with fish, and all non-casino tables
(grinders playing each other *is* the game), are never dead. Computed once per
table in `refresh_table_roster` and fed to `MovementContext.table_deadness`.

## Conservation

Seating is a pure bankroll → seat transfer and must keep the chip-ledger audit
at zero drift (see [`CASH_MODE_ECONOMY.md`](/docs/technical/CASH_MODE_ECONOMY.md)).
The global fill owns its own persistence (the per-table loop's bankroll /
settlement passes already ran), so:

- Each new seat is funded by an **inline `debit_bankroll_for_seat`**, and the AI
  is placed **only if the debit succeeds** — never seat without funding (no
  chip mint), and no result-level `to_seat` change is appended (would
  double-debit through the per-table loop).
- Step 1's `from_seat` credits and stake settlement are **untouched** by the
  fill pass (settlement keys on `from_seat` indices).
- `seated_globally` is mutated as each AI is seated; within-batch double-seating
  is prevented by one-`SeatSeeker`-per-pid candidate dedup.

Validated: a 2000-tick economy sim held `max|drift| = 0` across every audit
checkpoint.

## Tuning levers

All sim-tunable starting points (none deeply tuned yet). In
`cash_mode/attractiveness.py` unless noted:

| Constant | Default | Effect |
|---|---|---|
| `W_FISH` / `W_WHALE` | 1.0 / 2.0 | Weight of fish vs whale chips in the draw |
| `BASE_DRAW` | 1.0 | Floor draw so a fishless table is still pickable |
| `W_HUNGER` | 2.0 | How hard low bankroll amplifies the bait pull |
| `W_CROWD` | 0.5 | Per-grinder crowd penalty (the spreader) |
| `W_CLIMB` | 1.0 | Strength of the rich → prestigious-room pull |
| `ROOM_PRESTIGE_CURVE_EXP` | 2.0 | >1 makes the top room stand out |
| `STAKE_FIT_TAPER` | 0.5 | Attractiveness lost per tier from the fit center |
| `AFFORDABLE_BAND_BUYINS` | 5.0 | Buy-ins of bankroll that count as "comfortably rolled" |
| `ANCHOR_DRIFT` | 0.5 | How far wealth drags the fit center off the comfort anchor |
| `HUNGER_FULL_ROLL_RATIO` / `_DESPERATE_RATIO` | 1.0 / 0.2 | Hunger ramp endpoints (bankroll ÷ starting) |
| `CASINO_VENUE_APPEAL` | 0.5 | Casino baseline desirability multiplier (< 1) |
| `CASINO_DEAD_GRINDER_SCALE` | 3.0 | Fishless-casino grinders for full deadness |
| `DEFAULT_SEEK_RATE` | 0.35 | Per-refresh probability an idle AI goes room-hunting (replaces the old `live_fill_prob`); plumbed as `refresh_unseated_tables(seek_rate=…)` |
| `W_SLUM` (`movement.py`) | 0.01 | Scales wealth-over-tier into the stake_up climb |
| `SLUM_DEADZONE` (`movement.py`) | 20.0 | Multiples-over-tier before the climb fires (keeps healthy grinders settled) |
| `W_DEAD` (`movement.py`) | 0.4 | Scales `table_deadness` into the leave term |
| `PRESTIGE_RETENTION_OVERRIDE` (`movement.py`) | 20.0 | Wealth-over-tier that releases a predator from fish-retention (graduates up) |

`W_SLUM` / `SLUM_DEADZONE` are the headline knobs — the design doc's open
question ("the rich never settle" vs "the rich stay stuck").

## What this replaced

The old per-seat live-fill and its bolt-on heuristics were removed from
`refresh_unseated_tables`:

- `live_fill_prob × 2` casino/whale boosts → subsumed by `attractiveness` + the
  seek-rate.
- `list_hungry_grinders` / `list_affordable_predators` idle-pool reorders →
  subsumed by `hunger` + the fish/whale draw + `W_CROWD`.
- The per-seat Bernoulli fill in `refresh_table_roster` (Step 2) → gated off via
  `enable_live_fill=False`; replaced by the global greedy pass.

`refresh_table_roster`'s Step 2 code and the `live_fill_prob` knob remain for
back-compat / tests but are inert on the lobby path.

## Validation

- Unit: `tests/test_cash_mode/test_attractiveness.py` (scoring + greedy core),
  `test_movement_prestige.py` (leave-pressure terms), `test_global_greedy_fill.py`
  (fill wiring), plus the dead-table push + `enable_live_fill` guards in
  `test_movement.py`.
- Sim: `scripts/sim_smoke_attractiveness.py` (gitignored; runs a fresh sandbox in
  a WAL-safe copy of prod). 2000-tick run: 0 conservation drift; the `$1000` Pit
  populated (1–4 of 6 seats — the empty-Pit symptom is fixed); a sensible stake
  pyramid (`$50` most popular, thinning to the `$2` casino floor and the Pit);
  3 casinos holding fish steadily; stable wealth distribution.

## Related

- [`docs/plans/CASH_MODE_TABLE_ATTRACTIVENESS.md`](/docs/plans/CASH_MODE_TABLE_ATTRACTIVENESS.md) — the design/spec + the deferred v2 occupant-prestige layer.
- [`CASH_MODE_ECONOMY.md`](/docs/technical/CASH_MODE_ECONOMY.md) — the chip-conservation invariant + audit the seating path must preserve.
- [`CASH_MODE_FULL_SIM.md`](/docs/technical/CASH_MODE_FULL_SIM.md) — the sim that drives `refresh_unseated_tables`.
- TRIAGE **T2-75** (`docs/triage/REFRESH_UNSEATED_TABLES_GOD_FUNCTION.md`) — the
  ~1,715-line `refresh_unseated_tables` god-function (P1 refactor; the seating
  change was deliberately scoped to a contained pass to avoid touching it).

## Not yet studied

- Tuning the knobs against the real opponent distribution (busier Pit, flatter
  `$50`).
- The "fishless casino: keep open for the dregs vs shut down" question — fish
  stayed seated for all 2000 sim ticks, so the venue penalty + `table_deadness`
  push never fired in-sim. Needs bustable-fish seeding to exercise.

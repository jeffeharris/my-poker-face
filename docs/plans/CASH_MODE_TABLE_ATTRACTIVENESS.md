---
purpose: Give cash tables an AI-facing attractiveness score so grinders hunt where the fish/whales are, restrict fish movement, and turn whales into a pool relief valve
type: spec
created: 2026-05-25
last_updated: 2026-05-25
---

# Cash Mode: Table Attractiveness

## Problem

The AI movement model is **push-only**. `evaluate_ai_movement`
(`cash_mode/movement.py:145`) decides stay/leave purely from the AI's
*own* state — stack, leave-pressure, energy, tenure. Nothing scores a
table by *who else is sitting there*, and a leaving AI (`bored_move`)
just drops into the idle pool and gets re-seated by **independent
per-table live-fill rolls** — it never moves *toward* anything.

The only "go where the money is" signal today is a blunt constant at
`cash_mode/lobby.py:1054-1057`: casino tables get `live_fill_prob × 2`.
It doesn't know whether the casino actually holds fish right now — it
just jams grinders into every open seat.

**Observed failure (sandbox `d7e8a3f4`, 2026-05-25):** both casinos
($2, $10) were full of grinders with **zero fish seated**, while all 9
fish personas sat idle off-table holding leftover pool-funded
bankrolls. Mechanism:

1. A fish busts / short-leaves → its seat opens.
2. The 2× grinder live-fill grabs the open seat before the fish-refill
   pass runs (`resolve_casino_provisioning` is called at
   `lobby.py:1863`, *after* the per-table live-fill at
   `lobby.py:1080-1093`).
3. `_refill_one_fish` (`casino_provisioning.py:610-611`) bails when
   there's no open seat — so the fish can never get back in.
4. Fish are excluded from lobby live-fill (`lobby.py:616-620`), so the
   refill pass is their *only* re-seating path.

Net: the casino — designed as a fish farm — drifts to all-grinders, no
fish. Grinders farm each other and the human. The pool, with nothing
draining it, bloats (1,594,760 chips observed, vs spawn thresholds of
$2=5k / $10=50k).

### Not to be confused with the split-brain count bug

A *separate* acute cause of "casino shows no fish" was diagnosed and
fixed on `development` the same day (`fb339db6` + `6a4a296e`, see memory
`project_casino_eph_seat_wedge`): pre-migration `<fish>__eph_<hash>`
clone seats (placed via `ai_slot`, no `archetype='fish'` stamp) were
counted as fish because `_count_seated_fish` counts
`personality_id in fish_ids`. Casinos looked "full" → refill never
fired, teardown never fired, but the UI (reading the seat stamp) saw 0
fish. Wedged. The fix makes `_count_seated_fish` count by the
`archetype='fish'` *seat stamp* (single source of truth).

This is **distinct** from the structural problem above: even with clean
counting and zero eph-clones (as in sandbox `d7e8a3f4`, which held real
non-fish grinders, not clones), the model still lets grinders crowd fish
out of seats. Attractiveness addresses *that*.

> **Precondition — SATISFIED (2026-05-25).** The eph-fix
> (`fb339db6`+`6a4a296e`) is now merged into `career-mode-v0_1`;
> `_count_seated_fish` (`casino_provisioning.py:595`) counts by the
> `archetype='fish'` seat stamp. The split-brain no longer masks refill,
> so attractiveness work can proceed on a clean count.

## Design

Replace the "push-only + 2× hack" with a **pull**: an AI-facing
attractiveness score per table. Grinders are drawn to juicy tables and
repelled from dead ones; fish anchor the table and barely move; whales
are a rare, pool-funded jackpot that doubles as the pool's relief valve.

### 1. Table attractiveness score

A score a grinder rolls over candidate tables, layered so **stake-fit is
the base attractor** ("which stakes do I even play") and the fish/whale
draw rides on top ("which of those is juiciest"). Driven by **chips, not
headcount** — a fish down to 20 chips isn't worth chasing; a whale on
7,000 is:

```
attractiveness(table, ai) =
      stake_fit(ai, table_stake)                  # BASE — bankroll-derived band
    × (1 + W_HUNGER × hunger(ai) × fish_here)     # low bankroll → fish pull harder
    × ( W_FISH  × Σ(fish seat chips)
      + W_WHALE × Σ(whale seat chips)             # whales weighted higher AND
      + BASE_DRAW )                               #   deeper-stacked → dominate
    − W_CROWD × (other grinders at table)         # self-balancing term
```

The AI then makes a **weighted probability roll** over *affordable*
tables by this score — replacing today's "flat `live_fill_prob` roll →
oldest affordable idle AI" (`movement.py:1015-1060`) and the `×2` casino
hack.

#### Base attractor: stake fit (anchor + bankroll)

AIs already have a preferred stake — `stake_comfort_zone`, a **static**
per-personality config field (`personalities.json`, e.g. `"$10"`). Today
it's barely used for seating: it only gates the Phase-4 *staker* pool
(`lobby.py:647`, comfort ±1 adjacency); the actual seat-fill is driven by
affordability + queue order, not preference.

Make it **bankroll-responsive while keeping personality character**
(decision: anchor + bankroll, not fully derived):

- Keep `stake_comfort_zone` as a **bias/anchor** — a nit grinds low even
  when flush; a gambler takes shots above roll.
- Compute an **affordable band** from current bankroll (e.g. tiers where
  `bankroll ≥ N × buy_in`).
- `stake_fit` = a weighted preference peaking where the anchor and the
  band agree, tapering as a table's stake drifts from that. So a grinder
  who runs up a stack naturally drifts upward; one who's crushed drops
  down — without abandoning its character.

#### Hunger multiplier (continuous, replaces the binary gate)

"Low on cash → casino fish tables look more attractive" **already exists,
but as an on/off switch**: at casino tables, *hungry grinders* (bankroll
< starting × `GRINDER_HUNGER_THRESHOLD` 0.8, casino-tier comfort) get
priority seating + 2× fill (`lobby.py:1055-1079`). Generalize that to a
**continuous `hunger(ai)` term** (0 at full roll → 1 when desperate) that
amplifies the fish/whale draw. A flush grinder is mildly drawn to fish; a
near-broke one is pulled hard toward the casino to recover.

#### Notes

- **`W_CROWD` is load-bearing.** Without it, every grinder dogpiles the
  single fish table — the same failure we have now, just *motivated*
  instead of blind. Penalizing each additional grinder (the fish's money
  is split N ways; the table is "tough") yields an equilibrium where
  sharks spread across fish proportional to the meat. Also realistic:
  pros avoid tables stacked with pros.
- **This unifies three scattered mechanisms** into one scoring function:
  the comfort-zone adjacency gate, the hungry-grinder priority reorder,
  and the casino `×2` live-fill hack all collapse into
  `attractiveness()`.
- **v2 (deferred):** human-keyed terms — likability/rivalry from the
  relationship layer, the human's win/loss streak (motivator vs
  avoidance), and expected edge from the grinder's history vs known
  opponents. The relationship layer already exists
  (`CASH_MODE_AND_RELATIONSHIPS.md`), so this is additive, not
  foundational. Out of scope for v1.

### 2. Plug-in surfaces

The score feeds two existing decision points:

- **Pull — live-fill table selection.** Today open seats fill via
  independent per-table rolls (`movement.py:963+`). Change: when a
  grinder leaves the idle pool, weight *which* table it targets by
  attractiveness. This **replaces the `×2` casino hack
  (`lobby.py:1054-1057`)** — a casino is attractive because it *has
  fish*, computed live, not because `table_type=='casino'`.
- **Push — leave pressure.** Add an attractiveness term to
  `compute_leave_pressure` (`movement.py:117-141`): a grinder at a juicy
  table gets *reduced* leave pressure ("why leave, I'm printing"); one
  at a dead all-shark table gets *more* `bored_move` pressure to go find
  fish. Keep the existing four sources (`short` 0.6, `stake_up` 0.5,
  `detached` 0.3, `tenure` 0.2; `LEAVE_K` 2.0) intact.

### 3. Fish restriction

Fish are already partly sticky: `stake_up` and `bored_move` are coerced
to `stay` (`movement.py:794`), so fish never move up a tier or
table-wander. Two exits survive:

- `forced_leave` — busted (stack ≤ `0.3 × min_buy_in`,
  `FORCED_LEAVE_RATIO`). **Keep.** A fish going home broke is the point.
- `take_break` — short-stacked walk (leave-roll fires + `short`
  dominant + leave-vs-rebuy lands on `take_break`). **Suppress for
  fish:** coerce `take_break → rebuy` as long as the pool-funded
  bankroll can fund the top-up.

Result: a fish has exactly one way off the table — bleeding its entire
bankroll dry. Sit and feed until broke.

### 4. Fish seat reservation (the load-bearing fix)

Restriction alone won't keep tables stocked — a fully-restricted fish
still vacates on bust, and attractiveness makes grinders *more* eager to
grab that seat. So fish need seating priority. Two changes, belt and
suspenders:

- **Order flip:** run the fish-refill pass *before* grinder live-fill at
  casino tables (today it runs after — `lobby.py:1863` vs `1080`).
- **Seat reservation:** at casino tables, cap grinder live-fill so at
  least `reserved = max(0, CASINO_FISH_MIN − seated_fish)` seats stay
  open for fish until the fish quota is met. Grinders may fill only
  `open_seats − reserved`.

### 5. Whale activation = pool relief valve

The whale path exists but is **dead code**: `_fish_prefund(whale=True)`
(`casino_provisioning.py:440-453`, 10–18× buy-in vs 2.5–3.6× for a
regular fish) is never invoked — `whale=True` is passed nowhere.

Activate it as a **pool-depth-triggered** event, **not a flat random
roll** (per design intent at `casino_provisioning.py:431-433`: "the
relief valve for a pool accruing faster than grinders can farm it
down"):

```
pool_excess = max(0, pool_reserves − WHALE_POOL_THRESHOLD)
whale_due ∝ pool_excess          # probability scales with bloat, clamped
```

When a whale is due, the next casino spawn/refill seats a whale (deep
10–18× prefund drawn from the pool) instead of a regular fish. The deep
prefund is the drain: grinders farm the whale down, moving the bloated
pool chips into grinder bankrolls (circulation). Rare by construction —
only fires when the pool is genuinely over-full.

- **Ticker.** A whale seating emits a `world_event` on the existing
  realtime ticker (`CASH_MODE_REALTIME_TICKER.md`, `lobby_tick` /
  `world_event` socket push) — e.g. "🐋 high roller just sat down at
  $10." Both flavor and a real pull signal that draws grinders (and the
  human) toward the table.

## Touch points

| File | Change |
|---|---|
| *(precondition ✅)* | eph-fix `fb339db6`+`6a4a296e` (stamp-based `_count_seated_fish`) — merged into `career-mode-v0_1` 2026-05-25 |
| `cash_mode/movement.py` | new `table_attractiveness()` (stake_fit × hunger × fish/whale − crowd); `stake_fit()` band; `hunger()` curve; attractiveness term in `compute_leave_pressure`; coerce fish `take_break → rebuy`; **weighted-probability** live-fill target selection (replaces oldest-first) |
| `cash_mode/lobby.py` | drop `×2` casino hack (`1054-1057`) **and** the hungry-grinder reorder (`1058-1079`) — both subsumed by `attractiveness()`; flip refill-before-live-fill ordering at casinos; fish seat reservation |
| `cash_mode/casino_provisioning.py` | pool-depth whale trigger; pass `whale=True` to `_fish_prefund`; whale `world_event` emit |
| `cash_mode/closed_economy.py` | `WHALE_POOL_THRESHOLD`, attractiveness weights (`W_FISH`/`W_WHALE`/`W_CROWD`/`W_HUNGER`), affordable-band `N`, hunger curve constants |
| `cash_mode/bankroll.py` | derive affordable stake band from bankroll + `stake_comfort_zone` anchor (knobs already load here) |

## Validation (sim)

Run the cash economy sim (`cash_mode/sim_runner.py`) and assert:

1. **Fish stay seated.** Over N ticks, casino tables hold ≥
   `CASINO_FISH_MIN` fish the large majority of the time (vs ~0 today).
2. **Grinders cluster on fish, not each other.** Grinder-per-fish ratio
   converges; no table goes all-grinder while fish idle with bankrolls.
3. **Whale drains the pool.** Seed a bloated pool; confirm whale spawns
   fire and pool reserves trend down toward `WHALE_POOL_THRESHOLD`.
4. **Chip conservation holds.** Audit drift stays flat (no new leak from
   whale prefund / seat-reservation paths) — guard against the
   `vice_spending` / fish-accounting class of bugs.

## Open questions

- Exact `W_FISH` / `W_WHALE` / `W_CROWD` / `W_HUNGER` weights — tune in sim.
- Affordable-band `N` (buy-ins of cushion to consider a tier "affordable")
  and how sharply `stake_fit` tapers away from the anchor∩band peak.
- `hunger(ai)` curve shape — linear in `1 − bankroll/starting`, or steeper
  near broke? Reuse `GRINDER_HUNGER_THRESHOLD` (0.8) as the knee?
- `WHALE_POOL_THRESHOLD` — multiple of normal operating pool; and whether
  it's per-sandbox or global.
- Should attractiveness-weighted selection apply to lobby tables in v1
  (scope = "all cash tables") or stay casino-only until v2 brings the
  human terms? Leaning: compute the score for all tables in v1 but the
  only non-zero contributors are fish/whales (casino-only), so lobby
  tables score ~flat until v2 — no behavior change there yet, but the
  plumbing is in place.

## Deferred to v2

Human-as-attractor: relationship/rivalry/likability, win-loss streak as
motivator vs avoidance, and grinder edge estimated from history vs known
opponents. Built on the existing relationship layer.

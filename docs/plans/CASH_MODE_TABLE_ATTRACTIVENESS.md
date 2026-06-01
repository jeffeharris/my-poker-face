---
purpose: Give cash tables an AI-facing attractiveness score so grinders hunt where the fish/whales are and the rich are drawn up to prestigious high-stakes rooms — unifying today's scattered seating heuristics into one score. (Occupant/social prestige is deferred to v2.)
type: spec
created: 2026-05-25
last_updated: 2026-05-29
---

# Cash Mode: Table Attractiveness

## Implementation status (2026-05-29, branch `prestige`)

This doc covers six pieces. Three shipped via the diagnostics-driven
casino-economy work; one is deferred; two are the v1 build. Read this table
first.

| # | Piece | Status | Where |
|---|---|---|---|
| 1 | **Core attractiveness scoring** — `stake_fit` + continuous `hunger` + fish/whale draw − crowd, driving a **greedy best-available table pick** | **v1 — UNBUILT** | replaces hacks at `lobby.py:1192-1241` + selection at `movement.py:1301-1346` |
| 2 | **Room prestige** — wealth bends the stake band upward; the rich climb to the empty `$1000` Pit | **v1 — UNBUILT** | `movement.py` leave-pressure + retention |
| 3 | **Occupant / social prestige** — relationship-derived marquee draw | **DEFERRED to v2** (see "Deferred" §) | a future `renown` stat, *not* a `respect` projection |
| 4 | **Fish restriction** (stay/reload until bust) | **SHIPPED** | `_coerce_fish_movement` (`movement.py:273`) |
| 5 | **Fish seat reservation** | **SUPERSEDED** by the casino dam + predator retention | `casino_provisioning.py` |
| 6 | **Whale activation** (pool relief valve) | **SHIPPED, redesigned** — whale sits at a *lobby* table, not the casino | `resolve_whale_provisioning` (`casino_provisioning.py:1509`) |

**Why occupant prestige is deferred (2026-05-29 decision).** Nearly all of this
design's complexity — a projection of the shared `respect` axis, a new
aggregate stat, a schema migration, and a cluster of saturation/gravity-well
controls — lives in piece #3. It also hurts *legibility*: explaining why an AI
moved would mean tracing `respect → regard → social_prestige → occ_prestige →
attractiveness`. Pieces #1–#2 fix the actual observed symptom (rich AIs idle
while the Pit sits empty) using only numbers that **already exist** — no new
stat, no projection, no migration, and **`respect` is untouched in v1**. The
marquee idea is good and kept alive in "Deferred to v2," where it gets **its
own legible `renown` stat** instead of projecting `respect`.

## Refinement decisions (v1)

1. **Full unification is a loop *inversion*.** Seating today is
   **table-centric**: each table reorders the shared idle pool and does a
   per-seat Bernoulli roll, grabbing the first qualifying AI. There is no
   "AI compares tables." Making attractiveness actually steer AIs requires an
   **AI-centric** selection: each idle AI ranks candidate tables and picks the
   best available one (greedy `argmax`; see §2 for why greedy beats a roll). The
   spec commits to that inversion as the **target design** (§2), and documents a **minimal
   in-table-weighting fallback** as an optional phase-1 step with its
   limitation stated.
2. **v1 adds no new stored stat, no projection, no migration.** Both v1 pulls
   (fish/whale draw, wealth-driven climb) read numbers that already exist —
   seat chips, `projected_bankroll`, the stake-ladder index, `stake_comfort_zone`.
3. **`respect` and the relationship layer are untouched in v1.** Everything
   that wanted to read/migrate/rescale `respect` belonged to occupant
   prestige, now deferred. (Context for later: the current `respect` default
   `0.5`, and the staking/sponsorship floors at `0.5`/`0.6`, are **not tuned
   values** — just where the system started. Pre-release, single player. When
   we build the social layer we tune that whole cluster together, freely; no
   tiptoeing around today's defaults.)

## Problem

The AI movement model is **push-only**. `evaluate_ai_movement`
(`cash_mode/movement.py:224`) decides stay/leave purely from the AI's *own*
state — stack, leave-pressure, energy, tenure. Nothing scores a table by *who
else is sitting there*, and a leaving AI (`bored_move`) just drops into the
idle pool and gets re-seated by **independent per-table live-fill rolls**
(`movement.py:1301-1346`) — it never moves *toward* anything.

The only "go where the money is" signals today are blunt heuristics bolted
onto the lobby fill loop (`cash_mode/lobby.py:1192-1241`):

- Casino tables get `live_fill_prob × 2` and the idle pool reordered
  hungry-grinders-first (`list_hungry_grinders`).
- Lobby tables holding a whale get `× 2` and reordered richest-first
  (`list_affordable_predators`).

Neither weighs *how much* meat is on the table; they just bias *which* idle AI
fills *faster*. And `stake_comfort_zone` — the one per-personality stake
preference — is used **only** in the Phase-4 staker pool
(`lobby.py:728-781`), never in regular seat-fill.

**Why rooms end up sparse.** AIs don't *choose* rooms today. Each tick the
lobby loops over every table and flips a per-open-seat coin at
`live_fill_prob = 0.05` (`movement.py:68`); only on a hit does it grab the
first affordable idle AI. So a given seat fills ~once every 20 ticks
*regardless of how attractive the table is* — seating is a slow passive
trickle, not a demand-driven "AIs go sit at the best room." (The casino `×2`
hack bumps 5%→10% precisely because the base rate is too slow.) The
energy-recovery gate and per-table cooldown hold idle AIs out further. The
fix below makes seating **AI-centric and demand-driven**, with the fill *rate*
a separate, tunable knob.

**The wealth gap (the `prestige` branch's headline symptom).** The `$1000`
"High Roller Pit" (the single top table, `lobby_config.py:62-63`) sits **empty
while AIs hold 300k+ net worth**. Affordability isn't the gate — a 300k AI
clears the $1000 min buy-in (40bb = 40k) trivially. Three mechanics conspire:

1. **`stake_up` reads the seat stack, not wealth** (`movement.py:173`,
   `stake_up_raw = max(0, ai_chips/max_buy_in − 1)`). A 300k AI on a 5k stack
   at $50 generates *zero* climb pressure. Bankroll only *gates* whether it
   *can* move up (`movement.py:265-269`); it never makes it *want* to.
2. **One tier at a time, by lottery.** Climbing $2→$1000 means winning the
   leave-pressure roll with `stake_up` dominant *four* times in sequence.
3. **Predator retention pins the rich low** (`_coerce_predator_retention`,
   `movement.py:309`): a grinder at a table holding a fish has `bored_move`
   suppressed until energy drops below `CASINO_PREDATOR_FATIGUE_FLOOR` (0.2) —
   **but winning *raises* energy**, so winning grinders never release (the
   live hoarding bug: Lady Macbeth pinned on a 544k seat). The richer the AI,
   the more stuck it is.

> **Precondition — SATISFIED.** The eph split-brain count bug (a *separate*
> "casino shows no fish" cause) was fixed earlier (`fb339db6`+`6a4a296e`);
> `_count_seated_fish` (`casino_provisioning.py:753`) counts by the
> `archetype='fish'` seat stamp. Attractiveness work proceeds on a clean
> count.

## Design (v1)

Replace the "push-only + scattered hacks" with a **pull**: an AI-facing
attractiveness score per table. Grinders are drawn to juicy tables and
repelled from dead ones; the rich are drawn upward to prestigious rooms.

### 1. The attractiveness score

A score an AI computes for each candidate table, layered so **stake-fit is the base
attractor** ("which stakes do I even play") and the fish/whale draw rides on
top ("which of those is juiciest"). Driven by **chips, not headcount** — a
fish down to 20 chips isn't worth chasing; a whale on 7,000 is:

```
attractiveness(table, ai) =
      base_attractor(ai, table)                   # BASE — stake_fit + room-prestige bend (below)
    × (1 + W_HUNGER × hunger(ai) × fish_here)     # low bankroll → fish pull harder
    × ( W_FISH  × Σ(fish seat chips)
      + W_WHALE × Σ(whale seat chips)             # whales weighted higher AND
      + BASE_DRAW )                               #   deeper-stacked → dominate
    − W_CROWD × (other grinders at table)         # self-balancing term

base_attractor(ai, table) =
      stake_fit(ai, table_stake)                       # personality anchor ∩ affordable band
    + W_CLIMB × room_prestige(table) × wealth(ai)      # the rich are pulled UPWARD
```

> **v2 hook.** The occupant/marquee term
> (`+ W_MARQUEE × occ_prestige(table) × status_appetite(ai)`) is **deferred** —
> see "Deferred to v2." In v1 the base attractor is stake-fit plus the
> wealth-driven climb only.

The AI then **picks the best** of its *affordable* tables by this score —
greedy `argmax`, not a roll (§2).

#### Base attractor: stake fit (anchor + bankroll)

AIs already have `stake_comfort_zone`, a **static** per-personality field
(`personalities.json`, e.g. `"$10"`), used today only for the staker pool
(`lobby.py:728-781`). Make it **bankroll-responsive while keeping character**:

- Keep `stake_comfort_zone` as a **bias/anchor** — a nit grinds low even when
  flush; a gambler takes shots above roll.
- Compute an **affordable band** from current bankroll (tiers where
  `bankroll ≥ N × buy_in`).
- `stake_fit` peaks where anchor and band agree, tapering as a table's stake
  drifts away. A grinder who runs up a stack drifts upward; one who's crushed
  drops down — without abandoning character.

#### Hunger multiplier (continuous, replaces the binary gate)

"Low on cash → fish tables look more attractive" exists today only as an
**on/off switch**: hungry grinders (bankroll < starting × `GRINDER_HUNGER_THRESHOLD`
0.8, `closed_economy.py:84`) get priority + 2× fill at casinos
(`lobby.py:1193-1216`). Generalize to a **continuous `hunger(ai)`** (0 at full
roll → 1 when desperate) that amplifies the fish/whale draw. A flush grinder
is mildly drawn to fish; a near-broke one is pulled hard toward the casino.

#### Room prestige: wealth bends the band upward

The static, tier-derived glamour of the venue (the `$1000` Pit is a draw
because it's the Pit). It pulls the *rich* upward and seeds a cold table.
**This is the entirety of "prestige" in v1** — it's just two numbers that
already exist (a tier rank and the AI's bankroll), no new stat. Three pieces,
all **soft pull** (decision: **no net-worth floor**; the normal buy-in window
stays the only hard affordability gate):

**(a) `room_prestige(table)`** — tier-normalized scalar from the
`STAKES_ORDER` index (`$2`≈0 → `$1000`≈1), curved so the top stands out (e.g.
squared), with an **optional per-room override** in `lobby_config.py`
(`LobbyTableEntry` gains a `prestige` field, `lobby_config.py:36`) so two
same-stake rooms can differ in flavor. v1 ships tier-derived; the override is
the hook.

**(b) `wealth(ai)`** — continuous 0→1, rising as the AI's wealth exceeds a
multiple of the *current* tier. The product `room_prestige × wealth` is
meaningful only at high tiers held by wealthy AIs (a broke AI at $2 has
`wealth≈0` → no distortion; the model reduces to the plain anchor). Because
`wealth` bends the *base attractor* — which `stake_fit` would otherwise taper
toward zero far above the anchor — the rich get drawn *above* their anchor
while everyone else stays anchored.

> **Wealth signal (v1):** use `projected_bankroll` (already in
> `MovementContext`, `movement.py:154`), not full net worth. Net worth (chips +
> receivable − outstanding) is the truer figure but adds cost to the movement
> hot path for a number that ≈ bankroll for everyone except big stakers. **v2:**
> swap in net worth if staker-rich AIs need to feel the pull too.

**(c) Push + retention override.** The pull governs *which* table an idle AI
targets, but a content seated rich AI never enters the idle pool. Two edits:

- **Wealth-driven `stake_up` pressure.** Modify the `stake_up` source in
  `compute_leave_pressure` (`movement.py:173`):
  `stake_up_raw = max(ai_chips/max_buy_in − 1, W_SLUM × wealth_over_tier(ai))`.
  A 300k AI at $50 is ~60× over tier → strong "I'm slumming it" pressure → it
  leaves → idle pool → room prestige routes it up.
- **Prestige beats fish-retention for the truly rich** (decision: *prestige
  wins eventually*). In `_coerce_predator_retention` (`movement.py:309`), add a
  release condition `wealth_over_tier(ai) ≥ PRESTIGE_RETENTION_OVERRIDE`
  **alongside** the existing energy-floor release. **Co-benefit:** this also
  fixes the live hoarding bug — winning grinders whose energy never drops still
  graduate once they're rich enough, so a winner can't pin a fish seat forever.
  Keep `bored_move` suppression intact — the rich don't wander *sideways*, they
  go *up*.

**Cold start (decision: none).** No targeted seeding of an empty
high-prestige table. The Pit fills organically via the pull + wealth stake-up
pressure. Tradeoff: on a fresh world the Pit may stay empty until the first
qualifying AI climbs all the way up. Revisit only if sim shows fill is
unacceptably slow.

> **Scope note.** With room prestige, lobby tables carry **non-zero
> attractiveness in v1** — `room_prestige × wealth` is a real contributor for
> rich AIs at high-tier lobby tables. v1 attractiveness is non-zero for casino
> tables (fish), whale tables, **and** high-prestige lobby tables (the climb).

#### Notes

- **`W_CROWD` is load-bearing.** Without it, every grinder dogpiles the single
  fish table — the same failure we have now, just *motivated* instead of blind.
  Penalizing each additional grinder yields an equilibrium where sharks spread
  across fish proportional to the meat.
- **This unifies the scattered mechanisms** into one scoring function: the
  comfort-zone adjacency gate, the hungry-grinder reorder (`lobby.py:1193-1216`),
  the affordable-predator reorder (`lobby.py:1217-1241`), and both `× 2` fill
  boosts all collapse into `attractiveness()`.

### 2. Plug-in surfaces (the unification)

The score feeds two decision points. The **pull** path is where "full
unification" lives, and per decision 1 it is a **loop inversion**.

#### Pull — table selection (the inversion)

**Today (table-centric):** `refresh_unseated_tables` (`lobby.py:517`) loops
over tables; for each it reorders the shared idle pool (hungry/predator-first,
`lobby.py:1192-1241`), sets `_effective_live_fill_prob`, and calls
`refresh_table_roster`. Inside, **Step 2** (`movement.py:1249-1378`) rolls
`rng.random() >= live_fill_prob` **per open seat** (`movement.py:1302`) and
takes the **first qualifying AI** from the pre-sorted pool
(`movement.py:1307-1346`). No AI ever compares tables; whichever table's roll
fires first, in iteration order, grabs the AI.

**Target design (AI-centric inversion — recommended).** Flip the loop so AIs
choose rooms instead of tables fishing for AIs. A **pre-pass** over the idle
pool, for each seat-seeking idle AI:

1. **Rank** every room by `attractiveness(room, ai)`.
2. **Filter** out rooms it can't access (can't afford) or that have no open
   seat (and the existing recovery/cooldown eligibility).
3. **Pick the best** remaining room (greedy `argmax`) and seat it there.

**Greedy, not a probability roll** (decision 2026-05-29 — legibility): you can
print the ranked list and read off exactly why an AI sat where it did. The
obvious objection — "won't they all pile into the same room?" — is answered by
the **`W_CROWD` term**: as a room fills, its attractiveness drops, so the next
AI ranks it lower and goes elsewhere. For that to spread them, **seat AIs one
at a time and recompute occupancy between picks** (sequential greedy);
ranking once and seating everyone against a stale ranking would herd. A
**weighted roll over the top-k** is an optional later flavor if pure greedy
feels too deterministic — not needed for v1.

**Fill rate is a separate knob.** A **seek-rate** (per-tick probability an idle
AI goes room-hunting) replaces `live_fill_prob` and controls how fast tables
fill / how packed the world feels — independent of the greedy *choice*. This is
the direct fix for today's 5% sparseness: turn the rate up for fuller tables
without changing how AIs choose.

This inversion is the only shape where a juicy table actually *out-competes* a
dead one for the same AI. It **replaces**:

| Mechanism | File:line | Replaced by |
|---|---|---|
| `live_fill_prob × 2` (casino) | `lobby.py:1196` | attractiveness scales the pull directly |
| `live_fill_prob × 2` (whale table) | `lobby.py:1222` | same |
| hungry-grinder reorder | `lobby.py:1197-1216` | `hunger × fish_here` term |
| affordable-predator reorder | `lobby.py:1220-1241` | fish/whale chip draw + crowd |
| per-seat first-qualifying scan | `movement.py:1301-1346` | AI-centric greedy best-available pick |
| comfort-zone staker gate | `lobby.py:728-781` | `stake_fit` (anchor ∩ band) |

The existing **affordability**, **idle-recovery** (`reseat_readiness`,
`movement.py:208`), and **cooldown** gates stay — attractiveness chooses
*among* eligible tables; those gates still decide *eligibility*.

**Minimal fallback (optional phase-1 step).** Keep the per-table loop; replace
the hungry/predator sort key with an attractiveness-ranked pick (best candidate
first) *within each table's* candidate scan (`movement.py:1307-1346`). Smaller
change, lands the scoring function and the term-tuning. **Limitation (must be
documented in code):** tables still compete by iteration order + per-seat
Bernoulli, so a dead table iterated first can still grab an AI a juicy table
wanted — attractiveness biases *who fills* but doesn't fully bind *which table
wins*. Treat this as a stepping stone to the inversion, not the end state.

#### Push — leave pressure

Add an attractiveness term to `compute_leave_pressure` (`movement.py:164`): a
grinder at a juicy table gets *reduced* leave pressure ("why leave, I'm
printing"); one at a dead all-shark table gets *more* pressure to go find
fish. Keep the existing four sources (`short` 0.6, `stake_up` 0.5, `detached`
0.3, `tenure` 0.2; `LEAVE_K` 2.0) intact.

> **Routing (new requirement).** `evaluate_ai_movement` routes a leave by
> `dominant = max(pressures)` (`movement.py:261`). A new "dead-table" term must
> route to **`bored_move`** when dominant (go find a better table) — otherwise
> the decision is undefined. Add it to the same family as `detached`/`tenure`
> in the routing (`movement.py:270`).

### 3. Fish restriction — **SHIPPED**

`_coerce_fish_movement` (`movement.py:273`) already pins fish:
`stake_up`/`bored_move` → `stay`; `take_break`/`forced_leave` → `rebuy` while
the pool-funded bankroll can fund a buy-in; storm-off only when
`emotional_intensity ≥ FISH_TILT_LEAVE_THRESHOLD` (0.5). The only clean exit is
bust (`forced_leave`, stack ≤ `0.3 × min_buy_in`). No further work; see
`CASH_MODE_FISH_AS_PERSONAS.md`.

### 4. Fish seat reservation — **SUPERSEDED**

The original "reserve seats from grinder live-fill + flip refill ordering"
fix is no longer the plan. Baseline sim (2026-05-25) showed that after the
eph-fix, casinos **self-heal** to the designed fish/grinder mix; fish refill
runs via `resolve_casino_provisioning` (called at `lobby.py:2182-2199`, no
longer gated behind `vice_mode`), and the **casino dam** (laddered open
`CASINO_SPAWN_THRESHOLDS` `casino_provisioning.py:67` + pool-floor wind-down
`CASINO_CLOSE_THRESHOLDS:94`) + predator retention now govern the mix.
Reservation is **not needed**; the original "grinders crowd fish out" was the
wedged split-brain, since fixed. Left here as a record of the superseded plan.

### 5. Whale activation — **SHIPPED (redesigned)**

The whale is built as a **pool-depth relief valve at a lobby table**, not the
casino. `resolve_whale_provisioning` (`casino_provisioning.py:1509`, called at
`lobby.py:2208-2226`): one whale at a time, spawned highest-eligible-stake
first when `pool ≥ WHALE_POOL_THRESHOLDS` (`casino_provisioning.py:164`),
recalled when `pool < WHALE_POOL_FLOORS` (`:176`). The deep prefund
(`_fish_prefund(whale=True)`, 10–18× max buy-in, `:611-612`) is the drain;
grinders are pulled to farm it (`list_affordable_predators` reorder, the one
remaining whale-table seating heuristic — which the §2 inversion subsumes).
Emits a `world_event` ticker. No further work; see
`CASH_MODE_WHALE_AT_CARDROOM.md`. **Note:** which table a whale spawns at is
pool-driven (highest open eligible stake), *not* attractiveness-driven —
out of scope for v1.

## Touch points (v1)

| File | Change | Status |
|---|---|---|
| `cash_mode/movement.py` | new `table_attractiveness()` / `stake_fit()` / `hunger()` / `wealth()` / `wealth_over_tier()`; **base_attractor** = stake_fit + climb; wealth term in the `stake_up` source (`:173`); dead-table term in `compute_leave_pressure` (`:164`) routed to `bored_move` (`:270`); `_coerce_predator_retention` prestige override (`:309`); **AI-centric greedy table-selection pre-pass** (sequential, recompute occupancy between picks) replacing the per-seat scan (`:1301-1346`); **seek-rate** knob replacing `live_fill_prob` | new |
| `cash_mode/lobby.py` | **drop** the `×2` boosts + hungry/predator reorders (`:1192-1241`); feed the new pre-pass | new |
| `cash_mode/lobby_config.py` | optional `prestige` field on `LobbyTableEntry` (`:36`); v1 leaves unset → tier-derived | new |
| `cash_mode/closed_economy.py` | attractiveness weights (`W_FISH`/`W_WHALE`/`W_CROWD`/`W_HUNGER`/`BASE_DRAW`), room-prestige weights (`W_CLIMB`/`W_SLUM`/`PRESTIGE_RETENTION_OVERRIDE`), `room_prestige`/`wealth`/`wealth_over_tier` helpers + constants, affordable-band `N`, hunger curve | new |
| `cash_mode/bankroll.py` | derive affordable stake band from bankroll + `stake_comfort_zone` anchor (knobs already load here) | new |
| `cash_mode/casino_provisioning.py` | whale relief valve — `resolve_whale_provisioning`, `WHALE_POOL_THRESHOLDS`/`FLOORS`, `_fish_prefund(whale=True)`, world_event | ✅ SHIPPED |
| `cash_mode/movement.py` (`_coerce_fish_movement`) | fish stay/reload-until-bust + storm-off | ✅ SHIPPED |

**Not touched in v1:** `relationship_states` / `schema_manager.py` (no
migration), `relationship_repository.py`, `opponent_model.py`,
`cash_mode/social_prestige.py` (not created), `ticker_service.py`,
`cash_mode/tables.py` `seated_at` stamp, and the frontend marquee badge — all
were occupant-prestige plumbing, now deferred.

## Validation (sim, v1)

Run the cash economy sim (`cash_mode/sim_runner.py`) and assert:

1. **Fish stay seated.** Casino tables hold ≥ `CASINO_FISH_MIN` fish the large
   majority of ticks. *(Mostly a regression guard — shipped.)*
2. **Grinders cluster on fish, not each other.** Grinder-per-fish ratio
   converges; no table goes all-grinder while fish idle with bankrolls. The
   `W_CROWD` term is what makes this hold.
3. **The rich climb.** Seed several AIs with bankrolls ≫ a mid tier (the
   300k-at-$50 case). Over N ticks they generate `stake_up` pressure, graduate
   past fish-retention, and the `$1000` Pit goes empty → populated — **without
   dragging the broke up** (low-`wealth` AIs stay anchored near
   `stake_comfort_zone`). Also assert the **hoarding-bug co-benefit**: a
   winning, high-energy, rich grinder releases via the prestige override.
4. **Whale drains the pool.** Seed a bloated pool; whale spawns fire and pool
   reserves trend toward the floor. *(Regression guard — shipped.)*
5. **Chip conservation holds.** Audit drift stays flat across the new paths
   (greedy selection, prestige override) — guard against the
   `vice_spending` / fish-accounting class of leaks.

## Open questions (v1, tuning-only)

The structural decisions are settled (see "Refinement decisions"). What
remains is sim-tuning:

- **Weights** — `W_FISH`/`W_WHALE`/`W_CROWD`/`W_HUNGER`/`BASE_DRAW`, `W_CLIMB`.
- **Curves** — affordable-band `N` and `stake_fit` taper sharpness; `hunger`
  shape (linear in `1 − bankroll/starting`, or steeper near broke; reuse the
  0.8 knee?); `room_prestige` curve (linear vs squared); the multiple-of-tier
  knee for `wealth`/`wealth_over_tier` (how rich is "rich enough" to feel the
  pull and override retention, `PRESTIGE_RETENTION_OVERRIDE`).
- **`W_SLUM`** — how hard wealth-over-tier pushes a parked rich AI to leave.
  Too high → the rich never settle below the Pit; too low → they stay stuck.
- **`WHALE_POOL_THRESHOLDS`** — already shipped; revisit if the relief cadence
  is off.
- **Net worth vs bankroll for `wealth(ai)`** — v1 uses bankroll; revisit if
  staker-heavy AIs should feel the pull.

## Build order (v1)

1. **Core `attractiveness()` + `stake_fit()` + `hunger()`** and the §2
   selection change (minimal in-table weighting → AI-centric inversion). The
   prerequisite for everything.
2. **Room prestige** — `room_prestige` (static, no new state) + `wealth` /
   `wealth_over_tier`, wealth-driven `stake_up`, retention override.
3. **(optional) surface the climb to the human** — a lobby cue when a
   high-roller takes a seat at the Pit (reuses the existing ticker).

---

## Deferred to v2

### Occupant / social prestige (the marquee layer)

> **Status: deferred (2026-05-29).** A genuinely good idea — *"I want to play
> where the respected sit"* — but its complexity (a projection of the shared
> `respect` axis, a new aggregate stat, a schema migration, and a cluster of
> saturation controls) is disproportionate to v1, and it hurts legibility.
> Build it *after* the core attractiveness + room-prestige v1 is proven and we
> actually feel the lack of a marquee. **When we build it, give it its own
> first-class `renown` stat** (one number, one meaning, watchable as it ticks)
> rather than projecting `respect` — see "Why a dedicated stat" below.

**The concept.** A *second, non-EV attractor*, distinct in kind from the
money pulls: a status-seeking cohort chases tables where respected figures
sit, the longer they hold court the more the table becomes an "institution."
Marquee tables and fish tables coexist; grinders keep chasing EV. The human is
in the graph — their reputation pulls AIs to their table ("🔥 the big game").

**Why a dedicated `renown` stat, not a `respect` projection.** The original
design read the directed `relationship_states` graph
(`(observer_id, opponent_id, {heat, respect, likability})`,
`schema_manager.py:594-606`) and aggregated *inbound regard* into prestige.
That entangles prestige with an axis the betting, staking, and forgiveness
systems already consume — and at *different thresholds*, so the same stored
`respect` value would mean three different things. A standalone `renown`
counter, incremented by a clear rule (e.g. "+renown for a big clean win at a
table; slow decay"), is far easier to reason about and debug, and decouples the
marquee from the staking economy entirely.

**Respect-cluster context (for whoever builds this).** `respect` is read below
the betting gate by several `cash_mode` systems, so it is **not** a free axis
to repurpose:
- betting/exploitation — `relationship_modifier.py:137` (`respect > 0.7` →
  `fold_to_pressure_mult 0.7`);
- sponsorship — `sponsor_offers.py` (`> 0.5`, `> 0.6`, tier floors `:574-603`);
- AI staking — `movement.py:758`, `player_staking.py:317-319`;
- forgiveness scoring — `ai_carry_resolution.py` (continuous:
  `likability·0.5 + respect·0.4 − heat·0.3`).
The current `respect` default (`0.5`) and these floors (`0.5`/`0.6`) are
**untuned starting values** (pre-release, single player) — tune the whole
cluster together if the social layer ever wants to move them. This is *the*
reason a dedicated `renown` stat is cleaner than projecting `respect`.

**If we nonetheless reuse `respect`:** read it as *deviation from neutral*
(`max(0, respect − θ)`, `θ` per axis) so neutral acquaintances confer ~0; keep
the projection inside the prestige read (do **not** lower the global default,
which would shift sponsorship/staking/forgiveness availability); keep `[0,1]`
storage (a signed −1:1 rescale re-keys every consumer — too much blast radius).

**Model sketch (preserved):**
```
regard(o→p)        = W_RESPECT × renown_of(respect/regard)  + W_LIKE × …
social_prestige(p) = squash( Σ_{o knows p, in sandbox} weight(o) × regard(o→p) )
tenure_mult(occ)   = 1 + P_TENURE × (1 − exp(−hands_here / TENURE_SCALE))
occ_prestige(table)= max_seat( prestige(occ) × tenure_mult ) + P_LINEUP × Σ_others(…)
base_attractor    += W_MARQUEE × occ_prestige(table) × status_appetite(ai)
```
- `status_appetite(ai)` — derive from confidence/ego/chattiness traits
  (glory-hunters), not a new config field.
- `weight(o)` — flat to start; "respected by the respected" (PageRank flavor)
  is a later refinement and **must ship with damping** so cliques can't
  bootstrap unbounded.
- Sandbox scoping — `relationship_states` has no `sandbox_id` today; a
  `renown` stat can be sandbox-scoped from the start, sidestepping the
  migration the `respect`-projection would have needed.
- Compute on the world ticker (`_maybe_record_holdings_snapshot`,
  `ticker_service.py:233`, 600s); `tenure_mult` derived live from a `seated_at`
  seat stamp (`tables.py` JSON blob, no migration).

**Gravity wells to design against (grounded in the relationship event deltas,
`relationship_events.py`).** A winning reg gains ~+0.08 inbound respect per big
clean pot (`BIG_LOSS` mirror) and `respect` doesn't decay → three
positive-feedback loops a naïve `Σ` aggregator would suffer:
- **Ceiling-compression** — no decay + a narrow band + `Σ` pegs every active
  winner at the cap and stops discriminating. *Levers:* a low earned base, a
  `log1p(Σ)/log1p(SCALE)` squash (preferred — simple), and/or recency decay.
- **Marquee flywheel** — `occ_prestige` pulls the cohort → they keep the
  headliner company → tenure climbs → more pull. *Bounds:* the 6-seat physical
  cap and a saturating `tenure_mult`; add a congestion falloff (reuse the
  `W_CROWD` idea) only if sims show herd collapse.
- **PageRank runaway** — only if `weight(o)` becomes recursive; ship with
  damping.
*Validation must explicitly track* prestige histograms, top-player saturation
rate, and table-occupancy concentration.

**Property to preserve, not "fix".** `STACK_DOMINANCE` *subtracts* respect
(−0.002/hand envy drip, `relationship_events.py:201`), so a deep stack *loses*
social standing with envious peers. This keeps **room-prestige (wealth) and a
future social-prestige genuinely orthogonal** — wealth doesn't buy a marquee.

### Other v2 items

- **Personal human-keyed terms** — the chaser's own *outbound* affinity/rivalry
  toward a specific seated player (vs a global aggregate), the human's
  win/loss streak as motivator vs avoidance, and expected edge from history vs
  known opponents. Built on the relationship layer
  (`CASH_MODE_AND_RELATIONSHIPS.md`).
- **Table afterglow** — a departed headliner's table keeps cachet briefly.
- **Net-worth wealth signal** — swap `projected_bankroll` for true net worth in
  `wealth(ai)`.

---

Builds on `CASH_MODE_FISH_AS_PERSONAS.md` (fish = permanent personas),
`CASH_MODE_WHALE_AT_CARDROOM.md` (whale relief valve), and
`CASH_MODE_AND_RELATIONSHIPS.md` (the relationship graph — relevant to the
deferred social layer).

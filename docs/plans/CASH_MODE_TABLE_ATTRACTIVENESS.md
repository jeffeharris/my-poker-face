---
purpose: Give cash tables an AI-facing attractiveness score so grinders hunt where the fish/whales are, the rich are drawn up to prestigious high-stakes rooms, a status cohort chases marquee tables built on the social standing of who's sitting there, restrict fish movement, and turn whales into a pool relief valve
type: spec
created: 2026-05-25
last_updated: 2026-05-26
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
      base_attractor(ai, table)                   # BASE — stake_fit + prestige bend (below)
    × (1 + W_HUNGER × hunger(ai) × fish_here)     # low bankroll → fish pull harder
    × ( W_FISH  × Σ(fish seat chips)
      + W_WHALE × Σ(whale seat chips)             # whales weighted higher AND
      + BASE_DRAW )                               #   deeper-stacked → dominate
    − W_CROWD × (other grinders at table)         # self-balancing term

base_attractor(ai, table) =
      stake_fit(ai, table_stake)                       # personality anchor ∩ affordable band
    + W_CLIMB   × room_prestige(table)  × wealth(ai)          # the rich are pulled UPWARD
    + W_MARQUEE × occ_prestige(table)   × status_appetite(ai) # chase where the respected sit
```

`room_prestige` is the **static, tier-derived** draw (below); `occ_prestige`
is the **earned, social** draw built from who's sitting there (the
"Occupant prestige" section). The two are complementary: room prestige
**seeds** a cold table and pulls the rich to climb; occupant prestige is
the **flywheel** that compounds once respected figures sit and hold court.

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

#### Room prestige: wealth bends the band upward

Prestige has **two layers**. This one — **room prestige** — is the
*static, tier-derived* glamour of the venue itself (the `$1000` High
Roller Pit is a draw because it's the Pit). It pulls the *rich* upward
and seeds a cold table. The second layer — **occupant prestige**, the
earned social standing of who's actually sitting there — is the section
after this one. Room prestige is the seed; occupant prestige is the
flywheel.

The model so far is *lateral* — find your comfortable stake, then chase
the juiciest table at it. Nothing pulls a rich AI to climb. **Observed
failure (2026-05-25):** the `$1000` "High Roller Pit" (the single top
table, `lobby_config.py:62-64`) sat empty while several AIs held
**300k+ net worth**. Affordability wasn't the gate — a 300k AI clears
the $1000 min buy-in (40bb = 40k) trivially. The gaps:

1. **`stake_up` reads the seat stack, not wealth** (`movement.py:149`,
   `stake_up_raw = ai_chips/max_buy_in − 1`). A 300k AI sitting on a 5k
   stack at $50 generates *zero* climb pressure. Bankroll only gates
   whether it *can* move up (`movement.py:209-212`); it never *wants* to.
2. **One tier at a time, by lottery.** Climbing $2→$1000 means winning
   the leave-pressure roll with `stake_up` dominant *four* times in
   sequence. Nothing routes the rich straight up.
3. **Predator retention pins the rich low** (`movement.py:253-282`):
   any grinder at a table holding a fish has `stake_up`/`bored_move`
   coerced to `stay`. The wealthier an AI (the more it out-farms fish at
   mid-stakes), the *more* it's nailed in place. The rich are the most
   stuck.

Prestige fills that gap as an **aspirational pull layered on the base
attractor** — it does *not* gate (decision: **pure soft pull**, no
net-worth floor; the normal buy-in window stays the only hard
affordability check). Three pieces:

**(a) `room_prestige(table)` — a per-room draw value.** Defaults to a
tier-normalized scalar derived from `STAKES_ORDER` index (`$2`≈0 →
`$1000`≈1; curve it so the top stands out, e.g. squared), with an
**optional per-room override** in `lobby_config.py` (`LobbyTableEntry`
gets a `prestige` field) so two same-stake rooms can differ in flavor
("The Lodge" classier than "Tuesday Night Reg"). v1 ships tier-derived;
the override is the hook, not required.

**(b) `wealth(ai)` — who feels the pull.** Continuous 0→1, rising as the
AI's wealth exceeds a multiple of the *current* tier. So the prestige
term only fires for the genuinely rich, and the product
`prestige × wealth` is meaningful only at high tiers held by wealthy AIs
(a broke AI at $2 has `wealth≈0` → no prestige distortion; the model
reduces to the plain anchor). Because `wealth` bends the *base
attractor* — which `stake_fit` would otherwise taper toward zero far
above the personality's static `stake_comfort_zone` anchor — the rich
get drawn *above* their anchor, while the anchor still shapes everyone
else. Character preserved; the wealthy graduate.

> **Wealth signal (v1 decision):** use `projected_bankroll` (already in
> `MovementContext`) as the wealth proxy, not full net worth. Net worth
> (`holdings_view`, chips + receivable − outstanding) is the "truer"
> figure the 300k symptom was quoted in, but plumbing receivable/
> outstanding into the movement hot path is added cost for a number that
> ≈ bankroll for everyone except big stakers. **v2:** swap in net worth
> if staker-rich AIs need to feel the pull too.

**(c) Push + retention override.** The pull above governs *which* table
an idle AI targets, but a rich AI already seated and content never
enters the idle pool. Two complementary edits:

- **Wealth-driven `stake_up` pressure.** Add a wealth term to the
  `stake_up` source in `compute_leave_pressure` (`movement.py:149`):
  `stake_up_raw = max(ai_chips/max_buy_in − 1, W_SLUM × wealth_over_tier(ai))`.
  A 300k AI at $50 (max buy-in 5k) is ~60× over tier → strong "I'm
  slumming it" pressure → it leaves → idle pool → prestige routes it up.
- **Prestige beats fish-retention for the truly rich** (decision:
  *prestige wins eventually*). In `_coerce_predator_retention`, lift the
  `stake_up` suppression once `wealth_over_tier(ai) ≥
  PRESTIGE_RETENTION_OVERRIDE`: fish still hold ordinary winners, but a
  bona-fide high-roller graduates rather than babysitting small fish.
  Keep `bored_move` suppression intact — the rich don't wander
  *sideways*, they go *up*.

**Cold start (decision: none).** No targeted seeding of an empty
high-prestige table. The Pit fills organically via the pull + wealth
stake-up pressure. Tradeoff acknowledged: on a fresh world (or right
after the rich get unstuck) the Pit may stay empty for a while before
the first qualifying AI climbs all the way up. Revisit only if sim shows
fill is unacceptably slow.

#### Occupant prestige: social standing (the marquee layer)

> **Status (2026-05-26): LOCKED, parked (sleeper).** The
> relationship-derived model below is the settled design — prestige =
> earned social standing from the `relationship_states` graph, *not*
> wealth/stakes/celebrity-config. **Not building now:** it rides on the
> still-unbuilt core `attractiveness()`/`stake_fit()` layer (the real
> prerequisite), so it sits as a sleeper until that lands. Everything
> still open is **tuning-only** (weights, `heat` sign, `status_appetite`
> source) — the *model* is not up for revisit.

A *second, non-EV attractor*. Everything else in the economy pulls on
**money** (fish draw grinders; the whale drains the pool; room prestige
pulls the rich). Occupant prestige is different in kind — **"I want to
play where the respected sit"** — social, aspirational, not +EV. It's
what makes a real high-stakes game glamorous: the Big Game draws
gamblers who want to *say they played it*, not just sharks hunting the
soft seat. So it's a **distinct axis** (decision: only a
**status-seeking cohort** responds — grinders keep chasing fish/EV;
marquee tables and fish tables coexist), not folded into the fish term.

**Prestige = earned social standing** (decision: derived from the
relationship layer — "the likability and respect of the others who know
them"), **not** wealth/stakes/celebrity-config. The relationship layer
already carries the substrate: `relationship_states` is a **directed**
graph keyed `(observer_id, opponent_id)` with axes
`{likability, respect, heat}` (`relationship_repository.py:5,89`) —
*observer's view of opponent*. A person's prestige is their **inbound
regard**: aggregate over everyone who knows them.

```
regard(o→p)   = W_RESPECT × respect(o→p) + W_LIKE × likability(o→p)   # projected/decayed values
social_prestige(p) = saturate( Σ_{o knows p} weight(o) × regard(o→p) )
```

- **Respect-weighted** (`W_RESPECT > W_LIKE`): standing is more about
  being respected than liked. `heat` (conflict/rivalry intensity) is
  **neutral in v1** — a feared rival arguably carries a *dark* prestige;
  whether heat adds or subtracts is an open fork (below), not a v1
  commitment.
- **`weight(o)` — whose opinion counts.** v1: **flat** (`weight(o)=1`)
  to bootstrap. v2: `weight(o) = social_prestige(o)` from the *previous*
  tick — "respected by the respected" matters more (eigenvector/
  PageRank flavor). Using the prior tick's value sidesteps a fixpoint
  solve; it's recomputed every world tick anyway, so it converges.
- **`saturate`** keeps it 0→1 so a handful of strong regards don't blow
  it up; `Σ` (not avg) rewards **breadth × depth** — known by many *and*
  regarded highly — which is what "prestige" means.

**Bootstrapping is a feature, not a bug.** A brand-new persona (or one
nobody has sat with) has no inbound edges → prestige ≈ 0 → it must
*earn* standing through play. That's correct, but it means social
prestige **can't cold-start an empty Pit** — which is exactly why
**room prestige seeds and occupant prestige is the flywheel**: the room
is glamorous on its own and pulls the rich up via the climb term; once a
respected figure sits and holds court (`tenure_mult` below), occupant
prestige compounds and pulls the status cohort in.

**Table-level rollup** — *headliner-dominant* so one revered figure
beats a table of six unknowns, and amplified by **tenure** (the longer a
respected figure holds court, the more the table becomes an
*institution* — the original ask):

```
tenure_mult(occ, table) = 1 + P_TENURE × (1 − exp(−hands_here / TENURE_SCALE))
occ_prestige(table)     = max_seat( social_prestige(occ) × tenure_mult(occ, table) )   # the headliner
                        + P_LINEUP × Σ_others( social_prestige × tenure_mult )         # a stacked lineup adds a little
```

`tenure_mult` starts at 1 the moment they sit, climbs fast, saturates at
`1 + P_TENURE`. It needs the **one piece of new state**: a `seated_at`
(or `seated_hand`) stamp on the seat slot — *free*, since seats are a
JSON blob (`tables.py:115`), **no migration**. When the headliner
leaves, `occ_prestige` drops (it's a live sum); v2 could add an
**afterglow** so a table that *was* the big game keeps cachet briefly.

**The human is in the graph.** AIs' regard for the human is already
updated from play, so the human accrues social prestige the same way —
their **reputation literally pulls AIs to their table**. Surface
`room_prestige + occ_prestige` in the lobby as a "🔥 the big game"
marquee badge + a ticker event ("the big game is forming at the High
Roller Pit") so the human chases the action too. Plugs straight into the
existing rivalry-seek seating.

**Personal regard (complement, likely v2).** Global `social_prestige` is
"a Big Deal *to everyone*." Distinct from it is the chaser's **own
outbound** edge to a seated player — "*I* admire / rival this specific
person" — which should also pull that AI toward that table (affinity/
rivalry-seek, which the relationship layer already supports). v1 ships
the global aggregate; the personal pull is the natural v2 extension.

**Plumbing.** One new read: inbound regard (`WHERE opponent_id = ?`, or a
single grouped pass over the sandbox's relationship rows per tick — the
repo today only exposes *outbound* `load_all_relationships(observer_id)`,
`relationship_repository.py:176`). Compute on the existing world ticker
(where holdings snapshots already run); **sandbox-scoped** (reputation
within your world). `status_appetite(ai)` gates *who* chases it — derive
v1 from confidence/ego-ish traits (glory-hunters), optional config later.

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
- **v2 (deferred):** the *personal* human-keyed terms — the chaser's own
  outbound affinity/rivalry toward a specific seated player (vs the
  global `social_prestige` aggregate, which IS v1), the human's
  win/loss streak (motivator vs avoidance), and expected edge from the
  grinder's history vs known opponents. The relationship layer already
  exists (`CASH_MODE_AND_RELATIONSHIPS.md`), so this is additive, not
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
| `cash_mode/movement.py` | new `table_attractiveness()` (base_attractor × hunger × fish/whale − crowd); `stake_fit()` band; `hunger()` curve; **`base_attractor` = stake_fit + climb (room_prestige×wealth) + marquee (occ_prestige×status_appetite)**; **`wealth(ai)` + `wealth_over_tier(ai)` curves**; **`status_appetite(ai)`**; **wealth term in the `stake_up` source of `compute_leave_pressure`**; attractiveness term in `compute_leave_pressure`; **`_coerce_predator_retention` override at `PRESTIGE_RETENTION_OVERRIDE`**; coerce fish `take_break → rebuy`; **weighted-probability** live-fill target selection (replaces oldest-first) |
| `cash_mode/lobby.py` | drop `×2` casino hack (`1054-1057`) **and** the hungry-grinder reorder (`1058-1079`) — both subsumed by `attractiveness()`; flip refill-before-live-fill ordering at casinos; fish seat reservation; **stamp `seated_at` on the seat slot at sit-down; recompute `social_prestige` on the world ticker** |
| `cash_mode/lobby_config.py` | add optional `prestige` field to `LobbyTableEntry` for per-room overrides (v1 leaves it unset → tier-derived) |
| `cash_mode/tables.py` | **add `seated_at`/`seated_hand` to the seat slot dict (JSON blob — no migration); `tenure_mult` helper** |
| `poker/repositories/relationship_repository.py` | **new inbound read — aggregate regard `WHERE opponent_id = ?` (or one grouped pass per sandbox) to feed `social_prestige`** |
| `cash_mode/social_prestige.py` *(new)* | **`regard(o→p)`, `social_prestige(p)` rollup (saturate(Σ weight·regard)), `occ_prestige(table)` headliner-dominant + tenure; sandbox-scoped; flat regarder-weight v1 → prestige-weighted v2** |
| `cash_mode/casino_provisioning.py` | pool-depth whale trigger; pass `whale=True` to `_fish_prefund`; whale `world_event` emit |
| `cash_mode/closed_economy.py` | `WHALE_POOL_THRESHOLD`, attractiveness weights (`W_FISH`/`W_WHALE`/`W_CROWD`/`W_HUNGER`), **`W_CLIMB`/`W_MARQUEE`/`W_SLUM`/`PRESTIGE_RETENTION_OVERRIDE`**, **`room_prestige(table)` helper (tier-normalized + per-room override) + `wealth`/`wealth_over_tier` + `status_appetite` constants**, **`W_RESPECT`/`W_LIKE`/`P_TENURE`/`TENURE_SCALE`/`P_LINEUP` social-prestige weights**, affordable-band `N`, hunger curve constants |
| `cash_mode/bankroll.py` | derive affordable stake band from bankroll + `stake_comfort_zone` anchor (knobs already load here) |
| frontend lobby | **"🔥 the big game" marquee badge from `room_prestige + occ_prestige`; ticker event on a marquee table forming** |

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
5. **The rich climb.** Seed several AIs with bankrolls ≫ a mid tier
   (the 300k-at-$50 case). Over N ticks, assert they generate `stake_up`
   pressure, graduate past fish-retention, and the `$1000` High Roller
   Pit goes from empty to populated — without dragging the broke up with
   them (low-`wealth` AIs stay anchored near `stake_comfort_zone`).
6. **The marquee forms.** Seed a respected figure (high inbound regard)
   into a table; over N ticks assert `occ_prestige` rises with their
   tenure, status-appetite AIs cluster there, and `social_prestige`
   tracks the relationship graph (a persona others respect scores high;
   an unknown scores ~0 until it earns regard). Grinders **don't**
   abandon fish tables to chase the marquee (the two axes stay distinct).

## Open questions

- Exact `W_FISH` / `W_WHALE` / `W_CROWD` / `W_HUNGER` weights — tune in sim.
- Affordable-band `N` (buy-ins of cushion to consider a tier "affordable")
  and how sharply `stake_fit` tapers away from the anchor∩band peak.
- `hunger(ai)` curve shape — linear in `1 − bankroll/starting`, or steeper
  near broke? Reuse `GRINDER_HUNGER_THRESHOLD` (0.8) as the knee?
- `WHALE_POOL_THRESHOLD` — multiple of normal operating pool; and whether
  it's per-sandbox or global.
- `W_PRESTIGE` strength vs `W_FISH`/`W_WHALE` — prestige must out-pull a
  juicy lower table for a *rich* AI without making *everyone* abandon
  fish. The `wealth(ai)` gate should keep this clean, but tune in sim.
- `prestige(table)` curve — linear in tier index, or convex (squared) so
  the top room dominates? And the multiple-of-tier knee for `wealth(ai)`
  / `wealth_over_tier(ai)` (how rich is "rich enough" to feel the pull
  and to override retention, `PRESTIGE_RETENTION_OVERRIDE`).
- `W_SLUM` — how hard wealth-over-tier pushes a parked rich AI to leave.
  Too high and the rich never settle anywhere below the Pit; too low and
  they stay stuck (today's bug).
- Net worth vs bankroll for `wealth(ai)` (v1 uses bankroll; see decision
  box in §1) — revisit if staker-heavy AIs should feel the pull.
- **`W_RESPECT` : `W_LIKE` split** in `regard(o→p)` — how much standing is
  respect vs being liked.
- **Does `heat` count toward prestige?** Neutral in v1. Fork: a feared
  rival carries a *dark* prestige (heat adds), or heat is purely
  conflict and should subtract / stay out. Tune in sim.
- **`status_appetite(ai)`** — derive from which traits (confidence, ego,
  chattiness)? Dedicated config field vs derived. And how sharply
  `W_MARQUEE` must out-pull fish/whale draw for the *status cohort*
  without the cohort ever abandoning real EV.
- **Flat vs prestige-weighted regarders** (`weight(o)`) — when to turn on
  the recursive "respected-by-the-respected" weighting (v2), and whether
  it stays stable tick-over-tick.
- **`P_TENURE` / `TENURE_SCALE`** — how fast a table becomes an
  "institution," and whether a departed headliner leaves an **afterglow**
  (v2) or `occ_prestige` drops immediately (v1).
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

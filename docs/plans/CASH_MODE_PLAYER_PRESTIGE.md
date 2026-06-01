---
purpose: Design for a human-facing prestige/reputation system — an earned, persistent status axis with two poles (beloved legend ↔ infamous villain) that the world responds to, giving cash mode a career spine and sandbox replayability.
type: design
created: 2026-05-29
last_updated: 2026-06-01
---

# Player Prestige & Reputation

## The gap

Cash mode's only scoreboard is **bankroll**, and bankroll is volatile: once
you're rich there's nothing left to play *toward*, and a downswing erases your
sense of progress. There's no axis for *who you are at the table* — whether the
room respects you, fears you, likes you, or can't stand you.

This spec adds a **reputation** system for the human player: an earned,
**persistent** status axis that bankroll can't measure, and that **the world
responds to**. Crucially, it's symmetric — playing a **gracious champion** and
playing a **rude, mean villain** are *both* first-class paths, each with its
own rich set of world responses. That two-sidedness is the replayability hook:
a "villain run" in a fresh sandbox is a genuinely different game, not just a
lower score.

This is the human-facing keystone of the career arc (see
`CASH_MODE_CAREER_PROGRESSION.md`). The mirror feature — AIs having their *own*
prestige that pulls a status-seeking cohort to marquee tables — is the deferred
"occupant prestige" layer in `CASH_MODE_TABLE_ATTRACTIVENESS.md`; it becomes far
more compelling once the human is in the same graph (AIs flocking to — or
hunting — *you*), but it is **not** required for v1 here.

## The model: two axes, not one

A single signed "karma" scalar is tempting but wrong — it can't tell a
**feared villain** apart from a **disliked clown**, and it reduces the bad path
to "negative points" instead of its own achievement. Reputation is **2D**:

- **Renown** — *how much of a figure are you?* Magnitude/fame, largely
  behavior-agnostic, **ratchets** (slow/no decay — it's a career record). You
  build it by **impact**: reaching and sustaining high stakes, big pots,
  coolers delivered, beating respected opponents, tenure, how many AIs *know*
  you, mentoring a protégé. A beloved legend and an infamous villain are *both*
  high-renown. A newcomer is low-renown.
- **Regard** — *how does the room feel about you?* The valence, beloved ↔
  reviled, which **swings** with behavior. Warm regard = inbound `likability` +
  `respect`; hostile regard = inbound `heat` + low `likability`. This is where
  the hero/villain choice lives.

The substrate already exists. The `relationship_states` graph holds each AI's
view of the human as `{likability∈[0,1]@0.5, respect∈[0,1]@0.5, heat∈[0,1]@0.0}`
(`poker/memory/relationship_events.py`), updated by gameplay events
(`BIG_LOSS` → +heat/+respect/−likability, `STAKE_DEFAULTED` → big −, etc.).
**`heat` is one-sided and already the natural notoriety axis** — a mean player
racks it up across the graph. Reputation is an *aggregate read* over the
human's inbound edges + achievement events.

### The quadrants (and how the world responds)

|  | **Warm regard** | **Hostile regard** |
|---|---|---|
| **High renown** | **Beloved Legend** — AIs flock to your table (marquee pull); easy vouches/sponsorship; warm chat; protégés seek you. The aspirational path. | **Infamous Villain** — feared/disliked. Many AIs avoid you, but **rivals are drawn to take you down** (a "dethrone the villain" pull); chat turns hostile/needling; **backing dries up** (nobody stakes a jerk → harder economy, self-funded); AIs **tilt and play scared** around you (→ exploitable). A real, different game. |
| **Low renown** | **Up-and-comer** — the world is mildly warm but barely reacts yet; you're earning your name. | **Disliked nobody** — actively shunned, isolated; hardest mode. Climb out by either earning regard *or* leaning into infamy to build renown. |

The point: **both poles get equal, distinct mechanical + narrative responses.**
The villain isn't punished into a corner — they get a hostile-but-alive world
(rivals, fear, exploitable tilt, a self-funded grind) that's fun to play.

## What feeds each axis

Reputation updates from the **same events the relationship layer already
emits**, plus a few career/achievement signals — no new gameplay telemetry.

**Renown (ratchets up; behavior-agnostic):**
- Highest stake tier reached and *sustained* (sitting the `$1000` Pit is
  renown regardless of manners).
- Big clean pots / coolers delivered; multi-buy-in wins.
- Beating high-`respect` / high-renown opponents.
- Breadth: number of AIs with a strong edge to you (the room "knows" you).
- Tenure; mentoring a protégé to prestige (act-3, see career docs).

**Regard (swings; the hero/villain dial):**
- *Warm:* gracious wins, props, repaying stakes (`STAKE_REPAID`), good
  sportsmanship, hero calls → +likability/+respect.
- *Hostile:* berating/needling (the existing social-reactions / `bait` surface),
  rubbing in dominance (`STACK_DOMINANCE`), defaulting on stakes
  (`STAKE_DEFAULTED`), cruel coolers → +heat / −likability.
- Regard **partially decays** toward neutral as `heat` time-decays (see
  `project_heat`), so a reformed villain can climb back — but renown earned
  stays on the record.

> **Illustrative, not locked** (this is `type: design`):
> `renown = saturate( Σ achievement_weight + W_BREADTH·|known_by| )` (monotone)
> `regard = Σ_o w(o) · [ (likability_o − 0.5) + α·(respect_o − 0.5) − β·heat_o ]`
> Exact weights/decay are open questions below.

## Renown v2 — uncapped, continuous, achievement-aligned

> **Status (2026-05-29):** v1 shipped renown as a **capped [0,1] score** built
> from five placeholder drivers (breadth, tenure, stake-tier, beat-respected,
> high-stakes), each with a flat saturation cap. Playtesting exposed the caps
> as far too low and binary — breadth maxed at **12 AIs** (~11% of a 106-AI
> field); a single profitable high-stakes session maxed the "high-stakes" slice
> outright. This section is the agreed redesign. **Not yet built**; the capped
> v1 model is what currently runs.

Three decisions reframe the axis:

1. **Uncap renown.** Renown becomes a **lifetime points ledger** (the spec's
   original "uncapped scoreboard"), not a 0–100 bar. There is always more fame
   to earn. *Cascade:* the 0–100 renown gauge UI and the `renown ≥ 0.40`
   "high-renown" quadrant gate both have to change — "high renown" should be
   **relative to the field** (e.g. top-N or above a percentile of all entities'
   renown, which is self-scaling and AI-symmetric) rather than an absolute
   constant. The four world-response hooks gate on the quadrant, so they keep
   working once the quadrant is redefined.
2. **Continuous, not gated.** *Every hand moves the needle.* No "play 100 hands
   with an AI and you suddenly count" cliffs — each driver accrues smoothly
   (saturating per-unit functions), with impactful/rare actions weighing more
   per hand than grinding low.
3. **AI-symmetric where possible.** Renown should compute for AIs too (the
   deferred occupant-prestige layer in `CASH_MODE_TABLE_ATTRACTIVENESS.md`), so
   prefer drivers fed by **symmetric** data (`cash_pair_stats`,
   `holdings_snapshots`, stakes, `memorable_hands`) over human-only surfaces.

### The renown-source catalog

Tags: **data** ✅ exists / ⚠️ needs new tracking / 🔮 future; **sym** = AI-symmetric;
**ach** = the matching entry in `ACHIEVEMENTS_SYSTEM.md` (discrete cousin).

| Source | Continuous measure | data | sym | ach |
|---|---|---|---|---|
| ★ **Renown-weighted scalps** | players busted, **weighted by the victim's own renown** (busting a legend ≫ a nobody) | ⚠️ port | ⚠️ | `bounty`, `double_knockout` |
| ★ **Time at #1 net worth** | ticks spent atop the field's net-worth rank (+ peak net worth ever) — ratchets | ✅ | ✅ | `richest_in_room` |
| ★ **Kingmaker / backing** | volume + profit of stakes you've *backed* (a patron path; AI-to-AI staking already exists) | ✅ | ✅ | `backer`, `loan_shark`, `creditor` |
| ★ **Legendary hands** | rare/marquee hands (royal, quads, monster pots, coolers, hero calls) mint one-off renown nuggets | ✅ | ✅ | `royal_flush`, `monster_pot`, `hero`, `stone_cold_bluff` |
| **Recognition (breadth)** | per-opponent **hands-played volume**, summed across the field (not "met once") | ✅ | ✅ | `socialite` |
| **Stakes mastery** | hands played **per stake tier** (depth — e.g. credit for living at a level, big credit for volume at the top) | ✅ (human) | ⚠️ | `low_stakes_regular`, `stepping_up` |
| **Apex** | net-positive vs the whole roster | ✅ | ✅ | `apex_predator` |
| **Tenure** | total hands played (slow background floor) | ✅ | ✅ | `grinder`, `table_captain` |
| **Wealth milestones** | bankroll / lifetime-chips-won thresholds crossed | ✅ | ✅ | `high_roller` |
| **Comebacks / all-in survivals** | recover from the brink; survive shoves | ✅ | partial | — |
| 🔮 **Lineage** | a protégé you coached earning their *own* renown feeds back to you (mentor's cut) | 🔮 | n/a | — |
| 🔮 **Create-a-table / venue** | founding a room you host | 🔮 | n/a | — |

The four ★ are the agreed v2 core (a grinder, a whale, a patron, and a villain
each get a distinct route up). The rest are documented so the catalog is
complete and the registry can grow.

### How this aligns with the achievements system

Renown and `ACHIEVEMENTS_SYSTEM.md` **draw from the same fact surfaces and
trigger points** — `HAND` (busts, pots, rare hands), `STAKE_SETTLE` (backing),
and `CASH_STANDING` (net-worth rank, met/beaten counts). Achievements are the
**discrete milestone** view; renown is the **continuous score** the same events
feed. Don't build two parallel event pipelines — share them.

Bridge options (decide when building):
- **(A) Achievements grant renown** — each unlock awards renown points (tiered
  ones award more). Simple, punctuated bumps; rides the existing engine.
- **(B) Renown accrues continuously** over the same facts (each hand adds), with
  achievements as milestone markers on top. Matches "each hand moves the needle."
- **(Hybrid, recommended)** — continuous accrual for the core drivers (scalps,
  net-worth-time, backing, volume) **+** achievement unlocks mint one-off
  renown nuggets for *legendary* moments (royal flush, first $1000 seat). Smooth
  needle **and** punctuated spikes.

**AI-symmetry caveat:** the achievement engine is keyed by `owner_id` (human-only
today). The achievement→renown bridge is therefore human-only; **AI renown must
be computed directly from the symmetric fact sources** (as `compute_prestige`
already does), not via the achievement engine. So the continuous-accrual path is
the load-bearing one for symmetry; the achievement bridge is a human-side bonus.

### Known telemetry gaps (call-outs, not blockers)

- **Scalps need a durable, attributed counter.** The world tick runs the **full
  sim** (`cash_mode/full_sim.play_one_hand`, ~14 live tables), so real
  eliminations already happen (`HAND_EVENT_BUST`) — busts are *derivable* in-sim
  for AIs and humans alike. What's missing is **persisting "who busted whom"** as
  a durable per-entity counter: tournaments record it (`tournament_tracker.
  EliminationEvent` carries the *eliminator*) and `pressure_stats` counts it
  per-game, but the cash/world-sim path doesn't persist eliminator attribution
  yet. Wiring it serves both renown (scalps) and the `bounty`/`double_knockout`
  achievements, for the human **and** AIs. Lower priority than shipping the
  metric, per the product call. **Full spec: `CASH_MODE_SCALP_TRACKER.md`.**

### Pre-build balance validation (offline scorer) — Rung 1 done 2026-06-01

Because renown is a **read-side projection** over data the game already
produces, the formula's *balance* can be validated offline — against fixtures,
then the real DB, then a frozen sim log — with **zero production code** (no
migration, ticker, hooks, or UI). The plan is a four-rung ladder, cheapest
first, each a go/no-go gate:

1. **Rung 1 — synthetic archetype probe** (fixtures, no DB, no sim): do the
   four ★ routes *each* reach high renown, and does a volume bot dominate?
2. **Rung 2 — score the real field** (read-only DB snapshot): do the top names
   match intuition? (baseline-first sanity check)
3. **Rung 3 — weight sweep over a frozen sim log**: re-score one fixed log
   under a grid of weights; watch rank-order stability + the treadmill
   correlation. (Re-scoring a frozen log is a *perfectly paired* A/B — no RNG
   desync, unlike same-seed re-runs.)
4. **Rung 4 — build** A→B→C against the validated formula.

The instrument lives at `scripts/renown_v2_scorer.py` (pure stdlib, throwaway;
becomes the spec for the real `compute_prestige` v2 and the oracle for its
unit tests).

**Rung 1 result: PASS** — all four routes (grinder/whale/patron/villain) reach
high renown via *their own* signature driver, the control up-and-comer stays
low, and no single driver carries >85% of any score. The probe **failed twice
first**, and each failure is a design rule now baked into the spec:

- **Uncapping breaks any term that multiplies by another entity's renown.**
  v1's scalp weight `base + 1.6·victim_renown` was safe at `[0,1]` but explodes
  once renown is unbounded (one legend-scalp ≈ 70 pts; the villain hit 212 vs a
  ~30 field). **Rule: weight by the victim's *field percentile*, not raw
  renown** — bounded, outlier-robust, and "busting a big name" is correctly
  *relative* fame.
- **Pure top-X% gating manufactures fake stars** from a tourist-heavy field
  (top-30% labelled the up-and-comer and even the volume bot "high renown").
  **Rule: high renown = top-X% *AND* ≥ k×field-median.** The percentile caps
  *how many* figures exist (no star-inflation as renown ratchets up forever);
  the median multiple is a *self-scaling quality floor* (a weak field can't
  mint stars). Both inputs are field-relative — **no absolute constant**, which
  is precisely v1's `RENOWN_HIGH_THRESHOLD = 0.40` trap, now avoided.
- **Wall-clock denomination of volume drivers, quantified:** the same fixtures
  put the volume bot at #1 under hand-count, #6 under wall-clock. The
  anti-treadmill claim is no longer just an assertion.

Settled formula choices (spec-ready): uncapped lifetime ledger with every
driver **concave** (sqrt/log1p — unbounded but can't explode); scalp quality
`= base + scale·victim_field_percentile` with `log1p(count)` per victim (can't
farm one bot); the victim-percentile circularity resolved as **one refinement
pass over last-tick percentile** (verified not to need fixed-point iteration);
"high renown" `= max(top-20% boundary, 3×median)`.

**Rung 2 result (real field, 80-entity sandbox, `scripts/renown_v2_rung2.py`,
read-only `immutable=1`): structure validated, two weight issues flagged.** The
scaffolding holds on real data — cross-table ids join (after stripping the
`ai:`/`player:` prefix `holdings_snapshots` uses but `cash_pair_stats`/`stakes`
don't — a recurring trap), regard reproduces v1 exactly (the human reads
"Infamous Villain", regard −0.25 vs v1's −0.247), and quadrants are sensible
(warm high-renown AIs → Beloved Legend). Two balance findings that **fixtures
could not surface**, deferred to Rung 3's sweep (not hand-patched, to avoid
overfitting one sandbox):

- **Volume drivers let the most-active entity run away.** Hands-denominated,
  the human is #1 at 2.7× the #2, 71% from breadth alone. Confirms the
  wall-clock denomination / tighter breadth cap is load-bearing on real data,
  not just against the synthetic bot.
- **`w_backing` is too hot.** Real AI-to-AI staking volume dwarfs the Rung-1
  fixtures, so ~all AIs are 50–80% backing-driven — the AI field collapses to a
  single route. Scalps + legendary are absent on static data (no `cash_scalps`
  table / nugget log yet), so the villain/legendary routes can't be assessed
  until workstream A ships — reinforcing that the scalp tracker is the
  shippable prerequisite.

**Offline structural pass (2026-06-01) — both findings treated, and a deeper
root cause exposed.** Rather than hand-tune weights (overfitting one sandbox),
applied the Rung-1 rule — *uncapped → field-relative* — to the two heavy
drivers: backing and breadth now contribute `w · log1p(raw / field_median)`
instead of an absolute log. A median-relative log self-scales (median entity →
0.69, 10× → 2.4), and the raw/median *ratio* is ~denominator-robust, so the
hands-denominated offline read proxies the wall-clock design. Result: Rung 1
still PASSES (4 routes, no >85% dominance, anti-treadmill intact), and on the
real field the **human runaway halves** (84→54 renown; gap to #2 2.7×→1.26×)
and the **backing monoculture breaks** (backing shares 80–94% → 42–65%; the
field is now ~half breadth-led, half backing-led, with the one genuine outlier
patron still correctly backing-led). Lives in `renown_v2_scorer.py`
(`_relative` + `FieldContext`).

But the relativisation only treats the *scoring* symptom. The DB shows the
**backing economy itself is running hot**: top backers stake nearly the whole
field (deadpool 107 of ~113 borrowers; tyler_durden 96; ace_ventura 94), and
stakes are extended even at **default-neutral affinity**. Root cause is the
sponsor tier floors in `cash_mode/sponsor_offers.py` (`TIER_FLOORS`):
`premium = {lik 0.0, resp 0.0}` and `standard = {lik 0.4, resp 0.5}`, while a
stranger defaults to `0.5/0.5` — so a stranger clears both tiers and *anyone
backs anyone*. **Raising those affinity floors is a separate, sim-validated
backing-economy change** (it alters real chip flows + AI behaviour, not just a
read-side score), but it's the more fundamental fix: it slows the economy *and*
makes backing selective, so backing renown becomes a meaningful patron signal
rather than noise. Whether near-universal staking is a bug or intended
chip-cycling flavour is a product call — flagged with evidence, not assumed.

**Rung 3 harness built (2026-06-01) — machinery validated, real verdict pends a
sim capture.** Two-part, matching the "frozen log → paired re-scoring" design:
- `scripts/renown_v3_capture.py` produces a frozen-log JSON of every entity's
  renown inputs. `--from-db` (host, read-only) snapshots the real field;
  `--from-sim` (Docker) runs the rule-based cash sim (`full_sim.play_one_hand`,
  no LLM, no DB writes) over the sandbox's AI field, derives **scalps** via
  `cash_mode/scalps.eliminations_from_sim`, and overlays the play-derived
  drivers (scalps, volume, breadth, time-at-#1, peak stack) onto the DB field's
  economy/social drivers — the first log where the villain/scalp route is
  populated.
- `scripts/renown_v3_sweep.py` (pure) re-scores the frozen log under a 23-config
  weight grid and reports **(Q1) rank stability** — mean pairwise Spearman of
  the ranking + Jaccard of the figure set — and **(Q2) the treadmill
  correlation** — Spearman(renown, hand_count) vs Spearman(renown,
  performance_drivers).

Validated on a `--from-db` log: **Q1 strong** (mean rankρ=0.997 across all
perturbations — the ranking is robust to weights; only the gate knobs
`cut_median`/`w_breadth`/`w_backing` move the figure *set*, as designed). **Q2
correctly returns N/A** on a scalp-less log (the performance proxy is gutted
without scalps — which itself confirms scalps are load-bearing for the
anti-treadmill property). The real Q2 verdict needs the `--from-sim` capture
(Docker), where scalps are populated:
`docker compose run --rm --no-deps -v $PWD/scripts:/app/scripts backend python3 scripts/renown_v3_capture.py --from-sim --hands 350 -o /app/data/renown_log_sim.json`
then `python3 scripts/renown_v3_sweep.py data/renown_log_sim.json`.

**Sim capture RAN (2026-06-01, Docker) — harness end-to-end-validated, but the
synthetic field is too homogeneous for a real treadmill verdict.** The
`--from-sim` path works: it runs the rule-based engine with **zero LLM calls**
(seeded from real `personalities.json` names so the engine resolves existing
configs instead of LLM-generating them — the `bot_NN` synthetic path triggered
a paid DeepSeek call per id, fixed), no DB writes, and derives scalps via the
helper (207 scalps over ~2,800 hands). Q1 rank stability is strong (mean
rankρ=0.998). **But Q2 is DEGENERATE and the harness now says so** rather than
printing the trivial PASS: every bot played exactly 350 hands (rebuy-in-place →
**zero variance on the volume axis**, so renown can only track performance by
construction), and the fish/shark archetype split produced **no skill gradient**
(fish 3.71 scalps/3.71 busts ≈ sharks 4.92/4.92 — the fake-`bankroll_repo`
`'fish'` archetype isn't meaningfully weaker than the default TieredBot in
self-play). A flat field also yields **0 "figures"** (nobody reaches 3×median).

A scalp-driven verdict needs a **skill-tiered** field, and a premise check
killed that path: the real sandbox's personas are **not** skill-tiered —
`personalities.config_json` carries an `archetype`/`rule_strategy` for only
**2 of 80** entities (the rest default TieredBot). So a self-play sim can't
manufacture a skill→scalp gradient with the real `bankroll_repo` either; the
scalp/villain route is genuinely untestable on this field without authoring
tiered opponents. (Checking the data first avoided building a DB-extract
pipeline that would have produced the same flat result.)

**But the real DB field already has the heterogeneity the treadmill question
needs** — real *volume* (`total_hands`) and real *performance* (chips won,
net worth) from months of play. Running the sweep there (`--from-db` log) gives
the first honest, non-degenerate verdict:

- `Spearman(renown, hand_count)` = **+0.66** (volume)
- `Spearman(renown, peak_net_worth)` = **+0.59** (wealth standing)
- `Spearman(renown, chips_won_vs_field)` = **+0.02** (barely)
- **VOLUME-LEAN ⚠️** — renown tracks raw volume *more* than real performance.

So v2's current weights reward **out-grinding over out-performing**: breadth +
tenure + backing dominate, and actually *winning* barely registers. Rank
stability is otherwise strong (mean rankρ=0.997). The designed fix —
**wall-clock denomination** of the volume drivers — can't be exercised on a
static snapshot, but the lean confirms it's load-bearing; the lever testable
now is **down-weighting breadth/tenure relative to standing/scalps**. (A
`hand_count` CV<0.05 guard blocks degenerate PASSes; the verdict uses
formula-independent ground-truth signals, not renown's own driver split.)

## Renown as a live competition — world speed & keeping pace

Renown is competitive: the field (every AI) is on the same leaderboard, and the
world runs a **full sim across ~14 tables** while the player is present, so AIs
genuinely accrue renown. The design tension: **the sim plays hands far faster
than a human can** (no LLM-deliberation latency), so any renown denominated in
raw hand-count is a treadmill the human loses by construction. The resolution is
in how the metric is denominated, not in slowing the world:

- **Spine = relative/standing + conserved/rare renown** (net-worth **rank**,
  time-at-#1, renown-weighted scalps, legendary hands). These are *out-perform*,
  not *out-grind*: a fast sim scales the whole field together, so your **rank**
  is the contest and the hands-per-second gap doesn't bury you.
- **Denominate volume-ish renown in wall-clock, not hand-count** ("time at the
  tables / at #1", not "hands played"). This matches the human's wall-clock
  presence against the AIs' **wall-clock-throttled** presence and neutralizes the
  sim's hand-rate edge.
- **The off-grid economy is the natural governor.** Vices, side hustles, and
  energy recovery pull AIs off the felt for **wall-clock-bounded** windows
  (`DURATION_RANGES` are `timedelta`; `ends_at` is a datetime; idle energy
  recovers on wall-clock). That caps each AI's hands/hour, so the field can't
  infinitely out-accrue a present, performing human. Tuning these tunes how hard
  the field competes.
- **Away-time catch-up stays light.** The realtime ticker is presence-gated, with
  a small catch-up so the world feels lived-in rather than frozen when you return
  — intentionally *just a little*, so you never come back to a field that lapped
  you while you were gone. (Tunable lever.)
- **`world_pace` (subtle / lively / bustling) is then an honest difficulty dial**
  for the renown race — faster = the field gains ground faster *while you watch*.

Net: a player who shows up and **performs** (climbs rank, takes scalps, hits
legendary moments) keeps pace regardless of the sim's volume; raw hand-count is
deliberately *not* the spine, so the faster world doesn't make the race unwinnable
— and the same denomination makes AI-vs-AI renown coherent in pure sim runs.

## Money, debt & reputation

### Player side hustle for chips — considered, shelved

Idea: let the human work a side hustle (the AIs' off-grid earn mechanic) for
chips at a regard cost. **Shelved**, because:

- **Gameplay-thin.** For AIs the side hustle is an abstract off-grid timer that
  returns a pool-funded lump (world flavor). For a human it'd be "tap, wait
  wall-clock, get chips" — a money **faucet that competes with the core loop**
  and erodes the bankroll pressure that makes stakes matter.
- **Weak causal link to regard.** Off-table moonlighting doesn't plausibly change
  how the room feels about you *at the table* unless it's framed as public
  desperation — a stretch.
- **Duplicates a richer path.** The human's "I'm broke, get me money" mechanic is
  **sponsorship/backing** (hook 2) — relationship-driven and already built. The
  grubby-money-for-standing tradeoff already exists organically: *take backing →
  default* fires `STAKE_DEFAULTED` (+heat / −likability / −respect → regard
  drops). No new faucet needed.

The only niche it could fit is a broke **villain** whose backing has dried up
(regard ≤ `VILLAIN_REGARD_FLOOR`) with no sponsor path — a last-resort grind back
from zero — but "lose more regard while already at rock bottom" is a death
spiral, so even there it's shaky. Revisit only if the unbacked-villain recovery
loop proves to need it.

### Debt-to-assets (leverage) → credit & standing

A player's **leverage** — `outstanding / (chips + receivables)` — should be a
first-class financial-health signal. Mostly a refinement of existing wiring:

- **Net worth already nets debt** (`net_worth = chips + receivable −
  outstanding`), so carry already drags down net-worth **standing** (and thus the
  renown standing driver). Debt already hurts your rank, organically.
- **Backing already keys off carry** (`staking_tier.resolve_tier` maps outstanding
  carry load → premium/standard/restricted/house_only). The refinement: gate on
  the **ratio**, not absolute carry — 50k debt against 500k assets is fine; 50k
  against 5k is a drowning credit risk who can't get backed. AI-symmetric (every
  entity has chips / receivables / outstanding).

**Legibility line — keep two financial reputations distinct:**

- **Carrying debt = creditworthiness.** Gates *backing* (hook 2) and lowers
  *net-worth standing*. It must **not** tank *regard* — being over-leveraged
  isn't the same as being disliked (a beloved legend can be deep in margin).
- **Defaulting on debt = a behavioral betrayal.** *That's* the regard hit, and
  it's already wired (`STAKE_DEFAULTED` → +heat / −likability / −respect).

So leverage feeds **credit + standing**; only *stiffing someone* feeds **regard**.
That preserves regard's meaning ("how the room feels about you"), separate from
solvency.

## Storage & the legibility guardrail

The hard-won lesson from the attractiveness work (Codex-confirmed): **do not
re-project the shared `respect` axis into AI decision thresholds** — that
entangles betting/staking/forgiveness and destroys legibility. So:

- **Reputation is its own dedicated, sandbox-scoped persisted stat** for the
  human (`renown` + `regard`, plus maybe a cached quadrant label), updated from
  events and surfaced in the UI. It is **read-mostly** — a scoreboard, not a
  threshold injected into core AI math.
- **Sandbox-scoped storage is what enables replayability:** a fresh sandbox =
  a fresh reputation arc, so you can start a clean "villain run." (This also
  sidesteps the fact that `relationship_states` isn't sandbox-scoped today — the
  human's *reputation stat* is scoped even though the underlying edges aren't;
  reputation is fed by events as they happen in that sandbox.)
- The world bites through a **small, explicit set of response hooks** that
  *read* `renown`/`regard` — each one debuggable in isolation, none buried in
  the bet/raise path.

## World-response hooks (where reputation bites)

Symmetric — each reads `(renown, regard)` and responds for both poles:

1. **Table pull (marquee / pariah)** — high renown + warm regard adds a pull
   toward the human's table ("🔥 the big game"); high renown + hostile regard
   *splits* the field: most AIs get a small repulsion (avoid the jerk) while a
   **rival cohort** (high-ego / competitive personalities) get a *pull* to
   challenge you. This is the human-keyed version of the deferred occupant layer
   — it slots directly onto the shipped `table_attractiveness` as a new term.
2. **Backing economy** — warm regard eases vouches/sponsorship (`is_sponsor_*`,
   the career keyring/vouch spine); hostile regard tightens or closes it
   (nobody backs a villain → the self-funded hard mode).
3. **Chat tone** — AIs' chat toward the human skews by regard (warm banter /
   props vs hostile needling), reusing the social-reactions disposition split.
4. **AI demeanor at your table** — high renown + hostile regard makes some AIs
   play scared / tilt-prone around you (a villain's edge: fear is exploitable);
   warm regard makes them looser/friendlier.
5. **Surfacing** — a player-facing reputation panel (the scoreboard) + lobby/
   ticker beats ("the room is wary of you", "a challenger has arrived").

v1 can ship **hook 5 (the read-only scoreboard) alone** for immediate value,
then layer 1–4.

## Build order

1. **The reputation stat + read** (cheap, high-value, zero AI-behavior risk):
   derive `renown`/`regard` from the human's inbound relationship edges +
   achievement events; persist sandbox-scoped; recompute on the world ticker.
2. **Player-facing surface** — a reputation panel + quadrant label + a few
   ticker beats. This alone gives the career a spine and makes the villain path
   *visible*.
3. **World-response hooks 1–4**, smallest blast-radius first (chat tone →
   backing gating → table pull/rival-draw → demeanor).
4. **(v2) AI occupant prestige** — let AIs carry their own renown/regard so the
   marquee/rival dynamics work AI-to-AI too, not just human-keyed.

## Open questions / decisions to lock

- **Axis count** — recommend 2D (renown + regard). Confirm vs a single signed
  scalar (simpler, but loses feared-vs-disliked and the villain-as-achievement).
- **Decay** — renown ratchets (slow/no decay); regard partially decays with
  `heat`. Confirm rates; decide whether any renown decays with long inactivity.
- **Event weights** — which events move which axis and by how much; reuse the
  `relationship_events` AxisShift table vs a parallel reputation-event table.
- **Renown breadth vs depth** — does sitting many tables (breadth) matter as
  much as big results (depth)? Risk: a grinder farming low stakes shouldn't
  out-renown a high-stakes star.
- **Rival-draw selection** — which AI traits define the "challenger cohort"
  (ego/competitiveness?) drawn to a high-renown villain.
- **Cross-sandbox** — reputation is per-sandbox for replayability; confirm
  there's no desire for a global "career reputation" that spans sandboxes.
- **Does regard feed AI *behavior* or only world *responses*?** Strong default:
  only the explicit response hooks, never the core bet/raise/stake thresholds
  (legibility).

## Related

- `CASH_MODE_TABLE_ATTRACTIVENESS.md` — the shipped AI seating + the deferred
  occupant/marquee prestige this human layer would re-activate (human-keyed).
- `docs/technical/CASH_MODE_SEATING_ATTRACTIVENESS.md` — the as-built
  attractiveness/seating the marquee table-pull hook plugs into.
- `CASH_MODE_CAREER_PROGRESSION.md` — the keyring/vouch career spine the backing
  hook ties into.
- `CASH_MODE_AND_RELATIONSHIPS.md` + `poker/memory/relationship_events.py` — the
  likability/respect/heat graph + events that feed regard.
- `OPPONENT_DOSSIER_PROGRESSION.md` — related player-facing relationship surface.

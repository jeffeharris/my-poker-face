---
purpose: Design for a human-facing prestige/reputation system — an earned, persistent status axis with two poles (beloved legend ↔ infamous villain) that the world responds to, giving cash mode a career spine and sandbox replayability.
type: design
created: 2026-05-29
last_updated: 2026-05-29
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

- **Scalps need porting + an AI side.** Eliminations are recorded in tournaments
  (`tournament_tracker.EliminationEvent` carries the *eliminator*) and per-game
  in `pressure_stats` — but there's no durable **cash** "who busted whom"
  counter, and **AI busts aren't tracked at all** yet. Porting the tracker to
  the circuit serves both renown (scalps) and the `bounty`/`double_knockout`
  achievements; the AI side is required before AI renown can use scalps. Lower
  priority than shipping the metric, per the product call.

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

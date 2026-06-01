---
purpose: The top-level "what is the player working toward" doc for career mode — a three-act arc (climb → arrive → become a fixture) whose endgame engine is creating and mentoring an AI protégé to high prestige, tying the scattered cash-mode systems into one spine.
type: design
created: 2026-05-27
last_updated: 2026-05-27
---

# Cash Mode: Career Endgame (the spine)

> **Status (2026-05-27): direction, not a build spec.** Settles the
> question none of the existing cash-mode docs answer — *what is the
> player working toward?* — and shows how the already-designed pieces
> (career progression, chip sinks, table attractiveness/prestige,
> aspiration-ask, backing) compose into one arc. Individual mechanics
> keep their own specs; this is the map they hang on. Sandbox mode does
> **not** need this — it's the open world with the campaign scaffolding
> off.

## The problem this doc fixes

Career mode has a rich, mostly-unbuilt economy — vouches, fish/whale
movement, staking, relationships, prestige — but **no stated goal**. Ask
"what am I working toward?" and the implicit answer is "more net worth,"
which the design has *already disproven*:

`CASH_MODE_PLAYER_CHIP_SINKS.md` states plainly that a successful player
**outgrows the ladder** ($2 → $1000), the stakes soft-cap at $1000 by
design, and "past that bankroll, money is *only* for sinks." So net worth
is a perfectly good **mid-game scoreboard** and a structurally **broken
endgame** — it tops out, and the docs know it. We need a goal axis that
*doesn't* cap.

## The arc: three acts, three scoreboards

| Act | You are | Scoreboard | Driving doc |
|---|---|---|---|
| **1 — Climb** | a nobody grinding up | **bankroll** (gates the next room) | `CASH_MODE_CAREER_PROGRESSION.md` (vouches reveal doors) |
| **2 — Arrive** | a known name at the top | **reputation** (money has stopped meaning anything) | `CASH_MODE_TABLE_ATTRACTIVENESS.md` (occupant prestige) |
| **3 — Become a fixture** | a patron of the room | **prestige + legacy** (uncapped, self-renewing) | *this doc* (mentor loop) + chip-sinks (host/stake) |

Net worth is honest and load-bearing in **Act 1** — it's literally the
"Qualified" gate on every new room. It hands off to **reputation** in
Act 2 (the only uncapped axis), and reputation becomes **legacy** in
Act 3, where you stop chasing your own score and start building others'.

This is the *Rounders → Molly's Game* turn: lose-and-grind-back, earn your
way up through who you know, then become the one who runs the room.

## Act 3 is the endgame, and its engine is mentorship

"Become a fixture" is a vibe until it has a **loop**. The loop:

```
create a protégé → stake them → analyze their hands → coach →
their bankroll AND prestige climb → it reflects on you → repeat / scale
```

This is the **Sal arc inverted** — you become Sal Moretti. It's also the
single best Act-3 activity because it *consumes everything the rest of the
economy already produces*:

- The **chip surplus** the sinks doc frets about becomes the *fuel* (you
  stake your protégé) — the sink stops being a problem to drain and
  becomes the **goal**.
- The **hand-analysis stack** (real today — see feasibility) becomes the
  coaching instrument.
- **Prestige** stops being a passive sim number and gets a **player-driven
  engine**: your protégé earns regard, and that regard reflects back onto
  their mentor.
- The **aspiration-ask** doc already gives the *AI* version of this story
  (AIs climb, get backed, tell stories); the human endgame is the same
  graph run in reverse — you become the backer and the teacher.

It's also a genuinely **novel poker mechanic**: the player as a
character-*trainer*, not just a player. No other poker game does this.

## The protégé is YOUR creation (the character-protection rule)

**Decision: the protégé is a player-created persona, not a hijacked
celebrity.** You bring in a fresh person and shape them. This matters on
three axes at once:

1. **It protects the curated roster.** The whole game's soul is its
   personalities. Letting a player rewrite Napoleon's or Batman's
   *identity* with sliders would corrupt characters other players (and
   the autonomous world) depend on. Your creation is yours to shape; the
   celebrities stay autonomous and intact.
2. **It deepens the bond.** "I made you, I named you, I taught you to fold
   the blinds" is a far stronger attachment than adopting a stranger.
   Pride of ownership *is* the retention hook.
3. **It's already a locked sink.** `CASH_MODE_PLAYER_CHIP_SINKS.md` #4
   ("Player-created custom personalities," LOCKED, post-Phase-5) already
   specifies: create via the existing personality manager, auto-seed into
   the pool, **start with a higher-affinity bond toward the creator**
   ("I created you"), then evolve; **auto-staked by the creator** through
   the existing staking machinery. **The mentor loop IS this sink, given a
   purpose.** We don't invent a feature — we point an existing one at a
   goal.

### The malleability spectrum (resolves "edit identity vs coach strategy")

The earlier worry — *if players edit anchors directly, every protégé
collapses to the same optimal TAG and personality variety dies* — is
resolved by **who authored the character**:

| Protégé kind | What you may shape | Why |
|---|---|---|
| **Your creation** (create-a-player) | identity *and* strategy — it's clay from day one | nothing to corrupt; shaping it is the point |
| **An adopted up-and-comer** (an emergent AI you've bonded with) | **strategy only**; identity (anchors) stays | protect the existing character; you coach leaks, not souls |

So the rule is: **the more authored/beloved a character, the less you may
rewrite their identity — only your own creations are fully malleable.**

> **Decision (v1): create-only.** v1 ships **only** the "your creation"
> row — you mentor a persona you built. Adopting an emergent up-and-comer
> (strategy-only coaching) is **v2**. Rationale: create-a-player is the
> safe path (zero risk to the curated roster), it's an already-locked sink
> (#4), and it sidesteps the harder v2 questions — *which* emergent AIs are
> adoptable, how an adoption is offered, and how strategy-only coaching
> reads when you can't touch identity. Prove the loop on clay you own
> first.

## The coaching mechanic

Two layers, mapped onto the existing psychology architecture
(`player_psychology.py`: **identity = anchors / state = axes / expression
= zones**):

- **Strategy (what you coach).** The protégé runs as a **tiered bot**;
  you adjust their **deviation profile / strategy offsets**
  (`deviation_profiles.py`, the offsets in `tiered_bot_controller.py`) —
  "fold less from the blinds," "3-bet wider on the button." You analyze
  their hands (`ev_lost` / `decision_quality` / `equity_vs_ranges`), spot
  the leak, fix it. This is real poker coaching: you fix lines, you don't
  rewrite the person.
- **Coachability (the drama, governed by anchors).** A high-`adaptation_bias`
  protégé absorbs coaching fast; a high-`ego` one resists ("I know what I'm
  doing"); a low-`poise` one backslides under pressure even after you've
  fixed the leak. **The anchors become the texture of the mentoring
  relationship itself** rather than a slider you drag. Raising a
  talented-but-stubborn kid is a *better game* than min-maxing a stat
  block — and for created protégés you set those anchors at birth, so the
  difficulty is one you chose.
- **Earned identity drift (v2).** A long, successful mentorship can slowly
  shift an anchor (a jumpy kid's `poise` rises over a season) as a
  prestige reward — the power fantasy survives without the
  collapse-to-sameness, because it's *slow and earned*, not a slider.

## Prestige is the scoreboard — and the keystone to build first

Everything above needs **one visible number the player can watch climb**:
prestige. Today the player **cannot see their own standing at all** (the
relationship graph computes regard *toward* the human, but nothing
surfaces it). For an endgame scored by reputation, that invisible number
is the critical hole, not polish.

- **Player prestige** = the human's inbound regard from the relationship
  graph (the `social_prestige` model already specced in
  `CASH_MODE_TABLE_ATTRACTIVENESS.md`, computed for the human node).
- **Reflected prestige** = *the mentor of a famous player is famous.* Your
  protégé's prestige feeds yours at a fraction (`W_REFLECT < 1`), so
  mentoring *amplifies* standing but never substitutes for earning your
  own — your own play still dominates your score.
- **Where it lives:** the surfaces `CASH_MODE_TABLE_IDENTITY.md` already
  built — the in-game header chip and the lobby pin — are the natural home
  for a standing/reputation readout.

### Reflected prestige — the farm guard (decided)

The risk: a player spins up a *stable* of protégés and farms reflected
prestige instead of genuinely raising a champion. Three layered guards,
the first of which makes farming self-defeating at the source:

1. **Source guard (strongest, already inherent).** Reflected prestige
   flows from the protégé's *own earned* `social_prestige` — and the
   prestige model's "bootstrapping is a feature" (`CASH_MODE_TABLE_ATTRACTIVENESS.md`)
   means **a freshly-created persona has ≈0 inbound regard** until it earns
   standing through real play. So a stable of N new protégés contributes
   N × ≈0. You cannot mint prestige by creating characters; each must climb
   on its own merit. The farm is dead at the root.
2. **Rollup guard — headliner-dominant.** A mentor's reflected total reuses
   the attractiveness doc's `occ_prestige` shape: **`max(best protégé) +
   P_LINEUP × Σ(rest)`** with a small `P_LINEUP`. One genuine champion ≫ a
   stable of also-rans; the marginal 4th/5th protégé adds little. Quality
   over quantity by construction.
3. **Maintenance guard — decays without coaching.** A protégé's reflected
   contribution **decays toward zero without recent coaching activity**
   (hand reviews / strategy adjustments). Protégés are an ongoing time
   investment, not passive income — abandon one and its reflection fades.
   This caps how many a player can *genuinely* carry, since attention is
   the real constraint, not a hard count.

Net: farming requires raising *many genuinely-respected players and
actively coaching all of them at once* — which is just... being a great
mentor at scale. The guard doesn't forbid the exploit; it collapses the
exploit into the intended behavior. A hard concurrent-protégé cap is
**not** needed in v1 (the three guards make it redundant); revisit only if
sim/playtest shows attention isn't a tight enough constraint.

So the **first concrete build is the player-facing prestige surface.** It's
load-bearing for Act 2, Act 3, *and* the protégé loop simultaneously.

## How the existing docs compose into this spine

| Existing doc | Role in the arc |
|---|---|
| `CASH_MODE_CAREER_PROGRESSION.md` | **Act 1** — Sal stakes you, vouches reveal doors, you climb the ladder. The front half of the arc. |
| `CASH_MODE_TABLE_ATTRACTIVENESS.md` | **Act 2/3** — room + occupant prestige; the uncapped reputation axis. Gives us `social_prestige`. Currently a sleeper on the unbuilt `attractiveness()` core. |
| `CASH_MODE_PLAYER_CHIP_SINKS.md` | **Act 3 fuel** — #4 (player-created personas) = the protégé; #1 (staking) = how you back them; #2/#6 (home games/hosting) = the other fixture activities. |
| `CASH_MODE_AI_ASPIRATION_ASK.md` | the **AI mirror** of the arc — proves the world has its own upward-mobility stories; you become the backer those stories ask for. |
| `CASH_MODE_TABLE_IDENTITY.md` | the **surface** — where the prestige scoreboard and "vouched by" provenance live (it already deferred those to "the prestige layer"). |
| `CASH_MODE_BACKING_SYSTEM_HANDOFF.md` | the **staking machinery** that makes you a patron (Phase 5 = human-as-staker). |

## Feasibility — what exists vs what's new

Verified against the code 2026-05-27:

**Already shipped / proven pattern:**
- **Per-protégé tuning.** Each AI's `config_json` (in the `personalities`
  table) is a per-instance persisted blob that's *already selectively
  mutated and written back* — that's how `staker_profile` /
  `borrower_profile` / `bankroll_knobs` work (`bankroll_repository.py`).
  Anchors live in that same blob, so persisting a per-protégé adjustment
  is an operation we already do.
- **Hand analysis.** `decision_analyzer.py` + the `player_decision_analysis`
  table (`ev_lost` / `decision_quality` / `equity_vs_ranges`), a
  serializer, and the Range Explorer admin tab (VPIP ranges per player)
  give the coaching instrument a real substrate.
- **`COACHING` CallType** already scaffolded in the LLM layer.
- **Staking** (backing Phase 1+2), **`human_clone.py`** (clone-as-protégé
  variant), and the **relationship graph** all ship today.

**Designed but unbuilt:**
- **Prestige itself** — no `social_prestige.py` / `table_attractiveness` /
  `room_prestige` / `seated_at`; the whole `attractiveness()` core is on
  paper. This is the real foundation work for Acts 2–3.
- **Player-created personalities** (#4) — locked, post-Phase-5.

**New surface this doc implies:**
- The **player-facing prestige readout** (keystone).
- The **coaching UI** — surfacing a protégé's hand analysis and letting
  the player nudge strategy offsets.
- The **create-a-protégé flow** wired to ownership + auto-staking +
  warm-affinity seed.

## Build sequence (thinnest playable first)

1. **Player prestige surface (keystone).** Compute the human's inbound
   regard (the `social_prestige` read the attractiveness doc specs) and
   show it on the existing identity surfaces. Visible endgame score, no
   protégé yet.
2. **Create-a-protégé (sink #4).** The locked player-created-personality
   flow: create → auto-seed → warm bond → auto-staked. Now you have a
   character of your own in the world.
3. **Coaching v1 (strategy only).** Surface the protégé's hand analysis;
   let the player nudge strategy offsets; anchors govern how much it
   "takes."
4. **Reflected prestige (+ farm guard).** Wire the protégé's prestige into
   the mentor's score at `W_REFLECT` with the headliner-dominant rollup and
   coaching-decay guard above — the loop closes; mentoring visibly advances
   the endgame without becoming farmable.
5. **Fixture activities (the rest of Act 3).** Hosting/home games, scaling
   to multiple protégés, earned anchor drift (v2).

Each step is independently playable: step 1 alone gives Act 2 a
scoreboard; step 2 alone is a shipped sink; the loop only *needs* 1–4.

## Open questions

> **Decided:** v1 ships **create-only** (adopting an emergent up-and-comer
> is v2 — see the malleability spectrum). The **prestige-farm guard** is
> settled in three layers (source / headliner-dominant rollup / coaching
> decay), no hard protégé cap in v1 — see "Reflected prestige." Still to
> tune: the actual `W_REFLECT`, `P_LINEUP`, and decay-rate values
> (playtest/sim).

- **Coaching cadence.** Real-time nudges between hands, or a between-session
  "review the tape" beat? The latter fits "analyze their hands and make
  adjustments" and is cheaper.
- **Earned anchor drift (v2).** Which anchors may drift, how slowly, and
  is it automatic with success or a spent prestige reward?
- **Does the protégé need the player?** If you stop logging in, does your
  protégé keep playing the autonomous world (and keep earning/losing your
  staked chips)? Ties to offline progression + the side-hustle/world-ticker
  systems.
- **Finite vs open-ended.** Punctuate the open-ended prestige climb with
  finite milestones (a "defeat-every-$1000-celebrity gauntlet,"
  "first protégé to the Pit") — campaign beats over an endless career.

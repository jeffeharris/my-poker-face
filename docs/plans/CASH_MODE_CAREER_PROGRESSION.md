---
purpose: A progressive, scene-based career narrative where the player earns their way into a growing world of cardrooms through vouches (likability-driven, respect-gated) rather than being dropped into the full lobby
type: design
created: 2026-05-26
last_updated: 2026-05-26
---

# Cash Mode: Career Progression (the narrative spine)

## Problem

Career/cash mode today is a **menu, not a story**. A first-time player
hits `/api/cash/lobby` (`flask_app/routes/cash_routes.py:4268`), gets a
200-chip seed (`DEFAULT_PLAYER_STARTING_BANKROLL`,
`cash_routes.py:86`), and is shown **every room across all five stakes**
($2 → $1000) at once. The only thing standing between them and the High
Roller Pit is a money tri-state — `affordable` / `sponsor_eligible` /
`locked` (`cash_routes.py:4507-4512`). They're a shopper browsing a
price-tagged wall.

That dumps the whole world on the player in one screen and makes the
*only* progression axis "save up more chips." There's no sense of
*arriving* somewhere, no reason to be a good table citizen, and no
social texture to the world growing around you.

> **Greenfield.** The entire cash/career layer is unreleased and only in
> playtesting (built the week of 2026-05-26). There's nothing to
> migrate — the lobby entry, the casino layer, and the prestige sleeper
> are all this-week-new and free to reshape.

## The reframe: the lobby is a keyring, not a menu

Most doors start **invisible**. A room appears in your lobby only once
someone has **vouched you into it**. Money stops being the only lock on
a door you always saw, and becomes the *second* lock on a door you just
earned the right to see.

Entry to any new room is three distinct conditions:

1. **Revealed** — someone vouched for you; the key exists. *Social gate.*
2. **Qualified** — bankroll clears the buy-in cushion; you can afford the
   chair. *Economic gate.*
3. **Accepted** — you choose to walk in. *Player agency.*

This is exactly the ask: build your **reputation/prestige *as well as***
your bankroll. The vouch reveals the door; the roll qualifies you.

## The unlock currency already exists

No new "reputation number" is needed. The relationship layer
(`relationship_states`, a **directed** graph keyed
`(observer_id, opponent_id)` with axes `{respect, likability, heat}`,
`poker/repositories/relationship_repository.py`) already tracks how each
AI feels about the player, and **the human is already in that graph** —
regard is updated from play and from stake-settlement events
(`cash_routes.py:4810-4850`). Relationship rows are created on first play
(`record_event()` upserts), so "meeting" someone is just sitting with
them.

There are two layers of standing, and the narrative needs the **simpler
one first**:

| | What it is | What it gates | Status |
|---|---|---|---|
| **Personal regard** | One AI's `{respect, likability}` toward *you* | Getting *invited* to **that AI's room** | **This doc (v1)** |
| **Global prestige** | `social_prestige(p)` — saturated Σ of everyone's inbound regard | *Marquee* tables; the rich/status cohort | Sleeper in `CASH_MODE_TABLE_ATTRACTIVENESS.md` |

Personal regard is one edge crossing a threshold — much cheaper than the
global aggregate, and it's the natural foundation the prestige sleeper
builds on later.

### The vouch model: likability-driven, respect-gated

A vouch is a **reputational risk** — the AI is putting *their* name on
you at someone else's game. So:

```
vouch_ready(ai → human):
    has_played_with(ai, human)                  # PREREQ: you must have sat together
    not already_vouched(ai)                     # PREREQ: one vouch per AI (v1)
    respect(ai → human)    ≥ RESPECT_FLOOR      # GATE: can't be disrespected
    likability(ai → human) ≥ LIKE_THRESHOLD     # DRIVER: liked enough (v1 ≈ 0.70)
    # readiness/eagerness scales with likability above the threshold
```

- **You must have played with them.** Vouches come only from people
  you've actually sat with — they can't vouch for a stranger. This is
  what makes each room your *home court*: the people there are who open
  the next door.
- **One vouch per AI (v1).** Each AI spends its vouch once, then it's
  done. Start here and loosen if it's too limiting — caps how fast the
  world blooms (the "slow growth" goal).
- **Respect is a hard gate.** If they think you're a clown, no vouch at
  any likability — "I'm not embarrassing myself." Letting respect rot
  below the floor **stalls progression**.
- **Likability is the driver, ≈ 0.70 to trip it.** Among people they
  respect enough, they bring along the ones they *like*. Max likability
  (0.80+) is too tough to demand for *every* door; ~0.70 is reachable
  through a good session without being a gimme. The further likability
  climbs above it, the sooner/more eagerly they vouch.
- **Mentors start warm.** Curated personas (and home-court regulars you
  click with) seed a relatively **high baseline regard** toward you, so
  you're "already kind of high" and only need to close the last gap with
  a memorable hand or two — not grind a relationship up from zero.

The two failure modes are thematically clean and **opposite to the
prestige axis** (where respect dominates — see the attractiveness doc):

- **Respected but cold** (high respect, low likability): *feared, not
  invited.* A shark earns the marquee's glamour but not a friendly
  introduction.
- **Liked but disrespected** (high likability, low respect): *fun, but
  you won't vouch a donkey into the $10.* A pleasant fish is welcome at
  the floor, not sponsored upward.

**This is the cooperative-play lever.** Warmth — chatting, gracious play,
not cruelly stacking people — drives *likability*, which opens doors.
But warmth only counts *on top of* competence (the respect gate). Being
a good table citizen is how the world grows for you.

## Architecture: a visibility filter over a full-running world

**Decision: the world does not physically grow — the player's *view*
does.** The full attractiveness economy (fish, whales, AI movement,
room/occupant prestige) runs across **all** tables from day one
(`ensure_lobby_seeded`, `cash_mode/lobby.py`; the casino layer; the
world ticker). The narrative is a **per-user `revealed_tables` set**
layered on top: the lobby renders only the rooms you've been let into.

Why this split:

- The economy sim stays intact — fish/whale/prestige dynamics need the
  whole board populated; withholding table *creation* would starve them.
- The spine reduces to two cheap things: **(a)** the `revealed_tables`
  set, **(b)** the invitation events that grow it.
- It composes with everything already designed. *The sim is the world;
  the narrative is your keyring.*

**Delivery.** Invitations ride the **existing world ticker** built this
week (`CASH_MODE_REALTIME_TICKER.md`, `world_event` socket push). A vouch
arrives in-session as a ticker beat — *Doyle leans over: "You should come
by the Back Room Tuesday — tell 'em I sent you."* — which reveals the
room in the lobby and (optionally) seats the voucher there so the player
walks into a familiar face.

**Scope of a reveal (decision: one specific room).** A vouch opens **one
table**, not a stake tier. The world is a *graph of individual venues*
you've been let into, which is what gives "hosts of tables" / "who do you
know" its texture (and feeds the occupant-prestige flywheel later). The
room a vouch reveals is one the *voucher* has standing in — naturally one
they play at — so **lateral** ($2 → another $2, widening your world) vs
**vertical** ($2 → $10, climbing) falls out of *where the voucher sits*,
not a separate rule.

## The scene spine (hybrid: scripted floor + emergent expansion)

**Decision: hybrid** — a guaranteed scripted opening so no one stalls at
the casino, then emergent expansion driven by the relationship graph.

### Scene -1: the training lounge (the classroom)

An **optional** on-ramp *before* the floor. It splits the two jobs Scene 0
was overloading: the lounge **teaches** (mechanics + the master skill);
the floor **tests** (prove it for the vouch). Classroom, then exam.

- **Sal is the guide.** *"Before I let you sit with real money, come watch
  a few hands. I'll show you who's who."* The mentor bond builds *before*
  the test.
- **Labeled sparring bots.** Opponents are the existing rule-bots
  (`CaseBot`, `GTO-Lite`, `BaselineSolver`, the fish bots) shown as a
  **calling station / maniac / nit / solid reg**, with Sal narrating the
  read — *"This one calls everything, that's your money; this one only
  bets the nuts, stay out of his way."* This teaches **game/seat
  selection — the actual win condition of the whole mode** (grinders hunt
  fish; see the attractiveness doc), not tutorial filler. Doubles as the
  **UI tour** (pot / stack / action buttons / chat) and a natural home for
  the **bounded-options EV labels** as training wheels (shown here,
  optionally hidden in real play).
- **The freeroll (decision).** Sal **stakes you $80**, played
  **double-or-bust**:
  - **Bust → Sal covers it.** You lose nothing. No downside — kills
    new-player anxiety.
  - **Double up ($80 → $160) → you keep half the winnings.** Standard
    backer split: Sal's $80 stake returns to him, the $80 profit splits
    50/50 → **you pocket ~$40**, a small early-game bump. (The split % is a
    knob; 50/50 reads as "the makeup deal" and teaches it honestly.)
  - **First playthrough only** — a one-time onboarding grant, not a
    repeatable exploit.
  - **It's your first taste of being staked** — the split mechanic
    foreshadows real staking unlocking at the second-cardroom milestone,
    with Sal as your natural first backer.
- **Boundaries.** Optional + **skippable** (offered prominently first run;
  returning players skip), and **persistent + revisitable** (a practice
  room to try lines risk-free — nearly free once built, good retention).
  Lounge play **does not count toward the Scene-0 vouch** and **doesn't
  move regard**; the *only* economy touch is the one-time freeroll bonus.

> **Accounting.** The $80 stake and $40 bonus must source cleanly (chip
> conservation is a known soft spot — see `project_casino_fish_as_personas`
> audit-drift class). Narratively it's "Sal covering you," but mechanically
> fund it from the **bank pool / an onboarding allowance**, not literally
> Sal's character bankroll, so his roll stays stable and the audit stays
> flat.

### Scene 0 — the floor (scripted, intimate)

After the lounge (or straight in, if you skipped it), this is **the
exam** — the real game, with the reads now in your hands instead of on
labels. Start with 200 chips at a small **casino table**
(`table_type='casino'`, the pool-funded fish farm) — **not a full ring**.
The opening table is deliberately tiny and curated: **one fish, one pro
(your mentor), and you.**

The mentor is **Sal "The Clock" Moretti — an authored character we
control** (decision), *not* a random pick from the celebrity roster. Sal
is the **one fixed point on the critical path**: a weathered cardroom
grinder who never goes broke because he never gambles, gruff but
generous, sees himself in a promising newcomer. Because we own him, his
warm baseline regard, his vouch behavior, and the scripted graduation
beat are all guaranteed — the tutorial never depends on an emergent
personality cooperating.

> **Why authored, not a shortlist** (supersedes the earlier
> "curated-shortlist" lean): the **famous AIs stay autonomous** — they do
> their own thing out in the world. Onboarding is too important to hand to
> a random celebrity who might read cold. One controlled character we can
> tune makes the first impression reliable. The shortlist idea survives
> only as *later, emergent* vouchers (home-court regulars), never the
> opener.

### The first vouch (scripted, earned-but-guaranteed)

The graduation is a real beat, not a timer:

1. **Min hands played** with the mentor — you can't be vouched on hand one.
2. **You take some big pots off the fish** — beating the soft spot in
   front of the pro is what earns the pro's **respect** (the gate). You
   have to *do something*, not just sit.
3. With respect cleared and the mentor's warm baseline likability, the
   pro **vouches you into a cardroom at random** — which becomes your
   **home court**, the room where you "come up."

Because the mentor starts warm and the table is rigged to give you
winnable spots off the fish, this **always graduates** on a normal run
without being a free pass — you still have to win the hands. (Homage
hook: this is the *Rounders* opening — the grinder-mentor, the soft game,
earning your way into the room. See below.)

### Scene-0 beat sheet ("The Clock's table")

Cast: **you** (200 chips), **Sal Moretti** (the pro), **one fish** (a
tourist persona — Greg / Carl / Bobby). Only this casino table is visible.

1. **Cold open — the read.** Sal greets you, sizes you up; gruff-warm.
   Establishes he's a fixture and this is a place to *learn*. The fish is
   loud and loose. — *"Sit down, kid. Keep your money in your pocket till
   I tell you."*
2. **The lesson (first hands).** Sal narrates discipline in chat —
   patience, position, folding — and demonstrates by laying down marginal
   hands while the fish splashes. His baseline regard for you starts
   **warm** (seeded). You learn by watching + doing.
3. **The test (beat the fish).** The mechanical gate: you must take real
   pots **off the fish**. Sal points you at spots — *"He'll call you down
   with second pair. Make him pay for it."* Winning a meaningful pot off
   the fish ticks Sal's **respect** up. You have to actually do it — spew
   chips and respect won't move; the scene won't graduate you on a timer.
4. **The threshold.** After **min N hands** AND respect past the floor AND
   Sal's warm likability (≈ 0.70), the beat fires. Sal lands the lesson —
   *"You waited for the spot, then you took it. That's the whole game,
   right there."*
5. **The vouch + reveal.** Sal puts your name in at a **random cardroom**
   — your home court. Ticker `world_event`: *"Sal Moretti vouched you into
   [Home Court]."* The room appears in your lobby
   (`revealed_tables += home_court`). — *"Tell 'em Sal sent you. And don't
   make me look bad."*
6. **Graduation.** Scene-0 flags set (tutorial complete, home court
   revealed); emergent expansion takes over. The casino floor stays as the
   safety net.

**Can't fail out.** Scene 0 is low-stakes and pool-funded; if you bleed
your 200, the floor keeps you in (rebuy / small backstop) so a cold run
can't soft-lock the tutorial — you just take longer to beat the fish.
(Exact backstop mechanism: open detail.)

### Expansion (emergent)

From your home court the relationship graph drives it. Grind it; build
bankroll **and** regard with its regulars. Any regular who clears the
vouch model (respect-gated, likability-driven, must've played with you)
brings you toward a room *they* have standing in — **another $2 room**
(widen) or **knowledge of a $10 room** (climb). The expansion graph is
shaped by **which AIs like + respect you, and where they play** — so:

- A fun, skilled player who's liked by many opens **many** rooms (the
  world blooms — the cooperative-play reward).
- A cold shark earns money but few invitations (respected, not invited).
- A reckless clown stalls (disrespected → gated out), even if liked.

## Anti-skip: you still have to grind (the economic backstop)

The keyring hides **cardrooms** (the vouched lobby venues — your home
court and its graph). But the **casino floor is public** — you can *see*
that higher-stakes casino tables exist ($10, $50, …), and pretending a
$10 room doesn't exist in the world is awkward. So we **don't rely on
hiding alone**. Even a visible higher room is protected by economic gates
that force the grind. There are exactly **three ways into a higher room**:

1. **Bankroll** — grind up until you clear the buy-in cushion yourself.
   The early-game path.
2. **A staker** — someone backs your buy-in. **Disabled in early game:**
   you have no reputation yet, so no one stakes you. Gate the existing
   backing system on the **second-cardroom milestone** — once you've been
   vouched into a stranger's room *beyond* Sal's freebie home court,
   you've "got a name" and backers will talk to you.
3. **Really high reputation** — the prestige system pulls you up / gets
   you vouched straight in. The late-game sleeper
   (`CASH_MODE_TABLE_ATTRACTIVENESS.md`).

**Early game = only path 1**, plus a vouch to *reveal* the cardroom.
Staking and reputation-pull both unlock *later*, once you've earned
standing. So even if you impress someone who could get you into a $10
game early, you can't shortcut: no roll, no staker, no rep → you grind.
That's the "can't jump ahead too quickly" guarantee, enforced
economically rather than by hiding the world.

## Failure & loss states

- **Bust → back to the floor.** The casino always takes you back
  (pool-funded fish seats). The floor is the safety net, never a dead end.
- **Blown vouches cost regard (fork, likely v2).** Busting out of a game
  you were vouched into could drop the voucher's respect/likability —
  "I told them you were worth it." Strong stakes for the reputation
  economy; could even **rescind** a revealed room ("that was a mistake").
  v1 can ship without it (the respect *gate* already means bad behavior
  stalls you); add as the punishing-second-layer.

## Relationship to the attractiveness / prestige spec

This is the **companion in front of** `CASH_MODE_TABLE_ATTRACTIVENESS.md`:

- That doc's **occupant prestige** (global `social_prestige`,
  respect-weighted, "who's a Big Deal to everyone") is the **late-game**
  marquee unlock and is parked as a sleeper on the unbuilt
  `attractiveness()` layer.
- **This** doc's **personal regard** (one edge, likability-driven,
  respect-gated, "who'll go to bat for *you*") is the **early-game**
  unlock and rides directly on the relationship graph that exists today.
- They share one substrate and split cleanly: **respect makes you a Big
  Deal; likability gets you invited to dinner.** The "hosts of tables"
  and human-as-attractor ideas are the bridge between them (a v2+ where
  *you* become a voucher / host and pull others into *your* rooms).

## Homage & flavor (light touch)

The arc *is* the poker-movie comeback story, so lean into a few winks
without making it a licensing problem — names and lines evoke, never copy.

- **Spine = *Rounders* (1998).** The whole shape is Mike McDermott: lose
  it all, grind back from the small games, earn your way up through who
  you know. Hooks:
  - **Sal "The Clock" Moretti** *is* our *Joey Knish* — the steady pro who
    teaches bankroll discipline and gives you your first read on the room.
    (Sal mostly *vouches* rather than stakes you into rooms — proper
    staking is locked early — except the one-time lounge freeroll, his
    first-taste-of-staking gift. A nice play on Knish backing Mike.) He's
    an **original character**, not a licensed one; the nod is the vibe,
    not the name.
  - Your **home court** evokes the underground NYC clubs (the
    Chesterfield / the Taj feel) — the room where you come up.
  - A high-stakes **wall** later on can echo *Teddy KGB* — a beatable-
    but-fearsome boss table you have to grind to, and through. Easter-egg
    line on busting a fish: *"pay that man his money."*
- **Later emergent vouchers can each carry a film nod.** Sal is the only
  authored opener, but the home-court regulars who vouch you onward (drawn
  from the autonomous roster) can wink at the canon: road-gambler charm
  (*Maverick*), old-pro-vs-upstart (*The Cincinnati Kid*, "The Man"),
  exclusive-host energy (*Molly's Game* — which also seeds the future "you
  become a host" direction). Flavor only; no licensed characters.

Keep it to flavor text, ticker beats, and room names — zero gameplay
dependence on any of it, so it's free to tune or pull.

## Touch points (indicative — design, not a build spec)

| File / area | Change |
|---|---|
| persistence / schema | new per-user `revealed_tables` set (revealed table_ids per owner, or per sandbox); first-mentor + scripted-beat progress flags |
| `flask_app/routes/cash_routes.py` (`get_lobby`) | filter the lobby to `revealed_tables`; new player → Scene-0 casino only, not the full grid |
| `cash_mode/` (new module, e.g. `career_progression.py`) | the vouch model (`vouch_ready`, `RESPECT_FLOOR`, `LIKE_THRESHOLD`); which room a voucher reveals; scripted-beat triggers |
| world ticker / `world_event` push | emit invitation events; reveal-on-receipt; optionally seat the voucher in the revealed room |
| training lounge (new mode/context) | **Scene -1**: no-economy practice context vs labeled rule-bots; Sal-narrated reads + UI tour; bounded-options EV labels as training wheels; persistent + revisitable; **$80 double-or-bust freeroll** (bust→pool covers; double→50/50 split, player keeps ~$40; **first playthrough only**; pool/onboarding-funded for clean conservation) |
| `cash_mode/casino_provisioning.py` | seat the Scene-0 **intimate** table (1 fish + **Sal Moretti** + human) for new players; pick a **random** cardroom as the home court the first vouch reveals |
| Sal Moretti (authored mentor) | new **controlled** character — `personalities.json` entry *plus* whatever scripting the scene needs (warm baseline regard, guaranteed graduation beat). Distinct from the autonomous celebrity roster |
| backing / staking (`CASH_MODE` backing layer) | **gate staking on the second-cardroom milestone** — disabled until then (the anti-skip backstop) |
| `poker/repositories/relationship_repository.py` | read a single `(observer, human)` edge for the vouch check (outbound from the AI — already supported); track `already_vouched` per AI |
| frontend lobby (`Lobby.tsx`) | render only revealed *cardrooms* (casino floor stays public); invitation toast/animation when a new door opens; "vouched by X" provenance on a room card |

## Build sequence (thinnest playable first)

Ship the **thesis** before the system: prove "start small → earn a door"
end-to-end, *then* make the world grow, *then* onboard, *then* stakes &
polish. Each milestone is independently playable and testable.

**Decisions to nail before M1 (cheap, cross-cutting):**
- **`revealed_tables` scope** = **per-sandbox** (it's the world's keyring;
  a new save starts over). Storage: JSON set on the sandbox row or a small
  join table.
- **What a brand-new player sees** = *only the Scene-0 casino table*, zero
  cardrooms. So the lobby filter hides all lobby cardrooms until revealed;
  casino-floor visibility is handled by seeding just the one Scene-0 table
  for a new player (broaden the public floor later — see open questions).
- **Scene-0 table must be pinned** — no AI movement / no live-fill, so Sal
  and the fish stay put. This is the integration point with
  `movement.py` / live-fill; needs a "scripted/pinned" table flag.

### M1 — The core loop (the thesis; playable) 🎯

The thinnest end-to-end slice. The "door opens" moment.

- **Keyring filter** — `revealed_tables` state + `get_lobby` filters
  cardrooms to it. *(This is the foundational first PR; verifiable alone —
  manually adding a table_id reveals it.)*
- **Scene 0** — seed a new player into the pinned intimate table (Sal + 1
  fish + human); add the **Sal Moretti** persona entry now (plumbing
  exists).
- **Scripted first vouch** — *not* the general model yet. Fire on
  **min-hands + a crude "won pots off the fish" signal**, then add a
  **random cardroom** to `revealed_tables` + emit the ticker event. Room
  appears.
- **Instrument, don't gate** — log the live regard edges (Sal→human,
  fish→human) during playtest so M2's thresholds (0.70, respect floor) are
  grounded in real data, not guesses.
- *Frontend:* minimal — the room just appears (a basic toast is a bonus).
- *Tests:* lobby filter (revealed vs hidden), Scene-0 seeding, vouch fires
  once on the trigger and reveals exactly one room.

### M2 — Emergent expansion (the system)

Without this the world dead-ends after the first vouch, so it's next.

- Implement the real **`vouch_ready`** (respect-gated, likability-driven,
  played-with, one-per-AI) over the relationship graph; inbound-regard
  read; evaluate on the world ticker.
- Home-court regulars (and anyone you play) can now vouch you onward —
  lateral ($2) / vertical ($10) falls out of where they sit.
- *Risk this de-risks:* regard **tuning** — does normal play actually
  reach ~0.70 like / clear the respect floor in reasonable time? (M1's
  logging feeds this.)
- *Tests:* vouch gating (disrespected → no vouch; liked+respected → vouch;
  one-per-AI), reveal targets the voucher's room.

### M3 — The training lounge (onboarding)

Optional by design, so it sequences after the core loop is proven.

- New no-economy practice context; labeled rule-bots + Sal narration; UI
  tour; bounded-options EV labels as training wheels.
- **$80 double-or-bust freeroll**, **pool/onboarding-funded** — get the
  **conservation accounting** right (known soft spot): bust → pool eats
  $80; double → split returns stake + share cleanly, audit stays flat.
- *Tests:* freeroll payout/bust paths conserve chips; first-playthrough-only
  guard; lounge play writes no regard / no vouch credit.

### M4 — Stakes & polish

- Gate the **backing layer on the second-cardroom milestone**; wire the
  **Sal-as-first-backer** beat once it unlocks.
- Frontend polish: invitation toast/animation, "vouched by X" provenance,
  the marquee badge (shared with the attractiveness sleeper).
- Failure states: the Scene-0 **can't-fail-out backstop**; the
  **blown-vouch** regard penalty / room rescission (v2 fork).

## Open questions

> **Decided:** one vouch per AI (v1, revisit if limiting);
> `LIKE_THRESHOLD` ≈ **0.70**; vouches require having **played with** the
> AI; mentors **seed a warm baseline** regard; first vouch gated on
> **min-hands + winning pots off the fish**; the home court is a **random**
> cardroom; the Scene-0 mentor is **Sal Moretti, an authored/controlled
> character** (celebs stay autonomous, `starting_bankroll` 6,000 to keep
> him anchored at $2); **staking unlocks at the second-cardroom
> milestone** (anti-skip backstop); **Scene -1 training lounge** (optional,
> skippable, persistent) with an **$80 double-or-bust freeroll** (bust →
> covered; double → keep ~$40; first playthrough only).

- **`RESPECT_FLOOR` value** and how steeply vouch eagerness scales with
  likability above 0.70. Tune in playtest.
- **`min_hands` for the first vouch**, and how many big pots off the fish
  "count." Tune so graduation is reliable but earned.
- **How controlled is Sal?** Pure scripted controller, a constrained LLM
  persona, or a normal persona with scene-pinned regard/behavior? Enough
  control to guarantee the graduation beat; how much beyond is open.
- **Lateral vs vertical balance.** Does the early game over-widen ($2
  sprawl) before letting you climb? May want the mentor's first vouch to
  bias toward lateral (home court) and later vouches toward vertical.
- **Bankroll cushion per room.** The "Qualified" gate's buy-in multiple —
  reuse the existing affordability band or a stricter "vouched-game"
  cushion.
- **Casino floor visibility.** Exactly which casino tables are visible
  from the start vs. discovered — the whole floor, or stake-by-stake as
  you progress? (Cardrooms are always keyring-hidden; this is only about
  the public casino tier.)
- **Loss of access (v2 fork).** Do blown vouches cost regard / rescind
  rooms (above)?
- **Does a revealed room ever re-hide?** If you never play it, does it
  drop off? Leaning: no — a key is a key.
- **Human as voucher (v2+).** When does the player start pulling AIs into
  their own rooms (the bridge to occupant prestige / hosting)?

## Deferred to later

- Global `social_prestige` marquee unlocks (the attractiveness sleeper).
- Player-as-host / "who do you know" reverse direction — you vouch others,
  you host a table, your reputation pulls a lineup.
- Blown-vouch reputation penalties and room rescission.
- Named, multi-beat scripted storylines beyond the Scene-0 graduation.

## Appendix: Sal Moretti — persona draft

A standard `personalities.json` LLM persona (tight-aggressive grinder:
tight/patient, selectively aggressive, very poised/tilt-proof, low ego,
low risk-identity = *never gambles*, warm and chatty = mentor). The
scene-control (warm baseline regard, guaranteed graduation beat) is
layered *on top* of this entry by the Scene-0 script — the persona itself
is ordinary.

> **Do not merge into `personalities.json` yet.** Adding him to the live
> roster makes him a normal seedable persona — he'd start appearing in
> every sandbox's autonomous world *before* the scene system exists to
> place him. Land the Scene-0 plumbing first, then add the entry.

```json
"Sal Moretti": {
  "play_style": "disciplined and patient; a lifelong low-stakes grinder ('The Clock') who never gambles, waits for the right spot, and presses hard only when the math is with him",
  "default_confidence": "steady",
  "default_attitude": "gruff but generous",
  "anchors": {
    "baseline_aggression": 0.52,
    "baseline_looseness": 0.30,
    "ego": 0.28,
    "poise": 0.86,
    "expressiveness": 0.58,
    "risk_identity": 0.28,
    "adaptation_bias": 0.62,
    "baseline_energy": 0.42,
    "recovery_rate": 0.22
  },
  "verbal_tics": [
    "Patience is a position, kid. Fold enough and you'll never go broke.",
    "I've been grinding this room since before you could see over the rail.",
    "The money comes to the one who waits for it. It always does.",
    "Tick, tick. The right spot's coming.",
    "Mind your roll and your roll'll mind you."
  ],
  "physical_tics": [
    "*checks a battered steel wristwatch*",
    "*stacks his chips into perfect, even towers*",
    "*nods slowly, sizing you up*",
    "*sips black coffee without breaking eye contact*"
  ],
  "visual_identity": {
    "identity": "a weathered lifelong cardroom grinder they call 'The Clock'",
    "appearance": "late 60s, lean and sharp-eyed, deep smile lines, close-cropped silver hair and a neat grey mustache, unhurried",
    "apparel": "a worn but pressed flannel shirt under a faded windbreaker, a battered steel wristwatch, reading glasses pushed up on his head"
  },
  "nickname": "Sal",
  "bankroll_knobs": {
    "starting_bankroll": 6000,
    "bankroll_rate": 300,
    "buy_in_multiplier": 1.2,
    "stake_comfort_zone": "$2"
  },
  "id": "sal_moretti",
  "staker_profile": {
    "willing": true,
    "max_loan_pct_of_bankroll": 0.12,
    "floor_anchor": 1.0,
    "rate_anchor": 0.10,
    "respect_floor": -0.4,
    "heat_ceiling": 0.7
  },
  "borrower_profile": {
    "willing": false
  }
}
```

Anchor/knob rationale: `stake_comfort_zone "$2"` — he *belongs* at the
floor, the wise low-stakes lifer, not a climber. **`starting_bankroll`
6,000** (≈30 buy-ins at $2) is deliberately modest: the bankroll-
responsive `stake_fit` band would drift a fat-rolled grinder *up* off the
floor, so a big stack would contradict his character. His staking is
**character, not bankroll bloat** — `staker_profile` is generous (lowest
`rate_anchor` 0.10, fair `floor_anchor`) but disciplined (`max_loan_pct`
0.12 ≈ a $720 floor-stakes backing) with standards (`respect_floor` −0.4 —
he won't back someone he doesn't rate). The Joey-Knish arc: once the human
clears the second-cardroom milestone (staking unlocks), Sal is the natural
**first backer** — the man who vouched for you stakes you.
`borrower_profile.willing = false` — "never go broke, never owe" is his
creed. `recovery_rate` 0.22 above the 0.17 norm — tilt-proof.

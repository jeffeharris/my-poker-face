---
purpose: Ready-to-write outline for the "Inside the Table" post on AIs that leave cash tables to enter a Main Event tournament
type: vision
created: 2026-06-09
last_updated: 2026-06-09
---

# Outline 09 — The Main Event: AIs that leave the table for glory

- **Track:** Inside the Table (the player-facing "what's it like in the world" track)
- **Target reader:** players and curious builders. People who care that the AIs feel
  like a living cast with their own motives. Light poker knowledge assumed; every term
  gets a one-line gloss. The technical readers get a clearly-marked "how it works"
  sidebar; the lede is the *experience*.
- **Working title (candidates):**
  - "The Main Event: AIs that leave the table for glory" (plan default)
  - "When the bank gets too rich, it throws a tournament"
  - "The AIs you were playing with just left — they got the call for the Main Event"

## One-line hook (grounded)

> A Main Event in this game isn't on a calendar — it's what happens when the house bank
> gets *too* rich, and the AI personas who have the most to gain quietly cash out of
> their cash games and go chase the prize.

## Section beats (the narrative spine, in order)

1. **The premise, honestly stated.** Most poker games schedule a tournament. Here the
   tournament is an *economic* event: the closed economy has one bank, and when its
   reserves get flush, it stages a Main Event to redistribute chips back to the players
   by way of a prize pool. The founder's own one-line framing was a course correction
   in the build itself — when the AI pair called the tournament "for funsies," he pushed
   back: it's an economic regulator (pull-quote 1). Lead with that: the glory is the
   *story*, the redistribution is the *reason*.

2. **What a player actually sees.** You're grinding a cash table. Over a few hands, one
   or two of the AIs you've been playing against get up and leave — not busted, not
   bored: *called up*. A toast tells you the Main Event is forming. You can register and
   go play it yourself, or decline and watch it run without you. (Be honest about state:
   the lobby invite card and the register/decline flow are LIVE today; the *AIs leaving
   cash to join* is built but flag-gated off — see beat 7 and Open Gaps.)

3. **Who gets the call — and why it's not random.** The field isn't drawn by lottery.
   A "draw score" ranks the eligible personas on four human-legible motives: a small
   bankroll makes the prize look huge (`prize_appeal`); a low-renown persona has the
   most status to win (`renown_appeal`); the chance to sit with the big names pulls those
   who *aren't* big names (`field_appeal`); and a persona sitting deep and comfortable at
   a good seat is *harder* to pull (`cash_comfort`, subtracted). The personas who leave
   are the ones with the most to gain — which is exactly how a real player picks a
   tournament. (One honest caveat to state plainly: these four weights are *starting
   values, not yet sim-tuned* — there's a planned experiment, EXP_007, not yet run.)

4. **They don't all leave at once — the trickle.** A real design call from the founder
   shaped the feel: AIs shouldn't all teleport off their tables on a single tick. They
   trickle off as the registration window closes, and if a *human* starts the event, the
   AIs finish their current hand first, then go (pull-quote 2). The mechanism underneath
   is a single, blunt "called up" override that beats every other reason a persona might
   have to stay — fish-chasing, hoarding a winning seat, a pending stake — because once
   you've got the call, you're going.

5. **No ghost seats: the leaving has to be exact.** This is the honest engineering beat.
   The project has fought a recurring "ghost seat" bug class for months (a persona that's
   somehow in two places at once). So the migration is built as a strict sequence —
   *reserve* the field, *vacate* the cash seats, *spawn* the tournament — and the field is
   excluded from being re-seated the instant it's reserved, with a guard that **fails
   closed**: if the system can't prove a persona isn't double-booked, it aborts spawning
   the tournament rather than risk it. A persona that's vacated but can't be placed is
   never stranded, by construction.

6. **Glory is real currency: renown on a win.** Winning a Main Event isn't just chips.
   The in-the-money finishers get *renown* — the same prestige score that shapes how the
   world treats them — paid on the same curve as the money, so the winner's standing
   actually rises. And that renown feeds back into the *next* draw: a freshly-crowned
   champion is now a "big name" whose presence pulls the next field. The loop closes —
   the bank's wealth becomes a tournament, the tournament makes a champion, the champion
   becomes a draw. (This is the closing image; it's the "living economy" thesis made
   concrete.)

7. **What's live, what's dormant, and why we're not rushing it.** State this without
   spin. The full multi-table tournament engine runs today (you can play one). The
   *cash→tournament draw* — the part that makes AIs leave — is built, tested phase by
   phase, and flag-gated **off**. The reason it's off is the discipline beat: a
   redistribution mechanic that pulls the *wrong* personas, or the *identical cast* every
   time, would be worse than no draw at all — so the next step isn't more code, it's a
   sim to tune the weights and a hands-on playtest before the flag flips. (Good honest
   closer for the "build in public" brand: here's a finished feature deliberately left
   switched off until it's proven.)

## Evidence & assets

**Hard numbers / concrete specs to cite (all code-verified in `technical/TOURNAMENTS.md`):**
- The draw score is four terms with default weights **prize 0.40 / renown 0.25 /
  field 0.15 / cash_comfort 0.20** (`tournament_draw.py:71`) — explicitly flagged as
  *starting values, not sim-tuned* (TOURNAMENTS.md §9).
- A Main Event fires only when bank reserves cross a high-water mark
  (`RESERVE_TRIGGER = 0.12` of holdings) **and** a cooldown has elapsed —
  `MAIN_EVENT_COOLDOWN_SECONDS = 1800` (30 min), registration window
  `MAIN_EVENT_REGISTRATION_WINDOW_SECONDS = 600` (10 min) (TOURNAMENTS.md §7).
- Default event is a **freeroll**: `field_size=18, table_size=6, starting_stack=10_000,
  buy_in=0` (`economy_signal.py:277`).
- Prizes pay ~**top 30%** (`PAYOUT_FRACTION = 0.30`), front-loaded **38 / 24 / 15%**
  (`DEFAULT_PAYOUT_CURVE`), escrow nets to exactly 0 (TOURNAMENTS.md §2).
- v1 autonomous tournaments carry **0 LLM cost** — funny-money hands resolved by a
  deterministic, chip-conserving `FakeHandResolver`; only the persona's *economic
  identity* matters for payout (TOURNAMENTS.md §2). Good "this is cheap and honest" note.
- The draw is **RESERVE → VACATE → SPAWN**; the double-presence guard `draft_exclusions`
  **fails closed** (TOURNAMENTS.md §4–§5, §10 invariants table).
- Renown grant on win: `DEFAULT_WIN_RENOWN = 1.0`, paid on the same `paid_places_for`
  curve as the chips (TOURNAMENTS.md §5, Phase D).
- A real performance figure for the "what a player sees" beat, from the founder's own
  playtest (chat, tournaments transcript): registering took **~40s** from click to
  landing at a table — honest UX friction, candidate for a "we noticed it was slow"
  aside, NOT a spec to advertise.

**Supporting references (for a "how it works" sidebar):**
- `technical/TOURNAMENTS.md` — the canonical architecture doc; §5 (draw),
  §6 (real-chip economy), §7 (the chairman / when-and-how-big), §8 (gating flags
  table — use this to state live-vs-dormant *precisely*).
- `docs/captains-log/tournaments/tournaments-as-a-draw.md` — the phase-A–D build log,
  including the "OOM that wasn't" and the no-early-spawn product call. Source for the
  discipline beat (beat 7): "What it has **not** had is a single run with the flag on."
- `docs/captains-log/tournaments/multi-table-tournament-engine.md` — the engine build;
  source for the chip-conservation invariant firing on day one and the "field as source
  of truth" architecture that made human relocation a non-event.

**Screenshots / images available (verify fit before using):**
- `react/react/src/assets/screenshots/mobile-lobby.png` and `.../mobile-table.png` —
  the lobby and table surfaces a Main Event invite would appear on. Closest existing
  assets to "what a player sees."
- **No purpose-built Main Event screenshot exists** in the asset folders (no tournament
  clock / standings / "field paused" capture). Flagged as the top open gap below — the
  hero asset for this post (the broadcast tournament clock the log describes building)
  needs to be captured.

**Commits to reference:** the engine fix that conservation exposed on day one shipped on
its own SHA `96d6f7d0` ahead of the scaffold `3223043d` (cited in the multi-table log).
The phase-A–D draw work is logged but the per-phase SHAs aren't quoted in the logs —
`git log` on the `tournaments` branch can supply them if the post wants precise links.

## Candidate pull-quotes (verbatim)

1. **The founder reframing what a tournament *is* (real chat, tournaments transcript,
   2026-06-01; typos verbatim):**
   > "what do yo umean? the tournament is just for funsies!? why? we are using it as an
   > econmic regualtor to distribute cash to players"

   This is the thesis of the whole post — the glory is the surface, redistribution is the
   point. Anchor beat 1 with it.

2. **The founder specifying the trickle / "finish your hand first" feel (real chat,
   tournaments transcript, 2026-06-02; typos verbatim):**
   > "we ABSOLUTELY should be allowing AI players to leave tables for a tournament,
   > ideally its not all done at the same tick though. AI can start trickling in to the
   > tourney tables as it gets closer to tourney start time. however, if the human user
   > starts the tourney, AI's shoudl finish one more hand at their table and then join the
   > tournament."

   Anchor beat 4. Shows a real product decision about *feel*, not mechanics.

3. **The founder simplifying the accounting (real chat, tournaments transcript; typos
   verbatim) — optional, for the sidebar:**
   > "you can escrow the buy ins to the tournament from [players] or bank and then get back
   > the split from the tournamnet runner so the accounting stays [pretty] straightforward.
   > you get a list of tuples with recipient and percent of purse and the circuit sandbox
   > handles it from there"

   Good for the "how it works" sidebar: it shows the founder making the runner a pure
   funny-money function and keeping the sandbox the sole real-chip authority — a real
   architecture call made in plain language.

## Draft intro paragraph (post voice)

> In most poker games, a tournament is something on a schedule. In this one, it's
> something the *economy* does. There's a single house bank behind the whole world, and
> when its reserves get too flush, it doesn't just sit on the chips — it throws a Main
> Event and hands them back to the players as a prize pool. So when a couple of the AIs
> you've been grinding against suddenly get up and leave the table, they aren't busted or
> bored. They got the call. And the ones who leave aren't random: they're the personas
> with the most to gain — the short stacks for whom the prize looks enormous, the
> nobodies with a name to make. I built the tournament thinking of it as a feature; the
> honest correction, in my own notes, was realizing it was really a regulator wearing a
> trophy.

## Open gaps (what's missing / needs the founder)

- **The hero asset doesn't exist yet.** There's no captured screenshot of the Main Event
  clock / standings / "field paused" surface (the broadcast leaderboard the log describes
  building and screenshotting from a static preview). The post wants one. Existing
  `mobile-lobby.png` / `mobile-table.png` are *adjacent*, not the thing.
- **Live-vs-dormant framing needs the founder's blessing.** The draw (AIs leaving cash)
  is flag-gated OFF as of the source docs (2026-06-04). If it's since been turned on or
  sim-tuned, beats 2 and 7 must be rewritten. Confirm current flag state
  (`TOURNAMENT_DRAW_ENABLED`, `TOURNAMENT_CIRCUIT_ENABLED`) before publishing —
  do **not** claim it's live if it isn't.
- **How much economy to expose.** The post can lead with pure experience and bury the
  reserve-ratio / chairman mechanics in a sidebar, or weave them in. Founder's call on
  audience (player-first vs builder-first).
- **The ~40s registration time** is honest friction from a real playtest. Decide whether
  to include it as a "we noticed and are fixing the cold-start" aside or omit it.
- **Exact per-phase commit SHAs** for the draw work aren't in the logs; `git log` on the
  `tournaments` branch can supply them if precise links are wanted.

## Cross-links (within the series)

- **↔ "Your opponents remember you" (#3 / B2)** — the renown a Main Event grants is the
  same prestige/reputation machinery that shapes how AIs treat a player across sessions.
  Natural cross-link: glory here *means* something next time you sit down.
- **↔ "Opponents are alive" (#1 / B-track)** — this post is the strongest single proof of
  the "living world" thesis: the AIs have motives (status, a long-shot prize) and act on
  them independent of the human. Forward/back-link to whichever anchors the living-cast
  idea.
- **← the cash / "living economy" post (the May-2026 cash-mode sprint)** — the Main Event
  is the *release valve* of the closed economy that cash mode created; it only makes sense
  as the answer to "where do the bank's excess chips go?" If a cash-economy post exists in
  the series, this is its sequel.
- **Series spine:** part of the reconstructed origin arc — the late-May/June-2026
  "living economy" work, where the founder is visibly steering the AI pair (the "for
  funsies?" correction is a clean example) and deliberately holds a finished feature
  switched off until it's proven. Fits the blog's "honesty is the brand / wrong turns
  kept in" thesis.

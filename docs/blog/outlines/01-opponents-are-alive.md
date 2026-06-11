---
purpose: Ready-to-write outline for the "Poker where the opponents are alive" blog post (Inside the Table track)
type: vision
created: 2026-06-09
last_updated: 2026-06-09
---

# Outline: Poker where the opponents are alive

- **Working title:** Poker where the opponents are alive
- **Track:** Inside the Table
- **Target reader:** Poker-curious players and build-in-public followers who have played a poker app and bounced off the soulless bots. Secondary: people interested in how you make an LLM character feel consistent rather than random.

## Hook (one line)

The bots in this game can tilt, and they stay tilted into the next hand — because their mood is a number the game keeps, not a line the LLM made up.

## Narrative spine (section beats, in order)

1. **The premise predates everything else.** The whole project started from one line: AI opponents that aren't card-calculators but *characters*. Today's cast is public-domain figures (Sherlock Holmes, Blackbeard, Cleopatra, The Mad Hatter); the 2023 prototype used licensed names, so describe that era generically. The defining early move was a two-line commit titled "Added some confidence and attitude" that handed the LLM each character's evolving mood. That seed — emotion as a thing the game tracks — still runs the system three years later. (Origin is from the game repo's history; cite as known project lore, see Open gaps.)

2. **What "alive" actually means here: emotion is visible, strategy is hidden.** State the hard design line from the psychology overview verbatim — *"Emotion is visible; strategic intent must be inferred."* That one rule is the difference between a chatbot in a poker skin and an opponent you can read. You see the face and the table talk; you have to infer the play.

3. **The three-layer model — why the character doesn't dissolve into noise.** Identity (anchors) never changes during a session; State (axes) moves every hand; Expression filters what leaks out. This is the answer to "won't an LLM character just be random?" — anchors are the gravity well, the mood moves inside it. Ten anchors (ego, poise, expressiveness, risk_identity, etc.); three live axes (confidence, composure, energy).

4. **Tilt is a number, and it's sticky.** The mood isn't re-rolled per prompt — it's persisted to the database and reloaded next hand. A bad beat routes through the player's `poise` anchor to move their composure axis; a high-ego player loses more confidence when they get bluffed. Recovery is asymmetric and deliberately slow on the way down: *"Below baseline: tilt is sticky."* That's why the opponent who just got coolered plays scared three hands later.

5. **You can read it — and the game shows you that you're reading it.** The opponent dossier (screenshot) is the player-facing payoff: a profile that reads "overconfident," "Surreal and aggressive — applies pressure with bizarre bet sizes," with behavioral-read stats unlocked as you log hands. Tie back to the visibility formula (`visibility = 0.7 * expressiveness + 0.3 * energy`): a high-expressiveness character leaks; a stoic one keeps the mask on. Reading opponents is the actual game.

6. **Honest caveat: what the AI controls and what it doesn't.** The psychology system changes *information access*, not the action — "Zones modify information access, not force actions." And a key honesty point: expression hides the *face*, not the *pattern*. Betting patterns are behavioral, so a player who always bets big when confident is exploitable no matter how good their poker face is. This is a feature, not a bug — it's what makes the reads real.

7. **Where it shows up when you play.** Close on the lived experience: the narrated session recap (the "high roller pit" story screenshot), where a hand becomes a sentence you'd tell someone. The point of the whole stack is that a hand turns into a story worth telling — which loops back to the founding premise.

## Evidence & assets

**Hard facts / numbers to cite (all from the source docs, verifiable):**
- The hard design line: *"Emotion is visible; strategic intent must be inferred."* (`PSYCHOLOGY_OVERVIEW.md`, §1)
- **10 anchors** (identity, static per session) + **3 axes** (confidence, composure, energy; change every hand). (`PSYCHOLOGY_OVERVIEW.md`, §3–4)
- Mood **persists to DB and reloads next hand** — tilt carries across hands. (`EMOTION_AND_PRESSURE_ARCHITECTURE.md`, Track 2 table: "Persists? Yes — saved to DB, reloaded next hand")
- Asymmetric recovery — *"Below baseline: tilt is sticky"* vs *"Above baseline: hot streaks last."* (`PSYCHOLOGY_OVERVIEW.md`, §10; `below_modifier = 0.6 + 0.4 * current_value`, `above_modifier = 0.8`)
- Event routing through anchors: high-`ego` players lose more confidence when bluffed; high-`poise` players shrug off bad beats. (`PSYCHOLOGY_OVERVIEW.md`, §10 event-sensitivity table)
- Visibility formula: `visibility = 0.7 * expressiveness + 0.3 * energy`. (`PSYCHOLOGY_OVERVIEW.md`, §9)
- "Zones modify **information access**, not force actions." (`PSYCHOLOGY_OVERVIEW.md`, §7)
- Betting patterns are behavioral, not presentational — exploitable regardless of expressiveness. (`PSYCHOLOGY_OVERVIEW.md`, §9, "What expression does NOT hide")
- Optional depth (only if the post wants it): the runout-reaction face is a separate *ephemeral* track that overrides the baseline mood during an all-in — three independent emotion tracks share an equity foundation but never combine. (`EMOTION_AND_PRESSURE_ARCHITECTURE.md`, TL;DR). Probably too much for this post — flag for a future "Inside the Table" deep-dive.

**Screenshots / files to include:**
- `react/react/src/assets/screenshots/mobile-dossier.png` — opponent dossier ("overconfident," "Surreal and aggressive," behavioral-read stats locked behind hands played). **Primary visual.** Carries section 5 by itself. Capture from a public-domain persona (e.g. The Mad Hatter), not a licensed/estate-controlled one.
- `.images/tell-your-story.png` — narrated "High Roller Pit" session recap (a hand told as a story). Use for the closer (section 7).
- `react/react/src/assets/screenshots/desktop-table.png` — establishing shot of the table with named-character opponents (optional, for the top of the post).

**Commits to reference (verbatim subjects):**
- `c50dcca9 feat(cash): two-way persona psychology persistence on live cash tables (T3-77)` — proof the mood travels with the character across sessions, not just within one game.
- `c9134fec` / `e7c9fdf4 feat(cash): flush an AI's mood when it vacates the human's table (T3-77)` — the mood is flushed and rehydrated, i.e. it's real persisted state.
- Origin commit "Added some confidence and attitude" (2023, game repo) — the seed. Subject is reconstructed from the project arc; verify exact wording before quoting (see Open gaps).

## Candidate pull-quotes (verbatim)

1. *"Emotion is visible; strategic intent must be inferred."* — `PSYCHOLOGY_OVERVIEW.md`, §1. (The thesis of the whole post.)
2. *"Below baseline: tilt is sticky."* — `PSYCHOLOGY_OVERVIEW.md`, §10 (recovery comment). (Short, concrete, true.)
3. `feat(cash): flush an AI's mood when it vacates the human's table` — real commit subject `e7c9fdf4`. (Shows the mood is a tracked object you can flush, not vibes.)

> Note: the hinted chat transcript dirs (`hybrid-ai`, `player-psychology-system`) did **not** yield a usable verbatim human quote for *this* post — those sessions are about TieredBot strategy/decision-quality and bot-vs-bot sims, not the emotion/personality system. The `player-psychology-system` dir contains no transcripts (memory folder only). Don't invent a quote; the doc-sourced quotes above are stronger and real.

## Draft intro paragraph (post voice)

Most poker apps give you opponents that are really just a difficulty slider with a name attached. This one started from the opposite idea: the opponents should be characters first — Sherlock Holmes, Blackbeard, Cleopatra — and the cards second. The first real move, three years ago, was a two-line commit called "Added some confidence and attitude," which let the AI carry a mood. That's still the core of the game: every opponent has an internal emotional state the game actually keeps track of — so when one of them tilts, it stays tilted into the next hand, and you can read it on their face if you're paying attention.

## Open gaps (need the founder, or more reporting)

- **Exact wording + date of the 2023 origin commit.** The arc gives "Added some confidence and attitude" — confirm the verbatim subject and date from the game repo before quoting it as a commit. (It lives in the game repo, not this marketing repo.)
- **Is cross-session persistence (T3-77) actually live in production**, or staged behind a flag? `EMOTION_AND_PRESSURE_ARCHITECTURE.md` flags T3-77 closure status as *unverified* and notes the live-game-side wiring was incomplete at the time of that log entry. Don't claim "your rival remembers being tilted across days" as shipped until the founder confirms it's on in prod. Within a single session, persistence-and-reload is verified by the Track 2 table.
- **Whether to mention the three-track emotion architecture at all.** It's accurate and interesting but probably one layer too deep for an intro-track post; recommend saving the runout-reaction / three-tracks material for a dedicated deep-dive and keeping this post at the anchors/axes/expression level.
- **A concrete in-game anecdote** (a specific named character tilting and the human exploiting it) would make section 4–5 land harder than the mechanics alone. Founder could supply one real hand, or we pull one from a session recap.

## Cross-links (other posts in the series)

- The origin-story post (console era, July 2023, the celebrity-personas decision, the "confidence and attitude" commit) — this post should link back to it for the founding-premise beat and not re-tell it in full.
- A future "Inside the Table" deep-dive on the **three emotion tracks** (runout reactions vs psychology pipeline vs drama coloring) — this post seeds it and explicitly defers the detail.
- The "living economy" / cash-mode post (cross-session relationships, rivalries, sponsorship loans) — natural sequel: once a character has a persistent mood, the next step is a persistent *relationship*. Section 7's "stays tilted next hand" hands off directly to "remembers you next session."
- A bot-strategy / "is the AI actually good at poker" post — the honest counterweight to this one (the `hybrid-ai` TieredBot work). This post's section 6 caveat ("patterns are exploitable regardless of the poker face") points at it.

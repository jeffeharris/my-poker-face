---
purpose: Ready-to-write outline for the "Trash talk that actually lands" blog post (Inside the Table track)
type: vision
created: 2026-06-09
last_updated: 2026-06-09
---

# Outline — Trash talk that actually lands

- **Working title:** Trash talk that actually lands
- **Track:** Inside the Table
- **Target reader:** Someone who's curious how a poker game models *social* dynamics, not just cards — players who've noticed AIs react differently to the same jab, and builders interested in driving behavior from existing data instead of new config.

## One-line hook (grounded)

The same insult bonds one opponent and wounds another — and which one happens is a pure function of personality anchors the game already had, not a new "temperament" knob we added.

## Narrative spine (section beats)

1. **The premise: words should land in character.** In a game with named celebrity personalities, a generic "you got trash-talked, -0.05 likability" table is the wrong model. The interesting question isn't *did you needle someone* — it's *who you needled*. A proud tyrant takes a jab as an insult; a charmer takes it as banter.

2. **The honest origin: this was wiring, not invention.** Going in, the plan was to "build temperament." Exploration found `_classify_social_disposition()` already existed in `player_psychology.py` — already deriving `energized` / `stung` / `stoic` from static anchors (poise, ego, expressiveness, aggression), and already feeding the *emotional* axes that drive play. It just never touched the *relationship* axes (heat / respect / likability), which still used one flat global table. The whole feature was routing an existing classifier into a second consumer. (Quote the captain's log: "we connected a wire," not "we built a brain.")

3. **How a jab lands, by disposition.** Walk the override table. Neutral `TRASH_TALK` mirror is heat +0.05 / likability −0.10. For an `energized` recipient that *inverts* to likability **+0.05** (rivalry bonds). For a `stung` recipient it's heat +0.10 / likability −0.15 (~2x the heat, ~1.5x the sting). Post-win gloating at a `stung` target peaks at heat **+0.20** — the sharpest social needle in the game.

4. **The calibration discipline: invent no new ceiling.** That +0.20 wasn't picked for drama — it was set to *match* the existing maximum mirror heat already in the table (`STAKE_DEFAULTED`, +0.20). Pair this with the wrong-turn: the architect agent that blueprinted the numbers mis-remembered the neutral baseline (guessed heat +0.10/lik −0.05; real was +0.05/lik −0.10), and the fix was to read the actual table before trusting the estimate. ("trust the table, not the estimate.")

5. **Sarcasm: the design the founder corrected twice.** This is the most honest beat — two real overrides, both from the user, both kept in the log. (a) Sarcasm started as an *orthogonal* axis (intensity x register); the founder argued chill-vs-spicy-sarcastic is a distinction nobody reaches for, so it became a mutually-exclusive *third position* (chill | spicy | sarcastic). (b) The compatibility set was *backwards* — `gloat`/`trash_talk` had been tagged sarcasm-compatible, when in fact sarcasm "weaponizes a *warm* tone": the positive-surface tones (props, gracious, flatter) are the invertible ones; already-hostile tones have nothing to sharpen.

6. **The miss is the point.** Sarcasm only inverts for recipients who *detect* it — gated on `adaptation_bias >= 0.45`, the same opponent-reading trait that lets the perceptive see through flattery (flag `SARCASM_DETECTION_ENABLED`). A character who misses a sarcastic "nice play" reacts to the *literal* surface and genuinely raises their respect for you. Being too dim to catch the insult protects you from it.

7. **Why it's safe to ship: heat decays, respect and likability don't.** Rivalries cool (7-day plateau, 14-day half-life); perceived skill and warmth are permanent. And the deliberate asymmetry — only the *recipient's* view is reshaped, never the sender's — keeps "how it lands" separate from "how it feels to say." Close on the still-deferred gap: being resented still has no movement consequence yet (the `W_SOCIAL` term), and tilt-amplified reception is spec'd, clamped, flag-OFF.

## Evidence & assets

**Hard numbers to cite (all from `docs/technical/SOCIAL_DYNAMICS.md`, verified against code there):**
- Three dispositions from anchors: `stung` (poise <= 0.40, or ego >= 0.60 + expressiveness < 0.55), `energized` (ego >= 0.60 + expressiveness >= 0.55, etc.), `stoic` (default).
- `TRASH_TALK` neutral mirror heat +0.05 / lik −0.10 → energized lik **+0.05** (inverts) / stung heat +0.10, lik −0.15.
- `TAUNT_POST_WIN` stung heat **+0.20** = matches existing ceiling `STAKE_DEFAULTED` mirror heat +0.20 (no new ceiling invented).
- Sarcasm detection floor `_SARCASM_DETECTION_ADAPT_FLOOR = 0.45`; `sharpen/stung` carries respect −0.05 ("condescension cut" — backhand stings worse than open jab).
- Heat decay: 7-day plateau, 14-day half-life; respect/likability never decay.
- Exploitation feedback: heat > 0.5 → bluff_freq x1.3; respect > 0.7 → fold_to_pressure x0.7; likability > 0.7 → bluff_freq x0.85 (the social layer actually changes how AIs *play* you).

**Screenshots / files:**
- `react/react/src/assets/screenshots/mobile-chat.png` — the live quick-chat tone selector (confirm it shows the delivery register row before using as the hero image).
- Possible secondary: `react/react/src/assets/screenshots/mobile-dossier.png` (relationship state surfaced per opponent — confirm content).
- Source docs to link: `docs/technical/SOCIAL_DYNAMICS.md`, `docs/captains-log/temperament/social-temperament-and-sarcasm.md`.

**Commits to reference (real subjects, verbatim):**
- `54f1de27 feat(social): temperament-aware trash-talk reception`
- `af2dbd6b feat(quickchat): trait-keyed mid-hand palette + 3-mode sarcasm register`
- `a34b1001 feat(quickchat): sarcasm detection gate — not everyone gets it`
- `bd6369b0 docs(log): captain's log — social temperament & sarcasm session`
- merged via `9be464fb Merge pull request #169 from jeffeharris/quickchat-palette-sarcasm`

## Candidate pull-quotes (verbatim)

1. From the founder, steering the sarcasm design (chat transcript, temperament worktree):
   > "you cant be chill and sarcastic vs spicy and sarcastic really, thats too much of a nuance. but you could be saracastic instead of being chill or spicy."
2. From the founder, catching the inverted compatibility set:
   > "a sarcastic flatter is an obvious one that would be offensive so it could still fit the inversion premise."
3. From the captain's log (the honest framing of the build's size):
   > "The honest version of the changelog is 'we connected a wire,' not 'we built a brain.'"
4. Backup, the calibration lesson (captain's log):
   > "Cheap lesson: read the table you're calibrating against before trusting an agent's recollection of it."

## Draft intro paragraph (post voice)

A game full of celebrity personalities deserves better than "you trash-talked someone, minus a little likability, the end." If Napoleon and a card-counting wit take the exact same needle, they shouldn't feel the exact same way about it. So I set out to build a "temperament" system — and then found most of it already shipped. The classifier that sorts characters into *stung*, *energized*, and *stoic* was already there, already deriving from personality anchors, already steering how each AI plays a hand. The actual work wasn't building a brain; it was wiring an existing one into a second job: how a character *remembers* you after you ran your mouth.

## Open gaps (need the founder or more reporting)

- **No playtest data.** Everything here is the design + calibration as shipped to `development`. There's no logged before/after of an *actual game* where the same jab visibly bonded one AI and tilted another. A single annotated hand transcript would make the whole post concrete — only the founder can produce/confirm one. (Flagged: this is the biggest credibility gap.)
- **Screenshot truth.** I have not opened `mobile-chat.png` to confirm it shows the chill/spicy/sarcastic register row. Confirm before using as hero.
- **Is sarcasm live in prod?** SOCIAL_DYNAMICS notes the sarcastic *frontend* register row was still unbuilt on the tone selector at doc time (shipped on `development`, register UX deferred). Founder should confirm current prod state before the post implies players can pick "sarcastic" today.
- **Exploitation-modifier reality check.** The heat/respect/likability → play multipliers are documented; whether players *notice* the effect in real games is unverified anecdote. Don't overclaim "you can feel it."

## Cross-links (series)

- **Origin / psychology system post** — the "Added some confidence and attitude" 2023 commit and the anchor model this disposition classifier reads from. This post is a direct descendant of that seed.
- **Cross-session relationships / cash mode post** — heat/respect/likability persist across sessions and drive exploitation; this post is the *input* side (how those axes get written), that post is the *memory* side.
- **A build-in-public / "wrong turns with the AI pair" post** — the two founder corrections to the sarcasm design are a clean case study in steering and overriding the AI; pair with the June cutover misdiagnosis story.
- **Bounded EV decision-options post** — the relationship modifiers (bluff_freq, fold_to_pressure) feed the same decision layer; worth a one-line bridge.

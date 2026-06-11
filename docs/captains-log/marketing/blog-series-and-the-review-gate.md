---
purpose: Building the two poker blog series (archetypes + fundamentals) and the two-reviewer gate that caught what would have shipped wrong
type: guide
created: 2026-06-09
last_updated: 2026-06-09
---

# Two blog series, and the reviewers that earned their keep (2026-06-09)

This started as one post and turned into twenty. The useful part of the story is
not the writing, it is the review gate, because an independent pass caught a
factual error and a pile of overconfident advice that I would have published
without blinking.

## How it grew

The ask was "find SEO opportunities," which became "write a calling-station
post," which became a series once we saw the shape. Two series fell out of it:

- **Know Your Opponent** (7 posts): how to beat each player type. The key move
  was refusing to write textbook archetypes and instead deriving them from the
  game's own data. A quick script over `poker/personalities.json` bucketed all 76
  circulating opponents by looseness and aggression, and the clusters were real:
  32 maniacs, 18 TAGs, 11 nits, 7 calling stations. So every post profiles an
  actual cast (A Baby and Santa Claus are the stations; Zeus and Don Quixote are
  the maniacs) and links their opponent pages. The grounding is the differentiator.
- **Playing Better Poker** (11 posts): the fundamentals, from a glossary through
  position, pot odds, bluffing, exploitative and defensive play, stack depth, and
  tilt. The glossary doubles as an internal-link hub that points at everything.

Plus a standalone homage to *Stacked with Daniel Negreanu* (2006), the 20-year-old
benchmark our own competitive-analysis doc keeps citing.

## The voice rule, and the build break it caused

House rule, from the founder's saved feedback: no em-dashes, ever, because surface
AI-tells get content dismissed as machine-written. That sounds trivial and is
genuinely hard to hold across twenty posts. I codified it in a
`marketing/src/content/blog/CLAUDE.md` so the next writer (human or model) inherits
it, along with the SEO and grounding conventions.

Two self-inflicted wrong turns worth keeping:

- The glossary's player-type list used `—` as a bullet separator. Six em-dashes in
  the one post about not using em-dashes. Caught by the pre-commit grep, not my eyes.
- A `CLAUDE.md` in the content directory got picked up by the `**/*.md` content-collection
  glob and Astro tried to parse the conventions doc as a blog post. Build broke until
  I excluded it (`!**/CLAUDE.md`).
- An unquoted `excerpt` containing a colon ("Position is one thing: who acts last")
  parsed as YAML key-value and failed the build. Quote anything with a colon.

## The review gate (the actual point)

Every batch went through two independent reviewers before publishing: OpenAI's
Codex via `/codex-assist`, and a fresh subagent, each told to be skeptical and to
prioritize poker accuracy. They earned it.

- **The one real bug.** The LAG post leaned its whole thesis on a clean tell:
  "a maniac is rated recreational, a LAG is rated Shark, check the tier." The
  subagent clicked through and found it false. Zeus, the post's own running
  example, is rated *Regular*, and the others *Improving*. A reader following the
  instruction would have caught the contradiction on the first try. Reframed it to
  the claim that actually holds: no maniac is a Shark, all the LAGs are.
- **Overconfident absolutes everywhere.** Across the series the reviewers flagged
  the same class of error: advice stated too strongly. "Trap, do not bet" against a
  maniac (you still bet a monster on a wet board). "Three streets of value" against
  a station (not into an ugly runout). "Raise or do not enter the pot" (blind
  defense and set-mining exist). "A4o is trash" (fine on the button). Each was a
  small softening, but together they are the difference between teaching and
  misleading, and my own CLAUDE.md says avoid misleading absolutes. I wrote them
  anyway; the reviewers held me to it.
- **A genuine accuracy hole.** The stack-depth post said small pairs "go down"
  when short. Codex pointed out that set-mining value goes down, but a small pair is
  a fine *shove*, so the post contradicted itself on push/fold. Also flagged 100bb
  as "deep" (it is a standard full stack; deep is 150-200bb+) and an SPR claim with
  no numbers. All fair, all fixed.

The reviewers did not always agree with each other (one called "raise, don't limp"
sound beginner advice, the other too absolute), and on those I took the more
careful reading. The lesson is the boring one: I am a confident writer and a
confident writer ships confident mistakes. The gate is not optional.

## Shipping

Merged the `marketing` branch to `main` as PR #264. The reassuring discovery at
ship time: `origin/main` was fully contained in `marketing` (0 commits behind), so
this was a clean superset, not a divergence to untangle, and the net diff was
content and blog-infrastructure only, no app code. A pre-push hook stopped me once
over a missing trailing newline in a doc; committed the fix and it went through.
The deploy is gated on the test jobs and Caddy already routes `/blog/*`, so the
whole blog lands on the next image rebuild with no infra change.

Final tally: 20 posts, two cross-linked series, one homage, 0 em-dashes, and a
healthy respect for the second opinion.

---
purpose: Narrative log of the maniac-WARN investigation — how an audit reading of "maniac too tame postflop" turned out to be the opposite, and how two of my own hypotheses (the per-action clip, then range composition) got disproved by cheap reads before I touched a single knob
type: reference
created: 2026-06-10
last_updated: 2026-06-10
---

# The maniac isn't tame

The archetype probe came back with no hard fails but nineteen warns, and the
maniac owned five of them — the most of any archetype. The shape looked obvious:
c-bet under band, folds-to-c-bet over, aggression factor under, folds-to-3bet over.
A maniac that checks flops and folds to bets is not a maniac. I wrote it up as
"too tame postflop" and reached for the aggression knob.

Then I made myself read the numbers before turning it.

## Wrong turn one: the per-action clip

My first story was the per-action-shift clip. The maniac's deviation profile caps
each action's shift at 0.35, so I assumed the base chart sat around 40% c-bet and
the clip was stopping aggression from pushing it to 85%. Clean mechanical
explanation. I checked the base chart instead of trusting it.

The unopened-flop bet mass in the shipped chart is already 71.4%. The maniac
measures 71.9%. The clip has 35 points of headroom it never uses. It is not
binding, and it was never the cause. What actually caps c-bet is that the postflop
node has no aggressor flag: the "unopened" node is the same whether you are the
pre-flop raiser who should c-bet ninety percent or the cold-caller who should check
to the raiser, so it averages to ~71% and distortion cannot separate the two roles.
That is the same aggressor-aware-node gap the afq-wtsd arc already flagged. So
c-bet is structural, not a maniac knob.

## The per-node test: the maniac is the opposite of tame

Before believing "tame" at all, I ran the maniac's real distortion over every
facing-bet node and looked at where the fold mass went. It does not go to folding.
Facing a flop bet the maniac shifts fold down ten points and call down twenty, and
puts all of it into raise — raise mass goes from 27% to 55%, facing-bet aggression
factor from 0.71 to 2.95. Per node, the maniac raises more than half the time it
faces a bet. It is not tame anywhere. So how is its aggregate fold-to-c-bet 44%,
over band, when per node it folds far less than the neutral chart?

Only one answer fits: composition. The maniac must be arriving at those bets with a
weaker mix of hands. Which became wrong turn two.

## Wrong turn two: composition, but not the way I said

I told the story that a 49%-VPIP range floods the flop with air, so even a low
per-node air-fold aggregates high. Directionally right, magnitude wrong. The
handclass dump put a number on it: at facing-bet flop spots the maniac is 50.9% air
versus the nit's 45.1%. Five points, not the flood I implied. The reason is humbling
and obvious in hindsight — facing-bet air share is 45–51% for *everyone*, because it
is set by how often any hand whiffs a board, not by how wide you opened. The nit
whiffs flops too.

What the dump actually showed is that the maniac folds to c-bets the least of any
non-passive archetype — 44% against the nit's and tag's ~54% — and folds air least
of all of them, 62% where the nit folds 85%. It is the most aggressive serious
archetype at the table. Its fold-to-c-bet is "over band" only because the band asks
for 25–40, and the arithmetic floor for any range that is half air and folds that
air at a non-spewy rate is about 44%. To get under 40 the maniac would have to float
air at fish rates, which is not discipline, it is spew. The band is the thing out of
range, not the bot.

## Where it actually landed

Five warns, and after the reads none of them is a postflop aggression deficit.
Fold-to-c-bet and aggression factor are a band-floor problem — the bands look about
five points too tight for a realistic half-air maniac, so the fix is to gut-check
`archetype_targets.py` against the research doc, not to make the bot spew toward a
number. C-bet is the shared aggressor-aware-node item. The only genuinely fixable
pair is preflop: 4-bet under and fold-to-3bet over, both choked by a
`reraise_max_per_action_shift` of 0.01 that exists as a band-aid against the vs_4bet
stub charts — loosen it once those charts are regenerated, not before, or the maniac
goes back to jamming trash.

No knob was turned, which was the right outcome. The audit's "too tame" was exactly
backwards, and both of my mechanical explanations died to a one-line read of the
chart and a six-thousand-hand handclass tally. The lesson is the same one this
project keeps reteaching from new angles: a warn is a question, not a diagnosis, and
the cheap measurement answers it before the expensive tuning makes it worse.

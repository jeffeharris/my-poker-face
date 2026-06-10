---
purpose: Narrative log of the afq-wtsd-tuning arc — a reported "AFq too high" that was a measurement timeline bug plus miscalibrated bands, the 6-max instrument that separated artifact from real behavior, a buy-in lever ruled out by stack-depth data, the postflop rebalance that pulled WTSD/c-bet into band, and the station-immune win-rate gate that confirmed it
type: reference
created: 2026-06-09
last_updated: 2026-06-10
---

# Measuring before tuning

## How it started

Jeff: *"When I look at the Archetype review in production, I see some really
high numbers for AFq% and WTSD%, C-bet is low, and fold-to-cbet is a mix. I also
think we're still seeing really high fold-to-3bet for TAG, LAG, and Calling
Station."*

A feel, again, off the admin review tool. No repro. And like last time it was
partly right and partly a measurement mirage. The interesting part was telling
the two apart.

## The first tell: AF was fine but AFq was not

The live (human-cash) numbers were too sparse to judge (the "unknown" archetype
had more postflop rows than all seven real archetypes combined), so I pulled the
background-sim counter table, which has tens of thousands of hands per archetype.

AFq was high for everyone. A nit at 62%, a calling station at 49%. But the plain
aggression factor (AF, the bet+raise over call ratio) was sitting comfortably in
band for the same archetypes. The only thing that separates AF from AFq is folds
in the denominator. If AF looks right and AFq looks high, folds are being
undercounted. That was the whole diagnosis in one observation.

## The timeline bug

The counter table has `postflop_agg` / `postflop_call` columns and, separately,
per-street `flop_fold` / `turn_fold` / `river_fold` columns. The review tool
computed AFq as agg over (agg + call + folds). Looked correct on paper.

The trap was in the migrations. `postflop_agg` / `postflop_call` were created in
the 20260608_1600 migration. The per-street fold columns landed in 20260609_1200,
about twenty hours later. The dominant prod sandbox had been accumulating the
whole time, so the numerator and the call count carried a full day more history
than the fold term. Folds were weighted roughly eight times too light. Proof was
blunt: the same nit row had `postflop_agg` = 523 against a per-street agg sum of
69.

Fix: make the per-street columns the single source of truth for AF and AFq, so
all three terms share one accumulation timeline. The recorder stopped writing the
redundant aggregate counters and the repo dropped them from its column list. No
backfill needed, because the per-street data was already correct; the read just
had to stop mixing the two clocks. The orphaned physical columns stay (a
destructive migration on a live counter table is not worth the risk, and this
project has been bitten by that before).

## WTSD was a different animal

WTSD's two inputs landed in the same migration, so its ratio was internally
consistent. Not the timeline bug. The reason it read high was the lobby sim's
table sizes: thirteen tables, all six-seat capacity, but actual occupancy was
three heads-up tables, a few three- and four-handed, and the rest five. The
targets are written for six-max. Short-handed play inflates WTSD structurally.
Apples to oranges, not over-sticky play.

## The wrong turn

I told Jeff the fold-to-3bet readings (TAG 76, LAG 62, station 52) were a real
over-fold, citing them as a known backlog item. That was wrong, and I corrected
it the next step.

The lobby-sim numbers are off in ways we had already agreed to tolerate (the
short-handed regime, and any remaining squeeze-defence contamination). What we
did not have was an instrument that asks the actual question: under expected
six-max conditions, do the archetypes hit their targets? Jeff asked for exactly
that.

## Building the instrument

There was a mixed-field probe already (`archetype_mixedfield_probe.py`): seven
archetypes, six seats, one rotating out each hand, the realistic six-max field
the bands were written for. But it only measured the preflop stats and AF. AFq,
WTSD, W$SD, c-bet, and fold-to-c-bet had no controlled six-max instrument at all.
They only ever appeared through the lobby-sim counters.

So I extended the probe to compute the full postflop family from the ordered
decision stream (which makes AFq and AF share one timeline by construction, the
exact thing the counter table got wrong) and from the end-of-hand state for
WTSD/W$SD.

The first clean six-max run settled the fold-to-3bet question immediately: in
band for nit, tag, lag, and station. My earlier claim was a regime artifact. Off
the list.

## What the instrument actually found

Preflop: healthy. The real problems were all postflop and regime-independent:

- WTSD too high for everyone (nit 47, station 50, fish 57 against bands in the
  20s to mid-40s). The bots are too sticky and call down too light.
- C-bet too low for everyone (nit 17, tag 33, maniac 46 against bands of 55 to
  95). The preflop aggressor checks away most flops.
- W$SD slightly low for several, which is the downstream consequence of going to
  showdown too often with weak holdings.

AFq stayed high, but that one needed a second look.

## The postflop trace

The tiered bots play from a single hand-authored chart,
`postflop_strategies.json`, 2,160 single-raised-pot entries. Measured straight
off the file: unopened-flop bet frequency averaged 39.7% (nuts bet only 59%), and
facing a flop bet, medium-made hands called 71% and folded 13%. Too passive both
ways, and it is the source of truth for every tiered bot.

One important caveat for the c-bet side: the node has no aggressor flag. The
"unopened" node conflates "I am the preflop aggressor continuation-betting" (want
a high bet frequency) with "I am the caller first to act and should check to the
raiser" (want a low one). So you cannot simply crank c-bet frequency without
turning callers into donk-bettors. A clean c-bet fix needs a new aggressor-aware
node dimension. The calling-discipline side has no such wrinkle.

## Re-baseline before tuning

I was ready to start editing the chart. Jeff's call was to re-baseline the bands
first, so we would not tune behavior toward a possibly-wrong number. Right
instinct.

Hard population numbers at this granularity do not exist, so this was an honest
gut-check, not a citation. The finding: the AFq bands ran about ten to fifteen
points low for the tight and aggressive types. AFq excludes checks, so even a
"tight" player's non-check actions skew aggressive (a nit value-bets and folds,
rarely calls), which lifts AFq above where the old bands assumed. The WTSD bands
were roughly right, with a couple too narrow to survive sampling noise.

Re-scoring the same correctly-measured six-max behavior against the revised bands
gave the clean split we were after:

- AFq fell out of the fail list entirely — almost all band plus the timeline
  measurement bug, not behavior. (Correction, re-measured on the shipped chart: the
  single remaining AFq warn is the **calling station** — ~30% vs a 12–28 band — not
  tag, whose AFq now sits in band. This is consistent with the broadened station
  stickiness landing *after* the AFq re-baseline was set: more non-check actions in
  the station's mix lifts its AFq, and that profile change rode along untested by
  the gate, as noted below.)
- WTSD and c-bet stayed far outside even the widened bands. Those are the real
  behavior problems, and they are exactly the two postflop levers: calling
  discipline (tighten the wide medium-made calls) and c-bet (the aggressor-aware
  node).

## Where it stands

Shipped on this branch: the AFq timeline fix, the extended six-max probe, and the
re-baselined AFq/WTSD bands. The remaining work is the genuine postflop tuning,
now aimed at targets we trust: raise c-bet frequency for the aggressor, and stop
calling down so light. The order of operations mattered. Measuring before tuning
turned a vague "the numbers look high" into a precise, two-item list, and threw
out one of my own wrong answers along the way.

## The buy-in red herring

Before the tuning, Jeff floated a different lever for the in-game 3-bet
over-aggression: require a bigger minimum buy-in. Make the tables deeper and maybe
the bots stop shoving.

I liked it, and I had a mechanism ready. Short stacks are what drive
3-bet-and-jam-with-trash: when the effective stack is twenty or thirty big blinds,
a 3-bet is most of it, so "3-bet" and "shove" collapse into one action and the
math will happily jam junk. Prod was going all-in in seventeen percent of hands,
which reads like a short-stacked table. The story fit. The realistic-poker-rooms
question also checked out, because buy-in floors and deep-stack games are a normal
thing rooms actually offer.

Then I pulled the number instead of trusting the story. There is no big-blind
column on a cash game, so I recovered each table's blind from the one spot that
betrays it (an unopened-pot caller's cost-to-call is exactly the big blind),
snapped it to the known stake tiers, and bucketed the effective stack depth at
every decision. The median decision happened at 67bb. The 3-bets specifically
happened at a median of 78bb, with not one of them under 25bb. The table was not
short. The 3-bets were deep 3-bets, which means a bigger buy-in cannot touch them.
Only the all-ins skewed short (median 43bb), so a deeper buy-in plus a top-up would
trim the jamming, but jamming was the smaller pathology, not the 3-bet frequency
Jeff started from.

So the buy-in came off the table as a 3-bet lever. The over-3-betting is a strategy
matter (the charts, tilt conditioning, and the human being the table's biggest
3-bettor), which lines up with the controlled 100bb sim showing healthy 3-bet all
along. The idea did not die, though. Depth-varied tables, the same stake offered
shallow or deep, is a good feature on its own, and a deep-stack table is exactly
where the postflop tuning below earns its keep. It just was not the fix it looked
like. The cheap stack-depth query is the whole lesson: a plausible mechanism and a
suggestive aggregate are not a diagnosis.

## The tuning pass

Four probe iterations to balance the rest. The base chart was the easy half: a
reproducible transform that moves check->bet on unopened flops and call->fold
facing bets. The first pass over-corrected the calling. WTSD fell from the high
forties to the mid twenties everywhere, which looked like a win until you noticed
the calling station had dropped to a 28% WTSD and was folding flop c-bets half the
time. A calling station that folds is not a calling station. I had tightened the
base globally and forgotten that the per-archetype spread has to come from the
deviation profiles, not the shared chart.

The fix was to put the identity back where it belongs. The `sticky` tendency only
fired on the river, so a station had nothing making it call flop and turn bets once
the base tightened. Broadening sticky across all three streets and handing it to
the station restored the float-call; its WTSD and fold-to-c-bet came back without
touching the tight archetypes. Same story in reverse for the nit: it was drifting
tight-PASSIVE when it is supposed to be the tight-AGGRESSIVE seat, so it got
`auto_cbet` and a small aggression bump. Its c-bet is still a touch capped by the
per-action-shift architecture, which is an honest WARN, not a thing to crank the
band around.

End state: zero hard fails on the six-max probe, WTSD down from 41-57 to 24-36,
c-bet mostly in band, archetypes still readable as themselves. The real gate is
still ahead: this chart feeds every tiered bot, so a calling-discipline change has
to clear the SNG win-rate runner before it goes near prod. Behavior-in-band is
necessary, not sufficient.

## The gate

Here is the trap the whole project was built to avoid. Every postflop win this bot
ever measured came against exploitable opponents, a calling station or a folding
rule bot, so a chip gain could mean "the change is correct" or just "the change
farms the fish harder." You cannot tell which. The champion-challenger harness
removes the fish. It seats the old bot against the new bot, same archetype, same
profiles, differing only by the chart, and lets the better strategy take chips off
the worse one. There is no station to inflate the number.

I had a real worry going in. Tightening the calls means folding more, and folding
more against a loose field is exactly how you leave money on the table. If the
calling discipline was an over-correction, this is where it would show as a
negative number.

It showed the opposite. Heads-up, eighty thousand hands, the rebalanced chart won
+10.6 bb/100 with a confidence interval clear of zero. Not farming a fish, because
there was no fish, just the old version of itself. The tighter, more-aggressive
chart is simply better poker. Six-max came back neutral, and the WTA tournament
gate came back neutral too, which is the expected shape: tournament win-rate is a
coarse ruler that hides a ten-bb/100 cash edge under elimination variance. Neutral
in the coarse lens, clearly positive in the sensitive one, regressing in neither.

One honest limit. The gate A/Bs the chart, not the per-archetype profile changes,
so the broadened stickiness and the nit's c-bet boost rode along untested. I am
comfortable with that, because the chart is where the win-rate risk lived (the
folding), and the profile changes are identity-restoring rather than edge-seeking
(a calling station's stickiness is a deliberate losing leak, not an attempt to
win). The thing that could have cost chips was measured, and it did not.

So the chain closed the way it should have. A vague "the numbers look high" became
a measurement bug, a set of miscalibrated bands, a buy-in red herring, and finally
two real postflop levers, each one verified against an instrument that cannot be
fooled by the fish. Behavior in band, and an edge that holds up with the station
removed.

## A toggle, and the shape of the data

One small feature rode along at the end: a time-window toggle on the admin review
(last hour, day, week, month, all time). It looked trivial, and for one of the two
data sources it was. The live source is a per-decision event log with a timestamp,
so a window is just a `created_at >=` filter. The sim source is not. Those counters
are cumulative totals, one running number per archetype that only ever goes up, so
there is no "last hour" hiding in them to filter out. You cannot window a number
that only knows its lifetime sum. That is the same lesson the whole project kept
teaching from a different angle: the shape of the measurement decides what you can
ask of it. So the sim side is honestly locked to all-time, the toggle disables
itself there, and the reply carries a flag saying so rather than quietly returning
a number that does not mean what the label claims. Windowing the sim would take a
snapshot table, which is a real feature, not a toggle, and it was not what was asked
for.

## Shipping it

It went out as one PR, squash-merged after the backend suite, the lint, the
frontend build, and the independent code review all came back green (the E2E and
deploy jobs skip by design). I waited on the actual check results rather than a
watch command's exit code, because a green that is really a race is worse than no
green at all. The branch is on main now. The chart feeds every tiered bot, so the
proof of the pudding is still ahead, in the live numbers once the background sim
re-accumulates on the new chart. But the gate said the change is sound, and the
gate is the part that does not lie.

---
purpose: Narrative log of the tilt-EV-harness session — picking up the paired-EV scope, and how chasing a believable tilt %time number caught me trusting figures I had not reproduced, three times over
type: reference
created: 2026-06-09
last_updated: 2026-06-09
---

# Measuring tilt's cost, and the numbers that wouldn't hold still

This picks up where "balancing tilt, and trusting the harness over the story" left
off. That session ended with the §4 tilt layers built and a scope doc for the one
instrument still missing: a believable bb/100 for what tilt costs in real play. The
ask this time was modest — pick up that scope, and measure where we sit now that the
tilt distribution had moved. It turned into another lesson in not trusting a number
I had not personally reproduced.

## The first wrong turn: a multiplier off a stale anchor

I ran the two existing harnesses. The signature probe was unchanged. The
distribution harness, though, had shifted a lot since the design doc's snapshot —
hotheads at 18.7% where the doc said 7.8%. I compared that against the doc's recorded
live number (2.4% for a hothead) and wrote a confident conclusion: the synthetic
harness overstates live tilt by five to eight times, so the persistence lever the
whole design hinges on is probably unnecessary.

Then I actually ran the live config. OFF, all flags down, the hothead read ~12%, not
2.4%. The doc's number did not reproduce. My "5–8× overstatement" had been anchored
to a figure I never checked — the exact failure the last session was supposed to have
taught me. The synthetic was running maybe 1.5× hot, not 8×, and "persistence is
unnecessary" was built on sand.

## The second wrong turn: recalibrating to a single run

So I recalibrated the loss mix to the fresh live number and moved on. But "the fresh
live number" was one 1,200-hand run. To check persistence's effect I ran the ON arm
with the same method — and ON came back *lower* than OFF (Poe ON 4.4% vs OFF 12.1%).
That is impossible for a mechanism that only ever slows recovery: for a fixed hand
sequence it can lengthen a tilt episode, never shorten it. The only way ON < OFF is
if the two runs are different games. They are: the flag changes a decision, the
decision changes the deck of outcomes, the trajectories diverge. The on/off sim is
RNG-desynced — the same confound the harness scope was written about. And the prior
doc had read exactly this desync noise as "the mechanism fires, ON ≫ OFF."

A third OFF run made it concrete: 8.9%, 12.1%, and 4.4% (the last being ON) for the
same persona. The number would not hold still. A single run was telling me a story
each time, and the story was noise.

## The fix: stop reading single runs

I wired a multi-seed sweep — five base seeds per arm, mean and spread reported, so
the variance is on the page instead of hidden behind one point. Five seeds OFF put
the hotheads at Poe 9.3% ± 3.3 and Fyodor 15.8% ± 5.8, now in the right order
(deeper hothead tilts more — the earlier inversion was just variance). The
recalibrated synthetic's hothead band median, 12.5%, lands almost exactly on the
live hothead-pair mean of 12.6%.

But the per-persona check is humbling: the synthetic puts Poe at ~23% against his
live 9%, and even flips the Poe/Fyodor order. Poise is not the only driver of how a
persona tilts; recovery rate and baseline matter as much. So the harness is honest
as a spread-shape and order-of-magnitude tool and dishonest as a per-persona
predictor — the aggregate match is real, the per-name numbers are not. I wrote that
caveat into the harness header and the design doc rather than let the clean
band-median match imply more than it earns.

## The EV probe, and a flaw worth keeping

Phase-1 of the EV harness was the easy part: the paired signature probe plus an
eval7-priced EV per arm. It priced the collapse direction plausibly. It priced the
*spew* direction as +EV — tilt-shoving as profitable — which is nonsense, and the
nonsense was the point. The model used equity versus a random hand, and heads-up
even 72o is ~37% versus random, so with any fold equity aggression is mechanically
+EV. Light spew is only −EV against the range that actually *continues*. So
range-aware equity-when-called is not a Phase-2 nicety; it is the thing that makes
the spew sign meaningful at all. Better to ship Phase-1 with that limitation stated
loudly than to quote a +EV-tilt number with a straight face.

## A drive-by bug

Mid-session an agent flagged the telegraph's tilt-entry tracker: it only updated
while the feature flag was on, so flipping the flag on mid-episode would read as a
fresh entry and fire a spurious one-time beat. Real, narrow, cosmetic — no effect on
the actual poker decision. Tracked the edge unconditionally, and updated the test
that had been asserting the buggy behaviour as if it were the spec.

## What I'm taking from it

Last session's lesson was "trust the harness over the story." This one sharpened it:
trust the *right* harness, and only numbers you have reproduced. A recorded figure
with no script behind it is folklore. A single noisy run is a story generator. An
on/off sim of a decision-changing flag is two different games wearing a mustache. The
honest instruments here are the paired probe (no trajectory to desync) and the
multi-seed sweep (variance on the page). Everything else I had to catch myself
believing.

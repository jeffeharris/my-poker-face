---
purpose: Narrative log of the emotional-system tilt analysis and balance work, where a cheap measurement harness twice caught me drawing a confident wrong conclusion, and how the tilt-as-episode design landed
type: reference
created: 2026-06-09
last_updated: 2026-06-09
---

# Balancing tilt, and trusting the harness over the story

The job was to make AI tilt something a player can feel and exploit, and to find
the right balance for it. I expected a tuning task. It turned into a measurement
task, and the measurement caught me being confidently wrong twice.

## The starved feature

I turned the dormant `tilt_conditioning` layer on in dev. It barely fired. The
obvious explanation was that the emotional system never lets anyone reach tilt in
the first place, so the strategy spike had nothing to spike on.

## Wrong turn one: a diagnosis from a narrow sample

Three explore agents mapped the psychology system. A clean story came out of it:
a baseline clamp floors composure at 0.40, which is exactly the tilt line;
recovery runs every hand; high poise damps most events. The conclusion I wrote
into the analysis doc was that tilt is structurally unreachable and the chart
edges are dead. The live dev data agreed: zero percent tilt across 2,137
decisions.

Then I built a harness that drove the real `PlayerPsychology` over all 104
personas, instead of trusting the agent arithmetic and the dev sample. It said the
opposite. A single bad beat tilts 56 of the 104 personas, and a sustained cooler
run tilts every one of them. The dev "zero percent" was four placeholder test
players with default high poise, not the actual roster. The `tilt_fired=0` probe I
had cited as proof of starvation was just a baseline run with no negative events,
so of course nothing tilted.

So the strong claim was wrong, and I corrected the doc. The lesson is cheap and
keeps recurring: a narrow sample plus a plausible mechanism is enough to be
confidently wrong. The fix was measuring against the real code rather than
reasoning about it.

## Wrong turn two: an invariant I asserted on paper

With reachability sorted, the design fell out naturally. Tilt should be an
episode, scaled by temperament, never chronic, with a monk or two as the
deliberate exception. I wrote it up and claimed the mechanism I picked, slow
recovery while tilted, preserved the never-chronic rule automatically, because it
slows the climb-out but never blocks it.

Then I fit it, and the fit disproved the claim. Slowing recovery enough to make
episodes long enough to feel also let fresh bad beats re-tilt a slow-recovering
hothead before it climbed back out, so episodes chained together. The hothead 95th
percentile episode reached 70 hands, with the band tilted 35 percent of the time.
Slows-but-never-blocks turned out to say nothing about whether the tail is
bounded.

The fix was a second-wind escape: after a character has been stuck below the line
for K hands, recovery jumps to a brisk rate and the episode resolves. That capped
the tail without moving the median. The harness earned its keep here by killing a
wrong invariant while it was still a sentence in a doc.

## The balance whack-a-mole

Then the number was just too high. A hothead tilted 26 percent of the time felt
like a quarter of every session spent watching someone steam, which is too much. I
softened the event mix and it dropped to 16 percent, but that starved the middle.
Volatile characters fell to 3.5 percent, which is the same too-little-tilt problem
moved down a band.

The realization that ended the flailing: percent-of-time is a global onset knob.
You cannot lift one end without moving the other. The three things I had been
treating as one knob were actually separable. The recovery drag sets episode
length. The second-wind cap sets the tail. The event rate sets how often it
happens at all. Once they were separate, a middle event mix plus the
episode-length spread knob compressed the distribution into something sensible:
hothead 17.7 percent, volatile 5.9 percent, composed 1.1 percent, stoics near
zero.

Stoics near zero is correct, not a gap. Their baseline composure sits far from the
tilt line, so only a sustained cooler run gets them there, which is rare by
design. They can tilt. They just rarely do, which is what a stoic should look
like.

## A git detour worth noting

A side quest in the middle: I tried to sync the old `archetype-shaping` branch and
found `main` had already re-landed all of its work through a separate PR, plus 69
commits on top. The branch was fully superseded. A merge would have recreated the
duplicated files only to discard them again. I salvaged nothing and branched fresh
off main. I also caught a parallel session moving `main` under me while I worked,
which is the recurring tax of shared worktrees.

## What landed, and what is next

The persistence design is specified and validated in the harness
(`experiments/measure_zone_distribution.py`), with the locked parameters in the
design doc. Nothing is in production `recover()` yet. The port is a contained
change behind a flag, and the frequency has to be re-validated against real games,
because the absolute percent-of-time depends on the real bad-beat rate, which the
harness can only approximate.

The throughline of the day: every time I trusted a tidy story over a measurement I
was wrong, and a small harness driving the real code was what corrected it, twice.

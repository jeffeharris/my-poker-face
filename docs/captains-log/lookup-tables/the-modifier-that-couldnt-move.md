---
purpose: Narrative log of validating the relationship_modifier layer — a Phase-2 abort that retired the matrix's "highest unknown" without a single bb/100 run, and the probe bug that almost hid the answer
type: design
created: 2026-06-12
last_updated: 2026-06-12
---

# The modifier that couldn't move

With the exploitation layer shipped, I turned to the next item on the matrix: the
relationship modifier. On paper it was the scariest thing left — ON by default,
mutating the EV-critical exploitation offsets before the clamp, and **never
EV-measured** in its life. The audit called it the highest *unknown-validation*
risk in the whole stack. The obvious move was to build a seeded-heat bed and run
it through `exploit_bb100`.

I didn't, and that turned out to be the right call — but not for the reason I
expected.

## Phase 0/1 first, and they nearly answer it

Reading the layer before measuring it: the modifier scales the additive `offsets`
dict by a per-target multiplier — chase rivals harder (×1.3 on aggressive
offsets), go soft on friends (×0.85). And the offsets dict is *exactly* the
channel the exploit re-architecture had just spent a week proving inert: a soft
logit nudge that doesn't flip the sampled action. The modifier doesn't touch the
stop-bluff hard override or the value-vs-station intensity — the two channels that
actually move behavior. So the architecture itself predicted the answer: scaling a
dead nudge by 1.3 is still dead.

Phase 1 made it worse for the layer. It doesn't fire in *any* harness — by
construction. `simulate_bb100` hard-sets `apply_relationship_modifier = False` in
its controller factory ("sims don't seed relationship_states"); `exploit_bb100`
hands the bot a bare opponent model with no relationship repo, so the reader bails
at its first early-out. The reason it was never EV-measured is that it's
structurally unmeasurable without a heat-seeding bed. The "never measured" gap
wasn't an oversight — it was baked in.

## The probe bug that almost lied to me

To actually test the *behavioral ceiling* — if it fired at full strength, would it
do anything — I wrote `relationship_modifier_probe.py`: force the strongest
modifier the mapping can emit, on every decision, and watch the bluff rate.

First run: **0.0pp change.** Clean null. I almost wrote it up. But the
instrumentation I'd added on a hunch said the modifier had been called **zero
times** — it never even ran. A null result from a layer that didn't execute is
theater, the same trap as testing the unwired gate last week. So I didn't trust
it.

Two more runs chasing the "why." vs FoldyBot: still zero calls. I assumed the
additive offsets were just empty vs these bots — until I wrapped the offset
*builder* directly and found offsets populated **75.9% of decisions vs a
CallStation**. The channel was wide open. The modifier still wasn't running. The
culprit was that same factory line: it sets the flag False on the *instance*, and
my class-level `True` was being overwritten under me. I'd been measuring the
production no-op, not the ceiling.

## The real answer

Forced the flag True on the instance at decision time, re-ran vs the CallStation
with its 75.9%-populated offsets: the modifier now engaged **11,886 times and
scaled 21,494 aggressive offsets** by the max multiplier. Behavior change:
**0.0pp.** The decision distribution byte-identical to OFF, down to the integer
counts per hand class — and under CRN, a single flipped action would have desynced
the board and moved those counts.

So it's not "we couldn't measure it." It's: forced fully on, at maximum strength,
scaling twenty thousand offsets, it changes nothing. **Inert-by-channel.** It
rides the dead additive nudge and never reaches the levers that move a decision.
The respect axis is even more theoretical — there were zero fold offsets available
for it to scale across 2,500 hands.

That retires the matrix's highest unknown at Phase 2, with no bb/100 run. The
rating drops from "high / highest unknown" to "inert, no live EV risk" — it ships
ON and cannot change a decision. If we ever want relationship to actually bend
play, it has to be rebuilt on the hard-override / gear-switch channel, exactly
like the Tier-2 re-arch. A multiplier on the offset channel is a no-op by
construction, and now there's a probe that proves it.

## The lesson

The quick-pass habit isn't just "confirm it fires before a long run" — it's
"confirm it fires before you trust a *null*." A clean 0.0pp from a layer that
secretly never executed looks exactly like a clean 0.0pp from a layer that
executed and did nothing. The only thing that told them apart was a counter on the
thing I was supposedly testing. Instrument the mechanism, not just the outcome —
twice now the outcome was honest-looking and the mechanism was missing.

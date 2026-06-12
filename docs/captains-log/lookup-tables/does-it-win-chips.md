---
purpose: Narrative log of the bb/100 EV pass — testing the unwired gate by mistake, a second divergent-path bug caught by a quick pass, and the exploit finally winning chips vs real players
type: design
created: 2026-06-12
last_updated: 2026-06-12
---

# Does it win chips?

The behavior was proven (air-bluffing 56.9%→0.3% vs a station), the detector was
honest (WTSD-gated, folder-excluding). The last question was the only one that
ever mattered: does any of it win chips, or does it just look right?

## The mistake: testing the unwired gate

I ran the bb/100 EV pass — `exploit_bb100`, the whole exploitation layer ON vs an
OFF twin, against three fields. The station field came back +21.6 bb/100 (CI-clear,
roughly double the pre-override +10.3 — the hard override paying off). Then I ran
the honest control, a realistic field of the real-human Jeff clone plus the
disciplined Punisher: **+0.0, zero hands flipped.**

I reported it as a finding. Jeff stopped me cold: *"we had not changed the gate?
what were we testing? how it used to work?"*

He was right, and it stung because it was avoidable. The whole reason I'd built
the `loose_passive` detector was to make the exploit reach players like Jeff. But
I'd wired it to the classifier only, never to the counter that decides behavior.
So the realistic-field control couldn't have shown anything but 0% — the detector
that would change the answer wasn't in the loop. I spent twenty minutes of compute
re-measuring how it used to work, and dressed up a foregone conclusion as data.
The fix was one connection I should have made before testing, not after.

## The quick pass that earned its keep

So I wired `loose_passive` into `compute_value_vs_station_intensity` — and this
time, before any long run, I did a 1000-hand quick pass to confirm it actually
fires. It didn't: **0/1000 flipped, again.**

That cheap check just saved another foregone run. The root cause was the same
enemy a third time: `exploit_bb100` drives hands through
`champion_challenger.run_cc_hand`, which feeds actions to the opponent model but
never showdowns. So `_showdowns` stays 0, WTSD reads 0, and a WTSD-gated detector
is dead on arrival — in this harness specifically. The #324 fix had added the
showdown feed to `simulate_bb100.run_hand`, but `run_cc_hand` is a *different*
hand-driver, and the patch never reached it. Exactly the divergent-path debt the
source-of-truth doc was written about, biting in a place no one had looked.

I added the showdown feed to `run_cc_hand`, re-ran the quick pass: **68/1000
flipped (6.8%).** Now it fires. *Then* the long run.

## It wins chips

Full volume, the realistic field: **+13.4 bb/100, CI [+8.8, +18.0], CI-clear
positive.** The same field that was +0.0 an hour earlier. The exploit now reaches
a believable opponent — a real human's mined profile and a disciplined reg — and
takes their chips, where the original "+22.5" did exactly nothing. The station
field held at +24.2 (no regression; CallStation picks up the WTSD-ramped intensity
too). Both CI-clear. It merged as PR #330.

## What it is and isn't

This is the payoff of the whole re-validation: a soft nudge that moved nothing
became a hard override that wins +13.4 bb/100 vs real players, gated by detection
that's trustworthy and psychology that suspends it on tilt. But it's honest to say
what it isn't yet — the override is postflop-only, fired on a detected station.
The Tier-1 coarse lever, switching the whole preflop range by opponent read, is
still untouched. One lever proven, the bigger one waiting.

## The lesson, stated plainly

Two of them, both mine. Don't run an expensive test before connecting the thing
it's meant to test — a control with a foregone answer isn't a measurement, it's
theater. And when a stat reads zero where it shouldn't, suspect the feed before
the formula: three times this stretch the number was right and the plumbing was
missing, each in a different copy of the same path. The quick-pass habit — confirm
it *fires* before you spend an hour measuring by how much — is what turned the
second of those from an hour wasted into five minutes.

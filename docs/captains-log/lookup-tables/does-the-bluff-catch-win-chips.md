---
purpose: Narrative log of the Phase-3 bb/100 EV pass for the bluff-catch hard override — the 6-max smoke that looked negative, the HU run that cleared CI, and why both are the same story
type: design
created: 2026-06-13
last_updated: 2026-06-13
---

# Does the bluff-catch win chips?

The detect/exploit pair was built and behaviorally proven — vs an over-bluffer a
TAG stops folding its bluff-catchers (fold rate 18.2% → 6.2%, tilt gate holds).
The last gate is the only one that ever matters: does it win chips, or just look
right? (Same question, same discipline, as the station work two PRs back.)

## Make it ablatable first

The standard exploit_bb100 toggle flips the *whole* exploitation layer on/off, so
a positive number vs a maniac field wouldn't isolate *this* override from
value_override, the all-in veto, and everything else. So I added a default-on
ablation seam (`bluff_catch_override_enabled`) and a `bluff_catch` CRN change:
both arms run full exploitation, only the hard override differs. The paired edge
is then the override's standalone contribution — nothing else.

Quick-pass first (the habit that keeps earning its keep): confirm it fires in
`run_cc_hand`, the harness's *other* hand-driver, before any long run. It did —
18 fires on the ON hero, 0 on the OFF twin. Wired, not a divergent-feed casualty.

## The 6-max smoke that lied

First real run was 6-max — one hero, four over-bluffers — 800 hands, one seed.
Edge **−48 bb/100**. For a moment it read like the override was torching money,
turning a +20 line into −28.

But 800 hands at one seed is noise: CI [−115, +19]. And it was *multiway* — four
maniacs at the table means most pots are three-, four-, five-way, and a
bluff-catcher facing a bet into a crowd is up against far more value than air. The
catalog says it in plain text: bluff-catch is "HU strongest, weakens multiway as
ranges tighten." I'd run the exploit in exactly the field where it's weakest, at a
sample size that couldn't tell a real effect from a coin flip.

## Heads-up, where it's supposed to live

The CRN path seats one hero, so I could test true HU: TAG vs a single
over-bluffer, same deck both arms, 10,000 hands × 5 seeds.

**+14.8 bb/100, 95% CI [+3.8, +25.8]. All five seeds positive.** The override
flips a losing line (champion −8.5) into a winner (challenger +6.3) — it stops the
TAG from folding bluff-catchers to a maniac who's barreling air, and the calls
collect. CI-clear, no seed disagreement. That's the EV gate passed in the scope
the exploit was designed for.

## And multiway isn't a leak

Then the honest follow-up: is it a *leak* multiway, or just weaker? Proper volume
this time — 6-max, five over-bluffers, 8,000 × 3 seeds: **+2.5 bb/100, CI
[−13.3, +18.2]**, sign-disagreeing across seeds. Roughly neutral. The −48 smoke
was sample noise; with real hands the multiway edge is a wash, not a cost. The
override partly self-scopes — it keys on the *primary aggressor*, which goes
ambiguous in a multiway pot and no-ops — so it leans on the clean HU/short-handed
spots where it's right and mostly stays out of the messy ones.

## Where it lands

A complete detect → behavior → chips chain on a new exploit:
- detects over-bluffers (postflop AF read on the player betting into us),
- changes the right behavior (stops folding bluff-catchers, psychology-gated),
- wins +14.8 bb/100 HU and is neutral multiway — exactly the scope shape the
  literature predicts.

The lesson, again and inverted from last time: the test bed and the *field* both
lie if you let them. The +22.5 station number evaporated against a folder; this
−48 maniac number evaporated against sample size and the wrong table size. Pick
the field the exploit is scoped for, run enough hands to clear CI, and believe the
number only then.

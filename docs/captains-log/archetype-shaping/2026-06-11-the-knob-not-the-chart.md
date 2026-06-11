---
purpose: Narrative log of building the bluff-aware vs_3bet exploit — how "make it a knob per player" turned a chart change into a cleaner runtime layer, and why moderate beat the free EV
type: guide
created: 2026-06-11
last_updated: 2026-06-11
---

# The knob, not the chart

## What I set out to build

The last tracked follow-up on the preflop charts was a "bluff-frequency-aware
taper" for `vs_3bet`. The existing taper keys hero's fold-to-3bet on the villain's
3-bet *width*, floored at MDF, and the note in the code admitted it was "nearly
inert OOP." I assumed I'd be editing the chart generator.

## Measure first (the lesson that stuck)

Before touching anything I measured per node: the villain's bluff fraction, which
constraint actually binds, and the resulting fold rate. Two things fell out. The
MDF floor binds on 11 of 15 nodes, so the width taper really is mostly inert. And
the bluff fraction is *inversely* correlated with width: narrow 3-bet ranges (vs
tight early opens) are polarized, ~42-45% bluffs; wide ones (the BB punishing a
wide SB steal) are value-heavy, ~18%. So the width proxy half-works by accident,
but it can't tell a 38%-bluff node from a 45%-bluff one. Keying directly on bluff
fraction is the real fix.

## The tradeoff, and the user's call

I framed two settings — strong (fold value-heavy spots to the believability
ceiling) and moderate (respond but stay off the cap) — and the user asked for a
concrete example instead of a yes/no. Walking through SB-opens / BB-3-bets with a
real marginal hand (K9o) made the tradeoff legible: strong is higher EV, and
because the villain's 3-bet range is *fixed* in this closed economy it can't adapt
to punish over-folding, so the EV is nearly free. The cost is purely believability
— folding the whole marginal tier vs any value-heavy 3-bet reads nitty and
face-up. The user picked moderate as the default, and asked to make it a knob per
player.

## "Per player" reframed the whole thing

That instruction is what changed the design. A per-player knob can't live in a
chart that the whole field shares. So instead of baking a taper into
`build_vs3bet_defense` — which would have churned the chart JSONs and disturbed the
reproducible base I'd just spent the morning fixing — the exploit became a runtime
layer in `TieredBotController`. The chart stays the GTO/MDF reference; the layer
reads the villain's bluff fraction (a static chart property) at decision time and
folds more of hero's marginal continue, scaled by a per-persona `vs3bet_exploit`
knob that defaults to moderate. No regen, no chart diff, isolated to controller
code. The "make it per player" constraint produced the cleaner architecture, not a
harder one.

## The honest mechanics

The taper is a range concept — fold the equity-bottom of your range — but at
runtime the bot holds one hand, not a range. The trick that makes it clean is an
absolute subtraction from the hand's `call` weight: `call -> max(0, call - s)`. A
thin marginal continue sitting at the 0.10 junk-call floor loses almost all of it;
a core flat at 0.85 loses a sliver; the value 4-bet is never touched. So the
per-hand rule reproduces the range-level "fold the bottom first" behavior without
needing range context. Against a polarized 3-bettor (bluff fraction at or above the
balanced reference) it does nothing — you don't over-fold to someone who's actually
bluffing you. On the real charts: SB-vs-BB (18% bluff) folds K9o's 0.10 down to
0.035 at the moderate knob; UTG-vs-HJ (42% bluff) leaves it untouched.

## The throughline

Two of them. First, measuring before building paid off again — the inverse
correlation between width and bluff fraction wasn't something I'd have guessed, and
it's the whole reason the old proxy was inert. Second, a constraint from the user
("per player") that I could have treated as extra work turned out to point at the
simpler design. The free-EV strong setting is still there for the characters who
should play that way; the default just isn't it, because the table is supposed to
feel human before it feels optimal.

---
purpose: Captain's log — the second limper wrong-turn (a feature that never fired) and the measure-the-spot correction that finished it
type: guide
created: 2026-06-12
last_updated: 2026-06-12
---

# Measuring the limper spot — the second wrong turn, and finishing

The companion to `2026-06-12-the-limper-detour.md`, which ended hopeful: the
push/fold version retired, the *real* punish-limpers exploit scoped as an
exploitation-layer detect/exploit pair, built, unit-tested, flag off, "next is the
validation." That log stopped one wrong turn too early. Here's the rest, honestly.

## The second wrong turn: a feature that never fired

I'd built `_apply_limp_exploit` to **widen the open** — shift `fold→raise` for
playable hands when a foldy limper sits in front. It passed 14 unit tests. It was
gated off. It looked done.

Then I finally ran it in a sim. **0 fires in 2807 calls.** The detection was perfect
(`read_pos: 101/102` — it found the foldy limper every time), but `_apply_limp_exploit`
never changed a single decision. Why: the hero's single-limper spots were
`{check: 1.0}` — the **BB checking its option**. My `fold→raise` shift has nothing to
do with a check. I'd built a response to a decision the bot never faces in this spot.

## The callout

I started to write this up as "the v1 exploit is mis-targeted, here's the redesign,
want me to push through?" — and Jeff cut straight to the bone:

> *"wtf did you build a fold-to-raise case for? did you validate data? how did we
> decide to build that if it NEVER HAPPENS. we have the census data to prioritize —
> that was the whole point."*

Dead right. I built the response from the *abstract concept* of "iso a limper"
without ever measuring **where the hero actually faces one**. Twice in one session
now: the push/fold version took a census *tag* (`pushfold_fallthrough`) literally; this
version took a poker *concept* literally. Both skipped the one cheap step — measure
the spot — that the whole census exists to provide.

## Measure first, then build

So I did it in the right order. Instrumented the sim and dumped the distribution of
the hero's single-limper rfi spots by position and current strategy:

```
single-limper hero spots by POSITION: {'BB': 143}
by STRATEGY shape:                    {'check': 143}
```

**143 out of 143 are the BB checking its option.** Not an opener folding — that case
is empty. A lone limp stands alone almost only when it folds around to the BB; in
middle position a limp draws more limpers and the spot goes multiway (excluded). The
entire opportunity is one decision: *BB facing a lone limp, currently checking →
iso-raise.*

Retargeted to that: convert **passive give-up mass** (`check`, the measured spot, or
a folded open) into an iso-raise — **injecting `raise_2.5bb`** when the node has none,
which the BB `{check:1.0}` case always does. Broadened the iso pool to pairs + suited
+ offsuit broadways (you iso KQo over a limp; you don't iso 72o). Re-ran: it fires,
and the raise reaches the felt (`action='raise', raise_to=249`).

## Validating a 1%-of-hands edge

The bb/100 A/B was useless here — the spot is ~1% of hands, so the effect (~+1 bb/100)
sits a hundred-fold under the short-stack noise floor (±~50 at a few thousand hands).
You can't resolve a 1% spot with an aggregate that needs a million hands. The right
instrument is the **per-decision fold equity**, which is model-free: the `LIMP_FOLD`
limper folds **88.2%** of its limp range to the 2.5bb iso (limps 490 combos, continues
only 58). Even pricing every call as a total loss and ignoring the hero's equity,
that's **+1.14 bb/fire**. And the safety control held: vs a `LIMPS_EVERY_HAND`
never-folder the detect gate excludes it → **0 fires**, no spew.

Turned `LIMP_EXPLOIT_ENABLED` on (skill-graded, reversible, inert in sims), merged
(#328).

## The lesson, made durable

I wrote it into a memory (`feedback_measure_spot_before_building`) so it outlives the
session: **for spot-specific strategy, measure the spot — frequency, position, and
what the bot currently does there — before writing the response; write the coverage
probe before the feature.** Building feels like progress and measuring feels like a
detour, which is exactly backwards: a 5-minute "does this even happen, and what does
the bot do here" sim would have killed both wrong turns before a line of feature code.
The census was built to answer precisely that question. The fix wasn't more rigor
inside the wrong frame — the unit tests were green on a feature that never ran. It was
pointing the instrument at the spot first.

The feature that shipped is small and situational, and it's correct: it iso-raises the
limpers a good player would, it's +EV where it fires, and it folds quietly to a
station. The detours cost real tokens. The habit they bought is worth more.

---
purpose: Narrative log of chasing a "vs_3bet generator drift" that turned out to be an idempotency ratchet upstream in vs_open, and the two times the user's skepticism corrected a tidy story I was telling myself
type: guide
created: 2026-06-11
last_updated: 2026-06-11
---

# The vs_3bet drift that wasn't

## How it started

The day's real work was two preflop-chart changes. First, a small one: the new
6max push/fold Nash charts were firing for every tiered bot, including the fish, so
a calling station was open-jamming a flawless 15bb range. We gated the charts
behind a per-persona `push_fold_nash` flag on a curated handful of "skilled"
characters (PR #292). Worth noting a course-correction there too: I first leaned on
the existing `skill` field, then found ~50% of the cast is tagged `shark` and that
the field is flavor-assigned to the postflop-aggression axis, not short-stack math.
The user's original instinct — "define a few skilled players, not an archetype
mapping" — was the better design. I'd reached for the data that already existed
instead of the data that was actually right.

Second, the bigger one: a `vs_squeeze` chart, splitting an opener-faces-a-3bet from
a cold-caller-faces-a-squeeze, which the classifier had been conflating (PR #294).
That needed the engine to remember the original opener, since the per-raise reset
erases it. Building it is where the trouble surfaced. When I ran the full regen
cascade to add the squeeze section, the *core* charts changed too — vs_3bet, depth,
archetypes, thousands of lines. I dodged it by writing a `--squeeze-only` injection
that left vs_3bet untouched, flagged the unexplained drift, and the user said: look
into it.

## The tidy story I told myself

I called it "vs_3bet generator drift" and went archaeological. Ran each generator
in isolation, diffed per section. The drift was real: a clean `build_vs3bet_defense`
rewrote ~56/38 lines of the committed chart. I traced it to a commit titled
"tighten OOP vs_open flat-calls" that touched only the JSON, and concluded the
chart had been hand-tuned and the generator never encoded it. Neat. Defensible.
Wrong in two ways I hadn't earned the confidence to assert.

The user pushed first on a small thing: "who changed them? are we in the bands
because of the changes?" Fair — I'd been saying "hand-tuned" with more confidence
than I had, and I genuinely didn't know the band impact. Then the push that
actually cracked it: "we just updated the generator today, with the context I was
working with right before you, and gave you a handoff doc."

That reframed everything. This wasn't some stale relic generator that never matched
a hand-tuned chart. It was *today's* generator, written in the same effort as the
data, with a handoff doc describing it. So instead of treating the generator as
suspect, I read what it said it does. Its own docstring: "preserve each node's
current defend width." It is a width *preserver*, not a width *deriver*. The width
lives in the data; the generator carries it forward.

## The recommendation the user was right to reject

Before that reframing fully landed, I'd offered, and the user had picked, "encode
the tightening in build_vs_open." Then they asked the question that should have been
mine: "are we encoding something we don't want encoded?"

Yes. We would have been. Adding an OOP-tightening rule or a 28-hand override table
would have buried chart *data* inside generator *code* and, worse, papered over
whatever was actually broken. The right move wasn't to teach the generator the
tightening. It was to figure out why a width preserver wasn't preserving width.

## The measurement that ended the archaeology

I stopped diffing against history and tested the only thing that mattered: is the
generator a fixpoint? Ran `build_vs_open` three times in succession, no revert
between:

```
committed:  23.9
run 1:      24.77   (+0.87)
run 2:      25.64   (+0.87)
run 3:      26.52   (+0.87)
```

A ratchet. It widened ~0.87pp every run. The committed chart wasn't hand-tuned and
abandoned; it was simply one run old, and any re-run inflated it further, which is
exactly why my squeeze cascade had drifted. Twenty-eight "drifted" hands were just
the marginal boundary hands that flip when the width creeps up under 1pp.

The cause was four lines in `build_node`. Value 3-bet hands carry a small `call`
sliver, but only their raise mass was charged against the budget. So realized
defend width was `defend_total + slivers`, and because non-BB nodes read their own
width back each run, the uncounted sliver compounded. The fix was to compute the
call budget *after* the 3-bet passes, charging everything already placed.

## One more wrong turn inside the fix

I wanted the fix to change nothing in the live chart, so I tried making the BB nodes
also preserve their width from data. It backfired: 12 hands still differed, it
wasn't idempotent, two lints failed. The BB 3-bet composition doesn't round-trip
through `build_node` that way. Reverted it, and accepted what the clean fix actually
does: BB defense de-inflates to its exact designed targets. The reveal that settled
it — the fixed BB widths came out as `34 / 40 / 48 / 58 / 65`, clean round numbers,
which were the `BB_TARGETS` all along. The committed chart had been sitting ~0.4 to
1.7pp above them. The bug, not a tuning. All still above the defend floors, and
validate_preflop bands unchanged within 0.1pp, which finally answered the user's
earlier question: no, we were not in the bands because of this.

## The throughline

Two corrections, both from the user, both the same shape as a lesson from earlier in
this worktree: I trusted a tidy narrative ("lost hand-tuning the generator never
captured") over a measurement ("run it three times and watch the number"). And the
sharper one: when I proposed encoding a fix, the user's "do we even want that
encoded?" was better engineering than my proposal. The honest version of the bug is
duller than my archaeology and far more useful — a four-line accounting error made a
whole pipeline non-reproducible, and nobody had run it twice in a row to notice.

Also worth recording for what it's worth: the user suspected the push/fold 6max
merge had caused it and said they didn't think so. They were right. The shared
equity matrix that `build_vs_open` consumes predates the last regen and is stable.
I verified it rather than assuming, which is the one place I did the right thing
before being told to.

PR #295 landed the fix as a clean foundation commit — idempotent fixpoint, lints
green, no band tuning, scoped to the generator and the cascade-affected JSONs. The
squeeze PR rebases on top of it. The pipeline can be re-run now without rewriting
itself.

---
purpose: Narrative log of fixing tag's over-fold to 3-bets — choosing a spot-tendency over a chart override via a design/adversarial/codex bake-off, and validating it four ways
type: reference
created: 2026-06-09
last_updated: 2026-06-09
---

# De-polarizing the TAG — "let the bake-off pick the lever"

Once the metric fix cleaned the fold-to-3bet measurement, one real residual was
left standing: tag genuinely over-folds to 3-bets (68% vs a 40–58 band). It plays
a too-polarized 4-bet-or-fold defense, flatting almost nothing. And it's
chart-driven — the distortion-OFF Baseline on the same shared base chart already
folds 61% — so the obvious lever (edit the chart) was off the table, because that
chart feeds Baseline, nit/rock, and every derived chart.

## Two roads, and I didn't trust myself to pick

There were two plausible fixes: (A) give tag a scenario-scoped *chart override*
for just the vs_3bet node, or (B) a *spot-tendency* that reshapes fold→call at
vs_3bet. Both avoid the shared chart. I genuinely didn't know which was right, so
instead of guessing I ran a bake-off: two `code-architect` agents each designed
one approach, two adversarial reviewers tore into each design, and codex weighed
in.

The adversarial pass earned its keep. It found that the chart-override design —
which *looked* clean — had a transform that overshot the target (range-weighted
fold would land ~0.44, not the claimed 0.52), and, worse, that the new
constructor parameter it needed would silently no-op in **two of the four**
controller-construction paths (the live tournament runner and the sim's
`__new__` bypass). Meanwhile the spot-tendency's scariest risk — that making the
tendency layer fire preflop would wake up weak_fish's `sticky`/`over_bluff` and
regress it — turned out to be *contained*: every existing tendency street-gates,
so they all no-op when the preflop call passes `street=None`. The reviewer also
caught that the proposed strength (0.15) was an undershoot; the arithmetic wanted
~0.24. Codex's one-liner: ship B.

Three independent voices converging on B, with the specific corrections baked in,
is a much stronger place to start coding from than my first instinct. I'd have
reached for the chart override (it matched how the width-tier charts already
work) and walked straight into the two missed construction paths.

## The thing the aggregate wouldn't tell me

Codex's parting caveat was the sharp one: *aggregate bands can pass while range
quality rots.* A fold→call dampen that hits the target fold% could be calling the
wrong hands — flatting 72o instead of AJo. The mixed-field probe says "fold 50%,
in band" and feels like success, but it can't see *which* hands moved.

So I built the A/B range probe: tag's vs_3bet response, by hole-card equity, with
the tendency on vs off. The newly-defended hands came back AJo, AQo, KJo, QJo,
QTo, 77, TT, A9o, KTs — textbook 3-bet-defend flats, meanEq 0.59, 5% trash. The
4-bet range stayed value-weighted. That's the validation that actually answers
the question; the band number alone would have hidden a trash-flatting bug if one
existed.

## Four ways, because "doing what we want" isn't one number

Validated in four registers: unit tests (the handler + a test that *locks* the
no-op-preflop invariant across every postflop tendency — the one regression that
could sneak in later); the 6k behavioral probe (tag in band, every other
archetype byte-identical); the range-quality A/B (defends the right hands); and a
30k-hand antithetic EV gate (+1.1 bb/100, CI [−5, +7] — no harm, slightly
positive, exactly what de-polarizing an *exploitable* over-fold should read).

The EV gate is "inconclusive" by its own verdict — the CI spans zero. But that's
the right read for a small change: it proves *no harm*, and combined with the
range quality (defending broadways and pairs, not trash) the EV is sound by
construction. A clearly negative number would have been the alarm; +1.1 with the
right hands is a pass.

## What I'd tell the next person

1. **When you don't know the lever, run a bake-off.** Two designs, two
   adversarial reviews, a third opinion. The adversarial pass found two
   silent-no-op construction paths I'd have shipped.
2. **The cap wasn't the constraint — the strength was.** The reviewer's
   arithmetic (0.15 undershoots, 0.24 lands) beat the architect's guess.
3. **Aggregate-in-band is not "doing what we want."** Build the range-quality
   probe; it's the only thing that catches "passes the band by flatting trash."
4. **A preflop call into a postflop-only layer is a footgun.** PreflopNode has no
   `.street`/`.facing_action`; reusing the postflop helper would `AttributeError`.
   Lock the no-op-preflop invariant with a test so a future tendency can't quietly
   start firing preflop.
5. **"No measurable harm + right range" is a valid EV pass** for a small change —
   don't wait for a CI that a small effect will never give you.

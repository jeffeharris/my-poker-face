---
purpose: Captain's log — building blind squeeze-defense, and the per-seat conflation that made a small per-player leak look 6× bigger
type: guide
created: 2026-06-12
last_updated: 2026-06-12
---

# The squeeze leak that was six bots wearing one coat

Picked up `VS_SQUEEZE_DEFENSE_HANDOFF.md` at the line it stops on: *measure the EV
before building.* The diagnosis was already solid — the blinds have no `vs_squeeze`
chart node, so a sharp hero folds its **whole range, including AA**, to an open + a
3-bet. The handoff asked the right question before any code: is folding everything
actually a leak, or is it correct? A blind facing two raises out of position *should*
fold a lot.

## Measure first — and it looked real

So I built the per-decision EV probe the plan called for. Record every BB/SB squeeze
spot the hero reaches in a squeeze-heavy field, price fold (EV 0) against the best call
vs a *sweep* of squeezer range widths, haircut the equity for being out of position.
The shape was exactly what you'd hope for: ~0 leak vs a tight value squeeze (folding is
correct, MDF is high), growing sharply with width. Vs a maniac squeeze it read **5.3
bb/100** even after the realization haircut. Clean, opponent-dependent, a real leak.

I committed that as the step-1 milestone, recommended a value-floor + read-gated widen,
and built it: a new `_apply_vs_squeeze_defense` modifier mirroring `_apply_limp_exploit`,
a skill-graded knob, a feature flag, 16 unit tests, gated off. It looked done.

## Then I validated it, and the number fell apart

The behavioral validation — does it actually fire in a sim — is where it unraveled, the
same way the limper detour did a day earlier. OFF: blinds fold 100%. ON: the value floor
continues 100% (AA/KK/QQ no longer folded — good). But the spot count stopped me: the
validation found **84** hero blind-squeeze spots where my EV probe had reported **1235**.

Same field, same hands, a 15× gap. The tell: my EV probe wrapped
`TieredBotController._get_ai_decision` — and **every one of the six seats is a
TieredBot.** So the probe had been recording all six players' blind decisions and
summing their leaks, then dividing by table-hands and calling it bb/100. That's not a
per-player win rate. It's a per-*table* aggregate — six bots' dead money wearing one
coat. The 6-max harness only attaches an opponent model to the hero seat, so a one-line
filter on that isolates the real player.

Re-ran hero-only. The honest per-player leak: **0.83 bb/100 vs a maniac field, ~0 vs a
tight or standard one.** Roughly six times smaller than what I'd committed. (And the
hero-only frequency, 4.6% of decisions, snapped right back to the diagnosis's original
3.3% — the 41.8% the all-seat pass produced should have been a red flag on its own.)

## What survived the correction, and what didn't

The story changed but the build didn't have to. The **value floor** — stop folding the
nuts to a squeeze — is a correctness fix that's right at any magnitude; folding AA to an
open-plus-3-bet is just a bug, and it's closed. The **widen** is what shrank: it's a
real but marginal, opportunistic edge, worth ≤0.83 bb/100 and only vs genuinely wide
fields. The sim's "maniacs" don't even squeeze that wide — they're tiered bots reading
VPIP ~0.40, not the 0.56 their config claims — so the field never fully exercises the
deep tiers. That's the eval-instrument limit the handoff predicted: a believable read
wants a human-clone wide squeezer, not a rule bot.

So the feature ships dormant (EXPERIMENTAL, off), the doc carries the correction in a
box at the top of the results so the next reader can't miss it, and the flip decision is
a real one rather than a foregone "it's worth 5 bb/100."

## The lesson

The measure-first habit caught this — but only on the *second* measurement. The first
one was measuring the wrong thing with full conviction. When you monkeypatch a class
method in a multi-seat sim, you are instrumenting **every instance of that class**, not
the player you have in mind. bb/100 is a per-player unit; a sum across N seats reported
in that unit overstates by N. The number that's too good (5.3 bb/100 from auto-folding
blinds?) and the frequency that's too high (41.8% when the diagnosis said 3.3%) were
both shouting it; I committed before I listened. Cheap check, banked for next time:
**before trusting a bb/100 out of a wrapped-method probe, confirm it's counting one
seat.** The floor was worth building regardless. The 6× was not worth claiming.

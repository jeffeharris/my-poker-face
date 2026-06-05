---
purpose: Narrative log of building the Director policy-hold cache and sim-confirming the reserve sawtooth
type: guide
created: 2026-06-04
last_updated: 2026-06-04
---

# Director policy hold + the sawtooth that finally fired

Picked this up cold from `PROD_STARTING_CONDITIONS_HANDOFF.md`. The thermostat
was already mostly built across the prior rounds — genesis seed, vice gating,
graduated rake, lean casino fish, the trigger/floor split. The handoff left two
things at the top of the "what's next" list: **build the policy hold** (designed,
not built) and **sim-confirm the repeating sawtooth** (a prior fix that was
unit-tested but never actually watched run).

## The policy hold — a faithful read of a terse spec

The ask: stop `resolve_rake_params` from re-running a `signal()` ledger
aggregate scan *every hand* for a rake band that only drifts over hundreds of
hands. Hold the schedule for a window, recompute it in the lobby refresh.

The handoff gave one cryptic implementation hint: "read by `resolve_rake_params`
(add a `_fresh=` bypass for the refresh)." I sat on that phrase for a minute
because it admits two readings:

1. The lobby refresh calls a dedicated `refresh_director_policy()` directly
   (mirroring how the inequality read is wired), and `_fresh=` is just a
   side-door on `resolve_rake_params` for cold-cache/test cases.
2. The refresh's recompute *is* `resolve_rake_params(_fresh=True)` — the bypass
   is the recompute path itself, so all the compute logic stays in one function.

I went with #2, because it keeps the schedule computation (including the
inequality-band adjustment) in exactly one place — the held value and the
per-hand value can never diverge because they're the same code path. The cache
module (`cash_mode/director_policy.py`) is a near-photocopy of
`field_inequality.py`: same module-level dict, same throttle-on-`now`, same
`reset_cache()`. When two things in a codebase do the same shape of work,
copying the proven one is cheaper than being clever.

The one real subtlety: the cold-cache case. Before the first lobby refresh runs,
the cache is empty. If a per-hand read returned `(None, None)` (static rake) in
that gap, the opening hands of a fresh sandbox would silently under-rake. So a
cold read falls *through* to a live compute instead — correct from hand one, and
the next refresh seeds the held value. Wrote that as an explicit test
(`test_cold_cache_falls_through_to_live`) so a future refactor can't quietly
break it.

Nothing fought me here. The handoff was good; the pattern existed; flag-off is
byte-identical (the `if DIRECTOR_POLICY_HOLD and not _fresh` guard is simply
skipped). 6 new tests, 42 economy-flag tests, 47 lobby/occupancy tests all green,
ruff clean.

**Honest caveat I want on the record:** the policy hold is a *performance*
change, not an economic one. It has zero behavioural effect — its entire value is
the per-hand ledger scan it skips. The sawtooth sim below does **not** exercise
it (it's flag-off, and even on it'd produce identical chip flows). So "built and
unit-tested" is the honest status; "the skipped scan actually matters under load"
is unmeasured. I didn't profile it. The justification is a priori (a ledger
aggregate per hand on the hot path is obviously wasteful for a value that barely
moves), which is a reasonable basis to build it behind a default-off flag, but
it's not the same as having watched it pay off.

## The sawtooth — it works, and it's slower than it looks

This was the part worth watching. The vice-taper fix (`b7206b8b`, last round) was
unit-tested but nobody had run the harness and *seen* reserves cross the 0.12
trigger and fire an event. The prior bug had vice quitting at the 0.06 healthy
floor while the trigger sat at 0.12 — so reserves would stall around 0.06 and a
tournament could literally never fire. The fix moved the vice cutoff to a 0.18
ceiling *above* the trigger, so vice is still ~half-on at 0.12 and pushes
reserves across instead of asymptoting short.

Ran `--ticks 1000 --chunk 40 --seed 0` on the 76-cast. Real solver hands, so it
took ~15 minutes. The trace:

```
40    0.0539  low
240   0.0800  healthy
480   0.1026  healthy
560   0.1054  healthy   ← and here it nearly stops
600   0.1057  healthy
...
760   0.1197  healthy   ← finally crossing
800   0.0563  low  *** MAIN EVENT: −163,607 overlay → 0.0563
840   0.0565  low        ← re-climbing
```

It fired. The full loop — climb past the old stall, cross the trigger, drain a
163k overlay into the field as prizes (holdings jumped 2.47M → 2.62M in one
tick), fall back to the 0.06 floor, start climbing again. End to end, exactly the
designed behaviour. Good.

But the shape of the climb told me something the unit tests couldn't. Look at the
pacing: reserves covered **0.05 → 0.10 in the first ~480 ticks**, then crawled
**0.10 → 0.12 over the next ~280**. The vice brake, the thing that fixes the
asymptote, is *strong* — by the time you're near the trigger vice is mostly
tapered off and the last push is carried by rake alone, which is a thin trickle.
So one floor→trigger climb is ~700–900 ticks, i.e. roughly **one Main Event per
1000-tick run**.

That matters because the design target is "1–2 tournaments per day." Whether this
faucet hits that depends entirely on how many hands a real prod day actually
deals — at this rate it's on the *slow* side unless a day is many hundreds of
hands across the field. I didn't fix it (it's open tuning item #3, and the right
move is to measure expected hands/day before turning a knob), but I'd rather log
the observation now than let "the sawtooth works!" imply the cadence is also
right. It works. The *rate* is an open question, and the vice brake being this
aggressive near the trigger is the specific reason why.

## What I'm leaving for next time

- Cadence tuning (#3): the climb is rake-carried in its final third and that's
  slow. Either soften the vice taper nearer the trigger, or accept a slower
  cadence, but decide it against real hands/day, not vibes.
- `OVERLAY_CAP` (#5): at launch holdings the 0.12→0.06 drain wants ~158k and the
  cap is 250k, so it didn't bind *this* run — but it's close, and a flusher bank
  would clip. Still worth raising to ~6% of holdings.
- The policy hold's actual payoff is unprofiled (see caveat above).

Everything is uncommitted on `release-candidate`, flag-off, byte-identical until
flipped — same posture as the rest of the thermostat.

---
purpose: Narrative log of re-architecting exploitation — proving the hard override works, getting told to stop chasing bots, and three drift hits that pointed at one missing source of truth
type: design
created: 2026-06-12
last_updated: 2026-06-12
---

# Catching real players, and the nudge that finally bit

Picks up where "the +22.5 that wasn't" left off. We'd proven exploitation was a
soft logit nudge that changed nothing vs a believable opponent. This session was
about making it actually do something — and learning, repeatedly, that the test
beds were lying to me.

## Measure first, for once

I started with a read-only probe (`detection_fidelity_probe.py`): seat a TAG hero
vs each frozen human clone, monkeypatch the detector to record what it *actually
sees* in `AggregatedOpponentStats`, and diff that against the clone's authored
profile. No agent estimates this time — a reproducible number.

It paid off immediately. Punisher is authored as a disciplined aggressive reg
(AF 3.0, folds 70% to c-bets); in play the hero sees AF ~0.9 and fold-to-cbet
~0.00. Jeff (a real human's 4669-hand profile) reads `unmatched` — his vpip .35
sits in the dead zone between the nit cutoff (.30) and the station cutoff (.70),
and his real leak (postflop AF .18) is invisible because the station detector
gates on *global* AF. The authored leaks don't manifest; the moderate ones aren't
detected at all.

## "I do not care about catching bots"

I built a `loose_passive` detector — the literature "Fish" quadrant — and keyed
it, at first, on AF_postflop plus a not-a-nit VPIP floor. Jeff flipped
`unmatched → loose_passive`. I wrote "AF_postflop alone suffices."

Jeff: "the only thing you look for is low AF?" Fair hit. Low AF is ambiguous — a
calling station has low AF because it *calls*; a weak-tight folder has low AF
because it *folds*. Opposite counters. AF can't see folding. The Punisher result
should have warned me: its postflop AF was 0.77, a hair under my 0.80 ceiling —
the only thing keeping a reg out of the "station" bucket was the VPIP floor. A
knife-edge, not a read.

Then the bigger correction. I'd been quietly optimizing "does it catch
CallStation / Punisher / SpewyFolder" — and Jeff cut it off: the rule-bots were
throwaway fixtures. Because they were sitting in every harness, the logs (and I)
over-indexed on catching them. The actual game is a bot reading the *other
players at the table* — the human, and the AI personas — and reacting to their
leaks. The bots are only good for extreme stress cases now. I'd spent real effort
confirming a premise that was itself the artifact.

Two good things fell out of the reframe: `jeff.json` is a *real human*, so it was
the right bed all along, buried under the bots. And the canonical 4-quadrant grid
(TAG/LAG/Rock/Fish, with literature thresholds) already existed in
`poker/archetypes.py` — it just was never wired to the exploit layer, which had
grown its own narrower taxonomy bot-by-bot. The detection design was "done for
us"; we'd just never used it.

## The nudge that finally bit

The actual win. The first-principles test had shown the bluff-reduction *offset*
move air-bluffing 59.9%→59.8% vs a pure station — nothing. So I added a hard
override: pure air vs a confidently-read station, while composed, hard-set the
give-up line instead of nudging. `stop_bluff_probe.py`, CRN-paired:

- OFF: air_no_draw aggression 56.9%
- ON: **0.3%**
- ON+TILTED (tilt_factor forced 0): back to 56.9%

Draws (`air_strong_draw`) untouched at 82.6% — it kills pure bluffs, not
semi-bluffs. And the psychology gate holds: a tilted bot stops out-levelling and
reverts to base. That's the whole re-architecture thesis proven end-to-end on the
cleanest target — detection → real behavioral change → gated by emotion. The soft
nudge produces nothing; the hard override produces everything.

## Three strikes, one root cause

Then I tried to make the detector trustworthy enough for prod, and the test beds
fought me three times:

1. `call_rate_facing_bet` (calls / facing-bet decisions) — my "stickiness" axis.
   Calibrated across four clones: it read 0.80–0.92 for *all* of them, including
   the authored 90%-folder. Realized calling depends on what the hero bet, not
   just villain stickiness. Confounded.
2. So I reached for WTSD — the literature's real station/folder discriminator
   (sticky 0.5+, folder under 0.25). Jeff asked the right question: "we track
   WTSD, what's the problem?" And we mostly do — the numerator (`_showdowns`) is
   live, the archetype tool and clone derivation both compute it. The gap was one
   missing counter: a per-opponent saw-flop denominator. Cheap. I added it.
3. And WTSD read 0.000 for everyone. The sim feeds *actions* to the opponent model
   but never *showdowns* — `simulate_bb100` bypasses `MemoryManager.complete_hand`,
   so `observe_showdown` never fires. The denominator populated; the numerator path
   wasn't wired in sims.

The smoking gun was already in the tree: `_record_sim_equity_at_actions` is
documented as a "sim-side equivalent of `MemoryManager._record_showdown_equity_at_actions`"
— a deliberate duplicate recorder, written because the sim bypasses the prod path.
The showdown feed fell through the exact same crack.

That's when it clicked that I was fighting the same enemy each time: the same stat
family is computed in ~four places with four hand-written implementations, fed by
divergent prod/sim event paths. Authored≠observed, call_rate confounded, showdown
fed-in-prod-not-sim — all symptoms of definition duplication. Hardening detection
kept hitting "this stat isn't trustworthy across paths."

So I stopped digging and wrote it down: `OPPONENT_STAT_SOURCE_OF_TRUTH.md`. The
recommendation is deliberately *not* a pub/sub stats service (there's one
real-time consumer; that's speculative). It's a single source of truth for the
*formulas* — pure-function stat definitions every site imports — plus making the
event feed consistent (the sim should feed showdowns through the same path prod
uses, and the duplicate recorder should go). The source of truth is the
definition, not a runtime.

## Where it stands

WTSD is wired and persisted but dormant in the detector (still gating on
call_rate) until it can be validated, which needs the sim showdown feed. The hard
override is real and proven. The detector catches a real loose-passive human and
excludes the reg. Tier 2 (the fine per-spot override) is done; Tier 1 (the coarse
gear-switch) is still untouched. And the next person gets a doc instead of a
fourth re-derivation of WtSD.

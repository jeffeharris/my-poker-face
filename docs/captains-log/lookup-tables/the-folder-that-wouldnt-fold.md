---
purpose: Narrative log of hardening the station detector to "more than low AF" — the confound, the stat we already tracked, the test bed that wouldn't fold, and a handoff doc that came back implemented
type: design
created: 2026-06-12
last_updated: 2026-06-12
---

# The folder that wouldn't fold

Continues from "catching real players." The hard override was proven; the
detector behind it was still thin. Jeff had asked the question that drove this
whole stretch: "the only thing you look for is low AF?"

## One metric, and why it's the wrong one

He was right. My `loose_passive` detector keyed the "sticky" axis on low
AF_postflop plus a not-a-nit VPIP floor — effectively low AF. The problem is
that AF measures aggression, not folding: a calling station has low AF because it
*calls*, a weak-tight folder has low AF because it *folds*. Opposite leaks,
opposite counters (value-thin vs bluff-more), same AF. The detector couldn't tell
them apart.

So I added a stickiness signal — `call_rate_facing_bet`, calls over facing-bet
decisions — and calibrated it across four clones. It read 0.80–0.92 for *all* of
them, including the authored 90%-folder. Realized calling depends on what the hero
bet, not just villain stickiness. Confounded. The second metric was the wrong
second metric.

## "We track WTSD, what's the problem?"

WTSD — went-to-showdown — is the literature's actual station/folder
discriminator: a sticky station reaches showdown often, a fit-or-fold type rarely.
I said we didn't track it live. Jeff pushed back, and he was right to. We track
the *pieces* in four places (the archetype review tool, clone derivation, the
retroactive backfill, and the live model's `_showdowns` numerator). The only gap
was one counter: a per-opponent saw-flop denominator on the live tendencies. I
added it — same small pattern as the others.

And it read 0.000 for everyone. The sim feeds *actions* to the opponent model but
never *showdowns* — `simulate_bb100` bypasses `MemoryManager.complete_hand`, so
`observe_showdown` never fires. The denominator populated; the numerator path
wasn't wired in sims. The smoking gun was already in the tree, commented:
`_record_sim_equity_at_actions` is a deliberate "sim-side equivalent of"
`MemoryManager._record_showdown_equity_at_actions`, written because the sim
bypasses the prod path. A duplicated recorder, and the showdown feed fell through
the same crack.

That was the third time the same enemy had blocked me: the same stat family
computed four ways down divergent prod/sim paths, drifting. So I stopped digging
and wrote the design down — `OPPONENT_STAT_SOURCE_OF_TRUTH.md` — arguing for a
single source of truth for the *formulas* (not a pub/sub service we don't need),
and a consistent event feed. Jeff handed it to a fresh context.

## The handoff came back implemented

While I sat at the wall, the other context shipped it (PR #324): a shared
`stat_definitions.py`, and — the piece I actually needed — the sim showdown feed.
I fetched, fast-forwarded, and re-ran the calibration. WTSD now populated, and it
cleanly separated the disciplined reg (0.47) from the stations (0.78–0.80). A real
signal, at last.

## The folder that wouldn't fold

But it still didn't exclude the `spewy_folder_fish` clone — authored WTSD 0.30,
living at 0.704. The clone *doesn't actually fold in play*. I traced it: the
clone's facing-a-bet gate folds when equity is below pot odds times a multiplier
that **maxes out at 1.0** — textbook. It never *over*-folds, so it lays down only
true trash and calls everything else to showdown. Its authored "90% folder"
profile is a fiction the engine can't express. Every clone I had calls down like a
station; I had no faithful folder to validate the exclusion against.

So I built one. An additive `overfold_factor` lever on the clone (default 1.0 =
byte-identical legacy) that pushes the fold threshold past pot odds, plus a
`folder_fish.json` that sets it to 1.8 — a fish that genuinely lays down
pot-odds-correct hands. Same loose VPIP (.47) and low AF (.35) as a station;
nothing on the old axes could distinguish it. Live WTSD: **0.382**. The detector
excludes it (`loose_passive` False) while the two stations and the real-human
clone stay True at 0.78+. And it vindicated the flip — the folder's `call_rate`
was 0.617, *above* the old threshold, so call_rate would have kept it mislabeled;
WTSD is what excludes it. Existing clones came out byte-identical, the additive
lever earning its keep.

Then I flipped the live detector's sticky axis from call_rate to WTSD. The thing
Jeff pushed on three turns earlier — use more than low AF, and prove the third
axis against a bot that actually folds — was finally true and finally tested.

## The lesson, again

This is the third entry in a row whose moral is the same: the test bed lies, and
the discipline is to check it rather than trust it. The +22.5 evaporated against a
folding opponent. The authored clone stats didn't manifest in play. And here, two
different stickiness signals failed to exclude a "folder" — not because the
detector was wrong, but because the folder wasn't a folder. Every time, the fix
was to make the bed honest, then measure again.

Now running the bb/100 pass to see whether the proven behavior actually wins
chips, or just looks right.

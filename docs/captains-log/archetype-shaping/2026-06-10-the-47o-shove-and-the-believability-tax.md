---
purpose: Narrative log of the trash-4-bet-shove investigation — one prod hand (Alexander jams 47o into a 4-bet all-in) that unspooled into four merged PRs, plus the real wrong turns: a naive vs_3bet regen that collapsed the maniac, an EV gate that said no, and a prior session's captain's log I nearly lost to a stash-drop
type: reference
created: 2026-06-10
last_updated: 2026-06-10
---

# The 47o shove, and the believability tax

Jeff brought one prod hand: *"Alexander the Great jammed all-in with 47o after
two players were already all-in pre-flop. It makes no sense and it kills the
trust in the game."* That one hand turned into four PRs (#271–#274), a fifth for
a flake it surfaced (#275), and a sixth doc reconcile (#276).

## The diagnosis

The decision analyzer already *knew* the shoves were wrong — it scored both
`optimal=fold` at −25k EV. So the model wasn't the problem; the chart was. Two
decision snapshots for two different hands (47o and 89o) came back with the
**identical** `base_strategy_probs`. That's the tell: the `vs_4bet` chart was a
3-bucket stub — 165 of 169 hands shared one `{fold:.645, call:.223, jam:.132}`
blob, so 47o got AKo's line and jammed 13% of the time, bumped to ~22% by the
lag distortion, then sampled. The believability bug was a degenerate lookup
table, not a reasoning failure.

Two fixes, in order of blast radius:

- **#271 — runtime veto.** Facing a cold all-in pre-flop there's no skill in
  re-jamming vs calling; it's pure pot odds. So decide call/fold on eval7 equity
  and skip the chart sample entirely. A guardrail that catches *any* facing-all-in
  spot regardless of chart. EV-correct by construction.
- **#272 — fix the chart (vs_4bet).** Regenerate from the existing all-in equity
  matrix: value jams, suited/blocker bluffs, and **everything else exactly
  `{fold:1.0}`**. The pure-fold floor is load-bearing — the archetype/depth
  transforms all skip `fold>=0.999`, so trash stays folded across all nine charts
  even for the maniac. EV gate: neutral (−0.8 bb/100, CI spans 0). Clean.

## Wrong turn #1 — the vs_3bet recipe that collapsed the maniac

#273 was supposed to be "do vs_4bet again for vs_3bet." It was not.

I applied the same pure-fold-the-junk recipe and the mixed-field probe immediately
failed: **lag and maniac 4-bet cratered** (maniac 4.5% vs a 26–38 band). The stub
I was deleting was *load-bearing for the aggressive archetypes*: they got their
high 4-bet by amplifying the stub's universal 10% trash-4-bet. Remove it and the
maniac has nothing to amplify.

The fix was **polarization**: value hands and *suited* blocker bluffs carry the
4-bet (offsuit junk gets call/fold only, no raise key — so distortion can't invent
one). A maniac then 4-bets a wide *suited* range, never 72o. That recovered lag
cleanly.

## Wrong turn #1b — the maniac band was the real caricature

But the maniac still capped ~14–15%, because suited combos are only ~25% of the
deck — a polarized 4-bet *physically can't* hit 26–38% without offsuit trash. The
band itself was the artifact: it had only ever been reachable by 4-betting junk.

This wasn't mine to decide, so I stopped and asked. Jeff: *"believable maniac,
lower the band per §B."* Lowered `fourbet` 26–38 → 10–24 in `archetype_targets.py`,
cited the research §B (a *constant* high frequency is a caricature), and the field
went green.

A reframe I should have had sooner: the vs_3bet stub was *already in-band*. Unlike
vs_4bet, nothing was behaviorally broken. This was a believability change (a real
per-hand gradient, no offsuit trash 4-bets), not a bug fix. The bands probe was the
right gate for it.

## Wrong turn #2 — the EV gate said no, and that was the point

I ran champion_challenger anyway (gradient vs stub, TAG, 96k hands) and it came
back **CI-clear NEGATIVE: −3.8 bb/100**. The disciplined gradient *loses* to the
spewy stub head-to-head — because it bluffs offsuit trash less, and vs a clone
that over-folds to 4-bets, more bluffing extracts more. That's the exact
extract-from-passive confound the harness's own docstring warns about, and the
champion *was* the known-bad stub.

The honest move was to surface it as a tradeoff, not bury it under the green bands.
Jeff chose ship-and-document. So #273 is the one PR in the series that's an
explicit *believability-over-EV* trade — written up as such in the doc and the PR.
vs_4bet was a free win; vs_3bet costs ~4bb/100 against a trash-bluffer, and we said
so.

## The tail — a fix, a flake, and a near-loss

- **#274 — the bug the first hand surfaced.** Alexander's snapshot also showed
  `effective_stack_bb: 0.0` at a real ~78bb. `stack_utils` omitted committed `bet`,
  so a table of all-ins collapsed effective stack to zero. Fixed to `stack + bet`
  and consolidated the one correct inline copy (in push/fold) onto the shared
  helper so they can't drift again. The prod scenario now reads 78.23bb.
- **#275 — the flake I caused.** Adding `test_vs3bet_gradient.py` reshuffled the
  pytest-randomly xdist distribution and exposed a latent `StopIteration` in an
  *unrelated* mentor-stake test (no open seat at a home court the world-seed
  occasionally over-fills). I pulled the actual CI traceback to confirm it was the
  open-seat lookup (line 483, not a missing-table lookup) before hardening
  `_home_court` to guarantee an open seat. The lesson is the testing-notes one:
  passes-alone-fails-in-full-run is shared-mutable-global pollution.

## Wrong turn #3 — I almost deleted a prior log (the lesson that wouldn't take)

The sibling entry in this very folder
(`2026-06-10-perceptibility-and-the-stacked-pr-trap.md`) ends with a rule:
*uncommitted working-tree changes are almost never disposable.* I then spent the
session proving I hadn't internalized it. Switching branches to reproduce the
flake, I ran `git stash -u` (which stashes untracked files), then later
`git stash drop` — and that dropped the **prior session's uncommitted maniac
captain's log** along with it. Jeff's "did you get a captain's log in too" is what
caught it. Recovered the file from the dangling stash commit's untracked-tree
parent, and it's committed now (not left untracked, which is how it got
endangered in the first place). Same lesson, second time: if it's worth keeping,
commit it — don't leave it riding in the working tree where a stash can eat it.

## The shape of it

One hand → trace to the data layer → fix at runtime *and* at the source → hit a
real product fork (caricature vs believable) and hand it to the owner → take the
EV hit on the chin and document it → clean up the bug and the flake the work
surfaced. The two judgment calls were the owner's, the two wrong turns were mine,
and the most embarrassing miss was repeating a lesson already written down ten
feet away.

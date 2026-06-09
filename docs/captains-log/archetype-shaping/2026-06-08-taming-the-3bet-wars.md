---
purpose: Narrative log of diagnosing and fixing AI cash over-aggression — building the archetype review tool, the pre/postflop aggression split, and the measure-first loop that kept catching me being wrong
type: reference
created: 2026-06-08
last_updated: 2026-06-08
---

# Taming the 3-bet wars — "the cap, not the scale"

## What we set out to do

The player's complaint was visceral: every preflop felt like a shove-fest. AIs
3-betting and 4-betting into all-ins with trash, and a robotic "they always raise
by exactly 3× my bet." Underneath that, the real ask was a system: **a target
range per archetype** (VPIP/PFR/3-bet/c-bet/AF/all-in) and a tool to review actual
behavior against it — so we could *shape* the AIs into reliable, readable
opponents instead of guessing. Plus three specific worries: too many all-ins, a
"100% fold after I 3-bet them" exploit, and aggression "nudges" that fire in-game
at a comical 0.000002%.

## Wrong turn #1: I analyzed the wrong data and almost told him he was imagining it

First instinct: pull the local DB and measure. Raise:call was 2.8:1 — aggressive,
sure — but the **3-bet wars looked rare** (3.1% of hands) and the all-ins read as
ordinary tournament short-stack shoves. I was a hair from telling him his
anecdote was overblown.

It was overblown — *for that dataset*. The local DB was 96% **tournament sim/test**
hands (`Tester`, `P02`…`P04`). He plays **cash**. When he said "look at prod,"
prod cash told the opposite story and vindicated him completely: **3-bet in 25% of
hands, 4-bet in 10%, all-ins in 17%** — 3–5× normal — with hole cards showing
Q2-offsuit 4-bet-shoving and folding out AK. The lesson is one I keep re-learning:
*verify the premise against the right dataset before you "correct" the human.* I
nearly inverted the truth because the convenient data disagreed with him.

He then sharpened it himself: "what about the games I'm *not* in?" Good instinct —
his own defensive 3-betting could be inflating the table. The AI-only eval games
answered it: they 3-bet 32–54% *without a human present*. The aggression was
intrinsic, not a reaction to him.

## Wrong turn #2: I quoted a benchmark as if it were reality

When we discussed capturing the background-sim stats, I confidently cited "227
hands/sec" as the live rate to justify a lightweight design. He pushed back —
"I thought it was like 14 hands per 3s?" He was right. 227/sec was an *isolated
Phase-0 benchmark* in a code comment; the live cadence is single-digit hands/sec.
It didn't change the decision (bounded counters are still right — the sim runs
*forever*, so unbounded per-decision logging would balloon the table), but it
changed the *reason*, and I'd been about to ship a justification that was simply
false. Cite the measurement, not the folklore.

## The measurement layer, and a self-inflicted bug

Built the Archetype Review tool (target bands + a scored grid) and a lean
per-archetype stat recorder wired into the background sim. Two stumbles worth
recording:

- Registering an admin tab takes **three** edits (the union, the sidebar items,
  and `VALID_TABS` in the router). I did two. The tab silently fell back to
  "Personalities," and he found it before I did.
- A reviewer flagged that the recorder used `archetype_name` — which is
  anchor-derived and *cannot represent `weak_fish`* (it collapses to
  `calling_station`). Real bug, real data corruption. The fix (`_table_archetype_key`)
  then revealed the **same bug class** in the live snapshot labeling: it compared
  identity (`is`) against the `deviation_profile` *property*, which returns a
  `replace()` copy when a persona has spot-tendencies — so it silently labeled
  ~1050 prod decisions `unknown`. One bug class, two sites; the second only
  surfaced because we'd just fixed the first.

## The schema-version comedy

Mid-stream, `main` moved twice. My migration was `v156`; main merged its *own*
`v156`. I renumbered to `v157`; he warned "there's *another* v157 incoming." That
was the moment to stop fighting integers — main had just landed the per-file
applied-set migration system (built precisely for parallel-worktree collisions),
so I cut my migration over to a dated file. No version to collide on. Then PR #237
got squash-merged out from under me while I was still building on the branch; I
rebuilt the new work on a fresh branch off main. Annoying, but clean.

## The real fight: root-causing the 3-bet, and being wrong about the lever

I fanned out five perspectives (pipeline trace, chart frequencies, distortion
math, an empirical sim, and codex). They converged: over-3betting was
**distortion-driven for lag/maniac, chart-driven for tag**. So I lowered
`aggression_scale` on lag/maniac and measured.

**Almost nothing happened.** Maniac went 64% → 62%. I'd assumed `aggression_scale`
was the lever; the empirical loop said otherwise. The binding constraint is the
**per-action cap** (`max_per_action_shift`) — it *saturates*, so shrinking the
scale beneath it barely moves realized behavior. Lowering the *cap* finally cut
maniac's 3-bet to 47%… and dropped its postflop aggression factor **below tag's**.
A maniac less aggressive postflop than a TAG is a broken archetype. The global
knobs **couple the streets** — you cannot tame preflop 3-bets with them without
gutting the postflop wildness that *is* the maniac.

He saw it before I fully articulated it: "do we need to split it to pre and post
flop knobs?" Yes. The fix is a **facing-raise-scoped** aggression override
(`reraise_aggression_scale`), applied only at `vs_open`/`vs_3bet` nodes — global
aggression (postflop AF) preserved, re-raise *frequency* dampened. Maniac landed
at 47% 3-bet with AF restored to 4.59 (wildest again). lag dropped to 32% but was
**floored by its base chart** — the distortion split can only remove the
distortion, not the chart. So a second, surgical move: parameterize the chart
generator's freed-fold split (`raise_share`) so lag *flats* wide instead of
*3-betting* wide — same VPIP, lower 3-bet. lag → 25.9%, in band. Codex's review
nudged this the right way ("don't touch `keep_fold`/VPIP; shift the raise share").

The whole back half was a measure-first loop where the instrument kept telling me
my mental model was wrong, and the right answer only appeared because I believed
the numbers over the model.

## The 3× tell, and not pretending jitter is the fix

His "always exactly 3×" turned out to be a *different* axis from frequency: the
charts emit a single raise-size token per node. #240 makes it *less frequent*; it
doesn't make it *less robotic*. Shipped a live-path `sizing_jitter` band-aid
(2.6–3.4×), and — at his prompt — wrote the proper plan (`PREFLOP_SIZING_VARIETY.md`)
that builds on the existing `SIZING_PERSONALITY` design rather than reinventing it.
Naming it a band-aid in the code comment matters; the next person shouldn't think
it's solved.

## What shipped

- PR #237 — review tool + sim-capture (merged).
- PR #240 — pre/postflop split, looseness→raise decouple, target rescale, LAG
  chart trim, `unknown`-labeling fix, live size jitter (merged).
- PR #242 — handover doc + the tuning probe.
- Result: lag/maniac 3-bet wars in band, postflop identity preserved, VPIP intact.

## What I'd tell the next person

1. **Anchor to the dataset that matches the complaint** before you measure, and
   *before* you tell the human they're wrong.
2. The per-action **cap saturates** — it's the binding distortion constraint, not
   the scale. Don't tune the scale and conclude the lever is dead.
3. Global knobs that span streets will make you trade one archetype trait for
   another. Scope the knob to the spot.
4. When the chart is the floor, no amount of distortion-tuning gets under it —
   fix the chart (and shift `raise_share`, not `keep_fold`, to keep VPIP).
5. The instrument earns its keep by proving you wrong cheaply. Build it first.

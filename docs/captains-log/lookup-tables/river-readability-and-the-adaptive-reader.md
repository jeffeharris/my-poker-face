---
purpose: Narrative log of the river-readability work — finding the one face-up leak, fixing it, the supply-build dead end, and building the missing adaptive-reader instrument
type: design
created: 2026-06-01
last_updated: 2026-06-01
---

# River readability, the gated bluff, and building the adaptive reader

A fresh context picked up `BETTER_BOT_HANDOFF.md` with the goal: **a bot that's
hard for a competent human, without humans to test against.** The owner steered:
use sizing/bluffing to make the tiered bot harder to read, grounded in poker
theory, "use all the data we have except the other cards." This is the arc.

## The reframe that unblocked it

The prior session was stuck on "hard-for-humans is unmeasurable — no human data,
all opponents static." The owner's reframe dissolved it: **we don't need humans;
theory tells us what a thinking opponent exploits.** A bot is exploitable *by
definition* when its bet size leaks hand strength, when it never bluffs a size,
when it over-folds. All measurable from the bot's own decisions. No human needed.

I almost claimed "build an adaptive instrument" as a novel idea — then found the
prior session had already named exactly that fork ("build a range-reading attacker
first") and parked it. Owned it: my contribution was re-deriving *why* (the
objective was unmeasurable) and the concrete instruments, not the direction.

## The tell map → the river is the one leak

Built `measure_passivity --tell-map`: per (street, bet-size) the hand-class
composition of the bot's own betting range, vs the GTO bluff target `s/(1+2s)`. A
readability audit needing neither human nor oracle. (Bonus: point it at a human's
hand history and it finds *their* tells — useful for the training branch.)

The finding **reframed the prior session's whole focus**: the bot's range is
~balanced in aggregate; the leak is **the river**, not the turn. River pot+ bets
are 90–100% value, ~0% bluff (gap −25 to −44); the turn was already balanced —
which is *why* the prior T1 turn-reroute recovered only ~15%. It was de-facing-up
a street that wasn't the leak. Reproduced HU + 6-max, station + reg. Then audited
the rest: raises mirror bets (river under-bluffed, turn fine), preflop 3-bets are
wide/mixed (not face-up). **The whole aggressive game has exactly ONE readability
leak: the river bet.**

## T2 + the gate: a vs-human fix at zero fish cost

The fix had to *create* river bluff supply — T1 only relabels existing bet-mass,
but the river has ~none (the bot gives up air earlier). T2 (`_promote_check_to_bet`)
promotes give-up-air river checks to bets. Measured +1.90 vs the oracle (a reader)
/ −7.18 vs a caller — net-negative ungated, exactly the fish/human tension.

So the gate is mandatory, and it's what makes the fix *serve the goal*: fire the
river bluff ONLY vs a detected over-folder (`fold_to_big_bet ≥ 0.6`, the first
consumer of the dormant Phase A read). Validated: +1.90 vs reader (gain kept),
+0.00 vs caller (cost killed). Calibrated `river_bluff_fraction=1.0` and turned it
on — supply caps the bluff share at ~31% (< the 37% target), so max injection is
correct and there's no over-bluff risk. Gap −28 → −7.

## Wrong turn: the river-air "supply build"

The residual −7 looked like a supply problem (only 31% achievable). The obvious
fix — barrel more air on the turn so more reaches the river (the triple-barrel
line) — I built (gated turn air-barrel) and measured. **No-op.** River bluff share
25% with it on and off.

The premise was wrong: **give-up air already reaches a checked-to river.** Air dies
from *folding to a bet*, not from *checking* the turn — so barreling it a street
earlier adds zero river candidates (and air raised off the turn slightly *reduces*
supply). The ~31% cap is structural (the natural air:value ratio at the river), not
a turn-give-up problem. Measure-first paid for itself: we almost shipped an
EV-risky turn change that does nothing. Kept dormant as a documented negative.

## The read, validated against 57k real casino hands

The gate's whole safety story is "fish score low → gate stays off → no spew."
Reconstructed `fold_to_big_bet` (verbatim recorder logic) across 57,347 real casino
hands. **0 of 58 mature opponents trip the 0.6 gate.** Stations sit at 0.00–0.08,
the whole population maxes at 0.56. Safety proven; zero spew risk vs the real fish.

And the 0.6 threshold turned out **self-calibrating, not arbitrary**: it's the
breakeven fold rate for a 1.5× bluff (`1.5/2.5`), so the gate fires only when the
bluff is +EV by fold equity alone. No AI fish over-folds that much → the bot
correctly value-bets them. The honest flip side: at 0.6 the feature is *dormant vs
the entire current AI casino* — it activates only vs a genuinely over-folding
*human*, which the data can't supply. So the mechanism + threshold are confirmed
sound and safe, but the live benefit still couldn't be measured. That motivated the
last build.

## Building the missing instrument: the adaptive reader

The handoff's §2/§7 named it: the missing instrument is an *adaptive* best-responder
(the oracle is fixed — it only folds, so it can show the bluff-gets-through gain but
never "value gets paid because the reader is forced to call"). Built
`AdaptiveReaderState` + `build_adaptive_reader_strategy`: a competent reg that
observes the bot's revealed overbet hands (perfect observation = the strongest
realistic reader), estimates the bot's overbet bluff freq, and best-responds its
fold frequency — over-fold a face-up bot, call a balanced one. Wired into
`measure_passivity` (`ADAPTIVE_READER=1`), which feeds it the hero's river-overbet
classes each hand.

**It worked, and gave the cleanest number of the session.** HU, 4000h × 3 seeds:

| arm | reader learned bluff_freq | bb/100 (per seed) | mean |
|---|---|---|---|
| A: face-up overbet | **0.02** (value 61 / bluff 1) | 15.4 / 18.3 / 18.4 | **+17.4** |
| B: balanced overbet | **0.14** (value 61 / bluff 10) | 17.7 / 20.6 / 20.6 | **+19.6** |

**Live benefit of balancing = +2.2 bb/100, identical across all three seeds**
(+2.3 / +2.3 / +2.2). Two things this proves that the fixed oracle could not:
1. **The reader genuinely learned and discriminated** — 0.02 vs 0.14 observed bluff
   freq. The instrument adapts; it isn't a hard-coded fold.
2. **Balancing helps even vs a thinking, adapting opponent** — a clean, robust +2.2.

And the honest nuance the instrument surfaced: the reader's observed bluff freq in B
(0.14, bluff/all-overbets) is still well under its call-threshold (~0.375 for a
1.5×), so it *correctly keeps over-folding* — meaning the +2.2 is the bluffs getting
through, NOT the bot's value finally getting paid off. To force the reader to pay
off value, the bot would need to reach ~37.5% balance, which the structural supply
cap (§5e) prevents. So the adaptive reader independently re-derives the same ceiling
the tell map and oracle found — via a completely different mechanism (a learning
best-responder). Three instruments, one consistent story.

## The dual instrument: an adaptive bluff-RAISER (defense vs aggression)

The owner asked the natural next question: does the bot read aggression too — could
it tell a human bluff-raising it "10× in a row" from a calling station? The bot has
the *sensors* (AF, all-in rate, `equity_when_raising`, `compute_aggression_polarization`
which explicitly separates bluffers from value-raisers from stations) but the
"call-down vs a detected bluffer" *reflex* was the parked/inert piece — so it looked
like an open defense question. Built the dual of the reader to settle it.

`AdaptiveAggressorState` + `build_adaptive_aggressor_strategy` (human_clone.py): a reg
that bluff-raises its junk (would-otherwise-fold air) facing a hero bet, learns the
hero's fold-to-raise from the **visible** fold/call response (no perfect observation
needed — the dual is easier than the reader), and escalates while it's profitable.
Measure-first: build the instrument, measure the leak BEFORE building any defense.

There was no leak. HU, 4000h × 3:

| opponent | bot bb/100 | hero fold-to-raise |
|---|---|---|
| static reg (no bluff-raise) | +24.4 | 0.23 |
| adaptive bluff-raiser | +24.7 (no change — self-corrects) | 0.23 |
| relentless maniac (threshold 0, always) | **+31.3 (+6.9 — donates)** | 0.22 |

The bot folds to raises only ~22%, far below the ~50% a pot-raise bluff needs. So a
rational bluff-raiser learns the bot calls down and **stops**; a relentless one gets
**snapped off and donates +6.9**. The bot's sticky calling — its *liability* vs
honest value in a leaky pool — is precisely what makes it **robust vs aggression**.
No defense (Phase 2) needed. The "10× air-raises" worry is a non-issue: the bot calls
and wins. (The mirror exposure — over-paying a thin *value*-raiser — is real but is
ordinary poker, not a run-over.) Two adaptive instruments now exist, reader + raiser,
and both reach clean verdicts the static pool couldn't.

## Capped checking ranges — the first real defensive leak (small)

The dual of the overbet: when the bot checks, is its range capped → stabbable? Built
a check-range map + an adaptive stabber (bets junk a half-pot when checked to, learns
fold-to-stab). The check-range map surprised me: the FLOP is capped (5% strong) but
the **river is protected** (29% strong — the bot checks back / slowplays rivers),
the opposite of my worry. The stab A/B: the bot folds **41%** to half-pot stabs (vs
22% to raises — confirming the weaker-after-checking hypothesis), costing **−1.2
bb/100** (adaptive == relentless; 0.41 already clears the half-pot breakeven). So:
the **first genuine defensive leak** (bluff-raising had donated), but small,
flop-concentrated, river-protected, and ~0 vs the real fish (who don't stab). A gated
stab-defense (symmetric to the river-bluff gate) could capture it, but it's a real
build for a −1.2 ceiling that the actual pool never realizes — left as the owner's
cost/benefit call, recommended against on this session's repeated lesson (defensive
discipline costs more vs the value-betting pool than it saves). Measure-first again
kept us from a fix bigger than the leak.

## Where it landed

One real readability leak, found and fixed to its structural max, gated so it costs
nothing vs the fish and activates only vs the opponents who'd punish it — all from
theory, validated on real data, with a reusable adaptive-reader instrument for the
next face-up question. The one thing still beyond reach without live humans is the
exact magnitude of the benefit vs a real over-folding human — now precisely isolated
as the only open question, not a fog.

---
purpose: Grounded narrative log of the variety/fish validation + pricing + fish-migration session for a future write-up
type: reference
created: 2026-05-30
last_updated: 2026-05-30
---

# Captain's log — variety validation, pricing & fish migration (lookup-tables worktree)

## 2026-05-30 — running the handoff punch list

Picked up `VARIETY_VALIDATION_AND_DEPLOY_HANDOFF.md`. Goal: validate the shipped
variety/fish work (which went out unvalidated at short stacks), price the
aggressive end honestly, migrate the live fish, prep deploy.

Wrote one driver, `experiments/variety_eval.py`, that reuses
`measure_passivity`'s per-seed worker and sweeps a whole archetype×depth×field
grid in one process pool → compact markdown tables. Dev-first locally
(1500h×3), then Jeff said "use Hetzner for throughput" so I scaled to 3000h×8
on a 48-core ccx63.

### A — the real risk, and it held up

The fear: the precedence flip forces width-tier *100bb* tables at *every* depth,
so a Maniac might shove 100bb-wide ranges at 25bb. It doesn't. 25bb jam% topped
out at Maniac 4.3%; everything else ≤1.9%. The reason is the thing I didn't
expect to be so clean: the **range width** comes from the depth-agnostic table,
but the **sizing + jam layer is still depth-aware** — avg open size shrinks with
depth for every archetype. A loose range at 25bb just means more limps/small
opens, not shoves. PASS, no fix.

### B — I ran the wrong experiment first, and the right one inverted the premise

First pass I used the tiered **`Calling Station` archetype** as the "calling
field." Wrong instrument: that archetype is a weak passive *donator* (VPIP 45 /
AF 0.26), not a calls-down grinder. So aggression *extracted* from it and the
"honest cost" table showed aggression looking great — which I almost wrote up as
"reassuring" before catching that it answered nothing.

Swapped to the fields the handoff actually named: the Jeff_clone (realistic
calls-down human) and the always-call rulebot (the extreme). The result inverted
the handoff's own premise. The premise was "foldy fields make aggression look
+EV (overstated)." The data: every hero earns *far more* vs the calling fields —
Maniac +37 (foldy) → +218 (Jeff) → **+840** (never-fold). A field that calls is
a field that *pays off value and can't win without showdown* — it's the easiest
opponent, not the punisher. The premise conflated bluff-EV (higher vs callers)
with total-EV (much higher vs callers). The punishing direction is a competent
**folder**, and against the foldy proxy the *passive* heroes are the ones that
bleed (and bleed harder with depth). So: passivity is the punished trait,
aggression is robustly +EV. Lesson restated for the Nth time — the gate AND the
opponent both have to match reality; a "calling field" that's secretly a donor
lies just like a caricature bot does.

position_blind isolation (StationPBlind − Calling Station vs the foldy field)
came out exactly validating the existing $2-only stake gate: it makes the fish
lose *more* shallow (good drain) and *less* deep (would help deep fish). Keep it
stake-gated.

### D — depth is the cycling lever

Confirmed and sharpened: a Calling Station bleeds ~12.5× faster at 100bb than
40bb (−7 → −91), with the cliff between 60→80bb. Keep $2 shallow for a trickle;
deepening the bottom buy-in is the strongest knob if the economy needs faster
recycling.

### C/E — Jeff's calls

Asked Jeff two decisions up front (parallelizing the human latency while B ran):
he chose **apply the fish migration to this DB only** (defer the
lookup-tables→development merge — it's the fiddly one) and **no recurring eval**
(on-demand only; don't risk a leaked Hetzner box). Migration script
(`scripts/migrate_fish_spot_tendencies.py`, force-added) is WAL-safe-backup,
idempotent, dry-run-default; applied + verified on this DB. Found the prod fact
that matters: `deploy.sh` seeds without `--overwrite`, so existing prod fish
rows are skipped on deploy → the migration script is required for prod, not
optional.

Tore the box down (no servers left; ~25 min ≈ pennies). Everything staged on
lookup-tables, not committed — waiting on Jeff.

## 2026-05-30 (later) — the punisher test

Jeff: "do the punisher test" (the one open thread). Added a clean `over_bluff`-only
isolation (`calling_station_overbluff` profile + `StationOverBluff` archetype,
mirroring the existing position_blind isolation) and a sweep `P` in variety_eval,
then ran 2000h×8 on a fresh box.

The smoke test flagged something before the real run: StationOverBluff came back
**byte-identical** to plain Calling Station. Not a bug — `_over_bluff` only fires
on *unopened + air + turn/river* (hero must hold the betting lead with a busted
hand), and a passive station vs an aggressor almost never reaches that spot. The
full run confirmed it at scale: over_bluff Δ vs the punisher is +0.1 / −1.7, and
vs the over-folder it's ~0 too. So **over_bluff is near-dead weight on a passive
base** — the "cost of over-bluffing" can't even be priced there because the base
doesn't bluff with the lever maxed. The archetypes that *do* bluff (Maniac/LAG via
aggression_scale) are +EV even vs the punisher (+44 to +107). And the punisher
itself turns out beatable by stations (it barrels air, the station calls down) —
so it prices over-FOLDING cleanly but is a weak test of over-calling. The clone
set has no balanced GTO opponent; that's the only gap left.

Bottom line across foldy/calling/punisher: no hidden aggression cost anywhere,
passivity is the punished trait, and `position_blind` (not over_bluff) is the
fish's real EV leak. Box torn down. Committing.

## 2026-05-30 — chasing "can the game punish aggression?" to CaseBot, then sharpening it

Jeff's worry: does the game have any counter to relentless aggression, or does
"just bet" win? Long thread. The honest arc:

1. A lone Maniac beats a passive field +57 (blind-steals; the tight bots fold
   their blinds). HU, though, the same bots BEAT the maniac (+8…+17) — they widen
   to 53% VPIP and defend. So not an engine flaw — the static personalities just
   don't *adapt* in multiway.
2. Tried the engine's own adaptive counter (`hyper_aggressive`). A 2-seed smoke
   showed +36 "it works!" — I spun up Hetzner — and at 8 seeds it collapsed to
   −9/−2, CI spanning 0. **Pure noise; the per-seed signs had already disagreed
   in the smoke.** Re-learned (again): never trust a 2-seed CRN edge. Root cause
   in code: the counter widens calls vs *all-ins/big-bets* + tightens your own
   opens, but has **no blind/steal-defense** (the `fold_to_open`/PHASE_8_1 rule is
   explicitly unimplemented) — it defends the wrong street.
3. Tested "shift archetype" instead — only a full Maniac mirror breaks even
   (+0.2); LAG doesn't help (−49). And at a realistic mixed table (2 LAG + 3 TAG)
   a lone maniac still prints +29 (a normal TAG is −9.5 there).
4. Then **Jeff corrected me twice and both unlocked the answer:** (a) I'd called
   CaseBot a "fish" — it isn't; the clean tiered station drains −74, CaseBot is a
   competent adaptive bot. (b) "look at its rules" — CaseBot's `_strategy_case_based`
   *calls lighter vs aggression (>2.0 AF, −0.08 equity)* and plays ~everything
   preflop, so it **never gets blind-stolen AND snaps the over-bluffs.** It
   **demolishes the maniac field +175** where every tiered archetype lost −44…−50
   (GTO-Lite −480, so it's the strategy, not "any rule bot"). So the counter to
   aggression already existed in the codebase — wide blind-defense + adaptive
   call-down. CaseBot beats *everything* 6-max (+60…+340) — it's just a stronger
   adaptive bot than our personality caricatures (which carry deliberate leaks).

5. Jeff: "how does it play HU? … make it the best we have." Probed HU — and
   **CaseBot LOSES heads-up vs a single TAG (−29.6)**: it limps 100% / raises 2%,
   so a disciplined value-bettor isolates and value-owns it. Its whole edge
   (catching bluffs) evaporates 1-on-1 vs someone who doesn't over-bluff. So
   CaseBot is a *multiway field-exploiter*, not a sound fundamental player; its
   core (wide + low-PFR + call-down) is itself a calling-station leak.

So now sharpening it. First v2 attempt was *tighten the preflop calls* — wrong
diagnosis; the HU loss showed the leak is **passivity, not looseness.** v2 redone
as **raise-first preflop** (open/3-bet a real range, keep wide blind-defense, stop
limping), postflop still delegating to v1's adaptive play. Built a **gauntlet**
(`experiments/casebot_gauntlet.py`) — one scorecard across HU + 6-max vs every
field type, scored on the WORST cell.

**Then the gauntlet humbled me — I'd been chasing noise.** The "CaseBot loses HU
−29.6" that kicked this whole thread off was a **single-seed, 800-hand reading**.
At higher samples the same cell swung wildly: HU-vs-TAG came back −34.8 (300h×2),
then **+10** (600h×3), then **−59.8** (800h×4). HU-vs-Nit: +8.4 then −70. The HU
cells swing **±60 bb/100 across samples** — CaseBot's per-decision equity Monte
Carlo makes HU hands ~0.75s each, so I can't feasibly sim enough HU hands to get
a stable sign. **The HU "weakness" is unmeasurable at any scale I can run; it's
roughly break-even buried in variance.** The 6-max cells, by contrast, are
robustly positive every run (+57…+412).

And both "improvement" attempts were dead ends: a global raise-first rewrite
*regressed everything* (preflop aggression with v1's passive postflop bloats pots
then plays them passively); a hybrid (v1 multiway, gated on table-size==2, +
tight-value HU branch) keeps 6-max byte-identical but only touches the
*unmeasurable* HU case — so I can't validate it either, and a naive-aggressive HU
branch *spewed* far worse (−98) than v1's passivity.

I almost stopped there — "v1 is already best, HU is noise." Then Jeff pushed:
*"but it's just a simple heuristic, you can't see ANY way to improve it?"* Fair.
The cop-out was measuring against caricatures (which v1 already near-maxes) in
the noisy HU regime. The fix: measure the **low-variance** way — **v2 vs v1 at a
full table** (does the candidate beat v1?), plus vs the **clone profiles** (jeff
= calls-down human, punisher = competent reg), the realistic opponents.

That AB battery cracked it. First it ruled out the obvious: a tight-aggressive
rewrite (raise-or-fold + c-bet + fold-don't-pay-off) **regressed in every cell**,
incl. vs the punisher (+173→+5) — because the punisher *barrels air* and v1
*catches it* while the tight bot folds. Then wider-calling **also** regressed
(pays off the stations). So v1's range and call thresholds are a **local
optimum** — perturbing either way loses.

The lever that actually wins: v1 **under-extracts when ahead** — it limps
premiums (PFR ~2%), bets only 0.66 — while our whole pool **calls too much.** So
v2 = v1 + **bigger pots with strong hands**: value-raise premium/strong preflop,
overbet premium (1.2) / strong (0.9) + thin-value medium (0.6) when checked to;
everything else (the wide range + the call-down) stays v1. Result vs v1:
**jeff +116→+496, punisher +173→+382, Station +259→+394, TAG +42→+150,
mixed +120→+211, and it beats a table of v1s head-to-head +156.** 4–12× better
vs the realistic human clones. Only Maniac×5 dips (value-betting into an
aggressor; still +). Pure-static → identical in sim and prod. Shipped as
`case_based_v2` (`49af908b`), ready to promote the `casebot` bot type.

Lessons banked: (1) the HU-noise detour came from trusting a 1-seed number — the
[[feedback_verify_user_premise]] rule; (2) measure improvements the low-variance
way (candidate-vs-incumbent + realistic opponents), not absolute bb/100 vs
caricatures; (3) Jeff's "you can't improve a heuristic?" push was right — the win
was real, I'd just been measuring it wrong.

## 2026-05-30 (later) — the spewy fish, and "can the game punish aggression?"

Jeff asked to add a spewy aggressive fish. Built `spewy_fish` (loose table +
over_bluff + sticky) + `SpewyFish`. It spews (VPIP 58) but is a **universal
winner**: +67 vs grinders, +1426 vs callers. The tell is the air-bet column —
the engine's EV floor suppresses the bluffs exactly where they'd be called (43%
→ 14% vs the pure caller) and value-bets the donors. **A chart-based aggressive
bot can't be a losing fish** — aggression is EV-gated; only passivity is an
engine-compatible leak. Jeff: don't add it. Good call.

That surfaced his real worry: can the game punish aggression at all, or does
"just bet" win? Ran two experiments. **The field DOES lose to a maniac** (1
Maniac vs 5 tight bots: maniac +57; the bots play 10–16% VPIP and fold blinds to
its steals). But **heads-up the same bots BEAT the maniac** (+8 to +17, widening
to 53% VPIP). So it's not an engine flaw — it's that the static personalities
don't *adapt* (fixed tight range) in multiway, so a maniac steals blinds from a
passive field. Real poker: a maniac prints at a table of nits.

Then the wrong turn worth recording: I smoke-tested the engine's adaptive counter
(`hyper_aggressive`) on 2 seeds and got **+36 paired edge** — "the fix works!" —
and spun up Hetzner to confirm. At 8 seeds it collapsed to **−9 / −2, CI spanning
0, inconclusive**. The +36 was pure noise, and the per-seed signs had *already
disagreed* in the smoke (the exact tell I've written down twice). Lesson
re-learned: never trust a 2-seed CRN edge, especially with sign disagreement.
The honest result: the adaptive counter is **inert** vs aggression (re-confirming
EXP_004/005). So the reliable lever is field *variety* (don't let tables be
all-passive), not the counter. Box torn down.

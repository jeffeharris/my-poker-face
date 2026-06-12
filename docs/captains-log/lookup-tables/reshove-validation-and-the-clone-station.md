---
purpose: Narrative log of validating the 6max reshove table — the failed A/B, a gate that fixed nothing, and the clone hole that made every human clone a calling station
type: design
created: 2026-06-11
last_updated: 2026-06-11
---

# Reshove: validation, a gate that fixed nothing, and the clone-station

## The setup

The 6max push/fold table shipped with a reshove section (jam over a single
open) behind `PUSH_FOLD_6MAX_RESHOVE_ENABLED`, off. Routing-coverage said
reshove was the dominant short-stack spot (~66% of preflop decisions, ~17%→98%
coverage when on). What it didn't say was whether reshoving those spots *wins*.
So: run the bb/100 A/B before trusting it.

## Wrong turn 0 — it failed, hard

TAG vs the rule mix, reshove ON cost **−21 / −35 / −52 bb/100** at 8/10/12 BB,
worse the deeper it got. Vs a competent (GTO-Lite/ABCBot) field it was a wash.
The read: reshove is *field-dependent*. Nash reshove ranges are built to be
~break-even vs an opener who folds correctly; against a field that never folds
(stations, maniacs) you risk ~10–12 BB to win a ~3.7 BB pot with no fold equity.
The ranges weren't wrong — *unconditional* reshoving was.

## Wrong turn 1 — a gate that fixed nothing

The fix was obvious: only reshove when the opener has fold equity. I reached for
the existing "calling station" detector, `_is_hyper_passive` (high VPIP **and**
low AF). Built the gate, re-ran the A/B — and it was **byte-identical** to
ungated. −21/−35/−52, unchanged.

A safety gate that produces the exact same numbers as no gate is a red flag, so
I instrumented which openers the gate was consulted on. Answer: almost entirely
**ManiacBot** — the only rule bot that open-*raises* at 10 BB (the passive ones
limp/fold). And ManiacBot is vpip 0.97 / **AF 4.0**: hyper-*aggressive*, not
passive. So `_is_hyper_passive` returned False ("this one's aggressive, it must
fold"), the gate allowed the reshove, and the −35 leak stayed fully intact.

The lesson: "won't fold to a reshove" is **loose VPIP, not passivity**. A maniac
who plays 97% of hands never folds either — it just calls/4-bets instead of
calling. Re-keyed the gate on `vpip_per_voluntary_opportunity > 0.65` (catches
stations *and* maniacs). Re-validated: **+0.0 bb/100 at every depth** — the leak
was gone because the gate now suppressed reshove vs every rule-bot opener.

## The honest gap — upside unprovable

But +0.0 only proved the gate *stops the bleeding*. It never proved reshove
*wins* anywhere, because **no rule bot folds to a reshove** — every opener in
the roster is vpip>0.65. So I turned the flag on as a Pareto-safe bet (triple-
gated: flag + per-persona opt-in + fold-equity read; worst case a no-op) and
flagged the upside as unproven, needing a folding opponent.

## Wrong turn 2 — the clone was a calling station too

To prove the upside I needed an opener that folds to 3-bets. We had a "punisher"
clone (a hand-authored reg, vpip 0.26, folds-to-cbet 0.70) — exactly the
profile. But reshoving it still showed nothing. Reading
`human_clone.build_clone_strategy`'s preflop logic explained Jeff's old "the
clone turned into a calling station" complaint: facing **any** raise it gated
purely on hand tier — `pfr_tier → raise, vpip_tier → call`. Since it *opened*
with `pfr_tier`, facing a 3-bet its hand was still in `pfr_tier`, so it
**re-raised its entire opening range and never folded.** The clones weren't
stations because of bad data — the engine simply never modeled fold-to-3-bet.

Fixed it: facing a re-raise well beyond an open, a disciplined reg (high AF)
continues only a tight slice of its opens and folds the rest; a station (low AF)
keeps its full range. punisher now calls AA/KK/AKs/AQs/TT to a 10 BB reshove and
folds 99/AJo/KQo/A5s/22 — real fold equity. A station still calls wide.

## Closing the loop

Re-ran vs a Punisher_clone field: reshove fires (the gate allows the vpip-0.25
reg) and scores **−1.2 / +0.7 / +2.7 bb/100** at 8/10/12 BB — neutral to mildly
positive, the lean *growing* with depth (where it had been worst, −52, vs
non-folders). CIs overlap zero, which is honest and correct: vs a *disciplined*
reg the marginal reshove is break-even by construction; the real money is vs
over-folders, which punisher isn't. So the proof isn't "reshove prints" — it's
**"reshove is Pareto-safe: gated off it never leaks; gated on it's neutral-to-+EV
where fold equity exists."** Good enough to keep the flag on.

## Takeaways

- A safety gate whose A/B is byte-identical to no-gate is almost certainly
  inert — instrument *what it fires on* before believing it works.
- The "calling station" mental model (passivity) misses the loose-aggressive
  non-folder. Fold-to-aggression is a **VPIP** signal, not an AF one.
- Human clones collapse toward calling not from bad stats but because the engine
  never modeled fold-to-3-bet. That one hole is why past clones felt like fish.
- The bb/100 gate catches −EV that coverage gates can't — but a rule-bot field
  can't validate any fold-equity-dependent feature. You need a folder, and now
  the clone is one.

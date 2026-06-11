---
purpose: Narrative log of validating all 7 archetypes against their target bands — extending the probe, fixing the passive archetypes' chart-inherited 3-betting, and resisting the urge to chase a field-artifact
type: reference
created: 2026-06-08
last_updated: 2026-06-08
---

# Validating all seven — "the transform moved the wrong mass"

Continuation of [taming the 3-bet wars](2026-06-08-taming-the-3bet-wars.md). That
session tuned lag/maniac/tag. The handoff's backlog #1 said *do first*: validate
the other four (nit, rock, calling_station, weak_fish) against their target bands.

## The probe couldn't see the thing it was supposed to check

The handoff flagged two specific worries — `calling_station` over-PFR (~19.5) and
`nit` too-loose VPIP (~19). I opened the probe to run them and hit a wall: the
probe only measured 3-bet/4-bet/AF. It literally **could not measure the stats the
backlog was worried about.** So the first real work was widening the instrument:
tally every banded stat (vpip/pfr/threebet/fourbet/fold_to_3bet/af/all_in) for all
seven and score each against `ARCHETYPE_TARGETS` with a pass/warn/fail. Same lesson
as last time — the instrument has to be able to see the defect before you can chase
it.

That immediately paid off: the two flagged worries were **already fixed.** nit VPIP
15.0, station PFR 9.6 — both in band. PR #240's looseness→raise decouple had
quietly resolved them; nobody had re-measured. Good to confirm rather than re-tune
something that was already right.

## Wrong turn I caught in flight: the field artifact

First full run (all-Baseline field) lit up `fold_to_3bet` as a **FAIL for every
single archetype** — including the distortion-OFF Baseline control at 82%. The
tempting read: "systemic over-folding, big finding, go fix it." I almost did.

But the Baseline failing is the tell. Against a table of five *tight* bots, when
someone 3-bets you their range is genuinely strong — so folding a lot is *correct*,
not a bug. It's a property of the measuring field, not the bots. So instead of
chasing it, I built a **second instrument**: a mixed-field probe that seats six of
the seven archetypes at one table (rotating the sit-out) so each is measured
against a realistic field, the way the live `source=sim` counters will see them.

The mixed field is where it got interesting: fold_to_3bet came *down* but was **still
failing** (nit 86, maniac 67, station 83). So it wasn't *purely* an artifact — there
is a real over-fold underneath. The point is I only learned that by building the
tool to separate the artifact from the signal, instead of trusting my first
hypothesis either way. (I left it as the documented top backlog item, not a
half-tested fix — see below.)

## The real bug: the transform moved fold mass, never raise mass

The clean finding was the passive archetypes 3-betting too much: station 13.3 (band
1–5), rock 12.1, nit 9.2, weak_fish 8.6. A *calling station* 3-betting 13% of the
time is the archetype negating itself.

Tracing it was satisfying. The width-tier charts are built by transforms in
`build_archetype_charts.py` that cut fold mass and redistribute it. But they only
ever touched **fold** mass — the station chart still 3-bet AA at **85%**, inherited
verbatim from the base chart, because nothing in the transform reduced the existing
*raise* mass. So every derived chart carried the base's premium 3-betting as a
floor.

And here's where I nearly reached for the wrong hammer. The proven knob from last
session was the `reraise_*` distortion split. My first instinct was to add it to the
passive archetypes too. But thinking it through: that split *caps how far distortion
can move an action* — it's the right tool when distortion is **adding** 3-bets (lag/
maniac). For a passive archetype the 3-bet is **chart-inherited**, and distortion is
already trying to remove it; clamping the distortion does nothing, or worse. And the
per-action cap (last session's headline lesson — it saturates at ≤0.30) physically
cannot pull an 0.85 raise down to ~0. **The chart is the only lever that reaches the
premiums.** So: fix the chart, not the distortion.

The fix routes the existing re-raise mass into **call** — a station/fish *traps* its
premiums, it doesn't 3-bet them. Added a `damp_raise` param to `_station_facing`
(station + weak_station) and a new `build_tight()` that applies the same damp to the
tight chart nit/rock share, with `keep_fold=1.0` so the tight range is preserved (a
nit folds what it won't 3-bet — but a *premium* it now flats, it doesn't fold). Re-ran
the generator, three charts regenerated, loose/loose_mid untouched.

Result (mixed-field, 6k hands) — all four PASS: nit 9.2→4.7, rock 12.1→5.2,
calling_station 13.3→3.2, weak_fish 8.6→3.0. 1498 strategy tests green.

## Honest residue

- **rock PFR slipped 12.6→10.5** (WARN, 0.5 under the floor). It's mechanical:
  removing ~7 points of 3-bets removes preflop *raises*, and PFR counts those. I
  could nudge rock's RFI to recover it; I left it as a noted WARN rather than
  silently papering over it.
- **fold_to_3bet is still failing across the board**, and the *spread* is the real
  problem — nit 86 vs maniac 67 is ~20 points when a readable field should span ~50
  (a maniac should defend, a nit should fold). That's a base-chart vs_3bet change
  with strength implications (defending wider can be −EV), so it needs its own
  measure-and-validate pass, not a session-end tweak. Documented as the new top
  item. And a caution to the next person: real nits *do* fold 75–85% to 3-bets, so
  part of this may be the **targets** being slightly low, not the bots being wrong.
  Check the band before you chase it.

## Friction worth recording

- The sim spews `[EMOTIONAL] Failed to read zone effects` on every hand — 16MB of
  noise per run. `grep -vE "EMOTIONAL|zone_effects"` is mandatory; worth a real fix.
- pytest's summary line gets eaten by a sentry exit-hang inside the container, so
  "did the tests pass" returned nothing three times. `--junit-xml` + parse the XML
  is the reliable readout.

## What I'd tell the next person

1. **Make the instrument able to see the defect first.** The probe couldn't measure
   the very stats the backlog flagged; widening it was step zero.
2. **A failing control is a field artifact, not a finding.** When even the
   distortion-OFF baseline "fails," you're measuring the table, not the bot. Build
   the second field to separate them before you chase.
3. **Match the lever to the cause.** Chart-inherited behavior needs a chart fix;
   the distortion knobs (and especially the saturating per-action cap) can't reach
   a premium the chart raises at 85%. Don't reuse last session's hammer reflexively.
4. **Don't bring a base-chart change to a session-end knife fight.** The systemic
   over-fold is real but high-blast-radius; document it as the next focused pass.
5. **Suspect the target, not just the bot.** A band can be miscalibrated; sanity-
   check it against reference ranges before you tune toward it.

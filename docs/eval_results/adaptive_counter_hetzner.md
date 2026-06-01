TAG:

JSON source: /app/poker/personalities.json



  Updated: 0
  Skipped: 0 (already existed)


bb/100 CRN GATE — change='exploitation': TAG, challenger(ON) vs champion(OFF)
  opponent-modeling exploitation offsets ON (strength 1.0) vs OFF (strength 0.0) — identical opponent model both arms, only the applied offset differs. SNG-runner only; needs --opponent-model, a non-Baseline --archetype, and exploitable --backdrop opponents.
  backdrop (never-busting): Maniac, Maniac, Maniac, Maniac
  8000 hands × seeds [42, 142, 242, 342, 442, 542, 642, 742] | same deck/RNG/model both arms

── per-seed paired edge (ON − OFF, SAME hands), bb/100 ──
  seed 42: edge    -26.7   (ON -17.6 / OFF +9.1 bb/100)
  seed 142: edge     +3.7   (ON +9.1 / OFF +5.3 bb/100)
  seed 242: edge     +6.6   (ON -18.1 / OFF -24.7 bb/100)
  seed 342: edge    -35.0   (ON -44.8 / OFF -9.7 bb/100)
  seed 442: edge     -4.4   (ON -2.2 / OFF +2.3 bb/100)
  seed 542: edge    -15.8   (ON -27.3 / OFF -11.5 bb/100)
  seed 642: edge    +14.5   (ON -12.1 / OFF -26.6 bb/100)
  seed 742: edge    -17.1   (ON +6.8 / OFF +23.9 bb/100)

── pooled (all seeds) ──
  challenger (ON):    -13.3 bb/100
  champion  (OFF):     -4.0 bb/100
  the change flipped an action on 6800/64000 hands (10.6%) — the rest are exact ties (diff=0)
  PAIRED EDGE:       -9.3 bb/100   95% CI [-22.3, +3.7]   ⚠ per-seed SIGN DISAGREEMENT
  VERDICT: ➖ INCONCLUSIVE — CI spans 0 (more hands, or no real effect)
NIT:


bb/100 CRN GATE — change='exploitation': Nit, challenger(ON) vs champion(OFF)
  opponent-modeling exploitation offsets ON (strength 1.0) vs OFF (strength 0.0) — identical opponent model both arms, only the applied offset differs. SNG-runner only; needs --opponent-model, a non-Baseline --archetype, and exploitable --backdrop opponents.
  backdrop (never-busting): Maniac, Maniac, Maniac, Maniac
  8000 hands × seeds [42, 142, 242, 342, 442, 542, 642, 742] | same deck/RNG/model both arms

── per-seed paired edge (ON − OFF, SAME hands), bb/100 ──
  seed 42: edge    -11.9   (ON -15.9 / OFF -4.0 bb/100)
  seed 142: edge     +6.5   (ON -6.9 / OFF -13.4 bb/100)
  seed 242: edge     +2.7   (ON +1.2 / OFF -1.5 bb/100)
  seed 342: edge     +0.9   (ON -14.7 / OFF -15.6 bb/100)
  seed 442: edge    -11.3   (ON -12.6 / OFF -1.3 bb/100)
  seed 542: edge     +3.6   (ON -11.0 / OFF -14.6 bb/100)
  seed 642: edge     -6.6   (ON -22.3 / OFF -15.7 bb/100)
  seed 742: edge     +0.7   (ON +4.6 / OFF +4.0 bb/100)

── pooled (all seeds) ──
  challenger (ON):     -9.7 bb/100
  champion  (OFF):     -7.8 bb/100
  the change flipped an action on 4499/64000 hands (7.0%) — the rest are exact ties (diff=0)
  PAIRED EDGE:       -1.9 bb/100   95% CI [-12.2, +8.3]   ⚠ per-seed SIGN DISAGREEMENT
  VERDICT: ➖ INCONCLUSIVE — CI spans 0 (more hands, or no real effect)

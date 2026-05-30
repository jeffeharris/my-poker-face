# SWEEP B — aggression priced across fields, 2000h × 8 seeds
========================================================================

bb/100 by field. FOLDY=Baseline×5 (over-folds), JEFF=Jeff_clone×5 (realistic calls-down human), NEVERFOLD=CallStation×5 (always_call — punishes bluffs hardest).

The honest cost the foldy field hid = (FOLDY − JEFF) and (FOLDY − NEVERFOLD).

**Maniac**
| depth | vs FOLDY | vs JEFF | vs NEVERFOLD | foldy−jeff | foldy−neverfold |
|---|---|---|---|---|---|
| 40bb | +37.2 | +218.1 | +840.0 | -180.8 | -802.8 |
| 100bb | +72.3 | +275.3 | +1283.6 | -203.0 | -1211.3 |

**LAG**
| depth | vs FOLDY | vs JEFF | vs NEVERFOLD | foldy−jeff | foldy−neverfold |
|---|---|---|---|---|---|
| 40bb | +25.4 | +133.8 | +511.3 | -108.4 | -485.9 |
| 100bb | +23.1⚠ | +169.6 | +796.2 | -146.5 | -773.0 |

**StationPBlind**
| depth | vs FOLDY | vs JEFF | vs NEVERFOLD | foldy−jeff | foldy−neverfold |
|---|---|---|---|---|---|
| 40bb | -16.6⚠ | +49.2 | +291.7 | -65.8 | -308.3 |
| 100bb | -53.1⚠ | +73.2 | +391.2 | -126.3 | -444.3 |

**Calling Station**
| depth | vs FOLDY | vs JEFF | vs NEVERFOLD | foldy−jeff | foldy−neverfold |
|---|---|---|---|---|---|
| 40bb | -3.1⚠ | +39.3 | +220.7 | -42.4 | -223.8 |
| 100bb | -61.0⚠ | +70.4 | +336.4 | -131.4 | -397.4 |

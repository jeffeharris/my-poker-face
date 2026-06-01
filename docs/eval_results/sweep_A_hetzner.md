# SWEEP A — short-stack validation (vs foldy Baseline×5, 3000h × 8 seeds)
========================================================================

Metric columns: VPIP% / PFR% / jam% / avgOpen(bb) / AF / bb100

**Nit**
| depth | VPIP | PFR | jam | avgOpen | AF | bb/100 |
|---|---|---|---|---|---|---|
| 100bb | 15 | 9 | 0.1 | 4.3 | 0.29 | -25.7 |
| 50bb | 14 | 10 | 0.1 | 3.9 | 0.35 | -9.7 |
| 25bb | 14 | 10 | 0.1 | 3.6 | 0.35 | -0.5 ⚠SIGN |

**Rock**
| depth | VPIP | PFR | jam | avgOpen | AF | bb/100 |
|---|---|---|---|---|---|---|
| 100bb | 19 | 12 | 0.1 | 4.5 | 0.30 | -27.3 |
| 50bb | 17 | 12 | 0.1 | 4.0 | 0.35 | -6.2 ⚠SIGN |
| 25bb | 18 | 13 | 0.3 | 3.7 | 0.33 | +3.4 ⚠SIGN |

**TAG**
| depth | VPIP | PFR | jam | avgOpen | AF | bb/100 |
|---|---|---|---|---|---|---|
| 100bb | 23 | 19 | 0.1 | 4.6 | 0.62 | -15.5 |
| 50bb | 18 | 16 | 0.5 | 4.5 | 0.72 | +0.6 ⚠SIGN |
| 25bb | 14 | 13 | 1.3 | 2.5 | 0.86 | +0.7 ⚠SIGN |

**LAG**
| depth | VPIP | PFR | jam | avgOpen | AF | bb/100 |
|---|---|---|---|---|---|---|
| 100bb | 37 | 30 | 0.7 | 6.0 | 0.77 | +14.8 ⚠SIGN |
| 50bb | 35 | 29 | 1.0 | 5.6 | 0.83 | +23.8 |
| 25bb | 36 | 30 | 1.9 | 4.8 | 0.99 | +27.1 |

**Calling Station**
| depth | VPIP | PFR | jam | avgOpen | AF | bb/100 |
|---|---|---|---|---|---|---|
| 100bb | 44 | 15 | 0.1 | 4.9 | 0.25 | -72.8 |
| 50bb | 40 | 16 | 0.2 | 4.4 | 0.29 | -9.3 ⚠SIGN |
| 25bb | 41 | 17 | 1.2 | 3.8 | 0.29 | -12.3 |

**Maniac**
| depth | VPIP | PFR | jam | avgOpen | AF | bb/100 |
|---|---|---|---|---|---|---|
| 100bb | 55 | 47 | 1.6 | 6.2 | 1.26 | +52.5 |
| 50bb | 53 | 46 | 1.9 | 5.9 | 1.30 | +50.1 |
| 25bb | 56 | 50 | 4.3 | 4.6 | 1.31 | +28.8 |

#### Red-flag scan (25bb vs 100bb)
No red flags: no archetype shows runaway jam% or severe shallow bleed.

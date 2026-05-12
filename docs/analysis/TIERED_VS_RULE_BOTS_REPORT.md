---
purpose: Per-opponent EV analysis of tiered bot vs rule_bot mix at HU and 6-max
type: analysis
created: 2026-05-12
last_updated: 2026-05-12
---

# Tiered Bot vs Rule_Bots Evaluation

**Date:** May 12, 2026
**Tooling:** `experiments/simulate_bb100.py --six-max-vs-rules`, `experiments/analyze_6max_vs_rules.py`
**Total hands:** ~14,000 (sim) + 400 (per-archetype analyzer)

---

## TL;DR

The tiered bot architecture **wins or loses based on opponent composition, not table size.** Against a 6-max mix of 5 rule_bots, aggressive archetypes (Maniac, LAG) are net-profitable; passive ones (Nit, Rock, Calling Station, TAG) are net-losing. The win is driven entirely by exploiting *passive* rule_bots — every tiered archetype loses chips to ManiacBot (relentless all-in pressure). Phase 6 (opponent exploitation) is the architectural answer.

---

## What we ran

1. **HU sim vs GTO-Lite (rule_bot)** — 2,000 hands per archetype, all tiered archetypes vs the `pot_odds_robot` rule
2. **6-max sim vs 5-rule_bot mix** — 500 hands per archetype, opponents = `[GTO-Lite, ABCBot, CaseBot, CallStation, ManiacBot]`
3. **Per-opponent diagnostic** — 200 hands of one archetype vs the same 5-rule_bot mix, tracing action distribution and per-opponent chip transfer

Format details:
- 100 BB stacks, 100/200 blind structure (cash-game style, no escalation)
- Dealer rotates each hand for positional fairness
- Tiered bots run with personality distortion + math floor enabled; no Layer 3 LLM expression (we want raw decision quality)

---

## Headline results

### HU vs GTO-Lite (2,000 hands per matchup, tight CIs)

| Archetype | bb/100 vs GTO-Lite | 95% CI |
|-----------|--------------------|--------|
| GTO-Lite mirror     | -6.0   | [-116.5, +104.6] (sanity ✓) |
| Nit                 | -24.7  | [-40.7, -8.7]   |
| Rock                | -25.0  | [-44.1, -6.0]   |
| TAG                 | -30.2  | [-51.7, -8.7]   |
| Calling Station     | -31.5  | [-52.9, -10.1]  |
| Baseline (Layer 1)  | -53.1  | [-74.7, -31.5]  |
| LAG                 | -57.9  | [-83.2, -32.7]  |
| Maniac              | -65.7  | [-96.3, -35.2]  |

**Every tiered archetype loses to GTO-Lite at HU.** Aggressive archetypes lose *more* than passive ones (Maniac -65.7 vs Nit -24.7). Even Baseline (Layer 1 only, no personality distortion) loses -53.1 — the 6-max preflop charts simply don't transfer to HU ranges.

For context against the asymmetric finding documented earlier (vs BaselineSolverBot), Maniac swings from +43 vs Baseline to -65.7 vs GTO-Lite. The difference: GTO-Lite *folds* the math-correct amount, denying Maniac the dead-money harvest that worked against the non-adapting baseline.

### 6-max vs 5-rule_bot mix (500 hands, deterministic, two runs confirmed identical)

| Archetype | bb/100 vs 5×rule_bot mix | 95% CI |
|-----------|--------------------------|--------|
| Maniac             | **+1235.0** | [+921.7, +1548.2] |
| LAG                | +340.4      | [+108.7, +572.1]  |
| TAG                | -173.7      | [-281.0, -66.4]   |
| Baseline (Layer 1) | -199.8      | [-323.6, -76.0]   |
| Nit                | -200.3      | [-291.1, -109.5]  |
| Rock               | -261.5      | [-365.2, -157.8]  |
| Calling Station    | -295.7      | [-486.0, -105.4]  |

Initial read: "6-max is where the architecture shines." But this hides what's really happening.

---

## Why Maniac actually wins (200-hand diagnostic)

A net +1088 bb/100 result for Maniac decomposes by opponent:

| Opponent | Opponent net loss | Per-hand swing |
|----------|-------------------|----------------|
| CallStation | **+3,379 BB**     | favorable      |
| ABCBot      | +2,406 BB         | favorable      |
| GTO-Lite    | +1,615 BB         | favorable      |
| CaseBot     | -1,238 BB         | unfavorable    |
| ManiacBot   | **-3,985 BB**     | catastrophic   |

**Maniac is farming the passive rule_bots and getting blown out by ManiacBot.** The headline +1088 only works because the mix is net-passive (3 passive opponents, 2 aggressive) and the passive ones bleed faster than the aggressive ones extract.

Same pattern for Nit (-174 bb/100 net):

| Opponent | Opponent net loss for Nit |
|----------|---------------------------|
| CallStation | +3,161 BB |
| GTO-Lite    | +1,995 BB |
| ABCBot      | +791 BB   |
| CaseBot     | -1,498 BB |
| ManiacBot   | **-4,798 BB** |

Nit also farms passives, loses to ManiacBot — same shape, smaller magnitude. Maniac wins the average because it harvests dead money faster.

### Action distribution: 86% of Maniac's hands never see a flop

| Stat | Maniac | Nit |
|------|--------|-----|
| VPIP / PFR | 43% / 33% | 28% / 8% |
| Preflop-only hands | 86% | 80% |
| Reached river | 6% | 8% |
| Postflop AggFactor | 0.13 | 0.02 |

Two things stand out:
- **Decision EV concentrates in preflop**, because most hands close there. The strategy table's preflop accuracy matters more than its postflop coverage.
- **Postflop AF is collapsed for both archetypes**. AF of 0.02-0.13 means the tiered bot is barely raising/betting postflop, even when its design says "aggressive." Likely cause: the rule_bots' uniform preflop behavior breaks the strategy table's range model.

---

## What this means architecturally

The architecture's design — "personality distortion of solver baselines" — does what it's supposed to:
- ✅ It exploits opponents who play a fixed, exploitable strategy (CallStation, ABCBot, GTO-Lite — all rule-based, none adapt)
- ✅ It produces archetype-distinct EV signatures (Maniac/LAG win, Nit/Rock/Station lose, in the right rough order)
- ❌ It cannot defend against an opponent whose strategy ignores reads (ManiacBot's all-in spam), because the tiered bot's strategy table doesn't reweight under sustained pressure
- ❌ The 6-max preflop charts do not generalize to HU (Baseline at -53 vs GTO-Lite makes that explicit)

**This is precisely the gap Phase 6 (opponent exploitation) is designed to fill.** From the spec:

> Phase 6 (Opponent Exploitation - v2): Formalize opponent stat tracking. Exploitation logit offsets gated by `adaptation_bias`. Minimum sample size gates. Validation that high-adaptation characters adjust appropriately.

If we tracked ManiacBot's stats over 50 hands (very high VPIP, very high PFR, all-in shoves common), an exploitation layer would tighten our calling range and we'd stop paying off his shoves. Without that layer, archetype clamps make every tiered bot a fixed-strategy target.

---

## Implications for ship decisions

**Headline:** The system is shippable for entertainment and exploit-able-opponent matchups. It is *not* a tournament-winning machine, and was never spec'd to be.

| Use case | Recommendation |
|----------|----------------|
| Cash-game-style 6-max, mixed opponents | ✅ Ship. Archetype shaping is real and entertaining. |
| HU vs a competent opponent | ⚠️ Document underperformance. Postflop tables are HU-specific but preflop ranges are 6-max-wide. |
| Tournament play | ⚠️ The push/fold endgame favors rule_bots. Tiered survives but doesn't dominate. |
| vs a single aggressive opponent | ❌ Defer to Phase 6. No adaptation = no defense. |

**Next investment priority:** Phase 6 (opponent exploitation), specifically against the `aggressive constant pressure` opponent profile. The data above identifies it as the clearest failure mode. Implementation should:
- Track per-opponent VPIP/PFR/AF/aggression-frequency over a rolling window
- Apply exploitation logit offsets gated by `adaptation_bias` (per the spec)
- Validate that adaptive characters (high `adaptation_bias`) reduce calling frequency vs over-aggressive opponents

---

## Reproducibility

```bash
# Headline 6-max-vs-rules sim (500 hands × 7 archetypes = ~17 min)
docker compose exec backend python -m experiments.simulate_bb100 \
    --hands 500 --six-max-vs-rules

# Per-archetype diagnostic with action breakdown
docker compose exec backend python -m experiments.analyze_6max_vs_rules \
    Maniac --hands 200

# HU vs single rule_bot
docker compose exec backend python -m experiments.simulate_bb100 \
    --hands 2000 --opponent GTO-Lite
```

All sims are seeded (default seed=42) and deterministic across runs given the same code state.

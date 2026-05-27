---
purpose: Consolidated forward plan after the chart-frequency frontier was mapped to its edges — why multi-street postflop coherence is the next lever, how to test it, and what NOT to re-chase
type: guide
created: 2026-05-27
last_updated: 2026-05-27
---

# Postflop: the next lever (and the ruled-out frontier)

> **For a fresh context.** Branch `lookup-tables`. All Python runs in Docker:
> `docker compose exec -T backend python ...`. This is the successor to
> `PREFLOP_WIDTH_HANDOFF.md` (resolved). Read the captain's log
> `docs/captains-log/lookup-tables/eval-harness-and-exploitation.md` for the
> full narrative arc; this doc is the actionable plan.

## TL;DR

The cheap **frequency-chart** frontier is tapped or refuted. Three separate
"obvious improvement" hypotheses were each **measured and overturned** this
session, all with the same root cause. The binding constraint is **multi-street
postflop skill** — the c-bet→barrel follow-through, not any frequency table. The
concrete next lever is the **`multistreet_context` barrel-continuation layer**,
re-judged on the sound CRN/attribution gates (it read "inert" only on the old
coarse evals). Build nothing else until that's measured.

## What shipped (production behavior change)

- **Wider late-position RFI** (`4f5fb311`): CO 17.4→27.3 / BTN 25.1→47.5 /
  SB 20.2→40.3%, GTO-shaped pure opens. Steal-aware A/B: +15.97 bb/100 vs jeff,
  +5.33 vs punisher, CI-clear. Tight chart preserved; 50/25bb depth charts left
  tight (wide-at-short-stack unmeasured). This is the one gameplay delta on the
  branch vs `development`.

## The unifying diagnosis (why frequency charts are tapped)

Three refuted hypotheses, one root cause:

| Hypothesis | Measured verdict |
|---|---|
| "Tight late opens are correct" | WRONG — widening is +EV vs folders (shipped) |
| "Wide opens bleed vs calling stations" | WRONG — wide *crushes* stations (+147 vs CallStation) |
| "The bot under-c-bets HU → c-bet more" | WRONG — `hu_aggro` is −EV: −7.74 vs jeff, −14.54 vs station |

**Root cause (same as the preflop README's lesson):** aggressive/GTO frequencies
presume a GTO **multi-street follow-through** this bot lacks. It c-bets, gets
called, then **gives up the turn 51% of the time** — so a wider/more-aggressive
flop frequency just bloats pots it abandons. A frequency chart (solved or
hand-authored) sets the *flop* frequency, but the turn give-up is a **separate
downstream decision** — so no pure-frequency change can fix it. The bot's
passivity is largely *correct compensation* for weak barreling.

## The next lever: multi-street barrel coherence

The candidate mechanism already exists, flag-gated OFF:
- `tiered_bot_controller.py`: `enable_multistreet_context` (default `False`),
  `multistreet_h1_barrel` (barrel continuation), `multistreet_h2_foldbarrel`
  (fold to a sustained double-barrel). Module `poker/strategy/multistreet_context.py`.
- It read **+4.3 bb/100 (H2) / inert (H1)** on the OLD evals — but those were the
  coarse, station-inflated, steal-blind instruments we've since replaced. **It
  has never been judged on the CRN bb/100 gate or the per-node attribution gate.**

**Plan (measure before building, per the session's hard lesson):**

1. **CRN gate A/B** — `experiments/champion_challenger.py --change multistreet`
   (and `multistreet_h1`, `multistreet_h2`) vs the bot itself, AND
   `experiments/measure_passivity.py --mode on/h1/h2 --opponents jeff` /
   `punisher` (steal/realization-aware) + `--heads-up` (HU is where the give-up
   is worst). Does barrel-continuation pay, and vs whom?
2. **Per-node attribution** — `experiments/ab_node_attribution.py` to localize:
   *which* turn/river nodes does barreling help vs bleed? (The gate that turns
   "it helps" into "it helps at these nodes, bleeds at those.") Note: attribution
   currently A/Bs chart tables; to A/B a flag-flavor change, add a flag-aware arm
   (small extension) or use the CRN gate's per-hand deltas.
3. If it pays: tune barrel selection/sizing (which hands continue, on which
   turns), measured per-node. This is *logic*, not a chart.

The honest framing: this attacks the actual binding constraint. If barreling
*also* reads neutral/negative on the sound gates, then the bot's postflop ceiling
is structural (→ the parked solver program, expensive, and multiway is
research-grade — see codex note below), and the cheap improvement frontier is
genuinely exhausted.

## Tools available (all built/validated this session)

- `ab_node_attribution.py` — paired-CRN **first-divergence per-node attribution**
  (exact decomposition; conservation-checked). Arms: chart variants, `slices`,
  `hu_aggro`; flags `--stack-bb`, `--heads-up`.
- `measure_passivity.py` — Tier-A passivity diagnostics; `--heads-up` (HU mode),
  `--mode on/h1/h2` (multistreet arm), `--opponents jeff|punisher`,
  `--leak-report` (per-signature realized-vs-chart surface).
- `champion_challenger.py` — CRN bb/100 gate, `--change <preset>` (parallel
  session's; coordinate).
- `exploit_bb100.py` — bb/100 exploitation gate (parallel session's).
- `docs/EVAL_RUNNER.md` — Hetzner burst runner (safety rails: `poker-bot-optimization`
  context only, never prod; confirm billing; always tear down). 32-core dedicated
  quota; ccx53 in `hil` if `ash` is out of stock.

## Do NOT re-chase (measured dead/closed this session)

- **Preflop width** — shipped; frontier closed.
- **Opponent-adaptive open width** — `EXP_003` closed-negative (no opponent makes
  wide lose → no target to tighten toward).
- **HU postflop frequency charts** — refuted (`hu_aggro` −EV vs all); the leak is
  follow-through, not flop frequency.
- **Stack-size charts** — low value; the "short-stack leak" was a Jeff-station
  artifact (overturned). Only real gap is 6-max <15bb push/fold (low frequency).
- **Multiway postflop solver** — costly detour (codex): multiway is research-grade
  (GTO Wizard custom multiway postflop caps at 3 players; Monker needs
  64–128 core / 512GB–1.5TB RAM). Use solvers *selectively* for HU/3-way
  principles, not a production multiway chart.

## Pending (low priority)

- **Re-measure the restored slices** (`low_spr`/`3bp`, on-branch from `dd098d13`)
  on the attribution gate at **25/50bb** + with a **3-betting opponent** (for the
  3BP slices — they never fire vs non-3-bettors). Low surface area at 100bb.

## Coordination

The parallel session owns the **exploitation layer** work (CRN gate, per-sub-rule
ablation, the Hetzner matrix) — their captain's-log entry reports the +22.5
exploitation bundle is carried by 2 rules (`value_vs_station`, `hyper_passive`)
and is "inert in production." The multistreet lever overlaps their territory
(`champion_challenger`, `multistreet_context`); coordinate before picking it up.

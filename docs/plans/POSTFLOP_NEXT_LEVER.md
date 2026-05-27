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
postflop skill** — the c-bet→barrel follow-through, not any frequency table.

**UPDATE (2026-05-27, resolved):** the `multistreet_context` lever has now been
judged on the per-node attribution gate, HU vs coherent opponents — the decisive
test. The layer *as shipped* (H1 all-streets) is **null** (+1.73 vs jeff, CI∋0;
−0.95 vs punisher), confirming the rule-bot CRN null was real. **But the
attribution gate localized the null** into a +flop/−river decomposition (river
barrel-continuation bleeds vs *both* opponents — a resolved draw bluffing into a
caller), and **flop+turn-only H1 is CI-clear +EV: +3.33/+4.01-OOS vs an
over-folder, +11.94 vs a station, neutral (−0.34) vs a reg, +0.65/+1.98 in 6-max.**
**Shipped** (`enable_multistreet_context=True`, `multistreet_h1_streets={FLOP,TURN}`,
H2 off). This is a *coherence* win (per-street logic), not a frequency table —
exactly the lever predicted. See the captain's log entry of the same date.

**UPDATE 2 (2026-05-27, BIGGER lever found — sizing/overbets):** the chart's bet
menu caps at `bet_100` — **the bot is structurally incapable of overbetting.**
Adding **value overbets** (nuts/strong, ~150% pot, turn+river) measured +EV or
neutral vs *every* opponent type, never negative: punisher (reg) **+13**, jeff
**+42** HU / **+73 6-max**, station +159, nit/lag +11-12. The biggest postflop
lever of the session — and pure chart-data (the resolver already handles
overbets). Bluff overbets add ~nothing (bot rarely bets air late); size is a
plateau vs the reg (clone has no size-fear → don't over-tune). **Not yet shipped**
(chart-authoring work pending). See "Sizing / overbets" below + captain's log.

## What shipped (production behavior change)

- **Wider late-position RFI** (`4f5fb311`): CO 17.4→27.3 / BTN 25.1→47.5 /
  SB 20.2→40.3%, GTO-shaped pure opens. Steal-aware A/B: +15.97 bb/100 vs jeff,
  +5.33 vs punisher, CI-clear. Tight chart preserved; 50/25bb depth charts left
  tight (wide-at-short-stack unmeasured). This is the one gameplay delta on the
  branch vs `development`.
- **Flop+turn H1 barrel-continuation** (multistreet layer flipped ON, 2026-05-27):
  `enable_multistreet_context=True`, `multistreet_h1_streets={FLOP,TURN}` (river
  leg dropped — measured −EV), `multistreet_h2_foldbarrel=False`. Per-node
  attribution A/B, HU: +3.33 [+0.14,+6.52] / +4.01-OOS vs jeff, +11.94 vs station,
  −0.34 (neutral) vs punisher, +0.65 in 6-max. CI-clear vs the exploitable
  extremes, never bleeds. Sims bypass `__init__` so this touches real games only.

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

## The next lever: multi-street barrel coherence — ✅ MEASURED + PARTIALLY SHIPPED (2026-05-27)

> **Resolved.** The plan below was executed. Outcome: the layer *as shipped*
> (H1 all-streets + H2) is null on the per-node gate HU vs jeff/punisher — the
> self-play/rule-bot CRN null was *real*, not a coarse-gate artifact. But
> attribution localized a robust −EV **river barrel** leg (resolved draw → bluff
> into a caller) hiding a +EV flop/turn leg. **Flop+turn-only H1 shipped**
> (CI-clear +3.33/+4.01-OOS vs over-folder, +11.94 vs station, neutral vs reg,
> +0.65/+1.98 6-max). H2 stayed OFF (inert/−EV). Tooling added: `--a-mode/--b-mode`
> + `--h1-streets` on `ab_node_attribution.py`; `h1_streets` knob on the layer.
>
> **Follow-ons (open):** (1) the `value`-only H1 variant (drop `air_strong_draw`)
> vs a high-WtSD opponent — does it beat the all-classes flop+turn config?
> (2) does barrel *sizing* (not just frequency) move it? (3) the turn leg is only
> mildly +; is there a turn-specific refinement? (4) optionally pool/extend the
> 6-max runs for a tighter production-magnitude CI (two independent runs, +0.65
> and +1.98, both lean positive but neither is CI-clear — low-power because H1
> fires in only ~2.2% of 6-max hands). None are blocking; the win is banked.

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

## Sizing / overbets — MEASURED 2026-05-27, ship pending (chart-data)

The biggest postflop lever found this session. Full matrix on the attribution
gate (HU, 12-24k paired-CRN hands; arms `size_collapse`, `overbet_value`,
`overbet_polar[_<size>]` on `ab_node_attribution.py`):

- **Within-menu size selection is cosmetic.** `size_collapse` (flatten all bet
  sizes → `bet_67`) vs jeff = −1.13 [−6.77, +4.51], neutral. Tuning 33/67/100 is
  not the lever.
- **The bot can't overbet — menu gap.** Chart bets cap at `bet_100`; the resolver
  already handles `bet_150+` (zero plumbing). **Value overbets (nuts/strong, 150%
  pot, turn+river) are +EV or neutral vs EVERY opponent, never negative:** station
  +159, jeff +42 HU / +73 6-max, **punisher (reg) +13 [+8.5,+17.5]**, nit +11.5,
  lag +12.2. Localized to TURN ≫ RIVER, dry static boards.
- **Bluff overbets add ~nothing** (`overbet_polar` ≈ value-only; bot rarely bets
  air late). **Size**: flat vs the reg (150/200/300 = +13/+16/+14), monotonic vs
  the sticky clones — but that's a **clone artifact** (no size-fear); don't
  over-tune. 150-200% is the defensible production size.

**Ship design (next):** add overbet weight to nuts/strong on dry turns (+ some
river), value-only, ~150-200% pot, **with a multiway/active-count gate** (the
6-max +73 fires multiway, where overbetting `strong_made` into a reg-heavy field
is riskier). Pure chart-data — either a load-time transform or authored JSON
columns. **Caveat:** the probe is a crude max-overbet (relabels all value bets);
the clones can't model overbet psychology, so +13 vs the reg is the conservative
floor, not a humans number.

## Tools available (all built/validated this session)

- `ab_node_attribution.py` — paired-CRN **first-divergence per-node attribution**
  (exact decomposition; conservation-checked). Arms: chart variants, `slices`,
  `hu_aggro`; flags `--stack-bb`, `--heads-up`, **`--a-mode/--b-mode`**
  (off|h1|h2|on — A/B the multistreet *flag flavor* on the same chart) and
  **`--h1-streets`** (e.g. `flop,turn` to drop the −EV river barrel). The
  flag-flavor + street knobs are how the multistreet ship above was measured.
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

---

## Exploitation thread closeout + a multistreet CRN data point (exploitation session, 2026-05-27)

Closing the parallel exploitation thread; two findings hand off to the plan above.

### Multistreet HAS now been judged on a CRN gate — null vs rule-bots (but not the decisive test)

The plan says multistreet "has never been judged on the CRN bb/100 gate." It now
has, partially: `exploit_bb100.py --change multistreet --archetype TAG` (CRN
paired-replay, 8k×3) vs the fish (CallStation+FoldyBot) **and** reg
(GTO-Lite+ABCBot) backdrops returned **+0.0 / +1.0 bb/100, CI spans 0 — null.**

The caveat that keeps this from *closing* the question: this gate is
**hero-vs-fixed-rule-bot**, not the **HU / self-play coherent-opponent** scenario
the plan rightly emphasizes. Rule-bots don't reproduce the call-flop-then-fold-turn
dynamic where barrel-continuation should earn, so this null means *"multistreet
doesn't pay vs rule-bot fields,"* **not** *"multistreet is dead."* It's a mild
negative prior, nothing more. **The decisive test is still HU vs a coherent
opponent** (`champion_challenger --change multistreet` + `measure_passivity
--heads-up`). Note it's now *also* runnable on the CRN gate vs realistic opponents:
`exploit_bb100.py --change multistreet --archetype TAG --backdrop Jeff_clone,Jeff_clone`
— clone backdrops landed this session (`3386a656`). If the HU/coherent test *also*
reads null, the structural-ceiling conclusion is earned.

### Exploitation final status (corrects the "inert in production" line above)

"Inert in production" was an overstatement (corrected after discussion):

- **Decomposition** (CRN matrix, 8k×3): the +22.5 bundle is carried by **2 of 7
  rules** — `value_vs_station` (+13.3/+11.1 fish/reg) and `hyper_passive`
  (+9.1/+13.1). The other 5 are exact +0.0 vs these fields (never trip / never
  flip an action): cut candidates.
- **vs human clones: +0.0 exact — but largely CORRECT by design.** You should
  only deviate from GTO vs a genuinely exploitable opponent; a roughly-balanced
  player (Jeff_clone vpip 0.35) correctly gets no deviation. "Sharp vs extremes,
  idle vs balanced" is the intended *"punish the bet-every-street maniac /
  call-and-fold-river station"* behavior — and it does that (+22.5 vs those
  caricature rule-bots).
- **One real bug, not restraint:** Jeff_clone's modeled fold-to-cbet reads
  **0.00** despite his profile folding ~45% to c-bets — a genuine, *realistic*
  exploit (barrel him more) the detector can't see. A c-bet-detection gap worth
  fixing regardless of keep/cut.
- **Open question (NOT "dead weight"):** are the detector thresholds (station =
  vpip ≥ 0.70 = literal always-call) catching the leaks real opponents actually
  have, or sitting above them? Resolve by characterizing the real opponent
  distribution (LLM personalities + game-DB / Range-Explorer vpip/af/ftc), then
  decide threshold calibration. The layer **stays** (it does its founding job vs
  extremes); the work is the ftc bug + calibration, not removal.

Full narrative: captain's log, "the exploitation matrix on Hetzner."

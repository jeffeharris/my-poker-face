---
purpose: Opinion-agnostic handoff so a fresh context can re-examine "build a bot that's hard for a human" without inheriting prior bias
type: reference
created: 2026-06-01
last_updated: 2026-06-01
---

# Better-bot handoff — read with fresh eyes

## 0. How to use this document

This is a deliberately **neutral** record. It separates **measurements** (facts:
what was run, the numbers, the CIs, the harness, the caveats) from
**interpretations** (claims a prior session drew from those facts). Interpretations
are marked `⟦INTERPRETATION⟧` and should be **re-derived, not inherited** — several
are non-obvious and some may be wrong. There are no recommendations here on purpose.

The prior session's running notes live in the project memory and in
`docs/plans/BUILD_A_BETTER_BOT.md`, `SIZING_AWARE_OPPONENT_MODELING.md`,
`OVERBET_BALANCING.md`. Those carry the prior author's framing; this file is the
de-biased entry point.

## 1. The objective (fixed; set by the project owner)

**"Build a better bot" = a bot that is hard for a competent HUMAN to beat.**
- This is the success criterion. Not fish/AI-economy extraction — that is a
  separate role for a different bot and is **out of scope** for this goal.
- A bot that crushes weak AI ("fish") but is exploitable by a skilled human does
  **not** satisfy the objective.

## 2. The central, unresolved fact

**No human-vs-bot data exists.** Every measurement below is the bot vs other
**bots, frozen human-"clones" (rule-bot approximations), or a fixed "oracle"
instrument.** The success criterion — difficulty for a competent human — has never
been measured directly. Two ways exist to measure it; neither has been done:
- (a) a human plays the bot and we record results/exploits;
- (b) a *learned/solving best-responder* (e.g., CFR or RL) that adapts to and
  exploits the bot's leaks, used as a proxy for a thinking opponent.

All current eval opponents are **static** (do not adapt to the bot). This matters:
a static opponent cannot demonstrate an exploit that requires *reading and
adapting to* the bot — which is most of what a human does.

## 3. Assets that exist (facts)

| Asset | What it is |
|---|---|
| `RegPlus` (`_strategy_reg_plus`) | Rule bot. Playable bot type `regplus`. Disciplined value-bettor: overbets value, folds to big bets, **never bluffs**. |
| `CaseBotV2` (`case_based_v2`) | Rule bot, bot type `casebot`. ~95% VPIP value-extractor / fish-hunter. |
| Tiered solver bot (`sharp` / `TieredBotController`) | Chart + solver + personality "anchors" + exploitation/multistreet/overbet layers. The production opponent. Mixes bet sizes (`jitter`); has some semibluff barreling. |
| Oracle punisher | Eval-only instrument: a clone that perfectly folds non-nuts to any ≥1.2×-pot bet. A *fixed* stand-in for "a player who reads sizing." `--adaptive-opp` / `oracle_punish_overbets`. |
| Phase A sizing reads | Per-opponent `sizing_polarization_score` + `fold_to_big_bet` on `OpponentTendencies` (built + integrated; currently **read-only**, no consumer). |
| Overbet-bluff mechanism (T1) | `apply_overbet_context` can route air→overbet to polarize it. **Dormant** (`overbet_bluff_fraction=0.0`, production byte-identical). |
| Harnesses | `measure_passivity` (bb/100 vs fields; opponent-modeling OFF), `ab_node_attribution` (paired per-node EV, first-divergence attribution), `casebot_gauntlet`, `exploit_bb100` (opponent-modeling ON), `sng_runner`. |

## 4. Measurements (facts)

Numbers are bb/100. "fair" = raw minus the hero's self-play baseline (see §6.1).
CIs are 95% where shown.

**4.1 RegPlus vs static opponents** (`measure_passivity`, fair-adjusted):
- Beats `CaseBotV2` HU +102 / 6max +38; vs `jeff_clone` +115/+192; vs
  `punisher_clone` +60/+120; rule-bot gauntlet worst cell +8, positive everywhere.
- Three purpose-built **static** exploiters (`TrickyReg`, `TrickyAggro`,
  `Exploiter`) all **lost** to RegPlus (−149 to −443 fair).

**4.2 RegPlus's structure** (facts, from code + `--leak-report`): bet size is
near 1:1 with hand strength (overbet=nuts, check=give-up); it has **no bluffing
range** (never bets to make a better hand fold as a pure bluff).

**4.3 Tiered bot's value-overbet is face-up** (`ab_node_attribution`, HU, 30000h):
- vs normal `jeff` (no sizing read): **+60.9** [+52.7, +69.2]
- vs `oracle` (perfect sizing read): **−22.2** [−28.0, −16.4]

**4.4 Overbet-bluff (T1) effect** (`ab_node_attribution`, HU, 30000h, max
injection `overbet_bluff_fraction=1.0`):
- vs `oracle`: **+3.37** [+0.98, +5.77]
- vs normal `jeff`: **−5.66** [−8.65, −2.67]

**4.5 Bluff supply** (`measure_passivity --leak-report`, tiered bot): of all its
bets, ~22% pure air + ~18% weak; but the overbet (overbet_context) is 0% air.
Street split: TURN `air_no_draw` bets 16%, `air_strong_draw` 71%; RIVER air
betting negligible (it gives up river air).

**4.6 "PolarValue" bracket** (`measure_passivity`, 6max, fair): vs a maximally
face-up nit (bets big only with nuts), `CaseBotV2` +207 > `RegPlus` +81 >
tiered +50.

**4.7 Preflop steal-defense audit** (`ab_node_attribution`, punisher/nit):
- Combined "loose vs production" chart: +26 [+9, +43] vs punisher.
- Isolated `vs_open`-only widen: −1.86 [−12, +8] vs punisher; **−13.67 [−26, −2]
  vs nit**. Several `vs_open` rollup nodes flip sign (e.g. BB +2.02 → −2.47)
  between the combined and isolated runs.

**4.8 Self-play baseline artifact** (methodology): `X vs 5×X` ≠ 0 in
`measure_passivity` (CaseBotV2 +39, Reg −6, RegPlus −50.7…−77). Cross-bot
comparisons need this subtracted.

## 5. Interpretations the prior session drew (⟦re-examine⟧)

Each is a claim, not a fact. Listed with its evidence and its uncertainty.

- ⟦INTERPRETATION⟧ "RegPlus / the bot is robust vs all *bots* but exploitable by a
  *human*, because it's face-up and never bluffs." Evidence: §4.1–4.2, and the
  oracle leak §4.3. Uncertainty: the oracle is a *fixed* instrument, not a human;
  "a human would exploit this" is **inferred, not measured** (§2).
- ⟦INTERPRETATION⟧ "Three candidate fixes are phantom/low-leverage": adaptive
  profile-switching (4.1), defense-vs-big-bets (4.6), steal-defense (4.7) — each
  judged an artifact or low-value. Evidence: isolation tests + sign flips (4.7).
  Uncertainty: all judged vs **static** opponents and via first-divergence
  attribution, which **demonstrably misled at least once** (4.7 sign flips). A
  fresh look could re-test these against an *adaptive* opponent.
- ⟦INTERPRETATION⟧ "The only measured-effective path to de-face-up'ing the bot is a
  bigger **barreling range** (make it bet air to later streets so it has bluffs to
  polarize), because the minimal overbet-bluff (T1) is supply-limited." Evidence:
  §4.4 (+3.37 ceiling) + §4.5 (thin later-street air). Uncertainty: only the
  *overbet* slice was measured; T1 used one mechanism; the "barreling range is the
  fix" claim is **untested** (no barreling range was built).
- ⟦INTERPRETATION⟧ "Discipline ≠ balance; a value bot can be robust vs bots without
  bluffing." True vs the static pool (4.1); says nothing about humans (§2).

## 6. Methodology gotchas (facts — these bit the prior session)

1. **Self-play baseline** (§4.8): subtract `X vs 5×X` before comparing bots.
2. **First-divergence attribution** (`ab_node_attribution` rollups): per-node
   bb/100 can be a *selection artifact* — confirm with an isolated A/B (4.7).
3. **Opponent-modeling is OFF in `measure_passivity`**: any adaptive/read-driven
   behavior won't fire there. Use `exploit_bb100` / `sng_runner --opponent-model`
   for read-dependent measurements.
4. **All opponents are static** (§2): they cannot exhibit a read-and-adapt exploit.
5. **The oracle only punishes the overbet** (≥1.2×): it is not a general best
   responder; a balanced *overbet* can beat it while the rest of the bot's game
   stays face-up.

## 7. Open / untested directions (unordered, no recommendation)

- A **learned/solving best-responder** (CFR/RL) as an adaptive exploit-finder and
  human proxy — the missing instrument (§2).
- **Human-in-the-loop** measurement (owner or testers play the bots; log bb/100 +
  which exploits work). The app supports playing `regplus`/`casebot`; the data
  pipeline (`player_decision_analysis`, Range Explorer) exists.
- A **barreling range** (create later-street air) ± the overbet polarization (T1
  infra exists, dormant) ± the multiway/regime gate designed in
  `OVERBET_BALANCING.md` §3.
- **Re-testing the "phantom" fixes** (§5) against an adaptive opponent rather than
  static bots.
- **Other face-up dimensions** beyond overbet sizing (bet-vs-check range, turn
  give-up frequency, capped lines, blind defense vs an *adaptive* stealer) — none
  measured vs an adaptive opponent.
- Directions not yet considered.

## 8. Reproduce the key measurements

```bash
# face-up overbet leak (the proven, human-relevant signal)
docker compose exec -T backend python -m experiments.ab_node_attribution \
  jeff 10000 42,142,242 --hero TAG --a wide --b wide --overbet-b --adaptive-opp --heads-up

# overbet-bluff (T1) effect vs oracle and vs a caller
docker compose exec -T backend python -m experiments.ab_node_attribution \
  jeff 10000 42,142,242 --hero TAG --a wide --b wide --overbet-a --overbet-b \
  --overbet-bluff-b 1.0 [--adaptive-opp] --heads-up

# bluff supply / where the bot bets air, by street
docker compose exec -T backend python -m experiments.measure_passivity \
  --hero TAG --opponents jeff --heads-up --hands 3000 --seeds 42,142 --leak-report

# RegPlus vs the field (rule-bot gauntlet)
docker compose exec -T backend python -m experiments.casebot_gauntlet --hero RegPlus
```

## 9. Bias warning (explicit)

The prior session repeatedly drifted toward a "fish-extraction / value-machine"
framing that the owner had **explicitly rejected**, and repeatedly offered "ship
the value machine" as an option it is not. Discount that framing entirely. The
objective is §1: **hard for a competent human.** Equally, treat the owner's and the
prior author's *hypotheses* (e.g., which specific fix will work) as untested until
re-measured — the point of this handoff is a genuinely fresh read.

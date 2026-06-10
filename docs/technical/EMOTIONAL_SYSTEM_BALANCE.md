---
purpose: First-principles game-balance redesign of the AI emotional/psychology system — the axes, the state→behavior signatures, the event drivers, and the per-archetype targets, so tilt is believable AND noticeable
type: design
status: proposed
created: 2026-06-10
last_updated: 2026-06-10
---

# Emotional System Balance — First Principles

> **Status: PROPOSED design for review. Not implemented.** Supersedes the ad-hoc
> tuning of the current system. Motivated by the EXP_009 Phase-A diagnosis
> (`docs/experiments/EXP_009_EMOTIONAL_TUNING_BELIEVABLE_NOTICEABLE.md`). Builds on
> `PSYCHOLOGY_DESIGN.md` / `PSYCHOLOGY_OVERVIEW.md` (which it largely *validates* at
> the conception level — the 2D quadrant model is right; the **wiring** is wrong).

## 1. Why this exists (the diagnosis, in three measured facts)

EXP_009 measured the live system (tiered bots, 2 sims, ~3200 hands) and found:

1. **Emotions are already a large but MONOTONE force.** The always-on emotional
   shift moves play hard (+0.128 aggression-mass on tilted spots) — but in
   essentially **one direction**: the only states that fire (`overconfident`,
   `tilted`) both push toward *more aggression*. The fear pole (fold / clam-up /
   spiral) never fires.
2. **The fear pole is structurally dead — a catch-22.** `shaken` (the dramatic
   ±0.30 state) needs `confidence < 0.35`, but `compute_baseline_confidence`
   (`psychology_model.py:464`) defines confidence as
   `f(baseline_aggression, risk_identity, ego, self_belief)` — so the aggressive
   risk-seekers who actually tilt have the **highest** confidence (~0.80, floating
   to 0.96 on wins) and can only reach the cocky corner. The personas who could get
   scared (low ego/aggression) don't tilt at all.
3. **The system under-fires vs its own PRD** (91.7% baseline-band vs the 70–85%
   target) and is **flat for risk-averse personas** (Churchill/Scrooge ~0%
   penalty-time).

Root cause: **`confidence` is welded to chip-winning and trait-aggression.** That one
coupling collapses a 2D emotional space into a 1D "how aggressive am I feeling" line.
This redesign decouples it.

## 2. What the system is FOR (and the balance constraints that follow)

The emotional system is an **exploitable tell system**. Its product value is not
psychological realism — it is: opponents that are (a) *varied* (anti-robotic), (b)
*characterful* (distinct people), and (c) **beatable by a human who learns to read
them.** (c) is the actual fun, and it dictates the hard constraints any design must
satisfy:

| # | Constraint | Why | How we'll check it |
|---|---|---|---|
| **B1** | **Skill > emotion** | A non-reading opponent must not be taxed to death; emotion adds *variance + a readable edge*, not a systematic loss. | Bounded session EV: the emotional bot's own EV vs its composed baseline stays within a target band over 200+ hands (corpus-EV probe). |
| **B2** | **Directionally exploitable** | A tell is a *consistent, punishable bias* (over-aggression you trap, over-folding you bluff). Random noise is not exploitable. | Each state has ONE dominant exploit direction; Δaction-frequency has a stable sign per state (reachability/EV probes). |
| **B3** | **Readable & persistent** | The human must be able to perceive the state (avatar/chat/sizing) and it must last long enough to read. | Telegraph on entry; episode length ≥ a few hands (persistence work). |
| **B4** | **Character-true** | Same event → different response per persona; archetypes live in different home states. | Per-archetype state-mix vs targets (§7). |
| **B5** | **Episodic & recoverable** | States are reached, felt, recovered — not chronic, not instant. | PRD bands hold; mean hands-to-recover bounded; no persona chronically pinned. |

**Drama level: a TUNABLE magnitude knob, default MEDIUM — "visible swings."** At the
default a state produces a *single-hand-readable* shift (a heated maniac visibly
overbets; a shaken nit visibly folds), exploitable on sight, bounded by the divergence
clamp. The magnitude is **not hardcoded** — it is a global scalar (`EMOTIONAL_MAGNITUDE`,
see §4.1) so the whole system can be dialled calm→wild without re-deriving the per-state
tables, and can later surface as a **player-facing slider** (a "table spice" / opponent
volatility control).

## 3. The axes (keep two; decouple their meaning)

Keep the 2D quadrant model (it is readable and good). Energy stays a **flavor/tempo**
modifier (how a state manifests, not which state). The fix is in the two state axes:

| Axis | Means | Driven DOWN by | Driven UP by | Baseline from |
|---|---|---|---|---|
| **Composure** (C) | Emotional regulation: controlled ↔ reactive | Adversity — bad beats, big losses, coolers, needling, fatigue/card-dead | Wins, disciplined folds, time (recovery) | poise (high poise = high, stable C) |
| **Conviction** (K) *(recast of `confidence`)* | Belief in my **reads/decisions this session**: trusting ↔ doubting | **Epistemic** losses — bluff called, shown a bluff, hero-call wrong, folded the best hand, out-leveled | Epistemic wins — successful bluff, correct hero-call, read confirmed, out-thinking a rival | self_belief + experience (NOT aggression/ego/winning) |

**The central move — decouple K from chips and from aggression.** Today a cooler
*and* a bluff-getting-snapped both read as "confidence down," and aggression/ego
inflate the baseline. First principles: **losing chips ≠ losing belief in your
reads.** A bad beat is a *composure* hit — you played it right, got unlucky; your
reads are fine. Getting *shown a bluff* or *hero-called wrong* is a *conviction* hit —
you got out-thought. Splitting these is both poker-psychologically true and exactly
what makes the low-K (fear) corner reachable for an aggressive winner who is quietly
being out-read.

> Implementation note: this is a **re-definition of the existing `confidence` axis's
> baseline formula and event drivers**, not necessarily a variable rename. Cheapest
> path keeps the `confidence` field, rewrites `compute_baseline_confidence` + the
> event→axis table (§6), and leaves the zone geometry mostly intact.

## 4. The four states + behavioral signatures (medium-drama magnitudes)

Composure × Conviction → four readable states with **opposing** behavioral poles (a
real 2D behavior space, not a monotone push). Magnitudes are **full-intensity action-
mass shifts**, scaled in practice by intensity (depth into the zone) × expressiveness
and bounded by the existing divergence clamp.

|  | **High conviction (K)** | **Low conviction (K)** |
|---|---|---|
| **High composure (C)** | **Commanding** — controlled aggression, thin value. agg **+0.05–0.08**, bluff +small, sizing normal. *(reward zone; subtle)* | **Guarded** — cautious, doubting. agg **−0.12–0.15**, bluff **−**, fold-to-bet **+0.15**, sizing −. *Exploit: bluff relentlessly, value thin.* |
| **Low composure (C)** | **Heated** — reckless, "I KNOW I'm right and I'm tilted." agg **+0.15–0.20**, looseness +, oversize **+25–30%**, hero-call +. *Exploit: trap with strength.* | **Shaken** — erratic, spiraling. Variance widens; **risk-split**: risk-seekers punt (jam/oversize) **+0.20**, risk-averse fold/scared-call **+0.20–0.30**. *Exploit: bluff the folders, value the punters.* |

Design properties this table enforces:
- **B2 (directional):** each off-diagonal state has a single dominant, opposite
  exploit — Heated = trap, Guarded = bluff, Shaken = read-the-split. Commanding is
  the small +EV reward for staying regulated.
- **The two poles now oppose.** Today everything is the top-left→bottom-left
  aggression push; here Guarded/Shaken pull the *other* way, so the net emotional
  force across a session is **balanced** (supports B1), not a one-way aggression tax.
- **Medium drama:** +0.15–0.20 agg at full Heated intensity is a clear single-hand
  read, but the clamp keeps it from becoming "always all-in."

### 4.1 The magnitude knob (`EMOTIONAL_MAGNITUDE`)

The §4 numbers are the **default (medium)** point of a tunable scalar, not constants.
The effective per-state behavioral shift is:

```
applied_shift = base_signature(state)         # the §4 table value
             × intensity                       # depth into the zone (0–1)
             × expressiveness                  # per-persona leak (B4)
             × EMOTIONAL_MAGNITUDE             # the global drama knob (default 1.0)
then bounded by the divergence clamp.
```

Design rules for the knob:
- **Default 1.0 = medium** (the ratified "visible swings" calibration). 0 = emotions
  perceptible internally but behaviorally inert (≈ today's "off"); ~2.0 = theatrical.
- **It scales magnitude (size of the swing), NOT frequency (how often states fire).**
  Frequency is the axis volatility (§6.3) and stays a separate concern — a future
  slider could expose both, but they are distinct knobs and must be tuned separately.
- **Config now, player-facing later.** Implement as a `zone_params`-style override
  (`get_zone_param('EMOTIONAL_MAGNITUDE')`, same mechanism EXP_009 used for the sweep)
  so it is sim-tunable per arm immediately; the same value can later back a
  per-table/per-difficulty UI control ("calm" 0.5 … "wild" 1.5) without code change.
- **The clamp must move with it (or cap it):** at high magnitude the divergence clamp
  becomes the binding believability bound — either raise the clamp proportionally
  (let wild be wild) or keep it fixed (high magnitude saturates). Recommend: clamp
  scales with magnitude up to a hard ceiling, so "wild" is visibly wilder but still
  can't play a pure-random strategy.
- **B1 across the range:** the "skill > emotion" guard must hold at the *default* and
  degrade gracefully toward higher settings — at max drama we explicitly accept more
  variance (and more human exploit edge). The validation (§8) runs at ≥2 magnitude
  points so the guarantee is a *curve*, not a single point.

## 5. Reachability is the headline requirement

Every state must be **reachable for the archetypes that should feel it**, at the
right frequency. The current failure is that two of four corners are unreachable.
After the §3 decoupling + §6 driver rebalance, the target is:

- A **maniac** reaches **Heated** often and **Shaken** occasionally (when out-read on
  top of running bad) — not just overconfident.
- An **anxious nit** reaches **Guarded/Shaken** when out-leveled — the fear pole.
- An **ice pro** mostly stays **Commanding**, rarely leaves.
- No archetype is *locked out* of a corner it should be able to feel.

## 6. Event → axis driver model (the rebalance — where the real work is)

The current event table (`player_psychology.py:165-191`) is **hardcoded and
unbalanced**: composure-down events dominate, conviction-down events are weak, and
luck and skill events are conflated. Proposed re-derivation (magnitudes to be
tuned against §7 targets; this is the *structure*):

| Event class | → Composure | → Conviction | Notes |
|---|---|---|---|
| Bad beat / cooler / suckout-against | **down (strong)** | **0** | Luck: you played right. Composure hit only. (Extends today's "equity shocks skip confidence" rule — that instinct was correct.) |
| Big loss (outplayed pot) | down | **down** | Stacked because out-thought = both. |
| Losing streak / card-dead / fatigue | down | small down | Grind wears regulation; mild self-doubt. |
| **Bluff called / shown a bluff** | small down | **down (strong)** | The core conviction-killer. Currently too weak. |
| **Hero-call wrong / folded best hand** | small down | **down (strong)** | Epistemic miss. |
| Needled / trash-talked | down (poise-gated) | 0 | Social → composure only. |
| Big win | up | small up | |
| Successful bluff / correct hero-call | small up | **up (strong)** | Reads confirmed. |
| Disciplined fold / short-stack survival | up | 0 | Regulation reward. |

Plus three structural changes:
1. **Baseline K re-derivation:** drop `aggression`/`ego`/`winning` from
   `compute_baseline_confidence`; base it on `self_belief` + experience, with
   archetype-set spread (a nit starts lower, a pro higher) — so winning aggression
   no longer pins K high.
2. **Asymmetric recovery per axis:** conviction should *decay from above-baseline*
   faster (so a heater doesn't sit at K=0.96) and *recover from below* at the
   persona's rate — tune `RECOVERY_ABOVE_BASELINE` separately for K.
3. **Sensitivity is the character knob (B4):** per-persona volatility scales every
   delta (high for a maniac, low for a pro) — this is where archetype differentiation
   lives, not in the event magnitudes themselves.

### 6.1 v1 numbers (code-ready, to be sim-validated)

Concrete starting values so implementation is mechanical and the first sim has
something to measure. These are a **hypothesis to tune**, not final — but they encode
the principles above. Flag-gated; flag-off keeps the current values byte-identical.

**Baseline conviction** (`compute_baseline_confidence`, flag-on branch) — drop
`aggression`/`risk_identity`/`winning`, keep a self-regard core, widen the floor so
fragile archetypes can start low:
```
baseline_K = 0.35 + self_belief·0.35 + ego·0.15      # was: +agg·.25 +risk·.20 +ego·.25 +(sb−.5)·.4
clamp [0.40, 0.72]
# Fyodor 0.5/0.8 → 0.645 (was ~0.80);  anxious nit 0.35/0.30 → 0.52;  pro 0.7/0.5 → 0.67
```

**Event deltas** (the `confidence`→K column; composure mostly unchanged). The point is
to **cut the UP pumps** (so K stops pinning at 0.96) and **concentrate the DOWNs on
epistemic events**:

| Event | K now | K v1 | why |
|---|---|---|---|
| big_win | +0.12 | **+0.02** | winning chips ≠ validating reads — kill the pump |
| winning_streak | +0.10 | **+0.03** | same |
| successful_bluff | +0.20 | **+0.12** | read confirmed — real but smaller (it's half the pump) |
| big_loss | −0.15 | **−0.05** | losing chips ≠ losing reads (composure −0.15 carries it) |
| bad_beat / cooler | 0 (skip) | **0** | keep — luck never dents conviction |
| bluff_called | −0.25 | **−0.22** | core conviction-killer — keep strong |
| shown_a_bluff *(new)* | — | **−0.18** | you got read & fooled |
| hero_call_wrong *(new)* | — | **−0.15** | epistemic miss |
| nemesis_loss | −0.18 | **−0.18** | out-thought by a rival — keep |
| losing_streak | −0.12 | **−0.10** | sustained out-play → doubt |

**Asymmetric recovery:** add `RECOVERY_ABOVE_BASELINE_CONF` (K-specific, default same
as today so off-flag is identical); flag-on set it aggressive (~0.50) so euphoric
conviction decays toward baseline fast, while below-baseline recovery stays at the
persona's `recovery_rate`. Net: a heater's K falls back to baseline between pots
instead of ratcheting to 0.96, and an out-played stretch can drag K under 0.35.

> Expected effect (the §8 check): with the pumps cut + baseline lowered + epistemic
> downs concentrated, a maniac on a genuinely out-played downswing should cross into
> Conf < 0.35 → the `shaken` corner opens for the first time. The first sim measures
> exactly this with `tilt_reachability.py`.

## 7. Per-archetype profiles & frequency targets

Replace the global PRD with **per-archetype** targets (B4). Each archetype template =
(baseline C, baseline K, volatility, recovery, expressiveness) → home state +
reachable states + a target state-time distribution. Illustrative (to be finalised):

| Archetype | base C | base K | volatility | Home | Should reach | Target penalty-time |
|---|---|---|---|---|---|---|
| Volatile maniac (Fyodor) | 0.40 | 0.50 | high | Heated | Shaken (occasional) | 25–40% |
| Cocky fish | 0.55 | 0.70 | mid | Commanding/Heated | rarely Shaken | 15–25% |
| Anxious nit | 0.60 | 0.40 | high-K | Guarded | Shaken | 15–30% |
| Ice pro | 0.80 | 0.70 | low | Commanding | almost never | 2–8% |
| Steady recreational | 0.60 | 0.55 | mid | Poker-face | all, briefly | 10–20% |

Global believability still holds (B1/B5): aggregate full-tilt ≤ 5% (Moderate),
baseline-band ≥ 70%, episodic recovery.

## 8. Validation plan (against the tooling already built)

Every change is measured, not asserted, using the EXP_009 harness:

- **`experiments/tilt_reachability.py`** — per-archetype state mix vs §7 targets, PRD
  bands, `shaken` reachability, the confidence×composure joint.
- **`experiments/tilt_corpus_ev.py`** (`--mode emotional` + signature) — the EV swing
  (B1 bound) and the per-state Δagg **sign** (B2 directionality).
- **New check:** per-state exploit-direction consistency (does each state produce a
  stable, punishable bias) + a "reading human" edge estimate (bounded, not dominant).
- **Across the magnitude knob (§4.1):** the B1/B2 checks run at ≥2 `EMOTIONAL_MAGNITUDE`
  points (e.g. 0.5 / 1.0 / 1.5) so "skill > emotion" and directionality are validated
  as a **curve**, and the default (1.0) sits comfortably inside the safe band — a
  prerequisite before the knob is ever exposed to players.

Each implementation step ships behind a flag, off by default, byte-identical when off
— same discipline as the tilt-excursion work. `EMOTIONAL_MAGNITUDE` default 1.0
reproduces the reviewed "medium" calibration.

## 9. Open design decisions (for review before implementation)

1. **K rename? → RESOLVED: keep the `confidence` field, redefine its meaning/drivers.**
   Evidence: `confidence` has **289 references across 39 files, incl. 67 DB-column /
   JSON-key uses** (`zone_confidence`, `baseline_confidence`). A rename would touch the
   schema + persisted state — a large, risky diff for a pure semantic clarification.
   We change what *drives* `confidence` (§6) and its baseline (§3), not its name.
2. **Two axes or three? → RESOLVED: two.** `overconfident` is already *not* a separate
   axis — `zone_detection.py:307` computes it as a high-`confidence` edge zone
   (`(confidence − 0.90)/0.10`). In the four-quadrant model it is simply the **high-K
   extreme** of whichever composure band you are in (controlled → Commanding,
   uncontrolled → Heated), flavoured by high energy. No third axis, no fifth state.
3. **How hard to protect B1** — the exact session-EV band that counts as "skill still
   wins." Needs a number (ties to a future bb/100 target + playtest).
4. **Archetype taxonomy** — how many templates, and do personas map to one template
   or carry continuous (C,K,volatility) anchors? Recommend: continuous anchors,
   templates as presets.
5. **Migration** — re-derive all 104 personas' baselines, or grandfather + only
   re-derive the axis formula? Recommend: re-derive the formula, spot-fix outliers.

## 10. Next steps

1. Review + lock §3 (axes) and §4 (signatures) — the conceptual core.
2. Finalise §6 driver magnitudes + §7 archetype targets as the *tuning spec*.
3. Implement behind a flag (axis re-definition first — it unblocks everything).
4. Sim → `tilt_reachability.py` → iterate to targets (per-archetype EXP docs).
5. Playtest the "feel" (the part no metric captures).

## Cross-references

- `docs/experiments/EXP_009_EMOTIONAL_TUNING_BELIEVABLE_NOTICEABLE.md` — the
  diagnostic baseline that motivates this.
- `docs/technical/PSYCHOLOGY_DESIGN.md` — goals/PRD this refines (per-archetype).
- `docs/technical/PSYCHOLOGY_OVERVIEW.md` — current architecture (zones, families).
- `docs/plans/TILT_EV_HARNESS.md` — the EV measurement instruments reused here.

---
purpose: The target design for tilt as a felt, exploitable, temperament-scaled episode — keep the existing frequency spread, add slow-recovery-while-tilted persistence so episodes last long enough to read, with a monk exception and per-archetype episode-length targets
type: design
created: 2026-06-09
last_updated: 2026-06-09
---

# Tilt Excursion Design (target model)

Companion to the diagnosis in `docs/technical/EMOTIONAL_SYSTEM_ANALYSIS.md` (esp.
§7, the measured findings). That doc asks "where can personas go and why"; this
doc commits to **where we want them to go** and the one mechanism that gets us
there. Validated/tuned with `experiments/measure_zone_distribution.py`.

## Principle (the spine)

**Tilt is an episode, not a trait-state.** Everyone rests in their composed band;
a bad enough run pushes them into tilt; they recover. What varies by character is
how *easily* a run tips them in (onset, set by poise), how *long* the episode
lasts (persistence — the lever this design adds), and how it *changes their play*
(signature, by risk_identity). Three invariants make it coherent and match the
design intent:

1. **No one lives in tilt.** Every episode recovers; even a hothead returns to
   baseline during neutral/good stretches. Tilt is "steaming after that cooler,"
   never "permanently broken."
2. **Almost no one is immune.** A vicious enough run reaches anyone — it is not
   realistic for a character to *never* tilt.
3. **Except the designed exception.** One or two "monks" (poise ≥ 0.90) sit so
   high they effectively never tilt — the deliberate, legible exception that
   makes "you can't rattle them" a real read.

## Where it sits today (measured)

From `EMOTIONAL_SYSTEM_ANALYSIS.md §7` (real psychology, 104 personas, eval bots
excluded, play_rate 0.30):

| Band (poise) | n | %time tilt | median episode length |
|---|---|---|---|
| monk ≥0.90 | 1 | 0.00% | — |
| stoic 0.78–0.90 | 23 | 0.03% | 1 hd |
| composed 0.60–0.78 | 28 | 0.95% | 2 hd |
| volatile 0.45–0.60 | 36 | 2.85% | 2 hd |
| hothead <0.45 | 16 | 7.80% | 3 hd |

**Diagnosis:** the *frequency spread is already right* — a clean poise gradient
with a monk exception and nobody chronic. **The gap is persistence**: episodes are
1–3 hands — a flicker. Recovery lifts composure back over the 0.40 tilt line in a
few hands (even though full return to baseline takes ~16), so tilt never *lasts*
long enough for an opponent to notice and exploit. The lever is **persistence, not
frequency.**

## Target model

### 1. Frequency spread — KEEP (small nudge only)

The shape is right; do not touch the baseline clamp (it correctly floors the
*resting* point above tilt). Optionally nudge the volatile/hothead end up slightly.

| Band | current %tilt | target %tilt |
|---|---|---|
| monk | 0.00% | ~0% (never) |
| stoic | 0.03% | 0–1% |
| composed | 0.95% | 1–3% |
| volatile | 2.85% | 3–6% |
| hothead | 7.80% | 6–12% |

### 2. Persistence — ADD slow-recovery-while-tilted (the core change)

**Mechanism:** while composure is **below the tilt line** (`composure <
PENALTY_TILTED_THRESHOLD`, currently 0.35; or the emo-tilt line 0.40 — see "which
line" below), the per-hand recovery delta is multiplied by a **poise-scaled drag
`d(poise) ∈ (0, 1)`**, so climbing back out is slower for volatile characters and
quick for stoics. Above the line, recovery is unchanged (normal pull to baseline).

This is the *gentle* form of stickiness (chosen over a "latch-until-a-win"): it
only **slows** the climb-out, it never blocks it — so the never-chronic invariant
is preserved automatically (a win still lifts composure directly, and neutral
play still recovers, just slower).

It revives, in scoped form, the "zone gravity / stickiness" that was scaffolded
and removed 2026-05-15 (`docs/triage/ZONE_GRAVITY_DECISION.md`) — but applied
*only* as a tilt-zone recovery drag, not full bidirectional gravity.

**Hook point:** `PlayerPsychology.recover()` (`poker/player_psychology.py:909`),
in the below-baseline branch. Today: `new_comp = pre + (baseline − pre) · rate ·
(0.60 + 0.40·pre)`. Add a factor when `pre < tilt_line`:

```
drag        = TILT_DRAG_FLOOR + (1 − TILT_DRAG_FLOOR) · poise      # poise-scaled
eff_rate    = rate · (drag if pre < tilt_line else 1.0)
new_comp    = pre + (baseline − pre) · eff_rate · (0.60 + 0.40·pre)
```

`TILT_DRAG_FLOOR` is the drag for poise→0 (the most volatile). **Starting point
`TILT_DRAG_FLOOR ≈ 0.15`** (so a hothead recovers at ~30% speed while tilted, a
stoic at ~75%); the exact curve is **fit by the harness against the episode-length
targets below**, not hand-picked.

**Target episode lengths** (median hands below the line per episode):

| Band | current | target |
|---|---|---|
| monk | — | — (≈never enters) |
| stoic | 1–2 hd | 2–4 hd |
| composed | 2 hd | 4–7 hd |
| volatile | 2 hd | 6–10 hd |
| hothead | 3 hd | 12–20 hd |

Long enough that an opponent gets several decisions to read "they're tilting" and
attack it; short enough that the character visibly *recovers* and isn't broken.

**Which line gates the drag:** prefer the **emo-tilt line (0.40)** — the same
threshold the `tilt_conditioning` feature and the `emotional_state` `'tilted'`
descriptor use — so "in a tilt episode" means the same thing to the dynamics, the
strategy spike, and the avatar/telegraph. (The penalty-zone 0.35 line stays as-is
for the zone-penalty effects.)

**Tail bound — second-wind escape (REQUIRED; the drag alone is not enough).**
The fit proved slow-recovery couples the median and the tail: tuned to make
episodes *felt*, a slow-recovering hothead gets re-tilted by fresh bad events
before climbing out, so episodes *chain* (hothead 95th-pctile reached 70 hands,
~35% time). "Slows but never blocks" is **not** "bounded." So pair the drag with:

> **second-wind escape:** after `K` consecutive hands stuck below the tilt line,
> recovery jumps to a brisk rate (`accel`) and the episode resolves — capping the
> *tail* without moving the *median* (most episodes resolve before `K`). Hook: a
> per-episode "hands-tilted" counter in the recovery path; reset on climb-out.

**FIT CONVERGED (2026-06-09, `experiments/measure_zone_distribution.py`):**
`TILT_DRAG_FLOOR=0.30`, `exp=2.0`, `second_wind_K=15`, `accel≈0.45`, with a
**middle loss mix** (~29% of losses are composure-crushers):

| Band | %time tilt | median episode | 95p (tail) |
|---|---|---|---|
| monk | 0.0% | — | — |
| stoic | 0.0% | — | — |
| composed | 1.1% | 3 hd | 13 |
| volatile | **5.9%** | 4 hd | 16 |
| hothead | **17.7%** | 10 hd | 18 |

This is the locked balance: a clean monotonic temperament spread, **hothead tilt
felt (10-hand episodes, readable/exploitable) at <18% time and never-chronic**
(tail capped at 18 by the second wind), volatile felt at ~6%, composed occasional,
stoic/monk ≈ 0.

**The three knobs are separable (confirmed by the fit):**
- **drag (`floor`, `exp`)** sets episode *length* and the length *spread* (lower
  `exp` compresses; higher `floor` shortens),
- **`K` (second wind)** caps the *tail*,
- **onset (event rate/severity)** sets *how often* — the dominant %time lever.

**The frontier (a real constraint, not a tuning miss):** %time is driven by
*global* onset, so the mid/low bands can't be lifted without dragging the hothead
end back up. Tuning explored:
- `floor=0.30` (locked): hothead 17.7% / volatile 5.9% — safe margin, mid felt,
  low bands light.
- `floor=0.20`: volatile 7.2% (longer episodes) but hothead ~21% (over the line).

**Stoic/monk ≈ 0% is intended, not a defect.** Their baseline composure (~0.7) sits
far from the 0.40 line, so only a sustained cooler run tilts them — rare by design
(they're the near-immune band). They *can* tilt (the reachability check: all 104
personas can be held in tilt by a run); they rarely *do* in normal play, which is
realistic. The `exp` knob compresses episode-length spread but cannot make a stoic
*enter* tilt more often — that would require lowering their baseline (breaks the
archetype) or global onset (raises everyone).

**Caveat:** absolute %time is event-model-dependent (here: play_rate 0.30, middle
mix). The transferable signals are the *spread shape*, episode length, and the
bounded tail; **re-validate %time against real play once ported.**

### 3. Monk exceptions — designate 1–2 explicitly

Today exactly one persona sits at 0% by emergence. Pick 1–2 (e.g. a Buddha / Zen
archetype) and confirm their anchors (poise ≥ 0.90) keep them effectively
immune — the deliberate "unrattlable" read.

### 4. Signature + telegraph — the NEXT layer (design intent recorded here)

Once episodes last long enough to matter, make them *legible and exploitable*:
- **Behavioral signature by `risk_identity`** — ✅ BUILT (2026-06-09), flag
  `TILT_SIGNATURE_ENABLED` (EXPERIMENTAL, off). Makes the tiered bot's emotional
  distortion under a TILT state (`tilted`/`shaken`/`dissociated`) **character-driven
  by `risk_identity`** instead of state-driven: risk-seekers (≥0.5) **spew**
  (aggressive), risk-averse (<0.5) **collapse** (passive). Surgical change to the
  direction selection in `personality_modifier.compute_trait_offsets` (magnitude
  `intensity·(1-poise)` unchanged, only the direction flips) — brings the tiered
  bot to parity with the standard bot's `compute_modifiers` shaken-gate split, and
  adds no new term, so no new double-count with `tilt_conditioning`. Overconfident
  (a confidence state, not tilt) stays on the legacy state map. User call: the
  *direction* is character-driven by `risk_identity` (vs the random choice used for
  the erratic-reads coupling). Off => state-driven legacy direction.
  `tests/test_strategy/test_tilt_signature.py`.

  > **Validation status — behaviorally CONFIRMED by a paired probe.** Unit tests
  > prove the offset-direction flip. A quick on-vs-off *aggregate* game sim was
  > inconclusive (the flag changes decisions, so the arms diverge into different
  > trajectories — the *composed*-state rates differed, proving the spots weren't
  > comparable — and tilt→short-stack→forced all-ins swamp the offset). The clean
  > check is `experiments/tilt_signature_probe.py`: a **within-spot paired probe**
  > that runs both arms through the real `modify_strategy` on the *same* spot
  > (`reference_cash_sim_ab_paired`), so only the flag differs. Result (104
  > personas, intensity 0.5, aggression-mass Δ = on−off):
  >
  > | tier | Δagg tilted | Δagg shaken |
  > |---|---|---|
  > | risk-averse (<0.40) | **−0.058** (collapse) | +0.000 |
  > | mid (0.40–0.60) | −0.040 | +0.047 |
  > | risk-seeking (≥0.60) | +0.000 | **+0.171** (spew) |
  >
  > Exactly as designed: risk-averse collapse when tilted, risk-seekers spew when
  > shaken, same-direction cells at 0.000.
  >
  > **EV safety — bounded by construction, measured comparable.** The same probe
  > reports each arm's KL from the EV-optimal solver baseline (its exploitability
  > budget): risk-averse `KL_off 0.041 → KL_on 0.061` (+0.020), mid +0.001,
  > risk-seeking +0.000. The signature **redirects** the emotional offset within
  > the existing budget — it does not amplify it. The collapse direction adds at
  > most ~+0.02 KL (the *intended* "a collapsing player is more readable" effect),
  > small next to the 0.04–0.15 distortion the bot already applies every hand. And
  > structurally it *cannot* exceed that: `modify_strategy` step 6
  > (`clamp_divergence`) bounds the final distribution's divergence from baseline
  > by the profile cap, and the signature only changes the offset *direction*
  > inside that clamp. So it can't be catastrophic by construction. A precise
  > bb/100 number needs a psychology-in-the-loop paired harness (the bb/100
  > harnesses don't run psychology, so the bot never tilts in them — a real build);
  > the "right amount" of exploitability is then a playtest/taste call, not a
  > catastrophe check.
- **Coupling: cliff → erratic taper** — ✅ BUILT (2026-06-09), flag
  `TILT_ERRATIC_READS_ENABLED` (EXPERIMENTAL, off). The old `_zone_to_tilt_factor`
  was a deterministic cliff (composed 1.0 / tilted 0.5 / shaken 0.0 — a shaken bot
  forgot *all* its reads instantly). Flag-on replaces it with an **erratic** taper:
  `factor = 1 − intensity·U(0,1)`, one draw per decision (memoized on the threaded
  `emotional_state` so every layer in a decision agrees), so a tilted bot's reads
  go *unreliable* — sometimes trusts the read, sometimes loses the plot — with no
  hard 0.0. Chosen **random, not character-keyed** (user call: "random for now");
  this keeps the *dampening* direction (reads degrade on average, erratically), with
  the *over-apply* alternative ("I KNOW he's bluffing") left as a future option.
  Changes decisions → flag-gated; off = byte-identical legacy cliff.
  `tests/test_strategy/test_tilt_erratic_reads.py`.
  **EV safety:** `factor ∈ [0, 1]` is a pure *attenuator* on the exploitation layer
  — it can only *reduce* a read's strength, never invert it, so it can't make the
  bot play anti-exploitatively; worst case it skips a read (forgoing that read's
  edge). Exploitation is itself a small, ~2%-of-decisions layer, and the old cliff
  already attenuated it to 0.5/0.0 — the erratic taper's *expected* attenuation is
  in the same range, just smoother and never a hard 0.0. So the EV impact is
  structurally bounded and small; a precise bb/100 still needs the
  psychology-in-the-loop harness noted above.
- **Telegraph** (the perceptibility win) — ✅ BUILT (2026-06-09), flag
  `TILT_TELEGRAPH_ENABLED` (EXPERIMENTAL, off). On *entering* a tilt episode
  (`_was_tilted` transition), with probability `TILT_TELEGRAPH_PROB=0.7`, the sharp
  bot's Layer-3 path forces a spoken beat (overrides the chattiness gate, incl. an
  otherwise-silent turn) and hands the LLM the tilt **cause** (from
  `composure_state.pressure_source`) + a loose **suggestion** to react in its own
  words — explicitly NOT a fixed line, so the read isn't memorizable. New
  `ExpressionContext.tilt_telegraph` field + `_compute_tilt_telegraph` +
  `tests/test_strategy/test_tilt_telegraph.py`. Frequency-neutral. The avatar
  already leaks tilt (tilt zone ≠ poker-face) and post-hand commentary fires, so
  this is the third, in-the-moment verbal channel. Off => no block, no forced
  speech. Follow-up: suppress the fixed `narration_facts` `tilt_<type>` line when
  the telegraph fires (avoid the canned line co-occurring with the free-form one).
- **Remove the dead `SizeContext.emotional_state` wire** — ✅ DONE (2026-06-09).
  It was forward-scaffolding for a never-built `tilt_escalation` sizing behavior
  (`resolve_size_multiplier` never read it); removed the field + its construction
  site, left a docstring note to re-add if `tilt_escalation` is ever built.

## Validation (harness-driven)

`experiments/measure_zone_distribution.py` already reports per-band %time-tilt and
median episode length, and supports a recovery-policy knob. The build/tune loop:

1. Add the `slow-recovery-while-tilted` drag as a recovery policy (poise-scaled,
   `TILT_DRAG_FLOOR` param).
2. Fit `TILT_DRAG_FLOOR` (and curve) so per-band median episode length hits the §2
   targets, while §1 %time stays in band.
3. **Never-chronic check:** under neutral play (no events), the 95th-percentile
   episode length for the lowest-poise persona must stay bounded (target ≤ ~25
   hands) — verify the drag slows but never latches.
4. Absolute %time is event-model-dependent (needs real-play data to trust as a
   point); episode length + per-band spread are the robust signals to tune on.

## Implementation status

**Persistence is ported to production** (`poker/player_psychology.py:recover()`),
gated by `TILT_PERSISTENCE_ENABLED` (EXPERIMENTAL, off in dev+prod):
- Constants `TILT_LINE=0.40`, `TILT_DRAG_FLOOR=0.30`, `TILT_DRAG_EXP=2.0`,
  `TILT_SECOND_WIND_K=15`, `TILT_SECOND_WIND_ACCEL=0.45`.
- While below the line: composure recovery scaled by the poise drag; after `K`
  consecutive tilted hands the second wind jumps to the brisk rate. A
  `_tilt_streak` counter (not serialized) drives the escape.
- **Off => byte-identical** (`comp_rate == rate`, no streak state) — 415 existing
  psychology tests pass unchanged; new `tests/test_tilt_persistence.py` pins the
  mechanism + the inert-when-off guarantee.

**Real-play validation (2026-06-09)** via `experiments/configs/tilt_persistence_check.json`
(tiered no-LLM bots, psychology on, 1,200 hands, real pressure detector), flag ON vs OFF:

| persona | poise | ON %tilt | OFF %tilt |
|---|---|---|---|
| Edgar Allan Poe | 0.40 | 12.8% | 2.4% |
| Fyodor Dostoevsky | 0.25 | 7.0% | 3.7% |
| Abraham Lincoln (stoic) | 0.78 | 0.0% | 0.0% |
| Buddha (monk) | 0.92 | 0.0% | 0.0% |

Confirms: the mechanism fires in the real loop (hothead ON ≫ OFF), it is targeted
(stoic/monk untouched — they never enter tilt so the drag is inert), it is not
chronic (they recover; avg composure stays above the line), and it lands
**comfortably under 20%**. Real-poker onset is *gentler* than the synthetic harness
(which predicted ~16–18% for hotheads), so the live %time comes in lower — the
event-model-dependence flagged above, confirmed. The Poe-over-Fyodor ordering is
play variance over a modest sample and smooths with more hands.

Still to do: the §4 signature/coupling/telegraph layer (and a larger real-play
sample if a precise per-archetype %time is wanted).

## Open parameters / decisions

- `TILT_DRAG_FLOOR` and the drag curve shape (linear in poise vs steeper) — fit,
  then sanity-check the episode-length spread feels right in playtest.
- Whether to nudge the §1 frequency targets up at the volatile/hothead end or keep
  as-shipped.
- Which 1–2 personas are the designated monks.
- Signature/coupling/telegraph specifics (the §4 layer) — design in a follow-up
  once persistence lands.

## Cross-references

- `docs/technical/EMOTIONAL_SYSTEM_ANALYSIS.md` — the diagnosis + measured spread (§7).
- `experiments/measure_zone_distribution.py` — the validation harness.
- `docs/plans/PERCEPTIBILITY_CONDITIONING.md` — the tilt_conditioning feature + telegraph.
- `docs/triage/ZONE_GRAVITY_DECISION.md` — the removed stickiness this scopes back in.
- `docs/technical/PSYCHOLOGY_ZONES_MODEL.md` / `PSYCHOLOGY_OVERVIEW.md` — the system spec.

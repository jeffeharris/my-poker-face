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
`TILT_DRAG_FLOOR=0.20`, `exp=2.0`, `second_wind_K=20`, `accel≈0.45`:

| Band | median episode | target | %time tilt | 95p (tail) |
|---|---|---|---|---|
| stoic | 2 hd | 2–4 | ~0% | 9 ✓ |
| composed | 3 hd | 4–7 | ~2% | 14 ✗ (just short) |
| volatile | 6 hd | 6–10 | ~9% | 22 ✓ |
| hothead | 16 hd | 12–20 | ~26% | 23 ✓ |

Second wind pulled the hothead tail 70→23 (never-chronic passes); 3/4 median
targets hit. Two residuals:
- **composed sits at 3 hd** (target 4–7). Minor — arguably fine (composed *should*
  shake it fast); close by lowering `exp` toward ~1.7 if we want it longer.
- **hothead ~26% time tilted** exceeds the §1 frequency target (6–12%). This is an
  inherent tension, not a bug: lengthening episodes to be *felt* necessarily raises
  cumulative %time at a fixed onset rate. 26% is "tilts a lot but recovers" — far
  from "entire time," and arguably right for the most volatile band. If 26% is too
  high, the lever is **onset** (raise hotheads' baseline/threshold so they tilt
  less *often*), not persistence. (%time is also event-model-dependent — play_rate
  0.30; the robust signals are episode length + bounded tail + spread.)

Open decision: accept ~26% hothead %time, or also dial onset down.

### 3. Monk exceptions — designate 1–2 explicitly

Today exactly one persona sits at 0% by emergence. Pick 1–2 (e.g. a Buddha / Zen
archetype) and confirm their anchors (poise ≥ 0.90) keep them effectively
immune — the deliberate "unrattlable" read.

### 4. Signature + telegraph — the NEXT layer (design intent recorded here)

Once episodes last long enough to matter, make them *legible and exploitable*:
- **Behavioral signature by `risk_identity`** (already partly in
  `compute_modifiers`): aggressive characters **spew** (over-aggression — the
  maniac case), passive characters **collapse** (over-fold / call-station). The
  signature is what an opponent learns to punish.
- **Coupling: taper, not cliff.** The current `_zone_to_tilt_factor` *zeroes* the
  whole exploitation layer when shaken (`tiered_bot_controller.py:4067`). Replace
  the cliff with a taper, and reconsider direction (a tilted player arguably
  *over-applies* a read — "I KNOW he's bluffing" — rather than forgetting all
  reads).
- **Telegraph** (the perceptibility win): the avatar already leaks tilt (tilt zone
  ≠ poker-face), and post-hand commentary fires. Add a **probabilistic chat
  trigger on *entering* tilt** that feeds the LLM the tilt *state* + loose
  *suggestions* (not fixed lines — varied phrasing so it isn't memorizable). See
  `docs/plans/PERCEPTIBILITY_CONDITIONING.md`.
- **Remove the dead `SizeContext.emotional_state` wire** (`sizing_tendencies.py:158`).

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

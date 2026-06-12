---
purpose: Study protocol + tooling guide for the 2AFC perceptibility harness that measures whether the SHARP/TIERED bot's believability work (archetype distinctiveness, tilt, adaptation) is actually perceptible to players
type: guide
created: 2026-06-09
last_updated: 2026-06-09
---

# 2AFC Perceptibility Harness (backlog #12, Phase 5)

Scaffolding to MEASURE whether #12's believability work is *perceptible*, not just
present — the Stacked/Poki lesson that "adaptation that isn't *felt* is worthless"
(`docs/plans/PERCEPTIBILITY_CONDITIONING.md` §C). Implements the three detection /
discrimination tasks from the research §2.1
(`docs/vision/texas_hold_em_research_text_markdown.md`):

- **(a) archetype-ID** — given N hands of a hidden archetype, can a rater classify
  it? Scored as accuracy vs `1/n` chance with an exact binomial test + a confusion
  matrix.
- **(b) tilt-detection** — 2AFC "is this player on tilt?", scored with
  signal-detection **d-prime** (d′).
- **(c) adaptation ON-vs-OFF** — 2AFC "did the bot adjust to you?" vs a
  non-adapting control. Has an **automatable** KL-divergence check so the arm
  yields a number with no humans.

## Files

| File | What it does |
|---|---|
| `experiments/generate_2afc_sessions.py` | Generates LABELED sessions (seeded, reproducible, duplicate-hand pairs). CLI, never collected by pytest. |
| `experiments/score_2afc.py` | Scoring: binomial vs chance (a), d′ + Cohen's dz (b), automatable KL (c). CLI. |
| `tests/personality_tester/perceptibility/2afc_viewer.html` | Server-free rater UI: side-by-side anonymized sessions, forced choice + confidence, labels hidden until a choice is recorded, downloads a responses JSON. |
| `tests/personality_tester/perceptibility/README.md` | This file. |

The harness reuses the deterministic, LLM-free sim machinery in
`experiments/simulate_bb100.py` (`make_controller` / `make_game_state` /
`drive_hand`). **No production code is changed.** The tilt arm flips the
in-process `TILT_CONDITIONING_ENABLED` env var for the duration of
generation and injects a `ComposureState` + a `tilted` emotional zone onto the
sim controller's psychology namespace — the documented sim hooks, no strategy
edits.

## How to run

All sims run in the backend container (per `CLAUDE.md`).

### (a) archetype-ID

```bash
docker compose exec backend python -m experiments.generate_2afc_sessions \
    --arm archetype --sessions 14 --hands 40 --out /tmp/2afc_archetype.json
# rater plays via the viewer -> downloads /tmp/2afc_responses_archetype.json
docker compose exec backend python -m experiments.score_2afc \
    --sessions /tmp/2afc_archetype.json --arm archetype \
    --responses /tmp/2afc_responses_archetype.json
```

### (b) tilt 2AFC

```bash
docker compose exec backend python -m experiments.generate_2afc_sessions \
    --arm tilt --sessions 12 --hands 40 --out /tmp/2afc_tilt.json
# rater -> /tmp/2afc_responses_tilt.json  (each session_id -> "tilt" | "calm")
docker compose exec backend python -m experiments.score_2afc \
    --sessions /tmp/2afc_tilt.json --arm tilt \
    --responses /tmp/2afc_responses_tilt.json
```

### (c) adaptation 2AFC — AUTOMATABLE

```bash
docker compose exec backend python -m experiments.generate_2afc_sessions \
    --arm adaptation --sessions 8 --hands 250 --out /tmp/2afc_adaptation.json
# no humans needed for the KL number:
docker compose exec backend python -m experiments.score_2afc \
    --sessions /tmp/2afc_adaptation.json --arm adaptation
```

The viewer opens with `python -m http.server` (or just open the file); load the
sessions JSON, rate, **Download responses**, then feed that to `score_2afc.py`.

## What's AUTOMATABLE vs needs HUMAN RATERS

| Piece | Automatable? | Notes |
|---|---|---|
| Session generation (all arms) | **Yes** | seeded, reproducible, duplicate-hand pairs |
| Ground-truth labeling | **Yes** | labels stored separately from the player-facing view |
| Adaptation arm (c) KL number | **Yes** | KL(ON ‖ OFF) of hero action distributions on matched (street, facing) spots |
| d′ / binomial / Cohen's dz math | **Yes** | pure functions in `score_2afc.py` |
| Archetype-ID forced choice (a) | **No — human** | a *rater* must classify; automation would just re-measure the stat vectors the generator already knows |
| Tilt forced choice (b) | **No — human** | a *rater* must judge "on tilt?"; the d′ comes from human hit/false-alarm rates |
| Adaptation 2AFC *sufficiency* (c) | **No — human** | KL > 0 is **necessary** (the layer changed behavior) but not **sufficient** (that a player can *feel* it); the human 2AFC is still required |

The automatable adaptation-KL is the key win: arm (c) produces a perceptibility
*number* without a human in the loop, so it can run in CI / sims as a regression
gate on "did the adaptation layer's behavioral footprint shrink to nothing?".

## Study protocol (when you recruit human raters)

Per the research §2.2–§2.5 (n ≈ 12–20):

- **Within-subjects.** Every rater sees every condition (each is their own
  control) — the biggest lever for power at small n.
- **Counterbalanced order** (balanced Latin square) so fatigue/practice don't
  confound which build came first. The viewer deterministically flips left/right
  per pair as a first-pass counterbalance; for a real study, randomize block order
  per rater.
- **Duplicate / mirrored hands.** Paired sessions share the SAME seed → same hole
  cards & boards across conditions (à la duplicate bridge), cancelling card luck.
  Score *perception/decisions*, never money won.
- **Neutral phrasing.** No leading questions ("How realistic was the impressive
  adaptive AI?"). The viewer asks neutral yes/no + a 1–5 confidence slider.
- **Double-blind labels.** The viewer hides the ground-truth label AND the hero
  hole cards until a choice is recorded; sessions are presented as "A"/"B".
- **Skill covariate.** Capture each rater's poker skill (self-rating + short quiz
  or tracked win-rate) and use it as a covariate / blocking variable.
- **Pre-register** the hypotheses, primary metric, and analysis plan.

### The 5 ablation conditions (research §2.2, full toggle set)

1. **full** — everything on (the system as shipped).
2. **charts-only** — archetype preflop charts + injected behaviors; psychology /
   memory / adaptive layers OFF. The believability floor.
3. **adaptation-off** — exploitation/opponent-modeling layer OFF
   (`exploitation_strength = 0.0`); everything else on. (Arm (c)'s control.)
4. **tilt-off** — `tilt_conditioning` layer OFF (flag off); everything else on.
   (Arm (b)'s control.)
5. **full + surfacing** — full system PLUS the Phase-1 spoken reads / narration
   surfacing turned up (the "make adaptation audible" treatment). The hypothesis
   is that surfacing is what moves the adaptation 2AFC off chance.

## Decision thresholds (ship / iterate)

From the research §3 recommendations + the plan's playtest gates:

- **archetype-ID** accuracy significantly **> 1/7 chance** (≈14.3%; binomial
  p < 0.05). Below chance → archetypes blur (use the confusion matrix to see
  which pairs, e.g. Nit↔Rock, Station↔fish, and exaggerate those behaviors).
- **tilt d′ ≥ 1.0** → players reliably feel the tilt. d′ ≈ 0 → make the tells more
  overt (avatar / table-talk telegraphing).
- **adaptation 2AFC > 60%** (significantly > 50%) → the adaptive layer is felt.
  Pair with the automatable **KL threshold** (default `0.02` summed-bucket;
  tune with `--kl-threshold`): KL below threshold means the layer barely changes
  behavior on identical cards, so the human 2AFC has nothing to detect — fix the
  layer's footprint OR the surfacing before re-running the human study.

## Findings from the Phase-5 smoke runs (small-sample, not a study)

These are sanity checks from building the harness, NOT the study result:

- **Adaptation-KL is real but small.** Against an *exploitable rule-bot backdrop*
  (`CallStation` ×2 + `FoldyBot` ×2 + `Nit` — the `exploit_bb100.py` recipe),
  TAG ON vs OFF at 250 hands produced a non-zero KL (pooled ≈ 1.8e-4; the ON arm
  raises slightly more / checks slightly less, concentrated in FLOP-open and
  RIVER-open spots). Below the default 0.02 threshold → consistent with the plan's
  thesis that the current adaptation footprint is small and likely imperceptible
  without surfacing.
- **The personality-archetype `Calling Station` does NOT trip the exploitation
  layer** in-sim: it measures VPIP ≈ 0.36, below the hyper_passive threshold
  (0.70). ON == OFF against it (a genuine null, not a harness bug). Use the
  rule-bot stations (`ADAPTATION_BACKDROP`) for the adaptation arm — that's why
  the two arms use different backdrops.
- **The tilt layer fires** as designed: a maniac with injected `bad_beat` tilt is
  measurably more aggressive than the calm twin on the same cards (≈ +2 pp
  aggression at 40 hands). Whether a human can *see* +2pp is exactly the d′
  question the human study answers.

## OPEN QUESTION — `USES_EMOTIONAL_NARRATION` gating (do NOT resolve here)

The plan (`PERCEPTIBILITY_CONDITIONING.md` Phase 5) flags this and it directly
limits the tilt study:

> sharp bots set `USES_EMOTIONAL_NARRATION = False` outside heads-up, so tilt is
> near-invisible to raters except via the 3-bet spike.

In a 6-max session the tilt is conveyed almost entirely through the **behavioral**
spike (more 3-bets / aggression). The *emotional narration* path — the table-talk
/ tells that would telegraph "still stinging from that last one" — is off outside
heads-up. So a 6-max tilt 2AFC likely measures only the statistical spike, not the
telegraphed cue, and may land near d′ ≈ 0 even though the layer fires.

**Implication for the study (for the user to decide — not changed here):**

- either run the **tilt arm heads-up** (`USES_EMOTIONAL_NARRATION` is on in HU, so
  the narration telegraphs the tilt), OR
- add a **study-only narration override** that forces the emotional-narration path
  on for 6-max tilt sessions.

This harness does **not** touch `USES_EMOTIONAL_NARRATION` or any production flag's
default. The generator only flips `TILT_CONDITIONING_ENABLED` in-process for the
duration of generation. Resolve the narration-gating question before running the
human tilt study.

## Caveat: sim speech is the CUE, not the voiced line

The sim path does not call the LLM, so the player-facing *speech* in generated
sessions is the **deterministic intuition cue** the code picks (spoken-read /
narration-facts observation text) — i.e. what the LLM would be *asked* to voice,
not the voiced line. For the archetype-ID and tilt human studies you will likely
want LLM-voiced speech (generate via the full controller path, or post-process the
cue through the Default-tier LLM). The adaptation-KL arm is unaffected (it scores
actions, not speech).

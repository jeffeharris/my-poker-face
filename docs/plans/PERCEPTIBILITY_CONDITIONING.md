---
purpose: The full 5-phase plan to make the SHARP/TIERED bot's opponent adaptation perceptible (surface the read) and conditioned (vary aggression by state), with the locked design decisions and per-phase contracts
type: design
created: 2026-06-09
last_updated: 2026-06-09
---

# Perceptibility & Conditioning (backlog #12)

The unifier for Findings 1–3 and the "Stacked" lesson
(`docs/technical/ARCHETYPE_SHAPING_FINDINGS.md` §C): **adaptation that isn't
*felt* is worthless.** Stacked carried world-class per-opponent adaptation and was
*still* "readable in 40 hands" because its aggression was monotonic and its
adaptation imperceptible. Our system has the same shape — the exploitation layer
adapts on a sample-gated confidence ramp (Finding 3) but the read is never voiced
and the aggression is a flat per-archetype constant.

This plan has two halves: **surface the read** (make the existing adaptation
audible) and **condition the aggression** (make the aggression a *state*, not a
constant). The believability thesis: a high frequency is realistic; a *constant*
high frequency is a caricature.

## Locked design decisions (do not re-litigate)

1. **HYBRID surfacing.** Code detects the read, its confidence ARC tier, and picks
   the line's INTUITION framing; the LLM *voices* it. The code never emits a raw
   number or stat name into the player-facing text — only a feel. Applies to the
   SHARP/TIERED bot only (it owns the opponent model + intervention trace).
2. **BOTH channels.** The chosen read is injected into the *speech* path
   (`ExpressionContext.opponent_observations`) AND the always-evaluated
   *inner-context / narration_facts* path, so the "figuring you out" arc persists
   even on hands the bot stays silent.
3. **Phase-1-first, frequency-NEUTRAL.** Phase 1 (surface the read) runs entirely
   in the Layer-3 expression path AFTER the action is locked. It must NOT change
   any decision/frequency. The conditioning work (Phase 2) that DOES change
   frequency is staged behind it.

## The target player experience (the readability/depth arc)

- `<40 hands`: "that's the aggressive guy" — a readable archetype (good).
- `~200 hands`: "he 3-bets my steals but folds to my 4-bets when calm" — a
  learnable *conditional* exploit.
- under tilt: the read *inverts* (he won't fold) → the player has to re-read.

Playtest gates: ID'd in `<40` hands AND never surprises = too monotonic (add
conditioning); no articulable exploit after `~200` hands = too random (tighten the
rules); maniac *session-average* 3-bet `> ~25%` sustained = drifting to caricature
(cap baseline, let only transient states exceed); tilt spikes not attributable to a
visible cause = strengthen avatar/table-talk telegraphing.

---

## Phase 1 — Surface the read ✅ BUILT (2026-06-09)

Make the SHARP/TIERED bot voice an earned read about an opponent, grounded in real
observed stats, gated by a confidence ARC, rate-limited, and intuition-framed
(NEVER a stat readout). Frequency-neutral (post-decision Layer-3 only).

**Files:**
- `poker/memory/opponent_reads.py` — re-home of `deep_reads_from_tendencies` +
  `reconstruct_tendencies_from_lifetime` out of `flask_app/services/` so `poker/`
  imports them without a backwards `poker -> flask_app` dependency.
  `flask_app/services/opponent_reads.py` is now a re-export shim (single owner).
- `poker/strategy/spoken_reads.py` — the new module:
  - `SpokenReadConfig` (frozen): `max_observations_per_decision=2`,
    `cooldown_hands=8`, arc-tier floors.
  - `SpokenReadState`: per-(observer→opponent) anti-spam, `eligible_hand_index`
    advances on hands a read was ELIGIBLE to speak (not only voiced) so silent
    streets don't reset/spam. Held on the controller instance (per-session), not
    persisted.
  - `select_spoken_reads(...) -> (observations, new_state, reads)`.
  - `_select_best_read(...)`: priority by legibility + maturation —
    **fold_to_cbet > cbet_attempt_rate > barrel_frequency > all_in_frequency**.
    Sizing tells are EXCLUDED (Phase 4) but the priority table is structured so
    they plug in later.
  - `SpokenRead` record carries the arc tier for the narration_facts channel.
- `poker/tiered_bot_controller.py` — `_select_opponent_observations` calls
  `select_spoken_reads`, prefers spoken reads over the generic model observations,
  caps at 2, stashes the lead `SpokenRead` on `self._last_spoken_read`;
  `_build_narration_facts` folds it in as the LEAD `NarrationFact`
  (both-channels). Lazy-init of state/config + `getattr` guards (controllers
  built via `__new__` in sims/tests).

**Arc tiers** (from the read's own sample count — its confidence ramp, mirroring
exploitation's `CONFIDENCE_RAMP_HANDS`/`_cbet_sample_confidence`):
`tentative` (≥5) → `confident` (≥25) → `sure` (≥60). The tier passes to the LLM as
a cue (like narration_facts `certainty_bucket`) so the LLM voices the escalation.

**Anti-spam:** `cooldown_hands=8` eligible hands per opponent. The counter advances
on every eligible hand; silent (non-eligible) hands leave it untouched.

**Frequency-neutral proof:** `scripts/archetype_mixedfield_probe.py` @ 2500 hands
is byte-identical before/after (surfacing is post-decision).

**Tests:** `tests/test_strategy/test_spoken_reads.py` (arc-tier selection;
cooldown advances-on-eligible; no number/stat-name leak; priority ordering; None
below threshold; max-2 cap; graceful when manager/model absent; re-home importable
from both call sites).

This **subsumes the parked Finding-3 nudge-display fix** — surfacing the read is
the perceptibility win the nudge-display was reaching for.

---

## Phase 2 — Condition the aggression (the full Option-C `tilt_conditioning` layer)

Modulate 3-bet/aggression by **opponent memory > position > tilt/emotional state >
table image/recent history > stack depth/straddle** (priority per the aggression
brief) instead of a flat per-archetype constant. Unlike Phase 1 this DOES change
frequency and must go through the EV gate + sim validation.

**Layer contract:**
- A new strategy layer `tilt_conditioning` that, like the exploitation layer,
  emits a logit-space offset gated by confidence, applied at the same point in the
  pipeline (it is a *conditioner*, not an override).
- Its own `InterventionTrace` entries (`layer='tilt_conditioning'`,
  reason_codes per tilt-type) so it is replayable + surfaceable through
  narration_facts (the both-channels decision means a tilt spike is *telegraphed*).
- New `DeviationProfile` fields for the conditioner's reach (e.g.
  `tilt_aggression_scale`, per-tilt-type sensitivities), defaulting inert so
  non-conditioned archetypes are byte-identical (no-op invariant, test-locked —
  same pattern as the reraise-split + spot-tendency work).
- **Tendler's 7 tilt types** as the trigger taxonomy (running-bad, injustice,
  hate-losing, mistake, entitlement, revenge, desperation). Each has a distinct
  *trigger* = direct fuel into the existing emotion + relationship layers. The
  avatar MUST telegraph the spike (table talk + tells) so it's earned and readable.
- **Maniac baseline cap (backlog #9 fold-in):** lower the maniac 3-bet *baseline*
  to ~20–25 and let the conditioner push it transiently into the 30s, so 30+ reads
  as a *state* not a constant. Touches `ARCHETYPE_TARGETS['maniac']['threebet']` +
  the maniac reraise split in `deviation_profiles.py`.

**Correctness instance already shipped:** backlog #4 (`_apply_hyper_passive` opener
guard) is a small opponent-conditioning correctness fix — don't 3-bet a station.

## Phase 3 — Position & image conditioning

The next conditioning levers below tilt: position (steal-vs-defend asymmetry) and
table image / recent history (the bot adjusts to how IT has been playing and how
the table perceives it). Lower-leverage than tilt; sequenced after Phase 2 so the
conditioner machinery exists.

## Phase 4 — Sizing tells (the per-archetype sizing character)

Fold in `docs/plans/PREFLOP_SIZING_VARIETY.md` (backlog #7): a maniac who overbets
and a nit who min-3-bets *telegraph* their archetype through size. P1 emit multiple
raise-size tokens in the preflop charts, P2 engage the `SIZING_PERSONALITY` size
gradient, P3 add a "3-bet size" read to the review tool. **Sizing reads slot into
the Phase-1 `READ_PRIORITY` table** (`sizing_polarization_score`, `fold_to_big_bet`
— deliberately excluded from Phase 1) so the bot can *also voice* a sizing tell
("you only bet big when you've got it").

## Phase 5 — Measurement: a 2AFC perceptibility harness

A forced-choice harness (lives in `tests/personality_tester/`) that measures
whether the read is actually *perceptible*, not just present:
- **archetype-ID** — given N hands of a hidden archetype, can a rater classify it?
  (the "readable in <40 hands" / ">200 hands no exploit" gates, quantified).
- **tilt-detection d-prime** — signal-detection d′ for "is this bot on tilt right
  now?" given the telegraphing.
- **adaptation 2AFC** — two-alternative forced choice: "did the bot adjust to you?"
  vs a non-adapting control.

**Open question:** whether `USES_EMOTIONAL_NARRATION` (the emotional-narration flag)
should gate tilt-visibility — i.e. is the tilt spike telegraphed through the
emotional-narration path or a dedicated tell channel? Resolve before Phase 2 ships
the telegraphing.

---

## Cross-references

- `docs/technical/ARCHETYPE_SHAPING_FINDINGS.md` §C — the believability thesis +
  the Poki/Stacked/Nemesis/F.E.A.R./Alien/Drivatars lessons.
- `docs/plans/ARCHETYPE_SHAPING_HANDOFF.md` #9 (maniac cap), #12 (this work).
- `docs/plans/TELLS_SYSTEM.md`, `docs/plans/PREDATOR_LOADOUTS.md` — adjacent
  surfacing/loadout work.
- `docs/plans/PREFLOP_SIZING_VARIETY.md` — the Phase 4 substrate.

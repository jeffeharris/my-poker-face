---
purpose: The full 5-phase plan to make the SHARP/TIERED bot's opponent adaptation perceptible (surface the read) and conditioned (vary aggression by state), with the locked design decisions and per-phase contracts
type: design
created: 2026-06-09
last_updated: 2026-06-11
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

## Phase 2 — Condition the aggression (the full Option-C `tilt_conditioning` layer) ✅ BUILT (infra) (2026-06-10)

**Status: the layer + flag + all Tendler-type rule definitions are built**,
INERT by default for every archetype EXCEPT the maniac (opted in by Phase 3
below) — flag-off AND no-archetype-opted-in is **byte-identical** to pre-layer
behaviour (probe confirmed, md5 identical before/after at 9k hands).

### Phase 3 (#9) — maniac baseline + tilt opt-in ✅ BUILT (2026-06-10)

The maniac is the **first (and only) archetype opted into the layer**, and its
baseline 3-bet was lowered so 30+ reads as a tilt STATE not a constant (the
believability thesis, applied):

- **Baseline lowered** (`deviation_profiles.py['maniac']`):
  `reraise_max_per_action_shift` 0.08→**0.01**, `reraise_aggression_scale`
  0.8→**0.4**. 6k mixed-field: facing-open **3-bet 36.4→30.1, 4-bet 40.2→29.5**
  (both back in the re-set bands; 4-bet was at the ceiling). VPIP/PFR/AF/all_in
  unchanged (the split is isolated to facing-raise nodes) — maniac stays distinct
  from lag.
- **20–25 NOT reached — chart-floored at ~30, deferred.** The shared loose chart's
  own re-raise mass is ~29–30% combo-weighted (cap=0.0 floors 3-bet at 29.4), so
  the cap can't pull it below ~30; closing to 25 needs a maniac-only loose chart
  (folds into backlog #5, out of scope — the loose chart is SHARED with
  spewy_fish/maniac_overbluff).
- **Tilt opt-in**: `tilt_conditioning_cap=0.35` + the 6 aggressive Tendler rules
  (bad_beat/got_sucked_out/big_loss/losing_streak/nemesis_loss/crippled;
  bluff_called excluded — V1 no-op). GATED by `TILT_CONDITIONING_ENABLED` (off
  everywhere), so flag-OFF default = the ~30 baseline. Tilt-probe (flag on):
  composed = baseline (3-bet 30.6, tilt_fired=0); EXTREME forced bad_beat tilt =
  **3-bet 30.6→41.4 / 4-bet 29.0→41.9** (low-40s, cap-bounded, recovers).
- **Re-band** (`archetype_targets.py['maniac']`): threebet 36-52→**26-34**, fourbet
  24-40→**26-38**, fold_to_3bet 15-35→**15-40**. Bands describe the flag-OFF
  default; tilt-state spikes exceed them by design (the conditioned tail, noted in
  a code comment).
- Tests: the inert/byte-identical invariant now excludes the maniac + 5 positive
  maniac opt-in tests. Full suite 8276 passed; tsc clean. Probes (gitignored):
  `scripts/maniac_reraise_sweep.py`, `scripts/tilt_conditioning_probe.py`.

**Files:**
- `poker/strategy/tilt_conditioning.py` (new) — `TiltScenarioRule` (frozen),
  `TILT_TYPE_RULES` (the 7 Tendler types mapped from
  `composure_state.pressure_source`: bad_beat/got_sucked_out/big_loss/
  losing_streak/nemesis_loss/crippled → aggression UP in re-raise spots;
  bluff_called = conservative no-op for V1, registered + telegraphable),
  `_resolve_tilt_type` / `_resolve_scenario` / `_resolve_position`, and
  `apply_tilt_conditioning(...) -> (StrategyProfile, InterventionTrace)` (logit
  offset → clip → renormalize bounded by `profile.tilt_conditioning_cap`;
  identity + `fired=False` on cap==0.0 / composed / no-match).
- `poker/strategy/deviation_profiles.py` — `DeviationProfile` gains
  `tilt_conditioning_cap: float = 0.0` and
  `tilt_scenario_rules: Tuple[TiltScenarioRule, ...] = ()` (TYPE_CHECKING import
  to avoid the circular import). All profiles default inert.
- `poker/strategy/intervention_trace.py` — `'tilt_conditioning'` added to
  `_LAYER_NAMES`, `_RULE_IDS_BY_LAYER` (the `tilt_<type>` codes + `default`),
  and `_LAYER_ORDER` at **tier 1** (shares exploitation's coarse tier rather
  than a NEW ordinal — deliberately avoids the layer-order bump that would
  shift exploitation→2 and break the per-layer golden ordinals; runs in the
  pipeline before exploitation, monotonicity holds).
- `poker/tiered_bot_controller.py` — `_layer_tilt_conditioning` helper called in
  BOTH the preflop and postflop paths between spot-tendencies and
  `_apply_exploitation`. Double-gated: `is_enabled('TILT_CONDITIONING_ENABLED')`
  AND `profile.tilt_conditioning_cap > 0.0` (zero overhead + zero effect when
  off/inert). `composure_state` read via `getattr` (sim/`__new__` safety).
- `core/feature_flags.py` — `TILT_CONDITIONING_ENABLED` (EXPERIMENTAL,
  db_overridable, off by default everywhere).
- `poker/strategy/narration_facts.py` — the `tilt_<type>` reason codes added to
  `NARRATION_ALLOWLIST` + `REASON_CODE_TO_OBSERVATION` (+ fallbacks, narrative
  weight, action intent) with intuition-framed observations (bad_beat → "still
  stinging from that last one") so a fired spike is telegraphed (both channels).
- `tests/test_strategy/test_tilt_conditioning.py` (new, 48 tests) — per-type
  rule selection, cap clamp, composed/flag-off/inert no-ops, scenario+position
  gating, the double-count guard, and the byte-identical invariant across all
  real archetypes.

**Double-count guard:** `compute_trait_offsets` already applies the generic,
spot-blind, poise-gated emotional offset (`intensity*(1-poise)` for
tilted/overconfident). This layer is disjoint: it keys on
`composure_state.pressure_source` (the CAUSE, never read by the personality
term), its magnitude is the fixed profile cap (NOT re-multiplied by
`intensity*(1-poise)`), it only fires in re-raise/postflop-aggressor spots, and
it leaves the PASSIVE direction (shaken/dissociated) entirely to the poise-gate.

### (original Phase-2 contract, retained)


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

## Phase 4 — Surface the sizing tell ✅ BUILT (2026-06-11)

The "surface a sizing tell" slice of this phase is built: the two sizing reads
(`sizing_polarization_score`, `fold_to_big_bet`) — deliberately excluded from
Phase 1 — now plug into the Phase-1 `READ_PRIORITY` table so the SHARP/TIERED bot
*voices* a sizing tell ("when you bet big, you've got it"; "put a big enough bet
out there and you fold every time"). Expression-layer only, frequency-NEUTRAL.

**Priority placement:** below the three action-frequency reads, above the coarse
global all-in rate:
`fold_to_cbet > cbet_attempt_rate > barrel_frequency > sizing_polarization_score >
fold_to_big_bet > all_in_frequency`. Rationale: sizing tells mature slower
(showdown-gated equity bins / big-bets-faced) so they shouldn't pre-empt the
faster reads, but a matured sizing tell is far more evocative than the coarse
all-in rate, so it outranks it. `sizing_polarization` outranks `fold_to_big_bet`
("big bet, big hand" is the more vivid line).

**Files:**
- `poker/strategy/spoken_reads.py` — two new `_ReadSpec`s in `READ_PRIORITY`;
  `_ReadSpec` gains an optional `second_sample_attr` (sizing_polarization is
  dual-gated on BOTH equity bins, so its effective sample = `min(big, small)` —
  the weaker bin drives both the min_samples gate and the arc tier).
  `_read_sample_count(tendencies, spec)` now takes the spec and returns the min
  when a second attr is set. `min_samples` mirrors the deep_reads gates
  (`SIZING_MIN_BIN_SAMPLE=4` / `SIZING_MIN_BIG_BET_FACED=6`); arc floors (≥5)
  apply on top, so a read can be deep_reads-eligible yet still sub-tentative.
- `tests/test_strategy/test_spoken_reads.py` — `_tendencies` helper extended
  with sizing-bin params; +8 tests (fire-at-maturity, suppressed-below-sample,
  weaker-bin drives tier, deep_reads-None guard, priority ordering vs the action
  reads + all_in, end-to-end through `select_spoken_reads`); leak test extended
  to ban the sizing stat-name jargon (polarization/sizing_/fold_to_big/score).

**Wiring (confirmed, no new wiring needed):** the controller's
`_select_opponent_observations` → `select_spoken_reads` path already calls
`deep_reads_from_tendencies(tendencies)`, which already exposes both sizing
fields (sample-gated to None until mature). Adding the specs to the table was
sufficient — the surfacing path receives the sizing fields with no controller
change.

**Frequency-neutral proof:** `scripts/archetype_mixedfield_probe.py` @ 9k hands
is byte-identical (md5 match) before/after — the change is expression-layer only.

### Remaining Phase-4 substrate (not in this slice)

Fold in `docs/plans/PREFLOP_SIZING_VARIETY.md` (backlog #7): a maniac who overbets
and a nit who min-3-bets *telegraph* their archetype through size. P1 emit multiple
raise-size tokens in the preflop charts, P2 engage the `SIZING_PERSONALITY` size
gradient, P3 add a "3-bet size" read to the review tool. (The "voice the sizing
read" piece above is now done; the chart/gradient/review-tool pieces remain.)

## Phase 5 — Measurement: a 2AFC perceptibility harness ✅ BUILT (scaffolding) (2026-06-09)

A forced-choice harness that measures whether the read is actually *perceptible*,
not just present:
- **archetype-ID** — given N hands of a hidden archetype, can a rater classify it?
  Scored vs `1/n` chance (1/7) with an exact binomial test + confusion matrix
  (the "readable in <40 hands" / ">200 hands no exploit" gates, quantified).
- **tilt-detection d-prime** — signal-detection d′ for "is this bot on tilt right
  now?" given the telegraphing (+ Cohen's dz for the paired design).
- **adaptation 2AFC** — "did the bot adjust to you?" vs a non-adapting control,
  PLUS an **automatable** KL-divergence check so the arm yields a number with no
  humans.

**Files (all NEW — no production code changed):**
- `experiments/generate_2afc_sessions.py` — seeded, reproducible LABELED session
  generator (duplicate-hand pairs à la duplicate bridge). Reuses
  `simulate_bb100.make_controller/make_game_state/drive_hand`. Tilt arm flips the
  in-process `TILT_CONDITIONING_ENABLED` env var (OFF in prod) for generation +
  injects `ComposureState(pressure_source='bad_beat')` and a `tilted` emotional
  zone onto the sim psychology namespace (the documented sim hooks). Adaptation
  arm flips `exploitation_strength` 1.0↔0.0. CLI, never pytest-collected.
- `experiments/score_2afc.py` — binomial-vs-chance (a), d′ + Cohen's dz (b),
  automatable KL(ON ‖ OFF) on matched (street, facing) spots (c). Dependency-light
  (probit via Acklam; scipy used only if present).
- `tests/personality_tester/perceptibility/2afc_viewer.html` — server-free rater
  UI (side-by-side anonymized sessions, forced choice + 1–5 confidence, labels +
  hole cards hidden until a choice is recorded, downloads a responses JSON).
- `tests/personality_tester/perceptibility/README.md` — the full study protocol
  (within-subjects, counterbalanced, duplicate hands, neutral phrasing, the 5
  ablation conditions, decision thresholds), the automatable-vs-human split, and
  the open question below.

**Automatable vs needs-humans:** generation, labeling, the adaptation-KL number,
and all d′/binomial/dz math are AUTOMATABLE; the archetype-ID and tilt
forced-choice *judgments* need human raters. The adaptation-KL is necessary but
not sufficient for perceptibility (it confirms the layer changed behavior on
identical cards; a human 2AFC still confirms a player can *feel* it).

**Smoke-run finding (not a study):** against the exploitable rule-bot backdrop
(`exploit_bb100` recipe) TAG ON-vs-OFF at 250 hands produced a real but SMALL
KL (pooled ≈ 1.8e-4, below the 0.02 threshold) — consistent with the thesis that
the current adaptation footprint is likely imperceptible without surfacing. The
personality-archetype `Calling Station` (VPIP ≈ 0.36 in-sim) does NOT trip the
hyper_passive exploitation rule (a genuine null) — hence the adaptation arm uses
the rule-bot station backdrop. The tilt layer fires (maniac w/ injected bad_beat
is ~+2pp more aggressive than the calm twin on the same cards).

**Open question (flagged in the README, NOT resolved here):** whether
`USES_EMOTIONAL_NARRATION` (the emotional-narration flag) should gate
tilt-visibility. Sharp bots set it `False` outside heads-up, so 6-max tilt is
near-invisible to raters except via the 3-bet/aggression spike — the telegraphing
(table-talk / tells) is off. The tilt study likely needs **heads-up sessions** OR
a **study-only narration override** to be a fair test. The harness does NOT change
that flag (it only flips `TILT_CONDITIONING_ENABLED` in-process for generation);
left for the user to decide before the human tilt study (and before Phase 2 ships
the telegraphing).

---

## Cross-references

- `docs/technical/ARCHETYPE_SHAPING_FINDINGS.md` §C — the believability thesis +
  the Poki/Stacked/Nemesis/F.E.A.R./Alien/Drivatars lessons.
- `docs/plans/ARCHETYPE_SHAPING_HANDOFF.md` #9 (maniac cap), #12 (this work).
- `docs/plans/TELLS_SYSTEM.md`, `docs/plans/PREDATOR_LOADOUTS.md` — adjacent
  surfacing/loadout work.
- `docs/plans/PREFLOP_SIZING_VARIETY.md` — the Phase 4 substrate.

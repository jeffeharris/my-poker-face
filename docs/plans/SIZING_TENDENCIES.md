---
purpose: Design for per-PLAYER preflop bet-sizing personalities — sampled, archetype-weighted sizing habits with within-player variance, so size is a read you EARN over many hands rather than a one-shot archetype tell
type: design
created: 2026-06-09
last_updated: 2026-06-11
---

> **Status: P2 (first learnable tell) SHIPPED — 2026-06-09.** Adds the
> `size_by_strength` behavior (recreational "big = strong" tell) +
> archetype-weighted palette sampling + the hands-to-read measurement.
> `resolve_size_multiplier` now folds a strength-conditioned factor
> (`SIZE_BY_STRENGTH_GAP=0.10`, half in each direction so the per-player CENTER is
> preserved — only the size↔strength *correlation* is the tell) on top of
> `base_size_bias`, clamped to the existing band. `sample_sizing_personality` draws
> the palette behavior AFTER the bias draw (P1 bias values byte-unchanged), keyed by
> `SIZE_BY_STRENGTH_WEIGHTS`: calling_station 0.65 / weak_fish 0.70 carry it; tag and
> the disciplined tiers (nit/rock/lag/maniac) are ZERO — **regs stay clean**. The
> per-personality `sizing_tendencies` override lane still pins a behavior explicitly.
> **Validated:** mixed-field probe — VPIP/PFR/3-bet/4-bet/fold-to-3bet byte-identical
> vs the P1 baseline (only the same tiny downstream postflop-AF/all-in pot-size
> ripple P1 noted); hands-to-read (`scripts/sizing_hands_to_read.py`, AUC
> size→strength, live ±12% jitter) — reg control flat at AUC≈0.50 (no tell),
> recreational carriers reach a STABLE read after ~24–56 observed RFI opens (median
> 24) ≈ **~250 hands at the table** → "legible but not instant" PASS; EV probe
> (`scripts/sizing_ev_probe.py`) — modest leak, not a huge edge; regs (never sample
> it) unaffected. Tests: `tests/test_strategy/test_sizing_tendencies.py` extended
> (regs-never-carry, reg-invariant-to-strength, recreational-carries-with-prob,
> strong-up/weak-down, center-preserved, clamp, override-pins); strategy suite green.
> **P3 (polarized_size, position_blind, tilt_escalation, anchor_number) NOT yet
> started.**
> **P4 — surface the size tell: DONE (2026-06-11), surfaced via #12's
> `spoken_reads`.** The two sizing reads (`sizing_polarization_score`,
> `fold_to_big_bet`) now slot into the Perceptibility-Conditioning Phase-1
> `READ_PRIORITY` table (`poker/strategy/spoken_reads.py`), so the SHARP/TIERED
> bot *voices* an intuition-framed sizing tell ("when you bet big, you've got
> it" / "put a big enough bet out there and you fold every time"). Placed below
> the action-frequency reads, above the global all-in rate; dual-gated
> polarization matures off its weaker equity bin. Expression-layer only,
> frequency-neutral (mixed-field probe byte-identical). The review-tool "3-bet
> size" column piece (#7 P3) is separate and still pending. Detail:
> `docs/plans/PERCEPTIBILITY_CONDITIONING.md` Phase 4.

# Sizing tendencies — per-player sizing personalities (the learnable size tell)

> **Status: P1 (substrate) SHIPPED — 2026-06-09.** Data model
> (`poker/strategy/sizing_tendencies.py`: `SizingPersonality`, `SizeContext`,
> `ARCHETYPE_SIZE_BIAS` palette) + `sample_sizing_personality` (`base_size_bias`
> only, persona-seeded/deterministic) + `resolve_size_multiplier` (context-shape
> defined, P1 returns the base bias) + the `size_multiplier` seam threaded through
> `action_mapper._compute_raise_to` / `resolve_preflop_sizing` and wired at the
> tiered controller's preflop sizing call site. Per-personality override lane
> (`sizing_tendencies` config key, `parse_sizing_tendencies`,
> `_effective_sizing_tendencies`) fully wired (unused by stock personas in P1).
> **Validated:** mixed-field probe — preflop frequencies (VPIP/PFR/3-bet/4-bet/
> fold-to-3bet) byte-identical before/after across all 7 archetypes (only tiny
> downstream postflop-AF/all-in ripple from changed pot sizes); histogram —
> per-player open sizes differ within an archetype while archetype ranges OVERLAP
> (nit 2.2–2.6bb, maniac 2.2–2.9bb, tag 2.1–2.9bb — a given size maps to many
> types). No-op invariant + composition-order + determinism tests:
> `tests/test_strategy/test_sizing_tendencies.py` (1512 strategy tests green).
> Baseline-GTO / no-anchor controllers stay at multiplier 1.0 (exact).
> **P2+ (size_by_strength, palette, surfacing) NOT yet started.**

## The problem this solves (the Stacked lesson)

The frequency-shaping workstream made archetypes readable by how *often* they
3-bet. The obvious next step — give each archetype a characteristic raise *size*
(nit min-raises, maniac overbets) — would make size readable too, but in the
**wrong** way: get 3-bet *once* and you instantly know the type.

That is precisely the failure that sank the most-praised poker-game AI. "Stacked
with Daniel Negreanu" (2006) ran the U. Alberta **Poki** engine — genuine
world-leading opponent-modeling AI — and was still gutted by a serious player:
*"It took me only about 40 hands to figure out exactly which of the personalities
I was up against… the conservative players played ultra-conservative… the Poki
bots seemed to be pre-tuned."* (`docs/vision/texas_hold_em_research_text_markdown.md`
§3C.) The believability research is consistent: distinctiveness should be
**legible over play** (archetype-ID accuracy above chance is the *goal*) but NOT
a caricature you read in one orbit. The sweet spot is a read you **earn**.

So: sizing should be a per-PLAYER personality with real within-player variance —
a single 3-bet is ambiguous; the signature emerges only after you've watched the
player across many hands and showdowns. That is what feels human.

## How humans actually size (the model we're imitating)

A personal **default** + structured **adjustments** + **noise**, where the
default varies player-to-player:

- A habitual go-to open (2.2/2.5/3bb) and 3-bet (3–4×) — consistent *for that
  player*, but different *across* players, so the absolute number isn't
  type-diagnostic.
- Adjustments layered on top: **by hand strength** (the amateur "big = strong"
  tell, or reversed, or polarized), **by position** (bigger OOP/EP; recreational
  players are position-blind), **by emotion** (sizes creep up on tilt).
- **Round-number anchoring** ("he always makes it 7") and genuine hand-to-hand
  noise.

The read is the *pattern over time*, not one hand. We reproduce that.

## Design: a sampled, per-player sizing personality

Two independent sources of variety defeat the one-shot read:

1. **Per-player default, sampled semi-randomly (archetype-weighted).** At
   character creation, draw a sizing personality from a palette whose
   probabilities *lean* on archetype but don't determine it — a nit *leans*
   min-raise, a maniac *leans* overbet, but some nits play standard and some
   "regs" default big. Same-archetype players size differently; a given size maps
   to many types (many-to-many), which is what kills the caricature.
2. **Within-player variance.** Even one player's size is default ± a
   strength/position adjustment ± noise — so a single observation is
   ambiguous and the signature only resolves over N hands + showdowns.

### The palette (named sizing behaviors)

A registry of sizing *habits*, same `((name, strength), …)` shape as
`spot_tendencies` (the mechanism it mirrors). A player carries a *few*, sampled:

| behavior | what it does | read | who leans toward it |
|---|---|---|---|
| `base_size_bias` | the player's default open/3-bet multiplier (sampled w/ spread) | absolute size (weak alone) | everyone (mean leans by archetype) |
| `size_by_strength` | size tracks hand strength (big strong / small weak) | size↔showdown correlation (learnable) | recreational / fish |
| `polarized_size` | small with flats, big with value+bluffs | the trickier inverse read | LAG / strong regs |
| `position_blind` | same size from every seat | spot it across positions | recreational |
| `overbet_lean` / `min_raise_lean` | habitual big / small | a tendency, not a constant | maniac / nit-rock |
| `tilt_escalation` | size grows with emotional state (reads psychology axes) | size shifts after a beat | tilt-prone / ego |
| `anchor_number` | fixates on one favored amount | the repeated number | recreational |

Crucially the *legible-but-not-instant* behaviors (`size_by_strength`,
`anchor_number`, `position_blind`) are the **recreational** tells — the skill
gradient. The competent archetypes (tag/strong reg) stay **balanced and
unreadable** on size (or carry the *advanced* `polarized_size`). Importing the
obvious tell onto a "reg" would be the Drivatar "learned the bad habits" mistake
(research §Drivatars).

## Data model

- `sizing_tendencies: ((name, strength), …)` on the personality (alongside
  `spot_tendencies`), resolved at controller init like the deviation profile.
- A `sample_sizing_personality(anchors, persona_seed)`: deterministic, persona-
  seeded RNG so a character's sizing is **stable** (consistent "he always…"
  reads within and across sessions — the Nemesis "perceived memory" property).
  Draws `base_size_bias` from a per-archetype mean ± real spread (σ so
  same-archetype players visibly differ), then 0–2 palette behaviors by
  archetype-weighted probability.
- Stays in the existing per-personality override lane (same place
  `spot_tendencies` are parsed), so a specific character can pin a signature.

## Mechanism (where it applies)

- A `resolve_size_multiplier(sizing_personality, context) -> float` consulted at
  the point the raise size is computed — `action_mapper._compute_raise_to` /
  `resolve_preflop_sizing` (the seam #246 already touched). Context = scenario,
  hand-strength class (controller already classifies the canonical hand
  preflop), position, emotional state.
- **Order:** chart token (`raise_3x`) × sizing multiplier (the personality
  center) → live jitter ±12% (the human wobble, #246) → human-round (#246). The
  tendency sets the center; jitter+rounding give the realistic wobble + clean
  amounts. They compose.
- **Frequency-neutral by construction.** This only scales the *magnitude* of a
  raise; it never touches which action fires. The 3-bet/4-bet/VPIP bands we just
  tuned are untouched (validate this explicitly).
- **Live-realism layer.** Like the jitter, it's a per-player live behavior; the
  deterministic sim / Baseline-GTO reference stays exact (size multiplier = 1.0
  unless opted in).

## Why it isn't a one-shot read (the design property, tied to the research)

- Per-player `base_size_bias` spread → absolute size isn't type-diagnostic.
- Within-player variance (strength + position + noise) → one 3-bet is ambiguous.
- Palette × archetype-weighting → type↔size is many-to-many.
- Net: archetype-ID from *size alone* should be **above chance only after many
  hands** (legible, earned) — not the Stacked "40-hand" giveaway.

## Surfacing the read (the believability multiplier)

The research's loudest cross-game lesson: under-the-hood behavior is worthless to
players unless **perceived** (Nemesis callbacks, F.E.A.R. squad dialogue, Alien's
visible "learning"). The sizing tell pays off most when it's *felt*:
- the **opponent-model** learns the size↔strength correlation and (optionally)
  the bot exploits it / a coach surfaces it;
- **table talk** can call it out ("you've min-raised every pot tonight");
- the read lands at **showdown** (size predicted the strength). That loop — a
  habit you notice, then confirm at showdown — is the "alive" feeling. (Separate
  surfacing work; the substrate here is the precondition.)

## Validation

- **Size histogram** (`scripts/preflop_sizing_histogram.py`, extended):
  archetype size distributions should **overlap** (not separate cleanly — that's
  the point), while **per-player** signatures are distinct.
- **Hands-to-read** (borrow the research's archetype-ID methodology, §2.1):
  measure how many hands of observation it takes for size to predict type/strength
  above chance. Target: *legible but not instant* (not ~1 hand; emerges over
  dozens).
- **Frequency unchanged:** the mixed-field probe must show 3-bet/4-bet/VPIP
  identical to current (the hard constraint).
- **EV:** roughly neutral; a `size_by_strength` tell is a (realistic, intended)
  leak for the recreational tiers — confirm it doesn't hand a huge edge, and that
  the reg archetypes carry no such tell.

## Sequencing

1. **P1 — substrate.** ✅ **DONE (2026-06-09).** Data model +
   `sample_sizing_personality` (`base_size_bias` only) + the
   `resolve_size_multiplier` seam. Deliverable met: same-archetype players
   visibly size differently; histograms overlap; preflop frequencies unchanged
   (byte-identical). See the status block at the top.
2. **P2 — first learnable tell.** ✅ **DONE (2026-06-09).** `size_by_strength`
   (recreational-weighted) + the hands-to-read measurement. Deliverable met: an
   *earned* read — recreational carriers' opens become strength-predictive over ~250
   table-hands (AUC ≈0.50→0.78), regs stay flat at chance; frequencies byte-identical.
   See the status block at the top.
3. **P3 — palette.** `polarized_size`, `position_blind`, `tilt_escalation`
   (psychology-coupled), `anchor_number`.
4. **P4 — surface it.** ✅ **the size-tell voicing DONE (2026-06-11)** via #12's
   `spoken_reads` (`sizing_polarization_score` + `fold_to_big_bet` added to
   `READ_PRIORITY`; the bot voices an intuition-framed sizing tell). The
   review-tool "3-bet size" column (size as a first-class tunable stat) is
   separate and still pending. See the top status block +
   `docs/plans/PERCEPTIBILITY_CONDITIONING.md` Phase 4.

## Interactions / cross-links

- Composes with the live jitter + human rounding ([[PREFLOP_SIZING_VARIETY]] P0/
  P0.5) — tendency sets center, jitter/rounding give the wobble + clean amounts.
- Reuses the postflop size-gradient idea ([[SIZING_PERSONALITY]]) but as a
  per-PLAYER sampled personality, not a per-archetype constant.
- `tilt_escalation` reads the psychology axes ([[../technical/PSYCHOLOGY_OVERVIEW]]).
- The opponent model ([[../technical/CROSS_SESSION_OPPONENTS]]) is the consumer
  that makes the tell *felt*.
- Research backing: `docs/vision/texas_hold_em_research_text_markdown.md`
  (Stacked/Poki caricature lesson §3C; legibility-over-strength §2; opportunity-
  based stat defs §1A — which our #244 fold-to-3bet metric fix already matches).
- Frequency work this builds on: [[../technical/ARCHETYPE_SHAPING_FINDINGS]].

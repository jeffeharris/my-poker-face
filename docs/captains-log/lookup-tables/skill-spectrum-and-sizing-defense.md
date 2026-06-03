---
purpose: Narrative log of the player skill spectrum (named tiers over the adaptive stack) and the per-personality sizing-defense, including the monotonicity validation and the pre-reseed safety de-risk
type: design
created: 2026-06-02
last_updated: 2026-06-02
---

# The skill spectrum — giving the field a range

The river-readability work ended in a strange place: a tiered bot that, by
construction, sat at *the validated ceiling on every axis*. Balanced river
(`river_bluff_fraction=1.0`), defended check range (`stab_defense_intensity=0.5`),
overbets on (`overbet_fraction=1.0`), reads-and-adapts at full strength
(`exploitation_strength=1.0`). Every celebrity who ran the `sharp` controller was,
mechanically, as sharp as we'd ever validated. The field had no range. Socrates and
the dad-jokes guy played the same disciplined game.

That's not a field — it's a cohort of clones. The job this session was to give the
field a **skill spectrum**: some players genuinely sharp, others believably mediocre
(face-up, over-folding, slow to adapt) — *without building a new system.*

The precondition was already met, which is why it was worth doing now. Every lever is
already a per-instance intensity scalar (`river_bluff_fraction`,
`stab_defense_intensity`, `overbet_fraction`, `exploitation_strength`), and the
exploitation layer already takes a per-personality skill dial. Skill isn't a new
mechanism — it's a **preset bundle of intensities** over the stack we already built.
A config-plumbing + preset-table job, not a system.

## The one decision that shaped everything: it's a ladder *down*

The draft design assumed today's bot was a mid-strength `reg` with a sharper `shark`
above it. Reading the constructor defaults killed that assumption flat: the production
bot is **already at the validated ceiling**. There is no headroom above it, and we
wouldn't want any — "sharper than validated" is exactly the thing we have no
instrument to certify safe.

So the spectrum is a ladder *down from* today's bot, not *around* it:

- `shark` = today's defaults (the no-op tier). The ceiling, pinned.
- `reg`, `weak_reg`, `rec` = progressively weaker new tiers below it.

This reframing is what made the whole thing low-risk. **Weakening is free.** Turning
intensities *down* can only make a bot more exploitable and more face-up — which is
the intent. There's no "is this safe?" question for the weak tiers the way there'd be
for a stronger one. The only thing left to check is that the ladder is actually
*monotone* — that weaker tiers really do bleed more.

The default tier is a strict no-op: it never writes a field, so it can't clobber the
fish path's post-construction `overbet_fraction`/`_deviation_profile` customization.
Production behavior is unchanged until a weaker tier is explicitly assigned.

## Don't double-count adaptation_bias

The adaptive axis has two interchangeable linear factors: `exploitation_strength`
(per-instance, mutable) and `adaptation_bias` (frozen on `PersonalityAnchors`). They
multiply into the same exploitation product. The tempting move — have the tier set
*both* — would double-count, and worse, it would mutate the persona's authored
anchors. So the tier sets `exploitation_strength` **only**; the `adaptation_bias`
column in the tier table is *descriptive of the typical persona at that tier*, not
enforced. `exploitation_strength=0.1` already drags the whole product near zero for
`rec` regardless of the persona's authored bias — no need to touch the anchors.

This kept each persona's personality intact. Skill composes with the existing
per-archetype variation (preflop width charts, deviation profiles, psychology
anchors) as an *orthogonal* axis — it owns the adaptive/discipline dimension, the
chart owns preflop width, and they stack. A believable weak rec = a loose chart
(already) + low skill intensities (new).

## Monotonicity — the one real check, on three instruments

Weakening being free, the entire validation reduces to: *is the ladder monotone?* The
adaptive-reader instruments from the previous session were exactly the tools for it.
Hero = Baseline, heads-up, 2000h × seeds 42/142/242 on `measure_passivity`, driven by
a new `SKILL_TIER=` env hook:

- **Readability (tell map vs a calling station).** shark's river big bet is balanced
  (34% bluff share vs 37% GTO; 208 river bluffs fired). weak_reg leaks a **face-up
  1.0× size** (95% value). rec drops overbets entirely, fires **0 river bluffs**, and
  its river is **face-up** (98% value). The instruments *see* the skill drop
  shark → weak_reg → rec.
- **Value extraction (bb/100 vs the station).** +59.7 > +49.5 > +32.6 — monotone. (All
  three still beat the leaky station — balance under-exploits a fish, the
  BUILD_A_BETTER_BOT lesson — but the shark pulls ~2× from the same donor.)
- **Stab-defense (vs an aggressive reg).** fold% facing a bet 24% < 32% < 39%; fold%
  *with air* (the capped check range the stab attacks) 41% < 58% < 71%. rec over-folds
  its air where shark defends — cleanly monotone on the sensitive direct metric. (The
  bb/100-vs-stabber order is within the noise this harness explicitly warns is too
  insensitive for postflop deltas; the fold% ladder is the authoritative read — a
  reminder to trust the metric matched to the question, not the convenient one.)

The one axis Phase 3 left to *argument* rather than measurement was
`exploitation_strength` itself — the Baseline hero has `anchors=None`, so the
exploitation layer no-ops on it. Rather than leave it asserted, I measured it on an
anchored TAG hero (`exploit_bb100`, CallStation×2/FoldyBot×2 backdrop, 4000h × seeds
42/142). Because `exploitation_strength` and `adaptation_bias` are interchangeable
linear factors, the exposed `--hero-adaptation-bias` sweep measures exactly the tier
ladder: **1.0 → +38.5 · 0.7 → +30.3 · 0.4 → +14.8 · 0.1 → +2.9 bb/100** (first three
CI-clear positive; 0.1 spans zero — `rec` barely exploits, by design). A clean
38.5 → ~0 decay. The ladder is monotone by measurement, not just by the
linear-multiplier argument.

## The roster — keyed to a signal that already encodes "how sharp"

Phase 4 made `skill` a per-persona field read in `TieredBotController.__init__`
(mirroring the `adaptive_overbet` read, so it's native to every live build path; the
factory `skill=` kwarg still wins as an explicit override; an unknown tier logs and
falls back to the ceiling). The assignment didn't need a new judgment call — each
character already carries an `adaptation_bias` band that *is* "how sharp is this
character." So the roster is keyed to it:

- `≥ 0.65 → shark` (6): the deductive/strategic readers — Socrates, Sherlock, Sun Tzu,
  Machiavelli, Cleopatra, Elizabeth I.
- `0.45–0.64 → reg` (14): the solid middle — Churchill, Napoleon, Franklin, Twain…
- `0.25–0.44 → weak_reg` (23): the broad mediocre middle, **including the
  high-aggression maniacs** (Blackbeard, Zeus, Queen of Hearts, Honey Badger) — who
  become *aggressive but exploitable*. An active chart + weak skill is neither passive
  nor spewy chaos: it's a maniac who bets a lot and is easy to play back at. That's a
  real and missing field archetype.
- `≤ 0.2 → rec` (7): the gentle/passive face-up over-folders — Buddha, Bob Ross,
  Dr. Seuss, the Grandmother, the dad-jokes guy.
- **Excluded (12):** the 9 `archetype=fish` tourists (the rule-bot floor *below* the
  spectrum — no adaptive stack at all) + the 3 bot reference personas
  (CaseBot/GTO-Lite/BaselineSolver).

Crucially the effect is **staged, not live**: the roster lives in `personalities.json`
and only changes the field after the DB is re-seeded (`seed_personalities_from_json`
round-trips arbitrary config keys). Until a re-seed, production is unchanged and
untagged personas default to the no-op ceiling.

## De-risking the re-seed (the one thing that *isn't* free)

Weakening bots is free; **re-seeding the database isn't.** That's the operation that
could actually break something. So before calling it done I ran an A/B cash-economy
sim (`scripts/run_economy_sim`, 400 ticks, `hand_sim_prob=1.0`, seed 42) on two fresh
seeded DBs — baseline (skill stripped) vs rostered (the new tiers):

- Both ran the full 800 ticks with **zero errors/tracebacks**.
- Chips conserve — audit drift baseline 269 / rostered **0**, against ~1.8M chips.
- Macro-health comparable and non-degenerate: Gini_final 0.71 vs 0.75 (slightly more
  spread — the *intended* effect of skill differentiation), AI count 59 both, defaults
  0 vs 1.

One honest caveat worth recording: the per-tier **absolute** net chips do *not* cleanly
show the ladder (reg looked worst at −17k). That's not a skill inversion — it's the
expected **stake/bankroll/volume confound**: regs are higher-bankroll personas seated
at bigger stakes, so they swing larger in absolute terms, and everyone trends negative
against the rake sink. The clean skill→winrate ordering is the fixed-stakes
head-to-head evidence above; the economy sim's only job here was the *safety* de-risk,
and it passes. Re-seed is safe — but I won't pretend the economy sim showed the ladder,
because it can't.

## Sizing-defense, made per-personality

A loose end from the readability session: Phase B "fold-more vs a face-up big bettor."
Measured against a maximally face-up bot it's **~+4.27 bb/100 [−8.20, +16.74]** — real
but marginal, CI spans zero. Not enough to turn on globally. So it ships the way
`adaptive_overbet` does: **per-personality opt-in** (`"sizing_defense": true` in the
persona config), default-OFF globally.

Two refinements made it safer to hand out:

1. **Proportional dampener.** The call-retention multiplier now scales with *how
   face-up the read is* — 1.0 (no fold-more) at the `min_polar` gate, ramping to the
   floor at `full_polar` (0.40). A barely-face-up or small-sample read barely folds; a
   blatantly face-up one folds hard. This bounds the misfire cost on weak/false-positive
   reads and shrinks the surface an adapting adversary can exploit.
2. **A paired-CRN measurement arm** (`ab_node_attribution`, `loose_faceup` roster,
   per-arm `--sizing-defense-b`) so B's pure EV vs a face-up big bettor can be measured
   with common random numbers instead of the noisy same-seed A/B. The finding that
   keeps it honest: B fires on **<1% of hands** even 6-max against an all-face-up table
   — the marginal-made-hand-vs-big-bet spot is genuinely rare — so a tight CI needs
   real volume, and heads-up it barely fires at all. The effect is small because the
   *spot* is small, not because the logic is wrong.

The known limit, recorded for whoever picks it up: the underlying
`sizing_polarization_score` is a lifetime cumulative mean with no recency decay, **and**
folding suppresses the very showdowns the read needs — so it flips off *slowly* if an
adversary starts bluffing big into it. Recency-weighting the read is the real fix;
it touches the shared memory layer, so it's scoped as a follow-up, not smuggled in here.

## Where it landed

The field now has range, built entirely on the existing stack: 6 sharks / 14 regs /
23 weak_regs / 7 recs across the celebrities, keyed to the signal that already encodes
sharpness, composing with (not overriding) each persona's aggression and looseness. The
ladder is monotone on three independent instruments plus a measured exploitation axis.
The re-seed that activates it is de-risked clean. And sizing-defense is a per-persona
weapon you can hand to the characters who should have it, with its misfire cost bounded
and its real limit written down rather than hidden.

The shape of the whole session: the *expensive* questions (is a stronger bot safe? does
weakening break anything? does the ladder actually order?) got pushed onto instruments
and sims, and the *free* moves (turning intensities down) were taken without ceremony.
Nothing shipped on faith that could have been measured — and the two things that
genuinely can't be measured without live humans (the exact magnitude vs a real
over-folding human; how a real adversary counter-adapts to sizing-defense) are named as
open, not papered over.

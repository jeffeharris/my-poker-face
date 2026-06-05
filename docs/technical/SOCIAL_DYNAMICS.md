---
purpose: How verbal quick-chat (trash talk, flattery, sarcasm) is received in-character and moves the durable relationship axes
type: architecture
created: 2026-06-03
last_updated: 2026-06-03
---

# Social Dynamics — Verbal Reception & Relationship Axes

When a player sends a quick-chat with a *tone* (trash talk, props, flatter, gloat,
…), two independent things happen to each AI recipient:

1. **Emotional reaction** — composure / confidence / energy move (drives *play* this
   hand). Lives in [`PSYCHOLOGY_OVERVIEW.md`](PSYCHOLOGY_OVERVIEW.md).
2. **Relationship shift** — heat / respect / likability move (durable, persisted,
   drives exploitation modifiers + LLM prompt context across sessions). Lives in
   [`CROSS_SESSION_OPPONENTS.md`](CROSS_SESSION_OPPONENTS.md).

This doc covers the seam between them: **how a verbal message is received
in-character**, and how that reception reshapes the relationship axes. The central
idea is that *the same words land differently on different characters* — and that
the recipient's read of the words (do they catch the sarcasm? are they vain enough
to swallow flattery?) is itself a pure function of their static personality anchors.

> Status note. The temperament + sarcasm work described here is **shipped on
> `development`** (merged from the `temperament` / `quickchat-palette-sarcasm`
> branches). What is **not** built: the `W_SOCIAL` hog-leave-pressure term (being
> resented has no effect on the hog's own movement yet), the sarcastic UX register
> on the frontend tone selector, and the emotional-state-sensitive reception
> extension (tilt amplifies social hits) — spec'd, flag-gated, default OFF.
> Source: `docs/plans/SOCIAL_TEMPERAMENT_AND_QUICKCHATS.md`. Where a plan/log
> conflicts with code, the code wins.

---

## Two layers, one dispatch, deliberate asymmetry

A chat send enters at `dispatch_chat_relationship_event(game_data, sender,
addressing, tone, intensity)` — `flask_app/handlers/chat_relationship.py:307`.
Three architectural invariants hold throughout:

- **The two layers are decoupled.** The emotional reaction and the relationship
  shift are computed and applied independently; a relationship-repo failure does
  not block the emotional reaction and vice versa.
- **One mutation seam for relationships.** `OpponentModelManager.record_event`
  (`poker/memory/opponent_model.py:1977`) is the *only* writer of `RelationshipState`.
  No dispatch path bypasses it. (Invariant stated in
  `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md`.)
- **Actor side is never temperament-touched.** Only the *mirror* (the recipient's
  view of the sender) is reshaped by disposition or sarcasm reading. The sender's
  own feelings about what they said always use the neutral event table. This
  asymmetry is explicit: how it *lands* depends on the recipient; how it *feels to
  say* does not (`relationship_events.py:415-421`).

---

## 1. Disposition: how a character takes a jab

`PlayerPsychology._classify_social_disposition()`
(`poker/player_psychology.py:452`) maps a character's static anchors to one of
three reception styles. **Pure function of anchors** — no game state, no per-hand
mood, no `personalities.json` field. A character always reacts in-character.

| Result | Meaning | Branch (anchors) |
|---|---|---|
| `stung` | thin-skinned / proud-but-reserved — the needle bites | `poise <= 0.40`; **or** `ego >= 0.60` and `expressiveness < 0.55` |
| `energized` | banter-lover — rivalry as bonding | `ego >= 0.60` and `expressiveness >= 0.55`; **or** `poise >= 0.60` and (`expressiveness >= 0.55` or `baseline_aggression >= 0.60`) |
| `stoic` | detached — barely registers | default (no branch matched) |

Thresholds are class constants (`player_psychology.py:437-450`):

```
_SOCIAL_STUNG_POISE_CEILING  = 0.40
_SOCIAL_PROUD_EGO_FLOOR      = 0.60
_SOCIAL_EXPRESSIVE_FLOOR     = 0.55
_SOCIAL_COMPOSED_POISE_FLOOR = 0.60
_SOCIAL_AGGRESSIVE_FLOOR     = 0.60
```

**Why derive disposition instead of adding a `social_temperament` trait?** The
classifier already existed and fed the *emotional* axes; the temperament feature
routed it to a *second* consumer (the relationship mirror) rather than building a
new brain. Deriving from existing anchors (poise/ego/expressiveness/aggression)
also keeps `personalities.json` narrower (source:
`docs/captains-log/temperament/social-temperament-and-sarcasm.md`, 2026-06-01;
`docs/plans/SOCIAL_TEMPERAMENT_AND_QUICKCHATS.md`). The doc-comment at
`player_psychology.py:433-436` records validation against the seed roster: proud
tyrants → `stung`, wits & charmers → `energized`, sages & bots → `stoic`.

---

## 2. Trash-talk reception — disposition-keyed mirror shift

A hostile tone fires `RelationshipEvent.TRASH_TALK` (mid-hand) or
`TAUNT_POST_WIN` (post-round). On the **mirror** side only, the recipient's
disposition can *replace* the neutral mirror shift via
`temperament_adjusted_mirror_shift(event, disposition)`
(`relationship_events.py:468`), backed by `_TEMPERAMENT_MIRROR_OVERRIDES`
(`relationship_events.py:395`). Only these two events have overrides; everything
else (and the `stoic` disposition, and any unknown string) falls through to the
neutral `mirror_shift(event)`.

`AxisShift(heat, respect, likability)` per disposition vs. the neutral mirror it
replaces:

| Event / disposition | heat | respect | likability | vs. neutral |
|---|---|---|---|---|
| `TRASH_TALK` neutral mirror | +0.05 | 0.00 | −0.10 | — |
| `TRASH_TALK` / `energized` | 0.00 | +0.02 | **+0.05** | inverts the likability penalty (banter bonds) |
| `TRASH_TALK` / `stung` | +0.10 | 0.00 | −0.15 | ~2× heat, ~1.5× likability |
| `TAUNT_POST_WIN` neutral mirror | +0.15 | 0.00 | −0.10 | — |
| `TAUNT_POST_WIN` / `energized` | 0.00 | +0.05 | +0.04 | inverts penalty |
| `TAUNT_POST_WIN` / `stung` | **+0.20** | 0.00 | −0.15 | sharpest social needle |

Calibration anchor: the `stung TAUNT_POST_WIN` heat of **+0.20** was set to match
the existing maximum mirror heat in the table (`STAKE_DEFAULTED` mirror, +0.20,
`relationship_events.py:321`) so the feature invented **no new ceiling**
(`relationship_events.py:403-406`; rationale in the captain's log).

The **actor** side always uses the sincere `ACTOR_AXIS_SHIFTS` entry regardless of
how the target took it — `TRASH_TALK` actor = `heat +0.10 / respect 0 /
likability −0.05` (`relationship_events.py:195`); `TAUNT_POST_WIN` actor =
`heat +0.20 / respect 0 / likability −0.10` (`relationship_events.py:197`).

**Motivation (`SOCIAL_TEMPERAMENT_AND_QUICKCHATS.md`):** before this, needling had
no per-recipient color — and `STACK_DOMINANCE` already let an AI *resent* a hog,
but resentment only gated staking/seating preference, never behavior. Being
disliked still has no movement consequence (that is the deferred `W_SOCIAL` term).

---

## 3. The sarcasm-detection gate

When a message is sent with the `sarcastic` register, its *surface* tone is
inverted into a different reception — but **only for recipients who detect the
sarcasm**. A character who misses it reacts to the *literal* surface.

**Detection** is `_detects_sarcasm()` (`player_psychology.py:488`): a pure function
of `adaptation_bias`, the same opponent-reading trait that lets the perceptive
"see through" flattery.

```
_SARCASM_DETECTION_ADAPT_FLOOR = 0.45   # player_psychology.py:486
```

The gate is wrapped by `_perceives_sarcasm(psychology)`
(`chat_relationship.py:41`) behind the flag `SARCASM_DETECTION_ENABLED = True`
(`chat_relationship.py:38`). With the flag **off**, every recipient is assumed to
perceive sarcasm (the prior behavior); flip it off to A/B or if a sim shows the
read-dependence runs hot.

**Surface-direction mode** (`sarcasm_mode_for_tone`, `chat_intent.py:90`, table
`_SARCASM_MODE_BY_TONE` at `chat_intent.py:78`):

| Tone | Mode | Effect |
|---|---|---|
| `props` | `sharpen` | backhanded "nice play" |
| `gracious` | `sharpen` | fake-nice "wp" |
| `commiserate` | `sharpen` | fake sympathy |
| `flatter` | `sharpen` | mocking (resolved on the flattery path) |
| `trash_talk` | `soften` | hostile → affectionate ribbing |
| `humble` | `self` | dry self-deprecation |

A tone absent from this map has no sarcastic variant — the `sarcastic` register
falls back to neutral reception. (Only warm/positive-surface tones can be inverted;
already-hostile tones have nothing to sharpen. The earlier draft had this set
backwards — `gloat`/`trash_talk` had been tagged sarcasm-compatible — and was
corrected. Source: captain's log.)

**Mirror shift** when sarcasm *is* perceived: `sarcasm_mirror_shift(mode,
disposition)` (`relationship_events.py:453`), table `_SARCASM_MIRROR_SHIFTS`
(`relationship_events.py:422`), 3 modes × 3 dispositions:

| mode / disposition | heat | respect | likability |
|---|---|---|---|
| `sharpen` / energized | 0.00 | +0.05 | +0.05 |
| `sharpen` / stoic | +0.03 | 0.00 | −0.03 |
| `sharpen` / stung | +0.12 | −0.05 | −0.15 |
| `soften` / energized | −0.05 | +0.02 | +0.08 |
| `soften` / stoic | 0.00 | 0.00 | +0.03 |
| `soften` / stung | +0.02 | 0.00 | +0.02 |
| `self` / * | 0.00 | +0.02/0/0 | +0.05/+0.03/+0.02 |

Note the `sharpen / stung` row carries `respect −0.05` — the "condescension cut"
that makes a perceived backhand sting *worse* than an open jab (being talked down
to, not merely insulted; `relationship_events.py:428-430`). The `sharpen /
energized` likability is capped at +0.05 so a backhand never out-bonds a sincere
compliment.

**The miss is the point.** If the recipient's `adaptation_bias < 0.45`,
`_mirror_override` (`chat_relationship.py:192`) falls through past the sarcasm
branch to `temperament_adjusted_mirror_shift(event, disposition)` — i.e. the
*literal* event reception. A sarcastic `props` they don't catch fires the neutral
`PROPS` mirror (`heat −0.02 / respect +0.08 / likability +0.05`,
`relationship_events.py:307`) and **genuinely raises their respect**. The
emotional layer matches: `_sarcasm_emotional_stimulus(mode)`
(`chat_relationship.py:82`) maps a perceived `sharpen` to a `'jab'`, while a missed
one reverts to the event-derived `'praise'`.

---

## 4. Flattery — a separate vanity axis

Flattery (`tone='flatter'`) does **not** ride the fixed tone→event map. It is
dispatched per-target in `_dispatch_flattery` (`chat_relationship.py:241`) because
the outcome depends on each target's *vanity*, classified independently of the jab
disposition by `_classify_flattery_disposition()` (`player_psychology.py:500`):

| Result | Branch | Relationship event |
|---|---|---|
| `vain` | `ego >= 0.60` (checked first) | `FLATTERY_LANDED` |
| `sees_through` | `adaptation_bias >= 0.50` | `FLATTERY_BACKFIRED` |
| `unmoved` | default | none — no axis movement, no event |

```
_FLATTERY_VAIN_EGO_FLOOR         = 0.60   # player_psychology.py:480
_FLATTERY_PERCEPTIVE_ADAPT_FLOOR = 0.50   # player_psychology.py:481
```

The two classifiers are deliberately orthogonal: a character can be `stung` by a
needle yet `vain` enough to lap up flattery (both high-ego), or shrug a needle yet
`sees_through` a buttering-up (high `adaptation_bias`). Vanity is checked first
because "a proud reader still wants to believe the praise"
(`player_psychology.py:507-508`).

Flattery's relationship valence flips by vanity rather than by a disposition-keyed
*mirror override* — the path fires `FLATTERY_LANDED` / `FLATTERY_BACKFIRED` through
`record_event` with **no** `mirror_shift_override` (`chat_relationship.py:297-304`),
using the events' own actor/mirror shifts:

| Event | side | heat | respect | likability |
|---|---|---|---|---|
| `FLATTERY_LANDED` | actor | 0.00 | 0.00 | +0.02 |
| `FLATTERY_LANDED` | mirror | −0.02 | −0.02 | +0.06 |
| `FLATTERY_BACKFIRED` | actor | 0.00 | 0.00 | −0.02 |
| `FLATTERY_BACKFIRED` | mirror | +0.03 | −0.08 | −0.05 |

(`relationship_events.py:200-201`, `:308-309`.) The emotional-axis side flips too:
`react_to_social_stimulus('flatter', …)` (`player_psychology.py:545`) fires
`social_flattery_vain` (confidence +0.08 / energy +0.05) or
`social_flattery_seen_through` (composure −0.03), and `unmoved` is a no-op
(`player_psychology.py:187-188`, `:551-552`).

---

## 5. The mutation seam: `record_event` + `mirror_shift_override`

All relationship-axis writes go through one method
(`opponent_model.py:1977`):

```python
def record_event(self, actor_id, target_id, event, *,
                 impact_score=1.0, context_multiplier=1.0,
                 narrative="", hand_summary="", hand_id=None,
                 mirror_shift_override: Optional[AxisShift] = None,
                 now=None) -> None
```

It updates **both** directions bilaterally:

1. actor's view of target ← `actor_shift(event)` (always sincere)
2. target's view of actor ← `mirror_shift_override or mirror_shift(event)`

The `mirror_shift_override` kwarg (added for temperament/sarcasm) replaces the
neutral mirror lookup **for the target POV only**. `_mirror_override`
(`chat_relationship.py:192`) supplies it: sarcasm transform if perceived, else the
temperament reshape, else `None` (human target / no psychology / unknown tone →
neutral global mirror). `context_multiplier` (the chill/spicy intensity lever)
scales the chosen shift downstream in `_apply_one_side` (`opponent_model.py:2101`),
so intensity composes on top of the temperament/sarcasm reshape.

**Intensity lever** (`map_tone`, `chat_intent.py:131`): mid-hand tones multiply by
`chill → 0.5`, `spicy → 1.0`, default `1.0` when missing (`_INTENSITY_MULT`,
`chat_intent.py:124`). Post-round tones ignore intensity (they encode it in the
choice). `sarcastic` is a *third position* in the intensity slot, not a scalar —
"chill-sarcastic vs spicy-sarcastic is a distinction nobody reaches for" (source:
captain's log).

---

## 6. Tone vocabulary & what is emotional-only

`map_tone(tone, intensity)` (`chat_intent.py:131`) resolves a tone to a
`(RelationshipEvent, multiplier)`; `None` means "no relationship-axis effect, skip
the dispatch."

| Tone | Phase | Event | base mult |
|---|---|---|---|
| `trash_talk` / `tilt` / `goad` | mid | `TRASH_TALK` | 1.0 |
| `needle` / `bait` | mid | `TRASH_TALK` | 0.5 |
| `befriend` | mid | `FRIENDLY_BANTER` | 1.0 |
| `props` | mid + post | `PROPS` | 1.0 |
| `gloat` | post | `TAUNT_POST_WIN` | 1.0 |
| `humble` | post | `FRIENDLY_BANTER` | 1.0 |
| `salty` | post | `TRASH_TALK` | 1.0 |
| `gracious` | post | `COMPLIMENT` | 1.0 |
| `commiserate` | post | `COMMISERATE` | 1.0 |

(`_MID_HAND_TONE_MAP` `chat_intent.py:56`; `_POST_ROUND_TONE_MAP`
`chat_intent.py:105`.)

**Emotional-layer-only tones.** `intimidate`, `dare`, and their post-round reskins
`vow` / `cry_luck` are intercepted *before* `map_tone` in
`dispatch_chat_relationship_event` and routed straight to the psychology axes via a
coarse stimulus (`_EMOTIONAL_TONE_STIMULUS`, `chat_relationship.py:55`). They move
composure/confidence to affect *play* and **never touch** heat/respect/likability —
they are intentionally absent from the tone maps (`chat_intent.py:66-69`,
`:114-116`). The asymmetry each is named for falls out of the existing
`apply_pressure_event` filters for free (`player_psychology.py:189-199`):

- `intimidate` → `social_intimidate` (composure-led; the `(1−poise)` filter
  rattles the timid into folding, leaves the composed unmoved).
- `dare` → `social_dare` (confidence-led; the *ego* filter makes the proud puff up
  and overplay, the modest barely register — "you can't dare a humble man into a
  call").

Rationale (`SOCIAL_TEMPERAMENT_AND_QUICKCHATS.md`): making someone *fold* is a
different goal from making them *resent* you, so these stay orthogonal to the
relationship layer. `COMMISERATE` is the one genuinely new color — post-loss warmth
aimed at a *bystander* rather than the player who beat you, which no prior event
covered.

**Broadcast vs. explicit.** A message with no addressee fires emotional reactions at
`BROADCAST_REACTION_SCALE = 0.5` (`chat_relationship.py:30`) but **never** writes
relationship axes — there is no pairwise attribution for "talking to the table."

---

## 7. How the axes feed back into play

The relationship axes are consumed read-only (see `CROSS_SESSION_OPPONENTS.md` for
persistence + reload):

- **Exploitation modifier** — `get_relationship_modifier`
  (`poker/memory/relationship_modifier.py`): `heat > 0.5` → `bluff_freq_mult ×1.3`,
  `call_threshold_offset −0.03` (chase rivals); `respect > 0.7` →
  `fold_to_pressure_mult ×0.7` (harder to bluff off the respected); `likability >
  0.7` → `bluff_freq_mult ×0.85` (soft on friends). Thresholds
  `HEAT_RIVAL_THRESHOLD=0.5`, `RESPECT_HIGH_THRESHOLD=0.7`,
  `LIKABILITY_HIGH_THRESHOLD=0.7` (`relationship_modifier.py:54-56`).
- **Prompt context** — `build_relationship_context`
  (`poker/memory/relationship_prompt.py`) injects a labeled block; the bucket
  thresholds are calibrated to **match** the modifier thresholds so the numeric read
  and the verbal "rival/friendly" label agree (`CASH_MODE_AND_RELATIONSHIPS.md`).

**Heat decays, respect & likability do not.** `project_heat`
(`opponent_model.py:1165`) holds heat at its stored value for a plateau
(`HEAT_DECAY_PLATEAU_DAYS = 7`, `opponent_model.py:1098`) then decays exponentially
(`HEAT_DECAY_HALF_LIFE_DAYS = 14`, `:1099`). Rivalries cool; perceived skill and
warmth are earned, permanent state.

---

## End-to-end example: spicy `trash_talk` at a `stung` target

1. UI → `dispatch_chat_relationship_event(…, tone='trash_talk', intensity='spicy')`.
2. Not emotional-only, not `flatter`. `map_tone` → `(TRASH_TALK, 1.0)`. Sarcasm
   mode `None`.
3. Emotional layer: target's `react_to_social_stimulus('jab', …)` →
   disposition `stung` → `social_jab_stung` → composure −0.10, confidence −0.04
   (each through the ego/poise sensitivity filter in `apply_pressure_event`).
4. Relationship layer: `_mirror_override(…)` →
   `temperament_adjusted_mirror_shift(TRASH_TALK, 'stung')` →
   `AxisShift(heat +0.10, respect 0, likability −0.15)`.
5. `record_event(actor=human, target=napoleon, TRASH_TALK,
   mirror_shift_override=<above>)`:
   - actor side (human→Napoleon): neutral `ACTOR[TRASH_TALK]` = `heat +0.10,
     likability −0.05`.
   - mirror side (Napoleon→human): the override (stung-amplified).
6. Both rows persist via the relationship repository.

---

## Essential files

| File | Holds |
|---|---|
| `poker/player_psychology.py` | `_classify_social_disposition` (:452), `_classify_flattery_disposition` (:500), `_detects_sarcasm` (:488), `react_to_social_stimulus` (:517), social `_PRESSURE_IMPACTS` (:179-199) |
| `poker/memory/relationship_events.py` | event enum, `ACTOR_AXIS_SHIFTS` (:176), `MIRROR_AXIS_SHIFTS` (:285), `_TEMPERAMENT_MIRROR_OVERRIDES` (:395), `_SARCASM_MIRROR_SHIFTS` (:422), `temperament_adjusted_mirror_shift` (:468), `sarcasm_mirror_shift` (:453) |
| `poker/memory/chat_intent.py` | `map_tone` (:131), `sarcasm_mode_for_tone` (:90), tone maps (:56, :105), intensity mult (:124) |
| `flask_app/handlers/chat_relationship.py` | `dispatch_chat_relationship_event` (:307), `_mirror_override` (:192), `_dispatch_flattery` (:241), `_perceives_sarcasm` (:41), `SARCASM_DETECTION_ENABLED` (:38) |
| `poker/memory/opponent_model.py` | `record_event` (:1977), `_apply_one_side` (:2101), `project_heat` (:1165) |
| `poker/memory/relationship_modifier.py` | axis→exploitation thresholds + multipliers |
| `docs/plans/SOCIAL_TEMPERAMENT_AND_QUICKCHATS.md` | canonical design doc |
| `docs/captains-log/temperament/social-temperament-and-sarcasm.md` | rationale, wrong turns, calibration |

See also: [`PSYCHOLOGY_OVERVIEW.md`](PSYCHOLOGY_OVERVIEW.md) (the emotional axes /
zones / pressure events) and [`CROSS_SESSION_OPPONENTS.md`](CROSS_SESSION_OPPONENTS.md)
(how relationship state persists and reloads).

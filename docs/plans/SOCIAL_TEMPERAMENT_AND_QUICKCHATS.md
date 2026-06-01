---
purpose: Make table social friction (heat from being a hog, trash talk) drive behavior and vary by personality temperament, plus nuanced quickchats to express it
type: design
created: 2026-05-26
last_updated: 2026-06-01
---

> **Status (2026-06-01):** The **trash-talk-reception** half of the
> temperament nuance is **implemented** (derived from anchors, no
> personalities.json change). Inbound `TRASH_TALK` / `TAUNT_POST_WIN`
> now land on the recipient's relationship axes per their social
> disposition: `energized` bonds over the needle (heat suppressed,
> likability/respect up), `stung` takes it harder (heat/likability
> amplified), `stoic` keeps the neutral global mirror. Seam:
> `temperament_adjusted_mirror_shift` in `poker/memory/relationship_events.py`,
> reused disposition from `PlayerPsychology._classify_social_disposition`,
> wired through a `mirror_shift_override` kwarg on
> `OpponentModelManager.record_event` and resolved in
> `flask_app/handlers/chat_relationship.py`.
>
> **Quickchat UI groundwork also shipped (frontend-only):** the delivery
> register (`chill`/`spicy`) moved into its own row **directly below the
> tone selector** (was buried in the suggestions header), and the register
> is now remembered **per tone** (last-used) via a `{tone: register}`
> localStorage map (`quickchat_register_by_tone`) instead of one global
> value. These are the scaffolding the `sarcastic` register slots into —
> see "Quickchats redesign" below.
>
> Still **deferred / spec-only**: the near-term aggregate-heat
> leave-pressure term, the **hog disposition** (needs that movement term
> first), and the whole **sarcasm + palette redesign** (this doc's
> "Quickchats redesign" section).

# Social Temperament & Nuanced Quickchats

Side-track spun out of the predator-retention hoard fix (see
`[[project_predator_retention_hoard]]` / `cash_mode/movement.py`). Two
gaps surfaced:

1. **Heat is decoupled from behavior.** `STACK_DOMINANCE`
   (`poker/memory/relationship_events.py`) already makes the table resent
   a deep-stacked hog (observers' `heat`↑, `likability`↓), and it's firing
   live — but those axes only feed staking gates + seating preference,
   never the hog's own movement. Being hated has no consequence.
2. **Social reactions are personality-flat.** Every AI reacts to the same
   event with the same `AxisShift`. In reality some characters *relish*
   being the feared chip-bully or trade good-natured needling; others are
   genuinely offended. That intuition isn't expressed anywhere yet.

## Near-term (agreed scope — keep it simple)

**Aggregate table heat increases leave pressure.** Personality-blind for
now: the more attendees who resent the hog (sum / count of inbound `heat`
above a threshold), the higher the hog's per-hand leave probability.

- Add a 5th term to `compute_leave_pressure` in `cash_mode/movement.py`
  (alongside `stake_up / short / detached / tenure`), e.g. `social`,
  weight `W_SOCIAL`. Drive it from inbound heat aggregated across the
  seated peers who know the hog.
- Plumb the aggregate into `MovementContext` via a new lookup, mirroring
  how `energy` is sourced (`lobby.py` sim path + `game_handler.py`
  `_psych_lookup`). Needs a `relationship_repo` aggregate:
  "sum/mean inbound heat toward `pid` from currently-seated peers."
- Pairs naturally with the `stake_up` graduation fix: stack pressure says
  "I've won enough," social pressure says "and the table's sick of me."

This is the whole near-term deliverable. The temperament nuance below is
**deferred** — do not block the simple version on it.

## The nuance (future)

Per-personality **temperament** modulates how social events land. Two
independent dispositions:

- **Hog disposition** — relish vs discomfort at *being* the resented deep
  stack. A villain/predator (Lady Macbeth, Blackbeard, Scrooge, Queen of
  Hearts) enjoys it: little/no `social` leave pressure, maybe a
  confidence/composure bump. A people-pleaser (Bob Ross, the Kindergarten
  Teacher, the Grandmother) feels it: stronger `social` leave pressure,
  maybe gives action back / softens.
- **Trash-talk reception** — respect vs offense at inbound needling. A
  competitive banter-lover (the Honey Badger, Oscar Wilde, Freddie
  Fratboy) *respects* good trash talk → it can **build** `likability`/
  `respect` (rivalry-as-bonding), inverting today's flat penalty. An
  earnest character is offended → today's `heat`↑/`likability`↓ stands.

Mechanism sketch: make the relationship `AxisShift` lookup
personality-aware — a per-recipient multiplier or override layered on top
of the global `EVENT_AXIS_SHIFTS` table, keyed off a temperament trait.
Candidate hook: a new `social_temperament` field (or derive from existing
`attitude` / `aggression` / `chattiness` traits in `personalities.json`).
Keep the global table as the neutral default; temperament only deviates.

## Quickchats redesign (spec)

Supersedes the old one-line "add a SARCASM tone" idea. Sarcasm is **not a
tone** — it's a *delivery register*. A standalone `SARCASM` tone has a
hole in it (sarcastic about *what?*); sarcasm needs an underlying
sentiment to invert. So it rides on top of a tone, in the same slot as
`chill`/`spicy`.

Two surfaces feed the dispatch today (`flask_app/handlers/chat_relationship.py`
→ `poker/memory/chat_intent.py` `map_tone`): **mid-hand** `ChatTone` and
**post-round** `PostRoundTone`. Mid-hand tones take an intensity modifier;
post-round tones "encode their own intensity in the choice" (no chill/spicy).

### 1. Delivery register replaces "intensity"

The delivery slot becomes an enum, not a scalar:

- **Mid-hand:** `chill` · `spicy` · `sarcastic` — mutually exclusive.
  Sarcasm carries its own implicit intensity; "chill-sarcastic" vs
  "spicy-sarcastic" is a distinction nobody reaches for, so it's a third
  *position*, not an orthogonal axis.
- **Post-round:** `earnest` · `sarcastic` — chill/spicy never applied here
  anyway, so sarcastic is the only register worth adding.

Mechanically the slot stops being a pure multiplier: `chill`/`spicy`
resolve to the existing `0.5`/`1.0` multipliers; `sarcastic` is its own
branch (below).

### 2. Sarcasm has two halves — both required

1. **Generation** — a flag into the suggestion prompt so the produced line
   reads dry/backhanded ("Oh, *great* play") instead of plain.
2. **Reception** — a temperament-aware valence flip on the recipient's
   relationship axes, reusing the `mirror_shift_override` seam already
   built for trash-talk reception. Without (2), sarcasm is just flavor
   text that moves no axes.

### 3. The rule: sarcasm weaponizes *warm* tones

What makes a tone sarcasm-compatible is **having a positive surface to
invert into a barb**. Sarcasm turns a nice thing mean. So the compatible
set is the warm tones, *not* the already-hostile ones (there's nothing to
invert in "trash talk" — sarcastic trash talk is just trash talk said
dryly).

| Tone | Surface | Sarcastic = | Compatible? |
|---|---|---|---|
| Props | respect | backhanded "nice play" | ✅ |
| Gracious (post) | congrats | fake-nice "wp" | ✅ |
| Flatter | praise | mocking flattery (obviously offensive) | ✅ |
| Humble (post) | self-deprecating | humblebrag ("just got lucky 😏") | ✅ |
| Commiserate (post) | sympathy | fake sympathy ("aw, tough beat 🙄") | ✅ |
| Banter | playful | basically just a needle | marginal — exclude for now |
| Trash talk / Salty / Needle / Gloat / Intimidate | hostile | nothing to invert | ❌ |

Reception of a sarcastic warm tone is **register-dominated** (the barb
matters more than the surface event), and it *widens* the temperament
split: an `energized` recipient is in on the joke (banter — likability up),
a `stung`/earnest one takes the bait (heat up, condescension). Sarcastic
`flatter` short-circuits the existing flattery vain/sees-through path — it
is unambiguously a barb (banter for energized, insult for the rest).

### 4. UX interaction: tone-primary, register-yields

Settled rules (the register groundwork below #1–#2 is already built; the
sarcastic value + gating is spec):

- **Layout:** target → tone ("Goal?") → delivery ("Delivery?", directly
  beneath) → suggestions → send. The reactive control sits under the thing
  it reacts to; cause→effect reads top-to-bottom. *(Built.)*
- **Per-tone memory:** delivery is remembered per tone (last-used). Picking
  a tone recalls how you last delivered it. *(Built for chill/spicy.)*
- **Tone is primary; the register yields.** The tone grid never dims or
  reshuffles. The delivery row reflects the selected tone: `chill`/`spicy`
  always present, `sarcastic` lights up **only on warm tones**.
- **Reverse order:** if `sarcastic` is active and you then pick a hostile
  tone, `sarcastic` **visibly reverts** to `spicy` (the existing default);
  the grid is untouched. Invalid combos are unreachable, nothing silent.
- **Compositional bonus:** because the register is stored *per tone*, a
  hostile tone can only ever store `chill`/`spicy` and a warm tone may
  store `sarcastic`, so recalling a tone's last-used register always lands
  on a valid value — gating and memory don't fight.

### 5. Redesigned tone palette

Today four mid-hand tones (`tilt`/`goad`/`needle`/`bait`) all collapse to
`TRASH_TALK`, differing only by multiplier — the "near-duplicate" rot.
With temperament now splitting *reception*, trade redundant hostiles for
distinct *intents* and let disposition + register carry the nuance.

**Mid-hand** (6 intents + bluff):

| Tone | Intent | Maps to | Sarcastic? |
|---|---|---|---|
| Trash talk | open hostility, tilt them | `TRASH_TALK` (full) | ❌ |
| Needle | sly provocation, bait a call/rise | `PROVOKE` *(new)* or `TRASH_TALK`-light | ❌ |
| Banter | playful ribbing, rapport | `FRIENDLY_BANTER` | ❌ (marginal) |
| Props | respect a specific play | `PROPS` | ✅ |
| Intimidate | project dominance, pressure a fold | reputation/demeanor hook *(or new)* | ❌ |
| Flatter | insincere ego-stroke | flattery path | ✅ |
| Bluff | talk about own hand | `None` (no axis) — keep | — |

**Post-round** (6 intents):

| Tone | Intent | Maps to | Sarcastic? |
|---|---|---|---|
| Gloat | rub in the win | `TAUNT_POST_WIN` | ❌ |
| Salty | complain about the beat | `TRASH_TALK` | ❌ |
| Gracious | tip the cap to the winner | `COMPLIMENT` | ✅ |
| Humble | downplay your own win | `FRIENDLY_BANTER` | ✅ (humblebrag) |
| Props | respect the specific play | `PROPS` | ✅ |
| Commiserate | console a loser you weren't in the pot with | `COMMISERATE` *(new)* | ❌ |

`Commiserate` is the genuinely missing color — today there's no way to be
warm toward someone *other than* the person who just beat you.

### 6. Reception numbers — the sarcasm transform

Sarcasm reception is register-dominated, so rather than a full table per
warm tone, define one **disposition-keyed sarcasm transform** on the
mirror side (the recipient's view of the sender), layered like the
existing `_TEMPERAMENT_MIRROR_OVERRIDES`. Starting calibration (tune from
play data), against the `[0,1]` axes (heat default 0, respect/likability
default 0.5):

| Disposition | heat | respect | likability | reading |
|---|---|---|---|---|
| `energized` | 0.00 | +0.05 | +0.05 | in on the joke; wit bonds (capped at sincere-props warmth — sarcasm never out-bonds sincerity) |
| `stoic` | +0.03 | 0.00 | −0.03 | reads the literal surface, mild suspicion |
| `stung` | +0.12 | −0.05 | −0.15 | takes the bait; backhand stings worse than an open jab (condescension → respect down too) |

Notes:
- `stung` likability hit (−0.15) matches the amplified plain-needle value;
  the added `respect −0.05` is what makes a *backhand* nastier than an open
  jab — being condescended to, not just insulted.
- `energized` is net-positive (likability + respect up) — a sarcastic spar
  with a wit you respect is *bonding*, slightly better socially than a
  sincere compliment.
- Applies uniformly to the sarcasm-compatible tones; the underlying event
  (`PROPS`/`COMPLIMENT`/flattery/…) is overridden on the mirror side when
  `register == sarcastic`. The **actor** side still uses the neutral
  `actor_shift` for the surface event (the sender's own feelings don't
  depend on how the target took it) — same asymmetry as the shipped
  trash-talk reception.
- **Calibration flags (from review):** (a) `stung` sarcastic-props nets a
  ~0.13 respect swing from baseline (loses the sincere `PROPS` +0.08, then
  −0.05) — that makes it the **second-sharpest single-event respect hit**
  after `STAKE_DEFAULTED`/`BAD_BEAT`. Intended (condescension cuts), but
  verify against play data before shipping. (b) The inherited actor side
  means sending a sarcastic backhanded `PROPS` raises the *sender's*
  respect for the target by +0.10 (the neutral `PROPS` actor shift). Likely
  fine — you only bother backhand-complimenting someone you grudgingly rate
  — but it's an inherited behavior to ratify, not an accident.

### 7. Build outline (when greenlit) — verified against code

Two fresh design-review passes traced the spec end-to-end. Key findings
baked in below: `intensity` transports as a free-form string with **no hop
rejecting `'sarcastic'`** — but **two hops silently no-op it** unless
extended (`INTENSITY_GUIDANCE` dict at `stats_routes.py:72` on the
generation side, and the reception transform, since
`temperament_adjusted_mirror_shift` only matches `TRASH_TALK`/`TAUNT_POST_WIN`
events today). The post-round chat surface **exists and is live**
(`react/.../mobile/MobileWinnerAnnouncement.tsx`, 5 tones, mounted in
`MobilePokerTable.tsx` only on showdown) but has **no register row** and
its route reads no intensity — so post-round sarcasm is additive plumbing,
not a retrofit.

Steps 1 and 2 are fully parallel; step 3 depends on step 1's transport;
step 5 is blocked on step 4 only if `commiserate` ships with it.

1. **Type + transport + frontend gating.** Add `'sarcastic'` to
   `ChatIntensity` (`react/.../types/chat.ts:61`) **and** the per-tone
   memory map's value type. Add the `sarcastic` button to the delivery row
   in `QuickChatSuggestions.tsx` (currently two hardcoded buttons at
   ~`:359`), gated to warm tones, with the revert-to-`spicy`-on-hostile
   behavior. No route/validation change needed — `game_routes.py:1951` and
   `stats_routes.py:320` both `dict.get` intensity with no allowlist.
2. **Reception (backend).** Add a `register: str = 'earnest'` param to
   `temperament_adjusted_mirror_shift` (`relationship_events.py`) + a
   `_SARCASM_MIRROR_OVERRIDES` table keyed on disposition (uniform across
   warm tones, per §6) — Option A, keeps shift logic in `relationship_events.py`.
   Thread `register` through `_temperament_mirror_override` →
   `dispatch_chat_relationship_event` (`chat_relationship.py`). Backward-
   compatible default means existing callers are untouched. Can build
   concurrently with step 1 (invisible until the FE sends `'sarcastic'`).
   - **Flattery sub-task (understated in earlier drafts):** `flatter` takes
     a *separate early-return path* (`_dispatch_flattery`, `chat_relationship.py:137`)
     that (a) never passes `mirror_shift_override` to its `record_event`
     call (~`:193`) and (b) uses `_classify_flattery_disposition`
     (vain/sees-through), **not** `_classify_social_disposition`. Sarcastic
     flatter must: take the `register`, **bypass** the flattery-disposition
     branch, fire the `'jab'` emotional stimulus (not `'flatter'`), and pass
     the sarcasm `mirror_shift_override` keyed on the *social* disposition.
3. **Generation flag.** Add a `'sarcastic'` key to `INTENSITY_GUIDANCE`
   (`stats_routes.py:72`, e.g. *"Dry, backhanded — sounds like a compliment,
   reads as a barb."*). The templates already interpolate
   `{intensity_guidance}`, so **no template change**. Starts after step 1.
4. **Palette.** Retire the `tilt`/`bait`→`TRASH_TALK` collapse. For
   `needle`→`PROVOKE` and `commiserate`→`COMMISERATE`: reuse-first
   (`TRASH_TALK`-light / `FRIENDLY_BANTER`) is genuinely free for `needle`
   (one-line `map_tone` change to promote later). But **`commiserate` is
   worth adding as a real event now** — its reuse substitute `FRIENDLY_BANTER`
   has no temperament override, so it'd land flat. A new `RelationshipEvent`
   costs: enum + `ACTOR_AXIS_SHIFTS` + `MIRROR_AXIS_SHIFTS` rows + (optional)
   temperament override + `_POST_ROUND_TONE_MAP` entry + `_stimulus_for_event`
   + the post-round route's **hardcoded `allowed_tones` allowlist**
   (`stats_routes.py:535` — the *only* tone allowlist in the codebase) + a
   new `post_round_commiserate` YAML template + tests. Decide `intimidate`
   as a tone vs a reputation/demeanor byproduct.
5. **Post-round register (more than a toggle).** `MobileWinnerAnnouncement.tsx`
   needs a register-row state + buttons; `getPostRoundChatSuggestions`
   (`api.ts`, currently sends only name+tone) + the post-round route
   (`stats_routes.py:501`, reads no intensity) need an intensity/register
   field; and a sarcastic guidance/template branch for post-round. Dispatch
   side needs nothing (`map_tone` already ignores intensity for post-round).
   Blocked on step 4 if `commiserate` is in scope.

**Out-of-scope notes the review flagged:** the socket `send_message`
handler (`game_routes.py:2382`) reads neither `tone` nor `intensity` and
never calls the dispatch — a dead zone, harmless today because player chat
uses the REST path, but don't route structured quick-chat through the
socket without fixing it. A pre-existing `tsc` type-narrowing on
`handleSendMessage` (`usePokerGame.ts:58` declares 1 param, impl has 4) is
unrelated to this work.

## Open questions / decisions

- Which trait keys temperament — reuse `attitude`/`aggression` or add a
  dedicated `social_temperament`? **Resolved for trash-talk reception:**
  derived from anchors via `_classify_social_disposition` (no JSON field).
  Sarcasm reuses the same disposition.
- **New events (`PROVOKE`, `COMMISERATE`) now, or reuse + promote later?**
  Each new event = new ACTOR/MIRROR rows (+ temperament overrides). Leaning
  reuse-first (`TRASH_TALK`-light / `FRIENDLY_BANTER`) to keep the first
  cut small, promote when reception needs to differ.
- **Is `intimidate` a tone or a byproduct** of the reputation/demeanor
  system (a feared villain's plain trash talk already intimidates)? Could
  be redundant.
- `W_SOCIAL` weight + heat-aggregation shape (sum vs mean, threshold) —
  tune so a mildly-resented winner still farms a bit but a table-wide
  pile-on pushes them out.
- Does the human player's heat toward an AI count into the aggregate?
  (Probably yes — the human is in the relationship graph.)

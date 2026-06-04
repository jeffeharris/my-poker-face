---
purpose: Plan for the "dream table" magic moment — ask a new player who they'd want to play with at intake, generate those personalities, and seat them at the home court Sal vouches them into
type: spec
created: 2026-06-04
last_updated: 2026-06-04
---

# The Dream Table — plan

> A new player wanders into the Lucky Stack, plays the tutorial, graduates — and
> the room Sal vouches them into already has **the people they said they'd want to
> play poker with** sitting at it. Generated on the fly, dropped into the live sim,
> theirs to play with and against.

## North star

The onboarding's emotional payoff isn't just "you graduated" — it's *"told you I'd
put your name in somewhere real, kid… and I pulled a couple strings. Look who's at
your table."* The player asked for nobody in particular and the game conjured the
exact table they daydreamed about. It's personalization, retention, and the best
possible first-real-hand, all in one beat.

## Placement (decided): the home court, not Scene-0

The guests sit at the **home court** — the room `fire_first_vouch` reveals at
graduation — **not** the Scene-0 tutorial table. Why:

- Scene-0 is a **rigged, scripted 3-hander** (you + Sal + Larry, fixed hole cards
  per hand). Dropping dream opponents into it either breaks the deck rig or makes
  them fold-bots through every teaching hand — the magic dies on contact with the
  lesson.
- The home court is a **real (non-rigged) sim table**, so the guests genuinely
  play. And arriving *as the graduation payoff* lands the emotional beat instead of
  stepping on the tutorial.

**Narrative seam:** the waitress *plants* the wish at intake; **Sal delivers it**
at graduation. The ask is forward-looking ("who'd you want at your table *someday*")
— which is also what makes an imperfect caricature forgivable (it's who the room
*imagines*, not a likeness).

**Latency cover:** the ask happens at intake, generation kicks off immediately and
runs **in the background while the ~10-hand tutorial plays**, so the guests are
warm and seated by the time the player graduates. The slow part hides behind the
part that's already happening.

## The hard part: how the player enters the info

This is the crux (everything else is wiring existing parts). Goals, in tension:
in-world and low-friction, but constrained enough that the LLM rarely botches it,
and **always skippable** (the feature is a bonus, never a gate).

### Recommended: 1–2 discrete, optional name fields, framed in-world

A new beat in the existing waitress intake (`LuckyStackIntake`), after the
name + "tell me something about yourself" beats:

> *She tops off your coffee.* "One more thing, hon. We get all kinds through here.
> If you could pull up a chair with anybody — a legend, an old friend, some rival
> you'd love to bust — who'd it be? I'll see who's around."

- **Two optional inputs**, each one name (placeholder: *"a legend, a friend, a
  rival…"*). Not one free-text blob — discrete fields mean **trivial parsing**
  (field = name, no name-extraction LLM = one less botch surface) and a **hard cap
  of 2**.
- **Skip is one tap** ("Just deal me in") → no guests, ordinary home court. Empty
  fields are the common, fully-supported case.
- **Forgiveness is baked into the copy**: "I'll see who's around" / "the room's
  own version of 'em" frames the result as the house's *impression*, so a rough
  caricature reads as charm, not failure.

### Alternative (richer, riskier): one free-text "anyone at all" line

A single evocative line ("…anyone at all — who's pulling up a chair?") is more
magical but needs an **extraction LLM** to pull names out of prose, invites
"anyone at all" abuse, and widens the botch surface. **Defer** unless the
two-field version feels too form-like in playtest.

### Who/when, summarized

| Beat | Where | What |
|---|---|---|
| Ask | Intake (waitress) | up to 2 optional names |
| Generate | Background, during the tutorial | `bulk_generate` the names → playable personas |
| Deliver | Graduation (`fire_first_vouch`) | seat the ready guests at the home court; Sal's line nods to it |

## Data flow

1. **Intake capture** — `POST /api/cash/intake` (`cash_intake_route`) takes an
   optional `guests: [str, str]`. Sanitize (trim, length-cap, drop empties +
   reserved/junk names via the existing guard), store on `CareerProgress` as
   `requested_guests`.
2. **Background generation** — right after intake completes, kick off
   `PersonalityGenerator.bulk_generate(requested_guests)` (Assistant tier) off the
   request thread (the tutorial is playing). Persist the resulting persona_ids to
   `CareerProgress.guest_persona_ids`. On failure per-name, fall back to a
   competent generic wearing the requested name (`_create_default_personality`).
3. **Seat at the home court** — in/after `fire_first_vouch`, once a home court is
   chosen, seat the generated guests into that table's `seats_json` (sandbox-
   scoped), leaving the human seat + room for Sal's mentor stake. If generation
   isn't finished by graduation, seat whoever's ready and fill the rest the normal
   way (never block the vouch).
4. **Sal's line** — when guests were seated, swap the graduation/mentor-intro beat
   for the variant that nods to them ("pulled a couple strings — look who's here").

## Generation (reuse, don't build)

- `poker/personality_generator.py` → `get_personality(name, description)` /
  `bulk_generate(names)` already turns a name into a full playable persona
  (play_style, traits, anchors, skill tier) with `_create_default_personality`
  fallback and the `RESERVED_PERSONA_NAMES` zombie guard.
- Reuse the intake LLM discipline already in `generate_intake_persona`: Assistant
  tier, strict-JSON, PG-13 system prompt, canned fallback, prompt-template tag.
- **v1 skips avatars** (text + default art) — avatar gen is the slowest, riskiest
  call; add it later behind the same flag.

## Persistence hygiene (the dangerous part)

Generated guests must **not** leak into the global roster — this codebase has a
documented zombie-persona class (junk personas reappearing) and a
`circulating`/`visibility` split exactly for this.

- Create guests **sandbox-scoped** and **`circulating = False`** (a "guest" cohort,
  visible at the player's table but never auto-seeded into other sandboxes / the
  eligible pool).
- Seed their bankrolls via the established casino-fish-as-personas path (pool-
  funded, conservation-safe — no minting).
- Lean on `_is_reserved_persona_name` to reject junk/empty names before any DB
  write.

## Risk & mitigations

**Framing first: this is a private, single-player sandbox.** Whatever gets
generated is seated in the requester's *own* game, for their eyes only — nothing
is published or shown to other players. That collapses most of the "anything could
be entered" worry: ask for **"Spiderman"** and you get the room's Spiderman-at-the-
felt caricature, which is the *charm*, not a botch (the game already runs
fictional/celebrity personas). "The system generates what it generates" is the
right posture when the audience is the one person who asked. So the real residual
risk is **not** content — it's **containment** (don't let a private guest leak into
anyone else's game) plus graceful failure.

| Risk | Severity (private sandbox) | Mitigation |
|---|---|---|
| **Roster pollution** — guest leaks into other sandboxes / the global pool | **high — the one that matters** | sandbox-scoped + `circulating=False`; reserved-name guard; the zombie-persona class is the real hazard here |
| Generation fails / low quality | low — a caricature is on-brand | `_create_default_personality` fallback wearing the requested name |
| Latency | low | background gen under cover of the tutorial; seat-on-ready, never block graduation |
| Prompt injection | low | name is **data**, never instructions (intake already does this); strict-JSON output |
| Offensive / abusive input | low — self-requested + private | provider-default safety + the PG-13 system prompt; **no custom moderation gate in v1** |
| Real/private person entered | low — self-requested + private | frame as "the room's *impression* of who you asked for" (a caricature, not a likeness claim); defer avatars anyway |

The earlier draft proposed a custom moderation gate — **dropped for v1**: it's
unwarranted for a private, self-requested experience. Revisit only if guests ever
become shareable/visible to other players (then content moderation re-enters).

## Flag gating

Behind a single flag (e.g. `DREAM_TABLE_ENABLED`, default OFF) covering the intake
question, the generation, and the seating — so it ships dark and flips on after a
live playtest, and a custom (non-career) sandbox never engages it.

## v1 slice vs deferred

**v1:** the two-field intake ask → `bulk_generate` (text only) during the tutorial
→ seat ready guests at the home court → Sal's nodding line. Flag-gated, non-
circulating, fallback-guarded.

**Deferred:** avatar generation; the free-text "anyone at all" entry; richer Sal
dialogue keyed to *who* showed up; guests persisting across sessions / climbing the
circuit with you; a "your table" re-summon later in the career.

## File pointers

- **Intake UI** — `react/.../components/cash/LuckyStackIntake.tsx` (add the guest
  beat + 1–2 optional inputs + skip).
- **Intake API** — `flask_app/routes/cash_routes.py` `cash_intake_route`
  (`/api/cash/intake`): accept + sanitize `guests`, store on career_progress,
  kick off generation.
- **Career state** — `poker/repositories/career_progress_repository.py`: add
  `requested_guests` + `guest_persona_ids` (JSON, round-trips like the existing
  intake fields).
- **Generation** — `poker/personality_generator.py` (`bulk_generate`); a thin
  helper in `cash_mode/career_progression.py` to generate + persist guest ids.
- **Seating** — `cash_mode/career_progression.py` `fire_first_vouch` (seat guests
  into the chosen home court's `seats_json`); reuse `cash_mode/tables.py` slot
  helpers + the casino-fish bankroll seed path.
- **Sal's line** — the graduation / `mentor_intro` beat (career_scene
  `SAL_GRADUATION_SEQUENCE` / the lobby handoff) gets a guests-present variant.

## Open questions

- **Two fields vs one** — start with two discrete fields (recommended); revisit a
  single evocative free-text line only if it reads too form-like.
- **How many seats** — 1–2 guests + you + (Sal's stake?) leaves the rest of the
  home court to the normal world fill. Confirm the home-court size and how many
  seats to reserve for guests.
- **Generation trigger** — fire at intake-complete (max latency cover) vs lazily on
  graduation. Intake-complete is better unless it complicates the request path.
- ~~Moderation depth~~ — **decided:** no custom gate for v1 (private, self-
  requested sandbox); provider-default safety + PG-13 prompt + reserved-name guard.
  Revisit only if guests ever become shareable to other players.
- **Persistence beyond onboarding** — do guests stick around in the sandbox after
  the first session, or are they a one-time welcome cohort? (v1: they persist as
  non-circulating sandbox personas; revisit climbing-with-you later.)

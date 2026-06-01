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
> `flask_app/handlers/chat_relationship.py`. Still **deferred**: the
> near-term aggregate-heat leave-pressure term, the **hog disposition**
> (needs that movement term first), and the `SARCASM` quickchat tone.

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

## Quickchats

Today's quickchat tones map to `RelationshipEvent`s in
`flask_app/handlers/chat_relationship.py` (`TRASH_TALK`, `COMPLIMENT`,
`TAUNT_POST_WIN`, `FRIENDLY_BANTER`). The suggestion set
(`react/.../chat/QuickChatSuggestions/`) reportedly has near-duplicate
copies of the same intent.

- Introduce a **`SARCASM`** tone (and possibly 1–2 others, e.g. a dry
  "appreciative needle") whose *reception* is temperament-dependent —
  lands as banter (likability) for some, as an insult (heat) for others.
- **Replace** the duplicate quickchats with these nuanced ones rather than
  growing the list — net-neutral count, higher expressive range.

## Open questions

- Which trait keys temperament — reuse `attitude`/`aggression` or add a
  dedicated `social_temperament`? (Leaning dedicated, defaulted from
  existing traits so personalities.json doesn't need a mass edit.)
- `W_SOCIAL` weight + heat-aggregation shape (sum vs mean, threshold) —
  tune so a mildly-resented winner still farms a bit but a table-wide
  pile-on pushes them out.
- Does the human player's heat toward an AI count into the aggregate?
  (Probably yes — the human is in the relationship graph.)

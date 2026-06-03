---
purpose: What docs/technical/ is for, what deserves a doc, and how we keep these docs honest about staleness
type: guide
created: 2026-06-03
last_updated: 2026-06-03
---

# docs/technical/

Technical reference for the *systems* in this codebase — the architecture,
invariants, formulas, and "why it works this way" that you can't reconstruct by
reading one file. This is the map; the code is the territory.

For doc-level guidance to Claude (rules when editing/creating docs here), see
[`CLAUDE.md`](CLAUDE.md). For the live list of stale docs and gaps, see
[`TODO.md`](TODO.md). Project-wide doc standards live in the root `CLAUDE.md`.

## What belongs here

A doc earns its place when it captures something **non-obvious and durable**:

- **System architecture** — how components fit, data flow, the seams between layers.
- **Invariants & contracts** — chip conservation, seat occupancy, ordering rules,
  things that must stay true and aren't visible from one function.
- **Formulas & models** — psychology anchors, EV/equity math, economy levers,
  lookup-table provenance. The "magic numbers" and where they come from.
- **Reference surfaces** — config field catalogs, event catalogs, route maps —
  where an index saves everyone from grepping.

What does **not** belong here:

- **Restating the code.** If a doc just narrates what a function literally does,
  it will rot the moment the function changes and add nothing in the meantime.
- **Handoffs, progress notes, changelogs.** These are point-in-time artifacts.
  Write them in `docs/captains-log/` or a plan doc; when they go cold, archive
  them — don't pretend they're living reference. (See the archive set in `TODO.md`.)
- **Feature ideas / vision.** Those go in `docs/vision/`.
- **Open issues / tech debt.** Those go in `docs/TRIAGE.md` (the canonical tracker).

## Doc types (header `type:` field)

Every `.md` here MUST carry the YAML header from the root `CLAUDE.md` standard
(`purpose` / `type` / `created` / `last_updated`). Pick the type by intent:

| Type | For |
|------|-----|
| `architecture` | System structure, component relationships, data flow |
| `design` | Decisions, trade-offs, "why we do it this way" |
| `spec` | Formulas, algorithms, models, detailed mechanics |
| `reference` | Catalogs, schemas, field/event/route indexes |
| `guide` | How-to, setup, runbooks (this file) |

## How we think about coverage

We do **not** chase one-doc-per-module. Coverage means *the systems a newcomer (or
future-you) would otherwise have to reverse-engineer are mapped, and those maps are
true.* A small set of accurate, falsifiable docs beats a large set of plausible-
sounding rot.

Three principles:

1. **A doc must be falsifiable.** Cite concrete `file:line`, class, constant, and
   formula names. That's what lets the next reader (or an audit) *check* the doc
   against code instead of trusting it. The exemplary docs here
   (`FISH_BOT_SYSTEM.md`, `CASH_MODE_WEALTH_LEVERS.md`) name every constant — which
   is exactly why they're verifiably current.

2. **Document the "why," lean on code for the "what."** Line-by-line "what" rots
   fastest. Architecture, invariants, and rationale age slowly. When you must cite
   line numbers, expect them to drift and treat a re-verify as part of any edit.

3. **When a system ships, the doc is part of "done."** Either write/update its doc,
   or log the gap in `TODO.md`. A shipped system with no doc and no gap entry is the
   failure mode that produced the current backlog (chip-custody, the presence
   machine, backing/staking all shipped undocumented).

## Staleness model

`last_updated` reflects the last **content** change, not the last time the file was
touched. Do not bump it to today just because you added a header or fixed a typo —
that falsely signals currency. A doc whose claims you haven't re-verified against
code is stale regardless of its date.

The lifecycle we expect:

```
fresh ──(code moves on)──> stale ──(refresh or)──> archived
```

- **Fresh** — claims verified against current code.
- **Stale** — drifted; tracked in `TODO.md` with the code evidence that makes it wrong.
- **Archived** — historical artifact (handoff, abandoned design, old changelog).
  Move to `docs/archive/` rather than maintaining a fiction. Keep honestly-labeled
  historical records (e.g. `HYBRID_V2_ARCHITECTURE.md` says so up front) in place.

## The TODO tracker

[`TODO.md`](TODO.md) is the standing audit: per-doc staleness (with severity and
`file:line` evidence), header compliance, and the gap list of undocumented systems.
Run a fresh survey when the codebase has moved a lot; update the tracker, don't let
it go stale itself. It is *doc-debt* tracking — distinct from `docs/TRIAGE.md`, which
tracks *code* debt.

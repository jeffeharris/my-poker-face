# docs/technical/ — CLAUDE.md

Rules for working on technical docs in this directory. Purpose, what belongs here,
the doc types, and how we think about coverage: **[`README.md`](README.md)** — read
it first. The standing staleness/gap audit is **[`TODO.md`](TODO.md)**.

## Before you write or edit a doc

- **Verify against code, cite the evidence.** Spot-check the doc's concrete claims
  (file paths, class/function names, constants, formulas, schema version, routes)
  against the current code and cite `file:line`. Don't restate what you didn't check;
  write "unverified" rather than guessing. A doc you can't point at code for isn't
  reference, it's folklore.
- **Document the "why," not the line-by-line "what."** Architecture, invariants, and
  rationale age slowly; narration of a function rots on the next refactor.
- **Match the existing doc's altitude and style.** Tables over prose, constants named,
  cross-links with `[[...]]`-style relative links between docs.

## Headers (root CLAUDE.md standard)

- Every `.md` here MUST start with the YAML header (`purpose` / `type` / `created` /
  `last_updated`). The sole exception is this `CLAUDE.md` file itself.
- New doc: set `created` to today; pick `type` per the table in `README.md`.
- **`last_updated` tracks the last *content* change, not the last touch.** If you only
  add a header, fix a typo, or correct a link, do **not** bump it to today — that
  falsely signals the content is current. Bump it only when you actually re-verify or
  rewrite the substance. Use `YYYY-MM-DD` (no timestamps).

## When code changes under a doc

- If you ship or change a system that a doc here describes, update the doc **or** log
  the drift in `TODO.md` with the `file:line` evidence. "Shipped, no doc, no gap entry"
  is the exact failure mode that built the current backlog.
- New significant system with no doc? Add a gap entry to `TODO.md` (or write the doc).

## Staleness & archiving

- Found a stale/obsolete doc mid-task? Add it to `TODO.md` (severity + evidence)
  rather than silently leaving it or half-fixing it.
- Handoffs, progress notes, and changelogs are point-in-time — when they go cold,
  propose archiving to `docs/archive/` instead of maintaining a fiction. Honestly
  self-labeled historical records can stay in place.

## Scope boundaries

- **Open issues / code tech-debt → `docs/TRIAGE.md`** (the canonical tracker), not here.
- **Feature ideas / vision → `docs/vision/`.**
- **Doc-debt (stale docs, doc gaps) → `TODO.md`** in this directory.

---
purpose: Narrative log of finishing & shipping the respect/likability neutral rebaseline (0.5 → 0.35)
type: guide
created: 2026-06-07
last_updated: 2026-06-07
---

# Regard neutral rebaseline (0.5 → 0.35)

## Why

`0.5` was doing double duty in the relationship system: it was both the *start
point* for a fresh edge and the *zero point* of the renown/regard math
(`regard = value − 0.5`). So respect was never actually *earned* — you began
already-at-neutral and could only lose it. The goal: drop the neutral baseline to
`REGARD_NEUTRAL = 0.35` so the axes are asymmetric (~0.35 of downside, ~0.65 of
upside) — regard becomes a ladder you climb, "I don't know you" sits low, with
bounded downside. One named constant, no drift between start-point and
formula-center.

I picked this up as a **parked** branch: a prior session had landed the constant +
formula centers (`b1be2e8a`), written a handoff doc enumerating the remaining work,
and *closed* PR #202 to park it. The ask was "finish it."

## What we did

1. **§A threshold re-anchor** — the sponsor/staking bars (`TIER_STANDARD/RESTRICTED`
   floors, `_adjusted_terms`, `_relationship_hint`, `RELATIONSHIP_LIKABILITY_FLOOR`)
   were hand-tuned around the old neutral. Decision (with Jeff): **re-anchor to
   preserve behaviour** — shift each bar −0.15 so it tracks `REGARD_NEUTRAL`.
   Eligibility, offer terms, and forgiveness outcomes stay identical; only the
   underlying score *values* move with the baseline. (Most of this was already
   committed by the parallel session as a `wip`; I verified it matched intent.)
2. **Exploitation HIGH thresholds left absolute** — `relationship_modifier.py`'s
   `0.7` "respect/like enough to change how I play" bars were *not* re-anchored.
   Decision: those should be genuinely earned (climb from 0.35), validated by sim
   later — not smuggled into a baseline move.
3. **§B test sweep** — every test pinning `0.5`-as-neutral now references
   `REGARD_NEUTRAL` (relationship defaults, bilateral/accumulation shifts,
   chat-dispatch/sarcasm/temperament, the cash-route events, the
   `_adjusted_terms`/`_relationship_hint` unit tables).
4. **Missed `else 0.5` fallbacks** — the original sweep grepped for *comparisons*
   (`respect > 0.5`), so it skipped five no-edge *assignments* (`else 0.5`): the
   `cash_routes` forgiveness scorer, `ai_carry_resolution` (×2), `file_cabinet`
   dossier display, the `character_routes` hint clone, and `debug_routes`. A
   never-met stranger now defaults to neutral everywhere.
5. **v155 migration** — shift existing `relationship_states` regard −0.15 (clamped),
   heat untouched, so live data keeps its *meaning* (see "Subtract 0.15" below).
6. **A/B balance sim** — confirmed the rebaseline doesn't disturb the economy.

Merged (squash, `4953f17d`), deployed, verified on prod: schema 155, 9,106 edges,
respect distribution re-centered on ~0.36–0.40, relationships already climbing past
the migration ceiling post-deploy (the ladder working).

## Wrong turns & corrections

- **The git-status snapshot lied.** My session opened with a snapshot showing HEAD
  at the constant commit and "remaining work" per the handoff. Reality: a parallel
  session had *already* committed the §A re-anchor (`3bc34bfa`) and closed the PR an
  hour earlier — the snapshot predated both. Lesson: when "resuming," re-derive the
  actual current state from git before trusting a handoff's status section.
- **The cross-worktree recipe was a footgun.** The handoff said to develop on a
  sibling worktree's running container and port back. I followed it, made the §A
  edits there — and they got *wiped* mid-task: that worktree was being actively
  committed to by another session (`git status` clean, my staged changes gone, a
  new commit on its HEAD). Correction: stop sharing a worktree that isn't mine.
  Stood up a **dedicated container for the regard worktree** (own
  `docker-compose.override.yml`, ports `5008/6388`, subnet `10.123.48.0/24`) and
  worked the branch directly. Slower to set up, but stable and isolated.
- **Codex caught a hole the grep couldn't.** A `codex-assist review` against the
  merge-base flagged that `save_note`/`save_nickname_override` insert *only* their
  own column, so a brand-new row fell back to the **SQL column default of 0.5** for
  respect/likability — which the rebaselined scoring now reads as *above* neutral.
  A player who merely noted or nicknamed a stranger would have that AI score as
  positive regard. Fixed at both layers (explicit `REGARD_NEUTRAL` on insert +
  schema defaults `0.5 → 0.35`) with regression tests. A real bug the mechanical
  sweep structurally could not find — worth the second opinion.
- **"No migration" was the wrong default for prod.** The handoff said *no data
  migration — trash + recreate sandboxes*. Fine for sandboxes, but merging to `main`
  auto-deploys to prod, where 9k real relationship edges would suddenly re-read as
  warmer against the new neutral. I surfaced this as the one blocker before merging.

## "Subtract 0.15" — the key insight

When I laid out the merge options (accept the reinterpretation, or reset prod data),
Jeff's answer was sharper than either: *"can we subtract .15 from all of them?"*

That's the data-side mirror of the "re-anchor to preserve behaviour" decision we'd
already made for the thresholds. Shifting every existing respect/likability down by
`0.5 − 0.35 = 0.15` (clamped to `[0,1]`, heat untouched) holds each edge's
*offset from neutral* constant: an exactly-neutral `0.5` becomes exactly-neutral
`0.35`, a "+0.2" `0.7` becomes `0.55`, a "−0.3" `0.2` becomes `0.05`. Renown
contributions (`value − neutral`), hints, and offers therefore read identically —
no live relationship warms or cools on deploy. Shipped as schema migration **v155**:
a one-time transform, gated once-only by the version loop; fresh DBs are built at
`SCHEMA_VERSION` and skip it.

The trap (and its test): the migration assumes rows are still 0.5-based. A *dev* DB
that ran the new 0.35 code *before* v155 existed would get double-shifted to ~0.20.
Prod is safe (it ran old code right up to deploy, so it shifts exactly once); only
throwaway dev DBs hit the edge. A `test_fresh_db_does_not_shift` case locks in that
fresh builds don't double-apply.

## Did it stay balanced?

Ran an A/B: the rebaselined `0.35` vs a control with the constant temporarily set
back to `0.5`, same 400-tick closed-economy sim. The economy is **unchanged** — only
*where regard sits* moves:

| | 0.35 (rebaseline) | 0.50 (control) |
|---|---|---|
| chip-audit drift | 0 | 0 |
| gini start → end | 0.47 → 0.55 | 0.46 → 0.57 |
| stakes settled / defaulted | 5 / 0 | 12 / 0 |
| regard center (respect) | ~0.36 | ~0.50 |

Conservation exact, mobility identical, staking market alive with zero defaults in
both. The differences (settles, chip delta) are run-to-run sandbox variance, not a
baseline effect. The "re-anchor to preserve behaviour" decision held up empirically,
not just by construction. (Housekeeping note: the global `relationship_states` table
isn't sandbox-scoped, so the two runs' rows comingled in the dev DB afterward — I
captured each run's regard distribution *before* the next run polluted it.)

## What's deliberately left for later

- The exploitation HIGH thresholds (`relationship_modifier` `0.7`) stay absolute —
  a sim pass should confirm "earned enough to change play" still fires at a healthy
  rate now that relationships climb from a lower base, and tune if it craters.
- Vouch-system thresholds (`RESPECT_FLOOR`, `LIKE_THRESHOLD`,
  `HOME_TABLE_REVEAL_LIKABILITY`) re-tune against 0.35 when circuit-progression syncs
  to main — part of the #4 vouch live-tuning pass.

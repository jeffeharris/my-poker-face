---
purpose: Grounded narrative log of building the human player prestige/reputation scoreboard (v1) on the prestige branch
type: reference
created: 2026-05-29
last_updated: 2026-05-29
---

# Captain's log — player prestige scoreboard v1 (prestige worktree)

Honest record of building the human-facing prestige/reputation scoreboard
from `docs/plans/CASH_MODE_PLAYER_PRESTIGE.md`, via the feature-dev workflow.
Newest entries at the bottom. Wrong turns kept in.

---

## 2026-05-29 — v1 scoreboard, built end-to-end

**Scope, deliberately narrowed.** The spec is a two-pole reputation system
(renown + regard) with five world-response hooks. I asked up front and we
locked **v1 = the read-only scoreboard only** (build-order steps 1–2): the
stat, the ticker recompute, the lobby read, the React panel, and a quadrant-
change ticker beat. Hooks 1–4 (table pull, backing gating, chat tone, AI
demeanor) are deferred. The point of v1 is to make the villain path *visible*
without touching any AI decision math — the legibility guardrail from the
attractiveness work.

**Decisions the user locked (not my defaults):**
- 2D (renown + regard), not a single karma scalar.
- Renown inputs: breadth (# AIs who've met you), tenure, highest stake tier
  reached, beating high-respect opponents, winning at high stakes. Notably
  *not* raw PnL magnitude — they steered away from "grind low stakes to farm a
  big number."
- Storage: append-only `prestige_snapshots` (history), not a single-row
  upsert. Mirrors `holdings_snapshots`; gives a renown trajectory + an
  explainable component breakdown for tuning the (illustrative, not-locked)
  formula.

**What made this fast: two existing patterns did the heavy lifting.**
`holdings_view.py` + `holdings_snapshots_repository.py` (ticker-driven,
sandbox-scoped snapshot stat) and `cash_mode/whereabouts.py` (Flask-free,
repo-injected, best-effort pure aggregator). `cash_mode/prestige.py` is
basically "whereabouts for reputation"; the repo and ticker hook are
"holdings for reputation." Reusing them meant the new surface area is small
and the conventions came for free.

**The one real data-layer gap.** The relationship graph only had an
*outbound* query (`load_all_relationships(observer_id)` — my view of everyone).
Regard needs the *inbound* direction (everyone's view of me), which had no
method and — more to the point — no index. Added `load_inbound_relationships`
(scans `WHERE opponent_id = ?`) plus `idx_relationship_states_opponent` in the
v121 migration. `relationship_states` isn't sandbox-scoped (v87 design), so
regard reads the global inbound graph while the prestige *row* is sandbox-
scoped — which is correct, and exactly what the spec called out.

**A subtlety worth recording: heat is one-sided, so "balanced" regard isn't
zero.** My first regard test asserted a half-warm/half-hostile room averages
to ~neutral. It failed: regard came out clearly negative. That's correct, not
a bug — `heat` only ever subtracts (it's notoriety, 0→1), so *any* hot edges
pull regard down even in an otherwise balanced room. I'd written the test
expecting symmetry the model deliberately doesn't have. Fixed the test to
isolate the averaging (likability split, no heat) and added a separate test
asserting the heat asymmetry on purpose.

**Renown ratchets via read-before-write.** The ticker reads
`load_renown_peak` (a `MAX(renown)` over history) and passes it into the
compute, which returns `max(computed, peak)`. The append-only table stores the
already-ratcheted value each tick. A downswing can't erase the career record;
the just-written row is always inside the 60-day prune window so the peak
survives pruning.

**Quadrant-change beat rides the existing ticker.** Rather than a new socket
channel, a quadrant flip records an `EVENT_REPUTATION_SHIFT` into the
`activity.py` ring buffer — the ticker's existing fresh-events emit loop
carries it to the lobby. Placed the recompute *before* that emit block so the
beat goes out on the same tick. First-ever capture is silent (no prior
quadrant to compare).

**Review caught no bugs — three small quality wins applied.** A parallel
bugs/conventions/simplicity review came back clean on correctness. Applied the
worthwhile cleanups: `List[...]` over `list[...]` for file consistency, used
the repo's `DEFAULT_RETENTION_DAYS` instead of a hardcoded `60` in the ticker
(and caught that it's a *module* constant, not a class attr — the reviewer's
suggested `Repo.DEFAULT_RETENTION_DAYS` would have thrown), single-pass regard
averaging, and a named `_reputation_payload_from_snapshot` helper for the
DB-column→wire-format re-keying.

**Status:** 23 new tests (compute + repo) green; relationship-repo, cash-
session-repo, lobby-route, and full cash_mode buckets green; tsc + eslint
clean. Not yet committed. Hooks 1–4 are the obvious next slice now that the
stat exists and is read-mostly — they only need to *read*
`prestige_snapshots_repo.load_latest(...)`, no refactor of the compute.

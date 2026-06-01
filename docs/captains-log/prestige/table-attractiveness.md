---
purpose: Grounded narrative log of building cash-mode table attractiveness v1 (branch prestige)
type: reference
created: 2026-05-29
last_updated: 2026-05-29
---

# Captain's log — table attractiveness v1 (prestige worktree)

Honest record of building the AI table-attractiveness/room-prestige feature
from `docs/plans/CASH_MODE_TABLE_ATTRACTIVENESS.md`. Newest entries at the
bottom. Wrong turns and corrections kept in, not just the wins.

---

## 2026-05-29 — finishing the spec, then building it

**The 4.7 bug and a false corruption scare.** Picked up a spec-refinement
session that an Opus 4.7 thinking-block API bug had cut off mid-edit. The prior
turn had panicked that the doc was corrupted — "533 null bytes." That was a
measurement artifact: `grep -c $'\x00'` in bash strips the null, so it becomes
`grep -c ''` and matches all 533 *lines*. A real check (`tr -d '\000'` byte
count, `file`) confirmed zero null bytes, clean UTF-8. Lesson: verify the
instrument before trusting the alarm. The restructure was actually ~90% done;
finished reconciling six stale "weighted roll" references to the agreed greedy
selection and confirmed no v119-migration self-contradiction.

**Sync.** `prestige` was 43 commits behind `origin/development` and — usefully
— a strict *ancestor* (0 unique commits), so a stash-ff-pop was a clean
fast-forward. The 43 commits had reworked the exact seating files (`lobby.py`
+424 lines of session-lifecycle hardening), which mattered later. Pleasant
surprise: the spec's line-number anchors mostly survived the churn.

**Phase A — pure scoring (`cash_mode/attractiveness.py`).** Built the scoring
math as a dedicated pure module rather than scattering it across
closed_economy/bankroll/movement as the doc's touch-points sketched — cohesion
and testability, and it matches the codebase's own `aspiration.py` /
`stakes_ladder.py` pattern. Two deliberate choices worth flagging: chips
normalized to *stacks* (÷ table max buy-in) instead of the spec's raw sums
(scale-stable across stakes), and `wealth()` zeroes at the $2 min buy-in, not
some arbitrary "broke." 28 property tests — and I had to correct two of my own
over-specified assertions (they pinned constant-dependent specifics instead of
structural truths; the implementation was right).

**Phase B — leave-pressure, and the deadzone I didn't plan for.** Wired the
wealth-driven `stake_up`, the `dead`-table term, and the prestige retention
override. The instructive miss: my first wealth-climb term was continuous from
zero, so *any* over-rolled AI got a tiny perpetual leave chance. That surfaced
as `StopIteration` in two unrelated tests — they budget a fixed rng sequence,
and the tiny pressure flipped `total <= 0 → stay` (no roll) into a roll. The
real problem wasn't the tests; it was churn: a healthy grinder at its own tier
would re-shop tables constantly. Fix was a `SLUM_DEADZONE` — the climb only
fires for the *genuinely* slumming rich (300k at $50), which restored the
fast-path and fixed both tests untouched. Only the two vice-on-leave tests
needed editing (their 900k-at-a-$10-table "whale" now correctly climbs).

**Codex caught two real bugs.** Ran `codex-assist` in parallel over the
committed Phase A/B. It found: (1) the retention override released a rich
predator *sideways* (`bored_move` stayed `bored_move`) instead of up — sharp
because `SLUM_DEADZONE == PRESTIGE_RETENTION_OVERRIDE == 20`, so the climb
pressure is exactly zero at the release point; fixed by converting that
boredom drift to `stake_up`. (2) the hunger multiplier ignored whale-only
tables (only gated on `fish_chips`), so hungry grinders weren't pulled to
whales. Both fixed + committed. Worth the second opinion.

**Phase C exploration — the tension the spec hid.** The spec describes a clean
"global pre-pass over the idle pool." The reality: `refresh_unseated_tables` is
**table-sequential with per-table sim bursts** — fill is interleaved *inside*
each table's burst, with `seated_globally` + in-memory idle-pool pruning as the
only cross-table coordination. A truly global greedy pass wants all open seats
at once, which fights that structure. Codex independently confirmed the seam
(`refresh_table_roster(enable_live_fill=False)` + a post-Step-1 global fill
modeled on `_process_aspiration_asks`) and pinned the conservation/ghost-seat
invariants.

**The A/B decision.** Two ways in: (A) split the per-table loop from the
persistence/event loop so the global fill mutates results consumed by the
existing persistence (Codex's preference, DRY) — or (B) leave the loop alone and
run a contained global fill pass *after* it with its own small persistence.
Reading the whole function settled it: `refresh_unseated_tables` is a
~1,715-line god-function with a ~890-line per-table loop body of inline,
ordering-coupled side-effect stages (settlement → transfers → creations →
events). Option A would force me to partially refactor that freshly-hardened
monolith just to land a seating feature — exactly the entanglement to avoid.
**Chose B**: quarantine the change. Logged the god-function as its own P1 tech
debt (TRIAGE T2-75) rather than smuggling the refactor in here.

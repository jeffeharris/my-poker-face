---
purpose: Grounded narrative log of de-risking Renown v2 balance via an offline scorer before committing to the build (renown worktree)
type: reference
created: 2026-06-01
last_updated: 2026-06-01
---

# Captain's log — Renown v2 balance validation (renown worktree)

Honest record of scoping Renown v2 (the uncapped redesign in
`docs/plans/CASH_MODE_PLAYER_PRESTIGE.md`) and validating its *balance* before
writing any production code. Newest entries at the bottom. Wrong turns kept in.

---

## 2026-06-01 — finding the doc, scoping the work

Jeff remembered discussing an uncapped renown system "last week" and thought
there was a doc, but couldn't find it. There wasn't a *new* doc — the redesign
had been appended as a section ("Renown v2 — uncapped, continuous,
achievement-aligned") inside the existing `CASH_MODE_PLAYER_PRESTIGE.md` on
2026-05-29 (commits `21b69cd6`, `644139a5`). That's why it was hard to find:
not a standalone file. Lesson for future me — grep commit subjects, not just
filenames.

Scoped the build. Key facts established by reading v1 end-to-end:
- v1 (`cash_mode/prestige.py`) is **human-only**, renown capped `[0,1]`, a pure
  re-derivation from current DB state on the world ticker.
- The scalp tracker (`cash_scalps`) the v2 scalp driver depends on **does not
  exist** — schema is at v131, the scalp doc still assumes v123.
- `compute_prestige` is **only ever called for the human** — there is no
  field-wide renown anywhere. That's the real keystone (and the biggest risk):
  uncapping forces "high renown = relative to the field", which forces renown
  to be computed for every entity.

Three structural cascades, not just "delete the caps": (1) uncap → relative
quadrant → field-wide compute; (2) AI-symmetry is a real input refactor
(cash_sessions are human-only); (3) "lifetime ledger + every hand moves the
needle" contradicts v1's stateless projection — scalps and legendary nuggets
are *events*, so v2 needs hybrid storage.

## 2026-06-01 — the insight that makes cheap validation possible

Jeff asked the right question: how do we validate balance *before* committing
to the work? The unlock: **renown is a read-side projection.** Nothing about
*scoring* needs the migration, ticker, hooks, or UI. So the whole formula can
be validated offline — and re-scoring one frozen sim log under formula A vs B
is a *perfectly paired* comparison (no RNG desync, unlike the same-seed re-run
trap from the cash-sim A/B work). Wrote that up as a four-rung ladder and built
Rung 1.

## 2026-06-01 — Rung 1: the probe failed twice before passing (as it should)

Built `scripts/renown_v2_scorer.py` — pure stdlib, throwaway. Seven hand-built
archetypes (the 4 routes + a control + a high-renown legend + the bogey: a
"fast bot" that plays enormous volume in little wall-clock). Two questions: do
the 4 routes each reach high renown, and does the volume bot dominate?

**First run — FAIL, and a real bug.** The Villain scored 212 vs a ~30 field,
86% from scalps. Root cause: scalp quality was `base + 1.6·victim_renown` —
fine when renown was capped at `[0,1]` (v1), but uncapped, busting a 45-point
legend is worth ~70 points per scalp. Super-linear blow-up. The fix is a rule
for all of v2: anything that multiplies by another entity's renown must use a
*normalized/relative* measure. Switched scalp quality to weight by the victim's
**field percentile** (rank-based → robust to the very outliers it was
exploding on). This is exactly what Rung 1 is for — caught a structural bug for
the price of an afternoon, not a migration.

**Second run — still FAIL, but now a design decision, not a bug.** With the
percentile fix the routes balanced, but the *control* (up-and-comer) and even
the fast bot got labelled "high renown". Cause: the probe field was 7 entities
and "top 30%" of a tiny tourist-heavy field is meaningless. Added 30 realistic
filler AIs. That exposed the deeper issue: **pure top-X% percentile gating
manufactures fake stars** — top 30% of a mediocre field is still 30% "figures".
This is a genuine design question the probe forced into the open, not a
fixture artifact. Resolved it the way v2 should: high renown = top-X% **AND**
≥ k×field-median. Percentile caps *how many* (anti-inflation as renown ratchets
forever); median multiple is a *self-scaling* quality floor. Crucially both
field-relative — no absolute constant, which was v1's `0.40` mistake.

**Third run — PASS.** All 4 routes high (29–53 ≥ cut 14.35), each dominated by
its own signature driver; control below; no single-driver >85%. And the
headline: the fast bot is #1 under hand-count denomination, #6 under wall-clock
— the anti-treadmill lever is quantified, not just asserted.

Honest caveat: the specific weights (w_scalp=4, w_backing=3, etc.) are *not*
validated — Rung 1 only proves the four routes are each *viable* and the volume
bot is *contained*. The relative weighting is Rung 3's job (sweep over a frozen
sim log). What Rung 1 locks are the **structural** choices (percentile scalp
weighting, the AND-gated relative cut, wall-clock denomination), which are the
ones expensive to get wrong after building.

Force-added the scorer (scripts/ is gitignored) so it survives as the v2 spec
+ future test oracle.

Next: Rung 2 — point the same scorer at the real dev DB and eyeball whether the
top of the leaderboard matches intuition about who the field's "big names"
actually are.

## 2026-06-01 — Rung 2: real field, and the bug fixtures couldn't catch

Built `scripts/renown_v2_rung2.py` — maps the real repos onto the scorer's
`RenownInputs` and reads the live 4.9 GB main-worktree DB with `immutable=1`
(consistent snapshot, no locks/WAL writes — the SQLite-WAL-backup lesson). Found
the active sandbox `4db9b9f2…`: 80 cash entities, guest_jeff already a v1
"Infamous Villain" (renown 0.616, regard −0.247).

**First run — a silent join bug.** `peak_net_worth` and `time-at-#1` came out
**zero for every entity**. Cause: `holdings_snapshots` prefixes ids
(`ai:deadpool`, `player:guest_jeff`) while `cash_pair_stats`/`stakes` use raw
ids — so the holdings join matched nothing, silently nulling two of the four ★
drivers. This is the same cross-table prefix trap that's bitten the project
before. The dangerous part: it *looked* like a plausible leaderboard (backing
+ breadth carried it), so without the per-driver breakdown column I'd have
trusted a board with two dead drivers. Lesson: always print the component
breakdown — a renown number alone hides which drivers are silently zero.
Stripped the prefix; both drivers came alive (deadpool top1 9.0, peak 3.7).

**Second run — structure good, two weight problems only real data shows.**
- Regard reproduces v1 *exactly* (jeff villain, −0.25) — the read-side
  projection is faithful. Quadrants sensible (warm high-renown AIs = Beloved
  Legend). The scaffolding works.
- But: the human runs away at #1 (84, 2.7× #2), 71% from breadth alone —
  hands-denominated volume lets the most-active entity dominate. And the entire
  AI field is 50–80% backing-driven, because real AI-to-AI staking volume is
  far larger than my Rung-1 fixtures guessed. So `w_backing` is too hot and the
  AI field collapses to one route.

Resisted the urge to retune weights here — that's Rung 3's job (sweep over a
frozen sim log), and hand-patching to one sandbox would be overfitting. What
Rung 2 *proves* is that the structural choices survive real data and the id
plumbing is sound; the weights are explicitly *not* validated yet. Also
confirmed a hard dependency: scalps + legendary are 0 on static data (no
`cash_scalps` table), so the villain/legendary routes can't be evaluated until
the scalp tracker (workstream A) ships — which is exactly why A is the
self-contained, build-first prerequisite.

Force-added both scripts (scripts/ is gitignored).

## 2026-06-01 — offline structural pass on backing + breadth; the real culprit

Rather than tune weights against one sandbox (overfitting), applied the Rung-1
rule one more time — *uncapped drivers must be field-relative* — to the two
flagged drivers. Backing and breadth now contribute `w·log1p(raw/field_median)`
(a `_relative` helper + a `FieldContext` of field medians). Nice property I
didn't expect: the raw/median *ratio* is roughly denominator-robust, so the
hands-denominated offline read becomes a fair proxy for the wall-clock design —
which partly answers my earlier worry that breadth couldn't be judged offline.

One snag: my Rung-1 fixtures had only ONE backer (the Patron), so the field
median *was* the Patron's own value → log1p(1)=0.69, collapsing their route.
Fixed the fixtures to match reality (Rung 2 showed backing is near-universal):
broad modest backing + the Patron as the outlier. Also had to make the Grinder
a genuinely committed regular — after relativisation the pure-volume route is
(correctly, by anti-treadmill design) the weakest, so a *casual* grinder
shouldn't auto-qualify as a figure; a real one (huge wall-clock, broadest
network, modest winnings) does. Not gate-fitting — it's an honest statement
that volume demands more commitment than the other routes to reach fame.

Result: Rung 1 re-PASSES, and on the real field the human runaway halves
(84→54, gap 2.7×→1.26×) and the backing monoculture breaks (shares 80–94% →
42–65%, field now half breadth-led / half backing-led).

**Then Jeff made the key point: backing may be running away in the *economy*,
not just the score — and there are system levers (likability/respect gating).**
He was right, and the DB proves it. Top backers stake nearly the whole field
(deadpool 107 of ~113 borrowers), and stakes go out even at default-neutral
affinity. Traced it to `cash_mode/sponsor_offers.py` `TIER_FLOORS`: premium =
{0.0, 0.0}, standard = {0.4, 0.5}, and a stranger defaults to 0.5/0.5 — so a
stranger clears both tiers. Anyone backs anyone, by construction.

So there are two fixes at two layers: field-relative renown (done, read-side,
treats the symptom) and raising the sponsor affinity floors (a real
backing-economy change — alters chip flows + AI behaviour, must be sim-
validated, but is the more fundamental fix; it slows the economy AND makes
backing a *selective* signal so backing renown stops being noise). Did NOT
implement the economy change — it's out of scope for a read-side balance pass
and needs the sim. Flagged with evidence; whether near-universal staking is a
bug or intended chip-cycling is a product call, not mine to assume
(verify-the-premise discipline).

## 2026-06-01 — scalp attribution helper (scalp-tracker step 2)

Built `cash_mode/scalps.py` — the pure "who busted whom" rule (headline-winner
heuristic): `eliminations_from_sim(result)` for the AI-vs-AI world sim and
`eliminations_from_human_hand(human_id, busted)` for the human path. Shared
prerequisite for both the Rung-3 sim sweep (so the villain/scalp route is
exercised) and workstream A's durable counter. Verified the real shapes first
(`HandSimResult.winner_pid`, `HandEvent.type/personality_id`,
`HAND_EVENT_BUST="bust"`) rather than guessing — and noticed the bust event
already carries `opponent_pid = winner_pid`, so the headline-winner rule is
consistent with what the engine emits.

Gotcha worth keeping: my first cut imported `HAND_EVENT_BUST` from `full_sim`
for a single source of truth — but `full_sim` transitively imports the poker
engine → `core.llm` → `anthropic`, so the "pure, testable" helper suddenly
needed the whole runtime (and failed to import on a bare host). Fixed by
keeping a LOCAL `HAND_EVENT_BUST = "bust"` mirror + an integration-marked
drift-guard test pinning it equal to full_sim's (runs in CI/Docker, skipped by
`--quick`). Purity restored: the 11 logic tests need no engine. Validated all
11 by direct execution on the host (bare pytest is unsupported here and engine
deps aren't installed locally; the real pytest run is a Docker job).

## 2026-06-01 — Rung 3 sweep harness (two-part: capture → sweep)

Built the Rung-3 instrument as two scripts, matching the "frozen log → paired
re-scoring" design (re-scoring one frozen log under many weights is a perfectly
paired A/B — no RNG desync, the whole reason renown's read-side nature is a
gift). `renown_v3_capture.py` dumps a frozen-log JSON: `--from-db` (host,
read-only) for the real field; `--from-sim` (Docker) runs the rule-based cash
sim over the sandbox's AI ids, derives scalps with the new helper, and overlays
the play-derived drivers onto the DB economy/social state. `renown_v3_sweep.py`
(pure) re-scores under a 23-config grid and reports rank stability + the
treadmill correlation, with hand-rolled Spearman/Jaccard (no scipy).

Validated the machinery on a --from-db log (host): Q1 rank stability is strong
(mean rankρ=0.997 — the ranking barely moves as weights perturb; only the gate
knobs move the figure *set*, which is the point of those knobs). Q2 (treadmill)
correctly self-reports **N/A** because a db log has no scalps — so the
performance proxy is gutted and the verdict would be meaningless. I made the
sweep detect zero-scalp logs and say so rather than print a misleading FAIL.
That N/A is itself a finding: scalps are load-bearing for the anti-treadmill
property, which is exactly why the sim capture (the scalps helper's payoff) is
required for the real verdict.

Couldn't run --from-sim here (needs Docker + the engine). Wrote it carefully
against the verified play_one_hand signature (read-only: bankroll_repo=None,
chip_ledger_repo=None, no save_table; rule-based, no LLM) with rebuy-in-place so
hands keep flowing, but it's UNTESTED on the host — flagging that honestly. Next
real step is a Docker capture run to get the first scalp-populated frozen log
and the actual treadmill verdict.

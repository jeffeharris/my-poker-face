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

## 2026-06-01 — ran the sim in Docker; harness works, synthetic field doesn't

Built this worktree's backend image (COPY . . bakes my code) and ran the
--from-sim capture via `docker compose run --rm --no-deps` with scripts/
volume-mounted so I could iterate without rebuilds. It works end to end:
capture in the container → frozen JSON → sweep on the host.

Two bugs caught by actually running it (not guessing):
1. Synthetic `bot_NN` ids don't exist, so the engine LLM-GENERATED a persona
   per id — a real paid DeepSeek call each. Fixed by seeding from real
   `personalities.json` display names (engine resolves the existing config, no
   generation). Verified zero LLM calls after.
2. 100bb stacks barely bust in 30 hands (5 scalps). Shortened to ~50bb + more
   hands → 207 scalps over ~2,800 hands.

But the real lesson is about the FIELD, and it's a verify-the-premise moment: I
nearly banked a green "TREADMILL PASS ✅". It was bogus. The synthetic field is
homogeneous — rebuy-in-place means every bot plays exactly 350 hands, so the
volume axis has ZERO variance (CV=0.000) and renown can only track performance
by construction (perf corr=1.000, hands corr=0.000 — suspiciously perfect). And
the fish/shark split via the fake bankroll_repo produced no skill gradient (fish
3.71 scalps/busts ≈ sharks 4.92 — the 'fish' archetype isn't weaker than default
TieredBot in self-play). A flat field also yields 0 "figures". So the PASS was
trivial, not earned. I added a hand_count-CV guard so the sweep now reports
DEGENERATE instead of a misleading PASS — exactly the silent-green the project
keeps warning about.

Honest conclusion: harness + scalp helper + metrics are all PROVEN; the
synthetic DB-free roster is just the wrong field for the treadmill question. A
real verdict needs heterogeneity — real personas with real archetypes (skill
spread) and varied hand counts (fish rebuy more) — i.e. the real bankroll_repo
against the populated DB (main-worktree container). Stopped short of that
DB-wrangling step rather than chase a result; flagged it as the definitive next
move. What Rung 3 DID lock: the harness is trustworthy (incl. its own degeneracy
guard) and rank-stability is robust (ρ=0.998).

## 2026-06-01 — the premise check that pivoted Rung 3 (and the real verdict)

Was about to build a DB-extract pipeline to run the sim with the real
bankroll_repo (for a skill gradient). Checked the data first (verify-premise),
and it killed the plan cleanly: `ai_bankroll_state` has no archetype column at
all; `load_archetype/rule_strategy` actually read `personalities.config_json`,
and across the 80-entity sandbox only **2 of 80** have any archetype/strategy
(both 'fish') — the rest are default TieredBot. The field simply isn't
skill-tiered, so NO sim (real repo or fake) can manufacture a skill→scalp
gradient here. The scalp/villain route is untestable on this field without
authoring tiered opponents. Glad I checked before building the extract.

The pivot: the real DB field ALREADY has the heterogeneity the treadmill
question needs — real volume (total_hands) and real performance (chips won, net
worth) from months of play. Scalps were only ever needed for the villain route.
Refined the sweep to judge against formula-INDEPENDENT ground truth (roster_net,
net worth) rather than the partly-circular driver split, dropped the
scalp-required N/A, and ran it on the --from-db log. First honest verdict:
  renown ↔ hand_count    = +0.66  (volume)
  renown ↔ net_worth     = +0.59  (wealth standing)
  renown ↔ chips_won     = +0.02  (barely!)
  → VOLUME-LEAN ⚠️ — renown rewards out-GRINDING over out-PERFORMING.

That's a genuine, actionable Rung-3 finding (the synthetic sim never could have
told me this). The fix is the wall-clock denomination already in the design
(can't test on a static snapshot, but the lean confirms it matters) and/or
down-weighting breadth/tenure vs standing. Rank stability stays strong
(ρ≈0.997). Net: the whole Docker/sim detour taught me the sim was the wrong
instrument for THIS field; the read-side projection on real data was the answer
all along — exactly the property that made this feature cheap to validate.

## 2026-06-01 — the wall-clock fix WAS testable offline (presence proxy)

Jeff asked the sharp question: "how long do I need to play live?" — re the
wall-clock denomination I'd been calling untestable on static data. Checked,
and the answer is ZERO. holdings_snapshots per-entity distinct-tick counts vary
(271–637, spread 0.61) — a presence/lifespan proxy for wall-clock that's
independent of hand-count. Wired wall_clock_hours from it and re-ran with the
baseline's volume_denominator='wallclock'. The lean flipped to a clean PASS:
  renown ↔ hand_count  0.66 → 0.38   (volume pressure drops)
  renown ↔ net_worth   0.59 → 0.72   (performance tracking rises)
  → PASS ✅ — performance (0.72) well above volume (0.38).

So the CORE anti-treadmill lever is validated on real data, not just asserted.
The confound was clean (hand_count↔net_worth=+0.05 → genuinely separable, the
lean was a formula artifact). A blunt down-weight (~0.75× breadth/tenure) also
flips it (renown_v3_rebalance.py), but wall-clock denomination is the principled
fix and it works.

Honest caveat: the proxy is ticks-tracked-in-sandbox (presence/lifespan), not
active-table minutes — direction solid, magnitude indicative. And I kept
over-calling things "untestable on static data" (first the treadmill verdict,
then the wall-clock fix) — both turned out testable with the right
formula-independent signal from data the game already records. Lesson: before
declaring something needs live data, ask what proxy the read-side already has.
The only genuinely-untestable-here piece remains the scalp/villain route (needs
a skill-tiered field).

## 2026-06-01 — workstream A: the scalp tracker (steps 1 + 3a built)

Moved from validation to building. The scalp tracker is the shippable
prerequisite for the villain route. Step 2 (pure helper) was already done.

Step 1 (durable counter): schema v132 `cash_scalps` (per eliminator→victim
pair, so renown-weighting reads the victim's standing) + `CashScalpsRepository`
+ registrations. 7 repo tests + schema-chain tests green. Mirrored the v122/
v131 migration idiom exactly (new tables go via migration, not _init_db —
fresh DBs run the whole chain).

Step 3a (world-sim path): wired into `refresh_unseated_tables` — each sim
hand's eliminations recorded best-effort via a lazily-resolved cash_scalps_repo
(reused the file's own entity-presence getter pattern, so no param-threading
through 4 callers + 8 tests, and the lobby stays import-light). Only real busts
write. 42 lobby conservation/invariant tests green; clean import.

Step 3b (human's own table) deferred with the integration point pinned:
`_refill_cash_seats` has busted_indices but not the hand winner — attribution
needs the headline winner threaded from the award step. Lower value (one human
entity's renown vs the constant world tick) + higher risk (live human flow), so
a clean follow-up rather than a forced fragile wire.

Net: the durable counter is live and accruing from the world sim. The renown
driver that READS it is step 4 (lands with the broader v2 build). Validated each
step in Docker (poker/cash_mode/flask_app mounted live; tests/ mounted per-run)
— no rebuilds needed after the first image.

Then finished 3b (no pause, per Jeff). First corrected my own integration point:
I'd flagged `_refill_cash_seats`, but that's the next-hand step — the winner
isn't in scope. The right point is `handle_evaluating_hand_phase`, right after
`award_pot_winnings` (stacks final) where `winning_player_names` is known. Added
`_record_cash_scalps`: headline winner (human owner_id or AI personality_id via
`cash_personality_ids`) credited for each non-human busted to 0. Recorded
AI-vs-AI busts at the human's table too (consistent with 3a's headline-winner
rule) and excluded the human-as-victim. Best-effort wrapped; 5 wiring tests +
clean game_handler import. Asked whether this was a workflow candidate — no, it's
a single serial edit, nothing to fan out; workflows fit step 4 (parallel design
+ adversarial verify) or a cross-file review, not this. **Workstream A (the
scalp tracker) is complete through step 3; step 4 (renown reads scalps) is the
v2 build.**

## 2026-06-01 — step 4 (v2 compute_prestige) kicked off as a WORKFLOW

Jeff opted into a workflow for the v2 build — and this one genuinely fits the
fan-out shape (unlike 3b): comprehend a wide surface, weigh an architectural
decision, implement, and review from several adversarial angles. Structured it
as Map → Design → Implement → Verify:
- Map (4 parallel Explore readers): current v1 compute + all consumers; the
  ticker recompute path + the field-wide-renown question; the VALIDATED offline
  scorer as the porting spec; the scalp API + test surface.
- Design (1 architect): a concrete blueprint with MINIMAL blast radius — explicit
  on the field-wide/AI-symmetry decision and whether AI-renown persistence needs
  schema (defer if risky), preserving the legibility guardrail + the 4 hooks.
- Implement (1 agent, no parallel file conflicts): port the scorer into
  cash_mode/prestige.py + wire the scalp driver + mirror the validated balance
  assertions as pure unit tests. Implement-now vs defer per the blueprint.
- Verify (4 parallel adversarial reviewers): correctness-vs-scorer, the
  legibility guardrail + hook compatibility, AI-symmetry/blast-radius, tests.

Deliberate guardrails on the workflow: agents do NOT commit and do NOT run
Docker — I stay in the loop to run the real test suite in-container and commit,
and the risky ticker/schema surgery is gated behind a "defer if risky" flag
rather than executed blind. The offline scorer being pre-validated is what makes
the implement phase safe: it's a faithful PORT of known-good math, not a fresh
design. Workflow running in the background (wf_f79f48a0); will validate + land
its output once it returns.

## 2026-06-01 — step 4 landed (the workflow paid off)

The workflow returned (10 agents, ~943k subagent tokens, ~19 min). Its design
phase made the call I'd have wanted: **additive, behind a default-OFF flag**,
zero live behavior change. The v2 math is ported verbatim into
cash_mode/prestige.py (score_renown_field two-pass, field-relative
backing/breadth, scalp-percentile weighting, wall-clock volume,
quadrant_label_relative, high_renown_cut), the scalp driver reads the now-live
cash_scalps counter read-only, and v1's compute_prestige + absolute quadrant +
the 4 hooks + ReputationScore's v1 fields are all untouched. Genuinely risky
bits (prestige_snapshots schema for AI renown; ticker field-wide compute under
the 250ms budget; the flag flip; the frontend gauge) were correctly DEFERRED
behind their own validation gates — not executed blind.

The standout: the implement agent wrote 3 **oracle-parity** tests that assert
the production v2 totals/ordering/cut byte-match the balance-validated offline
scorer — so "is production the same math we validated?" is now a test, not a
hope. Plus the Rung-1 balance properties + backward-compat as tests.

Stayed in the loop as planned: agents didn't commit or run Docker; I ran the
suite myself (50 green: 21 v2 incl. parity + balance + compat, v1 prestige,
demeanor hook), confirmed the flag defaults OFF, then committed (d9dce6a1). The
4 adversarial reviewers flagged nothing critical/high (two cosmetic lows about a
victim_percentile docstring + the builder having no oracle counterpart — both
non-issues). Why this was a good workflow fit and 3b wasn't: wide comprehension
surface + an architectural decision + multi-angle review, with a PRE-VALIDATED
spec making the implement phase a safe port rather than a fresh design.

Step 4's safe slice is DONE; the renown feature now has validated math wired to
the live scalp counter, dark behind RENOWN_V2_ENABLED. Remaining is the deferred
persistence/ticker/flip stage — its own (sim-gated) project.

---

## 2026-06-01 (later) — human-only v2 integration, and a degenerate cut on real data

Picked up the handoff. Jeff chose the **human-only "ship it" path** (stages
B-human → C → D): get the uncapped, field-relative gauge live for the human
behind the kill switch, skipping the risky AI-persist migration (stage A).

Built the integration foundation, bottom-up, all validated:
- **Schema v133** — additive `prestige_snapshots` columns (`formula_version`
  default 'v1', uncapped `renown_v2`, `victim_percentile`, field `high_cut`,
  v2 component JSON, `field_size`). Non-destructive ADD COLUMNs.
- **Repo** — `record()` gained optional v2 kwargs (back-compat default = a v1
  row); `load_renown_v2_peak` MAX-ratchet (v2 has its own scale, never mixes
  with v1's ratchet).
- **`RenownFieldRepository`** — production port of the oracle's
  `renown_v2_rung2.load_field`, batched over one connection (~6 scans, NOT
  per-entity: `ticks_at_#1` is inherently field-relative), degrade-to-zero per
  source so a missing table never breaks the ticker.

Two things settled empirically that the handoff feared:
- **Cost**: the field build+score is **~480ms once per 300s per sandbox** (the
  recompute is already throttled) — a 0.16% duty cycle, NOT an O(N²)-per-tick
  cost. The "no O(N²) ticker" promise holds because the field-relative quadrant
  rides the existing throttle.
- **One-site flag flip**: all 4 hooks + the lobby read the persisted
  `snap["quadrant"]` STRING. So writing a field-relative quadrant in the
  recompute makes every consumer follow with zero hook-code change. Stage C
  collapses to the recompute.

**Parity PASS**: `scripts/renown_field_parity.py` — the prod loader's
shared-field inputs byte-match the oracle across all 80 real entities. (scalps
degrade to 0 on the old main-worktree DB, which predates the v132 table — the
intended ticker-safe behavior.) The port is faithful.

### The wrong-turn-caught: the "locked" cut is degenerate under the design denom

The handoff said the math is "locked — don't re-litigate; the work left is safe
integration." I did the mandated pre-flight anyway (end-to-end score on the real
field) and it surfaced a regime the offline validation never exercised:

- Prod scores with the **design denominator (`wallclock`)**; rung2 had used
  `hands` explicitly. Under wallclock the field **compresses** (presence-tick
  counts cluster), so the median rises relative to the max.
- The locked `HIGH_RENOWN_MEDIAN_MULTIPLE=3.0` floor then gives
  `3.0×median = 69.1 > field max 59.5` → **0 figures**. The human is the
  unambiguous **rank #1** (renown 59.5, **2.58× median**) yet classifies as
  **"Disliked Nobody"** — a regression from v1's "Infamous Villain."
- The hint was visible in the earlier sweep ("robust figures: none") — I
  under-weighted it. The `3.0` was tuned on synthetic Rung-1 fixtures with a
  designed skill gradient; the real field is more compressed.

Key insight that makes this safe to fix: **the cut is orthogonal to the
validated treadmill/anti-treadmill result** — it only sets the high/low
*classification* boundary, not renown magnitude or ordering. So re-calibrating
the floor doesn't re-open the locked math (and the parity tests force scorer +
prod to move together).

Quantified the fix space on the real wallclock field (figures / is-human-figure):
`3.0×→0/no`, `2.5×→1/yes`, `2.0×→2/yes`, `1.5×→8/yes`. So k∈[2.0,2.5] gives a
small, meaningful elite led by the standout.

STOPPED before wiring/flipping to put the calibration to Jeff — it's a genuine
product call (how exclusive "figure" status is), and changing a deliberately-set
"locked" constant warrants sign-off. Foundation is built + parity-validated and
uncommitted, waiting on the cut decision. Lesson reinforced: "the math is
locked" is itself a premise to verify — the lock covered the validated regime,
not the one production actually runs in.

---

## 2026-06-02 — human-only v2 BUILT + the denominator decision

Resolved the cut finding by following the data, not the "locked" label. Jeff
chose "dig into the treadmill first," and the dig flipped my own naive reading:

- The wall-clock **presence proxy is degenerate on real data**: holdings ticks
  are near-uniform (656 distinct ticks, presence median == max == 656, CV 0.16 —
  the ticker stamps ~every entity each cycle). So `wallclock` denomination
  flattens the volume drivers → `3×median` exceeds the field max → **0 figures**.
- The **sim couldn't arbitrate** (constant 300 hands → DEGENERATE treadmill
  verdict, 0 figures in all 23 sweep configs, no shark/fish separation). It only
  confirmed the scalp route is wired (185 scalps, top driver). Exactly the
  homogeneity the handoff warned about.
- Under `hands` the real field behaves: human = **Infamous Villain** (matches v1
  + rung2), 2 figures (human + deadpool the whale-backer). The "hands-treadmill"
  is **inert**: hand-count ⊥ performance (ρ≈0.05) AND AI hand-volumes are
  negligible (2–17 hands) — renown is driven by backing/wealth/scalps, not
  grinding. My preview's rule ("low ρ → treadmill risk") was backwards here.

Decision (Jeff: "ship hands denom"): production scores under **`hands`**
(`PROD_VOLUME_DENOMINATOR`), while the scorer/parity **default stays `wallclock`**
so the Rung-1 anti-treadmill lever tests stay green — the per-call WeightsV2
field exists exactly for this. Documented the divergence + the real-data reason
in `cash_mode/prestige.py`.

Built the whole human-only slice (stages B-human → C → D), bottom-up:
- v133 schema (additive), prestige repo v2 (record kwargs + own-scale ratchet),
  `RenownFieldRepository` (batched oracle port, degrade-to-zero).
- Ticker `_maybe_v2_overlay` behind `RENOWN_V2_ENABLED`: scores the field once
  per the existing 300s throttle (~480ms), ratchets v2 renown, writes the
  field-relative quadrant into the **consumed** `quadrant` column (so all 4
  hooks + the lobby follow with ZERO hook change) + the v2 columns.
- Lobby payload `formula_version` + v2 block; ReputationPanel v2 branch
  (uncapped figure + progress-to-"figure" rail + field percentile + uncapped
  ledger).

Verified: **79 green** (parity byte-match to the oracle, repo, field-loader
semantics, + a 4-test live-wiring integration: flag-off→None, flag-on→scores,
persist+payload round-trip, v1 has no v2 block). tsc + eslint clean. Host
parity re-confirmed (80/80 entities). **Committed flag stays OFF** (kill switch).
Remaining: the live flip + a real screenshot need a populated sandbox (this
worktree's DB is empty); the deferred AI-persist/field-wide-ticker stage (A/B)
is still its own project.

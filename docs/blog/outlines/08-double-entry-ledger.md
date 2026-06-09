---
purpose: Ready-to-write outline for the "A double-entry ledger for a game economy" blog post (Devlog track)
type: vision
created: 2026-06-09
last_updated: 2026-06-09
---

# Outline — "A double-entry ledger for a game economy"

- **Working title:** A double-entry ledger for a game economy
- **Track:** Devlog
- **Target reader:** Build-in-public followers and engineers who like a clean
  systems story — how a single silently-swept poker buy-in pushed a hobby game's
  fake-money economy onto a real append-only accounting ledger, and why "make every
  chip movement a balanced pair" turned a class of invisible bugs into a single
  measurable number.

## One-line hook (grounded)

A human's cash buy-in got silently swept with no trace it ever happened — and the
fix wasn't a patch, it was deciding that every chip in the game now has to come from
somewhere and go somewhere, the same way real money does.

## Narrative spine (section beats, in order)

1. **The bug that started it: a chip held in a deleted row is just gone.** The
   custody work was prompted by a real loss — a human's cash buy-in was silently
   swept when a stale row got cleaned up. The root insight (from the design log):
   there was no transaction history to prove a buy-in ever happened, so a deleted
   row forfeited chips *with no trace*. That's the whole motivation for the post:
   a mutable integer for "how many chips you have" can't tell you where they went.

2. **The model: an append-only log, and the balance is just a sum.**
   `chip_ledger_entries` is an append-only event log of every creation, destruction,
   and transfer. The bankroll integer on the table becomes a *cache* the ledger can
   re-derive. The one-sentence invariant is the spine of the post:
   `Σ(creations) − Σ(destructions) == Σ(all held balances)`. Any non-zero
   difference is **drift** — the single number that means "a chip moved without a
   row." Crucially, the ledger is never reconciled by editing rows ("no updates, no
   deletes"); drift is surfaced, not papered over.

3. **Double-entry, adapted to a game: creations, destructions, transfers.** Explain
   the three shapes plainly. A *creation* is `central_bank → someone` (chips enter
   the universe — seeding a new AI, a side-hustle payout). A *destruction* is
   `someone → central_bank` (rake, vice spend). A *transfer* is entity-to-entity,
   both sides already counted, so it's *invisible to the drift math* — it exists
   purely as the human-readable statement the silent-sweep bug proved was missing.
   The vocabulary is enforced: an unknown reason string is *rejected* at write time,
   so a typo becomes a test failure, not silent drift.

4. **The reframe that made the fix obvious: the scary number wasn't missing chips.**
   The honest middle of the post. Early audits showed an alarming ~32.6M "gap" that
   read like missing money. Decomposing one sandbox instead of the global number
   showed it was almost entirely *cancelling per-account noise* — winners (one AI
   +2.36M) offsetting cashed-out losers, table P&L moving *between* AIs with no
   ledger row. The signed sum in that sandbox was −327K against a 20.5M absolute sum.
   The fix dropped out of the reframe: ledger the seat buy-in/cash-out transfers and
   the per-account gaps collapse — no chips were ever minted. Lesson stated plainly:
   verify the *magnitude*, not just a plausible mechanism.

5. **The boring real cause: two commits, two connections, one crash window.** Even
   after the model was right, the derived bankrolls drifted from the stored integers
   by small mixed-sign amounts (~24k abs in one sandbox; a single AI off by 7,569).
   The cause wasn't exotic — the bankroll int and its ledger row committed in
   *separate transactions on separate connections*, so a crash in the millisecond
   window left one without the other. Tiny per event, but it accumulates over
   hundreds of thousands of sim hands. The fix: a re-entrant unit-of-work so the int
   and its ledger row commit on the *same* connection, converted hottest-path-first.

6. **The deadlock that rewrote the plan.** Wrapping the bankruptcy path in the
   unit-of-work deadlocked — `database is locked`. Holding one repo's writer
   transaction open while a *different* repo wrote the same SQLite file hit the
   single-writer lock. One error reshaped the design rule: a unit-of-work may not
   span another repo's write to the same file. Two genuinely-interleaved paths
   (a tournament buy-in saga, the human cash routes) got *deferred* rather than
   force-wrapped, covered meanwhile by the periodic reconcile. This is the honest
   "the architecture told me no" beat.

7. **The payoff: the audit earns its keep in production.** The point of all this is
   that drift is now a *gauge*. After the June launch, the ledger's conservation
   guard caught a string of real mint bugs that would otherwise have been invisible:
   a cross-sandbox stake leak minting ~1M chips every six hours, a house-stake
   double-credit, an aspiration-staking path funding the wrong seat (prod drift
   −2.16M). Each was a chip created without a balanced pair — and each showed up as
   non-zero drift instead of a slow, silent economy collapse. The ledger didn't
   prevent the bugs; it made them *loud*.

8. **Closing: real accounting for fake money, and why it's worth it.** Tie back to
   the founder's framing question for the whole "living economy" sprint — *what keeps
   you coming back?* A persistent economy where AIs stake each other, climb, and bust
   only feels real if the money is conserved; one minting bug and the whole world
   inflates into nonsense. Double-entry bookkeeping is ~700 years old; pointing it at
   a poker game's pretend chips is the unglamorous engineering that lets the *fun*
   part — a world with stakes — be trustworthy.

## Evidence & assets

**Hard facts / numbers to cite (verify each against code/audit before publishing):**
- The conservation invariant, verbatim: `Σ(creations) − Σ(destructions) == Σ(all
  held balances)`; `drift == 0` is the correctness signal
  (`CHIP_CUSTODY_LEDGER.md`, `chip_ledger_audit.py:compute_audit`).
- The "~32.6M gap" reframe: signed per-account sum **−327K** vs absolute sum
  **20.5M** in guest_jeff's sandbox; example winner **+2.36M** (deadpool), example
  cashed-out loser **−0.46M** (cruise_carl). Source: `chip-custody-cutover.md`.
- Atomic-write divergence figures: ~**24k abs** drift across ~**14** world-sim AIs
  in one sandbox; one AI (cthulhu) off by **7,569** while its `ai:`-sourced rake
  totalled **210,124** — the magnitude that *disproved* the first hypothesis. The
  one-time dev reconcile retired **25,832 abs** drift across **14 AI + 1 player**
  accounts; suspense holds net **+4,612**. Source: `chip-custody-atomic-writes.md`.
- Backfill end-state: dev went from **AI 339/1239** reconciled to **LEDGER COMPLETE
  — AI 1239/1239 + Player 4/4** (`scripts/backfill_chip_custody.py`).
- Schema lineage: `chip_ledger_entries` created in **v93/v94** (the one-shot
  `pre_ledger_universe` seed entry), nullable `sandbox_id` added in **v103**;
  `(source,sink)` index added later (commit `48830bd2`).
- Two cutover flags, both default OFF (Presence pattern): `CHIP_CUSTODY_ENABLED`
  (record the AI side) and `CHIP_CUSTODY_DERIVE_READS` (trust the ledger for reads —
  deferred because the int is the transaction-consistent hot-path cache).
- Post-launch drift-guard catches (from MEMORY.md / commits — confirm exact figures
  with Jeff): cross-sandbox stake leak ~**1M / 6h** (fixed, `4afd6e96`); house-stake
  double-credit (`e56829b6`); aspiration-staking phantom prod drift **−2.16M**
  (`299f77df`); `take_stake` mint + settle-time conservation guard (`99dcafbb`).
  These are the strongest "the audit paid for itself" evidence — but they postdate
  the cutover and several are sim/prod-diagnosed; frame as "the guard kept catching
  mint bugs," and let Jeff confirm which numbers are publishable.

**Screenshots / files:**
- There is **no chip-ledger / audit screenshot in the asset folders yet** — checked
  `react/react/src/assets/screenshots/` and `.images/`. The admin "Chip economy"
  panel (`react/.../admin/ChipLedgerPanel.tsx`) showing the audit's `drift` value and
  `by_reason` breakdown would be the ideal hero image. **Needs capture** (open gap).
- The post is diagram-friendly even without a screenshot: a simple `central_bank →
  player → seat → player` flow showing creation / transfer / destruction would carry
  beats 2–3 better than any UI shot.
- Source docs to excerpt/link: `docs/technical/CHIP_CUSTODY_LEDGER.md` (the
  authoritative model — invariants I1–I5, account vocabulary), `CASH_MODE_ECONOMY.md`
  (the flow tables), and the two captain's logs
  (`chip-custody-cutover.md`, `chip-custody-atomic-writes.md`) — the narrative spine
  of beats 1, 4, 5, 6 comes almost entirely from these two logs.

**Commits to reference (real subjects, dated):**
- `2bd453f9 Chip-custody atomic writes + ledger reconcile (T3-82) (#178)` (2026-06-04)
- `aab13f35 fix(cash): make ai_seed first-write atomic so a concurrent lobby race can't double-mint` (2026-06-05)
- `7750e07d fix(cash): ledger human-staking chip flows (close the staker-side gap)` (2026-06-04)
- `48830bd2 perf(cash): index chip_ledger by (source,sink) + track growth/retention (T3-88) (#179)` (2026-06-04)
- `99dcafbb fix(cash): close take_stake chip mint + add settle-time stake conservation guard (#235)` (2026-06-08)
- `299f77df fix(cash): aspiration staking minted chips — fund the climber's seat + don't settle on the climb-vacate (#217)` (2026-06-08)
- `e56829b6 fix(cash): house stake principal lands on the seat, not the bankroll (#215)` (2026-06-07)

## Candidate pull-quotes (verbatim)

- Jeff, applying ledger thinking to a *narrative* bug (chat, circuit-progression):
  **"start the conservation audit for the Sal-stake - it shoudl just come from his
  bankroll that he took off of Larry. It should not be minted."** — the perfect
  human-voice statement of the whole invariant: a scripted character's winnings must
  be *moved*, not *created*. (Typos verbatim; keep them or `[sic]`-clean — Jeff's call.)
- Jeff, treating the closed economy as a system to interrogate (chat, career-mode):
  **"where does the table rake come from in the chip economy?"** / **"where does the
  table rake flow to in the chip economy?"** — two-line illustration that "source and
  sink for every chip" is the mental model, not just code. Pairs with the fact that
  rake *recycles* into the bank pool rather than evaporating.
- Commit subject (the bug class in one line):
  **`fix(cash): make ai_seed first-write atomic so a concurrent lobby race can't
  double-mint`** — "double-mint" names the exact failure the ledger exists to catch.
- From the captain's log, the disproven-hypothesis beat (paraphrase candidate, or
  quote): **"The numbers killed it"** — cthulhu's 7,569 divergence vs its 210,124 of
  `ai:`-sourced rake. Good for the "verify the magnitude, not the mechanism" lesson.

## Draft intro paragraph (post voice)

> The bug that started this was small and quiet: a player put chips on a table, and
> when a stale session row got cleaned up later, those chips were just... gone. No
> error, no trace, nothing to prove the buy-in had ever happened. The chips lived in
> a single integer column, and when the row holding that integer was deleted, so was
> the money. That's the moment I stopped treating the game's economy as bookkeeping
> I could eyeball and started treating it like real accounting: every chip now has to
> come from somewhere and go somewhere, recorded in an append-only ledger, so that at
> any instant the chips that exist equal the chips anyone holds. It's double-entry
> bookkeeping — a 700-year-old idea — pointed at fake poker chips. The unglamorous
> part is that it works, and it has been catching minting bugs ever since.

## Open gaps (need the founder or more reporting)

- **Hero asset is missing.** No chip-ledger/audit screenshot exists in the marketing
  asset folders. Capture the admin "Chip economy" panel showing a live `drift` value
  and the `by_reason` breakdown, or commission a simple flow diagram. Without one,
  the post leans entirely on prose + a hand-drawn diagram.
- **Which post-launch mint-bug numbers are publishable?** The strongest "the audit
  earned its keep" evidence (the ~1M/6h cross-sandbox leak, the −2.16M aspiration
  drift) is diagnosed in sim/prod and recorded in internal memory. Confirm with Jeff
  which figures are real, current, and OK to publish — and whether to frame them as
  "the guard caught these" vs. naming specific drift magnitudes.
- **Production flag state.** `CHIP_CUSTODY_ENABLED` is on in *dev*; the committed
  default is OFF and the docs say "prod is pre-custody." But the post-launch fixes
  above imply the ledger *is* doing work in prod. Confirm the actual prod posture as
  of the June 5 cutover before the post says "live" — this is the most likely place
  to be wrong. (INFERRED tension between the doc's "prod untouched" and the prod-drift
  bug reports; only Jeff can resolve.)
- **`DERIVE_READS` status.** The end-state (ledger becomes the read authority) is
  deliberately deferred. Confirm it's still off so the post doesn't overclaim that the
  ledger is the live source of truth for reads (it's the audit substrate; the int is
  still the served value).
- **Scope discipline.** This topic overlaps heavily with the staking/backing system
  (`298476c2`) and the cross-sandbox mint fixes. Decide whether those are *this*
  post's payoff section or a separate "the economy kept trying to mint money" post.

## Cross-links (within the series)

- **Cash mode / "living economy" post:** the ledger is the substrate that whole
  economy sits on — pool, vice, tourists, side-hustle faucet. This post is the
  "how the money is conserved" companion to that post's "what the money *does*."
- **"Your opponents remember you" (03):** that post's informant-unlock chip sink and
  bank-pool recycling are ledger reasons (`informant_unlock`); cross-link the
  closed-economy recycling idea.
- **The June 5 production cutover post (if one exists):** the "four confident
  misdiagnoses resolved only by measuring" theme rhymes exactly with this post's
  "verify the magnitude, not the mechanism" and the disproven rake hypothesis — they
  share a thesis about reproducing/measuring over guessing.
- **A "wrong turns / build-in-public honesty" post:** the deadlock that rewrote the
  plan, the green-locally/red-in-CI miss, and the disproven first hypothesis are all
  honest-engineering beats that could anchor or feed that post.

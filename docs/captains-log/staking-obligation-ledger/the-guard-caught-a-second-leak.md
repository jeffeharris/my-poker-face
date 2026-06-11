---
purpose: Narrative log of the cross-sandbox stake-settlement leak the funding guard surfaced the day a second human joined — and the fix that pins a stake to its origination sandbox
type: guide
created: 2026-06-09
last_updated: 2026-06-09
---

# The guard caught a second leak

## The alarm we built actually fired

The whole point of the alarm-only funding/obligation guard
(`assert_stake_funding_reached_borrower_seat`) was this moment: a brand-new leak
shows up in Sentry the day it appears, instead of silently summing to millions
over weeks. It worked. The guard tripped — "borrower seat funded 0 < principal"
— on `the_honey_badger`, and the seat signature for one sandbox was sinking
~1M/6h, drift −930k and reliable.

It would have been easy to read that as the *old* wrong-seat bug regrowing
(#235/#239). It wasn't. Those were wrong-seat *within* a sandbox. This was
wrong-*sandbox*.

## The smoking gun

One stake, two sandboxes:

```
00:13  stake_fund    8000  ai:the_honey_badger → seat:ai:bfa7050b:…   funded in sandbox bfa7050b
01:41  stake_payoff  1486  seat:ai:c0b0f5d1:…  → ai:the_honey_badger  settled draining sandbox c0b0f5d1
```

Funding credited the borrower's seat in **bfa7050b**. Settlement drained the
borrower's seat in **c0b0f5d1** — a seat that stake never funded. Drain a
never-funded seat and it goes negative; negative seat balance is minted chips.

## Why it was invisible until now

AI personas exist *per sandbox*. With only one human (one sandbox) live, every
`the_honey_badger` stake funded and settled in the same place — the
single-sandbox checks from last turn looked clean because they *were* clean.
The day a second human joined, a second sandbox existed, `the_honey_badger`
lived in both, and the vector opened.

The mechanism: the `stakes` table was **global**.
`load_active_for_borrower(pid, 'personality')` filtered only on
`(borrower_id, borrower_kind, status)` — no sandbox. So a world-tick processing
c0b0f5d1 called settlement with `sandbox_id=c0b0f5d1`, but the active-stake
lookup happily returned the bfa7050b-originated row (the only active one). The
funding and settlement *ledger rows* were already sandbox-tagged correctly — it
was the **stake row being selected** that was unscoped.

## The fix: pin the stake to its origination sandbox

A stake funds `seat:ai:<sandbox>:<borrower>` and must settle against that *same*
seat. So the stake row now carries the sandbox it was originated in, and the
active-stake lookup is scoped to it:

- `stakes.sandbox_id` column (additive migration `20260609_1200_stakes_sandbox_id`,
  plus a partial index `idx_stakes_active_borrower`).
- Every origination site sets it: take_stake + aspiration (lobby), human
  sponsor + player-offer (cash_routes).
- `load_active_for_borrower(..., sandbox_id=...)` filters
  `(sandbox_id = ? OR sandbox_id IS NULL)`. A c0b0f5d1 tick can no longer load a
  bfa7050b stake. The `OR NULL` keeps pre-fix rows findable so they drain out
  under the old global behavior instead of orphaning.
- Every AI-borrower call site passes its sandbox (settlement, both
  origination-invariant checks, the player's stakeable-AI list, the offer
  route). Human-borrower paths (one human = one sandbox) stay global.

The conservation guard stays exactly where it is, as the backstop. The scoped
lookup is now the *primary* protection; the guard is the second line that alarms
if something ever slips past it again.

## What this fix does NOT do

It stops the *new* minting structurally — going forward every stake is tagged,
so cross-sandbox settlement is unwriteable. It does **not** retroactively pin the
stakes already active in prod (their `sandbox_id` is NULL, so the `OR NULL` path
still lets them be found from any sandbox — the in-flight bleed on *those*
specific rows continues until they close).

Closing that last gap is a data reconcile, not a schema change: backfill
`sandbox_id` on existing `status='active'` rows from their `stake_fund` ledger
row's tag (or the `seat:ai:<sandbox>:<borrower>` sink signature). Deliberately
kept out of the migration — joining stakes→ledger by `context_json` is fragile
and has no place running against fresh/test DBs. It belongs in a one-off prod
reconcile script, the same way the ~3M clawback did.

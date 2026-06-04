---
purpose: Grounded narrative of closing the chip-ledger int↔derived divergence — the atomic-write unit-of-work, a disproven hypothesis, a deadlock that reshaped the plan, and the reconcile safety net
type: reference
created: 2026-06-04
last_updated: 2026-06-04
---

<!-- newest entries at the bottom -->

# Captain's log — chip-custody atomic writes & the ledger reconcile (development)

How a single mis-settled poker stake turned into closing the chip ledger's
divergence at its source. Recorded warts-and-all: one hypothesis I had to throw
out, a deadlock that rewrote the plan mid-stream, and a green-locally/red-in-CI
miss.

## Where it started

A player staked Frida Kahlo, she walked from the table up ~$43.6k, and the
stake settled for scraps. Chasing that surfaced two things: a real
seated-leave settlement bug (fixed separately), and — while auditing the
chip ledger to make the player whole — that the **ledger-derived** bankrolls
disagreed with the **stored** ints by small, mixed-sign amounts across ~14 of
the world-sim AIs (~24k abs in one sandbox).

## The wrong turn I want on the record

My first hypothesis for the AI divergence was the pre-2026-06-01 rake path:
`full_sim` used to source table rake from the `ai:<pid>` bankroll account
instead of the seat, and that cut over cleanly at 06-01. It *looked* like the
culprit. The numbers killed it: cthulhu's divergence was 7,569 while its
`ai:`-sourced rake totalled 210,124 — nowhere near. The int had been correctly
debited for that rake all along; the rake-source change was a seat-account
accuracy fix, **not** the bankroll-divergence cause. I'd nearly written it up as
the answer. Lesson re-learned: verify the magnitude, not just the plausible
mechanism.

The real cause was the boring architectural one T3-82 had already named: the
bankroll int (the served authority) and its `chip_ledger_entries` row commit in
**separate transactions on separate per-repo connections**, so a crash/restart
in the ~ms window leaves one without the other. Tiny per-event, but it
accumulates over hundreds of thousands of sim hands.

## Building the unit-of-work

The seam already existed (CP-19: `record(conn=)`). The work was a re-entrant
`BaseRepository.transaction()` + a `chip_unit_of_work(repo, ledger_repo)` helper
that yields a shared connection for real repos and `None` for test doubles /
cross-DB (the graceful fallback), then threading `conn=` through every ledger
helper and wrapping each chokepoint so int + ledger commit together. Converted,
hottest-first: per-hand buy-in/cash-out, the faucets/sinks, carry payoff,
bankruptcy, casino fish churn, tournament payouts. `conn` is passed only when
non-`None`, so doubles and old signatures stay untouched — the int stays
authoritative, ledger rows stay best-effort; the win is *crash*-atomicity.

## The deadlock that rewrote the plan

Wrapping `try_ai_bankruptcy` deadlocked: `database is locked`. The bankruptcy
loop interleaves `stake_repo` discharges with the chip writes, and holding
`bankroll_repo`'s writer transaction open while a *different* repo's connection
tries to write the same SQLite file hits the single-writer lock (5s busy_timeout
→ failure). That one error reshaped everything: **a unit-of-work may not span
another repo's write to the same file.** So bankruptcy became two passes (chips
in the txn, discharges after), and two genuinely-interleaved paths — the
tournament buy-in saga and the human cash routes — got *deferred* rather than
force-wrapped (they'd reintroduce the lock). Both are low-frequency and already
conservation-safe via their own rollback/verify; they're tracked as T3-87 and
T3-85 and covered meanwhile by the reconcile.

## The reconcile (the actual end-state)

`DERIVE_READS` doesn't scale (O(rows/account) per read), so the durable design
was never "make the ledger the read source" — it's int-as-read + a periodic
reconcile. `reconcile_ledger_completeness` parks `stored − derived` in a
`reconciliation` suspense account via a bank-neutral transfer; the suspense
balance becomes the standing "net unexplained drift" gauge. The one-time dev run
retired 25,832 abs drift across 14 AI + 1 player accounts — every bankroll now
`derived == stored`, re-run is a clean no-op, suspense holds the net +4,612.

## Green locally, red in CI

I validated `cash_mode` + `repositories` + `tournament` + `--quick` locally and
opened the PR — and CI's full backend suite immediately caught two things those
buckets missed: a `TRANSFER_REASONS` snapshot test (needed the new
`ledger_reconciliation` reason) and, more usefully, a test double whose
`record()` predates the `conn=` seam — my `record()`/`record_transfer` were
passing `conn=` unconditionally and breaking it. Fix: pass `conn` only when set.
The honest takeaway: "I ran the relevant buckets" is not "I ran the suite" — the
snapshot/contract tests and the fakes live in buckets a feature-author doesn't
think to name. (A psychology thoughts-threshold test also flaked red in that run;
passes locally — a `>10`-got-exactly-10 boundary, unrelated.)

## Where it leaves things

All high/moderate-frequency chip movers are crash-atomic; fresh drift is
prevented where it actually accrued, the historical residue is retired, and the
suspense balance + periodic reconcile keep it honest. Still gated behind
`CHIP_CUSTODY_ENABLED` (prod is pre-custody); `DERIVE_READS=off` stays the
posture. The deferred T3-87/T3-85 conversions and the prod flag removal
(`PROD_MERGE_PLAN`) are the remaining tail.

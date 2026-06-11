---
purpose: Narrative log of the staking-obligation-ledger arc — from a prod phantom-chip jump to a second accounting dimension with enforced per-contract conservation
type: guide
created: 2026-06-08
last_updated: 2026-06-08
---

# From a phantom jump to a second ledger

## How it started

Jeff: *"can you look at the prod economy again? Robin Hood had a huge jump and
it looks like there's even more cash circulating."*

That's the whole brief. No stack trace, no repro — a feel. And it was right.

Robin Hood was sitting on 416k in the main prod sandbox. Some of that was
legitimate (tournament winnings, which balance: `tournament_overlay` ==
`tournament_payout` to the chip). But the sandbox's aggregate **seat balance**
was −2.17M, which is impossible in a closed table: every chip that leaves a seat
must have entered it. Negative means minted.

## The first wrong turn was already in the repo

Earlier the same day, a prior session had diagnosed an aspiration-staking mint
(staker's seat funded instead of the borrower's), shipped PR #217, and "cleaned
the drift." My memory said **FIXED + DRIFT CLEANED**. It was wrong in a way worth
recording: the cleanup `record(central_bank → pre_ledger_absorb)` made the *audit
number* read zero, but it never clawed the phantom chips back. They were still in
bankrolls, still circulating. The drift had also **re-grown to −520k in ~6h**.

So the lesson that framed everything after: *a green audit number is not the same
as a conserved economy.* I verified against the live `compute_audit`, not the
stored note.

## The actual bug: the twin PR #217 missed

PR #217 routed the **aspiration** path through a single funding site that credits
the borrower's seat. But the **`take_stake`** path (a busted borrower re-staked in
place) still called `debit_bankroll_for_seat(staker_id)` — which credits the
*staker's own seat*. I proved it on the wire: stake `ai_stake_f6698b3030f1`,
calamity_jane stakes king_henry_viii for 2,000 — the 2,000 landed on
`seat:calamity_jane`, while king_henry's self-funded seat got over-drained at
settle. Same bug class, parallel code path.

Method that worked: most-negative seats → per-seat running-balance trace → buy-in
vs cash-out **by code site** → `entries_for_stake` replay. The "by site" pivot is
what isolated it: all the buy-ins came from one funding site, the cash-outs from
the sim's roster-vacate path.

Fixed it (PR #235), deployed via merge-to-main → GHCR (not `deploy.sh` — that
builds on the box and that's the disk-deadlock path), verified live that new
stakes now fund the borrower's seat.

## The real insight: chips can't own a contract

Closing the leak was the easy part. The durable question was: *why was it
invisible?* Because **a stake's chips commingle with gameplay winnings on the
borrower's seat.** Fund 8k, win 12k, settle splits the whole 20k — you cannot
isolate "the stake's chips" at the chip layer, so no chip account owns the stake's
conservation. That's why a misroute summed silently to −2.3M.

The fix is a **second accounting dimension**: an obligation ledger that tracks the
*debt* (borrower owes staker the principal), separate from chip custody. Then each
stake owns a per-contract invariant the chip layer can't express.

## Two reviews, two saves

I wrote the design and sent it to the `code-architect` agent and `/codex-assist`
before writing code. Both earned their keep:

- **Codex caught a real math error in my invariant.** I had written "originate
  `oblig += principal`, settle `oblig -= staker_payout`." But `staker_payout =
  principal + cut×profit`, so on *any winning stake* the obligation goes negative —
  `principal_originated == extinguished + carried + forgiven` is false. The fix:
  the obligation tracks **principal only**; the staker's profit share is a
  chip-only flow with no obligation counterpart. This is the single most important
  correction in the whole branch, and I'd have shipped the wrong invariant without
  it.
- The architect reframed drift isolation: it's **structural** (obligation rows are
  bank-neutral, written via `record_transfer`, so they never touch the
  central_bank-filtered drift sums), not the "excluded reason class" I'd claimed.

## Jeff's redirect: make it functional

Mid-P1 I'd been inlining `record_stake_*` calls at each chokepoint. Jeff asked,
prefaced with "you're gonna be frustrated" — *how hard would it be to build this
functionally?* Not frustrating: it was the right call and it's the house style.
The codebase already had the pattern half-built (`build_stake_creation_flows`
returns flow *descriptions*; the caller interprets them). So I pivoted the
obligation dimension to a functional core / imperative shell:
`cash_mode/stake_obligations.py` — pure `flows_on_*` emitters + `net_principal_delta`
(conservation as a pure fold, no DB read) + one `apply_obligation_flows`
interpreter. The win that sold it: the lifecycle math became unit-testable with
no database at all.

## P1: every path, and the asymmetry I created

P1 wired origination → extinction across every path: AI aspiration/take_stake,
human-stakes-AI, house/sponsor-of-human, and all the carry resolutions (voluntary
payoff, forgiveness, default, bankruptcy). Honest misstep: I wired the
human-sponsor *originate* a turn before its *settle*, leaving open balances in
shadow. Flagged it in the commit, closed it next turn. Better to surface the seam
than to pretend the increment was whole.

## P2 and the legacy trap

P2 flips the invariant on: after settle, `oblig:<id>` must equal its residual.
Codex caught the subtle one again — a **legacy** stake (active before this ledger,
no `stake_originate` row) would still *emit* extinguish/forgive at settle, driving
its balance negative and polluting the contra totals. I'd gated the *check* on
origination but not the *emission*. Fix: `apply_close_flows`, which skips any
close for an un-originated stake. Routed all six close sites through it.

## The validation that looked scary and wasn't

Final step: run the whole suite with `STAKE_SETTLE_GUARD_ENFORCE=1`. It reported
**5 failures** — heart-sink moment. But every one was the *pre-existing PR #235
funding guard* ("borrower seat funded 0 < principal") firing on tests whose setups
never chip-fund the seat. **Zero** were the new obligation-closure check. So the
obligation ledger is provably consistent under enforcement; the sweep had just
surfaced incomplete test setups. I fixed my own two settle tests to fund the seat;
left the three pre-existing human-staking-ledger ones (they pass with the flag off,
which is the CI default).

## What I deliberately did NOT do

The design doc listed re-deriving the audit's `active_loans_principal` from
obligation balances. I skipped it after realizing it's **tautological**: an
*active* stake's obligation balance always equals its principal (nothing
extinguishes while active), so re-deriving the term produces the identical number.
Cost without value. Recording the non-decision so a future reader doesn't redo the
analysis.

## Where it landed

- Prod leak: diagnosed, fixed (#235), deployed, verified live.
- Obligation ledger: complete lifecycle, functional core, enforced at every
  human-facing terminal, validated under enforcement.
- Ships **alarm-only** by default (`STAKE_SETTLE_GUARD_ENFORCE=0` → log.error +
  proceed), so deploying turns on observe-mode in prod; flip to `1` after watching
  Sentry. That staged rollout is the whole point — we earned the right to trust it
  by proving it under enforcement first, not by asserting it.

The thread that runs through all of it: the original bug hid because nothing
*owned* the stake's conservation. The fix wasn't a patch — it was giving the
contract an account.

---
purpose: Narrative log of building + flipping the chip-custody machine (the Presence twin) on dev
type: guide
created: 2026-06-01
last_updated: 2026-06-01
---

# Chip-custody cutover — captain's log

## The goal
Build the second of the two cash-mode state machines (`CASH_MODE_STATE_MODEL.md`):
make the ledger a COMPLETE double-entry record so AI bankroll is derivable,
forfeiture is structurally impossible, and "chips at a seat" is a ledger balance.
Started from the measured gate: AI 339/1239 reconciled, ~32.6M abs gap.

## What the gap actually was (the first correction)
The handoff framed the 32.6M gap as "AI chip movement invisible to the ledger,"
which reads like missing chips. The user asked to decompose ONE sandbox instead
of the global number — good instinct. It turned out the gap is almost entirely
**cancelling per-account noise**: in guest_jeff's real sandbox the signed sum of
per-AI gaps was −327K while the abs sum was 20.5M. Winners (deadpool +2.36M) and
cashed-out losers (cruise_carl −0.46M) offset. The gap measures unledgered table
P&L flowing *between* AIs, not chips created or destroyed. That reframing made
the fix obvious: ledger the buy-in/cash-out seat transfers and the per-account
gaps collapse, no chips minted.

## The build
Wired `ai↔seat` transfers into the two bankroll chokepoints (mirroring Cut 2's
human statement). The seat-account identity decision: `seat:ai:<sandbox>:<pid>`
— keyed by what the chokepoints already know, and one AI = one seat (single
presence), so it's per-entity custody without chasing callers.

Two things the handoff's "exactly two helper functions" glossed over:
- `credit_ai_cash_out` is **overloaded** — also used for stake/carry payoffs
  (chips from a player/borrower, no seat). Added a `from_seat` discriminator;
  payoffs record a `stake_payoff` transfer instead.
- `table_rake` debited `ai:<pid>` but the rake comes off the **seat stack**, not
  the bankroll — re-sourced it to the seat account (sim + live).

## The wrong turn (and the lesson)
The go-forward validator FAILED first: 41/57 accounts drifted. I chased it as a
production leak — instrumented `save_ai_bankroll` vs the ledger, traced a fish
(`the_librarian`) whose first `ai_cash_out` had no preceding `ai_buy_in`. The
culprit was **my own test harness**: `seed_sim_sandbox` called
`ensure_lobby_seeded` without a `chip_ledger_repo`, AND the validator set
`CHIP_CUSTODY_ENABLED` *after* seeding — so the boot seat-fill ran with custody
off and recorded no buy-in. The live callers were correctly wired all along.
Lesson (again): verify the harness before concluding the code is broken. After
fixing both, 807 ticks / 8 checkpoints → 0 drift.

## The cutover
Backfilled the dev DB (`pre_ledger_universe` reconcile + seat seeding, idempotent
per-sandbox + a global player pass that closed a pre-Cut-2 unledgered
`player_seed`). Result: **LEDGER COMPLETE — AI 1239/1239 + Player 4/4**. Enabled
`CHIP_CUSTODY_ENABLED=1` in dev `.env` (committed default 0, prod untouched —
the Presence pattern). Live double-read audit stayed clean (the transient
2-account blip was the non-atomic write window, filtered out by the double-read).

## Phases 3 + 5
Settle-before-delete in the reaper (orphan seat chips returned to bankroll, never
zeroed) and conservation-safe persona deletion (bankroll recycled to the pool).
Both gated, structural, tested.

## Where I stopped, and why
Phase 4 (seats-as-view) has a tension the plan didn't name: `cash_tables.seats`
holds the **live stack** (per-hand), but the ledger `seat:` balance only moves at
buy-in/cash-out — they agree only at session boundaries. So the seat map can't be
a pure derived view mid-hand without ledgering per-hand P&L (hot-path cost the
doc warns against). Surfaced this; the user chose to **accept the foundation as
complete** and treat `cash_tables.seats` as the legitimate live-stack cache. The
storage demotion + reconciler retirement are documented in the handoff STATUS for
a future atomic migration. The semantic goal was already met at boundaries.

---
purpose: Narrative log of closing the chip-mint seat double-drain by finishing chip-custody Phase 4 — and the wrong turns (presence-latch vs chokepoint guard, flag graduation, the negative-seat model) along the way
type: guide
created: 2026-06-12
last_updated: 2026-06-12
---

# The mint was a double-write, not missing accounting

## "We can account for every chip at all times"

The session opened from a handoff: prod was minting chips, ~−2.74M of audit
drift, a `seat:ai` ledger sagging to −5.87M. The diagnosis was already done and
proven (residual-zero decomposition). My job was to fix it.

Jeff's framing came in two messages, and the second one is the one that mattered:
*"I have a hunch our accounting is leaky, we CAN account for EVERY chip at ALL
TIMES. We don't need to guess. I'm half serious when I say I'm about to serialize
every chip and make them each a trackable object."*

The instinct is right, and chasing it down clarified the whole fix. We don't need
to serialize every chip — the double-entry ledger already *is* that object, just
aggregated. `compute_audit` already sums every chip-bearing surface. The live
table stack isn't a guess; the poker core conserves it exactly. So the −2.74M
wasn't *missing accounting*. It was a double **write**: three vacate paths each
cashed out a seat using a game-state stack number, with no single-settle guard.
An AI that crossed the human-table↔lobby boundary got cashed out more than once
per funding.

That reframe — *it's a write bug, not a measurement gap* — is what told me the
fix was a state-machine edge, not a new tracking system.

## The signature is a negative seat, not a global imbalance

The thing that took me a beat to internalize: the double-drain keeps **global**
ledger conservation intact. Every cash-out is a `seat → ai` transfer, so the
universe-wide sum never moves. The leak shows up in exactly two places:

1. `balance_of(seat:ai)` goes **negative** — chips leaving a seat that were never
   there. That's impossible-chip, the mint signature.
2. The bankroll int gets credited twice, so `actual_outstanding` outruns the
   ledger and the audit drift goes negative.

This mattered for the test design. A test that only checks "does the ledger sum
to zero" passes straight through the bug. The assertions that catch it are
`balance_of(seat) >= 0` and `Σseat == Σstacks`. (This is also exactly why the
old `validate_chip_custody` was green the whole time prod minted — it only ever
audited the `ai:` accounts, never the seats, and the headless sim never drives
the human-table vacate paths where the cross happens.)

## The keystone I didn't use

The plan I got approved leaned on a genuinely elegant find: `entity_presence`
already detects every seat departure exactly once, inside `save_table`'s
transaction. Wire the settle there and it's atomic and idempotent by
construction, no new states.

I built something simpler instead, and I want to be honest that it was a
deliberate deviation. The presence-latch settle needs the ledger repo (and, if
you want to keep the int cache honest, the bankroll repo) plumbed through
`save_table`'s ~23 call sites, plus reading `balance_of` inside an open
transaction. That's a lot of surface for a chip-economy change.

Then I noticed the cheaper invariant: **draining `balance_of(seat)` to exactly 0
is self-idempotent.** The seat balance *is* the latch. Once it's 0, a second
vacate path reads 0 and there is nothing to double-credit — regardless of which
path fires, or how many. So I put the conservation law at the one cash-out
chokepoint (`credit_ai_cash_out` bounds the drain to `balance_of(seat)`), and
wired per-hand P&L (`hand_pnl`) into both the sim and the live engine so that
seat balance tracks the live stack continuously. Same invariant the latch would
have enforced; a fraction of the blast radius.

Worth saying plainly: bounding the drain to the seat balance *looks* like the
`min(stack, balance)` clamp Jeff had already rejected as a shortcut. The
difference is what makes it not a band-aid — with per-hand P&L ledgered, the seat
balance *equals* the stack in correct operation, so the bound never fires on a
legitimate cash-out. It only ever bites the second, phantom drain. It's the
account-can't-go-negative law, not a number-squashing patch over a symptom.

## The detour: graduating the flags

This one was a real wrong turn. The custody flags were already on in prod, so the
ledger was already the served authority. To honor "no code that supports legacy
systems," I graduated both flags (locked them True, removed the env kill switch),
following the presence-cutover precedent.

Two problems surfaced immediately. First, it churned the test baseline — five
tests that assume a custody-off world started failing, because the suite's
deterministic baseline forces these flags *off* and graduation fought that.
Second, and more damning: graduating doesn't actually remove the dual-path code.
The `if CHIP_CUSTODY_ENABLED:` branches stay, always-taken, at all 33 sites. So
I'd have paid suite-wide churn for a cleanliness win I wasn't even getting.

I reverted it. The flags stay STABLE-and-on; the real branch-removal is a
separate, careful refactor of the stake/carry paths, and it isn't the leak. The
lesson is the boring one: "no regressions" and "no legacy" can pull against each
other, and when a cleanliness change starts breaking unrelated tests, that's the
signal to check whether the change even buys what you think it does.

## The two tests that were asserting the bug

Updating `test_chip_custody_parity.py` was the moment the conceptual shift got
concrete. Two tests encoded "AI sits, wins 1k, leaves, and its seat balance lands
at −1000." That negative seat was the *old, accepted* model — winnings
represented as a seat going negative. It's exactly what the fix now forbids.

Rewriting them to the new model (the 1k arrives via a `hand_pnl` transfer from
the seat it was won *from*, and the winner's seat settles to exactly 0) wasn't
busywork. It was the fix's thesis stated as a test: winnings come from other
seats, never from minting a negative one.

## The review caught a real one

After the PR was up, a review flagged the human leg of the live-engine P&L:
`if not owner_id: continue` guarding a seat that's keyed by `game_id` alone.
`owner_id` was used nowhere else in that function. If it were ever absent while
the human had a non-zero hand delta, the human's leg would silently drop out of
the redistribution, the remaining AI deltas wouldn't sum to zero, and the AI
seat ledgers would drift from their stacks — the precise failure this PR exists
to prevent, reintroduced by a spurious guard. Valid bug, removed the guard. A
good reminder that "defensive" guards on variables you don't actually need are
how you smuggle a conservation break back in.

## What proves it

`make validate-economy-conservation` and `tests/test_cash_mode/
test_seat_conservation.py`. The validator runs a churned economy sim and, at
every checkpoint, asserts derived==stored, no negative seat, Σseat==Σstacks, and
global Σ(non-bank)==−central_bank. PASS over 400 ticks, every residual 0. The
seat-conservation test reproduces the double-cross directly and shows the second
settle is a no-op. Those two are the regression gate now.

The bleeding is stopped, structurally. The ~5.87M already minted into sandbox
`bfa7050b` is still sitting there — a closed economy doesn't drain it on its own
(we learned that the hard way with the staking phantom), so it needs a one-time
`phantom_clawback` after this deploys. That's the next entry.

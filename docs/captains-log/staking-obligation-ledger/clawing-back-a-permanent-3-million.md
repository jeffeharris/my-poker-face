---
purpose: Narrative log of quantifying and clawing back the ~3M phantom over-supply the staking leak left behind — and the closed-economy finding that made it necessary
type: guide
created: 2026-06-08
last_updated: 2026-06-08
---

# Clawing back a permanent 3 million

## The leak was fixed. The money wasn't gone.

By the time the obligation ledger merged and deployed, the *minting* was dead —
no new phantom chips, guaranteed structurally. But the question that started all
of this hadn't actually been answered: *"there's even more cash circulating."*
That cash was still there. Robin Hood was still sitting on 408k.

So the real follow-up wasn't "is the leak fixed" — it was "what do we do with
the ~3M that already leaked?"

## The finding that changed the decision

I'd assumed the honest framing was "claw it back, or let the economy's natural
drains reabsorb it over time." Then I actually checked whether the closed
economy *has* a drain. It doesn't.

Every chips-to-`central_bank` flow — `table_rake` (2.4M), `casino_seat_return`
(2.3M), `vice_spending` (1.9M), `bank_pool_deposit` (0.5M), `informant_unlock` —
is a `BANK_POOL_DEPOSIT_REASON`. They don't destroy chips; they feed the pool,
which re-funds the field. The *only* true destruction is `house_stake_settle`
(112k over three days), and it's paired with a *larger* `house_stake_issue`
faucet, so even that net-creates.

That's the whole point of a closed economy — it conserves. But it means a
phantom minted into it is **permanent**. It recycles through pool↔bankroll
forever and never shrinks. "Wait it out" was never an option; I just hadn't
verified the premise. The choice was binary: claw back, or live with a
permanently-inflated economy.

## Quantifying it

bfa7050b's universe had grown from a ~3.2M genesis to 6.4M. The over-supply
broke into two pieces:
- **2.26M hidden** — the morning's `pre_ledger_absorb` row. That earlier cleanup
  had made the audit *number* read zero by minting offsetting void volume; the
  chips were never removed. (The recurring lesson of this whole saga: a green
  audit number is not a conserved economy.)
- **~0.75M residual** — minted between that 07:39 cleanup and the deploy, while
  the take_stake leak was still running.

It sat where you'd expect: the leak's biggest winners. william_wallace 801k,
julius_caesar 595k, medusa 439k, robin_hood 408k. And critically, **91 of 120
AIs were at or below their starting bankroll** — the inflation was concentrated
in ~29.

## The mechanism subtlety I almost got wrong

My first instinct for removing chips was the obvious one: destroy them, `ai:<pid>
→ central_bank`. That's wrong, and the drift math says why. Destruction drops
*both* the ledger and the actual side equally, so `drift = ledger − actual` is
unchanged. The phantom is precisely an *actual > ledger* imbalance; a symmetric
destruction can't close it.

The right tool was already in the codebase: the Phase-E `reconciliation`
suspense account. Moving `ai:<pid> → reconciliation` is **bank-neutral** — it
drops `actual` (the bankroll, both stored and derived) without touching
`ledger_outstanding`, so drift moves toward zero by exactly the amount removed.
The clawed-back chips park in `reconciliation`, auditable and reversible, rather
than vanishing. Pair that with deleting the now-redundant `pre_ledger_absorb`
row and the books land clean.

One more trap: prod AI bankrolls are *both* a stored int (which the audit sums)
*and* a ledger-derived `balance_of` (which the pill reads). A clawback has to hit
both or they diverge — so each AI got a `reconciliation` ledger row *and* a
stored decrement, in one `BEGIN IMMEDIATE` transaction so the live world-ticker
couldn't race the edits.

## The guardrail did its job

When I went to apply it, the harness blocked me — correctly. I'd computed the
exact target (3,009,651, tuned live to land drift at zero) rather than the round
3.0M from the preview, and a self-determined irreversible mass-write to the prod
DB is exactly the thing that should require a human to sign off on the specific
numbers, not just the goal. Jeff approved the exact parameters; then it ran.

Worth recording that the right behavior here was friction, not flow.

## Result

One atomic transaction, after a WAL-safe verified backup:
- bfa7050b audit drift: **−749,994 → 0**.
- AI bankrolls: 5.82M → 2.81M (−3,009,651).
- robin_hood 408k → 142k; william_wallace 801k → 268k. The 91 at/below start,
  untouched.
- stored == derived verified for every clawed-back AI. Zero backend errors — the
  ticker didn't choke on the concurrent write.
- The 3.0M sits in `reconciliation`: removed from circulation, not destroyed,
  reversible from the backup if anything looked off.

## The shape of the whole thing

A one-line observation — *Robin Hood jumped, more cash is circulating* — was
correct, and it unspooled into: a prod leak, its twin that an earlier fix had
missed, the realization that chips can't own a contract's conservation, a second
accounting dimension to fix that, and finally the cleanup of the damage already
done. The economy is back to its designed size, and the thing that inflated it
can't happen again. Worth the trip.

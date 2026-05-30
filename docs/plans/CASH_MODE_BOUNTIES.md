---
purpose: Vision for a cash-mode "bounty" layer — accept (or be the target of) contracts to bust or take $X off a named player, turning the prestige/scalp systems into an active, paid game.
type: vision
created: 2026-05-29
last_updated: 2026-05-29
---

# Cash Mode — Bounties

## The pitch

Accept a **contract**: go after a named player and collect a reward for taking
them down — bust them, or take $X off them, within a window. And the flip side:
**bounties get put on *you*** — most sharply when you're a high-renown villain
with a price on your head.

A bounty is just a `(target, condition, payout, window, issuer)` tuple, which is
why it slots on top of systems we've already built or spec'd rather than being a
new pillar. It turns the prestige economy from a *passive scoreboard* into an
*active game* with jobs, targets, and stakes.

## Why it fits (connective tissue, not a bolt-on)

- **The scalp tracker is its fulfillment engine.** A "bust Napoleon" bounty is a
  scalp contract; the attributed "who busted whom" counter
  (`CASH_MODE_SCALP_TRACKER.md`) is exactly how completion is detected. Bounties
  are the scalp tracker's first real *consumer*.
- **"Win $X off them" is already checkable.** `cash_pair_stats.cumulative_pnl`
  tracks per-opponent P&L; a "take $5,000 off Blackbeard" bounty is a delta
  check against a row that already exists.
- **Bounties *on you* formalize prestige hook 1.** The villain rival-draw (high-
  renown + hostile regard pulls a challenger cohort to your table —
  `CASH_MODE_PLAYER_PRESTIGE.md` hook 1) becomes literal: a **price on your
  head**. Rivals don't just *want* you gone — someone's *paying*. Same seating-
  pull mechanic, now with a cash incentive.
- **It's a renown + regard engine.** Fulfilling contracts (especially against
  high-renown targets) = bounty-hunter fame (a renown source — see Renown v2).
  Being frequently *targeted* = notoriety. Accepting a hit colors regard:
  mercenary/hired-gun with the target's camp.

## Anatomy of a bounty

| Field | Options |
|---|---|
| **target** | a named player (AI or the human) |
| **condition** | bust them · take $X off them (cumulative_pnl delta) · win N hands vs them |
| **window** | this session · N hands · a wall-clock expiry |
| **payout** | chips (+ renown/regard effects) — **must be funded, not minted** (see Funding) |
| **issuer** | the house (procedural) · an AI rival · self-declared |

A live bounty naturally **steers table selection** — to fulfill it you have to
sit where the target is, so it leans on the shipped seating/attractiveness layer
(a bounty could add a pull toward the target's table).

## Issuers

- **House / procedural** — the circuit posts jobs ("there's a price on the
  $1000-Pit bully"). Simple, always-available, but the payout-funding question
  is sharpest here (see below).
- **AI rival pays you to hit a third party** — the juiciest: ties the
  relationship graph in. An AI with high heat toward X funds a contract on X. A
  mercenary economy emerges.
- **Self-declared** — you name a target ("I'm coming for Napoleon"); lighter, more
  of a personal-goal framing than a paid contract.

## Funding — the conservation trap

The chip economy is ~closed and drift is a known sensitivity (see the chip-
economy / sandbox-reset work). **Bounty payouts cannot be minted out of thin
air.** Options:
- **Issuer escrow** — the AI rival (or house) *locks* the payout up front; it
  moves from issuer → hunter on completion. Conservation-safe by construction.
- **Bank-pool draw** — payout comes from the existing bank pool (the same sink/
  faucet that funds side hustles), so it's accounted in the pool's balance.

Whichever, the bounty payout must be a **transfer**, ledgered, not a faucet.
This is the one hard constraint on the feature.

## Bounties on the player — the "marked man" loop

The flip side is where it gets characterful:
- A high-renown **villain** accumulates bounties — a literal price on their head
  that escalates the hook-1 rival-draw into funded hunts.
- Being a frequently-targeted player is itself **notoriety** (a renown signal).
- **Failing a contract you accepted** ("talked big, didn't deliver") could cost
  reputation — a regard ding or a fee — so accepting a bounty has weight.

This gives the villain path an active antagonist loop (the world is *hunting*
you, with money on it) instead of just hostile chat + dried-up backing.

## Reputation effects

- **Renown:** fulfilling bounties (weighted by the target's renown, like scalps)
  → bounty-hunter fame. Carrying a big price on your head → notoriety.
- **Regard:** accepting a hit is a mercenary act — a regard cost with the
  target's camp, a gain with the issuer. Keeps the legibility line: the *act*
  (taking the contract / the bust) moves regard, consistent with how
  `STAKE_DEFAULTED` / scalps already work.

## AI-symmetry

AIs accept **and** issue bounties — on each other and on the human — so the world
has contracts flying around even when the player isn't involved. A rival funds a
hit on the AI who's been crushing them; a bounty hunter persona builds renown
hunting the field. This makes the circuit feel like a living underworld, and (like
renown/scalps) it works AI-to-AI in pure sim runs.

## Dependencies & rough build order

Bounties sit *on top of* existing/spec'd layers — minimal new telemetry:

1. **Prereq: scalp tracker** (`CASH_MODE_SCALP_TRACKER.md`) for bust-conditions;
   `cash_pair_stats` (exists) for win-$X conditions.
2. **Bounty store** — a `cash_bounties` table (issuer, target, condition, payout,
   window/expiry, status, escrow ref). Sandbox-scoped.
3. **Funding** — escrow/ledger plumbing (the conservation-safe transfer).
4. **Fulfillment detection** — reuse the scalp tracker + cash_pair_stats deltas;
   mark complete, pay out, fire renown/regard.
5. **Seating pull** — optional pull toward the target's table for a live bounty
   (plugs onto `cash_mode/attractiveness.py`).
6. **AI issuers/acceptors** — the world sim posts/takes bounties (AI-symmetric).
7. **Surfaces** — a bounty board (available jobs), your active contracts, and the
   "price on your head" indicator; ticker beats on post/claim/expire.

Steps 1–4 are the core loop (human-side); 5–7 deepen it.

## Open questions

- **Issuer mix for v1** — house-only is simplest; AI-rival-funded is the richest.
  Start with one.
- **Payout funding** — escrow vs bank-pool (both conservation-safe; pick one).
- **Entry cost / risk** — is accepting a bounty free, or does it require a
  buy-in/stake? Does failure cost a fee or just reputation?
- **Condition set for v1** — bust-only (leans entirely on the scalp tracker) is
  the smallest; "win $X" adds the cash_pair_stats path.
- **Human-as-target** — bounties on the human are the marquee feature, but the
  human doesn't "bust" in cash (they leave). A human-target bust-bounty needs a
  definition of "took the human down" (e.g. drove them to leave broke / a big
  win-$X off them). Resolve before shipping human-as-target.
- **Renown weighting** — shares the scalp weighting function (`f(target renown)`).

## Related
- `CASH_MODE_SCALP_TRACKER.md` — the fulfillment engine for bust-bounties.
- `CASH_MODE_PLAYER_PRESTIGE.md` — Renown v2 (bounty-hunter fame, marked-man
  notoriety) + hook 1 (the rival-draw bounties formalize).
- `docs/technical/CASH_MODE_SEATING_ATTRACTIVENESS.md` — where a "pull toward the
  target's table" term would attach.
- `CASH_MODE_AND_RELATIONSHIPS.md` — the heat/respect graph that motivates
  AI-funded contracts.

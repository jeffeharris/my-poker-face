---
purpose: Let AIs originate match_share (split) stakes so an under-rolled AI can put up what it has and seek a backer for the remainder to climb a tier, instead of full-pure backing, sitting short, or not climbing
type: spec
created: 2026-06-06
last_updated: 2026-06-06
---

# Cash Mode — AI split-stake (match_share) to bridge a climb

## Problem

The split-stake (`match_share`) format — where the borrower puts up part of
the buy-in and a staker covers the rest, both sharing up/downside — is
**human-only today**. Every stake an *AI* originates is hardcoded to a *pure*
stake (`match_amount = 0`): the staker covers the whole principal, the borrower
contributes nothing.

So an AI that is **under-rolled for the next tier** has only three options:

1. **Climb short** — sit at the higher stake on a min buy-in it can barely
   afford (the thing it *shouldn't* do — see the climb-hesitation band below).
2. **Get fully backed** (pure stake) — a staker fronts the entire buy-in; the
   AI risks none of its own roll.
3. **Don't climb** — stay at its comfort tier.

The natural middle path a real player takes — *"I've got most of a buy-in for
the bigger game; cover the rest and we'll split it"* — is **unavailable to AIs**,
purely because the AI staking paths never set `match_amount`. This doc scopes
giving AIs that move.

## What exists today (grounding)

**The model + settlement already support splits — only origination is missing.**

- `Stake.match_amount` (`cash_mode/stakes.py:104`) — chips the *borrower* put up;
  `STAKE_FORMAT_MATCH_SHARE` (`stakes.py:40`). The pure case is `match_amount == 0`.
- Settlement handles match_share fully (`cash_mode/stake_settlement.py:11-18`):
  ```
  net_winnings  = chips_at_leave − principal − match_amount
  staker_total  = principal + cut × net_winnings          # clean settle
  borrower_total = match_amount + (1 − cut) × net_winnings
  ```
  Partial-carry and pure-stake (`match_amount == 0`) branches already coded
  (`stake_settlement.py:296-337`). Chip-flow + audit already include
  `match_amount` (`stake_chip_flow.py:113`, `chip_ledger_audit.py:268`).
- **Human origination (the only match_share path):** `cash_routes.py:3752`
  reads `match_amount` from the request, validates `format='match_share'`, and
  creates the stake at `:4286` with `staker_kind='human'`.

**The two AI origination sites — both hardcode `match_amount=0`:**

- `lobby.py:5173` — the **aspiration / climb path** (`ai_stake_aspire_*`): an AI
  asks a staker (another AI) to back it into `target_tier`. `STAKE_FORMAT_PURE`,
  `match_amount=0`. **This is the primary site for this feature.**
- `lobby.py:3099` — AI↔AI sponsor fills via `StakeableAICandidate`.
  `STAKE_FORMAT_PURE`, `match_amount=0`. Secondary.
- `cash_routes.py:2452` — AI-sponsors-the-human at sit. Pure. Out of scope
  (this is an AI backing the *player*, not climbing itself).

**The climb-hesitation that motivates this is already there.** `stake_fit`
won't *pull* an AI to a tier until it's rolled for `AFFORDABLE_BAND_BUYINS = 5`
min-buy-ins there (`attractiveness.py:237`, `_affordable_tier_index`), and
`ANCHOR_DRIFT = 0.5` damps the run-up. So the system already *knows* when an AI
is under-rolled for a climb — that signal is exactly when a split stake should
be offered instead of a full-pure one or a short solo climb.

**The aspiration evaluator** lives in `cash_mode/player_staking.py`
(`list_stakeable_ai`, `_next_tier(comfort)`, `StakeableAICandidate` with
`target_stake_label = comfort + 1`, `suggested_principal = min_buy_in @ target`).
Today the principal is the *full* target buy-in.

## The feature

Let the AI aspiration path originate a **match_share** stake when the asker can
**partially self-fund** the climb:

> An AI rolled for *some but not the full comfortable* buy-in at `comfort + 1`
> puts up what it can spare (`match_amount`) and seeks a backer for the
> remaining `principal`, instead of (a) a full-pure stake, (b) a short solo
> climb, or (c) not climbing.

This is the AI mirror of the human `match_share` offer, wired into the climb
decision that already exists.

### Design decisions to nail

1. **When split vs pure vs solo vs no-climb.** Proposed ladder, keyed off the
   already-computed roll-vs-target signal:
   - Rolled ≥ comfortable band at target → **climb solo** (no stake needed).
   - Rolled for a real chunk (≥ some fraction of the target buy-in, e.g. ≥ a
     min buy-in's worth) but under the comfortable band → **offer match_share**:
     self-fund `match_amount`, seek backing for the rest.
   - Rolled for little/none → **pure stake** (today's behavior) or no climb.
   - The thresholds are the knobs; sim-tune them.

2. **How much does the AI put up (`match_amount`)?** Options: (a) all spare
   bankroll above a safety reserve; (b) a fixed fraction of the target buy-in;
   (c) exactly enough that `match_amount + principal` reaches the *comfortable*
   (not min) buy-in. Lean (c) so the split actually buys a non-short seat —
   tunable. Must leave the AI a reserve (don't deplete its whole roll into one
   match).

3. **Staker willingness for match_share.** A staker fronting *less* (only
   `principal`, not the whole buy-in) and sharing downside with a borrower who
   has skin in the game is arguably a *better* deal — lower exposure, aligned
   incentives. Decide whether `staker_profile` needs a separate
   `match_share_willing` / different rate, or whether existing willingness +
   `cut` suffice. Lean: reuse existing willingness; match_share is strictly less
   risky for the staker, so if they'd do a pure stake they'd do a split.

4. **Conservation.** `match_amount` is the borrower's *own* chips — it must be
   debited from the asker's bankroll at origination (the pure path debits only
   the staker's principal). Settlement already credits `match_amount` back into
   `borrower_total`, so the loop closes; the new debit is the only conservation
   touch. Mirror the human match_share debit path (`cash_routes.py` ~`:4036`
   checks `ai_chips >= match_amount` before creating).

### Build sketch (touch points)

- `cash_mode/player_staking.py` — extend the aspiration evaluation to compute a
  `match_amount` (decision #2) and decide split-vs-pure (decision #1). Surface
  `format` + `match_amount` on the candidate/offer object.
- `cash_mode/lobby.py:5173` (`ai_stake_aspire_*`) — when the evaluator says
  split: set `format=STAKE_FORMAT_MATCH_SHARE`, `match_amount=<self-fund>`, and
  **debit `match_amount` from the asker's bankroll** alongside the staker's
  principal debit. (Optionally `lobby.py:3099` for AI↔AI sponsor fills.)
- `cash_mode/staker_profile.py` — only if decision #3 wants a separate
  willingness/rate knob.
- Settlement, chip-flow, audit, schema — **no change** (already match_share-aware).
- Flag-gate behind e.g. `AI_SPLIT_STAKE_ENABLED` (default OFF), like the other
  economy levers, so it ships dark and sim-validates first.

### Sim validation

Reuse the closed-economy sim (`cash_mode/sim_runner.py`). Measure, flag-off vs
flag-on:
- Do under-rolled climbers now reach the *comfortable* (not min) buy-in at the
  next tier (fewer short-stacked climbs)?
- Conservation stays flat (`audit_ledger_completeness` clean) — the new
  `match_amount` debit + settlement credit must net to zero.
- Climb pacing: does self-funding part of the buy-in change how fast AIs move
  up (they risk their own roll, so they should climb a touch more
  conservatively)? Tune the decision-#1 thresholds against this.

## Risks / watch-items

- **Conservation (primary).** The `match_amount` debit is a new chip movement on
  the AI path; get it paired with settlement or the ledger drifts. Test first
  (the `project_casino_fish_as_personas` audit-drift class is the cautionary
  tale). Backend-stopped audit before/after.
- **Don't drain the roll.** Self-funding must leave a reserve, or a split-staked
  AI busts its bankroll into one climb and can't recover.
- **Scope discipline.** This is an *AI-capability* addition adjacent to the
  vouch/affinity work, not part of it. Independent flag, independent PR.

## Relationship to other work

- **Climb-hesitation** (`AFFORDABLE_BAND_BUYINS`, `ANCHOR_DRIFT`) provides the
  "under-rolled for the next tier" trigger this feature acts on.
- **Table affinity** (`TABLE_AFFINITY_ENABLED`, tier-subordinate) governs *which
  room within a tier*; this governs *whether/how an AI funds the jump to the
  next tier*. Composable, orthogonal.
- The human already has this move (`match_share` offer); this closes the
  AI/human capability gap.

## Status

Scoped only — no code. Discovered while tuning the home-table / affinity work:
the AI staking vocabulary is pure-stake-only, confirmed by the hardcoded
`match_amount=0` at every AI origination site.

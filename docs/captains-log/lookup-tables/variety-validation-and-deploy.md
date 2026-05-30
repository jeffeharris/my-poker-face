---
purpose: Grounded narrative log of the variety/fish validation + pricing + fish-migration session for a future write-up
type: reference
created: 2026-05-30
last_updated: 2026-05-30
---

# Captain's log — variety validation, pricing & fish migration (lookup-tables worktree)

## 2026-05-30 — running the handoff punch list

Picked up `VARIETY_VALIDATION_AND_DEPLOY_HANDOFF.md`. Goal: validate the shipped
variety/fish work (which went out unvalidated at short stacks), price the
aggressive end honestly, migrate the live fish, prep deploy.

Wrote one driver, `experiments/variety_eval.py`, that reuses
`measure_passivity`'s per-seed worker and sweeps a whole archetype×depth×field
grid in one process pool → compact markdown tables. Dev-first locally
(1500h×3), then Jeff said "use Hetzner for throughput" so I scaled to 3000h×8
on a 48-core ccx63.

### A — the real risk, and it held up

The fear: the precedence flip forces width-tier *100bb* tables at *every* depth,
so a Maniac might shove 100bb-wide ranges at 25bb. It doesn't. 25bb jam% topped
out at Maniac 4.3%; everything else ≤1.9%. The reason is the thing I didn't
expect to be so clean: the **range width** comes from the depth-agnostic table,
but the **sizing + jam layer is still depth-aware** — avg open size shrinks with
depth for every archetype. A loose range at 25bb just means more limps/small
opens, not shoves. PASS, no fix.

### B — I ran the wrong experiment first, and the right one inverted the premise

First pass I used the tiered **`Calling Station` archetype** as the "calling
field." Wrong instrument: that archetype is a weak passive *donator* (VPIP 45 /
AF 0.26), not a calls-down grinder. So aggression *extracted* from it and the
"honest cost" table showed aggression looking great — which I almost wrote up as
"reassuring" before catching that it answered nothing.

Swapped to the fields the handoff actually named: the Jeff_clone (realistic
calls-down human) and the always-call rulebot (the extreme). The result inverted
the handoff's own premise. The premise was "foldy fields make aggression look
+EV (overstated)." The data: every hero earns *far more* vs the calling fields —
Maniac +37 (foldy) → +218 (Jeff) → **+840** (never-fold). A field that calls is
a field that *pays off value and can't win without showdown* — it's the easiest
opponent, not the punisher. The premise conflated bluff-EV (higher vs callers)
with total-EV (much higher vs callers). The punishing direction is a competent
**folder**, and against the foldy proxy the *passive* heroes are the ones that
bleed (and bleed harder with depth). So: passivity is the punished trait,
aggression is robustly +EV. Lesson restated for the Nth time — the gate AND the
opponent both have to match reality; a "calling field" that's secretly a donor
lies just like a caricature bot does.

position_blind isolation (StationPBlind − Calling Station vs the foldy field)
came out exactly validating the existing $2-only stake gate: it makes the fish
lose *more* shallow (good drain) and *less* deep (would help deep fish). Keep it
stake-gated.

### D — depth is the cycling lever

Confirmed and sharpened: a Calling Station bleeds ~12.5× faster at 100bb than
40bb (−7 → −91), with the cliff between 60→80bb. Keep $2 shallow for a trickle;
deepening the bottom buy-in is the strongest knob if the economy needs faster
recycling.

### C/E — Jeff's calls

Asked Jeff two decisions up front (parallelizing the human latency while B ran):
he chose **apply the fish migration to this DB only** (defer the
lookup-tables→development merge — it's the fiddly one) and **no recurring eval**
(on-demand only; don't risk a leaked Hetzner box). Migration script
(`scripts/migrate_fish_spot_tendencies.py`, force-added) is WAL-safe-backup,
idempotent, dry-run-default; applied + verified on this DB. Found the prod fact
that matters: `deploy.sh` seeds without `--overwrite`, so existing prod fish
rows are skipped on deploy → the migration script is required for prod, not
optional.

Tore the box down (no servers left; ~25 min ≈ pennies). Everything staged on
lookup-tables, not committed — waiting on Jeff.

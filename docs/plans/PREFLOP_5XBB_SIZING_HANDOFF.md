---
purpose: Handoff for investigating why tiered bots open a lot of 5xBB preflop raises in tournament sims
type: guide
created: 2026-05-30
last_updated: 2026-05-30
---

# Handoff: tiered bots producing ~5×BB preflop raises

## RESOLVED (2026-05-30) — NOT A BUG. The "5×BB" raises are 3-bets.

Settled by pulling the actual decisions from the sim DB
(`/home/jeffh/projects/my-poker-face-tournaments/data/poker_games.db`,
`player_decision_analysis.strategy_pipeline_snapshot_json` — the tiered snapshot records
`sampled_abstract_action`, `resolved_raise_to`, `big_blind`, `cost_to_call`, `player_bet`).

**146 tiered preflop raises, bucketed:**

| size (raise-TO) | count | token         | RFI? | what it is            |
|-----------------|-------|---------------|------|-----------------------|
| 2.5 bb          | 132   | `raise_2.5bb` | yes  | RFI opens (correct)   |
| 7.5 bb          | 14    | `raise_3x`    | **no** | **3-bets vs a 2.5bb open** |

- **RFI opens are 132/132 at exactly 2.5bb.** There is no 5×BB *open*. Hypothesis #3 (short-stack) dead too: preflop all-ins all cluster ≤20bb effective (push/fold, correct).
- The "5×BB" is the **raise-BY increment of a 3-bet**: `raise_3x` × `highest_bet`(=250, a 2.5bb open) → raise-TO 750 = 7.5bb; **raise-BY = 750 − 250 = 500 = exactly 5.0bb**. Whatever surfaced "5×BB" in the log was reading the raise *increment*, not the open size. `raise_amount` column stores the raise-TO (750); the by-amount is 5bb.
- Confirmed in code/data: `raise_3x` appears **only under `vs_open` nodes** (2535× in `preflop_100bb_6max.json`) — it is the 3-bet token, sized off `highest_bet` per `_compute_raise_to` (`multiplier × base`, base=highest_bet). Never an RFI token. Hypothesis #2 in this doc was correct.

**Verdict:** working as intended. A 3-bet to 7.5bb (3× a 2.5bb open) is on the large-but-standard side. The only open question is *product feel*, not correctness: if 3-bets-to-7.5bb feel too big for the tournament, tune the `raise_3x` multiplier in the `vs_open` table mass (e.g. → `raise_2.5x`/`raise_2.7x` for a ~6.5bb 3-bet), don't touch the engine. No engine defect.

---

## The question
On the `tournaments` branch (worktree `/home/jeffh/projects/my-poker-face-tournaments`),
sims of **all-tiered-bot** tables show a lot of **5×BB preflop raises**. Want to know
where that size comes from and whether it's a bug.

## Tooling bug hit in the previous thread (READ FIRST)
Two things broke the previous session's investigation — avoid them:

1. **Bash `cd` does not persist across calls in this session, and worse, the cwd was
   being *reset* back to `/home/jeffh/projects/my-poker-face-lookup-tables` between
   parallel calls.** Several `cd /home/jeffh/projects/my-poker-face-tournaments && …`
   commands silently ran against the wrong worktree or errored "No such file".
   **Mitigation:** pass absolute paths to every tool; do NOT rely on `cd`. If you must,
   use one compound command per call and never split cwd across parallel calls.
2. **Wrong filenames guessed.** `poker/strategy/preflop_ranges.py` and
   `preflop_charts.json` do **not** exist. The real strategy data lives in
   `poker/strategy/data/preflop_*.json` (see below).

## What's confirmed about the sizing path

Tiered (`sharp`) bots resolve preflop sizes in
`poker/strategy/action_mapper.py` → `resolve_preflop_sizing()` + `_compute_raise_to()`:

- `_compute_raise_to(multiplier, base, min_raise, max_raise)` returns
  `clamp(round(multiplier * base), min_raise, max_raise)` (line ~61). It's a **raise-TO**
  (total), not a raise-by.
- `raise_Xbb`  → base = `big_blind`  → raise-TO = `X × big_blind`.
- `raise_Xx`   → base = `highest_bet` → raise-TO = `X × highest_bet`.
- `min_raise = highest_bet + game_state.min_raise_amount` (clamps the floor).

### Token frequency across the shipped preflop tables (tournaments branch)
Counted from `poker/strategy/data/preflop_*.json`:

| token        | count  | meaning                  |
|--------------|--------|--------------------------|
| `raise_3x`   | 10214  | 3 × highest_bet          |
| `raise_2.2x` | 10152  | 2.2 × highest_bet        |
| `raise_2.5bb`|  3702  | 2.5 × big_blind          |
| `raise_3bb`  |   140  | 3 × big_blind (mostly HU)|
| `raise_4x`   |    38  | 4 × highest_bet (mostly HU)|

There is **no `raise_5bb`/`raise_5x`** in the shipped 6-max tables. So a literal 5×
token is NOT the source.

Data files present:
`preflop_100bb_6max.json`, `..._tight_rfi.json`, `..._wider_rfi.json`,
`preflop_50bb_6max.json`, `preflop_25bb_6max.json`, `preflop_100bb_hu.json`.

## Leading hypotheses for the 5×BB (UNVERIFIED — start here)

1. ~~Limper-adjusted isolation sizing (3bb + 1bb/limper).~~ **WEAKENED — checked.**
   `preflop_isolate.py` (`transform_vs_open_to_isolate`, line 37/50) does NOT add per
   limper; it only shifts `call` mass into the `raise_3x` token. `multiway.py` only scales
   an aggression multiplier (lines 24/33), no explicit BB add. So the 5× is almost
   certainly NOT a per-limper iso add. Note the comment at isolate.py:12 calls `raise_3x`
   a "3-bet to isolate" — so iso spots emit `raise_3x` sized off `highest_bet`.

2. **(NOW LEADING) `raise_3x`/`raise_2.2x` off an inflated `highest_bet` = 3-bets, not opens.**
   In a `vs_raise` node, `highest_bet` is the opener's size. `raise_2.2x` over a 2.5bb
   open ≈ 5.5bb; `raise_3x` over a limp-then-raise spot can land ~5bb. These are correct
   3-bet sizes but a human eyeballing the log may read them as "5×BB opens." Confirm
   whether the 5× events are opens (RFI) or re-raises.

3. **Short-stack / depth tables.** `preflop_25bb_6max.json` + `short_stack.py` /
   `push_fold.py`. At 25bb a min-raise floor or push-fold size could surface as ~5bb.

## Definitive next step the user invited: look at the actual decisions
The user said "you can look for the decisions." Pull captured tiered-bot preflop raises
from the sim DB and bucket the resulting size in BB, joined with context (is it RFI vs a
3bet, # limpers, position, the abstract token chosen). That tells you immediately which
hypothesis is right instead of reasoning from the tables.

- DB locations: Docker `/app/data/poker_games.db`; local `poker_games.db` in the worktree
  root. Tournament sims may write their own sqlite — confirm the path the sim used.
- Useful: `scripts/dbq.py` and `prompt_captures` (call_type / action_taken / phase). Note
  from memory: tiered decisions are snapshot-logged; verify the table actually records the
  abstract token + final chip amount + limper count, or instrument `resolve_preflop_sizing`
  to log them for one sim run.

## Suggested order of attack (revised)
1. **Pull real decisions FIRST** (the user invited this). ~50 tiered preflop raises from
   the sim DB → compute size/BB, split RFI vs facing-a-bet, record the abstract token +
   `highest_bet` at decision time. This settles it in one query.
2. If 5× events are **facing a raise/limp** → it's `raise_3x`/`raise_2.2x` × `highest_bet`,
   i.e. correct 3-bet/iso sizing being misread as "5×BB opens." Likely WAI; consider
   relabeling or capping iso/3bet size if undesired for the tournament feel.
3. If 5× events are genuine **RFI (folded-to) opens** → real bug. Inspect what `highest_bet`
   is at an unopened node (should == big_blind; if it's inflated, that's the defect) and
   whether `min_raise` clamping in `_compute_raise_to` is pushing 3x→5x at some depth.
4. Check the 25bb/50bb depth tables + `short_stack.py`/`push_fold.py` if the 5× clusters at
   shallow stacks.

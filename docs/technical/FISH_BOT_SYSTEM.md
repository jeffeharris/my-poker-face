---
purpose: How the fish (casino-tourist) rule bot decides, the leak catalog, how to configure fish personas, and how to simulate/validate them
type: reference
created: 2026-05-26
last_updated: 2026-05-26
---

# Fish Bot System

"Fish" are the casino-tourist opponents in cash mode: loose-passive recreational
players who are **here to lose chips**. They are deterministic rule bots (no LLM
on the decision path), designed to be *exploitable net losers* whose mistakes a
human (or a grinder AI) can learn to read and punish.

## Design philosophy

Two properties must always hold, and they pull in tension:

1. **Net loser / exploitable.** A fish must bleed chips to a competent opponent.
   The core leak — calling too wide and folding too little when *facing a bet* —
   is what makes them lose, and it is never softened.
2. **Readable / legible.** A fish's aggression must be a *transparent, unbalanced
   tell*: bigger hand → bigger bet, no bluff-balancing, no thin value. The player
   should be able to learn "when this tourist bets, they have it" (or, for a
   spewer, "this one bets too much — don't believe it").

The guiding rule when changing fish behavior: **make their mistakes louder, never
make them play better.** A pure calling station that checks the nuts every street
is *unrealistic* (no real rec player does that) and *hides* information. Honest
value betting + designated leaks broadcast information, which makes fish **more**
exploitable, not less — you just have to act on the read.

> History: the original fish were pure calling stations that raised in exactly one
> spot and never bet for value, so they leaked no information at all. The value
> betting and the three aggression leaks (transparent / spew / sticky) were added
> 2026-05-26 to give them a readable betting tell. See
> [Decision logic](#decision-logic) and [Leak catalog](#leak-catalog).

## Architecture

```
persona (personalities.json)                strategy fn (rule_strategies.py)
  archetype: "fish"          ┌── RuleBotController ──┐   _strategy_fish(context)
  rule_strategy: "fish"  ───►│ (production: psych +  │──►  baseline ladder
  fish_leak: "<leak>"        │  made_tier + leaks)   │     + one leak branch
                             └───────────────────────┘
                             ┌── RuleBasedController ┐
  experiments ARCHETYPES ───►│ (sims/eval: no psych, │──►  same _strategy_fish
  {kind: rule_bot,           │  now made-tier parity)│
   strategy: fish,           └───────────────────────┘
   fish_leak: ...}
```

The decision logic lives once in **`poker/rule_strategies.py::_strategy_fish`** — a
pure `(context: dict) -> {'action', 'raise_to'}` function. Two controllers build
the `context` and call it:

| Controller | File | Used by | Psychology? | Builds full fish context? |
|---|---|---|---|---|
| `RuleBotController` | `poker/rule_bot_controller.py` | **production** (live cash games) | yes | yes |
| `RuleBasedController` | `poker/rule_based_controller.py` | **sims / eval** (`experiments/`) | no | yes (parity added 2026-05-26) |

Both feed `_strategy_fish` the same decision-relevant fields, the key ones being
`made_tier`, `equity`, `has_top_pair_or_better`, draw flags, `cost_to_call`,
`valid_actions`, `pot_total`, `min_raise`/`max_raise`, `street`, and `fish_leak`.

> **Parity caveat (sims only):** `RuleBasedController` does not track per-hand
> starting stacks, so `committed_fraction_of_stack` and `is_losing_at_table`
> default neutral. The two leaks that depend on them — `POT_COMMITTED_EARLY` and
> `SPITE_RAISES_WHEN_LOSING` — therefore do **not** fire in sims. Every other fish
> behavior is faithful. Production (`RuleBotController`) tracks them and fires all
> leaks.

### `made_tier`

Postflop hand strength is classified by
`poker/strategy/hand_classification.py::classify_hand_full`, which returns a
nut-aware `made_tier`:

`nuts` · `strong_made` · `medium_made` · `weak_made` · `air`

`has_top_pair_or_better` is `made_tier in {nuts, strong_made, medium_made}`.
Preflop (no community cards) `made_tier` is `air` and the strategy falls back to
`equity`.

## Decision logic

`_strategy_fish` is evaluated top to bottom. A fish has **at most one** leak, so
only one leak branch can ever fire.

### When checked to (free to act)

Honest **value betting**, size proportional to strength (`_fish_value_fraction`):

| Hand | Bet (fraction of pot) | Constant |
|---|---|---|
| `nuts` or `equity ≥ 0.80` | 0.66 | `FISH_BET_NUTS` |
| `strong_made` or `equity ≥ 0.65` | 0.50 | `FISH_BET_STRONG` |
| `medium_made` (top pair) or `equity ≥ 0.55` | 0.40 | `FISH_BET_MEDIUM` — **only** with the transparent-bettor leak |
| otherwise | — | check |

Baseline fish only value-bet `strong_made`+. The transparent-bettor leak widens
the value range down to top pair. **No fish ever bluffs at baseline when checked
to** (only the spew leak does).

### When facing a bet

Baseline fish **never raise** — they call or fold on a tiered ladder keyed on the
bet size in big blinds:

| Bet size | Call if… | else |
|---|---|---|
| small (`≤ 3 BB`) | always (sees a bet, pays it) | — |
| medium (`≤ 8 BB`) | any pair, or `TOP_35`/suited, or `equity ≥ 0.40` | fold |
| large (`> 8 BB`) | `TOP_20` or `equity ≥ 0.55` | fold |

Facing-bet *aggression* (raising) comes **only** from an aggression leak. Pop
sizing (`_fish_pop_fraction`): `nuts` → pot (`FISH_POP_NUTS` 1.0), `strong_made`
→ 0.80, else → 0.60.

All sizing constants live at the top of `poker/rule_strategies.py`
(`FISH_BET_*`, `FISH_POP_*`, `SPEW_BLUFF_PROBABILITY`, `SPITE_RAISE_PROBABILITY`,
`POT_COMMITTED_THRESHOLD`) so tests can patch them.

## Leak catalog

A `FishLeak` (enum in `rule_strategies.py`) layers **one** identifiable deviation
on the baseline. Set via the persona's `fish_leak` field (its string value).

### Passive leaks — widen how loosely the fish *calls*

| Leak | Trigger | Deviation | The read |
|---|---|---|---|
| `calls_down_top_pair` | large bet + top pair or better | call instead of fold | won't fold a pair → value-bet relentlessly |
| `chases_any_draw` | medium bet + flush draw / OESD | call | pays for every draw → charge them |
| `doesnt_believe_big_bets` | large bet | call with `TOP_45` / any pair / `equity ≥ 0.40` | "can't be bluffed" → only value-bet, never bluff |
| `limps_every_hand` | preflop | never folds or raises preflop — just limps | always sees a flop → isolate wide |
| `pot_committed_early` | once `≥ 30%` of stack in | force-call any remaining bet | can't lay it down once committed |
| `overvalues_face_cards` | medium bet + any J/Q/K/A in hand | call | overvalues paint → bet into their second pair |
| `calls_river_light` | river | call with `TOP_45` / `equity ≥ 0.40` | hero-calls rivers → thin value, never bluff the river |

### Aggression leaks — give the fish a readable betting/raising tell

| Leak | Trigger | Deviation | The read |
|---|---|---|---|
| `spite_raises_when_losing` | losing at table | `8%` chance of a min-raise bluff (`SPITE_RAISE_PROBABILITY`) | random tilt-raises when stuck — noise, mostly ignorable |
| `bets_strong_transparently` | checked to / facing a bet with top pair+ | value-bets down to top pair **and** raises top pair+ facing a bet, size = strength | bet/raise size *is* their hand — fold to big, value-bet thin into small |
| `spews_bluffs` | checked to with air | `40%` chance to bet `0.60` pot (`SPEW_BLUFF_PROBABILITY`); value-bets normally otherwise | bets far too often → don't believe it, call down lighter |
| `sticky_then_pops` | facing a bet with `nuts`/`strong_made` | pure calling station except pops monsters hard | a raise from this rock = the nuts → fold everything marginal |

## Configuring a fish persona

A persona becomes a fish via three fields in `poker/personalities.json`:

```json
"Freddie Fratboy": {
  "archetype": "fish",
  "rule_strategy": "fish",
  "fish_leak": "spews_bluffs"
}
```

Production wiring reads `rule_strategy == "fish"` and the `fish_leak` value, then
constructs `RuleBotController(strategy="fish", fish_leak=...)`. This happens in:

- `flask_app/routes/cash_routes.py` (sit-down / cash table seating)
- `flask_app/handlers/game_handler.py` (live-game refill)

### Current roster (9 fish)

| Persona | Leak |
|---|---|
| Vacation Greg | `calls_down_top_pair` |
| Bachelorette Brenda | `chases_any_draw` |
| Cruise Carl | `limps_every_hand` |
| Birthday Bobby | `pot_committed_early` |
| After Hours Trent | `spite_raises_when_losing` |
| Lucky Mona | `overvalues_face_cards` |
| Slots Linda | `sticky_then_pops` |
| Golf Trip Brad | `bets_strong_transparently` |
| Freddie Fratboy | `spews_bluffs` |

Fish bankrolls/seating are managed as permanent pool-funded personas — see
`docs/plans/CASH_MODE_FISH_AS_PERSONAS.md`.

## Simulation & validation

### Decision-distribution profile (fast, behavioral)

`scripts/fish_aggression_profile.py` (gitignored diagnostic) runs the real
`_strategy_fish` over tens of thousands of strength-correlated spots and reports
the action distribution per leak — confirms aggression rises monotonically with
strength (the tell) and that the calling-station core (high call% / low fold%
facing bets) stays intact.

```bash
docker compose exec backend python -m scripts.fish_aggression_profile
```

### SNG win-rate (slow, economic)

Fish are registered as archetypes in `experiments/simulate_bb100.py::ARCHETYPES`:
`Fish` (baseline), `Fish-Transparent`, `Fish-Spew`, `Fish-Sticky`. Run the
Winner-Take-All single-table SNG field eval (`experiments/sng_runner.py`,
ported from the `lookup-tables` harness):

```bash
docker compose exec backend python -m experiments.sng_runner \
  --mode field \
  --field Baseline,CaseBot,Fish-Transparent,Fish-Spew \
  --sngs 80 --start-bb 100 --seed 42
```

> **Validate deep, not short.** Use `--start-bb 100`. At short stacks
> (`--start-bb 25`) the game is a push/fold turbo where the calling-station leak
> never expresses postflop and aggression earns fold equity — fish stop looking
> like losers (a regime artifact, consistent with prior short-stack findings).
> Deep 6-max SNGs are minutes each → this is a leave-it-running eval, not an
> inner-loop check.

### Results (2026-05-26)

- **Behavioral:** baseline checked-to bet rate ~18% (was ~0); aggression by
  strength monotonic (e.g. transparent: top-pair+ 100%, weaker 0%); facing-bet
  fold% ~44–47% across all variants (calling core intact).
- **Economic (100bb, n=80, 4-handed, null 25%):** CaseBot 46.2% ✅, Baseline
  22.5%, Fish-Transparent 17.5%, Fish-Spew 13.8% ❌ — fish are clear net losers,
  and `spews_bluffs` is the *worst* (bluffing into competent players bleeds
  faster). The aggression did not make fish harder to beat.

## Extending: adding a new leak

1. Add a value to `FishLeak` in `poker/rule_strategies.py` (group it passive vs
   aggression).
2. Add its branch to `_strategy_fish` — fire only when the trigger holds, else
   fall through to baseline. Reuse `_fish_bet` / `_fish_value_fraction` /
   `_fish_pop_fraction` for sizing so the tell stays consistent.
3. Pin trigger + non-trigger behavior (and sizing, if novel) in
   `tests/test_fish_leaks.py`. Use `context['_rng']` to make probabilistic leaks
   deterministic.
4. Assign it to a persona in `personalities.json`, and optionally add a
   `Fish-<Name>` entry to `ARCHETYPES` to make it measurable in the SNG eval.
5. If the leak needs a new context field, add it to **both**
   `RuleBotController._build_rule_context` and
   `RuleBasedController._build_context` (keep them in parity).

## Key files

| File | Purpose |
|---|---|
| `poker/rule_strategies.py` | `_strategy_fish`, `FishLeak`, sizing constants, `_fish_*` helpers, `RuleConfig.fish_leak` |
| `poker/rule_bot_controller.py` | production controller (psychology + `made_tier` context) |
| `poker/rule_based_controller.py` | sim/eval controller (made-tier parity) |
| `poker/strategy/hand_classification.py` | `classify_hand_full` → `made_tier` |
| `poker/personalities.json` | fish personas (`archetype`/`rule_strategy`/`fish_leak`) |
| `flask_app/routes/cash_routes.py`, `flask_app/handlers/game_handler.py` | production wiring |
| `experiments/simulate_bb100.py` | `ARCHETYPES` (incl. `Fish*`), `make_controller` |
| `experiments/sng_runner.py` | WTA SNG field / champion-challenger eval |
| `scripts/fish_aggression_profile.py` | decision-distribution diagnostic |
| `tests/test_fish_leaks.py` | per-leak behavior + sizing tests |

## Related docs

- `docs/plans/CASH_MODE_FISH_AS_PERSONAS.md` — fish as permanent pool-funded personas
- `docs/plans/CASH_MODE_EPHEMERAL_TOURISTS.md` — original designated-leak spec
- `docs/technical/CASH_MODE_ECONOMY.md` — where fish sit in the chip economy
- `docs/plans/EVAL_HARNESS_PLAN.md` / `docs/plans/SNG_RUNNER_HARDENING.md` — the SNG eval design the fish archetypes plug into (these docs currently live on the `lookup-tables` branch, alongside the runner that was ported here)

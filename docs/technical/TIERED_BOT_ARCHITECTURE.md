---
purpose: Architecture design for the 3-layer tiered bot (solver baselines + personality distortion + LLM expression)
type: architecture
created: 2026-02-16
last_updated: 2026-05-12T15:00:00
---

# Tiered Bot Architecture

## Motivation

LLMs are great at being characters but terrible at being strategists. The current system (bounded options + LLM selection) was a good first step — the rule engine constrains the decision space and the LLM picks from safe options. But the LLM still has the final say, and experiments show it can't play strategically over multiple hands. It can't exploit, adapt, or think in ranges.

The new approach separates concerns completely:

| Layer | Responsibility | Technology |
|-------|---------------|------------|
| **Strategic Core** | What to do | Solver-derived baselines + heuristics |
| **Personality Modifier** | How to deviate | Logit-space distortion of base frequencies |
| **Expression Layer** | What to say | LLM (chat, reactions, table talk) |

The LLM never touches the decision. It only narrates.

### What This Is (and Isn't)

This is **not** a multiplayer CFR solver. True multiway NLHE solving is a multi-year research problem we don't need to solve.

What we're actually building:
- **Solver-derived heads-up baselines** (from PioSOLVER)
- **Multiway tightening heuristics** (deterministic frequency adjustments)
- **Deterministic personality distortion** (logit-space math on base frequencies)

That is a solid core. It produces strategically sound opponents with distinct, exploitable personalities — which is the goal.

### Definition of Done

Build a three-layer AI poker opponent system that reliably produces strategic, human-readable play with distinct, exploitable personalities.

Specifically:

**The Strategic Core (Layer 1)** produces principled base action frequencies for all decision points using curated preflop charts and pre-solved postflop tables. These baselines must be consistent, reproducible, and aligned with known solver behavior.

**The Personality Modifier (Layer 2)** applies bounded, logit-space distortions to those baselines that:
- Preserve solver support (no new unsupported actions)
- Respect per-archetype deviation budgets (KL + per-action caps)
- Produce statistically distinct archetypes (e.g., VPIP, PFR, 3-bet) in validation

**The Expression Layer (Layer 3)** adds narrative output after decisions are made, enriching player experience without affecting action selection.

**Success means that in numeric bot-vs-bot validation:**
- Archetype stat profiles separate cleanly and directionally (higher aggression → higher PFR; higher looseness → higher VPIP)
- No archetype exhibits logically impossible stats (e.g., PFR > VPIP)
- Deviations from baseline cost EV within predefined guardrails (e.g., ≤ -20 bb/100)

**And in system maturity:**
- All edge cases (missing strategy keys, legal action masking, clamped distributions) behave deterministically and safely
- Turn/river heuristics are grounded in concrete frequency templates, not ad-hoc prose

This defines what done looks like: a robust, architecturally sound poker AI engine that's implementable, testable, and produces both strategic play and expressive character behavior.

---

## Layer 1: Strategic Core

### Goal

Provide solver-quality baseline frequencies for all decision points. The core answers: "What would a balanced player do here?" Layer 2 then distorts that answer based on personality.

### Data Sources

**Preflop: Curated 6-max charts** (100bb, standard rake)

Preflop 6-max at 100bb is essentially solved public knowledge. We encode established charts directly rather than running a solver. Sources: training sites, published GTO ranges, community-vetted charts.

Charts needed:
- **Open (RFI)** by position: UTG, HJ, CO, BTN, SB
- **3-bet** vs each opener position (e.g., CO 3-bet vs UTG open, BTN 3-bet vs CO open)
- **4-bet** frequencies by position matchup
- **BB defend** vs each opener position (call and raise frequencies)
- **SB defend** vs each opener position

Each chart specifies per-hand frequencies (pure or mixed) for all 169 canonical hands. See Preflop Storage Rule below.

**Postflop: PioSOLVER** (v1: HU SRP flops only)

Representative flop textures solved via PioSOLVER UPI scripting and exported.
- Strategy by hand class + board texture + facing action
- Turn/river use heuristics with discipline rules (not solved)

#### Pio Export Pipeline (Postflop Only)

```
1. Define flop solve configurations (preflop ranges by position, bet tree, stack depth)
     ↓
2. Batch solve representative flop textures via UPI scripting
     ↓
3. Export combo-level frequencies for IP and OOP
     ↓
4. Map combos to hand-class buckets (aggregate frequencies)
     ↓
5. Store as per-texture JSON strategy tables in repo
```

### Preflop Storage Rule (Rank-Class Exact, Not Suit-Exact)

**Preflop stored as 169 canonical hands. No further bucketing.**

- Store frequencies for all 169 canonical hands (13 pairs + 78 suited + 78 offsuit)
- Personality distortion operates per canonical hand
- Mixed frequencies are preserved exactly
- Suit-exact combos (`AhKs` vs `AcKd`) are not distinguished preflop — they play identically without board context

Why:
- 169 hands is trivial to store and process
- Mixing matters — KQs opening 85% vs 100% is a meaningful strategic distinction
- Avoids distortion artifacts from further aggregation into hand-class buckets
- Gives precise control over 3-bet, open, and defend frequencies
- Suit-exact matters only postflop (board interaction) — Pio postflop exports remain combo-exact

### Preflop Strategy Tables

Preflop is fully specified by position-based frequency charts at 100bb depth.

**Positions (6-max):**
- UTG (Under the Gun)
- HJ (Hijack)
- CO (Cutoff)
- BTN (Button)
- SB (Small Blind)
- BB (Big Blind)

**Decision nodes by scenario:**

| Scenario | Available Actions |
|----------|------------------|
| Unopened pot (RFI) | fold, raise_2.5bb, raise_3bb |
| Facing open (call/3bet/fold) | fold, call, raise_3x, raise_4x |
| Facing 3-bet | fold, call, raise_2.2x (4-bet) |
| Facing 4-bet | fold, call, jam |
| BB vs open (defend) | fold, call, raise_3x |
| SB vs open | fold, call, raise_3x |

Each node stores frequencies per canonical hand (e.g., AA, AKs, AKo, T9s — all 169 hands).

**Preflop scenarios NOT modeled in v1:**
- Open limping (no limp strategy — all unopened pots are raise-or-fold)
- Facing a limp (treat as unopened for RFI purposes)
- Squeeze (3-bet after open + cold call — uses standard vs-open 3-bet table)
- 5-bet+ beyond jam (facing 4-bet resolves to fold/call/jam only)

All non-covered preflop scenarios fall back to the conservative default policy. This is intentional — not a gap.

### Postflop Strategy (v1: HU SRP Flops Only)

Postflop state is defined by structured dimensions — **not** by equity as a primary key.

**Postflop state dimensions:**

| Dimension | Values | Purpose |
|-----------|--------|---------|
| **Street** | flop, turn, river | Which betting round |
| **Position** | IP, OOP | Relative to aggressor |
| **Pot type** | SRP, 3BP | Single raised pot vs 3-bet pot |
| **Board texture** | exactly 6 classes (see below) | Surface character of the board |
| **Hand class** | Made-hand tier + draw type | What we're holding |
| **Facing action** | unopened, facing_bet, facing_raise | What we need to respond to |
| **SPR bucket** | high (>6), medium (2-6), low (<2) | Stack-to-pot ratio |

**Board texture buckets** (flop) — exactly 6 for v1. Add more only if needed.

| Texture | Example | Character |
|---------|---------|-----------|
| Dry high | K♠ 7♦ 2♣ | Few draws, top pair dominant, includes A-high dry |
| Dry low/static | 8♠ 3♦ 3♣ | Paired or low rainbow — trips matter on pairs, few draws, static boards |
| Monotone | K♠ 7♠ 2♠ | Flush possible, otherwise static |
| Two-tone broadway | K♠ Q♥ J♠ | Flush draws + straight draws + big pairs |
| Two-tone connected | 8♠ 7♥ 5♠ | Flush draws + straight draws, medium/low |
| Wet rainbow | 9♠ 8♥ 7♦ | Straights and draws everywhere, no flush |

**Postflop hand classification (two-axis):**

A single-axis classification (nuts / strong / medium / weak / draw / air) is too lossy. Hands like "top pair with a flush draw" and "top pair no draw" play very differently.

Split into two axes:

**Axis 1: Made Tier**

| Tier | Examples |
|------|---------|
| Nuts / near-nuts | Sets, straights, flushes, full houses |
| Strong made | Overpair, top pair top kicker, two pair |
| Medium made | Top pair weak kicker, second pair |
| Weak made | Third pair, bottom pair |
| Air | No pair, no made hand |

**Axis 2: Draw Modifier**

| Modifier | Examples |
|----------|---------|
| No draw | No flush or straight draw |
| Strong draw | Flush draw, OESD, combo draw |
| Weak draw | Gutshot, backdoor flush draw |
| Backdoor only | Backdoor flush + backdoor straight |

**Final bucket key: `(made_tier, draw_modifier)`**

Examples:
- `(strong_made, strong_draw)` = overpair + flush draw → aggressive
- `(air, strong_draw)` = no pair + flush draw → semi-bluff candidate
- `(medium_made, no_draw)` = second pair, no draw → check-call
- `(nuts, no_draw)` = set on dry board → value bet / trap

This preserves solver nuance without exploding state space (5 × 4 = 20 buckets).

**Made tier classification rules** (deterministic, first matching rule wins):

| Rule | Made Tier | Examples |
|------|-----------|---------|
| Quads, full house, flush, straight | `nuts` | Any of these holdings |
| Set (pocket pair + board match) | `nuts` | 77 on 7♠ 4♦ 2♣ |
| Two pair (both hole cards paired with board) | `strong_made` | KJ on K♠ J♦ 5♣ |
| Overpair | `strong_made` | QQ on J♠ 8♦ 3♣ |
| Top pair + top kicker (TPTK) | `strong_made` | AK on K♠ 7♦ 2♣ |
| Top pair + weak kicker | `medium_made` | K9 on K♠ 7♦ 2♣ |
| Second pair | `medium_made` on dry boards, `weak_made` on wet boards |
| Third pair or bottom pair | `weak_made` | 55 on K♠ 8♦ 5♣ |
| Ace-high (no pair) | `air` | AQ on K♠ 8♦ 3♣ |
| No pair, no ace | `air` | T9 on K♠ 5♦ 2♣ |

**Draw modifier classification rules:**

| Rule | Draw Modifier |
|------|--------------|
| Flush draw (4 to a flush) or OESD (8 outs to straight) | `strong_draw` |
| Flush draw + straight draw (combo draw) | `strong_draw` |
| Gutshot (4 outs to straight) | `weak_draw` |
| Backdoor flush draw (3 to a flush) only | `backdoor` |
| Backdoor straight (3 connected) only | `backdoor` |
| None of the above | `no_draw` |

When both axes apply, combine them: e.g., top pair weak kicker + flush draw = `(medium_made, strong_draw)`.

**Note on invalid combinations**: Some bucket combinations rarely or never co-exist (e.g., `nuts + strong_draw` is uncommon — if you have a flush, there's no flush draw). All 20 buckets are structurally available but not all will be populated for every texture. Do not attempt to prune or optimize the bucket count in v1 — leave the full grid and let empty cells be empty.

**Equity is used only for:**
- Bluff eligibility (is this hand suitable as a bluff?)
- Thin value threshold (is this hand strong enough to bet for value?)
- Call vs fold threshold (are we getting the right price?)

Equity is **not** a primary key for strategy lookup.

### Action Sets by Node Type

Three separate action sets depending on what we're facing. This is critical — "raise_small" means completely different things in different contexts.

**Unopened node** (checking or betting):

| Action | Sizing |
|--------|--------|
| check | — |
| bet_33 | 33% pot |
| bet_67 | 67% pot |
| bet_100 | 100% pot |

**Facing bet** (responding to a bet):

| Action | Sizing |
|--------|--------|
| fold | — |
| call | — |
| raise_67 | 67% of (pot + bet) |
| raise_150 | 150% of (pot + bet) |
| jam | All-in (if SPR allows) |

**Facing raise** (responding to a raise/re-raise):

| Action | Sizing |
|--------|--------|
| fold | — |
| call | — |
| jam | All-in (or re-raise bucket) |

### Multiway Adjustments

When more than 2 players see a flop, apply deterministic heuristic adjustments to the HU baseline frequencies. **Adjustments are position-sensitive** — OOP multiway is tighter than IP multiway.

| Adjustment | IP Multiplier | OOP Multiplier | Rationale |
|------------|--------------|----------------|-----------|
| Bluff frequency | 0.5× | 0.3× | More opponents = more likely someone calls; worse OOP |
| Raise frequency | 0.6× | 0.4× | Aggression less profitable multiway, especially OOP |
| Check frequency | 1.3× | 1.5× | Pot control more important, critical OOP |
| Call thresholds | +5% equity | +8% equity | Need stronger hands to continue; OOP needs more |
| Value bet threshold | +5% equity | +8% equity | Need better hand to bet for value |

Multipliers are defined at 3-way and decrease linearly per additional opponent, clamped to `[0.2, 1.0]`:

```
adjustment = clamp(base_multiplier_3way + (num_opponents - 3) * per_opponent_delta, 0.2, 1.0)
```

The table above specifies 3-way values. Example for IP bluff frequency: `clamp(0.5 + (n-3) * -0.1, 0.2, 1.0)` → 3-way=0.5×, 4-way=0.4×, 5-way=0.3×, 6-way=0.2× (floor).

These are applied to the base frequencies before personality distortion. Not solved — just sound heuristics.

The IP/OOP split prevents accidental over-bluffing multiway OOP, which is one of the most common bot failure modes.

### Turn and River (v1: Heuristics)

v1 does not include solved turn/river strategies. Instead, use heuristic frequency tables keyed by hand class and node type.

Turn and river use the same postflop action sets defined in "Action Sets by Node Type" above.

**Turn heuristic frequencies (unopened, IP):**

| Hand Class | check | bet_33 | bet_67 | bet_100 |
|------------|-------|--------|--------|---------|
| nuts / near-nuts | 0.20 | 0.10 | 0.50 | 0.20 |
| strong_made | 0.30 | 0.30 | 0.30 | 0.10 |
| medium_made | 0.60 | 0.25 | 0.15 | 0.00 |
| weak_made | 0.80 | 0.15 | 0.05 | 0.00 |
| air + strong_draw | 0.50 | 0.10 | 0.30 | 0.10 |
| air + weak/no_draw | 0.85 | 0.10 | 0.05 | 0.00 |

**Turn heuristic frequencies (unopened, OOP):**

| Hand Class | check | bet_33 | bet_67 | bet_100 |
|------------|-------|--------|--------|---------|
| nuts / near-nuts | 0.30 | 0.10 | 0.40 | 0.20 |
| strong_made | 0.45 | 0.25 | 0.25 | 0.05 |
| medium_made | 0.80 | 0.15 | 0.05 | 0.00 |
| weak_made | 0.90 | 0.10 | 0.00 | 0.00 |
| air + strong_draw | 0.60 | 0.10 | 0.25 | 0.05 |
| air + weak/no_draw | 0.95 | 0.05 | 0.00 | 0.00 |

**Turn heuristic frequencies (facing bet):**

| Hand Class | fold | call | raise_67 | raise_150 | jam |
|------------|------|------|----------|-----------|-----|
| nuts / near-nuts | 0.00 | 0.40 | 0.30 | 0.20 | 0.10 |
| strong_made | 0.00 | 0.70 | 0.20 | 0.10 | 0.00 |
| medium_made | 0.20 | 0.70 | 0.10 | 0.00 | 0.00 |
| weak_made | 0.60 | 0.35 | 0.05 | 0.00 | 0.00 |
| air + strong_draw | 0.30 | 0.40 | 0.20 | 0.10 | 0.00 |
| air + weak/no_draw | 0.80 | 0.15 | 0.05 | 0.00 | 0.00 |

**River heuristic frequencies (unopened, IP):**

| Hand Class | check | bet_33 | bet_67 | bet_100 |
|------------|-------|--------|--------|---------|
| nuts / near-nuts | 0.10 | 0.05 | 0.40 | 0.45 |
| strong_made | 0.25 | 0.35 | 0.30 | 0.10 |
| medium_made | 0.70 | 0.20 | 0.10 | 0.00 |
| weak_made | 0.90 | 0.10 | 0.00 | 0.00 |
| air (missed draw) | 0.75 | 0.05 | 0.15 | 0.05 |
| air (no draw) | 0.95 | 0.05 | 0.00 | 0.00 |

**River heuristic frequencies (facing bet):**

| Hand Class | fold | call | raise_67 | raise_150 | jam |
|------------|------|------|----------|-----------|-----|
| nuts / near-nuts | 0.00 | 0.30 | 0.20 | 0.20 | 0.30 |
| strong_made | 0.00 | 0.80 | 0.15 | 0.05 | 0.00 |
| medium_made | 0.30 | 0.65 | 0.05 | 0.00 | 0.00 |
| weak_made | 0.70 | 0.25 | 0.05 | 0.00 | 0.00 |
| air (missed draw) | 0.80 | 0.05 | 0.10 | 0.05 | 0.00 |
| air (no draw) | 0.90 | 0.05 | 0.05 | 0.00 | 0.00 |

These are starting-point heuristics, not solver output. Personality distortion (Layer 2) applies on top. River bluff guardrail applies after distortion.

**Design notes:**
- OOP river tables follow the same structure but with higher check/fold frequencies (apply the same IP/OOP tightening pattern as the turn tables)
- "Facing raise" on both streets uses the simplified action set: fold/call/jam
- Draw modifier matters on the turn (draws still live) but collapses on the river (draws either hit or missed)

**River Bluff Guardrail (hard invariant):**

At each river decision node, after personality distortion:

```
bluff_frequency ≤ value_frequency × MAX_BLUFF_RATIO
```

| Archetype | MAX_BLUFF_RATIO |
|-----------|----------------|
| Competitive (TAG, Rock, Nit) | 0.8 |
| Balanced (default) | 1.0 |
| Extreme (LAG, Maniac) | 1.2 |

This prevents personality distortion from breaking endgame balance. Without this rule, extreme archetypes will collapse EV hard by over-bluffing the river. This guardrail is applied **after** logit-space distortion and clamping — it's a final safety check.

**Enforcement procedure** (when violation occurs):

1. Identify bluff-class actions (betting/raising with `air` or `weak_draw` hand class)
2. Scale all bluff-class action probabilities proportionally downward until `bluff_freq ≤ value_freq × MAX_BLUFF_RATIO`
3. Redistribute removed probability mass to check/fold (proportional to their current weights)
4. Do **not** modify value frequencies
5. Renormalize

This ensures personality distortion remains directional (aggressive archetypes still bluff more) but bounded (they can't bluff more than the ratio allows).

**Guardrail activation logging**: During validation, log per archetype:
- Guardrail activation rate (what % of river decisions trigger it)
- Check frequency delta after guardrail fires (how much check inflates)

If guardrail fires constantly (>50% of river decisions), distortion scales are too large upstream — fix the DeviationProfile, not the guardrail. If check frequency explodes after redistribution in extreme archetypes, same root cause.

### Data Structures

```python
@dataclass(frozen=True)
class StrategyProfile:
    """Action probability distribution for one decision point."""
    action_probabilities: Dict[str, float]
    # e.g. {'fold': 0.3, 'call': 0.5, 'raise_67': 0.15, 'jam': 0.05}

@dataclass(frozen=True)
class PreflopNode:
    """Preflop decision point — 169 canonical hands, not suit-exact."""
    hand: str             # 'AKs', 'AKo', 'AA', 'T9s', etc. (canonical hand)
    position: str         # 'UTG', 'HJ', 'CO', 'BTN', 'SB', 'BB'
    scenario: str         # 'rfi', 'vs_open', 'vs_3bet', 'vs_4bet', 'defend'
    opener_position: str  # Position of original raiser (if applicable)

    @property
    def key(self) -> str:
        """Compact string key for storage and lookup."""
        return f"{self.scenario}|{self.position}|{self.opener_position}|{self.hand}"

@dataclass(frozen=True)
class PostflopNode:
    """Postflop decision point — two-axis hand classification.

    Data model is v2-ready. v1 only populates (street='flop', pot_type='SRP').
    Turn/river and 3BP keys exist but have no table entries in v1.
    """
    street: str           # 'flop', 'turn', 'river'  (v1: flop only)
    position: str         # 'IP', 'OOP'
    pot_type: str         # 'SRP', '3BP'  (v1: SRP only)
    board_texture: str    # 'dry_high', 'monotone', 'wet_rainbow', etc.
    made_tier: str        # 'nuts', 'strong_made', 'medium_made', 'weak_made', 'air'
    draw_modifier: str    # 'no_draw', 'strong_draw', 'weak_draw', 'backdoor'
    facing_action: str    # 'unopened', 'facing_bet', 'facing_raise'
    spr_bucket: str       # 'high', 'medium', 'low'

    @property
    def key(self) -> str:
        """Compact string key for storage and lookup."""
        return f"{self.street}|{self.position}|{self.pot_type}|{self.board_texture}|{self.made_tier}|{self.draw_modifier}|{self.facing_action}|{self.spr_bucket}"

@dataclass
class StrategyTable:
    """Complete strategy lookup for the tiered bot.

    Internally keyed by compact strings (node.key) for efficient
    storage and lookup. Dataclasses used for readability at call sites.
    """
    preflop: Dict[str, StrategyProfile]   # keyed by PreflopNode.key
    postflop: Dict[str, StrategyProfile]  # keyed by PostflopNode.key

    def lookup(self, node) -> StrategyProfile:
        """Look up base strategy for a decision point."""
        if isinstance(node, PreflopNode):
            return self.preflop[node.key]
        return self.postflop[node.key]
```

### File Structure

```
poker/
  strategy/
    __init__.py
    strategy_table.py       # StrategyTable, lookup logic, node mapping
    hand_classification.py  # Postflop: map hand + board → (made_tier, draw_modifier)
    board_texture.py        # Classify board into texture buckets
    multiway.py             # Multiway frequency adjustments
    heuristics.py           # Turn/river heuristic rules

    data/
      preflop_100bb_6max.json   # Curated preflop charts (from established sources)
      flop_srp_hu/              # Pio-solved per-texture flop strategies
        dry_rainbow.json
        connected_wet.json
        ...

    pio/
      export_flops.py       # UPI script: batch solve + export flop textures
      combo_to_class.py     # Map Pio combo output → hand class buckets
```

### Safety & Fallbacks

#### Missing Strategy Keys

If `StrategyTable.lookup()` finds no exact key match, use a deterministic fallback ladder:

1. **Exact key** — preferred
2. **Nearest-neighbor on board texture** — deterministic, one-directional adjacency map. Each texture has exactly one fallback neighbor:

   | Missing Texture | Falls Back To | Rationale |
   |----------------|---------------|-----------|
   | `dry_high` | `dry_low_static` | Both static, top-pair dominant |
   | `dry_low_static` | `dry_high` | Both static |
   | `monotone` | `two_tone_connected` | Both flush-relevant |
   | `two_tone_broadway` | `wet_rainbow` | Both draw-heavy, high-card boards |
   | `two_tone_connected` | `wet_rainbow` | Both highly connected |
   | `wet_rainbow` | `two_tone_connected` | Both highly connected |
3. **Conservative default policy** — passive, avoid spewing:
   - Unopened: `{'check': 1.0}` (never bet without strategy data)
   - Facing bet: `{'fold': 0.7, 'call': 0.3}` (lean toward folding)
   - Facing raise: `{'fold': 0.8, 'call': 0.2}` (strongly lean toward folding)
   - Preflop missing key: fold unless check is legal (BB option) — never invent a raise

All fallbacks are logged in debug mode.

#### Legal Action Masking

Before distortion, compute legal actions from game state and filter the base distribution:

1. Get legal actions from `game_state.current_player_options`
2. Remove illegal actions from base distribution (set to 0)
3. Renormalize remaining actions
4. Distort only over legal, supported actions

This prevents distortion from boosting `jam` when it's not legal, or `check` when facing a bet.

#### Degenerate Distribution Handling

If after masking all probability mass is zero (shouldn't happen, but guard):

Safe fallback priority order:
1. `check` if legal (postflop only — check is rarely legal preflop)
2. `call` if legal and facing a bet (don't fold when calling is the only non-fold option)
3. `fold` if legal
4. Smallest legal raise if forced (e.g., BB must act)

#### River Guardrail: value_freq = 0

If no value hands exist at this river node (all hands in our bucket are air/weak), enforce `bet_freq = 0`. Move all bet/raise probability mass to check (if unopened) or fold/call (if facing bet). Do not invent bluffs where the solver had no value support.

#### Board Texture Classification Rules

Classification is deterministic. Priority order (first matching rule wins):

1. **Monotone**: 3 cards of same suit
2. **Paired**: Board contains a pair (any rank)
   - → `dry_low_static` (always — paired boards are static)
3. **Two-tone**: 2 cards of same suit
   - If highest card ≥ J and 2+ broadways → `two_tone_broadway`
   - Else → `two_tone_connected`
4. **Rainbow** (all different suits):
   - Compute connectedness score: count cards within 3 ranks of each other
   - If connectedness ≥ 2 → `wet_rainbow`
   - If highest card ≥ T → `dry_high`
   - Else → `dry_low_static`

Borderline boards are expected to map imperfectly. Classification is stable and deterministic — same board always maps to same bucket.

### Reproducibility

Validation tournaments must be fully reproducible. Log all inputs:

| Parameter | What to Log |
|-----------|------------|
| RNG seed | Master seed for all sampling and shuffling |
| Strategy table version | Hash of strategy JSON files (detect table changes) |
| DeviationProfile per player | Full profile values used |
| Personality anchors per player | All anchor values from personalities.json |
| Matchup config | Player names, archetypes, starting stacks, blind structure |

All RNG (action sampling, shuffling) uses a seeded `random.Random` instance passed into the controller. Same seed + same strategy tables + same matchup → identical results.

Strategy tables must be versioned (git hash or content hash). Once you start tuning, unversioned tables make tournament comparisons meaningless.

### Debug Mode Logging

For each decision, log the full distortion pipeline:

| Step | What's Logged |
|------|--------------|
| 1. Base probabilities | Raw solver/chart frequencies for this node |
| 2. Multiway-adjusted probabilities | After multiway multipliers (if applicable) |
| 3. Personality logit offsets | Per-action offsets from trait computation |
| 4. Emotional logit offsets | Per-action offsets from emotional state |
| 5. Pre-clamp probabilities | After softmax, before clamping |
| 6. Final probabilities | After KL/per-action clamping |
| 7. Sampled action | The action actually taken |
| 8. River bluff check | Whether bluff guardrail fired (if river) |

This is critical for tuning and validation. Without it, distortion tuning becomes guesswork. Enable via a `debug_logging` flag on the controller — off by default in production, always on during validation tournaments.

### Integration with Existing Code

| Existing Component | How It's Used |
|-------------------|---------------|
| `hand_tiers.py` | Foundation for preflop hand classification |
| `equity_calculator.py` | Bluff eligibility, thin value, call thresholds |
| `hand_evaluator.py` | Board texture analysis, made hand classification |
| `hand_ranges.py` | Opponent range initialization, stat tracking |

---

## Layer 2: Personality Modifier

### Goal

Take the solver-derived baseline strategy and apply systematic, characteristic deviations that create exploitable but believable opponents. Each archetype plays differently in a way the human can learn to read and exploit.

### Approach: Masked Logit-Space Distortion

**Do not modify raw probabilities directly.** That causes instability and collapse toward extremes.

Instead, use masked logit-space distortion with softmax normalization. Zero-support actions are preserved — personality never invents actions the solver didn't include.

```python
import numpy as np

def modify_strategy(
    base: StrategyProfile,
    legal_actions: Set[str],
    personality: PersonalityAnchors,  # From personalities.json anchors — see docs/technical/PSYCHOLOGY_OVERVIEW.md
    emotional_state: EmotionalShift,  # From player_psychology.py — see docs/technical/PSYCHOLOGY_OVERVIEW.md
    deviation_profile: DeviationProfile,
) -> StrategyProfile:
    """
    Apply personality distortion in logit space with masking.

    Key invariants:
    - Actions with base probability 0 stay at 0 (solver support preserved)
    - Illegal actions are masked out before distortion
    - DeviationProfile controls per-archetype clamp thresholds
    """
    actions = list(base.action_probabilities.keys())
    base_probs = np.array([base.action_probabilities[a] for a in actions])

    # Step 1: Mask illegal actions and zero-support actions
    support_mask = np.array([
        (base_probs[i] > 0) and (actions[i] in legal_actions)
        for i in range(len(actions))
    ])

    if support_mask.sum() == 0:
        # Degenerate: no legal supported actions → safe fallback
        return _safe_fallback(legal_actions)

    # Step 2: Extract supported subset, renormalize
    supported_probs = base_probs[support_mask]
    supported_probs = supported_probs / supported_probs.sum()
    supported_actions = [a for a, m in zip(actions, support_mask) if m]

    # Step 3: Convert to logits (safe — all probs > 0 in support set)
    base_logits = np.log(supported_probs)
    # NOTE: All subsequent KL computation operates ONLY over this support set.
    # Zero-mass actions are excluded. Including zeros in KL produces undefined terms.

    # Step 4: Compute trait offsets (scaled by DeviationProfile)
    offsets = compute_trait_offsets(
        supported_actions, personality, emotional_state, deviation_profile
    )

    # Step 5: Apply offsets in logit space → softmax
    new_logits = base_logits + offsets
    shifted = np.exp(new_logits - new_logits.max())  # numerical stability
    new_probs = shifted / shifted.sum()

    # Step 6: Clamp (see Clamping Guarantees below)
    new_probs = clamp_divergence(
        supported_probs, new_probs, base_logits, offsets, deviation_profile
    )

    # Step 7: Reconstruct full distribution (zeros preserved)
    result = {}
    j = 0
    for i, action in enumerate(actions):
        if support_mask[i]:
            result[action] = float(new_probs[j])
            j += 1
        else:
            result[action] = 0.0

    return StrategyProfile(action_probabilities=result)
```

### Trait Offset Computation

Each personality anchor produces signed offsets on specific action types:

```python
# Action categories for offset application.
# Categories are matched by PREFIX, not exact string, to handle all raise variants
# across preflop (raise_2.5bb, raise_3x, raise_4x, raise_2.2x) and
# postflop (raise_67, raise_150) action labels.

def categorize_action(action: str) -> str:
    """Categorize any action label into aggressive/passive/fold."""
    if action in {'fold'}:
        return 'fold'
    if action in {'check', 'call'}:
        return 'passive'
    if action == 'jam' or action.startswith(('bet_', 'raise_')):
        return 'aggressive'
    return 'passive'  # unknown defaults to passive (safe)

def compute_trait_offsets(
    actions: List[str],
    personality: PersonalityAnchors,
    emotional_state: EmotionalShift,
    profile: DeviationProfile,         # Per-archetype scales
) -> np.ndarray:
    offsets = np.zeros(len(actions))

    for i, action in enumerate(actions):
        cat = categorize_action(action)

        # Aggression: boost aggressive actions, penalize passive
        if cat == 'aggressive':
            offsets[i] += (personality.baseline_aggression - 0.5) * profile.aggression_scale
        elif cat == 'passive':
            offsets[i] -= (personality.baseline_aggression - 0.5) * profile.aggression_scale

        # Looseness: penalize fold, boost call/raise
        if cat == 'fold':
            offsets[i] -= (personality.baseline_looseness - 0.5) * profile.looseness_scale
        else:
            offsets[i] += (personality.baseline_looseness - 0.5) * profile.looseness_scale * 0.5

        # Risk identity: boost extreme actions (jam, fold), penalize middle
        if action == 'jam':
            offsets[i] += (personality.risk_identity - 0.5) * profile.risk_scale
        elif cat == 'passive':
            offsets[i] -= (personality.risk_identity - 0.5) * profile.risk_scale * 0.3

        # Ego: penalize folding (sunk cost resistance, per-archetype scale)
        if cat == 'fold':
            offsets[i] -= personality.ego * profile.ego_fold_penalty

    # Emotional modifiers (gated by poise)
    emotional_impact = emotional_state.intensity * (1.0 - personality.poise)
    emotional_offsets = compute_emotional_offsets(actions, emotional_state)
    offsets += emotional_offsets * emotional_impact

    return offsets
```

### DeviationProfile (Per Archetype)

Clamp thresholds are **not** global constants. Each archetype defines its own deviation budget — this is what enables competitive bots (small bandwidth) vs extreme bots (large bandwidth).

```python
@dataclass(frozen=True)
class DeviationProfile:
    """Controls how far an archetype can deviate from solver baseline."""
    max_kl: float                # Max KL divergence from base
    max_per_action_shift: float  # Max absolute shift per action
    aggression_scale: float      # Multiplier for aggression offsets
    looseness_scale: float       # Multiplier for looseness offsets
    risk_scale: float            # Multiplier for risk identity offsets
    ego_fold_penalty: float      # Penalty applied to fold when ego > 0
```

**Predefined profiles:**

| Archetype | max_kl | max_per_action | aggression_scale | looseness_scale | risk_scale | ego_fold_penalty |
|-----------|--------|---------------|-----------------|----------------|------------|-----------------|
| Nit | 0.2 | 0.10 | 0.3 | 0.3 | 0.2 | 0.05 |
| Rock | 0.3 | 0.15 | 0.5 | 0.4 | 0.3 | 0.10 |
| TAG | 0.3 | 0.15 | 0.7 | 0.4 | 0.4 | 0.10 |
| Calling Station | 0.4 | 0.20 | 0.3 | 0.8 | 0.3 | 0.25 |
| LAG | 0.5 | 0.25 | 0.8 | 0.7 | 0.6 | 0.20 |
| Maniac | 0.6 | 0.30 | 1.0 | 1.0 | 0.8 | 0.30 |

Each archetype has:
- **Direction** (from personality anchors: aggression, looseness, etc.)
- **Magnitude** (from DeviationProfile: how far it's allowed to deviate)

This ensures competitive bots play close to GTO (small kl, small shifts) while extreme bots can deviate widely (large kl, large shifts) — but both are controlled.

**Note on extreme profiles**: Maniac at max_kl=0.6 will show significant EV drop and high variance vs baseline. That's by design — extreme archetypes are meant to be exploitable. But verify during validation that bb/100 loss stays within the -20 bb/100 statistical guardrail. If it doesn't, reduce the Maniac's scales before relaxing the guardrail.

### Clamping Guarantees

The clamp step uses the archetype's DeviationProfile:

**Clamp procedure (deterministic, ordered):**

```python
def clamp_divergence(base_probs, new_probs, base_logits, offsets, profile):
    """
    Clamp distorted probabilities to stay within archetype's deviation budget.
    All variables are local — no globals.

    Order:
    1. Per-action cap
    2. Renormalize
    3. KL check → scale offsets if needed
    4. Re-apply per-action cap (KL scaling may have violated it)
    5. Final renormalize
    """
    # Step 1: Apply per-action absolute shift cap (floor at 0 to prevent negatives)
    for i in range(len(new_probs)):
        shift = new_probs[i] - base_probs[i]
        if abs(shift) > profile.max_per_action_shift:
            new_probs[i] = max(0.0, base_probs[i] + np.sign(shift) * profile.max_per_action_shift)

    # Step 2: Renormalize with numeric stability floor
    # This floor does NOT create solver support — we are already inside support_mask.
    # It only stabilizes log() in KL computation.
    eps = 1e-12
    new_probs = np.maximum(new_probs, eps)
    new_probs = new_probs / new_probs.sum()

    # Step 3: Compute KL divergence (safe — all probs ≥ eps > 0)
    kl = np.sum(new_probs * np.log(new_probs / base_probs))

    # Step 4: If KL exceeds budget, scale offsets toward zero
    if kl > profile.max_kl:
        # Binary search for α ∈ [0, 1] such that
        # KL(softmax(base_logits + α * offsets) || base_probs) ≤ max_kl
        lo, hi = 0.0, 1.0
        for _ in range(20):  # converges in ~20 iterations
            mid = (lo + hi) / 2
            candidate = softmax(base_logits + mid * offsets)
            candidate_kl = np.sum(candidate * np.log(candidate / base_probs))
            if candidate_kl > profile.max_kl:
                hi = mid
            else:
                lo = mid
        new_probs = softmax(base_logits + lo * offsets)

        # Step 5: Re-apply per-action cap (KL scaling may have violated it)
        for i in range(len(new_probs)):
            shift = new_probs[i] - base_probs[i]
            if abs(shift) > profile.max_per_action_shift:
                new_probs[i] = max(0.0, base_probs[i] + np.sign(shift) * profile.max_per_action_shift)

    # Step 6: Final renormalize
    new_probs = new_probs / new_probs.sum()

    return new_probs
```

Both constraints (per-action cap and KL budget) are guaranteed to hold after this procedure.

**Important invariants:**
- `base_probs` used in KL computation must be the **renormalized masked** base distribution (after legal action filtering), not the original unmasked distribution. Otherwise KL becomes distorted.
- Clamping prioritizes **constraint satisfaction over perfect preservation of offset ratios**. The final renormalize step may slightly distort relative ratios among non-capped actions. This is acceptable — don't try to "fix" it, or you'll break constraint guarantees.

This guarantees:
- **Monotonic personality effects** — higher aggression always means more aggressive play
- **No distribution collapse** — can't converge to always-fold or always-jam
- **Archetype-appropriate bandwidth** — competitive bots stay tight, extreme bots get room
- **Debuggability** — you can inspect logit offsets per trait independently

### Trait → Deviation Mapping

Using existing personality anchors:

| Anchor | Offset Target | Effect |
|--------|--------------|--------|
| `baseline_aggression` (0-1) | +aggressive, -passive | More bets/raises, fewer checks/calls |
| `baseline_looseness` (0-1) | -fold, +call/raise | Wider continuing range |
| `risk_identity` (0-1) | +jam, -middle actions | More polarized (all-in or fold) |
| `ego` (0-1) | -fold | Resistance to folding, sunk cost |
| `poise` (0-1) | Gates emotional impact | High poise = emotions barely affect play |
| `adaptation_bias` (0-1) | Gates exploitation adjustments | See Opponent Exploitation below |

### Archetype Profiles (Character Play Styles)

The combination of anchor values naturally produces classic poker archetypes:

| Archetype | Aggression | Looseness | Behavior |
|-----------|-----------|-----------|----------|
| **Rock** (tight-passive) | Low | Low | Folds a lot, rarely raises, only plays premiums. Exploitable by stealing blinds. |
| **TAG** (tight-aggressive) | High | Low | Plays few hands but plays them hard. Raises or folds, rarely calls. Exploitable by noting what they 3-bet. |
| **Calling Station** (loose-passive) | Low | High | Calls everything, rarely raises. Exploitable by value betting thin, never bluffing. |
| **LAG** (loose-aggressive) | High | High | Plays many hands aggressively. Hard to read but bleeds chips with bad hands. Exploitable by trapping. |
| **Maniac** | Very High | Very High | Raises almost everything. Exploitable by calling down with medium hands. |
| **Nit** | Very Low | Very Low | Tighter than rock. Only plays top 5% hands. Exploitable by stealing constantly. |

### Opponent Exploitation (v2 — NOT in v1 scope)

> **This section is the full v2 spec for opponent exploitation. Do not implement during v1.**
> v1 ships with static personality distortion only. The `adaptation_bias` anchor exists in personality data but is unused until v2.

Previously handled through LLM prompt direction, which was inconsistent. Now algorithmic — deterministic adjustments based on tracked opponent statistics, gated by `adaptation_bias`.

**Runtime stat tracking** (per opponent, per session):

| Stat | What It Measures | Exploitation When High |
|------|-----------------|----------------------|
| VPIP | % of hands voluntarily played | Value bet thinner, don't bluff |
| PFR | % of hands raised preflop | Tighten calling range, 3-bet light |
| Fold-to-3bet | % folds facing a re-raise | 3-bet more as a bluff |
| Fold-to-cbet | % folds facing continuation bet | C-bet wider |
| Aggression Factor | (bets + raises) / calls | Call down lighter vs high AF |
| WTSD | % went to showdown | Bluff less vs high WTSD |

Much of this tracking already exists in `hand_ranges.py`.

**How `adaptation_bias` gates exploitation:**

```
adaptation_bias = 0.0  →  Ignore all opponent stats. Pure archetype.
adaptation_bias = 0.5  →  Half-weight opponent adjustments.
adaptation_bias = 1.0  →  Full exploitation adjustments applied.
```

Exploitation adjustments are additional logit offsets, scaled by `adaptation_bias`, applied after personality offsets and before clamping.

**Minimum sample size**: 15+ observed hands before adjustments activate.

### Integration with Existing Code

| Existing Component | How It's Used |
|-------------------|---------------|
| `personalities.json` | Anchor values drive logit offsets |
| `player_psychology.py` | Emotional state feeds into emotional offsets |
| `hand_ranges.py` | Opponent stat tracking (v2) |

---

## Layer 3: Expression Layer (LLM)

### Goal

The LLM receives the **decision that was already made** and generates personality-appropriate table talk, reactions, and dramatic sequences. It has zero influence on the poker action.

### Input to LLM

```python
@dataclass
class ExpressionContext:
    # What happened (read-only, already decided)
    action_taken: str              # 'fold', 'call', 'raise', 'all_in'
    raise_amount: int              # if applicable
    was_bluff: bool                # did personality deviate from value?
    hand_strength_bucket: str      # 'nuts', 'strong_made', 'air', etc.
    baseline_action: str           # what the solver baseline would have done (enables "I know this is crazy" dialogue)
    deviation_magnitude: float     # how far personality deviated from baseline (L1 shift)

    # Game situation
    pot_size: int
    community_cards: List[str]
    phase: str
    opponent_count: int            # number of active opponents (affects expression tone)
    opponent_last_action: str

    # Character context
    personality_name: str
    play_style: str
    verbal_tics: List[str]
    physical_tics: List[str]
    emotional_state: str           # 'composed', 'tilted', 'overconfident', etc.
    emotional_severity: str

    # Dramatic calibration
    drama_level: str               # from MomentAnalyzer
    drama_tone: str
```

### LLM Output

```python
@dataclass
class ExpressionOutput:
    dramatic_sequence: List[str]   # Actions and speech beats
    inner_monologue: str           # Flavor text (optional, for UI)
    hand_strategy: str             # Character's stated reasoning (can be deceptive)
    bluff_likelihood: int          # Expressed confidence (0-100, can lie)
```

### Key Principle: The LLM Can Lie

The expression layer knows whether the action was a bluff, but the LLM is encouraged to **deceive** through its table talk. A bluffing character might:
- Express confidence: "I've got you right where I want you"
- Show fake tells: "*glances nervously at chips*" (reverse tell)
- Narrate a false strategy: "Playing it safe here"

This creates a richer experience — the human can try to read the table talk for tells, knowing the AI might be lying.

### Integration with Existing Code

| Existing Component | How It's Used |
|-------------------|---------------|
| `prompt_manager.py` | Template system for expression prompts |
| `moment_analyzer.py` | Drama level calibration |
| `controllers.py` | Dramatic sequence format |
| `response_validator.py` | Validate LLM output structure |
| `core/llm/` | LLM client for generating expressions |

---

## New Controller: `TieredBotController`

### Decision Flow

```
Game Turn Triggered
    ↓
[Layer 1] Classify hand, board, situation → Node
    ↓
[Layer 1] Look up base strategy (action probabilities)
    ↓
[Layer 1] Apply multiway adjustments (if >2 players)
    ↓
[Layer 2] Convert to logits
    ↓
[Layer 2] Apply personality trait offsets
    ↓
[Layer 2] Apply emotional state offsets (gated by poise)
    ↓
[Layer 2] Softmax → clamp → final probabilities
    ↓
[Layer 2] Sample action from modified distribution
    ↓
[Layer 2] Resolve concrete sizing (map abstract → actual chips)
    ↓
[ACTION COMMITS HERE — sent to game engine immediately]
    ↓
[Layer 3] Build ExpressionContext with decided action (async)
    ↓
[Layer 3] LLM generates table talk and reactions (async)
    ↓
Expression delivered to UI when ready
```

### Class Design

```python
class TieredBotController:
    """
    AI player controller using 3-layer architecture:
    Solver baselines → personality distortion → LLM expression.
    """

    def __init__(
        self,
        player_name: str,
        personality: dict,             # From personalities.json
        strategy_table: StrategyTable, # Pre-loaded solver-derived tables
        psychology: PlayerPsychology,  # Existing psychology system
        llm_assistant: Assistant,      # For expression layer only
        prompt_config: PromptConfig,
    ):
        self.classifier = HandClassifier()     # Maps hands → classes
        self.board_reader = BoardTextureReader()  # Classifies board texture
        self.modifier = PersonalityModifier()  # Layer 2 (logit-space)
        self.expresser = ExpressionGenerator() # Layer 3

    def decide_action(self, game_state, game_messages) -> Dict:
        # Layer 1: Strategic core
        node = self._classify_decision_point(game_state)
        base_strategy = self.strategy_table.lookup(node)
        legal_actions = self._get_legal_actions(game_state)

        if game_state.num_active_players > 2:
            base_strategy = apply_multiway_adjustments(
                base_strategy,
                game_state.num_active_players,
                node.position,  # IP/OOP — position-sensitive adjustments
            )

        # Layer 2: Personality distortion (masked logit-space)
        emotional_state = self.psychology.get_current_emotional_shift()
        modified = self.modifier.modify_strategy(
            base_strategy,
            legal_actions,
            self.personality.anchors,
            emotional_state,
            self.deviation_profile,
        )
        action = modified.sample_action()
        sizing = self._resolve_sizing(action, game_state)

        # Action commits here — returned immediately
        result = {
            'action': action,
            'raise_to': sizing,
        }

        # Layer 3: Expression (async — kicked off, resolved separately)
        self.expresser.generate_async(
            action=action,
            sizing=sizing,
            game_state=game_state,
            personality=self.personality,
            emotional_state=emotional_state,
            llm=self.llm_assistant,
        )

        return result
```

---

## Game Mode Configuration

Three game modes, selectable per game:

| Mode | Decision Layer | Expression | Use Case |
|------|---------------|------------|----------|
| **Tiered Bot** (new, default) | Solver baselines + personality | LLM narrates | Strategic play with character |
| **Hybrid AI** (existing) | Bounded options + LLM picks | LLM decides + narrates | Current system, personality-driven |
| **Pure LLM** (existing) | Freeform LLM | LLM decides + narrates | Original experience, novelty |

Selection happens at game creation time. The controller factory already exists in the codebase.

---

## Data Pipelines

### Preflop: Chart Encoding

Preflop charts are curated from established sources and encoded manually into JSON.

```
1. Source charts (training sites, published GTO ranges)
     ↓
2. Encode per-hand frequencies into structured format (all 169 canonical hands)
     ↓
3. Validate against known ranges (e.g., UTG RFI ~15-18%)
     ↓
4. Store as poker/strategy/data/preflop_100bb_6max.json
```

No aggregation step. Combos are stored exactly as sourced.

**Chart format** (per scenario, per position):

```json
{
  "scenario": "rfi",
  "position": "CO",
  "frequencies": {
    "AA": {"raise_2.5bb": 1.0},
    "AKs": {"raise_2.5bb": 1.0},
    "AKo": {"raise_2.5bb": 1.0},
    "KQs": {"raise_2.5bb": 0.85, "fold": 0.15},
    "T9s": {"raise_2.5bb": 0.60, "fold": 0.40},
    "72o": {"fold": 1.0}
  }
}
```

Combos with mixed frequencies (e.g., KQs opens 85%) are preserved — this is how the bot plays unpredictably even at baseline.

### Postflop: PioSOLVER Export

Requires PioSOLVER Pro (or Edge) with UPI scripting support. Communicates via stdin/stdout text protocol.

```python
# pio/export_flops.py
#
# For each representative flop texture:
# 1. Set postflop tree (HU, SRP, standard sizings matching our action sets)
# 2. Set preflop ranges (based on position pair, from our preflop charts)
# 3. Solve flop to convergence
# 4. Export IP and OOP strategies by combo
# 5. Classify combos into hand classes per board
# 6. Write per-texture JSON files
```

```python
# pio/combo_to_class.py
#
# Pio exports at combo level (e.g., AhKs: check 0.12, bet_33 0.45, bet_67 0.43)
# We aggregate into hand classes:
#   - Group combos by class (e.g., all overpair combos → 'strong_made')
#   - Average frequencies within each class
#   - Store aggregated frequencies in per-texture strategy tables
```

**Key**: The preflop ranges fed into Pio for postflop solving must match our curated preflop charts. This ensures the postflop strategies are calibrated to the actual ranges our bot plays.

---

## v1 Scope

### In Scope

**Preflop (complete):**
- Curated 6-max charts (100bb) from established sources
- Open / 3-bet / 4-bet / defend by position
- All hand classes covered

**Postflop (HU SRP flops only):**
- Exactly 6 flop texture buckets with Pio-solved strategies
- 3 bet sizes max per node type
- Turn/river use heuristics with discipline rules

**Multiway:**
- Reduce bluff frequency
- Reduce raise frequency
- Increase check frequency
- Tighten call thresholds
- (Simple multipliers, not solved)

**Personality (static distortion):**
- Logit-space distortion using personality anchors
- KL divergence / per-action clamping
- Emotional state modifiers (gated by poise)

**Expression:**
- LLM narrates pre-decided actions
- Reuses existing prompt/drama infrastructure

### NOT in v1 Scope

- Real-time CFR or re-solving
- Range propagation engine
- Deep re-solving
- Adaptive opponent modeling (adaptation_bias exploitation)
- Emotional compounding effects
- Solved turn/river strategies

These are v2+.

---

## Validation (Numeric, Before LLM)

### BaselineSolverBot (Reference Entity)

A system-only reference bot used exclusively for testing. Not selectable in normal games.

**Configuration:**
- Uses Layer 1 (Strategic Core) only
- Samples from solver/chart mixed strategies using seeded RNG (it's a reference distribution, not a fixed script)
- Applies multiway adjustments
- Applies river guardrail
- **Skips Layer 2** (no personality distortion)
- **Skips Layer 3** (no LLM expression)

**Used for:**
- **Regression testing**: Baseline vs baseline should produce stable, expected stats across runs
- **EV ordering validation**: All personality bots should lose to baseline (deviations cost EV)
- **Layer isolation**: If baseline drifts → Layer 1 bug. If archetype ordering breaks → Layer 2 bug. If LLM chaos appears → Layer 3 bug.

### Postflop Bucket Averaging Validation

When aggregating Pio combo-level frequencies into `(made_tier, draw_modifier)` buckets, bimodal distributions within a bucket can produce misleading mixed strategies (e.g., half pure bet, half pure check averages to 50/50 mixed — a strategy that never actually occurs at combo level).

This is unavoidable at this abstraction level. Validate by:
- Comparing BaselineSolverBot's flop c-bet frequencies per texture against raw Pio combo-level frequencies
- If deviation > 5-7% on core nodes, bucket granularity is too coarse for that texture
- Solution: split the offending bucket or add a texture

### Tournament Protocol

Run bot-vs-bot tournaments (100k+ hands per matchup).

### Required Stat Tracking

| Stat | What It Validates |
|------|------------------|
| VPIP | Looseness is working (LAG > TAG, Station > Rock) |
| PFR | Aggression preflop (TAG/LAG raise more, Station/Rock less) |
| 3-bet % | Archetype-appropriate 3-betting |
| C-bet % by texture | Appropriate continuation betting |
| Fold-to-cbet | Archetype response to aggression |
| Aggression Factor (postflop) | Aggression anchor behavior — validates postflop aggression more cleanly than PFR alone |
| River bluff frequency | Risk identity + aggression interaction |
| W$SD (won $ at showdown) | Hand selection quality |
| bb/100 vs baseline bot | Overall strength. Passive archetypes (Nit/Rock/Station) should lose slightly to baseline — verifies clamps. Aggressive archetypes are *not* expected to lose against a non-adapting baseline (see Empirical Findings below). |

### Validation Criteria

1. **Stat separation**: Each archetype must show distinctly different stat profiles. If TAG and LAG have similar VPIP, personality math is wrong.
2. **Directional correctness**: Higher aggression anchor → higher PFR. Higher looseness → higher VPIP. Always.
3. **Bounded deviation**: No archetype should have VPIP > 80% or < 5%. No archetype should have PFR > VPIP.
4. **EV ordering (asymmetric — see Empirical Findings)**: Against a non-adapting BaselineSolverBot, the *passive* deviation direction costs EV as expected (Nit/Rock/Station lose slightly). The *aggressive* direction (TAG/LAG/Maniac) does **not** lose to baseline because a fixed solver-strategy reference cannot punish over-aggression. True symmetric EV ordering requires exploitation, which is v2 scope (Phase 6).

### Empirical Findings (2026-05-12)

After Phase 1 + Phase 2 implementation, bb/100 was measured against the BaselineSolverBot in both formats. **The expected ordering (Baseline > TAG > LAG > Rock > Station > Maniac) does not hold** — see results below.

**Heads-up (10,000 hands per matchup):**

| Archetype | bb/100 vs Baseline | 95% CI |
|-----------|--------------------|--------|
| Maniac    | **+43.0**          | [+35.9, +50.1] |
| LAG       | +14.4              | [+9.0, +19.8]  |
| TAG       | +7.0               | [+2.9, +11.0]  |
| Calling Station | +1.4         | [-4.6, +7.4]   |
| Baseline (mirror) | -1.0       | [-5.5, +3.4]   |
| Rock      | -6.9               | [-10.9, -3.0]  |
| Nit       | -7.0               | [-11.2, -2.8]  |

**6-max (5,000 hands per archetype, 1 archetype + 5 Baselines):**

| Archetype | bb/100 vs 5 Baselines | 95% CI |
|-----------|-----------------------|--------|
| Maniac    | **+73.4**             | [+56.4, +90.3] |
| LAG       | +39.8                 | [+26.8, +52.8] |
| Baseline (mirror) | +4.3          | [-6.0, +14.6]  |
| TAG       | +1.1                  | [-7.6, +9.7]   |
| Rock      | -4.9                  | [-13.3, +3.5]  |
| Calling Station | -7.4            | [-20.9, +6.2]  |
| Nit       | -8.7                  | [-17.3, -0.1]  |

**Interpretation:**

1. **The mirror sanity check passes** in both formats (Baseline-vs-Baseline CI brackets zero), confirming the simulator has no positional bias.
2. **Passive deviations (Nit/Rock/Station) lose slightly** — the personality clamps correctly cost EV in the passive direction.
3. **Aggressive deviations (TAG/LAG/Maniac) profit against the baseline.** This is a property of fixed-strategy references, not a Layer 2 bug: the baseline folds the math-correct amount to over-steals, and aggressors collect dead money. A real GTO opponent would widen its defending range (exploitation) and punish; the v1 baseline cannot.
4. **The -20 bb/100 hard guardrail holds universally** — the worst leak in either format is -8.7 bb/100. Personality math is bounded as designed.
5. **Aggression-magnitude gradient is clean** — Maniac > LAG > TAG, in both HU and 6-max. The deviation profiles produce the intended shape; they just don't produce the intended sign of EV.

**Consequence:** `bb/100 vs baseline` is a one-sided validation gate — it catches passive-direction Layer 2 bugs but cannot bound aggressive-direction edge. Validating that "deviations cost EV" in both directions requires either an exploiting baseline (Phase 6) or measurement against a different reference (e.g., HU-solver-aware reference). v1 validates archetype *shaping*; v2 will validate symmetric EV cost.

### Follow-up Findings — vs rule_bots (2026-05-12, addendum)

The Empirical Findings above measured tiered archetypes against BaselineSolverBot (the architecture's Layer-1-only reference). A follow-up pass measured them against deterministic rule-based opponents (`GTO-Lite`, `ABCBot`, `CaseBot`, `CallStation`, `ManiacBot`) at HU and 6-max scales. Two important refinements emerged:

**HU vs GTO-Lite (2,000 hands per matchup):** Every tiered archetype loses to the `pot_odds_robot` rule. Aggressive archetypes lose *more* than passive ones (Maniac -65.7 bb/100 vs Nit -24.7). Even Baseline (Layer 1 alone) loses -53.1. The 6-max preflop charts do not transfer to HU — pure structural mismatch, not a Layer 2 bug. **The "aggression beats baseline" property is specific to non-adapting solver baselines; it does not hold against a math-disciplined opponent that folds the correct amount.**

**6-max vs 5-rule_bot mix (500 hands, deterministic, two confirming runs):**

| Archetype | bb/100 vs mix | 95% CI |
|-----------|---------------|--------|
| Maniac    | **+1235.0**   | [+921.7, +1548.2] |
| LAG       | +340.4        | [+108.7, +572.1]  |
| TAG       | -173.7        | [-281.0, -66.4]   |
| Baseline  | -199.8        | [-323.6, -76.0]   |
| Nit       | -200.3        | [-291.1, -109.5]  |
| Rock      | -261.5        | [-365.2, -157.8]  |
| Calling Station | -295.7  | [-486.0, -105.4]  |

Initial read: "6-max is the home format." The per-opponent diagnostic (see `docs/analysis/TIERED_VS_RULE_BOTS_REPORT.md`) shows this is misleading. Maniac's +1235 bb/100 decomposes as:

- vs CallStation, ABCBot, GTO-Lite (3 passive rule_bots): **+7,400 BB combined gain**
- vs CaseBot, ManiacBot (2 aggressive rule_bots): **-5,200 BB combined loss**

**The tiered bot wins by harvesting dead money from passive rule_bots, and loses every individual matchup against aggressive ones (ManiacBot specifically takes -3,985 BB from Maniac).** Same pattern holds for losing archetypes: Nit also harvests passives (+5,950 BB combined) and gets blown out by ManiacBot (-4,798 BB). The 6-max headline is a function of opponent *mix* (net-passive in this test), not format.

**Refined architectural read:**

1. The architecture works as designed for **opponents whose strategy is fixed and exploitable**. Aggressive archetypes extract EV from passive opponents; passive archetypes extract less (or lose) but stay within their personality clamps.
2. The architecture **cannot defend against an opponent whose strategy ignores reads** (e.g., a constant-aggression bot). The strategy table doesn't reweight under sustained pressure from a specific opponent type.
3. The architecture **degrades against any opponent that plays math-correct** (GTO-Lite's pot-odds discipline) because tiered's archetype distortion injects -EV deviations that a disciplined opponent doesn't pay off.
4. **Postflop aggression collapses against deterministic opponents** (AggFactor 0.02-0.13 in diagnostic runs). When the opponent's preflop range is uniform, the strategy table's bucket assumptions don't match the live situation. Postflop bucket calibration likely assumes wider/more-realistic preflop ranges than rule_bots produce.

**These findings sharpen the case for Phase 6 (Opponent Exploitation).** A working exploitation layer would observe ManiacBot's stats over a sample window, tighten the tiered bot's calling range, and stop paying off his shoves. Without it, every tiered archetype is a fixed-strategy target for any opponent willing to ignore archetype variance.

### Statistical Guardrails (Hard Limits)

These are automatic fail conditions. If any archetype hits these over 100k hands, the personality math needs fixing:

| Guardrail | Limit |
|-----------|-------|
| **Max VPIP** | 85% |
| **Min VPIP** | 5% |
| **Max bb/100 loss vs baseline** | -20 bb/100 |
| **PFR > VPIP** | Never (logically impossible in correct play) |

These prevent "fun but broken" extremes from sneaking through validation.

---

## Implementation Phases

### Phase 1: Preflop Core (PRIORITY)

Fix preflop completely first. This is the highest-leverage work because:
- Stabilizes VPIP/PFR immediately
- Gives personality visible shape
- Easy to validate against known charts
- Reduces chaos early in hands
- Makes the rest feel credible even if postflop is imperfect

Deliverables:
- Curated preflop charts encoded as JSON for all 169 canonical hands (open, 3-bet, 4-bet, defend)
- Preflop strategy table with lookup by hand + position + scenario
- Personality modifier (logit-space) with DeviationProfile per archetype
- Debug logging for full distortion pipeline
- Bot-vs-bot validation of preflop stats (VPIP, PFR, 3-bet%)

### Phase 2: Postflop Foundation

- Board texture classifier
- Pio export scripts for representative flop textures (HU SRP)
- Postflop strategy tables with node-type-specific action sets
- Turn/river heuristic engine
- Multiway adjustment multipliers

### Phase 3: Personality Tuning

- Tune logit offset scales (AGGRESSION_SCALE, LOOSENESS_SCALE, etc.)
- Tune clamping thresholds (KL divergence, per-action caps)
- Run 100k+ hand validation tournaments
- Verify all archetypes show distinct, directionally correct stat profiles

### Phase 4: Expression Layer

- ExpressionContext builder
- Expression prompt templates (narrate pre-decided action)
- Integration with existing drama/moment system
- LLM generates table talk for decided actions

### Phase 5: Controller Integration

- TieredBotController wiring
- Game mode selection in UI
- Decision analysis logging
- End-to-end play testing

### Phase 6: Opponent Exploitation (v2)

- Formalize opponent stat tracking
- Exploitation logit offsets gated by `adaptation_bias`
- Minimum sample size gates
- Validation that high-adaptation characters adjust appropriately

---

## Design Decisions

### 1. Pre-solved blueprints only (no real-time CFR)

Postflop uses pre-solved strategy tables exclusively. The abstraction buckets are coarse enough that unusual situations still map to a node. If we later discover dead spots, we can add targeted solving for those cases.

### 2. No archetype labels — pure discovery

The human discovers opponent tendencies through play, like real poker. No labels, no hints. Rewards paying attention.

### 3. Validation through numeric stats then tournaments

Verification order:
1. Spot-check specific situations against known correct play
2. Compare preflop ranges against established charts
3. Bot-vs-bot tournaments with stat tracking (100k+ hands)
4. Verify archetype stat separation and directional correctness

### 4. Action-first, expression async

Action commits immediately. Expression resolves asynchronously. If needed, show a placeholder delay animation while table talk generates. Strategic responsiveness takes priority over theatrical latency.

**Expression failure isolation**: If LLM times out or errors, game state proceeds cleanly. Expression is optional data attached to a completed action — never a blocker. The action pipeline must be fully independent of expression generation.

### 5. Adaptation is algorithmic, gated by `adaptation_bias`

Two separate mechanisms in Layer 2:

**Emotional adaptation** (existing psychology, in v1):
- Emotional states modify play, gated by `poise`
- Reactive to game events

**Opponent exploitation** (new algorithmic, v2):
- Track opponent stats, apply exploitation offsets gated by `adaptation_bias`
- Previously done via inconsistent LLM prompting, now deterministic math

### 6. Split data sources: curated preflop + PioSOLVER postflop

**Preflop**: Curated 6-max charts from established sources. Preflop at 100bb is essentially solved public knowledge — no need to run a solver for it. Encode charts for open, 3-bet, 4-bet, and defend scenarios.

**Postflop**: PioSOLVER exports for representative flop textures (HU SRP). Postflop strategies are board-texture-dependent and can't be reliably curated by hand. Pio ranges must be calibrated to match our curated preflop charts.

We do not implement our own solver. External tools provide the strategic foundation; our system provides personality, emotion, and expression on top.

---

## Architectural Invariants

Guardrails against future complexity creep. These must hold across all versions.

1. **Preflop is rank-class exact (169 hands).** Never bucket further. Suit-exact only for postflop.
2. **Postflop buckets preserve made-tier × draw-modifier.** No single-axis collapse.
3. **Personality distortion never overrides solver support.** Actions with base probability 0 stay at 0. Logit offsets shift supported probabilities; they don't create new actions.
4. **River bluff ratios are bounded.** `bluff_freq ≤ value_freq × MAX_BLUFF_RATIO` — always enforced after distortion.
5. **All deviations are clamped per archetype via DeviationProfile.** No global constants for clamp thresholds.
6. **LLM never influences action selection.** Expression layer is read-only on decisions.
7. **Numeric validation precedes expression integration.** Bot-vs-bot stats must pass before LLM narration is wired in.
8. **Debug logging captures full distortion pipeline.** Base → multiway → personality → emotional → clamp → action.
9. **Preflop and postflop solver inputs must remain range-consistent.** If preflop charts are adjusted, Pio postflop solves must be regenerated. Otherwise postflop baselines become miscalibrated to actual ranges.

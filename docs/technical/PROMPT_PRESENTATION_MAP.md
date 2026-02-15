---
purpose: Maps what each AI archetype sees in the lean prompt across all layers
type: reference
created: 2026-02-15
last_updated: 2026-02-15
---

# Prompt Presentation Map

What each AI archetype sees in the lean prompt. This is the single reference
for understanding how personality → profile → prompt content works end-to-end.

## The Pipeline

```
Personality anchors (looseness, aggression)
  → classify_from_anchors() → profile_key (TAG/TP/LAG/LP/default)
  → STYLE_PROFILES[key] → OptionProfile (thresholds + presentation fields)
  → generate_bounded_options() → options (action + EV + rationale)
  → apply_composed_nudges() → options (rationale replaced with playstyle phrases)
  → apply_emotional_window_shift() → options (may add/remove/reframe)
  → _build_lean_prompt() → final prompt text the LLM sees
```

Psychology also feeds a **playstyle** (commanding/aggro/poker_face/guarded) which
controls Phase 0 hand plan content. The archetype (TAG/LAG/etc.) and playstyle
are correlated but not identical — archetype drives option generation and prompt
presentation, playstyle drives strategic framing.

## Archetype Classification

From `poker/archetypes.py`. Based on personality anchor values (0–1 scale):

| Looseness | Aggression ≥ 0.50 | Aggression < 0.50 |
|-----------|-------------------|-------------------|
| < 0.45 (tight) | **TAG** (tight_aggressive) | **TP** (tight_passive) |
| 0.45–0.65 (balanced) | **default** | **default** |
| > 0.65 (loose) | **LAG** (loose_aggressive) | **LP** (loose_passive) |

Example personalities: Napoleon → LAG, Joan of Arc → TAG, Buddha → TP, Alice → LP.

## Layer 1: System Prompt

Universal across all archetypes. Always the lean system prompt:

```
You are a poker player. Pick one option. Return JSON: {"reasoning": "...", "choice": <number>}
```

No personality, no character instruction, no drama guidance. The LLM is a
pure option-picker in lean bounded mode.

## Layer 2: Phase 0 — Hand Plan (optional)

When `hand_plan=True`, fires once at hand start. The plan stays in the
conversation thread so Phase 1 decisions see it as prior context.

**When enabled, replaces style hints** (Layer 5) — the plan IS the
personality-driven context.

| Component | TAG | TP | LAG | LP | Default |
|-----------|-----|-----|-----|-----|---------|
| Cards + position + stack | ✓ | ✓ | ✓ | ✓ | ✓ |
| Mindset frame | "Extract maximum value" | "Control the pot" | "Target weakness" | "Control the pot" | "Trust the math" |
| Playstyle cue | "You play aggressively for max value" | "You play cautiously" | "You play aggressively and attack weakness" | "You play cautiously" | "You play a balanced game" |
| Exploit tips (medium+) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Stat lines (full) | Stack ratio, pot-to-stack | Stack ratio | Threat info, stack ratio | Stack ratio | Stack ratio |

The playstyle cues come from psychology's active_playstyle (commanding/aggro/
poker_face/guarded), not directly from the archetype key. In practice they
correlate: TAG → commanding/poker_face, LAG → aggro/commanding, etc.

## Layer 3: Situation Line (universal)

Always shown. Not profile-gated.

```
Street: Flop | Stack: 45 BB | Pot: 6.5 BB
```

**Not yet profile-gated.** Every archetype sees the same situation line.

## Layer 4: Contextual Information

### 4a. Cards (universal)

```
Cards: Ah Kh | Board: Qh Jh 2s
```

Always shown. Not profile-gated.

### 4b. Hand Classification / Breakdown (universal, not yet gated)

**Preflop:** `Hand: Premium pair` or `Hand: Suited broadway`
**Postflop:** `You have: flush draw (Ah Kh) with two hearts on board`

Currently shown for ALL profiles. Could be gated in future — analytical
profiles (TAG/default) might benefit more from explicit hand narration,
while intuitive profiles (LAG/LP) might play better without it.

| Component | TAG | TP | LAG | LP | Default | Gated? |
|-----------|-----|-----|-----|-----|---------|--------|
| Preflop classification | ✓ | ✓ | ✓ | ✓ | ✓ | **No** |
| Postflop hand breakdown | ✓ | ✓ | ✓ | ✓ | ✓ | **No** |

### 4c. Street Action Summary (universal, not yet gated)

```
Action: Opp raises 3BB → You call
```

Shown postflop when there's been betting action on the current street.
Currently shown for ALL profiles.

| Component | TAG | TP | LAG | LP | Default | Gated? |
|-----------|-----|-----|-----|-----|---------|--------|
| Action summary | ✓ | ✓ | ✓ | ✓ | ✓ | **No** |

### 4d. Board Read (profile-gated via `board_read`)

```
Board read: two-tone (2 hearts), connected (Q-J), flush draw + straight draw possible
```

Analytical profiles get a 1-line board texture read postflop. The idea:
TAG/default think about board texture when deciding; LAG/LP play their hand,
not the board.

| Component | TAG | TP | LAG | LP | Default | Gated? |
|-----------|-----|-----|-----|-----|---------|--------|
| Board read | **✓** | ✗ | ✗ | ✗ | **✓** | **Yes** — `profile.board_read` |

**Emotional override:** Suppressed when emotional_shift is extreme AND state
is tilted/shaken/dissociated. Extreme overconfident does NOT suppress
(they still think clearly, just too boldly).

## Layer 5: Style Hint (profile-gated via `style_hint`)

A 1-line directive injected before the options. Omitted when `hand_plan`
is enabled (the plan replaces it).

| Profile | Style Hint |
|---------|-----------|
| TAG | "Fold weak hands. When you do play, bet aggressively for value." |
| TP | "Fold most hands. Only continue with strong holdings." |
| LAG | "Play many hands and apply pressure — raise or fold, avoid flat calls." |
| LP | "See more flops — call liberally, but don't overcommit without a hand." |
| default | *(empty — no hint)* |

## Layer 6: Options (the core decision menu)

### 6a. Option Generation (profile-gated via OptionProfile thresholds)

The same hand produces different option menus per profile. Key differences:

| Threshold | TAG | TP | LAG | LP | Default |
|-----------|-----|-----|-----|-----|---------|
| Fold blocking | Hard (2.5x) | Hard (2.5x) | Easier (1.8x) | Easy (1.5x) | Medium (2.0x) |
| Call EV bar | High (2.0x) | High (2.0x) | Medium (1.5x) | Low (1.4x) | Medium (1.7x) |
| Raise EV bar | Medium (0.55) | High (0.65) | Medium (0.55) | High (0.65) | Medium (0.60) |
| Bet sizing | Medium-large | Small | Medium-overbet | Small | Medium |
| Bluff raises | ✗ | ✗ | **✓ (15%)** | ✗ | ✗ |
| Postflop raise limit | 2 | 1 | 3 | 1 | unlimited |
| Check penalty | > 40% equity | ✗ | > 35% equity | ✗ | ✗ |
| Check promotion | conditional | always | suppress_if_raises | always | default |

### 6b. Range Gate (preflop, when `preflop_range_gate=True`)

Out-of-range hands get biased EV labels:
- Fold → `[+EV]` with "outside your X% range" rationale
- Call → `[-EV]` with "speculative" rationale
- Raise → limited to 1 option, labeled `[-EV]`

In-range hands use standard EV labels (no upward bias — this is the
remaining bottleneck).

### 6c. Composed Nudges (when `composed_nudges=True`)

Raw rationale replaced with profile-specific phrases from `NUDGE_PHRASES`:

| Nudge Key | TAG example | LAG example |
|-----------|-------------|-------------|
| `raise_value` | "Extract value — bet with conviction" | "Fire away — they can't handle the heat" |
| `fold_correct` | "Discipline pays — save your chips" | "Cut your losses. Pick a better spot" |
| `call_strong` | "Easy call — the math checks out" | "Stay in the fight" |
| `check_free` | "See a free card — gather info" | "Take a free card. No rush" |

### 6d. EV Labels (profile-gated via `show_ev_labels`)

The `[+EV]`, `[-EV]`, `[neutral]`, `[marginal]` brackets next to each option.

| Profile | Sees EV Labels | Rationale |
|---------|---------------|-----------|
| TAG | **✓** | Thinks in math terms — labels reinforce tight discipline |
| TP | **✓** | Needs math signal to stay tight |
| LAG | **✗** | EV labels anchor toward GTO, suppressing loose play |
| LP | **✗** | Plays feel/odds, not math — labels would over-tighten |
| default | **✓** | Analytical default gets full information |

**PromptConfig override:** `show_ev_labels: Optional[bool]` — `None` defers
to profile (above), `True`/`False` forces for A/B experiments.

**What TAG sees:**
```
1. FOLD  [+EV]  — Discipline pays — save your chips
2. CALL  [marginal]  — Borderline. Trust your read
3. RAISE 3BB  [-EV]  — Thin value — bet with conviction
```

**What LAG sees:**
```
1. FOLD  — Cut your losses. Pick a better spot
2. CALL  — Stay in the fight
3. RAISE 3BB  — Fire away — they can't handle the heat
```

### 6e. Option Ordering

Controlled by `PromptConfig.option_order`:
- `'default'` — generator order (fold/check first, raises last)
- `'shuffle'` — random order per decision (prevents position bias)
- `'ev_descending'` — best EV first

Not profile-gated. Applied uniformly.

## Layer 7: Emotional Window Shift

Applied after option generation, before prompt rendering. Shifts the
option window along passive↔aggressive spectrum based on psychology state.

| Severity | Effect |
|----------|--------|
| None/composed | No change |
| Mild (70% chance) | Adds 1 option on the emotional end |
| Moderate (85% chance) | Adds option + narrative framing replaces rationale |
| Extreme (95% chance) | Adds option + removes opposite-end option + narrative framing |

**Narrative framing examples** (replaces nudge phrases at moderate+):

| State | Raise text | Fold text |
|-------|-----------|-----------|
| Tilted | "Make them pay" | "Folding again? Really?" |
| Overconfident | "You can't lose right now" | "Fold? You? Inconceivable." |
| Shaken | "Going big? Really?" | "Get out while you can" |
| Dissociated | "Raise." | "Fold." |

Dissociated strips ALL rationale to bare minimum — the player has minimal
cognitive bandwidth.

**Board read suppression:** Extreme tilted/shaken/dissociated → no board read.
Extreme overconfident → board read still shown.

**Math blocking:** Always re-applied after emotional shift as safety net.
Emotions never override fold-blocking or call-blocking.

## Summary: Complete Prompt Comparison

### TAG (tight_aggressive) — "The Analyst"

```
Cards: Ah Kh | Board: Qh Jh 2s
You have: flush draw (Ah Kh) with two hearts on board
Street: Flop | Stack: 45 BB | Pot: 6.5 BB
Action: Opp raises 3BB
Board read: two-tone (2 hearts), connected (Q-J), flush draw + straight draw possible
Fold weak hands. When you do play, bet aggressively for value.

1. FOLD  [+EV]  — Discipline pays — save your chips
2. CALL  [marginal]  — Borderline. Trust your read
3. RAISE 6BB  [+EV]  — Extract value — bet with conviction
```

### LAG (loose_aggressive) — "The Brawler"

```
Cards: Ah Kh | Board: Qh Jh 2s
You have: flush draw (Ah Kh) with two hearts on board
Street: Flop | Stack: 45 BB | Pot: 6.5 BB
Action: Opp raises 3BB
Play many hands and apply pressure — raise or fold, avoid flat calls.

1. FOLD  — Cut your losses. Pick a better spot
2. CALL  — Stay in the fight
3. RAISE 6BB  — Fire away — they can't handle the heat
4. RAISE 10BB  — Apply maximum pressure
```

### TP (tight_passive) — "The Rock"

```
Cards: Ah Kh | Board: Qh Jh 2s
You have: flush draw (Ah Kh) with two hearts on board
Street: Flop | Stack: 45 BB | Pot: 6.5 BB
Action: Opp raises 3BB
Fold most hands. Only continue with strong holdings.

1. CHECK  [+EV]  — Check (pot control — protect your stack)
2. CALL  [marginal]  — Proceed with caution
3. RAISE 4BB  [+EV]  — Tight is right — bet only the best
```

### LP (loose_passive) — "The Caller"

```
Cards: Ah Kh | Board: Qh Jh 2s
You have: flush draw (Ah Kh) with two hearts on board
Street: Flop | Stack: 45 BB | Pot: 6.5 BB
Action: Opp raises 3BB
See more flops — call liberally, but don't overcommit without a hand.

1. CHECK  — Check (pot control — protect your stack)
2. CALL  — See what develops
3. RAISE 4BB  — Tight is right — bet only the best
```

## What's NOT Yet Profile-Gated

These components are universal but could be differentiated:

| Component | Current | Potential Gating |
|-----------|---------|-----------------|
| Hand classification (preflop) | All profiles | Could suppress for LP/TP (they don't think in hand categories) |
| Hand breakdown (postflop) | All profiles | Could suppress for LP (plays feel, not made-hand analysis) |
| Action summary | All profiles | Could suppress in dissociated state |
| `(recommended)` tag on fold | Same logic all profiles | LAG could suppress for in-range hands |
| Raise sizing spread | Profile thresholds | Could show fewer sizes for intuitive profiles |

## Extensibility Pattern

Adding a new per-profile prompt manipulation:

1. Add a field to `OptionProfile` with a sensible default
2. Set per-profile values in `STYLE_PROFILES`
3. Consume in `_build_lean_prompt()` via the `profile` parameter
4. Optionally add a `PromptConfig` override (`Optional[X] = None`) for A/B testing

Examples already following this pattern: `board_read`, `show_ev_labels`, `style_hint`.

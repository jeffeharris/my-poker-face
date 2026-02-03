# Skill Ideas for Coaching Progression

Possible new skills organized by category. Each includes trigger conditions,
what "correct" looks like, and what data is needed (most use existing context).

---

## Preflop Skills

### Respect Preflop Raises
- **Trigger**: Preflop, `cost_to_call` > 1 BB (someone raised)
- **Correct**: Play top 15–20% only, fold bottom of normal range
- **Incorrect**: Calling raises with marginal hands you'd normally open
- **Data needed**: Already have `cost_to_call`, `big_blind`, `canonical`
- **Gate fit**: 2 or 3

### Don't Limp
- **Trigger**: Preflop, not in blinds, no raise ahead (`cost_to_call` == BB)
- **Correct**: Raise or fold — never flat-call the big blind
- **Incorrect**: `action == 'call'` preflop when not in blinds and no raise
- **Data needed**: Already there (`is_blind`, `cost_to_call`, `big_blind`)
- **Gate fit**: 1 or 2

### Blind Defense
- **Trigger**: Preflop, player is in blinds, facing a raise
- **Correct**: Defend with top 25–30% (wider than normal — you already have money in)
- **Incorrect**: Folding playable hands too often, or calling with junk
- **Data needed**: `is_blind` and `cost_to_call` already in context
- **Gate fit**: 2 or 3

---

## Post-Flop (Single Street)

### Don't Slowplay Monsters
- **Trigger**: Post-flop, `is_strong_hand` (two pair+), `can_check`
- **Correct**: Bet or raise — build the pot
- **Incorrect**: Checking with a strong hand when there's money to be won
- **Data needed**: Already there
- **Gate fit**: 2
- **Priority**: High — zero new data, triggers often, clear right answer

### Continuation Bet
- **Trigger**: Flop, player raised preflop, first to act or checked to
- **Correct**: Bet roughly 50–75% of the time (follow through on preflop aggression)
- **Incorrect**: Checking every flop after raising preflop
- **Data needed**: Would need `player_raised_preflop` flag derived from `hand_actions`
- **Gate fit**: 3
- **Priority**: High — very frequent trigger, teaches initiative

### Don't Bet Into Strength
- **Trigger**: Post-flop, opponent raised on a previous street, player has marginal hand
- **Correct**: Check or fold — don't lead into the aggressor
- **Incorrect**: Donk-betting into the preflop raiser with a weak hand
- **Data needed**: Could derive "opponent was last aggressor" from `hand_actions`
- **Gate fit**: 3 or 4

### River Value Betting
- **Trigger**: River, `is_strong_hand`, `can_check` (checked to player)
- **Correct**: Bet — extract value on the last street
- **Incorrect**: Checking back a strong hand on the river
- **Data needed**: Already there
- **Gate fit**: 3
- **Priority**: High — zero new data, clear lesson

---

## Multi-Street Situations

### Pot Control with Medium Hands
- **Trigger**: Turn, player has one pair (`is_marginal_hand`), no opponent aggression
- **Correct**: Check — control the pot size with a mediocre hand
- **Incorrect**: Betting every street with middle pair, inflating the pot
- **What it teaches**: Not every hand needs three streets of betting
- **Data needed**: Already have `is_marginal_hand`, `opponent_bet_turn`
- **Gate fit**: 3 or 4
- **Priority**: High — concept most beginners never learn

### Give Up on Failed Bluffs
- **Trigger**: Turn or river, player bet the flop with air/weak hand, still has air
- **Correct**: Check — stop bleeding chips when the bluff didn't work
- **Incorrect**: Firing again with no equity and no fold equity
- **Data needed**: `player_bet_flop` + `is_air` on later streets (both exist)
- **Gate fit**: 4
- **Priority**: Medium — pairs well with "Have a Plan" as the flip side

### Check-Raise Recognition
- **Trigger**: Post-flop, player bet, opponent raised
- **Correct**: Fold marginal hands, continue only with strong hands
- **What it teaches**: Post-flop raises are much stronger than preflop raises
- **Data needed**: Detectable from `hand_actions` within current street
- **Gate fit**: 3 or 4

### Triple Barrel Awareness
- **Trigger**: River, opponent has bet flop + turn + river
- **Correct**: Only call with strong hands, fold marginal
- **Incorrect**: Calling all three streets with one pair
- **Data needed**: Extend `opponent_double_barrel` → add `opponent_triple_barrel`
- **Gate fit**: 4
- **Priority**: Medium — extends existing tracking

### Probe Betting
- **Trigger**: Turn or river, opponent was preflop aggressor but checked this street
- **Correct**: Bet with decent hands (opponent showed weakness by checking)
- **Incorrect**: Checking back when the aggressor gives up
- **Data needed**: Track who raised preflop and whether they continued
- **Gate fit**: 4

### Don't Call Three Streets Light
- **Trigger**: River, player called on flop AND turn, still has marginal hand
- **Correct**: Fold — you've paid enough with a mediocre hand
- **Incorrect**: Calling a third bet with one pair
- **Data needed**: Track player's call history across streets from `hand_actions`
- **Gate fit**: 4

---

## Implementation Priority

Roughly ordered by impact and ease of implementation:

| # | Skill | New Data? | Trigger Frequency | Gate |
|---|---|---|---|---|
| 1 | Don't Slowplay Monsters | None | High | 2 |
| 2 | River Value Betting | None | Medium | 3 |
| 3 | Pot Control with Medium Hands | None | High | 3–4 |
| 4 | Continuation Bet | `player_raised_preflop` flag | Very high | 3 |
| 5 | Give Up on Failed Bluffs | None | Medium | 4 |
| 6 | Blind Defense | None | High | 2–3 |
| 7 | Triple Barrel Awareness | `opponent_triple_barrel` | Low | 4 |
| 8 | Respect Preflop Raises | None | Medium | 2–3 |
| 9 | Don't Limp | None | Medium | 1–2 |
| 10 | Check-Raise Recognition | Derive from hand_actions | Low | 3–4 |
| 11 | Don't Bet Into Strength | Derive from hand_actions | Medium | 3–4 |
| 12 | Probe Betting | Track preflop aggressor | Low | 4 |
| 13 | Don't Call Three Streets Light | Track call history | Low | 4 |

---

## Coaching Language Tiers

### The Problem

The current skill names and coaching language assume poker literacy. "Fold hands
outside the top 35% preflop" and "Position Awareness" mean nothing to someone
who just learned what a flush is. The system evaluates correctly at every level —
a beginner calling with 7-2 and a pro calling with 7-2 are the same mistake —
but the way the coach *talks* about it should be different.

### Core Idea

Tie coaching language to gate progression. The vocabulary grows as the player
advances. Early gates use plain English; later gates introduce standard poker
terminology. By the time a player reaches Gate 3, they've absorbed the vocabulary
through playing — the coach taught it to them naturally.

### What Changes Per Tier

| Layer | Beginner (Gate 1) | Standard (Gate 3+) |
|---|---|---|
| Skill names | "Bad Hand — Fold It" | "Fold Trash Hands" |
| Skill descriptions | "Some hands just aren't worth playing — let them go" | "Fold hands outside the top 35% preflop" |
| Coaching prompts | "You had a 7 and a 2 — those cards don't work well together" | "7-2 offsuit is outside your playable range" |
| Gate labels | "Gate 1: The Basics" | "Gate 1: Preflop Fundamentals" |
| Proactive tips | "You're going early — only play if your cards look really good together" | "You're UTG — tighten to top 10%" |

### What Stays the Same

- Trigger logic (same situations matter at every level)
- Evaluation logic (correct/incorrect doesn't change)
- Advancement thresholds (same accuracy to progress)
- Internal skill IDs

### Language Mapping

| Internal Skill | Beginner | Intermediate | Advanced |
|---|---|---|---|
| `fold_trash_hands` | "Bad Hand — Fold It" | "Fold Weak Hands" | "Fold Trash Hands" |
| `position_matters` | "Going First is Harder" | "Position Matters" | "Position Awareness" |
| `raise_or_fold` | "Bet Big or Walk Away" | "Raise or Fold" | "Raise or Fold" |
| `flop_connection` | "Did the Flop Help You?" | "Flop Connection" | "Flop Connection" |
| `bet_when_strong` | "You've Got a Good Hand — Bet!" | "Bet Your Strong Hands" | "Bet When Strong" |
| `checking_is_allowed` | "No Need to Bet Every Time" | "Checking is OK" | "Checking is Allowed" |
| `draws_need_price` | "Chasing Cards Costs Money" | "Draws Need the Right Price" | "Draws Need Price" |
| `respect_big_bets` | "A Big Bet Means They're Serious" | "Respect Large Bets" | "Respect Big Bets" |
| `have_a_plan` | "Decide Before You Bet" | "Stick to Your Plan" | "Have a Plan for the Hand" |

### How Language Progresses

The language tier is driven by the player's highest unlocked gate, not a separate
setting. As you advance, the coach starts using real poker terms:

- **Gate 1**: Plain English only. No jargon. "Your cards don't match the board."
- **Gate 2**: Introduce basic terms. "You missed the flop" instead of
  "your cards don't connect." Start using "pair", "fold", "raise" naturally.
- **Gate 3**: Standard poker vocabulary. "Pot odds", "position", "drawing hand."
  The player has heard these concepts enough that the terms make sense.
- **Gate 4**: Full poker language. "Double barrel", "probe bet", "pot control."

If a player regresses on a Gate 1 skill after reaching Gate 3, the coach still
uses Gate 3 language — they've already learned the vocabulary, so there's no
reason to dumb it down. The language tier generally moves forward with gate
progression, but can be adjusted dynamically by the coach (see
"Coach-Driven Language Adjustment" below).

### Self-Reported Level & Gate Placement

The existing `effective_level` setting (beginner/intermediate/experienced)
controls starting position and observation period:

| Self-Reported Level | Starting Gate | Prior Gates | Language Starts At |
|---|---|---|---|
| **Beginner** | Gate 1 unlocked, skills at Introduced | — | Beginner |
| **Intermediate** | Gate 2 unlocked, skills at Introduced | Gate 1 in **observation mode** | Intermediate |
| **Experienced** | Gate 3 unlocked, skills at Introduced | Gates 1–2 in **observation mode** | Advanced |

### Observation Mode for Skipped Gates

When a player self-reports as intermediate or experienced, they skip earlier gates
but those skills still get silently tracked. This handles the common case where
someone says "I know poker" but actually has fundamental leaks.

**How observation mode works:**

1. Skipped gate skills are set to `Practicing` state (not `Introduced` — no
   tutorial needed, just measurement)
2. The coach does **not** give feedback on these skills during the observation
   window
3. After a threshold (e.g., 20 opportunities), the system checks accuracy:
   - **≥ 60% accuracy**: Skill auto-advances to `Reliable` — player really does
     know this, never bother them about it
   - **< 60% accuracy**: Skill drops to `Introduced` and the coach starts giving
     active feedback — "I noticed you might benefit from working on X"
4. This avoids patronizing experienced players while catching people who
   overestimate their level

**Example flow for an "intermediate" player:**

1. Gate 1 skills silently tracked from hand 1
2. Gate 2 skills actively coached from hand 1
3. After 20 hands: Gate 1 `fold_trash_hands` is at 85% → auto-promote to Reliable
4. After 20 hands: Gate 1 `raise_or_fold` is at 45% → drop to Introduced, coach
   starts giving feedback: "I noticed you're limping a lot — try raising or
   folding instead of just calling"

### Vocabulary Introduction

As a nice touch, the coach could explicitly introduce terms as the player
advances. When a skill transitions from beginner to intermediate language:

> "By the way, poker players call what you've been doing 'folding trash.'
> When your cards aren't in the top 35% of starting hands, that's what we
> call a trash hand. You've been doing great at recognizing them."

This turns vocabulary from jargon into a reward — "you've earned the right to
talk like a poker player."

### Coach-Driven Language Adjustment

The language tier normally advances with gate progression, but the coach LLM
can override it in either direction based on how the player communicates.

**Why:** A player might pick "intermediate" but then ask "what does pot odds
mean?" — or pick "beginner" but casually say "I had the right odds to call."
The gate system measures play skill, but language comprehension is a separate
axis. The coach is already reading the player's chat messages — it's the
natural place to detect vocabulary fit.

**The tool:** Give the coach an `adjust_language_tier` tool it can call:

```
adjust_language_tier(
    direction: "simpler" | "standard" | "advanced",
    reason: str
)
```

**When the coach downgrades:**
- Player asks "what does X mean?" about poker terms
- Player seems confused by coaching feedback ("I don't understand what
  you mean by position")
- Player uses colloquial descriptions instead of terms ("the three cards
  in the middle" instead of "the flop")

**When the coach upgrades:**
- Player uses poker terms naturally in chat ("I had pot odds to call",
  "I was out of position")
- Player discusses strategy concepts unprompted ("should I have c-bet there?")
- Player corrects the coach's simplified language ("I know what a draw is")

**Behavior:**
- Adjustments are soft — the coach shifts one tier at a time, not from
  beginner straight to advanced
- Downgrades are temporary by default. If the coach drops to simpler
  language, it creeps back up as the player progresses through gates.
  The idea is "help them through this moment" not "permanently label
  them as confused"
- Upgrades are sticky. If the coach detects the player knows the
  vocabulary, there's no reason to go back to simplified terms
- Every adjustment is logged with a reason, so you can see patterns
  ("60% of beginners get upgraded by Gate 2" → the natural progression
  is working)

**Example flow:**

1. Player starts at beginner language tier (Gate 1)
2. On hand 12, player asks in chat: "what's a flush draw?"
3. Coach answers the question AND keeps language at beginner (already there)
4. On hand 30, player reaches Gate 2
5. Language would normally bump to intermediate, and it does
6. On hand 35, player says in chat: "I knew I should've folded — bad pot odds"
7. Coach calls `adjust_language_tier("advanced", "player used 'pot odds'
   correctly in context")`
8. Coach now uses full poker vocabulary even though player is only at Gate 2

**Implementation:**
- The tool writes to a `language_tier` field on the player's coach profile
  (new column on `player_coach_profile`, or in a JSON settings blob)
- The coaching prompt builder reads `language_tier` to pick skill names,
  descriptions, and prompt phrasing
- The gate-based default still applies if the coach has never adjusted:
  `effective_language_tier = language_tier_override or gate_based_default`
- Tool calls are logged to `api_usage` or a dedicated `language_adjustments`
  table for analytics

### Implementation Notes

- Language tiers could be stored as a dict per skill in `skill_definitions.py`,
  keyed by level: `{'beginner': 'Bad Hand — Fold It', 'intermediate': ...}`
- The coaching prompt template already receives skill name/description — just
  swap them based on the player's current language tier
- The frontend `ProgressionDetail` and `ProgressionStrip` components read skill
  names from the API response — the backend just sends the right name for the
  player's tier
- No new database tables needed — `effective_level` already exists on the profile

---
purpose: How to compose priced spot-tendency leaks, defenses, and attacks onto AI personalities to create an in-character skill gradient
type: design
created: 2026-05-29
last_updated: 2026-05-29
---

# Wiring leaks/defenses/attacks onto personalities

Companion to `PERSONALITY_PRICING_AND_VARIETY.md` (which *builds + prices* the
tendencies). This doc is the **composition scheme**: how to attach them to the 62
`personalities.json` characters so the variety/skill system is live in the game.

## The composition model

A bot is a bundle of three move-types (the symmetric system from the catalog):

> **{ weaknesses (leaks) · defenses (frequency guards) · attacks (detectors+counters) }**

The **skill gradient is the mix**, and a leak's cost is a **matchup** (a weakness vs
one opponent, a defense vs another — see the leak×counter matrix in the pricing doc),
so the gradient is really a matchup graph, not a scalar.

| Tier | Leaks | Defenses | Attacks | Net |
|---|---|---|---|---|
| **Fish / weak** | 2–3 in-character | (river guardrail on) | none | handicapped |
| **Mid / regular** | 1 signature (flavor, free) | on | none | ~even |
| **Elite** | none | on | adaptive overbet (+ exploitation) | apex |

## What's wireable, and how

- **Leaks (weaknesses) — pure JSON, zero new code.** A `spot_tendencies` key on a
  character's `personalities.json` entry merges onto its archetype profile:
  ```json
  "spot_tendencies": [["sticky", 0.7]]
  ```
  Resolution (`TieredBotController.deviation_profile` → `_effective_spot_tendencies`):
  a non-empty character config **replaces** the archetype profile's tendencies;
  absent/empty **inherits**; an explicit `[]` opts out. Strength is bounded by the
  character's archetype `max_per_action_shift` (so the same strength is a bigger
  reshape on a `calling_station` carrier than a `tag` one).
- **Attacks — one small read (added 2026-05-29).** `"adaptive_overbet": true` in a
  character's entry enables the surgical overbet (fires only on a detected payer).
  Read in `TieredBotController.__init__` from `personality_config`; default off.
  Requires a live `opponent_model_manager` (present in real games, not bare sims).
- **Defenses** are on by default (the river bluff guardrail). A future option:
  turn the guardrail *off* for a weak character so its `over_bluff`/`sticky`
  becomes detectably exploitable (not yet wired — noted in the pricing doc).

## Coherence rule — leaks must match the character's described style

Pick leaks from the character's archetype so play *reads* as the personality. The
natural archetype → leak map (all leaks priced; see the pricing doc):

| Character flavor | Coherent leaks | Tier |
|---|---|---|
| aggressive / loose-cannon / drunk | `over_bluff`, `auto_cbet`, `donk_when_weak` | fish |
| loose-passive / station / "can't fold" | `sticky`, `under_bluff`, `fit_or_fold` | fish |
| tight / nit / cautious | `fit_or_fold`, `give_up_turn`, `slowplay` | fish→mid |
| trappy / patient / "lures" | `slowplay` (free flavor) | mid |
| analytical / predatory / "reads opponents" | *(none)* + `adaptive_overbet` | elite |

## Skill-tier budget

A character's handicap ≈ the sum of its leaks' priced bb/100 costs (vs the realistic
field). Most leaks price free (style), so a fish's real handicap comes from the
**−EV river leaks** (`sticky` −1.87, `over_bluff` −EV vs callers) and from being
**exploitable** when it shares a table with an attacker. Budget the gradient by how
many −EV leaks + whether attacks/defenses are present, not by stacking free flavor.

## Wired exemplars (2026-05-29) — the loop, made concrete

Three characters that demonstrate a live matchup:

- **Don Quixote** ("delusionally confident, sees giants") → `[["sticky", 0.7]]`. The
  fish who can't fold a beat hand on the river. The −EV leak.
- **Sherlock Holmes** ("analytical… reads opponents") → `"adaptive_overbet": true`. The
  elite attacker whose surgical overbet *detects and punishes* a payer like Don Quixote.
- **The Grandmother** ("deceptively sweet, lures opponents") → `[["slowplay", 0.8]]`.
  Mid-tier flavor: a free, recognizable trappy character, no skill cost.

Sherlock-vs-Quixote is the loop: attach a leak to one, the attacker to another, and the
attacker extracts. The Grandmother is pure character. (Strengths are starting values;
re-price/tune per the pricing-doc budget if a character should be more/less handicapped.)

## Open: trait-derived auto-mapper (the scalable path)

Hand-authoring covers signature characters. To cover all 62 coherently, a function
could map each personality's archetype/anchors → a default leak set + skill tier
(elite → attacks, fish → leaks), with hand-authored overrides. Deferred until the
hand-wired exemplars prove the gradient feels right in-game. This doc is its spec.

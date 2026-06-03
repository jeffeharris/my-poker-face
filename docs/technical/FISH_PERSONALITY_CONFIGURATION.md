---
purpose: How to configure a fish persona's personality and emotional behavior — anchors, emotion families, the self_belief dial, and the cheerful-loss override
type: reference
created: 2026-06-01
last_updated: 2026-06-01
---

# Fish Personality Configuration

This covers the **personality / psychology / emotional** configuration of fish
personas: the `anchors` block, how it maps to emotion, and how to tune the tone.

> **Scope split.** The fish's *decision/strategy* configuration — `rule_strategy`,
> `fish_leak`, the rule-bot decision logic and leak catalog — lives in
> [`FISH_BOT_SYSTEM.md`](FISH_BOT_SYSTEM.md). The *psychology engine* (axes,
> quadrants, zones, emotion families) lives in
> [`PSYCHOLOGY_OVERVIEW.md`](PSYCHOLOGY_OVERVIEW.md). This doc is the bridge: how a
> fish persona's anchors produce its on-table *emotional read*.

## Design intent (canon)

Fish are the casino-tourist marks: **loud, clueless, and relentlessly happy.** The
scripted Scene-0 fish "Loose Larry" (`cash_mode/career_scene.py`, circuit-progression)
is the reference voice:

> "Ooh, a king! I like kings. *blub*"
> "Aw, ya got me — great hand, buddy! Deal again, deal again."
> "I'm feelin' lucky on this one, fellas! *blub* Gonna bet big — scaaary, right?"

The character note: *"Loud, clueless, blub. Larry never figures out he's the mark."*
So a fish is **obliviously over-confident and stays cheerful through losing** — anger
and sulking are the wrong emotions for a (fun-lover) fish. They feel good because
they're having fun and have no idea they're losing, **not** because they're skilled.

## The persona config

A fish is a normal persona in `poker/personalities.json` with `archetype: "fish"`
plus the strategy fields and an `anchors` block. Example:

```json
"Vacation Greg": {
  "archetype": "fish",
  "play_style": "loose-passive tourist; calls everything, here for fun",
  "default_confidence": "cheerful",
  "default_attitude": "oblivious",
  "rule_strategy": "fish",          // → decision bot (see FISH_BOT_SYSTEM.md)
  "fish_leak": "calls_down_top_pair",
  "spot_tendencies": [["sticky", 0.65]],
  "anchors": {                      // ← THIS doc
    "baseline_aggression": 0.15,
    "baseline_looseness": 0.85,
    "ego": 0.2,
    "poise": 0.6,
    "expressiveness": 0.8,
    "risk_identity": 0.4,
    "adaptation_bias": 0.0,
    "baseline_energy": 0.7,
    "recovery_rate": 0.3,
    "self_belief": 0.8
  }
}
```

`archetype: "fish"` is the load-bearing flag: it drives casino selection, pool-funded
bankroll, relationship-skip (see [`CASH_MODE_FISH_AS_PERSONAS.md`](../plans/CASH_MODE_FISH_AS_PERSONAS.md)),
**and** the cheerful-loss emotion override below (`PlayerPsychology.is_fish`).

## Anchors — the 10 dials

Anchors are the static identity layer (`PersonalityAnchors`, never change in a
session). Full semantics in [`PSYCHOLOGY_OVERVIEW.md`](PSYCHOLOGY_OVERVIEW.md) §3.
What matters when configuring a **fish**:

| Anchor | What it does | Fish guidance |
|---|---|---|
| `baseline_aggression` | bet/raise frequency | **low** (0.15–0.45) — passive. This is the leak; don't raise it. |
| `baseline_looseness` | hand-range width | **high** (0.75–0.95) — calls too much. |
| `ego` | sensitivity to being outplayed *and* the **emotion-family gate** | **low (<0.40)** for the cheerful tourists → `fun_lover` family, anger-resistant. High ego (>0.55) makes a "deluded/brash" fish (Carl, Freddie). |
| `poise` | composure resistance to bad beats | low-ish (0.45–0.6) — a fish *should* tilt easily; it doesn't matter much because the cheerful-loss override keeps them happy anyway. |
| `expressiveness` | emotional transparency (readability) | how loud they show it. Low (0.4) = deliberately flat (Slots Linda, "zoned out"); high (0.8–0.95) = an open book. |
| `risk_identity` | variance tolerance | moderate–high (0.3–0.8). Also nudges confidence. |
| `adaptation_bias` | opponent-adjustment rate | **0.0** — a fish never learns. |
| `baseline_energy` | resting energy / animation | high (0.4–0.9) — animated, "blub". |
| `recovery_rate` | how fast axes return to baseline | **high (0.2–0.3)** — bounces back, doesn't dwell ("already forgot the last hand"). The old uniform 0.10 parked them in tilt for ~10 hands. |
| `self_belief` | **bravado / overconfidence dial** (see below) | 0.6–0.85 — lifts them off the timid floor so they rest *confident*, not meek. |

### `self_belief` — the overconfidence dial

`self_belief` (added 2026-06-01) offsets baseline confidence **independently of ego**:

```
baseline_confidence = 0.3 + aggression*0.25 + risk_identity*0.20 + ego*0.25
                          + (self_belief - 0.5) * 0.4        # clamped to ~[0.45, 0.80]
```

- **Default 0.5 = neutral** (no offset) — every legacy persona is unchanged.
- It exists because `ego` was doing double duty: it's *both* a confidence source *and*
  the anger-proneness/family gate. You couldn't make a tourist **overconfident but
  cheerful** — high ego brought anger, low ego brought timidity. `self_belief`
  decouples them: raise confidence (overconfident, sits in the upbeat `COMMANDING`
  quadrant) while keeping `ego` low (cheerful, anger-resistant).
- The clamp (≤ ~0.80) keeps even a maxed tourist *below* the overconfident penalty
  zone, so they don't auto-tilt.
- **Strategic coupling (guardrail):** confidence feeds effective aggression/looseness
  (`compute_modifiers`). Keep `self_belief` moderate — at these values the shift is
  tiny (effective aggression +0.01–0.04, looseness +0.01–0.03), so the fish stays a
  passive calling station. Validate it (below) if you push it hard.

## How anchors become emotion

`ego` + `expressiveness` pick an **emotion family**; the family + the live quadrant
(confidence × composure) + energy pick the surface emotion. Full matrix in
[`PSYCHOLOGY_OVERVIEW.md`](PSYCHOLOGY_OVERVIEW.md) §5 ("Emotion Families").

`get_emotion_family(anchors)` (`poker/psychology_model.py`), precedence:

| Family | Rule | Reads as |
|---|---|---|
| `STOIC` | `expressiveness < 0.40` | muted / poker_face |
| `FUN_LOVER` | `ego < 0.40` | **elated / happy / giddy / gleeful** (the cheerful tourist) |
| `COMPETITOR` | `ego > 0.55` | smug / confident / **angry** under pressure |
| `ANXIOUS` | otherwise (0.40–0.55) | nervous / frustrated |

So **the family is independent of the `fish` strategy archetype.** Most fish are
low-ego `fun_lover`s (cheerful). A high-ego fish (Cruise Carl "convinced he's good")
is strategically still a leaky calling station but reads as a *smug* `competitor` who
can flash anger — the deluded-mark flavor.

### The cheerful-loss override (fish-only)

Canon says a fun-lover fish stays happy even when stacked. So
`PlayerPsychology._get_true_emotion` special-cases it:

```
if is_fish and family is FUN_LOVER and quadrant is SHAKEN:
    return 'gleeful' if energy > 0.6 else 'happy'   # instead of 'sheepish'
```

- Gated on `is_fish` (`personality_config['archetype'] == 'fish'`). Ordinary
  (non-fish) fun-lovers still read `sheepish` on a real beat — they feel the oops.
- Only `fun_lover` fish get it; high-ego `competitor` fish (Carl/Trent/Freddie) are
  *not* overridden and keep their sharp reads.
- Net effect: a fun-lover fish is cheerful in **every** quadrant — `elated`/`happy`
  (COMMANDING), `giddy`/`gleeful` (OVERHEATED), `happy` (GUARDED), `gleeful`/`happy`
  (SHAKEN). Never sheepish, never angry.

## Current roster (9 fish)

`fish_leak` column is the strategy tell (see [`FISH_BOT_SYSTEM.md`](FISH_BOT_SYSTEM.md));
the rest is the personality/emotion config.

| Persona | ego | self_belief | recovery | expr | family | fish_leak | tone |
|---|---|---|---|---|---|---|---|
| Vacation Greg | 0.20 | 0.80 | 0.30 | 0.80 | fun_lover | calls_down_top_pair | sunburned vacation dad, "Wooo!" |
| Lucky Mona | 0.30 | 0.85 | 0.28 | 0.60 | fun_lover | overvalues_face_cards | has a "system" from signs |
| Slots Linda | 0.20 | 0.65 | 0.20 | 0.40 | fun_lover | sticky_then_pops | zoned out from the penny slots (flat) |
| Birthday Bobby | 0.28 | 0.70 | 0.30 | 0.85 | fun_lover | pot_committed_early | "the math doesn't apply tonight" |
| Golf Trip Brad | 0.30 | 0.72 | 0.28 | 0.65 | fun_lover | bets_strong_transparently | treats poker like blackjack |
| Bachelorette Brenda | 0.35 | 0.70 | 0.25 | 0.95 | fun_lover | chases_any_draw | tipsy, "WAIT — is it my turn?" |
| Cruise Carl | 0.85 | 0.85 | 0.22 | 0.50 | competitor | limps_every_hand | "convinced he's good" — smug-deluded |
| After Hours Trent | 0.70 | 0.75 | 0.15 | 0.70 | competitor | spite_raises_when_losing | four whiskeys deep, posturing |
| Freddie Fratboy | 0.75 | 0.65 | 0.10 | 0.95 | competitor | spews_bluffs | "folding is no balls" — boils over |

The six low-ego tourists read uniformly cheerful; the three high-ego ones keep an
edge (Freddie still ~20% angry under pressure — intentional).

## Adding / tuning a fish

1. **Cheerful tourist** → `ego` low (≤0.30), `expressiveness` high (0.7–0.9),
   `self_belief` 0.7–0.85, `recovery_rate` 0.25–0.3. Lands `fun_lover`, rests
   `COMMANDING`/elated, cheerful through losing.
2. **Deluded / brash fish** ("convinced he's good", "spring-break loud") → `ego`
   high (>0.55) → `competitor`; pair with high `self_belief` for the swagger.
   They'll flash `smug`/`angry`. Use sparingly — one or two for contrast.
3. **Flat / zoned-out fish** → `expressiveness` ~0.40, moderate `self_belief`. Reads
   mostly `poker_face` — variety *across* the table, not within every persona.
4. Set the **strategy** fields (`rule_strategy: "fish"`, `fish_leak`,
   `spot_tendencies`) per [`FISH_BOT_SYSTEM.md`](FISH_BOT_SYSTEM.md).
5. **Don't crank `baseline_aggression`/`looseness`** to chase a mood — that changes
   the −EV leak. Use `self_belief` for the emotional read instead.

## Loading & deploy

- Personas load **DB-first**: memory cache → **DB** (`personalities` table) →
  `personalities.json` → AI generation (`AIPokerPlayer._load_personality_config`).
  So **editing `personalities.json` does not take effect live until you re-seed**:

  ```bash
  python bin/seed_personalities.py --force      # pushes JSON → personalities table
  ```

  (The docker entrypoint seeds on first run, but its sync only checks persona
  *count*, not changed *values* — value edits need `--force`.)
- `self_belief` and all anchors survive the DB round-trip: `save_personality` stores
  the whole config (anchors included) in the `config_json` blob.

## Validation

Two checks, both run headless / no LLM (use a seeded DB and dummy provider keys):

1. **Emotion distribution** — play gated hands (only the actual winner/loser get
   pressure events, approximating `PressureEventDetector`), drive the real
   `resolve_hand_events` + `recover`, and tally `get_display_emotion` per persona.
   Confirms fun-lover fish read gleeful/elated/giddy with **0% angry**, competitors
   keep their edge.
2. **Strategic guardrail** — for each fish compute effective aggression/looseness at
   the new vs neutral (`self_belief=0.5`) confidence and confirm the delta is small
   (~+0.01–0.04). Proves the confidence bump didn't turn the calling station
   aggressive.

> Gotcha: `full_sim` builds controllers from the **DB**, so `seed_personalities
> --force` first or the sim runs stale traits. Constructing controllers needs
> provider keys present (dummy is fine — LLM calls are mocked), or persona load
> falls through to AI-gen → default anchors.

For the strategy-side sims (decision-distribution profile, SNG win-rate) see
[`FISH_BOT_SYSTEM.md`](FISH_BOT_SYSTEM.md) § Simulation & validation.

## Key files

| File | Purpose |
|---|---|
| `poker/personalities.json` | the 9 fish personas (anchors + strategy fields) |
| `poker/psychology_model.py` | `PersonalityAnchors` (incl. `self_belief`), `get_emotion_family`, `compute_baseline_confidence` |
| `poker/player_psychology.py` | `is_fish`, `_get_true_emotion` (family matrix + fish cheerful-loss) |
| `poker/expression_filter.py` | visibility/dampening (how much emotion leaks) |
| `bin/seed_personalities.py` | JSON → `personalities` table (`--force` to update) |

## Related docs

- [`FISH_BOT_SYSTEM.md`](FISH_BOT_SYSTEM.md) — fish *decision* bot, leak catalog, strategy config
- [`PSYCHOLOGY_OVERVIEW.md`](PSYCHOLOGY_OVERVIEW.md) — anchors, axes, quadrants, zones, emotion families
- [`CASH_MODE_FISH_AS_PERSONAS.md`](../plans/CASH_MODE_FISH_AS_PERSONAS.md) — fish bankroll/lifecycle as pool-funded personas

"""Phase 7.6 Step 5: NarrationFacts adapter.

Bridges the analytical intervention trace (dev-facing, exhaustive) and
the LLM expression layer (player-facing, terse, allowlisted). Trace
schema can evolve without breaking narration — the adapter is the
single contract surface.

Why this layer exists (plan §"LLM narration"):

- Trace `rationale` strings are written for debugging and may include
  opponent-model internals (e.g. "Opp postflop_jam_open_rate=0.32").
- Trace `confidence` values are signal-strength numbers (0..1), NOT
  emotional certainty.
- Non-fired layers create noisy "I considered X" filler in prompts.
- Privacy / leak-safety needs to be enforced at one chokepoint
  (NARRATION_ALLOWLIST) rather than distributed across every layer's
  rationale strings.

The adapter does four things:
  1. Filters traces to fired layers in NARRATION_ALLOWLIST.
  2. Maps `reason_code` to player-facing (observation, why_it_matters)
     via REASON_CODE_TO_OBSERVATION.
  3. Scores each candidate fact via `_score_fact_importance` (6-dim
     weighted) and caps to NARRATION_MAX_FACTS=3.
  4. Picks `primary_factor` as the lead — the highest-scoring fact.

Independence: this module imports from intervention_trace but NOT from
controllers / expression_generator. Tests can run without spinning up
a game.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

NARRATION_MAX_FACTS = 3


@dataclass(frozen=True)
class NarrationFact:
    """One narration-safe observation derived from a fired intervention.

    Allowlisted fields only — no opponent-model internals, no
    hidden-card knowledge, no `confidence` values that could be
    misread as emotional certainty.
    """

    observation: str  # "Opponent has been jamming postflop a lot"
    why_it_matters: str  # "Their bet range is mostly bluffs here"
    decision_taken: str  # "I'm calling instead of folding"
    action_intent: str  # 'value_bet' / 'bluff' / 'bluff_catch' /
    # 'pot_control' / 'protection' / 'steal' /
    # 'induce' / 'give_up'
    intensity_bucket: str  # 'subtle' / 'noticeable' / 'strong'
    certainty_bucket: str  # 'tentative' / 'confident' / 'sure'
    importance: float  # 0-1 ranking score for top-N selection;
    # never exposed to LLM directly
    layer: str = ''  # debug only — not sent to LLM
    rule_id: str = ''  # debug only — not sent to LLM


@dataclass(frozen=True)
class NarrationContext:
    """Decision-level context the LLM needs alongside the facts."""

    street: str  # 'preflop' / 'flop' / 'turn' / 'river'
    position_context: str  # 'in_position' / 'out_of_position' /
    # 'big_blind' / 'small_blind' / 'button'
    risk_posture: str  # 'conservative' / 'balanced' / 'aggressive'


@dataclass(frozen=True)
class NarrationFacts:
    """The narration-safe view of one decision's trace.

    `facts` is capped at NARRATION_MAX_FACTS (3). `primary_factor` is
    the top-scoring fact (the prompt's lead). `summary_intensity` is
    the max intensity_bucket across surfaced facts. `suppressed_facts_count`
    is a debug counter — NOT included in the LLM prompt.
    """

    facts: List[NarrationFact]
    primary_factor: Optional[NarrationFact]
    context: NarrationContext
    summary_intensity: str = 'subtle'
    suppressed_facts_count: int = 0


# What can show up in narration. Anything not here is dev-facing only.
# Per plan §"NarrationFacts adapter": personality + short_stack +
# math_floor are intentionally absent — they're mechanical, not
# narratable observations. (Math floor in particular is "I have to
# call by the math" — that's a thought, not a poker read.)
NARRATION_ALLOWLIST: frozenset = frozenset(
    {
        ('exploitation', 'hyper_aggressive'),
        ('exploitation', 'hyper_passive'),
        ('exploitation', 'tight_nit'),
        ('exploitation', 'high_fold_to_cbet'),
        ('exploitation', 'multiway_cbet'),
        ('strong_hand_override', 'default'),
        ('bluff_catch_override', 'default'),
        ('value_vs_station', 'default'),
        # Tilt-conditioning (PERCEPTIBILITY_CONDITIONING.md Phase 2): a tilt
        # spike is telegraphed (the both-channels decision) so it's readable,
        # not silent. One entry per Tendler tilt type.
        ('tilt_conditioning', 'tilt_bad_beat'),
        ('tilt_conditioning', 'tilt_got_sucked_out'),
        ('tilt_conditioning', 'tilt_big_loss'),
        ('tilt_conditioning', 'tilt_losing_streak'),
        ('tilt_conditioning', 'tilt_nemesis_loss'),
        ('tilt_conditioning', 'tilt_crippled'),
        ('tilt_conditioning', 'tilt_bluff_called'),
    }
)


# Maps stable reason_codes to player-facing observation templates.
# Hand-curated. The LLM never sees the dev `rationale` field.
#
# Tuple form: (observation, why_it_matters). When a reason_code isn't
# in this dict, the adapter falls back to a generic phrasing based on
# the (layer, rule_id) — see _fallback_observation.
REASON_CODE_TO_OBSERVATION: Dict[str, Tuple[str, str]] = {
    # ── Exploitation: hyper_aggressive ──
    'extreme_tier_via_all_in_frequency': (
        "Opponent's been jamming a lot",
        "Their bet range is wider than usual here",
    ),
    'extreme_tier_via_aggression_factor': (
        "Opponent's been hyper-aggressive postflop",
        "They're betting too often relative to checking",
    ),
    'medium_tier_via_all_in_frequency': (
        "Opponent's jam frequency is up",
        "Some of those jams have to be light",
    ),
    'medium_tier_via_aggression_factor': (
        "Opponent's been betting a lot postflop",
        "Their bet-to-check ratio is high",
    ),
    # ── Exploitation: hyper_passive ──
    'station_value_extract': (
        "Opponent's a call station",
        "I can value-bet thinner against them",
    ),
    # ── Exploitation: tight_nit ──
    'nit_steal_open': (
        "Opponent's been folding a lot preflop",
        "Wider open should print here",
    ),
    # ── Exploitation: cbet rules ──
    'hu_cbet_exploit': (
        "Opponent's folding too often to c-bets",
        "Auto-bet flop should print",
    ),
    'multiway_cbet_exploit': (
        "Everyone's been folding to c-bets in multiway pots",
        "Smaller sizing keeps them honest",
    ),
    # ── strong_hand_override branches ──
    'facing_all_in_call': (
        "I've got a strong hand",
        "Snap call vs the shove",
    ),
    'facing_all_in_jam': (
        "I've got a strong hand and need to commit",
        "Time to shove the rest in",
    ),
    'facing_bet_call_or_raise': (
        "I'm calling or raising for value",
        "Strong hand vs their aggression",
    ),
    'facing_bet_call_only': (
        "Calling for value",
        "Strong hand and no raise available",
    ),
    'facing_bet_raise_only': (
        "Raising for value",
        "Strong hand, no flat-call line available",
    ),
    'open_value_bet_nuts': (
        "I've got the nuts and want value",
        "Bigger sizing — they'll call worse",
    ),
    'open_value_bet_strong_made': (
        "I've got a strong made hand",
        "Bet for value",
    ),
    'open_value_bet_strong': (
        "I've got a premium preflop hand",
        "Standard open for value",
    ),
    # ── bluff_catch_override branches ──
    'medium_made_vs_extreme_facing_bet': (
        "I have showdown value against an over-aggressor",
        "My pair beats most of their bluff range",
    ),
    'weak_made_vs_extreme_facing_bet': (
        "Marginal hand, but they bluff this size a lot",
        "Pot odds work against their wide range",
    ),
    # ── Phase 8 ──
    'strong_hand_vs_station': (
        "Calling station with my strong hand",
        "Bet bigger — they'll pay off",
    ),
    'preflop_open_vs_tight_passive': (
        "Tight-passive defender behind",
        "Wider open should fold them out",
    ),
    # ── Tilt-conditioning (Phase 2) — intuition-framed, never a stat/number ──
    # The reason_code IS the rule_id (tilt_<type>); these telegraph the spike's
    # CAUSE as a feeling the avatar can voice.
    'tilt_bad_beat': (
        "Still stinging from that last one",
        "Not about to back down now",
    ),
    'tilt_got_sucked_out': (
        "Can't believe that river",
        "Coming back swinging",
    ),
    'tilt_big_loss': (
        "Trying to win it all back at once",
        "Pressing hard after that hit",
    ),
    'tilt_losing_streak': (
        "Card-dead and fed up",
        "Done waiting — forcing the issue",
    ),
    'tilt_nemesis_loss': (
        "This one's personal now",
        "Out for payback against them",
    ),
    'tilt_crippled': (
        "Short and desperate",
        "Has to make something happen",
    ),
    'tilt_bluff_called': (
        "Rattled after getting caught",
        "Reading them more carefully now",
    ),
}


# Hand-curated narrative priority per (layer, rule_id). Used as one
# dimension in _score_fact_importance. Higher = more narratable.
LAYER_RULE_NARRATIVE_WEIGHT: Dict[Tuple[str, str], float] = {
    ('bluff_catch_override', 'default'): 1.0,
    ('strong_hand_override', 'default'): 1.0,
    ('exploitation', 'hyper_aggressive'): 0.8,
    ('exploitation', 'tight_nit'): 0.6,
    ('exploitation', 'hyper_passive'): 0.5,
    ('exploitation', 'high_fold_to_cbet'): 0.8,
    ('exploitation', 'multiway_cbet'): 0.6,
    ('value_vs_station', 'default'): 0.7,
    # Tilt-conditioning: a tilt spike is one of the most player-legible reads
    # (the believability thesis) — telegraph it prominently.
    ('tilt_conditioning', 'tilt_bad_beat'): 0.9,
    ('tilt_conditioning', 'tilt_got_sucked_out'): 0.9,
    ('tilt_conditioning', 'tilt_big_loss'): 0.85,
    ('tilt_conditioning', 'tilt_losing_streak'): 0.85,
    ('tilt_conditioning', 'tilt_nemesis_loss'): 0.95,
    ('tilt_conditioning', 'tilt_crippled'): 0.8,
    ('tilt_conditioning', 'tilt_bluff_called'): 0.7,
}


# Hand-curated `action_intent` per (layer, rule_id). When the action
# intent depends on the rule's role, this maps directly. The adapter
# overrides for special cases (e.g. value_override on a strong hand
# becomes `value_bet` regardless of layer).
LAYER_RULE_ACTION_INTENT: Dict[Tuple[str, str], str] = {
    ('exploitation', 'hyper_aggressive'): 'bluff_catch',
    ('exploitation', 'hyper_passive'): 'value_bet',
    ('exploitation', 'tight_nit'): 'steal',
    ('exploitation', 'high_fold_to_cbet'): 'bluff',
    ('exploitation', 'multiway_cbet'): 'bluff',
    ('strong_hand_override', 'default'): 'value_bet',
    ('bluff_catch_override', 'default'): 'bluff_catch',
    ('value_vs_station', 'default'): 'value_bet',
    # Tilt-conditioning: the spike amplifies aggression (re-raise/jam).
    ('tilt_conditioning', 'tilt_bad_beat'): 'aggression',
    ('tilt_conditioning', 'tilt_got_sucked_out'): 'aggression',
    ('tilt_conditioning', 'tilt_big_loss'): 'aggression',
    ('tilt_conditioning', 'tilt_losing_streak'): 'aggression',
    ('tilt_conditioning', 'tilt_nemesis_loss'): 'aggression',
    ('tilt_conditioning', 'tilt_crippled'): 'aggression',
    ('tilt_conditioning', 'tilt_bluff_called'): 'bluff_catch',
}


# ── Adapter ──────────────────────────────────────────────────────────────


def _intensity_bucket(effect_size: float) -> str:
    """Map L1 distance / offset L1 to a coarse intensity bucket.

    Per Codex r2: intensity ≠ certainty. This is "how much did the
    rule shift the distribution," not "how confident the rule is."
    """
    if effect_size >= 0.5:
        return 'strong'
    if effect_size >= 0.2:
        return 'noticeable'
    return 'subtle'


def _certainty_bucket(confidence: float) -> str:
    """Map rule `confidence` to a player-facing certainty bucket.

    Per Codex r2: distinct from intensity. A rule that's certain to
    fire (high `confidence`) can produce a subtle effect, and vice
    versa.
    """
    if confidence >= 0.8:
        return 'sure'
    if confidence >= 0.5:
        return 'confident'
    return 'tentative'


def _fallback_observation(layer: str, rule_id: str) -> Tuple[str, str]:
    """Generic phrasing when a reason_code lacks a curated mapping.

    Hand-curated minimum: each allowlisted (layer, rule_id) should
    have at least a fallback so a new reason_code doesn't break
    narration entirely. Adding to REASON_CODE_TO_OBSERVATION upgrades
    the phrasing later.
    """
    fallbacks: Dict[Tuple[str, str], Tuple[str, str]] = {
        ('exploitation', 'hyper_aggressive'): (
            "Reading them as over-aggressive",
            "Adjusting my range against the pattern",
        ),
        ('exploitation', 'hyper_passive'): (
            "They've been passive",
            "Time to extract value",
        ),
        ('exploitation', 'tight_nit'): (
            "They've been playing tight",
            "Steal opportunities open up",
        ),
        ('exploitation', 'high_fold_to_cbet'): (
            "They fold to c-bets a lot",
            "Standard auto-bet line",
        ),
        ('exploitation', 'multiway_cbet'): (
            "Multiway c-bet spot",
            "Smaller sizing protects equity",
        ),
        ('strong_hand_override', 'default'): (
            "Strong hand — getting money in",
            "Maximize value here",
        ),
        ('bluff_catch_override', 'default'): (
            "Marginal hand vs over-aggression",
            "Pot odds support the call",
        ),
        ('value_vs_station', 'default'): (
            "Value spot vs a station",
            "They'll call worse",
        ),
        # Tilt-conditioning fallbacks (every tilt_<type> rule maps to a curated
        # observation above; this generic one covers any future tilt type).
        ('tilt_conditioning', 'tilt_bad_beat'): (
            "Still rattled from the last hand",
            "Playing with a chip on the shoulder",
        ),
        ('tilt_conditioning', 'tilt_got_sucked_out'): (
            "Stewing over that suck-out",
            "Pushing back harder than usual",
        ),
        ('tilt_conditioning', 'tilt_big_loss'): (
            "Chasing a big loss",
            "Forcing the action to get it back",
        ),
        ('tilt_conditioning', 'tilt_losing_streak'): (
            "Running bad and frustrated",
            "Done waiting for a hand",
        ),
        ('tilt_conditioning', 'tilt_nemesis_loss'): (
            "Has a score to settle",
            "Targeting the one who got them",
        ),
        ('tilt_conditioning', 'tilt_crippled'): (
            "Backed into a corner",
            "Desperate to spin it up",
        ),
        ('tilt_conditioning', 'tilt_bluff_called'): (
            "Caught bluffing, now wary",
            "Tightening up after the call",
        ),
    }
    return fallbacks.get((layer, rule_id), ('', ''))


def _decision_taken_phrase(primary_action_after: str, action_changed: bool) -> str:
    """One-liner of what the bot decided.

    LLM gets this verbatim to anchor the narration to the actual move.
    """
    action_phrases = {
        'fold': "I'm folding",
        'call': "I'm calling",
        'check': "I'm checking",
        'all_in': "I'm shoving",
        'jam': "I'm shoving",
    }
    if primary_action_after in action_phrases:
        return action_phrases[primary_action_after]
    if primary_action_after.startswith('raise_') or primary_action_after.startswith('bet_'):
        return "I'm raising"
    return "Going with this line"


def _layer_was_overridden(
    trace_index: int,
    all_traces: Sequence,
) -> bool:
    """True if a LATER trace in the same decision recorded
    `replaced_prior_action=True` AND its `prior_action_source` points
    at this trace.

    Used by the importance scorer to down-rank facts that were
    superseded by a downstream override.
    """
    if trace_index >= len(all_traces) - 1:
        return False
    candidate_source = f"{all_traces[trace_index].layer}.{all_traces[trace_index].rule_id}"
    for later in all_traces[trace_index + 1 :]:
        if (
            getattr(later, 'replaced_prior_action', False)
            and getattr(later, 'prior_action_source', '') == candidate_source
        ):
            return True
    return False


def _score_fact_importance(
    trace,
    decision_context: NarrationContext,
    later_layer_overrode_this: bool,
    max_layer_order: int = 5,
) -> float:
    """Rank facts so top-N selection is principled, not first-come.

    Six weighted dimensions (plan §"_score_fact_importance"):

    1. Operation severity (0.30): override/veto=1.0, clamp=0.7,
       adjust=0.5, suggest=0.2, no_op=0.0
    2. Action change (0.25): class change=1.0, sizing-only=0.5, none=0.0
    3. Certainty (0.15): sure=1.0, confident=0.7, tentative=0.3
    4. Street importance (0.10): river=1.0, turn=0.7, flop=0.5,
       preflop=0.3
    5. Layer recency (0.10): layer_order / max_layer_order
    6. Narrative priority (0.10): hand-curated per (layer, rule_id)

    If `later_layer_overrode_this`, the final score is multiplied by
    0.3 so the primary_factor aligns with the final output not a
    superseded intermediate. Overwritten facts may still appear in
    the top-3 but won't be the lead.

    Returns float in [0, 1].
    """
    op_score = {
        'override': 1.0,
        'veto': 1.0,
        'clamp': 0.7,
        'adjust': 0.5,
        'suggest': 0.2,
        'no_op': 0.0,
    }.get(getattr(trace, 'operation', 'no_op'), 0.0)

    if getattr(trace, 'action_changed', False):
        act_score = 1.0
    elif getattr(trace, 'amount_bucket_before', '') != getattr(trace, 'amount_bucket_after', ''):
        act_score = 0.5
    else:
        act_score = 0.0

    confidence = float(getattr(trace, 'confidence', 0.0) or 0.0)
    if confidence >= 0.8:
        cert_score = 1.0
    elif confidence >= 0.5:
        cert_score = 0.7
    else:
        cert_score = 0.3

    street_score = {
        'river': 1.0,
        'turn': 0.7,
        'flop': 0.5,
        'preflop': 0.3,
    }.get(getattr(decision_context, 'street', '') or '', 0.5)

    layer_order = int(getattr(trace, 'layer_order', 0) or 0)
    layer_score = layer_order / max(max_layer_order, 1)

    narr_score = LAYER_RULE_NARRATIVE_WEIGHT.get(
        (getattr(trace, 'layer', ''), getattr(trace, 'rule_id', '')),
        0.5,
    )

    score = (
        0.30 * op_score
        + 0.25 * act_score
        + 0.15 * cert_score
        + 0.10 * street_score
        + 0.10 * layer_score
        + 0.10 * narr_score
    )

    if later_layer_overrode_this:
        score *= 0.3

    return round(score, 4)


def traces_to_narration_facts(
    traces: Sequence,
    decision_context: NarrationContext,
) -> NarrationFacts:
    """Convert a per-decision trace list to a narration-safe view.

    Pipeline:
      1. Filter to `fired=True` traces in NARRATION_ALLOWLIST.
      2. Map each to a NarrationFact via REASON_CODE_TO_OBSERVATION
         (with per-(layer, rule_id) fallback for unknown codes).
      3. Score each via `_score_fact_importance`, down-ranking
         overridden facts by 0.3×.
      4. Sort by score, cap to NARRATION_MAX_FACTS.
      5. Top-1 becomes `primary_factor`.

    `suppressed_facts_count` tracks how many candidates were filtered
    or capped — debug-only, never sent to the LLM.
    """
    candidates: List[NarrationFact] = []
    suppressed = 0

    # Index traces for the override-check pass.
    traces_list = list(traces)

    for idx, trace in enumerate(traces_list):
        key = (
            getattr(trace, 'layer', ''),
            getattr(trace, 'rule_id', ''),
        )
        if not getattr(trace, 'fired', False):
            suppressed += 1
            continue
        if key not in NARRATION_ALLOWLIST:
            suppressed += 1
            continue

        observation, why = REASON_CODE_TO_OBSERVATION.get(
            getattr(trace, 'reason_code', ''),
            _fallback_observation(*key),
        )
        if not observation:
            # Allowlisted but no phrasing — drop rather than emit empty
            # text into the LLM prompt.
            suppressed += 1
            continue

        overridden = _layer_was_overridden(idx, traces_list)
        importance = _score_fact_importance(
            trace,
            decision_context,
            overridden,
        )

        action_intent = LAYER_RULE_ACTION_INTENT.get(key, 'unknown')
        intensity = _intensity_bucket(float(getattr(trace, 'effect_size', 0.0) or 0.0))
        certainty = _certainty_bucket(float(getattr(trace, 'confidence', 0.0) or 0.0))
        decision_taken = _decision_taken_phrase(
            getattr(trace, 'primary_action_after', ''),
            getattr(trace, 'action_changed', False),
        )

        candidates.append(
            NarrationFact(
                observation=observation,
                why_it_matters=why,
                decision_taken=decision_taken,
                action_intent=action_intent,
                intensity_bucket=intensity,
                certainty_bucket=certainty,
                importance=importance,
                layer=key[0],
                rule_id=key[1],
            )
        )

    # Sort by importance descending; ties broken by layer_order desc
    # (later layers preferred — they had the final say).
    candidates.sort(
        key=lambda f: (-f.importance, f.layer),
    )

    if len(candidates) > NARRATION_MAX_FACTS:
        suppressed += len(candidates) - NARRATION_MAX_FACTS
        candidates = candidates[:NARRATION_MAX_FACTS]

    primary = candidates[0] if candidates else None

    # Summary intensity = max bucket across surfaced facts.
    intensity_rank = {'subtle': 0, 'noticeable': 1, 'strong': 2}
    if candidates:
        max_intensity = max(
            candidates,
            key=lambda f: intensity_rank.get(f.intensity_bucket, 0),
        ).intensity_bucket
    else:
        max_intensity = 'subtle'

    return NarrationFacts(
        facts=candidates,
        primary_factor=primary,
        context=decision_context,
        summary_intensity=max_intensity,
        suppressed_facts_count=suppressed,
    )


# ── Prompt rendering ────────────────────────────────────────────────────


# Allowlisted input fields that can appear in the LLM prompt. Keeps
# opponent-model internals (e.g. raw stat values) out of player-facing
# narration.
NARRATION_INPUT_ALLOWLIST: frozenset = frozenset(
    {
        'street',
        'position_context',
        'risk_posture',
    }
)


def render_narration_prompt(facts: NarrationFacts) -> str:
    """Render a NarrationFacts payload into LLM-ready prompt text.

    The expression generator wraps this in personality / drama
    context. This function produces just the structured facts block.
    `suppressed_facts_count` is NEVER included in the output — it's
    debug-only metadata.
    """
    if not facts.facts:
        return ""

    lines = ["WHAT YOU NOTICED:"]
    for fact in facts.facts:
        lines.append(f"- {fact.observation}")

    lines.append("")
    lines.append("WHAT YOU DECIDED:")
    if facts.primary_factor:
        lines.append(f"- {facts.primary_factor.decision_taken}")
        lines.append(f"- Why: {facts.primary_factor.why_it_matters}")
    lines.append(f"- Intensity: {facts.summary_intensity}")
    lines.append("")
    lines.append(
        "NARRATE THIS DECISION IN CHARACTER (1-2 sentences, present "
        "tense, no specific numbers or stats — just the read)."
    )
    return '\n'.join(lines)

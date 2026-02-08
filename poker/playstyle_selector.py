"""
Playstyle Selection System.

Sits between emotional state and prompt guidance:
- Emotion defines what's possible (Gaussian affinity)
- Identity defines what's natural (primary playstyle from baselines)
- Adaptation defines what's chosen (exploit scoring scaled by adaptation_bias)

Core formula:
    effective_adaptation = adaptation_bias × composure × energy
    style_score = zone_affinity + identity_bias + (effective_adaptation × exploit_score)
"""

import logging
import math
import random
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, TYPE_CHECKING

from .zone_detection import (
    ZONE_GUARDED_CENTER,
    ZONE_POKER_FACE_CENTER,
    ZONE_COMMANDING_CENTER,
    ZONE_AGGRO_CENTER,
    ZoneContext,
    ZoneEffects,
    build_zone_guidance,
)

if TYPE_CHECKING:
    from .prompt_manager import PromptManager
    from .memory.opponent_model import OpponentModel

logger = logging.getLogger(__name__)


# === CONSTANTS ===

ZONE_CENTERS = {
    'guarded': ZONE_GUARDED_CENTER,
    'poker_face': ZONE_POKER_FACE_CENTER,
    'commanding': ZONE_COMMANDING_CENTER,
    'aggro': ZONE_AGGRO_CENTER,
}

# Gaussian sigma for affinity falloff
AFFINITY_SIGMA = 0.25

# Identity bias values
PRIMARY_STYLE_BONUS = 0.20
ADJACENT_STYLE_BONUS = 0.05

# Adjacency map (ring: guarded <-> poker_face <-> commanding <-> aggro)
STYLE_ADJACENCY = {
    'guarded': ['poker_face'],
    'poker_face': ['guarded', 'commanding'],
    'commanding': ['poker_face', 'aggro'],
    'aggro': ['commanding'],
}

# Engagement tier thresholds (raw Gaussian affinity of active style)
ENGAGEMENT_BASIC_THRESHOLD = 0.25
ENGAGEMENT_FULL_THRESHOLD = 0.55

# Min hands for opponent data to be usable
MIN_THREAT_HANDS = 3

# Election system — probabilistic style selection at intervals
# interval = ELECTION_INTERVAL_MAX - adaptation_bias * ELECTION_INTERVAL_RANGE
# Low adaptation (0.0) -> 6 hands between elections (stubborn)
# High adaptation (1.0) -> 2 hands between elections (chameleon)
ELECTION_INTERVAL_MAX = 6
ELECTION_INTERVAL_RANGE = 4

# Emotional shock triggers an emergency election
# If confidence or composure swings by more than this in one hand
EMOTIONAL_SHOCK_THRESHOLD = 0.15

# Softmax temperature for probabilistic selection
# temperature = SOFTMAX_TEMP_BASE + (1 - composure) * SOFTMAX_TEMP_RANGE
# High composure -> sharp (0.4) -> usually picks "best" style
# Low composure -> flat (1.4) -> chaotic, unpredictable choices
SOFTMAX_TEMP_BASE = 0.4
SOFTMAX_TEMP_RANGE = 1.0

# Playstyle display names
STYLE_DISPLAY_NAMES = {
    'guarded': 'GUARDED',
    'poker_face': 'POKER FACE',
    'commanding': 'COMMANDING',
    'aggro': 'AGGRO',
}

# Mindset frames (used at medium+ engagement)
MINDSET_FRAMES = {
    'commanding': "You have leverage. Extract maximum value.",
    'aggro': "Target weakness. Bet when they show fear.",
    'poker_face': "Play your range. Trust the math.",
    'guarded': "Control the pot. Let them hang themselves.",
}

# Risk stances (used at medium+ engagement)
RISK_STANCES = {
    'commanding': "Size bets for maximum pressure.",
    'aggro': "Bet into weakness. Bluff on scare cards.",
    'poker_face': "Follow equity. Call when math says call.",
    'guarded': "Keep pots small without the nuts.",
}

# Planning prompts (used at medium+ engagement, encourages multi-street thinking)
PLANNING_PROMPTS = {
    'aggro': "If you're betting as a bluff, commit to a plan — what will you do on the next street if called?",
    'commanding': "Think multi-street: if you bet for value here, plan the next street too.",
    'poker_face': "Consider: what's your plan if called? If raised?",
    'guarded': "",  # Guarded is reactive by nature
}


# === DATA CLASSES ===

@dataclass
class PlaystyleState:
    """Tracks the current playstyle selection state."""
    active_playstyle: str = 'poker_face'
    primary_playstyle: str = 'poker_face'
    style_scores: Dict[str, float] = field(default_factory=dict)
    style_probabilities: Dict[str, float] = field(default_factory=dict)
    last_switch_hand: int = 0
    hands_in_current_style: int = 0
    hands_until_election: int = 0
    last_effective_adaptation: float = 0.0
    active_affinity: float = 0.0
    engagement: str = 'basic'
    elected_this_hand: bool = False
    # For emotional shock detection
    last_confidence: float = 0.5
    last_composure: float = 0.7

    def to_dict(self) -> Dict[str, Any]:
        return {
            'active_playstyle': self.active_playstyle,
            'primary_playstyle': self.primary_playstyle,
            'style_scores': dict(self.style_scores),
            'style_probabilities': dict(self.style_probabilities),
            'last_switch_hand': self.last_switch_hand,
            'hands_in_current_style': self.hands_in_current_style,
            'hands_until_election': self.hands_until_election,
            'last_effective_adaptation': self.last_effective_adaptation,
            'active_affinity': self.active_affinity,
            'engagement': self.engagement,
            'last_confidence': self.last_confidence,
            'last_composure': self.last_composure,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PlaystyleState':
        return cls(
            active_playstyle=data.get('active_playstyle', 'poker_face'),
            primary_playstyle=data.get('primary_playstyle', 'poker_face'),
            style_scores=data.get('style_scores', {}),
            style_probabilities=data.get('style_probabilities', {}),
            last_switch_hand=data.get('last_switch_hand', 0),
            hands_in_current_style=data.get('hands_in_current_style', 0),
            hands_until_election=data.get('hands_until_election', 0),
            last_effective_adaptation=data.get('last_effective_adaptation', 0.0),
            active_affinity=data.get('active_affinity', 0.0),
            engagement=data.get('engagement', 'basic'),
            last_confidence=data.get('last_confidence', 0.5),
            last_composure=data.get('last_composure', 0.7),
        )


@dataclass(frozen=True)
class PlaystyleBriefing:
    """Complete playstyle output: guidance text + prompt suppressions."""
    guidance: str
    engagement: str
    suppress_equity_verdict: bool = False
    suppress_pot_odds: bool = False
    suppress_opponent_emotion: bool = False


# === PURE FUNCTIONS ===

def compute_playstyle_affinities(
    confidence: float,
    composure: float,
) -> Dict[str, float]:
    """
    Compute Gaussian affinity for each playstyle zone.

    Uses same zone centers but with smooth Gaussian falloff (no dead zones).
    Always positive for all styles at any emotional state.
    Normalized to sum=1.0 for scoring.
    """
    raw = {}
    for style, center in ZONE_CENTERS.items():
        distance_sq = (confidence - center[0]) ** 2 + (composure - center[1]) ** 2
        raw[style] = math.exp(-distance_sq / (2 * AFFINITY_SIGMA ** 2))

    total = sum(raw.values())
    if total == 0:
        # Shouldn't happen with Gaussian, but safety net
        return {s: 0.25 for s in ZONE_CENTERS}

    return {s: v / total for s, v in raw.items()}


def compute_raw_affinity(
    confidence: float,
    composure: float,
    style: str,
) -> float:
    """Compute raw (unnormalized) Gaussian affinity for a single style."""
    center = ZONE_CENTERS[style]
    distance_sq = (confidence - center[0]) ** 2 + (composure - center[1]) ** 2
    return math.exp(-distance_sq / (2 * AFFINITY_SIGMA ** 2))


def derive_primary_playstyle(
    baseline_confidence: float,
    baseline_composure: float,
) -> str:
    """
    Derive the primary (identity) playstyle from baseline axes.

    Called once at init. Uses Gaussian affinity to find which style
    has highest affinity at the player's baseline emotional state.
    """
    affinities = compute_playstyle_affinities(baseline_confidence, baseline_composure)
    return max(affinities, key=affinities.get)


def compute_identity_bias(primary_playstyle: str) -> Dict[str, float]:
    """
    Compute identity bias for each style based on primary playstyle.

    Primary style: +0.20 bonus
    Adjacent styles: +0.05 bonus
    Others: 0.0
    """
    biases = {}
    adjacent = STYLE_ADJACENCY.get(primary_playstyle, [])

    for style in ZONE_CENTERS:
        if style == primary_playstyle:
            biases[style] = PRIMARY_STYLE_BONUS
        elif style in adjacent:
            biases[style] = ADJACENT_STYLE_BONUS
        else:
            biases[style] = 0.0

    return biases


def _select_biggest_threat(
    opponent_models: Dict[str, 'OpponentModel'],
    nemesis: Optional[str] = None,
) -> Optional['OpponentModel']:
    """
    Select the biggest threat from opponent models.

    If nemesis is set and has enough data, use nemesis.
    Otherwise pick most aggressive opponent with enough data.
    """
    if nemesis and nemesis in opponent_models:
        model = opponent_models[nemesis]
        if model.tendencies.hands_observed >= MIN_THREAT_HANDS:
            return model

    # Pick most aggressive opponent with enough data
    best = None
    best_af = -1.0
    for name, model in opponent_models.items():
        if model.tendencies.hands_observed >= MIN_THREAT_HANDS:
            if model.tendencies.aggression_factor > best_af:
                best = model
                best_af = model.tendencies.aggression_factor

    return best


def compute_exploit_scores(
    opponent_models: Optional[Dict[str, 'OpponentModel']] = None,
    nemesis: Optional[str] = None,
) -> Dict[str, float]:
    """
    Score each style 0.0-0.30 based on biggest threat's weaknesses.

    Returns dict of style -> exploit score.
    """
    scores = {s: 0.0 for s in ZONE_CENTERS}

    if not opponent_models:
        return scores

    threat = _select_biggest_threat(opponent_models, nemesis)
    if not threat:
        return scores

    t = threat.tendencies

    # Passive opponent (AF < 0.8): commanding +0.15, aggro +0.10
    if t.aggression_factor < 0.8:
        scores['commanding'] += 0.15
        scores['aggro'] += 0.10

    # Aggressive opponent (AF > 2.0): guarded +0.15, poker_face +0.10
    if t.aggression_factor > 2.0:
        scores['guarded'] += 0.15
        scores['poker_face'] += 0.10

    # Loose (VPIP > 0.45): poker_face +0.10, commanding +0.05
    if t.vpip > 0.45:
        scores['poker_face'] += 0.10
        scores['commanding'] += 0.05

    # Tight (VPIP < 0.20): aggro +0.15, commanding +0.05
    if t.vpip < 0.20:
        scores['aggro'] += 0.15
        scores['commanding'] += 0.05

    # High fold-to-cbet (> 0.65): commanding +0.05, aggro +0.05
    if t.fold_to_cbet > 0.65:
        scores['commanding'] += 0.05
        scores['aggro'] += 0.05

    # Low fold-to-cbet (< 0.30): guarded +0.05, poker_face +0.05
    if t.fold_to_cbet < 0.30:
        scores['guarded'] += 0.05
        scores['poker_face'] += 0.05

    # Cap at 0.30
    return {s: min(v, 0.30) for s, v in scores.items()}


def build_exploit_tips(
    active_playstyle: str,
    engagement: str,
    threat_model: Optional['OpponentModel'] = None,
    focal_model: Optional['OpponentModel'] = None,
) -> str:
    """
    Build 0-2 short, actionable exploit tips from opponent data.

    Tips come from two sources:
    - The biggest threat at the table (strategic awareness)
    - The focal opponent in the current hand (tactical advice)

    Only at medium+ engagement, only when opponent has enough observed hands.
    Returns empty string if no tips apply.
    """
    if engagement == 'basic':
        return ""

    tips = []

    for model in [threat_model, focal_model]:
        if model is None:
            continue
        if model.tendencies.hands_observed < MIN_THREAT_HANDS:
            continue
        # Skip duplicate (same opponent as threat and focal)
        if tips and model is threat_model:
            continue
        if len(tips) >= 2:
            break

        t = model.tendencies
        name = model.opponent

        if active_playstyle in ('aggro', 'commanding'):
            if t.fold_to_cbet > 0.60:
                tips.append(f"{name} folds to c-bets {t.fold_to_cbet:.0%} — c-bet as a bluff, they'll fold.")
                continue
            if t.aggression_factor < 0.8:
                tips.append(f"{name} is passive — bet thin for value, they won't raise without the nuts.")
                continue

        if active_playstyle == 'aggro' and t.vpip < 0.20:
            tips.append(f"{name} is tight — steal their blinds, they fold too much.")
            continue

        if active_playstyle == 'commanding' and t.vpip > 0.45:
            tips.append(f"{name} plays too many hands — value bet wider, they'll call with worse.")
            continue

        if active_playstyle in ('guarded', 'poker_face'):
            if t.aggression_factor > 2.0:
                tips.append(f"{name} is hyper-aggressive — let them bluff into your strong hands.")
                continue
            if t.fold_to_cbet < 0.30:
                tips.append(f"{name} never folds to c-bets — skip the bluff, value bet relentlessly.")
                continue

        if t.bluff_frequency > 0.50:
            tips.append(f"{name} bluffs often — call down lighter.")

    return "\n".join(tips)


def _determine_engagement(active_affinity: float) -> str:
    """Determine engagement tier from raw Gaussian affinity of active style."""
    if active_affinity >= ENGAGEMENT_FULL_THRESHOLD:
        return 'full'
    elif active_affinity >= ENGAGEMENT_BASIC_THRESHOLD:
        return 'medium'
    else:
        return 'basic'


def compute_election_interval(adaptation_bias: float) -> int:
    """
    Compute hands between elections from adaptation_bias.

    Low adaptation (0.0) -> 6 hands (stubborn, sticks with a style)
    High adaptation (1.0) -> 2 hands (chameleon, re-evaluates often)
    """
    return round(ELECTION_INTERVAL_MAX - adaptation_bias * ELECTION_INTERVAL_RANGE)


def _detect_emotional_shock(
    current_conf: float,
    current_comp: float,
    prev_conf: float,
    prev_comp: float,
) -> bool:
    """Detect if an emotional shift is significant enough to trigger an emergency election."""
    return (
        abs(current_conf - prev_conf) > EMOTIONAL_SHOCK_THRESHOLD
        or abs(current_comp - prev_comp) > EMOTIONAL_SHOCK_THRESHOLD
    )


def _softmax(scores: Dict[str, float], temperature: float) -> Dict[str, float]:
    """
    Convert scores to probabilities via softmax with temperature.

    Low temperature -> sharp (winner-take-all)
    High temperature -> flat (more uniform)
    """
    # Shift scores for numerical stability
    max_score = max(scores.values())
    exp_scores = {
        s: math.exp((v - max_score) / max(temperature, 0.01))
        for s, v in scores.items()
    }
    total = sum(exp_scores.values())
    return {s: v / total for s, v in exp_scores.items()}


def _weighted_choice(probabilities: Dict[str, float], rng: random.Random) -> str:
    """Select a style from probability weights."""
    styles = list(probabilities.keys())
    weights = [probabilities[s] for s in styles]
    return rng.choices(styles, weights=weights, k=1)[0]


def select_playstyle(
    current_state: PlaystyleState,
    confidence: float,
    composure: float,
    energy: float,
    adaptation_bias: float,
    identity_biases: Dict[str, float],
    opponent_models: Optional[Dict[str, 'OpponentModel']] = None,
    nemesis: Optional[str] = None,
    hand_number: int = 0,
    rng: Optional[random.Random] = None,
) -> PlaystyleState:
    """
    Core playstyle selection with election model.

    Styles are chosen probabilistically at election points:
    - Scheduled elections every N hands (N scales with adaptation_bias)
    - Emergency elections when emotional shock is detected
    - Between elections, the active style is locked in

    Composure controls the softmax temperature:
    - High composure -> sharp distribution -> usually the "best" style
    - Low composure -> flat distribution -> chaotic, unpredictable

    Returns a new PlaystyleState (functional, no mutation).
    """
    if rng is None:
        rng = random.Random()

    # 1. Always compute scores (for tracking/display even between elections)
    affinities = compute_playstyle_affinities(confidence, composure)
    exploit_scores = compute_exploit_scores(opponent_models, nemesis)
    effective_adaptation = adaptation_bias * composure * energy

    raw_scores = {}
    for style in ZONE_CENTERS:
        raw_scores[style] = (
            affinities[style]
            + identity_biases.get(style, 0.0)
            + effective_adaptation * exploit_scores.get(style, 0.0)
        )

    # 2. Compute probabilities (always, for display)
    temperature = SOFTMAX_TEMP_BASE + (1.0 - composure) * SOFTMAX_TEMP_RANGE
    probabilities = _softmax(raw_scores, temperature)

    # 3. Check if election is due
    shock = _detect_emotional_shock(
        confidence, composure,
        current_state.last_confidence, current_state.last_composure,
    )
    election_due = current_state.hands_until_election <= 0 or shock

    if not election_due:
        # Stay locked in, decrement counter
        current_style = current_state.active_playstyle
        active_affinity = compute_raw_affinity(confidence, composure, current_style)
        engagement = _determine_engagement(active_affinity)
        return PlaystyleState(
            active_playstyle=current_style,
            primary_playstyle=current_state.primary_playstyle,
            style_scores=raw_scores,
            style_probabilities=probabilities,
            last_switch_hand=current_state.last_switch_hand,
            hands_in_current_style=current_state.hands_in_current_style + 1,
            hands_until_election=current_state.hands_until_election - 1,
            last_effective_adaptation=effective_adaptation,
            active_affinity=active_affinity,
            engagement=engagement,
            elected_this_hand=False,
            last_confidence=confidence,
            last_composure=composure,
        )

    # 4. ELECTION: probabilistic selection
    chosen = _weighted_choice(probabilities, rng)
    interval = compute_election_interval(adaptation_bias)
    active_affinity = compute_raw_affinity(confidence, composure, chosen)
    engagement = _determine_engagement(active_affinity)
    switched = chosen != current_state.active_playstyle

    return PlaystyleState(
        active_playstyle=chosen,
        primary_playstyle=current_state.primary_playstyle,
        style_scores=raw_scores,
        style_probabilities=probabilities,
        last_switch_hand=hand_number if switched else current_state.last_switch_hand,
        hands_in_current_style=0 if switched else current_state.hands_in_current_style + 1,
        hands_until_election=interval,
        last_effective_adaptation=effective_adaptation,
        active_affinity=active_affinity,
        engagement=engagement,
        elected_this_hand=True,
        last_confidence=confidence,
        last_composure=composure,
    )


# === PLAYSTYLE BRIEFING ===

def _build_stat_lines(
    active_playstyle: str,
    player_stack: int = 0,
    avg_stack: float = 0,
    pot_total: int = 0,
    big_blind: int = 100,
    threat_name: Optional[str] = None,
    threat_summary: Optional[str] = None,
) -> str:
    """Build curated stat lines for the active playstyle."""
    lines = []

    if active_playstyle == 'commanding':
        if avg_stack > 0 and player_stack > 0:
            ratio = player_stack / avg_stack
            lines.append(f"Stack leverage: {ratio:.1f}x table average.")
        if pot_total > 0 and big_blind > 0:
            spr = player_stack / max(pot_total, 1)
            if spr > 0:
                lines.append(f"SPR is {spr:.1f} — {'room for multi-street value' if spr > 4 else 'commit-or-fold territory'}.")

    elif active_playstyle == 'aggro':
        if threat_name:
            line = f"Target: {threat_name}"
            if threat_summary:
                line += f" — {threat_summary}"
            lines.append(line + ".")

    elif active_playstyle == 'poker_face':
        if pot_total > 0 and big_blind > 0:
            pot_bb = pot_total / big_blind
            lines.append(f"Pot: {pot_bb:.1f} BB.")

    elif active_playstyle == 'guarded':
        if player_stack > 0 and pot_total > 0:
            pot_pct = pot_total / player_stack * 100
            lines.append(f"Pot is {pot_pct:.0f}% of your stack.")

    return " ".join(lines)


def build_playstyle_briefing(
    active_playstyle: str,
    zone_effects: ZoneEffects,
    zone_context: ZoneContext,
    prompt_manager: 'PromptManager',
    active_affinity: float = 0.0,
    engagement: str = 'basic',
    player_stack: int = 0,
    avg_stack: float = 0,
    pot_total: int = 0,
    big_blind: int = 100,
    threat_name: Optional[str] = None,
    threat_summary: Optional[str] = None,
    threat_model: Optional['OpponentModel'] = None,
    focal_model: Optional['OpponentModel'] = None,
) -> PlaystyleBriefing:
    """
    Build a PlaystyleBriefing with zone guidance, curated stats, framing, and suppressions.

    Engagement tiers:
    - basic (< 0.25 affinity): Zone strategy template only
    - medium (0.25-0.55): + Mindset framing + risk stance
    - full (> 0.55): + Curated stats + suppressions
    """
    # 1. Zone strategy template (existing behavior)
    zone_guidance = build_zone_guidance(
        zone_effects.to_dict(),
        zone_context,
        prompt_manager,
    )

    display_name = STYLE_DISPLAY_NAMES.get(active_playstyle, active_playstyle.upper())

    # Basic engagement: just the zone template
    if engagement == 'basic':
        guidance = zone_guidance if zone_guidance else ""
        return PlaystyleBriefing(
            guidance=guidance,
            engagement='basic',
        )

    # Medium+ engagement: add framing
    parts = []

    # Header
    engagement_label = 'Dominant' if engagement == 'full' else 'Active'
    parts.append(f"[{display_name} MODE | {engagement_label}]")

    # Full engagement: curated stats
    if engagement == 'full':
        stat_lines = _build_stat_lines(
            active_playstyle,
            player_stack=player_stack,
            avg_stack=avg_stack,
            pot_total=pot_total,
            big_blind=big_blind,
            threat_name=threat_name,
            threat_summary=threat_summary,
        )
        if stat_lines:
            parts.append(stat_lines)

    # Medium+ engagement: exploit tips from opponent data
    exploit_tips = build_exploit_tips(
        active_playstyle, engagement, threat_model, focal_model,
    )
    if exploit_tips:
        parts.append(exploit_tips)

    # Zone strategy template
    if zone_guidance:
        parts.append("---")
        parts.append(zone_guidance)
        parts.append("---")

    # Mindset frame + risk stance
    mindset = MINDSET_FRAMES.get(active_playstyle, '')
    risk = RISK_STANCES.get(active_playstyle, '')
    if mindset or risk:
        focus_parts = []
        if mindset:
            focus_parts.append(mindset)
        if risk:
            focus_parts.append(risk)
        parts.append("Focus: " + " ".join(focus_parts))

    # Planning prompt (medium+ engagement, encourages multi-street thinking)
    planning = PLANNING_PROMPTS.get(active_playstyle, '')
    if planning:
        parts.append(planning)

    guidance = "\n".join(parts)

    # Suppressions only at full engagement
    suppress_equity_verdict = False
    suppress_pot_odds = False
    suppress_opponent_emotion = False

    if engagement == 'full':
        if active_playstyle == 'aggro':
            suppress_equity_verdict = True
            suppress_pot_odds = True
        elif active_playstyle == 'poker_face':
            suppress_opponent_emotion = True
        elif active_playstyle == 'guarded':
            suppress_pot_odds = True
        # commanding: no suppressions (full info)

    return PlaystyleBriefing(
        guidance=guidance,
        engagement=engagement,
        suppress_equity_verdict=suppress_equity_verdict,
        suppress_pot_odds=suppress_pot_odds,
        suppress_opponent_emotion=suppress_opponent_emotion,
    )

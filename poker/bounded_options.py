"""
Bounded Options Generator for Hybrid AI Decisions.

Generates 2-4 sensible poker options based on game state, blocking catastrophic
decisions (folding monsters, calling when drawing dead) while preserving
personality expression through option selection.

The key insight: LLMs are bad at poker math but good at personality expression.
Let the rule engine handle the math, let the LLM handle the character.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import logging
import random

from .hand_tiers import PREMIUM_HANDS, TOP_10_HANDS, TOP_20_HANDS, TOP_35_HANDS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BoundedOption:
    """A single sensible poker option."""
    action: str                    # fold, check, call, raise, all_in
    raise_to: int                  # 0 if not raising
    rationale: str                 # Brief explanation for LLM
    ev_estimate: str               # "+EV", "neutral", "-EV"
    style_tag: str                 # "conservative", "aggressive", "trappy", "standard"

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            'action': self.action,
            'raise_to': self.raise_to,
            'rationale': self.rationale,
            'ev_estimate': self.ev_estimate,
            'style_tag': self.style_tag,
        }


@dataclass(frozen=True)
class OptionProfile:
    """Parameter bundle controlling option generation thresholds per style.

    Different profiles produce different option menus for the same hand,
    encoding strategy into the options themselves so the LLM just picks.
    """
    # Fold threshold: block fold when equity > N * required_equity
    fold_equity_multiplier: float = 2.0

    # Call EV bands: equity/required ratio thresholds
    call_plus_ev: float = 1.7      # ratio for +EV label
    call_marginal: float = 0.85    # ratio for marginal label

    # Raise EV thresholds (absolute equity)
    raise_plus_ev: float = 0.60    # equity for +EV raise
    raise_neutral: float = 0.45    # equity for neutral raise

    # Sizing multipliers on pot
    sizing_small: float = 0.33
    sizing_medium: float = 0.67
    sizing_large: float = 1.0

    # Value bet detection: equity threshold for "consider betting" on check
    value_bet_threshold: float = 0.65

    # Bluff frequency: 0-1, probability of including a -EV bluff raise
    bluff_frequency: float = 0.0

    # Post-flop overrides (None = use preflop values)
    postflop_raise_plus_ev: Optional[float] = None
    postflop_raise_neutral: Optional[float] = None
    postflop_value_bet_threshold: Optional[float] = None

    # Post-flop raise option limit (None = no limit, show all generated)
    postflop_max_raise_options: Optional[int] = None

    # Check penalty: equity threshold above which check is labeled 'marginal' for aggressive profiles
    # (None = no penalty, check stays 'neutral' as normal)
    check_penalty_threshold: Optional[float] = None

    # Check promotion style: 'default', 'always', 'conditional', 'suppress_if_raises'
    # - 'default': current behavior (promote best option to +EV)
    # - 'always': always promote check with pot-control rationale
    # - 'conditional': promote check only if no raise has neutral/+EV
    # - 'suppress_if_raises': never promote check when raise options exist
    check_promotion: str = 'default'

    def to_dict(self) -> Dict:
        """Serialize for prompt capture tracking."""
        d = {
            'fold_equity_multiplier': self.fold_equity_multiplier,
            'call_plus_ev': self.call_plus_ev,
            'call_marginal': self.call_marginal,
            'raise_plus_ev': self.raise_plus_ev,
            'raise_neutral': self.raise_neutral,
            'sizing_small': self.sizing_small,
            'sizing_medium': self.sizing_medium,
            'sizing_large': self.sizing_large,
            'value_bet_threshold': self.value_bet_threshold,
            'bluff_frequency': self.bluff_frequency,
        }
        if self.postflop_raise_plus_ev is not None:
            d['postflop_raise_plus_ev'] = self.postflop_raise_plus_ev
        if self.postflop_raise_neutral is not None:
            d['postflop_raise_neutral'] = self.postflop_raise_neutral
        if self.postflop_value_bet_threshold is not None:
            d['postflop_value_bet_threshold'] = self.postflop_value_bet_threshold
        if self.postflop_max_raise_options is not None:
            d['postflop_max_raise_options'] = self.postflop_max_raise_options
        if self.check_penalty_threshold is not None:
            d['check_penalty_threshold'] = self.check_penalty_threshold
        if self.check_promotion != 'default':
            d['check_promotion'] = self.check_promotion
        return d


# Style presets: Rock, TAG, Calling Station, LAG, and current default
STYLE_PROFILES = {
    'tight_passive': OptionProfile(
        fold_equity_multiplier=2.5,    # harder to block fold
        call_plus_ev=2.0,             # need more edge to call
        call_marginal=1.0,            # marginal zone narrower
        raise_plus_ev=0.65,           # need stronger hand to raise
        raise_neutral=0.50,
        sizing_small=0.25,            # smaller bets
        sizing_medium=0.50,
        sizing_large=0.75,
        value_bet_threshold=0.70,     # higher bar for value bets
        postflop_raise_plus_ev=0.75,  # needs very strong hand to raise post-flop
        postflop_raise_neutral=0.60,
        postflop_value_bet_threshold=0.80,
        postflop_max_raise_options=1, # passive — show minimal raise options
        check_promotion='always',     # always promote check with pot-control text
    ),
    'tight_aggressive': OptionProfile(
        fold_equity_multiplier=2.5,    # still hard to block fold
        call_plus_ev=2.0,             # prefer raising over calling
        call_marginal=1.0,
        raise_plus_ev=0.55,           # raises with less equity (but tight preflop)
        raise_neutral=0.40,
        sizing_small=0.33,
        sizing_medium=0.75,
        sizing_large=1.2,             # bigger sizing pressure
        value_bet_threshold=0.60,
        postflop_raise_plus_ev=0.42,  # aggressive post-flop — nearly as loose as LAG
        postflop_raise_neutral=0.25,  # very willing to bet thin value post-flop
        postflop_value_bet_threshold=0.55,
        postflop_max_raise_options=2, # aggressive but curated
        check_penalty_threshold=0.40,  # checking with >40% equity is leaving value
        check_promotion='conditional', # promote check only when no raise is neutral/+EV
    ),
    'loose_passive': OptionProfile(
        fold_equity_multiplier=1.5,    # easier to block fold (plays more)
        call_plus_ev=1.4,             # calls more easily
        call_marginal=0.70,           # wider marginal zone
        raise_plus_ev=0.65,           # doesn't raise much
        raise_neutral=0.50,
        sizing_small=0.25,
        sizing_medium=0.50,
        sizing_large=0.75,
        value_bet_threshold=0.70,
        postflop_raise_plus_ev=0.70,  # passive post-flop — needs strong hand
        postflop_raise_neutral=0.55,
        postflop_value_bet_threshold=0.75,
        postflop_max_raise_options=1, # passive — minimal raise options
        check_promotion='always',     # always promote check with pot-control text
    ),
    'loose_aggressive': OptionProfile(
        fold_equity_multiplier=1.8,    # plays more hands than default (2.0) but not every hand
        call_plus_ev=1.5,
        call_marginal=0.75,
        raise_plus_ev=0.55,           # slightly lower bar than default — raises for value more often
        raise_neutral=0.42,           # honest EV labels — marginal raises show as -EV, not neutral
        sizing_small=0.33,
        sizing_medium=0.75,
        sizing_large=1.5,             # overbets for pressure
        value_bet_threshold=0.55,     # bets thinner for value
        bluff_frequency=0.15,         # includes bluff raises
        postflop_raise_plus_ev=0.45,  # very aggressive post-flop — raises with thin value
        postflop_raise_neutral=0.30,
        postflop_value_bet_threshold=0.50,
        postflop_max_raise_options=3, # full menu of raise options
        check_penalty_threshold=0.35,  # checking with >35% equity is suboptimal for LAG
        check_promotion='suppress_if_raises', # never promote check over raises
    ),
    'default': OptionProfile(),       # current behavior unchanged
}

# Style hint text for lean prompt injection
STYLE_HINTS = {
    'tight_passive': "Play tight — fold marginal hands, only continue with strong holdings.",
    'tight_aggressive': "Play aggressively with strong hands — bet for value, pressure opponents.",
    'loose_passive': "See more flops — call liberally, but don't overcommit without a hand.",
    'loose_aggressive': "",
    'default': "",
}


# ============================================================
# Math helpers
# ============================================================

def calculate_required_equity(pot: float, cost_to_call: float) -> float:
    """Equity needed to break even on a call.

    Args:
        pot: Total pot size before this call
        cost_to_call: Amount needed to call

    Returns:
        Required equity as a decimal (0-1). 0 if no cost to call.
    """
    if cost_to_call <= 0:
        return 0.0
    return cost_to_call / (pot + cost_to_call)


def _should_block_fold(context: Dict, profile: OptionProfile = None) -> bool:
    """Block fold when it's mathematically insane.

    Blocking rules (high confidence):
    - Block when equity > Nx required_equity (N from profile, default 2x)
    - Block when holding top 5% hand strength (90%+ equity)
    - Block when pot-committed (already bet > remaining stack)

    Args:
        context: Decision context with equity, required_equity, stack info
        profile: OptionProfile controlling fold_equity_multiplier threshold

    Returns:
        True if folding should be blocked
    """
    if profile is None:
        profile = OptionProfile()

    equity = context.get('equity', 0.5)
    cost_to_call = context.get('cost_to_call', 0)
    pot_total = context.get('pot_total', 0)

    # No cost to call - check, not fold
    if cost_to_call <= 0:
        return True

    # Calculate required equity
    required = calculate_required_equity(pot_total, cost_to_call)

    # Block if equity >> required (the quad-folding problem)
    multiplier = profile.fold_equity_multiplier
    if required > 0 and equity > required * multiplier:
        logger.debug(f"[BOUNDED] Blocking fold: equity {equity:.2f} > {multiplier}x required {required:.2f}")
        return True

    # Block if we have a monster (top 5% hand)
    if equity >= 0.90:
        logger.debug(f"[BOUNDED] Blocking fold: monster hand with {equity:.2f} equity")
        return True

    # Block if pot-committed
    already_bet = context.get('already_bet', 0)
    remaining_stack = context.get('player_stack', 0)
    if already_bet > remaining_stack and equity >= 0.25:
        logger.debug(f"[BOUNDED] Blocking fold: pot-committed (bet {already_bet} > stack {remaining_stack})")
        return True

    return False


def _should_block_call(context: Dict) -> bool:
    """Block call when drawing dead.

    Blocking rules:
    - Block when equity < 5% (nearly drawing dead)
    - Block when equity < required_equity * 0.5 (very -EV)

    Args:
        context: Decision context with equity, required_equity

    Returns:
        True if calling should be blocked
    """
    equity = context.get('equity', 0.5)
    cost_to_call = context.get('cost_to_call', 0)

    # No cost to call - can always check
    if cost_to_call <= 0:
        return False

    # Nearly drawing dead
    if equity < 0.05:
        logger.debug(f"[BOUNDED] Blocking call: nearly drawing dead with {equity:.2f} equity")
        return True

    return False


# ============================================================
# Stack Depth Classification
# ============================================================

def _get_stack_depth(stack_bb: float) -> str:
    """Classify effective stack depth for sizing decisions.

    Returns: 'deep' (>30BB), 'medium' (10-30BB), or 'short' (<10BB)
    """
    if stack_bb < 10:
        return 'short'
    if stack_bb <= 30:
        return 'medium'
    return 'deep'


def _get_raise_options(
    context: Dict,
    profile: OptionProfile = None,
    eff_value_bet_threshold: float = None,
) -> List[Tuple[int, str, str]]:
    """Generate 2-3 sensible raise sizes.

    Args:
        context: Decision context with pot, min_raise, max_raise, stack, equity
        profile: OptionProfile controlling sizing multipliers and value bet threshold
        eff_value_bet_threshold: Effective value bet threshold (allows postflop override)

    Returns:
        List of (raise_to_amount, rationale, style_tag) tuples
    """
    if profile is None:
        profile = OptionProfile()

    pot = context.get('pot_total', 0)
    min_raise = context.get('min_raise', 0)
    max_raise = context.get('max_raise', 0)
    stack_bb = context.get('stack_bb', 100)
    big_blind = context.get('big_blind', 100)
    equity = context.get('equity', 0.5)

    if min_raise <= 0 or max_raise <= 0:
        return []

    options = []

    # Value betting emphasis when equity exceeds threshold
    vbt = eff_value_bet_threshold if eff_value_bet_threshold is not None else profile.value_bet_threshold
    value_betting = equity >= vbt
    equity_pct = int(equity * 100)

    # Small (profile.sizing_small * pot or min raise)
    small = max(min_raise, int(pot * profile.sizing_small))
    if small <= max_raise:
        if value_betting:
            rationale = f"Value bet ({equity_pct}% equity)"
        else:
            rationale = "Small probe/value bet"
        options.append((small, rationale, "conservative"))

    # Medium (profile.sizing_medium * pot)
    medium = int(pot * profile.sizing_medium)
    if medium > small and medium < max_raise and medium >= min_raise:
        if value_betting:
            rationale = f"Bet for value ({equity_pct}% equity)"
        else:
            rationale = "Standard value bet"
        options.append((medium, rationale, "standard"))

    # Large (profile.sizing_large * pot)
    large = int(pot * profile.sizing_large)
    if large > medium and large <= max_raise and large >= min_raise:
        if value_betting:
            rationale = f"Strong value bet ({equity_pct}% equity)"
        else:
            rationale = "Pressure/protection bet"
        options.append((large, rationale, "aggressive"))

    # All-in for short stacks (< 20 BB)
    if stack_bb < 20 and max_raise not in [o[0] for o in options]:
        options.append((max_raise, "All-in (short stack)", "aggressive"))

    return options


# ============================================================
# Smart Truncation
# ============================================================

_EV_RANK = {'+EV': 3, 'neutral': 2, 'marginal': 1, '-EV': 0}


def _truncate_options(options: List[BoundedOption], max_options: int = 4) -> List[BoundedOption]:
    """Truncate to max_options while preserving priority options.

    Priority-based truncation:
    1. One of each non-raise action type (fold, check, call, all_in) — always kept
    2. +EV raises kept before neutral/negative
    3. When dropping raises, keep most spread-out sizes (smallest + largest)
    """
    if len(options) <= max_options:
        return options

    non_raises = [o for o in options if o.action != 'raise']
    raises = [o for o in options if o.action == 'raise']

    # Start with non-raises (each is unique per action type)
    result = list(non_raises)
    budget = max_options - len(result)

    if budget > 0 and raises:
        # Sort: highest EV first, then by amount for spread
        raises_sorted = sorted(raises, key=lambda o: (
            -_EV_RANK.get(o.ev_estimate, 0),
            o.raise_to,
        ))
        if budget >= 2 and len(raises_sorted) >= 2:
            # Keep best-EV raise + largest for max sizing spread
            result.append(raises_sorted[0])
            if raises_sorted[-1] is not raises_sorted[0]:
                result.append(raises_sorted[-1])
                budget -= 2
            else:
                budget -= 1
            for r in raises_sorted[1:-1]:
                if budget <= 0:
                    break
                result.append(r)
                budget -= 1
        else:
            result.extend(raises_sorted[:budget])
    elif budget <= 0:
        # Non-raises alone exceeded budget — drop lowest EV
        result.sort(key=lambda o: -_EV_RANK.get(o.ev_estimate, 0))
        result = result[:max_options]

    return result


# ============================================================
# Main Option Generator
# ============================================================

def generate_bounded_options(
    context: Dict,
    profile: OptionProfile = None,
    phase: str = None,
    in_range: bool = True,
    range_pct: float = None,
    position_display: str = None,
    emotional_state: Optional[str] = None,
    emotional_severity: Optional[str] = None,
    rng: 'random.Random' = None,
) -> List[BoundedOption]:
    """Generate 2-4 sensible options based on game state.

    The rule engine generates mathematically reasonable options, blocking
    catastrophic decisions while leaving room for personality expression.

    Args:
        context: Decision context dictionary with:
            - equity: float (0-1) hand equity
            - pot_total: int pot size
            - cost_to_call: int cost to call
            - player_stack: int remaining stack
            - stack_bb: float stack in big blinds
            - min_raise: int minimum raise to
            - max_raise: int maximum raise to
            - valid_actions: List[str] valid action types
            - phase: str current betting phase
            - position: str player position
            - canonical_hand: str canonical hand notation
        profile: OptionProfile controlling thresholds and sizing. Defaults to OptionProfile().
        phase: Betting phase ('PRE_FLOP', 'FLOP', etc). If None, reads from context.
        in_range: Whether the hand is in the player's preflop range (for range biasing).
        range_pct: Player's range percentage (for rationale text).
        position_display: Human-readable position name (for rationale text).
        emotional_state: Emotional state name ('tilted', 'overconfident', 'shaken', 'dissociated').
            WARNING: Do not pass emotional_state/emotional_severity here if you also call
            apply_emotional_window_shift() on the result — that would double-apply the shift.
            The controller uses apply_emotional_window_shift() externally, so it should NOT
            pass these params.
        emotional_severity: Severity level ('none', 'mild', 'moderate', 'extreme')
        rng: Random instance for deterministic testing of probabilistic rolls

    Returns:
        List of 2-4 BoundedOption instances, always including at least one +EV option
    """
    if profile is None:
        profile = OptionProfile()

    if phase is None:
        phase = context.get('phase', 'PRE_FLOP')

    # Resolve effective thresholds: use postflop overrides when available
    is_postflop = phase != 'PRE_FLOP'
    eff_raise_plus_ev = (
        profile.postflop_raise_plus_ev if is_postflop and profile.postflop_raise_plus_ev is not None
        else profile.raise_plus_ev
    )
    eff_raise_neutral = (
        profile.postflop_raise_neutral if is_postflop and profile.postflop_raise_neutral is not None
        else profile.raise_neutral
    )
    eff_value_bet_threshold = (
        profile.postflop_value_bet_threshold if is_postflop and profile.postflop_value_bet_threshold is not None
        else profile.value_bet_threshold
    )

    options = []
    valid_actions = context.get('valid_actions', [])
    equity = context.get('equity', 0.5)
    cost_to_call = context.get('cost_to_call', 0)
    pot_total = context.get('pot_total', 0)
    stack_bb = context.get('stack_bb', 100)

    # Range biasing: out-of-range preflop hands get biased EV labels
    apply_range_bias = (not in_range and phase == 'PRE_FLOP' and cost_to_call > 0)

    block_fold = _should_block_fold(context, profile)
    block_call = _should_block_call(context)

    # Calculate required equity for pot odds
    required_equity = calculate_required_equity(pot_total, cost_to_call)

    # Determine EV estimate for calling (three-zone approach using profile thresholds)
    # - +EV: Clearly profitable (equity >= required * call_plus_ev)
    # - marginal: Close call, let personality/guidance decide
    # - -EV: Below required odds
    if cost_to_call <= 0:
        call_ev = "neutral"
    elif equity >= required_equity * profile.call_plus_ev:
        call_ev = "+EV"  # Clearly profitable
    elif equity >= required_equity * profile.call_marginal:
        call_ev = "marginal"  # Close - defer to hand guidance
    else:
        call_ev = "-EV"

    # === CHECK option ===
    if 'check' in valid_actions:
        # Adjust EV and rationale based on equity (value hand detection)
        cpt = profile.check_penalty_threshold
        if equity >= eff_value_bet_threshold and cost_to_call == 0:
            # Strong hand - checking may miss value
            check_ev = "marginal"
            check_rationale = "Check (strong hand - consider betting for value)"
            check_style = "trappy"  # Only appropriate when slowplaying
        elif cpt is not None and equity >= cpt and cost_to_call == 0:
            # Aggressive profile: checking with betting equity is suboptimal
            check_ev = "marginal"
            check_rationale = "Check (you have betting equity — consider raising)"
            check_style = "conservative"
        elif equity >= 0.50 and cost_to_call == 0:
            check_ev = "neutral"
            check_rationale = "Check and see a free card"
            check_style = "conservative"
        else:
            check_ev = "neutral"
            check_rationale = "Check and see a free card" if cost_to_call == 0 else "Check"
            check_style = "conservative"

        options.append(BoundedOption(
            action='check',
            raise_to=0,
            rationale=check_rationale,
            ev_estimate=check_ev,
            style_tag=check_style
        ))

    # === FOLD option (if not blocked) ===
    if 'fold' in valid_actions and not block_fold:
        # Fold EV from the player's perspective:
        # - equity well below required → folding saves money = +EV
        # - equity near required → borderline = neutral
        # - equity above required → folding gives up value = -EV
        if apply_range_bias:
            fold_ev = "+EV"
            range_pct_str = f"~{int(range_pct * 100)}%" if range_pct is not None else ""
            pos_str = f" from {position_display}" if position_display else ""
            fold_rationale = f"Fold (outside your {range_pct_str} range{pos_str})"
        elif required_equity > 0 and equity < required_equity * 0.85:
            fold_ev = "+EV"
            fold_rationale = f"Fold (need {int(required_equity*100)}% equity, have ~{int(equity*100)}%)"
        elif equity < required_equity:
            fold_ev = "neutral"
            fold_rationale = f"Fold (need {int(required_equity*100)}% equity, have ~{int(equity*100)}%)"
        else:
            fold_ev = "-EV"
            fold_rationale = f"Fold (need {int(required_equity*100)}% equity, have ~{int(equity*100)}%)"

        options.append(BoundedOption(
            action='fold',
            raise_to=0,
            rationale=fold_rationale,
            ev_estimate=fold_ev,
            style_tag="conservative"
        ))

    # === CALL option (if not blocked) ===
    if 'call' in valid_actions and not block_call:
        cost_bb = cost_to_call / context.get('big_blind', 100) if context.get('big_blind', 100) > 0 else 0
        rationale = f"Call {cost_bb:.1f} BB"
        if apply_range_bias:
            rationale += " - speculative (outside your range)"
            biased_call_ev = "-EV"
        elif equity >= required_equity * profile.call_plus_ev:
            rationale += " - clearly profitable"
            biased_call_ev = call_ev
        elif equity >= required_equity * profile.call_marginal:
            rationale += " - close, your call"
            biased_call_ev = call_ev
        else:
            rationale += " - below pot odds"
            biased_call_ev = call_ev

        options.append(BoundedOption(
            action='call',
            raise_to=0,
            rationale=rationale,
            ev_estimate=biased_call_ev,
            style_tag="standard"
        ))

    # === RAISE options ===
    if 'raise' in valid_actions:
        raise_options = _get_raise_options(context, profile, eff_value_bet_threshold)

        # Range bias: limit to at most 1 raise option when out-of-range preflop
        if apply_range_bias:
            raise_options = raise_options[:1]

        for raise_to, rationale, style_tag in raise_options:
            if apply_range_bias:
                raise_ev = "-EV"
                rationale = f"Speculative raise (outside your range)"
            elif equity >= eff_raise_plus_ev:
                raise_ev = "+EV"
            elif equity >= eff_raise_neutral:
                raise_ev = "neutral"
            else:
                raise_ev = "-EV"  # Below threshold — honest signal regardless of cost_to_call

            # Honest rationale: -EV raises are bluffs, not value bets
            if raise_ev == "-EV" and "value bet" in rationale.lower():
                rationale = rationale.replace("value bet", "bluff bet").replace("Value bet", "Bluff bet")

            options.append(BoundedOption(
                action='raise',
                raise_to=raise_to,
                rationale=rationale,
                ev_estimate=raise_ev,
                style_tag=style_tag
            ))

    # === Post-flop raise option limit ===
    if is_postflop and profile.postflop_max_raise_options is not None:
        raise_opts = [o for o in options if o.action == 'raise']
        if len(raise_opts) > profile.postflop_max_raise_options:
            # Keep the best N raise options (prefer +EV > neutral > -EV)
            ev_order = {'+EV': 0, 'neutral': 1, '-EV': 2, 'marginal': 1}
            sorted_raises = sorted(raise_opts, key=lambda o: ev_order.get(o.ev_estimate, 3))
            keep = set(id(o) for o in sorted_raises[:profile.postflop_max_raise_options])
            options = [o for o in options if o.action != 'raise' or id(o) in keep]

    # === ALL-IN option ===
    if 'all_in' in valid_actions:
        # All-in is +EV with strong hands or when pot-committed
        if equity >= 0.65 or (cost_to_call > context.get('player_stack', 0) * 0.5):
            all_in_ev = "+EV"
        elif equity >= 0.45:
            all_in_ev = "neutral"
        else:
            all_in_ev = "-EV"

        options.append(BoundedOption(
            action='all_in',
            raise_to=0,
            rationale="All-in - maximum commitment",
            ev_estimate=all_in_ev,
            style_tag="aggressive"
        ))

    # === Ensure at least one +EV option (profile-aware) ===
    has_plus_ev = any(o.ev_estimate == "+EV" for o in options)
    if not has_plus_ev and options:
        # Find the best candidate for promotion
        best = max(options, key=lambda o: (
            1 if o.ev_estimate == "+EV" else
            0 if o.ev_estimate == "neutral" else -1
        ))
        if best.ev_estimate != "+EV" and (block_fold or equity >= 0.40):
            has_raises = any(o.action == 'raise' for o in options)
            has_decent_raise = any(
                o.action == 'raise' and o.ev_estimate in ('+EV', 'neutral')
                for o in options
            )
            promotion = profile.check_promotion

            # Decide whether to promote check based on profile
            promote_check = True
            if best.action == 'check' and has_raises:
                if promotion == 'suppress_if_raises':
                    # LAG: never promote check over raises
                    promote_check = False
                elif promotion == 'conditional' and has_decent_raise:
                    # TAG: don't promote check when raises are neutral/+EV
                    promote_check = False

            if promote_check:
                # Build profile-aware rationale
                if best.action == 'check' and promotion == 'always':
                    promoted_rationale = "Check (pot control — protect your stack)"
                elif best.action == 'check' and promotion == 'conditional':
                    promoted_rationale = "Check (wait for better spot)"
                else:
                    promoted_rationale = best.rationale + " (recommended)"

                promoted = BoundedOption(
                    action=best.action,
                    raise_to=best.raise_to,
                    rationale=promoted_rationale,
                    ev_estimate="+EV" if block_fold else best.ev_estimate,
                    style_tag=best.style_tag
                )
                # Replace in-place to preserve original position
                options = [promoted if o == best else o for o in options]

    # === Limit to 2-4 options ===
    if len(options) > 4:
        # Keep fold/check, best call, 1-2 raises
        priority_order = ['fold', 'check', 'call', 'raise', 'all_in']
        options.sort(key=lambda o: (
            priority_order.index(o.action) if o.action in priority_order else 10,
            -1 if o.ev_estimate == "+EV" else 0 if o.ev_estimate == "neutral" else 1
        ))
        options = options[:4]

    # Ensure we have at least 2 options
    if len(options) < 2:
        logger.warning(f"[BOUNDED] Only {len(options)} options generated, valid_actions={valid_actions}")
        # Add a fallback check or call if missing
        if 'check' in valid_actions and not any(o.action == 'check' for o in options):
            options.append(BoundedOption(
                action='check',
                raise_to=0,
                rationale="Check",
                ev_estimate="neutral",
                style_tag="conservative"
            ))
        elif 'call' in valid_actions and not any(o.action == 'call' for o in options):
            options.append(BoundedOption(
                action='call',
                raise_to=0,
                rationale="Call",
                ev_estimate="neutral",
                style_tag="standard"
            ))

    logger.info(
        f"[BOUNDED] Generated {len(options)} options: "
        f"{[f'{o.action}({o.raise_to})' if o.action == 'raise' else o.action for o in options]}"
    )

    # === Layer 6: Emotional window shift (if provided) ===
    if emotional_state and emotional_severity and emotional_severity != 'none':
        shift = EmotionalShift(
            state=emotional_state,
            severity=emotional_severity,
            intensity={'mild': 0.2, 'moderate': 0.5, 'extreme': 0.8}.get(emotional_severity, 0.0),
        )
        options = apply_emotional_window_shift(options, shift, context, profile, rng=rng)

    return options


# ============================================================
# Emotional Window Shift (Layer 6)
#
# Slides the option window along passive<->aggressive based on
# the player's emotional state. Applied AFTER option generation,
# BEFORE math blocking (which is re-applied as safety net).
# ============================================================

@dataclass(frozen=True)
class EmotionalShift:
    """Emotional state input for window shift."""
    state: str        # 'tilted', 'overconfident', 'shaken', 'dissociated', 'composed'
    severity: str     # 'none', 'mild', 'moderate', 'extreme'
    intensity: float  # raw penalty zone intensity 0.0-1.0

    def to_dict(self) -> Dict:
        return {'state': self.state, 'severity': self.severity, 'intensity': self.intensity}


# Probability of impairment (shifted window) by severity
IMPAIRMENT_PROBABILITY = {
    'none': 0.0,
    'mild': 0.70,
    'moderate': 0.85,
    'extreme': 0.95,
}

# Which direction each emotional state pushes options
EMOTIONAL_DIRECTION = {
    'tilted': 'aggressive',
    'overconfident': 'aggressive',
    'shaken': 'passive',
    'dissociated': 'passive',
    'composed': None,
}

# Narrative framing per emotional state (moderate+ severity)
NARRATIVE_FRAMING = {
    'tilted': {
        'aggressive': "They keep pushing you around. Push back.",
        'passive': "Folding is weakness.",
        'raise': "Make them pay.",
        'fold': "Folding again? Really?",
        'check': "Just checking? Are you going to let them walk over you?",
        'call': "At least put up a fight.",
    },
    'overconfident': {
        'aggressive': "You're running hot. Press the advantage.",
        'passive': "You're too good for cautious play.",
        'raise': "You can't lose right now.",
        'fold': "Fold? You? Inconceivable.",
        'check': "Why slow down when you're dominating?",
        'call': "You should be raising, not calling.",
    },
    'shaken': {
        'passive': "Save your chips. Live to fight another hand.",
        'aggressive': "Big bet... are you sure about this?",
        'raise': "Going big? Really? After what just happened?",
        'fold': "Get out while you can.",
        'check': "Take a breath. No need to force it.",
        'call': "Just see the next card. Keep it cheap.",
    },
    'dissociated': {
        # Stripped to bare minimum — less information to reason with
        'aggressive': "Raise.",
        'passive': "Check.",
        'raise': "Raise.",
        'fold': "Fold.",
        'check': "Check.",
        'call': "Call.",
    },
}


def _option_spectrum_position(option: BoundedOption) -> int:
    """Position on the passive<->aggressive spectrum (lower = more passive)."""
    if option.action == 'fold':
        return 0
    if option.action == 'check':
        return 1
    if option.action == 'call':
        return 2
    if option.action == 'raise':
        return 3 + option.raise_to  # bigger raises are more aggressive
    if option.action == 'all_in':
        return 100000  # most aggressive
    return 2  # fallback


def _make_aggressive_option(options: List[BoundedOption], context: Dict,
                            state: str) -> Optional[BoundedOption]:
    """Create a new aggressive option beyond the current window.

    For tilted: adds larger raise or ALL-IN.
    For overconfident: adds overbet / value bet.
    """
    max_raise = context.get('max_raise', 0)
    min_raise = context.get('min_raise', 0)
    pot_total = context.get('pot_total', 0)
    big_blind = context.get('big_blind', 100)
    equity = context.get('equity', 0.5)

    # Find the largest existing raise
    raises = [o for o in options if o.action == 'raise']
    has_all_in = any(o.action == 'all_in' for o in options)
    largest_raise = max((o.raise_to for o in raises), default=0) if raises else 0

    # If no room to raise at all, can't add aggressive option
    if max_raise <= 0 or min_raise <= 0:
        return None

    # Try to add ALL-IN if not already present and there's room
    if not has_all_in and max_raise > largest_raise:
        if state == 'tilted':
            rationale = "All-in — make them pay for everything"
        else:
            rationale = "All-in — you can't lose"
        return BoundedOption(
            action='all_in',
            raise_to=0,
            rationale=rationale,
            ev_estimate="+EV" if equity >= 0.50 else "-EV",
            style_tag="aggressive",
        )

    # Try to add a larger raise (1.5x the largest existing or pot-sized overbet)
    if largest_raise > 0:
        overbet = int(largest_raise * 1.5)
        overbet = max(overbet, int(pot_total * 1.5))
        overbet = min(overbet, max_raise)
        overbet = max(overbet, min_raise)
        if overbet > largest_raise:
            if state == 'tilted':
                rationale = "Overbet — punish them"
            else:
                rationale = "Overbet — press your edge"
            return BoundedOption(
                action='raise',
                raise_to=overbet,
                rationale=rationale,
                ev_estimate="+EV" if equity >= 0.55 else "-EV",
                style_tag="aggressive",
            )

    # Add a pot-sized raise if none exists
    if not raises and max_raise >= min_raise:
        raise_to = min(int(pot_total), max_raise)
        raise_to = max(raise_to, min_raise)
        rationale = "Raise — assert yourself" if state == 'tilted' else "Raise — press your edge"
        return BoundedOption(
            action='raise',
            raise_to=raise_to,
            rationale=rationale,
            ev_estimate="+EV" if equity >= 0.55 else "-EV",
            style_tag="aggressive",
        )

    return None


def _make_passive_option(options: List[BoundedOption], context: Dict,
                         state: str) -> Optional[BoundedOption]:
    """Create a new passive option beyond the current window.

    For shaken: adds FOLD or CHECK.
    For dissociated: adds CHECK.
    """
    valid_actions = context.get('valid_actions', [])
    equity = context.get('equity', 0.5)
    cost_to_call = context.get('cost_to_call', 0)
    pot_total = context.get('pot_total', 0)
    required_equity = calculate_required_equity(pot_total, cost_to_call)

    has_fold = any(o.action == 'fold' for o in options)
    has_check = any(o.action == 'check' for o in options)

    if state == 'dissociated':
        # Dissociated adds CHECK if possible
        if not has_check and 'check' in valid_actions:
            return BoundedOption(
                action='check', raise_to=0,
                rationale="Check.",
                ev_estimate="neutral", style_tag="conservative",
            )
        if not has_fold and 'fold' in valid_actions:
            return BoundedOption(
                action='fold', raise_to=0,
                rationale="Fold.",
                ev_estimate="neutral", style_tag="conservative",
            )
    else:
        # Shaken: adds FOLD first (save chips), then CHECK
        if not has_fold and 'fold' in valid_actions:
            rationale = "Get out while you can." if state == 'shaken' else "Fold."
            fold_ev = "+EV" if equity < required_equity * 0.85 else "neutral"
            return BoundedOption(
                action='fold', raise_to=0,
                rationale=rationale,
                ev_estimate=fold_ev, style_tag="conservative",
            )
        if not has_check and 'check' in valid_actions:
            rationale = "Take a breath. No need to force it."
            return BoundedOption(
                action='check', raise_to=0,
                rationale=rationale,
                ev_estimate="neutral", style_tag="conservative",
            )

    return None


def _apply_narrative_framing(options: List[BoundedOption], state: str) -> List[BoundedOption]:
    """Modify rationale strings based on emotional state.

    Tilted: aggressive=revenge, passive=weakness.
    Overconfident: aggressive=inevitability, fold=inconceivable.
    Shaken: passive=safety, aggressive=doubt.
    Dissociated: rationale stripped to bare minimum.
    """
    framing = NARRATIVE_FRAMING.get(state, {})
    if not framing:
        return options

    modified = []
    for opt in options:
        # For dissociated, strip ALL rationale to bare minimum
        if state == 'dissociated':
            action_frame = framing.get(opt.action, opt.action.capitalize() + '.')
            modified.append(BoundedOption(
                action=opt.action,
                raise_to=opt.raise_to,
                rationale=action_frame,
                ev_estimate=opt.ev_estimate,
                style_tag=opt.style_tag,
            ))
            continue

        # For other states, replace rationale based on direction
        is_passive = opt.action in ('fold', 'check')
        is_aggressive = opt.action in ('raise', 'all_in')

        # Try action-specific framing first, then direction-based
        action_frame = framing.get(opt.action)
        if action_frame is None:
            if is_aggressive:
                action_frame = framing.get('aggressive')
            elif is_passive:
                action_frame = framing.get('passive')

        if action_frame:
            modified.append(BoundedOption(
                action=opt.action,
                raise_to=opt.raise_to,
                rationale=action_frame,
                ev_estimate=opt.ev_estimate,
                style_tag=opt.style_tag,
            ))
        else:
            modified.append(opt)

    return modified


def _reapply_math_blocking(options: List[BoundedOption], context: Dict,
                           profile: OptionProfile = None) -> List[BoundedOption]:
    """Re-apply math blocking as final safety net after emotional shift.

    Ensures emotional state never overrides mathematical guardrails:
    - Remove fold if fold should be blocked
    - Remove call if call should be blocked
    - Ensure at least 2 options remain
    - Ensure at least one non-fold option exists
    """
    if profile is None:
        profile = OptionProfile()

    block_fold = _should_block_fold(context, profile)
    # B2 (Crushing): always block fold, regardless of profile multiplier
    cost_to_call = context.get('cost_to_call', 0)
    if cost_to_call > 0:
        equity = context.get('equity', 0.5)
        pot_total = context.get('pot_total', 0)
        req = calculate_required_equity(pot_total, cost_to_call)
        if equity >= 0.90 or (req > 0 and equity / req >= 1.7):
            block_fold = True
    block_call = _should_block_call(context)
    valid_actions = context.get('valid_actions', [])

    result = list(options)

    # Remove blocked fold
    if block_fold:
        result = [o for o in result if o.action != 'fold']

    # Remove blocked call
    if block_call:
        result = [o for o in result if o.action != 'call']

    # Ensure at least 2 options
    # Short-stack facing bet: push/fold only, don't add CALL
    stack_bb = context.get('stack_bb', 100)
    short_facing_bet = _get_stack_depth(stack_bb) == 'short' and cost_to_call > 0
    if len(result) < 2:
        if 'check' in valid_actions and not any(o.action == 'check' for o in result):
            result.append(BoundedOption(
                action='check', raise_to=0,
                rationale="Check", ev_estimate="neutral", style_tag="conservative",
            ))
        elif 'call' in valid_actions and not block_call and not short_facing_bet and not any(o.action == 'call' for o in result):
            result.append(BoundedOption(
                action='call', raise_to=0,
                rationale="Call", ev_estimate="neutral", style_tag="standard",
            ))

    # Emotional shifts may legitimately produce 5 options (mild adds without removing).
    # Cap at 5 to prevent unbounded growth while preserving the shift's intent.
    if len(result) > 5:
        result = _truncate_options(result, max_options=5)

    return result


def apply_emotional_window_shift(
    options: List[BoundedOption],
    emotional_shift: EmotionalShift,
    context: Dict,
    profile: OptionProfile = None,
    rng: random.Random = None,
) -> List[BoundedOption]:
    """Apply emotional window shift to bounded options.

    Layer 6 in the architecture:
      Option Generation -> Math Blocking ->
      **Emotional Window Shift** -> Math Blocking (re-applied)

    The shift slides the option window along passive<->aggressive spectrum.
    Math blocking is re-applied at the end as a final safety net.

    Args:
        options: Base options from generate_bounded_options()
        emotional_shift: Player's emotional state and severity
        context: Decision context dict (for generating new options and blocking)
        profile: OptionProfile for math blocking thresholds
        rng: Random instance for deterministic testing

    Returns:
        Modified options list with emotional shift applied and math blocking enforced
    """
    if not options:
        return options

    if emotional_shift.state == 'composed' or emotional_shift.severity == 'none':
        return options

    _rng = rng or random.Random()

    # Probabilistic application: roll against severity
    impairment_chance = IMPAIRMENT_PROBABILITY.get(emotional_shift.severity, 0.0)
    if _rng.random() >= impairment_chance:
        # Lucid — return normal options unmodified
        logger.debug(
            f"[EMOTIONAL] Lucid roll for {emotional_shift.state}/{emotional_shift.severity} — "
            f"no shift applied"
        )
        return options

    direction = EMOTIONAL_DIRECTION.get(emotional_shift.state)
    if direction is None:
        return options

    severity = emotional_shift.severity
    modified = list(options)

    logger.info(
        f"[EMOTIONAL] Applying {severity} {emotional_shift.state} shift "
        f"(direction={direction}) to {len(options)} options"
    )

    # === Add option on the extreme end (mild+) ===
    if direction == 'aggressive':
        new_opt = _make_aggressive_option(modified, context, emotional_shift.state)
    else:
        new_opt = _make_passive_option(modified, context, emotional_shift.state)

    if new_opt:
        # For moderate+ severity: if at cap, remove from opposite end to make room
        # For mild: allow expansion (add without removing)
        if severity == 'extreme' and len(modified) >= 4:
            sorted_opts = sorted(modified, key=_option_spectrum_position)
            if direction == 'aggressive':
                to_drop = sorted_opts[0]  # drop most passive
            else:
                to_drop = sorted_opts[-1]  # drop most aggressive
            if len(modified) > 1:
                modified = [o for o in modified if o is not to_drop]
                logger.debug(f"[EMOTIONAL] Dropped {to_drop.action} to make room for {new_opt.action}")

        modified.append(new_opt)
        logger.debug(f"[EMOTIONAL] Added {new_opt.action} option")

    # === Remove option from opposite end (extreme only) ===
    if severity == 'extreme' and len(modified) > 1:
        sorted_opts = sorted(modified, key=_option_spectrum_position)
        if direction == 'aggressive':
            # Remove most passive (FOLD or CHECK)
            to_remove = sorted_opts[0]
        else:
            # Remove most aggressive (largest RAISE or ALL-IN)
            to_remove = sorted_opts[-1]

        # Only remove if we'll still have >= 2 options
        if len(modified) > 2:
            modified = [o for o in modified if o is not to_remove]
            logger.debug(f"[EMOTIONAL] Removed {to_remove.action} option (extreme shift)")

    # === Narrative framing (moderate+) ===
    if severity in ('moderate', 'extreme'):
        modified = _apply_narrative_framing(modified, emotional_shift.state)

    # === Math blocking: final safety net ===
    modified = _reapply_math_blocking(modified, context, profile)

    return modified


def get_emotional_shift(psychology) -> EmotionalShift:
    """Extract EmotionalShift from a PlayerPsychology instance.

    Maps psychology penalty zones to the spec's emotional states:
    - tilted / overheated -> Tilted (aggressive)
    - overconfident -> Overconfident (aggressive)
    - shaken / timid -> Shaken (passive)
    - detached -> Dissociated (passive)

    Severity is derived from penalty intensity:
    - 0 -> None
    - 0.01-0.33 -> Mild
    - 0.34-0.66 -> Moderate
    - 0.67+ -> Extreme

    Args:
        psychology: PlayerPsychology instance (or None)

    Returns:
        EmotionalShift with state, severity, and intensity
    """
    if psychology is None:
        return EmotionalShift(state='composed', severity='none', intensity=0.0)

    try:
        zone_fx = psychology.zone_effects
        penalties = zone_fx.penalties
    except Exception as e:
        logger.warning(f"[EMOTIONAL] Failed to read zone effects: {e}")
        return EmotionalShift(state='composed', severity='none', intensity=0.0)

    if not penalties:
        return EmotionalShift(state='composed', severity='none', intensity=0.0)

    # Map penalty zones to spec emotional states, pick the strongest
    state_map = {
        'tilted': 'tilted',
        'overheated': 'tilted',
        'overconfident': 'overconfident',
        'shaken': 'shaken',
        'timid': 'shaken',
        'detached': 'dissociated',
    }

    best_state = 'composed'
    best_intensity = 0.0

    for zone_name, intensity in penalties.items():
        mapped = state_map.get(zone_name)
        if mapped and intensity > best_intensity:
            best_state = mapped
            best_intensity = intensity

    if best_intensity <= 0:
        return EmotionalShift(state='composed', severity='none', intensity=0.0)

    # Map intensity to severity
    if best_intensity >= 0.67:
        severity = 'extreme'
    elif best_intensity >= 0.34:
        severity = 'moderate'
    else:
        severity = 'mild'

    return EmotionalShift(state=best_state, severity=severity, intensity=best_intensity)


def format_options_for_prompt(options: List[BoundedOption], equity: float, pot_odds: float) -> str:
    """Format bounded options for inclusion in LLM prompt.

    Args:
        options: List of BoundedOption instances
        equity: Current hand equity (0-1)
        pot_odds: Current pot odds ratio

    Returns:
        Formatted string for prompt inclusion
    """
    lines = [
        "=== YOUR OPTIONS ===",
        f"Given the math (equity: {int(equity*100)}%, pot odds: {pot_odds:.1f}:1),",
        "your sensible choices are:",
        ""
    ]

    for i, opt in enumerate(options, 1):
        action_str = opt.action.upper()
        if opt.action == 'raise' and opt.raise_to > 0:
            action_str += f" to {opt.raise_to}"

        lines.append(f"{i}. {action_str}")
        lines.append(f"   {opt.rationale}")
        lines.append(f"   [{opt.ev_estimate}, {opt.style_tag}]")
        lines.append("")

    return "\n".join(lines)

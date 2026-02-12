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

    def to_dict(self) -> Dict:
        """Serialize for prompt capture tracking."""
        return {
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


def _get_raise_options(context: Dict, profile: OptionProfile = None) -> List[Tuple[int, str, str]]:
    """Generate 2-3 sensible raise sizes.

    Args:
        context: Decision context with pot, min_raise, max_raise, stack, equity
        profile: OptionProfile controlling sizing multipliers and value bet threshold

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

    # Value betting emphasis when equity exceeds profile threshold
    value_betting = equity >= profile.value_bet_threshold
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


def generate_bounded_options(context: Dict, profile: OptionProfile = None) -> List[BoundedOption]:
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

    Returns:
        List of 2-4 BoundedOption instances, always including at least one +EV option
    """
    if profile is None:
        profile = OptionProfile()

    options = []
    valid_actions = context.get('valid_actions', [])
    equity = context.get('equity', 0.5)
    cost_to_call = context.get('cost_to_call', 0)
    pot_total = context.get('pot_total', 0)
    stack_bb = context.get('stack_bb', 100)

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
        if equity >= profile.value_bet_threshold and cost_to_call == 0:
            # Strong hand - checking may miss value
            check_ev = "marginal"
            check_rationale = "Check (strong hand - consider betting for value)"
            check_style = "trappy"  # Only appropriate when slowplaying
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
        if required_equity > 0 and equity < required_equity * 0.85:
            fold_ev = "+EV"
        elif equity < required_equity:
            fold_ev = "neutral"
        else:
            fold_ev = "-EV"

        options.append(BoundedOption(
            action='fold',
            raise_to=0,
            rationale=f"Fold (need {int(required_equity*100)}% equity, have ~{int(equity*100)}%)",
            ev_estimate=fold_ev,
            style_tag="conservative"
        ))

    # === CALL option (if not blocked) ===
    if 'call' in valid_actions and not block_call:
        cost_bb = cost_to_call / context.get('big_blind', 100) if context.get('big_blind', 100) > 0 else 0
        rationale = f"Call {cost_bb:.1f} BB"
        if equity >= required_equity * profile.call_plus_ev:
            rationale += " - clearly profitable"
        elif equity >= required_equity * profile.call_marginal:
            rationale += " - close, your call"
        else:
            rationale += " - below pot odds"

        options.append(BoundedOption(
            action='call',
            raise_to=0,
            rationale=rationale,
            ev_estimate=call_ev,
            style_tag="standard"
        ))

    # === RAISE options ===
    if 'raise' in valid_actions:
        raise_options = _get_raise_options(context, profile)
        for raise_to, rationale, style_tag in raise_options:
            # Determine EV for raise based on equity (profile thresholds)
            if equity >= profile.raise_plus_ev:
                raise_ev = "+EV"
            elif equity >= profile.raise_neutral:
                raise_ev = "neutral"
            else:
                raise_ev = "-EV" if cost_to_call > 0 else "neutral"  # Bluff territory

            options.append(BoundedOption(
                action='raise',
                raise_to=raise_to,
                rationale=rationale,
                ev_estimate=raise_ev,
                style_tag=style_tag
            ))

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

    # === Ensure at least one +EV option ===
    has_plus_ev = any(o.ev_estimate == "+EV" for o in options)
    if not has_plus_ev and options:
        # If we blocked fold and no +EV option exists, upgrade the best option
        # (This handles edge cases where all options seem marginal)
        best = max(options, key=lambda o: (
            1 if o.ev_estimate == "+EV" else
            0 if o.ev_estimate == "neutral" else -1
        ))
        if best.ev_estimate != "+EV" and (block_fold or equity >= 0.40):
            # Create a new option with +EV estimate
            options = [o for o in options if o != best]
            options.append(BoundedOption(
                action=best.action,
                raise_to=best.raise_to,
                rationale=best.rationale + " (recommended)",
                ev_estimate="+EV" if block_fold else best.ev_estimate,
                style_tag=best.style_tag
            ))

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

    return options


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

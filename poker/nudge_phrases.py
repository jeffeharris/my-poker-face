"""
Composed nudge phrases for lean bounded options.

Replaces raw EV labels ([+EV], [-EV]) and technical rationale with
playstyle-colored natural language. The differential tone between
action types (e.g. decisive raise vs accepting call) guides LLM
selection without explicit directives.

Architecture:
    BoundedOption → _classify_nudge_key() → NUDGE_PHRASES[profile][key] → phrase
"""

import random
from dataclasses import replace
from typing import Dict, List

from .bounded_options import BoundedOption, OptionProfile, STYLE_PROFILES

# ============================================================
# Nudge phrase dictionary
# ============================================================
#
# Keys: profile name → nudge category → list of phrases
# Each phrase is under 10 words (per hybrid-ai learnings).
# Profiles with missing keys fall through to 'default'.

NUDGE_PHRASES: Dict[str, Dict[str, List[str]]] = {
    'default': {
        'raise_value': ["Bet for value.", "You have the edge."],
        'raise_probe': ["Speculative raise.", "Thin value at best."],
        'raise_bluff': ["Represent strength.", "Risky bluff."],
        'call_strong': ["Clear call.", "Easy call."],
        'call_close': ["Borderline call.", "Close decision."],
        'call_light': ["Speculative call.", "Thin call."],
        'check_slow': ["Trap. Let them bet.", "Disguise your hand."],
        'check_passive': ["Check for now.", "Wait and see."],
        'check_free': ["Take the free card.", "No need to build the pot."],
        'fold_correct': ["Easy fold.", "Clear fold."],
        'fold_tough': ["Smart fold. Not your hand.", "Good fold."],
        'all_in': ["All-in. Maximum pressure.", "Ship it."],
    },
    'tight_aggressive': {
        'raise_value': ["Bet for value.", "Make them pay."],
        'raise_probe': ["Thin bet. Risky.", "Speculative raise."],
        'raise_bluff': ["Risky bluff.", "Aggressive play."],
        'call_strong': ["Easy call.", "Clear call."],
        'call_close': ["Marginal spot.", "Borderline call."],
        'call_light': ["Risky call. Below your range.", "Thin call."],
        'check_slow': ["Trap. Let them bet first.", "Set the trap."],
        'check_passive': ["Leaving value on the table.", "Missing value here."],
        'check_free': ["Take the free card.", "Control the pot."],
        'fold_correct': ["Easy fold. Below your standards.", "Clear fold."],
        'fold_tough': ["Not your hand. Move on.", "Disciplined fold."],
        'all_in': ["All-in. Maximum pressure.", "Put them to the test."],
    },
    'tight_passive': {
        'raise_value': ["Solid hand. Worth a bet.", "Good spot to bet."],
        'raise_probe': ["Small bet. Careful.", "Test the waters."],
        'raise_bluff': ["Risky bluff. Consider folding.", "Represent strength."],
        'call_strong': ["Comfortable call.", "Safe call."],
        'call_close': ["Close call. Be careful.", "Thin margin."],
        'call_light': ["Risky call.", "Speculative call."],
        'check_slow': ["Be patient. Let them act.", "Wait for them to bet."],
        'check_passive': ["Be patient.", "No need to force it."],
        'check_free': ["No reason to build the pot.", "Free card is fine."],
        'fold_correct': ["Easy fold. Save your chips.", "Clear fold."],
        'fold_tough': ["Smart fold. Wait for better.", "Good laydown."],
        'all_in': ["All-in. Strong hand.", "Commit with confidence."],
    },
    'loose_aggressive': {
        'raise_value': ["Punish them.", "Make them sweat."],
        'raise_probe': ["Fire away.", "Keep the heat on."],
        'raise_bluff': ["Bluff. Make them fold.", "Pure aggression."],
        'call_strong': ["Control the pot.", "Disguise your strength."],
        'call_close': ["Stay dangerous.", "Keep them guessing."],
        'call_light': ["See a cheap flop.", "Speculate and attack."],
        'check_slow': ["Let them hang themselves.", "Rope-a-dope."],
        'check_passive': ["Check, but attack next street.", "Reload for later."],
        'check_free': ["Slow down. For now.", "Pick your moment."],
        'fold_correct': ["Junk hand. Save ammo.", "Not worth it."],
        'fold_tough': ["Bad spot. Live to fight again.", "Regroup."],
        'all_in': ["All-in! Maximum chaos.", "Go for the kill."],
    },
    'loose_passive': {
        'raise_value': ["Good hand. Worth a raise.", "Bet while ahead."],
        'raise_probe': ["Small bet to see.", "Probe the field."],
        'raise_bluff': ["Try a bluff.", "Mix it up."],
        'call_strong': ["Easy call.", "Call and see."],
        'call_close': ["Call and see what happens.", "Worth a look."],
        'call_light': ["See one more card.", "Stick around."],
        'check_slow': ["Let them lead.", "Wait and watch."],
        'check_passive': ["See what develops.", "No rush."],
        'check_free': ["Free card? Sure.", "Take the free one."],
        'fold_correct': ["Not your hand. Easy fold.", "Let this one go."],
        'fold_tough': ["Bad cards. Move on.", "Save chips."],
        'all_in': ["All-in. Big moment.", "Go for it."],
    },
}

# ============================================================
# Heads-up nudge overrides
# ============================================================
#
# When playing 1v1, these phrases replace the profile-specific ones.
# Keys missing here fall through to the normal profile → default chain.

HEADS_UP_NUDGE_OVERRIDES: Dict[str, List[str]] = {
    'fold_correct': ["Not worth it heads-up.", "Save chips for a better spot."],
    'fold_tough': ["Even heads-up, this is a fold.", "Patience pays off."],
    'raise_value': ["Punish their wide range.", "Make them pay."],
    'raise_probe': ["Standard heads-up aggression.", "Keep the pressure on."],
    'raise_bluff': ["Represent strength. They fold often.", "Heads-up bluff."],
    'call_strong': ["Trap them.", "Strong call heads-up."],
    'call_close': ["Defend your blind.", "Wide call — standard heads-up."],
    'call_light': ["See a flop.", "Speculate heads-up."],
    'check_slow': ["Let them hang themselves.", "Trap."],
    'check_passive': ["Control the pot.", "Pick your spot."],
    'check_free': ["Take the free card.", "No need to bloat it."],
    'all_in': ["Maximum pressure.", "Ship it heads-up."],
}


# ============================================================
# Nudge key classifier
# ============================================================

def _classify_nudge_key(option: BoundedOption) -> str:
    """Map a BoundedOption to a nudge category based on action, EV, and style_tag.

    Returns one of: raise_value, raise_probe, raise_bluff, call_strong,
    call_close, call_light, check_slow, check_passive, check_free,
    fold_correct, fold_tough, all_in.
    """
    action = option.action
    ev = option.ev_estimate
    tag = option.style_tag

    if action == 'all_in':
        return 'all_in'

    if action == 'raise':
        if tag == 'aggressive' and ev in ('-EV', 'marginal'):
            return 'raise_bluff'
        if ev == '+EV':
            return 'raise_value'
        return 'raise_probe'

    if action == 'call':
        if ev == '+EV':
            return 'call_strong'
        if ev == 'marginal':
            return 'call_close'
        return 'call_light'

    if action == 'check':
        if tag == 'trappy':
            return 'check_slow'
        if ev in ('-EV', 'marginal'):
            return 'check_passive'
        return 'check_free'

    if action == 'fold':
        if ev in ('+EV', 'neutral'):
            return 'fold_correct'
        return 'fold_tough'

    return 'check_free'  # fallback


# ============================================================
# Apply composed nudges
# ============================================================

def apply_composed_nudges(
    options: List[BoundedOption],
    profile_key: str = 'default',
    is_heads_up: bool = False,
) -> List[BoundedOption]:
    """Replace raw rationale with playstyle-colored nudge phrases.

    Each option's rationale is replaced by a randomly selected phrase
    from the matching profile + nudge category. Uses a local Random
    instance per project convention (never mutate global RNG state).

    Args:
        options: List of BoundedOption instances from generate_bounded_options
        profile_key: Style profile name (e.g. 'tight_aggressive')
        is_heads_up: When True, overlay HU-specific phrases before profile fallthrough

    Returns:
        New list of BoundedOption with nudge rationale replacing raw rationale.
    """
    rng = random.Random()
    profile_phrases = NUDGE_PHRASES.get(profile_key, {})
    default_phrases = NUDGE_PHRASES['default']

    result = []
    for opt in options:
        nudge_key = _classify_nudge_key(opt)

        # Look up phrases: HU override first, then profile, fall through to default
        if is_heads_up:
            phrases = HEADS_UP_NUDGE_OVERRIDES.get(nudge_key)
        else:
            phrases = None
        phrases = phrases or profile_phrases.get(nudge_key) or default_phrases.get(nudge_key)

        if phrases:
            nudge_text = rng.choice(phrases)
        else:
            # Should not happen with complete default dict, but safe fallback
            nudge_text = opt.rationale

        result.append(replace(opt, rationale=nudge_text))

    return result

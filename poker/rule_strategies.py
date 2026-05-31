"""
Rule strategies for deterministic poker bots.

Pure-function strategy library shared between RuleBotController (production
opponents) and the experiment tournament runner (chaos baselines). Each
strategy is a function `(context: Dict) -> Dict` that returns
`{'action', 'raise_to'}`. The context dict is built by the calling
controller from game state — strategies themselves are stateless.

Strategy registry:
    BUILT_IN_STRATEGIES: name → strategy fn
    CHAOS_BOTS: name → preset RuleConfig (display name + strategy)

Custom rules can be expressed via _strategy_custom + RuleConfig.rules.
"""

import json
import logging
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional

from .hand_tiers import PREMIUM_HANDS, TOP_10_HANDS, TOP_20_HANDS, TOP_35_HANDS, TOP_45_HANDS

logger = logging.getLogger(__name__)


class FishLeak(str, Enum):
    """Designated, exploitable leak for a fish-archetype tourist.

    The base `_strategy_fish` plays a loose-passive calling station that
    value-bets its strong hands with honest, *unbalanced* sizing (bigger
    hand → bigger bet) but never raises a bet and never bluffs. A leak
    layers one specific, identifiable deviation on top so the same
    tourist makes the same mistake hand after hand: a grinder can learn
    to recognize and exploit it.

    Most leaks (the first block below) just widen how loosely the tourist
    *calls*. The aggression leaks (second block) instead give the tourist
    a readable *betting/raising* tell — transparent value, habitual spew,
    or a rock that only wakes up with the nuts. Each value modifies one
    branch of `_strategy_fish`; when no leak is set the baseline runs
    unchanged.

    Spec: docs/plans/CASH_MODE_EPHEMERAL_TOURISTS.md
    """

    # Passive (calling) leaks — widen the call/fold ladder.
    CALLS_DOWN_TOP_PAIR = "calls_down_top_pair"  # large bets: top pair or better → call
    CHASES_ANY_DRAW = "chases_any_draw"  # medium bets: FD/OESD → call
    DOESNT_BELIEVE_BIG_BETS = "doesnt_believe_big_bets"  # large bets: weakened threshold
    LIMPS_EVERY_HAND = "limps_every_hand"  # preflop: never folds
    POT_COMMITTED_EARLY = "pot_committed_early"  # once ≥30% in, can't fold
    OVERVALUES_FACE_CARDS = "overvalues_face_cards"  # medium bets: any face card → call
    CALLS_RIVER_LIGHT = "calls_river_light"  # river specifically: weak call threshold

    # Aggression leaks — give the tourist a readable betting/raising tell.
    SPITE_RAISES_WHEN_LOSING = (
        "spite_raises_when_losing"  # losing at table: random min-raise bluffs
    )
    BETS_STRONG_TRANSPARENTLY = (
        "bets_strong_transparently"  # bets/raises a wide value range, size = strength
    )
    SPEWS_BLUFFS = "spews_bluffs"  # checked to with air → fires a bet far too often
    STICKY_THEN_POPS = "sticky_then_pops"  # calling station until a monster → pops big


# Tunables for leak triggers. Held as module-level so unit tests can
# patch them without monkey-patching the strategy function itself.
POT_COMMITTED_THRESHOLD = 0.30  # fraction-of-starting-stack invested this hand
SPITE_RAISE_PROBABILITY = 0.08  # per-decision chance to spite-raise when losing
SPEW_BLUFF_PROBABILITY = 0.40  # SPEWS_BLUFFS: chance to bet air when checked to

# Honest value-bet sizing (fraction of pot) when a fish is checked to.
# The tourist's tell: a bigger made hand → a bigger bet, uncorrected and
# unbalanced on purpose. That transparency is what keeps the fish
# readable and exploitable even once it starts betting.
FISH_BET_NUTS = 0.66  # nuts / equity >= 0.80
FISH_BET_STRONG = 0.50  # strong_made / equity >= 0.65
FISH_BET_MEDIUM = 0.40  # medium_made (top pair) — transparent-bettor leak only
FISH_BET_BLUFF = 0.60  # SPEWS_BLUFFS air bet

# Facing-a-bet "pop" sizing (fraction of pot) for the aggression leaks.
FISH_POP_NUTS = 1.0
FISH_POP_STRONG = 0.80
FISH_POP_MEDIUM = 0.60


@dataclass(frozen=True)
class RuleConfig:
    """Configuration for rule-based decision making."""

    strategy: str = "always_fold"  # Built-in strategy name
    rules: tuple = field(default_factory=tuple)  # Custom rules for "custom" strategy
    raise_size: str = "min"  # Default raise sizing: "min", "pot", "half_pot", "all_in"
    name: str = "RuleBot"  # Display name for the bot
    fish_leak: Optional[str] = None  # FishLeak value for strategy='fish' (else ignored)

    @classmethod
    def from_dict(cls, d: Dict) -> 'RuleConfig':
        rules = tuple(d.get('rules', []))
        return cls(
            strategy=d.get('strategy', 'always_fold'),
            rules=rules,
            raise_size=d.get('raise_size', 'min'),
            name=d.get('name', 'RuleBot'),
            fish_leak=d.get('fish_leak'),
        )

    @classmethod
    def from_json_file(cls, path: str) -> 'RuleConfig':
        with open(path) as f:
            return cls.from_dict(json.load(f))


# ============================================================================
# Built-in Strategies
# ============================================================================


def _strategy_always_fold(context: Dict) -> Dict:
    """Fold everything except free checks."""
    if context['cost_to_call'] == 0:
        return {'action': 'check', 'raise_to': 0}
    return {'action': 'fold', 'raise_to': 0}


def _strategy_always_call(context: Dict) -> Dict:
    """Call any bet, check when free."""
    if context['cost_to_call'] == 0:
        return {'action': 'check', 'raise_to': 0}
    if 'call' in context['valid_actions']:
        return {'action': 'call', 'raise_to': 0}
    # Can't call (maybe all-in situation) - fold as fallback
    return {'action': 'fold', 'raise_to': 0}


def _strategy_always_raise(context: Dict) -> Dict:
    """Raise whenever possible, otherwise call."""
    if 'raise' in context['valid_actions']:
        return {'action': 'raise', 'raise_to': context['max_raise']}
    if 'call' in context['valid_actions']:
        return {'action': 'call', 'raise_to': 0}
    if context['cost_to_call'] == 0:
        return {'action': 'check', 'raise_to': 0}
    return {'action': 'fold', 'raise_to': 0}


def _strategy_always_all_in(context: Dict) -> Dict:
    """Go all-in every hand."""
    if 'all_in' in context['valid_actions']:
        return {'action': 'all_in', 'raise_to': 0}
    if 'raise' in context['valid_actions']:
        return {'action': 'raise', 'raise_to': context['player_stack']}
    if 'call' in context['valid_actions']:
        return {'action': 'call', 'raise_to': 0}
    return {'action': 'check', 'raise_to': 0}


def _strategy_abc(context: Dict) -> Dict:
    """
    Simple ABC poker:
    - Raise with premium hands
    - Call with decent hands
    - Fold weak hands
    """
    canonical = context.get('canonical_hand', '')
    equity = context.get('equity', 0.5)
    cost_to_call = context['cost_to_call']

    # Free check always
    if cost_to_call == 0:
        # Bet with good hands
        if equity >= 0.65 and 'raise' in context['valid_actions']:
            return {'action': 'raise', 'raise_to': context['min_raise']}
        return {'action': 'check', 'raise_to': 0}

    # Premium hands - raise
    if canonical in PREMIUM_HANDS or equity >= 0.75:
        if 'raise' in context['valid_actions']:
            return {'action': 'raise', 'raise_to': context['min_raise']}
        return {'action': 'call', 'raise_to': 0}

    # Good hands - call with odds.
    # `pot_odds` may be explicitly None when free to act; default to 1
    # in that case so required_equity falls back to a neutral 0.5.
    pot_odds = context.get('pot_odds')
    if pot_odds is None or pot_odds <= 0:
        pot_odds = 1
    required_equity = 1 / (pot_odds + 1)

    if canonical in TOP_20_HANDS or equity >= required_equity:
        if 'call' in context['valid_actions']:
            return {'action': 'call', 'raise_to': 0}

    # Default fold
    return {'action': 'fold', 'raise_to': 0}


def _strategy_foldy(context: Dict) -> Dict:
    """
    Loose preflop, tight postflop — the classic c-bet exploit target.

    Designed as a validation fixture for Phase 6.6 HU c-bet exploitation
    and Phase 6.7b Part A multiway c-bet: high fold_to_cbet rate while
    still seeing flops, so the detection threshold (fold_to_cbet > 0.60
    AND cbet_faced_count >= 5) can actually trip.

    Behavior:
      - Free postflop → check.
      - Preflop facing ≤ 2BB → call wide (any hand).
      - Preflop facing > 2BB → fold.
      - Postflop facing a bet → fold unless equity >= 0.75 (strong made
        hand). No raises ever.

    Net stats vs typical opens: VPIP high (~0.50-0.80 depending on
    sizing distribution), PFR ~0, fold_to_cbet ~0.70-0.85.
    """
    cost_to_call = context['cost_to_call']
    phase = context.get('phase', 'PRE_FLOP')
    equity = context.get('equity', 0.5)
    big_blind = context.get('big_blind', 100) or 100

    if cost_to_call == 0:
        return {'action': 'check', 'raise_to': 0}

    if phase == 'PRE_FLOP':
        # Call cheaply, fold to anything larger than a 2bb open.
        if cost_to_call <= 2 * big_blind:
            if 'call' in context['valid_actions']:
                return {'action': 'call', 'raise_to': 0}
        return {'action': 'fold', 'raise_to': 0}

    # Postflop facing a bet — fold unless strong made hand.
    if equity >= 0.75:
        if 'call' in context['valid_actions']:
            return {'action': 'call', 'raise_to': 0}
    return {'action': 'fold', 'raise_to': 0}


def _strategy_position_aware(context: Dict) -> Dict:
    """
    Position-based strategy:
    - Late position (button, cutoff): wider range, more aggressive
    - Early position: tight, premium hands only
    """
    position = context.get('position', 'button')
    canonical = context.get('canonical_hand', '')
    equity = context.get('equity', 0.5)
    cost_to_call = context['cost_to_call']

    # Determine position type
    late_positions = {'button', 'cutoff', 'btn', 'co'}
    is_late_position = position.lower() in late_positions

    # Free check
    if cost_to_call == 0:
        if equity >= 0.55 and 'raise' in context['valid_actions']:
            return {'action': 'raise', 'raise_to': context['min_raise']}
        return {'action': 'check', 'raise_to': 0}

    # Late position - play wider
    if is_late_position:
        if canonical in TOP_35_HANDS or equity >= 0.50:
            if 'raise' in context['valid_actions'] and equity >= 0.60:
                return {'action': 'raise', 'raise_to': context['min_raise']}
            if 'call' in context['valid_actions']:
                return {'action': 'call', 'raise_to': 0}

    # Early position - play tight
    else:
        if canonical in TOP_10_HANDS or equity >= 0.70:
            if 'raise' in context['valid_actions']:
                return {'action': 'raise', 'raise_to': context['min_raise']}
            if 'call' in context['valid_actions']:
                return {'action': 'call', 'raise_to': 0}

    return {'action': 'fold', 'raise_to': 0}


def _strategy_pot_odds_robot(context: Dict) -> Dict:
    """
    Pure GTO-ish: only call/raise when pot odds justify it.
    No personality, no bluffing - just math.
    """
    equity = context.get('equity', 0.5)
    cost_to_call = context['cost_to_call']
    pot = context['pot_total']

    if cost_to_call == 0:
        # Bet for value with strong hands
        if equity >= 0.65 and 'raise' in context['valid_actions']:
            # Bet 2/3 pot
            bet_size = int(pot * 0.67)
            bet_size = max(context['min_raise'], min(bet_size, context['max_raise']))
            return {'action': 'raise', 'raise_to': bet_size}
        return {'action': 'check', 'raise_to': 0}

    # Calculate required equity
    pot_odds = pot / cost_to_call if cost_to_call > 0 else float('inf')
    required_equity = 1 / (pot_odds + 1)

    # Pure EV calculation
    if equity >= required_equity:
        # +EV to call - but should we raise?
        if equity >= 0.70 and 'raise' in context['valid_actions']:
            # Value raise
            raise_size = int(pot * 0.75)
            raise_size = max(context['min_raise'], min(raise_size, context['max_raise']))
            return {'action': 'raise', 'raise_to': raise_size}
        if 'call' in context['valid_actions']:
            return {'action': 'call', 'raise_to': 0}

    return {'action': 'fold', 'raise_to': 0}


def _strategy_maniac(context: Dict) -> Dict:
    """
    Hyper-aggressive: raises most hands, barrels all streets.

    Tests if AI can call down light against constant aggression.
    - Raise 80% of hands preflop
    - Triple barrel (bet flop, turn, river) with 75% pot sizing
    - Only slow down with absolute air (< 20% equity)
    """
    equity = context.get('equity', 0.5)
    pot = context['pot_total']
    cost_to_call = context['cost_to_call']

    # Always try to raise/bet
    if 'raise' in context['valid_actions']:
        # 75% pot sizing
        bet_size = int(pot * 0.75)
        bet_size = max(context['min_raise'], min(bet_size, context['max_raise']))

        # Only check with total air when it's free
        if cost_to_call == 0 and equity < 0.20:
            return {'action': 'check', 'raise_to': 0}

        return {'action': 'raise', 'raise_to': bet_size}

    # Can't raise - call if we have anything
    if 'call' in context['valid_actions'] and equity >= 0.25:
        return {'action': 'call', 'raise_to': 0}

    if cost_to_call == 0:
        return {'action': 'check', 'raise_to': 0}

    return {'action': 'fold', 'raise_to': 0}


_TRAP_BAIT_FLOP_CHECK_PROB = 0.70

# Module-level RNG for the trap-bait flop check. Avoids per-call
# `random.Random()` instantiation (which would seed from system
# entropy on every action). Module-level state keeps draws consistent
# within a process while not being globally reproducible against
# experiment seeds — but since trap-bait's aggregate behavior is what
# matters (the stat converges), the lack of seed reproducibility is
# acceptable for the smoke target.
_TRAP_BAIT_RNG = random.Random()


def _strategy_trap_bait(context: Dict) -> Dict:
    """Phase B Item 4: OOP check-then-barrel trap-bait pattern.

    When OOP first-to-act on the flop, checks ~70% to set the trap; on
    turn/river (and any other postflop street decision) barrels like a
    maniac. Designed as a known-target opponent for the open-spot IP
    induce branch: a TieredBot that exploits the pattern should see its
    `flop_check_then_barrel_rate` stat on this bot converge above the
    induce gate threshold within ~50 hands.

    OOP detection here is the HU realization — BB seat acts first
    postflop in HU. Multi-table extension is out of scope for Item 4.
    """
    phase = context.get('phase', 'PRE_FLOP')
    position = (context.get('position') or '').lower()
    cost = context.get('cost_to_call', 0)
    valid = context.get('valid_actions', [])

    is_oop_blind = position == 'big_blind_player'

    if phase == 'FLOP' and is_oop_blind and cost == 0 and 'check' in valid:
        if _TRAP_BAIT_RNG.random() < _TRAP_BAIT_FLOP_CHECK_PROB:
            return {'action': 'check', 'raise_to': 0}

    return _strategy_maniac(context)


def _strategy_bluffbot(context: Dict) -> Dict:
    """
    Bluffs missed draws, especially on river.

    Tests if AI can detect bluffs and make hero calls.
    - On river with low equity but checked to us, bluff pot-sized
    - Value bet strong hands normally
    - Uses pot odds for calling decisions
    """
    equity = context.get('equity', 0.5)
    pot = context['pot_total']
    cost_to_call = context['cost_to_call']
    phase = context.get('phase', 'PRE_FLOP')

    if cost_to_call == 0:  # Can bet
        # River bluff with weak hands (representing missed draws)
        if phase == 'RIVER' and equity < 0.35 and 'raise' in context['valid_actions']:
            # Big bluff - pot-sized bet
            bluff_size = int(pot * 1.0)
            bluff_size = max(context['min_raise'], min(bluff_size, context['max_raise']))
            return {'action': 'raise', 'raise_to': bluff_size}

        # Value bet strong hands
        if equity >= 0.60 and 'raise' in context['valid_actions']:
            bet_size = int(pot * 0.66)
            bet_size = max(context['min_raise'], min(bet_size, context['max_raise']))
            return {'action': 'raise', 'raise_to': bet_size}

        return {'action': 'check', 'raise_to': 0}

    # Facing bet - only continue with decent equity (pot odds)
    pot_odds = pot / cost_to_call if cost_to_call > 0 else float('inf')
    required_equity = 1 / (pot_odds + 1)

    if equity >= required_equity and 'call' in context['valid_actions']:
        return {'action': 'call', 'raise_to': 0}

    return {'action': 'fold', 'raise_to': 0}


def _position_category(position: str) -> str:
    """Categorize position for case matching."""
    pos = position.lower() if position else ''
    if pos in ['button', 'cutoff']:
        return 'late'
    elif pos in ['under_the_gun', 'middle_position_1']:
        return 'early'
    elif pos in ['small_blind_player', 'big_blind_player']:
        return 'blind'
    return 'middle'


def _stack_category(stack_bb: float) -> str:
    """Categorize stack depth."""
    if stack_bb <= 15:
        return 'short'
    elif stack_bb <= 40:
        return 'mid'
    return 'deep'


def _equity_category(equity: float) -> str:
    """Categorize hand strength."""
    if equity >= 0.75:
        return 'premium'
    elif equity >= 0.60:
        return 'strong'
    elif equity >= 0.45:
        return 'medium'
    elif equity >= 0.25:
        return 'weak'
    return 'air'


def _strategy_case_based(context: Dict) -> Dict:
    """
    Case-based strategy using pattern matching on game state.
    Balances value betting, bluffing, and pot odds by situation.

    Adaptive features (v2):
    - Bluffs more vs high-fold opponents (fold_to_cbet > 60%)
    - Bluffs less vs calling stations (fold_to_cbet < 30%)
    - Calls lighter vs aggressive opponents (aggression > 2.0)
    - Calls tighter vs passive opponents (aggression < 0.5)
    """
    equity = context['equity']
    cost = context['cost_to_call']
    pot = context['pot_total']
    phase = context['phase']
    position = context.get('position', '')
    stack_bb = context.get('stack_bb', 100)
    spr = context.get('spr', 10)
    valid = context['valid_actions']

    # Opponent modeling stats
    opp_fold_rate = context.get('opp_fold_to_cbet', 0.5)
    opp_aggression = context.get('opp_aggression', 1.0)
    opp_hands = context.get('opp_hands_observed', 0)

    # Calculate adjustments based on opponent tendencies
    # Only adapt if we have enough observations (5+ hands)
    bluff_adjust = 1.0  # Multiplier for bluff frequency
    call_adjust = 0.0  # Additive adjustment to equity threshold

    if opp_hands >= 5:
        # Adjust bluff threshold based on opponent fold rate
        # If they fold > 60%, bluffs are more profitable
        if opp_fold_rate > 0.6:
            bluff_adjust = 1.5  # Bluff more
        elif opp_fold_rate < 0.3:
            bluff_adjust = 0.5  # Bluff less (calling station)

        # Adjust calling threshold based on aggression
        # High aggression = they bluff more = call lighter
        if opp_aggression > 2.0:
            call_adjust = -0.08  # Need 8% less equity to call
        elif opp_aggression < 0.5:
            call_adjust = 0.05  # Need more equity (they're not bluffing)

    # Categorize inputs
    pos = _position_category(position)
    stack = _stack_category(stack_bb)
    hand = _equity_category(equity)
    facing = 'bet' if cost > 0 else 'check'

    # Helpers
    def bet(fraction):
        size = int(pot * fraction)
        size = max(context['min_raise'], min(size, context['max_raise']))
        if 'raise' in valid:
            return {'action': 'raise', 'raise_to': size}
        return {'action': 'check', 'raise_to': 0}

    def call():
        if 'call' in valid:
            return {'action': 'call', 'raise_to': 0}
        return {'action': 'check', 'raise_to': 0}

    def check():
        if 'check' in valid:
            return {'action': 'check', 'raise_to': 0}
        return {'action': 'fold', 'raise_to': 0}

    def fold():
        return {'action': 'fold', 'raise_to': 0}

    def shove():
        if 'all_in' in valid:
            return {'action': 'all_in', 'raise_to': 0}
        if 'raise' in valid:
            return {'action': 'raise', 'raise_to': context['max_raise']}
        return call()

    # Pot odds calculation with opponent adjustment
    pot_odds_needed = cost / (pot + cost) if cost > 0 else 0
    adjusted_pot_odds = pot_odds_needed + call_adjust

    # === LOW SPR: Commit or fold ===
    if spr < 3:
        if hand in ['premium', 'strong']:
            return shove()
        if facing == 'bet' and hand == 'medium' and equity >= adjusted_pot_odds:
            return call()
        if facing == 'check':
            return check()
        return fold()

    # === SHORT STACK: Push/fold ===
    if stack == 'short':
        if hand in ['premium', 'strong']:
            return shove()
        if facing == 'bet' and equity >= adjusted_pot_odds:
            return call()
        if facing == 'check':
            return check()
        return fold()

    # === FACING BET ===
    if facing == 'bet':
        # Premium: raise for value
        if hand == 'premium':
            return bet(0.75)

        # Strong: call (raise sometimes in position)
        if hand == 'strong':
            if pos == 'late' and phase == 'FLOP':
                return bet(0.67)  # Raise flop IP
            return call()

        # Medium: call if pot odds are right (adjusted for opponent tendencies)
        if hand == 'medium' and equity >= adjusted_pot_odds:
            return call()

        # Weak with odds: call (adjusted for opponent tendencies)
        if hand == 'weak' and equity >= adjusted_pot_odds * 0.9:
            return call()

        return fold()

    # === CAN BET (checked to us) ===

    # Premium: bet big for value
    if hand == 'premium':
        return bet(0.75)

    # Strong: bet for value, size by position
    if hand == 'strong':
        if pos == 'late':
            return bet(0.67)
        return bet(0.5)

    # Medium: bet in position, check OOP
    if hand == 'medium':
        if pos == 'late':
            return bet(0.5)
        return check()

    # Weak: check (no showdown value but not pure air)
    if hand == 'weak':
        return check()

    # Air: bluff in position on river (adjusted by opponent fold rate)
    if hand == 'air':
        # Bluff more vs folders, less vs calling stations
        should_bluff_river = pos == 'late' and phase == 'RIVER' and bluff_adjust >= 1.0
        should_bluff_earlier = (
            pos == 'late' and phase in ['FLOP', 'TURN'] and equity > 0.15 and bluff_adjust >= 0.75
        )

        if should_bluff_river:
            return bet(0.67)
        if should_bluff_earlier:
            return bet(0.5)
        return check()

    return check()


def _strategy_case_based_v2(context: Dict) -> Dict:
    """CaseBot v2 — v1 + sharper VALUE EXTRACTION. The validated better bot.

    How this was found: a full AB battery (v2 vs v1 + each field, 1500h × 6
    seeds) proved v1's RANGE and CALL thresholds are already at a local optimum —
    BOTH tightening (raise-or-fold) AND wider calling regressed in every cell,
    including vs the most competent opponent we have (the punisher reg). The one
    lever that wins: v1 under-extracts when AHEAD — it limps premiums (PFR ~2%)
    and only bets 0.66 — while our whole pool CALLS too much. So v2 builds bigger
    pots with strong hands and leaves everything else (the wide range + the
    call-down) to v1:
      - value-RAISE premium/strong preflop (v1 limps them);
      - OVERBET premium (1.2 pot) / strong (0.9) and thin-value medium (0.6) when
        checked to — the pool calls anyway.
    Result vs v1 (bb/100): jeff_clone +116→+496, punisher +173→+382, Station
    +259→+394, TAG +42→+150, mixed +120→+211, and v2 BEATS a table of v1s
    head-to-head (+156). Only Maniac×5 dips slightly (value-betting into an
    aggressor) — still +, and maniacs are rare vs the calling casino field.
    Pure-static (no opponent reads needed → identical in sim and prod).
    """
    cost = context['cost_to_call']
    pot = context['pot_total']
    valid = context['valid_actions']
    phase = context.get('phase', 'PRE_FLOP')
    bb = context.get('big_blind', 100) or 100
    highest_bet = context.get('highest_bet', bb) or bb
    hand = _equity_category(context['equity'])

    # The AB battery proved v1's RANGE and CALL thresholds are at a local optimum
    # (both tightening and wider-calling regressed everywhere). The one untried
    # lever vs our calling-heavy pool: BUILD BIGGER POTS WITH STRONG HANDS. v1
    # limps premiums (PFR ~2%) and only bets 0.66 — it under-extracts when ahead.
    # v2 = v1 + value-raise premium/strong preflop + OVERBET them postflop; every
    # other spot delegates to v1 (keep the wide range + call-down that win).
    def bet(fraction: float) -> Dict:
        size = max(context['min_raise'], min(int(pot * fraction), context['max_raise']))
        return {'action': 'raise', 'raise_to': size} if 'raise' in valid else {'action': 'check', 'raise_to': 0}

    def do_raise(target_to: int) -> Dict:
        size = max(context['min_raise'], min(int(target_to), context['max_raise']))
        if 'raise' in valid:
            return {'action': 'raise', 'raise_to': size}
        return {'action': 'call', 'raise_to': 0} if 'call' in valid else {'action': 'check', 'raise_to': 0}

    if phase == 'PRE_FLOP':
        facing_raise = cost > 0 and highest_bet > bb
        if hand == 'premium':
            return do_raise(int(3.5 * highest_bet) if facing_raise else int(3.0 * bb))
        if hand == 'strong':
            # value-raise strong both first-in AND vs a raise (3-bet) — the pool
            # pays it off, so build the pot rather than flatting (v1).
            return do_raise(int(3.0 * highest_bet) if facing_raise else int(3.0 * bb))
        return _strategy_case_based(context)  # everything else: v1's wide entry

    # Postflop, checked to us: overbet strong hands + thin-value medium vs the
    # calling pool. They call too wide, so size up.
    if cost == 0:
        if hand == 'premium':
            return bet(1.2)  # big overbet the nuts — they call anyway
        if hand == 'strong':
            return bet(0.9)
        if hand == 'medium':
            return bet(0.6)  # thin value (the pool calls light)
        return _strategy_case_based(context)
    # Facing a bet: v1's call-down/value-raise is already optimal.
    return _strategy_case_based(context)


def _is_maniac_read(context: Dict) -> bool:
    """Coarse opponent classifier: is the table dominated by a hyper-aggressive
    player? Reads the aggregated opponent aggression factor (bets+raises / calls)
    once there's a small sample. AF ~4 = ManiacBot/maniac; balanced regs sit
    ≤1. Threshold 2.2 separates them. Needs only ~8 hands (a BUCKET decision, not
    a tuned offset), so it's usable live — unlike the 100-hand confidence ramp
    the tiered exploitation layer needs.
    """
    if context.get('opp_hands_observed', 0) < 8:
        return False
    return context.get('opp_aggression', 1.0) >= 2.2


def _reg_decision(context: Dict, anti_maniac: bool) -> Dict:
    """A tight-aggressive REG (the competent baseline) with an optional
    anti-maniac DEFENSE mode.

    The point: a tight player should NEUTRALIZE a lone maniac, not get run over —
    and the way you do that in real poker is to WIDEN YOUR DEFENSE, not to fold.
    A maniac's whole edge is (a) stealing blinds you fold too readily and (b)
    bluffing bets you fold to. So `anti_maniac=True` keeps the same tight OPENING
    range but: defends the blinds wide vs a raise, calls down much wider
    (bluff-catch), and 3-bets/raises back. Default mode is a disciplined reg
    (tight, value-bet, fold when beat) — strong vs everyone who isn't a maniac.

    This is the unit of the profile-switching design: a coarse classifier
    (`_is_maniac_read`) flips between the two modes. Keeps variety alive — when
    the field defends, aggression stops dominating and you get real poker
    (flops/showdowns) instead of endless preflop raising.
    """
    equity = context['equity']
    cost = context['cost_to_call']
    pot = context['pot_total']
    valid = context['valid_actions']
    phase = context.get('phase', 'PRE_FLOP')
    pos = _position_category(context.get('position', '') or '')
    bb = context.get('big_blind', 100) or 100
    highest_bet = context.get('highest_bet', bb) or bb
    hand = _equity_category(equity)
    is_blind = pos == 'blind'

    def do_raise(target_to: int) -> Dict:
        size = max(context['min_raise'], min(int(target_to), context['max_raise']))
        if 'raise' in valid:
            return {'action': 'raise', 'raise_to': size}
        return {'action': 'call', 'raise_to': 0} if 'call' in valid else {'action': 'check', 'raise_to': 0}

    def bet(fraction: float) -> Dict:
        size = max(context['min_raise'], min(int(pot * fraction), context['max_raise']))
        return {'action': 'raise', 'raise_to': size} if 'raise' in valid else {'action': 'check', 'raise_to': 0}

    def call() -> Dict:
        if 'call' in valid:
            return {'action': 'call', 'raise_to': 0}
        return {'action': 'check', 'raise_to': 0} if 'check' in valid else {'action': 'fold', 'raise_to': 0}

    def check_or_fold() -> Dict:
        return {'action': 'check', 'raise_to': 0} if 'check' in valid else {'action': 'fold', 'raise_to': 0}

    # ── PREFLOP: tight raise-or-fold; anti-maniac DEFENDS wide vs a raise ──
    if phase == 'PRE_FLOP':
        facing_raise = cost > 0 and highest_bet > bb
        if not facing_raise:
            if hand in ('premium', 'strong', 'medium'):
                return do_raise(int(2.5 * bb))
            return check_or_fold()
        # Facing a raise.
        if hand == 'premium':
            return do_raise(int(3.0 * highest_bet))  # 3-bet for value
        if hand == 'strong':
            return do_raise(int(3.0 * highest_bet)) if (pos == 'late' or anti_maniac) else call()
        if hand == 'medium':
            if anti_maniac or pos in ('late', 'blind'):
                return call()
            return {'action': 'fold', 'raise_to': 0}
        # weak/air: a disciplined reg folds — but vs a MANIAC, defend the blinds
        # wide (its raising range is trash, so you're not folding the best hand).
        if anti_maniac and is_blind:
            return call()
        return {'action': 'fold', 'raise_to': 0}

    # ── POSTFLOP, facing a bet: value-raise; anti-maniac CALLS DOWN wide ──
    if cost > 0:
        pot_odds = cost / (pot + cost) if (pot + cost) > 0 else 1.0
        if hand == 'premium':
            return bet(0.75)  # raise for value
        if hand == 'strong':
            return do_raise(int(highest_bet * 2.5)) if anti_maniac else call()  # raise back vs a maniac
        if hand == 'medium':
            thresh = pot_odds * (0.7 if anti_maniac else 1.0)
            return call() if equity >= thresh else {'action': 'fold', 'raise_to': 0}
        if hand == 'weak' and anti_maniac and equity >= pot_odds * 0.55:
            return call()  # bluff-catch the maniac (its bets are mostly air)
        return {'action': 'fold', 'raise_to': 0}

    # ── POSTFLOP, checked to us: value-bet ──
    if hand in ('premium', 'strong'):
        return bet(0.66)
    if hand == 'medium':
        return bet(0.5) if pos in ('late', 'blind') else check_or_fold()
    return check_or_fold()


def _strategy_reg(context: Dict) -> Dict:
    """Tight-aggressive reg, no adaptation (the 'vs-competent' baseline)."""
    return _reg_decision(context, anti_maniac=False)


def _strategy_reg_vs_maniac(context: Dict) -> Dict:
    """DEAD END (kept for the record): a tight reg that "defends wide" vs a
    maniac. It BACKFIRES — measured the maniac's edge going from +102 (vs a plain
    Reg field) to +352. Why: the tiered maniac has a REAL value range (the EV
    floor stops it pure-bluffing), so calling down wider pays off its value and
    raising back gets stacked. You cannot out-tight a maniac. See
    docs/eval_results/VARIETY_VALIDATION_RESULTS.md (anti-maniac)."""
    return _reg_decision(context, anti_maniac=True)


def _strategy_reg_adaptive(context: Dict) -> Dict:
    """The production unit — profile-switching done RIGHT.

    Default: a disciplined tight reg (strong vs everyone who isn't a maniac).
    When it READS a maniac (`_is_maniac_read`): switch to the LOOSE-VALUE
    CaseBot profile — the one style that actually beats a maniac (+175 vs
    Maniac×5), by value-betting the maniac harder than it gets value-owned and
    never folding to its steals. NOT tight-defense (`reg_vs_maniac`), which the
    data proved backfires. (In sims with no opponent stats it stays in reg mode;
    the classifier is live-only — but both endpoint profiles are independently
    validated, and the classifier is a cheap ~8-hand bucket call that works in
    prod where stats exist.)"""
    if _is_maniac_read(context):
        return _strategy_case_based_v2(context)  # loose-value beats maniacs
    return _reg_decision(context, anti_maniac=False)


def _strategy_reg_plus(context: Dict) -> Dict:
    """Reg+ — the COMPETENT YARDSTICK (keystone, docs/plans/BUILD_A_BETTER_BOT.md §2).

    The plain `reg` LOSES to CaseBotV2 (−88 HU, −126 6max) for three reasons:
    (1) it under-extracts when ahead (0.66-pot value bets — the station would call
    an overbet), (2) it PAYS OFF CaseBotV2's polarized overbets (calls "medium" by
    raw pot odds, not seeing that a value-heavy bettor's big bet is strength), and
    (3) it nits itself out of a fish table preflop (folds ~65%, making nothing).

    Reg+ fixes all three and so does what CaseBotV2 *cannot*:
      - EXTRACT like CaseBotV2 — overbet premium/strong when checked to, the
        calling pool pays anyway (kills leak 1);
      - but FOLD to a polarized big bet instead of calling down (kills leak 2).
        That is the asymmetry that beats a station: when *Reg+* overbets, the
        station pays; when the station overbets, Reg+ folds. CaseBotV2 calls down,
        so it pays off Reg+'s value — Reg+ does not return the favor;
      - never bluff-barrel a caller — give up air rather than spew (the station
        calls down, so a bluff just burns money);
      - play wide enough preflop to not bleed (iso the limpers for value, defend
        position) but TIGHTEN vs a raise (a value-heavy opponent's raise = a real
        range), so it isn't paying to enter dominated.

    This is the disciplined profile the adaptive bot (§3) switches to on a
    competent read. It is NOT meant to beat the fish (it folds to their value and
    misses their bluffs) — that is the fish-hunter profile's job. Its one mandate:
    neutralize / beat CaseBotV2 so we finally have a yardstick for robustness.
    Pure-static (no opponent reads) so sim == prod.
    """
    cost = context['cost_to_call']
    pot = context['pot_total']
    valid = context['valid_actions']
    phase = context.get('phase', 'PRE_FLOP')
    pos = _position_category(context.get('position', '') or '')
    bb = context.get('big_blind', 100) or 100
    highest_bet = context.get('highest_bet', bb) or bb
    spr = context.get('spr', 10)
    stack_bb = context.get('stack_bb', 100)
    hand = _equity_category(context['equity'])
    equity = context['equity']
    in_position = pos in ('late', 'blind')

    def do_raise(target_to: int) -> Dict:
        size = max(context['min_raise'], min(int(target_to), context['max_raise']))
        if 'raise' in valid:
            return {'action': 'raise', 'raise_to': size}
        return {'action': 'call', 'raise_to': 0} if 'call' in valid else {'action': 'check', 'raise_to': 0}

    def bet(fraction: float) -> Dict:
        size = max(context['min_raise'], min(int(pot * fraction), context['max_raise']))
        return {'action': 'raise', 'raise_to': size} if 'raise' in valid else {'action': 'check', 'raise_to': 0}

    def shove() -> Dict:
        if 'all_in' in valid:
            return {'action': 'all_in', 'raise_to': 0}
        if 'raise' in valid:
            return {'action': 'raise', 'raise_to': context['max_raise']}
        return {'action': 'call', 'raise_to': 0} if 'call' in valid else {'action': 'check', 'raise_to': 0}

    def call() -> Dict:
        if 'call' in valid:
            return {'action': 'call', 'raise_to': 0}
        return {'action': 'check', 'raise_to': 0} if 'check' in valid else {'action': 'fold', 'raise_to': 0}

    def check_or_fold() -> Dict:
        return {'action': 'check', 'raise_to': 0} if 'check' in valid else {'action': 'fold', 'raise_to': 0}

    # ── PREFLOP: iso the limpers for value (don't bleed), tighten vs a raise ──
    if phase == 'PRE_FLOP':
        facing_raise = cost > 0 and highest_bet > bb
        if not facing_raise:
            # First-in / limped pot. Iso big — the station calls, and we build a
            # pot where we have the range + position edge.
            if hand in ('premium', 'strong', 'medium'):
                return do_raise(int(3.0 * bb))
            if hand == 'weak' and in_position:
                return do_raise(int(3.0 * bb))  # widen IP/blind so we don't bleed
            return check_or_fold()  # fold air OOP (free check in the BB)
        # Facing a raise — a value-heavy opponent's raise is a REAL range. Tighten:
        if hand == 'premium':
            return do_raise(int(3.0 * highest_bet))  # 3-bet for value
        if hand == 'strong':
            return call()  # flat, don't bloat vs its value-raise range
        if hand == 'medium' and in_position:
            return call()  # see a flop in position
        return {'action': 'fold', 'raise_to': 0}

    # ── Commit short / low-SPR with a made hand ──
    if (spr < 3 or stack_bb <= 15) and hand in ('premium', 'strong'):
        return shove()

    # ── POSTFLOP, facing a bet: bluff-catch small, FOLD to polarized big bets ──
    if cost > 0:
        # How big is the bet relative to the pot before it? >=0.8 ≈ a polarized
        # bet, and a value-heavy opponent (CaseBotV2 overbets only premium/strong)
        # is rarely bluffing there → fold everything but the nuts. Do NOT pay off.
        bet_over_pot = cost / max(1.0, pot - cost)
        big_bet = bet_over_pot >= 0.8
        pot_odds = cost / (pot + cost) if (pot + cost) > 0 else 1.0
        if hand == 'premium':
            return bet(0.9)  # raise for value
        if hand == 'strong':
            return call()  # call down — ahead of most value bets
        if hand == 'medium':
            # Bluff-catch a SMALL/thin bet by price; fold to a big polarized bet.
            return call() if (not big_bet and equity >= pot_odds) else {'action': 'fold', 'raise_to': 0}
        if hand == 'weak' and not big_bet and equity >= pot_odds * 1.1:
            return call()  # only the cheapest, best-priced bluff-catches
        return {'action': 'fold', 'raise_to': 0}

    # ── POSTFLOP, checked to us: EXTRACT large (the pool calls), never bluff ──
    if hand == 'premium':
        return bet(1.1)  # overbet the nuts — the station calls anyway
    if hand == 'strong':
        return bet(0.85)
    if hand == 'medium':
        return bet(0.55) if in_position else check_or_fold()  # thin value IP only
    return check_or_fold()  # weak/air: give up, do NOT bluff a caller


def _fish_bet(context: Dict, fraction: float) -> Optional[Dict]:
    """Build a pot-fraction bet/raise for a fish, clamped to legal sizing.

    Returns a raise decision, or None when raising isn't available (the
    caller then falls back to check/call). Sizing mirrors
    `_strategy_case_based` / `_strategy_bluffbot`: `pot * fraction`,
    floored at the minimum legal raise and capped at the maximum. Pot
    defaults to 0 (→ min raise) so the helper stays safe when a caller or
    test fixture omits `pot_total`.
    """
    if 'raise' not in context.get('valid_actions', []):
        return None
    pot = context.get('pot_total', 0) or 0
    min_raise = context.get('min_raise', 0)
    size = max(min_raise, int(pot * fraction))
    max_raise = context.get('max_raise')
    if max_raise:
        size = min(size, max_raise)
    return {'action': 'raise', 'raise_to': size}


def _fish_value_fraction(context: Dict, *, include_top_pair: bool) -> Optional[float]:
    """Pot fraction to value-bet when checked to, or None to just check.

    Honest, monotonic sizing — bigger made hand → bigger bet — keyed on
    the made-hand tier with an equity fallback for fixtures that omit
    `made_tier`. `include_top_pair` widens the value range down to top
    pair (the transparent-bettor leak); the baseline only bets
    strong_made or better.
    """
    made_tier = context.get('made_tier', 'air')
    equity = context.get('equity', 0.5)
    if made_tier == 'nuts' or equity >= 0.80:
        return FISH_BET_NUTS
    if made_tier == 'strong_made' or equity >= 0.65:
        return FISH_BET_STRONG
    if include_top_pair and (made_tier == 'medium_made' or equity >= 0.55):
        return FISH_BET_MEDIUM
    return None


def _fish_pop_fraction(made_tier: str) -> float:
    """Pot fraction for a fish's facing-a-bet pop, sized by made tier."""
    if made_tier == 'nuts':
        return FISH_POP_NUTS
    if made_tier == 'strong_made':
        return FISH_POP_STRONG
    return FISH_POP_MEDIUM


def _strategy_fish(context: Dict) -> Dict:
    """Loose-passive 'calling station' tourist with honest value betting.

    The fish is here to lose chips, not to outplay anyone. Base behavior:
      - Checked to → value-bet strong_made+ hands with honest, unbalanced
        sizing (bigger hand → bigger bet); check everything else. This is
        the tell: a fish that bets is a fish that has something.
      - Facing a small bet (≤ 3 BB) → always calls. Tourist doesn't
        understand pot odds; sees a bet, pays the bet.
      - Facing a medium bet (3-8 BB) → calls with any pair, draw, or
        broadway. Folds total air.
      - Facing a large bet (> 8 BB) → calls only with a real hand
        (top-20 or equity >= 0.55). Folds otherwise.
      - Never *raises* a bet and never bluffs at baseline. Facing-a-bet
        aggression and bluffing only come from an aggression leak.

    Net play profile: very high VPIP (~0.70-0.85), low PFR, near-zero
    fold_to_cbet (calls flop wide), high WTSD. The classic chip-donor
    pattern grinders feast on — now legible, because its bets (and, with
    a leak, its raises) map transparently to hand strength.

    **Designated leaks** (`context['fish_leak']` matches a `FishLeak`
    value) layer one specific deviation on top of the baseline. Each
    leak fires only when its trigger holds; otherwise base behavior
    runs unchanged. See `FishLeak` enum for the catalogue.

    No psychology, no position adjustments, no opponent modeling, no
    bet-sizing balance. The tourist is too drunk / distracted /
    inexperienced for any of that.
    """
    canonical = context.get('canonical_hand', '')
    equity = context.get('equity', 0.5)
    cost_to_call = context['cost_to_call']
    big_blind = context.get('big_blind', 2)
    cost_in_bb = cost_to_call / big_blind if big_blind > 0 else cost_to_call
    leak = context.get('fish_leak')
    street = context.get('street', '')
    made_tier = context.get('made_tier', 'air')

    # --- POT_COMMITTED_EARLY: once enough in the pot, can't fold ------
    # Fires across every branch — overrides fold decisions but never
    # turns a check into a call. Cheap to evaluate first so it short-
    # circuits the cost-tier checks below.
    if (
        leak == FishLeak.POT_COMMITTED_EARLY
        and cost_to_call > 0
        and context.get('committed_fraction_of_stack', 0.0) >= POT_COMMITTED_THRESHOLD
        and 'call' in context['valid_actions']
    ):
        return {'action': 'call', 'raise_to': 0}

    # --- SPITE_RAISES_WHEN_LOSING: down at table → occasional bluff ---
    # Probabilistic. Uses context['_rng'] if provided (lets tests pin
    # the roll); falls back to module random so prod is non-deterministic.
    if (
        leak == FishLeak.SPITE_RAISES_WHEN_LOSING
        and context.get('is_losing_at_table', False)
        and 'raise' in context['valid_actions']
    ):
        rng = context.get('_rng') or random
        if rng.random() < SPITE_RAISE_PROBABILITY:
            return {'action': 'raise', 'raise_to': context['min_raise']}

    if cost_to_call == 0:
        # --- LIMPS_EVERY_HAND: preflop, never folds OR raises — just limps ---
        # The tourist truly is passive on every preflop hand: suppress
        # even the value bet so they only ever limp in preflop.
        if leak == FishLeak.LIMPS_EVERY_HAND and street == 'preflop':
            return {'action': 'check', 'raise_to': 0}

        # --- Honest value betting (baseline + transparent-bettor widening) ---
        # Bigger made hand → bigger bet, no balance. The transparent
        # bettor widens the value range down to top pair; everyone else
        # bets strong_made or better. A fish that bets has something.
        include_top_pair = leak == FishLeak.BETS_STRONG_TRANSPARENTLY
        frac = _fish_value_fraction(context, include_top_pair=include_top_pair)
        if frac is not None:
            bet = _fish_bet(context, frac)
            if bet is not None:
                return bet

        # --- SPEWS_BLUFFS: checked to with air → fire a bet far too often ---
        # The bluffer who won't stop betting. Probabilistic; uses
        # context['_rng'] when provided so tests can pin the roll.
        if leak == FishLeak.SPEWS_BLUFFS:
            rng = context.get('_rng') or random
            if rng.random() < SPEW_BLUFF_PROBABILITY:
                bluff = _fish_bet(context, FISH_BET_BLUFF)
                if bluff is not None:
                    return bluff

        return {'action': 'check', 'raise_to': 0}

    # --- LIMPS_EVERY_HAND: preflop facing-bet, always calls ----------
    # Tourist who limps every hand can't lay one down preflop either.
    if (
        leak == FishLeak.LIMPS_EVERY_HAND
        and street == 'preflop'
        and 'call' in context['valid_actions']
    ):
        return {'action': 'call', 'raise_to': 0}

    # --- STICKY_THEN_POPS: calling station until a monster, then pops big ---
    # The rock that finally wakes up. Pure passive call/fold (below) on
    # everything except a genuine monster (two pair / set / better), which
    # it raises hard. A raise from this tourist means the nuts.
    if leak == FishLeak.STICKY_THEN_POPS and made_tier in ('nuts', 'strong_made'):
        pop = _fish_bet(context, _fish_pop_fraction(made_tier))
        if pop is not None:
            return pop

    # --- BETS_STRONG_TRANSPARENTLY: value-raise top pair or better -------
    # Wears the hand on its sleeve facing a bet too: raises any top pair
    # or better, sized by strength. Otherwise falls through to the
    # calling-station ladder below.
    if leak == FishLeak.BETS_STRONG_TRANSPARENTLY and context.get(
        'has_top_pair_or_better', False
    ):
        pop = _fish_bet(context, _fish_pop_fraction(made_tier))
        if pop is not None:
            return pop

    if cost_in_bb <= 3:
        if 'call' in context['valid_actions']:
            return {'action': 'call', 'raise_to': 0}
        return {'action': 'fold', 'raise_to': 0}

    if cost_in_bb <= 8:
        is_pair = context.get('is_pair', False)
        is_suited_or_broadway = canonical in TOP_35_HANDS or context.get('is_suited', False)

        # --- CHASES_ANY_DRAW: any FD / OESD → call ----------------------
        if leak == FishLeak.CHASES_ANY_DRAW and (
            context.get('has_flush_draw', False) or context.get('has_oesd', False)
        ):
            if 'call' in context['valid_actions']:
                return {'action': 'call', 'raise_to': 0}

        # --- OVERVALUES_FACE_CARDS: any J/Q/K/A in hole → call --------
        if leak == FishLeak.OVERVALUES_FACE_CARDS and context.get('has_face_card', False):
            if 'call' in context['valid_actions']:
                return {'action': 'call', 'raise_to': 0}

        if is_pair or is_suited_or_broadway or equity >= 0.40:
            if 'call' in context['valid_actions']:
                return {'action': 'call', 'raise_to': 0}
        return {'action': 'fold', 'raise_to': 0}

    # Large bet (> 8 BB)
    # --- CALLS_DOWN_TOP_PAIR: any top pair or better → call ----------
    if leak == FishLeak.CALLS_DOWN_TOP_PAIR and context.get('has_top_pair_or_better', False):
        if 'call' in context['valid_actions']:
            return {'action': 'call', 'raise_to': 0}

    # --- DOESNT_BELIEVE_BIG_BETS: weakens the threshold significantly -
    # Hero-calls with TOP_45 or any pair or equity >= 0.40. Tourist
    # "can't be bluffed" — they pay off legitimate value bets too.
    if leak == FishLeak.DOESNT_BELIEVE_BIG_BETS:
        if canonical in TOP_45_HANDS or context.get('is_pair', False) or equity >= 0.40:
            if 'call' in context['valid_actions']:
                return {'action': 'call', 'raise_to': 0}

    # --- CALLS_RIVER_LIGHT: river only, weakened threshold -----------
    if leak == FishLeak.CALLS_RIVER_LIGHT and street == 'river':
        if canonical in TOP_45_HANDS or equity >= 0.40:
            if 'call' in context['valid_actions']:
                return {'action': 'call', 'raise_to': 0}

    if canonical in TOP_20_HANDS or equity >= 0.55:
        if 'call' in context['valid_actions']:
            return {'action': 'call', 'raise_to': 0}
    return {'action': 'fold', 'raise_to': 0}


BUILT_IN_STRATEGIES = {
    'always_fold': _strategy_always_fold,
    'always_call': _strategy_always_call,
    'always_raise': _strategy_always_raise,
    'always_all_in': _strategy_always_all_in,
    'abc': _strategy_abc,
    'foldy': _strategy_foldy,
    'position_aware': _strategy_position_aware,
    'pot_odds_robot': _strategy_pot_odds_robot,
    'maniac': _strategy_maniac,
    'trap_bait': _strategy_trap_bait,
    'bluffbot': _strategy_bluffbot,
    'case_based': _strategy_case_based,
    'case_based_v2': _strategy_case_based_v2,
    'reg': _strategy_reg,
    'reg_plus': _strategy_reg_plus,
    'reg_vs_maniac': _strategy_reg_vs_maniac,
    'reg_adaptive': _strategy_reg_adaptive,
    'fish': _strategy_fish,
}


# ============================================================================
# Custom Rule Evaluation
# ============================================================================


def _evaluate_condition(condition: str, context: Dict) -> bool:
    """
    Evaluate a condition string against the context.

    Supported variables:
        equity, pot_odds, cost_to_call, pot_total, player_stack,
        stack_bb, position, phase, canonical_hand, is_premium,
        is_top_10, is_top_20, is_suited, is_pair

    Supported operators:
        ==, !=, >=, <=, >, <, and, or, in

    Examples:
        "equity >= 0.65"
        "pot_odds >= 3 and equity >= 0.30"
        "canonical_hand in ['AA', 'KK', 'QQ']"
        "is_premium"
        "default"
    """
    if condition == 'default':
        return True

    # Build evaluation namespace.
    # `pot_odds` may legitimately be None when free to act (cost_to_call=0).
    # Coerce to a large value so conditions like `pot_odds >= 3` evaluate
    # truthfully — getting "infinite" pot odds when nothing is owed is
    # the mathematically sensible reading.
    canonical = context.get('canonical_hand', '')
    raw_pot_odds = context.get('pot_odds')
    if raw_pot_odds is None:
        raw_pot_odds = float('inf')
    namespace = {
        'equity': context.get('equity', 0.5),
        'pot_odds': raw_pot_odds,
        'cost_to_call': context.get('cost_to_call', 0),
        'pot_total': context.get('pot_total', 0),
        'player_stack': context.get('player_stack', 0),
        'stack_bb': context.get('stack_bb', 100),
        'position': context.get('position', 'button'),
        'phase': context.get('phase', 'PRE_FLOP'),
        'canonical_hand': canonical,
        'is_premium': canonical in PREMIUM_HANDS,
        'is_top_10': canonical in TOP_10_HANDS,
        'is_top_20': canonical in TOP_20_HANDS,
        'is_top_35': canonical in TOP_35_HANDS,
        'is_suited': canonical.endswith('s') if canonical else False,
        'is_pair': len(canonical) == 2 and canonical[0] == canonical[1] if canonical else False,
        'num_opponents': context.get('num_opponents', 1),
        'is_heads_up': context.get('num_opponents', 1) == 1,
    }

    try:
        # Safe eval with restricted namespace
        result = eval(condition, {"__builtins__": {}}, namespace)
        return bool(result)
    except Exception as e:
        logger.warning(f"Rule condition evaluation failed: {condition} - {e}")
        return False


def _calculate_raise_size(size_spec: str, context: Dict) -> int:
    """Calculate raise amount based on size specification."""
    pot = context.get('pot_total', 0)
    min_raise = context.get('min_raise', 100)
    max_raise = context.get('max_raise', 1000)

    if size_spec == 'min':
        return min_raise
    elif size_spec == 'pot':
        return max(min_raise, min(pot, max_raise))
    elif size_spec == 'half_pot':
        return max(min_raise, min(pot // 2, max_raise))
    elif size_spec == 'all_in':
        return max_raise
    elif size_spec.endswith('x'):
        # Multiplier: "3x" means 3x the big blind
        try:
            multiplier = float(size_spec[:-1])
            bb = context.get('big_blind', 100)
            return max(min_raise, min(int(bb * multiplier), max_raise))
        except ValueError:
            return min_raise
    else:
        # Try to parse as integer
        try:
            return max(min_raise, min(int(size_spec), max_raise))
        except ValueError:
            return min_raise


def _strategy_custom(context: Dict, rules: tuple) -> Dict:
    """
    Evaluate custom rules in priority order.
    First matching rule wins.
    """
    for rule in rules:
        condition = rule.get('condition', 'default')
        if _evaluate_condition(condition, context):
            action = rule.get('action', 'fold')

            # Handle raise sizing
            if action == 'raise':
                size_spec = rule.get('raise_size', 'min')
                raise_to = _calculate_raise_size(size_spec, context)
                return {'action': 'raise', 'raise_to': raise_to}

            return {'action': action, 'raise_to': 0}

    # No rules matched - fold as ultimate fallback
    return {'action': 'fold', 'raise_to': 0}


# ============================================================================
# Bot Presets
# ============================================================================

# Pre-defined bot configurations for common experiments
CHAOS_BOTS = {
    'always_fold': RuleConfig(strategy='always_fold', name='FoldBot'),
    'always_call': RuleConfig(strategy='always_call', name='CallStation'),
    'always_raise': RuleConfig(strategy='always_raise', name='AggBot'),
    'always_all_in': RuleConfig(strategy='always_all_in', name='YOLOBot'),
    'abc': RuleConfig(strategy='abc', name='ABCBot'),
    'foldy': RuleConfig(strategy='foldy', name='FoldyBot'),
    'position_aware': RuleConfig(strategy='position_aware', name='PositionBot'),
    'pot_odds_robot': RuleConfig(strategy='pot_odds_robot', name='GTO-Lite'),
    'maniac': RuleConfig(strategy='maniac', name='ManiacBot'),
    'trap_bait': RuleConfig(strategy='trap_bait', name='TrapBaitBot'),
    'bluffbot': RuleConfig(strategy='bluffbot', name='BluffBot'),
    'case_based': RuleConfig(strategy='case_based', name='CaseBot'),
    'case_based_v2': RuleConfig(strategy='case_based_v2', name='CaseBotV2'),
    'reg': RuleConfig(strategy='reg', name='Reg'),
    'reg_vs_maniac': RuleConfig(strategy='reg_vs_maniac', name='RegVsManiac'),
    'reg_adaptive': RuleConfig(strategy='reg_adaptive', name='RegAdaptive'),
}

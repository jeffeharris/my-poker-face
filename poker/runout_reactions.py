"""
Run-Out Reaction System.

Pre-computes avatar emotion reactions for all-in run-outs based on equity swings.
Since the deck is deterministic (pre-shuffled), we know exactly which cards will
come at each street. We calculate equity at each stage and map notable swings to
avatar emotions, personalized by each AI player's personality traits.

Note: This system intentionally bypasses EmotionalState's dimensional model.
During run-outs, equity swings happen rapidly per street, so we map equity
deltas directly to display emotions rather than updating the slower-moving
dimensional state. Overrides are cleared at hand end, restoring baseline behavior.

Usage:
    schedule = compute_runout_reactions(game_state, ai_controllers)
    # schedule.reactions_by_phase = {
    #     'FLOP': [PlayerReaction('Batman', 'happy', 0.43, 0.72, +0.29), ...],
    #     'TURN': [...],
    #     'RIVER': [...]
    # }
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

from .card_utils import card_to_string
from .equity_calculator import EquityCalculator

if TYPE_CHECKING:
    from .controllers import AIPlayerController
    from .poker_game import PokerGameState

logger = logging.getLogger(__name__)

# Base threshold for notable equity swing (15%)
BASE_REACTION_THRESHOLD = 0.15

# Personality modifier range: threshold can shift ±0.05
REACTIVE_THRESHOLD_OFFSET = -0.05  # Volatile personalities: 10%
STOIC_THRESHOLD_OFFSET = 0.05  # Stoic personalities: 20%

# Trait thresholds for personality classification
HIGH_TRAIT = 0.7
LOW_TRAIT = 0.3

# Monte Carlo iterations for equity calculation during run-outs
EQUITY_ITERATIONS = 2000  # Match DecisionAnalyzer; 2000 ≈ 20ms per calc


@dataclass(frozen=True)
class PlayerReaction:
    """A single avatar emotion reaction during run-out."""

    player_name: str
    emotion: str
    equity_before: float
    equity_after: float
    delta: float


@dataclass(frozen=True)
class RunoutStep:
    """One reveal step on the run-out timeline (the unit the client director walks).

    A step is finer-grained than a street: the flop is three steps (one per
    card), so a player can light up on the card that hit them and stay flat on
    the others. ``card_index`` is the 0-based position of this card within its
    street (always 0 for the single-card INITIAL/TURN/RIVER/SHOWDOWN steps; 0/1/2
    for the flop). ``reactions`` are the AI faces triggered by *this* card's
    equity delta.

    The step intentionally carries **no board cards** — the client sources each
    street's faces from the per-street game-state push it already receives, so
    future-street cards never ship in this payload (no spoiler/cheat surface).
    """

    phase: str
    card_index: int
    reactions: List[PlayerReaction]


@dataclass
class ReactionSchedule:
    """Pre-computed schedule of avatar reactions for an all-in run-out.

    Two views of the same underlying equity walk:

    - ``reactions_by_phase`` — street-granular (FLOP's three cards collapsed into
      one reaction from the start→end-of-flop delta). This is the legacy view the
      backend per-street emit and the desktop path consume; its semantics are
      unchanged.
    - ``steps`` — per-card granular, ordered (INITIAL → flop₁ → flop₂ → flop₃ →
      TURN → RIVER → SHOWDOWN). This is what the mobile ``useRunoutDirector``
      walks to drive per-card reactions on a client-owned clock.
    """

    reactions_by_phase: Dict[str, List[PlayerReaction]] = field(default_factory=dict)
    steps: List[RunoutStep] = field(default_factory=list)


def compute_runout_reactions(
    game_state: 'PokerGameState',
    ai_controllers: Dict[str, 'AIPlayerController'],
) -> ReactionSchedule:
    """Pre-compute all avatar reactions for the run-out.

    Simulates the remaining community cards (deterministic from deck order),
    calculates equity at each street, and generates reactions for AI players
    with notable equity swings.

    Args:
        game_state: Current game state with deterministic deck and player hands.
        ai_controllers: AI controllers keyed by player name (for personality traits).

    Returns:
        ReactionSchedule with reactions per phase (FLOP, TURN, RIVER).
    """
    active_ai_players = [
        p for p in game_state.players if not p.is_folded and not p.is_human and p.hand
    ]

    if len(active_ai_players) < 1:
        return ReactionSchedule()

    # Need at least 2 active players (AI or human) for equity to be meaningful
    active_players = [p for p in game_state.players if not p.is_folded and p.hand]
    if len(active_players) < 2:
        return ReactionSchedule()

    # Build hands dict for all active players (need all for equity calc)
    players_hands = {}
    for p in active_players:
        try:
            players_hands[p.name] = [card_to_string(c) for c in p.hand]
        except Exception as e:
            logger.warning(f"[RunOut] Failed to convert cards for {p.name}: {e}")
            return ReactionSchedule()

    # Current community cards
    current_board = [card_to_string(c) for c in game_state.community_cards]
    remaining_deck = list(game_state.deck)

    # Determine which streets remain to be dealt
    streets = _remaining_streets(len(current_board), remaining_deck)
    if not streets:
        return ReactionSchedule()

    calculator = EquityCalculator(monte_carlo_iterations=EQUITY_ITERATIONS)

    # Calculate starting equity
    prev_equities = _safe_calculate_equity(calculator, players_hands, current_board)
    if prev_equities is None:
        return ReactionSchedule()

    # Get personality thresholds for AI players
    thresholds = {
        p.name: _get_reaction_threshold(p.name, ai_controllers) for p in active_ai_players
    }

    schedule = ReactionSchedule()

    # Initial reactions: based on absolute equity when hole cards are revealed
    initial_reactions = []
    for player in active_ai_players:
        name = player.name
        if name not in prev_equities:
            continue
        equity = prev_equities[name]
        emotion = _equity_to_initial_emotion(equity)
        if emotion:
            initial_reactions.append(
                PlayerReaction(
                    player_name=name,
                    emotion=emotion,
                    equity_before=equity,
                    equity_after=equity,
                    delta=0.0,
                )
            )
            logger.info(f"[RunOut] {name} initial reaction: {emotion} (equity {equity:.0%})")
    if initial_reactions:
        schedule.reactions_by_phase['INITIAL'] = initial_reactions
    # The hole-card reveal is always the first step on the timeline, even with no
    # reaction — the director needs it as the matchup beat. (A per-*card* hole
    # reaction isn't poker-meaningful; you need both cards to read the matchup.)
    schedule.steps.append(RunoutStep(phase='INITIAL', card_index=0, reactions=initial_reactions))

    board_so_far = list(current_board)

    for phase_name, new_cards in streets:
        # Walk this street card-by-card so each card gets its own reaction step
        # (the per-card payoff over street granularity). Equity is computed after
        # each card added to the board; eval7 handles 1- and 2-card boards fine,
        # just at higher variance. `street_start_equities` anchors the legacy
        # whole-street reaction (initial-of-street → end-of-street), unchanged.
        street_start_equities = prev_equities
        per_card_prev = prev_equities

        for card_index, card in enumerate(new_cards):
            board_so_far = board_so_far + [card_to_string(card)]

            current_equities = _safe_calculate_equity(calculator, players_hands, board_so_far)
            if current_equities is None:
                continue

            step_reactions = []
            for player in active_ai_players:
                name = player.name
                if name not in per_card_prev or name not in current_equities:
                    continue

                before = per_card_prev[name]
                after = current_equities[name]
                delta = after - before

                if abs(delta) >= thresholds[name]:
                    emotion = _equity_to_emotion(delta, after)
                    step_reactions.append(
                        PlayerReaction(
                            player_name=name,
                            emotion=emotion,
                            equity_before=before,
                            equity_after=after,
                            delta=delta,
                        )
                    )
                    logger.info(
                        f"[RunOut] {name} reaction at {phase_name}[{card_index}]: {emotion} "
                        f"(equity {before:.0%} → {after:.0%}, Δ{delta:+.0%})"
                    )

            schedule.steps.append(
                RunoutStep(phase=phase_name, card_index=card_index, reactions=step_reactions)
            )
            per_card_prev = current_equities

        # Legacy street-granular reaction: the start→end-of-street delta, so the
        # backend per-street emit and the desktop path are byte-for-byte unchanged.
        street_end_equities = per_card_prev
        street_reactions = []
        for player in active_ai_players:
            name = player.name
            if name not in street_start_equities or name not in street_end_equities:
                continue

            before = street_start_equities[name]
            after = street_end_equities[name]
            delta = after - before

            if abs(delta) >= thresholds[name]:
                emotion = _equity_to_emotion(delta, after)
                street_reactions.append(
                    PlayerReaction(
                        player_name=name,
                        emotion=emotion,
                        equity_before=before,
                        equity_after=after,
                        delta=delta,
                    )
                )

        if street_reactions:
            schedule.reactions_by_phase[phase_name] = street_reactions

        prev_equities = street_end_equities

    # Showdown reactions: based on final equity after all cards are dealt
    # With all 5 community cards out, equity is ~1.0 (winner) or ~0.0 (loser)
    showdown_reactions = []
    for player in active_ai_players:
        name = player.name
        if name not in prev_equities:
            continue
        final_equity = prev_equities[name]
        emotion = _equity_to_showdown_emotion(final_equity)
        if emotion:
            showdown_reactions.append(
                PlayerReaction(
                    player_name=name,
                    emotion=emotion,
                    equity_before=final_equity,
                    equity_after=final_equity,
                    delta=0.0,
                )
            )
            logger.info(
                f"[RunOut] {name} showdown reaction: {emotion} "
                f"(final equity {final_equity:.0%})"
            )
    if showdown_reactions:
        schedule.reactions_by_phase['SHOWDOWN'] = showdown_reactions
    schedule.steps.append(RunoutStep(phase='SHOWDOWN', card_index=0, reactions=showdown_reactions))

    return schedule


def runout_schedule_payload(schedule: ReactionSchedule) -> dict:
    """Serialize a schedule's per-card steps for the ``runout_schedule`` socket event.

    Carries **reactions + timing structure only** — the ordered steps and, per
    step, the AI faces this card triggers. Deliberately omits every board card so
    no future-street card ever reaches the client ahead of its reveal (the
    director sources faces from the per-street game-state push it already gets).
    """
    return {
        'steps': [
            {
                'phase': step.phase,
                'card_index': step.card_index,
                'reactions': [
                    {'player_name': r.player_name, 'emotion': r.emotion} for r in step.reactions
                ],
            }
            for step in schedule.steps
        ],
    }


def _remaining_streets(
    board_count: int,
    remaining_deck: list,
) -> List[tuple]:
    """Determine which streets remain and what cards will be dealt.

    In Texas Hold'em, board_count is always 0, 3, 4, or 5.
    Cards are drawn sequentially from the top of the remaining deck.

    Returns list of (phase_name, cards) tuples.
    """
    # Map board state to the sequence of streets still to come
    street_sequence = {
        0: [('FLOP', 3), ('TURN', 1), ('RIVER', 1)],
        3: [('TURN', 1), ('RIVER', 1)],
        4: [('RIVER', 1)],
        5: [],  # Board complete, no streets remain
    }

    plan = street_sequence.get(board_count, [])
    streets = []
    deck_idx = 0

    for phase_name, num_cards in plan:
        if len(remaining_deck) < deck_idx + num_cards:
            break
        streets.append((phase_name, remaining_deck[deck_idx : deck_idx + num_cards]))
        deck_idx += num_cards

    return streets


def _safe_calculate_equity(
    calculator: EquityCalculator,
    players_hands: Dict[str, list],
    board: list,
) -> Optional[Dict[str, float]]:
    """Calculate equity with error handling. Returns None on failure."""
    try:
        result = calculator.calculate_equity(players_hands, board)
        if result is None:
            logger.warning("[RunOut] Equity calculator returned None (eval7 unavailable?)")
            return None
        return result.equities
    except Exception as e:
        logger.error(f"[RunOut] Equity calculation failed: {e}")
        return None


def _get_reaction_threshold(
    player_name: str,
    ai_controllers: Dict[str, 'AIPlayerController'],
) -> float:
    """Get personality-modified reaction threshold.

    Volatile personalities (high aggression or low tightness) react to
    smaller equity swings. Stoic personalities need larger swings.

    Supports both new 5-trait model (tightness) and old 4-trait model (bluff_tendency).
    """
    if player_name not in ai_controllers:
        return BASE_REACTION_THRESHOLD

    controller = ai_controllers[player_name]
    traits = controller.personality_traits
    if not traits:
        return BASE_REACTION_THRESHOLD

    aggression = traits.get('aggression', 0.5)
    # Support both new (tightness) and old (bluff_tendency) models
    # Low tightness = loose = volatile; high bluff_tendency = volatile
    tightness = traits.get('tightness')
    if tightness is not None:
        looseness = 1.0 - tightness
    else:
        looseness = traits.get('bluff_tendency', 0.5)

    # Volatile: high aggression or loose player → lower threshold (react more)
    if aggression > HIGH_TRAIT or looseness > HIGH_TRAIT:
        return BASE_REACTION_THRESHOLD + REACTIVE_THRESHOLD_OFFSET

    # Stoic: low aggression and tight player → higher threshold (react less)
    if aggression < LOW_TRAIT and looseness < LOW_TRAIT:
        return BASE_REACTION_THRESHOLD + STOIC_THRESHOLD_OFFSET

    return BASE_REACTION_THRESHOLD


def _equity_to_emotion(delta: float, equity_after: float) -> str:
    """Map an equity change and resulting position to an avatar emotion.

    Priority order ensures the most dramatic emotions take precedence.

    Returns one of: elated, angry, happy, frustrated, smug, confident,
                    nervous, thinking, poker_face
    """
    # Huge positive swing → excitement
    if delta > 0.30:
        return "elated"

    # Huge negative swing → fury
    if delta < -0.30:
        return "angry"

    # Notable positive swing
    if delta > 0.18:
        return "happy"

    # Notable negative swing → simmering frustration
    if delta < -0.18:
        return "frustrated"

    # Assess final position after smaller swings
    if equity_after >= 0.75:
        return "smug"

    if equity_after >= 0.60:
        return "confident"

    if equity_after <= 0.25:
        return "nervous"

    if equity_after <= 0.40:
        return "thinking"

    return "poker_face"


def _equity_to_initial_emotion(equity: float) -> Optional[str]:
    """Map absolute equity to an avatar emotion for the initial reveal.

    Returns None for mid-range equity (no strong reaction).
    Only players with clearly strong or weak positions react.

    Returns one of: smug, confident, happy, nervous, thinking, or None
    """
    if equity >= 0.80:
        return "smug"

    if equity >= 0.65:
        return "confident"

    if equity >= 0.50:
        return "happy"

    if equity <= 0.20:
        return "nervous"

    if equity <= 0.35:
        return "thinking"

    # Mid-range equity (35-50%) — no obvious reaction
    return None


def _equity_to_showdown_emotion(equity: float) -> str:
    """Map final equity to a showdown emotion after all cards are dealt.

    With all 5 community cards out, equity is effectively 1.0 (winner),
    0.0 (loser), or somewhere in between for splits.

    Returns one of: elated, happy, angry, frustrated, poker_face
    """
    if equity >= 0.90:
        return "elated"

    if equity >= 0.50:
        return "happy"

    if equity <= 0.10:
        return "angry"

    if equity < 0.50:
        return "frustrated"

    return "poker_face"

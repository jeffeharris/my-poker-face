"""
Decision Analyzer for AI Player Quality Monitoring (with psychology tracking).

Analyzes AI decisions inline (called after every decision) to track
decision quality and difficulty metrics without storing full prompts.
"""

import json
import time
import logging
from dataclasses import dataclass, asdict
from typing import Optional, List, Any, Tuple

logger = logging.getLogger(__name__)

# Import equity calculator - gracefully degrade if not available
try:
    from poker.equity_calculator import EquityCalculator, EVAL7_AVAILABLE
except ImportError:
    EVAL7_AVAILABLE = False
    EquityCalculator = None

from poker.card_utils import normalize_card_string


def calculate_max_winnable(
    player_bet: int,
    player_stack: int,
    cost_to_call: int,
    all_players_bets: List[Tuple[int, bool]],
) -> int:
    """Calculate max amount player can win, accounting for side pot limits.

    When a short-stacked player goes all-in, they can only win a portion of
    the pot proportional to their contribution. This function calculates
    the "main pot" amount the player is eligible to win.

    Args:
        player_bet: Player's current round bet
        player_stack: Player's remaining stack
        cost_to_call: Amount needed to call (0 if can check)
        all_players_bets: List of (bet, is_folded) tuples for ALL players
                         including the current player

    Returns:
        Maximum amount player can win from the pot

    Example:
        Player has 100 chips, opponent bet 500, pot = 600
        - player_bet=0, player_stack=100, cost_to_call=500
        - all_players_bets=[(0, False), (500, False)]  # hero, villain
        - Player can call 100 (all-in), contribution = 100
        - Opponent's matched contribution = 100
        - max_winnable = 100 + 100 = 200 (not 600!)
    """
    # Player's total contribution if they call (capped by their stack)
    effective_call = min(cost_to_call, player_stack)
    player_contribution = player_bet + effective_call

    # Start with hero's contribution (the money they're putting in)
    # Hero's existing bet is already in all_players_bets, so we start
    # with just the new effective_call amount
    max_winnable = effective_call

    # Add matched contributions from other players
    # For each player (including hero), add min(bet, player_contribution)
    # Hero's bet will add back their existing bet, and other players'
    # bets are capped at hero's contribution level
    for bet, is_folded in all_players_bets:
        # All players' bets (including folded players' dead money) are
        # added to the pot hero is playing for, capped at hero's contribution
        max_winnable += min(bet, player_contribution)

    return max_winnable


@dataclass
class DecisionAnalysis:
    """Analysis result for a single AI decision."""

    # Identity
    game_id: str
    player_name: str
    hand_number: Optional[int] = None
    phase: Optional[str] = None
    player_position: Optional[str] = None  # Hero's table position (button, UTG, etc.)
    request_id: Optional[str] = None
    capture_id: Optional[int] = None

    # Game state
    pot_total: int = 0
    cost_to_call: int = 0
    player_stack: int = 0
    num_opponents: int = 1
    player_hand: Optional[str] = None  # JSON string
    community_cards: Optional[str] = None  # JSON string

    # Decision
    action_taken: Optional[str] = None
    raise_amount: Optional[int] = None
    raise_amount_bb: Optional[float] = None  # BB amount when BB mode is active

    # Equity analysis
    equity: Optional[float] = None
    required_equity: float = 0
    ev_call: Optional[float] = None
    max_winnable: Optional[int] = None  # Max pot player can win (side pot aware)

    # Quality
    optimal_action: Optional[str] = None
    decision_quality: str = "unknown"
    ev_lost: float = 0

    # Hand strength
    hand_rank: Optional[int] = None
    relative_strength: Optional[float] = None

    # Position-based equity (alternative to random)
    equity_vs_ranges: Optional[float] = None  # Equity against position-based ranges
    opponent_positions: Optional[str] = None  # JSON list of opponent positions

    # Psychology snapshot (emotional state at decision time)
    tilt_level: Optional[float] = None
    tilt_source: Optional[str] = None
    valence: Optional[float] = None
    arousal: Optional[float] = None
    control: Optional[float] = None
    focus: Optional[float] = None
    display_emotion: Optional[str] = None
    elastic_aggression: Optional[float] = None
    elastic_bluff_tendency: Optional[float] = None  # Legacy - kept for historical data
    # New 5-trait poker-native model (v70)
    elastic_tightness: Optional[float] = None
    elastic_confidence: Optional[float] = None
    elastic_composure: Optional[float] = None
    elastic_table_talk: Optional[float] = None

    # Range tracking (v67)
    opponent_ranges_json: Optional[str] = None    # {"Batman": ["AA", "AKs", ...], ...}
    board_texture_json: Optional[str] = None      # Board texture dict from analyze_board_texture()
    player_hand_canonical: Optional[str] = None   # "AKo", "Q7o", etc.
    player_hand_in_range: Optional[bool] = None   # Is hand in standard range for position?
    player_hand_tier: Optional[str] = None        # "premium", "strong", ..., "trash"
    standard_range_pct: Optional[float] = None    # Expected range % for position (e.g., 15)

    # Zone detection snapshot (v71)
    zone_confidence: Optional[float] = None       # Confidence value at decision time
    zone_composure: Optional[float] = None        # Composure value at decision time
    zone_energy: Optional[float] = None           # Energy value at decision time
    zone_manifestation: Optional[str] = None      # 'low_energy', 'balanced', 'high_energy'
    zone_sweet_spots_json: Optional[str] = None   # JSON: {"poker_face": 0.8, ...}
    zone_penalties_json: Optional[str] = None     # JSON: {"tilted": 0.3, ...}
    zone_primary_sweet_spot: Optional[str] = None # Dominant sweet spot zone
    zone_primary_penalty: Optional[str] = None    # Dominant penalty zone
    zone_total_penalty_strength: Optional[float] = None  # Sum of penalty intensities
    zone_in_neutral_territory: Optional[bool] = None     # Not in any zone

    # Zone effects tracking (v71)
    zone_intrusive_thoughts_injected: Optional[bool] = None   # Were thoughts added?
    zone_intrusive_thoughts_json: Optional[str] = None        # JSON list of injected thoughts
    zone_penalty_strategy_applied: Optional[str] = None       # Bad advice string if added
    zone_info_degraded: Optional[bool] = None                 # Was strategic info removed?
    zone_strategy_selected: Optional[str] = None              # Sweet spot strategy template key

    # Metadata
    analyzer_version: str = "1.0"
    processing_time_ms: Optional[int] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for persistence."""
        return asdict(self)


class DecisionAnalyzer:
    """
    Analyzes AI decisions inline (called after every decision).

    Usage:
        analyzer = DecisionAnalyzer()
        analysis = analyzer.analyze(
            game_id="game_123",
            player_name="Batman",
            hand_number=5,
            phase="FLOP",
            player_hand=["As", "Kd"],
            community_cards=["Jh", "2d", "5s"],
            pot_total=100,
            cost_to_call=20,
            player_stack=500,
            num_opponents=2,
            action_taken="call",
        )
        # analysis.decision_quality = "correct" or "mistake"
        # analysis.ev_lost = 0 if correct, else EV difference
    """

    VERSION = "1.0"

    def __init__(self, iterations: int = 2000):
        """
        Initialize the analyzer.

        Args:
            iterations: Monte Carlo iterations for equity calculation.
                       Lower = faster but less accurate. 2000 ≈ 20ms.
        """
        self.iterations = iterations
        self._calculator = None

    @property
    def calculator(self):
        """Lazy-load the equity calculator."""
        if self._calculator is None and EVAL7_AVAILABLE:
            self._calculator = EquityCalculator(self.iterations)
        return self._calculator

    def analyze(
        self,
        game_id: str,
        player_name: str,
        hand_number: Optional[int],
        phase: Optional[str],
        player_hand: List[str],
        community_cards: List[str],
        pot_total: int,
        cost_to_call: int,
        player_stack: int,
        num_opponents: int,
        action_taken: str,
        raise_amount: Optional[int] = None,
        raise_amount_bb: Optional[float] = None,
        request_id: Optional[str] = None,
        capture_id: Optional[int] = None,
        player_position: Optional[str] = None,
        opponent_positions: Optional[List[str]] = None,
        opponent_infos: Optional[List[Any]] = None,
        player_bet: int = 0,
        all_players_bets: Optional[List[Tuple[int, bool]]] = None,
        psychology_snapshot: Optional[dict] = None,
    ) -> DecisionAnalysis:
        """
        Analyze a decision and return analysis result.

        Args:
            game_id: Game identifier
            player_name: AI player name
            hand_number: Current hand number
            phase: Game phase (PRE_FLOP, FLOP, TURN, RIVER)
            player_hand: List of hole cards as strings (e.g., ["As", "Kd"])
            community_cards: List of community cards as strings
            pot_total: Total pot size
            cost_to_call: Amount needed to call
            player_stack: Player's remaining chips
            num_opponents: Number of opponents still in hand
            action_taken: The action the AI chose
            raise_amount: Amount raised in dollars (if action is raise)
            raise_amount_bb: Amount raised in BB (if BB mode active)
            request_id: Link to api_usage table
            capture_id: Link to prompt_captures table
            player_position: Hero's table position (e.g., 'button', 'under_the_gun')
            opponent_positions: List of opponent position names for range-based equity
                               (e.g., ['button', 'big_blind_player']) - backward compat
            opponent_infos: List of OpponentInfo objects with observed stats and
                           personality data for more accurate range estimation
            player_bet: Player's current round bet (for max_winnable calculation)
            all_players_bets: List of (bet, is_folded) tuples for ALL players
                             to calculate stack-aware EV (for short stack scenarios)

        Returns:
            DecisionAnalysis with equity and quality assessment
        """
        start_time = time.time()

        analysis = DecisionAnalysis(
            game_id=game_id,
            player_name=player_name,
            hand_number=hand_number,
            phase=phase,
            player_position=player_position,
            request_id=request_id,
            capture_id=capture_id,
            pot_total=pot_total,
            cost_to_call=cost_to_call,
            player_stack=player_stack,
            num_opponents=num_opponents,
            player_hand=json.dumps(player_hand) if player_hand else None,
            community_cards=json.dumps(community_cards) if community_cards else None,
            action_taken=action_taken,
            raise_amount=raise_amount,
            raise_amount_bb=raise_amount_bb,
            analyzer_version=self.VERSION,
        )

        # Apply psychology snapshot if provided
        if psychology_snapshot:
            analysis.tilt_level = psychology_snapshot.get('tilt_level')
            analysis.tilt_source = psychology_snapshot.get('tilt_source')
            analysis.valence = psychology_snapshot.get('valence')
            analysis.arousal = psychology_snapshot.get('arousal')
            analysis.control = psychology_snapshot.get('control')
            analysis.focus = psychology_snapshot.get('focus')
            analysis.display_emotion = psychology_snapshot.get('display_emotion')
            analysis.elastic_aggression = psychology_snapshot.get('elastic_aggression')
            analysis.elastic_bluff_tendency = psychology_snapshot.get('elastic_bluff_tendency')
            # New 5-trait model
            analysis.elastic_tightness = psychology_snapshot.get('elastic_tightness')
            analysis.elastic_confidence = psychology_snapshot.get('elastic_confidence')
            analysis.elastic_composure = psychology_snapshot.get('elastic_composure')
            analysis.elastic_table_talk = psychology_snapshot.get('elastic_table_talk')

            # Zone detection snapshot (Phase 10)
            analysis.zone_confidence = psychology_snapshot.get('zone_confidence')
            analysis.zone_composure = psychology_snapshot.get('zone_composure')
            analysis.zone_energy = psychology_snapshot.get('zone_energy')
            analysis.zone_manifestation = psychology_snapshot.get('zone_manifestation')
            analysis.zone_sweet_spots_json = psychology_snapshot.get('zone_sweet_spots_json')
            analysis.zone_penalties_json = psychology_snapshot.get('zone_penalties_json')
            analysis.zone_primary_sweet_spot = psychology_snapshot.get('zone_primary_sweet_spot')
            analysis.zone_primary_penalty = psychology_snapshot.get('zone_primary_penalty')
            analysis.zone_total_penalty_strength = psychology_snapshot.get('zone_total_penalty_strength')
            analysis.zone_in_neutral_territory = psychology_snapshot.get('zone_in_neutral_territory')

            # Zone effects instrumentation (Phase 10)
            analysis.zone_intrusive_thoughts_injected = psychology_snapshot.get('zone_intrusive_thoughts_injected')
            analysis.zone_intrusive_thoughts_json = psychology_snapshot.get('zone_intrusive_thoughts_json')
            analysis.zone_penalty_strategy_applied = psychology_snapshot.get('zone_penalty_strategy_applied')
            analysis.zone_info_degraded = psychology_snapshot.get('zone_info_degraded')
            analysis.zone_strategy_selected = psychology_snapshot.get('zone_strategy_selected')

        # Calculate hand strength if we have cards
        if player_hand and community_cards:
            try:
                import eval7
                hero_hand = [eval7.Card(normalize_card_string(c)) for c in player_hand]
                board = [eval7.Card(normalize_card_string(c)) for c in community_cards]
                # eval7.evaluate returns higher scores for better hands
                analysis.hand_rank = eval7.evaluate(hero_hand + board)
                # Convert to relative strength (0-100 percentile)
                # eval7 scores range from ~0 to ~7462 (royal flush)
                # Higher = better, so we calculate percentile directly
                analysis.relative_strength = min(100, (analysis.hand_rank / 7462) * 100)
            except Exception as e:
                logger.debug(f"Hand strength calculation failed: {e}")

        # Calculate equity if we have cards and calculator
        if player_hand and self.calculator and num_opponents > 0:
            try:
                # Calculate equity vs random opponent hands using Monte Carlo
                analysis.equity = self.calculate_equity_vs_random(
                    player_hand, community_cards or [], num_opponents
                )
            except Exception as e:
                logger.debug(f"Equity calculation failed: {e}")

            # Calculate equity vs ranges - prefer opponent_infos (with stats) over positions
            range_data = opponent_infos if opponent_infos else opponent_positions
            if range_data:
                try:
                    analysis.equity_vs_ranges = self.calculate_equity_vs_ranges(
                        player_hand, community_cards or [], range_data
                    )
                    # Store opponent positions for reference
                    if opponent_positions:
                        analysis.opponent_positions = json.dumps(opponent_positions)
                    elif opponent_infos:
                        # Extract positions from opponent_infos
                        positions = [getattr(o, 'position', 'unknown') for o in opponent_infos]
                        analysis.opponent_positions = json.dumps(positions)

                    # Capture opponent ranges for timeline tracking
                    if opponent_infos:
                        try:
                            from .hand_ranges import get_opponent_range, EquityConfig
                            config = EquityConfig()
                            opponent_ranges = {}
                            for opp_info in opponent_infos:
                                opp_range = get_opponent_range(opp_info, config)
                                # Store sorted list of canonical hands
                                opponent_ranges[opp_info.name] = sorted(list(opp_range))
                            analysis.opponent_ranges_json = json.dumps(opponent_ranges)
                        except Exception as e:
                            logger.warning(f"Opponent range capture failed: {e}")
                except Exception as e:
                    logger.debug(f"Equity vs ranges calculation failed: {e}")

        # Board texture analysis
        if community_cards:
            try:
                from .board_analyzer import analyze_board_texture
                board_texture = analyze_board_texture(community_cards)
                analysis.board_texture_json = json.dumps(board_texture)
            except Exception as e:
                logger.warning(f"Board texture analysis failed: {e}")

        # Player hand range analysis (is this hand in standard range for position?)
        if player_hand and len(player_hand) == 2 and player_position:
            try:
                from .hand_ranges import is_hand_in_standard_range
                range_analysis = is_hand_in_standard_range(
                    player_hand[0], player_hand[1], player_position
                )
                analysis.player_hand_canonical = range_analysis.get('canonical_hand')
                analysis.player_hand_in_range = range_analysis.get('in_range')
                analysis.player_hand_tier = range_analysis.get('hand_tier')
                analysis.standard_range_pct = range_analysis.get('range_size_pct')
            except Exception as e:
                logger.warning(f"Player hand range analysis failed: {e}")

        # Calculate max winnable considering side pots (for short stacks)
        if all_players_bets is not None:
            analysis.max_winnable = calculate_max_winnable(
                player_bet, player_stack, cost_to_call, all_players_bets
            )

        # Calculate required equity and EV
        if cost_to_call > 0 and pot_total > 0:
            analysis.required_equity = cost_to_call / (pot_total + cost_to_call)
            if analysis.equity is not None:
                # Use max_winnable for accurate short-stack EV calculation
                # Falls back to pot_total when max_winnable isn't calculated
                winnable_pot = analysis.max_winnable if analysis.max_winnable is not None else pot_total
                # Cap winnable at pot_total (max_winnable can't exceed actual pot)
                winnable_pot = min(winnable_pot, pot_total)
                # EV(call) = (equity * winnable_pot) - ((1-equity) * call_cost)
                # Note: cost_to_call is already capped at player_stack by caller
                effective_call = min(cost_to_call, player_stack)
                analysis.ev_call = (analysis.equity * winnable_pot) - (
                    (1 - analysis.equity) * effective_call
                )
        else:
            # Free check - no cost to see more cards
            analysis.required_equity = 0
            analysis.ev_call = 0

        # Evaluate decision quality
        self._evaluate_quality(analysis)

        analysis.processing_time_ms = int((time.time() - start_time) * 1000)
        return analysis

    def calculate_equity_vs_random(
        self,
        player_hand: List[str],
        community_cards: List[str],
        num_opponents: int
    ) -> Optional[float]:
        """Calculate equity vs random opponent hands using Monte Carlo.

        Args:
            player_hand: Hero's hole cards as strings ['Ah', 'Kd']
            community_cards: Board cards as strings
            num_opponents: Number of opponents to simulate

        Returns:
            Win probability (0.0-1.0) or None if calculation fails
        """
        try:
            import eval7
            import random

            # Parse hero's hand
            hero_hand = [eval7.Card(normalize_card_string(c)) for c in player_hand]
            board = [eval7.Card(normalize_card_string(c)) for c in community_cards] if community_cards else []

            # Build deck excluding known cards
            all_known = set(hero_hand + board)
            deck = [c for c in eval7.Deck().cards if c not in all_known]

            wins = 0
            iterations = self.iterations

            for _ in range(iterations):
                # Shuffle remaining deck
                random.shuffle(deck)
                deck_idx = 0

                # Deal random hands to opponents
                opponent_hands = []
                for _ in range(num_opponents):
                    opp_hand = [deck[deck_idx], deck[deck_idx + 1]]
                    opponent_hands.append(opp_hand)
                    deck_idx += 2

                # Deal remaining board cards
                cards_needed = 5 - len(board)
                sim_board = board + deck[deck_idx:deck_idx + cards_needed]

                # Evaluate hands
                hero_score = eval7.evaluate(hero_hand + sim_board)

                # Check if hero beats all opponents
                hero_wins = True
                for opp_hand in opponent_hands:
                    opp_score = eval7.evaluate(opp_hand + sim_board)
                    if opp_score > hero_score:  # Higher is better in eval7
                        hero_wins = False
                        break

                if hero_wins:
                    wins += 1

            return wins / iterations

        except Exception as e:
            logger.debug(f"Equity vs random calculation failed: {e}")
            return None

    def calculate_equity_vs_ranges(
        self,
        player_hand: List[str],
        community_cards: List[str],
        opponent_infos: List[Any]  # List of OpponentInfo or position strings
    ) -> Optional[float]:
        """Calculate equity vs opponent hand ranges using fallback hierarchy.

        Uses the following priority for range estimation:
        1. In-game observed stats (if enough hands observed)
        2. Personality traits (for AI players)
        3. Position-based static ranges (fallback)

        Args:
            player_hand: Hero's hole cards as strings ['Ah', 'Kd']
            community_cards: Board cards as strings
            opponent_infos: List of OpponentInfo objects or position strings
                           (position strings are converted to basic OpponentInfo)

        Returns:
            Win probability (0.0-1.0) or None if calculation fails
        """
        try:
            import eval7
            import random
            from .hand_ranges import (
                sample_hands_for_opponent_infos,
                sample_hands_for_opponents,
                OpponentInfo,
                EquityConfig,
            )

            # Parse hero's hand
            hero_hand = [eval7.Card(normalize_card_string(c)) for c in player_hand]
            board = [eval7.Card(normalize_card_string(c)) for c in community_cards] if community_cards else []

            # Build set of excluded cards (hero's hand + board)
            excluded_cards = set(player_hand + (community_cards or []))

            # Build deck excluding known cards
            all_known = set(hero_hand + board)
            deck = [c for c in eval7.Deck().cards if c not in all_known]

            wins = 0
            iterations = self.iterations
            rng = random.Random()
            config = EquityConfig()

            # Check if we have OpponentInfo objects or just position strings
            use_opponent_infos = (
                opponent_infos and
                len(opponent_infos) > 0 and
                hasattr(opponent_infos[0], 'name')
            )

            for _ in range(iterations):
                # Sample opponent hands using appropriate method (with board-connection weighting)
                if use_opponent_infos:
                    opponent_hands_raw = sample_hands_for_opponent_infos(
                        opponent_infos, excluded_cards, config, rng, community_cards
                    )
                else:
                    # Backward compatibility: treat as position strings
                    opponent_hands_raw = sample_hands_for_opponents(
                        opponent_infos, excluded_cards, rng
                    )

                # Skip iteration if we couldn't sample valid hands
                if None in opponent_hands_raw:
                    continue

                # Convert to eval7 cards
                opponent_hands = []
                opp_cards_set = set()
                for hand in opponent_hands_raw:
                    opp_hand = [eval7.Card(normalize_card_string(hand[0])), eval7.Card(normalize_card_string(hand[1]))]
                    opponent_hands.append(opp_hand)
                    opp_cards_set.add(opp_hand[0])
                    opp_cards_set.add(opp_hand[1])

                # Build deck excluding all known cards for this iteration
                iter_deck = [c for c in deck if c not in opp_cards_set]
                rng.shuffle(iter_deck)

                # Deal remaining board cards
                cards_needed = 5 - len(board)
                sim_board = board + iter_deck[:cards_needed]

                # Evaluate hands
                hero_score = eval7.evaluate(hero_hand + sim_board)

                # Check if hero beats all opponents
                hero_wins = True
                for opp_hand in opponent_hands:
                    opp_score = eval7.evaluate(opp_hand + sim_board)
                    if opp_score > hero_score:  # Higher is better in eval7
                        hero_wins = False
                        break

                if hero_wins:
                    wins += 1

            return wins / iterations if iterations > 0 else None

        except Exception as e:
            logger.debug(f"Equity vs ranges calculation failed: {e}")
            return None

    def _evaluate_quality(self, analysis: DecisionAnalysis) -> None:
        """
        Evaluate decision quality based on EV and optimal action.

        Sets optimal_action, decision_quality, and ev_lost on the analysis.

        Optimal action is determined by:
        - Fold: EV(call) < 0
        - Call: EV(call) > 0 but equity not high enough to raise for value
        - Raise: High equity where raising extracts more value

        Decision quality considers:
        - "correct": Action matches or is close to optimal
        - "marginal": Action is defensible but not optimal
        - "mistake": Clear error (e.g., folding +EV, calling -EV)
        """
        if analysis.ev_call is None:
            analysis.decision_quality = "unknown"
            return

        # Special case: folding when you can check for free is always a mistake
        # You're giving up your equity share of the pot for no reason
        if analysis.cost_to_call == 0 and analysis.action_taken == "fold":
            analysis.optimal_action = "check"
            analysis.decision_quality = "mistake"
            if analysis.equity is not None and analysis.pot_total > 0:
                # EV lost = your equity share of the pot you're abandoning
                analysis.ev_lost = analysis.equity * analysis.pot_total
            else:
                analysis.ev_lost = 0  # Can't calculate without equity
            return

        equity = analysis.equity or 0
        num_opponents = analysis.num_opponents or 1
        phase = analysis.phase

        # Determine optimal action using sophisticated logic
        analysis.optimal_action = self.determine_optimal_action(
            equity=equity,
            ev_call=analysis.ev_call,
            required_equity=analysis.required_equity,
            num_opponents=num_opponents,
            phase=phase,
            pot_total=analysis.pot_total or 0,
            cost_to_call=analysis.cost_to_call or 0,
            player_stack=analysis.player_stack or 0,
            player_position=analysis.player_position,
        )

        # Evaluate decision quality
        action = analysis.action_taken
        optimal = analysis.optimal_action

        if action == optimal:
            analysis.decision_quality = "correct"
            analysis.ev_lost = 0
        elif action == "fold" and analysis.ev_call > 0:
            # Folded a +EV spot - clear mistake
            analysis.decision_quality = "mistake"
            analysis.ev_lost = analysis.ev_call
        elif action in ("call", "raise", "all_in") and analysis.ev_call < 0:
            # Called/raised a -EV spot - clear mistake
            analysis.decision_quality = "mistake"
            analysis.ev_lost = -analysis.ev_call
        elif action == "call" and optimal == "raise":
            # Called when should have raised - marginal (still +EV)
            analysis.decision_quality = "marginal"
            analysis.ev_lost = 0  # Didn't lose EV, just didn't maximize
        elif action == "raise" and optimal == "call":
            # Raised when calling was optimal - marginal (still +EV)
            analysis.decision_quality = "marginal"
            analysis.ev_lost = 0
        elif action == "check" and optimal == "raise":
            # Checked when should have bet - marginal (missed value)
            analysis.decision_quality = "marginal"
            analysis.ev_lost = 0
        elif action == "raise" and optimal == "check":
            # Bet when should have checked - marginal (built pot unnecessarily)
            analysis.decision_quality = "marginal"
            analysis.ev_lost = 0
        else:
            analysis.decision_quality = "correct"
            analysis.ev_lost = 0

    def _get_position_adjustment(self, player_position: Optional[str]) -> float:
        """
        Get equity threshold adjustment based on table position.

        Position is one of the most important factors in poker strategy.
        Acting last (late position) provides information advantage, allowing
        looser play. Acting first (early position) requires tighter ranges.

        Args:
            player_position: Position name (e.g., 'button', 'under_the_gun')

        Returns:
            Adjustment to equity thresholds:
            - Positive = need MORE equity (tighter)
            - Negative = need LESS equity (looser)
        """
        if not player_position:
            return 0.0

        # Import here to avoid circular imports
        from .hand_ranges import get_position_group, Position

        position_group = get_position_group(player_position)

        # Position adjustments based on standard poker theory
        # Early position: information disadvantage, need stronger hands
        # Late position: information advantage, can play more hands
        adjustments = {
            Position.EARLY: 0.08,    # Need 8% more equity from UTG
            Position.MIDDLE: 0.03,   # Need 3% more equity from middle
            Position.LATE: -0.05,    # Can play 5% looser from button/cutoff
            Position.BLIND: -0.03,   # Already invested chips, slightly looser
        }

        return adjustments.get(position_group, 0.0)

    def determine_optimal_action(
        self,
        equity: float,
        ev_call: float,
        required_equity: float,
        num_opponents: int,
        phase: Optional[str],
        pot_total: int,
        cost_to_call: int,
        player_stack: int,
        player_position: Optional[str] = None,
    ) -> str:
        """
        Determine the optimal action based on game theory considerations.

        Args:
            equity: Win probability (0-1)
            ev_call: Expected value of calling
            required_equity: Minimum equity needed to call profitably
            num_opponents: Number of opponents in the hand
            phase: Game phase (PRE_FLOP, FLOP, TURN, RIVER)
            pot_total: Current pot size
            cost_to_call: Amount needed to call
            player_stack: Player's remaining chips
            player_position: Hero's table position (e.g., 'button', 'under_the_gun')

        Returns:
            Optimal action: "fold", "check", "call", or "raise"
        """
        # Check if this is a check/bet situation (no cost to call)
        can_check = cost_to_call == 0

        # Position adjustment: late position can play looser, early position tighter
        # Acting last is a significant advantage in poker
        position_adjustment = self._get_position_adjustment(player_position)

        # Calculate value raise/bet threshold based on opponents and position
        # With more opponents, need higher equity to raise for value
        # Heads-up: ~55% equity is enough to value raise
        # Multi-way: Need ~60-70% equity
        base_raise_threshold = 0.55 + position_adjustment
        opponent_adjustment = (num_opponents - 1) * 0.05  # +5% per extra opponent
        raise_threshold = min(0.75, base_raise_threshold + opponent_adjustment)

        # Adjust required equity based on position (late position can call lighter)
        adjusted_required_equity = max(0, required_equity + position_adjustment)

        # Stack-to-pot ratio affects decision
        # Deep stacks = more implied odds, can call lighter
        # Short stacks = less room to maneuver, raise or fold
        spr = player_stack / pot_total if pot_total > 0 else 10

        # Phase adjustments
        is_preflop = phase == "PRE_FLOP" if phase else False

        # If we can check (no cost to call)
        if can_check:
            # Should we bet for value or check?
            if equity >= raise_threshold:
                # Strong hand - bet for value
                return "raise"
            elif not is_preflop and equity > 0.50 and spr < 3:
                # Post-flop, good equity, short SPR - bet to deny equity
                return "raise"
            else:
                # Check - see free cards or pot control
                return "check"

        # There's a bet to call - evaluate fold/call/raise
        if ev_call < 0:
            return "fold"

        # Determine optimal action when facing a bet
        if equity >= raise_threshold:
            # Strong hand - raise for value
            return "raise"
        elif equity >= adjusted_required_equity:
            # Enough equity to continue but not to raise
            # Consider semi-bluff potential (more viable in late position)
            semi_bluff_equity_threshold = 0.45 - (position_adjustment * 0.5)  # Looser in position
            if is_preflop and equity > semi_bluff_equity_threshold and spr > 5:
                # Pre-flop with decent equity and deep stacks - can raise
                return "raise"
            elif not is_preflop and equity > 0.50 and spr < 3:
                # Post-flop, good equity, short SPR - raise to deny equity
                return "raise"
            else:
                return "call"
        else:
            # Below required equity - should fold
            # But if we have very good implied odds (deep SPR), might call
            # Late position gets more implied odds value
            implied_odds_threshold = (adjusted_required_equity * 0.7) if position_adjustment < 0 else (adjusted_required_equity * 0.8)
            if spr > 10 and equity > implied_odds_threshold:
                return "call"  # Implied odds play
            return "fold"


# Singleton instance for reuse
_analyzer_instance: Optional[DecisionAnalyzer] = None


def get_analyzer(iterations: int = 2000) -> DecisionAnalyzer:
    """Get or create the singleton analyzer instance."""
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = DecisionAnalyzer(iterations)
    return _analyzer_instance

"""
Psychology System Balance Simulator

Simulates the composure/confidence dynamics to find optimal parameters
for creating engaging variance without chaos.

Key questions:
1. What % of time should players be in each emotional band?
2. How long should tilt/confidence swings persist?
3. What recovery rate balances event frequency?
"""

import random
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
from collections import Counter
import statistics


@dataclass
class EventProbabilities:
    """
    Per-hand event probabilities for a 6-player table.

    REALISTIC MODEL:
    - ~70% of hands: Fold pre-flop (no event)
    - ~20% of hands: Play but routine (fold to bet, small loss) - minor/no event
    - ~8% of hands: Win something (positive event)
    - ~2% of hands: "Drama" event (bad beat, big win, bluff called, etc.)

    The key insight: MOST hands should have NO psychological impact.
    Only significant events should move the axes.
    """
    # Probability of ANY event happening this hand
    event_probability: float = 0.15  # Only 15% of hands have notable events

    # Given an event happens, what type? (must sum to 1.0)
    # Win events (40% of events)
    win_given_event: float = 0.30  # Small win
    big_win_given_event: float = 0.05  # Big win
    successful_bluff_given_event: float = 0.05  # Successful bluff

    # Loss events (50% of events)
    small_loss_given_event: float = 0.25  # Small loss (no composure impact)
    big_loss_given_event: float = 0.10  # Big loss
    bluff_called_given_event: float = 0.08  # Bluff called
    bad_beat_given_event: float = 0.04  # Bad beat (rare but devastating)
    got_sucked_out_given_event: float = 0.03  # Suckout against you

    # Neutral events (10% of events)
    cooler_given_event: float = 0.05  # Cooler (unavoidable, low impact)
    fold_under_pressure_given_event: float = 0.05  # Folded under pressure

    # Streak events (conditional)
    losing_streak_threshold: int = 3  # Consecutive losses to trigger


@dataclass
class AggressiveEventProbabilities:
    """
    More aggressive event model for testing - events happen more often.
    Use this to create more volatile games (for fun/testing).
    """
    event_probability: float = 0.30  # 30% of hands have events

    win_given_event: float = 0.25
    big_win_given_event: float = 0.08
    successful_bluff_given_event: float = 0.07

    small_loss_given_event: float = 0.20
    big_loss_given_event: float = 0.15
    bluff_called_given_event: float = 0.10
    bad_beat_given_event: float = 0.06
    got_sucked_out_given_event: float = 0.04

    cooler_given_event: float = 0.03
    fold_under_pressure_given_event: float = 0.02

    losing_streak_threshold: int = 3


@dataclass
class ImpactValues:
    """Base impact values for events (before sensitivity scaling)."""
    # Wins
    win: Tuple[float, float] = (0.08, 0.05)  # (confidence, composure)
    big_win: Tuple[float, float] = (0.15, 0.10)
    successful_bluff: Tuple[float, float] = (0.20, 0.05)

    # Losses
    small_loss: Tuple[float, float] = (-0.03, -0.02)  # Minor loss, minimal impact
    big_loss: Tuple[float, float] = (-0.10, -0.15)
    bluff_called: Tuple[float, float] = (-0.20, -0.10)
    bad_beat: Tuple[float, float] = (-0.05, -0.25)
    got_sucked_out: Tuple[float, float] = (-0.05, -0.30)
    losing_streak: Tuple[float, float] = (-0.15, -0.20)
    crippled: Tuple[float, float] = (-0.15, -0.15)

    # Neutral
    cooler: Tuple[float, float] = (0.0, -0.05)
    fold_under_pressure: Tuple[float, float] = (-0.05, -0.03)


@dataclass
class PlayerConfig:
    """Player personality configuration."""
    ego: float = 0.5  # Confidence sensitivity (0=stable, 1=brittle)
    poise: float = 0.7  # Composure resistance (0=volatile, 1=stable)
    recovery_rate: float = 0.15  # How fast axes return to baseline

    # Personality-specific baselines (what recovery pulls toward)
    # If None, derived from poise/ego
    baseline_confidence: Optional[float] = None
    baseline_composure: Optional[float] = None

    def get_baseline_composure(self) -> float:
        """
        Get composure baseline - simplified formula for simulation.

        Note: The main system (player_psychology.py) uses a multi-factor formula
        including poise, expressiveness, and risk_identity. This simulator uses
        a simplified 2-parameter version for quick balance testing.

        Formula: 0.45 + 0.40 * poise
        """
        if self.baseline_composure is not None:
            return self.baseline_composure
        return 0.45 + 0.40 * self.poise

    def get_baseline_confidence(self) -> float:
        """
        Get confidence baseline - simplified formula for simulation.

        Note: The main system (player_psychology.py) uses a multi-factor formula
        including aggression, risk_identity, and ego (all positive contributors).
        This simulator uses a simplified 2-parameter version with inverted ego
        for quick balance testing.

        Formula: 0.35 + 0.30 * (1 - ego)
        """
        if self.baseline_confidence is not None:
            return self.baseline_confidence
        return 0.35 + 0.30 * (1.0 - self.ego)


def derive_baselines_summary(poise: float, ego: float) -> str:
    """Show what baselines would be derived for given poise/ego."""
    baseline_comp = 0.45 + 0.40 * poise
    baseline_conf = 0.35 + 0.30 * (1.0 - ego)
    return f"baseline_comp={baseline_comp:.2f}, baseline_conf={baseline_conf:.2f}"


# Personality Archetypes for testing (with derived baselines)
ARCHETYPES = {
    'poker_face': PlayerConfig(
        ego=0.32,   # Low ego (0.25-0.40)
        poise=0.77, # High poise (0.70-0.85)
        recovery_rate=0.15,
    ),
    'commanding': PlayerConfig(
        ego=0.80,   # High ego (0.70-0.90)
        poise=0.72, # Medium-high poise (0.65-0.80)
        recovery_rate=0.15,
    ),
    'overheated': PlayerConfig(
        ego=0.80,   # High ego (0.70-0.90)
        poise=0.35, # Low poise (0.25-0.45)
        recovery_rate=0.15,
    ),
    'guarded': PlayerConfig(
        ego=0.40,   # Low-medium ego (0.30-0.50)
        poise=0.72, # Medium-high poise (0.65-0.80)
        recovery_rate=0.15,
    ),
}


@dataclass
class SimulationResult:
    """Results from a simulation run."""
    composure_distribution: Dict[str, float]  # % in each band
    confidence_distribution: Dict[str, float]
    avg_tilt_duration: float  # Hands spent tilted after trigger
    avg_recovery_time: float  # Hands to return to baseline
    min_composure: float
    max_composure: float
    min_confidence: float
    max_confidence: float
    composure_history: List[float]
    confidence_history: List[float]


def clamp(value: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    return max(min_val, min(max_val, value))


def get_composure_band(composure: float) -> str:
    if composure >= 0.8:
        return 'focused'
    elif composure >= 0.6:
        return 'alert'
    elif composure >= 0.4:
        return 'rattled'
    else:
        return 'tilted'


def get_confidence_band(confidence: float) -> str:
    if confidence >= 0.7:
        return 'high'
    elif confidence >= 0.4:
        return 'neutral'
    else:
        return 'low'


def simulate_session(
    num_hands: int,
    player: PlayerConfig,
    events: EventProbabilities,
    impacts: ImpactValues,
    recovery_rate_override: float = None,
    impact_multiplier: float = 1.0,
    seed: int = None,
    use_compounding: bool = True,
    use_personality_baseline: bool = False,
    use_asymmetric_recovery: bool = False,
) -> SimulationResult:
    """
    Simulate a poker session and track psychological state.

    Args:
        num_hands: Number of hands to simulate
        player: Player personality config
        events: Event probability distribution
        impacts: Base impact values
        recovery_rate_override: Override player's recovery rate
        impact_multiplier: Scale all impacts by this factor
        seed: Random seed for reproducibility
        use_compounding: If True, multiple events can fire per hand (realistic)
        use_personality_baseline: If True, recovery pulls toward personality-specific baseline
        use_asymmetric_recovery: If True, recovery from negative states is affected by current composure
    """
    if seed is not None:
        random.seed(seed)

    # Base recovery rate from personality (or override)
    if recovery_rate_override is not None:
        base_recovery = recovery_rate_override
    else:
        # Derive from poise: low poise = faster base recovery
        base_recovery = 0.12 + 0.25 * (1.0 - player.poise)

    # Calculate sensitivities
    conf_sensitivity = 0.3 + 0.7 * player.ego
    comp_sensitivity = 0.3 + 0.7 * (1.0 - player.poise)

    # Determine recovery baselines (derived from poise/ego)
    if use_personality_baseline:
        baseline_conf = player.get_baseline_confidence()
        baseline_comp = player.get_baseline_composure()
    else:
        baseline_conf = 0.5
        baseline_comp = 0.7

    # Initialize state at baseline
    confidence = baseline_conf
    composure = baseline_comp

    composure_history = []
    confidence_history = []
    consecutive_losses = 0
    tilt_durations = []  # Track how long each tilt episode lasts
    current_tilt_start = None

    for hand in range(num_hands):
        # Record state at start of hand
        composure_history.append(composure)
        confidence_history.append(confidence)

        # Track tilt episodes
        if composure < 0.4:
            if current_tilt_start is None:
                current_tilt_start = hand
        else:
            if current_tilt_start is not None:
                tilt_durations.append(hand - current_tilt_start)
                current_tilt_start = None

        # Generate events for this hand
        conf_delta = 0.0
        comp_delta = 0.0
        hand_events = []

        # Most hands: NO EVENT (fold pre-flop, routine action)
        if random.random() < events.event_probability:
            if use_compounding:
                # COMPOUNDING MODEL: Multiple events can fire per hand
                hand_events = _generate_compounding_events(events, impacts, random.random())
            else:
                # SIMPLE MODEL: One event per hand
                hand_events = [_generate_single_event(events, impacts)]

            # Process all events
            is_loss_hand = False
            for event_conf, event_comp, is_loss in hand_events:
                conf_delta += event_conf
                comp_delta += event_comp
                if is_loss:
                    is_loss_hand = True

            if is_loss_hand:
                consecutive_losses += 1
            else:
                consecutive_losses = 0

            # Check for losing streak trigger (compounds with other events!)
            if consecutive_losses >= events.losing_streak_threshold:
                conf_delta += impacts.losing_streak[0]
                comp_delta += impacts.losing_streak[1]

        # Apply sensitivity scaling and impact multiplier
        conf_delta *= conf_sensitivity * impact_multiplier
        comp_delta *= comp_sensitivity * impact_multiplier

        # Apply deltas
        confidence = clamp(confidence + conf_delta)
        composure = clamp(composure + comp_delta)

        # Apply recovery with asymmetric modifier
        if use_asymmetric_recovery:
            # ASYMMETRIC RECOVERY:
            # - Below baseline: recovery speed affected by current state (high = faster escape)
            # - Above baseline: slower decay (let them ride the wave)

            # Composure recovery
            if composure < baseline_comp:
                # Below baseline - current composure affects recovery speed
                comp_modifier = 0.6 + 0.4 * composure
            else:
                # Above baseline - slow decay
                comp_modifier = 0.8

            # Confidence recovery
            if confidence < baseline_conf:
                # Below baseline - current confidence affects recovery speed
                conf_modifier = 0.6 + 0.4 * confidence
            else:
                # Above baseline - slow decay
                conf_modifier = 0.8

            effective_comp_recovery = base_recovery * comp_modifier
            effective_conf_recovery = base_recovery * conf_modifier
        else:
            effective_comp_recovery = base_recovery
            effective_conf_recovery = base_recovery

        # Apply recovery (drift toward baseline)
        confidence = confidence + (baseline_conf - confidence) * effective_conf_recovery
        composure = composure + (baseline_comp - composure) * effective_comp_recovery

    # Close any open tilt episode
    if current_tilt_start is not None:
        tilt_durations.append(num_hands - current_tilt_start)

    # Calculate distributions
    composure_bands = Counter(get_composure_band(c) for c in composure_history)
    confidence_bands = Counter(get_confidence_band(c) for c in confidence_history)

    total = len(composure_history)
    composure_dist = {band: composure_bands.get(band, 0) / total * 100 for band in ['focused', 'alert', 'rattled', 'tilted']}
    confidence_dist = {band: confidence_bands.get(band, 0) / total * 100 for band in ['high', 'neutral', 'low']}

    return SimulationResult(
        composure_distribution=composure_dist,
        confidence_distribution=confidence_dist,
        avg_tilt_duration=statistics.mean(tilt_durations) if tilt_durations else 0,
        avg_recovery_time=1 / base_recovery if base_recovery > 0 else float('inf'),
        min_composure=min(composure_history),
        max_composure=max(composure_history),
        min_confidence=min(confidence_history),
        max_confidence=max(confidence_history),
        composure_history=composure_history,
        confidence_history=confidence_history,
    )


def _generate_single_event(events: EventProbabilities, impacts: ImpactValues) -> Tuple[float, float, bool]:
    """Generate a single event. Returns (conf_delta, comp_delta, is_loss)."""
    roll = random.random()
    cumulative = 0.0

    # Win events (40% of events)
    cumulative += events.win_given_event
    if roll < cumulative:
        return (*impacts.win, False)

    cumulative += events.big_win_given_event
    if roll < cumulative:
        return (*impacts.big_win, False)

    cumulative += events.successful_bluff_given_event
    if roll < cumulative:
        return (*impacts.successful_bluff, False)

    # Loss events (50% of events)
    cumulative += events.small_loss_given_event
    if roll < cumulative:
        return (*impacts.small_loss, True)

    cumulative += events.big_loss_given_event
    if roll < cumulative:
        return (*impacts.big_loss, True)

    cumulative += events.bluff_called_given_event
    if roll < cumulative:
        return (*impacts.bluff_called, True)

    cumulative += events.bad_beat_given_event
    if roll < cumulative:
        return (*impacts.bad_beat, True)

    cumulative += events.got_sucked_out_given_event
    if roll < cumulative:
        return (*impacts.got_sucked_out, True)

    # Neutral events
    cumulative += events.cooler_given_event
    if roll < cumulative:
        return (*impacts.cooler, True)

    return (*impacts.fold_under_pressure, True)


def _generate_compounding_events(
    events: EventProbabilities,
    impacts: ImpactValues,
    base_roll: float,
) -> List[Tuple[float, float, bool]]:
    """
    Generate compounding events for a single hand.

    Models real poker where multiple events can fire together:
    - bad_beat + big_loss + nemesis_loss
    - big_win + double_up + nemesis_win

    Returns list of (conf_delta, comp_delta, is_loss) tuples.
    """
    result = []

    # Determine if this is a win or loss hand (primary event)
    is_win = base_roll < 0.4  # 40% wins, 60% losses

    if is_win:
        # WIN HAND - check for compounding positive events
        result.append(impacts.win + (False,))  # Base win

        # 15% chance of big_win (compounds with win)
        if random.random() < 0.15:
            result.append((impacts.big_win[0] - impacts.win[0],
                          impacts.big_win[1] - impacts.win[1], False))

        # 10% chance it was a successful bluff
        if random.random() < 0.10:
            result.append(impacts.successful_bluff + (False,))

        # 8% chance of nemesis_win (if nemesis was in hand)
        if random.random() < 0.08:
            result.append((0.15, 0.10, False))  # nemesis_win impact

        # 5% chance of double_up
        if random.random() < 0.05:
            result.append((0.20, 0.10, False))  # double_up impact

    else:
        # LOSS HAND - check for compounding negative events
        # Determine primary loss type
        loss_roll = random.random()

        if loss_roll < 0.40:
            # Small/routine loss
            result.append(impacts.small_loss + (True,))
        elif loss_roll < 0.70:
            # Big loss
            result.append(impacts.big_loss + (True,))

            # Big losses can compound with other events
            # 20% chance of bad_beat (was winning, got unlucky)
            if random.random() < 0.20:
                result.append(impacts.bad_beat + (True,))

            # 15% chance of got_sucked_out
            if random.random() < 0.15:
                result.append(impacts.got_sucked_out + (True,))

            # 12% chance of nemesis_loss
            if random.random() < 0.12:
                result.append((-0.10, -0.15, True))  # nemesis_loss impact

            # 8% chance of crippled
            if random.random() < 0.08:
                result.append(impacts.crippled + (True,))

        elif loss_roll < 0.85:
            # Bluff called
            result.append(impacts.bluff_called + (True,))
        else:
            # Cooler or fold under pressure
            if random.random() < 0.5:
                result.append(impacts.cooler + (True,))
            else:
                result.append(impacts.fold_under_pressure + (True,))

    return result


def run_parameter_sweep(
    num_hands: int = 500,
    num_runs: int = 100,
    recovery_rates: List[float] = None,
    impact_multipliers: List[float] = None,
    poise_values: List[float] = None,
    event_rates: List[float] = None,
) -> Dict:
    """
    Sweep parameter space to find optimal settings.

    Returns dict mapping parameter combo to aggregated results.
    """
    if recovery_rates is None:
        recovery_rates = [0.05, 0.08, 0.10, 0.15, 0.20]
    if impact_multipliers is None:
        impact_multipliers = [1.0, 1.5, 2.0]
    if poise_values is None:
        poise_values = [0.5, 0.7]  # Test average and high poise
    if event_rates is None:
        event_rates = [0.15, 0.25, 0.35]  # Conservative, moderate, aggressive

    results = {}
    impacts = ImpactValues()

    for event_rate in event_rates:
        for recovery in recovery_rates:
            for multiplier in impact_multipliers:
                for poise in poise_values:
                    key = f"e={event_rate:.2f}_r={recovery:.2f}_m={multiplier:.1f}_p={poise:.1f}"

                    # Create event config with this rate
                    events = EventProbabilities(event_probability=event_rate)

                    # Run multiple simulations
                    run_results = []
                    for run in range(num_runs):
                        player = PlayerConfig(ego=0.5, poise=poise, recovery_rate=recovery)
                        result = simulate_session(
                            num_hands=num_hands,
                            player=player,
                            events=events,
                            impacts=impacts,
                            impact_multiplier=multiplier,
                            seed=run,
                        )
                        run_results.append(result)

                    # Aggregate
                    results[key] = {
                        'event_rate': event_rate,
                        'recovery_rate': recovery,
                        'impact_multiplier': multiplier,
                        'poise': poise,
                        'avg_focused': statistics.mean(r.composure_distribution['focused'] for r in run_results),
                        'avg_alert': statistics.mean(r.composure_distribution['alert'] for r in run_results),
                        'avg_rattled': statistics.mean(r.composure_distribution['rattled'] for r in run_results),
                        'avg_tilted': statistics.mean(r.composure_distribution['tilted'] for r in run_results),
                        'avg_tilt_duration': statistics.mean(r.avg_tilt_duration for r in run_results),
                        'min_composure': min(r.min_composure for r in run_results),
                    }

    return results


def print_results_table(results: Dict, target_tilted: Tuple[float, float] = (2, 7)):
    """Print results as a table, highlighting those meeting targets."""
    print("\n" + "="*120)
    print("PARAMETER SWEEP RESULTS")
    print("="*120)
    print(f"Target: {target_tilted[0]}-{target_tilted[1]}% tilted time")
    print()
    print(f"{'Config':<40} {'Focused':>8} {'Alert':>8} {'Rattled':>8} {'Tilted':>8} {'TiltDur':>8} {'MinComp':>8}")
    print("-"*120)

    # Sort by how close to target tilted %
    target_mid = (target_tilted[0] + target_tilted[1]) / 2
    sorted_results = sorted(results.items(), key=lambda x: abs(x[1]['avg_tilted'] - target_mid))

    for key, data in sorted_results[:25]:  # Top 25
        tilted = data['avg_tilted']
        meets_target = target_tilted[0] <= tilted <= target_tilted[1]
        marker = " ✓" if meets_target else ""

        print(f"{key:<40} {data['avg_focused']:>7.1f}% {data['avg_alert']:>7.1f}% "
              f"{data['avg_rattled']:>7.1f}% {tilted:>7.1f}% {data['avg_tilt_duration']:>7.1f}h "
              f"{data['min_composure']:>7.2f}{marker}")


def visualize_single_run(result: SimulationResult, title: str = ""):
    """Print ASCII visualization of a single run."""
    print(f"\n{'='*60}")
    print(f"Simulation: {title}")
    print(f"{'='*60}")

    print("\nComposure Distribution:")
    for band in ['focused', 'alert', 'rattled', 'tilted']:
        pct = result.composure_distribution[band]
        bar = '█' * int(pct / 2)
        print(f"  {band:>10}: {bar:<50} {pct:5.1f}%")

    print(f"\nComposure Range: {result.min_composure:.2f} - {result.max_composure:.2f}")
    print(f"Confidence Range: {result.min_confidence:.2f} - {result.max_confidence:.2f}")
    print(f"Avg Tilt Duration: {result.avg_tilt_duration:.1f} hands")

    # Mini timeline (sample every 10 hands)
    print("\nComposure Timeline (sampled):")
    timeline = result.composure_history[::10][:50]
    for i, c in enumerate(timeline):
        if c >= 0.8:
            char = '▁'
        elif c >= 0.6:
            char = '▃'
        elif c >= 0.4:
            char = '▅'
        else:
            char = '█'
        print(char, end='')
    print(f" (n={len(timeline)*10})")


# === POKER FACE ZONE ===

@dataclass
class PokerFaceZone:
    """
    The Poker Face Zone is a 3D ellipsoid where emotions are masked.

    Players inside the zone show neutral expressions regardless of internal state.
    Players outside show their quadrant emotion.

    Zone center (universal):
    - Confidence: 0.65
    - Composure: 0.75
    - Energy: 0.4

    Zone radii (personality-shaped):
    - r_conf: Narrower for high risk_identity (risk-seekers show more)
    - r_comp: Larger for high poise (stable players hide more)
    - r_energy: Narrower for high expressiveness (expressive players show more)
    """
    center_confidence: float = 0.65
    center_composure: float = 0.75
    center_energy: float = 0.4

    # Default radii (personality-neutral)
    base_r_confidence: float = 0.25
    base_r_composure: float = 0.20
    base_r_energy: float = 0.30

    def get_radii(self, risk_identity: float, poise: float, expressiveness: float) -> Tuple[float, float, float]:
        """
        Calculate personality-adjusted radii.

        Higher risk_identity → narrower confidence radius (show more swagger)
        Higher poise → larger composure radius (hide tilt better)
        Higher expressiveness → narrower energy radius (show more emotion)
        """
        r_conf = self.base_r_confidence * (1.3 - 0.6 * risk_identity)  # 0.7x to 1.3x
        r_comp = self.base_r_composure * (0.7 + 0.6 * poise)  # 0.7x to 1.3x
        r_energy = self.base_r_energy * (1.3 - 0.6 * expressiveness)  # 0.7x to 1.3x
        return r_conf, r_comp, r_energy

    def is_inside(
        self,
        confidence: float,
        composure: float,
        energy: float,
        risk_identity: float = 0.5,
        poise: float = 0.7,
        expressiveness: float = 0.5,
    ) -> bool:
        """
        Test if emotional state is inside the poker face zone.

        Uses ellipsoid distance formula:
        distance = ((c - c0)/rc)² + ((comp - comp0)/rcomp)² + ((e - e0)/re)²
        Inside if distance <= 1.0
        """
        r_conf, r_comp, r_energy = self.get_radii(risk_identity, poise, expressiveness)

        distance = (
            ((confidence - self.center_confidence) / r_conf) ** 2 +
            ((composure - self.center_composure) / r_comp) ** 2 +
            ((energy - self.center_energy) / r_energy) ** 2
        )

        return distance <= 1.0

    def get_visible_emotion(
        self,
        confidence: float,
        composure: float,
        energy: float,
        internal_quadrant: str,
        risk_identity: float = 0.5,
        poise: float = 0.7,
        expressiveness: float = 0.5,
    ) -> str:
        """
        Get the emotion that should be displayed.

        If inside poker face zone → 'poker_face'
        If outside → internal_quadrant emotion
        """
        if self.is_inside(confidence, composure, energy, risk_identity, poise, expressiveness):
            return 'poker_face'
        return internal_quadrant


def analyze_poker_face_coverage(
    num_samples: int = 10000,
    player: PlayerConfig = None,
) -> Dict[str, float]:
    """
    Analyze what % of the emotional state space is covered by poker face.

    Samples random points in the 3D space and checks zone membership.
    """
    if player is None:
        player = PlayerConfig()

    zone = PokerFaceZone()

    inside_count = 0
    quadrant_counts = Counter()
    visible_counts = Counter()

    for _ in range(num_samples):
        conf = random.random()
        comp = random.random()
        energy = random.random()

        # Determine internal quadrant
        if conf < 0.35 and comp < 0.35:
            quadrant = 'shaken'
        elif conf > 0.5 and comp > 0.5:
            quadrant = 'commanding'
        elif conf > 0.5:
            quadrant = 'overheated'
        else:
            quadrant = 'guarded'

        quadrant_counts[quadrant] += 1

        visible = zone.get_visible_emotion(
            conf, comp, energy, quadrant,
            risk_identity=player.ego,  # Using ego as proxy
            poise=player.poise,
            expressiveness=0.5,
        )
        visible_counts[visible] += 1

        if visible == 'poker_face':
            inside_count += 1

    return {
        'poker_face_coverage': inside_count / num_samples * 100,
        'internal_quadrants': {k: v / num_samples * 100 for k, v in quadrant_counts.items()},
        'visible_emotions': {k: v / num_samples * 100 for k, v in visible_counts.items()},
    }


if __name__ == '__main__':
    import sys

    print("Psychology System Balance Simulator")
    print("="*80)
    print("""
    FULL SYSTEM WITH ALL MECHANICS:
    1. Personality-specific baselines (derived from poise/ego)
    2. Base recovery rate derived from poise
    3. Asymmetric dynamic recovery:
       - Below baseline: recovery speed × (0.6 + 0.4 × current_composure)
       - Above baseline: recovery speed × 0.8 (slow decay, ride the wave)
    4. Compounding events (multiple events per hand)
    """)

    events = EventProbabilities(event_probability=0.25)  # 25% event rate
    impacts = ImpactValues()

    # =========================================================================
    # TEST FULL SYSTEM WITH ASYMMETRIC RECOVERY
    # =========================================================================
    print("="*80)
    print("FULL SYSTEM: Personality Baselines + Asymmetric Recovery")
    print("="*80)
    print("""
    Formulas:
    - baseline_composure = 0.45 + 0.40 × poise
    - baseline_confidence = 0.35 + 0.30 × (1 - ego)
    - base_recovery = 0.12 + 0.25 × (1 - poise)
    - Below baseline: effective_recovery = base × (0.6 + 0.4 × current)
    - Above baseline: effective_recovery = base × 0.8
    """)

    for archetype_name, player in ARCHETYPES.items():
        baseline_comp = player.get_baseline_composure()
        baseline_conf = player.get_baseline_confidence()
        comp_sens = 0.3 + 0.7 * (1 - player.poise)
        conf_sens = 0.3 + 0.7 * player.ego
        base_recovery = 0.12 + 0.25 * (1 - player.poise)

        print(f"\n{'='*80}")
        print(f"ARCHETYPE: {archetype_name.upper()}")
        print(f"  Poise: {player.poise:.2f}")
        print(f"    → baseline_composure: {baseline_comp:.2f}")
        print(f"    → composure sensitivity: {comp_sens:.2f}")
        print(f"    → base_recovery: {base_recovery:.2f}")
        print(f"  Ego: {player.ego:.2f}")
        print(f"    → baseline_confidence: {baseline_conf:.2f}")
        print(f"    → confidence sensitivity: {conf_sens:.2f}")
        print("="*80)

        result = simulate_session(
            500, player, events, impacts,
            seed=42,
            use_compounding=True,
            use_personality_baseline=True,
            use_asymmetric_recovery=True,  # NEW: Asymmetric recovery!
        )
        visualize_single_run(result, archetype_name)

    # =========================================================================
    # COMPARE: Symmetric vs Asymmetric Recovery
    # =========================================================================
    print("\n" + "="*80)
    print("COMPARISON: Symmetric vs Asymmetric Recovery")
    print("="*80)
    print("""
    Symmetric: Same recovery rate whether above or below baseline
    Asymmetric: Below baseline = recovery affected by current state
                Above baseline = slow decay (0.8x), ride the wave
    """)

    for archetype_name, player in ARCHETYPES.items():
        # Symmetric recovery
        result_symmetric = simulate_session(
            500, player, events, impacts,
            seed=42,
            use_compounding=True,
            use_personality_baseline=True,
            use_asymmetric_recovery=False,
        )

        # Asymmetric recovery
        result_asymmetric = simulate_session(
            500, player, events, impacts,
            seed=42,
            use_compounding=True,
            use_personality_baseline=True,
            use_asymmetric_recovery=True,
        )

        baseline = player.get_baseline_composure()
        print(f"\n{archetype_name.upper()} (baseline={baseline:.2f}):")
        print(f"  Symmetric:   Focused={result_symmetric.composure_distribution['focused']:4.1f}%  "
              f"Alert={result_symmetric.composure_distribution['alert']:4.1f}%  "
              f"Rattled={result_symmetric.composure_distribution['rattled']:4.1f}%  "
              f"Tilted={result_symmetric.composure_distribution['tilted']:4.1f}%  "
              f"(tilt dur: {result_symmetric.avg_tilt_duration:.1f}h)")
        print(f"  Asymmetric:  Focused={result_asymmetric.composure_distribution['focused']:4.1f}%  "
              f"Alert={result_asymmetric.composure_distribution['alert']:4.1f}%  "
              f"Rattled={result_asymmetric.composure_distribution['rattled']:4.1f}%  "
              f"Tilted={result_asymmetric.composure_distribution['tilted']:4.1f}%  "
              f"(tilt dur: {result_asymmetric.avg_tilt_duration:.1f}h)")

    # =========================================================================
    # TEST: Tilt Stickiness - How long does it take to escape tilt?
    # =========================================================================
    print("\n" + "="*80)
    print("TILT STICKINESS: Simulating recovery from deep tilt")
    print("="*80)
    print("""
    Starting each archetype at composure=0.2 (deep tilt) and tracking recovery.
    With asymmetric recovery, high-composure recovery is penalized when tilted.
    """)

    for archetype_name, player in ARCHETYPES.items():
        baseline_comp = player.get_baseline_composure()
        base_recovery = 0.12 + 0.25 * (1 - player.poise)

        # Simulate recovery from tilt (composure = 0.2)
        composure = 0.2
        hands_to_recover = 0

        # Recover until we hit 90% of baseline
        target = baseline_comp * 0.9
        while composure < target and hands_to_recover < 100:
            # Asymmetric recovery
            modifier = 0.6 + 0.4 * composure
            effective_recovery = base_recovery * modifier
            composure = composure + (baseline_comp - composure) * effective_recovery
            hands_to_recover += 1

        print(f"{archetype_name.upper()}: {hands_to_recover} hands to recover from 0.20 → {target:.2f} "
              f"(base_r={base_recovery:.2f})")

    # =========================================================================
    # FINAL SUMMARY: All Archetypes with Full System
    # =========================================================================
    print("\n" + "="*80)
    print("FINAL SUMMARY: Full System (baselines + asymmetric recovery + compounding)")
    print("="*80)

    print(f"\n{'Archetype':<12} {'Poise':>6} {'Base':>6} {'Rec':>6} "
          f"{'Focused':>8} {'Alert':>8} {'Rattled':>8} {'Tilted':>8} {'TiltDur':>8}")
    print("-" * 90)

    for archetype_name, player in ARCHETYPES.items():
        baseline_comp = player.get_baseline_composure()
        base_recovery = 0.12 + 0.25 * (1 - player.poise)

        result = simulate_session(
            500, player, events, impacts,
            seed=42,
            use_compounding=True,
            use_personality_baseline=True,
            use_asymmetric_recovery=True,
        )

        dist = result.composure_distribution
        print(f"{archetype_name:<12} {player.poise:>6.2f} {baseline_comp:>6.2f} {base_recovery:>6.2f} "
              f"{dist['focused']:>7.1f}% {dist['alert']:>7.1f}% "
              f"{dist['rattled']:>7.1f}% {dist['tilted']:>7.1f}% "
              f"{result.avg_tilt_duration:>7.1f}h")

    # Parameter sweep
    if '--sweep' in sys.argv:
        print("\n" + "="*60)
        print("RUNNING PARAMETER SWEEP (this may take a moment)...")
        results = run_parameter_sweep(
            num_hands=500,
            num_runs=50,
            recovery_rates=[0.05, 0.08, 0.10, 0.12, 0.15, 0.20],
            impact_multipliers=[1.0, 1.5, 2.0],
            poise_values=[0.3, 0.5, 0.7],
        )
        print_results_table(results, target_tilted=(2, 7))

    # Poker Face Zone analysis
    print("\n" + "="*60)
    print("POKER FACE ZONE ANALYSIS")
    print("="*60)

    zone_result = analyze_poker_face_coverage(10000)
    print(f"\nPoker Face Coverage: {zone_result['poker_face_coverage']:.1f}% of state space")
    print("\nInternal Quadrant Distribution (uniform sampling):")
    for q, pct in sorted(zone_result['internal_quadrants'].items()):
        print(f"  {q}: {pct:.1f}%")
    print("\nVisible Emotion Distribution:")
    for e, pct in sorted(zone_result['visible_emotions'].items(), key=lambda x: -x[1]):
        print(f"  {e}: {pct:.1f}%")

"""
Zone Metrics Analyzer for Psychology System Experiments.

Analyzes zone distributions, tilt frequencies, and transitions from
experiment data stored in player_decision_analysis table.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from collections import defaultdict


@dataclass
class ZoneDistribution:
    """Distribution of time spent in each zone for a player."""
    player_name: str
    total_decisions: int = 0
    sweet_spots: Dict[str, float] = field(default_factory=dict)  # zone -> percentage
    penalties: Dict[str, float] = field(default_factory=dict)    # zone -> percentage
    neutral_percentage: float = 0.0


@dataclass
class TiltBandDistribution:
    """Distribution across tilt severity bands."""
    baseline: float = 0.0    # penalty_strength < 0.10
    medium: float = 0.0      # 0.10 <= penalty_strength < 0.50
    high: float = 0.0        # 0.50 <= penalty_strength < 0.75
    full_tilt: float = 0.0   # penalty_strength >= 0.75

    @property
    def total(self) -> float:
        return self.baseline + self.medium + self.high + self.full_tilt


@dataclass
class ZoneTransition:
    """A transition between zones."""
    player_name: str
    hand_number: int
    from_zone: Optional[str]
    to_zone: Optional[str]
    from_penalty: Optional[str]
    to_penalty: Optional[str]
    trigger_event: Optional[str] = None


# PRD targets for tilt band distribution
PRD_TARGETS = {
    'baseline': (0.70, 0.85),   # 70-85%
    'medium': (0.10, 0.20),     # 10-20%
    'high': (0.02, 0.07),       # 2-7%
    'full_tilt': (0.00, 0.02),  # 0-2%
}


class ZoneMetricsAnalyzer:
    """
    Analyzer for psychology zone metrics from experiment data.

    Queries player_decision_analysis table to compute zone distributions,
    tilt frequencies, and transition patterns.
    """

    def __init__(self, db_path: str):
        """
        Initialize the analyzer.

        Args:
            db_path: Path to the SQLite database
        """
        self.db_path = db_path

    def _get_connection(self) -> sqlite3.Connection:
        """Create a database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_zone_distribution(
        self, experiment_id: int
    ) -> Dict[str, ZoneDistribution]:
        """
        Get zone distribution for each player in an experiment.

        Args:
            experiment_id: ID of the experiment to analyze

        Returns:
            Dictionary mapping player_name to ZoneDistribution
        """
        with self._get_connection() as conn:
            # Get all decisions for games in this experiment
            cursor = conn.execute("""
                SELECT
                    pda.player_name,
                    pda.zone_primary_sweet_spot,
                    pda.zone_primary_penalty,
                    pda.zone_in_neutral_territory,
                    pda.zone_sweet_spots_json,
                    pda.zone_penalties_json
                FROM player_decision_analysis pda
                JOIN experiment_games eg ON pda.game_id = eg.game_id
                WHERE eg.experiment_id = ?
                  AND pda.zone_confidence IS NOT NULL
            """, (experiment_id,))

            # Aggregate by player
            player_data: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
                'total': 0,
                'sweet_spot_counts': defaultdict(int),
                'penalty_counts': defaultdict(int),
                'neutral_count': 0,
            })

            for row in cursor:
                player = row['player_name']
                data = player_data[player]
                data['total'] += 1

                if row['zone_in_neutral_territory']:
                    data['neutral_count'] += 1
                else:
                    if row['zone_primary_sweet_spot']:
                        data['sweet_spot_counts'][row['zone_primary_sweet_spot']] += 1
                    if row['zone_primary_penalty']:
                        data['penalty_counts'][row['zone_primary_penalty']] += 1

            # Convert to ZoneDistribution objects
            result = {}
            for player, data in player_data.items():
                total = data['total']
                if total == 0:
                    continue

                dist = ZoneDistribution(
                    player_name=player,
                    total_decisions=total,
                    sweet_spots={
                        zone: count / total
                        for zone, count in data['sweet_spot_counts'].items()
                    },
                    penalties={
                        zone: count / total
                        for zone, count in data['penalty_counts'].items()
                    },
                    neutral_percentage=data['neutral_count'] / total,
                )
                result[player] = dist

            return result

    def get_tilt_frequency(
        self, experiment_id: int
    ) -> Dict[str, TiltBandDistribution]:
        """
        Get tilt band distribution for each player in an experiment.

        Tilt bands:
        - Baseline: penalty_strength < 0.10
        - Medium: 0.10 <= penalty_strength < 0.50
        - High: 0.50 <= penalty_strength < 0.75
        - Full Tilt: penalty_strength >= 0.75

        Args:
            experiment_id: ID of the experiment to analyze

        Returns:
            Dictionary mapping player_name to TiltBandDistribution
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT
                    pda.player_name,
                    pda.zone_total_penalty_strength
                FROM player_decision_analysis pda
                JOIN experiment_games eg ON pda.game_id = eg.game_id
                WHERE eg.experiment_id = ?
                  AND pda.zone_total_penalty_strength IS NOT NULL
            """, (experiment_id,))

            # Aggregate by player
            player_data: Dict[str, Dict[str, int]] = defaultdict(lambda: {
                'baseline': 0,
                'medium': 0,
                'high': 0,
                'full_tilt': 0,
                'total': 0,
            })

            for row in cursor:
                player = row['player_name']
                strength = row['zone_total_penalty_strength'] or 0.0
                data = player_data[player]
                data['total'] += 1

                if strength < 0.10:
                    data['baseline'] += 1
                elif strength < 0.50:
                    data['medium'] += 1
                elif strength < 0.75:
                    data['high'] += 1
                else:
                    data['full_tilt'] += 1

            # Convert to TiltBandDistribution objects
            result = {}
            for player, data in player_data.items():
                total = data['total']
                if total == 0:
                    continue

                dist = TiltBandDistribution(
                    baseline=data['baseline'] / total,
                    medium=data['medium'] / total,
                    high=data['high'] / total,
                    full_tilt=data['full_tilt'] / total,
                )
                result[player] = dist

            return result

    def get_zone_transitions(
        self, experiment_id: int
    ) -> List[ZoneTransition]:
        """
        Get all zone transitions for an experiment.

        A transition occurs when a player's primary zone changes
        between consecutive decisions.

        Args:
            experiment_id: ID of the experiment to analyze

        Returns:
            List of ZoneTransition objects
        """
        transitions = []

        with self._get_connection() as conn:
            # Get decisions ordered by game and time
            cursor = conn.execute("""
                SELECT
                    pda.player_name,
                    pda.hand_number,
                    pda.zone_primary_sweet_spot,
                    pda.zone_primary_penalty,
                    pda.game_id
                FROM player_decision_analysis pda
                JOIN experiment_games eg ON pda.game_id = eg.game_id
                WHERE eg.experiment_id = ?
                  AND pda.zone_confidence IS NOT NULL
                ORDER BY pda.game_id, pda.player_name, pda.hand_number, pda.created_at
            """, (experiment_id,))

            # Track previous state per player per game
            prev_state: Dict[str, Dict[str, Any]] = {}

            for row in cursor:
                key = f"{row['game_id']}_{row['player_name']}"
                current_sweet = row['zone_primary_sweet_spot']
                current_penalty = row['zone_primary_penalty']

                if key in prev_state:
                    prev = prev_state[key]
                    # Check for sweet spot transition
                    if current_sweet != prev['sweet_spot'] or current_penalty != prev['penalty']:
                        transitions.append(ZoneTransition(
                            player_name=row['player_name'],
                            hand_number=row['hand_number'],
                            from_zone=prev['sweet_spot'],
                            to_zone=current_sweet,
                            from_penalty=prev['penalty'],
                            to_penalty=current_penalty,
                        ))

                prev_state[key] = {
                    'sweet_spot': current_sweet,
                    'penalty': current_penalty,
                }

        return transitions

    def get_intrusive_thought_frequency(
        self, experiment_id: int
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get intrusive thought injection statistics per player.

        Args:
            experiment_id: ID of the experiment to analyze

        Returns:
            Dictionary mapping player_name to stats dict with:
            - total_decisions: Total number of decisions
            - injections: Number of times thoughts were injected
            - injection_rate: Percentage of decisions with injections
            - thought_counts: Dict of thought -> count
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT
                    pda.player_name,
                    pda.zone_intrusive_thoughts_injected,
                    pda.zone_intrusive_thoughts_json
                FROM player_decision_analysis pda
                JOIN experiment_games eg ON pda.game_id = eg.game_id
                WHERE eg.experiment_id = ?
                  AND pda.zone_confidence IS NOT NULL
            """, (experiment_id,))

            player_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
                'total_decisions': 0,
                'injections': 0,
                'thought_counts': defaultdict(int),
            })

            for row in cursor:
                player = row['player_name']
                stats = player_stats[player]
                stats['total_decisions'] += 1

                if row['zone_intrusive_thoughts_injected']:
                    stats['injections'] += 1
                    if row['zone_intrusive_thoughts_json']:
                        try:
                            thoughts = json.loads(row['zone_intrusive_thoughts_json'])
                            for thought in thoughts:
                                stats['thought_counts'][thought] += 1
                        except (json.JSONDecodeError, TypeError):
                            pass

            # Calculate rates
            result = {}
            for player, stats in player_stats.items():
                total = stats['total_decisions']
                result[player] = {
                    'total_decisions': total,
                    'injections': stats['injections'],
                    'injection_rate': stats['injections'] / total if total > 0 else 0,
                    'thought_counts': dict(stats['thought_counts']),
                }

            return result

    def compare_to_targets(
        self, distribution: TiltBandDistribution
    ) -> Dict[str, Dict[str, Any]]:
        """
        Compare a tilt band distribution to PRD targets.

        Args:
            distribution: TiltBandDistribution to compare

        Returns:
            Dictionary with comparison results for each band:
            - value: Actual value
            - target_min: Target minimum
            - target_max: Target maximum
            - status: 'pass', 'warn', or 'fail'
        """
        results = {}

        bands = ['baseline', 'medium', 'high', 'full_tilt']
        for band in bands:
            value = getattr(distribution, band)
            target_min, target_max = PRD_TARGETS[band]

            if target_min <= value <= target_max:
                status = 'pass'
            elif band in ('baseline',) and value > target_max:
                status = 'warn'  # Above target is fine for baseline
            elif band in ('full_tilt', 'high') and value < target_min:
                status = 'warn'  # Below target is fine for dangerous zones
            else:
                status = 'fail'

            results[band] = {
                'value': value,
                'target_min': target_min,
                'target_max': target_max,
                'status': status,
            }

        return results

    def get_experiment_summary(self, experiment_id: int) -> Dict[str, Any]:
        """
        Get a comprehensive summary of zone metrics for an experiment.

        Args:
            experiment_id: ID of the experiment

        Returns:
            Dictionary with:
            - zone_distributions: Per-player zone distributions
            - tilt_bands: Per-player tilt band distributions
            - target_comparison: Aggregate comparison to PRD targets
            - intrusive_thought_stats: Per-player injection stats
            - total_decisions: Total number of decisions analyzed
            - total_transitions: Total number of zone transitions
        """
        zone_dists = self.get_zone_distribution(experiment_id)
        tilt_bands = self.get_tilt_frequency(experiment_id)
        thought_stats = self.get_intrusive_thought_frequency(experiment_id)
        transitions = self.get_zone_transitions(experiment_id)

        # Aggregate tilt band comparison
        aggregate_tilt = TiltBandDistribution()
        total_decisions = 0

        for player, dist in tilt_bands.items():
            total_decisions += zone_dists.get(player, ZoneDistribution(player)).total_decisions

        if tilt_bands:
            n = len(tilt_bands)
            aggregate_tilt.baseline = sum(d.baseline for d in tilt_bands.values()) / n
            aggregate_tilt.medium = sum(d.medium for d in tilt_bands.values()) / n
            aggregate_tilt.high = sum(d.high for d in tilt_bands.values()) / n
            aggregate_tilt.full_tilt = sum(d.full_tilt for d in tilt_bands.values()) / n

        return {
            'zone_distributions': zone_dists,
            'tilt_bands': tilt_bands,
            'aggregate_tilt': aggregate_tilt,
            'target_comparison': self.compare_to_targets(aggregate_tilt),
            'intrusive_thought_stats': thought_stats,
            'total_decisions': total_decisions,
            'total_transitions': len(transitions),
        }

"""
Zone Parameter Tuner for Psychology System.

Analyzes experiment results and recommends parameter adjustments
to achieve PRD targets for tilt band distributions.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional

from experiments.analysis.zone_metrics_analyzer import (
    ZoneMetricsAnalyzer,
    TiltBandDistribution,
    PRD_TARGETS,
)


@dataclass
class TunableParameter:
    """Definition of a tunable parameter."""
    name: str
    current_value: float
    min_value: float
    max_value: float
    description: str
    category: str  # 'zone_threshold', 'zone_radius', 'recovery', etc.


@dataclass
class TuningRecommendation:
    """A recommended parameter adjustment."""
    parameter: str
    current_value: float
    recommended_value: float
    reason: str
    confidence: str  # 'high', 'medium', 'low'
    priority: int    # 1=highest, 3=lowest


@dataclass
class AnalysisResult:
    """Result of analyzing an experiment for tuning."""
    experiment_id: int
    total_decisions: int
    tilt_band_comparison: Dict[str, Dict[str, Any]]
    issues_detected: List[str]
    recommendations: List[TuningRecommendation]
    aggregate_tilt: TiltBandDistribution


# Tunable parameters from poker/player_psychology.py
TUNABLE_PARAMETERS: Dict[str, TunableParameter] = {
    # Zone thresholds (penalty detection)
    'PENALTY_TILTED_THRESHOLD': TunableParameter(
        name='PENALTY_TILTED_THRESHOLD',
        current_value=0.35,
        min_value=0.20,
        max_value=0.50,
        description='Composure below this triggers tilted penalty zone',
        category='penalty_threshold',
    ),
    'PENALTY_OVERCONFIDENT_THRESHOLD': TunableParameter(
        name='PENALTY_OVERCONFIDENT_THRESHOLD',
        current_value=0.90,
        min_value=0.80,
        max_value=0.95,
        description='Confidence above this triggers overconfident penalty zone',
        category='penalty_threshold',
    ),
    'PENALTY_SHAKEN_CONF_THRESHOLD': TunableParameter(
        name='PENALTY_SHAKEN_CONF_THRESHOLD',
        current_value=0.35,
        min_value=0.20,
        max_value=0.50,
        description='Confidence threshold for shaken zone (corner)',
        category='penalty_threshold',
    ),
    'PENALTY_SHAKEN_COMP_THRESHOLD': TunableParameter(
        name='PENALTY_SHAKEN_COMP_THRESHOLD',
        current_value=0.35,
        min_value=0.20,
        max_value=0.50,
        description='Composure threshold for shaken zone (corner)',
        category='penalty_threshold',
    ),
    'PENALTY_OVERHEATED_CONF_THRESHOLD': TunableParameter(
        name='PENALTY_OVERHEATED_CONF_THRESHOLD',
        current_value=0.65,
        min_value=0.50,
        max_value=0.80,
        description='Confidence threshold for overheated zone',
        category='penalty_threshold',
    ),
    'PENALTY_OVERHEATED_COMP_THRESHOLD': TunableParameter(
        name='PENALTY_OVERHEATED_COMP_THRESHOLD',
        current_value=0.35,
        min_value=0.20,
        max_value=0.50,
        description='Composure threshold for overheated zone',
        category='penalty_threshold',
    ),
    'PENALTY_DETACHED_CONF_THRESHOLD': TunableParameter(
        name='PENALTY_DETACHED_CONF_THRESHOLD',
        current_value=0.35,
        min_value=0.20,
        max_value=0.50,
        description='Confidence threshold for detached zone',
        category='penalty_threshold',
    ),
    'PENALTY_DETACHED_COMP_THRESHOLD': TunableParameter(
        name='PENALTY_DETACHED_COMP_THRESHOLD',
        current_value=0.65,
        min_value=0.50,
        max_value=0.80,
        description='Composure threshold for detached zone',
        category='penalty_threshold',
    ),

    # Sweet spot radii
    'ZONE_POKER_FACE_RADIUS': TunableParameter(
        name='ZONE_POKER_FACE_RADIUS',
        current_value=0.16,
        min_value=0.10,
        max_value=0.25,
        description='Radius of poker face sweet spot zone',
        category='zone_radius',
    ),
    'ZONE_GUARDED_RADIUS': TunableParameter(
        name='ZONE_GUARDED_RADIUS',
        current_value=0.15,
        min_value=0.10,
        max_value=0.25,
        description='Radius of guarded sweet spot zone',
        category='zone_radius',
    ),
    'ZONE_COMMANDING_RADIUS': TunableParameter(
        name='ZONE_COMMANDING_RADIUS',
        current_value=0.14,
        min_value=0.10,
        max_value=0.25,
        description='Radius of commanding sweet spot zone',
        category='zone_radius',
    ),
    'ZONE_AGGRO_RADIUS': TunableParameter(
        name='ZONE_AGGRO_RADIUS',
        current_value=0.12,
        min_value=0.08,
        max_value=0.20,
        description='Radius of aggro sweet spot zone',
        category='zone_radius',
    ),

    # Recovery constants
    'RECOVERY_BELOW_BASELINE_FLOOR': TunableParameter(
        name='RECOVERY_BELOW_BASELINE_FLOOR',
        current_value=0.6,
        min_value=0.4,
        max_value=0.8,
        description='Minimum recovery modifier when below baseline (tilt is sticky)',
        category='recovery',
    ),
    'RECOVERY_BELOW_BASELINE_RANGE': TunableParameter(
        name='RECOVERY_BELOW_BASELINE_RANGE',
        current_value=0.4,
        min_value=0.2,
        max_value=0.6,
        description='Range added to floor based on current value',
        category='recovery',
    ),
    'RECOVERY_ABOVE_BASELINE': TunableParameter(
        name='RECOVERY_ABOVE_BASELINE',
        current_value=0.8,
        min_value=0.6,
        max_value=1.0,
        description='Recovery modifier when above baseline (hot streaks)',
        category='recovery',
    ),
}


class ZoneParameterTuner:
    """
    Analyzes experiment results and recommends parameter adjustments.

    This class provides informational recommendations only - it does not
    auto-apply any changes. Parameter adjustments should be reviewed
    and applied manually.
    """

    def __init__(self, db_path: str):
        """
        Initialize the tuner.

        Args:
            db_path: Path to the SQLite database
        """
        self.db_path = db_path
        self.analyzer = ZoneMetricsAnalyzer(db_path)

    def analyze_experiment(self, experiment_id: int) -> AnalysisResult:
        """
        Analyze an experiment and generate recommendations.

        Args:
            experiment_id: ID of the experiment to analyze

        Returns:
            AnalysisResult with issues and recommendations
        """
        summary = self.analyzer.get_experiment_summary(experiment_id)

        issues = []
        recommendations = []

        aggregate_tilt = summary['aggregate_tilt']
        comparison = summary['target_comparison']

        # Analyze baseline (should be 70-85%)
        baseline_data = comparison['baseline']
        if baseline_data['status'] == 'fail':
            if baseline_data['value'] < baseline_data['target_min']:
                issues.append(
                    f"Baseline too low: {baseline_data['value']:.1%} "
                    f"(target: {baseline_data['target_min']:.0%}-{baseline_data['target_max']:.0%})"
                )
                # Recommend making penalty zones narrower
                recommendations.append(TuningRecommendation(
                    parameter='PENALTY_TILTED_THRESHOLD',
                    current_value=TUNABLE_PARAMETERS['PENALTY_TILTED_THRESHOLD'].current_value,
                    recommended_value=max(0.20, TUNABLE_PARAMETERS['PENALTY_TILTED_THRESHOLD'].current_value - 0.05),
                    reason='Lower tilted threshold to reduce penalty zone entry',
                    confidence='medium',
                    priority=1,
                ))
                recommendations.append(TuningRecommendation(
                    parameter='RECOVERY_BELOW_BASELINE_FLOOR',
                    current_value=TUNABLE_PARAMETERS['RECOVERY_BELOW_BASELINE_FLOOR'].current_value,
                    recommended_value=min(0.8, TUNABLE_PARAMETERS['RECOVERY_BELOW_BASELINE_FLOOR'].current_value + 0.1),
                    reason='Increase recovery floor to help players exit tilt faster',
                    confidence='medium',
                    priority=2,
                ))

        # Analyze medium (should be 10-20%)
        medium_data = comparison['medium']
        if medium_data['status'] == 'fail':
            if medium_data['value'] < medium_data['target_min']:
                issues.append(
                    f"Medium tilt too low: {medium_data['value']:.1%} "
                    f"(target: {medium_data['target_min']:.0%}-{medium_data['target_max']:.0%})"
                )
            elif medium_data['value'] > medium_data['target_max']:
                issues.append(
                    f"Medium tilt too high: {medium_data['value']:.1%} "
                    f"(target: {medium_data['target_min']:.0%}-{medium_data['target_max']:.0%})"
                )
                recommendations.append(TuningRecommendation(
                    parameter='RECOVERY_ABOVE_BASELINE',
                    current_value=TUNABLE_PARAMETERS['RECOVERY_ABOVE_BASELINE'].current_value,
                    recommended_value=min(1.0, TUNABLE_PARAMETERS['RECOVERY_ABOVE_BASELINE'].current_value + 0.1),
                    reason='Increase recovery rate to reduce time in medium tilt',
                    confidence='low',
                    priority=2,
                ))

        # Analyze high (should be 2-7%)
        high_data = comparison['high']
        if high_data['status'] == 'fail' and high_data['value'] > high_data['target_max']:
            issues.append(
                f"High tilt too frequent: {high_data['value']:.1%} "
                f"(target: {high_data['target_min']:.0%}-{high_data['target_max']:.0%})"
            )
            recommendations.append(TuningRecommendation(
                parameter='PENALTY_TILTED_THRESHOLD',
                current_value=TUNABLE_PARAMETERS['PENALTY_TILTED_THRESHOLD'].current_value,
                recommended_value=max(0.20, TUNABLE_PARAMETERS['PENALTY_TILTED_THRESHOLD'].current_value - 0.05),
                reason='Lower tilted threshold to make severe tilt less common',
                confidence='medium',
                priority=1,
            ))

        # Analyze full tilt (should be 0-2%)
        full_tilt_data = comparison['full_tilt']
        if full_tilt_data['status'] == 'fail' and full_tilt_data['value'] > full_tilt_data['target_max']:
            issues.append(
                f"Full tilt too frequent: {full_tilt_data['value']:.1%} "
                f"(target: {full_tilt_data['target_min']:.0%}-{full_tilt_data['target_max']:.0%})"
            )
            recommendations.append(TuningRecommendation(
                parameter='RECOVERY_BELOW_BASELINE_FLOOR',
                current_value=TUNABLE_PARAMETERS['RECOVERY_BELOW_BASELINE_FLOOR'].current_value,
                recommended_value=min(0.8, TUNABLE_PARAMETERS['RECOVERY_BELOW_BASELINE_FLOOR'].current_value + 0.1),
                reason='Increase recovery floor to prevent extreme tilt states',
                confidence='high',
                priority=1,
            ))

        # Check for no issues
        if not issues:
            issues.append("All tilt bands within target ranges - no adjustments needed")

        return AnalysisResult(
            experiment_id=experiment_id,
            total_decisions=summary['total_decisions'],
            tilt_band_comparison=comparison,
            issues_detected=issues,
            recommendations=recommendations,
            aggregate_tilt=aggregate_tilt,
        )

    def recommend_adjustments(
        self, analysis: AnalysisResult
    ) -> List[TuningRecommendation]:
        """
        Get sorted recommendations from an analysis result.

        Args:
            analysis: AnalysisResult from analyze_experiment()

        Returns:
            List of recommendations sorted by priority
        """
        # Deduplicate by parameter (keep highest priority)
        seen = {}
        for rec in analysis.recommendations:
            if rec.parameter not in seen or rec.priority < seen[rec.parameter].priority:
                seen[rec.parameter] = rec

        return sorted(seen.values(), key=lambda r: r.priority)

    def get_parameter_summary(self) -> Dict[str, Dict[str, Any]]:
        """
        Get a summary of all tunable parameters.

        Returns:
            Dictionary of parameter name to info dict
        """
        return {
            name: {
                'current': param.current_value,
                'min': param.min_value,
                'max': param.max_value,
                'description': param.description,
                'category': param.category,
            }
            for name, param in TUNABLE_PARAMETERS.items()
        }

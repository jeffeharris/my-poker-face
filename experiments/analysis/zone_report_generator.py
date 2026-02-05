"""
Zone Report Generator for Psychology System Experiments.

Generates markdown reports summarizing zone metrics, tilt band
distributions, and comparison to PRD targets.
"""

from datetime import datetime
from typing import Dict, Any, Optional

from .zone_metrics_analyzer import (
    ZoneMetricsAnalyzer,
    ZoneDistribution,
    TiltBandDistribution,
    PRD_TARGETS,
)
from experiments.tuning.zone_parameter_tuner import ZoneParameterTuner


class ZoneReportGenerator:
    """
    Generates markdown reports for psychology zone experiments.

    Reports include:
    - Summary section with experiment metadata
    - Tilt band distribution vs PRD targets
    - Per-player zone distributions
    - Issues detected
    - Tuning recommendations (if any)
    """

    def __init__(self, db_path: str):
        """
        Initialize the generator.

        Args:
            db_path: Path to the SQLite database
        """
        self.db_path = db_path
        self.analyzer = ZoneMetricsAnalyzer(db_path)
        self.tuner = ZoneParameterTuner(db_path)

    def generate_report(
        self,
        experiment_id: int,
        include_recommendations: bool = True,
    ) -> str:
        """
        Generate a markdown report for an experiment.

        Args:
            experiment_id: ID of the experiment to report on
            include_recommendations: Whether to include tuning recommendations

        Returns:
            Markdown-formatted report string
        """
        summary = self.analyzer.get_experiment_summary(experiment_id)
        analysis = self.tuner.analyze_experiment(experiment_id)

        lines = []

        # Header
        lines.append(f"# Psychology Zone System Report")
        lines.append(f"")
        lines.append(f"**Experiment ID**: {experiment_id}")
        lines.append(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**Total Decisions Analyzed**: {summary['total_decisions']}")
        lines.append(f"**Total Zone Transitions**: {summary['total_transitions']}")
        lines.append(f"")

        # Summary section
        lines.append("## Summary")
        lines.append("")
        lines.append(self._generate_summary_section(analysis))
        lines.append("")

        # Tilt band distribution table
        lines.append("## Tilt Band Distribution")
        lines.append("")
        lines.append(self._generate_tilt_band_table(analysis))
        lines.append("")

        # Per-player zone distribution
        lines.append("## Player Zone Distributions")
        lines.append("")
        lines.append(self._generate_player_zone_table(summary['zone_distributions']))
        lines.append("")

        # Per-player tilt bands
        lines.append("## Player Tilt Bands")
        lines.append("")
        lines.append(self._generate_player_tilt_table(summary['tilt_bands']))
        lines.append("")

        # Intrusive thought stats
        if summary['intrusive_thought_stats']:
            lines.append("## Intrusive Thought Injection")
            lines.append("")
            lines.append(self._generate_thought_stats_table(summary['intrusive_thought_stats']))
            lines.append("")

        # Issues
        lines.append("## Issues Detected")
        lines.append("")
        if analysis.issues_detected:
            for issue in analysis.issues_detected:
                status = "✅" if "no adjustments needed" in issue.lower() else "⚠️"
                lines.append(f"- {status} {issue}")
        else:
            lines.append("- ✅ No issues detected")
        lines.append("")

        # Recommendations
        if include_recommendations and analysis.recommendations:
            lines.append("## Tuning Recommendations")
            lines.append("")
            lines.append("*These are informational recommendations - manual review required before applying.*")
            lines.append("")
            lines.append(self._generate_recommendations_table(analysis.recommendations))
            lines.append("")

        return "\n".join(lines)

    def _generate_summary_section(self, analysis) -> str:
        """Generate the summary section."""
        comparison = analysis.tilt_band_comparison
        aggregate = analysis.aggregate_tilt

        # Count pass/fail
        passed = sum(1 for v in comparison.values() if v['status'] == 'pass')
        warned = sum(1 for v in comparison.values() if v['status'] == 'warn')
        failed = sum(1 for v in comparison.values() if v['status'] == 'fail')

        status_emoji = "✅" if failed == 0 else "⚠️" if warned > 0 else "❌"

        lines = []
        lines.append(f"{status_emoji} **Overall Status**: {passed}/4 bands within target")
        lines.append(f"")
        lines.append(f"| Metric | Value | Target | Status |")
        lines.append(f"|--------|-------|--------|--------|")

        band_names = {
            'baseline': 'Baseline (stable)',
            'medium': 'Medium Tilt',
            'high': 'High Tilt',
            'full_tilt': 'Full Tilt',
        }

        for band, name in band_names.items():
            data = comparison[band]
            value = data['value']
            target = f"{data['target_min']:.0%}-{data['target_max']:.0%}"
            status = {'pass': '✅', 'warn': '⚠️', 'fail': '❌'}[data['status']]
            lines.append(f"| {name} | {value:.1%} | {target} | {status} |")

        return "\n".join(lines)

    def _generate_tilt_band_table(self, analysis) -> str:
        """Generate the tilt band comparison table."""
        comparison = analysis.tilt_band_comparison
        aggregate = analysis.aggregate_tilt

        lines = []
        lines.append("| Band | Actual | PRD Target | Difference | Status |")
        lines.append("|------|--------|------------|------------|--------|")

        bands = [
            ('Baseline', 'baseline', aggregate.baseline),
            ('Medium (10-50%)', 'medium', aggregate.medium),
            ('High (50-75%)', 'high', aggregate.high),
            ('Full Tilt (75%+)', 'full_tilt', aggregate.full_tilt),
        ]

        for name, key, value in bands:
            data = comparison[key]
            target_mid = (data['target_min'] + data['target_max']) / 2
            diff = value - target_mid
            diff_str = f"+{diff:.1%}" if diff > 0 else f"{diff:.1%}"
            status = {'pass': '✅ Pass', 'warn': '⚠️ Warn', 'fail': '❌ Fail'}[data['status']]
            lines.append(f"| {name} | {value:.1%} | {data['target_min']:.0%}-{data['target_max']:.0%} | {diff_str} | {status} |")

        return "\n".join(lines)

    def _generate_player_zone_table(
        self, distributions: Dict[str, ZoneDistribution]
    ) -> str:
        """Generate per-player zone distribution table."""
        lines = []
        lines.append("| Player | Decisions | Poker Face | Commanding | Aggro | Guarded | Neutral |")
        lines.append("|--------|-----------|------------|------------|-------|---------|---------|")

        for player, dist in sorted(distributions.items()):
            pf = dist.sweet_spots.get('poker_face', 0)
            cmd = dist.sweet_spots.get('commanding', 0)
            aggro = dist.sweet_spots.get('aggro', 0)
            guard = dist.sweet_spots.get('guarded', 0)
            neutral = dist.neutral_percentage

            lines.append(
                f"| {player} | {dist.total_decisions} | "
                f"{pf:.1%} | {cmd:.1%} | {aggro:.1%} | {guard:.1%} | {neutral:.1%} |"
            )

        return "\n".join(lines)

    def _generate_player_tilt_table(
        self, tilt_bands: Dict[str, TiltBandDistribution]
    ) -> str:
        """Generate per-player tilt band table."""
        lines = []
        lines.append("| Player | Baseline | Medium | High | Full Tilt |")
        lines.append("|--------|----------|--------|------|-----------|")

        for player, dist in sorted(tilt_bands.items()):
            # Color-code based on target compliance
            baseline_status = "✅" if 0.70 <= dist.baseline <= 0.85 else ""
            medium_status = "✅" if 0.10 <= dist.medium <= 0.20 else ""
            high_status = "✅" if 0.02 <= dist.high <= 0.07 else ""
            full_status = "✅" if dist.full_tilt <= 0.02 else "⚠️" if dist.full_tilt <= 0.05 else ""

            lines.append(
                f"| {player} | {baseline_status}{dist.baseline:.1%} | "
                f"{medium_status}{dist.medium:.1%} | {high_status}{dist.high:.1%} | "
                f"{full_status}{dist.full_tilt:.1%} |"
            )

        return "\n".join(lines)

    def _generate_thought_stats_table(
        self, stats: Dict[str, Dict[str, Any]]
    ) -> str:
        """Generate intrusive thought statistics table."""
        lines = []
        lines.append("| Player | Decisions | Injections | Rate |")
        lines.append("|--------|-----------|------------|------|")

        for player, data in sorted(stats.items()):
            lines.append(
                f"| {player} | {data['total_decisions']} | "
                f"{data['injections']} | {data['injection_rate']:.1%} |"
            )

        return "\n".join(lines)

    def _generate_recommendations_table(self, recommendations) -> str:
        """Generate tuning recommendations table."""
        if not recommendations:
            return "*No recommendations at this time.*"

        lines = []
        lines.append("| Priority | Parameter | Current | Recommended | Reason | Confidence |")
        lines.append("|----------|-----------|---------|-------------|--------|------------|")

        for rec in sorted(recommendations, key=lambda r: r.priority):
            priority_emoji = {1: '🔴 High', 2: '🟡 Medium', 3: '🟢 Low'}[rec.priority]
            conf_emoji = {'high': '🟢', 'medium': '🟡', 'low': '🔴'}[rec.confidence]
            lines.append(
                f"| {priority_emoji} | `{rec.parameter}` | "
                f"{rec.current_value:.2f} | {rec.recommended_value:.2f} | "
                f"{rec.reason} | {conf_emoji} {rec.confidence} |"
            )

        return "\n".join(lines)

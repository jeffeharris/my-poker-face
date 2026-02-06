#!/usr/bin/env python3
"""Monitor and compare zone tuning experiments."""

import sqlite3
import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from experiments.analysis.zone_metrics_analyzer import ZoneMetricsAnalyzer, PRD_TARGETS


def get_db_path():
    """Get the database path."""
    if (project_root / "data").exists():
        return str(project_root / "data" / "poker_games.db")
    return str(project_root / "poker_games.db")


def check_experiment_status(conn, experiment_ids):
    """Check status of experiments."""
    cursor = conn.cursor()
    placeholders = ','.join('?' * len(experiment_ids))
    cursor.execute(f'''
        SELECT id, name, status
        FROM experiments
        WHERE id IN ({placeholders})
    ''', experiment_ids)
    return {row[0]: {'name': row[1], 'status': row[2]} for row in cursor.fetchall()}


def generate_comparison_report(db_path, experiment_ids):
    """Generate comparative report for all experiments."""
    analyzer = ZoneMetricsAnalyzer(db_path)

    print("\n" + "=" * 80)
    print("ZONE TUNING EXPERIMENTS - COMPARATIVE REPORT")
    print("=" * 80)

    # PRD Targets
    print("\nPRD TARGETS:")
    print(f"  Baseline:   {PRD_TARGETS['baseline'][0]:.0%} - {PRD_TARGETS['baseline'][1]:.0%}")
    print(f"  Medium:     {PRD_TARGETS['medium'][0]:.0%} - {PRD_TARGETS['medium'][1]:.0%}")
    print(f"  High:       {PRD_TARGETS['high'][0]:.0%} - {PRD_TARGETS['high'][1]:.0%}")
    print(f"  Full Tilt:  {PRD_TARGETS['full_tilt'][0]:.0%} - {PRD_TARGETS['full_tilt'][1]:.0%}")

    results = []
    for exp_id in experiment_ids:
        try:
            summary = analyzer.get_experiment_summary(exp_id)
            results.append({
                'id': exp_id,
                'name': summary.get('name', f'Experiment {exp_id}'),
                'decisions': summary['total_decisions'],
                'tilt': summary['aggregate_tilt'],
                'comparison': summary['target_comparison'],
            })
        except Exception as e:
            print(f"\nWarning: Could not analyze experiment {exp_id}: {e}")

    if not results:
        print("\nNo completed experiments to compare.")
        return

    # Comparison table
    print("\n" + "-" * 80)
    print(f"{'Experiment':<35} {'Baseline':>10} {'Medium':>10} {'High':>10} {'Full':>10}")
    print("-" * 80)

    for r in results:
        tilt = r['tilt']
        name = r['name'].split('_')[2] if '_' in r['name'] else r['name'][:20]

        # Format with status indicators
        def fmt(band, value):
            target_min, target_max = PRD_TARGETS[band]
            if target_min <= value <= target_max:
                return f"{value:>8.1%} ✓"
            return f"{value:>8.1%} ✗"

        print(f"{name:<35} {fmt('baseline', tilt.baseline):>10} {fmt('medium', tilt.medium):>10} "
              f"{fmt('high', tilt.high):>10} {fmt('full_tilt', tilt.full_tilt):>10}")

    # Best performer per metric
    print("\n" + "-" * 80)
    print("ANALYSIS:")

    metrics = ['baseline', 'medium', 'high', 'full_tilt']
    for metric in metrics:
        target_min, target_max = PRD_TARGETS[metric]
        best = None
        best_distance = float('inf')

        for r in results:
            tilt = r['tilt']
            value = getattr(tilt, metric)
            target_mid = (target_min + target_max) / 2
            distance = abs(value - target_mid)
            if distance < best_distance:
                best_distance = distance
                best = r

        if best:
            value = getattr(best['tilt'], metric)
            status = "✓" if target_min <= value <= target_max else "✗"
            name = best['name'].split('_')[2] if '_' in best['name'] else best['name'][:20]
            print(f"  Best {metric}: {name} ({value:.1%}) {status}")

    print("\n" + "=" * 80)


def main():
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)

    # Find the latest tuning experiments
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, name, status
        FROM experiments
        WHERE name LIKE 'zone_tuning_%'
        ORDER BY id DESC
        LIMIT 6
    ''')
    experiments = cursor.fetchall()

    if not experiments:
        print("No zone tuning experiments found.")
        return

    experiment_ids = [e[0] for e in experiments]

    # Check status
    print("Monitoring Zone Tuning Experiments...")
    print("-" * 50)

    all_complete = False
    while not all_complete:
        status = check_experiment_status(conn, experiment_ids)

        running = [eid for eid, info in status.items() if info['status'] == 'running']
        completed = [eid for eid, info in status.items() if info['status'] == 'completed']

        print(f"\rRunning: {len(running)} | Completed: {len(completed)}", end="", flush=True)

        if not running:
            all_complete = True
            print("\n")
        else:
            time.sleep(15)

    # Generate report
    completed_ids = [eid for eid, info in check_experiment_status(conn, experiment_ids).items()
                     if info['status'] == 'completed']

    if completed_ids:
        generate_comparison_report(db_path, completed_ids)

    conn.close()


if __name__ == "__main__":
    main()

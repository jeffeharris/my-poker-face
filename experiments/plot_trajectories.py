#!/usr/bin/env python3
"""
Plot player emotional trajectories through confidence/composure space.

Usage:
    python -m experiments.plot_trajectories --experiment 21 --output trajectory.png
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

# Zone centers and radii
ZONE_CENTERS = {
    'guarded': (0.28, 0.72),
    'poker_face': (0.52, 0.72),
    'commanding': (0.78, 0.78),
    'aggro': (0.68, 0.48),
}

ZONE_RADII = {
    'guarded': 0.15,
    'poker_face': 0.16,
    'commanding': 0.14,
    'aggro': 0.12,
}

# Penalty thresholds
PENALTY_TILTED = 0.35
PENALTY_OVERCONFIDENT = 0.95


def get_trajectory_data(db_path: str, experiment_id: int):
    """Fetch trajectory data from database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get one point per hand per player (first decision of each hand)
    cursor.execute('''
        SELECT player_name, hand_number, zone_confidence, zone_composure,
               zone_primary_sweet_spot, zone_primary_penalty
        FROM player_decision_analysis pda
        JOIN experiment_games eg ON pda.game_id = eg.game_id
        WHERE eg.experiment_id = ?
          AND zone_confidence IS NOT NULL
        GROUP BY pda.game_id, player_name, hand_number
        ORDER BY player_name, hand_number
    ''', (experiment_id,))

    trajectories = {}
    for row in cursor:
        player = row['player_name']
        if player not in trajectories:
            trajectories[player] = {
                'hands': [],
                'confidence': [],
                'composure': [],
                'sweet_spot': [],
                'penalty': [],
            }
        trajectories[player]['hands'].append(row['hand_number'])
        trajectories[player]['confidence'].append(row['zone_confidence'])
        trajectories[player]['composure'].append(row['zone_composure'])
        trajectories[player]['sweet_spot'].append(row['zone_primary_sweet_spot'])
        trajectories[player]['penalty'].append(row['zone_primary_penalty'])

    conn.close()
    return trajectories


def plot_trajectories(trajectories: dict, output_path: str, experiment_id: int):
    """Create trajectory visualization."""
    fig, ax = plt.subplots(figsize=(12, 10))

    # Draw zone boundaries
    # Sweet spot circles
    zone_colors = {
        'poker_face': '#90EE90',  # Light green
        'guarded': '#87CEEB',     # Light blue
        'commanding': '#FFD700',  # Gold
        'aggro': '#FFA500',       # Orange
    }

    for zone_name, center in ZONE_CENTERS.items():
        radius = ZONE_RADII[zone_name]
        circle = plt.Circle(center, radius, fill=True, alpha=0.2,
                          color=zone_colors[zone_name], label=f'{zone_name} zone')
        ax.add_patch(circle)
        ax.annotate(zone_name, center, ha='center', va='center', fontsize=8, alpha=0.7)

    # Penalty zone shading
    # Tilted (bottom edge)
    ax.axhspan(0, PENALTY_TILTED, alpha=0.1, color='red', label='Tilted zone')
    ax.axhline(y=PENALTY_TILTED, color='red', linestyle='--', alpha=0.5, linewidth=1)

    # Overconfident (right edge)
    ax.axvspan(PENALTY_OVERCONFIDENT, 1.0, alpha=0.1, color='purple', label='Overconfident zone')
    ax.axvline(x=PENALTY_OVERCONFIDENT, color='purple', linestyle='--', alpha=0.5, linewidth=1)

    # Plot trajectories
    colors = plt.cm.tab10(np.linspace(0, 1, len(trajectories)))

    for (player, data), color in zip(trajectories.items(), colors):
        conf = data['confidence']
        comp = data['composure']

        # Plot line
        ax.plot(conf, comp, '-', color=color, alpha=0.6, linewidth=1.5, label=player)

        # Mark start and end
        ax.scatter(conf[0], comp[0], color=color, s=100, marker='o', edgecolor='black', zorder=5)
        ax.scatter(conf[-1], comp[-1], color=color, s=100, marker='s', edgecolor='black', zorder=5)

        # Add arrows to show direction (every 10 points)
        for i in range(0, len(conf) - 1, max(1, len(conf) // 10)):
            dx = conf[min(i+1, len(conf)-1)] - conf[i]
            dy = comp[min(i+1, len(comp)-1)] - comp[i]
            if abs(dx) > 0.001 or abs(dy) > 0.001:
                ax.annotate('', xy=(conf[i] + dx*0.5, comp[i] + dy*0.5),
                           xytext=(conf[i], comp[i]),
                           arrowprops=dict(arrowstyle='->', color=color, alpha=0.4))

    # Labels and formatting
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel('Confidence', fontsize=12)
    ax.set_ylabel('Composure', fontsize=12)
    ax.set_title(f'Player Emotional Trajectories - Experiment {experiment_id}', fontsize=14)
    ax.set_aspect('equal')
    ax.grid(True, alpha=0.3)

    # Legend
    ax.legend(loc='upper left', bbox_to_anchor=(1.02, 1), fontsize=9)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()

    print(f"Saved trajectory plot to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Plot player emotional trajectories")
    parser.add_argument("--experiment", "-e", type=int, required=True, help="Experiment ID")
    parser.add_argument("--output", "-o", default="trajectory.png", help="Output file path")
    parser.add_argument("--db", default=None, help="Database path")

    args = parser.parse_args()

    # Find database
    if args.db:
        db_path = args.db
    else:
        project_root = Path(__file__).parent.parent
        if (project_root / "data").exists():
            db_path = str(project_root / "data" / "poker_games.db")
        else:
            db_path = str(project_root / "poker_games.db")

    trajectories = get_trajectory_data(db_path, args.experiment)

    if not trajectories:
        print(f"No trajectory data found for experiment {args.experiment}")
        sys.exit(1)

    print(f"Found {len(trajectories)} players:")
    for player, data in trajectories.items():
        print(f"  {player}: {len(data['hands'])} data points")

    plot_trajectories(trajectories, args.output, args.experiment)


if __name__ == "__main__":
    main()

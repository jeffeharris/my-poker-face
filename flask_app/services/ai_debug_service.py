"""AI Debug service for retrieving LLM usage statistics per player.

This module queries the api_usage table to provide aggregated stats
for AI players, used in the debug card flip feature.
"""

import logging
import sqlite3
from typing import Dict, Any, Optional

from flask_app.config import DB_PATH

logger = logging.getLogger(__name__)


def get_player_llm_stats(game_id: str, player_name: str) -> Optional[Dict[str, Any]]:
    """Get aggregated LLM stats for a specific AI player in a game.

    Queries the api_usage table for player_decision calls and aggregates
    latency and cost metrics.

    Args:
        game_id: The game ID
        player_name: The AI player name

    Returns:
        Dictionary with provider, model, reasoning_effort, avg_latency_ms,
        avg_cost_per_call, total_calls. Returns None if no data found.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row

            cursor = conn.execute("""
                SELECT
                    provider,
                    model,
                    reasoning_effort,
                    COUNT(*) as total_calls,
                    AVG(latency_ms) as avg_latency_ms,
                    AVG(COALESCE(estimated_cost, 0)) as avg_cost_per_call
                FROM api_usage
                WHERE game_id = ?
                  AND player_name = ?
                  AND call_type = 'player_decision'
                GROUP BY provider, model, reasoning_effort
                ORDER BY COUNT(*) DESC
                LIMIT 1
            """, (game_id, player_name))

            row = cursor.fetchone()
            if not row:
                return None

            return {
                'provider': row['provider'],
                'model': row['model'],
                'reasoning_effort': row['reasoning_effort'],
                'total_calls': row['total_calls'],
                'avg_latency_ms': round(row['avg_latency_ms'], 0) if row['avg_latency_ms'] else 0,
                'avg_cost_per_call': round(row['avg_cost_per_call'], 6) if row['avg_cost_per_call'] else 0,
            }

    except Exception as e:
        logger.error(f"Error fetching LLM stats for {player_name} in game {game_id}: {e}")
        return None


def get_all_players_llm_stats(game_id: str, player_names: list) -> Dict[str, Dict[str, Any]]:
    """Get LLM stats for multiple AI players in a single query.

    More efficient than calling get_player_llm_stats for each player.

    Args:
        game_id: The game ID
        player_names: List of AI player names

    Returns:
        Dictionary mapping player_name to their LLM stats
    """
    if not player_names:
        return {}

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row

            # Build placeholder string for IN clause
            placeholders = ','.join('?' * len(player_names))

            cursor = conn.execute(f"""
                SELECT
                    player_name,
                    provider,
                    model,
                    reasoning_effort,
                    COUNT(*) as total_calls,
                    AVG(latency_ms) as avg_latency_ms,
                    AVG(COALESCE(estimated_cost, 0)) as avg_cost_per_call
                FROM api_usage
                WHERE game_id = ?
                  AND player_name IN ({placeholders})
                  AND call_type = 'player_decision'
                GROUP BY player_name, provider, model, reasoning_effort
                ORDER BY player_name, COUNT(*) DESC
            """, [game_id] + player_names)

            # Group by player, taking the most common model config
            results = {}
            for row in cursor:
                name = row['player_name']
                if name not in results:  # First row per player has highest count
                    results[name] = {
                        'provider': row['provider'],
                        'model': row['model'],
                        'reasoning_effort': row['reasoning_effort'],
                        'total_calls': row['total_calls'],
                        'avg_latency_ms': round(row['avg_latency_ms'], 0) if row['avg_latency_ms'] else 0,
                        'avg_cost_per_call': round(row['avg_cost_per_call'], 6) if row['avg_cost_per_call'] else 0,
                    }

            return results

    except Exception as e:
        logger.error(f"Error fetching LLM stats for players in game {game_id}: {e}")
        return {}

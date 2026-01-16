"""Elasticity service for formatting and managing personality elasticity data.

This module provides centralized functions for formatting elasticity data
for API responses and WebSocket emissions.
"""

from typing import Dict, Any


def format_elasticity_data(elasticity_manager) -> Dict[str, Any]:
    """Format elasticity data from an ElasticityManager for API/WebSocket emission.

    Args:
        elasticity_manager: The ElasticityManager instance

    Returns:
        Dictionary with player names as keys, containing traits and mood data
    """
    elasticity_data = {}

    for name, personality in elasticity_manager.personalities.items():
        traits_data = {}
        for trait_name, trait in personality.traits.items():
            traits_data[trait_name] = {
                'current': trait.value,
                'anchor': trait.anchor,
                'elasticity': trait.elasticity,
                'pressure': trait.pressure,
                'min': trait.min,
                'max': trait.max
            }

        elasticity_data[name] = {
            'traits': traits_data,
            'mood': personality.get_current_mood()
        }

    return elasticity_data

"""Psychology service for formatting player psychology data.

This module provides centralized functions for formatting psychology data
for API responses and WebSocket emissions.

Uses Psychology System v2.1 (anchors + axes).
"""

from typing import Dict, Any


def format_elasticity_data(ai_controllers: Dict[str, Any]) -> Dict[str, Any]:
    """Format psychology data from AI controllers for API/WebSocket emission.

    Maps the new psychology system (anchors + axes) to a format compatible
    with the frontend's elasticity display.

    Args:
        ai_controllers: Dictionary mapping player names to AIPlayerController instances

    Returns:
        Dictionary with player names as keys, containing traits and mood data
    """
    elasticity_data = {}

    for name, controller in ai_controllers.items():
        if not hasattr(controller, 'psychology'):
            continue

        psych = controller.psychology

        # Map new system to old trait format for frontend compatibility
        # The frontend expects: current, anchor, elasticity, pressure, min, max
        traits_data = {
            'tightness': {
                'current': 1.0 - psych.effective_looseness,
                'anchor': 1.0 - psych.anchors.baseline_looseness,
                'elasticity': 0.3,
                'pressure': (1.0 - psych.effective_looseness) - (1.0 - psych.anchors.baseline_looseness),
                'min': 0.0,
                'max': 1.0,
            },
            'aggression': {
                'current': psych.effective_aggression,
                'anchor': psych.anchors.baseline_aggression,
                'elasticity': 0.5,
                'pressure': psych.effective_aggression - psych.anchors.baseline_aggression,
                'min': 0.0,
                'max': 1.0,
            },
            'confidence': {
                'current': psych.axes.confidence,
                'anchor': psych._baseline_confidence,
                'elasticity': 0.4,
                'pressure': psych.axes.confidence - psych._baseline_confidence,
                'min': 0.0,
                'max': 1.0,
            },
            'composure': {
                'current': psych.axes.composure,
                'anchor': psych._baseline_composure,
                'elasticity': 0.4,
                'pressure': psych.axes.composure - psych._baseline_composure,
                'min': 0.0,
                'max': 1.0,
            },
            'table_talk': {
                'current': psych.anchors.expressiveness,
                'anchor': psych.anchors.expressiveness,
                'elasticity': 0.6,
                'pressure': 0.0,
                'min': 0.0,
                'max': 1.0,
            },
        }

        elasticity_data[name] = {
            'traits': traits_data,
            'mood': psych.mood
        }

    return elasticity_data

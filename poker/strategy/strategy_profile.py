"""
StrategyProfile: action probability distribution for one decision point.
"""

import random
from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class StrategyProfile:
    """Action probability distribution for one decision point."""
    action_probabilities: Dict[str, float]
    # e.g. {'fold': 0.3, 'call': 0.5, 'raise_2.5bb': 0.15, 'jam': 0.05}

    def sample_action(self, rng: random.Random) -> str:
        """Sample an action from the probability distribution.

        Args:
            rng: Seeded random.Random instance for reproducibility.

        Returns:
            Selected action string.
        """
        actions = list(self.action_probabilities.keys())
        weights = [self.action_probabilities[a] for a in actions]

        # Guard against all-zero weights
        total = sum(weights)
        if total <= 0:
            raise ValueError("Cannot sample from zero-weight distribution")

        return rng.choices(actions, weights=weights, k=1)[0]

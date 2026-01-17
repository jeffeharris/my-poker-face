"""
Pause Coordinator for Experiment Tournaments

Thread-safe pause flag coordinator for cooperative pausing of experiment tournaments.
Allows pause requests to be signaled and checked across parallel workers.
"""

import threading
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class PauseCoordinator:
    """Thread-safe coordinator for pause/resume signals across experiment workers.

    Each experiment has its own pause flag that workers check after each action.
    This enables cooperative pausing where workers stop cleanly after completing
    their current action.

    Usage:
        coordinator = PauseCoordinator()

        # In API endpoint:
        coordinator.request_pause(experiment_id)

        # In tournament worker (after each action):
        if coordinator.should_pause(experiment_id):
            return False  # Signal tournament should stop

        # When resuming:
        coordinator.clear_pause(experiment_id)
    """
    _pause_flags: Dict[int, bool] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def request_pause(self, experiment_id: int) -> None:
        """Signal that an experiment should pause.

        Workers will stop after completing their current action.

        Args:
            experiment_id: The experiment ID to pause
        """
        with self._lock:
            self._pause_flags[experiment_id] = True

    def clear_pause(self, experiment_id: int) -> None:
        """Clear the pause flag for an experiment.

        Called when resuming an experiment to allow workers to continue.

        Args:
            experiment_id: The experiment ID to clear
        """
        with self._lock:
            self._pause_flags.pop(experiment_id, None)

    def should_pause(self, experiment_id: int) -> bool:
        """Check if an experiment should pause.

        Workers should call this after each action to check for pause requests.

        Args:
            experiment_id: The experiment ID to check

        Returns:
            True if the experiment should pause, False otherwise
        """
        with self._lock:
            return self._pause_flags.get(experiment_id, False)

    def is_paused(self, experiment_id: int) -> bool:
        """Alias for should_pause for semantic clarity in status checks.

        Args:
            experiment_id: The experiment ID to check

        Returns:
            True if a pause is requested for this experiment
        """
        return self.should_pause(experiment_id)

    def get_paused_experiments(self) -> list:
        """Get list of all experiment IDs with active pause requests.

        Returns:
            List of experiment IDs that are paused
        """
        with self._lock:
            return [exp_id for exp_id, paused in self._pause_flags.items() if paused]


# Global singleton for use across the application
# This is shared between API endpoints and background workers
pause_coordinator = PauseCoordinator()

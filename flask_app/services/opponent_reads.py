"""Re-export shim — the canonical owner is `poker.memory.opponent_reads`.

The read-shaping moved into the `poker/` package so the in-game strategy /
expression layer (the SHARP/TIERED bot's spoken-read surfacing) can import it
without a backwards `poker -> flask_app` dependency. This module keeps the
historical import path (`flask_app.services.opponent_reads`) working for the
dossier route, the coach, and existing tests — single definition, no copy.
"""

from poker.memory.opponent_reads import (  # noqa: F401
    deep_reads_from_tendencies,
    reconstruct_tendencies_from_lifetime,
)

__all__ = [
    'deep_reads_from_tendencies',
    'reconstruct_tendencies_from_lifetime',
]

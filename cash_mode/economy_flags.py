"""Cash-mode economy toggles — central place for A/B-able knobs.

These exist so we can experiment with closing the chip universe (or
balancing the faucet against the sink) without rewriting call sites.
Three independent variables:

  - `REGEN_ENABLED`: the passive faucet. When False, `project_bankroll`
    returns stored chips verbatim — AIs no longer accrue chips while
    idle. Pair with the staking/loan system as the AI recovery
    mechanism, or keep regen on and balance it with rake.

  - `RAKE_ENABLED`: the table-side sink. When True, a fraction of every
    pot is destroyed (ledger reason `table_rake`, sink = central_bank)
    at award time. Default off so the live deployment behaviour is
    unchanged until we flip it.

  - `RAKE_PLAYER_TABLES`: when False, rake only fires at AI-only sim
    tables (`cash_mode/full_sim.play_one_hand`). When True, the same
    rake also applies at tables with a human seated. Keeping this off
    preserves the "sandbox" feel for players.

Tuning levers:

  - `RAKE_RATE`: fraction of pot destroyed per hand. 0.02 = 2%.
  - `RAKE_CAP_BB`: hard cap on rake per hand, expressed in big blinds.
    Mirrors the cap real cardrooms enforce so a single huge pot can't
    delete half the universe.

All values are module-level globals so tests can monkeypatch them
without plumbing config objects through. Production deployments can
override via a startup hook if/when we want runtime control.
"""

from __future__ import annotations


# --- Faucet ---------------------------------------------------------------

REGEN_ENABLED: bool = True


# --- Sink (table rake) ----------------------------------------------------

RAKE_ENABLED: bool = True
RAKE_PLAYER_TABLES: bool = True
RAKE_RATE: float = 0.02
RAKE_CAP_BB: int = 4


def compute_rake(pot: int, big_blind: int) -> int:
    """Pure helper — returns the rake amount for a given pot.

    Returns 0 when rake is disabled, the pot is non-positive, or
    big_blind is non-positive. The cap is applied in chip terms
    (`RAKE_CAP_BB * big_blind`) so it scales with the table's stake.
    """
    if not RAKE_ENABLED:
        return 0
    if pot <= 0 or big_blind <= 0:
        return 0
    raw = int(pot * RAKE_RATE)
    cap = RAKE_CAP_BB * big_blind
    return min(raw, cap)

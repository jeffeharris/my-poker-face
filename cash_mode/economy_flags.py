"""Cash-mode economy toggles — central place for A/B-able knobs.

These exist so we can experiment with closing the chip universe (or
balancing the faucet against the sink) without rewriting call sites.
Three independent variables:

  - `REGEN_ENABLED`: the passive faucet. When False, `project_bankroll`
    returns stored chips verbatim — AIs no longer accrue chips while
    idle. **Default False** as of CASH_MODE_SIDE_HUSTLE.md: passive regen
    is retired in favour of the active side hustle (`SIDE_HUSTLE_ENABLED`),
    where broke AIs go off-grid to earn a pool-funded lump. The
    projection machinery is kept (flip back to True to A/B the old
    passive faucet), but production runs with it off.

  - `SIDE_HUSTLE_ENABLED`: the active faucet that replaces passive regen.
    When True, broke AIs (those who can't afford to play) are sent to an
    off-grid side hustle on lobby refresh and return with a lump drawn
    from the bank pool. See `cash_mode/ai_side_hustle.py`.

  - `RAKE_ENABLED`: the table-side sink. When True, a fraction of every
    pot is destroyed (ledger reason `table_rake`, sink = central_bank)
    at award time. Default off so the live deployment behaviour is
    unchanged until we flip it.

  - `RAKE_PLAYER_TABLES`: when False, rake only fires at AI-only sim
    tables (`cash_mode/full_sim.play_one_hand`). When True, the same
    rake also applies at tables with a human seated. Keeping this off
    preserves the "sandbox" feel for players.

  - `RAKE_STAKE_BIG_BLINDS`: the stake tiers rake applies at, keyed by
    big blind (the 1:1 proxy for a stake label — see
    `cash_mode/stakes_ladder.STAKES_LADDER`). Tier-keyed, not table-keyed,
    so any number of tables at a listed stake rake identically (one $1000
    table or ten, present or future). Default `{1000}` — only the top
    tier rakes, which throttles pool inflow at the high-volume low stakes.
    Add a big blind to the set to rake another tier.

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

# Passive regen retired per CASH_MODE_SIDE_HUSTLE.md — the active side
# hustle is the replacement faucet. Flip back to True only to A/B the old
# passive-accrual behaviour; production runs with it off.
REGEN_ENABLED: bool = False

# The active faucet: broke AIs earn a pool-funded lump via an off-grid
# side hustle (`cash_mode/ai_side_hustle.py`), gated at the lobby refresh.
SIDE_HUSTLE_ENABLED: bool = True


# --- Vice mode (mutually-exclusive 3-state toggle) ------------------------

# Which vice mechanism (if any) drains rich AIs into the bank pool.
# Exactly one of `VICE_MODES` — a single value, so the two mechanisms can
# never both run (the double-drain bug) or silently both be off:
#
#   'real' — the live, LLM-narrated vice (`vice_spending`). Rich AIs go
#            off-grid with character narration + one-shot psych recovery.
#            Needs a vice_repo + an LLM call per fire → the production
#            setting. See `cash_mode/ai_vice_spending.py`.
#   'fake' — the LLM-free / psych-free stub (`bank_pool_deposit`) for
#            sims + testbeds that can't afford an LLM call per tick. Same
#            chip drain, no narration / off-grid / recovery. See
#            `resolve_fake_vice_deposits` in `cash_mode/closed_economy.py`.
#   'off'  — no vice; the pool is fed only by table rake (+ casino returns).
#
# `refresh_unseated_tables` reads this (overridable per-call via its
# `vice_mode` kwarg); the sim forces 'fake' since real vice can't run
# without an LLM. The real-vice *expiry* pass always runs regardless of
# mode, so existing off-grid AIs still return when the mode is switched.
VICE_MODE: str = 'real'
VICE_MODES = ('real', 'fake', 'off')


# --- Sink (table rake) ----------------------------------------------------

RAKE_ENABLED: bool = True
RAKE_PLAYER_TABLES: bool = True
RAKE_RATE: float = 0.02
RAKE_CAP_BB: int = 4

# Stake tiers rake applies at, keyed by big blind (1:1 with a stake
# label — see STAKES_LADDER). Tier-keyed, not table-keyed: every table
# at a listed stake rakes the same, however many there are. Default to
# the top tier only ($1000) to throttle pool inflow at low stakes.
RAKE_STAKE_BIG_BLINDS: frozenset[int] = frozenset({1000})


# --- Player-prestige hook 4: AI demeanor ----------------------------------

# Kill switch for the reputation-driven AI demeanor (player-prestige hook 4).
# When True (default), AIs seated with a HIGH-renown human get a small,
# poise-filtered psychology nudge once per hand: a feared **Infamous Villain**
# rattles low-poise opponents (composure pressure → scared / tilt-prone, an
# exploitable edge), while a **Beloved Legend** loosens them up (a confidence /
# energy lift). The nudge drives both decisions (via the emotional-window
# shift on bounded options) and table-talk demeanor (the expression generator
# reflects the axes), so it's the one prestige hook that touches the decision
# path — hence the dedicated switch. Flip to False to fully disable it with
# zero residual effect (the other prestige hooks — chat tone, backing gating,
# table pull — are unaffected). See `_apply_reputation_demeanor` in
# `flask_app/handlers/game_handler.py` and `docs/plans/CASH_MODE_PLAYER_PRESTIGE.md`.
REPUTATION_DEMEANOR_ENABLED: bool = True

# Number of open seats the live-world greedy fill leaves untouched on
# each table, so a human browsing the lobby always has a seat to sit/
# sponsor into. The world ticker fills aggressively (tick≈2s) and a
# lobby snapshot can be seconds stale, so without headroom the ticker
# wins the race for the last open seat and the player's tap 409s (read
# as a dead Sit/Sponsor button). Set 0 to restore full saturation.
# Passed explicitly as `human_headroom` to `refresh_unseated_tables` by
# the LIVE call sites only — sims default to 0 so closed-economy runs
# still fill tables completely.
LIVE_FILL_HUMAN_HEADROOM: int = 1


# --- Opponent dossier scouting gate (Phase 2) -----------------------------

# Kill switch for the dossier scouting meta-game's grind gate. When True
# (default), the opponent dossier's *earnable* reads (behavioral tendencies,
# track record, table posture) are gated behind hands observed against that
# opponent in the active sandbox: below the floor the file is "classified",
# and individual reads unlock as the sample grows. Identity/standing/notes
# are never gated. Applies ONLY in a Circuit context (a sandbox + observer
# with a lifetime observation row); outside the Circuit the dossier is
# ungated as before. Flip to False to show every read immediately again
# (zero residual effect — the gate is a pure read-time transform). See
# `flask_app/services/dossier_scouting.py` and
# `docs/plans/OPPONENT_DOSSIER_PROGRESSION.md`.
DOSSIER_SCOUTING_GATE_ENABLED: bool = True


def compute_rake(pot: int, big_blind: int) -> int:
    """Pure helper — returns the rake amount for a given pot.

    Returns 0 when rake is disabled, the pot is non-positive,
    big_blind is non-positive, or the stake (keyed by `big_blind`) is
    not in `RAKE_STAKE_BIG_BLINDS`. The cap is applied in chip terms
    (`RAKE_CAP_BB * big_blind`) so it scales with the table's stake.
    """
    if not RAKE_ENABLED:
        return 0
    if pot <= 0 or big_blind <= 0:
        return 0
    if big_blind not in RAKE_STAKE_BIG_BLINDS:
        return 0
    raw = int(pot * RAKE_RATE)
    cap = RAKE_CAP_BB * big_blind
    return min(raw, cap)

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

import os


def _env_flag(name: str, default: bool) -> bool:
    """Read a boolean toggle from the environment, falling back to `default`.

    Lets an operator opt a flag on/off per-deployment (e.g. enable the Presence
    shadow on dev without flipping the committed default — which would also flip
    production on the next deploy). Truthy: 1/true/yes/on (case-insensitive)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


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


# --- Presence machine cutover (dual-write shadow phase) -------------------

# Kill switch for the Presence state-machine SHADOW writes. When True, the
# cash-mode seat / idle / hustle / vice writers ALSO record the corresponding
# transition into `entity_presence` (the dormant Cut-3 table) *alongside* the
# existing authoritative stores — a dual-write used to prove the machine tracks
# reality on live traffic before authority is flipped to it (design Phase 3,
# CASH_MODE_PRESENCE_MIGRATION.md §Sequencing step 1). Default **False**: every
# shadow write is a guarded no-op, so the cutover code is inert until an
# operator opts in. The shadow path is additionally wrapped in try/except
# (`cash_mode/presence_shadow.py`) so even when enabled it can never break the
# real seat write — a shadow failure is logged and swallowed. The authoritative
# stores (`cash_tables`, `cash_idle_pool`, `ai_*_state`) remain the source of
# truth throughout this phase; only the eventual flip (a separate change) makes
# `entity_presence` authoritative.
PRESENCE_SHADOW_WRITE_ENABLED: bool = _env_flag("PRESENCE_SHADOW_WRITE_ENABLED", False)

# Phase 3 — the AUTHORITY flip. When True, `entity_presence` becomes the
# authoritative record of actor location: the seat-write chokepoint
# (`CashTableRepository.save_table`) drives presence transitions inside its own
# transaction (presence + seats commit together), and the old stores
# (`cash_tables` seat map / `cash_idle_pool` / `ai_*_state`) become projections.
# Default **False** — every authoritative presence path is a no-op until the
# operator opts in. This is SEPARATE from the shadow flag above: with authority
# OFF and shadow ON, presence is mirrored best-effort (validation); with
# authority ON, presence is the source of truth. Flipping this to True (in
# `cash_mode/economy_flags.py` or via env) is the single irreversible cut —
# everything else in the cutover is reversible. See
# `docs/plans/CASH_MODE_PRESENCE_PHASE3_FLIP.md`.
PRESENCE_AUTHORITY_ENABLED: bool = _env_flag("PRESENCE_AUTHORITY_ENABLED", True)


# --- Chip-custody machine cutover (the Presence twin) ---------------------

# Kill switch for the chip-custody ledger transfers — the AI side of what Cut 2
# did for humans. When True, the two AI bankroll chokepoints
# (`cash_mode/bankroll.py:debit_bankroll_for_seat` and `credit_ai_cash_out`) ALSO
# record an `ai ↔ seat` transfer into `chip_ledger_entries` alongside the existing
# bankroll int move, so an AI's at-table chips become a derivable ledger balance
# (`seat:ai:<sandbox_id>:<personality_id>`) exactly as a human's are
# (`seat:<game_id>`). Conservation-neutral: the bankroll int still moves; the
# transfer just records it, making AI bankroll ledger-derivable (the foundation
# for D2 / derived bankroll). Stake/carry payoffs (an overloaded second use of
# `credit_ai_cash_out`) record an `ai → ai` transfer instead — see the
# `from_seat` discriminator. Default **False** so every custody path is a guarded
# no-op until an operator opts in (mirror `PRESENCE_AUTHORITY_ENABLED`'s env
# pattern). See `docs/plans/CASH_MODE_CHIP_CUSTODY_SCOPE.md` +
# `docs/plans/CASH_MODE_CHIP_CUSTODY_HANDOFF.md`.
CHIP_CUSTODY_ENABLED: bool = _env_flag("CHIP_CUSTODY_ENABLED", False)

# D2 — ledger-derived bankroll reads. When True, `BankrollRepository.load_*`
# return the LEDGER-DERIVED chip count (Σ over `chip_ledger_entries`) as the
# authoritative value, treating the stored int as a cache and logging any
# divergence. Requires CHIP_CUSTODY_ENABLED (the ledger must be complete) and a
# backfilled DB, else derived reads return wrong values. Default **False**: the
# stored int is the read (transaction-consistent within a chokepoint's single
# save; the ledger row is written immediately after, so a derived read in that
# sub-millisecond window would be momentarily stale — the int avoids that). Flip
# on to make the ledger authoritative for reads after validating int==derived
# via scripts/audit_ledger_completeness.py. See CASH_MODE_CHIP_CUSTODY_SCOPE.md (D2).
CHIP_CUSTODY_DERIVE_READS: bool = _env_flag("CHIP_CUSTODY_DERIVE_READS", False)


# --- Player-prestige Renown-v2 (read-side field scorer) -------------------

# Kill switch for the Renown-v2 field-relative scoreboard. Default **False** —
# the v2 layer (cash_mode/prestige.py: score_renown_field +
# quadrant_label_relative + build_renown_inputs_from_repos) is computed-but-
# UNCONSUMED until this flips. v1's compute_prestige + absolute quadrant_label
# stay the live human path; the 4 reputation hooks keep reading v1's quadrant
# string. This flag is the seam the DEFERRED stage flips: once field-wide
# persistence + ticker surgery land (schema/ticker changes that must be
# sim-stress-validated), the hooks switch from quadrant_label (absolute 0.40)
# to quadrant_label_relative(renown, regard, high_cut) with a zero-residual
# kill switch. NOW it gates nothing live. See
# docs/plans/CASH_MODE_PLAYER_PRESTIGE.md ("v2 implemented" note).
# Env-flippable (committed default stays False so production is unaffected):
# set RENOWN_V2_ENABLED=1 in a dev .env to turn the field-relative gauge on.
RENOWN_V2_ENABLED: bool = _env_flag("RENOWN_V2_ENABLED", False)

# Persist a field-relative renown row for every AI entity (not just the human)
# each ticker recompute. The field scorer already computes every AI's renown and
# discards it; this writes those rows so AI fame can be surfaced (dossier badge,
# marquee table, whereabouts). Pure infrastructure — produces DATA, changes no
# behavior on its own; the consumers are separate (Stage B). IMPLIES
# RENOWN_V2_ENABLED (the overlay only scores the field when that's on). Its own
# kill switch so the per-AI write fan-out can be disabled independently of the
# human gauge. MUST be stress-validated (50+ AIs under CYCLE_BUDGET_MS) before
# enabling on a real field. Default OFF. See
# docs/plans/RENOWN_V2_AI_WIRING_PLAN.md (Stage A).
RENOWN_V2_PERSIST_AI: bool = _env_flag("RENOWN_V2_PERSIST_AI", False)

# Prestige-seeking movement (Renown-v2 B4). When on, the autonomous seat-fill
# adds the marquee term to table attractiveness: status-seeking AIs (own renown
# + showman traits) are pulled toward tables seating high-renown players. Reads
# persisted per-AI renown, so it IMPLIES RENOWN_V2_PERSIST_AI (no renown data →
# the term is 0 → no effect). A real chip-flow/movement change → sim-validate
# before enabling. Own kill switch; default OFF. See
# docs/plans/RENOWN_V2_AI_WIRING_PLAN.md (Stage B / B4).
PRESTIGE_SEEKING_ENABLED: bool = _env_flag("PRESTIGE_SEEKING_ENABLED", False)


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

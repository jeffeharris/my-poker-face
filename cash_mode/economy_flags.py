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

  - `RAKE_ENABLED`: the table-side skim. When True, a fraction of every
    pot is taken at award time (ledger reason `table_rake`). The chips
    are NOT destroyed — `table_rake` is a `BANK_POOL_DEPOSIT_REASON`, so
    the rake RECYCLES into the bank pool that funds fish / side-hustle
    (see CASH_MODE_SIDE_HUSTLE.md). Default ON.

  - `RAKE_PLAYER_TABLES`: when False, rake only fires at AI-only tables
    (`cash_mode/full_sim.play_one_hand`); when True, it also applies at
    tables with a human seated. Currently True — the human IS raked at
    the rake-eligible tiers. (Setting it False would preserve a pure
    "sandbox" feel for players; that's a live product choice.)

  - `RAKE_STAKE_BIG_BLINDS`: the stake tiers rake applies at, keyed by
    big blind (the 1:1 proxy for a stake label — see
    `cash_mode/stakes_ladder.STAKES_LADDER`). Tier-keyed, not table-keyed,
    so any number of tables at a listed stake rake identically (one $1000
    table or ten, present or future). Default `{1000}` — only the top
    tier rakes, which throttles pool inflow at the high-volume low stakes.
    Add a big blind to the set to rake another tier.

Tuning levers:

  - `RAKE_RATE`: fraction of pot skimmed to the pool per hand. 0.02 = 2%.
  - `RAKE_CAP_BB`: hard cap on rake per hand, expressed in big blinds.
    Mirrors the cap real cardrooms enforce so a single huge pot can't
    delete half the universe.

All values are module-level globals so tests can monkeypatch them
without plumbing config objects through. Production deployments can
override via a startup hook if/when we want runtime control.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


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


# --- Lever reference mode (field-relative vs own-start) -------------------
#
# Controls how the three closed-economy wealth levers (real vice,
# side-hustle, grinder-hunger) measure wealth:
#
#   'own_start'    — default; bit-for-bit current behaviour. Each lever
#                    keys off the AI's OWN starting_bankroll (vice off the
#                    cast-median of bankrolls; side-hustle/grinder off
#                    starting_bankroll). Inconsistent and anti-mobility.
#   'field_liquid' — all three key off the FIELD's LIQUID net worth
#                    (bankroll + seat stack) distribution at evaluation
#                    time, via a single per-tick FieldWealthSnapshot:
#                      * vice: concentration = liquid / field-median,
#                        tax above FIELD_CONCENTRATION_FLOOR×
#                      * side-hustle: eligible in the bottom decile of
#                        field liquid; tops up toward a field percentile
#                      * grinder-hunger: hungry below a field percentile
#
# Default 'own_start' so production is unchanged until flipped. The
# levers share one economic model and flip together (no per-lever flag).
LEVER_REFERENCE_MODE: str = os.environ.get('LEVER_REFERENCE_MODE', 'own_start').strip().lower()

# Tunables used only in 'field_liquid' mode (validate in sim before flip):
FIELD_CONCENTRATION_FLOOR: float = 2.5  # vice fires above N× field median
MIN_FIELD_MEDIAN_FOR_VICE: int = 5_000  # suppress vice when field is broke
FIELD_HUSTLE_ELIGIBLE_PERCENTILE: float = 0.10  # bottom 10% of field → hustle candidate
FIELD_HUSTLE_TARGET_PERCENTILE: float = 0.25  # hustle tops up toward this field pct
FIELD_GRINDER_HUNGER_PERCENTILE: float = 0.35  # below this field pct → hungry grinder


# --- Vice reserve-gating (the refill faucet, reserve-aware) ----------------
#
# Vice drains chips from wealthy AIs INTO the bank pool — it is a reserve
# REFILL faucet, the mirror of the side-hustle drain. By default it fires
# whenever the cast/field median clears MIN_*_MEDIAN_FOR_VICE, i.e. from the
# first tick of a freshly-seeded sandbox (median well above $5k) — which reads
# as an arbitrary "tax" on the field even when reserves are already healthy.
#
# When VICE_RESERVE_GATED is on, vice intensity instead scales with the
# bank-pool DEFICIT: off when reserves are healthy (don't tax a flush field),
# ramping to full as reserves fall toward critical. The band EDGES come from the
# shared canonical ladder (`core.economy.economy_signal.RESERVE_HEALTHY` /
# `RESERVE_CRITICAL`), so vice (refill) and rake (throttle) can't drift apart —
# see `ai_vice_spending.reserve_vice_multiplier`.
#
# Default OFF — flip only after sim-validating the cadence alongside the rest
# of the Director thermostat. See docs/plans/PROD_STARTING_CONDITIONS.md §1.5.
VICE_RESERVE_GATED: bool = _env_flag("VICE_RESERVE_GATED", False)


# --- Genesis reserve seed (fresh-sandbox bank pool) ------------------------
#
# A fresh prod sandbox seeds AI bankrolls (holdings) but starts with an EMPTY
# bank pool — so casinos can't spawn (need pool ≥ 5k/50k/100k) and no tournament
# can fire until rake/vice slowly fill it. The world boots economically inert.
#
# When GENESIS_RESERVE_ENABLED is on, `ensure_genesis_reserve_seeded` seeds the
# pool to GENESIS_RESERVE_RATIO of holdings ONCE at sandbox birth (drift-safe
# paired entry), so the world boots lived-in: casinos open, reserves sit below
# the tournament trigger (0.12) and climb into the first Main Event over the
# opening day. 0.05 lands just below RESERVE_HEALTHY (0.06) — the sim validates
# whether to boot in the low or healthy band. Default OFF; needs a prod genesis
# path + sim. See docs/plans/PROD_STARTING_CONDITIONS.md §1.1.
GENESIS_RESERVE_ENABLED: bool = _env_flag("GENESIS_RESERVE_ENABLED", False)
GENESIS_RESERVE_RATIO: float = 0.05  # seed reserve = this × holdings, once at birth


def lever_field_mode() -> bool:
    """True when the levers should use the field-liquid reference.

    Reads the env at call time (not just the import-time module global)
    so a sim/experiment can flip it per-run regardless of import order.
    """
    return (
        os.environ.get('LEVER_REFERENCE_MODE', LEVER_REFERENCE_MODE).strip().lower()
        == 'field_liquid'
    )


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

# Two-layer rake (Director lever). When off (default), rake is the static
# structural $1000-only skim above — a permanent playing-field leveler / money
# sink, NOT the Director's to touch. When on, `resolve_rake_params` lets the
# Director ADD lower-stake tiers ($200, …) and bump the rate as the bank empties
# (via `economy_signal.cash_rake_schedule`), contracting back to $1000-only when
# reserves recover. The $1000 tier is always present, so the structural rake is
# never switched off — only the extra refill layers are reserve-gated. Default
# OFF; flip with the rest of the Director thermostat after sim. See
# docs/plans/PROD_STARTING_CONDITIONS.md §1.4.
RAKE_RESERVE_GATED: bool = _env_flag("RAKE_RESERVE_GATED", False)

# Director instrument choice (inequality-aware rake). Vice drains the rich
# (concentration-gated) and so leads the refill on a TOP-HEAVY field; when the
# top is FLAT there is no runaway to drain, so the rake (even skim) should lead
# instead. When on, a flat field (`p90/median < INEQUALITY_FLAT_THRESHOLD`)
# upgrades the reserve-gated rake by one band so it picks up vice's slack. The
# inequality read is recomputed at most once per INEQUALITY_RECOMPUTE_SECONDS
# (`cash_mode.field_inequality`) — the Director steers slowly, not every tick.
# Implies RAKE_RESERVE_GATED. Default OFF. See PROD_STARTING_CONDITIONS.md §1.2.
DIRECTOR_INEQUALITY_RAKE: bool = _env_flag("DIRECTOR_INEQUALITY_RAKE", False)
INEQUALITY_FLAT_THRESHOLD: float = 2.5  # p90/median at/below this → flat field
INEQUALITY_RECOMPUTE_SECONDS: int = 300  # steer slowly: recompute cadence per sandbox

# Director policy hold. The reserve-gated rake schedule (`resolve_rake_params`)
# otherwise reads one fresh economy snapshot from the ledger PER HAND — a
# `signal()` aggregate scan on the hot rake path, recomputed even though the
# reserve band only drifts over many hands. When on, the schedule (raked stake
# tiers + rate) is HELD for a `POLICY_WINDOW_SECONDS` window and recomputed only
# in the lobby refresh (`cash_mode.director_policy`, the same throttle model as
# the inequality read), so the per-hand rake just reads a cached scalar. The
# Director steers slowly; the rake doesn't need to re-derive the band every hand.
# Vice + side-hustle stay per-tick (the always-on bounds); casino is already
# window-stable. Implies RAKE_RESERVE_GATED (it holds that schedule). Default
# OFF — flip with the rest of the thermostat after sim. See
# docs/plans/PROD_STARTING_CONDITIONS.md §1.4.
DIRECTOR_POLICY_HOLD: bool = _env_flag("DIRECTOR_POLICY_HOLD", False)
POLICY_WINDOW_SECONDS: int = 300  # rake schedule held this long between recomputes

# Casino/whale pool thresholds relative to holdings. The spawn/close/whale gates
# are absolute chip counts (5k/50k/100k …) that don't scale as the roster (and
# thus total holdings) grows. When on, `casino_provisioning` treats them as
# FRACTIONS of holdings instead (calibrated to ~the absolute values at the launch
# ~$2.64M holdings), so casinos open/close at the same relative bank depth at any
# economy size. Default OFF — sim-validate with the rest of the thermostat.
CASINO_RELATIVE_THRESHOLDS: bool = _env_flag("CASINO_RELATIVE_THRESHOLDS", False)

# Lean casino fish lifecycle. The casino is the pool→field drain (fish bring
# pool-funded chips and bleed them to grinders). With 2 fish/casino at a 2.5–3.6×
# prefund — plus the dam cascade opening $2/$10/$50 at once and topping every
# casino back to 2 each tick — that drain lands as a LUMP that crashes reserves
# and stalls the climb to the tournament trigger. When on, casinos hold just ONE
# fish (two at $2), prefunded leaner, and reseed only when the current fish busts
# — turning the step-function drain into a steady trickle (same net pool→field
# flow over time, no crashes). Default OFF — sim-validate with the thermostat.
CASINO_RESEED_ON_SPENT: bool = _env_flag("CASINO_RESEED_ON_SPENT", False)


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


# --- Presence machine (the authoritative actor-location store) -----------

# The Presence cutover is COMPLETE: `entity_presence` is the permanent,
# authoritative record of actor location. The seat-write chokepoint
# (`CashTableRepository.save_table`) drives presence transitions inside its own
# transaction (presence + seats commit together); the cash_tables seat map and
# `ai_*_state` are projections of it; the legacy `cash_idle_pool` cache has been
# retired (schema v152). This flag is hardwired True with NO env override — the
# rollback escape hatch was deliberately removed when the cache was dropped (a
# fresh DB no longer has `cash_idle_pool` to fall back to). Code still reads it
# (`if PRESENCE_AUTHORITY_ENABLED:`) at the call sites; those branches are now
# always taken.
PRESENCE_AUTHORITY_ENABLED: bool = True

# Vestigial. Was the kill switch for the pre-flip SHADOW dual-write (mirror to
# `entity_presence` to validate the machine before authority was flipped). With
# authority now permanent, `presence_shadow.is_enabled()` is True regardless of
# this flag (it is `SHADOW or AUTHORITY`), so the off-grid hustle/vice writers
# still record presence. Kept only so the few remaining references resolve; it
# no longer changes any behaviour.
PRESENCE_SHADOW_WRITE_ENABLED: bool = False


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


# --- Tournament circuit world-tick hook (P3.7) ----------------------------

# World-tick hook for the Main Event circuit. When True, the world ticker
# (`ticker_service._tick_sandbox`) does two extra things per active sandbox:
#   (a) lets the EconomyChairman offer / expire Main Event invites on the tick
#       (so an offer surfaces or an un-accepted one lapses without a lobby poll);
#   (b) advances the owner's *autonomous* (declined / expired, AI-only)
#       tournament one round per tick so it plays out at world pace — like the
#       cash tables — surfacing structural beats (final table / bubble / winner)
#       on the lobby ticker.
# Default **False**: inert for any sandbox without a live autonomous tournament,
# and a complete no-op when off — the lobby-poll path
# (`GET /api/tournament/invite`) still offers/expires invites without it. Flip on
# only after re-validating the economy sim under the per-tournament overlay
# cadence (P3_REMAINING_HANDOFF §6). See `docs/plans/P3_REMAINING_HANDOFF.md` §P3.7.
TOURNAMENT_CIRCUIT_ENABLED: bool = _env_flag("TOURNAMENT_CIRCUIT_ENABLED", False)

# --- Tournaments as a draw (cash→tournament migration) --------------------

# Master switch for the "tournament as a draw" feature: AI personas LEAVE cash
# tables to enter a tournament, pulled by a draw/attractiveness score (prize +
# renown/regard), trickling off their seats over the registration window. The
# conservation-safe "called-up" cash-leave primitive (cash_mode/movement.py
# `called_up_pids`) is inert until a caller populates it; this flag gates the
# layers that DO populate it (the draw scorer + reserve/spawn + ticker trickle,
# built in later phases). Default **False** — Phase A ships the primitive only,
# wired to nothing, so flipping this changes nothing yet. See
# docs/plans/* (tournaments-as-a-draw) when the later phases land.
TOURNAMENT_DRAW_ENABLED: bool = _env_flag("TOURNAMENT_DRAW_ENABLED", False)

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

# Career M2 — emergent vouches. When on, the world ticker evaluates the player's
# inbound regard edges and fires at most one `vouch_ready` AI per sandbox per tick
# to reveal that AI's room (slow growth). Default OFF so it ships dark and flips on
# after a live playtest; only engages for a career sandbox (career_active +
# tutorial_complete). See docs/plans/CASH_MODE_CAREER_M2_PLAN.md.
CAREER_VOUCH_ENABLED: bool = _env_flag("CAREER_VOUCH_ENABLED", False)

# Table affinity — success-weighted room stickiness. When on, an idle AI's
# table-selection score gains `W_AFFINITY · tanh(net_chips / SCALE)` per
# candidate room (cash_mode.attractiveness): it drifts back to rooms it wins at
# and away from rooms it loses at, concentrating its hands into a real home room.
# This counteracts the within-tier smear (2-3 tables per stake) that otherwise
# stops low-stakes regulars from ever clearing the Career-M2 home-table floor —
# i.e. it's what makes vouching work at the $2/$10 stakes where the career arc
# begins. A chip-flow/movement change → sim-validate before enabling. Default OFF.
TABLE_AFFINITY_ENABLED: bool = _env_flag("TABLE_AFFINITY_ENABLED", False)


def compute_rake(
    pot: int,
    big_blind: int,
    *,
    stake_big_blinds: Optional[frozenset] = None,
    rate: Optional[float] = None,
) -> int:
    """Pure helper — returns the rake amount for a given pot.

    Returns 0 when rake is disabled, the pot is non-positive,
    big_blind is non-positive, or the stake (keyed by `big_blind`) is
    not in the active rake-stake set. The cap is applied in chip terms
    (`RAKE_CAP_BB * big_blind`) so it scales with the table's stake.

    `stake_big_blinds` / `rate` override the static `RAKE_STAKE_BIG_BLINDS` /
    `RAKE_RATE` for the **Director rake** — the reserve-gated two-layer schedule
    (see `resolve_rake_params`). When both are None the static config is used,
    which is the structural always-on $1000 rake (and the flag-off behaviour).
    """
    if not RAKE_ENABLED:
        return 0
    if pot <= 0 or big_blind <= 0:
        return 0
    stakes = stake_big_blinds if stake_big_blinds is not None else RAKE_STAKE_BIG_BLINDS
    eff_rate = rate if rate is not None else RAKE_RATE
    if big_blind not in stakes:
        return 0
    raw = int(pot * eff_rate)
    cap = RAKE_CAP_BB * big_blind
    return min(raw, cap)


def resolve_rake_params(chip_ledger_repo, sandbox_id, *, _fresh: bool = False):
    """The reserve-gated rake schedule for this moment, or `(None, None)`.

    Returns `(stake_big_blinds, rate)` to pass to `compute_rake`. When the
    `RAKE_RESERVE_GATED` flag is off (default) or there's no ledger, returns
    `(None, None)` so `compute_rake` falls back to the static structural rake
    ($1000 only, base rate) — byte-identical to pre-gate behaviour.

    When on, reads ONE economy snapshot and applies `cash_rake_schedule`: the
    Director expands the raked stakes ($200, …) and bumps the rate as the bank
    empties, and contracts back to $1000-only when reserves recover. The base
    $1000 tier is always present in every schedule, so the structural rake is
    never switched off — the Director only adds the lower-stake layers.

    Inequality-aware (`DIRECTOR_INEQUALITY_RAKE`): when the field is FLAT
    (cached `p90/median <= INEQUALITY_FLAT_THRESHOLD`) and reserves are still
    below the tournament trigger, the schedule is evaluated one band lower so the
    rake leads the refill that vice (no rich target to drain) can't. The cached
    inequality read is slow-moving (`cash_mode.field_inequality`).

    Policy hold (`DIRECTOR_POLICY_HOLD`): the per-hand rake path otherwise re-runs
    the `signal()` ledger scan EVERY hand. When the hold is on this returns the
    schedule HELD by `cash_mode.director_policy` (recomputed in the lobby refresh,
    at most once per `POLICY_WINDOW_SECONDS`) so the hot path just reads a cached
    scalar. `_fresh=True` BYPASSES the hold and computes live from the ledger — it
    is how the refresh recomputes the held value (and a cold cache, before the
    first refresh, falls through to a live compute so the first hands aren't on
    static rake). The flag-off path is unaffected (`_fresh` is irrelevant when
    the hold is off — the live compute always runs).
    """
    if not RAKE_RESERVE_GATED or chip_ledger_repo is None:
        return None, None
    if DIRECTOR_POLICY_HOLD and not _fresh:
        from cash_mode.director_policy import director_rake_policy

        held = director_rake_policy(sandbox_id)
        if held is not None:
            return held
        # Cold cache: no lobby refresh has populated the hold yet this process.
        # Fall through to a live compute so the opening hands rake correctly; the
        # next refresh seeds the held value.
    try:
        import dataclasses

        from core.economy.economy_signal import (
            RESERVE_HEALTHY,
            RESERVE_TRIGGER,
            cash_rake_schedule,
            signal,
        )

        state = signal(chip_ledger_repo, sandbox_id=sandbox_id)

        if DIRECTOR_INEQUALITY_RAKE and state.ratio < RESERVE_TRIGGER:
            from cash_mode.field_inequality import field_inequality

            ineq = field_inequality(sandbox_id)
            if ineq is not None and ineq <= INEQUALITY_FLAT_THRESHOLD:
                # Flat field → no runaway for vice to drain, so lean on the rake:
                # evaluate the schedule at least one band lower (forces the $200
                # even-skim tier on, regardless of the reserve band).
                lowered = min(state.ratio, RESERVE_HEALTHY - 1e-9)
                state = dataclasses.replace(state, ratio=lowered)

        sched = cash_rake_schedule(state)
        return sched.stake_big_blinds, sched.rate
    except Exception:  # pragma: no cover - defensive; fall back to static
        logger.warning("[RAKE] reserve-gated schedule failed; using static rake", exc_info=True)
        return None, None

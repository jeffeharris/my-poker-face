"""Feature-flag registry — the single source of truth for boolean toggles.

Why this exists
---------------
Flags used to be scattered `os.environ.get(...)` / `_env_flag(...)` reads with
inline defaults across `cash_mode/`, `flask_app/`, `poker/` and `core/`. That
made three things hard:

  1. **Lifecycle.** A feature would ship behind a flag, bake on a branch, and
     then the flag would be carried forever — re-enabled by hand on every new
     branch, its dead `if flag:` branches never cleaned up.
  2. **Env drift.** Spinning up a fresh dev environment meant remembering which
     flags to set in `.env`; nobody could say with confidence what was on.
  3. **Prod visibility.** There was no one place to look and confirm "every
     launched feature is actually enabled on prod."

This module fixes all three by making each flag a declared `FeatureFlag` with:

  - a **lifecycle `Stage`** (experimental → beta → stable → graduated/retired),
  - an explicit **per-environment default** (`dev` / `prod`), so a fresh env
    resolves correctly with zero `.env` fiddling, and
  - a resolver that reports **where each value came from** so the CLI board
    (`scripts/flags.py status`) can show the whole picture at a glance.

Resolution order (see `resolve`)
--------------------------------
  1. `GRADUATED` → always **True** (locked; the feature is permanent).
  2. `RETIRED`   → always **False** (locked; the feature is gone).
  3. An environment variable of the same name, if set (the per-deploy override
     and the kill switch for `STABLE` flags). Truthy: 1/true/yes/on.
  4. A DB `app_settings` row, if the flag opts into `db_overridable` (reuses the
     same table the LLM model tiers use, so admin-UI runtime toggling is free).
  5. The flag's per-environment default for the current env.

Adding a flag
-------------
Declare it here with `register(FeatureFlag(...))`. Read it with
`is_enabled("MY_FLAG")` (or, in `economy_flags`, the module global stays bound at
import for back-compat). Do **not** add a new `os.environ.get(...ENABLED...)`
elsewhere — `tests/test_feature_flags.py` enforces that flags go through here.

Graduating a flag
-----------------
When a feature is locked in, flip its stage to `GRADUATED`. It is then forced on
everywhere and can no longer be disabled. `scripts/flags.py check` lists
graduated/retired flags whose code branches still reference the name, so the
dead toggle code can be deleted on your own schedule.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


class Stage(str, Enum):
    """Where a flag sits in its lifecycle.

    The stage drives both the locking behaviour and the integrity rules in
    `tests/test_feature_flags.py`.
    """

    #: Off by default everywhere; opt-in per deploy via env var. Not yet
    #: validated — the resting state of a freshly-introduced toggle.
    EXPERIMENTAL = "experimental"
    #: On in dev, off in prod — the "baking on a branch" state. A fresh dev env
    #: gets the feature automatically; prod stays untouched until promotion.
    BETA = "beta"
    #: On everywhere by default, but the kill switch is retained (env/DB can
    #: still disable it). The resting state of a live, shipped feature.
    STABLE = "stable"
    #: Locked **on** everywhere — the feature is permanent and the flag is no
    #: longer a toggle. Pending deletion of the flag and its dead branches.
    GRADUATED = "graduated"
    #: Locked **off** everywhere — the feature was removed. Pending deletion of
    #: the flag and its dead branches.
    RETIRED = "retired"


_TRUTHY = ("1", "true", "yes", "on")
_FALSEY = ("0", "false", "no", "off")


def _parse_bool(raw: str) -> Optional[bool]:
    """Parse a string toggle; return None if it isn't a recognised bool."""
    val = raw.strip().lower()
    if val in _TRUTHY:
        return True
    if val in _FALSEY:
        return False
    return None


def current_env() -> str:
    """The environment key used to pick per-env defaults: 'dev' or 'prod'.

    Mirrors `poker.config.is_development_mode` so flag defaults agree with the
    rest of the app's dev/prod sense (FLASK_ENV=development or FLASK_DEBUG=1).
    """
    flask_env = os.environ.get("FLASK_ENV", "production")
    flask_debug = os.environ.get("FLASK_DEBUG", "0")
    return "dev" if (flask_env == "development" or flask_debug == "1") else "prod"


@dataclass(frozen=True)
class FeatureFlag:
    """A single declared boolean toggle.

    `dev` / `prod` are the per-environment defaults used when no override is
    present. For `GRADUATED` / `RETIRED` flags they are ignored (the value is
    locked), but they are still validated for consistency so a flag's intent
    stays readable.
    """

    name: str
    stage: Stage
    description: str  # one line — full rationale lives next to the call site
    owner: str = ""  # e.g. "cash_mode.economy" — groups the status report
    dev: bool = False  # default when current_env() == 'dev'
    prod: bool = False  # default when current_env() == 'prod'
    db_overridable: bool = False  # may an app_settings row override it at runtime?
    since: str = ""  # ISO date the flag was introduced (lifecycle tracking)
    # When True, this flag's resolved value is published to the browser via
    # /api/feature-flags and attached to Sentry events (errors/replays/feedback)
    # so a bug report shows which UX-affecting flags were active. Keep this to
    # player-facing toggles — do NOT mark internal economy/sim flags, since the
    # endpoint is readable by any client (incl. guests). See sentry_relay_routes
    # / FEATURE_FLAGS.md.
    client_exposed: bool = False

    def default_for(self, env: str) -> bool:
        if self.stage is Stage.GRADUATED:
            return True
        if self.stage is Stage.RETIRED:
            return False
        return self.dev if env == "dev" else self.prod


REGISTRY: dict[str, FeatureFlag] = {}


def register(flag: FeatureFlag) -> FeatureFlag:
    """Add a flag to the registry. Raises on a duplicate name."""
    if flag.name in REGISTRY:
        raise ValueError(f"Duplicate feature flag: {flag.name!r}")
    REGISTRY[flag.name] = flag
    return flag


@lru_cache(maxsize=1)
def _settings_repo():
    """Cached SettingsRepository for DB overrides (same table as LLM tiers).

    Lazily imported so `core/` keeps no import-time dependency on `poker/`, and
    only touched for flags that opt into `db_overridable` — so the common case
    (env/default-only flags) never pays a DB or schema cost.
    """
    from poker.db_utils import get_default_db_path
    from poker.repositories import SchemaManager, SettingsRepository

    db_path = get_default_db_path()
    SchemaManager(db_path).ensure_schema()
    return SettingsRepository(db_path)


def _db_get(name: str) -> Optional[bool]:
    """Best-effort DB override lookup; None when unset or DB unavailable."""
    try:
        raw = _settings_repo().get_setting(name, "")
    except Exception:
        logger.debug("FLAG %s: DB unavailable for override; using env/default", name)
        return None
    if not raw:
        return None
    return _parse_bool(raw)


def resolve(flag: FeatureFlag, *, env: Optional[str] = None) -> tuple[bool, str]:
    """Resolve a flag to `(value, source)`.

    `source` is one of: ``locked:graduated``, ``locked:retired``, ``env``,
    ``db``, or ``default:<env>`` — surfaced by the status report so it's obvious
    why a flag holds the value it does.
    """
    if flag.stage is Stage.GRADUATED:
        return True, "locked:graduated"
    if flag.stage is Stage.RETIRED:
        return False, "locked:retired"

    raw = os.environ.get(flag.name)
    if raw is not None:
        parsed = _parse_bool(raw)
        if parsed is not None:
            return parsed, "env"
        logger.warning("FLAG %s: unparseable env value %r; ignoring", flag.name, raw)

    if flag.db_overridable:
        db_value = _db_get(flag.name)
        if db_value is not None:
            return db_value, "db"

    env = env or current_env()
    return flag.default_for(env), f"default:{env}"


def is_enabled(name: str, *, env: Optional[str] = None) -> bool:
    """The effective boolean value of a registered flag."""
    try:
        flag = REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"Unknown feature flag {name!r}. Declare it in core/feature_flags.py."
        ) from None
    return resolve(flag, env=env)[0]


def snapshot(env: Optional[str] = None) -> list[dict]:
    """Resolved view of every flag for the given env — powers the CLI report."""
    env = env or current_env()
    rows = []
    for flag in REGISTRY.values():
        value, source = resolve(flag, env=env)
        rows.append(
            {
                "name": flag.name,
                "stage": flag.stage.value,
                "value": value,
                "source": source,
                "owner": flag.owner,
                "dev": flag.dev,
                "prod": flag.prod,
                "db_overridable": flag.db_overridable,
                "since": flag.since,
                "description": flag.description,
                "client_exposed": flag.client_exposed,
            }
        )
    return rows


def client_snapshot(env: Optional[str] = None) -> dict[str, bool]:
    """`{name: value}` for flags marked ``client_exposed`` — the browser view.

    Backs the ``/api/feature-flags`` endpoint and the Sentry feature-flag
    context. Intentionally narrow: only player-facing toggles are published,
    since the endpoint is readable by any client. See the ``client_exposed``
    note on :class:`FeatureFlag`.
    """
    env = env or current_env()
    return {
        flag.name: resolve(flag, env=env)[0] for flag in REGISTRY.values() if flag.client_exposed
    }


# ---------------------------------------------------------------------------
# Flag declarations — THE source of truth. Grouped by owner.
#
# Stages preserve current behaviour exactly:
#   - flags that read `_env_flag(X, False)` today → EXPERIMENTAL (off, env opt-in)
#   - hardcoded `True` live kill switches → STABLE (on everywhere, disable-able)
#   - the permanent `True` constant → GRADUATED (locked on)
#   - the vestigial `False` flag → RETIRED (locked off)
# Promotion candidates (e.g. flags you set in every dev .env → BETA) are noted
# in docs/guides/FEATURE_FLAGS.md; promote them deliberately, not here silently.
# ---------------------------------------------------------------------------

_ECON = "cash_mode.economy"

# --- Faucet ---
register(
    FeatureFlag(
        "REGEN_ENABLED",
        # RETIRED 2026-06-10: passive idle-chip faucet, superseded by the side
        # hustle (SIDE_HUSTLE_ENABLED). Locked off everywhere; the bankroll.py
        # regen branch is dead-pending-cleanup (still exercised by economy tests
        # that set the global directly).
        Stage.RETIRED,
        "Passive idle-chip faucet — retired in favour of the side hustle.",
        owner=_ECON,
        dev=False,
        prod=False,
    )
)
register(
    FeatureFlag(
        "SIDE_HUSTLE_ENABLED",
        Stage.STABLE,
        "Active faucet: broke AIs earn a pool-funded lump off-grid.",
        owner=_ECON,
        dev=True,
        prod=True,
    )
)

# --- Vice / lever reserve-gating ---
# The Director thermostat (these + RAKE_RESERVE_GATED, DIRECTOR_POLICY_HOLD,
# CASINO_RESEED_ON_SPENT) was promoted dev=True on 2026-06-10 to close the
# dev/prod drift — dev now runs the same prod economy. (Tests still force these
# off via tests/conftest.py::RESET_ECONOMY_FLAGS, so determinism is unaffected.)
register(
    FeatureFlag(
        "VICE_RESERVE_GATED",
        Stage.STABLE,
        "Scale vice intensity with the bank-pool deficit instead of always-on.",
        owner=_ECON,
        dev=True,
        prod=True,
    )
)
register(
    FeatureFlag(
        "GENESIS_RESERVE_ENABLED",
        Stage.STABLE,
        "Seed the bank pool to a fraction of holdings once at sandbox birth.",
        owner=_ECON,
        dev=True,
        prod=True,
    )
)

# --- Sink (table rake) ---
register(
    FeatureFlag(
        "RAKE_ENABLED",
        Stage.GRADUATED,
        "Structural table rake that recycles to the bank pool (permanent).",
        owner=_ECON,
    )
)
register(
    FeatureFlag(
        "RAKE_PLAYER_TABLES",
        Stage.STABLE,
        "Apply rake at tables with a human seated (not just AI-only tables).",
        owner=_ECON,
        dev=True,
        prod=True,
    )
)
register(
    FeatureFlag(
        "RAKE_RESERVE_GATED",
        Stage.STABLE,
        "Director two-layer rake: add lower stake tiers as the bank empties.",
        owner=_ECON,
        # dev=True (2026-06-10): turned on in dev so the BETA DIRECTOR_INEQUALITY_RAKE
        # evaluation isn't a no-op (inequality-rake is gated inside this block).
        dev=True,
        prod=True,
    )
)
register(
    FeatureFlag(
        "DIRECTOR_INEQUALITY_RAKE",
        # BETA (2026-06-10): dev-on for evaluation; still prod-off pending sim.
        Stage.BETA,
        "On a flat field, lead the refill with rake (implies RAKE_RESERVE_GATED).",
        owner=_ECON,
        dev=True,
        prod=False,
    )
)
register(
    FeatureFlag(
        "DIRECTOR_POLICY_HOLD",
        Stage.STABLE,
        "Hold the rake schedule for a window instead of recomputing per hand.",
        owner=_ECON,
        dev=True,
        prod=True,
    )
)

# --- Casino provisioning ---
register(
    FeatureFlag(
        "CASINO_RELATIVE_THRESHOLDS",
        # BETA (2026-06-10): dev-on for evaluation; still prod-off pending sim.
        Stage.BETA,
        "Treat casino spawn/close/whale gates as fractions of holdings.",
        owner=_ECON,
        dev=True,
        prod=False,
    )
)
register(
    FeatureFlag(
        "CASINO_RESEED_ON_SPENT",
        Stage.STABLE,
        "Lean casino fish lifecycle: one fish, reseed on bust (steady trickle).",
        owner=_ECON,
        dev=True,
        prod=True,
    )
)

# --- Player-prestige hooks ---
register(
    FeatureFlag(
        "REPUTATION_DEMEANOR_ENABLED",
        Stage.STABLE,
        "Reputation-driven AI demeanor nudge when seated with a high-renown human.",
        owner=_ECON,
        dev=True,
        prod=True,
        client_exposed=True,  # player-observable: AI demeanor shifts toward them
    )
)
register(
    FeatureFlag(
        "DOSSIER_SCOUTING_GATE_ENABLED",
        Stage.STABLE,
        "Gate earnable opponent-dossier reads behind hands observed (Circuit only).",
        owner=_ECON,
        dev=True,
        prod=True,
        client_exposed=True,  # player-facing: gates a dossier UI feature
    )
)

# --- Presence machine ---
register(
    FeatureFlag(
        "PRESENCE_AUTHORITY_ENABLED",
        Stage.GRADUATED,
        "entity_presence is the authoritative actor-location store (permanent).",
        owner=_ECON,
    )
)

# --- Chip-custody machine ---
register(
    FeatureFlag(
        "CHIP_CUSTODY_ENABLED",
        Stage.STABLE,
        "Record AI at-table chips as ledger transfers (derivable bankroll).",
        owner=_ECON,
        dev=True,
        prod=True,
    )
)
register(
    FeatureFlag(
        "CHIP_CUSTODY_DERIVE_READS",
        Stage.STABLE,
        "Make ledger-derived chip counts authoritative for bankroll reads.",
        owner=_ECON,
        dev=True,
        prod=True,
    )
)

# --- Tournament circuit ---
register(
    FeatureFlag(
        "TOURNAMENT_CIRCUIT_ENABLED",
        Stage.STABLE,
        "World-tick hook: offer/expire Main Event invites and advance AI tournaments.",
        owner=_ECON,
        dev=True,
        prod=True,
        client_exposed=True,  # player-facing: Main Event invites surface in UI
    )
)
register(
    FeatureFlag(
        "TOURNAMENT_DRAW_ENABLED",
        Stage.STABLE,
        "AIs leave cash tables for tournaments, pulled by a draw score.",
        owner=_ECON,
        dev=True,
        prod=True,
        client_exposed=True,  # player-observable: AIs visibly leave the table
    )
)

# --- Renown v2 ---
register(
    FeatureFlag(
        "RENOWN_V2_ENABLED",
        Stage.STABLE,
        "Field-relative renown scoreboard (read-side).",
        owner=_ECON,
        dev=True,
        prod=True,
    )
)
register(
    FeatureFlag(
        "RENOWN_V2_PERSIST_AI",
        Stage.STABLE,
        "Persist a per-AI renown row each ticker recompute (implies RENOWN_V2_ENABLED).",
        owner=_ECON,
        dev=True,
        prod=True,
    )
)
register(
    FeatureFlag(
        "PRESTIGE_SEEKING_ENABLED",
        Stage.STABLE,
        "Status-seeking AIs pulled toward tables with high-renown players.",
        owner=_ECON,
        dev=True,
        prod=True,
    )
)
register(
    FeatureFlag(
        "TABLE_AFFINITY_ENABLED",
        # BETA (2026-06-10): dev-on for evaluation; still prod-off pending sim.
        Stage.BETA,
        "Success-weighted room stickiness in idle table selection.",
        owner=_ECON,
        dev=True,
        prod=False,
    )
)
register(
    FeatureFlag(
        "CAREER_PROGRESSION_ENABLED",
        Stage.EXPERIMENTAL,
        "Act-1 narrative master gate: Lucky Stack intake + Scene-0 tutorial + keyring lobby for a brand-new sandbox.",
        owner=_ECON,
        dev=False,
        prod=False,
    )
)
register(
    FeatureFlag(
        "CAREER_VOUCH_ENABLED",
        Stage.EXPERIMENTAL,
        "Career-M2 emergent vouches: the ticker reveals a played-with AI's home room when it likes+respects the player enough.",
        owner=_ECON,
        dev=False,
        prod=False,
    )
)
register(
    FeatureFlag(
        "INTAKE_WORLD_WARMUP_ENABLED",
        Stage.EXPERIMENTAL,
        "Pre-warm the hidden lobby with a short deterministic sim burst on intake completion (inert without CAREER_PROGRESSION_ENABLED).",
        owner=_ECON,
        dev=False,
        prod=False,
    )
)

# --- Strategy: archetype conditioning ---
_STRAT = "poker.strategy"
register(
    FeatureFlag(
        "TILT_CONDITIONING_ENABLED",
        # STABLE 2026-06-11: tilt reachability confirmed (EMOTIONAL_SYSTEM_ANALYSIS
        # measurement update) and the maniac opts in (cap 0.35); promoted prod-on.
        # Byte-identical for every other archetype (still tilt_conditioning_cap=0.0).
        Stage.STABLE,
        "Option-C tilt_conditioning layer: state-conditioned aggression spike in re-raise spots. Active for the maniac (DeviationProfile.tilt_conditioning_cap=0.35); inert for every archetype whose cap stays 0.0.",
        owner=_STRAT,
        dev=True,
        prod=True,
        db_overridable=True,
    )
)
register(
    FeatureFlag(
        "TILT_PERSISTENCE_ENABLED",
        Stage.EXPERIMENTAL,
        "Tilt-excursion persistence (TILT_EXCURSION_DESIGN.md): slow-recovery-while-tilted + second-wind escape in PlayerPsychology.recover() so tilt lasts long enough to be felt without going chronic. Inert (byte-identical recover) when off.",
        owner=_STRAT,
        dev=False,
        prod=False,
        db_overridable=True,
    )
)
register(
    FeatureFlag(
        "TILT_TELEGRAPH_ENABLED",
        Stage.EXPERIMENTAL,
        "Tilt telegraph (TILT_EXCURSION_DESIGN.md §4): on entering a tilt episode, a probabilistic Layer-3 trigger that forces the sharp bot to speak and hands the LLM the tilt state + loose suggestions (own words, not a fixed line). Frequency-neutral; off => no telegraph block, no forced speech.",
        owner=_STRAT,
        dev=False,
        prod=False,
        db_overridable=True,
    )
)
register(
    FeatureFlag(
        "TILT_ERRATIC_READS_ENABLED",
        Stage.EXPERIMENTAL,
        "Tilt coupling (TILT_EXCURSION_DESIGN.md §4): replace the deterministic exploitation cliff (_zone_to_tilt_factor 1.0/0.5/0.0) with an ERRATIC random taper scaled by tilt intensity, so a tilted sharp bot's reads get unreliable (never a hard 0.0). Changes decisions; off => the legacy deterministic cliff.",
        owner=_STRAT,
        dev=False,
        prod=False,
        db_overridable=True,
    )
)
register(
    FeatureFlag(
        "TILT_SIGNATURE_ENABLED",
        Stage.EXPERIMENTAL,
        "Tilt behavioral signature (TILT_EXCURSION_DESIGN.md §4): make the tiered bot's emotional distortion under tilt CHARACTER-driven by risk_identity — risk-seekers SPEW (more aggressive), risk-averse COLLAPSE (more passive) — instead of the state-driven default (tilted=aggressive for all). Brings the tiered bot to parity with the standard bot's compute_modifiers split. Changes decisions; off => state-driven direction.",
        owner=_STRAT,
        dev=False,
        prod=False,
        db_overridable=True,
    )
)

# --- Flask app: config-level toggles (read by flask_app/config.py) ---------
# Defaults match the prior os.environ.get(...) defaults exactly; dev/prod splits
# mirror the verified live state (CSRF / debug arming). Migrated 2026-06-10.
_FLASK_CFG = "flask_app.config"
register(
    FeatureFlag(
        "ENABLE_AVATAR_GENERATION",
        Stage.STABLE,
        "Background AI player avatar generation (expensive; default on).",
        owner=_FLASK_CFG,
        dev=True,
        prod=True,
    )
)
register(
    FeatureFlag(
        "ENABLE_AI_COMMENTARY",
        Stage.STABLE,
        "Post-hand AI commentary generation (default on).",
        owner=_FLASK_CFG,
        dev=True,
        prod=True,
    )
)
register(
    FeatureFlag(
        "CSRF_PROTECTION_ENABLED",
        Stage.STABLE,
        "Double-submit-cookie CSRF enforcement. Off in dev/test (cross-origin SPA, FLASK_ENV=development) and ON in prod (same-origin) — current_env() mirrors config.is_development exactly.",
        owner=_FLASK_CFG,
        dev=False,
        prod=True,
    )
)
register(
    FeatureFlag(
        "ENABLE_AI_DEBUG",
        Stage.EXPERIMENTAL,
        "Backend AI-debug surface: LLM stats on player cards (dev/debug opt-in). Also drives the frontend VITE_ENABLE_AI_DEBUG build arg separately.",
        owner=_FLASK_CFG,
        dev=False,
        prod=False,
    )
)
register(
    FeatureFlag(
        "ENABLE_TEST_ROUTES",
        Stage.EXPERIMENTAL,
        "Register the test-helper HTTP endpoints (flask_app/routes/test_routes.py); dev/test opt-in only.",
        owner=_FLASK_CFG,
        dev=False,
        prod=False,
    )
)

# --- Flask app: realtime world ticker (flask_app/services/ticker_service.py) -
_FLASK_SVC = "flask_app.services"
register(
    FeatureFlag(
        "WORLD_TICKER_ENABLED",
        Stage.STABLE,
        "Realtime cash-mode world ticker (default on); off falls back to read-driven refresh on /api/cash/lobby.",
        owner=_FLASK_SVC,
        dev=True,
        prod=True,
    )
)
register(
    FeatureFlag(
        "TICKER_ASYNC_NARRATION_ENABLED",
        Stage.STABLE,
        "Run vice/side-hustle START narration OFF the tick in a background greenlet (default on); off reverts to synchronous in-tick narration.",
        owner=_FLASK_SVC,
        dev=True,
        prod=True,
    )
)

# --- Flask app: chat / social --------------------------------------------
register(
    FeatureFlag(
        "SARCASM_DETECTION_ENABLED",
        Stage.STABLE,
        "Sarcasm perception gate: only recipients with high adaptation_bias detect sarcasm (off => the prior universal sarcasm transform).",
        owner="flask_app.chat",
        dev=True,
        prod=True,
    )
)

# --- Core: content moderation (core/moderation.py) ------------------------
register(
    FeatureFlag(
        "MODERATION_ENABLED",
        Stage.STABLE,
        "OpenAI text moderation on user content (default on; additionally no-ops without an OPENAI_API_KEY).",
        owner="core.moderation",
        dev=True,
        prod=True,
    )
)

# --- Poker: per-decision analytics (poker/controllers.py) -----------------
_CTRL = "poker.controllers"
register(
    FeatureFlag(
        "DECISION_ANALYSIS_ENABLED",
        Stage.STABLE,
        "Master switch for per-decision equity-MC quality logging (default on).",
        owner=_CTRL,
        dev=True,
        prod=True,
    )
)
register(
    FeatureFlag(
        "DECISION_ANALYSIS_QUEUE_ENABLED",
        Stage.STABLE,
        "Enqueue decisions for the out-of-band analytics worker instead of analyzing inline. Off in dev (inline, keeps tests cheap), on in prod (the worker container).",
        owner=_CTRL,
        dev=False,
        prod=True,
    )
)

# --- Cash mode: leave-table narration (cash_mode/leave_narrative.py) -------
# De-inverted from the legacy CASH_LEAVE_NARRATIVE_DISABLED env flag.
register(
    FeatureFlag(
        "CASH_LEAVE_NARRATIVE_ENABLED",
        Stage.STABLE,
        "AI leave-table narration LLM calls (default on; disabled in the test suite via env so the lobby doesn't fire real LLM calls).",
        owner="cash_mode.narrative",
        dev=True,
        prod=True,
    )
)

# --- Guest limits (poker/guest_limits.py) ---------------------------------
# Migrated 2026-06-10 (formerly a `not is_development_mode()` derive + a local
# `_bool_env` read that evaded the centralization guard).
_GUEST = "poker.guest_limits"
register(
    FeatureFlag(
        "GUEST_LIMITS_ENABLED",
        Stage.STABLE,
        "Enforce guest rate/abuse limits (hands cap, opponent cap, free-chat lock). On in prod, off in dev/test — current_env() mirrors is_development_mode().",
        owner=_GUEST,
        dev=False,
        prod=True,
    )
)
register(
    FeatureFlag(
        "GUEST_FREE_CHAT_ENABLED",
        Stage.EXPERIMENTAL,
        "Allow guests free-text chat (off by default — free text is appended verbatim to the AI prompt, a prompt-injection/cost surface; PRH-27). Structured quick-chat stays allowed regardless.",
        owner=_GUEST,
        dev=False,
        prod=False,
    )
)

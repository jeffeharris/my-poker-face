"""Full-sim entry point — AI-only poker hands at unseated tables.

Replaces `cash_mode/fake_sim.roll_fake_hand` at the call site
(`refresh_unseated_tables`) once Commit 3 lands. Until then, lives
beside fake-sim so the schema and tests can stabilize without
disturbing the live event surface.

**Phase 2 (current commit): real cardplay.** `play_one_hand`
constructs a minimal `PokerGameState` from the cash table's
seats, builds (or fetches from cache) one `TieredBotController`
per AI personality_id, runs the hand engine until showdown, and
returns the resulting chip deltas in the same `HandSimResult`
shape Commit 1 introduced. No SocketIO, no DB writes — the
caller (lobby refresh loop) owns persistence and event emission.

Memory hygiene (Phase 2.5, inlined here): the state machine is
constructed with `record_snapshots=False` so its `snapshots`
tuple doesn't accumulate across thousands of sim hands. Spike
showed +25 MB / 1000 hands when snapshots were retained;
disabling them keeps the warm path memory-flat.

Spec: `docs/plans/CASH_MODE_FULL_SIM_HANDOFF.md` Commits 1-2.
Phase 0 spike (2026-05-19) measured 227 hands/sec warm with the
cached controller path used here.
"""

from __future__ import annotations

import logging
import os
import random
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from cash_mode.controller_cache import LruControllerCache
from cash_mode.fake_sim import (
    DEFAULT_BIG_EVENT_THRESHOLD_BB,
    DEFAULT_FAKE_HAND_PROB,
    DEFAULT_MAX_POT_BB,
)
from cash_mode.movement import SEATED_ENERGY_DRAIN_PER_HAND
from cash_mode.psychology_persistence import (
    flush_persona_psychology as _flush_psychology,
    hydrate_persona_psychology as _hydrate_psychology,
)
from poker.poker_game import (
    Player,
    PokerGameState,
    advance_to_next_active_player,
    award_pot_winnings,
    create_deck,
    determine_winner,
    play_turn,
)
from poker.poker_state_machine import PokerPhase, PokerStateMachine

# Per-sandbox `AIMemoryManager` cache. Wires the opponent-model /
# memory pipeline into the sim path so exploitation rules
# (hyper_aggressive, induce_override, etc.) get fed real opponent
# stats — without this, every gate sees cold-start and never fires.
# Mirrors `experiments/run_ai_tournament.py:765` which creates a
# memory_manager per tournament. Module-level cache because
# play_one_hand is called per-table per-tick; constructing the
# manager every call would lose all accumulated stats.
_session_memory_managers: Dict[str, Any] = {}
_session_hand_counters: Dict[str, int] = {}
_session_memory_lock = threading.Lock()


def _get_session_memory_manager(sandbox_id: Optional[str], db_path: Optional[str]):
    """Return the AIMemoryManager for this sandbox, creating if first call.

    `sandbox_id` is required (the manager keys on it). `db_path` may be
    None — manager will still work in-memory; persistence-driven features
    (hand_history, relationships) just degrade gracefully.
    """
    if not sandbox_id:
        return None
    with _session_memory_lock:
        mm = _session_memory_managers.get(sandbox_id)
        if mm is not None:
            return mm
        try:
            from poker.memory.memory_manager import AIMemoryManager

            mm = AIMemoryManager(
                game_id=f"sim_{sandbox_id}",
                db_path=db_path,
                owner_id=None,
                commentary_enabled=False,
            )
        except Exception as exc:
            logger.warning(
                "[FULL_SIM] AIMemoryManager construction failed: %s",
                exc,
            )
            return None
        # Carry opponent reads across sessions: restore this sandbox's
        # persisted models so background AIs don't cold-start every time the
        # controller cache cycles (they accumulate reads over a career).
        # Mirrors the interactive restore in game_routes; keyed by the sim
        # game_id so it's flat (UNIQUE(game_id, observer, opponent) upsert).
        if db_path:
            try:
                from poker.memory.opponent_model import OpponentModelManager
                from poker.repositories.game_repository import GameRepository

                saved = GameRepository(db_path).load_opponent_models(f"sim_{sandbox_id}")
                if saved:
                    mm.opponent_model_manager = OpponentModelManager.from_dict(saved)
            except Exception as exc:
                logger.debug("[FULL_SIM] opponent-model restore failed: %s", exc)
        # Phase 3 relationships in the lobby sim: wire the relationship repo
        # so AI<->AI heat/respect/likability (and cash_pair_stats PnL) evolve
        # from off-screen big-pot drama — the social world keeps breathing
        # while the human is away. LEAN by construction: we deliberately do
        # NOT call set_hand_history_repo, so `_persistence` stays None and
        # on_hand_complete skips the per-hand hand_history DB write. Combined
        # with record_showdown_equity=False at the call site, this keeps the
        # ~227 hand/sec loop write-light. Mirrors the live cash wiring at
        # flask_app/routes/cash_routes.py:880.
        if db_path:
            try:
                from poker.repositories.personality_repository import (
                    PersonalityRepository,
                )
                from poker.repositories.relationship_repository import (
                    RelationshipRepository,
                )

                mm.set_relationship_repo(
                    RelationshipRepository(db_path),
                    cash_mode=True,
                    sandbox_id=sandbox_id,
                    # table_max_buy_in left None: STACK_DOMINANCE stays off in
                    # the lobby sim for v1. The manager is per-sandbox, shared
                    # across tables of differing stakes, so there's no single
                    # cap to set here. The big-pot / bluff / hero-call / cooler
                    # event families drive AI<->AI relationships without it.
                    table_max_buy_in=None,
                )
                # Suppress dossiers + cash-pair PnL for transient casino fish
                # (per-pair, both directions) — same policy as live cash.
                # Grinder<->grinder history still accrues normally.
                mm.set_fish_ids(
                    {
                        p["personality_id"]
                        for p in PersonalityRepository(db_path).list_fish_for_cash_mode()
                        if p.get("personality_id")
                    }
                )
            except Exception as exc:
                logger.warning("[FULL_SIM] relationship wiring failed: %s", exc)
        _session_memory_managers[sandbox_id] = mm
        _session_hand_counters[sandbox_id] = 0
        return mm


def _next_hand_number(sandbox_id: str) -> int:
    with _session_memory_lock:
        n = _session_hand_counters.get(sandbox_id, 0)
        _session_hand_counters[sandbox_id] = n + 1
        return n


# Per-table probability gate (per lobby read). Same numerical default
# as the predecessor fake-sim gate so the swap is behavior-neutral on
# the rate at which hands fire. Renamed because "fake" no longer
# describes what happens — full sim runs real hands.
DEFAULT_HAND_SIM_PROB = DEFAULT_FAKE_HAND_PROB

# --- Catch-up burst (Commit 5) ---
# Below this gap, lobby reads only fire a single probability-gated
# hand (existing behavior). Above it, we burst-tick more hands to
# simulate "the world advanced while the lobby was unwatched."
DEFAULT_BURST_THRESHOLD_SECONDS = 30.0

# Seconds of real time each burst hand represents. With ~3 hands per
# minute as a casual baseline, this means a 5-minute gap bursts 15
# hands per table. Spike measured ~4 ms per hand warm, so 15 × 4 ≈
# 60 ms per table — well within the lobby response budget for 4-5
# unseated tables.
DEFAULT_BURST_PACING_SECONDS = 20.0

# Hard cap on hands per table per refresh. 30 × 4 tables × 4 ms ≈
# 480 ms total compute — still inside the 500 ms lobby budget set
# by the Phase 0 spike. Multi-hour absences hit this cap; we trade
# realism for response time and emit a summary event covering the
# uncovered tail.
DEFAULT_BURST_HAND_CAP = 30


def hand_burst_count(
    *,
    gap_seconds: float,
    base_prob: float,
    rng: random.Random,
    burst_threshold_seconds: float = DEFAULT_BURST_THRESHOLD_SECONDS,
    burst_pacing_seconds: float = DEFAULT_BURST_PACING_SECONDS,
    burst_hand_cap: int = DEFAULT_BURST_HAND_CAP,
) -> int:
    """Return the number of sim hands to run for one refresh tick.

    Below `burst_threshold_seconds`, this is the existing probability
    gate (returns 0 or 1). Above it, the caller has been away long
    enough that one hand would leave the table looking frozen; we
    burst-tick `floor(gap / pacing)` hands, capped at
    `burst_hand_cap`.

    The cap is the load-bearing safety net — a 2-hour absence
    multiplied by 4 unseated tables would otherwise budget ~480
    hands per refresh. Cap respected → lobby read stays inside
    the 500 ms budget the Phase 0 spike pinned.
    """
    if gap_seconds < burst_threshold_seconds:
        return 1 if rng.random() < base_prob else 0
    if burst_pacing_seconds <= 0:
        return min(burst_hand_cap, 1)
    return min(burst_hand_cap, int(gap_seconds // burst_pacing_seconds))


logger = logging.getLogger(__name__)


# Hand-event types — Commit 4 wires these to the lobby ticker.
# Phase 2 leaves `hand_events` empty; Commit 4 introduces the
# detector that populates it from the hand outcome.
HAND_EVENT_ALL_IN = "all_in"
HAND_EVENT_SUCKOUT = "suckout"
HAND_EVENT_BUST = "bust"
HAND_EVENT_NICE_POT = "nice_pot"


# Default name resolver used when callers don't pass `name_for`. The
# personality_id is used as the Player.name fallback, which lets the
# engine run without a personality_repo at the cost of every controller
# falling back to the default psychology. Real callers (lobby.py)
# inject a name_for that resolves to display names so the controllers
# load the right personality config.
def _default_name_for(pid: str) -> str:
    return pid


# Cap on actions per hand. 6-handed hands rarely exceed ~40 actions;
# 200 is a defensive ceiling that catches stuck-loop bugs without
# truncating any legitimate hand.
_MAX_ACTIONS_PER_HAND = 200

# Periodic psychology flush. Every N sim hands per AI we serialize
# `controller.psychology` to `ai_bankroll_state.emotional_state_json`
# so state survives backend restart and LRU eviction. N=10 caps the
# loss window at ~10 hands × ~3 min per hand ≈ <30 min of tilt for
# an AI that gets evicted before its next scheduled flush — small
# enough that stale tilt isn't a UX hazard.
PSYCHOLOGY_FLUSH_EVERY_HANDS = 10

# Per-controller attribute name used to track flush cadence. Stored
# on the controller object itself so it stays paired with the live
# state and doesn't need a parallel dict keyed by personality_id.
_SIM_HAND_COUNTER_ATTR = "_full_sim_hand_count"


@dataclass(frozen=True)
class HandEvent:
    """One hand-level drama event surfaced from a single sim hand.

    Distinct from `cash_mode/activity.LobbyEvent`: HandEvent is the
    structured output of the sim (no UI text, no timestamps);
    LobbyEvent is the formatted ticker-bound record. The lobby loop
    translates the former to the latter in Commit 4.

    `opponent_pid` is the second party when the event is pairwise
    (e.g. RIVER_SUCKOUT names both the favored and the suckout
    winner). None for single-party events (BUST).
    """

    type: str
    personality_id: str
    amount: int = 0
    opponent_pid: Optional[str] = None


@dataclass(frozen=True)
class ShowdownHand:
    """One revealed hand at showdown.

    `hand_name` is the human-readable classification (e.g.
    "Two Pair - Strong"). `hole_cards` is the list of card
    string reprs (e.g. ["As", "Kh"]).
    """

    personality_id: str
    hand_name: str
    hole_cards: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class HandSimResult:
    """Outcome of one sim hand.

    Structural superset of `FakeHandResult` — the original fields
    carry identical semantics so the lobby emission code keeps
    working unchanged when the call site swaps in Commit 3.

    `dealer_seat_idx` is the cash-table seats index of the player
    who held the button during this hand. The lobby tracks this
    across burst hands so the dealer indicator on table cards
    reflects the real engine dealer (load-bearing for seat-choice
    UX — players pick positions relative to the button). None on
    no-op hands or when no AI seats were available.
    """

    new_seats: List[dict] = field(default_factory=list)
    winner_pid: Optional[str] = None
    loser_pid: Optional[str] = None
    delta: int = 0
    big_event: bool = False
    hand_events: List[HandEvent] = field(default_factory=list)
    pot: int = 0
    showdown_hands: Optional[List[ShowdownHand]] = None
    dealer_seat_idx: Optional[int] = None


# Process-level cache for the default code path. Tests and isolated
# call sites should pass their own cache via `controller_cache=...`
# so they don't share state with the live lobby.
_default_controller_cache: Optional[LruControllerCache] = None
_default_cache_lock = threading.Lock()


def _get_default_controller_cache() -> LruControllerCache:
    global _default_controller_cache
    with _default_cache_lock:
        if _default_controller_cache is None:
            _default_controller_cache = LruControllerCache()
    return _default_controller_cache


# Module-level strategy tables. Spike measured load at ~30 ms one-shot;
# we pay it once per process. `None` until first call.
_strategy_table = None
_hu_strategy_table = None
_archetype_preflop_tables = None
_strategy_lock = threading.Lock()


def _get_strategy_tables() -> Tuple[object, object]:
    """Lazy-load + memoize the preflop / HU strategy tables.

    Held module-level rather than per-cache because the table data is
    immutable across the process lifetime and several caches (tests,
    production) can safely share the same instances.
    """
    global _strategy_table, _hu_strategy_table
    with _strategy_lock:
        if _strategy_table is None:
            # Imported here so module import doesn't drag the strategy
            # data load into every poker_module consumer.
            from poker.strategy.strategy_table import (
                load_hu_strategy_table,
                load_strategy_table,
            )

            _strategy_table = load_strategy_table()
            _hu_strategy_table = load_hu_strategy_table()
    return _strategy_table, _hu_strategy_table


def _get_archetype_preflop_tables() -> dict:
    """Lazy-load + memoize the width-tier preflop charts keyed by archetype
    (loose/loose_mid/station/tight). Needed for the fish (calling_station →
    station table) and consistent with prod (tiered_factory loads these too)."""
    global _archetype_preflop_tables
    with _strategy_lock:
        if _archetype_preflop_tables is None:
            from poker.strategy.strategy_table import load_archetype_preflop_tables

            _archetype_preflop_tables = load_archetype_preflop_tables()
    return _archetype_preflop_tables


def _copy_seats(seats: List[dict]) -> List[dict]:
    """Deep-copy the seats list. Callers never see in-place mutation."""
    return [dict(s) for s in seats]


def _build_controller(
    *,
    personality_id: str,
    display_name: str,
    state_machine: PokerStateMachine,
    archetype: Optional[str] = None,
    rule_strategy: Optional[str] = None,
    fish_leak: Optional[str] = None,
):
    """Construct a controller for one AI seat.

    Default path: `TieredBotController` — solver tables + personality
    distortion, no LLM. Imported lazily because `TieredBotController.
    __init__` chains into `AIPokerPlayer.__init__` which builds an
    `Assistant` (LLM client setup) even though full sim never calls
    the LLM. Spike measured 77 ms per controller; the cache keeps
    this off the hot path after warm-up.

    Fish path: fish now run through the tiered engine as a `calling_station`
    (the station width-tier table — a true loose-passive caller), with the
    legacy `fish_leak` re-expressed as a spot tendency. A NON-fish
    `rule_strategy` (e.g. a casebot grinder) still dispatches to
    `RuleBotController`. See docs/plans/FISH_AS_CALLING_STATION.md.
    """
    is_fish = archetype == 'fish' or rule_strategy == 'fish'
    # Sim-only A/B knob: POKER_SIM_FISH_ENGINE=rulebot reverts fish to the legacy
    # RuleBotController('fish') path so the closed-economy sim can compare
    # fish->field drain (fish_net_to_players) of the old caricature fish vs the
    # new tiered calling_station. Default ('tiered') = production behavior.
    legacy_fish = is_fish and os.environ.get('POKER_SIM_FISH_ENGINE') == 'rulebot'
    if (rule_strategy and not is_fish) or legacy_fish:
        from poker.rule_bot_controller import RuleBotController

        controller = RuleBotController(
            player_name=display_name,
            state_machine=state_machine,
            strategy='fish' if legacy_fish else rule_strategy,
            llm_config={},
            fish_leak=fish_leak,
        )
        controller.skip_equity_in_analysis = True
        return controller

    from poker.tiered_bot_controller import TieredBotController

    strategy_table, hu_table = _get_strategy_tables()
    archetype_tables = _get_archetype_preflop_tables()
    controller = TieredBotController(
        player_name=display_name,
        state_machine=state_machine,
        strategy_table=strategy_table,
        hu_strategy_table=hu_table,
        archetype_preflop_tables=archetype_tables,
        llm_config={},  # no LLM for sim
    )
    if is_fish:
        # Bottom-tier ($2) fish get the weak_fish loadout (weak_station table +
        # sticky/over_bluff + position_blind); higher tiers stay calling_station
        # with the persona's own spot tendencies. Mirrors prod
        # (tiered_factory.build_fish_controller). See FISH_AS_CALLING_STATION.md.
        from cash_mode.stakes_ladder import WEAK_FISH_STAKES, stake_label_for_big_blind

        _gs = getattr(state_machine, 'game_state', None)
        bb = getattr(_gs, 'current_ante', None) or getattr(_gs, 'big_blind', None)
        if stake_label_for_big_blind(bb) in WEAK_FISH_STAKES:
            from poker.strategy.deviation_profiles import DEVIATION_PROFILES

            controller._deviation_profile = DEVIATION_PROFILES['weak_fish']
        else:
            from poker.strategy.fish_loadout import fish_spot_tendencies

            tendencies = fish_spot_tendencies(fish_leak)
            if tendencies:
                controller._spot_tendencies_override = tendencies
                controller._spot_tendencies_resolved = True
    # Skip the per-decision Monte Carlo equity calc (~200-500ms) in
    # sim runs. The controller still builds the pipeline snapshot +
    # intervention trace; only decision_analyzer's equity field is
    # skipped. Shipped in the induce_override / equity-eval merge —
    # see docs/plans/INDUCE_OVERRIDE_PHASE_A.md §"Plumbing fixes."
    controller.skip_equity_in_analysis = True
    return controller


# `_hydrate_psychology` / `_serialize_psychology` / `_flush_psychology` were
# promoted verbatim to `cash_mode.psychology_persistence` so the live cash seat
# build and the cash-world tournament builder share this exact logic. They're
# imported at the top of this module under their historical private names, so the
# call sites below and the sim-cadence wrapper `_maybe_flush_psychology` are
# unchanged.


def _maybe_flush_psychology(
    controller,
    personality_id: str,
    bankroll_repo,
    sandbox_id: str,
) -> None:
    """Increment the per-controller sim-hand counter and flush every
    PSYCHOLOGY_FLUSH_EVERY_HANDS hands."""
    if bankroll_repo is None:
        return
    count = getattr(controller, _SIM_HAND_COUNTER_ATTR, 0) + 1
    setattr(controller, _SIM_HAND_COUNTER_ATTR, count)
    if count % PSYCHOLOGY_FLUSH_EVERY_HANDS == 0:
        _flush_psychology(controller, personality_id, bankroll_repo, sandbox_id)


def _maybe_flush_opponent_models(memory_manager, sandbox_id: str, db_path) -> None:
    """Persist this sandbox's opponent models every PSYCHOLOGY_FLUSH_EVERY_HANDS
    hands, so reads survive backend restart + controller-cache eviction.

    Keyed by the sim game_id (`sim_{sandbox_id}`) and upserted, so growth is
    flat — bounded by the active observer/opponent pairs, not hand count.
    """
    if memory_manager is None or not sandbox_id or not db_path:
        return
    with _session_memory_lock:
        count = _session_hand_counters.get(sandbox_id, 0)
    if count % PSYCHOLOGY_FLUSH_EVERY_HANDS != 0:
        return
    try:
        from poker.repositories.game_repository import GameRepository

        GameRepository(db_path).save_opponent_models(
            f"sim_{sandbox_id}", memory_manager.get_opponent_model_manager()
        )
    except Exception as exc:
        logger.debug("[FULL_SIM] opponent-model flush failed: %s", exc)


def _ai_seat_indices(seats: List[dict]) -> List[int]:
    return [i for i, s in enumerate(seats) if s.get("kind") == "ai" and int(s.get("chips", 0)) > 0]


def _apply_rake_to_winner(
    *,
    final_chips: Dict[str, int],
    starting_chips: Dict[str, int],
    pot: int,
    big_blind: int,
    winner_pid: Optional[str],
    chip_ledger_repo: Optional[Any],
    sandbox_id: str,
    table_id: Optional[str],
) -> None:
    """Skim the per-hand rake off the winning seat. Mutates `final_chips`.

    The rake amount comes from `economy_flags.compute_rake`, which
    returns 0 when rake is disabled. No-op when there's no positive
    pot, no winner, no ledger repo, or compute_rake returns 0 —
    which keeps the call site at the engine boundary clean.

    Multiway hands: applied to the headline winner only. For the
    common case of a single-winner pot, this is exact. For split
    pots (where `_headline_pair` returns the largest beneficiary),
    we under-collect by at most one BB-cap's worth; cleaner than
    proportionally splitting the rake across winners and the cost
    is small at the cap rates we use.
    """
    from cash_mode import economy_flags
    from core.economy import ledger as chip_ledger

    if winner_pid is None or chip_ledger_repo is None:
        return
    # Director rake (reserve-gated, flag-off by default): may expand the raked
    # stakes / rate when the bank is empty; otherwise the static $1000 skim.
    rake_stakes, rake_rate = economy_flags.resolve_rake_params(chip_ledger_repo, sandbox_id)
    rake = economy_flags.compute_rake(pot, big_blind, stake_big_blinds=rake_stakes, rate=rake_rate)
    if rake <= 0:
        return

    # Don't let rake drive the winner's stack negative — clamp to
    # what they actually netted on the hand. (Real cardrooms enforce
    # "no flop, no drop"; this is a stricter version — we only rake
    # to the extent of the winner's net win.)
    winner_net = final_chips[winner_pid] - starting_chips.get(winner_pid, 0)
    rake = min(rake, max(0, winner_net))
    if rake <= 0:
        return

    final_chips[winner_pid] = final_chips[winner_pid] - rake
    ctx = {
        'site': 'full_sim.play_one_hand',
        'pot': pot,
        'big_blind': big_blind,
        'winner_pid': winner_pid,
    }
    if table_id:
        ctx['table_id'] = table_id
    # The rake comes off the winner's SEAT stack (`final_chips` above), not
    # their bankroll. Under chip custody the at-table chips live in the seat
    # account, so the rake must be sourced from there — debiting `ai:<pid>`
    # (the bankroll) would desync the ledger-derived bankroll from the stored
    # int (the chips never left the bankroll). Reason stays `table_rake`, so
    # bank-pool depth accounting is unchanged. Pre-custody falls back to the
    # bankroll account (the historical approximation).
    rake_source = (
        chip_ledger.ai_seat(sandbox_id, winner_pid)
        if (economy_flags.CHIP_CUSTODY_ENABLED and sandbox_id)
        else chip_ledger.ai(winner_pid)
    )
    chip_ledger.record_table_rake(
        chip_ledger_repo,
        source=rake_source,
        amount=rake,
        context=ctx,
        sandbox_id=sandbox_id,
    )


def play_one_hand(
    seats: List[dict],
    *,
    big_blind: int,
    rng: random.Random,
    sandbox_id: str,
    max_pot_bb: int = DEFAULT_MAX_POT_BB,  # unused in Phase 2; kept for caller compat
    big_event_threshold_bb: int = DEFAULT_BIG_EVENT_THRESHOLD_BB,
    name_for: Callable[[str], str] = _default_name_for,
    controller_cache: Optional[LruControllerCache] = None,
    starting_dealer_seat_idx: Optional[int] = None,
    bankroll_repo: Optional[Any] = None,
    chip_ledger_repo: Optional[Any] = None,
    table_id: Optional[str] = None,
    table_max_buy_in: Optional[int] = None,
) -> HandSimResult:
    """Run one AI-only hand at the given cash-mode table.

    Constructs a `PokerGameState` from the AI seats (at least 2
    seats with positive chips required), builds or fetches
    controllers from the cache keyed by `personality_id`, runs the
    hand to showdown, and returns the chip deltas in `HandSimResult`.

    `name_for(personality_id) -> display_name` lets the caller
    resolve the display name used as Player.name and as the
    TieredBotController's personality lookup key. Production
    (lobby.py) injects a personality_repo-backed resolver; tests
    that don't care can let the default (identity) drop through.

    `controller_cache` defaults to a process-level singleton so the
    lobby's repeated calls share the warm pool. Tests should pass
    their own cache to keep instances isolated.

    `starting_dealer_seat_idx` lets the caller pin the engine's
    button to a specific cash-table seat. The lobby uses this to
    walk the button through the seated AIs in real engine-order
    across a burst (matters for seat-choice UX). When None, or
    when the index points at a seat that's no longer an AI, the
    engine defaults to the first seated AI as dealer. The result's
    `dealer_seat_idx` reports who actually held the button.

    `bankroll_repo`, when provided, drives psychology persistence:
    cache-miss controllers hydrate from `emotional_state_json` (per
    schema v97), and every PSYCHOLOGY_FLUSH_EVERY_HANDS hands the
    controller's live state is flushed back. Pass None in tests
    that don't care about cross-call state; production (the lobby
    refresh loop) wires this to the repo it already has in scope.

    `chip_ledger_repo`, when provided alongside
    `economy_flags.RAKE_ENABLED`, triggers per-hand `table_rake`
    destruction: a fraction of the pot is deducted from the winning
    AI's stack and recorded in the chip ledger. `table_id` is
    threaded into the ledger context for traceability. Both are
    optional — without them the rake step is a no-op and the sim
    runs exactly as before.

    Pure-ish: no DB writes other than the optional rake ledger
    entry. No SocketIO, no LLM. The state machine runs with
    `record_snapshots=False` so the snapshots tuple doesn't
    accumulate across many sim hands (Phase 2.5 hygiene).
    """
    ai_indices = _ai_seat_indices(seats)
    if len(ai_indices) < 2:
        # No-op: same shape as fake-sim early-out so the lobby loop
        # behavior is unchanged.
        return HandSimResult(new_seats=_copy_seats(seats))

    if controller_cache is None:
        controller_cache = _get_default_controller_cache()

    # Hermetic global-random state. Several downstream modules in the
    # decision pipeline (equity_calculator, chattiness_manager, etc.)
    # call `random.x()` without a seeded RNG — see the Phase 0 spike
    # findings. Without isolation, those calls (1) leak state from
    # play_one_hand into the rest of the process and (2) make two
    # calls with the same hand `rng` produce different outcomes
    # whenever the global RNG happens to be in a different position
    # between them. We snapshot the global state on entry, re-seed it
    # from the hand `rng` so internal decisions are deterministic
    # under a given hand seed, then restore on exit. The proper fix
    # (threading an rng through every decision-pipeline call) is out
    # of scope here; tracked for a follow-up.
    _saved_global_random_state = random.getstate()
    random.seed(rng.randrange(2**32))
    try:
        return _play_one_hand_inner(
            seats=seats,
            ai_indices=ai_indices,
            big_blind=big_blind,
            rng=rng,
            big_event_threshold_bb=big_event_threshold_bb,
            name_for=name_for,
            controller_cache=controller_cache,
            starting_dealer_seat_idx=starting_dealer_seat_idx,
            bankroll_repo=bankroll_repo,
            sandbox_id=sandbox_id,
            chip_ledger_repo=chip_ledger_repo,
            table_id=table_id,
            table_max_buy_in=table_max_buy_in,
        )
    finally:
        random.setstate(_saved_global_random_state)


def _play_one_hand_inner(
    *,
    seats: List[dict],
    ai_indices: List[int],
    big_blind: int,
    rng: random.Random,
    big_event_threshold_bb: int,
    name_for: Callable[[str], str],
    controller_cache: LruControllerCache,
    starting_dealer_seat_idx: Optional[int],
    bankroll_repo: Optional[Any],
    sandbox_id: str,
    chip_ledger_repo: Optional[Any] = None,
    table_id: Optional[str] = None,
    table_max_buy_in: Optional[int] = None,
) -> HandSimResult:
    """Body of play_one_hand, run inside the hermetic random snapshot.

    Kept separate so the snapshot/restore wrapping is unambiguous —
    every code path inside _play_one_hand_inner sees the seeded
    global RNG, and play_one_hand's caller never does.
    """

    # Build the per-hand state machine. Players are added in seat
    # order (using the cash-table seat indices), so the dealer button
    # rotates in a stable order across hands at the same table.
    players: List[Player] = []
    seat_pid_by_name: Dict[str, str] = {}  # player.name -> personality_id
    for idx in ai_indices:
        seat = seats[idx]
        pid = seat["personality_id"]
        display = name_for(pid) or pid
        players.append(Player(name=display, stack=int(seat["chips"]), is_human=False))
        seat_pid_by_name[display] = pid

    # Resolve the engine dealer index from the caller's seat hint.
    # `starting_dealer_seat_idx` is in the full-seats coordinate space;
    # we map back to the compacted `players` array. When the hint
    # doesn't point at a seated AI (e.g. that seat opened up between
    # ticks), fall back to player 0 — the engine then deals from the
    # first occupied seat, which is the same behavior the engine had
    # before this kwarg existed.
    dealer_player_idx = 0
    if starting_dealer_seat_idx is not None:
        try:
            dealer_player_idx = ai_indices.index(starting_dealer_seat_idx)
        except ValueError:
            dealer_player_idx = 0

    game_state = PokerGameState(
        players=tuple(players),
        deck=create_deck(shuffled=True, random_seed=rng.randrange(2**32)),
        current_ante=big_blind,
        last_raise_amount=big_blind,
        current_dealer_idx=dealer_player_idx,
    )
    sm = PokerStateMachine(game_state, record_snapshots=False)

    # Fetch / build a controller per seated AI. Point each one at the
    # new state machine — cache hits get re-pointed for the new hand.
    # Re-seed controller.rng from the hand's rng so cross-hand
    # determinism holds: same starting seats + same hand rng yields
    # the same outcome regardless of cache warmth. Without this,
    # cached controllers' internal rng state leaks across hands and
    # makes outcomes depend on cache hit order.
    #
    # Cache misses additionally hydrate the controller's psychology
    # from `ai_bankroll_state.emotional_state_json` (schema v97) so
    # tilt / confidence carry across backend restarts and LRU
    # evictions. Hits skip hydration — the live state already
    # reflects the most recent flush.
    controllers: Dict[str, object] = {}
    cache_misses: List[Tuple[str, object]] = []
    for player in players:
        pid = seat_pid_by_name[player.name]
        # Look up archetype + rule strategy once per cache miss. Both
        # are static per personality — the cache holds the right
        # controller class permanently once built, so this is a
        # warm-path no-op after the first hand.
        archetype = bankroll_repo.load_archetype(pid) if bankroll_repo is not None else None
        rule_strategy = bankroll_repo.load_rule_strategy(pid) if bankroll_repo is not None else None
        fish_leak = bankroll_repo.load_fish_leak(pid) if bankroll_repo is not None else None
        ctrl, was_miss = controller_cache.get_or_create_tracked(
            pid,
            lambda pid_local=pid,
            name_local=player.name,
            arch_local=archetype,
            rs_local=rule_strategy,
            fl_local=fish_leak: _build_controller(
                personality_id=pid_local,
                display_name=name_local,
                state_machine=sm,
                archetype=arch_local,
                rule_strategy=rs_local,
                fish_leak=fl_local,
            ),
        )
        if was_miss:
            cache_misses.append((pid, ctrl))
        ctrl.state_machine = sm
        ctrl.rng = random.Random(rng.randrange(2**32))
        controllers[player.name] = ctrl

    # Hydrate psychology AFTER all controllers are built — keeps the
    # repo I/O in one cluster rather than interleaved with construction.
    for pid, ctrl in cache_misses:
        _hydrate_psychology(ctrl, pid, bankroll_repo, sandbox_id)

    # Wire the per-sandbox AIMemoryManager into every controller so
    # opponent-aware rules (exploitation, induce_override, value
    # override, bluff_catch_override) have real stats to gate on.
    # Without this, every gate sees cold-start data and never fires
    # — see scripts/sim_experiments/analyze_interventions.py. The
    # tournament runner does this same wiring at
    # experiments/run_ai_tournament.py:765+.
    # BaseRepository exposes the path as `db_path` (not `_db_path`). The
    # original `_db_path` lookup never matched any repo, so this silently
    # resolved to None — disabling opponent-model persistence/restore AND
    # (once wired) relationship-simming. Prefer `db_path`, keep `_db_path`
    # as a defensive fallback for any custom repo that exposes it.
    db_path_for_memory = (
        getattr(bankroll_repo, 'db_path', None) or getattr(bankroll_repo, '_db_path', None)
        if bankroll_repo is not None
        else None
    )
    memory_manager = _get_session_memory_manager(sandbox_id, db_path_for_memory)
    if memory_manager is not None:
        opponent_manager = memory_manager.get_opponent_model_manager()
        for player in players:
            ctrl = controllers[player.name]
            ctrl.opponent_model_manager = opponent_manager
            ctrl.memory_manager = memory_manager
            try:
                memory_manager.initialize_for_player(
                    player.name,
                    personality_id=seat_pid_by_name.get(player.name),
                )
            except Exception as exc:
                logger.debug(
                    "[FULL_SIM] initialize_for_player(%r) failed: %s",
                    player.name,
                    exc,
                )

    # Snapshot starting chips per pid so we can compute deltas.
    starting_chips: Dict[str, int] = {seat_pid_by_name[p.name]: p.stack for p in players}

    # Notify memory_manager of hand start. Safe no-op when memory is
    # disabled / unavailable.
    if memory_manager is not None:
        try:
            memory_manager.on_hand_start(
                sm.game_state,
                hand_number=_next_hand_number(sandbox_id) if sandbox_id else 0,
                deck_seed=None,
            )
        except Exception as exc:
            logger.debug("[FULL_SIM] on_hand_start failed: %s", exc)

    winner_info = _run_hand(sm, controllers, memory_manager=memory_manager)

    # Phase 3 relationships: run hand-outcome detection + dispatch so this
    # hand's AI<->AI axes (and cash_pair_stats PnL) evolve. No-op unless the
    # relationship repo was wired in _get_session_memory_manager. LEAN cost
    # controls (see CASH_MODE_FULL_SIM_HANDOFF + the storage/speed analysis):
    #   - skip_commentary=True  : no LLM (sims have no chat layer anyway)
    #   - record_showdown_equity=False : skip the inline eval7 enrichment,
    #     the dominant cost of on_hand_complete (feeds only opponent-model
    #     equity buckets, not relationship detection)
    #   - equity_history=None   : BAD_BEAT needs pre-river equity we don't
    #     compute on this path; the rest of the event family still fires
    #   - _persistence is None  : the per-hand hand_history INSERT is skipped
    # game_state.pot survives award_pot_winnings (it only credits stacks), so
    # the recorded pot_size is intact and the big-pot event gate works.
    if memory_manager is not None and winner_info is not None:
        # Backfill end-of-hand card info onto the recorder. The sim calls
        # on_hand_start BEFORE the state machine deals (so start_hand captured
        # no hole cards) and never feeds community cards street-by-street.
        # Without this the recorded hand has empty hole/community cards and the
        # showdown-based relationship detectors (HERO_CALL, COOLER,
        # DOMINATED_SHOWDOWN, BLUFFED_OFF, STRONG_FOLD_SHOWN) all bail. At hand
        # end every card is known (full information, incl. folders' cards which
        # the engine retains), so backfill here.
        _rec = getattr(memory_manager.hand_recorder, "current_hand", None)
        if _rec is not None:
            try:
                for _p in sm.game_state.players:
                    _hole = getattr(_p, "hand", None)
                    if _hole:
                        _rec.set_hole_cards(_p.name, [str(c) for c in _hole])
                _board = [str(c) for c in getattr(sm.game_state, "community_cards", [])]
                if _board:
                    _rec.community_cards = list(_board)
            except Exception as exc:
                logger.debug("[FULL_SIM] card backfill failed: %s", exc)
        # STACK_DOMINANCE needs this table's cap. The manager is per-sandbox
        # and shared across tables of differing stakes, so set it per-hand
        # right before detection rather than once at wiring time. None leaves
        # STACK_DOMINANCE off (the other event families are unaffected).
        try:
            memory_manager.set_table_max_buy_in(table_max_buy_in)
        except Exception:
            pass
        try:
            memory_manager.on_hand_complete(
                winner_info=winner_info,
                game_state=sm.game_state,
                ai_players={},
                skip_commentary=True,
                equity_history=None,
                record_showdown_equity=False,
            )
        except Exception as exc:
            logger.debug("[FULL_SIM] on_hand_complete failed: %s", exc)
        finally:
            # The recorder appends every completed hand and the manager is
            # cached per-sandbox for the whole process lifetime — clear it so
            # the list doesn't grow unbounded across thousands of sim hands.
            try:
                memory_manager.hand_recorder.completed_hands.clear()
            except Exception:
                pass

    # Periodic psychology flush. Increment the per-controller hand
    # counter; every PSYCHOLOGY_FLUSH_EVERY_HANDS hands we serialize
    # state back to the repo so it survives backend restart + LRU
    # eviction. Skipped silently when bankroll_repo is None.
    for player in players:
        pid = seat_pid_by_name[player.name]
        ctrl = controllers[player.name]
        # Seated fatigue (parity with the live game-handler path): one hand of
        # seated play wears energy down so off-screen grinders eventually tire
        # and rotate, instead of farming a fish table forever.
        _psych = getattr(ctrl, "psychology", None)
        if _psych is not None:
            try:
                _psych.apply_seated_fatigue(SEATED_ENERGY_DRAIN_PER_HAND)
            except Exception:  # noqa: BLE001 — fatigue is non-critical bookkeeping
                pass
        _maybe_flush_psychology(ctrl, pid, bankroll_repo, sandbox_id)
    _maybe_flush_opponent_models(memory_manager, sandbox_id, db_path_for_memory)

    # Awards already applied by _run_hand. Read final stacks.
    final_chips: Dict[str, int] = {seat_pid_by_name[p.name]: p.stack for p in sm.game_state.players}

    winner_pid, loser_pid, delta = _headline_pair(starting_chips, final_chips)
    big_event = delta >= big_blind * big_event_threshold_bb
    # Pot total: sum of all positive deltas (= sum of all negative
    # deltas in absolute value). Equivalent to the actual pot that
    # got awarded across all side pots in a multiway hand.
    pot = sum(max(0, final_chips[pid] - starting_chips[pid]) for pid in starting_chips)

    # Apply table rake (destruction sink, paired with `ai_regen` faucet).
    # Skim happens after the engine awards but before we materialize
    # `new_seats`, so the rake comes off the winner's stack and the
    # cash-table seat row reflects the post-rake value. No-op unless
    # `economy_flags.RAKE_ENABLED` is True and a ledger repo was
    # threaded in by the caller.
    _apply_rake_to_winner(
        final_chips=final_chips,
        starting_chips=starting_chips,
        pot=pot,
        big_blind=big_blind,
        winner_pid=winner_pid,
        chip_ledger_repo=chip_ledger_repo,
        sandbox_id=sandbox_id,
        table_id=table_id,
    )

    # Build new_seats reflecting the post-hand (and post-rake) chip
    # counts. Seats outside the AI set are passed through unchanged.
    new_seats = _copy_seats(seats)
    for idx in ai_indices:
        pid = seats[idx]["personality_id"]
        new_seats[idx] = {**new_seats[idx], "chips": int(final_chips.get(pid, 0))}

    # Career-M2 home-table tracking: record one hand for each seated AI at
    # this table so the vouch evaluator can resolve an AI's home court (the
    # lobby table where it has played the most hands). ONE increment per AI
    # per hand — not bilateral. Best-effort; a counter write never breaks
    # the ~227 hand/sec loop. Most AI hands happen here off-screen, so this
    # is where home tables are mostly established.
    if table_id and sandbox_id and memory_manager is not None:
        _rel_repo = getattr(memory_manager, "_relationship_repo", None)
        if _rel_repo is not None:
            try:
                for idx in ai_indices:
                    pid = seats[idx]["personality_id"]
                    net = int(final_chips.get(pid, 0)) - int(starting_chips.get(pid, 0))
                    _rel_repo.increment_ai_table_hands(
                        pid,
                        table_id,
                        sandbox_id=sandbox_id,
                        net_delta=net,
                    )
            except Exception as exc:
                logger.debug("[FULL_SIM] ai_table_hands incr failed: %s", exc)

    hand_events = _detect_hand_events(
        starting_chips=starting_chips,
        final_chips=final_chips,
        final_players=sm.game_state.players,
        seat_pid_by_name=seat_pid_by_name,
        winner_pid=winner_pid,
        loser_pid=loser_pid,
    )

    # Ring buffer: persist notable events to each subject AI's recent-events
    # memory (bounded, capped, "pop the oldest"). Event-driven — fires only on
    # drama (bust/suckout/big pot), which is rare per hand, so this isn't the
    # pressure_events firehose. Gives the lobby/dossier "what recently happened
    # to this character" without the full per-decision write volume.
    if bankroll_repo is not None and sandbox_id and hand_events:
        by_pid: Dict[str, List[dict]] = {}
        for ev in hand_events:
            by_pid.setdefault(ev.personality_id, []).append(
                {"type": ev.type, "amount": int(ev.amount), "opponent": ev.opponent_pid}
            )
        for pid, evs in by_pid.items():
            try:
                bankroll_repo.push_recent_events(pid, evs, sandbox_id=sandbox_id)
            except Exception as exc:
                logger.debug("[FULL_SIM] recent-events push failed for %s: %s", pid, exc)

    # Map the engine's post-hand dealer back to the cash-table seat
    # index. The engine doesn't rotate during a single hand, so this
    # equals the seat we set as dealer at hand start (when the caller
    # passed a valid hint) — but we read it from the engine to handle
    # the fall-back path where the hint pointed at a now-open seat.
    engine_dealer_player_idx = sm.game_state.current_dealer_idx
    dealer_seat_idx: Optional[int] = None
    if 0 <= engine_dealer_player_idx < len(ai_indices):
        dealer_seat_idx = ai_indices[engine_dealer_player_idx]

    return HandSimResult(
        new_seats=new_seats,
        winner_pid=winner_pid,
        loser_pid=loser_pid,
        delta=delta,
        big_event=big_event,
        hand_events=hand_events,
        pot=pot,
        showdown_hands=None,  # Phase 6 (psychology at unseated tables) may populate
        dealer_seat_idx=dealer_seat_idx,
    )


def _detect_hand_events(
    *,
    starting_chips: Dict[str, int],
    final_chips: Dict[str, int],
    final_players,
    seat_pid_by_name: Dict[str, str],
    winner_pid: Optional[str],
    loser_pid: Optional[str],
) -> List[HandEvent]:
    """Inspect the post-hand state for drama events to surface.

    What gets detected here:
      - **BUST**: a player whose final chips are 0. They'll be
        removed from the table by the normal `forced_leave`
        movement path on the next refresh tick, but the bust
        moment itself deserves a ticker event right when it
        happens.
      - **ALL_IN**: a player whose `is_all_in` flag is still set
        at the end of the hand. The flag persists through pot
        award and is only cleared by `reset_game_state_for_new_hand`,
        so reading it post-award correctly captures "someone went
        all-in this hand" regardless of whether they won or lost.

    Deferred to future commits:
      - **SUCKOUT**: needs per-street equity history. Phase 6 / hand
        history persistence would expose this.
      - **NICE_POT**: redundant with the `big_event` flag → `big_win`
        emission. Kept in the HandEvent vocabulary for future use
        (e.g. if we split "big" into "big" vs "huge").

    The amount on each event is the player's chip change vs
    starting — negative for losses, positive for wins. The lobby's
    formatter shows it as an absolute dollar figure.
    """
    events: List[HandEvent] = []

    # Bust detection: final chips == 0. Skipped for opens / non-AIs.
    for pid, final in final_chips.items():
        if final <= 0 and starting_chips.get(pid, 0) > 0:
            events.append(
                HandEvent(
                    type=HAND_EVENT_BUST,
                    personality_id=pid,
                    amount=starting_chips[pid],  # how much they lost
                    opponent_pid=winner_pid if winner_pid != pid else None,
                )
            )

    # All-in detection: read the per-player flag from the final
    # game-state. A player who won an all-in pot still has the flag
    # set until reset_game_state_for_new_hand runs (which we don't
    # need to trigger here — the lobby loop persists chips, not
    # the state machine).
    name_to_pid = seat_pid_by_name
    for player in final_players:
        if not getattr(player, "is_all_in", False):
            continue
        pid = name_to_pid.get(player.name)
        if not pid:
            continue
        # Skip if BUST already covered this player — bust subsumes
        # all-in as the more dramatic outcome.
        if final_chips.get(pid, 0) <= 0:
            continue
        events.append(
            HandEvent(
                type=HAND_EVENT_ALL_IN,
                personality_id=pid,
                amount=max(
                    starting_chips.get(pid, 0),
                    final_chips.get(pid, 0),
                ),
                opponent_pid=(
                    loser_pid if pid == winner_pid else winner_pid if pid == loser_pid else None
                ),
            )
        )

    return events


def _headline_pair(
    starting: Dict[str, int],
    final: Dict[str, int],
) -> Tuple[Optional[str], Optional[str], int]:
    """Pick the (winner, loser, delta) pair that headlines the hand.

    The headline is the personality with the largest positive net
    chip change vs the personality with the largest loss. `delta` is
    the WINNER's gain (always non-negative). When the hand was
    chip-neutral for all players (everyone folded preflop unopened,
    walks the BB), returns (None, None, 0).
    """
    deltas: List[Tuple[str, int]] = sorted(
        ((pid, final[pid] - starting[pid]) for pid in starting),
        key=lambda x: x[1],
    )
    if not deltas:
        return None, None, 0
    loser_pid, loser_delta = deltas[0]
    winner_pid, winner_delta = deltas[-1]
    if winner_delta <= 0 or loser_delta >= 0:
        # No actual chip movement — fold-around or pot-neutral split.
        return None, None, 0
    return winner_pid, loser_pid, winner_delta


def _run_hand(
    sm: PokerStateMachine,
    controllers: Dict[str, object],
    *,
    memory_manager: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Drive the state machine from PRE_FLOP to pot award.

    Mirrors the spike's loop: advance to EVALUATING_HAND, handling
    run-it-out auto-advance and per-action decisions. Decision-level
    exceptions silently fold (matches production tournament behavior;
    the math_floor 'jam' fix from Phase 0 means these should be near-
    zero, but the safety net stays as a last line of defense).

    `memory_manager` is the per-sandbox AIMemoryManager when wired —
    feeds opponent-stat tracking via `on_action` after each play_turn.
    None for legacy callers / tests not wiring memory.

    Returns the `winner_info` dict from `determine_winner` (pot
    breakdown + winning hand) so the caller can drive
    `on_hand_complete` for relationship detection. Returns None when
    the hand ended without reaching EVALUATING_HAND.
    """
    actions = 0
    while actions < _MAX_ACTIONS_PER_HAND:
        sm.run_until([PokerPhase.EVALUATING_HAND])
        gs = sm.game_state

        if sm.current_phase == PokerPhase.EVALUATING_HAND:
            break

        if gs.run_it_out:
            nxt = (
                PokerPhase.SHOWDOWN
                if sm.current_phase == PokerPhase.RIVER
                else PokerPhase.DEALING_CARDS
            )
            sm.game_state = gs.update(awaiting_action=False, run_it_out=False)
            sm.update_phase(nxt)
            continue

        if not gs.awaiting_action:
            break

        cp = gs.current_player
        actor_name = cp.name if cp is not None else None
        ctrl = controllers.get(actor_name) if actor_name else None
        if ctrl is None:
            gs = play_turn(gs, "fold", 0)
            action, amount = "fold", 0
        else:
            try:
                resp = ctrl.decide_action()
                action = resp.get("action", "fold")
                amount = resp.get("raise_to", 0)
            except Exception as exc:  # noqa: BLE001 — match production runner
                logger.debug(
                    f"[FULL_SIM] {cp.name}: decide_action raised "
                    f"{type(exc).__name__}: {exc} — folding"
                )
                action, amount = "fold", 0
            gs = play_turn(gs, action, amount)

        # Feed action into the memory manager so opponent_model
        # accumulates stats. The `phase` mapping mirrors what
        # MemoryManager expects (PRE_FLOP / FLOP / TURN / RIVER).
        if memory_manager is not None and actor_name is not None:
            try:
                phase_obj = sm.current_phase
                phase_name = phase_obj.name if phase_obj is not None else "PRE_FLOP"
                # MemoryManager only cares about the four betting
                # streets; DEALING_CARDS/EVALUATING_HAND/SHOWDOWN don't
                # carry actions through this codepath, but guard anyway.
                if phase_name in ("PRE_FLOP", "FLOP", "TURN", "RIVER"):
                    active = [p.name for p in gs.players if not getattr(p, "is_folded", False)]
                    # gs.pot is a dict ({'total': N, <name>: bet, ...}); on_action
                    # (and the recorded action's pot_after, which on_hand_complete's
                    # fold_to_big_bet replay subtracts from) expects the numeric
                    # total. Passing the raw dict silently poisoned pot_after.
                    _pot = getattr(gs, "pot", 0)
                    pot_total = _pot.get("total", 0) if isinstance(_pot, dict) else (_pot or 0)
                    memory_manager.on_action(
                        player_name=actor_name,
                        action=action,
                        amount=amount,
                        phase=phase_name,
                        pot_total=pot_total,
                        active_players=active,
                    )
            except Exception as exc:
                logger.debug(
                    "[FULL_SIM] on_action(%r, %r) failed: %s",
                    actor_name,
                    action,
                    exc,
                )

        adv = advance_to_next_active_player(gs)
        if adv is not None:
            gs = adv
        sm.game_state = gs
        actions += 1

    if sm.current_phase == PokerPhase.EVALUATING_HAND:
        winner_info = determine_winner(sm.game_state)
        sm.game_state = award_pot_winnings(sm.game_state, winner_info)
        return winner_info
    return None

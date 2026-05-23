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

import json
import logging
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
                "[FULL_SIM] AIMemoryManager construction failed: %s", exc,
            )
            return None
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


def _copy_seats(seats: List[dict]) -> List[dict]:
    """Deep-copy the seats list. Callers never see in-place mutation."""
    return [dict(s) for s in seats]


def _build_controller(
    *, personality_id: str, display_name: str, state_machine: PokerStateMachine,
):
    """Construct a `TieredBotController` for one AI seat.

    Imported lazily because `TieredBotController.__init__` chains into
    `AIPokerPlayer.__init__` which builds an `Assistant` (LLM client
    setup) even though full sim never calls the LLM. Spike measured
    77 ms per controller; the cache keeps this off the hot path after
    warm-up.
    """
    from poker.tiered_bot_controller import TieredBotController

    strategy_table, hu_table = _get_strategy_tables()
    controller = TieredBotController(
        player_name=display_name,
        state_machine=state_machine,
        strategy_table=strategy_table,
        hu_strategy_table=hu_table,
        llm_config={},  # no LLM for sim
    )
    # Skip the per-decision Monte Carlo equity calc (~200-500ms) in
    # sim runs. The controller still builds the pipeline snapshot +
    # intervention trace; only decision_analyzer's equity field is
    # skipped. Shipped in the induce_override / equity-eval merge —
    # see docs/plans/INDUCE_OVERRIDE_PHASE_A.md §"Plumbing fixes."
    controller.skip_equity_in_analysis = True
    return controller


def _hydrate_psychology(
    controller, personality_id: str, bankroll_repo, sandbox_id: str,
) -> None:
    """Apply persisted emotional state to a freshly-built controller.

    Reads `ai_bankroll_state.emotional_state_json` (schema v97) and
    deserializes via `PlayerPsychology.from_dict`. No-op when:
      - `bankroll_repo` is None (test paths that don't care)
      - the column is NULL (AI has never been touched by sim before)
      - the JSON fails to parse (logged + skipped; controller stays
        at fresh defaults — surfacing the error would block hands
        on a column we can rewrite from the next flush)

    The controller's `psychology` attribute is replaced in-place,
    not its underlying class. Anchors carry over via the
    `personality_config` arg to `from_dict`.
    """
    if bankroll_repo is None:
        return
    try:
        blob = bankroll_repo.load_emotional_state_json(
            personality_id, sandbox_id=sandbox_id,
        )
    except Exception as exc:  # noqa: BLE001 — repo is best-effort here
        logger.debug(
            f"[FULL_SIM] {personality_id}: load_emotional_state_json failed: {exc}"
        )
        return
    if not blob:
        return
    try:
        state_dict = json.loads(blob)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning(
            f"[FULL_SIM] {personality_id}: emotional_state_json malformed "
            f"({exc}); using fresh defaults"
        )
        return
    if controller.psychology is None:
        return
    try:
        from poker.player_psychology import PlayerPsychology

        personality_config = getattr(
            controller.ai_player, "personality_config", {}
        )
        controller.psychology = PlayerPsychology.from_dict(
            state_dict, personality_config,
        )
    except Exception as exc:  # noqa: BLE001 — psychology is best-effort
        logger.warning(
            f"[FULL_SIM] {personality_id}: PlayerPsychology.from_dict failed "
            f"({exc}); using fresh defaults"
        )


def _serialize_psychology(controller) -> Optional[str]:
    """Return the controller's psychology as a JSON blob, or None.

    Returns None if the controller has no psychology attached (some
    test stubs or partial builds). Wrapped to_dict / json.dumps so a
    serialization quirk on one field doesn't poison the rest.
    """
    psych = getattr(controller, "psychology", None)
    if psych is None:
        return None
    try:
        return json.dumps(psych.to_dict())
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"[FULL_SIM] _serialize_psychology failed: {exc}"
        )
        return None


def _flush_psychology(
    controller, personality_id: str, bankroll_repo, sandbox_id: str,
) -> None:
    """Write the controller's current emotional state to the repo.

    Called by the periodic flush cadence and (in a future commit) on
    cache eviction. Best-effort: a repo error logs at debug and
    returns — the next flush will retry. State loss is bounded by
    the flush cadence (PSYCHOLOGY_FLUSH_EVERY_HANDS).
    """
    if bankroll_repo is None:
        return
    blob = _serialize_psychology(controller)
    if blob is None:
        return
    try:
        bankroll_repo.save_emotional_state_json(
            personality_id, blob, sandbox_id=sandbox_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"[FULL_SIM] {personality_id}: save_emotional_state_json failed: {exc}"
        )


def _maybe_flush_psychology(
    controller, personality_id: str, bankroll_repo, sandbox_id: str,
) -> None:
    """Increment the per-controller sim-hand counter and flush every
    PSYCHOLOGY_FLUSH_EVERY_HANDS hands."""
    if bankroll_repo is None:
        return
    count = getattr(controller, _SIM_HAND_COUNTER_ATTR, 0) + 1
    setattr(controller, _SIM_HAND_COUNTER_ATTR, count)
    if count % PSYCHOLOGY_FLUSH_EVERY_HANDS == 0:
        _flush_psychology(controller, personality_id, bankroll_repo, sandbox_id)


def _ai_seat_indices(seats: List[dict]) -> List[int]:
    return [
        i for i, s in enumerate(seats)
        if s.get("kind") == "ai" and int(s.get("chips", 0)) > 0
    ]


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
    rake = economy_flags.compute_rake(pot, big_blind)
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
    chip_ledger.record_table_rake(
        chip_ledger_repo,
        source=chip_ledger.ai(winner_pid),
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
        ctrl, was_miss = controller_cache.get_or_create_tracked(
            pid,
            lambda pid_local=pid, name_local=player.name: _build_controller(
                personality_id=pid_local,
                display_name=name_local,
                state_machine=sm,
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
    db_path_for_memory = (
        bankroll_repo._db_path
        if bankroll_repo is not None and hasattr(bankroll_repo, '_db_path')
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
                    player.name, exc,
                )

    # Snapshot starting chips per pid so we can compute deltas.
    starting_chips: Dict[str, int] = {
        seat_pid_by_name[p.name]: p.stack for p in players
    }

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

    _run_hand(sm, controllers, memory_manager=memory_manager)

    # Periodic psychology flush. Increment the per-controller hand
    # counter; every PSYCHOLOGY_FLUSH_EVERY_HANDS hands we serialize
    # state back to the repo so it survives backend restart + LRU
    # eviction. Skipped silently when bankroll_repo is None.
    for player in players:
        pid = seat_pid_by_name[player.name]
        ctrl = controllers[player.name]
        _maybe_flush_psychology(ctrl, pid, bankroll_repo, sandbox_id)

    # Awards already applied by _run_hand. Read final stacks.
    final_chips: Dict[str, int] = {
        seat_pid_by_name[p.name]: p.stack for p in sm.game_state.players
    }

    winner_pid, loser_pid, delta = _headline_pair(starting_chips, final_chips)
    big_event = delta >= big_blind * big_event_threshold_bb
    # Pot total: sum of all positive deltas (= sum of all negative
    # deltas in absolute value). Equivalent to the actual pot that
    # got awarded across all side pots in a multiway hand.
    pot = sum(
        max(0, final_chips[pid] - starting_chips[pid])
        for pid in starting_chips
    )

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

    hand_events = _detect_hand_events(
        starting_chips=starting_chips,
        final_chips=final_chips,
        final_players=sm.game_state.players,
        seat_pid_by_name=seat_pid_by_name,
        winner_pid=winner_pid,
        loser_pid=loser_pid,
    )

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
        showdown_hands=None,     # Phase 6 (psychology at unseated tables) may populate
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
            events.append(HandEvent(
                type=HAND_EVENT_BUST,
                personality_id=pid,
                amount=starting_chips[pid],   # how much they lost
                opponent_pid=winner_pid if winner_pid != pid else None,
            ))

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
        events.append(HandEvent(
            type=HAND_EVENT_ALL_IN,
            personality_id=pid,
            amount=max(
                starting_chips.get(pid, 0),
                final_chips.get(pid, 0),
            ),
            opponent_pid=(
                loser_pid if pid == winner_pid else
                winner_pid if pid == loser_pid else
                None
            ),
        ))

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
) -> None:
    """Drive the state machine from PRE_FLOP to pot award.

    Mirrors the spike's loop: advance to EVALUATING_HAND, handling
    run-it-out auto-advance and per-action decisions. Decision-level
    exceptions silently fold (matches production tournament behavior;
    the math_floor 'jam' fix from Phase 0 means these should be near-
    zero, but the safety net stays as a last line of defense).

    `memory_manager` is the per-sandbox AIMemoryManager when wired —
    feeds opponent-stat tracking via `on_action` after each play_turn.
    None for legacy callers / tests not wiring memory.
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
                phase_name = (
                    phase_obj.name if phase_obj is not None else "PRE_FLOP"
                )
                # MemoryManager only cares about the four betting
                # streets; DEALING_CARDS/EVALUATING_HAND/SHOWDOWN don't
                # carry actions through this codepath, but guard anyway.
                if phase_name in ("PRE_FLOP", "FLOP", "TURN", "RIVER"):
                    active = [
                        p.name for p in gs.players
                        if not getattr(p, "is_folded", False)
                    ]
                    memory_manager.on_action(
                        player_name=actor_name,
                        action=action,
                        amount=amount,
                        phase=phase_name,
                        pot_total=getattr(gs, "pot", 0) or 0,
                        active_players=active,
                    )
            except Exception as exc:
                logger.debug(
                    "[FULL_SIM] on_action(%r, %r) failed: %s",
                    actor_name, action, exc,
                )

        adv = advance_to_next_active_player(gs)
        if adv is not None:
            gs = adv
        sm.game_state = gs
        actions += 1

    if sm.current_phase == PokerPhase.EVALUATING_HAND:
        winner_info = determine_winner(sm.game_state)
        sm.game_state = award_pot_winnings(sm.game_state, winner_info)

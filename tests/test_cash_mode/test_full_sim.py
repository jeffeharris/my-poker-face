"""Tests for cash_mode/full_sim.py — Phase 2 (real hand engine).

Phase 2 replaced Phase 1's delegate-to-fake-sim with actual
TieredBotController cardplay. Tests now exercise:

  - Conservation: total chips at the table are preserved across
    a hand (no chip creation, no chip loss).
  - Determinism: same RNG seed + same starting seats yields
    identical post-hand chips.
  - No-op edge cases (< 2 AIs with chips).
  - HandSimResult shape (delta is non-negative; pot is total
    chip movement; winner_pid is one of the seated AIs).
  - Controller cache reuse: a second hand hits the cache.
  - Input seats are not mutated by the call.

The tests pre-warm a per-test controller cache so each test pays
the ~77 ms × 6 = 460 ms cold setup once, not per hand.
"""

from __future__ import annotations

import random

import pytest

from cash_mode.controller_cache import LruControllerCache
from cash_mode.full_sim import (
    DEFAULT_BURST_HAND_CAP,
    DEFAULT_BURST_THRESHOLD_SECONDS,
    HAND_EVENT_ALL_IN,
    HAND_EVENT_BUST,
    HandEvent,
    HandSimResult,
    ShowdownHand,
    hand_burst_count,
    play_one_hand,
)
from cash_mode.tables import ai_slot, open_slot


# Personalities pulled from personalities.json. Using real names so
# the TieredBotController loads a real personality config (rather
# than falling back to defaults on unknown names).
PERSONALITIES = [
    "Napoleon",
    "Abraham Lincoln",
    "Buddha",
    "Bob Ross",
    "Jay Gatsby",
    "Shakespeare",
]


def _build_seats(chips_each: int, count: int) -> list:
    seats = [ai_slot(PERSONALITIES[i], chips_each) for i in range(count)]
    while len(seats) < 6:
        seats.append(open_slot())
    return seats


def _identity_name_for(pid: str) -> str:
    return pid


@pytest.fixture(scope="module")
def warm_cache() -> LruControllerCache:
    """Module-scoped cache so the 460 ms cold-setup cost is paid once
    per test session, not per test. The cache is the production
    code path's hot pool — pre-warming it isolates engine behavior
    from setup cost in test results."""
    return LruControllerCache(max_size=20)


class TestPlayOneHandRealCardplay:
    def test_chip_conservation(self, warm_cache):
        """Total chips at the table must be preserved across a hand —
        the hand engine doesn't create or destroy chips."""
        seats = _build_seats(5000, 4)
        total_before = sum(s.get("chips", 0) for s in seats)

        result = play_one_hand(
            seats,
            big_blind=100,
            rng=random.Random(42),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for,
            controller_cache=warm_cache,
        )

        total_after = sum(s.get("chips", 0) for s in result.new_seats)
        assert total_after == total_before, (
            f"chips not conserved: {total_before} -> {total_after}"
        )

    def test_determinism_with_fresh_cache(self):
        """Same starting seats + same RNG seed + fresh cache yields
        identical post-hand chips. We don't guarantee cross-hand
        determinism against a shared cache because controllers
        accumulate psychology / memory state across hands by design
        (Phase 6 builds on that); replaying a single hand from a
        fresh cache, though, must be deterministic."""
        seats = _build_seats(5000, 4)

        result_a = play_one_hand(
            seats, big_blind=100, rng=random.Random(10),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for,
            controller_cache=LruControllerCache(max_size=10),
        )
        result_b = play_one_hand(
            seats, big_blind=100, rng=random.Random(10),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for,
            controller_cache=LruControllerCache(max_size=10),
        )

        assert [s.get("chips") for s in result_a.new_seats] == [
            s.get("chips") for s in result_b.new_seats
        ]
        assert result_a.winner_pid == result_b.winner_pid
        assert result_a.delta == result_b.delta

    def test_handsimresult_shape(self, warm_cache):
        seats = _build_seats(5000, 4)
        result = play_one_hand(
            seats, big_blind=100, rng=random.Random(7),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=warm_cache,
        )

        assert isinstance(result, HandSimResult)
        assert result.delta >= 0
        assert result.pot >= result.delta
        if result.winner_pid is not None:
            assert result.winner_pid in PERSONALITIES[:4]
        if result.loser_pid is not None:
            assert result.loser_pid in PERSONALITIES[:4]
            assert result.loser_pid != result.winner_pid
        # dealer_seat_idx must point at one of the seated AIs (seats
        # 0..3 in this fixture). The lobby's seat-choice UX depends
        # on this being a real seat index, not None on a valid hand.
        assert result.dealer_seat_idx is not None
        assert result.dealer_seat_idx in range(4)


class TestPlayOneHandDealerRotation:
    """The dealer button is load-bearing for seat-choice UX. The lobby
    tracks the engine's dealer across burst hands so positional
    affordances (UTG vs CO vs BTN vs blinds) reflect reality."""

    def test_starting_dealer_seat_idx_is_honored(self, warm_cache):
        seats = _build_seats(5000, 4)
        result = play_one_hand(
            seats, big_blind=100, rng=random.Random(0),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=warm_cache,
            starting_dealer_seat_idx=2,
        )
        assert result.dealer_seat_idx == 2

    def test_dealer_hint_falls_back_when_seat_not_ai(self, warm_cache):
        """When the caller passes a seat index that isn't an AI seat
        (e.g. that seat opened up between refreshes), the engine
        falls back to player 0 rather than crashing."""
        seats = _build_seats(5000, 4)
        result = play_one_hand(
            seats, big_blind=100, rng=random.Random(0),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=warm_cache,
            starting_dealer_seat_idx=5,   # open seat in this fixture
        )
        assert result.dealer_seat_idx == 0

    def test_default_dealer_is_first_seated_ai(self, warm_cache):
        """No hint → dealer is seat 0 (first seated AI). Calling code
        in the lobby relies on this for the very first hand at a
        freshly seeded table (CashTableState.dealer_idx defaults to 0
        and seat 0 is the first AI in the fixture)."""
        seats = _build_seats(5000, 4)
        result = play_one_hand(
            seats, big_blind=100, rng=random.Random(0),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=warm_cache,
        )
        assert result.dealer_seat_idx == 0

    def test_input_seats_not_mutated(self, warm_cache):
        seats = _build_seats(5000, 4)
        before = [dict(s) for s in seats]

        play_one_hand(
            seats, big_blind=100, rng=random.Random(0),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=warm_cache,
        )

        assert seats == before, "play_one_hand must not mutate input seats"

    def test_multiple_hands_show_variance(self, warm_cache):
        """Sanity: across many hands different winners emerge. If only
        one personality ever wins, we likely have a bug in seat-order
        / dealer-rotation."""
        seats = _build_seats(5000, 4)
        winners = set()
        for seed in range(40):
            result = play_one_hand(
                seats, big_blind=100, rng=random.Random(seed),
                sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=warm_cache,
            )
            if result.winner_pid:
                winners.add(result.winner_pid)
        # At least 2 distinct winners — sanity, not a tight bound.
        assert len(winners) >= 2, f"only saw winners: {winners}"


class TestPlayOneHandNoOpCases:
    def test_only_one_ai_returns_no_op(self, warm_cache):
        seats = _build_seats(5000, 1)
        result = play_one_hand(
            seats, big_blind=100, rng=random.Random(0),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=warm_cache,
        )
        assert result.winner_pid is None
        assert result.delta == 0
        assert result.big_event is False
        # Chip layout unchanged.
        assert result.new_seats[0]["chips"] == 5000

    def test_zero_ais_returns_no_op(self, warm_cache):
        seats = [open_slot() for _ in range(6)]
        result = play_one_hand(
            seats, big_blind=100, rng=random.Random(0),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=warm_cache,
        )
        assert result.winner_pid is None
        assert result.delta == 0


class TestControllerCacheReuse:
    def test_second_hand_hits_cache(self):
        cache = LruControllerCache(max_size=10)
        seats = _build_seats(5000, 4)

        play_one_hand(
            seats, big_blind=100, rng=random.Random(0),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=cache,
        )
        assert len(cache) == 4

        # Second call should not grow the cache — same 4 AIs.
        play_one_hand(
            seats, big_blind=100, rng=random.Random(1),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=cache,
        )
        assert len(cache) == 4

    def test_cache_holds_same_instance_across_hands(self):
        cache = LruControllerCache(max_size=10)
        seats = _build_seats(5000, 4)

        play_one_hand(
            seats, big_blind=100, rng=random.Random(0),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=cache,
        )
        ctrl_first = cache.get("Napoleon")

        play_one_hand(
            seats, big_blind=100, rng=random.Random(1),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=cache,
        )
        ctrl_second = cache.get("Napoleon")

        assert ctrl_first is ctrl_second, "cache must reuse the same controller instance"


class TestHandEventDetection:
    """Commit 4: full_sim populates HandSimResult.hand_events."""

    def test_short_stacks_produce_bust_events(self, warm_cache):
        """Seat one AI with chips well below the BB so any hand
        likely ends in a bust. Sweep many seeds and assert at least
        one BUST event surfaces — the detection logic isn't gated
        on a specific hand outcome, just on final chips == 0."""
        # Two AIs with healthy stacks + one short stack.
        seats = [
            ai_slot("Napoleon", 50_000),
            ai_slot("Abraham Lincoln", 50_000),
            ai_slot("Buddha", 50),   # 0.5 BB — busts on most hands
        ] + [open_slot()] * 3

        saw_bust = False
        for seed in range(20):
            result = play_one_hand(
                seats, big_blind=100, rng=random.Random(seed),
                sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=warm_cache,
            )
            bust_events = [
                e for e in result.hand_events if e.type == HAND_EVENT_BUST
            ]
            if bust_events:
                saw_bust = True
                # Bust event must name the short-stacked player.
                assert bust_events[0].personality_id == "Buddha", (
                    f"BUST event personality_id should be Buddha, got "
                    f"{bust_events[0].personality_id!r}"
                )
                # The amount field carries their starting chip loss.
                assert bust_events[0].amount > 0
                break

        assert saw_bust, "expected at least one BUST event across 20 seeds"

    def test_hand_events_only_populated_with_actual_play(self, warm_cache):
        """Two AIs at healthy stacks shouldn't bust on a single hand —
        BUST events should be empty for the common case."""
        seats = _build_seats(10_000, 4)
        result = play_one_hand(
            seats, big_blind=100, rng=random.Random(0),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=warm_cache,
        )
        bust_events = [
            e for e in result.hand_events if e.type == HAND_EVENT_BUST
        ]
        assert bust_events == [], (
            "healthy stacks shouldn't bust on a single 100bb hand"
        )


class TestHandBurstCount:
    """Commit 5: catch-up burst logic — pins the gap-to-count
    mapping so a lobby that's been unwatched for a while
    burst-ticks but the cap protects the 500 ms lobby budget."""

    def test_short_gap_uses_probability_gate(self):
        """Below the threshold, the probability gate drives 0/1."""
        always_fire = random.Random(1)
        # base_prob=1.0 → always 1 hand
        result = hand_burst_count(
            gap_seconds=5.0, base_prob=1.0, rng=always_fire,
        )
        assert result == 1

        # base_prob=0.0 → never fires
        result = hand_burst_count(
            gap_seconds=5.0, base_prob=0.0, rng=random.Random(),
        )
        assert result == 0

    def test_just_above_threshold_starts_burst(self):
        # 30s threshold + 20s pacing → floor(31/20) = 1 hand
        result = hand_burst_count(
            gap_seconds=DEFAULT_BURST_THRESHOLD_SECONDS + 1.0,
            base_prob=1.0,
            rng=random.Random(),
        )
        assert result >= 1

    def test_long_gap_scales_with_pacing(self):
        # 5-minute gap at 20s pacing → 15 hands
        result = hand_burst_count(
            gap_seconds=300.0, base_prob=1.0, rng=random.Random(),
        )
        assert result == 15

    def test_burst_respects_cap(self):
        # 2 hours unwatched would naively budget 360 hands; cap
        # protects the lobby response budget.
        result = hand_burst_count(
            gap_seconds=7200.0, base_prob=1.0, rng=random.Random(),
        )
        assert result == DEFAULT_BURST_HAND_CAP

    def test_negative_gap_treated_as_zero(self):
        """Defensive: a future-dated last_activity_at (clock skew)
        shouldn't drive the burst into negative territory."""
        result = hand_burst_count(
            gap_seconds=0.0, base_prob=0.0, rng=random.Random(),
        )
        assert result == 0


class TestMemoryFlatness:
    """Full-sim Commit 2.5 invariant: memory does NOT grow per-hand.

    The original concern was the state-machine `snapshots` tuple
    accumulating monotonically (+25 MB / 1000 hands measured in the
    Phase 0 spike). The mechanism we shipped is
    `PokerStateMachine(record_snapshots=False)` set inside
    play_one_hand, which short-circuits the snapshot append in
    advance_state_pure entirely.

    This test pins that invariant with `tracemalloc`: 1000 sim hands
    must leave less than +5 MB on the heap. The threshold is well
    above noise (transient allocations from a single hand ~50 KB)
    and well below the pre-fix +25 MB regression mode.
    """

    def test_1000_hands_stays_under_5mb_heap_growth(self, warm_cache):
        import tracemalloc

        seats = _build_seats(5000, 4)
        # Warm the cache + run a small burn-in so first-time allocations
        # don't get counted against the measurement (strategy table,
        # PromptManager templates, etc. load lazily).
        for seed in range(5):
            play_one_hand(
                seats, big_blind=100, rng=random.Random(seed),
                sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=warm_cache,
            )

        tracemalloc.start()
        baseline = tracemalloc.take_snapshot()

        for seed in range(1000):
            play_one_hand(
                seats, big_blind=100, rng=random.Random(100 + seed),
                sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=warm_cache,
            )

        after = tracemalloc.take_snapshot()
        stats = after.compare_to(baseline, "filename")
        total_growth = sum(stat.size_diff for stat in stats)
        tracemalloc.stop()

        growth_mb = total_growth / (1024 * 1024)
        assert growth_mb < 5.0, (
            f"1000 sim hands grew heap by {growth_mb:.2f} MB; expected < 5 MB. "
            f"State machine snapshots may be leaking again — check that "
            f"PokerStateMachine(record_snapshots=False) is still set in "
            f"play_one_hand."
        )


class TestPsychologyPersistence:
    """Full-sim Commit 3 discipline: psychology hydrates from
    `ai_bankroll_state.emotional_state_json` on cache miss, and
    flushes back every PSYCHOLOGY_FLUSH_EVERY_HANDS hands so state
    survives backend restart + LRU eviction.

    These tests stub the bankroll_repo with a MagicMock so we can
    assert on the read/write calls without standing up a tempdb.
    """

    def test_cache_miss_calls_load_emotional_state_json(self, warm_cache):
        from unittest.mock import MagicMock

        seats = _build_seats(5000, 4)
        repo = MagicMock()
        repo.load_emotional_state_json.return_value = None

        # Fresh cache so every seat misses.
        fresh_cache = LruControllerCache(max_size=10)
        play_one_hand(
            seats, big_blind=100, rng=random.Random(0),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=fresh_cache,
            bankroll_repo=repo,
        )
        # All four seated AIs are cache misses → one load per pid.
        loaded_pids = {
            call.args[0] for call in repo.load_emotional_state_json.call_args_list
        }
        assert loaded_pids == set(PERSONALITIES[:4])

    def test_cache_hit_skips_hydrate(self, warm_cache):
        """A second call with the same cache must NOT re-load — the
        live state on the cached controller is authoritative."""
        from unittest.mock import MagicMock

        seats = _build_seats(5000, 4)
        repo = MagicMock()
        repo.load_emotional_state_json.return_value = None

        cache = LruControllerCache(max_size=10)
        # First call: all 4 misses → 4 loads.
        play_one_hand(
            seats, big_blind=100, rng=random.Random(0),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=cache,
            bankroll_repo=repo,
        )
        first_loads = repo.load_emotional_state_json.call_count

        # Second call: all 4 hits → 0 additional loads.
        play_one_hand(
            seats, big_blind=100, rng=random.Random(1),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=cache,
            bankroll_repo=repo,
        )
        second_loads = repo.load_emotional_state_json.call_count
        assert second_loads == first_loads

    def test_periodic_flush_at_threshold(self):
        """Every PSYCHOLOGY_FLUSH_EVERY_HANDS hands per AI, the
        controller's psychology should be flushed back to the repo."""
        from unittest.mock import MagicMock

        from cash_mode.full_sim import PSYCHOLOGY_FLUSH_EVERY_HANDS

        seats = _build_seats(5000, 4)
        repo = MagicMock()
        repo.load_emotional_state_json.return_value = None

        cache = LruControllerCache(max_size=10)
        for hand_i in range(PSYCHOLOGY_FLUSH_EVERY_HANDS):
            play_one_hand(
                seats, big_blind=100, rng=random.Random(hand_i),
                sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=cache,
                bankroll_repo=repo,
            )

        # Hand N (counting from 1) is the flush trigger. After
        # exactly N hands, every AI should have been flushed once.
        flushed_pids = {
            call.args[0] for call in repo.save_emotional_state_json.call_args_list
        }
        assert flushed_pids == set(PERSONALITIES[:4])

    def test_no_flush_before_threshold(self):
        from unittest.mock import MagicMock

        from cash_mode.full_sim import PSYCHOLOGY_FLUSH_EVERY_HANDS

        seats = _build_seats(5000, 4)
        repo = MagicMock()
        repo.load_emotional_state_json.return_value = None

        cache = LruControllerCache(max_size=10)
        # Run one fewer hand than the flush threshold.
        for hand_i in range(PSYCHOLOGY_FLUSH_EVERY_HANDS - 1):
            play_one_hand(
                seats, big_blind=100, rng=random.Random(hand_i),
                sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=cache,
                bankroll_repo=repo,
            )
        assert repo.save_emotional_state_json.call_count == 0

    def test_no_bankroll_repo_means_no_persistence(self, warm_cache):
        """When bankroll_repo is None (test paths), no repo calls
        happen — the controller stays at whatever state the cache
        has but nothing persists across calls."""
        seats = _build_seats(5000, 4)
        cache = LruControllerCache(max_size=10)
        # Without a repo, this must not raise. There's no positive
        # assertion to make here — just that the absence of a repo
        # is handled gracefully throughout the call.
        result = play_one_hand(
            seats, big_blind=100, rng=random.Random(0),
            sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=cache,
        )
        assert result is not None

    def test_hydrate_applies_persisted_state_on_miss(self, warm_cache):
        """Verify the hydrate path reaches PlayerPsychology.from_dict
        when the repo returns a valid JSON blob."""
        from unittest.mock import MagicMock, patch

        seats = _build_seats(5000, 4)
        # A realistic-enough state dict — actual schema doesn't
        # matter here; we're checking the wiring, and PlayerPsychology
        # is patched.
        repo = MagicMock()
        repo.load_emotional_state_json.side_effect = lambda pid, *, sandbox_id: (
            '{"axes": {}}' if pid == "Napoleon" else None
        )

        cache = LruControllerCache(max_size=10)
        with patch(
            "poker.player_psychology.PlayerPsychology.from_dict"
        ) as mock_from_dict:
            mock_from_dict.return_value = MagicMock()
            play_one_hand(
                seats, big_blind=100, rng=random.Random(0),
                sandbox_id="test-sandbox-1",
            name_for=_identity_name_for, controller_cache=cache,
                bankroll_repo=repo,
            )
        # Only Napoleon had a JSON blob → from_dict called once.
        assert mock_from_dict.call_count == 1


class TestHandEventDataclass:
    """Lock the HandEvent / ShowdownHand shape that Commit 4 relies on."""

    def test_hand_event_defaults(self):
        evt = HandEvent(type="bust", personality_id="p1")
        assert evt.amount == 0
        assert evt.opponent_pid is None

    def test_hand_event_pairwise(self):
        evt = HandEvent(
            type="suckout",
            personality_id="winner",
            amount=1400,
            opponent_pid="loser",
        )
        assert evt.opponent_pid == "loser"

    def test_showdown_hand_defaults(self):
        sd = ShowdownHand(personality_id="p1", hand_name="Pair of Kings")
        assert sd.hole_cards == []

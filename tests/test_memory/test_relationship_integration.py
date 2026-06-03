"""End-to-end tests for AIMemoryManager → HandOutcomeDetector → record_event.

This is the Phase 3 commit 4 wiring test. The detector + dispatch
ship in earlier commits with unit coverage; this file verifies the
integration point in `AIMemoryManager._process_relationship_events`
(called from `on_hand_complete`):

  - Without `set_relationship_repo`, the manager is detector-silent.
  - With a repo and a big-pot hand, relationship state mutates.
  - With cash_mode=True, cash_pair_stats updates too.
  - With cash_mode=False (tournament), cash_pair_stats stays empty.
  - Dedup: replaying the same hand twice through the same manager
    doesn't double-apply events.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pytestmark = pytest.mark.integration

from poker.memory.hand_history import (
    PlayerHandInfo,
    RecordedAction,
    RecordedHand,
    WinnerInfo,
)
from poker.memory.memory_manager import AIMemoryManager
from poker.repositories.relationship_repository import RelationshipRepository
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "rel.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def repo(db_path):
    r = RelationshipRepository(db_path)
    yield r
    r.close()


def _big_heads_up_hand(hand_number: int = 1) -> RecordedHand:
    players = (
        PlayerHandInfo(name="alice", starting_stack=1000, position="BTN", is_human=False),
        PlayerHandInfo(name="bob", starting_stack=1000, position="BB", is_human=False),
    )
    actions = (
        RecordedAction(
            player_name="alice",
            action="raise",
            amount=400,
            phase="PRE_FLOP",
            pot_after=400,
        ),
        RecordedAction(
            player_name="bob",
            action="call",
            amount=400,
            phase="PRE_FLOP",
            pot_after=800,
        ),
    )
    return RecordedHand(
        game_id="g1",
        hand_number=hand_number,
        timestamp=datetime(2026, 5, 18, 12, 0),
        players=players,
        hole_cards={"alice": ["Ah", "Ks"], "bob": ["7h", "2d"]},
        community_cards=("2c", "7d", "9s", "Th", "Jc"),
        actions=actions,
        winners=(
            WinnerInfo(
                name="alice",
                amount_won=800,
                hand_name="Pair",
                hand_rank=8,
            ),
        ),
        pot_size=800,
        was_showdown=True,
    )


class TestSilentWithoutRepo:
    def test_no_repo_no_dispatch(self):
        # No relationship_repo wired → detector path no-ops.
        mgr = AIMemoryManager(game_id="g1", db_path=None)
        mgr.initialize_for_player("alice")
        mgr.initialize_for_player("bob")

        # Direct call to the helper that on_hand_complete uses.
        mgr._process_relationship_events(_big_heads_up_hand())

        # No exceptions; opponent_model_manager.record_event was never
        # invoked (would have raised — no repo at construction). The
        # detector itself ran but its output was silently dropped.
        # Sanity: no opponent_model would have a memorable hand.
        model = mgr.opponent_model_manager.get_model_if_exists("alice", "bob")
        if model is not None:
            assert model.memorable_hands == []


class TestRelationshipStatePopulates:
    def test_big_pot_writes_axes(self, repo):
        mgr = AIMemoryManager(game_id="g1", db_path=None)
        mgr.initialize_for_player("alice")
        mgr.initialize_for_player("bob")
        mgr.set_relationship_repo(repo, cash_mode=False)

        mgr._process_relationship_events(_big_heads_up_hand())

        alice_view = repo.load_raw_relationship_state("alice", "bob")
        bob_view = repo.load_raw_relationship_state("bob", "alice")
        assert alice_view is not None
        assert bob_view is not None

    def test_small_pot_no_writes(self, repo):
        mgr = AIMemoryManager(game_id="g1", db_path=None)
        mgr.initialize_for_player("alice")
        mgr.initialize_for_player("bob")
        mgr.set_relationship_repo(repo, cash_mode=False)

        # Small pot — under the big-pot threshold, no event.
        small = RecordedHand(
            game_id="g1",
            hand_number=1,
            timestamp=datetime(2026, 5, 18, 12, 0),
            players=(
                PlayerHandInfo(name="alice", starting_stack=1000, position="BTN", is_human=False),
                PlayerHandInfo(name="bob", starting_stack=1000, position="BB", is_human=False),
            ),
            hole_cards={"alice": ["Ah", "Ks"], "bob": ["7h", "2d"]},
            community_cards=("2c", "7d", "9s", "Th", "Jc"),
            actions=(
                RecordedAction(
                    player_name="alice", action="raise", amount=25, phase="PRE_FLOP", pot_after=25
                ),
                RecordedAction(
                    player_name="bob", action="call", amount=25, phase="PRE_FLOP", pot_after=50
                ),
            ),
            winners=(WinnerInfo(name="alice", amount_won=50, hand_name="High", hand_rank=10),),
            pot_size=50,
            was_showdown=True,
        )
        mgr._process_relationship_events(small)
        assert repo.load_raw_relationship_state("alice", "bob") is None


class TestCashModeGate:
    def test_cash_mode_writes_cash_pair_stats(self, repo):
        mgr = AIMemoryManager(game_id="g1", db_path=None)
        mgr.initialize_for_player("alice")
        mgr.initialize_for_player("bob")
        mgr.set_relationship_repo(repo, cash_mode=True, sandbox_id="sb-1")

        mgr._process_relationship_events(_big_heads_up_hand())

        alice_stats = repo.load_cash_pair_stats("alice", "bob", sandbox_id="sb-1")
        bob_stats = repo.load_cash_pair_stats("bob", "alice", sandbox_id="sb-1")
        # alice net +400 from bob (her contribution 400, collected 800).
        assert alice_stats.cumulative_pnl == 400
        assert alice_stats.hands_played_cash == 1
        assert bob_stats.cumulative_pnl == -400
        assert bob_stats.hands_played_cash == 1

    def test_tournament_mode_no_cash_pair_stats(self, repo):
        mgr = AIMemoryManager(game_id="g1", db_path=None)
        mgr.initialize_for_player("alice")
        mgr.initialize_for_player("bob")
        mgr.set_relationship_repo(repo, cash_mode=False)

        mgr._process_relationship_events(_big_heads_up_hand())

        # Relationship state writes happen.
        assert repo.load_raw_relationship_state("alice", "bob") is not None
        # Cash pair stats stay empty.
        assert repo.load_cash_pair_stats("alice", "bob") is None
        assert repo.load_cash_pair_stats("bob", "alice") is None

    def test_cash_mode_without_sandbox_skips_pair_stats(self, repo):
        # Defensive: cash_mode=True with no sandbox_id is a misconfiguration
        # (admin Chip Economy panel wouldn't be able to scope), so the
        # dispatch silently skips the cash_pair_stats writes rather than
        # falling back to an empty-string bucket.
        mgr = AIMemoryManager(game_id="g1", db_path=None)
        mgr.initialize_for_player("alice")
        mgr.initialize_for_player("bob")
        mgr.set_relationship_repo(repo, cash_mode=True)  # sandbox_id omitted

        mgr._process_relationship_events(_big_heads_up_hand())

        # Relationship state writes still happen.
        assert repo.load_raw_relationship_state("alice", "bob") is not None
        # Cash pair stats stay empty (no sandbox to attribute to).
        assert repo.load_cash_pair_stats("alice", "bob") is None
        assert repo.load_cash_pair_stats("bob", "alice") is None


class TestDedupAtIntegration:
    def test_replaying_same_hand_doesnt_double_apply(self, repo):
        mgr = AIMemoryManager(game_id="g1", db_path=None)
        mgr.initialize_for_player("alice")
        mgr.initialize_for_player("bob")
        mgr.set_relationship_repo(repo, cash_mode=True, sandbox_id="sb-1")

        hand = _big_heads_up_hand()
        mgr._process_relationship_events(hand)
        # Snapshot after first pass.
        first_pnl = repo.load_cash_pair_stats(
            "alice",
            "bob",
            sandbox_id="sb-1",
        ).cumulative_pnl
        first_heat = repo.load_raw_relationship_state("alice", "bob").heat

        # Replay the same hand.
        mgr._process_relationship_events(hand)

        second_pnl = repo.load_cash_pair_stats(
            "alice",
            "bob",
            sandbox_id="sb-1",
        ).cumulative_pnl
        second_heat = repo.load_raw_relationship_state("alice", "bob").heat
        assert second_pnl == first_pnl
        assert second_heat == first_heat


class TestStackDominanceWiring:
    """End-to-end coverage of the STACK_DOMINANCE closure built in
    `_process_relationship_events`. Each guard (`cash_mode`,
    `table_max_buy_in`, `sandbox_id`, observer-has-negative-pnl) gates
    a different branch — verify the seam works as intended."""

    @staticmethod
    def _hand_with_deep_stack(hand_number: int = 1) -> RecordedHand:
        # Same heads-up structure as `_big_heads_up_hand` but with
        # alice sitting on 3× max buy-in (= 15_000 chips at our test
        # cap of 5_000). The hand itself doesn't matter beyond
        # carrying the seat snapshot — STACK_DOMINANCE reads
        # `starting_stack` and ignores the action history.
        players = (
            PlayerHandInfo(name="alice", starting_stack=15_000, position="BTN", is_human=False),
            PlayerHandInfo(name="bob", starting_stack=2_000, position="BB", is_human=False),
        )
        return RecordedHand(
            game_id="g1",
            hand_number=hand_number,
            timestamp=datetime(2026, 5, 23, 12, 0),
            players=players,
            hole_cards={"alice": ["Ah", "Ks"], "bob": ["7h", "2d"]},
            community_cards=(),
            actions=(),
            winners=(),
            pot_size=0,
            was_showdown=False,
        )

    def test_no_max_buy_in_skips_dominance_event(self, repo):
        # cash_mode=True, sandbox wired, but no table cap → detector
        # skips STACK_DOMINANCE entirely.
        mgr = AIMemoryManager(game_id="g1", db_path=None)
        mgr.initialize_for_player("alice")
        mgr.initialize_for_player("bob")
        mgr.set_relationship_repo(repo, cash_mode=True, sandbox_id="sb-1")
        # bob already net-down vs alice — would qualify if cap were set.
        repo.apply_cash_pair_pnl(
            winner_id="alice",
            loser_id="bob",
            chips=500,
            sandbox_id="sb-1",
        )

        mgr._process_relationship_events(self._hand_with_deep_stack())

        # bob's view of alice: no STACK_DOMINANCE → likability stays
        # at default (the big-pot path didn't fire either; small,
        # zero-pot synthetic hand).
        bob_view = repo.load_raw_relationship_state("bob", "alice")
        if bob_view is not None:
            # Default likability is 0.5; any drop below that means
            # STACK_DOMINANCE leaked through.
            assert bob_view.likability == pytest.approx(0.5)

    def test_missing_sandbox_skips_dominance(self, repo):
        # cash_mode=True + cap set, but no sandbox_id. The PnL gate
        # can't be built (cash_pair_stats is sandbox-scoped), so the
        # detector must skip rather than emit ungated — otherwise
        # every seated peer would resent every deep stack regardless
        # of whether they've actually lost chips to them.
        mgr = AIMemoryManager(game_id="g1", db_path=None)
        mgr.initialize_for_player("alice")
        mgr.initialize_for_player("bob")
        mgr.set_relationship_repo(
            repo,
            cash_mode=True,
            table_max_buy_in=5_000,
            # sandbox_id intentionally omitted
        )

        mgr._process_relationship_events(self._hand_with_deep_stack())

        bob_view = repo.load_raw_relationship_state("bob", "alice")
        if bob_view is not None:
            assert bob_view.likability == pytest.approx(0.5)

    def test_tournament_mode_skips_dominance(self, repo):
        # cash_mode=False → no PnL gating possible, detector silent.
        mgr = AIMemoryManager(game_id="g1", db_path=None)
        mgr.initialize_for_player("alice")
        mgr.initialize_for_player("bob")
        mgr.set_relationship_repo(repo, cash_mode=False, table_max_buy_in=5_000)

        mgr._process_relationship_events(self._hand_with_deep_stack())

        bob_view = repo.load_raw_relationship_state("bob", "alice")
        if bob_view is not None:
            assert bob_view.likability == pytest.approx(0.5)

    def test_fires_when_observer_is_net_down_and_cap_wired(self, repo):
        # Full happy path: cash_mode, sandbox, cap, AND observer has
        # negative cumulative_pnl against the deep stack. Verifies the
        # closure built in _process_relationship_events plumbs all
        # four args into detect_events correctly.
        mgr = AIMemoryManager(game_id="g1", db_path=None)
        mgr.initialize_for_player("alice")
        mgr.initialize_for_player("bob")
        mgr.set_relationship_repo(
            repo,
            cash_mode=True,
            sandbox_id="sb-1",
            table_max_buy_in=5_000,
        )
        # Pre-populate: alice has taken 500 chips from bob.
        repo.apply_cash_pair_pnl(
            winner_id="alice",
            loser_id="bob",
            chips=500,
            sandbox_id="sb-1",
        )

        mgr._process_relationship_events(self._hand_with_deep_stack())

        bob_view = repo.load_raw_relationship_state("bob", "alice")
        assert bob_view is not None
        # bob's likability toward alice dropped below default — the
        # only path that moves it on a zero-pot synthetic hand is
        # STACK_DOMINANCE.
        assert bob_view.likability < 0.5
        assert bob_view.respect < 0.5
        # Heat untouched: envy is not hostility.
        assert bob_view.heat == 0.0

    def test_multi_hand_accumulation_through_persistent_repo(self, repo):
        # Drip behavior: each hand at 2× cap subtracts a small amount
        # of likability. Over N hands the persistent repo should show
        # the running total — confirms the projection-on-read +
        # clamp + persist cycle composes correctly across multiple
        # _process_relationship_events calls (not just the dedup
        # path within a single hand_number).
        mgr = AIMemoryManager(game_id="g1", db_path=None)
        mgr.initialize_for_player("alice")
        mgr.initialize_for_player("bob")
        mgr.set_relationship_repo(
            repo,
            cash_mode=True,
            sandbox_id="sb-1",
            table_max_buy_in=5_000,
        )
        # bob has lost to alice → eligible to feel STACK_DOMINANCE.
        repo.apply_cash_pair_pnl(
            winner_id="alice",
            loser_id="bob",
            chips=500,
            sandbox_id="sb-1",
        )

        # Process 5 firing hands. STACK_DOMINANCE is throttled to once per
        # STACK_DOMINANCE_COOLDOWN_HANDS per pair, so space the hand_numbers by
        # the cooldown to get 5 independent drips (consecutive hands would
        # collapse to one — that throttle is covered by the test below).
        from poker.memory.hand_outcome_detector import STACK_DOMINANCE_COOLDOWN_HANDS

        fire_hands = [1 + i * STACK_DOMINANCE_COOLDOWN_HANDS for i in range(5)]
        for hand_number in fire_hands:
            mgr._process_relationship_events(
                self._hand_with_deep_stack(hand_number=hand_number),
            )

        bob_view = repo.load_raw_relationship_state("bob", "alice")
        assert bob_view is not None
        # At 3× cap, excess = 1.5; per-hand likability shift =
        # −0.003 × 1.5 = −0.0045. Over 5 firing hands that's −0.0225.
        # Default likability is 0.5, so expect ~0.4775.
        expected = 0.5 - (0.003 * 1.5 * 5)
        assert bob_view.likability == pytest.approx(expected, abs=1e-6)
        # Respect shifts similarly at −0.002 × 1.5 per hand.
        expected_respect = 0.5 - (0.002 * 1.5 * 5)
        assert bob_view.respect == pytest.approx(expected_respect, abs=1e-6)

    def test_stack_dominance_throttled_to_cooldown(self, repo):
        # The per-pair cooldown caps STACK_DOMINANCE to one drip per
        # STACK_DOMINANCE_COOLDOWN_HANDS hands, so a long-seated deep stack
        # doesn't flood the relationship layer. Firing on every consecutive
        # hand inside one window must collapse to a single drip.
        from poker.memory.hand_outcome_detector import STACK_DOMINANCE_COOLDOWN_HANDS

        mgr = AIMemoryManager(game_id="g1", db_path=None)
        mgr.initialize_for_player("alice")
        mgr.initialize_for_player("bob")
        mgr.set_relationship_repo(repo, cash_mode=True, sandbox_id="sb-1", table_max_buy_in=5_000)
        repo.apply_cash_pair_pnl(winner_id="alice", loser_id="bob", chips=500, sandbox_id="sb-1")

        # Consecutive hands within one cooldown window → only the first fires.
        for hand_number in range(1, STACK_DOMINANCE_COOLDOWN_HANDS + 1):
            mgr._process_relationship_events(
                self._hand_with_deep_stack(hand_number=hand_number),
            )

        bob_view = repo.load_raw_relationship_state("bob", "alice")
        assert bob_view is not None
        # Exactly one drip despite COOLDOWN consecutive hands.
        assert bob_view.likability == pytest.approx(0.5 - 0.003 * 1.5, abs=1e-6)
        assert bob_view.respect == pytest.approx(0.5 - 0.002 * 1.5, abs=1e-6)

    def test_set_table_max_buy_in_setter_enables_detection(self, repo):
        # Cold-load production path: set_relationship_repo runs BEFORE
        # the stake_label is resolved, then set_table_max_buy_in fills
        # in the cap separately. Verify that path actually arms the
        # detector — calling set_table_max_buy_in after the repo wire
        # is what game_routes.py:722 does on restore.
        mgr = AIMemoryManager(game_id="g1", db_path=None)
        mgr.initialize_for_player("alice")
        mgr.initialize_for_player("bob")
        # First wire: no cap yet (mimics game_routes.py cold-load).
        mgr.set_relationship_repo(
            repo,
            cash_mode=True,
            sandbox_id="sb-1",
        )
        # Pre-populate PnL so bob would qualify if cap were set.
        repo.apply_cash_pair_pnl(
            winner_id="alice",
            loser_id="bob",
            chips=500,
            sandbox_id="sb-1",
        )
        # Sanity: without the cap, no STACK_DOMINANCE fires.
        mgr._process_relationship_events(self._hand_with_deep_stack(hand_number=1))
        baseline = repo.load_raw_relationship_state("bob", "alice")
        baseline_lik = baseline.likability if baseline is not None else 0.5
        assert baseline_lik == pytest.approx(0.5)

        # Now fill in the cap via the separate setter — same flow
        # as the cold-load path after stake_label resolves.
        mgr.set_table_max_buy_in(5_000)
        mgr._process_relationship_events(self._hand_with_deep_stack(hand_number=2))

        bob_view = repo.load_raw_relationship_state("bob", "alice")
        assert bob_view is not None
        # likability dropped — proves the setter armed the detector.
        assert bob_view.likability < 0.5

    def test_no_fire_when_observer_is_net_up(self, repo):
        # Same setup but bob is the one who took chips from alice.
        # bob shouldn't resent alice for being deep — bob has
        # actually been winning. PnL gate filters this case out.
        mgr = AIMemoryManager(game_id="g1", db_path=None)
        mgr.initialize_for_player("alice")
        mgr.initialize_for_player("bob")
        mgr.set_relationship_repo(
            repo,
            cash_mode=True,
            sandbox_id="sb-1",
            table_max_buy_in=5_000,
        )
        # bob took 500 from alice — bob's PnL vs alice is +500.
        repo.apply_cash_pair_pnl(
            winner_id="bob",
            loser_id="alice",
            chips=500,
            sandbox_id="sb-1",
        )

        mgr._process_relationship_events(self._hand_with_deep_stack())

        bob_view = repo.load_raw_relationship_state("bob", "alice")
        if bob_view is not None:
            assert bob_view.likability == pytest.approx(0.5)


class TestRegistryUpdatePropagates:
    def test_register_player_id_after_init_uses_new_id(self, repo):
        # The detector shares name_to_id by reference with the manager,
        # so registering a personality_id after the detector is built
        # changes the (actor_id, target_id) on the next emission.
        mgr = AIMemoryManager(game_id="g1", db_path=None)
        mgr.initialize_for_player("alice", personality_id="alice_v1")
        mgr.initialize_for_player("bob", personality_id="bob_v1")
        mgr.set_relationship_repo(repo, cash_mode=False)

        mgr._process_relationship_events(_big_heads_up_hand())

        # Relationship rows keyed on the registered personality_ids,
        # not the display names.
        assert repo.load_raw_relationship_state("alice_v1", "bob_v1") is not None
        assert repo.load_raw_relationship_state("bob_v1", "alice_v1") is not None
        # No name-keyed rows should exist.
        assert repo.load_raw_relationship_state("alice", "bob") is None


def _four_seat_big_pot_hand(hand_number: int = 1) -> RecordedHand:
    """Mirrors the bug-report session: 1 human + 3 AIs, big pot, showdown.

    Jeff (human) calls down two streets and loses 47K to Oscar Wilde's
    set. Big enough to clear MomentAnalyzer.is_big_pot vs the 40-70K
    starting stacks → BIG_WIN/BIG_LOSS should fire and write
    relationship_states + cash_pair_stats + memorable_hands.
    """
    players = (
        PlayerHandInfo(
            name="Jeff",
            starting_stack=70000,
            position="BTN",
            is_human=True,
        ),
        PlayerHandInfo(
            name="Oscar Wilde",
            starting_stack=40000,
            position="SB",
            is_human=False,
        ),
        PlayerHandInfo(
            name="Jay Gatsby",
            starting_stack=40000,
            position="BB",
            is_human=False,
        ),
        PlayerHandInfo(
            name="Cheshire Cat",
            starting_stack=40000,
            position="UTG",
            is_human=False,
        ),
    )
    actions = (
        RecordedAction("Cheshire Cat", "fold", 0, "PRE_FLOP", 30),
        RecordedAction("Jeff", "raise", 1000, "PRE_FLOP", 1010),
        RecordedAction("Oscar Wilde", "raise", 4000, "PRE_FLOP", 4500),
        RecordedAction("Jay Gatsby", "fold", 0, "PRE_FLOP", 4500),
        RecordedAction("Jeff", "call", 3000, "PRE_FLOP", 8500),
        RecordedAction("Jeff", "check", 0, "FLOP", 8500),
        RecordedAction("Oscar Wilde", "bet", 8000, "FLOP", 16500),
        RecordedAction("Jeff", "call", 8000, "FLOP", 24500),
        RecordedAction("Jeff", "check", 0, "TURN", 24500),
        RecordedAction("Oscar Wilde", "bet", 11540, "TURN", 36040),
        RecordedAction("Jeff", "call", 11540, "TURN", 47580),
    )
    return RecordedHand(
        game_id="cash-repro",
        hand_number=hand_number,
        timestamp=datetime(2026, 5, 23, 12, 30),
        players=players,
        hole_cards={
            "Jeff": ["Ah", "Kd"],
            "Oscar Wilde": ["Qs", "Qd"],
        },
        community_cards=("2c", "7d", "Qh", "9s", "3c"),
        actions=actions,
        winners=(
            WinnerInfo(
                name="Oscar Wilde",
                amount_won=47580,
                hand_name="Set of Queens",
                hand_rank=7,
            ),
        ),
        pot_size=47580,
        was_showdown=True,
    )


def _wire_four_seat_manager(repo, *, sandbox_id="sb-cash"):
    """Set up an AIMemoryManager the same way `/api/cash/start` does:
    initialize 3 AI players + 1 human observer, then wire the
    relationship repo with cash_mode=True. Pre-populate `self.models`
    for each pair so the memorable-hand sidecar path can find the
    actor's model.
    """
    mgr = AIMemoryManager(
        game_id="cash-repro",
        db_path=None,
        owner_id="guest_jeff",
    )
    mgr.initialize_for_player("Oscar Wilde", personality_id="oscar_wilde")
    mgr.initialize_for_player("Jay Gatsby", personality_id="jay_gatsby")
    mgr.initialize_for_player("Cheshire Cat", personality_id="cheshire_cat")
    mgr.initialize_human_observer("Jeff", personality_id="guest_jeff")
    mgr.set_relationship_repo(
        repo,
        cash_mode=True,
        sandbox_id=sandbox_id,
    )
    # observe_action is what populates self.models in production; the
    # memorable-hand sidecar in record_event reads it. Pre-touch every
    # pair so the in-memory dict matches a session that has played at
    # least one prior action.
    for obs in ("Jeff", "Oscar Wilde", "Jay Gatsby", "Cheshire Cat"):
        for opp in ("Jeff", "Oscar Wilde", "Jay Gatsby", "Cheshire Cat"):
            if obs != opp:
                mgr.opponent_model_manager.get_model(obs, opp)
    return mgr


class TestFourSeatCashHandReproduction:
    """Mirrors the bug-report session structure: 4 seats, big pot,
    cash mode. Verifies the full chain writes relationship_states,
    cash_pair_stats, and in-memory memorable_hands.
    """

    def test_big_pot_writes_everything(self, repo):
        mgr = _wire_four_seat_manager(repo)
        hand = _four_seat_big_pot_hand()

        mgr._process_relationship_events(hand)

        # relationship_states: bilateral writes for the (winner, loser)
        # pair using personality_ids (lower-case slugs).
        assert (
            repo.load_raw_relationship_state(
                "oscar_wilde",
                "guest_jeff",
            )
            is not None
        )
        assert (
            repo.load_raw_relationship_state(
                "guest_jeff",
                "oscar_wilde",
            )
            is not None
        )

        # cash_pair_stats: PnL accumulates per (sandbox, observer, opponent).
        oscar_stats = repo.load_cash_pair_stats(
            "oscar_wilde",
            "guest_jeff",
            sandbox_id="sb-cash",
        )
        jeff_stats = repo.load_cash_pair_stats(
            "guest_jeff",
            "oscar_wilde",
            sandbox_id="sb-cash",
        )
        assert oscar_stats is not None and oscar_stats.cumulative_pnl > 0
        assert jeff_stats is not None and jeff_stats.cumulative_pnl < 0
        assert oscar_stats.cumulative_pnl == -jeff_stats.cumulative_pnl

        # memorable_hands: actor's in-memory model has the sidecar entry
        # for high-impact events (impact_score >= 0.7).
        oscar_view = mgr.opponent_model_manager.models["Oscar Wilde"]["Jeff"]
        jeff_view = mgr.opponent_model_manager.models["Jeff"]["Oscar Wilde"]
        assert oscar_view.memorable_hands, "Oscar's model should have a memorable BIG_WIN entry"
        assert (
            jeff_view.memorable_hands
        ), "Jeff's model should have a memorable BIG_LOSS / DOMINATED entry"


class TestColdLoadRewiresRelationshipRepo:
    """Cold-load path bug: replacing `opponent_model_manager` after
    `set_relationship_repo` drops the wired repo, so subsequent
    `record_event` calls raise RuntimeError silently swallowed by
    `_process_relationship_events`. None of the surfaces — relationship
    state, cash pair stats, memorable hands — get written.

    This regression mirrors the production cold-load sequence in
    `flask_app/routes/game_routes.py`:

        memory_manager.set_relationship_repo(repo, cash_mode=True, ...)
        memory_manager.opponent_model_manager = OpponentModelManager.from_dict(saved)
    """

    def _saved_opponent_models_payload(self):
        """Minimal saved-OPM dict the cold-load path feeds to from_dict.
        Includes the `__name_to_id__` sidecar so the restored OPM
        carries the registry, matching `game_repo.load_opponent_models`.
        """
        return {
            "Oscar Wilde": {
                "Jeff": {
                    "observer": "Oscar Wilde",
                    "opponent": "Jeff",
                    "observer_id": "oscar_wilde",
                    "opponent_id": "guest_jeff",
                    "tendencies": {},
                    "memorable_hands": [],
                    "narrative_observations": [],
                },
            },
            "Jeff": {
                "Oscar Wilde": {
                    "observer": "Jeff",
                    "opponent": "Oscar Wilde",
                    "observer_id": "guest_jeff",
                    "opponent_id": "oscar_wilde",
                    "tendencies": {},
                    "memorable_hands": [],
                    "narrative_observations": [],
                },
            },
            "__name_to_id__": {
                "Oscar Wilde": "oscar_wilde",
                "Jeff": "guest_jeff",
            },
        }

    def test_set_relationship_repo_after_opm_swap_rewires_dispatch(self, repo):
        """The production cold-load fix: wire `set_relationship_repo`
        AFTER restoring the OPM from DB, so the wiring lands on the
        OPM that record_event will mutate.
        """
        from poker.memory.opponent_model import OpponentModelManager

        mgr = AIMemoryManager(
            game_id="cash-repro",
            db_path=None,
            owner_id="guest_jeff",
        )
        # OPM swap (cold-load step in game_routes.py).
        mgr.opponent_model_manager = OpponentModelManager.from_dict(
            self._saved_opponent_models_payload(),
        )
        # Wire AFTER the swap — this is the production fix order.
        mgr.set_relationship_repo(
            repo,
            cash_mode=True,
            sandbox_id="sb-cash",
        )

        # Remaining cold-load steps: re-init each seat on the
        # restored OPM, then dispatch.
        mgr.initialize_for_player("Oscar Wilde", personality_id="oscar_wilde")
        mgr.initialize_for_player("Jay Gatsby", personality_id="jay_gatsby")
        mgr.initialize_for_player("Cheshire Cat", personality_id="cheshire_cat")
        mgr.initialize_human_observer("Jeff", personality_id="guest_jeff")
        for obs in ("Jeff", "Oscar Wilde", "Jay Gatsby", "Cheshire Cat"):
            for opp in ("Jeff", "Oscar Wilde", "Jay Gatsby", "Cheshire Cat"):
                if obs != opp:
                    mgr.opponent_model_manager.get_model(obs, opp)

        mgr._process_relationship_events(_four_seat_big_pot_hand())

        # Personality-id-keyed rows confirm the detector's `_name_to_id`
        # reference was re-synced to the restored OPM's registry —
        # without that fix the detector would fall back to display
        # names and produce 'Jeff'/'Oscar Wilde' rows instead.
        assert (
            repo.load_raw_relationship_state(
                "oscar_wilde",
                "guest_jeff",
            )
            is not None
        )
        assert (
            repo.load_cash_pair_stats(
                "oscar_wilde",
                "guest_jeff",
                sandbox_id="sb-cash",
            )
            is not None
        )
        # No name-keyed rows — registry resolution worked.
        assert repo.load_raw_relationship_state("Oscar Wilde", "Jeff") is None

    def test_set_relationship_repo_before_opm_swap_is_documented_break(self, repo):
        """Defensive regression: pre-fix sequence (wire then swap)
        silently drops the wiring on the new OPM. This test pins the
        original failure mode so the cold-load fix in game_routes.py
        can't accidentally revert.
        """
        from poker.memory.opponent_model import OpponentModelManager

        mgr = AIMemoryManager(
            game_id="cash-repro",
            db_path=None,
            owner_id="guest_jeff",
        )
        mgr.set_relationship_repo(
            repo,
            cash_mode=True,
            sandbox_id="sb-cash",
        )
        # Buggy order: swap AFTER wiring. The new OPM has no repo.
        mgr.opponent_model_manager = OpponentModelManager.from_dict(
            self._saved_opponent_models_payload(),
        )
        mgr.initialize_for_player("Oscar Wilde", personality_id="oscar_wilde")
        mgr.initialize_human_observer("Jeff", personality_id="guest_jeff")
        mgr.opponent_model_manager.get_model("Jeff", "Oscar Wilde")
        mgr.opponent_model_manager.get_model("Oscar Wilde", "Jeff")

        # Dispatch fails silently — the try/except in
        # _process_relationship_events swallows the RuntimeError.
        # Nothing should crash, but the surfaces stay empty.
        mgr._process_relationship_events(_four_seat_big_pot_hand())
        assert (
            repo.load_raw_relationship_state(
                "oscar_wilde",
                "guest_jeff",
            )
            is None
        )
        assert (
            repo.load_cash_pair_stats(
                "oscar_wilde",
                "guest_jeff",
                sandbox_id="sb-cash",
            )
            is None
        )

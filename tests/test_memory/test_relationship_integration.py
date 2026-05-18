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
            player_name="alice", action="raise", amount=400,
            phase="PRE_FLOP", pot_after=400,
        ),
        RecordedAction(
            player_name="bob", action="call", amount=400,
            phase="PRE_FLOP", pot_after=800,
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
        winners=(WinnerInfo(
            name="alice", amount_won=800, hand_name="Pair", hand_rank=8,
        ),),
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
            game_id="g1", hand_number=1,
            timestamp=datetime(2026, 5, 18, 12, 0),
            players=(
                PlayerHandInfo(name="alice", starting_stack=1000, position="BTN", is_human=False),
                PlayerHandInfo(name="bob", starting_stack=1000, position="BB", is_human=False),
            ),
            hole_cards={"alice": ["Ah", "Ks"], "bob": ["7h", "2d"]},
            community_cards=("2c", "7d", "9s", "Th", "Jc"),
            actions=(
                RecordedAction(player_name="alice", action="raise", amount=25, phase="PRE_FLOP", pot_after=25),
                RecordedAction(player_name="bob", action="call", amount=25, phase="PRE_FLOP", pot_after=50),
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
        mgr.set_relationship_repo(repo, cash_mode=True)

        mgr._process_relationship_events(_big_heads_up_hand())

        alice_stats = repo.load_cash_pair_stats("alice", "bob")
        bob_stats = repo.load_cash_pair_stats("bob", "alice")
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


class TestDedupAtIntegration:
    def test_replaying_same_hand_doesnt_double_apply(self, repo):
        mgr = AIMemoryManager(game_id="g1", db_path=None)
        mgr.initialize_for_player("alice")
        mgr.initialize_for_player("bob")
        mgr.set_relationship_repo(repo, cash_mode=True)

        hand = _big_heads_up_hand()
        mgr._process_relationship_events(hand)
        # Snapshot after first pass.
        first_pnl = repo.load_cash_pair_stats("alice", "bob").cumulative_pnl
        first_heat = repo.load_raw_relationship_state("alice", "bob").heat

        # Replay the same hand.
        mgr._process_relationship_events(hand)

        second_pnl = repo.load_cash_pair_stats("alice", "bob").cumulative_pnl
        second_heat = repo.load_raw_relationship_state("alice", "bob").heat
        assert second_pnl == first_pnl
        assert second_heat == first_heat


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

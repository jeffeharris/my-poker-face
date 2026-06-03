"""Tests for the relationship-prompt formatter.

`build_relationship_context` is a pure read-side formatter that turns
the durable relationship layer into a label-driven prompt block. These
tests target the bucketing rules + memorable-hand surfacing + the
documented no-op cases.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

from poker.memory.opponent_model import OpponentModelManager, RelationshipState
from poker.memory.relationship_events import RelationshipEvent
from poker.memory.relationship_prompt import (
    _classify,
    build_relationship_context,
)
from poker.repositories.relationship_repository import RelationshipRepository
from poker.repositories.schema_manager import SchemaManager

NOW = datetime(2026, 5, 20, 12, 0)


class _FixedNow(datetime):
    """`datetime` subclass whose `utcnow()` is pinned to NOW.

    The controller helper (`_append_relationship_context_if_enabled`) calls
    `build_relationship_context` WITHOUT an explicit `now`, so it projects heat
    to the real `datetime.utcnow()`. Tests that go through the helper monkeypatch
    the relationship_prompt module's `datetime` with this so the seeded heat
    (pinned to NOW) doesn't decay below threshold as wall-clock time advances —
    otherwise the test rots once "now" drifts past NOW. Subclassing keeps every
    other datetime behavior intact; only the clock source is fixed.
    """

    @classmethod
    def utcnow(cls):
        return NOW


@pytest.fixture
def repo(tmp_path):
    path = str(tmp_path / "rel.db")
    SchemaManager(path).ensure_schema()
    r = RelationshipRepository(path)
    yield r
    r.close()


@pytest.fixture
def manager(repo):
    mgr = OpponentModelManager(relationship_repo=repo)
    mgr.register_player_id("alice", "alice_pid")
    mgr.register_player_id("bob", "bob_pid")
    mgr.register_player_id("carol", "carol_pid")
    return mgr


def _seed_state(repo, observer_id, opponent_id, *, heat=0.0, respect=0.5, likability=0.5, at=NOW):
    """Seed a relationship row with the given axes pinned to `at` (default NOW).

    Tests that read heat back through a path projecting to a fixed `now=NOW`
    leave `at` at the default (zero decay). A test that reads through a path
    using the REAL wall clock (e.g. the controller helper, which calls
    `datetime.utcnow()` internally and can't take an injected `now`) must seed
    with `at=datetime.utcnow()` instead — otherwise heat decays from the fixed
    NOW to the real now and the test rots as wall-clock time drifts."""
    state = RelationshipState(
        heat=heat,
        respect=respect,
        likability=likability,
        last_seen=at,
        last_decay_tick=at,
    )
    repo.save_relationship_state(observer_id, opponent_id, state)


class TestClassifyBuckets:
    def test_high_heat_is_rival(self):
        s = RelationshipState(heat=0.6, respect=0.5, likability=0.5)
        assert _classify(s) == "rival"

    def test_high_respect_and_likability_is_friendly(self):
        s = RelationshipState(heat=0.1, respect=0.8, likability=0.8)
        assert _classify(s) == "friendly"

    def test_high_only_one_of_respect_likability_skips(self):
        s = RelationshipState(heat=0.0, respect=0.9, likability=0.5)
        assert _classify(s) is None
        s = RelationshipState(heat=0.0, respect=0.5, likability=0.9)
        assert _classify(s) is None

    def test_neutral_skips(self):
        s = RelationshipState(heat=0.0, respect=0.5, likability=0.5)
        assert _classify(s) is None

    def test_rival_takes_precedence_over_friendly(self):
        # Edge case: heated AND high respect+likability. Heat wins
        # because the emotional foreground is the rivalry.
        s = RelationshipState(heat=0.7, respect=0.8, likability=0.8)
        assert _classify(s) == "rival"


class TestBuildRelationshipContext:
    def test_returns_empty_when_no_repo(self):
        mgr_no_repo = OpponentModelManager()  # no relationship_repo
        result = build_relationship_context(
            observer_name="alice",
            opponents=["bob"],
            opponent_model_manager=mgr_no_repo,
            now=NOW,
        )
        assert result == ""

    def test_returns_empty_when_all_opponents_neutral(self, manager, repo):
        _seed_state(repo, "alice_pid", "bob_pid", heat=0.1, respect=0.5, likability=0.5)
        result = build_relationship_context(
            observer_name="alice",
            opponents=["bob"],
            opponent_model_manager=manager,
            now=NOW,
        )
        assert result == ""

    def test_rival_line_present(self, manager, repo):
        _seed_state(repo, "alice_pid", "bob_pid", heat=0.65)
        result = build_relationship_context(
            observer_name="alice",
            opponents=["bob"],
            opponent_model_manager=manager,
            now=NOW,
        )
        assert "RECENT HISTORY" in result
        assert "bob: rival" in result

    def test_friendly_line_present(self, manager, repo):
        _seed_state(repo, "alice_pid", "bob_pid", heat=0.0, respect=0.8, likability=0.8)
        result = build_relationship_context(
            observer_name="alice",
            opponents=["bob"],
            opponent_model_manager=manager,
            now=NOW,
        )
        assert "bob: friendly" in result

    def test_neutral_opponent_omitted_when_others_qualify(self, manager, repo):
        # bob is a rival, carol is neutral. Block should mention bob
        # but not carol.
        _seed_state(repo, "alice_pid", "bob_pid", heat=0.65)
        _seed_state(repo, "alice_pid", "carol_pid", heat=0.1)
        result = build_relationship_context(
            observer_name="alice",
            opponents=["bob", "carol"],
            opponent_model_manager=manager,
            now=NOW,
        )
        assert "bob: rival" in result
        assert "carol" not in result

    def test_memorable_hands_surfaced_under_label(self, manager, repo):
        # Seed rival state + attach two memorable hands to the in-memory
        # opponent model. Both narratives should appear under bob's line.
        _seed_state(repo, "alice_pid", "bob_pid", heat=0.65)
        model = manager.get_model("alice", "bob")
        model.add_memorable_hand(
            hand_id=42,
            event=RelationshipEvent.BAD_BEAT,
            impact_score=0.9,
            narrative="bob bad-beat alice on hand 42",
            hand_summary="QQ vs KQ rivered",
        )
        model.add_memorable_hand(
            hand_id=47,
            event=RelationshipEvent.HERO_CALL,
            impact_score=0.7,
            narrative="alice hero-called bob's river bluff on hand 47",
            hand_summary="AK vs Q-high",
        )

        result = build_relationship_context(
            observer_name="alice",
            opponents=["bob"],
            opponent_model_manager=manager,
            now=NOW,
        )
        assert "bad-beat alice on hand 42" in result
        assert "hero-called bob's river bluff" in result

    def test_memorable_hands_truncated_to_max(self, manager, repo):
        _seed_state(repo, "alice_pid", "bob_pid", heat=0.65)
        model = manager.get_model("alice", "bob")
        # Add three hands at distinct timestamps; only the 2 most-recent
        # should land in the output.
        for i, (offset_hours, label) in enumerate(
            [
                (3, "oldest"),
                (2, "middle"),
                (1, "newest"),
            ]
        ):
            model.add_memorable_hand(
                hand_id=i,
                event=RelationshipEvent.BIG_LOSS,
                impact_score=0.8,
                narrative=f"{label} narrative",
                hand_summary="",
            )
            # Reach back into the dataclass to pin timestamps for the
            # ordering assertion — newest = 1 hour ago, oldest = 3.
            model.memorable_hands[-1].timestamp = NOW - timedelta(hours=offset_hours)

        result = build_relationship_context(
            observer_name="alice",
            opponents=["bob"],
            opponent_model_manager=manager,
            now=NOW,
            max_memorable_per_opponent=2,
        )
        assert "newest narrative" in result
        assert "middle narrative" in result
        assert "oldest narrative" not in result

    def test_self_addressed_opponent_skipped(self, manager, repo):
        _seed_state(repo, "alice_pid", "alice_pid", heat=0.65)
        result = build_relationship_context(
            observer_name="alice",
            opponents=["alice"],
            opponent_model_manager=manager,
            now=NOW,
        )
        assert result == ""


class TestPromptInjection:
    """Confirm the controller helper threads the block through when
    the flag is on and skips it when off. Smoke-tests the seam, not
    the full prompt assembly.
    """

    def test_helper_returns_prompt_unchanged_when_flag_off(self, manager):
        from types import SimpleNamespace

        # Synthesize the minimum surface AIPlayerController._append…
        # touches. We don't construct the full controller because
        # that pulls in the whole game stack.
        from poker.controllers import AIPlayerController
        from poker.prompt_config import PromptConfig

        prompt_config = PromptConfig(relationship_context=False)
        fake_self = SimpleNamespace(
            prompt_config=prompt_config,
            opponent_model_manager=manager,
            player_name="alice",
        )
        # Bind the unbound method onto the fake to test the helper in
        # isolation — same shape the real controller calls it with.
        helper = AIPlayerController._append_relationship_context_if_enabled
        fake_player = SimpleNamespace(name="alice", is_folded=False)
        fake_state = SimpleNamespace(
            players=[fake_player, SimpleNamespace(name="bob", is_folded=False)],
        )
        result = helper(fake_self, "ORIGINAL", fake_state, fake_player)
        assert result == "ORIGINAL"

    def test_helper_appends_block_when_flag_on(self, manager, repo, monkeypatch):
        _seed_state(repo, "alice_pid", "bob_pid", heat=0.65)
        from types import SimpleNamespace

        import poker.memory.relationship_prompt as rp
        from poker.controllers import AIPlayerController
        from poker.prompt_config import PromptConfig

        # The helper projects heat to its own utcnow() (no explicit `now`); pin it
        # to NOW so the seeded heat doesn't decay below the rival threshold as
        # real wall-clock time advances past NOW.
        monkeypatch.setattr(rp, "datetime", _FixedNow)

        prompt_config = PromptConfig(relationship_context=True)
        fake_self = SimpleNamespace(
            prompt_config=prompt_config,
            opponent_model_manager=manager,
            player_name="alice",
        )
        helper = AIPlayerController._append_relationship_context_if_enabled
        fake_player = SimpleNamespace(name="alice", is_folded=False)
        fake_state = SimpleNamespace(
            players=[
                fake_player,
                SimpleNamespace(name="bob", is_folded=False),
            ],
        )
        result = helper(fake_self, "ORIGINAL", fake_state, fake_player)
        assert "ORIGINAL" in result
        assert "bob: rival" in result

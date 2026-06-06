"""Tests for OpponentModelManager.record_event — the single mutation
entry point for relationship-axis state.

Covers:
  - Bilateral updates (both pair rows write in a single call)
  - Project-first-then-apply ordering (stale heat decays before shift)
  - Axis clamping to [0, 1]
  - last_seen + last_decay_tick anchor to `now`
  - context_multiplier scales shifts
  - UNKNOWN event is a documented no-op
  - Missing repo raises a clear error
  - impact_score >= threshold attaches MemorableHand on the actor side
  - impact_score below threshold does NOT attach MemorableHand
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

from poker.memory.opponent_model import (
    HEAT_DECAY_HALF_LIFE_DAYS,
    HEAT_DECAY_PLATEAU_DAYS,
    MEMORABLE_HAND_THRESHOLD,
    REGARD_NEUTRAL,
    OpponentModelManager,
    RelationshipState,
)
from poker.memory.relationship_events import (
    ACTOR_AXIS_SHIFTS,
    MIRROR_AXIS_SHIFTS,
    RelationshipEvent,
)
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


@pytest.fixture
def manager(repo):
    return OpponentModelManager(relationship_repo=repo)


# --- Basic invariants ---


class TestRecordEventInvariants:
    def test_requires_repo_at_construction(self):
        # Manager constructed without a repo can't record events.
        mgr = OpponentModelManager()
        with pytest.raises(RuntimeError, match="relationship_repo"):
            mgr.record_event(
                "alice",
                "bob",
                RelationshipEvent.BIG_LOSS,
                now=datetime(2026, 5, 17),
            )

    def test_unknown_event_is_silent_no_op(self, manager, repo):
        manager.record_event(
            "alice",
            "bob",
            RelationshipEvent.UNKNOWN,
            now=datetime(2026, 5, 17),
        )
        # No row should have been written for either side.
        assert repo.load_raw_relationship_state("alice", "bob") is None
        assert repo.load_raw_relationship_state("bob", "alice") is None


# --- Bilateral updates ---


class TestBilateralUpdate:
    def test_both_pair_rows_written(self, manager, repo):
        now = datetime(2026, 5, 17, 12, 0)
        manager.record_event(
            "alice",
            "bob",
            RelationshipEvent.BLUFFED_OFF,
            now=now,
        )
        alice_view = repo.load_raw_relationship_state("alice", "bob")
        bob_view = repo.load_raw_relationship_state("bob", "alice")
        assert alice_view is not None
        assert bob_view is not None

    def test_actor_side_uses_actor_table(self, manager, repo):
        # BLUFFED_OFF actor entry from the design table.
        now = datetime(2026, 5, 17, 12, 0)
        expected = ACTOR_AXIS_SHIFTS[RelationshipEvent.BLUFFED_OFF]
        manager.record_event(
            "alice",
            "bob",
            RelationshipEvent.BLUFFED_OFF,
            now=now,
        )
        # alice (actor) sees: heat +0.20, respect -0.05, likability -0.02
        # starting from defaults (0.0, REGARD_NEUTRAL, REGARD_NEUTRAL)
        alice = repo.load_raw_relationship_state("alice", "bob")
        assert alice.heat == pytest.approx(0.0 + expected.heat)
        assert alice.respect == pytest.approx(REGARD_NEUTRAL + expected.respect)
        assert alice.likability == pytest.approx(REGARD_NEUTRAL + expected.likability)

    def test_target_side_uses_mirror_table(self, manager, repo):
        # BAD_BEAT mirror is the canonical design example:
        # target sees heat 0, respect +0.05, likability -0.05.
        now = datetime(2026, 5, 17, 12, 0)
        expected = MIRROR_AXIS_SHIFTS[RelationshipEvent.BAD_BEAT]
        manager.record_event(
            "alice",
            "bob",
            RelationshipEvent.BAD_BEAT,
            now=now,
        )
        bob = repo.load_raw_relationship_state("bob", "alice")
        assert bob.heat == pytest.approx(0.0 + expected.heat)
        assert bob.respect == pytest.approx(REGARD_NEUTRAL + expected.respect)
        assert bob.likability == pytest.approx(REGARD_NEUTRAL + expected.likability)


# --- Project-first-then-apply ordering ---


class TestProjectFirstThenApply:
    """A refresh event 30+ days after a peak must not reset stale heat
    back to its day-zero peak. The stored snapshot decays first; then
    the event shift adds on top of the decayed value."""

    def test_stale_heat_decays_before_event_applies(self, manager, repo):
        # Seed alice→bob with a hot rivalry tick from 30 days ago.
        thirty_days_ago = datetime(2026, 4, 17, 12, 0)
        now = datetime(2026, 5, 17, 12, 0)  # 30 days later

        seed = RelationshipState(
            heat=0.8,
            respect=REGARD_NEUTRAL,
            likability=REGARD_NEUTRAL,
            last_seen=thirty_days_ago,
            last_decay_tick=thirty_days_ago,
        )
        repo.save_relationship_state("alice", "bob", seed)

        # New event today.
        manager.record_event(
            "alice",
            "bob",
            RelationshipEvent.BLUFFED_OFF,  # actor heat +0.20
            now=now,
        )

        result = repo.load_raw_relationship_state("alice", "bob")
        # Old heat 0.8 decayed across 30 days (7 plateau + 23 decaying
        # → ~0.8 * 0.5^(23/14) ≈ 0.255), THEN +0.20 shift applied.
        # Bad ordering would be: 0.8 + 0.20 = 1.0 (saturated)
        # Good ordering: ~0.255 + 0.20 ≈ ~0.455
        assert result.heat < 0.6  # decayed + shifted, well under saturation
        assert result.heat > 0.4  # but not at the freshly-decayed baseline either

    def test_last_decay_tick_advances_to_now(self, manager, repo):
        old_tick = datetime(2026, 4, 1, 12, 0)
        now = datetime(2026, 5, 17, 12, 0)
        repo.save_relationship_state(
            "alice",
            "bob",
            RelationshipState(heat=0.3, last_decay_tick=old_tick, last_seen=old_tick),
        )

        manager.record_event(
            "alice",
            "bob",
            RelationshipEvent.BIG_LOSS,
            now=now,
        )

        result = repo.load_raw_relationship_state("alice", "bob")
        assert result.last_decay_tick == now
        assert result.last_seen == now


# --- Clamping ---


class TestClamping:
    def test_heat_clamps_to_one(self, manager, repo):
        now = datetime(2026, 5, 17, 12, 0)
        # Pre-seed with heat near max, then apply a BAD_BEAT (+0.30) —
        # uncapped would be 1.2, clamped should stay at 1.0.
        repo.save_relationship_state(
            "alice",
            "bob",
            RelationshipState(heat=0.9, last_decay_tick=now),
        )
        manager.record_event(
            "alice",
            "bob",
            RelationshipEvent.BAD_BEAT,
            now=now,
        )
        result = repo.load_raw_relationship_state("alice", "bob")
        assert result.heat == 1.0

    def test_respect_clamps_to_zero(self, manager, repo):
        now = datetime(2026, 5, 17, 12, 0)
        # respect starts at REGARD_NEUTRAL (0.35) default. Apply BAD_BEAT
        # (respect -0.15) six times — would go negative, clamp to 0.0.
        for _ in range(6):
            manager.record_event(
                "alice",
                "bob",
                RelationshipEvent.BAD_BEAT,
                now=now,
            )
        result = repo.load_raw_relationship_state("alice", "bob")
        assert result.respect == 0.0

    def test_likability_clamps_to_one(self, manager, repo):
        now = datetime(2026, 5, 17, 12, 0)
        # likability starts at REGARD_NEUTRAL (0.35). COMPLIMENT (+0.05) ×20.
        for _ in range(20):
            manager.record_event(
                "alice",
                "bob",
                RelationshipEvent.COMPLIMENT,
                now=now,
            )
        result = repo.load_raw_relationship_state("alice", "bob")
        assert result.likability == 1.0


# --- Context multiplier ---


class TestContextMultiplier:
    def test_scales_actor_shift(self, manager, repo):
        now = datetime(2026, 5, 17, 12, 0)
        # BIG_LOSS actor shift heat +0.15. With context_multiplier=2.0
        # the applied shift is +0.30.
        manager.record_event(
            "alice",
            "bob",
            RelationshipEvent.BIG_LOSS,
            context_multiplier=2.0,
            now=now,
        )
        result = repo.load_raw_relationship_state("alice", "bob")
        assert result.heat == pytest.approx(0.30)

    def test_scales_mirror_shift(self, manager, repo):
        now = datetime(2026, 5, 17, 12, 0)
        # BIG_LOSS mirror is heat -0.05. With context_multiplier=2.0,
        # bob's heat toward alice goes -0.10. Starts at 0.0, clamps
        # at 0.0 — so we use a state with higher starting heat to
        # see the actual scaled shift survive the clamp.
        repo.save_relationship_state(
            "bob",
            "alice",
            RelationshipState(heat=0.5, last_decay_tick=now),
        )
        manager.record_event(
            "alice",
            "bob",
            RelationshipEvent.BIG_LOSS,
            context_multiplier=2.0,
            now=now,
        )
        result = repo.load_raw_relationship_state("bob", "alice")
        assert result.heat == pytest.approx(0.5 - 0.10)


# --- MemorableHand sidecar ---


class TestMemorableHandSidecar:
    def test_threshold_attaches_memorable_hand(self, manager, repo):
        # Register so we can resolve id→name and find a PlayerModel
        manager.register_player_id("alice", "alice_id")
        manager.register_player_id("bob", "bob_id")
        # Create the in-memory model
        manager.get_model("alice", "bob")

        manager.record_event(
            "alice_id",
            "bob_id",
            RelationshipEvent.BAD_BEAT,
            impact_score=MEMORABLE_HAND_THRESHOLD + 0.1,
            narrative="A bad beat indeed",
            hand_summary="River miracle for Bob",
            hand_id=42,
            now=datetime(2026, 5, 17, 12, 0),
        )

        model = manager.get_model("alice", "bob")
        assert len(model.memorable_hands) == 1
        memorable = model.memorable_hands[0]
        assert memorable.hand_id == 42
        assert memorable.event is RelationshipEvent.BAD_BEAT
        assert memorable.narrative == "A bad beat indeed"

    def test_below_threshold_no_memorable_hand(self, manager, repo):
        manager.register_player_id("alice", "alice_id")
        manager.register_player_id("bob", "bob_id")
        manager.get_model("alice", "bob")

        manager.record_event(
            "alice_id",
            "bob_id",
            RelationshipEvent.BAD_BEAT,
            impact_score=MEMORABLE_HAND_THRESHOLD - 0.1,
            narrative="Minor",
            hand_summary="Nothing major",
            hand_id=42,
            now=datetime(2026, 5, 17, 12, 0),
        )
        model = manager.get_model("alice", "bob")
        assert len(model.memorable_hands) == 0

    def test_missing_hand_id_skips_memorable_hand(self, manager, repo):
        manager.register_player_id("alice", "alice_id")
        manager.register_player_id("bob", "bob_id")
        manager.get_model("alice", "bob")

        manager.record_event(
            "alice_id",
            "bob_id",
            RelationshipEvent.BAD_BEAT,
            impact_score=MEMORABLE_HAND_THRESHOLD + 0.1,
            # no hand_id
            now=datetime(2026, 5, 17, 12, 0),
        )
        model = manager.get_model("alice", "bob")
        assert len(model.memorable_hands) == 0

    def test_no_player_model_skips_silently(self, manager, repo):
        # Don't register names or create a model. The relationship
        # state still persists; memorable hand is skipped.
        manager.record_event(
            "ghost_id",
            "phantom_id",
            RelationshipEvent.BAD_BEAT,
            impact_score=MEMORABLE_HAND_THRESHOLD + 0.1,
            hand_id=99,
            now=datetime(2026, 5, 17, 12, 0),
        )
        # The relationship state row exists
        assert repo.load_raw_relationship_state("ghost_id", "phantom_id") is not None
        # No PlayerModel was created
        assert manager.get_model_if_exists("ghost_id", "phantom_id") is None


# --- Sanity: existing axis state is preserved across record_event ---


class TestStateAccumulation:
    def test_multiple_events_accumulate(self, manager, repo):
        now = datetime(2026, 5, 17, 12, 0)
        # Three BIG_LOSS events from alice (heat +0.15 each, no decay
        # — all at same `now`).
        for _ in range(3):
            manager.record_event(
                "alice",
                "bob",
                RelationshipEvent.BIG_LOSS,
                now=now,
            )
        result = repo.load_raw_relationship_state("alice", "bob")
        # 0 + 3*0.15 = 0.45 (uncapped, under 1.0)
        assert result.heat == pytest.approx(0.45)

    def test_other_axis_untouched_by_zero_shift_events(self, manager, repo):
        # DOMINATED_SHOWDOWN actor shift: heat 0, respect -0.15, lik 0.
        now = datetime(2026, 5, 17, 12, 0)
        manager.record_event(
            "alice",
            "bob",
            RelationshipEvent.DOMINATED_SHOWDOWN,
            now=now,
        )
        result = repo.load_raw_relationship_state("alice", "bob")
        assert result.heat == 0.0  # unchanged
        assert result.respect == pytest.approx(REGARD_NEUTRAL - 0.15)
        assert result.likability == REGARD_NEUTRAL  # unchanged

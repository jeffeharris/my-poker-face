"""Tests for the RelationshipEvent enum and dispatch tables.

Locks in the invariants the relationship layer depends on:
  - Enum covers every event named in the design doc
  - Both dispatch tables key on the same set of events
  - UNKNOWN sentinel has zero impact in both tables (quarantine path)
  - Legacy / unknown strings on load coerce to UNKNOWN without raising
  - The BAD_BEAT mirror values match the design-doc canonical example
"""

from __future__ import annotations

import pytest

from poker.memory.relationship_events import (
    ACTOR_AXIS_SHIFTS,
    MIRROR_AXIS_SHIFTS,
    AxisShift,
    RelationshipEvent,
    actor_shift,
    mirror_shift,
)


class TestEnumCoverage:
    """The enum is the source-of-truth for the event vocabulary. If a
    new event lands in the design doc but not here (or vice versa),
    these tests catch the drift."""

    def test_all_design_doc_events_present(self):
        expected = {
            # Hand-outcome events
            "bluffed_off",
            "hero_call",
            "big_loss",
            "big_win",
            "bad_beat",
            "dominated_showdown",
            "strong_fold_shown",
            "cooler",
            # Chat events
            "chat_trash_talk",
            "chat_compliment",
            "chat_taunt_post_win",
            "chat_friendly_banter",
            "chat_props",
            "chat_flattery_landed",
            "chat_flattery_backfired",
            "chat_commiserate",
            # Cash-mode staking
            "stake_offered",
            "stake_repaid",
            "stake_defaulted",
            "stake_forgiven",
            "stake_forgiveness_refused",
            # Cash-mode table dynamics
            "stack_dominance",
            "knockout",
            "rival",
            "nemesis",
            "regular",
            # Quarantine
            "_unknown",
        }
        actual = {member.value for member in RelationshipEvent}
        assert actual == expected, (
            f"Enum / design-doc drift. "
            f"Missing: {expected - actual}. Extra: {actual - expected}."
        )

    def test_unknown_value_is_underscore_prefixed(self):
        # Sentinel string starts with underscore so it can't collide
        # with any legacy memory_type that was a real event name.
        assert RelationshipEvent.UNKNOWN.value.startswith("_")


class TestFromStringParsing:
    """Load-path parsing: legacy DB rows must coerce safely."""

    def test_known_string_round_trips(self):
        for event in RelationshipEvent:
            assert RelationshipEvent.from_string(event.value) is event

    def test_unknown_string_coerces_to_unknown(self):
        assert RelationshipEvent.from_string("ancient_event_name") is RelationshipEvent.UNKNOWN
        assert RelationshipEvent.from_string("") is RelationshipEvent.UNKNOWN
        assert RelationshipEvent.from_string("PUNCTUATION!?") is RelationshipEvent.UNKNOWN

    def test_strict_constructor_still_raises(self):
        # Callers that need strict parsing bypass from_string.
        with pytest.raises(ValueError):
            RelationshipEvent("not_a_real_event")


class TestDispatchTableCoverage:
    """Both tables must have an entry for every enum member.
    Missing entries would yield silent zero-shifts (via .get default)
    rather than raising, so this test is the safety net."""

    def test_actor_table_covers_every_event(self):
        for event in RelationshipEvent:
            assert event in ACTOR_AXIS_SHIFTS, f"Actor table missing {event.name}"

    def test_mirror_table_covers_every_event(self):
        for event in RelationshipEvent:
            assert event in MIRROR_AXIS_SHIFTS, f"Mirror table missing {event.name}"


class TestUnknownQuarantine:
    """UNKNOWN must have zero impact on both axes in both tables.
    This is the load-path safety net: when an unparseable string
    coerces to UNKNOWN, record_event must produce no axis movement."""

    def test_actor_unknown_is_zero(self):
        shift = ACTOR_AXIS_SHIFTS[RelationshipEvent.UNKNOWN]
        assert shift == AxisShift(0.0, 0.0, 0.0)

    def test_mirror_unknown_is_zero(self):
        shift = MIRROR_AXIS_SHIFTS[RelationshipEvent.UNKNOWN]
        assert shift == AxisShift(0.0, 0.0, 0.0)

    def test_actor_shift_helper_returns_zero_for_unknown(self):
        assert actor_shift(RelationshipEvent.UNKNOWN) == AxisShift()

    def test_mirror_shift_helper_returns_zero_for_unknown(self):
        assert mirror_shift(RelationshipEvent.UNKNOWN) == AxisShift()


class TestActorTableValues:
    """The actor's-POV table is the design-doc calibration. These tests
    catch accidental edits to the starting values; tuning passes
    should update both this table AND these expected values
    intentionally."""

    @pytest.mark.parametrize(
        "event,expected",
        [
            (RelationshipEvent.BLUFFED_OFF, AxisShift(+0.20, -0.05, -0.02)),
            (RelationshipEvent.HERO_CALL, AxisShift(-0.05, -0.10, +0.01)),
            (RelationshipEvent.BIG_LOSS, AxisShift(+0.15, +0.08, -0.05)),
            (RelationshipEvent.BIG_WIN, AxisShift(-0.10, -0.05, +0.02)),
            (RelationshipEvent.BAD_BEAT, AxisShift(+0.30, -0.15, -0.10)),
            (RelationshipEvent.DOMINATED_SHOWDOWN, AxisShift(0.00, -0.15, 0.00)),
            (RelationshipEvent.STRONG_FOLD_SHOWN, AxisShift(0.00, +0.10, 0.00)),
            (RelationshipEvent.COOLER, AxisShift(+0.10, +0.10, -0.05)),
            (RelationshipEvent.TRASH_TALK, AxisShift(+0.10, 0.00, -0.05)),
            (RelationshipEvent.COMPLIMENT, AxisShift(0.00, +0.03, +0.05)),
            (RelationshipEvent.TAUNT_POST_WIN, AxisShift(+0.20, 0.00, -0.10)),
            (RelationshipEvent.FRIENDLY_BANTER, AxisShift(0.00, 0.00, +0.03)),
        ],
    )
    def test_actor_shift_matches_design(self, event, expected):
        assert actor_shift(event) == expected


class TestMirrorTableCanonicalExamples:
    """The design doc specifies the BAD_BEAT mirror explicitly as the
    canonical illustration of mirror semantics. Other mirrors are
    derived from poker semantics and are tunable, but BAD_BEAT must
    match the spec verbatim."""

    def test_bad_beat_mirror_matches_design_doc(self):
        # From CASH_MODE_AND_RELATIONSHIPS.md Part 1 §"Symmetry":
        # "Example: BAD_BEAT against actor → mirror entry for target:
        #  heat 0, respect +0.05 (feared), likability −0.05 (unearned win)."
        assert mirror_shift(RelationshipEvent.BAD_BEAT) == AxisShift(
            heat=0.0, respect=+0.05, likability=-0.05
        )


class TestMirrorTableShape:
    """Sanity checks on mirror values rather than exact match — the
    mirror calibration is tunable and these tests should not lock in
    every number. They lock in the SHAPE of the mapping."""

    def test_chat_events_mirror_target_perspective(self):
        # Trash talk: target hearing trash should get angrier and less
        # fond of the speaker. Direction matters more than magnitude.
        m = mirror_shift(RelationshipEvent.TRASH_TALK)
        assert m.heat > 0
        assert m.likability < 0

        # Compliment: target hearing a compliment should warm up.
        m = mirror_shift(RelationshipEvent.COMPLIMENT)
        assert m.likability > 0

        # Taunt-post-win: target loses + hears taunt. Heat must rise.
        m = mirror_shift(RelationshipEvent.TAUNT_POST_WIN)
        assert m.heat > 0
        assert m.likability < 0

        # Friendly banter: target feels warmer.
        m = mirror_shift(RelationshipEvent.FRIENDLY_BANTER)
        assert m.likability > 0

    def test_hero_call_mirror_target_is_frustrated_but_respects(self):
        # Hero call: actor catches target's bluff. Target's view of
        # actor: more heat (frustrated), respect goes up (sick read).
        m = mirror_shift(RelationshipEvent.HERO_CALL)
        assert m.heat > 0
        assert m.respect > 0

    def test_big_win_and_big_loss_mirror_invert(self):
        # If actor BIG_LOSS-es, target won. Heat against actor cools.
        big_loss_mirror = mirror_shift(RelationshipEvent.BIG_LOSS)
        # If actor BIG_WIN-s, target lost. Heat against actor rises.
        big_win_mirror = mirror_shift(RelationshipEvent.BIG_WIN)

        assert big_loss_mirror.heat < 0
        assert big_win_mirror.heat > 0

    def test_strong_fold_shown_has_no_mirror_impact(self):
        # Target doesn't really know what actor folded unless reveal.
        # Mirror is zero for now; could grow when reveal-on-fold ships.
        assert mirror_shift(RelationshipEvent.STRONG_FOLD_SHOWN) == AxisShift()


class TestStakingShifts:
    """Cash-mode staking events — directional sanity checks.

    Lock in the SHAPE of the deltas (which axes move which direction)
    rather than exact magnitudes — the starting calibration is tunable.
    """

    def test_stake_offered_staker_extends_trust(self):
        # Actor = AI staker. Staker's view of borrower: small respect +
        # likability bump for extending the trust.
        a = actor_shift(RelationshipEvent.STAKE_OFFERED)
        assert a.respect > 0
        assert a.likability > 0
        assert a.heat == 0  # no aggression on offering

    def test_stake_offered_borrower_grateful(self):
        # Mirror = borrower's view of staker. Receiving a stake creates
        # a positive feeling toward the staker.
        m = mirror_shift(RelationshipEvent.STAKE_OFFERED)
        assert m.respect > 0
        assert m.likability > 0

    def test_stake_repaid_strengthens_relationship(self):
        # Borrower returned principal + cut. Both sides move positive.
        a = actor_shift(RelationshipEvent.STAKE_REPAID)
        m = mirror_shift(RelationshipEvent.STAKE_REPAID)
        assert a.respect > 0
        assert a.likability > 0
        assert a.heat <= 0  # any friction cools off
        assert m.respect > 0
        assert m.likability > 0

    def test_stake_defaulted_sharpest_negative_event(self):
        # Staker stiffed → respect drops hard, heat surges. The
        # canonical "worst thing a borrower can do" event.
        a = actor_shift(RelationshipEvent.STAKE_DEFAULTED)
        assert a.respect < 0
        assert a.heat > 0
        assert a.likability < 0
        # Comparison to other negative events: defaulting hits respect
        # harder than even BAD_BEAT (which is the harshest hand-outcome).
        bad_beat = actor_shift(RelationshipEvent.BAD_BEAT)
        assert a.respect < bad_beat.respect

    def test_stake_defaulted_mirror_mutual_animosity(self):
        # Borrower feels watched/judged by the staker → heat rises
        # in both directions; likability drops both ways.
        m = mirror_shift(RelationshipEvent.STAKE_DEFAULTED)
        assert m.heat > 0
        assert m.likability < 0

    def test_stack_dominance_observer_loses_respect_and_likability(self):
        # Actor = observer. The deep stack costs them respect and
        # likability but never heat (envy isn't hostility). The
        # mirror is zero — the deep stack doesn't notice.
        a = actor_shift(RelationshipEvent.STACK_DOMINANCE)
        m = mirror_shift(RelationshipEvent.STACK_DOMINANCE)
        assert a.heat == 0
        assert a.respect < 0
        assert a.likability < 0
        assert m == AxisShift()

    def test_stake_forgiven_both_sides_positive(self):
        # Staker wrote off a carry → small heat drop + likability bump
        # on their side; borrower feels strong gratitude in mirror.
        a = actor_shift(RelationshipEvent.STAKE_FORGIVEN)
        m = mirror_shift(RelationshipEvent.STAKE_FORGIVEN)
        assert a.heat < 0
        assert a.likability > 0
        assert m.heat < 0
        assert m.likability > 0
        assert m.respect > 0


class TestAxisShiftDataclass:
    def test_defaults_are_zero(self):
        s = AxisShift()
        assert s.heat == 0.0
        assert s.respect == 0.0
        assert s.likability == 0.0

    def test_equality_by_value(self):
        assert AxisShift(0.1, 0.2, 0.3) == AxisShift(0.1, 0.2, 0.3)
        assert AxisShift(0.1, 0.2, 0.3) != AxisShift(0.1, 0.2, 0.4)

    def test_is_frozen(self):
        s = AxisShift()
        with pytest.raises((AttributeError, Exception)):  # FrozenInstanceError
            s.heat = 0.5  # type: ignore[misc]

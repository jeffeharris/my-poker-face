"""Unit tests for temperament-adjusted mirror shifts.

`temperament_adjusted_mirror_shift(event, disposition)` reshapes how a
needle LANDS on its recipient, by the recipient's social disposition
('energized' / 'stung' / 'stoic'). These are pure-function tests over the
dispatch tables — no DB, no controllers. The wiring into the bilateral
relationship update is covered in test_chat_relationship_dispatch.py.
"""

from __future__ import annotations

import pytest

from poker.memory.relationship_events import (
    RelationshipEvent,
    mirror_shift,
    temperament_adjusted_mirror_shift,
)

NEEDLING = [RelationshipEvent.TRASH_TALK, RelationshipEvent.TAUNT_POST_WIN]


class TestStoicIsNeutral:
    """'stoic' (and any unrecognized disposition) returns the global mirror."""

    @pytest.mark.parametrize("event", NEEDLING)
    def test_stoic_matches_global_mirror(self, event):
        assert temperament_adjusted_mirror_shift(event, 'stoic') == mirror_shift(event)

    @pytest.mark.parametrize("event", NEEDLING)
    def test_unknown_disposition_falls_through(self, event):
        # Defensive: a disposition string the table doesn't know about
        # must not crash and must yield the neutral default.
        assert temperament_adjusted_mirror_shift(event, 'nonsense') == mirror_shift(event)


class TestEnergizedBondsOverNeedling:
    """A banter-lover takes a needle as rivalry-as-bonding: heat suppressed,
    likability gained (inverting the neutral penalty)."""

    @pytest.mark.parametrize("event", NEEDLING)
    def test_heat_suppressed_to_zero(self, event):
        assert temperament_adjusted_mirror_shift(event, 'energized').heat == 0.0

    @pytest.mark.parametrize("event", NEEDLING)
    def test_likability_inverts_to_positive(self, event):
        neutral = mirror_shift(event)
        energized = temperament_adjusted_mirror_shift(event, 'energized')
        assert neutral.likability < 0  # the neutral default penalizes
        assert energized.likability > 0  # temperament flips it

    @pytest.mark.parametrize("event", NEEDLING)
    def test_respect_is_earned(self, event):
        assert temperament_adjusted_mirror_shift(event, 'energized').respect > 0


class TestStungTakesItHarder:
    """A proud/thin-skinned recipient: heat and the likability hit amplified
    over the neutral default."""

    @pytest.mark.parametrize("event", NEEDLING)
    def test_heat_amplified(self, event):
        neutral = mirror_shift(event)
        stung = temperament_adjusted_mirror_shift(event, 'stung')
        assert stung.heat > neutral.heat

    @pytest.mark.parametrize("event", NEEDLING)
    def test_likability_hit_amplified(self, event):
        neutral = mirror_shift(event)
        stung = temperament_adjusted_mirror_shift(event, 'stung')
        assert stung.likability < neutral.likability


class TestNonNeedlingEventsUnaffected:
    """Temperament only reshapes TRASH_TALK / TAUNT_POST_WIN. Every other
    event returns the neutral mirror regardless of disposition — the seam
    must never touch hand-outcome or staking flows."""

    @pytest.mark.parametrize(
        "event",
        [
            RelationshipEvent.COMPLIMENT,
            RelationshipEvent.FRIENDLY_BANTER,
            RelationshipEvent.PROPS,
            RelationshipEvent.BAD_BEAT,
            RelationshipEvent.STAKE_DEFAULTED,
            RelationshipEvent.STACK_DOMINANCE,
        ],
    )
    @pytest.mark.parametrize("disposition", ['energized', 'stung', 'stoic'])
    def test_event_is_neutral_for_all_dispositions(self, event, disposition):
        assert temperament_adjusted_mirror_shift(event, disposition) == mirror_shift(event)

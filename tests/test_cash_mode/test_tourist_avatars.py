"""Tests for `cash_mode.tourist_avatars` — the registry that maps
tourist (template, name) pairs to existing personality avatars without
triggering zombie creation in `personality_generator.get_personality`.
"""

from __future__ import annotations

import pytest

from cash_mode.tourist_avatars import (
    NAME_LEVEL,
    TEMPLATE_FALLBACK,
    register_tourist_avatar,
    resolve_tourist_avatar_personality_id,
)


class TestDirectNameMatch:
    """Tourists whose first name matches an existing JSON personality
    should resolve to that personality's avatar verbatim."""

    @pytest.mark.parametrize("template_key,first_name,expected_pid", [
        ("vacation_dad", "Greg", "vacation_greg"),
        ("bachelorette", "Brenda", "bachelorette_brenda"),
        ("retired_know_it_all", "Carl", "cruise_carl"),
        ("birthday_kid", "Bobby", "birthday_bobby"),
    ])
    def test_anchor_match(self, template_key, first_name, expected_pid):
        assert resolve_tourist_avatar_personality_id(
            template_key, first_name,
        ) == expected_pid


class TestTemplateFallback:
    """No NAME_LEVEL entry → fall back to template's anchor avatar."""

    def test_unknown_vacation_dad_name_uses_anchor(self):
        # Dave isn't in NAME_LEVEL but vacation_dad has an anchor
        assert resolve_tourist_avatar_personality_id(
            "vacation_dad", "Dave",
        ) == "vacation_greg"

    def test_unknown_bachelorette_name_uses_anchor(self):
        assert resolve_tourist_avatar_personality_id(
            "bachelorette", "Tiffany",
        ) == "bachelorette_brenda"

    def test_template_without_anchor_returns_none(self):
        # finance_bro has no JSON counterpart — every tourist falls
        # through to letter fallback
        assert resolve_tourist_avatar_personality_id(
            "finance_bro", "Trent",
        ) is None
        assert resolve_tourist_avatar_personality_id(
            "superstitious_grandma", "Mona",
        ) is None
        assert resolve_tourist_avatar_personality_id(
            "slot_refugee", "Linda",
        ) is None
        assert resolve_tourist_avatar_personality_id(
            "golf_trip_dude", "Brad",
        ) is None

    def test_unknown_template_returns_none(self):
        assert resolve_tourist_avatar_personality_id(
            "made_up_template", "Greg",
        ) is None


class TestRegistryExtension:
    """The batch-generation hook should be able to add new mappings."""

    def test_register_adds_to_NAME_LEVEL(self):
        try:
            register_tourist_avatar(
                "finance_bro", "Trent", "_tourist_finance_bro_trent",
            )
            assert resolve_tourist_avatar_personality_id(
                "finance_bro", "Trent",
            ) == "_tourist_finance_bro_trent"
        finally:
            # Don't leak state into other tests
            NAME_LEVEL.pop(("finance_bro", "Trent"), None)

    def test_direct_match_wins_over_template_fallback(self):
        try:
            register_tourist_avatar(
                "vacation_dad", "Dave", "_tourist_vacation_dad_dave",
            )
            # Dave should now resolve to the new mapping, not the anchor
            assert resolve_tourist_avatar_personality_id(
                "vacation_dad", "Dave",
            ) == "_tourist_vacation_dad_dave"
            # Greg unaffected (still resolves via NAME_LEVEL anchor)
            assert resolve_tourist_avatar_personality_id(
                "vacation_dad", "Greg",
            ) == "vacation_greg"
        finally:
            NAME_LEVEL.pop(("vacation_dad", "Dave"), None)


class TestRegistryShapeInvariants:
    """Catch regressions in the hardcoded registry contents."""

    def test_all_template_fallbacks_are_string_or_none(self):
        for k, v in TEMPLATE_FALLBACK.items():
            assert isinstance(k, str)
            assert v is None or isinstance(v, str)

    def test_all_name_level_keys_have_template_in_fallback(self):
        """Sanity: every (template, name) → pid mapping references a
        template that exists in TEMPLATE_FALLBACK (catches typos)."""
        for (template, _name), _pid in NAME_LEVEL.items():
            assert template in TEMPLATE_FALLBACK, (
                f"NAME_LEVEL references unknown template {template!r}"
            )

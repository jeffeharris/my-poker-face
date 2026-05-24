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
        ("vacation_dad", "Greg", "_tourist_vacation_dad_greg"),
        ("bachelorette", "Brenda", "_tourist_bachelorette_brenda"),
        ("retired_know_it_all", "Carl", "_tourist_retired_know_it_all_carl"),
        ("birthday_kid", "Bobby", "_tourist_birthday_kid_bobby"),
        ("finance_bro", "Trent", "_tourist_finance_bro_trent"),
        ("superstitious_grandma", "Mona", "_tourist_superstitious_grandma_mona"),
        ("slot_refugee", "Linda", "_tourist_slot_refugee_linda"),
        ("golf_trip_dude", "Brad", "_tourist_golf_trip_dude_brad"),
    ])
    def test_anchor_match(self, template_key, first_name, expected_pid):
        assert resolve_tourist_avatar_personality_id(
            template_key, first_name,
        ) == expected_pid


class TestTemplateFallback:
    """No NAME_LEVEL entry → fall back to template's anchor avatar.

    Post batch-gen, every template has at least one portrait, so an
    unknown name (e.g., a future addition to a template's name_pool
    before re-running batch-gen) still resolves to SOMETHING."""

    def test_unknown_vacation_dad_name_uses_anchor(self):
        # "Notarealname" isn't in any pool — must fall back
        assert resolve_tourist_avatar_personality_id(
            "vacation_dad", "Notarealname",
        ) == "_tourist_vacation_dad_greg"

    def test_unknown_bachelorette_name_uses_anchor(self):
        assert resolve_tourist_avatar_personality_id(
            "bachelorette", "Notarealname",
        ) == "_tourist_bachelorette_brenda"

    def test_every_template_has_a_fallback(self):
        """After batch-gen, no template returns None for the fallback."""
        for template_key in [
            "vacation_dad", "bachelorette", "retired_know_it_all",
            "birthday_kid", "finance_bro", "superstitious_grandma",
            "slot_refugee", "golf_trip_dude",
        ]:
            result = resolve_tourist_avatar_personality_id(
                template_key, "Notarealname",
            )
            assert result is not None, f"{template_key} has no fallback"

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
        """Override an existing NAME_LEVEL entry — direct match always
        wins. Restore the original mapping in finally so other tests
        keep their expected state."""
        original_dave = NAME_LEVEL[("vacation_dad", "Dave")]
        try:
            register_tourist_avatar(
                "vacation_dad", "Dave", "_test_override_dave",
            )
            assert resolve_tourist_avatar_personality_id(
                "vacation_dad", "Dave",
            ) == "_test_override_dave"
            # Other entries unaffected
            assert resolve_tourist_avatar_personality_id(
                "vacation_dad", "Greg",
            ) == "_tourist_vacation_dad_greg"
        finally:
            NAME_LEVEL[("vacation_dad", "Dave")] = original_dave


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

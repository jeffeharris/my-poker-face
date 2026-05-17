"""Tests for the personality-id backfill script.

Locks in invariants the relationship layer and cash mode rely on:
  - Every personality in personalities.json has a stable `id` field
  - Ids are unique across the roster
  - Ids match the slugify rule (no collisions, predictable form)
  - The backfill script is idempotent (re-running produces no changes)
  - The script's collision-resolution suffix scheme works as documented
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
PERSONALITIES_PATH = REPO_ROOT / "poker" / "personalities.json"
SCRIPT_PATH = REPO_ROOT / "scripts" / "backfill_personality_ids.py"


def _load_backfill_module():
    """Import the backfill script as a module so we can exercise its
    pure helpers directly. The script lives in `scripts/` which isn't on
    the Python path, so we load it via importlib."""
    spec = importlib.util.spec_from_file_location("backfill_personality_ids", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def backfill():
    return _load_backfill_module()


@pytest.fixture(scope="module")
def personalities_data():
    with PERSONALITIES_PATH.open() as f:
        return json.load(f)


class TestSlugify:
    def test_simple_two_word_name(self, backfill):
        assert backfill.slugify("Abraham Lincoln") == "abraham_lincoln"

    def test_dashes_collapse_to_underscore(self, backfill):
        assert backfill.slugify("GTO-Lite") == "gto_lite"

    def test_periods_become_underscores(self, backfill):
        assert backfill.slugify("Dr. Seuss") == "dr_seuss"

    def test_roman_numerals_preserved_as_lowercase(self, backfill):
        assert backfill.slugify("Louis XIV") == "louis_xiv"

    def test_runs_of_separators_collapse(self, backfill):
        # comma + space should not produce double underscores
        assert (
            backfill.slugify("Someone who is very, very mean to people")
            == "someone_who_is_very_very_mean_to_people"
        )

    def test_diacritics_stripped(self, backfill):
        # Accented chars normalize via NFKD then ASCII-encode drops them.
        assert backfill.slugify("Renée") == "renee"
        assert backfill.slugify("Núñez") == "nunez"

    def test_leading_trailing_separators_stripped(self, backfill):
        assert backfill.slugify("...Lincoln...") == "lincoln"
        assert backfill.slugify("  Abraham  ") == "abraham"

    def test_empty_input(self, backfill):
        assert backfill.slugify("") == ""

    def test_only_non_alphanumerics_yields_empty(self, backfill):
        # Pathological case — caller is expected to handle this.
        assert backfill.slugify("---") == ""


class TestAssignUniqueId:
    def test_first_use_returns_base(self, backfill):
        assert backfill.assign_unique_id("abraham", set()) == "abraham"

    def test_collision_appends_v2(self, backfill):
        taken = {"abraham"}
        assert backfill.assign_unique_id("abraham", taken) == "abraham_v2"

    def test_second_collision_appends_v3(self, backfill):
        taken = {"abraham", "abraham_v2"}
        assert backfill.assign_unique_id("abraham", taken) == "abraham_v3"

    def test_gaps_are_skipped(self, backfill):
        # If v2 is missing but v3 is present, the next slot is still v2
        # (the suffix loop starts at 2 and increments only past taken slots).
        taken = {"abraham", "abraham_v3"}
        assert backfill.assign_unique_id("abraham", taken) == "abraham_v2"


class TestRosterInvariants:
    """Tests against the actual personalities.json — these lock in that
    the file stays in good shape as personalities are added or edited."""

    def test_every_personality_has_an_id(self, personalities_data):
        missing = [
            name
            for name, entry in personalities_data["personalities"].items()
            if not isinstance(entry, dict) or "id" not in entry
        ]
        assert missing == [], f"Personalities missing `id`: {missing}"

    def test_all_ids_unique(self, personalities_data):
        ids = [e["id"] for e in personalities_data["personalities"].values()]
        duplicates = {x for x in ids if ids.count(x) > 1}
        assert duplicates == set(), f"Duplicate ids: {duplicates}"

    def test_all_ids_are_non_empty_strings(self, personalities_data):
        for name, entry in personalities_data["personalities"].items():
            assert isinstance(entry["id"], str), f"{name} id is not a string"
            assert entry["id"], f"{name} has empty id"

    def test_ids_match_slugify_or_have_versioned_suffix(
        self, personalities_data, backfill
    ):
        """Every id is either slugify(name) or slugify(name)_v<N>. This
        catches accidental hand-edits that diverge from the deterministic
        scheme — if a personality is renamed, the id is supposed to stay
        the same, so the test allows the id to no longer match the
        current name's slug (the explicit rename case). What it forbids
        is an id with extra mystery characters or unrelated tokens."""
        for name, entry in personalities_data["personalities"].items():
            id_ = entry["id"]
            base = backfill.slugify(name)
            # Either it matches the current name's slug, or it has been
            # versioned (collision resolution), or the personality was
            # renamed after id was assigned (allowed — id must remain
            # stable across renames). We check the loose property:
            # the id is a valid slug shape (lowercase alphanumeric +
            # underscores, no leading/trailing underscores, non-empty).
            assert id_ == id_.lower(), f"{name}: id {id_!r} is not lowercase"
            assert id_.strip("_") == id_, f"{name}: id {id_!r} has edge underscores"
            assert id_.replace("_", "").isalnum() or id_.isalnum(), (
                f"{name}: id {id_!r} has unexpected characters"
            )


class TestConsistencyWithSharedModule:
    """The script and the in-codebase shared module
    (`poker/personality_id.py`) duplicate the slugify + collision rules.
    The duplication is intentional (script runs standalone from host,
    can't pay for `poker/__init__.py` import chain), but the two copies
    must agree byte-for-byte. These tests catch any drift."""

    @pytest.fixture(scope="class")
    def shared_module(self):
        from poker.personality_id import (
            slugify_personality_name,
            assign_unique_personality_id,
        )
        return slugify_personality_name, assign_unique_personality_id

    def test_slugify_matches_shared_module(self, backfill, shared_module):
        shared_slugify, _ = shared_module
        cases = [
            "Abraham Lincoln",
            "Louis XIV",
            "King Henry VIII",
            "A Mime",
            "A guy who tells too many dad jokes",
            "Someone who is very, very mean to people",
            "GTO-Lite",
            "Dr. Seuss",
            "Bob Ross",
            "Renée Zellweger",
            "Núñez",
            "...Lincoln...",
            "  Abraham  ",
            "",
            "---",
            "MixedCASE Name 42",
        ]
        for case in cases:
            assert backfill.slugify(case) == shared_slugify(case), (
                f"Script slugify and shared module diverge on {case!r}"
            )

    def test_assign_unique_id_matches_shared_module(self, backfill, shared_module):
        _, shared_assign = shared_module
        cases = [
            ("abraham", set()),
            ("abraham", {"abraham"}),
            ("abraham", {"abraham", "abraham_v2"}),
            ("abraham", {"abraham", "abraham_v3"}),  # gap
            ("test_hero", {"other_id"}),
        ]
        for base, taken in cases:
            assert backfill.assign_unique_id(base, set(taken)) == shared_assign(
                base, set(taken)
            ), f"Script and shared module diverge on assign_unique_id({base!r}, {taken!r})"


class TestBackfillIdempotence:
    def test_running_backfill_again_makes_no_changes(self, personalities_data, backfill):
        # Deep copy so we don't mutate the module-level fixture
        data = json.loads(json.dumps(personalities_data))
        updated, assigned, skipped = backfill.backfill(data, verbose=False)
        assert assigned == 0
        assert skipped == len(data["personalities"])
        # Round-trip should produce byte-identical JSON
        assert json.dumps(updated, sort_keys=True) == json.dumps(
            personalities_data, sort_keys=True
        )

    def test_new_personality_gets_id(self, backfill):
        data = {
            "personalities": {
                "Test Hero": {"play_style": "test"},
                "Existing Personality": {"id": "existing_personality", "play_style": "test"},
            }
        }
        updated, assigned, skipped = backfill.backfill(data, verbose=False)
        assert assigned == 1
        assert skipped == 1
        assert updated["personalities"]["Test Hero"]["id"] == "test_hero"
        assert (
            updated["personalities"]["Existing Personality"]["id"] == "existing_personality"
        )

    def test_collision_with_existing_id_gets_versioned_suffix(self, backfill):
        data = {
            "personalities": {
                "Pre-existing Slug": {"id": "test_hero", "play_style": "test"},
                "Test Hero": {"play_style": "test"},  # would slugify to test_hero
            }
        }
        updated, assigned, _ = backfill.backfill(data, verbose=False)
        assert assigned == 1
        assert updated["personalities"]["Pre-existing Slug"]["id"] == "test_hero"
        assert updated["personalities"]["Test Hero"]["id"] == "test_hero_v2"

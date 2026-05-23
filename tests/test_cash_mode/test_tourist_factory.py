"""Tests for cash_mode.tourist_factory.

Covers the fish-fingerprint invariant (every template must be a real
chip-leaker, not a sneakily competent player), structural shape of
generated personality dicts, deterministic seeding, and batch uniqueness.

Spec: docs/plans/CASH_MODE_EPHEMERAL_TOURISTS.md
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from cash_mode.tourist_factory import (
    TOURIST_TEMPLATES,
    TouristProfile,
    TouristTemplate,
    generate_tourist,
    generate_tourist_batch,
)
from poker.rule_strategies import FishLeak


class TestFishFingerprintInvariant:
    """Every template must satisfy the fish play profile. Without this
    invariant, someone could accidentally add a 'fish' template that
    actually plays competently — defeating the whole point of the
    casino.
    """

    @pytest.mark.parametrize("template", TOURIST_TEMPLATES, ids=lambda t: t.key)
    def test_anchors_in_fish_range(self, template: TouristTemplate):
        a = template.anchors
        assert 0.70 <= a["baseline_looseness"] <= 0.95, (
            f"{template.key}: looseness {a['baseline_looseness']} outside fish range")
        assert 0.10 <= a["baseline_aggression"] <= 0.45, (
            f"{template.key}: aggression {a['baseline_aggression']} outside fish range")
        # Defining traits — fish never adapt, never recover from tilt
        assert a["adaptation_bias"] == 0.0, f"{template.key}: adaptation_bias must be 0"
        assert a["recovery_rate"] == 0.0, f"{template.key}: recovery_rate must be 0"
        assert a["poise"] <= 0.45, f"{template.key}: poise {a['poise']} too high (must be rattle-able)"

    @pytest.mark.parametrize("template", TOURIST_TEMPLATES, ids=lambda t: t.key)
    def test_required_anchor_keys_present(self, template: TouristTemplate):
        """Mirror existing fish JSON structure — all 9 anchors required."""
        required = {
            "baseline_aggression", "baseline_looseness", "ego", "poise",
            "expressiveness", "risk_identity", "adaptation_bias",
            "baseline_energy", "recovery_rate",
        }
        assert set(template.anchors.keys()) == required

    @pytest.mark.parametrize("template", TOURIST_TEMPLATES, ids=lambda t: t.key)
    def test_has_multiple_candidate_leaks(self, template: TouristTemplate):
        """Variance is the point — single-leak templates would make
        every spawn of that template identical."""
        assert len(template.candidate_leaks) >= 2, (
            f"{template.key} has only one candidate leak — no variance per spawn")

    @pytest.mark.parametrize("template", TOURIST_TEMPLATES, ids=lambda t: t.key)
    def test_no_duplicate_leaks_in_pool(self, template: TouristTemplate):
        keys = [l.value for l in template.candidate_leaks]
        assert len(keys) == len(set(keys)), (
            f"{template.key} has duplicate leaks in candidate_leaks")

    @pytest.mark.parametrize("template", TOURIST_TEMPLATES, ids=lambda t: t.key)
    def test_all_candidate_leaks_are_real(self, template: TouristTemplate):
        """Catches enum typos / removed leaks."""
        for leak in template.candidate_leaks:
            assert leak in FishLeak

    @pytest.mark.parametrize("template", TOURIST_TEMPLATES, ids=lambda t: t.key)
    def test_has_tics_and_name_pool(self, template: TouristTemplate):
        assert template.verbal_tics, f"{template.key} has empty verbal_tics"
        assert template.physical_tics, f"{template.key} has empty physical_tics"
        assert len(template.name_pool) >= 5, (
            f"{template.key} has only {len(template.name_pool)} names — "
            f"need at least 5 for variety")


class TestGenerateTourist:
    def test_returns_tourist_profile(self):
        rng = random.Random(42)
        t = generate_tourist(rng)
        assert isinstance(t, TouristProfile)
        assert t.personality_id.startswith("tourist-")
        assert t.display_name  # non-empty
        assert t.template_key in {tpl.key for tpl in TOURIST_TEMPLATES}

    def test_personality_dict_has_required_fields(self):
        """Mirrors existing fish JSON structure — controllers depend on
        these keys being present."""
        rng = random.Random(42)
        t = generate_tourist(rng)
        d = t.personality_dict
        required_keys = {
            "name", "archetype", "ephemeral", "template_key", "play_style",
            "default_confidence", "default_attitude", "anchors",
            "verbal_tics", "physical_tics", "nickname", "bankroll_knobs",
            "id", "staker_profile", "borrower_profile", "rule_strategy",
            "fish_leak",
        }
        assert required_keys.issubset(set(d.keys())), (
            f"missing keys: {required_keys - set(d.keys())}")
        assert d["archetype"] == "fish"
        assert d["ephemeral"] is True
        assert d["rule_strategy"] == "fish"
        assert d["staker_profile"] == {"willing": False}
        assert d["borrower_profile"] == {"willing": False}
        assert d["id"] == t.personality_id
        assert d["name"] == t.display_name

    def test_fish_leak_in_template_candidate_pool(self):
        """The picked leak must come from the assigned template's pool."""
        rng = random.Random(0)
        for _ in range(50):
            t = generate_tourist(rng)
            template = next(tpl for tpl in TOURIST_TEMPLATES if tpl.key == t.template_key)
            picked = FishLeak(t.personality_dict["fish_leak"])
            assert picked in template.candidate_leaks, (
                f"template {template.key} produced leak {picked} not in {template.candidate_leaks}")

    def test_bankroll_knobs_zero_for_ephemeral(self):
        """Ephemeral tourists have no bankroll — chips live on the seat."""
        rng = random.Random(7)
        t = generate_tourist(rng)
        knobs = t.personality_dict["bankroll_knobs"]
        assert knobs["starting_bankroll"] == 0
        assert knobs["bankroll_rate"] == 0
        assert knobs["stake_comfort_zone"] == "$2"

    def test_pids_are_unique(self):
        """100 calls → 100 distinct pids. Synthetic ids must collision-
        proof so casino_seat_seed ledger rows don't conflate seats."""
        rng = random.Random(0)
        pids = {generate_tourist(rng).personality_id for _ in range(100)}
        assert len(pids) == 100

    def test_deterministic_with_seeded_rng(self):
        """Same seed → identical output. Critical for reproducible
        sandbox tests and audit replays."""
        rng1 = random.Random(12345)
        rng2 = random.Random(12345)
        for _ in range(10):
            t1 = generate_tourist(rng1)
            t2 = generate_tourist(rng2)
            # Pids differ (uuid4 is non-deterministic), but everything else matches
            assert t1.display_name == t2.display_name
            assert t1.template_key == t2.template_key
            assert t1.personality_dict["fish_leak"] == t2.personality_dict["fish_leak"]


class TestGenerateTouristBatch:
    def test_returns_requested_count(self):
        rng = random.Random(0)
        batch = generate_tourist_batch(rng, 4)
        assert len(batch) == 4

    def test_unique_display_names_within_batch(self):
        """No two seats at one casino can have the same display name —
        that would break the rotating-disguise illusion."""
        rng = random.Random(0)
        for seed in range(20):
            batch = generate_tourist_batch(random.Random(seed), 4)
            names = [t.display_name for t in batch]
            assert len(set(names)) == len(names), (
                f"seed {seed}: duplicate names {Counter(names)}")

    def test_unique_pids_within_batch(self):
        rng = random.Random(0)
        batch = generate_tourist_batch(rng, 4)
        pids = [t.personality_id for t in batch]
        assert len(set(pids)) == len(pids)

    def test_handles_zero_count(self):
        assert generate_tourist_batch(random.Random(0), 0) == []

    def test_handles_count_one(self):
        batch = generate_tourist_batch(random.Random(0), 1)
        assert len(batch) == 1


class TestVarietyAcrossSpawns:
    """If 20 batches all produce the same 2 templates, the casino feels
    stale fast. Sanity-check that the factory exercises the template +
    leak diversity it claims to have."""

    def test_multiple_templates_appear_across_many_spawns(self):
        rng = random.Random(0)
        templates_seen: set[str] = set()
        for _ in range(80):
            t = generate_tourist(rng)
            templates_seen.add(t.template_key)
        # With 8 templates and 80 picks, statistically we should see
        # at least 6 of them (P(all 8) ≈ 1, but we use 6 to avoid flakes)
        assert len(templates_seen) >= 6, (
            f"only saw {len(templates_seen)} templates in 80 picks: {templates_seen}")

    def test_multiple_leaks_appear_across_many_spawns(self):
        rng = random.Random(0)
        leaks_seen: set[str] = set()
        for _ in range(80):
            t = generate_tourist(rng)
            leaks_seen.add(t.personality_dict["fish_leak"])
        # 8 leaks total, candidate pools cover all of them collectively
        assert len(leaks_seen) >= 5, (
            f"only saw {len(leaks_seen)} leaks in 80 picks: {leaks_seen}")

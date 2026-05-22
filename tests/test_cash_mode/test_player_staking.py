"""Phase 5 refinement (2026-05-21) — player_staking helpers.

Covers:
  - `_next_tier` / `_compute_desperation` / `_relationship_score`
    pure-math helpers.
  - `list_stakeable_ai` end-to-end: gate enforcement + per-tier
    sampling.
  - `evaluate_player_offer`: cut-penalty + desperation-relief math
    against the willingness threshold.

The full route integration (POST /api/cash/stakes/offer) is exercised
by ad-hoc smoke tests in dev; the per-helper tests here pin the math
so regressions surface cleanly.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from cash_mode.bankroll import AIBankrollState, PlayerBankrollState
from cash_mode.player_staking import (
    CUT_PENALTY_SLOPE,
    DESPERATION_RELIEF,
    FAIR_CUT_REFERENCE,
    _compute_desperation,
    _next_tier,
    _relationship_score,
    evaluate_player_offer,
    list_stakeable_ai,
)
from cash_mode.stakes import (
    BORROWER_KIND_PERSONALITY,
    STAKE_FORMAT_PURE,
    STAKE_STATUS_ACTIVE,
    STAKE_STATUS_DEFAULTED,
    STAKER_KIND_HUMAN,
    Stake,
)
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.relationship_repository import RelationshipRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.stake_repository import StakeRepository

PLAYER_ID = "test-player"
SBX = "test-sandbox-1"
ANCHOR = datetime(2026, 5, 21, 12, 0, 0)


# --- Pure-math tests ---------------------------------------------------------


class TestNextTier(unittest.TestCase):
    def test_returns_next(self):
        self.assertEqual(_next_tier("$2"), "$10")
        self.assertEqual(_next_tier("$10"), "$50")
        self.assertEqual(_next_tier("$200"), "$1000")

    def test_top_tier_returns_none(self):
        self.assertIsNone(_next_tier("$1000"))

    def test_unknown_returns_none(self):
        self.assertIsNone(_next_tier("$99"))


class TestComputeDesperation(unittest.TestCase):
    def test_high_ego_broke_is_max_desperate(self):
        self.assertAlmostEqual(
            _compute_desperation(ego=1.0, current_chips=0, starting_bankroll=1000),
            1.0,
        )

    def test_low_ego_never_desperate(self):
        # ego=0 multiplied by any wealth_deficit = 0.
        self.assertEqual(
            _compute_desperation(ego=0.0, current_chips=0, starting_bankroll=1000),
            0.0,
        )

    def test_above_starting_no_desperation(self):
        # wealth_deficit floors at 0 — surplus doesn't make AI desperate.
        self.assertEqual(
            _compute_desperation(ego=1.0, current_chips=2000, starting_bankroll=1000),
            0.0,
        )

    def test_half_starting_half_desperate(self):
        self.assertAlmostEqual(
            _compute_desperation(ego=0.5, current_chips=500, starting_bankroll=1000),
            0.25,
        )

    def test_zero_starting_returns_zero(self):
        # Defensive: starting_bankroll=0 means we can't compute a ratio.
        self.assertEqual(
            _compute_desperation(ego=1.0, current_chips=0, starting_bankroll=0),
            0.0,
        )


class TestRelationshipScore(unittest.TestCase):
    def test_neutral_score(self):
        # Default no-history relationship axes.
        self.assertAlmostEqual(
            _relationship_score(likability=0.5, respect=0.5, heat=0.0),
            0.45,
        )

    def test_heat_subtracts(self):
        warm = _relationship_score(likability=0.5, respect=0.5, heat=0.0)
        hot = _relationship_score(likability=0.5, respect=0.5, heat=1.0)
        self.assertGreater(warm, hot)


class TestWillingnessThresholdDerivation(unittest.TestCase):
    """The borrower profile loader derives willingness_threshold from
    the personality's `ego` anchor when not explicitly set. This pins
    the calibration so a future tweak (slope, clamps) surfaces here
    rather than as a silent behavior change in the staking flow."""

    def test_humble_ego_yields_low_threshold(self):
        from cash_mode.staker_profile import compute_default_willingness_threshold
        # Lincoln-style humble: ego 0.36 → ~0.23
        self.assertAlmostEqual(
            compute_default_willingness_threshold(0.36), 0.23, places=2,
        )

    def test_baseline_ego_yields_default(self):
        from cash_mode.staker_profile import compute_default_willingness_threshold
        self.assertAlmostEqual(
            compute_default_willingness_threshold(0.5), 0.30, places=2,
        )

    def test_proud_ego_yields_high_threshold(self):
        from cash_mode.staker_profile import compute_default_willingness_threshold
        # Napoleon-style proud: ego 0.86 → ~0.48
        self.assertAlmostEqual(
            compute_default_willingness_threshold(0.86), 0.48, places=2,
        )

    def test_extreme_egos_clamped(self):
        from cash_mode.staker_profile import (
            WILLINGNESS_THRESHOLD_MAX,
            WILLINGNESS_THRESHOLD_MIN,
            compute_default_willingness_threshold,
        )
        # Below clamp.
        self.assertEqual(
            compute_default_willingness_threshold(0.0),
            WILLINGNESS_THRESHOLD_MIN,
        )
        # Above clamp.
        self.assertEqual(
            compute_default_willingness_threshold(1.0),
            WILLINGNESS_THRESHOLD_MAX,
        )


class TestAspirationBiasDerivation(unittest.TestCase):
    """Pure-function tests for `compute_default_aspiration_bias`. Pins
    the calibration so future tweaks (weights, clamp behavior) surface
    here rather than as silent shifts in trigger probabilities.

    Spec: docs/plans/CASH_MODE_AI_ASPIRATION_ASK.md Commit 1.
    """

    def test_baseline_yields_midpoint(self):
        from cash_mode.staker_profile import compute_default_aspiration_bias
        # 0.6 × 0.5 + 0.4 × 0.5 = 0.5
        self.assertAlmostEqual(
            compute_default_aspiration_bias(0.5, 0.5), 0.5, places=4,
        )

    def test_lincoln_class_grinder(self):
        from cash_mode.staker_profile import compute_default_aspiration_bias
        # Lincoln (ego 0.36, risk 0.38) → 0.6×0.36 + 0.4×0.38 = 0.368
        self.assertAlmostEqual(
            compute_default_aspiration_bias(0.36, 0.38), 0.368, places=4,
        )

    def test_napoleon_class_climber(self):
        from cash_mode.staker_profile import compute_default_aspiration_bias
        # Napoleon (ego 0.86, risk 0.90) → 0.6×0.86 + 0.4×0.90 = 0.876
        self.assertAlmostEqual(
            compute_default_aspiration_bias(0.86, 0.90), 0.876, places=4,
        )

    def test_clamps_at_zero(self):
        from cash_mode.staker_profile import compute_default_aspiration_bias
        # Below clamp (defensive — out-of-range anchor values).
        self.assertEqual(compute_default_aspiration_bias(-1.0, -1.0), 0.0)

    def test_clamps_at_one(self):
        from cash_mode.staker_profile import compute_default_aspiration_bias
        # Above clamp.
        self.assertEqual(compute_default_aspiration_bias(2.0, 2.0), 1.0)

    def test_ego_weighted_more_than_risk(self):
        """Ego dominates the composite — high ego with low risk still
        produces a climber-ish value, while low ego with high risk
        stays below 0.5. This pins the relative weighting decision."""
        from cash_mode.staker_profile import compute_default_aspiration_bias
        high_ego_low_risk = compute_default_aspiration_bias(1.0, 0.0)
        low_ego_high_risk = compute_default_aspiration_bias(0.0, 1.0)
        self.assertGreater(high_ego_low_risk, low_ego_high_risk)
        self.assertAlmostEqual(high_ego_low_risk, 0.6, places=4)
        self.assertAlmostEqual(low_ego_high_risk, 0.4, places=4)

    def test_loader_uses_ego_when_threshold_not_set(self, db_repos=None):
        """End-to-end: a personality with ego=0.86 and no explicit
        willingness_threshold loads with the derived value."""
        import json
        import sqlite3
        import tempfile
        from poker.repositories.schema_manager import SchemaManager
        from poker.repositories.bankroll_repository import BankrollRepository

        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        SchemaManager(db).ensure_schema()
        config = {
            "anchors": {"ego": 0.86, "poise": 0.5},
            "borrower_profile": {"willing": True},  # no willingness_threshold
        }
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO personalities "
                "(name, personality_id, config_json, visibility) "
                "VALUES (?, ?, ?, 'public')",
                ("Proud", "proud_pid", json.dumps(config)),
            )
        repo = BankrollRepository(db)
        profile = repo.load_borrower_profile("proud_pid")
        self.assertTrue(profile.willing)
        self.assertAlmostEqual(profile.willingness_threshold, 0.48, places=2)
        import os
        os.unlink(db)

    def test_save_borrower_profile_persists_override(self):
        """Save an explicit override; loader reads it back."""
        import json
        import sqlite3
        import tempfile
        import os
        from poker.repositories.schema_manager import SchemaManager
        from poker.repositories.bankroll_repository import BankrollRepository

        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        SchemaManager(db).ensure_schema()
        config = {
            "anchors": {"ego": 0.86},  # would derive ~0.48
            "borrower_profile": {"willing": True},
        }
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO personalities "
                "(name, personality_id, config_json, visibility) "
                "VALUES (?, ?, ?, 'public')",
                ("Test", "test_pid", json.dumps(config)),
            )
        repo = BankrollRepository(db)
        # Pre-save: derived from ego.
        before = repo.load_borrower_profile("test_pid")
        self.assertAlmostEqual(before.willingness_threshold, 0.48, places=2)
        # Save an explicit override.
        ok = repo.save_borrower_profile(
            "test_pid", willing=True, willingness_threshold=0.20,
        )
        self.assertTrue(ok)
        after = repo.load_borrower_profile("test_pid")
        self.assertEqual(after.willingness_threshold, 0.20)
        # Clear the override → should fall back to ego-derived again.
        ok = repo.save_borrower_profile(
            "test_pid", willing=True, willingness_threshold=None,
        )
        self.assertTrue(ok)
        reverted = repo.load_borrower_profile("test_pid")
        self.assertAlmostEqual(reverted.willingness_threshold, 0.48, places=2)
        os.unlink(db)

    def test_save_borrower_profile_preserves_other_config(self):
        """Saving borrower_profile must NOT touch sibling keys."""
        import json
        import sqlite3
        import tempfile
        import os
        from poker.repositories.schema_manager import SchemaManager
        from poker.repositories.bankroll_repository import BankrollRepository

        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        SchemaManager(db).ensure_schema()
        config = {
            "anchors": {"ego": 0.5, "poise": 0.7},
            "bankroll_knobs": {
                "starting_bankroll": 12000,
                "bankroll_rate": 350,
            },
            "staker_profile": {"willing": True, "rate_anchor": 0.25},
            "verbal_tics": ["test tic"],
        }
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO personalities "
                "(name, personality_id, config_json, visibility) "
                "VALUES (?, ?, ?, 'public')",
                ("Test", "test_pid", json.dumps(config)),
            )
        repo = BankrollRepository(db)
        repo.save_borrower_profile(
            "test_pid", willing=False, willingness_threshold=0.40,
        )
        # Re-load full config and confirm every sibling key survives.
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT config_json FROM personalities WHERE personality_id = ?",
                ("test_pid",),
            ).fetchone()
        cfg = json.loads(row[0])
        self.assertEqual(cfg["anchors"]["ego"], 0.5)
        self.assertEqual(cfg["anchors"]["poise"], 0.7)
        self.assertEqual(cfg["bankroll_knobs"]["starting_bankroll"], 12000)
        self.assertEqual(cfg["staker_profile"]["rate_anchor"], 0.25)
        self.assertEqual(cfg["verbal_tics"], ["test tic"])
        self.assertEqual(cfg["borrower_profile"]["willing"], False)
        self.assertEqual(cfg["borrower_profile"]["willingness_threshold"], 0.40)
        os.unlink(db)

    def test_loader_explicit_override_wins(self):
        """When the sub-dict sets willingness_threshold explicitly, it
        overrides the ego derivation."""
        import json
        import sqlite3
        import tempfile
        from poker.repositories.schema_manager import SchemaManager
        from poker.repositories.bankroll_repository import BankrollRepository

        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        SchemaManager(db).ensure_schema()
        config = {
            "anchors": {"ego": 0.86},  # would derive ~0.48
            "borrower_profile": {
                "willing": True,
                "willingness_threshold": 0.20,  # explicit override
            },
        }
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO personalities "
                "(name, personality_id, config_json, visibility) "
                "VALUES (?, ?, ?, 'public')",
                ("Override", "override_pid", json.dumps(config)),
            )
        repo = BankrollRepository(db)
        profile = repo.load_borrower_profile("override_pid")
        self.assertEqual(profile.willingness_threshold, 0.20)
        import os
        os.unlink(db)


class TestCutPenaltyCalibration(unittest.TestCase):
    def test_below_fair_no_penalty(self):
        penalty = max(0.0, 0.20 - FAIR_CUT_REFERENCE) * CUT_PENALTY_SLOPE
        self.assertEqual(penalty, 0.0)

    def test_at_fair_no_penalty(self):
        penalty = max(0.0, 0.30 - FAIR_CUT_REFERENCE) * CUT_PENALTY_SLOPE
        self.assertEqual(penalty, 0.0)

    def test_steep_cut_penalty_scales(self):
        # 0.55 cut = 0.25 over fair × 2.0 slope = 0.50 penalty
        penalty = max(0.0, 0.55 - FAIR_CUT_REFERENCE) * CUT_PENALTY_SLOPE
        self.assertAlmostEqual(penalty, 0.50, places=2)


# --- Integration tests against tempdb ---------------------------------------


@pytest.fixture
def db_repos(tmp_path):
    db = str(tmp_path / "player_staking.db")
    SchemaManager(db).ensure_schema()
    return {
        "db": db,
        "bankroll": BankrollRepository(db),
        "stake": StakeRepository(db),
        "personality": PersonalityRepository(db),
        "relationship": RelationshipRepository(db),
        "cash_table": CashTableRepository(db),
    }


def _seed_personality(repos, pid, name, *, comfort, ego=0.5, starting=10_000):
    """Insert a personality with bankroll_knobs + anchors so the
    staking helpers have something to work with."""
    config = {
        "anchors": {
            "baseline_aggression": 0.5, "baseline_looseness": 0.3,
            "ego": ego, "poise": 0.7,
        },
        "bankroll_knobs": {
            "starting_bankroll": starting,
            "bankroll_rate": 0,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": comfort,
        },
        "borrower_profile": {"willing": True, "willingness_threshold": 0.30},
    }
    import sqlite3
    with sqlite3.connect(repos["db"]) as conn:
        conn.execute(
            "INSERT INTO personalities (name, personality_id, config_json, visibility) "
            "VALUES (?, ?, ?, 'public')",
            (name, pid, json.dumps(config)),
        )
    repos["bankroll"].save_ai_bankroll(
        AIBankrollState(personality_id=pid, chips=starting, last_regen_tick=ANCHOR),
        sandbox_id=SBX,
    )


def _seed_relationship(repos, *, observer, opponent, likability=0.5, respect=0.5, heat=0.0):
    """Insert a relationship row so the met-before gate clears."""
    from poker.memory.opponent_model import RelationshipState
    repos["relationship"].save_relationship_state(
        observer,
        opponent,
        RelationshipState(
            likability=likability,
            respect=respect,
            heat=heat,
            last_seen=ANCHOR,
            last_decay_tick=ANCHOR,
        ),
    )


def _seed_player_bankroll(repos, chips=10_000):
    repos["bankroll"].save_player_bankroll(PlayerBankrollState(
        player_id=PLAYER_ID,
        chips=chips,
        starting_bankroll=chips,
    ))


class TestListStakeableAI:
    def test_met_before_gate(self, db_repos):
        """Without a prior relationship row, the AI is filtered out."""
        _seed_personality(db_repos, "napoleon", "Napoleon", comfort="$10")
        _seed_player_bankroll(db_repos)
        # No relationship seeded.
        candidates = list_stakeable_ai(
            owner_id=PLAYER_ID,
            player_bankroll=10_000,
            sandbox_id=SBX,
            personality_repo=db_repos["personality"],
            bankroll_repo=db_repos["bankroll"],
            relationship_repo=db_repos["relationship"],
            stake_repo=db_repos["stake"],
            cash_table_repo=db_repos["cash_table"],
            now=ANCHOR,
        )
        assert candidates == []

    def test_full_gate_pass_surfaces_at_plus_one_tier(self, db_repos):
        """AI with comfort $10 + met-before + good relationship +
        sufficient player bankroll → surfaces with target_stake = $50."""
        _seed_personality(db_repos, "napoleon", "Napoleon", comfort="$10")
        _seed_player_bankroll(db_repos)
        _seed_relationship(
            db_repos, observer="napoleon", opponent=PLAYER_ID,
            likability=0.6, respect=0.7, heat=0.0,
        )
        candidates = list_stakeable_ai(
            owner_id=PLAYER_ID,
            player_bankroll=10_000,
            sandbox_id=SBX,
            personality_repo=db_repos["personality"],
            bankroll_repo=db_repos["bankroll"],
            relationship_repo=db_repos["relationship"],
            stake_repo=db_repos["stake"],
            cash_table_repo=db_repos["cash_table"],
            now=ANCHOR,
        )
        assert len(candidates) == 1
        c = candidates[0]
        assert c.personality_id == "napoleon"
        assert c.comfort_zone == "$10"
        assert c.target_stake_label == "$50"

    def test_top_tier_ai_not_stakable(self, db_repos):
        """A $1000-comfort AI has no +1 tier — they're at the top."""
        _seed_personality(db_repos, "whale", "Whale", comfort="$1000", starting=200_000)
        _seed_player_bankroll(db_repos, chips=500_000)
        _seed_relationship(db_repos, observer="whale", opponent=PLAYER_ID, likability=0.8)
        candidates = list_stakeable_ai(
            owner_id=PLAYER_ID,
            player_bankroll=500_000,
            sandbox_id=SBX,
            personality_repo=db_repos["personality"],
            bankroll_repo=db_repos["bankroll"],
            relationship_repo=db_repos["relationship"],
            stake_repo=db_repos["stake"],
            cash_table_repo=db_repos["cash_table"],
            now=ANCHOR,
        )
        assert candidates == []

    def test_relationship_floor_filters(self, db_repos):
        """High heat or low likability filters the AI out."""
        _seed_personality(db_repos, "napoleon", "Napoleon", comfort="$10")
        _seed_player_bankroll(db_repos)
        # Heat above ceiling.
        _seed_relationship(
            db_repos, observer="napoleon", opponent=PLAYER_ID,
            likability=0.5, respect=0.5, heat=0.6,
        )
        candidates = list_stakeable_ai(
            owner_id=PLAYER_ID,
            player_bankroll=10_000,
            sandbox_id=SBX,
            personality_repo=db_repos["personality"],
            bankroll_repo=db_repos["bankroll"],
            relationship_repo=db_repos["relationship"],
            stake_repo=db_repos["stake"],
            cash_table_repo=db_repos["cash_table"],
            now=ANCHOR,
        )
        assert candidates == []

    def test_recent_default_filters(self, db_repos):
        """A 7-day-recent default from this AI to this player blocks
        re-offering them."""
        _seed_personality(db_repos, "napoleon", "Napoleon", comfort="$10")
        _seed_player_bankroll(db_repos)
        _seed_relationship(
            db_repos, observer="napoleon", opponent=PLAYER_ID,
            likability=0.6, respect=0.6,
        )
        # Seed a defaulted stake from this player to this AI 3 days ago.
        db_repos["stake"].create_stake(Stake(
            stake_id="prior_default",
            session_id="player_session_napoleon_prior",
            staker_id=PLAYER_ID,
            staker_kind=STAKER_KIND_HUMAN,
            borrower_id="napoleon",
            borrower_kind=BORROWER_KIND_PERSONALITY,
            format=STAKE_FORMAT_PURE,
            principal=2000,
            match_amount=0,
            origination_fee=0,
            cut=0.30,
            status=STAKE_STATUS_DEFAULTED,
            carry_amount=0,
            stake_tier="$50",
            created_at=ANCHOR - timedelta(days=5),
            settled_at=ANCHOR - timedelta(days=3),  # within 7d cooldown
        ))
        candidates = list_stakeable_ai(
            owner_id=PLAYER_ID,
            player_bankroll=10_000,
            sandbox_id=SBX,
            personality_repo=db_repos["personality"],
            bankroll_repo=db_repos["bankroll"],
            relationship_repo=db_repos["relationship"],
            stake_repo=db_repos["stake"],
            cash_table_repo=db_repos["cash_table"],
            now=ANCHOR,
        )
        assert candidates == []


class TestEvaluatePlayerOffer:
    def test_fair_offer_to_friendly_ai_accepted(self, db_repos):
        """Score (0.66 from friendly axes) > threshold (0.30 with no
        cut penalty, no desperation relief) → accepted."""
        _seed_personality(db_repos, "napoleon", "Napoleon", comfort="$10")
        _seed_relationship(
            db_repos, observer="napoleon", opponent=PLAYER_ID,
            likability=0.7, respect=0.8, heat=0.0,
        )
        result = evaluate_player_offer(
            target_pid="napoleon",
            owner_id=PLAYER_ID,
            principal=2000,
            cut=0.30,  # at fair reference — no penalty
            sandbox_id=SBX,
            personality_repo=db_repos["personality"],
            bankroll_repo=db_repos["bankroll"],
            relationship_repo=db_repos["relationship"],
            now=ANCHOR,
        )
        assert result.accepted is True

    def test_steep_cut_refused_with_cut_too_steep(self, db_repos):
        """50% cut + neutral goodwill + comfortable AI → refused
        for cut overage."""
        _seed_personality(db_repos, "napoleon", "Napoleon", comfort="$10", ego=0.5)
        _seed_relationship(
            db_repos, observer="napoleon", opponent=PLAYER_ID,
            likability=0.5, respect=0.5, heat=0.0,
        )
        result = evaluate_player_offer(
            target_pid="napoleon",
            owner_id=PLAYER_ID,
            principal=2000,
            cut=0.50,
            sandbox_id=SBX,
            personality_repo=db_repos["personality"],
            bankroll_repo=db_repos["bankroll"],
            relationship_repo=db_repos["relationship"],
            now=ANCHOR,
        )
        assert result.accepted is False
        assert result.reason == 'cut_too_steep'
        # 0.30 base + (0.20)*2 cut_penalty - 0 relief = 0.70
        assert result.effective_threshold > 0.65

    def test_desperate_proud_ai_accepts_steep_cut(self, db_repos):
        """High-ego AI down to 0 chips → max desperation → relief = 0.4,
        offsetting cut_penalty. Should accept where comfortable AI refused."""
        _seed_personality(
            db_repos, "napoleon", "Napoleon", comfort="$10",
            ego=1.0, starting=10_000,
        )
        # Drain bankroll to 0 → desperation = 1.0
        db_repos["bankroll"].save_ai_bankroll(
            AIBankrollState(personality_id="napoleon", chips=0, last_regen_tick=ANCHOR),
            sandbox_id=SBX,
        )
        _seed_relationship(
            db_repos, observer="napoleon", opponent=PLAYER_ID,
            likability=0.5, respect=0.5, heat=0.0,
        )
        result = evaluate_player_offer(
            target_pid="napoleon",
            owner_id=PLAYER_ID,
            principal=2000,
            cut=0.50,
            sandbox_id=SBX,
            personality_repo=db_repos["personality"],
            bankroll_repo=db_repos["bankroll"],
            relationship_repo=db_repos["relationship"],
            now=ANCHOR,
        )
        # score=0.45 (default 0.5/0.5/0.0 from the seed), threshold:
        # 0.30 + 0.40 - 1.0*0.4 = 0.30
        # 0.45 > 0.30 → ACCEPTED
        assert result.accepted is True
        assert result.desperation == 1.0
        assert result.desperation_relief == DESPERATION_RELIEF

    def test_low_goodwill_refused_with_low_goodwill_reason(self, db_repos):
        """At-fair cut but no goodwill → refused with low_goodwill."""
        _seed_personality(db_repos, "napoleon", "Napoleon", comfort="$10")
        _seed_relationship(
            db_repos, observer="napoleon", opponent=PLAYER_ID,
            likability=0.1, respect=0.1, heat=0.0,
        )
        result = evaluate_player_offer(
            target_pid="napoleon",
            owner_id=PLAYER_ID,
            principal=2000,
            cut=0.30,  # no cut penalty
            sandbox_id=SBX,
            personality_repo=db_repos["personality"],
            bankroll_repo=db_repos["bankroll"],
            relationship_repo=db_repos["relationship"],
            now=ANCHOR,
        )
        # score = 0.09; threshold = 0.30 → refused
        assert result.accepted is False
        assert result.reason == 'low_goodwill'


if __name__ == '__main__':
    unittest.main()

"""Integration test for the per-persona `skill` read (PLAYER_SKILL_SPECTRUM.md Phase 4).

A persona carrying `"skill": "<tier>"` in its config must have the tier applied
when built as a tiered (`sharp`) controller — the live wiring that turns the
authored roster into behavior. An un-tiered persona (or the default `shark`
ceiling) must be byte-identical to today.
"""

import pytest

from flask_app.handlers.tiered_factory import build_tiered_controller
from poker.poker_game import initialize_game_state
from poker.poker_player import AIPokerPlayer
from poker.poker_state_machine import PokerStateMachine
from poker.personality_generator import PersonalityGenerator

pytestmark = pytest.mark.integration

_BASE_ANCHORS = {
    "baseline_aggression": 0.5, "baseline_looseness": 0.5, "ego": 0.5, "poise": 0.5,
    "expressiveness": 0.5, "risk_identity": 0.5, "adaptation_bias": 0.5,
    "baseline_energy": 0.5, "recovery_rate": 0.2,
}


def _persona(skill=None):
    cfg = {
        "play_style": "x", "default_confidence": "y", "default_attitude": "z",
        "anchors": dict(_BASE_ANCHORS),
        "personality_traits": {"tightness": .5, "aggression": .5, "confidence": .5,
                               "composure": .7, "table_talk": .5},
    }
    if skill:
        cfg["skill"] = skill
    return cfg


@pytest.fixture
def build_with_skill():
    """Build a tiered controller for a persona whose config carries `skill`,
    injecting the persona into the shared generator cache (no DB needed)."""
    def _build(name, skill):
        AIPokerPlayer._personality_generator = PersonalityGenerator()
        AIPokerPlayer._personality_generator._cache[name] = _persona(skill)
        gs = initialize_game_state(player_names=[name, "Opp1", "Opp2"])
        sm = PokerStateMachine(game_state=gs)
        return build_tiered_controller(
            player_name=name, state_machine=sm, llm_config={},
            game_id=None, owner_id=None, expression_enabled=False,
        )
    yield _build
    AIPokerPlayer._personality_generator = None  # don't leak the cache


def test_rec_persona_applies_weakest_intensities(build_with_skill):
    c = build_with_skill("RecGuy", "rec")
    assert c.exploitation_strength == 0.1
    assert c.river_bluff_fraction == 0.0
    assert c.stab_defense_intensity == 0.0
    assert c.overbet_fraction == 0.0


def test_weak_reg_persona_applies_mid_intensities(build_with_skill):
    c = build_with_skill("Greg", "weak_reg")
    assert c.exploitation_strength == 0.4
    assert c.river_bluff_fraction == 0.5
    assert c.stab_defense_intensity == 0.25
    assert c.overbet_fraction == 0.5


def test_shark_persona_is_a_noop_at_the_ceiling(build_with_skill):
    # shark == constructor defaults; the no-op tier must not set exploitation_strength
    # (it's read via getattr default 1.0) and must leave the rest at ceiling.
    c = build_with_skill("SharkGal", "shark")
    assert not hasattr(c, "exploitation_strength")
    assert c.river_bluff_fraction == 1.0
    assert c.stab_defense_intensity == 0.5
    assert c.overbet_fraction == 1.0


def test_untagged_persona_matches_today(build_with_skill):
    # No `skill` key → identical to the shark ceiling (today's behavior preserved).
    c = build_with_skill("PlainJane", None)
    assert not hasattr(c, "exploitation_strength")
    assert c.river_bluff_fraction == 1.0
    assert c.stab_defense_intensity == 0.5
    assert c.overbet_fraction == 1.0


def test_unknown_skill_falls_back_to_default(build_with_skill):
    # A typo'd tier must NOT crash construction — it logs and keeps the ceiling.
    c = build_with_skill("Typoed", "genius")
    assert not hasattr(c, "exploitation_strength")
    assert c.river_bluff_fraction == 1.0
    assert c.overbet_fraction == 1.0

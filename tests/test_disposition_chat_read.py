"""Tests for the disposition-aware 'opponent read' folded into quick-chat
suggestions. `target_social_read` maps a target AI's social disposition
(from its psychology anchors) to a one-line tell for the suggestion LLM.
"""

from __future__ import annotations

from types import SimpleNamespace

from flask_app.handlers.chat_reads import target_social_read
from poker.player_psychology import PlayerPsychology


def _game_data(name, anchors):
    psych = PlayerPsychology.from_personality_config(name, {"anchors": anchors})
    return {"ai_controllers": {name: SimpleNamespace(psychology=psych)}}


# Napoleon-like: proud + reserved → stung.
STUNG = {"ego": 0.86, "poise": 0.65, "expressiveness": 0.32, "baseline_aggression": 0.8}
# Wilde-like: proud + expressive → energized.
ENERGIZED = {"ego": 0.8, "poise": 0.62, "expressiveness": 0.68, "baseline_aggression": 0.35}
# Buddha-like: serene → stoic.
STOIC = {"ego": 0.36, "poise": 0.9, "expressiveness": 0.4, "baseline_aggression": 0.15}


def test_stung_read_names_target_and_warns_it_lands():
    read = target_social_read(_game_data("Napoleon", STUNG), "Napoleon")
    assert "Napoleon" in read
    assert "personally" in read


def test_energized_read_invites_a_volley():
    read = target_social_read(_game_data("Wilde", ENERGIZED), "Wilde")
    assert "banter" in read


def test_stoic_read_flags_hard_to_rattle():
    read = target_social_read(_game_data("Buddha", STOIC), "Buddha")
    assert "rattle" in read


def test_unknown_or_missing_target_is_empty():
    assert target_social_read({"ai_controllers": {}}, "Ghost") == ""
    assert target_social_read({}, "Ghost") == ""
    assert target_social_read(_game_data("Napoleon", STUNG), None) == ""

"""Headless drive of a `TableScene` — the Scene Engine, Pillar 1 (testability).

These tests are the canary the vision asked for: Scene-0 is driven to graduation
*in-process* and asserted in CI — the finale (Sal busts Larry to 0) that used to
need a human playtest, every teaching-hand fork (fold-or-stay → pass/fail), the
per-street fish tell, conservation, and authoring validation. They exercise the
SAME judge / scripted-action / narration code the live game_handler runs (it
delegates to `scene_runner`), so a green here means the live scene is sound too.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

from cash_mode import scene_runner as sr, table_scenes
from cash_mode.career_progression import SAL_ID, SCENE0_FISH_ID

SCENE0 = table_scenes.SCENE0
FISH = SCENE0_FISH_ID  # 'loose_larry'
MENTOR = SAL_ID  # 'sal_moretti'


# --- the finale canary -------------------------------------------------------


def test_scene0_runs_to_graduation_and_busts_the_fish():
    """Drive the whole tutorial: the finale stacks Larry to 0, the mentor covers
    him (chips transfer, never mint), and the completion effect fires."""
    res = sr.run_scene(SCENE0, hero=sr.hero_intent("fold"))

    assert res.completed is True
    assert res.hands_played == SCENE0.length  # all 10 hands played
    assert res.on_complete == "career_first_vouch"

    # The finale: the fish busts; the mentor covered (took the chips).
    assert res.busted == [FISH]
    assert res.final_stacks[FISH] == 0
    assert res.final_stacks[MENTOR] > 0

    # Conservation: chips moved (Larry → Sal), nothing minted beyond the declared
    # cast top-ups (the in-memory stand-in for the bankroll-funded top-up).
    assert res.conserved

    # The graduation beat played (the mentor's closing sequence).
    grad = res.lines(role="mentor", trigger="graduation")
    assert grad == list(SCENE0.graduation_lines)
    assert len(grad) >= 1


# --- per-lesson forks (the choice the judge routes on) -----------------------


def test_passing_every_lesson_counts_all_three():
    """Stay on value + bluff-catch, lay down discipline → all three pass, and the
    mentor's *pass* line is the one narrated for each."""
    res = sr.run_scene(
        SCENE0,
        hero=sr.hero_by_lesson(
            {"value": "passive", "bluff_catch": "passive", "discipline": "fold"}
        ),
    )
    assert res.passed == 3
    verdicts = res.lines(role="mentor", trigger="verdict")
    # Each teaching hand's PASS line (not the fail line) was emitted.
    import cash_mode.career_scene as cs

    for hand in cs.SCENE0_SCRIPT:
        if hand.lesson:
            assert hand.sal_pass in verdicts
            assert hand.sal_fail not in verdicts


def test_failing_every_lesson_passes_none_and_narrates_the_fail_line():
    """The mirror: fold the ones you should stay in, call the one you should fold
    → zero passes, and each FAIL line is the one narrated."""
    res = sr.run_scene(
        SCENE0,
        hero=sr.hero_by_lesson({"value": "fold", "bluff_catch": "fold", "discipline": "passive"}),
    )
    assert res.passed == 0
    verdicts = res.lines(role="mentor", trigger="verdict")
    import cash_mode.career_scene as cs

    for hand in cs.SCENE0_SCRIPT:
        if hand.lesson:
            assert hand.sal_fail in verdicts
            assert hand.sal_pass not in verdicts


def test_each_lesson_forks_independently():
    """A mixed line: pass bluff-catch, fail value + discipline → exactly 1 pass."""
    res = sr.run_scene(
        SCENE0,
        hero=sr.hero_by_lesson(
            {"value": "fold", "bluff_catch": "passive", "discipline": "passive"}
        ),
    )
    assert res.passed == 1


# --- per-street fish tell ----------------------------------------------------


def test_discipline_fish_tell_fires_on_the_flop_only():
    """Larry comes alive on the FLOP of the discipline hand (when he makes the nut
    straight) — the per-street tell, captured headlessly as narration."""
    import cash_mode.career_scene as cs

    disc = next(h for h in cs.SCENE0_SCRIPT if h.lesson == "discipline")
    # The hero must reach the flop on the discipline hand for Larry's flop tell to
    # fire — fold preflop and the hand ends before the board comes. Stay in.
    res = sr.run_scene(SCENE0, hero=sr.hero_by_lesson({"discipline": "passive"}))
    street_lines = res.lines(role="fish", trigger="street")
    assert disc.fish_streets["FLOP"] in street_lines
    # The discipline hand scripts no TURN/RIVER fish street line.
    assert "TURN" not in disc.fish_streets and "RIVER" not in disc.fish_streets


# --- conservation across hero lines -----------------------------------------


@pytest.mark.parametrize("intent", ["fold", "passive"])
def test_chips_are_conserved_for_any_hero_line(intent):
    res = sr.run_scene(SCENE0, hero=sr.hero_intent(intent))
    assert res.conserved
    assert all(stack >= 0 for stack in res.final_stacks.values())


# --- authoring validation ----------------------------------------------------


def test_scene0_validates_clean():
    assert sr.validate_scene(SCENE0) == []


def test_validation_catches_a_duplicate_card():
    import dataclasses

    import cash_mode.career_scene as cs

    bad_hand = dataclasses.replace(
        cs._VALUE,
        board=["Ks", "7d", "2c", "9h", "7s"],  # 7s already a hero hole card
    )
    bad_scene = dataclasses.replace(SCENE0, script=[bad_hand])
    errors = sr.validate_scene(bad_scene)
    assert any("placed more than once" in e for e in errors)


def test_validation_catches_an_unknown_intent():
    import dataclasses

    import cash_mode.career_scene as cs

    bad_hand = dataclasses.replace(cs._VALUE, fish_plan={"FLOP": "teleport"})
    bad_scene = dataclasses.replace(SCENE0, script=[bad_hand])
    errors = sr.validate_scene(bad_scene)
    assert any("unknown intent" in e for e in errors)


def test_validation_catches_a_judged_hand_missing_its_verdict():
    import dataclasses

    import cash_mode.career_scene as cs

    bad_hand = dataclasses.replace(cs._VALUE, sal_fail="")  # lesson but no fail line
    bad_scene = dataclasses.replace(SCENE0, script=[bad_hand])
    errors = sr.validate_scene(bad_scene)
    assert any("verdict line" in e for e in errors)


def test_validation_catches_a_short_board():
    import dataclasses

    import cash_mode.career_scene as cs

    bad_hand = dataclasses.replace(cs._VALUE, board=["Ks", "7d", "2c"])
    bad_scene = dataclasses.replace(SCENE0, script=[bad_hand])
    errors = sr.validate_scene(bad_scene)
    assert any("5-card board" in e for e in errors)


# --- shared-core parity (guards against future divergence) -------------------


def test_judge_matches_the_live_handler_rule():
    """The runner's judge is the SAME function the live game_handler delegates to
    — assert the binary rule both share (folded ⇔ discipline pass)."""
    import cash_mode.career_scene as cs

    value = next(h for h in cs.SCENE0_SCRIPT if h.lesson == "value")
    disc = next(h for h in cs.SCENE0_SCRIPT if h.lesson == "discipline")
    filler = cs.SCENE0_SCRIPT[1]  # no lesson

    assert sr.judge_hand(value, hero_folded=False) is True
    assert sr.judge_hand(value, hero_folded=True) is False
    assert sr.judge_hand(disc, hero_folded=True) is True
    assert sr.judge_hand(disc, hero_folded=False) is False
    assert sr.judge_hand(filler, hero_folded=False) is None  # no lesson → no verdict

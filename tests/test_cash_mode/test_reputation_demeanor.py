"""Tests for player-prestige hook 4 (AI demeanor).

Two pure surfaces:
  - `cash_mode.prestige.reputation_demeanor_stimulus` — quadrant → coarse
    stimulus (only the high-renown quadrants react).
  - `PlayerPsychology.react_to_table_reputation` — the bounded, poise-filtered
    axis nudge (villain rattles low-poise opponents; legend lifts).

The handler glue (`_apply_reputation_demeanor`) is gated by
`economy_flags.REPUTATION_DEMEANOR_ENABLED`; the kill-switch behavior is
asserted at the flag level here (the method itself stays pure/callable).
"""

from __future__ import annotations

from cash_mode.prestige import (
    QUADRANT_BELOVED_LEGEND,
    QUADRANT_DISLIKED_NOBODY,
    QUADRANT_INFAMOUS_VILLAIN,
    QUADRANT_UP_AND_COMER,
    reputation_demeanor_stimulus,
)
from poker.player_psychology import PlayerPsychology

# Low-poise hothead (rattles hard) vs high-poise sage (shrugs it off).
LOW_POISE = {"ego": 0.7, "poise": 0.2, "expressiveness": 0.5, "baseline_aggression": 0.6}
HIGH_POISE = {"ego": 0.4, "poise": 0.95, "expressiveness": 0.4, "baseline_aggression": 0.2}


def _psych(anchors):
    return PlayerPsychology.from_personality_config("Test", {"anchors": anchors})


# --- quadrant → stimulus ----------------------------------------------------


def test_demeanor_stimulus_only_for_high_renown_quadrants():
    assert reputation_demeanor_stimulus(QUADRANT_INFAMOUS_VILLAIN) == "intimidating"
    assert reputation_demeanor_stimulus(QUADRANT_BELOVED_LEGEND) == "reassuring"
    assert reputation_demeanor_stimulus(QUADRANT_UP_AND_COMER) is None
    assert reputation_demeanor_stimulus(QUADRANT_DISLIKED_NOBODY) is None
    assert reputation_demeanor_stimulus("Nonsense") is None


# --- the psychology nudge ---------------------------------------------------


def test_intimidating_lowers_composure():
    p = _psych(LOW_POISE)
    before = p.composure
    p.react_to_table_reputation("intimidating")
    assert p.composure < before  # rattled


def test_intimidating_hits_low_poise_harder_than_high_poise():
    low, high = _psych(LOW_POISE), _psych(HIGH_POISE)
    low_before, high_before = low.composure, high.composure
    low.react_to_table_reputation("intimidating")
    high.react_to_table_reputation("intimidating")
    low_drop = low_before - low.composure
    high_drop = high_before - high.composure
    # The (1-poise) sensitivity filter: the hothead rattles much harder.
    assert low_drop > high_drop


def test_reassuring_lifts_confidence_or_energy():
    p = _psych(LOW_POISE)
    conf_before, energy_before = p.confidence, p.energy
    p.react_to_table_reputation("reassuring")
    assert p.confidence > conf_before or p.energy > energy_before


def test_unknown_stimulus_is_noop():
    p = _psych(LOW_POISE)
    before = (p.composure, p.confidence, p.energy)
    p.react_to_table_reputation("whatever")
    assert (p.composure, p.confidence, p.energy) == before


# --- kill switch ------------------------------------------------------------


def test_flag_defaults_on_and_is_a_simple_bool():
    from cash_mode import economy_flags

    # Default ON (the user asked for it active with a disable switch).
    assert economy_flags.REPUTATION_DEMEANOR_ENABLED is True

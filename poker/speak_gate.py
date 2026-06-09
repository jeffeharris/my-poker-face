"""Unified table-talk gate — one drama→probability model shared by the
in-hand narration gate (`AIPlayerController.compute_narration_gate`) and the
post-hand commentary gate (`CommentaryGenerator._should_speak`).

Both answer the same question — "given how dramatic this moment is and how
chatty/animated this character is, should they speak / react?" — with the same
shape, so the two systems stay in sync and each gets a live admin dial:

    speak_prob   = clamp(weight · drama + CHAT_WEIGHT   · (chattiness − 0.5) + callout_bonus)
    gesture_prob = clamp(weight · drama + ENERGY_WEIGHT · (energy     − 0.5))

`drama` is a 0..1 eventfulness signal. The two paths compute it differently
because they fire at different times:
  - post-hand: hand_score.score_hand / 100 (the whole hand is known)
  - in-hand:   MomentAnalyzer drama level mapped via LEVEL_DRAMA (only the
               in-progress state is known)

`weight` is the live dial (app_settings): higher = more talk/reaction. The
shaping constants below are fixed; tune volume with the dial.
"""

CHAT_WEIGHT = 0.4
ENERGY_WEIGHT = 0.3
MAX_PROB = 0.95

# MomentAnalyzer drama level → 0..1 eventfulness. Routine folds/checks sit low
# so they're quiet (and, on the tiered path, skip the expression LLM call);
# climactic spots sit high so the table still comes alive.
LEVEL_DRAMA = {
    'routine': 0.10,
    'notable': 0.35,
    'high_stakes': 0.65,
    'climactic': 0.90,
}

# Speech bonus when a player was directly addressed/needled recently — they
# get to respond even on an otherwise routine spot (social realism). Applied
# to speech only, not gestures.
CALLOUT_SPEAK_BONUS = 0.45


def level_to_drama(level: str) -> float:
    """Map a MomentAnalyzer drama level to a 0..1 eventfulness signal."""
    return LEVEL_DRAMA.get(level, LEVEL_DRAMA['routine'])


def _clamp(x: float) -> float:
    return max(0.0, min(MAX_PROB, x))


def speak_probability(
    drama: float, chattiness: float, weight: float, *, callout_bonus: float = 0.0
) -> float:
    """Base probability that a character SPEAKS, before any rate-limit /
    personality adjustments the caller layers on top.

    drama: 0..1 eventfulness. chattiness: 0..1 trait. weight: live dial.
    callout_bonus: extra push when the player was just addressed.
    """
    return _clamp(weight * drama + CHAT_WEIGHT * (chattiness - 0.5) + callout_bonus)


def gesture_probability(drama: float, energy: float, weight: float) -> float:
    """Base probability that a character makes a physical GESTURE. Energy-driven
    rather than chattiness-driven, but now drama-gated the same way speech is so
    routine actions don't trigger a wasted *mucks* / *taps chips* beat."""
    return _clamp(weight * drama + ENERGY_WEIGHT * (energy - 0.5))

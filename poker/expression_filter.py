"""
Expression Filtering for Psychology System Phase 2.

Controls how emotions are displayed based on visibility (0.7*expressiveness + 0.3*energy).
Low visibility players show poker face more often; high visibility shows true emotions.

Also provides prompt guidance for dramatic_sequence length and tempo based on visibility/energy.
"""

import random
from typing import Optional


# === Emotion Dampening Map ===
# Maps true emotions to dampened versions as visibility decreases.
# Format: true_emotion -> (medium_visibility_emotion, low_visibility_emotion)
EMOTION_DAMPENING_MAP = {
    # High intensity -> medium -> poker face
    'angry': ('frustrated', 'poker_face'),
    'shocked': ('nervous', 'poker_face'),
    'elated': ('happy', 'poker_face'),
    'smug': ('confident', 'poker_face'),

    # Medium intensity -> thinking -> poker face
    'frustrated': ('thinking', 'poker_face'),
    'nervous': ('thinking', 'poker_face'),
    'happy': ('thinking', 'poker_face'),
    'confident': ('thinking', 'poker_face'),

    # Low intensity emotions stay or go to poker face
    'thinking': ('thinking', 'poker_face'),
    'poker_face': ('poker_face', 'poker_face'),
}

# Visibility thresholds
HIGH_VISIBILITY_THRESHOLD = 0.6
MEDIUM_VISIBILITY_THRESHOLD = 0.3

# Low visibility: probability of showing poker_face vs dampened emotion
LOW_VISIBILITY_POKER_FACE_RATIO = 0.7


def calculate_visibility(expressiveness: float, energy: float) -> float:
    """
    Calculate visibility from expressiveness and energy.

    Visibility determines how much of the player's true emotion shows through.
    High visibility = open book (shows true feelings)
    Low visibility = poker face (hides emotions)

    Args:
        expressiveness: Player's expressiveness anchor (0.0 to 1.0)
        energy: Current energy axis value (0.0 to 1.0)

    Returns:
        Visibility value (0.0 to 1.0)
    """
    return 0.7 * expressiveness + 0.3 * energy


def dampen_emotion(true_emotion: str, visibility: float, use_random: bool = True) -> str:
    """
    Dampen emotion based on visibility level.

    High visibility (>0.6): Show true emotion
    Medium visibility (0.3-0.6): Show dampened emotion
    Low visibility (<0.3): 70% poker_face, 30% dampened emotion

    Args:
        true_emotion: The player's actual emotional state
        visibility: Calculated visibility (0.0 to 1.0)
        use_random: If True, use random selection for low visibility;
                   if False, always return poker_face for low visibility

    Returns:
        The emotion to display (may be dampened or poker_face)
    """
    # Normalize emotion name to lowercase for lookup
    true_emotion = true_emotion.lower()

    # High visibility: show true emotion
    if visibility >= HIGH_VISIBILITY_THRESHOLD:
        return true_emotion

    # Get dampening info (default to poker_face if unknown emotion)
    dampening = EMOTION_DAMPENING_MAP.get(true_emotion, ('poker_face', 'poker_face'))
    medium_emotion, _ = dampening

    # Medium visibility: show dampened emotion
    if visibility >= MEDIUM_VISIBILITY_THRESHOLD:
        return medium_emotion

    # Low visibility: mostly poker_face, occasionally dampened
    if use_random:
        if random.random() < LOW_VISIBILITY_POKER_FACE_RATIO:
            return 'poker_face'
        return medium_emotion

    # Deterministic mode: always poker_face for low visibility
    return 'poker_face'


def get_dramatic_sequence_guidance(visibility: float) -> str:
    """
    Get guidance for dramatic_sequence length based on visibility.

    High visibility players can have full dramatic sequences.
    Low visibility players should be minimal or silent.

    Args:
        visibility: Calculated visibility (0.0 to 1.0)

    Returns:
        Guidance string to include in prompt
    """
    if visibility >= HIGH_VISIBILITY_THRESHOLD:
        return (
            "EXPRESSION: You're animated and readable. "
            "Full dramatic_sequence (2-5 beats). Show your personality."
        )
    elif visibility >= MEDIUM_VISIBILITY_THRESHOLD:
        return (
            "EXPRESSION: You're controlled but present. "
            "Restrained dramatic_sequence (1-2 beats max). Keep it subtle."
        )
    else:
        return (
            "EXPRESSION: You're unreadable. "
            "Minimal or no dramatic_sequence. Poker face. Let your actions speak."
        )


def get_tempo_guidance(energy: float) -> str:
    """
    Get guidance for decision tempo/thinking based on energy level.

    High energy: Quick decisions, brief inner thoughts
    Low energy: Deliberate pace, detailed analysis

    Args:
        energy: Current energy axis value (0.0 to 1.0)

    Returns:
        Guidance string to include in prompt
    """
    if energy >= 0.7:
        return (
            "TEMPO: You're running hot. Quick decision, brief inner_monologue. "
            "Trust your instincts."
        )
    elif energy >= 0.4:
        return (
            "TEMPO: Moderate pace. Normal inner_monologue length. "
            "Balance analysis with feel."
        )
    else:
        return (
            "TEMPO: You're deliberate and measured. Detailed inner_monologue. "
            "Think through the hand carefully."
        )


def get_expression_guidance(
    expressiveness: float,
    energy: float,
    include_tempo: bool = True,
) -> str:
    """
    Get combined expression guidance for prompt injection.

    Combines visibility-based dramatic_sequence guidance with
    energy-based tempo guidance.

    Args:
        expressiveness: Player's expressiveness anchor (0.0 to 1.0)
        energy: Current energy axis value (0.0 to 1.0)
        include_tempo: Whether to include tempo guidance

    Returns:
        Combined guidance string
    """
    visibility = calculate_visibility(expressiveness, energy)

    parts = [get_dramatic_sequence_guidance(visibility)]

    if include_tempo:
        parts.append(get_tempo_guidance(energy))

    return "\n".join(parts)

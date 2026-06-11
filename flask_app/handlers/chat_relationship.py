"""Chat-send → relationship + emotional dispatch.

The Flask chat-send route extracts structured `(tone, intensity,
addressing)` from quick-chat messages and forwards to
`dispatch_chat_relationship_event`. This module maps the tone to a
`RelationshipEvent` via `poker.memory.chat_intent` and fires two
independent effects on each addressed AI:

  1. An *emotional* reaction on the target's own psychology axes
     (confidence/composure/energy), branched by the character's
     disposition — a proud hothead is stung, a charmer is energized, a
     sage shrugs. Lives on the controller, in memory; needs no repo.
  2. A *relationship*-axis update (heat/respect/likability) between the
     two players via `OpponentModelManager.record_event`.

Lives in `handlers/` (not in the route file) so it can be unit-tested
without booting Flask's blueprint + limiter machinery. Same convention
the existing `message_handler.py` follows.
"""

from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# A broadcast (gloat after a win, or a "to the table" jab) lands softer on
# any single player than a message aimed straight at them. Tunable.
BROADCAST_REACTION_SCALE = 0.5


def _perceives_sarcasm(psychology) -> bool:
    """Whether this recipient catches the sarcasm (vs. takes the surface).

    Gated on the ``SARCASM_DETECTION_ENABLED`` flag: when on (default), a
    sarcastic message only reads as sarcasm to recipients who *detect* it (via
    adaptation_bias) — a low-awareness target misses the register and reacts to
    the LITERAL surface (a backhanded compliment lands as sincere). When off,
    every recipient is assumed to perceive the sarcasm (the prior behavior).
    """
    from core import feature_flags

    if not feature_flags.is_enabled("SARCASM_DETECTION_ENABLED"):
        return True
    return psychology._detects_sarcasm()


# Emotional-layer tones: dispatched straight to the psychology axes (they
# move composure/confidence, not relationship axes) via a coarse stimulus.
# These never touch map_tone or the relationship repo.
_EMOTIONAL_TONE_STIMULUS = {
    'intimidate': 'intimidate',
    'dare': 'dare',
    # Post-round reskins of the same two weapons (the spec's mirror):
    'vow': 'intimidate',  # "I'm coming for that stack" → poise rattle
    'cry_luck': 'dare',  # "you got lucky" → ego poke (proud bristles)
}


def _stimulus_for_event(event) -> Optional[str]:
    """Reduce a RelationshipEvent to the coarse social stimulus the
    psychology layer understands ('jab' | 'praise'), or None when the
    event carries no emotional valence for the recipient."""
    from poker.memory.relationship_events import RelationshipEvent

    if event in (RelationshipEvent.TRASH_TALK, RelationshipEvent.TAUNT_POST_WIN):
        return 'jab'
    if event in (
        RelationshipEvent.COMPLIMENT,
        RelationshipEvent.FRIENDLY_BANTER,
        RelationshipEvent.PROPS,
        RelationshipEvent.COMMISERATE,
    ):
        return 'praise'
    return None


def _sarcasm_emotional_stimulus(mode: Optional[str]) -> Optional[str]:
    """Coarse emotional stimulus for a sarcastic message, by mode.

    Overrides the event-derived stimulus so the recipient's *emotional*
    reaction matches the sarcasm reading: a softened (banter) or self-mocking
    message lands as 'praise' (warm), a sharpened backhand as a mild 'jab'.
    None falls back to the event-derived stimulus.
    """
    if mode in ('soften', 'self'):
        return 'praise'
    if mode == 'sharpen':
        return 'jab'
    return None


def _dispatch_emotional_tone(
    game_data: dict,
    sender: str,
    addressing: Optional[List[str]],
    stimulus: str,
    intensity: Optional[str],
) -> None:
    """Fire an emotional-layer weapon (intimidate / dare) on the target(s).

    Moves only the target's psychology axes — no relationship-axis effect, so
    no opponent manager / repo is needed. Explicit targets take the full
    stimulus; a broadcast fans out at the reduced scale. `sarcastic` has no
    meaning here (no surface to read literally), so intensity only scales the
    chill/spicy lever.
    """
    multiplier = 0.5 if intensity == 'chill' else 1.0
    broadcast = not addressing
    targets = _broadcast_targets(game_data, sender) if broadcast else addressing
    scale = BROADCAST_REACTION_SCALE if broadcast else 1.0
    controllers = game_data.get('ai_controllers') or {}
    for target_name in targets:
        if target_name == sender:
            continue
        psychology = getattr(controllers.get(target_name), 'psychology', None)
        if psychology is None:
            continue
        psychology.react_to_social_stimulus(
            stimulus, opponent=sender, multiplier=multiplier * scale
        )


def _apply_social_reactions(
    game_data: dict,
    sender: str,
    addressing: List[str],
    mapping,
    scale: float = 1.0,
    sarcasm_mode: Optional[str] = None,
) -> None:
    """Move each addressed AI's emotional axes in response to the message.

    Independent of the relationship repo: psychology is held in-memory on
    the controller, so this fires even in flows where the opponent-model
    repo isn't wired. Non-AI targets (the human) and self-targets are
    skipped because they have no controller in `ai_controllers`.

    `scale` further dampens the hit — used for broadcasts, where one
    message is split across the whole table. When `sarcasm_mode` is set the
    stimulus is resolved PER TARGET: a recipient who perceives the sarcasm
    reacts to its reading (banter → 'praise', backhand → 'jab'); one who
    misses it reacts to the literal event instead — so the same backhanded
    compliment that needles a sharp reader pleases an oblivious one.
    """
    controllers = game_data.get('ai_controllers') or {}
    for target_name in addressing:
        if target_name == sender:
            continue
        controller = controllers.get(target_name)
        psychology = getattr(controller, 'psychology', None)
        if psychology is None:
            continue
        if sarcasm_mode is not None and _perceives_sarcasm(psychology):
            stimulus = _sarcasm_emotional_stimulus(sarcasm_mode)
        else:
            stimulus = _stimulus_for_event(mapping.event)
        if not stimulus:
            continue
        psychology.react_to_social_stimulus(
            stimulus,
            opponent=sender,
            multiplier=mapping.multiplier * scale,
        )


def _broadcast_targets(game_data: dict, sender: str) -> List[str]:
    """Seated AI player names (excluding the sender) for a table broadcast.

    Intersects the AI controllers with the currently-seated players so a
    gloat/table jab only touches who's actually at the felt, not every AI
    that ever sat down. Falls back to all controllers if the live seating
    isn't readable.
    """
    controllers = game_data.get('ai_controllers') or {}
    names = set(controllers.keys())
    state_machine = game_data.get('state_machine')
    if state_machine is not None:
        try:
            seated = {p.name for p in state_machine.game_state.players}
            names &= seated
        except Exception:
            pass
    names.discard(sender)
    return sorted(names)


def _mirror_override(game_data: dict, target_name: str, tone: str, event, intensity):
    """Resolve the recipient's mirror-side override for this message, or None.

    Two sources, both keyed on the target's social disposition:
      - `sarcastic` register on a tone with a sarcasm mode → the
        disposition-keyed sarcasm transform REPLACES the neutral mirror
        (props → backhand, trash talk → banter), but ONLY if the target
        *perceives* the sarcasm. A recipient who misses it (low
        adaptation_bias) falls through to the literal event's reception — so
        a sarcastic compliment they take at face value genuinely warms them.
        The actor side is left on the sincere event shift either way, same
        asymmetry as the temperament overrides.
      - otherwise → the temperament reshape for needling events.

    Returns None (leave the global mirror table in force) whenever the target
    has no AI controller (the human), no psychology, or neither source
    applies — so existing flows are unchanged.
    """
    controllers = game_data.get('ai_controllers') or {}
    psychology = getattr(controllers.get(target_name), 'psychology', None)
    if psychology is None:
        return None
    disposition = psychology._classify_social_disposition()

    if intensity == 'sarcastic':
        from poker.memory.chat_intent import sarcasm_mode_for_tone
        from poker.memory.relationship_events import sarcasm_mirror_shift

        mode = sarcasm_mode_for_tone(tone)
        if mode is not None and _perceives_sarcasm(psychology):
            return sarcasm_mirror_shift(mode, disposition)
        # mode set but missed → fall through to the LITERAL event reception.

    from poker.memory.relationship_events import temperament_adjusted_mirror_shift

    return temperament_adjusted_mirror_shift(event, disposition)


def _flattery_relationship_event(disposition: str):
    """The relationship event for a flattery outcome, or None for 'unmoved'."""
    from poker.memory.relationship_events import RelationshipEvent

    if disposition == 'vain':
        return RelationshipEvent.FLATTERY_LANDED
    if disposition == 'sees_through':
        return RelationshipEvent.FLATTERY_BACKFIRED
    return None


def _dispatch_flattery(
    game_data: dict,
    sender: str,
    addressing: Optional[List[str]],
    intensity: Optional[str],
) -> None:
    """Handle the `flatter` tone, whose valence flips by the TARGET's vanity.

    Because the outcome (and thus the relationship event) depends on each
    target's disposition, flattery can't ride the fixed tone→event mapping —
    it's resolved per target here. For every addressed AI we fire the emotional
    reaction; for explicit (non-broadcast) targets we also fire the
    disposition-picked relationship event: FLATTERY_LANDED on the vain (they're
    charmed), FLATTERY_BACKFIRED on the perceptive (they catch the ploy and
    think less of you). 'unmoved' targets move nothing. Broadcasts ("flatter
    the table") move only the reaction, at a reduced scale.
    """
    multiplier = 0.5 if intensity == 'chill' else 1.0
    broadcast = not addressing
    targets = _broadcast_targets(game_data, sender) if broadcast else addressing
    scale = BROADCAST_REACTION_SCALE if broadcast else 1.0
    controllers = game_data.get('ai_controllers') or {}

    # Relationship side (explicit targets only) needs the opponent manager.
    opponent_manager = None
    actor_id = None
    hand_id = None
    if not broadcast:
        memory_manager = game_data.get('memory_manager')
        if memory_manager is not None:
            mgr = memory_manager.get_opponent_model_manager()
            if mgr is not None and mgr.has_relationship_repo:
                opponent_manager = mgr
                actor_id = mgr.resolve_player_id(sender)
                hand_id = getattr(memory_manager, 'hand_count', None) or None

    for target_name in targets:
        if target_name == sender:
            continue
        controller = controllers.get(target_name)
        psychology = getattr(controller, 'psychology', None)
        if psychology is None:
            continue
        # (1) Emotional reaction (classifies vanity internally).
        psychology.react_to_social_stimulus(
            'flatter', opponent=sender, multiplier=multiplier * scale
        )
        # (2) Relationship valence flip — explicit targets only.
        if opponent_manager is None:
            continue
        rel_event = _flattery_relationship_event(psychology._classify_flattery_disposition())
        if rel_event is None:
            continue
        target_id = opponent_manager.resolve_player_id(target_name)
        if not actor_id or not target_id or actor_id == target_id:
            continue
        opponent_manager.record_event(
            actor_id=actor_id,
            target_id=target_id,
            event=rel_event,
            context_multiplier=multiplier,
            narrative=f"{sender} → {target_name}: flatter",
            hand_id=hand_id,
        )


def dispatch_chat_relationship_event(
    game_data: dict,
    sender: str,
    addressing: Optional[List[str]],
    tone: Optional[str],
    intensity: Optional[str],
) -> None:
    """Map quick-chat tone to a RelationshipEvent and fire its effects.

    Documented no-op when:
      - tone is missing / not in the recognized vocabulary (free-form
        chat — no structured intent to dispatch).

    `flatter` is handled on a dedicated path (`_dispatch_flattery`) because
    its valence flips by the target's vanity; everything below is the fixed
    tone→event path.

    Two addressing modes:
      - Explicit target(s): both the emotional reaction (1) AND the
        bilateral relationship-axis update (2) fire.
      - No target but a tone is present (a "to the table" jab or a gloat
        after a win — the only ways to reach here without an addressee):
        the emotional reaction fans out to every seated AI at a reduced
        scale, and the relationship update is intentionally skipped —
        ambiguous pairwise attribution shouldn't move heat between
        specific players.

    The emotional reaction (1) needs only the in-memory controllers. The
    relationship-axis update (2) additionally needs the opponent manager +
    repo, and is skipped (with the reaction still applied) when:
      - memory_manager isn't on game_data (older flows, replay paths).
      - relationship_repo isn't wired on the manager (in-memory tests).

    Self-addressed messages (actor_id == target_id) are skipped — the
    route is meant to be human-to-AI but a misrouted self-target
    shouldn't write any state.

    Wraps the dispatch in a single try/except so a failure here can't
    block the message delivery the caller has already confirmed. Both
    effects are side-effects; the chat send is the primary action.
    """
    if not tone:
        return

    try:
        # Emotional-layer tones (intimidate/dare) move the target's play, not
        # the relationship axes — dispatch them straight to psychology and stop.
        emotional_stimulus = _EMOTIONAL_TONE_STIMULUS.get(tone)
        if emotional_stimulus is not None:
            _dispatch_emotional_tone(game_data, sender, addressing, emotional_stimulus, intensity)
            return

        # Flattery is disposition-dependent (valence flips per target), so it
        # can't ride the fixed tone→event mapping — handle it on its own path.
        if tone == 'flatter':
            _dispatch_flattery(game_data, sender, addressing, intensity)
            return

        from poker.memory.chat_intent import map_tone, sarcasm_mode_for_tone

        mapping = map_tone(tone, intensity)
        if mapping is None:
            return

        # Sarcastic register: the reaction is resolved per target inside
        # _apply_social_reactions — a recipient who perceives the sarcasm reacts
        # to its reading, one who misses it reacts to the literal event.
        sarcasm_mode = sarcasm_mode_for_tone(tone) if intensity == 'sarcastic' else None

        # Broadcast: tone with no specific target. Fan the emotional
        # reaction out to the seated AIs at a reduced scale; leave the
        # relationship layer untouched (no pairwise target to attribute to).
        if not addressing:
            _apply_social_reactions(
                game_data,
                sender,
                _broadcast_targets(game_data, sender),
                mapping,
                scale=BROADCAST_REACTION_SCALE,
                sarcasm_mode=sarcasm_mode,
            )
            return

        # (1) Emotional reaction — in-memory, repo-independent.
        _apply_social_reactions(game_data, sender, addressing, mapping, sarcasm_mode=sarcasm_mode)

        # (2) Bilateral relationship-axis update — needs the opponent manager.
        memory_manager = game_data.get('memory_manager')
        if memory_manager is None:
            return
        opponent_manager = memory_manager.get_opponent_model_manager()
        if opponent_manager is None or not opponent_manager.has_relationship_repo:
            return

        # Pull the current hand_number so the bilateral update can
        # attach a MemorableHand sidecar (record_event silently skips
        # the memorable-hand step when hand_id is None — that path is
        # for replay / out-of-band tests). Best-effort: missing
        # hand_count just degrades to "axes move but no narrative
        # gets surfaced in the debug view," which is the current
        # behavior anyway.
        hand_id = getattr(memory_manager, 'hand_count', None) or None

        actor_id = opponent_manager.resolve_player_id(sender)
        for target_name in addressing:
            target_id = opponent_manager.resolve_player_id(target_name)
            if not actor_id or not target_id or actor_id == target_id:
                continue
            opponent_manager.record_event(
                actor_id=actor_id,
                target_id=target_id,
                event=mapping.event,
                context_multiplier=mapping.multiplier,
                narrative=f"{sender} → {target_name}: {tone}",
                hand_id=hand_id,
                mirror_shift_override=_mirror_override(
                    game_data, target_name, tone, mapping.event, intensity
                ),
            )
    except Exception:
        logger.exception("[chat] relationship dispatch failed")

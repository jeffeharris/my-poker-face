"""Player journey narrative — turn a player's hand history into a story.

Hierarchical, the way the user framed it: per-hand beats roll up into a session
recap, sessions roll up into the circuit/journey arc — "the story of the
player's ups and downs through the circuit," player as the central character.

Design principle (hard-won this session, see docs/experiments/EXP_008):
**deterministic spine, LLM only for voice.** Every FACT in the story comes from
the deterministic hand narrators (`narrate_key_moments`) and from real
`hand_history` numbers — so the story can never hallucinate who won, what they
held, or the amounts (the failure mode that sank the beat-as-LLM-input idea).
The LLM is an optional prose layer ON TOP of already-true facts, and it's
fail-soft: if it errors, you still get the factual deterministic story.

This module is pure logic over `RecordedHand` objects — no DB, no Flask. The
caller loads hands (e.g. via `hand_history_repository.load_hand_history` +
`RecordedHand.from_dict`) and hands them in.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..hand_narrator import narrate_key_moments
from .hand_history import RecordedHand

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deterministic facts (zero hallucination)
# ---------------------------------------------------------------------------
def _player_in(hand: RecordedHand, player_name: str) -> bool:
    return any(p.name == player_name for p in hand.players)


def _player_info(hand: RecordedHand, player_name: str):
    """The player's PlayerHandInfo for this hand, or None."""
    return next((p for p in hand.players if p.name == player_name), None)


def _player_net(hand: RecordedHand, player_name: str) -> int:
    """Chips the player won minus chips they put in this hand."""
    won = sum(w.amount_won for w in hand.winners if w.name == player_name)
    contributed = hand.get_player_contributions().get(player_name, 0)
    return won - contributed


def hand_beat(
    hand: RecordedHand, player_name: str, big_blind: Optional[int] = None
) -> Optional[str]:
    """A factual one-line beat for a single hand, or None for a routine hand
    (preflop fold / tiny pot) that doesn't belong in the story."""
    return narrate_key_moments(hand, player_name, big_blind)


def session_story(
    hands: List[RecordedHand],
    player_name: str,
    *,
    big_blind: Optional[int] = None,
) -> Dict[str, Any]:
    """Roll a session's hands up into a factual recap for one player.

    Returns a dict with deterministic ``stats``, the notable per-hand ``beats``
    (hand_number + text), and a plain-prose ``summary`` line. All facts; no LLM.
    """
    mine = [h for h in hands if _player_in(hand=h, player_name=player_name)]
    beats: List[Dict[str, Any]] = []
    net = 0
    won = 0
    biggest_pot_won = 0
    for h in mine:
        net += _player_net(h, player_name)
        if any(w.name == player_name for w in h.winners):
            won += 1
            biggest_pot_won = max(biggest_pot_won, h.pot_size)
        b = hand_beat(h, player_name, big_blind)
        if b:
            beats.append({"hand_number": h.hand_number, "text": b})

    first_info = _player_info(mine[0], player_name) if mine else None
    start_stack = first_info.starting_stack if first_info else None
    end_stack = None
    for h in reversed(mine):
        info = _player_info(h, player_name)
        if info is not None and info.final_stack is not None:
            end_stack = info.final_stack
            break

    stats = {
        "hands_played": len(mine),
        "hands_won": won,
        "net_chips": net,
        "biggest_pot_won": biggest_pot_won,
        "start_stack": start_stack,
        "end_stack": end_stack,
    }
    summary = _deterministic_session_summary(player_name, stats)
    return {"player": player_name, "stats": stats, "beats": beats, "summary": summary}


def _fmt_chips(n: Optional[int]) -> str:
    if n is None:
        return "?"
    sign = "+" if n > 0 else ""
    return f"{sign}{n:,}"


def _deterministic_session_summary(player_name: str, stats: Dict[str, Any]) -> str:
    played = stats["hands_played"]
    if played == 0:
        return f"{player_name} sat out this session."
    arc = "up" if stats["net_chips"] > 0 else "down" if stats["net_chips"] < 0 else "even"
    parts = [
        f"{player_name} played {played} hand{'s' if played != 1 else ''}, "
        f"won {stats['hands_won']}, and finished {arc} "
        f"{_fmt_chips(stats['net_chips'])} chips for the session."
    ]
    if stats["start_stack"] is not None and stats["end_stack"] is not None:
        parts.append(f"Stack: {stats['start_stack']:,} → {stats['end_stack']:,}.")
    if stats["biggest_pot_won"]:
        parts.append(f"Biggest pot taken down: {stats['biggest_pot_won']:,}.")
    return " ".join(parts)


def journey_arc_facts(session_stories: List[Dict[str, Any]], player_name: str) -> Dict[str, Any]:
    """Roll sessions up into the overall journey — deterministic stats only."""
    total_net = sum(s["stats"]["net_chips"] for s in session_stories)
    total_hands = sum(s["stats"]["hands_played"] for s in session_stories)
    total_won = sum(s["stats"]["hands_won"] for s in session_stories)
    winning_sessions = sum(1 for s in session_stories if s["stats"]["net_chips"] > 0)
    peak = max((s["stats"]["end_stack"] or 0) for s in session_stories) if session_stories else 0
    return {
        "sessions": len(session_stories),
        "winning_sessions": winning_sessions,
        "total_hands": total_hands,
        "total_hands_won": total_won,
        "total_net_chips": total_net,
        "peak_stack": peak,
    }


# ---------------------------------------------------------------------------
# Optional LLM voice (prose ON TOP of the deterministic facts; fail-soft)
# ---------------------------------------------------------------------------
_VOICE_SYSTEM = (
    "You are a sports-style narrator telling the story of a poker player's "
    "journey, with {hero} as the central character. You are given FACTS (a "
    "factual recap). Write {length} of grounded narrative — the arc of their "
    "ups and downs. Use ONLY the facts given: never invent hands, amounts, or "
    "outcomes. No clichés, no cheese, no hype — just an honest, readable story "
    "beat. Plain prose, no headers or lists."
)


def voice_over(facts: str, *, hero: str = "the player", length: str = "2-4 sentences") -> str:
    """Wrap deterministic facts in narrative prose via the cheap Fast tier.

    Fail-soft: returns ``facts`` unchanged on any error or empty input. The
    facts are the source of truth; this only changes the telling.
    """
    facts = (facts or "").strip()
    if not facts:
        return ""
    try:
        from core.llm import CallType, LLMClient, settings as llm_settings
        from core.llm.config import FAST_LLM_TIMEOUT_SECONDS

        client = LLMClient(
            provider=llm_settings.get_fast_provider(),
            model=llm_settings.get_fast_model(),
            reasoning_effort="minimal",
            default_timeout=FAST_LLM_TIMEOUT_SECONDS,
        )
        resp = client.complete(
            messages=[
                {"role": "system", "content": _VOICE_SYSTEM.format(hero=hero, length=length)},
                {"role": "user", "content": f"FACTS:\n{facts}\n\nTell the story."},
            ],
            json_format=False,
            max_tokens=300,
            call_type=CallType.HAND_NARRATIVE,
            prompt_template="journey_voice",
        )
        text = (resp.content or "").strip()
        return text or facts
    except Exception as e:  # never let voice break the story
        logger.warning("journey voice_over failed, using deterministic facts: %s", e)
        return facts

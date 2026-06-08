"""Player journey narrative — turn a player's hand history into a story.

Hierarchical, the way the user framed it: per-hand beats roll up into a session
recap, sessions roll up into the circuit/journey arc — "the story of the
player's ups and downs through the circuit," player as the central character.

Design principle (hard-won, see docs/experiments/EXP_008):
**deterministic spine, LLM only for voice.** Facts come from the deterministic
hand narrators (`narrate_key_moments`) for the per-hand beats, and from the
**cash-session ledger** for the money (buy-in vs. take-home). The per-hand chip
flow is NOT summed for session P&L — that double-counts blinds/uncalled bets and
can even flip the sign; the authoritative result lives in `cash_sessions`. The
LLM is an optional prose layer ON TOP of those true facts, and it's fail-soft.

Pure logic over `RecordedHand` objects + ledger values — no DB, no Flask.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..hand_narrator import narrate_key_moments
from .hand_history import RecordedHand
from .hand_score import top_hands

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deterministic hand-derived facts (counts + beats — all reliable from hands)
# ---------------------------------------------------------------------------
def _player_in(hand: RecordedHand, player_name: str) -> bool:
    return any(p.name == player_name for p in hand.players)


def hand_beat(
    hand: RecordedHand, player_name: str, big_blind: Optional[int] = None
) -> Optional[str]:
    """A factual one-line beat for a single hand, or None for a routine hand."""
    return narrate_key_moments(hand, player_name, big_blind)


def _spectator_beat(hand: RecordedHand) -> str:
    """A neutral one-liner for a hand the hero wasn't central to (folded early)
    but that still mattered at the table — they watched it happen."""
    if hand.winners:
        w = hand.winners[0]
        where = " at showdown" if hand.was_showdown else ""
        with_hand = f" with {w.hand_name}" if (hand.was_showdown and w.hand_name) else ""
        return f"You folded; {w.name} took ${hand.pot_size:,}{where}{with_hand}"
    return f"A ${hand.pot_size:,} pot played out"


def session_facts(
    hands: List[RecordedHand],
    player_name: str,
    *,
    big_blind: Optional[int] = None,
    equity_by_hand: Optional[Dict[int, Any]] = None,
    top_n: int = 5,
) -> Dict[str, Any]:
    """Hand-derived facts for one player's session: counts + the most DRAMATIC
    hands (ranked by `hand_score`, not every showdown), with a one-line
    headline of WHY each mattered (pot size, equity swing, cooler, all-in…).

    Pass `big_blind` (improves the pot-size signal) and `equity_by_hand`
    (hand_number → HandEquityHistory; enables swing/lead-change/suckout signals).
    Both optional — scoring degrades gracefully without them.

    NOTE: deliberately does NOT compute session net — per-hand chip flow isn't a
    reliable session P&L (blinds, uncalled returns, rebuys). The caller supplies
    the authoritative net from the cash-session ledger (see `cash_pnl`).
    """
    mine = [h for h in hands if _player_in(h, player_name)]
    won = [h for h in mine if any(w.name == player_name for w in h.winners)]
    biggest = max((h.pot_size for h in won), default=0)

    ranked = top_hands(
        mine,
        player_name,
        big_blind=big_blind,
        equity_by_hand=equity_by_hand,
        limit=top_n,
        min_score=1,
    )
    beats: List[Dict[str, Any]] = []
    for h, sc in ranked:
        # Narrate in dollars (readable); the BB is for SCORING, and the pot's BB
        # size already rides in the headline. When the hero wasn't central
        # (folded out of a hand that was dramatic at the table), fall back to a
        # spectator beat instead of a bare "notable hand".
        text = hand_beat(h, player_name) or _spectator_beat(h)
        beats.append(
            {
                "hand_number": h.hand_number,
                "text": text,
                "headline": sc.headline,
                "score": sc.score,
            }
        )
    return {
        "hands_played": len(mine),
        "hands_won": len(won),
        "biggest_pot_won": biggest,
        "beats": beats,
    }


# ---------------------------------------------------------------------------
# Authoritative money — from the cash-session ledger, not the hands
# ---------------------------------------------------------------------------
def cash_pnl(
    *,
    total_buy_in: Optional[int],
    sponsor_principal: Optional[int],
    player_take_home: Optional[int],
    ended_at: Any,
) -> Optional[int]:
    """The player's session net from the ledger: take-home minus their OWN
    buy-in (total buy-in less any sponsor principal). None while the session is
    still in progress (no take-home yet)."""
    if ended_at is None or player_take_home is None:
        return None
    return int(player_take_home) - own_buy_in(total_buy_in, sponsor_principal)


def own_buy_in(total_buy_in: Optional[int], sponsor_principal: Optional[int]) -> int:
    # Floor at 0: in a STAKED session the sponsor funds the seat, so
    # `total_buy_in - sponsor_principal` can go negative. The player risked none
    # of their own chips, so their own buy-in is 0 — never negative. (Staked P&L
    # is thus the player's own-pocket net; it doesn't model the staker's split.)
    return max(0, (total_buy_in or 0) - (sponsor_principal or 0))


def summarize_session(
    player_name: str,
    *,
    hands_played: int,
    hands_won: int,
    biggest_pot_won: int = 0,
    net: Optional[int] = None,
    buy_in: Optional[int] = None,
    take_home: Optional[int] = None,
    stake_label: Optional[str] = None,
) -> str:
    """Plain-prose session recap. Uses the LEDGER net when available; an
    in-progress / ledger-less session just states the action (no up/down claim
    it can't back up)."""
    hp = f"{hands_played} hand{'s' if hands_played != 1 else ''}"
    at = f" at {stake_label}" if stake_label else ""
    biggest = f" Biggest pot: {biggest_pot_won:,}." if biggest_pot_won else ""

    if net is None:
        return f"{player_name} played {hp}{at}, won {hands_won}.{biggest}".rstrip()

    arc = "up" if net > 0 else "down" if net < 0 else "even"
    if buy_in is not None and take_home is not None:
        return (
            f"{player_name} bought in for {buy_in:,}{at}, played {hp} "
            f"(won {hands_won}), and walked away with {take_home:,} — "
            f"{arc} {net:+,} for the session.{biggest}"
        )
    return f"{player_name} played {hp}{at}, won {hands_won} — {arc} {net:+,}.{biggest}"


def journey_arc_facts(session_stats: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Roll session stat dicts up into the overall arc. Net is summed only over
    ENDED sessions (those with a ledger net); in-progress ones don't count."""
    nets: List[int] = [int(s["net_chips"]) for s in session_stats if s.get("net_chips") is not None]
    return {
        "sessions": len(session_stats),
        "ended_sessions": len(nets),
        "winning_sessions": sum(1 for n in nets if n > 0),
        "total_hands": sum(s.get("hands_played") or 0 for s in session_stats),
        "total_hands_won": sum(s.get("hands_won") or 0 for s in session_stats),
        "total_net_chips": sum(nets),
        "biggest_pot": max((s.get("biggest_pot_won") or 0 for s in session_stats), default=0),
    }


def session_facts_text(summary: str, beats: List[Dict[str, Any]]) -> str:
    """Flatten a session's recap + ranked beats into the grounded input for
    voice_over. Each beat carries its drama headline (pot size, swing, cooler…)
    so the narrator can lean on WHY a hand mattered."""
    lines = [summary]
    for b in beats:
        headline = b.get("headline")
        lines.append(f"{b['text']} [{headline}]" if headline else b["text"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Optional LLM voice (prose ON TOP of the deterministic facts; fail-soft)
# ---------------------------------------------------------------------------
_VOICE_SYSTEM = (
    "You are a sports-style narrator telling the story of a poker player's "
    "journey, with {hero} as the central character. You are given FACTS (a "
    "factual recap). Write {length} of grounded narrative — the arc of their "
    "ups and downs. Use ONLY the facts given: never invent hands, amounts, "
    "outcomes, OR context — no places, no extra events, no backstory, nothing "
    "like 'away from the table'. Do not reinterpret a pot as anything other "
    "than what the facts say. No clichés, no cheese, no hype — an honest, "
    "readable story beat. Plain prose, no headers or lists."
)


def voice_over(facts: str, *, hero: str = "the player", length: str = "2-4 sentences") -> str:
    """Wrap deterministic facts in narrative prose via the stronger Assistant tier.

    The story is a deliberate, on-demand read (a button press, not an in-game
    latency path), and the cheap Fast tier was caught embellishing dollar
    amounts that weren't in the facts. The Assistant tier (a reasoning model)
    stays faithful to the numbers, which matters here.

    Fail-soft: returns ``facts`` unchanged on any error or empty input. The
    facts are the source of truth; this only changes the telling.
    """
    facts = (facts or "").strip()
    if not facts:
        return ""
    try:
        from core.llm import CallType, LLMClient, settings as llm_settings

        client = LLMClient(
            provider=llm_settings.get_assistant_provider(),
            model=llm_settings.get_assistant_model(),
            default_timeout=30.0,
        )
        resp = client.complete(
            messages=[
                {"role": "system", "content": _VOICE_SYSTEM.format(hero=hero, length=length)},
                {"role": "user", "content": f"FACTS:\n{facts}\n\nTell the story."},
            ],
            json_format=False,
            max_tokens=400,
            call_type=CallType.HAND_NARRATIVE,
            prompt_template="journey_voice",
        )
        text = (resp.content or "").strip()
        return text or facts
    except Exception as e:  # never let voice break the story
        logger.warning("journey voice_over failed, using deterministic facts: %s", e)
        return facts

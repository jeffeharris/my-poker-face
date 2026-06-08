"""Score a poker hand for narrative significance ("drama").

Pure functions over a ``RecordedHand`` (+ optional ``HandEquityHistory``). The
journey uses this to RANK a session's hands and surface the most notable ones,
rather than dumping every showdown.

The score is a weighted blend of orthogonal signals — each normalized to 0..1,
then weighted (see ``WEIGHTS``) and summed to a 0..100 total:

  - magnitude    pot size relative to the blinds (how big the pot was)
  - commitment   pot relative to the stacks at risk (how much was on the line)
  - equity_swing the biggest momentum shift across streets (suckout / bad beat)
  - closeness    how strong the LOSING showdown hand was (the cooler factor —
                 a monster losing to a bigger monster is the drama)
  - bigness      absolute strength of the winning hand (quads, straight flush)
  - all_in       how many players put it all on the line

Weights are module constants on purpose: this is a first cut and they're meant
to be tuned against real sessions. Nothing here touches a DB or the network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# eval7 is optional at import time — the closeness/bigness components degrade to
# 0 if it (or a hand's hole cards) isn't available, rather than failing.
try:
    import eval7

    _EVAL7 = True
except Exception:  # pragma: no cover - eval7 is a hard dep in practice
    _EVAL7 = False


WEIGHTS: Dict[str, float] = {
    "magnitude": 25.0,  # pot size relative to the blinds
    "commitment": 8.0,  # pot relative to the table's stacks
    "equity_swing": 16.0,  # biggest momentum shift across streets
    "closeness": 12.0,  # how strong the losing showdown hand was (cooler)
    "bigness": 7.0,  # absolute strength of the winning hand
    "all_in": 8.0,  # how many players were all-in
    "lead_changes": 8.0,  # how many times the equity favorite flipped
    "hero_risk": 16.0,  # how much of the HERO's own stack was on the line
}

# eval7.handtype() strings → ordinal 1..9 (worst..best).
_HANDTYPE_ORDINAL: Dict[str, int] = {
    "High Card": 1,
    "Pair": 2,
    "Two Pair": 3,
    "Trips": 4,
    "Straight": 5,
    "Flush": 6,
    "Full House": 7,
    "Quads": 8,
    "Straight Flush": 9,
}

_SUIT = {"♦": "d", "♠": "s", "♣": "c", "♥": "h", "d": "d", "s": "s", "c": "c", "h": "h"}


@dataclass(frozen=True)
class HandScore:
    """A hand's drama score with its component breakdown and human-readable tags."""

    score: int  # 0..100 composite
    components: Dict[str, float] = field(default_factory=dict)  # factor -> 0..1
    tags: List[str] = field(default_factory=list)  # ['cooler', '3-way all-in', ...]
    headline: str = ""  # one-line "why this hand mattered"


# ---------------------------------------------------------------------------
# Card parsing + showdown evaluation
# ---------------------------------------------------------------------------
def _to_eval7(card: str) -> Optional[eval7.Card]:
    """Parse a stored card string ('10♣', 'A♠', 'Kd') into an eval7.Card."""
    if not _EVAL7 or not card:
        return None
    s = card.strip()
    suit_ch = s[-1]
    rank = s[:-1]
    if rank == "10":
        rank = "T"
    suit = _SUIT.get(suit_ch)
    if suit is None or not rank:
        return None
    try:
        return eval7.Card(f"{rank}{suit}")
    except Exception:
        return None


def _showdown_strengths(hand) -> List[Tuple[str, int, int]]:
    """For each player who reached showdown with known hole cards, the
    (name, ordinal 1..9, raw eval7 value). Sorted strongest-first."""
    if not _EVAL7 or not hand.was_showdown or not hand.hole_cards:
        return []
    board = [c for c in (_to_eval7(x) for x in (hand.community_cards or [])) if c]
    folded = {a.player_name for a in hand.actions if a.action == "fold"}
    out: List[Tuple[str, int, int]] = []
    for name, cards in hand.hole_cards.items():
        if name in folded:
            continue
        hole = [c for c in (_to_eval7(x) for x in cards) if c]
        if len(hole) + len(board) < 5:
            continue
        try:
            value = eval7.evaluate(hole + board)
        except Exception:
            continue
        ordinal = _HANDTYPE_ORDINAL.get(eval7.handtype(value), 1)
        out.append((name, ordinal, value))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Component helpers (each returns 0..1)
# ---------------------------------------------------------------------------
def _all_in_count(hand) -> int:
    return len({a.player_name for a in hand.actions if a.action == "all_in"})


def _avg_starting_stack(hand) -> float:
    stacks = [p.starting_stack for p in hand.players if p.starting_stack]
    return (sum(stacks) / len(stacks)) if stacks else 0.0


def _magnitude(hand, big_blind: Optional[int]) -> Tuple[float, Optional[int]]:
    """Pot relative to the blinds (log-scaled): 10bb→0.33, 100bb→0.67, 1000bb→1.0.
    Falls back to pot / average starting stack when the big blind is unknown.
    Returns (component, pot_in_bb_or_None)."""
    pot = hand.pot_size or 0
    if big_blind and big_blind > 0:
        pot_bb = pot / big_blind
        return min(1.0, math.log10(max(pot_bb, 1.0)) / 3.0), int(round(pot_bb))
    avg = _avg_starting_stack(hand)
    return (min(1.0, pot / avg) if avg else 0.0), None


def _commitment(hand) -> float:
    """Pot relative to a full double-stack of the deepest effective stack —
    i.e. how much of the money on the table was in the middle."""
    stacks = sorted((p.starting_stack for p in hand.players if p.starting_stack), reverse=True)
    if len(stacks) < 2:
        return 0.0
    effective = stacks[1]  # the second-deepest caps a heads-up confrontation
    return min(1.0, (hand.pot_size or 0) / (2.0 * effective)) if effective else 0.0


def _all_in_component(n: int) -> float:
    return {0: 0.0, 1: 0.4, 2: 0.7}.get(n, 1.0)


def _lead_changes(equity) -> int:
    """How many times the equity favorite flipped between streets — a hand that
    see-sawed back and forth is more dramatic than a flat one."""
    if equity is None:
        return 0
    leaders: List[str] = []
    for street in ("PRE_FLOP", "FLOP", "TURN", "RIVER"):
        try:
            eqs = equity.get_active_street_equities(street)
        except Exception:
            eqs = {}
        if eqs:
            leaders.append(max(eqs.items(), key=lambda kv: kv[1])[0])
    return sum(1 for i in range(1, len(leaders)) if leaders[i] != leaders[i - 1])


def _hero_risk(hand, player_name: str) -> float:
    """Fraction of the hero's OWN starting stack they put in the pot (1.0 = they
    got it all in). 'amount' on each action is the chips that action added."""
    p = next((p for p in hand.players if p.name == player_name), None)
    if not p or not p.starting_stack:
        return 0.0
    risked = sum(a.amount for a in hand.actions if a.player_name == player_name and a.amount > 0)
    return min(1.0, risked / p.starting_stack)


# ---------------------------------------------------------------------------
# The scorer
# ---------------------------------------------------------------------------
def score_hand(
    hand,
    player_name: str,
    *,
    big_blind: Optional[int] = None,
    equity=None,  # Optional[HandEquityHistory]
) -> HandScore:
    """Score one hand's narrative significance from the player's perspective."""
    comp: Dict[str, float] = {}
    tags: List[str] = []

    mag, pot_bb = _magnitude(hand, big_blind)
    comp["magnitude"] = mag
    comp["commitment"] = _commitment(hand)

    # Equity swing + suckout / bad-beat framing (needs equity history).
    swing = 0.0
    if equity is not None:
        try:
            for name in equity.get_player_names():
                s = equity.get_max_equity_swing(name)
                if s:
                    swing = max(swing, abs(s[2]))
            if equity.was_behind_then_won(player_name):
                tags.append("suckout")
            if equity.was_ahead_then_lost(player_name):
                tags.append("bad beat")
        except Exception:
            swing = 0.0
    comp["equity_swing"] = swing

    # Closeness / cooler: how strong the LOSING showdown hand was.
    strengths = _showdown_strengths(hand)
    closeness = 0.0
    bigness = 0.0
    if strengths:
        bigness = (strengths[0][1] - 1) / 8.0  # winner's category, 0..1
        if len(strengths) >= 2:
            runner_ord = strengths[1][1]
            closeness = (runner_ord - 1) / 8.0
            # Both holding a real hand (trips+) decided narrowly = a cooler.
            if runner_ord >= 4 and strengths[0][1] >= 4:
                tags.append("cooler")
    elif hand.winners and hand.winners[0].hand_rank:
        bigness = min(1.0, (hand.winners[0].hand_rank or 0) / 9.0)
    comp["closeness"] = closeness
    comp["bigness"] = bigness

    n_all_in = _all_in_count(hand)
    comp["all_in"] = _all_in_component(n_all_in)

    flips = _lead_changes(equity)
    comp["lead_changes"] = {0: 0.0, 1: 0.5}.get(flips, 1.0)

    hero_risk = _hero_risk(hand, player_name)
    comp["hero_risk"] = hero_risk

    total = sum(WEIGHTS[k] * comp.get(k, 0.0) for k in WEIGHTS)
    score = int(round(min(100.0, total)))

    # Tags + headline.
    if pot_bb is not None and pot_bb >= 20:
        tags.insert(0, f"{pot_bb}bb pot")
    if n_all_in >= 2:
        tags.append(f"{n_all_in}-way all-in")
    elif n_all_in == 1:
        tags.append("all-in")
    elif hero_risk >= 0.95:  # got it in without a recorded all-in marker
        tags.append("stack on the line")
    if flips >= 2:
        tags.append(f"lead changed {flips}x")
    if strengths and strengths[0][1] >= 7:  # full house or better won
        won_name = hand.winners[0].hand_name if hand.winners else None
        if won_name:
            tags.append(won_name.lower())

    # De-dup while preserving order.
    seen: set = set()
    tags = [t for t in tags if not (t in seen or seen.add(t))]

    return HandScore(score=score, components=comp, tags=tags, headline=" · ".join(tags))


def top_hands(
    hands: List[Any],
    player_name: str,
    *,
    big_blind: Optional[int] = None,
    equity_by_hand: Optional[Dict[int, Any]] = None,
    limit: int = 3,
    min_score: int = 25,
) -> List[Tuple[Any, HandScore]]:
    """Rank a player's hands by drama score; return the top ``limit`` above
    ``min_score``, strongest first."""
    equity_by_hand = equity_by_hand or {}
    scored = [
        (
            h,
            score_hand(
                h, player_name, big_blind=big_blind, equity=equity_by_hand.get(h.hand_number)
            ),
        )
        for h in hands
        if any(p.name == player_name for p in h.players)
    ]
    scored = [pair for pair in scored if pair[1].score >= min_score]
    scored.sort(key=lambda pair: pair[1].score, reverse=True)
    return scored[:limit]

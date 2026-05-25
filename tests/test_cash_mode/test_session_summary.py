"""Tests for `cash_mode.session_summary.summarize_cash_session`.

The helper is the only thing the leave route delegates to for stats,
so the goal here is to lock down the metric math (VPIP, PFR, aggression,
biggest pot, play-style label) against synthetic hand-history rows.

Hand-history shape matches `RecordedHand.to_dict()` — the dicts that
`HandHistoryRepository.load_hand_history` returns.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

import pytest

from cash_mode.session_summary import summarize_cash_session

HUMAN = "Hero"


def _player(name: str, is_human: bool = False) -> Dict[str, Any]:
    return {
        "name": name,
        "starting_stack": 1000,
        "position": "BTN",
        "is_human": is_human,
    }


def _action(name: str, action: str, amount: int = 0, phase: str = "PRE_FLOP") -> Dict[str, Any]:
    return {
        "player_name": name,
        "action": action,
        "amount": amount,
        "phase": phase,
        "pot_after": 0,
        "timestamp": datetime.utcnow().isoformat(),
    }


def _hand(
    *,
    hand_number: int,
    actions: List[Dict[str, Any]],
    winners: List[str],
    pot_size: int = 100,
    players: List[str] = None,
) -> Dict[str, Any]:
    players = players or [HUMAN, "Villain"]
    return {
        "game_id": "g1",
        "hand_number": hand_number,
        "timestamp": datetime.utcnow().isoformat(),
        "players": [_player(p, is_human=(p == HUMAN)) for p in players],
        "hole_cards": {},
        "community_cards": [],
        "actions": actions,
        "winners": [
            {"name": n, "amount_won": pot_size, "hand_name": None, "hand_rank": None}
            for n in winners
        ],
        "pot_size": pot_size,
        "was_showdown": False,
    }


def test_summary_empty_session():
    """No hands played — falls back to provided hand_count, all stats zero."""
    now = datetime(2026, 5, 19, 12, 0, 0)
    result = summarize_cash_session(
        hands=[],
        human_name=HUMAN,
        buy_in=500,
        cash_out=500,
        started_at=now - timedelta(seconds=30),
        now=now,
        fallback_hand_count=0,
    )
    assert result["net_pnl"] == 0
    assert result["hands_played"] == 0
    assert result["hands_won"] == 0
    assert result["biggest_pot_won"] == 0
    assert result["vpip_pct"] == 0.0
    assert result["pfr_pct"] == 0.0
    assert result["play_style"] == "Not enough hands"
    assert result["duration_seconds"] == 30


def test_summary_net_pnl_positive():
    result = summarize_cash_session(
        hands=[],
        human_name=HUMAN,
        buy_in=500,
        cash_out=1200,
        started_at=None,
        now=datetime.utcnow(),
        fallback_hand_count=5,
    )
    assert result["net_pnl"] == 700
    assert result["hands_played"] == 5  # fallback
    assert result["duration_seconds"] == 0  # no started_at


def test_vpip_excludes_blinds_and_folds():
    """Posting blinds + folding doesn't count as VPIP."""
    hands = [
        # Hand 1: Hero posts blind then folds — NOT VPIP
        _hand(
            hand_number=1,
            actions=[
                _action(HUMAN, "post_blind", 10, "PRE_FLOP"),
                _action(HUMAN, "fold", 0, "PRE_FLOP"),
            ],
            winners=["Villain"],
        ),
        # Hand 2: Hero calls preflop — VPIP but not PFR
        _hand(
            hand_number=2,
            actions=[
                _action(HUMAN, "call", 20, "PRE_FLOP"),
            ],
            winners=["Villain"],
        ),
        # Hand 3: Hero raises preflop — both VPIP and PFR
        _hand(
            hand_number=3,
            actions=[
                _action(HUMAN, "raise", 60, "PRE_FLOP"),
            ],
            winners=[HUMAN],
            pot_size=200,
        ),
    ]
    result = summarize_cash_session(
        hands=hands,
        human_name=HUMAN,
        buy_in=500,
        cash_out=600,
        started_at=None,
        now=datetime.utcnow(),
    )
    # 2 VPIP hands out of 3 = 66.7%
    assert result["vpip_pct"] == pytest.approx(66.7, abs=0.05)
    # 1 PFR hand out of 3 = 33.3%
    assert result["pfr_pct"] == pytest.approx(33.3, abs=0.05)
    assert result["hands_played"] == 3
    assert result["hands_won"] == 1
    assert result["biggest_pot_won"] == 200


def test_postflop_aggression():
    """Aggression = raises / (raises + calls) postflop only."""
    hands = [
        _hand(
            hand_number=1,
            actions=[
                # Preflop call (ignored for aggression)
                _action(HUMAN, "call", 20, "PRE_FLOP"),
                # Postflop: 1 raise + 1 call = 50% aggression
                _action(HUMAN, "raise", 50, "FLOP"),
                _action(HUMAN, "call", 50, "TURN"),
            ],
            winners=["Villain"],
        ),
    ]
    result = summarize_cash_session(
        hands=hands,
        human_name=HUMAN,
        buy_in=500,
        cash_out=400,
        started_at=None,
        now=datetime.utcnow(),
    )
    assert result["aggression_pct"] == pytest.approx(50.0, abs=0.05)


def test_play_style_classification():
    """Generate enough hands to trigger style labeling (>= 10)."""
    # 10 hands: 6 voluntary preflop, 4 of those raises → VPIP=60%, PFR=40%, ratio=0.67 → Loose-Aggressive
    actions_voluntary_raise = [_action(HUMAN, "raise", 30, "PRE_FLOP")]
    actions_voluntary_call = [_action(HUMAN, "call", 20, "PRE_FLOP")]
    actions_fold = [_action(HUMAN, "fold", 0, "PRE_FLOP")]
    hands = []
    for i in range(4):
        hands.append(_hand(hand_number=i + 1, actions=actions_voluntary_raise, winners=["Villain"]))
    for i in range(2):
        hands.append(_hand(hand_number=i + 5, actions=actions_voluntary_call, winners=["Villain"]))
    for i in range(4):
        hands.append(_hand(hand_number=i + 7, actions=actions_fold, winners=["Villain"]))

    result = summarize_cash_session(
        hands=hands,
        human_name=HUMAN,
        buy_in=500,
        cash_out=500,
        started_at=None,
        now=datetime.utcnow(),
    )
    assert result["hands_played"] == 10
    assert result["vpip_pct"] == 60.0
    assert result["pfr_pct"] == 40.0
    assert result["play_style"] == "Loose-Aggressive"


def test_play_style_tight_passive():
    """Low VPIP + low aggression ratio → Tight-Passive."""
    actions_fold = [_action(HUMAN, "fold", 0, "PRE_FLOP")]
    actions_call = [_action(HUMAN, "call", 20, "PRE_FLOP")]
    hands = []
    # 10 hands: 1 call, 9 folds → VPIP=10%, PFR=0%, ratio=0 → Tight-Passive
    hands.append(_hand(hand_number=1, actions=actions_call, winners=["Villain"]))
    for i in range(9):
        hands.append(_hand(hand_number=i + 2, actions=actions_fold, winners=["Villain"]))

    result = summarize_cash_session(
        hands=hands,
        human_name=HUMAN,
        buy_in=500,
        cash_out=400,
        started_at=None,
        now=datetime.utcnow(),
    )
    assert result["play_style"] == "Tight-Passive"


def test_staked_session_net_pnl_uses_player_take_home():
    """Staked sessions: net_pnl is player take-home, NOT gross table P&L.

    A staked player walks in with $0 of their own money. The sponsor
    funded the principal. At leave-time, the sponsor pulls their cut
    off the top and the player keeps what's left. The headline number
    has to reflect what the player actually gains in their bankroll,
    not the misleading `cash_out - principal` gross P&L.
    """
    result = summarize_cash_session(
        hands=[],
        human_name=HUMAN,
        buy_in=0,
        cash_out=1200,  # ended with $1,200 on the table
        started_at=None,
        now=datetime.utcnow(),
        fallback_hand_count=3,
        is_staked=True,
        sponsor_principal=500,  # sponsor put up $500
        sponsor_repaid=850,  # sponsor pulled $850 off the top
        player_take_home=350,  # player walks away with $350
    )
    # Headline reflects player take-home, NOT cash_out - principal
    # (which would say +$700, double-counting the sponsor's recovery).
    assert result["net_pnl"] == 350
    assert result["is_staked"] is True
    assert result["sponsor_principal"] == 500
    assert result["sponsor_repaid"] == 850
    assert result["player_take_home"] == 350


def test_staked_session_full_bust_take_home_zero():
    """Staked player who busts: take-home is 0, not negative principal."""
    result = summarize_cash_session(
        hands=[],
        human_name=HUMAN,
        buy_in=0,
        cash_out=0,
        started_at=None,
        now=datetime.utcnow(),
        is_staked=True,
        sponsor_principal=500,
        sponsor_repaid=0,
        player_take_home=0,
    )
    assert result["net_pnl"] == 0  # not -500; the player didn't lose own money
    assert result["sponsor_repaid"] == 0
    assert result["player_take_home"] == 0


def test_self_funded_summary_unchanged_by_new_fields():
    """The legacy self-funded path still uses cash_out - buy_in."""
    result = summarize_cash_session(
        hands=[],
        human_name=HUMAN,
        buy_in=500,
        cash_out=750,
        started_at=None,
        now=datetime.utcnow(),
        # is_staked omitted → False; all sponsor_* default to 0/None
    )
    assert result["net_pnl"] == 250
    assert result["is_staked"] is False
    assert result["sponsor_principal"] == 0
    assert result["sponsor_repaid"] == 0
    assert result["player_take_home"] is None


def test_hand_not_counted_when_player_not_dealt_in():
    """Hands where the human isn't seated should not contribute to hands_played."""
    hands = [
        _hand(
            hand_number=1,
            actions=[_action("Villain", "call", 20, "PRE_FLOP")],
            winners=["Villain"],
            players=["Villain", "Other"],  # Hero not in this hand
        ),
        _hand(
            hand_number=2,
            actions=[_action(HUMAN, "fold", 0, "PRE_FLOP")],
            winners=["Villain"],
        ),
    ]
    result = summarize_cash_session(
        hands=hands,
        human_name=HUMAN,
        buy_in=500,
        cash_out=500,
        started_at=None,
        now=datetime.utcnow(),
    )
    assert result["hands_played"] == 1

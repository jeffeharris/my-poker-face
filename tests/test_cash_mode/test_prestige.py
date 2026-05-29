"""Unit tests for the prestige/reputation compute (cash_mode/prestige.py).

Pure — no DB. The compute is repo-injected, so we feed it tiny fakes that
return canned relationship edges / pair stats / sessions and assert on the
two axes, the quadrant classifier, the renown ratchet, the saturations, and
graceful degradation when a repo throws.
"""

from __future__ import annotations

from datetime import datetime

from cash_mode.prestige import (
    BREADTH_CAP,
    QUADRANT_BELOVED_LEGEND,
    QUADRANT_DISLIKED_NOBODY,
    QUADRANT_INFAMOUS_VILLAIN,
    QUADRANT_UP_AND_COMER,
    W_HIGH_STAKES,
    W_TENURE,
    compute_prestige,
    quadrant_label,
    reputation_chat_tone,
)

NOW = datetime(2026, 5, 29, 12, 0, 0)
OWNER = "guest_jeff"
SB = "sb-1"


# --- tiny fakes -------------------------------------------------------------


class _Edge:
    """Stand-in for a RelationshipState (only the axes the formula reads)."""

    def __init__(self, likability=0.5, respect=0.5, heat=0.0):
        self.likability = likability
        self.respect = respect
        self.heat = heat


class _Pair:
    def __init__(self, opponent_id, cumulative_pnl=0, hands_played_cash=1):
        self.opponent_id = opponent_id
        self.cumulative_pnl = cumulative_pnl
        self.hands_played_cash = hands_played_cash


class _Session:
    def __init__(self, stake_label="$2", hands_played=0, player_take_home=None, total_buy_in=0):
        self.stake_label = stake_label
        self.hands_played = hands_played
        self.player_take_home = player_take_home
        self.total_buy_in = total_buy_in


class _RelRepo:
    def __init__(self, inbound=None, pairs=None, raise_inbound=False):
        self._inbound = inbound or {}
        self._pairs = pairs or []
        self._raise_inbound = raise_inbound

    def load_inbound_relationships(self, owner_id, *, now=None):
        if self._raise_inbound:
            raise RuntimeError("boom")
        return self._inbound

    def list_cash_pair_stats_for_observer(self, owner_id, *, sandbox_id=None):
        return self._pairs


class _SessionRepo:
    def __init__(self, sessions=None):
        self._sessions = sessions or []

    def list_completed_for_sandbox(self, owner_id, sandbox_id):
        return self._sessions


def _compute(rel_repo, session_repo, renown_peak=0.0):
    return compute_prestige(
        owner_id=OWNER,
        sandbox_id=SB,
        now=NOW,
        relationship_repo=rel_repo,
        cash_session_repo=session_repo,
        renown_peak=renown_peak,
    )


# --- quadrant classifier ----------------------------------------------------


def test_quadrant_classifier_all_four():
    assert quadrant_label(0.9, 0.5) == QUADRANT_BELOVED_LEGEND
    assert quadrant_label(0.9, -0.5) == QUADRANT_INFAMOUS_VILLAIN
    assert quadrant_label(0.1, 0.5) == QUADRANT_UP_AND_COMER
    assert quadrant_label(0.1, -0.5) == QUADRANT_DISLIKED_NOBODY


def test_reputation_chat_tone_only_for_high_renown_quadrants():
    # High-renown quadrants get a tone hint...
    assert "warmth" in reputation_chat_tone(QUADRANT_BELOVED_LEGEND).lower()
    assert "notorious" in reputation_chat_tone(QUADRANT_INFAMOUS_VILLAIN).lower()
    # ...low-renown quadrants (and unknowns) stay silent — the room doesn't
    # react to a player who isn't a figure yet.
    assert reputation_chat_tone(QUADRANT_UP_AND_COMER) == ""
    assert reputation_chat_tone(QUADRANT_DISLIKED_NOBODY) == ""
    assert reputation_chat_tone("Nonsense") == ""


def test_zero_state_is_disliked_nobody():
    score = _compute(_RelRepo(), _SessionRepo())
    assert score.renown == 0.0
    assert score.regard == 0.0
    assert score.quadrant == QUADRANT_DISLIKED_NOBODY
    assert score.opponent_count == 0
    assert score.computed_at.endswith("Z")


# --- renown -----------------------------------------------------------------


def test_renown_ratchets_to_peak():
    # Empty inputs compute renown 0, but a prior peak holds it up.
    score = _compute(_RelRepo(), _SessionRepo(), renown_peak=0.62)
    assert score.renown == 0.62


def test_breadth_saturates_at_cap():
    pairs_at_cap = [_Pair(f"ai{i}", hands_played_cash=3) for i in range(BREADTH_CAP)]
    pairs_over = [_Pair(f"ai{i}", hands_played_cash=3) for i in range(BREADTH_CAP + 8)]
    at_cap = _compute(_RelRepo(pairs=pairs_at_cap), _SessionRepo())
    over = _compute(_RelRepo(pairs=pairs_over), _SessionRepo())
    assert at_cap.renown_breadth == over.renown_breadth  # extra opponents add nothing


def test_breadth_ignores_unplayed_pairs():
    pairs = [_Pair("a", hands_played_cash=0), _Pair("b", hands_played_cash=5)]
    score = _compute(_RelRepo(pairs=pairs), _SessionRepo())
    # Only one opponent actually played → 1/CAP of the breadth weight.
    assert score.renown_breadth > 0
    one_only = _compute(_RelRepo(pairs=[_Pair("b", hands_played_cash=5)]), _SessionRepo())
    assert score.renown_breadth == one_only.renown_breadth


def test_tenure_caps():
    huge = _SessionRepo([_Session(stake_label="$2", hands_played=10_000_000)])
    score = _compute(_RelRepo(), huge)
    assert round(score.renown_tenure, 4) == round(W_TENURE, 4)


def test_highest_stake_tier_drives_component():
    low = _compute(_RelRepo(), _SessionRepo([_Session(stake_label="$2", hands_played=10)]))
    high = _compute(
        _RelRepo(),
        _SessionRepo([_Session(stake_label="$2"), _Session(stake_label="$1000")]),
    )
    assert high.renown_stake_tier > low.renown_stake_tier
    # $2 is the bottom rung → zero tier credit.
    assert low.renown_stake_tier == 0.0


def test_high_stakes_win_requires_profit_at_high_table():
    # Profitable $200 session → full bonus.
    win = _compute(
        _RelRepo(),
        _SessionRepo([_Session(stake_label="$200", player_take_home=900, total_buy_in=400)]),
    )
    assert round(win.renown_high_stakes, 4) == round(W_HIGH_STAKES, 4)
    # Losing $200 session → no bonus.
    loss = _compute(
        _RelRepo(),
        _SessionRepo([_Session(stake_label="$200", player_take_home=100, total_buy_in=400)]),
    )
    assert loss.renown_high_stakes == 0.0
    # Winning at a low table → no bonus (not a high-stakes label).
    low_win = _compute(
        _RelRepo(),
        _SessionRepo([_Session(stake_label="$10", player_take_home=900, total_buy_in=400)]),
    )
    assert low_win.renown_high_stakes == 0.0


def test_beat_respected_credits_respect_of_beaten_opponents():
    # Human beat 'whale', who deeply respects them.
    inbound = {"whale": _Edge(respect=1.0), "fish": _Edge(respect=0.5)}
    pairs = [_Pair("whale", cumulative_pnl=5000), _Pair("fish", cumulative_pnl=-200)]
    score = _compute(_RelRepo(inbound=inbound, pairs=pairs), _SessionRepo())
    assert score.renown_beat_respected > 0
    # Beating only a low-respect opponent → little/no credit.
    low = _compute(
        _RelRepo(inbound={"fish": _Edge(respect=0.5)}, pairs=[_Pair("fish", cumulative_pnl=300)]),
        _SessionRepo(),
    )
    assert low.renown_beat_respected == 0.0


# --- regard -----------------------------------------------------------------


def test_regard_warm_when_room_likes_you():
    inbound = {"a": _Edge(likability=1.0, respect=1.0, heat=0.0) for _ in range(1)}
    inbound = {f"ai{i}": _Edge(likability=1.0, respect=0.9, heat=0.0) for i in range(4)}
    score = _compute(_RelRepo(inbound=inbound), _SessionRepo())
    assert score.regard > 0
    assert score.opponent_count == 4


def test_regard_hostile_when_room_runs_hot():
    inbound = {f"ai{i}": _Edge(likability=0.1, respect=0.3, heat=0.9) for i in range(4)}
    score = _compute(_RelRepo(inbound=inbound), _SessionRepo())
    assert score.regard < 0


def test_regard_averages_to_near_zero_for_balanced_room():
    # A room split warm/cold on likability+respect but with NO heat averages
    # to ~neutral. (Heat is deliberately excluded here: it's one-sided
    # notoriety, so any heat pulls regard negative even in an otherwise
    # balanced room — see test_regard_hostile_*.)
    inbound = {
        "warm1": _Edge(likability=1.0, respect=0.9, heat=0.0),
        "warm2": _Edge(likability=1.0, respect=0.9, heat=0.0),
        "cold1": _Edge(likability=0.0, respect=0.1, heat=0.0),
        "cold2": _Edge(likability=0.0, respect=0.1, heat=0.0),
    }
    score = _compute(_RelRepo(inbound=inbound), _SessionRepo())
    assert abs(score.regard) < 0.01


def test_heat_pulls_regard_negative_even_in_otherwise_warm_room():
    # One-sidedness of heat: a room that likes & respects you but runs hot
    # still nets negative regard.
    inbound = {f"ai{i}": _Edge(likability=0.8, respect=0.8, heat=0.8) for i in range(4)}
    score = _compute(_RelRepo(inbound=inbound), _SessionRepo())
    assert score.regard < 0


def test_high_renown_plus_hostile_is_villain():
    # Lots of respected beaten opponents + high stakes → high renown; but the
    # room is hostile (heat) → villain quadrant.
    inbound = {f"ai{i}": _Edge(likability=0.0, respect=0.95, heat=0.9) for i in range(BREADTH_CAP)}
    pairs = [_Pair(f"ai{i}", cumulative_pnl=9000, hands_played_cash=20) for i in range(BREADTH_CAP)]
    sessions = [
        _Session(stake_label="$1000", hands_played=3000, player_take_home=5000, total_buy_in=1000)
    ]
    score = _compute(_RelRepo(inbound=inbound, pairs=pairs), _SessionRepo(sessions))
    assert score.renown >= 0.4
    assert score.regard < 0.05
    assert score.quadrant == QUADRANT_INFAMOUS_VILLAIN


# --- robustness -------------------------------------------------------------


def test_repo_failure_degrades_to_zero_not_crash():
    score = _compute(_RelRepo(raise_inbound=True), _SessionRepo())
    # Inbound blew up → regard 0, opponent_count 0, no exception.
    assert score.regard == 0.0
    assert score.opponent_count == 0


def test_determinism_same_inputs_same_score():
    rel = _RelRepo(inbound={"a": _Edge(likability=0.8, respect=0.7, heat=0.1)})
    a = _compute(rel, _SessionRepo([_Session(stake_label="$50", hands_played=100)]))
    b = _compute(rel, _SessionRepo([_Session(stake_label="$50", hands_played=100)]))
    assert a == b

"""Tests for the success-weighted table-affinity lever.

An AI is drawn back to rooms it wins at and away from rooms it loses at, via
`W_AFFINITY * stake_fit * table_affinity(net, buy_in)` added to
`table_attractiveness`. Affinity is buy-in-normalized (stake-relative) and
tier-subordinate (scaled by stake_fit, so it differentiates rooms within a
tier the AI fits, never overriding a sensible climb). The per-room net feeding
it lives in `ai_table_hand_counts` (RelationshipRepository).

See `cash_mode/attractiveness.py`, `poker/repositories/relationship_repository.py`,
`docs/plans/CASH_MODE_AI_SPLIT_STAKE_CLIMB.md` is unrelated — this is the affinity
lever (`TABLE_AFFINITY_ENABLED`).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

from cash_mode.attractiveness import (
    AFFINITY_SCALE_BUYINS,
    table_affinity,
    table_attractiveness,
)
from cash_mode.stakes_ladder import table_buy_in_window

_, _, _MAX_BI_10 = table_buy_in_window("$10")


# --- table_affinity (pure) ---------------------------------------------------


def test_affinity_zero_for_untried_room():
    assert table_affinity(0, _MAX_BI_10) == 0.0


def test_affinity_sign_follows_net():
    assert table_affinity(5000, _MAX_BI_10) > 0  # won here → positive pull
    assert table_affinity(-5000, _MAX_BI_10) < 0  # lost here → negative pull


def test_affinity_zero_when_buyin_unknown():
    assert table_affinity(9999, 0) == 0.0


def test_affinity_is_buyin_normalized_not_chip_absolute():
    """Same net in BUY-INS gives the same pull at any stake — the fix for the
    stake-blind absolute-chip scale."""
    _, _, max_bi_2 = table_buy_in_window("$2")
    _, _, max_bi_1000 = table_buy_in_window("$1000")
    # "up 2 buy-ins" at each stake → identical affinity.
    a2 = table_affinity(2 * max_bi_2, max_bi_2)
    a1000 = table_affinity(2 * max_bi_1000, max_bi_1000)
    assert a2 == pytest.approx(a1000)
    # And 2 buy-ins is the half-saturation point (tanh(1)).
    import math

    assert a2 == pytest.approx(math.tanh(2.0 / AFFINITY_SCALE_BUYINS))


def test_affinity_saturates():
    """A huge win doesn't pin a room beyond the cap — tanh bounds the pull to 1,
    so the affinity term can never exceed W_AFFINITY no matter the net."""
    assert 0.9 < table_affinity(10_000_000, _MAX_BI_10) <= 1.0
    # A couple of buy-ins already gets most of the pull (diminishing returns).
    assert table_affinity(3 * _MAX_BI_10, _MAX_BI_10) > 0.9


# --- table_attractiveness: affinity term integration ------------------------


def _attr(stake, net, *, comfort="$10", bankroll=12_000):
    return table_attractiveness(
        projected_bankroll=bankroll,
        starting_bankroll=bankroll,
        comfort_zone=comfort,
        stake_label=stake,
        fish_chips=0,
        whale_chips=0,
        other_grinders=0,
        net_at_table=net,
    )


def test_won_at_outranks_untried_outranks_lost_at():
    """At one table: won-at (net>0) > untried (net=0) > lost-at (net<0)."""
    won = _attr("$10", 8000)
    untried = _attr("$10", 0)
    lost = _attr("$10", -8000)
    assert won > untried > lost


def test_affinity_is_tier_subordinate():
    """Affinity is scaled by stake_fit, so the SAME net pulls harder at the AI's
    comfort tier than at an ill-fitting one — it differentiates within a tier
    rather than dragging the AI across tiers."""
    net = 8000
    # boost = score(net) - score(0), isolating the affinity contribution.
    boost_home = _attr("$10", net, comfort="$10") - _attr("$10", 0, comfort="$10")
    boost_offtier = _attr("$2", net, comfort="$10") - _attr("$2", 0, comfort="$10")
    assert boost_home > boost_offtier
    assert boost_offtier >= 0  # never negative, just damped


# --- the counter that feeds it (net accumulation + read) --------------------


def test_increment_accumulates_net(repos):
    rel = repos["relationship_repo"]
    rel.increment_ai_table_hands("zeus", "t-a", sandbox_id="sb1", net_delta=100)
    rel.increment_ai_table_hands("zeus", "t-a", sandbox_id="sb1", net_delta=-30)
    rel.increment_ai_table_hands("zeus", "t-b", sandbox_id="sb1", net_delta=50)
    assert rel.load_ai_table_net("zeus", sandbox_id="sb1") == {"t-a": 70, "t-b": 50}
    # hands counted independently of net
    assert rel.load_ai_table_hands("zeus", sandbox_id="sb1") == {"t-a": 2, "t-b": 1}


def test_load_ai_table_net_empty_when_no_hands(repos):
    assert repos["relationship_repo"].load_ai_table_net("nobody", sandbox_id="sb1") == {}


def test_net_is_sandbox_scoped(repos):
    rel = repos["relationship_repo"]
    rel.increment_ai_table_hands("zeus", "t-a", sandbox_id="sb1", net_delta=100)
    rel.increment_ai_table_hands("zeus", "t-a", sandbox_id="sb2", net_delta=-100)
    assert rel.load_ai_table_net("zeus", sandbox_id="sb1") == {"t-a": 100}
    assert rel.load_ai_table_net("zeus", sandbox_id="sb2") == {"t-a": -100}

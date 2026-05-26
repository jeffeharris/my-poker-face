"""Tests for `chip_flow.allocate_chip_flow` (Phase 3 commit 2).

The allocator is the single shared rule for splitting a winner's net
gain across losers. Both `HandOutcomeDetector` (relationship events)
and the cash-mode `cash_pair_stats` writer consume its output, so
its invariants are load-bearing for the whole Phase 3 surface.
"""

from __future__ import annotations

from poker.memory.chip_flow import (
    ChipFlow,
    PotShare,
    allocate_chip_flow,
)


def _by_pair(flows):
    return {(f.winner, f.loser): f.chips for f in flows}


# ---------------------------------------------------------------------
# Heads-up + simple multiway
# ---------------------------------------------------------------------


class TestHeadsUp:
    def test_heads_up_full_transfer(self):
        # Alice wins 1000; both contributed 500. Alice net +500, from bob.
        pot = PotShare(
            amount=1000,
            winners=("alice",),
            contributions={"alice": 500, "bob": 500},
        )
        flows = allocate_chip_flow([pot])
        assert flows == [ChipFlow(winner="alice", loser="bob", chips=500)]

    def test_heads_up_uneven_contribution(self):
        # Walk-by-fold: alice raised 100, bob folded after putting in
        # only the BB (50). Pot = 150. Alice net = 150 - 100 = 50.
        pot = PotShare(
            amount=150,
            winners=("alice",),
            contributions={"alice": 100, "bob": 50},
        )
        flows = allocate_chip_flow([pot])
        assert flows == [ChipFlow(winner="alice", loser="bob", chips=50)]


class TestThreeWayMainPotOnly:
    def test_proportional_split_equal_contribs(self):
        # Alice wins; bob and carol each put in 300.
        pot = PotShare(
            amount=900,
            winners=("alice",),
            contributions={"alice": 300, "bob": 300, "carol": 300},
        )
        flows = allocate_chip_flow([pot])
        # Alice net = 600; bob and carol each get -300.
        assert _by_pair(flows) == {
            ("alice", "bob"): 300,
            ("alice", "carol"): 300,
        }

    def test_proportional_split_uneven_contribs(self):
        # Alice net 500; bob put in 100, carol put in 400.
        pot = PotShare(
            amount=900,
            winners=("alice",),
            contributions={"alice": 400, "bob": 100, "carol": 400},
        )
        flows = allocate_chip_flow([pot])
        # Total loser contrib = 500. bob's share = 100/500 * 500 = 100.
        # carol's share = 400/500 * 500 = 400.
        assert _by_pair(flows) == {
            ("alice", "bob"): 100,
            ("alice", "carol"): 400,
        }


# ---------------------------------------------------------------------
# Side pots
# ---------------------------------------------------------------------


class TestSidePots:
    def test_three_way_with_one_side_pot(self):
        # Alice all-in for 100; bob raises 500, carol calls 500.
        # Main pot (300): alice + bob + carol each contribute 100,
        # alice eligible. Alice wins main.
        # Side pot (800): bob + carol contribute 400 each. Carol wins.
        main = PotShare(
            amount=300,
            winners=("alice",),
            contributions={"alice": 100, "bob": 100, "carol": 100},
        )
        side = PotShare(
            amount=800,
            winners=("carol",),
            contributions={"bob": 400, "carol": 400},
        )
        flows = allocate_chip_flow([main, side])
        # Main: alice net = 200, split 100/100 vs bob/carol.
        # Side: carol net = 400, all from bob.
        assert _by_pair(flows) == {
            ("alice", "bob"): 100,
            ("alice", "carol"): 100,
            ("carol", "bob"): 400,
        }

    def test_all_in_collision(self):
        # Alice all-in 200; bob all-in 500; carol calls 500.
        # Main pot (600): all three contribute 200; alice wins.
        # Side pot (600): bob + carol contribute 300 each; bob wins.
        main = PotShare(
            amount=600,
            winners=("alice",),
            contributions={"alice": 200, "bob": 200, "carol": 200},
        )
        side = PotShare(
            amount=600,
            winners=("bob",),
            contributions={"bob": 300, "carol": 300},
        )
        flows = allocate_chip_flow([main, side])
        # Main: alice net = 400 → 200 from bob, 200 from carol.
        # Side: bob net = 300 → 300 from carol.
        assert _by_pair(flows) == {
            ("alice", "bob"): 200,
            ("alice", "carol"): 200,
            ("bob", "carol"): 300,
        }

    def test_same_pair_in_two_pots_aggregates(self):
        # Bob/carol appear as winner/loser in both pots — verify the
        # final flow aggregates rather than emitting two entries.
        pot1 = PotShare(
            amount=200,
            winners=("bob",),
            contributions={"bob": 100, "carol": 100},
        )
        pot2 = PotShare(
            amount=400,
            winners=("bob",),
            contributions={"bob": 200, "carol": 200},
        )
        flows = allocate_chip_flow([pot1, pot2])
        assert flows == [ChipFlow(winner="bob", loser="carol", chips=300)]


# ---------------------------------------------------------------------
# Split pots
# ---------------------------------------------------------------------


class TestSplitPots:
    def test_split_pot_two_winners(self):
        # Alice and bob chop a 900 pot; carol is the only loser.
        pot = PotShare(
            amount=900,
            winners=("alice", "bob"),
            contributions={"alice": 300, "bob": 300, "carol": 300},
        )
        flows = allocate_chip_flow([pot])
        # Each winner gets 450, net 150, all from carol.
        assert _by_pair(flows) == {
            ("alice", "carol"): 150,
            ("bob", "carol"): 150,
        }

    def test_split_pot_odd_chip_to_first_winner(self):
        # 901-chip split → alice gets 451, bob gets 450 (odd chip to
        # first winner per the settlement convention).
        pot = PotShare(
            amount=901,
            winners=("alice", "bob"),
            contributions={"alice": 300, "bob": 300, "carol": 301},
        )
        flows = allocate_chip_flow([pot])
        # alice net = 451 - 300 = 151
        # bob net = 450 - 300 = 150
        # Carol contributed 301, single loser.
        assert _by_pair(flows) == {
            ("alice", "carol"): 151,
            ("bob", "carol"): 150,
        }


# ---------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_pots_returns_empty(self):
        assert allocate_chip_flow([]) == []

    def test_zero_amount_pot_skipped(self):
        pot = PotShare(amount=0, winners=("alice",), contributions={})
        assert allocate_chip_flow([pot]) == []

    def test_no_winners_skipped(self):
        pot = PotShare(
            amount=100,
            winners=(),
            contributions={"alice": 50, "bob": 50},
        )
        assert allocate_chip_flow([pot]) == []

    def test_no_losers_skipped(self):
        # Only the winner contributed (returned uncalled bet).
        pot = PotShare(
            amount=100,
            winners=("alice",),
            contributions={"alice": 100},
        )
        assert allocate_chip_flow([pot]) == []

    def test_winner_overpaid_skipped(self):
        # Pathological: winner contributed more than they collected
        # (possible only if amount < contribution, e.g., split pot
        # with weird allocation). No flow generated for that winner.
        pot = PotShare(
            amount=100,
            winners=("alice",),
            contributions={"alice": 200, "bob": 0},
        )
        flows = allocate_chip_flow([pot])
        assert flows == []

    def test_largest_remainder_preserves_total(self):
        # Allocation must sum exactly to the winner's net gain even
        # when proportions don't divide evenly. 3 losers, net gain 10.
        pot = PotShare(
            amount=40,
            winners=("alice",),
            contributions={"alice": 30, "bob": 10, "carol": 10, "dave": 10},
        )
        flows = allocate_chip_flow([pot])
        total_allocated = sum(f.chips for f in flows)
        # alice net = 40 - 30 = 10; split 3 ways → 4, 3, 3.
        assert total_allocated == 10
        # All three losers receive at least 3 chips.
        chips = sorted(f.chips for f in flows)
        assert chips == [3, 3, 4]

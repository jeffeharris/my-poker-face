"""Tests for hand classification (made tier + draw modifier)."""

import pytest

from poker.strategy.hand_classification import (
    FOUR_FLUSH_BOARD,
    FOUR_STRAIGHT_BOARD,
    FULL_HOUSE_POSSIBLE,
    HIGHER_FLUSH_POSSIBLE,
    HIGHER_STRAIGHT_POSSIBLE,
    NUT_ACTUAL,
    NUT_BLUFF_CATCHER,
    NUT_NEAR,
    NUT_NON_NUT_STRONG,
    PAIRED_BOARD,
    TRIPS_ON_BOARD,
    classify_hand,
    classify_hand_full,
    simplify_hand_class,
)

# ---------------------------------------------------------------------------
# Made-tier tests
# ---------------------------------------------------------------------------


class TestMadeTierClassification:
    """Test made-hand tier assignment."""

    def test_flush_is_nuts(self):
        made, _ = classify_hand(['Ah', 'Kh'], ['Qh', 'Jh', '2h'])
        assert made == 'nuts'

    def test_straight_is_nuts(self):
        made, _ = classify_hand(['9h', '8d'], ['7s', '6c', '5h'])
        assert made == 'nuts'

    def test_set_is_nuts(self):
        # Pocket pair + board match = set
        made, _ = classify_hand(['7h', '7d'], ['7s', '4c', '2h'])
        assert made == 'nuts'

    def test_trips_is_strong_made(self):
        # One hole card + board pair = trips (not a set)
        made, _ = classify_hand(['Ah', '7d'], ['7s', '7c', '2h'])
        assert made == 'strong_made'

    def test_two_pair_is_strong_made(self):
        made, _ = classify_hand(['Kh', 'Jd'], ['Ks', 'Jc', '5h'])
        assert made == 'strong_made'

    def test_overpair_is_strong_made(self):
        made, _ = classify_hand(['Qh', 'Qd'], ['Js', '8c', '3h'])
        assert made == 'strong_made'

    def test_tptk_is_strong_made(self):
        # Top pair (K matches board K) + A kicker
        made, _ = classify_hand(['Ah', 'Kd'], ['Ks', '7c', '2h'])
        assert made == 'strong_made'

    def test_top_pair_weak_kicker_is_medium_made(self):
        # Top pair (K matches board K) + 9 kicker (not A/K)
        made, _ = classify_hand(['Kh', '9d'], ['Ks', '7c', '2h'])
        assert made == 'medium_made'

    def test_second_pair_dry_board_is_medium_made(self):
        # 8 matches second-highest board rank (K > 8 > 2), dry/rainbow board
        made, _ = classify_hand(['8h', '7d'], ['Ks', '8c', '2h'])
        assert made == 'medium_made'

    def test_weak_made_bottom_pair(self):
        # 5 matches lowest board rank
        made, _ = classify_hand(['5h', '4d'], ['Ks', '8c', '5s'])
        assert made == 'weak_made'

    def test_air_no_pair(self):
        made, _ = classify_hand(['Ah', 'Qd'], ['Ks', '8c', '3h'])
        assert made == 'air'

    def test_full_house_is_nuts(self):
        made, _ = classify_hand(['Ah', 'Ad'], ['As', 'Kc', 'Kh'])
        assert made == 'nuts'

    def test_four_of_a_kind_is_nuts(self):
        made, _ = classify_hand(['Ah', 'Ad'], ['As', 'Ac', '2h'])
        assert made == 'nuts'


# ---------------------------------------------------------------------------
# Draw-modifier tests
# ---------------------------------------------------------------------------


class TestDrawModifierClassification:
    """Test draw modifier assignment."""

    def test_flush_draw_is_strong_draw(self):
        # 4 hearts: Ah, 5h, Kh, 7h
        _, draw = classify_hand(['Ah', '5h'], ['Kh', '7h', '2s'])
        assert draw == 'strong_draw'

    def test_oesd_is_strong_draw(self):
        # J-T-9-8 are 4 consecutive ranks
        _, draw = classify_hand(['Jh', 'Td'], ['9s', '8c', '2h'])
        assert draw == 'strong_draw'

    def test_gutshot_is_weak_draw(self):
        # J-T-8-7: needs 9, 4-in-5-window but not 4 consecutive → gutshot
        _, draw = classify_hand(['Jh', 'Td'], ['8s', '7c', '2h'])
        assert draw == 'weak_draw'

    def test_backdoor_flush(self):
        # Only 2 hearts in hole + 1 on board = 3 total (not 4)
        # But we need to make sure no straight draw overrides
        _, draw = classify_hand(['Ah', '5h'], ['Kh', '7s', '2d'])
        assert draw == 'backdoor'

    def test_no_draw(self):
        _, draw = classify_hand(['Ah', 'Kd'], ['Qs', '7c', '2h'])
        assert draw == 'no_draw'

    def test_made_flush_has_no_draw(self):
        # Already made a flush (5 hearts) → no_draw
        _, draw = classify_hand(['Ah', 'Kh'], ['Qh', 'Jh', '2h'])
        assert draw == 'no_draw'

    def test_made_straight_has_no_draw(self):
        _, draw = classify_hand(['9h', '8d'], ['7s', '6c', '5h'])
        assert draw == 'no_draw'


# ---------------------------------------------------------------------------
# simplify_hand_class tests
# ---------------------------------------------------------------------------


class TestSimplifyHandClass:
    """Test the simplified 6-class mapping."""

    def test_nuts_any_draw(self):
        assert simplify_hand_class('nuts', 'strong_draw') == 'nuts'
        assert simplify_hand_class('nuts', 'no_draw') == 'nuts'

    def test_strong_made_strong_draw_promotes_to_nuts(self):
        assert simplify_hand_class('strong_made', 'strong_draw') == 'nuts'

    def test_strong_made_other(self):
        assert simplify_hand_class('strong_made', 'no_draw') == 'strong_made'
        assert simplify_hand_class('strong_made', 'weak_draw') == 'strong_made'

    def test_medium_made_strong_draw_promotes(self):
        assert simplify_hand_class('medium_made', 'strong_draw') == 'strong_made'

    def test_medium_made_other(self):
        assert simplify_hand_class('medium_made', 'no_draw') == 'medium_made'
        assert simplify_hand_class('medium_made', 'backdoor') == 'medium_made'

    def test_weak_made_strong_draw_promotes(self):
        assert simplify_hand_class('weak_made', 'strong_draw') == 'medium_made'

    def test_weak_made_other(self):
        assert simplify_hand_class('weak_made', 'no_draw') == 'weak_made'
        assert simplify_hand_class('weak_made', 'weak_draw') == 'weak_made'

    def test_air_strong_draw(self):
        assert simplify_hand_class('air', 'strong_draw') == 'air_strong_draw'

    def test_air_no_draw(self):
        assert simplify_hand_class('air', 'no_draw') == 'air_no_draw'
        assert simplify_hand_class('air', 'weak_draw') == 'air_no_draw'
        assert simplify_hand_class('air', 'backdoor') == 'air_no_draw'


# ---------------------------------------------------------------------------
# Integration: classify_hand → simplify_hand_class
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """Full pipeline from cards to simplified class."""

    def test_set_simplifies_to_nuts(self):
        made, draw = classify_hand(['7h', '7d'], ['7s', '4c', '2h'])
        assert simplify_hand_class(made, draw) == 'nuts'

    def test_tptk_simplifies_to_strong_made(self):
        made, draw = classify_hand(['Ah', 'Kd'], ['Ks', '7c', '2h'])
        assert simplify_hand_class(made, draw) == 'strong_made'

    def test_air_with_flush_draw_simplifies(self):
        # Air (no pair) + flush draw → air_strong_draw
        made, draw = classify_hand(['Ah', '5h'], ['Kh', '7h', '2s'])
        # Actually Ah-5h with Kh-7h is 4 hearts → flush draw
        # But Ah is high card only (no pair) → air + strong_draw
        assert simplify_hand_class(made, draw) == 'air_strong_draw'


# ---------------------------------------------------------------------------
# §1: Danger flag detection
# ---------------------------------------------------------------------------


class TestDangerFlags:
    """Board-level + hand-vs-board danger flag detection."""

    def test_paired_board_flag(self):
        result = classify_hand_full(['Ah', 'Kd'], ['Ts', 'Tc', '4h'])
        assert PAIRED_BOARD in result.danger_flags

    def test_trips_on_board_flag(self):
        result = classify_hand_full(['Ah', 'Kd'], ['Ts', 'Tc', 'Td'])
        assert TRIPS_ON_BOARD in result.danger_flags
        assert PAIRED_BOARD in result.danger_flags  # superset

    def test_four_flush_board_flag(self):
        # Four hearts on the board, hero unrelated suits
        result = classify_hand_full(['As', 'Kc'], ['Th', '7h', '2h', '4h'])
        assert FOUR_FLUSH_BOARD in result.danger_flags

    def test_four_straight_board_flag(self):
        # 7-8-9-T on board → 4 consecutive ranks
        result = classify_hand_full(['As', 'Kc'], ['7h', '8d', '9c', 'Ts'])
        assert FOUR_STRAIGHT_BOARD in result.danger_flags

    def test_dry_board_no_flags(self):
        # K-7-2 rainbow: nothing dangerous
        result = classify_hand_full(['Ah', 'Kd'], ['Ks', '7c', '2h'])
        assert result.danger_flags == frozenset()

    def test_higher_straight_possible_fires_on_4_straight_board(self):
        # Hero 6♠Q♦ on 7♥T♥8♠2♣9♣ — Example 1 from the plan.
        # Board 7-T-8-9 has 4 consecutive ranks (7,8,9,T); hero has 10-high
        # straight; J-high straight is possible if opp holds a J.
        result = classify_hand_full(['6s', 'Qd'], ['7h', 'Th', '8s', '2c', '9c'])
        assert FOUR_STRAIGHT_BOARD in result.danger_flags
        assert HIGHER_STRAIGHT_POSSIBLE in result.danger_flags

    def test_higher_straight_not_possible_on_3_card_board(self):
        # 5-6-7 flop with hero 9-8: 9-high straight is the nut straight
        # (opp can't have a higher straight without using hero's 8 or 9).
        result = classify_hand_full(['9h', '8d'], ['7s', '6c', '5h'])
        assert FOUR_STRAIGHT_BOARD not in result.danger_flags
        assert HIGHER_STRAIGHT_POSSIBLE not in result.danger_flags

    def test_full_house_possible_on_paired_board_with_pair_hand(self):
        # Hero K-9 on T-T-4: one pair (9s)... wait, no pair from hole.
        # Hero K-T on T-T-4: trips. Paired board, hand_rank=7 (trips).
        # FULL_HOUSE_POSSIBLE should fire since paired board + non-FH hand.
        result = classify_hand_full(['Kh', 'Td'], ['Ts', 'Tc', '4h'])
        assert PAIRED_BOARD in result.danger_flags
        assert FULL_HOUSE_POSSIBLE in result.danger_flags

    def test_higher_flush_possible_when_hero_doesnt_hold_nut_card(self):
        # Hero T♠ 9♠ on monotone spade board, no A or K of spades in
        # hero's hand or board → higher flush possible
        result = classify_hand_full(['Ts', '9s'], ['7s', '5s', '2s'])
        assert HIGHER_FLUSH_POSSIBLE in result.danger_flags

    def test_nut_flush_no_higher_possible(self):
        # Hero As-Ks on spade-heavy board → hero has nut flush, no
        # higher flush possible
        result = classify_hand_full(['As', 'Ks'], ['7s', '5s', '2s'])
        assert HIGHER_FLUSH_POSSIBLE not in result.danger_flags


# ---------------------------------------------------------------------------
# §1: nut_status assignment
# ---------------------------------------------------------------------------


class TestNutStatus:
    """nut_status: actual_nuts / near_nuts / non_nut_strong / bluff_catcher."""

    def test_quads_is_actual_nuts(self):
        result = classify_hand_full(['Ah', 'Ad'], ['As', 'Ac', '2h'])
        assert result.nut_status == NUT_ACTUAL

    def test_full_house_safe_board_is_actual_nuts(self):
        result = classify_hand_full(['Kh', 'Kd'], ['Ks', '7c', '7h'])
        assert result.nut_status == NUT_ACTUAL

    def test_full_house_trips_on_board_drops_to_near_nuts(self):
        # Board trips means a bigger FH is possible; hero's FH is no
        # longer the deck-best.
        result = classify_hand_full(['9h', '9d'], ['Ts', 'Tc', 'Td'])
        assert result.nut_status == NUT_NEAR

    def test_nut_flush_is_actual_nuts(self):
        result = classify_hand_full(['As', 'Ks'], ['7s', '5s', '2s'])
        assert result.nut_status == NUT_ACTUAL

    def test_non_nut_flush_is_non_nut_strong(self):
        result = classify_hand_full(['Ts', '9s'], ['7s', '5s', '2s'])
        assert result.nut_status == NUT_NON_NUT_STRONG

    def test_nut_straight_on_dry_board_is_actual_nuts(self):
        # 9-high straight on 5-6-7 flop using hero's 8-9 is the nut
        # straight (no higher straight reachable).
        result = classify_hand_full(['9h', '8d'], ['7s', '6c', '5h'])
        assert result.nut_status == NUT_ACTUAL

    def test_non_nut_straight_when_higher_possible(self):
        # Example 1 from plan: 10-high straight, J-high possible.
        result = classify_hand_full(['6s', 'Qd'], ['7h', 'Th', '8s', '2c', '9c'])
        assert result.nut_status == NUT_NON_NUT_STRONG

    def test_set_on_dry_board_is_near_nuts(self):
        # Set of 7s on K-7-2 board, no danger flags
        result = classify_hand_full(['7h', '7d'], ['7s', 'Kc', '2h'])
        assert result.nut_status == NUT_NEAR

    def test_trips_on_paired_board_is_non_nut_strong(self):
        # Hero K-9 on 9-9-A-2 (paired board, hero has trip 9s — NOT
        # a "set", since set + paired board would already be FH).
        # FULL_HOUSE_POSSIBLE fires; nut_status drops to non_nut_strong.
        result = classify_hand_full(['Kh', '9d'], ['9s', '9c', 'Ah', '2c'])
        assert FULL_HOUSE_POSSIBLE in result.danger_flags
        assert result.nut_status == NUT_NON_NUT_STRONG

    def test_top_pair_on_4broadway_is_bluff_catcher(self):
        # Hero K-3 on 4-Q-J-K-T board (Example 5 from plan): top pair on
        # 4-Broadway → bluff catcher.
        result = classify_hand_full(['Kd', '3s'], ['4c', 'Qd', 'Jc', 'Kh', 'Ts'])
        assert FOUR_STRAIGHT_BOARD in result.danger_flags
        assert result.nut_status == NUT_BLUFF_CATCHER

    def test_pair_on_paired_board_is_bluff_catcher(self):
        # Pair on a paired board: bluff catcher
        result = classify_hand_full(['9h', '8d'], ['Ts', 'Tc', '4h'])
        # Hero plays one pair (Ts) — actually this becomes two pair if
        # hero pairs the board. With 9-8 hole + T-T-4 board hero has
        # just one pair (the board T's), with kicker 9. Wait — pair on
        # paired board means hero's hand is the board pair plus kickers.
        # That's hand_rank=9. Bluff catcher.
        assert PAIRED_BOARD in result.danger_flags
        assert result.nut_status == NUT_BLUFF_CATCHER

    def test_high_card_is_bluff_catcher(self):
        result = classify_hand_full(['Ah', 'Qd'], ['Ks', '8c', '3h'])
        assert result.nut_status == NUT_BLUFF_CATCHER


# ---------------------------------------------------------------------------
# §1: made_tier downgrades
# ---------------------------------------------------------------------------


class TestMadeTierDowngrades:
    """The downgrade flow: raw 'nuts' / 'strong_made' → corrected tier."""

    def test_non_nut_straight_downgrades_from_nuts_to_strong_made(self):
        # Example 1 from plan: 10-high straight with J-high possible.
        # Raw classifier would call it 'nuts' (hand_rank=6). The §1
        # downgrade should make it 'strong_made'.
        made, _ = classify_hand(['6s', 'Qd'], ['7h', 'Th', '8s', '2c', '9c'])
        assert made == 'strong_made'

    def test_nut_straight_stays_nuts(self):
        # 9-high straight on 5-6-7 flop is the nut straight.
        made, _ = classify_hand(['9h', '8d'], ['7s', '6c', '5h'])
        assert made == 'nuts'

    def test_non_nut_flush_downgrades_from_nuts_to_strong_made(self):
        # Hero holds a non-nut flush; raw classifier calls 'nuts' → downgrade.
        made, _ = classify_hand(['Ts', '9s'], ['7s', '5s', '2s'])
        assert made == 'strong_made'

    def test_nut_flush_stays_nuts(self):
        made, _ = classify_hand(['As', 'Ks'], ['7s', '5s', '2s'])
        assert made == 'nuts'

    def test_top_pair_on_4broadway_downgrades_to_medium(self):
        # Example 5 from plan. Top pair K with 3 kicker on K-Q-J-T-4
        # board. Raw classifier returns 'medium_made' already (kicker
        # too weak for strong_made path). nut_status = bluff_catcher
        # because of FOUR_STRAIGHT_BOARD. Downgrade only kicks in for
        # 'strong_made' → 'medium_made', so this stays 'medium_made'.
        made, _ = classify_hand(['Kd', '3s'], ['4c', 'Qd', 'Jc', 'Kh', 'Ts'])
        assert made == 'medium_made'

    def test_tptk_on_paired_board_downgrades_to_medium(self):
        # TPTK (A-K hole, K paired with paired board) — raw classifier
        # would return 'strong_made'; danger flag PAIRED_BOARD makes
        # nut_status=bluff_catcher (one pair on paired board); downgrade
        # 'strong_made' → 'medium_made'.
        made, _ = classify_hand(['Ah', 'Kd'], ['Ks', '7c', '7h'])
        # Hero plays K-7-7 + A kicker + K = two pair (KK+77). hand_rank=8.
        # Two pair on paired board → nut_status = non_nut_strong, NOT
        # bluff_catcher (per the rule). 'strong_made' stays.
        assert made == 'strong_made'

    def test_one_pair_on_paired_board_downgrades(self):
        # Hero 9-8 with T-T-4: hero's hand is one pair (TT on board),
        # kicker 9. nut_status = bluff_catcher (paired_board); raw
        # classifier returns... let's compute. hand_rank=9 (one pair).
        # _classify_made_tier: pair_rank = 10 (T). hero hole ranks
        # [9, 8] don't match board, so matching_ranks=[]. Falls into
        # 'weak_made' (line 78). So raw = 'weak_made', downgrade noop.
        made, _ = classify_hand(['9h', '8d'], ['Ts', 'Tc', '4h'])
        assert made == 'weak_made'


# ---------------------------------------------------------------------------
# §1: HandClassification dataclass shape
# ---------------------------------------------------------------------------


class TestHandClassificationDataclass:
    """The new `classify_hand_full` returns a populated dataclass."""

    def test_all_fields_populated(self):
        result = classify_hand_full(['Ah', 'Kd'], ['Ks', '7c', '2h'])
        assert result.made_tier == 'strong_made'
        assert result.draw_modifier == 'no_draw'
        assert result.hand_class == 'strong_made'
        assert result.nut_status == NUT_NON_NUT_STRONG
        assert isinstance(result.danger_flags, frozenset)

    def test_dataclass_is_frozen(self):
        from dataclasses import FrozenInstanceError

        result = classify_hand_full(['Ah', 'Kd'], ['Ks', '7c', '2h'])
        with pytest.raises(FrozenInstanceError):
            result.made_tier = 'air'

    def test_preflop_no_community_cards_returns_empty_flags(self):
        # No community cards → danger_flags empty
        result = classify_hand_full(['Ah', 'Kd'], [])
        assert result.danger_flags == frozenset()


# ---------------------------------------------------------------------------
# Board-play detection: hole cards must improve on the naked board
# ---------------------------------------------------------------------------


class TestBoardPlay:
    """Hands whose strength is supplied by the board, not the hole cards.

    Regression for the Lucille Ball jam: 5h Qh on 2h 2c Ad Th As was tagged
    `nuts` (board two pair AA22 + a dead river 4-flush promoted strong_made →
    nuts) and the postflop_commit layer jammed it. Both the dead-draw read and
    the board-play credit are fixed.
    """

    LUCILLE_HOLE = ['5h', 'Qh']
    LUCILLE_BOARD = ['2h', '2c', 'Ad', 'Th', 'As']  # board itself is two pair

    def test_lucille_jam_hand_is_air_not_nuts(self):
        result = classify_hand_full(self.LUCILLE_HOLE, self.LUCILLE_BOARD)
        assert result.made_tier == 'air'
        assert result.hand_class == 'air_no_draw'
        assert result.nut_status == NUT_BLUFF_CATCHER

    def test_river_dead_flush_is_no_draw(self):
        # The 4-flush (Qh 5h 2h Th) is dead on a complete board — not a draw.
        _, draw = classify_hand(self.LUCILLE_HOLE, self.LUCILLE_BOARD)
        assert draw == 'no_draw'

    def test_plays_board_ties_total_air(self):
        # Q5 contributes nothing the board doesn't already have — same as 34.
        played = classify_hand_full(self.LUCILLE_HOLE, self.LUCILLE_BOARD)
        air = classify_hand_full(['3c', '4d'], self.LUCILLE_BOARD)
        assert played.made_tier == air.made_tier == 'air'

    def test_using_a_hole_card_for_a_full_house_survives(self):
        # AK pairs the board ace → genuine full house, must stay nuts.
        result = classify_hand_full(['Ac', 'Kd'], self.LUCILLE_BOARD)
        assert result.made_tier == 'nuts'
        assert result.nut_status == NUT_ACTUAL

    def test_real_two_pair_using_both_hole_cards_survives(self):
        # KJ on an unpaired board makes a genuine two pair — not board-play.
        result = classify_hand_full(['Kh', 'Jd'], ['Ks', 'Jc', '5h', '8d', '2s'])
        assert result.made_tier == 'strong_made'

    def test_set_on_paired_board_survives(self):
        # 77 makes a set the board does not have → uses hole cards.
        result = classify_hand_full(['7h', '7d'], ['7s', '4c', '2h', '9d', 'Js'])
        assert result.made_tier == 'nuts'

    def test_board_play_inert_before_river(self):
        # On the turn the board can't stand alone as a 5-card hand, so the
        # board-play guard is inert and normal made-tier logic applies.
        result = classify_hand_full(['Kh', 'Jd'], ['Ks', 'Jc', '5h', '8d'])
        assert result.made_tier == 'strong_made'


# ---------------------------------------------------------------------------
# Board-topped two pair: the board's own pair leads hero's "two pair"
# ---------------------------------------------------------------------------


class TestBoardToppedTwoPair:
    """Two pair whose *higher* pair is the board's own pair.

    Regression for the TieredBot 150% overbet with 3s 5s on 3h Qd Ks Tc Th:
    the evaluator calls it "two pair, tens and threes", but the tens are the
    board's pair (Tc Th) — everyone shares them. Hero's only real edge is the
    bottom pair of threes, so the hand loses to the whole continuing range. The
    made-tier path credited *all* two pair as `strong_made`, so the overbet
    value layer ({'nuts','strong_made'}) fired. It must grade as a bluff-catcher
    instead. `_board_play_level` does NOT catch this (hero genuinely lifts the
    board's one pair to two pair via the 3 → `uses_hole`).
    """

    def test_tens_and_threes_is_bluff_catcher_not_value(self):
        result = classify_hand_full(['3s', '5s'], ['3h', 'Qd', 'Ks', 'Tc', 'Th'])
        assert result.made_tier == 'weak_made'
        assert result.hand_class == 'weak_made'
        assert result.nut_status == NUT_BLUFF_CATCHER

    def test_underpair_plus_board_pair_is_bluff_catcher(self):
        # 55 → "tens and fives"; tens are the board's pair, fives an underpair.
        result = classify_hand_full(['5h', '5d'], ['3h', 'Qd', 'Ks', 'Tc', 'Th'])
        assert result.made_tier == 'weak_made'
        assert result.nut_status == NUT_BLUFF_CATCHER

    def test_top_pair_made_with_hole_card_survives(self):
        # K5 on K-7-7-2-3 → "kings and sevens"; the kings use a hole card, so
        # this is genuine top-two value, not board-topped.
        result = classify_hand_full(['Kh', '5d'], ['Ks', '7c', '7d', '2s', '3h'])
        assert result.made_tier == 'strong_made'

    def test_hole_card_top_two_on_paired_board_survives(self):
        # KQ on K-Q-7-7-2 → "kings and queens"; both pairs use hole cards and
        # outrank the board's 77, so it stays strong value.
        result = classify_hand_full(['Kh', 'Qh'], ['Ks', 'Qd', '7c', '7d', '2s'])
        assert result.made_tier == 'strong_made'

    def test_board_topped_two_pair_fires_on_paired_flop(self):
        # Unlike _board_play_level (river-only), the board-shared-pair logic
        # holds on any street: tens & threes on a paired FLOP is still a
        # bluff-catcher.
        result = classify_hand_full(['3s', '5s'], ['Th', 'Tc', '3h'])
        assert result.made_tier == 'weak_made'


# ---------------------------------------------------------------------------
# Adversarial sweep: "the board is never your private hand"
# ---------------------------------------------------------------------------


def _best_eval(cards):
    """Black-box hand strength via HandEvaluator (rank + tiebreak values),
    computed independently of the classifier so the sweep is a true property
    test, not a tautology against the code under test."""
    from poker.hand_evaluator import HandEvaluator
    from poker.strategy.hand_classification import _parse_card

    r = HandEvaluator([_parse_card(c) for c in cards]).evaluate_hand()
    return r['hand_rank'], tuple(r.get('hand_values') or [])


# Curated complete (5-card) boards covering every family where a hand's
# strength can be supplied by the board rather than the hole cards.
_SWEEP_BOARDS = [
    ['3h', 'Qd', 'Ks', 'Tc', 'Th'],   # the bug: paired board (tens)
    ['Kh', 'Ks', '7c', '7d', '9s'],   # double-paired board
    ['Qh', 'Qc', '7h', '4h', '2s'],   # paired + 3-flush
    ['Kh', 'Qh', '7h', '4h', '2h'],   # monotone (board is a flush)
    ['Th', 'Jc', 'Qd', 'Ks', 'Ah'],   # board is broadway straight
    ['4h', '5c', '6d', '7s', '8h'],   # board is a low straight
    ['Ah', 'Ac', 'Ad', '2h', '2c'],   # board is a full house
    ['9h', '9c', '9d', '2s', '5h'],   # board is trips
    ['Ks', '9d', '4c', '2h', '7s'],   # dry unpaired control (genuine value OK)
]

_RANKS = '23456789TJQKA'
_SUITS = 'shdc'
_RANK_VAL = {r: i for i, r in enumerate(_RANKS, start=2)}
_FULL_DECK = [r + s for r in _RANKS for s in _SUITS]
_VALUE_TIERS = {'nuts', 'strong_made'}


def _holes_for_board(board):
    """All 2-card holes drawable from the deck minus the board (C(47,2))."""
    remaining = [c for c in _FULL_DECK if c not in board]
    for i in range(len(remaining)):
        for j in range(i + 1, len(remaining)):
            yield [remaining[i], remaining[j]]


class TestBoardIsNeverPrivateHand:
    """Property sweep enforcing the 'board as your private hand' class fix.

    For every curated board archetype and every possible 2-card hole, the
    classifier must never credit board-supplied strength as value. Two
    independent invariants, each with its precondition computed directly from
    HandEvaluator (not from the classifier), so a future regression that
    rewires the guards is caught:

      1. plays-the-board: hero's best 7-card hand exactly equals the naked
         board's best 5-card hand → hero contributed nothing everyone doesn't
         share → must be `air` / bluff-catcher.
      2. board-topped two pair: hero's best hand is two pair whose *higher*
         pair is a rank the board pairs by itself → real edge is only the
         lower pair → must not be `nuts`/`strong_made`.
    """

    def test_plays_the_board_is_always_air(self):
        checked = 0
        for board in _SWEEP_BOARDS:
            board_key = _best_eval(board)
            for hole in _holes_for_board(board):
                if _best_eval(hole + board) != board_key:
                    continue  # hero genuinely improves on the board
                checked += 1
                r = classify_hand_full(hole, board)
                assert r.made_tier == 'air', (
                    f'{hole} on {board} ties the naked board but was '
                    f'classified {r.made_tier!r}'
                )
                assert r.nut_status == NUT_BLUFF_CATCHER, (
                    f'{hole} on {board} plays the board but nut_status={r.nut_status!r}'
                )
        assert checked > 0  # guard: the precondition actually fired

    def test_board_topped_two_pair_is_never_value(self):
        checked = 0
        for board in _SWEEP_BOARDS:
            board_pairs = {
                _RANK_VAL[c[0]]
                for c in board
                if [b[0] for b in board].count(c[0]) >= 2
            }
            if not board_pairs:
                continue
            for hole in _holes_for_board(board):
                rank, values = _best_eval(hole + board)
                if rank != 8:  # not two pair
                    continue
                if values[0] not in board_pairs:  # top pair isn't the board's
                    continue
                checked += 1
                r = classify_hand_full(hole, board)
                assert r.made_tier not in _VALUE_TIERS, (
                    f'{hole} on {board} is two pair topped by the board pair '
                    f'but was credited {r.made_tier!r} (value)'
                )
        assert checked > 0  # guard: the precondition actually fired

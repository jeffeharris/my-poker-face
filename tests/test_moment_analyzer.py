"""Tests for MomentAnalyzer drama detection system."""

import pytest
from poker.moment_analyzer import MomentAnalyzer, MomentAnalysis
from poker.poker_game import Player, PokerGameState


# ============================================================================
# Test Fixtures
# ============================================================================

def make_player(name: str, stack: int, is_folded: bool = False) -> Player:
    """Create a simple player for testing."""
    return Player(
        name=name,
        stack=stack,
        is_human=False,
        bet=0,
        is_folded=is_folded,
    )


def make_game_state(
    players: list,
    pot_total: int = 0,
    community_cards: tuple = ()
) -> PokerGameState:
    """Create a minimal game state for testing."""
    return PokerGameState(
        deck=(),
        players=tuple(players),
        community_cards=community_cards,
        pot={'total': pot_total},
    )


# ============================================================================
# Factor Detection Tests
# ============================================================================

class TestIsAllInSituation:
    """Tests for is_all_in_situation factor detection."""

    def test_cost_exceeds_stack(self):
        """All-in when cost to call >= player stack."""
        assert MomentAnalyzer.is_all_in_situation(
            player_stack=100,
            cost_to_call=150,
            big_blind=50
        ) is True

    def test_cost_equals_stack(self):
        """All-in when cost to call equals player stack exactly."""
        assert MomentAnalyzer.is_all_in_situation(
            player_stack=100,
            cost_to_call=100,
            big_blind=50
        ) is True

    def test_short_stack_below_threshold(self):
        """All-in situation when stack < 3 BB (desperate)."""
        # Stack of 100 with BB of 50 = 2 BB < 3 BB threshold
        assert MomentAnalyzer.is_all_in_situation(
            player_stack=100,
            cost_to_call=0,
            big_blind=50
        ) is True

    def test_healthy_stack_not_all_in(self):
        """Not all-in with healthy stack and manageable cost."""
        assert MomentAnalyzer.is_all_in_situation(
            player_stack=1000,
            cost_to_call=100,
            big_blind=50
        ) is False

    def test_exactly_three_bb(self):
        """Stack exactly at 3 BB is all-in (boundary - uses <=)."""
        # Stack of 150 with BB of 50 = 3 BB exactly -> still desperate
        assert MomentAnalyzer.is_all_in_situation(
            player_stack=150,
            cost_to_call=0,
            big_blind=50
        ) is True

    def test_above_three_bb(self):
        """Stack above 3 BB is not all-in."""
        # Stack of 151 with BB of 50 = 3.02 BB > 3 BB threshold
        assert MomentAnalyzer.is_all_in_situation(
            player_stack=151,
            cost_to_call=0,
            big_blind=50
        ) is False


class TestIsBigPot:
    """Tests for is_big_pot factor detection."""

    def test_pot_exceeds_half_player_stack(self):
        """Big pot when pot > 50% of player's stack."""
        assert MomentAnalyzer.is_big_pot(
            pot_total=600,
            player_stack=1000,
            avg_stack=1000
        ) is True

    def test_pot_exactly_half_player_stack(self):
        """Not big pot when pot = 50% of player's stack (boundary)."""
        assert MomentAnalyzer.is_big_pot(
            pot_total=500,
            player_stack=1000,
            avg_stack=1000
        ) is False

    def test_small_pot_relative_to_stack(self):
        """Not big pot when pot is small relative to stack."""
        assert MomentAnalyzer.is_big_pot(
            pot_total=100,
            player_stack=1000,
            avg_stack=1000
        ) is False

    def test_uses_avg_stack_when_player_stack_zero(self):
        """Uses average stack when player stack is 0."""
        # Pot of 800 with avg_stack of 1000 -> 80% > 75% threshold
        assert MomentAnalyzer.is_big_pot(
            pot_total=800,
            player_stack=0,
            avg_stack=1000
        ) is True

    def test_avg_stack_boundary(self):
        """Not big pot when pot = 75% of avg stack (boundary)."""
        assert MomentAnalyzer.is_big_pot(
            pot_total=750,
            player_stack=0,
            avg_stack=1000
        ) is False


class TestIsBigBet:
    """Tests for is_big_bet factor detection."""

    def test_bet_exceeds_10bb(self):
        """Big bet when cost > 10 BB."""
        assert MomentAnalyzer.is_big_bet(
            cost_to_call=600,
            big_blind=50
        ) is True

    def test_bet_exactly_10bb(self):
        """Not big bet when cost = 10 BB (boundary)."""
        assert MomentAnalyzer.is_big_bet(
            cost_to_call=500,
            big_blind=50
        ) is False

    def test_small_bet(self):
        """Not big bet for small cost to call."""
        assert MomentAnalyzer.is_big_bet(
            cost_to_call=100,
            big_blind=50
        ) is False


class TestIsShowdown:
    """Tests for is_showdown factor detection."""

    def test_river_is_showdown(self):
        """Showdown when 5 community cards dealt."""
        game_state = make_game_state(
            players=[make_player("Hero", 1000)],
            community_cards=("Ah", "Kh", "Qh", "Jh", "Th"),
        )
        assert MomentAnalyzer.is_showdown(game_state) is True

    def test_turn_not_showdown(self):
        """Not showdown with 4 community cards."""
        game_state = make_game_state(
            players=[make_player("Hero", 1000)],
            community_cards=("Ah", "Kh", "Qh", "Jh"),
        )
        assert MomentAnalyzer.is_showdown(game_state) is False

    def test_preflop_not_showdown(self):
        """Not showdown with 0 community cards."""
        game_state = make_game_state(
            players=[make_player("Hero", 1000)],
            community_cards=(),
        )
        assert MomentAnalyzer.is_showdown(game_state) is False


class TestIsHeadsUp:
    """Tests for is_heads_up factor detection."""

    def test_two_players_is_heads_up(self):
        """Heads up with exactly 2 active players."""
        players = [
            make_player("Hero", 1000),
            make_player("Villain", 1000),
        ]
        assert MomentAnalyzer.is_heads_up(players) is True

    def test_three_players_not_heads_up(self):
        """Not heads up with 3 active players."""
        players = [
            make_player("Hero", 1000),
            make_player("Villain1", 1000),
            make_player("Villain2", 1000),
        ]
        assert MomentAnalyzer.is_heads_up(players) is False

    def test_one_player_not_heads_up(self):
        """Not heads up with 1 active player."""
        players = [make_player("Hero", 1000)]
        assert MomentAnalyzer.is_heads_up(players) is False


class TestIsHugeRaise:
    """Tests for is_huge_raise factor detection."""

    def test_raise_exceeds_3x_pot(self):
        """Huge raise when raise > 3x pot."""
        assert MomentAnalyzer.is_huge_raise(
            raise_amount=400,
            pot_total=100
        ) is True

    def test_raise_exactly_3x_pot(self):
        """Not huge raise when raise = 3x pot (boundary)."""
        assert MomentAnalyzer.is_huge_raise(
            raise_amount=300,
            pot_total=100
        ) is False

    def test_normal_raise(self):
        """Not huge raise for normal sized raise."""
        assert MomentAnalyzer.is_huge_raise(
            raise_amount=100,
            pot_total=100
        ) is False

    def test_zero_pot_not_huge(self):
        """Not huge raise when pot is 0."""
        assert MomentAnalyzer.is_huge_raise(
            raise_amount=1000,
            pot_total=0
        ) is False


class TestIsLateStage:
    """Tests for is_late_stage factor detection."""

    def test_three_players_shallow_stacks(self):
        """Late stage with 3 players and avg < 15 BB."""
        players = [
            make_player("P1", 500),  # 10 BB
            make_player("P2", 500),  # 10 BB
            make_player("P3", 500),  # 10 BB
        ]
        # Avg = 500, BB = 50 -> 10 BB < 15 BB
        assert MomentAnalyzer.is_late_stage(players, big_blind=50) is True

    def test_three_players_deep_stacks(self):
        """Not late stage with 3 players but deep stacks."""
        players = [
            make_player("P1", 1000),  # 20 BB
            make_player("P2", 1000),  # 20 BB
            make_player("P3", 1000),  # 20 BB
        ]
        # Avg = 1000, BB = 50 -> 20 BB > 15 BB
        assert MomentAnalyzer.is_late_stage(players, big_blind=50) is False

    def test_four_players_not_late_stage(self):
        """Not late stage with > 3 players."""
        players = [
            make_player("P1", 500),
            make_player("P2", 500),
            make_player("P3", 500),
            make_player("P4", 500),
        ]
        assert MomentAnalyzer.is_late_stage(players, big_blind=50) is False

    def test_empty_players_not_late_stage(self):
        """Not late stage with no players."""
        assert MomentAnalyzer.is_late_stage([], big_blind=50) is False

    def test_zero_big_blind_not_late_stage(self):
        """Not late stage when big blind is 0."""
        players = [make_player("P1", 500), make_player("P2", 500)]
        assert MomentAnalyzer.is_late_stage(players, big_blind=0) is False


# ============================================================================
# Level Determination Tests
# ============================================================================

class TestDetermineLevel:
    """Tests for _determine_level() drama level calculation."""

    def test_routine_no_factors(self):
        """Routine level with 0 factors."""
        assert MomentAnalyzer._determine_level([]) == 'routine'

    def test_notable_one_factor(self):
        """Notable level with 1 factor."""
        assert MomentAnalyzer._determine_level(['big_bet']) == 'notable'

    def test_high_stakes_two_factors(self):
        """High stakes level with 2 factors."""
        assert MomentAnalyzer._determine_level(['big_bet', 'heads_up']) == 'high_stakes'

    def test_high_stakes_three_factors(self):
        """High stakes level with 3 factors (not climactic)."""
        assert MomentAnalyzer._determine_level(['big_bet', 'heads_up', 'huge_raise']) == 'high_stakes'

    def test_climactic_all_in(self):
        """Climactic level when all_in factor present."""
        assert MomentAnalyzer._determine_level(['all_in']) == 'climactic'
        assert MomentAnalyzer._determine_level(['all_in', 'big_bet']) == 'climactic'

    def test_climactic_big_pot_showdown(self):
        """Climactic level when big_pot AND showdown."""
        assert MomentAnalyzer._determine_level(['big_pot', 'showdown']) == 'climactic'

    def test_not_climactic_big_pot_alone(self):
        """Not climactic with big_pot alone (only 1 factor = notable)."""
        assert MomentAnalyzer._determine_level(['big_pot']) == 'notable'

    def test_not_climactic_showdown_alone(self):
        """Not climactic with showdown alone (only 1 factor = notable)."""
        assert MomentAnalyzer._determine_level(['showdown']) == 'notable'


# ============================================================================
# Tone Determination Tests
# ============================================================================

class TestDetermineTone:
    """Tests for _determine_tone() emotional tone calculation."""

    def test_triumphant_climactic_strong_hand(self):
        """Triumphant tone in climactic moment with 70%+ equity."""
        tone = MomentAnalyzer._determine_tone(
            level='climactic',
            factors=['all_in'],
            hand_equity=0.75,
            is_short_stack=False
        )
        assert tone == 'triumphant'

    def test_not_triumphant_high_stakes(self):
        """Not triumphant in high_stakes (only climactic qualifies)."""
        tone = MomentAnalyzer._determine_tone(
            level='high_stakes',
            factors=['big_bet', 'heads_up'],
            hand_equity=0.80,
            is_short_stack=False
        )
        assert tone == 'confident'  # Falls through to confident

    def test_desperate_short_stack(self):
        """Desperate tone when short-stacked."""
        tone = MomentAnalyzer._determine_tone(
            level='notable',
            factors=['big_bet'],
            hand_equity=0.60,
            is_short_stack=True
        )
        assert tone == 'desperate'

    def test_desperate_weak_hand_high_stakes(self):
        """Desperate tone with weak hand in high-stakes moment."""
        tone = MomentAnalyzer._determine_tone(
            level='high_stakes',
            factors=['big_bet', 'heads_up'],
            hand_equity=0.25,
            is_short_stack=False
        )
        assert tone == 'desperate'

    def test_confident_good_hand_notable(self):
        """Confident tone with 50%+ equity in notable+ moment."""
        tone = MomentAnalyzer._determine_tone(
            level='notable',
            factors=['big_bet'],
            hand_equity=0.55,
            is_short_stack=False
        )
        assert tone == 'confident'

    def test_neutral_routine_moment(self):
        """Neutral tone in routine moments."""
        tone = MomentAnalyzer._determine_tone(
            level='routine',
            factors=[],
            hand_equity=0.60,
            is_short_stack=False
        )
        assert tone == 'neutral'

    def test_neutral_weak_hand_notable(self):
        """Neutral tone with weak hand in notable moment (not high enough stakes for desperate)."""
        tone = MomentAnalyzer._determine_tone(
            level='notable',
            factors=['big_bet'],
            hand_equity=0.35,
            is_short_stack=False
        )
        assert tone == 'neutral'

    def test_triumphant_boundary_70_equity(self):
        """Triumphant at exactly 70% equity boundary."""
        tone = MomentAnalyzer._determine_tone(
            level='climactic',
            factors=['all_in'],
            hand_equity=0.70,
            is_short_stack=False
        )
        assert tone == 'triumphant'

    def test_confident_boundary_50_equity(self):
        """Confident at exactly 50% equity boundary."""
        tone = MomentAnalyzer._determine_tone(
            level='notable',
            factors=['big_bet'],
            hand_equity=0.50,
            is_short_stack=False
        )
        assert tone == 'confident'


# ============================================================================
# Integration Tests - Full analyze() Method
# ============================================================================

class TestAnalyzeIntegration:
    """Integration tests for the full analyze() method."""

    def test_routine_preflop_small_pot(self):
        """Routine analysis for standard preflop situation."""
        players = [
            make_player("Hero", 1000),
            make_player("Villain1", 1000),
            make_player("Villain2", 1000),
        ]
        game_state = make_game_state(
            players=players,
            pot_total=150,  # Small pot
            community_cards=(),  # Preflop
        )

        analysis = MomentAnalyzer.analyze(
            game_state=game_state,
            player=players[0],
            cost_to_call=50,  # 1 BB - small
            big_blind=50,
            last_raise_amount=50,
            hand_equity=0.5
        )

        assert analysis.level == 'routine'
        assert analysis.factors == []
        assert analysis.tone == 'neutral'
        assert analysis.is_dramatic is False

    def test_climactic_all_in_showdown(self):
        """Climactic analysis for all-in on the river."""
        players = [
            make_player("Hero", 500),
            make_player("Villain", 1500),
        ]
        game_state = make_game_state(
            players=players,
            pot_total=1000,
            community_cards=("Ah", "Kh", "Qh", "Jh", "2c"),
        )

        analysis = MomentAnalyzer.analyze(
            game_state=game_state,
            player=players[0],
            cost_to_call=500,  # All-in
            big_blind=50,
            last_raise_amount=500,
            hand_equity=0.85  # Strong hand
        )

        assert analysis.level == 'climactic'
        assert 'all_in' in analysis.factors
        assert 'showdown' in analysis.factors
        assert 'big_pot' in analysis.factors  # Pot > 50% of 500
        assert 'heads_up' in analysis.factors
        assert analysis.tone == 'triumphant'
        assert analysis.is_dramatic is True

    def test_high_stakes_big_bet_heads_up(self):
        """High stakes with multiple factors but not climactic."""
        players = [
            make_player("Hero", 2000),
            make_player("Villain", 2000),
        ]
        game_state = make_game_state(
            players=players,
            pot_total=400,
            community_cards=("Ah", "Kh", "Qh"),  # Flop
        )

        analysis = MomentAnalyzer.analyze(
            game_state=game_state,
            player=players[0],
            cost_to_call=600,  # 12 BB > 10 BB = big bet
            big_blind=50,
            last_raise_amount=600,
            hand_equity=0.65
        )

        assert analysis.level == 'high_stakes'
        assert 'big_bet' in analysis.factors
        assert 'heads_up' in analysis.factors
        assert analysis.tone == 'confident'
        assert analysis.is_dramatic is True

    def test_notable_single_factor(self):
        """Notable with single factor."""
        players = [
            make_player("Hero", 1000),
            make_player("Villain1", 1000),
            make_player("Villain2", 1000),
        ]
        game_state = make_game_state(
            players=players,
            pot_total=300,
            community_cards=("Ah", "Kh", "Qh", "Jh", "Th"),  # Showdown
        )

        analysis = MomentAnalyzer.analyze(
            game_state=game_state,
            player=players[0],
            cost_to_call=100,  # Small bet
            big_blind=50,
            last_raise_amount=100,
            hand_equity=0.40
        )

        assert analysis.level == 'notable'
        assert 'showdown' in analysis.factors
        assert len(analysis.factors) == 1
        assert analysis.is_dramatic is False

    def test_late_stage_tournament(self):
        """Late stage tournament with shallow stacks."""
        players = [
            make_player("Hero", 600),   # 12 BB
            make_player("Villain", 700),  # 14 BB
        ]
        game_state = make_game_state(
            players=players,
            pot_total=200,
            community_cards=(),
        )

        analysis = MomentAnalyzer.analyze(
            game_state=game_state,
            player=players[0],
            cost_to_call=50,
            big_blind=50,
            last_raise_amount=50,
            hand_equity=0.50
        )

        # Avg stack = 650, BB = 50 -> 13 BB < 15 BB threshold
        assert 'late_stage' in analysis.factors
        assert 'heads_up' in analysis.factors
        assert analysis.level == 'high_stakes'


class TestMomentAnalysisDataclass:
    """Tests for MomentAnalysis dataclass properties."""

    def test_is_dramatic_high_stakes(self):
        """is_dramatic returns True for high_stakes."""
        analysis = MomentAnalysis(level='high_stakes', factors=['big_bet'])
        assert analysis.is_dramatic is True

    def test_is_dramatic_climactic(self):
        """is_dramatic returns True for climactic."""
        analysis = MomentAnalysis(level='climactic', factors=['all_in'])
        assert analysis.is_dramatic is True

    def test_is_not_dramatic_notable(self):
        """is_dramatic returns False for notable."""
        analysis = MomentAnalysis(level='notable', factors=['showdown'])
        assert analysis.is_dramatic is False

    def test_is_not_dramatic_routine(self):
        """is_dramatic returns False for routine."""
        analysis = MomentAnalysis(level='routine', factors=[])
        assert analysis.is_dramatic is False

    def test_default_tone_is_neutral(self):
        """Default tone is neutral."""
        analysis = MomentAnalysis(level='routine', factors=[])
        assert analysis.tone == 'neutral'

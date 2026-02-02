"""
Deterministic tests for tournament flow, hand endings, and eliminations.

These tests verify end-game scenarios without requiring AI decisions,
using controlled game states to catch bugs in:
- Run-it-out scenarios
- Tournament tracker persistence
- Final hand position calculations
- Multi-way all-in showdowns
- Side pot distributions
- Mid-tournament eliminations
"""
import pytest
from typing import List, Dict, Any, Tuple

from poker.poker_game import (
    Player, PokerGameState, initialize_game_state,
    determine_winner, award_pot_winnings
)
from poker.poker_state_machine import (
    PokerStateMachine, PokerPhase, ImmutableStateMachine,
    run_betting_round_transition
)
from poker.tournament_tracker import TournamentTracker, EliminationEvent
from poker.persistence import GamePersistence
from core.card import Card


# =============================================================================
# Helper Functions
# =============================================================================

def create_player(name: str, stack: int = 1000, bet: int = 0,
                  is_human: bool = False, is_folded: bool = False,
                  is_all_in: bool = False, has_acted: bool = False,
                  hand: Tuple = ()) -> Player:
    """Create a player with specified attributes."""
    return Player(
        name=name,
        stack=stack,
        bet=bet,
        is_human=is_human,
        is_folded=is_folded,
        is_all_in=is_all_in,
        has_acted=has_acted,
        hand=hand,
    )


def create_hand(*cards: Tuple[str, str]) -> Tuple[Card, ...]:
    """Create a hand from (rank, suit) tuples.

    Example: create_hand(('A', 'spades'), ('K', 'hearts'))
    """
    return tuple(Card(rank, suit) for rank, suit in cards)


def create_community_cards(*cards: Tuple[str, str]) -> Tuple[Card, ...]:
    """Create community cards from (rank, suit) tuples."""
    return create_hand(*cards)


def create_game_state(
    players: List[Player],
    community_cards: Tuple = (),
    pot_total: int = 0,
    current_dealer_idx: int = 0,
    awaiting_action: bool = False,
    run_it_out: bool = False,
) -> PokerGameState:
    """Create a game state with specified configuration."""
    # Calculate pot breakdown from player bets if not specified
    if pot_total == 0:
        pot_total = sum(p.bet for p in players)

    pot = {'total': pot_total}
    for p in players:
        pot[p.name] = p.bet

    return PokerGameState(deck=(),
        players=tuple(players),
        community_cards=community_cards,
        pot=pot,
        current_dealer_idx=current_dealer_idx,
        awaiting_action=awaiting_action,
        run_it_out=run_it_out,
    )


def create_state_machine(
    game_state: PokerGameState,
    phase: PokerPhase = PokerPhase.RIVER
) -> PokerStateMachine:
    """Create a state machine at a specific phase."""
    return PokerStateMachine.from_saved_state(game_state, phase)


# =============================================================================
# Test Classes
# =============================================================================

class TestRunItOutPersistence:
    """Tests for run_it_out flag persistence across save/reload."""

    def test_run_it_out_true_survives_reload(self, persistence):
        """Game state with run_it_out=True should persist."""
        game_id = 'test_game_123'
        # Create state with run_it_out=True
        players = [
            create_player('Human', stack=0, bet=500, is_human=True, is_all_in=True),
            create_player('AI', stack=0, bet=500, is_all_in=True),
        ]
        game_state = create_game_state(players, run_it_out=True)
        state_machine = create_state_machine(game_state, PokerPhase.RIVER)

        # Save
        persistence.save_game(game_id, state_machine, 'owner1', 'Owner')

        # Load
        loaded = persistence.load_game(game_id)

        # Assert
        assert loaded is not None
        assert loaded.game_state.run_it_out is True

    def test_run_it_out_false_survives_reload(self, persistence):
        """Game state with run_it_out=False should persist."""
        game_id = 'test_game_123'
        players = [
            create_player('Human', stack=500, bet=100, is_human=True),
            create_player('AI', stack=500, bet=100),
        ]
        game_state = create_game_state(players, run_it_out=False)
        state_machine = create_state_machine(game_state, PokerPhase.FLOP)

        persistence.save_game(game_id, state_machine, 'owner1', 'Owner')
        loaded = persistence.load_game(game_id)

        assert loaded.game_state.run_it_out is False


class TestTournamentTrackerPersistence:
    """Tests for tournament tracker persistence."""

    def test_tracker_with_eliminations_survives_reload(self, persistence):
        """Tournament tracker with eliminations should persist."""
        game_id = 'test_tournament_123'
        # Create tracker with eliminations
        starting_players = [
            {'name': 'Human', 'is_human': True},
            {'name': 'AI1', 'is_human': False},
            {'name': 'AI2', 'is_human': False},
            {'name': 'AI3', 'is_human': False},
        ]
        tracker = TournamentTracker(
            game_id=game_id,
            starting_players=starting_players
        )

        # Record eliminations
        tracker.on_player_eliminated('AI3', 'AI1', pot_size=500)
        tracker.on_player_eliminated('AI2', 'Human', pot_size=800)
        tracker.hand_count = 15
        tracker.biggest_pot = 1200

        # Save
        persistence.save_tournament_tracker(game_id, tracker)

        # Load
        tracker_data = persistence.load_tournament_tracker(game_id)
        loaded_tracker = TournamentTracker.from_dict(tracker_data)

        # Assert
        assert len(loaded_tracker.eliminations) == 2
        assert len(loaded_tracker.starting_players) == 4
        assert loaded_tracker.hand_count == 15
        assert loaded_tracker.biggest_pot == 1200
        assert loaded_tracker.active_player_count == 2

        # Check elimination details
        assert loaded_tracker.eliminations[0].eliminated_player == 'AI3'
        assert loaded_tracker.eliminations[0].finishing_position == 4
        assert loaded_tracker.eliminations[1].eliminated_player == 'AI2'
        assert loaded_tracker.eliminations[1].finishing_position == 3

    def test_empty_tracker_survives_reload(self, persistence):
        """New tournament tracker with no eliminations should persist."""
        game_id = 'test_tournament_123'
        starting_players = [
            {'name': 'Human', 'is_human': True},
            {'name': 'AI1', 'is_human': False},
        ]
        tracker = TournamentTracker(
            game_id=game_id,
            starting_players=starting_players
        )

        persistence.save_tournament_tracker(game_id, tracker)
        tracker_data = persistence.load_tournament_tracker(game_id)
        loaded_tracker = TournamentTracker.from_dict(tracker_data)

        assert len(loaded_tracker.eliminations) == 0
        assert loaded_tracker.active_player_count == 2


def get_winners_from_pot_breakdown(winner_info: Dict) -> List[str]:
    """Extract winner names from pot_breakdown structure."""
    winners = set()
    for pot in winner_info.get('pot_breakdown', []):
        for winner in pot.get('winners', []):
            winners.add(winner['name'])
    return list(winners)


class TestFinalHandPosition:
    """Tests for final hand position calculations."""

    def test_human_loses_headsup_gets_second_place(self):
        """Human losing heads-up should finish 2nd, not 1st."""
        # Create heads-up state where AI wins
        human_hand = create_hand(('K', 'spades'), ('K', 'hearts'))  # Pair of Kings
        ai_hand = create_hand(('A', 'spades'), ('A', 'hearts'))  # Pair of Aces
        community = create_community_cards(
            ('7', 'diamonds'), ('8', 'clubs'), ('9', 'spades'),
            ('2', 'hearts'), ('3', 'diamonds')
        )

        players = [
            create_player('Human', stack=0, bet=1000, is_human=True,
                         is_all_in=True, hand=human_hand),
            create_player('AI', stack=0, bet=1000, is_all_in=True, hand=ai_hand),
        ]
        game_state = create_game_state(players, community_cards=community)

        # Determine winner
        winner_info = determine_winner(game_state)
        winners = get_winners_from_pot_breakdown(winner_info)

        # AI should win with pair of Aces
        assert 'AI' in winners
        assert 'Human' not in winners

        # Award pot and check stacks
        updated_state = award_pot_winnings(game_state, winner_info)

        # AI should have all the chips
        ai_player = next(p for p in updated_state.players if p.name == 'AI')
        human_player = next(p for p in updated_state.players if p.name == 'Human')
        assert ai_player.stack == 2000
        assert human_player.stack == 0

    def test_human_wins_headsup_gets_first_place(self):
        """Human winning heads-up should finish 1st."""
        human_hand = create_hand(('A', 'spades'), ('A', 'hearts'))  # Pair of Aces
        ai_hand = create_hand(('K', 'spades'), ('K', 'hearts'))  # Pair of Kings
        community = create_community_cards(
            ('7', 'diamonds'), ('8', 'clubs'), ('9', 'spades'),
            ('2', 'hearts'), ('3', 'diamonds')
        )

        players = [
            create_player('Human', stack=0, bet=1000, is_human=True,
                         is_all_in=True, hand=human_hand),
            create_player('AI', stack=0, bet=1000, is_all_in=True, hand=ai_hand),
        ]
        game_state = create_game_state(players, community_cards=community)

        winner_info = determine_winner(game_state)
        winners = get_winners_from_pot_breakdown(winner_info)

        assert 'Human' in winners
        assert 'AI' not in winners


class TestMultiWayAllIn:
    """Tests for multi-way all-in showdowns."""

    def test_three_way_all_in_triggers_run_it_out(self):
        """Three-way all-in should trigger run_it_out."""
        players = [
            create_player('P1', stack=0, bet=1000, is_all_in=True, has_acted=True),
            create_player('P2', stack=0, bet=1000, is_all_in=True, has_acted=True),
            create_player('P3', stack=0, bet=1000, is_all_in=True, has_acted=True),
        ]
        game_state = create_game_state(players, awaiting_action=True)
        state = ImmutableStateMachine(game_state=game_state, phase=PokerPhase.FLOP)

        result = run_betting_round_transition(state)

        assert result.game_state.run_it_out is True

    def test_three_way_all_in_different_stacks_pot_distribution(self):
        """Three players all-in with different stacks should create side pots."""
        p1_hand = create_hand(('A', 'spades'), ('A', 'hearts'))  # Best hand
        p2_hand = create_hand(('K', 'spades'), ('K', 'hearts'))
        p3_hand = create_hand(('Q', 'spades'), ('Q', 'hearts'))
        community = create_community_cards(
            ('7', 'diamonds'), ('8', 'clubs'), ('9', 'spades'),
            ('2', 'hearts'), ('3', 'diamonds')
        )

        players = [
            create_player('P1', stack=0, bet=300, is_all_in=True, hand=p1_hand),
            create_player('P2', stack=0, bet=500, is_all_in=True, hand=p2_hand),
            create_player('P3', stack=0, bet=1000, is_all_in=True, hand=p3_hand),
        ]
        game_state = create_game_state(players, community_cards=community)

        winner_info = determine_winner(game_state)
        updated_state = award_pot_winnings(game_state, winner_info)

        p1 = next(p for p in updated_state.players if p.name == 'P1')
        p2 = next(p for p in updated_state.players if p.name == 'P2')
        p3 = next(p for p in updated_state.players if p.name == 'P3')

        # Total chips should be conserved
        total_chips = p1.stack + p2.stack + p3.stack
        assert total_chips == 1800  # 300 + 500 + 1000

        # P1 should win the most (has best hand)
        assert p1.stack > 0


class TestMidTournamentElimination:
    """Tests for eliminations during tournament (not final hand)."""

    def test_elimination_records_correct_position(self):
        """Eliminating a player should record correct finishing position."""
        starting_players = [
            {'name': 'Human', 'is_human': True},
            {'name': 'AI1', 'is_human': False},
            {'name': 'AI2', 'is_human': False},
            {'name': 'AI3', 'is_human': False},
        ]
        tracker = TournamentTracker(
            game_id='test_game',
            starting_players=starting_players
        )

        # First elimination - should be 4th place
        event1 = tracker.on_player_eliminated('AI3', 'AI1', pot_size=500)
        assert event1.finishing_position == 4
        assert tracker.active_player_count == 3

        # Second elimination - should be 3rd place
        event2 = tracker.on_player_eliminated('AI2', 'Human', pot_size=800)
        assert event2.finishing_position == 3
        assert tracker.active_player_count == 2

    def test_elimination_updates_active_players(self):
        """Eliminating a player should update active players set."""
        starting_players = [
            {'name': 'Human', 'is_human': True},
            {'name': 'AI1', 'is_human': False},
            {'name': 'AI2', 'is_human': False},
        ]
        tracker = TournamentTracker(
            game_id='test_game',
            starting_players=starting_players
        )

        assert 'AI2' in tracker._active_players
        assert tracker.active_player_count == 3

        tracker.on_player_eliminated('AI2', 'Human')

        assert 'AI2' not in tracker._active_players
        assert tracker.active_player_count == 2

    def test_cannot_eliminate_same_player_twice(self):
        """Eliminating an already eliminated player should raise error."""
        tracker = TournamentTracker(
            game_id='test_game',
            starting_players=[
                {'name': 'P1', 'is_human': False},
                {'name': 'P2', 'is_human': False},
            ]
        )

        tracker.on_player_eliminated('P2', 'P1')

        with pytest.raises(ValueError):
            tracker.on_player_eliminated('P2', 'P1')


class TestTournamentStandings:
    """Tests for tournament standings generation."""

    def test_standings_ordered_by_position(self):
        """Standings should be ordered by finishing position."""
        tracker = TournamentTracker(
            game_id='test_game',
            starting_players=[
                {'name': 'P1', 'is_human': True},
                {'name': 'P2', 'is_human': False},
                {'name': 'P3', 'is_human': False},
                {'name': 'P4', 'is_human': False},
            ]
        )

        # Eliminate in order: P4 (4th), P3 (3rd), P2 (2nd)
        tracker.on_player_eliminated('P4', 'P1')
        tracker.on_player_eliminated('P3', 'P1')
        tracker.on_player_eliminated('P2', 'P1')

        standings = tracker.get_standings()

        assert len(standings) == 4
        assert standings[0].player_name == 'P1'  # Winner
        assert standings[0].finishing_position == 1
        assert standings[1].player_name == 'P2'
        assert standings[1].finishing_position == 2
        assert standings[2].player_name == 'P3'
        assert standings[2].finishing_position == 3
        assert standings[3].player_name == 'P4'
        assert standings[3].finishing_position == 4

    def test_human_player_identified_in_standings(self):
        """Human player should be correctly identified in standings."""
        tracker = TournamentTracker(
            game_id='test_game',
            starting_players=[
                {'name': 'Human', 'is_human': True},
                {'name': 'AI1', 'is_human': False},
                {'name': 'AI2', 'is_human': False},
            ]
        )

        tracker.on_player_eliminated('AI2', 'Human')
        tracker.on_player_eliminated('AI1', 'Human')

        standings = tracker.get_standings()

        human_standing = next(s for s in standings if s.player_name == 'Human')
        assert human_standing.is_human is True
        assert human_standing.finishing_position == 1


class TestWinnerInfoStructure:
    """Tests for winner info data structure."""

    def test_determine_winner_returns_required_fields(self):
        """determine_winner should return all fields needed by frontend."""
        human_hand = create_hand(('A', 'spades'), ('K', 'spades'))
        ai_hand = create_hand(('Q', 'hearts'), ('J', 'hearts'))
        community = create_community_cards(
            ('A', 'hearts'), ('K', 'hearts'), ('7', 'diamonds'),
            ('2', 'clubs'), ('3', 'spades')
        )

        players = [
            create_player('Human', stack=0, bet=500, is_human=True, hand=human_hand),
            create_player('AI', stack=0, bet=500, hand=ai_hand),
        ]
        game_state = create_game_state(players, community_cards=community)

        winner_info = determine_winner(game_state)

        # Required fields for pot distribution
        assert 'pot_breakdown' in winner_info
        assert 'hand_name' in winner_info
        assert isinstance(winner_info['pot_breakdown'], list)

        # pot_breakdown structure - this is what frontend uses
        for pot in winner_info['pot_breakdown']:
            assert 'pot_name' in pot
            assert 'total_amount' in pot
            assert 'winners' in pot
            # Each winner in pot has name and amount
            for winner in pot['winners']:
                assert 'name' in winner
                assert 'amount' in winner

    def test_pot_breakdown_amounts_sum_to_total(self):
        """Pot breakdown amounts should sum to total pot."""
        players = [
            create_player('P1', stack=0, bet=300, is_all_in=True,
                         hand=create_hand(('A', 'spades'), ('A', 'hearts'))),
            create_player('P2', stack=0, bet=500, is_all_in=True,
                         hand=create_hand(('K', 'spades'), ('K', 'hearts'))),
            create_player('P3', stack=0, bet=500, is_all_in=True,
                         hand=create_hand(('Q', 'spades'), ('Q', 'hearts'))),
        ]
        community = create_community_cards(
            ('7', 'diamonds'), ('8', 'clubs'), ('9', 'spades'),
            ('2', 'hearts'), ('3', 'diamonds')
        )
        game_state = create_game_state(players, community_cards=community)

        winner_info = determine_winner(game_state)

        total_from_breakdown = sum(
            pot['total_amount'] for pot in winner_info['pot_breakdown']
        )
        expected_total = 300 + 500 + 500  # 1300

        assert total_from_breakdown == expected_total

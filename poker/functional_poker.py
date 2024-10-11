import json
from sys import modules as sys_modules
from dataclasses import dataclass, field, replace
from random import shuffle
from typing import Tuple, Mapping, List, Any

from utils import get_celebrities

# DEFAULTS
HUMAN_NAME = "Jeff"
STACK_SIZE = 10000
NUM_AI_PLAYERS = 3
ANTE = 25

@dataclass(frozen=True)
class PokerGameState:
    players: Tuple[Mapping, ...]
    deck: Tuple[Mapping, ...] = field(default_factory=tuple)
    discard_pile: Tuple[Mapping, ...] = field(default_factory=tuple)
    pot: Mapping = field(default_factory=lambda: {'total': 0})
    current_player_idx: int = 0
    current_dealer_idx: int = 0
    community_cards: Tuple[Mapping, ...] = field(default_factory=tuple)

    @property
    def current_player(self):
        return self.players[self.current_player_idx]

    @property
    def next_player_idx(self):
        return (self.current_player_idx + 1) % len(self.players)

    @property
    def highest_bet(self):
        # Determine the highest bet made to the pot by an active player in the hand
        highest_bet = 0
        for player in self.players:
            highest_bet = player['bet'] if player['bet'] > highest_bet else highest_bet
        return highest_bet


def create_new_game_state(
        game_state: PokerGameState,
        players: Tuple[Mapping[str, any], ...] = None,
        deck: Tuple[Mapping[str, any], ...] = None,
        discard_pile: Tuple[Mapping[str, any], ...] = None,
        pot: Mapping[str, any] = None,
        current_player_idx: int = None,
        current_dealer_idx: int = None,
        community_cards: Tuple[Mapping[str, any], ...] = None
) -> PokerGameState:
    """
    Simplify updates to the PokerGameState
    """
    return replace(
        game_state,
        players=players or game_state.players,
        deck=deck or game_state.deck,
        discard_pile=discard_pile or game_state.discard_pile,
        pot=pot or game_state.pot,
        current_player_idx=current_player_idx if current_player_idx is not None else game_state.current_player_idx,
        current_dealer_idx=current_dealer_idx if current_dealer_idx is not None else game_state.current_dealer_idx,
        community_cards=community_cards or game_state.community_cards,
    )

# @dataclass
# class Player(frozen=True):
#     name: str
#     stack: int = 10000
#     bet: int = 0
#     hand: Tuple[Mapping] = field(default_factory=tuple)
#     is_all_in: bool = False
#     is_folded: bool = False
#     is_human: bool = False


def create_player(name: str, stack: int = STACK_SIZE, is_human: bool = False) -> Mapping[str, any]:
    """
    Returns a new player as a map ready to be added to a game.
    """
    return {
        'name': name,
        'stack': stack,
        'bet': 0,
        'hand': (),
        'is_all_in': False,
        'is_folded': False,
        'is_human': is_human
    }


def player_is_active(player: Mapping[str, Any]) -> bool:
    """
    Checks to see if a player is active in the game.
    Active means that the player is not all in and hasn't folded.

    Parameters:
    - player (Mapping[str, Any]): A mapping containing player information with at least
      'is_all_in' and 'is_folded' keys.

    Returns:
    - bool: True if the player is active, False otherwise.
    """
    return not player['is_all_in'] and not player['is_folded']

def create_deck(shuffled: bool = True):
    """
    Deck created as a tuple to be used immediately in a new game. Set shuffled = False to return an ordered deck.
    """
    ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
    suits = ['Spades', 'Diamonds', 'Clubs', 'Hearts']
    deck = [{'rank': rank, 'suit': suit} for rank in ranks for suit in suits]
    shuffle(deck) if shuffled else None
    return tuple(deck)


def create_ai_players(player_names: List[str]):
    """
    Create the dict for each AI player name in the list of player names.
    """
    return tuple(create_player(name=name, is_human=False) for name in player_names)


def start_game(player_names: List[str]) -> PokerGameState:
    """
    Generate a new game state and prepare the game for the initial round of betting.
        - get a new deck of shuffled cards
        - deal cards to starting players
        - set dealer, current_player
    """
    # Create a tuple of Human and AI players to be added to the game state. Using a hard-coded human name
    new_players = (create_player(HUMAN_NAME, is_human=True),) + create_ai_players(player_names)
    game_state = PokerGameState(players=new_players, deck=create_deck())

    return game_flow(game_state)


def get_next_active_player_idx(game_state: PokerGameState) -> int:
    """
    Find the index for the next active player in the game. Skips players that have no money left or have folded.
    """
    starting_idx = game_state.next_player_idx
    next_player_idx = starting_idx
    player_count = len(game_state.players)

    while True:
        next_player_idx = (next_player_idx + 1) % player_count
        if player_is_active(game_state.players[next_player_idx]):
            return next_player_idx
        if next_player_idx == starting_idx:  # If we looped back to the starting player
            break

def next_active_player(game_state: PokerGameState) -> PokerGameState:
    """
    Move to the next active player in the game. Skips players that have no money left or have folded.
    """
    next_active_player_idx = get_next_active_player_idx(game_state)
    return create_new_game_state(game_state=game_state, current_player_idx=next_active_player_idx)

def place_bet(game_state: PokerGameState, amount):
    """
    Update the player and pot and advance to the next player
    Assumes:
        - game_state.current_player_idx is set to the current player's index
        - amount is an int

    Returns:
        - updated game_state
    """
    # Get the current player and update their total bet amount
    current_player = game_state.players[game_state.current_player_idx]

    # Check to see if player has enough to bet, adjust the amount to the player stack to prevent
    # them from betting more than they have and set them to all-in if they have bet everything
    is_all_in = current_player['is_all_in']
    if current_player['stack'] <= amount:
        amount = current_player['stack']
        is_all_in = True

    # Update the players chip stack by removing the bet amount from the stack
    new_stack = current_player['stack'] - amount
    updated_player = {**current_player,
                      'stack': new_stack,
                      'bet': current_player['bet'] + amount,
                      'is_all_in': is_all_in}

    # Create a new list of players with the updated player
    new_players = (game_state.players[:game_state.current_player_idx] +
                   (updated_player,) + game_state.players[game_state.current_player_idx + 1:])

    # Create a new pot with updated totals for the pot and the amount contributed by the player.
    # TODO: account for multiple pots
    new_bet = current_player['bet'] + amount
    new_pot = {**game_state.pot, 'total': game_state.pot['total'] + amount, current_player['name']: new_bet}

    return create_new_game_state(game_state, players=new_players, pot=new_pot, current_player_idx=(game_state.current_player_idx + 1) % len(game_state.players))


def draw_cards(deck, num_cards: int = 1, pos: int = 0) -> Tuple[Tuple[Mapping, ...], Tuple[Mapping, ...]]:
    """
    Pulls cards from a position in the deck. Defaults to 1 card from the beginning of the deck.
    Assumes:
        -
    Returns:
        - card tuple
        - new deck
    """
    cards = deck[pos:pos + num_cards]
    new_deck = deck[:pos] + deck[pos + num_cards:]
    return cards, new_deck


def update_player(game_state, player, **kwargs):
    """
    Update a specific player's state with the provided kwargs within the game state.
    """
    player_idx = game_state.players.index(player)
    updated_player = {**player, **kwargs}
    new_players = (game_state.players[:player_idx] +
                   (updated_player,) + game_state.players[player_idx + 1:])
    return create_new_game_state(game_state, players=new_players)


def deal_hole_cards(game_state: PokerGameState):
    """
    Generate a new game state by removing cards from the deck and dealing to the current player.
    """
    for player in game_state.players:
        if player_is_active(player):
            cards, new_deck = draw_cards(deck=game_state.deck, num_cards=2)
            new_hand = player['hand'] + cards
            game_state = update_player(game_state, player=player, hand=new_hand)
            game_state = create_new_game_state(game_state, deck=new_deck)

    # Return a new game state with the updated deck and players
    return game_state


def play_turn(game_state):
    action, amount = get_player_action(game_state)
    function_name = "player_" + action.strip().lower()
    player_action_function = getattr(sys_modules[__name__], function_name)

    if action == 'raise':
        game_state = player_action_function(game_state, amount)
    else:
        game_state = player_action_function(game_state)

    return game_state


def get_player_action(game_state) -> Tuple[str, int]:
    """
    Retrieve play decision from an external source, either the human or the AI.
    If the player chooses to raise, the amount of the raise also needs to be captured.
    If a player calls a bet or raises and can't cover the amount they added, it's currently
    handled in the 'place_bet' function.
    TODO: this will need to be reviewed when we want to add support for multiple pots
    """
    bet_amount = 0
    player_input = input(f"{game_state.current_player['name']}, what's your move?")
    if player_input == "raise":
        bet_amount = input("how much would you like to bet?")

    return player_input, bet_amount


def player_call(game_state):
    """
    Player calls the current bet
    """
    call_amount = game_state.highest_bet - game_state.current_player['bet']
    game_state = place_bet(game_state=game_state, amount=call_amount)
    return next_active_player(game_state)

def player_check(game_state):
    return next_active_player(game_state)


def player_fold(game_state):
    """
    Player folds their hand.
    """
    new_discard_pile = game_state.discard_pile + game_state.current_player['hand']
    game_state = update_player(game_state, player=game_state.current_player, is_folded=True, hand=[])
    game_state = create_new_game_state(game_state, discard_pile=new_discard_pile)
    return next_active_player(game_state)


def player_raise(game_state, amount: int):
    """
    Player raises the current bet by the provided amount.
    """
    game_state = place_bet(game_state=game_state, amount=amount)
    return next_active_player(game_state)


def player_all_in(game_state):
    """
    Player bets all of their remaining chips.
    """
    game_state = place_bet(game_state=game_state, amount=game_state.current_player['stack'])
    return next_active_player(game_state)


def deal_community_cards(game_state: PokerGameState, num_cards: int = 1):
    cards, new_deck = draw_cards(game_state.deck, num_cards=num_cards)
    new_community_cards = game_state.community_cards + cards
    return create_new_game_state(game_state, community_cards=new_community_cards, deck=new_deck)


def game_flow(game_state: PokerGameState):
    phases = [
        lambda state: deal_hole_cards(state),
        lambda state: next_active_player(state),
        lambda state: place_bet(state, ANTE),
        lambda state: place_bet(state, ANTE*2),
        lambda state: play_turn(state),
        # lambda state: deal_community_cards(state, num_cards=3),
        # lambda state: play_turn(state),
        # lambda state: deal_community_cards(state, num_cards=1),
        # lambda state: play_turn(state),
        # lambda state: deal_community_cards(state, num_cards=1),
        # lambda state: play_turn(state),
        # lambda state: determine_winner(state)
    ]

    for phase in phases:
        game_state = phase(game_state)
    return game_state

if __name__ == '__main__':
    ai_player_names = get_celebrities(shuffled=True)[:NUM_AI_PLAYERS]
    game_instance = start_game(player_names=ai_player_names)


    # Convert game_state to JSON and pretty print to console
    game_state_json = json.loads(json.dumps(game_instance, default=lambda o: o.__dict__))
    print(json.dumps(game_state_json, indent=4))

import json
from sys import modules as sys_modules
from dataclasses import dataclass, field, replace
from random import shuffle
from typing import Tuple, Mapping, List, Any

from core.card import Card
from poker.hand_evaluator import HandEvaluator

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
    is_big_blind_option_taken: bool = False
    community_cards: Tuple[Mapping, ...] = field(default_factory=tuple)

    @property
    def current_player(self):
        return self.players[self.current_player_idx]

    @property
    def highest_bet(self):
        # Determine the highest bet made to the pot by an active player in the hand
        highest_bet = 0
        for player in self.players:
            highest_bet = player['bet'] if player['bet'] > highest_bet else highest_bet
        return highest_bet

    def get_player_by_name(self, search_name: str):
        for idx, player in enumerate(self.players):
            if player['name'] == search_name:
                return player, idx
        return None


##################################################################
##################            CHECKS            ##################
##################################################################
def big_blind_can_raise_or_check(game_state):
    """
    Determines if it is the pre-flop round of betting in the hand and no one has raised above the big blind ante.

    Parameters:
    game_state (GameState): The current state of the game.

    Returns:
    bool: True if the big blind player can raise or check, False otherwise.
    """
    # Check if no community cards are dealt, which would indicate that we are in the pre-flop round of betting
    no_community_cards_dealt = len(game_state.community_cards) == 0
    big_blind_player_name = game_state.players[(game_state.current_dealer_idx + 2) % len(game_state.players)]['name']

    can_raise_or_check = (
            game_state.current_player['name'] == big_blind_player_name and
            no_community_cards_dealt and
            game_state.highest_bet == ANTE * 2  # TODO: replace ANTE with a property of the game_state
    )
    return can_raise_or_check

def is_round_complete(game_state):
    """
    Validates that all players have contributed an even amount to the pot or have folded or gone all-in.
    Special case: In the first betting round, the big blind should get a chance to check or raise.
    """
    if big_blind_can_raise_or_check(game_state) and not game_state.is_big_blind_option_taken:
        return False
    # Check if all players have checked, folded, or gone all-in
    for player in game_state.players:
        if is_player_active(player):
            return False
    return True


def is_player_active(player: Mapping[str, Any]) -> bool:
    """
    Checks if a player is active in the betting round.

    Args:
        player (Mapping[str, Any]): A dictionary representing the player's status

    Returns:
        bool: True if the player is active, False otherwise
    """
    player_has_no_chips = player['stack'] == 0
    return not (player['is_all_in'] or player['is_folded'] or player['has_acted'] or player_has_no_chips)


##################################################################
#################           GENERATORS           #################
##################################################################
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
        'has_acted': False,
        'is_human': is_human
    }


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


##################################################################
##################         UPDATERS         ######################
##################################################################
def update_poker_game_state(
        game_state: PokerGameState,
        players: Tuple[Mapping[str, any], ...] = None,
        deck: Tuple[Mapping[str, any], ...] = None,
        discard_pile: Tuple[Mapping[str, any], ...] = None,
        pot: Mapping[str, any] = None,
        current_player_idx: int = None,
        current_dealer_idx: int = None,
        is_big_blind_option_taken: bool = None,
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
        is_big_blind_option_taken=is_big_blind_option_taken or game_state.is_big_blind_option_taken,
        community_cards=community_cards or game_state.community_cards,
    )


def update_player_state(players: Tuple[Mapping, ...], player_idx: int, **state) -> Tuple[Mapping, ...]:
    """
    Update a specific player's state with the provided kwargs within a player tuple
    """
    player = players[player_idx]
    updated_player = {**player, **state}
    new_players = (players[:player_idx] +
                   (updated_player,) + players[player_idx + 1:])
    return new_players


##################################################################
##################      DEALER ACTIONS      ######################
##################################################################
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


def deal_hole_cards(game_state: PokerGameState):
    """
    Generate a new game state by removing cards from the deck and dealing to the current player.
    """
    for player in game_state.players:
        if is_player_active(player):
            player_idx = game_state.players.index(player)
            cards, new_deck = draw_cards(deck=game_state.deck, num_cards=2)
            new_hand = player['hand'] + cards
            new_players = update_player_state(players=game_state.players, player_idx=player_idx, hand=new_hand)
            game_state = update_poker_game_state(game_state, players=new_players, deck=new_deck)

    # Return a new game state with the updated deck and players
    return game_state


def deal_community_cards(game_state: PokerGameState, num_cards: int = 1):
    cards, new_deck = draw_cards(game_state.deck, num_cards=num_cards)
    new_community_cards = game_state.community_cards + cards
    return update_poker_game_state(game_state, community_cards=new_community_cards, deck=new_deck)


##################################################################
##################      PLAYER_ACTIONS      ######################
##################################################################
def place_bet(game_state: PokerGameState, amount: int):
    """
    Updates the current_player and pot based on the amount bet.
    Resets all players 'has_acted' if a raise is made.
    Assumes:
        - amount is an int

    Returns:
        - updated game_state
    """
    # Get the current player and update their total bet amount
    current_player = game_state.current_player

    # If the player has raised the bet we will want to reset all other players 'has_acted' flags.
    previous_high_bet = game_state.highest_bet   # Note the current high bet to compare later.

    # Check to see if player has enough to bet, adjust the amount to the player stack to prevent
    # them from betting more than they have and set them to all-in if they have bet everything
    # TODO: create a new pot when a player goes all in
    is_player_all_in = current_player['is_all_in']
    if current_player['stack'] <= amount:
        amount = current_player['stack']
        is_player_all_in = True

    # Update the players chip stack by removing the bet amount from the stack
    new_stack = current_player['stack'] - amount
    new_bet = current_player['bet'] + amount
    new_players = update_player_state(players=game_state.players,
                                     player_idx=game_state.current_player_idx,
                                     stack=new_stack,
                                     is_all_in=is_player_all_in,
                                     bet=new_bet)

    # Create a new pot with updated totals for the pot and the amount contributed by the player.
    new_pot = {**game_state.pot, 'total': game_state.pot['total'] + amount, game_state.current_player['name']: new_bet}
    game_state = update_poker_game_state(game_state, players=new_players, pot=new_pot)

    # If the players bet has raised the high bet, reset the player action flags so that they become active in the round
    # Exclude current player from being marked False so they don't get to take an action again unless someone else bets
    if previous_high_bet < new_bet:
        game_state = reset_player_action_flags(game_state, exclude_current_player=True)

    return game_state


def reset_player_action_flags(game_state: PokerGameState, exclude_current_player: bool = False):
    """
    Sets all player action flags to False. Current player can be excluded from this action when they are betting and
    just other players should be reset.
    """
    for player in game_state.players:
        if player['name'] != game_state.current_player['name'] or not exclude_current_player:
            new_players = update_player_state(players=game_state.players,
                                              player_idx=game_state.players.index(player),
                                              has_acted=False)
            game_state = update_poker_game_state(game_state, players=new_players)
    return game_state


def player_call(game_state):
    """
    Player calls the current bet
    """
    call_amount = game_state.highest_bet - game_state.current_player['bet']
    game_state = place_bet(game_state=game_state, amount=call_amount)
    return game_state


def player_check(game_state):
    """
    Player checks their bet.
    """
    return game_state


def player_fold(game_state):
    """
    Player folds their hand.
    """
    new_discard_pile = game_state.discard_pile + game_state.current_player['hand']
    new_players = update_player_state(players=game_state.players,
                                      player_idx=game_state.current_player_idx,
                                      is_folded=True,
                                      hand=())
    return update_poker_game_state(game_state, players=new_players, discard_pile=new_discard_pile)


def player_raise(game_state, amount: int):
    """
    Player raises the current highest bet by the provided amount.
    """
    # Calculate the cost_to_call as the difference between the current highest bet and the players current bet
    cost_to_call = game_state.highest_bet - game_state.current_player['bet']
    game_state = place_bet(game_state=game_state, amount=amount + cost_to_call)
    return game_state


def player_all_in(game_state):
    """
    Player bets all of their remaining chips.
    """
    game_state = place_bet(game_state=game_state, amount=game_state.current_player['stack'])
    return game_state


def player_players(game_state):
    # Convert game_state to JSON and pretty print to console
    game_state_json = json.loads(json.dumps(game_state, default=lambda o: o.__dict__))
    del game_state_json['deck']
    print(json.dumps(game_state_json, indent=4))
    return game_state


##################################################################
######################      GAME FLOW       ######################
##################################################################
def play_betting_round(game_state):
    """
    Cycle through all players until the pot is good.
    """
    if len(game_state.community_cards) > 0:
        first_action_player_idx = get_next_active_player_idx(players=game_state.players,
                                                             relative_player_idx=game_state.current_dealer_idx)
    else:
        first_action_player_idx = get_next_active_player_idx(players=game_state.players,
                                                             relative_player_idx=game_state.current_dealer_idx + 2)
    game_state = update_poker_game_state(game_state, current_player_idx=first_action_player_idx)

    while not is_round_complete(game_state):
        game_state = play_turn(game_state)
        game_state = advance_to_next_active_player(game_state)
    game_state = reset_player_action_flags(game_state, exclude_current_player=False)
    return game_state


def play_turn(game_state):
    """
    Process the current player's turn by retrieving an action from some other input and calling the appropriate
    function. The player's 'has_acted' flag will be set to True here and is reset
    TODO: add a check to see if the hand end conditions have been met (all players folded or all-in)
    """
    action, amount = get_player_action(game_state)
    function_name = "player_" + action.strip().lower()
    player_action_function = getattr(sys_modules[__name__], function_name)

    if action == 'raise':
        game_state = player_action_function(game_state, amount)
    else:
        game_state = player_action_function(game_state)

    new_players = update_player_state(players=game_state.players, player_idx=game_state.current_player_idx, has_acted=True)
    game_state = update_poker_game_state(game_state, players=new_players)

    if big_blind_can_raise_or_check(game_state) and not game_state.is_big_blind_option_taken:
        game_state = update_poker_game_state(game_state, is_big_blind_option_taken=True)

    return game_state


def get_next_active_player_idx(players: Tuple[Mapping, ...], relative_player_idx: int) -> int:
    """
    Find the index for the next active player in the game.
    """
    player_count = len(players)
    # Start with the next player in the queue, save the starting index for later so we can end the loop
    # if we come all the way around
    starting_idx = relative_player_idx
    next_player_idx = (starting_idx + 1) % player_count

    players_checked = []    # TODO: remove this test variable
    while True:
        players_checked.append(next_player_idx)
        if is_player_active(players[next_player_idx]):
            return next_player_idx
        if next_player_idx == starting_idx:  # If we looped back to the starting player
            print(f"\nwhat should we do now? {players_checked}\n")
            break
        next_player_idx = (next_player_idx + 1) % player_count  # Iterate through the players by 1 with a wrap around


def advance_to_next_active_player(game_state: PokerGameState) -> PokerGameState:
    """
    Move to the next active player in the game.
    """
    next_active_player_idx = get_next_active_player_idx(players=game_state.players, relative_player_idx=game_state.current_player_idx)
    return update_poker_game_state(game_state=game_state, current_player_idx=next_active_player_idx)


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

    return game_state


def reset_game_state_for_new_hand(game_state):
    """
    Sets all game_state flags to new hand state.
    Creates a new deck and resets the player's hand.
    Rotates the dealer position.
    Deals the hole cards.
    """
    # Create new players with reset flags to prepare for the next round
    new_players = []
    for player in game_state.players:
        new_player = create_player(name=player['name'], stack=player['stack'], is_human=player['is_human'])
        new_players.append(new_player)
    new_players = tuple(new_players)

    # Rotate the dealer position to the next active player in the game.
    new_dealer_idx = get_next_active_player_idx(players=new_players,
                                                relative_player_idx=game_state.current_dealer_idx)
    new_players = new_players[new_dealer_idx:] + new_players[:new_dealer_idx]

    # Create a new game state with just the properties we want to carry over (just the new players queue)
    return PokerGameState(players=new_players, deck=create_deck())


def determine_winner(game_state):
    """
    Resolves the pot by determining the winner based on their hand and the community cards.
    """
    # Get list of player names that contributed to the pot
    players_eligible_for_pot = []
    for player_name in game_state.pot:
        if not player_name == 'total':
            has_player_folded = game_state.get_player_by_name(player_name)[0]['is_folded']
            if not has_player_folded:
                players_eligible_for_pot.append(player_name)
    # Create Tuple with each player's hand using the Card class
    # Create a list which will hold a Tuple of (PokerPlayer, HandEvaluator)
    hands = []
    # Convert the community cards to Cards
    new_community_cards = []
    for card in game_state.community_cards:
        new_community_cards.append(Card(card['rank'], card['suit']))

    for player in game_state.players:
        if player['name'] in players_eligible_for_pot:
            new_cards = []
            for card in player['hand']:
                new_cards.append(Card(rank=card['rank'], suit=card['suit']))
            hands.append((player['name'], HandEvaluator(new_cards + new_community_cards).evaluate_hand()))

    print(f"players_in_pot: {players_eligible_for_pot}\n"
          f"new_community_cards: {new_community_cards}\n"
          f"hands: {hands}\n")

    hands.sort(key=lambda x: sorted(x[1]["kicker_values"]), reverse=True)
    hands.sort(key=lambda x: sorted(x[1]["hand_values"]), reverse=True)
    hands.sort(key=lambda x: x[1]["hand_rank"])

    winning_player_name = hands[0][0]
    winning_hand = hands[0][1]["hand_values"] + hands[0][1]["kicker_values"]

    # Reward winning player
    _, winning_player_idx = game_state.get_player_by_name(winning_player_name)
    new_stack_total = game_state.pot['total'] + game_state.players[winning_player_idx]['stack']
    new_players = update_player_state(game_state.players, player_idx=winning_player_idx, stack=new_stack_total)
    game_state = update_poker_game_state(game_state, players=new_players)

    print(winning_player_name, winning_hand)        # TODO: log the win and the game_states for this hand
    return game_state



def play_hand(game_state: PokerGameState):
    phases = [
        lambda state: advance_to_next_active_player(state),
        lambda state: place_bet(state, ANTE),
        lambda state: advance_to_next_active_player(state),
        lambda state: place_bet(state, ANTE*2),
        lambda state: advance_to_next_active_player(state),
        lambda state: deal_hole_cards(state),
        lambda state: play_betting_round(state),
        lambda state: deal_community_cards(state, num_cards=3),
        lambda state: play_betting_round(state),
        lambda state: deal_community_cards(state, num_cards=1),
        lambda state: play_betting_round(state),
        lambda state: deal_community_cards(state, num_cards=1),
        lambda state: play_betting_round(state),
        lambda state: determine_winner(state)
    ]

    for phase in phases:
        game_state = phase(game_state)
    return game_state


##################################################################
##################      EXTERNAL INTERFACE      ##################
##################################################################
def get_player_action(game_state) -> Tuple[str, int]:
    """
    Retrieve play decision from an external source, either the human or the AI.
    If the player chooses to raise, the amount of the raise also needs to be captured.
    If a player calls a bet or raises and can't cover the amount they added, it's currently
    handled in the 'place_bet' function.
    TODO: this will need to be reviewed when we want to add support for multiple pots
    """
    cost_to_call_bet = game_state.highest_bet - game_state.current_player['bet']

    print(f"community cards: {game_state.community_cards}\n"
          f"cards:  {game_state.current_player['hand']}\n"
          f"pot:    {game_state.pot['total']}\n"
          f"stack:  {game_state.current_player['stack']}\n"
          f"cost to call:   {cost_to_call_bet}\n")

    bet_amount = 0
    player_input = input(f"{game_state.current_player['name']}, what's your move?   ")
    if player_input == "raise":
        bet_amount = int(input("how much would you like to bet? "))

    print(f"{game_state.current_player['name']} has chosen {player_input} ({bet_amount})")
    print()
    return player_input, bet_amount


if __name__ == '__main__':
    ai_player_names = get_celebrities(shuffled=True)[:NUM_AI_PLAYERS]
    game_instance = start_game(player_names=ai_player_names)
    # game_instance = start_game(player_names=["Small Blind", "Big Blind", "Player 4"])

    players_remain_in_game = True       # TODO: define players_remain_in_game
    while players_remain_in_game:
        game_instance = play_hand(game_state=game_instance)
        game_instance = reset_game_state_for_new_hand(game_state=game_instance)

        # Convert game_state to JSON and pretty print to console
        game_state_json = json.loads(json.dumps(game_instance, default=lambda o: o.__dict__))
        print(json.dumps(game_state_json, indent=4))

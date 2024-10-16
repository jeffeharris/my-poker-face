import json
from sys import modules as sys_modules
from dataclasses import dataclass, field, replace
from random import shuffle
from typing import Tuple, Mapping, List, Any

from core.card import Card
from old_files.hand_evaluator import HandEvaluator

# DEFAULTS
NUM_AI_PLAYERS = 1
HUMAN_NAME = "Jeff"
STACK_SIZE = 10000
ANTE = 25


def create_deck(shuffled: bool = True):
    """
    Deck created as a tuple to be used immediately in a new game. Set shuffled = False to return an ordered deck.
    """
    ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
    suits = ['Spades', 'Diamonds', 'Clubs', 'Hearts']
    deck = [{'rank': rank, 'suit': suit} for rank in ranks for suit in suits]
    shuffle(deck) if shuffled else None
    return tuple(deck)


@dataclass(frozen=True)
class PokerGameState:
    players: Tuple[Mapping, ...]
    deck: Tuple[Mapping, ...] = field(default_factory=lambda: create_deck(shuffled=True))
    discard_pile: Tuple[Mapping, ...] = field(default_factory=tuple)
    pot: Mapping = field(default_factory=lambda: {'total': 0})
    current_player_idx: int = 0
    current_dealer_idx: int = 0
    community_cards: Tuple[Mapping, ...] = field(default_factory=tuple)
    ### FLAGS ###
    pre_flop_action_taken: bool = False

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

    @property
    def can_big_blind_take_pre_flop_action(self):
        """
        Determines if it is the pre-flop round of betting in the hand and no one has raised above the big blind ante.

        Parameters:
        game_state (GameState): The current state of the game.

        Returns:
        bool: True if the big blind player can raise or check, False otherwise.
        """
        # Check if no community cards are dealt, which would indicate that we are in the pre-flop round of betting
        no_community_cards_dealt = len(self.community_cards) == 0
        big_blind_player_name = self.players[(self.current_dealer_idx + 2) % len(self.players)]['name']

        can_raise_or_check = (
                self.current_player['name'] == big_blind_player_name
                and no_community_cards_dealt
                and self.highest_bet == ANTE * 2  # TODO: replace ANTE with a property of the game_state
                and not self.pre_flop_action_taken
        )
        return can_raise_or_check

    @property
    def current_player_options(self) -> List[str]:
        """
        Used when the player's turn comes up to display the available actions.
        """
        player = self.current_player
        # How much is it to call the bet for the player?
        player_cost_to_call = self.highest_bet - player['bet']
        # Does the player have enough to call
        player_has_enough_to_call = player['stack'] > player_cost_to_call

        # If the current player is last to act (aka big blind), and we're still in the pre-flop round
        if self.can_big_blind_take_pre_flop_action:
            player_options = ['check', 'raise', 'all-in', 'chat']
        else:
            player_options = ['fold', 'check', 'call', 'raise', 'all_in', 'chat']
            if player_cost_to_call == 0:
                player_options.remove('fold')
            if player_cost_to_call > 0:
                player_options.remove('check')
            if not player_has_enough_to_call or player_cost_to_call == 0:
                player_options.remove('call')
            if player['stack'] - self.highest_bet <= 0:
                player_options.remove('raise')
            if player['stack'] == 0:
                player_options.remove('all-in')
            if True:                                    # TODO: implement ai chat and then fix this check
                player_options.remove('chat')
        return player_options

    def get_player_by_name(self, search_name: str):
        for idx, player in enumerate(self.players):
            if player['name'] == search_name:
                return player, idx
        return None


##################################################################
##################            CHECKS            ##################
##################################################################
def are_pot_contributions_valid(game_state):
    """
    Validates that all players have contributed an even amount to the pot or have folded or gone all-in.
    Special case: In the first betting round, the big blind should get a chance to check or raise.
    """
    # Check that big blind has not taken their option to check/raise and that they are
    if game_state.can_big_blind_take_pre_flop_action:
        return False
    # Check if all players have checked, folded, or gone all-in
    for player in game_state.players:
        if is_player_active(player):
            return False
    return True


def is_player_active(player: Mapping[str, Any]) -> bool:
    """
    Checks if a player is active in the betting round. Active in this case means that the player is still
    active in the hand to bet or active in the Game to be a dealer for the next hand.

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
# TODO: Refactor player to a dataclass
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
        pre_flop_action_taken: bool = None,
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
        pre_flop_action_taken=pre_flop_action_taken or game_state.pre_flop_action_taken,
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
def play_betting_round_until_action(game_state) -> PokerGameState:
    """
    Cycle through all players until the pot is good.

    Side Effects: accepts input from a player that is used for play_turn.

    Parameters:
        game_state: The current game state
        get_player_action_function: Callback function used to retrieve player action from a UI

    Returns:
        game_state: The updated game state with all player actions taken for the round
    """
    if len(game_state.community_cards) > 0:
        first_action_player_idx = get_next_active_player_idx(players=game_state.players,
                                                             relative_player_idx=game_state.current_dealer_idx)
    else:
        first_action_player_idx = get_next_active_player_idx(players=game_state.players,
                                                             relative_player_idx=game_state.current_dealer_idx + 2)

    game_state = update_poker_game_state(game_state, current_player_idx=first_action_player_idx)

    return game_state

def play_betting_round_post_action(game_state):
    # TODO: Move the following line somewhere it makes more sense to reset the betting round
    # Reset the betting round action flags
    game_state = reset_player_action_flags(game_state, exclude_current_player=False)

    # Deal the community cards
    # Define a map of count of community cards in the game state to the round info to be used for dealing cards (or not)
    community_card_count_to_round_name_map = {
        0: ("Pre-flop", 3),
        3: ("Flop", 1),
        4: ("Turn", 1),
        5: ("River", 0)
    }

    # Using this as a proxy to tell us what round of betting we are in
    num_community_cards = len(game_state.community_cards)

    round_name = community_card_count_to_round_name_map[num_community_cards][0]         # TODO: make the round name a property of the game state
    cards_to_deal = community_card_count_to_round_name_map[num_community_cards][1]

    game_state = deal_community_cards(game_state, cards_to_deal)

    return game_state


def play_turn(game_state, action, amount):
    """
    Process the current player's turn given the action and amount provided.
    The player's 'has_acted' flag will be set to True here and is reset when
    the bet is raised or the betting round ends.

    Parameters:
        game_state: The current game state
        action: The player action selected. Assumes this is validated before the function call.
        amount: Amount that the player wants to contribute to the pot
    """
    function_name = "player_" + action.strip().lower()
    player_action_function = getattr(sys_modules[__name__], function_name)

    if action == 'raise':
        game_state = player_action_function(game_state, amount)
    else:
        game_state = player_action_function(game_state)

    new_players = update_player_state(players=game_state.players,
                                      player_idx=game_state.current_player_idx,
                                      has_acted=True)
    game_state = update_poker_game_state(game_state, players=new_players)

    if game_state.can_big_blind_take_pre_flop_action:
        game_state = update_poker_game_state(game_state, pre_flop_action_taken=True)

    return game_state


def get_next_active_player_idx(players: Tuple[Mapping, ...], relative_player_idx: int) -> int or None:
    """
    Find the index for the next active player in the game.
    """
    player_count = len(players)
    # Start with the next player in the queue, save the starting index for later so we can take action
    # if we come all the way around without finding an active player
    starting_idx = relative_player_idx
    next_player_idx = (starting_idx + 1) % player_count

    while True:
        if is_player_active(players[next_player_idx]):
            return next_player_idx
        if next_player_idx == starting_idx:
            break
        next_player_idx = (next_player_idx + 1) % player_count  # Iterate through the players by 1 with a wrap around


def advance_to_next_active_player(game_state: PokerGameState) -> PokerGameState:
    """
    Move to the next active player in the game.
    """
    next_active_player_idx = get_next_active_player_idx(players=game_state.players, relative_player_idx=game_state.current_player_idx)
    return update_poker_game_state(game_state=game_state, current_player_idx=next_active_player_idx)


def initialize_game_state(player_names: List[str]) -> PokerGameState:
    """
    Generate a new game state and prepare the game for the initial round of betting.
        - get a new deck of shuffled cards
        - deal cards to starting players
        - set dealer, current_player
    """
    # Create a tuple of Human and AI players to be added to the game state. Using a hard-coded human name
    new_players = (create_player(HUMAN_NAME, is_human=True),) + create_ai_players(player_names)
    game_state = PokerGameState(players=new_players)

    return game_state


def setup_hand(game_state):
    """
    Sets the hand up to the point before any player's need to take action.
    This should be followed by a call to play_hand.
    """
    # Pre-flop actions
    game_state = advance_to_next_active_player(game_state)
    game_state = place_bet(game_state, ANTE)
    game_state = advance_to_next_active_player(game_state)
    game_state = place_bet(game_state, ANTE * 2)
    game_state = advance_to_next_active_player(game_state)
    game_state = deal_hole_cards(game_state)

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

    # Rotate the dealer position to the next active player in the game. This needs to come after the players are
    # reset so that get_next_active_player isn't based on the prior hands actions.
    new_dealer_idx = get_next_active_player_idx(players=tuple(new_players),
                                                relative_player_idx=game_state.current_dealer_idx)
    new_players = new_players[new_dealer_idx:] + new_players[:new_dealer_idx]

    # Remove players who have no chips left. This needs to come after the players are reset and the dealer is rotated
    # because we reference the game state's current dealer index in order to rotate.
    new_players = [player for player in new_players if player['stack'] > 0]

    # Create a new game state with just the properties we want to carry over (just the new players queue)
    return PokerGameState(players=tuple(new_players))


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

    # Create a list which will hold a Tuple of (PokerPlayer, HandEvaluator)
    hands = []

    # Convert the community cards to Cards
    new_community_cards = []
    for card in game_state.community_cards:
        new_community_cards.append(Card(card['rank'], card['suit']))

    # Create a List with each player's hand using the Card class and Evaluate them
    for player in game_state.players:
        if player['name'] in players_eligible_for_pot:
            new_cards = []
            for card in player['hand']:
                new_cards.append(Card(rank=card['rank'], suit=card['suit']))
            hands.append((player['name'], HandEvaluator(new_cards + new_community_cards).evaluate_hand()))

    # Sort the hands from best to worst
    hands.sort(key=lambda x: sorted(x[1]["kicker_values"]), reverse=True)
    hands.sort(key=lambda x: sorted(x[1]["hand_values"]), reverse=True)
    hands.sort(key=lambda x: x[1]["hand_rank"])

    print(hands)
    # Check a tie amongst the hands
    winning_hand = hands[0][1]
    winning_hands = [hand for hand in hands if hand[1] == winning_hand]

    winning_player_names = [hand[0] for hand in winning_hands]
    winning_hand = hands[0][1]["hand_values"] + hands[0][1]["kicker_values"]

    # Reward winning players
    for hand in winning_hands:
        # Retrieve the player index for the player of the winning hand
        _ , player_idx = game_state.get_player_by_name(hand[0])

        new_stack_total = game_state.pot['total']/len(winning_hands) + game_state.players[player_idx]['stack']
        new_players = update_player_state(game_state.players, player_idx=player_idx, stack=new_stack_total)
        game_state = update_poker_game_state(game_state, players=new_players)

    winner_info = {
        'winning_player_names': winning_player_names,
        'winning_hand': winning_hand,
        'pot_total': game_state.pot['total']
    }
    return game_state, winner_info


def end_game(game_state: PokerGameState):
    """
    Placeholder for wrapping the game up when a user quits or the game has ended due to only 1 player remaining.
    """
    winner, _ = determine_winner(game_state)
    end_game_info = {
        'winner': game_state.players[0]['name'],
        'message': f"{winner} won! Thanks for playing!"
    }

    return end_game_info

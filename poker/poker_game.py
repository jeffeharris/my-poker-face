import json
from sys import modules as sys_modules
from dataclasses import dataclass, field, replace
from random import shuffle
from typing import Tuple, Mapping, List, Optional, Dict

from core.card import Card
from hand_evaluator import HandEvaluator
from utils import obj_to_dict

# DEFAULTS
NUM_AI_PLAYERS = 2
HUMAN_NAME = "Jeff"
STACK_SIZE = 10000      # player starting stack
ANTE = 50               # starting big blind
TEST_MODE = False

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
class Player:
    name: str
    stack: int
    is_human: bool
    bet: int = 0
    hand: Tuple[Mapping, ...] = field(default_factory=tuple)
    ### FLAGS ###
    is_all_in: bool = False
    is_folded: bool = False
    has_acted: bool = False

    def to_dict(self):
        return {
            'name': self.name,
            'stack': self.stack,
            'is_human': self.is_human,
            'is_all_in': self.is_all_in,
            'is_folded': self.is_folded,
            'has_acted': self.has_acted,
            'bet': self.bet,
            'hand': self.hand,
        }

    def update(self, **kwargs):
        return replace(self, **kwargs)

    @property
    def is_active(self) -> bool:
        """
        Checks if a player is active in the betting round. Active in this case means that the player is still
        active in the hand to bet or active in the Game to be a dealer for the next hand.

        Args:
            self (Player): A dictionary representing the player's status

        Returns:
            bool: True if the player is active, False otherwise
        """
        player_has_no_chips = self.stack == 0
        return not (self.is_all_in or self.is_folded or self.has_acted or player_has_no_chips)


@dataclass(frozen=True)
class PokerGameState:
    players: Tuple[Player, ...]
    deck: Tuple[Mapping, ...] = field(default_factory=lambda: create_deck(shuffled=True))
    discard_pile: Tuple[Mapping, ...] = field(default_factory=tuple)
    pot: Mapping = field(default_factory=lambda: {'total': 0})
    current_player_idx: int = 0
    current_dealer_idx: int = 0
    community_cards: Tuple[Mapping, ...] = field(default_factory=tuple)
    current_ante: int = ANTE
    ### FLAGS ###
    pre_flop_action_taken: bool = False
    awaiting_action: bool = False

    def to_dict(self) -> Dict:
        """
        Converts the GameState to a dict, including some of the dynamic properties that would be useful to display.
        """
        return {
            'players': [p.to_dict() for p in self.players],
            'deck': list(self.deck),
            'discard_pile': list(self.discard_pile),
            'pot': {**self.pot, 'highest_bet': self.highest_bet},
            'current_player_idx': self.current_player_idx,
            'current_dealer_idx': self.current_dealer_idx,
            'community_cards': list(self.community_cards),
            'current_ante': self.current_ante,
            'pre_flop_action_taken': self.pre_flop_action_taken,
            'awaiting_action': self.awaiting_action,
            'small_blind_idx': self.small_blind_idx,
            'big_blind_idx': self.big_blind_idx,
            'current_player_options': self.current_player_options,
            'are_pot_contributions_valid': are_pot_contributions_valid(self),
        }

    @property
    def as_json(self):
        # Convert game_state to JSON and pretty print to console
        # game_state_json = json.loads(json.dumps(self, default=lambda o: o.__dict__))
        return json.dumps(self, indent=4)

    @property
    def current_player(self) -> Player:
        return self.players[self.current_player_idx]

    @property
    def small_blind_idx(self) -> int:
        return (self.current_dealer_idx + 1) % len(self.players)

    @property
    def big_blind_idx(self) -> int:
        return (self.current_dealer_idx + 2) % len(self.players)

    @property
    def no_action_taken(self) -> bool:
        """
        Checks if no player has taken an action.
        """
        result = all(p.has_acted is False for p in self.players)
        return result

    @property
    def highest_bet(self) -> int:
        # Determine the highest bet made to the pot by an active player in the hand
        highest_bet = 0
        for player in self.players:
            highest_bet = player.bet if player.bet > highest_bet else highest_bet
        return highest_bet

    @property
    def can_big_blind_take_pre_flop_action(self) -> bool:
        """
        Determines if it is the pre-flop round of betting in the hand and no one has raised above the big blind ante.

        Parameters:
        game_state (GameState): The current state of the game.

        Returns:
        bool: True if the big blind player can raise or check, False otherwise.
        """
        # Check if no community cards are dealt, which would indicate that we are in the pre-flop round of betting
        no_community_cards_dealt = len(self.community_cards) == 0
        big_blind_player = self.players[self.big_blind_idx]

        can_raise_or_check = (
                self.current_player.name == big_blind_player.name
                and no_community_cards_dealt
                and self.highest_bet == self.current_ante
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
        player_cost_to_call = self.highest_bet - player.bet
        # Does the player have enough to call
        player_has_enough_to_call = player.stack > player_cost_to_call

        # If the current player is last to act (aka big blind), and we're still in the pre-flop round
        if self.can_big_blind_take_pre_flop_action:
            player_options = ['check', 'raise', 'all_in', 'chat']
        else:
            player_options = ['fold', 'check', 'call', 'raise', 'all_in', 'chat']
            if player_cost_to_call == 0:
                player_options.remove('fold')
            if player_cost_to_call > 0:
                player_options.remove('check')
            if not player_has_enough_to_call or player_cost_to_call == 0:
                player_options.remove('call')
            if player.stack - player_cost_to_call <= 0:
                player_options.remove('raise')
            # if player['stack'] == 0:
            #     player_options.remove('all_in')
            if True:                                    # TODO: implement ai chat and then fix this check
                player_options.remove('chat')
        return player_options

    @property
    def table_positions(self) -> Dict:
        num_players = len(self.players)
        current_dealer_idx = self.current_dealer_idx

        # Handle the special case for 2 players
        if num_players == 2:
            # In a 2-player game, Button is also the Small Blind
            return {
                "button": self.players[current_dealer_idx].name,
                "small_blind_player": self.players[current_dealer_idx].name,
                "big_blind_player": self.players[(current_dealer_idx + 1) % num_players].name,
            }

        # Define the base positions in order: Button, Small Blind, Big Blind
        positions = ["button", "small_blind_player", "big_blind_player"]

        # Dynamically add positions based on the number of players
        if num_players >= 4:
            positions.append("under_the_gun")
        if num_players >= 5:
            positions.append("cutoff")
        if num_players >= 6:
            positions.insert(-1, "middle_position_1")
        if num_players >= 7:
            positions.insert(-1, "middle_position_2")
        if num_players == 8:
            positions.insert(-1, "middle_position_3")

        # Create the dictionary to map each position to the corresponding player
        current_positions = {}
        for i, position in enumerate(positions):
            player_index = (current_dealer_idx + i) % num_players
            current_positions[position] = self.players[player_index].name

        return current_positions

    @property
    def opponent_status(self, requesting_player=None) -> List[str]:
        opponent_positions = []
        for player in self.players:
            if player != requesting_player:
                position = f'{player.name} has ${player.stack}'
                position += ' and they have folded' if player.is_folded else ''
                position += '.\n'
                opponent_positions.append(position)
        return opponent_positions

    def update(self, **kwargs) -> 'PokerGameState':
        return replace(self, **kwargs)

    def update_player(self, player_idx: int, **kwargs) -> 'PokerGameState':
        """
        Update a specific player's state with the provided kwargs within a player tuple
        """
        players: List[Player] = list(self.players)
        player = players[player_idx]
        updated_player = player.update(**kwargs)
        players[player_idx] = updated_player
        return self.update(players=tuple(players))

    def get_player_by_name(self, search_name: str) -> Optional[Tuple[Player, int]]:
        for idx, player in enumerate(self.players):
            if player.name == search_name:
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
        if player.is_active:
            return False
    return True


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


def deal_hole_cards(game_state: PokerGameState) -> PokerGameState:
    """
    Deal hole cards to active poker players in the game state starting from the first player in the list of players.
    If there are active players, the 'deck' and player's 'hand' are updated in the returned game state. If there are
    no active players, the game state is returned unchanged.

    Notes:
        See 'is_player_active()' for evaluation of active players.
        Dealing starts from the 1st player in the list, does not take in to consideration dealer position.

    :param game_state: (PokerGameState)
        The current state of the poker game, which includes player information and the deck of cards.
    :return: (PokerGameState)
        The updated game state with new hands for active players and an updated deck.
    """
    for player in game_state.players:
        if player.is_active:
            player_idx = game_state.players.index(player)
            cards, new_deck = draw_cards(deck=game_state.deck, num_cards=2)
            new_hand = player.hand + cards
            game_state = game_state.update_player(player_idx=player_idx, hand=new_hand)
            game_state = game_state.update(deck=new_deck)

    # Return a new game state with the updated deck and players
    return game_state


##################################################################
##################      PLAYER_ACTIONS      ######################
##################################################################
def place_bet(game_state: PokerGameState, amount: int, player_idx: int = None) -> PokerGameState:
    """
    Handle the logic for a player placing a bet in a poker game, updating the game state accordingly.

    :param game_state: (PokerGameState)
        The current state of the poker game, including player information, pot details, and bet amounts.
    :param amount: (int)
        The amount the player wishes to bet.
    :param player_idx: (int, optional)
        The index of the player making the bet. If not provided, the default is the current player.

    :return: (PokerGameState)
        The updated state of the poker game after the bet has been placed.

    :raises ValueError:
        If the bet amount is less than or equal to zero or if the player does not have enough chips to cover the bet.
    """
    # Get the betting player, default to current player if betting player is not set and update their total bet amount
    player_idx = player_idx or game_state.current_player_idx
    betting_player = game_state.players[player_idx]


    # If the player has raised the bet we will want to reset all other players 'has_acted' flags.
    previous_high_bet = game_state.highest_bet   # Note the current high bet to compare later.

    # Check to see if player has enough to bet, adjust the amount to the player stack to prevent
    # them from betting more than they have and set them to all-in if they have bet everything
    # TODO: create a new pot when a player goes all in
    is_player_all_in = False
    if betting_player.stack <= amount:
        amount = betting_player.stack
        is_player_all_in = True

    # Update the players chip stack by removing the bet amount from the stack
    new_stack = betting_player.stack - amount
    new_bet = betting_player.bet + amount
    game_state = game_state.update_player(player_idx=player_idx,
                                          stack=new_stack,
                                          is_all_in=is_player_all_in,
                                          bet=new_bet)

    # Create a new pot with updated totals for the pot and the amount contributed by the betting player.
    new_pot = {**game_state.pot, 'total': game_state.pot['total'] + amount, betting_player.name: new_bet}
    game_state = game_state.update(pot=new_pot)

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
        if player.name != game_state.current_player.name or not exclude_current_player:
            game_state = game_state.update_player(player_idx=game_state.players.index(player),
                                                  has_acted=False)
    return game_state


def player_call(game_state):
    """
    Player calls the current bet
    """
    call_amount = game_state.highest_bet - game_state.current_player.bet
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
    new_discard_pile = game_state.discard_pile + game_state.current_player.hand
    game_state = game_state.update_player(player_idx=game_state.current_player_idx,
                                          is_folded=True,
                                          # Commenting out hand reset for now, keeping the cards allows for some other
                                          # ways to compare later. Removing them removes them from the UI as well.
                                          # hand=()         # TODO: decide whether or not to reset the hand
                                          )
    return game_state.update(discard_pile=new_discard_pile)


def player_raise(game_state, amount: int):
    """
    Player raises the current highest bet by the provided amount.
    """
    # Calculate the cost_to_call as the difference between the current highest bet and the players current bet
    cost_to_call = game_state.highest_bet - game_state.current_player.bet
    game_state = place_bet(game_state=game_state, amount=amount + cost_to_call)
    return game_state


def player_all_in(game_state):
    """
    Player bets all of their remaining chips.
    """
    game_state = place_bet(game_state=game_state, amount=game_state.current_player.stack)
    return game_state


##################################################################
######################      GAME FLOW       ######################
##################################################################
def set_betting_round_start_player(game_state) -> PokerGameState:
    """
    Set the starting player for the betting round based on the current state of the game.

    If there are community cards dealt, the next active player after the dealer will start the betting round.
    Otherwise, the player after the two seats from the dealer starts the betting round.

    :param game_state: (PokerGameState)
        The current state of the poker game, including players, dealer index, and community cards.
    :return: (PokerGameState)
        Updated state of the poker game with the current player index set for the betting round start.
    """
    if len(game_state.community_cards) > 0:
        first_action_player_idx = get_next_active_player_idx(players=game_state.players,
                                                             relative_player_idx=game_state.current_dealer_idx)
    else:
        first_action_player_idx = get_next_active_player_idx(players=game_state.players,
                                                             relative_player_idx=game_state.current_dealer_idx + 2)
    return game_state.update(current_player_idx=first_action_player_idx)

def deal_community_cards(game_state: PokerGameState) -> PokerGameState:
    """
    Deal the community cards based on the current phase of the game.
    This function expects that it will be called at the right time, after the betting round has ended.

    :param game_state: (PokerGameState)
        The current state of the game, including phase, deck, and community cards.
    :return: (PokerGameState)
        The updated game state after dealing the community cards.
    """
    # Assumes this function is called at the right time during the round.
    num_cards_to_draw = 3 if len(game_state.community_cards) == 0 else 1

    cards, new_deck = draw_cards(game_state.deck, num_cards=num_cards_to_draw)
    new_community_cards = game_state.community_cards + cards
    return game_state.update(community_cards=new_community_cards,
                             deck=new_deck)


def play_turn(game_state: PokerGameState, action: str, amount: int) -> PokerGameState:
    """
    Process the current player's turn given the action and amount provided.
    The player's 'has_acted' flag will be set to True here and is reset when
    the bet is raised or the betting round ends.
    The game's 'awaiting_action' flag is also set to False here.

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

    game_state = game_state.update_player(player_idx=game_state.current_player_idx,
                                          has_acted=True)

    if game_state.can_big_blind_take_pre_flop_action:
        game_state = game_state.update(pre_flop_action_taken=True)

    return game_state.update(awaiting_action=False)


def get_next_active_player_idx(players: Tuple[Player, ...], relative_player_idx: int) -> int:
    """
    Determines the index of the next active player in the players list based on is_player_active()

    :param players: (Tuple[Mapping, ...])
        A tuple of player mappings where each mapping represents player data.
    :param relative_player_idx: (int)
        The index of the current player.

    :return: (int)
        The index of the next active player.

    :raises ValueError:
        If there are no active players in the list.
    """
    player_count = len(players)
    # Start with the next player in the queue, save the starting index for later so we can take action
    # if we come all the way around without finding an active player
    starting_idx = relative_player_idx
    next_player_idx = (starting_idx + 1) % player_count

    while True:
        if players[next_player_idx].is_active:
            return next_player_idx
        if next_player_idx == starting_idx:
            return starting_idx
        next_player_idx = (next_player_idx + 1) % player_count  # Iterate through the players by 1 with a wrap around


def advance_to_next_active_player(game_state: PokerGameState) -> PokerGameState:
    """
    Move to the next active player in the game.
    """
    next_active_player_idx = get_next_active_player_idx(players=game_state.players, relative_player_idx=game_state.current_player_idx)
    return game_state.update(current_player_idx=next_active_player_idx)


def initialize_game_state(player_names: List[str]) -> PokerGameState:
    """
    Generate a new game state and prepare the game for the initial round of betting.
        - get a new deck of shuffled cards
        - deal cards to starting players
        - set dealer, current_player
    """
    # Create a tuple of Human and AI players to be added to the game state. Using a hard-coded human name
    ai_players = tuple(Player(name=n, stack=STACK_SIZE, is_human=False) for n in player_names)
    test_players = tuple(Player(name=n, stack=STACK_SIZE, is_human=True) for n in player_names)
    new_players = (Player(name=HUMAN_NAME, stack= STACK_SIZE, is_human=True),) + (ai_players if not TEST_MODE else test_players)
    game_state = PokerGameState(players=new_players)

    return game_state


def setup_hand(game_state: PokerGameState) -> PokerGameState:
    """
    Set up the initial hand by dealing hole cards and placing small and big blind bets.

    :param game_state: (dict)
        The current state of the game including players, deck, pot, etc.
    :return: (dict)
        Updated game state after dealing hole cards and placing initial blinds.
    :raises KeyError:
        If required keys are missing in the game state.
    :raises ValueError:
        If invalid player index or bet value is encountered.
    """
    game_state = deal_hole_cards(game_state)
    hand_ante = game_state.current_ante
    game_state = place_bet(game_state, int(hand_ante / 2), player_idx=game_state.small_blind_idx)
    game_state = place_bet(game_state, hand_ante, player_idx=game_state.big_blind_idx)
    return game_state


def reset_game_state_for_new_hand(game_state: PokerGameState) -> PokerGameState:
    """
    Sets all game_state flags to new hand state.
    Creates a new deck and resets the player's hand.
    Rotates the dealer position.
    Deals the hole cards.
    """
    # Create new players with reset flags to prepare for the next round
    new_players = []
    for player in game_state.players:
        new_player = Player(name=player.name, stack=player.stack, is_human=player.is_human)
        new_players.append(new_player)

    # Rotate the dealer position to the next active player in the game. This needs to come after the players are
    # reset so that get_next_active_player isn't based on the prior hands actions.
    new_dealer_idx = get_next_active_player_idx(players=tuple(new_players),
                                                relative_player_idx=game_state.current_dealer_idx)
    new_players = new_players[new_dealer_idx:] + new_players[:new_dealer_idx]

    # Remove players who have no chips left. This needs to come after the players are reset and the dealer is rotated
    # because we reference the game state's current dealer index in order to rotate.
    new_players = [player for player in new_players if player.stack > 0]

    # Create a new game state with just the properties we want to carry over (just the new players queue and the ante)
    return PokerGameState(players=tuple(new_players), current_ante=game_state.current_ante)


# TODO: refactor to only return PokerGameState, add winner info to the state
def determine_winner(game_state: PokerGameState) -> Dict:
    """
    Determine the winners and calculate the winnings for each player based on side pot contributions.
    :param game_state: (PokerGameState)
        The current state of the poker game, including players, community cards, and contributions.
    :return: (Dict)
        A dictionary with calculated winnings for each player and information on the winning hand.
            - 'winnings': {player_name: amount_won, ...}
            - 'winning_hand': details of the best hand
            - 'hand_name': Name of the winning hand
    """
    # Sort active players by contribution to handle side pots at showdown
    active_players = [p for p in game_state.players if not p.is_folded and p.bet > 0]
    active_players_sorted = sorted(active_players, key=lambda p: p.bet)
    # Prepare community cards for hand evaluation
    community_cards = [Card(card['rank'], card['suit']) for card in game_state.community_cards]
    # Track winnings for each player
    winnings = {}
    # Track each player's remaining contributions independently
    remaining_contributions = {p.name: p.bet for p in game_state.players}
    # List to track evaluated hands for all eligible players
    evaluated_hands = []

    # Award pots based on contribution tiers
    while active_players_sorted:
        # Minimum contribution for this tier (from the lowest all-in player, if applicable)
        tier_contribution = remaining_contributions[active_players_sorted[0].name]
        # Players eligible for this tier (all with contribution >= tier_contribution)
        eligible_players = [p for p in active_players_sorted if remaining_contributions[p.name] >= tier_contribution]
        # Calculate the pot for this tier by adding all the player's actual contributions up to the tier_contribution
        tier_pot = sum([min(remaining_contributions[p], tier_contribution) for p in remaining_contributions])
        # Evaluate hands for eligible players and find the winner(s)
        hands = []
        for player in eligible_players:
            player_hand = [Card(card['rank'], card['suit']) for card in player.hand]
            full_hand = HandEvaluator(player_hand + community_cards).evaluate_hand()
            hands.append((player.name, full_hand))
        # Add evaluated hands to the tracking list
        evaluated_hands.extend(hands)
        # Sort hands to find the best one(s) for the current tier
        hands.sort(key=lambda x: sorted(x[1]["kicker_values"]), reverse=True)
        hands.sort(key=lambda x: sorted(x[1]["hand_values"]), reverse=True)
        hands.sort(key=lambda x: x[1]["hand_rank"])
        # Determine winners for this tier
        best_hand = hands[0][1]
        tier_winners = [hand[0] for hand in hands if hand[1] == best_hand]
        split_amount = tier_pot // len(tier_winners)
        # Distribute winnings for this tier
        for winner_name in tier_winners:
            winnings[winner_name] = winnings.get(winner_name, 0) + split_amount
        # Subtract the tier contribution from each eligible player's contribution without modifying player objects
        for player in eligible_players:
            remaining_contributions[player.name] -= min(remaining_contributions[player.name], tier_contribution)
        # Remove players whose remaining contributions are zero
        active_players_sorted = [p for p in active_players_sorted if remaining_contributions[p.name] > 0]

    # Determine the best hand among all evaluated hands
    evaluated_hands.sort(key=lambda x: sorted(x[1]["kicker_values"]), reverse=True)
    evaluated_hands.sort(key=lambda x: sorted(x[1]["hand_values"]), reverse=True)
    evaluated_hands.sort(key=lambda x: x[1]["hand_rank"])
    best_overall_hand = evaluated_hands[0][1]

    # Prepare the result to include only winnings and winning hand details
    winner_info = {
        'winnings': winnings,
        'winning_hand': best_overall_hand["hand_values"] + best_overall_hand["kicker_values"],
        'hand_name': best_overall_hand['hand_name']
    }

    print(winner_info)
    return winner_info



def award_pot_winnings(game_state, winnings):
    # Reward winning players
    for name in winnings:
        if winnings[name] > 0:
            # Retrieve the player index for the player of the winning hand
            _, player_idx = game_state.get_player_by_name(name)
            current_stack = game_state.players[player_idx].stack
            new_stack_total = winnings[name] + current_stack
            game_state = game_state.update_player(player_idx=player_idx, stack=new_stack_total)
    return game_state

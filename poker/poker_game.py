import json
import logging
from sys import modules as sys_modules
from dataclasses import dataclass, field, replace
from random import shuffle
from typing import Tuple, Mapping, List, Optional, Dict

from core.card import Card
from .hand_evaluator import HandEvaluator, rank_to_display
from .utils import obj_to_dict
from .betting_context import BettingContext

logger = logging.getLogger(__name__)

# DEFAULTS
NUM_AI_PLAYERS = 2
STACK_SIZE = 10000      # player starting stack
ANTE = 50               # starting big blind
TEST_MODE = False
MAX_RAISES_PER_ROUND = 4  # Standard casino rule - unlimited when heads-up

def create_deck(shuffled: bool = True, random_seed: Optional[int] = None):
    """
    Create a deck as a tuple. If shuffled=True, uses the provided random_seed
    or current random state. Pure function with no side effects.
    """
    ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
    suits = ['Spades', 'Diamonds', 'Clubs', 'Hearts']
    deck = [Card(rank, suit) for rank in ranks for suit in suits]
    
    if shuffled:
        # Create a new Random instance to avoid modifying global state
        import random
        rng = random.Random(random_seed)
        # Create a copy and shuffle it
        shuffled_deck = deck.copy()
        rng.shuffle(shuffled_deck)
        return tuple(shuffled_deck)
    
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
    last_action: Optional[str] = None

    def to_dict(self):
        return {
            'name': self.name,
            'stack': self.stack,
            'is_human': self.is_human,
            'is_all_in': self.is_all_in,
            'is_folded': self.is_folded,
            'has_acted': self.has_acted,
            'last_action': self.last_action,
            'bet': self.bet,
            'hand': [card.to_dict() if hasattr(card, 'to_dict') else card for card in self.hand] if self.hand else None,
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
    deck: Tuple[Mapping, ...]  # Must be provided explicitly to support deterministic seeding
    discard_pile: Tuple[Mapping, ...] = field(default_factory=tuple)
    pot: Mapping = field(default_factory=lambda: {'total': 0})
    current_player_idx: int = 0
    current_dealer_idx: int = 0
    community_cards: Tuple[Mapping, ...] = field(default_factory=tuple)
    current_ante: int = ANTE
    last_raise_amount: int = ANTE  # Tracks the size of the last raise (defaults to big blind)
    raises_this_round: int = 0  # Track raises for cap enforcement (reset each betting round)
    ### FLAGS ###
    pre_flop_action_taken: bool = False
    awaiting_action: bool = False
    run_it_out: bool = False  # True when all players are all-in, auto-advance with delays
    has_revealed_cards: bool = False  # True once hole cards have been revealed during run-it-out
    newly_dealt_count: int = 0  # Number of community cards just dealt (3 for flop, 1 for turn/river)

    def to_dict(self) -> Dict:
        """
        Converts the GameState to a dict, including some of the dynamic properties that would be useful to display.
        """
        return {
            'players': [p.to_dict() for p in self.players],
            'deck': [card.to_dict() if hasattr(card, 'to_dict') else card for card in self.deck],
            'discard_pile': [card.to_dict() if hasattr(card, 'to_dict') else card for card in self.discard_pile],
            'pot': {**self.pot, 'highest_bet': self.highest_bet},
            'current_player_idx': self.current_player_idx,
            'current_dealer_idx': self.current_dealer_idx,
            'community_cards': [card.to_dict() if hasattr(card, 'to_dict') else card for card in self.community_cards],
            'current_ante': self.current_ante,
            'raises_this_round': self.raises_this_round,
            'pre_flop_action_taken': self.pre_flop_action_taken,
            'awaiting_action': self.awaiting_action,
            'run_it_out': self.run_it_out,
            'newly_dealt_count': self.newly_dealt_count,
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
    def min_raise_amount(self) -> int:
        """
        Returns the minimum raise amount according to poker rules.
        The minimum raise must be at least the size of the last raise (or big blind if no raises yet).
        """
        return self.last_raise_amount

    @property
    def call_amount(self) -> int:
        """Amount the current player needs to call (match the highest bet)."""
        return max(0, self.highest_bet - self.current_player.bet)

    @property
    def max_raise_amount(self) -> int:
        """Maximum raise amount (player's remaining stack)."""
        return self.current_player.stack

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
        Build the list functionally without mutations.
        """
        player = self.current_player
        # How much is it to call the bet for the player?
        player_cost_to_call = self.highest_bet - player.bet
        # Does the player have enough to call
        player_has_enough_to_call = player.stack > player_cost_to_call

        # Check raise cap - unlimited when heads-up (2 active players)
        num_active = len([p for p in self.players if not p.is_folded and p.stack > 0])
        is_heads_up = num_active == 2
        raise_cap_reached = not is_heads_up and self.raises_this_round >= MAX_RAISES_PER_ROUND
        can_raise = player.stack - player_cost_to_call > 0 and not raise_cap_reached

        # If the current player is last to act (aka big blind), and we're still in the pre-flop round
        if self.can_big_blind_take_pre_flop_action:
            options = ['check', 'all_in']
            if not raise_cap_reached:
                options.insert(1, 'raise')
            return options

        # Build options based on game state using list comprehension
        option_conditions = [
            ('fold', player_cost_to_call > 0),
            ('check', player_cost_to_call == 0),
            ('call', player_has_enough_to_call and player_cost_to_call > 0),
            ('raise', can_raise),
            ('all_in', player.stack > 0),
            # ('chat', False),  # Not implemented yet
        ]

        return [option for option, condition in option_conditions if condition]

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

        # Build positions list functionally based on player count
        base_positions = ["button", "small_blind_player", "big_blind_player"]
        
        # Define position mapping based on player count
        position_configs = {
            4: base_positions + ["under_the_gun"],
            5: base_positions + ["under_the_gun", "cutoff"],
            6: base_positions + ["under_the_gun", "middle_position_1", "cutoff"],
            7: base_positions + ["under_the_gun", "middle_position_1", "middle_position_2", "cutoff"],
            8: base_positions + ["under_the_gun", "middle_position_1", "middle_position_2", "middle_position_3", "cutoff"],
        }
        
        # Get positions for current player count (default to base if not in mapping)
        all_positions = position_configs.get(num_players, base_positions)

        # Create the dictionary to map each position to the corresponding player
        return {
            position: self.players[(current_dealer_idx + i) % num_players].name
            for i, position in enumerate(all_positions)
            if i < num_players  # Only include positions we have players for
        }

    @property
    def opponent_status(self) -> List[str]:
        """
        Get status of all players. Properties shouldn't take parameters,
        so this returns status for all players.
        """
        return [
            f'{player.name} has ${player.stack}'
            + (' and they have folded' if player.is_folded else '')
            + '.\n'
            for player in self.players
        ]

    def update(self, **kwargs) -> 'PokerGameState':
        return replace(self, **kwargs)

    def update_player(self, player_idx: int, **kwargs) -> 'PokerGameState':
        """
        Update a specific player's state with the provided kwargs within a player tuple.
        Uses functional approach without list mutations.
        """
        updated_players = tuple(
            player.update(**kwargs) if i == player_idx else player
            for i, player in enumerate(self.players)
        )
        return self.update(players=updated_players)

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
    if player_idx is None:
        player_idx = game_state.current_player_idx
    betting_player = game_state.players[player_idx]


    # If the player has raised the bet we will want to reset all other players 'has_acted' flags.
    previous_high_bet = game_state.highest_bet   # Note the current high bet to compare later.

    # Check to see if player has enough to bet, adjust the amount to the player stack to prevent
    # them from betting more than they have and set them to all-in if they have bet everything
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
    updated_players = tuple(
        player if (exclude_current_player and idx == game_state.current_player_idx)
        else player.update(has_acted=False, last_action=None)
        for idx, player in enumerate(game_state.players)
    )
    return game_state.update(players=updated_players)


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


def player_raise(game_state, raise_to_amount: int):
    """
    Player raises TO the specified total bet amount.

    Args:
        game_state: Current game state
        raise_to_amount: Total amount player wants to bet TO (not increment).
                        This is the final bet amount the player will have.

    Auto-corrects:
        - If > stack: converts to all-in
        - If < min_raise_to: uses min_raise_to (unless all-in)

    Returns:
        Updated game state after the raise.
    """
    player = game_state.current_player
    context = BettingContext.from_game_state(game_state)

    # Validate and auto-correct the raise amount
    sanitized_amount, correction_msg = context.validate_and_sanitize(raise_to_amount)

    if correction_msg:
        logger.debug(f"[RAISE] {player.name}: {correction_msg}")

    # If raising to all-in, use the all_in function for proper handling
    if sanitized_amount == player.stack + player.bet:
        return player_all_in(game_state)

    # Calculate the raise increment (for tracking min raise) and total to add
    raise_by_amount = sanitized_amount - game_state.highest_bet
    total_to_add = sanitized_amount - player.bet

    # Place the bet (total amount to add to reach the raise_to amount)
    game_state = place_bet(game_state=game_state, amount=total_to_add)

    # Track the raise amount for minimum raise calculations and increment raise counter
    game_state = game_state.update(
        last_raise_amount=raise_by_amount,
        raises_this_round=game_state.raises_this_round + 1
    )
    return game_state


def player_all_in(game_state):
    """
    Player bets all of their remaining chips.
    Counts as a raise if the amount exceeds the cost to call.

    Updates last_raise_amount only for "full raises" - where the raise increment
    is >= the current last_raise_amount. A "short all-in" (raising less than the
    minimum) doesn't change the min raise for subsequent players.
    """
    player = game_state.current_player
    previous_highest_bet = game_state.highest_bet
    cost_to_call = previous_highest_bet - player.bet
    all_in_amount = player.stack

    game_state = place_bet(game_state=game_state, amount=all_in_amount)

    # Count as a raise if going all-in for more than the call amount
    if all_in_amount > cost_to_call:
        # Calculate the raise increment (how much above the previous high bet)
        new_highest_bet = game_state.highest_bet
        raise_by = new_highest_bet - previous_highest_bet

        # Only update last_raise_amount if this is a "full raise" (>= current min raise)
        # A "short all-in" doesn't reopen betting or change the min raise
        updates = {'raises_this_round': game_state.raises_this_round + 1}
        if raise_by >= game_state.last_raise_amount:
            updates['last_raise_amount'] = raise_by

        game_state = game_state.update(**updates)
    return game_state


##################################################################
######################      GAME FLOW       ######################
##################################################################
def set_betting_round_start_player(game_state) -> Optional[PokerGameState]:
    """
    Set the starting player for the betting round based on the current state of the game.

    If there are community cards dealt, the next active player after the dealer will start the betting round.
    Otherwise, the player after the two seats from the dealer starts the betting round.

    :param game_state: (PokerGameState)
        The current state of the poker game, including players, dealer index, and community cards.
    :return: (Optional[PokerGameState])
        Updated state of the poker game with the current player index set for the betting round start,
        or None if no active players exist (betting round should not start).
    """
    if len(game_state.community_cards) > 0:
        first_action_player_idx = get_next_active_player_idx(players=game_state.players,
                                                             relative_player_idx=game_state.current_dealer_idx)
    else:
        first_action_player_idx = get_next_active_player_idx(players=game_state.players,
                                                             relative_player_idx=game_state.current_dealer_idx + 2)
    if first_action_player_idx is None:
        return None  # No active players, betting round should not start
    # Clear all players' last_action for a clean slate at the start of each betting round
    updated_players = tuple(
        player.update(last_action=None) for player in game_state.players
    )
    return game_state.update(current_player_idx=first_action_player_idx, players=updated_players)

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
                             deck=new_deck,
                             newly_dealt_count=num_cards_to_draw)


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
                                          has_acted=True,
                                          last_action=action)

    if game_state.can_big_blind_take_pre_flop_action:
        game_state = game_state.update(pre_flop_action_taken=True)

    return game_state.update(awaiting_action=False)


def get_next_active_player_idx(players: Tuple[Player, ...], relative_player_idx: int) -> Optional[int]:
    """
    Determines the index of the next active player in the players list based on is_player_active()

    :param players: (Tuple[Mapping, ...])
        A tuple of player mappings where each mapping represents player data.
    :param relative_player_idx: (int)
        The index of the current player.

    :return: (Optional[int])
        The index of the next active player, or None if no active players exist
        (signals betting round should end - all players folded/all-in).
    """
    player_count = len(players)
    # Normalize starting_idx to valid range (handles cases like dealer_idx + 2 exceeding player count)
    starting_idx = relative_player_idx % player_count
    next_player_idx = (starting_idx + 1) % player_count

    while True:
        if players[next_player_idx].is_active:
            return next_player_idx
        if next_player_idx == starting_idx:
            # No active players found - return None to signal betting round should end
            # (e.g., trigger showdown when all players are all-in or folded)
            return None
        next_player_idx = (next_player_idx + 1) % player_count  # Iterate through the players by 1 with a wrap around


def advance_to_next_active_player(game_state: PokerGameState) -> Optional[PokerGameState]:
    """
    Move to the next active player in the game.

    :return: Updated game state with next active player, or None if no active players exist
             (signals betting round should end - all players folded/all-in).
    """
    next_active_player_idx = get_next_active_player_idx(players=game_state.players, relative_player_idx=game_state.current_player_idx)
    if next_active_player_idx is None:
        return None  # Signal: no active players, betting should end
    return game_state.update(current_player_idx=next_active_player_idx)


def initialize_game_state(
    player_names: List[str],
    human_name: str = "Player",
    starting_stack: int = STACK_SIZE,
    big_blind: int = ANTE
) -> PokerGameState:
    """
    Generate a new game state and prepare the game for the initial round of betting.
        - get a new deck of shuffled cards
        - deal cards to starting players
        - set dealer, current_player

    :param player_names: List of AI player names
    :param human_name: Name of the human player (default: "Player")
    :param starting_stack: Starting chip stack for each player (default: 10000)
    :param big_blind: Starting big blind amount (default: 50)
    """
    # Validate no duplicate names
    all_names = [human_name] + list(player_names)
    if len(all_names) != len(set(all_names)):
        raise ValueError(f"Duplicate player names are not allowed: {all_names}")

    # Create a tuple of Human and AI players to be added to the game state
    ai_players = tuple(Player(name=n, stack=starting_stack, is_human=False) for n in player_names)
    test_players = tuple(Player(name=n, stack=starting_stack, is_human=True) for n in player_names)
    new_players = (Player(name=human_name, stack=starting_stack, is_human=True),) + (ai_players if not TEST_MODE else test_players)
    game_state = PokerGameState(
        players=new_players,
        deck=create_deck(shuffled=True),
        current_ante=big_blind,
        last_raise_amount=big_blind
    )

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


def reset_game_state_for_new_hand(
    game_state: PokerGameState,
    deck_seed: Optional[int] = None
) -> PokerGameState:
    """
    Sets all game_state flags to new hand state.
    Creates a new deck and resets the player's hand.
    Rotates the dealer position.
    Deals the hole cards.

    Args:
        game_state: Current game state
        deck_seed: Optional seed for deterministic deck shuffling (for A/B experiments)
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
    if new_dealer_idx is None:
        raise ValueError("No active players for dealer assignment")
    new_players = new_players[new_dealer_idx:] + new_players[:new_dealer_idx]

    # Remove players who have no chips left. This needs to come after the players are reset and the dealer is rotated
    # because we reference the game state's current dealer index in order to rotate.
    new_players = [player for player in new_players if player.stack > 0]

    # Create a new game state with just the properties we want to carry over (just the new players queue and the ante)
    # last_raise_amount must be set to current_ante so the min raise is correct for the current blind level.
    # In no-limit Texas Hold'em, the minimum raise is defined relative to the size of the *last* raise.
    # At the start of a hand, the big blind is effectively the first "raise" and sets that baseline.
    # In this code, current_ante represents the big blind amount, so by initializing last_raise_amount to
    # current_ante we ensure that pre-flop min-raise calculations (e.g., bet logic that does `min_raise =
    # last_raise_amount + amount_to_call`) treat the blind as the last raise size. If we set last_raise_amount
    # to 0 or left it from the previous hand, the computed minimum raise for the new hand would be incorrect.
    return PokerGameState(
        players=tuple(new_players),
        deck=create_deck(shuffled=True, random_seed=deck_seed),
        current_ante=game_state.current_ante,
        last_raise_amount=game_state.current_ante,
        newly_dealt_count=0
    )


def determine_winner(game_state: PokerGameState) -> Dict:
    """
    Determine the winners and calculate the winnings for each player based on side pot contributions.
    :param game_state: (PokerGameState)
        The current state of the poker game, including players, community cards, and contributions.
    :return: (Dict)
        A dictionary with pot breakdown and information on the winning hand.
            - 'pot_breakdown': list of pots with winners and amounts
            - 'winning_hand': details of the best hand
            - 'hand_name': Name of the winning hand
    """
    # Sort active players by contribution to handle side pots at showdown
    active_players = [p for p in game_state.players if not p.is_folded and p.bet > 0]
    # Handle edge case: no active players
    if not active_players:
        return {'pot_breakdown': [], 'winning_hand': [], 'hand_name': '', 'hand_rank': 10}
    active_players_sorted = sorted(active_players, key=lambda p: p.bet)
    # Prepare community cards for hand evaluation (handle both Card objects and dicts)
    community_cards = [
        card if isinstance(card, Card) else Card(card['rank'], card['suit'])
        for card in game_state.community_cards
    ]
    # Track pot breakdown for each tier
    pot_breakdown = []
    pot_index = 0
    # Track each player's remaining contributions independently
    remaining_contributions = {p.name: p.bet for p in game_state.players}
    # Track chips returned to players who over-contributed (no opponents to contest)
    returned_chips = {}
    # List to track evaluated hands for all eligible players
    evaluated_hands = []

    # Award pots based on contribution tiers
    while active_players_sorted:
        # Minimum contribution for this tier (from the lowest all-in player, if applicable)
        tier_contribution = remaining_contributions[active_players_sorted[0].name]
        # Players eligible for this tier (all with contribution >= tier_contribution)
        eligible_players = [p for p in active_players_sorted if remaining_contributions[p.name] >= tier_contribution]

        # If only one player is eligible AND this is a side pot situation (not the main pot),
        # silently return their excess chips (no opponents to contest this tier)
        # Note: pot_index > 0 means we've already processed at least one pot
        if len(eligible_players) == 1 and pot_index > 0:
            single_player = eligible_players[0]
            excess_amount = remaining_contributions[single_player.name]
            returned_chips[single_player.name] = returned_chips.get(single_player.name, 0) + excess_amount
            remaining_contributions[single_player.name] = 0
            active_players_sorted = [p for p in active_players_sorted if remaining_contributions[p.name] > 0]
            continue  # Skip to next tier - no pot entry created

        # Calculate the pot for this tier by adding all the player's actual contributions up to the tier_contribution
        tier_pot = sum([min(remaining_contributions[p], tier_contribution) for p in remaining_contributions])
        # Evaluate hands for eligible players and find the winner(s)
        hands = []
        for player in eligible_players:
            player_hand = [
                card if isinstance(card, Card) else Card(card['rank'], card['suit'])
                for card in player.hand
            ]
            full_hand = HandEvaluator(player_hand + community_cards).evaluate_hand()
            hands.append((player.name, full_hand))
        # Add evaluated hands to the tracking list
        evaluated_hands.extend(hands)
        # Sort hands to find the best one(s) for the current tier
        # Note: kicker_values and hand_values are already sorted descending by HandEvaluator
        # Using sorted() here would re-sort them ascending, breaking element-by-element comparison
        hands.sort(key=lambda x: x[1]["kicker_values"], reverse=True)
        hands.sort(key=lambda x: x[1]["hand_values"], reverse=True)
        hands.sort(key=lambda x: x[1]["hand_rank"])
        # Determine winners for this tier
        best_hand = hands[0][1]
        tier_winners = [hand[0] for hand in hands if hand[1] == best_hand]

        # Calculate split amount and remainder (odd chips)
        base_split_amount = tier_pot // len(tier_winners)
        remainder = tier_pot % len(tier_winners)

        # Standard poker rule: odd chips go to players closest to dealer's left
        # Sort winners by seat position relative to dealer
        player_positions = {p.name: idx for idx, p in enumerate(game_state.players)}
        num_players = len(game_state.players)
        dealer_idx = game_state.current_dealer_idx

        def distance_from_dealer(player_name):
            """Calculate seats to the left of dealer (1 = immediately left, etc.)

            The dealer (distance 0) receives odd chips last, so we return
            num_players instead of 0 to place dealer at the end of the order.
            """
            pos = player_positions.get(player_name, 0)
            dist = (pos - dealer_idx) % num_players
            return dist if dist > 0 else num_players

        # Sort winners by distance from dealer (closest to left gets odd chip first)
        sorted_winners = sorted(tier_winners, key=distance_from_dealer)

        # Build winner payouts with odd chips distributed
        winner_payouts = []
        for i, name in enumerate(sorted_winners):
            extra_chip = 1 if i < remainder else 0
            winner_payouts.append({'name': name, 'amount': base_split_amount + extra_chip})

        # Add pot info to breakdown
        pot_name = 'Main Pot' if pot_index == 0 else f'Side Pot {pot_index}'
        pot_breakdown.append({
            'pot_name': pot_name,
            'total_amount': tier_pot,
            'winners': winner_payouts,
            'hand_name': best_hand['hand_name']
        })
        pot_index += 1
        # Subtract the tier contribution from each eligible player's contribution without modifying player objects
        for player_name in remaining_contributions:
            remaining_contributions[player_name] -= min(remaining_contributions[player_name], tier_contribution)
        # Remove players whose remaining contributions are zero
        active_players_sorted = [p for p in active_players_sorted if remaining_contributions[p.name] > 0]

    # Determine the best hand among all evaluated hands
    evaluated_hands.sort(key=lambda x: x[1]["kicker_values"], reverse=True)
    evaluated_hands.sort(key=lambda x: x[1]["hand_values"], reverse=True)
    evaluated_hands.sort(key=lambda x: x[1]["hand_rank"])
    best_overall_hand = evaluated_hands[0][1]

    # Prepare the result to include pot breakdown and winning hand details
    # Keep raw numeric values for internal use (e.g., pressure_detector comparisons)
    # and convert to display names for UI (e.g., 14 -> 'A', 11 -> 'J')
    raw_hand_values = best_overall_hand["hand_values"] + best_overall_hand["kicker_values"]
    display_hand = [rank_to_display(v) for v in raw_hand_values]
    winner_info = {
        'pot_breakdown': pot_breakdown,
        'returned_chips': returned_chips,  # Chips returned to players who over-contributed
        'winning_hand': display_hand,
        'winning_hand_values': raw_hand_values,  # Keep numeric values for internal comparisons
        'hand_name': best_overall_hand['hand_name'],
        'hand_rank': best_overall_hand['hand_rank']
    }

    logger.debug(f"[HAND_END] {winner_info}")
    return winner_info


def award_pot_winnings(game_state, winner_info):
    """Award winnings to winning players by adding to their stack."""
    # Sum winnings per player from pot_breakdown
    winnings = {}
    for pot in winner_info.get('pot_breakdown', []):
        for winner in pot['winners']:
            winnings[winner['name']] = winnings.get(winner['name'], 0) + winner['amount']

    # Add returned chips (excess contributions with no opponents to contest)
    for name, amount in winner_info.get('returned_chips', {}).items():
        winnings[name] = winnings.get(name, 0) + amount

    # Reward winning players
    for name, amount in winnings.items():
        if amount > 0:
            # Retrieve the player index for the player of the winning hand
            _, player_idx = game_state.get_player_by_name(name)
            current_stack = game_state.players[player_idx].stack
            new_stack_total = amount + current_stack
            game_state = game_state.update_player(player_idx=player_idx, stack=new_stack_total)
    return game_state

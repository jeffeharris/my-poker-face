from enum import Enum, auto
from typing import List

from .poker_game import PokerGameState, setup_hand, set_betting_round_start_player, reset_player_action_flags, \
    are_pot_contributions_valid, deal_community_cards, determine_winner, reset_game_state_for_new_hand, \
    award_pot_winnings


class PokerPhase(Enum):
    """
    An enum class that represents different phases of the poker game.
    """
    INITIALIZING_GAME = auto()
    INITIALIZING_HAND = auto()
    HAND_INITIALIZED = auto()
    INITIALIZING_BET_ROUND = auto()
    PRE_FLOP = auto()
    DEALING_CARDS = auto()
    FLOP = auto()
    TURN = auto()
    RIVER = auto()
    SHOWDOWN = auto()
    EVALUATING_HAND = auto()
    HAND_OVER = auto()

    @classmethod
    def _to_string(cls, phase):
        phase_to_strings = {
            cls.INITIALIZING_GAME: "Initializing Game",
            cls.INITIALIZING_HAND: "Initializing Hand",
            cls.INITIALIZING_BET_ROUND: "Initializing Betting Round",
            cls.PRE_FLOP: "Pre-Flop",
            cls.DEALING_CARDS: "Dealing Cards",
            cls.FLOP: "Flop",
            cls.TURN: "Turn",
            cls.RIVER: "River",
            cls.SHOWDOWN: "Showdown",
            cls.EVALUATING_HAND: "Determining Winners",
            cls.HAND_OVER: "Hand Over",
        }
        return phase_to_strings.get(phase, "Unknown Phase")

    def __str__(self):
        return self._to_string(self)


class PokerStateMachine:
    def __init__(self, game_state: PokerGameState):
        self.game_state = game_state
        self.phase = PokerPhase.INITIALIZING_GAME
        self.snapshots = []
        self.stats = {
            'hand_count': 0,
        }
    
    @property
    def current_phase(self):
        return self.phase
    
    @current_phase.setter
    def current_phase(self, value):
        self.phase = value

    @property
    def next_phase(self):
        current_phase = self.phase

        def next_betting_round_phase() -> PokerPhase:
            num_cards_dealt = len(self.game_state.community_cards)
            # What is the next phase of the game based on the number of community cards currently dealt
            num_cards_dealt_to_next_phase = {
                0: PokerPhase.PRE_FLOP,
                3: PokerPhase.FLOP,
                4: PokerPhase.TURN,
                5: PokerPhase.RIVER
            }
            return num_cards_dealt_to_next_phase[num_cards_dealt]

        next_phase_map = {
            PokerPhase.INITIALIZING_GAME: PokerPhase.INITIALIZING_HAND,
            PokerPhase.INITIALIZING_HAND: PokerPhase.PRE_FLOP,
            PokerPhase.INITIALIZING_BET_ROUND: next_betting_round_phase(),
            PokerPhase.PRE_FLOP: PokerPhase.DEALING_CARDS,
            PokerPhase.FLOP: PokerPhase.DEALING_CARDS,
            PokerPhase.TURN: PokerPhase.DEALING_CARDS,
            PokerPhase.DEALING_CARDS: PokerPhase.INITIALIZING_BET_ROUND,
            PokerPhase.RIVER: PokerPhase.EVALUATING_HAND,
            PokerPhase.SHOWDOWN: PokerPhase.EVALUATING_HAND,
            PokerPhase.EVALUATING_HAND: PokerPhase.HAND_OVER,
            PokerPhase.HAND_OVER: PokerPhase.INITIALIZING_HAND
        }

        return next_phase_map[current_phase]

    def run_until_player_action(self):
        while not self.game_state.awaiting_action:
            self.advance_state()

    def run_until(self, phases: List[PokerPhase]):
        """
        Run the state machine to the next phase until it reaches a player action or a phase in the list of phases.
        """
        while self.phase not in phases:
            self.advance_state()
            if self.game_state.awaiting_action:
                break

    def advance_state(self):
        # print(1, self.phase, 'at start of state machine')
        self.snapshots.append(self.game_state)
        if self.phase == PokerPhase.INITIALIZING_GAME:
            self.initialize_game()
        elif self.phase == PokerPhase.INITIALIZING_HAND:
            self.initialize_hand()
        elif self.phase == PokerPhase.INITIALIZING_BET_ROUND:
            self.initialize_betting_round()
        elif self.phase in [PokerPhase.PRE_FLOP,
                            PokerPhase.FLOP,
                            PokerPhase.TURN,
                            PokerPhase.RIVER]:
            self.run_betting_round()
        elif self.phase == PokerPhase.DEALING_CARDS:
            self.deal_cards()
        elif self.phase == PokerPhase.SHOWDOWN:
            self.showdown()
        elif self.phase == PokerPhase.EVALUATING_HAND:
            self.evaluating_hand()
        elif self.phase == PokerPhase.HAND_OVER:
            self.hand_over()
        else:
            raise Exception(f"Invalid game phase: {self.phase}")

    def update_phase(self, phase=None):
        """
        Change the phase of the state machine, defaults to the next_phase based on the current_phase.

        :param phase: (PokerPhase)
            Defaults to self.next_phase if not provided. Can be set in cases where the path is not direct based on the
            next_phase_map. Example of this is in the initialize_betting_round where the state can advance to SHOWDOWN
            if there are no more player actions possible based on the state or it can advance to net_phase if play can
            continue.
        """
        self.phase = phase or self.next_phase

    def initialize_game(self):
        # print(2, self.phase, 'game is ready')
        self.update_phase()

    def initialize_hand(self):
        # print(3, self.phase,
        #       f"there are {len(self.game_state.community_cards)} community cards so far, waiting for 5 to be dealt")
        self.game_state = setup_hand(self.game_state)
        self.game_state = set_betting_round_start_player(game_state=self.game_state)
        self.update_phase()
        # print(4, self.phase, "hand is ready")

    def initialize_betting_round(self):
        num_active_players = len([p.name for p in self.game_state.players if not p.is_folded])

        if num_active_players == 1:
            self.update_phase(phase=PokerPhase.SHOWDOWN)
        else:
            # print(8, self.phase, "pot is settled, dealing cards and resetting betting round")
            self.game_state = reset_player_action_flags(self.game_state)
            self.game_state = set_betting_round_start_player(self.game_state)
            self.update_phase(phase=self.next_phase)
        # print(5, self.phase, "betting round players set ready to start")

    def run_betting_round(self):
        pot_is_settled = not (not are_pot_contributions_valid(self.game_state)
                              # number of players still able to bet is greater than 1  TODO: can this be moved into the same are_pot_valid... check?
                              and len([p.name for p in self.game_state.players if not p.is_folded or not p.is_all_in]) > 1)
        if not are_pot_contributions_valid(self.game_state):
            # print(7, self.phase, f"pot is not settled, {self.game_state.current_player.name} is up next")
            self.game_state = self.game_state.update(awaiting_action=True)  # Expect this flag to be reset after player action has been taken in play_turn
        elif pot_is_settled and self.phase != PokerPhase.EVALUATING_HAND:
            self.update_phase()

    def deal_cards(self):
        self.game_state = deal_community_cards(self.game_state)
        # print(6, self.phase,
        #       f"{len(self.game_state.community_cards)} community cards have been dealt")
        self.update_phase()

    def showdown(self):
        active_players = [p for p in self.game_state.players if not p.is_folded]
        num_cards_dealt = len(self.game_state.community_cards)
        num_cards_dealt_to_last_phase = {
            0: PokerPhase.PRE_FLOP,
            3: PokerPhase.FLOP,
            4: PokerPhase.TURN,
            5: PokerPhase.RIVER
        }
        last_phase = num_cards_dealt_to_last_phase[num_cards_dealt]
        # if last_phase == PokerPhase.RIVER and len(active_players) > 1:
        # If only 1 player remaining, award the player without any further cards dealt
        # If all active players are all-in, show the cards
        # If more than 1 player is in the pot after the river round of betting, show the cards
        self.update_phase()

    def evaluating_hand(self):
        winner_info = determine_winner(self.game_state)
        self.game_state = award_pot_winnings(self.game_state, winner_info['winnings'])
        if winner_info:
            self.update_phase()

    def hand_over(self):
        self.game_state = reset_game_state_for_new_hand(self.game_state)
        self.stats['hand_count'] += 1
        if self.stats['hand_count'] % 5 == 0:
            self.game_state = self.game_state.update(current_ante=self.game_state.current_ante*2)
        hand_is_reset = True    # TODO: implement a check before advancing to the next phase
        if hand_is_reset:
            self.update_phase()

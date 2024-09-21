import random
from typing import List, Dict, Optional

from core.interface import Interface
from poker.poker_settings import PokerSettings

from poker.poker_action import PokerAction, PlayerAction

from core.deck import Deck
from core.assistants import OpenAILLMAssistant
from poker.poker_hand_pot import PokerHandPot
from poker.poker_player import PokerPlayer, AIPokerPlayer
from poker.utils import shift_list_left, PokerHandPhase


class SystemPrompt:
    LARGE_PROMPT = \
    """You are Cookie Masterson, the snarky and witty narrator of the web-based game "My Poker Face: The Timeless Poker League." Your role is to provide humorous and sarcastic commentary throughout the game, interacting with the player and AI characters. Your tone should be playful, sarcastic, and occasionally over-the-top, always leaning into the absurdity of the game's setting where historical figures and celebrities from different eras play poker together in a timeless dimension.
    
    **Context:** 
    The player, {player_name}, is currently involved in a poker game with various AI characters, including {ai_characters}, in the Timeless Lounge—a casino where time is suspended, and anything can happen. Your job is to comment on the player's actions, the AI characters' antics, and any random events that occur during the game.
    
    ### **Scenarios:**
    
    1. **Introduction to the Game/Session Start:**
       "Welcome back to the Timeless Lounge, where the stakes are high, the characters are... questionable, and time doesn’t matter—just like your chances of winning. I’m Cookie Masterson, your guide through this chaotic carnival of cards. Let’s see what kind of mess you get yourself into today!"
    
    2. **Player Action Commentary:**
       - If {player_action} is bold: 
         "Ooh, look at you, {player_name}! Bold move, let’s see if it pays off... or if you’ll be laughing stock for the next century."
       - If {player_action} is hesitant:
         "Come on, {player_name}, I’ve seen snails make decisions faster than this. You’re not actually thinking, are you?"
       - If {player_action} wins the hand:
         "Well, would you look at that! A win! Who knew miracles could happen outside of Christmas movies?"
       - If {player_action} loses the hand:
         "Ouch! That’s gotta hurt. Maybe next time, try playing with a brain instead of... whatever that was."
    
    3. **AI Character Interaction:**
       - If Cleopatra flirts:
         "Cleopatra, using her legendary charm to distract her opponents. You might want to focus on your cards, though—you’re not *that* charming."
       - If Elvis sings:
         "And here comes Elvis, crooning his way into another bad decision. Stick to the poker, King."
       - If Caesar rants:
         "Caesar’s speechifying again—someone get him a laurel wreath, or maybe just a clue."
    
    4. **Random Events/Minigames:**
       - If a random event occurs:
         "Oh, what’s this? Looks like the Timeless Lounge is throwing you a curveball. Better limber up, {player_name}, it’s about to get weird."
       - If a quirky minigame starts:
         "Thumb wrestling with Caesar? Really? This game’s gone off the rails faster than a Roman chariot on a greased track."
    
    5. **Session End/Wrap-Up:**
       - If {session_outcome} is the end of the game:
         "Well, that’s another round in the books. Whether you’re leaving with your head held high or hiding under the table, remember: It’s all in good fun... unless you lose, in which case, it’s all your fault."
       - If {session_outcome} is the player’s debt increases:
         "Ooh, tough break! Looks like you’re digging yourself deeper into that debt hole. Better luck next time—or maybe just better luck!"
    
    6. **General Snarky Remarks:**
       - If {game_state} is slow:
         "Hey, don’t all jump in at once! I haven’t seen this much excitement since the time Caesar found out what a salad was."
       - If {game_state} is downtime:
         "While we wait for someone to make a move, why don’t you take a moment to reflect on your life choices? No rush, we’ve got all of time itself."
    
    ### **Instructions:**
    1. Always maintain Cookie Masterson’s sarcastic, witty, and playful tone.
    2. Integrate humor into every response, making sure it fits the current context of the game.
    3. Tailor the responses based on the player’s actions and the AI characters’ behavior, keeping the narrative lively and engaging.
    4. Use the context variables ({player_name}, {ai_characters}, {player_action}, {session_outcome}, {game_state}) to dynamically generate Cookie's commentary.
    
    Now, channel your inner Cookie Masterson and keep the game entertaining, sarcastic, and fun!"""

    SIMPLE_PROMPT = \
    """You are Cookie Masterson, the snarky and witty Table Manager and narrator of the web-based game "My Poker Face: The Timeless Poker League." Your role is to provide humorous and sarcastic commentary throughout the game, interacting with the player and AI characters. Your tone should be playful, sarcastic, and occasionally over-the-top, always leaning into the absurdity of the game's setting where historical figures and celebrities from different eras play poker together in a timeless dimension.
    
    **Context:** 
    The players are currently involved in a poker game with various AI characters, in the Timeless Lounge—a casino where time is suspended, and anything can happen. Your job is to comment on the player's actions, the AI characters' antics, and any random events that occur during the game.
    
    ### **Instructions:**
    1. Always maintain Cookie Masterson’s sarcastic, witty, and playful tone.
    2. Integrate humor into every response, making sure it fits the current context of the game.
    3. Tailor the responses based on the player’s actions and the AI characters’ behavior, keeping the narrative lively and engaging.
    
    Now, channel your inner Cookie Masterson and keep the game entertaining, sarcastic, and fun!"""

    GENERIC_PROMPT = \
    """You are the table manager for a celebrity poker game. You will be presented with a set of actions and comments that have happened.
    Please provide a brief summary of the events to share with the next player. Format your summaries as a bulleted list."""

MANAGER_PERSONA = "George Carlin"
SMALL_BLIND = 50

class RoundManager:
    assistant: OpenAILLMAssistant
    table_messages: List[str]
    deck: Deck
    players: List[PokerPlayer]
    table_positions: Dict[str, PokerPlayer]
    starting_players: List[PokerPlayer]
    remaining_players: List[PokerPlayer]
    dealer: PokerPlayer or None
    small_blind_player: PokerPlayer
    big_blind_player: PokerPlayer
    under_the_gun: PokerPlayer
    small_blind: int
    min_bet: int

    # TODO: <REFACTOR> Move these to a constraints class
    # Constraints used for initializing the AI PLayer attitude and confidence
    LESS_THAN_20_WORDS = "Use less than 20 words."
    LESS_THAN_50_WORDS = "Use less than 50 words."
    LESS_THAN_100_WORDS = "Use less than 100 words."

    def __init__(self):
        self.name = MANAGER_PERSONA
        self.assistant = self.initialize_assistant()
        self.table_messages = []
        self.deck = Deck()
        self.players = []
        self.table_positions = {}
        self.starting_players = []
        self.remaining_players = []
        self.dealer = None
        self.small_blind = SMALL_BLIND
        self.interface = Interface()

    def to_dict(self):
        dict_instance = {
            "name": self.name,
            "assistant": self.assistant.to_dict(),
            "table_messages": self.table_messages,
            "deck": self.deck.to_dict(),
            "players": [p.to_dict() for p in self.players],
            "table_positions": self.table_positions,
            "starting_players": [p.to_dict() for p in self.starting_players],
            "remaining_players": [p.to_dict() for p in self.remaining_players],
            "dealer": self.dealer.to_dict(),
            "small_blind": self.small_blind
        }
        return dict_instance

    @property
    def round_manager_state(self):
        state = {
            # "table_manager": self,
            "players": [p.to_dict() for p in self.players],
            "remaining_players": [p.to_dict() for p in self.remaining_players],
            "opponent_status": self.get_opponent_status(),
            "table_positions": self.get_table_positions(),
            "table_messages": self.table_messages,
            "deck": self.deck.to_dict(),
            "small_blind": self.small_blind
        }

        return state

    @property
    def small_blind_player(self):
        return self.players[(self.dealer_position + 1) % len(self.players)]

    @property
    def big_blind_player(self):
        return self.players[(self.dealer_position + 2) % len(self.players)]

    @property
    def under_the_gun(self):
        return self.players[(self.dealer_position + 3) % len(self.players)]

    @property
    def dealer_position(self):
        return self.players.index(self.dealer)

    @staticmethod
    def persona_prompt() -> str:
        prompt = SystemPrompt.GENERIC_PROMPT
        return prompt

    def add_players(self, player_names: List[str], ai=False):
        for name in player_names:
            self.players.append(AIPokerPlayer(name)) if ai else self.players.append(PokerPlayer(name))

    def initialize_ai_player(self, player, player_names):
        """Set initial confidence and attitude attributes for AI Poker Player."""
        # Choose AI attribute  at random, using 3 different values; low, medium and high for any attribute.
        i = random.randint(0, 2)
        player.confidence = player.initialize_attribute(
            "confidence",
            self.LESS_THAN_20_WORDS,
            player_names,
            mood=1
        )
        player.attitude = player.initialize_attribute(
            "attitude",
            self.LESS_THAN_20_WORDS,
            player_names,
            mood=i
        )

    def initialize_players(self):
        for player in self.players:
            if isinstance(player, AIPokerPlayer):
                player_names = [p.name for p in self.players if p != player]
                self.initialize_ai_player(player, player_names)

        self.starting_players = self.players.copy()
        self.remaining_players = self.players.copy()

    def get_table_positions(self) -> Dict[str, str]:
        table_positions = {"dealer": self.dealer.name,
                           "small_blind_player": self.small_blind_player.name,
                           "big_blind_player": self.big_blind_player.name,
                           "under_the_gun": self.under_the_gun.name
                           }
        return table_positions

    def rotate_dealer(self):
        """
        Rotates the dealer to the next player in the starting players list.
        If the new dealer has no money, recursively finds the next eligible dealer.

        Parameters:
        - None

        Returns:
        - None

        Usage example:

          game = Game()  # create an instance of the Game class
          game.rotate_dealer()  # rotate the dealer
        """

        # Find the current dealer's position in the starting players list
        current_index = self.starting_players.index(self.dealer)

        # Calculate the new dealer's index using modulo for wrap-around
        new_index = (current_index + 1) % len(self.starting_players)

        # Update the dealer to the new player at the calculated index
        self.dealer = self.starting_players[new_index]

        # Check if the new dealer has no money left
        if self.dealer.money <= 0:
            # Recursively find the next eligible dealer
            self.rotate_dealer()

    def set_remaining_players(self):
        self.remaining_players = [player for player in self.players if not player.folded]

    def setup_hand(self, poker_hand_pot, poker_hand_phase):
        self.set_remaining_players()  # TODO: <REFACTOR> review set_remaining_players to understand why it's here.
        self.dealer = self.players[random.randint(0, len(self.players) - 1)]
        self.post_blinds(poker_hand_pot)
        self.deal_hole_cards()

        start_player = self.determine_start_player(poker_hand_phase)

        index = self.players.index(start_player)  # Set index at the start_player
        round_queue = self.players.copy()  # Copy list of all players that started the hand, could include folded
        shift_list_left(round_queue, index)  # Move to the start_player

        return round_queue

    def determine_start_player(self, poker_hand_phase: PokerHandPhase):
        start_player = None
        if poker_hand_phase == PokerHandPhase.PRE_FLOP:
            # Player after big blind starts
            start_player = self.players[(self.dealer_position + 3) % len(self.players)]
        else:
            # Find the first player after the dealer who hasn't folded
            for j in range(1, len(self.players) + 1):
                index = (self.dealer_position + j) % len(self.players)
                if not self.players[index].folded:
                    start_player = self.players[index]
                    break
        return start_player

    def post_blinds(self, pot: PokerHandPot):
        small_blind = self.small_blind
        big_blind = small_blind * 2
        pot.add_to_pot(self.small_blind_player.name, self.small_blind_player.get_for_pot,small_blind)
        pot.add_to_pot(self.big_blind_player.name, self.big_blind_player.get_for_pot, big_blind)

    def deal_hole_cards(self):
        for player in self.players:
            self.deck.card_deck.deal(player.cards,2)

    def get_opponent_status(self, requesting_player=None) -> List[str]:
        opponent_positions = []
        for player in self.players:
            if player != requesting_player:
                position = f'{player.name} has ${player.money}'
                position += ' and they have folded' if player.folded else ''
                position += '.\n'
                opponent_positions.append(position)
        return opponent_positions

    @staticmethod
    def get_next_round_queue(round_queue, betting_player: Optional[PokerPlayer]):
        next_round_queue = round_queue.copy()
        if betting_player:
            index = round_queue.index(betting_player) + 1
        else:
            index = 1
        shift_list_left(next_round_queue, index)
        return next_round_queue

    # TODO: <REFACTOR> summarize_actions and summarize_actions_for_player have too much in common, better to combine so future updates are more maintainable
    def summarize_actions_for_player(self, actions: List[str] or str, player_name: str, constraints=LESS_THAN_50_WORDS) -> str:
        """
        Function should take in text descriptions of actions taken during a poker round and create a summary.
        """
        if actions is str:
            action_summary = actions
        else:
            summary_request = (f"In the style of {self.name}, please summarize the actions and comments from the hand since {player_name}'s last turn.\n"
                               f"Constraints: {constraints}\n"
                               f"Recent Actions:    {actions}\n")
            message = [{"role": "user", "content": summary_request}]
            response_json = self.assistant.get_response(message)
            action_summary = response_json.choices[0].message.content
        return action_summary

    def summarize_actions(self, actions: List[str] or str, constraints=LESS_THAN_100_WORDS) -> str:
        """
        Function should take in text descriptions of actions taken during a poker round and create a summary.
        """
        if actions is str:
            action_summary = actions
        else:
            summary_request = (
                f"In the style of {self.name}, please summarize the actions and comments from the hand.\n"
                f"Constraints: {constraints}\n"
                f"Recent Actions:    {actions}\n")
            message = [{"role": "user", "content": summary_request}]
            response_json = self.assistant.get_response(message)
            action_summary = response_json.choices[0].message.content
        return action_summary

    def initialize_assistant(self):
        return OpenAILLMAssistant(system_message=self.persona_prompt())

    def betting_round(self, poker_hand, player_queue: List[PokerPlayer], is_initial_round: bool = True):
        # Check to see if remaining players are all-in
        active_player_queue = self.initialize_active_players(player_queue, is_initial_round)

        if len(self.remaining_players) <= 0:
            raise ValueError("No remaining players left in the hand")

        for player in active_player_queue:
            if player.folded:
                continue

            all_in_count = 0
            for p in self.remaining_players:
                if p.money <= 0:
                    all_in_count += 1
            if all_in_count == len(self.remaining_players):
                return
            elif len(self.remaining_players) <= 1:
                return
            else:
                player_options = self.get_player_options(poker_hand, player, PokerSettings())

                poker_action = self.get_player_action(player, {**self.to_dict(), **poker_hand.hand_state,}, player_options)
                poker_hand.poker_actions.append(poker_action)

                if self.process_player_action(poker_hand, player, poker_action):
                    return

    @staticmethod
    def initialize_active_players(player_queue: List[PokerPlayer], is_initial_round: bool) -> List[PokerPlayer]:
        return player_queue.copy() if is_initial_round else player_queue[:-1]

###########################################################################################################
#####                                    PROCESS PLAYER ACTIONS                                       #####
###########################################################################################################
    @staticmethod
    def process_pot_update(poker_hand, player: PokerPlayer, amount_to_add: int):
        poker_hand.pots[0].add_to_pot(player.name, player.get_for_pot, amount_to_add)

    def handle_bet_or_raise(self, poker_hand, player: PokerPlayer, add_to_pot: int,
                            next_round_queue: List[PokerPlayer]):
        self.process_pot_update(poker_hand, player, add_to_pot)
        return self.betting_round(poker_hand, next_round_queue, is_initial_round=False)

    def handle_all_in(self, poker_hand, player: PokerPlayer, add_to_pot: int,
                      next_round_queue: List[PokerPlayer]):
        raising = add_to_pot > poker_hand.pots[0].current_bet
        self.process_pot_update(poker_hand, player, add_to_pot)
        if raising:
            return self.betting_round(poker_hand, next_round_queue, is_initial_round=False)
        else:
            # TODO: <FEATURE> create a side pot
            pass

    def handle_call(self, poker_hand, player: PokerPlayer, add_to_pot: int):
        self.process_pot_update(poker_hand, player, add_to_pot)

    def handle_fold(self, player: PokerPlayer):
        player.folded = True
        self.set_remaining_players()

    def process_player_action(self, poker_hand, player: PokerPlayer, poker_action: PokerAction) -> bool:
        player_action = poker_action.player_action
        amount = poker_action.amount

        if player_action in {PlayerAction.BET, PlayerAction.RAISE}:
            self.handle_bet_or_raise(poker_hand, player, amount,
                                self.get_next_round_queue(self.remaining_players, player))
            return True
        elif player_action == PlayerAction.ALL_IN:
            self.handle_all_in(poker_hand, player, amount,
                          self.get_next_round_queue(self.remaining_players, player))
            return True
        elif player_action == PlayerAction.CALL:
            self.handle_call(poker_hand, player, amount)
        elif player_action == PlayerAction.FOLD:
            self.handle_fold(player)
        elif player_action == PlayerAction.CHECK:
            return False
        elif player_action == PlayerAction.CHAT:
            # TODO: <FEATURE> implement handle_chat to open up ability for AIs to chat with each other or the player.
            pass
        else:
            raise ValueError("Invalid action selected: " + str(player_action))
        return False

###########################################################################################################
#####                           PLAYER INTERACTIONS AND OPTION SETTING                                #####
###########################################################################################################
    # TODO: <REFACTOR> change this to return the options as a PlayerAction enum
    def get_player_options(self, poker_hand, poker_player: PokerPlayer, settings: PokerSettings):
        # How much is it to call the bet for the player?
        player_cost_to_call = poker_hand.pots[0].get_player_cost_to_call(poker_player.name)
        # Does the player have enough to call
        player_has_enough_to_call = poker_player.money > player_cost_to_call
        # Is the current player also the big_blind TODO: <BUG> add "and have they played this hand yet"
        current_player_is_big_blind = (poker_player.name == self.big_blind_player.name)

        # If the current player is last to act (aka big blind), and we're still in the pre-flop round
        if (current_player_is_big_blind
                and poker_hand.current_phase == PokerHandPhase.PRE_FLOP
                and poker_hand.pots[0].current_bet == self.small_blind * 2):
            player_options = ['check', 'raise', 'all-in', 'chat']
        else:
            player_options = ['fold', 'check', 'call', 'bet', 'raise', 'all-in', 'chat']
            if player_cost_to_call == 0:
                player_options.remove('fold')
            if player_cost_to_call > 0:
                player_options.remove('check')
            if not player_has_enough_to_call or player_cost_to_call == 0:
                player_options.remove('call')
            if poker_hand.pots[0].current_bet > 0 or player_cost_to_call > 0:
                player_options.remove('bet')
            if poker_player.money - poker_hand.pots[0].current_bet <= 0 or 'bet' in player_options:
                player_options.remove('raise')
            if not settings.all_in_allowed or poker_player.money == 0:
                player_options.remove('all-in')

        poker_player.set_options(player_options)    # TODO: <REFACTOR> remove the player.options attribute and just have 1 set of options for the current player
        return player_options

    def get_player_action(self, player, hand_state, player_options) -> PokerAction:
        # if isinstance(player, AIPokerPlayer):
        #     return get_ai_player_action(player, hand_state)

        current_pot = hand_state["current_pot"]
        cost_to_call = current_pot.get_player_cost_to_call(player.name)

        # display_hand_update_text(hand_state, player)

        action = self.interface.request_action(player.options, "Enter action: \n")

        add_to_pot = 0
        if action is None:
            if "check" in player_options:
                action = "check"
            elif "call" in player_options:
                action = "call"
            else:
                action = "fold"
        if action in ["bet"]:
            add_to_pot = int(self.interface.get_user_input("Enter amount: "))
            action = "bet"
        elif action in ["raise"]:
            raise_amount = int(self.interface.get_user_input(f"Calling {cost_to_call}.\nEnter amount to raise: "))
            # add_to_pot = raise_amount - current_pot.current_bet
            add_to_pot = raise_amount + cost_to_call
            action = "raise"
        elif action in ["all-in"]:
            add_to_pot = player.money
            action = "all-in"
        elif action in ["call"]:
            add_to_pot = cost_to_call
            action = "call"
        elif action in ["fold"]:
            add_to_pot = 0
            action = "fold"
        elif action in ["check"]:
            add_to_pot = 0
            action = "check"
        elif action in ["show"]:
            pass
        elif action in ["quit"]:
            exit()
        elif action in ["chat"]:
            run_chat(hand_state)
            return self.get_player_action(player, hand_state, player_options)
        else:
            return self.get_player_action(player, hand_state, player_options)

        chat_message = self.interface.get_user_input("Enter table comment (optional): ")
        if chat_message != "":
            hand_state["table_messages"].append({"name": player.name, "message": chat_message})

        action_detail = {"comment": chat_message}
        table_message = f"{player.name} chooses to {action} by {add_to_pot}."
        action_comment = (f"{player.name}:\t'{chat_message}'\n"
                          f"\t{table_message}\n")

        poker_action = PokerAction(player, action, add_to_pot, hand_state, action_detail, action_comment)
        return poker_action
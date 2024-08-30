from typing import List, Dict

from core.deck import Deck
from core.game import Manager, LLMAssistant, OpenAILLMAssistant
from core.poker_player import AIPokerPlayer, PokerPlayer


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

    SUMMARY_PROMPT = \
    """You are the table manager for a celebrity poker game. You will be presented with a set of actions and comments that have happened.
    Please provide a brief summary of the events to share with the next player. Format your summaries as a bulleted list."""

class RoundManager(Manager):
    assistant: OpenAILLMAssistant
    deck: Deck
    players: List[PokerPlayer]
    table_messages: List[str]
    starting_players: List[PokerPlayer]
    remaining_players: List[PokerPlayer]
    table_positions: Dict[str, PokerPlayer]
    dealer: PokerPlayer or None
    small_blind_player: PokerPlayer
    big_blind_player: PokerPlayer
    under_the_gun: PokerPlayer
    small_blind: int
    min_bet: int

    # Constraints used for initializing the AI PLayer attitude and confidence
    LESS_THAN_50_WORDS = "Use less than 50 words."
    LESS_THAN_100_WORDS = "Use less than 100 words."

    def __init__(self):
        self.assistant = OpenAILLMAssistant(system_message=self.persona_prompt())
        self.table_messages = []
        self.deck = Deck()
        self.players = []
        self.table_positions = {}
        self.starting_players = []
        self.remaining_players = []
        self.dealer = None
        self.small_blind = 50
        self.small_blind_player = self.players[(self.dealer_position + 1) % len(self.players)]
        self.big_blind_player = self.players[(self.dealer_position + 2) % len(self.players)]
        self.under_the_gun = self.players[(self.dealer_position + 3) % len(self.players)]

    def persona_prompt(self) -> str:
        prompt = SystemPrompt.SUMMARY_PROMPT
        return prompt

    @property
    def dealer_position(self):
        return self.players.index(self.dealer)

    # TODO: update this to be a property and to initialize the object attribute
    def get_table_positions(self) -> Dict[str, str]:
        table_positions = {"dealer": self.dealer.name,
                           "small_blind_player": self.players[(self.dealer_position + 1) % len(self.players)].name,
                           "big_blind_player": self.players[(self.dealer_position + 2) % len(self.players)].name,
                           "under_the_gun": self.players[(self.dealer_position + 3) % len(self.players)].name
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
        self.remaining_players = [player for player in self.table_manager.players if not player.folded]

    # TODO: change this to return the options as a PlayerAction enum
    def set_player_options(self, poker_player: PokerPlayer, settings: PokerSettings):
        # How much is it to call the bet for the player?
        player_cost_to_call = self.pots[0].get_player_cost_to_call(poker_player)
        # Does the player have enough to call
        player_has_enough_to_call = poker_player.money > player_cost_to_call
        # Is the current player also the big_blind TODO: add "and have they played this hand yet"
        current_player_is_big_blind = poker_player is self.big_blind_player

        # If the current player is last to act (aka big blind), and we're still in the pre-flop round
        if (current_player_is_big_blind
                and self.current_round == PokerHandPhase.PRE_FLOP
                and self.pots[0].current_bet == self.small_blind * 2):
            player_options = ['check', 'raise', 'all-in']
        else:
            player_options = ['fold', 'check', 'call', 'bet', 'raise', 'all-in', 'chat']
            if player_cost_to_call == 0:
                player_options.remove('fold')
            if player_cost_to_call > 0:
                player_options.remove('check')
            if not player_has_enough_to_call or player_cost_to_call == 0:
                player_options.remove('call')
            if self.pots[0].current_bet > 0 or player_cost_to_call > 0:
                player_options.remove('bet')
            if poker_player.money - self.pots[0].current_bet <= 0 or 'bet' in player_options:
                player_options.remove('raise')
            if not settings.all_in_allowed or poker_player.money == 0:
                player_options.remove('all-in')

        poker_player.options = player_options.copy()

    def post_blinds(self):
        small_blind = self.small_blind
        big_blind = small_blind * 2
        self.pots[0].add_to_pot(self.small_blind_player, small_blind)
        self.pots[0].add_to_pot(self.big_blind_player, big_blind)

    def deal_hole_cards(self):
        for player in self.players:
            player.cards = self.deck.deal(2)

    def get_opponent_status(self, requesting_player=None) -> List[str]:
        opponent_positions = []
        for player in self.players:
            if player != requesting_player:
                position = f'{player.name} has ${player.money}'
                position += ' and they have folded' if player.folded else ''
                position += '.\n'
                opponent_positions.append(position)
        return opponent_positions

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

    def summarize_actions(self, actions: List[str] or str, constraints="Use less than 100 words") -> str:
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
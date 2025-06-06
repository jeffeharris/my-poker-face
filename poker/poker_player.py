import json
import random
from typing import List, Dict
from pathlib import Path

from core.card import Card
from core.assistants import OpenAILLMAssistant
from old_files.deck import CardSet
from .poker_action import PlayerAction
from .prompt_manager import PromptManager, RESPONSE_FORMAT, PERSONA_EXAMPLES
from .elasticity_manager import ElasticPersonality


class PokerPlayer:
    money: int
    cards: CardSet
    options: List[PlayerAction]
    folded: bool

    def __init__(self, name="Player", starting_money=10000):
        self.name = name
        self.money = starting_money
        self.cards = CardSet()
        self.options = []
        self.folded = False

    def __str__(self):
        return self.name

    def to_dict(self):
        return {
            "type": "PokerPlayer",
            "name": self.name,
            "money": self.money,
            "cards": self.cards.to_dict(),
            "options": self.options,
            "folded": self.folded
        }

    @classmethod
    def from_dict(cls, player_dict: Dict):
        player = cls(
            name = player_dict["name"],
            starting_money=player_dict["money"]
        )
        player.cards = Card.list_from_dict_list(player_dict["cards"])
        player.options = player_dict["options"]
        player.folded = player_dict["folded"]
        return player

    @classmethod
    def list_from_dict_list(cls, player_dict_list: List[Dict]):
        player_list = []
        for player_dict in player_dict_list:
            player_list.append(cls.from_dict(player_dict))
        return player_list

    @staticmethod
    def players_to_dict(players: List['PokerPlayer']) -> List[Dict]:
        player_dict_list = []
        for player in players:
            player_dict = player.to_dict()
            player_dict_list.append(player_dict)
        return player_dict_list

    @property
    def player_state(self):
        return {
            "name": self.name,
            "player_money": self.money,
            "player_cards": self.cards,
            "player_options": self.options,
            "has_folded": self.folded
        }

    def get_for_pot(self, amount):
        self.money -= amount

    def collect_winnings(self, amount):
        self.money += amount

    def set_for_new_hand(self):
        self.cards = CardSet()
        self.folded = False

    def get_index(self, players):
        return players.index(self)

    def set_options(self, player_options):
        self.options = player_options


class AIPokerPlayer(PokerPlayer):
    name: str
    money: int
    confidence: str
    attitude: str
    assistant: OpenAILLMAssistant

    # Constraints used for initializing the AI PLayer attitude and confidence
    DEFAULT_CONSTRAINTS = "Use less than 50 words."

    def __init__(self, name="AI Player", starting_money=10000, ai_temp=.9):
        # Options for models ["gpt-3.5-turbo", "gpt-3.5-turbo-16k", "gpt-4","gpt-4-32k"]
        super().__init__(name, starting_money=starting_money)
        self.prompt_manager = PromptManager()
        self.personality_config = self._load_personality_config()
        self.confidence = self.personality_config.get("default_confidence", "Unsure")
        self.attitude = self.personality_config.get("default_attitude", "Distracted")
        self.assistant = OpenAILLMAssistant(ai_temp=ai_temp,
                                            system_message=self.persona_prompt())
        
        # Initialize elastic personality
        self.elastic_personality = ElasticPersonality.from_base_personality(
            name=self.name,
            personality_config=self.personality_config
        )
        
        # Hand strategy persistence
        self.current_hand_strategy = None
        self.hand_action_count = 0

    def to_dict(self):
        return {
            "type": "AIPokerPlayer",
            "name": self.name,
            "money": self.money,
            "cards": [card.to_dict() for card in self.cards.cards] if self.cards else [],
            "options": self.options if self.options is not None else [],
            "folded": self.folded if self.folded is not None else False,
            "confidence": self.confidence if self.confidence is not None else "Unsure",
            "attitude": self.attitude if self.attitude is not None else "Distracted",
            "assistant": {
                "ai_temp": self.assistant.ai_temp,
                "system_message": self.assistant.system_message,
                "messages": self.assistant.messages,
                "model": self.assistant.ai_model,
            } if self.assistant else {"ai_temp": 1.0, "system_message": "Default message"},
            "elastic_personality": self.elastic_personality.to_dict() if hasattr(self, 'elastic_personality') else None,
            "current_hand_strategy": self.current_hand_strategy if hasattr(self, 'current_hand_strategy') else None,
            "hand_action_count": self.hand_action_count if hasattr(self, 'hand_action_count') else 0
        }

    @classmethod
    def from_dict(cls, player_dict):
        try:
            name = player_dict.get("name", "AI Player")
            starting_money = player_dict.get("money", 10000)
            cards = Card.list_from_dict_list(player_dict.get("cards", []))
            options = player_dict.get("options", [])
            folded = player_dict.get("folded", False)
            confidence = player_dict.get("confidence", "Unsure")
            attitude = player_dict.get("attitude", "Distracted")
            assistant_dict = player_dict.get("assistant", {})
            ai_temp = assistant_dict.get("ai_temp", .9)
            system_message = assistant_dict.get("system_message", cls().persona_prompt())
            assistant = OpenAILLMAssistant(
                ai_temp=ai_temp,
                system_message=system_message
            )

            instance = cls(name=name, starting_money=starting_money, ai_temp=ai_temp)
            instance.cards = cards
            instance.options = options
            instance.folded = folded
            instance.confidence = confidence
            instance.attitude = attitude
            instance.assistant = assistant
            
            # Restore elastic personality if present
            if 'elastic_personality' in player_dict and player_dict['elastic_personality']:
                instance.elastic_personality = ElasticPersonality.from_dict(player_dict['elastic_personality'])
            
            # Restore hand strategy persistence
            if 'current_hand_strategy' in player_dict:
                instance.current_hand_strategy = player_dict['current_hand_strategy']
            if 'hand_action_count' in player_dict:
                instance.hand_action_count = player_dict['hand_action_count']
            
            return instance
        except KeyError as e:
            raise ValueError(f"Missing key in player_dict: {e}")

    @property
    def player_state(self):
        ai_player_state = super().player_state
        ai_player_state["confidence"] = self.confidence
        ai_player_state["attitude"] = self.attitude
        return ai_player_state

    def set_for_new_hand(self):
        """
        Prepares the player for a new hand.
        Resets the memory of the assistant.
        """
        super().set_for_new_hand()
        # Reset the assistant's memory instead of directly assigning a new list.
        self.assistant.reset_memory()
        # Reset hand strategy for new hand
        self.current_hand_strategy = None
        self.hand_action_count = 0

    def initialize_attribute(self, attribute: str, constraints: str = DEFAULT_CONSTRAINTS, opponents: str = "other players", mood: int or None = None) -> str:
        """
        Initializes the attribute for the player's inner voice.

        Args:
            attribute (str): The attribute to describe.
            constraints (str): Constraints for the description. Default is use less than 50 words.
            opponents (str): Description of opponents. Default is "other players".
            mood (int): The mood to set, corresponds to [0] low, [1] regular, [2] high levels. Default is 1.

        Returns:
            str: A response based on the mood.
        """
        formatted_message = (
            f"You are {self.name}'s inner voice. Describe their {attribute} as they enter a poker game against {opponents}. "
            f"This description is being used for a simulation of a poker game and we want to have a variety of personalities "
            f"and emotions for the players. Your phrasing must be as if you are their inner voice and you are speaking to them. "
            f"{constraints}\n\n"
            f"Provide 3 responses with different levels of {attribute} (low, regular, high) and put them in JSON format like: "
            f'{{"responses": ["string", "string", "string"]}}'
        )

        response = self.assistant.get_json_response(messages=[{"role": "user", "content": formatted_message}])
        content = json.loads(response.choices[0].message.content)
        responses = content["responses"]

        # if mood is None, randomly assign the mood from the response
        if mood is None:
            # Randomly select the response mood
            random.shuffle(responses)
            return responses[0]
        else:
            return responses[mood]
    
    def _load_personality_config(self):
        """Load personality configuration from JSON file."""
        filepath = Path(__file__).parent / 'personalities.json'
        if filepath.exists():
            with open(filepath, 'r') as f:
                personalities = json.load(f)['personalities']
                return personalities.get(self.name, self._default_personality_config())
        return self._default_personality_config()
    
    def _default_personality_config(self):
        """Return default personality configuration."""
        return {
            "play_style": "balanced",
            "default_confidence": "Unsure",
            "default_attitude": "Distracted",
            "personality_traits": {
                "bluff_tendency": 0.5,
                "aggression": 0.5,
                "chattiness": 0.5,
                "emoji_usage": 0.3
            }
        }

    def persona_prompt(self):
        """Generate persona prompt using the PromptManager."""
        # Get example for this persona if available
        example_name = self.name.split()[0] if ' ' in self.name else self.name
        if example_name in PERSONA_EXAMPLES:
            example = json.dumps(PERSONA_EXAMPLES[example_name]['sample_response'], indent=2)
        else:
            # Use a default example
            example = json.dumps(PERSONA_EXAMPLES['Eeyore']['sample_response'], indent=2)
        
        base_prompt = self.prompt_manager.render_prompt(
            'poker_player',
            name=self.name,
            attitude=self.attitude,
            confidence=self.confidence,
            money=self.money,
            json_template=json.dumps(RESPONSE_FORMAT, indent=2)
        )
        
        # Add example response
        return f"{base_prompt}\n\nExample response:\n{example}"
    
    def adjust_strategy_based_on_state(self):
        """Dynamically adjust strategy based on current game state."""
        if self.money < 1000:  # Low on chips
            return "You're running low on chips. Play conservatively and wait for strong hands."
        elif self.money > 20000:  # Chip leader
            return "You're the chip leader. Use your stack to pressure opponents."
        else:
            return ""
    
    def get_personality_modifier(self):
        """Get personality-specific play instructions based on current elastic trait values."""
        if hasattr(self, 'elastic_personality'):
            # Use elastic trait values
            traits = {
                name: self.elastic_personality.get_trait_value(name)
                for name in ['bluff_tendency', 'aggression', 'chattiness', 'emoji_usage']
            }
        else:
            # Fallback to static config
            traits = self.personality_config.get("personality_traits", {})
        
        modifiers = []
        
        if traits.get("bluff_tendency", 0.5) > 0.7:
            modifiers.append("Remember: You love to bluff! Look for opportunities to deceive.")
        elif traits.get("bluff_tendency", 0.5) < 0.3:
            modifiers.append("Remember: You prefer honest play. Only bet when you have it.")
        
        if traits.get("aggression", 0.5) > 0.7:
            modifiers.append("Be aggressive! Raise often and put pressure on opponents.")
        elif traits.get("aggression", 0.5) < 0.3:
            modifiers.append("Play cautiously. Avoid big risks unless you're certain.")
        
        return " ".join(modifiers)
    
    def update_mood_from_elasticity(self):
        """Update confidence and attitude based on current elastic personality state."""
        if hasattr(self, 'elastic_personality'):
            # Get current mood from elastic personality
            current_mood = self.elastic_personality.get_current_mood()
            
            # Update confidence/attitude if mood has changed
            if current_mood != self.elastic_personality.current_mood:
                self.confidence = current_mood
                self.elastic_personality.current_mood = current_mood
                
                # Regenerate system message with new mood
                self.assistant.system_message = self.persona_prompt()
    
    def apply_pressure_event(self, event_name: str):
        """Apply a pressure event to this player's elastic personality."""
        if hasattr(self, 'elastic_personality'):
            self.elastic_personality.apply_pressure_event(event_name)
            self.update_mood_from_elasticity()
    
    def recover_traits(self):
        """Apply recovery to elastic traits."""
        if hasattr(self, 'elastic_personality'):
            self.elastic_personality.recover_all_traits()
            self.update_mood_from_elasticity()
    
    # def evaluate_hole_cards(self):
    #     # Use Monte Carlo method to approximate hand strength
    #     hand_ranks = []
    #     for _ in range(100):  # Adjust this number as needed
    #         simulated_community = Deck().deal(5)
    #         simulated_hand_rank = HandEvaluator(self.cards + simulated_community).evaluate_hand()["hand_rank"]
    #         hand_ranks.append(simulated_hand_rank)
    #     hand_rank = sum(hand_ranks) / len(hand_ranks)
    #     return hand_rank
    # *** MORE BELOW ***
    # if len(community_cards) < 3:
    #     hand_rank = player.evaluate_hole_cards()
    # else:
    #     hand_rank = HandEvaluator(player.cards + community_cards).evaluate_hand()["hand_rank"]
    #
    # pot_odds = current_pot / current_bet if current_bet else 1
    # money_left = player.money / current_bet if current_bet else 1
    #
    # bet = 0
    #
    # # Adjust these thresholds as needed
    # if current_bet == 0:
    #     if hand_rank < 5 or pot_odds > 3 or money_left > 3:
    #         action = "raise"
    #         bet = player.money // 10  # Bet 10% of AI's money
    #     else:
    #         action = "check"
    # elif hand_rank > 5 and pot_odds < 2 and money_left < 2:
    #     action = "fold"
    # elif hand_rank < 5 or pot_odds > 3 or money_left > 3:
    #     action = "raise"
    #     bet = player.money // 10  # Bet 10% of AI's money
    # else:
    #     action = "call"
    #     bet = current_bet
    #
    # player.chat_message = f"{player.name} chooses to {action} by {bet}."
    # return action, bet

    def get_player_response(self, message) -> Dict[str, str]:
        try:
            # Increment action count before getting response
            self.hand_action_count += 1
            
            # Add context about strategy requirement
            if self.hand_action_count == 1:
                message += "\n\nThis is your FIRST action this hand. You must set your 'hand_strategy' for the entire hand."
            elif self.current_hand_strategy:
                message += f"\n\nYour hand strategy remains: '{self.current_hand_strategy}'"
            
            player_response = json.loads(self.assistant.chat(message, json_format=True))
            
            # Lock in hand strategy on first action
            if self.hand_action_count == 1 and 'hand_strategy' in player_response:
                self.current_hand_strategy = player_response['hand_strategy']
            elif self.current_hand_strategy and 'hand_strategy' in player_response:
                # Override any attempt to change strategy mid-hand
                player_response['hand_strategy'] = self.current_hand_strategy
                
        except (json.JSONDecodeError, TypeError) as e:
            print(f"Error decoding player response: {e}")
            player_response = {"error": "Invalid response from assistant"}
        return player_response

    def build_hand_update_message(self, hand_state):
        # Currently used values
        persona = self.name
        attitude = self.attitude
        confidence = self.confidence
        table_positions = hand_state["table_positions"]
        opponent_status = hand_state["opponent_status"]
        current_round = hand_state["current_phase"]
        community_cards = [str(card) for card in hand_state["community_cards"]]
        opponents = hand_state["remaining_players"]
        number_of_opponents = len(opponents) - 1
        player_money = self.money
        # TODO: <FEATURE> decide what to do with this position idea
        # position = hand_state["positions"][self]
        current_situation = hand_state["current_situation"]
        hole_cards = [str(card) for card in self.cards]
        current_pot = hand_state["current_pot"]
        # current_bet = current_pot.current_bet     # removed this because i wasn't able to get the ai player to understand how to bet when i included this, the pot, the cost to call etc.
        cost_to_call = current_pot.get_player_cost_to_call(self.name)
        player_options = self.options

        # create a list of the action comments and then send them to the table manager to summarize
        action_comment_list = [action.action_comment for action in hand_state["poker_actions"]]
        action_summary = "We're just getting started! You're first to go."
        if len(action_comment_list) > 0:
            action_summary = hand_state["table_manager"].summarize_actions_for_player(action_comment_list[-number_of_opponents:], self.name)

        persona_state = (
            f"Persona: {persona}\n"
            f"Attitude: {attitude}\n"
            f"Confidence: {confidence}\n"
            f"Your Cards: {hole_cards}\n"
            f"Your Money: {player_money}\n"
        )

        hand_state = (
            f"{current_situation}\n"
            f"Current Round: {current_round}\n"
            f"Community Cards: {community_cards}\n"
            f"Table Positions: {table_positions}\n"
            f"Opponent Status:\n{opponent_status}\n"
            f"Actions since your last turn: {action_summary}\n"
        )

        pot_state = (
            f"Pot Total: ${current_pot.total}\n"
            f"How much you've bet: ${current_pot.get_player_pot_amount(self.name)}\n"
            f"Your cost to call: ${cost_to_call}\n"
        )

        hand_update_message = persona_state + hand_state + pot_state + (
            #f"You have {hole_cards} in your hand.\n"  # The current bet is ${current_bet} and
            # f"Remember, you're feeling {attitude} and {confidence}.\n"
            f"Consider the strength of your hand relative to the pot and the likelihood that your opponents might have stronger hands. "
            f"Preserve your chips for when the odds are in your favor, and remember that sometimes folding or checking is the best move. "
            f"You cannot bet more than you have, ${player_money}.\n"
            f"You must select from these options: {player_options}\n"
            f"What is your move, {persona}?\n\n"
        )

        return hand_update_message
